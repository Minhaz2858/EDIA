import {
    AlertTriangle,
    BarChart3,
    FileText,
    ShieldAlert,
    Sparkles,
    TrendingUp,
    type LucideIcon,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { useLanguage } from "../lib/language";
import {
    api,
    type AlertItem,
    type AuthUser,
    type RecommendationCardPayload,
    type RecommendationCardTag,
} from "../services/api";

interface EmptyProps {
  onSelectSuggestion?: (prompt: string) => void;
  currentUser?: AuthUser | null;
  alerts?: AlertItem[];
}

type RecommendationRole =
  | "executive"
  | "sales"
  | "risk"
  | "procurement"
  | "finance"
  | "operations"
  | "general";

interface RecommendationCard {
  id: string;
  icon: LucideIcon;
  badge: string;
  prompt: string;
  color: string;
  reason: string;
  score: number;
  tags: RecommendationCardTag[];
}

interface LibraryCard {
  id: string;
  icon: LucideIcon;
  badge: Record<"zh" | "en", string>;
  prompt: Record<"zh" | "en", string>;
  reason: Record<"zh" | "en", string>;
  color: string;
  roles: RecommendationRole[];
  priority: number;
}

const WEEK_MS = 7 * 24 * 60 * 60 * 1000;
const USAGE_STORAGE_KEY = "edia.empty.recommendation-usage.v1";

const iconMap: Record<string, LucideIcon> = {
  sparkles: Sparkles,
  alert: AlertTriangle,
  alerttriangle: AlertTriangle,
  risk: ShieldAlert,
  shieldalert: ShieldAlert,
  trendingup: TrendingUp,
  trend: TrendingUp,
  report: FileText,
  filetext: FileText,
  market: BarChart3,
  barchart3: BarChart3,
};

const promptLibrary: LibraryCard[] = [
  {
    id: "exec-weekly-report",
    icon: FileText,
    badge: { zh: "周报洞察", en: "Weekly Report" },
    prompt: {
      zh: "请基于本周中国市场数据库，生成一份包含价格变化、供需信号和下周建议动作的高层周报。",
      en: "Generate this week's China market executive report with pricing shifts, supply-demand signals, and recommended actions for next week.",
    },
    reason: {
      zh: "适合管理层快速获取本周重点变化。",
      en: "Useful for weekly leadership reviews and fast decision-making.",
    },
    color: "from-emerald-500 to-teal-500",
    roles: ["executive", "general"],
    priority: 210,
  },
  {
    id: "market-direction",
    icon: TrendingUp,
    badge: { zh: "市场感知", en: "Market Sensing" },
    prompt: {
      zh: "请基于中国市场数据库，说明本周裂解C5和异戊二烯的主流价格变化，以及周环比方向。",
      en: "Based on the China market database, what are the main price moves for Cracked C5 and isoprene this week, and what changed week over week?",
    },
    reason: {
      zh: "快速识别市场方向和价格动量。",
      en: "Quickly establishes market direction and price momentum.",
    },
    color: "from-sky-500 to-blue-500",
    roles: ["risk", "executive", "sales", "general"],
    priority: 205,
  },
  {
    id: "pricing-advice-pipeline",
    icon: BarChart3,
    badge: { zh: "定价建议", en: "Pricing Advice" },
    prompt: {
      zh: "请基于中国区域数据库，给出本周和下周聚合级管线业务的推荐报价区间，并解释与中石化上海挂牌价的价差。",
      en: "Based on the China regional database, give the recommended quote range for Piperlyene this week and next week, and explain the spread versus SINOPEC Shanghai listed price.",
    },
    reason: {
      zh: "适合销售和管理层决定报价窗口。",
      en: "Best for sales and leaders deciding this week's quote window.",
    },
    color: "from-fuchsia-500 to-violet-500",
    roles: ["sales", "executive", "finance", "general"],
    priority: 200,
  },
  {
    id: "risk-operations-shutdown",
    icon: ShieldAlert,
    badge: { zh: "风险预警", en: "Risk Alerts" },
    prompt: {
      zh: "请分析中国乙烯/C5装置停开车变化对异戊二烯和双环戊二烯库存与价格的影响，并给出运营建议。",
      en: "How do China ethylene/C5 plant shutdown and restart changes affect isoprene and DCPD inventory and pricing? Please give operational recommendations.",
    },
    reason: {
      zh: "适合风控和供应链冲击分析。",
      en: "Designed for risk control and supply-shock analysis.",
    },
    color: "from-orange-500 to-amber-500",
    roles: ["risk", "operations", "executive", "general"],
    priority: 198,
  },
  {
    id: "supply-demand-driver",
    icon: AlertTriangle,
    badge: { zh: "市场异动", en: "Market Alert" },
    prompt: {
      zh: "如果日本石脑油7日上涨接近两位数，请解释背后的供需驱动、传导风险，以及我们本周应采取的动作。",
      en: "If Japanese Naphtha jumps sharply over 7 days, explain the supply-demand driver, the transmission risk, and what action we should take this week.",
    },
    reason: {
      zh: "适合快速应对上游原料异动。",
      en: "Helps react quickly to upstream feedstock shocks.",
    },
    color: "from-amber-500 to-orange-500",
    roles: ["risk", "procurement", "executive", "general"],
    priority: 192,
  },
  {
    id: "procurement-window",
    icon: Sparkles,
    badge: { zh: "采购窗口", en: "Procurement Window" },
    prompt: {
      zh: "请结合本周价格、库存和装置动态，判断未来两周C5原料采购窗口，并给出买入节奏建议。",
      en: "Using this week's prices, inventories, and plant dynamics, identify the procurement window for C5 feedstock over the next two weeks and recommend a buying cadence.",
    },
    reason: {
      zh: "适合采购判断节奏与库存安全边界。",
      en: "Useful for procurement timing and inventory coverage decisions.",
    },
    color: "from-cyan-500 to-blue-500",
    roles: ["procurement", "operations", "finance", "general"],
    priority: 188,
  },
];

function normalizeIconKey(iconKey: string): string {
  return iconKey.toLowerCase().replace(/[^a-z0-9]/g, "");
}

function resolveRecommendationRole(
  currentUser?: AuthUser | null,
): RecommendationRole {
  if (!currentUser) return "general";
  if (currentUser.is_admin) return "executive";

  const haystack = [
    currentUser.role_name,
    currentUser.department_name,
    currentUser.username,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();

  if (/sales|account|commercial|bizdev/.test(haystack)) return "sales";
  if (/risk|market|analyst|intelligence/.test(haystack)) return "risk";
  if (/procure|sourcing|buyer|purchase/.test(haystack)) return "procurement";
  if (/finance|cfo|accounting/.test(haystack)) return "finance";
  if (/operation|ops|production|plant/.test(haystack)) return "operations";
  return "general";
}

function getRoleLabel(role: RecommendationRole, language: "zh" | "en"): string {
  const labels = {
    zh: {
      executive: "管理层",
      sales: "销售",
      risk: "风控/市场",
      procurement: "采购",
      finance: "财务",
      operations: "运营/生产",
      general: "通用",
    },
    en: {
      executive: "Executive",
      sales: "Sales",
      risk: "Risk/Market",
      procurement: "Procurement",
      finance: "Finance",
      operations: "Operations",
      general: "General",
    },
  } as const;

  return labels[language][role];
}

function readUsageStore(): Record<string, Record<string, number[]>> {
  try {
    const raw = window.localStorage.getItem(USAGE_STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return typeof parsed === "object" && parsed ? parsed : {};
  } catch {
    return {};
  }
}

function recordPromptUsage(userKey: string, promptId: string): void {
  const now = Date.now();
  const store = readUsageStore();
  const userStats = store[userKey] || {};
  const recent = (userStats[promptId] || []).filter(
    (timestamp) => now - timestamp < WEEK_MS,
  );
  userStats[promptId] = [...recent, now];
  store[userKey] = userStats;
  window.localStorage.setItem(USAGE_STORAGE_KEY, JSON.stringify(store));
}

function buildAlertPrompt(
  alert: AlertItem,
  language: "zh" | "en",
): RecommendationCard {
  const percent = `${alert.change_7d_pct >= 0 ? "+" : ""}${alert.change_7d_pct.toFixed(1)}%`;
  const isSpike = alert.type === "price_spike";

  return {
    id: `alert-${alert.id}`,
    icon: AlertTriangle,
    badge: language === "en" ? "Market Alert" : "市场异动",
    prompt:
      language === "en"
        ? `${alert.product_name} moved ${percent} in 7 days. Explain the supply-demand driver, the pricing risk, and what action we should take this week.`
        : `${alert.product_name} 7日变动 ${percent}。请解释背后的供需驱动、价格风险，以及我们本周应该采取的动作。`,
    color: isSpike
      ? "from-amber-500 to-orange-500"
      : "from-emerald-500 to-teal-500",
    reason:
      language === "en"
        ? `Triggered by today's market movement in ${alert.product_name}.`
        : `根据今日 ${alert.product_name} 市场异动自动推荐。`,
    score:
      240 +
      (alert.severity === "high" ? 40 : alert.severity === "medium" ? 20 : 10),
    tags: [
      {
        label: language === "en" ? "Live market signal" : "实时市场信号",
        tone: "alert",
      },
    ],
  };
}

function dedupeCards(cards: RecommendationCard[]): RecommendationCard[] {
  const seen = new Set<string>();
  return cards.filter((card) => {
    const key = `${card.id}:${card.prompt}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function Empty({
  onSelectSuggestion,
  currentUser,
  alerts = [],
}: EmptyProps) {
  const { language } = useLanguage();
  const role = resolveRecommendationRole(currentUser);
  const roleLabel = getRoleLabel(role, language);
  const userKey = `${currentUser?.username || "guest"}:${language}`;
  const [remoteCards, setRemoteCards] = useState<RecommendationCardPayload[]>(
    [],
  );

  const text =
    language === "en"
      ? {
          subtitle: "AI-powered decision intelligence platform",
          recommendedFor: "Recommended for",
          hot: "Hot this week",
          loadError:
            "Live recommendations are unavailable, so local recommendation cards are shown instead.",
        }
      : {
          subtitle: "AI-powered decision intelligence platform",
          recommendedFor: "适合",
          hot: "本周热点",
          loadError: "实时推荐暂不可用，当前显示本地推荐卡片。",
        };

  useEffect(() => {
    let alive = true;

    api
      .getRecommendationCards(language)
      .then((response) => {
        if (!alive) return;
        setRemoteCards(response.cards || []);
      })
      .catch(() => {
        if (!alive) return;
        setRemoteCards([]);
      });

    return () => {
      alive = false;
    };
  }, [language, currentUser?.id]);

  const prompts = useMemo(() => {
    const usageStore = readUsageStore();
    const usageByPrompt = usageStore[userKey] || {};
    const weekCounts = Object.fromEntries(
      Object.entries(usageByPrompt).map(([id, timestamps]) => [
        id,
        timestamps.filter((timestamp) => Date.now() - timestamp < WEEK_MS)
          .length,
      ]),
    ) as Record<string, number>;

    const localBaseCards: RecommendationCard[] = promptLibrary.map((item) => {
      const roleMatch = item.roles.includes(role)
        ? 100
        : item.roles.includes("general")
          ? 40
          : 0;

      return {
        id: item.id,
        icon: item.icon,
        badge: item.badge[language],
        prompt: item.prompt[language],
        color: item.color,
        reason: item.reason[language],
        score: item.priority + roleMatch + (weekCounts[item.id] || 0) * 14,
        tags:
          roleMatch >= 100
            ? [
                {
                  label: `${text.recommendedFor} ${roleLabel}`,
                  tone: "role",
                },
              ]
            : [],
      };
    });

    const localAlertCards = alerts
      .slice(0, 2)
      .map((alert) => buildAlertPrompt(alert, language));

    const backendCards: RecommendationCard[] = remoteCards.map((card) => ({
      id: card.id,
      icon: iconMap[normalizeIconKey(card.icon_key)] || Sparkles,
      badge: card.badge,
      prompt: card.prompt,
      color: card.color,
      reason: card.reason,
      score: card.score + (weekCounts[card.id] || 0) * 14,
      tags: (card.tags || []).map((tag) => ({
        label: tag.label,
        tone:
          tag.tone === "role" || tag.tone === "alert" || tag.tone === "hot"
            ? tag.tone
            : "role",
      })),
    }));

    const combined = dedupeCards([
      ...localAlertCards,
      ...backendCards,
      ...localBaseCards,
    ])
      .sort((left, right) => right.score - left.score)
      .slice(0, 6);

    const hotCards = [...combined]
      .filter(
        (card) =>
          !card.id.startsWith("alert-") && (weekCounts[card.id] || 0) > 0,
      )
      .sort(
        (left, right) =>
          (weekCounts[right.id] || 0) - (weekCounts[left.id] || 0),
      )
      .slice(0, 2)
      .map((card) => card.id);

    return combined.map((card) => ({
      ...card,
      tags: [
        ...card.tags,
        ...(hotCards.includes(card.id)
          ? [{ label: text.hot, tone: "hot" }]
          : []),
      ],
    }));
  }, [
    alerts,
    language,
    remoteCards,
    role,
    roleLabel,
    text.hot,
    text.recommendedFor,
    userKey,
  ]);

  const handleSelect = (card: RecommendationCard) => {
    recordPromptUsage(userKey, card.id);
    onSelectSuggestion?.(card.prompt);
  };

  return (
    <div className="relative flex flex-col items-center justify-center space-y-8 px-4 py-6 text-center animate-in fade-in duration-700">
      <div className="relative z-10 max-w-3xl space-y-3">
        <p className="text-3xl font-medium tracking-tight text-slate-700 md:text-4xl">
          {text.subtitle}
        </p>
        {remoteCards.length === 0 && (
          <p className="text-xs text-amber-600">{text.loadError}</p>
        )}
      </div>

      <div className="grid w-full max-w-5xl grid-cols-1 gap-4 text-left md:grid-cols-2 xl:grid-cols-3">
        {prompts.map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => handleSelect(item)}
            className="group relative overflow-hidden rounded-2xl border border-stone-200 bg-white p-4 shadow-sm transition-colors duration-200 hover:border-stone-300 hover:bg-stone-50"
          >
            <div className="relative z-10 flex items-start gap-3">
              <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-stone-200 bg-stone-100 text-slate-700">
                <item.icon className="h-4 w-4" />
              </div>

              <div>
                <p className="mb-2 inline-flex items-center rounded-full border border-stone-200 bg-stone-50 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-700">
                  {item.badge}
                </p>

                {item.tags.length > 0 && (
                  <div className="mb-2 flex flex-wrap gap-1.5">
                    {item.tags.map((tag) => (
                      <span
                        key={`${item.id}-${tag.label}`}
                        className="inline-flex items-center rounded-full border border-stone-200 bg-white px-2 py-1 text-[10px] font-semibold text-slate-600"
                      >
                        {tag.label}
                      </span>
                    ))}
                  </div>
                )}

                <p className="text-sm leading-6 text-slate-700">
                  {item.prompt}
                </p>
                <p className="mt-3 text-xs leading-5 text-slate-500">
                  {item.reason}
                </p>
              </div>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
