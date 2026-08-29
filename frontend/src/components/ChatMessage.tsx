import {
  BookmarkPlus,
  BookOpen,
  Bot,
  Building2,
  Circle,
  Check,
  CheckCircle2,
  X as CloseX,
  Copy,
  Database,
  Download,
  ExternalLink,
  FileText,
  Globe,
  Loader2,
  Sparkles,
  User,
  XCircle,
  Zap,
  BarChart3,
  TrendingUp,
  Package,
  Newspaper,
  Lightbulb,
} from "lucide-react";
import React, { useCallback, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  Brush,
  CartesianGrid,
  Cell,
  ComposedChart,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  Tooltip as RechartsTooltip,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  XAxis,
  YAxis,
} from "recharts";
import remarkGfm from "remark-gfm";
import { useLanguage } from "../lib/language";
import { useTheme } from "../lib/theme";
import { DynamicComparisonChart } from "./DynamicComparisonChart";
import { ChatVisualization, ReportExportFormat } from "../services/api";

export interface PreviewData {
  markdown: string;
  grounding_source?: string;
  products?: string[];
  intents?: string[];
  needs_forecast?: boolean;
}

export interface Message {
  role: "user" | "assistant";
  content: string;
  llmRoute?: string;
  context?: {
    visualization?: ChatVisualization;
    visualizations?: ChatVisualization[];
    chart_attachments?: Array<{
      title?: string;
      url?: string;
      base64?: string;
      mime_type?: string;
    }>;
    previewData?: PreviewData;
    [key: string]: any;
  };
  status?: "pending" | "success" | "partial" | "error";
  qualityFlags?: Array<{
    code: string;
    severity: string;
    message: string;
    source: string;
  }>;
  alertsPrepended?: string[];
  enhancementApplied?: Record<string, string>;
}

type PlannerSourceEntry = {
  product?: string;
  intent?: string;
  table?: string;
  row_count?: number;
  latest_date?: string;
  has_data?: boolean;
  metrics?: Record<string, number>;
};

type PlannerSourcesContext = {
  grounding_source?: string;
  generated_at?: string;
  products?: string[];
  intents?: string[];
  time_range?: { start?: string; end?: string };
  data_sources?: string[];
  sources?: PlannerSourceEntry[];
  errors?: Array<Record<string, unknown>>;
};

function isVisualization(value: unknown): value is ChatVisualization {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<ChatVisualization>;
  return (
    (candidate.kind === "line" ||
      candidate.kind === "bar" ||
      candidate.kind === "multi_line" ||
      candidate.kind === "forecast_range" ||
      candidate.kind === "waterfall" ||
      candidate.kind === "pie" ||
      candidate.kind === "scatter" ||
      candidate.kind === "composed" ||
      candidate.kind === "multi_axis_composed" ||
      candidate.kind === "table") &&
    typeof candidate.title === "string" &&
    typeof candidate.x_key === "string" &&
    Array.isArray(candidate.series) &&
    Array.isArray(candidate.data)
  );
}

const PIE_COLORS = [
  "#2563eb",
  "#16a34a",
  "#dc2626",
  "#f59e0b",
  "#7c3aed",
  "#0891b2",
  "#e11d48",
];

interface ChatVisualizationCardProps {
  visualization: ChatVisualization;
  theme?: "dark" | "light";
}

/** Shared dark-theme tooltip formatter */
const DarkTooltipFormatter = (value: number, name: string) => {
  const formatted = typeof value === "number"
    ? value.toLocaleString(undefined, { maximumFractionDigits: 2 })
    : String(value);
  return [formatted, name];
};

const ChatVisualizationCard: React.FC<ChatVisualizationCardProps> = ({
  visualization,
  theme = "light",
}) => {
  const { language } = useLanguage();
  const isDark = theme === "dark";
  const latest =
    typeof visualization.summary?.latest === "number"
      ? visualization.summary.latest
      : null;
  const unit = visualization.unit ? ` ${visualization.unit}` : "";

  const gridColor = isDark ? "#1F2D3D" : "#e2e8f0";
  const textColor = isDark ? "#9AA5B4" : "#64748b";
  const bgColor = isDark ? "bg-[#0B1929]" : "bg-slate-50";
  const borderColor = isDark ? "border-slate-700" : "border-slate-200";
  const titleColor = isDark ? "text-slate-100" : "text-slate-900";
  const subtitleColor = isDark ? "text-slate-400" : "text-slate-500";
  const cardClass = isDark
    ? "rounded-xl border border-slate-700 bg-[#0B1929] p-4"
    : "rounded-xl border border-slate-200 bg-slate-50 p-4";

  // ── Multi-axis composed chart — dedicated dark-themed component ─────────────
  if (visualization.kind === "multi_axis_composed") {
    return <DynamicComparisonChart visualization={visualization} theme={theme} />;
  }

  // ── Table visualization (rendered as HTML, not Recharts) ──────────────────
  if (visualization.kind === "table") {
    return (
      <div className={`mt-3 w-full ${cardClass}`}>
        <div className="mb-3">
          <div className={`text-sm font-semibold ${titleColor}`}>
            {visualization.title}
          </div>
          {visualization.subtitle && (
            <div className={`text-xs ${subtitleColor}`}>
              {visualization.subtitle}
            </div>
          )}
        </div>
        <div className={`overflow-x-auto rounded-lg border ${borderColor}`}>
          <table className="min-w-full text-sm">
            <thead>
              <tr className={isDark ? "bg-slate-800" : "bg-slate-100"}>
                {visualization.series.map((s) => (
                  <th
                    key={s.key}
                    className={`py-2 px-3 text-left text-xs font-semibold uppercase tracking-wide whitespace-nowrap ${subtitleColor}`}
                  >
                    {s.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {visualization.data.map((row, i) => (
                <tr
                  key={i}
                  className={i % 2 === 0 ? (isDark ? "bg-slate-900" : "bg-white") : (isDark ? "bg-slate-800/50" : "bg-slate-50")}
                >
                  {visualization.series.map((s) => (
                    <td
                      key={s.key}
                      className={`py-2 px-3 text-xs ${isDark ? "text-slate-300" : "text-slate-700"}`}
                    >
                      {String(row[s.key] ?? "")}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  const renderChart = () => {
    // ── Pie chart ─────────────────────────────────────────────────────────────
    if (visualization.kind === "pie") {
      return (
        <PieChart>
          <Pie
            data={visualization.data as any[]}
            dataKey="value"
            nameKey="name"
            cx="50%"
            cy="50%"
            outerRadius={90}
            label={({ name, percent }: any) =>
              `${name} ${(percent * 100).toFixed(0)}%`
            }
            labelLine={false}
          >
            {visualization.data.map((_entry, index) => (
              <Cell
                key={`cell-${index}`}
                fill={PIE_COLORS[index % PIE_COLORS.length]}
              />
            ))}
          </Pie>
          <RechartsTooltip
            contentStyle={{
              backgroundColor: isDark ? "#0F1B2D" : "#fff",
              border: `1px solid ${isDark ? "#1F2D3D" : "#e2e8f0"}`,
              color: isDark ? "#9AA5B4" : "#64748b",
              borderRadius: "8px",
            }}
          />
          <Legend
            wrapperStyle={{ fontSize: 11, color: textColor }}
          />
        </PieChart>
      );
    }

    // ── Scatter chart ─────────────────────────────────────────────────────────
    if (visualization.kind === "scatter") {
      const xSeries = visualization.series[0];
      return (
        <ScatterChart>
          <CartesianGrid strokeDasharray="3 3" stroke={gridColor} />
          <XAxis
            dataKey="x"
            name={visualization.x_key}
            tick={{ fontSize: 11, fill: textColor }}
          />
          <YAxis
            dataKey="y"
            name={xSeries?.label ?? "y"}
            tick={{ fontSize: 11, fill: textColor }}
          />
          <RechartsTooltip
            cursor={{ strokeDasharray: "3 3" }}
            contentStyle={{
              backgroundColor: isDark ? "#0F1B2D" : "#fff",
              border: `1px solid ${isDark ? "#1F2D3D" : "#e2e8f0"}`,
              color: isDark ? "#9AA5B4" : "#64748b",
              borderRadius: "8px",
            }}
          />
          <Scatter
            name={xSeries?.label ?? ""}
            data={visualization.data as any[]}
            fill={xSeries?.color ?? "#7c3aed"}
          />
        </ScatterChart>
      );
    }

    // ── Composed chart (bar + line mix) ───────────────────────────────────────
    if (visualization.kind === "composed") {
      return (
        <ComposedChart data={visualization.data}>
          <CartesianGrid strokeDasharray="3 3" stroke={gridColor} />
          <XAxis
            dataKey={visualization.x_key}
            tick={{ fontSize: 11, fill: textColor }}
          />
          <YAxis tick={{ fontSize: 11, fill: textColor }} />
          <RechartsTooltip
            contentStyle={{
              backgroundColor: isDark ? "#0F1B2D" : "#fff",
              border: `1px solid ${isDark ? "#1F2D3D" : "#e2e8f0"}`,
              color: isDark ? "#9AA5B4" : "#64748b",
              borderRadius: "8px",
            }}
          />
          <Legend
            wrapperStyle={{ fontSize: 11, color: textColor }}
          />
          {visualization.series.map((s) =>
            s.chart_type === "line" ? (
              <Line
                key={s.key}
                type="monotone"
                dataKey={s.key}
                name={s.label}
                stroke={s.color}
                strokeWidth={2.5}
                dot={{ r: 3 }}
              />
            ) : (
              <Bar
                key={s.key}
                dataKey={s.key}
                name={s.label}
                fill={s.color}
                radius={[4, 4, 0, 0]}
                opacity={0.85}
              />
            ),
          )}
        </ComposedChart>
      );
    }

    if (visualization.kind === "forecast_range") {
      return (
        <AreaChart data={visualization.data}>
          <defs>
            {visualization.series.map((s) => (
              <linearGradient
                key={s.key}
                id={`grad-${s.key}`}
                x1="0"
                y1="0"
                x2="0"
                y2="1"
              >
                <stop offset="5%" stopColor={s.color} stopOpacity={0.25} />
                <stop offset="95%" stopColor={s.color} stopOpacity={0.02} />
              </linearGradient>
            ))}
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke={gridColor} />
          <XAxis
            dataKey={visualization.x_key}
            tick={{ fontSize: 12, fill: textColor }}
          />
          <YAxis tick={{ fontSize: 12, fill: textColor }} />
          <RechartsTooltip
            contentStyle={{
              backgroundColor: isDark ? "#0F1B2D" : "#fff",
              border: `1px solid ${isDark ? "#1F2D3D" : "#e2e8f0"}`,
              color: isDark ? "#9AA5B4" : "#64748b",
              borderRadius: "8px",
            }}
          />
          <Legend
            wrapperStyle={{ fontSize: 11, color: textColor }}
          />
          {visualization.series.map((s) => (
            <Area
              key={s.key}
              type="monotone"
              dataKey={s.key}
              name={s.label}
              stroke={s.color}
              fill={`url(#grad-${s.key})`}
              strokeWidth={2}
              dot={false}
            />
          ))}
        </AreaChart>
      );
    }

    if (visualization.kind === "line" || visualization.kind === "multi_line") {
      return (
        <LineChart data={visualization.data}>
          <CartesianGrid strokeDasharray="3 3" stroke={gridColor} />
          <XAxis
            dataKey={visualization.x_key}
            tick={{ fontSize: 12, fill: textColor }}
          />
          <YAxis tick={{ fontSize: 12, fill: textColor }} />
          <RechartsTooltip
            contentStyle={{
              backgroundColor: isDark ? "#0F1B2D" : "#fff",
              border: `1px solid ${isDark ? "#1F2D3D" : "#e2e8f0"}`,
              color: isDark ? "#9AA5B4" : "#64748b",
              borderRadius: "8px",
            }}
          />
          <Legend
            wrapperStyle={{ fontSize: 11, color: textColor }}
            iconType="line"
            iconSize={10}
          />
          {visualization.series.map((series) => (
            <Line
              key={series.key}
              type="monotone"
              dataKey={series.key}
              name={series.label}
              stroke={series.color}
              strokeWidth={2.5}
              dot={{ r: 3 }}
              activeDot={{ r: 5 }}
            />
          ))}
          {/* Brush for zoom/pan — only show when data is dense enough */}
          {visualization.data.length > 10 && (
            <Brush
              dataKey={visualization.x_key}
              height={28}
              stroke={isDark ? "#1F2D3D" : "#e2e8f0"}
              fill={isDark ? "#0F1B2D" : "#f8fafc"}
            />
          )}
        </LineChart>
      );
    }

    // bar / waterfall
    return (
      <BarChart data={visualization.data}>
        <CartesianGrid strokeDasharray="3 3" stroke={gridColor} />
        <XAxis
          dataKey={visualization.x_key}
          tick={{ fontSize: 12, fill: textColor }}
        />
        <YAxis tick={{ fontSize: 12, fill: textColor }} />
        <RechartsTooltip
          contentStyle={{
            backgroundColor: isDark ? "#0F1B2D" : "#fff",
            border: `1px solid ${isDark ? "#1F2D3D" : "#e2e8f0"}`,
            color: isDark ? "#9AA5B4" : "#64748b",
            borderRadius: "8px",
          }}
        />
        <Legend
          wrapperStyle={{ fontSize: 11, color: textColor }}
        />
        {visualization.series.map((series) => (
          <Bar
            key={series.key}
            dataKey={series.key}
            name={series.label}
            fill={series.color}
            radius={[6, 6, 0, 0]}
          />
        ))}
      </BarChart>
    );
  };

  return (
    <div className={`mt-3 w-full ${cardClass}`}>
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
        {latest !== null && (
          <div className={`rounded-lg ${isDark ? "bg-[#0F1B2D]" : "bg-white"} px-3 py-2 text-right shadow-sm border ${isDark ? "border-slate-700" : "border-slate-200"}`}>
            <div className={`text-[11px] uppercase tracking-wide ${isDark ? "text-slate-400" : "text-slate-400"}`}>
              {language === "en" ? "Latest" : "最新值"}
            </div>
            <div className={`text-sm font-semibold ${titleColor}`}>
              {latest}
              {unit}
            </div>
          </div>
        )}
      </div>
      <div className="h-64 w-full">
        <ResponsiveContainer width="100%" height="100%">
          {renderChart()}
        </ResponsiveContainer>
      </div>
    </div>
  );
};

const STAGE_LABELS_ZH: Record<string, string> = {
  thinking: "思考中",
  analyzing: "分析中",
  preparing: "准备中",
  parse_intent: "正在理解你的问题...",
  extract_context: "正在识别产品与上下文...",
  validate_context: "正在校验上下文...",
  parallel_fetch: "正在并行获取市场数据...",
  perception: "正在加载市场快照...",
  intelligence_check: "正在检查情报预警...",
  web_search: "正在检索近期资讯...",
  research_enrichment: "正在评估证据质量...",
  resolve_product: "正在解析产品对象...",
  gather_context: "正在从知识库收集上下文...",
  diagnosis: "正在执行市场诊断...",
  forecast: "正在生成价格预测...",
  pricing: "正在计算定价决策...",
  evidence_compile: "正在汇总证据包...",
  decision_critic: "正在进行决策质量校验...",
  tool_execution: "正在执行分析工具...",
  erp_data_retrieval: "正在查询内部经营数据...",
  assemble_prompt: "正在组装最终分析...",
  short_circuit_check: "正在准备直接回复...",
  generate_report: "正在生成报告...",
  clarify_intent: "正在准备澄清问题...",
  thin_evidence: "正在检查数据可用性...",
  conversational_direct: "正在组织回复...",
  final_synthesis: "最终综合生成中",
  dual_model_synthesis: "双模型综合中",
};

function normalizeStageLabel(stage?: string, label?: string, language: string = "en"): string {
  const rawLabel = safeString(label).trim();
  if (rawLabel) {
    const cleaned = rawLabel.replace(/^\/+/, "").trim();
    if (!cleaned) {
      return rawLabel;
    }
    if (language === "en") {
      return cleaned;
    }
    const key = cleaned.toLowerCase().replace(/\s+/g, "_");
    return STAGE_LABELS_ZH[key] || cleaned;
  }

  const rawStage = safeString(stage).trim();
  if (!rawStage) return language === "en" ? "Thinking" : "思考中";
  const normalizedStage = rawStage.replace(/^\/+/, "").trim().toLowerCase();
  if (language !== "en") {
    return STAGE_LABELS_ZH[normalizedStage] || "处理中";
  }
  return rawStage
    .replace(/^\/+/, "")
    .split("_")
    .filter(Boolean)
    .map((token) => token.charAt(0).toUpperCase() + token.slice(1))
    .join(" ");
}

const BackendOperationTracker: React.FC<{
  streamMeta: {
    status?: string;
    currentStage?: string;
    currentStageLabel?: string;
    totalMs?: number;
    lastStageElapsedMs?: number;
    stages?: Array<{
      stage?: string;
      label?: string;
      elapsedMs?: number;
      phase?: string;
    }>;
  };
  isPending?: boolean;
}> = ({ streamMeta, isPending = false }) => {
  const { language } = useLanguage();
  const isCompleted = !isPending || String(streamMeta.status || "") === "completed";

  const currentLabel = normalizeStageLabel(
    String(streamMeta.currentStage || ""),
    String(streamMeta.currentStageLabel || ""),
    language,
  );

  const summaryLabel = isCompleted
    ? language === "en"
      ? "Completed"
      : "已完成"
    : language === "en"
      ? "Thinking..."
      : "思考中...";
  const hiddenThinkingLabel = language === "en" ? "Thinking" : "思考中";
  const summaryTime = Number.isFinite(Number(streamMeta.totalMs))
    ? Math.round(Number(streamMeta.totalMs))
    : Number.isFinite(Number(streamMeta.lastStageElapsedMs))
      ? Math.round(Number(streamMeta.lastStageElapsedMs))
      : undefined;

  const pipelineStages = Array.isArray(streamMeta.stages) ? streamMeta.stages : [];
  const visibleStages = pipelineStages
    .filter((item) => {
      const label = normalizeStageLabel(
        String(item?.stage || ""),
        String(item?.label || ""),
        language,
      );
      return label && label !== hiddenThinkingLabel;
    })
    .slice(-6);

  return (
    <div className="mb-2 w-full rounded-xl border border-slate-200 bg-white px-3 py-3 shadow-sm">
      <div className="flex items-center gap-2 text-xs text-slate-600">
        {isCompleted ? (
          <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />
        ) : (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-sky-600" />
        )}
        <span className="font-medium">{summaryLabel}</span>
        {!isCompleted && currentLabel && currentLabel !== hiddenThinkingLabel && (
          <span className="text-[11px] text-slate-500">{currentLabel}</span>
        )}
        {summaryTime !== undefined && (
          <span className="text-[11px] text-slate-500">{summaryTime} ms</span>
        )}
      </div>
      {!isCompleted && visibleStages.length > 0 && (
        <ul className="mt-2 space-y-1 border-t border-slate-100 pt-2">
          {visibleStages.map((item, index) => {
            const label = normalizeStageLabel(
              String(item?.stage || ""),
              String(item?.label || ""),
              language,
            );
            const phase = String(item?.phase || "completed");
            const isActive = index === visibleStages.length - 1 && phase === "started";
            return (
              <li
                key={`${item?.stage || "stage"}-${index}`}
                className="flex items-center gap-2 text-[11px] text-slate-500"
              >
                {isActive ? (
                  <Loader2 className="h-3 w-3 animate-spin text-sky-500" />
                ) : (
                  <CheckCircle2 className="h-3 w-3 text-emerald-500" />
                )}
                <span>{label}</span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
};

function safeString(value: unknown): string {
  if (typeof value === "string") return value;
  return "";
}

function extractSectionSnippet(
  source: string,
  anchors: string[],
  maxChars = 900,
): string {
  const hay = source;
  const lower = hay.toLowerCase();
  const idx = anchors
    .map((a) => ({ a, i: lower.indexOf(a.toLowerCase()) }))
    .filter((x) => x.i >= 0)
    .sort((x, y) => x.i - y.i)[0]?.i;

  if (idx === undefined) return "";
  const slice = hay.slice(idx, idx + maxChars).trim();
  return slice;
}

function extractFollowUpSuggestions(markdown: string): string[] {
  try {
    const text = safeString(markdown);
    if (!text) return [];

    const lines = text.split(/\r?\n/);
    let inSection = false;
    const out: string[] = [];

    for (const raw of lines) {
      const line = raw.trim();
      if (!line) continue;

      if (
        /^\*\*?(您可能还想问|You may also ask)/i.test(line) ||
        /^#{1,6}\s*(您可能还想问|You may also ask)/i.test(line)
      ) {
        inSection = true;
        continue;
      }

      if (!inSection) continue;

      if (/^#{1,6}\s+/.test(line) || /^\*\*[^*]+\*\*$/.test(line)) {
        break;
      }

      if (/^[-*]\s+/.test(line)) {
        const item = line
          .replace(/^[-*]\s+/, "")
          .replace(/^"|"$/g, "")
          .trim();
        if (item) out.push(item);
        continue;
      }

      // Stop section if non-list content starts after collecting suggestions.
      if (out.length > 0) break;
    }

    const uniq: string[] = [];
    const seen = new Set<string>();
    for (const s of out) {
      if (!seen.has(s)) {
        seen.add(s);
        uniq.push(s);
      }
    }
    return uniq.slice(0, 5);
  } catch {
    return [];
  }
}

function stripFollowUpSection(markdown: string): string {
  try {
    const text = safeString(markdown);
    if (!text) return text;

    const lines = text.split(/\r?\n/);
    let start = -1;
    let end = -1;

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i].trim();
      if (
        /^\*\*?(您可能还想问|You may also ask)/i.test(line) ||
        /^#{1,6}\s*(您可能还想问|You may also ask)/i.test(line)
      ) {
        start = i;
        end = i + 1;

        for (let j = i + 1; j < lines.length; j++) {
          const next = lines[j].trim();
          if (!next) {
            end = j + 1;
            continue;
          }
          if (/^[-*]\s+/.test(next)) {
            end = j + 1;
            continue;
          }
          break;
        }
        break;
      }
    }

    if (start < 0 || end < 0) return text;

    // Remove a preceding horizontal rule for cleaner markdown after section removal.
    let removeFrom = start;
    if (removeFrom > 0) {
      const prev = lines[removeFrom - 1].trim();
      if (prev === "---") {
        removeFrom -= 1;
      }
    }

    const kept = [...lines.slice(0, removeFrom), ...lines.slice(end)];
    return kept
      .join("\n")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  } catch {
    return safeString(markdown);
  }
}

// ---------------------------------------------------------------------------
// Reference source parsing
// ---------------------------------------------------------------------------

export interface ParsedRef {
  key: string;
  type:
  | "db"
  | "weekly"
  | "web"
  | "erp"
  | "intel"
  | "other"
  | "appendix"
  | "delta";
  label: string;
  detail: string;
  urls: string[];
  markdown?: string;
}

function parseRefLine(line: string, index: number): ParsedRef {
  const urls = Array.from(line.matchAll(/https?:\/\/[^\s)]+/g), (match) =>
    String(match[0] || "").replace(/[.,;]+$/, ""),
  );
  const detail = line.replace(/https?:\/\/[^\s)]+/g, "").trim() || line.trim();
  const normalized = detail.toLowerCase();

  let type: ParsedRef["type"] = "other";
  if (/erp|inventory|sales|contract|production|internal/.test(normalized) || /库存|销售|合同|生产|内部/.test(detail)) {
    type = "erp";
  } else if (/weekly|report|longzhong|weekly_report/.test(normalized) || /周报|隆众/.test(detail)) {
    type = "weekly";
  } else if (/db|database|table|live data|market_prices|v_actual_price|forecast_price/.test(normalized) || /数据库|实时数据|数据表/.test(detail)) {
    type = "db";
  } else if (/intel|event|alert|news/.test(normalized) || /情报|事件|预警|新闻/.test(detail)) {
    type = "intel";
  } else if (urls.length > 0) {
    type = "web";
  }

  return {
    key: `ref-${index}`,
    type,
    label: detail || urls[0] || `Reference ${index + 1}`,
    detail: detail || urls[0] || `Reference ${index + 1}`,
    urls,
  };
}

function domainLabel(url: string): { name: string; isPdf: boolean } {
  try {
    const normalized = String(url || "").trim();
    const host = new URL(normalized).hostname.replace(/^www\./, "");
    const h = host.toLowerCase();
    const isP = /\.pdf(?:$|[?#])/i.test(normalized);

    if (h.includes("cninfo")) return { name: "巨潮资讯", isPdf: true };
    if (h.includes("sohu")) return { name: "搜狐", isPdf: false };
    if (h.includes("sina") || h.includes("finance.sina")) {
      return { name: "新浪财经", isPdf: false };
    }
    if (h.includes("hzeyun")) return { name: "化工眼", isPdf: isP };
    if (h.includes("szse.cn")) return { name: "深交所", isPdf: true };
    if (h.includes("163.com") || h.includes("netease")) {
      return { name: "网易", isPdf: false };
    }
    if (h.includes("jiuyuangang") || h.includes("steelnews")) {
      return { name: "我的钢网", isPdf: false };
    }
    if (h.includes("sinopec") || h.includes("petrochina")) {
      return { name: "中国石油化工网", isPdf: false };
    }
    if (h.includes("dfcfw") || h.includes("eastmoney")) {
      return { name: "东方财富", isPdf: false };
    }
    return { name: host, isPdf: isP };
  } catch {
    return { name: url.slice(0, 30), isPdf: false };
  }
}

// Appendix blocks generated deterministically after the LLM section
const APPENDIX_BLOCK_DEFS: Array<{
  pattern: RegExp;
  label: string;
  icon: string;
}> = [
    {
      pattern: /^\*\*(组合覆盖清单|Portfolio Coverage)/,
      label: "组合覆盖清单",
      icon: "📋",
    },
    {
      pattern: /^\*\*(定价推导过程)/,
      label: "定价推导过程",
      icon: "🔢",
    },
    {
      pattern: /^\*\*(数据新鲜度|Data Freshness)/,
      label: "数据新鲜度",
      icon: "🕐",
    },
    {
      pattern: /^\*\*(一致性校验|Consistency Check)/,
      label: "一致性校验",
      icon: "✅",
    },
    {
      pattern: /^\*\*(异常波动提示|Volatility Alert)/,
      label: "异常波动提示",
      icon: "⚡",
    },
  ];

function extractAndStripRefsSections(markdown: string): {
  content: string;
  refs: ParsedRef[];
} {
  const text = safeString(markdown);
  const lines = text.split("\n");
  const refs: ParsedRef[] = [];
  const keep: string[] = [];

  const isRefsHeader = (s: string) => /^\*\*(参考来源|References)\*\*$/.test(s);
  const isProvHeader = (s: string) => /^\*\*(数据来源|Provenance)/.test(s);
  const getAppendixDef = (s: string) =>
    APPENDIX_BLOCK_DEFS.find((d) => d.pattern.test(s));
  const isDeltaHeader = (s: string) =>
    /与上次查询/.test(s) && /相比的变化/.test(s);
  const isDeltaEndMarker = (s: string) => /以下为完整更新分析/.test(s);
  // A line belongs to the delta comparison block if it is blank OR looks like a price-change row.
  // Any other line (section headers, narrative, tables, etc.) signals delta is over.
  const isDeltaContent = (s: string) => {
    if (!s) return true; // blank lines are acceptable inside a delta block
    return /[→↑↓]|[+\-]\d[^a-zA-Z]*%|触发条件|推荐价/.test(s);
  };

  // mode: 'main' | 'refs' | 'skip' | 'appendix' | 'delta'
  let mode: "main" | "refs" | "skip" | "appendix" | "delta" = "main";
  let appendixBuf: string[] = [];
  let appendixLabel = "";
  let appendixIcon = "";

  // Delta state
  let hasDelta = false;
  let deltaBuf: string[] = [];

  const flushAppendix = () => {
    const content = appendixBuf.join("\n").trim();
    if (appendixLabel && content) {
      refs.push({
        key: `appendix-${refs.length}`,
        type: "appendix",
        label: appendixLabel,
        detail: appendixIcon,
        urls: [],
        markdown: content,
      });
    }
    appendixBuf = [];
    appendixLabel = "";
    appendixIcon = "";
  };

  const flushDelta = () => {
    const content = deltaBuf
      .join("\n")
      .replace(/以下为完整更新分析[.…。→\s]*/g, "")
      .trim();
    if (content && !hasDelta) {
      refs.push({
        key: "delta-0",
        type: "delta",
        label: "本次 vs 上次查询",
        detail: "📊",
        urls: [],
        markdown: content,
      });
      hasDelta = true;
    }
    deltaBuf = [];
  };

  for (let i = 0; i < lines.length; i++) {
    const trimmed = lines[i].trim();

    if (trimmed === "---") {
      // Look ahead to find next non-empty line
      let j = i + 1;
      while (j < lines.length && !lines[j].trim()) j++;
      const ahead = (lines[j] || "").trim();

      if (isRefsHeader(ahead)) {
        flushAppendix();
        flushDelta();
        mode = "refs";
        i = j;
        continue;
      }
      if (isProvHeader(ahead)) {
        flushAppendix();
        flushDelta();
        mode = "skip";
        i = j;
        continue;
      }
      const appDef = getAppendixDef(ahead);
      if (appDef) {
        flushAppendix();
        flushDelta();
        appendixLabel =
          ahead
            .replace(/^\*\*/, "")
            .replace(/\*\*$/, "")
            .replace(/\s*\([^)]*\)$/, "")
            .trim() || appDef.label;
        appendixIcon = appDef.icon;
        mode = "appendix";
        i = j;
        continue;
      }
      // Separator between known non-main mode → return to main
      if (mode !== "main") {
        if (mode === "delta") flushDelta();
        else flushAppendix();
        mode = "main";
        keep.push(lines[i]);
        continue;
      }
    }

    if (mode === "main") {
      if (isDeltaHeader(trimmed)) {
        // Enter delta mode — don't keep this line in the chat body
        flushDelta();
        deltaBuf.push(lines[i]);
        mode = "delta";
      } else {
        keep.push(lines[i]);
      }
    } else if (mode === "delta") {
      if (isDeltaHeader(trimmed)) {
        // Duplicate delta header — flush current block, start collecting the next one
        flushDelta();
        deltaBuf.push(lines[i]);
      } else if (isDeltaEndMarker(trimmed)) {
        // "以下为完整更新分析" — flush and return to main; second block will re-enter delta mode
        flushDelta();
        mode = "main";
      } else if (isDeltaContent(trimmed)) {
        deltaBuf.push(lines[i]);
      } else {
        // Any line that isn't delta content (section headers, narrative, tables…)
        // → flush the buffer and keep this line in the main body
        flushDelta();
        mode = "main";
        keep.push(lines[i]);
      }
    } else if (mode === "refs") {
      if (trimmed.startsWith("- ")) {
        refs.push(parseRefLine(trimmed.slice(2).trim(), refs.length));
      } else if (trimmed === "---") {
        mode = "main";
        keep.push(lines[i]);
      }
    } else if (mode === "skip") {
      if (trimmed === "---") {
        mode = "main";
        keep.push(lines[i]);
      }
    } else if (mode === "appendix") {
      appendixBuf.push(lines[i]);
    }
  }

  flushAppendix();
  flushDelta();

  return {
    content: keep
      .join("\n")
      .replace(/\n{3,}/g, "\n\n")
      .trim(),
    refs,
  };
}

// ---------------------------------------------------------------------------
// Reference Panel sub-component
// ---------------------------------------------------------------------------

const RefTypeIcon: React.FC<{ type: ParsedRef["type"] }> = ({ type }) => {
  const base =
    "w-7 h-7 rounded-full flex items-center justify-center text-white flex-shrink-0";
  if (type === "db")
    return (
      <div className={`${base} bg-blue-500`}>
        <Database className="w-3.5 h-3.5" />
      </div>
    );
  if (type === "weekly")
    return (
      <div className={`${base} bg-emerald-500`}>
        <FileText className="w-3.5 h-3.5" />
      </div>
    );
  if (type === "web")
    return (
      <div className={`${base} bg-orange-500`}>
        <Globe className="w-3.5 h-3.5" />
      </div>
    );
  if (type === "erp")
    return (
      <div className={`${base} bg-purple-500`}>
        <Building2 className="w-3.5 h-3.5" />
      </div>
    );
  if (type === "intel")
    return (
      <div className={`${base} bg-rose-500`}>
        <Zap className="w-3.5 h-3.5" />
      </div>
    );
  if (type === "appendix")
    return (
      <div className={`${base} bg-slate-600`}>
        <BookOpen className="w-3.5 h-3.5" />
      </div>
    );
  if (type === "delta")
    return (
      <div className={`${base} bg-amber-500`}>
        <Zap className="w-3.5 h-3.5" />
      </div>
    );
  return (
    <div className={`${base} bg-slate-400`}>
      <BookOpen className="w-3.5 h-3.5" />
    </div>
  );
};

const AppendixCard: React.FC<{ ref_: ParsedRef }> = ({ ref_ }) => {
  const { language } = useLanguage();
  const [open, setOpen] = useState(false);
  const contentRef = useRef<HTMLDivElement>(null);
  const markdown = (ref_.markdown || "").trim();

  const handleToggle = () => {
    const next = !open;
    setOpen(next);
    if (next) {
      setTimeout(
        () =>
          contentRef.current?.scrollIntoView({
            behavior: "smooth",
            block: "nearest",
          }),
        60,
      );
    }
  };

  return (
    <div className="rounded-lg border border-slate-200 bg-white">
      <button
        type="button"
        onClick={handleToggle}
        className="w-full flex items-center gap-2.5 p-2.5 hover:bg-slate-50 transition-colors text-left"
      >
        <span className="text-base flex-shrink-0">{ref_.detail}</span>
        <div className="flex-1 min-w-0">
          <div className="text-xs font-semibold text-slate-800">
            {ref_.label}
          </div>
        </div>
        <span className="text-slate-400 text-xs">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div ref={contentRef} className="border-t border-slate-100 px-3 py-2">
          {markdown ? (
            <div className="[&_table]:w-full [&_table]:text-[11px] [&_table]:border-collapse [&_th]:text-left [&_th]:font-semibold [&_th]:text-slate-600 [&_th]:py-1 [&_th]:px-1.5 [&_th]:border-b [&_th]:border-slate-200 [&_td]:py-1 [&_td]:px-1.5 [&_td]:text-slate-700 [&_td]:border-b [&_td]:border-slate-100 [&_ul]:pl-4 [&_ul]:text-[11px] [&_li]:text-slate-700 [&_li]:my-0.5 [&_p]:text-[11px] [&_p]:text-slate-700 overflow-x-auto">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {markdown}
              </ReactMarkdown>
            </div>
          ) : (
            <p className="text-xs text-slate-400 italic py-1">
              {language === "en" ? "No additional details available." : "暂无更多详情。"}
            </p>
          )}
        </div>
      )}
    </div>
  );
};

const ReferencePanel: React.FC<{ refs: ParsedRef[]; onClose: () => void }> = ({
  refs,
  onClose,
}) => {
  const { language } = useLanguage();
  const deltaRefs = refs.filter((r) => r.type === "delta");
  const appendixRefs = refs.filter((r) => r.type === "appendix");
  const intelRefs = refs.filter((r) => r.type === "intel");
  const webRefs = refs.filter((r) => r.urls.length > 0 && r.type !== "intel");
  const internalRefs = refs.filter(
    (r) =>
      r.urls.length === 0 &&
      r.type !== "appendix" &&
      r.type !== "delta" &&
      r.type !== "intel",
  );

  const renderWebCard = (url: string, refIdx: number, urlIdx: number) => {
    const { name, isPdf } = domainLabel(url);
    const title = name + (isPdf ? " (PDF)" : "");
    return (
      <a
        key={`${refIdx}-${urlIdx}`}
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        className="flex items-start gap-2.5 rounded-lg border border-slate-200 bg-white p-2.5 hover:bg-blue-50 hover:border-blue-200 transition-colors group"
      >
        <RefTypeIcon type="web" />
        <div className="flex-1 min-w-0">
          <div className="text-xs font-semibold text-slate-800 leading-snug line-clamp-2 group-hover:text-blue-700">
            {title}
          </div>
          <div className="text-[11px] text-slate-400 mt-0.5 truncate">
            {new URL(url).hostname.replace(/^www\./, "")}
          </div>
        </div>
        {isPdf && (
          <span className="flex-shrink-0 text-[10px] bg-red-100 text-red-600 font-bold px-1 rounded">
            PDF
          </span>
        )}
        <ExternalLink className="w-3 h-3 text-slate-300 group-hover:text-blue-400 flex-shrink-0 mt-0.5" />
      </a>
    );
  };

  const renderInternalCard = (ref: ParsedRef) => {
    const typeLabel: Record<ParsedRef["type"], string> = {
      db: "实时数据库",
      weekly: "市场周报",
      web: "网页来源",
      erp: "ERP业务库",
      intel: "情报事件",
      other: "参考来源",
      appendix: "附录",
      delta: "变化追踪",
    };
    return (
      <div
        key={ref.key}
        className="flex items-start gap-2.5 rounded-lg border border-slate-200 bg-white p-2.5"
      >
        <RefTypeIcon type={ref.type} />
        <div className="flex-1 min-w-0">
          <div className="text-xs font-semibold text-slate-800">
            {typeLabel[ref.type]}
          </div>
          <div className="text-[11px] text-slate-500 mt-0.5 line-clamp-2">
            {ref.detail || ref.label}
          </div>
        </div>
      </div>
    );
  };

  const renderIntelCard = (ref: ParsedRef) => {
    const primaryUrl = ref.urls[0];
    return (
      <div
        key={ref.key}
        className="rounded-lg border border-rose-200 bg-white p-2.5"
      >
        <div className="flex items-start gap-2.5">
          <RefTypeIcon type="intel" />
          <div className="flex-1 min-w-0">
            <div className="text-xs font-semibold text-slate-800">情报事件</div>
            <div className="text-[11px] text-slate-600 mt-0.5 leading-relaxed">
              {ref.detail || ref.label}
            </div>
            {primaryUrl && (
              <a
                href={primaryUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-1 inline-flex items-center gap-1 text-[11px] text-blue-600 hover:text-blue-700"
              >
                <ExternalLink className="w-3 h-3" />
                查看原始来源
              </a>
            )}
          </div>
        </div>
      </div>
    );
  };

  const totalCount =
    deltaRefs.length +
    internalRefs.length +
    intelRefs.length +
    webRefs.reduce((s, r) => s + r.urls.length, 0) +
    appendixRefs.length;

  return (
    <div className="flex flex-col h-full bg-white">
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-3.5 border-b border-slate-200 flex-shrink-0">
        <span className="text-sm font-bold text-slate-800">
          {language === "en" ? "Reference Sources" : "参考来源"} {totalCount}
        </span>
        <button
          type="button"
          onClick={onClose}
          className="text-slate-400 hover:text-slate-600 transition-colors p-0.5 rounded"
        >
          <CloseX className="w-4 h-4" />
        </button>
      </div>
      {/* Scrollable content */}
      <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-2.5">
        {deltaRefs.length > 0 && (
          <>
            <div className="text-[10px] font-bold text-amber-500 uppercase tracking-wide mb-0.5">
              变化追踪
            </div>
            {deltaRefs.map((ref) => (
              <AppendixCard key={ref.key} ref_={ref} />
            ))}
            {(internalRefs.length > 0 ||
              webRefs.length > 0 ||
              appendixRefs.length > 0) && (
                <div className="border-t border-slate-100 pt-1" />
              )}
          </>
        )}
        {internalRefs.map((ref) => renderInternalCard(ref))}
        {intelRefs.map((ref) => renderIntelCard(ref))}
        {webRefs.flatMap((ref, ri) =>
          ref.urls.map((url, ui) => renderWebCard(url, ri, ui)),
        )}
        {appendixRefs.length > 0 && (
          <>
            {(internalRefs.length > 0 ||
              intelRefs.length > 0 ||
              webRefs.length > 0) && (
                <div className="text-[10px] font-bold text-slate-400 uppercase tracking-wide mt-1 mb-0.5">
                  分析附录
                </div>
              )}
            {appendixRefs.map((ref) => (
              <AppendixCard key={ref.key} ref_={ref} />
            ))}
          </>
        )}
        {totalCount === 0 && (
          <div className="text-xs text-slate-400 text-center py-8">
            {language === "en"
              ? "No reference sources captured for this response."
              : "本次回答未捕获参考来源。"}
          </div>
        )}
      </div>
    </div>
  );
};

export { ReferencePanel };

function normalizeMarkdownForRender(markdown: string): string {
  const text = safeString(markdown);
  if (!text) return text;

  // Normalize common model-output variants so markdown emphasis renders reliably.
  const normalized = text
    .replace(/\uFF0A/g, "*") // full-width asterisk
    .replace(/\\\*/g, "*") // escaped asterisk
    .replace(/\*\*\s+([\d+\-.%]+)\s+\*\*/g, "**$1**");

  // Reflow malformed inline numbering (e.g. "...如下: 1. A ... 2. B ...") into separate lines.
  const reflowedNumbering = normalized
    .split(/\r?\n/)
    .map((line) => {
      const raw = line.trim();
      const hasMultiInlineNumbering =
        /(?:^|\s)\d+\.\s/.test(raw) && /\s\d+\.\s/.test(raw);
      if (!hasMultiInlineNumbering) return line;
      return line.replace(/\s(?=\d+\.\s)/g, "\n");
    })
    .join("\n");

  // Some responses emit a compressed pipe table in one line (e.g. pricing ladder),
  // or star-separated fields on a numbered line. Convert these into readable bullets.
  const lines = reflowedNumbering.split(/\r?\n/).map((line) => {
    const raw = line.trim();
    const isLadderLine =
      /(定价推导过程)/i.test(raw) && raw.includes("|");

    if (!isLadderLine) {
      const numberedWithInlineStars =
        /^\d+\.\s/.test(raw) && raw.includes(" * ");
      if (!numberedWithInlineStars) return line;

      const tokens = raw
        .split(" * ")
        .map((t) => t.trim())
        .filter((t) => t.length > 0);
      if (tokens.length <= 1) return line;

      const head = tokens[0];
      const subs = tokens
        .slice(1)
        .map((t) => `  - ${t}`)
        .join("\n");
      return `${head}\n${subs}`;
    }

    const colonIdx = raw.indexOf(":");
    const prefix = colonIdx >= 0 ? raw.slice(0, colonIdx + 1) : "定价推导过程:";
    const body = colonIdx >= 0 ? raw.slice(colonIdx + 1) : raw;

    const tokens = body
      .split("|")
      .map((t) => t.trim())
      .filter((t) => t.length > 0)
      .filter((t) => !/^:?-{2,}:?$/.test(t));

    if (tokens.length === 0) return line;

    const bullets = tokens.map((t) => `- ${t}`).join("\n");
    return `${prefix}\n${bullets}`;
  });

  return lines.join("\n");
}

function stripAssistantUiNoise(markdown: string): string {
  const text = safeString(markdown);
  if (!text) return text;

  const uiLineMatchers: RegExp[] = [
    /^EDIA Assistant$/i,
    /^Orchestrator$/i,
    /^Perception Agent$/i,
    /^RAG Research$/i,
    /^Diagnosis Agent$/i,
    /^Forecasting Agent$/i,
    /^Decision Agent$/i,
    /^Report Generation$/i,
    /^Handled by (Local|External) LLM$/i,
    /^Handled by Rule Engine/i,
    /^Branching:\s*/i,
    /^Reference$/i,
    /^复制$/,
    /^PDF$/i,
    /^PPTX$/i,
    /^Word$/i,
    /^Generate Report$/i,
    /^Add to Weekly Report$/i,
  ];

  const uiTagMatcher = /^#(行情|成本|竞品|风险|周报|深度市场研究|销售报告|市场数据)$/;
  const uiTokenLineMatcher =
    /^(PDF|PPTX|Word|Reference|复制|Generate Report|Add to Weekly Report)(\s+(PDF|PPTX|Word|Reference|复制|Generate Report|Add to Weekly Report))*$/i;

  const cleaned = text
    .split(/\r?\n/)
    .filter((line) => {
      const trimmed = line.trim();
      if (!trimmed) return true;
      if (uiTagMatcher.test(trimmed)) return false;
      if (uiTokenLineMatcher.test(trimmed)) return false;
      return !uiLineMatchers.some((rx) => rx.test(trimmed));
    })
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();

  return cleaned || text;
}

function dedupeConsecutiveFastPreviews(markdown: string): string {
  const text = safeString(markdown);
  if (!text) return text;

  const blockPattern =
    /(?:##\s*Quick Take|##\s*快速结论)\s*[\s\S]*?(?=\n(?:##\s+|###\s+)|$)/g;
  const blocks = [...text.matchAll(blockPattern)].map((match) => match[0].trim());
  if (blocks.length < 2) {
    return text;
  }

  const normalized = blocks.map((block) => block.replace(/\s+/g, " ").trim());
  const allIdentical = normalized.every((block) => block === normalized[0]);
  if (!allIdentical) {
    return text;
  }

  const first = blocks[0];
  const firstIndex = text.indexOf(first);
  if (firstIndex < 0) {
    return text;
  }
  const afterFirst = text.slice(firstIndex + first.length);
  const secondIndex = afterFirst.indexOf(first);
  if (secondIndex < 0) {
    return text;
  }
  return (text.slice(0, firstIndex + first.length) + afterFirst.slice(secondIndex + first.length)).trim();
}

function stripFastStartPreview(markdown: string): string {
  const text = dedupeConsecutiveFastPreviews(safeString(markdown)).trimStart();
  if (!text) return text;

  const previewBlock = text.match(
    /^(?:##\s*Quick Take|##\s*快速结论)\s*[\s\S]*?(?=\n(?:##\s+|###\s+|\*\*|#\s+)|$)/,
  );

  if (!previewBlock) return text;
  const preview = previewBlock[0];
  const remainder = text.slice(preview.length).trimStart();
  if (!remainder || remainder.length < preview.length * 1.5) {
    return text;
  }
  return remainder;
}

// ── Quick Take preview card — rendered while the full answer is streaming ────────
const QuickTakeCard: React.FC<{ previewData: PreviewData }> = ({ previewData }) => {
  const { language } = useLanguage();
  const hasProducts = Array.isArray(previewData.products) && previewData.products.length > 0;
  const hasIntents = Array.isArray(previewData.intents) && previewData.intents.length > 0;

  return (
    <div className="mb-3 w-full rounded-lg border-l-4 border-amber-400 bg-amber-50 px-4 py-3 shadow-sm">
      {/* Header */}
      <div className="mb-2 flex items-center gap-2">
        <Zap className="w-4 h-4 text-amber-600 shrink-0" />
        <span className="text-xs font-semibold uppercase tracking-wide text-amber-800">
          {language === "en" ? "Quick Take" : "快速结论"}
        </span>
        {previewData.grounding_source === "planner" && (
          <span className="rounded-full border border-amber-300 bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-700">
            {language === "en" ? "Planner" : "规划器"}
          </span>
        )}
        {previewData.needs_forecast && (
          <span className="rounded-full border border-blue-300 bg-blue-100 px-1.5 py-0.5 text-[10px] font-medium text-blue-700">
            {language === "en" ? "Forecast" : "含预测"}
          </span>
        )}
      </div>

      {/* Products / Intents chips */}
      {(hasProducts || hasIntents) && (
        <div className="mb-2 flex flex-wrap gap-1.5">
          {hasProducts && previewData.products!.map((p) => (
            <span
              key={p}
              className="rounded-full border border-slate-300 bg-white px-2 py-0.5 text-[11px] font-medium text-slate-700"
            >
              {p}
            </span>
          ))}
          {hasIntents && previewData.intents!.map((i) => (
            <span
              key={i}
              className="rounded-full border border-indigo-200 bg-indigo-50 px-2 py-0.5 text-[11px] font-medium text-indigo-700"
            >
              {i}
            </span>
          ))}
        </div>
      )}

      {/* Markdown preview */}
      <div className="text-sm leading-relaxed text-slate-800">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>
          {previewData.markdown}
        </ReactMarkdown>
      </div>
    </div>
  );
};

const PipelineChips: React.FC<{
  status: Message["status"];
  context?: any;
}> = ({ status, context }) => {
  const icon =
    status === "pending" ? (
      <Loader2 className="w-3.5 h-3.5 animate-spin text-slate-500" />
    ) : status === "error" ? (
      <XCircle className="w-3.5 h-3.5 text-rose-600" />
    ) : status === "partial" ? (
      <CheckCircle2 className="w-3.5 h-3.5 text-amber-600" />
    ) : (
      <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600" />
    );

  return (
    <div className="mt-2 w-full">
      <div className="flex flex-wrap gap-2">
        <div className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-medium text-slate-600">
          <span className="inline-flex items-center">{icon}</span>
          <span>Live stage trace enabled in diagnostics only</span>
        </div>
      </div>
    </div>
  );
};

type DataSourceBadge = {
  key: string;
  label: string;
  icon: React.ReactNode;
  colorClass: string;
};

const DataSourceBadges: React.FC<{
  sources: Array<{ type: string; label: string; count?: number }>;
  language: string;
}> = ({ sources, language }) => {
  if (!sources || sources.length === 0) return null;

  const typeConfig: Record<string, { icon: React.ReactNode; color: string; label: string }> = {
    erp: { icon: <Building2 className="w-3 h-3" />, color: "chat-badge chat-badge-purple", label: language === "en" ? "ERP" : "ERP内部" },
    market: { icon: <TrendingUp className="w-3 h-3" />, color: "chat-badge chat-badge-blue", label: language === "en" ? "Market" : "市场数据" },
    rag: { icon: <BookOpen className="w-3 h-3" />, color: "chat-badge chat-badge-emerald", label: language === "en" ? "Reports" : "周报报告" },
    intel: { icon: <Newspaper className="w-3 h-3" />, color: "chat-badge chat-badge-rose", label: language === "en" ? "Intel" : "外部情报" },
    forecast: { icon: <Zap className="w-3 h-3" />, color: "chat-badge chat-badge-amber", label: language === "en" ? "Forecast" : "预测模型" },
    chart: { icon: <BarChart3 className="w-3 h-3" />, color: "chat-badge chat-badge-cyan", label: language === "en" ? "Chart" : "图表" },
  };

  return (
    <div className="mb-2 flex flex-wrap items-center gap-1.5">
      <span className="text-[11px] font-medium text-slate-500 mr-1">
        {language === "en" ? "Sources:" : "数据来源:"}
      </span>
      {sources.map((s, idx) => {
        const cfg = typeConfig[s.type] || { icon: <Database className="w-3 h-3" />, color: "chat-badge chat-badge-slate", label: s.label };
        return (
          <span
            key={`${s.type}-${idx}`}
            className={`${cfg.color}`}
            title={s.label}
          >
            {cfg.icon}
            <span>{cfg.label}</span>
            {typeof s.count === "number" && s.count > 0 && (
              <span className="opacity-75">{s.count}</span>
            )}
          </span>
        );
      })}
    </div>
  );
};

const DrillDownActions: React.FC<{
  onSelectSuggestion?: (text: string) => void;
  language: string;
  content: string;
}> = ({ onSelectSuggestion, language, content }) => {
  if (!onSelectSuggestion) return null;
  const lowerContent = (content || "").toLowerCase();

  const actions: Array<{ label: string; prompt: string; icon: React.ReactNode }> = [];

  if (/price|market|trend|行情|价格|走势/.test(lowerContent)) {
    actions.push({
      label: language === "en" ? "Show raw data" : "查看原始数据",
      prompt: language === "en" ? "Show me the raw market price data behind this analysis" : "展示这份分析背后的原始市场价格数据",
      icon: <Database className="w-3 h-3" />,
    });
    actions.push({
      label: language === "en" ? "Competitor comparison" : "竞品对比",
      prompt: language === "en" ? "Compare our position with key competitors on this" : "将我们的情况与主要竞品进行对比",
      icon: <BarChart3 className="w-3 h-3" />,
    });
  }

  if (/inventory|stock|库存|存货/.test(lowerContent)) {
    actions.push({
      label: language === "en" ? "Inventory detail" : "库存明细",
      prompt: language === "en" ? "Show inventory breakdown by warehouse and product" : "按仓库和产品展示库存明细",
      icon: <Package className="w-3 h-3" />,
    });
  }

  if (/sales|revenue|销售|收入|订单/.test(lowerContent)) {
    actions.push({
      label: language === "en" ? "Sales by customer" : "客户销售",
      prompt: language === "en" ? "Break down sales by top customers this month" : "按本月头部客户拆分销售情况",
      icon: <TrendingUp className="w-3 h-3" />,
    });
  }

  if (/forecast|predict|预测|未来/.test(lowerContent)) {
    actions.push({
      label: language === "en" ? "Explain forecast" : "解释预测",
      prompt: language === "en" ? "Explain how this forecast was calculated and its confidence level" : "解释该预测的计算方法和置信度",
      icon: <Lightbulb className="w-3 h-3" />,
    });
  }

  if (/margin|profit|利润|盈利/.test(lowerContent)) {
    actions.push({
      label: language === "en" ? "Cost breakdown" : "成本拆解",
      prompt: language === "en" ? "Show the cost bridge and margin drivers" : "展示成本桥和利润驱动因素",
      icon: <BarChart3 className="w-3 h-3" />,
    });
  }

  if (actions.length === 0) {
    actions.push({
      label: language === "en" ? "Tell me more" : "详细说明",
      prompt: language === "en" ? "Explain this in more detail with specific numbers" : "用具体数字更详细地解释",
      icon: <Lightbulb className="w-3 h-3" />,
    });
    actions.push({
      label: language === "en" ? "Show data table" : "展示数据表",
      prompt: language === "en" ? "Present the key data in a table format" : "将关键数据以表格形式展示",
      icon: <Database className="w-3 h-3" />,
    });
  }

  return (
    <div className="mt-2 flex flex-wrap gap-2">
      {actions.slice(0, 4).map((action, idx) => (
        <button
          key={`drill-${idx}`}
          type="button"
          onClick={() => onSelectSuggestion(action.prompt)}
          className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 hover:border-slate-300 transition-colors"
        >
          {action.icon}
          <span>{action.label}</span>
        </button>
      ))}
    </div>
  );
};

export const ChatMessage: React.FC<{
  message: Message;
  onSelectSuggestion?: (text: string) => void;
  onOpenRefs?: (refs: ParsedRef[]) => void;
  refSourceOpen?: boolean;
  onGenerateReport?: () => void;
  onExportReport?: (format: ReportExportFormat) => void;
  onAddToWeeklyReport?: () => void;
  onSaveToDecisionLog?: (messageContent: string) => void;
  canGenerateReport?: boolean;
  resolvedMode?: string;
  onExportMessage?: (format: "pdf" | "docx") => Promise<void>;
}> = ({
  message,
  onSelectSuggestion,
  onOpenRefs,
  refSourceOpen,
  onGenerateReport,
  onExportReport,
  onAddToWeeklyReport,
  onSaveToDecisionLog,
  canGenerateReport = false,
  resolvedMode,
  onExportMessage,
}) => {
    const isUser = message.role === "user";
    const { language } = useLanguage();
    const { isDark } = useTheme();
    const chartTheme = isDark ? "dark" : "light";
    const assistantStatus = message.status || "success";
    const text =
      language === "en"
        ? {
          you: "You",
          assistant: "EDIA Assistant",
          exportThisReport: "Export this report",
          exportPdf: "Export PDF",
          exportWord: "Export Word",
          followUps: "You may also ask:",
          suggestionTitle: "Click to ask",
          copied: "Copied",
          copy: "Copy",
          generateReport: "Generate Report",
          addToWeeklyReport: "Add to Weekly Report",
          saveToLog: "Save to Decision Log",
          reference: "Reference",
          exportPdfTitle: "Export as PDF",
          exportWordTitle: "Export as Word",
          generateReportTitle: "Generate report",
          addToWeeklyReportTitle: "Add to weekly report",
          saveToLogTitle: "Save to Decision Log",
          openRefsTitle: "Open reference sources",
          qualityChecked: "Quality checked",
          qualityNeedsReview: "Quality needs review",
          qualityDetails: "Quality details",
          qualityFlags: "Quality Flags",
          qualityAlerts: "Alerts",
          skillExecution: "Skill Execution",
        }
        : {
          you: "你",
          assistant: "EDIA助手",
          exportThisReport: "导出本报告",
          exportPdf: "导出PDF",
          exportWord: "导出Word",
          followUps: "您可能还想问：",
          suggestionTitle: "点击直接提问",
          copied: "已复制",
          copy: "复制",
          generateReport: "生成报告",
          addToWeeklyReport: "加入周报",
          saveToLog: "存入决策日志",
          reference: "参考来源",
          exportPdfTitle: "导出为PDF",
          exportWordTitle: "导出为Word",
          generateReportTitle: "生成报告",
          addToWeeklyReportTitle: "加入周报",
          saveToLogTitle: "存入决策日志",
          openRefsTitle: "查看参考来源",
          qualityChecked: "质量已检查",
          qualityNeedsReview: "质量需复核",
          qualityDetails: "质量详情",
          qualityFlags: "质量标记",
          qualityAlerts: "预警",
          skillExecution: "技能执行",
        };
    const showDebugMeta = useMemo(() => {
      // Keep debug pipeline badges disabled in normal chat UX.
      return false;
    }, []);
    const [qualityPanelOpen, setQualityPanelOpen] = useState(false);
    const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);

    const isPricingRecommendation = useMemo(() => {
      if (isUser) return false;
      const content = safeString(message.content);
      return (
        /[¥￥]\s*[\d,]+\s*[–\-—~～]\s*[¥￥]?\s*[\d,]+/.test(content) ||
        /(报价区间|price range|pricing range|price target|建议报价|定价建议)/i.test(
          content,
        ) ||
        /(PRICING DECISION|FLEXIBLE PRICING|PRICE RECOMMENDATION)/i.test(content)
      );
    }, [isUser, message.content]);
    const [copied, setCopied] = useState(false);
    const [isExporting, setIsExporting] = useState<"pdf" | "docx" | null>(null);
    const visualization = useMemo(
      () =>
        isUser
          ? null
          : isVisualization(message.context?.visualization)
            ? message.context.visualization
            : null,
      [isUser, message.context],
    );

    const visualizations = useMemo(() => {
      if (isUser) return null;
      const arr = message.context?.visualizations;
      if (Array.isArray(arr) && arr.length > 0) {
        return arr.filter(isVisualization);
      }
      return null;
    }, [isUser, message.context]);

    const chartAttachments = useMemo(() => {
      if (isUser) return [];
      const raw = message.context?.chart_attachments;
      if (!Array.isArray(raw)) return [];
      return raw.filter(
        (item) =>
          item &&
          typeof item === "object" &&
          (typeof (item as { base64?: string }).base64 === "string" ||
            typeof (item as { url?: string }).url === "string"),
      ) as Array<{ title?: string; url?: string; base64?: string; mime_type?: string }>;
    }, [isUser, message.context]);

    const followUps = useMemo(
      () => (isUser ? [] : extractFollowUpSuggestions(message.content)),
      [isUser, message.content],
    );

    // Extract references and strip refs section from main content
    const { displayContent, refs } = useMemo(() => {
      try {
        if (isUser)
          return { displayContent: safeString(message.content), refs: [] };
        const cleaned = stripAssistantUiNoise(safeString(message.content));
        const { content, refs } = extractAndStripRefsSections(cleaned);
        const contentWithoutFollowups = stripFollowUpSection(content);
        const strippedPreview = stripFastStartPreview(contentWithoutFollowups);
        const normalizedPrimary = normalizeMarkdownForRender(strippedPreview);
        const normalizedFallback = normalizeMarkdownForRender(contentWithoutFollowups);
        return {
          // If the response is only a quick-preview block, do not strip it to empty.
          displayContent:
            normalizedPrimary.trim().length > 0
              ? normalizedPrimary
              : normalizedFallback,
          refs,
        };
      } catch {
        return { displayContent: safeString(message.content), refs: [] };
      }
    }, [isUser, message.content]);

    const renderedContent = displayContent;
    const qualityFlags = Array.isArray(message.qualityFlags)
      ? message.qualityFlags
      : [];
    const alertsPrepended = Array.isArray(message.alertsPrepended)
      ? message.alertsPrepended
      : [];
    const enhancementApplied = message.enhancementApplied || {};
    const enhancementRows = Object.entries(enhancementApplied);
    const executiveQualityScore = useMemo(() => {
      const raw = String(enhancementApplied.executive_answer_quality || "");
      const match = raw.match(/score:(\d+)/i);
      return match ? Number(match[1]) : null;
    }, [enhancementApplied]);
    const streamMeta = useMemo(() => {
      const raw = message.context?._stream;
      if (!raw || typeof raw !== "object") return null;
      return raw as {
        status?: string;
        currentStage?: string;
        currentStageLabel?: string;
        ttftMs?: number;
        totalMs?: number;
        lastStageElapsedMs?: number;
        stages?: Array<{
          stage?: string;
          label?: string;
          elapsedMs?: number;
        }>;
      };
    }, [message.context]);
    const perfMeta = useMemo(() => {
      const raw = message.context?._perf;
      if (!raw || typeof raw !== "object") return null;
      return raw as {
        prep_ms?: number;
        llm_ms?: number;
        total_ms?: number;
      };
    }, [message.context]);
    const timingsMeta = useMemo(() => {
      const raw = message.context?._timings;
      if (!raw || typeof raw !== "object") return null;
      return raw as Record<string, number>;
    }, [message.context]);
    const promptStatsMeta = useMemo(() => {
      const raw = message.context?._prompt_stats;
      if (!raw || typeof raw !== "object") return null;
      return raw as Record<string, any>;
    }, [message.context]);
    const plannerSources = useMemo(() => {
      const raw = message.context?.planner_sources;
      if (isUser || !raw || typeof raw !== "object") return null;
      const payload = raw as PlannerSourcesContext;
      const sources = Array.isArray(payload.sources) ? payload.sources : [];
      const dataSources = Array.isArray(payload.data_sources)
        ? payload.data_sources
        : [];
      if (sources.length === 0 && dataSources.length === 0) return null;
      return { ...payload, sources, data_sources: dataSources };
    }, [isUser, message.context]);
    const hasDiagnostics =
      !isUser &&
      assistantStatus !== "pending" &&
      (perfMeta !== null || timingsMeta !== null || promptStatsMeta !== null);
    const hasQualityMeta =
      qualityFlags.length > 0 ||
      alertsPrepended.length > 0 ||
      executiveQualityScore !== null;
    const blockingQualityFlags = qualityFlags.filter((flag) => {
      const level = String(flag.severity || "").toLowerCase();
      return level === "high" || level === "medium";
    });
    const qualityNeedsReview = blockingQualityFlags.length > 0 || alertsPrepended.length > 0;

    const skillTrace = useMemo(() => {
      const raw = message.context?.skill_trace;
      if (isUser || !raw || typeof raw !== "object") return null;
      const payload = raw as Record<string, any>;
      const selectedSkills = Array.isArray(payload.selected_skills)
        ? payload.selected_skills.map((skill) => String(skill)).filter(Boolean)
        : [];
      const executedSkills = Array.isArray(payload.executed_skills)
        ? payload.executed_skills.map((skill) => String(skill)).filter(Boolean)
        : [];
      if (selectedSkills.length === 0 && executedSkills.length === 0) return null;
      return {
        route: String(payload.route || "edia"),
        selectedSkills,
        executedSkills,
        qualityGate: String(payload.quality_gate || "passed"),
        presentationMode: payload.presentation_mode
          ? String(payload.presentation_mode)
          : "",
        chartCount: Number(payload.chart_count || 0),
      };
    }, [isUser, message.context]);

    const dataSources = useMemo(() => {
      if (isUser) return [];
      const sources: Array<{ type: string; label: string; count?: number }> = [];
      const content_lower = String(message.content || "").toLowerCase();
      const hasErp = /erp|inventory|sales|contract|production|内部|库存|销售|合同|生产/.test(content_lower);
      const hasMarket = /market|price|trend|行情|价格|走势|报价/.test(content_lower);
      const hasForecast = /forecast|predict|预测|未来|下周/.test(content_lower);
      const hasRag = /weekly|report|周报|报告|decision|决策/.test(content_lower);
      const hasIntel = /news|event|alert|competitor|intel|新闻|事件|预警|竞品/.test(content_lower);
      const hasChart = /chart|graph|visualization|图表|走势图/.test(content_lower);

      if (plannerSources && plannerSources.sources && plannerSources.sources.length > 0) {
        sources.push({ type: "erp", label: "ERP Internal", count: plannerSources.sources.length });
      } else if (hasErp) {
        sources.push({ type: "erp", label: "ERP Internal" });
      }
      if (hasMarket) {
        sources.push({ type: "market", label: "Market DB" });
      }
      if (hasForecast) {
        sources.push({ type: "forecast", label: "Forecast" });
      }
      if (hasRag) {
        sources.push({ type: "rag", label: "Weekly Reports" });
      }
      if (hasIntel) {
        sources.push({ type: "intel", label: "Intelligence" });
      }
      if (hasChart || (skillTrace && skillTrace.chartCount > 0)) {
        sources.push({ type: "chart", label: "Chart" });
      }
      return sources;
    }, [isUser, message.content, plannerSources, skillTrace]);
    const severityClass = (severity: string): string => {
      const level = String(severity || "").toLowerCase();
      if (level === "high") {
        return "bg-red-50 text-red-700 border-red-200";
      }
      if (level === "medium") {
        return "bg-amber-50 text-amber-700 border-amber-200";
      }
      return "bg-slate-50 text-slate-700 border-slate-200";
    };

    const handleCopy = useCallback(() => {
      const text = safeString(renderedContent || message.content);
      if (!text) return;
      navigator.clipboard
        .writeText(text)
        .then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 2000);
        })
        .catch(() => { });
    }, [renderedContent, message.content]);

    return (
      <div
        className={`flex w-full max-w-6xl mx-auto p-4 ${isUser ? "justify-end" : "justify-start"}`}
      >
        <div
          className={`flex gap-4 ${isUser ? "max-w-[80%] flex-row-reverse" : "w-full flex-row"
            }`}
        >
          <div
            className={`flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center ${isUser ? "bg-blue-500 text-white" : "bg-emerald-600 text-white"
              }`}
          >
            {isUser ? <User size={16} /> : <Bot size={16} />}
          </div>

          <div
            className={`flex flex-col gap-1 ${isUser ? "items-end" : "items-start w-full"
              }`}
          >
            <div className="chat-meta-label font-semibold text-sm text-gray-600 mb-1">
              {isUser ? text.you : text.assistant}
            </div>

            {!isUser && streamMeta && (
              <BackendOperationTracker
                streamMeta={streamMeta}
                isPending={assistantStatus === "pending"}
              />
            )}

            {!isUser && message.context?.previewData && (
              <QuickTakeCard previewData={message.context.previewData} />
            )}

            {!isUser && showDebugMeta && (
              <PipelineChips status={assistantStatus} context={message.context} />
            )}

            {!isUser && showDebugMeta && streamMeta && (
              <div className="mb-1 flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
                {streamMeta.currentStageLabel && (
                  <span className="inline-flex items-center rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5">
                    Stage: {streamMeta.currentStageLabel}
                  </span>
                )}
                {Number.isFinite(Number(streamMeta.ttftMs)) && (
                  <span className="inline-flex items-center rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-emerald-700">
                    TTFT {Math.round(Number(streamMeta.ttftMs))} ms
                  </span>
                )}
                {Number.isFinite(Number(streamMeta.totalMs)) && streamMeta.status !== "pending" && (
                  <span className="inline-flex items-center rounded-full border border-slate-200 bg-white px-2 py-0.5">
                    Total {Math.round(Number(streamMeta.totalMs))} ms
                  </span>
                )}
              </div>
            )}

            {!isUser && skillTrace && assistantStatus !== "pending" && (
              <div className="chat-skill-panel mb-2 w-full rounded-md px-3 py-2 text-xs">
                <div className="mb-1.5 flex flex-wrap items-center gap-2">
                  <span className="inline-flex items-center gap-1.5 font-semibold text-slate-800">
                    <Sparkles className="w-3.5 h-3.5" />
                    {text.skillExecution}
                  </span>
                  <span className="chat-skill-chip rounded px-1.5 py-0.5 text-[11px] uppercase tracking-wide">
                    {skillTrace.route}
                  </span>
                  <span className="chat-quality-pass rounded px-1.5 py-0.5 text-[11px] border">
                    {language === "en" ? "Quality gate" : "质量门"}: {skillTrace.qualityGate}
                  </span>
                  {skillTrace.presentationMode && (
                    <span className="chat-skill-chip rounded px-1.5 py-0.5 text-[11px]">
                      {skillTrace.presentationMode}
                    </span>
                  )}
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {(skillTrace.executedSkills.length > 0
                    ? skillTrace.executedSkills
                    : skillTrace.selectedSkills
                  ).slice(0, 8).map((skill) => (
                    <span
                      key={`skill-trace-${skill}`}
                      className="chat-skill-tag inline-flex max-w-full items-center rounded px-2 py-1 text-[11px] font-medium"
                    >
                      <span className="truncate">{skill}</span>
                    </span>
                  ))}
                </div>
              </div>
            )}

            {showDebugMeta && hasDiagnostics && (
              <div className="mb-1 w-full">
                <button
                  type="button"
                  onClick={() => setDiagnosticsOpen((prev) => !prev)}
                  className="inline-flex items-center gap-2 rounded-md border border-slate-200 bg-slate-50 px-2.5 py-1 text-[11px] font-medium text-slate-700 hover:bg-slate-100"
                >
                  <Database className="w-3.5 h-3.5" />
                  <span>Response Diagnostics</span>
                </button>

                {diagnosticsOpen && (
                  <div className="mt-2 rounded-md border border-slate-200 bg-white p-3 text-xs text-slate-700 space-y-3">
                    {perfMeta && (
                      <div>
                        <div className="mb-1 font-semibold text-slate-800">Performance</div>
                        <div className="flex flex-wrap gap-2">
                          {Number.isFinite(Number(perfMeta.prep_ms)) && (
                            <span className="inline-flex items-center rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5">
                              prep {Math.round(Number(perfMeta.prep_ms))} ms
                            </span>
                          )}
                          {Number.isFinite(Number(perfMeta.llm_ms)) && (
                            <span className="inline-flex items-center rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5">
                              llm {Math.round(Number(perfMeta.llm_ms))} ms
                            </span>
                          )}
                          {Number.isFinite(Number(perfMeta.total_ms)) && (
                            <span className="inline-flex items-center rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5">
                              total {Math.round(Number(perfMeta.total_ms))} ms
                            </span>
                          )}
                        </div>
                      </div>
                    )}

                    {timingsMeta && (
                      <div>
                        <div className="mb-1 font-semibold text-slate-800">Pipeline Timings</div>
                        <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded border border-slate-200 bg-slate-50 p-2 text-[11px] leading-relaxed">
                          {JSON.stringify(timingsMeta, null, 2)}
                        </pre>
                      </div>
                    )}

                    {promptStatsMeta && (
                      <div>
                        <div className="mb-1 font-semibold text-slate-800">Prompt Stats</div>
                        <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded border border-slate-200 bg-slate-50 p-2 text-[11px] leading-relaxed">
                          {JSON.stringify(promptStatsMeta, null, 2)}
                        </pre>
                      </div>
                    )}

                    {streamMeta?.stages && streamMeta.stages.length > 0 && (
                      <div>
                        <div className="mb-1 font-semibold text-slate-800">Stage Timeline</div>
                        <div className="space-y-1">
                          {streamMeta.stages.map((entry, idx) => (
                            <div
                              key={`${String(entry.stage || "unknown")}-${idx}`}
                              className="flex items-center justify-between rounded border border-slate-200 bg-slate-50 px-2 py-1"
                            >
                              <span className="text-[11px] text-slate-700">
                                {String(entry.label || entry.stage || "unknown")}
                              </span>
                              <span className="text-[11px] text-slate-500">
                                {Number.isFinite(Number(entry.elapsedMs))
                                  ? `${Math.round(Number(entry.elapsedMs))} ms`
                                  : "-"}
                              </span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}

            {!isUser && dataSources.length > 0 && assistantStatus !== "pending" && (
              <DataSourceBadges sources={dataSources} language={language} />
            )}

            {!isUser && plannerSources && assistantStatus !== "pending" && (
              <div className="chat-planner-panel mb-2 w-full rounded-md px-3 py-2 text-xs">
                <div className="mb-1.5 flex flex-wrap items-center gap-2">
                  <span className="inline-flex items-center gap-1.5 font-semibold text-slate-800">
                    <Database className="w-3.5 h-3.5" />
                    {language === "en" ? "Internal data used" : "已使用内部数据"}
                  </span>
                  {plannerSources.time_range?.start && plannerSources.time_range?.end && (
                    <span className="chat-planner-chip rounded px-1.5 py-0.5 text-[11px]">
                      {plannerSources.time_range.start} - {plannerSources.time_range.end}
                    </span>
                  )}
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {(plannerSources.sources || []).slice(0, 6).map((source, idx) => {
                    const rowCount = Number(source.row_count || 0);
                    const label = [source.product, source.intent]
                      .filter(Boolean)
                      .join(" / ");
                    return (
                      <span
                        key={`${source.table || "source"}-${source.intent || "intent"}-${idx}`}
                        className="chat-planner-chip inline-flex max-w-full items-center gap-1 rounded px-2 py-1 text-[11px]"
                        title={String(source.table || "")}
                      >
                        <span className="truncate font-medium">
                          {label || source.table || "source"}
                        </span>
                        <span className="opacity-80">
                          {rowCount} {language === "en" ? "rows" : "行"}
                        </span>
                      </span>
                    );
                  })}
                  {(plannerSources.sources || []).length === 0 &&
                    (plannerSources.data_sources || []).slice(0, 6).map((source, idx) => (
                      <span
                        key={`${source}-${idx}`}
                        className="chat-planner-chip inline-flex max-w-full items-center rounded px-2 py-1 text-[11px]"
                      >
                        <span className="truncate">{source}</span>
                      </span>
                    ))}
                </div>
              </div>
            )}

            {!(
              !isUser &&
              assistantStatus === "pending" &&
              !String(renderedContent || "").trim()
            ) && (
            <div
              className={`prose prose-sm md:prose-base max-w-none rounded-lg p-4 shadow-sm ${isUser
                ? "chat-user-bubble"
                : "chat-assistant-bubble w-full"
                }`}
            >
              {!isUser && alertsPrepended.length > 0 && (
                <div className="mb-3 space-y-2">
                  {alertsPrepended.slice(0, 3).map((alertText, idx) => (
                    <div
                      key={`${alertText}-${idx}`}
                      className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800"
                    >
                      {alertText}
                    </div>
                  ))}
                </div>
              )}

              {!isUser && blockingQualityFlags.length > 0 && (
                <div className="mb-3 flex flex-wrap gap-2">
                  {blockingQualityFlags.slice(0, 5).map((flag, idx) => (
                    <span
                      key={`${flag.code}-${idx}`}
                      className={`inline-flex items-center gap-1 rounded-full border px-2 py-1 text-[11px] ${severityClass(flag.severity)}`}
                      title={flag.message}
                    >
                      <span className="font-semibold uppercase">
                        {flag.severity}
                      </span>
                      <span>{flag.code}</span>
                    </span>
                  ))}
                </div>
              )}

              {!isUser && hasQualityMeta && assistantStatus !== "pending" && (
                <div className="mb-3">
                  <button
                    type="button"
                    onClick={() => setQualityPanelOpen((prev) => !prev)}
                    className={`inline-flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-[11px] font-medium hover:bg-slate-100 ${qualityNeedsReview
                      ? "border-amber-200 bg-amber-50 text-amber-800"
                      : "border-emerald-200 bg-emerald-50 text-emerald-800"
                      }`}
                  >
                    <Zap className="w-3.5 h-3.5" />
                    <span>{qualityNeedsReview ? text.qualityNeedsReview : text.qualityChecked}</span>
                    {executiveQualityScore !== null && (
                      <span className="rounded bg-white/70 px-1.5 py-0.5">
                        {executiveQualityScore}%
                      </span>
                    )}
                    {(qualityFlags.length + alertsPrepended.length) > 0 && (
                      <span className="text-current/70">
                        ({qualityFlags.length + alertsPrepended.length})
                      </span>
                    )}
                  </button>

                  {qualityPanelOpen && (
                    <div className="mt-2 rounded-md border border-slate-200 bg-slate-50 p-3 text-xs text-slate-700 space-y-3">
                      {alertsPrepended.length > 0 && (
                        <div>
                          <div className="mb-1 font-semibold text-slate-800">
                            {text.qualityAlerts}
                          </div>
                          <div className="space-y-1">
                            {alertsPrepended.slice(0, 5).map((alertText, idx) => (
                              <div key={`quality-alert-${idx}`}>
                                - {alertText}
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {qualityFlags.length > 0 && (
                        <div>
                          <div className="mb-1 font-semibold text-slate-800">
                            {text.qualityFlags}
                          </div>
                          <div className="space-y-1">
                            {qualityFlags.slice(0, 8).map((flag, idx) => (
                              <div key={`quality-flag-${flag.code}-${idx}`}>
                                - [{String(flag.severity || "").toUpperCase()}]{" "}
                                {flag.code}: {flag.message}
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {enhancementRows.length > 0 && (
                        <div>
                          <div className="mb-1 font-semibold text-slate-800">
                            {text.skillExecution}
                          </div>
                          <div className="space-y-1">
                            {enhancementRows
                              .slice(0, 10)
                              .map(([skill, status]) => (
                                <div key={`quality-skill-${skill}`}>
                                  - {skill}: {status}
                                </div>
                              ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}

              {isUser ? (
                <p className="whitespace-pre-wrap">{renderedContent}</p>
              ) : message.status === "pending" ? (
                renderedContent?.trim() ? (
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {String(renderedContent)}
                  </ReactMarkdown>
                ) : (
                  <div className="flex items-center gap-2 text-sm text-slate-400 py-1 not-prose">
                    <Loader2 className="h-4 w-4 animate-spin text-sky-500" />
                    <span>{language === "en" ? "Streaming answer..." : "正在流式生成答案..."}</span>
                  </div>
                )
              ) : (
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {String(renderedContent || "")}
                </ReactMarkdown>
              )}
            </div>
            )}

            {!isUser && assistantStatus !== "pending" && (
              <DrillDownActions
                onSelectSuggestion={onSelectSuggestion}
                language={language}
                content={String(renderedContent || message.content || "")}
              />
            )}

            {/* Only render legacy PNG attachments when no interactive visualizations exist */}
            {!isUser && chartAttachments.length > 0 && (!visualizations || visualizations.length === 0) && (
              <div className="mt-3 w-full space-y-3">
                {chartAttachments.map((chart, idx) => {
                  const src = chart.base64
                    ? `data:${chart.mime_type || "image/png"};base64,${chart.base64}`
                    : chart.url || "";
                  if (!src) return null;
                  return (
                    <div
                      key={`chart-attachment-${idx}`}
                      className="rounded-lg border border-slate-200 bg-slate-900 p-2"
                    >
                      {chart.title && (
                        <div className="mb-1 text-xs font-medium text-slate-300">
                          {chart.title}
                        </div>
                      )}
                      <img
                        src={src}
                        alt={chart.title || "Chart"}
                        className="max-w-full rounded"
                        loading="lazy"
                      />
                    </div>
                  );
                })}
              </div>
            )}

            {!isUser && visualizations && visualizations.length > 0
              ? visualizations.map((viz, idx) => (
                <ChatVisualizationCard key={idx} visualization={viz} theme={chartTheme} />
              ))
              : !isUser &&
              visualization && (
                <ChatVisualizationCard visualization={visualization} theme={chartTheme} />
              )}

            {/* Prominent export card for report mode */}
            {!isUser &&
              resolvedMode === "report" &&
              onExportMessage &&
              assistantStatus === "success" && (
                <div className="mt-3 w-full rounded-xl border border-blue-200 bg-blue-50 p-4">
                  <div className="mb-2 flex items-center gap-2">
                    <FileText className="w-4 h-4 text-blue-600" />
                    <span className="text-sm font-semibold text-blue-800">
                      {text.exportThisReport}
                    </span>
                  </div>
                  <div className="flex gap-2 flex-wrap">
                    <button
                      type="button"
                      disabled={isExporting !== null}
                      onClick={async () => {
                        setIsExporting("pdf");
                        try {
                          await onExportMessage("pdf");
                        } finally {
                          setIsExporting(null);
                        }
                      }}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-blue-300 bg-white px-3 py-1.5 text-xs font-medium text-blue-700 hover:bg-blue-100 transition-colors disabled:opacity-40 disabled:cursor-not-allowed shadow-sm"
                    >
                      {isExporting === "pdf" ? (
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      ) : (
                        <Download className="w-3.5 h-3.5" />
                      )}
                      {text.exportPdf}
                    </button>
                    <button
                      type="button"
                      disabled={isExporting !== null}
                      onClick={async () => {
                        setIsExporting("docx");
                        try {
                          await onExportMessage("docx");
                        } finally {
                          setIsExporting(null);
                        }
                      }}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-blue-300 bg-white px-3 py-1.5 text-xs font-medium text-blue-700 hover:bg-blue-100 transition-colors disabled:opacity-40 disabled:cursor-not-allowed shadow-sm"
                    >
                      {isExporting === "docx" ? (
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      ) : (
                        <FileText className="w-3.5 h-3.5" />
                      )}
                      {text.exportWord}
                    </button>
                  </div>
                </div>
              )}

            {!isUser && followUps.length > 0 && onSelectSuggestion && (
              <div className="mt-2 flex flex-wrap gap-2 w-full max-w-full">
                <div className="w-full text-xs font-semibold text-slate-600 mb-1">
                  {text.followUps}
                </div>
                {followUps.map((suggestion, idx) => (
                  <button
                    key={`${suggestion}-${idx}`}
                    type="button"
                    onClick={() => onSelectSuggestion(suggestion)}
                    className="rounded-full border border-blue-200 bg-blue-50 px-3 py-1.5 text-xs text-blue-700 hover:bg-blue-100 transition-colors text-left"
                    title={text.suggestionTitle}
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            )}

            {/* Action toolbar */}
            {!isUser && assistantStatus !== "pending" && (
              <div className="mt-2 flex items-center gap-1 flex-wrap">
                <button
                  type="button"
                  onClick={handleCopy}
                  title={text.copy}
                  className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-100 hover:text-slate-900 transition-colors"
                >
                  {copied ? (
                    <Check className="w-3.5 h-3.5 text-emerald-500" />
                  ) : (
                    <Copy className="w-3.5 h-3.5" />
                  )}
                  <span>{copied ? text.copied : text.copy}</span>
                </button>

                {/* Per-message export buttons */}
                {onExportMessage && (
                  <>
                    <button
                      type="button"
                      disabled={isExporting !== null}
                      onClick={async () => {
                        setIsExporting("pdf");
                        try {
                          await onExportMessage("pdf");
                        } finally {
                          setIsExporting(null);
                        }
                      }}
                      title={text.exportPdfTitle}
                      className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-100 hover:text-slate-900 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      {isExporting === "pdf" ? (
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      ) : (
                        <Download className="w-3.5 h-3.5" />
                      )}
                      <span>PDF</span>
                    </button>
                    <button
                      type="button"
                      disabled={isExporting !== null}
                      onClick={async () => {
                        setIsExporting("docx");
                        try {
                          await onExportMessage("docx");
                        } finally {
                          setIsExporting(null);
                        }
                      }}
                      title={text.exportWordTitle}
                      className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-100 hover:text-slate-900 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      {isExporting === "docx" ? (
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      ) : (
                        <FileText className="w-3.5 h-3.5" />
                      )}
                      <span>Word</span>
                    </button>
                  </>
                )}
                {onGenerateReport && (
                  <button
                    type="button"
                    onClick={onGenerateReport}
                    disabled={!canGenerateReport}
                    title={text.generateReportTitle}
                    className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-100 hover:text-slate-900 transition-colors disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    <FileText className="w-3.5 h-3.5" />
                    <span>{text.generateReport}</span>
                  </button>
                )}
                {onAddToWeeklyReport && (
                  <button
                    type="button"
                    onClick={onAddToWeeklyReport}
                    disabled={!canGenerateReport}
                    title={text.addToWeeklyReportTitle}
                    className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-100 hover:text-slate-900 transition-colors disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    <span>{text.addToWeeklyReport}</span>
                  </button>
                )}
                {isPricingRecommendation && onSaveToDecisionLog && (
                  <button
                    type="button"
                    onClick={() =>
                      onSaveToDecisionLog(safeString(message.content))
                    }
                    title={text.saveToLogTitle}
                    className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-emerald-600 hover:bg-emerald-50 hover:text-emerald-700 border border-emerald-200 transition-colors"
                  >
                    <BookmarkPlus className="w-3.5 h-3.5" />
                    <span>{text.saveToLog}</span>
                  </button>
                )}
                <div className="w-px h-4 bg-slate-200 mx-1" />
                <button
                  type="button"
                  onClick={() => onOpenRefs?.(refs)}
                  title={text.openRefsTitle}
                  className={`inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs transition-colors ${refSourceOpen
                    ? "bg-blue-50 text-blue-700 border border-blue-200"
                    : "font-medium text-slate-700 hover:bg-slate-100 hover:text-slate-900"
                    }`}
                >
                  <BookOpen className="w-3.5 h-3.5" />
                  <span>{text.reference}</span>
                  {refs.length > 0 && (
                    <span
                      className={`rounded-full px-1.5 py-0 text-[10px] font-bold ${refSourceOpen
                        ? "bg-blue-600 text-white"
                        : "bg-slate-200 text-slate-600"
                        }`}
                    >
                      {refs.reduce(
                        (s, r) => s + (r.urls.length > 0 ? r.urls.length : 1),
                        0,
                      )}
                    </span>
                  )}
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    );
  };
