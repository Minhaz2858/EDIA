from backend.src.planner.schema_registry import (
    build_source_operations,
    get_tables_for_plan,
    resolve_product,
)
from backend.src.planner.query_planner import keyword_fallback, parse_planner_output
from backend.src.planner.query_executor import build_operation_sql, build_sql


def test_resolve_product_chinese() -> None:
    assert resolve_product("异戊二烯本周价格") == "isoprene"


def test_resolve_product_english() -> None:
    assert resolve_product("DCPD market trend") == "dcpd"


def test_resolve_product_unknown() -> None:
    assert resolve_product("hello world") is None


def test_resolve_product_uses_domain_alias_mapping() -> None:
    assert resolve_product("戊烷发泡剂库存") == "foaming_agent"


def test_get_tables_for_plan_normal() -> None:
    assert "v_actual_price" in get_tables_for_plan("isoprene", "actual_price")


def test_get_tables_for_plan_lz() -> None:
    assert "异戊二烯" in get_tables_for_plan("isoprene", "market_reference")


def test_get_tables_for_plan_no_lz() -> None:
    assert get_tables_for_plan("c5_petroleum_resin", "market_reference") == []


def test_keyword_fallback_with_product() -> None:
    plan = keyword_fallback("异戊二烯价格")
    assert plan is not None
    assert plan["products"] == ["isoprene"]
    assert plan["confidence"] == "fallback"
    assert plan["source_operations"]


def test_keyword_fallback_no_product() -> None:
    assert keyword_fallback("hello") is None


def test_keyword_fallback_inbound_chinese_routes_logistics_intents() -> None:
    plan = keyword_fallback("异戊二烯最近入库和收货情况")
    assert plan is not None
    assert "inbound_receipt" in plan["intents"]
    assert "purchase_order" in plan["intents"]


def test_keyword_fallback_outbound_english_routes_logistics_intents() -> None:
    plan = keyword_fallback("Isoprene outbound shipment delivery this week")
    assert plan is not None
    assert "outbound_shipment" in plan["intents"]


def test_keyword_fallback_production_and_transfer_routes_logistics_intents() -> None:
    plan = keyword_fallback("DCPD production plan and transfer status")
    assert plan is not None
    assert "production_in" in plan["intents"]
    assert "production_out" in plan["intents"]
    assert "internal_transfer" in plan["intents"]


def test_get_tables_for_new_logistics_intents() -> None:
    assert "erp_v_raw_material_receiving" in get_tables_for_plan("isoprene", "inbound_receipt")
    assert "erp_v_sale_orderentry" in get_tables_for_plan("isoprene", "outbound_shipment")


def test_build_sql_parameterized() -> None:
    built = build_sql(
        "v_actual_price",
        "actual_price",
        "isoprene",
        {"start": "2026-04-10", "end": "2026-04-17"},
    )
    assert built is not None
    sql, params = built
    assert ":product_name" in sql
    assert ":start_date" in sql
    assert params["product_name"] == "异戊二烯"


def test_build_source_operations_from_intents() -> None:
    operations = build_source_operations(["isoprene"], ["inventory", "sales"])
    assert {op["source"] for op in operations} == {"inventory", "sales"}
    assert all(op["product"] == "isoprene" for op in operations)


def test_parse_planner_output_validates_source_operations() -> None:
        raw = """
        {
            "products": ["isoprene"],
            "intents": ["inventory", "sales"],
            "source_operations": [
                {"source": "inventory", "product": "isoprene", "operation": "latest"},
                {"source": "not_allowed", "product": "isoprene", "operation": "drop"}
            ],
            "time_range": {"start": "2026-04-01", "end": "2026-04-08"},
            "needs_forecast": false,
            "confidence": "high"
        }
        """
        plan = parse_planner_output(raw, "异戊二烯库存和销售")
        assert plan is not None
        assert len(plan["source_operations"]) == 1
        assert plan["source_operations"][0]["source"] == "inventory"


def test_build_operation_sql_uses_allowlisted_operation() -> None:
    tasks = build_operation_sql(
        {"source": "sales", "product": "isoprene", "operation": "sum"},
        {"start": "2026-04-01", "end": "2026-04-08"},
    )
    assert tasks
    _product, intent, table, sql, params = tasks[0]
    assert intent == "sales"
    assert table == "erp_v_sale_orderentry"
    assert "SUM(" in sql
    assert ":product_name" in sql
    assert params["product_name"] == "异戊二烯"
