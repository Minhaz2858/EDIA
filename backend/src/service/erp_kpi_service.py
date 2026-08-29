from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, Optional

from sqlalchemy import text

from backend.src.db.sqlite_db import db


@dataclass
class ProductKPI:
    product_id: str
    region: str
    inventory_tons: Optional[float] = None
    inventory_days: Optional[float] = None
    sales_30d_ton: Optional[float] = None
    utilization_pct: Optional[float] = None


_PRODUCT_ALIASES: Dict[str, str] = {
    "mixed_c5": "cracked_c5",
    "mixed_c9": "cracked_c9",
    "sis_rubber": "sis",
    "c9_resin": "hydrogenated_c9",
    "adhesive_resin": "c5_resin",
    "road_marking_resin": "c5_resin",
    "mthpa": "mthpa_hardener",
}

_PRODUCT_TO_MATERIAL_NAMES: Dict[str, tuple[str, ...]] = {
    "isoprene": ("异戊二烯",),
    "piperylene": ("间戊二烯", "顺式间戊二烯产品"),
    "dcpd": ("双环戊二烯",),
    "cracked_c5": ("工业用裂解碳五", "裂解碳五"),
    "cracked_c9": ("工业用裂解碳九",),
    "raffinate_c5": ("抽余碳五", "戊烷发泡剂"),
    "sis": ("SIS", "热塑性橡胶"),
    "c5_resin": ("碳五石油树脂",),
    "blowing_agent": ("戊烷发泡剂",),
    "styrene": ("苯乙烯",),
    "hydrogenated_c9": ("加氢碳九",),
    "trimethylbenzene": ("混三甲苯",),
    "mthpa_hardener": ("固化剂", "甲基四氢苯酐"),
}


def _canonical_product_id(product_id: str) -> str:
    return _PRODUCT_ALIASES.get(product_id, product_id)


def _material_name_params(
    material_names: tuple[str, ...],
) -> tuple[str, Dict[str, Any]]:
    placeholders = []
    params: Dict[str, Any] = {}
    for idx, name in enumerate(material_names):
        key = f"name_{idx}"
        placeholders.append(f":{key}")
        params[key] = name
    return ", ".join(placeholders), params


class ErpKPIService:
    """On-demand ERP KPI reader backed by the active application database."""

    _latest_date_cache: Optional[date] = None

    @classmethod
    def invalidate_latest_date_cache(cls) -> None:
        """Clear cached latest ERP date so subsequent reads use fresh rows."""
        cls._latest_date_cache = None

    def _get_latest_date(self) -> date:
        """Get the latest PLANDATE in the sales table (cached). Falls back to today()."""
        if ErpKPIService._latest_date_cache is not None:
            return ErpKPIService._latest_date_cache
        try:
            with db.engine.connect() as conn:
                val = conn.execute(
                    text(
                        "SELECT MAX(DATE(PLANDATE)) FROM erp_v_sale_orderentry WHERE PLANDATE IS NOT NULL"
                    )
                ).scalar()
                if val:
                    ErpKPIService._latest_date_cache = (
                        val
                        if isinstance(val, date)
                        else date.fromisoformat(str(val)[:10])
                    )
                    return ErpKPIService._latest_date_cache
        except Exception:
            pass
        return date.today()

    def get_product_kpi(
        self, product_id: str, region: str = "east_china"
    ) -> Optional[ProductKPI]:
        canonical_product_id = _canonical_product_id(product_id)
        material_names = _PRODUCT_TO_MATERIAL_NAMES.get(canonical_product_id)
        if not material_names:
            return None

        sales_30d_ton = self._query_sales_30d(material_names)
        inventory_tons = self._query_inventory_tons(material_names)
        utilization_pct = self._query_utilization_pct(material_names)

        if sales_30d_ton is None and inventory_tons is None and utilization_pct is None:
            return None

        inventory_days = None
        if (
            inventory_tons is not None
            and sales_30d_ton is not None
            and sales_30d_ton > 0
        ):
            inventory_days = round(inventory_tons / (sales_30d_ton / 30.0), 2)

        return ProductKPI(
            product_id=canonical_product_id,
            region=region,
            inventory_tons=inventory_tons,
            inventory_days=inventory_days,
            sales_30d_ton=sales_30d_ton,
            utilization_pct=utilization_pct,
        )

    def get_inventory(self, product_id: str) -> Dict[str, Any]:
        canonical_product_id = _canonical_product_id(product_id)
        material_names = _PRODUCT_TO_MATERIAL_NAMES.get(canonical_product_id)
        if not material_names:
            return {
                "found": False,
                "reason": f"No ERP mapping for {canonical_product_id}",
                "product_id": canonical_product_id,
            }

        inventory_tons = self._query_inventory_tons(material_names)
        if inventory_tons is None:
            return {
                "found": False,
                "reason": f"No inventory rows for {material_names}",
                "product_id": canonical_product_id,
            }

        sales_30d_ton = self._query_sales_30d(material_names)
        daily_rate = (
            (sales_30d_ton / 30.0) if sales_30d_ton and sales_30d_ton > 0 else None
        )
        inventory_days = (inventory_tons / daily_rate) if daily_rate else None

        return {
            "found": True,
            "product_id": canonical_product_id,
            "total_tons": round(float(inventory_tons), 2),
            "inventory_days": (
                round(float(inventory_days), 1) if inventory_days else None
            ),
            "sales_30d_tons": round(float(sales_30d_ton), 2) if sales_30d_ton else None,
        }

    def _query_sales_period(
        self,
        material_names: tuple[str, ...],
        days_ago_start: int,
        days_ago_end: int = 0,
    ) -> Optional[float]:
        """Sum FDELIQTY between (ref_date - days_ago_start) and (ref_date - days_ago_end)."""
        placeholders, params = _material_name_params(material_names)
        ref = self._get_latest_date()
        params["start_date"] = ref - timedelta(days=days_ago_start)
        params["end_date"] = ref - timedelta(days=days_ago_end)
        try:
            with db.engine.connect() as conn:
                value = conn.execute(
                    text(
                        "SELECT SUM(COALESCE(FDELIQTY, 0)) "
                        "FROM erp_v_sale_orderentry "
                        f"WHERE material_name IN ({placeholders}) "
                        "AND PLANDATE IS NOT NULL "
                        "AND DATE(PLANDATE) >= :start_date "
                        "AND DATE(PLANDATE) <= :end_date"
                    ),
                    params,
                ).scalar()
        except Exception:
            return None
        if value is None:
            return None
        return round(float(value), 2)

    def _query_sales_30d(self, material_names: tuple[str, ...]) -> Optional[float]:
        return self._query_sales_period(material_names, 30, 0)

    def get_sales_growth_pct(self, product_id: str) -> Optional[float]:
        """Compare current 30d sales vs previous 30d sales. Returns % change."""
        canonical = _canonical_product_id(product_id)
        material_names = _PRODUCT_TO_MATERIAL_NAMES.get(canonical)
        if not material_names:
            return None
        current = self._query_sales_period(material_names, 30, 0)
        previous = self._query_sales_period(material_names, 60, 31)
        if current is None or previous is None or previous <= 0:
            return None
        return round((current - previous) / previous * 100, 2)

    def get_sales_window_bulk(
        self,
        period_days: int,
    ) -> tuple[Dict[str, Optional[float]], Dict[str, Optional[float]]]:
        """Fetch current sales total and growth vs previous equal-length window."""
        sales_totals: Dict[str, Optional[float]] = {
            p: None for p in _PRODUCT_TO_MATERIAL_NAMES
        }
        growth_pct: Dict[str, Optional[float]] = {
            p: None for p in _PRODUCT_TO_MATERIAL_NAMES
        }
        if period_days <= 0:
            return sales_totals, growth_pct

        mat_to_product: Dict[str, str] = {}
        all_names: list[str] = []
        for prod_id, names in _PRODUCT_TO_MATERIAL_NAMES.items():
            for name in names:
                mat_to_product[name] = prod_id
                all_names.append(name)

        if not all_names:
            return sales_totals, growth_pct

        ref = self._get_latest_date()
        current_start = ref - timedelta(days=period_days - 1)
        previous_start = ref - timedelta(days=(2 * period_days) - 1)
        previous_end = ref - timedelta(days=period_days)

        ph = ", ".join(f":n{i}" for i in range(len(all_names)))
        base_params: Dict[str, Any] = {
            f"n{i}": name for i, name in enumerate(all_names)
        }

        # Fetch raw rows and aggregate in Python — erp_v_sale_orderentry has
        # NO indexes, so MySQL GROUP BY takes 120s+. Python aggregation: ~3.5s.
        current_sales: Dict[str, float] = {}
        previous_sales: Dict[str, float] = {}
        try:
            with db.engine.connect() as conn:
                raw_rows = conn.execute(
                    text(
                        f"SELECT material_name, COALESCE(FDELIQTY, 0) AS qty, "
                        f"       DATE(PLANDATE) AS d "
                        f"FROM erp_v_sale_orderentry "
                        f"WHERE material_name IN ({ph}) "
                        f"AND PLANDATE IS NOT NULL "
                        f"AND DATE(PLANDATE) >= :prev_start "
                        f"AND DATE(PLANDATE) <= :ref_date"
                    ),
                    {**base_params, "prev_start": previous_start, "ref_date": ref},
                ).fetchall()
            for mat, qty, d in raw_rows:
                if qty is None:
                    continue
                prod = mat_to_product.get(mat)
                if not prod:
                    continue
                val = float(qty)
                d_str = str(d) if d else ""
                if d_str >= str(current_start):
                    current_sales[prod] = round(current_sales.get(prod, 0.0) + val, 4)
                if d_str <= str(previous_end):
                    previous_sales[prod] = round(previous_sales.get(prod, 0.0) + val, 4)
        except Exception:
            pass

        for prod_id in _PRODUCT_TO_MATERIAL_NAMES:
            current = current_sales.get(prod_id)
            previous = previous_sales.get(prod_id)
            sales_totals[prod_id] = current
            if current is not None and previous is not None and previous > 0:
                growth_pct[prod_id] = round((current - previous) / previous * 100, 2)

        return sales_totals, growth_pct

    # ------------------------------------------------------------------
    # Bulk fetch: replaces N×5 individual queries with 4 total queries
    # Used by _compute_market_snapshot() for fast dashboard loading.
    # ------------------------------------------------------------------
    def get_all_kpis_bulk(self) -> tuple[Dict[str, Optional["ProductKPI"]], Dict[str, Optional[float]]]:
        """Fetch KPI + sales growth for ALL products in 4 batched DB queries.

        Returns:
            (kpis, growth_pct) where both are dicts keyed by product_id.
        """
        # Build reverse map: material_name → product_id
        mat_to_product: Dict[str, str] = {}
        all_names: list[str] = []
        for prod_id, names in _PRODUCT_TO_MATERIAL_NAMES.items():
            for name in names:
                mat_to_product[name] = prod_id
                all_names.append(name)

        kpis: Dict[str, Optional[ProductKPI]] = {p: None for p in _PRODUCT_TO_MATERIAL_NAMES}
        growth_pct: Dict[str, Optional[float]] = {p: None for p in _PRODUCT_TO_MATERIAL_NAMES}

        if not all_names:
            return kpis, growth_pct

        ref = self._get_latest_date()
        cutoff_30 = ref - timedelta(days=30)
        cutoff_60 = ref - timedelta(days=60)
        cutoff_31 = ref - timedelta(days=31)

        ph = ", ".join(f":n{i}" for i in range(len(all_names)))
        base_params: Dict[str, Any] = {f"n{i}": name for i, name in enumerate(all_names)}

        # Query 1: current 30d sales per material_name
        sales_30d: Dict[str, float] = {}
        try:
            with db.engine.connect() as conn:
                for mat, qty in conn.execute(
                    text(
                        f"SELECT material_name, SUM(COALESCE(FDELIQTY, 0)) "
                        f"FROM erp_v_sale_orderentry "
                        f"WHERE material_name IN ({ph}) "
                        f"AND PLANDATE IS NOT NULL "
                        f"AND DATE(PLANDATE) >= :s AND DATE(PLANDATE) <= :e "
                        f"GROUP BY material_name"
                    ),
                    {**base_params, "s": cutoff_30, "e": ref},
                ).fetchall():
                    if qty is not None:
                        prod = mat_to_product.get(mat)
                        if prod:
                            sales_30d[prod] = round(sales_30d.get(prod, 0.0) + float(qty), 4)
        except Exception:
            pass

        # Query 2: previous 30d sales (days 31-60) for growth calculation
        sales_prev: Dict[str, float] = {}
        try:
            with db.engine.connect() as conn:
                for mat, qty in conn.execute(
                    text(
                        f"SELECT material_name, SUM(COALESCE(FDELIQTY, 0)) "
                        f"FROM erp_v_sale_orderentry "
                        f"WHERE material_name IN ({ph}) "
                        f"AND PLANDATE IS NOT NULL "
                        f"AND DATE(PLANDATE) >= :s AND DATE(PLANDATE) <= :e "
                        f"GROUP BY material_name"
                    ),
                    {**base_params, "s": cutoff_60, "e": cutoff_31},
                ).fetchall():
                    if qty is not None:
                        prod = mat_to_product.get(mat)
                        if prod:
                            sales_prev[prod] = round(sales_prev.get(prod, 0.0) + float(qty), 4)
        except Exception:
            pass

        # Query 3: inventory per material_name
        inventory: Dict[str, float] = {}
        try:
            with db.engine.connect() as conn:
                for mat, qty in conn.execute(
                    text(
                        f"SELECT material_name, SUM(COALESCE(FBASEQTY, 0)) "
                        f"FROM erp_v_stk_inventory "
                        f"WHERE material_name IN ({ph}) "
                        f"GROUP BY material_name"
                    ),
                    base_params,
                ).fetchall():
                    if qty is not None:
                        prod = mat_to_product.get(mat)
                        if prod:
                            inventory[prod] = round(inventory.get(prod, 0.0) + float(qty), 4)
        except Exception:
            pass

        # Query 4: utilization (produced vs planned) per material_name
        util_agg: Dict[str, Dict[str, float]] = {}
        try:
            with db.engine.connect() as conn:
                for mat, produced, planned in conn.execute(
                    text(
                        f"SELECT material_name, "
                        f"SUM(COALESCE(produce_qty, 0)), SUM(COALESCE(plan_qty, 0)) "
                        f"FROM erp_v_sale_orderentry "
                        f"WHERE material_name IN ({ph}) "
                        f"AND PLANDATE IS NOT NULL "
                        f"AND DATE(PLANDATE) >= :cutoff "
                        f"GROUP BY material_name"
                    ),
                    {**base_params, "cutoff": cutoff_30},
                ).fetchall():
                    prod = mat_to_product.get(mat)
                    if prod:
                        agg = util_agg.setdefault(prod, {"p": 0.0, "q": 0.0})
                        agg["p"] += float(produced or 0)
                        agg["q"] += float(planned or 0)
        except Exception:
            pass

        utilization: Dict[str, float] = {}
        for prod, agg in util_agg.items():
            if agg["q"] > 0:
                utilization[prod] = round(agg["p"] / agg["q"] * 100.0, 2)

        # Build ProductKPI objects and growth pct
        for prod_id in _PRODUCT_TO_MATERIAL_NAMES:
            s30 = sales_30d.get(prod_id)
            inv = inventory.get(prod_id)
            util = utilization.get(prod_id)

            if s30 is None and inv is None and util is None:
                kpis[prod_id] = None
            else:
                s30r = round(s30, 2) if s30 is not None else None
                invr = round(inv, 2) if inv is not None else None
                inv_days = None
                if invr is not None and s30r is not None and s30r > 0:
                    inv_days = round(invr / (s30r / 30.0), 2)
                kpis[prod_id] = ProductKPI(
                    product_id=prod_id,
                    region="east_china",
                    inventory_tons=invr,
                    inventory_days=inv_days,
                    sales_30d_ton=s30r,
                    utilization_pct=util,
                )

            prev = sales_prev.get(prod_id)
            if s30 is not None and prev is not None and prev > 0:
                growth_pct[prod_id] = round((s30 - prev) / prev * 100, 2)

        return kpis, growth_pct

    def get_daily_sales(self, product_id: str, days: int = 90) -> list[dict]:
        """Return daily aggregated sales [{date, volume}] for chart overlay."""
        canonical = _canonical_product_id(product_id)
        material_names = _PRODUCT_TO_MATERIAL_NAMES.get(canonical)
        if not material_names:
            return []
        placeholders, params = _material_name_params(material_names)
        params["cutoff_date"] = self._get_latest_date() - timedelta(days=days)
        try:
            with db.engine.connect() as conn:
                raw_rows = conn.execute(
                    text(
                        "SELECT DATE(PLANDATE) AS d, COALESCE(FDELIQTY, 0) AS qty "
                        "FROM erp_v_sale_orderentry "
                        f"WHERE material_name IN ({placeholders}) "
                        "AND PLANDATE IS NOT NULL "
                        "AND DATE(PLANDATE) >= :cutoff_date "
                        "ORDER BY d"
                    ),
                    params,
                ).fetchall()
        except Exception:
            return []
        # Aggregate in Python — avoids slow MySQL GROUP BY on unindexed table
        vol_by_date: dict = {}
        for d, qty in raw_rows:
            if qty is None:
                continue
            d_str = str(d)
            vol_by_date[d_str] = vol_by_date.get(d_str, 0.0) + float(qty)
        return [
            {"date": d, "volume": round(v, 2)} for d, v in sorted(vol_by_date.items())
        ]

    def _query_inventory_tons(self, material_names: tuple[str, ...]) -> Optional[float]:
        placeholders, params = _material_name_params(material_names)
        try:
            with db.engine.connect() as conn:
                value = conn.execute(
                    text(
                        "SELECT SUM(COALESCE(FBASEQTY, 0)) "
                        "FROM erp_v_stk_inventory "
                        f"WHERE material_name IN ({placeholders})"
                    ),
                    params,
                ).scalar()
        except Exception:
            return None

        if value is None:
            return None
        return round(float(value), 2)

    def _query_utilization_pct(
        self, material_names: tuple[str, ...]
    ) -> Optional[float]:
        placeholders, params = _material_name_params(material_names)
        try:
            with db.engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT "
                        "  SUM(COALESCE(produce_qty, 0)) AS produced_qty, "
                        "  SUM(COALESCE(plan_qty, 0)) AS planned_qty "
                        "FROM erp_v_sale_orderentry "
                        f"WHERE material_name IN ({placeholders}) "
                        "AND PLANDATE IS NOT NULL "
                        "AND DATE(PLANDATE) >= :cutoff_date"
                    ),
                    {
                        **params,
                        "cutoff_date": self._get_latest_date() - timedelta(days=30),
                    },
                ).fetchone()
        except Exception:
            return None

        if not row:
            return None

        produced_qty = float(row[0] or 0)
        planned_qty = float(row[1] or 0)
        if planned_qty <= 0:
            return None
        return round(produced_qty / planned_qty * 100.0, 2)

    # ------------------------------------------------------------------
    # Logistics: inbound (入库) and outbound (出库) summary methods
    # ------------------------------------------------------------------

    def get_inbound_summary(
        self, product_id: str, days: int = 30
    ) -> Dict[str, Any]:
        """Return total received quantity and latest receipt date for a product.

        Queries erp_v_raw_material_receiving which maps to the raw-material
        receiving view joined from erp_t_stk_instock + erp_t_stk_instockentry.
        """
        canonical = _canonical_product_id(product_id)
        material_names = _PRODUCT_TO_MATERIAL_NAMES.get(canonical)
        if not material_names:
            return {"found": False, "reason": f"No ERP mapping for {canonical}"}

        placeholders, params = _material_name_params(material_names)
        ref = self._get_latest_date()
        params["cutoff"] = ref - timedelta(days=days)
        params["ref"] = ref

        try:
            with db.engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT "
                        "  SUM(COALESCE(FREALQTY, 0)) AS total_received, "
                        "  MAX(DATE(FDATE)) AS latest_date, "
                        "  COUNT(*) AS receipt_count "
                        "FROM erp_v_raw_material_receiving "
                        f"WHERE material_name IN ({placeholders}) "
                        "  AND FDATE IS NOT NULL "
                        "  AND DATE(FDATE) >= :cutoff "
                        "  AND DATE(FDATE) <= :ref"
                    ),
                    params,
                ).fetchone()

                if not row or row[0] is None:
                    return {
                        "found": False,
                        "product_id": canonical,
                        "reason": "No inbound records in period",
                        "period_days": days,
                    }

                return {
                    "found": True,
                    "product_id": canonical,
                    "period_days": days,
                    "total_received_ton": round(float(row[0]), 2),
                    "latest_receipt_date": str(row[1])[:10] if row[1] else None,
                    "receipt_count": int(row[2] or 0),
                }
        except Exception as exc:
            return {"found": False, "reason": str(exc), "product_id": canonical}

    def get_outbound_summary(
        self, product_id: str, days: int = 7
    ) -> Dict[str, Any]:
        """Return total shipped quantity, open backlog, and latest ship date.

        Queries erp_v_sale_orderentry (same view used for sales KPIs).
        """
        canonical = _canonical_product_id(product_id)
        material_names = _PRODUCT_TO_MATERIAL_NAMES.get(canonical)
        if not material_names:
            return {"found": False, "reason": f"No ERP mapping for {canonical}"}

        placeholders, params = _material_name_params(material_names)
        ref = self._get_latest_date()
        params["cutoff"] = ref - timedelta(days=days)
        params["ref"] = ref

        try:
            with db.engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT "
                        "  SUM(COALESCE(FDELIQTY, 0)) AS total_shipped, "
                        "  SUM(COALESCE(FREMAINOUTQTY, 0)) AS open_backlog, "
                        "  MAX(DATE(PLANDATE)) AS latest_ship_date, "
                        "  COUNT(*) AS order_line_count "
                        "FROM erp_v_sale_orderentry "
                        f"WHERE material_name IN ({placeholders}) "
                        "  AND PLANDATE IS NOT NULL "
                        "  AND DATE(PLANDATE) >= :cutoff "
                        "  AND DATE(PLANDATE) <= :ref"
                    ),
                    params,
                ).fetchone()

                if not row or row[0] is None:
                    return {
                        "found": False,
                        "product_id": canonical,
                        "reason": "No outbound records in period",
                        "period_days": days,
                    }

                return {
                    "found": True,
                    "product_id": canonical,
                    "period_days": days,
                    "total_shipped_ton": round(float(row[0]), 2),
                    "open_backlog_ton": round(float(row[1] or 0), 2),
                    "latest_shipment_date": str(row[2])[:10] if row[2] else None,
                    "order_line_count": int(row[3] or 0),
                }
        except Exception as exc:
            return {"found": False, "reason": str(exc), "product_id": canonical}

    def get_replenishment_coverage(
        self, product_id: str, forecast_days: int = 14
    ) -> Dict[str, Any]:
        """Estimate days-of-supply and whether near-term replenishment is needed.

        Combines inventory_tons + inbound pipeline against recent daily outbound rate.
        """
        canonical = _canonical_product_id(product_id)
        material_names = _PRODUCT_TO_MATERIAL_NAMES.get(canonical)
        if not material_names:
            return {"found": False, "reason": f"No ERP mapping for {canonical}"}

        inv_tons = self._query_inventory_tons(material_names)
        inbound = self.get_inbound_summary(product_id, days=14)
        sales_7d = self._query_sales_period(material_names, 7, 0)

        daily_outbound_rate = (sales_7d / 7.0) if sales_7d and sales_7d > 0 else None
        days_cover = (
            round(inv_tons / daily_outbound_rate, 1)
            if inv_tons and daily_outbound_rate and daily_outbound_rate > 0
            else None
        )

        return {
            "found": True,
            "product_id": canonical,
            "inventory_tons": inv_tons,
            "daily_outbound_rate_ton": (
                round(daily_outbound_rate, 2) if daily_outbound_rate else None
            ),
            "days_of_supply": days_cover,
            "replenishment_needed": (
                days_cover is not None and days_cover < forecast_days
            ),
            "inbound_14d": inbound,
        }


def compute_kpi_forecast_adjustment(kpi: Optional[ProductKPI]) -> Dict[str, Any]:
    """Translate ERP KPI readings into a bounded forecast percentage tilt."""

    signal = {
        "active": False,
        "bias": "neutral",
        "inventory_days": None,
        "sales_30d_ton": None,
        "utilization_pct": None,
        "adjustment_pct": {"3d": 0.0, "7d": 0.0, "30d": 0.0},
        "summary": None,
    }
    if not kpi:
        return signal

    signal["inventory_days"] = kpi.inventory_days
    signal["sales_30d_ton"] = kpi.sales_30d_ton
    signal["utilization_pct"] = kpi.utilization_pct

    adjustments = {"3d": 0.0, "7d": 0.0, "30d": 0.0}
    reasons = []

    if kpi.inventory_days is not None:
        if kpi.inventory_days <= 7:
            adjustments["3d"] += 0.5
            adjustments["7d"] += 0.8
            adjustments["30d"] += 0.7
            reasons.append("tight inventory coverage")
        elif kpi.inventory_days <= 15:
            adjustments["3d"] += 0.25
            adjustments["7d"] += 0.45
            adjustments["30d"] += 0.4
            reasons.append("supportive inventory coverage")
        elif kpi.inventory_days >= 45:
            adjustments["3d"] -= 0.5
            adjustments["7d"] -= 0.8
            adjustments["30d"] -= 1.0
            reasons.append("heavy inventory coverage")
        elif kpi.inventory_days >= 30:
            adjustments["3d"] -= 0.25
            adjustments["7d"] -= 0.45
            adjustments["30d"] -= 0.6
            reasons.append("elevated inventory coverage")

    if kpi.utilization_pct is not None:
        if kpi.utilization_pct >= 85:
            adjustments["3d"] += 0.1
            adjustments["7d"] += 0.15
            adjustments["30d"] += 0.2
            reasons.append("high operating rate")
        elif kpi.utilization_pct <= 60:
            adjustments["3d"] -= 0.1
            adjustments["7d"] -= 0.15
            adjustments["30d"] -= 0.2
            reasons.append("soft operating rate")

    for horizon in adjustments:
        adjustments[horizon] = round(max(min(adjustments[horizon], 1.2), -1.2), 2)

    max_abs = max(abs(value) for value in adjustments.values())
    if max_abs > 0:
        signal["active"] = True
        if adjustments["7d"] > 0:
            signal["bias"] = "bullish"
        elif adjustments["7d"] < 0:
            signal["bias"] = "bearish"
        signal["adjustment_pct"] = adjustments
        reason_text = ", ".join(reasons)
        details = []
        if kpi.inventory_days is not None:
            details.append(f"inventory_days={kpi.inventory_days:.1f}")
        if kpi.sales_30d_ton is not None:
            details.append(f"sales_30d_ton={kpi.sales_30d_ton:.0f}")
        if kpi.utilization_pct is not None:
            details.append(f"utilization_pct={kpi.utilization_pct:.1f}")
        signal["summary"] = "; ".join(
            part for part in [reason_text, ", ".join(details)] if part
        )

    return signal


erp_kpi_service = ErpKPIService()
