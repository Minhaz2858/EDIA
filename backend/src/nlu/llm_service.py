import json
import logging
import os
import re
import time
import contextvars
from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Iterator, Optional

import httpx
from openai import OpenAI


logger = logging.getLogger(__name__)

from backend.src.nlu.external_llm_config import (
    DEFAULT_EXTERNAL_BASE_URL,
    DEFAULT_EXTERNAL_MODEL,
    DEFAULT_EXTERNAL_PROVIDER,
    validate_external_llm_env,
)


def _normalize_openai_base_url(url: str) -> str:
    """Ensure OpenAI SDK base URL ends with /v1 (required by DeepSeek and most providers)."""
    cleaned = str(url or "").strip().rstrip("/")
    if not cleaned:
        return DEFAULT_EXTERNAL_BASE_URL
    if cleaned.endswith("/v1"):
        return cleaned
    if "deepseek.com" in cleaned.lower() and not cleaned.endswith("/v1"):
        return f"{cleaned}/v1"
    if cleaned.endswith("/api"):
        return f"{cleaned}/v1"
    return cleaned


def _env_flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


class LLMService(ABC):
    @abstractmethod
    def generate_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        route_hint: Optional[str] = None,
        sanitized: Optional[bool] = None,
    ) -> str:
        pass

    @abstractmethod
    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        route_hint: Optional[str] = None,
        sanitized: Optional[bool] = None,
    ) -> dict:
        pass

    # Optional streaming API. Implementations may provide this.
    def generate_completion_stream(
        self,
        system_prompt: str,
        user_prompt: str,
        route_hint: Optional[str] = None,
        sanitized: Optional[bool] = None,
    ):
        raise NotImplementedError

    @contextmanager
    def request_scope(
        self,
        route_hint: Optional[str] = None,
        sanitized: Optional[bool] = None,
    ):
        yield


class DisabledLLMService(LLMService):
    """
    Disabled placeholder implementation. Any usage is treated as a configuration error.
    """

    def generate_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        route_hint: Optional[str] = None,
        sanitized: Optional[bool] = None,
    ) -> str:
        raise RuntimeError(
            "DisabledLLMService cannot be used. Configure a real LLM backend."
        )

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        route_hint: Optional[str] = None,
        sanitized: Optional[bool] = None,
    ) -> dict:
        raise RuntimeError(
            "DisabledLLMService cannot be used. Configure a real LLM backend."
        )


class RealLLMService(LLMService):
    """
    Real LLM service: local Ollama + external DeepSeek (OpenAI-compatible API).
    """

    def __init__(self):
        validate_external_llm_env(strict=True)
        api_key = os.getenv("LLM_API_KEY")
        base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
        self.model = os.getenv("LLM_MODEL", "gpt-3.5-turbo")
        self.base_url = base_url.rstrip("/")
        self.disable_local_llm = _env_flag("DISABLE_LOCAL_LLM", "false")
        self.provider_name = os.getenv("LLM_PROVIDER", "local")
        if self.base_url.endswith("/api/generate"):
            self.backend_kind = "ollama-generate"
        else:
            self.backend_kind = "openai-compatible"
        self.is_ready = False
        # Global timeout value kept for compatibility/logging.
        self.timeout_sec = float(os.getenv("LLM_TIMEOUT_SEC", "0") or "0")
        self.local_completion_timeout_sec = float(
            os.getenv("LLM_COMPLETION_TIMEOUT_SEC", str(self.timeout_sec)) or "0"
        )
        self.local_json_timeout_sec = float(
            os.getenv("LLM_JSON_TIMEOUT_SEC", str(self.timeout_sec)) or "0"
        )
        self.local_no_think = _env_flag("LLM_LOCAL_NO_THINK", "true")

        default_route = "external" if self.disable_local_llm else "local"
        self.route_hint_var: contextvars.ContextVar[str] = contextvars.ContextVar(
            "llm_route_hint", default=default_route
        )
        self.sanitized_var: contextvars.ContextVar[bool] = contextvars.ContextVar(
            "llm_sanitized_hint", default=False
        )

        self.external_enabled = _env_flag("LLM_ENABLE_EXTERNAL_REASONING", "false")
        self.external_allow_unsanitized = _env_flag(
            "LLM_EXTERNAL_ALLOW_UNSANITIZED", "false"
        )
        self.external_base_url = _normalize_openai_base_url(
            os.getenv("EXTERNAL_LLM_BASE_URL") or DEFAULT_EXTERNAL_BASE_URL
        )
        self.external_model = os.getenv("EXTERNAL_LLM_MODEL") or DEFAULT_EXTERNAL_MODEL
        self.external_api_key = os.getenv("EXTERNAL_LLM_API_KEY")
        self.external_provider = os.getenv("EXTERNAL_LLM_PROVIDER") or DEFAULT_EXTERNAL_PROVIDER
        self.external_timeout_sec = float(
            os.getenv("EXTERNAL_LLM_TIMEOUT_SEC", str(self.timeout_sec)) or "0"
        )
        self.external_completion_timeout_sec = float(
            os.getenv(
                "EXTERNAL_LLM_COMPLETION_TIMEOUT_SEC",
                str(self.external_timeout_sec),
            )
            or "0"
        )
        self.external_json_timeout_sec = float(
            os.getenv("EXTERNAL_LLM_JSON_TIMEOUT_SEC", str(self.external_timeout_sec))
            or "0"
        )
        self.external_no_think = _env_flag("LLM_EXTERNAL_NO_THINK", "false")
        self.external_client = None
        self.external_backend_kind = ""
        self.external_ready = False

        if self.backend_kind == "ollama-generate":
            self.client = None
            self.is_ready = True
            print(
                f"RealLLMService configured for generate-only local LLM API: {self.base_url}"
            )
        else:
            if not api_key:
                print("Warning: LLM_API_KEY not set. RealLLMService will fail if called.")
                self.client = None
                return

            self.client = OpenAI(
                api_key=api_key,
                base_url=self.base_url,
                timeout=None,
            )
            self.is_ready = True

        if self.external_base_url and self.external_model:
            if self.external_base_url.endswith("/api/generate"):
                self.external_backend_kind = "ollama-generate"
                self.external_ready = True
            else:
                self.external_backend_kind = "openai-compatible"
                if self.external_api_key:
                    self.external_client = OpenAI(
                        api_key=self.external_api_key,
                        base_url=self.external_base_url,
                        timeout=None,
                    )
                    self.external_ready = True
                else:
                    logger.warning(
                        "External LLM configured without EXTERNAL_LLM_API_KEY; external routing disabled."
                    )

        if self.external_ready:
            logger.info(
                "External LLM active: provider=%s model=%s base=%s",
                self.external_provider,
                self.external_model,
                self.external_base_url,
            )

    @contextmanager
    def request_scope(
        self,
        route_hint: Optional[str] = None,
        sanitized: Optional[bool] = None,
    ):
        default_route = "external" if self.disable_local_llm else "local"
        route_value = (route_hint or default_route).strip().lower()
        if route_value not in {"local", "external"}:
            route_value = default_route
        if self.disable_local_llm and route_value == "local":
            route_value = "external"
        token_route = self.route_hint_var.set(route_value)
        token_sanitized = self.sanitized_var.set(bool(sanitized))
        try:
            yield
        finally:
            self.route_hint_var.reset(token_route)
            self.sanitized_var.reset(token_sanitized)

    def _resolve_route(
        self,
        route_hint: Optional[str],
        system_prompt: str,
        sanitized: Optional[bool],
    ) -> str:
        default_route = "external" if self.disable_local_llm else "local"
        requested = (route_hint or self.route_hint_var.get() or default_route).strip().lower()
        if self.disable_local_llm and requested == "local":
            requested = "external"
        if requested != "external":
            return "local"

        if not self.external_enabled:
            if self.disable_local_llm:
                raise RuntimeError(
                    "External LLM route requested but disabled, and local LLM is globally disabled."
                )
            logger.info("External LLM route requested but disabled; using local model.")
            return "local"

        if not self.external_ready:
            if self.disable_local_llm:
                raise RuntimeError(
                    "External LLM backend is not ready, and local LLM is globally disabled."
                )
            logger.info(
                "External LLM route requested but external backend is not ready; using local model."
            )
            return "local"

        is_sanitized = bool(sanitized) or self.sanitized_var.get() or (
            "SANITIZED_FOR_EXTERNAL_LLM=YES" in (system_prompt or "")
        )
        if not self.external_allow_unsanitized and not is_sanitized:
            if self.disable_local_llm:
                raise RuntimeError(
                    "External route blocked because payload is not marked sanitized, and local LLM is globally disabled."
                )
            logger.warning(
                "Blocked external LLM route because payload is not marked sanitized; using local model."
            )
            return "local"

        return "external"

    def _external_ollama_generate(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        stream: bool,
        json_mode: bool = False,
    ):
        adjusted_user_prompt = self._with_no_think_for_ollama(
            user_prompt,
            backend_kind=self.external_backend_kind,
            enabled=self.external_no_think,
        )
        payload = {
            "model": self.external_model,
            "prompt": self._build_prompt(system_prompt, adjusted_user_prompt),
            "stream": stream,
            "options": {
                "temperature": self._completion_temperature("external"),
                "num_predict": self._max_tokens(route="external", json_mode=False),
            },
        }
        if json_mode:
            payload["format"] = "json"
            payload["options"]["temperature"] = self._json_temperature("external")
            payload["options"]["num_predict"] = self._max_tokens(
                route="external", json_mode=True
            )

        timeout = self._timeout_for_call(route="external", json_mode=json_mode)
        if stream:
            return httpx.stream(
                "POST",
                self.external_base_url,
                json=payload,
                timeout=timeout,
            )
        return httpx.post(self.external_base_url, json=payload, timeout=timeout)

    def _timeout_error_text(self) -> str:
        return (
            "Error generating response: model response was interrupted. "
            "Please retry or check external LLM (DeepSeek) service availability."
        )

    def _log_llm_result(self, operation: str, *, elapsed_sec: float, outcome: str, extra: str = "") -> None:
        suffix = f" | {extra}" if extra else ""
        logger.info(
            "LLM %s | backend=%s | model=%s | timeout=%.1fs | elapsed=%.2fs | outcome=%s%s",
            operation,
            self.backend_kind,
            self.model,
            self.timeout_sec,
            elapsed_sec,
            outcome,
            suffix,
        )

    def _emit_timeout_warning(self, operation: str, elapsed_sec: float) -> None:
        message = (
            f"LLM {operation} timeout | backend={self.backend_kind} | model={self.model} "
            f"| timeout=disabled | elapsed={elapsed_sec:.2f}s | url={self.base_url}"
        )
        logger.warning(message)
        print(message)

    def _build_prompt(self, system_prompt: str, user_prompt: str) -> str:
        return f"System:\n{system_prompt}\n\nUser:\n{user_prompt}\n\nAssistant:\n"

    def _with_no_think_for_ollama(
        self,
        user_prompt: str,
        *,
        backend_kind: str,
        enabled: bool,
    ) -> str:
        if not enabled or backend_kind != "ollama-generate":
            return user_prompt

        prompt = str(user_prompt or "")
        if re.search(r"(?i)(?<!\\w)/no_think(?!\\w)", prompt):
            return prompt
        if re.search(r"(?i)(?<!\\w)/think(?!\\w)", prompt):
            return re.sub(r"(?i)(?<!\\w)/think(?!\\w)", "/no_think", prompt)
        if not prompt:
            return "/no_think"
        return f"{prompt}\\n/no_think"

    def _effective_system_prompt(self, system_prompt: str) -> str:
        if self.backend_kind != "ollama-generate":
            return system_prompt
        local_rules = """
LOCAL MODEL EXECUTION RULES:
- Follow the requested output structure exactly; do not compress sections or merge tables.
- Do not expose internal reasoning or chain-of-thought.
- If INTERNAL DATABASE LIVE DATA or REAL MARKET DATA is present, copy current prices, dates, and deltas exactly from that context.
- For authoritative numeric priority, use this order only:
  1. INTERNAL DATABASE LIVE DATA / REAL MARKET DATA
  2. PRICING DECISION (STAGE 4)
  3. FORECAST (STAGE 3)
  4. DIAGNOSIS narrative / weekly report / RAG narrative
- Never output two different current prices or two different 7d deltas for the same product.
- Never output two pricing ladders or a second "corrected" pricing table.
- If ladder intermediate steps do not reconcile to the final recommended price, keep the final recommended price authoritative and mention the mismatch briefly instead of inventing new adjustments.
- If the requested current price is unavailable, say it is unavailable; do not infer it.
""".strip()
        return f"{system_prompt.rstrip()}\n\n{local_rules}"

    def _is_kimi_style_external_model(self) -> bool:
        """Moonshot/Kimi models use higher default temperature; DeepSeek uses standard tuning."""
        model = str(self.external_model or "").lower()
        base_url = str(self.external_base_url or "").lower()
        return "kimi" in model or "moonshot.cn" in base_url

    def _completion_temperature(self, route: str = "local") -> float:
        if route == "external" and self._is_kimi_style_external_model():
            return 1.0
        if route == "external":
            raw = os.getenv("EXTERNAL_LLM_TEMPERATURE", "")
            if raw.strip():
                try:
                    return float(raw)
                except ValueError:
                    pass
        if self.backend_kind == "ollama-generate":
            return 0.15
        return 0.3

    def _json_temperature(self, route: str = "local") -> float:
        if route == "external" and self._is_kimi_style_external_model():
            return 1.0
        if route == "external":
            raw = os.getenv("EXTERNAL_LLM_JSON_TEMPERATURE", "")
            if raw.strip():
                try:
                    return float(raw)
                except ValueError:
                    pass
        if self.backend_kind == "ollama-generate":
            return 0.0
        return 0.1

    def _max_tokens(self, *, route: str, json_mode: bool = False) -> int:
        if json_mode:
            base_default = int(os.getenv("LLM_JSON_MAX_TOKENS", "1024") or "1024")
            route_key = (
                "LLM_JSON_MAX_TOKENS_EXTERNAL"
                if route == "external"
                else "LLM_JSON_MAX_TOKENS_LOCAL"
            )
        else:
            base_default = int(os.getenv("LLM_MAX_TOKENS", "4096") or "4096")
            route_key = (
                "LLM_MAX_TOKENS_EXTERNAL"
                if route == "external"
                else "LLM_MAX_TOKENS_LOCAL"
            )

        raw = os.getenv(route_key)
        if raw is None:
            return base_default
        try:
            return max(1, int(raw))
        except Exception:
            return base_default

    def _timeout_for_call(
        self,
        *,
        route: str,
        json_mode: bool,
    ) -> Optional[httpx.Timeout]:
        # Timeout policy is intentionally disabled so long-running responses can
        # complete without being interrupted by per-call hard limits.
        return None

    def _strip_reasoning_blocks(self, text: str) -> str:
        original = str(text or "")
        cleaned = re.sub(r"<think>.*?</think>", "", original, flags=re.DOTALL | re.IGNORECASE)
        cleaned = cleaned.replace("<think>", "").replace("</think>", "")
        cleaned = cleaned.strip()

        reasoning_markers = (
            "Thinking Process:",
            "Reasoning:",
            "思考过程：",
            "思考过程:",
        )
        if cleaned.startswith(reasoning_markers):
            heading_match = re.search(
                r"(?m)^(##\s*1\)\s*Direct Answer|##\s*1\)\s*直接回答|##\s*0?1\.|##\s*📊.+|##\s+Executive View|##\s+Market Overview|#{2,3}\s+.+|📊\s*01.+|基于.+|本周.+|当前.+|推荐价格.+|Direct Answer[:：]|##\s*结论|##\s*ERP)",
                cleaned,
            )
            if heading_match:
                cleaned = cleaned[heading_match.start() :].strip()
            else:
                final_answer_match = re.search(
                    r"(?is)(?:final\s+answer|answer)\s*[:：]\s*(.+)$",
                    cleaned,
                )
                if final_answer_match:
                    cleaned = final_answer_match.group(1).strip()
                else:
                    parts = cleaned.split("\n\n", 1)
                    if len(parts) > 1 and parts[1].strip():
                        cleaned = parts[1].strip()
                    elif len(cleaned) >= 120:
                        pass
                    elif original.strip():
                        return original.strip()

        return cleaned.strip()

    def _stream_visible_deltas(self, chunks, state=None):
        start_tag = "<think>"
        end_tag = "</think>"
        state = state or {}
        inside_think = bool(state.get("inside_think", False))
        buffer = str(state.get("buffer", ""))

        for chunk in chunks:
            if not chunk:
                continue
            buffer += chunk
            visible_parts = []

            while buffer:
                if inside_think:
                    end_idx = buffer.find(end_tag)
                    if end_idx == -1:
                        keep = len(end_tag) - 1
                        if len(buffer) > keep:
                            buffer = buffer[-keep:]
                        break
                    buffer = buffer[end_idx + len(end_tag):]
                    inside_think = False
                    continue

                start_idx = buffer.find(start_tag)
                if start_idx == -1:
                    keep = len(start_tag) - 1
                    if len(buffer) > keep:
                        visible_parts.append(buffer[:-keep])
                        buffer = buffer[-keep:]
                    break

                if start_idx > 0:
                    visible_parts.append(buffer[:start_idx])
                buffer = buffer[start_idx + len(start_tag):]
                inside_think = True

            visible = "".join(visible_parts)
            if visible:
                yield visible

        state["inside_think"] = inside_think
        state["buffer"] = buffer

    def _flush_stream_state(self, state) -> str:
        if not state or state.get("inside_think"):
            return ""
        buffer = str(state.get("buffer", ""))
        state["buffer"] = ""
        if not buffer:
            return ""
        return self._strip_reasoning_blocks(buffer)

    def _recover_from_empty_stream(
        self,
        system_prompt: str,
        user_prompt: str,
        route_hint: Optional[str] = None,
        sanitized: Optional[bool] = None,
    ) -> str:
        try:
            recovered = self.generate_completion(
                system_prompt,
                user_prompt,
                route_hint=route_hint,
                sanitized=sanitized,
            )
        except Exception as exc:
            return f"Error generating response: {str(exc)}"

        text = str(recovered or "").strip()
        if text:
            return text
        return self._timeout_error_text()

    def _ollama_generate(self, system_prompt: str, user_prompt: str, *, stream: bool, json_mode: bool = False):
        adjusted_user_prompt = self._with_no_think_for_ollama(
            user_prompt,
            backend_kind=self.backend_kind,
            enabled=self.local_no_think,
        )
        payload = {
            "model": self.model,
            "prompt": self._build_prompt(
                self._effective_system_prompt(system_prompt),
                adjusted_user_prompt,
            ),
            "stream": stream,
            "options": {
                "temperature": self._completion_temperature(),
                "num_predict": self._max_tokens(route="local", json_mode=False),
            },
        }
        if json_mode:
            payload["format"] = "json"
            payload["options"]["temperature"] = self._json_temperature()
            payload["options"]["num_predict"] = self._max_tokens(
                route="local", json_mode=True
            )

        timeout = self._timeout_for_call(route="local", json_mode=json_mode)
        if stream:
            return httpx.stream("POST", self.base_url, json=payload, timeout=timeout)
        return httpx.post(self.base_url, json=payload, timeout=timeout)

    def generate_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        route_hint: Optional[str] = None,
        sanitized: Optional[bool] = None,
    ) -> str:
        route = self._resolve_route(route_hint, system_prompt, sanitized)
        if route == "external":
            if self.external_backend_kind == "ollama-generate":
                started_at = time.perf_counter()
                try:
                    response = self._external_ollama_generate(
                        self._effective_system_prompt(system_prompt),
                        user_prompt,
                        stream=False,
                    )
                    response.raise_for_status()
                    data = response.json()
                    elapsed_sec = time.perf_counter() - started_at
                    logger.info(
                        "LLM generate_completion | provider=external | backend=%s | model=%s | timeout=%.1fs | elapsed=%.2fs | outcome=success",
                        self.external_backend_kind,
                        self.external_model,
                        self.external_timeout_sec,
                        elapsed_sec,
                    )
                    return self._strip_reasoning_blocks(str(data.get("response", "")))
                except Exception as e:
                    logger.exception(
                        "External LLM generate_completion failed | backend=%s | model=%s | url=%s",
                        self.external_backend_kind,
                        self.external_model,
                        self.external_base_url,
                    )
                    return f"Error generating response: {str(e)}"

            started_at = time.perf_counter()
            try:
                max_tokens = self._max_tokens(route="external", json_mode=False)
                response = self.external_client.chat.completions.create(
                    model=self.external_model,
                    messages=[
                        {
                            "role": "system",
                            "content": self._effective_system_prompt(system_prompt),
                        },
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=self._completion_temperature("external"),
                    max_tokens=max_tokens,
                    timeout=self._timeout_for_call(route="external", json_mode=False),
                )
                elapsed_sec = time.perf_counter() - started_at
                logger.info(
                    "LLM generate_completion | provider=external | backend=%s | model=%s | timeout=%.1fs | elapsed=%.2fs | outcome=success",
                    self.external_backend_kind,
                    self.external_model,
                    self.external_timeout_sec,
                    elapsed_sec,
                )
                return self._strip_reasoning_blocks(response.choices[0].message.content or "")
            except Exception as e:
                logger.exception(
                    "External LLM generate_completion failed | backend=%s | model=%s | url=%s",
                    self.external_backend_kind,
                    self.external_model,
                    self.external_base_url,
                )
                return f"Error generating response: {str(e)}"

        if self.backend_kind == "ollama-generate":
            started_at = time.perf_counter()
            try:
                response = self._ollama_generate(system_prompt, user_prompt, stream=False)
                response.raise_for_status()
                data = response.json()
                elapsed_sec = time.perf_counter() - started_at
                self._log_llm_result("generate_completion", elapsed_sec=elapsed_sec, outcome="success")
                return self._strip_reasoning_blocks(str(data.get("response", "")))
            except Exception as e:
                elapsed_sec = time.perf_counter() - started_at
                logger.exception(
                    "LLM generate_completion failed | backend=%s | model=%s | elapsed=%.2fs | url=%s",
                    self.backend_kind,
                    self.model,
                    elapsed_sec,
                    self.base_url,
                )
                if isinstance(e, httpx.TimeoutException):
                    return self._timeout_error_text()
                return f"Error generating response: {str(e)}"

        if not self.client:
            return "Error: LLM_API_KEY is not configured."

        try:
            started_at = time.perf_counter()
            max_tokens = self._max_tokens(route="local", json_mode=False)
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self._effective_system_prompt(system_prompt)},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=self._completion_temperature(),
                max_tokens=max_tokens,
                timeout=self._timeout_for_call(route="local", json_mode=False),
            )
            elapsed_sec = time.perf_counter() - started_at
            self._log_llm_result("generate_completion", elapsed_sec=elapsed_sec, outcome="success")
            return self._strip_reasoning_blocks(response.choices[0].message.content or "")
        except Exception as e:
            logger.exception(
                "LLM generate_completion failed | backend=%s | model=%s | url=%s",
                self.backend_kind,
                self.model,
                self.base_url,
            )
            try:
                pieces = []
                for delta in self.generate_completion_stream(system_prompt, user_prompt):
                    if not delta:
                        continue
                    pieces.append(delta)
                recovered = self._strip_reasoning_blocks("".join(pieces))
                if recovered and not recovered.startswith("Error generating response:"):
                    print("LLM fallback: recovered final completion via streaming path")
                    return recovered
            except Exception as stream_error:
                print(f"LLM Fallback Stream Error: {stream_error}")
            return f"Error generating response: {str(e)}"

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        route_hint: Optional[str] = None,
        sanitized: Optional[bool] = None,
    ) -> dict:
        route = self._resolve_route(route_hint, system_prompt, sanitized)
        if route == "external":
            if self.external_backend_kind == "ollama-generate":
                try:
                    response = self._external_ollama_generate(
                        self._effective_system_prompt(system_prompt),
                        user_prompt,
                        stream=False,
                        json_mode=True,
                    )
                    response.raise_for_status()
                    content = str(response.json().get("response", "")).strip()
                    return json.loads(content) if content else {}
                except Exception:
                    logger.exception(
                        "External LLM generate_json failed | backend=%s | model=%s | url=%s",
                        self.external_backend_kind,
                        self.external_model,
                        self.external_base_url,
                    )
                    return {}

            try:
                max_tokens = self._max_tokens(route="external", json_mode=True)
                response = self.external_client.chat.completions.create(
                    model=self.external_model,
                    messages=[
                        {
                            "role": "system",
                            "content": self._effective_system_prompt(system_prompt)
                            + "\nIMPORTANT: Return valid JSON only. Do not include Markdown formatting like ```json ... ```.",
                        },
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format={"type": "json_object"},
                    temperature=self._json_temperature("external"),
                    max_tokens=max_tokens,
                    timeout=self._timeout_for_call(route="external", json_mode=True),
                )
                content = response.choices[0].message.content
                return json.loads(content) if content else {}
            except Exception:
                logger.exception(
                    "External LLM generate_json failed | backend=%s | model=%s | url=%s",
                    self.external_backend_kind,
                    self.external_model,
                    self.external_base_url,
                )
                return {}

        if self.backend_kind == "ollama-generate":
            started_at = time.perf_counter()
            try:
                response = self._ollama_generate(system_prompt, user_prompt, stream=False, json_mode=True)
                response.raise_for_status()
                content = str(response.json().get("response", "")).strip()
                elapsed_sec = time.perf_counter() - started_at
                self._log_llm_result("generate_json", elapsed_sec=elapsed_sec, outcome="success")
                return json.loads(content) if content else {}
            except Exception as e:
                elapsed_sec = time.perf_counter() - started_at
                logger.exception(
                    "LLM generate_json failed | backend=%s | model=%s | elapsed=%.2fs | url=%s",
                    self.backend_kind,
                    self.model,
                    elapsed_sec,
                    self.base_url,
                )
                return {}

        if not self.client:
            return {}

        try:
            started_at = time.perf_counter()
            max_tokens = self._max_tokens(route="local", json_mode=True)
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": self._effective_system_prompt(system_prompt)
                        + "\nIMPORTANT: Return valid JSON only. Do not include Markdown formatting like ```json ... ```.",
                    },
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=self._json_temperature(),
                max_tokens=max_tokens,  # Keep JSON responses compact for cost control
                timeout=self._timeout_for_call(route="local", json_mode=True),
            )
            elapsed_sec = time.perf_counter() - started_at
            self._log_llm_result("generate_json", elapsed_sec=elapsed_sec, outcome="success")
            content = response.choices[0].message.content
            return json.loads(content)
        except Exception as e:
            logger.exception(
                "LLM generate_json failed | backend=%s | model=%s | url=%s",
                self.backend_kind,
                self.model,
                self.base_url,
            )
            try:
                return {}
            except:
                return {}

    def generate_completion_stream(
        self,
        system_prompt: str,
        user_prompt: str,
        route_hint: Optional[str] = None,
        sanitized: Optional[bool] = None,
    ):
        """Yield incremental text deltas from an OpenAI-compatible streaming API."""
        route = self._resolve_route(route_hint, system_prompt, sanitized)
        if route == "external":
            if self.external_backend_kind == "ollama-generate":
                try:
                    stream_state = {"inside_think": False, "buffer": ""}
                    emitted_visible = False
                    with self._external_ollama_generate(
                        self._effective_system_prompt(system_prompt),
                        user_prompt,
                        stream=True,
                    ) as response:
                        response.raise_for_status()
                        for line in response.iter_lines():
                            if not line:
                                continue
                            try:
                                data = json.loads(line)
                            except Exception:
                                continue
                            delta = data.get("response")
                            if delta:
                                for visible in self._stream_visible_deltas([str(delta)], stream_state):
                                    emitted_visible = True
                                    yield visible
                            if data.get("done"):
                                trailing = self._flush_stream_state(stream_state)
                                if trailing:
                                    emitted_visible = True
                                    yield trailing
                                if not emitted_visible:
                                    yield self._recover_from_empty_stream(
                                        system_prompt,
                                        user_prompt,
                                        route_hint=route_hint,
                                        sanitized=sanitized,
                                    )
                                break
                except Exception as e:
                    logger.exception(
                        "External LLM generate_completion_stream failed | backend=%s | model=%s | url=%s",
                        self.external_backend_kind,
                        self.external_model,
                        self.external_base_url,
                    )
                    yield f"Error generating response: {str(e)}"
                return

            try:
                max_tokens = self._max_tokens(route="external", json_mode=False)
                stream_state = {"inside_think": False, "buffer": ""}
                emitted_visible = False
                stream = self.external_client.chat.completions.create(
                    model=self.external_model,
                    messages=[
                        {
                            "role": "system",
                            "content": self._effective_system_prompt(system_prompt),
                        },
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=self._completion_temperature("external"),
                    max_tokens=max_tokens,
                    stream=True,
                    timeout=self._timeout_for_call(route="external", json_mode=False),
                )
                for chunk in stream:
                    try:
                        delta = chunk.choices[0].delta.content
                    except Exception:
                        delta = None
                    if delta:
                        for visible in self._stream_visible_deltas([delta], stream_state):
                            emitted_visible = True
                            yield visible
                trailing = self._flush_stream_state(stream_state)
                if trailing:
                    emitted_visible = True
                    yield trailing
                if not emitted_visible:
                    yield self._recover_from_empty_stream(
                        system_prompt,
                        user_prompt,
                        route_hint=route_hint,
                        sanitized=sanitized,
                    )
            except Exception as e:
                logger.exception(
                    "External LLM generate_completion_stream failed | backend=%s | model=%s | url=%s",
                    self.external_backend_kind,
                    self.external_model,
                    self.external_base_url,
                )
                yield f"Error generating response: {str(e)}"
            return

        if self.backend_kind == "ollama-generate":
            started_at = time.perf_counter()
            try:
                stream_state = {"inside_think": False, "buffer": ""}
                emitted_visible = False
                with self._ollama_generate(system_prompt, user_prompt, stream=True) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except Exception:
                            continue
                        delta = data.get("response")
                        if delta:
                            for visible in self._stream_visible_deltas([str(delta)], stream_state):
                                emitted_visible = True
                                yield visible
                        if data.get("done"):
                            trailing = self._flush_stream_state(stream_state)
                            if trailing:
                                emitted_visible = True
                                yield trailing
                            if not emitted_visible:
                                yield self._recover_from_empty_stream(
                                    system_prompt,
                                    user_prompt,
                                    route_hint=route_hint,
                                    sanitized=sanitized,
                                )
                            elapsed_sec = time.perf_counter() - started_at
                            self._log_llm_result("generate_completion_stream", elapsed_sec=elapsed_sec, outcome="success")
                            break
            except Exception as e:
                elapsed_sec = time.perf_counter() - started_at
                logger.exception(
                    "LLM generate_completion_stream failed | backend=%s | model=%s | elapsed=%.2fs | url=%s",
                    self.backend_kind,
                    self.model,
                    elapsed_sec,
                    self.base_url,
                )
                fallback = self.generate_completion(system_prompt, user_prompt)
                if fallback and not fallback.startswith("Error generating response:"):
                    yield fallback
                else:
                    yield f"Error generating response: {str(e)}"
            return

        if not self.client:
            yield "Error: LLM_API_KEY is not configured."
            return

        try:
            started_at = time.perf_counter()
            max_tokens = self._max_tokens(route="local", json_mode=False)
            stream_state = {"inside_think": False, "buffer": ""}
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self._effective_system_prompt(system_prompt)},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=self._completion_temperature(),
                max_tokens=max_tokens,
                stream=True,
                timeout=self._timeout_for_call(route="local", json_mode=False),
            )
            for chunk in stream:
                try:
                    delta = chunk.choices[0].delta.content
                except Exception:
                    delta = None
                if delta:
                    for visible in self._stream_visible_deltas([delta], stream_state):
                        yield visible
            trailing = self._flush_stream_state(stream_state)
            if trailing:
                yield trailing
            elapsed_sec = time.perf_counter() - started_at
            self._log_llm_result("generate_completion_stream", elapsed_sec=elapsed_sec, outcome="success")
        except Exception as e:
            logger.exception(
                "LLM generate_completion_stream failed | backend=%s | model=%s | url=%s",
                self.backend_kind,
                self.model,
                self.base_url,
            )
            yield f"Error generating response: {str(e)}"
