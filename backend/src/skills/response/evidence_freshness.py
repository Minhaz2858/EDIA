from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.src.service.types import DecisionReport, QualityFlag
from .base import ResponseSkill


class EvidenceFreshnessSkill(ResponseSkill):
    name = "evidence_freshness"

    def run(self, report: DecisionReport, state: dict[str, Any]) -> DecisionReport:
        snapshot = state.get("perception_snapshot") or {}
        reference_date = str(snapshot.get("reference_date") or "").strip()
        if not reference_date:
            report.enhancement_applied[self.name] = "skipped:no_reference_date"
            return report

        try:
            ref_dt = datetime.strptime(reference_date[:10], "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
            now_dt = datetime.now(timezone.utc)
            age_days = (now_dt.date() - ref_dt.date()).days
        except Exception:
            report.enhancement_applied[self.name] = "skipped:parse_error"
            return report

        if age_days <= 1:
            report.enhancement_applied[self.name] = "passed"
            return report

        message = f"market reference data is {age_days} day(s) old"
        report.quality_flags.append(
            QualityFlag(
                code="stale_evidence",
                severity="medium" if age_days <= 3 else "high",
                message=message,
                source=self.name,
            )
        )
        report.alerts_prepended.append(
            f"⚠️ Data freshness warning: key market snapshot is {age_days} day(s) old."
        )
        report.enhancement_applied[self.name] = "flagged"
        return report
