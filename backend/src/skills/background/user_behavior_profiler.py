"""User Behavior Profiler — Background Skill.

Automatically analyses user chat behaviour (trading goals, style, preferences,
recurring patterns) and stores structured memory in ChromaDB so that EDIA
can personalise every conversation.

No human in the loop — EDIA self-updates by observing its own chat history.
Runs every 30 minutes as a background skill.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from .base import BackgroundSkill, SkillResult

logger = logging.getLogger(__name__)

# LLM extraction prompt — instructs the model to produce structured JSON
_EXTRACTION_SYSTEM_PROMPT = """You are a commodities-trading analyst observing a user conversation with an AI pricing advisor (EDIA).

Your task is to extract the user's behavioural profile from the chat history below.

Return a JSON object with these optional keys (include only keys that have meaningful evidence in the conversation):

{
  "goal": "Stated or inferred trading/commodity objective (e.g. 'hedge Q2 benzene exposure', 'optimise C5-C9 portfolio')",
  "trading_style": "Trading style description (e.g. 'conservative, data-driven, prefers short-term positions', 'aggressive speculator, uses leverage')",
  "preference": "Notable preferences or dislikes (e.g. 'prefers weekly reports in Chinese', 'focuses on supply-chain risk', 'avoids OTC derivatives')",
  "pattern": "Recurring behavioural pattern (e.g. 'always follows up on competitor price moves', 'approvals-seeking before major decisions', 'queries price forecasts weekly')"
}

Rules:
- Be concise and specific. Use the evidence from the conversation.
- confidence should reflect how strong the evidence is (0.0–1.0).
- If a key has no evidence, omit it entirely (do NOT set it to null/empty).
- Return valid JSON only, no markdown, no explanation.
""".strip()


def _build_conversation_summary(messages: list[dict]) -> str:
    """Render a flat text summary of a message thread for the LLM."""
    lines = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = str(msg.get("content") or msg.get("message") or "").strip()
        if not content:
            continue
        prefix = "USER" if role == "user" else "EDIA"
        lines.append(f"{prefix}: {content[:300]}")
    return "\n".join(lines)


def _extract_with_llm(conversation_text: str) -> list[dict]:
    """Call the LLM to extract behavioural profiles from a conversation.

    Returns a list of memory-entry dicts (one per non-omitted key).
    """
    from backend.src.nlu.llm_service import RealLLMService

    try:
        llm = RealLLMService()
    except Exception as exc:
        logger.warning("UserBehaviorProfiler: could not instantiate LLM service: %s", exc)
        return []

    try:
        result = llm.generate_json(
            system_prompt=_EXTRACTION_SYSTEM_PROMPT,
            user_prompt=f"Chat history:\n{conversation_text}",
            sanitized=True,
        )
    except Exception as exc:
        logger.warning("UserBehaviorProfiler: LLM extraction failed: %s", exc)
        return []

    memories = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for key in ("goal", "trading_style", "preference", "pattern"):
        value = result.get(key)
        if not value:
            continue
        confidence = float(result.get(f"{key}_confidence", 0.7))
        memories.append({
            "memory_type": key,
            "content": str(value).strip(),
            "confidence": confidence,
            "extracted_at": now_iso,
        })

    return memories


class UserBehaviorProfiler(BackgroundSkill):
    """Background skill that auto-learns user profiles from chat history."""

    name = "user_behavior_profiler"
    interval_minutes = 30  # run every 30 minutes

    def run(self) -> SkillResult:
        """Scan recent sessions, extract behavioural profiles, store in ChromaDB."""
        try:
            from backend.src.db.sqlite_db import db
            from backend.src.rag.vector_store import kb
        except Exception as exc:
            return SkillResult(
                skill_name=self.name,
                status="error",
                summary=f"import failed: {exc}",
            )

        users_analyzed = 0
        memories_stored = 0
        errors: list[str] = []

        # Process sessions from the last 4 hours (catch users who chat frequently)
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=4)
            recent_sessions = db.list_recent_sessions(limit=200)
        except Exception as exc:
            return SkillResult(
                skill_name=self.name,
                status="error",
                summary=f"failed to list sessions: {exc}",
            )

        # Group sessions by user
        sessions_by_user: dict[str, list[str]] = {}
        for session in recent_sessions:
            session_id = str(session.get("id") or session.get("session_id") or "")
            user_id = str(session.get("user_id") or "")
            if not session_id or not user_id:
                continue
            try:
                msg_list = db.get_messages(session_id)
                if not msg_list:
                    continue
                # Check if any message is recent enough
                latest_ts = msg_list[-1].get("created_at") or msg_list[-1].get("timestamp")
                if latest_ts:
                    if isinstance(latest_ts, str):
                        try:
                            ts_dt = datetime.fromisoformat(latest_ts.replace("Z", "+00:00"))
                            if not ts_dt.tzinfo:
                                ts_dt = ts_dt.replace(tzinfo=timezone.utc)
                        except Exception:
                            ts_dt = datetime.now(timezone.utc)
                    else:
                        ts_dt = latest_ts
                    if ts_dt < cutoff:
                        continue
            except Exception:
                pass
            sessions_by_user.setdefault(user_id, []).append(session_id)

        for user_id, session_ids in sessions_by_user.items():
            users_analyzed += 1

            # Collect recent conversation across all sessions for this user
            all_messages: list[dict] = []
            for sid in session_ids:
                try:
                    msgs = db.get_messages(sid)
                    all_messages.extend(msgs[-20:])  # last 20 messages per session
                except Exception:
                    pass

            if len(all_messages) < 2:
                continue

            # Deduplicate by content+role
            seen: set[str] = set()
            deduped: list[dict] = []
            for msg in all_messages:
                key = f"{msg.get('role', '')}:{str(msg.get('content') or msg.get('message') or '')[:80]}"
                if key not in seen:
                    seen.add(key)
                    deduped.append(msg)
            deduped.sort(key=lambda m: str(m.get("created_at") or m.get("timestamp") or ""))

            conversation_text = _build_conversation_summary(deduped)
            if not conversation_text.strip():
                continue

            memories = _extract_with_llm(conversation_text)
            if not memories:
                continue

            # Build ChromaDB documents
            documents = []
            metadatas = []
            ids = []
            now_iso = datetime.now(timezone.utc).isoformat()

            for mem in memories:
                session_tag = session_ids[0] if session_ids else "unknown"
                doc_id = f"user_mem_{user_id}_{session_tag}_{now_iso[:13]}".replace(":", "-").replace("+", "-")
                documents.append(mem["content"])
                metadatas.append({
                    "user_id": user_id,
                    "session_id": session_tag,
                    "memory_type": mem["memory_type"],
                    "confidence": mem["confidence"],
                    "extracted_at": now_iso,
                })
                ids.append(doc_id)
                memories_stored += 1

            if documents:
                try:
                    kb.upsert_user_memory(
                        documents=documents,
                        metadatas=metadatas,
                        ids=ids,
                    )
                except Exception as exc:
                    errors.append(f"user {user_id}: {exc}")

        status = "error" if (errors and memories_stored == 0) else "success"
        summary = (
            f"analysed {users_analyzed} users, stored {memories_stored} memory entries"
            + (f", {len(errors)} errors" if errors else "")
        )

        return SkillResult(
            skill_name=self.name,
            status=status,
            summary=summary,
            data={
                "users_analyzed": users_analyzed,
                "memories_stored": memories_stored,
                "error_count": len(errors),
            },
            alerts=[],
        )
