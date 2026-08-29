from __future__ import annotations

import html
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import requests

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

_SHANGHAI_OFFSET = timedelta(hours=8)

_PUBLIC_URLS = {
    "brent": "https://oil.oilchem.net/oil/crude_oil_zone.shtml",
    "c5": "https://oil.oilchem.net/oil/c5.shtml",
    "c9": "https://oil.oilchem.net/oil/c9.shtml",
    "naphtha": "https://oil.oilchem.net/oil/naphtha.shtml",
}


def _env_flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _flatten_html(raw_html: str) -> str:
    text_value = re.sub(r"<script[\\s\\S]*?</script>", " ", raw_html, flags=re.IGNORECASE)
    text_value = re.sub(r"<style[\\s\\S]*?</style>", " ", text_value, flags=re.IGNORECASE)
    text_value = re.sub(r"<[^>]+>", " ", text_value)
    text_value = html.unescape(text_value)
    return re.sub(r"\s+", " ", text_value).strip()


def _parse_market_block_date(text_value: str, label: str) -> Optional[str]:
    match = re.search(rf"{re.escape(label)}\s+Market Price\s+(\d{{4}}-\d{{2}}-\d{{2}})", text_value)
    if match:
        return match.group(1)
    return None


def _parse_number(value: str) -> Optional[float]:
    cleaned = (value or "").replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return round(float(cleaned), 2)
    except ValueError:
        return None


def _sql_text(sql: str):
    from sqlalchemy import text

    return text(sql)


def parse_brent_public_page(raw_html: str) -> Tuple[Optional[str], Optional[float]]:
    text_value = _flatten_html(raw_html)
    price_date = _parse_market_block_date(text_value, "国际原油价格")
    match = re.search(r"布伦特期货\s+([0-9]+(?:\.[0-9]+)?)", text_value)
    if not match:
        return price_date, None
    return price_date, _parse_number(match.group(1))


def parse_regional_market_price(raw_html: str, label: str, region: str) -> Tuple[Optional[str], Optional[float]]:
    text_value = _flatten_html(raw_html)
    price_date = _parse_market_block_date(text_value, label)
    block_match = re.search(
        rf"{re.escape(label)}\s+Market Price\s+\d{{4}}-\d{{2}}-\d{{2}}\s+\d{{1,2}}:\d{{2}}(.*?)(?:广告招商|隆众核心数据|CopyRight)",
        text_value,
    )
    block_text = block_match.group(1) if block_match else text_value
    region_match = re.search(rf"{re.escape(region)}\s+([0-9]+(?:\.[0-9]+)?)", block_text)
    if not region_match:
        return price_date, None
    return price_date, _parse_number(region_match.group(1))


def parse_japan_naphtha_article(raw_html: str) -> Tuple[Optional[str], Optional[float]]:
    text_value = _flatten_html(raw_html)

    publish_match = re.search(r"发布时间：\s*(\d{4})-\d{2}-\d{2}", text_value)
    title_match = re.search(r"(\d{1,2})月(\d{1,2})日国际石脑油价格快讯", text_value)
    if publish_match and title_match:
        year = int(publish_match.group(1))
        month = int(title_match.group(1))
        day = int(title_match.group(2))
        price_date = f"{year:04d}-{month:02d}-{day:02d}"
    else:
        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", text_value)
        price_date = date_match.group(1) if date_match else None

    patterns = [
        r"日本石脑油\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?)",
        r"日本石脑油[^0-9]{0,30}([0-9]+(?:\.[0-9]+)?)",
        r"Japan(?:ese)?\s+Naphtha[^0-9]{0,30}([0-9]+(?:\.[0-9]+)?)",
        r"CFR\s+Japan[^0-9]{0,30}([0-9]+(?:\.[0-9]+)?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text_value, flags=re.IGNORECASE)
        if match:
            return price_date, _parse_number(match.group(1))
    return price_date, None


def extract_latest_naphtha_article_url(raw_html: str) -> Optional[str]:
    match = re.search(r'href="([^"]+)"[^>]*>\s*\[石脑油\]：\d+月\d+日国际石脑油价格快讯', raw_html)
    if not match:
        return None
    url = match.group(1).strip()
    if url.startswith("http"):
        return url
    return f"https://www.oilchem.net{url}"


@dataclass
class OilchemSnapshot:
    price_date: str
    brent_crude: Optional[float]
    japan_naphtha: Optional[float]
    industrial_cracking_c5: Optional[float]
    industrial_cracking_c9: Optional[float]
    source_status: Dict[str, Any]


class OilchemSyncService:
    def __init__(self) -> None:
        self._engine: Optional["Engine"] = None
        db_url = os.getenv("DATABASE_URL", "").strip()
        if db_url:
            try:
                from backend.src.db.engine_factory import get_engine

                self._engine = get_engine(db_url)
                self._ensure_table()
            except Exception as exc:
                logger.warning("Oilchem DB engine initialization skipped: %s", exc)

    def enabled(self) -> bool:
        return _env_flag("ENABLE_OILCHEM_SYNC", "true")

    def schedule_hour(self) -> int:
        return max(0, min(23, int(os.getenv("OILCHEM_SYNC_HOUR", "0") or "0")))

    def schedule_minute(self) -> int:
        return max(0, min(59, int(os.getenv("OILCHEM_SYNC_MINUTE", "30") or "30")))

    def has_database(self) -> bool:
        return self._engine is not None

    def _ensure_table(self) -> None:
        if not self._engine:
            return
        ddl = _sql_text(
            """
            CREATE TABLE IF NOT EXISTS oilchem_data (
                price_date DATE PRIMARY KEY,
                brent_crude DECIMAL(12, 2) NULL,
                japan_naphtha DECIMAL(12, 2) NULL,
                industrial_cracking_c5 DECIMAL(12, 2) NULL,
                industrial_cracking_c9 DECIMAL(12, 2) NULL,
                source_status TEXT NULL,
                captured_at VARCHAR(64) NOT NULL,
                updated_at VARCHAR(64) NOT NULL
            )
            """
        )
        with self._engine.begin() as conn:
            conn.execute(ddl)

    def _session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
        )
        cookie_value = os.getenv("OILCHEM_SESSION_COOKIE", "").strip()
        if cookie_value:
            session.headers["Cookie"] = cookie_value
        return session

    def _fetch_text(self, session: requests.Session, url: str) -> str:
        response = session.get(url)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or response.encoding or "utf-8"
        return response.text

    def collect_snapshot(self) -> Dict[str, Any]:
        session = self._session()
        now = (datetime.utcnow() + _SHANGHAI_OFFSET).replace(microsecond=0)

        crude_html = self._fetch_text(session, _PUBLIC_URLS["brent"])
        c5_html = self._fetch_text(session, _PUBLIC_URLS["c5"])
        c9_html = self._fetch_text(session, _PUBLIC_URLS["c9"])
        naphtha_html = self._fetch_text(session, _PUBLIC_URLS["naphtha"])

        brent_date, brent_price = parse_brent_public_page(crude_html)
        c5_date, c5_price = parse_regional_market_price(c5_html, "裂解C5市场价格", "华东")
        c9_date, c9_price = parse_regional_market_price(c9_html, "裂解C9市场价格", "华东")

        naphtha_date = None
        japan_naphtha = None
        naphtha_status = "missing: public page does not expose 日本石脑油"
        article_url = extract_latest_naphtha_article_url(naphtha_html)
        if article_url and "Cookie" in session.headers:
            try:
                article_html = self._fetch_text(session, article_url)
                naphtha_date, japan_naphtha = parse_japan_naphtha_article(article_html)
                if japan_naphtha is not None:
                    naphtha_status = f"authenticated article parsed from {article_url}"
                else:
                    naphtha_status = f"authenticated article did not yield 日本石脑油 from {article_url}"
            except Exception as exc:
                naphtha_status = f"authenticated article fetch failed: {exc}"
        elif article_url:
            naphtha_status = "missing: set OILCHEM_SESSION_COOKIE to fetch gated 日本石脑油 content"

        date_candidates = [value for value in [brent_date, c5_date, c9_date, naphtha_date] if value]
        price_date = max(date_candidates) if date_candidates else now.date().isoformat()

        snapshot = OilchemSnapshot(
            price_date=price_date,
            brent_crude=brent_price,
            japan_naphtha=japan_naphtha,
            industrial_cracking_c5=c5_price,
            industrial_cracking_c9=c9_price,
            source_status={
                "brent_crude": {
                    "url": _PUBLIC_URLS["brent"],
                    "price_date": brent_date,
                    "status": "ok" if brent_price is not None else "missing",
                },
                "japan_naphtha": {
                    "url": article_url or _PUBLIC_URLS["naphtha"],
                    "price_date": naphtha_date,
                    "status": naphtha_status,
                },
                "industrial_cracking_c5": {
                    "url": _PUBLIC_URLS["c5"],
                    "price_date": c5_date,
                    "status": "ok: public 华东 market price" if c5_price is not None else "missing",
                },
                "industrial_cracking_c9": {
                    "url": _PUBLIC_URLS["c9"],
                    "price_date": c9_date,
                    "status": "ok: public 华东 market price" if c9_price is not None else "missing",
                },
            },
        )
        return {
            "price_date": snapshot.price_date,
            "brent_crude": snapshot.brent_crude,
            "japan_naphtha": snapshot.japan_naphtha,
            "industrial_cracking_c5": snapshot.industrial_cracking_c5,
            "industrial_cracking_c9": snapshot.industrial_cracking_c9,
            "source_status": snapshot.source_status,
            "captured_at": now.isoformat(),
        }

    def sync_current_snapshot(self) -> Dict[str, Any]:
        snapshot = self.collect_snapshot()
        if not self._engine:
            return {
                "status": "error",
                "message": "DATABASE_URL is not configured",
                **snapshot,
            }

        now_iso = (datetime.utcnow() + _SHANGHAI_OFFSET).replace(microsecond=0).isoformat()
        params = {
            "price_date": snapshot["price_date"],
            "brent_crude": snapshot["brent_crude"],
            "japan_naphtha": snapshot["japan_naphtha"],
            "industrial_cracking_c5": snapshot["industrial_cracking_c5"],
            "industrial_cracking_c9": snapshot["industrial_cracking_c9"],
            "source_status": json.dumps(snapshot["source_status"], ensure_ascii=False),
            "captured_at": snapshot["captured_at"],
            "updated_at": now_iso,
        }
        exists_stmt = _sql_text("SELECT COUNT(*) FROM oilchem_data WHERE price_date = :price_date")
        insert_stmt = _sql_text(
            """
            INSERT INTO oilchem_data (
                price_date,
                brent_crude,
                japan_naphtha,
                industrial_cracking_c5,
                industrial_cracking_c9,
                source_status,
                captured_at,
                updated_at
            ) VALUES (
                :price_date,
                :brent_crude,
                :japan_naphtha,
                :industrial_cracking_c5,
                :industrial_cracking_c9,
                :source_status,
                :captured_at,
                :updated_at
            )
            """
        )
        update_stmt = _sql_text(
            """
            UPDATE oilchem_data
            SET brent_crude = :brent_crude,
                japan_naphtha = :japan_naphtha,
                industrial_cracking_c5 = :industrial_cracking_c5,
                industrial_cracking_c9 = :industrial_cracking_c9,
                source_status = :source_status,
                captured_at = :captured_at,
                updated_at = :updated_at
            WHERE price_date = :price_date
            """
        )
        with self._engine.begin() as conn:
            exists = int(conn.execute(exists_stmt, {"price_date": snapshot["price_date"]}).scalar() or 0)
            conn.execute(update_stmt if exists else insert_stmt, params)

        return {
            "status": "ok",
            "message": "oilchem snapshot synced",
            **snapshot,
        }

    def list_recent_rows(self, limit: int = 31) -> List[Dict[str, Any]]:
        if not self._engine:
            return []
        safe_limit = max(1, min(90, int(limit)))
        stmt = _sql_text(
            f"""
            SELECT price_date, brent_crude, japan_naphtha, industrial_cracking_c5, industrial_cracking_c9, source_status, captured_at, updated_at
            FROM oilchem_data
            ORDER BY price_date DESC
            LIMIT {safe_limit}
            """
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()

        output: List[Dict[str, Any]] = []
        for row in rows:
            source_status = row.get("source_status")
            if isinstance(source_status, str):
                try:
                    source_status = json.loads(source_status)
                except json.JSONDecodeError:
                    pass
            output.append(
                {
                    "price_date": str(row.get("price_date") or ""),
                    "brent_crude": row.get("brent_crude"),
                    "japan_naphtha": row.get("japan_naphtha"),
                    "industrial_cracking_c5": row.get("industrial_cracking_c5"),
                    "industrial_cracking_c9": row.get("industrial_cracking_c9"),
                    "source_status": source_status,
                    "captured_at": row.get("captured_at"),
                    "updated_at": row.get("updated_at"),
                }
            )
        return output


oilchem_sync_service = OilchemSyncService()