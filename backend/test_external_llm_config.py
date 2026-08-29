from __future__ import annotations

import os

import pytest

from backend.src.nlu.external_llm_config import (
    _looks_like_kimi_or_moonshot,
    validate_external_llm_env,
)


def test_rejects_moonshot_urls():
    assert _looks_like_kimi_or_moonshot("https://api.moonshot.cn/v1") is True
    assert _looks_like_kimi_or_moonshot("kimi-k2.6") is True
    assert _looks_like_kimi_or_moonshot("https://api.deepseek.com/v1") is False
    assert _looks_like_kimi_or_moonshot("deepseek-chat") is False


def test_validate_accepts_deepseek_env(monkeypatch):
    monkeypatch.setenv("EXTERNAL_LLM_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("EXTERNAL_LLM_MODEL", "deepseek-chat")
    monkeypatch.delenv("KIMI_BASE_URL", raising=False)
    validate_external_llm_env(strict=True)


def test_validate_rejects_kimi_model(monkeypatch):
    monkeypatch.setenv("EXTERNAL_LLM_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("EXTERNAL_LLM_MODEL", "kimi-k2.6")
    with pytest.raises(RuntimeError, match="DeepSeek"):
        validate_external_llm_env(strict=True)
