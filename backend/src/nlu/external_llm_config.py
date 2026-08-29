"""External LLM configuration (DeepSeek only — Kimi/Moonshot disabled)."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_EXTERNAL_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_EXTERNAL_MODEL = "deepseek-chat"
DEFAULT_EXTERNAL_PROVIDER = "deepseek"

_LEGACY_KIMI_ENV_KEYS = (
    "KIMI_BASE_URL",
    "KIMI_MODEL",
    "KIMI_API_KEY",
    "KIMI_PROVIDER",
)


def _env_flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _looks_like_kimi_or_moonshot(value: str) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    if "deepseek" in text:
        return False
    return "moonshot" in text or "kimi-k" in text or text.startswith("kimi-")


def validate_external_llm_env(*, strict: bool = True) -> None:
    """Reject Kimi/Moonshot endpoints; warn on legacy KIMI_* env keys."""
    base = os.getenv("EXTERNAL_LLM_BASE_URL", DEFAULT_EXTERNAL_BASE_URL)
    model = os.getenv("EXTERNAL_LLM_MODEL", DEFAULT_EXTERNAL_MODEL)
    if _looks_like_kimi_or_moonshot(base):
        msg = f"EXTERNAL_LLM_BASE_URL must be DeepSeek, not Kimi/Moonshot: {base!r}"
        if strict:
            raise RuntimeError(msg)
        logger.error(msg)
    if _looks_like_kimi_or_moonshot(model):
        msg = f"EXTERNAL_LLM_MODEL must be DeepSeek, not Kimi: {model!r}"
        if strict:
            raise RuntimeError(msg)
        logger.error(msg)

    legacy_present = [key for key in _LEGACY_KIMI_ENV_KEYS if os.getenv(key)]
    if legacy_present:
        logger.warning(
            "Legacy KIMI_* env vars are set (%s) but ignored — use EXTERNAL_LLM_* only.",
            ", ".join(legacy_present),
        )


def strict_external_llm_only() -> bool:
    if str(os.getenv("STRICT_EXTERNAL_LLM_ONLY", "") or "").strip():
        return _env_flag("STRICT_EXTERNAL_LLM_ONLY", "true")
    if str(os.getenv("STRICT_KIMI_ONLY", "") or "").strip():
        logger.warning("STRICT_KIMI_ONLY is deprecated; use STRICT_EXTERNAL_LLM_ONLY=true")
        return _env_flag("STRICT_KIMI_ONLY", "true")
    return True


def use_external_for_planner() -> bool:
    if _env_flag("USE_EXTERNAL_LLM_FOR_PLANNER", ""):
        return True
    if str(os.getenv("USE_KIMI_FOR_PLANNER", "") or "").strip():
        logger.warning("USE_KIMI_FOR_PLANNER is deprecated; use USE_EXTERNAL_LLM_FOR_PLANNER=true")
        return _env_flag("USE_KIMI_FOR_PLANNER", "false")
    return _env_flag("USE_DEEPSEEK_FOR_PLANNER", "true")


def use_external_for_synthesis() -> bool:
    if _env_flag("USE_EXTERNAL_LLM_FOR_SYNTHESIS", ""):
        return True
    if str(os.getenv("USE_KIMI_FOR_SYNTHESIS", "") or "").strip():
        logger.warning("USE_KIMI_FOR_SYNTHESIS is deprecated; use USE_EXTERNAL_LLM_FOR_SYNTHESIS=true")
        return _env_flag("USE_KIMI_FOR_SYNTHESIS", "false")
    return _env_flag("USE_DEEPSEEK_FOR_SYNTHESIS", "true")
