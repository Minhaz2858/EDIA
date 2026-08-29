from __future__ import annotations

from datetime import date

from backend.src.tools import erp_tools
from backend.src.tools.db_capability_registry import (
    looks_like_database_backed_question,
    resolve_database_capability,
)
from backend.server import _deterministic_erp_stream_enhancements
from backend.server import _is_deterministic_erp_stream_request


QUESTION_CASES = [
    {
        "id": "q01_exact_chinese_order_details",
        "question": "请提供 5 月 11 日至 5 月 12 日的订单详情。",
        "perspective": "sales_ops",
        "product": "all",
        "scenario": "exact UI regression for spaced Chinese date range",
        "expected_capability": "erp_order_details",
        "expected_start": "2026-05-11",
        "expected_end": "2026-05-12",
    },
    {
        "id": "q02_boss_blowing_agent_sales",
        "question": "老板想看最近一周戊烷发泡剂的销售情况怎么样。",
        "perspective": "boss",
        "product": "blowing_agent",
        "scenario": "weekly product sales summary",
        "expected_capability": "erp_sales_summary",
    },
    {
        "id": "q03_english_order_details",
        "question": "Please provide order details from 5-11 to 5-12.",
        "perspective": "sales_ops",
        "product": "all",
        "scenario": "English month-day range",
        "expected_capability": "erp_order_details",
        "expected_start": "2026-05-11",
        "expected_end": "2026-05-12",
    },
    {
        "id": "q03b_english_month_name_order_details",
        "question": "Please provide order details for May 11th to May 12th.",
        "perspective": "sales_ops",
        "product": "all",
        "scenario": "English month-name range",
        "expected_capability": "erp_order_details",
        "expected_start": "2026-05-11",
        "expected_end": "2026-05-12",
    },
    {
        "id": "q04_customer_detail_c5_resin",
        "question": "请按客户明细列出本周碳五石油树脂的客户销量。",
        "perspective": "sales_manager",
        "product": "c5_resin",
        "scenario": "customer volume by product",
        "expected_capability": "erp_customer_sales",
    },
    {
        "id": "q05_inventory_stock_boss",
        "question": "老板问当前库存记录按产品怎么分布？",
        "perspective": "boss",
        "product": "all",
        "scenario": "unmapped inventory discovery",
        "expected_discovery": True,
    },
    {
        "id": "q06_isoprene_sales_amount",
        "question": "异戊二烯本月销售额是多少，和销量一起给我。",
        "perspective": "finance",
        "product": "isoprene",
        "scenario": "monthly revenue and volume",
        "expected_capability": "erp_sales_summary",
    },
    {
        "id": "q07_piperylene_order_rows",
        "question": "间戊二烯 5 月 11 日到 5 月 12 日销售订单明细给我。",
        "perspective": "sales_ops",
        "product": "piperylene",
        "scenario": "Chinese order rows by product",
        "expected_capability": "erp_order_details",
        "expected_start": "2026-05-11",
        "expected_end": "2026-05-12",
    },
    {
        "id": "q08_dcpd_customer_sales",
        "question": "双环戊二烯客户销售排名本周是什么情况？",
        "perspective": "sales_director",
        "product": "dcpd",
        "scenario": "customer ranking",
        "expected_capability": "erp_customer_sales",
    },
    {
        "id": "q09_cracked_c5_sales_detail",
        "question": "工业用裂解碳五最近7天销售明细要给业务复盘。",
        "perspective": "business_review",
        "product": "cracked_c5",
        "scenario": "sales detail review",
        "expected_capability": "erp_sales_summary",
    },
    {
        "id": "q10_cracked_c9_revenue",
        "question": "工业用裂解碳九本周销售额和销量分别是多少？",
        "perspective": "finance",
        "product": "cracked_c9",
        "scenario": "weekly revenue and volume",
        "expected_capability": "erp_sales_summary",
    },
    {
        "id": "q11_raffinate_c5_order_detail",
        "question": "抽余碳五本周订单详情按订单号列出来。",
        "perspective": "sales_ops",
        "product": "raffinate_c5",
        "scenario": "order number detail",
        "expected_capability": "erp_order_details",
    },
    {
        "id": "q12_sis_sales_summary",
        "question": "SIS橡胶最近一周销售数据给我，老板只看重点。",
        "perspective": "boss",
        "product": "sis",
        "scenario": "executive summary",
        "expected_capability": "erp_sales_summary",
    },
    {
        "id": "q13_styrene_customer_volume",
        "question": "苯乙烯客户销量明细本月有多少？",
        "perspective": "sales_manager",
        "product": "styrene",
        "scenario": "customer volume detail",
        "expected_capability": "erp_customer_sales",
    },
    {
        "id": "q14_hydrogenated_c9_sales",
        "question": "加氢碳九今年销售情况如何？",
        "perspective": "strategy",
        "product": "hydrogenated_c9",
        "scenario": "year-to-date sales",
        "expected_capability": "erp_sales_summary",
    },
    {
        "id": "q15_trimethylbenzene_order_details",
        "question": "混三甲苯订单明细从 2026 年 5 月 11 日至 2026 年 5 月 12 日。",
        "perspective": "sales_ops",
        "product": "trimethylbenzene",
        "scenario": "full Chinese year-month-day range",
        "expected_capability": "erp_order_details",
        "expected_start": "2026-05-11",
        "expected_end": "2026-05-12",
    },
    {
        "id": "q16_contract_execution",
        "question": "本周合同执行明细有没有异常？",
        "perspective": "risk_control",
        "product": "all",
        "scenario": "unmapped contract discovery",
        "expected_discovery": True,
    },
    {
        "id": "q17_outbound_shipments",
        "question": "请查一下 5 月 11 日至 5 月 12 日出库发货记录。",
        "perspective": "logistics",
        "product": "all",
        "scenario": "unmapped outbound discovery",
        "expected_discovery": True,
    },
    {
        "id": "q18_inbound_receipts",
        "question": "入库收货数据按物料给我看。",
        "perspective": "warehouse",
        "product": "all",
        "scenario": "unmapped inbound discovery",
        "expected_discovery": True,
    },
    {
        "id": "q19_forecast_records",
        "question": "未来两周戊烷发泡剂预测记录在哪些表里？",
        "perspective": "planning",
        "product": "blowing_agent",
        "scenario": "forecast table discovery",
        "expected_discovery": True,
    },
    {
        "id": "q20_market_price_data",
        "question": "碳五石油树脂市场价格数据最近有哪些记录？",
        "perspective": "market_analyst",
        "product": "c5_resin",
        "scenario": "market data discovery",
        "expected_discovery": True,
    },
    {
        "id": "q21_customer_sales_english",
        "question": "Show customer sales for blowing agent this week.",
        "perspective": "sales_manager",
        "product": "blowing_agent",
        "scenario": "English customer sales",
        "expected_capability": "erp_customer_sales",
    },
    {
        "id": "q22_sales_volume_english",
        "question": "What is this month's sales volume for isoprene?",
        "perspective": "finance",
        "product": "isoprene",
        "scenario": "English sales volume",
        "expected_capability": "erp_sales_summary",
    },
    {
        "id": "q23_order_detail_english_product",
        "question": "Give me sales order details for DCPD from 2026-05-11 to 2026-05-12.",
        "perspective": "sales_ops",
        "product": "dcpd",
        "scenario": "English full date order detail",
        "expected_capability": "erp_order_details",
        "expected_start": "2026-05-11",
        "expected_end": "2026-05-12",
    },
    {
        "id": "q24_inventory_english",
        "question": "Show current inventory stock records by product.",
        "perspective": "warehouse",
        "product": "all",
        "scenario": "English inventory discovery",
        "expected_discovery": True,
    },
    {
        "id": "q25_contract_english",
        "question": "Which contract detail records exist for cracked C5 this month?",
        "perspective": "legal_ops",
        "product": "cracked_c5",
        "scenario": "English contract discovery",
        "expected_discovery": True,
    },
    {
        "id": "q26_boss_top_products",
        "question": "老板要看本周哪些产品销售额最高。",
        "perspective": "boss",
        "product": "all",
        "scenario": "top products by revenue",
        "expected_capability": "erp_sales_summary",
    },
    {
        "id": "q27_low_volume_review",
        "question": "本周销量最低的产品销售数据是什么？",
        "perspective": "business_review",
        "product": "all",
        "scenario": "low volume review",
        "expected_capability": "erp_sales_summary",
    },
    {
        "id": "q28_price_check_sales",
        "question": "戊烷发泡剂销售明细里含税单价有没有变化？",
        "perspective": "pricing",
        "product": "blowing_agent",
        "scenario": "price in sales detail",
        "expected_capability": "erp_sales_summary",
    },
    {
        "id": "q29_customer_detail_risk",
        "question": "客户明细里有没有单一客户占比过高的情况？",
        "perspective": "risk_control",
        "product": "all",
        "scenario": "customer concentration",
        "expected_capability": "erp_customer_sales",
    },
    {
        "id": "q30_order_rows_audit",
        "question": "审计需要 5/11 到 5/12 的销售订单详情。",
        "perspective": "audit",
        "product": "all",
        "scenario": "audit order rows",
        "expected_capability": "erp_order_details",
        "expected_start": "2026-05-11",
        "expected_end": "2026-05-12",
    },
    {
        "id": "q31_no_data_future",
        "question": "请提供 2027 年 1 月 4 日至 2027 年 1 月 10 日的订单详情。",
        "perspective": "sales_ops",
        "product": "all",
        "scenario": "future no-data range should still route to order details",
        "expected_capability": "erp_order_details",
        "expected_start": "2027-01-04",
        "expected_end": "2027-01-10",
    },
    {
        "id": "q32_last_week_sales",
        "question": "上周异戊二烯销售情况怎么样？",
        "perspective": "sales_manager",
        "product": "isoprene",
        "scenario": "last week period",
        "expected_capability": "erp_sales_summary",
    },
    {
        "id": "q33_yesterday_sales",
        "question": "昨天双环戊二烯销量和销售额是多少？",
        "perspective": "daily_ops",
        "product": "dcpd",
        "scenario": "yesterday period",
        "expected_capability": "erp_sales_summary",
    },
    {
        "id": "q34_today_order_detail",
        "question": "今天的订单详情按客户和产品列出来。",
        "perspective": "daily_ops",
        "product": "all",
        "scenario": "today order rows",
        "expected_capability": "erp_order_details",
    },
    {
        "id": "q35_quarter_sales",
        "question": "本季度裂解碳九销售数据给经营会看。",
        "perspective": "management_meeting",
        "product": "cracked_c9",
        "scenario": "quarter sales",
        "expected_capability": "erp_sales_summary",
    },
    {
        "id": "q36_last_month_customers",
        "question": "上个月戊烷发泡剂客户销售表现怎么样？",
        "perspective": "sales_director",
        "product": "blowing_agent",
        "scenario": "last month customer sales",
        "expected_capability": "erp_customer_sales",
    },
    {
        "id": "q37_board_revenue",
        "question": "给董事会准备一页本月各产品销售额和销量摘要。",
        "perspective": "board",
        "product": "all",
        "scenario": "board summary",
        "expected_capability": "erp_sales_summary",
    },
    {
        "id": "q38_procurement_inbound",
        "question": "采购想看本周入库记录按供应商和产品分布。",
        "perspective": "procurement",
        "product": "all",
        "scenario": "inbound records discovery",
        "expected_discovery": True,
    },
    {
        "id": "q39_shipping_delay",
        "question": "物流角度看本周发货记录有没有延迟？",
        "perspective": "logistics",
        "product": "all",
        "scenario": "shipping delay discovery",
        "expected_discovery": True,
    },
    {
        "id": "q40_margin_proxy",
        "question": "财务想看本周销售额最高客户明细。",
        "perspective": "finance",
        "product": "all",
        "scenario": "customer revenue detail",
        "expected_capability": "erp_customer_sales",
    },
    {
        "id": "q41_product_mix",
        "question": "本周产品销售结构里戊烷发泡剂占比如何？",
        "perspective": "strategy",
        "product": "blowing_agent",
        "scenario": "product mix",
        "expected_capability": "erp_sales_summary",
    },
    {
        "id": "q42_customer_followup",
        "question": "哪些客户本周买了碳五石油树脂，客户明细给我。",
        "perspective": "account_manager",
        "product": "c5_resin",
        "scenario": "customer follow-up list",
        "expected_capability": "erp_customer_sales",
    },
    {
        "id": "q43_english_revenue_rank",
        "question": "Rank products by ERP revenue this week.",
        "perspective": "boss",
        "product": "all",
        "scenario": "English product revenue ranking",
        "expected_capability": "erp_sales_summary",
    },
    {
        "id": "q44_english_customer_concentration",
        "question": "List customer detail for C5 resin sales this month.",
        "perspective": "risk_control",
        "product": "c5_resin",
        "scenario": "English customer detail",
        "expected_capability": "erp_customer_sales",
    },
    {
        "id": "q45_english_outbound_records",
        "question": "Do we have outbound shipment records from May 11 to May 12?",
        "perspective": "logistics",
        "product": "all",
        "scenario": "English outbound discovery",
        "expected_discovery": True,
    },
    {
        "id": "q46_english_forecast_table",
        "question": "Which forecast records exist for blowing agent by date?",
        "perspective": "planning",
        "product": "blowing_agent",
        "scenario": "English forecast discovery",
        "expected_discovery": True,
    },
    {
        "id": "q47_audit_customer_volume",
        "question": "审计要客户销量明细，范围是本月。",
        "perspective": "audit",
        "product": "all",
        "scenario": "audit customer volume",
        "expected_capability": "erp_customer_sales",
    },
    {
        "id": "q48_sales_detail_negative",
        "question": "销售明细中有没有数量为零的订单？",
        "perspective": "data_quality",
        "product": "all",
        "scenario": "sales detail data quality",
        "expected_capability": "erp_sales_summary",
    },
    {
        "id": "q49_order_detail_customer",
        "question": "东莞新长桥塑料有限公司 5 月 11 日到 5 月 12 日订单明细。",
        "perspective": "account_manager",
        "product": "customer_specific",
        "scenario": "customer-specific order rows",
        "expected_capability": "erp_order_details",
        "expected_start": "2026-05-11",
        "expected_end": "2026-05-12",
    },
    {
        "id": "q50_database_boss_gap",
        "question": "老板问数据库里销售、库存、合同哪些数据目前能直接回答？",
        "perspective": "boss",
        "product": "all",
        "scenario": "database capability discovery for coverage gaps",
        "expected_discovery": True,
    },
    {
        "id": "q51_piperylene_inventory_quote_shipment_advisory",
        "question": (
            "Piperylene 7d decrease +0.0% (current 9,300 CNY/ton). "
            "How should current inventory be handled, do quotes need to follow downward, "
            "and how should shipment pace be adjusted?"
        ),
        "perspective": "sales_ops",
        "product": "piperylene",
        "scenario": "hybrid commercial ops advisory",
        "expected_hybrid_ops_advisory": True,
    },
]

MARKET_QUESTION_CASES = [
    {
        "id": "m01_chinese_sinopec_listing",
        "question": "SINOPEC的月度挂牌价是否已公布？其变动方向如何",
        "expected_path": "deterministic_market",
        "expected_metric": "sinopec_monthly_listing",
        "not_discovery": True,
    },
    {
        "id": "m02_english_sinopec_listing",
        "question": "Has SINOPEC's monthly listing price been released? And how has it changed?",
        "expected_path": "deterministic_market",
        "expected_metric": "sinopec_monthly_listing",
        "not_discovery": True,
    },
    {
        "id": "m03_china_market_db_wow",
        "question": "请基于中国市场数据库，说明本周裂解C5和异戊二烯的主流价格变化，以及周环比方向。",
        "expected_path_not": "deterministic_discovery",
        "not_discovery": True,
    },
    {
        "id": "m04_database_catalog",
        "question": "老板问数据库里销售、库存、合同哪些数据目前能直接回答？",
        "expected_path": "deterministic_discovery",
        "not_discovery": False,
    },
]


def test_market_question_suite_routes():
    from backend.src.routing.query_router import route_query
    from backend.src.tools.db_capability_registry import should_use_database_capability_discovery

    for case in MARKET_QUESTION_CASES:
        routed = route_query(case["question"])
        if case.get("expected_path"):
            assert routed.path == case["expected_path"], case["id"]
        if case.get("expected_path_not"):
            assert routed.path != case["expected_path_not"], case["id"]
        if case.get("expected_metric"):
            assert routed.metric_id == case["expected_metric"], case["id"]
        if case.get("not_discovery"):
            assert should_use_database_capability_discovery(case["question"]) is False, case["id"]


def test_question_suite_contains_52_distinct_business_scenarios():
    assert len(QUESTION_CASES) == 52
    assert len({case["id"] for case in QUESTION_CASES}) == 52
    assert len({case["question"] for case in QUESTION_CASES}) == 52
    for case in QUESTION_CASES:
        assert case["perspective"]
        assert case["product"]
        assert case["scenario"]


def test_50_question_suite_routes_to_expected_database_behavior():
    for case in QUESTION_CASES:
        question = case["question"]
        capability = resolve_database_capability(question)
        enhancements = _deterministic_erp_stream_enhancements(question)

        if case.get("expected_hybrid_ops_advisory"):
            assert _is_deterministic_erp_stream_request(question) is False, case["id"]
            assert enhancements.get("hybrid_ops_advisory") == "true", case["id"]
            assert enhancements.get("external_llm_blocked") == "false", case["id"]
            assert enhancements.get("llm_synthesis") == "facts_first", case["id"]
            continue

        assert _is_deterministic_erp_stream_request(question) is True, case["id"]

        if case.get("expected_discovery"):
            assert capability is None, case["id"]
            assert looks_like_database_backed_question(question) is True, case["id"]
            assert enhancements.get("database_capability_discovery") == "true", case["id"]
            assert enhancements.get("external_llm_blocked") == "true", case["id"]
            continue

        expected_capability = case["expected_capability"]
        assert capability is not None, case["id"]
        assert capability.capability_id == expected_capability, case["id"]
        assert capability.external_llm_allowed is False, case["id"]
        assert enhancements.get("database_capability") == expected_capability, case["id"]
        assert enhancements.get("external_llm_blocked") == "true", case["id"]
        if expected_capability == "erp_order_details":
            assert enhancements.get("deterministic_erp_order_details") == "true", case["id"]
            assert enhancements.get("skip_finalizer") == "true", case["id"]
        if expected_capability == "erp_sales_summary":
            assert enhancements.get("deterministic_erp_sales") == "true", case["id"]
            assert enhancements.get("skip_finalizer") == "true", case["id"]


def test_50_question_suite_explicit_date_ranges(monkeypatch):
    monkeypatch.setattr(erp_tools, "_get_latest_erp_date", lambda: date(2026, 5, 14))

    checked = 0
    for case in QUESTION_CASES:
        if not case.get("expected_start"):
            continue
        start, end = erp_tools._resolve_period(case["question"])
        assert str(start) == case["expected_start"], case["id"]
        assert str(end) == case["expected_end"], case["id"]
        checked += 1

    assert checked >= 8
