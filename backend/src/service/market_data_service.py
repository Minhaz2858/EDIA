import os
import urllib.request
from datetime import datetime
from typing import List, Dict, Optional
from backend.src.domain.knowledge_graph import domain_graph
from sqlalchemy import text
from sqlalchemy import create_engine

from backend.src.db.load_market_prices import (
    _ERP_MARKET_SERIES,
    _LZ_INTERNAL_VIEW_SPECS,
    _fetch_table_frame,
    _iter_c5_c9,
    _iter_lz_internal_view_series,
)


REAL_DATA_MAP = {
    "crude_oil": ("crude_oil", "brent"),
    "naphtha": ("naphtha", "japan_cif"),
    "cracked_c5": ("cracked_c5", "yangzi"),
    "mixed_c5": ("cracked_c5", "yangzi"),
    "cracked_c9": ("cracked_c9", "average"),
    "mixed_c9": ("cracked_c9", "average"),
    "isoprene": ("isoprene", "east_china"),
    "piperylene": ("piperylene", "east_china"),
    "dcpd": ("dcpd", "east_china"),
    "raffinate_c5": ("raffinate_c5", "erp_sale_order_weighted"),
    "sis": ("sis", "baling_1105"),
    "sis_rubber": ("sis", "baling_1105"),
    "styrene": ("styrene", "sinopec_sh"),
    "blowing_agent": ("blowing_agent", "erp_sale_order_weighted"),
    "c5_resin": ("c5_resin", "erp_sale_order_weighted"),
    "hydrogenated_c9": ("hydrogenated_c9", "partner_forecast_price"),
    "trimethylbenzene": ("trimethylbenzene", "south_china"),
    "c9_resin": ("hydrogenated_c9", "partner_forecast_price"),
    "mthpa_hardener": ("mthpa_hardener", "juhui"),
}


# External market series must come from lz_v_* views directly.
LZ_EXTERNAL_NODE_IDS = {
    "crude_oil",
    "naphtha",
    "cracked_c5",
    "mixed_c5",
    "isoprene",
    "piperylene",
    "dcpd",
    "sis",
    "sis_rubber",
    "styrene",
    "cracked_c9",
    "mixed_c9",
    "trimethylbenzene",
    "mthpa_hardener",
}


_PLATFORM_PRODUCT_DATA_MAP_CACHE: Dict[str, tuple] = {}


def _get_platform_data_map() -> Dict[str, tuple]:
    """Load data_table/data_key pairs from admin-registered platform products."""
    try:
        from backend.src.db.sqlite_db import db as _sqlite_db
        rows = _sqlite_db.list_platform_products("org-default")
        result: Dict[str, tuple] = {}
        for row in rows:
            pid = str(row.get("id") or "").strip()
            table = str(row.get("data_table") or "").strip()
            key = str(row.get("data_key") or "").strip()
            if pid and table and key:
                if table == "market_prices":
                    if key in REAL_DATA_MAP:
                        result[pid] = REAL_DATA_MAP[key]
                    elif ":" in key:
                        mp_product, mp_price_type = key.split(":", 1)
                        result[pid] = (mp_product.strip(), mp_price_type.strip() or None)
                    else:
                        result[pid] = (key, None)
                else:
                    result[pid] = (table, key)
        return result
    except Exception:
        return {}


# KNOWN GAP (v2): Platform product data bindings only support direct LZ table lookups
# via (data_table, data_key). They do NOT support:
#   - ERP weighted average: SUM(FPRICE * FQTY_ORIGIN) / SUM(FQTY_ORIGIN)
#   - Numeric guard regex filtering for non-numeric rows in LZ tables
#   - Supplier or date-range filters
# v2 schema should add: aggregation_type enum, quantity_column, filter_json,
# and numeric_guard_regex fields to the platform_products table.
# WARNING: Admins binding to an ERP table currently get a simple column lookup
# (wrong price formula). Use display_in=[] for such products until v2 is ready.
def get_effective_data_map() -> Dict[str, tuple]:
    """REAL_DATA_MAP merged with any admin-registered platform products."""
    merged = dict(REAL_DATA_MAP)
    merged.update(_get_platform_data_map())
    return merged


_LIVE_ERP_MATERIAL_NAMES = {
    product_id: material_name for product_id, _price_type, material_name in _ERP_MARKET_SERIES
}


class MarketDataService:
    def __init__(self):
        self.graph = domain_graph
        self.history: Dict[str, List[Dict]] = {}
        self._real_data_sources: set = set()  # tracks which products have real data
        self._lz_latest_regional_prices: Dict[str, Dict[str, float]] = {}
        self._lz_latest_regional_rows: Dict[str, Dict[str, Dict[str, object]]] = {}
        self._lz_regional_history: Dict[tuple, List[Dict]] = {}  # Store full regional history
        self._data_source_detail: Dict[str, str] = {}
        self._load_real_data()

    def refresh(self) -> None:
        self.history.clear()
        self._real_data_sources.clear()
        self._lz_latest_regional_prices.clear()
        self._lz_latest_regional_rows.clear()
        self._lz_regional_history.clear()
        self._data_source_detail.clear()
        self._load_real_data()

    def _append_data_source_detail(self, product_id: str, source_label: str) -> None:
        label = str(source_label or "").strip().lower()
        if not product_id or not label:
            return

        existing = self._data_source_detail.get(product_id)
        if not existing:
            self._data_source_detail[product_id] = label
            return

        tokens = [token for token in existing.split("+") if token]
        if label not in tokens:
            tokens.append(label)
        self._data_source_detail[product_id] = "+".join(tokens)

    def get_data_source_detail(self, product_id: str) -> str:
        return self._data_source_detail.get(product_id, "unknown")

    def _load_lz_direct_series(self, engine) -> Dict[tuple, List[Dict]]:
        """Load and normalize all configured lz_v_* series directly from source views."""
        series_by_key: Dict[tuple, List[Dict]] = {}

        for table_name, mappings in _LZ_INTERNAL_VIEW_SPECS:
            try:
                df = self._fetch_lz_frame_with_fallback(engine, table_name)
            except Exception:
                continue

            if table_name == "lz_v_裂解c5_data":
                merged_rows = {}
                for row in _iter_c5_c9(df):
                    merged_rows[(row["product_id"], row["price_type"], row["date"])] = row
                for row in _iter_lz_internal_view_series(df, mappings):
                    merged_rows[(row["product_id"], row["price_type"], row["date"])] = row
                row_iter = merged_rows.values()
            else:
                row_iter = _iter_lz_internal_view_series(df, mappings)

            for row in row_iter:
                key = (row["product_id"], row["price_type"])
                series_by_key.setdefault(key, []).append(
                    {"date": row["date"], "price": float(row["price"])}
                )

            # Some lz_v_裂解c5_data deployments expose no parseable date column.
            # Backfill Brent from this frame using today's date so crude_oil is visible.
            if table_name == "lz_v_裂解c5_data" and ("crude_oil", "brent") not in series_by_key:
                fallback_points = self._parse_crude_oil_from_lz_c5_frame(df)
                if fallback_points:
                    series_by_key[("crude_oil", "brent")] = fallback_points

        for (product_id, price_type), points in series_by_key.items():
            points.sort(key=lambda item: item["date"])
            if points:
                # Store full regional history
                self._lz_regional_history[(product_id, price_type)] = points
                
                # Store latest regional prices
                self._lz_latest_regional_prices.setdefault(product_id, {})[price_type] = (
                    float(points[-1]["price"])
                )
                latest_point = points[-1]
                previous_point = points[-2] if len(points) > 1 else None
                self._lz_latest_regional_rows.setdefault(product_id, {})[price_type] = {
                    "price": float(latest_point["price"]),
                    "date": str(latest_point["date"]),
                    "prev_price": None if previous_point is None else float(previous_point["price"]),
                }

        return series_by_key

    def get_regional_price_rows(self, product_id: str) -> List[Dict[str, object]]:
        mp_product = REAL_DATA_MAP.get(product_id, (product_id, None))[0]
        regional_rows = self._lz_latest_regional_rows.get(mp_product)
        if not regional_rows:
            return []

        return [
            {
                "price_type": price_type,
                "price": float(payload.get("price") or 0.0),
                "date": str(payload.get("date") or ""),
                "prev_price": payload.get("prev_price"),
            }
            for price_type, payload in regional_rows.items()
        ]
    
    def get_regional_price_history(
        self,
        product_id: str,
        price_types: List[str],
        start_date: str,
        end_date: str,
        limit: int = 100
    ) -> List[Dict[str, object]]:
        """
        Get historical price data for a product across multiple regions/price_types.
        Returns a list of dicts with date, price_type, and price.
        """
        mp_product = REAL_DATA_MAP.get(product_id, (product_id, None))[0]
        
        all_rows = []
        for price_type in price_types:
            key = (mp_product, price_type)
            if key in self._lz_regional_history:
                points = self._lz_regional_history[key]
                # Filter by date range
                filtered_points = [
                    p for p in points
                    if start_date <= str(p["date"]) <= end_date
                ]
                # Add price_type to each point
                for point in filtered_points:
                    all_rows.append({
                        "date": str(point["date"]),
                        "price_type": price_type,
                        "price": float(point["price"]),
                    })
        
        # Sort by date descending, limit results
        all_rows.sort(key=lambda x: x["date"], reverse=True)
        return all_rows[:limit]

    def _fetch_lz_frame_with_fallback(self, primary_engine, table_name: str):
        """Read an LZ view from primary DB, then optional external DB if needed."""
        primary_exc = None
        try:
            return _fetch_table_frame(primary_engine, table_name)
        except Exception as exc:
            primary_exc = exc

        external_url = (
            os.getenv("LZ_DATABASE_URL")
            or os.getenv("EXTERNAL_DATABASE_URL")
            or os.getenv("EXTERNAL_DB_URL")
        )
        if not external_url:
            raise primary_exc

        _connect_args = {"connect_timeout": 3} if "mysql" in external_url or "pymysql" in external_url else {}
        fallback_engine = create_engine(external_url, pool_pre_ping=True, connect_args=_connect_args)
        return _fetch_table_frame(fallback_engine, table_name)

    def _load_real_data(self):
        """
        Load only real data from MySQL-backed sources.
        No synthetic or simulated market series are allowed.
        Uses a single batched query instead of N separate queries.
        """
        try:
            from backend.src.db.sqlite_db import db as _app_db

            engine = _app_db.engine
            lz_series_by_key = self._load_lz_direct_series(engine)
            # Build (product_id, price_type) pairs for all entries in REAL_DATA_MAP
            targets = [
                (node_id, mp_product, mp_price_type)
                for node_id, (mp_product, mp_price_type) in REAL_DATA_MAP.items()
            ]
            if not targets:
                return

            # Build a lookup from (mp_product, mp_price_type) → [node_ids].
            # Some node IDs are aliases of the same source pair (e.g. cracked_c5/mixed_c5),
            # so we must retain all of them instead of letting later entries overwrite earlier ones.
            key_to_nodes: dict = {}
            for node_id, mp_product, mp_price_type in targets:
                key = (mp_product, mp_price_type)
                key_to_nodes.setdefault(key, []).append(node_id)

            by_node: dict = {}
            market_prices_targets = []

            for node_id, mp_product, mp_price_type in targets:
                source_key = (mp_product, mp_price_type)
                if node_id in LZ_EXTERNAL_NODE_IDS:
                    points = lz_series_by_key.get(source_key, [])
                    if (
                        not points
                        and node_id == "crude_oil"
                        and mp_price_type == "brent"
                    ):
                        points = self._load_crude_oil_from_lz_c5_view(engine)
                    if (
                        not points
                        and node_id == "crude_oil"
                        and mp_price_type == "brent"
                    ):
                        points = self._load_crude_oil_from_oilchem_data(engine)
                    if (
                        not points
                        and node_id == "crude_oil"
                        and mp_price_type == "brent"
                    ):
                        points = self._load_crude_oil_from_public_proxy()
                    if points:
                        by_node[node_id] = [dict(item) for item in points]
                        self._append_data_source_detail(node_id, "lz_view")
                        # Keep LZ-only behavior for non-crude products.
                        # For crude_oil, also merge DB history from market_prices.
                        if node_id != "crude_oil":
                            continue
                    market_prices_targets.append((node_id, mp_product, mp_price_type))
                    continue
                if node_id in _LIVE_ERP_MATERIAL_NAMES:
                    points = self._load_history_from_erp_sale_orders(
                        product_id=node_id,
                        material_name=_LIVE_ERP_MATERIAL_NAMES[node_id],
                    )
                    if points:
                        by_node[node_id] = [dict(item) for item in points]
                        self._append_data_source_detail(node_id, "erp_live")
                        continue
                market_prices_targets.append((node_id, mp_product, mp_price_type))

            if market_prices_targets:
                # Single query: fetch non-LZ products from market_prices.
                conditions = " OR ".join(
                    f"(product_id = :pid{i} AND price_type = :pt{i})"
                    for i in range(len(market_prices_targets))
                )
                params = {}
                for i, (_, mp_product, mp_price_type) in enumerate(
                    market_prices_targets
                ):
                    params[f"pid{i}"] = mp_product
                    params[f"pt{i}"] = mp_price_type

                with engine.connect() as conn:
                    rows = conn.execute(
                        text(
                            f"SELECT product_id, price_type, date, price "
                            f"FROM market_prices "
                            f"WHERE ({conditions}) AND price IS NOT NULL "
                            f"ORDER BY date ASC"
                        ),
                        params,
                    ).fetchall()

                # Group rows by node_id
                for pid, pt, dt, price in rows:
                    node_ids = key_to_nodes.get((pid, pt), [])
                    for node_id in node_ids:
                        # Most LZ external products should only use lz_v_* sources.
                        # crude_oil is allowed to fall back to market_prices when LZ is unavailable.
                        if node_id in LZ_EXTERNAL_NODE_IDS and node_id != "crude_oil":
                            continue
                        by_node.setdefault(node_id, []).append(
                            {"date": str(dt), "price": float(price)}
                        )
                        self._append_data_source_detail(node_id, "mysql")

            for node_id, history in by_node.items():
                # Normalize order and deduplicate same-day rows.
                by_date: dict[str, float] = {}
                for row in history:
                    date_value = str(row.get("date") or "").strip()[:10]
                    price_value = row.get("price")
                    if not date_value:
                        continue
                    try:
                        by_date[date_value] = float(price_value)
                    except (TypeError, ValueError):
                        continue
                normalized = [
                    {"date": d, "price": p}
                    for d, p in sorted(by_date.items(), key=lambda item: item[0])
                ]
                if not normalized:
                    continue
                self.history[node_id] = normalized
                self._real_data_sources.add(node_id)

            print(
                "Real market data loaded: "
                + ", ".join(
                    f"{k}({len(self.history[k])} pts)"
                    for k in REAL_DATA_MAP
                    if k in self.history and self.history[k]
                )
            )
        except Exception as e:
            print(f"Warning: Could not load real market data: {e}")

    def get_price_history(
        self, product_id: str, cutoff_date: Optional[str] = None
    ) -> List[Dict]:
        history = self.history.get(product_id, [])
        if not history:
            _eff_map = get_effective_data_map()
            if product_id == "crude_oil":
                mp_product, mp_price_type = _eff_map.get(
                    product_id, (product_id, None)
                )
                if mp_price_type == "brent":
                    try:
                        from backend.src.db.sqlite_db import db as _app_db

                        history = self._load_crude_oil_from_lz_c5_view(_app_db.engine)
                    except Exception:
                        history = []
                    if not history:
                        try:
                            from backend.src.db.sqlite_db import db as _app_db

                            history = self._load_crude_oil_from_oilchem_data(
                                _app_db.engine
                            )
                        except Exception:
                            history = []
                    if not history:
                        history = self._load_crude_oil_from_public_proxy()
            if not history and product_id in _LIVE_ERP_MATERIAL_NAMES:
                history = self._load_history_from_erp_sale_orders(
                    product_id=product_id,
                    material_name=_LIVE_ERP_MATERIAL_NAMES[product_id],
                )
            mp_product, mp_price_type = _eff_map.get(
                product_id, (product_id, None)
            )
            if not history and mp_product:
                history = self._load_history_from_market_prices(
                    product_id=product_id,
                    mp_product=mp_product,
                    mp_price_type=mp_price_type,
                )
        if cutoff_date:
            history = [r for r in history if r["date"] <= cutoff_date]
        return history

    def is_real_data(self, product_id: str) -> bool:
        """Returns True if this product's data comes from real market prices."""
        return product_id in self._real_data_sources

    def get_latest_price(self, product_id: str) -> float:
        history = self.get_price_history(product_id)
        if history:
            return history[-1]["price"]
        return 0.0

    def get_price_on_date(self, product_id: str, target_date: str) -> Optional[float]:
        """Return the exact or nearest observed price within +/- 3 days."""
        history = self.get_price_history(product_id)
        if not history:
            return None

        target_dt = datetime.fromisoformat(target_date[:10])

        exact_match = next(
            (float(point["price"]) for point in history if point.get("date") == target_date),
            None,
        )
        if exact_match is not None:
            return exact_match

        nearby = [
            point
            for point in history
            if point.get("date") is not None
            and abs((datetime.fromisoformat(str(point["date"])[:10]) - target_dt).days) <= 3
        ]
        if not nearby:
            return None

        nearby.sort(
            key=lambda point: abs((datetime.fromisoformat(str(point["date"])[:10]) - target_dt).days)
        )
        return float(nearby[0]["price"])

    def _load_history_from_market_prices(
        self, product_id: str, mp_product: str, mp_price_type: Optional[str]
    ) -> List[Dict]:
        """Fallback loader: hydrate a product series directly from market_prices."""
        try:
            from backend.src.db.sqlite_db import db as _app_db

            engine = _app_db.engine
            price_type_filter = ""
            params = {"pid": mp_product}
            if mp_price_type:
                price_type_filter = "  AND price_type = :ptype "
                params["ptype"] = mp_price_type
            with engine.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT date, price "
                        "FROM market_prices "
                        "WHERE product_id = :pid "
                        f"{price_type_filter}"
                        "  AND price IS NOT NULL "
                        "ORDER BY date ASC"
                    ),
                    params,
                ).fetchall()

            hydrated = [
                {"date": str(row[0]), "price": float(row[1])}
                for row in rows
                if row[0] is not None and row[1] is not None
            ]
            if hydrated:
                self.history[product_id] = hydrated
                self._real_data_sources.add(product_id)
                self._append_data_source_detail(product_id, "mysql")
            return hydrated
        except Exception:
            return []

    def _load_history_from_erp_sale_orders(
        self, product_id: str, material_name: str
    ) -> List[Dict]:
        """Hydrate a product series directly from ERP sale orders for freshest weighted pricing."""
        try:
            from backend.src.db.sqlite_db import db as _app_db

            engine = _app_db.engine
            with engine.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT DATE(PLANDATE) AS biz_date, "
                        "SUM(COALESCE(FDELIQTY, 0) * COALESCE(FTAXPRICE, FPRICE, 0)) "
                        "  / NULLIF(SUM(COALESCE(FDELIQTY, 0)), 0) AS weighted_price "
                        "FROM erp_v_sale_orderentry "
                        "WHERE material_name = :material_name "
                        "  AND PLANDATE IS NOT NULL "
                        "GROUP BY DATE(PLANDATE) "
                        "ORDER BY DATE(PLANDATE) ASC"
                    ),
                    {"material_name": material_name},
                ).fetchall()

            hydrated = [
                {"date": str(row[0]), "price": float(row[1])}
                for row in rows
                if row[0] is not None and row[1] is not None
            ]
            if hydrated:
                self.history[product_id] = hydrated
                self._real_data_sources.add(product_id)
                self._append_data_source_detail(product_id, "erp_live")
            return hydrated
        except Exception:
            return []

    def _load_crude_oil_from_lz_c5_view(self, engine) -> List[Dict]:
        """Fallback specifically for Brent from internal LZ C5 view."""
        try:
            df = self._fetch_lz_frame_with_fallback(engine, "lz_v_裂解c5_data")
            points = [
                {"date": row["date"], "price": float(row["price"])}
                for row in _iter_c5_c9(df)
                if row.get("product_id") == "crude_oil" and row.get("price_type") == "brent"
            ]
            if not points:
                points = self._parse_crude_oil_from_lz_c5_frame(df)
            if points:
                self.history["crude_oil"] = points
                self._real_data_sources.add("crude_oil")
                self._append_data_source_detail("crude_oil", "lz_view")
                self._lz_latest_regional_prices.setdefault("crude_oil", {})[
                    "brent"
                ] = float(points[-1]["price"])
            return points
        except Exception:
            return []

    def _load_crude_oil_from_oilchem_data(self, engine) -> List[Dict]:
        """Fallback for Brent from synced oilchem_data table."""
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT price_date, brent_crude "
                        "FROM oilchem_data "
                        "WHERE price_date IS NOT NULL "
                        "  AND brent_crude IS NOT NULL "
                        "ORDER BY price_date ASC"
                    )
                ).fetchall()

            points = [
                {"date": str(row[0])[:10], "price": float(row[1])}
                for row in rows
                if row[0] is not None and row[1] is not None
            ]

            if points:
                self.history["crude_oil"] = points
                self._real_data_sources.add("crude_oil")
                self._append_data_source_detail("crude_oil", "oilchem_data")
                self._lz_latest_regional_prices.setdefault("crude_oil", {})[
                    "brent"
                ] = float(points[-1]["price"])
            return points
        except Exception:
            return []

    def _load_crude_oil_from_public_proxy(self) -> List[Dict]:
        """Last-resort fallback: pull a live crude quote when DB Brent is unavailable."""
        try:
            with urllib.request.urlopen(
                "https://stooq.com/q/l/?s=cl.f&i=d"
            ) as response:
                raw = response.read().decode("utf-8", errors="ignore").strip()

            if not raw:
                return []

            parts = [p.strip() for p in raw.split(",")]
            if len(parts) < 7:
                return []

            date_raw = parts[1]
            close_raw = parts[6]
            if not date_raw or close_raw in {"", "N/D"}:
                return []

            if len(date_raw) == 8 and date_raw.isdigit():
                date_value = f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:8]}"
            else:
                date_value = str(datetime.utcnow().date())

            price_value = float(close_raw)
            points = [{"date": date_value, "price": price_value}]
            self.history["crude_oil"] = points
            self._real_data_sources.add("crude_oil")
            self._append_data_source_detail("crude_oil", "public_proxy")
            self._lz_latest_regional_prices.setdefault("crude_oil", {})[
                "brent"
            ] = price_value
            return points
        except Exception:
            return []

    def _parse_crude_oil_from_lz_c5_frame(self, df) -> List[Dict]:
        """Parse Brent rows from lz_v_裂解c5_data with tolerant schema handling."""
        try:
            from datetime import date as _date
            import pandas as pd

            def _norm(value: object) -> str:
                return "".join(str(value or "").strip().lower().split())

            def _find_col_contains_local(df, *candidates: str):
                normalized_columns = [(_norm(col), col) for col in df.columns]
                for candidate in candidates:
                    target = _norm(candidate)
                    for normalized_column, original_column in normalized_columns:
                        if target and target in normalized_column:
                            return original_column
                return None

            def _to_float(value: object) -> Optional[float]:
                if value is None:
                    return None
                text_value = str(value).strip().replace(",", "")
                if not text_value:
                    return None
                try:
                    return float(text_value)
                except (TypeError, ValueError):
                    return None

            supplier_col = _find_col_contains_local(
                df,
                "supplier_name",
                "厂商名称",
                "supplier",
                "fsupplier_name",
            )
            material_col = _find_col_contains_local(
                df,
                "material_name",
                "品名",
                "产品名称",
                "fmaterial_name",
            )
            price_col = _find_col_contains_local(
                df,
                "tax_price",
                "taxprice",
                "price",
                "含税价",
                "单价",
            )
            date_col = _find_col_contains_local(
                df,
                "biz_date",
                "fdate",
                "date",
                "业务日期",
                "统计日期",
                "报价日期",
            )

            if (supplier_col is None and material_col is None) or price_col is None:
                return []

            parsed_rows: List[Dict] = []
            for _, row in df.iterrows():
                supplier_value = _norm(row.get(supplier_col)) if supplier_col else ""
                material_value = _norm(row.get(material_col)) if material_col else ""
                text_blob = f"{supplier_value} {material_value}".strip()
                if not text_blob:
                    continue
                if not any(
                    token in text_blob
                    for token in ("布伦特", "布伦", "brent")
                ):
                    continue

                price = _to_float(row.get(price_col))
                if price is None:
                    continue

                row_date = None
                if date_col is not None:
                    raw_date = row.get(date_col)
                    parsed_date = pd.to_datetime(raw_date, errors="coerce")
                    if not pd.isna(parsed_date):
                        row_date = parsed_date.strftime("%Y-%m-%d")
                if row_date is None:
                    row_date = _date.today().isoformat()

                parsed_rows.append({"date": row_date, "price": float(price)})

            parsed_rows.sort(key=lambda item: item["date"])
            points = parsed_rows
            if points:
                # Deduplicate same-day rows: keep the last seen value for each date.
                by_date = {item["date"]: float(item["price"]) for item in points}
                points = [
                    {"date": d, "price": p}
                    for d, p in sorted(by_date.items(), key=lambda item: item[0])
                ]
            return points
        except Exception:
            return []

    def get_regional_prices(self, product_id: str) -> Dict[str, float]:
        """Return all available price types with their latest price for a product."""
        try:
            from backend.src.db.sqlite_db import db as _app_db

            engine = _app_db.engine
            mp_product = REAL_DATA_MAP.get(product_id, (product_id, None))[0]
            if product_id in LZ_EXTERNAL_NODE_IDS:
                lz_prices = self._lz_latest_regional_prices.get(mp_product)
                if lz_prices:
                    return dict(lz_prices)

            with engine.connect() as conn:
                result = conn.execute(
                    text(
                        "SELECT mp1.price_type, mp1.price "
                        "FROM market_prices mp1 "
                        "WHERE mp1.product_id = :pid "
                        "  AND mp1.date = ("
                        "    SELECT MAX(mp2.date) FROM market_prices mp2 "
                        "    WHERE mp2.product_id = mp1.product_id "
                        "      AND mp2.price_type = mp1.price_type"
                        "  ) AND mp1.price IS NOT NULL"
                    ),
                    {"pid": mp_product},
                )
                return {row[0]: float(row[1]) for row in result}
        except Exception:
            return {}

    def calculate_spreads(self) -> List[Dict]:
        """
        Calculates key industry spreads (margins).
        """
        spreads = []

        # 1. Cracked C5 - Naphtha spread
        c5_base_price = self.get_latest_price("cracked_c5")
        nap_price = self.get_latest_price("naphtha")
        if c5_base_price and nap_price:
            spreads.append(
                {
                    "id": "spread_cracked_c5_naphtha",
                    "name": "Cracked C5-Naphtha Spread",
                    "name_cn": "裂解碳五-石脑油价差",
                    "value": round(c5_base_price - nap_price, 2),
                    "unit": "USD/ton",
                    "description": "反映裂解碳五相对石脑油的加工价差。",
                }
            )

        # 2. Isoprene Separation Spread (Isoprene - Cracked C5)
        iso_price = self.get_latest_price("isoprene")
        c5_price = self.get_latest_price("cracked_c5")
        if iso_price and c5_price:
            spreads.append(
                {
                    "id": "spread_isoprene_c5",
                    "name": "Isoprene-Cracked C5 Spread",
                    "name_cn": "异戊二烯-裂解碳五价差",
                    "value": round(iso_price - c5_price, 2),
                    "unit": "USD/ton",
                    "description": "反映异戊二烯相对裂解碳五的加工盈利空间。",
                }
            )

        return spreads


# Singleton
market_data = MarketDataService()
