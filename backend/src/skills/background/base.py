"""Background Intelligence Skills – base infrastructure.

Background skills run on a periodic schedule (not per-request).  They
analyse market data, intelligence events, ERP KPIs, and historical
decisions asynchronously and persist their findings so that the online
pipeline can consume pre-computed insights at query time.

Architecture:
  BackgroundSkill  (ABC)
    ├── run()           – main execution (called by scheduler)
    ├── name            – unique identifier
    ├── interval_minutes– how often to run
    └── get_latest()    – retrieve last result for injection into pipeline

  BackgroundSkillScheduler
    ├── register(skill) – add a skill
    ├── start()         – launch asyncio tasks
    └── stop()          – cancel all tasks
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SkillResult:
    """Standardised output of a background skill run."""
    skill_name: str
    ran_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: str = "success"          # success | error | skipped
    summary: str = ""                # human-readable headline
    data: dict[str, Any] = field(default_factory=dict)
    alerts: list[str] = field(default_factory=list)  # items injected into next report


class BackgroundSkill(ABC):
    """Contract for a background intelligence skill."""

    name: str = "background_skill"
    interval_minutes: int = 60       # default: hourly

    @abstractmethod
    def run(self) -> SkillResult:
        """Execute the skill.  Called periodically by the scheduler."""
        raise NotImplementedError

    def get_latest(self) -> SkillResult | None:
        """Return the most recent result (cached by scheduler)."""
        return _latest_results.get(self.name)


# Global result store – lightweight in-memory cache
_latest_results: dict[str, SkillResult] = {}


class BackgroundSkillScheduler:
    """Manages lifecycle of all registered background skills."""

    def __init__(self) -> None:
        self._skills: list[BackgroundSkill] = []
        self._tasks: list[asyncio.Task] = []
        self._running = False

    def register(self, skill: BackgroundSkill) -> None:
        self._skills.append(skill)

    @property
    def skills(self) -> list[BackgroundSkill]:
        return list(self._skills)

    def start(self) -> None:
        """Create one asyncio task per skill."""
        if self._running:
            return
        self._running = True
        for skill in self._skills:
            task = asyncio.create_task(self._loop(skill))
            self._tasks.append(task)
            logger.info(
                "Background skill '%s' scheduled every %d min",
                skill.name,
                skill.interval_minutes,
            )

    def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()

    # Provide the latest results for pipeline injection
    def get_all_latest(self) -> dict[str, SkillResult]:
        return dict(_latest_results)

    def get_skill_result(self, name: str) -> SkillResult | None:
        return _latest_results.get(name)

    # ------------------------------------------------------------------

    async def _loop(self, skill: BackgroundSkill) -> None:
        """Run a single skill periodically."""
        # Small jitter so skills don't all fire at the same instant
        await asyncio.sleep(5)
        loop = asyncio.get_event_loop()
        while self._running:
            try:
                result = await loop.run_in_executor(None, skill.run)
                _latest_results[skill.name] = result
                logger.info(
                    "Skill '%s' completed: %s – %s",
                    skill.name,
                    result.status,
                    result.summary[:120] if result.summary else "",
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Skill '%s' failed: %s", skill.name, exc)
                _latest_results[skill.name] = SkillResult(
                    skill_name=skill.name,
                    status="error",
                    summary=str(exc)[:200],
                )
            await asyncio.sleep(skill.interval_minutes * 60)


# Singleton scheduler
background_skill_scheduler = BackgroundSkillScheduler()
