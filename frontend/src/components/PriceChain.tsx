import React, { useEffect, useState, useRef } from "react";
import { useLanguage } from "../lib/language";
import { formatSignedPriceChange, getPriceChangeBadgeClass, getPriceChangeDirection, getPriceChangeLineColor, getPriceChangeTextClass } from "../lib/priceChange";
import { API_BASE_URL, fetchWithRetry } from "../services/api";

interface ChainNode {
  id: string;
  name_cn: string;
  name_en: string;
  price: number;
  change_pct: number;
  change_latest_date?: string | null;
  change_target_date?: string | null;
  unit: string;
  sparkline_series?: number[];
  remark_zh?: string;
  remark_en?: string;
  execution_price?: number | null;
  execution_price_date?: string | null;
  execution_price_change?: number | null;
  execution_price_change_pct?: number | null;
  execution_price_prev_date?: string | null;
}

interface ChainData {
  nodes: ChainNode[];
  edges: { source: string; target: string }[];
}

type CachedPayload<T> = {
  value: T;
  cachedAt: number;
};

const PRICE_CHAIN_CACHE_KEY = "edia.dashboard.price-chain.v2-date-based-change";
const PRICE_CHAIN_CACHE_TTL_MS = 10 * 60 * 1000;

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

// Layout tiers matching the industrial chain
const TIER_CONFIG = {
  zh: [
    { tier: 0, label: "原油", ids: ["crude_oil"], color: "#1e293b" },
    { tier: 1, label: "石脑油", ids: ["naphtha"], color: "#6366f1" },
    {
      tier: 2,
      label: "裂解产物",
      ids: ["cracked_c5", "cracked_c9"],
      color: "#0ea5e9",
    },
    {
      tier: 3,
      label: "C5 下游产品",
      ids: ["isoprene", "piperylene", "dcpd"],
      color: "#10b981",
    },
  ],
  en: [
    { tier: 0, label: "Crude Oil", ids: ["crude_oil"], color: "#1e293b" },
    { tier: 1, label: "Naphtha", ids: ["naphtha"], color: "#6366f1" },
    {
      tier: 2,
      label: "Cracking Products",
      ids: ["cracked_c5", "cracked_c9"],
      color: "#0ea5e9",
    },
    {
      tier: 3,
      label: "C5 Downstream",
      ids: ["isoprene", "piperylene", "dcpd"],
      color: "#10b981",
    },
  ],
} as const;

interface NodeCardProps {
  node: ChainNode;
  accentColor: string;
  language: "zh" | "en";
}

const NodeCard: React.FC<NodeCardProps> = ({ node, accentColor, language }) => {
  const direction = getPriceChangeDirection(node.change_pct);
  const arrow = direction === "increase" ? "▲" : direction === "decrease" ? "▼" : "";
  const arrowColor = getPriceChangeLineColor(node.change_pct);
  const primaryName = language === "en" ? node.name_en : node.name_cn;
  const secondaryName = language === "en" ? node.name_cn : node.name_en;
  const dateWindowLabel =
    node.change_latest_date && node.change_target_date
      ? language === "en"
        ? `30-day Trend: ${node.change_latest_date} - ${node.change_target_date}`
        : `30天趋势：${node.change_latest_date} - ${node.change_target_date}`
      : language === "en"
        ? "30-day Trend"
        : "30天趋势";
  const remark = language === "en" ? node.remark_en : node.remark_zh;

  // Sparkline for cracked_c5 and cracked_c9
  const showSparkline = node.sparkline_series && node.sparkline_series.length >= 2;
  const sparklineData = node.sparkline_series || [];

  return (
    <div
      className="rounded-lg bg-white shadow-md hover:shadow-lg transition-shadow border border-slate-200 overflow-hidden flex"
      style={{ minWidth: 148 }}
    >
      {/* Colored left accent bar */}
      <div
        className="w-1.5 shrink-0"
        style={{ backgroundColor: accentColor }}
      />
      <div className="flex-1 px-4 py-3">
        <div className="text-base font-bold text-slate-800">{primaryName}</div>
        <div className="text-xs text-slate-400 mt-0.5">{secondaryName}</div>
        <div className="text-2xl font-black text-slate-900 mt-2 font-mono tracking-tight">
          {node.price.toLocaleString(undefined, {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
          })}
        </div>
        <div className="flex items-center gap-2 mt-1.5">
          <span
            className={`inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-sm font-semibold ${getPriceChangeBadgeClass(node.change_pct)}`}
          >
            <span style={{ color: arrowColor }}>{arrow}</span>{" "}
            {formatSignedPriceChange(node.change_pct, {}, undefined, "%")}
          </span>
          <span className="text-xs text-slate-400">{node.unit}</span>
        </div>
        <div className="mt-1 text-[10px] text-slate-400">{dateWindowLabel}</div>

        {/* Sparkline chart */}
        {showSparkline && (
          <div className="mt-2">
            <Sparkline data={sparklineData} color={arrowColor} />
          </div>
        )}

        {/* Historical percentile remark */}
        {remark && (
          <div className="mt-1 text-[10px] text-slate-500 italic leading-tight">
            {remark}
          </div>
        )}

        {/* Company execution price */}
        {node.execution_price != null && (
          <div className="mt-1.5 text-[11px] text-slate-500 leading-tight font-mono">
            <span className="font-semibold text-amber-600">
              {language === "en" ? "Execution" : "执行"}
            </span>
            {" | "}
            <span className="text-slate-400">
              {node.execution_price_date
                ? node.execution_price_date.slice(5)
                : "--"}
            </span>
            {" | ¥"}
            <span className="font-semibold text-slate-600">
              {node.execution_price.toLocaleString(undefined, {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2,
              })}
            </span>
            {node.execution_price_change != null && (
              <>
                {" | "}
                <span className={getPriceChangeTextClass(node.execution_price_change)}>
                  {node.execution_price_change > 0 ? "+" : ""}
                  {node.execution_price_change.toLocaleString(undefined, {
                    minimumFractionDigits: 2,
                    maximumFractionDigits: 2,
                  })}
                </span>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

// Simple SVG sparkline component
const Sparkline: React.FC<{ data: number[]; color: string }> = ({ data, color }) => {
  if (!data || data.length < 2) return null;
  const width = 120;
  const height = 32;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const stepX = width / (data.length - 1);
  const points = data.map((v, i) => {
    const x = i * stepX;
    const y = height - ((v - min) / range) * (height - 4) - 2;
    return `${x},${y}`;
  }).join(" ");
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className="block">
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth="1.5"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
};

const FlowArrow: React.FC<{ vertical?: boolean }> = ({ vertical = false }) => (
  <div
    className={`flex items-center justify-center ${vertical ? "py-1" : "px-2"}`}
  >
    <svg
      width="40"
      height="20"
      viewBox="0 0 40 20"
      className={`text-slate-300 ${vertical ? "rotate-90" : ""}`}
    >
      <defs>
        <marker
          id="arrowhead"
          markerWidth="8"
          markerHeight="6"
          refX="8"
          refY="3"
          orient="auto"
        >
          <polygon points="0 0, 8 3, 0 6" fill="currentColor" />
        </marker>
      </defs>
      <line
        x1="0"
        y1="10"
        x2="32"
        y2="10"
        stroke="currentColor"
        strokeWidth="2"
        markerEnd="url(#arrowhead)"
      />
    </svg>
  </div>
);

export const PriceChain: React.FC = () => {
  const { language } = useLanguage();
  const text =
    language === "en"
      ? {
        loading: "Loading industrial chain data...",
        empty: "No industrial chain data available.",
      }
      : {
        loading: "加载产业链数据...",
        empty: "暂无产业链数据",
      };
  const [data, setData] = useState<ChainData | null>(() =>
    readCache<ChainData>(PRICE_CHAIN_CACHE_KEY, PRICE_CHAIN_CACHE_TTL_MS),
  );
  const [loading, setLoading] = useState(
    () =>
      readCache<ChainData>(PRICE_CHAIN_CACHE_KEY, PRICE_CHAIN_CACHE_TTL_MS) ==
      null,
  );
  const [edgePaths, setEdgePaths] = useState<Array<{ path: string; idx: number }>>([]);

  // Refs for edge-based arrow positioning
  const nodeRefs = useRef<Map<string, HTMLDivElement | null>>(new Map());

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      try {
        const res = await fetchWithRetry(
          `${API_BASE_URL}/api/v2/market/price-chain`,
        );
        const json = await res.json();
        if (mounted) {
          setData(json);
          writeCache(PRICE_CHAIN_CACHE_KEY, json);
          setLoading(false);
        }
      } catch {
        if (mounted) setLoading(false);
      }
    };
    load();
    const interval = setInterval(load, 60000);
    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, []);

  // Compute edge paths after data loads and DOM is painted
  useEffect(() => {
    if (!data || data.nodes.length === 0) return;
    // Wait for next paint so refs are populated
    requestAnimationFrame(() => {
      const container = document.querySelector(".price-chain-grid");
      if (!container) return;
      const containerRect = container.getBoundingClientRect();
      const paths: Array<{ path: string; idx: number }> = [];
      data.edges.forEach((edge, idx) => {
        const sourceEl = nodeRefs.current.get(edge.source);
        const targetEl = nodeRefs.current.get(edge.target);
        if (!sourceEl || !targetEl) return;
        const sourceRect = sourceEl.getBoundingClientRect();
        const targetRect = targetEl.getBoundingClientRect();
        const sourceX = sourceRect.right - containerRect.left;
        const sourceY = (sourceRect.top + sourceRect.bottom) / 2 - containerRect.top;
        const targetX = targetRect.left - containerRect.left;
        const targetY = (targetRect.top + targetRect.bottom) / 2 - containerRect.top;
        if (targetX <= sourceX) return;
        const midX = (sourceX + targetX) / 2;
        const path = `M ${sourceX} ${sourceY} C ${midX} ${sourceY}, ${midX} ${targetY}, ${targetX - 8} ${targetY}`;
        paths.push({ path, idx });
      });
      setEdgePaths(paths);
    });
  }, [data]);

  if (loading) {
    return (
      <div className="flex min-h-[380px] items-center justify-center p-4">
        <div className="animate-pulse text-sm text-slate-400">
          {text.loading}
        </div>
      </div>
    );
  }

  if (!data || data.nodes.length === 0) {
    return (
      <div className="flex min-h-[380px] items-center justify-center p-4">
        <div className="text-sm text-slate-400">{text.empty}</div>
      </div>
    );
  }

  const nodeMap = new Map(data.nodes.map((n) => [n.id, n]));

  // Build tiered layout
  const tiers = TIER_CONFIG[language]
    .map((tc) => ({
      ...tc,
      nodes: tc.ids.map((id) => nodeMap.get(id)).filter(Boolean) as ChainNode[],
    }))
    .filter((t) => t.nodes.length > 0);

  return (
    <div className="flex min-h-[380px] items-center justify-center p-4 overflow-hidden">
      <div
        className="relative price-chain-grid flex w-full flex-col items-center justify-center gap-0 xl:flex-row"
        style={{ minHeight: 380 }}
      >
        {tiers.map((tier, tierIdx) => (
          <React.Fragment key={tier.tier}>
            {tierIdx > 0 && (
              <>
                <div className="hidden xl:block">
                  <FlowArrow />
                </div>
                <div className="xl:hidden">
                  <FlowArrow vertical />
                </div>
              </>
            )}
            <div className="flex flex-col items-center gap-3">
              {/* Tier label badge */}
              <div
                className="tier-label px-3 py-1.5 rounded-full text-sm font-bold text-white shadow-sm"
                style={{ backgroundColor: tier.color }}
              >
                {tier.label}
              </div>
              {/* Node cards */}
              <div className="flex flex-col gap-3">
                {tier.nodes.map((node) => (
                  <div
                    key={node.id}
                    ref={(el) => { nodeRefs.current.set(node.id, el); }}
                  >
                    <NodeCard
                      node={node}
                      accentColor={tier.color}
                      language={language}
                    />
                  </div>
                ))}
              </div>
            </div>
          </React.Fragment>
        ))}
        {/* SVG edge connectors rendered after cards are laid out */}
        {edgePaths.length > 0 && (
          <svg
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              width: "100%",
              height: "100%",
              pointerEvents: "none",
              overflow: "visible",
              zIndex: 10,
            }}
          >
            <defs>
              {edgePaths.map(({ idx }) => (
                <marker
                  key={`marker-${idx}`}
                  id={`arrowhead-${idx}`}
                  markerWidth="8"
                  markerHeight="6"
                  refX="7"
                  refY="3"
                  orient="auto"
                >
                  <polygon points="0 0, 8 3, 0 6" fill="#94a3b8" />
                </marker>
              ))}
            </defs>
            {edgePaths.map(({ path, idx }) => (
              <path
                key={`edge-${idx}`}
                d={path}
                fill="none"
                stroke="#94a3b8"
                strokeWidth="1.5"
                markerEnd={`url(#arrowhead-${idx})`}
                strokeDasharray="4 2"
              />
            ))}
          </svg>
        )}
      </div>
    </div>
  );
};
