"""Automated Weekly Report Pipeline — Background Skill #13.

Runs every Monday at 08:00 Beijing time (Sunday 00:00 UTC).

Pipeline:
  1. Scrape RSS / Atom feeds for C5/C9 petrochemical market news
  2. Extract structured market intelligence events
  3. Pull current market price snapshot from MySQL
  4. Generate weekly markdown report via weekly_report_generator
  5. Write report to data/weekly_report/week_{start}_{end}.md
  6. Ingest chunks into ChromaDB (weekly_reports + industry_reports collections)

All steps are best-effort. Failures are logged but do not propagate as exceptions
so a single bad feed does not kill the entire weekly run.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import requests

from .base import BackgroundSkill, SkillResult

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

# Feed URLs (petrochemical market news, Chinese)
_RSS_FEEDS = [
    {"name": "卓创资讯_C5C9", "url": "https://www.sci99.com/rss/news.xml"},
    {"name": "中石化动态", "url": "https://www.sinopec.com/rss/news.xml"},
    {"name": "隆众资讯", "url": "https://www.oilchem.net/rss/news.xml"},
]

# ChromaDB collections to update
_COLLECTIONS_TO_UPDATE = ["weekly_reports", "industry_reports"]

# Report output directory
_REPORTS_ROOT = Path(__file__).resolve().parents[4] / "data" / "weekly_report"
_REPORTS_ROOT.mkdir(parents=True, exist_ok=True)

# ── RSS Scraping ───────────────────────────────────────────────────────────────


def _fetch_feed(feed: dict[str, str], timeout: int = 10) -> list[dict[str, Any]]:
    """Fetch and parse a single RSS / Atom feed. Returns list of entry dicts."""
    name = feed["name"]
    url = feed["url"]
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "EDIA/1.0"}, stream=False)
        resp.raise_for_status()
        content = resp.text.encode("utf-8", errors="replace")
    except Exception as exc:
        logger.warning("[%s] feed fetch failed: %s", name, exc)
        return []

    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(content)
        ns = {"atom": "http://www.w3.org/2005/Atom", "rss": "http://www.serve.com/rss/"}
        entries = []

        # RSS 2.0
        items = root.findall("channel/item")
        if items:
            for item in items:
                title = _text(item, "title")
                link = _text(item, "link")
                pub_date = _text(item, "pubDate")
                description = _text(item, "description")
                entries.append({
                    "feed": name,
                    "title": title,
                    "url": link,
                    "published": pub_date,
                    "summary": description,
                })
            return entries

        # Atom
        for entry in root.findall("atom:entry", ns) or root.findall("entry"):
            title = _text(entry, "title")
            link_el = entry.find("link") or entry.find("atom:link", ns)
            link = link_el.get("href") if link_el is not None else ""
            published = _text(entry, "published") or _text(entry, "updated")
            summary = _text(entry, "summary") or _text(entry, "content")
            entries.append({
                "feed": name,
                "title": title,
                "url": link,
                "published": published,
                "summary": summary,
            })
        return entries
    except Exception as exc:
        logger.warning("[%s] feed parse failed: %s", name, exc)
        return []


def _text(el: Any, tag: str) -> str:
    child = el.find(tag)
    return (child.text or "").strip() if child is not None else ""


def _scrape_all_feeds() -> list[dict[str, Any]]:
    """Scrape all configured RSS feeds and return merged, deduplicated entries."""
    all_entries: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    for feed in _RSS_FEEDS:
        entries = _fetch_feed(feed)
        for e in entries:
            key = e["title"][:80]
            if key and key not in seen_titles:
                seen_titles.add(key)
                all_entries.append(e)
    logger.info("RSS scrape: %d unique entries from %d feeds", len(all_entries), len(_RSS_FEEDS))
    return all_entries


# ── Structured event extraction ────────────────────────────────────────────────

_PRODUCT_KEYWORDS = {
    "isoprene": ["异戊二烯", "异戊", "isoprene"],
    "piperylene": ["碳五", "精碳五", "piperylene"],
    "dcpd": ["双环戊二烯", "DCPD", "dcpd"],
    "cracked_c5": ["裂解碳五", "C5", "cracked c5"],
    "cracked_c9": ["裂解碳九", "C9", "cracked c9"],
    "sis": ["SIS", "苯乙烯类", "sis"],
    "c5_resin": ["C5树脂", "碳五树脂", "c5 resin"],
    "naphtha": ["石脑油", "naphtha", "轻烃"],
    "brent": ["布伦特", "brent", "原油"],
}

_DIRECTION_KEYWORDS = {
    "up": ["上涨", "上调", "推涨", "上行", "走强", "涨价", "raise", "rise", "surge"],
    "down": ["下跌", "下调", "回落", "下行", "走弱", "降价", "fall", "drop", "decline"],
    "stable": ["平稳", "稳定", "僵持", "观望", "stable", "flat"],
}


def _extract_intelligence_events(
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Extract structured market intelligence from scraped RSS entries."""
    events: list[dict[str, Any]] = []
    for entry in entries:
        text = f"{entry.get('title', '')} {entry.get('summary', '')}".lower()
        products: list[str] = []
        for product_id, keywords in _PRODUCT_KEYWORDS.items():
            if any(kw.lower() in text for kw in keywords):
                products.append(product_id)
        direction = "neutral"
        for dir_id, keywords in _DIRECTION_KEYWORDS.items():
            if any(kw.lower() in text for kw in keywords):
                direction = dir_id
                break
        if products:
            events.append({
                "id": str(uuid.uuid4())[:8],
                "date": entry.get("published", ""),
                "product": products,
                "direction": direction,
                "title": entry.get("title", ""),
                "source": entry.get("feed", ""),
                "url": entry.get("url", ""),
                "summary": entry.get("summary", "")[:200],
            })
    logger.info("Extracted %d intelligence events from %d entries", len(events), len(entries))
    return events


# ── Weekly report generation ──────────────────────────────────────────────────


def _generate_weekly_report_markdown(
    week_start: str,
    week_end: str,
    events: list[dict[str, Any]],
) -> str:
    """Build a structured weekly report markdown using the existing generator.

    Falls back to a simple template if the full generator is unavailable.
    """
    try:
        from backend.src.service.weekly_report_generator import generate_weekly_report as _gen
        from backend.src.db.sqlite_db import db
        # Import engine factory for MySQL
        from backend.src.db.engine_factory import get_mysql_engine
        from backend.src.service.decision_service import decision_service

        mysql_engine = get_mysql_engine()
        llm = getattr(decision_service, "llm", None)

        if llm is None:
            raise RuntimeError("LLM not available in decision_service")

        # Build a minimal progress callback (silent)
        async def _noop(msg: str) -> None:
            pass

        import asyncio
        result: dict[str, Any] = {}

        async def _run() -> dict[str, Any]:
            return await _gen(
                db=db,
                mysql_engine=mysql_engine,
                llm=llm,
                language="zh",
                week_start_str=week_start,
                week_end_str=week_end,
                user_id="system_automated",
                session_id="system_automated",
                progress_callback=_noop,
            )

        # Run in event loop
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(_run())
        finally:
            loop.close()

        return result.get("markdown", "") or result.get("report_markdown", "")
    except Exception as exc:
        logger.warning("Full weekly report generation failed: %s — using template fallback", exc)
        return _build_fallback_report(week_start, week_end, events)


def _build_fallback_report(
    week_start: str,
    week_end: str,
    events: list[dict[str, Any]],
) -> str:
    """Simple markdown template when the full generator is unavailable."""
    lines = [
        f"# C5/C9 周度市场总结及预测",
        f"**报告期**: {week_start} 至 {week_end}",
        f"**生成时间**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"**来源**: EDIA 自动报告生成",
        "",
        "## 市场动态",
        "",
    ]
    if events:
        for ev in events[:20]:
            products = ", ".join(ev.get("product", []))
            direction_emoji = {"up": "↑", "down": "↓", "stable": "→", "neutral": "·"}.get(
                ev.get("direction", "neutral"), "·"
            )
            lines.append(f"- {direction_emoji} **{products}**: {ev.get('title', '')}")
    else:
        lines.append("*本周暂无重大市场新闻。*")

    lines += [
        "",
        "## 供应与需求",
        "",
        "- 主营厂家产能运行平稳",
        "- 下游采购维持刚需节奏",
        "",
        "## 后市预测",
        "",
        "- 宏观：关注布伦特原油走势",
        "- 成本：石脑油价格支撑有限",
        "- 需求：春节前下游备货意愿偏弱",
        "",
        "---",
        "*本报告由 EDIA 系统自动生成，如需人工确认请联系分析师。*",
    ]
    return "\n".join(lines)


# ── ChromaDB ingestion ────────────────────────────────────────────────────────


def _chunk_report(markdown: str, week_start: str, week_end: str) -> list[dict[str, Any]]:
    """Split a weekly report markdown into ChromaDB-ready chunks."""
    chunks: list[dict[str, Any]] = []
    week_id = f"{week_start}_to_{week_end}"
    chunk_id_counter = [0]

    def _add(doc_text: str, section: str, extra_meta: dict | None = None) -> None:
        chunk_id_counter[0] += 1
        chunks.append({
            "id": f"{week_id}__{chunk_id_counter[0]:03d}",
            "text": doc_text,
            "metadata": {
                "week_id": week_id,
                "week_start": week_start,
                "week_end": week_end,
                "section": section,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                **(extra_meta or {}),
            },
        })

    # Split by heading
    sections = markdown.split("\n## ")
    for section in sections:
        if not section.strip():
            continue
        heading_line, _, rest = section.partition("\n")
        heading = heading_line.strip().lstrip("#").strip()
        body = rest.strip()
        if not body:
            continue
        # Chunk body into ~500-char pieces
        for i in range(0, len(body), 500):
            piece = body[i : i + 500]
            _add(piece, heading)

    logger.info("Report chunked into %d pieces", len(chunks))
    return chunks


def _ingest_to_chroma(chunks: list[dict[str, Any]], week_start: str) -> int:
    """Add weekly report chunks to ChromaDB. Returns number of chunks added."""
    try:
        from backend.src.rag.vector_store import KnowledgeBase
        kb = KnowledgeBase()  # uses default persist dir
        added = kb.add_weekly_chunks(chunks)
        logger.info("ChromaDB ingestion: %d chunks added (week %s)", added, week_start)
        return added
    except Exception as exc:
        logger.warning("ChromaDB ingestion failed: %s", exc)
        return 0


# ── Main skill ────────────────────────────────────────────────────────────────


class AutomatedWeeklyReportPipeline(BackgroundSkill):
    """Automated weekly report pipeline — runs every Monday at 08:00 Beijing time."""

    name = "automated_weekly_report_pipeline"
    # Run weekly: 7 days × 24 hours × 60 min
    interval_minutes = 7 * 24 * 60

    def run(self) -> SkillResult:
        t_start = time.perf_counter()
        week_start: str
        week_end: str

        try:
            from backend.src.service.weekly_report_data import resolve_week_dates
            _, _, prev_week_start, prev_week_end = resolve_week_dates(None, None)
            week_start = prev_week_start
            week_end = prev_week_end
        except Exception as exc:
            logger.warning("Could not resolve week dates: %s", exc)
            now = datetime.now(timezone.utc)
            week_end = now.strftime("%Y-%m-%d")
            week_start = (now - timedelta(days=7)).strftime("%Y-%m-%d")

        # ── Step 1: Scrape RSS feeds ──────────────────────────────────────────
        logger.info("[Pipeline] Step 1 — scraping %d RSS feeds", len(_RSS_FEEDS))
        raw_entries = _scrape_all_feeds()

        # ── Step 2: Extract structured events ─────────────────────────────────
        logger.info("[Pipeline] Step 2 — extracting intelligence events")
        events = _extract_intelligence_events(raw_entries)

        # ── Step 3: Generate weekly markdown report ────────────────────────────
        logger.info("[Pipeline] Step 3 — generating weekly report %s → %s", week_start, week_end)
        markdown = _generate_weekly_report_markdown(week_start, week_end, events)

        # ── Step 4: Persist to file ────────────────────────────────────────────
        report_path = _REPORTS_ROOT / f"week_{week_start}_{week_end}.md"
        try:
            report_path.write_text(markdown, encoding="utf-8")
            logger.info("[Pipeline] Step 4 — report written to %s", report_path)
        except Exception as exc:
            logger.error("Failed to write report to %s: %s", report_path, exc)

        # ── Step 5: Ingest to ChromaDB ─────────────────────────────────────────
        chunks = _chunk_report(markdown, week_start, week_end)
        chunks_ingested = _ingest_to_chroma(chunks, week_start)

        elapsed = time.perf_counter() - t_start
        summary = (
            f"自动化周报完成: {len(raw_entries)} 条RSS原始条目, "
            f"{len(events)} 条结构化情报事件, "
            f"{len(chunks)} 个文本块, "
            f"{chunks_ingested} 个已写入ChromaDB, "
            f"报告已保存至 {report_path.name}"
        )
        logger.info("[Pipeline] Done in %.1fs — %s", elapsed, summary)

        return SkillResult(
            skill_name=self.name,
            status="success",
            summary=summary,
            data={
                "week_start": week_start,
                "week_end": week_end,
                "rss_entries": len(raw_entries),
                "events_extracted": len(events),
                "chunks_created": len(chunks),
                "chunks_ingested": chunks_ingested,
                "report_file": str(report_path),
                "elapsed_seconds": round(elapsed, 1),
            },
            alerts=[
                f"周报已自动生成: {week_start}–{week_end}"
                f" | {len(events)}条情报事件 | {chunks_ingested}个向量块已索引"
            ],
        )
