"""
Professional report export service with PDF, DOCX, and PPTX generation.

Generates publication-quality exports matching enterprise standards shown in market reports.
"""

import os
import json
import logging
from typing import Dict, Any, List, Optional, Literal
from datetime import datetime
from pathlib import Path
import base64
from io import BytesIO
from urllib.request import urlopen

logger = logging.getLogger(__name__)

ExportFormat = Literal["pdf", "docx", "pptx", "html"]


class ReportExportService:
    """
    Converts report blocks into professional exports.
    
    Supports:
    - PDF with styled tables, charts, KPI cards
    - DOCX for editing
    - PPTX for presentations
    - HTML preview
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.template_dir = Path(__file__).parent.parent.parent / "templates" / "exports"
        self.template_dir.mkdir(parents=True, exist_ok=True)
        
    def export_report(
        self, 
        report_data: Dict[str, Any], 
        format: ExportFormat = "pdf",
        options: Optional[Dict[str, Any]] = None
    ) -> bytes:
        """
        Export a report to the specified format.
        
        Args:
            report_data: Report structure with blocks, metadata, styling
            format: Output format (pdf, docx, pptx, html)
            options: Format-specific options
            
        Returns:
            Binary content of the exported file
        """
        options = options or {}
        
        # Enrich blocks with actual data if they have config
        report_data = self._enrich_blocks(report_data)
        
        if format == "pdf":
            return self._export_to_pdf(report_data, options)
        elif format == "html":
            return self._export_to_html(report_data, options).encode('utf-8')
        elif format == "docx":
            return self._export_to_docx(report_data, options)
        elif format == "pptx":
            return self._export_to_pptx(report_data, options)
        else:
            raise ValueError(f"Unsupported export format: {format}")
    
    def _enrich_blocks(self, report_data: Dict[str, Any]) -> Dict[str, Any]:
        """Enrich blocks that need data fetching"""
        enriched_blocks = []
        
        logger.info(f"=== EXPORT ENRICHMENT START ===")
        logger.info(f"Total blocks to process: {len(report_data.get('blocks', []))}")
        
        for idx, block in enumerate(report_data.get("blocks", [])):
            block_type = block.get("type")
            rendered = block.get("rendered", {})
            
            logger.info(f"Block {idx}: type={block_type}, has_rendered={bool(rendered)}, rendered_keys={list(rendered.keys()) if rendered else []}, block_keys={list(block.keys())}")
            
            # Determine if this block needs enrichment
            # Check if rendered field is missing or empty
            needs_enrichment = False
            
            if block_type == "data_table":
                # Data tables need enrichment if rendered doesn't have columns/rows
                needs_enrichment = not rendered or "columns" not in rendered or "rows" not in rendered
            elif block_type == "chart":
                # Charts need enrichment if rendered doesn't have data
                needs_enrichment = not rendered or ("data" not in rendered and "chartData" not in rendered)
            elif block_type == "kpi_cards":
                # KPI cards need enrichment if rendered doesn't have cards data
                needs_enrichment = not rendered or ("cards" not in rendered and "data" not in rendered)
            elif block_type == "custom_sql":
                # Custom SQL must have tabular payload for proper export rendering
                needs_enrichment = not rendered or ("columns" not in rendered or "rows" not in rendered)
            elif block_type == "text":
                # Text blocks with definition but no rendered content need enrichment
                needs_enrichment = not rendered or "content" not in rendered
            elif block_type == "image":
                needs_enrichment = not rendered or "image_url" not in rendered
            
            # Enrich if needed, otherwise pass through
            if needs_enrichment and (block.get("definition") or block.get("config")):
                logger.info(f"Block {idx} needs enrichment - fetching data")
                enriched_block = self._fetch_block_data(block)
                enriched_blocks.append(enriched_block)
                logger.info(f"Block {idx} enriched - final keys: {list(enriched_block.keys())}")
            else:
                # If already has rendered data, flatten it to top level for compatibility
                if rendered:
                    flattened = {**block, **rendered}
                    enriched_blocks.append(flattened)
                    logger.info(f"Block {idx} flattened - final keys: {list(flattened.keys())}, cards={flattened.get('cards')}, columns={flattened.get('columns')}")
                else:
                    enriched_blocks.append(block)
                    logger.info(f"Block {idx} passed through unchanged")
        
        logger.info(f"=== EXPORT ENRICHMENT COMPLETE: {len(enriched_blocks)} blocks ready ===")
        return {**report_data, "blocks": enriched_blocks}
    
    def _fetch_block_data(self, block: Dict[str, Any]) -> Dict[str, Any]:
        """Fetch actual data for a block using report authoring service"""
        try:
            from backend.src.services.report_authoring_service import get_authoring_service
            
            authoring_service = get_authoring_service()
            
            # Normalize block format: frontend spreads config to top level,
            # but preview_block_data expects config as nested object
            normalized_block = self._normalize_block_for_preview(block)
            
            # preview_block_data expects the whole block (with type and config)
            rendered_data = authoring_service.preview_block_data(normalized_block, limit=1000)
            
            # Debug log to see what we're getting
            logger.info(f"Enriched block {block.get('type')}: columns={len(rendered_data.get('columns', []))}, rows={len(rendered_data.get('rows', []))}")
            
            # Merge original block with rendered data
            return {**block, **rendered_data}
        except Exception as e:
            logger.error(f"Failed to enrich block {block.get('type')}: {e}", exc_info=True)
            # Return original block if enrichment fails
            return block
    
    def _normalize_block_for_preview(self, block: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize block format for preview_block_data.
        Frontend may send definition field which should be used as config.
        """
        # If already has config, return as is
        if "config" in block:
            return block
        
        # If has definition field, use it as config
        if "definition" in block:
            return {
                "type": block.get("type"),
                "title": block.get("title", ""),
                "config": block.get("definition", {})
            }
        
        # Otherwise, wrap relevant keys into config
        block_type = block.get("type")
        config_keys = set()
        
        if block_type == "data_table":
            config_keys = {"products", "dateRange", "date_range", "columns_config", "filters"}
        elif block_type == "chart":
            config_keys = {"products", "dateRange", "date_range", "chart_type", "chartType"}
        elif block_type == "kpi_cards":
            config_keys = {"products", "metrics", "dateRange", "date_range"}
        elif block_type == "text":
            config_keys = {"content", "style", "format"}
        elif block_type == "image":
            config_keys = {"asset_id", "image_url", "alt_text", "caption"}
        
        # Build config from matching keys
        config = {k: v for k, v in block.items() if k in config_keys}
        
        # Build normalized block with config nested
        normalized = {
            "type": block_type,
            "title": block.get("title", ""),
            "config": config
        }
        
        # Preserve any other top-level keys that aren't in config
        for k, v in block.items():
            if k not in config_keys and k not in ["type", "title"]:
                normalized[k] = v
        
        logger.debug(f"Normalized block: {list(block.keys())} -> {list(normalized.keys())}, config keys: {list(config.keys())}")
        
        return normalized
    
    def _export_to_html(self, report_data: Dict[str, Any], options: Dict[str, Any]) -> str:
        """
        Generate styled HTML suitable for PDF rendering or web preview.
        
        Matches the professional styling from the market report image:
        - Blue header with KPI cards
        - Clean table grid with color-coded deltas
        - Side-by-side charts
        - Footer annotations
        """
        blocks = report_data.get("blocks", [])
        title = report_data.get("title", "Report")
        generated_at = report_data.get("generated_at", datetime.now().strftime("%Y-%m-%d %H:%M"))
        
        html_parts = [self._get_html_header(title, options)]
        
        # Add title section
        html_parts.append(f"""
        <div class="report-header">
            <h1>{self._escape_html(title)}</h1>
            <div class="report-meta">Generated at: {generated_at}</div>
        </div>
        """)
        
        # Render blocks with section numbers
        section_counter = 1
        for block in blocks:
            block_type = block.get("type")
            block_title = block.get("title", "")
            
            # Add section title
            if block_title:
                html_parts.append(f'<div class="section-title">{section_counter}. {self._escape_html(block_title)}</div>')
                section_counter += 1
            
            if block_type == "kpi_cards":
                html_parts.append(self._render_kpi_cards(block))
            elif block_type == "data_table":
                html_parts.append(self._render_data_table(block))
            elif block_type == "custom_sql":
                html_parts.append(self._render_custom_sql(block))
            elif block_type == "chart":
                html_parts.append(self._render_chart(block))
            elif block_type == "text":
                html_parts.append(self._render_text(block))
            elif block_type == "image":
                html_parts.append(self._render_image(block))
            elif block_type == "chart_row":
                html_parts.append(self._render_chart_row(block))
            else:
                logger.warning(f"Unknown block type: {block_type}")
        
        html_parts.append(self._get_html_footer())
        
        return "\n".join(html_parts)
    
    def _get_html_header(self, title: str, options: Dict[str, Any]) -> str:
        """Generate HTML header with simple clean styling"""
        return f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{self._escape_html(title)}</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: "Microsoft YaHei", "PingFang SC", "Helvetica Neue", Arial, sans-serif;
            font-size: 13px;
            line-height: 1.6;
            color: #333;
            background: #ffffff;
            padding: 40px 60px;
            max-width: 1200px;
            margin: 0 auto;
        }}
        
        .report-header {{
            text-align: left;
            margin-bottom: 36px;
        }}
        
        .report-header h1 {{
            font-size: 32px;
            font-weight: 700;
            color: #111827;
            margin-bottom: 8px;
        }}
        
        .report-meta {{
            font-size: 14px;
            color: #94a3b8;
        }}
        
        .section-title {{
            font-size: 20px;
            font-weight: 700;
            color: #1f2937;
            margin: 26px 0 14px 0;
            padding-bottom: 8px;
            border-bottom: 2px solid #e5e7eb;
        }}
        
        /* KPI Cards */
        .kpi-section {{
            margin-bottom: 32px;
        }}

        .kpi-period-label {{
            font-size: 12px;
            color: #94a3b8;
            font-style: italic;
            margin: 2px 0 10px 0;
        }}

        .kpi-container {{
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 0;
            margin-bottom: 22px;
            border-top: 1px solid #d6ccb7;
            border-bottom: 1px solid #d6ccb7;
            background: #ffffff;
        }}
        
        .kpi-card {{
            background: #ffffff;
            padding: 20px 22px 18px;
            min-height: 145px;
            border-right: 1px solid #e8ddc8;
            display: flex;
            flex-direction: column;
            gap: 10px;
        }}
        
        .kpi-card:nth-child(4n) {{
            border-right: none;
        }}

        .kpi-label-row {{
            display: flex;
            flex-wrap: wrap;
            align-items: baseline;
            gap: 8px;
        }}

        .kpi-label {{
            font-size: 13px;
            font-weight: 700;
            color: #374151;
            letter-spacing: 0.02em;
        }}

        .kpi-label-secondary {{
            font-size: 9px;
            color: #7b808a;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.1em;
        }}

        .kpi-value-row {{
            display: flex;
            flex-wrap: wrap;
            align-items: flex-end;
            gap: 8px;
        }}
        
        .kpi-value {{
            font-size: 34px;
            font-weight: 700;
            line-height: 0.98;
            letter-spacing: -0.03em;
            color: #111827;
            font-variant-numeric: tabular-nums;
        }}

        .kpi-unit {{
            font-size: 11px;
            color: #6b7280;
            padding-bottom: 4px;
            letter-spacing: 0.03em;
            text-transform: uppercase;
        }}

        .kpi-change-row {{
            display: flex;
            align-items: center;
            flex-wrap: wrap;
            gap: 8px;
        }}
        
        .kpi-delta {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            font-size: 14px;
            font-weight: 700;
        }}
        
        .kpi-delta.up {{
            color: #0f766e;
        }}
        
        .kpi-delta.down {{
            color: #a63d2a;
        }}

        .kpi-delta.neutral {{
            color: #6b7280;
        }}

        .kpi-delta-arrow {{
            font-size: 18px;
            line-height: 1;
        }}
        
        .kpi-comparison {{
            font-size: 11px;
            color: #6b7280;
            font-weight: 500;
        }}

        .kpi-meta {{
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            margin-top: auto;
            font-size: 10px;
            color: #6b7280;
        }}

        .kpi-meta-item {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            white-space: nowrap;
        }}

        .kpi-meta-label {{
            color: #9ca3af;
            font-weight: 600;
        }}
        
        /* Data Tables */
        .data-table-container {{
            margin-bottom: 30px;
            overflow-x: auto;
        }}
        
        table.data-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 12px;
            background: white;
        }}
        
        table.data-table th {{
            background: #0066cc;
            color: white;
            padding: 10px 8px;
            text-align: center;
            font-weight: 600;
            border: 1px solid #0066cc;
        }}
        
        table.data-table td {{
            padding: 10px 8px;
            border: 1px solid #ddd;
            text-align: center;
        }}
        
        table.data-table tr:nth-child(even) {{
            background: #fafafa;
        }}
        
        .cell-delta {{
            font-weight: 500;
        }}
        
        .cell-delta.positive {{
            color: #d32f2f;
        }}
        
        .cell-delta.negative {{
            color: #388e3c;
        }}
        
        .cell-null {{
            color: #999;
        }}
        
        /* Charts */
        .chart-container {{
            margin-bottom: 30px;
            background: white;
            padding: 15px;
            border: 1px solid #ddd;
            border-radius: 4px;
            max-width: 100%;
            overflow: hidden;
        }}
        
        .chart-row {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 15px;
            margin-bottom: 20px;
        }}
        
        .chart-row .chart-container {{
            margin-bottom: 0;
        }}
        
        .chart-title {{
            font-size: 13px;
            font-weight: 600;
            margin-bottom: 10px;
            color: #1e40af;
        }}
        
        .chart-image {{
            width: 100%;
            max-width: 100%;
            height: auto;
            display: block;
            object-fit: contain;
        }}
        
        /* Text blocks */
        .text-block {{
            margin-bottom: 15px;
            padding: 12px;
            background: #f9fafb;
            border-left: 3px solid #3b82f6;
            font-size: 11px;
            line-height: 1.6;
        }}
        
        .text-block.notes {{
            background: #fef3c7;
            border-left-color: #f59e0b;
        }}
        
        /* Footer */
        .report-footer {{
            margin-top: 30px;
            padding-top: 15px;
            border-top: 2px solid #e5e7eb;
            font-size: 10px;
            color: #6b7280;
        }}
        
        /* Print optimization */
        @media print {{
            body {{
                padding: 0;
                max-width: 100%;
            }}
            
            .report-header {{
                break-inside: avoid;
            }}
            
            .kpi-container {{
                break-inside: avoid;
            }}

            .kpi-card {{
                break-inside: avoid;
            }}
            
            .data-table-container {{
                break-inside: avoid;
            }}
            
            .chart-container {{
                break-inside: avoid;
                max-width: 100%;
                page-break-inside: avoid;
            }}
            
            .chart-image {{
                max-width: 100%;
                max-height: 400px;
                object-fit: contain;
            }}
            
            .chart-row {{
                break-inside: avoid;
            }}
        }}

        @media (max-width: 1100px) {{
            .kpi-container {{
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }}

            .kpi-card:nth-child(4n) {{
                border-right: 1px solid #e8ddc8;
            }}

            .kpi-card:nth-child(2n) {{
                border-right: none;
            }}
        }}

        @media (max-width: 720px) {{
            .kpi-container {{
                grid-template-columns: 1fr;
            }}

            .kpi-card,
            .kpi-card:nth-child(4n),
            .kpi-card:nth-child(2n) {{
                border-right: none;
                border-bottom: 1px solid #e8ddc8;
            }}

            .kpi-card:last-child {{
                border-bottom: none;
            }}

            .kpi-value {{
                font-size: 28px;
            }}
        }}
    </style>
</head>
<body>
"""
    
    def _get_html_footer(self) -> str:
        """Generate HTML footer"""
        return """
</body>
</html>
"""
    
    def _render_kpi_cards(self, block: Dict[str, Any]) -> str:
        """Render KPI cards using the same professional structure as preview."""
        # Support both "cards" and "data" field names
        cards = block.get("cards", block.get("data", []))
        
        logger.info(f"Rendering KPI cards: has_cards={bool(block.get('cards'))}, has_data={bool(block.get('data'))}, cards_count={len(cards) if isinstance(cards, list) else 0}")
        if cards and isinstance(cards, list) and len(cards) > 0:
            logger.info(f"First card structure: {cards[0]}")
        
        period_label = block.get("period_label")
        cards_html = []
        for card in cards:
            view_model = self._build_kpi_card_view_model(card)
            secondary_label_html = (
                f'<div class="kpi-label-secondary">{self._escape_html(view_model["secondary_label"])}</div>'
                if view_model["secondary_label"]
                else ""
            )
            unit_html = (
                f'<div class="kpi-unit">{self._escape_html(view_model["unit"])}</div>'
                if view_model["unit"]
                else ""
            )
            meta_html = ""
            if view_model["range_items"]:
                meta_html = '<div class="kpi-meta">' + "".join(
                    f'<span class="kpi-meta-item"><span class="kpi-meta-label">{self._escape_html(item["label"])}</span><span>{self._escape_html(item["value"])}</span></span>'
                    for item in view_model["range_items"]
                ) + '</div>'

            cards_html.append(f"""
            <div class="kpi-card">
                <div class="kpi-label-row">
                    <div class="kpi-label">{self._escape_html(view_model["primary_label"])}</div>
                    {secondary_label_html}
                </div>
                <div class="kpi-value-row">
                    <div class="kpi-value">{self._escape_html(view_model["value_display"])}</div>
                    {unit_html}
                </div>
                <div class="kpi-change-row">
                    <div class="kpi-delta {view_model["trend"]}">
                        <span class="kpi-delta-arrow">{view_model["arrow"]}</span>
                        <span>{self._escape_html(view_model["change_text"])}</span>
                    </div>
                    <div class="kpi-comparison">{self._escape_html(view_model["comparison"])}</div>
                </div>
                {meta_html}
            </div>
            """)

        period_html = (
            f'<div class="kpi-period-label">Period: {self._escape_html(str(period_label))}</div>'
            if period_label
            else ""
        )
        return f'<div class="kpi-section">{period_html}<div class="kpi-container">{"".join(cards_html)}</div></div>'

    def _format_kpi_value(self, value: Any) -> str:
        """Format KPI values with grouping and trimmed decimals."""
        if value in (None, ""):
            return "-"
        if isinstance(value, (int, float)):
            if float(value).is_integer():
                return f"{int(value):,}"
            return f"{float(value):,.2f}".rstrip("0").rstrip(".")
        try:
            numeric_value = float(str(value))
            return self._format_kpi_value(numeric_value)
        except (TypeError, ValueError):
            return str(value)

    def _parse_kpi_change_value(self, card: Dict[str, Any]) -> Optional[float]:
        """Extract a numeric percentage change from a KPI card when possible."""
        change_value = card.get("change_value")
        if isinstance(change_value, (int, float)):
            return float(change_value)

        delta_pct = card.get("delta_pct")
        if isinstance(delta_pct, (int, float)):
            return float(delta_pct)

        change = card.get("change")
        if isinstance(change, str):
            try:
                return float(change.replace("%", "").strip())
            except ValueError:
                return None
        return None

    def _build_kpi_card_view_model(self, card: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize KPI cards so preview, HTML export, and PDF export share one layout model."""
        change_value = self._parse_kpi_change_value(card)
        trend = str(card.get("trend") or "").lower()
        if trend not in {"up", "down", "neutral"}:
            if change_value is None or change_value == 0:
                trend = "neutral"
            else:
                trend = "up" if change_value > 0 else "down"

        change_text = "持平"
        if trend != "neutral":
            if change_value is not None:
                change_text = f"{abs(change_value):.1f}%"
            else:
                raw_change = str(card.get("change") or "").strip()
                change_text = raw_change.lstrip("+-") or "0.0%"

        range_items: List[Dict[str, str]] = []
        high_display = card.get("high_display") or self._format_kpi_value(card.get("high"))
        low_display = card.get("low_display") or self._format_kpi_value(card.get("low"))
        if card.get("high") is not None or card.get("high_display"):
            range_items.append({"label": "区间高", "value": str(high_display)})
        if card.get("low") is not None or card.get("low_display"):
            range_items.append({"label": "区间低", "value": str(low_display)})

        secondary_label = str(card.get("label_en") or "").strip()
        primary_label = str(card.get("label") or card.get("name") or "-").strip()
        if secondary_label == primary_label:
            secondary_label = ""

        return {
            "arrow": "▲" if trend == "up" else "▼" if trend == "down" else "—",
            "change_text": change_text,
            "comparison": str(card.get("comparison_label") or "较期初"),
            "primary_label": primary_label,
            "range_items": range_items,
            "secondary_label": secondary_label,
            "trend": trend,
            "unit": str(card.get("unit_zh") or card.get("unit") or card.get("unit_en") or "").strip(),
            "value_display": str(card.get("value_display") or self._format_kpi_value(card.get("value"))),
        }
    
    def _render_data_table(self, block: Dict[str, Any]) -> str:
        """Render simple clean data table"""
        columns = block.get("columns", [])
        rows = block.get("rows", [])
        
        # Build table header
        header_html = "<tr>"
        for col in columns:
            col_label = col if isinstance(col, str) else col.get("label", "")
            header_html += f"<th>{self._escape_html(col_label)}</th>"
        header_html += "</tr>"
        
        # Build table rows
        rows_html = []
        for row in rows:
            row_html = "<tr>"
            for i, col in enumerate(columns):
                col_key = col if isinstance(col, str) else col.get("key", f"col_{i}")
                cell_value = row.get(col_key, "")
                
                # Handle special cell types
                cell_html = self._format_table_cell(cell_value, col)
                row_html += f"<td>{cell_html}</td>"
            
            row_html += "</tr>"
            rows_html.append(row_html)
        
        return f"""
        <div class="data-table-container">
            <table class="data-table">
                <thead>{header_html}</thead>
                <tbody>{"".join(rows_html)}</tbody>
            </table>
        </div>
        """
    
    def _render_custom_sql(self, block: Dict[str, Any]) -> str:
        """Render custom SQL query results as a table"""
        columns = block.get("columns", [])
        rows = block.get("rows", [])
        error = block.get("error")
        sql_query = block.get("sql_query", "")
        
        # If there was an error, display it
        if error:
            return f"""
            <div class="custom-sql-container">
                <div class="error-message" style="color: #d32f2f; padding: 12px; background: #ffebee; border-radius: 4px; margin-bottom: 16px;">
                    <strong>Query Error:</strong> {self._escape_html(error)}
                </div>
                {f'<pre style="background: #f5f5f5; padding: 12px; border-radius: 4px; font-size: 12px; overflow-x: auto;"><code>{self._escape_html(sql_query)}</code></pre>' if sql_query else ''}
            </div>
            """
        
        # If no rows, show message
        if not rows:
            message = block.get("message", "No results")
            return f"""
            <div class="custom-sql-container">
                <div class="info-message" style="color: #757575; padding: 12px; background: #f5f5f5; border-radius: 4px;">
                    {self._escape_html(message)}
                </div>
                {f'<pre style="background: #f5f5f5; padding: 12px; border-radius: 4px; margin-top: 8px; font-size: 12px; overflow-x: auto;"><code>{self._escape_html(sql_query)}</code></pre>' if sql_query else ''}
            </div>
            """
        
        # Build table header
        header_html = "<tr>"
        for col in columns:
            col_label = col if isinstance(col, str) else col.get("label", "")
            header_html += f"<th>{self._escape_html(col_label)}</th>"
        header_html += "</tr>"
        
        # Build table rows
        rows_html = []
        for row in rows:
            row_html = "<tr>"
            for i, col in enumerate(columns):
                col_key = col if isinstance(col, str) else col.get("key", f"col_{i}")
                cell_value = row.get(col_key, "")
                
                # Format cell value
                cell_html = self._format_table_cell(cell_value, col)
                row_html += f"<td>{cell_html}</td>"
            
            row_html += "</tr>"
            rows_html.append(row_html)
        
        # Optionally show the SQL query below the table (for transparency)
        query_display = ""
        if sql_query:
            query_display = f"""
            <details style="margin-top: 16px;">
                <summary style="cursor: pointer; color: #666; font-size: 12px;">View Query</summary>
                <pre style="background: #f5f5f5; padding: 12px; border-radius: 4px; margin-top: 8px; font-size: 12px; overflow-x: auto;"><code>{self._escape_html(sql_query)}</code></pre>
            </details>
            """
        
        return f"""
        <div class="custom-sql-container">
            <table class="data-table">
                <thead>{header_html}</thead>
                <tbody>{"".join(rows_html)}</tbody>
            </table>
            {query_display}
        </div>
        """
    
    def _format_table_cell(self, value: Any, column: Any) -> str:
        """Format individual table cell with proper styling"""
        if value is None or value == "" or value == "/":
            return '<span class="cell-null">/</span>'
        
        # Check if it's a delta value (number with sign)
        if isinstance(value, (int, float)):
            if value == 0:
                return "0"
            elif value > 0:
                return f'<span class="cell-delta positive">+{value}</span>'
            else:
                return f'<span class="cell-delta negative">{value}</span>'
        
        # Handle dict with value and delta
        if isinstance(value, dict):
            main_value = value.get("value", "")
            delta = value.get("delta")
            
            if delta is not None and delta != 0:
                delta_class = "positive" if delta > 0 else "negative"
                sign = "+" if delta > 0 else ""
                return f'{main_value} <span class="cell-delta {delta_class}">({sign}{delta})</span>'
            
            return str(main_value)
        
        return self._escape_html(str(value))
    
    def _render_chart(self, block: Dict[str, Any]) -> str:
        """Render chart (from base64 image or chart config)"""
        title = block.get("title", "")
        image_data = block.get("image_base64")
        chart_url = block.get("chart_url")
        
        title_html = f'<div class="chart-title">{self._escape_html(title)}</div>' if title else ''
        
        if image_data:
            # Base64 embedded image
            img_src = f"data:image/png;base64,{image_data}"
            return f"""
            <div class="chart-container">
                {title_html}
                <img src="{img_src}" class="chart-image" alt="{self._escape_html(title)}" />
            </div>
            """
        elif chart_url:
            # External chart URL
            return f"""
            <div class="chart-container">
                {title_html}
                <img src="{self._escape_html(chart_url)}" class="chart-image" alt="{self._escape_html(title)}" />
            </div>
            """
        else:
            # Placeholder for chart to be rendered
            return f"""
            <div class="chart-container">
                {title_html}
                <div style="height: 250px; max-width: 100%; background: #f3f4f6; display: flex; align-items: center; justify-content: center; color: #9ca3af; border-radius: 4px;">
                    图表占位符
                </div>
            </div>
            """
    
    def _render_chart_row(self, block: Dict[str, Any]) -> str:
        """Render two charts side by side"""
        charts = block.get("charts", [])
        
        charts_html = []
        for chart in charts:
            charts_html.append(self._render_chart(chart))
        
        return f'<div class="chart-row">{"".join(charts_html)}</div>'
    
    def _render_text(self, block: Dict[str, Any]) -> str:
        """Render text/note block"""
        content = block.get("content", "")
        block_class = block.get("class", "")
        
        class_attr = f' {block_class}' if block_class else ''
        
        # Convert markdown-style formatting to HTML
        formatted = self._simple_markdown(content)
        
        return f'<div class="text-block{class_attr}">{formatted}</div>'

    def _render_image(self, block: Dict[str, Any]) -> str:
        """Render image block"""
        image_url = block.get("image_url", "")
        if not image_url:
            return '<div class="empty-state">No image selected</div>'

        alt_text = self._escape_html(block.get("alt_text", ""))
        caption = block.get("caption", "")
        caption_html = f'<div class="footnote">{self._escape_html(caption)}</div>' if caption else ""
        safe_url = self._escape_html(image_url)
        return (
            '<div class="chart-box" style="text-align:center;">'
            f'<img src="{safe_url}" alt="{alt_text}" '
            'style="max-width:100%; max-height:420px; object-fit:contain; border-radius:8px;" />'
            f'{caption_html}'
            '</div>'
        )

    def _load_image_stream(self, image_url: str) -> Optional[BytesIO]:
        if not image_url:
            return None
        try:
            if image_url.startswith("data:image") and "," in image_url:
                encoded = image_url.split(",", 1)[1]
                return BytesIO(base64.b64decode(encoded))
            if image_url.startswith("http://") or image_url.startswith("https://"):
                with urlopen(image_url, timeout=10) as response:
                    return BytesIO(response.read())

            image_path = Path(image_url)
            if image_path.exists():
                return BytesIO(image_path.read_bytes())
        except Exception as exc:
            logger.warning("Failed to load image asset %s: %s", image_url, exc)
        return None
    
    def _simple_markdown(self, text: str) -> str:
        """Convert basic markdown to HTML"""
        # Bold
        text = text.replace("**", "<strong>", 1).replace("**", "</strong>", 1) if text.count("**") >= 2 else text
        # Line breaks
        text = text.replace("\n", "<br>")
        return self._escape_html(text)
    
    def _escape_html(self, text: str) -> str:
        """Escape HTML special characters"""
        return (str(text)
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
                .replace("'", "&#39;"))
    
    def _export_to_pdf(self, report_data: Dict[str, Any], options: Dict[str, Any]) -> bytes:
        """
        Export to PDF using HTML rendering.
        
        Requires weasyprint or playwright for HTML→PDF conversion.
        """
        html_content = self._export_to_html(report_data, options)
        
        # Use reportlab for PDF generation (WeasyPrint has compatibility issues on macOS)
        try:
            return self._export_to_pdf_reportlab(report_data, options)
        except ImportError:
            logger.error("No PDF library available. Install reportlab: pip install reportlab")
            # Return HTML as fallback
            return html_content.encode('utf-8')
    
    def _export_to_pdf_reportlab(self, report_data: Dict[str, Any], options: Dict[str, Any]) -> bytes:
        """Fallback PDF generation using reportlab"""
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib import colors
        from reportlab.lib.units import inch
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        
        # Register Chinese font for proper rendering
        try:
            # Try STHeiti Medium (available on macOS)
            chinese_font_path = "/System/Library/Fonts/STHeiti Medium.ttc"
            if os.path.exists(chinese_font_path):
                # For TTC files, we need to specify the font index
                pdfmetrics.registerFont(TTFont('STHeiti', chinese_font_path, subfontIndex=0))
                chinese_font_name = 'STHeiti'
            else:
                # Fallback to Helvetica
                chinese_font_name = 'Helvetica'
        except Exception as e:
            logger.warning(f"Failed to register Chinese font: {e}, using Helvetica")
            chinese_font_name = 'Helvetica'
        
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=landscape(A4),
            leftMargin=36,
            rightMargin=36,
            topMargin=36,
            bottomMargin=30,
        )
        story = []
        
        styles = getSampleStyleSheet()
        
        # Update styles to use Chinese font
        if chinese_font_name == 'STHeiti':
            styles['Normal'].fontName = 'STHeiti'
            styles['BodyText'].fontName = 'STHeiti'
            styles['Heading1'].fontName = 'STHeiti'
            styles['Heading2'].fontName = 'STHeiti'
            styles['Italic'].fontName = 'STHeiti'
        
        # Add title
        title = report_data.get("title", "Report")
        title_style = ParagraphStyle('CustomTitle', parent=styles['Heading1'], fontSize=18, textColor=colors.HexColor('#111827'))
        story.append(Paragraph(title, title_style))
        story.append(Spacer(1, 12))
        
        # Render blocks
        for block in report_data.get("blocks", []):
            block_type = block.get("type")
            
            if block_type == "text":
                # Add text content
                content = block.get("content", "")
                if content:
                    story.append(Paragraph(content, styles['BodyText']))
                    story.append(Spacer(1, 12))
            
            elif block_type == "kpi_cards":
                title = block.get("title", "关键指标")
                story.append(Paragraph(title, ParagraphStyle('SectionTitle', parent=styles['Heading2'], fontSize=12, spaceAfter=8)))
                period_label = block.get("period_label")
                if period_label:
                    story.append(
                        Paragraph(
                            f'<font size="8" color="#94a3b8"><i>Period: {self._escape_html(str(period_label))}</i></font>',
                            styles['BodyText'],
                        )
                    )
                    story.append(Spacer(1, 8))
                
                cards = block.get("cards", []) or block.get("data", [])
                
                if cards:
                    cards_per_row = 4
                    card_rows = []
                    card_style = ParagraphStyle(
                        'KpiCardText',
                        parent=styles['BodyText'],
                        leading=12,
                        spaceAfter=0,
                    )
                    
                    for i in range(0, len(cards), cards_per_row):
                        row_cards = cards[i:i + cards_per_row]
                        card_data = []
                        
                        for card in row_cards:
                            view_model = self._build_kpi_card_view_model(card)
                            change_color = '#0f766e' if view_model['trend'] == 'up' else '#a63d2a' if view_model['trend'] == 'down' else '#6b7280'
                            secondary_label = (
                                f' <font size="6" color="#7b808a">{self._escape_html(view_model["secondary_label"].upper())}</font>'
                                if view_model['secondary_label']
                                else ''
                            )
                            unit_markup = (
                                f' <font size="7" color="#6b7280">{self._escape_html(view_model["unit"])}</font>'
                                if view_model['unit']
                                else ''
                            )
                            range_markup = ''
                            if view_model['range_items']:
                                range_markup = '<br/><br/><font size="6.5" color="#6b7280">' + '   '.join(
                                    f'{self._escape_html(item["label"])} {self._escape_html(item["value"])}'
                                    for item in view_model['range_items']
                                ) + '</font>'

                            card_text = (
                                f'<font size="9" color="#374151"><b>{self._escape_html(view_model["primary_label"])}</b></font>'
                                f'{secondary_label}<br/><br/>'
                                f'<font size="18" color="#111827"><b>{self._escape_html(view_model["value_display"])}</b></font>'
                                f'{unit_markup}<br/><br/>'
                                f'<font size="10" color="{change_color}"><b>{view_model["arrow"]} {self._escape_html(view_model["change_text"])}</b></font>'
                                f' <font size="6.5" color="#6b7280">{self._escape_html(view_model["comparison"])}</font>'
                                f'{range_markup}'
                            )

                            card_data.append(Paragraph(card_text, card_style))
                        
                        # Pad row if needed
                        while len(card_data) < cards_per_row:
                            card_data.append(Paragraph("", card_style))
                        
                        card_rows.append(card_data)
                    
                    for card_row in card_rows:
                        col_width = doc.width / cards_per_row
                        
                        kpi_table = Table([card_row], colWidths=[col_width] * cards_per_row)
                        kpi_table.setStyle(TableStyle([
                            ('BACKGROUND', (0, 0), (-1, -1), colors.white),
                            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                            ('LEFTPADDING', (0, 0), (-1, -1), 14),
                            ('RIGHTPADDING', (0, 0), (-1, -1), 14),
                            ('TOPPADDING', (0, 0), (-1, -1), 14),
                            ('BOTTOMPADDING', (0, 0), (-1, -1), 14),
                            ('LINEABOVE', (0, 0), (-1, -1), 1, colors.HexColor('#d6ccb7')),
                            ('LINEBELOW', (0, 0), (-1, -1), 1, colors.HexColor('#d6ccb7')),
                            ('INNERGRID', (0, 0), (-1, -1), 0.8, colors.HexColor('#e8ddc8')),
                        ]))
                        story.append(kpi_table)
                        story.append(Spacer(1, 12))
                
                story.append(Spacer(1, 12))
            
            elif block_type == "data_table":
                # Add table title if present
                title = block.get("title")
                if title:
                    story.append(Paragraph(title, ParagraphStyle('TableTitle', parent=styles['Heading2'], fontSize=14)))
                    story.append(Spacer(1, 6))
                
                table_data = self._prepare_reportlab_table(block)
                if table_data:
                    t = Table(table_data)
                    
                    # Use Chinese font if available, otherwise fall back to Helvetica
                    header_font = chinese_font_name if chinese_font_name == 'STHeiti' else 'Helvetica-Bold'
                    data_font = chinese_font_name if chinese_font_name == 'STHeiti' else 'Helvetica'
                    
                    t.setStyle(TableStyle([
                        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e40af')),
                        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                        ('FONTNAME', (0, 0), (-1, 0), header_font),  # Header font
                        ('FONTNAME', (0, 1), (-1, -1), data_font),  # Data rows font - supports Chinese
                        ('FONTSIZE', (0, 0), (-1, 0), 10),
                        ('FONTSIZE', (0, 1), (-1, -1), 9),  # Data rows slightly smaller
                        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                        ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                        ('GRID', (0, 0), (-1, -1), 1, colors.grey)
                    ]))
                    story.append(t)
                    story.append(Spacer(1, 12))
            
            elif block_type == "chart":
                # Add chart title
                title = block.get("title", "Chart")
                story.append(Paragraph(f"<b>{title}</b>", ParagraphStyle('ChartTitle', parent=styles['Heading2'], fontSize=14)))
                story.append(Spacer(1, 6))
                
                # Generate chart image
                chart_img = self._generate_chart_image(block)
                if chart_img:
                    from reportlab.platypus import Image
                    # Add chart image with controlled size
                    img = Image(chart_img, width=450, height=250)
                    story.append(img)
                else:
                    # Fallback if chart generation fails
                    story.append(Paragraph("[Chart visualization - see online version]", styles['Italic']))
                
                story.append(Spacer(1, 12))

            elif block_type == "image":
                title = block.get("title")
                if title:
                    story.append(Paragraph(title, ParagraphStyle('ImageTitle', parent=styles['Heading2'], fontSize=14)))
                    story.append(Spacer(1, 6))

                image_stream = self._load_image_stream(block.get("image_url", ""))
                if image_stream:
                    from reportlab.platypus import Image

                    img = Image(image_stream)
                    img._restrictSize(6.5 * inch, 4.5 * inch)
                    story.append(img)
                    caption = block.get("caption")
                    if caption:
                        story.append(Spacer(1, 6))
                        story.append(Paragraph(caption, styles['Italic']))
                else:
                    story.append(Paragraph("[Image unavailable in export]", styles['Italic']))

                story.append(Spacer(1, 12))

            elif block_type == "custom_sql":
                # Add block title
                block_title = block.get("title") or block.get("rendered", {}).get("title", "Custom Query")
                story.append(Paragraph(block_title, ParagraphStyle('TableTitle', parent=styles['Heading2'], fontSize=14)))
                story.append(Spacer(1, 6))

                # Columns and rows may be at top level (after enrichment) or inside rendered
                rendered = block.get("rendered", {}) or {}
                columns = block.get("columns") or rendered.get("columns") or []
                rows = block.get("rows") or rendered.get("rows") or []

                if block.get("error") or rendered.get("error"):
                    err_msg = block.get("error") or rendered.get("error", "Query error")
                    story.append(Paragraph(f"Query Error: {err_msg}", ParagraphStyle('ErrStyle', parent=styles['BodyText'], textColor=colors.HexColor('#991b1b'))))
                elif columns and rows:
                    # Build table data: header row + data rows
                    header = [str(col.get("label", col.get("key", "")) if isinstance(col, dict) else col) for col in columns]
                    table_data = [header]
                    for row in rows:
                        if isinstance(row, dict):
                            table_data.append([str(row.get(col.get("key", col) if isinstance(col, dict) else col, "")) for col in columns])
                        else:
                            table_data.append([str(v) for v in row])

                    header_font = chinese_font_name if chinese_font_name == 'STHeiti' else 'Helvetica-Bold'
                    data_font = chinese_font_name if chinese_font_name == 'STHeiti' else 'Helvetica'

                    sql_table = Table(table_data, repeatRows=1)
                    sql_table.setStyle(TableStyle([
                        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e40af')),
                        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                        ('FONTNAME', (0, 0), (-1, 0), header_font),
                        ('FONTNAME', (0, 1), (-1, -1), data_font),
                        ('FONTSIZE', (0, 0), (-1, 0), 10),
                        ('FONTSIZE', (0, 1), (-1, -1), 9),
                        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                        ('LEFTPADDING', (0, 0), (-1, -1), 6),
                        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
                        ('TOPPADDING', (0, 0), (-1, -1), 4),
                        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                        ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')]),
                        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
                    ]))
                    story.append(sql_table)
                else:
                    story.append(Paragraph("No data available", styles['Italic']))

                story.append(Spacer(1, 12))

        doc.build(story)
        return buffer.getvalue()
    
    def _generate_chart_image(self, block: Dict[str, Any]) -> BytesIO:
        """Generate chart image using matplotlib"""
        try:
            import matplotlib
            matplotlib.use('Agg')  # Use non-interactive backend
            import matplotlib.pyplot as plt
            import matplotlib.dates as mdates
            from datetime import datetime
            
            # Configure Chinese font for matplotlib
            try:
                import matplotlib.font_manager as fm
                # Use STHeiti for Chinese characters
                chinese_font_path = "/System/Library/Fonts/STHeiti Medium.ttc"
                if os.path.exists(chinese_font_path):
                    plt.rcParams['font.sans-serif'] = ['STHeiti', 'Arial Unicode MS', 'SimHei']
                    plt.rcParams['axes.unicode_minus'] = False  # Fix minus sign display
            except Exception as e:
                logger.warning(f"Failed to set Chinese font for matplotlib: {e}")
            
            series_data = block.get("series", [])
            if not series_data:
                logger.warning("No series data for chart")
                return None
            
            # Create figure
            fig, ax = plt.subplots(figsize=(10, 5))
            
            # Colors for different series
            colors = ['#ef4444', '#10b981', '#3b82f6', '#f59e0b', '#8b5cf6', '#ec4899']
            
            # Plot each series
            for idx, series in enumerate(series_data):
                name = series.get("name", f"Series {idx + 1}")
                data_points = series.get("data", [])
                
                if not data_points:
                    continue
                
                # Extract dates and values
                dates = [datetime.fromisoformat(point["date"]) for point in data_points]
                values = [point["value"] for point in data_points]
                
                # Plot line
                color = colors[idx % len(colors)]
                ax.plot(dates, values, marker='o', label=name, color=color, linewidth=2, markersize=5)
            
            # Format chart
            ax.set_xlabel('Date', fontsize=10)
            ax.set_ylabel('Price (¥)', fontsize=10)
            ax.grid(True, alpha=0.3)
            ax.legend(loc='best', fontsize=9)

            # Tighten y-axis around actual data range so small gaps are visible
            all_values = [
                point["value"]
                for series in series_data
                for point in series.get("data", [])
                if isinstance(point.get("value"), (int, float)) and point["value"] == point["value"]
            ]
            if all_values:
                y_min, y_max = min(all_values), max(all_values)
                y_range = y_max - y_min if y_max != y_min else max(abs(y_max) * 0.1, 100)
                pad = y_range * 0.12
                ax.set_ylim(y_min - pad, y_max + pad)

            # Format x-axis dates
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
            plt.xticks(rotation=45)

            # Tight layout
            plt.tight_layout()
            
            # Save to buffer
            img_buffer = BytesIO()
            plt.savefig(img_buffer, format='png', dpi=100, bbox_inches='tight')
            plt.close(fig)
            
            img_buffer.seek(0)
            return img_buffer
            
        except Exception as e:
            logger.error(f"Failed to generate chart image: {e}", exc_info=True)
            return None
    
    def _prepare_reportlab_table(self, block: Dict[str, Any]) -> List[List[str]]:
        """Prepare table data for reportlab"""
        columns = block.get("columns", [])
        rows = block.get("rows", [])
        
        # Skip if no columns
        if not columns:
            logger.warning(f"Skipping table with no columns. Block keys: {list(block.keys())}")
            return None
        
        # Header row
        header = [col if isinstance(col, str) else col.get("label", "") for col in columns]
        table_data = [header]
        
        # Data rows
        for row in rows:
            row_data = []
            for col in columns:
                col_key = col if isinstance(col, str) else col.get("key", "")
                value = row.get(col_key, "/")
                row_data.append(str(value))
            table_data.append(row_data)
        
        return table_data if len(table_data) > 1 else None
    
    def _export_to_docx(self, report_data: Dict[str, Any], options: Dict[str, Any]) -> bytes:
        """Export to Microsoft Word format"""
        try:
            from docx import Document
            from docx.shared import Inches, Pt, RGBColor
            from docx.enum.text import WD_ALIGN_PARAGRAPH
            from docx.enum.section import WD_ORIENT
            
            doc = Document()
            section = doc.sections[0]
            section.orientation = WD_ORIENT.LANDSCAPE
            section.page_width, section.page_height = section.page_height, section.page_width
            section.left_margin = Inches(0.45)
            section.right_margin = Inches(0.45)
            section.top_margin = Inches(0.5)
            section.bottom_margin = Inches(0.5)
            
            # Add title
            title = doc.add_heading(report_data.get("title", "Report"), 0)
            title.alignment = WD_ALIGN_PARAGRAPH.CENTER
            
            # Render blocks
            for block in report_data.get("blocks", []):
                block_type = block.get("type")
                
                if block_type == "kpi_cards":
                    self._add_docx_kpi_cards(doc, block)
                elif block_type == "data_table":
                    self._add_docx_table(doc, block)
                elif block_type == "chart":
                    self._add_docx_chart(doc, block)
                elif block_type == "image":
                    self._add_docx_image(doc, block)
                elif block_type == "text":
                    content = block.get("content", "")
                    doc.add_paragraph(content)
            
            buffer = BytesIO()
            doc.save(buffer)
            return buffer.getvalue()
            
        except ImportError:
            logger.error("python-docx not available. Install: pip install python-docx")
            raise
    
    def _add_docx_chart(self, doc, block: Dict[str, Any]):
        """Add chart to DOCX document as a matplotlib-rendered image."""
        from docx.shared import Inches

        if block.get("title"):
            doc.add_heading(block["title"], level=2)

        img_buffer = self._generate_chart_image(block)
        if img_buffer:
            doc.add_picture(img_buffer, width=Inches(6.5))
        else:
            doc.add_paragraph("[图表无法在导出中渲染 / Chart could not be rendered in export]")

    def _add_docx_table(self, doc, block: Dict[str, Any]):
        """Add table to DOCX document"""
        columns = block.get("columns", [])
        rows = block.get("rows", [])
        
        if block.get("title"):
            doc.add_heading(block["title"], level=2)
        
        table = doc.add_table(rows=len(rows) + 1, cols=len(columns))
        table.style = 'Light Grid Accent 1'
        
        # Header
        header_cells = table.rows[0].cells
        for i, col in enumerate(columns):
            col_label = col if isinstance(col, str) else col.get("label", "")
            header_cells[i].text = col_label
        
        # Data rows
        for row_idx, row in enumerate(rows):
            row_cells = table.rows[row_idx + 1].cells
            for col_idx, col in enumerate(columns):
                col_key = col if isinstance(col, str) else col.get("key", "")
                value = row.get(col_key, "/")
                row_cells[col_idx].text = str(value)

    def _add_docx_kpi_cards(self, doc, block: Dict[str, Any]):
        """Add KPI cards to DOCX using a wide table layout."""
        from docx.enum.table import WD_ALIGN_VERTICAL
        from docx.shared import Pt, RGBColor

        if block.get("title"):
            doc.add_heading(block["title"], level=2)

        period_label = block.get("period_label")
        if period_label:
            period_paragraph = doc.add_paragraph()
            period_run = period_paragraph.add_run(f"Period: {period_label}")
            period_run.italic = True
            period_run.font.size = Pt(8)
            period_run.font.color.rgb = RGBColor(148, 163, 184)

        cards = block.get("cards", []) or block.get("data", [])
        if not cards:
            return

        cards_per_row = 4
        row_count = (len(cards) + cards_per_row - 1) // cards_per_row
        table = doc.add_table(rows=row_count, cols=cards_per_row)
        table.style = 'Table Grid'

        for index, card in enumerate(cards):
            cell = table.cell(index // cards_per_row, index % cards_per_row)
            cell.vertical_alignment = WD_ALIGN_VERTICAL.TOP
            view_model = self._build_kpi_card_view_model(card)
            cell.text = ""

            heading_paragraph = cell.paragraphs[0]
            heading_run = heading_paragraph.add_run(view_model["primary_label"])
            heading_run.bold = True
            heading_run.font.size = Pt(10)
            heading_run.font.color.rgb = RGBColor(55, 65, 81)
            if view_model["secondary_label"]:
                secondary_run = heading_paragraph.add_run(f"  {view_model['secondary_label'].upper()}")
                secondary_run.font.size = Pt(7)
                secondary_run.font.color.rgb = RGBColor(123, 128, 138)

            value_paragraph = cell.add_paragraph()
            value_run = value_paragraph.add_run(view_model["value_display"])
            value_run.bold = True
            value_run.font.size = Pt(18)
            value_run.font.color.rgb = RGBColor(17, 24, 39)
            if view_model["unit"]:
                unit_run = value_paragraph.add_run(f" {view_model['unit']}")
                unit_run.font.size = Pt(7)
                unit_run.font.color.rgb = RGBColor(107, 114, 128)

            change_paragraph = cell.add_paragraph()
            change_color = RGBColor(15, 118, 110) if view_model["trend"] == "up" else RGBColor(166, 61, 42) if view_model["trend"] == "down" else RGBColor(107, 114, 128)
            change_run = change_paragraph.add_run(f"{view_model['arrow']} {view_model['change_text']}")
            change_run.bold = True
            change_run.font.size = Pt(10)
            change_run.font.color.rgb = change_color
            comparison_run = change_paragraph.add_run(f"  {view_model['comparison']}")
            comparison_run.font.size = Pt(7)
            comparison_run.font.color.rgb = RGBColor(107, 114, 128)

            if view_model["range_items"]:
                meta_paragraph = cell.add_paragraph()
                meta_run = meta_paragraph.add_run("   ".join(f"{item['label']} {item['value']}" for item in view_model["range_items"]))
                meta_run.font.size = Pt(7)
                meta_run.font.color.rgb = RGBColor(107, 114, 128)

        doc.add_paragraph()

    def _add_docx_image(self, doc, block: Dict[str, Any]):
        """Add image to DOCX document"""
        from docx.shared import Inches

        if block.get("title"):
            doc.add_heading(block["title"], level=2)

        image_stream = self._load_image_stream(block.get("image_url", ""))
        if image_stream:
            doc.add_picture(image_stream, width=Inches(6.0))
        else:
            doc.add_paragraph("[Image unavailable in export]")

        if block.get("caption"):
            doc.add_paragraph(block["caption"])
    
    def _export_to_pptx(self, report_data: Dict[str, Any], options: Dict[str, Any]) -> bytes:
        """Export to PowerPoint format"""
        try:
            from pptx import Presentation
            from pptx.util import Inches, Pt
            
            prs = Presentation()
            
            # Title slide
            title_slide_layout = prs.slide_layouts[0]
            slide = prs.slides.add_slide(title_slide_layout)
            title = slide.shapes.title
            subtitle = slide.placeholders[1]
            
            title.text = report_data.get("title", "Report")
            subtitle.text = f"生成时间: {report_data.get('generated_at', '')}"
            
            # Content slides
            for block in report_data.get("blocks", []):
                if block.get("type") == "data_table":
                    self._add_pptx_table(prs, block)
                elif block.get("type") == "image":
                    self._add_pptx_image(prs, block)
            
            buffer = BytesIO()
            prs.save(buffer)
            return buffer.getvalue()
            
        except ImportError:
            logger.error("python-pptx not available. Install: pip install python-pptx")
            raise
    
    def _add_pptx_table(self, prs, block: Dict[str, Any]):
        """Add table to PowerPoint slide"""
        columns = block.get("columns", [])
        rows = block.get("rows", [])
        
        # Add slide
        slide_layout = prs.slide_layouts[5]  # Blank layout
        slide = prs.slides.add_slide(slide_layout)
        
        # Add title if present
        if block.get("title"):
            title_shape = slide.shapes.title
            title_shape.text = block["title"]
        
        # Add table
        from pptx.util import Inches
        rows_count = len(rows) + 1  # +1 for header
        cols_count = len(columns)
        
        left = Inches(0.5)
        top = Inches(1.5)
        width = Inches(9)
        height = Inches(0.3) * rows_count
        
        table = slide.shapes.add_table(rows_count, cols_count, left, top, width, height).table
        
        # Header
        for col_idx, col in enumerate(columns):
            col_label = col if isinstance(col, str) else col.get("label", "")
            table.cell(0, col_idx).text = col_label
        
        # Data
        for row_idx, row in enumerate(rows):
            for col_idx, col in enumerate(columns):
                col_key = col if isinstance(col, str) else col.get("key", "")
                value = row.get(col_key, "/")
                table.cell(row_idx + 1, col_idx).text = str(value)

    def _add_pptx_image(self, prs, block: Dict[str, Any]):
        """Add image block to PowerPoint slide"""
        from pptx.util import Inches

        slide_layout = prs.slide_layouts[5]
        slide = prs.slides.add_slide(slide_layout)

        if block.get("title") and slide.shapes.title is not None:
            slide.shapes.title.text = block["title"]

        image_stream = self._load_image_stream(block.get("image_url", ""))
        if image_stream:
            slide.shapes.add_picture(image_stream, Inches(0.75), Inches(1.25), width=Inches(8.0))

        if block.get("caption"):
            caption_box = slide.shapes.add_textbox(Inches(0.75), Inches(6.8), Inches(8.5), Inches(0.6))
            caption_box.text_frame.text = block["caption"]


# Singleton instance
_export_service: Optional[ReportExportService] = None


def get_export_service() -> ReportExportService:
    """Get or create the export service singleton"""
    global _export_service
    if _export_service is None:
        _export_service = ReportExportService()
    return _export_service
