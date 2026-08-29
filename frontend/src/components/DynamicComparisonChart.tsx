import React, { useEffect, useRef, useState } from "react";
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ChatVisualization } from "../services/api";

interface DynamicComparisonChartProps {
  visualization: ChatVisualization;
  theme?: "dark" | "light";
}

/** Format a value with a unit string. */
function formatValue(value: number, unit: string): string {
  if (unit === "¥") {
    if (Math.abs(value) >= 1_000_000) return `¥${(value / 1_000_000).toFixed(1)}M`;
    if (Math.abs(value) >= 1_000) return `¥${(value / 1_000).toFixed(1)}K`;
    return `¥${value.toFixed(0)}`;
  }
  if (unit === "吨") {
    if (Math.abs(value) >= 1_000) return `${(value / 1_000).toFixed(1)}K吨`;
    return `${value.toFixed(1)}吨`;
  }
  if (unit === "单") return `${value.toFixed(0)}单`;
  if (unit === "¥/吨") return `¥${value.toFixed(0)}/吨`;
  if (Math.abs(value) >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (Math.abs(value) >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return value.toFixed(2);
}

/** Custom tooltip showing per-series values with their own units. */
const CustomTooltip: React.FC<{
  active?: boolean;
  payload?: Array<{ name?: string; value?: number; color?: string; unit?: string }>;
  label?: string;
}> = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-slate-600 bg-[#0F1B2D] px-3 py-2 shadow-xl">
      <p className="mb-1.5 text-xs font-medium text-slate-300">{label}</p>
      {payload.map((entry, idx) => (
        <div key={idx} className="flex items-center gap-2 text-[11px] text-slate-200">
          <span
            className="inline-block h-2 w-2 rounded-full flex-shrink-0"
            style={{ backgroundColor: entry.color }}
          />
          <span className="text-slate-400">{entry.name}:</span>
          <span className="font-semibold" style={{ color: entry.color }}>
            {entry.value !== undefined ? formatValue(entry.value, entry.unit || "") : "—"}
          </span>
        </div>
      ))}
    </div>
  );
};

/** Dark-themed multi-axis composed chart.
 *  Matches the reference image style: bg-[#0B1929], slate text, Revenue|Volume|Orders on 3 Y axes.
 */
export const DynamicComparisonChart: React.FC<DynamicComparisonChartProps> = ({
  visualization,
  theme = "dark",
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const [isVisible, setIsVisible] = useState(false);

  useEffect(() => {
    const node = containerRef.current;
    if (!node || isVisible) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setIsVisible(true);
          observer.disconnect();
        }
      },
      { rootMargin: "240px 0px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [isVisible]);

  const isDark = theme === "dark";
  const gridColor = isDark ? "#1F2D3D" : "#E2E8F0";
  const textColor = isDark ? "#9AA5B4" : "#64748B";
  const bgColor = isDark ? "bg-[#0B1929]" : "bg-slate-50";
  const borderColor = isDark ? "border-slate-700" : "border-slate-200";
  const titleColor = isDark ? "text-slate-100" : "text-slate-900";
  const subtitleColor = isDark ? "text-slate-400" : "text-slate-500";

  // Separate series by axis position
  const leftSeries = visualization.series.filter((s) => (s.axis || "left") === "left");
  const rightSeries = visualization.series.filter((s) => s.axis === "right");
  const farRightSeries = visualization.series.filter((s) => s.axis === "farRight");

  // Tick formatter per axis
  const leftUnit = leftSeries[0]?.unit || "";
  const rightUnit = rightSeries[0]?.unit || rightSeries[0]?.label || "";
  const farRightUnit = farRightSeries[0]?.unit || farRightSeries[0]?.label || "";

  const leftTick = (v: number) => formatValue(v, leftUnit);
  const rightTick = (v: number) => formatValue(v, rightUnit);
  const farRightTick = (v: number) => formatValue(v, farRightUnit);

  if (!isVisible) {
    return (
      <div ref={containerRef} className={`mt-3 w-full rounded-xl border ${borderColor} ${bgColor} p-4`}>
        <div className="h-64 w-full animate-pulse rounded bg-slate-800" />
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className={`mt-3 w-full rounded-xl border ${borderColor} ${bgColor} p-4`}
    >
      {/* Header */}
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <div className={`text-sm font-semibold ${titleColor}`}>
            {visualization.title}
          </div>
          {visualization.subtitle && (
            <div className={`text-xs ${subtitleColor}`}>
              {visualization.subtitle}
            </div>
          )}
        </div>
        {/* Axis legend pills */}
        <div className="flex flex-col items-end gap-1">
          {leftSeries[0] && (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-blue-500/10 border border-blue-500/30 px-2 py-0.5 text-[10px] font-medium text-blue-400">
              <span className="h-1.5 w-1.5 rounded-full bg-blue-400 inline-block" />
              {leftSeries[0].label} (L)
            </span>
          )}
          {rightSeries[0] && (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/10 border border-emerald-500/30 px-2 py-0.5 text-[10px] font-medium text-emerald-400">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 inline-block" />
              {rightSeries[0].label} (R)
            </span>
          )}
          {farRightSeries[0] && (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-orange-500/10 border border-orange-500/30 px-2 py-0.5 text-[10px] font-medium text-orange-400">
              <span className="h-1.5 w-1.5 rounded-full bg-orange-400 inline-block" />
              {farRightSeries[0].label}
            </span>
          )}
        </div>
      </div>

      {/* Chart */}
      <div className="h-64 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={visualization.data as any[]} margin={{ top: 4, right: 48, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={gridColor} vertical={false} />

            {/* X Axis */}
            <XAxis
              dataKey={visualization.x_key}
              tick={{ fontSize: 11, fill: textColor }}
              tickLine={false}
              axisLine={{ stroke: gridColor }}
            />

            {/* Left Y Axis — Revenue/Monetary */}
            {leftSeries.length > 0 && (
              <YAxis
                yAxisId="left"
                orientation="left"
                tick={{ fontSize: 10, fill: textColor }}
                tickLine={false}
                axisLine={false}
                tickFormatter={leftTick}
                width={56}
              />
            )}

            {/* Right Y Axis — Volume/Count */}
            {rightSeries.length > 0 && (
              <YAxis
                yAxisId="right"
                orientation="right"
                tick={{ fontSize: 10, fill: "#34D399" }}
                tickLine={false}
                axisLine={false}
                tickFormatter={rightTick}
                width={48}
              />
            )}

            {/* Far Right Y Axis — Additional metrics */}
            {farRightSeries.length > 0 && (
              <YAxis
                yAxisId="farRight"
                orientation="right"
                tick={{ fontSize: 10, fill: "#FB923C" }}
                tickLine={false}
                axisLine={false}
                tickFormatter={farRightTick}
                width={48}
                offset={farRightSeries.length > 0 && rightSeries.length > 0 ? 48 : 0}
              />
            )}

            {/* Tooltip */}
            <RechartsTooltip
              content={<CustomTooltip />}
              cursor={{ stroke: gridColor, strokeDasharray: "3 3" }}
            />

            {/* Legend */}
            <Legend
              wrapperStyle={{ fontSize: 11, color: textColor }}
              iconType="line"
              iconSize={10}
            />

            {/* Render each series */}
            {visualization.series.map((s) => {
              const axisId = s.axis === "right"
                ? "right"
                : s.axis === "farRight"
                  ? "farRight"
                  : "left";
              const baseProps = {
                key: s.key,
                yAxisId: axisId,
                name: s.label,
                stroke: s.color,
                strokeWidth: 2,
                dot: { r: 3, fill: s.color },
                activeDot: { r: 5 },
                unit: s.unit,
              };
              if (s.chart_type === "bar") {
                return (
                  <Bar
                    {...baseProps}
                    dataKey={s.key}
                    fill={s.color}
                    opacity={0.75}
                    radius={[3, 3, 0, 0]}
                  />
                );
              }
              return <Line {...baseProps} type="monotone" dataKey={s.key} />;
            })}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
};

export default DynamicComparisonChart;
