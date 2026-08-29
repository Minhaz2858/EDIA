from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from backend.src.service.types import DecisionReport


class ResponseSkill(ABC):
    """Base contract for lightweight response enhancement skills."""

    name: str = "response_skill"

    @abstractmethod
    def run(self, report: DecisionReport, state: dict[str, Any]) -> DecisionReport:
        """Apply enhancement in-place and return the report."""
        raise NotImplementedError
