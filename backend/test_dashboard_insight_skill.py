from __future__ import annotations

from backend.src.skills.dashboard.insight_skill import build_dashboard_insights


def _combined_bundle() -> dict:
    return {
        "ok": True,
        "by_product": {
            "blowing_agent": {
                "product_id": "blowing_agent",
                "market": {
                    "product_id": "blowing_agent",
                    "as_of_date": "2026-05-14",
                    "latest_price": 7600.0,
                    "weekly_change_pct": 6.2,
                    "data_source": "real_mysql",
                },
                "erp": {
                    "product_id": "blowing_agent",
                    "period_start": "2026-04-14",
                    "period_end": "2026-05-14",
                    "sales_growth_pct": 12.0,
                    "inventory_days": 4.5,
                    "risk_level": "critical_low_stock",
                },
            },
            "c5_resin": {
                "product_id": "c5_resin",
                "market": {
                    "product_id": "c5_resin",
                    "as_of_date": "2026-05-14",
                    "latest_price": 8300.0,
                    "weekly_change_pct": -5.8,
                    "data_source": "real_mysql",
                },
                "erp": {
                    "product_id": "c5_resin",
                    "period_start": "2026-04-14",
                    "period_end": "2026-05-14",
                    "sales_growth_pct": -18.0,
                    "inventory_days": 52.0,
                    "risk_level": "high_stock",
                },
            },
            "naphtha": {
                "product_id": "naphtha",
                "market": {
                    "product_id": "naphtha",
                    "as_of_date": "2026-05-14",
                    "latest_price": 7000.0,
                    "weekly_change_pct": 0.2,
                    "data_source": "real_mysql",
                },
                "erp": None,
            },
        },
    }


def test_dashboard_insight_skill_ranks_actionable_signals() -> None:
    insights = build_dashboard_insights(_combined_bundle())

    assert insights["ok"] is True
    assert insights["contract"] == "dashboard_insight_skill_v1"
    assert insights["source_counts"]["critical"] == 2
    assert insights["priority_products"][0]["product_id"] == "c5_resin"
    assert insights["priority_products"][0]["risk_code"] == "inventory_overhang"
    assert insights["priority_products"][1]["product_id"] == "blowing_agent"
    assert any("allocation" in action for action in insights["actions"])
    assert any(gap["product_id"] == "naphtha" and gap["missing"] == "erp" for gap in insights["data_gaps"])


def test_combined_insight_bundle_attaches_dashboard_insights(monkeypatch) -> None:
    from backend.src.service.insight_snapshot_service import InsightSnapshotService

    service = InsightSnapshotService()

    monkeypatch.setattr(
        service,
        "get_bundle_cached",
        lambda *args, **kwargs: {
            "ok": True,
            "stale": False,
            "refresh_enqueued": False,
            "items": [_combined_bundle()["by_product"]["blowing_agent"]["market"]],
        },
    )
    monkeypatch.setattr(
        service,
        "get_erp_bundle_cached",
        lambda *args, **kwargs: {
            "ok": True,
            "stale": False,
            "refresh_enqueued": False,
            "items": [_combined_bundle()["by_product"]["blowing_agent"]["erp"]],
        },
    )

    bundle = service.get_combined_bundle_cached(async_refresh_on_miss=False)

    assert bundle["ok"] is True
    assert bundle["dashboard_insights"]["ok"] is True
    assert bundle["dashboard_insights"]["priority_products"][0]["product_id"] == "blowing_agent"
    assert bundle["dashboard_insights"]["priority_products"][0]["severity"] == "critical"