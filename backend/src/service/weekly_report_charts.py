"""
weekly_report_charts.py
-----------------------
Matplotlib chart renderer for the C5/C9 weekly report.

Style: Bloomberg/Reuters terminal-inspired dark-navy theme (spec §6.2).
Font: Noto Sans CJK SC (required for Chinese labels — tofu prevention).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Style constants (spec §6.2)
# ---------------------------------------------------------------------------

EDIA_CHART_STYLE = {
    "axes.facecolor": "#0B1929",
    "figure.facecolor": "#0B1929",
    "axes.edgecolor": "#3A4A5C",
    "axes.labelcolor": "#E8E8E8",
    "xtick.color": "#9AA5B4",
    "ytick.color": "#9AA5B4",
    "grid.color": "#1F2D3D",
    "grid.linestyle": ":",
    "axes.titlesize": 13,
    "axes.titleweight": "bold",
    "axes.titlecolor": "#E8E8E8",
    "legend.facecolor": "#0B1929",
    "legend.edgecolor": "#3A4A5C",
    "legend.labelcolor": "#E8E8E8",
    "legend.fontsize": 10,
}

SERIES_COLORS = {
    "erp_weighted": "#D4A437",   # gold
    "lz_prevailing": "#00A6A6",  # teal
    "sinopec_listed": "#E8E8E8", # off-white
    "forecast_band": "#D4A437",  # gold @ alpha=0.15
}

_SERIES_LINEWIDTH = 2.2
_SERIES_MARKERSIZE = 5.5
_LEGEND_TOP_MARGIN = 0.8
_TITLE_PAD = 2


def _legend_columns(item_count: int) -> int:
    return item_count or 1


def _place_legend(ax: Any, legend_handles: List[Any]) -> None:
    if not legend_handles:
        return
    labels = [handle.get_label() for handle in legend_handles]
    ax.legend(
        legend_handles,
        labels,
        loc="lower left",
        bbox_to_anchor=(0.0, 1.09, 1.0, 0.2),
        ncol=_legend_columns(len(labels)),
        mode="expand",
        framealpha=0.82,
        borderaxespad=0.0,
        handlelength=2.4,
        columnspacing=1.2,
        markerscale=1.0,
    )


def _finalize_layout(fig: Any) -> None:
    fig.tight_layout(rect=(0.0, 0.0, 1.0, _LEGEND_TOP_MARGIN))


def _to_optional_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _configure_font() -> None:
    """Set CJK-capable font for Chinese label rendering."""
    import matplotlib as mpl
    # Try common font paths
    candidate_paths = [
        "/usr/share/fonts/truetype/alibaba-puhuiti/AlibabaPuHuiTi-3-55-Regular.ttf",
        "/usr/share/fonts/truetype/alibaba/Alibaba-PuHuiTi-Regular.ttf",
        "/usr/share/fonts/opentype/source-han-sans/SourceHanSansSC-Regular.otf",
        "/usr/share/fonts/adobe-source-han-sans/SourceHanSansSC-Regular.otf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
    ]
    for path in candidate_paths:
        if os.path.exists(path):
            from matplotlib import font_manager
            font_manager.fontManager.addfont(path)
            prop = font_manager.FontProperties(fname=path)
            mpl.rcParams["font.family"] = prop.get_name()
            return

    # Try font names already known to matplotlib
    for fname in [
        "Alibaba PuHuiTi",
        "Alibaba PuHuiTi 2.0",
        "Source Han Sans SC",
        "思源黑体",
        "Noto Sans CJK SC",
        "SimHei",
        "STSong",
        "PingFang SC",
        "Arial Unicode MS",
    ]:
        try:
            from matplotlib import font_manager
            if font_manager.findfont(fname, fallback_to_default=False):
                mpl.rcParams["font.family"] = fname
                return
        except Exception:
            continue

    logger.warning(
        "No CJK font found — Chinese labels may render as tofu squares. "
        "Install Alibaba PuHuiTi, Source Han Sans SC, or noto-fonts-cjk."
    )


# Initialise style and font once at module load
def _init_style() -> None:
    try:
        import matplotlib as mpl
        import matplotlib.pyplot as plt  # noqa: F401
        mpl.use("Agg")  # non-interactive backend (no display needed)
        for k, v in EDIA_CHART_STYLE.items():
            try:
                mpl.rcParams[k] = v
            except Exception:
                pass
        _configure_font()
    except ImportError:
        logger.warning("matplotlib not available — charts will be skipped.")


_init_style()


# ---------------------------------------------------------------------------
# Main chart function
# ---------------------------------------------------------------------------

def render_product_chart(
    ym_list: list[str],
    lz_avg: list[Optional[float]],
    erp_wavg: list[Optional[float]],
    product_name_zh: str,
    slug: str,
    report_id: str,
    charts_dir: Path,
    forecast_low: Optional[float] = None,
    forecast_high: Optional[float] = None,
    language: str = "zh",
) -> Optional[Path]:
    """
    Render a 12-month price trend chart and save to charts_dir/{slug}_12mo.png.

    Returns the Path to the saved PNG, or None if rendering failed.
    Spec §6.2: 1200×600px @ 150 DPI, Bloomberg dark-navy style.
    """
    try:
        import matplotlib.pyplot as plt
        import matplotlib.ticker as mticker

        charts_dir.mkdir(parents=True, exist_ok=True)
        out_path = charts_dir / f"{slug}_12mo.png"
        is_en = (language or "zh").lower().startswith("en")

        fig, ax = plt.subplots(figsize=(8, 4), dpi=150)
        has_any_data = False

        lz_points = [(ym, v) for ym, v in zip(ym_list, lz_avg) if v is not None]
        if lz_points:
            xs, ys = zip(*lz_points)
            ax.plot(
                xs,
                ys,
                color=SERIES_COLORS["lz_prevailing"],
                linewidth=_SERIES_LINEWIDTH,
                marker="o",
                markersize=_SERIES_MARKERSIZE,
                markeredgewidth=0.0,
                label="LZ Market Avg" if is_en else "隆众市场均价",
            )
            has_any_data = True

        erp_points = [(ym, v) for ym, v in zip(ym_list, erp_wavg) if v is not None]
        if erp_points:
            xs, ys = zip(*erp_points)
            ax.plot(
                xs,
                ys,
                color=SERIES_COLORS["erp_weighted"],
                linewidth=_SERIES_LINEWIDTH,
                marker="s",
                markersize=_SERIES_MARKERSIZE,
                markeredgewidth=0.0,
                label="ERP Weighted Avg" if is_en else "ERP 加权均价",
            )
            has_any_data = True

        if not has_any_data:
            ax.text(
                0.5,
                0.5,
                "No price data this week" if is_en else "本周暂无价格数据",
                transform=ax.transAxes,
                ha="center",
                va="center",
                fontsize=14,
                color="#E8E8E8",
            )

        if forecast_low is not None and forecast_high is not None and ym_list:
            try:
                idx = len(ym_list) - 1
                ax.axhspan(
                    forecast_low,
                    forecast_high,
                    xmin=max(0.0, (idx - 0.3) / max(len(ym_list) - 1, 1)),
                    xmax=1.0,
                    color=SERIES_COLORS["forecast_band"],
                    alpha=0.15,
                    label=(
                        f"Forecast Range {forecast_low:,.0f}-{forecast_high:,.0f}"
                        if is_en
                        else f"预测区间 {forecast_low:,.0f}-{forecast_high:,.0f}"
                    ),
                )
            except Exception:
                pass

        if ym_list:
            last_idx = len(ym_list) - 1
            ax.axvline(
                x=ym_list[last_idx],
                color="#E8E8E8",
                linestyle=":",
                linewidth=1.0,
                alpha=0.6,
            )
            ax.text(
                ym_list[last_idx],
                ax.get_ylim()[1] if has_any_data else 0,
                "  This Week" if is_en else "  本周",
                color="#E8E8E8",
                fontsize=9,
                va="bottom",
            )

        type_label = "12-Month Price Trend (CNY/t)" if is_en else "近12月价格走势（元/吨）"
        ax.set_title(product_name_zh, fontsize=13, fontweight="bold", color="#E8E8E8", loc="left", pad=_TITLE_PAD)
        ax.set_title(type_label, fontsize=10, color="#9AA5B4", loc="center", pad=_TITLE_PAD)
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))
        ax.set_xlabel("")

        plt.xticks(rotation=35, ha="right", fontsize=8)
        plt.yticks(fontsize=8)
        ax.grid(True, axis="y", alpha=0.4)
        ax.grid(True, axis="x", alpha=0.2)

        if has_any_data:
            _place_legend(ax, ax.lines)

        _finalize_layout(fig)
        plt.savefig(str(out_path), dpi=150, bbox_inches="tight")
        plt.close(fig)
        return out_path
    except Exception as e:
        logger.error("Chart render failed for %s (%s): %s", slug, product_name_zh, e, exc_info=True)
        return None


def render_product_monthly_chart(
    date_list: list,
    lz_daily: list,
    erp_daily: list,
    product_name_zh: str,
    slug: str,
    report_id: str,
    charts_dir: "Path",
    week_start: Optional[str] = None,
    week_end: Optional[str] = None,
    language: str = "zh",
) -> Optional["Path"]:
    """
    Render a daily price chart for the current month and save to
    charts_dir/{slug}_monthly.png.

    X-axis: daily dates (MM/DD), same Bloomberg dark-navy style.
    Highlights the report-week span with a subtle vertical band.
    Returns the Path to the saved PNG, or None on failure.
    """
    try:
        import matplotlib.pyplot as plt
        import matplotlib.ticker as mticker
        from datetime import datetime as _dt

        charts_dir.mkdir(parents=True, exist_ok=True)
        out_path = charts_dir / f"{slug}_monthly.png"
        is_en = (language or "zh").lower().startswith("en")

        lz_label = "LZ Market Avg" if is_en else "隆众市场均价"
        erp_label = "ERP Weighted Avg" if is_en else "ERP 加权均价"
        no_data_label = "No price data this month" if is_en else "本月暂无价格数据"
        week_label = "  Report Week" if is_en else "  本周"
        title_suffix = "Daily Prices This Month (CNY/t)" if is_en else "本月日价格走势（元/吨）"

        fig, ax = plt.subplots(figsize=(8, 4), dpi=150)
        has_any_data = False

        def _parse_any_date(value: str):
            for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
                try:
                    return _dt.strptime(str(value), fmt)
                except Exception:
                    continue
            return None

        rows = []
        for d, lz_v, erp_v in zip(date_list, lz_daily, erp_daily):
            parsed = _parse_any_date(d)
            if parsed is not None:
                rows.append((parsed, lz_v, erp_v))

        rows.sort(key=lambda x: x[0])
        x_labels = [r[0].strftime("%m/%d") for r in rows]
        lz_vals = [r[1] for r in rows]
        erp_vals = [r[2] for r in rows]
        n_points = len(x_labels)

        if rows:
            month_label = rows[0][0].strftime("%Y-%m")
            title_suffix = (
                f"Daily Prices ({month_label}, CNY/t)"
                if is_en
                else f"{month_label}日价格走势（元/吨）"
            )

        lz_xs = [i for i, v in enumerate(lz_vals) if v is not None]
        lz_ys = [v for v in lz_vals if v is not None]
        if lz_xs:
            ax.plot(
                lz_xs,
                lz_ys,
                color=SERIES_COLORS["lz_prevailing"],
                linewidth=_SERIES_LINEWIDTH,
                marker="o",
                markersize=_SERIES_MARKERSIZE,
                markeredgewidth=0.0,
                label=lz_label,
            )
            has_any_data = True

        erp_xs = [i for i, v in enumerate(erp_vals) if v is not None]
        erp_ys = [v for v in erp_vals if v is not None]
        if erp_xs:
            ax.plot(
                erp_xs,
                erp_ys,
                color=SERIES_COLORS["erp_weighted"],
                linewidth=_SERIES_LINEWIDTH,
                marker="s",
                markersize=_SERIES_MARKERSIZE,
                markeredgewidth=0.0,
                label=erp_label,
            )
            has_any_data = True

        if not has_any_data:
            ax.text(
                0.5,
                0.5,
                no_data_label,
                transform=ax.transAxes,
                ha="center",
                va="center",
                fontsize=14,
                color="#E8E8E8",
            )

        if week_start and week_end and rows:
            try:
                ws_dt = _dt.strptime(week_start, "%Y-%m-%d")
                we_dt = _dt.strptime(week_end, "%Y-%m-%d")
                week_indices = [
                    idx for idx, (day_dt, _, _) in enumerate(rows)
                    if ws_dt.date() <= day_dt.date() <= we_dt.date()
                ]
                if week_indices:
                    wi_start = week_indices[0]
                    wi_end = week_indices[-1]
                    ax.axvspan(wi_start - 0.5, wi_end + 0.5, color="#2A4A7A", alpha=0.35)
                    ax.text(
                        wi_end,
                        ax.get_ylim()[1] if has_any_data else 0,
                        week_label,
                        color="#E8E8E8",
                        fontsize=9,
                        va="bottom",
                    )
            except Exception:
                pass

        ax.set_title(product_name_zh, fontsize=13, fontweight="bold", color="#E8E8E8", loc="left", pad=_TITLE_PAD)
        ax.set_title(title_suffix, fontsize=10, color="#9AA5B4", loc="center", pad=_TITLE_PAD)
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))
        ax.set_xlabel("")

        if x_labels:
            tick_step = max(1, n_points // 10)
            tick_positions = list(range(0, n_points, tick_step))
            ax.set_xticks(tick_positions)
            ax.set_xticklabels([x_labels[i] for i in tick_positions], rotation=35, ha="right", fontsize=8)
            ax.set_xlim(-0.5, n_points - 0.5)

        plt.yticks(fontsize=8)
        ax.grid(True, axis="y", alpha=0.4)
        ax.grid(True, axis="x", alpha=0.2)

        if has_any_data:
            _place_legend(ax, ax.lines)

        _finalize_layout(fig)
        plt.savefig(str(out_path), dpi=150, bbox_inches="tight")
        plt.close(fig)
        return out_path
    except Exception as e:
        logger.error("Monthly chart render failed for %s (%s): %s", slug, product_name_zh, e, exc_info=True)
        return None


def _render_snapshot_chart(
    product_payload: Dict[str, Any],
    chart: Dict[str, Any],
    chart_type: str,
    report_id: str,
    charts_dir: Path,
    language: str = "zh",
) -> Optional[Path]:
    try:
        import matplotlib.pyplot as plt
        import matplotlib.ticker as mticker
        from datetime import datetime as _dt

        charts_dir.mkdir(parents=True, exist_ok=True)
        slug = str(product_payload.get("product_slug") or "chart")
        out_path = charts_dir / f"{slug}_{'12mo' if chart_type == 'yearly' else 'monthly'}.png"
        product_name = str(
            product_payload.get("product_name_zh")
            or product_payload.get("product_name_en")
            or slug
        )
        is_en = (language or "zh").lower().startswith("en")

        series_list = [series for series in list(chart.get("series") or []) if isinstance(series, dict) and str(series.get("key") or "")]
        points = [point for point in list(chart.get("points") or []) if isinstance(point, dict)]
        if chart_type == "monthly":
            def _parse_period(point: Dict[str, Any]):
                period = str(point.get("period") or point.get("label") or "")
                for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
                    try:
                        return _dt.strptime(period, fmt)
                    except Exception:
                        continue
                return _dt.min
            points.sort(key=_parse_period)
        else:
            points.sort(key=lambda point: str(point.get("period") or point.get("label") or ""))

        x_labels = [str(point.get("label") or point.get("period") or "") for point in points]
        x_positions = list(range(len(points)))
        no_data_label = "No price data available" if is_en else "暂无价格数据"
        week_label = "  Report Week" if is_en else "  本周"

        fig, ax = plt.subplots(figsize=(8, 4), dpi=150)
        ax_right = None
        legend_handles: List[Any] = []
        has_any_data = False
        markers = ["o", "s", "^", "D", "P", "X"]

        for index, series in enumerate(series_list):
            target_ax = ax_right if str(series.get("axis") or "left") == "right" else ax
            if target_ax is None and str(series.get("axis") or "left") == "right":
                ax_right = ax.twinx()
                target_ax = ax_right
                ax_right.tick_params(axis="y", colors="#9AA5B4")
                ax_right.spines["right"].set_color("#3A4A5C")
            values = [_to_optional_float(point.get(str(series.get("key")))) for point in points]
            xs = [x for x, value in zip(x_positions, values) if value is not None]
            ys = [value for value in values if value is not None]
            if not xs:
                continue
            handle = target_ax.plot(
                xs,
                ys,
                color=str(series.get("color") or "#1890ff"),
                linewidth=_SERIES_LINEWIDTH,
                marker=markers[index % len(markers)],
                markersize=_SERIES_MARKERSIZE,
                markeredgewidth=0.0,
                label=str(series.get("label") or series.get("key") or "Series"),
            )[0]
            legend_handles.append(handle)
            has_any_data = True

        if not has_any_data:
            ax.text(
                0.5,
                0.5,
                no_data_label,
                transform=ax.transAxes,
                ha="center",
                va="center",
                fontsize=14,
                color="#E8E8E8",
            )

        if chart_type == "yearly" and x_positions:
            ax.axvline(x=x_positions[-1], color="#E8E8E8", linestyle=":", linewidth=1.0, alpha=0.6)
            ax.text(x_positions[-1], ax.get_ylim()[1] if has_any_data else 0, week_label, color="#E8E8E8", fontsize=9, va="bottom")

        if chart_type == "monthly" and points:
            highlight = dict(chart.get("highlight") or {})
            start = str(highlight.get("start") or "")
            end = str(highlight.get("end") or "")
            if start and end:
                try:
                    ws_dt = _dt.strptime(start, "%Y-%m-%d")
                    we_dt = _dt.strptime(end, "%Y-%m-%d")
                    hit_indices = []
                    for idx, point in enumerate(points):
                        period = str(point.get("period") or "")
                        dt = _dt.strptime(period[:10], "%Y-%m-%d")
                        if ws_dt.date() <= dt.date() <= we_dt.date():
                            hit_indices.append(idx)
                    if hit_indices:
                        ax.axvspan(hit_indices[0] - 0.5, hit_indices[-1] + 0.5, color="#2A4A7A", alpha=0.35)
                        ax.text(hit_indices[-1], ax.get_ylim()[1] if has_any_data else 0, week_label, color="#E8E8E8", fontsize=9, va="bottom")
                except Exception:
                    pass

        ax.set_title(product_name, fontsize=13, fontweight="bold", color="#E8E8E8", loc="left", pad=_TITLE_PAD)
        ax.set_title(str(chart.get("title") or "Price Trend"), fontsize=10, color="#9AA5B4", loc="center", pad=_TITLE_PAD)
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))
        left_unit = str(chart.get("left_axis_unit") or chart.get("unit") or ("CNY/t" if is_en else "元/吨"))
        ax.set_ylabel(left_unit, fontsize=9, color="#9AA5B4")
        if ax_right is not None:
            right_unit = str(chart.get("right_axis_unit") or "")
            if right_unit:
                ax_right.set_ylabel(right_unit, fontsize=9, color="#9AA5B4")
            ax_right.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))

        if x_labels:
            tick_step = max(1, len(x_labels) // 10)
            tick_positions = list(range(0, len(x_labels), tick_step))
            ax.set_xticks(tick_positions)
            ax.set_xticklabels([x_labels[i] for i in tick_positions], rotation=35, ha="right", fontsize=8)
            ax.set_xlim(-0.5, len(x_labels) - 0.5)

        plt.yticks(fontsize=8)
        ax.grid(True, axis="y", alpha=0.4)
        ax.grid(True, axis="x", alpha=0.2)

        if legend_handles:
            _place_legend(ax, legend_handles)

        _finalize_layout(fig)
        plt.savefig(str(out_path), dpi=150, bbox_inches="tight")
        plt.close(fig)
        return out_path
    except Exception as e:
        logger.error("Snapshot chart render failed for %s/%s: %s", product_payload.get("product_slug"), chart_type, e, exc_info=True)
        return None


def render_chart_from_snapshot_payload(
    product_payload: Dict[str, Any],
    chart_type: str,
    report_id: str,
    charts_dir: Path,
    language: str = "zh",
) -> Optional[Path]:
    """Render a weekly report PNG from a stored chart snapshot payload."""
    chart = dict(product_payload.get(chart_type) or {})
    if chart_type in {"yearly", "monthly"}:
        return _render_snapshot_chart(
            product_payload=product_payload,
            chart=chart,
            chart_type=chart_type,
            report_id=report_id,
            charts_dir=charts_dir,
            language=language,
        )

    raise ValueError(f"Unsupported chart_type: {chart_type}")
