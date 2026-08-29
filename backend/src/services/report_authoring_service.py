"""
Professional report authoring service with smart templates and visual configuration.

This service provides enterprise-grade report authoring capabilities:
- Template gallery with pre-built reports
- Visual query builder (no SQL needed)
- AI-powered block suggestions
- Live data preview
- Smart field mapping
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta, date
import json
from sqlalchemy import text
from backend.src.db.sqlite_db import db

logger = logging.getLogger(__name__)


class ReportAuthoringService:
    """
    Enhanced report authoring with smart features.
    """
    
    def __init__(self, db_connection=None):
        self.db = db_connection
        
    def get_template_gallery(self, user_id: Optional[str] = None, user_role: str = None) -> List[Dict[str, Any]]:
        """
        Get pre-built report templates based on user role.
        
        Returns templates with:
        - Preview thumbnail
        - Block structure
        - Required data sources
        - Estimated generation time
        """
        templates = [
            {
                "id": "weekly_market_report",
                "name": "周市场价格报告",
                "name_en": "Weekly Market Price Report",
                "description": "Regional petrochemical prices with trend analysis",
                "category": "market_analysis",
                "thumbnail": "/logo2.png",
                "estimated_time": "2 minutes",
                "blocks": [
                    {
                        "type": "kpi_cards",
                        "title": "关键指标",
                        "config": {
                            "products": ["crude_oil", "naphtha"],
                            "date_range": {
                                "type": "relative",
                                "relative": {"value": 30, "unit": "days"},
                            },
                        }
                    },
                    {
                        "type": "chart",
                        "title": "上游价格走势",
                        "config": {
                            "chart_type": "line",
                            "products": ["crude_oil", "naphtha"],
                            "date_range": {
                                "type": "relative",
                                "relative": {"value": 30, "unit": "days"},
                            },
                        }
                    },
                    {
                        "type": "data_table",
                        "title": "区域价格表",
                        "config": {
                            "products": ["isoprene", "piperylene"],
                            "regions": ["east_china", "sinopec_sh"],
                            "date_range": {
                                "type": "relative",
                                "relative": {"value": 30, "unit": "days"},
                            },
                        }
                    }
                ],
                "required_data": ["market_prices", "oil_benchmarks"],
                "tags": ["weekly", "pricing", "market"]
            },
            {
                "id": "customer_sales_report",
                "name": "客户销售报告",
                "name_en": "Customer Sales Report",
                "description": "Downstream product pricing snapshot for commercial teams",
                "category": "sales",
                "thumbnail": "/logo2.png",
                "estimated_time": "3 minutes",
                "blocks": [
                    {
                        "type": "kpi_cards",
                        "title": "销售概况",
                        "config": {
                            "products": ["sis", "c5_resin"],
                            "date_range": {
                                "type": "relative",
                                "relative": {"value": 30, "unit": "days"},
                            },
                        }
                    },
                    {
                        "type": "data_table",
                        "title": "客户销售明细",
                        "config": {
                            "products": ["sis", "c5_resin"],
                            "regions": ["sinopec_sh", "east_china"],
                            "date_range": {
                                "type": "relative",
                                "relative": {"value": 30, "unit": "days"},
                            },
                        }
                    },
                    {
                        "type": "text",
                        "title": "销售备注",
                        "config": {
                            "content": "Add customer commentary and commercial notes here."
                        }
                    }
                ],
                "required_data": ["market_prices"],
                "tags": ["sales", "customer", "monthly"]
            },
            {
                "id": "inventory_alert_report",
                "name": "库存预警报告",
                "name_en": "Inventory Alert Report",
                "description": "Feedstock and midstream price movements for operational monitoring",
                "category": "operations",
                "thumbnail": "/logo2.png",
                "estimated_time": "1 minute",
                "blocks": [
                    {
                        "type": "kpi_cards",
                        "title": "库存状态",
                        "config": {
                            "products": ["cracked_c5", "dcpd"],
                            "date_range": {
                                "type": "relative",
                                "relative": {"value": 30, "unit": "days"},
                            },
                        }
                    },
                    {
                        "type": "data_table",
                        "title": "低库存产品",
                        "config": {
                            "products": ["cracked_c5"],
                            "regions": ["yangzi", "maoming"],
                            "date_range": {
                                "type": "relative",
                                "relative": {"value": 30, "unit": "days"},
                            },
                        }
                    },
                    {
                        "type": "chart",
                        "title": "原料价格走势",
                        "config": {
                            "chart_type": "line",
                            "products": ["cracked_c5", "dcpd"],
                            "date_range": {
                                "type": "relative",
                                "relative": {"value": 90, "unit": "days"},
                            },
                        }
                    }
                ],
                "required_data": ["market_prices"],
                "tags": ["inventory", "alerts", "daily"]
            },
            {
                "id": "executive_dashboard",
                "name": "高管决策看板",
                "name_en": "Executive Dashboard",
                "description": "High-level KPIs and strategic insights",
                "category": "executive",
                "thumbnail": "/logo2.png",
                "estimated_time": "5 minutes",
                "blocks": [
                    {
                        "type": "kpi_cards",
                        "title": "核心指标",
                        "config": {
                            "products": ["crude_oil", "isoprene", "sis"],
                            "date_range": {
                                "type": "relative",
                                "relative": {"value": 30, "unit": "days"},
                            },
                        }
                    },
                    {
                        "type": "chart",
                        "title": "收入趋势",
                        "config": {
                            "chart_type": "line",
                            "products": ["isoprene", "dcpd"],
                            "date_range": {
                                "type": "relative",
                                "relative": {"value": 90, "unit": "days"},
                            },
                        }
                    },
                    {
                        "type": "data_table",
                        "title": "关键风险",
                        "config": {
                            "products": ["isoprene", "dcpd"],
                            "regions": ["east_china", "wuhan"],
                            "date_range": {
                                "type": "relative",
                                "relative": {"value": 30, "unit": "days"},
                            },
                        }
                    }
                ],
                "required_data": ["market_prices"],
                "tags": ["executive", "dashboard", "daily"]
            }
        ]

        if self.db is not None:
            self.db.ensure_system_report_templates(templates)
            stored_templates = self.db.list_report_gallery_templates(user_id)
            seed_lookup = {item["id"]: item for item in templates}
            gallery = []
            for row in stored_templates:
                source_template = seed_lookup.get(str(row.get("id") or ""), {})
                blocks = []
                for block in row.get("template_blocks") or []:
                    if not isinstance(block, dict):
                        continue
                    blocks.append(
                        {
                            "type": str(block.get("type") or "text"),
                            "title": str(block.get("title") or ""),
                            "config": dict(block.get("definition") or {}),
                        }
                    )
                gallery.append(
                    {
                        "id": str(row.get("id") or ""),
                        "name": str(row.get("name") or ""),
                        "name_en": str(source_template.get("name_en") or row.get("name") or ""),
                        "description": str(
                            row.get("description")
                            or source_template.get("description")
                            or ("Saved custom report template" if not row.get("is_system") else "")
                        ),
                        "category": str(row.get("category") or ("saved_templates" if not row.get("is_system") else "general")),
                        "thumbnail": str(row.get("thumbnail") or "/logo2.png"),
                        "estimated_time": str(row.get("estimated_time") or "1 minute"),
                        "blocks": blocks,
                        "tags": [str(tag) for tag in (row.get("tags") or [row.get("time_period") or "custom"])],
                        "is_user_template": not bool(row.get("is_system")),
                        "is_system_template": bool(row.get("is_system")),
                        "is_pinned": bool(row.get("is_pinned")),
                    }
                )
            return gallery

        return templates
    
    def get_available_data_sources(self) -> List[Dict[str, Any]]:
        """
        List all available data sources with metadata.
        
        Users can browse and select from these when configuring blocks.
        """
        return [
            {
                "id": "market_prices",
                "name": "市场价格",
                "name_en": "Market Prices",
                "description": "Regional petrochemical product prices",
                "type": "table",
                "schema": {
                    "date": {"type": "date", "label": "日期"},
                    "region": {"type": "string", "label": "地区"},
                    "company": {"type": "string", "label": "生产企业"},
                    "product": {"type": "string", "label": "产品"},
                    "grade_92": {"type": "number", "label": "92#汽油", "unit": "元/吨"},
                    "grade_95": {"type": "number", "label": "95#汽油", "unit": "元/吨"},
                    "grade_98": {"type": "number", "label": "98#汽油", "unit": "元/吨"},
                    "grade_0": {"type": "number", "label": "0#柴油", "unit": "元/吨"},
                    "delta_92": {"type": "number", "label": "92#涨跌"},
                    "delta_95": {"type": "number", "label": "95#涨跌"}
                },
                "sample_count": 1500,
                "last_updated": "2026-04-24 14:00"
            },
            {
                "id": "oil_benchmarks",
                "name": "国际油价",
                "name_en": "Oil Benchmarks",
                "description": "WTI, Brent, INE crude oil prices",
                "type": "table",
                "schema": {
                    "date": {"type": "date", "label": "日期"},
                    "wti_price": {"type": "number", "label": "WTI", "unit": "USD/bbl"},
                    "brent_price": {"type": "number", "label": "Brent", "unit": "USD/bbl"},
                    "ine_price": {"type": "number", "label": "INE", "unit": "CNY/bbl"},
                    "wti_change": {"type": "number", "label": "WTI变化"},
                    "brent_change": {"type": "number", "label": "Brent变化"}
                },
                "sample_count": 365,
                "last_updated": "2026-04-24 14:00"
            },
            {
                "id": "customer_sales",
                "name": "客户销售",
                "name_en": "Customer Sales",
                "description": "Sales transactions by customer",
                "type": "table",
                "schema": {
                    "date": {"type": "date", "label": "日期"},
                    "customer": {"type": "string", "label": "客户"},
                    "product": {"type": "string", "label": "产品"},
                    "volume": {"type": "number", "label": "销量", "unit": "吨"},
                    "revenue": {"type": "number", "label": "收入", "unit": "元"},
                    "price": {"type": "number", "label": "单价", "unit": "元/吨"}
                },
                "sample_count": 5000,
                "last_updated": "2026-04-24 10:00"
            },
            {
                "id": "inventory",
                "name": "库存",
                "name_en": "Inventory",
                "description": "Current stock levels",
                "type": "table",
                "schema": {
                    "product": {"type": "string", "label": "产品"},
                    "current_stock": {"type": "number", "label": "当前库存", "unit": "吨"},
                    "reorder_point": {"type": "number", "label": "补货点", "unit": "吨"},
                    "max_stock": {"type": "number", "label": "最大库存", "unit": "吨"},
                    "days_of_supply": {"type": "number", "label": "可用天数"}
                },
                "sample_count": 50,
                "last_updated": "2026-04-24 08:00"
            },
            {
                "id": "inbound_receipts",
                "name": "入库记录",
                "name_en": "Inbound Receipts",
                "description": "Raw-material goods receipts from suppliers (ERP inbound)",
                "type": "table",
                "schema": {
                    "receipt_date": {"type": "date", "label": "入库日期"},
                    "material_name": {"type": "string", "label": "物料名称"},
                    "planned_qty": {"type": "number", "label": "计划量", "unit": "吨"},
                    "received_qty": {"type": "number", "label": "实收量", "unit": "吨"},
                    "qty_variance": {"type": "number", "label": "差异量", "unit": "吨"}
                },
                "sample_count": 500,
                "last_updated": "live"
            },
            {
                "id": "outbound_shipments",
                "name": "出库记录",
                "name_en": "Outbound Shipments",
                "description": "Customer delivery shipments from ERP sales order view",
                "type": "table",
                "schema": {
                    "ship_date": {"type": "date", "label": "出库日期"},
                    "material_name": {"type": "string", "label": "物料名称"},
                    "shipped_qty": {"type": "number", "label": "已发量", "unit": "吨"},
                    "remaining_qty": {"type": "number", "label": "未发量", "unit": "吨"},
                    "avg_price": {"type": "number", "label": "均价", "unit": "元/吨"}
                },
                "sample_count": 2000,
                "last_updated": "live"
            },
            {
                "id": "production_summary",
                "name": "生产汇总",
                "name_en": "Production Summary",
                "description": "Planned vs actual production quantities by product and grade",
                "type": "table",
                "schema": {
                    "prod_date": {"type": "date", "label": "生产日期"},
                    "material_name": {"type": "string", "label": "物料名称"},
                    "product_grade": {"type": "string", "label": "牌号"},
                    "produced_qty": {"type": "number", "label": "产量", "unit": "吨"}
                },
                "sample_count": 300,
                "last_updated": "live"
            },
            {
                "id": "internal_transfers",
                "name": "调拨记录",
                "name_en": "Internal Transfers",
                "description": "Warehouse-to-warehouse stock transfer and current inventory snapshot",
                "type": "table",
                "schema": {
                    "material_name": {"type": "string", "label": "物料名称"},
                    "stock_qty": {"type": "number", "label": "库存量", "unit": "吨"},
                    "warehouse": {"type": "string", "label": "仓库"},
                    "last_in_date": {"type": "date", "label": "最近入库日"},
                    "last_out_date": {"type": "date", "label": "最近出库日"}
                },
                "sample_count": 100,
                "last_updated": "live"
            }
        ]
    
    def build_visual_query(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert visual configuration to SQL query.
        
        Input config:
        {
            "data_source": "market_prices",
            "columns": ["region", "company", "grade_92", "delta_92"],
            "filters": {
                "date": "latest",
                "region": ["东营", "淄博"]
            },
            "sort": {"column": "grade_92", "order": "desc"},
            "limit": 100
        }
        
        Returns:
        {
            "query": "SELECT ... FROM ... WHERE ...",
            "params": {...},
            "estimated_rows": 50
        }
        """
        data_source = config.get("data_source")
        columns = config.get("columns", ["*"])
        filters = config.get("filters", {})
        sort = config.get("sort")
        limit = config.get("limit")
        
        # Build SELECT clause
        if columns == ["*"]:
            select_clause = "*"
        else:
            select_clause = ", ".join(columns)
        
        # Build WHERE clause
        where_clauses = []
        params = {}
        
        for field, value in filters.items():
            if value == "latest":
                where_clauses.append(f"{field} = (SELECT MAX({field}) FROM {data_source})")
            elif isinstance(value, list):
                placeholders = ", ".join([f":{field}_{i}" for i in range(len(value))])
                where_clauses.append(f"{field} IN ({placeholders})")
                for i, v in enumerate(value):
                    params[f"{field}_{i}"] = v
            else:
                where_clauses.append(f"{field} = :{field}")
                params[field] = value
        
        where_clause = " AND ".join(where_clauses) if where_clauses else "1=1"
        
        # Build ORDER BY clause
        order_clause = ""
        if sort:
            order_direction = sort.get("order", "asc").upper()
            order_clause = f" ORDER BY {sort['column']} {order_direction}"
        
        # Build LIMIT clause
        limit_clause = f" LIMIT {limit}" if limit else ""
        
        # Assemble query
        query = f"SELECT {select_clause} FROM {data_source} WHERE {where_clause}{order_clause}{limit_clause}"
        
        return {
            "query": query,
            "params": params,
            "estimated_rows": self._estimate_row_count(data_source, filters)
        }
    
    def _estimate_row_count(self, data_source: str, filters: Dict[str, Any]) -> int:
        """Estimate how many rows a query will return"""
        # This would query the database for a count
        # For now, return a mock estimate
        base_counts = {
            "market_prices": 1500,
            "oil_benchmarks": 365,
            "customer_sales": 5000,
            "inventory": 50,
            "inbound_receipts": 500,
            "outbound_shipments": 2000,
            "production_summary": 300,
            "internal_transfers": 100,
        }
        
        count = base_counts.get(data_source, 100)
        
        # Apply filter reduction estimates
        if filters.get("date") == "latest":
            count = count // 30  # Reduce to ~latest month
        if "region" in filters and isinstance(filters["region"], list):
            count = count * len(filters["region"]) // 10
        
        return max(1, count)
    
    def preview_block_data(self, block_config: Dict[str, Any], limit: int = 10) -> Dict[str, Any]:
        """
        Preview data for a block configuration with REAL data from database.
        
        Returns actual data to show users before they add the block.
        """
        from backend.src.service.market_data_service import market_data
        
        block_type = block_config.get("type")
        config = block_config.get("config", {})
        
        # Helper function to parse frontend date range format
        def parse_date_range(date_range: dict) -> tuple:
            """Convert frontend date_range format to (start_date, end_date) tuple."""
            end_date = datetime.now().date()
            start_date = end_date - timedelta(days=30)  # Default to last 30 days
            
            if not date_range:
                return (start_date, end_date)
            
            # Handle "type": "relative" with "relative": {value, unit}
            if date_range.get("type") == "relative" and "relative" in date_range:
                rel = date_range["relative"]
                value = rel.get("value", 0)
                unit = rel.get("unit", "days")
                
                if value == 0 and unit == "days":
                    # Today only
                    start_date = end_date
                elif unit == "days":
                    start_date = end_date - timedelta(days=value)
                elif unit == "weeks":
                    start_date = end_date - timedelta(weeks=value)
                elif unit == "months":
                    start_date = end_date - timedelta(days=value * 30)  # Approximate
            
            # Handle "type": "range" with start/end dates
            elif date_range.get("type") == "range":
                if "start" in date_range and date_range["start"]:
                    start_date = datetime.fromisoformat(date_range["start"]).date()
                if "end" in date_range and date_range["end"]:
                    end_date = datetime.fromisoformat(date_range["end"]).date()
            
            # Legacy format: "preset" field (backward compatibility)
            elif "preset" in date_range:
                if date_range["preset"] == "today":
                    start_date = end_date
                elif date_range["preset"] == "last_7_days":
                    start_date = end_date - timedelta(days=7)
                elif date_range["preset"] == "last_30_days":
                    start_date = end_date - timedelta(days=30)
                elif date_range["preset"] == "last_90_days":
                    start_date = end_date - timedelta(days=90)
            
            # Legacy format: direct start_date/end_date fields
            elif "start_date" in date_range and "end_date" in date_range:
                start_date = datetime.fromisoformat(date_range["start_date"]).date()
                end_date = datetime.fromisoformat(date_range["end_date"]).date()
            
            return (start_date, end_date)
        
        if block_type == "chart":
            products = config.get("products", [])
            date_range = config.get("date_range", {})
            chart_type = config.get("chart_type", "line")
            
            # Parse date range using helper function
            start_date, end_date = parse_date_range(date_range)
            
            # Fetch real data for each product
            series_data = []
            
            for product_id in products:
                price_history = market_data.get_price_history(
                    product_id, cutoff_date=end_date.isoformat()
                )
                
                # Filter for date range
                if price_history:
                    filtered_history = [
                        point for point in price_history
                        if start_date.isoformat() <= point["date"] <= end_date.isoformat()
                    ]
                    
                    if filtered_history:
                        series_data.append({
                            "name": self._get_product_display_name(product_id),
                            "data": [
                                {
                                    "date": point["date"],
                                    "value": point["price"]
                                }
                                for point in filtered_history
                            ]
                        })
            
            return {
                "type": "chart",
                "chart_type": chart_type,
                "title": config.get("title", "Price Trend"),
                "series": series_data,
                "preview": True
            }
        
        elif block_type == "kpi_cards":
            metrics = config.get("metrics", [])
            products = config.get("products", [])
            date_range = config.get("date_range", {})

            # Use the selected date range instead of hardcoded 7 days
            start_date, end_date = parse_date_range(date_range)
            period_label = f"{start_date.isoformat()} ~ {end_date.isoformat()}"

            # Build a human-friendly comparison label reflecting the chosen period
            def build_comparison_labels(date_range: dict) -> tuple:
                """Return (zh_label, en_label) describing what the % change is relative to."""
                if date_range.get("type") == "relative" and "relative" in date_range:
                    rel = date_range["relative"]
                    value = rel.get("value", 0)
                    unit = rel.get("unit", "days")
                    if value == 0:
                        return "较今日", "vs today"
                    unit_zh = {"days": "日", "weeks": "周", "months": "月"}.get(unit, unit)
                    unit_en = {"days": "days", "weeks": "weeks", "months": "months"}.get(unit, unit)
                    return f"较{value}{unit_zh}前", f"vs {value} {unit_en} ago"
                # Legacy preset fallback
                preset = date_range.get("preset", "")
                preset_map = {
                    "today":        ("较今日", "vs today"),
                    "last_7_days":  ("较7日前", "vs 7 days ago"),
                    "last_30_days": ("较30日前", "vs 30 days ago"),
                    "last_90_days": ("较90日前", "vs 90 days ago"),
                }
                return preset_map.get(preset, ("较期初", "vs period start"))

            comparison_label_zh, comparison_label_en = build_comparison_labels(date_range)

            cards_data = []

            for product_id in products:
                try:
                    price_history = market_data.get_price_history(
                        product_id, cutoff_date=end_date.isoformat()
                    )
                    filtered_history = [
                        point for point in (price_history or [])
                        if start_date.isoformat() <= str(point.get("date", "")) <= end_date.isoformat()
                        and point.get("price") is not None
                    ]

                    # Sparse market series often have no exact value on the requested
                    # start or end boundary. Use the first and last observed prices
                    # inside the selected period instead of defaulting to 0.0%.
                    if filtered_history:
                        filtered_history.sort(key=lambda point: str(point.get("date", "")))
                        start_price = float(filtered_history[0]["price"])
                        latest_price = float(filtered_history[-1]["price"])
                        period_prices = [float(point["price"]) for point in filtered_history]
                    else:
                        latest_price = market_data.get_price_on_date(product_id, end_date.isoformat())
                        start_price = market_data.get_price_on_date(product_id, start_date.isoformat())
                        period_prices = []

                    if latest_price is not None:
                        latest_price = float(latest_price)
                        start_price_value = float(start_price) if start_price not in (None, 0) else None

                        if start_price_value and start_price_value > 0:
                            change_pct = ((latest_price - start_price_value) / start_price_value) * 100
                        else:
                            change_pct = 0

                        if change_pct > 0:
                            trend = "up"
                        elif change_pct < 0:
                            trend = "down"
                        else:
                            trend = "neutral"

                        product_profile = self._get_product_profile(product_id)

                        card = {
                            "metric": product_id,
                            "product_id": product_id,
                            "label": product_profile["zh"],
                            "label_en": product_profile["en"],
                            "unit": product_profile["unit_zh"],
                            "unit_en": product_profile["unit_en"],
                            "unit_zh": product_profile["unit_zh"],
                            "value": latest_price,
                            "value_display": self._format_kpi_value(latest_price),
                            "trend": trend,
                            "change": f"{'+' if change_pct > 0 else ''}{change_pct:.1f}%",
                            "change_value": round(change_pct, 1),
                            "comparison_label": comparison_label_zh,
                            "comparison_label_en": comparison_label_en,
                        }

                        if period_prices:
                            card["high"] = max(period_prices)
                            card["low"] = min(period_prices)
                            card["high_display"] = self._format_kpi_value(card["high"])
                            card["low_display"] = self._format_kpi_value(card["low"])

                        cards_data.append(card)
                except Exception as e:
                    logger.error(f"Error fetching KPI for {product_id}: {e}")
                    continue

            return {
                "type": "kpi_cards",
                "title": config.get("title", "关键指标"),
                "period_label": period_label,
                "data": cards_data,
                "preview": True
            }
        
        elif block_type == "data_table":
            products = config.get("products", [])
            date_range = config.get("date_range", {})
            regions = config.get("regions", [])  # Optional region filter
            data_source = config.get("data_source", "market_prices")

            # Parse date range using helper function
            start_date, end_date = parse_date_range(date_range)

            # --- Logistics data sources: fetch from ERP tools ---
            if data_source in ("inbound_receipts", "outbound_shipments", "production_summary", "internal_transfers"):
                from backend.src.tools.erp_tools import (
                    query_inbound_receipts,
                    query_outbound_shipments,
                    query_production_summary,
                    query_inventory_levels,
                )
                period_str = f"{start_date.isoformat()} to {end_date.isoformat()}"
                product_id = products[0] if products else None
                if data_source == "inbound_receipts":
                    raw_text = query_inbound_receipts(product_id, period=period_str)
                    col_keys = ["receipt_date", "material_name", "planned_qty", "received_qty", "qty_variance"]
                    col_labels = ["入库日期", "物料名称", "计划量(吨)", "实收量(吨)", "差异(吨)"]
                elif data_source == "outbound_shipments":
                    raw_text = query_outbound_shipments(product_id, period=period_str)
                    col_keys = ["ship_date", "material_name", "shipped_qty", "remaining_qty", "avg_price"]
                    col_labels = ["出库日期", "物料名称", "已发量(吨)", "未发量(吨)", "均价(元/吨)"]
                elif data_source == "production_summary":
                    raw_text = query_production_summary(period=period_str)
                    col_keys = ["prod_date", "material_name", "planned_qty", "produced_qty", "utilization"]
                    col_labels = ["生产日期", "物料名称", "计划量(吨)", "实产量(吨)", "完成率"]
                else:  # internal_transfers
                    raw_text = query_inventory_levels(product_id)
                    col_keys = ["material_name", "stock_qty", "warehouse"]
                    col_labels = ["物料名称", "库存量(吨)", "仓库"]

                return {
                    "type": "data_table",
                    "title": config.get("title", data_source.replace("_", " ").title()),
                    "columns": [{"key": k, "label": l} for k, l in zip(col_keys, col_labels)],
                    "rows": [],  # raw_text is narrative; frontend renders text block
                    "raw_text": raw_text,
                    "message": None,
                    "preview": True,
                }

            rows_data = []

            # Helper: Get region display name
            def get_region_display_name(price_type: str) -> str:
                region_names = {
                    "east_china": "华东",
                    "south_china": "华南",
                    "wuhan": "华中(武汉)",
                    "sinopec_sh": "上海石化",
                    "maoming": "茂名石化",
                    "yangzi": "扬子石化",
                    "brent": "国际(布伦特)",
                    "japan_cif": "日本到岸",
                    "average": "市场均价",
                    "bulk_sh": "上海大户",
                    "sinokor": "中韩石化",
                    "guangzhou": "广州石化",
                    "baling_1105": "巴陵1105",
                    "central_china": "华中",
                    "erp_sale_order_weighted": "销售加权",
                }
                return region_names.get(price_type, price_type)

            def resolve_requested_regions(product_id: str, requested_regions: List[str]) -> List[str]:
                alias_map = {
                    "sis": {
                        "sinopec_sh": ["baling_1105"],
                    },
                }
                resolved = []
                for region_id in requested_regions:
                    resolved.extend(
                        alias_map.get(product_id, {}).get(region_id, [region_id])
                    )
                return list(dict.fromkeys(resolved))

            def build_table_rows(
                history_points: List[Dict[str, Any]],
                product_name: str,
                market_name: str,
                unit: str,
            ) -> List[Dict[str, str]]:
                sorted_points = sorted(
                    [
                        point for point in history_points
                        if point.get("date") is not None and point.get("price") is not None
                    ],
                    key=lambda point: str(point["date"]),
                )
                rows = []
                previous_price = None
                for point in sorted_points:
                    current_price = float(point["price"])
                    if previous_price not in (None, 0):
                        change_value = current_price - previous_price
                        change_pct = (change_value / previous_price) * 100
                    else:
                        change_value = 0.0
                        change_pct = 0.0
                    rows.append({
                        "date": str(point["date"]),
                        "product": product_name,
                        "market": market_name,
                        "lowest_price": f"{current_price:.2f}",
                        "highest_price": f"{current_price:.2f}",
                        "mainstream_price": f"{current_price:.2f}",
                        "change_value": f"{change_value:+.2f}" if change_value else "0.00",
                        "change_pct": f"{change_pct:+.2f}%" if change_pct else "0.00%",
                        "unit": unit,
                    })
                    previous_price = current_price
                rows.sort(key=lambda row: row["date"], reverse=True)
                return rows
            
            # Helper: Get product unit
            def get_product_unit(product_id: str) -> str:
                from backend.src.domain.knowledge_graph import domain_graph
                # Find node in the graph's nodes list
                for node in domain_graph.nodes:
                    if node.id == product_id:
                        return node.unit or "元/吨"
                return "元/吨"
            
            # Get data for each product
            for product_id in products:
                product_name = self._get_product_display_name(product_id)
                unit = get_product_unit(product_id)
                
                # If regions specified, get regional historical data
                if regions:
                    try:
                        resolved_regions = resolve_requested_regions(product_id, regions)
                        regional_history = market_data.get_regional_price_history(
                            product_id=product_id,
                            price_types=resolved_regions,
                            start_date=start_date.isoformat(),
                            end_date=end_date.isoformat(),
                            limit=limit
                        )

                        if regional_history:
                            rows_by_market: Dict[str, List[Dict[str, Any]]] = {}
                            for row in regional_history:
                                rows_by_market.setdefault(str(row["price_type"]), []).append(row)
                            for price_type, market_rows in rows_by_market.items():
                                rows_data.extend(
                                    build_table_rows(
                                        history_points=market_rows,
                                        product_name=product_name,
                                        market_name=get_region_display_name(price_type),
                                        unit=unit,
                                    )
                                )
                            continue
                    except Exception as e:
                        logger.error(f"Error getting regional history for {product_id}: {e}")

                else:
                    pass

                # Fallback to standard product history if no regional series exists.
                price_history = market_data.get_price_history(
                    product_id, cutoff_date=end_date.isoformat()
                )

                if price_history:
                    filtered_history = [
                        point for point in price_history
                        if start_date.isoformat() <= str(point["date"]) <= end_date.isoformat()
                    ]
                    fallback_market = get_region_display_name("average") if regions else "默认"
                    rows_data.extend(
                        build_table_rows(
                            history_points=filtered_history[:min(limit, len(filtered_history))],
                            product_name=product_name,
                            market_name=fallback_market,
                            unit=unit,
                        )
                    )

            rows_data.sort(key=lambda row: (row["date"], row["product"], row["market"]), reverse=True)
            rows_data = rows_data[:limit]
            
            return {
                "type": "data_table",
                "title": config.get("title", "Data Table"),
                "columns": [
                    {"key": "date", "label": "数据日期"},
                    {"key": "product", "label": "产品名称"},
                    {"key": "market", "label": "市场"},
                    {"key": "lowest_price", "label": "最低价"},
                    {"key": "highest_price", "label": "最高价"},
                    {"key": "mainstream_price", "label": "主流价"},
                    {"key": "change_value", "label": "涨跌值"},
                    {"key": "change_pct", "label": "涨跌幅"},
                    {"key": "unit", "label": "单位"}
                ],
                "rows": rows_data,
                "message": "No data available for the selected filters" if not rows_data else None,
                "preview": True
            }
        
        elif block_type == "custom_sql":
            sql_query = config.get("sql_query", "").strip()
            
            if not sql_query:
                return {
                    "type": "custom_sql",
                    "title": config.get("title", "Custom Query"),
                    "error": "No SQL query provided",
                    "preview": True
                }
            
            # Security: Only allow SELECT statements
            sql_upper = sql_query.upper().strip()
            if not sql_upper.startswith("SELECT"):
                return {
                    "type": "custom_sql",
                    "title": config.get("title", "Custom Query"),
                    "error": "Only SELECT queries are allowed",
                    "preview": True
                }
            
            # Check for potentially dangerous SQL keywords
            dangerous_keywords = ["DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "CREATE", "TRUNCATE", "EXEC", "EXECUTE"]
            for keyword in dangerous_keywords:
                if keyword in sql_upper:
                    return {
                        "type": "custom_sql",
                        "title": config.get("title", "Custom Query"),
                        "error": f"Query contains prohibited keyword: {keyword}",
                        "preview": True
                    }
            
            try:
                # Execute query with limit
                limited_query = sql_query
                if "LIMIT" not in sql_upper:
                    limited_query = f"{sql_query.rstrip(';')} LIMIT {limit}"
                
                with self.db.engine.connect() as conn:
                    result = conn.execute(text(limited_query))
                    rows = result.fetchall()
                    
                    if not rows:
                        return {
                            "type": "custom_sql",
                            "title": config.get("title", "Custom Query"),
                            "columns": [],
                            "rows": [],
                            "message": "Query returned no results",
                            "preview": True
                        }
                    
                    # Get column names from result
                    column_names = list(rows[0]._mapping.keys())
                    columns = [{"key": col, "label": col} for col in column_names]
                    
                    # Convert rows to dictionaries
                    rows_data = []
                    for row in rows:
                        row_dict = {}
                        for col in column_names:
                            value = row._mapping[col]
                            # Format the value nicely
                            if isinstance(value, float):
                                row_dict[col] = f"{value:.2f}"
                            elif value is None:
                                row_dict[col] = ""
                            else:
                                row_dict[col] = str(value)
                        rows_data.append(row_dict)
                    
                    return {
                        "type": "custom_sql",
                        "title": config.get("title", "Custom Query"),
                        "columns": columns,
                        "rows": rows_data,
                        "sql_query": sql_query,
                        "preview": True
                    }
            
            except Exception as e:
                logger.error(f"Error executing custom SQL query: {e}")
                return {
                    "type": "custom_sql",
                    "title": config.get("title", "Custom Query"),
                    "error": f"Query execution error: {str(e)}",
                    "preview": True
                }

        elif block_type == "image":
            return {
                "type": "image",
                "title": config.get("title", "Image"),
                "image_url": config.get("image_url"),
                "asset_id": config.get("asset_id"),
                "alt_text": config.get("alt_text"),
                "caption": config.get("caption"),
                "preview": True,
            }
        
        return {"preview": False, "message": "Preview not available for this block type"}

    def _format_kpi_value(self, value: Any) -> str:
        """Format KPI values with grouping while trimming trailing decimals."""
        if value is None:
            return "-"
        if isinstance(value, (int, float)):
            if float(value).is_integer():
                return f"{int(value):,}"
            return f"{float(value):,.2f}".rstrip("0").rstrip(".")
        return str(value)

    def _get_product_profile(self, product_id: str) -> Dict[str, str]:
        """Get bilingual product names and display units for KPI cards."""
        product_profiles = {
            "crude_oil": {"zh": "原油", "en": "Brent Crude", "unit_en": "USD/bbl", "unit_zh": "USD/桶"},
            "naphtha": {"zh": "日本石脑油", "en": "Japan Naphtha", "unit_en": "USD/ton", "unit_zh": "USD/吨"},
            "cracked_c5": {"zh": "裂解C5", "en": "Cracked C5", "unit_en": "CNY/ton", "unit_zh": "CNY/吨"},
            "cracked_c9": {"zh": "裂解C9", "en": "Cracked C9", "unit_en": "CNY/ton", "unit_zh": "CNY/吨"},
            "mixed_c5": {"zh": "混合C5", "en": "Mixed C5", "unit_en": "CNY/ton", "unit_zh": "CNY/吨"},
            "mixed_c9": {"zh": "混合C9", "en": "Mixed C9", "unit_en": "CNY/ton", "unit_zh": "CNY/吨"},
            "isoprene": {"zh": "异戊二烯", "en": "Isoprene", "unit_en": "CNY/ton", "unit_zh": "CNY/吨"},
            "piperylene": {"zh": "间戊二烯", "en": "Piperylene", "unit_en": "CNY/ton", "unit_zh": "CNY/吨"},
            "dcpd": {"zh": "双环戊二烯", "en": "DCPD", "unit_en": "CNY/ton", "unit_zh": "CNY/吨"},
            "raffinate_c5": {"zh": "抽余C5", "en": "Raffinate C5", "unit_en": "CNY/ton", "unit_zh": "CNY/吨"},
            "sis": {"zh": "SIS（巴陵1105）", "en": "SIS", "unit_en": "CNY/ton", "unit_zh": "CNY/吨"},
            "sis_rubber": {"zh": "SIS橡胶", "en": "SIS Rubber", "unit_en": "CNY/ton", "unit_zh": "CNY/吨"},
            "c5_resin": {"zh": "C5树脂", "en": "C5 Resin", "unit_en": "CNY/ton", "unit_zh": "CNY/吨"},
            "c9_resin": {"zh": "C9树脂", "en": "C9 Resin", "unit_en": "CNY/ton", "unit_zh": "CNY/吨"},
            "hydrogenated_c9": {"zh": "氢化C9", "en": "Hydrogenated C9", "unit_en": "CNY/ton", "unit_zh": "CNY/吨"},
            "blowing_agent": {"zh": "发泡剂", "en": "Blowing Agent", "unit_en": "CNY/ton", "unit_zh": "CNY/吨"},
            "styrene": {"zh": "苯乙烯", "en": "Styrene", "unit_en": "CNY/ton", "unit_zh": "CNY/吨"},
            "trimethylbenzene": {"zh": "三甲苯", "en": "Trimethylbenzene", "unit_en": "CNY/ton", "unit_zh": "CNY/吨"},
            "mthpa_hardener": {"zh": "MTHPA固化剂", "en": "MTHPA Hardener", "unit_en": "CNY/ton", "unit_zh": "CNY/吨"},
            "brent_crude_oil": {"zh": "布伦特原油", "en": "Brent Crude", "unit_en": "USD/bbl", "unit_zh": "USD/桶"},
            "japanese_naphtha": {"zh": "日本石脑油", "en": "Japan Naphtha", "unit_en": "USD/ton", "unit_zh": "USD/吨"},
        }
        default_label = product_id.replace("_", " ").title()
        return product_profiles.get(
            product_id,
            {"zh": self._get_product_display_name(product_id), "en": default_label, "unit_en": "", "unit_zh": ""},
        )
    
    def _get_product_display_name(self, product_id: str) -> str:
        """Get display name for product ID (Chinese)"""
        product_names = {
            "crude_oil": "原油",
            "naphtha": "日本石脑油",
            "cracked_c5": "裂解C5",
            "cracked_c9": "裂解C9",
            "mixed_c5": "混合C5",
            "mixed_c9": "混合C9",
            "isoprene": "异戊二烯",
            "piperylene": "间戊二烯",
            "dcpd": "双环戊二烯",
            "raffinate_c5": "抽余C5",
            "sis": "SIS",
            "sis_rubber": "SIS橡胶",
            "c5_resin": "C5树脂",
            "c9_resin": "C9树脂",
            "hydrogenated_c9": "氢化C9",
            "blowing_agent": "发泡剂",
            "styrene": "苯乙烯",
            "trimethylbenzene": "三甲苯",
            "mthpa_hardener": "MTHPA固化剂",
            "brent_crude_oil": "布伦特原油",
            "japanese_naphtha": "日本石脑油",
        }
        return product_names.get(product_id, product_id.replace("_", " ").title())
    
    def _get_mock_table_data(self, data_source: str, limit: int) -> List[Dict[str, Any]]:
        """Generate mock table data for preview"""
        if data_source == "market_prices":
            return [
                {"region": "东营", "company": "华联石化", "grade_92": 7933, "delta_92": -100},
                {"region": "东营", "company": "联合石化", "grade_92": 7933, "delta_92": -100},
                {"region": "淄博", "company": "齐成石化", "grade_92": 7745, "delta_92": -80}
            ][:limit]
        return []
    
    def _get_mock_kpi_value(self, metric: str) -> Any:
        """Generate mock KPI value"""
        kpi_values = {
            "wti_price": 92.13,
            "brent_price": 98.48,
            "ine_price": 613.9,
            "total_revenue": 15680000,
            "total_volume": 2340,
            "avg_price": 6700
        }
        return kpi_values.get(metric, 0)
    
    def _get_mock_kpi_change(self, metric: str) -> float:
        """Generate mock KPI change"""
        changes = {
            "wti_price": -0.41,
            "brent_price": -0.14,
            "ine_price": -6.2,
            "total_revenue": 230000,
            "total_volume": -45
        }
        return changes.get(metric, 0)
    
    def _get_mock_chart_data(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Generate mock chart data"""
        # Would query real data
        return [
            {"date": "2026-04-01", "value": 7800},
            {"date": "2026-04-08", "value": 7900},
            {"date": "2026-04-15", "value": 7850},
            {"date": "2026-04-22", "value": 7950}
        ]
    
    def suggest_next_block(self, current_blocks: List[Dict[str, Any]], context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        AI-powered suggestions for next block to add.
        
        Analyzes current report structure and suggests relevant additions.
        """
        suggestions = []
        
        has_kpis = any(b.get("type") == "kpi_cards" for b in current_blocks)
        has_table = any(b.get("type") == "data_table" for b in current_blocks)
        has_chart = any(b.get("type") == "chart" for b in current_blocks)
        
        # Suggest KPIs if missing
        if not has_kpis:
            suggestions.append({
                "type": "kpi_cards",
                "title": "添加关键指标",
                "reason": "报告通常以关键指标开始",
                "confidence": 0.9,
                "config": {
                    "metrics": ["wti_price", "brent_price", "ine_price"]
                }
            })
        
        # Suggest table if has KPIs but no table
        if has_kpis and not has_table:
            suggestions.append({
                "type": "data_table",
                "title": "添加数据表",
                "reason": "KPI指标后通常跟详细数据表",
                "confidence": 0.85,
                "config": {
                    "data_source": "market_prices",
                    "columns": ["region", "company", "grade_92", "delta_92"]
                }
            })
        
        # Suggest chart if has table but no chart
        if has_table and not has_chart:
            suggestions.append({
                "type": "chart",
                "title": "添加趋势图表",
                "reason": "数据表后添加趋势图可以更好地展示变化",
                "confidence": 0.8,
                "config": {
                    "chart_type": "line",
                    "data_source": "market_prices",
                    "x_axis": "date",
                    "y_axis": "avg_price"
                }
            })
        
        return sorted(suggestions, key=lambda x: x["confidence"], reverse=True)
    
    def validate_report_structure(self, blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Validate report structure and provide quality score.
        
        Checks:
        - Has title/header
        - Logical block order
        - Data source availability
        - Missing critical sections
        """
        issues = []
        warnings = []
        score = 100
        
        if not blocks:
            issues.append("报告为空，至少需要一个内容块")
            score -= 50
        
        has_header = any(b.get("type") in ["kpi_cards", "text"] for b in blocks[:2])
        if not has_header:
            warnings.append("建议在报告开头添加标题或关键指标")
            score -= 10
        
        # Check for data diversity
        data_sources = set()
        for block in blocks:
            config = block.get("config", {})
            if "data_source" in config:
                data_sources.add(config["data_source"])
        
        if len(data_sources) == 1:
            warnings.append("报告仅使用一个数据源，考虑添加更多维度")
            score -= 5
        
        # Check for narrative
        has_text = any(b.get("type") == "text" for b in blocks)
        if not has_text:
            warnings.append("建议添加文字说明以增强报告可读性")
            score -= 10
        
        return {
            "valid": len(issues) == 0,
            "score": max(0, score),
            "issues": issues,
            "warnings": warnings,
            "suggestions": self._get_structure_suggestions(blocks)
        }
    
    def _get_structure_suggestions(self, blocks: List[Dict[str, Any]]) -> List[str]:
        """Get suggestions for improving report structure"""
        suggestions = []
        
        if len(blocks) < 3:
            suggestions.append("增加更多内容块以提供全面分析")
        
        has_conclusion = False
        if blocks:
            last_block = blocks[-1]
            if last_block.get("type") == "text":
                has_conclusion = True
        
        if not has_conclusion:
            suggestions.append("在报告结尾添加总结或建议")
        
        return suggestions


# Singleton instance
_authoring_service: Optional[ReportAuthoringService] = None


def get_authoring_service() -> ReportAuthoringService:
    """Get or create the authoring service singleton"""
    global _authoring_service
    if _authoring_service is None:
        _authoring_service = ReportAuthoringService(db_connection=db)
    return _authoring_service
