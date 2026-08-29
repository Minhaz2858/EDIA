import {
  ArrowDown,
  ArrowUp,
  Download,
  Layers,
  RefreshCw,
  TrendingUp,
} from "lucide-react";
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLiveEvent } from "../context/LiveEventContext";
import {
  Area,
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  Tooltip as RechartsTooltip,
  ReferenceLine,
  ResponsiveContainer,
  XAxis,
  YAxis,
} from "recharts";
import { getChartTheme, rechartsTooltipStyle } from "../lib/chartTheme";
import { useLanguage } from "../lib/language";
import { useTheme } from "../lib/theme";
import { NO_DATA_LABEL, formatSignedPriceChange, getPriceChangeArrow, getPriceChangeBadgeClass, getPriceChangeLineColor, getPriceChangeStrongTextClass, getPriceChangeTextClass } from "../lib/priceChange";
import { API_BASE_URL, fetchWithRetry, pingBackend, api, PlatformProduct } from "../services/api";
import { InventorySafety } from "./InventorySafety";
import { OverviewDashboard } from "./OverviewDashboard";
import { PriceChain } from "./PriceChain";
import { PricingActions } from "./PricingActions";
import { ProductComparison } from "./ProductComparison";

interface MarketSnapshot {
  id: string;
  name: string;
  price: number;
  date?: string | null;
  change_pct: number;
  weekly_change_pct: number | null;
  daily_gap_value?: number | null;
  weekly_gap_value?: number | null;
  daily_reference_date?: string | null;
  weekly_reference_date?: string | null;
  high: number;
  low: number;
  high_low_start_date?: string | null;
  high_low_end_date?: string | null;
  volume: number | null;
  unit: string;
  data_source?: string;
  data_source_detail?: string;
  sales_7d_ton: number | null;
  sales_7d_growth_pct: number | null;
  execution_price?: number | null;
  execution_price_date?: string | null;
  execution_price_change?: number | null;
  execution_price_change_pct?: number | null;
  execution_price_prev_date?: string | null;
}

interface RegionalPriceRegion {
  price_type: string;
  label: string;
  is_internal: boolean;
  price: number;
  change_pct: number | null;
  date: string;
  days_stale: number; // days behind the most recent region date in this product
}

interface RegionalPriceData {
  product_id: string;
  regions: RegionalPriceRegion[];
  spread_cny: number;
  spread_level: "low" | "medium" | "high";
  price_realization_pct: number | null;
  cost_floor: {
    floor_price: number;
    current_price?: number;
    below_floor?: boolean;
    margin_pct?: number;
    naphtha_price?: number;
  } | null;
}

// EXCLUDED_UI_PRODUCTS hides specific legacy hardcoded products from the market
// snapshot. This ONLY applies to hardcoded products returned by the backend snapshot API.
// Platform products registered via Platform Builder are governed by their own
// display_in field and surfaced through customIds — they are never filtered here.
const EXCLUDED_UI_PRODUCTS = new Set(["hydrogenated_c9", "trimethylbenzene"]);

async function buildBrentSnapshotFallback(): Promise<MarketSnapshot | null> {
  try {
    const historyRes = await fetchWithRetry(
      `${API_BASE_URL}/api/v2/market/history/crude_oil?days=30`,
      { timeout: 9000, retries: 1, backoff: 300 },
    );
    const historyData = await historyRes.json();
    const history = Array.isArray(historyData)
      ? (historyData as Array<{ date: string; price: number }>)
        .filter(
          (item) =>
            item &&
            typeof item.date === "string" &&
            typeof item.price === "number" &&
            Number.isFinite(item.price),
        )
        .sort((a, b) => a.date.localeCompare(b.date))
      : [];

    let regionalPrice: number | null = null;
    try {
      const regionalRes = await fetchWithRetry(
        `${API_BASE_URL}/api/v2/market/regional/crude_oil`,
        { timeout: 9000, retries: 0 },
      );
      const regionalData =
        (await regionalRes.json()) as Partial<RegionalPriceData>;
      const brentRegion = Array.isArray(regionalData.regions)
        ? regionalData.regions.find((region) => region.price_type === "brent")
        : undefined;
      regionalPrice =
        typeof brentRegion?.price === "number" ? brentRegion.price : null;
    } catch {
      regionalPrice = null;
    }

    const latest = history[history.length - 1];
    const prev = history.length > 1 ? history[history.length - 2] : undefined;
    const latestPrice = latest?.price ?? regionalPrice;
    if (typeof latestPrice !== "number" || !Number.isFinite(latestPrice)) {
      return null;
    }

    const latestDate = latest?.date ? new Date(`${latest.date}T00:00:00`) : null;
    const rangeStartDate = latestDate
      ? new Date(latestDate.getTime() - 6 * 24 * 60 * 60 * 1000)
      : null;
    const rangeStartIso = rangeStartDate?.toISOString().slice(0, 10) ?? null;
    const rangeEndIso = latest?.date ?? null;
    const windowPoints = rangeStartIso
      ? history.filter(
        (item) => item.date >= rangeStartIso && item.date <= (rangeEndIso ?? item.date),
      )
      : history.slice(-7);
    const baselinePoint = windowPoints[0];
    const baselinePrice = baselinePoint?.price ?? latestPrice;
    const changePct = baselinePrice
      ? Number(
        (((latestPrice - baselinePrice) / baselinePrice) * 100).toFixed(2),
      )
      : 0;
    const high =
      windowPoints.length > 0 ? Math.max(...windowPoints.map((item) => item.price)) : latestPrice;
    const low =
      windowPoints.length > 0 ? Math.min(...windowPoints.map((item) => item.price)) : latestPrice;

    return {
      id: "crude_oil",
      name: PRODUCT_LABELS.zh.crude_oil,
      price: latestPrice,
      change_pct: changePct,
      daily_gap_value: baselinePrice
        ? Number((latestPrice - baselinePrice).toFixed(2))
        : null,
      weekly_change_pct: changePct,
      weekly_gap_value: baselinePrice
        ? Number((latestPrice - baselinePrice).toFixed(2))
        : null,
      daily_reference_date: prev?.date ?? null,
      weekly_reference_date: baselinePoint?.date ?? null,
      high,
      low,
      high_low_start_date: rangeStartIso,
      high_low_end_date: rangeEndIso,
      volume: null,
      unit: "USD/bbl",
      data_source: "frontend_fallback",
      data_source_detail: "frontend_fallback",
      sales_7d_ton: null,
      sales_7d_growth_pct: null,
    };
  } catch {
    return null;
  }
}

const PRODUCT_LABELS = {
  zh: {
    crude_oil: "布伦特原油",
    naphtha: "日本石脑油",
    cracked_c5: "工业用裂解碳五",
    cracked_c9: "工业用裂解碳九",
    isoprene: "异戊二烯",
    piperylene: "间戊二烯",
    dcpd: "双环戊二烯",
    raffinate_c5: "抽余碳五",
    sis: "SIS",
    c5_resin: "碳五石油树脂",
    blowing_agent: "戊烷发泡剂",
    styrene: "苯乙烯",
    hydrogenated_c9: "加氢碳九",
    trimethylbenzene: "混三甲苯",
  },
  en: {
    crude_oil: "Brent Crude Oil",
    naphtha: "Japanese Naphtha",
    cracked_c5: "Cracked C5",
    cracked_c9: "Cracked C9",
    isoprene: "Isoprene",
    piperylene: "Piperylene",
    dcpd: "DCPD",
    raffinate_c5: "Raffinate C5",
    sis: "SIS",
    c5_resin: "C5 Resin",
    blowing_agent: "Blowing Agent",
    styrene: "Styrene",
    hydrogenated_c9: "Hydrogenated C9",
    trimethylbenzene: "Trimethylbenzene",
  },
} as const;

const PRODUCT_PRICE_UNITS: Record<string, string> = {
  crude_oil: "USD/bbl",
  naphtha: "USD/ton",
};

interface PricePoint {
  date: string;
  price: number;
  volume?: number;
}

interface ObservedHistoryWindow {
  startDate: string | null;
  endDate: string | null;
  points: PricePoint[];
}

interface ForecastPoint {
  date: string;
  // history fields
  price?: number;
  // forecast fields
  base?: number;
  bull?: number;
  bear?: number;
  forecast_date?: string;
  step_number?: number;
  baseline_price?: number | null;
  final_price?: number | null;
  confidence?: number | null;
  cause_summary?: string | null;
  source?: string | null;
  evidence_event_ids?: string[];
  proposed_override_pct?: number | null;
  applied_override_pct?: number | null;
  intelligence_override_pct?: number | null;
  kimi_feature_adjustment_pct?: number | null;
  kimi_feature_raw_adjustment_pct?: number | null;
  kimi_feature_confidence?: number | null;
  kimi_feature_application_weight?: number | null;
  kimi_feature_applied?: boolean | null;
  kimi_reason_codes?: string[];
  kimi_evidence_event_ids?: string[];
  daily_forecast_mode?: string | null;
}

interface ForecastAdjustment {
  source?: string | null;
  proposed_override_pct?: number | null;
  applied_override_pct?: number | null;
  combined_applied_override_pct?: number | null;
  kimi_application_weight?: number | null;
  kimi_applied_to_numeric_forecast?: boolean | null;
  kimi_reason_codes?: string[];
  kimi_evidence_event_ids?: string[];
  evidence_event_ids?: string[];
  kimi_applied_horizon_bias_pct?: Record<string, number | null> | null;
  combined_horizon_adjustment_pct?: Record<string, number | null> | null;
  cause_summary?: string | null;
}

interface DeepseekFeatureSignal {
  snapshot_id?: string | null;
  status?: string | null;
  generated_at?: string | null;
  expires_at?: string | null;
  model?: string | null;
  application_weight?: number | null;
  applied_to_numeric_forecast?: boolean | null;
  integration_stage?: string | null;
  feature?: {
    confidence?: number | null;
    reason_codes?: string[];
    evidence_event_ids?: string[];
  } | null;
}

interface ForecastData {
  product_id: string;
  product_name: string;
  unit: string;
  history: ForecastPoint[];
  forecast: ForecastPoint[];
  forecast_path?: ForecastPoint[];
  forecast_native?: ForecastPoint[];
  forecast_dense?: ForecastPoint[];
  forecast_generation?: {
    baseline_status?: string | null;
    baseline_source?: string | null;
    path_source?: string | null;
    display_series?: string | null;
    native_display_series?: string | null;
    daily_forecast_mode?: string | null;
    kimi_feature_snapshot_id?: string | null;
    message?: string | null;
  };
  forecast_adjustment?: ForecastAdjustment;
  kimi_feature_signal?: DeepseekFeatureSignal;
  cadence_info?: {
    cadence_class?: string | null;
    change_frequency?: number | null;
    native_step_days?: number | null;
    daily_forecast_advised?: boolean | null;
    display_preferred_series?: string | null;
  };
  data_quality?: {
    ffill_ratio?: number | null;
    low_data?: boolean | null;
    reason?: string | null;
  };
  model_quality?: {
    below_naive_baseline?: boolean | null;
    warning?: string | null;
  };
  trust_tier?: {
    tier?: string | null;
    below_naive?: boolean | null;
    cadence_class?: string | null;
    mape?: number | null;
    reason_zh?: string | null;
    reason_en?: string | null;
    badge_color?: string | null;
    badge_label_zh?: string | null;
    badge_label_en?: string | null;
  };
  daily_forecast_mode?: string | null;
  cause_summary?: string | null;
  evidence_event_ids?: string[];
  seasonal_adj_pct: number;
}

interface DecisionLogEntry {
  id: string;
  product_id: string;
  product_name: string;
  created_at: string;
  ai_price_low: number;
  ai_price_high: number;
  ai_price_mid: number;
  price_unit?: string | null;
  executed_price: number | null;
  price_in_range: boolean | null;
  outcome: "pending" | "deal" | "no_deal" | "above_range" | "below_range";
  status: "pending" | "executed" | "closed";
}

type DecisionPointState =
  | "in_range"
  | "above_range"
  | "below_range"
  | "no_deal";

interface DecisionChartPoint {
  id: string;
  productId: string;
  productName: string;
  label: string;
  date: string;
  rangeLow: number;
  rangeHigh: number;
  rangeSpan: number;
  rangeOffset: number;
  rangeMid: number;
  executedPrice: number | null;
  noDealMarker: number | null;
  pointState: DecisionPointState;
}

function isForecastData(value: unknown): value is ForecastData {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<ForecastData>;
  return (
    typeof candidate.product_id === "string" &&
    Array.isArray(candidate.history) &&
    Array.isArray(candidate.forecast)
  );
}

function formatShortDateLabel(value: unknown): string {
  if (typeof value === "string")
    return value.length > 5 ? value.slice(5) : value;
  if (typeof value === "number") {
    const asText = String(value);
    return asText.length > 5 ? asText.slice(5) : asText;
  }
  return "";
}

function getNiceStep(value: number): number {
  if (!Number.isFinite(value) || value <= 0) {
    return 1;
  }

  const exponent = Math.floor(Math.log10(value));
  const fraction = value / 10 ** exponent;

  let niceFraction = 1;
  if (fraction <= 1) {
    niceFraction = 1;
  } else if (fraction <= 2) {
    niceFraction = 2;
  } else if (fraction <= 5) {
    niceFraction = 5;
  } else {
    niceFraction = 10;
  }

  return niceFraction * 10 ** exponent;
}

function buildNiceYAxisDomain(
  dataMin: number,
  dataMax: number,
  targetTickCount: number = 12,
): [number, number] {
  if (!Number.isFinite(dataMin) || !Number.isFinite(dataMax)) {
    return [0, 1];
  }

  if (dataMin === dataMax) {
    const pad = Math.max(1, Math.abs(dataMin) * 0.02);
    const step = getNiceStep((pad * 2) / Math.max(1, targetTickCount - 1));
    const minBound = Math.floor((dataMin - pad) / step) * step;
    const maxBound = Math.ceil((dataMax + pad) / step) * step;
    return [minBound, maxBound];
  }

  const span = dataMax - dataMin;
  const step = getNiceStep(span / Math.max(1, targetTickCount - 1));
  const pad = Math.max(step, span * 0.05);
  const minBound = Math.floor((dataMin - pad) / step) * step;
  const maxBound = Math.ceil((dataMax + pad) / step) * step;
  return [minBound, maxBound];
}

function buildNiceYAxisTicks(
  dataMin: number,
  dataMax: number,
  targetTickCount: number = 12,
): number[] {
  const [domainMin, domainMax] = buildNiceYAxisDomain(
    dataMin,
    dataMax,
    targetTickCount,
  );
  if (!Number.isFinite(domainMin) || !Number.isFinite(domainMax)) {
    return [0, 1];
  }

  if (domainMin === domainMax) {
    return [domainMin];
  }

  const step = getNiceStep(
    (domainMax - domainMin) / Math.max(1, targetTickCount - 1),
  );
  const ticks: number[] = [];
  for (let value = domainMin; value <= domainMax + step * 0.5; value += step) {
    ticks.push(Number(value.toFixed(6)));
  }
  return ticks;
}

function compactNumbers(values: Array<number | null | undefined>): number[] {
  return values.filter(
    (value): value is number =>
      typeof value === "number" && Number.isFinite(value),
  );
}

function medianNumber(values: number[]): number | null {
  if (values.length === 0) return null;
  const sorted = values.slice().sort((left, right) => left - right);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0
    ? (sorted[mid - 1] + sorted[mid]) / 2
    : sorted[mid];
}

function isCompatiblePriceScale(
  candidateValues: Array<number | null | undefined>,
  referenceValues: Array<number | null | undefined>,
): boolean {
  const candidates = compactNumbers(candidateValues).filter((value) => value > 0);
  const references = compactNumbers(referenceValues).filter((value) => value > 0);
  if (candidates.length === 0) return false;
  if (references.length === 0) return true;

  const referenceMedian = medianNumber(references);
  const candidateMedian = medianNumber(candidates);
  if (!referenceMedian || !candidateMedian) return true;

  const ratio = candidateMedian / referenceMedian;
  return ratio >= 0.25 && ratio <= 4;
}

function normalizePriceUnit(unit: string | null | undefined): string | null {
  const raw = String(unit || "").trim().toLowerCase();
  if (!raw) return null;
  if (raw === "bbl" || raw === "barrel") return "usd/bbl";
  if (raw.includes("usd") || raw.includes("$") || raw.includes("美元")) {
    return raw.includes("bbl") || raw.includes("barrel") ? "usd/bbl" : "usd/ton";
  }
  if (
    raw.includes("cny") ||
    raw.includes("rmb") ||
    raw.includes("¥") ||
    raw.includes("￥") ||
    raw.includes("人民币") ||
    raw.includes("元")
  ) {
    return "cny/ton";
  }
  return raw.replace(/\s+/g, "");
}

function getCanonicalProductUnit(
  productId: string | null | undefined,
  fallback?: string | null,
): string | undefined {
  const canonical = PRODUCT_PRICE_UNITS[String(productId || "")];
  if (canonical) return canonical;
  const normalizedFallback = String(fallback || "").trim();
  return normalizedFallback || undefined;
}

function arePriceUnitsCompatible(
  left: string | null | undefined,
  right: string | null | undefined,
): boolean {
  const leftUnit = normalizePriceUnit(left);
  const rightUnit = normalizePriceUnit(right);
  if (rightUnit?.startsWith("usd/") && !leftUnit) return false;
  return !leftUnit || !rightUnit || leftUnit === rightUnit;
}

function formatUnitPrice(
  value: number,
  unit: string | null | undefined,
  locale: string,
): string {
  const rounded = Math.round(value).toLocaleString(locale);
  const normalized = normalizePriceUnit(unit);
  if (normalized?.startsWith("usd/")) return `$${rounded}`;
  if (normalized === "cny/ton") return `¥${rounded}`;
  const unitLabel = String(unit || "").trim();
  return unitLabel ? `${rounded} ${unitLabel}` : rounded;
}

function getCompatibleAiRange(
  entry: DecisionLogEntry | null,
  referenceValues: Array<number | null | undefined>,
  referenceUnit?: string | null,
): { low: number; high: number } | null {
  if (!entry) return null;
  if (!arePriceUnitsCompatible(entry.price_unit, referenceUnit)) return null;
  const low = Math.min(entry.ai_price_low, entry.ai_price_high);
  const high = Math.max(entry.ai_price_low, entry.ai_price_high);
  if (!Number.isFinite(low) || !Number.isFinite(high)) return null;

  return isCompatiblePriceScale([low, high], referenceValues)
    ? { low, high }
    : null;
}

function buildAxisScale(
  values: Array<number | null | undefined>,
  targetTickCount: number = 12,
): { domain: [number, number]; ticks: number[] } {
  const numericValues = compactNumbers(values);
  if (numericValues.length === 0) {
    return { domain: [0, 1], ticks: [0, 1] };
  }

  const dataMin = Math.min(...numericValues);
  const dataMax = Math.max(...numericValues);

  return {
    domain: buildNiceYAxisDomain(dataMin, dataMax, targetTickCount),
    ticks: buildNiceYAxisTicks(dataMin, dataMax, targetTickCount),
  };
}

function normalizePriceHistory(value: unknown): PricePoint[] {
  if (!Array.isArray(value)) return [];

  const byDate = new Map<string, PricePoint>();
  for (const item of value) {
    if (!item || typeof item !== "object") continue;
    const candidate = item as Partial<PricePoint>;
    if (typeof candidate.date !== "string") continue;
    if (
      typeof candidate.price !== "number" ||
      !Number.isFinite(candidate.price)
    ) {
      continue;
    }

    byDate.set(candidate.date.slice(0, 10), {
      date: candidate.date.slice(0, 10),
      price: candidate.price,
      volume:
        typeof candidate.volume === "number" &&
          Number.isFinite(candidate.volume)
          ? candidate.volume
          : undefined,
    });
  }

  return Array.from(byDate.values()).sort((left, right) =>
    left.date.localeCompare(right.date),
  );
}

function formatShortDate(value: string | null | undefined): string {
  if (!value) return "--";
  const parts = value.slice(0, 10).split("-");
  if (parts.length !== 3) return value;
  return `${Number(parts[1])}.${Number(parts[2])}`;
}

function filterHistoryWindow(
  history: PricePoint[],
  startDate: string | null | undefined,
  endDate: string | null | undefined,
): PricePoint[] {
  if (!startDate || !endDate) return [];
  return history.filter(
    (point) => point.date >= startDate && point.date <= endDate,
  );
}

function buildObservedDailyRows(
  points: PricePoint[],
  startDate: string | null | undefined,
  endDate: string | null | undefined,
): Array<{ date: string; price: number | null }> {
  const byDate = new Map(points.map((point) => [point.date, point.price]));
  if (!startDate || !endDate) {
    return points.map((point) => ({ date: point.date, price: point.price }));
  }

  const start = new Date(`${startDate}T00:00:00Z`);
  const end = new Date(`${endDate}T00:00:00Z`);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || start > end) {
    return points.map((point) => ({ date: point.date, price: point.price }));
  }

  const rows: Array<{ date: string; price: number | null }> = [];
  const cursor = new Date(start);
  while (cursor <= end && rows.length < 14) {
    const dateKey = cursor.toISOString().slice(0, 10);
    rows.push({ date: dateKey, price: byDate.get(dateKey) ?? null });
    cursor.setUTCDate(cursor.getUTCDate() + 1);
  }
  return rows;
}

const ObservedSparkline: React.FC<{
  points: PricePoint[];
  changePct: number | null | undefined;
  label: string;
  startDate: string | null | undefined;
  endDate: string | null | undefined;
  locale: string;
  dataTitle: string;
  noDataLabel: string;
}> = ({ points, changePct, label, startDate, endDate, locale, dataTitle, noDataLabel }) => {
  const width = 96;
  const height = 34;
  const padding = 3;
  const prices = points.map((point) => point.price).filter(Number.isFinite);
  if (prices.length < 2) {
    return <div className="h-[34px] w-24" aria-hidden="true" />;
  }

  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const range = max - min || 1;
  const linePoints = points
    .map((point, index) => {
      const x =
        padding +
        (index / Math.max(1, points.length - 1)) * (width - padding * 2);
      const y =
        height -
        padding -
        ((point.price - min) / range) * (height - padding * 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const stroke = getPriceChangeLineColor(changePct);
  const rows = buildObservedDailyRows(points, startDate, endDate);

  return (
    <span
      className="group relative inline-flex h-[34px] w-24 shrink-0 cursor-help items-center"
      tabIndex={0}
      aria-label={label}
    >
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="h-[34px] w-24 overflow-visible"
        role="img"
        aria-label={label}
      >
        <title>{label}</title>
        <polyline
          points={linePoints}
          fill="none"
          stroke={stroke}
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        {points.map((point, index) => {
          const x =
            padding +
            (index / Math.max(1, points.length - 1)) * (width - padding * 2);
          const y =
            height -
            padding -
            ((point.price - min) / range) * (height - padding * 2);
          return (
            <circle
              key={`${point.date}-${index}`}
              cx={x}
              cy={y}
              r={index === points.length - 1 ? 2.4 : 1.8}
              fill="white"
              stroke={stroke}
              strokeWidth="1.4"
            />
          );
        })}
      </svg>
      <span className="pointer-events-none absolute right-0 top-full z-30 mt-2 hidden min-w-[190px] rounded-md border border-slate-200 bg-white px-3 py-2 text-left font-sans text-[11px] font-medium text-slate-600 shadow-lg group-hover:block group-focus:block">
        <span className="mb-1 block text-xs font-semibold text-slate-800">
          {dataTitle}
        </span>
        <span className="grid grid-cols-[auto_auto] gap-x-4 gap-y-0.5">
          {rows.map((row) => (
            <React.Fragment key={row.date}>
              <span>{formatShortDate(row.date)}</span>
              <span className="text-right font-mono text-slate-800">
                {row.price == null ? noDataLabel : row.price.toLocaleString(locale)}
              </span>
            </React.Fragment>
          ))}
        </span>
      </span>
    </span>
  );
};

function normalizeForecastPoints(points: ForecastPoint[]): ForecastPoint[] {
  const byDate = new Map<string, ForecastPoint>();

  for (const point of points) {
    if (!point || typeof point.date !== "string") continue;
    byDate.set(point.date.slice(0, 10), {
      ...point,
      date: point.date.slice(0, 10),
    });
  }

  return Array.from(byDate.values()).sort((left, right) =>
    left.date.localeCompare(right.date),
  );
}

function normalizeForecastData(data: ForecastData): ForecastData {
  const normalizedHistory = normalizeForecastPoints(data.history);
  const rawDenseForecast =
    Array.isArray(data.forecast_path) && data.forecast_path.length > 0
      ? data.forecast_path
      : Array.isArray(data.forecast_dense) && data.forecast_dense.length > 0
        ? data.forecast_dense
        : data.forecast;
  const normalizedDenseForecast = normalizeForecastPoints(rawDenseForecast);
  const normalizedNativeForecast = Array.isArray(data.forecast_native) && data.forecast_native.length > 0
    ? normalizeForecastPoints(data.forecast_native)
    : normalizedDenseForecast;
  const preferredDisplaySeries =
    data.forecast_generation?.display_series ??
    data.cadence_info?.display_preferred_series;
  const preferredForecast =
    preferredDisplaySeries === "forecast_native" &&
      normalizedNativeForecast.length > 0
      ? normalizedNativeForecast
      : normalizedDenseForecast;

  return {
    ...data,
    history: normalizedHistory,
    forecast: preferredForecast,
    forecast_path: normalizedDenseForecast,
    forecast_native: normalizedNativeForecast,
    forecast_dense: normalizedDenseForecast,
  };
}

type CachedPayload<T> = {
  value: T;
  cachedAt: number;
};

const SNAPSHOT_CACHE_KEY = "edia.dashboard.snapshot.v1";
const HISTORY_CACHE_PREFIX = "edia.dashboard.history.v3";
const FORECAST_CACHE_PREFIX = "edia.dashboard.forecast.v4";
const DECISION_CACHE_PREFIX = "edia.dashboard.decision.v2";
const SNAPSHOT_CACHE_TTL_MS = 5 * 60 * 1000;
const HISTORY_CACHE_TTL_MS = 10 * 60 * 1000;
const FORECAST_CACHE_TTL_MS = 10 * 60 * 1000;
const DECISION_CACHE_TTL_MS = 2 * 60 * 1000;

function readCache<T>(key: string, maxAgeMs: number): T | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as CachedPayload<T>;
    if (!parsed || typeof parsed !== "object") return null;
    if (typeof parsed.cachedAt !== "number") return null;
    if (Date.now() - parsed.cachedAt > maxAgeMs) return null;
    return parsed.value ?? null;
  } catch {
    return null;
  }
}

function writeCache<T>(key: string, value: T): void {
  if (typeof window === "undefined") return;
  try {
    const payload: CachedPayload<T> = { value, cachedAt: Date.now() };
    window.localStorage.setItem(key, JSON.stringify(payload));
  } catch {
    // no-op
  }
}

function historyCacheKey(productId: string): string {
  return `${HISTORY_CACHE_PREFIX}:${productId}`;
}

function forecastCacheKey(productId: string): string {
  return `${FORECAST_CACHE_PREFIX}:${productId}`;
}

function decisionCacheKey(days: number): string {
  return `${DECISION_CACHE_PREFIX}:${days}`;
}

const REGIONAL_LABEL_EN: Record<string, string> = {
  east_china: "East China",
  south_china: "South China",
  central_china: "Central China",
  qingdao_f7: "Qingdao Refining F7",
  wuhan: "Central China (Wuhan)",
  average: "National Avg.",
  yangzi: "Yangzi",
  yangzi_basf: "Yangzi BASF",
  sinokor: "Sinokor",
  maoming: "Maoming",
  guangzhou: "Guangzhou",
  henhe_hc9: "Henhe HC9",
  brent: "Brent",
  japan_cif: "Japan CIF",
  sinopec_sh: "Sinopec Shanghai",
  baling_1105: "Baling 1105",
  partner_forecast_price: "Partner Forecast",
  juhui: "Juhui",
  erp_sale_order_weighted: "Co. Actual Avg.",
};

const REGIONAL_LABEL_ZH: Record<string, string> = {
  east_china: "华东",
  south_china: "华南",
  central_china: "华中",
  qingdao_f7: "青岛炼化 F7",
  wuhan: "华中（武汉）",
  average: "全国均价",
  yangzi: "扬子",
  yangzi_basf: "扬子巴斯夫",
  sinokor: "新湖",
  maoming: "茂名",
  guangzhou: "广州",
  henhe_hc9: "恒河 HC9",
  brent: "布伦特",
  japan_cif: "日本 CIF",
  sinopec_sh: "上海石化",
  baling_1105: "巴陵 1105",
  partner_forecast_price: "合作方预测",
  juhui: "聚辉",
  erp_sale_order_weighted: "公司实际均价",
};

const HIDDEN_REGIONAL_PRICE_TYPES = new Set(["erp_forecast_price"]);

export const Dashboard: React.FC<{ isActive?: boolean }> = ({
  isActive = true,
}) => {
  const { language } = useLanguage();
  const { isDark } = useTheme();
  const chart = useMemo(() => getChartTheme(isDark), [isDark]);
  const tooltipStyle = useMemo(
    () => rechartsTooltipStyle(chart, isDark),
    [chart, isDark],
  );
  const locale = language === "en" ? "en-US" : "zh-CN";
  const text =
    language === "en"
      ? {
        globalMarket: "Global Market",
        backend: "Backend",
        online: "Online",
        offline: "Offline",
        dataSource: "Data Source",
        latency: "Latency",
        updatedTo: "Updated To",
        upstream: "Upstream Feedstocks",
        midstream: "Midstream Products",
        downstream: "Downstream Products",
        overview: "Overview",
        comparison: "Product Comparison",
        marketOverview: "Market Overview",
        category: "Category",
        price: "Price",
        change: "Change",
        volume: "Volume",
        noMysql: NO_DATA_LABEL,
        observation: "Observed Data",
        priceBadge: "Price",
        salesBadge: "7d sales",
        benchmarkTitle: "Price change benchmark",
        startReference: "Start reference",
        latestEnd: "Latest end",
        observedDataPoints: "7-day data",
        noMysqlMarket: NO_DATA_LABEL,
        noRealSeries: "No real series connected yet",
        daySales30: "7d sales",
        priceTrend: "Price Trend",
        forecast: "Forecast",
        futureDays: "Forecast for next",
        days: "days",
        seasonalAdj: "Seasonal adj.",
        bull: "Bull",
        base: "Base",
        bear: "Bear",
        history: "History",
        today: "Today",
        latestPrice: "Latest Price",
        priceMove: "Change",
        inventorySafety: "Inventory Safety Warning",
        pricingActions: "Pricing Action Recommendations",
        priceChain: "Price Transmission Chain",
        inventoryCalc: "Computed from live ERP data",
        pricingCalc: "Computed by pricing engine",
        mysqlCalc: "Computed from MySQL data",
        chainHint: "Crude oil → Naphtha → C5/C9 → Downstream products (30-day change)",
        range7d: "7-day range",
        highLabel: "High",
        lowLabel: "Low",
        spread: "Spread",
        realization: "Realization",
        belowCostFloor: "⚠ Below cost floor",
        costFloor: "Cost floor",
        decisionTracking: "Actual Executed Price vs AI Recommended Range",
        decisionSubtitle: "ERP + Decision Log",
        allProducts: "All Products",
        execInRange: "Executed in range",
        execAboveRange: "Executed above range",
        execBelowRange: "Executed below range",
        noDeal: "No deal",
        aiRange: "AI recommended range",
        previousAiPrice: "Previous AI recommended price",
        forecastMadeOn: "forecast made on",
        bestPerformer: "Best Performer",
        needsAttention: "Needs Attention",
        patternDetected: "Pattern Detected",
        hitRate: "hit rate",
        noDecisionData: "No executed decision records yet",
        decisionRefreshFailed: "Failed to load decision log",
        liveSync: "Refresh Mode",
        liveConnected: "Manual only",
        liveReconnecting: "Refresh needed",
        liveLastUpdate: "Last refresh",
        checking: "Checking...",
      }
      : {
        globalMarket: "全局市场",
        backend: "后端",
        online: "在线",
        offline: "离线",
        dataSource: "数据源",
        latency: "延迟",
        updatedTo: "更新至",
        upstream: "上游基础原料",
        midstream: "中游自产品",
        downstream: "下游成品",
        overview: "总览",
        comparison: "产品对比",
        marketOverview: "行情总览",
        category: "品类",
        price: "价格",
        change: "涨跌幅",
        volume: "成交量",
        noMysql: NO_DATA_LABEL,
        observation: "观测数据",
        priceBadge: "价格",
        salesBadge: "7天销量",
        benchmarkTitle: "涨跌幅基准",
        startReference: "起始基准",
        latestEnd: "最新价格",
        observedDataPoints: "7天数据",
        noMysqlMarket: NO_DATA_LABEL,
        noRealSeries: "当前未接入真实系列",
        daySales30: "7天销量",
        priceTrend: "价格走势",
        forecast: "预测",
        futureDays: "未来",
        days: "日",
        seasonalAdj: "季节调整",
        bull: "看涨",
        base: "基准",
        bear: "看跌",
        history: "历史",
        today: "今日",
        latestPrice: "最新价",
        priceMove: "涨跌幅",
        inventorySafety: "库存安全预警",
        pricingActions: "定价行动建议",
        priceChain: "产业链价格传导",
        inventoryCalc: "由 ERP 实时计算",
        pricingCalc: "由定价引擎实时计算",
        mysqlCalc: "由 MySQL 实时计算",
        chainHint: "原油 → 石脑油 → C5/C9 → 下游产品（30天涨跌幅）",
        range7d: "7天区间",
        highLabel: "高",
        lowLabel: "低",
        spread: "价差",
        realization: "实现率",
        belowCostFloor: "⚠ 低于成本底价",
        costFloor: "成本底",
        decisionTracking: "实际执行价格 vs AI 推荐区间",
        decisionSubtitle: "ERP + 决策日志",
        allProducts: "全部产品",
        execInRange: "执行在区间内",
        execAboveRange: "执行高于区间",
        execBelowRange: "执行低于区间",
        noDeal: "未成交",
        aiRange: "AI 推荐区间",
        previousAiPrice: "上一版 AI 推荐价格",
        forecastMadeOn: "预测生成于",
        bestPerformer: "表现最佳",
        needsAttention: "需要关注",
        patternDetected: "模式识别",
        hitRate: "命中率",
        noDecisionData: "暂无执行决策数据",
        decisionRefreshFailed: "决策日志加载失败",
        liveSync: "刷新模式",
        liveConnected: "仅手动",
        liveReconnecting: "需要刷新",
        liveLastUpdate: "上次刷新",
        checking: "检查中...",
      };
  const productLabels = PRODUCT_LABELS[language];
  const [platformProducts, setPlatformProducts] = useState<PlatformProduct[]>([]);
  const displayName = (id: string, fallback?: string) => {
    const staticLabel = productLabels[id as keyof typeof productLabels];
    if (staticLabel) return staticLabel;
    const platformProduct = platformProducts.find((p) => p.id === id);
    if (platformProduct) return language === "en" ? platformProduct.name_en : platformProduct.name_cn;
    return fallback ?? id;
  };
  const displayUnit = (item: MarketSnapshot) =>
    getCanonicalProductUnit(item.id, item.unit) ?? "";
  const [snapshot, setSnapshot] = useState<MarketSnapshot[]>(
    () =>
      readCache<MarketSnapshot[]>(SNAPSHOT_CACHE_KEY, SNAPSHOT_CACHE_TTL_MS) ??
      [],
  );
  const [selectedProduct, setSelectedProduct] = useState<string>("naphtha");
  const [history, setHistory] = useState<PricePoint[]>(
    () =>
      readCache<PricePoint[]>(
        historyCacheKey("naphtha"),
        HISTORY_CACHE_TTL_MS,
      ) ?? [],
  );
  const [observedHistoryById, setObservedHistoryById] = useState<
    Record<string, ObservedHistoryWindow>
  >({});
  const [historyProductId, setHistoryProductId] = useState<string>("naphtha");
  const [showForecast, setShowForecast] = useState(false);
  const [forecastHorizon, setForecastHorizon] = useState<7 | 15 | 30>(30);
  const [forecastData, setForecastData] = useState<ForecastData | null>(() =>
    readCache<ForecastData>(forecastCacheKey("naphtha"), FORECAST_CACHE_TTL_MS),
  );
  const [decisionLog, setDecisionLog] = useState<DecisionLogEntry[]>(
    () =>
      readCache<DecisionLogEntry[]>(
        decisionCacheKey(45),
        DECISION_CACHE_TTL_MS,
      ) ?? [],
  );
  const [decisionLogError, setDecisionLogError] = useState<string | null>(null);
  const [decisionProductTab, setDecisionProductTab] = useState<string>("all");
  const [categoryTab, setCategoryTab] = useState<
    "upstream" | "midstream" | "downstream" | "overview" | "comparison"
  >("overview");
  const [backendOnline, setBackendOnline] = useState<boolean | null>(null);
  const [snapshotLatencyMs, setSnapshotLatencyMs] = useState<number | null>(
    null,
  );
  const [isRefreshingSnapshot, setIsRefreshingSnapshot] = useState(false);
  const [liveStreamLastUpdate, setLiveStreamLastUpdate] = useState<
    number | null
  >(null);
  const backendHealthFailureCountRef = useRef(0);
  const selectedProductRef = useRef(selectedProduct);
  const historyRequestSeqRef = useRef(0);
  const observedHistoryInFlightRef = useRef<Set<string>>(new Set());
  const forecastRequestSeqRef = useRef(0);
  const forecastAccuracyRequestSeqRef = useRef(0);

  // Forecast accuracy tracking
  const [forecastAccuracy, setForecastAccuracy] = useState<{
    product_id: string;
    mape: number | null;
    hit_rate: number | null;
    total_evaluated: number;
    total_pending: number;
    records: Array<{
      forecast_date: string;
      target_date: string;
      predicted_price: number | null;
      actual_price: number | null;
      error_pct: number | null;
      status: "evaluated" | "pending";
      scenarios?: { base: number; bull: number; bear: number };
    }>;
  } | null>(null);

  // Regional price expansion state
  const [expandedProduct, setExpandedProduct] = useState<string | null>(null);
  const [regionalCache, setRegionalCache] = useState<
    Record<string, RegionalPriceData>
  >({});
  const [loadingRegional, setLoadingRegional] = useState<string | null>(null);

  const loadRegionalPrices = async (productId: string) => {
    if (regionalCache[productId]) {
      setExpandedProduct((prev) => (prev === productId ? null : productId));
      return;
    }
    setLoadingRegional(productId);
    try {
      const res = await fetchWithRetry(
        `${API_BASE_URL}/api/v2/market/regional/${productId}`,
        { timeout: 9000, retries: 0 },
      );
      const data: RegionalPriceData = await res.json();
      setRegionalCache((prev) => ({ ...prev, [productId]: data }));
      setExpandedProduct(productId);
    } catch (e) {
      console.error("loadRegionalPrices failed:", e);
    } finally {
      setLoadingRegional(null);
    }
  };
  const baseUpstreamIds = ["crude_oil", "naphtha", "cracked_c5", "cracked_c9"];
  const baseMidstreamIds = ["isoprene", "piperylene", "dcpd", "raffinate_c5"];
  const baseDownstreamIds = ["sis", "c5_resin", "blowing_agent", "styrene"];

  // Merge platform products flagged for dashboard display into their chosen category tab
  const platformDashboardProducts = platformProducts.filter((p) => p.display_in.includes("dashboard"));
  const upstreamIds = [...baseUpstreamIds, ...platformDashboardProducts.filter((p) => p.product_type === "upstream").map((p) => p.id)];
  const midstreamIds = [...baseMidstreamIds, ...platformDashboardProducts.filter((p) => p.product_type === "midstream").map((p) => p.id)];
  const downstreamIds = [...baseDownstreamIds, ...platformDashboardProducts.filter((p) => p.product_type === "downstream" || !p.product_type).map((p) => p.id)];
  const customIds: string[] = [];

  useEffect(() => {
    selectedProductRef.current = selectedProduct;
  }, [selectedProduct]);

  // Fetch admin-registered platform products for dashboard display
  useEffect(() => {
    api.listAvailablePlatformProducts({ displayIn: "dashboard", requireData: true })
      .then((rows) => setPlatformProducts(rows))
      .catch(() => { /* non-critical */ });
  }, []);

  // Define data loading functions first
  const loadSnapshot = async () => {
    setIsRefreshingSnapshot(true);
    try {
      const startedAt = performance.now();
      const res = await fetchWithRetry(
        `${API_BASE_URL}/api/v2/market/snapshot`,
        { timeout: 30000, retries: 2, backoff: 1000 },
      );
      const rawData = await res.json();
      const data = Array.isArray(rawData)
        ? (rawData as MarketSnapshot[]).filter(
          (item) => !EXCLUDED_UI_PRODUCTS.has(item.id),
        )
        : [];
      let nextSnapshot = data;
      const hasCrudeOil = data.some((item) => item.id === "crude_oil");
      if (!hasCrudeOil) {
        const brentFallback = await buildBrentSnapshotFallback();
        if (brentFallback) {
          nextSnapshot = [brentFallback, ...data];
        }
      }
      setSnapshot(nextSnapshot);
      writeCache(SNAPSHOT_CACHE_KEY, nextSnapshot);
      setSnapshotLatencyMs(Math.round(performance.now() - startedAt));
      setLiveStreamLastUpdate(Date.now());
    } catch (e) {
      console.error("loadSnapshot failed after retries:", e);
      setSnapshotLatencyMs(null);
    } finally {
      setIsRefreshingSnapshot(false);
    }
  };

  const loadHistory = async (id: string) => {
    const requestSeq = ++historyRequestSeqRef.current;
    try {
      const res = await fetchWithRetry(
        `${API_BASE_URL}/api/v2/market/history/${id}?days=180`,
        { timeout: 10000, retries: 1, backoff: 300 },
      );
      const data = await res.json();
      const nextHistory = normalizePriceHistory(data);
      if (
        selectedProductRef.current !== id ||
        historyRequestSeqRef.current !== requestSeq
      ) {
        return;
      }
      setHistory(nextHistory);
      setHistoryProductId(id);
      writeCache(historyCacheKey(id), nextHistory);
    } catch (e) {
      console.error("loadHistory failed:", e);
      if (
        selectedProductRef.current !== id ||
        historyRequestSeqRef.current !== requestSeq
      ) {
        return;
      }
      setHistory([]);
      setHistoryProductId(id);
    }
  };

  const loadObservedHistoryWindow = async (item: MarketSnapshot) => {
    const startDate = item.high_low_start_date ?? null;
    const endDate = item.high_low_end_date ?? null;
    if (!startDate || !endDate || item.data_source === "missing_mysql") return;

    const requestKey = `${item.id}:${startDate}:${endDate}`;
    if (observedHistoryInFlightRef.current.has(requestKey)) return;

    const cachedWindow = observedHistoryById[item.id];
    if (
      cachedWindow?.startDate === startDate &&
      cachedWindow?.endDate === endDate &&
      (cachedWindow.points?.length ?? 0) > 0
    ) {
      return;
    }

    observedHistoryInFlightRef.current.add(requestKey);
    try {
      const res = await fetchWithRetry(
        `${API_BASE_URL}/api/v2/market/history/${item.id}?days=14`,
        { timeout: 9000, retries: 1, backoff: 300 },
      );
      const data = await res.json();
      const points = filterHistoryWindow(
        normalizePriceHistory(data),
        startDate,
        endDate,
      );
      setObservedHistoryById((prev) => ({
        ...prev,
        [item.id]: { startDate, endDate, points },
      }));
    } catch (e) {
      console.error("loadObservedHistoryWindow failed:", e);
    } finally {
      observedHistoryInFlightRef.current.delete(requestKey);
    }
  };

  const loadForecast = async (id: string) => {
    const requestSeq = ++forecastRequestSeqRef.current;
    try {
      const res = await fetchWithRetry(
        `${API_BASE_URL}/api/market/forecast/${id}`,
        { timeout: 9000, retries: 0 },
      );
      const data = await res.json();
      if (isForecastData(data)) {
        const normalized = normalizeForecastData(data);
        if (
          selectedProductRef.current !== id ||
          forecastRequestSeqRef.current !== requestSeq ||
          normalized.product_id !== id
        ) {
          return;
        }
        setForecastData(normalized);
        writeCache(forecastCacheKey(id), normalized);
        return;
      }
      if (
        selectedProductRef.current !== id ||
        forecastRequestSeqRef.current !== requestSeq
      ) {
        return;
      }
      setForecastData(null);
    } catch (e) {
      console.error("loadForecast failed:", e);
      if (
        selectedProductRef.current !== id ||
        forecastRequestSeqRef.current !== requestSeq
      ) {
        return;
      }
      setForecastData(null);
    }
  };

  const loadForecastAccuracy = async (id: string) => {
    const requestSeq = ++forecastAccuracyRequestSeqRef.current;
    try {
      const res = await fetchWithRetry(
        `${API_BASE_URL}/api/v2/market/forecast-accuracy/${id}?days=30`,
        { timeout: 9000, retries: 0 },
      );
      const data = await res.json();
      if (
        selectedProductRef.current !== id ||
        forecastAccuracyRequestSeqRef.current !== requestSeq ||
        String(data?.product_id ?? "") !== id
      ) {
        return;
      }
      setForecastAccuracy(data);
    } catch (e) {
      console.error("loadForecastAccuracy failed:", e);
      if (
        selectedProductRef.current !== id ||
        forecastAccuracyRequestSeqRef.current !== requestSeq
      ) {
        return;
      }
      setForecastAccuracy(null);
    }
  };

  const loadDecisionLog = async (days = 45) => {
    try {
      const res = await fetchWithRetry(
        `${API_BASE_URL}/api/decision-log?days=${days}`,
        { timeout: 9000, retries: 1, backoff: 300 },
      );
      const data = await res.json();
      const entries = Array.isArray(data) ? (data as DecisionLogEntry[]) : [];
      setDecisionLog(entries);
      writeCache(decisionCacheKey(days), entries);
      setDecisionLogError(null);
    } catch (e) {
      console.error("loadDecisionLog failed:", e);
      setDecisionLogError(text.decisionRefreshFailed);
    }
  };

  const refreshMarketFromDb = async () => {
    try {
      await fetchWithRetry(`${API_BASE_URL}/api/v2/market/refresh`, {
        method: "POST",
        timeout: 12000,
        retries: 0,
      });
    } catch (e) {
      console.error("refreshMarketFromDb failed:", e);
    }
  };

  const checkBackendHealth = async () => {
    const status = await pingBackend();
    if (status === "online") {
      backendHealthFailureCountRef.current = 0;
      setBackendOnline(true);
      return true;
    }

    if (status === "aborted") {
      return backendOnline === true;
    }

    backendHealthFailureCountRef.current += 1;
    setBackendOnline((prev) =>
      prev === null || backendHealthFailureCountRef.current >= 2 ? false : prev,
    );
    return false;
  };

  // Then use them in effects
  useEffect(() => {
    if (!isActive) return;

    const cachedSnapshot = readCache<MarketSnapshot[]>(
      SNAPSHOT_CACHE_KEY,
      SNAPSHOT_CACHE_TTL_MS,
    );
    if (snapshot.length === 0 && cachedSnapshot) {
      setSnapshot(cachedSnapshot);
    }
    if (snapshot.length === 0 || !cachedSnapshot) {
      void loadSnapshot();
    }
  }, [isActive, snapshot.length]);

  // ── Live SSE event consumer ──────────────────────────────────
  // Listens for market-data-change events pushed by the backend
  // via /api/stream/market-live (SSE) and automatically re-fetches
  // the snapshot / decision log so the Dashboard stays current
  // without polling or --reload.
  const { latestEvent } = useLiveEvent();
  const lastAutoRefreshRef = useRef<number>(0);
  const AUTO_REFRESH_COOLDOWN_MS = 3_000; // at most one auto-refresh per 3s

  const autoRefreshSnapshot = useCallback(() => {
    const now = Date.now();
    if (now - lastAutoRefreshRef.current < AUTO_REFRESH_COOLDOWN_MS) return;
    lastAutoRefreshRef.current = now;
    void loadSnapshot();
  }, [loadSnapshot]);

  const autoRefreshDecisionLog = useCallback(() => {
    void loadDecisionLog(45);
  }, [loadDecisionLog]);

  useEffect(() => {
    if (!isActive) return;
    if (!latestEvent) return;

    switch (latestEvent.event) {
      case "market_prices_changed":
      case "market_refreshed":
      case "forecasts_precomputed":
        autoRefreshSnapshot();
        break;
      case "decision_log_changed":
        autoRefreshDecisionLog();
        break;
      case "connected":
        // On SSE (re)connect, fetch snapshot to catch any data
        // changes that happened while disconnected.
        autoRefreshSnapshot();
        break;
    }
  }, [isActive, latestEvent, autoRefreshSnapshot, autoRefreshDecisionLog]);

  useEffect(() => {
    if (!isActive) return;

    void checkBackendHealth();
  }, [isActive]);

  useEffect(() => {
    if (!isActive) return;

    const rafId = window.requestAnimationFrame(() => {
      window.dispatchEvent(new Event("resize"));
    });

    return () => window.cancelAnimationFrame(rafId);
  }, [isActive]);

  useEffect(() => {
    if (selectedProduct) {
      const cachedHistory = readCache<PricePoint[]>(
        historyCacheKey(selectedProduct),
        HISTORY_CACHE_TTL_MS,
      );
      if (cachedHistory) {
        setHistory(normalizePriceHistory(cachedHistory));
        setHistoryProductId(selectedProduct);
      } else {
        setHistory([]);
        setHistoryProductId(selectedProduct);
      }

      const cachedForecast = readCache<ForecastData>(
        forecastCacheKey(selectedProduct),
        FORECAST_CACHE_TTL_MS,
      );
      setForecastData(
        cachedForecast?.product_id === selectedProduct
          ? normalizeForecastData(cachedForecast)
          : null,
      );
      setForecastAccuracy((prev) =>
        prev?.product_id === selectedProduct ? prev : null,
      );

      void loadHistory(selectedProduct);
      void loadForecast(selectedProduct);
      void loadForecastAccuracy(selectedProduct);
    }
  }, [selectedProduct]);

  useEffect(() => {
    if (!isActive || !showForecast) return;

    const cachedDecision = readCache<DecisionLogEntry[]>(
      decisionCacheKey(45),
      DECISION_CACHE_TTL_MS,
    );
    if (cachedDecision) {
      setDecisionLog(cachedDecision);
    }

    loadDecisionLog(45);
  }, [isActive, showForecast]);

  const liveUpdateAgeLabel = (() => {
    if (liveStreamLastUpdate == null) return "--";
    const seconds = Math.max(
      0,
      Math.floor((Date.now() - liveStreamLastUpdate) / 1000),
    );
    if (seconds < 60) return `${seconds}s`;
    const minutes = Math.floor(seconds / 60);
    return `${minutes}m`;
  })();

  const snapshotById = new Map(snapshot.map((item) => [item.id, item]));
  const selectedSnapshot = snapshot.find((s) => s.id === selectedProduct);
  const selectedMarketUnit = getCanonicalProductUnit(
    selectedProduct,
    selectedSnapshot?.unit,
  );
  const activeHistory = historyProductId === selectedProduct ? history : [];
  const chartHistory = activeHistory.slice(-45);
  const chartPriceAxis = useMemo(
    () => buildAxisScale(chartHistory.map((point) => point.price), 12),
    [chartHistory],
  );
  const activeForecastData =
    forecastData?.product_id === selectedProduct ? forecastData : null;
  const selectedChartUnit = getCanonicalProductUnit(
    selectedProduct,
    activeForecastData?.unit || selectedMarketUnit,
  );
  const activeForecastAccuracy =
    forecastAccuracy?.product_id === selectedProduct ? forecastAccuracy : null;
  const forecastWindow = (activeForecastData?.forecast ?? []).slice(
    0,
    forecastHorizon,
  );
  const backendStatusClass =
    backendOnline === null
      ? "text-slate-400"
      : backendOnline
        ? "text-green-400"
        : "text-red-400";
  const backendStatusLabel =
    backendOnline === null
      ? text.checking
      : backendOnline
        ? text.online
        : text.offline;
  const previousForecastByTargetDate = useMemo(() => {
    const records = activeForecastAccuracy?.records ?? [];
    const productId = activeForecastAccuracy?.product_id;
    if (!productId)
      return new Map<
        string,
        {
          predictedPrice: number;
          forecastDate: string;
        }
      >();

    const byTargetDate = new Map<
      string,
      {
        predictedPrice: number;
        forecastDate: string;
      }
    >();

    for (const record of records) {
      const targetDate = String(record.target_date || "").slice(0, 10);
      const forecastDate = String(record.forecast_date || "").slice(0, 10);
      if (!targetDate || typeof record.predicted_price !== "number") continue;

      const existing = byTargetDate.get(targetDate);
      if (!existing || forecastDate > existing.forecastDate) {
        byTargetDate.set(targetDate, {
          predictedPrice: record.predicted_price,
          forecastDate,
        });
      }
    }

    return byTargetDate;
  }, [activeForecastAccuracy]);
  const forecastChartData = activeForecastData
    ? (() => {
      const forecastProductId = activeForecastAccuracy?.product_id;
      // Map decision-log executed prices for the selected product onto history dates
      const executedByDate = new Map<string, DecisionLogEntry>();
      decisionLog
        .filter(
          (e) => e.executed_price != null && e.product_id === selectedProduct,
        )
        .forEach((e) => {
          const d = e.created_at?.slice(0, 10);
          if (d) executedByDate.set(d, e);
        });

      const recentHistory = activeForecastData.history.slice(-21);
      const lastHistoryDate = recentHistory[recentHistory.length - 1]?.date;
      const lastHistoryPrice =
        recentHistory[recentHistory.length - 1]?.price ?? null;

      const histPts = recentHistory.map((h) => {
        const entry = executedByDate.get(h.date);
        const inRange = entry?.price_in_range === true;
        const isBoundaryPoint = h.date === lastHistoryDate;
        const previousForecast =
          forecastProductId === selectedProduct
            ? previousForecastByTargetDate.get(h.date)
            : undefined;
        return {
          ...h,
          priceActual: h.price,
          previousAiPrice: previousForecast?.predictedPrice ?? null,
          previousAiForecastDate: previousForecast?.forecastDate ?? null,
          baseForecast: isBoundaryPoint ? h.price : null,
          bullScenario: isBoundaryPoint ? h.price : null,
          bearScenario: isBoundaryPoint ? h.price : null,
          todayMarker: isBoundaryPoint ? h.price : null,
          execInRange: entry && inRange ? entry.executed_price : null,
          execOutRange: entry && !inRange ? entry.executed_price : null,
        };
      });

      const filteredForecastWindow = forecastWindow.filter(
        (f) => !lastHistoryDate || f.date > lastHistoryDate,
      );
      const firstForecastDate = filteredForecastWindow[0]?.date;

      const fcPts = filteredForecastWindow.map((f) => {
        const isFirstForecastPoint = f.date === firstForecastDate;
        const bridgeValue = isFirstForecastPoint ? lastHistoryPrice : null;
        return {
          ...f,
          // Keep the actual line visually continuous into the first forecast date.
          priceActual: bridgeValue,
          previousAiPrice: null,
          previousAiForecastDate: null,
          baseForecast: bridgeValue ?? f.base ?? null,
          bullScenario: bridgeValue ?? f.bull ?? null,
          bearScenario: bridgeValue ?? f.bear ?? null,
          todayMarker: null,
          execInRange: null,
          execOutRange: null,
        };
      });

      return [...histPts, ...fcPts];
    })()
    : [];
  const forecastChartAxis = useMemo(
    () =>
      buildAxisScale(
        forecastChartData.flatMap((point) => [
          point.priceActual,
          point.previousAiPrice,
          point.baseForecast,
          point.bullScenario,
          point.bearScenario,
          point.todayMarker,
          point.execInRange,
          point.execOutRange,
        ]),
        12,
      ),
    [forecastChartData],
  );
  const latestHistoryDate =
    activeHistory.length > 0
      ? activeHistory[activeHistory.length - 1]?.date
      : null;
  const updatedToLabel = useMemo(() => {
    if (!latestHistoryDate && liveStreamLastUpdate == null) return "--";

    const fallbackDate =
      liveStreamLastUpdate == null
        ? ""
        : new Date(liveStreamLastUpdate).toISOString().slice(0, 10);
    const baseDate = latestHistoryDate ?? fallbackDate;
    if (!baseDate) return "--";

    if (liveStreamLastUpdate == null) return baseDate;
    const dt = new Date(liveStreamLastUpdate);
    if (Number.isNaN(dt.getTime())) return baseDate;

    const hour24 = dt.getHours();
    const hour12 = hour24 % 12 || 12;
    const minute = String(dt.getMinutes()).padStart(2, "0");
    const suffix = hour24 >= 12 ? "pm" : "am";
    return `${baseDate} ${String(hour12).padStart(2, "0")}.${minute} ${suffix}`;
  }, [latestHistoryDate, liveStreamLastUpdate]);
  const decisionEntriesExecuted = decisionLog.filter(
    (entry) => entry.executed_price != null || entry.outcome === "no_deal",
  );
  const knownProductIds = new Set(Object.keys(PRODUCT_LABELS.en));
  const decisionProducts = Array.from(
    new Map(
      decisionEntriesExecuted
        .filter((entry) => knownProductIds.has(entry.product_id))
        .map((entry) => [
          entry.product_id,
          entry.product_name || displayName(entry.product_id),
        ]),
    ).entries(),
  ).map(([productId, productName]) => ({ productId, productName }));

  // Always keep decisionProductTab in sync with whatever product the user selects
  useEffect(() => {
    setDecisionProductTab(selectedProduct);
  }, [selectedProduct]);

  const currentDecisionTabIds =
    decisionProductTab === "all"
      ? null
      : new Set([decisionProductTab, selectedProduct]);

  const decisionChartData: DecisionChartPoint[] = decisionEntriesExecuted
    .filter((entry) => {
      if (currentDecisionTabIds == null) return true;
      return currentDecisionTabIds.has(entry.product_id);
    })
    .sort((a, b) => a.created_at.localeCompare(b.created_at))
    .slice(-12)
    .map((entry) => {
      const inRange = entry.price_in_range === true;
      const exec = entry.executed_price;
      const pointState: DecisionPointState =
        entry.outcome === "no_deal"
          ? "no_deal"
          : inRange
            ? "in_range"
            : exec != null && exec > entry.ai_price_high
              ? "above_range"
              : "below_range";

      const labelDate =
        entry.created_at?.slice(5, 10).replace("-", "/") || "--/--";
      const productName = displayName(entry.product_id, entry.product_name);
      return {
        id: entry.id,
        productId: entry.product_id,
        productName,
        label: `${productName.split(" ")[0]} ${labelDate}`,
        date: entry.created_at,
        rangeLow: entry.ai_price_low,
        rangeHigh: entry.ai_price_high,
        rangeSpan: Math.max(entry.ai_price_high - entry.ai_price_low, 1),
        rangeOffset: entry.ai_price_low,
        rangeMid: entry.ai_price_mid,
        executedPrice: entry.executed_price,
        noDealMarker: entry.outcome === "no_deal" ? entry.ai_price_mid : null,
        pointState,
      };
    });

  const inRangeCount = decisionChartData.filter(
    (d) => d.pointState === "in_range",
  ).length;
  const aboveRangeCount = decisionChartData.filter(
    (d) => d.pointState === "above_range",
  ).length;
  const belowRangeCount = decisionChartData.filter(
    (d) => d.pointState === "below_range",
  ).length;
  const noDealCount = decisionChartData.filter(
    (d) => d.pointState === "no_deal",
  ).length;
  const decisionChartAxis = useMemo(
    () =>
      buildAxisScale(
        decisionChartData.flatMap((point) => [
          point.rangeLow,
          point.rangeHigh,
          point.rangeMid,
          point.executedPrice,
          point.noDealMarker,
        ]),
        12,
      ),
    [decisionChartData],
  );

  const perProductStats = Array.from(
    decisionChartData.reduce((acc, item) => {
      const prev = acc.get(item.productId) ?? {
        productName: item.productName,
        total: 0,
        inRange: 0,
      };
      prev.total += 1;
      if (item.pointState === "in_range") prev.inRange += 1;
      acc.set(item.productId, prev);
      return acc;
    }, new Map<string, { productName: string; total: number; inRange: number }>()),
  )
    .map(([productId, stats]) => ({
      productId,
      ...stats,
      hitRate:
        stats.total > 0 ? Math.round((stats.inRange / stats.total) * 100) : 0,
    }))
    .sort((a, b) => b.hitRate - a.hitRate);

  const bestPerformer = perProductStats[0] ?? null;
  const needsAttention = perProductStats[perProductStats.length - 1] ?? null;
  const patternText =
    belowRangeCount >= aboveRangeCount + 2
      ? language === "en"
        ? "Execution is skewing below AI floor; competitor discount pressure may be under-modeled."
        : "执行价格明显低于 AI 底价，竞争对手降价压力可能未充分建模。"
      : aboveRangeCount >= belowRangeCount + 2
        ? language === "en"
          ? "Execution is skewing above AI ceiling; demand willingness-to-pay may be stronger than expected."
          : "执行价格明显高于 AI 上限，需求支付意愿可能高于模型预期。"
        : language === "en"
          ? "Execution is broadly aligned with AI range; continue tracking more closed-loop samples."
          : "执行价格整体与 AI 区间接近，建议继续积累更多闭环样本。";

  // Overlay chart data — only computed when a single product tab is selected.
  // Builds its own dataset from the product's full history + forecast, placing:
  //   • a step-function AI range band (tracks the most-recent AI recommendation at each date)
  //   • green/red executed-price dots at their actual positions on the history line
  //   • forecast fan (Bull/Base/Bear) extending from today
  const overlayChartData = useMemo(() => {
    if (decisionProductTab === "all" || !activeForecastData) return null;

    const productId = decisionProductTab;
    const forecastProductId = activeForecastAccuracy?.product_id;

    // Sort all entries for this product ascending by creation date
    const productEntries = decisionLog
      .filter((e) => e.product_id === productId)
      .slice()
      .sort((a, b) => (a.created_at || "").localeCompare(b.created_at || ""));

    // No early return — always build history + forecast even if there are no decision entries

    // Build executed-price lookup keyed by date
    const executedByDate = new Map<string, DecisionLogEntry>();
    productEntries
      .filter((e) => e.executed_price != null)
      .forEach((e) => {
        const d = e.created_at?.slice(0, 10);
        if (d) executedByDate.set(d, e);
      });

    // Step-function: for a given date, return the latest AI range on or before it
    const getLatestAiRangeEntry = (date: string): DecisionLogEntry | null => {
      let latest: DecisionLogEntry | null = null;
      for (const e of productEntries) {
        const d = e.created_at?.slice(0, 10);
        if (d && d <= date) latest = e;
        else if (d && d > date) break;
      }
      return latest;
    };

    // Use the last 30 history points so the trend is clearly visible
    const recentHistory = activeForecastData.history.slice(-30);
    const lastHistoryDate = recentHistory[recentHistory.length - 1]?.date;
    const lastHistoryPrice =
      recentHistory[recentHistory.length - 1]?.price ?? null;

    const filteredForecastWindow = forecastWindow.filter(
      (f) => !lastHistoryDate || f.date > lastHistoryDate,
    );
    const priceScaleReferenceValues = [
      ...recentHistory.map((h) => h.price),
      ...filteredForecastWindow.flatMap((f) => [f.bull, f.base, f.bear]),
    ];

    const histPts = recentHistory.map((h) => {
      const aiRange = getCompatibleAiRange(
        getLatestAiRangeEntry(h.date),
        priceScaleReferenceValues,
        selectedChartUnit,
      );
      const execEntry = executedByDate.get(h.date);
      const inRange = execEntry?.price_in_range === true;
      const isBoundaryPoint = h.date === lastHistoryDate;
      const previousForecastCandidate = previousForecastByTargetDate.get(h.date);
      const previousForecast =
        forecastProductId === productId &&
          isCompatiblePriceScale(
            [previousForecastCandidate?.predictedPrice],
            priceScaleReferenceValues,
          )
          ? previousForecastCandidate
          : undefined;
      // Anchor dot at the MARKET PRICE on that date so it appears on the price
      // line regardless of what unit the executed_price was recorded in.
      // The actual executed price is stored separately for the tooltip.
      const anchor = execEntry ? h.price : null;
      return {
        date: h.date,
        price: h.price,
        aiRangeLow: aiRange ? aiRange.low : null,
        aiRangeBand: aiRange ? Math.max(aiRange.high - aiRange.low, 1) : null,
        previousAiPrice: previousForecast?.predictedPrice ?? null,
        previousAiForecastDate: previousForecast?.forecastDate ?? null,
        execInRange: execEntry && inRange ? anchor : null,
        execOutRange: execEntry && !inRange ? anchor : null,
        execActualPrice: execEntry?.executed_price ?? null,
        execAiLow: execEntry?.ai_price_low ?? null,
        execAiHigh: execEntry?.ai_price_high ?? null,
        todayMarker: isBoundaryPoint ? h.price : (null as number | null),
        bullBand: null as number | null,
        bearBand: null as number | null,
        // Seed scenario lines at the boundary so forecast lines connect smoothly
        // to the final actual point (no visual break at "today").
        bull: isBoundaryPoint ? h.price : (null as number | null),
        base: isBoundaryPoint ? h.price : (null as number | null),
        bear: isBoundaryPoint ? h.price : (null as number | null),
      };
    });

    const firstForecastDate = filteredForecastWindow[0]?.date;

    const fcPts = filteredForecastWindow.map((f) => {
      const isFirstForecastPoint = f.date === firstForecastDate;
      const bridgeValue = isFirstForecastPoint ? lastHistoryPrice : null;
      const execEntry = executedByDate.get(f.date);
      const inRange = execEntry?.price_in_range === true;
      // On forecast dates, anchor the dot at the base forecast price so it sits
      // on the visible chart area rather than at the raw executed_price value.
      const anchor = execEntry ? (f.base ?? f.bull ?? f.bear ?? null) : null;
      return {
        date: f.date,
        // Keep the actual line visually continuous into the first forecast date.
        price: bridgeValue,
        aiRangeLow: null as number | null,
        aiRangeBand: null as number | null,
        previousAiPrice: null as number | null,
        previousAiForecastDate: null as string | null,
        execInRange: execEntry && inRange ? anchor : null,
        execOutRange: execEntry && !inRange ? anchor : null,
        execActualPrice: execEntry?.executed_price ?? null,
        execAiLow: execEntry?.ai_price_low ?? null,
        execAiHigh: execEntry?.ai_price_high ?? null,
        todayMarker: null as number | null,
        bullBand: bridgeValue ?? f.bull ?? null,
        bearBand: bridgeValue ?? f.bear ?? null,
        bull: bridgeValue ?? f.bull ?? null,
        base: bridgeValue ?? f.base ?? null,
        bear: bridgeValue ?? f.bear ?? null,
      };
    });

    return [...histPts, ...fcPts];
  }, [
    decisionProductTab,
    decisionLog,
    activeForecastAccuracy?.product_id,
    activeForecastData,
    forecastWindow,
    previousForecastByTargetDate,
    selectedChartUnit,
  ]);
  const overlayChartAxis = useMemo(
    () =>
      buildAxisScale(
        (overlayChartData ?? []).flatMap((point) => [
          point.price,
          point.aiRangeLow,
          point.aiRangeLow != null && point.aiRangeBand != null
            ? point.aiRangeLow + point.aiRangeBand
            : null,
          point.previousAiPrice,
          point.bull,
          point.base,
          point.bear,
          point.todayMarker,
          point.execInRange,
          point.execOutRange,
        ]),
        12,
      ),
    [overlayChartData],
  );

  const snapshotFiltered = snapshot.filter((s) => {
    if (categoryTab === "upstream") return upstreamIds.includes(s.id);
    if (categoryTab === "midstream") return midstreamIds.includes(s.id);
    if (categoryTab === "downstream") return downstreamIds.includes(s.id);
    return false;
  });
  const requestedIds =
    categoryTab === "upstream"
      ? upstreamIds
      : categoryTab === "midstream"
        ? midstreamIds
        : categoryTab === "downstream"
          ? downstreamIds
          : upstreamIds;
  const snapshotDisplay = requestedIds.map(
    (id) =>
      snapshotById.get(id) ?? {
        id,
        name: displayName(id),
        price: 0,
        change_pct: 0,
        weekly_change_pct: null,
        high: 0,
        low: 0,
        volume: null,
        unit: "",
        data_source: "missing_mysql",
        data_source_detail: "missing_mysql",
        sales_7d_ton: null,
        sales_7d_growth_pct: null,
      },
  );
  const observedRangeItem = snapshotDisplay.find(
    (item) => item.high_low_start_date && item.high_low_end_date,
  );
  const observedRangeLabel = observedRangeItem
    ? `${text.range7d} (${observedRangeItem.high_low_end_date} ~ ${observedRangeItem.high_low_start_date})`
    : text.range7d;
  const observedWindowRequestKey = snapshotDisplay
    .slice(0, 8)
    .map(
      (item) =>
        `${item.id}:${item.high_low_start_date ?? ""}:${item.high_low_end_date ?? ""}:${item.data_source ?? ""}`,
    )
    .join("|");

  useEffect(() => {
    if (!isActive) return;
    snapshotDisplay.slice(0, 8).forEach((item) => {
      void loadObservedHistoryWindow(item);
    });
  }, [isActive, observedWindowRequestKey]);

  useEffect(() => {
    const firstAvailable = snapshotFiltered[0];
    if (firstAvailable) {
      setSelectedProduct(firstAvailable.id);
    }
  }, [categoryTab, snapshotFiltered.length]);

  return (
    <div className="flex min-w-0 flex-col bg-slate-50 text-slate-800 font-sans text-[15px] min-h-full">
      {/* 顶部：全局市场滚动条 */}
      <div className="h-10 bg-slate-900 text-slate-300 flex items-center px-4 text-sm gap-6 shrink-0 overflow-x-auto whitespace-nowrap">
        <span className="font-bold text-blue-400 shrink-0">
          {text.globalMarket}
        </span>
        {snapshot.slice(0, 5).map((item) => (
          <div key={item.id} className="flex items-center gap-1.5 shrink-0">
            <span className="font-medium text-slate-200">
              {displayName(item.id, item.name)}
            </span>
            <span
              className={`font-mono ${getPriceChangeTextClass(item.change_pct, "bright")}`}
            >
              {item.price.toLocaleString(locale)}{" "}
              <span className="text-xs">
                ({formatSignedPriceChange(item.change_pct, {}, locale, "%")})
              </span>
            </span>
          </div>
        ))}
        <div className="ml-auto flex items-center gap-4 text-slate-500 shrink-0">
          {isRefreshingSnapshot && (
            <span className="text-blue-400">Refreshing...</span>
          )}
          <span>
            {text.backend}：
            <span className={backendStatusClass}>{backendStatusLabel}</span>
          </span>
          <span>{text.dataSource}：MySQL</span>
          <span>
            {text.latency}：
            {snapshotLatencyMs == null ? "--" : `${snapshotLatencyMs}ms`}
          </span>
          <span>
            {text.updatedTo}：{updatedToLabel}
          </span>
        </div>
      </div>

      <div className="flex min-w-0 flex-col">
        {/* Category tabs */}
        <div className="h-11 edia-panel border-b border-slate-200 flex items-center px-4 gap-2 shrink-0 overflow-x-auto whitespace-nowrap shadow-sm">
          <button
            className={`px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${categoryTab === "overview" ? "bg-blue-600 text-white" : "text-slate-600 hover:bg-slate-100"}`}
            onClick={() => setCategoryTab("overview")}
          >
            {text.overview}
          </button>
          <button
            className={`px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${categoryTab === "upstream" ? "bg-blue-600 text-white" : "text-slate-600 hover:bg-slate-100"}`}
            onClick={() => setCategoryTab("upstream")}
          >
            {text.upstream}
          </button>
          <button
            className={`px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${categoryTab === "midstream" ? "bg-blue-600 text-white" : "text-slate-600 hover:bg-slate-100"}`}
            onClick={() => setCategoryTab("midstream")}
          >
            {text.midstream}
          </button>
          <button
            className={`px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${categoryTab === "downstream" ? "bg-blue-600 text-white" : "text-slate-600 hover:bg-slate-100"}`}
            onClick={() => setCategoryTab("downstream")}
          >
            {text.downstream}
          </button>
          <button
            className={`px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${categoryTab === "comparison" ? "bg-blue-600 text-white" : "text-slate-600 hover:bg-slate-100"}`}
            onClick={() => setCategoryTab("comparison")}
          >
            {text.comparison}
          </button>
        </div>

        {/* Full-width modes */}
        {categoryTab === "comparison" ? (
          <div className="flex flex-col">
            <ProductComparison />
          </div>
        ) : categoryTab === "overview" ? (
          <OverviewDashboard
            language={language}
            locale={locale}
            snapshot={snapshot}
            displayName={displayName}
            groupIds={{ upstream: upstreamIds, midstream: midstreamIds, downstream: downstreamIds }}
          />
        ) : (
          <div className="flex min-w-0 flex-col lg:flex-row">
            {/* 左侧：行情总览 */}
            <div className="w-full lg:w-80 xl:w-96 shrink-0 flex flex-col border-b lg:border-b-0 lg:border-r border-slate-200 bg-white min-w-0">
              <div className="h-10 border-b border-slate-100 flex items-center justify-between px-3 bg-white">
                <span className="font-semibold text-slate-700 text-sm tracking-wider uppercase">
                  {text.marketOverview}
                </span>
              </div>

              {/* 表头 */}
              <div className="grid grid-cols-12 px-3 py-2.5 bg-slate-50 text-xs font-bold text-slate-500 border-b border-slate-100 uppercase tracking-wider">
                <div className="col-span-5">{text.category}</div>
                <div className="col-span-3 text-right">{text.price}</div>
                <div className="col-span-2 text-right">{text.change}</div>
                <div className="col-span-2 text-right">{text.volume}</div>
              </div>

              {/* 行数据 */}
              <div className="overflow-x-hidden overflow-y-auto">
                {snapshotDisplay.map((item) => {
                  const isAvailable = item.data_source !== "missing_mysql";
                  const isExpanded = expandedProduct === item.id;
                  const regional = regionalCache[item.id];
                  const isLoadingThis = loadingRegional === item.id;
                  const unitLabel = displayUnit(item);
                  return (
                    <div key={item.id}>
                      <div
                        onClick={() =>
                          isAvailable && setSelectedProduct(item.id)
                        }
                        className={`grid grid-cols-12 px-3 py-2.5 border-b border-slate-50 transition-colors text-sm ${isAvailable ? "cursor-pointer hover:bg-blue-50" : "cursor-not-allowed opacity-60"} ${selectedProduct === item.id ? "bg-blue-50 border-l-2 border-l-blue-500" : ""}`}
                      >
                        <div
                          className="col-span-5 font-semibold text-slate-700 flex items-start gap-1 min-w-0"
                          title={displayName(item.id, item.name)}
                        >
                          {isAvailable && (
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                loadRegionalPrices(item.id);
                              }}
                              className="shrink-0 w-4 h-4 rounded text-slate-400 hover:text-blue-600 flex items-center justify-center"
                              title="查看区域价格"
                            >
                              {isLoadingThis ? (
                                <span className="animate-spin text-[10px]">
                                  ⟳
                                </span>
                              ) : isExpanded ? (
                                <span className="text-[10px] text-blue-500">
                                  ▼
                                </span>
                              ) : (
                                <span className="text-[10px]">▶</span>
                              )}
                            </button>
                          )}
                          <span className="min-w-0 whitespace-normal break-words leading-tight">
                            {displayName(item.id, item.name)}
                          </span>
                        </div>
                        <div
                          className={`col-span-3 text-right font-mono ${!isAvailable ? "text-slate-400" : getPriceChangeStrongTextClass(item.change_pct)}`}
                        >
                          {isAvailable ? (
                            <div className="flex flex-col items-end leading-tight">
                              <span>{item.price.toLocaleString(locale)}</span>
                              {unitLabel ? (
                                <span className="text-[11px] font-medium text-slate-400 mt-0.5">
                                  {unitLabel}
                                </span>
                              ) : null}
                              {item.execution_price != null && (
                                <span className="text-[11px] text-slate-500 mt-0.5">
                                  {language === "zh" ? "执行 " : "Exec "}
                                  {formatShortDate(item.execution_price_date)}{" "}
                                  ¥{item.execution_price.toLocaleString(locale)}
                                  {item.execution_price_change != null && (
                                    <span className={`ml-1 font-mono ${getPriceChangeTextClass(item.execution_price_change)}`}>
                                      {item.execution_price_change > 0 ? "+" : ""}
                                      {item.execution_price_change.toLocaleString(locale, {
                                        minimumFractionDigits: 0,
                                        maximumFractionDigits: 0,
                                      })}
                                    </span>
                                  )}
                                </span>
                              )}
                            </div>
                          ) : (
                            <span className="text-[11px] leading-tight">{NO_DATA_LABEL}</span>
                          )}
                        </div>
                        <div
                          className={`col-span-2 text-right font-mono ${!isAvailable ? "text-slate-400" : getPriceChangeStrongTextClass(item.change_pct)}`}
                        >
                          {isAvailable
                            ? formatSignedPriceChange(item.change_pct, {}, locale, "%")
                            : <span className="text-[11px] leading-tight">{NO_DATA_LABEL}</span>}
                        </div>
                        <div className="col-span-2 text-right text-slate-400 text-xs mt-0.5">
                          {isAvailable
                            ? item.volume == null
                              ? "--"
                              : `${(item.volume / 1000).toFixed(0)}K`
                            : "--"}
                        </div>
                      </div>

                      {/* Regional price expansion panel */}
                      {isExpanded && regional && (
                        <div className="mx-2 mb-1 bg-slate-50 border border-slate-100 rounded-lg text-xs">
                          {/* Realization badge */}
                          <div className="flex items-center gap-2 px-3 pt-2 pb-1 flex-wrap">
                            {regional.price_realization_pct != null && (
                              <span
                                className={`px-1.5 py-0.5 rounded font-semibold ${regional.price_realization_pct >= 98 ? "bg-green-100 text-green-700" : regional.price_realization_pct >= 94 ? "bg-yellow-100 text-yellow-700" : "bg-red-100 text-red-700"}`}
                              >
                                {text.realization}{" "}
                                {regional.price_realization_pct}%
                              </span>
                            )}
                          </div>
                          {/* Regional rows */}
                          {regional.regions
                            .filter(
                              (r) =>
                                !HIDDEN_REGIONAL_PRICE_TYPES.has(
                                  String(r.price_type || "").trim().toLowerCase(),
                                ),
                            )
                            .map((r) => {
                              const isStale = (r.days_stale ?? 0) > 7;
                              return (
                                <div
                                  key={r.price_type}
                                  className={`flex items-center justify-between px-3 py-1 border-t border-slate-100 ${r.is_internal
                                    ? "bg-blue-50"
                                    : isStale
                                      ? "opacity-50"
                                      : ""
                                    }`}
                                  title={
                                    isStale
                                      ? language === "en"
                                        ? `Last updated ${r.date} — ${r.days_stale} days behind latest data. May not reflect current market.`
                                        : `最后更新 ${r.date}，落后最新数据 ${r.days_stale} 天，可能不反映当前市场。`
                                      : undefined
                                  }
                                >
                                  <span
                                    className={`font-medium ${r.is_internal
                                      ? "text-blue-700"
                                      : isStale
                                        ? "text-slate-400"
                                        : "text-slate-600"
                                      }`}
                                  >
                                    {language === "en"
                                      ? (REGIONAL_LABEL_EN[r.price_type] ??
                                        r.label)
                                      : (REGIONAL_LABEL_ZH[r.price_type] ??
                                        REGIONAL_LABEL_ZH[String(r.label || "").trim().toLowerCase()] ??
                                        r.label)}
                                    {r.is_internal ? " 🏭" : ""}
                                  </span>
                                  <span className="flex items-center gap-2">
                                    <span
                                      className={`font-mono ${isStale ? "text-slate-400" : "text-slate-800"}`}
                                    >
                                      ¥{r.price.toLocaleString()}
                                    </span>
                                    {r.change_pct != null && (
                                      <span
                                        className={`font-mono ${isStale
                                          ? "text-slate-400"
                                          : getPriceChangeTextClass(r.change_pct)
                                          }`}
                                      >
                                        {formatSignedPriceChange(r.change_pct, {}, locale, "%")}
                                      </span>
                                    )}
                                    <span
                                      className={`text-xs ${isStale
                                        ? "text-amber-500 font-semibold"
                                        : "text-slate-400"
                                        }`}
                                    >
                                      {r.date.slice(5)}
                                      {isStale && " ⚠"}
                                    </span>
                                  </span>
                                </div>
                              );
                            })}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>

            {/* 右侧：工作区 */}
            <div className="flex-1 min-w-0 flex flex-col bg-slate-50 p-3 gap-3">
              <div className="bg-white border border-slate-200 rounded-xl shadow-sm p-4">
                <div className="mb-3 flex flex-wrap items-center gap-2">
                  <span className="text-sm font-semibold text-slate-500 uppercase tracking-wider">
                    {text.observation}
                  </span>
                  <span className="text-xs font-medium text-slate-400">
                    {observedRangeLabel}
                  </span>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                  {snapshotDisplay.slice(0, 8).map((item) => {
                    const isAvailable = item.data_source !== "missing_mysql";
                    const observedHistory = observedHistoryById[item.id];
                    const observedPoints =
                      observedHistory?.startDate === item.high_low_start_date &&
                        observedHistory?.endDate === item.high_low_end_date
                        ? (observedHistory?.points ?? [])
                        : [];
                    const benchmarkStartPrice =
                      item.weekly_gap_value != null
                        ? Number((item.price - item.weekly_gap_value).toFixed(2))
                        : observedPoints[0]?.price ?? null;
                    const benchmarkEndPrice = item.price;
                    const benchmarkStartDate =
                      item.high_low_start_date ?? item.weekly_reference_date ?? observedPoints[0]?.date ?? null;
                    const benchmarkEndDate =
                      item.date ?? observedPoints[observedPoints.length - 1]?.date ?? item.high_low_end_date ?? null;
                    const sparklineLabel = `${displayName(item.id, item.name)} ${formatShortDate(item.high_low_start_date)}-${formatShortDate(item.high_low_end_date)} trend`;
                    return (
                      <div
                        key={item.id}
                        className="border border-slate-100 rounded-lg p-3 hover:border-blue-200 transition-colors"
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div className="min-w-0">
                            <div className="text-xs text-slate-500 mb-1 truncate">
                              {displayName(item.id, item.name)}
                            </div>
                            <div className="text-lg font-mono font-bold text-slate-800">
                              {isAvailable
                                ? item.price.toLocaleString(locale)
                                : NO_DATA_LABEL}
                            </div>
                            {isAvailable && item.execution_price != null && (
                              <div className="text-[11px] text-slate-500 mt-0.5">
                                {language === "zh" ? "执行 " : "Exec "}
                                {formatShortDate(item.execution_price_date)}{" "}
                                ¥{item.execution_price.toLocaleString(locale)}
                                {item.execution_price_change != null && (
                                  <span className={`ml-1 font-mono ${getPriceChangeTextClass(item.execution_price_change)}`}>
                                    {item.execution_price_change > 0 ? "+" : ""}
                                    {item.execution_price_change.toLocaleString(locale, {
                                      minimumFractionDigits: 0,
                                      maximumFractionDigits: 0,
                                    })}
                                  </span>
                                )}
                              </div>
                            )}
                          </div>
                          {isAvailable && (
                            <ObservedSparkline
                              points={observedPoints}
                              changePct={item.weekly_change_pct}
                              label={sparklineLabel}
                              startDate={item.high_low_start_date}
                              endDate={item.high_low_end_date}
                              locale={locale}
                              dataTitle={text.observedDataPoints}
                              noDataLabel="--"
                            />
                          )}
                        </div>
                        {/* Growth badges row */}
                        {isAvailable && (
                          <div className="flex items-center gap-1.5 mt-0.5 flex-wrap">
                            {/* Price growth badge (weekly) */}
                            {item.weekly_change_pct != null && (
                              <span
                                className={`group relative inline-flex cursor-help items-center gap-0.5 rounded px-1.5 py-0.5 text-[11px] font-mono font-semibold ${getPriceChangeBadgeClass(item.weekly_change_pct)}`}
                                tabIndex={0}
                              >
                                {getPriceChangeArrow(item.weekly_change_pct) === "up" ? (
                                  <ArrowUp className="w-2.5 h-2.5" />
                                ) : getPriceChangeArrow(item.weekly_change_pct) === "down" ? (
                                  <ArrowDown className="w-2.5 h-2.5" />
                                ) : null}
                                {text.priceBadge}{" "}
                                {formatSignedPriceChange(item.weekly_change_pct, {}, locale, "%")}
                                <span className="pointer-events-none absolute left-0 top-full z-20 mt-2 hidden min-w-[210px] rounded-md border border-slate-200 bg-white px-3 py-2 text-left font-sans text-[11px] font-medium text-slate-600 shadow-lg group-hover:block group-focus:block">
                                  <span className="mb-1 block text-xs font-semibold text-slate-800">
                                    {text.benchmarkTitle}
                                  </span>
                                  <span className="flex items-center justify-between gap-3">
                                    <span>{text.startReference}</span>
                                    <span className="font-mono text-slate-800">
                                      {formatShortDate(benchmarkStartDate)} {benchmarkStartPrice != null ? benchmarkStartPrice.toLocaleString(locale) : "--"}
                                    </span>
                                  </span>
                                  <span className="mt-0.5 flex items-center justify-between gap-3">
                                    <span>{text.latestEnd}</span>
                                    <span className="font-mono text-slate-800">
                                      {formatShortDate(benchmarkEndDate)} {benchmarkEndPrice.toLocaleString(locale)}
                                    </span>
                                  </span>
                                </span>
                              </span>
                            )}
                            {/* Sales growth badge */}
                            {item.sales_7d_growth_pct != null && (
                              <span
                                className={`inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[11px] font-mono font-semibold ${item.sales_7d_growth_pct >= 0
                                  ? "bg-blue-50 text-blue-700"
                                  : "bg-orange-50 text-orange-700"
                                  }`}
                              >
                                {item.sales_7d_growth_pct >= 0 ? (
                                  <ArrowUp className="w-2.5 h-2.5" />
                                ) : (
                                  <ArrowDown className="w-2.5 h-2.5" />
                                )}
                                {text.salesBadge}{" "}
                                {item.sales_7d_growth_pct > 0 ? "+" : ""}
                                {item.sales_7d_growth_pct}%
                              </span>
                            )}
                          </div>
                        )}
                        {!isAvailable && (
                          <div className="text-xs text-slate-400 mt-0.5">
                            {NO_DATA_LABEL}
                          </div>
                        )}
                        <div className="text-xs text-slate-400 mt-0.5 leading-5">
                          {isAvailable ? (
                            <>
                              {text.highLabel} {item.high} {text.lowLabel}{" "}
                              {item.low}
                              {item.sales_7d_ton != null && (
                                <span className="ml-1">
                                  • {text.daySales30}{" "}
                                  {item.sales_7d_ton.toLocaleString(locale)}t
                                </span>
                              )}
                            </>
                          ) : (
                            text.noRealSeries
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              <div
                className={`${showForecast ? "min-h-[520px]" : "min-h-[380px]"} bg-white border border-slate-200 rounded-xl shadow-sm flex flex-col`}
              >
                <div className="h-10 border-b border-slate-100 flex items-center justify-between px-4 py-2 flex-wrap gap-2">
                  <div className="flex items-center gap-2">
                    <span className="font-semibold text-slate-700 text-base">
                      {displayName(
                        selectedSnapshot?.id || "",
                        selectedSnapshot?.name,
                      )}{" "}
                      {text.priceTrend}
                    </span>
                    <span className="text-sm text-slate-400 font-mono">
                      {selectedProduct.toUpperCase()} · MYSQL
                    </span>
                  </div>
                  <div className="flex gap-2 items-center text-slate-400 flex-wrap justify-end">
                    <button
                      onClick={() => setShowForecast((v) => !v)}
                      className={`flex items-center gap-1 px-2.5 py-1 rounded text-xs border transition-colors ${showForecast ? "bg-blue-100 text-blue-700 border-blue-300" : "bg-slate-100 text-slate-500 border-slate-300 hover:bg-blue-50"}`}
                      title={text.forecast}
                    >
                      <TrendingUp className="w-3 h-3" />
                      {text.forecast}
                    </button>
                    {showForecast && activeForecastData && (
                      <div className="flex items-center rounded border border-slate-300 bg-white p-0.5 text-xs">
                        {[7, 15, 30].map((days) => (
                          <button
                            key={days}
                            onClick={() =>
                              setForecastHorizon(days as 7 | 15 | 30)
                            }
                            className={`rounded px-2 py-0.5 transition-colors ${forecastHorizon === days ? "bg-blue-100 text-blue-700" : "text-slate-500 hover:bg-slate-100"}`}
                            title={
                              language === "en"
                                ? `View next ${days} day forecast`
                                : `查看未来${days}日预测`
                            }
                          >
                            {days}
                            {text.days}
                          </button>
                        ))}
                      </div>
                    )}
                    <RefreshCw
                      className="w-3.5 h-3.5 cursor-pointer hover:text-slate-600"
                      onClick={async () => {
                        await refreshMarketFromDb();
                        await checkBackendHealth();
                        await loadSnapshot();
                        await loadHistory(selectedProduct);
                        await loadForecast(selectedProduct);
                        if (showForecast) {
                          await loadDecisionLog(45);
                        }
                      }}
                    />
                    <Download className="w-3.5 h-3.5 cursor-pointer hover:text-slate-600" />
                    <Layers className="w-3.5 h-3.5 cursor-pointer hover:text-slate-600" />
                  </div>
                </div>

                {/* History chart */}
                {(() => {
                  const hasVolume = chartHistory.some(
                    (p) => p.volume != null && p.volume > 0,
                  );
                  return (
                    <div
                      className={`p-3 relative ${showForecast ? "h-[220px]" : "min-h-[300px]"}`}
                    >
                      <ResponsiveContainer width="100%" height="100%">
                        <ComposedChart data={chartHistory}>
                          <defs>
                            <linearGradient
                              id="colorPrice"
                              x1="0"
                              y1="0"
                              x2="0"
                              y2="1"
                            >
                              <stop
                                offset="5%"
                                stopColor="#3b82f6"
                                stopOpacity={0.1}
                              />
                              <stop
                                offset="95%"
                                stopColor="#3b82f6"
                                stopOpacity={0}
                              />
                            </linearGradient>
                          </defs>
                          <CartesianGrid
                            strokeDasharray="3 3"
                            stroke={chart.grid}
                            vertical={false}
                          />
                          <XAxis
                            dataKey="date"
                            tick={{ fontSize: 11, fill: chart.tick }}
                            tickFormatter={(val) => formatShortDateLabel(val)}
                            axisLine={{ stroke: chart.axis }}
                            tickLine={false}
                            minTickGap={30}
                          />
                          <YAxis
                            yAxisId="price"
                            domain={chartPriceAxis.domain}
                            ticks={chartPriceAxis.ticks}
                            orientation="right"
                            tick={{ fontSize: 11, fill: chart.tick }}
                            axisLine={false}
                            tickLine={false}
                            allowDecimals={false}
                            interval={0}
                            tickFormatter={(v) =>
                              formatUnitPrice(Number(v), selectedMarketUnit, locale)
                            }
                          />
                          {hasVolume && (
                            <YAxis
                              yAxisId="volume"
                              orientation="left"
                              tick={{ fontSize: 10, fill: chart.tickMuted }}
                              axisLine={false}
                              tickLine={false}
                              tickCount={4}
                              tickFormatter={(v) =>
                                v >= 1000 ? `${(v / 1000).toFixed(0)}K` : `${v}`
                              }
                            />
                          )}
                          <RechartsTooltip
                            contentStyle={tooltipStyle}
                            formatter={(value: any, name: string) => [
                              typeof value === "number"
                                ? formatUnitPrice(
                                  value,
                                  selectedMarketUnit,
                                  locale,
                                )
                                : value,
                              name === "price"
                                ? text.price
                                : language === "en"
                                  ? "Volume (t)"
                                  : "销量(吨)",
                            ]}
                          />
                          {hasVolume && (
                            <Bar
                              yAxisId="volume"
                              dataKey="volume"
                              fill={chart.tickMuted}
                              opacity={0.3}
                              barSize={4}
                            />
                          )}
                          <Area
                            yAxisId="price"
                            type="linear"
                            dataKey="price"
                            stroke="#2563eb"
                            strokeWidth={1.5}
                            fillOpacity={1}
                            fill="url(#colorPrice)"
                          />
                        </ComposedChart>
                      </ResponsiveContainer>

                      {/* 图表叠加统计 */}
                      {!showForecast && (
                        <div className="absolute top-4 left-4 hidden sm:flex gap-4">
                          <div>
                            <div className="text-xs text-slate-400">
                              {text.latestPrice}
                            </div>
                            <div className="text-xl font-bold text-slate-800 font-mono">
                              {selectedSnapshot?.price.toLocaleString(locale)}
                            </div>
                          </div>
                          <div>
                            <div className="text-xs text-slate-400">
                              {text.priceMove}
                            </div>
                            <div
                              className={`text-sm font-bold font-mono mt-1 ${getPriceChangeTextClass(selectedSnapshot?.change_pct)}`}
                            >
                              {formatSignedPriceChange(selectedSnapshot?.change_pct, {}, locale, "%")}
                            </div>
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })()}

                {/* Forecast chart removed — overlay chart below is the single unified view */}
                {false && activeForecastData && (
                  <div className="flex-1 min-h-[280px] sm:min-h-[320px] flex flex-col border-t border-slate-100 bg-white">
                    <div className="flex items-center justify-between px-3 pt-2 pb-1 flex-wrap gap-2">
                      <span className="text-sm font-bold text-slate-600">
                        {language === "en"
                          ? `Forecast for next ${forecastHorizon} days — ${displayName(activeForecastData.product_id, activeForecastData.product_name)}`
                          : `未来${forecastHorizon}日预测 — ${activeForecastData.product_name}`}
                        {activeForecastData.seasonal_adj_pct !== 0 && (
                          <span
                            className={`ml-2 text-xs font-normal ${activeForecastData.seasonal_adj_pct > 0 ? "text-green-600" : "text-red-500"}`}
                          >
                            {text.seasonalAdj}{" "}
                            {activeForecastData.seasonal_adj_pct > 0 ? "+" : ""}
                            {activeForecastData.seasonal_adj_pct}%
                          </span>
                        )}
                      </span>
                      <div className="flex gap-2 text-xs flex-wrap items-center">
                        <span
                          className="inline-flex items-center gap-1.5 rounded border border-blue-200 bg-blue-50 px-2 py-1 text-[11px] text-blue-700"
                          title={`${text.liveLastUpdate}: ${liveUpdateAgeLabel}`}
                        >
                          <span className="inline-block h-2 w-2 rounded-full bg-blue-500" />
                          {text.liveSync}: {text.liveConnected} ·{" "}
                          {text.liveLastUpdate} {liveUpdateAgeLabel}
                        </span>
                        <span className="flex items-center gap-1.5">
                          <span className="inline-block w-4 h-[2px] bg-blue-600 rounded"></span>
                          {language === "en" ? "Actual price" : "实际价格"}
                        </span>
                        <span className="flex items-center gap-1.5">
                          <span className="inline-block w-3 h-3 rounded-full bg-green-600 border-2 border-white shadow"></span>
                          {text.execInRange}
                        </span>
                        <span className="flex items-center gap-1.5">
                          <span className="inline-block w-3 h-3 rounded-full bg-red-500 border-2 border-white shadow"></span>
                          {language === "en"
                            ? "Executed outside range"
                            : "执行超出区间"}
                        </span>
                        <span className="ml-1 pl-2 border-l border-slate-200 flex items-center gap-1.5">
                          <span className="inline-block w-4 h-[2px] bg-emerald-500 rounded"></span>
                          {text.bull}
                        </span>
                        <span className="flex items-center gap-1.5">
                          <span className="inline-block w-4 h-[2px] bg-blue-400 rounded"></span>
                          {text.base}
                        </span>
                        <span className="flex items-center gap-1.5">
                          <span className="inline-block w-4 h-[2px] bg-red-400 rounded"></span>
                          {text.bear}
                        </span>
                      </div>
                    </div>
                    <div className="flex-1 min-h-[240px] sm:min-h-[270px] px-2 pb-2">
                      <ResponsiveContainer width="100%" height="100%">
                        <ComposedChart data={forecastChartData}>
                          <defs>
                            <linearGradient
                              id="bullGrad"
                              x1="0"
                              y1="0"
                              x2="0"
                              y2="1"
                            >
                              <stop
                                offset="5%"
                                stopColor="#10b981"
                                stopOpacity={0.15}
                              />
                              <stop
                                offset="95%"
                                stopColor="#10b981"
                                stopOpacity={0}
                              />
                            </linearGradient>
                          </defs>
                          <CartesianGrid
                            strokeDasharray="3 3"
                            stroke={chart.grid}
                            vertical={false}
                          />
                          <XAxis
                            dataKey="date"
                            tick={{ fontSize: 9, fill: chart.tickMuted }}
                            tickFormatter={(v) => formatShortDateLabel(v)}
                            axisLine={false}
                            tickLine={false}
                            minTickGap={20}
                          />
                          <YAxis
                            domain={forecastChartAxis.domain}
                            ticks={forecastChartAxis.ticks}
                            orientation="right"
                            tick={{ fontSize: 9, fill: chart.tickMuted }}
                            axisLine={false}
                            tickLine={false}
                            interval={0}
                            tickFormatter={(v) =>
                              formatUnitPrice(Number(v), selectedChartUnit, locale)
                            }
                          />
                          <RechartsTooltip
                            contentStyle={tooltipStyle}
                            formatter={(
                              value: any,
                              name: string,
                              props: any,
                            ) => {
                              if (typeof value !== "number")
                                return [value, name];
                              const v = formatUnitPrice(
                                value,
                                selectedChartUnit,
                                locale,
                              );
                              if (name === "bullScenario")
                                return [v, text.bull];
                              if (name === "bearScenario")
                                return [v, text.bear];
                              if (name === "baseForecast")
                                return [v, text.base];
                              if (name === "previousAiPrice") {
                                const forecastDate =
                                  props?.payload?.previousAiForecastDate;
                                const label = forecastDate
                                  ? `${text.previousAiPrice} (${text.forecastMadeOn} ${forecastDate.slice(5)})`
                                  : text.previousAiPrice;
                                return [v, label];
                              }
                              if (name === "execInRange")
                                return [
                                  v,
                                  language === "en"
                                    ? "✓ Executed (in range)"
                                    : "✓ 执行在区间内",
                                ];
                              if (name === "execOutRange")
                                return [
                                  v,
                                  language === "en"
                                    ? "✗ Executed (outside range)"
                                    : "✗ 执行超出区间",
                                ];
                              if (name === "priceActual")
                                return [
                                  v,
                                  language === "en"
                                    ? "Actual price"
                                    : "实际价格",
                                ];
                              if (name === "todayMarker")
                                return [v, text.today];
                              return [v, text.history];
                            }}
                          />
                          {/* Confidence band: bear → bull fill */}
                          <Area
                            type="linear"
                            dataKey="bullScenario"
                            stroke="none"
                            fill="url(#bullGrad)"
                            fillOpacity={1}
                            dot={false}
                            legendType="none"
                          />
                          <Area
                            type="linear"
                            dataKey="bearScenario"
                            stroke="none"
                            fill="white"
                            fillOpacity={1}
                            dot={false}
                            legendType="none"
                          />
                          {/* Scenario lines */}
                          <Line
                            type="linear"
                            dataKey="bullScenario"
                            stroke="#10b981"
                            strokeWidth={1.5}
                            dot={false}
                            strokeDasharray="4 2"
                          />
                          <Line
                            type="monotone"
                            dataKey="priceActual"
                            stroke="#2563eb"
                            strokeWidth={2}
                            dot={false}
                          />
                          <Line
                            type="linear"
                            dataKey="previousAiPrice"
                            stroke="#f59e0b"
                            strokeWidth={2}
                            dot={false}
                            strokeDasharray="5 3"
                            connectNulls={false}
                            isAnimationActive={false}
                          />
                          <Line
                            type="linear"
                            dataKey="bearScenario"
                            stroke="#f87171"
                            strokeWidth={1.5}
                            dot={false}
                            strokeDasharray="4 2"
                          />
                          <Line
                            type="linear"
                            dataKey="baseForecast"
                            stroke="#60a5fa"
                            strokeWidth={2}
                            dot={false}
                            strokeDasharray="4 2"
                          />
                          <Line
                            type="monotone"
                            dataKey="todayMarker"
                            stroke="transparent"
                            strokeWidth={0}
                            isAnimationActive={false}
                            legendType="none"
                            activeDot={false}
                            dot={(props: any) => {
                              if (props?.value == null) return <g />;
                              return (
                                <circle
                                  key={`today-main-${props.index}`}
                                  cx={props.cx}
                                  cy={props.cy}
                                  r={9}
                                  fill="#2563eb"
                                  stroke="white"
                                  strokeWidth={3}
                                />
                              );
                            }}
                          />
                          {/* Executed price dots — in range (green) */}
                          <Line
                            type="monotone"
                            dataKey="execInRange"
                            stroke="transparent"
                            strokeWidth={0}
                            connectNulls={false}
                            isAnimationActive={false}
                            legendType="none"
                            dot={(props: any) => {
                              if (props?.value == null) return <g />;
                              return (
                                <circle
                                  key={`exec-in-${props.index}`}
                                  cx={props.cx}
                                  cy={props.cy}
                                  r={7}
                                  fill="#16a34a"
                                  stroke="white"
                                  strokeWidth={2}
                                />
                              );
                            }}
                            activeDot={{ r: 9, fill: "#16a34a" }}
                          />
                          {/* Executed price dots — outside range (red) */}
                          <Line
                            type="monotone"
                            dataKey="execOutRange"
                            stroke="transparent"
                            strokeWidth={0}
                            connectNulls={false}
                            isAnimationActive={false}
                            legendType="none"
                            dot={(props: any) => {
                              if (props?.value == null) return <g />;
                              return (
                                <circle
                                  key={`exec-out-${props.index}`}
                                  cx={props.cx}
                                  cy={props.cy}
                                  r={7}
                                  fill="#dc2626"
                                  stroke="white"
                                  strokeWidth={2}
                                />
                              );
                            }}
                            activeDot={{ r: 9, fill: "#dc2626" }}
                          />
                          {/* Divider at today */}
                          <ReferenceLine
                            x={
                              activeForecastData.history[
                                activeForecastData.history.length - 1
                              ]?.date
                            }
                            stroke="#475569"
                            strokeDasharray="4 3"
                            strokeWidth={1.5}
                            label={{
                              value: text.today,
                              position: "insideTopLeft",
                              fontSize: 9,
                              fill: chart.tick,
                            }}
                          />
                        </ComposedChart>
                      </ResponsiveContainer>
                    </div>
                  </div>
                )}

                {showForecast && (
                  <div className="border-t border-slate-100 bg-slate-50/40 p-3">
                    <div className="bg-white border border-slate-200 rounded-xl shadow-sm p-3">
                      <div className="flex items-center justify-between flex-wrap gap-2">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-sm font-bold text-slate-600">
                            {activeForecastData
                              ? language === "en"
                                ? `Forecast for next ${forecastHorizon} days — ${displayName(activeForecastData.product_id, activeForecastData.product_name)}`
                                : `未来${forecastHorizon}日预测 — ${activeForecastData.product_name}`
                              : text.decisionTracking}
                            {activeForecastData?.seasonal_adj_pct !== 0 &&
                              activeForecastData?.seasonal_adj_pct != null && (
                                <span
                                  className={`ml-2 text-xs font-normal ${activeForecastData.seasonal_adj_pct > 0
                                    ? "text-green-600"
                                    : "text-red-500"
                                    }`}
                                >
                                  {text.seasonalAdj}{" "}
                                  {activeForecastData.seasonal_adj_pct > 0
                                    ? "+"
                                    : ""}
                                  {activeForecastData.seasonal_adj_pct}%
                                </span>
                              )}
                          </span>
                          <span className="inline-flex items-center gap-1.5 rounded border border-blue-200 bg-blue-50 px-2 py-1 text-[11px] text-blue-700">
                            <span className="inline-block h-2 w-2 rounded-full bg-blue-500" />
                            {text.liveSync}: {text.liveConnected} ·{" "}
                            {text.liveLastUpdate} {liveUpdateAgeLabel}
                          </span>
                          {/* P0: Forecast Trust Tier Badge */}
                          {activeForecastData?.trust_tier?.tier && activeForecastData.trust_tier.tier !== "unknown" && (
                            <span
                              className={`inline-flex items-center gap-1 rounded px-2 py-1 text-[11px] font-semibold ${
                                activeForecastData.trust_tier.badge_color === "green"
                                  ? "border border-green-300 bg-green-50 text-green-700"
                                  : activeForecastData.trust_tier.badge_color === "yellow"
                                    ? "border border-yellow-300 bg-yellow-50 text-yellow-700"
                                    : activeForecastData.trust_tier.badge_color === "orange"
                                      ? "border border-orange-300 bg-orange-50 text-orange-700"
                                      : "border border-red-300 bg-red-50 text-red-700"
                              }`}
                              title={
                                language === "zh"
                                  ? (activeForecastData.trust_tier.reason_zh ?? "")
                                  : (activeForecastData.trust_tier.reason_en ?? "")
                              }
                            >
                              {activeForecastData.trust_tier.badge_color === "green" ? (
                                <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" /></svg>
                              ) : activeForecastData.trust_tier.badge_color === "yellow" ? (
                                <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
                              ) : activeForecastData.trust_tier.badge_color === "orange" ? (
                                <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg>
                              ) : (
                                <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" /></svg>
                              )}
                              {language === "zh"
                                ? (activeForecastData.trust_tier.badge_label_zh ?? activeForecastData.trust_tier.tier)
                                : (activeForecastData.trust_tier.badge_label_en ?? activeForecastData.trust_tier.tier)}
                            </span>
                          )}
                        </div>
                        <span className="px-2.5 py-1 rounded-lg text-xs border bg-blue-50 text-blue-700 border-blue-200">
                          {displayName(selectedProduct)}
                        </span>
                      </div>

                      {decisionLogError && (
                        <div className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-600">
                          {decisionLogError}
                        </div>
                      )}

                      {/* ── Single-product: overlay chart (history + AI range band + forecast + dots) ── */}
                      {decisionProductTab !== "all" && overlayChartData ? (
                        <>
                          <div className="mt-3 rounded-xl border border-slate-200 bg-slate-50/60 p-3">
                            <div className="mb-3 grid grid-cols-2 gap-1 text-center text-sm font-semibold">
                              <div className="rounded-lg bg-stone-200 py-1.5 text-slate-700">
                                {language === "en"
                                  ? "Past 30 days · AI accuracy review"
                                  : "过去30天 · AI准确率复盘"}
                              </div>
                              <div className="rounded-lg bg-blue-100 py-1.5 text-blue-700">
                                {language === "en"
                                  ? `Next ${forecastHorizon} days · Forecast`
                                  : `未来${forecastHorizon}天 · 预测`}
                              </div>
                            </div>

                            <div className="h-[390px]">
                              <ResponsiveContainer width="100%" height="100%">
                                <ComposedChart data={overlayChartData}>
                                  <defs>
                                    <linearGradient
                                      id="overlayBullGrad"
                                      x1="0"
                                      y1="0"
                                      x2="0"
                                      y2="1"
                                    >
                                      <stop
                                        offset="5%"
                                        stopColor="#10b981"
                                        stopOpacity={0.12}
                                      />
                                      <stop
                                        offset="95%"
                                        stopColor="#10b981"
                                        stopOpacity={0}
                                      />
                                    </linearGradient>
                                  </defs>
                                  <CartesianGrid
                                    strokeDasharray="2 4"
                                    stroke={chart.grid}
                                    vertical={true}
                                  />
                                  <XAxis
                                    dataKey="date"
                                    tick={{ fontSize: 11, fill: chart.tick }}
                                    tickFormatter={(v) =>
                                      formatShortDateLabel(v)
                                    }
                                    axisLine={false}
                                    tickLine={false}
                                    minTickGap={18}
                                  />
                                  <YAxis
                                    domain={overlayChartAxis.domain}
                                    ticks={overlayChartAxis.ticks}
                                    orientation="left"
                                    tick={{ fontSize: 11, fill: chart.tick }}
                                    axisLine={false}
                                    tickLine={false}
                                    interval={0}
                                    tickFormatter={(v) =>
                                      formatUnitPrice(Number(v), selectedChartUnit, locale)
                                    }
                                  />
                                  <RechartsTooltip
                                    filterNull={true}
                                    cursor={{
                                      stroke: chart.tickMuted,
                                      strokeDasharray: "4 4",
                                    }}
                                    contentStyle={tooltipStyle}
                                    labelFormatter={(value) =>
                                      formatShortDateLabel(value)
                                    }
                                    formatter={(
                                      value: any,
                                      name: string,
                                      props: any,
                                    ) => {
                                      if (typeof value !== "number")
                                        return [value, name];
                                      if (
                                        name === "bullBand" ||
                                        name === "bearBand" ||
                                        name === "aiRangeLow"
                                      ) {
                                        return null;
                                      }
                                      const currency = formatUnitPrice(
                                        value,
                                        selectedChartUnit,
                                        locale,
                                      );
                                      if (name === "price") {
                                        return [
                                          currency,
                                          displayName(decisionProductTab),
                                        ];
                                      }
                                      if (
                                        name === "execInRange" ||
                                        name === "execOutRange"
                                      ) {
                                        const payload = props?.payload;
                                        const execPrice =
                                          payload?.execActualPrice;
                                        const aiLow = payload?.execAiLow;
                                        const aiHigh = payload?.execAiHigh;
                                        const execStr =
                                          typeof execPrice === "number"
                                            ? formatUnitPrice(
                                              execPrice,
                                              selectedChartUnit,
                                              locale,
                                            )
                                            : "--";
                                        const rangeStr =
                                          typeof aiLow === "number" &&
                                            typeof aiHigh === "number"
                                            ? ` (AI: ${formatUnitPrice(aiLow, selectedChartUnit, locale)}–${formatUnitPrice(aiHigh, selectedChartUnit, locale)})`
                                            : "";
                                        const label =
                                          name === "execInRange"
                                            ? language === "en"
                                              ? `✓ Executed${rangeStr}`
                                              : `✓ 执行在区间内${rangeStr}`
                                            : language === "en"
                                              ? `✗ Executed outside range${rangeStr}`
                                              : `✗ 执行超出区间${rangeStr}`;
                                        return [execStr, label];
                                      }
                                      if (name === "aiRangeBand") {
                                        const low = props?.payload?.aiRangeLow;
                                        if (typeof low === "number") {
                                          return [
                                            `${formatUnitPrice(low, selectedChartUnit, locale)} - ${formatUnitPrice(low + value, selectedChartUnit, locale)}`,
                                            language === "en"
                                              ? "AI range"
                                              : "AI推荐区间",
                                          ];
                                        }
                                      }
                                      if (name === "previousAiPrice") {
                                        const forecastDate =
                                          props?.payload
                                            ?.previousAiForecastDate;
                                        const label = forecastDate
                                          ? `${text.previousAiPrice} (${text.forecastMadeOn} ${forecastDate.slice(5)})`
                                          : text.previousAiPrice;
                                        return [currency, label];
                                      }
                                      if (name === "base")
                                        return [currency, text.base];
                                      if (name === "bull")
                                        return [currency, text.bull];
                                      if (name === "bear")
                                        return [currency, text.bear];
                                      return [currency, name];
                                    }}
                                  />
                                  {/* AI recommended range band (past portion — at decision dates) */}
                                  <Area
                                    type="monotone"
                                    dataKey="aiRangeLow"
                                    fill="transparent"
                                    stroke="transparent"
                                    stackId="aiRange"
                                    legendType="none"
                                    connectNulls={true}
                                    isAnimationActive={false}
                                    dot={false}
                                  />
                                  <Area
                                    type="monotone"
                                    dataKey="aiRangeBand"
                                    fill="#bfdbfe"
                                    fillOpacity={0.55}
                                    stroke="#93c5fd"
                                    strokeWidth={0.5}
                                    stackId="aiRange"
                                    legendType="none"
                                    connectNulls={true}
                                    isAnimationActive={false}
                                    dot={false}
                                  />
                                  {/* Forecast confidence band: bear → bull */}
                                  <Area
                                    type="linear"
                                    dataKey="bullBand"
                                    stroke="none"
                                    fill="url(#overlayBullGrad)"
                                    fillOpacity={1}
                                    dot={false}
                                    legendType="none"
                                  />
                                  <Area
                                    type="linear"
                                    dataKey="bearBand"
                                    stroke="none"
                                    fill="white"
                                    fillOpacity={1}
                                    dot={false}
                                    legendType="none"
                                  />
                                  {/* Actual price line */}
                                  <Line
                                    type="monotone"
                                    dataKey="price"
                                    stroke="#2563eb"
                                    strokeWidth={4}
                                    dot={false}
                                    connectNulls={false}
                                    isAnimationActive={false}
                                  />
                                  {/* Forecast scenario lines */}
                                  <Line
                                    type="linear"
                                    dataKey="previousAiPrice"
                                    stroke="#f59e0b"
                                    strokeWidth={2.5}
                                    dot={false}
                                    strokeDasharray="5 3"
                                    connectNulls={false}
                                    isAnimationActive={false}
                                  />
                                  <Line
                                    type="linear"
                                    dataKey="bull"
                                    stroke="#10b981"
                                    strokeWidth={3}
                                    dot={false}
                                    strokeDasharray="4 2"
                                    isAnimationActive={false}
                                  />
                                  <Line
                                    type="linear"
                                    dataKey="base"
                                    stroke="#60a5fa"
                                    strokeWidth={3}
                                    dot={false}
                                    strokeDasharray="4 2"
                                    isAnimationActive={false}
                                  />
                                  <Line
                                    type="linear"
                                    dataKey="bear"
                                    stroke="#f87171"
                                    strokeWidth={3}
                                    dot={false}
                                    strokeDasharray="4 2"
                                    isAnimationActive={false}
                                  />
                                  <Line
                                    type="monotone"
                                    dataKey="todayMarker"
                                    stroke="transparent"
                                    strokeWidth={0}
                                    isAnimationActive={false}
                                    legendType="none"
                                    activeDot={false}
                                    dot={(props: any) => {
                                      if (props?.value == null) return <g />;
                                      return (
                                        <circle
                                          key={`today-overlay-${props.index}`}
                                          cx={props.cx}
                                          cy={props.cy}
                                          r={10}
                                          fill="#16a34a"
                                          stroke="white"
                                          strokeWidth={3}
                                        />
                                      );
                                    }}
                                  />
                                  {/* Executed price dots — in range (green) */}
                                  <Line
                                    type="monotone"
                                    dataKey="execInRange"
                                    stroke="transparent"
                                    strokeWidth={0}
                                    connectNulls={false}
                                    isAnimationActive={false}
                                    legendType="none"
                                    dot={(props: any) => {
                                      if (props?.value == null) return <g />;
                                      return (
                                        <circle
                                          key={`ov-in-${props.index}`}
                                          cx={props.cx}
                                          cy={props.cy}
                                          r={9}
                                          fill="#16a34a"
                                          stroke="white"
                                          strokeWidth={3}
                                        />
                                      );
                                    }}
                                    activeDot={{ r: 11, fill: "#16a34a" }}
                                  />
                                  {/* Executed price dots — outside range (red) */}
                                  <Line
                                    type="monotone"
                                    dataKey="execOutRange"
                                    stroke="transparent"
                                    strokeWidth={0}
                                    connectNulls={false}
                                    isAnimationActive={false}
                                    legendType="none"
                                    dot={(props: any) => {
                                      if (props?.value == null) return <g />;
                                      return (
                                        <circle
                                          key={`ov-out-${props.index}`}
                                          cx={props.cx}
                                          cy={props.cy}
                                          r={9}
                                          fill="#dc2626"
                                          stroke="white"
                                          strokeWidth={3}
                                        />
                                      );
                                    }}
                                    activeDot={{ r: 11, fill: "#dc2626" }}
                                  />
                                  {/* "Today" divider */}
                                  <ReferenceLine
                                    x={
                                      activeForecastData.history[
                                        activeForecastData.history.length - 1
                                      ]?.date
                                    }
                                    stroke="#475569"
                                    strokeDasharray="5 4"
                                    strokeWidth={2}
                                    label={{
                                      value: text.today,
                                      position: "top",
                                      fontSize: 11,
                                      fill: chart.tickMuted,
                                    }}
                                  />
                                </ComposedChart>
                              </ResponsiveContainer>
                            </div>
                          </div>

                          <div className="mt-2 flex flex-wrap gap-4 text-xs text-slate-600">
                            <span className="inline-flex items-center gap-1.5">
                              <span className="inline-block w-4 h-[2px] bg-blue-600 rounded" />
                              {language === "en" ? "Actual price" : "实际价格"}
                            </span>
                            <span className="inline-flex items-center gap-1.5">
                              <span className="inline-block w-3 h-3 rounded-sm bg-blue-200" />
                              {text.aiRange} (
                              {language === "en" ? "past" : "历史区间"})
                            </span>
                            <span className="inline-flex items-center gap-1.5">
                              <span className="inline-block w-4 h-[2px] bg-amber-500 rounded" />
                              {text.previousAiPrice}
                            </span>
                            <span className="inline-flex items-center gap-1.5">
                              <span className="inline-block w-3 h-3 rounded-full bg-green-600" />
                              {text.execInRange} ({inRangeCount})
                            </span>
                            <span className="inline-flex items-center gap-1.5">
                              <span className="inline-block w-3 h-3 rounded-full bg-red-500" />
                              {language === "en"
                                ? "Executed outside range"
                                : "执行超出区间"}{" "}
                              ({belowRangeCount + aboveRangeCount})
                            </span>
                            <span className="ml-1 pl-2 border-l border-slate-200 inline-flex items-center gap-1.5">
                              <span className="inline-block w-4 h-[2px] bg-emerald-500 rounded" />
                              {text.bull}
                            </span>
                            <span className="inline-flex items-center gap-1.5">
                              <span className="inline-block w-4 h-[2px] bg-blue-400 rounded" />
                              {text.base}
                            </span>
                            <span className="inline-flex items-center gap-1.5">
                              <span className="inline-block w-4 h-[2px] bg-red-400 rounded" />
                              {text.bear}
                            </span>
                          </div>
                        </>
                      ) : /* ── All Products: original bar chart ── */
                        decisionChartData.length === 0 ? (
                          <div className="mt-3 rounded-md border border-slate-200 bg-slate-50 px-3 py-8 text-center text-sm text-slate-500">
                            {text.noDecisionData}
                          </div>
                        ) : (
                          <>
                            <div className="mt-3 h-[320px]">
                              <ResponsiveContainer width="100%" height="100%">
                                <ComposedChart data={decisionChartData}>
                                  <CartesianGrid
                                    strokeDasharray="3 3"
                                    stroke={chart.grid}
                                    vertical={false}
                                  />
                                  <XAxis
                                    dataKey="label"
                                    tick={{ fontSize: 11, fill: chart.tick }}
                                    axisLine={false}
                                    tickLine={false}
                                    minTickGap={18}
                                  />
                                  <YAxis
                                    domain={decisionChartAxis.domain}
                                    ticks={decisionChartAxis.ticks}
                                    tick={{ fontSize: 11, fill: chart.tick }}
                                    axisLine={false}
                                    tickLine={false}
                                    interval={0}
                                    tickFormatter={(v) =>
                                      `¥${Math.round(v).toLocaleString(locale)}`
                                    }
                                  />
                                  <RechartsTooltip
                                    contentStyle={tooltipStyle}
                                    formatter={(
                                      value: any,
                                      name: string,
                                      props: any,
                                    ) => {
                                      if (name === "rangeSpan") {
                                        return [
                                          `¥${props.payload.rangeLow.toLocaleString(locale)} - ¥${props.payload.rangeHigh.toLocaleString(locale)}`,
                                          text.aiRange,
                                        ];
                                      }
                                      if (
                                        name === "executedPrice" &&
                                        typeof value === "number"
                                      ) {
                                        return [
                                          `¥${value.toLocaleString(locale)}`,
                                          language === "en"
                                            ? "Executed"
                                            : "执行价",
                                        ];
                                      }
                                      if (
                                        name === "noDealMarker" &&
                                        typeof value === "number"
                                      ) {
                                        return [
                                          `¥${value.toLocaleString(locale)}`,
                                          text.noDeal,
                                        ];
                                      }
                                      return [value, name];
                                    }}
                                  />
                                  <Bar
                                    dataKey="rangeOffset"
                                    stackId="range"
                                    fill="transparent"
                                  />
                                  <Bar
                                    dataKey="rangeSpan"
                                    stackId="range"
                                    fill="#bfdbfe"
                                    radius={[4, 4, 0, 0]}
                                    maxBarSize={28}
                                  />
                                  <Line
                                    type="monotone"
                                    dataKey="executedPrice"
                                    stroke="transparent"
                                    dot={(props: any) => {
                                      const state = props?.payload
                                        ?.pointState as DecisionPointState;
                                      const fill =
                                        state === "in_range"
                                          ? "#2f855a"
                                          : state === "above_range"
                                            ? "#3182ce"
                                            : "#e53e3e";
                                      if (props?.value == null) return <g />;
                                      return (
                                        <circle
                                          cx={props.cx}
                                          cy={props.cy}
                                          r={7}
                                          fill={fill}
                                        />
                                      );
                                    }}
                                    activeDot={{ r: 8 }}
                                    connectNulls={false}
                                    isAnimationActive={false}
                                  />
                                  <Line
                                    type="monotone"
                                    dataKey="noDealMarker"
                                    stroke="transparent"
                                    dot={(props: any) => {
                                      if (props?.value == null) return <g />;
                                      return (
                                        <circle
                                          cx={props.cx}
                                          cy={props.cy}
                                          r={7}
                                          fill={chart.tooltip.backgroundColor}
                                          stroke={chart.tickMuted}
                                          strokeWidth={2}
                                        />
                                      );
                                    }}
                                    activeDot={{ r: 8 }}
                                    connectNulls={false}
                                    isAnimationActive={false}
                                  />
                                </ComposedChart>
                              </ResponsiveContainer>
                            </div>

                            <div className="mt-2 flex flex-wrap gap-4 text-xs text-slate-600">
                              <span className="inline-flex items-center gap-1.5">
                                <span className="inline-block w-3 h-3 rounded-sm bg-blue-200" />
                                {text.aiRange}
                              </span>
                              <span className="inline-flex items-center gap-1.5">
                                <span className="inline-block w-3 h-3 rounded-full bg-green-600" />
                                {text.execInRange} ({inRangeCount})
                              </span>
                              <span className="inline-flex items-center gap-1.5">
                                <span className="inline-block w-3 h-3 rounded-full bg-blue-500" />
                                {text.execAboveRange} ({aboveRangeCount})
                              </span>
                              <span className="inline-flex items-center gap-1.5">
                                <span className="inline-block w-3 h-3 rounded-full bg-red-500" />
                                {text.execBelowRange} ({belowRangeCount})
                              </span>
                              <span className="inline-flex items-center gap-1.5">
                                <span className="inline-block w-3 h-3 rounded-full border-2 border-slate-400 bg-white" />
                                {text.noDeal} ({noDealCount})
                              </span>
                            </div>

                            <div className="mt-3 grid grid-cols-1 md:grid-cols-3 gap-2">
                              <div className="rounded-xl border border-green-200 bg-green-50 p-3">
                                <div className="text-[11px] font-bold uppercase tracking-wide text-green-700">
                                  {text.bestPerformer}
                                </div>
                                <div className="mt-1 text-sm text-slate-700">
                                  {bestPerformer
                                    ? `${bestPerformer.productName} — ${bestPerformer.hitRate}% ${text.hitRate}`
                                    : "--"}
                                </div>
                              </div>
                              <div className="rounded-xl border border-amber-200 bg-amber-50 p-3">
                                <div className="text-[11px] font-bold uppercase tracking-wide text-amber-700">
                                  {text.needsAttention}
                                </div>
                                <div className="mt-1 text-sm text-slate-700">
                                  {needsAttention
                                    ? `${needsAttention.productName} — ${needsAttention.hitRate}% ${text.hitRate}`
                                    : "--"}
                                </div>
                              </div>
                              <div className="rounded-xl border border-blue-200 bg-blue-50 p-3">
                                <div className="text-[11px] font-bold uppercase tracking-wide text-blue-700">
                                  {text.patternDetected}
                                </div>
                                <div className="mt-1 text-sm text-slate-700">
                                  {patternText}
                                </div>
                              </div>
                            </div>
                          </>
                        )}
                    </div>
                  </div>
                )}
              </div>

              {/* Bottom panel — all three stay mounted, toggle visibility for instant tab switching */}
              <div className="bg-white border border-slate-200 rounded-xl shadow-sm flex flex-col">
                <div className="h-10 border-b border-slate-100 flex items-center px-4 py-2 justify-between flex-wrap gap-1">
                  <div className="flex items-center gap-2">
                    <span className="font-semibold text-slate-700 text-xs tracking-wider">
                      {categoryTab === "midstream"
                        ? text.inventorySafety
                        : categoryTab === "downstream"
                          ? text.pricingActions
                          : text.priceChain}
                    </span>
                    <span className="hidden sm:inline text-[10px] text-slate-400">
                      {categoryTab === "midstream"
                        ? text.inventorySafety
                        : categoryTab === "downstream"
                          ? text.pricingActions
                          : text.chainHint}
                    </span>
                  </div>
                  <span className="text-[10px] text-slate-400 whitespace-nowrap">
                    {categoryTab === "midstream"
                      ? text.inventoryCalc
                      : categoryTab === "downstream"
                        ? text.pricingCalc
                        : text.mysqlCalc}
                  </span>
                </div>
                {categoryTab === "midstream" ? (
                  <InventorySafety />
                ) : (
                  <div className="min-h-[380px] flex-1">
                    <div
                      className={
                        categoryTab === "upstream" ? "block" : "hidden"
                      }
                    >
                      <PriceChain />
                    </div>
                    <div
                      className={
                        categoryTab === "downstream" ? "block" : "hidden"
                      }
                    >
                      <PricingActions />
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
