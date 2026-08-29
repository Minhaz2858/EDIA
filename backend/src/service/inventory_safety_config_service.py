from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from sqlalchemy import text

from backend.src.db.sqlite_db import db


@dataclass
class InventorySafetyConfig:
    product_id: str
    name_cn: str
    safety_min: float
    safety_max: float
    daily_consumption: float


class InventorySafetyConfigService:
    def get_config_map(self) -> Dict[str, InventorySafetyConfig]:
        stmt = text(
            """
            SELECT product_id, name_cn, safety_min, safety_max, daily_consumption
            FROM inventory_safety_config
            ORDER BY product_id ASC
            """
        )
        with db.engine.connect() as conn:
            rows = conn.execute(stmt).mappings().fetchall()

        return {
            str(row["product_id"]): InventorySafetyConfig(
                product_id=str(row["product_id"]),
                name_cn=str(row["name_cn"]),
                safety_min=float(row["safety_min"]),
                safety_max=float(row["safety_max"]),
                daily_consumption=float(row["daily_consumption"]),
            )
            for row in rows
        }


inventory_safety_config_service = InventorySafetyConfigService()