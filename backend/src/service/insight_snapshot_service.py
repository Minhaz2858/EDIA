from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy import text

from backend.src.db.sqlite_db import db
from backend.src.domain.knowledge_graph import domain_graph
from backend.src.service.erp_kpi_service import ErpKPIService, _PRODUCT_TO_MATERIAL_NAMES
from backend.src.service.market_data_service import market_data
from backend.src.skills.dashboard import build_dashboard_insights


class InsightSnapshotService:
    """Precompute and serve DB-backed market insight bundles for fast online chat."""

    def __init__(self) -> None:
        self.engine = db.engine
        self._erp_kpi = ErpKPIService()
        self._refresh_lock = threading.Lock()
        self._refresh_inflight: Dict[str, bool] = {"market": False, "erp": False}
        self._last_refresh_enqueue_ts: Dict[str, float] = {"market": 0.0, "erp": 0.0}
        self._init_tables()

    def _init_tables(self) -> None:
        ddl = text(
            """
            CREATE TABLE IF NOT EXISTS insight_market_cache (
                snapshot_id VARCHAR(64) NOT NULL,
                product_id VARCHAR(60) NOT NULL,
                name_cn VARCHAR(120),
                unit VARCHAR(32),
                as_of_date VARCHAR(16),
                latest_price REAL,
                weekly_change_pct REAL,
                weekly_gap_value REAL,
                weekly_reference_date VARCHAR(16),
                high_7d REAL,
                low_7d REAL,
                data_source VARCHAR(64),
                source_detail VARCHAR(128),
                insight_json TEXT,
                narrative TEXT,
                generated_at VARCHAR(32) NOT NULL,
                PRIMARY KEY (snapshot_id, product_id)
            )
            """
        )

        ddl_erp = text(
            """
            CREATE TABLE IF NOT EXISTS insight_erp_cache (
                snapshot_id VARCHAR(64) NOT NULL,
                product_id VARCHAR(60) NOT NULL,
                period_start VARCHAR(16),
                period_end VARCHAR(16),
                sales_30d_ton REAL,
                sales_growth_pct REAL,
                inventory_tons REAL,
                inventory_days REAL,
                utilization_pct REAL,
                risk_level VARCHAR(32),
                insight_json TEXT,
                narrative TEXT,
                generated_at VARCHAR(32) NOT NULL,
                PRIMARY KEY (snapshot_id, product_id)
            )
            """
        )

        with self.engine.begin() as conn:
            conn.execute(ddl)
            conn.execute(ddl_erp)

        idx_statements = [
            text(
                "CREATE INDEX IF NOT EXISTS idx_insight_market_cache_generated_at "
                "ON insight_market_cache (generated_at)"
            ),
            text(
                "CREATE INDEX IF NOT EXISTS idx_insight_market_cache_product_id "
                "ON insight_market_cache (product_id)"
            ),
            text(
                "CREATE INDEX IF NOT EXISTS idx_insight_erp_cache_generated_at "
                "ON insight_erp_cache (generated_at)"
            ),
            text(
                "CREATE INDEX IF NOT EXISTS idx_insight_erp_cache_product_id "
                "ON insight_erp_cache (product_id)"
            ),
        ]
        for stmt in idx_statements:
            try:
                with self.engine.begin() as conn:
                    conn.execute(stmt)
            except Exception:
                pass

    @staticmethod
    def _snapshot_point_date(point: dict) -> date | None:
        raw_value = point.get("date") if isinstance(point, dict) else None
        if raw_value is None:
            return None
        try:
            return datetime.fromisoformat(str(raw_value)[:10]).date()
        except Exception:
            return None

    def _select_weekly_reference_point(
        self,
        history: List[dict],
        latest_date: date,
        tolerance_days: int = 3,
    ) -> tuple[float | None, str | None]:
        target_date = latest_date - timedelta(days=7)
        best_point = None
        best_date = None
        best_abs_delta = None

        for point in history[:-1]:
            point_date = self._snapshot_point_date(point)
            if point_date is None or point_date > latest_date:
                continue
            abs_delta = abs((point_date - target_date).days)
            if abs_delta > tolerance_days:
                continue
            if best_abs_delta is None or abs_delta < best_abs_delta or (
                abs_delta == best_abs_delta and best_date is not None and point_date > best_date
            ):
                best_point = point
                best_date = point_date
                best_abs_delta = abs_delta

        if not best_point:
            return None, None
        try:
            return float(best_point.get("price")), best_date.isoformat() if best_date else None
        except (TypeError, ValueError):
            return None, None

    @staticmethod
    def _build_market_template_narrative(
        *,
        product_name: str,
        latest_price: float,
        unit: str,
        direction: str,
        weekly_change_pct: float | None,
        low_7d: float | None,
        high_7d: float | None,
    ) -> str:
        if direction == "up":
            return (
                f"{product_name} is trending up this week at {latest_price:,.0f} {unit}; "
                f"WoW {weekly_change_pct:+.2f}% with 7-day range {low_7d:,.0f}-{high_7d:,.0f}."
                if weekly_change_pct is not None and low_7d is not None and high_7d is not None
                else f"{product_name} latest is {latest_price:,.0f} {unit}."
            )
        if direction == "down":
            return (
                f"{product_name} softened this week to {latest_price:,.0f} {unit}; "
                f"WoW {weekly_change_pct:+.2f}% with 7-day range {low_7d:,.0f}-{high_7d:,.0f}."
                if weekly_change_pct is not None and low_7d is not None and high_7d is not None
                else f"{product_name} latest is {latest_price:,.0f} {unit}."
            )
        return (
            f"{product_name} is stable this week around {latest_price:,.0f} {unit}; "
            f"WoW {weekly_change_pct:+.2f}% with 7-day range {low_7d:,.0f}-{high_7d:,.0f}."
            if weekly_change_pct is not None and low_7d is not None and high_7d is not None
            else f"{product_name} latest is {latest_price:,.0f} {unit}."
        )

    def _build_market_local_llm_narrative(
        self,
        *,
        llm_service: Any,
        product_id: str,
        product_name: str,
        as_of_date: str,
        latest_price: float,
        unit: str,
        weekly_change_pct: float | None,
        weekly_reference_date: str | None,
        low_7d: float | None,
        high_7d: float | None,
        data_source: str,
    ) -> str:
        if llm_service is None:
            raise RuntimeError("Local LLM service is required for local_llm narrative mode")

        system_prompt = (
            "You are EDIA local snapshot narrator. "
            "Write exactly one short Chinese market snapshot sentence grounded only in the provided numbers. "
            "No headings. No labels. No reasoning. No bullet points. No markdown. "
            "Do not invent drivers, forecasts, or extra facts."
        )
        user_prompt = (
            f"产品: {product_name}\n"
            f"产品ID: {product_id}\n"
            f"数据日期: {as_of_date}\n"
            f"最新价格: {latest_price} {unit}\n"
            f"周变化: {weekly_change_pct}\n"
            f"周参考日期: {weekly_reference_date}\n"
            f"7日低点: {low_7d}\n"
            f"7日高点: {high_7d}\n"
            f"数据源: {data_source}\n"
            "只输出一句最终结论。"
        )

        if getattr(llm_service, "backend_kind", "") == "ollama-generate" and getattr(llm_service, "base_url", ""):
            timeout_seconds = float(getattr(llm_service, "local_completion_timeout_sec", 120.0) or 120.0)
            prompt = f"{system_prompt}\n\n{user_prompt}"
            response = httpx.post(
                str(llm_service.base_url),
                json={
                    "model": str(getattr(llm_service, "model", "") or ""),
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.1,
                        "num_predict": 96,
                    },
                },
                timeout=timeout_seconds,
            )
            response.raise_for_status()
            text_value = str((response.json() or {}).get("response") or "").strip()
        else:
            text_value = str(
                llm_service.generate_completion(
                    system_prompt,
                    user_prompt,
                    route_hint="local",
                    sanitized=False,
                )
                or ""
            ).strip()
        if not text_value or text_value.lower().startswith("error generating response"):
            raise RuntimeError(text_value or "empty local llm response")

        bad_markers = (
            "<think>",
            "thinking process",
            "analyze the request",
            "return plain text only",
            "role:",
            "task:",
            "constraints:",
            "constraint ",
            "input data:",
            "priority:",
            "provided numbers",
        )
        cleaned_lines: List[str] = []
        for raw_line in text_value.splitlines():
            line = raw_line.strip().strip("-*# ")
            if not line:
                continue
            lowered = line.lower()
            if any(marker in lowered for marker in bad_markers):
                continue
            if "**" in line:
                continue
            cleaned_lines.append(line)

        cleaned_text = " ".join(cleaned_lines or [text_value]).strip()
        cleaned_text = " ".join(cleaned_text.split())
        if not cleaned_text:
            raise RuntimeError("empty local llm response after cleanup")
        lowered_cleaned = cleaned_text.lower()
        if any(marker in lowered_cleaned for marker in bad_markers):
            raise RuntimeError("local llm response contained reasoning text")
        return cleaned_text[:400]

    def _build_insight_rows(
        self,
        generated_at: str,
        snapshot_id: str,
        *,
        as_of_date: date | None = None,
        narrative_mode: str = "template",
        llm_service: Any = None,
        product_ids: Optional[List[str]] = None,
    ) -> List[dict]:
        rows: List[dict] = []
        allowed_product_ids = {
            str(item).strip().lower() for item in (product_ids or []) if str(item).strip()
        }
        for node in domain_graph.nodes:
            if allowed_product_ids and str(node.id).strip().lower() not in allowed_product_ids:
                continue
            cutoff_date = as_of_date.isoformat() if as_of_date else None
            history = market_data.get_price_history(node.id, cutoff_date=cutoff_date)
            if not history:
                continue

            latest = history[-1]
            try:
                latest_price = float(latest.get("price"))
            except (TypeError, ValueError):
                continue

            latest_date = self._snapshot_point_date(latest)
            if latest_date is not None:
                weekly_ref_price, weekly_ref_date = self._select_weekly_reference_point(history, latest_date)
            else:
                weekly_ref_price = float(history[-7]["price"]) if len(history) >= 7 else None
                weekly_ref_date = str(history[-7].get("date"))[:10] if len(history) >= 7 else None

            weekly_gap_value = (
                round(latest_price - weekly_ref_price, 2) if weekly_ref_price is not None else None
            )
            weekly_change_pct = (
                round((weekly_gap_value / weekly_ref_price) * 100, 2)
                if weekly_ref_price and weekly_gap_value is not None
                else None
            )

            window = history[-7:] if len(history) >= 7 else history
            try:
                high_7d = round(max(float(point["price"]) for point in window), 2)
                low_7d = round(min(float(point["price"]) for point in window), 2)
            except Exception:
                high_7d = None
                low_7d = None

            source_detail = market_data.get_data_source_detail(node.id)
            if source_detail == "mysql":
                data_source = "real_mysql"
            elif source_detail and source_detail != "unknown":
                data_source = source_detail
            else:
                data_source = "unknown"

            direction = "flat"
            if isinstance(weekly_change_pct, (int, float)):
                if weekly_change_pct > 0.2:
                    direction = "up"
                elif weekly_change_pct < -0.2:
                    direction = "down"

            insight_payload = {
                "product_id": node.id,
                "as_of_date": str(latest.get("date") or "")[:10],
                "latest_price": latest_price,
                "weekly_change_pct": weekly_change_pct,
                "weekly_gap_value": weekly_gap_value,
                "weekly_reference_date": weekly_ref_date,
                "high_7d": high_7d,
                "low_7d": low_7d,
                "direction": direction,
                "unit": node.unit,
                "data_source": data_source,
                "narrative_mode": narrative_mode,
            }

            narrative = self._build_market_template_narrative(
                product_name=node.name_cn,
                latest_price=latest_price,
                unit=node.unit,
                direction=direction,
                weekly_change_pct=weekly_change_pct,
                low_7d=low_7d,
                high_7d=high_7d,
            )
            if narrative_mode == "local_llm":
                try:
                    narrative = self._build_market_local_llm_narrative(
                        llm_service=llm_service,
                        product_id=node.id,
                        product_name=node.name_cn,
                        as_of_date=str(latest.get("date") or "")[:10],
                        latest_price=latest_price,
                        unit=node.unit,
                        weekly_change_pct=weekly_change_pct,
                        weekly_reference_date=weekly_ref_date,
                        low_7d=low_7d,
                        high_7d=high_7d,
                        data_source=data_source,
                    )
                    insight_payload["narrative_source"] = "local_llm"
                except Exception:
                    insight_payload["narrative_source"] = "template_fallback"
            else:
                insight_payload["narrative_source"] = "template"

            rows.append(
                {
                    "snapshot_id": snapshot_id,
                    "product_id": node.id,
                    "name_cn": node.name_cn,
                    "unit": node.unit,
                    "as_of_date": str(latest.get("date") or "")[:10],
                    "latest_price": latest_price,
                    "weekly_change_pct": weekly_change_pct,
                    "weekly_gap_value": weekly_gap_value,
                    "weekly_reference_date": weekly_ref_date,
                    "high_7d": high_7d,
                    "low_7d": low_7d,
                    "data_source": data_source,
                    "source_detail": (
                        f"{source_detail}|local_llm_test"
                        if narrative_mode == "local_llm" and source_detail
                        else ("local_llm_test" if narrative_mode == "local_llm" else source_detail)
                    ),
                    "insight_json": json.dumps(insight_payload, ensure_ascii=False),
                    "narrative": narrative,
                    "generated_at": generated_at,
                }
            )

        return rows

    def _prune_old_snapshots(self, table_name: str, keep_latest: int = 12) -> None:
        with self.engine.connect() as conn:
            snapshot_rows = conn.execute(
                text(
                    f"SELECT snapshot_id FROM {table_name} "
                    "GROUP BY snapshot_id "
                    "ORDER BY MAX(generated_at) DESC"
                )
            ).fetchall()
        snapshot_ids = [str(row[0]) for row in snapshot_rows if row and row[0]]
        if len(snapshot_ids) <= keep_latest:
            return

        stale = snapshot_ids[keep_latest:]
        with self.engine.begin() as conn:
            for snapshot_id in stale:
                conn.execute(
                    text(f"DELETE FROM {table_name} WHERE snapshot_id = :snapshot_id"),
                    {"snapshot_id": snapshot_id},
                )

    def refresh_snapshot(
        self,
        *,
        as_of_date: date | None = None,
        generated_at: str | None = None,
        narrative_mode: str = "template",
        llm_service: Any = None,
        product_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        generated_at = generated_at or (datetime.utcnow().replace(microsecond=0).isoformat() + "Z")
        snapshot_id = str(uuid.uuid4())
        rows = self._build_insight_rows(
            generated_at=generated_at,
            snapshot_id=snapshot_id,
            as_of_date=as_of_date,
            narrative_mode=narrative_mode,
            llm_service=llm_service,
            product_ids=product_ids,
        )

        if not rows:
            return {
                "ok": False,
                "snapshot_id": snapshot_id,
                "generated_at": generated_at,
                "rows": 0,
                "message": "No market data rows available for insight snapshot.",
            }

        insert_sql = text(
            """
            INSERT INTO insight_market_cache (
                snapshot_id,
                product_id,
                name_cn,
                unit,
                as_of_date,
                latest_price,
                weekly_change_pct,
                weekly_gap_value,
                weekly_reference_date,
                high_7d,
                low_7d,
                data_source,
                source_detail,
                insight_json,
                narrative,
                generated_at
            ) VALUES (
                :snapshot_id,
                :product_id,
                :name_cn,
                :unit,
                :as_of_date,
                :latest_price,
                :weekly_change_pct,
                :weekly_gap_value,
                :weekly_reference_date,
                :high_7d,
                :low_7d,
                :data_source,
                :source_detail,
                :insight_json,
                :narrative,
                :generated_at
            )
            """
        )

        with self.engine.begin() as conn:
            for row in rows:
                conn.execute(insert_sql, row)

        self._prune_old_snapshots("insight_market_cache", keep_latest=12)

        return {
            "ok": True,
            "snapshot_id": snapshot_id,
            "generated_at": generated_at,
            "as_of_date": as_of_date.isoformat() if as_of_date else None,
            "narrative_mode": narrative_mode,
            "product_ids": list(product_ids or []),
            "rows": len(rows),
        }

    def backfill_market_local_llm_test(
        self,
        *,
        llm_service: Any,
        end_date: date | None = None,
        days: int = 3,
        product_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        if days <= 0:
            raise ValueError("days must be >= 1")

        end_date = end_date or datetime.utcnow().date()
        results: List[Dict[str, Any]] = []
        for offset in range(days - 1, -1, -1):
            target_date = end_date - timedelta(days=offset)
            generated_at = f"{target_date.isoformat()}T02:00:00Z"
            result = self.refresh_snapshot(
                as_of_date=target_date,
                generated_at=generated_at,
                narrative_mode="local_llm",
                llm_service=llm_service,
                product_ids=product_ids,
            )
            results.append(result)

        return {
            "ok": all(bool(item.get("ok")) for item in results),
            "mode": "market_local_llm_test_backfill",
            "days": days,
            "end_date": end_date.isoformat(),
            "product_ids": list(product_ids or []),
            "results": results,
        }

    def _build_erp_rows(self, generated_at: str, snapshot_id: str) -> List[dict]:
        rows: List[dict] = []
        latest_date = self._erp_kpi._get_latest_date()
        period_start = (latest_date - timedelta(days=30)).isoformat()
        period_end = latest_date.isoformat()

        for product_id in _PRODUCT_TO_MATERIAL_NAMES.keys():
            kpi = self._erp_kpi.get_product_kpi(product_id)
            growth = self._erp_kpi.get_sales_growth_pct(product_id)
            if not kpi and growth is None:
                continue

            inventory_days = kpi.inventory_days if kpi else None
            if isinstance(inventory_days, (int, float)):
                if inventory_days <= 7:
                    risk_level = "critical_low_stock"
                elif inventory_days <= 14:
                    risk_level = "low_stock"
                elif inventory_days >= 45:
                    risk_level = "high_stock"
                else:
                    risk_level = "normal"
            else:
                risk_level = "unknown"

            insight_payload = {
                "product_id": product_id,
                "period_start": period_start,
                "period_end": period_end,
                "sales_30d_ton": (kpi.sales_30d_ton if kpi else None),
                "sales_growth_pct": growth,
                "inventory_tons": (kpi.inventory_tons if kpi else None),
                "inventory_days": inventory_days,
                "utilization_pct": (kpi.utilization_pct if kpi else None),
                "risk_level": risk_level,
            }

            sales_30d_text = (
                f"{float(kpi.sales_30d_ton):,.1f}" if kpi and isinstance(kpi.sales_30d_ton, (int, float)) else "N/A"
            )
            inv_days_text = (
                f"{float(inventory_days):.1f}" if isinstance(inventory_days, (int, float)) else "N/A"
            )
            growth_text = f"{float(growth):+.2f}%" if isinstance(growth, (int, float)) else "N/A"
            narrative = (
                f"ERP snapshot {product_id}: sales_30d={sales_30d_text} tons, "
                f"growth={growth_text}, inventory_days={inv_days_text}, risk={risk_level}."
            )

            rows.append(
                {
                    "snapshot_id": snapshot_id,
                    "product_id": product_id,
                    "period_start": period_start,
                    "period_end": period_end,
                    "sales_30d_ton": (kpi.sales_30d_ton if kpi else None),
                    "sales_growth_pct": growth,
                    "inventory_tons": (kpi.inventory_tons if kpi else None),
                    "inventory_days": inventory_days,
                    "utilization_pct": (kpi.utilization_pct if kpi else None),
                    "risk_level": risk_level,
                    "insight_json": json.dumps(insight_payload, ensure_ascii=False),
                    "narrative": narrative,
                    "generated_at": generated_at,
                }
            )

        return rows

    def refresh_erp_snapshot(self) -> Dict[str, Any]:
        generated_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
        snapshot_id = str(uuid.uuid4())
        rows = self._build_erp_rows(generated_at=generated_at, snapshot_id=snapshot_id)

        if not rows:
            return {
                "ok": False,
                "snapshot_id": snapshot_id,
                "generated_at": generated_at,
                "rows": 0,
                "message": "No ERP rows available for insight snapshot.",
            }

        insert_sql = text(
            """
            INSERT INTO insight_erp_cache (
                snapshot_id,
                product_id,
                period_start,
                period_end,
                sales_30d_ton,
                sales_growth_pct,
                inventory_tons,
                inventory_days,
                utilization_pct,
                risk_level,
                insight_json,
                narrative,
                generated_at
            ) VALUES (
                :snapshot_id,
                :product_id,
                :period_start,
                :period_end,
                :sales_30d_ton,
                :sales_growth_pct,
                :inventory_tons,
                :inventory_days,
                :utilization_pct,
                :risk_level,
                :insight_json,
                :narrative,
                :generated_at
            )
            """
        )

        with self.engine.begin() as conn:
            for row in rows:
                conn.execute(insert_sql, row)

        self._prune_old_snapshots("insight_erp_cache", keep_latest=12)

        return {
            "ok": True,
            "snapshot_id": snapshot_id,
            "generated_at": generated_at,
            "rows": len(rows),
        }

    def get_bundle(
        self,
        product_ids: Optional[List[str]] = None,
        max_age_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        with self.engine.connect() as conn:
            latest_snapshot_id = conn.execute(
                text(
                    "SELECT snapshot_id FROM insight_market_cache "
                    "GROUP BY snapshot_id ORDER BY MAX(generated_at) DESC LIMIT 1"
                )
            ).scalar()

            if not latest_snapshot_id:
                return {
                    "ok": False,
                    "snapshot_id": None,
                    "generated_at": None,
                    "stale": True,
                    "items": [],
                    "reason": "no_snapshot",
                }

            params: Dict[str, Any] = {"snapshot_id": str(latest_snapshot_id)}
            where_clause = "WHERE snapshot_id = :snapshot_id"
            if product_ids:
                normalized = [str(item).strip().lower() for item in product_ids if str(item).strip()]
                if normalized:
                    placeholders = []
                    for idx, pid in enumerate(normalized):
                        key = f"pid{idx}"
                        params[key] = pid
                        placeholders.append(f":{key}")
                    where_clause += f" AND LOWER(product_id) IN ({', '.join(placeholders)})"

            rows = conn.execute(
                text(
                    "SELECT snapshot_id, product_id, name_cn, unit, as_of_date, latest_price, "
                    "weekly_change_pct, weekly_gap_value, weekly_reference_date, high_7d, low_7d, "
                    "data_source, source_detail, insight_json, narrative, generated_at "
                    f"FROM insight_market_cache {where_clause} "
                    "ORDER BY product_id"
                ),
                params,
            ).mappings().all()

        if not rows:
            return {
                "ok": False,
                "snapshot_id": str(latest_snapshot_id),
                "generated_at": None,
                "stale": True,
                "items": [],
                "reason": "empty_snapshot",
            }

        generated_at = str(rows[0].get("generated_at") or "")
        stale = False
        age_seconds: Optional[int] = None
        if generated_at:
            try:
                parsed = datetime.fromisoformat(generated_at.replace("Z", ""))
                age_seconds = max(0, int((datetime.utcnow() - parsed).total_seconds()))
                if max_age_seconds is not None and max_age_seconds > 0:
                    stale = age_seconds > max_age_seconds
            except Exception:
                stale = True

        items: List[Dict[str, Any]] = []
        for row in rows:
            payload = {}
            try:
                payload = json.loads(str(row.get("insight_json") or "{}"))
            except Exception:
                payload = {}
            items.append(
                {
                    "product_id": row.get("product_id"),
                    "name_cn": row.get("name_cn"),
                    "unit": row.get("unit"),
                    "as_of_date": row.get("as_of_date"),
                    "latest_price": row.get("latest_price"),
                    "weekly_change_pct": row.get("weekly_change_pct"),
                    "weekly_gap_value": row.get("weekly_gap_value"),
                    "weekly_reference_date": row.get("weekly_reference_date"),
                    "high_7d": row.get("high_7d"),
                    "low_7d": row.get("low_7d"),
                    "data_source": row.get("data_source"),
                    "source_detail": row.get("source_detail"),
                    "narrative": row.get("narrative"),
                    "insight": payload,
                }
            )

        return {
            "ok": True,
            "snapshot_id": str(latest_snapshot_id),
            "generated_at": generated_at,
            "age_seconds": age_seconds,
            "stale": stale,
            "count": len(items),
            "items": items,
        }

    def get_erp_bundle(
        self,
        product_ids: Optional[List[str]] = None,
        max_age_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        with self.engine.connect() as conn:
            latest_snapshot_id = conn.execute(
                text(
                    "SELECT snapshot_id FROM insight_erp_cache "
                    "GROUP BY snapshot_id ORDER BY MAX(generated_at) DESC LIMIT 1"
                )
            ).scalar()

            if not latest_snapshot_id:
                return {
                    "ok": False,
                    "snapshot_id": None,
                    "generated_at": None,
                    "stale": True,
                    "items": [],
                    "reason": "no_snapshot",
                }

            params: Dict[str, Any] = {"snapshot_id": str(latest_snapshot_id)}
            where_clause = "WHERE snapshot_id = :snapshot_id"
            if product_ids:
                normalized = [str(item).strip().lower() for item in product_ids if str(item).strip()]
                if normalized:
                    placeholders = []
                    for idx, pid in enumerate(normalized):
                        key = f"pid{idx}"
                        params[key] = pid
                        placeholders.append(f":{key}")
                    where_clause += f" AND LOWER(product_id) IN ({', '.join(placeholders)})"

            rows = conn.execute(
                text(
                    "SELECT snapshot_id, product_id, period_start, period_end, sales_30d_ton, "
                    "sales_growth_pct, inventory_tons, inventory_days, utilization_pct, risk_level, "
                    "insight_json, narrative, generated_at "
                    f"FROM insight_erp_cache {where_clause} "
                    "ORDER BY product_id"
                ),
                params,
            ).mappings().all()

        if not rows:
            return {
                "ok": False,
                "snapshot_id": str(latest_snapshot_id),
                "generated_at": None,
                "stale": True,
                "items": [],
                "reason": "empty_snapshot",
            }

        generated_at = str(rows[0].get("generated_at") or "")
        stale = False
        age_seconds: Optional[int] = None
        if generated_at:
            try:
                parsed = datetime.fromisoformat(generated_at.replace("Z", ""))
                age_seconds = max(0, int((datetime.utcnow() - parsed).total_seconds()))
                if max_age_seconds is not None and max_age_seconds > 0:
                    stale = age_seconds > max_age_seconds
            except Exception:
                stale = True

        items: List[Dict[str, Any]] = []
        for row in rows:
            payload = {}
            try:
                payload = json.loads(str(row.get("insight_json") or "{}"))
            except Exception:
                payload = {}
            items.append(
                {
                    "product_id": row.get("product_id"),
                    "period_start": row.get("period_start"),
                    "period_end": row.get("period_end"),
                    "sales_30d_ton": row.get("sales_30d_ton"),
                    "sales_growth_pct": row.get("sales_growth_pct"),
                    "inventory_tons": row.get("inventory_tons"),
                    "inventory_days": row.get("inventory_days"),
                    "utilization_pct": row.get("utilization_pct"),
                    "risk_level": row.get("risk_level"),
                    "narrative": row.get("narrative"),
                    "insight": payload,
                }
            )

        return {
            "ok": True,
            "snapshot_id": str(latest_snapshot_id),
            "generated_at": generated_at,
            "age_seconds": age_seconds,
            "stale": stale,
            "count": len(items),
            "items": items,
        }

    def _refresh_async_worker(self, kind: str) -> None:
        try:
            if kind == "erp":
                self.refresh_erp_snapshot()
            else:
                self.refresh_snapshot()
        except Exception:
            pass
        finally:
            with self._refresh_lock:
                self._refresh_inflight[kind] = False

    def _enqueue_async_refresh(self, kind: str) -> bool:
        cooldown_seconds = 30.0
        now = time.time()
        with self._refresh_lock:
            if self._refresh_inflight.get(kind, False):
                return False
            last_ts = float(self._last_refresh_enqueue_ts.get(kind, 0.0) or 0.0)
            if (now - last_ts) < cooldown_seconds:
                return False
            self._refresh_inflight[kind] = True
            self._last_refresh_enqueue_ts[kind] = now

        worker = threading.Thread(
            target=self._refresh_async_worker,
            args=(kind,),
            daemon=True,
            name=f"insight-refresh-{kind}",
        )
        worker.start()
        return True

    def get_bundle_cached(
        self,
        product_ids: Optional[List[str]] = None,
        max_age_seconds: Optional[int] = None,
        async_refresh_on_miss: bool = True,
    ) -> Dict[str, Any]:
        bundle = self.get_bundle(product_ids=product_ids, max_age_seconds=max_age_seconds)
        refresh_enqueued = False
        if async_refresh_on_miss and (not bundle.get("ok") or bool(bundle.get("stale"))):
            refresh_enqueued = self._enqueue_async_refresh("market")
        bundle["refresh_enqueued"] = refresh_enqueued
        return bundle

    def get_erp_bundle_cached(
        self,
        product_ids: Optional[List[str]] = None,
        max_age_seconds: Optional[int] = None,
        async_refresh_on_miss: bool = True,
    ) -> Dict[str, Any]:
        bundle = self.get_erp_bundle(product_ids=product_ids, max_age_seconds=max_age_seconds)
        refresh_enqueued = False
        if async_refresh_on_miss and (not bundle.get("ok") or bool(bundle.get("stale"))):
            refresh_enqueued = self._enqueue_async_refresh("erp")
        bundle["refresh_enqueued"] = refresh_enqueued
        return bundle

    def get_combined_bundle_cached(
        self,
        product_ids: Optional[List[str]] = None,
        market_max_age_seconds: Optional[int] = None,
        erp_max_age_seconds: Optional[int] = None,
        async_refresh_on_miss: bool = True,
    ) -> Dict[str, Any]:
        market_bundle = self.get_bundle_cached(
            product_ids=product_ids,
            max_age_seconds=market_max_age_seconds,
            async_refresh_on_miss=async_refresh_on_miss,
        )
        erp_bundle = self.get_erp_bundle_cached(
            product_ids=product_ids,
            max_age_seconds=erp_max_age_seconds,
            async_refresh_on_miss=async_refresh_on_miss,
        )

        merged: Dict[str, Dict[str, Any]] = {}
        for item in list(market_bundle.get("items") or []):
            product_id = str(item.get("product_id") or "").strip().lower()
            if not product_id:
                continue
            merged.setdefault(product_id, {"product_id": product_id, "market": None, "erp": None})["market"] = item

        for item in list(erp_bundle.get("items") or []):
            product_id = str(item.get("product_id") or "").strip().lower()
            if not product_id:
                continue
            merged.setdefault(product_id, {"product_id": product_id, "market": None, "erp": None})["erp"] = item

        result = {
            "ok": bool(market_bundle.get("ok") or erp_bundle.get("ok")),
            "market": market_bundle,
            "erp": erp_bundle,
            "by_product": merged,
            "count": len(merged),
            "stale": bool(market_bundle.get("stale") or erp_bundle.get("stale")),
            "refresh_enqueued": {
                "market": bool(market_bundle.get("refresh_enqueued")),
                "erp": bool(erp_bundle.get("refresh_enqueued")),
            },
        }
        result["dashboard_insights"] = build_dashboard_insights(result)
        return result


insight_snapshot_service = InsightSnapshotService()
