from typing import List, Optional
from dataclasses import dataclass, asdict
from backend.src.prompts.task_prompt import TaskType

@dataclass
class UserContext:
    product: Optional[str] = None
    industry: Optional[str] = None
    period: Optional[str] = None
    goal: Optional[str] = None
    region: Optional[str] = None
    user_role: Optional[str] = None
    additional_info: Optional[str] = None  # New field for RAG content
    visualization: Optional[dict] = None

    def to_dict(self):
        return {k: v for k, v in self.__dict__.items() if v is not None}

@dataclass
class ParsedIntent:
    original_query: str
    task_type: TaskType
    confidence: float
