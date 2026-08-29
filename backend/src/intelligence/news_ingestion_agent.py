"""News Ingestion Agent — background service that monitors news sources continuously.

Uses the LLM's web_search capability (or direct search API) to pull current news
about crude oil, naphtha, petrochemical, SINOPEC, geopolitical, and plant operations.
Extracts structured ExtractedEvent objects and stores high-impact ones in ChromaDB.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlparse

from backend.src.intelligence.event_types import ExtractedEvent
from backend.src.intelligence.event_registry import intelligence_event_registry
from backend.src.intelligence.event_extractor import EventExtractor
from backend.src.intelligence.event_deduplicator import EventDeduplicator
from backend.src.intelligence.rss_feed_service import get_rss_feed_service
from backend.src.intelligence.web_search_service import get_web_search

logger = logging.getLogger(__name__)

_STATUS_AGENT_NAME = "news_ingestion_agent"
_STALE_ALERT_PRODUCT = "intelligence_system"
_STALE_ALERT_TYPE = "intelligence_stale"

_NOISY_RESULT_MARKERS = (
    "request blocked",
    "被拦截",
    "waf",
    "access denied",
    "forbidden",
    "captcha",
    "gateway timeout",
    "error page",
    "cloudflare",
    "javascript enabled",
    "please enable javascript",
    "访问受限",
    "请求存在威胁",
    "安全检测",
)

_MARKET_EVENT_KEYWORDS = (
    "price",
    "prices",
    "listing",
    "shutdown",
    "restart",
    "maintenance",
    "outage",
    "disruption",
    "force majeure",
    "opec",
    "production cut",
    "quota",
    "brent",
    "wti",
    "supply",
    "demand",
    "inventory",
    "tariff",
    "sanction",
    "shipping",
    "tanker",
    "cracker",
    "petrochemical",
    "naphtha",
    "isoprene",
    "piperylene",
    "dcpd",
    "styrene",
    "sis",
    "c5",
    "c9",
    "resin",
    "原油",
    "欧佩克",
    "油价",
    "挂牌",
    "停工",
    "停车",
    "检修",
    "复产",
    "开工",
    "供应",
    "需求",
    "库存",
    "关税",
    "制裁",
    "航运",
    "运费",
    "石脑油",
    "异戊二烯",
    "间戊二烯",
    "双环戊二烯",
    "苯乙烯",
    "石油树脂",
    "裂解",
    "碳五",
    "碳九",
)


def _is_noisy_search_result(title: str, snippet: str, url: str = "") -> bool:
    combined = " ".join(part for part in [title, snippet, url] if part).strip().lower()
    if not combined:
        return True

    if any(marker in combined for marker in _NOISY_RESULT_MARKERS):
        return True

    if re.search(r"\b(?:403|404|500|502|503|504)\b", combined):
        return True

    if re.search(r"(门户|首页|财经门户|网站导航)", combined):
        return True

    if "提供专业的财经" in combined or "博客等各类财经资讯" in combined:
        return True

    return False


def _has_market_event_signal(title: str, snippet: str) -> bool:
    combined = " ".join(part for part in [title, snippet] if part).strip().lower()
    if not combined:
        return False
    return any(keyword in combined for keyword in _MARKET_EVENT_KEYWORDS)


def _resolve_ingestion_search_mode() -> str:
    raw_value = (os.getenv("NEWS_INGESTION_SEARCH_MODE", "auto") or "auto").strip().lower()
    allowed = {"auto", "rss_only", "trusted_rss_only"}
    if raw_value in allowed:
        return raw_value
    logger.warning(
        "Invalid NEWS_INGESTION_SEARCH_MODE=%r, falling back to auto",
        raw_value,
    )
    return "auto"

# ── News source categories and search queries ─────────────────────────────

NEWS_SOURCES: dict[str, list[str]] = {
    "crude_oil": [
        "crude oil price Brent WTI geopolitical supply disruption",
        "OPEC production cut supply decision oil",
        "原油 布伦特 WTI 油价 OPEC 减产",
    ],
    "naphtha_petrochemical": [
        "naphtha petrochemical Asia C5 price supply",
        "naphtha cracker shutdown maintenance capacity",
        "石脑油 碳五 碳九 裂解 装置 检修 供应",
    ],
    "sinopec_cnpc": [
        "SINOPEC monthly listing price isoprene piperylene C5",
        "中石化 价格 异戊二烯 间戊二烯 碳五",
        "中国石油 挂牌价 炼化 装置 开停工",
    ],
    "geopolitical_shipping": [
        "Strait Hormuz tanker shipping disruption oil",
        "Red Sea shipping oil tanker Houthi",
        "航运 运费 油轮 红海 霍尔木兹",
    ],
    "china_macro": [
        "China PMI manufacturing chemical demand industrial",
        "China tariff chemical import export trade",
        "中国 PMI 工业增加值 化工 需求 出口",
    ],
    "plant_operations": [
        "petrochemical plant shutdown restart force majeure capacity",
        "cracker shutdown naphtha cracker maintenance schedule",
        "石化 装置 停车 检修 复产 不可抗力",
    ],
}

def _resolve_scan_interval_sec() -> int:
    raw_value = (os.getenv("NEWS_INGESTION_INTERVAL_SECONDS", "3600") or "3600").strip()
    try:
        return max(60, int(raw_value))
    except ValueError:
        logger.warning(
            "Invalid NEWS_INGESTION_INTERVAL_SECONDS=%r, falling back to 3600",
            raw_value,
        )
        return 3600


def _resolve_min_impact_threshold() -> float:
    raw_value = (os.getenv("NEWS_INGESTION_MIN_IMPACT", "0.5") or "0.5").strip()
    try:
        return max(0.0, min(1.0, float(raw_value)))
    except ValueError:
        logger.warning(
            "Invalid NEWS_INGESTION_MIN_IMPACT=%r, falling back to 0.5",
            raw_value,
        )
        return 0.5


def _resolve_positive_int_env(name: str, default: int, minimum: int = 1, maximum: int = 100) -> int:
    raw_value = (os.getenv(name, str(default)) or str(default)).strip()
    try:
        return max(minimum, min(maximum, int(raw_value)))
    except ValueError:
        logger.warning("Invalid %s=%r, falling back to %d", name, raw_value, default)
        return default


def _resolve_bool_env(name: str, default: bool = False) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _utcnow_iso() -> str:
    return datetime.utcnow().isoformat()


class NewsIngestionAgent:
    """Background agent that ingests news events and stores them for pipeline use.

    The agent can operate in two modes:
    1. LLM-assisted: Uses the LLM to search web and extract events (production)
    2. Manual ingestion: Events can be submitted directly via API (always available)
    """

    def __init__(self, llm_service, vector_store, override_engine=None):
        self.llm = llm_service
        self.kb = vector_store
        self.override_engine = override_engine
        self.registry = intelligence_event_registry
        self.extractor = EventExtractor(llm_service)
        self.deduplicator = EventDeduplicator(window_hours=24)
        self.rss_feed = get_rss_feed_service()
        self.web_search = get_web_search()
        self._recent_events: list[ExtractedEvent] = []
        self._lock = threading.Lock()
        self._cycle_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._scan_interval_sec = _resolve_scan_interval_sec()
        self._min_impact_threshold = _resolve_min_impact_threshold()
        self._search_mode = _resolve_ingestion_search_mode()
        self._max_queries_per_category = _resolve_positive_int_env(
            "NEWS_INGESTION_MAX_QUERIES_PER_CATEGORY", 1, minimum=1, maximum=5
        )
        self._max_results_per_query = _resolve_positive_int_env(
            "NEWS_INGESTION_MAX_RESULTS_PER_QUERY", 8, minimum=1, maximum=30
        )
        self._trusted_max_per_site = _resolve_positive_int_env(
            "NEWS_INGESTION_TRUSTED_MAX_PER_SITE", 1, minimum=1, maximum=5
        )
        self._llm_extraction_enabled = _resolve_bool_env(
            "NEWS_INGESTION_LLM_EXTRACTION_ENABLED", False
        )
        self._last_started_at: Optional[str] = None
        self._last_completed_at: Optional[str] = None
        self._last_success_at: Optional[str] = None
        self._last_error: str = ""
        self._last_cycle_duration_ms: Optional[float] = None
        self._last_articles_seen: int = 0
        self._last_events_extracted: int = 0
        self._last_events_stored: int = 0
        self._next_run_at: Optional[str] = None
        self._hydrate_status_from_registry()

    # ── Public API ────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background news scanning loop."""
        if self._running:
            return
        self._running = True
        with self._lock:
            self._next_run_at = _utcnow_iso()
        self._persist_status()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info(
            "NewsIngestionAgent started (scan every %ds)", self._scan_interval_sec
        )

    def stop(self) -> None:
        """Stop the background loop."""
        self._running = False
        with self._lock:
            self._next_run_at = None
        self._persist_status()
        if self._thread and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)

    def get_status(self) -> dict:
        """Return operational status for background monitoring and admin APIs."""
        with self._lock:
            status = {
                "enabled": True,
                "running": self._running,
                "thread_alive": bool(self._thread and self._thread.is_alive()),
                "cycle_in_progress": self._cycle_lock.locked(),
                "scan_interval_seconds": self._scan_interval_sec,
                "search_mode": self._search_mode,
                "last_started_at": self._last_started_at,
                "last_completed_at": self._last_completed_at,
                "last_success_at": self._last_success_at,
                "last_error": self._last_error or None,
                "last_cycle_duration_ms": self._last_cycle_duration_ms,
                "last_articles_seen": self._last_articles_seen,
                "last_events_extracted": self._last_events_extracted,
                "last_events_stored": self._last_events_stored,
                "next_run_at": self._next_run_at,
                "recent_event_cache_size": len(self._recent_events),
            }
        stale_after_hours = self._stale_after_hours()
        try:
            status["freshness"] = self.registry.get_ingestion_freshness(
                _STATUS_AGENT_NAME,
                stale_after_hours=stale_after_hours,
            )
        except Exception as exc:
            logger.warning("Unable to compute intelligence freshness: %s", exc)
            status["freshness"] = {
                "state": "unknown",
                "stale": True,
                "age_minutes": None,
                "last_success_at": status.get("last_success_at"),
                "stale_after_hours": stale_after_hours,
            }
        return status

    def get_recent_events(self, hours: int = 24) -> list[ExtractedEvent]:
        """Return events detected in the last N hours."""
        try:
            events = self.registry.list_recent_events(hours=hours, limit=50)
            if events:
                return events
        except Exception as e:
            logger.warning("Recent events registry lookup failed: %s", e)

        cutoff = datetime.utcnow().timestamp() - hours * 3600
        with self._lock:
            return [
                ev
                for ev in self._recent_events
                if datetime.fromisoformat(ev.detected_at).timestamp() > cutoff
            ]

    def get_high_impact_events(self, hours: int = 4) -> list[ExtractedEvent]:
        """Return only high-impact events from the last N hours (for pipeline injection)."""
        try:
            events = self.registry.list_recent_events(
                hours=hours,
                min_impact=0.50,
                limit=20,
            )
            if events:
                return events
        except Exception as e:
            logger.warning("High-impact registry lookup failed: %s", e)

        cutoff = datetime.utcnow().timestamp() - hours * 3600
        with self._lock:
            return [
                ev
                for ev in self._recent_events
                if (
                    datetime.fromisoformat(ev.detected_at).timestamp() > cutoff
                    and ev.impact_magnitude > 0.50
                )
            ]

    def ingest_manual_event(self, event_data: dict) -> ExtractedEvent:
        """Manually submit a pre-structured event (from API or weekly report parsing)."""
        payload = {
            "time_horizon": "short",
            "relevance_to_c5_c9": "direct",
            "source_credibility": "tier1",
            "affected_commodities": [],
            **{
                k: v
                for k, v in event_data.items()
                if k in ExtractedEvent.__dataclass_fields__
            },
        }
        event = ExtractedEvent(**payload)
        self._process_event(event)
        return event

    def ingest_article(
        self, article_text: str, category: str = "general", source_url: str = ""
    ) -> Optional[ExtractedEvent]:
        """Ingest a single news article text, extract event, store if relevant."""
        event = self.extractor.extract(article_text, category, source_url)
        if event and not self.deduplicator.is_duplicate(event):
            self.deduplicator.mark_seen(event)
            self._process_event(event)
            return event
        return None

    def run_news_cycle(self, trigger: str = "manual") -> list[ExtractedEvent]:
        """Run one complete news scanning cycle across all source categories.

        Uses the LLM to search for recent news and extract structured events.
        In production, this would use web_search tools; here we use the LLM
        to synthesize current market intelligence from its training data
        combined with any injected context.
        """
        if not self._cycle_lock.acquire(blocking=False):
            raise RuntimeError("News ingestion cycle already in progress")

        cycle_started_at = _utcnow_iso()
        cycle_started_perf = time.perf_counter()
        all_events: list[ExtractedEvent] = []
        articles_seen = 0
        stored_count = 0
        trusted_results: list = []

        with self._lock:
            self._last_started_at = cycle_started_at
            self._last_error = ""
            self._next_run_at = None
        self._persist_status()

        try:
            try:
                trusted_results = self.web_search.scrape_trusted_sources(
                    max_per_site=self._trusted_max_per_site
                )
                for baseline_event in self._trusted_source_baseline_events(trusted_results):
                    self._process_event(baseline_event)
                    stored_count += 1
            except Exception as e:
                logger.warning("Trusted source scrape failed for cycle trigger=%s: %s", trigger, e)

            for category, queries in NEWS_SOURCES.items():
                active_queries = queries
                if self._search_mode != "auto" and queries:
                    active_queries = [queries[0]]
                elif queries:
                    active_queries = queries[: self._max_queries_per_category]

                for query in active_queries:
                    try:
                        events, result_count = self._search_and_extract(
                            query,
                            category,
                            trusted_results=trusted_results,
                        )
                        articles_seen += result_count
                        all_events.extend(events)
                        for event in self.deduplicator.deduplicate(events):
                            if event.impact_magnitude >= self._min_impact_threshold:
                                self._process_event(event)
                                stored_count += 1
                    except Exception as e:
                        logger.warning(
                            "News cycle error for category=%s trigger=%s: %s",
                            category,
                            trigger,
                            e,
                        )

            unique = self.deduplicator.deduplicate(all_events)

            duration_ms = round((time.perf_counter() - cycle_started_perf) * 1000.0, 1)
            completed_at = _utcnow_iso()
            with self._lock:
                self._last_completed_at = completed_at
                self._last_success_at = completed_at
                self._last_cycle_duration_ms = duration_ms
                self._last_articles_seen = articles_seen
                self._last_events_extracted = len(unique)
                self._last_events_stored = stored_count
                self._next_run_at = datetime.utcfromtimestamp(
                    time.time() + self._scan_interval_sec
                ).isoformat() if self._running else None
            self._persist_status()

            logger.info(
                "News cycle complete (trigger=%s): %d raw → %d unique → %d stored in %.1f ms",
                trigger,
                len(all_events),
                len(unique),
                stored_count,
                duration_ms,
            )
            return unique
        except Exception as exc:
            duration_ms = round((time.perf_counter() - cycle_started_perf) * 1000.0, 1)
            with self._lock:
                self._last_completed_at = _utcnow_iso()
                self._last_cycle_duration_ms = duration_ms
                self._last_articles_seen = articles_seen
                self._last_events_extracted = 0
                self._last_events_stored = stored_count
                self._last_error = str(exc)
                self._next_run_at = datetime.utcfromtimestamp(
                    time.time() + self._scan_interval_sec
                ).isoformat() if self._running else None
            self._persist_status()
            raise
        finally:
            self._cycle_lock.release()

    # ── Internal ──────────────────────────────────────────────────────────

    def _search_and_extract(
        self,
        query: str,
        category: str,
        trusted_results: Optional[list] = None,
    ) -> tuple[list[ExtractedEvent], int]:
        """Search the web for real news and extract structured events.

        Uses WebSearchService (DuckDuckGo / Tavily) to find actual current
        news articles, then passes each result through the EventExtractor
        to produce structured ExtractedEvent objects.
        """
        events: list[ExtractedEvent] = []

        # Pull RSS, trusted source scraping, and search results in priority order.
        search_results = []
        seen_urls: set[str] = set()

        def _add_results(results: list) -> None:
            for result in results:
                url = getattr(result, "url", "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                search_results.append(result)

        try:
            rss_results = self.rss_feed.fetch_category(category, max_results=5)
            _add_results(rss_results)
        except Exception as e:
            logger.warning("RSS fetch failed for category=%s: %s", category, e)

        if trusted_results:
            _add_results(trusted_results)

        if self._search_mode == "auto":
            try:
                news_results = self.web_search.search_news(
                    query, max_results=5, timelimit="w"
                )
                _add_results(news_results)
            except Exception as e:
                logger.warning("News search failed for '%s': %s", query, e)

            try:
                cn_results = self.web_search.search_chinese(query, max_results=5)
                _add_results(cn_results)
            except Exception as e:
                logger.warning("Chinese-region search failed for '%s': %s", query, e)

        # If RSS + news search returned few results, supplement with general search.
        if self._search_mode == "auto" and len(search_results) < 3:
            try:
                text_results = self.web_search.search(query, max_results=5)
                _add_results(text_results)
            except Exception as e:
                logger.warning("Text search failed for '%s': %s", query, e)

        if not search_results:
            logger.debug("No web search results for '%s'", query)
            return [], 0

        total_results = len(search_results)
        if total_results > self._max_results_per_query:
            search_results = search_results[: self._max_results_per_query]
            logger.info(
                "Intelligence search capped results for '%s' at %d of %d",
                query[:60],
                len(search_results),
                total_results,
            )

        logger.info(
            "Intelligence search returned %d results for '%s' (category=%s)",
            len(search_results),
            query[:60],
            category,
        )

        # 2. Extract events from each search result using EventExtractor
        for result in search_results:
            try:
                if _is_noisy_search_result(
                    str(getattr(result, "title", "") or ""),
                    str(getattr(result, "snippet", "") or ""),
                    str(getattr(result, "url", "") or ""),
                ):
                    continue

                article_text = f"{result.title}\n\n{result.snippet}"
                event = self._fallback_event_from_result(result, category)
                if event is None and self._llm_extraction_enabled:
                    event = self.extractor.extract(
                        article_text,
                        category=category,
                        source_url=result.url,
                    )
                if event and not self.deduplicator.is_duplicate(event):
                    events.append(event)
            except Exception as e:
                logger.debug(
                    "Event extraction failed for result '%s': %s", result.title[:40], e
                )

        return events, len(search_results)

    def _fallback_event_from_result(self, result, category: str) -> Optional[ExtractedEvent]:
        """Build a minimal structured event from search metadata when LLM skips extraction."""
        title = str(getattr(result, "title", "") or "").strip()
        snippet = str(getattr(result, "snippet", "") or "").strip()
        source_url = str(getattr(result, "url", "") or "").strip()
        source_name = str(getattr(result, "source", "") or "").strip().lower()
        source_domain = (urlparse(source_url).hostname or "").lower()
        text = f"{title} {snippet}".strip()
        if len(text) < 30:
            return None

        if _is_noisy_search_result(title, snippet, source_url):
            return None

        lowered = text.lower()
        trusted_hosts = set()
        for raw in os.getenv("TRUSTED_SOURCE_URLS", "").split(","):
            host = (urlparse(raw.strip()).hostname or "").lower()
            if host:
                trusted_hosts.add(host)

        has_signal_keyword = _has_market_event_signal(title, snippet)
        is_trusted_source = any(
            source_domain == host or source_domain.endswith(f".{host}")
            for host in trusted_hosts
        )

        # Trusted source alone is not enough; the result still needs an explicit
        # market/event signal so portal pages and block pages do not enter the DB.
        if not has_signal_keyword:
            return None

        if not is_trusted_source and category in {"sinopec_cnpc", "plant_operations", "naphtha_petrochemical"}:
            return None

        event_type_map = {
            "crude_oil": "opec_decision",
            "naphtha_petrochemical": "demand_signal",
            "sinopec_cnpc": "sinopec_price_change",
            "geopolitical_shipping": "shipping_disruption",
            "china_macro": "demand_signal",
            "plant_operations": "plant_outage",
        }
        commodity_map = {
            "crude_oil": ["crude_oil"],
            "naphtha_petrochemical": ["naphtha", "c5", "c9_resin"],
            "sinopec_cnpc": ["isoprene", "piperylene", "c5"],
            "geopolitical_shipping": ["crude_oil", "naphtha"],
            "china_macro": ["naphtha", "c5", "c9_resin"],
            "plant_operations": ["naphtha", "c5", "c9_resin"],
        }

        direction = "uncertain"
        if any(k in lowered for k in ("rise", "surge", "tight", "shortage", "上涨", "走强", "紧张")):
            direction = "bullish"
        elif any(k in lowered for k in ("fall", "drop", "oversupply", "decline", "下跌", "走弱", "过剩")):
            direction = "bearish"

        source_credibility = "tier1" if any(
            marker in source_name for marker in ("gov", "xinhua", "jin10", "cnpc", "sinopec")
        ) else "tier2"

        headline = title or text[:160]
        source_excerpt = (snippet or title or text)[:240].strip()
        if source_excerpt:
            causal_hint = f"Source key information: {source_excerpt}"
        else:
            causal_hint = "Source key information unavailable."

        magnitude_estimate = "medium" if category in {
            "crude_oil",
            "geopolitical_shipping",
            "sinopec_cnpc",
            "plant_operations",
        } else "small"

        return ExtractedEvent(
            event_type=event_type_map.get(category, "demand_signal"),
            headline=headline[:180],
            affected_commodities=commodity_map.get(category, ["naphtha", "c5", "c9_resin"]),
            direction=direction,
            magnitude_estimate=magnitude_estimate,
            certainty="likely",
            time_horizon="short",
            geographic_scope="china" if "china" in category or "sinopec" in category else "asia_pacific",
            relevance_to_c5_c9="direct" if category in {"naphtha_petrochemical", "sinopec_cnpc", "plant_operations"} else "indirect",
            key_entities=[source_domain or source_name or "trusted_source", category],
            causal_chain_hint=causal_hint,
            source_url=source_url,
            source_credibility=source_credibility,
            full_text=text[:2000],
            raw_quote=(snippet or title)[:180],
        )

    def _trusted_source_baseline_events(self, trusted_results: list) -> list[ExtractedEvent]:
        """Create one lightweight baseline event per trusted source result."""
        baseline_events: list[ExtractedEvent] = []
        seen_domains: set[str] = set()

        for result in trusted_results:
            source_url = str(getattr(result, "url", "") or "").strip()
            if not source_url:
                continue
            domain = (urlparse(source_url).hostname or "").lower()
            if not domain or domain in seen_domains:
                continue
            seen_domains.add(domain)

            title = str(getattr(result, "title", "") or "").strip() or f"Trusted source baseline: {domain}"
            snippet = str(getattr(result, "snippet", "") or "").strip()
            event_date = datetime.utcnow().strftime("%Y%m%d")
            event_domain = re.sub(r"[^a-z0-9]+", "_", domain.lower()).strip("_")
            baseline_events.append(
                ExtractedEvent(
                    event_id=f"trusted_{event_domain}_{event_date}",
                    event_type="demand_signal",
                    headline=title[:180],
                    affected_commodities=["naphtha", "c5", "c9_resin"],
                    direction="uncertain",
                    magnitude_estimate="medium",
                    certainty="likely",
                    time_horizon="short",
                    geographic_scope="china",
                    relevance_to_c5_c9="direct",
                    key_entities=[domain, "trusted_source_baseline"],
                    causal_chain_hint="Trusted-source baseline capture for DB-backed intelligence context.",
                    source_url=source_url,
                    source_credibility="tier2",
                    full_text=(f"{title}\n\n{snippet}")[:2000],
                    raw_quote=snippet[:180],
                )
            )

        return baseline_events

    def _process_event(self, event: ExtractedEvent) -> None:
        """Store event in memory, ChromaDB, and evaluate for auto-override."""
        with self._lock:
            self._recent_events.append(event)
            # Keep only last 200 events in memory
            if len(self._recent_events) > 200:
                self._recent_events = self._recent_events[-200:]

        try:
            self.registry.upsert_event(event)
        except Exception as e:
            logger.warning("MySQL event registry write failed: %s", e)

        # Store in ChromaDB news_events collection
        try:
            self.kb.add_news_event(
                {
                    "headline": event.headline,
                    "body": event.causal_chain_hint,
                    "source": event.source_url or "web_search",
                    "category": event.event_type,
                    "impact": event.direction,
                    "date": event.detected_at[:10],
                }
            )
        except Exception as e:
            logger.debug("ChromaDB storage failed: %s", e)

        # Phase D: Evaluate for auto-override injection
        if self.override_engine and event.impact_magnitude >= self._min_impact_threshold:
            try:
                self._evaluate_override(event)
            except Exception as exc:
                logger.debug("Auto-override evaluation failed for event %s: %s", event.event_id, exc)

    def _evaluate_override(self, event: ExtractedEvent) -> None:
        """Build fast causal analysis and run auto-override decision."""
        from backend.src.intelligence.causal_narrative_engine import (
            CausalChainResult,
            _APPROX_PRICES_CNY,
            _NAPHTHA_ELASTICITIES,
        )
        from backend.src.intelligence.second_order_effects import (
            compute_total_second_order_pct,
            get_product_specific_effects,
        )

        # Fast causal mapping (mirrors intelligence_orchestrator._build_fast_causal_analysis)
        entry_commodity = "crude_oil"
        if event.event_type in {"plant_outage", "sinopec_price_change", "demand_signal"}:
            entry_commodity = "naphtha"
        elif event.event_type in {"currency_move", "tariff_policy"}:
            entry_commodity = "exchange_rate"

        primary_pct = 0.0
        if event.magnitude_estimate == "small":
            primary_pct = 2.0
        elif event.magnitude_estimate == "medium":
            primary_pct = 5.0
        elif event.magnitude_estimate == "large":
            primary_pct = 10.0
        elif event.magnitude_estimate == "extreme":
            primary_pct = 20.0
        if event.direction == "bearish":
            primary_pct = -primary_pct

        # Crude → naphtha pass-through (simplified)
        if entry_commodity == "crude_oil":
            naphtha_base_pct = primary_pct * 0.75
        elif entry_commodity == "naphtha":
            naphtha_base_pct = primary_pct
        else:
            naphtha_base_pct = primary_pct * 0.40

        second_order_total = compute_total_second_order_pct(event.event_type) * 100
        naphtha_total_pct = naphtha_base_pct + second_order_total

        product_impacts = {}
        product_specific_effects = get_product_specific_effects(event.event_type)
        for product_id, elasticity in _NAPHTHA_ELASTICITIES.items():
            pct_impact = naphtha_total_pct * elasticity / 100
            product_extra = product_specific_effects.get(product_id, 0.0)
            total_pct = pct_impact + product_extra
            approx_price = _APPROX_PRICES_CNY.get(product_id, 5000)
            product_impacts[product_id] = {
                "pct_impact": round(total_pct * 100, 2),
                "cny_impact": round(approx_price * total_pct, 0),
                "causal_path": f"event → {entry_commodity} → naphtha → {product_id}",
                "elasticity_used": round(elasticity, 3),
                "second_order_pct": round(product_extra * 100, 2),
            }

        narrative = str(
            getattr(event, "causal_chain_hint", "")
            or getattr(event, "key_information", "")
            or getattr(event, "raw_quote", "")
            or getattr(event, "full_text", "")
            or getattr(event, "headline", "")
        ).strip()

        # Compute confidence decay
        confidence_by_horizon = {"3d": 0.85, "7d": 0.70, "30d": 0.50}
        if event.certainty == "confirmed":
            confidence_by_horizon = {"3d": 0.95, "7d": 0.90, "30d": 0.75}
        elif event.certainty == "likely":
            confidence_by_horizon = {"3d": 0.85, "7d": 0.75, "30d": 0.55}
        elif event.certainty == "speculative":
            confidence_by_horizon = {"3d": 0.60, "7d": 0.45, "30d": 0.30}

        recommended_override_type = event.event_type
        if event.event_type in {"geopolitical_supply_disruption", "shipping_disruption", "sanctions"}:
            recommended_override_type = "geopolitical_supply_disruption"
        elif event.event_type in {"plant_outage", "force_majeure"}:
            recommended_override_type = "plant_outage"

        causal_chain = CausalChainResult(
            event=event.to_dict(),
            entry_commodity=entry_commodity,
            primary_impact_pct=round(primary_pct, 2),
            naphtha_total_pct=round(naphtha_total_pct, 2),
            product_impacts=product_impacts,
            causal_narrative=narrative,
            confidence_by_horizon=confidence_by_horizon,
            historical_precedents={},
            recommended_override_type=recommended_override_type,
            second_order_summary={},
        )

        decision = self.override_engine.decide(event, causal_chain)
        logger.info(
            "Override decision for event %s: action=%s confidence=%.2f shock=%+.2f%%",
            event.event_id,
            decision.action,
            decision.injection_confidence,
            decision.naphtha_shock_pct,
        )

    def _run_loop(self) -> None:
        """Background loop: run one cycle, then sleep until the next interval."""
        while self._running:
            self._run_scheduled_cycle_with_retry()

            remaining = self._scan_interval_sec
            while self._running and remaining > 0:
                sleep_chunk = min(1.0, float(remaining))
                time.sleep(sleep_chunk)
                remaining -= sleep_chunk

    def _stale_after_hours(self) -> int:
        raw_value = (os.getenv("NEWS_INGESTION_STALE_AFTER_HOURS", "2") or "2").strip()
        try:
            return max(1, int(raw_value))
        except ValueError:
            return 2

    def _retry_attempts(self) -> int:
        raw_value = (os.getenv("NEWS_INGESTION_RETRY_ATTEMPTS", "1") or "1").strip()
        try:
            return max(0, int(raw_value))
        except ValueError:
            return 1

    def _retry_backoff_seconds(self) -> int:
        raw_value = (os.getenv("NEWS_INGESTION_RETRY_BACKOFF_SECONDS", "300") or "300").strip()
        try:
            return max(5, int(raw_value))
        except ValueError:
            return 300

    def _status_payload(self) -> dict:
        with self._lock:
            return {
                "running": self._running,
                "cycle_in_progress": self._cycle_lock.locked(),
                "scan_interval_seconds": self._scan_interval_sec,
                "last_started_at": self._last_started_at,
                "last_completed_at": self._last_completed_at,
                "last_success_at": self._last_success_at,
                "last_error": self._last_error or None,
                "last_cycle_duration_ms": self._last_cycle_duration_ms,
                "last_articles_seen": self._last_articles_seen,
                "last_events_extracted": self._last_events_extracted,
                "last_events_stored": self._last_events_stored,
                "next_run_at": self._next_run_at,
            }

    def _persist_status(self) -> None:
        try:
            self.registry.upsert_ingestion_status(_STATUS_AGENT_NAME, self._status_payload())
        except Exception as exc:
            logger.warning("Unable to persist intelligence ingestion status: %s", exc)

    def _run_scheduled_cycle_with_retry(self) -> None:
        retries = self._retry_attempts()
        backoff_seconds = self._retry_backoff_seconds()
        last_error: Optional[Exception] = None

        for attempt in range(retries + 1):
            trigger = "scheduled" if attempt == 0 else f"scheduled_retry_{attempt}"
            try:
                self.run_news_cycle(trigger=trigger)
                return
            except Exception as exc:
                last_error = exc
                is_final_attempt = attempt >= retries
                if is_final_attempt:
                    logger.error("News cycle failed after retries: %s", exc)
                    self._emit_stale_alert_if_needed()
                    return

                wait_seconds = backoff_seconds * attempt if attempt > 0 else backoff_seconds
                logger.warning(
                    "News cycle attempt %d/%d failed: %s; retrying in %ds",
                    attempt + 1,
                    retries + 1,
                    exc,
                    wait_seconds,
                )
                self._sleep_with_stop(wait_seconds)

        if last_error:
            logger.error("Unexpected news cycle retry loop termination: %s", last_error)

    def _sleep_with_stop(self, seconds: int) -> None:
        remaining = max(0, int(seconds))
        while self._running and remaining > 0:
            time.sleep(min(1.0, float(remaining)))
            remaining -= 1

    def _emit_stale_alert_if_needed(self) -> None:
        try:
            freshness = self.registry.get_ingestion_freshness(
                _STATUS_AGENT_NAME,
                stale_after_hours=self._stale_after_hours(),
            )
            if not freshness.get("stale"):
                return

            from backend.src.service.alert_service import AlertDB

            alert_db = AlertDB()
            if alert_db.recent_alert_exists(
                _STALE_ALERT_PRODUCT,
                _STALE_ALERT_TYPE,
                window_days=1,
            ):
                return

            age_minutes = freshness.get("age_minutes")
            if isinstance(age_minutes, int):
                age_text = f"{age_minutes} minute(s)"
            else:
                age_text = "an unknown amount of time"

            message = (
                "News intelligence freshness is stale. "
                f"Last successful ingestion was {age_text} ago. "
                "Forecasting and event-aware features may be using outdated intelligence."
            )
            alert_db.insert_alert(
                {
                    "id": str(uuid.uuid4()),
                    "product": _STALE_ALERT_PRODUCT,
                    "product_name": "News Intelligence",
                    "alert_type": _STALE_ALERT_TYPE,
                    "severity": "high",
                    "change_7d_pct": 0.0,
                    "price": 0.0,
                    "message": message,
                    "triggered_at": datetime.now().isoformat(),
                }
            )
        except Exception as exc:
            logger.warning("Unable to emit stale intelligence alert: %s", exc)

    def _hydrate_status_from_registry(self) -> None:
        try:
            status = self.registry.get_ingestion_status(_STATUS_AGENT_NAME)
        except Exception as exc:
            logger.warning("Unable to load intelligence ingestion status: %s", exc)
            return

        if not status:
            return

        self._last_started_at = status.get("last_started_at") or None
        self._last_completed_at = status.get("last_completed_at") or None
        self._last_success_at = status.get("last_success_at") or None
        self._last_error = str(status.get("last_error") or "")
        self._last_cycle_duration_ms = status.get("last_cycle_duration_ms")
        self._last_articles_seen = int(status.get("last_articles_seen") or 0)
        self._last_events_extracted = int(status.get("last_events_extracted") or 0)
        self._last_events_stored = int(status.get("last_events_stored") or 0)
        self._next_run_at = status.get("next_run_at") or None
