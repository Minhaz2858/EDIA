from enum import Enum
from typing import List, Optional, Dict
from pydantic import BaseModel

class ProductType(str, Enum):
    CRUDE = "Crude Oil"
    FEEDSTOCK = "Feedstock"  # Naphtha
    OLEFIN = "Olefin"        # Ethylene, Propylene
    C5_MIX = "Mixed C5"
    C5_DERIVATIVE = "C5 Derivative"
    C9_DERIVATIVE = "C9 Derivative"
    DOWNSTREAM = "Downstream Application"

class ProductNode(BaseModel):
    id: str
    name_en: str
    name_cn: str
    type: ProductType
    description: Optional[str] = None
    
    # Economics
    typical_price_range: Optional[str] = None  # e.g., "800-1200 USD"
    unit: str = "ton"

class RelationshipType(str, Enum):
    PRODUCES = "produces"        # Cracking/Separation
    CONSUMES = "consumes"        # Downstream usage
    SUBSTITUTE = "substitute"    # Competition

class Relationship(BaseModel):
    source_id: str
    target_id: str
    relation: RelationshipType
    yield_rate: Optional[float] = None  # e.g., 1 ton Naphtha -> 0.15 ton C5
    description: Optional[str] = None

class KnowledgeGraph(BaseModel):
    nodes: List[ProductNode]
    edges: List[Relationship]
