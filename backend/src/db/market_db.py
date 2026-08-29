import os
from typing import Dict, List, Optional

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.engine import Engine

from backend.src.db.engine_factory import get_engine


load_dotenv()


class MarketDB:
    """Read historical market prices from the configured MySQL database.

    This provides a thin layer over the ingested three_years.xlsx data so that
    services can consume a unified [{"date", "price"}, ...] structure.
    """

    def __init__(self, db_url: Optional[str] = None) -> None:
        if db_url is None:
            db_url = os.getenv("DATABASE_URL")
        if not db_url:
            raise RuntimeError("DATABASE_URL must be set for cloud DB mode.")

        self.engine: Engine = get_engine(db_url)

        # Mapping from internal product_id -> (table_name, value_column)
        self.mapping: Dict[str, Dict[str, str]] = {
            # Upstream / midstream
            "naphtha": {
                "table": "裂解C5",
                "value_col": "日本石脑油Japanese naphtha",
            },
            "mixed_c5": {
                "table": "裂解C5",
                "value_col": "扬子裂解C5Yangzi Cracking C5",
            },
            # C5 derivatives
            "isoprene": {
                "table": "异戊二烯",
                "value_col": "华东市场价East China market price",
            },
            "piperylene": {
                "table": "间戊二烯",
                "value_col": "华东市场价East China market price",
            },
            "dcpd": {
                "table": "双环戊二烯",
                "value_col": "华东市场价East China market price",
            },
            # Downstream
            "sis_rubber": {
                "table": "SIS",
                "value_col": "巴陵1105Baling 1105",
            },
        }

    def is_available(self) -> bool:
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    def get_price_history(self, product_id: str) -> List[Dict]:
        cfg = self.mapping.get(product_id)
        if not cfg:
            return []

        table = cfg["table"]
        value_col = cfg["value_col"]

        try:
            with self.engine.connect() as conn:
                result = conn.execute(text(f"SELECT * FROM `{table}`"))
                rows = result.fetchall()
                cols = list(result.keys())
        except Exception:
            return []

        # First column is the date index from Excel (often named 'Unnamed: 0')
        if not cols or value_col not in cols:
            return []

        date_idx = 0
        value_idx = cols.index(value_col)

        out: List[Dict] = []
        for r in rows:
            raw_date = str(r[date_idx])
            # Normalise date to YYYY-MM-DD where possible
            date = raw_date.replace("/", "-")[:10]
            price = r[value_idx]
            if price is None:
                continue
            try:
                price_val = float(price)
            except (TypeError, ValueError):
                continue
            out.append({"date": date, "price": round(price_val, 2)})

        return out


market_db = MarketDB()
