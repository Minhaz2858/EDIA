from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Any, List, Optional

from sqlalchemy import text

from backend.src.db.sqlite_db import db
from backend.src.intelligence.event_types import ExtractedEvent


class IntelligenceEventRegistry:
    """MySQL-backed registry for online news and geopolitical intelligence events."""

    def __init__(self) -> None:
        self.engine = db.engine
        self._init_db()

    def _init_db(self) -> None:
        ddl = text(
            """
            CREATE TABLE IF NOT EXISTS intelligence_events (
                event_id VARCHAR(64) PRIMARY KEY,
                event_type VARCHAR(80) NOT NULL,
                headline TEXT NOT NULL,
                affected_commodities TEXT,
                direction VARCHAR(32),
                magnitude_estimate VARCHAR(32),
                certainty VARCHAR(32),
                time_horizon VARCHAR(32),
                geographic_scope VARCHAR(64),
                relevance_to_c5_c9 VARCHAR(32),
                key_entities TEXT,
                key_information TEXT,
                causal_chain_hint TEXT,
                source_url TEXT,
                source_credibility VARCHAR(32),
                full_text TEXT,
                raw_quote TEXT,
                detected_at VARCHAR(64) NOT NULL,
                impact_magnitude FLOAT,
                relevance_score FLOAT,
                review_status VARCHAR(32) NOT NULL DEFAULT 'approved',
                usefulness_score INTEGER NOT NULL DEFAULT 0,
                reviewed_by VARCHAR(64),
                reviewed_at VARCHAR(64),
                review_note TEXT,
                created_at VARCHAR(64) NOT NULL,
                updated_at VARCHAR(64) NOT NULL
            )
            """
        )
        status_ddl = text(
            """
            CREATE TABLE IF NOT EXISTS intelligence_ingestion_status (
                agent_name VARCHAR(64) PRIMARY KEY,
                running INTEGER DEFAULT 0,
                cycle_in_progress INTEGER DEFAULT 0,
                scan_interval_seconds INTEGER,
                last_started_at VARCHAR(64),
                last_completed_at VARCHAR(64),
                last_success_at VARCHAR(64),
                last_error TEXT,
                last_cycle_duration_ms FLOAT,
                last_articles_seen INTEGER DEFAULT 0,
                last_events_extracted INTEGER DEFAULT 0,
                last_events_stored INTEGER DEFAULT 0,
                next_run_at VARCHAR(64),
                updated_at VARCHAR(64) NOT NULL
            )
            """
        )

        with self.engine.begin() as conn:
            conn.execute(ddl)
            conn.execute(status_ddl)
            # Lightweight schema evolution for existing deployments.
            self._ensure_column(conn, "review_status", "VARCHAR(32) NOT NULL DEFAULT 'approved'")
            self._ensure_column(conn, "usefulness_score", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "reviewed_by", "VARCHAR(64)")
            self._ensure_column(conn, "reviewed_at", "VARCHAR(64)")
            self._ensure_column(conn, "review_note", "TEXT")
            self._ensure_column(conn, "key_information", "TEXT")

    @staticmethod
    def _default_review_status() -> str:
        raw_value = (os.getenv("INTELLIGENCE_DEFAULT_REVIEW_STATUS", "approved") or "approved").strip().lower()
        if raw_value in {"pending", "approved", "rejected"}:
            return raw_value
        return "approved"

    @staticmethod
    def _ensure_column(conn, column_name: str, column_def: str) -> None:
        try:
            conn.execute(
                text(
                    f"ALTER TABLE intelligence_events ADD COLUMN {column_name} {column_def}"
                )
            )
        except Exception:
            # Column likely already exists; ignore to keep startup resilient.
            pass

    def upsert_event(self, event: ExtractedEvent) -> None:
        now = datetime.utcnow().isoformat()
        payload = {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "headline": event.headline,
            "affected_commodities": json.dumps(event.affected_commodities),
            "direction": event.direction,
            "magnitude_estimate": event.magnitude_estimate,
            "certainty": event.certainty,
            "time_horizon": event.time_horizon,
            "geographic_scope": event.geographic_scope,
            "relevance_to_c5_c9": event.relevance_to_c5_c9,
            "key_entities": json.dumps(event.key_entities),
            "key_information": (event.key_information or "").strip(),
            "causal_chain_hint": event.causal_chain_hint,
            "source_url": event.source_url,
            "source_credibility": event.source_credibility,
            "full_text": event.full_text,
            "raw_quote": event.raw_quote,
            "detected_at": event.detected_at,
            "impact_magnitude": event.impact_magnitude,
            "relevance_score": event.relevance_score,
            "review_status": (event.review_status or self._default_review_status()).strip().lower(),
            "usefulness_score": int(event.usefulness_score or 0),
            "reviewed_by": (event.reviewed_by or "").strip(),
            "reviewed_at": (event.reviewed_at or "").strip(),
            "review_note": (event.review_note or "").strip(),
            "created_at": now,
            "updated_at": now,
        }

        with self.engine.begin() as conn:
            existing = conn.execute(
                text(
                    "SELECT 1 FROM intelligence_events WHERE event_id = :event_id LIMIT 1"
                ),
                {"event_id": event.event_id},
            ).first()

            if existing:
                conn.execute(
                    text(
                        """
                        UPDATE intelligence_events
                        SET event_type = :event_type,
                            headline = :headline,
                            affected_commodities = :affected_commodities,
                            direction = :direction,
                            magnitude_estimate = :magnitude_estimate,
                            certainty = :certainty,
                            time_horizon = :time_horizon,
                            geographic_scope = :geographic_scope,
                            relevance_to_c5_c9 = :relevance_to_c5_c9,
                            key_entities = :key_entities,
                            key_information = :key_information,
                            causal_chain_hint = :causal_chain_hint,
                            source_url = CASE
                                WHEN :source_url IS NOT NULL AND :source_url != '' THEN :source_url
                                ELSE source_url
                            END,
                            source_credibility = :source_credibility,
                            full_text = :full_text,
                            raw_quote = :raw_quote,
                            detected_at = :detected_at,
                            impact_magnitude = :impact_magnitude,
                            relevance_score = :relevance_score,
                            review_status = CASE
                                WHEN :review_status IS NOT NULL AND :review_status != '' THEN :review_status
                                ELSE review_status
                            END,
                            usefulness_score = CASE
                                WHEN :usefulness_score IS NOT NULL THEN :usefulness_score
                                ELSE usefulness_score
                            END,
                            reviewed_by = CASE
                                WHEN :reviewed_by IS NOT NULL AND :reviewed_by != '' THEN :reviewed_by
                                ELSE reviewed_by
                            END,
                            reviewed_at = CASE
                                WHEN :reviewed_at IS NOT NULL AND :reviewed_at != '' THEN :reviewed_at
                                ELSE reviewed_at
                            END,
                            review_note = CASE
                                WHEN :review_note IS NOT NULL AND :review_note != '' THEN :review_note
                                ELSE review_note
                            END,
                            updated_at = :updated_at
                        WHERE event_id = :event_id
                        """
                    ),
                    payload,
                )
            else:
                conn.execute(
                    text(
                        """
                        INSERT INTO intelligence_events (
                            event_id, event_type, headline, affected_commodities,
                            direction, magnitude_estimate, certainty, time_horizon,
                            geographic_scope, relevance_to_c5_c9, key_entities,
                            key_information,
                            causal_chain_hint, source_url, source_credibility,
                            full_text, raw_quote, detected_at, impact_magnitude,
                            relevance_score, review_status, usefulness_score,
                            reviewed_by, reviewed_at, review_note,
                            created_at, updated_at
                        ) VALUES (
                            :event_id, :event_type, :headline, :affected_commodities,
                            :direction, :magnitude_estimate, :certainty, :time_horizon,
                            :geographic_scope, :relevance_to_c5_c9, :key_entities,
                            :key_information,
                            :causal_chain_hint, :source_url, :source_credibility,
                            :full_text, :raw_quote, :detected_at, :impact_magnitude,
                            :relevance_score, :review_status, :usefulness_score,
                            :reviewed_by, :reviewed_at, :review_note,
                            :created_at, :updated_at
                        )
                        """
                    ),
                    payload,
                )

    def list_recent_events(
        self,
        hours: int = 24,
        min_impact: Optional[float] = None,
        review_status: Optional[str] = None,
        min_usefulness_score: Optional[int] = None,
        limit: int = 50,
    ) -> List[ExtractedEvent]:
        cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
        query = "SELECT * FROM intelligence_events " "WHERE detected_at >= :cutoff "
        params: dict[str, Any] = {"cutoff": cutoff, "limit": limit}

        if min_impact is not None:
            query += "AND impact_magnitude >= :min_impact "
            params["min_impact"] = min_impact

        if review_status:
            query += "AND review_status = :review_status "
            params["review_status"] = review_status.strip().lower()

        if min_usefulness_score is not None:
            query += "AND usefulness_score >= :min_usefulness_score "
            params["min_usefulness_score"] = int(min_usefulness_score)

        query += "ORDER BY detected_at DESC LIMIT :limit"

        with self.engine.connect() as conn:
            rows = conn.execute(text(query), params)
            return [self._row_to_event(dict(row._mapping)) for row in rows]

    def list_events_as_of(
        self,
        as_of: str,
        lookback_hours: int = 168,
        min_impact: Optional[float] = None,
        review_status: Optional[str] = None,
        min_usefulness_score: Optional[int] = None,
        limit: int = 80,
    ) -> List[ExtractedEvent]:
        """Return persisted events that would have been known at a historical as-of time."""
        as_of_dt = datetime.fromisoformat(str(as_of)[:19])
        cutoff = (as_of_dt - timedelta(hours=lookback_hours)).isoformat()
        query = (
            "SELECT * FROM intelligence_events "
            "WHERE detected_at >= :cutoff AND detected_at <= :as_of "
        )
        params: dict[str, Any] = {
            "cutoff": cutoff,
            "as_of": as_of_dt.isoformat(),
            "limit": limit,
        }

        if min_impact is not None:
            query += "AND impact_magnitude >= :min_impact "
            params["min_impact"] = min_impact

        if review_status:
            query += "AND review_status = :review_status "
            params["review_status"] = review_status.strip().lower()

        if min_usefulness_score is not None:
            query += "AND usefulness_score >= :min_usefulness_score "
            params["min_usefulness_score"] = int(min_usefulness_score)

        query += "ORDER BY detected_at DESC LIMIT :limit"

        with self.engine.connect() as conn:
            rows = conn.execute(text(query), params)
            return [self._row_to_event(dict(row._mapping)) for row in rows]

    def list_recent_event_rows(
        self,
        hours: int = 24,
        min_impact: Optional[float] = None,
        review_status: Optional[str] = None,
        min_usefulness_score: Optional[int] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
        query = "SELECT * FROM intelligence_events WHERE detected_at >= :cutoff "
        params: dict[str, Any] = {"cutoff": cutoff, "limit": limit}

        if min_impact is not None:
            query += "AND impact_magnitude >= :min_impact "
            params["min_impact"] = min_impact

        if review_status:
            query += "AND review_status = :review_status "
            params["review_status"] = review_status.strip().lower()

        if min_usefulness_score is not None:
            query += "AND usefulness_score >= :min_usefulness_score "
            params["min_usefulness_score"] = int(min_usefulness_score)

        query += "ORDER BY detected_at DESC LIMIT :limit"
        with self.engine.connect() as conn:
            rows = conn.execute(text(query), params)
            return [dict(row._mapping) for row in rows]

    def review_event(
        self,
        event_id: str,
        *,
        review_status: str,
        usefulness_score: Optional[int] = None,
        reviewed_by: str = "",
        review_note: str = "",
    ) -> bool:
        status = (review_status or "").strip().lower()
        if status not in {"pending", "approved", "rejected"}:
            raise ValueError("review_status must be pending|approved|rejected")

        score = usefulness_score
        if score is not None:
            score = max(0, min(5, int(score)))

        payload = {
            "event_id": event_id,
            "review_status": status,
            "usefulness_score": score,
            "reviewed_by": reviewed_by.strip(),
            "reviewed_at": datetime.utcnow().isoformat(),
            "review_note": review_note.strip(),
        }

        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    UPDATE intelligence_events
                    SET review_status = :review_status,
                        usefulness_score = COALESCE(:usefulness_score, usefulness_score),
                        reviewed_by = :reviewed_by,
                        reviewed_at = :reviewed_at,
                        review_note = :review_note,
                        updated_at = :reviewed_at
                    WHERE event_id = :event_id
                    """
                ),
                payload,
            )
            return bool(getattr(row, "rowcount", 0))

    def remove_event(
        self,
        event_id: str,
        *,
        reviewed_by: str = "",
        review_note: str = "",
    ) -> bool:
        note = (review_note or "").strip()
        if not note:
            note = "Removed by admin moderation"
        return self.review_event(
            event_id,
            review_status="rejected",
            usefulness_score=0,
            reviewed_by=reviewed_by,
            review_note=note,
        )

    def get_review_summary(self, hours: int = 168) -> dict[str, Any]:
        cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
        query = text(
            """
            SELECT
                review_status,
                COUNT(*) AS total,
                AVG(COALESCE(usefulness_score, 0)) AS avg_usefulness
            FROM intelligence_events
            WHERE detected_at >= :cutoff
            GROUP BY review_status
            """
        )
        with self.engine.connect() as conn:
            rows = [dict(r._mapping) for r in conn.execute(query, {"cutoff": cutoff})]

        counts = {
            str(row.get("review_status") or "approved").strip().lower(): int(row.get("total") or 0)
            for row in rows
        }
        total_events = sum(counts.values())
        weighted_usefulness = sum(
            float(row.get("avg_usefulness") or 0) * int(row.get("total") or 0)
            for row in rows
        )
        return {
            "hours": hours,
            "buckets": rows,
            "total_events": total_events,
            "approved_count": counts.get("approved", 0),
            "pending_count": counts.get("pending", 0),
            "rejected_count": counts.get("rejected", 0),
            "usefulness_avg": round(weighted_usefulness / total_events, 3) if total_events else 0,
            "usefulness_distribution": {},
        }

    def get_event(self, event_id: str) -> Optional[ExtractedEvent]:
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT * FROM intelligence_events WHERE event_id = :event_id LIMIT 1"
                ),
                {"event_id": event_id},
            ).first()
            if not row:
                return None
            return self._row_to_event(dict(row._mapping))

    def upsert_ingestion_status(self, agent_name: str, status: dict[str, Any]) -> None:
        now = datetime.utcnow().isoformat()
        payload = {
            "agent_name": agent_name,
            "running": 1 if status.get("running") else 0,
            "cycle_in_progress": 1 if status.get("cycle_in_progress") else 0,
            "scan_interval_seconds": status.get("scan_interval_seconds"),
            "last_started_at": status.get("last_started_at"),
            "last_completed_at": status.get("last_completed_at"),
            "last_success_at": status.get("last_success_at"),
            "last_error": status.get("last_error"),
            "last_cycle_duration_ms": status.get("last_cycle_duration_ms"),
            "last_articles_seen": int(status.get("last_articles_seen") or 0),
            "last_events_extracted": int(status.get("last_events_extracted") or 0),
            "last_events_stored": int(status.get("last_events_stored") or 0),
            "next_run_at": status.get("next_run_at"),
            "updated_at": now,
        }

        with self.engine.begin() as conn:
            existing = conn.execute(
                text(
                    "SELECT 1 FROM intelligence_ingestion_status WHERE agent_name = :agent_name LIMIT 1"
                ),
                {"agent_name": agent_name},
            ).first()

            if existing:
                conn.execute(
                    text(
                        """
                        UPDATE intelligence_ingestion_status
                        SET running = :running,
                            cycle_in_progress = :cycle_in_progress,
                            scan_interval_seconds = :scan_interval_seconds,
                            last_started_at = :last_started_at,
                            last_completed_at = :last_completed_at,
                            last_success_at = :last_success_at,
                            last_error = :last_error,
                            last_cycle_duration_ms = :last_cycle_duration_ms,
                            last_articles_seen = :last_articles_seen,
                            last_events_extracted = :last_events_extracted,
                            last_events_stored = :last_events_stored,
                            next_run_at = :next_run_at,
                            updated_at = :updated_at
                        WHERE agent_name = :agent_name
                        """
                    ),
                    payload,
                )
            else:
                conn.execute(
                    text(
                        """
                        INSERT INTO intelligence_ingestion_status (
                            agent_name, running, cycle_in_progress, scan_interval_seconds,
                            last_started_at, last_completed_at, last_success_at, last_error,
                            last_cycle_duration_ms, last_articles_seen, last_events_extracted,
                            last_events_stored, next_run_at, updated_at
                        ) VALUES (
                            :agent_name, :running, :cycle_in_progress, :scan_interval_seconds,
                            :last_started_at, :last_completed_at, :last_success_at, :last_error,
                            :last_cycle_duration_ms, :last_articles_seen, :last_events_extracted,
                            :last_events_stored, :next_run_at, :updated_at
                        )
                        """
                    ),
                    payload,
                )

    def get_ingestion_status(self, agent_name: str) -> dict[str, Any]:
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT * FROM intelligence_ingestion_status WHERE agent_name = :agent_name LIMIT 1"
                ),
                {"agent_name": agent_name},
            ).first()
            if not row:
                return {}
            data = dict(row._mapping)
            data["running"] = bool(data.get("running"))
            data["cycle_in_progress"] = bool(data.get("cycle_in_progress"))
            return data

    def get_ingestion_freshness(
        self,
        agent_name: str,
        stale_after_hours: int = 2,
    ) -> dict[str, Any]:
        status = self.get_ingestion_status(agent_name)
        last_success_raw = str(status.get("last_success_at") or "").strip()
        if not last_success_raw:
            return {
                "state": "unknown",
                "stale": True,
                "age_minutes": None,
                "last_success_at": None,
                "stale_after_hours": stale_after_hours,
            }

        try:
            last_success = datetime.fromisoformat(last_success_raw)
            age = datetime.utcnow() - last_success
            age_minutes = max(0, int(age.total_seconds() // 60))
            stale = age > timedelta(hours=stale_after_hours)
            return {
                "state": "stale" if stale else "fresh",
                "stale": stale,
                "age_minutes": age_minutes,
                "last_success_at": last_success_raw,
                "stale_after_hours": stale_after_hours,
            }
        except ValueError:
            return {
                "state": "invalid_timestamp",
                "stale": True,
                "age_minutes": None,
                "last_success_at": last_success_raw,
                "stale_after_hours": stale_after_hours,
            }

    def _row_to_event(self, row: dict[str, Any]) -> ExtractedEvent:
        return ExtractedEvent(
            event_id=row["event_id"],
            event_type=row["event_type"],
            headline=row["headline"],
            affected_commodities=self._load_json_list(row.get("affected_commodities")),
            direction=row.get("direction") or "uncertain",
            magnitude_estimate=row.get("magnitude_estimate") or "medium",
            certainty=row.get("certainty") or "likely",
            time_horizon=row.get("time_horizon") or "short",
            geographic_scope=row.get("geographic_scope") or "global",
            relevance_to_c5_c9=row.get("relevance_to_c5_c9") or "indirect",
            key_entities=self._load_json_list(row.get("key_entities")),
            key_information=row.get("key_information") or "",
            causal_chain_hint=row.get("causal_chain_hint") or "",
            source_url=row.get("source_url") or "",
            source_credibility=row.get("source_credibility") or "tier2",
            full_text=row.get("full_text") or "",
            raw_quote=row.get("raw_quote") or "",
            detected_at=row.get("detected_at") or datetime.utcnow().isoformat(),
            review_status=row.get("review_status") or "approved",
            usefulness_score=int(row.get("usefulness_score") or 0),
            reviewed_by=row.get("reviewed_by") or "",
            reviewed_at=row.get("reviewed_at") or "",
            review_note=row.get("review_note") or "",
        )

    @staticmethod
    def _load_json_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item) for item in value]
        text_value = str(value).strip()
        if not text_value:
            return []
        try:
            parsed = json.loads(text_value)
        except json.JSONDecodeError:
            return [text_value]
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
        return [str(parsed)]


intelligence_event_registry = IntelligenceEventRegistry()
