from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, Optional

from sqlalchemy import text

from backend.src.db.sqlite_db import db
from backend.src.service.erp_kpi_service import (
    _PRODUCT_TO_MATERIAL_NAMES,
    _canonical_product_id,
    _material_name_params,
)


@dataclass
class ProductWeeklyPlan:
    product_id: str
    plan_date: str
    source: str
    material_names: list[str]
    planned_production_qty: float
    planned_sales_qty: float
    planned_inventory_qty: float
    actual_delivery_qty: float
    planned_sales_amount: float
    average_tax_price: float | None
    net_supply_gap_qty: float
    inventory_coverage_days: float | None
    fulfillment_ratio: float | None
    pressure_signal: str
    note: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "product_id": self.product_id,
            "plan_date": self.plan_date,
            "source": self.source,
            "material_names": self.material_names,
            "planned_production_qty": round(self.planned_production_qty, 2),
            "planned_sales_qty": round(self.planned_sales_qty, 2),
            "planned_inventory_qty": round(self.planned_inventory_qty, 2),
            "actual_delivery_qty": round(self.actual_delivery_qty, 2),
            "planned_sales_amount": round(self.planned_sales_amount, 2),
            "average_tax_price": (
                round(self.average_tax_price, 2)
                if self.average_tax_price is not None
                else None
            ),
            "net_supply_gap_qty": round(self.net_supply_gap_qty, 2),
            "inventory_coverage_days": (
                round(self.inventory_coverage_days, 2)
                if self.inventory_coverage_days is not None
                else None
            ),
            "fulfillment_ratio": (
                round(self.fulfillment_ratio, 4)
                if self.fulfillment_ratio is not None
                else None
            ),
            "pressure_signal": self.pressure_signal,
            "note": self.note,
        }


class WeeklyPlanService:
    """Normalize ERP weekly planning data for pricing and reporting flows."""

    _material_lookup_cache: Optional[Dict[int, str]] = None

    def invalidate_cache(self) -> None:
        self._material_lookup_cache = None

    def _get_material_lookup(self) -> Dict[int, str]:
        if self._material_lookup_cache is not None:
            return self._material_lookup_cache

        lookup: Dict[int, str] = {}
        try:
            with db.engine.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT CAST(material_id AS UNSIGNED) AS FMATERIALID, "
                        "NULLIF(TRIM(material_name), '') AS material_name "
                        "FROM material WHERE material_id REGEXP '^[0-9]+$'"
                    )
                )
                for material_id, material_name in rows:
                    if material_id is None or not material_name:
                        continue
                    lookup[int(material_id)] = str(material_name).strip()
        except Exception:
            lookup = {}

        self._material_lookup_cache = lookup
        return lookup

    def _latest_plan_date(self) -> Optional[date]:
        try:
            with db.engine.connect() as conn:
                value = conn.execute(
                    text(
                        "SELECT MAX(DATE(FDATE)) FROM erp_paez_t_monweek_plan "
                        "WHERE FDATE IS NOT NULL"
                    )
                ).scalar()
                if value:
                    return (
                        value
                        if isinstance(value, date)
                        else date.fromisoformat(str(value)[:10])
                    )
        except Exception:
            return None
        return None

    def _latest_sales_date(self) -> Optional[date]:
        try:
            with db.engine.connect() as conn:
                value = conn.execute(
                    text(
                        "SELECT MAX(DATE(PLANDATE)) FROM erp_v_sale_orderentry "
                        "WHERE PLANDATE IS NOT NULL"
                    )
                ).scalar()
                if value:
                    return (
                        value
                        if isinstance(value, date)
                        else date.fromisoformat(str(value)[:10])
                    )
        except Exception:
            return None
        return None

    def get_product_plan(self, product_id: str) -> Optional[Dict[str, Any]]:
        canonical = _canonical_product_id(product_id)
        material_names = _PRODUCT_TO_MATERIAL_NAMES.get(canonical)
        if not material_names:
            return None

        plan = self._query_plan_tables(canonical, material_names)
        if plan is None:
            plan = self._query_sales_view_fallback(canonical, material_names)
        return plan.to_dict() if plan else None

    def get_all_product_plans(self) -> list[Dict[str, Any]]:
        items: list[Dict[str, Any]] = []
        seen: set[str] = set()
        for product_id in _PRODUCT_TO_MATERIAL_NAMES:
            if product_id in seen:
                continue
            seen.add(product_id)
            plan = self.get_product_plan(product_id)
            if plan:
                items.append(plan)
        items.sort(
            key=lambda item: (item.get("plan_date", ""), item["product_id"]),
            reverse=True,
        )
        return items

    def _query_plan_tables(
        self,
        product_id: str,
        material_names: tuple[str, ...],
    ) -> Optional[ProductWeeklyPlan]:
        latest_date = self._latest_plan_date()
        if latest_date is None:
            return None

        material_lookup = self._get_material_lookup()
        try:
            with db.engine.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT DATE(mp.FDATE) AS plan_date, mpe.FMATERIALID, "
                        "SUM(COALESCE(mpe.F_PAEZ_PROQTY, 0)) AS planned_production_qty, "
                        "SUM(COALESCE(mpe.F_PAEZ_SALQTY, 0)) AS planned_sales_qty, "
                        "SUM(COALESCE(mpe.F_PAEZ_KUCUNQTY, 0)) AS planned_inventory_qty, "
                        "SUM(COALESCE(mpe.F_PAEZ_SALEAMOUNT, 0)) AS planned_sales_amount, "
                        "AVG(NULLIF(mpe.FYSSALETAXPRICE, 0)) AS average_tax_price "
                        "FROM erp_paez_t_monweek_plan mp "
                        "JOIN erp_paez_t_monweek_planentry mpe ON mpe.FID = mp.FID "
                        "WHERE DATE(mp.FDATE) = :plan_date "
                        "  AND COALESCE(mp.FBILLSTATUS, 'C') = 'C' "
                        "GROUP BY DATE(mp.FDATE), mpe.FMATERIALID"
                    ),
                    {"plan_date": latest_date.isoformat()},
                )

                aggregates = {
                    "planned_production_qty": 0.0,
                    "planned_sales_qty": 0.0,
                    "planned_inventory_qty": 0.0,
                    "planned_sales_amount": 0.0,
                    "average_tax_price_values": [],
                    "material_names": set(),
                }

                for (
                    plan_date,
                    material_id,
                    planned_production_qty,
                    planned_sales_qty,
                    planned_inventory_qty,
                    planned_sales_amount,
                    average_tax_price,
                ) in rows:
                    material_name = material_lookup.get(int(material_id or 0))
                    if material_name not in material_names:
                        continue
                    aggregates["material_names"].add(material_name)
                    aggregates["planned_production_qty"] += float(
                        planned_production_qty or 0
                    )
                    aggregates["planned_sales_qty"] += float(planned_sales_qty or 0)
                    aggregates["planned_inventory_qty"] += float(
                        planned_inventory_qty or 0
                    )
                    aggregates["planned_sales_amount"] += float(
                        planned_sales_amount or 0
                    )
                    if average_tax_price is not None:
                        aggregates["average_tax_price_values"].append(
                            float(average_tax_price)
                        )

                if not aggregates["material_names"]:
                    return None

                return self._build_summary(
                    product_id=product_id,
                    plan_date=latest_date.isoformat(),
                    source="erp_paez_t_monweek_plan",
                    material_names=sorted(aggregates["material_names"]),
                    planned_production_qty=aggregates["planned_production_qty"],
                    planned_sales_qty=aggregates["planned_sales_qty"],
                    planned_inventory_qty=aggregates["planned_inventory_qty"],
                    actual_delivery_qty=0.0,
                    planned_sales_amount=aggregates["planned_sales_amount"],
                    average_tax_price=(
                        sum(aggregates["average_tax_price_values"])
                        / len(aggregates["average_tax_price_values"])
                        if aggregates["average_tax_price_values"]
                        else None
                    ),
                )
        except Exception:
            return None

    def _query_sales_view_fallback(
        self,
        product_id: str,
        material_names: tuple[str, ...],
    ) -> Optional[ProductWeeklyPlan]:
        latest_date = self._latest_sales_date()
        if latest_date is None:
            return None

        window_start = latest_date - timedelta(days=6)
        placeholders, params = _material_name_params(material_names)
        params.update(
            {
                "window_start": window_start.isoformat(),
                "window_end": latest_date.isoformat(),
            }
        )
        try:
            with db.engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT "
                        "  SUM(COALESCE(produce_qty, 0)) AS planned_production_qty, "
                        "  SUM(COALESCE(plan_qty, 0)) AS planned_sales_qty, "
                        "  SUM(COALESCE(FDELIQTY, 0)) AS actual_delivery_qty, "
                        "  SUM(COALESCE(FDELIQTY, 0) * COALESCE(FTAXPRICE, FPRICE, 0)) AS actual_sales_amount, "
                        "  AVG(NULLIF(COALESCE(FTAXPRICE, FPRICE), 0)) AS average_tax_price "
                        "FROM erp_v_sale_orderentry "
                        f"WHERE material_name IN ({placeholders}) "
                        "  AND PLANDATE IS NOT NULL "
                        "  AND DATE(PLANDATE) BETWEEN :window_start AND :window_end"
                    ),
                    params,
                ).first()
                if row is None:
                    return None

                planned_production_qty = float(row.planned_production_qty or 0)
                planned_sales_qty = float(row.planned_sales_qty or 0)
                actual_delivery_qty = float(row.actual_delivery_qty or 0)
                actual_sales_amount = float(row.actual_sales_amount or 0)
                average_tax_price = (
                    float(row.average_tax_price)
                    if row.average_tax_price is not None
                    else None
                )

                if (
                    planned_production_qty <= 0
                    and planned_sales_qty <= 0
                    and actual_delivery_qty <= 0
                ):
                    return None

                return self._build_summary(
                    product_id=product_id,
                    plan_date=latest_date.isoformat(),
                    source="erp_v_sale_orderentry",
                    material_names=list(material_names),
                    planned_production_qty=planned_production_qty,
                    planned_sales_qty=planned_sales_qty,
                    planned_inventory_qty=0.0,
                    actual_delivery_qty=actual_delivery_qty,
                    planned_sales_amount=actual_sales_amount,
                    average_tax_price=average_tax_price,
                )
        except Exception:
            return None

    def _build_summary(
        self,
        *,
        product_id: str,
        plan_date: str,
        source: str,
        material_names: list[str],
        planned_production_qty: float,
        planned_sales_qty: float,
        planned_inventory_qty: float,
        actual_delivery_qty: float,
        planned_sales_amount: float,
        average_tax_price: float | None,
    ) -> ProductWeeklyPlan:
        net_supply_gap_qty = planned_sales_qty - planned_production_qty
        inventory_coverage_days = None
        if planned_sales_qty > 0 and planned_inventory_qty > 0:
            inventory_coverage_days = planned_inventory_qty / max(
                planned_sales_qty / 7.0, 1e-6
            )

        fulfillment_ratio = None
        if planned_sales_qty > 0 and actual_delivery_qty > 0:
            fulfillment_ratio = actual_delivery_qty / planned_sales_qty

        if net_supply_gap_qty > 0 and (
            inventory_coverage_days is None or inventory_coverage_days < 7
        ):
            pressure_signal = "tight"
            note = (
                "Planned sales exceed planned production and inventory cover is thin."
            )
        elif net_supply_gap_qty < 0 and (inventory_coverage_days or 0) >= 10:
            pressure_signal = "long"
            note = "Planned production exceeds planned sales with comfortable inventory cover."
        else:
            pressure_signal = "balanced"
            note = "Weekly plan is broadly balanced versus expected supply and demand."

        return ProductWeeklyPlan(
            product_id=product_id,
            plan_date=plan_date,
            source=source,
            material_names=material_names,
            planned_production_qty=planned_production_qty,
            planned_sales_qty=planned_sales_qty,
            planned_inventory_qty=planned_inventory_qty,
            actual_delivery_qty=actual_delivery_qty,
            planned_sales_amount=planned_sales_amount,
            average_tax_price=average_tax_price,
            net_supply_gap_qty=net_supply_gap_qty,
            inventory_coverage_days=inventory_coverage_days,
            fulfillment_ratio=fulfillment_ratio,
            pressure_signal=pressure_signal,
            note=note,
        )


weekly_plan_service = WeeklyPlanService()
