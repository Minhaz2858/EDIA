"""Unit tests for unified query router."""

from __future__ import annotations

from backend.src.routing.query_router import route_query
from backend.src.prompts.task_prompt import TaskType


def test_chinese_sinopec_listing_routes_deterministic_market():
    q = "SINOPEC的月度挂牌价是否已公布？其变动方向如何"
    routed = route_query(q)
    assert routed.path == "deterministic_market"
    assert routed.metric_id == "sinopec_monthly_listing"
    assert "sinopec_sh" in routed.price_types
    assert routed.skip_rag is True
    assert routed.skip_llm_finalizer is True
    assert routed.task_type == TaskType.SIMPLE_LOOKUP


def test_english_sinopec_listing_routes_deterministic_market():
    q = "Has SINOPEC's monthly listing price been released? And how has it changed?"
    routed = route_query(q)
    assert routed.path == "deterministic_market"
    assert routed.metric_id == "sinopec_monthly_listing"


def test_china_market_database_price_wow_not_discovery():
    q = "请基于中国市场数据库，说明本周裂解C5和异戊二烯的主流价格变化，以及周环比方向。"
    routed = route_query(q)
    assert routed.path != "deterministic_discovery"
    assert routed.path in ("deterministic_market", "tool_then_llm", "full_pipeline")


def test_database_catalog_still_discovery():
    q = "老板问数据库里销售、库存、合同哪些数据目前能直接回答？"
    routed = route_query(q)
    assert routed.path == "deterministic_discovery"


def test_erp_order_details_deterministic_erp():
    q = "请提供 5 月 11 日至 5 月 12 日的订单详情。"
    routed = route_query(q)
    assert routed.path == "deterministic_erp"
    assert routed.erp_capability == "erp_order_details"
