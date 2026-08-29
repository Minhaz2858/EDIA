from fastapi import FastAPI, HTTPException, UploadFile, File, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from pathlib import Path
import shutil
import os
from dotenv import load_dotenv

# Load environment variables before backend module imports that may initialize
# network-bound components (e.g., embedding models) during import time.
_BOOT_REPO_ROOT = Path(__file__).resolve().parents[1]
_REPORT_IMAGE_UPLOAD_DIR = _BOOT_REPO_ROOT / "backend" / "uploads" / "report_images"
_REPORT_IMAGE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
load_dotenv(dotenv_path=_BOOT_REPO_ROOT / ".env")
if str(os.getenv("HF_HUB_OFFLINE", "") or "").strip().lower() in {"1", "true", "yes", "on"}:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
if str(os.getenv("TRANSFORMERS_OFFLINE", "") or "").strip().lower() in {"1", "true", "yes", "on"}:
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

_CJK_FONT_NAMES = [
    "Alibaba PuHuiTi",
    "Alibaba PuHuiTi 2.0",
    "Source Han Sans SC",
    "思源黑体",
    "Noto Sans CJK SC",
]
_CJK_PRIMARY_FONT = _CJK_FONT_NAMES[0]
_CJK_FONT_PATHS = [
    Path("/usr/share/fonts/truetype/alibaba-puhuiti/AlibabaPuHuiTi-3-55-Regular.ttf"),
    Path("/usr/share/fonts/truetype/alibaba/Alibaba-PuHuiTi-Regular.ttf"),
    Path("/usr/share/fonts/opentype/source-han-sans/SourceHanSansSC-Regular.otf"),
    Path("/usr/share/fonts/adobe-source-han-sans/SourceHanSansSC-Regular.otf"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc"),
]

import asyncio
import io
import html
import json
from backend.src.nlu.llm_service import RealLLMService
from backend.src.skills.response import apply_response_enhancement_skills
from backend.src.service.decision_service import DecisionService
from backend.src.db.sqlite_db import db
from backend.src.service.market_data_service import market_data
from backend.src.service.fx_rate_service import build_fx_rate_payload
from backend.src.service.kimi_forecast_feature_service import kimi_forecast_feature_service
from backend.src.service.enterprise_data_service import enterprise_data
from backend.src.service.forecasting_service import forecasting_service
from backend.src.service.weekly_plan_service import weekly_plan_service
from backend.src.service.macro_override_service import (
    get_active_override,
    set_override as _set_override,
    clear_override as _clear_override,
    override_summary,
)
from backend.src.service.oilchem_sync_service import oilchem_sync_service
from backend.src.service.alert_service import alert_worker
from backend.src.service.insight_snapshot_service import insight_snapshot_service
from backend.src.domain.knowledge_graph import domain_graph
from backend.src.service.confidence_scorer import confidence_scorer
from backend.src.service.decision_audit import decision_history
from backend.src.service.decision_log_service import decision_log_service
from backend.src.validation.backtest_runner import backtest_runner
from backend.src.planner.llm_router import synthesize_streaming as planner_synthesize_streaming
import uvicorn
import sys
import csv
import logging
import re
import smtplib
import threading as _threading
import queue as _queue
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from typing import Optional, List, Dict, Any, AsyncIterator
import time
import copy
from uuid import uuid4
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from backend.src.service.auth_service import normalize_username, verify_password
from backend.src.service.types import DecisionReport

logger = logging.getLogger(__name__)

# Human-readable labels for SSE stage events
_STAGE_LABELS: Dict[str, Dict[str, str]] = {
    "parse_intent": {"en": "Understanding your question...", "zh": "正在理解你的问题..."},
    "extract_context": {"en": "Identifying products and context...", "zh": "正在识别产品与上下文..."},
    "validate_context": {"en": "Validating context...", "zh": "正在校验上下文..."},
    "parallel_fetch": {"en": "Fetching market data in parallel...", "zh": "正在并行获取市场数据..."},
    "perception": {"en": "Loading market snapshot...", "zh": "正在加载市场快照..."},
    "intelligence_check": {"en": "Checking intelligence alerts...", "zh": "正在检查情报预警..."},
    "web_search": {"en": "Searching recent news...", "zh": "正在检索近期资讯..."},
    "research_enrichment": {"en": "Scoring evidence quality...", "zh": "正在评估证据质量..."},
    "resolve_product": {"en": "Resolving product...", "zh": "正在解析产品对象..."},
    "gather_context": {"en": "Gathering context from knowledge base...", "zh": "正在从知识库收集上下文..."},
    "diagnosis": {"en": "Running market diagnosis...", "zh": "正在执行市场诊断..."},
    "forecast": {"en": "Generating price forecast...", "zh": "正在生成价格预测..."},
    "pricing": {"en": "Computing pricing decision...", "zh": "正在计算定价决策..."},
    "evidence_compile": {"en": "Compiling evidence bundle...", "zh": "正在汇总证据包..."},
    "decision_critic": {"en": "Quality-checking decision...", "zh": "正在进行决策质量校验..."},
    "tool_execution": {"en": "Running analytical tools...", "zh": "正在执行分析工具..."},
    "erp_data_retrieval": {"en": "Querying internal business data...", "zh": "正在查询内部经营数据..."},
    "assemble_prompt": {"en": "Assembling final analysis...", "zh": "正在组装最终分析..."},
    "short_circuit_check": {"en": "Preparing direct response...", "zh": "正在准备直接回复..."},
    "generate_report": {"en": "Generating report...", "zh": "正在生成报告..."},
    "clarify_intent": {"en": "Preparing clarification...", "zh": "正在准备澄清问题..."},
    "thin_evidence": {"en": "Checking data availability...", "zh": "正在检查数据可用性..."},
    "conversational_direct": {"en": "Composing reply...", "zh": "正在组织回复..."},
}


def _localized_stage_label(stage_name: str, language: str) -> str:
    normalized_language = "en" if str(language or "").strip().lower() == "en" else "zh"
    key = str(stage_name or "").strip()
    localized = _STAGE_LABELS.get(key) or {}
    if normalized_language == "en":
        return localized.get("en") or key.replace("_", " ").title()
    return localized.get("zh") or "处理中..."

_EXCLUDED_PRODUCT_IDS = {"hydrogenated_c9"}
_EXCLUDED_PRODUCT_NAMES = {"加氢碳九"}


def _is_excluded_product_id(product_id: Optional[str]) -> bool:
    return str(product_id or "").strip().lower() in _EXCLUDED_PRODUCT_IDS


def _is_excluded_product_name(product_name: Optional[str]) -> bool:
    return str(product_name or "").strip() in _EXCLUDED_PRODUCT_NAMES


def _is_excluded_product_entry(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False

    product_id = entry.get("product_id") or entry.get("id")
    if _is_excluded_product_id(product_id):
        return True

    for key in ("name_cn", "name_en", "name", "product_name"):
        if _is_excluded_product_name(entry.get(key)):
            return True
    return False


def _filter_excluded_product_entries(items: Any) -> Any:
    if not isinstance(items, list):
        return items
    return [item for item in items if not _is_excluded_product_entry(item)]


def _ensure_product_allowed(product_id: str) -> None:
    if _is_excluded_product_id(product_id):
        raise HTTPException(status_code=404, detail=f"Unknown product: {product_id}")

# Load environment variables (ensure this works regardless of CWD)
_REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(dotenv_path=_REPO_ROOT / ".env")


def _env_flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _strict_kimi_only_mode() -> bool:
    """When true, final answers use external LLM only (DeepSeek via EXTERNAL_LLM_*)."""
    from backend.src.nlu.external_llm_config import strict_external_llm_only

    return strict_external_llm_only()


def _database_only_mode() -> bool:
    return _env_flag("DATABASE_ONLY_MODE", "true")


def _live_data_only_mode() -> bool:
    return _env_flag("LIVE_DATA_ONLY", "true")


def _news_ingestion_agent_enabled() -> bool:
    return _env_flag("ENABLE_NEWS_INGESTION_AGENT", "false")


def _should_start_news_ingestion_agent() -> bool:
    return (
        bool(_intel_news_agent)
        and LLM_MODE == "real"
        and _news_ingestion_agent_enabled()
    )


def _pricing_background_enabled() -> bool:
    if _live_data_only_mode():
        return False
    default = "false" if sys.platform == "darwin" else "true"
    return _env_flag("ENABLE_PRICING_BACKGROUND_CACHE", default)


def _background_workers_enabled() -> bool:
    default = "false" if sys.platform == "darwin" else "true"
    return _env_flag("ENABLE_BACKGROUND_WORKERS", default)


def _insight_precompute_enabled() -> bool:
    if _live_data_only_mode():
        return False
    default = "false" if sys.platform == "darwin" else "true"
    return _env_flag("ENABLE_INSIGHT_PRECOMPUTE", default)


def _kimi_forecast_features_enabled() -> bool:
    if _live_data_only_mode():
        return False
    default = "false" if sys.platform == "darwin" else "true"
    if str(os.getenv("ENABLE_EXTERNAL_FORECAST_FEATURES", "") or "").strip():
        return _env_flag("ENABLE_EXTERNAL_FORECAST_FEATURES", default)
    if str(os.getenv("ENABLE_KIMI_FORECAST_FEATURES", "") or "").strip():
        return _env_flag("ENABLE_KIMI_FORECAST_FEATURES", default)
    return _env_flag("ENABLE_EXTERNAL_FORECAST_FEATURES", default)


def _forecast_shadow_self_improvement_enabled() -> bool:
    default = "false" if sys.platform == "darwin" else "true"
    return _env_flag("ENABLE_FORECAST_SHADOW_SELF_IMPROVEMENT", default)


def _pricing_parallelism() -> int:
    default = "1" if sys.platform == "darwin" else "4"
    raw_value = (os.getenv("PRICING_ACTIONS_PARALLELISM", default) or default).strip()
    try:
        return max(1, int(raw_value))
    except ValueError:
        return int(default)


def _market_perf_log_enabled() -> bool:
    return _env_flag("MARKET_PERF_LOG", "false")


def _normalize_chat_mode(mode_value: Optional[object]) -> str:
    mode = str(mode_value or "").strip().lower()
    if mode in {"analysis", "report", "deep_research"}:
        return mode
    return "chat"


def _resolve_request_chat_mode(
    message: str,
    prefs: Optional[Dict[str, Any]] = None,
    request_chat_mode: Optional[object] = None,
) -> str:
    allowed_modes = {"chat", "analysis", "report", "deep_research"}

    explicit_mode = str(request_chat_mode or "").strip().lower()
    if explicit_mode in allowed_modes:
        return explicit_mode

    pref_mode = str((prefs or {}).get("chat_mode") or "").strip().lower()
    if pref_mode in allowed_modes:
        return pref_mode

    try:
        from backend.src.engine.decision_graph import _classify_chat_mode
        from backend.src.nlu.intent_parser import _fast_intent

        task_type, _confidence = _fast_intent(str(message or ""))
        inferred_mode, _ = _classify_chat_mode(
            message or "",
            user_prefs=dict(prefs or {}),
            task_type=task_type,
            explanation_mode=False,
        )
        inferred_mode = str(inferred_mode or "").strip().lower()
        if inferred_mode in allowed_modes:
            return inferred_mode
    except Exception:
        pass

    try:
        from backend.src.engine.decision_graph import _is_business_data_task
        from backend.src.nlu.intent_parser import _fast_intent

        task_type, _confidence = _fast_intent(str(message or ""))
        if _is_business_data_task(task_type, str(message or "")):
            return "analysis"
    except Exception:
        pass

    return "chat"


def _log_market_timing(endpoint: str, started_at: float, **fields: Any) -> None:
    if not _market_perf_log_enabled():
        return
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    metadata = " ".join(f"{k}={fields[k]}" for k in sorted(fields))
    if metadata:
        logger.info("PERF %s %.1fms %s", endpoint, elapsed_ms, metadata)
    else:
        logger.info("PERF %s %.1fms", endpoint, elapsed_ms)


def _refresh_market_data_on_startup() -> None:
    refresh_on_startup = _env_flag("MARKET_PRICES_REFRESH_ON_STARTUP", "true")
    reset_before_refresh = _env_flag("MARKET_PRICES_REFRESH_RESET", "true")

    if refresh_on_startup:
        from backend.src.db.load_market_prices import load_all

        loaded_rows = load_all(reset=reset_before_refresh)
        print(f"Market prices refreshed on startup: {loaded_rows:,} rows")

    market_data.refresh()
    _set_market_runtime_fingerprint(_read_market_live_fingerprint())
    print("Market data cache refreshed")


async def _refresh_market_data_on_startup_async() -> None:
    try:
        await asyncio.to_thread(_refresh_market_data_on_startup)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Market data refresh on startup failed")


async def _prime_snapshot_cache_after_startup() -> None:
    """Pre-compute market snapshot in background so the first dashboard load is fast."""
    try:
        # Wait for market data refresh to finish first
        await asyncio.sleep(10)
        loop = asyncio.get_event_loop()
        snapshot = await loop.run_in_executor(None, _compute_market_snapshot)
        with _market_runtime_state_lock:
            _market_snapshot_cache["data"] = copy.deepcopy(snapshot)
            _market_snapshot_cache["ts"] = time.time()
        logger.info("Market snapshot cache primed: %d products", len(snapshot))
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Failed to prime snapshot cache at startup")


class _SSEHub:
    """In-process pub/sub hub for server-sent events."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=16)
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    async def publish(self, event: str, payload: Optional[Dict[str, Any]] = None) -> None:
        envelope = {
            "event": event,
            "payload": payload or {},
            "ts": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        }
        async with self._lock:
            subscribers = list(self._subscribers)

        for queue in subscribers:
            if queue.full():
                try:
                    queue.get_nowait()
                except Exception:
                    pass
            try:
                queue.put_nowait(envelope)
            except Exception:
                continue


_market_live_hub = _SSEHub()
_market_live_watch_task: Optional[asyncio.Task] = None
_morning_briefing_task: Optional[asyncio.Task] = None
_insight_precompute_task: Optional[asyncio.Task] = None
_kimi_forecast_feature_task: Optional[asyncio.Task] = None
_forecast_shadow_self_improvement_task: Optional[asyncio.Task] = None
_market_startup_refresh_task: Optional[asyncio.Task] = None
_market_runtime_state_lock = _threading.Lock()
_market_runtime_fingerprint: Optional[Dict[str, Any]] = None
_market_runtime_last_checked_ts = 0.0
_market_table_columns_cache: Dict[str, List[str]] = {}
_market_forecast_cache_lock = _threading.Lock()
_platform_refresh_lock = _threading.Lock()  # guards platform product refresh sequence

# ── Multi-worker singleton guard ─────────────────────────────────
# When running with --workers N, uvicorn spawns N independent
# processes *without* assigning worker IDs.  Background loops that
# must run exactly once (market live watch, forecast precompute,
# etc.) can use this file-lock guard so only one worker carries
# them out.
try:
    import fcntl as _fcntl
    _singleton_lock_path = os.path.join(
        os.environ.get("TMPDIR", "/tmp"),
        "edia_backend_singleton.lock",
    )
    _singleton_lock_fd = os.open(_singleton_lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        _fcntl.flock(_singleton_lock_fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        _is_singleton_worker = True
    except (BlockingIOError, OSError):
        _is_singleton_worker = False
except Exception:
    # Fallback: on platforms without fcntl (Windows), every worker
    # runs the loops — wasteful but not harmful.
    _is_singleton_worker = True
    _singleton_lock_fd = -1
_MARKET_AUTO_REFRESH_CHECK_SECONDS = max(
    0.0, float(os.getenv("MARKET_AUTO_REFRESH_CHECK_SECONDS", "15"))
)
_MARKET_LIVE_WATCH_INTERVAL_SECONDS = max(
    0.5,
    float(
        os.getenv(
            "MARKET_LIVE_WATCH_INTERVAL_SECONDS",
            "0.5",
        )
    ),
)
_INSIGHT_PRECOMPUTE_INTERVAL_SECONDS = max(
    60.0,
    float(os.getenv("INSIGHT_PRECOMPUTE_INTERVAL_SECONDS", "1800")),
)
_KIMI_FORECAST_FEATURE_INTERVAL_SECONDS = max(
    300.0,
    float(os.getenv("KIMI_FORECAST_FEATURE_INTERVAL_SECONDS", str(6 * 60 * 60))),
)
_FORECAST_SHADOW_SELF_IMPROVEMENT_CHECK_SECONDS = max(
    60.0,
    float(os.getenv("FORECAST_SHADOW_SELF_IMPROVEMENT_CHECK_SECONDS", "300")),
)
_FORECAST_SHADOW_SELF_IMPROVEMENT_STARTUP_DELAY_SECONDS = max(
    0.0,
    float(os.getenv("FORECAST_SHADOW_SELF_IMPROVEMENT_STARTUP_DELAY_SECONDS", "20")),
)
_FORECAST_SHADOW_SELF_IMPROVEMENT_RUN_HOUR = min(
    23,
    max(0, int(os.getenv("FORECAST_SHADOW_SELF_IMPROVEMENT_RUN_HOUR", "2"))),
)
_FORECAST_SHADOW_SELF_IMPROVEMENT_RUN_MINUTE = min(
    59,
    max(0, int(os.getenv("FORECAST_SHADOW_SELF_IMPROVEMENT_RUN_MINUTE", "15"))),
)
_FORECAST_SHADOW_SELF_IMPROVEMENT_JOB_NAME = "forecast_shadow_self_improvement"


def _normalize_db_column_name(value: object) -> str:
    return "".join(str(value or "").strip().lower().split())


def _quote_identifier(identifier: str) -> str:
    return f"`{str(identifier).replace('`', '``')}`"


def _get_table_columns(conn, table_name: str) -> List[str]:
    cached = _market_table_columns_cache.get(table_name)
    if cached is not None:
        return cached
    rows = conn.execute(text(f"SHOW COLUMNS FROM {_quote_identifier(table_name)}")).fetchall()
    columns = [str(row[0]) for row in rows]
    _market_table_columns_cache[table_name] = columns
    return columns


def _find_table_column(conn, table_name: str, *candidates: str) -> Optional[str]:
    normalized_columns = [
        (_normalize_db_column_name(column), column)
        for column in _get_table_columns(conn, table_name)
    ]
    for candidate in candidates:
        normalized_candidate = _normalize_db_column_name(candidate)
        for normalized_column, original_column in normalized_columns:
            if normalized_candidate and normalized_candidate in normalized_column:
                return original_column
    return None


def _read_table_signature(
    conn,
    table_name: str,
    *,
    date_candidates: tuple[str, ...] = (),
    numeric_candidates: tuple[str, ...] = (),
) -> Dict[str, Any]:
    try:
        select_parts = ["COUNT(*) AS total_rows"]
        date_column = (
            _find_table_column(conn, table_name, *date_candidates)
            if date_candidates
            else None
        )
        numeric_column = (
            _find_table_column(conn, table_name, *numeric_candidates)
            if numeric_candidates
            else None
        )

        if date_column:
            select_parts.append(
                f"MAX({_quote_identifier(date_column)}) AS latest_marker"
            )
        if numeric_column:
            select_parts.append(
                f"COALESCE(SUM(COALESCE({_quote_identifier(numeric_column)}, 0)), 0) AS numeric_total"
            )

        row = conn.execute(
            text(
                f"SELECT {', '.join(select_parts)} FROM {_quote_identifier(table_name)}"
            )
        ).mappings().first()
        if not row:
            return {"rows": 0, "latest": "", "total": 0.0}
        return {
            "rows": int(row.get("total_rows") or 0),
            "latest": str(row.get("latest_marker") or ""),
            "total": round(float(row.get("numeric_total") or 0.0), 4),
        }
    except Exception as exc:
        logger.debug("Signature read skipped for %s: %s", table_name, exc)
        return {"rows": 0, "latest": "", "total": 0.0, "error": "skipped"}


def _read_v_market_data_signature(conn) -> Dict[str, Any]:
    """Fingerprint for v_market_data with mixed biz_date formats (YYYY/MM/DD and YYYY-MM-DD)."""
    try:
        date_column = _find_table_column(conn, "v_market_data", "biz_date", "业务日期")
        price_column = _find_table_column(conn, "v_market_data", "tax_price", "含税单价")
        if not date_column:
            return _read_table_signature(conn, "v_market_data")

        date_expr = (
            f"COALESCE("
            f"STR_TO_DATE(REPLACE({_quote_identifier(date_column)}, '/', '-'), '%Y-%m-%d'), "
            f"STR_TO_DATE({_quote_identifier(date_column)}, '%Y-%m-%d')"
            f")"
        )
        select_parts = [
            "COUNT(*) AS total_rows",
            f"MAX({date_expr}) AS latest_marker",
        ]
        if price_column:
            select_parts.append(
                f"COALESCE(SUM(COALESCE({_quote_identifier(price_column)}, 0)), 0) AS numeric_total"
            )

        row = conn.execute(
            text(f"SELECT {', '.join(select_parts)} FROM v_market_data")
        ).mappings().first()
        if not row:
            return {"rows": 0, "latest": "", "total": 0.0}
        latest_marker = row.get("latest_marker")
        return {
            "rows": int(row.get("total_rows") or 0),
            "latest": str(latest_marker)[:10] if latest_marker else "",
            "total": round(float(row.get("numeric_total") or 0.0), 4),
        }
    except Exception as exc:
        logger.debug("v_market_data signature skipped: %s", exc)
        return {"rows": 0, "latest": "", "total": 0.0, "error": "skipped"}


def _read_erp_sales_signature(conn) -> Dict[str, Any]:
    try:
        row = conn.execute(
            text(
                """
                SELECT
                    COUNT(*) AS total_rows,
                    MAX(DATE(PLANDATE)) AS latest_marker,
                    COALESCE(SUM(COALESCE(FDELIQTY, 0)), 0) AS delivered_total,
                    COALESCE(SUM(COALESCE(produce_qty, 0)), 0) AS produced_total,
                    COALESCE(SUM(COALESCE(plan_qty, 0)), 0) AS planned_total
                FROM erp_v_sale_orderentry
                """
            )
        ).mappings().first()
        if not row:
            return {
                "rows": 0,
                "latest": "",
                "delivered": 0.0,
                "produced": 0.0,
                "planned": 0.0,
            }
        return {
            "rows": int(row.get("total_rows") or 0),
            "latest": str(row.get("latest_marker") or ""),
            "delivered": round(float(row.get("delivered_total") or 0.0), 4),
            "produced": round(float(row.get("produced_total") or 0.0), 4),
            "planned": round(float(row.get("planned_total") or 0.0), 4),
        }
    except Exception as exc:
        logger.debug("ERP sales signature skipped: %s", exc)
        return {
            "rows": 0,
            "latest": "",
            "delivered": 0.0,
            "produced": 0.0,
            "planned": 0.0,
            "error": "skipped",
        }


def _read_market_source_fingerprint(conn) -> Dict[str, Any]:
    from backend.src.db.load_market_prices import _LZ_INTERNAL_VIEW_SPECS

    lz_views: Dict[str, Dict[str, Any]] = {}
    for table_name, _ in _LZ_INTERNAL_VIEW_SPECS:
        lz_views[table_name] = _read_table_signature(
            conn,
            table_name,
            date_candidates=("biz_date", "业务日期"),
            numeric_candidates=("tax_price", "含税单价"),
        )

    # Primary source for blowing-agent and other unified Longzhong rows.
    # biz_date mixes slash and hyphen formats; plain MAX() picks the wrong "latest".
    v_market_data_sig = _read_v_market_data_signature(conn)

    return {
        "v_market_data": v_market_data_sig,
        "market_prices": _read_table_signature(
            conn,
            "market_prices",
            date_candidates=("date",),
            numeric_candidates=("price",),
        ),
        "erp_sales": _read_erp_sales_signature(conn),
        "erp_inventory": _read_table_signature(
            conn,
            "erp_v_stk_inventory",
            numeric_candidates=("FBASEQTY",),
        ),
        "contracts": {
            "contract_head": _read_table_signature(
                conn,
                "erp_t_crm_contract",
                date_candidates=("FDATE",),
            ),
            "contract_entry": _read_table_signature(
                conn,
                "erp_t_crm_contractentry",
                date_candidates=("FDELIVERYDATE",),
                numeric_candidates=("FQTY",),
            ),
            "contract_amount": _read_table_signature(
                conn,
                "erp_t_crm_contractentry_f",
                numeric_candidates=("FALLAMOUNT_LC",),
            ),
        },
        "weekly_plan": _read_table_signature(
            conn,
            "erp_paez_t_monweek_plan",
            date_candidates=("FDATE",),
        ),
        "lz_views": lz_views,
    }


def _clear_db_dependent_runtime_caches() -> None:
    from backend.src.analytics import seasonal as _seasonal
    from backend.src.service.erp_kpi_service import ErpKPIService
    from backend.src.service.weekly_plan_service import weekly_plan_service
    from backend.src.tools.erp_tools import invalidate_erp_tools_cache

    _clear_market_runtime_caches()
    ErpKPIService.invalidate_latest_date_cache()
    invalidate_erp_tools_cache()
    weekly_plan_service.invalidate_cache()
    _ai_analysis_cache.clear()
    _seasonal._SEASONAL_CACHE.clear()


def _set_market_runtime_fingerprint(fingerprint: Dict[str, Any]) -> None:
    global _market_runtime_fingerprint, _market_runtime_last_checked_ts
    _market_runtime_fingerprint = copy.deepcopy(fingerprint)
    _market_runtime_last_checked_ts = time.time()


def _ensure_market_runtime_fresh(force: bool = False) -> None:
    global _market_runtime_fingerprint, _market_runtime_last_checked_ts
    now = time.time()
    if (
        not force
        and _market_runtime_fingerprint is not None
        and (now - _market_runtime_last_checked_ts) < _MARKET_AUTO_REFRESH_CHECK_SECONDS
    ):
        return

    with _market_runtime_state_lock:
        now = time.time()
        if (
            not force
            and _market_runtime_fingerprint is not None
            and (now - _market_runtime_last_checked_ts) < _MARKET_AUTO_REFRESH_CHECK_SECONDS
        ):
            return

        current = _read_market_live_fingerprint()
        previous = _market_runtime_fingerprint
        if previous is not None and current.get("market") != previous.get("market"):
            logger.info("Detected DB/source change; refreshing market runtime state")
            _reload_market_runtime_after_db_change()
        _set_market_runtime_fingerprint(current)


def _read_market_live_fingerprint() -> Dict[str, Any]:
    """Capture DB fingerprints for live updates and automatic cache invalidation."""
    out: Dict[str, Any] = {
        "decision": {"total": 0, "latest_ts": ""},
        "market": {},
    }

    with db.engine.connect() as conn:
        try:
            decision_row = conn.execute(
                text(
                    """
                    SELECT
                        COUNT(*) AS total,
                        MAX(COALESCE(outcome_at, executed_at, created_at)) AS latest_ts
                    FROM decision_log
                    """
                )
            ).mappings().first()
            if decision_row:
                out["decision"] = {
                    "total": int(decision_row.get("total") or 0),
                    "latest_ts": str(decision_row.get("latest_ts") or ""),
                }
        except Exception as exc:
            logger.debug("Decision-log fingerprint skipped: %s", exc)

        out["market"] = _read_market_source_fingerprint(conn)

    return out


def _reload_market_runtime_after_db_change() -> None:
    # Re-import MySQL source views into market_prices; refresh() alone only
    # re-reads the existing market_prices cache and misses new v_market_data rows.
    try:
        from backend.src.db.load_market_prices import load_all

        loaded_rows = load_all(reset=True)
        logger.info("Market prices reloaded after source change: %s rows", f"{loaded_rows:,}")
    except Exception as exc:
        logger.warning("Market prices reload after source change failed: %s", exc)

    market_data.refresh()
    _clear_db_dependent_runtime_caches()

    # Pre-compute a fresh snapshot immediately so the first Dashboard
    # request after a DB change gets a cache hit (no 5-min TTL wait).
    try:
        snap = _compute_market_snapshot()
        with _market_runtime_state_lock:
            _market_snapshot_cache["data"] = copy.deepcopy(snap)
            _market_snapshot_cache["ts"] = time.time()
        logger.info("Snapshot cache pre-computed after DB change: %d products", len(snap))
    except Exception as _snap_exc:
        logger.debug("Snapshot pre-compute after DB change skipped: %s", _snap_exc)

    # Auto-evaluate pending forecasts now that new actual prices may be available
    try:
        from backend.src.db.daily_calibration import evaluate_pending_forecasts
        from backend.src.db.sqlite_db import db as _eval_db
        evaluated = evaluate_pending_forecasts(_eval_db.engine)
        if evaluated:
            logger.info("Auto-evaluated %d pending forecasts against new actuals", len(evaluated))
    except Exception as _eval_exc:
        logger.debug("Auto-evaluate forecasts skipped: %s", _eval_exc)


async def _market_live_watch_loop() -> None:
    """Watch DB fingerprints and emit live update events on change."""
    loop = asyncio.get_event_loop()
    previous = await loop.run_in_executor(None, _read_market_live_fingerprint)
    _set_market_runtime_fingerprint(previous)

    while True:
        await asyncio.sleep(_MARKET_LIVE_WATCH_INTERVAL_SECONDS)
        current = await loop.run_in_executor(None, _read_market_live_fingerprint)

        if current.get("decision") != previous.get("decision"):
            await _market_live_hub.publish(
                "decision_log_changed",
                {"fingerprint": current.get("decision")},
            )

        if current.get("market") != previous.get("market"):
            try:
                await loop.run_in_executor(None, _reload_market_runtime_after_db_change)
            except Exception as exc:
                logger.warning("Market runtime reload after DB change failed: %s", exc)
            await _market_live_hub.publish(
                "market_prices_changed",
                {"fingerprint": current.get("market")},
            )

        previous = current
        _set_market_runtime_fingerprint(current)


@asynccontextmanager
async def _app_lifespan(_: FastAPI):
    if _background_workers_enabled():
        alert_worker.start()
    else:
        logger.info("Background workers disabled for this environment")

    if _should_start_news_ingestion_agent():
        _intel_news_agent.start()
        agent_status = _intel_news_agent.get_status()
        print(
            "Intelligence: News ingestion agent started "
            f"({agent_status.get('scan_interval_seconds')}s cycle)"
        )
    else:
        logger.info(
            "Intelligence news ingestion agent not started: initialized=%s llm_mode=%s background_workers=%s database_only=%s enabled_flag=%s",
            bool(_intel_news_agent),
            LLM_MODE,
            _background_workers_enabled(),
            _database_only_mode(),
            _env_flag("ENABLE_NEWS_INGESTION_AGENT", "false"),
        )
    global _market_startup_refresh_task
    _market_startup_refresh_task = asyncio.create_task(_refresh_market_data_on_startup_async())
    asyncio.create_task(asyncio.to_thread(_sync_platform_product_knowledge_index))

    # Pre-compute snapshot cache in background after market data loads
    asyncio.create_task(_prime_snapshot_cache_after_startup())

    # Initialise forecast accuracy tables (non-blocking, best-effort)
    try:
        from backend.src.db.daily_calibration import init_tables
        from backend.src.db.sqlite_db import db as _cdb
        init_tables(_cdb.engine)
        logger.info("Forecast accuracy tables initialised.")
    except Exception as _e:
        logger.warning(f"Could not init forecast accuracy tables: {_e}")

    # Initialise canonical forecast decision snapshot tables (non-blocking, best-effort)
    try:
        from backend.src.db.forecast_snapshots import init_tables as init_forecast_snapshot_tables
        from backend.src.db.sqlite_db import db as _fsdb
        init_forecast_snapshot_tables(_fsdb.engine)
        logger.info("Forecast decision snapshot tables initialised.")
    except Exception as _e:
        logger.warning(f"Could not init forecast decision snapshot tables: {_e}")

    # Start pricing precompute immediately in background
    global _pricing_bg_task, _report_scheduler_task, _oilchem_scheduler_task, _market_live_watch_task, _morning_briefing_task, _insight_precompute_task, _kimi_forecast_feature_task, _forecast_shadow_self_improvement_task
    if _background_workers_enabled() and _pricing_background_enabled():
        _pricing_bg_task = asyncio.create_task(_pricing_background_loop())
    else:
        _pricing_bg_task = None
        logger.info("Pricing background cache warm-up disabled for this environment")
    if _background_workers_enabled():
        _report_scheduler_task = asyncio.create_task(_weekly_report_scheduler_loop())
        _oilchem_scheduler_task = asyncio.create_task(_oilchem_scheduler_loop())
        _morning_briefing_task = asyncio.create_task(_morning_briefing_scheduler_loop())
        if _insight_precompute_enabled():
            _insight_precompute_task = asyncio.create_task(_insight_precompute_loop())
        else:
            _insight_precompute_task = None
        if _kimi_forecast_features_enabled():
            _kimi_forecast_feature_task = asyncio.create_task(_kimi_forecast_feature_loop())
        else:
            _kimi_forecast_feature_task = None
        if _forecast_shadow_self_improvement_enabled():
            _forecast_shadow_self_improvement_task = asyncio.create_task(_forecast_shadow_self_improvement_loop())
        else:
            _forecast_shadow_self_improvement_task = None
    else:
        _report_scheduler_task = None
        _oilchem_scheduler_task = None
        _morning_briefing_task = None
        _insight_precompute_task = None
        _kimi_forecast_feature_task = None
        _forecast_shadow_self_improvement_task = None

    # Singleton guard: only one worker runs the watch loop (and other
    # shared background tasks) to avoid duplicate fingerprint checks
    # and redundant cache-reload cascades.
    if _is_singleton_worker:
        _market_live_watch_task = asyncio.create_task(_market_live_watch_loop())
        logger.info("This worker is the singleton — running market live watch loop.")
    else:
        _market_live_watch_task = None
        logger.info("Another worker is the singleton — skipping duplicate background tasks.")

    # ── Background Intelligence Skills ────────────────────────────────
    _bg_skill_scheduler = None
    if _background_workers_enabled():
        try:
            from backend.src.skills.background.registry import register_all_background_skills
            from backend.src.skills.background import background_skill_scheduler as _bg_sched

            register_all_background_skills()
            _bg_sched.start()
            _bg_skill_scheduler = _bg_sched
            logger.info(
                "Background intelligence skills started: %d skills",
                len(_bg_sched.skills),
            )
        except Exception as _e:
            logger.warning("Could not start background intelligence skills: %s", _e)
    else:
        logger.info("Background intelligence skills disabled for this environment")

    try:
        yield
    finally:
        if _background_workers_enabled():
            alert_worker.stop()
        if _intel_news_agent:
            _intel_news_agent.stop()
        if _pricing_bg_task:
            _pricing_bg_task.cancel()
        if _report_scheduler_task:
            _report_scheduler_task.cancel()
        if _oilchem_scheduler_task:
            _oilchem_scheduler_task.cancel()
        if _morning_briefing_task:
            _morning_briefing_task.cancel()
        if _insight_precompute_task:
            _insight_precompute_task.cancel()
        if _kimi_forecast_feature_task:
            _kimi_forecast_feature_task.cancel()
        if _forecast_shadow_self_improvement_task:
            _forecast_shadow_self_improvement_task.cancel()
        if _market_startup_refresh_task:
            _market_startup_refresh_task.cancel()
        if _market_live_watch_task:
            _market_live_watch_task.cancel()
        if _is_singleton_worker and _singleton_lock_fd >= 0:
            try:
                import fcntl as _fcntl_cleanup
                _fcntl_cleanup.flock(_singleton_lock_fd, _fcntl_cleanup.LOCK_UN)
                os.close(_singleton_lock_fd)
            except Exception:
                pass
        if _bg_skill_scheduler:
            _bg_skill_scheduler.stop()


app = FastAPI(
    title="Enterprise Decision Intelligence Agent API",
    lifespan=_app_lifespan,
)
app.mount("/report-assets", StaticFiles(directory=str(_REPORT_IMAGE_UPLOAD_DIR)), name="report-assets")

# Weekly report chart static files
_WEEKLY_REPORTS_DIR = _BOOT_REPO_ROOT / "exports" / "weekly_reports"
_WEEKLY_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
app.mount(
    "/api/reports-static",
    StaticFiles(directory=str(_WEEKLY_REPORTS_DIR)),
    name="weekly-reports-static",
)

_CHAT_CHARTS_DIR = _BOOT_REPO_ROOT / "exports" / "chat_charts"
_CHAT_CHARTS_DIR.mkdir(parents=True, exist_ok=True)
app.mount(
    "/api/chat-charts",
    StaticFiles(directory=str(_CHAT_CHARTS_DIR)),
    name="chat-charts-static",
)


_ALLOWED_CORS_ORIGINS = [
    "http://localhost:5001",
    "http://127.0.0.1:5001",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
_ALLOWED_CORS_ORIGIN_REGEX = re.compile(
    r"^https?://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[0-1])\.\d+\.\d+)(:\d+)?$"
)


def _apply_request_cors_headers(request: Request, response: JSONResponse) -> JSONResponse:
    origin = request.headers.get("origin")
    if not origin:
        return response

    if origin in _ALLOWED_CORS_ORIGINS or _ALLOWED_CORS_ORIGIN_REGEX.match(origin):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Vary"] = "Origin"
    return response


# Allow CORS for frontend development
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_CORS_ORIGINS,
    allow_origin_regex=_ALLOWED_CORS_ORIGIN_REGEX.pattern,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Service
# The runtime requires a real LLM backend.

use_real_llm = os.getenv("USE_REAL_LLM", "false").lower() == "true"

if use_real_llm:
    print("Attempting to use Real LLM Service (USE_REAL_LLM=true)")
    real_llm = RealLLMService()
    if not getattr(real_llm, "is_ready", False):
        raise RuntimeError(
            "FATAL: USE_REAL_LLM=true but the configured LLM backend is not ready. "
            "Check LLM_BASE_URL / LLM_API_KEY in .env."
        )
    llm_service = real_llm
    LLM_MODE = "real"
    print(f"Real LLM active: model={real_llm.model}")
else:
    raise RuntimeError(
        "FATAL: Real LLM mode is required for this project. Set USE_REAL_LLM=true and configure the real LLM backend."
    )

decision_service = DecisionService(llm_service)
kimi_forecast_feature_service.llm = llm_service

from backend.src.hermes.stream_preview import (
    build_fast_start_preview as _hermes_build_fast_start_preview,
    dedupe_fast_preview_markdown,
)
from backend.src.routing.chat_route_plan import (
    build_chat_route_plan,
    build_route_plan_metadata,
)


# Initialize intelligence pipeline (next_version — 6-layer intelligence enhancement)
try:
    from backend.src.intelligence.intelligence_orchestrator import (
        init_intelligence,
        get_news_agent,
        get_override_engine,
        get_learning_loop,
        get_intelligence_context,
    )

    init_intelligence(llm_service)
    _intel_news_agent = get_news_agent()
    _intel_override_engine = get_override_engine()
    _intel_learning_loop = get_learning_loop()
    print("Intelligence pipeline initialized successfully")
except Exception as _intel_err:
    print(f"Intelligence pipeline init warning (non-fatal): {_intel_err}")
    _intel_news_agent = None
    _intel_override_engine = None
    _intel_learning_loop = None


class ChatAttachmentContext(BaseModel):
    id: str
    filename: str
    file_type: str
    size_bytes: int
    content: str
    excerpt: str
    summary: str


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    prefs: Optional[Dict[str, object]] = None
    attachment_contexts: Optional[List[ChatAttachmentContext]] = None
    chat_mode: Optional[str] = None


class HermesPlanRequest(BaseModel):
    message: str


class LoginRequest(BaseModel):
    username: str
    password: str


class CreateUserRequest(BaseModel):
    username: str
    password: str
    is_admin: bool = False


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class ResetUserPasswordRequest(BaseModel):
    new_password: str


class UpdateUserStatusRequest(BaseModel):
    is_active: bool


class AccessModelConfigPayload(BaseModel):
    organizations: List[Dict[str, Any]]
    departments: List[Dict[str, Any]]
    roles: List[Dict[str, Any]]
    accessUsers: List[Dict[str, Any]]


class ResearchBriefPayload(BaseModel):
    decision: str
    target_customer: str
    geography: str
    category_or_substitute_set: str
    time_horizon: str
    must_answer_questions: List[str] = []
    evidence_bar: str = "medium"


class MultiEngineSearchPayload(BaseModel):
    query: str
    engines: List[str] = []
    max_results_per_engine: int = 4
    include_news: bool = True
    timelimit: str = "w"


class RecommendationCardTag(BaseModel):
    label: str
    tone: str


class RecommendationCardResponse(BaseModel):
    id: str
    icon_key: str
    badge: str
    prompt: str
    color: str
    reason: str
    score: int
    tags: List[RecommendationCardTag]


class ComposerSuggestionPayload(BaseModel):
    id: str
    title: str
    prompt: str
    reason: str
    source: str
    icon_key: str
    color: str
    score: int
    tags: List[RecommendationCardTag]
    session_id: Optional[str] = None


class ComposerSuggestionsResponse(BaseModel):
    suggestions: List[ComposerSuggestionPayload]
    role: str
    role_label: str
    counts: Dict[str, int]


class WeeklyPlanProductResponse(BaseModel):
    product_id: str
    plan_date: str
    source: str
    material_names: List[str]
    planned_production_qty: float
    planned_sales_qty: float
    planned_inventory_qty: float
    actual_delivery_qty: float
    planned_sales_amount: float
    average_tax_price: Optional[float] = None
    net_supply_gap_qty: float
    inventory_coverage_days: Optional[float] = None
    fulfillment_ratio: Optional[float] = None
    pressure_signal: str
    note: str


class WeeklyPlanResponse(BaseModel):
    as_of: Optional[str] = None
    items: List[WeeklyPlanProductResponse]


class RenameSessionRequest(BaseModel):
    title: str


class ChatResponse(BaseModel):
    markdown: str
    status: str
    context: dict
    task_type: str
    session_id: str
    error_message: Optional[str] = None
    llm_route: Optional[str] = None
    quality_flags: List[dict] = Field(default_factory=list)
    alerts_prepended: List[str] = Field(default_factory=list)
    enhancement_applied: Dict[str, str] = Field(default_factory=dict)
    resolved_chat_mode: Optional[str] = None
    response_mode: Optional[str] = None
    mode_reason: Optional[str] = None
    confidence: Optional[Dict[str, Any]] = None
    suggestions: List[Dict[str, str]] = Field(default_factory=list)
    citations: List[Dict[str, str]] = Field(default_factory=list)


class SessionReportPreviewResponse(BaseModel):
    title: str
    markdown: str
    session_id: str
    generated_at: str


class GeneratedReportSummaryResponse(BaseModel):
    id: str
    session_id: str
    report_type: str
    title: str
    summary: str
    status: str = "draft"
    approved_at: Optional[str] = None
    approved_by: Optional[str] = None
    is_unread: bool = False
    read_at: Optional[str] = None
    created_at: str


class GeneratedReportResponse(GeneratedReportSummaryResponse):
    markdown: str
    blocks: List[Dict[str, Any]] = Field(default_factory=list)
    context: Dict[str, Any] = Field(default_factory=dict)
    last_refreshed_at: Optional[str] = None


class UpdateGeneratedReportRequest(BaseModel):
    title: str
    markdown: str
    blocks: Optional[List[Dict[str, Any]]] = None


class RefreshReportBlocksResponse(BaseModel):
    report: GeneratedReportResponse
    refreshed_block_count: int = 0
    refreshed_at: str


class UpdateGeneratedReportStatusRequest(BaseModel):
    status: str


class ReportRecipientResponse(BaseModel):
    id: str
    username: str
    role_label: str


class DeliverReportRequest(BaseModel):
    delivery_mode: str
    recipient_ids: List[str]
    message: Optional[str] = None


class ReportDeliveryResponse(BaseModel):
    id: str
    report_id: str
    sender_user_id: str
    delivery_mode: str
    recipient_ids: List[str]
    recipient_labels: List[str]
    message: Optional[str] = None
    status: str
    provider: Optional[str] = None
    delivered_at: str


class ReportTemplateResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    time_period: str
    audience: Optional[str] = None
    schedule_hint: Optional[str] = None
    sections: List[str]
    template_title: Optional[str] = None
    template_markdown: Optional[str] = None
    template_blocks: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: str
    updated_at: str
    updated_by: Optional[str] = None
    auto_schedule_enabled: bool = Field(default=False)
    auto_schedule_day: Optional[str] = None  # e.g., "Monday"
    auto_schedule_time: Optional[str] = None  # e.g., "08:00"


class SaveReportTemplateRequest(BaseModel):
    id: Optional[str] = None
    name: str
    description: Optional[str] = None
    time_period: str
    audience: Optional[str] = None
    schedule_hint: Optional[str] = None
    sections: List[str]
    template_title: Optional[str] = None
    template_markdown: Optional[str] = None
    template_blocks: Optional[List[Dict[str, Any]]] = None


class ReportExportResponse(BaseModel):
    filename: str
    content_type: str


class ReportScheduleRequest(BaseModel):
    enabled: bool
    hour: int = 8
    minute: int = 0


class ReportScheduleResponse(BaseModel):
    id: str
    report_type: str
    report_label: str
    enabled: bool
    weekday: int
    hour: int
    minute: int
    timezone: str
    last_run_at: Optional[str] = None
    next_run_at: str


class ReportCategoryResponse(BaseModel):
    id: str
    org_id: str
    parent_id: Optional[str] = None
    name: str
    icon: Optional[str] = None
    position: int = 0
    permissions: Dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str
    created_by: Optional[str] = None


class SaveReportCategoryRequest(BaseModel):
    id: Optional[str] = None
    parent_id: Optional[str] = None
    name: str
    icon: Optional[str] = None
    position: int = 0
    permissions: Dict[str, Any] = Field(default_factory=dict)


class CockpitDefinitionResponse(BaseModel):
    id: str
    name: str
    org_id: str
    department_id: Optional[str] = None
    layout: Dict[str, Any] = Field(default_factory=dict)
    status: str
    version: int
    is_default: bool = False
    permissions: Dict[str, Any] = Field(default_factory=dict)
    created_by: Optional[str] = None
    created_at: str
    updated_at: str
    published_at: Optional[str] = None


class SaveCockpitDefinitionRequest(BaseModel):
    id: Optional[str] = None
    name: str
    department_id: Optional[str] = None
    layout: Dict[str, Any] = Field(default_factory=dict)
    status: str = "draft"
    version: int = 1
    is_default: bool = False
    permissions: Dict[str, Any] = Field(default_factory=dict)


class AlertRuleResponse(BaseModel):
    id: str
    name: str
    org_id: str
    created_by: Optional[str] = None
    metric_expression: str
    condition_type: str
    condition_params: Dict[str, Any] = Field(default_factory=dict)
    severity: str
    category: str
    notification_channels: List[Dict[str, Any]] = Field(default_factory=list)
    default_owner_id: Optional[str] = None
    enabled: bool = True
    evaluation_interval_min: int = 15
    created_at: str
    updated_at: str


class SaveAlertRuleRequest(BaseModel):
    id: Optional[str] = None
    name: str
    metric_expression: str
    condition_type: str
    condition_params: Dict[str, Any] = Field(default_factory=dict)
    severity: str
    category: str
    notification_channels: List[Dict[str, Any]] = Field(default_factory=list)
    default_owner_id: Optional[str] = None
    enabled: bool = True
    evaluation_interval_min: int = 15


# =============================================================================
# ERP Write-Back Models
# =============================================================================


class ERPPricingRecommendationRequest(BaseModel):
    product_id: str
    recommended_price: float
    valid_from: str  # ISO date string YYYY-MM-DD
    valid_until: Optional[str] = None
    rationale: Optional[str] = None
    session_id: Optional[str] = None


class ERPPricingRecommendationResponse(BaseModel):
    recommendation_id: str
    status: str  # pending | approved | rejected | committed
    created_at: str


class ERPPricingListResponse(BaseModel):
    recommendations: list[dict]



    unit: str


class DataSourceResponse(BaseModel):
    id: str
    org_id: str
    name: str
    connector_type: str
    connection_config: Dict[str, Any] = Field(default_factory=dict)
    status: str
    last_tested_at: Optional[str] = None
    created_by: Optional[str] = None
    created_at: str
    updated_at: str


class SaveDataSourceRequest(BaseModel):
    id: Optional[str] = None
    name: str
    connector_type: str
    connection_config: Dict[str, Any] = Field(default_factory=dict)
    status: str = "active"
    last_tested_at: Optional[str] = None


class DatasetResponse(BaseModel):
    id: str
    source_id: str
    org_id: str
    friendly_name: str
    physical_reference: str
    description: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    schema_snapshot: Dict[str, Any] = Field(default_factory=dict)
    status: str
    created_at: str
    updated_at: str


class SaveDatasetRequest(BaseModel):
    id: Optional[str] = None
    source_id: str
    friendly_name: str
    physical_reference: str
    description: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    schema_snapshot: Dict[str, Any] = Field(default_factory=dict)
    status: str = "active"


class DatasetColumnMappingResponse(BaseModel):
    dataset_id: str
    physical_column: str
    ontology_concept: str
    transform: Optional[str] = None


class DatasetColumnMappingItem(BaseModel):
    physical_column: str
    ontology_concept: str
    transform: Optional[str] = None


class SaveDatasetColumnMappingsRequest(BaseModel):
    mappings: List[DatasetColumnMappingItem] = Field(default_factory=list)


class DatasetPermissionResponse(BaseModel):
    dataset_id: str
    role_id: str
    can_query: bool = False
    can_export: bool = False
    can_chat: bool = False


class DatasetPermissionItem(BaseModel):
    role_id: str
    can_query: bool = False
    can_export: bool = False
    can_chat: bool = False


class SaveDatasetPermissionsRequest(BaseModel):
    permissions: List[DatasetPermissionItem] = Field(default_factory=list)


class PlatformProductResponse(BaseModel):
    id: str
    org_id: str
    name_en: str
    name_cn: str
    product_type: str
    unit: str
    description: Optional[str] = None
    aliases: List[str] = Field(default_factory=list)
    data_table: Optional[str] = None
    data_key: Optional[str] = None
    display_in: List[str] = Field(default_factory=list)
    created_by: Optional[str] = None
    created_at: str
    updated_at: str


class SavePlatformProductRequest(BaseModel):
    id: Optional[str] = None
    name_en: Optional[str] = None
    name_cn: Optional[str] = None
    product_type: str
    unit: str = "CNY/ton"
    description: Optional[str] = None
    aliases: List[str] = Field(default_factory=list)
    data_table: Optional[str] = None
    data_key: Optional[str] = None
    display_in: List[str] = Field(default_factory=list)


class ProductDataMappingPreviewRequest(BaseModel):
    data_table: Optional[str] = None
    data_key: Optional[str] = None


class ProductDataMappingPreviewResponse(BaseModel):
    data_table: str
    data_key: str
    available: bool
    row_count: int = 0
    latest_date: Optional[str] = None
    latest_price: Optional[float] = None
    price_type: Optional[str] = None
    message: str
    mini_chart_data: List[Dict[str, Any]] = Field(default_factory=list)


class PlatformProductImportRowResponse(BaseModel):
    row_number: int
    product_id: str
    name_en: str
    name_cn: str
    product_type: str
    unit: str
    description: str
    aliases: List[str] = Field(default_factory=list)
    data_source: str = "market_prices"
    data_table: Optional[str] = None
    data_key: Optional[str] = None
    display_in: List[str] = Field(default_factory=list)
    valid: bool
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    will_update: bool = False
    data_available: Optional[bool] = None
    data_row_count: Optional[int] = None


class PlatformProductImportPreviewResponse(BaseModel):
    total_rows: int
    valid_rows: int
    invalid_rows: int
    rows: List[PlatformProductImportRowResponse] = Field(default_factory=list)


class PlatformProductImportCommitResponse(PlatformProductImportPreviewResponse):
    imported_rows: int = 0
    imported_products: List[PlatformProductResponse] = Field(default_factory=list)


SCHEDULED_REPORT_LABELS: Dict[str, str] = {
    "weekly_market": "Weekly Market Report",
    "weekly_executive": "Weekly Executive Brief",
    "weekly_competitor": "Weekly Competitor Watch",
}
TEMPLATE_SCHEDULE_PREFIX = "template:"


def _template_schedule_type(template_id: str) -> str:
    return f"{TEMPLATE_SCHEDULE_PREFIX}{str(template_id).strip()}"


def _extract_template_id_from_schedule_type(report_type: str) -> Optional[str]:
    raw = str(report_type or "").strip()
    if not raw.startswith(TEMPLATE_SCHEDULE_PREFIX):
        return None
    template_id = raw[len(TEMPLATE_SCHEDULE_PREFIX) :].strip()
    return template_id or None

REPORT_EXPORT_CONTENT_TYPES: Dict[str, str] = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def _build_product_alias_map() -> Dict[str, str]:
    # Use lazy import so we always read the current domain_graph singleton,
    # even after refresh_domain_graph() has rebound it in knowledge_graph.py.
    from backend.src.domain.knowledge_graph import domain_graph as _live_dg
    aliases: Dict[str, str] = {}
    for node in _live_dg.nodes:
        values = {
            node.id,
            getattr(node, "name_en", ""),
            getattr(node, "name_cn", ""),
            getattr(node, "description", ""),
        }
        for value in values:
            text_value = str(value or "").strip().lower()
            if text_value:
                aliases[text_value] = node.id
    aliases.update(
        {
            "mixed c5": "cracked_c5",
            "c5": "cracked_c5",
            "mixed c9": "cracked_c9",
            "c9": "cracked_c9",
            "sis rubber": "sis",
            "c5 petroleum resin": "c5_resin",
        }
    )
    return aliases


_PRODUCT_ALIAS_MAP: Dict[str, str] = _build_product_alias_map()


def _rebuild_product_alias_map() -> None:
    """Repopulate _PRODUCT_ALIAS_MAP after platform products change."""
    global _PRODUCT_ALIAS_MAP
    _PRODUCT_ALIAS_MAP = _build_product_alias_map()


def _resolve_product_id_for_visualization(
    message: str, context: Dict[str, Any]
) -> Optional[str]:
    ids = _resolve_product_ids_for_visualization(message, context)
    return ids[0] if ids else None


def _resolve_product_ids_for_visualization(
    message: str, context: Dict[str, Any]
) -> List[str]:
    """Return up to 3 distinct product IDs found in the message/context."""
    haystack = " ".join([
        str((context or {}).get("product") or "").strip(),
        str(message or "").strip(),
    ]).lower()

    by_product: Dict[str, str] = {}  # product_id → longest alias matched
    for alias, product_id in _PRODUCT_ALIAS_MAP.items():
        if not alias or alias not in haystack:
            continue
        if product_id not in by_product or len(alias) > len(by_product[product_id]):
            by_product[product_id] = alias

    # Sort by alias length descending (most specific first), cap at 3
    ordered = sorted(by_product.keys(), key=lambda pid: len(by_product[pid]), reverse=True)
    return ordered[:3]


def _looks_like_market_visualization_query(message: str) -> bool:
    q = str(message or "").lower()
    markers = [
        "trend",
        "market",
        "price",
        "history",
        "走势",
        "行情",
        "价格",
        "变化",
        "环比",
        "weekly",
    ]
    return any(marker in q for marker in markers)


def _build_market_visualization(
    product_id: str, language: str
) -> Optional[Dict[str, Any]]:
    history = market_data.get_price_history(product_id)
    node = next((item for item in domain_graph.nodes if item.id == product_id), None)
    if not node or len(history) < 2:
        return None

    window = history[-8:]
    latest_price = float(window[-1]["price"])
    prior_price = float(window[0]["price"])
    delta_pct = (
        round(((latest_price - prior_price) / prior_price) * 100, 2)
        if prior_price
        else 0.0
    )

    return {
        "kind": "line",
        "title": (
            f"{getattr(node, 'name_en', product_id)} Price Trend"
            if language == "en"
            else f"{getattr(node, 'name_cn', product_id)}价格走势"
        ),
        "subtitle": "Last 8 observations" if language == "en" else "最近8个观测点",
        "unit": getattr(node, "unit", ""),
        "x_key": "label",
        "series": [
            {
                "key": "price",
                "label": "Price" if language == "en" else "价格",
                "color": "#2563eb",
            }
        ],
        "data": [
            {
                "label": (
                    str(point["date"])[5:]
                    if len(str(point["date"])) >= 5
                    else str(point["date"])
                ),
                "price": round(float(point["price"]), 2),
            }
            for point in window
        ],
        "summary": {
            "latest": round(latest_price, 2),
            "change_pct": delta_pct,
        },
    }


def _build_erp_sales_bar_visualization(
    summary: Dict[str, Any],
    product_id: Optional[str],
    language: str,
) -> Optional[Dict[str, Any]]:
    """Build a bar chart from ERP sales summary rows (volume in tons)."""
    rows = list(summary.get("rows") or [])
    if not rows:
        return None

    data: List[Dict[str, Any]] = []
    for row in rows[:12]:
        label = str(row.get("product") or "").strip()
        volume = float(row.get("volume_ton") or 0)
        if not label or volume <= 0:
            continue
        data.append(
            {
                "label": label,
                "volume": round(volume, 2),
                "avg_price": round(float(row.get("avg_price") or 0), 2),
            }
        )
    if not data:
        return None

    period_start = str(summary.get("period_start") or "").strip()
    period_end = str(summary.get("period_end") or "").strip()
    subtitle = (
        f"{period_start} to {period_end}"
        if language == "en" and period_start and period_end
        else f"{period_start} 至 {period_end}" if period_start and period_end else ""
    )

    node = None
    if product_id:
        node = next((n for n in domain_graph.nodes if n.id == product_id), None)
    title_product = (
        getattr(node, "name_en", product_id)
        if language == "en" and node
        else getattr(node, "name_cn", product_id) if node else None
    )
    if title_product and len(data) == 1:
        title = (
            f"{title_product} ERP Sales Volume"
            if language == "en"
            else f"{title_product} ERP销量"
        )
    else:
        title = "ERP Sales Volume" if language == "en" else "ERP销量（吨）"

    total_volume = sum(float(point.get("volume") or 0) for point in data)
    return {
        "kind": "bar",
        "title": title,
        "subtitle": subtitle,
        "unit": "tons" if language == "en" else "吨",
        "x_key": "label",
        "series": [
            {
                "key": "volume",
                "label": "Sales volume" if language == "en" else "销量",
                "color": "#2563eb",
            }
        ],
        "data": data,
        "summary": {
            "latest": round(total_volume, 2),
            "change_pct": summary.get("period_change"),
        },
    }


def _build_erp_daily_bar_visualization(
    product_id: Optional[str],
    language: str,
    period: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Build an interactive daily ERP sales bar chart using the erp-daily-chart skill."""
    try:
        from backend.src.hermes.skills.erp_daily_chart_skill import build_erp_daily_bar_visualization

        return build_erp_daily_bar_visualization(product_id=product_id, period=period, language=language)
    except Exception:
        return None


def _message_requests_erp_bar_chart(message: str) -> bool:
    msg = str(message or "")
    q = msg.lower()
    chart_markers_zh = ("柱图", "柱状图", "条形图", "柱形图")
    chart_markers_en = ("bar chart", "barchart")
    if not (
        any(marker in msg for marker in chart_markers_zh)
        or any(marker in q for marker in chart_markers_en)
    ):
        return False
    try:
        from backend.src.engine.decision_graph import _looks_like_erp_sales_query

        return _looks_like_erp_sales_query(str(message or ""))
    except Exception:
        return any(
            marker in str(message or "")
            for marker in ("销售", "销量", "明细", "情况", "状况", "erp", "sales")
        )


def _build_competitor_visualization(
    product_id: str, language: str
) -> Optional[Dict[str, Any]]:
    from backend.src.service.competitor_service import COMPETITOR_BENCHMARKS

    competitors = COMPETITOR_BENCHMARKS.get(product_id) or []
    if not competitors:
        return None

    own_price = float(market_data.get_latest_price(product_id) or 0.0)
    if own_price <= 0:
        return None

    def _reference_price(row: Dict[str, Any]) -> float:
        for key in ("key_account", "quoted", "listed", "price"):
            if row.get(key) is not None:
                return float(row[key])
        if row.get("range_low") is not None and row.get("range_high") is not None:
            return (float(row["range_low"]) + float(row["range_high"])) / 2.0
        if row.get("range_low") is not None:
            return float(row["range_low"])
        return 0.0

    def _label_for_row(row_name: Any) -> str:
        label = str(row_name or "Competitor").strip() or "Competitor"
        lower = label.lower()
        if "上海石化" in label or "sinopec" in lower:
            return "SINOPEC Shanghai listed" if language == "en" else "中石化上海挂牌"
        return label

    data = [
        {
            "label": "Ecisco reference" if language == "en" else "Ecisco参考价",
            "price": round(own_price, 2),
            "spread": 0.0,
        }
    ]
    for row in competitors[:4]:
        ref_price = _reference_price(row)
        if ref_price <= 0:
            continue
        data.append(
            {
                "label": _label_for_row(row.get("name")),
                "price": round(ref_price, 2),
                "spread": round(own_price - ref_price, 2),
            }
        )

    if len(data) <= 1:
        return None

    return {
        "kind": "bar",
        "title": "Competitor Benchmark" if language == "en" else "竞品价格对标",
        "subtitle": (
            "Ecisco current market reference vs competitor/listed references"
            if language == "en"
            else "Ecisco当前参考价与竞品/挂牌参考价对比"
        ),
        "unit": "CNY/ton",
        "x_key": "label",
        "series": [
            {
                "key": "price",
                "label": "Reference price" if language == "en" else "参考价格",
                "color": "#f59e0b",
            }
        ],
        "data": data,
        "summary": {
            "latest": round(own_price, 2),
            "change_pct": None,
        },
    }


def _build_multi_line_visualization(
    product_ids: List[str], language: str
) -> Optional[Dict[str, Any]]:
    """Build a multi-series line chart overlaying price trends for 2-3 products."""
    COLORS = ["#2563eb", "#dc2626", "#16a34a", "#f59e0b"]
    series_list: List[Dict[str, Any]] = []
    data_by_label: Dict[str, Dict[str, Any]] = {}

    for idx, product_id in enumerate(product_ids[:3]):
        history = market_data.get_price_history(product_id)
        node = next((n for n in domain_graph.nodes if n.id == product_id), None)
        if not node or len(history) < 2:
            continue
        window = history[-8:]
        label_key = f"price_{product_id}"
        name = getattr(node, "name_en" if language == "en" else "name_cn", product_id)
        series_list.append({
            "key": label_key,
            "label": name,
            "color": COLORS[idx % len(COLORS)],
        })
        for point in window:
            lbl = str(point["date"])[5:] if len(str(point["date"])) >= 5 else str(point["date"])
            if lbl not in data_by_label:
                data_by_label[lbl] = {"label": lbl}
            data_by_label[lbl][label_key] = round(float(point["price"]), 2)

    if len(series_list) < 2:
        return None

    sorted_data = sorted(data_by_label.values(), key=lambda r: str(r.get("label", "")))

    first_node = next((n for n in domain_graph.nodes if n.id == product_ids[0]), None)
    unit = getattr(first_node, "unit", "") if first_node else ""
    names = [s["label"] for s in series_list]

    return {
        "kind": "multi_line",
        "title": (
            " vs ".join(names) + " Price Trend"
            if language == "en"
            else " vs ".join(names) + " 价格走势对比"
        ),
        "subtitle": "Last 8 observations" if language == "en" else "最近8个观测点",
        "unit": unit,
        "x_key": "label",
        "series": series_list,
        "data": sorted_data,
        "summary": {},
    }


def _build_forecast_range_visualization(
    product_id: str, language: str
) -> Optional[Dict[str, Any]]:
    """Build a 7-day bull/base/bear forecast corridor chart."""
    node = next((n for n in domain_graph.nodes if n.id == product_id), None)
    if not node:
        return None
    history = market_data.get_price_history(product_id)
    if len(history) < 2:
        return None

    latest_price = float(history[-1]["price"])
    latest_date_str = str(history[-1]["date"])
    try:
        latest_date = datetime.strptime(latest_date_str[:10], "%Y-%m-%d")
    except ValueError:
        return None

    try:
        fc = forecasting_service.forecast(product_id)
    except Exception:
        return None

    sc7 = fc.get("scenarios", {}).get("7d", {})

    def mid(sc: dict, key: str) -> float:
        b = sc.get(key, {})
        return (b.get("low", latest_price) + b.get("high", latest_price)) / 2

    t7_base = mid(sc7, "base")
    t7_bull = mid(sc7, "bull")
    t7_bear = mid(sc7, "bear")

    # Anchor point (current actual)
    data = [{
        "label": latest_date_str[5:10],
        "base": round(latest_price, 2),
        "bull": round(latest_price, 2),
        "bear": round(latest_price, 2),
    }]
    for i in range(1, 8):
        t = i / 7
        d = (latest_date + timedelta(days=i)).strftime("%m-%d")
        data.append({
            "label": d,
            "base": round(latest_price + (t7_base - latest_price) * t, 2),
            "bull": round(latest_price + (t7_bull - latest_price) * t, 2),
            "bear": round(latest_price + (t7_bear - latest_price) * t, 2),
        })

    name = getattr(node, "name_en" if language == "en" else "name_cn", product_id)
    return {
        "kind": "forecast_range",
        "title": (
            f"7-Day Forecast — {name}"
            if language == "en"
            else f"7日价格预测 — {name}"
        ),
        "subtitle": "Bull / Base / Bear scenarios" if language == "en" else "多空基准三情景",
        "unit": getattr(node, "unit", ""),
        "x_key": "label",
        "series": [
            {"key": "bull", "label": "Bull", "color": "#16a34a"},
            {"key": "base", "label": "Base", "color": "#2563eb"},
            {"key": "bear", "label": "Bear", "color": "#dc2626"},
        ],
        "data": data,
        "summary": {
            "latest": round(latest_price, 2),
            "target_base": round(t7_base, 2),
            "target_bull": round(t7_bull, 2),
            "target_bear": round(t7_bear, 2),
        },
    }


def _classify_chart_intent(
    query: str,
    task_type: str,
    resolved_chat_mode: str,
    product_ids: List[str],
) -> List[str]:
    """Return list of chart directives. Rules-first, optional LLM fallback."""
    q = str(query or "").lower()
    lower_tt = str(task_type or "").lower()
    directives: List[str] = []

    # Detect daily/weekly bar chart intent (time-series sales by day)
    if any(kw in q for kw in ("按日", "日销售", "日销量", "每日销量", "周度", "daily sales", "daily bar", "weekly chart", "by day")) and any(kw in q for kw in ("柱图", "柱状图", "条形图", "bar chart", "barchart", "chart", "graph")):
        directives.append("daily_bar")

    if any(
        kw in q
        for kw in (
            "柱图",
            "柱状图",
            "条形图",
            "柱形图",
            "bar chart",
            "barchart",
        )
    ):
        directives.append("bar")

    if any(kw in q for kw in ("breakdown", "share", "composition", "proportion", "segment",
                               "占比", "结构", "品种", "成分", "构成")):
        directives.append("pie")

    if any(kw in q for kw in ("supply", "demand", "inventory", "capacity", "utilization",
                               "库存", "产能", "开工", "供需", "去库", "补库", "产量", "消费")):
        directives.append("composed")

    if any(kw in q for kw in ("correlation", "relationship", "co-movement", "联动", "相关")):
        if len(product_ids) >= 2:
            directives.append("scatter")

    from backend.src.service.competitor_service import looks_like_competitor_query
    if looks_like_competitor_query(query) and product_ids:
        directives.append("bar_competitor")
    elif any(kw in q for kw in ("compare", "vs", "versus", "对比", "比较")) and len(product_ids) >= 2:
        directives.append("multi_line")

    if any(kw in q for kw in ("forecast", "predict", "outlook", "预测")) or "forecast" in lower_tt:
        directives.append("forecast_range")

    # Detect dynamic multi-metric comparison (Revenue + Volume + Orders)
    erp_metric_keywords = (
        "revenue", "销售额", "销量", "订单", "volume", "orders",
        "销售金额", "销售收入", "营收", "profit", "利润",
    )
    comparison_keywords = ("compare", "comparison", "vs", "versus", "对比", "综合", "一起", "同时", "综合对比")
    if any(kw in q for kw in erp_metric_keywords) and any(kw in q for kw in comparison_keywords):
        directives.append("dynamic_comparison")

    if any(kw in q for kw in ("trend", "price", "history", "走势", "价格", "行情", "变化")) or \
       any(t in lower_tt for t in ("market", "pricing")):
        if not any(d in directives for d in ("multi_line", "forecast_range", "bar_competitor", "dynamic_comparison")):
            directives.append("line")

    if any(kw in q for kw in ("report", "briefing", "summary", "table", "报告", "汇报", "总结")) or \
       any(t in lower_tt for t in ("boss_briefing", "erp")):
        directives.append("table_from_markdown")

    if directives:
        return directives

    # LLM fallback — only for analysis/report mode with known products
    if resolved_chat_mode in ("analysis", "report") and product_ids:
        try:
            import json as _json
            llm = getattr(decision_service, "llm", None)
            if llm and hasattr(llm, "generate_completion"):
                prod_list = ", ".join(product_ids[:3])
                sys_msg = "You are a chart type selector. Respond with JSON only."
                usr_msg = (
                    f"User query: {query}\nProducts mentioned: {prod_list}\n"
                    "Which 1-2 chart types best visualize the answer? "
                    'Return ONLY JSON: {"charts": ["line"|"bar"|"pie"|"scatter"|"composed"|"forecast_range"|"multi_line"]}\n'
                    'If no chart needed return {"charts": []}.'
                )
                resp = llm.generate_completion(sys_msg, usr_msg, max_tokens=80, temperature=0.0)
                m = _re.search(r'\{.*?\}', str(resp or ""), _re.DOTALL)
                if not m:
                    return []
                payload = _json.loads(m.group(0))
                charts = payload.get("charts", []) if isinstance(payload, dict) else []
                allowed = {
                    "line",
                    "bar",
                    "pie",
                    "scatter",
                    "composed",
                    "forecast_range",
                    "multi_line",
                }
                return [str(chart) for chart in charts if str(chart) in allowed][:2]
        except Exception:
            return []

    return []


def _build_breakdown_visualization(product_id: str, language: str) -> Optional[Dict[str, Any]]:
    """Build a pie chart showing market/downstream breakdown for a product."""
    node = next((n for n in domain_graph.nodes if n.id == product_id), None)
    if not node:
        return None

    name = getattr(node, "name_en" if language == "en" else "name_cn", product_id)

    # Try child products via domain graph edges
    children: List[Dict[str, Any]] = []
    for edge in getattr(domain_graph, "edges", []):
        src = getattr(edge, "source", None) or (edge[0] if isinstance(edge, (list, tuple)) else None)
        tgt = getattr(edge, "target", None) or (edge[1] if isinstance(edge, (list, tuple)) else None)
        if src != product_id:
            continue
        child_node = next((n for n in domain_graph.nodes if n.id == tgt), None)
        if child_node:
            price = market_data.get_latest_price(tgt)
            if price and float(price) > 0:
                children.append({
                    "name": getattr(child_node, "name_en" if language == "en" else "name_cn", tgt),
                    "value": round(float(price), 2),
                })

    if len(children) >= 3:
        data: List[Dict[str, Any]] = children[:6]
    else:
        # Generic downstream breakdown fallback
        if language == "en":
            data = [
                {"name": "Rubber/Elastomers", "value": 35},
                {"name": "Plastics", "value": 30},
                {"name": "Adhesives", "value": 20},
                {"name": "Coatings", "value": 15},
            ]
        else:
            data = [
                {"name": "橡胶/弹性体", "value": 35},
                {"name": "塑料", "value": 30},
                {"name": "粘合剂", "value": 20},
                {"name": "涂料", "value": 15},
            ]

    COLORS = ["#2563eb", "#16a34a", "#dc2626", "#f59e0b", "#7c3aed", "#0891b2"]
    return {
        "kind": "pie",
        "title": f"{name} Market Breakdown" if language == "en" else f"{name}市场分布",
        "subtitle": "Downstream segments" if language == "en" else "下游应用结构",
        "unit": "%",
        "x_key": "name",
        "series": [
            {"key": "value", "label": d["name"], "color": COLORS[i % len(COLORS)]}
            for i, d in enumerate(data)
        ],
        "data": data,
        "summary": {},
    }


def _build_supply_demand_visualization(product_id: str, language: str) -> Optional[Dict[str, Any]]:
    """Build a composed chart: price bars + 3-period moving average line."""
    history = market_data.get_price_history(product_id)
    node = next((n for n in domain_graph.nodes if n.id == product_id), None)
    if not node or len(history) < 4:
        return None

    window = history[-12:]
    prices = [float(p["price"]) for p in window]
    data: List[Dict[str, Any]] = []
    for i, point in enumerate(window):
        ma = sum(prices[max(0, i - 2): i + 1]) / len(prices[max(0, i - 2): i + 1])
        lbl = str(point["date"])[5:] if len(str(point["date"])) >= 5 else str(point["date"])
        data.append({"label": lbl, "price": round(float(point["price"]), 2), "ma": round(ma, 2)})

    name = getattr(node, "name_en" if language == "en" else "name_cn", product_id)
    unit = getattr(node, "unit", "")
    return {
        "kind": "composed",
        "title": f"{name} Price & Trend" if language == "en" else f"{name}价格与均线",
        "subtitle": "Bars: actual · Line: 3-period MA" if language == "en" else "柱状：实际价 · 折线：3期移动均线",
        "unit": unit,
        "x_key": "label",
        "series": [
            {"key": "price", "label": "Price" if language == "en" else "价格",
             "color": "#3b82f6", "chart_type": "bar"},
            {"key": "ma", "label": "3P MA" if language == "en" else "3期均线",
             "color": "#f59e0b", "chart_type": "line"},
        ],
        "data": data,
        "summary": {"latest": round(float(window[-1]["price"]), 2)},
    }


def _build_scatter_visualization(product_ids: List[str], language: str) -> Optional[Dict[str, Any]]:
    """Build a scatter plot correlating prices of two products."""
    if len(product_ids) < 2:
        return None
    id1, id2 = product_ids[0], product_ids[1]
    hist1 = market_data.get_price_history(id1)
    hist2 = market_data.get_price_history(id2)
    node1 = next((n for n in domain_graph.nodes if n.id == id1), None)
    node2 = next((n for n in domain_graph.nodes if n.id == id2), None)
    if not node1 or not node2 or len(hist1) < 4 or len(hist2) < 4:
        return None

    h1_map = {str(p["date"])[:10]: float(p["price"]) for p in hist1}
    scatter_data: List[Dict[str, Any]] = []
    for p in hist2[-20:]:
        dt = str(p["date"])[:10]
        if dt in h1_map:
            scatter_data.append({"x": round(h1_map[dt], 2), "y": round(float(p["price"]), 2)})

    if len(scatter_data) < 4:
        return None

    n1 = getattr(node1, "name_en" if language == "en" else "name_cn", id1)
    n2 = getattr(node2, "name_en" if language == "en" else "name_cn", id2)
    return {
        "kind": "scatter",
        "title": f"{n1} vs {n2} Correlation" if language == "en" else f"{n1} vs {n2} 价格联动",
        "subtitle": f"x: {n1} · y: {n2}",
        "unit": getattr(node1, "unit", ""),
        "x_key": "x",
        "series": [{"key": "y", "label": n2, "color": "#7c3aed"}],
        "data": scatter_data,
        "summary": {},
    }


def _build_dynamic_comparison_visualization(
    message: str,
    product_ids: List[str],
    language: str,
) -> Optional[Dict[str, Any]]:
    """Build a multi-metric comparison visualization (Revenue + Volume + Orders)."""
    from backend.src.hermes.skills.dynamic_chart_skill import build_dynamic_comparison_visualization

    return build_dynamic_comparison_visualization(
        query=message,
        product_ids=product_ids,
        language=language,
        source="auto",
        period=None,
    )


def _extract_markdown_table_visualization(markdown: str, language: str) -> Optional[Dict[str, Any]]:
    """Extract the first markdown table from text and convert to a table visualization."""
    import re as _re2
    lines = str(markdown or "").splitlines()
    table_lines: List[str] = []
    in_table = False
    for line in lines:
        if _re2.match(r"^\s*\|.+\|", line):
            in_table = True
            table_lines.append(line.strip())
        elif in_table:
            break  # first complete table captured

    if len(table_lines) < 3:  # header + separator + ≥1 data row
        return None

    header_cells = [c.strip() for c in table_lines[0].split("|") if c.strip()]
    if not header_cells:
        return None

    series = [
        {"key": f"col_{i}", "label": h, "color": ""}
        for i, h in enumerate(header_cells)
    ]
    data: List[Dict[str, Any]] = []
    for row_line in table_lines[2:22]:  # skip separator; cap at 20 rows
        if _re2.match(r"^\s*\|[-: ]+\|", row_line):
            continue
        cells = [c.strip() for c in row_line.split("|") if c.strip() != ""]
        row_dict: Dict[str, Any] = {
            col["key"]: cells[i] if i < len(cells) else "" for i, col in enumerate(series)
        }
        data.append(row_dict)

    if not data:
        return None

    return {
        "kind": "table",
        "title": "Data Summary" if language == "en" else "数据汇总",
        "subtitle": "",
        "x_key": "col_0",
        "series": series,
        "data": data,
        "summary": {},
    }


def _build_response_context(
    *,
    report_context: Any,
    message: str,
    task_type: str,
    language: str,
    raw_markdown: str = "",
    resolved_chat_mode: str = "",
) -> Dict[str, Any]:
    context_dict = (
        report_context.to_dict()
        if hasattr(report_context, "to_dict")
        else dict(report_context or {})
    )
    product_ids = _resolve_product_ids_for_visualization(message, context_dict)
    primary_id = product_ids[0] if product_ids else None

    visualizations: List[Dict[str, Any]] = []

    if primary_id:
        directives = _classify_chart_intent(message, task_type, resolved_chat_mode, product_ids)
        for directive in directives:
            viz: Optional[Dict[str, Any]] = None
            if directive == "pie":
                viz = _build_breakdown_visualization(primary_id, language)
            elif directive == "composed":
                viz = _build_supply_demand_visualization(primary_id, language)
            elif directive == "scatter" and len(product_ids) >= 2:
                viz = _build_scatter_visualization(product_ids, language)
            elif directive == "multi_line" and len(product_ids) >= 2:
                viz = _build_multi_line_visualization(product_ids, language)
            elif directive == "daily_bar":
                viz = _build_erp_daily_bar_visualization(primary_id, language, period=message)
            elif directive == "bar":
                erp_summary = context_dict.get("erp_summary")
                if not isinstance(erp_summary, dict):
                    erp_summary = None
                if erp_summary is None:
                    erp_raw = str(context_dict.get("erp_data") or raw_markdown or "")
                    if erp_raw:
                        try:
                            from backend.src.engine.decision_graph import (
                                _extract_erp_sales_summary_rows,
                            )

                            erp_summary = _extract_erp_sales_summary_rows(erp_raw)
                        except Exception:
                            erp_summary = None
                if isinstance(erp_summary, dict) and list(erp_summary.get("rows") or []):
                    viz = _build_erp_sales_bar_visualization(
                        erp_summary, primary_id, language
                    )
                elif _message_requests_erp_bar_chart(message):
                    viz = None
                else:
                    viz = _build_competitor_visualization(primary_id, language)
            elif directive == "bar_competitor":
                viz = _build_competitor_visualization(primary_id, language)
            elif directive == "forecast_range":
                viz = _build_forecast_range_visualization(primary_id, language)
            elif directive == "line":
                viz = _build_market_visualization(primary_id, language)
            elif directive == "dynamic_comparison":
                viz = _build_dynamic_comparison_visualization(
                    message=message,
                    product_ids=product_ids,
                    language=language,
                )
            # table_from_markdown handled below via raw_markdown
            if viz:
                visualizations.append(viz)

        # Always add forecast range if task_type says forecast but not yet added
        if "forecast_range" not in directives and (
            "forecast" in str(task_type or "").lower()
            or any(kw in str(message or "").lower()
                   for kw in ("forecast", "predict", "outlook", "预测", "趋势预测"))
        ):
            fviz = _build_forecast_range_visualization(primary_id, language)
            if fviz and not any(v.get("kind") == "forecast_range" for v in visualizations):
                visualizations.append(fviz)

    # Auto-extract markdown tables → table visualization
    if raw_markdown and not any(v.get("kind") == "table" for v in visualizations):
        table_viz = _extract_markdown_table_visualization(raw_markdown, language)
        if table_viz:
            visualizations.append(table_viz)

    if visualizations:
        context_dict["visualization"] = visualizations[0]
        context_dict["visualizations"] = visualizations
    return context_dict


def _strip_markdown_sections(markdown: str) -> str:
    text_value = str(markdown or "").strip()
    if not text_value:
        return ""

    lines = text_value.splitlines()
    kept: List[str] = []
    skip = False
    for line in lines:
        trimmed = line.strip()
        if trimmed.startswith("**References") or trimmed.startswith("**参考来源"):
            skip = True
            continue
        if skip and trimmed == "---":
            skip = False
            continue
        if not skip:
            kept.append(line)
    return "\n".join(kept).strip()


def _build_session_report_preview(
    session_title: str, messages: List[Dict[str, Any]]
) -> str:
    user_messages = [
        str(item.get("content") or "").strip()
        for item in messages
        if str(item.get("role") or "") == "user"
    ]
    assistant_messages = [
        str(item.get("content") or "").strip()
        for item in messages
        if str(item.get("role") or "") == "assistant"
    ]
    latest_assistant = _strip_markdown_sections(
        assistant_messages[-1] if assistant_messages else ""
    )
    key_questions = [question for question in user_messages[-3:] if question]

    lines: List[str] = [
        f"# {session_title or 'Generated Session Report'}",
        "",
        f"Generated at: {datetime.utcnow().replace(microsecond=0).isoformat()}Z",
        "",
        "## Conversation Snapshot",
        f"- Total turns: {len(messages)}",
        f"- User questions captured: {len(user_messages)}",
        f"- Assistant responses captured: {len(assistant_messages)}",
        "",
        "## Key Questions",
    ]

    if key_questions:
        lines.extend([f"- {question}" for question in key_questions])
    else:
        lines.append("- No user questions were captured in this session.")

    lines.extend(["", "## Latest Conclusion", ""])
    if latest_assistant:
        lines.append(latest_assistant)
    else:
        lines.append("No assistant conclusion is available yet.")

    lines.extend(
        [
            "",
            "## Suggested Next Steps",
            "- Review the latest conclusion with the supporting market evidence.",
            "- Convert any numeric signals into a formal weekly or executive report if needed.",
            "- Re-run the analysis after new market data or competitor moves are loaded.",
        ]
    )
    return "\n".join(lines).strip()


def _build_report_export_context(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    visualizations = _extract_visualizations_from_messages(messages)
    if not visualizations:
        return {}
    return {
        "visualization": visualizations[0],
        "visualizations": visualizations,
    }


def _build_report_summary(markdown: str, max_chars: int = 220) -> str:
    cleaned = " ".join(_strip_markdown_sections(markdown).replace("#", " ").split())
    if len(cleaned) <= max_chars:
        return cleaned
    return f"{cleaned[: max_chars - 3].rstrip()}..."


def _weekly_draft_anchor_date(reference_time: Optional[datetime] = None) -> datetime:
    current = reference_time or (datetime.utcnow() + timedelta(hours=8))
    monday = current - timedelta(days=current.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


def _weekly_draft_title(reference_time: Optional[datetime] = None) -> str:
    anchor = _weekly_draft_anchor_date(reference_time)
    return f"Weekly Report Draft - {anchor.strftime('%Y-%m-%d')}"


def _strip_primary_heading(markdown: str) -> str:
    lines = str(markdown or "").splitlines()
    if lines and lines[0].strip().startswith("#"):
        lines = lines[1:]
    return "\n".join(lines).strip()


def _build_weekly_digest_entry(
    *,
    session_id: str,
    session_title: str,
    source_markdown: str,
    generated_at: str,
) -> str:
    body = _strip_primary_heading(source_markdown)
    lines = [
        f"## Added From Chat: {session_title}",
        f"- Source session: {session_id}",
        f"- Added at: {generated_at}",
        "",
    ]
    if body:
        lines.append(body)
    else:
        lines.append("No report body was available for this session.")
    return "\n".join(lines).strip()


def _create_or_update_weekly_draft_report(
    user_id: str, session_id: str
) -> Dict[str, Any]:
    session_title, messages = _resolve_session_title_and_messages(user_id, session_id)
    generated_at = datetime.utcnow().replace(microsecond=0).isoformat()
    draft_title = _weekly_draft_title()
    system_session_id = db.get_or_create_system_session(
        user_id, "[System] Weekly Report Drafts"
    )
    source_markdown = _build_session_report_preview(session_title, messages)
    source_context = _build_report_export_context(messages)
    entry_markdown = _build_weekly_digest_entry(
        session_id=session_id,
        session_title=session_title,
        source_markdown=source_markdown,
        generated_at=generated_at,
    )
    existing = db.find_generated_report_by_type_and_title(
        user_id, "weekly_digest_draft", draft_title
    )
    anchor = f"- Source session: {session_id}"

    if existing:
        current_markdown = str(existing.get("markdown") or "")
        if anchor in current_markdown:
            return existing

        next_markdown = "\n\n---\n\n".join(
            part for part in [current_markdown.strip(), entry_markdown] if part.strip()
        )
        updated = db.update_generated_report(
            str(existing.get("id") or ""),
            user_id=user_id,
            title=draft_title,
            summary=_build_report_summary(next_markdown),
            markdown=next_markdown,
            context=existing.get("context") if isinstance(existing.get("context"), dict) else source_context,
            read_at=datetime.utcnow().replace(microsecond=0).isoformat(),
        )
        if not updated:
            raise HTTPException(
                status_code=500, detail="Unable to update weekly draft report"
            )
        return updated

    initial_markdown = "\n".join(
        [
            f"# {draft_title}",
            "",
            "This draft collects chat-generated report sections that should feed the weekly management report.",
            "",
            entry_markdown,
        ]
    ).strip()
    return db.create_generated_report(
        user_id=user_id,
        session_id=system_session_id,
        title=draft_title,
        summary=_build_report_summary(initial_markdown),
        markdown=initial_markdown,
        context=source_context,
        report_type="weekly_digest_draft",
        read_at=datetime.utcnow().replace(microsecond=0).isoformat(),
    )


def _resolve_session_title_and_messages(
    user_id: str, session_id: str
) -> tuple[str, List[Dict[str, Any]]]:
    if not db.session_belongs_to_user(session_id, user_id):
        raise HTTPException(status_code=404, detail="Session not found")

    session_row = next(
        (
            item
            for item in db.get_sessions(user_id)
            if str(item.get("id") or "") == session_id
        ),
        None,
    )
    messages = db.get_messages(session_id)
    if not messages:
        raise HTTPException(status_code=400, detail="Session has no messages")

    session_title = str(
        (session_row or {}).get("title") or "Generated Session Report"
    ).strip()
    return session_title, messages


def _compute_next_weekly_run(
    *,
    weekday: int,
    hour: int,
    minute: int,
    now: Optional[datetime] = None,
) -> datetime:
    current = now or (datetime.utcnow() + timedelta(hours=8))
    candidate = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    days_ahead = (weekday - candidate.weekday()) % 7
    if days_ahead == 0 and candidate <= current:
        days_ahead = 7
    return candidate + timedelta(days=days_ahead)


def _report_label(report_type: str) -> str:
    template_id = _extract_template_id_from_schedule_type(report_type)
    if template_id:
        template = db.get_report_template(template_id)
        if template:
            return str(template.get("name") or "Template Weekly Report")
        return "Template Weekly Report"
    return SCHEDULED_REPORT_LABELS.get(
        report_type, report_type.replace("_", " ").title()
    )


def _safe_export_basename(title: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", (title or "report").strip()).strip(
        "-._"
    )
    return sanitized or "report"


def _clean_markdown_inline(text: str) -> str:
    cleaned = str(text or "").replace("\u00a0", " ")
    cleaned = cleaned.replace("\\*", "*").replace("\\_", "_").replace("\\#", "#")
    cleaned = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r"\1", cleaned)
    cleaned = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", cleaned)
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"__([^_]+)__", r"\1", cleaned)
    cleaned = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", cleaned)
    cleaned = re.sub(r"(?<!_)_([^_]+)_(?!_)", r"\1", cleaned)
    cleaned = re.sub(r"~~([^~]+)~~", r"\1", cleaned)
    cleaned = re.sub(r"<[^>]+>", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _parse_markdown_blocks(markdown: str) -> List[Dict[str, Any]]:
    lines = str(markdown or "").splitlines()
    blocks: List[Dict[str, Any]] = []
    index = 0

    def is_table_start(position: int) -> bool:
        if position + 1 >= len(lines):
            return False
        current = lines[position].strip()
        nxt = lines[position + 1].strip()
        return (
            current.startswith("|")
            and current.endswith("|")
            and re.match(r"^\|?(\s*:?-{3,}:?\s*\|)+\s*$", nxt) is not None
        )

    while index < len(lines):
        stripped = lines[index].strip()

        if not stripped:
            index += 1
            continue

        if stripped.startswith("```"):
            index += 1
            code_lines: List[str] = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code_lines.append(lines[index].rstrip())
                index += 1
            if index < len(lines):
                index += 1
            if code_lines:
                blocks.append({"type": "paragraph", "text": " ".join(code_lines)})
            continue

        heading_match = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading_match:
            blocks.append(
                {
                    "type": "heading",
                    "level": len(heading_match.group(1)),
                    "text": _clean_markdown_inline(heading_match.group(2)),
                }
            )
            index += 1
            continue

        if re.match(r"^[-*_]{3,}$", stripped):
            blocks.append({"type": "separator"})
            index += 1
            continue

        if is_table_start(index):
            table_rows: List[List[str]] = []
            while index < len(lines):
                row = lines[index].strip()
                if not (row.startswith("|") and row.endswith("|")):
                    break
                if re.match(r"^\|?(\s*:?-{3,}:?\s*\|)+\s*$", row):
                    index += 1
                    continue
                cells = [_clean_markdown_inline(cell.strip()) for cell in row.strip("|").split("|")]
                table_rows.append(cells)
                index += 1
            if table_rows:
                blocks.append({"type": "table", "rows": table_rows})
            continue

        bullet_match = re.match(r"^[-*+]\s+(.*)$", stripped)
        numbered_match = re.match(r"^\d+[.)]\s+(.*)$", stripped)
        if bullet_match or numbered_match:
            items: List[str] = []
            while index < len(lines):
                current = lines[index].strip()
                bullet_match = re.match(r"^[-*+]\s+(.*)$", current)
                numbered_match = re.match(r"^\d+[.)]\s+(.*)$", current)
                if bullet_match:
                    items.append(_clean_markdown_inline(bullet_match.group(1)))
                    index += 1
                    continue
                if numbered_match:
                    items.append(_clean_markdown_inline(numbered_match.group(1)))
                    index += 1
                    continue
                break
            if items:
                blocks.append({"type": "list", "items": items})
            continue

        paragraph_lines: List[str] = []
        while index < len(lines):
            current = lines[index].strip()
            if not current:
                break
            if current.startswith("```"):
                break
            if re.match(r"^(#{1,6})\s+", current):
                break
            if re.match(r"^[-*_]{3,}$", current):
                break
            if is_table_start(index):
                break
            if re.match(r"^[-*+]\s+", current) or re.match(r"^\d+[.)]\s+", current):
                break
            paragraph_lines.append(_clean_markdown_inline(current))
            index += 1
        if paragraph_lines:
            blocks.append({"type": "paragraph", "text": " ".join(part for part in paragraph_lines if part)})
            continue
        index += 1

    return blocks


def _markdown_to_export_sections(markdown: str) -> List[Dict[str, Any]]:
    sections: List[Dict[str, Any]] = []
    current: Dict[str, Any] = {"title": "Overview", "blocks": []}

    for block in _parse_markdown_blocks(markdown):
        if block["type"] == "heading":
            if current["blocks"]:
                sections.append(current)
            current = {"title": block["text"] or "Section", "blocks": []}
            continue
        current["blocks"].append(block)

    if current["blocks"]:
        sections.append(current)
    return sections or [{"title": "Overview", "blocks": [{"type": "paragraph", "text": _clean_markdown_inline(markdown)}]}]


def _extract_visualizations_from_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    visualizations: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for message in reversed(messages):
        if str(message.get("role") or "") != "assistant":
            continue
        context = message.get("context") or {}
        candidates: List[Dict[str, Any]] = []
        if isinstance(context, dict):
            multi = context.get("visualizations") or []
            if isinstance(multi, list):
                candidates.extend(item for item in multi if isinstance(item, dict))
            single = context.get("visualization")
            if isinstance(single, dict):
                candidates.append(single)
        for viz in candidates:
            marker = json.dumps(
                {
                    "kind": viz.get("kind"),
                    "title": viz.get("title"),
                    "x_key": viz.get("x_key"),
                },
                sort_keys=True,
                ensure_ascii=False,
            )
            if marker in seen:
                continue
            seen.add(marker)
            visualizations.append(viz)
            if len(visualizations) >= 4:
                return list(reversed(visualizations))
    return list(reversed(visualizations))


def _markdown_to_plain_lines(markdown: str) -> List[str]:
    lines: List[str] = []
    for raw_line in (markdown or "").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            lines.append("")
            continue

        if stripped.startswith("#"):
            heading = re.sub(r"^#+\s*", "", stripped)
            lines.append(heading)
            continue

        if re.match(r"^\|?(\s*---+\s*\|)+\s*$", stripped):
            continue

        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            lines.append(" | ".join(cells))
            continue

        bullet_match = re.match(r"^[-*+]\s+(.*)$", stripped)
        if bullet_match:
            lines.append(f"- {_clean_markdown_inline(bullet_match.group(1))}")
            continue

        numbered_match = re.match(r"^\d+\.\s+(.*)$", stripped)
        if numbered_match:
            lines.append(f"- {_clean_markdown_inline(numbered_match.group(1))}")
            continue

        cleaned = _clean_markdown_inline(stripped)
        lines.append(cleaned)

    while lines and not lines[-1]:
        lines.pop()
    return lines


def _markdown_to_sections(markdown: str) -> List[Dict[str, Any]]:
    sections: List[Dict[str, Any]] = []
    current = {
        "title": "Overview",
        "lines": [],
    }

    for raw_line in (markdown or "").splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("#"):
            if current["lines"]:
                sections.append(current)
            current = {
                "title": re.sub(r"^#+\s*", "", stripped) or "Section",
                "lines": [],
            }
            continue
        current["lines"].append(raw_line)

    if current["title"] or current["lines"]:
        sections.append(current)

    normalized: List[Dict[str, Any]] = []
    for section in sections:
        plain_lines = _markdown_to_plain_lines("\n".join(section["lines"]))
        if section["title"] == "Overview" and not plain_lines:
            continue
        normalized.append(
            {
                "title": section["title"],
                "lines": plain_lines,
            }
        )
    return normalized or [
        {"title": "Overview", "lines": _markdown_to_plain_lines(markdown)}
    ]


def _render_visualization_to_png(viz: Dict[str, Any], width: int = 800, height: int = 360) -> Optional[bytes]:
    """Render a ChatVisualization dict to a PNG image using matplotlib. Returns None if unavailable."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.ticker as mticker
        from matplotlib import font_manager
        import numpy as np
    except ImportError:
        return None

    def _to_number(value: Any) -> float:
        if value is None:
            return float("nan")
        if isinstance(value, (int, float)):
            return float(value)
        cleaned = re.sub(r"[^0-9.\-]", "", str(value).replace(",", "").strip())
        if cleaned in {"", "-", ".", "-."}:
            return float("nan")
        try:
            return float(cleaned)
        except (TypeError, ValueError):
            return float("nan")

    fig = None
    try:
        cjk_font_path = next((path for path in _CJK_FONT_PATHS if path.exists()), None)
        if cjk_font_path is not None:
            try:
                font_manager.fontManager.addfont(str(cjk_font_path))
                font_name = font_manager.FontProperties(fname=str(cjk_font_path)).get_name()
                matplotlib.rcParams["font.family"] = font_name
                matplotlib.rcParams["font.sans-serif"] = [font_name, *_CJK_FONT_NAMES, "Noto Sans", "DejaVu Sans"]
            except Exception:
                matplotlib.rcParams["font.sans-serif"] = [*_CJK_FONT_NAMES, "Noto Sans", "DejaVu Sans"]
        else:
            matplotlib.rcParams["font.sans-serif"] = [*_CJK_FONT_NAMES, "Noto Sans", "DejaVu Sans"]
        matplotlib.rcParams["axes.unicode_minus"] = False

        kind = str(viz.get("kind") or "line")
        data = viz.get("data") or []
        series_list = viz.get("series") or []
        x_key = str(viz.get("x_key") or "label")
        title = str(viz.get("title") or "")
        subtitle = str(viz.get("subtitle") or "")
        unit = str(viz.get("unit") or "")

        labels = [str(row.get(x_key, "")) for row in data]
        dpi = 96
        fig, ax = plt.subplots(figsize=(width / dpi, height / dpi), dpi=dpi)
        ax.set_facecolor("#f8fafc")
        fig.patch.set_facecolor("#f8fafc")
        ax.grid(axis="y", linestyle="--", color="#e2e8f0", linewidth=0.8, zorder=0)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color("#e2e8f0")
        ax.spines["bottom"].set_color("#e2e8f0")

        if kind in ("line", "multi_line", "forecast_range"):
            x = np.arange(len(labels))
            for s in series_list:
                y = [_to_number(row.get(s["key"])) for row in data]
                alpha = 0.8 if kind == "forecast_range" else 1.0
                ax.plot(x, y, label=s.get("label", s["key"]), color=s.get("color", "#2563eb"),
                        linewidth=2.0, alpha=alpha, marker="o" if kind != "forecast_range" else None,
                        markersize=4)
                if kind == "forecast_range":
                    ax.fill_between(x, y, alpha=0.08, color=s.get("color", "#2563eb"))
            ax.set_xticks(x)
            ax.set_xticklabels(labels, fontsize=9, rotation=30, ha="right")
        elif kind == "composed":
            x = np.arange(len(labels))
            n_bars = max(1, sum(1 for s in series_list if str(s.get("chart_type") or "bar") == "bar"))
            bar_width = 0.6 / n_bars
            bar_i = 0
            for s in series_list:
                y = [_to_number(row.get(s["key"])) for row in data]
                chart_type = str(s.get("chart_type") or "bar").lower()
                if chart_type == "line":
                    ax.plot(
                        x,
                        y,
                        label=s.get("label", s["key"]),
                        color=s.get("color", "#2563eb"),
                        linewidth=2.0,
                        marker="o",
                        markersize=4,
                        zorder=4,
                    )
                else:
                    offset = (bar_i - n_bars / 2 + 0.5) * bar_width
                    ax.bar(
                        x + offset,
                        y,
                        width=bar_width,
                        label=s.get("label", s["key"]),
                        color=s.get("color", "#f59e0b"),
                        alpha=0.85,
                        zorder=3,
                        edgecolor="white",
                        linewidth=0.5,
                    )
                    bar_i += 1
            ax.set_xticks(x)
            ax.set_xticklabels(labels, fontsize=9, rotation=30, ha="right")
        elif kind == "pie":
            if data and series_list:
                pie_series = series_list[0]
                values = [_to_number(row.get(pie_series["key"])) for row in data]
                filtered = [
                    (labels[i], values[i], series_list[i].get("color") if i < len(series_list) else None)
                    for i in range(len(values))
                    if not np.isnan(values[i])
                ]
                if filtered:
                    pie_labels = [item[0] for item in filtered]
                    pie_values = [item[1] for item in filtered]
                    pie_colors = [item[2] for item in filtered]
                    ax.clear()
                    ax.set_facecolor("#f8fafc")
                    ax.pie(
                        pie_values,
                        labels=pie_labels,
                        autopct="%1.1f%%",
                        startangle=140,
                        colors=pie_colors,
                        textprops={"fontsize": 9},
                    )
                    ax.axis("equal")
        elif kind == "scatter":
            if series_list:
                y_key = series_list[0]["key"]
                x_values = [_to_number(row.get(x_key)) for row in data]
                y_values = [_to_number(row.get(y_key)) for row in data]
                valid_points = [
                    (x_values[i], y_values[i])
                    for i in range(len(x_values))
                    if not np.isnan(x_values[i]) and not np.isnan(y_values[i])
                ]
                if valid_points:
                    xv, yv = zip(*valid_points)
                    ax.scatter(
                        xv,
                        yv,
                        color=series_list[0].get("color", "#7c3aed"),
                        alpha=0.8,
                        s=34,
                        edgecolors="white",
                        linewidths=0.5,
                    )
                    ax.set_xlabel(x_key, fontsize=9, color="#64748b")
                    ax.set_ylabel(series_list[0].get("label", y_key), fontsize=9, color="#64748b")
        elif kind == "table":
            ax.clear()
            ax.axis("off")
            table_series = series_list
            if data and table_series:
                column_labels = [str(s.get("label", s.get("key", ""))) for s in table_series]
                rows = [
                    [str(row.get(str(s.get("key", "")), "")) for s in table_series]
                    for row in data[:12]
                ]
                table = ax.table(
                    cellText=rows,
                    colLabels=column_labels,
                    loc="center",
                    cellLoc="left",
                )
                table.auto_set_font_size(False)
                table.set_fontsize(8)
                table.scale(1, 1.35)
        else:
            # bar / fallback
            x = np.arange(len(labels))
            n = len(series_list)
            width_bar = 0.6 / max(n, 1)
            for i, s in enumerate(series_list):
                y = [_to_number(row.get(s["key"])) for row in data]
                offset = (i - n / 2 + 0.5) * width_bar
                ax.bar(x + offset, y, width=width_bar, label=s.get("label", s["key"]),
                       color=s.get("color", "#f59e0b"), alpha=0.85, zorder=3,
                       edgecolor="white", linewidth=0.5)
            ax.set_xticks(x)
            ax.set_xticklabels(labels, fontsize=9, rotation=30, ha="right")

        if kind not in ("pie", "table"):
            # Tighten y-axis around the actual data range so price gaps are readable
            if kind not in ("bar", "composed"):
                all_y_vals = []
                for s in series_list:
                    for row in data:
                        v = _to_number(row.get(s["key"]))
                        if not np.isnan(v):
                            all_y_vals.append(v)
                if all_y_vals:
                    y_lo, y_hi = min(all_y_vals), max(all_y_vals)
                    y_rng = y_hi - y_lo if y_hi != y_lo else max(abs(y_hi) * 0.1, 100)
                    pad = y_rng * 0.12
                    ax.set_ylim(y_lo - pad, y_hi + pad)
            ax.yaxis.set_major_formatter(mticker.FuncFormatter(
                lambda v, _: f"{v:,.0f}" + (f" {unit}" if unit and v == max(ax.get_ylim()) else "")
            ))
            ax.tick_params(axis="y", labelsize=9, labelcolor="#64748b")

        full_title = title + (f"\n{subtitle}" if subtitle else "")
        ax.set_title(full_title, fontsize=11, fontweight="semibold", color="#0f172a", pad=8)
        if kind not in ("pie", "table") and any(s.get("label") for s in series_list):
            ax.legend(fontsize=9, frameon=False)

        plt.tight_layout(pad=1.2)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=dpi)
        buf.seek(0)
        return buf.read()
    except Exception as exc:
        logger.debug("Visualization PNG render failed: %s", exc)
        return None
    finally:
        if fig is not None:
            try:
                plt.close(fig)
            except Exception:
                pass


def _extract_visualizations_for_export(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract visualizations stored in the report's context (if any)."""
    ctx = report.get("context") or {}
    if isinstance(ctx, dict):
        vizs = ctx.get("visualizations") or []
        if isinstance(vizs, list) and vizs:
            return vizs
        single = ctx.get("visualization")
        if isinstance(single, dict):
            return [single]
    session_id = str(report.get("session_id") or "").strip()
    if session_id:
        try:
            return _extract_visualizations_from_messages(db.get_messages(session_id))
        except Exception as exc:
            logger.debug("Visualization fallback lookup failed for report export: %s", exc)
    return []


def _docx_apply_font(paragraph: Any, font_name: str, size_pt: Optional[int] = None, bold: bool = False) -> None:
    try:
        from docx.oxml.ns import qn
    except ImportError:
        return

    for run in paragraph.runs:
        run.font.name = font_name
        rpr = run._element.get_or_add_rPr()
        rpr.rFonts.set(qn("w:eastAsia"), font_name)
        if size_pt is not None:
            run.font.size = size_pt
        run.font.bold = bold or run.font.bold


def _section_to_slide_lines(section: Dict[str, Any], max_lines: int = 8) -> List[str]:
    lines: List[str] = []
    for block in section.get("blocks") or []:
        if block["type"] == "paragraph" and block.get("text"):
            lines.append(str(block["text"]))
        elif block["type"] == "list":
            lines.extend(f"• {item}" for item in block.get("items") or [])
        elif block["type"] == "table":
            rows = block.get("rows") or []
            if rows:
                lines.append(" | ".join(rows[0]))
                for row in rows[1:3]:
                    lines.append(" | ".join(row))
    return lines[:max_lines]


def _export_report_to_docx(report: Dict[str, Any]) -> bytes:
    try:
        from docx import Document
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Inches, Pt
    except ImportError as exc:
        raise HTTPException(
            status_code=503, detail="DOCX export dependency is not installed"
        ) from exc

    document = Document()
    font_name = _CJK_PRIMARY_FONT
    normal_style = document.styles["Normal"]
    normal_style.font.name = font_name
    normal_style.font.size = Pt(10.5)

    title_paragraph = document.add_paragraph()
    title_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title_run = title_paragraph.add_run(str(report.get("title") or "Report"))
    title_run.bold = True
    title_run.font.name = font_name
    title_run.font.size = Pt(18)
    _docx_apply_font(title_paragraph, font_name, Pt(18), bold=True)

    meta_paragraph = document.add_paragraph()
    meta_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    meta_run = meta_paragraph.add_run(
        f"Generated at: {str(report.get('created_at') or '')}"
    )
    meta_run.italic = True
    meta_run.font.name = font_name
    meta_run.font.size = Pt(9)
    _docx_apply_font(meta_paragraph, font_name, Pt(9))

    # Embed charts near the top if available
    for viz in _extract_visualizations_for_export(report):
        png_bytes = _render_visualization_to_png(viz)
        if png_bytes:
            heading = document.add_heading(str(viz.get("title") or "Chart"), level=2)
            _docx_apply_font(heading, font_name, Pt(13), bold=True)
            document.add_picture(io.BytesIO(png_bytes), width=Inches(5.5))
            if viz.get("subtitle"):
                subtitle = document.add_paragraph(str(viz.get("subtitle") or ""))
                subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
                _docx_apply_font(subtitle, font_name, Pt(9))

    report_title = _clean_markdown_inline(str(report.get("title") or "Report"))
    for section in _markdown_to_export_sections(str(report.get("markdown") or "")):
        section_title = _clean_markdown_inline(str(section.get("title") or "")).strip()
        if section_title and section_title != report_title:
            heading = document.add_heading(section_title, level=1)
            _docx_apply_font(heading, font_name, Pt(14), bold=True)
        for block in section.get("blocks") or []:
            if block["type"] == "paragraph" and block.get("text"):
                paragraph = document.add_paragraph(str(block["text"]))
                paragraph.paragraph_format.space_after = Pt(6)
                _docx_apply_font(paragraph, font_name, Pt(10.5))
            elif block["type"] == "list":
                for item in block.get("items") or []:
                    paragraph = document.add_paragraph(str(item), style="List Bullet")
                    _docx_apply_font(paragraph, font_name, Pt(10.5))
            elif block["type"] == "table":
                rows = block.get("rows") or []
                if not rows:
                    continue
                col_count = max(len(row) for row in rows)
                table = document.add_table(rows=len(rows), cols=col_count)
                table.style = "Table Grid"
                for row_index, row in enumerate(rows):
                    for col_index in range(col_count):
                        value = str(row[col_index]) if col_index < len(row) else ""
                        cell_paragraph = table.cell(row_index, col_index).paragraphs[0]
                        cell_paragraph.text = value
                        _docx_apply_font(cell_paragraph, font_name, Pt(10), bold=row_index == 0)
            elif block["type"] == "separator":
                document.add_paragraph("")

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _export_report_to_pptx(report: Dict[str, Any]) -> bytes:
    try:
        from pptx import Presentation
        from pptx.util import Inches, Pt
    except ImportError as exc:
        raise HTTPException(
            status_code=503, detail="PPTX export dependency is not installed"
        ) from exc

    presentation = Presentation()
    font_name = _CJK_PRIMARY_FONT
    title_slide = presentation.slides.add_slide(presentation.slide_layouts[0])
    title_slide.shapes.title.text = str(report.get("title") or "Report")
    title_slide.placeholders[1].text = (
        f"Generated at {str(report.get('created_at') or '')}"
    )
    for shape in title_slide.shapes:
        if not hasattr(shape, "text_frame"):
            continue
        for paragraph in shape.text_frame.paragraphs:
            for run in paragraph.runs:
                run.font.name = font_name

    # Add a slide per visualization
    for viz in _extract_visualizations_for_export(report):
        png_bytes = _render_visualization_to_png(viz, width=900, height=420)
        if not png_bytes:
            continue
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])  # blank
        slide_w = presentation.slide_width
        slide_h = presentation.slide_height
        img_w = int(slide_w * 0.85)
        img_h = int(img_w * 420 / 900)
        left = int((slide_w - img_w) / 2)
        top = int((slide_h - img_h) / 2)
        slide.shapes.add_picture(
            io.BytesIO(png_bytes), left, top, width=img_w, height=img_h
        )
        # Caption
        txb = slide.shapes.add_textbox(left, top + img_h + int(Inches(0.1)), img_w, int(Inches(0.35)))
        txb.text_frame.text = str(viz.get("title") or "")
        for para in txb.text_frame.paragraphs:
            for run in para.runs:
                run.font.name = font_name
                run.font.size = Pt(10)

    report_title = _clean_markdown_inline(str(report.get("title") or "Report"))
    for section in _markdown_to_export_sections(str(report.get("markdown") or "")):
        slide_lines = _section_to_slide_lines(section)
        section_title = _clean_markdown_inline(str(section.get("title") or "")).strip()
        if not section_title and not slide_lines:
            continue
        slide = presentation.slides.add_slide(presentation.slide_layouts[1])
        slide.shapes.title.text = str(section_title or report_title or "Section")
        text_frame = slide.placeholders[1].text_frame
        text_frame.clear()

        lines = slide_lines or [""]
        for index, line in enumerate(lines[:8]):
            paragraph = (
                text_frame.paragraphs[0] if index == 0 else text_frame.add_paragraph()
            )
            paragraph.text = line
            paragraph.level = 0
            for run in paragraph.runs:
                run.font.name = font_name
                run.font.size = Pt(16 if index == 0 else 14)

    buffer = io.BytesIO()
    presentation.save(buffer)
    return buffer.getvalue()


def _export_report_to_pdf(report: Dict[str, Any]) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.platypus import Image as RLImage, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as exc:
        raise HTTPException(
            status_code=503, detail="PDF export dependency is not installed"
        ) from exc

    try:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        base_font = "STSong-Light"
    except Exception:
        base_font = "Helvetica"

    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=48,
        rightMargin=48,
        topMargin=48,
        bottomMargin=48,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ExportTitle",
        parent=styles["Title"],
        fontName=base_font,
        fontSize=20,
        leading=24,
        spaceAfter=10,
        textColor=colors.HexColor("#0f172a"),
        alignment=1,
    )
    meta_style = ParagraphStyle(
        "ExportMeta",
        parent=styles["BodyText"],
        fontName=base_font,
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#64748b"),
        alignment=1,
        spaceAfter=12,
    )
    heading_style = ParagraphStyle(
        "ExportHeading",
        parent=styles["Heading2"],
        fontName=base_font,
        fontSize=13,
        leading=16,
        textColor=colors.HexColor("#0f172a"),
        spaceBefore=6,
        spaceAfter=6,
    )
    body_style = ParagraphStyle(
        "ExportBody",
        parent=styles["BodyText"],
        fontName=base_font,
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#1e293b"),
        spaceAfter=4,
    )
    bullet_style = ParagraphStyle(
        "ExportBullet",
        parent=body_style,
        leftIndent=14,
        firstLineIndent=-8,
    )
    story: List[Any] = []

    story.append(Paragraph(html.escape(str(report.get("title") or "Report")), title_style))
    story.append(
        Paragraph(
            html.escape(f"Generated at: {str(report.get('created_at') or '')}"), meta_style
        )
    )
    story.append(Spacer(1, 16))

    # Embed charts
    for viz in _extract_visualizations_for_export(report):
        png_bytes = _render_visualization_to_png(viz)
        if png_bytes:
            story.append(Paragraph(html.escape(str(viz.get("title") or "Chart")), heading_style))
            story.append(Spacer(1, 6))
            story.append(RLImage(io.BytesIO(png_bytes), width=6.1 * inch, height=2.8 * inch))
            if viz.get("subtitle"):
                story.append(Spacer(1, 4))
                story.append(Paragraph(html.escape(str(viz.get("subtitle") or "")), meta_style))
            story.append(Spacer(1, 12))

    report_title = _clean_markdown_inline(str(report.get("title") or "Report"))
    for section in _markdown_to_export_sections(str(report.get("markdown") or "")):
        section_title = _clean_markdown_inline(str(section.get("title") or "")).strip()
        if section_title and section_title != report_title:
            story.append(Paragraph(html.escape(section_title), heading_style))
            story.append(Spacer(1, 4))
        for block in section.get("blocks") or []:
            if block["type"] == "paragraph" and block.get("text"):
                story.append(Paragraph(html.escape(str(block["text"])), body_style))
            elif block["type"] == "list":
                for item in block.get("items") or []:
                    story.append(Paragraph(html.escape(f"• {item}"), bullet_style))
            elif block["type"] == "table":
                rows = block.get("rows") or []
                if rows:
                    table = Table(rows, hAlign="LEFT")
                    table.setStyle(
                        TableStyle(
                            [
                                ("FONTNAME", (0, 0), (-1, -1), base_font),
                                ("FONTSIZE", (0, 0), (-1, -1), 9),
                                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
                                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
                                ("TEXTCOLOR", (0, 1), (-1, -1), colors.HexColor("#1e293b")),
                                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                                ("TOPPADDING", (0, 0), (-1, -1), 4),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                            ]
                        )
                    )
                    story.append(table)
            elif block["type"] == "separator":
                story.append(Spacer(1, 6))
        story.append(Spacer(1, 10))

    document.build(story)
    return buffer.getvalue()


def _export_generated_report_bytes(report: Dict[str, Any], export_format: str) -> bytes:
    # Use ReportExportService if report has blocks, otherwise fall back to markdown export
    if report.get("blocks") and export_format in ("pdf", "docx", "pptx", "html"):
        from backend.src.services.report_export_service import ReportExportService
        export_service = ReportExportService()
        
        # Transform report to ReportExportService format
        export_data = {
            "title": str(report.get("title") or "Report"),
            "generated_at": str(report.get("created_at") or datetime.utcnow().isoformat()),
            "blocks": report.get("blocks", [])
        }
        
        return export_service.export_report(export_data, format=export_format)
    
    # Fallback to old markdown-based export
    if export_format == "pdf":
        return _export_report_to_pdf(report)
    if export_format == "docx":
        return _export_report_to_docx(report)
    if export_format == "pptx":
        return _export_report_to_pptx(report)
    raise HTTPException(status_code=400, detail="Unsupported export format")


def _serialize_generated_report_summary(
    row: Dict[str, Any],
) -> GeneratedReportSummaryResponse:
    return GeneratedReportSummaryResponse(
        id=str(row.get("id") or ""),
        session_id=str(row.get("session_id") or ""),
        report_type=str(row.get("report_type") or ""),
        title=str(row.get("title") or ""),
        summary=str(row.get("summary") or ""),
        status=str(row.get("status") or "draft"),
        approved_at=(str(row.get("approved_at")) if row.get("approved_at") else None),
        approved_by=(str(row.get("approved_by")) if row.get("approved_by") else None),
        is_unread=bool(row.get("is_unread")),
        read_at=(str(row.get("read_at")) if row.get("read_at") else None),
        created_at=str(row.get("created_at") or ""),
    )


def _serialize_generated_report(row: Dict[str, Any]) -> GeneratedReportResponse:
    summary = _serialize_generated_report_summary(row)
    return GeneratedReportResponse(
        **summary.model_dump(),
        markdown=str(row.get("markdown") or ""),
        blocks=[item for item in (row.get("blocks") or []) if isinstance(item, dict)],
        context=(row.get("context") if isinstance(row.get("context"), dict) else {}),
        last_refreshed_at=(
            str(row.get("last_refreshed_at"))
            if row.get("last_refreshed_at")
            else None
        ),
    )


def _normalize_report_blocks(blocks: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for index, raw in enumerate(blocks or []):
        if not isinstance(raw, dict):
            continue
        block_id = str(raw.get("id") or uuid.uuid4())
        block_type = str(raw.get("type") or "rich_text").strip().lower() or "rich_text"
        title = str(raw.get("title") or "").strip()
        definition = raw.get("definition") if isinstance(raw.get("definition"), dict) else {}
        rendered = raw.get("rendered") if isinstance(raw.get("rendered"), dict) else {}
        normalized.append(
            {
                "id": block_id,
                "order": int(raw.get("order") or index),
                "type": block_type,
                "title": title,
                "definition": definition,
                "rendered": rendered,
                "error": str(raw.get("error") or "").strip() or None,
                "last_refreshed_at": (
                    str(raw.get("last_refreshed_at"))
                    if raw.get("last_refreshed_at")
                    else None
                ),
            }
        )
    return sorted(normalized, key=lambda item: int(item.get("order") or 0))


def _refresh_single_report_block(
    block: Dict[str, Any], report: Dict[str, Any]
) -> Dict[str, Any]:
    refreshed_at = datetime.utcnow().replace(microsecond=0).isoformat()
    next_block = dict(block)
    block_type = str(next_block.get("type") or "rich_text").strip().lower()
    definition = (
        dict(next_block.get("definition") or {})
        if isinstance(next_block.get("definition"), dict)
        else {}
    )

    rendered: Dict[str, Any] = {}
    error: Optional[str] = None

    try:
        if block_type == "rich_text":
            rendered = {"content": str(definition.get("content") or "")}
        elif block_type == "table":
            rows, source_meta = _resolve_block_rows_from_definition(definition)
            rendered = {"rows": rows, "source": source_meta}
        elif block_type == "kpi":
            value = definition.get("value")
            if str(definition.get("query") or "").strip() or (
                isinstance(definition.get("data_source"), dict)
                and str((definition.get("data_source") or {}).get("query") or "").strip()
            ):
                rows, source_meta = _resolve_block_rows_from_definition(definition)
                value = None
                if rows and isinstance(rows[0], dict):
                    first_row = rows[0]
                    first_key = next(iter(first_row.keys()), None)
                    if first_key is not None:
                        value = first_row.get(first_key)
                rendered = {
                    "label": str(
                        definition.get("label") or next_block.get("title") or "KPI"
                    ),
                    "value": value,
                    "unit": str(definition.get("unit") or ""),
                    "source": source_meta,
                }
            else:
                rendered = {
                    "label": str(
                        definition.get("label") or next_block.get("title") or "KPI"
                    ),
                    "value": value,
                    "unit": str(definition.get("unit") or ""),
                }
        elif block_type == "chart":
            points = definition.get("points")
            source_meta: Dict[str, Any] = {"kind": "inline"}
            if str(definition.get("query") or "").strip() or (
                isinstance(definition.get("data_source"), dict)
                and str((definition.get("data_source") or {}).get("query") or "").strip()
            ):
                rows, source_meta = _resolve_block_rows_from_definition(definition)
                x_key = str(definition.get("x_key") or "x")
                y_key = str(definition.get("y_key") or "y")
                points = [
                    {
                        "x": row.get(x_key),
                        "y": row.get(y_key),
                    }
                    for row in rows
                    if isinstance(row, dict)
                ]
            rendered = {
                "series_name": str(definition.get("series_name") or "Series"),
                "points": points if isinstance(points, list) else [],
                "source": source_meta,
            }
        elif block_type == "image":
            rendered = {
                "url": str(definition.get("url") or "").strip(),
                "alt": str(definition.get("alt") or ""),
            }
        elif block_type == "chat_output":
            fallback = str(report.get("markdown") or "")
            rendered = {"content": str(definition.get("content") or fallback)}
        elif block_type == "custom_sql":
            query_text = str(
                definition.get("sql_query") or definition.get("query") or ""
            ).strip()
            limit_value = int(definition.get("limit") or 100)
            rows, source_meta = _resolve_block_rows_from_definition(
                {
                    **definition,
                    "data_source": {
                        "kind": "custom_sql",
                        "query": query_text,
                        "limit": limit_value,
                    },
                },
            )
            column_names = list(rows[0].keys()) if rows else []
            rendered = {
                "type": "custom_sql",
                "title": str(
                    next_block.get("title") or definition.get("title") or "Custom Query"
                ),
                "columns": [{"key": col, "label": col} for col in column_names],
                "rows": rows,
                "sql_query": query_text,
                "limit": int((source_meta or {}).get("limit") or limit_value),
                "source": source_meta,
            }
        else:
            rendered = dict(next_block.get("rendered") or {})
    except Exception as exc:
        error = str(exc)
        rendered = dict(next_block.get("rendered") or {})

    next_block["rendered"] = rendered
    next_block["error"] = error
    next_block["last_refreshed_at"] = refreshed_at
    return next_block


def _sanitize_sql_identifier(value: str) -> str:
    candidate = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_\.]*", candidate):
        raise ValueError("Invalid SQL identifier")
    return candidate


def _ensure_single_select(query: str) -> str:
    candidate = str(query or "").strip()
    lowered = candidate.lower()
    if not candidate:
        raise ValueError("SQL query is empty")
    if not lowered.startswith("select"):
        raise ValueError("Only SELECT queries are allowed")
    if ";" in candidate.rstrip(";"):
        raise ValueError("Only single SELECT query is allowed")
    return candidate.rstrip(";")


def _run_block_select(query: str, limit: int = 200) -> List[Dict[str, Any]]:
    safe_query = _ensure_single_select(query)
    safe_limit = max(1, min(500, int(limit or 200)))
    wrapped_sql = (
        f"SELECT * FROM ({safe_query}) AS _report_block_query LIMIT :_limit"
    )
    with db.engine.connect() as conn:
        rows = conn.execute(text(wrapped_sql), {"_limit": safe_limit})
        return [dict(row._mapping) for row in rows]


def _resolve_block_rows_from_definition(
    definition: Dict[str, Any],
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    data_source = (
        dict(definition.get("data_source") or {})
        if isinstance(definition.get("data_source"), dict)
        else {}
    )
    source_kind = str(data_source.get("kind") or "").strip().lower()
    source_ref = str(data_source.get("ref") or "").strip()
    source_query = str(
        data_source.get("query") or data_source.get("sql_query") or ""
    ).strip()
    source_limit = int(data_source.get("limit") or definition.get("limit") or 200)

    if source_kind == "view" and source_ref:
        safe_ref = _sanitize_sql_identifier(source_ref)
        rows = _run_block_select(f"SELECT * FROM {safe_ref}", source_limit)
        return rows, {
            "kind": "view",
            "ref": safe_ref,
            "limit": max(1, min(500, source_limit)),
        }

    if source_kind in {"query", "custom_sql"} and source_query:
        rows = _run_block_select(source_query, source_limit)
        return rows, {
            "kind": "query",
            "query": source_query,
            "limit": max(1, min(500, source_limit)),
        }

    query = str(definition.get("query") or definition.get("sql_query") or "").strip()
    if query:
        rows = _run_block_select(query, int(definition.get("limit") or 200))
        return rows, {
            "kind": "query",
            "query": query,
            "limit": max(1, min(500, int(definition.get("limit") or 200))),
        }

    rows = definition.get("rows")
    if isinstance(rows, list):
        normalized_rows = [
            dict(item) for item in rows if isinstance(item, dict)
        ]
        return normalized_rows, {
            "kind": "inline",
            "rows": len(normalized_rows),
        }

    return [], {"kind": "empty"}


def _refresh_report_blocks(
    report: Dict[str, Any], block_id: Optional[str] = None
) -> tuple[List[Dict[str, Any]], int, str]:
    blocks = _normalize_report_blocks(
        report.get("blocks") if isinstance(report.get("blocks"), list) else []
    )
    refreshed_at = datetime.utcnow().replace(microsecond=0).isoformat()
    if not blocks:
        return [], 0, refreshed_at

    refreshed_count = 0
    refreshed_blocks: List[Dict[str, Any]] = []
    target_id = str(block_id or "").strip() or None

    for block in blocks:
        if target_id and str(block.get("id") or "") != target_id:
            refreshed_blocks.append(block)
            continue
        refreshed_blocks.append(_refresh_single_report_block(block, report))
        refreshed_count += 1

    return refreshed_blocks, refreshed_count, refreshed_at


def _serialize_report_recipient(user: Dict[str, Any]) -> ReportRecipientResponse:
    return ReportRecipientResponse(
        id=str(user.get("id") or ""),
        username=str(user.get("username") or ""),
        role_label="Admin" if bool(user.get("is_admin")) else "User",
    )


def _serialize_report_delivery(row: Dict[str, Any]) -> ReportDeliveryResponse:
    return ReportDeliveryResponse(
        id=str(row.get("id") or ""),
        report_id=str(row.get("report_id") or ""),
        sender_user_id=str(row.get("sender_user_id") or ""),
        delivery_mode=str(row.get("delivery_mode") or "share"),
        recipient_ids=[str(item) for item in (row.get("recipient_ids") or [])],
        recipient_labels=[str(item) for item in (row.get("recipient_labels") or [])],
        message=(str(row.get("message")) if row.get("message") else None),
        status=str(row.get("status") or "queued"),
        provider=(str(row.get("provider")) if row.get("provider") else None),
        delivered_at=str(row.get("delivered_at") or ""),
    )


def _serialize_report_template(row: Dict[str, Any], schedule: Optional[Dict[str, Any]] = None) -> ReportTemplateResponse:
    # Map weekday number to name
    weekday_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    
    auto_enabled = False
    auto_day = None
    auto_time = None
    
    if schedule and schedule.get("enabled"):
        auto_enabled = True
        weekday = int(schedule.get("weekday", 0))
        hour = int(schedule.get("hour", 8))
        minute = int(schedule.get("minute", 0))
        auto_day = weekday_names[weekday] if 0 <= weekday < 7 else "Monday"
        auto_time = f"{hour:02d}:{minute:02d}"
    
    return ReportTemplateResponse(
        id=str(row.get("id") or ""),
        name=str(row.get("name") or ""),
        description=(str(row.get("description")) if row.get("description") else None),
        time_period=str(row.get("time_period") or "weekly"),
        audience=(str(row.get("audience")) if row.get("audience") else None),
        schedule_hint=(
            str(row.get("schedule_hint")) if row.get("schedule_hint") else None
        ),
        sections=[str(item) for item in (row.get("sections") or [])],
        template_title=(
            str(row.get("template_title")) if row.get("template_title") else None
        ),
        template_markdown=(
            str(row.get("template_markdown")) if row.get("template_markdown") else None
        ),
        template_blocks=[
            item
            for item in (row.get("template_blocks") or [])
            if isinstance(item, dict)
        ],
        created_at=str(row.get("created_at") or ""),
        updated_at=str(row.get("updated_at") or ""),
        updated_by=(str(row.get("updated_by")) if row.get("updated_by") else None),
        auto_schedule_enabled=auto_enabled,
        auto_schedule_day=auto_day,
        auto_schedule_time=auto_time,
    )


def _serialize_report_category(row: Dict[str, Any]) -> ReportCategoryResponse:
    return ReportCategoryResponse(
        id=str(row.get("id") or ""),
        org_id=str(row.get("org_id") or ""),
        parent_id=(str(row.get("parent_id")) if row.get("parent_id") else None),
        name=str(row.get("name") or ""),
        icon=(str(row.get("icon")) if row.get("icon") else None),
        position=int(row.get("position") or 0),
        permissions=dict(row.get("permissions") or {}),
        created_at=str(row.get("created_at") or ""),
        updated_at=str(row.get("updated_at") or ""),
        created_by=(str(row.get("created_by")) if row.get("created_by") else None),
    )


def _serialize_cockpit_definition(row: Dict[str, Any]) -> CockpitDefinitionResponse:
    return CockpitDefinitionResponse(
        id=str(row.get("id") or ""),
        name=str(row.get("name") or ""),
        org_id=str(row.get("org_id") or ""),
        department_id=(
            str(row.get("department_id")) if row.get("department_id") else None
        ),
        layout=dict(row.get("layout") or {}),
        status=str(row.get("status") or "draft"),
        version=int(row.get("version") or 1),
        is_default=bool(row.get("is_default")),
        permissions=dict(row.get("permissions") or {}),
        created_by=(str(row.get("created_by")) if row.get("created_by") else None),
        created_at=str(row.get("created_at") or ""),
        updated_at=str(row.get("updated_at") or ""),
        published_at=(str(row.get("published_at")) if row.get("published_at") else None),
    )


def _serialize_alert_rule(row: Dict[str, Any]) -> AlertRuleResponse:
    return AlertRuleResponse(
        id=str(row.get("id") or ""),
        name=str(row.get("name") or ""),
        org_id=str(row.get("org_id") or ""),
        created_by=(str(row.get("created_by")) if row.get("created_by") else None),
        metric_expression=str(row.get("metric_expression") or ""),
        condition_type=str(row.get("condition_type") or ""),
        condition_params=dict(row.get("condition_params") or {}),
        severity=str(row.get("severity") or "medium"),
        category=str(row.get("category") or "custom"),
        notification_channels=list(row.get("notification_channels") or []),
        default_owner_id=(
            str(row.get("default_owner_id")) if row.get("default_owner_id") else None
        ),
        enabled=bool(row.get("enabled")),
        evaluation_interval_min=int(row.get("evaluation_interval_min") or 15),
        created_at=str(row.get("created_at") or ""),
        updated_at=str(row.get("updated_at") or ""),
    )


def _serialize_data_source(row: Dict[str, Any]) -> DataSourceResponse:
    return DataSourceResponse(
        id=str(row.get("id") or ""),
        org_id=str(row.get("org_id") or ""),
        name=str(row.get("name") or ""),
        connector_type=str(row.get("connector_type") or "mysql"),
        connection_config=dict(row.get("connection_config") or {}),
        status=str(row.get("status") or "active"),
        last_tested_at=(
            str(row.get("last_tested_at")) if row.get("last_tested_at") else None
        ),
        created_by=(str(row.get("created_by")) if row.get("created_by") else None),
        created_at=str(row.get("created_at") or ""),
        updated_at=str(row.get("updated_at") or ""),
    )


def _serialize_dataset(row: Dict[str, Any]) -> DatasetResponse:
    return DatasetResponse(
        id=str(row.get("id") or ""),
        source_id=str(row.get("source_id") or ""),
        org_id=str(row.get("org_id") or ""),
        friendly_name=str(row.get("friendly_name") or ""),
        physical_reference=str(row.get("physical_reference") or ""),
        description=(str(row.get("description")) if row.get("description") else None),
        tags=[str(item) for item in (row.get("tags") or [])],
        schema_snapshot=dict(row.get("schema_snapshot") or {}),
        status=str(row.get("status") or "active"),
        created_at=str(row.get("created_at") or ""),
        updated_at=str(row.get("updated_at") or ""),
    )


def _serialize_dataset_column_mapping(
    row: Dict[str, Any],
) -> DatasetColumnMappingResponse:
    return DatasetColumnMappingResponse(
        dataset_id=str(row.get("dataset_id") or ""),
        physical_column=str(row.get("physical_column") or ""),
        ontology_concept=str(row.get("ontology_concept") or ""),
        transform=(str(row.get("transform")) if row.get("transform") else None),
    )


def _serialize_dataset_permission(row: Dict[str, Any]) -> DatasetPermissionResponse:
    return DatasetPermissionResponse(
        dataset_id=str(row.get("dataset_id") or ""),
        role_id=str(row.get("role_id") or ""),
        can_query=bool(row.get("can_query")),
        can_export=bool(row.get("can_export")),
        can_chat=bool(row.get("can_chat")),
    )


def _serialize_platform_product(row: Dict[str, Any]) -> PlatformProductResponse:
    return PlatformProductResponse(
        id=str(row.get("id") or ""),
        org_id=str(row.get("org_id") or ""),
        name_en=str(row.get("name_en") or ""),
        name_cn=str(row.get("name_cn") or ""),
        product_type=str(row.get("product_type") or ""),
        unit=str(row.get("unit") or "CNY/ton"),
        description=(str(row.get("description")) if row.get("description") else None),
        aliases=[str(a) for a in (row.get("aliases") or [])],
        data_table=(str(row.get("data_table")) if row.get("data_table") else None),
        data_key=(str(row.get("data_key")) if row.get("data_key") else None),
        display_in=[str(d) for d in (row.get("display_in") or [])],
        created_by=(str(row.get("created_by")) if row.get("created_by") else None),
        created_at=str(row.get("created_at") or ""),
        updated_at=str(row.get("updated_at") or ""),
    )


def _smtp_config() -> Dict[str, str]:
    return {
        "host": str(os.getenv("SMTP_HOST") or "").strip(),
        "port": str(os.getenv("SMTP_PORT") or "").strip(),
        "username": str(os.getenv("SMTP_USERNAME") or "").strip(),
        "password": str(os.getenv("SMTP_PASSWORD") or "").strip(),
        "from": str(os.getenv("SMTP_FROM") or "").strip(),
        "use_tls": str(os.getenv("SMTP_USE_TLS") or "true").strip().lower(),
    }


def _send_report_delivery_email(
    report: Dict[str, Any], recipients: List[Dict[str, Any]], message: Optional[str]
) -> str:
    smtp = _smtp_config()
    if not smtp["host"] or not smtp["from"]:
        return "simulated"

    email_message = EmailMessage()
    email_message["Subject"] = (
        f"EDIA Report: {str(report.get('title') or 'Approved Report')}"
    )
    email_message["From"] = smtp["from"]
    email_message["To"] = ", ".join(
        str(item.get("username") or "") for item in recipients
    )
    body = [
        str(report.get("title") or "Approved Report"),
        "",
        str(report.get("summary") or ""),
        "",
        str(message or "").strip(),
        "",
        str(report.get("markdown") or ""),
    ]
    email_message.set_content("\n".join(part for part in body if part is not None))

    port = int(smtp["port"] or "587")
    use_tls = smtp["use_tls"] not in {"0", "false", "no", "off"}
    with smtplib.SMTP(smtp["host"], port) as server:
        if use_tls:
            server.starttls()
        if smtp["username"]:
            server.login(smtp["username"], smtp["password"])
        server.send_message(email_message)
    return "smtp"


def _build_template_generated_report(
    template: Dict[str, Any],
    user_id: str,
    mark_as_read: bool = True,
    report_type: str = "management_template_draft",
) -> Dict[str, Any]:
    now = datetime.utcnow().replace(microsecond=0)
    now_iso = now.isoformat()
    default_title = (
        f"{str(template.get('name') or 'Management Report')} - {now.strftime('%Y-%m-%d')}"
    )
    title = str(template.get("template_title") or "").strip() or default_title

    template_markdown = str(template.get("template_markdown") or "").strip()
    if template_markdown:
        markdown = template_markdown
    else:
        sections = [
            str(item).strip()
            for item in (template.get("sections") or [])
            if str(item).strip()
        ]
        lines = [
            f"# {title}",
            "",
            f"Template: {str(template.get('name') or '')}",
            f"Time Period: {str(template.get('time_period') or '')}",
        ]
        if template.get("audience"):
            lines.append(f"Audience: {str(template.get('audience') or '')}")
        if template.get("schedule_hint"):
            lines.append(f"Schedule: {str(template.get('schedule_hint') or '')}")
        if template.get("description"):
            lines.extend(["", str(template.get("description") or "")])
        for section in sections:
            lines.extend(
                [
                    "",
                    f"## {section}",
                    "",
                    f"Add {section.lower()} content here.",
                ]
            )
        markdown = "\n".join(lines).strip()

    template_blocks = _normalize_report_blocks(
        [
            item
            for item in (template.get("template_blocks") or [])
            if isinstance(item, dict)
        ]
    )

    session_id = db.get_or_create_system_session(
        user_id, "[System] Template Generated Reports"
    )
    created = db.create_generated_report(
        user_id=user_id,
        session_id=session_id,
        title=title,
        summary=_build_report_summary(markdown),
        markdown=markdown,
        blocks=template_blocks,
        context={
            "template_id": str(template.get("id") or ""),
            "template_name": str(template.get("name") or ""),
            "generated_at": now_iso,
        },
        report_type=report_type,
        read_at=(now_iso if mark_as_read else None),
    )

    refreshed_blocks, _, refreshed_at = _refresh_report_blocks(created)
    if not refreshed_blocks:
        return created

    updated = db.update_generated_report(
        report_id=str(created.get("id") or ""),
        user_id=user_id,
        title=str(created.get("title") or title),
        summary=_build_report_summary(str(created.get("markdown") or markdown)),
        markdown=str(created.get("markdown") or markdown),
        blocks=refreshed_blocks,
        context=created.get("context") if isinstance(created.get("context"), dict) else {},
        read_at=str(created.get("read_at") or now_iso),
        last_refreshed_at=refreshed_at,
    )
    if not updated:
        return created
    return updated


def _serialize_report_schedule(schedule: Dict[str, Any]) -> ReportScheduleResponse:
    next_run = _compute_next_weekly_run(
        weekday=int(schedule.get("weekday") or 0),
        hour=int(schedule.get("hour") or 8),
        minute=int(schedule.get("minute") or 0),
    )
    report_type = str(schedule.get("report_type") or "weekly_market")
    return ReportScheduleResponse(
        id=str(schedule.get("id") or ""),
        report_type=report_type,
        report_label=_report_label(report_type),
        enabled=bool(schedule.get("enabled")),
        weekday=int(schedule.get("weekday") or 0),
        hour=int(schedule.get("hour") or 8),
        minute=int(schedule.get("minute") or 0),
        timezone=str(schedule.get("timezone") or "Asia/Shanghai"),
        last_run_at=(
            str(schedule.get("last_run_at")) if schedule.get("last_run_at") else None
        ),
        next_run_at=next_run.replace(microsecond=0).isoformat(),
    )


def _build_weekly_market_report_markdown(
    reference_time: Optional[datetime] = None,
) -> str:
    now = reference_time or datetime.utcnow()
    snapshot = _compute_market_snapshot()
    sorted_snapshot = sorted(
        snapshot,
        key=lambda item: abs(float(item.get("weekly_change_pct") or 0.0)),
        reverse=True,
    )
    highlights = sorted_snapshot[:5]
    focus_ids = ["cracked_c5", "cracked_c9", "isoprene", "piperylene", "dcpd"]
    focus_rows = [item for item in snapshot if str(item.get("id") or "") in focus_ids]

    lines: List[str] = [
        f"# Weekly Market Report - {now.strftime('%Y-%m-%d')}",
        "",
        "## Executive Summary",
    ]

    if highlights:
        for item in highlights[:3]:
            product_name = str(item.get("name") or item.get("id") or "Product")
            weekly_change = float(item.get("weekly_change_pct") or 0.0)
            latest_price = float(item.get("price") or 0.0)
            direction = "up" if weekly_change >= 0 else "down"
            lines.append(
                f"- {product_name} moved {weekly_change:+.2f}% week over week to {latest_price:,.0f} {item.get('unit') or ''}. Momentum remains {direction}."
            )
    else:
        lines.append("- Market snapshot data is not available yet.")

    lines.extend(
        [
            "",
            "## Core Product Snapshot",
            "| Product | Latest Price | WoW Change | 7-day High | 7-day Low |",
            "|---|---:|---:|---:|---:|",
        ]
    )

    if focus_rows:
        for item in focus_rows:
            lines.append(
                f"| {item.get('name') or item.get('id')} | {float(item.get('price') or 0.0):,.0f} | {float(item.get('weekly_change_pct') or 0.0):+.2f}% | {float(item.get('high') or 0.0):,.0f} | {float(item.get('low') or 0.0):,.0f} |"
            )
    else:
        lines.append("| No tracked products available | 0 | 0.00% | 0 | 0 |")

    lines.extend(
        [
            "",
            "## Key Watchpoints",
        ]
    )

    if highlights:
        for item in highlights:
            lines.append(
                f"- Watch {item.get('name') or item.get('id')}: weekly move {float(item.get('weekly_change_pct') or 0.0):+.2f}% with latest price {float(item.get('price') or 0.0):,.0f}."
            )
    else:
        lines.append("- Refresh market data before publishing the next weekly report.")

    lines.extend(
        [
            "",
            "## Recommended Actions",
            "- Review chart-backed movements in chat for any product with outsized weekly change.",
            "- Re-check competitor benchmarking before approving price moves for benchmark-sensitive products.",
            "- Use the report archive as the in-product distribution channel for Monday morning updates.",
        ]
    )
    return "\n".join(lines).strip()


def _build_weekly_executive_report_markdown(
    reference_time: Optional[datetime] = None,
) -> str:
    now = reference_time or (datetime.utcnow() + timedelta(hours=8))
    snapshot = _compute_market_snapshot()
    ranked = sorted(
        snapshot,
        key=lambda item: abs(float(item.get("weekly_change_pct") or 0.0)),
        reverse=True,
    )
    top_moves = ranked[:3]
    focus = ranked[:5]

    lines: List[str] = [
        f"# Weekly Executive Brief - {now.strftime('%Y-%m-%d')}",
        "",
        "## What Changed This Week",
    ]
    if top_moves:
        lines.extend(
            [
                f"- {item.get('name')}: {float(item.get('weekly_change_pct') or 0.0):+,.2f}% WoW, latest {float(item.get('price') or 0.0):,.0f} {item.get('unit') or ''}."
                for item in top_moves
            ]
        )
    else:
        lines.append("- Market snapshot data is not available yet.")

    lines.extend(
        [
            "",
            "## Executive Implications",
            "- Price volatility remains concentrated in a small number of chain nodes; those products should receive immediate commercial review.",
            "- Chat-based trend and competitor charts can be used for product-specific follow-up before any quote move is approved.",
            "- Scheduled reports should be reviewed alongside alerts and dashboard shifts to confirm the Monday opening position.",
            "",
            "## Priority Watchlist",
        ]
    )
    if focus:
        lines.extend(
            [
                f"- {item.get('name')}: 7-day range {float(item.get('low') or 0.0):,.0f} to {float(item.get('high') or 0.0):,.0f}, weekly move {float(item.get('weekly_change_pct') or 0.0):+,.2f}%."
                for item in focus
            ]
        )
    else:
        lines.append("- No watchlist available.")

    lines.extend(
        [
            "",
            "## Recommended Leadership Actions",
            "- Confirm whether the largest movers require revised pricing guardrails this week.",
            "- Ask sales and market teams to validate the benchmark-sensitive products with the competitor watch report.",
            "- Keep the report archive as the system of record for weekly management reporting.",
        ]
    )
    return "\n".join(lines).strip()


def _build_weekly_competitor_report_markdown(
    reference_time: Optional[datetime] = None,
) -> str:
    from backend.src.service.competitor_service import COMPETITOR_BENCHMARKS

    now = reference_time or (datetime.utcnow() + timedelta(hours=8))
    lines: List[str] = [
        f"# Weekly Competitor Watch - {now.strftime('%Y-%m-%d')}",
        "",
        "## Benchmark Snapshot",
        "| Product | Our Price | Competitor | Reference Price | Spread vs Us |",
        "| --- | ---: | --- | ---: | ---: |",
    ]

    row_count = 0
    for product_id, competitors in COMPETITOR_BENCHMARKS.items():
        own_price = float(market_data.get_latest_price(product_id) or 0.0)
        if own_price <= 0:
            continue
        for competitor in competitors[:3]:
            reference_price = 0.0
            for key in ("key_account", "quoted", "listed", "price"):
                if competitor.get(key) is not None:
                    reference_price = float(competitor[key])
                    break
            if reference_price <= 0 and competitor.get("range_low") is not None:
                low = float(competitor.get("range_low") or 0.0)
                high = float(competitor.get("range_high") or low)
                reference_price = (low + high) / 2.0
            if reference_price <= 0:
                continue
            row_count += 1
            lines.append(
                f"| {product_id} | {own_price:,.0f} | {competitor.get('name') or 'Competitor'} | {reference_price:,.0f} | {own_price - reference_price:+,.0f} |"
            )

    if row_count == 0:
        lines.append("| n/a | 0 | No competitor data | 0 | 0 |")

    lines.extend(
        [
            "",
            "## Competitive Interpretation",
            "- Use products with the widest negative spread as the first priority for account-level defense.",
            "- Use products with a positive spread advantage to test disciplined price improvement where demand allows.",
            "- Pair this report with the inline competitor charts in chat before final quote adjustments.",
        ]
    )
    return "\n".join(lines).strip()


def _generated_report_type_for_schedule(report_type: str) -> str:
    return f"scheduled_{report_type}"


def _scheduled_system_session_title(report_type: str) -> str:
    return f"[System] {_report_label(report_type)} Archive"


def _scheduled_report_title(report_type: str, run_time: datetime) -> str:
    return f"{_report_label(report_type)} - {run_time.strftime('%Y-%m-%d')}"


def _build_scheduled_report_markdown(
    report_type: str, reference_time: Optional[datetime] = None
) -> str:
    if report_type == "weekly_market":
        return _build_weekly_market_report_markdown(reference_time)
    if report_type == "weekly_executive":
        return _build_weekly_executive_report_markdown(reference_time)
    if report_type == "weekly_competitor":
        return _build_weekly_competitor_report_markdown(reference_time)
    raise HTTPException(status_code=404, detail="Unsupported report schedule template")


def _create_scheduled_report_for_user(
    user_id: str,
    report_type: str,
    reference_time: Optional[datetime] = None,
    mark_as_read: bool = False,
) -> Dict[str, Any]:
    template_id = _extract_template_id_from_schedule_type(report_type)
    if template_id:
        template = db.get_report_template(template_id)
        if not template:
            raise HTTPException(status_code=404, detail="Template not found")
        report = _build_template_generated_report(
            template,
            user_id,
            mark_as_read=mark_as_read,
            report_type=_generated_report_type_for_schedule(report_type),
        )
        # Create notification alert
        from backend.src.service.alert_service import alert_worker
        alert_worker._db.insert_notification_alert(
            product="report",
            product_name="Scheduled Report",
            message=f"Your scheduled report '{template.get('title', 'Report')}' has been generated and is ready to view.",
            severity="low"
        )
        return report

    run_time = reference_time or (datetime.utcnow() + timedelta(hours=8))
    session_id = db.get_or_create_system_session(
        user_id, _scheduled_system_session_title(report_type)
    )
    title = _scheduled_report_title(report_type, run_time)
    markdown = _build_scheduled_report_markdown(report_type, run_time)
    summary = _build_report_summary(markdown)
    read_at = (
        datetime.utcnow().replace(microsecond=0).isoformat() if mark_as_read else None
    )
    report = db.create_generated_report(
        user_id=user_id,
        session_id=session_id,
        title=title,
        summary=summary,
        markdown=markdown,
        report_type=_generated_report_type_for_schedule(report_type),
        read_at=read_at,
    )
    # Create notification alert
    from backend.src.service.alert_service import alert_worker
    alert_worker._db.insert_notification_alert(
        product="report",
        product_name="Scheduled Report",
        message=f"Your scheduled report '{title}' has been generated and is ready to view.",
        severity="low"
    )
    return report


async def _weekly_report_scheduler_loop() -> None:
    while True:
        try:
            now = (datetime.utcnow() + timedelta(hours=8)).replace(
                second=0, microsecond=0
            )
            schedules = db.list_enabled_report_schedules()
            for schedule in schedules:
                try:
                    weekday = int(schedule.get("weekday") or 0)
                    hour = int(schedule.get("hour") or 8)
                    minute = int(schedule.get("minute") or 0)
                    if (
                        now.weekday() != weekday
                        or now.hour != hour
                        or now.minute != minute
                    ):
                        continue

                    last_run_at = str(schedule.get("last_run_at") or "").strip()
                    if last_run_at.startswith(now.date().isoformat()):
                        continue

                    _create_scheduled_report_for_user(
                        str(schedule.get("user_id") or ""),
                        str(schedule.get("report_type") or "weekly_market"),
                        now,
                        mark_as_read=False,
                    )
                    db.mark_weekly_report_schedule_run(
                        str(schedule.get("id") or ""), now.isoformat()
                    )
                except Exception as schedule_exc:
                    logger.warning(
                        "Skipping invalid report schedule %s: %s",
                        str(schedule.get("id") or ""),
                        schedule_exc,
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Scheduled report scheduler error: %s", exc)

        await asyncio.sleep(60)


async def _oilchem_scheduler_loop() -> None:
    while True:
        try:
            now = (datetime.utcnow() + timedelta(hours=8)).replace(
                second=0, microsecond=0
            )
            if oilchem_sync_service.enabled():
                if (
                    now.hour == oilchem_sync_service.schedule_hour()
                    and now.minute == oilchem_sync_service.schedule_minute()
                ):
                    loop = asyncio.get_event_loop()
                    result = await loop.run_in_executor(
                        None, oilchem_sync_service.sync_current_snapshot
                    )
                    logger.info("Oilchem scheduler sync result: %s", result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Oilchem scheduler error: %s", exc)

        await asyncio.sleep(60)


async def _morning_briefing_scheduler_loop() -> None:
    """Auto-trigger morning briefing at 07:30 Beijing time (UTC+8) on weekdays."""
    _last_briefing_date: Optional[str] = None

    while True:
        try:
            now = (datetime.utcnow() + timedelta(hours=8)).replace(
                second=0, microsecond=0
            )
            today = now.date().isoformat()
            if (
                now.weekday() < 5  # Monday–Friday
                and now.hour == 7
                and now.minute == 30
                and _last_briefing_date != today
            ):
                _last_briefing_date = today
                try:
                    briefing_md = _build_today_briefing(language="zh")
                    await _market_live_hub.publish(
                        "morning_briefing",
                        {
                            "markdown": briefing_md,
                            "date": today,
                            "generated_at": now.isoformat(),
                        },
                    )
                    logger.info("Morning briefing published for %s", today)
                except Exception as be:
                    logger.warning("Morning briefing generation failed: %s", be)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Morning briefing scheduler error: %s", exc)

        await asyncio.sleep(60)


AUTH_EXEMPT_PATHS = {
    "/api/auth/login",
    "/api/stream/market-live",
    "/api/report-authoring/preview-block",  # Allow preview without auth for testing
    "/api/report-authoring/data-sources",  # Allow data sources without auth for testing
    "/api/reports/export-professional",  # Allow export without auth for testing
    "/api/erp/catalog",  # Product metadata only — no sensitive data
}

PASSWORD_CHANGE_ALLOWED_PATHS = {
    "/api/auth/me",
    "/api/auth/logout",
    "/api/auth/change-password",
}

_CHAT_CONTEXT_ALLOWED_SUFFIXES = {
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".xlsx",
    ".xls",
    ".pdf",
}
_CHAT_CONTEXT_MAX_BYTES = 5 * 1024 * 1024
_CHAT_CONTEXT_MAX_PROMPT_CHARS = 4000
_CHAT_CONTEXT_MAX_EXCERPT_CHARS = 480


def _auth_token_ttl_hours() -> int:
    try:
        configured = int(os.getenv("AUTH_TOKEN_TTL_HOURS", "168"))
    except ValueError:
        configured = 168
    return max(1, configured)


def _truncate_text(value: str, limit: int) -> str:
    text_value = (value or "").strip()
    if len(text_value) <= limit:
        return text_value
    return text_value[: limit - 3].rstrip() + "..."


def _decode_text_payload(raw_bytes: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("utf-8", errors="ignore")


def _dataframe_to_chat_context(df, label: str) -> tuple[str, str]:
    safe_df = df.fillna("").astype(str)
    row_count = len(safe_df.index)
    columns = [str(col) for col in safe_df.columns.tolist()]
    sample = safe_df.head(8)
    sample_csv = sample.to_csv(index=False)
    content = (
        f"Section: {label}\n"
        f"Rows: {row_count}\n"
        f"Columns: {', '.join(columns[:20])}\n"
        f"Sample rows:\n{sample_csv}"
    )
    summary = f"{label}: {row_count} rows, {len(columns)} columns"
    return _truncate_text(content, _CHAT_CONTEXT_MAX_PROMPT_CHARS), summary


def _extract_chat_context_text(filename: str, raw_bytes: bytes) -> tuple[str, str, str]:
    suffix = Path(filename).suffix.lower()
    if suffix not in _CHAT_CONTEXT_ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Use TXT, CSV, XLSX, PDF, MD, or JSON.",
        )

    if suffix in {".txt", ".md"}:
        text_content = _decode_text_payload(raw_bytes)
        lines = [line.strip() for line in text_content.splitlines() if line.strip()]
        summary = f"{len(lines)} non-empty lines extracted"
        return (
            _truncate_text(text_content, _CHAT_CONTEXT_MAX_PROMPT_CHARS),
            _truncate_text(text_content, _CHAT_CONTEXT_MAX_EXCERPT_CHARS),
            summary,
        )

    if suffix == ".json":
        try:
            parsed = json.loads(_decode_text_payload(raw_bytes))
            pretty = json.dumps(parsed, ensure_ascii=False, indent=2)
            if isinstance(parsed, dict):
                summary = f"JSON object with {len(parsed.keys())} top-level fields"
            elif isinstance(parsed, list):
                summary = f"JSON array with {len(parsed)} items"
            else:
                summary = f"JSON value of type {type(parsed).__name__}"
        except json.JSONDecodeError:
            pretty = _decode_text_payload(raw_bytes)
            summary = "JSON-like text extracted"
        return (
            _truncate_text(pretty, _CHAT_CONTEXT_MAX_PROMPT_CHARS),
            _truncate_text(pretty, _CHAT_CONTEXT_MAX_EXCERPT_CHARS),
            summary,
        )

    if suffix == ".csv":
        import pandas as pd

        try:
            df = pd.read_csv(io.StringIO(_decode_text_payload(raw_bytes)))
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail=f"Unable to read CSV file: {exc}"
            )
        content, summary = _dataframe_to_chat_context(df, "CSV")
        return (
            content,
            _truncate_text(content, _CHAT_CONTEXT_MAX_EXCERPT_CHARS),
            summary,
        )

    if suffix in {".xlsx", ".xls"}:
        import pandas as pd

        try:
            workbook = pd.ExcelFile(io.BytesIO(raw_bytes))
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail=f"Unable to read spreadsheet: {exc}"
            )

        sections: List[str] = []
        summaries: List[str] = []
        for sheet_name in workbook.sheet_names[:3]:
            df = workbook.parse(sheet_name)
            content, summary = _dataframe_to_chat_context(df, f"Sheet {sheet_name}")
            sections.append(content)
            summaries.append(summary)
        joined = "\n\n".join(sections)
        summary = "; ".join(summaries) if summaries else "Spreadsheet extracted"
        return (
            _truncate_text(joined, _CHAT_CONTEXT_MAX_PROMPT_CHARS),
            _truncate_text(joined, _CHAT_CONTEXT_MAX_EXCERPT_CHARS),
            summary,
        )

    if suffix == ".pdf":
        from pypdf import PdfReader

        try:
            reader = PdfReader(io.BytesIO(raw_bytes))
            chunks = [(page.extract_text() or "") for page in reader.pages[:5]]
            joined = "\n".join(chunk for chunk in chunks if chunk.strip())
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail=f"Unable to read PDF file: {exc}"
            )

        if not joined.strip():
            raise HTTPException(
                status_code=400, detail="The PDF did not contain extractable text."
            )
        summary = f"PDF text extracted from up to {min(5, len(reader.pages))} pages"
        return (
            _truncate_text(joined, _CHAT_CONTEXT_MAX_PROMPT_CHARS),
            _truncate_text(joined, _CHAT_CONTEXT_MAX_EXCERPT_CHARS),
            summary,
        )

    raise HTTPException(status_code=400, detail="Unsupported file type.")


def _build_effective_chat_message(
    message: str,
    attachment_contexts: Optional[List[ChatAttachmentContext]],
) -> str:
    if not attachment_contexts:
        return message

    sections: List[str] = []
    base_message = (message or "").strip()
    if base_message:
        sections.append(base_message)

    uploaded_sections: List[str] = []
    for index, attachment in enumerate(attachment_contexts, start=1):
        content = _truncate_text(
            attachment.content or attachment.excerpt,
            _CHAT_CONTEXT_MAX_PROMPT_CHARS,
        )
        if not content:
            continue
        block = [
            f"[Uploaded File {index}] {attachment.filename} ({attachment.file_type})"
        ]
        if attachment.summary:
            block.append(f"Summary: {attachment.summary}")
        block.append(content)
        uploaded_sections.append("\n".join(block))

    if not uploaded_sections:
        return message

    sections.append(
        "Use the uploaded file context below as supporting evidence when answering. If the file data changes your recommendation, say so explicitly."
    )
    sections.extend(uploaded_sections)
    return "\n\n".join(section for section in sections if section.strip())


def _public_user(user: Dict[str, object]) -> Dict[str, object]:
    access_profile = db.get_access_profile_by_username(str(user.get("username", "")))
    return {
        "id": user["id"],
        "username": user["username"],
        "is_admin": bool(user.get("is_admin")),
        "is_active": bool(user.get("is_active", True)),
        "must_change_password": bool(user.get("must_change_password", False)),
        "created_at": user.get("created_at"),
        "created_by": user.get("created_by"),
        "organization_id": access_profile.get("organization_id"),
        "organization_name": access_profile.get("organization_name"),
        "department_id": access_profile.get("department_id"),
        "department_name": access_profile.get("department_name"),
        "role_id": access_profile.get("role_id"),
        "role_name": access_profile.get("role_name"),
        "feature_access": access_profile.get("feature_access") or {},
    }


_RECOMMENDATION_LIBRARY: List[Dict[str, Any]] = [
    {
        "id": "market-sensing",
        "icon_key": "trending-up",
        "badge": {"zh": "行情感知", "en": "Market Sensing"},
        "prompt": {
            "zh": "基于中国市场数据库，本周裂解C5和异戊二烯主流价格分别是多少？环比涨跌是多少？",
            "en": "Based on the China market database, what are the mainstream prices for Cracked C5 and Isoprene this week, and what are the week-over-week changes?",
        },
        "reason": {
            "zh": "帮助你快速判断市场方向与价格动量。",
            "en": "Quickly establish market direction and price momentum.",
        },
        "roles": ["executive", "sales", "risk", "procurement", "general"],
        "priority": 90,
        "color": "from-sky-500 to-blue-500",
    },
    {
        "id": "pricing-advice",
        "icon_key": "pricing",
        "badge": {"zh": "定价建议", "en": "Pricing Advice"},
        "prompt": {
            "zh": "请基于中国区数据库数据，给出间戊二烯本周建议报价区间，并说明与中石化上海挂牌价的价差。",
            "en": "Based on the China regional database, give me the recommended quote range for Piperylene this week and explain the spread versus the SINOPEC Shanghai listed price.",
        },
        "reason": {
            "zh": "适合销售与管理层快速判断本周报价窗口。",
            "en": "Best for sales and leaders deciding this week’s quote window.",
        },
        "roles": ["sales", "executive", "general"],
        "priority": 88,
        "color": "from-violet-500 to-fuchsia-500",
    },
    {
        "id": "weekly-report",
        "icon_key": "report",
        "badge": {"zh": "生成周报", "en": "Weekly Report"},
        "prompt": {
            "zh": "请生成中国区本周C5/C9市场周报，严格基于我的数据库，涵盖12个品类的行情与定价建议。",
            "en": "Generate this week's China C5/C9 market weekly report strictly from my database, covering market conditions and pricing recommendations for all 12 categories.",
        },
        "reason": {
            "zh": "适合晨会、周会和管理层同步。",
            "en": "Useful for weekly reviews and leadership updates.",
        },
        "roles": ["executive", "sales", "finance", "general"],
        "priority": 82,
        "color": "from-emerald-500 to-teal-500",
    },
    {
        "id": "risk-alert",
        "icon_key": "risk",
        "badge": {"zh": "风险预警", "en": "Risk Alerts"},
        "prompt": {
            "zh": "中国乙烯/C5装置开停工变化对异戊二烯和间戊二烯库存及定价有什么影响？请给出应对建议。",
            "en": "How do China ethylene/C5 plant shutdown and restart changes affect isoprene and piperylene inventory and pricing? Please give operational recommendations.",
        },
        "reason": {
            "zh": "面向风险控制与供应波动判断。",
            "en": "Designed for risk control and supply-shock analysis.",
        },
        "roles": ["risk", "procurement", "executive", "general"],
        "priority": 86,
        "color": "from-amber-500 to-orange-500",
    },
    {
        "id": "competitor-benchmark",
        "icon_key": "benchmark",
        "badge": {"zh": "竞品对标", "en": "Competitor Benchmark"},
        "prompt": {
            "zh": "按中国区数据库口径，我的顺酐合剂C5树脂相比金海晨光当前价差是多少？本周是否需要调价？",
            "en": "Using the China regional database, what is the current price spread between my MTHPA-related C5 resin and Jinhai Chenguang, and should we adjust price this week?",
        },
        "reason": {
            "zh": "适合竞品价格追踪与调价判断。",
            "en": "Supports competitive tracking and pricing response.",
        },
        "roles": ["sales", "risk", "executive", "general"],
        "priority": 80,
        "color": "from-rose-500 to-pink-500",
    },
    {
        "id": "costing",
        "icon_key": "costing",
        "badge": {"zh": "成本核算", "en": "Costing"},
        "prompt": {
            "zh": "基于中国区成本口径和数据库价格，当前裂解C5成本底线是多少？甲基四氢苯酐价格下限如何计算？",
            "en": "Based on China cost assumptions and database prices, what is the current cost floor for Cracked C5 and how should the lower bound for MTHPA pricing be calculated?",
        },
        "reason": {
            "zh": "适合成本底线、利润空间和采购判断。",
            "en": "Useful for cost floor, margin, and sourcing decisions.",
        },
        "roles": ["finance", "procurement", "operations", "general"],
        "priority": 84,
        "color": "from-indigo-500 to-blue-600",
    },
    {
        "id": "procurement-window",
        "icon_key": "procurement",
        "badge": {"zh": "采购窗口", "en": "Buy Window"},
        "prompt": {
            "zh": "结合本周价格、开工率和库存变化，当前哪些原料适合提前锁量采购？哪些应继续观望？",
            "en": "Based on this week’s prices, operating rates, and inventory changes, which feedstocks should be secured early and which should stay on watch?",
        },
        "reason": {
            "zh": "适合采购团队判断提前锁量时点。",
            "en": "Helps sourcing teams judge when to lock volume early.",
        },
        "roles": ["procurement", "finance", "risk"],
        "priority": 83,
        "color": "from-cyan-500 to-sky-500",
    },
    {
        "id": "sales-coverage",
        "icon_key": "sales",
        "badge": {"zh": "销售动作", "en": "Sales Action"},
        "prompt": {
            "zh": "请按客户类型和区域拆解本周报价策略，告诉我哪些客户适合提价、哪些客户需要保量。",
            "en": "Break down this week’s quote strategy by customer type and region, and tell me which accounts should take a price increase versus volume protection.",
        },
        "reason": {
            "zh": "帮助销售团队把价格策略落到客户动作。",
            "en": "Turns pricing strategy into concrete account actions.",
        },
        "roles": ["sales", "executive"],
        "priority": 81,
        "color": "from-pink-500 to-rose-500",
    },
    {
        "id": "operations-impact",
        "icon_key": "operations",
        "badge": {"zh": "运营影响", "en": "Operations Impact"},
        "prompt": {
            "zh": "如果本周原料价格继续波动，我们的产销节奏、库存结构和排产优先级应该如何调整？",
            "en": "If feedstock prices keep moving this week, how should we adjust production rhythm, inventory mix, and scheduling priorities?",
        },
        "reason": {
            "zh": "适合生产与运营团队评估排产节奏。",
            "en": "Supports operations and production planning decisions.",
        },
        "roles": ["operations", "risk", "executive"],
        "priority": 79,
        "color": "from-slate-500 to-slate-700",
    },
]


def _resolve_recommendation_role(user: Dict[str, object]) -> str:
    if bool(user.get("is_admin")):
        return "executive"

    role_name = str(user.get("role_name") or "").lower()
    department_name = str(user.get("department_name") or "").lower()
    username = str(user.get("username") or "").lower()
    haystack = " ".join([role_name, department_name, username])

    if any(token in haystack for token in ["sales", "commercial", "account"]):
        return "sales"
    if any(
        token in haystack for token in ["risk", "market", "analyst", "intelligence"]
    ):
        return "risk"
    if any(token in haystack for token in ["procure", "sourcing", "buyer", "purchase"]):
        return "procurement"
    if any(token in haystack for token in ["finance", "cfo", "accounting"]):
        return "finance"
    if any(token in haystack for token in ["operation", "ops", "production", "plant"]):
        return "operations"
    return "general"


def _recommendation_role_label(role: str, language: str) -> str:
    labels = {
        "zh": {
            "executive": "管理层",
            "sales": "销售",
            "risk": "风控/市场",
            "procurement": "采购",
            "finance": "财务",
            "operations": "运营/生产",
            "general": "通用",
        },
        "en": {
            "executive": "Executive",
            "sales": "Sales",
            "risk": "Risk/Market",
            "procurement": "Procurement",
            "finance": "Finance",
            "operations": "Operations",
            "general": "General",
        },
    }
    return labels[language].get(role, labels[language]["general"])


def _build_alert_recommendation(alert: Dict[str, Any], language: str) -> Dict[str, Any]:
    percent = float(alert.get("change_7d_pct") or 0)
    pct_text = f"{percent:+.1f}%"
    is_spike = str(alert.get("type") or "") == "price_spike"
    product_name = str(alert.get("product_name") or alert.get("product") or "Market")
    return {
        "id": f"alert-{alert.get('id')}",
        "icon_key": "alert",
        "badge": "Market Alert" if language == "en" else "市场异动",
        "prompt": (
            f"{product_name} moved {pct_text} in 7 days. Explain the supply-demand driver, the pricing risk, and what action we should take this week."
            if language == "en"
            else f"{product_name} 7日变动 {pct_text}。请解释背后的供需驱动、价格风险，以及我们本周应该采取的动作。"
        ),
        "color": (
            "from-amber-500 to-orange-500"
            if is_spike
            else "from-emerald-500 to-teal-500"
        ),
        "reason": (
            f"Triggered by today's market movement in {product_name}."
            if language == "en"
            else f"根据今日 {product_name} 市场异动自动推荐。"
        ),
        "score": 220
        + (
            40
            if str(alert.get("severity")) == "high"
            else 20 if str(alert.get("severity")) == "medium" else 10
        ),
        "tags": [
            {
                "label": "Live market signal" if language == "en" else "实时市场信号",
                "tone": "alert",
            }
        ],
    }


def _build_role_recommendation_cards(
    user: Dict[str, object], language: str
) -> List[Dict[str, Any]]:
    role = _resolve_recommendation_role(user)
    role_label = _recommendation_role_label(role, language)
    base_cards: List[Dict[str, Any]] = []

    for item in _RECOMMENDATION_LIBRARY:
        role_match = (
            100 if role in item["roles"] else 40 if "general" in item["roles"] else 0
        )
        if role_match == 0:
            continue
        base_cards.append(
            {
                "id": item["id"],
                "icon_key": item["icon_key"],
                "badge": item["badge"][language],
                "prompt": item["prompt"][language],
                "color": item["color"],
                "reason": item["reason"][language],
                "score": int(item["priority"]) + role_match,
                "tags": [
                    {
                        "label": (
                            f"Recommended for {role_label}"
                            if language == "en"
                            else f"适合角色 {role_label}"
                        ),
                        "tone": "role",
                    }
                ],
            }
        )

    return base_cards


def _compact_text(value: str, limit: int = 120) -> str:
    collapsed = " ".join((value or "").split())
    if len(collapsed) <= limit:
        return collapsed
    return f"{collapsed[: max(0, limit - 3)].rstrip()}..."


def _build_recent_session_suggestions(
    user: Dict[str, object],
    language: str,
    limit: int = 3,
) -> List[Dict[str, Any]]:
    user_id = str(user.get("id") or "").strip()
    if not user_id:
        return []

    suggestions: List[Dict[str, Any]] = []
    sessions = db.get_sessions(user_id)[: max(0, limit)]
    role_label = _recommendation_role_label(
        _resolve_recommendation_role(user), language
    )

    for index, session in enumerate(sessions):
        session_id = str(session.get("id") or "")
        if not session_id:
            continue

        messages = db.get_messages(session_id)
        last_user_message = next(
            (
                message
                for message in reversed(messages)
                if str(message.get("role") or "") == "user"
                and str(message.get("content") or "").strip()
            ),
            None,
        )

        raw_title = str(
            session.get("title")
            or ("Recent session" if language == "en" else "最近会话")
        )
        title = _compact_text(raw_title, 60)
        focus = _compact_text(
            (
                str(last_user_message.get("content") or "")
                if last_user_message
                else raw_title
            ),
            120,
        )

        if language == "en":
            prompt = (
                f'Revisit my recent session "{title}". Refresh the answer with current alerts, '
                "the newest market changes, and the next action to take."
            )
            if focus:
                prompt = f"{prompt} Focus on: {focus}"
            reason = "Based on one of your most recent EDIA conversations."
            badge = "Recent Session"
            tags = [
                {"label": "Recent session", "tone": "history"},
                {"label": f"Recommended for {role_label}", "tone": "role"},
            ]
        else:
            prompt = f"延续我最近的会话“{title}”，结合当前预警、最新市场变化和下一步动作给出更新判断。"
            if focus:
                prompt = f"{prompt} 重点问题：{focus}"
            reason = "基于你最近在 EDIA 中的历史会话生成。"
            badge = "最近会话"
            tags = [
                {"label": "最近会话", "tone": "history"},
                {"label": f"适合角色 {role_label}", "tone": "role"},
            ]

        suggestions.append(
            {
                "id": f"session-{session_id}",
                "title": title,
                "icon_key": "history",
                "badge": badge,
                "prompt": prompt,
                "color": "from-slate-500 to-slate-700",
                "reason": reason,
                "score": 210 - (index * 12),
                "tags": tags,
                "source": "recent_session",
                "session_id": session_id,
            }
        )

    return suggestions


def _build_composer_suggestions(
    user: Dict[str, object],
    language: str,
    limit: int = 8,
) -> Dict[str, Any]:
    selected_limit = max(3, min(limit, 12))
    role = _resolve_recommendation_role(user)
    role_label = _recommendation_role_label(role, language)

    role_cards = _build_role_recommendation_cards(user, language)
    active_alerts = (alert_worker.get_alerts() or {}).get("alerts") or []
    alert_cards = [
        _build_alert_recommendation(alert, language) for alert in active_alerts[:2]
    ]
    session_cards = _build_recent_session_suggestions(user, language, limit=3)

    combined: List[Dict[str, Any]] = []
    for card in role_cards:
        combined.append(
            {
                **card,
                "title": str(card.get("badge") or "Suggested prompt"),
                "source": "role",
                "session_id": None,
            }
        )

    for card in alert_cards:
        combined.append(
            {
                **card,
                "title": str(card.get("badge") or "Market alert"),
                "source": "alert",
                "session_id": None,
            }
        )

    combined.extend(session_cards)

    ranked = sorted(
        combined, key=lambda item: int(item.get("score") or 0), reverse=True
    )
    seen_keys: set[str] = set()
    suggestions: List[Dict[str, Any]] = []
    for item in ranked:
        dedupe_key = (
            f"{item.get('source')}::{str(item.get('prompt') or '').strip().lower()}"
        )
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        suggestions.append(item)
        if len(suggestions) >= selected_limit:
            break

    return {
        "suggestions": suggestions,
        "role": role,
        "role_label": role_label,
        "counts": {
            "role": len(role_cards),
            "alerts": len(alert_cards),
            "recent_sessions": len(session_cards),
        },
    }


def _build_recommendation_cards(
    user: Dict[str, object], language: str
) -> List[Dict[str, Any]]:
    base_cards = _build_role_recommendation_cards(user, language)

    active_alerts = (alert_worker.get_alerts() or {}).get("alerts") or []
    alert_cards = [
        _build_alert_recommendation(alert, language) for alert in active_alerts[:2]
    ]
    combined = sorted(
        [*alert_cards, *base_cards], key=lambda item: int(item["score"]), reverse=True
    )
    return combined[:6]


def _issue_auth_session(user: Dict[str, object]) -> Dict[str, object]:
    expires_at = (
        (datetime.utcnow() + timedelta(hours=_auth_token_ttl_hours()))
        .replace(microsecond=0)
        .isoformat()
    )
    token = db.create_auth_token(str(user["id"]), expires_at)
    return {
        "token": token,
        "user": _public_user(user),
        "expires_at": expires_at,
    }


def _extract_bearer_token(request: Request) -> Optional[str]:
    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        token = authorization[len("Bearer ") :].strip()
        return token or None

    # EventSource does not allow custom headers; accept token via query string.
    query_token = (request.query_params.get("access_token") or "").strip()
    return query_token or None


def _require_current_user(request: Request) -> Dict[str, object]:
    current_user = getattr(request.state, "current_user", None)
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return current_user


def _require_admin(request: Request) -> Dict[str, object]:
    current_user = _require_current_user(request)
    if not current_user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


@app.middleware("http")
async def require_authenticated_api_user(request: Request, call_next):
    path = request.url.path
    if (
        request.method == "OPTIONS"
        or not path.startswith("/api")
        or path in AUTH_EXEMPT_PATHS
        or path.startswith("/api/reports-static/")  # chart PNGs are public static assets
        or path.startswith("/api/chat-charts/")  # Hermes chat chart PNGs
    ):
        return await call_next(request)

    token = _extract_bearer_token(request)
    if not token:
        return _apply_request_cors_headers(request, JSONResponse(
            status_code=401, content={"detail": "Authentication required"}
        ))

    try:
        current_user = db.get_user_by_token(token)
    except SQLAlchemyError as exc:
        logger.error("Auth token lookup failed due to database error", exc_info=True)
        return _apply_request_cors_headers(
            request,
            JSONResponse(status_code=503, content={"detail": "Authentication service unavailable"}),
        )

    if not current_user or not current_user.get("is_active"):
        return _apply_request_cors_headers(request, JSONResponse(
            status_code=401, content={"detail": "Invalid or expired session"}
        ))

    request.state.current_user = current_user
    request.state.auth_token = token

    if (
        current_user.get("must_change_password")
        and path not in PASSWORD_CHANGE_ALLOWED_PATHS
    ):
        return _apply_request_cors_headers(request, JSONResponse(
            status_code=403,
            content={"detail": "Password change required before continuing"},
        ))

    return await call_next(request)


@app.post("/api/auth/login")
async def login(request: LoginRequest):
    username = normalize_username(request.username)
    try:
        user = db.get_user_by_username(username)
    except SQLAlchemyError:
        logger.error("Login failed due to database error", exc_info=True)
        raise HTTPException(status_code=503, detail="Authentication service unavailable")

    if not user or not user.get("is_active"):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    if not verify_password(
        request.password,
        str(user["password_salt"]),
        str(user["password_hash"]),
    ):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    return _issue_auth_session(user)


@app.post("/api/auth/logout")
async def logout(request: Request):
    token = getattr(request.state, "auth_token", None)
    if token:
        db.revoke_auth_token(token)
    return {"status": "ok"}


@app.get("/api/auth/me")
async def get_current_user(request: Request):
    return _public_user(_require_current_user(request))


@app.post("/api/auth/change-password")
async def change_password(request: Request, payload: ChangePasswordRequest):
    current_user = _require_current_user(request)

    if payload.current_password == payload.new_password:
        raise HTTPException(
            status_code=400,
            detail="New password must be different from current password",
        )

    persisted_user = db.get_user_by_id(str(current_user["id"]))
    if not persisted_user:
        raise HTTPException(status_code=404, detail="User not found")

    if not verify_password(
        payload.current_password,
        str(persisted_user["password_salt"]),
        str(persisted_user["password_hash"]),
    ):
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    try:
        db.update_user_password(
            str(persisted_user["id"]),
            payload.new_password,
            must_change_password=False,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    db.revoke_all_auth_tokens_for_user(str(persisted_user["id"]))

    refreshed_user = db.get_user_by_id(str(persisted_user["id"]))
    if not refreshed_user:
        raise HTTPException(status_code=404, detail="User not found")

    return _issue_auth_session(refreshed_user)


@app.get("/api/auth/users")
async def list_users(request: Request):
    _require_admin(request)
    return db.list_users()


@app.post("/api/auth/users")
async def create_user(request: Request, payload: CreateUserRequest):
    admin_user = _require_admin(request)
    try:
        return db.create_user(
            username=payload.username,
            password=payload.password,
            is_admin=payload.is_admin,
            created_by=str(admin_user["username"]),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/auth/users/{user_id}/reset-password")
async def reset_user_password(
    user_id: str,
    request: Request,
    payload: ResetUserPasswordRequest,
):
    admin_user = _require_admin(request)
    target_user = db.get_user_by_id(user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    if str(target_user["id"]) == str(admin_user["id"]):
        raise HTTPException(
            status_code=400,
            detail="Use the Change Password action for your own account",
        )

    try:
        db.update_user_password(
            str(target_user["id"]),
            payload.new_password,
            must_change_password=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    db.revoke_all_auth_tokens_for_user(str(target_user["id"]))

    return {
        "status": "ok",
        "message": (
            f"Temporary password set for {target_user['username']}. "
            "The user must change it on next login."
        ),
        "user": _public_user(target_user),
        "reset_by": str(admin_user["username"]),
    }


@app.post("/api/auth/users/{user_id}/status")
async def update_user_status(
    user_id: str,
    request: Request,
    payload: UpdateUserStatusRequest,
):
    admin_user = _require_admin(request)
    target_user = db.get_user_by_id(user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    if str(target_user["id"]) == str(admin_user["id"]) and not payload.is_active:
        raise HTTPException(
            status_code=400, detail="You cannot deactivate your own account"
        )

    db.update_user_active_status(str(target_user["id"]), payload.is_active)
    if not payload.is_active:
        db.revoke_all_auth_tokens_for_user(str(target_user["id"]))

    updated_user = db.get_user_by_id(str(target_user["id"]))
    if not updated_user:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "status": "ok",
        "message": (
            f"User {updated_user['username']} {'activated' if payload.is_active else 'deactivated'}"
        ),
        "user": _public_user(updated_user),
    }


@app.get("/api/admin/access-model")
async def get_access_model(request: Request):
    _require_admin(request)
    return db.get_access_model_config()


@app.put("/api/admin/access-model")
async def save_access_model(request: Request, payload: AccessModelConfigPayload):
    admin_user = _require_admin(request)
    try:
        return db.save_access_model_config(
            payload.model_dump(),
            updated_by=str(admin_user["username"]),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/recommendations/cards")
async def get_recommendation_cards(request: Request, language: str = "zh"):
    current_user = _public_user(_require_current_user(request))
    selected_language = "en" if language == "en" else "zh"
    return {
        "cards": _build_recommendation_cards(current_user, selected_language),
        "role": _resolve_recommendation_role(current_user),
        "role_label": _recommendation_role_label(
            _resolve_recommendation_role(current_user), selected_language
        ),
    }


@app.get("/api/composer/suggestions", response_model=ComposerSuggestionsResponse)
async def get_composer_suggestions(
    request: Request,
    language: str = "zh",
    limit: int = 8,
):
    current_user = _public_user(_require_current_user(request))
    selected_language = "en" if language == "en" else "zh"
    return _build_composer_suggestions(current_user, selected_language, limit=limit)


@app.post("/api/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    http_request: Request,
):
    try:
        current_user = _require_current_user(http_request)
        user_id = str(current_user["id"])

        # 1. Manage Session
        session_id = request.session_id
        prior_messages = []
        if session_id:
            if not db.session_belongs_to_user(session_id, user_id):
                raise HTTPException(status_code=404, detail="Session not found")
            prior_messages = db.get_messages(session_id)
        else:
            # Create new session if not provided
            # Use first 20 chars of message as title
            title = (
                (request.message[:20] + "...")
                if len(request.message) > 20
                else request.message
            )
            session_id = db.create_session(user_id, title)

        effective_message = _build_effective_chat_message(
            request.message,
            request.attachment_contexts,
        )

        # 2. Save User Message
        db.add_message(session_id, "user", request.message)

        fast_response = _build_simple_greeting_fast_response(
            request.message,
            request.prefs,
        )
        if fast_response is not None:
            db.add_message(
                session_id,
                "assistant",
                fast_response["markdown"],
                context=fast_response["context"],
            )
            return ChatResponse(
                markdown=fast_response["markdown"],
                status=fast_response["status"],
                context=fast_response["context"],
                task_type=fast_response["task_type"],
                session_id=session_id,
                error_message=None,
                llm_route=fast_response["llm_route"],
                quality_flags=fast_response["quality_flags"],
                alerts_prepended=fast_response["alerts_prepended"],
                enhancement_applied=fast_response["enhancement_applied"],
                resolved_chat_mode=fast_response["resolved_chat_mode"],
                mode_reason=fast_response["mode_reason"],
            )

        # Weekly report intent detection — intercept before main pipeline
        if _is_weekly_report_chat_request(request.message):
            try:
                _wr_payload = await _generate_weekly_report_chat_payload(
                    user_id=user_id,
                    session_id=session_id,
                    language=_detect_response_language(request.message, request.prefs),
                    current_week=True,
                )
                _wr_final_md = str(_wr_payload.get("markdown") or "")
                db.add_message(
                    session_id,
                    "assistant",
                    _wr_final_md,
                    context=_wr_payload.get("context"),
                )
                return ChatResponse(
                    markdown=_wr_final_md,
                    status="success",
                    context=_wr_payload.get("context"),
                    task_type="weekly_report",
                    session_id=session_id,
                    error_message=None,
                    llm_route="external",
                )
            except Exception as _wr_err:
                logger.error("Weekly report from chat failed: %s", _wr_err, exc_info=True)
                # Fall through to normal pipeline with an error notice
                effective_message = request.message + f"\n\n[注：周报生成失败: {_wr_err}]"

        # 3. Process Request
        effective_prefs = dict(request.prefs or {})
        timeout_mode = _resolve_request_chat_mode(
            request.message,
            prefs=effective_prefs,
            request_chat_mode=request.chat_mode,
        )
        effective_prefs["chat_mode"] = timeout_mode
        effective_prefs["prefer_token_streaming"] = True
        effective_prefs = _apply_llm_route_defaults(effective_prefs, timeout_mode)
        hermes_plan_metadata = _build_hermes_plan_metadata(effective_message)
        if _is_heavy_llm_task(effective_message, hermes_plan_metadata):
            effective_prefs["graph_llm_route"] = "external"
            effective_prefs["use_external_graph_reasoning"] = True
        deterministic_market = _build_deterministic_market_chat_report(
            effective_message,
            effective_prefs,
        )
        if deterministic_market is not None:
            final_markdown = str(deterministic_market.get("markdown") or "")
            response_context = _build_response_context(
                report_context=deterministic_market.get("context") or {},
                message=request.message,
                task_type=str(deterministic_market.get("task_type") or "simple_lookup"),
                language=str((request.prefs or {}).get("language") or "zh"),
                raw_markdown=final_markdown,
                resolved_chat_mode=timeout_mode,
            )
            enhancement_applied = {
                "deterministic_market_lookup": "true",
                "skip_finalizer": "true",
            }
            response_context = _attach_llm_route_to_context(
                response_context,
                "deterministic",
            )
            response_context = _attach_response_quality_to_context(
                response_context,
                [],
                [],
                enhancement_applied,
            )
            db.add_message(
                session_id,
                "assistant",
                final_markdown,
                context=response_context,
            )
            return ChatResponse(
                markdown=final_markdown,
                status="success",
                context=response_context,
                task_type=str(deterministic_market.get("task_type") or "simple_lookup"),
                session_id=session_id,
                error_message=None,
                llm_route="deterministic",
                resolved_chat_mode=timeout_mode,
                mode_reason=None,
                quality_flags=[],
                alerts_prepended=[],
                enhancement_applied=enhancement_applied,
            )

        # ── User Memory Recall: inject relevant prior context ──────────────────
        user_memory_context = ""
        try:
            from backend.src.rag.user_memory_retriever import UserMemoryRetriever
            from backend.src.rag.vector_store import kb as _user_kb

            retriever = UserMemoryRetriever(_user_kb)
            user_memory_context = retriever.build_context_block(
                user_id=user_id,
                query=effective_message,
                top_k=5,
            )
        except Exception:
            pass  # Non-blocking: user memory is best-effort

        memory_enhanced_message = (
            (user_memory_context + "\n\n---\n\n" + effective_message)
            if user_memory_context
            else effective_message
        )

        report = await asyncio.to_thread(
            decision_service.process_request,
            memory_enhanced_message,
            conversation_history=prior_messages,
            user_prefs=effective_prefs,
            user_id=user_id,
            session_id=session_id,
            hermes_plan=hermes_plan_metadata,
        )

        final_markdown = str(report.raw_markdown or "")
        enhancement_applied = _apply_hermes_privacy_to_enhancement(
            report.enhancement_applied,
            hermes_plan_metadata,
        )
        finalizer_enabled = _final_kimi_hook_enabled(effective_prefs)
        finalizer_allowed = _finalizer_allowed_by_privacy(
            finalizer_enabled,
            report.enhancement_applied,
            hermes_plan_metadata,
        )
        if finalizer_allowed and report.status == "success" and final_markdown.strip():
            llm = getattr(decision_service, "llm", None)
            final_markdown = _finalize_with_kimi_sync(
                llm=llm,
                request_message=request.message,
                draft_markdown=final_markdown,
                task_type=report.task_type.value,
                resolved_chat_mode=str(report.resolved_chat_mode or ""),
            )

        response_context = _build_response_context(
            report_context=report.context,
            message=request.message,
            task_type=report.task_type.value,
            language=str((request.prefs or {}).get("language") or "zh"),
            raw_markdown=final_markdown,
            resolved_chat_mode=str(report.resolved_chat_mode or ""),
        )
        llm_route = _resolve_response_llm_route(
            task_type=report.task_type.value,
            enhancement_applied=enhancement_applied,
            prefs=effective_prefs,
        )
        if finalizer_allowed and report.status == "success":
            llm_route = "external"
        response_context = _attach_llm_route_to_context(response_context, llm_route)
        response_context = _attach_llm_branching_to_context(
            response_context,
            enhancement_applied,
        )
        response_context = _attach_planner_sources_to_context(
            response_context,
            enhancement_applied,
        )
        response_context = _attach_mixed_timings_to_context(
            response_context,
            enhancement_applied,
        )
        quality_flags_payload = [
            {
                "code": f.code,
                "severity": f.severity,
                "message": f.message,
                "source": f.source,
            }
            for f in report.quality_flags
        ]
        response_context = _attach_response_quality_to_context(
            response_context,
            quality_flags_payload,
            report.alerts_prepended,
            enhancement_applied,
        )

        # 4. Save AI Response
        if report.status == "success":
            db.add_message(
                session_id,
                "assistant",
                final_markdown,
                context=response_context,
            )

        confidence_payload = _build_confidence_payload(report)
        suggestions_payload = _extract_suggestions_from_markdown(
            final_markdown, language=str((request.prefs or {}).get("language") or "zh")
        )
        if confidence_payload:
            response_context["confidence"] = confidence_payload

        return ChatResponse(
            markdown=final_markdown,
            status=report.status,
            context=response_context,
            task_type=report.task_type.value,
            session_id=session_id,
            error_message=report.error_message,
            llm_route=llm_route,
            resolved_chat_mode=report.resolved_chat_mode,
            mode_reason=report.mode_reason,
            quality_flags=quality_flags_payload,
            alerts_prepended=report.alerts_prepended,
            enhancement_applied=enhancement_applied,
            confidence=confidence_payload,
            suggestions=suggestions_payload,
            citations=list(getattr(report, "citations", []) or []),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _sse(event: str, data: dict) -> bytes:
    import json as _json

    payload = _json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


def _sse_comment(message: str = "keep-alive") -> bytes:
    return f": {message}\n\n".encode("utf-8")


def _chat_stream_keepalive_seconds() -> float:
    try:
        target = float(os.getenv("CHAT_STREAM_KEEPALIVE_SECONDS", "10") or "10")
    except Exception:
        target = 10.0
    return max(2.0, min(target, 60.0))


def _chat_stream_total_budget_seconds() -> float:
    try:
        target = float(os.getenv("CHAT_STREAM_TOTAL_BUDGET_SECONDS", "270") or "270")
    except Exception:
        target = 270.0
    return max(30.0, min(target, 600.0))


def _pick_stream_display_markdown(raw_markdown: str, final_markdown: str) -> str:
    raw = str(raw_markdown or "")
    final = str(final_markdown or "")
    if final.strip():
        raw_stripped = raw.strip()
        final_stripped = final.strip()
        if raw_stripped:
            raw_len = len(raw_stripped)
            final_len = len(final_stripped)
            final_is_erp_availability = (
                "ERP数据可用性" in final_stripped
                or "ERP Data Availability" in final_stripped
            )
            raw_has_structured_sections = any(
                marker in raw_stripped
                for marker in ("## 01", "## 02", "## 03", "## 04", "## 05", "## 06")
            )
            raw_mentions_availability_facts = any(
                marker in raw_stripped
                for marker in (
                    "可用ERP销售历史范围",
                    "最接近的可用",
                    "AVAILABLE ERP",
                    "CLOSEST AVAILABLE",
                )
            )

            # Keep detailed streamed analysis when postprocess collapses it into
            # a short ERP availability-only block.
            if (
                final_is_erp_availability
                and raw_has_structured_sections
                and raw_mentions_availability_facts
                and final_len < int(raw_len * 0.55)
            ):
                return raw
        return final
    return raw


def _ensure_non_empty_markdown(
    markdown: str,
    prep_state: Optional[Dict[str, Any]],
    request_message: str,
) -> str:
    text = dedupe_fast_preview_markdown(str(markdown or ""))
    if text.strip():
        return text

    stream_meta = prep_state.get("_stream") if isinstance(prep_state, dict) else None
    if isinstance(stream_meta, dict) and stream_meta.get("fast_preview_sent"):
        lang = _detect_response_language(str(request_message or ""), {})
        if lang == "zh":
            return "正在生成回复。当前结果为空，请重试或补充更具体的产品与时间范围。"
        return "Generating response. The current result was empty; please retry or provide a more specific product and time window."

    preview = _build_fast_start_preview(dict(prep_state or {}), request_message)
    if str(preview or "").strip():
        return str(preview)

    lang = _detect_response_language(str(request_message or ""), {})
    if lang == "zh":
        return "正在生成回复。当前结果为空，请重试或补充更具体的产品与时间范围。"
    return "Generating response. The current result was empty; please retry or provide a more specific product and time window."


def _local_fast_start_target_seconds() -> float:
    try:
        target = float(os.getenv("LOCAL_LLM_FAST_START_SECONDS", "6") or "6")
    except Exception:
        target = 6.0
    # Keep fast preview latency bounded even if env is configured too high.
    return max(1.0, min(target, 8.0))


def _chat_stream_prepare_timeout_seconds() -> float:
    try:
        target = float(os.getenv("CHAT_STREAM_PREP_TIMEOUT_SECONDS", "75") or "75")
    except Exception:
        target = 75.0
    # Honor configured prep warning threshold (bounded to avoid unbounded waits).
    return max(20.0, min(target, 180.0))


def _chat_stream_prepare_hard_timeout_seconds() -> float:
    try:
        target = float(os.getenv("CHAT_STREAM_PREP_HARD_TIMEOUT_SECONDS", "300") or "300")
    except Exception:
        target = 300.0
    return max(60.0, min(target, 900.0))


def _chat_stream_prepare_force_synthesis_seconds() -> float:
    try:
        target = float(os.getenv("CHAT_STREAM_PREP_FORCE_SYNTHESIS_SECONDS", "45") or "45")
    except Exception:
        target = 45.0
    return max(20.0, min(target, 600.0))


def _iter_stream_chunks(text: str, max_chars: int = 70):
    body = str(text or "")
    if not body:
        return
    if len(body) <= max_chars:
        yield body
        return

    start = 0
    n = len(body)
    while start < n:
        end = min(n, start + max_chars)
        if end < n:
            probe = body[start:end]
            cut = max(
                probe.rfind("\n"),
                probe.rfind(". "),
                probe.rfind("。"),
                probe.rfind("; "),
                probe.rfind("；"),
                probe.rfind(", "),
                probe.rfind("，"),
            )
            if cut >= max_chars // 2:
                end = start + cut + 1
        piece = body[start:end]
        if piece:
            yield piece
        start = end


def _build_thinking_stage_label(prep_state: Dict[str, Any], request_message: str) -> str:
    lang = _detect_response_language(str(request_message or ""), {})
    user_context = prep_state.get("user_context")
    parsed = prep_state.get("parsed_intent")
    task_type = str(getattr(getattr(parsed, "task_type", None), "value", "") or "").strip()
    mode = str(prep_state.get("resolved_chat_mode") or "").strip().lower()
    product = str(getattr(user_context, "product", "") or "").strip()
    period = str(getattr(user_context, "period", "") or "").strip()

    if lang == "zh":
        parts: List[str] = ["思考中：已识别需求"]
        if product:
            parts.append(f"产品={product}")
        if period:
            parts.append(f"周期={period}")
        return " | ".join(parts)

    parts_en: List[str] = ["Thinking: request understood"]
    if mode:
        parts_en.append(f"mode={mode}")
    if task_type:
        parts_en.append(f"task={task_type}")
    if product:
        parts_en.append(f"product={product}")
    if period:
        parts_en.append(f"window={period}")
    return " | ".join(parts_en)


def _build_fast_start_preview(prep_state: Dict[str, Any], request_message: str) -> str:
    return _hermes_build_fast_start_preview(prep_state, request_message)


def _looks_like_mysql_engineering_query(message: str) -> bool:
    text = str(message or "").strip().lower()
    if not text:
        return False
    markers = (
        "explain",
        "slow query",
        "index",
        "indexes",
        "schema",
        "migration",
        "innodb",
        "deadlock",
        "索引",
        "慢查询",
        "执行计划",
        "表结构",
    )
    return any(marker in text for marker in markers)


_WEEKLY_REPORT_CHAT_KEYWORDS = (
    "周报",
    "weekly report",
    "c5c9 report",
    "generate weekly",
    "生成周报",
    "本周报告",
    "weekly summary",
    "market weekly",
)


def _is_weekly_report_chat_request(message: str) -> bool:
    """True when the user wants the C5/C9 market weekly report (not ERP sales)."""
    msg = str(message or "").strip()
    if not msg:
        return False
    try:
        from backend.src.engine.decision_graph import _looks_like_weekly_report_request

        if _looks_like_weekly_report_request(msg):
            return True
    except Exception:
        logger.debug("weekly report intent check failed", exc_info=True)
    lower = msg.lower()
    return any(kw in lower for kw in _WEEKLY_REPORT_CHAT_KEYWORDS)


def _should_bypass_erp_fast_path(
    message: str,
    hermes_plan: Optional[Dict[str, Any]] = None,
) -> bool:
    from backend.src.hermes.erp_grounding import should_bypass_erp_fast_path

    return should_bypass_erp_fast_path(message, hermes_plan)


# ---------------------------------------------------------------------------
# Weekly chart payload → ChatVisualization converter
# ---------------------------------------------------------------------------

def _chart_section_to_visualization(
    section: Dict[str, Any],
    product_slug: str,
    product_name: str,
    week_end: str,
) -> Dict[str, Any]:
    """
    Convert a yearly/monthly chart section from weekly_report_data.py
    serialize_product_chart_payload() format into a ChatVisualization dict.

    Mapping:
      - section.points  → visualization.data
      - section.series  → visualization.series
      - section.title   → visualization.title
      - section.unit    → visualization.unit
      - "label" (from points) → x_key
      - kind: "multi_axis_composed" if any series has axis:"right",
              else "multi_line"
    """
    series_out = []
    has_right_axis = False
    for s in (section.get("series") or []):
        axis = s.get("axis", "left")
        if axis in ("right", "farRight"):
            has_right_axis = True
        series_out.append({
            "key": s.get("key", ""),
            "label": s.get("label", ""),
            "color": s.get("color", "#2563eb"),
            "area_opacity": s.get("area_opacity"),
            "chart_type": s.get("chart_type"),
            "axis": axis if axis != "left" else None,
            "unit": s.get("unit"),
        })

    # Build data rows from points, using "label" as x axis value
    data_rows = []
    for pt in (section.get("points") or []):
        row: Dict[str, Any] = {"label": pt.get("label", pt.get("period", ""))}
        for s in (section.get("series") or []):
            row[s.get("key", "")] = pt.get(s.get("key", ""))
        data_rows.append(row)

    kind = "multi_axis_composed" if has_right_axis else "multi_line"

    viz: Dict[str, Any] = {
        "kind": kind,
        "title": section.get("title", ""),
        "product_id": product_slug,
        "unit": section.get("unit"),
        "x_key": "label",
        "series": series_out,
        "data": data_rows,
    }

    # Attach forecast band summary if present (for yearly charts)
    fb = section.get("forecast_band")
    if fb and fb.get("low") is not None and fb.get("high") is not None:
        viz["summary"] = {
            "forecast_low": fb["low"],
            "forecast_high": fb["high"],
            "forecast_period": fb.get("period"),
            "confidence_label": fb.get("confidence_label"),
        }

    return viz


def _build_visualizations_from_chart_payload(
    chart_payload: Dict[str, Any],
    week_end: str,
) -> List[Dict[str, Any]]:
    """
    Convert the full weekly_chart_payload.products dict (product slug → yearly+monthly sections)
    into a flat list of ChatVisualization objects for the frontend.
    """
    visualizations = []
    products = chart_payload.get("products") or {}
    for slug, payload in products.items():
        product_name_zh = payload.get("product_name_zh", slug)
        product_name_en = payload.get("product_name_en", slug)
        product_name = product_name_en  # will be overridden by frontend i18n if needed

        for chart_type in ("yearly", "monthly"):
            section = payload.get(chart_type)
            if not section:
                continue
            viz = _chart_section_to_visualization(section, slug, product_name, week_end)
            visualizations.append(viz)

    return visualizations


# ---------------------------------------------------------------------------
# Weekly report chat payload
# ---------------------------------------------------------------------------

async def _generate_weekly_report_chat_payload(
    *,
    user_id: str,
    session_id: str,
    language: str = "zh",
    progress_callback: Optional[Any] = None,
    current_week: bool = False,
) -> Dict[str, Any]:
    """Build market weekly report from MySQL market data (not ERP sales tables)."""
    from backend.src.db.market_db import market_db as _market_db_inst
    from backend.src.service.weekly_report_generator import generate_weekly_report

    _wr_llm = getattr(decision_service, "llm", real_llm)
    _wr_result = await generate_weekly_report(
        db=db,
        mysql_engine=_market_db_inst.engine,
        llm=_wr_llm,
        user_id=user_id,
        session_id=session_id,
        static_base_url="/api/reports-static",
        progress_callback=progress_callback,
        current_week=current_week,
    )
    is_en = str(language or "zh").strip().lower() in {"en", "english"}
    _wr_markdown = str(_wr_result.get("markdown") or "")
    products_count = int(_wr_result.get("products_count") or 0)
    if is_en:
        _wr_banner = (
            f"\n\n---\n> 📊 **Report generated** ({products_count} products). "
            "Open **Reports > Weekly** for the full report.\n"
        )
    else:
        _wr_banner = (
            f"\n\n---\n> 📊 **报告已生成**（{products_count} 个产品）。"
            "前往 **「报告 > 周报」** 标签页查看完整报告。\n"
        )

    # Convert structured chart payloads → ChatVisualization objects and strip
    # static markdown image refs so only interactive cards are rendered.
    _wr_markdown = re.sub(
        r'\n?!\[([^\]]*)\]\(/api/reports-static/[^\)]+\)\n?',
        "",
        _wr_markdown,
    )

    visualizations: List[Dict[str, Any]] = []
    chart_payload = _wr_result.get("weekly_chart_payload") or {}
    if chart_payload.get("products"):
        visualizations = _build_visualizations_from_chart_payload(
            chart_payload, _wr_result.get("week_end", "")
        )

    # Action chips for post-report workflow
    action_chips = [
        {"label": "生成 PPTX" if not is_en else "Generate PPTX", "skill_id": "pptx-maker", "intent": "generate_pptx"},
        {"label": "聚焦 异戊二烯" if not is_en else "Focus Isoprene", "skill_id": "ecisco-weekly-report", "intent": "focus_product", "product_id": "isoprene"},
        {"label": "添加 ERP 销售数据" if not is_en else "Add ERP Sales", "skill_id": "erp-customer-sales", "intent": "add_erp_sales"},
        {"label": "本周 vs 上周对比" if not is_en else "WoW Comparison", "skill_id": "ecisco-weekly-report", "intent": "week_over_week"},
    ]

    return {
        "markdown": _wr_markdown + _wr_banner,
        "status": "success",
        "context": {
            "weekly_report_id": _wr_result.get("report_id"),
            "week_start": _wr_result.get("week_start"),
            "week_end": _wr_result.get("week_end"),
            "visualizations": visualizations,
            "action_chips": action_chips,
            "_stream": {"display_markdown_source": "weekly_report_generator"},
        },
        "task_type": "weekly_report",
        "llm_route": "external",
        "resolved_chat_mode": "analysis",
        "mode_reason": "weekly_report_generator",
        "enhancement_applied": {"weekly_report": "true"},
        "quality_flags": [],
        "alerts_prepended": False,
    }


def _is_deterministic_erp_stream_request(message: str, hermes_plan: Optional[Dict[str, Any]] = None) -> bool:
    if _should_bypass_erp_fast_path(message, hermes_plan):
        return False
    try:
        from backend.src.routing.query_router import is_deterministic_erp_stream, route_query

        resolved_hermes_plan = hermes_plan if isinstance(hermes_plan, dict) else None
        if resolved_hermes_plan is None:
            resolved_hermes_plan = build_chat_route_plan(message).to_dict()
        early_domains = {
            str(domain or "").strip().lower()
            for domain in (resolved_hermes_plan.get("domains") or [])
        }
        early_skills = {
            str(skill or "").strip().lower()
            for skill in (resolved_hermes_plan.get("agent_skills") or [])
        }
        if early_domains & {"document", "presentation", "frontend"}:
            return False
        export_skills = {
            "docx",
            "minimax-docx",
            "presentation-creator",
            "html-ppt",
            "pptx-generator",
            "frontend-design",
            "frontend-dev",
        }
        if early_skills & export_skills:
            return False
        erp_backed = bool(early_domains & {"erp", "database"})
        if early_skills & {"mysql"} and _looks_like_mysql_engineering_query(message):
            from backend.src.engine.decision_graph import (
                _looks_like_erp_revenue_query,
                _looks_like_erp_sales_detail_query,
                _looks_like_erp_sales_order_detail_query,
            )

            if not (
                _looks_like_erp_sales_order_detail_query(message)
                or _looks_like_erp_sales_detail_query(message)
                or _looks_like_erp_revenue_query(message)
            ):
                return False
        if not erp_backed and early_skills & {
            "markdown-converter",
            "mysql",
            "vision-analysis",
        }:
            return False
        routed = route_query(
            message,
            hermes_domains=resolved_hermes_plan.get("domains"),
            capabilities=resolved_hermes_plan.get("capabilities"),
        )
        if routed.path == "deterministic_market":
            return False
        if is_deterministic_erp_stream(routed):
            return True

        from backend.src.engine.decision_graph import (
            _is_commercial_ops_advisory_query,
            _looks_like_erp_revenue_query,
            _looks_like_erp_row_level_detail_query,
            _looks_like_erp_sales_order_detail_query,
            _looks_like_erp_sales_detail_query,
            _looks_like_other_products_excluding_sales_query,
        )
        from backend.src.tools.db_capability_registry import (
            resolve_database_capability,
            should_bypass_database_capability_discovery,
            should_use_database_capability_discovery,
        )
        from backend.src.nlu.intent_parser import _fast_intent

        resolved_hermes_plan = hermes_plan if isinstance(hermes_plan, dict) else build_chat_route_plan(message).to_dict()
        hermes_domains = set(resolved_hermes_plan.get("domains") or [])
        hermes_agent_skills = set(resolved_hermes_plan.get("agent_skills") or [])
        hermes_capabilities = set(resolved_hermes_plan.get("capabilities") or [])
        task_type, _confidence = _fast_intent(str(message or ""))

        if should_bypass_database_capability_discovery(
            message,
            task_type=task_type,
            hermes_domains=hermes_domains,
            capabilities=hermes_capabilities,
        ):
            return False
        if hermes_domains & {"document", "presentation", "frontend"}:
            return False
        if "forecast" in hermes_domains and not _looks_like_erp_sales_order_detail_query(message):
            if not should_use_database_capability_discovery(
                message,
                task_type=task_type,
                hermes_domains=hermes_domains,
                capabilities=hermes_capabilities,
            ):
                return False
        if hermes_agent_skills & {"markdown-converter", "mysql", "vision-analysis", "frontend-dev"}:
            return False
        if "commercial_ops_advisory" in hermes_capabilities:
            return False
        if _is_commercial_ops_advisory_query(message):
            return False

        capability = resolve_database_capability(message)
        if capability is not None:
            return True
        if should_use_database_capability_discovery(
            message,
            task_type=task_type,
            hermes_domains=hermes_domains,
            capabilities=hermes_capabilities,
        ):
            return True
        from backend.src.engine.decision_graph import _looks_like_erp_report_query

        if (
            _looks_like_erp_sales_order_detail_query(message)
            or _looks_like_erp_sales_detail_query(message)
            or _looks_like_erp_row_level_detail_query(message)
            or _looks_like_erp_revenue_query(message)
            or _looks_like_erp_report_query(message)
            or _looks_like_other_products_excluding_sales_query(message)
        ):
            return True
        if str(getattr(task_type, "value", task_type)) == "erp_data_query":
            return True
        return False
    except Exception:
        return False


def _deterministic_erp_stream_enhancements(message: str) -> Dict[str, str]:
    applied: Dict[str, str] = {}
    try:
        from backend.src.routing.query_router import route_query, stream_enhancements_for_route

        routed = route_query(message)
        applied.update(stream_enhancements_for_route(routed))
        if routed.path == "deterministic_market":
            return applied

        from backend.src.tools.db_capability_registry import (
            capability_privacy_metadata,
            resolve_database_capability,
            should_use_database_capability_discovery,
        )
        from backend.src.nlu.intent_parser import _fast_intent
        from backend.src.engine.decision_graph import (
            _is_commercial_ops_advisory_query,
            _looks_like_erp_report_query,
            _looks_like_erp_revenue_query,
            _looks_like_erp_row_level_detail_query,
            _looks_like_erp_sales_order_detail_query,
            _looks_like_erp_sales_detail_query,
            _looks_like_other_products_excluding_sales_query,
        )

        capability = resolve_database_capability(message)
        task_type, _confidence = _fast_intent(str(message or ""))
        applied.update(capability_privacy_metadata(capability))
        if _is_commercial_ops_advisory_query(message):
            applied.update(
                {
                    "hybrid_ops_advisory": "true",
                    "answer_mode": "hybrid_commercial_ops_advisory",
                    "data_sensitivity": "market_internal_mixed",
                    "external_llm_blocked": "false",
                    "final_llm_route": "external",
                    "llm_synthesis": "facts_first",
                }
            )
        elif _looks_like_erp_sales_order_detail_query(message):
            applied["deterministic_erp_order_details"] = "true"
        elif capability is not None and capability.capability_id == "erp_customer_sales":
            applied["deterministic_erp_customer_sales"] = "true"
        elif capability is not None and capability.capability_id == "erp_sales_summary":
            applied["deterministic_erp_sales"] = "true"
        elif should_use_database_capability_discovery(message, task_type=task_type):
            applied.update(
                {
                    "database_capability_discovery": "true",
                    "data_sensitivity": "internal_metadata",
                    "answer_mode": "capability_discovery",
                    "external_llm_blocked": "true",
                }
            )
        elif _looks_like_erp_row_level_detail_query(message):
            applied["deterministic_erp_row_level_guard"] = "true"
        elif (
            _looks_like_erp_sales_detail_query(message)
            or _looks_like_erp_revenue_query(message)
            or _looks_like_erp_report_query(message)
        ):
            applied["deterministic_erp_sales"] = "true"
            applied["database_capability"] = "erp_sales_summary"
        elif _looks_like_other_products_excluding_sales_query(message):
            applied["deterministic_erp_exclusion_sales"] = "true"
    except Exception:
        pass
    return applied


_HEAVY_TASK_MARKERS_ZH = ("预测", "预报", "走势", "趋势", "周报", "weekly report", "深度研究", "情景分析")
_HEAVY_TASK_MARKERS_EN = ("forecast", "prediction", "outlook", "trend", "weekly report", "deep research", "scenario analysis")


def _is_heavy_llm_task(message: str, hermes_plan: Optional[Dict[str, Any]] = None) -> bool:
    """Detect tasks that should route to external LLM for acceptable latency."""
    q = str(message or "").strip().lower()
    if any(m in q for m in _HEAVY_TASK_MARKERS_ZH) or any(m in q for m in _HEAVY_TASK_MARKERS_EN):
        return True
    plan = hermes_plan or {}
    domains = [str(d).lower() for d in (plan.get("domains") or []) if d]
    if "forecast" in domains:
        return True
    if len(domains) > 2:
        return True
    skills = [str(s).lower() for s in (plan.get("agent_skills") or []) if s]
    if any(s in skills for s in ("ecisco-weekly-report", "forecast-run", "edia-market-research")):
        return True
    task_type = str(plan.get("task_type") or "").lower()
    if task_type in ("forecast_only", "boss_briefing"):
        return True
    return False


_PRODUCT_KEYWORDS = (
    "c5", "c9", "c5/c9", "c5-c9", "苯", "benzene", "甲苯", "toluene",
    "二甲苯", "xylene", "乙烯", "ethylene", "丙烯", "propylene",
    "丁二烯", "butadiene", "纯苯", "混合芳烃", "mx", "px", "ox",
)

_DOMAIN_KEYWORDS = (
    "forecast", "预测", "price", "价格", "inventory", "库存",
    "supply", "供应", "demand", "需求", "margin", "利润",
    "erp", "sales", "销售", "purchase", "采购", "trade", "贸易",
)


def _extract_memory_facts(
    user_message: str,
    task_type: str,
    hermes_plan: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Lightweight, rule-based extraction of session memory facts.

    Returns a list of dicts with keys: memory_type, content, confidence.
    """
    msg = str(user_message or "").lower().strip()
    if not msg:
        return []

    facts: List[Dict[str, Any]] = []
    plan = hermes_plan or {}

    # 1. Product interest
    products_found = [p for p in _PRODUCT_KEYWORDS if p in msg]
    if products_found:
        facts.append({
            "memory_type": "preference",
            "content": f"User queried about products: {', '.join(products_found[:5])}",
            "confidence": 0.85,
        })
    elif plan.get("products"):
        facts.append({
            "memory_type": "preference",
            "content": f"User queried about products: {', '.join(str(p) for p in plan['products'][:5])}",
            "confidence": 0.75,
        })

    # 2. Domain / task pattern
    domains_found = [d for d in _DOMAIN_KEYWORDS if d in msg]
    if domains_found:
        facts.append({
            "memory_type": "pattern",
            "content": f"User showed interest in domains: {', '.join(domains_found[:5])}",
            "confidence": 0.8,
        })
    elif plan.get("domains"):
        facts.append({
            "memory_type": "pattern",
            "content": f"User showed interest in domains: {', '.join(str(d) for d in plan['domains'][:5])}",
            "confidence": 0.7,
        })

    # 3. Task type memory
    if task_type and task_type not in ("unknown", "general_chat"):
        facts.append({
            "memory_type": "pattern",
            "content": f"User engaged in {task_type} tasks",
            "confidence": 0.75,
        })

    # 4. Language preference (if explicitly stated)
    if "english" in msg or "英文" in msg:
        facts.append({
            "memory_type": "preference",
            "content": "User prefers English responses",
            "confidence": 0.9,
        })
    if "chinese" in msg or "中文" in msg:
        facts.append({
            "memory_type": "preference",
            "content": "User prefers Chinese responses",
            "confidence": 0.9,
        })

    # 5. Report cadence preference
    if any(k in msg for k in ("weekly report", "周报", "daily report", "日报")):
        facts.append({
            "memory_type": "pattern",
            "content": "User requests regular reports",
            "confidence": 0.8,
        })

    return facts


def _write_session_memory(
    user_id: str,
    session_id: str,
    user_message: str,
    task_type: str,
    hermes_plan: Optional[Dict[str, Any]] = None,
) -> None:
    """Fire-and-forget write of lightweight session memory to ChromaDB.

    Runs after every successful chat turn so the next turn can recall it
    immediately — no need to wait for the 30-min background profiler.
    """
    try:
        facts = _extract_memory_facts(user_message, task_type, hermes_plan)
        if not facts:
            return
        from backend.src.rag.vector_store import kb as _kb
        from datetime import datetime, timezone

        now_iso = datetime.now(timezone.utc).isoformat()
        documents: List[str] = []
        metadatas: List[Dict[str, Any]] = []
        ids: List[str] = []
        for fact in facts:
            doc_id = (
                f"sess_mem_{user_id}_{session_id}_{fact['memory_type']}_{now_iso[:13]}"
            ).replace(":", "-").replace("+", "-")
            documents.append(fact["content"])
            metadatas.append({
                "user_id": str(user_id)[:64],
                "session_id": str(session_id),
                "memory_type": fact["memory_type"],
                "confidence": round(float(fact["confidence"]), 4),
                "extracted_at": now_iso,
            })
            ids.append(doc_id)
        if documents:
            _kb.upsert_user_memory(documents=documents, metadatas=metadatas, ids=ids)
    except Exception:
        pass


def _apply_llm_route_defaults(
    prefs: Dict[str, object],
    request_chat_mode: Optional[str] = None,
) -> Dict[str, object]:
    resolved = dict(prefs or {})
    local_disabled = str(os.getenv("DISABLE_LOCAL_LLM", "false")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    if local_disabled:
        resolved["llm_route"] = "external"
        resolved["final_decision_llm"] = "external"
        resolved["use_external_reasoning"] = True
        return resolved

    if _strict_kimi_only_mode():
        resolved["llm_route"] = "external"
        resolved["final_decision_llm"] = "external"
        resolved["use_external_reasoning"] = True
        resolved["external_payload_sanitized"] = True
        if not local_disabled and _env_flag("CHAT_STREAM_PREP_LOCAL_GRAPH", "true"):
            resolved.setdefault("graph_llm_route", "local")
        resolved.setdefault("kimi_mixed_synthesis", False)
        resolved.setdefault("planner_streaming_synthesis", True)
        resolved.setdefault("final_kimi_hook", False)
        return resolved

    mode = str(request_chat_mode or resolved.get("chat_mode") or "").strip().lower()

    if mode in {"analysis", "report", "deep_research"}:
        resolved.setdefault("kimi_mixed_synthesis", True)
        resolved.setdefault("external_first_online", False)
        resolved.setdefault("planner_streaming_synthesis", False)
        resolved.setdefault("use_external_reasoning", True)
        resolved.setdefault("llm_route", "external")
        resolved.setdefault("final_decision_llm", "external")

    explicit = str(resolved.get("llm_route") or "").strip().lower()
    if explicit in {"local", "external"}:
        return resolved

    if bool(resolved.get("use_external_reasoning", False)):
        resolved["llm_route"] = "external"
        return resolved

    default_external = _env_flag("CHAT_EXTERNAL_REASONING_DEFAULT", "false")
    if default_external and mode in {"analysis", "report", "deep_research"}:
        resolved["llm_route"] = "external"

    return resolved


def _resolve_final_llm_route_hint(prefs: Dict[str, object]) -> str:
    local_disabled = str(os.getenv("DISABLE_LOCAL_LLM", "false")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if local_disabled:
        return "external"

    explicit_final = str((prefs or {}).get("final_decision_llm") or "").strip().lower()
    if explicit_final in {"local", "external"}:
        return explicit_final

    explicit_route = str((prefs or {}).get("llm_route") or "").strip().lower()
    if explicit_route in {"local", "external"}:
        return explicit_route

    if bool((prefs or {}).get("use_external_reasoning", False)):
        return "external"

    mode = str((prefs or {}).get("chat_mode") or "").strip().lower()
    if mode in {"analysis", "report", "deep_research"}:
        return "local"

    # Keep conversational safety fallback unchanged unless explicitly overridden.
    return "local"


def _final_kimi_hook_enabled(prefs: Optional[Dict[str, object]] = None) -> bool:
    if _strict_kimi_only_mode():
        return True
    effective_prefs = dict(prefs or {})
    if "final_kimi_hook" in effective_prefs:
        return bool(effective_prefs.get("final_kimi_hook"))
    return _env_flag("FINAL_KIMI_HOOK", "false")


def _build_final_kimi_prompts(
    request_message: str,
    draft_markdown: str,
    task_type: str,
    resolved_chat_mode: str,
) -> tuple[str, str]:
    system_prompt = (
        "You are EDIA final response engine running on DeepSeek. "
        "Rewrite the draft into the final user-facing answer.\n\n"
        "RULES:\n"
        "- Keep all factual values from the draft; do not invent missing data.\n"
        "- If evidence is missing, say so explicitly and ask for the smallest needed clarification.\n"
        "- Keep the same language as the user query.\n"
        "- Return only the final answer markdown, no meta commentary."
    )
    user_prompt = (
        f"USER_QUERY:\n{str(request_message or '').strip()}\n\n"
        f"TASK_TYPE: {str(task_type or '').strip()}\n"
        f"CHAT_MODE: {str(resolved_chat_mode or '').strip()}\n\n"
        "DRAFT_RESPONSE:\n"
        f"{str(draft_markdown or '').strip()}\n\n"
        "Produce the final response now."
    )
    return system_prompt, user_prompt


def _finalize_with_kimi_sync(
    llm: Any,
    request_message: str,
    draft_markdown: str,
    task_type: str,
    resolved_chat_mode: str,
) -> str:
    source = str(draft_markdown or "")
    if not source.strip():
        raise RuntimeError("External finalizer requires non-empty draft markdown")

    if not llm or not hasattr(llm, "generate_completion"):
        raise RuntimeError("LLM service not configured for external finalizer")

    system_prompt, user_prompt = _build_final_kimi_prompts(
        request_message=request_message,
        draft_markdown=source,
        task_type=task_type,
        resolved_chat_mode=resolved_chat_mode,
    )
    final_text = llm.generate_completion(
        system_prompt,
        user_prompt,
        route_hint="external",
        sanitized=True,
    )
    rendered = str(final_text or "").strip()
    if not rendered or rendered.startswith("Error generating response:"):
        raise RuntimeError("External finalizer failed to generate a valid response")
    return rendered


def _finalize_with_kimi_stream(
    llm: Any,
    request_message: str,
    draft_markdown: str,
    task_type: str,
    resolved_chat_mode: str,
):
    source = str(draft_markdown or "")
    if not source.strip():
        raise RuntimeError("External finalizer requires non-empty draft markdown")

    system_prompt, user_prompt = _build_final_kimi_prompts(
        request_message=request_message,
        draft_markdown=source,
        task_type=task_type,
        resolved_chat_mode=resolved_chat_mode,
    )

    stream_fn = getattr(llm, "generate_completion_stream", None)
    if callable(stream_fn):
        for delta in stream_fn(
            system_prompt,
            user_prompt,
            route_hint="external",
            sanitized=True,
        ):
            if not delta:
                continue
            for chunk in _iter_stream_chunks(str(delta)):
                if chunk:
                    yield chunk
        return

    full = _finalize_with_kimi_sync(
        llm=llm,
        request_message=request_message,
        draft_markdown=source,
        task_type=task_type,
        resolved_chat_mode=resolved_chat_mode,
    )
    for chunk in _iter_stream_chunks(full):
        if chunk:
            yield chunk


def _resolve_response_llm_route(
    *,
    task_type: str,
    enhancement_applied: Optional[Dict[str, str]],
    prefs: Dict[str, object],
) -> str:
    enhancement = dict(enhancement_applied or {})
    if _flag_enabled(enhancement.get("erp_facts_then_kimi")):
        synthesis_route = str(
            enhancement.get("erp_synthesis_route")
            or enhancement.get("final_llm_route")
            or "external"
        ).strip().lower()
        if synthesis_route in {"local", "external"}:
            return synthesis_route
        return "external"

    external_blocked = str(enhancement.get("external_llm_blocked") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    skip_finalizer = str(enhancement.get("skip_finalizer") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    answer_mode = str(enhancement.get("answer_mode") or "").strip().lower()
    if external_blocked or skip_finalizer or answer_mode.startswith("deterministic"):
        return "deterministic"

    local_disabled = str(os.getenv("DISABLE_LOCAL_LLM", "false")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if local_disabled:
        return "external"

    enhancement_route = str(enhancement.get("final_llm_route") or "").strip().lower()
    if enhancement_route in {"local", "external"}:
        return enhancement_route

    external_first = str(
        (prefs or {}).get("external_first_online")
        or os.getenv("EXTERNAL_FIRST_ONLINE", "false")
    ).strip().lower() in {"1", "true", "yes", "on", "y"}

    if external_first and str(task_type or "").strip().lower() != "erp_data_query":
        return _resolve_final_llm_route_hint(prefs)

    if str(task_type or "").strip().lower() == "simple_lookup":
        return "deterministic"

    return _resolve_final_llm_route_hint(prefs)


_TRUE_FLAG_VALUES = {"1", "true", "yes", "on"}


def _flag_enabled(value: Any) -> bool:
    return str(value or "").strip().lower() in _TRUE_FLAG_VALUES


def _hermes_external_llm_blocked(hermes_plan: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(hermes_plan, dict):
        return False
    privacy = hermes_plan.get("privacy") if isinstance(hermes_plan.get("privacy"), dict) else {}
    if _flag_enabled((privacy or {}).get("external_llm_blocked")):
        return True
    domains = hermes_plan.get("domains") if isinstance(hermes_plan.get("domains"), list) else []
    return any(str(domain or "").strip().lower() in {"erp", "database"} for domain in domains) and str(
        (privacy or {}).get("external_llm_blocked") or ""
    ).strip().lower() != "false"


def _apply_hermes_privacy_to_enhancement(
    enhancement_applied: Optional[Dict[str, str]],
    hermes_plan: Optional[Dict[str, Any]],
) -> Dict[str, str]:
    resolved = {str(key): str(value) for key, value in dict(enhancement_applied or {}).items()}
    if not isinstance(hermes_plan, dict) or not hermes_plan:
        return resolved

    privacy = hermes_plan.get("privacy") if isinstance(hermes_plan.get("privacy"), dict) else {}
    if isinstance(privacy, dict):
        for key in ("data_sensitivity", "answer_mode", "database_capability"):
            value = privacy.get(key)
            if value is not None and str(value).strip():
                resolved.setdefault(key, str(value))

    if _flag_enabled(resolved.get("erp_facts_then_kimi")):
        resolved.setdefault("skip_finalizer", "true")
        resolved.pop("external_llm_blocked", None)
        return resolved

    if _hermes_external_llm_blocked(hermes_plan):
        resolved["external_llm_blocked"] = "true"
        resolved["skip_finalizer"] = "true"
        resolved["hermes_privacy_enforced"] = "true"

    return resolved


def _finalizer_allowed_by_privacy(
    finalizer_enabled: bool,
    enhancement_applied: Optional[Dict[str, str]],
    hermes_plan: Optional[Dict[str, Any]],
) -> bool:
    if not finalizer_enabled:
        return False
    enhancement = _apply_hermes_privacy_to_enhancement(enhancement_applied, hermes_plan)
    if _flag_enabled(enhancement.get("skip_finalizer")):
        return False
    if _flag_enabled(enhancement.get("external_llm_blocked")):
        return False
    return True


def _attach_llm_route_to_context(
    context: Optional[Dict[str, Any]],
    llm_route: str,
) -> Dict[str, Any]:
    resolved = dict(context or {})
    resolved["llm_route"] = llm_route
    return resolved


def _attach_response_quality_to_context(
    context: Optional[Dict[str, Any]],
    quality_flags: Optional[List[dict]],
    alerts_prepended: Optional[List[str]],
    enhancement_applied: Optional[Dict[str, str]],
) -> Dict[str, Any]:
    resolved = dict(context or {})
    flags = list(quality_flags or [])
    alerts = list(alerts_prepended or [])
    enhancement = dict(enhancement_applied or {})
    score = str(enhancement.get("executive_answer_quality") or "").strip()
    needs_review = bool(alerts) or any(
        str(flag.get("severity") or "").strip().lower() in {"high", "medium"}
        for flag in flags
    )

    if flags:
        resolved["quality_flags"] = flags
    if alerts:
        resolved["alerts_prepended"] = alerts
    if flags or alerts or score:
        resolved["response_quality"] = {
            "status": "needs_review" if needs_review else "passed",
            "executive_answer_quality": score,
            "flag_count": len(flags),
            "alert_count": len(alerts),
        }
    return resolved


def _build_confidence_payload(report: Any) -> Optional[Dict[str, Any]]:
    """Build a structured confidence payload from a DecisionReport."""
    confidence = getattr(report, "confidence", None)
    if confidence is None:
        return None
    try:
        return {
            "overall": round(float(getattr(confidence, "overall", 0.0)), 4),
            "label": str(getattr(confidence, "confidence_label", "") or "").strip() or "UNKNOWN",
            "data_quality": round(float(getattr(confidence, "data_quality", 0.0)), 4),
            "evidence_strength": round(float(getattr(confidence, "evidence_strength", 0.0)), 4),
            "model_stability": round(float(getattr(confidence, "model_stability", 0.0)), 4),
            "macro_uncertainty": round(float(getattr(confidence, "macro_uncertainty", 0.0)), 4),
            "uncertainty_source": str(getattr(confidence, "uncertainty_source", "") or "").strip(),
            "lookback_mape": getattr(confidence, "lookback_mape", None),
        }
    except Exception:
        return None


_FOLLOW_UP_HEADER_RE = re.compile(
    r"(?:##\s*❓\s*0?6\s*·\s*(?:建议追问|建议后续问题|Suggested\s*Follow-ups|Recommended\s*Follow-up))"
    r"|(?:##\s*(?:建议追问|建议后续问题|Suggested\s*Follow-ups|Recommended\s*Follow-up))"
    r"|(?:\*\*(?:建议追问|建议后续问题|Suggested\s*Follow-ups|Recommended\s*Follow-up)\*\*)",
    re.IGNORECASE,
)


_SUGGESTION_LINE_RE = re.compile(
    r"^(?:[-*•]|\d+[.\)】])\s*(.+)$",
    re.MULTILINE,
)


def _extract_suggestions_from_markdown(markdown: str, language: str = "zh") -> List[Dict[str, str]]:
    """Extract structured follow-up suggestions from markdown text."""
    text = str(markdown or "")
    if not text:
        return []

    # Find the follow-up section and extract lines after it
    match = _FOLLOW_UP_HEADER_RE.search(text)
    if not match:
        return []

    section_start = match.end()
    section_text = text[section_start:section_start + 1200]

    # Stop at next major header
    next_header = re.search(r"\n##\s", section_text)
    if next_header:
        section_text = section_text[: next_header.start()]

    suggestions: List[Dict[str, str]] = []
    seen: set[str] = set()
    for line_match in _SUGGESTION_LINE_RE.finditer(section_text):
        label = line_match.group(1).strip()
        # Remove inline markdown links
        label = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", label)
        # Remove bold/italic markers
        label = re.sub(r"[*_]{1,2}", "", label)
        label = label.strip()
        if not label or len(label) < 4:
            continue
        dedupe = label.lower()
        if dedupe in seen:
            continue
        seen.add(dedupe)
        suggestions.append({
            "id": f"suggest_{len(suggestions) + 1}",
            "label": label,
            "intent": "follow_up",
        })

    return suggestions[:5]


def _attach_llm_branching_to_context(
    context: Optional[Dict[str, Any]],
    enhancement_applied: Optional[Dict[str, str]],
) -> Dict[str, Any]:
    resolved = dict(context or {})
    enhancement = dict(enhancement_applied or {})
    keys = (
        "router_model",
        "erp_branch_model",
        "public_branch_model",
        "synthesis_model",
        "branch_summary",
        "policy_status",
        "router_ms",
        "fanout_ms",
        "erp_branch_ms",
        "public_branch_ms",
        "payload_ms",
        "final_synthesis_ms",
        "total_mixed_ms",
    )
    branch_meta = {
        key: str(enhancement.get(key) or "").strip()
        for key in keys
        if str(enhancement.get(key) or "").strip()
    }
    if branch_meta:
        resolved["llm_branching"] = branch_meta
    return resolved


def _attach_planner_sources_to_context(
    context: Optional[Dict[str, Any]],
    enhancement_applied: Optional[Dict[str, str]],
) -> Dict[str, Any]:
    resolved = dict(context or {})
    raw = str((enhancement_applied or {}).get("planner_sources_json") or "").strip()
    if not raw:
        return resolved
    try:
        payload = json.loads(raw)
    except Exception:
        return resolved
    if not isinstance(payload, dict):
        return resolved
    sources = payload.get("sources")
    data_sources = payload.get("data_sources")
    if isinstance(sources, list) or isinstance(data_sources, list):
        resolved["planner_sources"] = payload
    return resolved


def _build_hermes_plan_metadata(message: str) -> Dict[str, Any]:
    """Route plan metadata (domains/skills/privacy). Hermes agent removed."""
    return build_route_plan_metadata(message, llm_service=llm_service)


def _attach_hermes_plan_to_payload(
    payload: Dict[str, Any],
    hermes_plan: Optional[Dict[str, Any]],
    hermes_execution: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not hermes_plan:
        return payload
    payload["context"] = dict(payload.get("context") or {})
    payload["context"]["_stream"] = dict(payload["context"].get("_stream") or {})
    payload["context"]["_stream"].setdefault("hermes_plan", hermes_plan)
    agent_skills = [
        str(skill)
        for skill in hermes_plan.get("agent_skills") or []
        if str(skill).strip()
    ]
    execution = hermes_execution if isinstance(hermes_execution, dict) else {}
    executed = list((execution.get("skill_execution") or {}).get("executed_skills") or [])
    chart_attachments = list(execution.get("chart_attachments") or hermes_plan.get("chart_attachments") or [])
    visualizations = list(execution.get("visualizations") or hermes_plan.get("visualizations") or [])
    if chart_attachments:
        payload["context"]["chart_attachments"] = chart_attachments
    if visualizations:
        existing = list(payload["context"].get("visualizations") or [])
        payload["context"]["visualizations"] = existing + visualizations
    if agent_skills or executed or chart_attachments:
        payload["context"]["skill_trace"] = {
            "route": "edia",
            "selected_skills": agent_skills,
            "executed_skills": executed,
            "domains": list(hermes_plan.get("domains") or []),
            "capabilities": list(hermes_plan.get("capabilities") or []),
            "presentation_mode": hermes_plan.get("presentation_mode"),
            "observation_count": execution.get("observation_count"),
            "chart_count": len(chart_attachments) + len(visualizations),
            "quality_gate": "passed",
        }
    return payload


def _planner_sources_payload_from_metrics(
    metrics: Any,
    grounding_source: str = "planner",
) -> Dict[str, Any]:
    if not isinstance(metrics, dict):
        return {}
    plan = metrics.get("plan") or {}
    by_product = metrics.get("by_product") or {}
    sources: List[Dict[str, Any]] = []
    for product, intents in dict(by_product).items():
        if not isinstance(intents, dict):
            continue
        for intent, detail in intents.items():
            if not isinstance(detail, dict):
                continue
            row_count = int(detail.get("row_count") or 0)
            sources.append(
                {
                    "product": str(product or ""),
                    "intent": str(intent or ""),
                    "table": str(detail.get("table") or ""),
                    "row_count": row_count,
                    "latest_date": str(detail.get("latest_date") or ""),
                    "has_data": row_count > 0,
                    "metrics": dict(detail.get("metrics") or {}),
                }
            )
    if not sources and not metrics.get("data_sources"):
        return {}
    return {
        "grounding_source": str(grounding_source or "planner"),
        "generated_at": str(metrics.get("generated_at") or ""),
        "products": list(plan.get("products") or []),
        "intents": list(plan.get("intents") or []),
        "time_range": dict(plan.get("time_range") or {}),
        "data_sources": list(metrics.get("data_sources") or []),
        "sources": sources[:12],
        "errors": list(metrics.get("errors") or [])[:5],
    }


def _attach_mixed_timings_to_context(
    context: Optional[Dict[str, Any]],
    enhancement_applied: Optional[Dict[str, str]],
) -> Dict[str, Any]:
    resolved = dict(context or {})
    enhancement = dict(enhancement_applied or {})
    keys = (
        "router_ms",
        "fanout_ms",
        "erp_branch_ms",
        "public_branch_ms",
        "payload_ms",
        "final_synthesis_ms",
        "total_mixed_ms",
    )
    mixed_timings: Dict[str, float] = {}
    for key in keys:
        raw = str(enhancement.get(key) or "").strip()
        if not raw:
            continue
        try:
            mixed_timings[key] = round(float(raw), 1)
        except Exception:
            continue
    if mixed_timings:
        existing = resolved.get("_timings")
        if isinstance(existing, dict):
            merged = dict(existing)
            merged.update(mixed_timings)
            resolved["_timings"] = merged
        else:
            resolved["_timings"] = mixed_timings
    return resolved


def _is_erp_report_stream_query(message: str) -> bool:
    try:
        from backend.src.engine.decision_graph import _looks_like_erp_report_query

        return _looks_like_erp_report_query(str(message or ""))
    except Exception:
        return False


def _is_fast_erp_stream_eligible(
    message: str,
    hermes_plan: Optional[Dict[str, Any]] = None,
    *,
    deterministic_erp_stream: bool = False,
) -> bool:
    from backend.src.hermes.erp_grounding import is_fast_erp_stream_eligible

    return is_fast_erp_stream_eligible(
        message,
        hermes_plan,
        deterministic_erp_stream=deterministic_erp_stream,
    )


def _maybe_apply_erp_hybrid_markdown(
    llm: Any,
    message: str,
    facts_markdown: str,
    prefs: Optional[Dict[str, object]],
) -> str:
    from backend.src.hermes.erp_grounding import apply_erp_hybrid_synthesis

    facts = str(facts_markdown or "").strip()
    if not facts:
        return facts
    language = str((prefs or {}).get("language") or "zh")
    if not any("\u4e00" <= ch <= "\u9fff" for ch in message) and language not in {"en", "english"}:
        language = "en"
    return apply_erp_hybrid_synthesis(llm, message, facts, language=language)


def _try_erp_facts_then_kimi_stream_report(
    message: str,
    prefs: Optional[Dict[str, object]],
    *,
    hermes_plan: Optional[Dict[str, Any]] = None,
    deterministic_erp_stream: bool = False,
) -> Optional[Dict[str, Any]]:
    """Stage A: local ERP DB facts. Stage B: Kimi (or local) final report synthesis."""
    if not _is_fast_erp_stream_eligible(
        message,
        hermes_plan,
        deterministic_erp_stream=deterministic_erp_stream,
    ):
        return None
    try:
        import time as _time

        from backend.src.hermes.erp_grounding import (
            build_erp_no_data_markdown,
            build_erp_sales_report_markdown,
            erp_synthesis_route,
            guard_erp_markdown_against_denial,
            stream_erp_synthesis,
        )
        from backend.src.nlu.intent_parser import _fast_intent
        from backend.src.nlu.types import TaskType

        data_start = _time.perf_counter()
        facts = build_erp_sales_report_markdown(message, prefs)
        data_ms = round((_time.perf_counter() - data_start) * 1000.0, 1)
        language = str((prefs or {}).get("language") or "zh")
        if not any("\u4e00" <= ch <= "\u9fff" for ch in message) and language not in {
            "en",
            "english",
        }:
            language = "en"

        # Build multi-metric comparison visualization when applicable
        viz = None
        try:
            from backend.src.domain.product_resolver import extract_product_ids_in_text

            product_ids = extract_product_ids_in_text(message)
            viz = _build_dynamic_comparison_visualization(message, product_ids, language)
        except Exception:
            pass

        if not facts:
            if _should_bypass_erp_fast_path(message, hermes_plan):
                return None
            no_data = build_erp_no_data_markdown(message, language=language)
            task_type, _ = _fast_intent(message)
            return {
                "markdown": no_data,
                "task_type": getattr(task_type, "value", str(task_type or TaskType.ERP_DATA_QUERY)),
                "status": "success",
                "context": {
                    "erp_facts_then_kimi": True,
                    "erp_data_available": False,
                    "_stream": {"display_markdown_source": "erp_facts_no_rows"},
                    "_perf": {"erp_data_ms": data_ms, "kimi_ms": 0.0},
                    "visualization": viz,
                    "visualizations": [viz] if viz else [],
                },
                "enhancement_applied": {
                    "erp_facts_then_kimi": "true",
                    "deterministic_erp_sales": "true",
                    "skip_finalizer": "true",
                    "database_capability": "erp_sales_summary",
                    "erp_synthesis_route": erp_synthesis_route(),
                },
                "llm_route": "deterministic",
                "resolved_chat_mode": str((prefs or {}).get("chat_mode") or "analysis"),
                "mode_reason": "erp_facts_no_rows",
            }

        markdown = facts
        kimi_ms = 0.0
        synthesis_route = erp_synthesis_route()
        if llm_service is not None:
            kimi_start = _time.perf_counter()
            if _env_flag("EDIA_ERP_FAST_HYBRID", "false"):
                markdown = _maybe_apply_erp_hybrid_markdown(
                    llm_service,
                    message,
                    facts,
                    prefs,
                )
            else:
                markdown = stream_erp_synthesis(
                    llm_service,
                    message,
                    facts,
                    language=language,
                )
            kimi_ms = round((_time.perf_counter() - kimi_start) * 1000.0, 1)

        markdown = guard_erp_markdown_against_denial(
            markdown,
            facts,
            has_erp_data=True,
        )

        task_type, _confidence = _fast_intent(message)
        resolved_task = task_type or TaskType.ERP_DATA_QUERY
        llm_route = "external" if synthesis_route == "external" else "local"
        return {
            "markdown": markdown,
            "task_type": getattr(resolved_task, "value", str(resolved_task)),
            "status": "success",
            "context": {
                "erp_facts_then_kimi": True,
                "erp_data_available": True,
                "_stream": {"display_markdown_source": "erp_facts_then_kimi"},
                "_perf": {"erp_data_ms": data_ms, "kimi_ms": kimi_ms},
                "visualization": viz,
                "visualizations": [viz] if viz else [],
            },
            "enhancement_applied": {
                "erp_facts_then_kimi": "true",
                "fast_erp_report_stream": "true",
                "skip_finalizer": "true",
                "deterministic_erp_sales": "true",
                "database_capability": "erp_sales_summary",
                "erp_synthesis_route": synthesis_route,
                "final_llm_route": synthesis_route,
            },
            "llm_route": llm_route,
            "resolved_chat_mode": str((prefs or {}).get("chat_mode") or "analysis"),
            "mode_reason": "erp_facts_then_kimi",
        }
    except Exception:
        logger.warning("ERP facts-then-Kimi stream failed", exc_info=True)
        return None


def _try_fast_erp_report_stream_report(
    message: str,
    prefs: Optional[Dict[str, object]],
    *,
    hermes_plan: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Backward-compatible alias for the two-stage ERP path."""
    return _try_erp_facts_then_kimi_stream_report(
        message,
        prefs,
        hermes_plan=hermes_plan,
        deterministic_erp_stream=False,
    )


def _try_fast_ops_inventory_stream_report(
    message: str,
    prefs: Optional[Dict[str, object]],
    *,
    hermes_plan: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Build a grounded C5/ethylene supply-impact report without the full LangGraph."""
    if not _env_flag("EDIA_FAST_OPS_STREAM", "true"):
        return None
    try:
        from types import SimpleNamespace

        from backend.src.engine.decision_graph import (
            _build_ops_inventory_pricing_report,
            _is_ops_inventory_pricing_query,
            _node_parallel_fetch,
            _node_tool_execution,
        )
        from backend.src.domain.product_resolver import extract_product_ids_in_text
        from backend.src.nlu.intent_parser import _fast_intent
        from backend.src.nlu.types import TaskType

        if not _is_ops_inventory_pricing_query(message):
            return None

        task_type, confidence = _fast_intent(message)
        product_ids = extract_product_ids_in_text(message)
        target_ids = [pid for pid in product_ids if pid in {"isoprene", "dcpd", "piperylene"}]
        if not target_ids:
            target_ids = ["isoprene", "dcpd"]
        primary_product = target_ids[0]
        lookup_products = ", ".join(target_ids)
        resolved_task = task_type or TaskType.MARKET_ANALYSIS_DECISION

        state: Dict[str, Any] = {
            "user_query": message,
            "user_prefs": dict(prefs or {}),
            "hermes_plan": dict(hermes_plan or {}),
            "conversation_history": [],
            "parsed_intent": SimpleNamespace(
                task_type=resolved_task,
                confidence=float(confidence or 0.9),
                product=lookup_products,
                time_period=None,
                region=None,
                question=message,
            ),
            "user_context": SimpleNamespace(product=lookup_products),
            "graph_product_id": primary_product,
            "graph_product_ids": target_ids,
        }
        state.update(_node_parallel_fetch(state))
        state.update(_node_tool_execution(state))
        markdown = str(_build_ops_inventory_pricing_report(state) or "").strip()
        if not markdown:
            return None

        return {
            "markdown": markdown,
            "task_type": getattr(resolved_task, "value", str(resolved_task)),
            "status": "success",
            "context": {
                "fast_ops_inventory_stream": True,
                "graph_product_id": primary_product,
                "graph_product_ids": product_ids or [primary_product],
                "_stream": {
                    "display_markdown_source": "fast_ops_inventory_stream",
                },
            },
            "enhancement_applied": {
                "fast_ops_inventory_stream": "true",
                "skip_finalizer": "true",
                "deterministic_market_lookup": "true",
            },
            "llm_route": "local",
            "resolved_chat_mode": str((prefs or {}).get("chat_mode") or "analysis"),
            "mode_reason": "fast_ops_inventory_stream",
        }
    except Exception:
        logger.warning("Fast ops inventory stream report failed", exc_info=True)
        return None


def _try_fast_naphtha_passthrough_stream_report(
    message: str,
    prefs: Optional[Dict[str, object]],
    *,
    hermes_plan: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Build a grounded naphtha cost pass-through report without the full LangGraph."""
    if not _env_flag("EDIA_FAST_NAPHTHA_STREAM", "true"):
        return None
    try:
        from types import SimpleNamespace

        from backend.src.engine.decision_graph import (
            _build_naphtha_passthrough_report,
            _is_naphtha_cost_passthrough_query,
            _node_parallel_fetch,
            _node_tool_execution,
            _resolve_output_language,
        )
        from backend.src.nlu.intent_parser import _fast_intent
        from backend.src.nlu.types import TaskType

        if not _is_naphtha_cost_passthrough_query(message):
            return None

        task_type, confidence = _fast_intent(message)
        resolved_task = task_type or TaskType.MARKET_ANALYSIS_DECISION
        language = str((prefs or {}).get("language") or "").strip().lower()
        if language not in {"zh", "en", "english"}:
            language = "en" if not any("\u4e00" <= ch <= "\u9fff" for ch in message) else "zh"

        state: Dict[str, Any] = {
            "user_query": message,
            "user_prefs": dict(prefs or {}),
            "hermes_plan": dict(hermes_plan or {}),
            "conversation_history": [],
            "parsed_intent": SimpleNamespace(
                task_type=resolved_task,
                confidence=float(confidence or 0.9),
                product="naphtha",
                time_period=None,
                region=None,
                question=message,
            ),
            "user_context": SimpleNamespace(product="naphtha"),
            "graph_product_id": "naphtha",
        }
        state.update(_node_parallel_fetch(state))
        state.update(_node_tool_execution(state))
        output_language = _resolve_output_language(state)
        if language in {"en", "english"}:
            output_language = "en"
        elif language == "zh":
            output_language = "zh"
        markdown = str(_build_naphtha_passthrough_report(state, language=output_language) or "").strip()
        if not markdown:
            return None

        return {
            "markdown": markdown,
            "task_type": getattr(resolved_task, "value", str(resolved_task)),
            "status": "success",
            "context": {
                "fast_naphtha_passthrough_stream": True,
                "graph_product_id": "naphtha",
                "_stream": {"display_markdown_source": "fast_naphtha_passthrough_stream"},
            },
            "enhancement_applied": {
                "fast_naphtha_passthrough_stream": "true",
                "skip_finalizer": "true",
                "deterministic_market_lookup": "true",
            },
            "llm_route": "local",
            "resolved_chat_mode": str((prefs or {}).get("chat_mode") or "analysis"),
            "mode_reason": "fast_naphtha_passthrough_stream",
        }
    except Exception:
        logger.warning("Fast naphtha passthrough stream report failed", exc_info=True)
        return None


def _harvest_forced_synthesis_context(
    message: str,
    prefs: Dict[str, object],
    *,
    hermes_plan: Optional[Dict[str, Any]] = None,
    prep_thread: Optional[_threading.Thread] = None,
    prep_result: Optional[Dict[str, Any]] = None,
    join_seconds: float = 10.0,
) -> Optional[Dict[str, Any]]:
    """Harvest partial prep or build a deterministic report before empty LLM synthesis."""
    prep_result = prep_result if isinstance(prep_result, dict) else {}
    if prep_thread is not None and prep_thread.is_alive():
        prep_thread.join(timeout=max(0.1, float(join_seconds)))

    report = prep_result.get("report")
    if report is not None and str(getattr(report, "raw_markdown", "") or "").strip():
        report_context = getattr(report, "context", None)
        if hasattr(report_context, "to_dict"):
            report_context = report_context.to_dict()
        return {
            "markdown": str(getattr(report, "raw_markdown", "") or ""),
            "status": str(getattr(report, "status", None) or "success"),
            "task_type": getattr(getattr(report, "task_type", None), "value", "analysis"),
            "context": report_context if isinstance(report_context, dict) else {},
            "enhancement_applied": dict(getattr(report, "enhancement_applied", None) or {}),
            "resolved_chat_mode": getattr(report, "resolved_chat_mode", None),
            "mode_reason": getattr(report, "mode_reason", None) or "forced_synthesis_harvested_report",
        }

    for builder in (
        lambda: _try_erp_facts_then_kimi_stream_report(
            message,
            prefs,
            hermes_plan=hermes_plan,
            deterministic_erp_stream=True,
        ),
        lambda: _try_fast_naphtha_passthrough_stream_report(
            message, prefs, hermes_plan=hermes_plan
        ),
        lambda: _try_fast_ops_inventory_stream_report(
            message, prefs, hermes_plan=hermes_plan
        ),
    ):
        fast = builder()
        if fast is not None:
            fast = dict(fast)
            fast["mode_reason"] = "forced_synthesis_deterministic_fast_path"
            return fast

    if prep_result.get("tool_info") or prep_result.get("perception_snapshot"):
        return None

    try:
        from types import SimpleNamespace

        from backend.src.engine.decision_graph import (
            _build_naphtha_passthrough_report,
            _is_naphtha_cost_passthrough_query,
            _node_parallel_fetch,
            _node_tool_execution,
            _resolve_output_language,
        )

        state: Dict[str, Any] = {
            "user_query": message,
            "user_prefs": dict(prefs or {}),
            "hermes_plan": dict(hermes_plan or {}),
        }
        state.update(_node_parallel_fetch(state))
        state.update(_node_tool_execution(state))
        prep_result.update(state)

        if _is_naphtha_cost_passthrough_query(message):
            lang = _resolve_output_language(state)
            markdown = str(_build_naphtha_passthrough_report(state, language=lang) or "").strip()
            if markdown:
                return {
                    "markdown": markdown,
                    "status": "success",
                    "task_type": "analysis",
                    "context": {"forced_synthesis_minimal_fetch": True},
                    "enhancement_applied": {
                        "skip_finalizer": "true",
                        "deterministic_market_lookup": "true",
                    },
                    "resolved_chat_mode": str(prefs.get("chat_mode") or "analysis"),
                    "mode_reason": "forced_synthesis_minimal_fetch",
                }
    except Exception:
        logger.debug("Forced synthesis minimal fetch failed", exc_info=True)
    return None


def _build_deterministic_market_chat_report(
    message: str,
    prefs: Optional[Dict[str, object]],
) -> Optional[Dict[str, Any]]:
    try:
        from types import SimpleNamespace

        from backend.src.routing.query_router import route_query
        from backend.src.planner.market_metrics import fetch_routed_market_block
        from backend.src.engine.decision_graph import (
            _build_factual_data_unavailable_report,
            _build_simple_lookup_report,
        )
        from backend.src.nlu.types import TaskType

        routed = route_query(message)
        if routed.path != "deterministic_market" or not routed.skip_llm_finalizer:
            return None

        language = str((prefs or {}).get("language") or "").strip().lower()
        output_language = "en" if language in {"en", "english"} else "zh"
        tool_info = fetch_routed_market_block(
            message,
            product_ids=list(routed.product_ids or []),
            price_types=list(routed.price_types or []),
            metric_id=routed.metric_id,
            change_days=int(routed.change_days or 7),
            language=output_language,
        )
        parsed = SimpleNamespace(
            task_type=TaskType.SIMPLE_LOOKUP,
            product=routed.product_ids[0] if routed.product_ids else None,
            time_period=None,
            region=None,
            question=message,
        )
        state = {
            "user_query": message,
            "user_prefs": dict(prefs or {}),
            "parsed_intent": parsed,
            "user_context": SimpleNamespace(product=parsed.product),
            "routed_query": routed.to_dict(),
            "tool_info": tool_info,
        }
        if "INTERNAL DATABASE LIVE DATA" in str(tool_info or ""):
            markdown = _build_simple_lookup_report(state)
            if routed.metric_id == "sinopec_monthly_listing":
                header = (
                    "### SINOPEC Shanghai listing prices (market_prices / sinopec_sh)\n\n"
                    if output_language == "en"
                    else "### 中石化上海石化挂牌价（market_prices / sinopec_sh）\n\n"
                )
                if header.strip() not in markdown:
                    markdown = header + markdown
        else:
            markdown = _build_factual_data_unavailable_report(state)

        return {
            "markdown": markdown,
            "context": {
                "routed_query": routed.to_dict(),
                "tool_info": tool_info,
            },
            "task_type": TaskType.SIMPLE_LOOKUP.value,
        }
    except Exception:
        logger.warning("Deterministic market chat precheck failed", exc_info=True)
        return None


def _detect_response_language(message: str, prefs: Optional[Dict[str, object]] = None) -> str:
    pref_language = str((prefs or {}).get("language") or "").strip().lower()
    if pref_language in {"zh", "en"}:
        return pref_language
    return "zh" if any("\u4e00" <= ch <= "\u9fff" for ch in str(message or "")) else "en"


def _localized_stream_message(language: str, key: str) -> str:
    normalized_language = "en" if str(language or "").strip().lower() == "en" else "zh"
    messages = {
        "prep_force_synthesis": {
            "en": "Preparation is slow; starting direct synthesis with current context",
            "zh": "准备较慢，正在基于当前上下文直接生成分析结果",
        },
        "prep_still_waiting": {
            "en": "Still preparing analysis; continuing to wait for full synthesis",
            "zh": "仍在准备分析，继续等待完整结果生成",
        },
        "prep_timeout_preview": {
            "en": "Preparing analysis is taking longer than expected.",
            "zh": "分析准备时间超出预期，请稍后查看完整结果。",
        },
        "prep_timeout_error": {
            "en": "Preparation exceeded hard stream timeout; returned preview instead of dropping the connection.",
            "zh": "准备阶段超过流式硬超时，已返回预览内容以避免连接中断。",
        },
        "stage_thinking": {
            "en": "/thinking",
            "zh": "思考中",
        },
        "stage_analyzing": {
            "en": "/analyzing",
            "zh": "分析中",
        },
        "stage_preparing": {
            "en": "/preparing",
            "zh": "准备中",
        },
        "stage_dual_model_synthesis": {
            "en": "/dual-model-synthesis",
            "zh": "双模型综合中",
        },
        "stage_final_synthesis": {
            "en": "/final-synthesis",
            "zh": "最终综合生成中",
        },
        "stage_weekly_report_complete": {
            "en": "Weekly report ready",
            "zh": "周报生成完成",
        },
        "continue_dual_model_empty": {
            "en": "Continuing synthesis: dual-model returned empty content",
            "zh": "继续综合生成：双模型返回内容为空",
        },
        "continue_report_empty": {
            "en": "Continuing synthesis: report content was empty",
            "zh": "继续综合生成：报告内容为空",
        },
        "llm_not_configured": {
            "en": "LLM service not configured",
            "zh": "LLM 服务未配置",
        },
        "llm_stream_budget_timeout": {
            "en": "Streaming reached the response time budget; returned the best available result to avoid connection interruption.",
            "zh": "流式生成达到响应时间预算，已返回当前最优结果以避免连接中断。",
        },
    }
    return messages.get(key, {}).get(normalized_language) or messages[key]["zh"]


def _build_simple_greeting_fast_response(
    message: str,
    prefs: Optional[Dict[str, object]] = None,
) -> Optional[Dict[str, Any]]:
    if _strict_kimi_only_mode():
        return None

    raw_message = str(message or "").strip()
    if not raw_message:
        return None

    normalized = re.sub(r"\s+", " ", raw_message.lower()).strip()
    collapsed = re.sub(r"[^\w\u4e00-\u9fff]+", "", normalized)
    greeting_markers = {
        "hi",
        "hello",
        "hey",
        "hi there",
        "hello there",
        "good morning",
        "good afternoon",
        "good evening",
        "morning",
        "你好",
        "您好",
        "嗨",
        "哈喽",
        "早上好",
        "下午好",
        "晚上好",
    }
    collapsed_markers = {
        "hi",
        "hello",
        "hey",
        "hithere",
        "hellothere",
        "goodmorning",
        "goodafternoon",
        "goodevening",
        "morning",
        "你好",
        "您好",
        "嗨",
        "哈喽",
        "早上好",
        "下午好",
        "晚上好",
    }

    if len(normalized) > 24 or (normalized not in greeting_markers and collapsed not in collapsed_markers):
        return None

    language = _detect_response_language(raw_message, prefs)
    if language == "zh":
        markdown = (
            "你好。我在。\n\n"
            "我可以直接帮你看价格、走势、价差、库存，或者内部经营数据。"
            "你想先看哪一项？"
        )
    else:
        markdown = (
            "Hello. I'm here.\n\n"
            "I can help with prices, market direction, spreads, inventory, or internal business data. "
            "What would you like to look at first?"
        )

    context = _build_response_context(
        report_context={"_fast_path": "simple_greeting"},
        message=raw_message,
        task_type="conversational",
        language=language,
        raw_markdown=markdown,
        resolved_chat_mode="chat",
    )
    context["_stream"] = {
        "display_markdown_source": "fast_path",
        "fast_path": "simple_greeting",
        "stages": [
            {
                "stage": "conversational_direct",
                "label": _localized_stage_label("conversational_direct", language),
                "elapsed_ms": 0.0,
            }
        ],
    }
    context["_perf"] = {
        "prep_ms": 0.0,
        "llm_ms": 0.0,
        "first_delta_ms": 0.0,
        "total_ms": 0.0,
    }
    context = _attach_llm_route_to_context(context, "deterministic")

    return {
        "markdown": markdown,
        "status": "success",
        "context": context,
        "task_type": "conversational",
        "llm_route": "deterministic",
        "resolved_chat_mode": "chat",
        "response_mode": "conversational",
        "mode_reason": "simple_greeting_fast_path",
        "quality_flags": [],
        "alerts_prepended": [],
        "enhancement_applied": {},
    }


@app.post("/api/chat/route-plan")
async def chat_route_plan(
    request: HermesPlanRequest,
    http_request: Request,
):
    _require_current_user(http_request)
    return build_chat_route_plan(request.message, llm_service=llm_service).to_dict()


@app.post("/api/hermes/plan")
async def hermes_plan_deprecated(
    request: HermesPlanRequest,
    http_request: Request,
):
    """Deprecated: use POST /api/chat/route-plan."""
    return await chat_route_plan(request, http_request)


@app.post("/api/chat/stream")
async def chat_stream(
    request: ChatRequest,
    http_request: Request,
):

    """Streaming variant of /api/chat using Server-Sent Events (SSE)."""

    total_start = time.perf_counter()

    current_user = _require_current_user(http_request)
    user_id = str(current_user["id"])
    response_language = _detect_response_language(str(request.message or ""), request.prefs)

    # 1. Manage Session (same behavior as /api/chat)
    session_id = request.session_id
    prior_messages = []
    if session_id:
        if not db.session_belongs_to_user(session_id, user_id):
            raise HTTPException(status_code=404, detail="Session not found")
        prior_messages = db.get_messages(session_id)
    else:
        title = (
            (request.message[:20] + "...")
            if len(request.message) > 20
            else request.message
        )
        session_id = db.create_session(user_id, title)

    effective_message = _build_effective_chat_message(
        request.message,
        request.attachment_contexts,
    )
    hermes_plan_metadata = _build_hermes_plan_metadata(effective_message)

    # 2. Save User Message
    db.add_message(session_id, "user", request.message)

    fast_response = _build_simple_greeting_fast_response(
        request.message,
        request.prefs,
    )

    weekly_stream_report: Optional[Dict[str, Any]] = None
    weekly_progress_queue: Optional[Any] = None
    weekly_gen_task = None
    if fast_response is None and _is_weekly_report_chat_request(request.message):
        weekly_progress_queue = asyncio.Queue()

        async def _generate_wr():
            nonlocal weekly_stream_report
            def _progress(msg: str) -> None:
                label = str(msg or "").strip()
                if label:
                    weekly_progress_queue.put_nowait(("progress", label))
            try:
                weekly_stream_report = await _generate_weekly_report_chat_payload(
                    user_id=user_id,
                    session_id=session_id,
                    language=response_language,
                    progress_callback=_progress,
                    current_week=True,
                )
                weekly_progress_queue.put_nowait(("done", weekly_stream_report))
            except Exception as _wr_err:
                logger.error("Weekly report from chat stream failed: %s", _wr_err, exc_info=True)
                weekly_progress_queue.put_nowait(("error", str(_wr_err)))

        weekly_gen_task = asyncio.create_task(_generate_wr())

    async def event_iter():
        nonlocal fast_response, response_language, weekly_stream_report, weekly_progress_queue, effective_message, hermes_plan_metadata
        # Let UI begin rendering immediately
        yield _sse("meta", {"session_id": session_id})
        if fast_response is not None:
            if _final_kimi_hook_enabled(dict(request.prefs or {})):
                llm_fast = getattr(decision_service, "llm", None)
                fast_response = dict(fast_response)
                fast_response["markdown"] = _finalize_with_kimi_sync(
                    llm=llm_fast,
                    request_message=request.message,
                    draft_markdown=str(fast_response.get("markdown") or ""),
                    task_type=str(fast_response.get("task_type") or "conversational"),
                    resolved_chat_mode=str(fast_response.get("resolved_chat_mode") or "chat"),
                )
                fast_response["llm_route"] = "external"
                fast_response["context"] = _attach_llm_route_to_context(
                    fast_response.get("context"),
                    "external",
                )

            db.add_message(
                session_id,
                "assistant",
                fast_response["markdown"],
                context=_attach_hermes_plan_to_payload(
                    fast_response,
                    hermes_plan_metadata,
                )["context"],
            )
            stage_info = (
                fast_response["context"].get("_stream", {}).get("stages", [{}])[0]
            )
            yield _sse("stage", stage_info)
            yield _sse("delta", {"delta": fast_response["markdown"]})
            yield _sse(
                "done",
                _attach_hermes_plan_to_payload({
                    **fast_response,
                    "session_id": session_id,
                }, hermes_plan_metadata),
            )
            return

        if weekly_progress_queue is not None:
            phase_start = time.perf_counter()
            while True:
                event_type, data = await weekly_progress_queue.get()
                if event_type == "progress":
                    yield _sse(
                        "stage",
                        {
                            "stage": "weekly_report",
                            "label": data,
                            "elapsed_ms": round((time.perf_counter() - phase_start) * 1000.0, 1),
                            "phase": "started",
                        },
                    )
                elif event_type == "error":
                    effective_message = request.message + f"\n\n[注：周报生成失败: {data}]"
                    hermes_plan_metadata = _build_hermes_plan_metadata(effective_message)
                    break
                elif event_type == "done":
                    weekly_stream_report = data
                    break

            if weekly_stream_report is not None:
                yield _sse(
                    "stage",
                    {
                        "stage": "weekly_report",
                        "label": _localized_stream_message(
                            response_language,
                            "stage_weekly_report_complete",
                        ),
                        "elapsed_ms": round((time.perf_counter() - phase_start) * 1000.0, 1),
                        "phase": "completed",
                    },
                )
                fast_markdown = str(weekly_stream_report.get("markdown") or "")
                for chunk in _iter_stream_chunks(fast_markdown, max_chars=120):
                    yield _sse("delta", {"delta": chunk})
                payload = {
                    **weekly_stream_report,
                    "session_id": session_id,
                    "error_message": None,
                    "response_mode": "structured",
                }
                payload["context"] = _attach_llm_route_to_context(
                    payload.get("context") if isinstance(payload.get("context"), dict) else {},
                    str(weekly_stream_report.get("llm_route") or "external"),
                )
                payload["context"].setdefault("_perf", {})
                payload["context"]["_perf"] = {
                    "prep_ms": round((time.perf_counter() - phase_start) * 1000.0, 1),
                    "llm_ms": 0.0,
                    "first_delta_ms": round((time.perf_counter() - total_start) * 1000.0, 1),
                    "total_ms": round((time.perf_counter() - total_start) * 1000.0, 1),
                }
                payload = _attach_hermes_plan_to_payload(payload, hermes_plan_metadata)
                db.add_message(
                    session_id,
                    "assistant",
                    fast_markdown,
                    context=payload["context"],
                )
                yield _sse("done", payload)
                if weekly_gen_task is not None:
                    await weekly_gen_task
                return
            else:
                if weekly_gen_task is not None:
                    await weekly_gen_task

        phase_start = time.perf_counter()
        yield _sse(
            "stage",
            {
                "stage": "thinking",
                "label": _localized_stream_message(response_language, "stage_thinking"),
                "elapsed_ms": 0.0,
            },
        )

        # ── P0: Emit planner-based fast preview immediately ────────────────────
        # plan_query runs in <2s and gives us intent + products before the
        # slow background thread (prepare_request) completes. Emit it right away
        # so the UI shows a Quick Take instead of a blank screen.
        try:
            from backend.src.planner.query_planner import plan_query, build_planner_fast_preview

            planner_plan = plan_query(str(request.message or ""))
            if planner_plan:
                fast_preview_md = build_planner_fast_preview(planner_plan, str(request.message or ""))
                if fast_preview_md and len(fast_preview_md.strip()) > 10:
                    yield _sse(
                        "preview",
                        {
                            "markdown": fast_preview_md,
                            "grounding_source": "planner",
                            "products": planner_plan.get("products") or [],
                            "intents": planner_plan.get("intents") or [],
                            "needs_forecast": bool(planner_plan.get("needs_forecast")),
                        },
                    )
        except Exception:
            pass  # Non-blocking: fast preview is best-effort

        effective_prefs = dict(request.prefs or {})
        timeout_mode = _resolve_request_chat_mode(
            request.message,
            prefs=effective_prefs,
            request_chat_mode=request.chat_mode,
        )
        effective_prefs["chat_mode"] = timeout_mode
        effective_prefs = _apply_llm_route_defaults(effective_prefs, timeout_mode)
        effective_prefs.setdefault("graph_llm_route", "local")
        if _is_heavy_llm_task(request.message, hermes_plan_metadata):
            effective_prefs["graph_llm_route"] = "external"
            effective_prefs["use_external_graph_reasoning"] = True
        effective_prefs["kimi_mixed_synthesis"] = False
        effective_prefs["planner_streaming_synthesis"] = True
        effective_prefs["final_kimi_hook"] = False
        effective_prefs["stream_dual_model_report"] = False
        finalizer_enabled = _final_kimi_hook_enabled(effective_prefs)
        llm = getattr(decision_service, "llm", None)
        deterministic_erp_stream = _is_deterministic_erp_stream_request(
            request.message,
            hermes_plan_metadata,
        )
        fast_deterministic_report = None
        if _is_fast_erp_stream_eligible(
            effective_message,
            hermes_plan_metadata,
            deterministic_erp_stream=deterministic_erp_stream,
        ):
            fast_deterministic_report = _try_erp_facts_then_kimi_stream_report(
                effective_message,
                effective_prefs,
                hermes_plan=hermes_plan_metadata,
                deterministic_erp_stream=deterministic_erp_stream,
            )
        if fast_deterministic_report is None and not deterministic_erp_stream:
            fast_deterministic_report = _try_fast_naphtha_passthrough_stream_report(
                effective_message,
                effective_prefs,
                hermes_plan=hermes_plan_metadata,
            )
            if fast_deterministic_report is None:
                fast_deterministic_report = _try_fast_ops_inventory_stream_report(
                    effective_message,
                    effective_prefs,
                    hermes_plan=hermes_plan_metadata,
                )
        fast_ops_report = fast_deterministic_report
        if fast_ops_report is not None:
            fast_markdown = str(fast_ops_report.get("markdown") or "")
            route_name = str(
                fast_ops_report.get("mode_reason") or "fast_deterministic_stream"
            )
            logger.info(
                "chat_stream route=%s session=%s chars=%s",
                route_name,
                session_id,
                len(fast_markdown),
            )
            if route_name.startswith("erp_facts"):
                yield _sse(
                    "stage",
                    {
                        "stage": "erp_data_retrieval",
                        "label": _localized_stage_label(
                            "erp_data_retrieval", response_language
                        ),
                        "elapsed_ms": round((time.perf_counter() - phase_start) * 1000.0, 1),
                        "phase": "completed",
                    },
                )
            yield _sse(
                "stage",
                {
                    "stage": "parallel_fetch",
                    "label": _localized_stage_label("parallel_fetch", response_language),
                    "elapsed_ms": round((time.perf_counter() - phase_start) * 1000.0, 1),
                    "phase": "completed",
                },
            )
            for chunk in _iter_stream_chunks(fast_markdown, max_chars=120):
                yield _sse("delta", {"delta": chunk})
            enhancement_applied = _apply_hermes_privacy_to_enhancement(
                dict(fast_ops_report.get("enhancement_applied") or {}),
                hermes_plan_metadata,
            )
            llm_route = _resolve_response_llm_route(
                task_type=str(fast_ops_report.get("task_type") or "analysis"),
                enhancement_applied=enhancement_applied,
                prefs=effective_prefs,
            )
            response_context = _attach_llm_route_to_context(
                fast_ops_report.get("context") if isinstance(fast_ops_report.get("context"), dict) else {},
                llm_route,
            )
            response_context = _attach_llm_branching_to_context(
                response_context,
                enhancement_applied,
            )
            response_context.setdefault("_perf", {})
            response_context["_perf"] = {
                "prep_ms": round((time.perf_counter() - phase_start) * 1000.0, 1),
                "llm_ms": 0.0,
                "first_delta_ms": round((time.perf_counter() - total_start) * 1000.0, 1),
                "total_ms": round((time.perf_counter() - total_start) * 1000.0, 1),
            }
            payload = {
                "markdown": fast_markdown,
                "status": str(fast_ops_report.get("status") or "success"),
                "context": response_context,
                "task_type": str(fast_ops_report.get("task_type") or "analysis"),
                "session_id": session_id,
                "error_message": None,
                "llm_route": llm_route,
                "resolved_chat_mode": str(
                    fast_ops_report.get("resolved_chat_mode")
                    or effective_prefs.get("chat_mode")
                    or "analysis"
                ),
                "response_mode": "structured",
                "mode_reason": str(fast_ops_report.get("mode_reason") or "fast_ops_inventory_stream"),
                "quality_flags": [],
                "alerts_prepended": False,
                "enhancement_applied": enhancement_applied,
            }
            payload = _attach_hermes_plan_to_payload(payload, hermes_plan_metadata)
            db.add_message(
                session_id,
                "assistant",
                fast_markdown,
                context=payload["context"],
            )
            yield _sse("done", payload)
            return

        if deterministic_erp_stream:
            enhancement_hint = _apply_hermes_privacy_to_enhancement(
                _deterministic_erp_stream_enhancements(request.message),
                hermes_plan_metadata,
            )
            yield _sse(
                "stage",
                {
                    "stage": "erp_data_retrieval",
                    "label": _localized_stage_label("erp_data_retrieval", response_language),
                    "elapsed_ms": round((time.perf_counter() - phase_start) * 1000.0, 1),
                    "phase": "started",
                },
            )

            if enhancement_hint.get("database_capability_discovery") == "true":
                try:
                    from types import SimpleNamespace

                    from backend.src.engine.decision_graph import _build_unmapped_database_capability_report

                    report_markdown = _build_unmapped_database_capability_report(
                        {
                            "user_query": request.message,
                            "user_context": SimpleNamespace(product=None, period=request.message),
                            "user_prefs": effective_prefs,
                            "resolved_chat_mode": timeout_mode,
                        }
                    )
                except Exception as exc:
                    yield _sse("error", {"message": str(exc)})
                    return

                enhancement_applied = dict(enhancement_hint)
                llm_route = _resolve_response_llm_route(
                    task_type="erp_data_query",
                    enhancement_applied=enhancement_applied,
                    prefs=effective_prefs,
                )
                report_context = _attach_llm_route_to_context({}, llm_route)
                report_context = _attach_llm_branching_to_context(
                    report_context,
                    enhancement_applied,
                )
                payload = {
                    "markdown": _ensure_non_empty_markdown(
                        report_markdown,
                        {},
                        request.message,
                    ),
                    "status": "success",
                    "context": report_context,
                    "task_type": "erp_data_query",
                    "session_id": session_id,
                    "error_message": None,
                    "llm_route": llm_route,
                    "resolved_chat_mode": timeout_mode,
                    "response_mode": "structured",
                    "mode_reason": None,
                    "quality_flags": [],
                    "alerts_prepended": False,
                    "enhancement_applied": enhancement_applied,
                }
                try:
                    payload["context"] = dict(payload.get("context") or {})
                    payload["context"]["_stream"] = dict(payload["context"].get("_stream") or {})
                    payload["context"]["_stream"]["display_markdown_source"] = "database_capability_discovery"
                    payload["context"]["_perf"] = {
                        "prep_ms": round(float((time.perf_counter() - phase_start) * 1000.0), 1),
                        "llm_ms": 0.0,
                        "first_delta_ms": None,
                        "total_ms": round(float((time.perf_counter() - total_start) * 1000.0), 1),
                    }
                except Exception:
                    pass
                payload = _attach_hermes_plan_to_payload(payload, hermes_plan_metadata)

                db.add_message(
                    session_id,
                    "assistant",
                    payload["markdown"],
                    context=payload["context"],
                )
                if payload["markdown"]:
                    yield _sse("delta", {"delta": payload["markdown"]})
                yield _sse("done", payload)
                return

            if enhancement_hint.get("deterministic_erp_order_details") == "true":
                try:
                    from types import SimpleNamespace

                    from backend.src.engine.decision_graph import _build_deterministic_erp_sales_order_detail_report

                    report_markdown = _build_deterministic_erp_sales_order_detail_report(
                        {
                            "user_query": request.message,
                            "user_context": SimpleNamespace(product=None, period=request.message),
                            "user_prefs": effective_prefs,
                            "resolved_chat_mode": timeout_mode,
                            "hermes_plan": hermes_plan_metadata,
                        }
                    )
                except Exception as exc:
                    yield _sse("error", {"message": str(exc)})
                    return

                enhancement_applied = dict(enhancement_hint)
                llm_route = _resolve_response_llm_route(
                    task_type="erp_data_query",
                    enhancement_applied=enhancement_applied,
                    prefs=effective_prefs,
                )
                report_context = _attach_llm_route_to_context({}, llm_route)
                report_context = _attach_llm_branching_to_context(
                    report_context,
                    enhancement_applied,
                )
                payload = {
                    "markdown": _ensure_non_empty_markdown(
                        report_markdown,
                        {},
                        request.message,
                    ),
                    "status": "success",
                    "context": report_context,
                    "task_type": "erp_data_query",
                    "session_id": session_id,
                    "error_message": None,
                    "llm_route": llm_route,
                    "resolved_chat_mode": timeout_mode,
                    "response_mode": "structured",
                    "mode_reason": None,
                    "quality_flags": [],
                    "alerts_prepended": False,
                    "enhancement_applied": enhancement_applied,
                }
                try:
                    payload["context"] = dict(payload.get("context") or {})
                    payload["context"]["_stream"] = dict(payload["context"].get("_stream") or {})
                    payload["context"]["_stream"]["display_markdown_source"] = "deterministic_erp_order_details"
                    payload["context"]["_perf"] = {
                        "prep_ms": round(float((time.perf_counter() - phase_start) * 1000.0), 1),
                        "llm_ms": 0.0,
                        "first_delta_ms": None,
                        "total_ms": round(float((time.perf_counter() - total_start) * 1000.0), 1),
                    }
                except Exception:
                    pass
                payload = _attach_hermes_plan_to_payload(payload, hermes_plan_metadata)

                db.add_message(
                    session_id,
                    "assistant",
                    payload["markdown"],
                    context=payload["context"],
                )
                if payload["markdown"]:
                    yield _sse("delta", {"delta": payload["markdown"]})
                yield _sse("done", payload)
                return

            if enhancement_hint.get("database_capability") == "erp_sales_summary":
                try:
                    from types import SimpleNamespace

                    from backend.src.domain.product_resolver import extract_product_ids_in_text
                    from backend.src.engine.decision_graph import (
                        TaskType,
                        _build_deterministic_erp_sales_report,
                    )
                    from backend.src.tools.erp_tools import build_erp_context

                    product_ids = extract_product_ids_in_text(request.message)
                    product_id = product_ids[0] if product_ids else None
                    erp_data = build_erp_context(
                        product_id=product_id,
                        period=request.message,
                        query_type="sales",
                        user_query=request.message,
                    )
                    report_markdown = _build_deterministic_erp_sales_report(
                        {
                            "parsed_intent": SimpleNamespace(task_type=TaskType.ERP_DATA_QUERY),
                            "user_query": request.message,
                            "user_context": SimpleNamespace(product=product_id, period=request.message),
                            "user_prefs": effective_prefs,
                            "resolved_chat_mode": timeout_mode,
                            "erp_data": erp_data,
                        }
                    )
                    if not str(report_markdown or "").strip():
                        from backend.src.hermes.erp_grounding import build_erp_sales_report_markdown

                        report_markdown = build_erp_sales_report_markdown(
                            request.message,
                            effective_prefs,
                        ) or str(erp_data or "")
                    if report_markdown and llm is not None:
                        report_markdown = _maybe_apply_erp_hybrid_markdown(
                            llm,
                            request.message,
                            str(report_markdown),
                            effective_prefs,
                        )
                    from backend.src.engine.decision_graph import _extract_erp_sales_summary_rows

                    erp_summary = _extract_erp_sales_summary_rows(str(erp_data or ""))
                except Exception as exc:
                    yield _sse("error", {"message": str(exc)})
                    return

                enhancement_applied = dict(enhancement_hint)
                enhancement_applied["external_llm_blocked"] = "true"
                enhancement_applied["skip_finalizer"] = "true"
                enhancement_applied["deterministic_erp_sales"] = "true"
                llm_route = _resolve_response_llm_route(
                    task_type="erp_data_query",
                    enhancement_applied=enhancement_applied,
                    prefs=effective_prefs,
                )
                stream_language = str(effective_prefs.get("language") or response_language or "zh")
                base_context: Dict[str, Any] = {
                    "erp_data": erp_data,
                    "erp_summary": erp_summary,
                }
                report_context = _build_response_context(
                    report_context=base_context,
                    message=request.message,
                    task_type="erp_data_query",
                    language=stream_language,
                    raw_markdown=report_markdown,
                    resolved_chat_mode=timeout_mode,
                )
                report_context = _attach_llm_route_to_context(report_context, llm_route)
                report_context = _attach_llm_branching_to_context(
                    report_context,
                    enhancement_applied,
                )
                payload = {
                    "markdown": _ensure_non_empty_markdown(
                        report_markdown,
                        {},
                        request.message,
                    ),
                    "status": "success",
                    "context": report_context,
                    "task_type": "erp_data_query",
                    "session_id": session_id,
                    "error_message": None,
                    "llm_route": llm_route,
                    "resolved_chat_mode": timeout_mode,
                    "response_mode": "structured",
                    "mode_reason": None,
                    "quality_flags": [],
                    "alerts_prepended": False,
                    "enhancement_applied": enhancement_applied,
                }
                try:
                    payload["context"] = dict(payload.get("context") or {})
                    payload["context"]["_stream"] = dict(payload["context"].get("_stream") or {})
                    payload["context"]["_stream"]["display_markdown_source"] = "deterministic_erp_sales_summary"
                    payload["context"]["_perf"] = {
                        "prep_ms": round(float((time.perf_counter() - phase_start) * 1000.0), 1),
                        "llm_ms": 0.0,
                        "first_delta_ms": None,
                        "total_ms": round(float((time.perf_counter() - total_start) * 1000.0), 1),
                    }
                except Exception:
                    pass
                payload = _attach_hermes_plan_to_payload(payload, hermes_plan_metadata)

                db.add_message(
                    session_id,
                    "assistant",
                    payload["markdown"],
                    context=payload["context"],
                )
                if payload["markdown"]:
                    yield _sse("delta", {"delta": payload["markdown"]})
                yield _sse("done", payload)
                return

            if enhancement_hint.get("database_capability") == "erp_customer_sales":
                try:
                    from backend.src.domain.product_resolver import extract_product_ids_in_text
                    from backend.src.tools.erp_tools import build_customer_sales_report_markdown

                    product_ids = extract_product_ids_in_text(request.message)
                    product_id = product_ids[0] if product_ids else None
                    report_markdown = build_customer_sales_report_markdown(
                        product_id=product_id,
                        period=request.message,
                        language=str(effective_prefs.get("language") or "zh"),
                    )
                except Exception as exc:
                    yield _sse("error", {"message": str(exc)})
                    return

                enhancement_applied = dict(enhancement_hint)
                enhancement_applied["external_llm_blocked"] = "true"
                enhancement_applied["skip_finalizer"] = "true"
                enhancement_applied["deterministic_erp_customer_sales"] = "true"
                llm_route = _resolve_response_llm_route(
                    task_type="erp_data_query",
                    enhancement_applied=enhancement_applied,
                    prefs=effective_prefs,
                )
                report_context = _attach_llm_route_to_context({}, llm_route)
                report_context = _attach_llm_branching_to_context(
                    report_context,
                    enhancement_applied,
                )
                payload = {
                    "markdown": _ensure_non_empty_markdown(
                        report_markdown,
                        {},
                        request.message,
                    ),
                    "status": "success",
                    "context": report_context,
                    "task_type": "erp_data_query",
                    "session_id": session_id,
                    "error_message": None,
                    "llm_route": llm_route,
                    "resolved_chat_mode": timeout_mode,
                    "response_mode": "structured",
                    "mode_reason": None,
                    "quality_flags": [],
                    "alerts_prepended": False,
                    "enhancement_applied": enhancement_applied,
                }
                try:
                    payload["context"] = dict(payload.get("context") or {})
                    payload["context"]["_stream"] = dict(payload["context"].get("_stream") or {})
                    payload["context"]["_stream"]["display_markdown_source"] = "deterministic_erp_customer_sales"
                    payload["context"]["_perf"] = {
                        "prep_ms": round(float((time.perf_counter() - phase_start) * 1000.0), 1),
                        "llm_ms": 0.0,
                        "first_delta_ms": None,
                        "total_ms": round(float((time.perf_counter() - total_start) * 1000.0), 1),
                    }
                except Exception:
                    pass
                payload = _attach_hermes_plan_to_payload(payload, hermes_plan_metadata)

                db.add_message(
                    session_id,
                    "assistant",
                    payload["markdown"],
                    context=payload["context"],
                )
                if payload["markdown"]:
                    yield _sse("delta", {"delta": payload["markdown"]})
                yield _sse("done", payload)
                return

            from backend.src.hermes.erp_grounding import build_erp_sales_report_markdown

            facts_fallback = build_erp_sales_report_markdown(
                request.message,
                effective_prefs,
            )
            if facts_fallback and (
                enhancement_hint.get("deterministic_erp_sales") == "true"
                or enhancement_hint.get("database_capability") == "erp_sales_summary"
                or _is_erp_report_stream_query(request.message)
            ):
                enhancement_applied = dict(enhancement_hint)
                enhancement_applied["external_llm_blocked"] = "true"
                enhancement_applied["skip_finalizer"] = "true"
                report_markdown = facts_fallback
                if llm is not None:
                    report_markdown = _maybe_apply_erp_hybrid_markdown(
                        llm,
                        request.message,
                        str(report_markdown),
                        effective_prefs,
                    )
                llm_route = _resolve_response_llm_route(
                    task_type="erp_data_query",
                    enhancement_applied=enhancement_applied,
                    prefs=effective_prefs,
                )
                report_context = _attach_llm_route_to_context(
                    {"erp_data_available": True},
                    llm_route,
                )
                report_context = _attach_llm_branching_to_context(
                    report_context,
                    enhancement_applied,
                )
                payload = {
                    "markdown": _ensure_non_empty_markdown(
                        report_markdown,
                        {},
                        request.message,
                    ),
                    "status": "success",
                    "context": report_context,
                    "task_type": "erp_data_query",
                    "session_id": session_id,
                    "error_message": None,
                    "llm_route": llm_route,
                    "resolved_chat_mode": timeout_mode,
                    "response_mode": "structured",
                    "mode_reason": "deterministic_erp_facts_fallback",
                    "quality_flags": [],
                    "alerts_prepended": False,
                    "enhancement_applied": enhancement_applied,
                }
                try:
                    payload["context"] = dict(payload.get("context") or {})
                    payload["context"]["_stream"] = dict(payload["context"].get("_stream") or {})
                    payload["context"]["_stream"]["display_markdown_source"] = "deterministic_erp_facts_fallback"
                except Exception:
                    pass
                payload = _attach_hermes_plan_to_payload(payload, hermes_plan_metadata)
                db.add_message(
                    session_id,
                    "assistant",
                    payload["markdown"],
                    context=payload["context"],
                )
                if payload["markdown"]:
                    yield _sse("delta", {"delta": payload["markdown"]})
                yield _sse("done", payload)
                return

            # ── Phase 2 RAG: retrieve knowledge-base context ──────────────────────
            rag_context = ""
            try:
                from backend.src.rag.rag_retriever import retrieve_with_context
                rag_context = retrieve_with_context(str(request.message or ""), n_results=3) or ""
            except Exception:
                pass  # Non-blocking: RAG is best-effort
            if rag_context:
                # Prepend RAG context to the message so the LLM sees it as grounding.
                effective_message = (
                    f"{rag_context}\n\n---\n\n[User Question]\n{effective_message}"
                )

            # ── User Memory Recall: inject relevant prior context ──────────────────
            user_memory_context = ""
            try:
                from backend.src.rag.user_memory_retriever import UserMemoryRetriever
                from backend.src.rag.vector_store import kb as _user_kb

                retriever = UserMemoryRetriever(_user_kb)
                user_memory_context = retriever.build_context_block(
                    user_id=user_id,
                    query=effective_message,
                    top_k=5,
                )
            except Exception:
                pass  # Non-blocking: user memory is best-effort

            if user_memory_context:
                effective_message = (
                    f"{user_memory_context}\n\n---\n\n{effective_message}"
                )

            try:
                report = decision_service.process_request(
                    effective_message,
                    conversation_history=prior_messages,
                    user_prefs=effective_prefs,
                    user_id=user_id,
                    session_id=session_id,
                    hermes_plan=hermes_plan_metadata,
                )
            except Exception as exc:
                yield _sse("error", {"message": str(exc)})
                return

            enhancement_applied = dict(report.enhancement_applied or {})
            enhancement_applied.update(enhancement_hint)
            enhancement_applied = _apply_hermes_privacy_to_enhancement(
                enhancement_applied,
                hermes_plan_metadata,
            )
            report_markdown = report.raw_markdown
            if enhancement_applied.get("deterministic_erp_row_level_guard") == "true":
                try:
                    from backend.src.engine.decision_graph import _build_deterministic_erp_row_level_detail_guard_report

                    guard_markdown = _build_deterministic_erp_row_level_detail_guard_report(
                        {
                            "user_query": request.message,
                            "user_prefs": effective_prefs,
                            "resolved_chat_mode": timeout_mode,
                        }
                    )
                    if guard_markdown:
                        report_markdown = guard_markdown
                except Exception:
                    pass
            if (
                enhancement_applied.get("deterministic_erp_stream") == "true"
                or enhancement_applied.get("deterministic_erp_sales") == "true"
                or enhancement_hint.get("deterministic_erp_sales") == "true"
            ):
                from backend.src.hermes.erp_grounding import (
                    build_erp_sales_report_markdown,
                    guard_erp_markdown_against_denial,
                    looks_like_erp_access_denial,
                )

                facts_markdown = build_erp_sales_report_markdown(
                    request.message,
                    effective_prefs,
                ) or str(report_markdown or "")
                if looks_like_erp_access_denial(str(report_markdown or "")):
                    report_markdown = facts_markdown
                elif llm is not None and str(report_markdown or "").strip():
                    report_markdown = _maybe_apply_erp_hybrid_markdown(
                        llm,
                        request.message,
                        str(report_markdown),
                        effective_prefs,
                    )
                elif llm is not None and facts_markdown:
                    report_markdown = _maybe_apply_erp_hybrid_markdown(
                        llm,
                        request.message,
                        facts_markdown,
                        effective_prefs,
                    )
                report_markdown = guard_erp_markdown_against_denial(
                    str(report_markdown or ""),
                    facts_markdown,
                    has_erp_data=bool(str(facts_markdown or "").strip()),
                )
            llm_route = _resolve_response_llm_route(
                task_type=report.task_type.value,
                enhancement_applied=enhancement_applied,
                prefs=effective_prefs,
            )
            report_context = _attach_llm_route_to_context(
                report.context.to_dict(),
                llm_route,
            )
            report_context = _attach_llm_branching_to_context(
                report_context,
                enhancement_applied,
            )
            payload = {
                "markdown": _ensure_non_empty_markdown(
                    report_markdown,
                    {},
                    request.message,
                ),
                "status": report.status,
                "context": report_context,
                "task_type": report.task_type.value,
                "session_id": session_id,
                "error_message": report.error_message,
                "llm_route": llm_route,
                "resolved_chat_mode": report.resolved_chat_mode,
                "response_mode": "structured",
                "mode_reason": report.mode_reason,
                "quality_flags": [
                    {
                        "code": f.code,
                        "severity": f.severity,
                        "message": f.message,
                        "source": f.source,
                    }
                    for f in report.quality_flags
                ],
                "alerts_prepended": report.alerts_prepended,
                "enhancement_applied": enhancement_applied,
            }
            try:
                payload["context"] = dict(payload.get("context") or {})
                payload["context"]["_stream"] = dict(payload["context"].get("_stream") or {})
                payload["context"]["_stream"]["display_markdown_source"] = "deterministic_erp_stream"
                payload["context"]["_perf"] = {
                    "prep_ms": round(float((time.perf_counter() - phase_start) * 1000.0), 1),
                    "llm_ms": 0.0,
                    "first_delta_ms": None,
                    "total_ms": round(float((time.perf_counter() - total_start) * 1000.0), 1),
                }
            except Exception:
                pass
            payload = _attach_hermes_plan_to_payload(payload, hermes_plan_metadata)

            db.add_message(
                session_id,
                "assistant",
                payload["markdown"],
                context=payload["context"],
            )
            _write_session_memory(
                user_id=user_id,
                session_id=session_id,
                user_message=request.message,
                task_type=report.task_type.value,
                hermes_plan=hermes_plan_metadata,
            )
            if payload["markdown"]:
                yield _sse("delta", {"delta": payload["markdown"]})
            payload["confidence"] = _build_confidence_payload(report)
            payload["suggestions"] = _extract_suggestions_from_markdown(
                payload["markdown"], language=response_language
            )
            payload["citations"] = list(getattr(report, "citations", []) or [])
            yield _sse("done", payload)
            return

        # --- Run prepare_request in a background thread so we can stream
        # stage events to the client while the pipeline executes. ---
        fast_preview_sent = False

        def _maybe_emit_fast_preview(prep_state_for_preview: Dict[str, Any]) -> Optional[str]:
            nonlocal fast_preview_sent
            if fast_preview_sent or deterministic_erp_stream:
                return None
            preview = _build_fast_start_preview(prep_state_for_preview, request.message)
            if not preview:
                return None
            fast_preview_sent = True
            prep_state_for_preview.setdefault("_stream", {})
            if isinstance(prep_state_for_preview.get("_stream"), dict):
                prep_state_for_preview["_stream"]["fast_preview_sent"] = True
            return preview

        stage_q: _queue.SimpleQueue = _queue.SimpleQueue()
        prep_result: Dict[str, Any] = {}
        prep_error: List[str] = []
        prep_timing: List[float] = []
        prep_preview = ""
        prep_timeout_seconds = _chat_stream_prepare_timeout_seconds()
        prep_hard_timeout_seconds = _chat_stream_prepare_hard_timeout_seconds()
        prep_force_synthesis_seconds = _chat_stream_prepare_force_synthesis_seconds()
        if deterministic_erp_stream:
            prep_force_synthesis_seconds = max(
                prep_force_synthesis_seconds,
                prep_hard_timeout_seconds + 30.0,
            )
        prep_timeout_warned = False
        forced_synthesis_started = False

        def _stage_cb(node_name: str, elapsed_ms: float, phase: str = "completed") -> None:
            label = _localized_stage_label(node_name, response_language)
            stage_q.put(
                {
                    "stage": node_name,
                    "label": label,
                    "elapsed_ms": elapsed_ms,
                    "phase": phase,
                }
            )

        def _run_prep() -> None:
            t0 = time.perf_counter()
            try:
                result = decision_service.prepare_request(
                    effective_message,
                    conversation_history=prior_messages,
                    user_prefs=effective_prefs,
                    user_id=user_id,
                    session_id=session_id,
                    stage_callback=_stage_cb,
                    hermes_plan=hermes_plan_metadata,
                )
                prep_result.update(result)
                prep_timing.append((time.perf_counter() - t0) * 1000.0)
            except Exception as e:
                prep_error.append(str(e))
            finally:
                stage_q.put(None)  # sentinel: pipeline finished

        prep_thread = _threading.Thread(target=_run_prep, daemon=True)
        prep_start = time.perf_counter()
        prep_thread.start()
        yield _sse(
            "stage",
            {
                "stage": "analyzing",
                "label": _localized_stream_message(response_language, "stage_analyzing"),
                "elapsed_ms": round((time.perf_counter() - phase_start) * 1000.0, 1),
            },
        )
        # Drain stage events until we get the sentinel (None)
        prep_preview_timeout = max(
            0.0,
            _local_fast_start_target_seconds() - (time.perf_counter() - total_start),
        )
        last_wait_stage_emit = 0.0
        while True:
            elapsed_prepare = time.perf_counter() - prep_start
            if (
                not forced_synthesis_started
                and elapsed_prepare >= prep_force_synthesis_seconds
            ):
                forced_synthesis_started = True
                yield _sse(
                    "stage",
                    {
                        "stage": "preparing",
                        "label": _localized_stream_message(response_language, "prep_force_synthesis"),
                        "elapsed_ms": round(elapsed_prepare * 1000.0, 1),
                        "phase": "started",
                    },
                )

                harvested = _harvest_forced_synthesis_context(
                    effective_message,
                    effective_prefs,
                    hermes_plan=hermes_plan_metadata,
                    prep_thread=prep_thread,
                    prep_result=prep_result,
                )
                if harvested is not None:
                    harvested_markdown = str(harvested.get("markdown") or "").strip()
                    if harvested_markdown:
                        enhancement_applied = _apply_hermes_privacy_to_enhancement(
                            dict(harvested.get("enhancement_applied") or {}),
                            hermes_plan_metadata,
                        )
                        llm_route = _resolve_response_llm_route(
                            task_type=str(harvested.get("task_type") or "analysis"),
                            enhancement_applied=enhancement_applied,
                            prefs=effective_prefs,
                        )
                        response_context = _attach_llm_route_to_context(
                            harvested.get("context")
                            if isinstance(harvested.get("context"), dict)
                            else {},
                            llm_route,
                        )
                        response_context = _attach_llm_branching_to_context(
                            response_context,
                            enhancement_applied,
                        )
                        response_context.setdefault("_perf", {})
                        response_context["_perf"] = {
                            "prep_ms": round(elapsed_prepare * 1000.0, 1),
                            "llm_ms": 0.0,
                            "first_delta_ms": round(
                                (time.perf_counter() - total_start) * 1000.0, 1
                            ),
                            "total_ms": round(
                                (time.perf_counter() - total_start) * 1000.0, 1
                            ),
                        }
                        payload = {
                            "markdown": harvested_markdown,
                            "status": str(harvested.get("status") or "success"),
                            "context": response_context,
                            "task_type": str(harvested.get("task_type") or "analysis"),
                            "session_id": session_id,
                            "error_message": None,
                            "llm_route": llm_route,
                            "resolved_chat_mode": str(
                                harvested.get("resolved_chat_mode")
                                or effective_prefs.get("chat_mode")
                                or "analysis"
                            ),
                            "response_mode": "structured",
                            "mode_reason": str(
                                harvested.get("mode_reason")
                                or "forced_synthesis_deterministic"
                            ),
                            "quality_flags": [],
                            "alerts_prepended": False,
                            "enhancement_applied": enhancement_applied,
                        }
                        payload = _attach_hermes_plan_to_payload(payload, hermes_plan_metadata)
                        for chunk in _iter_stream_chunks(harvested_markdown, max_chars=120):
                            yield _sse("delta", {"delta": chunk})
                        db.add_message(
                            session_id,
                            "assistant",
                            harvested_markdown,
                            context=payload["context"],
                        )
                        yield _sse("done", payload)
                        return

                prep_result.setdefault(
                    "system_prompt",
                    "You are EDIA assistant. Provide a grounded answer based on available market context."
                )
                prep_result.setdefault("user_prompt", effective_message)
                prep_result.setdefault("resolved_chat_mode", effective_prefs.get("chat_mode"))
                prep_result.setdefault("mode_reason", "forced_synthesis_after_prepare_dwell")
                break

            if elapsed_prepare >= prep_timeout_seconds:
                if not prep_timeout_warned:
                    prep_timeout_warned = True
                    preview_delta = _maybe_emit_fast_preview(prep_result)
                    if preview_delta:
                        yield _sse("delta", {"delta": preview_delta})
                    yield _sse(
                        "stage",
                        {
                            "stage": "thinking",
                            "label": _localized_stream_message(response_language, "prep_still_waiting"),
                            "elapsed_ms": round(elapsed_prepare * 1000.0, 1),
                            "phase": "started",
                        },
                    )

                if elapsed_prepare >= prep_hard_timeout_seconds:
                    timeout_fast = _try_erp_facts_then_kimi_stream_report(
                        effective_message,
                        effective_prefs,
                        hermes_plan=hermes_plan_metadata,
                        deterministic_erp_stream=deterministic_erp_stream,
                    )
                    if timeout_fast is None:
                        timeout_fast = _try_fast_naphtha_passthrough_stream_report(
                            effective_message,
                            effective_prefs,
                            hermes_plan=hermes_plan_metadata,
                        )
                    if timeout_fast is None:
                        timeout_fast = _try_fast_ops_inventory_stream_report(
                            effective_message,
                            effective_prefs,
                            hermes_plan=hermes_plan_metadata,
                        )
                    if timeout_fast is not None:
                        timeout_markdown = str(timeout_fast.get("markdown") or "").strip()
                        if timeout_markdown:
                            enhancement_applied = _apply_hermes_privacy_to_enhancement(
                                dict(timeout_fast.get("enhancement_applied") or {}),
                                hermes_plan_metadata,
                            )
                            llm_route = _resolve_response_llm_route(
                                task_type=str(timeout_fast.get("task_type") or "analysis"),
                                enhancement_applied=enhancement_applied,
                                prefs=effective_prefs,
                            )
                            response_context = _attach_llm_route_to_context(
                                timeout_fast.get("context")
                                if isinstance(timeout_fast.get("context"), dict)
                                else {},
                                llm_route,
                            )
                            response_context.setdefault("_perf", {})
                            response_context["_perf"] = {
                                "prep_ms": round(float(elapsed_prepare * 1000.0), 1),
                                "llm_ms": 0.0,
                                "total_ms": round(
                                    float((time.perf_counter() - total_start) * 1000.0), 1
                                ),
                            }
                            payload = {
                                "markdown": timeout_markdown,
                                "status": "success",
                                "context": response_context,
                                "task_type": str(timeout_fast.get("task_type") or "analysis"),
                                "session_id": session_id,
                                "error_message": None,
                                "llm_route": llm_route,
                                "resolved_chat_mode": effective_prefs.get("chat_mode"),
                                "mode_reason": "prep_hard_timeout_fast_deterministic",
                                "quality_flags": [],
                                "alerts_prepended": False,
                                "enhancement_applied": enhancement_applied,
                            }
                            payload = _attach_hermes_plan_to_payload(payload, hermes_plan_metadata)
                            for chunk in _iter_stream_chunks(timeout_markdown, max_chars=120):
                                yield _sse("delta", {"delta": chunk})
                            db.add_message(
                                session_id,
                                "assistant",
                                timeout_markdown,
                                context=payload["context"],
                            )
                            yield _sse("done", payload)
                            return

                    partial_context = {
                        "llm_route": _resolve_final_llm_route_hint(effective_prefs),
                        "_stream": {
                            "display_markdown_source": "prep_timeout_preview",
                            "prep_timeout_seconds": round(float(prep_timeout_seconds), 1),
                            "prep_hard_timeout_seconds": round(float(prep_hard_timeout_seconds), 1),
                        },
                        "_perf": {
                            "prep_ms": round(float(elapsed_prepare * 1000.0), 1),
                            "llm_ms": None,
                            "first_delta_ms": round(float(_local_fast_start_target_seconds() * 1000.0), 1)
                            if fast_preview_sent
                            else None,
                            "total_ms": round(float((time.perf_counter() - total_start) * 1000.0), 1),
                        },
                    }
                    payload = {
                        "markdown": _localized_stream_message(response_language, "prep_timeout_preview"),
                        "status": "partial",
                        "context": partial_context,
                        "task_type": "analysis",
                        "session_id": session_id,
                        "error_message": _localized_stream_message(response_language, "prep_timeout_error"),
                        "llm_route": partial_context["llm_route"],
                        "resolved_chat_mode": effective_prefs.get("chat_mode"),
                        "response_mode": str(effective_prefs.get("default_format") or ""),
                        "mode_reason": "prep_timeout_preview",
                        "quality_flags": [],
                        "alerts_prepended": [],
                        "enhancement_applied": {"final_llm_route": str(partial_context["llm_route"] or "")},
                    }
                    payload = _attach_hermes_plan_to_payload(payload, hermes_plan_metadata)
                    db.add_message(
                        session_id,
                        "assistant",
                        payload["markdown"],
                        context=payload["context"],
                    )
                    yield _sse("done", payload)
                    return
            remaining_prepare_budget = max(0.0, prep_timeout_seconds - elapsed_prepare)
            queue_wait_timeout = prep_preview_timeout
            if fast_preview_sent:
                queue_wait_timeout = 1.0
            try:
                item = stage_q.get(timeout=queue_wait_timeout)
            except _queue.Empty:
                if elapsed_prepare - last_wait_stage_emit >= 4.0:
                    last_wait_stage_emit = elapsed_prepare
                    yield _sse(
                        "stage",
                        {
                            "stage": "thinking",
                            "label": (
                                "Thinking... selecting data sources and preparing analysis"
                                if response_language == "en"
                                else "正在思考并选择数据源，准备分析内容"
                            ),
                            "elapsed_ms": round(elapsed_prepare * 1000.0, 1),
                            "phase": "started",
                        },
                    )
                preview_delta = _maybe_emit_fast_preview(prep_result)
                if preview_delta:
                    yield _sse("delta", {"delta": preview_delta})
                prep_preview_timeout = 1.0
                continue
            if item is None:
                break
            yield _sse("stage", item)

        if not forced_synthesis_started:
            prep_thread.join()
        prep_ms = prep_timing[0] if prep_timing else (time.perf_counter() - prep_start) * 1000.0

        if prep_error and not forced_synthesis_started:
            yield _sse("error", {"message": prep_error[0]})
            return

        prep_state = prep_result
        yield _sse(
            "stage",
            {
                "stage": "thinking_summary",
                "label": _build_thinking_stage_label(prep_state, request.message),
                "elapsed_ms": round((time.perf_counter() - phase_start) * 1000.0, 1),
                "phase": "completed",
            },
        )

        use_dual_model_stream = (
            bool(effective_prefs.get("stream_dual_model_report", False))
            and bool(effective_prefs.get("kimi_mixed_synthesis", False))
            and prep_state.get("report") is None
            and str(
                prep_state.get("resolved_chat_mode")
                or effective_prefs.get("chat_mode")
                or ""
            ).strip().lower()
            in {"analysis", "report", "deep_research"}
        )

        if use_dual_model_stream:
            yield _sse(
                "stage",
                {
                    "stage": "dual_model_synthesis",
                    "label": _localized_stream_message(response_language, "stage_dual_model_synthesis"),
                    "elapsed_ms": round((time.perf_counter() - phase_start) * 1000.0, 1),
                },
            )
            mixed_q: _queue.SimpleQueue = _queue.SimpleQueue()

            def _run_mixed() -> None:
                try:
                    report = decision_service.process_request(
                        effective_message,
                        conversation_history=prior_messages,
                        user_prefs=effective_prefs,
                        user_id=user_id,
                        session_id=session_id,
                        hermes_plan=hermes_plan_metadata,
                    )
                    mixed_q.put(("ok", report))
                except Exception as mixed_error:
                    mixed_q.put(("error", str(mixed_error)))

            mixed_thread = _threading.Thread(target=_run_mixed, daemon=True)
            mixed_thread.start()

            keepalive_seconds = _chat_stream_keepalive_seconds()
            stream_budget_seconds = _chat_stream_total_budget_seconds()
            mixed_report = None
            while True:
                if (time.perf_counter() - total_start) >= stream_budget_seconds:
                    break
                try:
                    kind, value = mixed_q.get(timeout=keepalive_seconds)
                except _queue.Empty:
                    yield _sse_comment()
                    continue
                if kind == "error":
                    yield _sse("error", {"message": str(value or "dual model synthesis failed")})
                    return
                if kind == "ok":
                    mixed_report = value
                    break

            if mixed_report is None:
                partial_markdown = _ensure_non_empty_markdown(
                    prep_preview or "",
                    prep_result,
                    request.message,
                )
                partial_context = {
                    "llm_route": _resolve_final_llm_route_hint(effective_prefs),
                    "_stream": {
                        "display_markdown_source": "stream_budget_partial",
                        "stream_budget_seconds": round(float(stream_budget_seconds), 1),
                    },
                    "_perf": {
                        "prep_ms": round(float(prep_ms), 1),
                        "llm_ms": None,
                        "first_delta_ms": None,
                        "total_ms": round(float((time.perf_counter() - total_start) * 1000.0), 1),
                    },
                }
                payload = {
                    "markdown": partial_markdown,
                    "status": "partial",
                    "context": partial_context,
                    "task_type": "analysis",
                    "session_id": session_id,
                    "error_message": _localized_stream_message(response_language, "llm_stream_budget_timeout"),
                    "llm_route": partial_context["llm_route"],
                    "resolved_chat_mode": effective_prefs.get("chat_mode"),
                    "mode_reason": "stream_budget_partial",
                    "quality_flags": [],
                    "alerts_prepended": [],
                    "enhancement_applied": {"final_llm_route": str(partial_context["llm_route"] or "")},
                }
                payload = _attach_hermes_plan_to_payload(payload, hermes_plan_metadata)
                db.add_message(
                    session_id,
                    "assistant",
                    payload["markdown"],
                    context=payload["context"],
                )
                yield _sse("done", payload)
                return

            mixed_enhancement_applied = _apply_hermes_privacy_to_enhancement(
                mixed_report.enhancement_applied,
                hermes_plan_metadata,
            )
            mixed_finalizer_allowed = _finalizer_allowed_by_privacy(
                finalizer_enabled,
                mixed_enhancement_applied,
                hermes_plan_metadata,
            )
            llm_route = _resolve_response_llm_route(
                task_type=mixed_report.task_type.value,
                enhancement_applied=mixed_enhancement_applied,
                prefs=effective_prefs,
            )
            if mixed_finalizer_allowed:
                llm_route = "external"
            report_context = _attach_llm_route_to_context(
                mixed_report.context.to_dict(),
                llm_route,
            )
            report_context = _attach_llm_branching_to_context(
                report_context,
                mixed_enhancement_applied,
            )
            payload = {
                "markdown": _ensure_non_empty_markdown(
                    mixed_report.raw_markdown,
                    prep_state,
                    request.message,
                ),
                "status": mixed_report.status,
                "context": report_context,
                "task_type": mixed_report.task_type.value,
                "session_id": session_id,
                "error_message": mixed_report.error_message,
                "llm_route": llm_route,
                "resolved_chat_mode": mixed_report.resolved_chat_mode,
                "response_mode": str(prep_state.get("response_mode") or ""),
                "mode_reason": mixed_report.mode_reason,
                "quality_flags": [
                    {
                        "code": f.code,
                        "severity": f.severity,
                        "message": f.message,
                        "source": f.source,
                    }
                    for f in mixed_report.quality_flags
                ],
                "alerts_prepended": mixed_report.alerts_prepended,
                "enhancement_applied": mixed_enhancement_applied,
            }
            try:
                payload["context"] = dict(payload.get("context") or {})
                payload["context"]["_stream"] = dict(payload["context"].get("_stream") or {})
                payload["context"]["_stream"]["display_markdown_source"] = "dual_model_report"
                payload["context"]["_perf"] = {
                    "prep_ms": round(float(prep_ms), 1),
                    "llm_ms": None,
                    "first_delta_ms": None,
                    "total_ms": round(float((time.perf_counter() - total_start) * 1000.0), 1),
                }
            except Exception:
                pass
            payload = _attach_hermes_plan_to_payload(payload, hermes_plan_metadata)

            mixed_has_content = bool(str(mixed_report.raw_markdown or "").strip())
            if mixed_report.status == "success" and mixed_has_content:
                if mixed_finalizer_allowed:
                    yield _sse(
                        "stage",
                        {
                            "stage": "final_synthesis",
                            "label": _localized_stream_message(response_language, "stage_final_synthesis"),
                            "elapsed_ms": round((time.perf_counter() - phase_start) * 1000.0, 1),
                        },
                    )
                    kimi_chunks: List[str] = []
                    for chunk in _finalize_with_kimi_stream(
                        llm=llm,
                        request_message=request.message,
                        draft_markdown=payload["markdown"],
                        task_type=str(mixed_report.task_type.value),
                        resolved_chat_mode=str(mixed_report.resolved_chat_mode or ""),
                    ):
                        if not chunk:
                            continue
                        kimi_chunks.append(chunk)
                        yield _sse("delta", {"delta": chunk})
                    finalized_text = "".join(kimi_chunks).strip()
                    if finalized_text:
                        from backend.src.engine.decision_graph import postprocess_generated_report

                        payload["markdown"] = postprocess_generated_report(
                            finalized_text,
                            prep_state,
                        )

                db.add_message(
                    session_id,
                    "assistant",
                    payload["markdown"],
                    context=payload["context"],
                )
                _write_session_memory(
                    user_id=user_id,
                    session_id=session_id,
                    user_message=request.message,
                    task_type=mixed_report.task_type.value,
                    hermes_plan=hermes_plan_metadata,
                )
                if payload["markdown"] and not mixed_finalizer_allowed:
                    yield _sse("delta", {"delta": payload["markdown"]})
                payload["confidence"] = _build_confidence_payload(mixed_report)
                payload["suggestions"] = _extract_suggestions_from_markdown(
                    payload["markdown"], language=response_language
                )
                payload["citations"] = list(getattr(mixed_report, "citations", []) or [])
                yield _sse("done", payload)
                return
            if mixed_report.status != "success":
                payload["suggestions"] = _extract_suggestions_from_markdown(
                    payload["markdown"], language=response_language
                )
                payload["citations"] = list(getattr(mixed_report, "citations", []) or [])
                yield _sse("done", payload)
                return

            # If dual-model returned success but empty markdown, continue into
            # final LLM synthesis instead of completing with preview-only text.
            yield _sse(
                "stage",
                {
                    "stage": "preparing",
                    "label": _localized_stream_message(response_language, "continue_dual_model_empty"),
                    "elapsed_ms": round((time.perf_counter() - phase_start) * 1000.0, 1),
                    "phase": "started",
                },
            )

        # Missing-field follow-up (non-stream path)
        report = prep_state.get("report")
        if report is not None:
            report_has_content = bool(str(getattr(report, "raw_markdown", "") or "").strip())
            if report.status == "success" and not report_has_content:
                yield _sse(
                    "stage",
                    {
                        "stage": "preparing",
                        "label": _localized_stream_message(response_language, "continue_report_empty"),
                        "elapsed_ms": round((time.perf_counter() - phase_start) * 1000.0, 1),
                        "phase": "started",
                    },
                )
            else:
                yield _sse(
                    "stage",
                    {
                        "stage": "preparing",
                        "label": _localized_stream_message(response_language, "stage_preparing"),
                        "elapsed_ms": round((time.perf_counter() - phase_start) * 1000.0, 1),
                    },
                )
                report_enhancement_applied = _apply_hermes_privacy_to_enhancement(
                    report.enhancement_applied,
                    hermes_plan_metadata,
                )
                report_finalizer_allowed = _finalizer_allowed_by_privacy(
                    finalizer_enabled,
                    report_enhancement_applied,
                    hermes_plan_metadata,
                )
                llm_route = _resolve_response_llm_route(
                    task_type=report.task_type.value,
                    enhancement_applied=report_enhancement_applied,
                    prefs=effective_prefs,
                )
                if report_finalizer_allowed:
                    llm_route = "external"
                report_context = _attach_llm_route_to_context(
                    report.context.to_dict(),
                    llm_route,
                )
                report_context = _attach_llm_branching_to_context(
                    report_context,
                    report_enhancement_applied,
                )
                payload = {
                    "markdown": _ensure_non_empty_markdown(
                        report.raw_markdown,
                        prep_state,
                        request.message,
                    ),
                    "status": report.status,
                    "context": report_context,
                    "task_type": report.task_type.value,
                    "session_id": session_id,
                    "error_message": report.error_message,
                    "llm_route": llm_route,
                    "resolved_chat_mode": report.resolved_chat_mode,
                    "response_mode": str(prep_state.get("response_mode") or ""),
                    "mode_reason": report.mode_reason,
                    "quality_flags": [
                        {
                            "code": f.code,
                            "severity": f.severity,
                            "message": f.message,
                            "source": f.source,
                        }
                        for f in report.quality_flags
                    ],
                    "alerts_prepended": report.alerts_prepended,
                    "enhancement_applied": report_enhancement_applied,
                }
                if report.status == "success":
                    if report_finalizer_allowed and str(payload["markdown"] or "").strip():
                        yield _sse(
                            "stage",
                            {
                                "stage": "final_synthesis",
                                "label": _localized_stream_message(response_language, "stage_final_synthesis"),
                                "elapsed_ms": round((time.perf_counter() - phase_start) * 1000.0, 1),
                            },
                        )
                        kimi_chunks: List[str] = []
                        for chunk in _finalize_with_kimi_stream(
                            llm=llm,
                            request_message=request.message,
                            draft_markdown=payload["markdown"],
                            task_type=str(report.task_type.value),
                            resolved_chat_mode=str(report.resolved_chat_mode or ""),
                        ):
                            if not chunk:
                                continue
                            kimi_chunks.append(chunk)
                            yield _sse("delta", {"delta": chunk})
                        finalized_text = "".join(kimi_chunks).strip()
                        if finalized_text:
                            from backend.src.engine.decision_graph import postprocess_generated_report

                            payload["markdown"] = postprocess_generated_report(
                                finalized_text,
                                prep_state,
                            )

                    db.add_message(
                        session_id,
                        "assistant",
                        payload["markdown"],
                        context=report_context,
                    )
                    if payload["markdown"] and not report_finalizer_allowed:
                        yield _sse("delta", {"delta": payload["markdown"]})
                payload["confidence"] = _build_confidence_payload(report)
                payload["suggestions"] = _extract_suggestions_from_markdown(
                    payload["markdown"], language=response_language
                )
                payload["citations"] = list(getattr(report, "citations", []) or [])
                yield _sse("done", payload)
                return

        sys_prompt = prep_state.get("system_prompt") or ""
        usr_prompt = prep_state.get("user_prompt") or ""

        prep_timings = (
            prep_state.get("timings") if isinstance(prep_state, dict) else None
        )
        prompt_stats = (
            prep_state.get("prompt_stats") if isinstance(prep_state, dict) else None
        )

        llm = getattr(decision_service, "llm", None)
        if not llm:
            yield _sse("error", {"message": _localized_stream_message(response_language, "llm_not_configured")})
            return

        llm_route_hint = _resolve_final_llm_route_hint(effective_prefs)
        llm_payload_sanitized = bool(effective_prefs.get("external_payload_sanitized", False))

        content_parts: list[str] = []
        try:
            llm_start = time.perf_counter()
            first_delta_ms: Optional[float] = None
            stream_budget_seconds = _chat_stream_total_budget_seconds()
            yield _sse(
                "stage",
                {
                    "stage": "preparing",
                    "label": _localized_stream_message(response_language, "stage_preparing"),
                    "elapsed_ms": round((time.perf_counter() - phase_start) * 1000.0, 1),
                },
            )
            planner_streaming_enabled = bool(
                effective_prefs.get("planner_streaming_synthesis", True)
            )
            planner_grounded = (
                str(prep_state.get("grounding_source") or "").strip().lower()
                == "planner"
            )

            if planner_streaming_enabled and planner_grounded:
                import asyncio

                planner_prompt = (
                    "You are EDIA final synthesis engine. Follow system instructions exactly.\n\n"
                    f"SYSTEM INSTRUCTIONS:\n{sys_prompt}\n\n"
                    f"USER CONTEXT:\n{usr_prompt}\n"
                )

                async def _consume_planner_stream() -> None:
                    async for delta in planner_synthesize_streaming(planner_prompt):
                        if not delta:
                            continue
                        content_parts.append(str(delta))

                loop = asyncio.new_event_loop()
                try:
                    ait = planner_synthesize_streaming(planner_prompt).__aiter__()
                    while True:
                        if (time.perf_counter() - total_start) >= stream_budget_seconds:
                            break
                        try:
                            delta = loop.run_until_complete(ait.__anext__())
                        except StopAsyncIteration:
                            break
                        if not delta:
                            continue
                        piece = str(delta)
                        content_parts.append(piece)
                        yield _sse("delta", {"delta": piece})
                finally:
                    loop.close()
            else:
                stream_fn = getattr(llm, "generate_completion_stream", None)
                if callable(stream_fn):
                    stream_q: _queue.SimpleQueue = _queue.SimpleQueue()

                    def _run_stream() -> None:
                        try:
                            for delta in stream_fn(
                                sys_prompt,
                                usr_prompt,
                                route_hint=llm_route_hint,
                                sanitized=llm_payload_sanitized,
                            ):
                                stream_q.put(("delta", delta))
                        except Exception as stream_error:
                            stream_q.put(("error", str(stream_error)))
                        finally:
                            stream_q.put(("done", None))

                    stream_thread = _threading.Thread(target=_run_stream, daemon=True)
                    stream_thread.start()

                    fast_start_target = _local_fast_start_target_seconds()
                    keepalive_seconds = _chat_stream_keepalive_seconds()
                    remaining_until_preview = max(
                        0.0,
                        fast_start_target - (time.perf_counter() - total_start),
                    )

                    while True:
                        if (time.perf_counter() - total_start) >= stream_budget_seconds:
                            break
                        try:
                            wait_timeout = (
                                remaining_until_preview
                                if not fast_preview_sent
                                else keepalive_seconds
                            )
                            kind, value = stream_q.get(timeout=wait_timeout)
                        except _queue.Empty:
                            if not fast_preview_sent:
                                preview = _maybe_emit_fast_preview(prep_state)
                                if preview:
                                    content_parts.append(preview)
                                    yield _sse("delta", {"delta": preview})
                                remaining_until_preview = None
                            else:
                                yield _sse_comment()
                            continue

                        if kind == "error":
                            raise RuntimeError(str(value or "Streaming generation failed"))
                        if kind == "done":
                            break

                        delta = str(value or "")
                        if not delta:
                            continue
                        for chunk in _iter_stream_chunks(delta):
                            if not chunk:
                                continue
                            if first_delta_ms is None:
                                first_delta_ms = (time.perf_counter() - llm_start) * 1000.0
                            content_parts.append(chunk)
                            yield _sse("delta", {"delta": chunk})
                else:
                    full = llm.generate_completion(
                        sys_prompt,
                        usr_prompt,
                        route_hint=llm_route_hint,
                        sanitized=llm_payload_sanitized,
                    )
                    first_delta_ms = (time.perf_counter() - llm_start) * 1000.0
                    for chunk in _iter_stream_chunks(str(full)):
                        if not chunk:
                            continue
                        content_parts.append(chunk)
                        yield _sse("delta", {"delta": chunk})

            budget_exhausted = (time.perf_counter() - total_start) >= stream_budget_seconds
            if budget_exhausted and not content_parts:
                preview = _maybe_emit_fast_preview(prep_state)
                if preview:
                    content_parts.append(preview)
            if budget_exhausted:
                llm_ms = (time.perf_counter() - llm_start) * 1000.0
                display_markdown = _ensure_non_empty_markdown(
                    "".join(content_parts),
                    prep_state,
                    request.message,
                )
                partial_context = _build_response_context(
                    report_context=prep_state.get("user_context"),
                    message=request.message,
                    task_type=getattr(getattr(prep_state.get("parsed_intent"), "task_type", None), "value", "unknown"),
                    language=str((request.prefs or {}).get("language") or "zh"),
                    raw_markdown=display_markdown,
                    resolved_chat_mode=str(prep_state.get("resolved_chat_mode") or ""),
                ) if prep_state.get("user_context") else {}
                partial_context = _attach_llm_route_to_context(partial_context, llm_route_hint)
                partial_context["_stream"] = dict(partial_context.get("_stream") or {})
                partial_context["_stream"]["display_markdown_source"] = "stream_budget_partial"
                partial_context["_stream"]["stream_budget_seconds"] = round(float(stream_budget_seconds), 1)
                partial_context["_perf"] = {
                    "prep_ms": round(float(prep_ms), 1),
                    "llm_ms": round(float(llm_ms), 1),
                    "first_delta_ms": round(float(first_delta_ms), 1) if first_delta_ms is not None else None,
                    "total_ms": round(float((time.perf_counter() - total_start) * 1000.0), 1),
                }
                payload = {
                    "markdown": display_markdown,
                    "status": "partial",
                    "context": partial_context,
                    "task_type": getattr(getattr(prep_state.get("parsed_intent"), "task_type", None), "value", "unknown"),
                    "session_id": session_id,
                    "error_message": _localized_stream_message(response_language, "llm_stream_budget_timeout"),
                    "llm_route": llm_route_hint,
                    "resolved_chat_mode": prep_state.get("resolved_chat_mode"),
                    "response_mode": str(prep_state.get("response_mode") or ""),
                    "mode_reason": "stream_budget_partial",
                    "quality_flags": [],
                    "alerts_prepended": [],
                    "enhancement_applied": {"final_llm_route": str(llm_route_hint or "")},
                }
                payload = _attach_hermes_plan_to_payload(payload, hermes_plan_metadata)
                db.add_message(
                    session_id,
                    "assistant",
                    display_markdown,
                    context=partial_context,
                )
                yield _sse("done", payload)
                return

            llm_ms = (time.perf_counter() - llm_start) * 1000.0

            from backend.src.engine.decision_graph import (
                postprocess_generated_report,
                _replace_transport_error_with_fallback,
            )

            raw_markdown = dedupe_fast_preview_markdown("".join(content_parts))
            raw_markdown = _replace_transport_error_with_fallback(raw_markdown, prep_state)
            final_markdown = postprocess_generated_report(raw_markdown, prep_state)

            parsed = prep_state.get("parsed_intent")
            task_type = getattr(parsed, "task_type", None)
            user_context = prep_state.get("user_context")

            quality_flags: List[dict] = []
            alerts_prepended: List[str] = []
            enhancement_applied: Dict[str, str] = {}

            is_conversational = (
                task_type is not None
                and hasattr(task_type, "value")
                and task_type.value in ("conversational", "out_of_domain")
            )

            if task_type is not None and user_context is not None and not is_conversational:
                report_obj = DecisionReport(
                    raw_markdown=final_markdown,
                    task_type=task_type,
                    context=user_context,
                    status="success",
                )
                critic_result = prep_state.get("critic_result") or {}
                report_obj.critic_level = str(critic_result.get("critic_level") or "pass")
                report_obj.critic_flags = list(critic_result.get("flags") or [])
                report_obj = apply_response_enhancement_skills(report_obj, prep_state)
                final_markdown = report_obj.raw_markdown
                quality_flags = [
                    {
                        "code": f.code,
                        "severity": f.severity,
                        "message": f.message,
                        "source": f.source,
                    }
                    for f in report_obj.quality_flags
                ]
                alerts_prepended = report_obj.alerts_prepended
                enhancement_applied = report_obj.enhancement_applied

            display_markdown = _pick_stream_display_markdown(raw_markdown, final_markdown)
            display_markdown = dedupe_fast_preview_markdown(
                _ensure_non_empty_markdown(
                    display_markdown,
                    prep_state,
                    request.message,
                )
            )

            payload = {
                "markdown": display_markdown,
                "status": "success",
                "context": _build_response_context(
                    report_context=user_context,
                    message=request.message,
                    task_type=task_type.value if task_type else "unknown",
                    language=str((request.prefs or {}).get("language") or "zh"),
                    raw_markdown=display_markdown,
                    resolved_chat_mode=str(prep_state.get("resolved_chat_mode") or ""),
                ) if user_context else {},
                "task_type": task_type.value if task_type else "unknown",
                "session_id": session_id,
                "resolved_chat_mode": prep_state.get("resolved_chat_mode"),
                "response_mode": str(prep_state.get("response_mode") or ""),
                "mode_reason": prep_state.get("mode_reason"),
                "quality_flags": quality_flags,
                "alerts_prepended": alerts_prepended,
                "enhancement_applied": enhancement_applied,
            }
            llm_route = _resolve_response_llm_route(
                task_type=task_type.value if task_type else "unknown",
                enhancement_applied=enhancement_applied,
                prefs=effective_prefs,
            )
            payload["context"] = _attach_llm_route_to_context(
                payload.get("context"),
                llm_route,
            )
            payload["context"] = _attach_llm_branching_to_context(
                payload.get("context"),
                enhancement_applied,
            )
            if "planner_sources_json" not in enhancement_applied:
                planner_payload = _planner_sources_payload_from_metrics(
                    prep_state.get("planner_metrics"),
                    str(prep_state.get("grounding_source") or "planner"),
                )
                if planner_payload:
                    enhancement_applied["grounding_source"] = str(
                        planner_payload.get("grounding_source") or "planner"
                    )
                    enhancement_applied["planner_sources_json"] = json.dumps(
                        planner_payload,
                        ensure_ascii=False,
                    )
            payload["context"] = _attach_planner_sources_to_context(
                payload.get("context"),
                enhancement_applied,
            )
            payload["context"] = _attach_response_quality_to_context(
                payload.get("context"),
                quality_flags,
                alerts_prepended,
                enhancement_applied,
            )
            payload["llm_route"] = llm_route

            try:
                payload["context"] = dict(payload.get("context") or {})
                payload["context"]["_stream"] = dict(payload["context"].get("_stream") or {})
                payload["context"]["_stream"]["display_markdown_source"] = (
                    "streamed_raw" if str(display_markdown).strip() == str(raw_markdown).strip() else "postprocessed"
                )
                if str(raw_markdown).strip() and str(final_markdown).strip() and str(raw_markdown).strip() != str(final_markdown).strip():
                    payload["context"]["_stream"]["postprocessed_markdown"] = final_markdown
                payload["context"]["_perf"] = {
                    "prep_ms": round(float(prep_ms), 1),
                    "llm_ms": round(float(llm_ms), 1),
                    "first_delta_ms": round(float(first_delta_ms), 1) if first_delta_ms is not None else None,
                    "total_ms": round(
                        float((time.perf_counter() - total_start) * 1000.0), 1
                    ),
                }
                if isinstance(prep_timings, dict):
                    payload["context"]["_timings"] = prep_timings
                if isinstance(prompt_stats, dict):
                    payload["context"]["_prompt_stats"] = prompt_stats
            except Exception:
                pass
            payload = _attach_hermes_plan_to_payload(payload, hermes_plan_metadata)

            db.add_message(
                session_id,
                "assistant",
                display_markdown,
                context=payload["context"],
            )
            _write_session_memory(
                user_id=user_id,
                session_id=session_id,
                user_message=request.message,
                task_type=task_type.value if task_type else "unknown",
                hermes_plan=hermes_plan_metadata,
            )
            _payload_report_obj = locals().get("report_obj")
            payload["confidence"] = _build_confidence_payload(_payload_report_obj)
            payload["suggestions"] = _extract_suggestions_from_markdown(
                payload["markdown"], language=response_language
            )
            payload["citations"] = list(
                getattr(_payload_report_obj, "citations", []) or []
            )
            yield _sse("done", payload)
        except Exception as e:
            yield _sse("error", {"message": str(e)})

    return StreamingResponse(
        event_iter(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/chat/context-file")
async def upload_chat_context_file(
    http_request: Request,
    file: UploadFile = File(...),
):
    _require_current_user(http_request)

    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing file name.")

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    if len(raw_bytes) > _CHAT_CONTEXT_MAX_BYTES:
        raise HTTPException(
            status_code=400, detail="File is too large. Maximum size is 5 MB."
        )

    content, excerpt, summary = _extract_chat_context_text(file.filename, raw_bytes)
    return {
        "status": "ok",
        "id": f"ctx-{uuid4().hex[:12]}",
        "filename": file.filename,
        "file_type": Path(file.filename).suffix.lower().lstrip("."),
        "size_bytes": len(raw_bytes),
        "content": content,
        "excerpt": excerpt,
        "summary": summary,
    }


@app.get("/api/sessions")
async def get_sessions(
    request: Request,
):
    user_id = str(_require_current_user(request)["id"])
    return db.get_sessions(user_id)


@app.get("/api/sessions/{session_id}")
async def get_session_history(
    session_id: str,
    request: Request,
):
    user_id = str(_require_current_user(request)["id"])
    if not db.session_belongs_to_user(session_id, user_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return db.get_messages(session_id)


@app.post(
    "/api/sessions/{session_id}/report-preview",
    response_model=SessionReportPreviewResponse,
)
async def generate_session_report_preview(
    session_id: str,
    request: Request,
):
    user_id = str(_require_current_user(request)["id"])
    session_title, messages = _resolve_session_title_and_messages(user_id, session_id)
    generated_at = datetime.utcnow().replace(microsecond=0).isoformat()
    markdown = _build_session_report_preview(session_title, messages)
    return SessionReportPreviewResponse(
        title=session_title,
        markdown=markdown,
        session_id=session_id,
        generated_at=generated_at,
    )


@app.post("/api/sessions/{session_id}/reports", response_model=GeneratedReportResponse)
async def create_generated_report(
    session_id: str,
    request: Request,
):
    user_id = str(_require_current_user(request)["id"])
    session_title, messages = _resolve_session_title_and_messages(user_id, session_id)
    markdown = _build_session_report_preview(session_title, messages)
    summary = _build_report_summary(markdown)
    context = _build_report_export_context(messages)
    created = db.create_generated_report(
        user_id=user_id,
        session_id=session_id,
        title=session_title,
        summary=summary,
        markdown=markdown,
        context=context,
        report_type="conversation_summary",
        read_at=datetime.utcnow().replace(microsecond=0).isoformat(),
    )
    return _serialize_generated_report(created)


@app.post(
    "/api/sessions/{session_id}/weekly-report", response_model=GeneratedReportResponse
)
async def add_session_to_weekly_report(
    session_id: str,
    request: Request,
):
    user_id = str(_require_current_user(request)["id"])
    report = _create_or_update_weekly_draft_report(user_id, session_id)
    return _serialize_generated_report(report)


@app.get("/api/reports", response_model=List[GeneratedReportSummaryResponse])
async def list_generated_reports(
    request: Request,
):
    user_id = str(_require_current_user(request)["id"])
    return [
        _serialize_generated_report_summary(item)
        for item in db.list_generated_reports(user_id)
    ]


@app.get("/api/reports/weekly")
async def list_weekly_reports(request: Request):
    """List all weekly reports for the current user, newest first."""
    current_user = _require_current_user(request)
    user_id = str(current_user["id"])
    try:
        rows = db.list_weekly_reports(user_id)
        return {"reports": rows, "total": len(rows)}
    except Exception as e:
        logger.error("list_weekly_reports error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/reports/{report_id}", response_model=GeneratedReportResponse)
async def get_generated_report(
    report_id: str,
    request: Request,
):
    user_id = str(_require_current_user(request)["id"])
    report = db.mark_generated_report_read(report_id, user_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return _serialize_generated_report(report)


@app.delete("/api/reports/{report_id}")
async def delete_generated_report(
    report_id: str,
    request: Request,
):
    """Delete a generated report."""
    user_id = str(_require_current_user(request)["id"])

    # Validate ownership first so we only touch files for accessible reports.
    existing = db.get_generated_report(report_id, user_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Report not found or already deleted")

    # Remove on-disk weekly report artifacts (markdown + chart PNGs).
    repo_root = Path(__file__).resolve().parents[1]
    reports_root = (repo_root / "exports" / "weekly_reports").resolve()
    candidate_paths = [
        reports_root / report_id,
        reports_root / f"{report_id}_charts",
        reports_root / f"{report_id}.md",
    ]

    for artifact_path in candidate_paths:
        if not artifact_path.exists():
            continue
        resolved_artifact = artifact_path.resolve()
        if reports_root != resolved_artifact and reports_root not in resolved_artifact.parents:
            logger.warning(
                "Skipping artifact cleanup outside weekly_reports root: report_id=%s path=%s",
                report_id,
                resolved_artifact,
            )
            continue
        try:
            if artifact_path.is_dir():
                shutil.rmtree(artifact_path)
            else:
                artifact_path.unlink()
        except Exception as e:
            logger.error("Failed to delete report artifacts for %s: %s", report_id, e)
            raise HTTPException(status_code=500, detail="Failed to delete report files")

    deleted = db.delete_generated_report(report_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Report not found or already deleted")
    return {"status": "ok", "deleted_id": report_id}


@app.patch("/api/reports/{report_id}", response_model=GeneratedReportResponse)
async def update_generated_report(
    report_id: str,
    payload: UpdateGeneratedReportRequest,
    request: Request,
):
    user_id = str(_require_current_user(request)["id"])
    title = str(payload.title or "").strip()
    markdown = str(payload.markdown or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Report title cannot be empty")
    if not markdown:
        raise HTTPException(status_code=400, detail="Report content cannot be empty")

    existing = db.get_generated_report(report_id, user_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Report not found")

    updated = db.update_generated_report(
        report_id=report_id,
        user_id=user_id,
        title=title,
        summary=_build_report_summary(markdown),
        markdown=markdown,
        blocks=_normalize_report_blocks(
            payload.blocks
            if isinstance(payload.blocks, list)
            else (
                existing.get("blocks")
                if isinstance(existing.get("blocks"), list)
                else []
            )
        ),
        context=existing.get("context") if isinstance(existing.get("context"), dict) else {},
        read_at=str(
            existing.get("read_at")
            or datetime.utcnow().replace(microsecond=0).isoformat()
        ),
        last_refreshed_at=(
            str(existing.get("last_refreshed_at"))
            if existing.get("last_refreshed_at")
            else None
        ),
    )
    if not updated:
        raise HTTPException(status_code=500, detail="Unable to update report")
    return _serialize_generated_report(updated)


# ---------------------------------------------------------------------------
# Chart regeneration endpoint
# ---------------------------------------------------------------------------

class RegenChartRequest(BaseModel):
    product_slug: str
    chart_type: str = "yearly"   # "yearly" | "monthly"
    language: str = "zh"


class RegenChartResponse(BaseModel):
    chart_url: str
    chart_type: str


class UpdateChartSnapshotRequest(BaseModel):
    product_slug: str
    chart_type: str = "yearly"
    chart: Dict[str, Any]


class UpdateChartSnapshotResponse(BaseModel):
    chart_url: str
    chart_type: str


def _to_optional_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_chart_snapshot(
    current_chart: Dict[str, Any],
    incoming_chart: Dict[str, Any],
    chart_type: str,
) -> Dict[str, Any]:
    series = list(incoming_chart.get("series") or current_chart.get("series") or [])
    normalized_series: List[Dict[str, Any]] = []
    for index, series_item in enumerate(series):
        if not isinstance(series_item, dict):
            continue
        key = str(series_item.get("key") or "").strip()
        if not key:
            continue
        normalized_series.append(
            {
                "key": key,
                "label": str(series_item.get("label") or key),
                "color": str(series_item.get("color") or (["#00A6A6", "#D4A437", "#ff4d4f", "#1890ff", "#73d13d"][index % 5])),
                "axis": str(series_item.get("axis") or "left"),
                "unit": str(series_item.get("unit") or current_chart.get("unit") or ""),
            }
        )
    if not normalized_series:
        normalized_series = list(current_chart.get("series") or [])

    series_keys = [str(item.get("key")) for item in normalized_series if str(item.get("key") or "")]
    points = list(incoming_chart.get("points") or [])
    normalized_points: List[Dict[str, Any]] = []
    for point in points:
        if not isinstance(point, dict):
            continue
        period = str(point.get("period") or point.get("label") or "").strip()
        label = str(point.get("label") or point.get("period") or period).strip()
        if not period and not label:
            continue
        normalized_point: Dict[str, Any] = {
            "period": period or label,
            "label": label or period,
        }
        for key in series_keys:
            normalized_point[key] = _to_optional_float(point.get(key))
        normalized_points.append(normalized_point)

    normalized_chart: Dict[str, Any] = {
        "chart_type": chart_type,
        "title": str(incoming_chart.get("title") or current_chart.get("title") or ""),
        "unit": str(incoming_chart.get("unit") or current_chart.get("unit") or ""),
        "series": normalized_series,
        "points": normalized_points,
        "left_axis_unit": str(incoming_chart.get("left_axis_unit") or current_chart.get("left_axis_unit") or incoming_chart.get("unit") or current_chart.get("unit") or ""),
        "right_axis_unit": str(incoming_chart.get("right_axis_unit") or current_chart.get("right_axis_unit") or "") or None,
    }

    if chart_type == "yearly":
        current_forecast = dict(current_chart.get("forecast_band") or {})
        incoming_forecast = dict(incoming_chart.get("forecast_band") or {})
        normalized_chart["forecast_band"] = {
            "low": _to_optional_float(incoming_forecast.get("low") if "low" in incoming_forecast else current_forecast.get("low")),
            "high": _to_optional_float(incoming_forecast.get("high") if "high" in incoming_forecast else current_forecast.get("high")),
            "period": str(incoming_forecast.get("period") or current_forecast.get("period") or "") or None,
            "confidence_label": str(incoming_forecast.get("confidence_label") or current_forecast.get("confidence_label") or "") or None,
        }
        normalized_chart["report_period"] = str(incoming_chart.get("report_period") or current_chart.get("report_period") or "") or None
    else:
        current_highlight = dict(current_chart.get("highlight") or {})
        incoming_highlight = dict(incoming_chart.get("highlight") or {})
        normalized_chart["anchor_month"] = str(incoming_chart.get("anchor_month") or current_chart.get("anchor_month") or "") or None
        normalized_chart["highlight"] = {
            "start": str(incoming_highlight.get("start") or current_highlight.get("start") or "") or None,
            "end": str(incoming_highlight.get("end") or current_highlight.get("end") or "") or None,
        }

    return normalized_chart


def _render_chart_snapshot_to_png(
    report_id: str,
    product_payload: Dict[str, Any],
    chart_type: str,
    language: str,
) -> Path:
    from backend.src.service.weekly_report_charts import render_chart_from_snapshot_payload

    charts_dir = _WEEKLY_REPORTS_DIR / report_id / "charts"
    chart_path = render_chart_from_snapshot_payload(
        product_payload=product_payload,
        chart_type=chart_type,
        report_id=report_id,
        charts_dir=charts_dir,
        language=language,
    )
    if not chart_path or not chart_path.exists():
        raise HTTPException(status_code=500, detail="Chart rendering produced no output")
    return chart_path


@app.patch("/api/reports/{report_id}/chart-data", response_model=UpdateChartSnapshotResponse)
async def update_report_chart_snapshot(
    report_id: str,
    payload: UpdateChartSnapshotRequest,
    request: Request,
):
    user_id = str(_require_current_user(request)["id"])
    report = db.get_generated_report(report_id, user_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    slug = str(payload.product_slug or "").strip()
    chart_type = str(payload.chart_type or "yearly").strip().lower()
    if not slug:
        raise HTTPException(status_code=400, detail="product_slug is required")
    if chart_type not in {"yearly", "monthly"}:
        raise HTTPException(status_code=400, detail="chart_type must be 'yearly' or 'monthly'")
    if not isinstance(payload.chart, dict):
        raise HTTPException(status_code=400, detail="chart payload is required")

    weekly_ctx = dict(report.get("context") or {})
    chart_payload = dict(weekly_ctx.get("weekly_chart_payload") or {})
    product_payloads = dict(chart_payload.get("products") or {})
    product_payload = dict(product_payloads.get(slug) or {})
    if not product_payload:
        raise HTTPException(status_code=404, detail=f"Chart payload for '{slug}' was not found")

    current_chart = dict(product_payload.get(chart_type) or {})
    if not current_chart:
        raise HTTPException(status_code=404, detail=f"{chart_type} chart for '{slug}' was not found")

    product_payload[chart_type] = _normalize_chart_snapshot(current_chart, payload.chart, chart_type)

    language = str(chart_payload.get("language") or weekly_ctx.get("language") or "zh")
    chart_path = _render_chart_snapshot_to_png(report_id, product_payload, chart_type, language)

    product_payloads[slug] = product_payload
    chart_payload.update(
        {
            "version": int(chart_payload.get("version") or 1),
            "report_id": chart_payload.get("report_id") or weekly_ctx.get("report_id") or report_id,
            "week_start": chart_payload.get("week_start") or weekly_ctx.get("week_start"),
            "week_end": chart_payload.get("week_end") or weekly_ctx.get("week_end"),
            "language": language,
            "products": product_payloads,
        }
    )
    weekly_ctx["weekly_chart_payload"] = chart_payload

    updated = db.update_generated_report(
        report_id=report_id,
        user_id=user_id,
        title=str(report.get("title") or "Untitled report"),
        summary=_build_report_summary(str(report.get("markdown") or "")),
        markdown=str(report.get("markdown") or ""),
        blocks=report.get("blocks") if isinstance(report.get("blocks"), list) else [],
        context=weekly_ctx,
        read_at=str(report.get("read_at") or datetime.utcnow().replace(microsecond=0).isoformat()),
        last_refreshed_at=str(datetime.utcnow().replace(microsecond=0).isoformat()),
    )
    if not updated:
        raise HTTPException(status_code=500, detail="Unable to update report chart")

    suffix = "12mo" if chart_type == "yearly" else "monthly"
    return UpdateChartSnapshotResponse(
        chart_url=f"/api/reports-static/{report_id}/charts/{slug}_{suffix}.png",
        chart_type=chart_type,
    )


@app.post("/api/reports/{report_id}/regenerate-chart", response_model=RegenChartResponse)
async def regenerate_report_chart(
    report_id: str,
    payload: RegenChartRequest,
    request: Request,
):
    """Re-render a single product chart for an existing weekly report.

    The chart PNG is overwritten in place at:
        exports/weekly_reports/{report_id}/charts/{slug}_12mo.png  (yearly)
        exports/weekly_reports/{report_id}/charts/{slug}_monthly.png (monthly)

    The frontend can force a cache-bust by appending ?t=<timestamp> to the URL.
    """
    user_id = str(_require_current_user(request)["id"])

    # 1. Verify report ownership
    report = db.get_generated_report(report_id, user_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    slug = (payload.product_slug or "").strip()
    chart_type = (payload.chart_type or "yearly").strip().lower()
    lang = (payload.language or "zh").strip()

    if not slug:
        raise HTTPException(status_code=400, detail="product_slug is required")
    if chart_type not in ("yearly", "monthly"):
        raise HTTPException(status_code=400, detail="chart_type must be 'yearly' or 'monthly'")

    # 2. Load product spec from catalog
    try:
        from backend.src.service.weekly_report_data import load_product_catalog, build_product_chart_payload
        from backend.src.service.weekly_report_charts import render_chart_from_snapshot_payload
        from backend.src.service.forecast_snapshot_adapter import (
            forecast_snapshot_to_band,
            forecast_snapshot_to_report_context,
            get_or_create_forecast_snapshot,
        )
        from backend.src.db.market_db import market_db as _market_db_inst

        products = load_product_catalog(db)
        product = next((p for p in products if p.slug == slug), None)
        if product is None:
            raise HTTPException(status_code=404, detail=f"Product '{slug}' not found in catalog")

        charts_dir = _WEEKLY_REPORTS_DIR / report_id / "charts"

        # Derive a reasonable week_end from the report context
        ctx = report.get("context") if isinstance(report, dict) else {}
        week_end = (ctx or {}).get("week_end") or datetime.utcnow().strftime("%Y-%m-%d")
        week_start = (ctx or {}).get("week_start") or week_end

        forecast_snapshot = get_or_create_forecast_snapshot(
            db.engine,
            product.slug,
            as_of_date=week_end,
            run_reason="chart_regeneration",
        )
        refreshed_forecast = forecast_snapshot_to_band(
            forecast_snapshot,
            horizon="7d",
            language=lang,
        )
        product_payload = build_product_chart_payload(
            engine=_market_db_inst.engine,
            product=product,
            forecast=refreshed_forecast,
            week_start=week_start,
            week_end=week_end,
            language=lang,
        )

        chart_path = render_chart_from_snapshot_payload(
            product_payload=product_payload,
            chart_type=chart_type,
            report_id=report_id,
            charts_dir=charts_dir,
            language=lang,
        )
        if not chart_path or not chart_path.exists():
            raise HTTPException(status_code=500, detail="Chart rendering produced no output")

        weekly_ctx = dict(ctx or {})
        forecast_snapshots = dict(weekly_ctx.get("forecast_snapshots") or {})
        forecast_snapshots[slug] = forecast_snapshot_to_report_context(
            forecast_snapshot,
            horizon="7d",
        )
        weekly_ctx["forecast_snapshots"] = forecast_snapshots
        chart_payload = dict(weekly_ctx.get("weekly_chart_payload") or {})
        product_payloads = dict(chart_payload.get("products") or {})
        product_payloads[slug] = product_payload
        chart_payload.update(
            {
                "version": 1,
                "report_id": weekly_ctx.get("report_id") or report_id,
                "week_start": week_start,
                "week_end": week_end,
                "language": lang,
                "products": product_payloads,
            }
        )
        weekly_ctx["weekly_chart_payload"] = chart_payload

        updated = db.update_generated_report(
            report_id=report_id,
            user_id=user_id,
            title=str(report.get("title") or "Untitled report"),
            summary=_build_report_summary(str(report.get("markdown") or "")),
            markdown=str(report.get("markdown") or ""),
            blocks=report.get("blocks") if isinstance(report.get("blocks"), list) else [],
            context=weekly_ctx,
            read_at=str(
                report.get("read_at") or datetime.utcnow().replace(microsecond=0).isoformat()
            ),
            last_refreshed_at=str(datetime.utcnow().replace(microsecond=0).isoformat()),
        )
        if not updated:
            raise HTTPException(status_code=500, detail="Unable to update report context after chart regeneration")

        return RegenChartResponse(
            chart_url=(
                f"/api/reports-static/{report_id}/charts/{slug}_12mo.png"
                if chart_type == "yearly"
                else f"/api/reports-static/{report_id}/charts/{slug}_monthly.png"
            ),
            chart_type=chart_type,
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Chart regeneration failed for report %s slug %s: %s", report_id, slug, exc)
        raise HTTPException(status_code=500, detail=f"Chart regeneration error: {exc}")


@app.post(
    "/api/reports/{report_id}/refresh",
    response_model=RefreshReportBlocksResponse,
)
async def refresh_generated_report_blocks(report_id: str, request: Request):
    user_id = str(_require_current_user(request)["id"])
    report = db.get_generated_report(report_id, user_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    refreshed_blocks, refreshed_count, refreshed_at = _refresh_report_blocks(report)

    updated = db.update_generated_report(
        report_id=report_id,
        user_id=user_id,
        title=str(report.get("title") or "Untitled report"),
        summary=_build_report_summary(str(report.get("markdown") or "")),
        markdown=str(report.get("markdown") or ""),
        blocks=refreshed_blocks,
        context=report.get("context") if isinstance(report.get("context"), dict) else {},
        read_at=str(
            report.get("read_at") or datetime.utcnow().replace(microsecond=0).isoformat()
        ),
        last_refreshed_at=refreshed_at,
    )
    if not updated:
        raise HTTPException(status_code=500, detail="Unable to refresh report blocks")

    return RefreshReportBlocksResponse(
        report=_serialize_generated_report(updated),
        refreshed_block_count=refreshed_count,
        refreshed_at=refreshed_at,
    )


@app.post(
    "/api/reports/{report_id}/refresh/{block_id}",
    response_model=RefreshReportBlocksResponse,
)
async def refresh_single_generated_report_block(
    report_id: str,
    block_id: str,
    request: Request,
):
    user_id = str(_require_current_user(request)["id"])
    report = db.get_generated_report(report_id, user_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    refreshed_blocks, refreshed_count, refreshed_at = _refresh_report_blocks(
        report, block_id=block_id
    )
    if refreshed_count == 0:
        raise HTTPException(status_code=404, detail="Block not found")

    updated = db.update_generated_report(
        report_id=report_id,
        user_id=user_id,
        title=str(report.get("title") or "Untitled report"),
        summary=_build_report_summary(str(report.get("markdown") or "")),
        markdown=str(report.get("markdown") or ""),
        blocks=refreshed_blocks,
        context=report.get("context") if isinstance(report.get("context"), dict) else {},
        read_at=str(
            report.get("read_at") or datetime.utcnow().replace(microsecond=0).isoformat()
        ),
        last_refreshed_at=refreshed_at,
    )
    if not updated:
        raise HTTPException(status_code=500, detail="Unable to refresh report block")

    return RefreshReportBlocksResponse(
        report=_serialize_generated_report(updated),
        refreshed_block_count=refreshed_count,
        refreshed_at=refreshed_at,
    )


@app.post("/api/reports/{report_id}/status", response_model=GeneratedReportResponse)
async def update_generated_report_status(
    report_id: str,
    payload: UpdateGeneratedReportStatusRequest,
    request: Request,
):
    user_id = str(_require_current_user(request)["id"])
    current_user = _require_current_user(request)
    next_status = str(payload.status or "draft").strip().lower()
    if next_status not in {"draft", "approved"}:
        raise HTTPException(status_code=400, detail="Unsupported report status")

    existing = db.get_generated_report(report_id, user_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Report not found")

    updated = db.set_generated_report_status(
        report_id=report_id,
        user_id=user_id,
        status=next_status,
        approved_by=(
            str(current_user.get("username") or "")
            if next_status == "approved"
            else None
        ),
    )
    if not updated:
        raise HTTPException(status_code=500, detail="Unable to update report status")
    return _serialize_generated_report(updated)


@app.get("/api/report-recipients", response_model=List[ReportRecipientResponse])
async def list_report_recipients(request: Request):
    _require_current_user(request)
    return [
        _serialize_report_recipient(user)
        for user in db.list_users()
        if bool(user.get("is_active", True))
    ]


@app.get(
    "/api/reports/{report_id}/deliveries", response_model=List[ReportDeliveryResponse]
)
async def list_report_deliveries(report_id: str, request: Request):
    user_id = str(_require_current_user(request)["id"])
    report = db.get_generated_report(report_id, user_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return [
        _serialize_report_delivery(item)
        for item in db.list_report_deliveries(report_id)
    ]


@app.post("/api/reports/{report_id}/deliver", response_model=ReportDeliveryResponse)
async def deliver_report(
    report_id: str, payload: DeliverReportRequest, request: Request
):
    current_user = _require_current_user(request)
    user_id = str(current_user["id"])
    report = db.get_generated_report(report_id, user_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    if str(report.get("status") or "draft") != "approved":
        raise HTTPException(
            status_code=400, detail="Only approved reports can be shared or emailed"
        )

    delivery_mode = str(payload.delivery_mode or "").strip().lower()
    if delivery_mode not in {"share", "email"}:
        raise HTTPException(status_code=400, detail="Unsupported delivery mode")

    recipient_ids = [
        str(item).strip() for item in payload.recipient_ids if str(item).strip()
    ]
    if not recipient_ids:
        raise HTTPException(status_code=400, detail="Select at least one recipient")

    available_users = {
        str(user.get("id") or ""): user
        for user in db.list_users()
        if bool(user.get("is_active", True))
    }
    recipients = [
        available_users[recipient_id]
        for recipient_id in recipient_ids
        if recipient_id in available_users
    ]
    if not recipients:
        raise HTTPException(status_code=400, detail="No valid recipients selected")

    provider = "in-app-share"
    status = "sent"
    if delivery_mode == "email":
        provider = _send_report_delivery_email(report, recipients, payload.message)
        status = "sent" if provider == "smtp" else "simulated"

    created = db.create_report_delivery(
        report_id=report_id,
        sender_user_id=user_id,
        delivery_mode=delivery_mode,
        recipient_ids=recipient_ids,
        recipient_labels=[str(user.get("username") or "") for user in recipients],
        message=payload.message,
        status=status,
        provider=provider,
    )
    return _serialize_report_delivery(created)


@app.get("/api/report-templates", response_model=List[ReportTemplateResponse])
async def list_report_templates(request: Request):
    user = _require_current_user(request)
    user_id = str(user["id"])
    
    # Get all templates
    templates = db.list_report_templates()
    
    # Get all schedules for this user
    schedules_list = db.list_enabled_report_schedules()
    schedules_by_template: Dict[str, Dict[str, Any]] = {}
    
    for schedule in schedules_list:
        if str(schedule.get("user_id", "")) == user_id:
            report_type = str(schedule.get("report_type", ""))
            template_id = _extract_template_id_from_schedule_type(report_type)
            if template_id:
                schedules_by_template[template_id] = schedule
    
    # Serialize templates with schedule info
    return [
        _serialize_report_template(item, schedules_by_template.get(str(item.get("id", ""))))
        for item in templates
    ]


@app.post("/api/report-templates", response_model=ReportTemplateResponse)
async def save_report_template(payload: SaveReportTemplateRequest, request: Request):
    admin_user = _require_admin(request)
    sections = [str(item).strip() for item in payload.sections if str(item).strip()]
    if not str(payload.name or "").strip():
        raise HTTPException(status_code=400, detail="Template name is required")
    if not sections:
        raise HTTPException(status_code=400, detail="Add at least one template section")
    saved = db.save_report_template(
        template_id=payload.id,
        name=str(payload.name).strip(),
        description=(str(payload.description).strip() if payload.description else None),
        time_period=str(payload.time_period or "weekly").strip() or "weekly",
        audience=(str(payload.audience).strip() if payload.audience else None),
        schedule_hint=(
            str(payload.schedule_hint).strip() if payload.schedule_hint else None
        ),
        sections=sections,
        template_title=(
            str(payload.template_title).strip() if payload.template_title else None
        ),
        template_markdown=(
            str(payload.template_markdown)
            if payload.template_markdown is not None
            else None
        ),
        template_blocks=(
            _normalize_report_blocks(payload.template_blocks)
            if isinstance(payload.template_blocks, list)
            else []
        ),
        updated_by=str(admin_user.get("username") or ""),
    )
    return _serialize_report_template(saved)


@app.delete("/api/report-templates/{template_id}")
async def delete_report_template(template_id: str, request: Request):
    _require_admin(request)
    
    # Delete associated schedules first to prevent orphaned schedules
    schedule_type = _template_schedule_type(template_id)
    deleted_schedules = db.delete_schedules_by_report_type(schedule_type)
    logger.info(
        "Deleted %d schedule(s) for template %s (schedule_type=%s)",
        deleted_schedules,
        template_id,
        schedule_type,
    )
    
    # Now delete the template
    deleted = db.delete_report_template(template_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Template not found")
    
    return {
        "status": "ok",
        "template_id": template_id,
        "deleted_schedules": deleted_schedules,
    }


@app.post(
    "/api/report-templates/{template_id}/generate",
    response_model=GeneratedReportResponse,
)
async def generate_report_from_template(template_id: str, request: Request):
    user_id = str(_require_current_user(request)["id"])
    template = db.get_report_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    created = _build_template_generated_report(template, user_id)
    return _serialize_generated_report(created)


@app.get(
    "/api/report-templates/{template_id}/schedule",
    response_model=ReportScheduleResponse,
)
async def get_report_template_schedule(template_id: str, request: Request):
    user_id = str(_require_current_user(request)["id"])
    template = db.get_report_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    schedule = db.get_report_schedule(user_id, _template_schedule_type(template_id))
    return _serialize_report_schedule(schedule)


@app.put(
    "/api/report-templates/{template_id}/schedule",
    response_model=ReportScheduleResponse,
)
async def update_report_template_schedule(
    template_id: str,
    payload: ReportScheduleRequest,
    request: Request,
):
    user_id = str(_require_current_user(request)["id"])
    template = db.get_report_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    hour = max(0, min(23, int(payload.hour)))
    minute = max(0, min(59, int(payload.minute)))
    schedule = db.upsert_report_schedule(
        user_id=user_id,
        report_type=_template_schedule_type(template_id),
        enabled=bool(payload.enabled),
        hour=hour,
        minute=minute,
        weekday=0,
        timezone="Asia/Shanghai",
    )
    return _serialize_report_schedule(schedule)


@app.post(
    "/api/report-templates/{template_id}/schedule/run-now",
    response_model=GeneratedReportResponse,
)
async def run_report_template_now(template_id: str, request: Request):
    user_id = str(_require_current_user(request)["id"])
    template = db.get_report_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    created = _build_template_generated_report(
        template,
        user_id,
        mark_as_read=True,
        report_type=_generated_report_type_for_schedule(
            _template_schedule_type(template_id)
        ),
    )
    return _serialize_generated_report(created)


@app.get("/api/platform/report-categories", response_model=List[ReportCategoryResponse])
async def list_platform_report_categories(request: Request):
    current_user = _require_current_user(request)
    org_id = str(current_user.get("organization_id") or "org-default")
    return [
        _serialize_report_category(item)
        for item in db.list_report_categories(org_id)
    ]


@app.post("/api/platform/report-categories", response_model=ReportCategoryResponse)
async def save_platform_report_category(
    payload: SaveReportCategoryRequest, request: Request
):
    current_user = _require_current_user(request)
    org_id = str(current_user.get("organization_id") or "org-default")
    name = str(payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Category name is required")

    saved = db.save_report_category(
        category_id=payload.id,
        org_id=org_id,
        name=name,
        parent_id=(str(payload.parent_id).strip() if payload.parent_id else None),
        icon=(str(payload.icon).strip() if payload.icon else None),
        position=int(payload.position),
        permissions=payload.permissions,
        created_by=str(current_user.get("username") or ""),
    )
    return _serialize_report_category(saved)


@app.delete("/api/platform/report-categories/{category_id}")
async def delete_platform_report_category(category_id: str, request: Request):
    current_user = _require_current_user(request)
    org_id = str(current_user.get("organization_id") or "org-default")
    deleted = db.delete_report_category(category_id, org_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Category not found")
    return {"status": "ok", "category_id": category_id}


@app.get("/api/platform/cockpits", response_model=List[CockpitDefinitionResponse])
async def list_platform_cockpits(request: Request):
    current_user = _require_current_user(request)
    org_id = str(current_user.get("organization_id") or "org-default")
    return [
        _serialize_cockpit_definition(item)
        for item in db.list_cockpit_definitions(org_id)
    ]


@app.post("/api/platform/cockpits", response_model=CockpitDefinitionResponse)
async def save_platform_cockpit(
    payload: SaveCockpitDefinitionRequest, request: Request
):
    current_user = _require_current_user(request)
    org_id = str(current_user.get("organization_id") or "org-default")
    name = str(payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Cockpit name is required")

    status = str(payload.status or "draft").strip().lower()
    if status not in {"draft", "published", "archived"}:
        raise HTTPException(status_code=400, detail="Unsupported cockpit status")

    saved = db.save_cockpit_definition(
        cockpit_id=payload.id,
        org_id=org_id,
        name=name,
        department_id=(
            str(payload.department_id).strip() if payload.department_id else None
        ),
        layout=payload.layout,
        status=status,
        version=max(1, int(payload.version)),
        is_default=bool(payload.is_default),
        permissions=payload.permissions,
        created_by=str(current_user.get("username") or ""),
    )
    return _serialize_cockpit_definition(saved)


@app.delete("/api/platform/cockpits/{cockpit_id}")
async def delete_platform_cockpit(cockpit_id: str, request: Request):
    current_user = _require_current_user(request)
    org_id = str(current_user.get("organization_id") or "org-default")
    deleted = db.delete_cockpit_definition(cockpit_id, org_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Cockpit definition not found")
    return {"status": "ok", "cockpit_id": cockpit_id}


@app.get("/api/platform/alert-rules", response_model=List[AlertRuleResponse])
async def list_platform_alert_rules(request: Request):
    current_user = _require_current_user(request)
    org_id = str(current_user.get("organization_id") or "org-default")
    return [_serialize_alert_rule(item) for item in db.list_alert_rules(org_id)]


@app.post("/api/platform/alert-rules", response_model=AlertRuleResponse)
async def save_platform_alert_rule(payload: SaveAlertRuleRequest, request: Request):
    current_user = _require_current_user(request)
    org_id = str(current_user.get("organization_id") or "org-default")
    name = str(payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Rule name is required")
    metric_expression = str(payload.metric_expression or "").strip()
    if not metric_expression:
        raise HTTPException(status_code=400, detail="Metric expression is required")

    saved = db.save_alert_rule(
        rule_id=payload.id,
        org_id=org_id,
        name=name,
        metric_expression=metric_expression,
        condition_type=str(payload.condition_type or "threshold_above").strip(),
        condition_params=payload.condition_params,
        severity=str(payload.severity or "medium").strip().lower(),
        category=str(payload.category or "custom").strip().lower(),
        notification_channels=payload.notification_channels,
        default_owner_id=(
            str(payload.default_owner_id).strip() if payload.default_owner_id else None
        ),
        enabled=bool(payload.enabled),
        evaluation_interval_min=max(1, int(payload.evaluation_interval_min)),
        created_by=str(current_user.get("username") or ""),
    )
    return _serialize_alert_rule(saved)


@app.delete("/api/platform/alert-rules/{rule_id}")
async def delete_platform_alert_rule(rule_id: str, request: Request):
    current_user = _require_current_user(request)
    org_id = str(current_user.get("organization_id") or "org-default")
    deleted = db.delete_alert_rule(rule_id, org_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Alert rule not found")
    return {"status": "ok", "rule_id": rule_id}


@app.get("/api/platform/data-sources", response_model=List[DataSourceResponse])
async def list_platform_data_sources(request: Request):
    current_user = _require_current_user(request)
    org_id = str(current_user.get("organization_id") or "org-default")
    return [_serialize_data_source(item) for item in db.list_data_sources(org_id)]


@app.post("/api/platform/data-sources", response_model=DataSourceResponse)
async def save_platform_data_source(
    payload: SaveDataSourceRequest, request: Request
):
    current_user = _require_current_user(request)
    org_id = str(current_user.get("organization_id") or "org-default")
    name = str(payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Data source name is required")
    connector_type = str(payload.connector_type or "").strip().lower()
    if not connector_type:
        raise HTTPException(status_code=400, detail="Connector type is required")

    saved = db.save_data_source(
        source_id=payload.id,
        org_id=org_id,
        name=name,
        connector_type=connector_type,
        connection_config=payload.connection_config,
        status=str(payload.status or "active").strip().lower(),
        last_tested_at=(
            str(payload.last_tested_at).strip() if payload.last_tested_at else None
        ),
        created_by=str(current_user.get("username") or ""),
    )
    return _serialize_data_source(saved)


@app.delete("/api/platform/data-sources/{source_id}", status_code=204)
async def delete_platform_data_source(source_id: str, request: Request):
    current_user = _require_current_user(request)
    org_id = str(current_user.get("organization_id") or "org-default")
    deleted = db.delete_data_source(source_id, org_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Data source not found")


@app.get("/api/platform/datasets", response_model=List[DatasetResponse])
async def list_platform_datasets(request: Request, source_id: Optional[str] = None):
    current_user = _require_current_user(request)
    org_id = str(current_user.get("organization_id") or "org-default")
    return [
        _serialize_dataset(item)
        for item in db.list_datasets(org_id, source_id=source_id)
    ]


@app.post("/api/platform/datasets", response_model=DatasetResponse)
async def save_platform_dataset(payload: SaveDatasetRequest, request: Request):
    current_user = _require_current_user(request)
    org_id = str(current_user.get("organization_id") or "org-default")
    if not str(payload.source_id or "").strip():
        raise HTTPException(status_code=400, detail="Source id is required")
    if not str(payload.friendly_name or "").strip():
        raise HTTPException(status_code=400, detail="Dataset name is required")
    if not str(payload.physical_reference or "").strip():
        raise HTTPException(
            status_code=400, detail="Physical reference is required"
        )

    saved = db.save_dataset(
        dataset_id=payload.id,
        org_id=org_id,
        source_id=str(payload.source_id).strip(),
        friendly_name=str(payload.friendly_name).strip(),
        physical_reference=str(payload.physical_reference).strip(),
        description=(str(payload.description).strip() if payload.description else None),
        tags=[str(item).strip() for item in payload.tags if str(item).strip()],
        schema_snapshot=payload.schema_snapshot,
        status=str(payload.status or "active").strip().lower(),
    )
    return _serialize_dataset(saved)


@app.delete("/api/platform/datasets/{dataset_id}", status_code=204)
async def delete_platform_dataset(dataset_id: str, request: Request):
    current_user = _require_current_user(request)
    org_id = str(current_user.get("organization_id") or "org-default")
    deleted = db.delete_dataset(dataset_id, org_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Dataset not found")


@app.get("/api/platform/tables/{table_name}/inspect")
async def inspect_platform_table(table_name: str, request: Request):
    """Introspect a table in the connected MySQL/ERP database and return its columns."""
    _require_current_user(request)
    try:
        # Use the report engine which connects to DATABASE_URL (MySQL)
        try:
            from backend.src.db.engine_factory import get_erp_engine as _get_erp
            _insp_engine = _get_erp()
        except Exception:
            raise HTTPException(status_code=503, detail="Database engine not available")
        with _insp_engine.connect() as conn:
            rows = conn.execute(
                text(f"SHOW COLUMNS FROM {_quote_identifier(table_name)}")
            ).fetchall()
        columns = [{"name": str(r[0]), "type": str(r[1])} for r in rows]
        return {"table": table_name, "columns": columns}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Cannot inspect table '{table_name}': {str(exc)}",
        )


@app.get(
    "/api/platform/datasets/{dataset_id}/mappings",
    response_model=List[DatasetColumnMappingResponse],
)
async def list_platform_dataset_mappings(dataset_id: str, request: Request):
    current_user = _require_current_user(request)
    org_id = str(current_user.get("organization_id") or "org-default")
    dataset = db.get_dataset(dataset_id, org_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return [
        _serialize_dataset_column_mapping(item)
        for item in db.list_dataset_column_mappings(dataset_id)
    ]


@app.put(
    "/api/platform/datasets/{dataset_id}/mappings",
    response_model=List[DatasetColumnMappingResponse],
)
async def replace_platform_dataset_mappings(
    dataset_id: str, payload: SaveDatasetColumnMappingsRequest, request: Request
):
    current_user = _require_current_user(request)
    org_id = str(current_user.get("organization_id") or "org-default")
    dataset = db.get_dataset(dataset_id, org_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    rows = db.replace_dataset_column_mappings(
        dataset_id,
        [item.model_dump() for item in payload.mappings],
    )
    return [_serialize_dataset_column_mapping(item) for item in rows]


@app.get(
    "/api/platform/datasets/{dataset_id}/permissions",
    response_model=List[DatasetPermissionResponse],
)
async def list_platform_dataset_permissions(dataset_id: str, request: Request):
    current_user = _require_current_user(request)
    org_id = str(current_user.get("organization_id") or "org-default")
    dataset = db.get_dataset(dataset_id, org_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return [
        _serialize_dataset_permission(item)
        for item in db.list_dataset_permissions(dataset_id)
    ]


@app.put(
    "/api/platform/datasets/{dataset_id}/permissions",
    response_model=List[DatasetPermissionResponse],
)
async def replace_platform_dataset_permissions(
    dataset_id: str, payload: SaveDatasetPermissionsRequest, request: Request
):
    _require_admin(request)
    org_id = str(_require_current_user(request).get("organization_id") or "org-default")
    dataset = db.get_dataset(dataset_id, org_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    rows = db.replace_dataset_permissions(
        dataset_id,
        [item.model_dump() for item in payload.permissions],
    )
    return [_serialize_dataset_permission(item) for item in rows]


# ---------------------------------------------------------------------------
# Platform Products — admin-managed product catalogue
# ---------------------------------------------------------------------------

_VALID_PLATFORM_PRODUCT_TYPES = {
    "upstream",
    "midstream",
    "downstream",
    "crude",
    "feedstock",
    "olefin",
    "c5_mix",
    "c5_derivative",
    "c9_derivative",
}


def _normalize_platform_product_type(product_type: Optional[str]) -> str:
    normalized = str(product_type or "downstream").strip()
    return normalized if normalized in _VALID_PLATFORM_PRODUCT_TYPES else "downstream"


def _clean_platform_product_payload(payload: SavePlatformProductRequest) -> Dict[str, Any]:
    name_en = str(payload.name_en or "").strip()
    name_cn = str(payload.name_cn or "").strip()
    description = str(payload.description or "").strip()
    if not name_en and not name_cn:
        raise HTTPException(status_code=400, detail="name_en or name_cn is required")
    if not description:
        raise HTTPException(status_code=400, detail="description is required")
    if not name_en:
        name_en = name_cn
    if not name_cn:
        name_cn = name_en

    aliases = [str(alias).strip() for alias in (payload.aliases or []) if str(alias).strip()]
    display_in = [str(item).strip() for item in (payload.display_in or []) if str(item).strip()]
    return {
        "name_en": name_en,
        "name_cn": name_cn,
        "product_type": _normalize_platform_product_type(payload.product_type),
        "unit": str(payload.unit or "CNY/ton").strip() or "CNY/ton",
        "description": description,
        "aliases": aliases,
        "data_table": (str(payload.data_table).strip() if payload.data_table else None),
        "data_key": (str(payload.data_key).strip() if payload.data_key else None),
        "display_in": display_in,
    }


def _refresh_platform_product_runtime(
    action: str,
    product: Optional[Dict[str, Any]] = None,
    product_id: Optional[str] = None,
    org_id: Optional[str] = None,
) -> None:
    try:
        with _platform_refresh_lock:
            from backend.src.domain.knowledge_graph import refresh_domain_graph
            from backend.src.domain.product_resolver import rebuild_product_resolver
            from backend.src.rag.vector_store import kb
            refresh_domain_graph()
            rebuild_product_resolver()
            _rebuild_product_alias_map()
            market_data.refresh()
            _clear_market_runtime_caches()
            if action == "delete":
                kb.delete_product_catalog(str(product_id or ""), org_id=org_id)
            elif product:
                kb.upsert_product_catalog(product)
    except Exception as _e:
        print(f"Warning: could not refresh after product {action}: {_e}")


def _sync_platform_product_knowledge_index() -> None:
    try:
        from backend.src.rag.vector_store import kb

        rows = db.list_all_platform_products()
        for row in rows:
            kb.upsert_product_catalog(row)
        if rows:
            logger.info("Synced %d platform product knowledge profiles", len(rows))
    except Exception as exc:
        logger.warning("Could not sync platform product knowledge profiles: %s", exc)


def _market_price_preview_pairs(data_table: str, data_key: str) -> List[tuple[str, Optional[str]]]:
    from backend.src.service.market_data_service import REAL_DATA_MAP

    table = data_table.strip()
    key = data_key.strip()
    pairs: List[tuple[str, Optional[str]]] = []
    seen: set[tuple[str, Optional[str]]] = set()

    def add_pair(product_id: str, price_type: Optional[str]) -> None:
        product = str(product_id or "").strip()
        ptype = str(price_type or "").strip() or None
        pair = (product, ptype)
        if product and pair not in seen:
            seen.add(pair)
            pairs.append(pair)

    if table == "market_prices":
        mapped = REAL_DATA_MAP.get(key)
        if mapped:
            add_pair(str(mapped[0]), str(mapped[1]))
        if ":" in key:
            product_id, price_type = key.split(":", 1)
            add_pair(product_id, price_type)
        add_pair(key, None)
    else:
        add_pair(table, key)

    return pairs


def _preview_market_price_pair(mp_product: str, mp_price_type: Optional[str]) -> Dict[str, Any]:
    where = "product_id = :product_id AND price IS NOT NULL"
    params: Dict[str, Any] = {"product_id": mp_product}
    if mp_price_type:
        where += " AND price_type = :price_type"
        params["price_type"] = mp_price_type

    with db.engine.connect() as conn:
        summary = conn.execute(
            text(
                "SELECT COUNT(*) AS row_count, MIN(date) AS first_date, MAX(date) AS latest_date "
                f"FROM market_prices WHERE {where}"
            ),
            params,
        ).mappings().first()
        row_count = int((summary or {}).get("row_count") or 0)
        if row_count <= 0:
            return {"row_count": 0, "mini_chart_data": []}

        latest = conn.execute(
            text(
                "SELECT date, price, price_type FROM market_prices "
                f"WHERE {where} ORDER BY date DESC LIMIT 1"
            ),
            params,
        ).mappings().first()
        sample_rows = conn.execute(
            text(
                "SELECT date, price, price_type FROM market_prices "
                f"WHERE {where} ORDER BY date DESC LIMIT 30"
            ),
            params,
        ).mappings().fetchall()

    sample = [
        {
            "date": str(row.get("date") or ""),
            "price": float(row.get("price") or 0),
            "price_type": str(row.get("price_type") or ""),
        }
        for row in reversed(sample_rows)
    ]
    return {
        "row_count": row_count,
        "latest_date": str(latest.get("date") or "") if latest else None,
        "latest_price": float(latest.get("price") or 0) if latest else None,
        "price_type": str(latest.get("price_type") or mp_price_type or "") if latest else mp_price_type,
        "mini_chart_data": sample,
    }


def _preview_product_data_mapping(data_table: str, data_key: str) -> Dict[str, Any]:
    table = data_table.strip()
    key = data_key.strip()
    if not table or not key:
        return {
            "available": False,
            "row_count": 0,
            "message": "data_table and data_key are required",
            "mini_chart_data": [],
        }

    for mp_product, mp_price_type in _market_price_preview_pairs(table, key):
        preview = _preview_market_price_pair(mp_product, mp_price_type)
        if int(preview.get("row_count") or 0) > 0:
            return {
                "available": True,
                "message": "Mapping has usable market price history",
                **preview,
            }

    return {
        "available": False,
        "row_count": 0,
        "message": "No market price rows found for this mapping",
        "mini_chart_data": [],
    }


def _platform_product_has_usable_data(row: Dict[str, Any]) -> bool:
    data_table = str(row.get("data_table") or "").strip()
    data_key = str(row.get("data_key") or "").strip()
    if not data_table or not data_key:
        return False
    try:
        preview = _preview_product_data_mapping(data_table, data_key)
    except Exception:
        return False
    return bool(preview.get("available")) and int(preview.get("row_count") or 0) > 0


_PRODUCT_IMPORT_COLUMNS = [
    "product_id",
    "name_en",
    "name_cn",
    "module",
    "unit",
    "description",
    "aliases",
    "data_source",
    "data_table",
    "data_key",
    "display_in",
]

_PRODUCT_IMPORT_TEMPLATE_ROWS = [
    {
        "product_id": "custom_hc5_resin",
        "name_en": "Hydrogenated C5 Resin",
        "name_cn": "加氢碳五树脂",
        "module": "downstream",
        "unit": "CNY/ton",
        "description": "C5 downstream resin used in adhesives and road-marking paint, linked to piperylene and mixed C5 costs.",
        "aliases": "HC5 resin; hydrogenated C5; 氢化碳五树脂",
        "data_source": "market_prices",
        "data_table": "market_prices",
        "data_key": "custom_hc5_resin",
        "display_in": "dashboard;reports",
    },
    {
        "product_id": "custom_erp_material",
        "name_en": "ERP Material Example",
        "name_cn": "ERP物料示例",
        "module": "midstream",
        "unit": "CNY/ton",
        "description": "Example ERP-backed product. Replace data_key with the ERP material or product key used by your ERP mapping.",
        "aliases": "ERP material alias",
        "data_source": "erp",
        "data_table": "erp",
        "data_key": "replace_with_erp_material_key",
        "display_in": "reports",
    },
]


def _split_import_cell(value: Any) -> List[str]:
    return [item.strip() for item in re.split(r"[,，;；\n]", str(value or "")) if item.strip()]


def _slugify_product_id(value: str) -> str:
    raw = str(value or "").strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
    return slug[:80]


def _normalize_product_import_header(value: Any) -> str:
    key = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "id": "product_id",
        "productid": "product_id",
        "product_id": "product_id",
        "english_name": "name_en",
        "name_english": "name_en",
        "product_name_en": "name_en",
        "chinese_name": "name_cn",
        "name_chinese": "name_cn",
        "name_zh": "name_cn",
        "product_name_cn": "name_cn",
        "product_name_zh": "name_cn",
        "product_type": "module",
        "category": "module",
        "data_source": "data_source",
        "source": "data_source",
        "data_table": "data_table",
        "table": "data_table",
        "data_key": "data_key",
        "key": "data_key",
        "display": "display_in",
        "display_in": "display_in",
        "show_in": "display_in",
    }
    return aliases.get(key, key)


def _parse_product_import_file(filename: str, raw_bytes: bytes) -> List[Dict[str, Any]]:
    suffix = Path(filename or "").suffix.lower()
    if suffix == ".csv" or not suffix:
        text_payload = _decode_text_payload(raw_bytes)
        reader = csv.DictReader(io.StringIO(text_payload))
        if not reader.fieldnames:
            raise HTTPException(status_code=400, detail="Import file has no header row")
        rows = []
        for row in reader:
            rows.append({_normalize_product_import_header(k): v for k, v in row.items()})
        return rows

    if suffix in {".xlsx", ".xls"}:
        import pandas as pd

        try:
            frame = pd.read_excel(io.BytesIO(raw_bytes), dtype=str).fillna("")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Unable to read spreadsheet: {exc}")
        frame.columns = [_normalize_product_import_header(column) for column in frame.columns]
        return frame.to_dict(orient="records")

    raise HTTPException(status_code=400, detail="Only CSV, XLSX, or XLS files are accepted")


def _preview_platform_product_import_rows(rows: List[Dict[str, Any]], org_id: str) -> PlatformProductImportPreviewResponse:
    preview_rows: List[PlatformProductImportRowResponse] = []
    seen_ids: set[str] = set()

    for index, raw_row in enumerate(rows, start=2):
        name_en = str(raw_row.get("name_en") or "").strip()
        name_cn = str(raw_row.get("name_cn") or "").strip()
        product_id = str(raw_row.get("product_id") or "").strip()
        if not product_id:
            product_id = _slugify_product_id(name_en) or _slugify_product_id(name_cn)

        raw_module = str(raw_row.get("module") or "").strip().lower() or "downstream"
        product_type = _normalize_platform_product_type(raw_module)
        unit = str(raw_row.get("unit") or "CNY/ton").strip() or "CNY/ton"
        description = str(raw_row.get("description") or "").strip()
        aliases = _split_import_cell(raw_row.get("aliases"))
        display_in = [item for item in _split_import_cell(raw_row.get("display_in")) if item in {"dashboard", "reports"}]
        data_source = str(raw_row.get("data_source") or "market_prices").strip().lower() or "market_prices"
        data_table = str(raw_row.get("data_table") or "").strip()
        data_key = str(raw_row.get("data_key") or "").strip()

        if data_source == "market" or data_source == "market price":
            data_source = "market_prices"
        if data_source not in {"market_prices", "erp", "other"}:
            data_source = "other"
        if not data_table and data_source == "market_prices":
            data_table = "market_prices"
        if not data_table and data_source == "erp":
            data_table = "erp"

        errors: List[str] = []
        warnings: List[str] = []
        data_available: Optional[bool] = None
        data_row_count: Optional[int] = None

        if not product_id:
            errors.append("product_id is required or must be derivable from an English name")
        elif product_id in seen_ids:
            errors.append("duplicate product_id in import file")
        else:
            seen_ids.add(product_id)
        if not name_en and not name_cn:
            errors.append("name_en or name_cn is required")
        if not description:
            errors.append("description is required")
        if raw_module not in {"upstream", "midstream", "downstream"}:
            errors.append("module must be upstream, midstream, or downstream")
        if display_in and (not data_table or not data_key):
            warnings.append("dashboard/report display is selected but data_table or data_key is missing")
        if data_table and data_key:
            try:
                data_preview = _preview_product_data_mapping(data_table, data_key)
                data_available = bool(data_preview.get("available"))
                data_row_count = int(data_preview.get("row_count") or 0)
                if display_in and not data_available:
                    warnings.append("data mapping has no usable rows, so dashboard/report availability will be filtered out")
            except Exception as exc:
                warnings.append(f"data mapping preview failed: {exc}")

        existing = db.get_platform_product(product_id, org_id) if product_id else None
        preview_rows.append(
            PlatformProductImportRowResponse(
                row_number=index,
                product_id=product_id,
                name_en=name_en,
                name_cn=name_cn,
                product_type=product_type,
                unit=unit,
                description=description,
                aliases=aliases,
                data_source=data_source,
                data_table=data_table or None,
                data_key=data_key or None,
                display_in=display_in,
                valid=not errors,
                errors=errors,
                warnings=warnings,
                will_update=bool(existing),
                data_available=data_available,
                data_row_count=data_row_count,
            )
        )

    valid_count = sum(1 for row in preview_rows if row.valid)
    return PlatformProductImportPreviewResponse(
        total_rows=len(preview_rows),
        valid_rows=valid_count,
        invalid_rows=len(preview_rows) - valid_count,
        rows=preview_rows,
    )


@app.get("/api/platform/products/available", response_model=List[PlatformProductResponse])
async def list_available_platform_products(
    request: Request,
    display_in: Optional[str] = None,
    require_data: bool = False,
):
    user = _require_current_user(request)
    org_id = str(user.get("organization_id") or "org-default")
    rows = db.list_platform_products(org_id)
    display_filter = str(display_in or "").strip()
    if display_filter:
        rows = [
            row
            for row in rows
            if display_filter in [str(item).strip() for item in (row.get("display_in") or [])]
        ]
    if require_data:
        rows = [row for row in rows if _platform_product_has_usable_data(row)]
    return [_serialize_platform_product(row) for row in rows]

@app.get("/api/platform/products", response_model=List[PlatformProductResponse])
async def list_platform_products(request: Request):
    _require_admin(request)
    org_id = str(_require_current_user(request).get("organization_id") or "org-default")
    rows = db.list_platform_products(org_id)
    return [_serialize_platform_product(row) for row in rows]


@app.get("/api/platform/products/import-template")
async def download_platform_product_import_template(request: Request):
    _require_admin(request)
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=_PRODUCT_IMPORT_COLUMNS)
    writer.writeheader()
    writer.writerows(_PRODUCT_IMPORT_TEMPLATE_ROWS)
    content = output.getvalue().encode("utf-8-sig")
    return StreamingResponse(
        io.BytesIO(content),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": "attachment; filename=edia_product_import_template.csv",
        },
    )


@app.post("/api/platform/products/import-preview", response_model=PlatformProductImportPreviewResponse)
async def preview_platform_product_import(request: Request, file: UploadFile = File(...)):
    _require_admin(request)
    org_id = str(_require_current_user(request).get("organization_id") or "org-default")
    raw_bytes = await file.read()
    rows = _parse_product_import_file(file.filename or "products.csv", raw_bytes)
    return _preview_platform_product_import_rows(rows, org_id)


@app.post("/api/platform/products/import", response_model=PlatformProductImportCommitResponse)
async def import_platform_products(request: Request, file: UploadFile = File(...)):
    _require_admin(request)
    user = _require_current_user(request)
    org_id = str(user.get("organization_id") or "org-default")
    created_by = str(user.get("id") or "")
    raw_bytes = await file.read()
    rows = _parse_product_import_file(file.filename or "products.csv", raw_bytes)
    preview = _preview_platform_product_import_rows(rows, org_id)

    imported: List[PlatformProductResponse] = []
    imported_rows = 0
    last_row: Optional[Dict[str, Any]] = None
    for row in preview.rows:
        if not row.valid:
            continue
        payload = SavePlatformProductRequest(
            id=row.product_id,
            name_en=row.name_en or None,
            name_cn=row.name_cn or None,
            product_type=row.product_type,
            unit=row.unit,
            description=row.description,
            aliases=row.aliases,
            data_table=row.data_table,
            data_key=row.data_key,
            display_in=row.display_in,
        )
        clean = _clean_platform_product_payload(payload)
        saved = db.save_platform_product(
            product_id=row.product_id,
            org_id=org_id,
            name_en=clean["name_en"],
            name_cn=clean["name_cn"],
            product_type=clean["product_type"],
            unit=clean["unit"],
            description=clean["description"],
            aliases=clean["aliases"],
            data_table=clean["data_table"],
            data_key=clean["data_key"],
            display_in=clean["display_in"],
            created_by=created_by,
        )
        last_row = saved
        imported_rows += 1
        imported.append(_serialize_platform_product(saved))

    if imported_rows:
        _refresh_platform_product_runtime("import", product=last_row)

    return PlatformProductImportCommitResponse(
        total_rows=preview.total_rows,
        valid_rows=preview.valid_rows,
        invalid_rows=preview.invalid_rows,
        rows=preview.rows,
        imported_rows=imported_rows,
        imported_products=imported,
    )


@app.post(
    "/api/platform/products/preview-data-mapping",
    response_model=ProductDataMappingPreviewResponse,
)
async def preview_platform_product_data_mapping(
    payload: ProductDataMappingPreviewRequest,
    request: Request,
):
    _require_admin(request)
    data_table = str(payload.data_table or "").strip()
    data_key = str(payload.data_key or "").strip()
    try:
        preview = _preview_product_data_mapping(data_table, data_key)
    except Exception as exc:
        preview = {
            "available": False,
            "row_count": 0,
            "message": f"Could not preview data mapping: {str(exc)}",
            "mini_chart_data": [],
        }
    return ProductDataMappingPreviewResponse(
        data_table=data_table,
        data_key=data_key,
        available=bool(preview.get("available")),
        row_count=int(preview.get("row_count") or 0),
        latest_date=(str(preview.get("latest_date")) if preview.get("latest_date") else None),
        latest_price=(float(preview.get("latest_price")) if preview.get("latest_price") is not None else None),
        price_type=(str(preview.get("price_type")) if preview.get("price_type") else None),
        message=str(preview.get("message") or ""),
        mini_chart_data=list(preview.get("mini_chart_data") or []),
    )


@app.post("/api/platform/products", response_model=PlatformProductResponse)
async def save_platform_product(payload: SavePlatformProductRequest, request: Request):
    _require_admin(request)
    user = _require_current_user(request)
    org_id = str(user.get("organization_id") or "org-default")
    created_by = str(user.get("id") or "")

    product_id = str(payload.id or "").strip()
    if not product_id:
        import uuid
        product_id = str(uuid.uuid4())

    clean = _clean_platform_product_payload(payload)

    row = db.save_platform_product(
        product_id=product_id,
        org_id=org_id,
        name_en=clean["name_en"],
        name_cn=clean["name_cn"],
        product_type=clean["product_type"],
        unit=clean["unit"],
        description=clean["description"],
        aliases=clean["aliases"],
        data_table=clean["data_table"],
        data_key=clean["data_key"],
        display_in=clean["display_in"],
        created_by=created_by,
    )

    # Refresh NLU, visualization alias map, and market data after mutating the catalogue.
    # Lock prevents half-built state if two saves land concurrently.
    _refresh_platform_product_runtime("save", product=row)

    # TODO (v2): Add POST /api/platform/products/{id}/preview that runs the
    # (data_table, data_key) binding and returns {row_count, latest_price,
    # latest_date, mini_chart_data} for wizard validation before commit.
    return _serialize_platform_product(row)


@app.put("/api/platform/products/{product_id}", response_model=PlatformProductResponse)
async def update_platform_product(product_id: str, payload: SavePlatformProductRequest, request: Request):
    _require_admin(request)
    user = _require_current_user(request)
    org_id = str(user.get("organization_id") or "org-default")

    # Validate that the product exists before updating
    existing = db.get_platform_product(product_id, org_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Product not found")

    clean = _clean_platform_product_payload(payload)

    row = db.save_platform_product(
        product_id=product_id,
        org_id=org_id,
        name_en=clean["name_en"],
        name_cn=clean["name_cn"],
        product_type=clean["product_type"],
        unit=clean["unit"],
        description=clean["description"],
        aliases=clean["aliases"],
        data_table=clean["data_table"],
        data_key=clean["data_key"],
        display_in=clean["display_in"],
        created_by=str(user.get("id", "")),
    )

    # Refresh NLU, visualization alias map, and market data after updating the catalogue.
    _refresh_platform_product_runtime("update", product=row)

    return _serialize_platform_product(row)


@app.delete("/api/platform/products/{product_id}", status_code=204)
async def delete_platform_product(product_id: str, request: Request):
    _require_admin(request)
    org_id = str(_require_current_user(request).get("organization_id") or "org-default")
    deleted = db.delete_platform_product(product_id, org_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Product not found")

    # Refresh NLU, visualization alias map, and market data after removing a product.
    _refresh_platform_product_runtime("delete", product_id=product_id, org_id=org_id)


@app.post("/api/platform/products/{product_id}/insight")
async def generate_product_insight(product_id: str, request: Request):
    """Use Kimi to automatically generate a useful market insight card for a custom platform product."""
    _require_current_user(request)
    org_id = str(_require_current_user(request).get("organization_id") or "org-default")

    products = db.list_platform_products(org_id)
    product = next((p for p in products if p["id"] == product_id), None)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    name_en = str(product.get("name_en") or "")
    name_cn = str(product.get("name_cn") or "")
    description = str(product.get("description") or "")
    aliases = product.get("aliases") or []
    aliases_str = ", ".join(aliases) if aliases else "none"

    system_prompt = (
        "You are an expert commodity market analyst specializing in petrochemicals, chemicals, and energy markets. "
        "When given a product name, you automatically determine what useful market intelligence should be shown on a dashboard "
        "and produce a concise, structured JSON response. "
        "Your response must always be valid JSON with this exact schema:\n"
        "{\n"
        '  "headline": "One-sentence market summary for this product today",\n'
        '  "key_drivers": ["driver 1", "driver 2", "driver 3"],\n'
        '  "price_context": "Brief note on typical price range, unit, and key benchmarks",\n'
        '  "related_products": ["product A", "product B"],\n'
        '  "watch_points": ["risk or opportunity 1", "risk or opportunity 2"],\n'
        '  "data_suggestion": "What data series or table would best represent this product\'s price trend"\n'
        "}\n"
        "Be specific, factual, and focused on what a procurement or trading analyst would find actionable. "
        "Do not invent prices. Do not use markdown — return only the JSON object."
    )
    user_prompt = (
        f"Product: {name_en} ({name_cn})\n"
        f"Description: {description or 'not provided'}\n"
        f"Known aliases: {aliases_str}\n\n"
        "Based on your knowledge of commodity markets, determine what information is most useful to display "
        "on a dashboard card for this product. Return the JSON insight object."
    )

    try:
        raw = llm_service.generate_completion(
            system_prompt,
            user_prompt,
            route_hint="external",
            sanitized=True,
        )
        import json as _json
        # Strip markdown code fences if present
        cleaned = str(raw or "").strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        cleaned = cleaned.strip().rstrip("`").strip()
        insight = _json.loads(cleaned)
        return {"product_id": product_id, "insight": insight}
    except Exception as exc:
        return {
            "product_id": product_id,
            "insight": {
                "headline": f"Market intelligence for {name_en} is being loaded.",
                "key_drivers": ["Supply-demand balance", "Upstream feedstock costs", "Regional trade flows"],
                "price_context": "Price data not yet connected. Link a database table in Platform Builder to see live prices.",
                "related_products": [],
                "watch_points": ["Connect a data table to enable live price tracking"],
                "data_suggestion": "Register a MySQL table with date and price columns in Platform Builder.",
            },
            "error": str(exc),
        }


@app.get("/api/reports/{report_id}/export/{export_format}")
async def export_generated_report(
    report_id: str,
    export_format: str,
    request: Request,
):
    normalized_format = str(export_format or "").strip().lower()
    if normalized_format not in REPORT_EXPORT_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported export format")

    user_id = str(_require_current_user(request)["id"])
    report = db.get_generated_report(report_id, user_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    payload = _export_generated_report_bytes(report, normalized_format)
    filename = f"{_safe_export_basename(str(report.get('title') or 'report'))}.{normalized_format}"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
    }
    return StreamingResponse(
        io.BytesIO(payload),
        media_type=REPORT_EXPORT_CONTENT_TYPES[normalized_format],
        headers=headers,
    )


class ExportChatMessageRequest(BaseModel):
    content: str
    title: str = "Chat Export"
    format: str = "pdf"
    visualizations: List[Dict[str, Any]] = []


@app.post("/api/chat/export-message")
async def export_chat_message(
    payload: ExportChatMessageRequest,
    request: Request,
):
    """Export a single chat message as PDF or DOCX."""
    normalized_format = str(payload.format or "pdf").strip().lower()
    if normalized_format not in ("pdf", "docx"):
        raise HTTPException(status_code=400, detail="Unsupported format. Use pdf or docx.")

    _require_current_user(request)

    report_dict: Dict[str, Any] = {
        "title": str(payload.title or "Chat Export"),
        "created_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "markdown": str(payload.content or ""),
        "context": {"visualizations": list(payload.visualizations or [])},
    }

    try:
        file_bytes = _export_generated_report_bytes(report_dict, normalized_format)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Export failed: {exc}") from exc

    ext_map = {"pdf": "application/pdf",
               "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    safe_title = _safe_export_basename(str(payload.title or "chat_export"))
    filename = f"{safe_title}_{ts}.{normalized_format}"
    return StreamingResponse(
        io.BytesIO(file_bytes),
        media_type=ext_map[normalized_format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class ProfessionalReportExportRequest(BaseModel):
    """Request for professional export with styled blocks"""
    title: str
    blocks: List[Dict[str, Any]]
    generated_at: Optional[str] = None
    format: str = "pdf"


@app.post("/api/reports/export-professional")
async def export_professional_report(
    payload: ProfessionalReportExportRequest,
    request: Request,
):
    """
    Export a report with professional styling matching market report standards.
    
    Supports blocks: kpi_cards, data_table, chart, chart_row, text
    Formats: pdf, html, docx, pptx
    """
    # Allow export without authentication for testing
    # _require_current_user(request)
    
    from backend.src.services.report_export_service import get_export_service
    
    normalized_format = str(payload.format or "pdf").strip().lower()
    if normalized_format not in ("pdf", "html", "docx", "pptx"):
        raise HTTPException(status_code=400, detail="Unsupported format. Use pdf, html, docx, or pptx.")
    
    report_data = {
        "title": str(payload.title or "Professional Report"),
        "generated_at": str(payload.generated_at or datetime.utcnow().strftime("%Y-%m-%d %H:%M")),
        "blocks": list(payload.blocks or []),
    }
    
    try:
        export_service = get_export_service()
        file_bytes = export_service.export_report(report_data, format=normalized_format)
    except Exception as exc:
        logger.error(f"Professional export failed: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Export failed: {exc}") from exc
    
    content_types = {
        "pdf": "application/pdf",
        "html": "text/html; charset=utf-8",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
    
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    safe_title = _safe_export_basename(str(payload.title or "report"))
    filename = f"{safe_title}_{ts}.{normalized_format}"
    
    return StreamingResponse(
        io.BytesIO(file_bytes),
        media_type=content_types[normalized_format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )




@app.get("/api/report-schedules", response_model=List[ReportScheduleResponse])
async def list_report_schedules(
    request: Request,
):
    user_id = str(_require_current_user(request)["id"])
    existing = {
        str(item.get("report_type") or ""): item
        for item in db.list_report_schedules(user_id)
    }
    schedules: List[ReportScheduleResponse] = []
    for report_type in SCHEDULED_REPORT_LABELS:
        schedules.append(
            _serialize_report_schedule(
                existing.get(report_type)
                or db.get_report_schedule(user_id, report_type)
            )
        )
    return schedules


@app.get("/api/report-schedules/{report_type}", response_model=ReportScheduleResponse)
async def get_report_schedule(
    report_type: str,
    request: Request,
):
    if report_type not in SCHEDULED_REPORT_LABELS:
        raise HTTPException(
            status_code=404, detail="Unsupported report schedule template"
        )
    user_id = str(_require_current_user(request)["id"])
    schedule = db.get_report_schedule(user_id, report_type)
    return _serialize_report_schedule(schedule)


@app.put("/api/report-schedules/{report_type}", response_model=ReportScheduleResponse)
async def update_report_schedule(
    report_type: str,
    payload: ReportScheduleRequest,
    request: Request,
):
    if report_type not in SCHEDULED_REPORT_LABELS:
        raise HTTPException(
            status_code=404, detail="Unsupported report schedule template"
        )
    user_id = str(_require_current_user(request)["id"])
    hour = max(0, min(23, int(payload.hour)))
    minute = max(0, min(59, int(payload.minute)))
    schedule = db.upsert_report_schedule(
        user_id=user_id,
        report_type=report_type,
        enabled=bool(payload.enabled),
        hour=hour,
        minute=minute,
        weekday=0,
        timezone="Asia/Shanghai",
    )
    return _serialize_report_schedule(schedule)


# ============================================================================
# Enhanced Report Authoring Endpoints
# ============================================================================

@app.get("/api/report-authoring/templates")
async def get_report_template_gallery(
    request: Request,
):
    """
    Get pre-built report templates for quick start.
    
    Returns templates with preview, block structure, and required data sources.
    Filters out templates hidden by the current user.
    """
    from backend.src.services.report_authoring_service import get_authoring_service
    import logging
    logger = logging.getLogger(__name__)
    
    authoring_service = get_authoring_service()
    user_id = None
    try:
        user = _require_current_user(request)
        user_id = str(user["id"])
    except Exception as e:
        logger.warning(f"Failed to resolve current user for report authoring templates: {e}")
    templates = authoring_service.get_template_gallery(user_id=user_id)
    logger.info(f"Template gallery count: {len(templates)}")
    return {"templates": templates}


@app.get("/api/report-authoring/data-sources")
async def get_available_data_sources(
    request: Request,
):
    """
    List all available data sources with schema information.
    
    Users browse these when configuring data table blocks.
    """
    # _require_current_user(request)  # Disabled for testing
    
    from backend.src.services.report_authoring_service import get_authoring_service
    
    authoring_service = get_authoring_service()
    data_sources = authoring_service.get_available_data_sources()
    
    return {"data_sources": data_sources}


@app.post("/api/report-authoring/upload-image")
async def upload_report_authoring_image(
    request: Request,
    file: UploadFile = File(...),
):
    user = _require_current_user(request)
    user_id = str(user["id"])

    allowed_types = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }
    content_type = str(file.content_type or "").lower()
    if content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="Unsupported image type")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded image is empty")
    if len(file_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image exceeds 10MB limit")

    file_extension = Path(str(file.filename or "")).suffix.lower() or allowed_types[content_type]
    filename = f"{uuid4().hex}{file_extension}"
    destination = _REPORT_IMAGE_UPLOAD_DIR / filename
    destination.write_bytes(file_bytes)

    public_url = f"{str(request.base_url).rstrip('/')}/report-assets/{filename}"
    asset = db.save_report_image_asset(
        user_id=user_id,
        original_filename=str(file.filename or filename),
        mime_type=content_type,
        file_size=len(file_bytes),
        file_path=str(destination),
        public_url=public_url,
    )

    return {
        "id": asset["id"],
        "url": public_url,
        "filename": asset["original_filename"],
        "mime_type": asset["mime_type"],
        "file_size": asset["file_size"],
    }


@app.post("/api/user/hidden-gallery-templates/{template_id}")
async def hide_gallery_template(
    template_id: str,
    request: Request,
):
    """
    Hide a gallery template for the current user.
    
    The template will no longer appear in the template gallery.
    This does not delete the template, just hides it from this user's view.
    """
    user = _require_current_user(request)
    user_id = str(user["id"])
    
    db.hide_gallery_template(user_id, template_id)
    
    return {"status": "ok", "template_id": template_id, "hidden": True}


@app.delete("/api/user/hidden-gallery-templates/{template_id}")
async def unhide_gallery_template(
    template_id: str,
    request: Request,
):
    """
    Unhide a previously hidden gallery template for the current user.
    
    The template will reappear in the template gallery.
    """
    user = _require_current_user(request)
    user_id = str(user["id"])
    
    unhidden = db.unhide_gallery_template(user_id, template_id)
    
    return {"status": "ok", "template_id": template_id, "hidden": False, "was_hidden": unhidden}


@app.get("/api/user/hidden-gallery-templates")
async def list_hidden_gallery_templates(
    request: Request,
):
    """
    Get list of gallery templates hidden by the current user.
    
    Useful for showing a "restore hidden templates" UI.
    """
    user = _require_current_user(request)
    user_id = str(user["id"])
    
    hidden_template_ids = db.get_hidden_gallery_templates(user_id)
    
    return {"hidden_template_ids": hidden_template_ids}


@app.post("/api/user/pinned-templates/{template_id}")
async def pin_report_template(
    template_id: str,
    request: Request,
):
    user = _require_current_user(request)
    user_id = str(user["id"])
    db.pin_report_template(user_id, template_id)
    return {"status": "ok", "template_id": template_id, "pinned": True}


@app.delete("/api/user/pinned-templates/{template_id}")
async def unpin_report_template(
    template_id: str,
    request: Request,
):
    user = _require_current_user(request)
    user_id = str(user["id"])
    was_pinned = db.unpin_report_template(user_id, template_id)
    return {"status": "ok", "template_id": template_id, "pinned": False, "was_pinned": was_pinned}


@app.get("/api/user/pinned-templates")
async def list_pinned_report_templates(
    request: Request,
):
    user = _require_current_user(request)
    user_id = str(user["id"])
    pinned_template_ids = db.get_pinned_report_templates(user_id)
    return {"pinned_template_ids": pinned_template_ids}


class VisualQueryRequest(BaseModel):
    """Request for building a visual query"""
    data_source: str
    columns: List[str] = ["*"]
    filters: Dict[str, Any] = {}
    sort: Optional[Dict[str, str]] = None
    limit: Optional[int] = None


@app.post("/api/report-authoring/build-query")
async def build_visual_query(
    payload: VisualQueryRequest,
    request: Request,
):
    """
    Convert visual configuration to SQL query.
    
    No SQL knowledge required - users configure visually, we generate the query.
    """
    _require_current_user(request)
    
    from backend.src.services.report_authoring_service import get_authoring_service
    
    authoring_service = get_authoring_service()
    query_info = authoring_service.build_visual_query({
        "data_source": payload.data_source,
        "columns": payload.columns,
        "filters": payload.filters,
        "sort": payload.sort,
        "limit": payload.limit
    })
    
    return query_info


class BlockPreviewRequest(BaseModel):
    """Request for previewing block data"""
    type: str
    config: Dict[str, Any]
    limit: int = 10


@app.post("/api/report-authoring/preview-block")
async def preview_block_data(
    payload: BlockPreviewRequest,
    request: Request,
):
    """
    Preview data for a block before adding it to the report.
    
    Shows sample data so users can verify the configuration.
    ERP block types are routed to the ERP query layer.
    """
    # Allow preview without authentication for testing
    # _require_current_user(request)

    # Route ERP block types to ERP query layer
    if payload.type.startswith("erp_"):
        config = payload.config
        product_id = config.get("product_id", "")
        group_by_map = {
            "erp_sales_summary":     "month",
            "erp_customer_breakdown": "customer",
            "erp_monthly_trend":     "month",
            "erp_delivery_status":   "date",
            "erp_vs_market":         "date",
        }
        group_by = group_by_map.get(payload.type, "month")

        catalog_entry = _ERP_TABLE_MAP.get(product_id)
        if not catalog_entry:
            return {"type": payload.type, "data": [], "columns": [], "error": f"Unknown product: {product_id}"}

        from backend.src.db.engine_factory import get_erp_engine
        from sqlalchemy import text as sa_text

        period_days = config.get("period_days")
        date_from = config.get("date_from")
        if not date_from:
            days = int(period_days) if period_days else 365
            date_from = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        date_to = config.get("date_to")
        table = catalog_entry["table"]
        where_parts = ["PLANDATE >= :date_from"]
        params: dict = {"date_from": date_from, "limit": min(payload.limit, 50)}
        if date_to:
            where_parts.append("PLANDATE <= :date_to")
            params["date_to"] = date_to
        where = "WHERE " + " AND ".join(where_parts)

        try:
            engine = get_erp_engine()
            with engine.connect() as conn:
                if group_by == "customer":
                    sql = f"""
                        SELECT COALESCE(CUST_NAME, '(Unknown)') AS customer,
                               ROUND(SUM(FQTY_ORIGIN),2) AS total_quantity,
                               ROUND(SUM(FAMOUNT),2) AS total_amount,
                               ROUND(SUM(FQTY_ORIGIN*FPRICE)/NULLIF(SUM(FQTY_ORIGIN),0),2) AS avg_sale_price,
                               COUNT(*) AS order_count
                        FROM `{table}` {where}
                        GROUP BY CUST_NAME ORDER BY total_quantity DESC LIMIT :limit
                    """
                    keys = ["customer", "total_quantity", "total_amount", "avg_sale_price", "order_count"]
                elif group_by == "month":
                    sql = f"""
                        SELECT DATE_FORMAT(PLANDATE,'%Y-%m') AS month,
                               ROUND(SUM(FQTY_ORIGIN),2) AS total_quantity,
                               ROUND(SUM(FAMOUNT),2) AS total_amount,
                               ROUND(SUM(FQTY_ORIGIN*FPRICE)/NULLIF(SUM(FQTY_ORIGIN),0),2) AS avg_sale_price,
                               COUNT(*) AS order_count
                        FROM `{table}` {where}
                        GROUP BY month ORDER BY month DESC LIMIT :limit
                    """
                    keys = ["month", "total_quantity", "total_amount", "avg_sale_price", "order_count"]
                else:
                    sql = f"""
                        SELECT DATE(PLANDATE) AS date,
                               COALESCE(CUST_NAME,'(Unknown)') AS customer,
                               ROUND(FQTY_ORIGIN,2) AS quantity,
                               ROUND(FPRICE,2) AS sale_price,
                               ROUND(FAMOUNT,2) AS amount
                        FROM `{table}` {where}
                        ORDER BY PLANDATE DESC LIMIT :limit
                    """
                    keys = ["date", "customer", "quantity", "sale_price", "amount"]

                rows = conn.execute(sa_text(sql), params).fetchall()
                data = []
                for row in rows:
                    item = {}
                    for i, k in enumerate(keys):
                        v = row[i]
                        if hasattr(v, "isoformat"):
                            v = str(v)[:10]
                        elif v is not None and k not in ("month", "customer", "date"):
                            try:
                                v = float(v)
                            except (TypeError, ValueError):
                                v = str(v)
                        item[k] = v
                    data.append(item)

            return {
                "type": payload.type,
                "product_id": product_id,
                "product_name_en": catalog_entry["name_en"],
                "product_name_zh": catalog_entry["name_zh"],
                "columns": keys,
                "data": data,
                "row_count": len(data),
            }
        except Exception as exc:
            return {"type": payload.type, "data": [], "columns": [], "error": str(exc)}

    from backend.src.services.report_authoring_service import get_authoring_service

    authoring_service = get_authoring_service()
    preview_data = authoring_service.preview_block_data({
        "type": payload.type,
        "config": payload.config
    }, limit=payload.limit)

    return preview_data


class BlockSuggestionsRequest(BaseModel):
    """Request for block suggestions"""
    current_blocks: List[Dict[str, Any]]
    context: Dict[str, Any] = {}


class ReportCopilotPlanRequest(BaseModel):
    """Request for report-builder copilot planning."""
    objective: str = ""
    current_blocks: List[Dict[str, Any]] = []
    language: str = "zh"


@app.post("/api/report-authoring/suggest-blocks")
async def suggest_next_blocks(
    payload: BlockSuggestionsRequest,
    request: Request,
):
    """
    AI-powered suggestions for next block to add.
    
    Analyzes current report and suggests relevant additions.
    """
    _require_current_user(request)
    
    from backend.src.services.report_authoring_service import get_authoring_service
    
    authoring_service = get_authoring_service()
    suggestions = authoring_service.suggest_next_block(
        payload.current_blocks,
        payload.context
    )
    
    return {"suggestions": suggestions}


@app.post("/api/report-authoring/copilot-plan")
async def build_report_copilot_plan(
    payload: ReportCopilotPlanRequest,
    request: Request,
):
    """Build a template/data/readiness plan for the report builder."""
    _require_current_user(request)

    from backend.src.services.report_authoring_service import get_authoring_service
    from backend.src.services.report_builder_copilot import report_builder_copilot

    authoring_service = get_authoring_service()
    user_id = None
    try:
        user = _require_current_user(request)
        user_id = str(user["id"])
    except Exception:
        user_id = None
    templates = authoring_service.get_template_gallery(user_id=user_id)
    data_sources = authoring_service.get_available_data_sources()
    objective = str(payload.objective or "").strip()
    if not objective:
        objective = "Build a boss-ready report from current selected report blocks."

    return report_builder_copilot.build_plan(
        objective=objective,
        current_blocks=payload.current_blocks,
        templates=templates,
        data_sources=data_sources,
        language=payload.language,
    )


class ReportValidationRequest(BaseModel):
    """Request for report validation"""
    blocks: List[Dict[str, Any]]


@app.post("/api/report-authoring/validate")
async def validate_report_structure(
    payload: ReportValidationRequest,
    request: Request,
):
    """
    Validate report structure and provide quality score.
    
    Checks for logical structure, data availability, and completeness.
    """
    _require_current_user(request)
    
    from backend.src.services.report_authoring_service import get_authoring_service
    
    authoring_service = get_authoring_service()
    validation = authoring_service.validate_report_structure(payload.blocks)
    
    return validation


# ---------------------------------------------------------------------------
# ERP / Sales Data API — used by Report Builder ERP block types
# ---------------------------------------------------------------------------

# Full catalog of ERP sale tables: product_id → (display_name_en, display_name_zh, table_name)
_ERP_PRODUCT_CATALOG: list[dict] = [
    {"product_id": "dcpd",       "name_en": "DCPD",              "name_zh": "双环戊二烯", "table": "sale_erp_v_双环戊二烯_data"},
    {"product_id": "eth_carbon",  "name_en": "Ethylene Carbon Black", "name_zh": "乙烯炭黑料", "table": "sale_erp_v_乙烯炭黑料_data"},
    {"product_id": "hex",         "name_en": "Industrial Hexane", "name_zh": "工业己烷",    "table": "sale_erp_v_工业己烷_data"},
    {"product_id": "cracked_c9",  "name_en": "Cracked C9",       "name_zh": "工业用裂解碳九","table": "sale_erp_v_工业用裂解碳九_data"},
    {"product_id": "cracked_c5",  "name_en": "Cracked C5",       "name_zh": "工业用裂解碳五","table": "sale_erp_v_工业用裂解碳五_data"},
    {"product_id": "isoprene",    "name_en": "Isoprene",         "name_zh": "异戊二烯",    "table": "sale_erp_v_异戊二烯_data"},
    {"product_id": "blowing_agent","name_en": "Blowing Agent",   "name_zh": "戊烷发泡剂",  "table": "sale_erp_v_戊烷发泡剂_data"},
    {"product_id": "raffinate_c5","name_en": "Raffinate C5",     "name_zh": "抽余碳五",    "table": "sale_erp_v_抽余碳五_data"},
    {"product_id": "trimethylbenzene","name_en": "Trimethylbenzene","name_zh": "混三甲苯",  "table": "sale_erp_v_混三甲苯_data"},
    {"product_id": "c5_resin",    "name_en": "C5 Petroleum Resin","name_zh": "碳五石油树脂","table": "sale_erp_v_碳五石油树脂_data"},
    {"product_id": "piperylene",  "name_en": "Piperylene",       "name_zh": "间戊二烯",    "table": "sale_erp_v_间戊二烯_data"},
    {"product_id": "sis",         "name_en": "SIS",              "name_zh": "SIS",          "table": "sale_erp_v_sis_d2015_data"},
]

# Map product_id → catalog entry for fast lookup
_ERP_TABLE_MAP: dict[str, dict] = {p["product_id"]: p for p in _ERP_PRODUCT_CATALOG}

# Market price tables (Longzhong) for vs-market comparisons
_LZ_TABLE_MAP: dict[str, str] = {
    "cracked_c5": "lz_v_裂解c5_data",
    "cracked_c9": "lz_v_裂解c9_data",
    "isoprene":   "lz_v_异戊二烯_data",
    "piperylene":  "lz_v_间戊二烯_data",
    "dcpd":        "lz_v_双环戊二烯_data",
    "raffinate_c5":"lz_v_抽余碳五_data",
    "sis":         "lz_v_sis_data",
    "c5_resin":    "lz_v_碳五石油树脂_data",
    "blowing_agent":"lz_v_戊烷泡发剂_data",
}


def _require_erp_access(request: Request) -> dict:
    """Require authenticated user. Returns user dict or raises 403."""
    user = _require_current_user(request)
    role = str(user.get("role", "viewer")).lower()
    if role not in ("admin", "analyst", "erp_reader"):
        raise HTTPException(status_code=403, detail="ERP data access requires analyst or admin role")
    return user


@app.get("/api/erp/catalog")
async def erp_catalog(request: Request):
    """
    List all ERP sale products with date range metadata.
    Exempt from auth — contains product names only, no sensitive data.
    Actual ERP data flows through the authenticated preview-block endpoint.
    """

    from backend.src.db.engine_factory import get_erp_engine
    from sqlalchemy import text as sa_text

    try:
        engine = get_erp_engine()
    except RuntimeError:
        return {"products": _ERP_PRODUCT_CATALOG, "note": "date_range unavailable — DB not configured"}

    results = []
    try:
        with engine.connect() as conn:
            for product in _ERP_PRODUCT_CATALOG:
                try:
                    row = conn.execute(
                        sa_text(
                            f"SELECT MIN(date) as min_date, MAX(date) as max_date, COUNT(*) as row_count "
                            f"FROM `{product['table']}`"
                        )
                    ).fetchone()
                    results.append({
                        **product,
                        "date_from": str(row[0]) if row and row[0] else None,
                        "date_to":   str(row[1]) if row and row[1] else None,
                        "row_count": int(row[2]) if row and row[2] else 0,
                    })
                except Exception:
                    results.append({**product, "date_from": None, "date_to": None, "row_count": 0})
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"ERP database unavailable: {exc}")

    return {"products": results}


@app.get("/api/erp/sales")
async def erp_sales(
    request: Request,
    product: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    group_by: str = "date",  # date | month | customer
    limit: int = 500,
):
    """
    Query ERP sale records for a product with clean field aliases.
    group_by: date = raw rows by date, month = monthly aggregates, customer = by customer
    """
    _require_erp_access(request)

    catalog_entry = _ERP_TABLE_MAP.get(product)
    if not catalog_entry:
        raise HTTPException(status_code=404, detail=f"Unknown ERP product: {product}")

    table = catalog_entry["table"]
    limit = min(max(1, limit), 2000)

    from backend.src.db.engine_factory import get_erp_engine
    from sqlalchemy import text as sa_text

    try:
        engine = get_erp_engine()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured")

    # Build date filter
    date_clauses = []
    params: dict = {}
    if date_from:
        date_clauses.append("PLANDATE >= :date_from")
        params["date_from"] = date_from
    if date_to:
        date_clauses.append("PLANDATE <= :date_to")
        params["date_to"] = date_to
    where = ("WHERE " + " AND ".join(date_clauses)) if date_clauses else ""

    try:
        with engine.connect() as conn:
            if group_by == "month":
                sql = f"""
                    SELECT
                        DATE_FORMAT(PLANDATE, '%Y-%m') AS month,
                        ROUND(SUM(FQTY_ORIGIN), 2)    AS total_quantity,
                        ROUND(SUM(FAMOUNT), 2)         AS total_amount,
                        ROUND(
                            SUM(FQTY_ORIGIN * FPRICE) / NULLIF(SUM(FQTY_ORIGIN), 0), 2
                        )                             AS avg_sale_price,
                        COUNT(*)                      AS order_count
                    FROM `{table}` {where}
                    GROUP BY DATE_FORMAT(PLANDATE, '%Y-%m')
                    ORDER BY month DESC
                    LIMIT :limit
                """
                params["limit"] = limit
                rows = conn.execute(sa_text(sql), params).fetchall()
                keys = ["month", "total_quantity", "total_amount", "avg_sale_price", "order_count"]
            elif group_by == "customer":
                sql = f"""
                    SELECT
                        COALESCE(CUST_NAME, '(Unknown)')            AS customer,
                        ROUND(SUM(FQTY_ORIGIN), 2)                  AS total_quantity,
                        ROUND(SUM(FAMOUNT), 2)                       AS total_amount,
                        ROUND(
                            SUM(FQTY_ORIGIN * FPRICE) / NULLIF(SUM(FQTY_ORIGIN), 0), 2
                        )                                           AS avg_sale_price,
                        COUNT(*)                                    AS order_count
                    FROM `{table}` {where}
                    GROUP BY CUST_NAME
                    ORDER BY total_quantity DESC
                    LIMIT :limit
                """
                params["limit"] = limit
                rows = conn.execute(sa_text(sql), params).fetchall()
                keys = ["customer", "total_quantity", "total_amount", "avg_sale_price", "order_count"]
            else:
                # Raw date rows
                sql = f"""
                    SELECT
                        DATE(PLANDATE)                              AS date,
                        COALESCE(CUST_NAME, '(Unknown)')            AS customer,
                        ROUND(FQTY_ORIGIN, 2)                       AS quantity,
                        ROUND(FPRICE, 2)                            AS sale_price,
                        ROUND(FAMOUNT, 2)                           AS amount
                    FROM `{table}` {where}
                    ORDER BY PLANDATE DESC
                    LIMIT :limit
                """
                params["limit"] = limit
                rows = conn.execute(sa_text(sql), params).fetchall()
                keys = ["date", "customer", "quantity", "sale_price", "amount"]

            data = []
            for row in rows:
                item = {}
                for i, key in enumerate(keys):
                    v = row[i]
                    if hasattr(v, "isoformat"):
                        v = str(v)[:10]
                    elif v is not None:
                        try:
                            v = float(v) if key not in ("month", "customer", "date") else str(v)
                        except (TypeError, ValueError):
                            v = str(v)
                    item[key] = v
                data.append(item)

    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"ERP query failed: {exc}")

    return {
        "product_id": product,
        "product_name_zh": catalog_entry["name_zh"],
        "product_name_en": catalog_entry["name_en"],
        "group_by": group_by,
        "date_from": date_from,
        "date_to": date_to,
        "row_count": len(data),
        "data": data,
    }


@app.get("/api/erp/sales-vs-market")
async def erp_sales_vs_market(
    request: Request,
    product: str,
    period_days: int = 90,
):
    """
    Return time-series of Ecisco's actual sale price vs Longzhong market reference price.
    Used for the 'Actual vs Market Price' chart block.
    """
    _require_erp_access(request)

    catalog_entry = _ERP_TABLE_MAP.get(product)
    if not catalog_entry:
        raise HTTPException(status_code=404, detail=f"Unknown ERP product: {product}")

    lz_table = _LZ_TABLE_MAP.get(product)

    from backend.src.db.engine_factory import get_erp_engine
    from sqlalchemy import text as sa_text

    date_from = (datetime.utcnow() - timedelta(days=period_days)).strftime("%Y-%m-%d")
    erp_table = catalog_entry["table"]

    try:
        engine = get_erp_engine()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured")

    try:
        with engine.connect() as conn:
            # ERP daily weighted sale price
            erp_sql = f"""
                SELECT
                    DATE(PLANDATE) AS day,
                    ROUND(SUM(FQTY_ORIGIN * FPRICE) / NULLIF(SUM(FQTY_ORIGIN), 0), 2) AS sale_price,
                    ROUND(SUM(FQTY_ORIGIN), 2) AS total_qty
                FROM `{erp_table}`
                WHERE PLANDATE >= :date_from
                GROUP BY DATE(PLANDATE)
                ORDER BY day
            """
            erp_rows = conn.execute(sa_text(erp_sql), {"date_from": date_from}).fetchall()

            market_rows = []
            if lz_table:
                # Try to get market reference price from Longzhong table
                # LZ tables have `Unnamed: 0` as date column and supplier columns
                try:
                    lz_sql = f"""
                        SELECT `Unnamed: 0` AS day, AVG(price) AS market_price
                        FROM (
                            SELECT `Unnamed: 0`, price
                            FROM (
                                SELECT `Unnamed: 0`,
                                       CAST(COALESCE(
                                           {','.join([f'`{col}`' for col in ['price', 'FPRICE', '市场价', '均价']])}
                                       ) AS DECIMAL(20,4)) AS price
                                FROM `{lz_table}`
                                WHERE `Unnamed: 0` >= :date_from
                            ) t WHERE price IS NOT NULL AND price > 0
                        ) t2
                        GROUP BY day
                        ORDER BY day
                    """
                    market_rows = conn.execute(sa_text(lz_sql), {"date_from": date_from}).fetchall()
                except Exception:
                    # LZ table has different structure — try a simpler approach
                    try:
                        # Get first numeric column as market price proxy
                        info_sql = f"SELECT `Unnamed: 0` FROM `{lz_table}` WHERE `Unnamed: 0` >= :date_from LIMIT 1"
                        conn.execute(sa_text(info_sql), {"date_from": date_from})
                    except Exception:
                        pass

        # Build response with merged series
        erp_by_day = {}
        for row in erp_rows:
            day = str(row[0])[:10] if row[0] else None
            if day:
                erp_by_day[day] = {"sale_price": float(row[1]) if row[1] else None, "total_qty": float(row[2]) if row[2] else 0}

        mkt_by_day = {}
        for row in market_rows:
            day = str(row[0])[:10] if row[0] else None
            if day:
                mkt_by_day[day] = float(row[1]) if row[1] else None

        all_days = sorted(set(list(erp_by_day.keys()) + list(mkt_by_day.keys())))
        series = []
        for day in all_days:
            erp_entry = erp_by_day.get(day, {})
            series.append({
                "date": day,
                "sale_price": erp_entry.get("sale_price"),
                "market_price": mkt_by_day.get(day),
                "total_qty": erp_entry.get("total_qty", 0),
            })

    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"ERP vs market query failed: {exc}")

    return {
        "product_id": product,
        "product_name_en": catalog_entry["name_en"],
        "product_name_zh": catalog_entry["name_zh"],
        "period_days": period_days,
        "has_market_data": lz_table is not None,
        "series": series,
    }


@app.post(
    "/api/report-schedules/{report_type}/run-now",
    response_model=GeneratedReportResponse,
)
async def run_report_now(
    report_type: str,
    request: Request,
):
    if report_type not in SCHEDULED_REPORT_LABELS:
        raise HTTPException(
            status_code=404, detail="Unsupported report schedule template"
        )
    user_id = str(_require_current_user(request)["id"])
    created = _create_scheduled_report_for_user(user_id, report_type, mark_as_read=True)
    return _serialize_generated_report(created)


@app.patch("/api/sessions/{session_id}")
async def rename_session(
    session_id: str,
    body: RenameSessionRequest,
    request: Request,
):
    user_id = str(_require_current_user(request)["id"])
    new_title = (body.title or "").strip()
    if not new_title:
        raise HTTPException(status_code=400, detail="Title cannot be empty")
    if len(new_title) > 255:
        raise HTTPException(status_code=400, detail="Title is too long")

    renamed = db.rename_session(session_id, user_id, new_title)
    if not renamed:
        raise HTTPException(status_code=404, detail="Session not found")

    return {"status": "ok", "session_id": session_id, "title": new_title}


@app.delete("/api/sessions/{session_id}")
async def delete_session(
    session_id: str,
    request: Request,
):
    user_id = str(_require_current_user(request)["id"])
    deleted = db.delete_session(session_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")

    return {"status": "ok", "session_id": session_id}


@app.get("/health")
async def health():
    return {"status": "ok", "mode": LLM_MODE}


@app.get("/api/rag/health")
async def rag_health():
    """Return RAG vector-store health: embedding backend, collection counts, index size."""
    try:
        from backend.src.rag.vector_store import KnowledgeBase, LexicalKnowledgeBase, DisabledKnowledgeBase
        from backend.src.rag.vector_store import embedding_model_id
    except Exception as e:
        return {"status": "error", "message": str(e)}

    try:
        embedding_info = embedding_model_id()
    except Exception as e:
        embedding_info = {"error": str(e)}

    try:
        kb = KnowledgeBase()
        # Map collection names → KnowledgeBase attribute names
        _COLL_ATTR_MAP = {
            "industry_reports": "collection",
            "weekly_reports": "_weekly_collection",
            "past_decisions": "_decisions_collection",
            "market_signals": "_market_signals_collection",
            "news_events": "_news_events_collection",
            "product_catalog": "_product_catalog_collection",
        }
        collections = {}
        for name, attr in _COLL_ATTR_MAP.items():
            try:
                coll = getattr(kb, attr, None) or kb.client.get_collection(name)
                collections[name] = {"count": coll.count()}
            except Exception:
                collections[name] = {"count": -1, "error": "unavailable"}
        return {
            "status": "ok",
            "embedding": embedding_info,
            "collections": collections,
        }
    except Exception as e:
        # Fall back to describing what's available
        try:
            import chromadb
            client = chromadb.PersistentClient(path="/home/ysk2025/EDIA_5.1.2/chroma_db")
            all_collections = [c.name for c in client.list_collections()]
            return {
                "status": "degraded",
                "embedding": embedding_info,
                "collections": {name: {"count": -1, "error": "could not access"} for name in all_collections},
                "error": str(e),
            }
        except Exception:
            return {"status": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# Background Intelligence Skills API
# ---------------------------------------------------------------------------

@app.get("/api/skills/background/status")
async def background_skills_status():
    """Return latest results from all background intelligence skills."""
    try:
        from backend.src.skills.background import background_skill_scheduler
        results = background_skill_scheduler.get_all_latest()
        return {
            "total_skills": len(background_skill_scheduler.skills),
            "results": {
                name: {
                    "ran_at": r.ran_at,
                    "status": r.status,
                    "summary": r.summary,
                    "alert_count": len(r.alerts),
                    "alerts": r.alerts[:10],
                }
                for name, r in results.items()
            },
        }
    except Exception as exc:
        return {"error": str(exc)}


@app.get("/api/skills/background/{skill_name}")
async def background_skill_detail(skill_name: str):
    """Return full detail for a specific background skill."""
    try:
        from backend.src.skills.background import background_skill_scheduler
        result = background_skill_scheduler.get_skill_result(skill_name)
        if result is None:
            return {"error": f"no result for skill '{skill_name}'"}
        return {
            "skill_name": result.skill_name,
            "ran_at": result.ran_at,
            "status": result.status,
            "summary": result.summary,
            "data": result.data,
            "alerts": result.alerts,
        }
    except Exception as exc:
        return {"error": str(exc)}


@app.get("/api/skills/background/alerts/active")
async def background_skills_active_alerts():
    """Return all active alerts from background skills (for dashboard)."""
    try:
        from backend.src.skills.background import background_skill_scheduler
        results = background_skill_scheduler.get_all_latest()
        all_alerts = []
        for name, r in results.items():
            for alert in r.alerts:
                all_alerts.append({
                    "skill": name,
                    "alert": alert,
                    "ran_at": r.ran_at,
                })
        return {"total_alerts": len(all_alerts), "alerts": all_alerts}
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Research Framework API (market-research + data-analysis rigor)
# ---------------------------------------------------------------------------

@app.post("/api/intelligence/research/brief/validate")
async def validate_research_brief_endpoint(request: Request, payload: ResearchBriefPayload):
    """Validate a market research brief before running deep analysis."""
    _require_current_user(request)
    from backend.src.intelligence.research_framework import (
        ResearchBrief,
        validate_research_brief,
    )

    brief = ResearchBrief(
        decision=payload.decision,
        target_customer=payload.target_customer,
        geography=payload.geography,
        category_or_substitute_set=payload.category_or_substitute_set,
        time_horizon=payload.time_horizon,
        must_answer_questions=list(payload.must_answer_questions or []),
        evidence_bar=payload.evidence_bar,
    )
    issues = validate_research_brief(brief)
    return {
        "ok": len(issues) == 0,
        "issues": issues,
        "brief": {
            "decision": brief.decision,
            "target_customer": brief.target_customer,
            "geography": brief.geography,
            "category_or_substitute_set": brief.category_or_substitute_set,
            "time_horizon": brief.time_horizon,
            "must_answer_questions": brief.must_answer_questions,
            "evidence_bar": brief.evidence_bar,
        },
    }


@app.post("/api/intelligence/research/search")
async def research_multi_engine_search(request: Request, payload: MultiEngineSearchPayload):
    """Run multi-engine web/news search for research tasks."""
    _require_current_user(request)
    from backend.src.intelligence.web_search_service import get_web_search

    query = (payload.query or "").strip()
    if not query:
        raise HTTPException(status_code=422, detail="query is required")

    engines = payload.engines or ["google", "duckduckgo", "bing-int", "baidu"]
    ws = get_web_search()
    results = ws.search_multi_engine(
        query=query,
        engines=engines,
        max_results_per_engine=max(1, min(10, int(payload.max_results_per_engine))),
        include_news=bool(payload.include_news),
        timelimit=(payload.timelimit or "w"),
    )
    return {
        "query": query,
        "engines": engines,
        "count": len(results),
        "results": [r.to_dict() for r in results],
    }


@app.post("/api/intelligence/research/evidence-score")
async def research_evidence_score(request: Request):
    """Score evidence quality for decision confidence calibration."""
    _require_current_user(request)
    body = await request.json()
    from backend.src.intelligence.research_framework import EvidenceItem, grade_evidence

    raw_items = body.get("items") or []
    items: list[EvidenceItem] = []
    for entry in raw_items:
        if not isinstance(entry, dict):
            continue
        items.append(
            EvidenceItem(
                title=str(entry.get("title") or ""),
                url=str(entry.get("url") or ""),
                source_type=str(entry.get("source_type") or "news"),
                published_date=str(entry.get("published_date") or ""),
                corroboration_count=int(entry.get("corroboration_count") or 1),
            )
        )

    score = grade_evidence(items)
    return {
        "count": len(items),
        "score": score,
    }


# ---------------------------------------------------------------------------
# Proactive Alerts API  (Phase 6 — Perception Worker)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Decision Log API — closed-loop AI recommendation tracking
# ---------------------------------------------------------------------------

@app.post("/api/decision-log")
async def create_decision_log(request: Request):
    """Save an AI pricing recommendation to the decision log."""
    current_user = _require_current_user(request)
    body = await request.json()
    if _is_excluded_product_id(body.get("product_id")) or _is_excluded_product_name(
        body.get("product_name")
    ):
        raise HTTPException(status_code=404, detail="Unknown product")
    try:
        raw_confidence = body.get("ai_confidence", 0)
        ai_confidence = 0.0 if raw_confidence in (None, "") else float(raw_confidence)
        entry = decision_log_service.create(
            product_id=body["product_id"],
            product_name=body.get("product_name", body["product_id"]),
            ai_price_low=float(body["ai_price_low"]),
            ai_price_high=float(body["ai_price_high"]),
            price_unit=body.get("price_unit"),
            ai_action=body.get("ai_action", ""),
            ai_confidence=ai_confidence,
            ai_reasoning=body.get("ai_reasoning", ""),
            user_id=str(current_user.get("id") or ""),
            session_id=body.get("session_id"),
            market_snapshot=body.get("market_snapshot"),
            source=body.get("source", "chat"),
        )
        await _market_live_hub.publish(
            "decision_log_updated",
            {
                "change": "created",
                "entry_id": entry.get("id"),
                "product_id": entry.get("product_id"),
            },
        )
        return entry
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/api/decision-log")
async def list_decision_log(
    product_id: Optional[str] = None,
    status: Optional[str] = None,
    days: int = 90,
):
    """List decision log entries with optional filters."""
    if _is_excluded_product_id(product_id):
        return []
    entries = decision_log_service.list(product_id=product_id, status=status, days=days)
    return _filter_excluded_product_entries(entries)


@app.patch("/api/decision-log/{entry_id}/execute")
async def mark_decision_executed(entry_id: str, request: Request):
    """Record the actual executed price for a decision."""
    body = await request.json()
    try:
        entry = decision_log_service.mark_executed(
            entry_id=entry_id,
            executed_price=float(body["executed_price"]),
            executed_by=body.get("executed_by"),
        )
        await _market_live_hub.publish(
            "decision_log_updated",
            {
                "change": "executed",
                "entry_id": entry.get("id"),
                "product_id": entry.get("product_id"),
                "price_in_range": entry.get("price_in_range"),
            },
        )
        return entry
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.patch("/api/decision-log/{entry_id}/outcome")
async def mark_decision_outcome(entry_id: str, request: Request):
    """Record the outcome of a decision (deal / no_deal / etc.)."""
    body = await request.json()
    try:
        entry = decision_log_service.mark_outcome(
            entry_id=entry_id,
            outcome=body["outcome"],
            outcome_reason=body.get("outcome_reason"),
            outcome_competitor_price=body.get("outcome_competitor_price"),
            outcome_note=body.get("outcome_note"),
        )
        await _market_live_hub.publish(
            "decision_log_updated",
            {
                "change": "outcome",
                "entry_id": entry.get("id"),
                "product_id": entry.get("product_id"),
                "outcome": entry.get("outcome"),
            },
        )
        return entry
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/api/decision-log/accuracy")
async def get_decision_accuracy(product_id: Optional[str] = None):
    """Return per-product accuracy metrics from closed decisions."""
    if _is_excluded_product_id(product_id):
        return []
    stats = decision_log_service.accuracy_stats(product_id=product_id)
    return _filter_excluded_product_entries(stats)


@app.delete("/api/decision-log/{entry_id}")
async def delete_decision_log(entry_id: str, request: Request):
    """Hard-delete a decision log entry."""
    _require_current_user(request)
    try:
        decision_log_service.delete(entry_id)
        await _market_live_hub.publish(
            "decision_log_updated",
            {"change": "deleted", "entry_id": entry_id},
        )
        return {"ok": True}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.patch("/api/decision-log/{entry_id}")
async def update_decision_log(entry_id: str, request: Request):
    """Update editable fields on a decision log entry."""
    _require_current_user(request)
    body = await request.json()
    try:
        ai_price_low = body.get("ai_price_low")
        ai_price_high = body.get("ai_price_high")
        entry = decision_log_service.update(
            entry_id=entry_id,
            ai_price_low=float(ai_price_low) if ai_price_low is not None else None,
            ai_price_high=float(ai_price_high) if ai_price_high is not None else None,
            price_unit=body.get("price_unit"),
            ai_action=body.get("ai_action"),
            ai_reasoning=body.get("ai_reasoning"),
            outcome_note=body.get("outcome_note"),
            executed_price=float(body["executed_price"]) if body.get("executed_price") is not None else None,
        )
        await _market_live_hub.publish(
            "decision_log_updated",
            {"change": "updated", "entry_id": entry_id},
        )
        return entry
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/api/alerts")
async def get_alerts():
    """Return all non-dismissed alerts from MySQL, with unread_count for badge."""
    return alert_worker.get_alerts()


@app.post("/api/alerts/read")
async def mark_alerts_read():
    """Mark all unread alerts as read (called when user opens the alert panel)."""
    updated = alert_worker.mark_all_read()
    return {"status": "ok", "marked_read": updated}


@app.post("/api/alerts/dismiss/{alert_id}")
async def dismiss_alert(alert_id: str):
    """Permanently dismiss a single alert so it no longer appears in the panel."""
    found = alert_worker.dismiss_alert(alert_id)
    if not found:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=404, detail="Alert not found or already dismissed"
        )
    return {"status": "ok", "dismissed_id": alert_id}


@app.post("/api/alerts/scan")
async def trigger_alert_scan():
    """Manually trigger an immediate alert scan (useful for testing / on data ingest)."""
    new_alerts = alert_worker.scan()
    return {
        "status": "scan_complete",
        "new_alerts_persisted": len(new_alerts),
        "alerts": new_alerts,
    }


# --- New V2 API Endpoints ---


@app.get("/api/v2/market/products")
async def get_products():
    """Returns the list of tracked products."""
    _ensure_market_runtime_fresh()
    return [n for n in domain_graph.nodes if not _is_excluded_product_id(getattr(n, "id", ""))]


def _load_sis_history_from_v_market_data(days: int) -> list[dict]:
    """Return dense daily SIS history for the last N days from v_market_data.

    Source rows are quote dates only; we carry forward the last known DB quote so
    the chart can render a full six-month daily timeline.
    """
    from backend.src.db.sqlite_db import db as _app_db
    from datetime import date, timedelta
    from sqlalchemy import text as _text

    today = date.today()
    cutoff_dt = today - timedelta(days=max(days, 1))

    with _app_db.engine.connect() as conn:
        rows = conn.execute(
            _text(
                "SELECT biz_date, AVG(CAST(tax_price AS DECIMAL(18,4))) AS avg_price "
                "FROM v_market_data "
                "WHERE LOWER(material_name) = 'sis' "
                "  AND supplier_name LIKE '%巴陵1105%' "
                "  AND tax_price REGEXP '^-?[0-9]+(\\\\.[0-9]+)?$' "
                "  AND biz_date IS NOT NULL "
                "  AND biz_date <= CURDATE() "
                "GROUP BY biz_date "
                "ORDER BY biz_date ASC"
            )
        ).fetchall()

    if not rows:
        return []

    by_date: dict[str, float] = {}
    ordered_points: list[tuple[str, float]] = []
    for biz_date, avg_price in rows:
        if biz_date is None or avg_price is None:
            continue
        d = str(biz_date)[:10]
        p = float(avg_price)
        by_date[d] = p
        ordered_points.append((d, p))

    if not ordered_points:
        return []

    seed_price = None
    cutoff_iso = cutoff_dt.isoformat()
    for d, p in ordered_points:
        if d <= cutoff_iso:
            seed_price = p
        else:
            break

    history: list[dict] = []
    current_price = seed_price
    cursor = cutoff_dt
    while cursor <= today:
        key = cursor.isoformat()
        if key in by_date:
            current_price = by_date[key]
        if current_price is not None:
            history.append({"date": key, "price": round(float(current_price), 2)})
        cursor += timedelta(days=1)

    return history


@app.get("/api/v2/market/history/{product_id}")
async def get_product_history(product_id: str, days: int = 180):
    """Returns historical price data for charting (default last 180 days ≈ 6 months),
    with daily ERP sales volume when available."""
    from backend.src.service.erp_kpi_service import ErpKPIService
    from datetime import date, timedelta
    started_at = time.perf_counter()

    _ensure_product_allowed(product_id)
    _ensure_market_runtime_fresh()

    # Compute cutoff date from today
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    if product_id in {"sis", "sis_rubber"}:
        history = _load_sis_history_from_v_market_data(days)
    else:
        history = market_data.get_price_history(product_id, cutoff_date=None)
    # Slice to the requested window
    history = [p for p in history if p["date"] >= cutoff]

    erp_kpi = ErpKPIService()
    daily_sales = erp_kpi.get_daily_sales(product_id, days=days)
    if not daily_sales:
        _log_market_timing(
            "history",
            started_at,
            product_id=product_id,
            days=days,
            points=len(history),
            sales_rows=0,
        )
        return history
    # Merge volume into price history by date
    vol_by_date = {row["date"]: row["volume"] for row in daily_sales}
    merged = []
    for point in history:
        entry = {"date": point["date"], "price": point["price"]}
        if point["date"] in vol_by_date:
            entry["volume"] = vol_by_date[point["date"]]
        merged.append(entry)
    _log_market_timing(
        "history",
        started_at,
        product_id=product_id,
        days=days,
        points=len(merged),
        sales_rows=len(daily_sales),
    )
    return merged


@app.get("/api/v2/market/snapshot")
async def get_market_snapshot():
    """Returns latest prices for all products (for Dashboard)."""
    started_at = time.perf_counter()
    _ensure_market_runtime_fresh()
    now = time.time()
    with _market_runtime_state_lock:
        cached_snapshot = _market_snapshot_cache.get("data")
        cached_ts = float(_market_snapshot_cache.get("ts") or 0.0)
    if cached_snapshot is not None and (now - cached_ts) < _MARKET_SNAPSHOT_TTL:
        _log_market_timing(
            "snapshot",
            started_at,
            cache_hit=1,
            count=len(cached_snapshot),
        )
        return copy.deepcopy(cached_snapshot)

    try:
        loop = asyncio.get_event_loop()
        snapshot = await loop.run_in_executor(None, _compute_market_snapshot)
        with _market_runtime_state_lock:
            _market_snapshot_cache["data"] = copy.deepcopy(snapshot)
            _market_snapshot_cache["ts"] = time.time()
        _log_market_timing(
            "snapshot",
            started_at,
            cache_hit=0,
            count=len(snapshot),
        )
        return snapshot
    except Exception as exc:
        logger.error("Market snapshot computation failed: %s", exc)
        raise HTTPException(status_code=503, detail="Market data temporarily unavailable")


@app.get("/api/v2/market/fx/usd-cny")
async def get_usd_cny_fx_rate():
    """Return latest USD/CNY (USD/RMB) rate from DB market_prices table."""
    _ensure_market_runtime_fresh()
    try:
        with db.engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT date, product_id, price_type, price
                    FROM market_prices
                    WHERE price IS NOT NULL
                      AND (
                        LOWER(product_id) IN ('usd_rmb', 'usd_cny', 'fx_usd_cny', 'usdcny', 'usdrmb')
                        OR LOWER(price_type) IN ('usd_rmb', 'usd_cny', 'fx_usd_cny', 'usdcny', 'usdrmb')
                        OR (LOWER(product_id) LIKE '%usd%' AND LOWER(product_id) LIKE '%cny%')
                        OR (LOWER(product_id) LIKE '%usd%' AND LOWER(product_id) LIKE '%rmb%')
                        OR (LOWER(price_type) LIKE '%usd%' AND LOWER(price_type) LIKE '%cny%')
                        OR (LOWER(price_type) LIKE '%usd%' AND LOWER(price_type) LIKE '%rmb%')
                      )
                    ORDER BY date DESC
                    LIMIT 1
                    """
                )
            ).fetchone()

        return build_fx_rate_payload(row)
    except Exception as exc:
        logger.error("USD/CNY FX rate query failed: %s", exc)
        raise HTTPException(status_code=503, detail="FX data temporarily unavailable")


@app.get("/api/debug/market/crude-oil")
async def debug_crude_oil_market(request: Request):
    """Debug endpoint for Brent runtime visibility in the current backend process."""
    _require_current_user(request)
    _ensure_market_runtime_fresh()
    history = market_data.get_price_history("crude_oil")
    regional = market_data.get_regional_prices("crude_oil")
    snapshot_item = next(
        (item for item in _compute_market_snapshot() if item.get("id") == "crude_oil"),
        None,
    )
    return {
        "history_count": len(history),
        "latest_history": history[-1] if history else None,
        "regional_prices": regional,
        "is_real_data": market_data.is_real_data("crude_oil"),
        "in_snapshot": snapshot_item is not None,
        "snapshot_item": snapshot_item,
    }


def _clear_market_runtime_caches() -> None:
    _market_snapshot_cache["data"] = None
    _market_snapshot_cache["ts"] = 0.0
    _market_forecast_cache.clear()
    _regional_prices_cache.clear()
    _price_chain_cache["data"] = None
    _price_chain_cache["ts"] = 0.0
    _inventory_safety_cache["data"] = None
    _inventory_safety_cache["ts"] = 0.0
    _pricing_actions_cache["data"] = None
    _pricing_actions_cache["ts"] = 0.0


@app.post("/api/v2/market/refresh")
async def refresh_market_runtime_data():
    """Reload market series from MySQL and clear runtime caches.

    Useful when DB rows were updated after the process started.
    """
    loop = asyncio.get_event_loop()

    def _reload_sync() -> dict:
        loaded_rows = 0
        try:
            from backend.src.db.load_market_prices import load_all

            loaded_rows = load_all(reset=True)
        except Exception as exc:
            logger.warning("market_prices reload during /api/v2/market/refresh failed: %s", exc)

        market_data.refresh()
        _clear_db_dependent_runtime_caches()
        loaded_products = len(market_data.history)
        loaded_points = sum(len(v) for v in market_data.history.values())
        # Also evaluate pending forecasts against the freshly loaded actuals
        evaluated_count = 0
        try:
            from backend.src.db.daily_calibration import evaluate_pending_forecasts
            from backend.src.db.sqlite_db import db as _eval_db
            evaluated = evaluate_pending_forecasts(_eval_db.engine)
            evaluated_count = len(evaluated)
        except Exception as _eval_exc:
            logger.debug("Forecast evaluation during refresh skipped: %s", _eval_exc)
        return {
            "loaded_rows": loaded_rows,
            "loaded_products": loaded_products,
            "loaded_points": loaded_points,
            "forecasts_evaluated": evaluated_count,
        }

    result = await loop.run_in_executor(None, _reload_sync)
    _set_market_runtime_fingerprint(_read_market_live_fingerprint())
    await _market_live_hub.publish("market_refreshed", result)
    return {
        "status": "ok",
        "message": "Market prices re-imported from MySQL and runtime cache refreshed",
        **result,
    }


@app.get("/api/stream/market-live")
async def stream_market_live_events():
    """Server-sent event stream for market + decision-log live updates."""

    async def _event_gen() -> AsyncIterator[str]:
        queue = await _market_live_hub.subscribe()
        try:
            connected_payload = {
                "event": "connected",
                "payload": {"message": "market-live stream ready"},
                "ts": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
            }
            yield (
                f"event: connected\n"
                f"data: {json.dumps(connected_payload, ensure_ascii=False)}\n\n"
            )

            while True:
                envelope = await queue.get()

                event_name = str(envelope.get("event") or "message")
                yield (
                    f"event: {event_name}\n"
                    f"data: {json.dumps(envelope, ensure_ascii=False)}\n\n"
                )
        finally:
            await _market_live_hub.unsubscribe(queue)

    return StreamingResponse(
        _event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/admin/oilchem/sync")
async def sync_oilchem_data(request: Request):
    _require_admin(request)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, oilchem_sync_service.sync_current_snapshot
    )
    return result


@app.get("/api/admin/oilchem/latest")
async def list_oilchem_data(request: Request, limit: int = 31):
    _require_admin(request)
    rows = oilchem_sync_service.list_recent_rows(limit=limit)
    return {
        "status": "ok",
        "rows": rows,
    }


_market_snapshot_cache: dict = {"data": None, "ts": 0.0}
_MARKET_SNAPSHOT_TTL = 300  # seconds (5 min — data refreshed by /api/v2/market/refresh)

# AI analysis cache: keyed by (start_date, end_date, material) → {data, ts}
_ai_analysis_cache: dict = {}
_AI_ANALYSIS_CACHE_TTL = 3600 * 6  # 6 hours


def _snapshot_point_date(point: dict) -> date | None:
    raw_value = point.get("date") if isinstance(point, dict) else None
    if raw_value is None:
        return None
    try:
        return datetime.fromisoformat(str(raw_value)[:10]).date()
    except Exception:
        return None


def _select_daily_reference_point(
    history: list[dict],
    latest_date: date,
    tolerance_days: int = 2,
) -> tuple[float | None, str | None]:
    target_date = latest_date - timedelta(days=1)
    best_point = None
    best_date = None
    best_delta = None

    for point in history[:-1]:
        point_date = _snapshot_point_date(point)
        if point_date is None or point_date > target_date:
            continue
        delta_days = (target_date - point_date).days
        if delta_days > tolerance_days:
            continue
        if best_delta is None or delta_days < best_delta:
            best_point = point
            best_date = point_date
            best_delta = delta_days

    if not best_point:
        return None, None
    try:
        return float(best_point.get("price")), best_date.isoformat() if best_date else None
    except (TypeError, ValueError):
        return None, None


def _select_weekly_reference_point(
    history: list[dict],
    latest_date: date,
    tolerance_days: int = 3,
) -> tuple[float | None, str | None]:
    target_date = latest_date - timedelta(days=7)
    best_point = None
    best_date = None
    best_abs_delta = None

    for point in history[:-1]:
        point_date = _snapshot_point_date(point)
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


def _iter_market_snapshot_products() -> list[dict]:
    try:
        platform_rows = db.list_platform_products("org-default")
    except Exception:
        platform_rows = []

    platform_by_id = {
        str(row.get("id") or "").strip(): row
        for row in platform_rows
        if str(row.get("id") or "").strip()
    }

    products: list[dict] = []
    seen: set[str] = set()
    for node in domain_graph.nodes:
        row = platform_by_id.get(node.id)
        products.append(
            {
                "id": node.id,
                "name_cn": (str(row.get("name_cn") or "").strip() if row else "") or node.name_cn,
                "unit": (str(row.get("unit") or "").strip() if row else "") or node.unit,
            }
        )
        seen.add(node.id)

    for row in platform_rows:
        product_id = str(row.get("id") or "").strip()
        if not product_id or product_id in seen:
            continue
        display_in = [str(item).strip() for item in (row.get("display_in") or [])]
        if "dashboard" not in display_in:
            continue
        if not str(row.get("data_table") or "").strip() or not str(row.get("data_key") or "").strip():
            continue
        products.append(
            {
                "id": product_id,
                "name_cn": str(row.get("name_cn") or row.get("name_en") or product_id).strip(),
                "unit": str(row.get("unit") or "CNY/ton").strip(),
            }
        )
        seen.add(product_id)

    return products


def _compute_market_snapshot() -> list[dict]:
    from backend.src.service.erp_kpi_service import ErpKPIService

    # Single bulk fetch: 4 DB queries total instead of N×5
    erp_kpi = ErpKPIService()
    sales_7d_map, sales_7d_growth_map = erp_kpi.get_sales_window_bulk(period_days=7)

    # Pre-fetch all execution prices in one query
    snapshot_product_ids = [
        str(p.get("id") or "").strip()
        for p in _iter_market_snapshot_products()
        if not _is_excluded_product_id(str(p.get("id") or "").strip())
    ]
    execution_prices = _get_execution_prices(snapshot_product_ids)

    snapshot: list[dict] = []
    for product in _iter_market_snapshot_products():
        product_id = str(product.get("id") or "").strip()
        if not product_id or _is_excluded_product_id(product_id):
            continue
        history = market_data.get_price_history(product_id)
        if not history:
            continue

        latest = history[-1]
        price = float(latest["price"])
        latest_date = _snapshot_point_date(latest)

        if latest_date is not None:
            daily_ref_price, daily_ref_date = _select_daily_reference_point(history, latest_date)
        else:
            daily_ref_price = float(history[-2]["price"]) if len(history) > 1 else None
            daily_ref_date = str(history[-2].get("date"))[:10] if len(history) > 1 else None

        daily_gap_value = round(price - daily_ref_price, 2) if daily_ref_price else None
        change = (
            round((daily_gap_value / daily_ref_price) * 100, 2)
            if daily_ref_price and daily_gap_value is not None
            else 0
        )

        # 7-day range should be calendar-date based, not last 7 data points.
        if latest_date is not None:
            range_start = latest_date - timedelta(days=6)
            range_end = latest_date
            window = [
                point
                for point in history
                if (point_date := _snapshot_point_date(point)) is not None
                and range_start <= point_date <= range_end
            ]
            high_low_start_date = range_start.isoformat()
            high_low_end_date = range_end.isoformat()
        else:
            window = history[-7:] if len(history) >= 7 else history
            high_low_start_date = str(window[0].get("date"))[:10] if window else None
            high_low_end_date = str(window[-1].get("date"))[:10] if window else None

        if window:
            high = round(max(float(point["price"]) for point in window), 2)
            low = round(min(float(point["price"]) for point in window), 2)
        else:
            high = round(price, 2)
            low = round(price, 2)

        weekly_ref_point = window[0] if window else None
        weekly_ref_price = (
            float(weekly_ref_point["price"]) if weekly_ref_point and weekly_ref_point.get("price") is not None else None
        )
        weekly_ref_date = (
            str(weekly_ref_point.get("date"))[:10] if weekly_ref_point else None
        )

        weekly_gap_value = round(price - weekly_ref_price, 2) if weekly_ref_price else None
        weekly_change_pct = (
            round((weekly_gap_value / weekly_ref_price) * 100, 2)
            if weekly_ref_price and weekly_gap_value is not None
            else None
        )

        sales_7d_ton = sales_7d_map.get(product_id)
        sales_7d_growth_pct = sales_7d_growth_map.get(product_id)
        source_detail = market_data.get_data_source_detail(product_id)
        if source_detail == "mysql":
            data_source = "real_mysql"
        elif source_detail and source_detail != "unknown":
            data_source = source_detail
        else:
            data_source = "unknown"

        snapshot.append(
            {
                "id": product_id,
                "name": product.get("name_cn") or product_id,
                "price": price,
                "date": latest_date.isoformat() if latest_date else str(latest.get("date") or "")[:10] or None,
                "change_pct": change,
                "weekly_change_pct": weekly_change_pct,
                "daily_gap_value": daily_gap_value,
                "weekly_gap_value": weekly_gap_value,
                "daily_reference_date": daily_ref_date,
                "weekly_reference_date": weekly_ref_date,
                "high": high,
                "low": low,
                "high_low_start_date": high_low_start_date,
                "high_low_end_date": high_low_end_date,
                "volume": None,
                "unit": product.get("unit") or "CNY/ton",
                "data_source": data_source,
                "data_source_detail": source_detail,
                "sales_7d_ton": sales_7d_ton,
                "sales_7d_growth_pct": sales_7d_growth_pct,
                "execution_price": execution_prices.get(product_id, {}).get("price"),
                "execution_price_date": execution_prices.get(product_id, {}).get("date"),
                "execution_price_change": execution_prices.get(product_id, {}).get("change"),
                "execution_price_change_pct": execution_prices.get(product_id, {}).get("change_pct"),
                "execution_price_prev_date": execution_prices.get(product_id, {}).get("prev_date"),
            }
        )
    return snapshot


@app.get("/api/v2/market/comparison")
async def get_market_comparison():
    """Returns comparison data for all products: snapshot + 12-week sparkline + full history."""
    _ensure_market_runtime_fresh()
    products = []
    for node in domain_graph.nodes:
        if _is_excluded_product_id(node.id):
            continue
        history = market_data.get_price_history(node.id)
        if not history:
            continue

        latest = history[-1]
        price = latest["price"]
        prev_price = history[-2]["price"] if len(history) > 1 else price
        change_pct = (
            round((price - prev_price) / prev_price * 100, 2) if prev_price else 0
        )

        # 12-week sparkline (~84 trading days, sample weekly)
        spark_window = history[-84:] if len(history) >= 84 else history
        sparkline = [
            round(p["price"], 2)
            for p in spark_window[:: max(1, len(spark_window) // 12)]
        ]

        # High/low over all available history
        all_prices = [p["price"] for p in history]
        high = round(max(all_prices), 2)
        low = round(min(all_prices), 2)

        # Volume placeholder
        volume = None

        products.append(
            {
                "id": node.id,
                "name": node.name_cn,
                "name_en": node.name_en,
                "price": round(price, 2),
                "change_pct": change_pct,
                "high": high,
                "low": low,
                "volume": volume,
                "unit": node.unit,
                "sparkline": sparkline,
                "history": [
                    {"date": p["date"], "price": round(p["price"], 2)} for p in history
                ],
            }
        )
    return products


@app.get("/api/v2/market/spreads")
async def get_market_spreads():
    """Returns calculated spreads/margins."""
    _ensure_market_runtime_fresh()
    return market_data.calculate_spreads()


# ---------------------------------------------------------------------------
# Part 1 – Regional Price API
# ---------------------------------------------------------------------------

_REGIONAL_PRICE_LABEL_MAP: dict[str, str] = {
    "east_china": "华东",
    "south_china": "华南",
    "wuhan": "华中(武汉)",
    "average": "全国均价",
    "yangzi": "扬子",
    "yangzi_basf": "扬子巴斯夫",
    "sinokor": "中韩石化",
    "maoming": "茂名",
    "guangzhou": "广州",
    "henhe_hc9": "恒河HC9",
    "brent": "布伦特",
    "japan_cif": "日本CIF",
    "sinopec_sh": "上海石化",
    "baling_1105": "巴陵1105",
    "partner_forecast_price": "合作商预报价",
    "juhui": "聚汇",
}


def _compute_regional_prices(product_id: str, fast_mode: bool = False) -> dict:
    from backend.src.service.market_data_service import REAL_DATA_MAP
    from backend.src.service.market_data_service import LZ_EXTERNAL_NODE_IDS
    from backend.src.service.cost_floor_service import cost_floor_service
    from backend.src.db.sqlite_db import db as _app_db
    from sqlalchemy import text as _text

    # Resolve canonical product name in market_prices
    mp_product = REAL_DATA_MAP.get(product_id, (product_id, None))[0]

    engine = _app_db.engine
    regions: list[dict] = []

    if product_id in LZ_EXTERNAL_NODE_IDS:
        lz_rows = market_data.get_regional_price_rows(product_id)
        if lz_rows:
            rows = [
                (
                    row.get("price_type"),
                    row.get("price"),
                    row.get("date"),
                    row.get("prev_price"),
                )
                for row in lz_rows
            ]
        else:
            rows = []
    else:
        rows = []

    if not rows:
        with engine.connect() as conn:
            if fast_mode:
                # Fast path for batch loading: latest regional rows plus prior price for daily moves.
                rows = conn.execute(
                    _text(
                        "SELECT mp.price_type, mp.price, mp.date, "
                        "  (SELECT mp_prev.price FROM market_prices mp_prev "
                        "   WHERE mp_prev.product_id = mp.product_id "
                        "     AND mp_prev.price_type = mp.price_type "
                        "     AND mp_prev.date < mp.date "
                        "     AND mp_prev.price IS NOT NULL "
                        "   ORDER BY mp_prev.date DESC LIMIT 1) AS prev_price "
                        "FROM market_prices mp "
                        "INNER JOIN ("
                        "  SELECT price_type, MAX(date) AS max_date "
                        "  FROM market_prices "
                        "  WHERE product_id = :pid AND price IS NOT NULL "
                        "  GROUP BY price_type"
                        ") latest "
                        "  ON latest.price_type = mp.price_type AND latest.max_date = mp.date "
                        "WHERE mp.product_id = :pid "
                        "  AND mp.price IS NOT NULL "
                        "ORDER BY mp.price_type"
                    ),
                    {"pid": mp_product},
                ).fetchall()
            else:
                # Full path: includes previous price for daily % change.
                rows = conn.execute(
                    _text(
                        "SELECT mp1.price_type, mp1.price, mp1.date, "
                        "  (SELECT mp_prev.price FROM market_prices mp_prev "
                        "   WHERE mp_prev.product_id = mp1.product_id "
                        "     AND mp_prev.price_type = mp1.price_type "
                        "     AND mp_prev.date < mp1.date "
                        "     AND mp_prev.price IS NOT NULL "
                        "   ORDER BY mp_prev.date DESC LIMIT 1) AS prev_price "
                        "FROM market_prices mp1 "
                        "WHERE mp1.product_id = :pid "
                        "  AND mp1.date = (SELECT MAX(mp2.date) FROM market_prices mp2 "
                        "                  WHERE mp2.product_id = mp1.product_id "
                        "                    AND mp2.price_type = mp1.price_type) "
                        "  AND mp1.price IS NOT NULL "
                        "ORDER BY mp1.price_type"
                    ),
                    {"pid": mp_product},
                ).fetchall()

    # SIS should display stable market quote regions, not sparse internal sale averages.
    if product_id in {"sis", "sis_rubber"}:
        baling_rows = [row for row in rows if str(row[0]) == "baling_1105"]
        if baling_rows:
            rows = baling_rows

    from datetime import date as _date, datetime as _datetime

    raw_regions = []
    for row in rows:
        price_type, price, date_val, prev_price = row

        # Hide internal ERP realization rows from regional price UI payloads.
        if price_type == "erp_sale_order_weighted":
            continue

        price = float(price)
        change_pct = None
        if prev_price:
            prev_price = float(prev_price)
            change_pct = round((price - prev_price) / prev_price * 100, 2) if prev_price else None

        label = _REGIONAL_PRICE_LABEL_MAP.get(price_type, price_type)
        is_internal = price_type == "erp_sale_order_weighted"

        # Normalise date_val to a date object for staleness calculation
        if isinstance(date_val, str):
            d = _datetime.strptime(date_val[:10], "%Y-%m-%d").date()
        elif hasattr(date_val, "date"):
            d = date_val.date()
        else:
            d = date_val  # already a date

        raw_regions.append({
            "price_type": price_type,
            "label": label,
            "is_internal": is_internal,
            "price": round(price, 0),
            "change_pct": change_pct,
            "date": str(d),
            "_date_obj": d,
        })

    if not raw_regions:
        regions = raw_regions  # triggers the 404 below
    else:
        # Find the most recent date across all regions so we can flag stale rows
        latest_date = max(r["_date_obj"] for r in raw_regions)
        for r in raw_regions:
            r["days_stale"] = (latest_date - r["_date_obj"]).days
            del r["_date_obj"]
        regions = raw_regions

    if not regions:
        raise HTTPException(status_code=404, detail=f"No regional price data for {product_id}")

    prices_only = [r["price"] for r in regions]
    spread_cny = round(max(prices_only) - min(prices_only), 0) if len(prices_only) > 1 else 0

    # Spread severity
    if spread_cny >= 500:
        spread_level = "high"
    elif spread_cny >= 300:
        spread_level = "medium"
    else:
        spread_level = "low"

    # Price realization rate: internal ERP / best LZ market reference
    erp_region = next((r for r in regions if r["is_internal"]), None)
    market_refs = [r["price"] for r in regions if not r["is_internal"]]
    price_realization_pct = None
    if erp_region and market_refs:
        best_ref = max(market_refs)
        price_realization_pct = round(erp_region["price"] / best_ref * 100, 1)

    # Cost floor is omitted in batch fast mode to reduce first-load latency.
    floor_data = None if fast_mode else cost_floor_service.get_floor(product_id)

    return {
        "product_id": product_id,
        "mp_product": mp_product,
        "regions": regions,
        "spread_cny": spread_cny,
        "spread_level": spread_level,
        "price_realization_pct": price_realization_pct,
        "cost_floor": floor_data,
        "updated_at": regions[0]["date"] if regions else None,
    }


_regional_prices_cache: dict[str, dict[str, Any]] = {}
_REGIONAL_PRICES_TTL = 120  # seconds
_regional_prices_cache_lock = _threading.Lock()


def _get_cached_regional_prices(
    product_id: str,
    market_fingerprint: dict[str, Any],
) -> dict | None:
    now = time.time()
    with _regional_prices_cache_lock:
        cached = _regional_prices_cache.get(product_id)
        if cached is None:
            return None
        if (now - float(cached.get("ts") or 0.0)) >= _REGIONAL_PRICES_TTL:
            _regional_prices_cache.pop(product_id, None)
            return None
        if cached.get("market_fingerprint") != market_fingerprint:
            _regional_prices_cache.pop(product_id, None)
            return None
        return copy.deepcopy(cached.get("data"))


def _store_regional_prices_cache(
    product_id: str,
    payload: dict,
    market_fingerprint: dict[str, Any],
) -> None:
    with _regional_prices_cache_lock:
        _regional_prices_cache[product_id] = {
            "data": copy.deepcopy(payload),
            "ts": time.time(),
            "market_fingerprint": copy.deepcopy(market_fingerprint),
        }


def _compute_regional_prices_batch(
    product_ids: list[str],
    market_fingerprint: dict[str, Any],
) -> tuple[dict[str, dict], dict[str, str]]:
    items: dict[str, dict] = {}
    errors: dict[str, str] = {}
    for product_id in product_ids:
        try:
            cached = _get_cached_regional_prices(product_id, market_fingerprint)
            if cached is not None:
                items[product_id] = cached
                continue

            payload = _compute_regional_prices(product_id, fast_mode=True)
            _store_regional_prices_cache(product_id, payload, market_fingerprint)
            items[product_id] = payload
        except HTTPException as exc:
            errors[product_id] = str(exc.detail)
        except Exception as exc:
            errors[product_id] = str(exc)
    return items, errors


@app.get("/api/v2/market/regional/{product_id}")
async def get_regional_prices(product_id: str):
    """Returns all regional price types for a product with spread and cost floor analysis."""
    started_at = time.perf_counter()
    _ensure_product_allowed(product_id)
    _ensure_market_runtime_fresh()
    market_fingerprint = _current_market_fingerprint()

    cached = _get_cached_regional_prices(product_id, market_fingerprint)
    if cached is not None:
        _log_market_timing(
            "regional",
            started_at,
            product_id=product_id,
            cache_hit=1,
            regions=len(cached.get("regions") or []),
        )
        return cached

    loop = asyncio.get_event_loop()
    payload = await loop.run_in_executor(None, _compute_regional_prices, product_id)
    _store_regional_prices_cache(product_id, payload, market_fingerprint)
    _log_market_timing(
        "regional",
        started_at,
        product_id=product_id,
        cache_hit=0,
        regions=len(payload.get("regions") or []),
    )
    return payload


@app.get("/api/v2/market/regional-batch")
async def get_regional_prices_batch(product_ids: str):
    """Returns regional price payloads for multiple products in one request."""
    started_at = time.perf_counter()
    requested = [p.strip() for p in str(product_ids or "").split(",") if p.strip()]
    if not requested:
        raise HTTPException(status_code=400, detail="product_ids is required")

    deduped_ids: list[str] = []
    seen: set[str] = set()
    for product_id in requested:
        _ensure_product_allowed(product_id)
        if product_id in seen:
            continue
        seen.add(product_id)
        deduped_ids.append(product_id)

    _ensure_market_runtime_fresh()
    market_fingerprint = _current_market_fingerprint()

    loop = asyncio.get_event_loop()
    items, errors = await loop.run_in_executor(
        None,
        _compute_regional_prices_batch,
        deduped_ids,
        market_fingerprint,
    )
    _log_market_timing(
        "regional_batch",
        started_at,
        requested=len(deduped_ids),
        returned=len(items),
        errors=len(errors),
    )
    return {"items": items, "errors": errors}


@app.get("/api/v2/market/cost-floor")
async def get_cost_floor_all():
    """Returns cost floor analysis for all C5/C9 products based on current naphtha price."""
    from backend.src.service.cost_floor_service import cost_floor_service
    _ensure_market_runtime_fresh()
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, cost_floor_service.get_all_floors)


# ---------------------------------------------------------------------------
# Part 6 – Cross-product correlation
# ---------------------------------------------------------------------------

@app.get("/api/v2/market/correlation")
async def get_market_correlation(days: int = 90):
    """Returns pairwise price correlation matrix for all products over last N days."""
    _ensure_market_runtime_fresh()
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _compute_correlation, days)


@app.get("/api/v2/market/seasonal/{product_id}")
async def get_seasonal_analysis(product_id: str):
    """Returns seasonal decomposition (trend, seasonal, residual) for a product."""
    from backend.src.analytics.seasonal import get_seasonal_analysis
    _ensure_product_allowed(product_id)
    _ensure_market_runtime_fresh()
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, get_seasonal_analysis, product_id)


def _compute_correlation(days: int = 90) -> dict:
    from backend.src.db.sqlite_db import db as _app_db
    from sqlalchemy import text as _text
    from datetime import datetime, timedelta

    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")

    # Pull last N days of primary price series for each product in REAL_DATA_MAP
    from backend.src.service.market_data_service import REAL_DATA_MAP
    series: dict[str, dict[str, float]] = {}

    with _app_db.engine.connect() as conn:
        for node_id, (mp_product, mp_price_type) in REAL_DATA_MAP.items():
            if _is_excluded_product_id(node_id):
                continue
            rows = conn.execute(
                _text(
                    "SELECT date, price FROM market_prices "
                    "WHERE product_id=:pid AND price_type=:pt "
                    "  AND date >= :cutoff AND price IS NOT NULL "
                    "ORDER BY date"
                ),
                {"pid": mp_product, "pt": mp_price_type, "cutoff": cutoff},
            ).fetchall()
            if rows:
                series[node_id] = {str(r[0]): float(r[1]) for r in rows}

    if len(series) < 2:
        return {"products": [], "matrix": [], "days": days}

    products = sorted(series.keys())

    # Build aligned date index
    all_dates = sorted({d for s in series.values() for d in s})

    # Fill forward for missing dates
    def fill_forward(s: dict[str, float]) -> list[float]:
        out: list[float] = []
        last: float | None = None
        for d in all_dates:
            v = s.get(d)
            if v is not None:
                last = v
            if last is not None:
                out.append(last)
        return out

    aligned: dict[str, list[float]] = {p: fill_forward(series[p]) for p in products}

    def pearson(a: list[float], b: list[float]) -> float | None:
        n = min(len(a), len(b))
        if n < 10:
            return None
        a, b = a[:n], b[:n]
        mean_a = sum(a) / n
        mean_b = sum(b) / n
        cov = sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(n))
        var_a = sum((x - mean_a) ** 2 for x in a) ** 0.5
        var_b = sum((x - mean_b) ** 2 for x in b) ** 0.5
        if var_a == 0 or var_b == 0:
            return None
        return round(cov / (var_a * var_b), 3)

    matrix: list[list] = []
    for p1 in products:
        row: list = []
        for p2 in products:
            row.append(pearson(aligned[p1], aligned[p2]))
        matrix.append(row)

    return {
        "products": products,
        "matrix": matrix,
        "days": days,
        "date_count": len(all_dates),
    }


@app.get("/api/v2/market/forecast/{product_id}")
@app.get("/api/market/forecast/{product_id}")
async def get_market_forecast(product_id: str):
    """Returns 30-day 3-scenario forecast as chart-ready daily points.

    Each point:  { date, price (history), base, bull, bear (forecast) }
    History points include price only; forecast points include base/bull/bear only.
    The frontend can render all on one chart with a clear handoff boundary.
    """
    started_at = time.perf_counter()
    _ensure_product_allowed(product_id)
    # Force a DB/source fingerprint check before consulting cached forecasts so
    # updated market tables invalidate old responses immediately.
    _ensure_market_runtime_fresh()
    market_fingerprint = _current_market_fingerprint()
    intelligence_context = _build_market_forecast_intelligence_context(product_id)
    intelligence_fingerprint = _build_market_forecast_intelligence_fingerprint(
        product_id,
        intelligence_context,
    )
    cache_fingerprint = _compose_market_forecast_fingerprint(
        market_fingerprint,
        intelligence_fingerprint,
    )
    cached = _get_cached_market_forecast(product_id, cache_fingerprint)
    if cached is not None:
        if isinstance(cached, dict):
            cached["unit"] = _canonical_market_price_unit(product_id, cached.get("unit"))
        forecast_points = cached.get("forecast") if isinstance(cached, dict) else []
        _log_market_timing(
            "forecast",
            started_at,
            product_id=product_id,
            cache_hit=1,
            points=len(forecast_points or []),
        )
        return cached

    loop = asyncio.get_event_loop()
    payload = await loop.run_in_executor(
        None,
        _compute_market_forecast,
        product_id,
        intelligence_context,
        intelligence_fingerprint,
        market_fingerprint,
    )
    _store_market_forecast_cache(product_id, payload, cache_fingerprint)
    forecast_points = payload.get("forecast") if isinstance(payload, dict) else []
    _log_market_timing(
        "forecast",
        started_at,
        product_id=product_id,
        cache_hit=0,
        points=len(forecast_points or []),
    )
    return payload


def _get_market_forecast_payload_sync(
    product_id: str,
    market_fingerprint: dict[str, Any],
    allow_compute: bool = True,
) -> dict | list:
    intelligence_context = _build_market_forecast_intelligence_context(product_id)
    intelligence_fingerprint = _build_market_forecast_intelligence_fingerprint(
        product_id,
        intelligence_context,
    )
    cache_fingerprint = _compose_market_forecast_fingerprint(
        market_fingerprint,
        intelligence_fingerprint,
    )
    cached = _get_cached_market_forecast(product_id, cache_fingerprint)
    if cached is not None:
        return cached
    if not allow_compute:
        return {}
    payload = _compute_market_forecast(
        product_id,
        intelligence_context,
        intelligence_fingerprint,
        market_fingerprint,
    )
    _store_market_forecast_cache(product_id, payload, cache_fingerprint)
    return payload


def _compute_forecast_base_batch(
    product_ids: list[str],
    day_index: int,
    market_fingerprint: dict[str, Any],
) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    for product_id in product_ids:
        try:
            # Batch endpoint is for fast dashboard hints; avoid cold ML compute.
            payload = _get_market_forecast_payload_sync(
                product_id,
                market_fingerprint,
                allow_compute=False,
            )
            if not isinstance(payload, dict):
                result[product_id] = None
                continue
            points = payload.get("forecast")
            if not isinstance(points, list) or not points:
                result[product_id] = None
                continue
            picked = points[day_index] if day_index < len(points) else points[-1]
            base = picked.get("base") if isinstance(picked, dict) else None
            result[product_id] = float(base) if isinstance(base, (int, float)) else None
        except Exception:
            result[product_id] = None
    return result


def _canonical_market_price_unit(product_id: str, fallback: Any = None) -> str:
    canonical_units = {
        "crude_oil": "USD/bbl",
        "naphtha": "USD/ton",
    }
    unit = canonical_units.get(str(product_id or ""))
    if unit:
        return unit
    fallback_unit = str(fallback or "").strip()
    return fallback_unit or "CNY/ton"


@app.get("/api/v2/market/forecast-base-batch")
@app.get("/api/market/forecast-base-batch")
async def get_market_forecast_base_batch(product_ids: str, day: int = 7):
    """Returns forecast base prices for multiple products for a given day index (1-30)."""
    started_at = time.perf_counter()
    requested = [p.strip() for p in str(product_ids or "").split(",") if p.strip()]
    if not requested:
        raise HTTPException(status_code=400, detail="product_ids is required")

    deduped_ids: list[str] = []
    seen: set[str] = set()
    for product_id in requested:
        _ensure_product_allowed(product_id)
        if product_id in seen:
            continue
        seen.add(product_id)
        deduped_ids.append(product_id)

    day_index = max(0, min(29, int(day) - 1))
    _ensure_market_runtime_fresh()
    market_fingerprint = _current_market_fingerprint()

    loop = asyncio.get_event_loop()
    base_by_product = await loop.run_in_executor(
        None,
        _compute_forecast_base_batch,
        deduped_ids,
        day_index,
        market_fingerprint,
    )
    non_null = sum(1 for value in base_by_product.values() if value is not None)
    _log_market_timing(
        "forecast_base_batch",
        started_at,
        requested=len(deduped_ids),
        day=day_index + 1,
        non_null=non_null,
    )
    return {
        "day": day_index + 1,
        "base_by_product": base_by_product,
    }


_market_forecast_cache: dict[str, dict] = {}
_MARKET_FORECAST_TTL = 90  # seconds


def _current_market_fingerprint() -> dict[str, Any]:
    if _market_runtime_fingerprint is None:
        return {}
    return copy.deepcopy(_market_runtime_fingerprint.get("market") or {})


def _market_forecast_intelligence_lookback_hours() -> int:
    raw_value = (
        os.getenv("MARKET_FORECAST_INTELLIGENCE_LOOKBACK_HOURS", "168") or "168"
    ).strip()
    try:
        return max(1, min(24 * 30, int(raw_value)))
    except ValueError:
        return 168


def _market_forecast_intelligence_event_limit() -> int:
    raw_value = (
        os.getenv("MARKET_FORECAST_INTELLIGENCE_EVENT_LIMIT", "40") or "40"
    ).strip()
    try:
        return max(1, min(200, int(raw_value)))
    except ValueError:
        return 40


def _empty_market_forecast_intelligence_context(hours: int) -> dict[str, Any]:
    return {
        "has_intelligence": False,
        "checked": True,
        "checked_hours": hours,
        "events": [],
        "causal_analyses": [],
        "scenarios": [],
        "override_decisions": [],
        "intelligence_summary": "Automatic forecast intelligence unavailable.",
    }


def _market_forecast_product_aliases(product_id: str) -> set[str]:
    try:
        from backend.src.intelligence.product_intelligence import _PRODUCT_ALIASES

        aliases = _PRODUCT_ALIASES.get(product_id, {product_id})
        normalized = {str(alias).strip() for alias in aliases if str(alias).strip()}
        return normalized or {product_id}
    except Exception:
        return {product_id}


def _build_market_forecast_intelligence_context(product_id: str) -> dict[str, Any]:
    hours = _market_forecast_intelligence_lookback_hours()
    base_context = _empty_market_forecast_intelligence_context(hours)

    try:
        from backend.src.intelligence.intelligence_orchestrator import (
            get_intelligence_context as _get_intelligence_context,
        )

        base_context = _get_intelligence_context(
            hours=hours,
            review_status="auto",
            min_usefulness_score=0,
            event_limit=_market_forecast_intelligence_event_limit(),
            analysis_limit=None,
            fast_mode=True,
        )
    except Exception as exc:
        logger.debug("Market forecast intelligence lookup skipped: %s", exc)
        return base_context

    if not isinstance(base_context, dict) or not base_context.get("has_intelligence"):
        return _empty_market_forecast_intelligence_context(hours)

    aliases = _market_forecast_product_aliases(product_id)
    matched_analyses: list[dict[str, Any]] = []
    matched_event_ids: set[str] = set()
    matched_headlines: set[str] = set()

    for analysis in base_context.get("causal_analyses") or []:
        if not isinstance(analysis, dict):
            continue
        impacts = analysis.get("product_impacts") or {}
        if not isinstance(impacts, dict) or not any(alias in impacts for alias in aliases):
            continue
        matched_analyses.append(copy.deepcopy(analysis))
        event_id = str(analysis.get("event_id") or "").strip()
        headline = str(analysis.get("event_headline") or "").strip()
        if event_id:
            matched_event_ids.add(event_id)
        if headline:
            matched_headlines.add(headline)

    if not matched_analyses:
        return {
            **_empty_market_forecast_intelligence_context(hours),
            "checked": bool(base_context.get("checked", True)),
            "checked_hours": int(base_context.get("checked_hours") or hours),
            "intelligence_summary": (
                f"Automatic forecast intelligence checked the last {hours}h but found no matched event analyses for {product_id}."
            ),
        }

    matched_events: list[dict[str, Any]] = []
    for event in base_context.get("events") or []:
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("event_id") or "").strip()
        headline = str(event.get("headline") or "").strip()
        if (event_id and event_id in matched_event_ids) or (
            headline and headline in matched_headlines
        ):
            matched_events.append(copy.deepcopy(event))

    matched_override_decisions: list[dict[str, Any]] = []
    for decision in base_context.get("override_decisions") or []:
        if not isinstance(decision, dict):
            continue
        event_id = str(decision.get("event_id") or "").strip()
        headline = str(decision.get("event_headline") or "").strip()
        if (event_id and event_id in matched_event_ids) or (
            headline and headline in matched_headlines
        ):
            matched_override_decisions.append(copy.deepcopy(decision))

    return {
        **copy.deepcopy(base_context),
        "has_intelligence": True,
        "events": matched_events,
        "causal_analyses": matched_analyses,
        "override_decisions": matched_override_decisions,
    }


def _build_market_forecast_intelligence_fingerprint(
    product_id: str,
    intelligence_context: dict[str, Any] | None,
) -> dict[str, Any]:
    hours = _market_forecast_intelligence_lookback_hours()
    fingerprint = {
        "mode": "automatic_non_rejected",
        "hours": hours,
        "event_keys": [],
        "event_ids": [],
        "event_count": 0,
        "latest_detected_at": "",
        "active": False,
    }

    if not isinstance(intelligence_context, dict) or not intelligence_context.get(
        "has_intelligence"
    ):
        return fingerprint

    try:
        from backend.src.intelligence.product_intelligence import (
            summarize_product_intelligence,
        )

        signal = summarize_product_intelligence(product_id, intelligence_context)
    except Exception as exc:
        logger.debug("Market forecast intelligence fingerprint fallback used: %s", exc)
        signal = {"active": False, "events": []}

    events = signal.get("events") or []
    event_ids = sorted(
        {
            str(event.get("event_id") or "").strip()
            for event in events
            if isinstance(event, dict) and str(event.get("event_id") or "").strip()
        }
    )
    event_keys = sorted(
        {
            str(event.get("event_id") or event.get("headline") or "").strip()
            for event in events
            if isinstance(event, dict)
            and str(event.get("event_id") or event.get("headline") or "").strip()
        }
    )
    detected_values = [
        str(event.get("detected_at") or "").strip()
        for event in events
        if isinstance(event, dict) and str(event.get("detected_at") or "").strip()
    ]

    fingerprint.update(
        {
            "event_keys": event_keys,
            "event_ids": event_ids,
            "event_count": len(events),
            "latest_detected_at": max(detected_values) if detected_values else "",
            "active": bool(signal.get("active")) and bool(events),
        }
    )
    return fingerprint


def _compose_market_forecast_fingerprint(
    market_fingerprint: dict[str, Any],
    intelligence_fingerprint: dict[str, Any] | None,
) -> dict[str, Any]:
    combined = copy.deepcopy(market_fingerprint)
    combined["forecast_engine_version"] = "champion_routing_v1"
    combined["forecast_intelligence"] = copy.deepcopy(intelligence_fingerprint or {})
    return combined


def _snapshot_matches_intelligence(
    snapshot: dict[str, Any],
    intelligence_fingerprint: dict[str, Any] | None,
) -> bool:
    expected = intelligence_fingerprint or {}
    snapshot_meta = snapshot.get("forecast_snapshot") if isinstance(snapshot, dict) else {}
    snapshot_fingerprint = {}
    if isinstance(snapshot_meta, dict):
        snapshot_fingerprint = snapshot_meta.get("intelligence_fingerprint") or {}
    if not snapshot_fingerprint and isinstance(snapshot, dict):
        snapshot_fingerprint = snapshot.get("intelligence_fingerprint") or {}

    expected_keys = expected.get("event_keys") or []
    if not snapshot_fingerprint:
        return not expected_keys
    return snapshot_fingerprint == expected


def _snapshot_matches_market_fingerprint(
    snapshot: dict[str, Any],
    market_fingerprint: dict[str, Any] | None,
) -> bool:
    if not isinstance(snapshot, dict):
        return False
    expected = market_fingerprint or {}
    snapshot_meta = snapshot.get("forecast_snapshot") if isinstance(snapshot.get("forecast_snapshot"), dict) else {}
    snapshot_fingerprint = snapshot_meta.get("market_fingerprint") or snapshot.get("market_fingerprint") or {}

    if snapshot_fingerprint:
        return snapshot_fingerprint == expected

    history = snapshot.get("history")
    forecast_path = snapshot.get("forecast_path")
    if not isinstance(history, list) or not history or not isinstance(forecast_path, list) or not forecast_path:
        return False
    try:
        last_history_date = str(history[-1].get("date") or "")[:10]
        first_forecast_date = str(forecast_path[0].get("date") or forecast_path[0].get("forecast_date") or "")[:10]
        if not last_history_date or not first_forecast_date:
            return False
        return date.fromisoformat(first_forecast_date) > date.fromisoformat(last_history_date)
    except Exception:
        return False


def _get_cached_market_forecast(
    product_id: str,
    market_fingerprint: dict[str, Any],
) -> dict | list | None:
    now = time.time()
    with _market_forecast_cache_lock:
        cached = _market_forecast_cache.get(product_id)
        if cached is None:
            return None
        if (now - cached.get("ts", 0.0)) >= _MARKET_FORECAST_TTL:
            _market_forecast_cache.pop(product_id, None)
            return None
        if cached.get("market_fingerprint") != market_fingerprint:
            _market_forecast_cache.pop(product_id, None)
            return None
        cached_data = cached.get("data")
        if (
            isinstance(cached_data, dict)
            and market_fingerprint.get("forecast_engine_version") == "champion_routing_v1"
        ):
            cached_generation = cached_data.get("forecast_generation") or {}
            cached_champion = cached_generation.get("champion_routing") or {}
            forecast_path = cached_data.get("forecast_path") or []
            if str(cached_generation.get("baseline_status") or "") == "ok" and not isinstance(cached_champion, dict):
                _market_forecast_cache.pop(product_id, None)
                return None
            if (
                str(cached_generation.get("baseline_status") or "") == "ok"
                and isinstance(cached_champion, dict)
                and cached_champion.get("selected_model") != "naive"
                and not any(
                    isinstance(point, dict)
                    and isinstance(point.get("daily_calibration"), dict)
                    and point["daily_calibration"].get("active")
                    for point in forecast_path
                )
            ):
                _market_forecast_cache.pop(product_id, None)
                return None
        return copy.deepcopy(cached_data)


def _store_market_forecast_cache(
    product_id: str,
    payload: dict | list,
    market_fingerprint: dict[str, Any],
) -> None:
    with _market_forecast_cache_lock:
        _market_forecast_cache[product_id] = {
            "data": copy.deepcopy(payload),
            "ts": time.time(),
            "market_fingerprint": copy.deepcopy(market_fingerprint),
        }


def _compute_market_forecast(
    product_id: str,
    intelligence_context: dict[str, Any] | None = None,
    intelligence_fingerprint: dict[str, Any] | None = None,
    market_fingerprint: dict[str, Any] | None = None,
) -> dict | list:
    node = next((n for n in domain_graph.nodes if n.id == product_id), None)
    if not node:
        raise HTTPException(status_code=404, detail=f"Unknown product: {product_id}")

    response_unit = _canonical_market_price_unit(product_id, getattr(node, "unit", None))

    if intelligence_context is None:
        intelligence_context = _build_market_forecast_intelligence_context(product_id)
    if intelligence_fingerprint is None:
        intelligence_fingerprint = _build_market_forecast_intelligence_fingerprint(
            product_id,
            intelligence_context,
        )

    def _snapshot_has_distinct_scenario_envelope(snapshot: dict[str, Any]) -> bool:
        forecast = snapshot.get("forecast")
        if not isinstance(forecast, list) or not forecast:
            return False
        for point in forecast:
            if not isinstance(point, dict):
                continue
            if point.get("bull") != point.get("base") or point.get("bear") != point.get("base"):
                return True
        return False

    try:
        from backend.src.service.forecasting_engine import _PRODUCT_MAP
        from backend.src.db.forecast_snapshots import get_latest_approved_snapshot
        latest_snapshot = get_latest_approved_snapshot(
            db.engine,
            product_id,
            min_path_points=30,
            exclude_validation_statuses=(
                ["legacy_interpolated_fallback"] if product_id in _PRODUCT_MAP else None
            ),
        )
        if latest_snapshot is not None:
            if product_id in _PRODUCT_MAP and not _snapshot_has_distinct_scenario_envelope(latest_snapshot):
                latest_snapshot = None
            elif not _snapshot_matches_intelligence(latest_snapshot, intelligence_fingerprint):
                latest_snapshot = None
            elif not _snapshot_matches_market_fingerprint(latest_snapshot, market_fingerprint):
                latest_snapshot = None
            else:
                latest_snapshot["unit"] = response_unit
                return latest_snapshot
    except Exception as _snapshot_lookup_exc:
        logger.debug("Stored forecast snapshot lookup skipped: %s", _snapshot_lookup_exc)

    # --- last 60 days of history ---
    full_history = market_data.get_price_history(product_id)
    history_points = [
        {"date": h["date"], "price": round(h["price"], 2)} for h in full_history[-60:]
    ]

    if not history_points:
        return []

    latest_price = history_points[-1]["price"]
    latest_date = datetime.strptime(history_points[-1]["date"], "%Y-%m-%d")

    # --- run ForecastingService for the 30d scenario targets ---
    fc = forecasting_service.forecast(
        product_id,
        intelligence_context=intelligence_context,
    )
    sc30 = fc.get("scenarios", {}).get("30d", {})
    sc7 = fc.get("scenarios", {}).get("7d", {})
    intelligence_signal = fc.get("intelligence_signal") or {}
    intelligence_adj_pct = fc.get("intelligence_adj_pct") or {}
    evidence_events = [
        copy.deepcopy(event)
        for event in (intelligence_signal.get("events") or [])
        if isinstance(event, dict)
    ]
    evidence_event_ids = sorted(
        {
            str(event.get("event_id") or "").strip()
            for event in evidence_events
            if str(event.get("event_id") or "").strip()
        }
    )
    dominant_event = (
        copy.deepcopy(intelligence_signal.get("dominant_event") or {})
        if isinstance(intelligence_signal.get("dominant_event") or {}, dict)
        else {}
    )
    intelligence_active = bool(intelligence_signal.get("active")) and bool(
        evidence_event_ids or evidence_events
    )

    try:
        from backend.src.service.forecast_event_routing import resolve_event_adjustment_policy
    except Exception:
        resolve_event_adjustment_policy = None

    def event_adjustment_policy_for_step(step_number: int) -> dict[str, Any]:
        if resolve_event_adjustment_policy is None:
            return {"enabled": False, "reason": "event routing policy unavailable"}
        try:
            return resolve_event_adjustment_policy(product_id, max(1, int(step_number or 1)))
        except Exception:
            return {"enabled": False, "reason": "event routing policy lookup failed"}

    event_adjustment_policy_7d = event_adjustment_policy_for_step(7)

    def raw_intelligence_override_pct(step_number: int) -> float:
        if not intelligence_active or step_number <= 0:
            return 0.0
        day3_pct = float(intelligence_adj_pct.get("3d", 0.0) or 0.0)
        day7_pct = float(intelligence_adj_pct.get("7d", 0.0) or 0.0)
        day30_pct = float(intelligence_adj_pct.get("30d", 0.0) or 0.0)
        if step_number <= 3:
            return day3_pct * (step_number / 3)
        if step_number <= 7:
            return day3_pct + (day7_pct - day3_pct) * ((step_number - 3) / 4)
        return day7_pct + (day30_pct - day7_pct) * ((step_number - 7) / 23)

    def intelligence_override_pct(step_number: int) -> float:
        if not intelligence_active or step_number <= 0:
            return 0.0
        policy = event_adjustment_policy_for_step(step_number)
        if not policy.get("enabled"):
            return 0.0
        routed_pct = raw_intelligence_override_pct(step_number)
        try:
            max_abs_pct = abs(float(policy.get("max_abs_adjustment_pct") or 3.0))
        except (TypeError, ValueError):
            max_abs_pct = 3.0
        return max(-max_abs_pct, min(max_abs_pct, routed_pct))

    event_count = int(intelligence_signal.get("event_count") or len(evidence_events))
    dominant_headline = str(dominant_event.get("headline") or "").strip()
    if intelligence_active and event_adjustment_policy_7d.get("enabled"):
        cause_summary = (
            "Automatic event analyses (no human approval gate) adjusted the forecast "
            f"{str(intelligence_signal.get('bias') or 'neutral')} "
            f"{float(intelligence_adj_pct.get('7d', 0.0) or 0.0):+.2f}% over 7d"
        )
        if dominant_headline:
            cause_summary += f"; dominant event: {dominant_headline}"
        if event_count > 0:
            cause_summary += f"; matched events: {event_count}"
    elif intelligence_active:
        cause_summary = (
            "Automatic event analyses were captured as evidence, but numeric event adjustment "
            "is disabled by measured routing for this product/horizon."
        )
        if event_count > 0:
            cause_summary += f" matched events: {event_count}"
    else:
        cause_summary = "Local ARIMA baseline; no Kimi/event override applied yet."

    # Derive target midpoints for interpolation
    def mid(sc: dict, key: str) -> float:
        b = sc.get(key, {})
        return (b.get("low", latest_price) + b.get("high", latest_price)) / 2

    target_base = mid(sc30, "base")
    target_bull = mid(sc30, "bull")
    target_bear = mid(sc30, "bear")
    target_7_base = mid(sc7, "base")
    target_7_bull = mid(sc7, "bull")
    target_7_bear = mid(sc7, "bear")

    def scenario_spread_pct(step_number: int) -> tuple[float, float]:
        safe_target_7_base = target_7_base if abs(target_7_base) > 1e-9 else latest_price
        safe_target_base = target_base if abs(target_base) > 1e-9 else latest_price
        day7_bull_pct = (target_7_bull - safe_target_7_base) / safe_target_7_base if abs(safe_target_7_base) > 1e-9 else 0.0
        day7_bear_pct = (target_7_bear - safe_target_7_base) / safe_target_7_base if abs(safe_target_7_base) > 1e-9 else 0.0
        day30_bull_pct = (target_bull - safe_target_base) / safe_target_base if abs(safe_target_base) > 1e-9 else 0.0
        day30_bear_pct = (target_bear - safe_target_base) / safe_target_base if abs(safe_target_base) > 1e-9 else 0.0

        if step_number <= 7:
            ratio = step_number / 7
            bull_pct = day7_bull_pct * ratio
            bear_pct = day7_bear_pct * ratio
        else:
            ratio = (step_number - 7) / 23
            bull_pct = day7_bull_pct + (day30_bull_pct - day7_bull_pct) * ratio
            bear_pct = day7_bear_pct + (day30_bear_pct - day7_bear_pct) * ratio
        return bull_pct, bear_pct

    def apply_scenario_envelope(point: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(point)
        step_number = int(enriched.get("step_number") or 0)
        baseline_price = float(
            enriched.get("final_price")
            or enriched.get("baseline_price")
            or enriched.get("base")
            or latest_price
        )
        applied_override_pct = intelligence_override_pct(step_number)
        proposed_override_pct = raw_intelligence_override_pct(step_number)
        base_price = baseline_price * (1.0 + applied_override_pct / 100.0)
        if step_number <= 0:
            bull_price = round(base_price, 2)
            bear_price = round(base_price, 2)
        else:
            bull_pct, bear_pct = scenario_spread_pct(step_number)
            raw_bull_price = base_price * (1.0 + bull_pct)
            raw_bear_price = base_price * (1.0 + bear_pct)
            bull_price = round(max(base_price, raw_bull_price, raw_bear_price), 2)
            bear_price = round(min(base_price, raw_bull_price, raw_bear_price), 2)
        enriched["baseline_price"] = round(baseline_price, 2)
        enriched["proposed_override_pct"] = round(proposed_override_pct, 2)
        enriched["applied_override_pct"] = round(applied_override_pct, 2)
        enriched["event_signal_present"] = intelligence_active
        enriched["event_routing_applied"] = abs(applied_override_pct) > 1e-9
        enriched["event_routing_blocked"] = intelligence_active and abs(proposed_override_pct) > 1e-9 and abs(applied_override_pct) <= 1e-9
        enriched["base"] = round(base_price, 2)
        enriched["final_price"] = round(base_price, 2)
        enriched["bull"] = bull_price
        enriched["bear"] = bear_price
        enriched["bull_price"] = bull_price
        enriched["bear_price"] = bear_price
        enriched["cause_summary"] = cause_summary
        enriched["evidence_event_ids"] = list(evidence_event_ids)
        if enriched.get("live_challenger_promotion"):
            enriched["source"] = str(enriched.get("source") or "kimi_approved_live_challenger")
        elif intelligence_active and abs(applied_override_pct) > 1e-9:
            enriched["source"] = "automatic_event_intelligence"
        elif intelligence_active:
            enriched["source"] = "event_intelligence_observed_not_routed"
        else:
            enriched["source"] = str(enriched.get("source") or "arima_native")
        return enriched

    # --- interpolate 30 daily forecast points (piecewise: day 0-7, 7-30) ---
    forecast_points: list[dict] = []
    for i in range(1, 31):
        d = (latest_date + timedelta(days=i)).strftime("%Y-%m-%d")

        if i <= 7:
            t7 = i / 7
            base_p = latest_price + (target_7_base - latest_price) * t7
            bull_p = latest_price + (target_7_bull - latest_price) * t7
            bear_p = latest_price + (target_7_bear - latest_price) * t7
        else:
            t30 = (i - 7) / 23
            base_p = target_7_base + (target_base - target_7_base) * t30
            bull_p = target_7_bull + (target_bull - target_7_bull) * t30
            bear_p = target_7_bear + (target_bear - target_7_bear) * t30

        forecast_points.append(
            {
                "date": d,
                "base": round(base_p, 2),
                "bull": round(bull_p, 2),
                "bear": round(bear_p, 2),
            }
        )

    # Anchor first forecast point to latest history price so chart is continuous
    history_points[-1]["base"] = latest_price
    history_points[-1]["bull"] = latest_price
    history_points[-1]["bear"] = latest_price

    # --- Phase 2.1: data-quality + cadence signal ---
    try:
        from backend.src.service.forecasting_engine import (
            get_cadence_info,
            get_native_baseline_forecast_path,
            get_price_series_quality,
        )
        data_quality = get_price_series_quality(product_id)
        cadence_info = get_cadence_info(product_id)
        native_baseline = get_native_baseline_forecast_path(
            product_id,
            horizon_days=30,
            start_date=latest_date,
        )
    except Exception as _dq_exc:
        logger.debug("data_quality/cadence computation skipped: %s", _dq_exc)
        data_quality = {
            "raw_observations": None,
            "model_window_days": None,
            "ffill_ratio": None,
            "low_data": None,
            "reason": "unavailable",
        }
        cadence_info = {
            "cadence_class": "unsupported",
            "change_frequency": None,
            "native_step_days": None,
            "daily_forecast_advised": None,
            "display_preferred_series": "forecast",
        }
        native_baseline = {
            "status": "unavailable",
            "source": "unavailable",
            "dense_path": [],
            "native_path": [],
            "message": "Native baseline unavailable.",
        }

    # --- Phase 1 win validation: below-naive flag ---
    try:
        from backend.src.service.forecasting_engine import _BELOW_NAIVE_PRODUCTS, resolve_champion_model
        native_components = native_baseline.get("model_components") if isinstance(native_baseline, dict) else {}
        champion_routing = (
            native_components.get("champion_routing")
            if isinstance(native_components, dict) and isinstance(native_components.get("champion_routing"), dict)
            else resolve_champion_model(product_id, 30)
        )
        selected_model = str(champion_routing.get("selected_model") or "arima_residual_daily")
        _below = product_id in _BELOW_NAIVE_PRODUCTS
        model_quality = {
            "below_naive_baseline": _below,
            "champion_selected_model": selected_model,
            "champion_routing": copy.deepcopy(champion_routing),
            "warning": (
                "Old-data validation selected the naive baseline for this product/horizon; "
                "the measured model path did not beat holding the latest price constant."
                if selected_model == "naive"
                else (
                    "Current official Phase 1 model performed worse than naive for this product, "
                    "but champion routing selected the newer residual-daily path for this horizon."
                    if _below else ""
                )
            ),
        }
    except Exception as _mq_exc:
        logger.debug("model_quality computation skipped: %s", _mq_exc)
        model_quality = {"below_naive_baseline": None, "warning": "unavailable"}

    native_step_days = cadence_info.get("native_step_days")
    if not isinstance(native_step_days, int) or native_step_days < 1:
        native_step_days = 1

    cadence_class = str(cadence_info.get("cadence_class") or "unsupported")
    if cadence_info.get("daily_forecast_advised"):
        daily_forecast_mode = "native_daily"
    elif cadence_class in {"multi-day", "weekly", "sparse"}:
        daily_forecast_mode = f"ml_daily_from_{cadence_class.replace('-', '_')}"
    else:
        daily_forecast_mode = "ml_daily_from_unsupported"

    kimi_feature_bundle = kimi_forecast_feature_service.get_product_features(product_id)
    kimi_feature = kimi_feature_bundle.get("feature") or {}
    kimi_horizon_bias = (
        kimi_feature.get("horizon_bias_pct")
        if isinstance(kimi_feature.get("horizon_bias_pct"), dict)
        else {}
    )
    kimi_reason_codes = [
        str(item)
        for item in (kimi_feature.get("reason_codes") or [])
        if str(item or "").strip()
    ] if isinstance(kimi_feature.get("reason_codes"), list) else []
    kimi_evidence_event_ids = [
        str(item)
        for item in (kimi_feature.get("evidence_event_ids") or [])
        if str(item or "").strip()
    ] if isinstance(kimi_feature.get("evidence_event_ids"), list) else []
    try:
        kimi_confidence = max(0.0, min(1.0, float(kimi_feature.get("confidence") or 0.0)))
    except (TypeError, ValueError):
        kimi_confidence = 0.0
    kimi_snapshot_status = str(kimi_feature_bundle.get("status") or "missing")
    try:
        kimi_is_fresh = (
            bool(kimi_feature_bundle.get("expires_at"))
            and datetime.fromisoformat(str(kimi_feature_bundle.get("expires_at"))) >= datetime.utcnow()
        )
    except Exception:
        kimi_is_fresh = False
    try:
        ffill_ratio = float(data_quality.get("ffill_ratio") or 0.0)
    except (TypeError, ValueError):
        ffill_ratio = 0.0
    cadence_weight = {
        "daily-capable": 1.0,
        "multi-day": 0.85,
        "weekly": 0.7,
        "sparse": 0.55,
    }.get(cadence_class, 0.4)
    quality_weight = max(0.35, 1.0 - max(0.0, min(1.0, ffill_ratio)) * 0.6)
    if data_quality.get("low_data"):
        quality_weight *= 0.8
    kimi_application_weight = 0.0
    if kimi_snapshot_status == "success" and kimi_is_fresh and kimi_confidence > 0.0:
        kimi_application_weight = round(kimi_confidence * cadence_weight * quality_weight, 4)

    def kimi_raw_bias_pct(step_number: int) -> float:
        if step_number <= 1:
            key = "1d"
        elif step_number <= 3:
            key = "3d"
        elif step_number <= 7:
            key = "7d"
        elif step_number <= 14:
            key = "14d"
        else:
            key = "30d"
        try:
            return round(float(kimi_horizon_bias.get(key) or 0.0), 2)
        except (TypeError, ValueError):
            return 0.0

    def event_adjustment_enabled_for_horizon_key(key: str) -> bool:
        horizon_days = {"1d": 1, "3d": 3, "7d": 7, "14d": 14, "30d": 30}.get(key, 30)
        return bool(event_adjustment_policy_for_step(horizon_days).get("enabled"))

    applied_kimi_horizon_bias = {
        key: round(float(kimi_horizon_bias.get(key) or 0.0) * kimi_application_weight, 2)
        if event_adjustment_enabled_for_horizon_key(key)
        else 0.0
        for key in ("1d", "3d", "7d", "14d", "30d")
    }
    kimi_feature_signal = {
        "snapshot_id": kimi_feature_bundle.get("snapshot_id"),
        "status": kimi_feature_bundle.get("status"),
        "generated_at": kimi_feature_bundle.get("generated_at"),
        "expires_at": kimi_feature_bundle.get("expires_at"),
        "model": kimi_feature_bundle.get("model"),
        "feature": kimi_feature,
        "application_weight": kimi_application_weight,
        "applied_to_numeric_forecast": False,
        "integration_stage": "feature_snapshot_available_not_applied",
    }

    baseline_status = str(native_baseline.get("status") or "unavailable")
    dense_baseline_path = native_baseline.get("dense_path") if isinstance(native_baseline, dict) else []
    native_baseline_path = native_baseline.get("native_path") if isinstance(native_baseline, dict) else []
    native_components = native_baseline.get("model_components") if isinstance(native_baseline, dict) else {}
    champion_routing = (
        native_components.get("champion_routing")
        if isinstance(native_components, dict) and isinstance(native_components.get("champion_routing"), dict)
        else {}
    )
    champion_selected_model = str(champion_routing.get("selected_model") or "arima_residual_daily")
    if baseline_status == "ok" and isinstance(dense_baseline_path, list) and isinstance(native_baseline_path, list):
        if champion_selected_model != "naive" and dense_baseline_path and not any(
            isinstance(point, dict)
            and isinstance(point.get("daily_calibration"), dict)
            and point["daily_calibration"].get("active")
            for point in dense_baseline_path
        ):
            try:
                import numpy as np

                from backend.src.service.forecasting_engine import (
                    _historical_residual_daily_calibration,
                    _series_for_product,
                )

                baseline_values = np.asarray(
                    [float(point.get("base")) for point in dense_baseline_path],
                    dtype=float,
                )
                calibrated_values, daily_calibration = _historical_residual_daily_calibration(
                    _series_for_product(product_id),
                    baseline_values,
                    cadence_info,
                )
                if daily_calibration.get("active") and len(calibrated_values) == len(dense_baseline_path):
                    calibrated_source = f"{native_baseline.get('source', 'arima_native')}_residual_daily"
                    dense_baseline_path = [
                        {
                            **point,
                            "base": round(float(calibrated_values[idx]), 2),
                            "baseline_price": round(float(calibrated_values[idx]), 2),
                            "final_price": round(float(calibrated_values[idx]), 2),
                            "source": calibrated_source,
                            "daily_calibration": daily_calibration,
                        }
                        for idx, point in enumerate(dense_baseline_path)
                    ]
                    native_baseline = {
                        **native_baseline,
                        "source": calibrated_source,
                        "message": (
                            f"{native_baseline.get('message', 'Real per-step baseline generated.')} "
                            "Daily path calibrated with recent historical residual volatility."
                        ),
                    }
            except Exception as calibration_exc:
                logger.debug("daily residual calibration skipped: %s", calibration_exc)
        enriched_dense_path = [
            apply_scenario_envelope(point)
            for point in dense_baseline_path
            if isinstance(point, dict) and point.get("date")
        ]
        native_step_numbers = {
            int(point.get("step_number") or 0)
            for point in native_baseline_path
            if isinstance(point, dict) and point.get("date")
        }
        enriched_native_path = [
            point for point in enriched_dense_path if int(point.get("step_number") or 0) in native_step_numbers
        ]
        forecast_dense = [
            {
                "date": point["date"],
                "base": point["base"],
                "bull": point["bull"],
                "bear": point["bear"],
            }
            for point in enriched_dense_path
        ]
        forecast_native = [
            {
                "date": point["date"],
                "base": point["base"],
                "bull": point["bull"],
                "bear": point["bear"],
            }
            for point in enriched_native_path
        ]
        forecast_path = enriched_dense_path
        forecast_points = forecast_dense
        forecast_generation = {
            "baseline_status": "ok",
            "baseline_source": native_baseline.get("source", "arima_native"),
            "path_source": "real_model_output",
            "selected_model": champion_selected_model,
            "champion_routing": copy.deepcopy(champion_routing),
            "display_series": "forecast",
            "native_display_series": "forecast" if cadence_info.get("daily_forecast_advised") else "forecast_native",
            "daily_forecast_mode": daily_forecast_mode,
            "kimi_feature_snapshot_id": kimi_feature_bundle.get("snapshot_id"),
            "message": native_baseline.get("message", "Real per-step baseline generated."),
        }
        validation_status = (
            "approved_arima_native_intelligence_adjusted"
            if intelligence_active
            else "approved_arima_native_baseline"
        )
    else:
        forecast_dense = forecast_points
        forecast_native = [
            point
            for idx, point in enumerate(forecast_points, start=1)
            if idx == len(forecast_points) or idx % native_step_days == 0
        ]
        forecast_path = [
            {
                "date": point.get("date"),
                "forecast_date": point.get("date"),
                "step_number": idx,
                "baseline_price": round(
                    float(point.get("base") or latest_price)
                    / (1.0 + (intelligence_override_pct(idx) / 100.0))
                    if abs(1.0 + (intelligence_override_pct(idx) / 100.0)) > 1e-9
                    else float(point.get("base") or latest_price),
                    2,
                ),
                "proposed_override_pct": round(raw_intelligence_override_pct(idx), 2),
                "applied_override_pct": round(intelligence_override_pct(idx), 2),
                "final_price": point.get("base"),
                "base": point.get("base"),
                "bull": point.get("bull"),
                "bear": point.get("bear"),
                "confidence": None,
                "cause_summary": (
                    cause_summary
                    if intelligence_active
                    else "Legacy scenario-anchor fallback; native model path unavailable."
                ),
                "evidence_event_ids": list(evidence_event_ids),
                "event_signal_present": intelligence_active,
                "event_routing_applied": abs(intelligence_override_pct(idx)) > 1e-9,
                "event_routing_blocked": intelligence_active and abs(raw_intelligence_override_pct(idx)) > 1e-9 and abs(intelligence_override_pct(idx)) <= 1e-9,
                "source": (
                    "legacy_interpolated_event_adjusted"
                    if intelligence_active
                    else "legacy_interpolated_fallback"
                ),
            }
            for idx, point in enumerate(forecast_points, start=1)
            if isinstance(point, dict)
        ]
        forecast_generation = {
            "baseline_status": baseline_status,
            "baseline_source": native_baseline.get("source", "unavailable") if isinstance(native_baseline, dict) else "unavailable",
            "path_source": (
                "legacy_interpolated_event_adjusted"
                if intelligence_active
                else "legacy_interpolated_fallback"
            ),
            "selected_model": champion_selected_model,
            "champion_routing": copy.deepcopy(champion_routing),
            "display_series": "forecast",
            "native_display_series": "forecast_native",
            "daily_forecast_mode": daily_forecast_mode,
            "kimi_feature_snapshot_id": kimi_feature_bundle.get("snapshot_id"),
            "message": native_baseline.get("message", "Native model path unavailable; used legacy fallback.") if isinstance(native_baseline, dict) else "Native model path unavailable; used legacy fallback.",
        }
        validation_status = (
            "legacy_interpolated_event_adjusted"
            if intelligence_active
            else "legacy_interpolated_fallback"
        )

    forecast_adjustment = {
        "source": (
            "automatic_event_intelligence"
            if intelligence_active and event_adjustment_policy_7d.get("enabled")
            else "event_intelligence_observed_not_routed"
            if intelligence_active
            else "baseline_only"
        ),
        "proposed_override_pct": round(float(intelligence_signal.get("net_price_pct", 0.0) or 0.0), 2),
        "applied_override_pct": round(intelligence_override_pct(7), 2),
        "raw_horizon_adjustment_pct": {
            "3d": round(raw_intelligence_override_pct(3), 2),
            "7d": round(raw_intelligence_override_pct(7), 2),
            "30d": round(raw_intelligence_override_pct(30), 2),
        },
        "bound_pct": round(
            max(
                abs(intelligence_override_pct(3)),
                abs(intelligence_override_pct(7)),
                abs(intelligence_override_pct(30)),
            ),
            2,
        ) if intelligence_active else 0.0,
        "event_adjustment_policy": copy.deepcopy(event_adjustment_policy_7d),
        "confidence": intelligence_signal.get("confidence") if intelligence_active else None,
        "evidence_event_ids": list(evidence_event_ids),
        "evidence_events": evidence_events,
        "dominant_event": dominant_event if dominant_event else None,
        "matched_event_count": event_count,
        "horizon_adjustment_pct": copy.deepcopy(intelligence_adj_pct),
        "kimi_horizon_bias_pct": copy.deepcopy(kimi_horizon_bias),
        "kimi_applied_horizon_bias_pct": copy.deepcopy(applied_kimi_horizon_bias),
        "kimi_reason_codes": list(kimi_reason_codes),
        "kimi_evidence_event_ids": list(kimi_evidence_event_ids),
        "kimi_application_weight": kimi_application_weight,
        "kimi_applied_to_numeric_forecast": False,
        "combined_horizon_adjustment_pct": {
            "1d": round(applied_kimi_horizon_bias.get("1d", 0.0), 2),
            "3d": round(intelligence_override_pct(3) + applied_kimi_horizon_bias.get("3d", 0.0), 2),
            "7d": round(intelligence_override_pct(7) + applied_kimi_horizon_bias.get("7d", 0.0), 2),
            "14d": round(applied_kimi_horizon_bias.get("14d", 0.0), 2),
            "30d": round(intelligence_override_pct(30) + applied_kimi_horizon_bias.get("30d", 0.0), 2),
        },
        "cause_summary": cause_summary,
    }

    combined_evidence_event_ids = list(evidence_event_ids)
    forecast_series_by_date: dict[str, dict[str, Any]] = {}
    any_kimi_applied = False
    kimi_note = ""
    if kimi_application_weight > 0.0:
        if kimi_reason_codes:
            kimi_note = f"Kimi feature adjustment applied from {', '.join(kimi_reason_codes[:3])}."
        else:
            kimi_note = "Kimi cross-node feature adjustment applied."

    for idx, point in enumerate(forecast_path, start=1):
        if not isinstance(point, dict):
            continue
        step_number = int(point.get("step_number") or idx)
        raw_feature_bias = kimi_raw_bias_pct(step_number)
        point_event_policy = event_adjustment_policy_for_step(step_number)
        feature_bias = (
            round(raw_feature_bias * kimi_application_weight, 2)
            if point_event_policy.get("enabled")
            else 0.0
        )
        point["daily_forecast_mode"] = daily_forecast_mode
        point["event_adjustment_policy"] = copy.deepcopy(point_event_policy)
        point["kimi_feature_raw_adjustment_pct"] = raw_feature_bias
        point["kimi_feature_adjustment_pct"] = feature_bias
        point["kimi_feature_confidence"] = kimi_confidence
        point["kimi_feature_application_weight"] = kimi_application_weight
        point["kimi_reason_codes"] = list(kimi_reason_codes)
        point["kimi_evidence_event_ids"] = list(kimi_evidence_event_ids)
        point["kimi_feature_applied"] = abs(feature_bias) > 1e-9

        try:
            prior_override_pct = float(point.get("applied_override_pct") or 0.0)
        except (TypeError, ValueError):
            prior_override_pct = 0.0
        point["intelligence_override_pct"] = round(prior_override_pct, 2)

        if point["kimi_feature_applied"]:
            any_kimi_applied = True
            multiplier = 1.0 + (feature_bias / 100.0)
            current_base = float(point.get("base") or point.get("final_price") or point.get("baseline_price") or latest_price)
            current_final = float(point.get("final_price") or current_base)
            current_bull = float(point.get("bull") or current_base)
            current_bear = float(point.get("bear") or current_base)
            adjusted_base = round(current_base * multiplier, 2)
            adjusted_final = round(current_final * multiplier, 2)
            adjusted_bull = round(max(adjusted_base, current_bull * multiplier, current_bear * multiplier), 2)
            adjusted_bear = round(min(adjusted_base, current_bull * multiplier, current_bear * multiplier), 2)
            point["base"] = adjusted_base
            point["final_price"] = adjusted_final
            point["bull"] = adjusted_bull
            point["bear"] = adjusted_bear
            point["bull_price"] = adjusted_bull
            point["bear_price"] = adjusted_bear
            point["proposed_override_pct"] = round(float(point.get("proposed_override_pct") or prior_override_pct) + feature_bias, 2)
            point["applied_override_pct"] = round(prior_override_pct + feature_bias, 2)
            point["evidence_event_ids"] = list(dict.fromkeys([*point.get("evidence_event_ids", []), *kimi_evidence_event_ids]))
            if kimi_note:
                point["cause_summary"] = f"{str(point.get('cause_summary') or cause_summary).strip()} {kimi_note}".strip()
            point_source = str(point.get("source") or "")
            if point_source == "automatic_event_intelligence":
                point["source"] = "automatic_event_intelligence_kimi_adjusted"
            elif point_source:
                point["source"] = f"{point_source}_kimi_adjusted"
            else:
                point["source"] = "kimi_feature_adjusted"

        forecast_series_by_date[str(point.get("date") or point.get("forecast_date") or "")] = {
            "base": point.get("base"),
            "bull": point.get("bull"),
            "bear": point.get("bear"),
            "kimi_feature_adjustment_pct": point.get("kimi_feature_adjustment_pct"),
            "kimi_feature_applied": point.get("kimi_feature_applied"),
        }

    for series in (forecast_dense, forecast_native):
        for point in series:
            if not isinstance(point, dict):
                continue
            series_point = forecast_series_by_date.get(str(point.get("date") or point.get("forecast_date") or ""))
            if not series_point:
                continue
            point["base"] = series_point["base"]
            point["bull"] = series_point["bull"]
            point["bear"] = series_point["bear"]
            point["daily_forecast_mode"] = daily_forecast_mode
            point["kimi_feature_adjustment_pct"] = series_point["kimi_feature_adjustment_pct"]
            point["kimi_feature_applied"] = series_point["kimi_feature_applied"]

    if any_kimi_applied:
        combined_evidence_event_ids = list(dict.fromkeys([*evidence_event_ids, *kimi_evidence_event_ids]))
        kimi_feature_signal["applied_to_numeric_forecast"] = True
        kimi_feature_signal["integration_stage"] = "deterministic_exogenous_adjustment_active"
        forecast_adjustment["kimi_applied_to_numeric_forecast"] = True
        forecast_adjustment["combined_applied_override_pct"] = round(
            float(forecast_adjustment.get("applied_override_pct") or 0.0) + applied_kimi_horizon_bias.get("7d", 0.0),
            2,
        )
        forecast_adjustment["evidence_event_ids"] = list(combined_evidence_event_ids)
        if intelligence_active:
            forecast_adjustment["source"] = "automatic_event_intelligence_plus_kimi_features"
        else:
            forecast_adjustment["source"] = "kimi_feature_deterministic_adjustment"
        forecast_generation["path_source"] = f"{forecast_generation.get('path_source')}_kimi_feature_adjusted"
        forecast_generation["message"] = f"{forecast_generation.get('message', '').strip()} Kimi feature adjustments applied deterministically.".strip()
        if kimi_note:
            cause_summary = f"{cause_summary} {kimi_note}".strip()
            forecast_adjustment["cause_summary"] = cause_summary
    else:
        forecast_adjustment["combined_applied_override_pct"] = round(
            float(forecast_adjustment.get("applied_override_pct") or 0.0),
            2,
        )

    # P0: Trust tier — compute before payload so it can be injected
    from backend.src.service.forecast_supervisor import compute_forecast_trust_tier as _compute_trust_tier

    payload = {
        "product_id": product_id,
        "product_name": node.name_cn,
        "unit": response_unit,
        "history": history_points,
        "forecast": forecast_points,
        "forecast_dense": forecast_dense,
        "forecast_native": forecast_native,
        "forecast_path": forecast_path,
        "forecast_generation": forecast_generation,
        "forecast_adjustment": forecast_adjustment,
        "kimi_feature_signal": kimi_feature_signal,
        "daily_forecast_mode": daily_forecast_mode,
        "cause_summary": cause_summary,
        "evidence_event_ids": list(combined_evidence_event_ids),
        "evidence_events": evidence_events,
        "scenarios_summary": fc.get("scenarios", {}),
        "seasonal_adj_pct": fc.get("seasonal_adj_pct", 0),
        "policy": fc.get("policy", {}),
        "model_version": fc.get("model_version", "v1"),
        "cadence_info": cadence_info,
        "data_quality": data_quality,
        "model_quality": model_quality,
        "trust_tier": (
            _compute_trust_tier(
                product_id,
                below_naive=bool(model_quality.get("below_naive_baseline")),
                cadence_class=str(cadence_info.get("cadence_class") or "unsupported"),
            )
            if isinstance(model_quality, dict)
            else {"tier": "unknown", "cadence_class": "unknown", "below_naive": None}
        ),
        "intelligence_fingerprint": copy.deepcopy(intelligence_fingerprint),
        "market_fingerprint": copy.deepcopy(market_fingerprint or {}),
        "forecast_snapshot": {
            "status": "computed_live",
            "validation_status": validation_status,
            "source": "live_compute",
            "intelligence_fingerprint": copy.deepcopy(intelligence_fingerprint),
            "market_fingerprint": copy.deepcopy(market_fingerprint or {}),
        },
    }

    # --- Persist daily forecast snapshot for accuracy tracking ---
    try:
        from backend.src.db.daily_calibration import persist_forecast_snapshot
        persist_forecast_snapshot(
            db.engine,
            product_id,
            forecast_points,
            model_version=str(fc.get("model_version") or "v1"),
        )
    except Exception as _persist_exc:
        logger.debug("Forecast accuracy snapshot persist skipped: %s", _persist_exc)

    # --- Persist canonical forecast decision snapshot for shared EDIA consumers ---
    try:
        from backend.src.db.forecast_snapshots import persist_snapshot
        snapshot_id = persist_snapshot(
            db.engine,
            payload,
            run_reason="live_compute",
        )
        payload["snapshot_id"] = snapshot_id
        payload["forecast_snapshot"] = {
            **payload.get("forecast_snapshot", {}),
            "snapshot_id": snapshot_id,
            "status": "approved",
        }
    except Exception as _snapshot_persist_exc:
        logger.debug("Canonical forecast snapshot persist skipped: %s", _snapshot_persist_exc)

    return payload


@app.get("/api/v2/market/price-chain")
async def get_price_chain():
    """Returns price transmission chain data for the upstream dashboard panel."""
    _ensure_market_runtime_fresh()
    loop = asyncio.get_event_loop()
    payload = await loop.run_in_executor(None, _compute_price_chain)
    return payload


_price_chain_cache: dict = {"data": None, "ts": 0.0}
_PRICE_CHAIN_TTL = 60  # seconds


def _select_price_chain_reference_point(
    history: list[dict],
    target_date: date,
    tolerance_days: int = 5,
) -> tuple[float | None, str | None]:
    best_point = None
    best_date = None
    best_abs_delta = None

    for point in history[:-1]:
        point_date = _snapshot_point_date(point)
        if point_date is None:
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


def _get_usd_cny_rate() -> float:
    """Fetch latest USD/CNY rate from DB, default to 7.25 if unavailable."""
    try:
        with db.engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT date, product_id, price_type, price
                    FROM market_prices
                    WHERE price IS NOT NULL
                      AND (
                        LOWER(product_id) IN ('usd_rmb', 'usd_cny', 'fx_usd_cny', 'usdcny', 'usdrmb')
                        OR LOWER(price_type) IN ('usd_rmb', 'usd_cny', 'fx_usd_cny', 'usdcny', 'usdrmb')
                        OR (LOWER(product_id) LIKE '%usd%' AND LOWER(product_id) LIKE '%cny%')
                        OR (LOWER(product_id) LIKE '%usd%' AND LOWER(product_id) LIKE '%rmb%')
                        OR (LOWER(price_type) LIKE '%usd%' AND LOWER(price_type) LIKE '%cny%')
                        OR (LOWER(price_type) LIKE '%usd%' AND LOWER(price_type) LIKE '%rmb%')
                      )
                    ORDER BY date DESC
                    LIMIT 1
                    """
                )
            ).fetchone()
        if row:
            return float(row[3])
    except Exception:
        pass
    return 7.25  # default fallback


def _compute_percentile_remark(history: list[dict], latest_price: float, language: str = "zh") -> str:
    """
    Compute the percentile rank of the latest price against ~3 years of history.
    Returns a remark string in the requested language.
    """
    if len(history) < 10:
        return ""

    # Use up to ~3 years (1095 days) of history; fall back to all available if less
    three_years_ago = (datetime.now() - timedelta(days=1095)).date().isoformat()
    historical_prices = [
        float(p["price"]) for p in history
        if str(p.get("date", ""))[:10] <= three_years_ago
    ]
    if len(historical_prices) < 10:
        historical_prices = [float(p["price"]) for p in history]

    if not historical_prices:
        return ""

    sorted_prices = sorted(historical_prices)
    rank = sum(1 for p in sorted_prices if p < latest_price)
    percentile = round(rank / len(sorted_prices) * 100, 1)

    if language == "en":
        if percentile <= 10:
            return f"At {percentile}th percentile in ~3 years — extremely low"
        elif percentile >= 90:
            return f"At {percentile}th percentile in ~3 years — extremely high"
        elif percentile <= 30:
            return f"At {percentile}th percentile in ~3 years — relatively low"
        elif percentile >= 70:
            return f"At {percentile}th percentile in ~3 years — relatively high"
        else:
            return f"At {percentile}th percentile in ~3 years — near median"
    else:
        if percentile <= 10:
            return f"处于近3年{percentile}%分位—表示极低"
        elif percentile >= 90:
            return f"处于近3年{percentile}%分位—表示极高"
        elif percentile <= 30:
            return f"处于近3年{percentile}%分位—表示较低"
        elif percentile >= 70:
            return f"处于近3年{percentile}%分位—表示较高"
        else:
            return f"处于近3年{percentile}%分位—接近中位"


def _get_execution_prices(product_ids: list[str]) -> dict[str, dict]:
    """Fetch latest and previous erp_sale_order_weighted price for each product in one query."""
    from backend.src.db.sqlite_db import db as _app_db
    result: dict[str, dict] = {}
    if not product_ids:
        return result
    try:
        placeholders = ", ".join(f":pid{i}" for i in range(len(product_ids)))
        params = {f"pid{i}": pid for i, pid in enumerate(product_ids)}
        with _app_db.engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"SELECT product_id, price, date "
                    f"FROM market_prices "
                    f"WHERE product_id IN ({placeholders}) "
                    f"  AND price_type = 'erp_sale_order_weighted' "
                    f"  AND price IS NOT NULL "
                    f"ORDER BY product_id, date DESC"
                ),
                params,
            ).fetchall()
        count: dict[str, int] = {}
        for product_id, price, date in rows:
            pid = str(product_id)
            cnt = count.get(pid, 0)
            if cnt == 0:
                result[pid] = {"price": float(price), "date": str(date)[:10]}
            elif cnt == 1:
                prev_price = float(price)
                prev_date = str(date)[:10]
                cur_price = result[pid]["price"]
                result[pid]["prev_price"] = prev_price
                result[pid]["prev_date"] = prev_date
                result[pid]["change"] = round(cur_price - prev_price, 2)
                result[pid]["change_pct"] = round(
                    (cur_price - prev_price) / prev_price * 100, 2
                ) if prev_price else None
            count[pid] = cnt + 1
    except Exception:
        pass
    return result


def _compute_price_chain() -> dict:
    chain_products = [
        "crude_oil",
        "naphtha",
        "cracked_c5",
        "cracked_c9",
        "isoprene",
        "piperylene",
        "dcpd",
    ]

    # Fetch USD/CNY once for FX conversion
    usd_cny = _get_usd_cny_rate()
    execution_prices = _get_execution_prices(chain_products)

    # Products priced in USD that need conversion to CNY
    usd_products = {"crude_oil", "naphtha"}

    nodes_out: list[dict] = []
    for pid in chain_products:
        history = market_data.get_price_history(pid)
        if not history:
            continue
        latest_row = history[-1]
        latest = float(latest_row["price"])

        # --- FX conversion for USD-priced products ---
        node = next((n for n in domain_graph.nodes if n.id == pid), None)
        original_unit = node.unit if node else "CNY/MT"
        is_usd = original_unit.startswith("USD/")

        price_cny = latest
        display_unit = original_unit
        if is_usd:
            price_cny = latest * usd_cny
            display_unit = "CNY/" + original_unit.split("/", 1)[1] if "/" in original_unit else "CNY/MT"

        # --- 30-calendar-day change (date-based) ---
        change_pct = 0.0
        latest_date_str: str | None = None
        target_date_str: str | None = None
        reference_date_str: str | None = None
        latest_date_str = str(latest_row.get("date", ""))[:10]
        try:
            latest_date = datetime.fromisoformat(latest_date_str)
            baseline_date = (latest_date - timedelta(days=30)).date().isoformat()
            target_date_str = baseline_date

            old_price, reference_date_str = _select_price_chain_reference_point(
                history,
                datetime.fromisoformat(baseline_date).date(),
            )
            if old_price not in (None, 0):
                old_price = float(old_price)
                change_pct = round((latest - old_price) / old_price * 100, 2)
        except Exception:
            change_pct = 0.0
            latest_date_str = None
            target_date_str = None
            reference_date_str = None

        # --- Sparkline series: last 30 data points ---
        sparkline_series: list[float] = []
        for row in history[-30:]:
            p = float(row["price"])
            if is_usd:
                p = p * usd_cny
            sparkline_series.append(round(p, 2))

        # --- Historical percentile remark (zh + en) ---
        remark_zh = _compute_percentile_remark(history, latest, "zh")
        remark_en = _compute_percentile_remark(history, latest, "en")

        exec_info = execution_prices.get(pid)
        nodes_out.append(
            {
                "id": pid,
                "name_cn": node.name_cn if node else pid,
                "name_en": node.name_en if node else pid,
                "price": round(price_cny, 2),
                "change_pct": change_pct,
                "change_latest_date": latest_date_str,
                "change_target_date": target_date_str,
                "change_reference_date": reference_date_str,
                "unit": display_unit,
                "sparkline_series": sparkline_series,
                "remark_zh": remark_zh,
                "remark_en": remark_en,
                "execution_price": exec_info["price"] if exec_info else None,
                "execution_price_date": exec_info["date"] if exec_info else None,
                "execution_price_change": exec_info["change"] if exec_info else None,
                "execution_price_change_pct": exec_info["change_pct"] if exec_info else None,
                "execution_price_prev_date": exec_info["prev_date"] if exec_info else None,
            }
        )

    # Edges from knowledge graph
    edges_out: list[dict] = []
    for edge in domain_graph.edges:
        if edge.source_id in chain_products and edge.target_id in chain_products:
            edges_out.append(
                {
                    "source": edge.source_id,
                    "target": edge.target_id,
                }
            )
    return {"nodes": nodes_out, "edges": edges_out}


# --- Inventory Safety Warning (midstream) ---

_inventory_safety_cache: dict = {"data": None, "ts": 0.0}
_INVENTORY_SAFETY_TTL = 60  # seconds


def _compute_inventory_safety() -> list[dict]:
    from backend.src.service.erp_kpi_service import ErpKPIService
    from backend.src.service.inventory_safety_config_service import (
        inventory_safety_config_service,
    )

    kpi_svc = ErpKPIService()
    config_map = inventory_safety_config_service.get_config_map()
    items = []
    for pid, cfg in config_map.items():
        if _is_excluded_product_id(pid) or _is_excluded_product_name(cfg.name_cn):
            continue
        kpi = kpi_svc.get_product_kpi(pid)
        inv_tons = kpi.inventory_tons if kpi and kpi.inventory_tons else 0
        inv_days = kpi.inventory_days if kpi and kpi.inventory_days else 0
        sales_30d = kpi.sales_30d_ton if kpi and kpi.sales_30d_ton else 0

        daily = cfg.daily_consumption
        if inv_days == 0 and inv_tons > 0 and daily > 0:
            inv_days = round(inv_tons / daily, 1)

        safety_min = cfg.safety_min
        safety_max = cfg.safety_max

        if inv_tons <= safety_min * 0.5:
            status = "danger"
        elif inv_tons <= safety_min:
            status = "warning"
        elif inv_tons <= safety_max:
            status = "safe"
        else:
            status = "excess"

        items.append(
            {
                "product_id": pid,
                "name_cn": cfg.name_cn,
                "inventory_tons": round(inv_tons, 1),
                "inventory_days": round(inv_days, 1),
                "safety_min": safety_min,
                "safety_max": safety_max,
                "sales_30d": round(sales_30d, 1),
                "daily_consumption": cfg.daily_consumption,
                "status": status,
            }
        )
    return items


@app.get("/api/v2/inventory/safety")
async def get_inventory_safety():
    """Returns inventory safety warning data for midstream products."""
    _ensure_market_runtime_fresh()

    loop = asyncio.get_event_loop()
    items = await loop.run_in_executor(None, _compute_inventory_safety)
    return {"items": items}


# ---------------------------------------------------------------------------
# Part 5 – Forecast Accuracy + Daily Calibration
# ---------------------------------------------------------------------------

@app.post("/api/v2/calibration/run")
async def run_calibration():
    """Trigger the daily forecast calibration pipeline.

    Evaluates pending T-7 forecasts vs actual prices, computes MAPE,
    and updates evidence outcome scores.
    """
    from backend.src.db.daily_calibration import run_daily_calibration, init_tables
    from backend.src.db.sqlite_db import db as _app_db

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, run_daily_calibration)
    return result


@app.get("/api/v2/calibration/mape")
async def get_forecast_mape(days: int = 30):
    """Returns per-product MAPE over last N days of evaluated forecasts."""
    from backend.src.db.daily_calibration import compute_product_mape, init_tables
    from backend.src.db.sqlite_db import db as _app_db

    def _run():
        init_tables(_app_db.engine)
        mape = compute_product_mape(_app_db.engine, days_back=days)
        if isinstance(mape, dict):
            return {
                pid: value
                for pid, value in mape.items()
                if not _is_excluded_product_id(pid)
            }
        return mape

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _run)


@app.get("/api/v2/calibration/evidence-outcomes")
async def get_evidence_outcomes():
    """Returns evidence tier accuracy scores learned from forecast outcomes."""
    from backend.src.db.sqlite_db import db as _app_db
    from sqlalchemy import text as _text

    def _run():
        try:
            with _app_db.engine.connect() as conn:
                rows = conn.execute(
                    _text(
                        "SELECT evidence_tier, product_id, correct, total, accuracy_rate, updated_at "
                        "FROM evidence_outcomes ORDER BY evidence_tier, product_id"
                    )
                ).fetchall()
                return [
                    {
                        "evidence_tier": r[0],
                        "product_id": r[1],
                        "correct": r[2],
                        "total": r[3],
                        "accuracy_rate": r[4],
                        "updated_at": str(r[5]),
                    }
                    for r in rows
                    if not _is_excluded_product_id(r[1])
                ]
        except Exception:
            return []

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _run)
    return {"items": items}


@app.get("/api/v2/market/forecast-accuracy/{product_id}")
async def get_forecast_accuracy(product_id: str, days: int = 30):
    """Returns daily forecast-vs-actual accuracy history for a product.

    Each record shows: forecast_date, target_date, predicted_price, actual_price,
    error_pct, status (evaluated/pending), and the bull/base/bear scenarios stored.
    Also returns aggregate MAPE for the period.
    """
    from backend.src.db.daily_calibration import (
        get_forecast_accuracy_history,
        compute_product_mape,
    )
    from backend.src.db.sqlite_db import db as _app_db

    _ensure_product_allowed(product_id)
    _ensure_market_runtime_fresh()

    def _run():
        try:
            records = get_forecast_accuracy_history(_app_db.engine, product_id, days_back=days)
            mape_all = compute_product_mape(_app_db.engine, days_back=days)
            evaluated = [r for r in records if r["status"] == "evaluated"]
            pending = [r for r in records if r["status"] == "pending"]
            hit_count = sum(1 for r in evaluated if r["error_pct"] is not None and r["error_pct"] < 5.0)
            total_eval = len(evaluated)
            return {
                "product_id": product_id,
                "days": days,
                "mape": mape_all.get(product_id),
                "hit_rate": round(hit_count / total_eval * 100, 1) if total_eval > 0 else None,
                "total_evaluated": total_eval,
                "total_pending": len(pending),
                "records": records,
                "data_source": "mysql",
            }
        except Exception as exc:
            logger.warning(
                "forecast-accuracy fallback: product=%s days=%s error=%s",
                product_id,
                days,
                exc,
                exc_info=True,
            )
            return {
                "product_id": product_id,
                "days": days,
                "mape": None,
                "hit_rate": None,
                "total_evaluated": 0,
                "total_pending": 0,
                "records": [],
                "data_source": "fallback",
                "warning": "Forecast accuracy data temporarily unavailable",
            }

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _run)


@app.get("/api/v2/market/forecast-policy-compare/{product_id}")
async def compare_forecast_policy(
    product_id: str,
    start_date: str,
    end_date: str,
    horizon_days: int = 7,
    step_days: int = 7,
):
    """Compare baseline vs adaptive forecast policy through historical replay."""
    from backend.src.validation.backtest_runner import backtest_runner

    _ensure_product_allowed(product_id)
    _ensure_market_runtime_fresh()

    def _run():
        return backtest_runner.compare_policy_modes(
            product_id=product_id,
            start_date=start_date,
            end_date=end_date,
            horizon_days=horizon_days,
            step_days=step_days,
        )

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _run)


# --- Pricing Action Recommendations (downstream) ---

_DOWNSTREAM_PRODUCTS = ["sis", "c5_resin", "blowing_agent", "styrene"]
_pricing_actions_cache: dict = {"data": None, "ts": 0.0}
_PRICING_ACTIONS_TTL = 120  # seconds


def _compute_single_product(pid: str, snapshot: dict) -> dict | None:
    """Compute pricing recommendation for a single product (runs in thread)."""
    from backend.src.service.diagnosis_service import diagnosis_service
    from backend.src.service.pricing_service import pricing_service

    node = next((n for n in domain_graph.nodes if n.id == pid), None)
    if not node:
        return None
    try:
        diag = diagnosis_service.diagnose(pid, snapshot)
        fc = forecasting_service.forecast(pid, diagnosis=diag)
        decision = pricing_service.compute_pricing_decision(
            product_id=pid,
            perception_snapshot=snapshot,
            diagnosis=diag,
            forecast=fc,
        )
        return {
            "product_id": pid,
            "name_cn": decision.get("product_name_cn", node.name_cn),
            "name_en": decision.get("product_name_en", node.name_en),
            "latest_market_price": decision.get("latest_market_price", 0),
            "recommended_price": decision.get("recommended_price", 0),
            "price_range": decision.get("price_range", [0, 0]),
            "price_delta_vs_market": decision.get("price_delta_vs_market", 0),
            "action": decision.get("action", "hold"),
            "action_cn": decision.get("action_cn", "持平"),
            "confidence": decision.get("confidence", 0),
            "pricing_formula": decision.get("pricing_formula", ""),
            "unit": decision.get("unit", "ton"),
            "adjustment_steps": decision.get("adjustment_steps", []),
        }
    except Exception as exc:
        logger.warning("Pricing action failed for %s: %s", pid, exc)
        return {
            "product_id": pid,
            "name_cn": node.name_cn,
            "name_en": node.name_en,
            "latest_market_price": 0,
            "recommended_price": 0,
            "price_range": [0, 0],
            "price_delta_vs_market": 0,
            "action": "hold",
            "action_cn": "持平",
            "confidence": 0,
            "pricing_formula": "",
            "unit": "ton",
        }


def _compute_pricing_actions() -> dict:
    """Heavy computation: perception → diagnosis → forecast → pricing for all downstream products (parallelized)."""
    import time as _time
    from concurrent.futures import ThreadPoolExecutor
    from backend.src.service.perception_service import perception_service

    t0 = _time.time()
    snapshot = perception_service.build_snapshot()
    logger.info("Pricing snapshot built in %.1fs", _time.time() - t0)

    items = []
    parallelism = _pricing_parallelism()
    if parallelism <= 1:
        for pid in _DOWNSTREAM_PRODUCTS:
            result = _compute_single_product(pid, snapshot)
            if result is not None:
                items.append(result)
    else:
        with ThreadPoolExecutor(max_workers=parallelism) as pool:
            futures = {
                pid: pool.submit(_compute_single_product, pid, snapshot)
                for pid in _DOWNSTREAM_PRODUCTS
            }
            for pid in _DOWNSTREAM_PRODUCTS:
                result = futures[pid].result()
                if result is not None:
                    items.append(result)

    logger.info(
        "Pricing actions computed for %d products in %.1fs",
        len(items),
        _time.time() - t0,
    )
    return {"items": items}


def _refresh_pricing_cache() -> None:
    """Refresh the pricing actions cache (called from background thread)."""
    import time as _time

    try:
        result = _compute_pricing_actions()
        _pricing_actions_cache["data"] = result
        _pricing_actions_cache["ts"] = _time.time()
        logger.info(
            "Pricing actions cache warmed: %d items", len(result.get("items", []))
        )
    except Exception as exc:
        logger.warning("Background pricing refresh failed: %s", exc)


_pricing_bg_task: asyncio.Task | None = None


async def _pricing_background_loop() -> None:
    """Background loop that refreshes pricing cache every TTL seconds."""
    loop = asyncio.get_event_loop()
    # Initial warm-up (wait a few seconds for other services to initialize)
    await asyncio.sleep(5)
    await loop.run_in_executor(None, _refresh_pricing_cache)
    # Periodic refresh
    while True:
        await asyncio.sleep(_PRICING_ACTIONS_TTL)
        await loop.run_in_executor(None, _refresh_pricing_cache)


async def _insight_precompute_loop() -> None:
    """Background loop to precompute DB insight snapshots for fast online retrieval."""
    loop = asyncio.get_event_loop()
    await asyncio.sleep(8)

    try:
        market_result = await loop.run_in_executor(None, insight_snapshot_service.refresh_snapshot)
        erp_result = await loop.run_in_executor(None, insight_snapshot_service.refresh_erp_snapshot)
        logger.info("Insight snapshot warm-up: market=%s erp=%s", market_result, erp_result)
    except Exception as exc:
        logger.warning("Insight snapshot warm-up failed: %s", exc)

    while True:
        await asyncio.sleep(_INSIGHT_PRECOMPUTE_INTERVAL_SECONDS)
        try:
            market_result = await loop.run_in_executor(None, insight_snapshot_service.refresh_snapshot)
            erp_result = await loop.run_in_executor(None, insight_snapshot_service.refresh_erp_snapshot)
            logger.info("Insight snapshot refresh: market=%s erp=%s", market_result, erp_result)
        except Exception as exc:
            logger.warning("Insight snapshot refresh failed: %s", exc)


async def _kimi_forecast_feature_loop() -> None:
    """Background loop to refresh 16-node Kimi forecast features every 6 hours."""
    loop = asyncio.get_event_loop()
    await asyncio.sleep(12)

    while True:
        try:
            snapshot = await loop.run_in_executor(None, kimi_forecast_feature_service.refresh_snapshot)
            logger.info(
                "Kimi forecast feature snapshot refreshed: id=%s status=%s",
                snapshot.get("snapshot_id"),
                snapshot.get("status"),
            )
        except Exception as exc:
            logger.warning("Kimi forecast feature refresh failed: %s", exc)
        await asyncio.sleep(_KIMI_FORECAST_FEATURE_INTERVAL_SECONDS)


def _forecast_shadow_run_date() -> str:
    return (datetime.utcnow() + timedelta(hours=8)).date().isoformat()


def _forecast_shadow_due_for_today() -> bool:
    now = (datetime.utcnow() + timedelta(hours=8)).replace(second=0, microsecond=0)
    scheduled = now.replace(
        hour=_FORECAST_SHADOW_SELF_IMPROVEMENT_RUN_HOUR,
        minute=_FORECAST_SHADOW_SELF_IMPROVEMENT_RUN_MINUTE,
    )
    return now >= scheduled


def _ensure_forecast_self_improvement_run_table() -> None:
    with db.engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS forecast_self_improvement_runs (
                    id VARCHAR(64) PRIMARY KEY,
                    job_name VARCHAR(120) NOT NULL,
                    run_date VARCHAR(16) NOT NULL,
                    trigger_source VARCHAR(64) NOT NULL,
                    started_at VARCHAR(32) NOT NULL,
                    finished_at VARCHAR(32) NULL,
                    status VARCHAR(32) NOT NULL,
                    candidate_count INTEGER DEFAULT 0,
                    generated_count INTEGER DEFAULT 0,
                    skipped_count INTEGER DEFAULT 0,
                    evaluated_count INTEGER DEFAULT 0,
                    report_json TEXT NULL,
                    error_message TEXT NULL
                )
                """
            )
        )
        for stmt in (
            "CREATE INDEX idx_forecast_self_improvement_job_date ON forecast_self_improvement_runs (job_name, run_date)",
            "CREATE INDEX idx_forecast_self_improvement_status ON forecast_self_improvement_runs (status)",
        ):
            try:
                conn.execute(text(stmt))
            except Exception:
                pass


def _forecast_shadow_success_recorded(run_date: str) -> bool:
    _ensure_forecast_self_improvement_run_table()
    with db.engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT COUNT(*) AS count
                FROM forecast_self_improvement_runs
                WHERE job_name = :job_name
                  AND run_date = :run_date
                  AND status = 'success'
                """
            ),
            {
                "job_name": _FORECAST_SHADOW_SELF_IMPROVEMENT_JOB_NAME,
                "run_date": run_date,
            },
        ).mappings().first()
    return bool(row and int(row.get("count") or 0) > 0)


def _run_forecast_shadow_self_improvement_once(trigger_source: str = "scheduler") -> Dict[str, Any]:
    from scripts.run_forecast_shadow_mode import run_shadow_mode

    _ensure_forecast_self_improvement_run_table()
    run_date = _forecast_shadow_run_date()
    if _forecast_shadow_success_recorded(run_date):
        return {
            "status": "skipped",
            "reason": "already_successful_today",
            "run_date": run_date,
        }

    run_id = uuid4().hex
    started_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    with db.engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO forecast_self_improvement_runs (
                    id, job_name, run_date, trigger_source, started_at, status
                ) VALUES (
                    :id, :job_name, :run_date, :trigger_source, :started_at, 'running'
                )
                """
            ),
            {
                "id": run_id,
                "job_name": _FORECAST_SHADOW_SELF_IMPROVEMENT_JOB_NAME,
                "run_date": run_date,
                "trigger_source": trigger_source,
                "started_at": started_at,
            },
        )

    try:
        report = run_shadow_mode(dry_run=False)
        pending_evaluation = report.get("pending_evaluation") or {}
        finished_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
        with db.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE forecast_self_improvement_runs
                    SET finished_at = :finished_at,
                        status = 'success',
                        candidate_count = :candidate_count,
                        generated_count = :generated_count,
                        skipped_count = :skipped_count,
                        evaluated_count = :evaluated_count,
                        report_json = :report_json,
                        error_message = NULL
                    WHERE id = :id
                    """
                ),
                {
                    "id": run_id,
                    "finished_at": finished_at,
                    "candidate_count": int(report.get("candidate_count") or 0),
                    "generated_count": int(report.get("generated_count") or 0),
                    "skipped_count": int(report.get("skipped_count") or 0),
                    "evaluated_count": int(pending_evaluation.get("evaluated") or 0),
                    "report_json": json.dumps(report, ensure_ascii=False, sort_keys=True),
                },
            )
        return {"status": "success", "run_id": run_id, "run_date": run_date, "report": report}
    except Exception as exc:
        finished_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
        with db.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE forecast_self_improvement_runs
                    SET finished_at = :finished_at,
                        status = 'failed',
                        error_message = :error_message
                    WHERE id = :id
                    """
                ),
                {"id": run_id, "finished_at": finished_at, "error_message": str(exc)},
            )
        raise


async def _forecast_shadow_self_improvement_loop() -> None:
    """Run approved forecast challengers in shadow mode once per Beijing day."""
    loop = asyncio.get_event_loop()
    await asyncio.sleep(_FORECAST_SHADOW_SELF_IMPROVEMENT_STARTUP_DELAY_SECONDS)

    while True:
        try:
            run_date = _forecast_shadow_run_date()
            if _forecast_shadow_due_for_today() and not await loop.run_in_executor(
                None, _forecast_shadow_success_recorded, run_date
            ):
                result = await loop.run_in_executor(
                    None, _run_forecast_shadow_self_improvement_once, "daily_scheduler"
                )
                if result.get("status") == "success":
                    report = result.get("report") or {}
                    logger.info(
                        "Forecast shadow self-improvement completed: candidates=%s generated=%s skipped=%s",
                        report.get("candidate_count"),
                        report.get("generated_count"),
                        report.get("skipped_count"),
                    )
                else:
                    logger.info("Forecast shadow self-improvement skipped: %s", result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Forecast shadow self-improvement failed: %s", exc)

        await asyncio.sleep(_FORECAST_SHADOW_SELF_IMPROVEMENT_CHECK_SECONDS)


@app.get("/api/v2/forecast/self-improvement/status")
async def get_forecast_self_improvement_status():
    try:
        _ensure_forecast_self_improvement_run_table()
    except Exception as exc:
        return {
            "enabled": _forecast_shadow_self_improvement_enabled(),
            "error": f"run ledger unavailable: {exc}",
        }

    latest_runs: List[Dict[str, Any]] = []
    shadow_status_counts: Dict[str, int] = {}
    evaluated_rollup: List[Dict[str, Any]] = []
    with db.engine.connect() as conn:
        try:
            latest_runs = [
                dict(row)
                for row in conn.execute(
                    text(
                        """
                        SELECT run_date, trigger_source, started_at, finished_at, status,
                               candidate_count, generated_count, skipped_count,
                               evaluated_count, error_message
                        FROM forecast_self_improvement_runs
                        WHERE job_name = :job_name
                        ORDER BY started_at DESC
                        LIMIT 10
                        """
                    ),
                    {"job_name": _FORECAST_SHADOW_SELF_IMPROVEMENT_JOB_NAME},
                ).mappings().all()
            ]
        except Exception as exc:
            latest_runs = [{"status": "unavailable", "error_message": str(exc)}]

        try:
            shadow_status_counts = {
                str(row["status"]): int(row["count"])
                for row in conn.execute(
                    text(
                        """
                        SELECT status, COUNT(*) AS count
                        FROM forecast_shadow_predictions
                        GROUP BY status
                        """
                    )
                ).mappings().all()
            }
        except Exception:
            shadow_status_counts = {}

        try:
            evaluated_rollup = [
                dict(row)
                for row in conn.execute(
                    text(
                        """
                        SELECT product_id, horizon_days,
                               COUNT(*) AS evaluated_count,
                               AVG(challenger_improvement_pct) AS avg_improvement_pct,
                               AVG(CASE WHEN challenger_error_pct < champion_error_pct THEN 1 ELSE 0 END) AS win_rate
                        FROM forecast_shadow_predictions
                        WHERE status = 'evaluated'
                        GROUP BY product_id, horizon_days
                        """
                    )
                ).mappings().all()
            ]
        except Exception:
            evaluated_rollup = []

    return {
        "enabled": _background_workers_enabled() and _forecast_shadow_self_improvement_enabled(),
        "job_name": _FORECAST_SHADOW_SELF_IMPROVEMENT_JOB_NAME,
        "timezone": "Asia/Shanghai",
        "run_hour": _FORECAST_SHADOW_SELF_IMPROVEMENT_RUN_HOUR,
        "run_minute": _FORECAST_SHADOW_SELF_IMPROVEMENT_RUN_MINUTE,
        "check_interval_seconds": _FORECAST_SHADOW_SELF_IMPROVEMENT_CHECK_SECONDS,
        "today": _forecast_shadow_run_date(),
        "due_today": _forecast_shadow_due_for_today(),
        "latest_runs": latest_runs,
        "shadow_status_counts": shadow_status_counts,
        "evaluated_rollup": evaluated_rollup,
    }


@app.get("/api/v2/market/forecast-features/status")
async def get_kimi_forecast_feature_status():
    snapshot = kimi_forecast_feature_service.get_latest_snapshot(include_expired=True)
    if not snapshot:
        return {
            "available": False,
            "refresh_interval_seconds": _KIMI_FORECAST_FEATURE_INTERVAL_SECONDS,
            "node_count": 16,
        }
    features = snapshot.get("features") or {}
    products = features.get("products") if isinstance(features, dict) else {}
    return {
        "available": True,
        "snapshot_id": snapshot.get("snapshot_id"),
        "generated_at": snapshot.get("generated_at"),
        "expires_at": snapshot.get("expires_at"),
        "status": snapshot.get("status"),
        "model": snapshot.get("model"),
        "product_count": len(products) if isinstance(products, dict) else 0,
        "refresh_interval_seconds": _KIMI_FORECAST_FEATURE_INTERVAL_SECONDS,
    }


@app.post("/api/v2/market/forecast-features/refresh")
async def refresh_kimi_forecast_features():
    loop = asyncio.get_event_loop()
    try:
        snapshot = await loop.run_in_executor(None, kimi_forecast_feature_service.refresh_snapshot)
        return {
            "ok": True,
            "snapshot_id": snapshot.get("snapshot_id"),
            "generated_at": snapshot.get("generated_at"),
            "expires_at": snapshot.get("expires_at"),
            "status": snapshot.get("status"),
            "model": snapshot.get("model"),
            "error": snapshot.get("error"),
        }
    except Exception as exc:
        logger.error("Kimi forecast feature manual refresh failed: %s", exc)
        raise HTTPException(status_code=500, detail="Forecast feature refresh failed")


@app.post("/api/v2/insights/refresh")
async def refresh_insight_snapshot():
    """Trigger an immediate refresh of precomputed insight snapshot rows."""
    loop = asyncio.get_event_loop()
    try:
        market_result = await loop.run_in_executor(None, insight_snapshot_service.refresh_snapshot)
        erp_result = await loop.run_in_executor(None, insight_snapshot_service.refresh_erp_snapshot)
        return {
            "ok": bool(market_result.get("ok") and erp_result.get("ok")),
            "market": market_result,
            "erp": erp_result,
        }
    except Exception as exc:
        logger.error("Insight snapshot manual refresh failed: %s", exc)
        raise HTTPException(status_code=500, detail="Insight snapshot refresh failed")


@app.post("/api/v2/insights/backfill/local-llm-market")
async def backfill_local_llm_market_snapshots(
    end_date: Optional[str] = None,
    days: int = 3,
    product_ids: Optional[str] = None,
):
    """Write dated market test snapshots with local-LLM narratives into the cache table."""
    if days < 1 or days > 7:
        raise HTTPException(status_code=400, detail="days must be between 1 and 7")

    parsed_end_date: Optional[date] = None
    parsed_product_ids: Optional[List[str]] = None
    if end_date:
        try:
            parsed_end_date = date.fromisoformat(str(end_date).strip()[:10])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="end_date must be YYYY-MM-DD") from exc
    if product_ids:
        parsed_product_ids = [item.strip().lower() for item in str(product_ids).split(",") if item.strip()]
        if not parsed_product_ids:
            raise HTTPException(status_code=400, detail="product_ids must contain at least one product")

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: insight_snapshot_service.backfill_market_local_llm_test(
                llm_service=llm_service,
                end_date=parsed_end_date,
                days=days,
                product_ids=parsed_product_ids,
            ),
        )
        return result
    except Exception as exc:
        logger.error("Local LLM market backfill failed: %s", exc)
        raise HTTPException(status_code=500, detail="Local LLM market backfill failed")


@app.get("/api/v2/insights/bundle")
async def get_insight_bundle(
    product_ids: Optional[str] = None,
    max_age_seconds: Optional[int] = None,
    async_refresh_on_miss: bool = True,
):
    """Fetch latest precomputed insight bundle for external-LLM online narration."""
    parsed_ids = None
    if product_ids:
        parsed_ids = [item.strip() for item in str(product_ids).split(",") if item.strip()]
    return insight_snapshot_service.get_bundle_cached(
        product_ids=parsed_ids,
        max_age_seconds=max_age_seconds,
        async_refresh_on_miss=async_refresh_on_miss,
    )


@app.get("/api/v2/insights/combined")
async def get_combined_insight_bundle(
    product_ids: Optional[str] = None,
    market_max_age_seconds: Optional[int] = None,
    erp_max_age_seconds: Optional[int] = None,
    async_refresh_on_miss: bool = True,
):
    """Fetch latest combined market and ERP insight bundle for unified external narration."""
    parsed_ids = None
    if product_ids:
        parsed_ids = [item.strip() for item in str(product_ids).split(",") if item.strip()]
    return insight_snapshot_service.get_combined_bundle_cached(
        product_ids=parsed_ids,
        market_max_age_seconds=market_max_age_seconds,
        erp_max_age_seconds=erp_max_age_seconds,
        async_refresh_on_miss=async_refresh_on_miss,
    )


@app.post("/api/v2/insights/erp/refresh")
async def refresh_erp_insight_snapshot():
    """Trigger an immediate refresh of ERP precomputed insight snapshot rows."""
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, insight_snapshot_service.refresh_erp_snapshot)
        return result
    except Exception as exc:
        logger.error("ERP insight snapshot manual refresh failed: %s", exc)
        raise HTTPException(status_code=500, detail="ERP insight snapshot refresh failed")


@app.get("/api/v2/insights/erp/bundle")
async def get_erp_insight_bundle(
    product_ids: Optional[str] = None,
    max_age_seconds: Optional[int] = None,
    async_refresh_on_miss: bool = True,
):
    """Fetch latest precomputed ERP insight bundle for low-latency internal context."""
    parsed_ids = None
    if product_ids:
        parsed_ids = [item.strip() for item in str(product_ids).split(",") if item.strip()]
    return insight_snapshot_service.get_erp_bundle_cached(
        product_ids=parsed_ids,
        max_age_seconds=max_age_seconds,
        async_refresh_on_miss=async_refresh_on_miss,
    )


@app.get("/api/v2/pricing/actions/downstream")
async def get_pricing_actions_downstream():
    """Returns pricing action recommendations for all downstream products (pre-cached)."""
    import time

    _ensure_market_runtime_fresh()

    # Background task is started in lifespan, restart if needed
    global _pricing_bg_task
    if _pricing_bg_task is None or _pricing_bg_task.done():
        _pricing_bg_task = asyncio.create_task(_pricing_background_loop())

    now = time.time()
    if (
        _pricing_actions_cache["data"] is not None
        and (now - _pricing_actions_cache["ts"]) < _PRICING_ACTIONS_TTL
    ):
        return _pricing_actions_cache["data"]

    if not _pricing_background_enabled():
        result = _compute_pricing_actions()
        _pricing_actions_cache["data"] = result
        _pricing_actions_cache["ts"] = now
        return result

    # Cache not ready yet — return empty with status so frontend can show loading
    return {"items": [], "computing": True}


# --- Enterprise (C5/C9) KPI & Forecast ---


@app.get("/api/v2/enterprise/summary")
async def get_enterprise_summary():
    _ensure_market_runtime_fresh()
    return {
        "c5": enterprise_data.get_summary("enterprise_c5"),
        "c9": enterprise_data.get_summary("enterprise_c9"),
    }


@app.get("/api/v2/enterprise/history/{product_id}")
async def get_enterprise_history(product_id: str):
    _ensure_market_runtime_fresh()
    if product_id not in ["enterprise_c5", "enterprise_c9"]:
        raise HTTPException(status_code=404, detail="Unknown enterprise product")
    return enterprise_data.get_history(product_id)


@app.get("/api/v2/enterprise/gap/{product_id}")
async def get_enterprise_gap_history(product_id: str):
    _ensure_market_runtime_fresh()
    if product_id not in ["enterprise_c5", "enterprise_c9"]:
        raise HTTPException(status_code=404, detail="Unknown enterprise product")
    return enterprise_data.get_gap_history(product_id)


@app.get("/api/v2/enterprise/coverage/{product_id}")
async def get_enterprise_coverage(product_id: str):
    _ensure_market_runtime_fresh()
    if product_id not in ["enterprise_c5", "enterprise_c9"]:
        raise HTTPException(status_code=404, detail="Unknown enterprise product")
    return enterprise_data.get_coverage(product_id)


@app.get("/api/v2/erp/weekly-plan", response_model=WeeklyPlanResponse)
async def get_weekly_plan_summary():
    _ensure_market_runtime_fresh()
    items = _filter_excluded_product_entries(weekly_plan_service.get_all_product_plans())
    as_of = items[0]["plan_date"] if items else None
    return {"as_of": as_of, "items": items}


@app.get(
    "/api/v2/erp/weekly-plan/{product_id}",
    response_model=WeeklyPlanProductResponse,
)
async def get_product_weekly_plan(product_id: str):
    _ensure_product_allowed(product_id)
    plan = weekly_plan_service.get_product_plan(product_id)
    if not plan:
        raise HTTPException(
            status_code=404,
            detail=f"No weekly plan data available for product: {product_id}",
        )
    return plan


@app.get("/api/v2/enterprise/forecast/{product_id}")
async def get_enterprise_forecast(product_id: str):
    if product_id not in ["enterprise_c5", "enterprise_c9"]:
        raise HTTPException(status_code=404, detail="Unknown enterprise product")
    return enterprise_data.get_forecast(product_id, weeks=4)


def _build_today_briefing() -> dict:
    today = datetime.now().date()
    watch_products = [
        ("naphtha", "石脑油"),
        ("crude_oil", "布伦特原油"),
        ("styrene", "苯乙烯"),
        ("piperylene", "间戊二烯"),
        ("isoprene", "异戊二烯"),
        ("c5_resin", "碳五树脂"),
    ]

    overnight = []
    for pid, name in watch_products:
        hist = market_data.get_price_history(pid)
        if len(hist) < 2:
            continue
        latest = float(hist[-1]["price"])
        prev = float(hist[-2]["price"])
        if prev == 0:
            continue
        pct = (latest - prev) / prev * 100.0
        overnight.append(
            {
                "product_id": pid,
                "name": name,
                "price": round(latest, 2),
                "change_pct": round(pct, 2),
                "direction": "up" if pct > 0 else ("down" if pct < 0 else "flat"),
            }
        )

    overnight.sort(key=lambda x: abs(x["change_pct"]), reverse=True)

    safety_items = _compute_inventory_safety()
    risks = []
    for item in safety_items:
        if item["status"] in {"danger", "warning", "excess"}:
            risks.append(item)

    weekly_expectations = [
        "关注中石化挂牌价窗口与上游成本传导节奏",
        "关注道路标线施工旺季临近对树脂需求的边际抬升",
    ]

    no_attention = []
    stable_candidates = [x for x in overnight if abs(x["change_pct"]) <= 1.0]
    for row in stable_candidates[:3]:
        no_attention.append(f"{row['name']} 处于常态波动区间")

    md_lines = [
        f"EDIA 每日早报 — {today.isoformat()}",
        "",
        "📊 昨夜变动：",
    ]
    if overnight:
        for row in overnight[:4]:
            arrow = (
                "↗"
                if row["change_pct"] > 0
                else ("↘" if row["change_pct"] < 0 else "→")
            )
            md_lines.append(
                f"- {row['name']} {arrow} {row['change_pct']:+.2f}% → {row['price']:,.0f}"
            )
    else:
        md_lines.append("- 暂无足够的隔夜变动数据")

    md_lines.extend(["", "⚠️ 需要关注："])
    if risks:
        for r in risks[:3]:
            md_lines.append(
                f"- {r['name_cn']} 库存 {r['inventory_days']:.1f} 天（{r['status']}），建议复核出货节奏"
            )
    else:
        md_lines.append("- 库存与出货节奏整体稳定")

    abnormal_moves = [x for x in overnight if abs(x["change_pct"]) >= 5.0]
    for row in abnormal_moves[:2]:
        md_lines.append(
            f"- {row['name']} 单日波动 {row['change_pct']:+.2f}%（异常），建议核实最新数据"
        )

    md_lines.extend(["", "📅 本周预期："])
    for item in weekly_expectations:
        md_lines.append(f"- {item}")

    md_lines.extend(["", "✅ 无需关注："])
    if no_attention:
        for item in no_attention:
            md_lines.append(f"- {item}")
    else:
        md_lines.append("- 暂无明显稳定项")

    return {
        "date": today.isoformat(),
        "overnight_changes": overnight[:6],
        "risk_items": risks[:6],
        "weekly_expectations": weekly_expectations,
        "no_attention": no_attention,
        "markdown": "\n".join(md_lines),
    }


@app.get("/api/briefing/today")
async def get_today_briefing():
    try:
        return _build_today_briefing()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Stage 4: Macro Override API  (§16 Macro Override Layer)
# ---------------------------------------------------------------------------


class MacroOverrideRequest(BaseModel):
    naphtha_shock_pct: float  # e.g. +2.5 means naphtha +2.5%  (hard-capped ±4%)
    crude_shock_pct: float = 0.0  # informational crude price change %
    event_type: str = (
        "manual"  # "crude_spike" | "tariff_shock" | "force_majeure" | "manual"
    )
    description: str = ""  # e.g. "Brent +$5 — geopolitical tensions in Middle East"
    ttl_hours: int = 24  # override expires after this many hours
    product_shocks: Optional[Dict[str, float]] = (
        None  # optional per-product pct adjustments
    )


@app.post("/api/macro/override")
async def set_macro_override(req: MacroOverrideRequest):
    """Inject a real-time macro shock — naphtha spike, tariff, force majeure, etc.
    The override immediately propagates through the monthly forecast causal chain
    and the Stage 4 pricing ladder.
    Naphtha shock is hard-capped at ±4% per EDIA spec §16.
    """
    override = _set_override(
        naphtha_shock_pct=req.naphtha_shock_pct,
        crude_shock_pct=req.crude_shock_pct,
        event_type=req.event_type,
        description=req.description,
        ttl_hours=req.ttl_hours,
        product_shocks=req.product_shocks,
    )
    return {
        "status": "override_set",
        "summary": override_summary(override),
        "override": override,
    }


@app.delete("/api/macro/override")
async def clear_macro_override():
    """Manually deactivate the current macro override before it expires."""
    _clear_override()
    return {"status": "override_cleared"}


@app.get("/api/macro/override")
async def get_macro_override():
    """Return the current override status (active dict or null)."""
    active = get_active_override()
    if active:
        return {"active": True, "override": active, "summary": override_summary(active)}
    return {"active": False, "override": None}


# ---------------------------------------------------------------------------
# Feedback API — store decision outcomes for institutional memory (§7 ChromaDB)
# ---------------------------------------------------------------------------


class FeedbackRequest(BaseModel):
    product: str
    date: str  # "2026-03-06"
    actual_price: float  # actual executed price CNY/ton
    volume_sold: float  # tons
    forecast_accuracy: str  # "accurate" | "over" | "under"
    decision_quality: str  # "good" | "acceptable" | "poor"
    notes: str = ""


@app.post("/api/feedback")
async def submit_feedback(req: FeedbackRequest):
    """Log an actual decision outcome. Stored in ChromaDB `decision_outcomes`
    collection for future retrieval by the Diagnosis Agent (institutional memory).
    Currently persists to the DB messages table as a structured audit record.
    """
    try:
        import json as _json

        audit_record = {
            "type": "decision_outcome",
            "product": req.product,
            "date": req.date,
            "actual_price": req.actual_price,
            "volume_sold": req.volume_sold,
            "forecast_accuracy": req.forecast_accuracy,
            "decision_quality": req.decision_quality,
            "notes": req.notes,
        }
        # Persist as an audit record — create (or reuse) a feedback session
        try:
            feedback_session_id = db.create_session("system", "Feedback Audit Log")
            db.add_message(
                feedback_session_id,
                "system",
                f"[FEEDBACK] {_json.dumps(audit_record, ensure_ascii=False)}",
            )
        except Exception:
            pass  # persist is best-effort

        # Also try to add to ChromaDB knowledge base for RAG retrieval
        try:
            from backend.src.rag.vector_store import kb
            import uuid as _uuid

            outcome_text = (
                f"Product: {req.product} | Date: {req.date} | "
                f"Actual price: {req.actual_price:,.0f} CNY/ton | "
                f"Volume: {req.volume_sold:.0f} ton | "
                f"Forecast accuracy: {req.forecast_accuracy} | "
                f"Decision quality: {req.decision_quality}"
                + (f" | Notes: {req.notes}" if req.notes else "")
            )
            kb.add_documents(
                [outcome_text],
                [
                    {
                        "doc_type": "decision_outcome",
                        "product": req.product,
                        "date": req.date,
                    }
                ],
                [f"outcome_{req.product}_{req.date}_{_uuid.uuid4().hex[:8]}"],
            )
        except Exception:
            pass  # RAG persistence is best-effort

        return {"status": "feedback_recorded", "record": audit_record}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/feedback/prompt")
async def get_feedback_prompt(
    product_id: str,
    request: Request,
):
    """Return a one-click execution feedback prompt for last recommendation."""
    try:
        from backend.src.rag.vector_store import kb

        current_user = _require_current_user(request)
        hits = kb.query_recent_decision_for_user(
            product_id=product_id,
            user_id=str(current_user["id"]),
            days=7,
            limit=1,
        )
        if not hits:
            return {
                "has_prompt": False,
                "message": "近7天内暂无该产品的推荐记录。",
            }

        meta = (hits[0] or {}).get("metadata", {}) or {}
        rec = int(meta.get("price_recommended", 0) or 0)
        d = str(meta.get("date", "近一周"))

        message = (
            f"上次（{d}）EDIA建议 {product_id} 调整至 {rec:,} 元/吨。"
            "请反馈实际执行情况："
        )

        return {
            "has_prompt": True,
            "product_id": product_id,
            "recommended_price": rec,
            "decision_date": d,
            "message": message,
            "options": [
                {"id": "followed", "label": "✅ 按建议执行"},
                {"id": "raised_more", "label": "⬆️ 上调幅度更大"},
                {"id": "raised_less", "label": "⬇️ 上调幅度较小"},
                {"id": "not_followed", "label": "❌ 未执行调价"},
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Stage 4: Direct Pricing Decision API
# ---------------------------------------------------------------------------


@app.get("/api/v2/pricing/{product_id}")
async def get_pricing_decision(product_id: str):
    """Returns the full Stage 4 pricing decision for a product.
    Builds a fresh perception snapshot and runs all 4 pipeline stages.
    """
    from backend.src.service.perception_service import perception_service
    from backend.src.service.diagnosis_service import diagnosis_service
    from backend.src.service.pricing_service import pricing_service

    node = next((n for n in domain_graph.nodes if n.id == product_id), None)
    if not node:
        raise HTTPException(status_code=404, detail=f"Unknown product: {product_id}")

    try:
        snapshot = perception_service.build_snapshot()
        diag = diagnosis_service.diagnose(product_id, snapshot)
        fc = forecasting_service.forecast(product_id, diagnosis=diag)
        decision = pricing_service.compute_pricing_decision(
            product_id=product_id,
            perception_snapshot=snapshot,
            diagnosis=diag,
            forecast=fc,
        )
        return decision
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/admin/market/refresh")
async def refresh_market_prices(request: Request, reset: bool = True):
    _require_admin(request)

    try:
        from backend.src.db.load_market_prices import load_all

        loaded_rows = load_all(reset=reset)
        market_data.refresh()
        _clear_db_dependent_runtime_caches()
        _set_market_runtime_fingerprint(_read_market_live_fingerprint())
        return {
            "status": "ok",
            "loaded_rows": loaded_rows,
            "cache_refreshed": True,
            "reset": reset,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Weekly Report Ingestion API  (§5 Build Spec)
# ---------------------------------------------------------------------------


@app.get("/api/data-update/options")
async def get_data_update_options():
    raise HTTPException(status_code=410, detail="Data update has been removed.")


@app.get("/api/data-update/history")
async def get_data_update_history(limit: int = 20):
    raise HTTPException(status_code=410, detail="Data update has been removed.")


@app.post("/api/data-update/upload")
async def upload_data_update_csv(request: Request):
    raise HTTPException(status_code=410, detail="Data update has been removed.")


@app.post("/api/ingest/weekly-report")
async def ingest_weekly_report(file: UploadFile = File(...)):
    """
    Accept an `.md` weekly report upload, parse it into product×week chunks
    with rich metadata, and upsert into the `weekly_reports` ChromaDB collection.

    Returns: { status, filename, chunks_added }
    """
    if not file.filename or not file.filename.endswith(".md"):
        raise HTTPException(status_code=400, detail="Only .md files are accepted.")

    import tempfile
    from pathlib import Path
    from ingest_data import ingest_file

    content = await file.read()
    with tempfile.NamedTemporaryFile(
        suffix=".md",
        prefix=file.filename.rstrip(".md") + "_",
        delete=False,
    ) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        chunks_added = ingest_file(tmp_path, reset_weekly=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {e}")
    finally:
        tmp_path.unlink(missing_ok=True)

    # Phase 6: after ingestion, trigger an immediate alert scan to pick up
    # any new signals from the freshly-ingested report data.
    try:
        alert_worker.scan()
    except Exception:
        pass  # best-effort

    return {
        "status": "ingested",
        "filename": file.filename,
        "chunks_added": chunks_added,
    }


# ---------------------------------------------------------------------------
# Intelligence API — Event-driven override + calibration endpoints
# ---------------------------------------------------------------------------


def _safe_json_list(raw_value: Any) -> list[str]:
    if raw_value is None:
        return []
    if isinstance(raw_value, list):
        return [str(item) for item in raw_value]
    if isinstance(raw_value, str):
        text_value = raw_value.strip()
        if not text_value:
            return []
        try:
            parsed = json.loads(text_value)
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except Exception:
            return [text_value]
    return []


@app.get("/api/events/latest")
async def get_latest_events(
    hours: int = 24,
    review_status: str = "approved",
    min_usefulness_score: int = 0,
):
    """Return recent events detected by the intelligence pipeline."""
    from backend.src.intelligence.event_registry import intelligence_event_registry

    normalized_status = (review_status or "approved").strip().lower()
    status_filter = None if normalized_status == "all" else normalized_status
    rows = intelligence_event_registry.list_recent_event_rows(
        hours=hours,
        review_status=status_filter,
        min_usefulness_score=max(0, min(5, int(min_usefulness_score))),
        limit=100,
    )

    events = [
        {
            "event_id": row.get("event_id"),
            "event_type": row.get("event_type"),
            "headline": row.get("headline"),
            "affected_commodities": _safe_json_list(row.get("affected_commodities")),
            "direction": row.get("direction"),
            "magnitude_estimate": row.get("magnitude_estimate"),
            "certainty": row.get("certainty"),
            "time_horizon": row.get("time_horizon"),
            "geographic_scope": row.get("geographic_scope"),
            "relevance_to_c5_c9": row.get("relevance_to_c5_c9"),
            "key_entities": _safe_json_list(row.get("key_entities")),
            "causal_chain_hint": row.get("causal_chain_hint"),
            "source_url": row.get("source_url"),
            "source_credibility": row.get("source_credibility"),
            "full_text": row.get("full_text"),
            "raw_quote": row.get("raw_quote"),
            "detected_at": row.get("detected_at"),
            "impact_magnitude": row.get("impact_magnitude"),
            "relevance_score": row.get("relevance_score"),
            "review_status": row.get("review_status") or "approved",
            "usefulness_score": int(row.get("usefulness_score") or 0),
            "reviewed_by": row.get("reviewed_by") or "",
            "reviewed_at": row.get("reviewed_at") or "",
            "review_note": row.get("review_note") or "",
        }
        for row in rows
    ]

    return {
        "events": events,
        "count": len(events),
    }


@app.get("/api/events/high-impact")
async def get_high_impact_events(
    hours: int = 4,
    review_status: str = "approved",
    min_usefulness_score: int = 0,
):
    """Return high-impact events from the last N hours."""
    from backend.src.intelligence.event_registry import intelligence_event_registry

    normalized_status = (review_status or "approved").strip().lower()
    status_filter = None if normalized_status == "all" else normalized_status
    rows = intelligence_event_registry.list_recent_event_rows(
        hours=hours,
        min_impact=0.50,
        review_status=status_filter,
        min_usefulness_score=max(0, min(5, int(min_usefulness_score))),
        limit=100,
    )

    events = [
        {
            "event_id": row.get("event_id"),
            "event_type": row.get("event_type"),
            "headline": row.get("headline"),
            "affected_commodities": _safe_json_list(row.get("affected_commodities")),
            "direction": row.get("direction"),
            "magnitude_estimate": row.get("magnitude_estimate"),
            "certainty": row.get("certainty"),
            "time_horizon": row.get("time_horizon"),
            "geographic_scope": row.get("geographic_scope"),
            "relevance_to_c5_c9": row.get("relevance_to_c5_c9"),
            "key_entities": _safe_json_list(row.get("key_entities")),
            "causal_chain_hint": row.get("causal_chain_hint"),
            "source_url": row.get("source_url"),
            "source_credibility": row.get("source_credibility"),
            "full_text": row.get("full_text"),
            "raw_quote": row.get("raw_quote"),
            "detected_at": row.get("detected_at"),
            "impact_magnitude": row.get("impact_magnitude"),
            "relevance_score": row.get("relevance_score"),
            "review_status": row.get("review_status") or "approved",
            "usefulness_score": int(row.get("usefulness_score") or 0),
            "reviewed_by": row.get("reviewed_by") or "",
            "reviewed_at": row.get("reviewed_at") or "",
            "review_note": row.get("review_note") or "",
        }
        for row in rows
    ]

    return {
        "events": events,
        "count": len(events),
    }


class EventReviewRequest(BaseModel):
    review_status: str = Field(default="approved")
    usefulness_score: Optional[int] = Field(default=None, ge=0, le=5)
    review_note: str = ""


class EventRemoveRequest(BaseModel):
    review_note: str = ""


@app.post("/api/events/{event_id}/review")
async def review_event(event_id: str, payload: EventReviewRequest, request: Request):
    """Review one ingested event (approve/reject/usefulness score)."""
    admin_user = _require_admin(request)
    from backend.src.intelligence.event_registry import intelligence_event_registry

    try:
        updated = intelligence_event_registry.review_event(
            event_id,
            review_status=payload.review_status,
            usefulness_score=payload.usefulness_score,
            reviewed_by=str(admin_user.get("username") or admin_user.get("id") or "admin"),
            review_note=payload.review_note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not updated:
        raise HTTPException(status_code=404, detail=f"Event not found: {event_id}")

    return {
        "status": "updated",
        "event_id": event_id,
        "review_status": payload.review_status,
        "usefulness_score": payload.usefulness_score,
    }


@app.post("/api/events/{event_id}/remove")
async def remove_event(event_id: str, payload: EventRemoveRequest, request: Request):
    """Soft-remove one event from decision paths (marks review_status=rejected)."""
    admin_user = _require_admin(request)
    from backend.src.intelligence.event_registry import intelligence_event_registry

    removed = intelligence_event_registry.remove_event(
        event_id,
        reviewed_by=str(admin_user.get("username") or admin_user.get("id") or "admin"),
        review_note=payload.review_note,
    )
    if not removed:
        raise HTTPException(status_code=404, detail=f"Event not found: {event_id}")

    return {
        "status": "removed",
        "event_id": event_id,
        "review_status": "rejected",
    }


@app.get("/api/events/moderation/queue")
async def get_event_moderation_queue(
    request: Request,
    hours: int = 168,
    review_status: str = "all",
    min_usefulness_score: int = 0,
    limit: int = 200,
):
    """Dashboard-facing moderation queue for reviewing ingested events."""
    _require_admin(request)
    from backend.src.intelligence.event_registry import intelligence_event_registry

    normalized_status = (review_status or "all").strip().lower()
    status_filter = None if normalized_status == "all" else normalized_status
    rows = intelligence_event_registry.list_recent_event_rows(
        hours=max(1, int(hours)),
        review_status=status_filter,
        min_usefulness_score=max(0, min(5, int(min_usefulness_score))),
        limit=max(1, min(1000, int(limit))),
    )

    events = [
        {
            "event_id": row.get("event_id"),
            "detected_at": row.get("detected_at"),
            "headline": row.get("headline"),
            "event_type": row.get("event_type"),
            "key_entities": _safe_json_list(row.get("key_entities")),
            "causal_chain_hint": row.get("causal_chain_hint") or "",
            "raw_quote": row.get("raw_quote") or "",
            "website_key_info": (row.get("raw_quote") or row.get("headline") or "").strip(),
            "source_url": row.get("source_url"),
            "impact_magnitude": row.get("impact_magnitude"),
            "review_status": row.get("review_status") or "approved",
            "usefulness_score": int(row.get("usefulness_score") or 0),
            "reviewed_by": row.get("reviewed_by") or "",
            "reviewed_at": row.get("reviewed_at") or "",
            "review_note": row.get("review_note") or "",
        }
        for row in rows
    ]

    return {
        "events": events,
        "count": len(events),
        "filters": {
            "hours": max(1, int(hours)),
            "review_status": normalized_status,
            "min_usefulness_score": max(0, min(5, int(min_usefulness_score))),
        },
    }


@app.get("/api/events/review/summary")
async def get_event_review_summary(request: Request, hours: int = 168):
    """Return moderation/usefulness summary for recent ingested events."""
    _require_admin(request)
    from backend.src.intelligence.event_registry import intelligence_event_registry

    return intelligence_event_registry.get_review_summary(hours=max(1, int(hours)))


@app.get("/api/events/pending-review")
async def get_pending_review_events():
    """Return events pending human review for override injection."""
    if not _intel_override_engine:
        return {"pending": [], "message": "Override engine not initialized"}
    return {"pending": _intel_override_engine.get_pending_reviews()}


@app.post("/api/events/{event_id}/approve-override")
async def approve_event_override(event_id: str):
    """Approve a pending override — inject it into the macro override."""
    if not _intel_override_engine:
        raise HTTPException(status_code=503, detail="Override engine not initialized")
    decision = _intel_override_engine.approve_pending(event_id)
    if not decision:
        raise HTTPException(
            status_code=404, detail=f"No pending review for event {event_id}"
        )
    return {"status": "approved_and_injected", "decision": decision.to_dict()}


@app.post("/api/events/{event_id}/reject-override")
async def reject_event_override(event_id: str):
    """Reject a pending override — discard it."""
    if not _intel_override_engine:
        raise HTTPException(status_code=503, detail="Override engine not initialized")
    decision = _intel_override_engine.reject_pending(event_id)
    if not decision:
        raise HTTPException(
            status_code=404, detail=f"No pending review for event {event_id}"
        )
    return {"status": "rejected", "decision": decision.to_dict()}


@app.get("/api/events/auto-injected")
async def get_auto_injected_events():
    """Return events that were auto-injected as overrides."""
    if not _intel_override_engine:
        return {"auto_injected": [], "message": "Override engine not initialized"}
    return {"auto_injected": _intel_override_engine.get_auto_injected()}


class EventOutcomeRequest(BaseModel):
    event_type: str
    predicted_naphtha_pct: float
    actual_naphtha_pct: float
    resolution_days: int
    resolution_type: str = "gradual"  # "rapid" | "gradual" | "structural"
    notes: str = ""


@app.post("/api/events/{event_id}/outcome")
async def record_event_outcome(event_id: str, req: EventOutcomeRequest):
    """Record the actual outcome of an event for learning loop calibration."""
    if not _intel_learning_loop:
        raise HTTPException(status_code=503, detail="Learning loop not initialized")
    outcome = _intel_learning_loop.record_event_outcome(
        event_id=event_id,
        event_type=req.event_type,
        predicted_naphtha_pct=req.predicted_naphtha_pct,
        actual_naphtha_pct=req.actual_naphtha_pct,
        resolution_days=req.resolution_days,
        resolution_type=req.resolution_type,
        notes=req.notes,
    )
    return {"status": "outcome_recorded", "outcome": outcome}


@app.get("/api/intelligence/calibration")
async def get_intelligence_calibration():
    """Return calibration summary and recent outcomes."""
    if not _intel_learning_loop:
        return {"message": "Learning loop not initialized", "calibration": {}}
    return {"calibration": _intel_learning_loop.get_calibration_summary()}


@app.get("/api/intelligence/context")
async def get_intelligence_context_api(hours: int = 4):
    """Return the full intelligence context (events + causal + scenarios + overrides)."""
    try:
        ctx = get_intelligence_context(hours=hours)
        return ctx
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/intelligence/status")
async def get_intelligence_status_api(request: Request):
    """Return operational status for the always-on intelligence ingestion service."""
    _require_admin(request)

    stale_after_hours_raw = (os.getenv("NEWS_INGESTION_STALE_AFTER_HOURS", "2") or "2").strip()
    try:
        stale_after_hours = max(1, int(stale_after_hours_raw))
    except ValueError:
        stale_after_hours = 2

    status = {
        "initialized": bool(_intel_news_agent),
        "llm_mode": LLM_MODE,
        "background_workers_enabled": _background_workers_enabled(),
        "database_only_mode": _database_only_mode(),
        "news_agent_enabled": _env_flag("ENABLE_NEWS_INGESTION_AGENT", "false"),
        "stale_after_hours": stale_after_hours,
        "agent": None,
    }
    if _intel_news_agent:
        status["agent"] = _intel_news_agent.get_status()

    return status


@app.post("/api/intelligence/run-once")
async def run_intelligence_cycle_api(request: Request):
    """Run one manual ingestion cycle immediately for verification or recovery."""
    admin_user = _require_admin(request)
    if not _intel_news_agent:
        raise HTTPException(status_code=503, detail="News agent not initialized")

    try:
        events = await asyncio.to_thread(_intel_news_agent.run_news_cycle, "manual_api")
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "status": "completed",
        "triggered_by": str(admin_user.get("username") or admin_user.get("id") or "admin"),
        "event_count": len(events),
        "headlines": [
            str(getattr(event, "headline", "") or "") for event in events[:10]
        ],
        "agent": _intel_news_agent.get_status(),
    }


class ManualEventRequest(BaseModel):
    headline: str
    event_type: str = "manual"
    direction: str = "up"
    magnitude_estimate: str = "medium"
    certainty: str = "likely"
    geographic_scope: str = "china"
    affected_commodities: List[str] = []


@app.post("/api/events/manual")
async def ingest_manual_event(req: ManualEventRequest):
    """Manually inject an event into the intelligence pipeline."""
    if not _intel_news_agent:
        raise HTTPException(status_code=503, detail="News agent not initialized")
    event = _intel_news_agent.ingest_manual_event(req.model_dump())
    return {
        "status": "event_ingested",
        "event": event.to_dict() if hasattr(event, "to_dict") else event,
    }


# ---------------------------------------------------------------------------
# Report endpoints — Customer Sales & Contract Performance
# ---------------------------------------------------------------------------

from sqlalchemy import text as sa_text
from backend.src.db.engine_factory import get_erp_engine

_REPORT_DB_URL = os.getenv("DATABASE_URL")
if not _REPORT_DB_URL:
    raise RuntimeError("DATABASE_URL must be set for cloud DB mode.")
_report_engine = get_erp_engine()


def _norm_row(row_mapping) -> dict:
    """Normalise a MySQL row: convert None values to empty string.

    Downstream report code already does ``float(r.get('x') or 0)`` for
    numeric fields, so mapping None → '' is safe and consistent with what
    csv.DictReader used to return (all values are strings).
    """
    normalized = {}
    for key, value in dict(row_mapping).items():
        if value is None:
            normalized[key] = ""
        elif isinstance(value, datetime):
            normalized[key] = value.isoformat(sep=" ")
        elif isinstance(value, date):
            normalized[key] = value.isoformat()
        else:
            normalized[key] = value
    return normalized


def _ensure_material_mapping():
    """Create _ref_material_mapping if it doesn't exist yet.

    The warehouse database includes a material master table. Use it as the
    authoritative FMATERIALID → material mapping so report labels do not
    depend on brittle joins against contract amounts.
    """
    with _report_engine.connect() as conn:
        exists = conn.execute(
            sa_text(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = '_ref_material_mapping'"
            )
        ).scalar()
        if not exists:
            conn.execute(
                sa_text(
                    """
                CREATE TABLE _ref_material_mapping (
                    FMATERIALID BIGINT PRIMARY KEY,
                    material_code VARCHAR(64),
                    material_name VARCHAR(200),
                    material_unit VARCHAR(50),
                    product_type VARCHAR(100)
                )
            """
                )
            )
        else:
            conn.execute(
                sa_text(
                    """
                ALTER TABLE _ref_material_mapping
                    MODIFY material_code VARCHAR(64) NULL,
                    MODIFY material_name VARCHAR(200) NULL,
                    MODIFY material_unit VARCHAR(50) NULL,
                    MODIFY product_type VARCHAR(100) NULL
            """
                )
            )
        conn.execute(
            sa_text(
                """
            INSERT INTO _ref_material_mapping
                (FMATERIALID, material_code, material_name, material_unit, product_type)
            SELECT
                CAST(m.material_id AS UNSIGNED) AS FMATERIALID,
                NULLIF(TRIM(m.material_code), '') AS material_code,
                NULLIF(TRIM(m.material_name), '') AS material_name,
                NULLIF(TRIM(m.material_unit), '') AS material_unit,
                NULL
            FROM material m
            WHERE m.material_id REGEXP '^[0-9]+$'
            ON DUPLICATE KEY UPDATE
                material_code = VALUES(material_code),
                material_name = VALUES(material_name),
                material_unit = VALUES(material_unit)
        """
            )
        )
        conn.commit()


_REPORT_ORG_TABLE_MAP: Dict[str, Dict[str, str]] = {
    "huizhou": {
        "label": "Huizhou Isco",
        "table": "erp_v_sale_huizhou",
    },
    "guangdong": {
        "label": "Guangdong Isco",
        "table": "erp_v_sale_guangdong",
    },
}

_REPORT_ORG_ALIASES: Dict[str, str] = {
    "huizhou": "huizhou",
    "huizhou isco": "huizhou",
    "erp_v_sale_huizhou": "huizhou",
    "guangdong": "guangdong",
    "guangdong isco": "guangdong",
    "erp_v_sale_guangdong": "guangdong",
}


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _resolve_report_org(org: str = "huizhou") -> Dict[str, str]:
    normalized = str(org or "huizhou").strip().lower()
    org_key = _REPORT_ORG_ALIASES.get(normalized)
    if not org_key:
        allowed = ", ".join(sorted(_REPORT_ORG_TABLE_MAP.keys()))
        raise HTTPException(
            status_code=400,
            detail=f"Unknown organization '{org}'. Allowed values: {allowed}",
        )
    config = _REPORT_ORG_TABLE_MAP[org_key]
    return {
        "key": org_key,
        "label": config["label"],
        "table": config["table"],
    }


_CONTRACT_EXECUTION_SQL = """
WITH delivery_agg AS (
    SELECT
        olk.FSID AS contract_entry_id,
        SUM(dlk.FBASEUNITQTY) AS out_fqty,
        SUM(def.FALLAMOUNT_LC) AS out_amount
    FROM erp_t_sal_orderentry_lk olk
    JOIN erp_t_sal_deliverynoticeentry_lk dlk
        ON dlk.FSID = olk.FENTRYID
        AND dlk.FSTABLENAME = 'T_SAL_ORDERENTRY'
    JOIN erp_t_sal_deliverynoticeentry_f def
        ON def.FENTRYID = dlk.FENTRYID
    WHERE olk.FSTABLENAME = 'T_CRM_CONTRACTENTRY'
    GROUP BY olk.FSID
),
order_agg AS (
    SELECT
        olk.FSID AS contract_entry_id,
        SUM(olk.FBASEUNITQTY) AS order_qty,
        SUM(COALESCE(vso.FALLAMOUNT, 0)) AS order_amount,
        SUM(CASE WHEN COALESCE(vso.FTAXPRICE, 0) > 0 THEN 1 ELSE 0 END) AS ftaxprice_hit_count,
        SUM(CASE WHEN COALESCE(vso.FTAXPRICE, 0) <= 0 AND COALESCE(vso.FPRICE, 0) > 0 THEN 1 ELSE 0 END) AS fprice_hit_count,
        CASE
            WHEN SUM(olk.FBASEUNITQTY) > 0
                THEN SUM(COALESCE(vso.FTAXPRICE, vso.FPRICE, 0) * COALESCE(olk.FBASEUNITQTY, 0)) / SUM(olk.FBASEUNITQTY)
            ELSE 0
        END AS order_tax_unit_price
    FROM erp_t_sal_orderentry_lk olk
    JOIN erp_v_sale_orderentry vso ON vso.FENTRYID = olk.FENTRYID
    WHERE olk.FSTABLENAME = 'T_CRM_CONTRACTENTRY'
    GROUP BY olk.FSID
)
SELECT
    '惠州伊斯科' AS ORG_NAME,
    a.FID,
    a.FDATE,
    a.FVALISTARTDATE,
    a.FVALIENDDATE,
    b.FDELIVERYDATE,
    b.FENTRYID AS contract_entry_id,
    a.FBILLNO,
    a.FNAME,
    cu.FNAME AS CUST_NAME,
    a.FHEADDELIVERYWAY,
    CASE a.F_PAEZ_CPLX
        WHEN '5d18b3234263f2' THEN '液体'
        WHEN '5d18b3374263f4' THEN '固体'
        ELSE NULL
    END AS product_type,
    m.material_code,
    m.material_name,
    m.material_unit,
    '' AS currency,
    CASE b.F_PAEZ_ASSISTANT2_WY_DJ
        WHEN '617a12d4f418f1' THEN '优级品'
        WHEN '617a12e0f418f3' THEN '一等品'
        WHEN '5b500876e018b9' THEN '合格品'
        WHEN '630434ce03ebdb' THEN '低值品'
        ELSE NULL
    END AS grade,
    b.FQTY AS contract_fqty,
    f.FALLAMOUNT_LC AS contract_amount,
    GREATEST(b.FQTY - IFNULL(oa.order_qty, 0), 0) AS contract_notdofqty,
    IFNULL(oa.order_qty, 0) AS ORDERQTY,
    IFNULL(oa.order_amount, 0) AS ORDERAMOUNT,
    IFNULL(oa.ftaxprice_hit_count, 0) AS ftaxprice_hit_count,
    IFNULL(oa.fprice_hit_count, 0) AS fprice_hit_count,
    IFNULL(oa.order_tax_unit_price, 0) AS order_tax_unit_price,
    0 AS plan_fqty,
    IFNULL(da.out_fqty, 0) AS out_fqty,
    IFNULL(da.out_amount, 0) AS out_amount,
    GREATEST(b.FQTY - IFNULL(da.out_fqty, 0), 0) AS out_notoutqty
FROM erp_t_crm_contract a
JOIN erp_t_crm_contractentry b ON b.FID = a.FID
JOIN erp_t_crm_contractentry_f f ON f.FENTRYID = b.FENTRYID
LEFT JOIN erp_t_bd_customer cu ON cu.FCUSTID = a.FBDCUSTID
LEFT JOIN _ref_material_mapping m ON m.FMATERIALID = b.FMATERIALID
LEFT JOIN delivery_agg da ON da.contract_entry_id = b.FENTRYID
LEFT JOIN order_agg oa ON oa.contract_entry_id = b.FENTRYID
WHERE a.FDOCUMENTSTATUS = 'C'
"""


def _load_contract_execution(org: str = "huizhou"):
    """Load organization-specific sale data and normalize it for report aggregation."""
    org_meta = _resolve_report_org(org)
    table_name = org_meta["table"]

    with _report_engine.connect() as conn:
        result = conn.execute(sa_text(f"SELECT * FROM {_quote_identifier(table_name)}"))
        source_rows = [_norm_row(row._mapping) for row in result]

    normalized_rows: List[Dict[str, object]] = []
    for idx, row in enumerate(source_rows, start=1):
        row_lower = {str(k).strip().lower(): v for k, v in row.items()}

        def pick(*candidates: str, default: object = "") -> object:
            for candidate in candidates:
                value = row_lower.get(candidate.strip().lower())
                if value not in (None, ""):
                    return value
            return default

        out_qty = _safe_float(
            pick("fdeliqty", "qty", "out_fqty", "fqty_origin", "contract_fqty")
        )
        contract_qty = _safe_float(
            pick("fqty_origin", "contract_fqty", "qty", "fdeliqty")
        )
        not_out_qty = _safe_float(
            pick("fremainoutqty", "out_notoutqty", "contract_notdofqty")
        )
        if not_out_qty <= 0 and contract_qty > 0:
            not_out_qty = max(contract_qty - out_qty, 0.0)

        amount = _safe_float(
            pick("amount", "fallamount", "out_amount", "contract_amount", "famount")
        )
        tax_price = _safe_float(pick("ftaxprice", "price"))
        plain_price = _safe_float(pick("fprice"))
        unit_price = tax_price if tax_price > 0 else plain_price
        if unit_price <= 0 and out_qty > 0 and amount > 0:
            unit_price = amount / out_qty

        bill_no = str(pick("fbillno", "bill_no", default=f"{org_meta['key']}-{idx}"))
        fdate = str(pick("date", "plandate", "fdate", "fdeliverydate", default=""))
        fdelivery_date = str(pick("fdeliverydate", "date", "plandate", "fdate", default=fdate))
        material_name = str(pick("material_name", "product", default="")).strip()
        customer_name = str(
            pick("cust_name", "customer", "partner_name", default="未知")
        ).strip()
        product_type = str(
            pick("material_group", "product_type", "material_type", default="")
        ).strip()
        grade = str(pick("material_property", "grade", "product_grade", default="")).strip()

        normalized_rows.append(
            {
                "ORG_NAME": org_meta["label"],
                "FID": pick("fid", default=""),
                "FDATE": fdate,
                "FVALISTARTDATE": str(pick("fvalistartdate", default="")),
                "FVALIENDDATE": str(pick("fvalienddate", default="")),
                "FDELIVERYDATE": fdelivery_date,
                "contract_entry_id": pick("fentryid", "contract_entry_id", default=""),
                "FBILLNO": bill_no,
                "FNAME": str(pick("fname", default="销售记录")),
                "CUST_NAME": customer_name,
                "FHEADDELIVERYWAY": str(pick("fheaddeliveryway", default="")),
                "product_type": product_type,
                "material_code": pick("material_code", default=""),
                "material_name": material_name,
                "material_unit": str(pick("material_unit", "unit", default="吨")),
                "currency": str(pick("currency", default="")),
                "grade": grade,
                "contract_fqty": contract_qty,
                "contract_amount": amount,
                "contract_notdofqty": not_out_qty,
                "ORDERQTY": contract_qty,
                "ORDERAMOUNT": amount,
                "ftaxprice_hit_count": 1 if tax_price > 0 else 0,
                "fprice_hit_count": 1 if tax_price <= 0 and plain_price > 0 else 0,
                "order_tax_unit_price": unit_price,
                "plan_fqty": _safe_float(pick("plan_qty")),
                "out_fqty": out_qty,
                "out_amount": amount,
                "out_notoutqty": not_out_qty,
            }
        )

    return normalized_rows, org_meta


def _load_sale_orderentry():
    """Load erp_v_sale_orderentry from MySQL into a list of dicts."""
    with _report_engine.connect() as conn:
        result = conn.execute(sa_text("SELECT * FROM erp_v_sale_orderentry"))
        return [_norm_row(row._mapping) for row in result]


def _load_product_grade():
    """Load erp_v_product_grade from MySQL into a list of dicts."""
    with _report_engine.connect() as conn:
        result = conn.execute(sa_text("SELECT * FROM erp_v_product_grade"))
        return [_norm_row(row._mapping) for row in result]


def _week_label(date_str: str) -> str:
    """Convert a date string to a week label like '6.1-6.7'."""
    try:
        dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
        start = dt - timedelta(days=dt.weekday())
        end = start + timedelta(days=6)
        return f"{start.month}.{start.day}-{end.month}.{end.day}"
    except Exception:
        return "unknown"


def _week_monday(date_str: str) -> str:
    """Return the Monday date string for sorting weeks chronologically."""
    try:
        dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
        start = dt - timedelta(days=dt.weekday())
        return start.strftime("%Y-%m-%d")
    except Exception:
        return "9999-12-31"


def _row_sales_amount(row: Dict[str, object]) -> float:
    """Compute sales amount with consistent unit-price semantics.

    Priority:
            1) shipped qty * sales-order tax unit price (erp_v_sale_orderentry.FTAXPRICE/FPRICE)
      2) shipment amount (delivery amount)
      3) fallback contract amount for legacy rows
    """
    qty = float(row.get("out_fqty") or 0)
    # Sales amount should only come from shipped quantity rows.
    if qty <= 0:
        return 0.0

    unit_price = float(row.get("order_tax_unit_price") or 0)
    if qty > 0 and unit_price > 0:
        return qty * unit_price

    out_amount = float(row.get("out_amount") or 0)
    if out_amount > 0:
        return out_amount

    return float(row.get("contract_amount") or 0)


def _row_amount_source(row: Dict[str, object]) -> str:
    """Describe which amount source is used for a row."""
    qty = float(row.get("out_fqty") or 0)
    if qty <= 0:
        return "ZERO_QTY_SKIPPED"
    unit_price = float(row.get("order_tax_unit_price") or 0)
    if qty > 0 and unit_price > 0:
        return "QTY_X_UNIT_PRICE"
    out_amount = float(row.get("out_amount") or 0)
    if out_amount > 0:
        return "OUT_AMOUNT"
    return "CONTRACT_AMOUNT"


def _row_unit_price_source(row: Dict[str, object]) -> str:
    """Describe whether FTAXPRICE or FPRICE drove the unit price."""
    ftax_hits = int(float(row.get("ftaxprice_hit_count") or 0))
    fprice_hits = int(float(row.get("fprice_hit_count") or 0))
    if ftax_hits > 0 and fprice_hits > 0:
        return "MIXED_FTAXPRICE_FPRICE"
    if ftax_hits > 0:
        return "FTAXPRICE"
    if fprice_hits > 0:
        return "FPRICE"
    return "UNAVAILABLE"


@app.get("/api/v2/reports/customer-sales")
async def get_customer_sales_report(
    month: str = "", material: str = "", debug: bool = False, org: str = "huizhou"
):
    """
    Customer sales report aggregated from contract execution data.
    month: e.g. '2025-06' or '2025-01'. If empty, uses latest available month.
    material: e.g. '碳五石油树脂'. If empty, includes all.
    """
    _ensure_market_runtime_fresh()
    rows, org_meta = _load_contract_execution(org)

    # Determine available months
    all_months = sorted(set(r["FDATE"][:7] for r in rows if r.get("FDATE")))
    if not month or month not in all_months:
        month = all_months[-1] if all_months else "2025-01"

    # Filter by month
    filtered = [r for r in rows if r.get("FDATE", "")[:7] == month]

    # Filter by material if specified
    if material:
        filtered = [r for r in filtered if r.get("material_name", "") == material]

    # Available materials for the filter dropdown
    available_materials = sorted(
        set(r.get("material_name", "") for r in rows if r.get("FDATE", "")[:7] == month)
    )

    # Calculate KPIs
    total_sales_qty = sum(float(r.get("out_fqty") or 0) for r in filtered)
    total_contract_amount = sum(_row_sales_amount(r) for r in filtered)
    total_sales_amount_wan = total_contract_amount / 10000  # Convert to 万元

    # Previous year same month for YoY
    try:
        year, mon = month.split("-")
        prev_month = f"{int(year) - 1}-{mon}"
    except Exception:
        prev_month = ""
    prev_filtered = [r for r in rows if r.get("FDATE", "")[:7] == prev_month]
    if material:
        prev_filtered = [
            r for r in prev_filtered if r.get("material_name", "") == material
        ]
    prev_qty = sum(float(r.get("out_fqty") or 0) for r in prev_filtered)
    prev_amount = sum(_row_sales_amount(r) for r in prev_filtered)
    sales_qty_yoy = (
        round((total_sales_qty - prev_qty) / prev_qty * 100, 1) if prev_qty > 0 else 0
    )
    sales_amount_yoy = (
        round((total_contract_amount - prev_amount) / prev_amount * 100, 1)
        if prev_amount > 0
        else 0
    )

    # Customer share (top 5 + others)
    from collections import Counter, defaultdict

    cust_qty: Dict[str, float] = defaultdict(float)
    for r in filtered:
        cust_qty[r.get("CUST_NAME", "未知")] += float(r.get("out_fqty") or 0)
    sorted_custs = sorted(cust_qty.items(), key=lambda x: -x[1])
    top_custs = sorted_custs[:5]
    other_qty = sum(v for _, v in sorted_custs[5:])
    customer_share = [{"name": name, "value": round(qty, 2)} for name, qty in top_custs]
    if other_qty > 0:
        customer_share.append({"name": "其他", "value": round(other_qty, 2)})

    # Product breakdown by material_name + product_type
    product_key = lambda r: f"{r.get('material_name', '')}"
    prod_qty: Dict[str, float] = defaultdict(float)
    prev_prod_qty: Dict[str, float] = defaultdict(float)
    for r in filtered:
        prod_qty[product_key(r)] += float(r.get("out_fqty") or 0)
    for r in prev_filtered:
        prev_prod_qty[product_key(r)] += float(r.get("out_fqty") or 0)
    product_growth = []
    for pname, qty in sorted(prod_qty.items(), key=lambda x: -x[1]):
        pq = prev_prod_qty.get(pname, 0)
        yoy = round((qty - pq) / pq * 100, 1) if pq > 0 else 0
        ptype = ""
        for r in filtered:
            if product_key(r) == pname:
                ptype = r.get("product_type", "")
                break
        product_growth.append(
            {
                "product": f"{pname}{'，' + ptype if ptype else ''}",
                "qty": round(qty, 2),
                "unit": "吨",
                "yoy": yoy,
            }
        )

    # Weekly sales detail table
    week_data: Dict[str, Dict[str, Dict[str, float]]] = defaultdict(
        lambda: defaultdict(lambda: {"qty": 0.0, "amount": 0.0})
    )
    week_monday_map: Dict[str, str] = {}  # label -> monday date for sorting
    for r in filtered:
        wk = _week_label(r.get("FDATE", ""))
        monday = _week_monday(r.get("FDATE", ""))
        week_monday_map[wk] = monday
        pname = product_key(r)
        cust = r.get("CUST_NAME", "未知")
        key = f"{pname}|{cust}"
        qty = float(r.get("out_fqty") or 0)
        week_data[key][wk]["qty"] += qty
        week_data[key][wk]["amount"] += _row_sales_amount(r)
    weeks_sorted = sorted(week_monday_map.keys(), key=lambda w: week_monday_map[w])

    sales_detail = []
    for key, wk_vals in sorted(week_data.items()):
        pname, cust = key.split("|", 1)
        row_data = {"product": pname, "customer": cust, "weeks": []}
        for wk in weeks_sorted:
            v = wk_vals.get(wk, {"qty": 0, "amount": 0})
            qty = round(v["qty"], 2)
            price = round(v["amount"] / v["qty"], 2) if v["qty"] > 0 else 0
            row_data["weeks"].append({"week": wk, "qty": qty, "price": price})
        sales_detail.append(row_data)

    # Weekly price chart data (aggregate avg price per week per product)
    weekly_prices: Dict[str, Dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for r in filtered:
        wk = _week_label(r.get("FDATE", ""))
        pname = product_key(r)
        amt = _row_sales_amount(r)
        qty = float(r.get("out_fqty") or 0)
        if qty > 0:
            weekly_prices[pname][wk].append(amt / qty)

    price_chart = []
    for wk in weeks_sorted:
        point: Dict[str, object] = {"week": wk}
        for pname in sorted(prod_qty.keys()):
            prices = weekly_prices[pname].get(wk, [])
            point[pname] = round(sum(prices) / len(prices), 2) if prices else None
        price_chart.append(point)

    # Weekly sales volume chart
    weekly_sales_chart: Dict[str, Dict[str, float]] = defaultdict(
        lambda: defaultdict(float)
    )
    for r in filtered:
        wk = _week_label(r.get("FDATE", ""))
        pname = product_key(r)
        weekly_sales_chart[wk][pname] += float(r.get("out_fqty") or 0)

    sales_chart = []
    for wk in weeks_sorted:
        point: Dict[str, object] = {"week": wk}
        for pname in sorted(prod_qty.keys()):
            point[pname] = round(weekly_sales_chart[wk].get(pname, 0), 2)
        sales_chart.append(point)

    response = {
        "month": month,
        "available_months": all_months[-24:],
        "available_materials": available_materials,
        "org_name": org_meta["label"],
        "summary": {
            "total_sales_qty": round(total_sales_qty, 2),
            "total_sales_amount_wan": round(total_sales_amount_wan, 2),
            "sales_qty_yoy": sales_qty_yoy,
            "sales_amount_yoy": sales_amount_yoy,
        },
        "customer_share": customer_share,
        "product_growth": product_growth,
        "weeks": weeks_sorted,
        "price_chart": price_chart,
        "sales_chart": sales_chart,
        "sales_detail": sales_detail[:50],
        "products": sorted(prod_qty.keys()),
    }

    if debug:
        price_source_counts: Dict[str, int] = Counter()
        amount_source_counts: Dict[str, int] = Counter()
        debug_rows = []
        for r in filtered:
            price_source_counts[_row_unit_price_source(r)] += 1
            amount_source_counts[_row_amount_source(r)] += 1

        for r in filtered[:200]:
            qty = round(float(r.get("out_fqty") or 0), 4)
            unit_price = round(float(r.get("order_tax_unit_price") or 0), 4)
            debug_rows.append(
                {
                    "contract_entry_id": r.get("contract_entry_id"),
                    "bill_no": r.get("FBILLNO", ""),
                    "date": (r.get("FDATE") or "")[:10],
                    "product": r.get("material_name", ""),
                    "customer": r.get("CUST_NAME", ""),
                    "qty": qty,
                    "unit_price": unit_price,
                    "unit_price_source": _row_unit_price_source(r),
                    "amount": round(_row_sales_amount(r), 4),
                    "amount_source": _row_amount_source(r),
                }
            )

        response["debug"] = {
            "price_source_counts": dict(price_source_counts),
            "amount_source_counts": dict(amount_source_counts),
            "sample_rows": debug_rows,
            "sample_limit": 200,
        }

    return response


@app.get("/api/v2/reports/contract-performance")
async def get_contract_performance_report(
    start_date: str = "", end_date: str = "", material: str = "", org: str = "huizhou"
):
    """
    Contract performance report aggregated from contract execution data.
    start_date/end_date: e.g. '2025-01-01'/'2025-08-31'. If empty, uses latest 6 months.
    material: filter by material_name.
    """
    _ensure_market_runtime_fresh()
    rows, org_meta = _load_contract_execution(org)
    from collections import defaultdict

    # Default date range: latest 1 month
    all_dates = sorted(r["FDATE"][:10] for r in rows if r.get("FDATE"))
    if not start_date or not end_date:
        if all_dates:
            end_dt = datetime.strptime(all_dates[-1], "%Y-%m-%d")
            start_dt = end_dt - timedelta(days=30)
            start_date = start_dt.strftime("%Y-%m-%d")
            end_date = end_dt.strftime("%Y-%m-%d")
        else:
            start_date = "2025-01-01"
            end_date = "2025-08-31"

    # Filter by date range
    filtered = [r for r in rows if start_date <= r.get("FDATE", "")[:10] <= end_date]
    if material:
        filtered = [r for r in filtered if r.get("material_name", "") == material]

    available_materials = sorted(
        set(
            r.get("material_name", "")
            for r in rows
            if start_date <= r.get("FDATE", "")[:10] <= end_date
        )
    )

    # KPIs
    total_contract_amount = sum(float(r.get("contract_amount") or 0) for r in filtered)
    total_contract_qty = sum(float(r.get("contract_fqty") or 0) for r in filtered)
    total_out_qty = sum(float(r.get("out_fqty") or 0) for r in filtered)
    total_out_amount = sum(float(r.get("out_amount") or 0) for r in filtered)
    total_not_out_qty = max(total_contract_qty - total_out_qty, 0)
    num_contracts = len(set(r.get("FBILLNO", "") for r in filtered))
    unfulfilled_contracts = len(
        set(
            r.get("FBILLNO", "")
            for r in filtered
            if float(r.get("out_notoutqty") or 0) > 0
        )
    )
    completion_rate = (
        round(total_out_qty / total_contract_qty * 100, 2)
        if total_contract_qty > 0
        else 0
    )
    invoice_amount_wan = total_out_amount / 10000
    uninvoiced_amount_wan = (total_contract_amount - total_out_amount) / 10000

    # YoY for KPIs
    try:
        sd = datetime.strptime(start_date, "%Y-%m-%d")
        ed = datetime.strptime(end_date, "%Y-%m-%d")
        prev_start = (sd - timedelta(days=365)).strftime("%Y-%m-%d")
        prev_end = (ed - timedelta(days=365)).strftime("%Y-%m-%d")
    except Exception:
        prev_start = prev_end = ""
    prev_filtered = [
        r for r in rows if prev_start <= r.get("FDATE", "")[:10] <= prev_end
    ]
    if material:
        prev_filtered = [
            r for r in prev_filtered if r.get("material_name", "") == material
        ]
    prev_contract_amount = sum(
        float(r.get("contract_amount") or 0) for r in prev_filtered
    )
    prev_contract_qty = sum(float(r.get("contract_fqty") or 0) for r in prev_filtered)
    prev_out_qty = sum(float(r.get("out_fqty") or 0) for r in prev_filtered)
    prev_out_amount = sum(float(r.get("out_amount") or 0) for r in prev_filtered)
    prev_not_out_qty = max(prev_contract_qty - prev_out_qty, 0)
    prev_unfulfilled = len(
        set(
            r.get("FBILLNO", "")
            for r in prev_filtered
            if float(r.get("out_notoutqty") or 0) > 0
        )
    )
    prev_completion_rate = (
        round(prev_out_qty / prev_contract_qty * 100, 2) if prev_contract_qty > 0 else 0
    )
    prev_invoice_wan = prev_out_amount / 10000
    prev_uninvoiced_wan = (prev_contract_amount - prev_out_amount) / 10000

    def _yoy(cur, prev):
        return round((cur - prev) / abs(prev) * 100, 1) if prev != 0 else 0

    amount_yoy = _yoy(total_contract_amount, prev_contract_amount)
    unfulfilled_yoy = _yoy(unfulfilled_contracts, prev_unfulfilled)
    completion_rate_yoy = _yoy(completion_rate, prev_completion_rate)
    contract_qty_yoy = _yoy(total_contract_qty, prev_contract_qty)
    out_qty_yoy = _yoy(total_out_qty, prev_out_qty)
    not_out_qty_yoy = _yoy(total_not_out_qty, prev_not_out_qty)
    invoice_yoy = _yoy(invoice_amount_wan, prev_invoice_wan)
    uninvoiced_yoy = _yoy(uninvoiced_amount_wan, prev_uninvoiced_wan)
    qty_yoy = out_qty_yoy  # backward compat

    # Unshipped contracts > 30 tons
    unshipped_raw = []
    seen_entries = set()
    for r in filtered:
        notout = float(r.get("out_notoutqty") or 0)
        if notout >= 30:
            bill = r.get("FBILLNO", "")
            mat = str(r.get("material_name", "") or "").strip()
            ptype = str(r.get("product_type", "") or "").strip()
            grade = str(r.get("grade", "") or "").strip()
            entry_key = f"{bill}_{mat}_{ptype}_{grade}"
            if entry_key in seen_entries:
                continue
            seen_entries.add(entry_key)
            parts = [mat]
            if ptype:
                parts.append(ptype)
            if grade:
                parts.append(grade)
            unshipped_raw.append(
                {
                    "product": "\uff0c".join(parts),
                    "product_type": ptype,
                    "bill_no": bill,
                    "customer": r.get("CUST_NAME", ""),
                    "delivery_date": r.get("FDELIVERYDATE", ""),
                    "contract_qty": round(float(r.get("contract_fqty") or 0), 2),
                    "unshipped_qty": round(notout, 2),
                }
            )
    unshipped_list = sorted(unshipped_raw, key=lambda x: -x["unshipped_qty"])[:15]
    # Re-sort for grouped display: by product, then descending unshipped_qty
    unshipped_list.sort(key=lambda x: (x["product"], -x["unshipped_qty"]))

    # Per-product execution stats — grouped by material_name + product_type + grade
    prod_stats: Dict[str, dict] = defaultdict(
        lambda: {"contract_qty": 0.0, "out_qty": 0.0}
    )
    for r in filtered:
        mat = str(r.get("material_name", "") or "").strip()
        ptype = str(r.get("product_type", "") or "").strip()
        grade = str(r.get("grade", "") or "").strip()
        if not mat:
            continue
        parts = [mat]
        if ptype:
            parts.append(ptype)
        if grade:
            parts.append(grade)
        label = "，".join(parts)
        prod_stats[label]["contract_qty"] += float(r.get("contract_fqty") or 0)
        prod_stats[label]["out_qty"] += float(r.get("out_fqty") or 0)

    execution_chart = []
    for pname, stats in sorted(prod_stats.items(), key=lambda x: -x[1]["contract_qty"]):
        exec_rate = (
            round(stats["out_qty"] / stats["contract_qty"] * 100, 1)
            if stats["contract_qty"] > 0
            else 0
        )
        execution_chart.append(
            {
                "product": pname,
                "contract_qty": round(stats["contract_qty"], 2),
                "out_qty": round(stats["out_qty"], 2),
                "execution_rate": exec_rate,
            }
        )

    return {
        "start_date": start_date,
        "end_date": end_date,
        "available_materials": available_materials,
        "org_name": org_meta["label"],
        "summary": {
            "contract_amount_yi": round(total_contract_amount / 100000000, 2),
            "unfulfilled_orders": unfulfilled_contracts,
            "completion_rate": completion_rate,
            "contract_qty": round(total_contract_qty, 2),
            "out_qty": round(total_out_qty, 2),
            "not_out_qty": round(total_not_out_qty, 2),
            "invoice_amount_wan": round(invoice_amount_wan, 2),
            "uninvoiced_amount_wan": round(uninvoiced_amount_wan, 2),
            "amount_yoy": amount_yoy,
            "qty_yoy": qty_yoy,
            "unfulfilled_yoy": unfulfilled_yoy,
            "completion_rate_yoy": completion_rate_yoy,
            "contract_qty_yoy": contract_qty_yoy,
            "out_qty_yoy": out_qty_yoy,
            "not_out_qty_yoy": not_out_qty_yoy,
            "invoice_yoy": invoice_yoy,
            "uninvoiced_yoy": uninvoiced_yoy,
        },
        "unshipped_contracts": unshipped_list,
        "execution_chart": execution_chart,
        "ai_analysis": None,
    }


@app.get("/api/v2/reports/contract-performance/ai-analysis")
async def get_contract_performance_ai_analysis(
    start_date: str = "",
    end_date: str = "",
    material: str = "",
    stream: bool = False,
    org: str = "huizhou",
):
    """
    Separate endpoint for AI analysis — called after main report data loads.
    Returns only the AI-generated analysis texts.
    If stream=true, returns SSE partial updates so UI can render incrementally.
    """
    _ensure_market_runtime_fresh()
    rows, org_meta = _load_contract_execution(org)
    from collections import defaultdict

    # Default date range: latest 1 month
    all_dates = sorted(r["FDATE"][:10] for r in rows if r.get("FDATE"))
    if not start_date or not end_date:
        if all_dates:
            end_dt = datetime.strptime(all_dates[-1], "%Y-%m-%d")
            start_dt = end_dt - timedelta(days=30)
            start_date = start_dt.strftime("%Y-%m-%d")
            end_date = end_dt.strftime("%Y-%m-%d")
        else:
            start_date = "2025-01-01"
            end_date = "2025-08-31"

    # Cache check — same date range returns instantly without LLM calls
    cache_key = (org_meta["key"], start_date, end_date, material or "")
    cached = _ai_analysis_cache.get(cache_key)
    if cached and (time.time() - cached["ts"]) < _AI_ANALYSIS_CACHE_TTL:
        if stream:

            async def _cached_events():
                yield _sse("meta", {"status": "cached"})
                yield _sse("done", cached["data"])

            return StreamingResponse(
                _cached_events(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
            )
        return cached["data"]

    filtered = [r for r in rows if start_date <= r.get("FDATE", "")[:10] <= end_date]
    if material:
        filtered = [r for r in filtered if r.get("material_name", "") == material]

    # Rebuild lightweight stats for LLM context
    total_contract_amount = sum(float(r.get("contract_amount") or 0) for r in filtered)
    total_contract_qty = sum(float(r.get("contract_fqty") or 0) for r in filtered)
    total_out_qty = sum(float(r.get("out_fqty") or 0) for r in filtered)
    total_out_amount = sum(float(r.get("out_amount") or 0) for r in filtered)
    total_not_out_qty = max(total_contract_qty - total_out_qty, 0)
    unfulfilled_contracts = len(
        set(
            r.get("FBILLNO", "")
            for r in filtered
            if float(r.get("out_notoutqty") or 0) > 0
        )
    )
    completion_rate = (
        round(total_out_qty / total_contract_qty * 100, 2)
        if total_contract_qty > 0
        else 0
    )

    # YoY
    try:
        sd = datetime.strptime(start_date, "%Y-%m-%d")
        ed = datetime.strptime(end_date, "%Y-%m-%d")
        prev_start = (sd - timedelta(days=365)).strftime("%Y-%m-%d")
        prev_end = (ed - timedelta(days=365)).strftime("%Y-%m-%d")
    except Exception:
        prev_start = prev_end = ""
    prev_filtered = [
        r for r in rows if prev_start <= r.get("FDATE", "")[:10] <= prev_end
    ]
    if material:
        prev_filtered = [
            r for r in prev_filtered if r.get("material_name", "") == material
        ]
    prev_contract_amount = sum(
        float(r.get("contract_amount") or 0) for r in prev_filtered
    )
    prev_completion_rate = round(
        sum(float(r.get("out_fqty") or 0) for r in prev_filtered)
        / max(sum(float(r.get("contract_fqty") or 0) for r in prev_filtered), 1)
        * 100,
        2,
    )

    def _yoy(cur, prev):
        return round((cur - prev) / abs(prev) * 100, 1) if prev != 0 else 0

    amount_yoy = _yoy(total_contract_amount, prev_contract_amount)
    completion_rate_yoy = _yoy(completion_rate, prev_completion_rate)
    unfulfilled_yoy = _yoy(
        unfulfilled_contracts,
        len(
            set(
                r.get("FBILLNO", "")
                for r in prev_filtered
                if float(r.get("out_notoutqty") or 0) > 0
            )
        ),
    )

    # Execution chart
    prod_stats: Dict[str, dict] = defaultdict(
        lambda: {"contract_qty": 0.0, "out_qty": 0.0}
    )
    for r in filtered:
        mat = str(r.get("material_name", "") or "").strip()
        if not mat:
            continue
        parts = [mat]
        ptype = str(r.get("product_type", "") or "").strip()
        grade = str(r.get("grade", "") or "").strip()
        if ptype:
            parts.append(ptype)
        if grade:
            parts.append(grade)
        label = "，".join(parts)
        prod_stats[label]["contract_qty"] += float(r.get("contract_fqty") or 0)
        prod_stats[label]["out_qty"] += float(r.get("out_fqty") or 0)
    execution_chart = []
    for pname, stats in sorted(prod_stats.items(), key=lambda x: -x[1]["contract_qty"]):
        exec_rate = (
            round(stats["out_qty"] / stats["contract_qty"] * 100, 1)
            if stats["contract_qty"] > 0
            else 0
        )
        execution_chart.append(
            {
                "product": pname,
                "contract_qty": round(stats["contract_qty"], 2),
                "out_qty": round(stats["out_qty"], 2),
                "execution_rate": exec_rate,
            }
        )

    # Unshipped
    unshipped_raw = []
    seen_entries = set()
    for r in filtered:
        notout = float(r.get("out_notoutqty") or 0)
        if notout >= 30:
            bill = r.get("FBILLNO", "")
            mat = str(r.get("material_name", "") or "").strip()
            ptype = str(r.get("product_type", "") or "").strip()
            grade = str(r.get("grade", "") or "").strip()
            entry_key = f"{bill}_{mat}_{ptype}_{grade}"
            if entry_key in seen_entries:
                continue
            seen_entries.add(entry_key)
            parts = [mat]
            if ptype:
                parts.append(ptype)
            if grade:
                parts.append(grade)
            unshipped_raw.append(
                {
                    "product": "\uff0c".join(parts),
                    "product_type": ptype,
                    "bill_no": bill,
                    "customer": r.get("CUST_NAME", ""),
                    "delivery_date": r.get("FDELIVERYDATE", ""),
                    "contract_qty": round(float(r.get("contract_fqty") or 0), 2),
                    "unshipped_qty": round(notout, 2),
                }
            )
    unshipped_list = sorted(unshipped_raw, key=lambda x: -x["unshipped_qty"])[:15]

    import json as _json

    _report_data_context = {
        "org_name": org_meta["label"],
        "start_date": start_date,
        "end_date": end_date,
        "contract_amount_yi": round(total_contract_amount / 100000000, 2),
        "unfulfilled_orders": unfulfilled_contracts,
        "completion_rate": completion_rate,
        "contract_qty": round(total_contract_qty, 2),
        "out_qty": round(total_out_qty, 2),
        "not_out_qty": round(total_not_out_qty, 2),
        "invoice_amount_wan": round(total_out_amount / 10000, 2),
        "uninvoiced_amount_wan": round(
            (total_contract_amount - total_out_amount) / 10000, 2
        ),
        "amount_yoy": amount_yoy,
        "completion_rate_yoy": completion_rate_yoy,
        "unfulfilled_yoy": unfulfilled_yoy,
        "execution_chart": execution_chart[:10],
        "unshipped_top5": unshipped_list[:5],
    }
    _data_str = _json.dumps(_report_data_context, ensure_ascii=False, indent=2)

    _chart_system = (
        "你是一位资深化工行业业务分析师，擅长从销售合同执行数据中提炼业务洞察。"
        "请根据提供的各产品合同执行数据，生成简洁、专业的中文分析文本。"
        "要求：1）基于实际数据说话，引用具体数字；2）指出执行率最高和最低的产品；"
        "3）分析已出数量与合同数量的差距；4）给出业务建议。"
        '返回JSON格式：{"chart_top": "图表上方的概述分析(1-2句)", "chart_bottom": "图表下方的趋势总结(1-2句)"}'
    )
    _chart_user = (
        f"以下是{start_date}至{end_date}期间各产品合同执行情况数据：\n{_data_str}"
    )

    _summary_system = (
        "你是一位资深化工企业运营分析师。请根据报表全部核心数据，生成销售合同执行情况的全局总结分析。"
        "要求：1）用一段话概述整体履约风险（引用具体产品名、数量、日期）；"
        "2）再列出5条核心业务洞察要点(bullets)，每条1-2句，涵盖液体vs固体产品对比、同比变化、"
        "库存压力、完成率评估、高风险合同预警；3）语言专业简洁，适合管理层阅读。"
        '返回JSON格式：{"paragraph": "总结段落", "bullets": ["要点1", "要点2", ...]}'
    )
    _summary_user = f"以下是{start_date}至{end_date}期间{org_meta['label']}销售合同执行报表完整数据：\n{_data_str}"

    _unshipped_system = (
        "你是一位供应链与物流分析师。请根据未出库30吨以上的合同明细数据，"
        "生成1-2句简洁的风险分析文本，指出哪些产品积压最严重、涉及多少份合同、"
        "未出数量范围、交货日期集中情况，并给出紧急建议。"
        '返回JSON格式：{"analysis": "分析文本"}'
    )
    _unshipped_user = f"未出库30吨以上的合同明细：\n{_json.dumps(unshipped_list[:10], ensure_ascii=False, indent=2)}"

    ai_analysis = {
        "chart_top": "",
        "chart_bottom": "",
        "summary_paragraph": "",
        "summary_bullets": [],
        "unshipped_analysis": "",
    }

    async def _run_chart_llm():
        try:
            result = await asyncio.to_thread(
                llm_service.generate_json, _chart_system, _chart_user
            )
            ai_analysis["chart_top"] = result.get("chart_top", "")
            ai_analysis["chart_bottom"] = result.get("chart_bottom", "")
        except Exception as e:
            print(f"[AI Analysis] Chart analysis failed: {e}")

    async def _run_summary_llm():
        try:
            result = await asyncio.to_thread(
                llm_service.generate_json, _summary_system, _summary_user
            )
            ai_analysis["summary_paragraph"] = result.get("paragraph", "")
            ai_analysis["summary_bullets"] = result.get("bullets", [])
        except Exception as e:
            print(f"[AI Analysis] Summary analysis failed: {e}")

    async def _run_unshipped_llm():
        try:
            result = await asyncio.to_thread(
                llm_service.generate_json, _unshipped_system, _unshipped_user
            )
            ai_analysis["unshipped_analysis"] = result.get("analysis", "")
        except Exception as e:
            print(f"[AI Analysis] Unshipped analysis failed: {e}")

    if stream:

        stream_fn = getattr(llm_service, "generate_completion_stream", None)

        # Plain-text prompts — direct text output, no JSON wrapper
        _ct_sys = (
            "你是一位资深化工行业业务分析师。根据提供的各产品合同执行数据，"
            "用1-2句话生成图表上方的概述分析，要引用具体数字。"
            "直接返回分析文本，不要任何额外格式或前缀。"
        )
        _ct_usr = f"以下是{start_date}至{end_date}期间各产品合同执行情况数据：\n{_data_str}\n请生成图表上方的概述分析（1-2句）。"
        _cb_sys = (
            "你是一位资深化工行业业务分析师。根据提供的各产品合同执行数据，"
            "用1-2句话生成图表下方的趋势总结，指出执行率最高和最低的产品，给出业务建议。"
            "直接返回分析文本，不要任何额外格式或前缀。"
        )
        _cb_usr = f"以下是{start_date}至{end_date}期间各产品合同执行情况数据：\n{_data_str}\n请生成图表下方的趋势总结（1-2句）。"
        _sp_sys = (
            "你是一位资深化工企业运营分析师。根据报表数据，"
            "用一段话概述整体履约风险，要引用具体产品名、数量、日期。"
            "语言专业简洁，适合管理层阅读。直接返回分析文本，不要任何额外格式或前缀。"
        )
        _sp_usr = f"以下是{start_date}至{end_date}期间{org_meta['label']}销售合同执行报表完整数据：\n{_data_str}\n请生成总结段落。"
        _sb_sys = (
            "你是一位资深化工企业运营分析师。根据报表数据，给出5条核心业务洞察要点，"
            "每条1-2句，涵盖液体vs固体产品对比、同比变化、库存压力、完成率评估、高风险合同预警。"
            "每条要点单独占一行，以「•」开头，语言专业简洁，适合管理层阅读。"
            "直接返回要点列表，不要序号、不要其他格式。"
        )
        _sb_usr = f"以下是{start_date}至{end_date}期间{org_meta['label']}销售合同执行报表完整数据：\n{_data_str}\n请生成5条核心业务洞察要点。"
        _ua_sys = (
            "你是一位供应链与物流分析师。根据未出库30吨以上的合同明细数据，"
            "用1-2句话指出哪些产品积压最严重、涉及多少份合同、未出数量范围、"
            "交货日期集中情况，并给出紧急建议。"
            "直接返回分析文本，不要任何额外格式或前缀。"
        )
        _ua_usr = f"未出库30吨以上的合同明细：\n{_json.dumps(unshipped_list[:10], ensure_ascii=False, indent=2)}\n请生成分析文本（1-2句）。"

        async def _stream_events():
            yield _sse("meta", {"status": "started"})

            if not callable(stream_fn):
                # Fallback: run all at once then emit done
                await asyncio.gather(
                    _run_chart_llm(), _run_summary_llm(), _run_unshipped_llm()
                )
                _ai_analysis_cache[cache_key] = {
                    "data": dict(ai_analysis),
                    "ts": time.time(),
                }
                yield _sse("done", ai_analysis)
                return

            # ── Parallel streaming ────────────────────────────────────────────────
            # All 5 LLM calls run simultaneously in separate threads.
            # Tokens from each thread are pushed onto a shared asyncio.Queue so the
            # SSE generator can yield them as they arrive — ~5x faster than sequential.
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue = asyncio.Queue()
            NUM_WORKERS = 5

            def _sync_text_worker(sys_p: str, usr_p: str, field: str) -> None:
                tokens: list[str] = []
                try:
                    for delta in stream_fn(sys_p, usr_p):
                        if delta:
                            tokens.append(delta)
                            asyncio.run_coroutine_threadsafe(
                                queue.put(
                                    _sse("delta", {"field": field, "text": delta})
                                ),
                                loop,
                            ).result()
                    ai_analysis[field] = "".join(tokens)
                except Exception as e:
                    print(f"[AI Parallel] {field} error: {e}")
                finally:
                    asyncio.run_coroutine_threadsafe(queue.put(None), loop).result()

            def _sync_bullets_worker() -> None:
                parts: list[str] = []
                try:
                    for delta in stream_fn(_sb_sys, _sb_usr):
                        if delta:
                            parts.append(delta)
                    text = "".join(parts)
                    bullets = [
                        b.lstrip("•·-●\uff65 ").strip()
                        for b in text.split("\n")
                        if b.strip()
                    ]
                    ai_analysis["summary_bullets"] = bullets
                    asyncio.run_coroutine_threadsafe(
                        queue.put(_sse("partial", {"summary_bullets": bullets})), loop
                    ).result()
                except Exception as e:
                    print(f"[AI Parallel] summary_bullets error: {e}")
                finally:
                    asyncio.run_coroutine_threadsafe(queue.put(None), loop).result()

            # Start all workers concurrently
            gather_task = asyncio.gather(
                asyncio.to_thread(_sync_text_worker, _ct_sys, _ct_usr, "chart_top"),
                asyncio.to_thread(_sync_text_worker, _cb_sys, _cb_usr, "chart_bottom"),
                asyncio.to_thread(
                    _sync_text_worker, _sp_sys, _sp_usr, "summary_paragraph"
                ),
                asyncio.to_thread(
                    _sync_text_worker, _ua_sys, _ua_usr, "unshipped_analysis"
                ),
                asyncio.to_thread(_sync_bullets_worker),
            )

            # Drain queue; None is the per-worker sentinel
            done_count = 0
            while done_count < NUM_WORKERS:
                item = await queue.get()
                if item is None:
                    done_count += 1
                else:
                    yield item

            await gather_task

            # Save completed result to cache so next load is instant
            _ai_analysis_cache[cache_key] = {
                "data": dict(ai_analysis),
                "ts": time.time(),
            }
            yield _sse("done", ai_analysis)

        return StreamingResponse(
            _stream_events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    await asyncio.gather(_run_chart_llm(), _run_summary_llm(), _run_unshipped_llm())

    return ai_analysis


# ===========================================================================================
# PHASE 1: CONFIDENCE SCORING, AUDIT TRAIL, RISK ALERTS, BACKTEST VALIDATION
# ===========================================================================================


@app.get("/api/phase1/forecast/with-confidence")
async def fetch_forecast_with_confidence(product_id: str) -> dict:
    """
    Return a forecast with detailed confidence scoring and uncertainty ranges.

    Example:
        GET /api/phase1/forecast/with-confidence?product_id=Cracked%20C5

    Returns:
        {
            "product_id": "Cracked C5",
            "base_price": 5000,
            "confidence": {
                "overall": 0.78,  # HIGH
                "data_quality": 0.95,
                "evidence_strength": 0.72,
                "model_stability": 0.75,
                "macro_uncertainty": 0.35,
                "p25": 4950,      # 25th percentile
                "p50": 5000,      # Median
                "p75": 5050,      # 75th percentile
                "uncertainty_source": "macro_volatility",
                "confidence_label": "HIGH",
                "lookback_mape": 0.032
            },
            "scenarios": {
                "bull": {"price": 5200, "probability": 0.20},
                "base": {"price": 5000, "probability": 0.65},
                "bear": {"price": 4800, "probability": 0.15}
            }
        }
    """

    try:
        from backend.src.service.perception_service import perception_service
        from backend.src.service.diagnosis_service import diagnosis_service

        # Get real snapshot from perception
        perception_snapshot = perception_service.build_snapshot()

        # Get real diagnosis
        diag = diagnosis_service.diagnose(product_id, perception_snapshot)

        # Get real forecast
        forecast_result = forecasting_service.forecast(product_id)
        forecast = {
            "base": forecast_result.get("mid", 5000),
            "bull": forecast_result.get("high", 5200),
            "bear": forecast_result.get("low", 4800),
        }

        # Get evidence from diagnosis
        evidence_bundle = {
            "bullish_score": diag.get("bullish_signals_strength", 0.6),
            "bearish_score": diag.get("bearish_signals_strength", 0.4),
            "contradictions": diag.get("contradictions", []),
            "dominant_view": (
                "bullish"
                if diag.get("bullish_signals_strength", 0.5) >= 0.5
                else "bearish"
            ),
        }

        # Get macro context from overrides
        active_override = get_active_override()
        macro_context = {
            "active_overrides": [active_override] if active_override else [],
            "risk_level": diag.get("macro_risk_level", "medium"),
        }

        # Get lookback MAPE from backtest
        lookback_results = backtest_runner.run_backtest_series(
            product_id=product_id,
            start_date=(datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"),
            end_date=datetime.now().strftime("%Y-%m-%d"),
            horizon_days=3,
            step_days=7,
        )
        lookback_mape = lookback_results.get("mape") if lookback_results else None

        # Score confidence with real data
        conf = confidence_scorer.score_forecast(
            product_id=product_id,
            evidence_bundle=evidence_bundle,
            diagnosis=diag,
            forecast=forecast,
            perception_snapshot=perception_snapshot,
            macro_context=macro_context,
            lookback_mape=lookback_mape,
        )

        return {
            "product_id": product_id,
            "base_price": forecast["base"],
            "confidence": {
                "overall": conf.overall,
                "data_quality": conf.data_quality,
                "evidence_strength": conf.evidence_strength,
                "model_stability": conf.model_stability,
                "macro_uncertainty": conf.macro_uncertainty,
                "p25": conf.p25,
                "p50": conf.p50,
                "p75": conf.p75,
                "uncertainty_source": conf.uncertainty_source,
                "confidence_label": conf.confidence_label,
                "lookback_mape": conf.lookback_mape,
            },
            "scenarios": {
                "bull": {
                    "price": forecast.get("bull", forecast["base"] * 1.04),
                    "probability": 0.20,
                    "description": "Supply constraints + demand strength",
                },
                "base": {
                    "price": forecast["base"],
                    "probability": 0.65,
                    "description": "Seasonal patterns + normal macro",
                },
                "bear": {
                    "price": forecast.get("bear", forecast["base"] * 0.96),
                    "probability": 0.15,
                    "description": "Demand weakness + supply surplus",
                },
            },
            "confidence_interpretation": {
                "HIGH": "Use for pricing/trading",
                "MEDIUM": "Watch for signals; consider scenarios",
                "LOW": "Wait for clarity; monitor closely",
            }[conf.confidence_label],
        }

    except Exception as e:
        logger.error(f"Confidence forecast error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/phase1/decision/audit")
async def get_decision_audit_trail(product_id: str, days: int = 7) -> dict:
    """
    Get the decision audit trail: how recommendations changed over days.

    Example:
        GET /api/phase1/decision/audit?product_id=Cracked%20C5&days=7

    Returns:
        {
            "product": "Cracked C5",
            "period": "Last 7 days",
            "audit_trail": [
                {
                    "date": "2026-03-07T10:30:00",
                    "recommendation_price": 4950,
                    "confidence": 0.75,
                    "changes": {},
                    "change_summary": "No changes from prior day"
                },
                {
                    "date": "2026-03-08T10:30:00",
                    "recommendation_price": 4980,
                    "confidence": 0.78,
                    "changes": {
                        "price": {"from": 4950, "to": 4980, "delta": 30, "pct": 0.61},
                        "naphtha_price": {"from": 648, "to": 653}
                    },
                    "change_summary": "↑ Price 4950 → 4980 (+0.61%) | naphtha_price: 648 → 653"
                },
                ...
            ]
        }
    """

    try:
        audit_trail = decision_history.get_audit_trail(product_id, days)

        return {
            "product": product_id,
            "period": f"Last {days} days",
            "audit_trail": audit_trail,
            "interpretation": {
                "stable": "Recommendations haven't changed significantly (trust level: HIGH)",
                "moderate": "Small adjustments day-to-day (trust level: MEDIUM)",
                "volatile": "Large swings day-to-day (trust level: LOW, investigate)",
            },
        }

    except Exception as e:
        logger.error(f"Audit trail error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/phase1/risk/alerts")
async def get_risk_alerts(product_id: Optional[str] = None) -> dict:
    """
    Get all active risk alerts ranked by severity × probability.

    Example:
        GET /api/phase1/risk/alerts?product_id=Cracked%20C5
        or
        GET /api/phase1/risk/alerts (for all products)

    Returns:
        {
            "total_alerts": 3,
            "critical_count": 1,
            "high_count": 1,
            "medium_count": 1,
            "alerts": [
                {
                    "severity": "🔴 CRITICAL",
                    "category": "macro",
                    "description": "Strait of Hormuz tensions; crude +8%",
                    "impact_on_price": 300,
                    "probability": 0.65,
                    "evidence": "Iran navy activity + insurance premiums spike",
                    "products_affected": ["Cracked C5", "Isoprene", "C9"]
                },
                {
                    "severity": "🟠 HIGH",
                    "category": "supply",
                    "description": "Derong new unit ramping faster than expected",
                    "impact_on_price": -400,
                    "probability": 0.45,
                    "evidence": "Operating rate jumped 12% in 1 week",
                    "products_affected": ["DCPD"]
                },
                ...
            ]
        }
    """

    try:
        from backend.src.service.perception_service import perception_service
        from backend.src.service.diagnosis_service import diagnosis_service

        # Get alerts from alert service
        alerts = []  # Real alerts would come from decision_critic or alert_worker

        # Extract risks from perception and diagnosis
        perception_snapshot = perception_service.build_snapshot()

        for pid in [product_id] if product_id else [n.id for n in domain_graph.nodes]:
            try:
                diag = diagnosis_service.diagnose(pid, perception_snapshot)

                # Extract risks from diagnosis
                for risk_key in ["critical_risks", "risks"]:
                    if risk_key in diag and diag[risk_key]:
                        for risk in diag[risk_key]:
                            if isinstance(risk, dict):
                                # Convert diagnosis risk to alert format
                                severity_map = {
                                    "critical": "🔴 CRITICAL",
                                    "high": "🟠 HIGH",
                                    "medium": "🟡 MEDIUM",
                                    "low": "🟢 LOW",
                                }
                                alert = {
                                    "severity": severity_map.get(
                                        risk.get("severity", "medium"), "🟡 MEDIUM"
                                    ),
                                    "category": risk.get("category", "general"),
                                    "description": risk.get(
                                        "description", "Risk identified"
                                    ),
                                    "impact_on_price": risk.get("impact", 0),
                                    "probability": risk.get("probability", 0.5),
                                    "evidence": risk.get("evidence", ""),
                                    "products_affected": risk.get(
                                        "affected_products", [pid]
                                    ),
                                }
                                alerts.append(alert)
            except Exception as e:
                logger.debug(f"Could not diagnose {pid}: {e}")
                continue

        # Filter by product if requested
        if product_id:
            alerts = [a for a in alerts if product_id in a["products_affected"]]

        # Count by severity
        severity_counts = {
            "critical": len([a for a in alerts if "CRITICAL" in a["severity"]]),
            "high": len([a for a in alerts if "HIGH" in a["severity"]]),
            "medium": len([a for a in alerts if "MEDIUM" in a["severity"]]),
            "low": len([a for a in alerts if "LOW" in a["severity"]]),
        }

        return {
            "product": product_id or "all",
            "status": "ok" if alerts else "no_live_alerts",
            "total_alerts": len(alerts),
            **severity_counts,
            "alerts": sorted(
                alerts,
                key=lambda x: (
                    x["probability"]
                    * (
                        500
                        if "CRITICAL" in x["severity"]
                        else 300 if "HIGH" in x["severity"] else 100
                    )
                ),
                reverse=True,
            ),
            "message": (
                None
                if alerts
                else "No warehouse-backed alerts were available for the requested scope."
            ),
        }

    except Exception as e:
        logger.error(f"Risk alerts error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/phase1/validation/backtest-performance")
async def get_backtest_performance(product_id: str, days_back: int = 90) -> dict:
    """
    Backtest performance: show how well EDIA forecasts matched reality.
    Builds trust by proving accuracy vs human analyst.

    Example:
        GET /api/phase1/validation/backtest-performance?product_id=Cracked%20C5&days_back=90

    Returns:
        {
            "product": "Cracked C5",
            "period": "Last 90 days",
            "accuracy": {
                "mape_3d": 0.032,          # 3.2% mean absolute percentage error
                "mape_7d": 0.048,
                "direction_accuracy": 0.77,  # Did system get up/down right?
                "range_coverage": 0.82,      # Did bull/bear range contain actual?
                "mape_label": "Good"
            },
            "vs_human": {
                "edia_win_rate": 0.68,     # EDIA better than human 68% of time
                "human_win_rate": 0.22,
                "tie_rate": 0.10
            },
            "monthly_breakdown": [
                {"month": "2026-01", "count": 13, "mape": 0.028, "direction_accuracy": 0.80},
                {"month": "2026-02",  "count": 12, "mape": 0.045, "direction_accuracy": 0.73},
            ],
            "worst_miss": {
                "date": "2026-01-06",
                "forecast": 10000,
                "actual": 10890,
                "error_pct": 0.089,
                "reason": "Missed Israel-Hamas geopolitical shock (not in training data)"
            },
            "interpretation": "EDIA is more accurate than experienced human 7 out of 10 times."
        }
    """

    try:
        # Run backtest series
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

        backtest_results = backtest_runner.run_backtest_series(
            product_id=product_id,
            start_date=start_date,
            end_date=end_date,
            horizon_days=7,
            step_days=7,
        )

        if not backtest_results:
            return {
                "product": product_id,
                "status": "unavailable",
                "message": "Historical forecast snapshots are not available; simulated backtests are disabled in warehouse-only mode.",
            }

        return {
            "product": product_id,
            "period": f"Last {days_back} days",
            "status": "ok",
            "accuracy": {
                "mape_7d": backtest_results.get("mape"),
                "mape_label": backtest_results.get("mape_label"),
                "direction_accuracy": backtest_results.get("direction_accuracy"),
                "range_coverage": backtest_results.get("range_coverage"),
            },
            "monthly_breakdown": backtest_results.get("monthly", []),
            "results": backtest_results.get("results", []),
            "total_backtests": backtest_results.get("total_backtests", 0),
        }

    except Exception as e:
        logger.error(f"Backtest performance error: {e}")
        raise HTTPException(status_code=500, detail=str(e))



# ===========================================================================================
# Weekly Report Endpoints
# ===========================================================================================

class WeeklyReportRequest(BaseModel):
    week_start: Optional[str] = None       # "2026-04-21"; defaults to last Monday
    week_end: Optional[str] = None         # "2026-04-27"; defaults to last Sunday
    products: List[str] = Field(default_factory=lambda: ["all"])
    language: str = "zh"


@app.post("/api/reports/weekly")
async def create_weekly_report(payload: WeeklyReportRequest, request: Request):
    """
    Generate a C5/C9 weekly Markdown report (spec §2 POST /api/reports/weekly).
    Runs the full 8-step pipeline: data fetch → chart → LLM narrative → persist.
    """
    current_user = _require_current_user(request)
    user_id = str(current_user["id"])

    try:
        from backend.src.service.weekly_report_generator import generate_weekly_report
        from backend.src.db.market_db import market_db

        llm = getattr(decision_service, "llm", real_llm)

        # Create a dedicated session for this API-triggered report
        week_label = f"{payload.week_start or 'auto'}"
        api_session_id = db.create_session(user_id, f"Weekly Report {week_label}")

        result = await generate_weekly_report(
            db=db,
            mysql_engine=market_db.engine,
            llm=llm,
            week_start_str=payload.week_start,
            week_end_str=payload.week_end,
            language=(payload.language or "zh"),
            user_id=user_id,
            session_id=api_session_id,
            static_base_url="/api/reports-static",
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error("Weekly report generation error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Report generation failed: {e}")


# GET /api/reports/weekly is defined earlier (before /api/reports/{report_id}) to avoid route conflict.


# ===========================================================================================
# ERP Write-Back Endpoints  (§ERP-WRITE-BACK)
# ===========================================================================================


@app.post("/api/erp/prices", response_model=ERPPricingRecommendationResponse)
async def submit_erp_price_recommendation(
    request: Request,
    payload: ERPPricingRecommendationRequest,
):
    """Stage a pricing recommendation for ERP admin review.

    Writes to the SQLite staging table (status=pending).  An ERP admin must
    then approve via PUT /api/erp/prices/{id}/approve before the record is
    committed to ERP MySQL.
    """
    current_user = _require_current_user(request)
    user_id = str(current_user["id"])

    try:
        from backend.src.service.erp_writeback_service import stage_recommendation

        result = stage_recommendation(
            product_id=payload.product_id,
            recommended_price=payload.recommended_price,
            valid_from=payload.valid_from,
            valid_until=payload.valid_until,
            rationale=payload.rationale,
            submitted_by=user_id,
            session_id=payload.session_id,
        )
        return ERPPricingRecommendationResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("ERP price submission error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Submission failed: {e}")


@app.get("/api/erp/prices", response_model=ERPPricingListResponse)
async def list_erp_price_recommendations(
    request: Request,
    product_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
):
    """List ERP pricing recommendations (admin or submitter)."""
    current_user = _require_current_user(request)

    from backend.src.service.erp_writeback_service import list_recommendations

    recommendations = list_recommendations(
        product_id=product_id,
        status=status,
        limit=limit,
    )
    return ERPPricingListResponse(recommendations=recommendations)


@app.get("/api/erp/prices/{rec_id}")
async def get_erp_price_recommendation(request: Request, rec_id: str):
    """Get a single recommendation by ID."""
    _require_current_user(request)

    from backend.src.service.erp_writeback_service import get_recommendation

    rec = get_recommendation(rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    return rec


@app.put("/api/erp/prices/{rec_id}/approve")
async def approve_erp_price_recommendation(
    request: Request,
    rec_id: str,
    new_status: str = "approved",
):
    """Approve, reject, or commit a staged recommendation (admin only)."""
    current_user = _require_current_user(request)
    user_id = str(current_user["id"])

    if not current_user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin role required")

    try:
        from backend.src.service.erp_writeback_service import update_status

        result = update_status(rec_id=rec_id, new_status=new_status, actor_user_id=user_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("ERP recommendation status update error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Update failed: {e}")


# ===========================================================================================
# End of Phase 1 endpoints
# ===========================================================================================


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=3000)
