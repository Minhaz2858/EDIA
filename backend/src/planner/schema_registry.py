"""Static registry mapping products/intents to DB query targets."""

from __future__ import annotations

import re
from typing import Dict, List, Optional


try:
    from backend.src.domain.product_resolver import resolve_product_id
except Exception:  # pragma: no cover - optional when planner is imported in isolation
    resolve_product_id = None


PRODUCTS: Dict[str, dict] = {
    "isoprene": {
        "chinese_name": "异戊二烯",
        "english_name": "Isoprene",
        "material_codes": [202000001, 10000001],
        "lz_table": "异戊二烯",
        "aliases": ["异戊二烯", "isoprene", "IP"],
    },
    "piperylene": {
        "chinese_name": "间戊二烯",
        "english_name": "Piperylene",
        "material_codes": [202000002],
        "lz_table": "间戊二烯",
        "aliases": ["间戊二烯", "piperylene"],
    },
    "dcpd": {
        "chinese_name": "双环戊二烯",
        "english_name": "DCPD",
        "material_codes": [202000003],
        "lz_table": "双环戊二烯",
        "aliases": ["双环戊二烯", "DCPD", "dicyclopentadiene"],
    },
    "styrene": {
        "chinese_name": "苯乙烯",
        "english_name": "Styrene",
        "material_codes": [],
        "lz_table": "苯乙烯",
        "aliases": ["苯乙烯", "styrene"],
    },
    "cracked_c5": {
        "chinese_name": "工业用裂解碳五",
        "english_name": "Cracked C5",
        "material_codes": [101000001, 101000002],
        "lz_table": "裂解C5",
        "aliases": ["裂解C5", "裂解碳五", "cracked C5", "C5"],
    },
    "foaming_agent": {
        "chinese_name": "戊烷发泡剂",
        "english_name": "Pentane Blowing Agent",
        "material_codes": [202000023],
        "lz_table": "发泡剂",
        "aliases": ["发泡剂", "戊烷发泡剂", "pentane blowing agent"],
    },
    "sis": {
        "chinese_name": "SIS",
        "english_name": "SIS",
        "material_codes": [],
        "lz_table": "SIS",
        "aliases": ["SIS"],
    },
    "sbs": {
        "chinese_name": "SBS",
        "english_name": "SBS / Butadiene",
        "material_codes": [],
        "lz_table": "丁二烯_SBS",
        "aliases": ["SBS", "丁二烯", "butadiene"],
    },
    "c5_petroleum_resin": {
        "chinese_name": "碳五石油树脂",
        "english_name": "C5 Petroleum Resin",
        "material_codes": [201000001, 201000002, 201000020],
        "lz_table": None,
        "aliases": ["碳五石油树脂", "C5 resin", "petroleum resin"],
    },
}


DOMAIN_PRODUCT_TO_PLANNER_KEY: Dict[str, str] = {
    "blowing_agent": "foaming_agent",
    "c5_resin": "c5_petroleum_resin",
}


INTENTS: Dict[str, dict] = {
    "actual_price": {
        "tables": ["v_actual_price"],
        "date_col": "date",
        "filter_col": "material_name",
    },
    "forecast_price": {
        "tables": ["v_forecast_price"],
        "date_col": "date",
        "filter_col": "material_name",
    },
    "partner_forecast": {
        "tables": ["v_partner_forecast_price"],
        "date_col": "date",
        "filter_col": "material_name",
    },
    "market_reference": {
        "tables": ["<lz_table>"],
        "date_col": "Unnamed: 0",
        "filter_col": None,
    },
    "inventory": {
        "tables": ["erp_v_stk_inventory"],
        "date_col": None,
        "filter_col": "material_name",
    },
    "sales": {
        "tables": ["erp_v_sale_orderentry"],
        "date_col": "PLANDATE",
        "filter_col": "material_name",
    },
    "outbound_shipment": {
        "tables": ["erp_v_sale_orderentry"],
        "date_col": "PLANDATE",
        "filter_col": "material_name",
    },
    "inbound_receipt": {
        "tables": ["erp_v_raw_material_receiving"],
        "date_col": "FDATE",
        "filter_col": "material_name",
    },
    "purchase_order": {
        "tables": ["erp_v_raw_material_receiving"],
        "date_col": "FDATE",
        "filter_col": "material_name",
    },
    "contract": {
        "tables": ["erp_v_contract", "erp_v_contract_execution"],
        "date_col": "FDATE",
        "filter_col": "material_name",
    },
    "raw_material": {
        "tables": ["erp_v_raw_material_receiving"],
        "date_col": "FDATE",
        "filter_col": "material_name",
    },
    "production": {
        "tables": ["erp_v_product_grade", "v2_erp_v_product_grade"],
        "date_col": "FDATE",
        "filter_col": "material_name",
    },
    "production_in": {
        "tables": ["erp_v_product_grade", "v2_erp_v_product_grade"],
        "date_col": "FDATE",
        "filter_col": "material_name",
    },
    "production_out": {
        "tables": ["erp_v_product_grade", "v2_erp_v_product_grade"],
        "date_col": "FDATE",
        "filter_col": "material_name",
    },
    "internal_transfer": {
        "tables": ["erp_v_stk_inventory"],
        "date_col": None,
        "filter_col": "material_name",
    },
    "market_listing_sinopec": {
        "tables": ["market_prices"],
        "date_col": "date",
        "filter_col": "product_id",
        "price_type": "sinopec_sh",
    },
    "market_spot_east_china": {
        "tables": ["market_prices"],
        "date_col": "date",
        "filter_col": "product_id",
        "price_type": "east_china",
    },
    "market_multi_product_wow": {
        "tables": ["market_prices"],
        "date_col": "date",
        "filter_col": "product_id",
        "price_type": "east_china",
    },
}


SOURCE_CATALOG: Dict[str, dict] = {
    "actual_price": {
        "intent": "actual_price",
        "domain": "erp_price",
        "label": "Internal ERP actual price",
        "default_operation": "average",
        "allowed_operations": ["raw", "latest", "average", "top_n"],
        "value_columns": ["price"],
        "sensitive_columns": ["customer_name", "contract_no"],
    },
    "forecast_price": {
        "intent": "forecast_price",
        "domain": "forecast",
        "label": "Model forecast price",
        "default_operation": "latest",
        "allowed_operations": ["raw", "latest", "average"],
        "value_columns": ["price"],
        "sensitive_columns": [],
    },
    "partner_forecast": {
        "intent": "partner_forecast",
        "domain": "forecast",
        "label": "Partner forecast price",
        "default_operation": "latest",
        "allowed_operations": ["raw", "latest", "average"],
        "value_columns": ["price"],
        "sensitive_columns": [],
    },
    "market_reference": {
        "intent": "market_reference",
        "domain": "market",
        "label": "Market reference price",
        "default_operation": "latest",
        "allowed_operations": ["raw", "latest", "average"],
        "value_columns": ["price"],
        "sensitive_columns": [],
    },
    "inventory": {
        "intent": "inventory",
        "domain": "erp_inventory",
        "label": "ERP inventory",
        "default_operation": "latest",
        "allowed_operations": ["raw", "latest", "sum", "top_n"],
        "value_columns": ["FBASEQTY"],
        "sensitive_columns": ["STOCKNAME"],
    },
    "sales": {
        "intent": "sales",
        "domain": "erp_sales",
        "label": "ERP sales orders",
        "default_operation": "sum",
        "allowed_operations": ["raw", "latest", "sum", "average", "top_n"],
        "value_columns": ["FDELIQTY"],
        "sensitive_columns": ["customer_name", "FCUSTID", "contract_no"],
    },
    "outbound_shipment": {
        "intent": "outbound_shipment",
        "domain": "erp_logistics",
        "label": "ERP outbound shipment",
        "default_operation": "sum",
        "allowed_operations": ["raw", "latest", "sum", "top_n"],
        "value_columns": ["FDELIQTY"],
        "sensitive_columns": ["customer_name", "FCUSTID"],
    },
    "inbound_receipt": {
        "intent": "inbound_receipt",
        "domain": "erp_logistics",
        "label": "ERP inbound receipt",
        "default_operation": "sum",
        "allowed_operations": ["raw", "latest", "sum", "top_n"],
        "value_columns": ["FREALQTY"],
        "sensitive_columns": ["FPAYERID", "supplier_name"],
    },
    "purchase_order": {
        "intent": "purchase_order",
        "domain": "erp_procurement",
        "label": "ERP purchase order and receipt",
        "default_operation": "sum",
        "allowed_operations": ["raw", "latest", "sum", "top_n"],
        "value_columns": ["PLAN_QTY"],
        "sensitive_columns": ["FPAYERID", "supplier_name"],
    },
    "contract": {
        "intent": "contract",
        "domain": "erp_contract",
        "label": "ERP contract execution",
        "default_operation": "latest",
        "allowed_operations": ["raw", "latest", "sum", "top_n"],
        "value_columns": ["FQTY"],
        "sensitive_columns": ["contract_no", "customer_name"],
    },
    "raw_material": {
        "intent": "raw_material",
        "domain": "erp_procurement",
        "label": "ERP raw material receipt",
        "default_operation": "sum",
        "allowed_operations": ["raw", "latest", "sum", "top_n"],
        "value_columns": ["FREALQTY"],
        "sensitive_columns": ["FPAYERID", "supplier_name"],
    },
    "production": {
        "intent": "production",
        "domain": "erp_production",
        "label": "ERP production",
        "default_operation": "sum",
        "allowed_operations": ["raw", "latest", "sum", "top_n"],
        "value_columns": ["realqty"],
        "sensitive_columns": [],
    },
    "production_in": {
        "intent": "production_in",
        "domain": "erp_production",
        "label": "ERP production in",
        "default_operation": "sum",
        "allowed_operations": ["raw", "latest", "sum", "top_n"],
        "value_columns": ["realqty"],
        "sensitive_columns": [],
    },
    "production_out": {
        "intent": "production_out",
        "domain": "erp_production",
        "label": "ERP production out",
        "default_operation": "sum",
        "allowed_operations": ["raw", "latest", "sum", "top_n"],
        "value_columns": ["realqty"],
        "sensitive_columns": [],
    },
    "internal_transfer": {
        "intent": "internal_transfer",
        "domain": "erp_inventory",
        "label": "ERP internal transfer/inventory movement",
        "default_operation": "latest",
        "allowed_operations": ["raw", "latest", "sum", "top_n"],
        "value_columns": ["FBASEQTY"],
        "sensitive_columns": ["STOCKNAME"],
    },
    "market_listing_sinopec": {
        "intent": "market_listing_sinopec",
        "domain": "market",
        "label": "SINOPEC Shanghai listing (market_prices)",
        "default_operation": "latest",
        "allowed_operations": ["raw", "latest"],
        "value_columns": ["price"],
        "sensitive_columns": [],
        "price_type": "sinopec_sh",
    },
    "market_spot_east_china": {
        "intent": "market_spot_east_china",
        "domain": "market",
        "label": "East China spot (market_prices)",
        "default_operation": "latest",
        "allowed_operations": ["raw", "latest"],
        "value_columns": ["price"],
        "sensitive_columns": [],
        "price_type": "east_china",
    },
    "market_multi_product_wow": {
        "intent": "market_multi_product_wow",
        "domain": "market",
        "label": "Multi-product week-over-week prices",
        "default_operation": "latest",
        "allowed_operations": ["raw", "latest"],
        "value_columns": ["price"],
        "sensitive_columns": [],
        "price_type": "east_china",
    },
}


def get_product_keys() -> List[str]:
    return sorted(PRODUCTS.keys())


def get_intent_keys() -> List[str]:
    return sorted(INTENTS.keys())


def get_source_keys() -> List[str]:
    return sorted(SOURCE_CATALOG.keys())


def get_source_spec(source_key: str) -> Optional[dict]:
    return SOURCE_CATALOG.get(str(source_key or ""))


def normalize_product_key(value: str) -> Optional[str]:
    key = str(value or "").strip()
    if not key:
        return None
    if key in PRODUCTS:
        return key
    mapped = DOMAIN_PRODUCT_TO_PLANNER_KEY.get(key)
    if mapped in PRODUCTS:
        return mapped
    return None


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").lower())


def resolve_product(text: str) -> Optional[str]:
    direct = normalize_product_key(text)
    if direct:
        return direct

    if resolve_product_id is not None:
        try:
            resolved = resolve_product_id(text)
            mapped = normalize_product_key(str(resolved or ""))
            if mapped:
                return mapped
        except Exception:
            pass

    normalized = _normalize_text(text)
    if not normalized:
        return None

    for product_key, spec in PRODUCTS.items():
        for alias in spec.get("aliases", []):
            alias_norm = _normalize_text(alias)
            if alias_norm and alias_norm in normalized:
                return product_key
    return None


def normalize_source_operation(raw: dict, default_product: Optional[str] = None) -> Optional[dict]:
    source = str(raw.get("source") or raw.get("intent") or "").strip()
    if source not in SOURCE_CATALOG:
        return None

    product = normalize_product_key(str(raw.get("product") or default_product or ""))
    if not product:
        return None

    spec = SOURCE_CATALOG[source]
    operation = str(raw.get("operation") or spec.get("default_operation") or "raw").strip().lower()
    allowed_operations = set(spec.get("allowed_operations") or [])
    if operation not in allowed_operations:
        operation = str(spec.get("default_operation") or "raw")

    return {
        "source": source,
        "intent": str(spec.get("intent") or source),
        "product": product,
        "operation": operation,
        "metric": str(raw.get("metric") or "").strip(),
        "group_by": str(raw.get("group_by") or "").strip(),
        "reason": str(raw.get("reason") or "").strip(),
    }


def build_source_operations(products: List[str], intents: List[str]) -> List[dict]:
    operations: List[dict] = []
    for product_key in products:
        product = normalize_product_key(product_key)
        if not product:
            continue
        for intent_key in intents:
            if intent_key not in SOURCE_CATALOG:
                continue
            spec = SOURCE_CATALOG[intent_key]
            operations.append(
                {
                    "source": intent_key,
                    "intent": str(spec.get("intent") or intent_key),
                    "product": product,
                    "operation": str(spec.get("default_operation") or "raw"),
                    "metric": "",
                    "group_by": "",
                    "reason": "derived_from_intent",
                }
            )
    return operations


def get_tables_for_plan(product_key: str, intent_key: str) -> List[str]:
    product = PRODUCTS.get(product_key)
    intent = INTENTS.get(intent_key)
    if not product or not intent:
        return []

    tables: List[str] = []
    for table in intent.get("tables", []):
        if table == "<lz_table>":
            lz_table = product.get("lz_table")
            if lz_table:
                tables.append(str(lz_table))
            continue
        tables.append(str(table))
    return tables
