import pytest
import importlib
import sys
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import text

from backend.src.nlu.llm_service import DisabledLLMService
from backend.src.nlu.types import TaskType
from backend.src.service.decision_service import DecisionService
import backend.src.service.forecasting_service as forecasting_module
import backend.src.service.pricing_service as pricing_module
import backend.src.service.diagnosis_service as diagnosis_module
from backend.src.service.erp_kpi_service import ProductKPI
from backend.src.service.erp_kpi_service import ErpKPIService
from backend.src.service.forecasting_service import ForecastingService
from backend.src.service.diagnosis_service import DiagnosisService
from backend.src.service.pricing_service import PricingService
from backend.src.service.evidence_compiler import EvidenceCompiler
from backend.src.service.decision_critic import DecisionCritic
from backend.src.intelligence.event_extractor import EventExtractor


def _require_mysql_backend():
    from backend.src.db.sqlite_db import db as runtime_db

    if runtime_db.engine.dialect.name != "mysql":
        pytest.skip("MySQL-backed integration test requires a MySQL DATABASE_URL")

    try:
        with runtime_db.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"MySQL-backed integration test skipped: {exc}")

    return runtime_db


def test_decision_service_surfaces_disabled_llm_configuration():
    service = DecisionService(DisabledLLMService())

    report = service.process_request(
        "What should be our pricing strategy for C5 in Q3 2024?"
    )

    assert report.status == "error"
    assert report.error_message is not None
    assert "real LLM backend" in report.error_message


def test_forecasting_service_applies_live_intelligence_adjustment(monkeypatch):
    def fake_ensemble_forecast(product_id, horizon_days, diagnosis=None, cutoff_date=None):
        return {
            "base_pct": 2.0 if horizon_days == 7 else 1.0,
            "vol_pct": 1.0,
            "latest": 5000.0,
            "arima_ok": True,
            "xgb_ok": True,
            "lstm_ok": True,
        }

    def fake_monthly_stl_forecast(product_id, diagnosis=None, reference_month=None, cutoff_date=None):
        return {
            "base_pct": 3.0,
            "vol_pct": 2.0,
            "seasonal_pct": 0.0,
            "layers_used": ["stl", "causal"],
            "stl_pct": 1.0,
            "causal_pct": 1.0,
        }

    monkeypatch.setattr(forecasting_module, "ensemble_forecast", fake_ensemble_forecast)
    monkeypatch.setattr(
        forecasting_module,
        "monthly_stl_forecast",
        fake_monthly_stl_forecast,
    )

    service = ForecastingService()
    intelligence_context = {
        "has_intelligence": True,
        "causal_analyses": [
            {
                "event_headline": "Hormuz closure",
                "entry_commodity": "crude_oil",
                "naphtha_total_pct": 4.62,
                "product_impacts": {
                    "mixed_c5": {
                        "pct_impact": 4.0,
                        "cny_impact": 200,
                        "causal_path": "event -> crude_oil -> naphtha -> mixed_c5",
                    }
                },
                "confidence_by_horizon": {"7d": 0.8},
                "narrative": "Hormuz disruption lifts C5.",
            }
        ],
    }

    baseline = service.forecast("mixed_c5")
    shifted = service.forecast("mixed_c5", intelligence_context=intelligence_context)

    baseline_mid = (
        baseline["scenarios"]["7d"]["base"]["low"]
        + baseline["scenarios"]["7d"]["base"]["high"]
    ) / 2
    shifted_mid = (
        shifted["scenarios"]["7d"]["base"]["low"]
        + shifted["scenarios"]["7d"]["base"]["high"]
    ) / 2

    assert shifted["intelligence_signal"]["active"] is True
    assert shifted["intelligence_adj_pct"]["7d"] == pytest.approx(3.2)
    assert shifted_mid > baseline_mid


def test_forecasting_service_applies_erp_kpi_tilt(monkeypatch):
    def fake_ensemble_forecast(product_id, horizon_days, diagnosis=None, cutoff_date=None):
        return {
            "base_pct": 1.0 if horizon_days == 7 else 0.5,
            "vol_pct": 1.0,
            "latest": 5000.0,
            "arima_ok": True,
            "xgb_ok": True,
            "lstm_ok": True,
        }

    def fake_monthly_stl_forecast(product_id, diagnosis=None, reference_month=None, cutoff_date=None):
        return {
            "base_pct": 1.5,
            "vol_pct": 2.0,
            "seasonal_pct": 0.0,
            "layers_used": ["stl", "causal"],
            "stl_pct": 0.5,
            "causal_pct": 0.5,
        }

    monkeypatch.setattr(forecasting_module, "ensemble_forecast", fake_ensemble_forecast)
    monkeypatch.setattr(
        forecasting_module,
        "monthly_stl_forecast",
        fake_monthly_stl_forecast,
    )
    monkeypatch.setattr(
        forecasting_module.erp_kpi_service,
        "get_product_kpi",
        lambda product_id, region="east_china": None,
    )

    service = ForecastingService()
    baseline = service.forecast("mixed_c5")

    monkeypatch.setattr(
        forecasting_module.erp_kpi_service,
        "get_product_kpi",
        lambda product_id, region="east_china": ProductKPI(
            product_id="cracked_c5",
            region=region,
            inventory_tons=180.0,
            inventory_days=6.0,
            sales_30d_ton=900.0,
            utilization_pct=88.0,
        ),
    )

    shifted = service.forecast("mixed_c5")

    baseline_mid = (
        baseline["scenarios"]["7d"]["base"]["low"]
        + baseline["scenarios"]["7d"]["base"]["high"]
    ) / 2
    shifted_mid = (
        shifted["scenarios"]["7d"]["base"]["low"]
        + shifted["scenarios"]["7d"]["base"]["high"]
    ) / 2

    assert shifted["erp_kpi_signal"]["active"] is True
    assert shifted["erp_kpi_signal"]["bias"] == "bullish"
    assert shifted["erp_kpi_adj_pct"]["7d"] == pytest.approx(0.95)
    assert "erp_kpi=bullish" in shifted["method_notes"]


def test_erp_inventory_alias_mapping_for_isoprene(monkeypatch):
    service = ErpKPIService()
    captured: dict[str, tuple[str, ...]] = {}

    def fake_inventory(material_names):
        captured["inventory_names"] = material_names
        return 1898.72

    def fake_sales_30d(material_names):
        captured["sales_names"] = material_names
        return 900.0

    monkeypatch.setattr(service, "_query_inventory_tons", fake_inventory)
    monkeypatch.setattr(service, "_query_sales_30d", fake_sales_30d)

    inv = service.get_inventory("isoprene")

    assert inv["found"] is True
    assert inv["product_id"] == "isoprene"
    assert inv["total_tons"] == pytest.approx(1898.72)
    assert inv["inventory_days"] == pytest.approx(63.3)
    assert inv["sales_30d_tons"] == pytest.approx(900.0)
    assert "异戊二烯" in captured["inventory_names"]
    assert captured["inventory_names"] == captured["sales_names"]


def test_diagnosis_service_surfaces_erp_kpi_signal(monkeypatch):
    monkeypatch.setattr(
        diagnosis_module.erp_kpi_service,
        "get_product_kpi",
        lambda product_id, region="east_china": ProductKPI(
            product_id="cracked_c5",
            region=region,
            inventory_tons=180.0,
            inventory_days=6.0,
            sales_30d_ton=900.0,
            utilization_pct=88.0,
        ),
    )

    service = DiagnosisService()
    monkeypatch.setattr(service, "_get_multi_hop_upstream", lambda product_id: [])
    monkeypatch.setattr(service, "_get_multi_hop_downstream", lambda product_id: [])
    monkeypatch.setattr(service, "_generate_hypotheses", lambda *args, **kwargs: [])
    monkeypatch.setattr(service, "_score_hypotheses", lambda *args, **kwargs: [])
    monkeypatch.setattr(service, "_propagate_downstream_signals", lambda product_id: {})

    diagnosis = service.diagnose(
        "cracked_c5",
        {
            "products": {
                "cracked_c5": {
                    "id": "cracked_c5",
                    "name_en": "Cracked C5",
                    "change_7d_pct": 0.2,
                    "anomaly": {},
                }
            }
        },
    )

    assert diagnosis["erp_kpi_signal"]["active"] is True
    assert diagnosis["erp_kpi_signal"]["bias"] == "bullish"
    assert diagnosis["net_signal"] == "bullish"
    assert diagnosis["primary_driver"]["id"] == "erp_kpi_signal"
    assert diagnosis["primary_driver"]["role"] == "erp_kpi"
    assert diagnosis["chain_impact"][0].startswith("ERP KPI tilt:")


def test_diagnosis_prompt_text_includes_erp_kpi_signal():
    rendered = DiagnosisService().to_prompt_text(
        {
            "focal_product": "cracked_c5",
            "market_regime": "bullish",
            "regime_confidence": 0.62,
            "net_signal": "bullish",
            "primary_driver": {
                "id": "erp_kpi_signal",
                "name_en": "ERP Inventory / Sales Signal",
                "role": "erp_kpi",
                "change_7d_pct": 0.95,
                "confidence": 0.7,
                "hop": 0,
                "hypothesis": "erp_kpi_signal",
                "hypothesis_confidence": 0.7,
            },
            "secondary_drivers": [],
            "chain_impact": [],
            "evidence_sources": [],
            "contradicting_factors": [],
            "downstream_propagated_signals": {},
            "intelligence_signal": {"active": False},
            "erp_kpi_signal": {
                "active": True,
                "bias": "bullish",
                "adjustment_pct": {"7d": 0.95},
                "summary": "tight inventory coverage, high operating rate; inventory_days=6.0, sales_30d_ton=900, utilization_pct=88.0",
            },
        }
    )

    assert "ERP KPI       : BULLISH +0.95%" in rendered
    assert "tight inventory coverage, high operating rate" in rendered


def test_evidence_compiler_produces_competing_evidence_bundle():
    compiler = EvidenceCompiler()

    bundle = compiler.compile(
        product_id="mixed_c5",
        perception_snapshot={
            "products": {
                "mixed_c5": {
                    "change_7d_pct": 1.2,
                }
            }
        },
        diagnosis={"market_regime": "tight_supply", "net_signal": "bullish"},
        forecast={
            "latest_market_price": 5000,
            "scenarios": {"7d": {"base": {"low": 5050, "high": 5150}}},
        },
        pricing_decision={"recommended_price": 4950, "latest_market_price": 5000},
        intelligence_context={
            "causal_analyses": [
                {
                    "event_headline": "Plant restart eases short-term tightness",
                    "product_impacts": {"mixed_c5": {"pct_impact": -1.8}},
                }
            ]
        },
    )

    assert bundle["dominant_view"] in {"bullish", "mixed", "bearish"}
    assert bundle["evidence_for_rise"]
    assert bundle["evidence_for_fall"]
    assert bundle["contradictions"]

    rendered = compiler.to_prompt_text(bundle)
    assert "EVIDENCE COMPILER:" in rendered
    assert "Evidence For Rise:" in rendered
    assert "Evidence For Fall:" in rendered


def test_event_extractor_normalizes_invalid_event_type_with_keyword_fallback():
    class StubLLM:
        def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
            return {
                "event_type": "macro_unknown",
                "headline": "OPEC signals deeper output cuts",
                "affected_commodities": ["crude_oil", "naphtha"],
                "direction": "bullish",
                "magnitude_estimate": "very_large",
                "certainty": "almost_sure",
                "time_horizon": "near_term",
                "geographic_scope": "middle_east",
                "relevance_to_c5_c9": "direct",
            }

    extractor = EventExtractor(StubLLM())
    event = extractor.extract(
        "OPEC discussed fresh production cuts this week, raising crude concerns.",
        category="crude_oil",
    )

    assert event is not None
    assert event.event_type == "opec_decision"
    assert event.magnitude_estimate == "medium"
    assert event.certainty == "likely"
    assert event.time_horizon == "short"


def test_decision_critic_flags_mixed_evidence_and_overconfidence():
    critic = DecisionCritic()

    result = critic.review(
        diagnosis={"regime_confidence": 0.82},
        forecast={},
        pricing_decision={"action": "raise"},
        evidence_bundle={
            "dominant_view": "mixed",
            "bullish_score": 1.0,
            "bearish_score": 1.2,
            "residual_uncertainty": "high",
            "contradictions": ["upstream bullish but downstream weak"],
        },
    )

    assert result["critic_level"] in {"warn", "high_risk"}
    assert "contradictory_evidence_present" in result["flags"]
    assert "potential_overconfidence" in result["flags"]

    rendered = critic.to_prompt_text(result)
    assert "DECISION CRITIC:" in rendered
    assert "Flags:" in rendered


def test_pricing_service_uses_intelligence_for_recommendation_but_not_market_price(
    monkeypatch,
):
    monkeypatch.setattr(pricing_module, "_compute_macro_adj", lambda *args: (0, ""))
    monkeypatch.setattr(
        pricing_module,
        "_compute_downstream_adj",
        lambda *args: (0, ""),
    )
    monkeypatch.setattr(
        pricing_module,
        "_compute_inventory_pressure",
        lambda *args: (0, "inventory neutral", 2.0),
    )
    monkeypatch.setattr(
        pricing_module,
        "_compute_action_bias",
        lambda *args, **kwargs: ("neutral", 0, ""),
    )
    monkeypatch.setattr(
        pricing_module,
        "_compute_weekly_plan_pressure",
        lambda *args, **kwargs: (0, "weekly plan neutral", None),
    )
    monkeypatch.setattr(pricing_module, "get_active_override", lambda: None)

    perception_snapshot = {
        "products": {
            "mixed_c5": {
                "id": "mixed_c5",
                "name_en": "Mixed C5",
                "name_cn": "Mixed C5",
                "unit": "CNY/ton",
                "latest_price": 5000.0,
            },
            "naphtha": {
                "id": "naphtha",
                "name_en": "Naphtha",
                "name_cn": "Naphtha",
                "unit": "CNY/ton",
                "latest_price": 4500.0,
                "change_7d_pct": 0.0,
            },
        }
    }
    intelligence_context = {
        "has_intelligence": True,
        "causal_analyses": [
            {
                "event_headline": "Hormuz closure",
                "entry_commodity": "crude_oil",
                "naphtha_total_pct": 4.62,
                "product_impacts": {
                    "mixed_c5": {
                        "pct_impact": 4.0,
                        "cny_impact": 200,
                        "causal_path": "event -> crude_oil -> naphtha -> mixed_c5",
                    }
                },
                "confidence_by_horizon": {"7d": 0.8},
                "narrative": "Hormuz disruption lifts C5.",
            }
        ],
    }

    service = PricingService()
    baseline = service.compute_pricing_decision("mixed_c5", perception_snapshot)
    shifted = service.compute_pricing_decision(
        "mixed_c5",
        perception_snapshot,
        intelligence_context=intelligence_context,
    )

    assert shifted["latest_market_price"] == 5000
    assert shifted["recommended_price"] > baseline["recommended_price"]
    assert shifted["intelligence_signal"]["active"] is True
    assert any(
        step["label"] == "实时情报事件调整" for step in shifted["adjustment_steps"]
    )


def test_pricing_service_applies_weekly_plan_pressure(monkeypatch):
    monkeypatch.setattr(pricing_module, "_compute_macro_adj", lambda *args: (0, ""))
    monkeypatch.setattr(
        pricing_module,
        "_compute_downstream_adj",
        lambda *args: (0, ""),
    )
    monkeypatch.setattr(
        pricing_module,
        "_compute_inventory_pressure",
        lambda *args: (0, "inventory neutral", 2.0),
    )
    monkeypatch.setattr(
        pricing_module,
        "_compute_action_bias",
        lambda *args, **kwargs: ("neutral", 0, ""),
    )
    monkeypatch.setattr(pricing_module, "get_active_override", lambda: None)

    perception_snapshot = {
        "products": {
            "mixed_c5": {
                "id": "mixed_c5",
                "name_en": "Mixed C5",
                "name_cn": "Mixed C5",
                "unit": "CNY/ton",
                "latest_price": 5000.0,
            },
            "naphtha": {
                "id": "naphtha",
                "name_en": "Naphtha",
                "name_cn": "Naphtha",
                "unit": "CNY/ton",
                "latest_price": 4500.0,
                "change_7d_pct": 0.0,
            },
        }
    }

    monkeypatch.setattr(
        pricing_module,
        "_compute_weekly_plan_pressure",
        lambda *args, **kwargs: (0, "weekly plan neutral", None),
    )
    baseline = PricingService().compute_pricing_decision(
        "mixed_c5", perception_snapshot
    )

    monkeypatch.setattr(
        pricing_module,
        "_compute_weekly_plan_pressure",
        lambda *args, **kwargs: (
            100,
            "weekly plan is tight",
            {
                "product_id": "mixed_c5",
                "plan_date": "2026-04-01",
                "pressure_signal": "tight",
                "net_supply_gap_qty": 240.0,
            },
        ),
    )

    decision = PricingService().compute_pricing_decision(
        "mixed_c5", perception_snapshot
    )

    assert decision["weekly_plan"]["pressure_signal"] == "tight"
    assert any(step["step"] == "6A" for step in decision["adjustment_steps"])
    assert decision["recommended_price"] > baseline["recommended_price"]


def test_api_chat_surfaces_live_intelligence_context(monkeypatch, tmp_path):
    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient

    sqlite_path = tmp_path / "test_chat.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{sqlite_path}")
    monkeypatch.setenv("USE_REAL_LLM", "true")
    monkeypatch.setenv("MARKET_PRICES_REFRESH_ON_STARTUP", "false")

    class StubLLM:
        def __init__(self):
            self.client = object()
            self.model = "stub-llm"
            self.is_ready = True

        def generate_json(
            self,
            system_prompt: str,
            user_prompt: str,
            route_hint: str | None = None,
            sanitized: bool | None = None,
        ) -> dict:
            if "TASK_CLASSIFICATION" in system_prompt:
                return {"task_type": "market_analysis_decision", "confidence": 1.0}
            if "ENTITY_EXTRACTION" in system_prompt:
                return {
                    "product": "Cracked C5",
                    "industry": "Petrochemical",
                    "period": "Current Week",
                    "region": "China",
                }
            return {}

        def generate_completion(
            self,
            system_prompt: str,
            user_prompt: str,
            route_hint: str | None = None,
            sanitized: bool | None = None,
        ) -> str:
            lines = [line for line in user_prompt.splitlines() if line.strip()]
            return "\n".join(lines[:80])

        def request_scope(self, route_hint=None, sanitized=None):
            class _Scope:
                def __enter__(self_inner):
                    return None

                def __exit__(self_inner, exc_type, exc, tb):
                    return False

            return _Scope()

    for module_name in [
        "backend.server",
        "backend.src.db.sqlite_db",
    ]:
        sys.modules.pop(module_name, None)

    import backend.src.nlu.llm_service as llm_module
    import backend.src.service.alert_service as alert_module
    import backend.src.intelligence.intelligence_orchestrator as intel_module

    monkeypatch.setattr(llm_module, "RealLLMService", StubLLM)
    monkeypatch.setattr(alert_module.alert_worker, "start", lambda: None)
    monkeypatch.setattr(intel_module, "init_intelligence", lambda llm: None)
    monkeypatch.setattr(intel_module, "get_news_agent", lambda: None)
    monkeypatch.setattr(intel_module, "get_override_engine", lambda: None)
    monkeypatch.setattr(intel_module, "get_learning_loop", lambda: None)
    monkeypatch.setattr(
        intel_module,
        "get_intelligence_context",
        lambda hours=4, **kwargs: {
            "has_intelligence": True,
            "checked": True,
            "checked_hours": hours,
            "events": [
                {
                    "headline": "Hormuz closure",
                    "direction": "bullish",
                    "certainty": "confirmed",
                    "detected_at": "2026-03-10T12:00:00",
                    "raw_quote": "Ships attacked in the strait",
                    "source_url": "https://example.com/hormuz-live",
                }
            ],
            "causal_analyses": [
                {
                    "event_headline": "Hormuz closure",
                    "entry_commodity": "crude_oil",
                    "naphtha_total_pct": 4.62,
                    "product_impacts": {
                        "mixed_c5": {
                            "pct_impact": 4.0,
                            "cny_impact": 200,
                            "causal_path": "event -> crude_oil -> naphtha -> mixed_c5",
                        }
                    },
                    "confidence_by_horizon": {"7d": 0.8},
                    "narrative": "Hormuz disruption lifts C5.",
                    "recommended_override_type": "crude_spike",
                }
            ],
            "scenarios": [],
            "override_decisions": [],
            "intelligence_summary": "DETECTED 1 RECENT MARKET EVENT(S): Hormuz closure",
        },
    )

    server = importlib.import_module("backend.server")

    server.market_data.refresh = lambda: None
    server.db.get_user_by_token = lambda token: (
        {
            "id": "api-test-user",
            "username": "api-test-user",
            "is_admin": True,
            "is_active": True,
            "must_change_password": False,
        }
        if token == "test-token"
        else None
    )

    session_messages: dict[str, list[dict]] = {}

    def fake_create_session(user_id: str, title: str = "New Chat") -> str:
        session_id = "test-session-1"
        session_messages.setdefault(session_id, [])
        return session_id

    def fake_add_message(
        session_id: str, role: str, content: str, context: dict | None = None
    ) -> None:
        session_messages.setdefault(session_id, []).append(
            {"role": role, "content": content, "context": context}
        )

    server.db.create_session = fake_create_session
    server.db.add_message = fake_add_message
    server.db.get_messages = lambda session_id: session_messages.get(session_id, [])
    server.db.get_sessions = lambda user_id: []
    server.db.session_belongs_to_user = lambda session_id, user_id: True

    import backend.src.service.perception_service as perception_module

    perception_module.perception_service.build_snapshot = lambda cutoff_date=None: {
        "reference_date": "2026-03-10",
        "products": {
            "cracked_c5": {
                "id": "cracked_c5",
                "name_en": "Cracked C5",
                "name_cn": "Cracked C5",
                "unit": "CNY/ton",
                "latest_price": 5000.0,
                "latest_date": "2026-03-10",
                "change_7d_pct": 1.2,
                "anomaly": {"is_anomaly": False},
                "data_source": "real",
            },
            "naphtha": {
                "id": "naphtha",
                "name_en": "Naphtha",
                "name_cn": "Naphtha",
                "unit": "CNY/ton",
                "latest_price": 4500.0,
                "latest_date": "2026-03-10",
                "change_7d_pct": 0.5,
                "anomaly": {"is_anomaly": False},
                "data_source": "real",
            },
        },
        "anomalies": [],
        "historical_matches": [],
        "alert_level": "WATCH",
        "market_phase": "normal",
        "crude_trend": "up",
        "naphtha_trend": "up",
    }

    monkeypatch.setattr(
        forecasting_module,
        "ensemble_forecast",
        lambda *args, **kwargs: {
            "base_pct": 2.0,
            "vol_pct": 1.0,
            "latest": 5000.0,
            "arima_ok": True,
            "xgb_ok": True,
            "lstm_ok": True,
        },
    )
    monkeypatch.setattr(
        forecasting_module,
        "monthly_stl_forecast",
        lambda *args, **kwargs: {
            "base_pct": 3.0,
            "vol_pct": 2.0,
            "seasonal_pct": 0.0,
            "layers_used": ["stl", "causal"],
            "stl_pct": 1.0,
            "causal_pct": 1.0,
        },
    )
    monkeypatch.setattr(pricing_module, "_compute_macro_adj", lambda *args: (0, ""))
    monkeypatch.setattr(
        pricing_module, "_compute_downstream_adj", lambda *args: (0, "")
    )
    monkeypatch.setattr(
        pricing_module,
        "_compute_inventory_pressure",
        lambda *args: (0, "inventory neutral", 2.0),
    )
    monkeypatch.setattr(
        pricing_module,
        "_compute_action_bias",
        lambda *args, **kwargs: ("neutral", 0, ""),
    )
    monkeypatch.setattr(pricing_module, "get_active_override", lambda: None)

    import backend.src.rag.vector_store as vector_store_module

    vector_store_module.kb.query = lambda *args, **kwargs: []
    vector_store_module.kb.query_weekly = lambda *args, **kwargs: []
    vector_store_module.kb.query_past_decisions = lambda *args, **kwargs: []
    vector_store_module.kb.query_news_events = lambda *args, **kwargs: []
    vector_store_module.kb.store_decision = lambda *args, **kwargs: None

    with TestClient(server.app) as client:
        response = client.post(
            "/api/chat",
            json={
                "message": "Analyze the current cracked C5 market situation and recommend the pricing action"
            },
            headers={"Authorization": "Bearer test-token"},
        )

    assert response.status_code == 200
    payload = response.json()
    # Keyword canonicalization normalizes "cracked C5" to the internal bucket "Mixed C5".
    assert payload["context"]["product"] == "Mixed C5"
    assert payload["session_id"] == "test-session-1"

    assembled_context = payload["context"]["additional_info"]
    assert "DETECTED 1 RECENT MARKET EVENT(S): Hormuz closure" in assembled_context
    assert "Live intelligence adj: bullish +4.00%" in assembled_context
    assert "PRICING DECISION (STAGE 4):" in assembled_context
    assert "Live Intelligence: bullish +4.00% (+200 CNY/ton)" in assembled_context
    assert "[INTEL-1] Ingested event: Hormuz closure" in payload["markdown"]


def test_build_references_block_emits_ingested_event_references():
    from backend.src.engine.decision_graph import _build_references_block

    block = _build_references_block(
        {
            "intelligence_context": {
                "has_intelligence": True,
                "checked": True,
                "checked_hours": 4,
                "events": [
                    {
                        "headline": "Reuters: Hormuz disruption pushes Brent higher",
                        "certainty": "confirmed",
                        "detected_at": "2026-03-15T09:15:00",
                        "raw_quote": "Traffic through the strait faded away",
                        "source_url": "https://www.reuters.com/example-hormuz",
                    }
                ],
            },
            "tool_info": "",
            "rag_info": "",
            "web_search_results": "",
            "erp_data": "",
            "perception_snapshot": {},
        },
        "en",
    )

    assert (
        "[INTEL-1] Ingested event: Reuters: Hormuz disruption pushes Brent higher"
        in block
    )
    assert "confirmed" in block
    assert "https://www.reuters.com/example-hormuz" in block


def test_build_references_block_prefers_forecast_driving_events():
    from backend.src.engine.decision_graph import _build_references_block

    block = _build_references_block(
        {
            "forecast": {
                "intelligence_signal": {
                    "events": [
                        {
                            "event_id": "evt-forecast-1",
                            "headline": "Forecast-driving Hormuz disruption",
                            "certainty": "confirmed",
                            "detected_at": "2026-03-16T10:00:00",
                            "raw_quote": "Traffic through the strait faded away",
                            "source_url": "https://www.reuters.com/forecast-hormuz",
                        }
                    ]
                }
            },
            "intelligence_context": {
                "events": [
                    {
                        "event_id": "evt-generic-1",
                        "headline": "Generic weekly summary event",
                        "detected_at": "2026-03-10T10:00:00",
                        "source_url": "https://example.com/generic-summary",
                    }
                ]
            },
            "tool_info": "",
            "rag_info": "",
            "web_search_results": "",
            "erp_data": "",
            "perception_snapshot": {},
        },
        "en",
    )

    assert "Forecast-driving Hormuz disruption" in block
    assert "https://www.reuters.com/forecast-hormuz" in block
    assert "Generic weekly summary event" not in block


def test_two_named_products_do_not_trigger_portfolio_mode():
    from backend.src.engine.decision_graph import _looks_like_portfolio_query

    user_context = SimpleNamespace(product="C5 and isoprene")

    assert (
        _looks_like_portfolio_query(
            "C5 and isoprene this week",
            user_context,
            ["cracked_c5", "isoprene"],
        )
        is False
    )
    assert (
        _looks_like_portfolio_query(
            "Portfolio view for C5, isoprene, and piperylene",
            user_context,
            ["cracked_c5", "isoprene", "piperylene"],
        )
        is True
    )


def test_analysis_only_guardrails_skip_unsolicited_delta_and_pricing_blocks():
    from backend.src.engine.decision_graph import _apply_deterministic_guardrails
    from backend.src.prompts.task_prompt import TaskType

    report = "## 1) Direct Answer\nC5 and isoprene are both up this week."
    state = {
        "user_query": "C5 and isoprene this week",
        "response_mode": "structured",
        "parsed_intent": SimpleNamespace(task_type=TaskType.MARKET_ANALYSIS_ONLY),
        "delta_summary_text": "Compared with the prior answer: no change.",
        "pricing_decision": {
            "recommended_price": 12800,
            "adjustment_steps": [
                {"label": "Anchor", "delta": 0, "note": "Sinopec anchor"},
                {"label": "Step 3", "delta": 150, "note": "Cost push"},
            ],
        },
        "portfolio_mode": False,
        "tool_info": "",
        "rag_info": "",
        "erp_data": "",
        "perception_snapshot": {},
        "explanation_mode": False,
    }

    guarded = _apply_deterministic_guardrails(report, state)

    assert "Changes vs last same-product query" not in guarded
    assert "Pricing Ladder" not in guarded
    assert "You may also ask:" not in guarded


def test_reasoning_scrubber_drops_reasoning_only_output():
    from backend.src.nlu.llm_service import RealLLMService

    service = RealLLMService.__new__(RealLLMService)
    cleaned = service._strip_reasoning_blocks(
        "Thinking Process:\n1. Compare the live move with feedstock signals.\n2. Decide whether to chase the spike."
    )

    assert cleaned == ""


def test_placeholder_skeleton_report_is_replaced_by_guardrail():
    from backend.src.engine.decision_graph import (
        _apply_deterministic_guardrails,
        _looks_like_placeholder_skeleton_report,
    )

    garbled = """
，，。

###

| | | | |
| :--- | :--- | :--- | :--- |
| **** | **** | 1. ****：OPEC+，Brent Crude Oil2.5%。<br>2. ****：、PX，1.5%-3%。 | ****。，。 |

---
**Provenance (scan-friendly)**
- Inference
"""
    state = {
        "parsed_intent": SimpleNamespace(task_type=TaskType.MARKET_ANALYSIS_DECISION),
        "user_query": "结合原料、能耗和加工利润，分析本周成本支撑变化。",
        "user_prefs": {"language": "zh"},
        "response_mode": "full_report",
        "explanation_mode": False,
        "graph_product_id": "cracked_c5",
        "user_context": SimpleNamespace(product="裂解C5", period="本周"),
        "diagnosis": {
            "root_cause": "原料与下游传导不一致，成本支撑需要按原油、石脑油和加工价差拆开判断。",
            "chain_impact": ["原油/石脑油影响成本端", "PX/PTA价差影响下游利润传导"],
        },
        "forecast": {},
        "pricing_decision": {"no_pricing": True},
        "tool_info": "",
        "rag_info": "",
        "erp_data": "",
        "perception_snapshot": {},
    }

    guarded = _apply_deterministic_guardrails(garbled, state)

    assert _looks_like_placeholder_skeleton_report(garbled)
    assert "****" not in guarded
    assert "## 🔍 02 · 关键驱动因素" in guarded
    assert "原料与下游传导不一致" in guarded


def test_stripped_chinese_fragment_is_replaced_by_guardrail():
    from backend.src.engine.decision_graph import _apply_deterministic_guardrails

    fragment = """
；

- A 75%，120%。

- ****，，。
"""
    state = {
        "parsed_intent": SimpleNamespace(task_type=TaskType.MARKET_ANALYSIS_DECISION),
        "user_query": "指出当前最紧急的预警，并说明今天是否需要立即动作。",
        "user_prefs": {"language": "zh"},
        "response_mode": "full_report",
        "explanation_mode": False,
        "graph_product_id": "isoprene",
        "user_context": SimpleNamespace(product="异戊二烯", period="今天"),
        "diagnosis": {
            "market_regime": "bearish",
            "primary_driver": {"name_en": "isoprene", "change_7d_pct": -2.5},
        },
        "forecast": {"scenarios": {"7d": {"base": {"low": 11000, "high": 11600}}}},
        "pricing_decision": {
            "product_id": "isoprene",
            "product_name_cn": "异戊二烯",
            "latest_market_price": 11500,
            "recommended_price": 11300,
            "price_range": [11200, 11400],
            "unit": "元/吨",
            "action_cn": "下调报价",
            "confidence": 0.75,
        },
        "tool_info": "",
        "rag_info": "",
        "erp_data": "",
        "perception_snapshot": {},
    }

    guarded = _apply_deterministic_guardrails(fragment, state)

    assert "****" not in guarded
    assert "## 💡 04 · 定价决策" in guarded
    assert "异戊二烯" in guarded
    assert "下调报价" in guarded


def test_db_only_price_move_action_query_short_circuits_to_deterministic_report(monkeypatch):
    import backend.src.engine.decision_graph as decision_graph

    monkeypatch.setenv("DATABASE_ONLY_MODE", "true")

    state = {
        "parsed_intent": SimpleNamespace(task_type=TaskType.MARKET_ANALYSIS_DECISION),
        "user_query": "C5 Petroleum Resin moved +17.1% in 7 days. Explain the supply-demand driver, the pricing risk, and what action we should take this week.",
        "resolved_chat_mode": "analysis",
        "mode_reason": "test",
        "graph_product_id": "c5_resin",
        "user_context": SimpleNamespace(product="C5 Petroleum Resin", period="Current Week"),
        "perception_snapshot": {
            "crude_trend": "down",
            "naphtha_trend": "down",
            "products": {
                "c5_resin": {
                    "id": "c5_resin",
                    "name_en": "C5 Petroleum Resin",
                    "latest_price": 16000.0,
                    "latest_date": "2026-04-08",
                    "change_7d_pct": 17.07,
                    "unit": "ton",
                }
            },
        },
        "diagnosis": {
            "market_regime": "bullish",
            "divergence_detected": {"gap": 3.52},
            "demand_status": {
                "effective_demand_status": "peak_for_infra",
                "pricing_note": "Pre-construction season. Demand will improve. Hold price; do not over-discount.",
            },
            "erp_kpi_signal": {"inventory_days": 23.28},
        },
        "forecast": {
            "scenarios": {
                "7d": {
                    "base": {"low": 5262.18, "high": 14417.63},
                    "bull": {"low": 14417.63, "high": 23573.09},
                    "bear": {"low": -3893.28, "high": 5262.18},
                }
            }
        },
        "pricing_decision": {
            "product_id": "c5_resin",
            "product_name_en": "C5 Petroleum Resin",
            "latest_market_price": 16000,
            "recommended_price": 10950,
            "price_range": [10850, 11050],
            "confidence": 0.64,
            "price_delta_vs_market": -5050,
            "dissenting_view": "If the monthly anchor is not cut, aggressive price cuts can damage margin.",
            "triggers": [
                {"condition": "naphtha 7d decline widens beyond 8.4%", "action": "move to 10,850 CNY/t"},
                {"condition": "weekly orders fall by at least 10%", "action": "hold 10,950 CNY/t and reassess in 3-5 days"},
            ],
            "watch_signals": ["Jinhai quotes", "Luhua quotes"],
            "weekly_plan": {"note": "Weekly plan is broadly balanced versus expected supply and demand."},
        },
    }

    out = decision_graph._node_short_circuit_check(state)

    assert out.get("report") is not None
    assert out["report"].status == "success"
    assert "## 1) Direct Answer" in out["report"].raw_markdown
    assert "## 4) Action We Should Take This Week" in out["report"].raw_markdown
    assert "10,950 CNY/t" in out["report"].raw_markdown


def test_db_only_price_move_action_query_returns_native_chinese_report(monkeypatch):
    import backend.src.engine.decision_graph as decision_graph

    monkeypatch.setenv("DATABASE_ONLY_MODE", "true")

    state = {
        "parsed_intent": SimpleNamespace(task_type=TaskType.MARKET_ANALYSIS_DECISION),
        "user_query": "如果日本石脑油7日上涨接近两位数，请解释背后的供需驱动、传导风险，以及我们本周应采取的动作。",
        "resolved_chat_mode": "analysis",
        "mode_reason": "test",
        "graph_product_id": "naphtha",
        "user_prefs": {"language": "zh"},
        "user_context": SimpleNamespace(product="日本石脑油", period="本周"),
        "perception_snapshot": {
            "crude_trend": "down",
            "naphtha_trend": "down",
            "products": {
                "naphtha": {
                    "id": "naphtha",
                    "name_en": "Japanese Naphtha",
                    "name_cn": "日本石脑油",
                    "latest_price": 1017.0,
                    "latest_date": "2026-04-08",
                    "change_7d_pct": -7.45,
                    "unit": "ton",
                }
            },
        },
        "diagnosis": {
            "market_regime": "bearish",
            "divergence_detected": {"gap": 3.52},
            "demand_status": {
                "effective_demand_status": "normal",
                "pricing_note": "下游接货节奏没有明显放量。",
            },
            "erp_kpi_signal": {"inventory_days": 12.0},
        },
        "forecast": {
            "scenarios": {
                "7d": {
                    "base": {"low": 910, "high": 1124},
                    "bull": {"low": 1124, "high": 1338},
                    "bear": {"low": 696, "high": 910},
                }
            }
        },
        "pricing_decision": {
            "product_id": "naphtha",
            "product_name_cn": "日本石脑油",
            "latest_market_price": 1017,
            "recommended_price": 980,
            "price_range": [960, 1000],
            "confidence": 0.64,
            "triggers": [
                {"condition": "买方继续抵触高价", "action": "回到防守报价区间"},
            ],
            "watch_signals": ["竞品报价", "订单转化率"],
            "weekly_plan": {"note": "本周接单与供给总体平衡。"},
        },
    }

    out = decision_graph._node_short_circuit_check(state)
    report = out["report"].raw_markdown

    assert "## 1) 直接回答" in report
    assert "## 4) 本周操作建议" in report
    assert "日本石脑油短期价格表现偏强" in report
    assert "Japanese Naphtha is showing short-term bullish momentum" not in report
    assert "do not build high-cost inventory into this spike" not in report


def test_competitor_portfolio_prompt_does_not_short_circuit_out_of_scope(monkeypatch):
    import backend.src.engine.decision_graph as decision_graph

    monkeypatch.setenv("DATABASE_ONLY_MODE", "true")

    state = {
        "parsed_intent": SimpleNamespace(task_type=TaskType.MARKET_ANALYSIS_DECISION),
        "user_query": "梳理主要竞品装置运行、报价变化、以及对我们成交的影响。",
        "resolved_chat_mode": "analysis",
        "mode_reason": "test",
        "user_context": SimpleNamespace(product="", period="本周"),
        "perception_snapshot": {
            "products": {
                "isoprene": {"id": "isoprene", "latest_price": 17104, "change_7d_pct": 33.62, "data_source": "real"},
                "piperylene": {"id": "piperylene", "latest_price": 14933, "change_7d_pct": 52.92, "data_source": "real"},
                "dcpd": {"id": "dcpd", "latest_price": 8960, "change_7d_pct": 20.27, "data_source": "real"},
                "c5_resin": {"id": "c5_resin", "latest_price": 16000, "change_7d_pct": 17.07, "data_source": "real"},
            }
        },
        "graph_product_ids": [],
        "out_of_scope_detected": False,
    }

    resolved = decision_graph._node_resolve_product(state)

    assert resolved["portfolio_mode"] is True
    assert resolved["out_of_scope_detected"] is False
    assert len(resolved["graph_product_ids"]) >= 1


def test_competitor_portfolio_prompt_short_circuits_to_multi_product_summary(monkeypatch):
    import backend.src.engine.decision_graph as decision_graph

    monkeypatch.setenv("DATABASE_ONLY_MODE", "true")

    state = {
        "parsed_intent": SimpleNamespace(task_type=TaskType.MARKET_ANALYSIS_DECISION),
        "user_query": "梳理主要竞品装置运行、报价变化、以及对我们成交的影响。",
        "resolved_chat_mode": "analysis",
        "mode_reason": "test",
        "user_context": SimpleNamespace(product="", period="本周"),
        "portfolio_mode": True,
        "graph_product_ids": ["piperylene", "isoprene", "dcpd"],
        "perception_snapshot": {
            "products": {
                "piperylene": {"name_cn": "间戊二烯", "latest_price": 14933, "change_7d_pct": 52.92},
                "isoprene": {"name_cn": "异戊二烯", "latest_price": 17104, "change_7d_pct": 33.62},
                "dcpd": {"name_cn": "双环戊二烯", "latest_price": 8960, "change_7d_pct": 20.27},
            }
        },
    }

    out = decision_graph._node_short_circuit_check(state)
    report = out["report"].raw_markdown

    assert "## 快速结论" in report
    assert "组合分析" in report
    assert "对我们成交的影响" in report
    assert "单一产品报价问答" in report


def test_deep_research_prompt_short_circuits_to_professional_research_report(monkeypatch):
    import backend.src.engine.decision_graph as decision_graph

    monkeypatch.setenv("DATABASE_ONLY_MODE", "true")

    state = {
        "parsed_intent": SimpleNamespace(task_type=TaskType.MARKET_ANALYSIS_DECISION),
        "user_query": "形成一版深度市场研究报告，包含短期预测、风险和经营启示。",
        "resolved_chat_mode": "deep_research",
        "mode_reason": "test",
        "user_context": SimpleNamespace(product="", period="本周"),
        "portfolio_mode": True,
        "graph_product_ids": [],
        "perception_snapshot": {
            "products": {
                "piperylene": {"name_cn": "间戊二烯", "latest_price": 14933, "change_7d_pct": 4.32, "data_source": "real"},
                "isoprene": {"name_cn": "异戊二烯", "latest_price": 17104, "change_7d_pct": 0.00, "data_source": "real"},
                "sis": {"name_cn": "SIS", "latest_price": 21800, "change_7d_pct": 3.81, "data_source": "real"},
                "blowing_agent": {"name_cn": "戊烷发泡剂", "latest_price": 7699, "change_7d_pct": 18.85, "data_source": "real"},
            }
        },
        "intelligence_text": "已跟踪到近期供需与价格联动事件。",
    }

    out = decision_graph._node_short_circuit_check(state)
    report = out["report"].raw_markdown

    assert "边界说明" not in report
    assert "## 快速结论" in report
    assert "## 3) 短期预测" in report
    assert "## 5) 经营启示" in report


def test_latest_market_anomaly_prompt_stays_in_scope_and_enters_portfolio_mode(monkeypatch):
    import backend.src.engine.decision_graph as decision_graph

    monkeypatch.setenv("DATABASE_ONLY_MODE", "true")

    state = {
        "parsed_intent": SimpleNamespace(task_type=TaskType.MARKET_ANALYSIS_ONLY),
        "user_query": "Find anomalies in the latest market data and explain what may be driving them.",
        "resolved_chat_mode": "analysis",
        "mode_reason": "test",
        "user_context": SimpleNamespace(product="", period="latest"),
        "perception_snapshot": {
            "products": {
                "piperylene": {"id": "piperylene", "latest_price": 14933, "change_7d_pct": 52.92, "data_source": "real"},
                "isoprene": {"id": "isoprene", "latest_price": 17104, "change_7d_pct": 33.62, "data_source": "real"},
                "dcpd": {"id": "dcpd", "latest_price": 8960, "change_7d_pct": 20.27, "data_source": "real"},
                "blowing_agent": {"id": "blowing_agent", "latest_price": 7699, "change_7d_pct": 18.85, "data_source": "real"},
            }
        },
        "graph_product_ids": [],
        "out_of_scope_detected": False,
    }

    resolved = decision_graph._node_resolve_product(state)

    assert resolved["portfolio_mode"] is True
    assert resolved["out_of_scope_detected"] is False
    assert "Find anomalies in the latest market data" in resolved["out_of_scope_target"]
    assert len(resolved["graph_product_ids"]) >= 1


def test_latest_market_anomaly_prompt_short_circuits_to_full_anomaly_report(monkeypatch):
    import backend.src.engine.decision_graph as decision_graph

    monkeypatch.setenv("DATABASE_ONLY_MODE", "true")

    state = {
        "parsed_intent": SimpleNamespace(task_type=TaskType.MARKET_ANALYSIS_ONLY),
        "user_query": "Find anomalies in the latest market data and explain what may be driving them.",
        "resolved_chat_mode": "analysis",
        "mode_reason": "test",
        "user_context": SimpleNamespace(product="", period="latest"),
        "portfolio_mode": True,
        "graph_product_ids": ["piperylene", "isoprene", "dcpd", "blowing_agent"],
        "perception_snapshot": {
            "crude_trend": "down",
            "naphtha_trend": "down",
            "products": {
                "piperylene": {"id": "piperylene", "name_en": "Piperylene", "latest_price": 14933, "change_7d_pct": 52.92, "data_source": "real", "anomaly": {"is_anomaly": True, "anomaly_score": 0.92}},
                "isoprene": {"id": "isoprene", "name_en": "Isoprene", "latest_price": 17104, "change_7d_pct": 33.62, "data_source": "real", "anomaly": {"is_anomaly": True, "anomaly_score": 0.81}},
                "dcpd": {"id": "dcpd", "name_en": "DCPD", "latest_price": 8960, "change_7d_pct": 20.27, "data_source": "real", "anomaly": {"is_anomaly": False, "anomaly_score": 0.33}},
                "blowing_agent": {"id": "blowing_agent", "name_en": "Blowing Agent", "latest_price": 7699, "change_7d_pct": 18.85, "data_source": "real", "anomaly": {"is_anomaly": True, "anomaly_score": 0.72}},
            },
        },
        "diagnosis": {
            "root_cause": "Downstream pricing is outrunning upstream feedstock moves, pointing to localized tightness rather than clean cost pass-through.",
            "risks": ["Buyer resistance may rise if competitor follow-through is weak."],
        },
        "pricing_decision": {
            "recommended_price": 14800,
            "price_range": [14700, 14900],
            "confidence": 0.48,
        },
        "forecast": {
            "scenarios": {
                "7d": {
                    "base": {"low": 14700, "high": 14900},
                }
            }
        },
    }

    out = decision_graph._node_short_circuit_check(state)
    report = out["report"].raw_markdown

    assert "## 1) Direct Answer" in report
    assert "## 2) Main Anomalies Detected" in report
    assert "## 3) What May Be Driving Them" in report
    assert "## 4) Business Implications And Watchpoints" in report
    assert "## Quick Take" not in report


def test_prompt_assembler_english_prompt_uses_english_instruction_labels():
    from backend.src.engine.prompt_assembler import PromptAssembler

    assembler = PromptAssembler()
    prompt = assembler.assemble_user_prompt(
        {"product": "Cracked C5", "region": "China"},
        "C5 and isoprene this week",
        language="en",
    )

    assert "You may also ask:" in prompt
    assert "Pricing Ladder" in prompt
    assert "您可能还想问" not in prompt
    assert "定价推导过程" not in prompt


def test_consistency_guardrail_flags_live_db_mismatch_in_report():
    from backend.src.engine.decision_graph import _build_consistency_guardrail_block

    state = {
        "tool_info": (
            "INTERNAL DATABASE LIVE DATA:\n"
            "间戊二烯 (Piperylene): 8181.0 元/吨 (7d:+24.07%) "
            "[src=market_prices|product_id=piperylene|price_type=east_china|date=2026-03-17]"
        ),
        "pricing_decision": {},
        "forecast": {},
    }
    report = "间戊二烯（Piperylene）华东市场当前价格为 8,181 元/吨，7 日涨幅为 +4.75%。"

    guarded = _build_consistency_guardrail_block(state, report, "zh")

    assert "潜在数值不一致" in guarded
    assert "24.07%" in guarded
    assert "8181" in guarded


def test_real_llm_service_detects_generate_backend(monkeypatch):
    from backend.src.nlu.llm_service import RealLLMService

    monkeypatch.setenv("LLM_BASE_URL", "http://10.0.0.1:11434/api/generate")
    monkeypatch.setenv("LLM_MODEL", "qwen3.5-27b:latest")
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    service = RealLLMService()

    assert service.backend_kind == "ollama-generate"
    assert service.is_ready is True


def test_news_ingestion_fallback_skips_blocked_and_portal_pages(monkeypatch):
    from backend.src.intelligence.news_ingestion_agent import NewsIngestionAgent
    from backend.src.intelligence.web_search_service import SearchResult

    agent = NewsIngestionAgent.__new__(NewsIngestionAgent)

    monkeypatch.setenv("TRUSTED_SOURCE_URLS", "https://wallstreetcn.com,https://www.eastmoney.com")

    blocked = SearchResult(
        title="请求存在威胁，已被拦截",
        url="https://wallstreetcn.com/",
        snippet="请求存在威胁，已被拦截 拦截时间：安全检测后由 WAF 驱动",
        source="wallstreetcn.com",
    )
    portal = SearchResult(
        title="东方财富网：财经门户，提供专业的财经、股票、行情、证券、基金、理财、银行、保险、信托、期货、黄金、股吧、博客等各类财经资讯及数据",
        url="https://www.eastmoney.com/",
        snippet="东方财富网，专业的互联网财经媒体，提供7*24小时财经资讯及全球金融市场报价。",
        source="eastmoney.com",
    )

    assert agent._fallback_event_from_result(blocked, "plant_operations") is None
    assert agent._fallback_event_from_result(portal, "plant_operations") is None


def test_news_ingestion_fallback_keeps_relevant_trusted_market_event(monkeypatch):
    from backend.src.intelligence.news_ingestion_agent import NewsIngestionAgent
    from backend.src.intelligence.web_search_service import SearchResult

    agent = NewsIngestionAgent.__new__(NewsIngestionAgent)

    monkeypatch.setenv("TRUSTED_SOURCE_URLS", "https://www.oilchem.net")

    relevant = SearchResult(
        title="华东异戊二烯市场价格上调，部分装置检修导致供应偏紧",
        url="https://www.oilchem.net/article/123",
        snippet="异戊二烯华东价格上调100元/吨，部分装置检修，供应收紧。",
        source="oilchem.net",
    )

    event = agent._fallback_event_from_result(relevant, "naphtha_petrochemical")

    assert event is not None
    assert event.source_url == "https://www.oilchem.net/article/123"
    assert "isoprene" in event.affected_commodities or "c5" in event.affected_commodities


def test_external_news_context_builder_prefers_product_specific_china_queries():
    from backend.src.intelligence.external_news_context_builder import (
        ExternalNewsContextBuilder,
    )
    from backend.src.intelligence.web_search_service import SearchResult

    class FakeWebSearch:
        def __init__(self):
            self.news_queries = []

        def search_news(self, query, max_results=8, region="wt-wt", timelimit="w"):
            self.news_queries.append((query, region, timelimit))
            return [
                SearchResult(
                    title="华东异戊二烯市场价格走高",
                    url=f"https://www.longzhong.com/article/{len(self.news_queries)}",
                    snippet="华东异戊二烯价格上调100元/吨，SIS开工率维持55%。",
                    source="longzhong.com",
                    published_date="2026-03-16",
                )
            ]

        def search(self, query, max_results=8, region="wt-wt"):
            return []

    fake = FakeWebSearch()
    builder = ExternalNewsContextBuilder(web_search=fake)

    payload = builder.build_context(
        user_query="C5 and isoprene this week",
        user_context=SimpleNamespace(product="C5 and isoprene"),
        graph_product_ids=["cracked_c5", "isoprene"],
        portfolio_mode=False,
        perception_snapshot={},
        language="en",
        mode="on",
    )

    query_texts = [item[0] for item in fake.news_queries]
    assert any("异戊二烯 市场价格 华东" in query for query in query_texts)
    assert any("裂解C5 市场价格 本周" in query for query in query_texts)
    assert "longzhong.com" in payload["formatted_text"]
    assert "score=" in payload["formatted_text"]


def test_external_news_context_builder_skips_routine_market_query_in_auto_mode():
    from backend.src.intelligence.external_news_context_builder import (
        ExternalNewsContextBuilder,
    )

    class FakeWebSearch:
        def __init__(self):
            self.news_queries = []
            self.text_queries = []

        def search_news(self, query, max_results=8, region="wt-wt", timelimit="w"):
            self.news_queries.append((query, region, timelimit))
            return []

        def search(self, query, max_results=8, region="wt-wt"):
            self.text_queries.append((query, region))
            return []

    fake = FakeWebSearch()
    builder = ExternalNewsContextBuilder(web_search=fake)

    payload = builder.build_context(
        user_query="Based on the China market database, what are the main price moves for cracked C5 and isoprene this week?",
        user_context=SimpleNamespace(product="C5 and isoprene"),
        graph_product_ids=["cracked_c5", "isoprene"],
        portfolio_mode=False,
        perception_snapshot={},
        language="en",
        mode="auto",
    )

    assert payload == {"formatted_text": "", "results": [], "queries": []}
    assert fake.news_queries == []
    assert fake.text_queries == []


def test_external_news_context_builder_ranks_authoritative_recent_result_above_noise():
    from backend.src.intelligence.external_news_context_builder import (
        ExternalNewsContextBuilder,
    )
    from backend.src.intelligence.web_search_service import SearchResult

    class FakeWebSearch:
        def search_news(self, query, max_results=8, region="wt-wt", timelimit="w"):
            return [
                SearchResult(
                    title="裂解C5华东市场价格上涨",
                    url="https://www.longzhong.com/c5-strong",
                    snippet="裂解C5华东主流价格上涨200元/吨，石脑油成本支撑增强。",
                    source="longzhong.com",
                    published_date="2026-03-16",
                ),
                SearchResult(
                    title="Celebrity market rumor roundup",
                    url="https://example.com/noise",
                    snippet="General commentary without China petrochemical pricing.",
                    source="example.com",
                    published_date="2026-02-01",
                ),
            ]

        def search(self, query, max_results=8, region="wt-wt"):
            return []

    builder = ExternalNewsContextBuilder(web_search=FakeWebSearch())
    payload = builder.build_context(
        user_query="裂解C5 本周行情",
        user_context=SimpleNamespace(product="裂解C5"),
        graph_product_ids=["cracked_c5"],
        portfolio_mode=False,
        perception_snapshot={},
        language="zh",
        mode="auto",
    )

    results = payload["results"]
    assert len(results) == 1
    assert results[0].domain.endswith("longzhong.com")
    assert results[0].score >= 0.8
    assert "example.com/noise" not in payload["formatted_text"]


def test_external_news_context_builder_adds_risk_queries_for_cost_shock_questions():
    from backend.src.intelligence.external_news_context_builder import (
        ExternalNewsContextBuilder,
    )
    from backend.src.intelligence.web_search_service import SearchResult

    class FakeWebSearch:
        def __init__(self):
            self.news_queries = []

        def search_news(self, query, max_results=8, region="wt-wt", timelimit="w"):
            self.news_queries.append((query, region, timelimit))
            return [
                SearchResult(
                    title="亚洲石脑油受航运扰动上涨",
                    url=f"https://www.reuters.com/article/{len(self.news_queries)}",
                    snippet="红海航运扰动导致亚洲石脑油供应偏紧，成本端继续偏强。",
                    source="reuters.com",
                    published_date="2026-03-16",
                )
            ]

        def search(self, query, max_results=8, region="wt-wt"):
            return []

    builder = ExternalNewsContextBuilder(web_search=FakeWebSearch())
    payload = builder.build_context(
        user_query="What is the reversal risk behind cracked C5 cost shock this week?",
        user_context=SimpleNamespace(product="Cracked C5"),
        graph_product_ids=["cracked_c5"],
        portfolio_mode=False,
        perception_snapshot={
            "products": {
                "crude_oil": {"change_7d_pct": 11.5},
                "naphtha": {"change_7d_pct": 17.8},
                "cracked_c5": {"change_7d_pct": 14.2},
            }
        },
        language="en",
        mode="auto",
    )

    query_texts = [item[0] for item in builder.web_search.news_queries]
    assert any("霍尔木兹 红海 航运 亚洲 石脑油" in query for query in query_texts)
    assert any("成本推动 原因 石脑油 本周" in query for query in query_texts)
    assert "reuters.com" in payload["formatted_text"]


def test_node_web_search_forces_news_mode_for_reversal_risk_queries(monkeypatch):
    from backend.src.engine import decision_graph as graph_module

    class FakeBuilder:
        def __init__(self):
            self.last_mode = None

        def build_context(self, **kwargs):
            self.last_mode = kwargs.get("mode")
            return {
                "formatted_text": "WEB_SEARCH (QUERY-TIME MARKET INTELLIGENCE):\n- https://example.com/news\n"
            }

    fake_builder = FakeBuilder()
    monkeypatch.setattr(
        graph_module, "get_external_news_context_builder", lambda: fake_builder
    )

    result = graph_module._node_web_search(
        {
            "user_query": "Is this a temporary cost shock reversal risk?",
            "user_context": SimpleNamespace(product=""),
            "graph_product_ids": [],
            "portfolio_mode": False,
            "perception_snapshot": {},
        }
    )

    assert fake_builder.last_mode == "on"
    assert "https://example.com/news" in result["web_search_results"]


def test_guardrails_strip_intelligence_section_without_verified_external_evidence():
    from backend.src.engine.decision_graph import _apply_deterministic_guardrails
    from backend.src.prompts.task_prompt import TaskType

    markdown = """## 📊 01 · 市场现状
当前市场偏强。

## 🌍 02.5 · 情报事件（如有检测到）
- 未知上游冲击：原油与石脑油同步上涨。

## 📈 03 · 价格预测
短期震荡偏强。
"""

    guarded = _apply_deterministic_guardrails(
        markdown,
        {
            "user_query": "请基于中国区数据库数据，给出间戊二烯本周建议报价区间",
            "response_mode": "full_report",
            "parsed_intent": SimpleNamespace(
                task_type=TaskType.MARKET_ANALYSIS_DECISION
            ),
            "intelligence_context": {"events": []},
            "web_search_results": "",
            "tool_info": "",
            "rag_info": "",
            "erp_data": "",
            "perception_snapshot": {},
            "portfolio_mode": False,
            "explanation_mode": False,
        },
    )

    assert "情报事件" not in guarded
    assert "未知上游冲击" not in guarded
    assert "价格预测" in guarded


def test_guardrails_downgrade_unsupported_geopolitical_risk_alerts_without_references():
    from backend.src.engine.decision_graph import _apply_deterministic_guardrails
    from backend.src.prompts.task_prompt import TaskType

    markdown = """## 📊 01 · Market Overview
Market is firm.

## ⚠️ 05 · Risk Alerts
- **Risk 1**: Geopolitical shock reversal. Probability: Medium-High. Potential impact: Rapid decline in naphtha prices.
- **What the market may be missing**: The move may be amplified by a transient, high-reversal-risk event rather than pure fundamentals.

## ❓ 06 · Suggested Follow-ups
- Check inventory.
"""

    guarded = _apply_deterministic_guardrails(
        markdown,
        {
            "user_query": "give me cracked c5 pricing this week",
            "response_mode": "full_report",
            "parsed_intent": SimpleNamespace(
                task_type=TaskType.MARKET_ANALYSIS_DECISION
            ),
            "intelligence_context": {"events": []},
            "web_search_results": "",
            "tool_info": "",
            "rag_info": "",
            "erp_data": "",
            "perception_snapshot": {},
            "portfolio_mode": False,
            "explanation_mode": False,
        },
    )

    assert "Geopolitical shock reversal" not in guarded
    assert "upstream cost-spike reversal" in guarded
    assert "high-reversal-risk event" not in guarded
    assert "high-reversal-risk cost spike" in guarded


def test_diagnosis_geopolitical_driver_marks_fallback_as_unverified():
    service = DiagnosisService()

    driver = service._derive_geopolitical_driver(
        {
            "crude_oil": {"change_7d_pct": 26.7},
            "naphtha": {"change_7d_pct": 36.5},
        },
        intelligence_context={"events": []},
    )

    assert driver is not None
    assert driver["verified_event"] is False
    assert driver["event"].startswith("Unverified upstream move pattern")
    assert driver["event_type"] == "demand_signal"


def test_api_chat_uses_mysql_backed_intelligence_registry(monkeypatch):
    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient

    runtime_db = _require_mysql_backend()

    monkeypatch.setenv("USE_REAL_LLM", "true")
    monkeypatch.setenv("MARKET_PRICES_REFRESH_ON_STARTUP", "false")

    class StubLLM:
        def __init__(self):
            self.client = object()
            self.model = "stub-llm"
            self.is_ready = True

        def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
            if "TASK_CLASSIFICATION" in system_prompt:
                return {"task_type": "market_analysis_decision", "confidence": 1.0}
            if "ENTITY_EXTRACTION" in system_prompt:
                return {
                    "product": "Cracked C5",
                    "industry": "Petrochemical",
                    "period": "Current Week",
                    "region": "China",
                }
            return {}

        def generate_completion(self, system_prompt: str, user_prompt: str) -> str:
            lines = [line for line in user_prompt.splitlines() if line.strip()]
            return "\n".join(lines[:80])

    session_id = None
    user_id = f"mysql-int-test-{uuid.uuid4().hex[:8]}"
    event_id = f"evt_mysql_test_{uuid.uuid4().hex[:8]}"

    for module_name in ["backend.server"]:
        sys.modules.pop(module_name, None)

    import backend.src.nlu.llm_service as llm_module
    import backend.src.service.alert_service as alert_module
    import backend.src.intelligence.news_ingestion_agent as news_agent_module
    import backend.src.intelligence.intelligence_orchestrator as intel_module
    import backend.src.service.perception_service as perception_module
    import backend.src.service.diagnosis_service as diagnosis_module
    import backend.src.rag.vector_store as vector_store_module
    from backend.src.intelligence.event_registry import intelligence_event_registry
    from backend.src.intelligence.event_types import ExtractedEvent

    monkeypatch.setattr(llm_module, "RealLLMService", StubLLM)
    monkeypatch.setattr(alert_module.alert_worker, "start", lambda: None)
    monkeypatch.setattr(alert_module.alert_worker, "stop", lambda: None)
    monkeypatch.setattr(
        news_agent_module.NewsIngestionAgent, "start", lambda self: None
    )
    monkeypatch.setattr(news_agent_module.NewsIngestionAgent, "stop", lambda self: None)

    test_event = ExtractedEvent(
        event_type="geopolitical_supply_disruption",
        headline="Hormuz shipping disruption squeezes prompt C5 supply",
        affected_commodities=["crude_oil", "naphtha", "mixed_c5"],
        direction="bullish",
        magnitude_estimate="large",
        certainty="confirmed",
        time_horizon="short",
        geographic_scope="middle_east",
        relevance_to_c5_c9="direct",
        key_entities=["Hormuz", "Middle East"],
        causal_chain_hint="Hormuz disruption -> crude -> naphtha -> cracked_c5",
        source_url="https://example.com/hormuz-test",
        source_credibility="tier1",
        full_text="Test event inserted into MySQL registry for integration coverage.",
        raw_quote="Integration test event",
        event_id=event_id,
        detected_at=datetime.utcnow().isoformat(),
    )
    intelligence_event_registry.upsert_event(test_event)

    server = importlib.import_module("backend.server")
    server.market_data.refresh = lambda: None

    class FakeCausalEngine:
        def reason_through_event(self, event):
            class Chain:
                entry_commodity = "crude_oil"
                primary_impact_pct = 5.0
                naphtha_total_pct = 4.62
                product_impacts = {
                    "cracked_c5": {
                        "pct_impact": 4.0,
                        "cny_impact": 200,
                        "causal_path": "event -> crude_oil -> naphtha -> cracked_c5",
                    }
                }
                causal_narrative = (
                    f"{event.headline} lifts cracked C5 through feedstock pressure."
                )
                confidence_by_horizon = {"7d": 0.8}
                recommended_override_type = "crude_spike"

            return Chain()

    monkeypatch.setattr(intel_module, "_causal_engine", FakeCausalEngine())
    monkeypatch.setattr(intel_module, "_scenario_engine", None)
    monkeypatch.setattr(intel_module, "_override_engine", None)

    monkeypatch.setattr(
        perception_module.perception_service,
        "build_snapshot",
        lambda: {
            "reference_date": "2026-03-10",
            "products": {
                "cracked_c5": {
                    "id": "cracked_c5",
                    "name_en": "Cracked C5",
                    "name_cn": "Cracked C5",
                    "unit": "CNY/ton",
                    "latest_price": 5000.0,
                    "latest_date": "2026-03-10",
                    "change_7d_pct": 1.2,
                    "anomaly": {"is_anomaly": False},
                    "data_source": "real",
                },
                "naphtha": {
                    "id": "naphtha",
                    "name_en": "Naphtha",
                    "name_cn": "Naphtha",
                    "unit": "CNY/ton",
                    "latest_price": 4500.0,
                    "latest_date": "2026-03-10",
                    "change_7d_pct": 0.5,
                    "anomaly": {"is_anomaly": False},
                    "data_source": "real",
                },
            },
            "anomalies": [],
            "historical_matches": [],
            "alert_level": "WATCH",
            "market_phase": "normal",
            "crude_trend": "up",
            "naphtha_trend": "up",
        },
    )
    monkeypatch.setattr(
        diagnosis_module.diagnosis_service,
        "diagnose",
        lambda *args, **kwargs: {
            "market_regime": "tight_supply",
            "primary_driver": "geopolitical intelligence",
            "net_signal": "bullish",
            "evidence": ["MySQL-backed intelligence event"],
        },
    )
    monkeypatch.setattr(
        diagnosis_module.diagnosis_service,
        "to_prompt_text",
        lambda diag: "DIAGNOSIS (STAGE 2): tight_supply driven by MySQL-backed intelligence.",
    )

    monkeypatch.setattr(
        forecasting_module,
        "ensemble_forecast",
        lambda *args, **kwargs: {
            "base_pct": 2.0,
            "vol_pct": 1.0,
            "latest": 5000.0,
            "arima_ok": True,
            "xgb_ok": True,
            "lstm_ok": True,
        },
    )
    monkeypatch.setattr(
        forecasting_module,
        "monthly_stl_forecast",
        lambda *args, **kwargs: {
            "base_pct": 3.0,
            "vol_pct": 2.0,
            "seasonal_pct": 0.0,
            "layers_used": ["stl", "causal"],
            "stl_pct": 1.0,
            "causal_pct": 1.0,
        },
    )
    monkeypatch.setattr(pricing_module, "_compute_macro_adj", lambda *args: (0, ""))
    monkeypatch.setattr(
        pricing_module, "_compute_downstream_adj", lambda *args: (0, "")
    )
    monkeypatch.setattr(
        pricing_module,
        "_compute_inventory_pressure",
        lambda *args: (0, "inventory neutral", 2.0),
    )
    monkeypatch.setattr(
        pricing_module,
        "_compute_action_bias",
        lambda *args, **kwargs: ("neutral", 0, ""),
    )
    monkeypatch.setattr(pricing_module, "get_active_override", lambda: None)

    vector_store_module.kb.query = lambda *args, **kwargs: []
    vector_store_module.kb.query_weekly = lambda *args, **kwargs: []
    vector_store_module.kb.query_past_decisions = lambda *args, **kwargs: []
    vector_store_module.kb.query_news_events = lambda *args, **kwargs: []
    vector_store_module.kb.store_decision = lambda *args, **kwargs: None

    try:
        with TestClient(server.app) as client:
            response = client.post(
                "/api/chat",
                json={
                    "message": "Analyze the current cracked C5 market situation and recommend the pricing action"
                },
                headers={"X-User-Id": user_id},
            )

        assert response.status_code == 200
        payload = response.json()
        session_id = payload["session_id"]
        assert payload["context"]["product"] == "Cracked C5"

        assembled_context = payload["context"]["additional_info"]
        assert test_event.headline in assembled_context
        assert "DETECTED 1 RECENT MARKET EVENT(S):" in assembled_context
        assert "Live intelligence adj: bullish +4.00%" in assembled_context
        assert "Live Intelligence: bullish +4.00% (+200 CNY/ton)" in assembled_context

        with runtime_db.engine.connect() as conn:
            stored_event = conn.execute(
                text(
                    "SELECT headline FROM intelligence_events WHERE event_id = :event_id"
                ),
                {"event_id": event_id},
            ).first()
            stored_session = conn.execute(
                text("SELECT user_id FROM sessions WHERE id = :session_id"),
                {"session_id": session_id},
            ).first()
            message_count = conn.execute(
                text("SELECT COUNT(*) FROM messages WHERE session_id = :session_id"),
                {"session_id": session_id},
            ).scalar_one()

        assert stored_event is not None
        assert stored_event[0] == test_event.headline
        assert stored_session is not None
        assert stored_session[0] == user_id
        assert message_count >= 2
    finally:
        with runtime_db.engine.begin() as conn:
            if session_id:
                conn.execute(
                    text("DELETE FROM messages WHERE session_id = :session_id"),
                    {"session_id": session_id},
                )
                conn.execute(
                    text("DELETE FROM sessions WHERE id = :session_id"),
                    {"session_id": session_id},
                )
            conn.execute(
                text("DELETE FROM intelligence_events WHERE event_id = :event_id"),
                {"event_id": event_id},
            )


def test_api_events_latest_reads_from_mysql_backed_registry(monkeypatch):
    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient

    runtime_db = _require_mysql_backend()

    monkeypatch.setenv("USE_REAL_LLM", "true")
    monkeypatch.setenv("MARKET_PRICES_REFRESH_ON_STARTUP", "false")

    class StubLLM:
        def __init__(self):
            self.client = object()
            self.model = "stub-llm"
            self.is_ready = True

        def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
            return {}

        def generate_completion(self, system_prompt: str, user_prompt: str) -> str:
            return user_prompt

    event_id = f"evt_mysql_latest_{uuid.uuid4().hex[:8]}"

    for module_name in ["backend.server"]:
        sys.modules.pop(module_name, None)

    import backend.src.nlu.llm_service as llm_module
    import backend.src.service.alert_service as alert_module
    import backend.src.intelligence.news_ingestion_agent as news_agent_module
    from backend.src.intelligence.event_registry import intelligence_event_registry
    from backend.src.intelligence.event_types import ExtractedEvent

    monkeypatch.setattr(llm_module, "RealLLMService", StubLLM)
    monkeypatch.setattr(alert_module.alert_worker, "start", lambda: None)
    monkeypatch.setattr(alert_module.alert_worker, "stop", lambda: None)
    monkeypatch.setattr(
        news_agent_module.NewsIngestionAgent, "start", lambda self: None
    )
    monkeypatch.setattr(news_agent_module.NewsIngestionAgent, "stop", lambda self: None)

    test_event = ExtractedEvent(
        event_type="shipping_disruption",
        headline="Integration test latest-event readback from MySQL registry",
        affected_commodities=["crude_oil", "naphtha", "mixed_c5"],
        direction="bullish",
        magnitude_estimate="medium",
        certainty="confirmed",
        time_horizon="short",
        geographic_scope="asia_pacific",
        relevance_to_c5_c9="direct",
        key_entities=["integration-test"],
        causal_chain_hint="shipping disruption -> naphtha -> cracked_c5",
        source_url="https://example.com/mysql-latest-test",
        source_credibility="tier1",
        full_text="MySQL latest events API integration test record.",
        raw_quote="latest events test",
        event_id=event_id,
        detected_at=datetime.utcnow().isoformat(),
    )
    intelligence_event_registry.upsert_event(test_event)

    try:
        server = importlib.import_module("backend.server")
        server.market_data.refresh = lambda: None

        with TestClient(server.app) as client:
            response = client.get("/api/events/latest", params={"hours": 4})

        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] >= 1

        matching = [
            event for event in payload["events"] if event.get("event_id") == event_id
        ]
        assert matching
        assert matching[0]["headline"] == test_event.headline
        assert matching[0]["direction"] == "bullish"
        assert matching[0]["relevance_to_c5_c9"] == "direct"

        with runtime_db.engine.connect() as conn:
            stored_event = conn.execute(
                text(
                    "SELECT headline FROM intelligence_events WHERE event_id = :event_id"
                ),
                {"event_id": event_id},
            ).first()

        assert stored_event is not None
        assert stored_event[0] == test_event.headline
    finally:
        with runtime_db.engine.begin() as conn:
            conn.execute(
                text("DELETE FROM intelligence_events WHERE event_id = :event_id"),
                {"event_id": event_id},
            )


def test_api_chat_surfaces_mysql_backed_erp_kpi_reasoning(monkeypatch):
    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient

    runtime_db = _require_mysql_backend()

    monkeypatch.setenv("USE_REAL_LLM", "true")
    monkeypatch.setenv("MARKET_PRICES_REFRESH_ON_STARTUP", "false")

    class StubLLM:
        def __init__(self):
            self.client = object()
            self.model = "stub-llm"
            self.is_ready = True

        def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
            if "TASK_CLASSIFICATION" in system_prompt:
                return {"task_type": "market_analysis_decision", "confidence": 1.0}
            if "ENTITY_EXTRACTION" in system_prompt:
                return {
                    "product": "Cracked C5",
                    "industry": "Petrochemical",
                    "period": "Current Week",
                    "region": "China",
                }
            return {}

        def generate_completion(self, system_prompt: str, user_prompt: str) -> str:
            lines = [line for line in user_prompt.splitlines() if line.strip()]
            return "\n".join(lines[:80])

    session_id = None
    user_id = f"mysql-kpi-test-{uuid.uuid4().hex[:8]}"
    bill_no = f"ERP-KPI-{uuid.uuid4().hex[:10]}"
    stock_org = f"erp_kpi_stock_{uuid.uuid4().hex[:8]}"
    sales_org = f"erp_kpi_sales_{uuid.uuid4().hex[:8]}"

    for module_name in ["backend.server"]:
        sys.modules.pop(module_name, None)

    import backend.src.nlu.llm_service as llm_module
    import backend.src.service.alert_service as alert_module
    import backend.src.intelligence.news_ingestion_agent as news_agent_module
    import backend.src.service.perception_service as perception_module
    import backend.src.rag.vector_store as vector_store_module

    monkeypatch.setattr(llm_module, "RealLLMService", StubLLM)
    monkeypatch.setattr(alert_module.alert_worker, "start", lambda: None)
    monkeypatch.setattr(alert_module.alert_worker, "stop", lambda: None)
    monkeypatch.setattr(
        news_agent_module.NewsIngestionAgent, "start", lambda self: None
    )
    monkeypatch.setattr(news_agent_module.NewsIngestionAgent, "stop", lambda self: None)

    with runtime_db.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO erp_v_sale_orderentry "
                "(org_name, material_name, FDELIQTY, PLANDATE, FBILLNO, produce_qty, plan_qty) "
                "VALUES (:org_name, :material_name, :delivered_qty, :plan_date, :bill_no, :produce_qty, :plan_qty)"
            ),
            {
                "org_name": sales_org,
                "material_name": "工业用裂解碳五",
                "delivered_qty": 900.0,
                "plan_date": datetime.utcnow().strftime("%Y-%m-%d 00:00:00"),
                "bill_no": bill_no,
                "produce_qty": 880.0,
                "plan_qty": 1000.0,
            },
        )
        conn.execute(
            text(
                "INSERT INTO erp_v_stk_inventory "
                "(FOWNORGNAME, STOCKORGNAME, material_name, FBASEQTY) "
                "VALUES (:own_org, :stock_org, :material_name, :base_qty)"
            ),
            {
                "own_org": stock_org,
                "stock_org": stock_org,
                "material_name": "工业用裂解碳五",
                "base_qty": 180.0,
            },
        )

    try:
        server = importlib.import_module("backend.server")
        server.market_data.refresh = lambda: None

        monkeypatch.setattr(
            perception_module.perception_service,
            "build_snapshot",
            lambda: {
                "reference_date": "2026-03-10",
                "products": {
                    "cracked_c5": {
                        "id": "cracked_c5",
                        "name_en": "Cracked C5",
                        "name_cn": "Cracked C5",
                        "unit": "CNY/ton",
                        "latest_price": 5000.0,
                        "latest_date": "2026-03-10",
                        "change_7d_pct": 1.2,
                        "anomaly": {"is_anomaly": False},
                        "data_source": "real",
                    },
                    "naphtha": {
                        "id": "naphtha",
                        "name_en": "Naphtha",
                        "name_cn": "Naphtha",
                        "unit": "CNY/ton",
                        "latest_price": 4500.0,
                        "latest_date": "2026-03-10",
                        "change_7d_pct": 0.1,
                        "anomaly": {"is_anomaly": False},
                        "data_source": "real",
                    },
                },
                "anomalies": [],
                "historical_matches": [],
                "alert_level": "WATCH",
                "market_phase": "normal",
                "crude_trend": "flat",
                "naphtha_trend": "flat",
            },
        )

        monkeypatch.setattr(
            forecasting_module,
            "ensemble_forecast",
            lambda *args, **kwargs: {
                "base_pct": 1.0,
                "vol_pct": 1.0,
                "latest": 5000.0,
                "arima_ok": True,
                "xgb_ok": True,
                "lstm_ok": True,
            },
        )
        monkeypatch.setattr(
            forecasting_module,
            "monthly_stl_forecast",
            lambda *args, **kwargs: {
                "base_pct": 1.5,
                "vol_pct": 2.0,
                "seasonal_pct": 0.0,
                "layers_used": ["stl", "causal"],
                "stl_pct": 0.5,
                "causal_pct": 0.5,
            },
        )
        monkeypatch.setattr(pricing_module, "_compute_macro_adj", lambda *args: (0, ""))
        monkeypatch.setattr(
            pricing_module, "_compute_downstream_adj", lambda *args: (0, "")
        )
        monkeypatch.setattr(
            pricing_module,
            "_compute_inventory_pressure",
            lambda *args: (0, "inventory neutral", 2.0),
        )
        monkeypatch.setattr(
            pricing_module,
            "_compute_action_bias",
            lambda *args, **kwargs: ("neutral", 0, ""),
        )
        monkeypatch.setattr(pricing_module, "get_active_override", lambda: None)

        vector_store_module.kb.query = lambda *args, **kwargs: []
        vector_store_module.kb.query_weekly = lambda *args, **kwargs: []
        vector_store_module.kb.query_past_decisions = lambda *args, **kwargs: []
        vector_store_module.kb.query_news_events = lambda *args, **kwargs: []
        vector_store_module.kb.store_decision = lambda *args, **kwargs: None

        with TestClient(server.app) as client:
            response = client.post(
                "/api/chat",
                json={
                    "message": "Analyze the current cracked C5 market situation and explain the price forecast"
                },
                headers={"X-User-Id": user_id},
            )

        assert response.status_code == 200
        payload = response.json()
        session_id = payload["session_id"]
        assert payload["context"]["product"] == "Cracked C5"

        assembled_context = payload["context"]["additional_info"]
        assert "DIAGNOSIS (STAGE 2)" in assembled_context
        assert "ERP KPI       : BULLISH +0.95%" in assembled_context
        assert "tight inventory coverage, high operating rate" in assembled_context

        with runtime_db.engine.connect() as conn:
            stored_session = conn.execute(
                text("SELECT user_id FROM sessions WHERE id = :session_id"),
                {"session_id": session_id},
            ).first()
            message_count = conn.execute(
                text("SELECT COUNT(*) FROM messages WHERE session_id = :session_id"),
                {"session_id": session_id},
            ).scalar_one()

        assert stored_session is not None
        assert stored_session[0] == user_id
        assert message_count >= 2
    finally:
        with runtime_db.engine.begin() as conn:
            if session_id:
                conn.execute(
                    text("DELETE FROM messages WHERE session_id = :session_id"),
                    {"session_id": session_id},
                )
                conn.execute(
                    text("DELETE FROM sessions WHERE id = :session_id"),
                    {"session_id": session_id},
                )
            conn.execute(
                text("DELETE FROM erp_v_sale_orderentry WHERE FBILLNO = :bill_no"),
                {"bill_no": bill_no},
            )
            conn.execute(
                text(
                    "DELETE FROM erp_v_stk_inventory "
                    "WHERE FOWNORGNAME = :stock_org AND STOCKORGNAME = :stock_org "
                    "AND material_name = :material_name AND FBASEQTY = :base_qty"
                ),
                {
                    "stock_org": stock_org,
                    "material_name": "工业用裂解碳五",
                    "base_qty": 180.0,
                },
            )


def test_build_composer_suggestions_uses_role_alerts_and_recent_sessions(
    monkeypatch, tmp_path
):
    sqlite_path = tmp_path / "test_composer_suggestions.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{sqlite_path}")
    monkeypatch.setenv("USE_REAL_LLM", "true")
    monkeypatch.setenv("MARKET_PRICES_REFRESH_ON_STARTUP", "false")
    monkeypatch.setenv("DISABLE_RAG", "true")

    class StubLLM:
        def __init__(self):
            self.client = object()
            self.model = "stub-llm"
            self.is_ready = True

        def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
            return {}

        def generate_completion(self, system_prompt: str, user_prompt: str) -> str:
            return user_prompt

    for module_name in ["backend.server"]:
        sys.modules.pop(module_name, None)

    import backend.src.nlu.llm_service as llm_module

    monkeypatch.setattr(llm_module, "RealLLMService", StubLLM)

    server = importlib.import_module("backend.server")

    monkeypatch.setattr(
        server.alert_worker,
        "get_alerts",
        lambda: {
            "alerts": [
                {
                    "id": "alert-1",
                    "type": "price_spike",
                    "product": "cracked_c5",
                    "product_name": "Cracked C5",
                    "severity": "high",
                    "change_7d_pct": 4.8,
                    "price": 5230,
                    "message": "Cracked C5 moved sharply this week.",
                }
            ]
        },
    )
    monkeypatch.setattr(
        server.db,
        "get_sessions",
        lambda user_id: [
            {
                "id": "session-1",
                "user_id": user_id,
                "title": "C5 weekly pricing review",
                "created_at": "2026-04-01T09:00:00",
            }
        ],
    )
    monkeypatch.setattr(
        server.db,
        "get_messages",
        lambda session_id: [
            {
                "id": "m-1",
                "session_id": session_id,
                "role": "user",
                "content": "What changed in cracked C5 this week and what price action should we take?",
                "timestamp": "2026-04-01T09:00:00",
            }
        ],
    )

    payload = server._build_composer_suggestions(
        {
            "id": "user-1",
            "username": "Gao",
            "is_admin": False,
            "department_name": "Market Intelligence",
            "role_name": "Market Analyst",
        },
        "en",
        limit=8,
    )

    assert payload["role"] == "risk"
    assert payload["counts"]["alerts"] == 1
    assert payload["counts"]["recent_sessions"] == 1
    sources = {item["source"] for item in payload["suggestions"]}
    assert "role" in sources
    assert "alert" in sources
    assert "recent_session" in sources
    session_suggestion = next(
        item for item in payload["suggestions"] if item["source"] == "recent_session"
    )
    assert session_suggestion["session_id"] == "session-1"
    assert "current alerts" in session_suggestion["prompt"].lower()


def test_database_only_mode_disables_intelligence_context(monkeypatch):
    monkeypatch.setenv("DATABASE_ONLY_MODE", "true")

    import backend.src.intelligence.intelligence_orchestrator as intel_module
    from backend.src.intelligence.event_registry import intelligence_event_registry

    monkeypatch.setattr(
        intelligence_event_registry,
        "list_recent_events",
        lambda **kwargs: [],
    )

    payload = intel_module.get_intelligence_context(hours=4)

    assert payload["has_intelligence"] is False
    assert payload["events"] == []
    assert "DATABASE_ONLY_MODE active" in payload["intelligence_summary"]
    assert "persisted intelligence registry only" in payload["intelligence_summary"]


def test_database_only_mode_uses_persisted_registry_events(monkeypatch):
    monkeypatch.setenv("DATABASE_ONLY_MODE", "true")

    import backend.src.intelligence.intelligence_orchestrator as intel_module
    from backend.src.intelligence.event_types import ExtractedEvent
    from backend.src.intelligence.event_registry import intelligence_event_registry

    test_event = ExtractedEvent(
        event_type="plant_outage",
        headline="Database event: cracker outage tightens prompt C5 supply",
        affected_commodities=["cracked_c5"],
        direction="bullish",
        magnitude_estimate="medium",
        certainty="confirmed",
        time_horizon="short",
        geographic_scope="china",
        relevance_to_c5_c9="direct",
        full_text="Persisted intelligence event from database.",
    )

    monkeypatch.setattr(
        intelligence_event_registry,
        "list_recent_events",
        lambda **kwargs: [test_event],
    )
    monkeypatch.setattr(intel_module, "_news_agent", None)

    payload = intel_module.get_intelligence_context(hours=4)

    assert payload["has_intelligence"] is True
    assert len(payload["events"]) == 1
    assert payload["events"][0]["headline"] == test_event.headline
    assert "persisted intelligence registry only" in payload["intelligence_summary"]


def test_database_only_mode_builds_causal_analyses_from_persisted_events(monkeypatch):
    monkeypatch.setenv("DATABASE_ONLY_MODE", "true")
    monkeypatch.delenv("INTELLIGENCE_RETRIEVAL_REVIEW_STATUS", raising=False)

    import backend.src.intelligence.intelligence_orchestrator as intel_module
    from backend.src.intelligence.event_types import ExtractedEvent
    from backend.src.intelligence.event_registry import intelligence_event_registry

    test_event = ExtractedEvent(
        event_type="plant_outage",
        headline="Database event: cracker outage tightens prompt C5 supply",
        affected_commodities=["cracked_c5"],
        direction="bullish",
        magnitude_estimate="medium",
        certainty="confirmed",
        time_horizon="short",
        geographic_scope="china",
        relevance_to_c5_c9="direct",
        full_text="Persisted intelligence event from database.",
        review_status="pending",
        source_url="https://example.com/cracker-outage",
    )

    class StubChain:
        entry_commodity = "mixed_c5"
        primary_impact_pct = 4.0
        naphtha_total_pct = 4.0
        product_impacts = {
            "mixed_c5": {
                "pct_impact": 3.2,
                "cny_impact": 160,
                "causal_path": "event -> mixed_c5",
            }
        }
        causal_narrative = "Prompt C5 outage tightens spot availability."
        confidence_by_horizon = {"7d": 0.75}
        recommended_override_type = "plant_outage"

    class StubCausalEngine:
        def reason_through_event(self, event):
            assert event.event_id == test_event.event_id
            return StubChain()

    monkeypatch.setattr(
        intelligence_event_registry,
        "list_recent_events",
        lambda **kwargs: [test_event],
    )
    monkeypatch.setattr(intel_module, "_news_agent", None)
    monkeypatch.setattr(intel_module, "_causal_engine", StubCausalEngine())
    monkeypatch.setattr(intel_module, "_scenario_engine", None)
    monkeypatch.setattr(intel_module, "_override_engine", None)

    payload = intel_module.get_intelligence_context(hours=4)

    assert payload["has_intelligence"] is True
    assert payload["events"][0]["review_status"] == "pending"
    assert len(payload["causal_analyses"]) == 1
    assert payload["causal_analyses"][0]["event_id"] == test_event.event_id
    assert payload["causal_analyses"][0]["source_url"] == "https://example.com/cracker-outage"
    assert "persisted intelligence registry only" in payload["intelligence_summary"]


def test_database_only_mode_can_still_initialize_news_agent_when_enabled(monkeypatch):
    monkeypatch.setenv("DATABASE_ONLY_MODE", "true")
    monkeypatch.setenv("ENABLE_NEWS_INGESTION_AGENT", "true")

    import backend.src.intelligence.intelligence_orchestrator as intel_module

    assert intel_module.should_initialize_news_ingestion_agent() is True


def test_server_can_start_news_agent_in_database_only_mode_when_enabled(monkeypatch):
    monkeypatch.setenv("DATABASE_ONLY_MODE", "true")
    monkeypatch.setenv("ENABLE_NEWS_INGESTION_AGENT", "true")

    import backend.server as server

    class StubAgent:
        pass

    monkeypatch.setattr(server, "_intel_news_agent", StubAgent())
    monkeypatch.setattr(server, "LLM_MODE", "real")
    monkeypatch.setattr(server, "_background_workers_enabled", lambda: True)

    assert server._should_start_news_ingestion_agent() is True


def test_database_only_mode_disables_web_search_node(monkeypatch):
    monkeypatch.setenv("DATABASE_ONLY_MODE", "true")

    from backend.src.engine.decision_graph import _node_web_search

    payload = _node_web_search(
        {
            "user_query": "What changed in the market this week?",
            "perception_snapshot": {},
            "graph_product_ids": [],
            "portfolio_mode": False,
            "user_context": None,
        }
    )

    assert payload == {
        "web_search_results": "",
        "web_search_meta": {"results": [], "queries": []},
    }


def test_regional_prices_use_fresh_lz_rows_for_external_products(monkeypatch):
    import backend.server as server

    monkeypatch.setattr(
        server.market_data,
        "get_regional_price_rows",
        lambda product_id: [
            {
                "price_type": "average",
                "price": 7200.0,
                "date": "2026-04-21",
                "prev_price": 7700.0,
            },
            {
                "price_type": "yangzi",
                "price": 7200.0,
                "date": "2026-04-21",
                "prev_price": 7700.0,
            },
        ]
        if product_id == "cracked_c5"
        else [],
    )

    payload = server._compute_regional_prices("cracked_c5", fast_mode=False)

    assert payload["updated_at"] == "2026-04-21"
    assert [region["date"] for region in payload["regions"]] == [
        "2026-04-21",
        "2026-04-21",
    ]
    assert {region["price_type"] for region in payload["regions"]} == {
        "average",
        "yangzi",
    }


def test_regional_prices_fast_mode_keeps_lz_daily_change(monkeypatch):
    import backend.server as server

    monkeypatch.setattr(
        server.market_data,
        "get_regional_price_rows",
        lambda product_id: [
            {
                "price_type": "average",
                "price": 7200.0,
                "date": "2026-05-13",
                "prev_price": 7000.0,
            }
        ]
        if product_id == "cracked_c5"
        else [],
    )

    payload = server._compute_regional_prices("cracked_c5", fast_mode=True)

    assert payload["regions"][0]["date"] == "2026-05-13"
    assert payload["regions"][0]["change_pct"] == 2.86


def test_market_data_service_prefers_live_erp_history_for_c5_resin(monkeypatch):
    import backend.src.service.market_data_service as market_data_service

    service = market_data_service.MarketDataService.__new__(market_data_service.MarketDataService)
    service.history = {}
    service._real_data_sources = set()
    service._lz_latest_regional_prices = {}
    service._lz_latest_regional_rows = {}
    service._data_source_detail = {}

    monkeypatch.setattr(
        service,
        "_load_history_from_erp_sale_orders",
        lambda product_id, material_name: [{"date": "2026-04-22", "price": 16300.0}]
        if product_id == "c5_resin" and material_name == "碳五石油树脂"
        else [],
    )
    monkeypatch.setattr(
        service,
        "_load_history_from_market_prices",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("stale market_prices fallback should not run")),
    )

    history = market_data_service.MarketDataService.get_price_history(service, "c5_resin")

    assert history == [{"date": "2026-04-22", "price": 16300.0}]


def test_market_data_service_marks_sis_as_lz_backed():
    import backend.src.service.market_data_service as market_data_service

    assert "sis" in market_data_service.LZ_EXTERNAL_NODE_IDS


def test_db_only_ops_query_uses_fast_forecast_path(monkeypatch):
    import backend.src.engine.decision_graph as decision_graph
    from backend.src.nlu.types import TaskType as NluTaskType

    monkeypatch.setenv("DATABASE_ONLY_MODE", "true")

    monkeypatch.setattr(
        decision_graph.forecasting_service,
        "forecast",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("heavy forecast should not run")),
    )

    state = {
        "user_query": "How do China ethylene/C5 plant shutdown and restart changes affect isoprene and DCPD inventory and pricing? Please give operational recommendations.",
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.MARKET_ANALYSIS_DECISION),
        "graph_product_id": "isoprene",
        "perception_snapshot": {
            "products": {
                "isoprene": {
                    "latest_price": 18000.0,
                    "change_7d_pct": 4.2,
                    "name_cn": "异戊二烯",
                    "name_en": "Isoprene",
                    "unit": "CNY/t",
                }
            }
        },
        "diagnosis": {"market_regime": "bullish"},
        "intelligence_context": {"events": []},
    }

    payload = decision_graph._node_forecast(state)

    assert payload["forecast"]["model_version"] == "fast-db-only-ops-v1"
    assert payload["forecast"]["scenarios"]["7d"]["base"]["high"] > 0
    assert "FORECAST (STAGE 3)" in payload["forecast_text"]


def test_db_only_ops_query_short_circuits_to_deterministic_report(monkeypatch):
    import backend.src.engine.decision_graph as decision_graph
    from backend.src.nlu.types import TaskType as NluTaskType, UserContext

    monkeypatch.setenv("DATABASE_ONLY_MODE", "true")

    state = {
        "user_query": "How do China ethylene/C5 plant shutdown and restart changes affect isoprene and DCPD inventory and pricing? Please give operational recommendations.",
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.MARKET_ANALYSIS_DECISION),
        "user_context": UserContext(product="Isoprene, DCPD", period="Current Week"),
        "user_prefs": {"language": "en"},
        "resolved_chat_mode": "analysis",
        "mode_reason": "test",
        "graph_product_id": "isoprene",
        "diagnosis": {
            "market_regime": "bullish",
            "root_cause": "China cracker restart delays keep prompt C5 supply tight.",
            "chain_impact": ["Prompt C5 availability stays constrained for isoprene and DCPD chains."],
            "risks": ["Restart timing may slip by another week."],
        },
        "forecast": {
            "scenarios": {
                "3d": {"base": {"low": 17800, "high": 18200}, "bull": {"low": 18000, "high": 18400}, "bear": {"low": 17600, "high": 18000}},
                "7d": {"base": {"low": 17900, "high": 18400}, "bull": {"low": 18100, "high": 18600}, "bear": {"low": 17600, "high": 18100}},
                "30d": {"base": {"low": 17500, "high": 18500}, "bull": {"low": 17800, "high": 18800}, "bear": {"low": 17200, "high": 18200}},
            }
        },
        "pricing_decision": {
            "recommended_price": 18100,
            "price_range": [17900, 18300],
            "triggers": [{"condition": "restart confirmation slips", "action": "keep quotes firm"}],
        },
        "tool_info": "INTERNAL DATABASE LIVE DATA:\n异戊二烯 (Isoprene): 18,000 元/吨 (7d:+4.20%) [src=market_prices|product_id=isoprene|price_type=east_china|date=2026-04-20]\ninventory_days=12\n双环戊二烯 (DCPD): 9,200 元/吨 (7d:+2.10%) [src=market_prices|product_id=dcpd|price_type=east_china|date=2026-04-20]\ninventory_days=21",
    }

    payload = decision_graph._node_short_circuit_check(state)

    assert payload["report"].status == "success"
    assert "## 04. Recommended Actions" in payload["report"].raw_markdown
    assert "Isoprene" in payload["report"].raw_markdown


def test_external_evidence_policy_blocks_hypothetical_driver_generation_without_events():
    from backend.src.engine.decision_graph import _build_external_evidence_policy

    policy = _build_external_evidence_policy(
        {
            "intelligence_context": {"events": []},
            "web_search_results": "",
        },
        "en",
    )

    assert policy["has_verified_external_evidence"] is False
    assert policy["verified_event_count"] == 0
    assert "Do not propose probable drivers" in policy["guidance"]


def test_external_evidence_policy_allows_evidence_grounded_causal_use_with_events():
    from backend.src.engine.decision_graph import _build_external_evidence_policy

    policy = _build_external_evidence_policy(
        {
            "intelligence_context": {
                "events": [{"headline": "Approved naphtha outage event"}]
            },
            "web_search_results": "",
        },
        "en",
    )

    assert policy["has_verified_external_evidence"] is True
    assert policy["verified_event_count"] == 1
    assert "must cite the corresponding event headline" in policy["guidance"]


def test_boss_briefing_uses_external_mixed_synthesis_without_raw_erp(monkeypatch):
    import backend.src.engine.decision_graph as decision_graph
    from backend.src.nlu.types import TaskType as NluTaskType

    captured = {}

    class FakeLLM:
        def generate_json(self, system_prompt, user_prompt, route_hint=None, sanitized=None):
            captured["router_route_hint"] = route_hint
            captured["router_sanitized"] = sanitized
            return {
                "need_erp": True,
                "need_public": True,
                "products": ["naphtha"],
                "time_window": "Current Week",
                "branch_tasks": {
                    "erp_local": True,
                    "lz_market": True,
                    "news_events": True,
                },
                "rationale": "test",
            }

        def generate_completion(self, system_prompt, user_prompt, route_hint=None, sanitized=None, **kwargs):
            captured["final_route_hint"] = route_hint
            captured["final_sanitized"] = sanitized
            captured["final_user_prompt"] = user_prompt
            return "## Weekly Executive Report\n\nTest output"

    monkeypatch.setattr(
        decision_graph,
        "extract_product_ids_in_text",
        lambda text: ["naphtha"],
    )

    class FakeKPI:
        sales_30d_ton = 321.0
        inventory_tons = 123.0
        inventory_days = 9.5
        utilization_pct = 74.0

    class FakeErpKPIService:
        def get_product_kpi(self, pid):
            return FakeKPI()

        def get_sales_growth_pct(self, pid):
            return -4.2

    import types as _types
    import sys as _sys

    fake_erp_module = _types.ModuleType("backend.src.service.erp_kpi_service")
    fake_erp_module.ErpKPIService = FakeErpKPIService
    monkeypatch.setitem(_sys.modules, "backend.src.service.erp_kpi_service", fake_erp_module)

    node = decision_graph._make_generate_report(FakeLLM())
    state = {
        "user_query": "Generate this week's China market executive report with pricing shifts.",
        "user_context": SimpleNamespace(product=None, period="Current Week"),
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.BOSS_BRIEFING),
        "system_prompt": "local system prompt",
        "user_prompt": "RAW ERP PROMPT SECRET_SALES_ROWS",
        "user_prefs": {"kimi_mixed_synthesis": True},
        "response_mode": "full_report",
        "tool_info": "INTERNAL DATABASE LIVE DATA:\nNaphtha: 1074 [2026-04-14]",
        "erp_data": "LIVE MARKET DATA (MYSQL): SECRET_SALES_ROWS customer=ABC contract=XYZ",
        "forecast_text": "forecast summary",
        "pricing_text": "pricing summary",
        "intelligence_text": "",
        "diagnosis_text": "diagnosis summary",
        "graph_product_ids": ["naphtha"],
        "intelligence_context": {"events": []},
        "web_search_results": "",
        "web_search_meta": {"results": [], "queries": []},
        "perception_snapshot": {"reference_date": "2026-04-14"},
        "conversation_history": [],
        "resolved_chat_mode": "report",
        "mode_reason": "test",
    }

    node(state)

    assert captured["router_route_hint"] == "external"
    assert captured["router_sanitized"] is True
    assert captured["final_route_hint"] == "external"
    assert captured["final_sanitized"] is True
    assert "Unified external payload JSON" in captured["final_user_prompt"]
    assert "SECRET_SALES_ROWS" not in captured["final_user_prompt"]
    assert "combined_insight_bundle_v1" in captured["final_user_prompt"]


def test_boss_briefing_external_compact_fallback_avoids_raw_erp(monkeypatch):
    import backend.src.engine.decision_graph as decision_graph
    from backend.src.nlu.types import TaskType as NluTaskType

    captured = {}

    class FakeLLM:
        def generate_completion(self, system_prompt, user_prompt, route_hint=None, sanitized=None, **kwargs):
            captured["route_hint"] = route_hint
            captured["sanitized"] = sanitized
            captured["user_prompt"] = user_prompt
            return "## Weekly Executive Report\n\nCompact output"

    monkeypatch.setattr(
        decision_graph.insight_snapshot_service,
        "get_combined_bundle_cached",
        lambda product_ids=None, market_max_age_seconds=None, erp_max_age_seconds=None, async_refresh_on_miss=True: {
            "ok": True,
            "market": {"snapshot_id": "m1", "generated_at": "2026-04-17T08:00:00", "age_seconds": 120, "stale": False, "count": 1},
            "erp": {"snapshot_id": "e1", "generated_at": "2026-04-17T07:55:00", "age_seconds": 420, "stale": False, "count": 1},
            "by_product": {
                "naphtha": {
                    "product_id": "naphtha",
                    "market": {"latest_price": 1074, "weekly_change_pct": -7.45, "unit": "RMB/ton", "as_of_date": "2026-04-14", "narrative": "Naphtha softened this week."},
                    "erp": {"sales_30d_ton": 111.0, "inventory_days": 8.0, "risk_level": "low", "narrative": "Inventory remains tight."},
                }
            },
            "refresh_enqueued": {"market": False, "erp": False},
        },
    )

    monkeypatch.setattr(
        decision_graph,
        "extract_product_ids_in_text",
        lambda text: ["naphtha"],
    )

    class FakeKPI:
        sales_30d_ton = 111.0
        inventory_tons = 55.0
        inventory_days = 8.0
        utilization_pct = 70.0

    class FakeErpKPIService:
        def get_product_kpi(self, pid):
            return FakeKPI()

        def get_sales_growth_pct(self, pid):
            return 2.5

    import types as _types
    import sys as _sys

    fake_erp_module = _types.ModuleType("backend.src.service.erp_kpi_service")
    fake_erp_module.ErpKPIService = FakeErpKPIService
    monkeypatch.setitem(_sys.modules, "backend.src.service.erp_kpi_service", fake_erp_module)

    node = decision_graph._make_generate_report(FakeLLM())
    state = {
        "user_query": "Generate this week's China market executive report with pricing shifts.",
        "user_context": SimpleNamespace(product=None, period="Current Week"),
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.BOSS_BRIEFING),
        "system_prompt": "local system prompt",
        "user_prompt": "RAW ERP PROMPT SECRET_SALES_ROWS",
        "user_prefs": {
            "kimi_mixed_synthesis": False,
            "final_decision_llm": "external",
        },
        "response_mode": "full_report",
        "tool_info": "INTERNAL DATABASE LIVE DATA:\nNaphtha: 1074 [2026-04-14]",
        "erp_data": "LIVE MARKET DATA (MYSQL): SECRET_SALES_ROWS customer=ABC contract=XYZ",
        "forecast_text": "forecast summary",
        "pricing_text": "pricing summary",
        "intelligence_text": "",
        "diagnosis_text": "diagnosis summary",
        "graph_product_ids": ["naphtha"],
        "intelligence_context": {"events": []},
        "web_search_results": "",
        "conversation_history": [],
        "resolved_chat_mode": "report",
        "mode_reason": "test",
    }

    node(state)

    assert captured["route_hint"] == "external"
    assert captured["sanitized"] is True
    assert "combined_insight_bundle" in captured["user_prompt"]
    assert "SECRET_SALES_ROWS" not in captured["user_prompt"]
    assert "combined_insight_bundle_v1" in captured["user_prompt"]


def test_analysis_external_route_uses_unified_bundle_payload(monkeypatch):
    import backend.src.engine.decision_graph as decision_graph
    from backend.src.nlu.types import TaskType as NluTaskType

    captured = {}

    class FakeLLM:
        def generate_completion(self, system_prompt, user_prompt, route_hint=None, sanitized=None, **kwargs):
            captured["route_hint"] = route_hint
            captured["sanitized"] = sanitized
            captured["user_prompt"] = user_prompt
            return "analysis output"

    monkeypatch.setattr(
        decision_graph.insight_snapshot_service,
        "get_combined_bundle_cached",
        lambda product_ids=None, market_max_age_seconds=None, erp_max_age_seconds=None, async_refresh_on_miss=True: {
            "ok": True,
            "market": {"snapshot_id": "m1", "generated_at": "2026-04-17T08:00:00", "age_seconds": 120, "stale": False, "count": 1},
            "erp": {"snapshot_id": "e1", "generated_at": "2026-04-17T07:55:00", "age_seconds": 420, "stale": False, "count": 1},
            "by_product": {
                "naphtha": {
                    "product_id": "naphtha",
                    "market": {"latest_price": 1074, "weekly_change_pct": -7.45, "unit": "RMB/ton", "as_of_date": "2026-04-14", "narrative": "Naphtha softened this week."},
                    "erp": {"sales_30d_ton": 111.0, "inventory_days": 8.0, "risk_level": "low", "narrative": "Inventory remains tight."},
                }
            },
            "refresh_enqueued": {"market": False, "erp": False},
        },
    )

    node = decision_graph._make_generate_report(FakeLLM())
    state = {
        "user_query": "Analyze next-week price outlook for naphtha.",
        "user_context": SimpleNamespace(product="naphtha", period="Current Week"),
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.MARKET_ANALYSIS_DECISION),
        "system_prompt": "system",
        "user_prompt": "RAW LEGACY USER PROMPT SECRET_SALES_ROWS",
        "user_prefs": {
            "kimi_mixed_synthesis": False,
            "chat_mode": "analysis",
        },
        "response_mode": "structured",
        "tool_info": "INTERNAL DATABASE LIVE DATA: SECRET_SALES_ROWS",
        "erp_data": "SECRET_SALES_ROWS",
        "forecast_text": "forecast summary",
        "pricing_text": "pricing summary",
        "intelligence_text": "intelligence summary",
        "diagnosis_text": "diagnosis summary",
        "graph_product_ids": ["naphtha"],
        "intelligence_context": {"events": []},
        "web_search_results": "",
        "conversation_history": [],
        "resolved_chat_mode": "analysis",
        "mode_reason": "test",
    }

    node(state)

    assert captured["route_hint"] == "external"
    assert captured["sanitized"] is True
    assert "combined_insight_bundle" in captured["user_prompt"]
    assert "combined_insight_bundle_v1" in captured["user_prompt"]
    assert "SECRET_SALES_ROWS" not in captured["user_prompt"]


def test_analysis_final_route_defaults_to_external_when_unset():
    import backend.src.engine.decision_graph as decision_graph
    from backend.src.nlu.types import TaskType as NluTaskType

    captured = {}

    class FakeLLM:
        def generate_completion(self, system_prompt, user_prompt, route_hint=None, sanitized=None, **kwargs):
            captured["route_hint"] = route_hint
            captured["sanitized"] = sanitized
            return "analysis output"

    node = decision_graph._make_generate_report(FakeLLM())
    state = {
        "user_query": "Analyze next-week price outlook for naphtha.",
        "user_context": SimpleNamespace(product="naphtha", period="Current Week"),
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.MARKET_ANALYSIS_DECISION),
        "system_prompt": "system",
        "user_prompt": "user",
        "user_prefs": {
            "kimi_mixed_synthesis": False,
            "chat_mode": "analysis",
        },
        "response_mode": "structured",
        "tool_info": "",
        "erp_data": "",
        "forecast_text": "",
        "pricing_text": "",
        "intelligence_text": "",
        "diagnosis_text": "",
        "graph_product_ids": ["naphtha"],
        "intelligence_context": {"events": []},
        "web_search_results": "",
        "conversation_history": [],
        "resolved_chat_mode": "analysis",
        "mode_reason": "test",
    }

    node(state)

    assert captured["route_hint"] == "external"
    assert captured["sanitized"] is False


def test_analysis_explicit_local_override_kept_for_final_route():
    import backend.src.engine.decision_graph as decision_graph
    from backend.src.nlu.types import TaskType as NluTaskType

    captured = {}

    class FakeLLM:
        def generate_completion(self, system_prompt, user_prompt, route_hint=None, sanitized=None, **kwargs):
            captured["route_hint"] = route_hint
            return "analysis output"

    node = decision_graph._make_generate_report(FakeLLM())
    state = {
        "user_query": "Analyze next-week price outlook for naphtha.",
        "user_context": SimpleNamespace(product="naphtha", period="Current Week"),
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.MARKET_ANALYSIS_DECISION),
        "system_prompt": "system",
        "user_prompt": "user",
        "user_prefs": {
            "kimi_mixed_synthesis": False,
            "final_decision_llm": "local",
            "chat_mode": "analysis",
        },
        "response_mode": "structured",
        "tool_info": "",
        "erp_data": "",
        "forecast_text": "",
        "pricing_text": "",
        "intelligence_text": "",
        "diagnosis_text": "",
        "graph_product_ids": ["naphtha"],
        "intelligence_context": {"events": []},
        "web_search_results": "",
        "conversation_history": [],
        "resolved_chat_mode": "analysis",
        "mode_reason": "test",
    }

    node(state)

    assert captured["route_hint"] == "local"


def test_conversational_fallback_keeps_local_default_route():
    import backend.src.engine.decision_graph as decision_graph
    from backend.src.nlu.types import TaskType as NluTaskType

    captured = {}

    class FakeLLM:
        def generate_completion(self, system_prompt, user_prompt, route_hint=None, sanitized=None, **kwargs):
            captured["route_hint"] = route_hint
            return "chat output"

    node = decision_graph._make_generate_report(FakeLLM())
    state = {
        "user_query": "Hello, can you summarize quickly?",
        "user_context": SimpleNamespace(product=None, period="Current Week"),
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.CONVERSATIONAL),
        "system_prompt": "system",
        "user_prompt": "user",
        "user_prefs": {
            "kimi_mixed_synthesis": False,
            "chat_mode": "chat",
        },
        "response_mode": "concise",
        "tool_info": "",
        "erp_data": "",
        "forecast_text": "",
        "pricing_text": "",
        "intelligence_text": "",
        "diagnosis_text": "",
        "graph_product_ids": [],
        "intelligence_context": {"events": []},
        "web_search_results": "",
        "conversation_history": [],
        "resolved_chat_mode": "chat",
        "mode_reason": "test",
    }

    node(state)

    assert captured["route_hint"] == "local"


def test_external_first_online_routes_simple_lookup_to_external():
    import backend.src.engine.decision_graph as decision_graph
    from backend.src.nlu.types import TaskType as NluTaskType

    captured = {}

    class FakeLLM:
        def generate_completion(self, system_prompt, user_prompt, route_hint=None, sanitized=None, **kwargs):
            captured["route_hint"] = route_hint
            captured["called"] = True
            return "external lookup output"

    node = decision_graph._make_generate_report(FakeLLM())
    state = {
        "user_query": "What are main price moves for Cracked C5 and isoprene this week, and what changed week over week?",
        "user_context": SimpleNamespace(product="Cracked C5 and isoprene", period="Current Week"),
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.SIMPLE_LOOKUP),
        "system_prompt": "system",
        "user_prompt": "user",
        "user_prefs": {
            "kimi_mixed_synthesis": False,
            "external_first_online": True,
            "chat_mode": "analysis",
        },
        "response_mode": "concise",
        "tool_info": "",
        "erp_data": "",
        "forecast_text": "",
        "pricing_text": "",
        "intelligence_text": "",
        "diagnosis_text": "",
        "graph_product_ids": ["cracked_c5", "isoprene"],
        "intelligence_context": {"events": []},
        "web_search_results": "",
        "conversation_history": [],
        "resolved_chat_mode": "analysis",
        "mode_reason": "test",
    }

    out = node(state)

    assert captured.get("called") is True
    assert captured["route_hint"] == "external"
    assert "external lookup output" in out["report"].raw_markdown


def test_external_first_online_keeps_erp_data_query_local_by_default():
    import backend.src.engine.decision_graph as decision_graph
    from backend.src.nlu.types import TaskType as NluTaskType

    captured = {}

    class FakeLLM:
        def generate_completion(self, system_prompt, user_prompt, route_hint=None, sanitized=None, **kwargs):
            captured["route_hint"] = route_hint
            return "erp output"

    node = decision_graph._make_generate_report(FakeLLM())
    state = {
        "user_query": "Summarize our inventory and sales this week.",
        "user_context": SimpleNamespace(product=None, period="Current Week"),
        "parsed_intent": SimpleNamespace(task_type=NluTaskType.ERP_DATA_QUERY),
        "system_prompt": "system",
        "user_prompt": "user",
        "user_prefs": {
            "kimi_mixed_synthesis": False,
            "external_first_online": True,
            "chat_mode": "analysis",
        },
        "response_mode": "structured",
        "tool_info": "",
        "erp_data": "sales summary",
        "forecast_text": "",
        "pricing_text": "",
        "intelligence_text": "",
        "diagnosis_text": "",
        "graph_product_ids": [],
        "intelligence_context": {"events": []},
        "web_search_results": "",
        "conversation_history": [],
        "resolved_chat_mode": "analysis",
        "mode_reason": "test",
    }

    node(state)

    assert captured["route_hint"] == "local"
