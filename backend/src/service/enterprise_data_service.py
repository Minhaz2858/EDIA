from datetime import datetime, timedelta
import os
from typing import List, Dict

from dotenv import load_dotenv

from backend.src.db.engine_factory import get_erp_engine
from backend.src.db.price_semantics import (
    ERP_SALE_SPECS,
    LZ_MARKET_SPECS,
    PRODUCT_PAIR_SPECS,
    compute_series_coverage,
    merge_price_gap_series,
    query_erp_weighted_sale_series,
    query_lz_average_series,
)
from backend.src.service.market_data_service import market_data


load_dotenv()

_DB_URL = os.getenv("DATABASE_URL")
if not _DB_URL:
    raise RuntimeError("DATABASE_URL must be set for cloud DB mode.")

_ENTERPRISE_PRODUCTS = ("enterprise_c5", "enterprise_c9")


class EnterpriseDataService:
    """
    Loads enterprise sales and price history for C5 and C9 from ERP,
    plus short-term forecasts influenced by upstream costs.
    """

    def __init__(self):
        self.history: Dict[str, List[Dict]] = {}
        self.gap_history: Dict[str, List[Dict]] = {}
        self.coverage: Dict[str, Dict] = {}
        self.data_source: Dict[str, str] = {}
        self._engine = get_erp_engine()
        self._load_from_erp()

    def _load_from_erp(self) -> None:
        """Load daily ERP weighted prices and market-reference gap series."""
        try:
            with self._engine.connect() as conn:
                for product_id in _ENTERPRISE_PRODUCTS:
                    pair_spec = PRODUCT_PAIR_SPECS[product_id]
                    erp_spec = pair_spec.erp_spec
                    lz_spec = pair_spec.lz_spec

                    erp_series = query_erp_weighted_sale_series(conn, erp_spec)
                    lz_series = query_lz_average_series(conn, lz_spec)
                    gap_series = merge_price_gap_series(erp_series, lz_series)
                    coverage = compute_series_coverage(erp_series, lz_series)

                    self.history[product_id] = [
                        {
                            "date": row["date"],
                            "sales": round(float(row.get("total_qty") or 0), 2),
                            "price": round(float(row["ecisco_price"]), 2),
                            "erp_row_count": int(row.get("row_count") or 0),
                        }
                        for row in erp_series
                    ]
                    self.gap_history[product_id] = gap_series
                    self.coverage[product_id] = {
                        **coverage,
                        "canonical_product_id": pair_spec.canonical_product_id,
                        "display_name": pair_spec.display_name,
                        "lz_source_name": pair_spec.lz_source_name,
                        "erp_source_name": pair_spec.erp_source_name,
                    }
                    self.data_source[product_id] = (
                        "real_erp_lz_same_day"
                        if coverage["status"] == "READY_FOR_GAP"
                        else "real_erp_lz_partial"
                        if erp_series or lz_series
                        else "missing"
                    )
        except Exception as exc:
            print(f"Warning: enterprise ERP load failed: {exc}")
            for product_id in _ENTERPRISE_PRODUCTS:
                self.history[product_id] = []
                self.gap_history[product_id] = []
                self.coverage[product_id] = {
                    "alignment_mode": "same_day",
                    "status": "NOT_COMPARABLE",
                    "erp_date_count": 0,
                    "lz_date_count": 0,
                    "overlap_date_count": 0,
                    "overlap_ratio": 0.0,
                    "latest_erp_date": None,
                    "latest_lz_date": None,
                    "latest_overlap_date": None,
                }
                self.data_source[product_id] = "error"

    def refresh(self) -> None:
        """Reload enterprise history from ERP source tables."""
        self.history.clear()
        self.gap_history.clear()
        self.coverage.clear()
        self.data_source.clear()
        self._load_from_erp()

    def get_history(self, product_id: str) -> List[Dict]:
        return self.history.get(product_id, [])

    def get_gap_history(self, product_id: str) -> List[Dict]:
        return self.gap_history.get(product_id, [])

    def get_coverage(self, product_id: str) -> Dict:
        return self.coverage.get(product_id, {})

    def get_summary(self, product_id: str, window_days: int = 30) -> Dict:
        hist = self.history.get(product_id, [])
        gap_hist = self.gap_history.get(product_id, [])
        if not hist:
            pair_spec = PRODUCT_PAIR_SPECS.get(product_id)
            erp_spec = ERP_SALE_SPECS.get(product_id)
            lz_spec = LZ_MARKET_SPECS.get(product_id)
            return {
                "product_id": product_id,
                "canonical_product_id": (
                    pair_spec.canonical_product_id if pair_spec else None
                ),
                "avg_price_30d": 0.0,
                "avg_sales_30d": 0.0,
                "price_growth_pct": 0.0,
                "sales_growth_pct": 0.0,
                "market_ref_price_30d": 0.0,
                "gap_vs_market_abs": 0.0,
                "gap_vs_market_pct": 0.0,
                "coverage": self.coverage.get(product_id, {}),
                "semantics": {
                    "ecisco_actual_source": erp_spec.table_name if erp_spec else None,
                    "market_reference_source": lz_spec.table_name if lz_spec else None,
                    "market_reference_supplier": (
                        lz_spec.average_supplier if lz_spec else None
                    ),
                    "lz_source_name": pair_spec.lz_source_name if pair_spec else None,
                    "erp_source_name": pair_spec.erp_source_name if pair_spec else None,
                    "ecisco_calc": "weighted_avg(FPRICE*FQTY_ORIGIN)/sum(FQTY_ORIGIN)",
                    "market_calc": "daily_avg(numeric tax_price)",
                    "alignment_rule": "same_day_overlap_only",
                },
                "data_source": self.data_source.get(product_id, "missing"),
            }
        window = hist[-window_days:] if len(hist) >= window_days else hist
        avg_price = sum(p["price"] for p in window) / len(window)
        avg_sales = sum(p["sales"] for p in window) / len(window)
        # Growth vs prior window
        prior = (
            hist[-2 * window_days : -window_days]
            if len(hist) >= 2 * window_days
            else []
        )
        price_growth = 0.0
        sales_growth = 0.0
        if prior:
            prior_avg_price = sum(p["price"] for p in prior) / len(prior)
            prior_avg_sales = sum(p["sales"] for p in prior) / len(prior)
            price_growth = (
                ((avg_price - prior_avg_price) / prior_avg_price) * 100
                if prior_avg_price
                else 0.0
            )
            sales_growth = (
                ((avg_sales - prior_avg_sales) / prior_avg_sales) * 100
                if prior_avg_sales
                else 0.0
            )

        market_ref_price_30d = 0.0
        gap_vs_market_abs = 0.0
        gap_vs_market_pct = 0.0
        if gap_hist:
            gap_window = gap_hist[-window_days:] if len(gap_hist) >= window_days else gap_hist
            if gap_window:
                market_ref_price_30d = round(
                    sum(float(item["market_price"]) for item in gap_window) / len(gap_window),
                    2,
                )
                latest_gap = gap_window[-1]
                gap_vs_market_abs = round(float(latest_gap["gap_abs"]), 2)
                gap_vs_market_pct = round(float(latest_gap["gap_pct"]), 2)

        pair_spec = PRODUCT_PAIR_SPECS.get(product_id)
        erp_spec = ERP_SALE_SPECS.get(product_id)
        lz_spec = LZ_MARKET_SPECS.get(product_id)
        return {
            "product_id": product_id,
            "canonical_product_id": pair_spec.canonical_product_id if pair_spec else None,
            "avg_price_30d": round(avg_price, 2),
            "avg_sales_30d": round(avg_sales, 2),
            "price_growth_pct": round(price_growth, 2),
            "sales_growth_pct": round(sales_growth, 2),
            "market_ref_price_30d": market_ref_price_30d,
            "gap_vs_market_abs": gap_vs_market_abs,
            "gap_vs_market_pct": gap_vs_market_pct,
            "coverage": self.coverage.get(product_id, {}),
            "semantics": {
                "ecisco_actual_source": erp_spec.table_name if erp_spec else None,
                "market_reference_source": lz_spec.table_name if lz_spec else None,
                "market_reference_supplier": (
                    lz_spec.average_supplier if lz_spec else None
                ),
                "lz_source_name": pair_spec.lz_source_name if pair_spec else None,
                "erp_source_name": pair_spec.erp_source_name if pair_spec else None,
                "ecisco_calc": "weighted_avg(FPRICE*FQTY_ORIGIN)/sum(FQTY_ORIGIN)",
                "market_calc": "daily_avg(numeric tax_price)",
                "alignment_rule": "same_day_overlap_only",
            },
            "data_source": self.data_source.get(product_id, "unknown"),
        }

    def get_forecast(self, product_id: str, weeks: int = 4) -> List[Dict]:
        """
        Produce simple weekly forecast for price and sales using last 30 days averages
        and upstream naphtha trend signal.
        """
        hist = self.history.get(product_id, [])
        if not hist:
            return []
        last_date = datetime.strptime(hist[-1]["date"], "%Y-%m-%d")
        # Upstream signal: last 7-day change of naphtha
        nap_hist = market_data.get_price_history("naphtha")
        nap_tail = nap_hist[-7:] if len(nap_hist) >= 7 else nap_hist
        upstream_signal = 0.0
        if len(nap_tail) >= 2:
            upstream_signal = (nap_tail[-1]["price"] - nap_tail[0]["price"]) / max(
                nap_tail[0]["price"], 1
            )  # ~%

        # Base from recent means
        recent = hist[-30:] if len(hist) >= 30 else hist
        base_price = sum(p["price"] for p in recent) / len(recent)
        base_sales = sum(p["sales"] for p in recent) / len(recent)

        forecasts = []
        for w in range(1, weeks + 1):
            # Apply upstream signal modestly and small growth trend
            price = base_price * (1 + 0.15 * upstream_signal) * (1 + 0.01 * w)
            sales = base_sales * (1 + 0.05 * upstream_signal) * (1 + 0.015 * w)
            week_date = (last_date + timedelta(days=7 * w)).strftime("%Y-%m-%d")
            forecasts.append(
                {
                    "date": week_date,
                    "price": round(price, 2),
                    "sales": round(sales, 2),
                }
            )
        return forecasts


# Singleton
enterprise_data = EnterpriseDataService()
