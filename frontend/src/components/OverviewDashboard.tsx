import React, { useEffect, useMemo, useState } from "react";
import { NO_DATA_LABEL, getPriceChangeTextClass } from "../lib/priceChange";
import { API_BASE_URL, fetchWithRetry } from "../services/api";

interface MarketSnapshotItem {
  id: string;
  name: string;
  price: number;
  date?: string | null;
  change_pct: number | null;
  weekly_change_pct: number | null;
  daily_gap_value?: number | null;
  weekly_gap_value?: number | null;
  daily_reference_date?: string | null;
  weekly_reference_date?: string | null;
  data_source_detail?: string;
  unit?: string;
  execution_price?: number | null;
  execution_price_date?: string | null;
  execution_price_change?: number | null;
  execution_price_change_pct?: number | null;
  execution_price_prev_date?: string | null;
}

const OVERVIEW_ROW_GRID =
  "grid grid-cols-[minmax(0,1fr)_64px_112px_78px] items-center gap-2";

interface EventItem {
  event_id: string;
  headline: string;
  detected_at: string;
  source_url?: string;
  event_type?: string;
  direction?: string;
  impact_magnitude?: number;
  certainty?: string;
  source_credibility?: number | string;
  usefulness_score?: number;
  relevance_score?: number;
  affected_commodities?: string[];
  causal_chain_hint?: string;
  raw_quote?: string;
  full_text?: string;
  relevance_to_c5_c9?: string;
  source_label?: string;
  summary?: string;
}

interface EventsResponse {
  events: EventItem[];
}

interface FxRateResponse {
  price: number | null;
  unit: string;
  as_of_date: string | null;
  series_product_id: string;
  series_price_type: string;
  data_source: string;
  available?: boolean;
}

interface ForecastPointLite {
  base?: number;
}

interface ForecastResponseLite {
  forecast?: ForecastPointLite[];
}

interface ForecastBaseBatchResponse {
  day: number;
  base_by_product?: Record<string, number | null>;
}

interface RegionalBatchResponse {
  items?: Record<string, RegionalPriceData>;
  errors?: Record<string, string>;
}

interface RegionalPriceRegion {
  price_type: string;
  label: string;
  is_internal: boolean;
  price: number;
  change_pct: number | null;
  date: string;
  days_stale: number;
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

interface OverviewDashboardProps {
  language: "en" | "zh";
  locale: string;
  snapshot: MarketSnapshotItem[];
  displayName: (id: string, fallback?: string) => string;
  groupIds?: Partial<Record<"upstream" | "midstream" | "downstream", string[]>>;
}

const GROUPS: Array<{
  key: "upstream" | "midstream" | "downstream";
  ids: string[];
}> = [
    {
      key: "upstream",
      ids: ["crude_oil", "naphtha", "cracked_c5", "cracked_c9"],
    },
    {
      key: "midstream",
      ids: ["isoprene", "piperylene", "dcpd", "raffinate_c5"],
    },
    {
      key: "downstream",
      ids: ["sis", "c5_resin", "blowing_agent", "styrene"],
    },
  ];

const GROUP_VISUALS: Record<
  "upstream" | "midstream" | "downstream",
  {
    priceHeader: string;
    futuresHeader: string;
    chip: string;
  }
> = {
  upstream: {
    priceHeader: "bg-gradient-to-r from-sky-500 to-blue-600",
    futuresHeader: "bg-gradient-to-r from-violet-500 to-indigo-600",
    chip: "bg-sky-50 text-sky-700 border-sky-200",
  },
  midstream: {
    priceHeader: "bg-gradient-to-r from-blue-500 to-indigo-600",
    futuresHeader: "bg-gradient-to-r from-indigo-500 to-purple-600",
    chip: "bg-indigo-50 text-indigo-700 border-indigo-200",
  },
  downstream: {
    priceHeader: "bg-gradient-to-r from-indigo-500 to-blue-700",
    futuresHeader: "bg-gradient-to-r from-purple-500 to-violet-700",
    chip: "bg-violet-50 text-violet-700 border-violet-200",
  },
};

const NEWS_LIMIT = 18;
const NEWS_COLUMN_LIMIT = 6;
const NEWS_WINDOW_HOURS = 168;
const NEWS_REFRESH_MS = 15 * 60 * 1000;

const RELATED_NEWS_TOKENS = [
  "c5",
  "c9",
  "naphtha",
  "crude",
  "isoprene",
  "piperylene",
  "dcpd",
  "raffinate",
  "styrene",
  "sis",
  "resin",
  "blowing",
  "石脑油",
  "原油",
  "裂解",
  "碳五",
  "碳九",
  "异戊二烯",
  "间戊二烯",
  "双环戊二烯",
  "抽余碳五",
  "苯乙烯",
  "树脂",
  "发泡剂",
];

const BAD_NEWS_PATTERNS = [
  "fallback extraction",
  "source signal may affect",
  "请求存在威胁",
  "已被拦截",
  "waf",
  "access denied",
  "forbidden",
  "captcha",
  "robot",
  "javascript enabled",
  "please enable javascript",
  "doesn't work properly without javascript",
  "验证",
  "拦截",
  "安全检测",
  "403",
  "404",
  "500",
];

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

function normalizeText(raw: string | undefined): string {
  return String(raw || "")
    .replace(/\s+/g, " ")
    .trim();
}

function cleanNarrativeText(raw: string | undefined): string {
  const text = normalizeText(raw);
  if (!text) return "";
  return text
    .replace(/^source key information\s*:\s*/i, "")
    .replace(/^来源关键信息\s*[:：]\s*/i, "")
    .trim();
}

function isLikelyUrl(value: string): boolean {
  const text = normalizeText(value).toLowerCase();
  return (
    text.startsWith("http://") ||
    text.startsWith("https://") ||
    text.startsWith("www.")
  );
}

function compactSnippet(value: string | undefined, maxLen = 120): string {
  const text = cleanNarrativeText(value);
  if (!text) return "";
  return text.length > maxLen ? `${text.slice(0, maxLen).trim()}...` : text;
}

function containsBadPattern(text: string): boolean {
  const normalized = normalizeText(text).toLowerCase();
  if (!normalized) return false;
  return BAD_NEWS_PATTERNS.some((pattern) =>
    normalized.includes(pattern.toLowerCase()),
  );
}

function qualityScore(item: EventItem): number {
  const usefulness =
    typeof item.usefulness_score === "number" ? item.usefulness_score : 0;
  const credibility = mapCredibility(item.source_credibility);
  const relevance =
    typeof item.relevance_score === "number" ? item.relevance_score : 0;
  const impact =
    typeof item.impact_magnitude === "number" ? item.impact_magnitude : 0;
  const recencyBoost = isWithinHours(item.detected_at, 24)
    ? 2
    : isWithinHours(item.detected_at, 72)
      ? 0.5
      : 0;
  const certaintyBoost = ["high", "confirmed", "certain", "高"].some((token) =>
    normalizeText(item.certainty).toLowerCase().includes(token),
  )
    ? 1
    : 0;
  return (
    usefulness * 2 +
    credibility * 1.5 +
    relevance +
    impact * 0.2 +
    certaintyBoost +
    recencyBoost
  );
}

function mapCredibility(credibility: number | string | undefined): number {
  if (typeof credibility === "number") {
    return credibility;
  }
  const normalized = normalizeText(String(credibility || ""))
    .toLowerCase()
    .replace(/[_\s-]/g, "");
  if (!normalized) return 0;
  if (normalized.includes("tier1")) return 0.95;
  if (normalized.includes("tier2")) return 0.7;
  if (normalized.includes("tier3")) return 0.45;
  if (normalized.includes("socialmedia")) return 0.3;
  return 0;
}

function isWithinHours(isoText: string | undefined, hours: number): boolean {
  if (!isoText) return false;
  const timestamp = new Date(isoText).getTime();
  if (!Number.isFinite(timestamp)) return false;
  const delta = Date.now() - timestamp;
  if (delta < 0) return true;
  return delta <= hours * 60 * 60 * 1000;
}

function formatCredibility(credibility: number | string | undefined): string {
  const value = mapCredibility(credibility);
  if (value <= 0) return "--";
  return value.toFixed(2);
}

function isProfessionalNews(item: EventItem): boolean {
  const headline = normalizeText(item.headline);
  const summary = normalizeText(
    item.summary ||
    item.causal_chain_hint ||
    item.raw_quote ||
    item.full_text ||
    "",
  );

  if (!headline || headline.length < 8 || isLikelyUrl(headline)) return false;
  if (containsBadPattern(headline) || containsBadPattern(summary)) return false;

  const combined = `${headline} ${summary}`;
  if (!isRelatedNews(item)) return false;
  if (qualityScore(item) < 2) return false;

  // Keep only items that carry domain signal, not crawl/system pages.
  return RELATED_NEWS_TOKENS.some((token) =>
    combined.toLowerCase().includes(token.toLowerCase()),
  );
}

function deriveSourceLabel(url: string | undefined): string {
  const text = normalizeText(url);
  if (!text) return "";
  try {
    const normalized =
      text.startsWith("http://") || text.startsWith("https://")
        ? text
        : `https://${text}`;
    const hostname = new URL(normalized).hostname;
    return hostname || text;
  } catch {
    return text;
  }
}

function deriveHeadline(item: EventItem): string {
  const headline = normalizeText(item.headline);
  if (headline && !isLikelyUrl(headline)) return headline;

  const quote = compactSnippet(item.raw_quote, 100);
  if (quote && !isLikelyUrl(quote)) return quote;

  const hint = compactSnippet(item.causal_chain_hint, 100);
  if (hint && !isLikelyUrl(hint)) return hint;

  const sourceLabel = deriveSourceLabel(item.source_url);
  return sourceLabel || headline || "Market event";
}

function deriveSummary(item: EventItem, headline: string): string {
  const candidates = [item.causal_chain_hint, item.raw_quote, item.full_text]
    .map((entry) => compactSnippet(entry, 160))
    .filter((entry) => entry && entry !== headline && !isLikelyUrl(entry));
  return candidates[0] || "";
}

type WeeklyImpactDirection = "up" | "down" | "neutral";
type WeeklyImpactConfidence = "high" | "medium" | "low";

interface WeeklyImpactSignal {
  direction: WeeklyImpactDirection;
  score: number;
  confidence: WeeklyImpactConfidence;
  rationale: string;
}

function inferDirectionFromText(item: EventItem): WeeklyImpactDirection {
  const text = [
    item.headline,
    item.summary,
    item.causal_chain_hint,
    item.raw_quote,
    item.full_text,
  ]
    .map((entry) => normalizeText(entry))
    .join(" ")
    .toLowerCase();

  const upHints = [
    "bullish",
    "rise",
    "rising",
    "increase",
    "upward",
    "outage",
    "shutdown",
    "tight",
    "shortage",
    "tariff",
    "sanction",
    "上涨",
    "上行",
    "偏强",
    "偏紧",
    "短缺",
    "检修",
    "停产",
    "限产",
    "加税",
    "制裁",
  ];
  const downHints = [
    "bearish",
    "fall",
    "falling",
    "decrease",
    "downward",
    "oversupply",
    "weak demand",
    "inventory build",
    "price cut",
    "下跌",
    "下行",
    "偏弱",
    "过剩",
    "累库",
    "降价",
    "需求疲软",
    "需求走弱",
  ];

  const upScore = upHints.reduce(
    (acc, token) => (text.includes(token) ? acc + 1 : acc),
    0,
  );
  const downScore = downHints.reduce(
    (acc, token) => (text.includes(token) ? acc + 1 : acc),
    0,
  );

  if (upScore > downScore) return "up";
  if (downScore > upScore) return "down";
  return "neutral";
}

function weeklyImpactSignal(item: EventItem): WeeklyImpactSignal {
  const explicitDirection = normalizeText(item.direction).toLowerCase();
  const direction: WeeklyImpactDirection = [
    "up",
    "bullish",
    "increase",
    "rise",
    "positive",
    "上涨",
    "上行",
  ].includes(explicitDirection)
    ? "up"
    : [
      "down",
      "bearish",
      "decrease",
      "fall",
      "negative",
      "下跌",
      "下行",
    ].includes(explicitDirection)
      ? "down"
      : inferDirectionFromText(item);

  const impact =
    typeof item.impact_magnitude === "number"
      ? Math.max(0, Math.min(100, item.impact_magnitude))
      : 0;
  const relevance =
    typeof item.relevance_score === "number"
      ? Math.max(0, Math.min(10, item.relevance_score))
      : 0;
  const usefulness =
    typeof item.usefulness_score === "number"
      ? Math.max(0, Math.min(10, item.usefulness_score))
      : 0;
  const credibility = Math.max(
    0,
    Math.min(1, mapCredibility(item.source_credibility)),
  );
  const recencyBoost = isWithinHours(item.detected_at, 24)
    ? 10
    : isWithinHours(item.detected_at, 72)
      ? 5
      : 0;
  const certainty = normalizeText(item.certainty).toLowerCase();
  const certaintyBoost = ["high", "confirmed", "certain", "高", "确认"].some(
    (t) => certainty.includes(t),
  )
    ? 10
    : ["medium", "中"].some((t) => certainty.includes(t))
      ? 5
      : 0;

  const scoreRaw =
    impact * 0.35 +
    relevance * 4 +
    usefulness * 2 +
    credibility * 20 +
    recencyBoost +
    certaintyBoost;
  const score = Math.max(0, Math.min(100, Math.round(scoreRaw)));

  const confidence: WeeklyImpactConfidence =
    score >= 75 ? "high" : score >= 45 ? "medium" : "low";

  const rationale =
    compactSnippet(
      item.causal_chain_hint ||
      item.summary ||
      item.raw_quote ||
      item.full_text,
      110,
    ) || "--";
  return { direction, score, confidence, rationale };
}

function weeklyImpactTone(direction: WeeklyImpactDirection): string {
  if (direction === "up")
    return "text-emerald-700 bg-emerald-50 border-emerald-200";
  if (direction === "down") return "text-rose-700 bg-rose-50 border-rose-200";
  return "text-slate-600 bg-slate-100 border-slate-200";
}

function weeklyConfidenceTone(confidence: WeeklyImpactConfidence): string {
  if (confidence === "high") return "text-blue-700 bg-blue-50 border-blue-200";
  if (confidence === "medium")
    return "text-amber-700 bg-amber-50 border-amber-200";
  return "text-slate-600 bg-slate-100 border-slate-200";
}

function weeklyDirectionLabel(
  direction: WeeklyImpactDirection,
  language: "en" | "zh",
): string {
  if (language === "zh") {
    if (direction === "up") return "下周偏强";
    if (direction === "down") return "下周偏弱";
    return "下周中性";
  }
  if (direction === "up") return "Next week: Upward pressure";
  if (direction === "down") return "Next week: Downward pressure";
  return "Next week: Neutral";
}

function weeklyConfidenceLabel(
  confidence: WeeklyImpactConfidence,
  language: "en" | "zh",
): string {
  if (language === "zh") {
    if (confidence === "high") return "高置信";
    if (confidence === "medium") return "中置信";
    return "低置信";
  }
  if (confidence === "high") return "High confidence";
  if (confidence === "medium") return "Medium confidence";
  return "Low confidence";
}

function buildDirectNewsExplanation(
  item: EventItem,
  language: "en" | "zh",
): string {
  const impact = weeklyImpactSignal(item);
  const detail = compactSnippet(
    item.summary || item.causal_chain_hint || item.raw_quote || item.full_text,
    150,
  );
  const dirLabel = weeklyDirectionLabel(impact.direction, language);
  const confLabel = weeklyConfidenceLabel(impact.confidence, language);

  if (language === "zh") {
    const core =
      detail || "该信息与产业链供需和情绪有关，需结合价格与库存跟踪确认。";
    return `判断：${dirLabel}（影响 ${impact.score}/100，${confLabel}）。${core}`;
  }

  const core =
    detail ||
    "This signal may affect chain supply-demand and sentiment; validate with price and inventory moves.";
  return `View: ${dirLabel} (impact ${impact.score}/100, ${confLabel}). ${core}`;
}

function sortByNewsPriority(items: EventItem[]): EventItem[] {
  return [...items].sort((left, right) => {
    const leftDaily = isWithinHours(left.detected_at, 24) ? 1 : 0;
    const rightDaily = isWithinHours(right.detected_at, 24) ? 1 : 0;
    if (leftDaily !== rightDaily) return rightDaily - leftDaily;
    const impactScoreDiff =
      weeklyImpactSignal(right).score - weeklyImpactSignal(left).score;
    if (impactScoreDiff !== 0) return impactScoreDiff;
    const scoreDiff = qualityScore(right) - qualityScore(left);
    if (scoreDiff !== 0) return scoreDiff;
    return String(right.detected_at || "").localeCompare(
      String(left.detected_at || ""),
    );
  });
}

function dedupeByHeadline(items: EventItem[]): EventItem[] {
  const seen = new Set<string>();
  const result: EventItem[] = [];
  for (const item of items) {
    const key = normalizeText(item.headline).toLowerCase();
    if (!key || seen.has(key)) continue;
    seen.add(key);
    result.push(item);
  }
  return result;
}

function normalizeEvent(item: EventItem): EventItem {
  const headline = deriveHeadline(item);
  return {
    ...item,
    headline,
    summary: deriveSummary(item, headline),
    source_label: deriveSourceLabel(item.source_url),
  };
}

function selectUsefulNews(items: EventItem[]): EventItem[] {
  const highQuality = items.filter(isProfessionalNews);
  const relatedFallback = items.filter((item) => {
    const headline = normalizeText(item.headline);
    const summary = normalizeText(item.summary);
    return (
      isRelatedNews(item) &&
      !containsBadPattern(headline) &&
      !containsBadPattern(summary)
    );
  });
  const weeklyRelated = relatedFallback.filter((item) =>
    isWithinHours(item.detected_at, NEWS_WINDOW_HOURS),
  );
  const highQualityIds = new Set(
    highQuality
      .filter((item) => isWithinHours(item.detected_at, NEWS_WINDOW_HOURS))
      .map((item) => item.event_id),
  );
  const effectiveWeekly = weeklyRelated.filter(
    (item) =>
      highQualityIds.has(item.event_id) ||
      isWithinHours(item.detected_at, 24) ||
      weeklyImpactSignal(item).score >= 20 ||
      qualityScore(item) >= 2,
  );
  const base = effectiveWeekly.length > 0 ? effectiveWeekly : weeklyRelated;
  return dedupeByHeadline(sortByNewsPriority(base)).slice(0, NEWS_LIMIT);
}

function classifyNewsColumn(item: EventItem): "upstream" | "midstream" | "downstream" {
  const eventType = normalizeText(item.event_type).toLowerCase();
  const sourceUrl = normalizeText(item.source_url).toLowerCase();
  const affected = Array.isArray(item.affected_commodities)
    ? item.affected_commodities.join(" ").toLowerCase()
    : "";
  const combined = [
    item.headline,
    item.summary,
    item.causal_chain_hint,
    item.raw_quote,
    item.full_text,
    affected,
  ]
    .map(normalizeText)
    .join(" ")
    .toLowerCase();

  if (eventType.includes("demand")) {
    if (
      sourceUrl.includes("ndrc.gov.cn") ||
      sourceUrl.includes("cpcia.org.cn") ||
      combined.includes("政策") ||
      combined.includes("需求") ||
      combined.includes("market")
    ) {
      return "downstream";
    }
    if (sourceUrl.includes("oilchem.net") || sourceUrl.includes("sci99.com")) {
      return "midstream";
    }
  }

  if (
    eventType.includes("opec") ||
    eventType.includes("geopolitical") ||
    eventType.includes("shipping") ||
    eventType.includes("sanction") ||
    /\b(crude|brent|wti|naphtha)\b/.test(combined) ||
    combined.includes("原油") ||
    combined.includes("石脑油") ||
    combined.includes("欧佩克")
  ) {
    return "upstream";
  }

  if (
    eventType.includes("sinopec") ||
    eventType.includes("plant") ||
    /\b(c5|c9|isoprene|piperylene|dcpd|sis|styrene|resin)\b/.test(combined) ||
    combined.includes("碳五") ||
    combined.includes("碳九") ||
    combined.includes("异戊二烯") ||
    combined.includes("间戊二烯") ||
    combined.includes("双环戊二烯") ||
    combined.includes("苯乙烯") ||
    combined.includes("树脂")
  ) {
    return "midstream";
  }

  return "downstream";
}

function isRelatedNews(item: EventItem): boolean {
  const relevance = normalizeText(item.relevance_to_c5_c9).toLowerCase();
  if (
    relevance &&
    (relevance.includes("direct") ||
      relevance.includes("high") ||
      relevance.includes("相关") ||
      relevance.includes("直接"))
  ) {
    return true;
  }

  const affected = Array.isArray(item.affected_commodities)
    ? item.affected_commodities.join(" ")
    : "";
  const merged = [
    item.headline,
    item.causal_chain_hint,
    item.raw_quote,
    item.full_text,
    affected,
  ]
    .map((entry) => normalizeText(entry))
    .join(" ")
    .toLowerCase();

  return RELATED_NEWS_TOKENS.some((token) =>
    merged.includes(token.toLowerCase()),
  );
}

function formatDateLabel(language: "en" | "zh", locale: string): string {
  const now = new Date();
  if (language === "zh") {
    return now.toLocaleDateString("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      weekday: "long",
    });
  }
  return now.toLocaleDateString(locale, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    weekday: "long",
  });
}

function directionTone(direction: string | undefined): string {
  const normalized = String(direction || "").toLowerCase();
  if (["up", "bullish", "increase", "rise", "positive"].includes(normalized)) {
    return "text-emerald-700 bg-emerald-50 border-emerald-200";
  }
  if (
    ["down", "bearish", "decrease", "fall", "negative"].includes(normalized)
  ) {
    return "text-rose-700 bg-rose-50 border-rose-200";
  }
  return "text-slate-600 bg-slate-100 border-slate-200";
}

function formatNewsTimestamp(
  value: string | undefined,
  language: "en" | "zh",
  locale: string,
): string {
  if (!value) return "--";
  const d = new Date(value);
  if (!Number.isFinite(d.getTime())) return value;
  if (language === "zh") {
    return d.toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  }
  return d.toLocaleString(locale, {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export const OverviewDashboard: React.FC<OverviewDashboardProps> = ({
  language,
  locale,
  snapshot,
  displayName,
  groupIds,
}) => {
  const text =
    language === "en"
      ? {
        overview: "Overview",
        date: "Date",
        brent: "Brent Crude Oil",
        usdrmb: "USD/RMB",
        unavailable: "Feed unavailable",
        prices: "Prices of Chemical Products",
        futures: "Futures Movement",
        industryNews: "Industry News",
        upstream: "Upstream",
        midstream: "Midstream",
        downstream: "Downstream",
        latestPrice: "Latest",
        dailyMove: "Daily",
        weeklyMove: "Weekly",
        futurePrice: "Next Price",
        gap: "Gap",
        source: "Source",
        impact: "Impact",
        confidence: "Confidence",
        signal: "Signal",
        spread: "Spread",
        realization: "Realization",
        belowCostFloor: "Below cost floor",
        costFloor: "Cost floor",
        dailyFeed: "Daily intelligence feed",
        updatedAt: "Updated",
        today: "Today",
        openLink: "Open",
        noNews: "No recent industry events found.",
        reportDate: "Report Date",
        marketIndicators: "Market Indicators",
        naphthaLabel: "Japanese Naphtha CFR",
        crackedC5Label: "Cracked C5",
        overviewTitle: "Chemical Product Market Overview",
        futuresWeeklyGapTitle: "7-Day AI Price Outlook · EDIA Forecast Model",
        forecast7dLabel: "7-day forecast",
        forecastLabel: "Forecast",
        industryIntelligenceTitle: "Industry Intelligence",
        items: "items",
        product: "Product",
        dataStatus: "Data Status",
        dataAvailable: "Data Available",
        noDataAvailable: "No Data Available",
        price: "Price",
        loading: "Loading...",
        regionalUnavailable: "No regional data",
        execution: "Execution",
        feedstockAndCrude: "Feedstock & Crude",
        midstreamProducts: "Midstream Products",
        downstreamAndMarket: "Downstream & Market",
        sourcesNote:
          "Sources: Longzhong · SCI · Sinopec · ERP · Internal use only · All prices RMB/mt unless noted",
      }
      : {
        overview: "总览",
        date: "日期",
        brent: "布伦特原油",
        usdrmb: "美元/人民币",
        unavailable: "暂无实时数据",
        prices: "化工产品价格",
        futures: "期货/行情波动",
        industryNews: "行业资讯",
        upstream: "上游",
        midstream: "中游",
        downstream: "下游",
        latestPrice: "最新价",
        dailyMove: "日涨跌",
        weeklyMove: "周涨跌",
        futurePrice: "下周价格",
        gap: "价差",
        source: "来源",
        impact: "影响",
        confidence: "可信度",
        signal: "信号",
        spread: "价差",
        realization: "实现率",
        belowCostFloor: "低于成本底价",
        costFloor: "成本底价",
        dailyFeed: "每日情报看板",
        updatedAt: "更新时间",
        today: "今日",
        openLink: "查看",
        noNews: "暂无近期行业事件。",
        reportDate: "报告日期",
        marketIndicators: "市场指标",
        naphthaLabel: "日本石脑油 CFR",
        crackedC5Label: "裂解碳五",
        overviewTitle: "化工产品市场总览",
        futuresWeeklyGapTitle: "7日AI价格展望 · EDIA预测模型",
        forecast7dLabel: "7日预测",
        forecastLabel: "预测",
        industryIntelligenceTitle: "行业资讯动态",
        items: "条",
        product: "产品",
        dataStatus: "数据状态",
        dataAvailable: "有今日数据",
        noDataAvailable: "今日暂无数据",
        price: "价格",
        loading: "加载中...",
        regionalUnavailable: "暂无区域数据",
        execution: "执行",
        feedstockAndCrude: "石化要闻",
        midstreamProducts: "基础化工",
        downstreamAndMarket: "行业动态",
        sourcesNote:
          "来源：隆众 · SCI · 中石化 · ERP · 仅供内部使用 · 除特别说明外单位为人民币/吨",
      };

  const [news, setNews] = useState<EventItem[]>([]);
  const [newsLoading, setNewsLoading] = useState(false);
  const [newsError, setNewsError] = useState<string | null>(null);
  const [newsUpdatedAt, setNewsUpdatedAt] = useState<string>("");
  const [fxRate, setFxRate] = useState<FxRateResponse | null>(null);
  const [regionalCache, setRegionalCache] = useState<
    Record<string, RegionalPriceData>
  >({});
  const [loadingRegionalProducts, setLoadingRegionalProducts] = useState<
    Record<string, boolean>
  >({});
  const [failedRegionalProducts, setFailedRegionalProducts] = useState<
    Record<string, boolean>
  >({});
  const [weeklyForecastBaseByProduct, setWeeklyForecastBaseByProduct] =
    useState<Record<string, number | null>>({});

  useEffect(() => {
    let cancelled = false;

    const loadNews = async () => {
      setNewsLoading(true);
      setNewsError(null);
      try {
        const collectFromWindow = async (
          hours: number,
          minUsefulnessScore: number,
        ) => {
          const res = await fetchWithRetry(
            `${API_BASE_URL}/api/events/latest?hours=${hours}&review_status=approved&min_usefulness_score=${minUsefulnessScore}`,
            { timeout: 15000, retries: 0 },
          );
          const payload = (await res.json()) as EventsResponse;
          return Array.isArray(payload.events) ? payload.events : [];
        };

        const weeklyEvents = await collectFromWindow(NEWS_WINDOW_HOURS, 0);
        const selected = selectUsefulNews(weeklyEvents.map(normalizeEvent));

        if (!cancelled) {
          setNews(selected);
          setNewsUpdatedAt(new Date().toISOString());
        }
      } catch (err) {
        if (!cancelled) {
          // Suppress AbortError (fetch timeout or component unmount) — not an actionable error for the user.
          const isAbort =
            (err instanceof DOMException && err.name === "AbortError") ||
            (err instanceof Error && err.message.toLowerCase().includes("aborted"));
          if (!isAbort) {
            setNewsError(
              err instanceof Error ? err.message : "Failed to load events",
            );
          }
          setNews([]);
        }
      } finally {
        if (!cancelled) {
          setNewsLoading(false);
        }
      }
    };

    void loadNews();
    const timer = window.setInterval(() => {
      void loadNews();
    }, NEWS_REFRESH_MS);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;

    const loadFxRate = async () => {
      try {
        const res = await fetchWithRetry(
          `${API_BASE_URL}/api/v2/market/fx/usd-cny`,
          { timeout: 6000, retries: 0 },
        );
        const payload = (await res.json()) as FxRateResponse;
        if (!cancelled) {
          setFxRate(payload);
        }
      } catch {
        if (!cancelled) {
          setFxRate(null);
        }
      }
    };

    void loadFxRate();
    return () => {
      cancelled = true;
    };
  }, []);

  const snapshotById = useMemo(
    () => new Map(snapshot.map((item) => [item.id, item])),
    [snapshot],
  );

  const groupedRows = useMemo(
    () =>
      GROUPS.map((group) => ({
        key: group.key,
        rows: (groupIds?.[group.key] ?? group.ids)
          .map((id) => snapshotById.get(id))
          .filter(Boolean) as MarketSnapshotItem[],
      })),
    [groupIds, snapshotById],
  );

  const regionalProductIds = useMemo(
    () =>
      Array.from(
        new Set(groupedRows.flatMap((group) => group.rows.map((row) => row.id))),
      ),
    [groupedRows],
  );
  const regionalProductIdsKey = regionalProductIds.join(",");

  useEffect(() => {
    let cancelled = false;

    const loadWeeklyForecastBase = async () => {
      const productIds = Array.from(
        new Set(
          groupedRows.flatMap((group) => group.rows.map((row) => row.id)),
        ),
      );

      if (productIds.length === 0) {
        if (!cancelled) {
          setWeeklyForecastBaseByProduct({});
        }
        return;
      }

      try {
        const res = await fetchWithRetry(
          `${API_BASE_URL}/api/market/forecast-base-batch?product_ids=${encodeURIComponent(productIds.join(","))}&day=7`,
          { timeout: 9000, retries: 1, backoff: 300 },
        );
        const payload = (await res.json()) as ForecastBaseBatchResponse;
        const mapped =
          payload.base_by_product && typeof payload.base_by_product === "object"
            ? payload.base_by_product
            : {};

        if (!cancelled) {
          setWeeklyForecastBaseByProduct(mapped);
        }
      } catch {
        if (!cancelled) {
          setWeeklyForecastBaseByProduct({});
        }
      }
    };

    void loadWeeklyForecastBase();
    return () => {
      cancelled = true;
    };
  }, [groupedRows]);
  const brent = snapshotById.get("crude_oil") ?? null;
  const naphtha = snapshotById.get("naphtha") ?? null;
  const crackedC5 = snapshotById.get("cracked_c5") ?? null;
  const hasFxRate =
    !!fxRate &&
    fxRate.available !== false &&
    fxRate.price !== null &&
    !!fxRate.as_of_date;
  const usdrmb = hasFxRate ? fxRate.price : null;
  const usdCardUnitLabel = hasFxRate
    ? `${fxRate.unit} · ${fxRate.as_of_date}`
    : text.unavailable;

  const groupLabel = (key: "upstream" | "midstream" | "downstream") => {
    if (key === "upstream") return text.upstream;
    if (key === "midstream") return text.midstream;
    return text.downstream;
  };

  useEffect(() => {
    let isMounted = true;

    const requestedIds = regionalProductIdsKey
      .split(",")
      .map((id) => id.trim())
      .filter(Boolean);

    if (requestedIds.length === 0) {
      return;
    }

    setLoadingRegionalProducts((prev) => {
      const next = { ...prev };
      requestedIds.forEach((id) => {
        next[id] = true;
      });
      return next;
    });

    const loadMissingRegionalBatch = async () => {
      try {
        const res = await fetchWithRetry(
          `${API_BASE_URL}/api/v2/market/regional-batch?product_ids=${encodeURIComponent(requestedIds.join(","))}`,
          { timeout: 30000, retries: 2, backoff: 1000 },
        );
        const payload = (await res.json()) as RegionalBatchResponse;
        const items =
          payload.items && typeof payload.items === "object"
            ? payload.items
            : {};
        const errors =
          payload.errors && typeof payload.errors === "object"
            ? payload.errors
            : {};
        const returnedIds = new Set(Object.keys(items));
        const erroredIds = new Set(Object.keys(errors));
        const failedIds = requestedIds.filter(
          (id) => !returnedIds.has(id) || erroredIds.has(id),
        );

        if (isMounted) {
          setRegionalCache((prev) => ({ ...prev, ...items }));
          if (failedIds.length > 0) {
            setFailedRegionalProducts((prev) => {
              const next = { ...prev };
              failedIds.forEach((id) => {
                next[id] = true;
              });
              return next;
            });
          }
        }
      } catch (e) {
        console.error("load regional batch failed:", e);
        if (isMounted) {
          setFailedRegionalProducts((prev) => {
            const next = { ...prev };
            requestedIds.forEach((id) => {
              next[id] = true;
            });
            return next;
          });
        }
      } finally {
        if (isMounted) {
          setLoadingRegionalProducts((prev) => {
            const next = { ...prev };
            requestedIds.forEach((id) => {
              delete next[id];
            });
            return next;
          });
        }
      }
    };

    void loadMissingRegionalBatch();

    return () => {
      isMounted = false;
    };
  }, [regionalProductIdsKey]);

  const toAbsoluteChange = (
    latestPrice: number | undefined,
    pct: number | null | undefined,
  ): number | null => {
    if (latestPrice == null || pct == null) return null;
    const denominator = 100 + pct;
    if (denominator === 0) return null;
    return latestPrice * (pct / denominator);
  };

  const resolveGap = (
    latestPrice: number | undefined,
    pct: number | null | undefined,
    explicitGap: number | null | undefined,
  ): number | null => {
    if (typeof explicitGap === "number" && Number.isFinite(explicitGap)) {
      return explicitGap;
    }
    return toAbsoluteChange(latestPrice, pct);
  };

  const formatAbsoluteChange = (
    latestPrice: number | undefined,
    pct: number | null | undefined,
    explicitGap?: number | null,
  ) => {
    const value = resolveGap(latestPrice, pct, explicitGap);
    if (value == null || !Number.isFinite(value)) return NO_DATA_LABEL;
    const abs = Math.abs(value);
    const formatted = abs.toLocaleString(locale, {
      minimumFractionDigits: abs < 10 ? 2 : 0,
      maximumFractionDigits: abs < 10 ? 2 : 0,
    });
    return `${value > 0 ? "+" : value < 0 ? "-" : ""}${formatted}`;
  };

  const changeSignal = (
    latestPrice: number | undefined,
    pct: number | null | undefined,
    explicitGap?: number | null,
  ): number | null => {
    const value = resolveGap(latestPrice, pct, explicitGap);
    if (value == null || !Number.isFinite(value)) return pct ?? null;
    return value;
  };

  const formatPriceValue = (value: number | null | undefined) => {
    if (value == null || !Number.isFinite(value)) return NO_DATA_LABEL;
    return value.toLocaleString(locale, {
      minimumFractionDigits: 0,
      maximumFractionDigits: 2,
    });
  };

  const changeColor = (
    value: number | null | undefined,
    neutral = "text-slate-500",
  ) => {
    if (value == null) return neutral;
    return getPriceChangeTextClass(value);
  };

  const todayIsoDate = useMemo(() => {
    const now = new Date();
    const month = String(now.getMonth() + 1).padStart(2, "0");
    const day = String(now.getDate()).padStart(2, "0");
    return `${now.getFullYear()}-${month}-${day}`;
  }, []);

  const rowHasTodayData = (dateValue: string | null | undefined) =>
    !!dateValue && String(dateValue).slice(0, 10) === todayIsoDate;

  const formatShortDate = (dateValue: string | null | undefined) => {
    const normalized = String(dateValue || "").slice(0, 10);
    return normalized ? normalized.slice(5) : "--";
  };

  const dateTextClass = (dateValue: string | null | undefined) =>
    dateValue ? "text-slate-800" : "text-slate-400";

  const newsColumns = useMemo(() => {
    const columns: Record<"upstream" | "midstream" | "downstream", EventItem[]> = {
      upstream: [],
      midstream: [],
      downstream: [],
    };
    const overflow: EventItem[] = [];

    for (const item of news) {
      const columnKey = classifyNewsColumn(item);
      if (columns[columnKey].length < NEWS_COLUMN_LIMIT) {
        columns[columnKey].push(item);
      } else {
        overflow.push(item);
      }
    }

    const targets: Array<"upstream" | "midstream" | "downstream"> = [
      "upstream",
      "midstream",
      "downstream",
    ];
    for (const item of overflow) {
      const target = targets
        .filter((key) => columns[key].length < NEWS_COLUMN_LIMIT)
        .sort((left, right) => columns[left].length - columns[right].length)[0];
      if (!target) break;
      columns[target].push(item);
    }

    return [columns.upstream, columns.midstream, columns.downstream];
  }, [news]);

  return (
    <div className="min-h-[calc(100vh-130px)] bg-stone-50">
      <div className="flex min-h-[calc(100vh-130px)]">
        <aside className="hidden xl:flex w-[168px] shrink-0 flex-col bg-gradient-to-b from-[#071424] via-[#0d2040] to-[#163256] px-3.5 py-5 text-slate-100">
          <div className="rounded-lg border border-white/10 bg-white/5 px-3 py-2.5">
            <p className="text-[10px] md:text-[11px] uppercase tracking-[0.08em] text-slate-400">
              {text.reportDate}
            </p>
            <p className="mt-1 text-[16px] font-semibold text-white">
              {formatDateLabel(language, locale)}
            </p>
          </div>

          <p className="mt-4 mb-2 text-[10px] md:text-[11px] uppercase tracking-[0.1em] text-slate-400">
            {text.marketIndicators}
          </p>

          <div className="space-y-1.5">
            <div className="rounded-md border border-white/10 bg-white/5 px-2.5 py-2">
              <p className="text-[9px] md:text-[10px] text-slate-400">
                {text.brent}
              </p>
              <p className="font-mono text-[15px] font-semibold text-white">
                {brent ? brent.price.toLocaleString(locale) : NO_DATA_LABEL}
              </p>
              <p
                className={`font-mono text-[11px] md:text-[12px] ${changeColor(changeSignal(brent?.price, brent?.change_pct), "text-slate-400")}`}
              >
                {brent
                  ? formatAbsoluteChange(
                    brent.price,
                    brent.change_pct,
                    brent.daily_gap_value,
                  )
                  : NO_DATA_LABEL}
              </p>
            </div>

            <div className="rounded-md border border-white/10 bg-white/5 px-2.5 py-2">
              <p className="text-[9px] md:text-[10px] text-slate-400">
                {text.naphthaLabel}
              </p>
              <p className="font-mono text-[15px] font-semibold text-white">
                {naphtha ? naphtha.price.toLocaleString(locale) : NO_DATA_LABEL}
              </p>
              <p
                className={`font-mono text-[11px] md:text-[12px] ${changeColor(changeSignal(naphtha?.price, naphtha?.change_pct), "text-slate-400")}`}
              >
                {naphtha
                  ? formatAbsoluteChange(
                    naphtha.price,
                    naphtha.change_pct,
                    naphtha.daily_gap_value,
                  )
                  : NO_DATA_LABEL}
              </p>
            </div>

            <div className="rounded-md border border-white/10 bg-white/5 px-2.5 py-2">
              <p className="text-[9px] md:text-[10px] text-slate-400">
                {text.crackedC5Label}
              </p>
              <p className="font-mono text-[15px] font-semibold text-white">
                {crackedC5 ? crackedC5.price.toLocaleString(locale) : NO_DATA_LABEL}
              </p>
              <p
                className={`font-mono text-[11px] md:text-[12px] ${changeColor(changeSignal(crackedC5?.price, crackedC5?.change_pct), "text-slate-400")}`}
              >
                {crackedC5
                  ? formatAbsoluteChange(
                    crackedC5.price,
                    crackedC5.change_pct,
                    crackedC5.daily_gap_value,
                  )
                  : NO_DATA_LABEL}
              </p>
            </div>
          </div>
        </aside>

        <main className="min-w-0 flex-1 px-3 py-4 md:px-4 lg:px-5">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 pb-3">
            <h2 className="text-[15px] md:text-[16px] font-semibold text-slate-800">
              {text.overviewTitle}
            </h2>
          </div>

          <section className="mb-6">
            <h3 className="mb-3 flex items-center gap-2 text-[11px] md:text-[12px] font-semibold uppercase tracking-[0.08em] text-slate-500">
              <span className="block h-3 w-[3px] rounded bg-blue-600" />
              {text.prices}
            </h3>

            <div className="grid grid-cols-1 gap-2.5 lg:grid-cols-3">
              {groupedRows.map((group) => (
                <article
                  key={group.key}
                  className="overflow-hidden rounded-xl border border-slate-200 bg-white"
                >
                  <div
                    className={`${GROUP_VISUALS[group.key].priceHeader} flex items-center justify-between px-3.5 py-2 text-white`}
                  >
                    <h4 className="text-[22px] font-semibold">
                      {groupLabel(group.key)}
                    </h4>
                    <span className="rounded-full bg-white/20 px-2 py-0.5 text-[11px] md:text-[12px] font-medium">
                      {group.rows.length}
                    </span>
                  </div>

                  <div className="grid grid-cols-[minmax(0,1fr)_64px_112px_78px] gap-2 border-b border-slate-200 bg-slate-50 px-3.5 py-1.5 text-[10px] md:text-[11px] font-semibold uppercase tracking-[0.04em] text-slate-400">
                    <span>{text.product}</span>
                    <span className="text-right">{text.date}</span>
                    <span className="text-right">{text.price}</span>
                    <span className="text-right">{text.dailyMove}</span>
                  </div>

                  <div>
                    {group.rows.map((item) => {
                      const regional = regionalCache[item.id];
                      const isLoadingThis = !!loadingRegionalProducts[item.id];

                      return (
                        <div
                          key={item.id}
                          className="overview-product-unit product-unit border-b border-slate-200 last:border-b-0"
                        >
                          <div className="overview-product-label px-3.5 py-2 border-b border-slate-200">
                            <span className="overview-exec-primary text-[14px] md:text-[15px] font-semibold">
                              {displayName(item.id, item.name)}
                            </span>
                          </div>

                          <div className="overview-regional-panel border-t border-slate-200">
                            {item.execution_price != null && (
                              <div className={`overview-regional-row ${OVERVIEW_ROW_GRID} border-b border-slate-200 px-3.5 py-1.5 text-[12px] md:text-[13px] overview-regional-internal`}>
                                <span className="font-semibold text-blue-700 truncate">
                                  {text.execution}
                                </span>
                                <span className={`overview-exec-muted text-right text-[11px] md:text-[12px] ${dateTextClass(item.execution_price_date)}`}>
                                  {formatShortDate(item.execution_price_date)}
                                </span>
                                <span className="overview-exec-value text-right font-mono text-[13px] md:text-[14px]">
                                  ¥{item.execution_price.toLocaleString(locale)}
                                </span>
                                <span className={`overview-exec-value text-right font-mono text-[12px] md:text-[13px] ${changeColor(item.execution_price_change, "text-slate-400")}`}>
                                  {formatAbsoluteChange(
                                    item.execution_price,
                                    null,
                                    item.execution_price_change,
                                  )}
                                </span>
                              </div>
                            )}
                            {isLoadingThis ? (
                              <div className={`overview-regional-row ${OVERVIEW_ROW_GRID} border-b border-slate-200 px-3.5 py-2 text-[12px] md:text-[13px] overview-exec-muted last:border-b-0`}>
                                <span>{text.loading}</span>
                                <span />
                                <span />
                              </div>
                            ) : !regional ? (
                              <div className={`overview-regional-row ${OVERVIEW_ROW_GRID} border-b border-slate-200 px-3.5 py-2 text-[12px] md:text-[13px] overview-exec-muted last:border-b-0`}>
                                <span className="truncate">{text.latestPrice}</span>
                                <span className={`overview-exec-muted text-right text-[11px] md:text-[12px] ${dateTextClass(item.date)}`}>
                                  {formatShortDate(item.date)}
                                </span>
                                <span className="overview-exec-value text-right font-mono text-[13px] md:text-[14px]">
                                  ¥{item.price.toLocaleString(locale)}
                                </span>
                                <span
                                  className={`overview-exec-value text-right font-mono text-[12px] md:text-[13px] ${getPriceChangeTextClass(item.change_pct ?? null)}`}
                                >
                                  {formatAbsoluteChange(item.price, item.change_pct)}
                                </span>
                              </div>
                            ) : (
                              regional.regions
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
                                      className={`overview-regional-row ${OVERVIEW_ROW_GRID} border-b border-slate-200 px-3.5 py-1.5 text-[12px] md:text-[13px] last:border-b-0 ${r.is_internal ? "overview-regional-internal" : ""}`}
                                    >
                                      <span
                                        className={`${r.is_internal ? "font-semibold text-blue-700" : "overview-exec-muted"} truncate`}
                                      >
                                        {language === "en"
                                          ? (REGIONAL_LABEL_EN[r.price_type] ??
                                            r.label)
                                          : (REGIONAL_LABEL_ZH[r.price_type] ??
                                            REGIONAL_LABEL_ZH[String(r.label || "").trim().toLowerCase()] ??
                                            r.label)}
                                        {r.is_internal ? " 🏭" : ""}
                                      </span>
                                      <span
                                        className={`overview-exec-muted text-right text-[11px] md:text-[12px] ${dateTextClass(r.date)}`}
                                      >
                                        {formatShortDate(r.date)}
                                      </span>
                                      <span className="overview-exec-value text-right font-mono text-[13px] md:text-[14px]">
                                        ¥{r.price.toLocaleString(locale)}
                                      </span>
                                      <span
                                        className={`overview-exec-value text-right font-mono text-[12px] md:text-[13px] ${isStale ? "overview-exec-muted" : changeColor(changeSignal(r.price, r.change_pct), "text-slate-400")}`}
                                      >
                                        {formatAbsoluteChange(
                                          r.price,
                                          r.change_pct,
                                        )}
                                      </span>
                                    </div>
                                  );
                                })
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </article>
              ))}
            </div>
          </section>

          <section className="mb-5">
            <h3 className="mb-3 flex items-center gap-2 text-[12px] md:text-[13px] font-semibold uppercase tracking-[0.08em] text-slate-500">
              <span className="block h-3 w-[3px] rounded bg-blue-600" />
              {text.futuresWeeklyGapTitle}
            </h3>

            <div className="grid grid-cols-1 gap-2.5 lg:grid-cols-2 xl:grid-cols-3">
              {groupedRows.map((group) => (
                <article
                  key={`${group.key}-movement`}
                  className="overflow-hidden rounded-xl border border-slate-200 bg-white"
                >
                  <div
                    className={`${GROUP_VISUALS[group.key].futuresHeader} flex items-center justify-between px-3.5 py-2 text-white`}
                  >
                    <h4 className="text-[12px] md:text-[13px] font-semibold">
                      {groupLabel(group.key)}
                    </h4>
                    <span className="rounded-full bg-white/20 px-2 py-0.5 text-[10px] md:text-[11px]">
                      {group.rows.length} {text.items}
                    </span>
                  </div>

                  <div>
                    {group.rows.map((item) => {
                      const forecastBase = weeklyForecastBaseByProduct[item.id];
                      const hasForecast =
                        typeof forecastBase === "number" &&
                        Number.isFinite(forecastBase);
                      const snapshotGap = resolveGap(
                        item.price,
                        item.weekly_change_pct,
                        item.weekly_gap_value,
                      );
                      const nextPrice = hasForecast
                        ? forecastBase
                        : snapshotGap != null && Number.isFinite(snapshotGap)
                          ? item.price + snapshotGap
                          : null;
                      const forecastGap = hasForecast
                        ? Math.round((nextPrice - item.price) * 100) / 100
                        : snapshotGap;
                      return (
                        <article
                          key={`${item.id}-move`}
                          className="border-b border-slate-200 px-3.5 py-2.5 last:border-b-0"
                        >
                          <div className="grid grid-cols-[minmax(0,1fr)_minmax(110px,210px)_72px] items-center gap-2">
                            <div className="min-w-0">
                              <h5
                                className="truncate text-[13px] md:text-[14px] font-semibold text-slate-700"
                                title={displayName(item.id, item.name)}
                              >
                                {displayName(item.id, item.name)}
                              </h5>
                              <p className="mt-1 text-[11px] md:text-[12px] text-slate-400">
                                {text.forecast7dLabel}
                              </p>
                            </div>
                            <div className="text-left md:text-center text-[12px] md:text-[13px] text-slate-500">
                              {text.forecastLabel}:{" "}
                              <span className="font-semibold text-slate-600">
                                {formatPriceValue(nextPrice)}
                              </span>
                            </div>
                            <div
                              className={`text-right font-mono text-[13px] md:text-[14px] font-semibold ${changeColor(forecastGap, "text-slate-500")}`}
                            >
                              {formatAbsoluteChange(
                                item.price,
                                null,
                                forecastGap,
                              )}
                            </div>
                          </div>
                        </article>
                      );
                    })}
                  </div>
                </article>
              ))}
            </div>
          </section>

          <section>
            <div className="mb-2 flex items-center justify-between gap-2">
              <h3 className="flex items-center gap-2 text-[11px] md:text-[12px] font-semibold uppercase tracking-[0.08em] text-slate-500">
                <span className="block h-3 w-[3px] rounded bg-blue-600" />
                {text.industryIntelligenceTitle}
              </h3>
              <p className="text-[10px] md:text-[11px] text-slate-400">
                {language === "en" ? "Updated" : "更新"}:{" "}
                {formatNewsTimestamp(newsUpdatedAt, language, locale)}
                {" · "}
                {news.length} {text.items}
              </p>
            </div>

            {newsLoading ? (
              <div className="rounded-xl border border-slate-200 bg-white p-3 text-[14px] md:text-[15px] text-slate-500">
                {text.loading}
              </div>
            ) : newsError ? (
              <div className="rounded-xl border border-slate-200 bg-white p-3 text-[14px] md:text-[15px] text-rose-600">
                {newsError}
              </div>
            ) : news.length === 0 ? (
              <div className="rounded-xl border border-slate-200 bg-white p-3 text-[14px] md:text-[15px] text-slate-500">
                {text.noNews}
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-2.5 lg:grid-cols-3">
                {newsColumns.map((column, idx) => (
                  <article
                    key={`news-col-${idx}`}
                    className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-[0_1px_3px_rgba(15,23,42,0.06)]"
                  >
                    <div
                      className={`${idx === 0 ? "bg-gradient-to-r from-blue-800 to-blue-600" : idx === 1 ? "bg-gradient-to-r from-violet-800 to-purple-600" : "bg-gradient-to-r from-emerald-800 to-emerald-600"} flex items-center justify-between px-3.5 py-2.5 text-[12px] md:text-[13px] font-semibold uppercase tracking-[0.06em] text-white`}
                    >
                      <span>
                        {idx === 0
                          ? text.feedstockAndCrude
                          : idx === 1
                            ? text.midstreamProducts
                            : text.downstreamAndMarket}
                      </span>
                      <span className="rounded-full bg-white/20 px-2 py-0.5 text-[10px] md:text-[11px]">
                        {column.length}
                      </span>
                    </div>

                    <div>
                      {column.map((item) => (
                        <article
                          key={item.event_id}
                          className="border-b border-slate-200 px-3.5 py-3.5 last:border-b-0 hover:bg-slate-50/70 transition-colors"
                        >
                          <h4 className="line-clamp-2 text-[14px] md:text-[15px] font-semibold leading-snug text-slate-800">
                            {item.headline}
                          </h4>
                          {(() => {
                            const impact = weeklyImpactSignal(item);
                            const explanation = buildDirectNewsExplanation(
                              item,
                              language,
                            );
                            return (
                              <>
                                <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[11px] md:text-[12px]">
                                  <span
                                    className={`rounded border px-1.5 py-0.5 font-semibold ${weeklyImpactTone(impact.direction)}`}
                                  >
                                    {weeklyDirectionLabel(
                                      impact.direction,
                                      language,
                                    )}
                                  </span>
                                  <span className="text-blue-700">
                                    {item.source_label || text.source}
                                  </span>
                                  <span className="text-slate-400">
                                    {formatNewsTimestamp(
                                      item.detected_at,
                                      language,
                                      locale,
                                    )}
                                  </span>
                                </div>
                                <p className="mt-2 rounded-md border border-slate-200 bg-slate-50 px-2.5 py-2 text-[12px] md:text-[13px] leading-relaxed text-slate-700">
                                  {explanation}
                                </p>
                              </>
                            );
                          })()}
                          {item.summary ? (
                            <p className="mt-1.5 line-clamp-2 text-[12px] md:text-[13px] leading-relaxed text-slate-500">
                              {item.summary}
                            </p>
                          ) : null}
                          {item.source_url ? (
                            <div className="mt-2">
                              <a
                                href={item.source_url}
                                target="_blank"
                                rel="noreferrer noopener"
                                className="inline-flex items-center rounded border border-blue-200 bg-blue-50 px-2 py-0.5 text-[11px] md:text-[12px] font-medium text-blue-700 hover:bg-blue-100"
                              >
                                {text.openLink}
                              </a>
                            </div>
                          ) : null}
                        </article>
                      ))}
                    </div>
                  </article>
                ))}
              </div>
            )}
          </section>

          <div className="mt-4 border-t border-slate-200 pt-2.5 text-[11px] md:text-[12px] text-slate-400">
            {text.sourcesNote}
          </div>
        </main>
      </div>
    </div>
  );
};
