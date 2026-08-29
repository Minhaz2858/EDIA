"""Execute focused SQL queries generated from planner output."""

from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

from sqlalchemy import text

from backend.src.db.sqlite_db import db
from backend.src.planner.query_planner import QueryPlan
from backend.src.planner.schema_registry import (
    INTENTS,
    PRODUCTS,
    SOURCE_CATALOG,
    get_tables_for_plan,
    normalize_source_operation,
)


MAX_ROWS_PER_QUERY = max(10, int(os.getenv("PLANNER_MAX_ROWS_PER_QUERY", "200")))
MAX_WORKERS = max(1, int(os.getenv("PLANNER_SQL_PARALLELISM", "4")))


def _safe_ident(value: str) -> Optional[str]:
    v = str(value or "").strip()
    if not v:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_:\-\u4e00-\u9fff]+", v):
        return None
    return v


_LOGISTICS_SELECT: dict[str, str] = {
    # Inbound receipt: receipt date, material, quantity, supplier ref
    "inbound_receipt": (
        "SELECT FDATE AS receipt_date, material_name, "
        "COALESCE(FREALQTY, FBASEQTY, 0) AS qty_ton, "
        "FUNITID AS unit, FPAYERID AS supplier_id "
        "FROM `{table}` "
        "WHERE material_name = :product_name "
        "AND FDATE BETWEEN :start_date AND :end_date "
        "ORDER BY FDATE DESC LIMIT :limit_rows"
    ),
    # Purchase order: same view (erp_v_raw_material_receiving) with plan vs actual
    "purchase_order": (
        "SELECT FDATE AS po_date, material_name, "
        "COALESCE(PLAN_QTY, 0) AS planned_qty, "
        "COALESCE(FREALQTY, 0) AS received_qty, "
        "COALESCE(FREALQTY, 0) - COALESCE(PLAN_QTY, 0) AS qty_variance "
        "FROM `{table}` "
        "WHERE material_name = :product_name "
        "AND FDATE BETWEEN :start_date AND :end_date "
        "ORDER BY FDATE DESC LIMIT :limit_rows"
    ),
    # Outbound shipment: delivery plan date, quantity, price, customer
    "outbound_shipment": (
        "SELECT DATE(PLANDATE) AS ship_date, material_name, "
        "COALESCE(FDELIQTY, 0) AS shipped_qty, "
        "COALESCE(FTAXPRICE, FPRICE, 0) AS unit_price, "
        "COALESCE(FREMAINOUTQTY, 0) AS remaining_qty "
        "FROM `{table}` "
        "WHERE material_name = :product_name "
        "AND PLANDATE IS NOT NULL "
        "AND DATE(PLANDATE) BETWEEN :start_date AND :end_date "
        "ORDER BY PLANDATE DESC LIMIT :limit_rows"
    ),
    # Production in/out: production date, grade, actual vs plan quantities
    "production_in": (
        "SELECT FDATE AS prod_date, material_name, product_grade, "
        "COALESCE(realqty, 0) AS produced_qty "
        "FROM `{table}` "
        "WHERE material_name = :product_name "
        "AND FDATE BETWEEN :start_date AND :end_date "
        "ORDER BY FDATE DESC LIMIT :limit_rows"
    ),
    "production_out": (
        "SELECT FDATE AS prod_date, material_name, product_grade, "
        "COALESCE(realqty, 0) AS produced_qty "
        "FROM `{table}` "
        "WHERE material_name = :product_name "
        "AND FDATE BETWEEN :start_date AND :end_date "
        "ORDER BY FDATE DESC LIMIT :limit_rows"
    ),
    # Internal transfer: current inventory snapshot with in/out dates
    "internal_transfer": (
        "SELECT material_name, "
        "COALESCE(FBASEQTY, 0) AS stock_qty, "
        "IN_DATE AS last_in_date, OUT_DATE AS last_out_date, "
        "STOCKNAME AS warehouse "
        "FROM `{table}` "
        "WHERE material_name = :product_name "
        "LIMIT :limit_rows"
    ),
}


def build_sql(
    table: str,
    intent_key: str,
    product_key: str,
    time_range: dict,
) -> tuple[str, dict] | None:
    safe_table = _safe_ident(table)
    if not safe_table:
        return None

    intent = INTENTS.get(intent_key)
    product = PRODUCTS.get(product_key)
    if not intent or not product:
        return None

    # Use intent-specific SQL template when available for richer logistics output.
    if intent_key in _LOGISTICS_SELECT:
        template = _LOGISTICS_SELECT[intent_key]
        sql = template.format(table=safe_table)
        params: dict[str, Any] = {
            "product_name": product.get("chinese_name"),
            "limit_rows": MAX_ROWS_PER_QUERY,
        }
        if "{table}" not in template:
            pass  # already formatted
        if "start_date" in sql:
            params["start_date"] = str(time_range.get("start", ""))
            params["end_date"] = str(time_range.get("end", ""))
        return sql, params

    date_col = intent.get("date_col")
    filter_col = intent.get("filter_col")

    where = []
    params = {"limit_rows": MAX_ROWS_PER_QUERY}

    if filter_col:
        where.append(f"`{filter_col}` = :product_name")
        params["product_name"] = product.get("chinese_name")

    if date_col and time_range.get("start") and time_range.get("end"):
        where.append(f"`{date_col}` BETWEEN :start_date AND :end_date")
        params["start_date"] = str(time_range["start"])
        params["end_date"] = str(time_range["end"])

    where_sql = f" WHERE {' AND '.join(where)}" if where else ""
    order_sql = f" ORDER BY `{date_col}` DESC" if date_col else ""

    sql = (
        f"SELECT * FROM `{safe_table}`"
        f"{where_sql}"
        f"{order_sql}"
        " LIMIT :limit_rows"
    )
    return sql, params


def _operation_value_expression(source_key: str) -> str:
    spec = SOURCE_CATALOG.get(source_key) or {}
    columns = []
    for value in list(spec.get("value_columns") or []):
        safe_col = _safe_ident(str(value))
        if safe_col:
            columns.append(safe_col)
    if not columns:
        return "1"
    return "COALESCE(" + ", ".join(f"`{col}`" for col in columns) + ", 0)"


def build_operation_sql(operation: dict, time_range: dict) -> list[tuple[str, str, str, str, dict]]:
    normalized = normalize_source_operation(operation)
    if not normalized:
        return []

    source_key = normalized["source"]
    intent_key = normalized["intent"]
    product_key = normalized["product"]
    op = normalized["operation"]

    product = PRODUCTS.get(product_key)
    intent = INTENTS.get(intent_key)
    if not product or not intent:
        return []

    tasks: list[tuple[str, str, str, str, dict]] = []
    for table in get_tables_for_plan(product_key, intent_key):
        safe_table = _safe_ident(table)
        if not safe_table:
            continue

        if op == "raw":
            built = build_sql(table, intent_key, product_key, time_range)
            if built:
                sql, params = built
                tasks.append((product_key, intent_key, table, sql, params))
            continue

        date_col = _safe_ident(str(intent.get("date_col") or ""))
        filter_col = _safe_ident(str(intent.get("filter_col") or ""))
        value_expr = _operation_value_expression(source_key)
        params: dict[str, Any] = {"limit_rows": MAX_ROWS_PER_QUERY}
        where = []

        if filter_col:
            where.append(f"`{filter_col}` = :product_name")
            params["product_name"] = product.get("chinese_name")
        if date_col and time_range.get("start") and time_range.get("end"):
            where.append(f"`{date_col}` BETWEEN :start_date AND :end_date")
            params["start_date"] = str(time_range["start"])
            params["end_date"] = str(time_range["end"])

        where_sql = f" WHERE {' AND '.join(where)}" if where else ""
        order_sql = f" ORDER BY `{date_col}` DESC" if date_col else ""

        if op == "latest":
            sql = f"SELECT * FROM `{safe_table}`{where_sql}{order_sql} LIMIT :limit_rows"
        elif op == "average":
            latest_expr = f"MAX(`{date_col}`)" if date_col else "NULL"
            sql = (
                f"SELECT COUNT(*) AS row_count, {latest_expr} AS latest_date, "
                f"AVG({value_expr}) AS avg_value, MIN({value_expr}) AS min_value, "
                f"MAX({value_expr}) AS max_value FROM `{safe_table}`{where_sql}"
            )
        elif op == "sum":
            latest_expr = f"MAX(`{date_col}`)" if date_col else "NULL"
            sql = (
                f"SELECT COUNT(*) AS row_count, {latest_expr} AS latest_date, "
                f"SUM({value_expr}) AS total_value, AVG({value_expr}) AS avg_value "
                f"FROM `{safe_table}`{where_sql}"
            )
        elif op == "top_n":
            sql = f"SELECT * FROM `{safe_table}`{where_sql} ORDER BY {value_expr} DESC LIMIT :limit_rows"
        else:
            continue

        tasks.append((product_key, intent_key, table, sql, params))

    return tasks


def _run_one(sql: str, params: dict) -> list[dict]:
    with db.engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
        return [dict(r._mapping) for r in rows]


def execute_plan(plan: QueryPlan) -> dict:
    tasks: list[tuple[str, str, str, str, dict]] = []
    for operation in list(plan.get("source_operations") or []):
        tasks.extend(build_operation_sql(operation, plan.get("time_range", {})))

    if not tasks:
        for product_key in plan.get("products", []):
            for intent_key in plan.get("intents", []):
                tables = get_tables_for_plan(product_key, intent_key)
                for table in tables:
                    built = build_sql(
                        table=table,
                        intent_key=intent_key,
                        product_key=product_key,
                        time_range=plan.get("time_range", {}),
                    )
                    if not built:
                        continue
                    sql, params = built
                    tasks.append((product_key, intent_key, table, sql, params))

    out_rows: list[dict] = []
    errors: list[dict] = []

    if not tasks:
        return {"plan": plan, "rows": out_rows, "errors": errors, "data_sources": []}

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(tasks))) as executor:
        futures = {
            executor.submit(_run_one, sql, params): (product_key, intent_key, table)
            for product_key, intent_key, table, sql, params in tasks
        }
        for fut in as_completed(futures):
            product_key, intent_key, table = futures[fut]
            try:
                rows = fut.result()
                out_rows.append(
                    {
                        "product": product_key,
                        "intent": intent_key,
                        "table": table,
                        "row_count": len(rows),
                        "rows": rows,
                    }
                )
            except Exception as exc:
                errors.append(
                    {
                        "product": product_key,
                        "intent": intent_key,
                        "table": table,
                        "error": str(exc),
                    }
                )

    data_sources = sorted({f"{r['intent']}:{r['table']}" for r in out_rows if r.get("row_count", 0) > 0})
    return {
        "plan": plan,
        "rows": out_rows,
        "errors": errors,
        "data_sources": data_sources,
    }
