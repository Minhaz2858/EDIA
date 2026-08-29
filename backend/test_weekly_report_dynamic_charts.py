import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.src.db.sqlite_db import db
from backend.src.service.weekly_report_data import (
    ForecastBand,
    MonthlyDailySeries,
    PriceTimeseries,
    ProductSpec,
    serialize_product_chart_payload,
)
from backend.src.service.weekly_report_charts import render_chart_from_snapshot_payload


class WeeklyReportDynamicChartsTests(unittest.TestCase):
    def test_serialize_product_chart_payload_returns_snapshot_structure(self):
        product = ProductSpec(
            slug="cracked_c5",
            name_zh="裂解 C5",
            name_en="Cracked C5",
            category="liquid_product",
            in_scope=True,
            lz_view="lz_v_裂解c5_data",
            erp_sales_view="sale_erp_v_工业用裂解碳五_data",
            forecast_key="cracked_c5",
        )
        timeseries = PriceTimeseries(
            ym_list=["2026-03", "2026-04"],
            lz_avg=[4010.0, 4520.3],
            erp_wavg=[4120.0, 4576.5],
        )
        monthly_series = MonthlyDailySeries(
            date_list=["2026-04-27", "2026-04-28", "2026-04-30"],
            lz_daily=[4506.7, 4069.5, 4072.4],
            erp_daily=[4424.7, 4424.7, None],
        )
        forecast = ForecastBand(
            period="下周",
            trend_verb="走稳",
            low="4,100",
            high="4,500",
            confidence_label="中",
            confidence_score=3,
            model_version="fc_test",
        )

        payload = serialize_product_chart_payload(
            product=product,
            timeseries=timeseries,
            monthly_series=monthly_series,
            forecast=forecast,
            week_start="2026-04-27",
            week_end="2026-05-03",
            language="zh",
        )

        self.assertEqual(payload["product_slug"], "cracked_c5")
        self.assertEqual(payload["yearly"]["chart_type"], "yearly")
        self.assertEqual(payload["monthly"]["chart_type"], "monthly")
        self.assertEqual(payload["monthly"]["anchor_month"], "2026-04")
        self.assertEqual(len(payload["yearly"]["points"]), 2)
        self.assertEqual(len(payload["monthly"]["points"]), 3)
        self.assertEqual(payload["yearly"]["forecast_band"]["low"], 4100.0)
        self.assertEqual(payload["yearly"]["forecast_band"]["high"], 4500.0)
        self.assertEqual(payload["monthly"]["highlight"]["start"], "2026-04-27")
        self.assertEqual(payload["monthly"]["highlight"]["end"], "2026-05-03")

    def test_render_chart_from_snapshot_payload_writes_png(self):
        product = ProductSpec(
            slug="cracked_c5",
            name_zh="裂解 C5",
            name_en="Cracked C5",
            category="liquid_product",
            in_scope=True,
            lz_view="lz_v_裂解c5_data",
            erp_sales_view="sale_erp_v_工业用裂解碳五_data",
            forecast_key="cracked_c5",
        )
        timeseries = PriceTimeseries(
            ym_list=["2026-03", "2026-04"],
            lz_avg=[4010.0, 4520.3],
            erp_wavg=[4120.0, 4576.5],
        )
        monthly_series = MonthlyDailySeries(
            date_list=["2026-04-27", "2026-04-28", "2026-04-30"],
            lz_daily=[4506.7, 4069.5, 4072.4],
            erp_daily=[4424.7, 4424.7, None],
        )
        forecast = ForecastBand(
            period="下周",
            trend_verb="走稳",
            low="4,100",
            high="4,500",
            confidence_label="中",
            confidence_score=3,
            model_version="fc_test",
        )
        payload = serialize_product_chart_payload(
            product=product,
            timeseries=timeseries,
            monthly_series=monthly_series,
            forecast=forecast,
            week_start="2026-04-27",
            week_end="2026-05-03",
            language="zh",
        )

        with TemporaryDirectory() as temp_dir:
            out_path = render_chart_from_snapshot_payload(
                product_payload=payload,
                chart_type="yearly",
                report_id="wr_test_dynamic_context",
                charts_dir=Path(temp_dir),
                language="zh",
            )

            self.assertIsNotNone(out_path)
            self.assertTrue(Path(out_path).exists())
            self.assertTrue(str(out_path).endswith("cracked_c5_12mo.png"))

    def test_render_chart_from_snapshot_payload_supports_multi_series_profile(self):
        payload = {
            "product_slug": "cracked_c5",
            "product_name_zh": "裂解 C5",
            "product_name_en": "Cracked C5",
            "yearly": {
                "chart_type": "yearly",
                "title": "近12月价格走势",
                "unit": "元/吨",
                "left_axis_unit": "元/吨",
                "right_axis_unit": "美元/吨",
                "series": [
                    {"key": "yangzi", "label": "扬子裂解C5", "color": "#ff4d4f", "axis": "left", "unit": "元/吨"},
                    {"key": "maoming", "label": "茂名裂解C5", "color": "#1890ff", "axis": "left", "unit": "元/吨"},
                    {"key": "japan_cif", "label": "日本石脑油", "color": "#73d13d", "axis": "right", "unit": "美元/吨"},
                ],
                "points": [
                    {"period": "2026-02", "label": "2026-02", "yangzi": 4500, "maoming": 4400, "japan_cif": 610},
                    {"period": "2026-03", "label": "2026-03", "yangzi": 5450, "maoming": 5100, "japan_cif": 648},
                    {"period": "2026-04", "label": "2026-04", "yangzi": 6000, "maoming": 4780, "japan_cif": 573},
                ],
                "forecast_band": {"low": None, "high": None, "period": None, "confidence_label": None},
                "report_period": "2026-04",
            },
            "monthly": {
                "chart_type": "monthly",
                "title": "2026-04日价格走势",
                "unit": "元/吨",
                "series": [],
                "points": [],
                "anchor_month": "2026-04",
                "highlight": {"start": "2026-04-27", "end": "2026-05-03"},
            },
        }

        with TemporaryDirectory() as temp_dir:
            out_path = render_chart_from_snapshot_payload(
                product_payload=payload,
                chart_type="yearly",
                report_id="wr_test_profile_chart",
                charts_dir=Path(temp_dir),
                language="zh",
            )

            self.assertIsNotNone(out_path)
            self.assertTrue(Path(out_path).exists())
            self.assertTrue(str(out_path).endswith("cracked_c5_12mo.png"))

    def test_generated_report_context_preserves_weekly_chart_payload(self):
        session = db.create_session("test-user", "Weekly Chart Context Test")
        session_id = str(session["id"]) if isinstance(session, dict) else str(session)
        context = {
            "report_id": "wr_test_dynamic_context",
            "week_start": "2026-04-27",
            "week_end": "2026-05-03",
            "weekly_chart_payload": {
                "version": 1,
                "products": {
                    "cracked_c5": {
                        "product_slug": "cracked_c5",
                        "product_name_zh": "裂解 C5",
                        "product_name_en": "Cracked C5",
                        "yearly": {
                            "chart_type": "yearly",
                            "title": "近12月价格走势",
                            "unit": "元/吨",
                            "series": [],
                            "points": [],
                        },
                        "monthly": {
                            "chart_type": "monthly",
                            "title": "2026-04日价格走势",
                            "unit": "元/吨",
                            "series": [],
                            "points": [],
                            "anchor_month": "2026-04",
                            "highlight": {
                                "start": "2026-04-27",
                                "end": "2026-05-03",
                            },
                        },
                    }
                },
            },
        }

        created = db.create_generated_report(
            user_id="test-user",
            session_id=session_id,
            title="Weekly Dynamic Chart Context",
            summary="summary",
            markdown="# Weekly Dynamic Chart Context",
            context=context,
            report_type="weekly",
        )

        fetched = db.get_generated_report(created["id"], "test-user")

        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["context"]["report_id"], "wr_test_dynamic_context")
        self.assertIn("weekly_chart_payload", fetched["context"])
        self.assertIn("cracked_c5", fetched["context"]["weekly_chart_payload"]["products"])


if __name__ == "__main__":
    unittest.main()