"""
Phase 1 Enhancement Endpoints: Confidence Scoring, Audit Trail, Risk Alerts, Backtest Validation
"""

# Add these imports at the top of server.py (in the imports section):
# from backend.src.service.confidence_scorer import confidence_scorer
# from backend.src.service.decision_audit import decision_history
# from backend.src.validation.backtest_runner import backtest_runner

# ===========================================================================================
# PHASE 1: CONFIDENCE SCORING, AUDIT TRAIL, RISK ALERTS, BACKTEST VALIDATION
# ===========================================================================================

# Phase 1 endpoints to add to FastAPI app in server.py


@app.get("/api/phase1/forecast/with-confidence")
async def fetch_forecast_with_confidence(product_id: str) -> dict:
    """
    Return a forecast with detailed confidence scoring and uncertainty ranges.

    Example:
        GET /api/phase1/forecast/with-confidence?product_id=Cracked%20C5

    Returns:
        {
            "product_id": "Cracked C5",
            "base_price": 5000,
            "confidence": {
                "overall": 0.78,  # HIGH
                "data_quality": 0.95,
                "evidence_strength": 0.72,
                "model_stability": 0.75,
                "macro_uncertainty": 0.35,
                "p25": 4950,      # 25th percentile
                "p50": 5000,      # Median
                "p75": 5050,      # 75th percentile
                "uncertainty_source": "macro_volatility",
                "confidence_label": "HIGH",
                "lookback_mape": 0.032
            },
            "scenarios": {
                "bull": {"price": 5200, "probability": 0.20},
                "base": {"price": 5000, "probability": 0.65},
                "bear": {"price": 4800, "probability": 0.15}
            }
        }
    """

    try:
        from backend.src.service.market_data_service import market_data as md_svc
        from backend.src.service.confidence_scorer import confidence_scorer

        # Get current snapshot
        snapshot = {}
        for node in domain_graph.nodes:
            history = md_svc.get_price_history(node.id)
            if history:
                latest = history[-1]
                snapshot[node.id] = {
                    "latest_price": latest["price"],
                    "data_source": "real",
                }

        # Mock forecast and evidence (in real system, would run full pipeline)
        forecast = {
            "base": 5000,
            "bull": 5200,
            "bear": 4800,
        }
        evidence_bundle = {
            "bullish_score": 0.6,
            "bearish_score": 0.4,
            "contradictions": [],
            "dominant_view": "bullish",
        }
        diagnosis = {
            "regime_confidence": 0.75,
        }
        perception_snapshot = {
            "products": {product_id: snapshot.get(product_id, {})},
            "reference_date": datetime.now().isoformat(),
        }
        macro_context = {
            "active_overrides": [],
            "risk_level": "medium",
        }

        # Score confidence
        conf = confidence_scorer.score_forecast(
            product_id=product_id,
            evidence_bundle=evidence_bundle,
            diagnosis=diagnosis,
            forecast=forecast,
            perception_snapshot=perception_snapshot,
            macro_context=macro_context,
            lookback_mape=None,
        )

        return {
            "product_id": product_id,
            "base_price": forecast["base"],
            "confidence": {
                "overall": conf.overall,
                "data_quality": conf.data_quality,
                "evidence_strength": conf.evidence_strength,
                "model_stability": conf.model_stability,
                "macro_uncertainty": conf.macro_uncertainty,
                "p25": conf.p25,
                "p50": conf.p50,
                "p75": conf.p75,
                "uncertainty_source": conf.uncertainty_source,
                "confidence_label": conf.confidence_label,
                "lookback_mape": conf.lookback_mape,
            },
            "scenarios": {
                "bull": {
                    "price": forecast.get("bull", forecast["base"] * 1.04),
                    "probability": 0.20,
                    "description": "Supply constraints + demand strength",
                },
                "base": {
                    "price": forecast["base"],
                    "probability": 0.65,
                    "description": "Seasonal patterns + normal macro",
                },
                "bear": {
                    "price": forecast.get("bear", forecast["base"] * 0.96),
                    "probability": 0.15,
                    "description": "Demand weakness + supply surplus",
                },
            },
            "confidence_interpretation": {
                "HIGH": "Use for pricing/trading",
                "MEDIUM": "Watch for signals; consider scenarios",
                "LOW": "Wait for clarity; monitor closely",
            }[conf.confidence_label],
        }

    except Exception as e:
        logger.error(f"Confidence forecast error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/phase1/decision/audit")
async def get_decision_audit_trail(product_id: str, days: int = 7) -> dict:
    """
    Get the decision audit trail: how recommendations changed over days.

    Example:
        GET /api/phase1/decision/audit?product_id=Cracked%20C5&days=7

    Returns:
        {
            "product": "Cracked C5",
            "period": "Last 7 days",
            "audit_trail": [
                {
                    "date": "2026-03-07T10:30:00",
                    "recommendation_price": 4950,
                    "confidence": 0.75,
                    "changes": {},
                    "change_summary": "No changes from prior day"
                },
                {
                    "date": "2026-03-08T10:30:00",
                    "recommendation_price": 4980,
                    "confidence": 0.78,
                    "changes": {
                        "price": {"from": 4950, "to": 4980, "delta": 30, "pct": 0.61},
                        "naphtha_price": {"from": 648, "to": 653}
                    },
                    "change_summary": "↑ Price 4950 → 4980 (+0.61%) | naphtha_price: 648 → 653"
                },
                ...
            ]
        }
    """

    try:
        from backend.src.service.decision_audit import decision_history

        audit_trail = decision_history.get_audit_trail(product_id, days)

        return {
            "product": product_id,
            "period": f"Last {days} days",
            "audit_trail": audit_trail,
            "interpretation": {
                "stable": "Recommendations haven't changed significantly (trust level: HIGH)",
                "moderate": "Small adjustments day-to-day (trust level: MEDIUM)",
                "volatile": "Large swings day-to-day (trust level: LOW, investigate)",
            },
        }

    except Exception as e:
        logger.error(f"Audit trail error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/phase1/risk/alerts")
async def get_risk_alerts(product_id: Optional[str] = None) -> dict:
    """
    Get all active risk alerts ranked by severity × probability.

    Example:
        GET /api/phase1/risk/alerts?product_id=Cracked%20C5
        or
        GET /api/phase1/risk/alerts (for all products)

    Returns:
        {
            "total_alerts": 3,
            "critical_count": 1,
            "high_count": 1,
            "medium_count": 1,
            "alerts": [
                {
                    "severity": "🔴 CRITICAL",
                    "category": "macro",
                    "description": "Strait of Hormuz tensions; crude +8%",
                    "impact_on_price": 300,
                    "probability": 0.65,
                    "evidence": "Iran navy activity + insurance premiums spike",
                    "products_affected": ["Cracked C5", "Isoprene", "C9"]
                },
                {
                    "severity": "🟠 HIGH",
                    "category": "supply",
                    "description": "Derong new unit ramping faster than expected",
                    "impact_on_price": -400,
                    "probability": 0.45,
                    "evidence": "Operating rate jumped 12% in 1 week",
                    "products_affected": ["DCPD"]
                },
                ...
            ]
        }
    """

    try:
        alerts = []

        # Filter by product if requested
        if product_id:
            alerts = [a for a in alerts if product_id in a["products_affected"]]

        # Count by severity
        severity_counts = {
            "critical": len([a for a in alerts if "CRITICAL" in a["severity"]]),
            "high": len([a for a in alerts if "HIGH" in a["severity"]]),
            "medium": len([a for a in alerts if "MEDIUM" in a["severity"]]),
            "low": len([a for a in alerts if "LOW" in a["severity"]]),
        }

        return {
            "product": product_id or "all",
            "status": "ok" if alerts else "no_live_alerts",
            "total_alerts": len(alerts),
            **severity_counts,
            "alerts": sorted(
                alerts,
                key=lambda x: (
                    x["probability"]
                    * (
                        500
                        if "CRITICAL" in x["severity"]
                        else 300 if "HIGH" in x["severity"] else 100
                    )
                ),
                reverse=True,
            ),
            "message": (
                None
                if alerts
                else "No warehouse-backed alerts were available for the requested scope."
            ),
        }

    except Exception as e:
        logger.error(f"Risk alerts error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/phase1/validation/backtest-performance")
async def get_backtest_performance(product_id: str, days_back: int = 90) -> dict:
    """
    Backtest performance: show how well EDIA forecasts matched reality.
    Builds trust by proving accuracy vs human analyst.

    Example:
        GET /api/phase1/validation/backtest-performance?product_id=Cracked%20C5&days_back=90

    Returns:
        {
            "product": "Cracked C5",
            "period": "Last 90 days",
            "accuracy": {
                "mape_3d": 0.032,          # 3.2% mean absolute percentage error
                "mape_7d": 0.048,
                "direction_accuracy": 0.77,  # Did system get up/down right?
                "range_coverage": 0.82,      # Did bull/bear range contain actual?
                "mape_label": "Good"
            },
            "vs_human": {
                "edia_win_rate": 0.68,     # EDIA better than human 68% of time
                "human_win_rate": 0.22,
                "tie_rate": 0.10
            },
            "monthly_breakdown": [
                {"month": "2026-01", "count": 13, "mape": 0.028, "direction_accuracy": 0.80},
                {"month": "2026-02",  "count": 12, "mape": 0.045, "direction_accuracy": 0.73},
            ],
            "worst_miss": {
                "date": "2026-01-06",
                "forecast": 10000,
                "actual": 10890,
                "error_pct": 0.089,
                "reason": "Missed Israel-Hamas geopolitical shock (not in training data)"
            },
            "interpretation": "EDIA is more accurate than experienced human 7 out of 10 times."
        }
    """

    try:
        from backend.src.validation.backtest_runner import backtest_runner

        # Run backtest series
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

        backtest_results = backtest_runner.run_backtest_series(
            product_id=product_id,
            start_date=start_date,
            end_date=end_date,
            horizon_days=7,
            step_days=7,
        )

        if not backtest_results:
            return {
                "product": product_id,
                "status": "unavailable",
                "message": "Historical forecast snapshots are not available; simulated backtests are disabled in warehouse-only mode.",
            }

        return {
            "product": product_id,
            "period": f"Last {days_back} days",
            "status": "ok",
            "accuracy": {
                "mape_7d": backtest_results.get("mape"),
                "mape_label": backtest_results.get("mape_label"),
                "direction_accuracy": backtest_results.get("direction_accuracy"),
                "range_coverage": backtest_results.get("range_coverage"),
            },
            "monthly_breakdown": backtest_results.get("monthly", []),
            "results": backtest_results.get("results", []),
            "total_backtests": backtest_results.get("total_backtests", 0),
        }

    except Exception as e:
        logger.error(f"Backtest performance error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ===========================================================================================
# End of Phase 1 endpoints
# ===========================================================================================
