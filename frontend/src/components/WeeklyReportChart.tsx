import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { getChartTheme, rechartsTooltipStyle } from '../lib/chartTheme';
import { useTheme } from '../lib/theme';
import { WeeklyReportProductCharts, WeeklyReportSingleChart } from '../services/api';

interface WeeklyReportChartProps {
  product: WeeklyReportProductCharts;
  chart: WeeklyReportSingleChart;
}

function isChineseText(text: string): boolean {
  return /[\u4e00-\u9fff]/.test(text);
}

function formatAxisValue(value: number | string): string {
  if (typeof value !== 'number') return String(value);
  return value.toLocaleString();
}

function hasRenderableData(chart: WeeklyReportSingleChart): boolean {
  return (chart.points || []).some((point) =>
    chart.series.some((series) => point[series.key] !== null && point[series.key] !== undefined),
  );
}

function findMonthlyHighlight(chart: WeeklyReportSingleChart): { start?: string; end?: string } {
  if (chart.chart_type !== 'monthly' || !chart.highlight?.start || !chart.highlight?.end) {
    return {};
  }

  let startLabel: string | undefined;
  let endLabel: string | undefined;

  for (const point of (chart.points || [])) {
    const period = String(point.period || '');
    if (!startLabel && period >= chart.highlight.start) {
      startLabel = String(point.label || period);
    }
    if (period >= chart.highlight.start && period <= chart.highlight.end) {
      endLabel = String(point.label || period);
    }
  }

  return { start: startLabel, end: endLabel };
}

export const WeeklyReportChart: React.FC<WeeklyReportChartProps> = ({ product, chart: chartData }) => {
  const { isDark } = useTheme();
  const chartTheme = useMemo(() => getChartTheme(isDark), [isDark]);
  const tooltipStyle = useMemo(
    () => rechartsTooltipStyle(chartTheme, isDark),
    [chartTheme, isDark],
  );
  const productName = product.product_name_zh || product.product_name_en || product.product_slug;
  const isZh = useMemo(() => isChineseText(`${productName} ${chartData.title}`), [productName, chartData.title]);
  const hasData = hasRenderableData(chartData);
  const yearlyMarkerLabel = chartData.report_period
    ? (chartData.points || []).find((point) => point.period === chartData.report_period)?.label
    : undefined;
  const monthlyHighlight = findMonthlyHighlight(chartData);
  const forecastBand = chartData.forecast_band;
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [isVisible, setIsVisible] = useState(false);

  useEffect(() => {
    const node = containerRef.current;
    if (!node || isVisible) return;

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setIsVisible(true);
          observer.disconnect();
        }
      },
      { rootMargin: '240px 0px' },
    );

    observer.observe(node);
    return () => observer.disconnect();
  }, [isVisible]);

  const forecastBadge = forecastBand && (forecastBand.low != null || forecastBand.high != null)
    ? `${isZh ? '预测' : 'Forecast'}: ${forecastBand.period || '—'} | ${forecastBand.low ?? '—'} - ${forecastBand.high ?? '—'} ${chartData.unit}`
    : null;
  const weekLabel = isZh ? '本周' : 'This Week';
  const noDataLabel = isZh ? '当前图表暂无价格数据' : 'No price data available for this chart';
  const loadingLabel = isZh ? '图表即将加载…' : 'Chart will load shortly...';
  const refLineColor = isDark ? chartTheme.tick : '#334155';

  return (
    <div
      ref={containerRef}
      className={`my-4 w-full min-w-0 overflow-hidden rounded-lg border shadow-sm ${
        isDark
          ? 'border-slate-700 bg-[#1c1f24]'
          : 'border-slate-200 bg-white'
      }`}
    >
      <div className={`border-b px-4 py-3 ${isDark ? 'border-slate-700' : 'border-slate-200'}`}>
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className={`text-sm font-semibold ${isDark ? 'text-slate-100' : 'text-slate-900'}`}>{productName}</div>
            <div className={`text-xs ${isDark ? 'text-slate-400' : 'text-slate-500'}`}>
              {chartData.title} ({chartData.unit})
            </div>
          </div>
          <div className={`flex flex-wrap items-center justify-end gap-3 pt-0.5 text-[11px] ${isDark ? 'text-slate-300' : 'text-slate-600'}`}>
            {chartData.series.map((series) => (
              <span key={series.key} className="inline-flex items-center gap-1.5 whitespace-nowrap">
                <span
                  className="inline-block h-2.5 w-2.5 rounded-full"
                  style={{ backgroundColor: series.color }}
                />
                <span>{series.label}</span>
              </span>
            ))}
          </div>
        </div>
      </div>

      <div className="relative h-[320px] px-3 py-4">
        {forecastBadge ? (
          <div className="absolute right-4 top-4 z-10 max-w-[70%] rounded-full border border-amber-400/35 bg-amber-300/10 px-3 py-1 text-[10px] text-amber-100 backdrop-blur-sm">
            {forecastBadge}
          </div>
        ) : null}

        {!isVisible ? (
          <div className={`flex h-full items-center justify-center text-sm ${isDark ? 'text-slate-400' : 'text-slate-500'}`}>
            {loadingLabel}
          </div>
        ) : hasData ? (
          <ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={280}>
            <LineChart data={chartData.points} margin={{ top: 8, right: 18, left: 8, bottom: 12 }}>
              <CartesianGrid stroke={chartTheme.grid} strokeDasharray="3 3" vertical={false} />
              <XAxis
                dataKey="label"
                stroke={chartTheme.tick}
                tick={{ fontSize: 11, fill: chartTheme.tick }}
                minTickGap={18}
              />
              <YAxis
                stroke={chartTheme.tick}
                tick={{ fontSize: 11, fill: chartTheme.tick }}
                tickFormatter={formatAxisValue}
                width={72}
              />
              <RechartsTooltip
                contentStyle={tooltipStyle}
                formatter={(value: number | string | null | undefined, name: string) => [
                  value == null ? '—' : formatAxisValue(value),
                  name,
                ]}
              />

              {chartData.chart_type === 'yearly' && yearlyMarkerLabel ? (
                <ReferenceLine
                  x={yearlyMarkerLabel}
                  stroke={refLineColor}
                  strokeDasharray="3 3"
                  label={{ value: weekLabel, position: 'top', fill: refLineColor, fontSize: 11 }}
                />
              ) : null}

              {chartData.chart_type === 'monthly' && monthlyHighlight.start && monthlyHighlight.end ? (
                <ReferenceArea
                  x1={monthlyHighlight.start}
                  x2={monthlyHighlight.end}
                  fill={isDark ? '#2A4A7A' : '#dbeafe'}
                  fillOpacity={0.32}
                />
              ) : null}

              {chartData.chart_type === 'yearly' && forecastBand?.low != null && forecastBand?.high != null && chartData.points.length >= 2 ? (
                <ReferenceArea
                  x1={String(chartData.points[Math.max(chartData.points.length - 2, 0)]?.label || chartData.points[0]?.label || '')}
                  x2={String(chartData.points[chartData.points.length - 1]?.label || '')}
                  y1={forecastBand.low}
                  y2={forecastBand.high}
                  fill="#D4A437"
                  fillOpacity={0.14}
                />
              ) : null}

              {chartData.series.map((series) => (
                <Line
                  key={series.key}
                  type="monotone"
                  dataKey={series.key}
                  name={series.label}
                  stroke={series.color}
                  strokeWidth={2}
                  dot={{ r: 2 }}
                  activeDot={{ r: 4 }}
                  connectNulls={false}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <div className={`flex h-full items-center justify-center text-sm ${isDark ? 'text-slate-300' : 'text-slate-600'}`}>
            {noDataLabel}
          </div>
        )}
      </div>

      {forecastBand?.confidence_label ? (
        <div className={`border-t px-4 py-2 text-[11px] ${isDark ? 'border-slate-700 text-slate-400' : 'border-slate-200 text-slate-500'}`}>
          {isZh ? '预测置信度' : 'Forecast confidence'}: {forecastBand.confidence_label}
        </div>
      ) : null}
    </div>
  );
};
