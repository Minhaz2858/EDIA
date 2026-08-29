import React, { useCallback, useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { api, API_BASE_URL, GeneratedReport, WeeklyReportChartPoint, WeeklyReportProductCharts, WeeklyReportSingleChart, WeeklyReportSummary, WeeklyReportResponse } from '../services/api';
import { useLanguage } from '../lib/language';

// ─── helpers ─────────────────────────────────────────────────────────────────

function fmtDate(iso?: string): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' });
}

function fmtDateRange(start?: string, end?: string): string {
  if (!start && !end) return '—';
  const s = fmtDate(start);
  const e = fmtDate(end);
  return `${s} 至 ${e}`;
}

function toWeeklyReportSummary(report: GeneratedReport): WeeklyReportSummary {
  return {
    id: String(report.id),
    title: report.title,
    status: report.status,
    created_at: report.created_at,
    context: report.context ?? {},
    markdown: report.markdown,
    is_unread: report.is_unread,
  };
}

interface ProductReportSection {
  index: number;
  title: string;
  slug: string;
  raw: string;
  priceSnapshot: string;
  marketAnalysis: string;
  forecast: string;
  confidence: string;
  source: string;
}

interface TextDraft {
  marketAnalysis: string;
  forecast: string;
  confidence: string;
}

interface ChartDraft {
  yearly: WeeklyReportSingleChart;
  monthly: WeeklyReportSingleChart;
  yearlyCsv: string;
  monthlyCsv: string;
}

type ChartType = 'yearly' | 'monthly';

function getReportId(report: WeeklyReportSummary): string {
  return String(report.context?.report_id || report.id || '');
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function stripImages(markdown: string): string {
  return markdown.replace(/!\[[^\]]*\]\([^)]*\)\s*/g, '').trim();
}

function extractBetween(markdown: string, labelPattern: string, stopPattern: string): string {
  const regex = new RegExp(`\\*\\*(?:${labelPattern})\\s*[:：]\\*\\*\\s*\\n?([\\s\\S]*?)(?=\\n\\n\\*\\*(?:${stopPattern})\\s*[:：]\\*\\*|$)`, 'i');
  return (markdown.match(regex)?.[1] || '').trim();
}

function extractProductSlug(title: string, raw: string, products: Record<string, any>): string {
  const imageSlug = raw.match(/\/charts\/([^/)]+?)_(?:12mo|monthly)\.png/i)?.[1];
  if (imageSlug && products[imageSlug]) return imageSlug;
  const normalizedTitle = title.toLowerCase();
  const found = Object.entries(products).find(([, product]) => {
    const en = String(product?.product_name_en || '').toLowerCase();
    const zh = String(product?.product_name_zh || '').toLowerCase();
    return (!!en && normalizedTitle.includes(en)) || (!!zh && title.includes(zh));
  });
  return found?.[0] || imageSlug || '';
}

function parseProductSections(markdown: string, products: Record<string, any>): ProductReportSection[] {
  const sections: ProductReportSection[] = [];
  const regex = /^###\s+(\d+)\.\s+([^\n]+)\n([\s\S]*?)(?=\n---\s*(?:\n|$)|\n###\s+\d+\.\s+|$)/gm;
  for (const match of markdown.matchAll(regex)) {
    const raw = match[0].trim();
    const title = match[2].trim();
    const body = match[3].trim();
    const slug = extractProductSlug(title, raw, products);
    if (!slug || !products[slug]) continue;
    const forecastBlock = extractBetween(body, 'Forecast|预测', '$^');
    const confidence = forecastBlock.match(/(?:Confidence|置信度):\s*([^\n]+)/i)?.[1]?.trim() || '';
    const source = forecastBlock.match(/(?:Source|预测来源):\s*([^\n]+)/i)?.[1]?.trim() || '';
    const forecast = forecastBlock
      .split('\n')
      .filter(line => !/^>\s*(?:Confidence|置信度|Source|预测来源):/i.test(line.trim()))
      .join('\n')
      .replace(/^>\s?/gm, '')
      .trim();
    sections.push({
      index: Number(match[1]),
      title,
      slug,
      raw,
      priceSnapshot: stripImages(extractBetween(body, 'Price Snapshot|价格快照', 'Market Analysis|市场分析')),
      marketAnalysis: extractBetween(body, 'Market Analysis|市场分析', 'Forecast|预测'),
      forecast,
      confidence,
      source,
    });
  }
  return sections;
}

function replaceProductSectionText(markdown: string, section: ProductReportSection, draft: TextDraft, isEnglish: boolean): string {
  const analysisLabel = isEnglish ? 'Market Analysis' : '市场分析';
  const forecastLabel = isEnglish ? 'Forecast' : '预测';
  const confidenceLabel = isEnglish ? 'Confidence' : '置信度';
  const sourceLabel = isEnglish ? 'Source' : '预测来源';
  const source = section.source || (isEnglish ? 'Forecasting Tier' : 'Forecasting Tier');
  const forecastBlock = `**${forecastLabel}:**\n${draft.forecast.trim()}\n> ${confidenceLabel}: ${draft.confidence.trim()}\n> ${sourceLabel}: ${source}`;
  const updatedRaw = section.raw
    .replace(
      /(\*\*(?:Market Analysis|市场分析):\*\*\s*\n?)([\s\S]*?)(?=\n\n\*\*(?:Forecast|预测):\*\*)/i,
      `**${analysisLabel}:**\n${draft.marketAnalysis.trim()}\n`,
    )
    .replace(/\*\*(?:Forecast|预测):\*\*\s*\n?[\s\S]*$/i, forecastBlock);
  return markdown.replace(section.raw, updatedRaw);
}

function pointsToCsv(chart: WeeklyReportSingleChart): string {
  const seriesKeys = (chart.series || []).map(series => series.key);
  const header = ['period', 'label', ...seriesKeys];
  const rows = (chart.points || []).map(point => header.map(key => String(point[key] ?? '')).join(','));
  return [header.join(','), ...rows].join('\n');
}

function csvToPoints(csv: string, chart: WeeklyReportSingleChart): WeeklyReportChartPoint[] {
  const rows = csv.split(/\r?\n/).map(row => row.trim()).filter(Boolean);
  if (rows.length < 2) return chart.points || [];
  const headers = rows[0].split(',').map(item => item.trim());
  return rows.slice(1).map(row => {
    const cells = row.split(',').map(item => item.trim());
    const point: WeeklyReportChartPoint = {
      period: cells[headers.indexOf('period')] || cells[0] || '',
      label: cells[headers.indexOf('label')] || cells[1] || cells[0] || '',
    };
    headers.forEach((key, index) => {
      if (key === 'period' || key === 'label') return;
      const raw = cells[index];
      point[key] = raw === '' || raw == null ? null : Number(raw);
    });
    return point;
  });
}

function editableChartFromCsv(chart: WeeklyReportSingleChart, csv: string): WeeklyReportSingleChart {
  return { ...chart, points: csvToPoints(csv, chart) };
}

function chartSummary(chart: WeeklyReportSingleChart): { rows: number; numeric: number; min: number | null; max: number | null } {
  const values = (chart.points || []).flatMap(point =>
    (chart.series || [])
      .map(series => Number(point[series.key]))
      .filter(value => Number.isFinite(value)),
  );
  return {
    rows: chart.points?.length || 0,
    numeric: values.length,
    min: values.length ? Math.min(...values) : null,
    max: values.length ? Math.max(...values) : null,
  };
}

function formatCompactNumber(value: number | null): string {
  return value == null ? '—' : value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function parseDay(value: string | null | undefined): number | null {
  if (!value) return null;
  const ts = new Date(value).getTime();
  return Number.isFinite(ts) ? ts : null;
}

function latestSeriesMove(chart: WeeklyReportSingleChart): { label: string; start: number; end: number; pct: number } | null {
  const key = chart.series?.[0]?.key;
  const label = chart.series?.[0]?.label || key;
  if (!key) return null;

  const highlightStart = parseDay(chart.highlight?.start);
  const highlightEnd = parseDay(chart.highlight?.end);
  const pointsInReportWindow = (chart.points || []).filter(point => {
    if (highlightStart == null || highlightEnd == null) return false;
    const pointTs = parseDay(String(point.period || ''));
    return pointTs != null && pointTs >= highlightStart && pointTs <= highlightEnd;
  });

  const sourcePoints = pointsInReportWindow.length >= 2
    ? pointsInReportWindow
    : (chart.points || []).slice(-2);
  const numericPoints = sourcePoints
    .map(point => Number(point[key]))
    .filter(value => Number.isFinite(value));
  if (!key || numericPoints.length < 2) return null;
  const start = numericPoints[0];
  const end = numericPoints[numericPoints.length - 1];
  if (!start) return null;
  return { label, start, end, pct: ((end - start) / start) * 100 };
}

function cleanProductName(title: string): string {
  const trimmed = title.trim();
  const match = trimmed.match(/^(.+?)\s*\(([^)]+)\)$/);
  if (!match) return trimmed;
  return match[1].trim();
}

function hasBackendForecastSource(chart: WeeklyReportSingleChart): boolean {
  const source = String(chart.forecast_band?.source || chart.forecast_band?.model_version || '');
  return /Forecasting Service|canonical|forecasting_service/i.test(source);
}

function buildRegeneratedText(section: ProductReportSection, yearly: WeeklyReportSingleChart, monthly: WeeklyReportSingleChart, isEnglish: boolean): TextDraft {
  const move = latestSeriesMove(monthly) || latestSeriesMove(yearly);
  const direction = !move || Math.abs(move.pct) < 0.5 ? 'stable' : move.pct > 0 ? 'up' : 'down';
  const productName = cleanProductName(section.title);
  const confidence = yearly.forecast_band?.confidence_label || section.confidence || (isEnglish ? 'Low (2/5)' : '低 (2/5)');
  const forecastRange = yearly.forecast_band?.low != null && yearly.forecast_band?.high != null
    ? `${Number(yearly.forecast_band.low).toLocaleString()}-${Number(yearly.forecast_band.high).toLocaleString()}`
    : '';
  if (isEnglish) {
    const movement = move
      ? `${move.label} moved from ${move.start.toLocaleString()} to ${move.end.toLocaleString()}, ${move.pct >= 0 ? 'up' : 'down'} ${Math.abs(move.pct).toFixed(2)}% over the configured window.`
      : 'The configured data does not show enough numeric points to calculate a fresh movement signal.';
    return {
      marketAnalysis: `${movement} The regenerated read is ${direction === 'stable' ? 'broadly stable, with limited directional conviction' : direction === 'up' ? 'firmer, with short-term upside pressure visible in the edited data' : 'softer, with downside pressure visible in the edited data'}. Please review supply, demand, and order conversion before final pricing action.`,
      forecast: forecastRange
        ? `For next week, ${productName} is expected to remain in the ${forecastRange} range unless fresh market evidence changes the direction.`
        : hasBackendForecastSource(yearly)
          ? `For next week, ${productName} should be reviewed against the backend forecast snapshot before final pricing action.`
        : `For next week, ${productName} is expected to remain ${direction === 'up' ? 'firm' : direction === 'down' ? 'under pressure' : 'stable'}.`,
      confidence,
    };
  }
  const movement = move
    ? `${move.label} 从 ${move.start.toLocaleString()} 变动至 ${move.end.toLocaleString()}，配置区间内${move.pct >= 0 ? '上涨' : '下跌'} ${Math.abs(move.pct).toFixed(2)}%。`
    : '当前配置数据不足以重新计算明确的价格变动信号。';
  return {
    marketAnalysis: `${movement} 重新生成后的判断为${direction === 'stable' ? '整体走稳，方向性不强' : direction === 'up' ? '短期偏强，编辑后的数据体现一定上行压力' : '短期偏弱，编辑后的数据体现一定下行压力'}。建议结合供需、订单转化和竞品报价后再确定最终动作。`,
    forecast: forecastRange
      ? `预计下周 ${productName} 主流价格维持在 ${forecastRange} 区间，除非出现新的市场证据改变方向。`
      : hasBackendForecastSource(yearly)
        ? `预计下周 ${productName} 需以后台预测快照为准，定价前复核预测区间与最新市场证据。`
      : `预计下周 ${productName}${direction === 'up' ? '偏强运行' : direction === 'down' ? '承压运行' : '维持稳定'}。`,
    confidence,
  };
}

function resolveDisplayText(
  section: ProductReportSection,
  productChart: WeeklyReportProductCharts | undefined,
  isEnglish: boolean,
): TextDraft {
  if (section.marketAnalysis.trim() && section.forecast.trim()) {
    return {
      marketAnalysis: section.marketAnalysis,
      forecast: section.forecast,
      confidence: section.confidence,
    };
  }

  if (productChart?.yearly && productChart?.monthly) {
    const fallback = buildRegeneratedText(section, productChart.yearly, productChart.monthly, isEnglish);
    return {
      marketAnalysis: section.marketAnalysis.trim() || fallback.marketAnalysis,
      forecast: section.forecast.trim() || fallback.forecast,
      confidence: section.confidence.trim() || fallback.confidence,
    };
  }

  return {
    marketAnalysis:
      section.marketAnalysis.trim() ||
      (isEnglish
        ? 'The report has chart data for this product, but the generated narrative is not available. Please review the chart and price snapshot before final publication.'
        : '本产品已有图表数据，但报告正文暂未生成。发布前请结合图表和价格快照补充市场分析。'),
    forecast:
      section.forecast.trim() ||
      (isEnglish
        ? 'Forecast text is not available. Please regenerate the report or edit this section before publishing.'
        : '预测正文暂未生成，请重新生成报告或在发布前手动编辑本段。'),
    confidence: section.confidence,
  };
}

// ─── sub-components ───────────────────────────────────────────────────────────

interface DirectWeeklyReportChartProps {
  chart: WeeklyReportSingleChart | undefined;
  productName: string;
  language: string;
}

const DirectWeeklyReportChart: React.FC<DirectWeeklyReportChartProps> = ({ chart, productName, language }) => {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const points = chart?.points || [];
  const series = chart?.series || [];
  const activePoints = points.filter(point => series.some(item => Number.isFinite(Number(point[item.key]))));
  const width = 720;
  const height = 250;
  const padding = { top: 62, right: 32, bottom: 34, left: 54 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;

  if (!chart || activePoints.length === 0 || series.length === 0) {
    return (
      <div className="flex h-[250px] items-center justify-center rounded-lg bg-[#0b1829] text-sm text-slate-400">
        {language === 'en' ? 'No chart data configured' : '暂无图表数据'}
      </div>
    );
  }

  const values = activePoints.flatMap(point =>
    series
      .map(item => Number(point[item.key]))
      .filter(value => Number.isFinite(value)),
  );
  const minValue = Math.min(...values);
  const maxValue = Math.max(...values);
  const domainPadding = Math.max((maxValue - minValue) * 0.12, 1);
  const yMin = minValue - domainPadding;
  const yMax = maxValue + domainPadding;
  const formatAxis = (value: number) => {
    if (Math.abs(value) >= 1000) return `${(value / 1000).toFixed(1)}k`;
    return value.toLocaleString(undefined, { maximumFractionDigits: 1 });
  };
  const xFor = (index: number) => padding.left + (activePoints.length <= 1 ? plotWidth / 2 : (index / (activePoints.length - 1)) * plotWidth);
  const yFor = (value: number) => padding.top + ((yMax - value) / (yMax - yMin)) * plotHeight;
  const gridValues = Array.from({ length: 5 }, (_, index) => yMin + ((yMax - yMin) / 4) * index).reverse();
  const labelStep = Math.max(1, Math.ceil(activePoints.length / 8));
  const selectedIndex = hoverIndex;
  const selectedPoint = hoverIndex !== null ? activePoints[hoverIndex] : null;
  const tooltipX = selectedIndex !== null ? Math.min(Math.max(xFor(selectedIndex) + 12, padding.left + 12), width - 185) : 0;
  const tooltipY = selectedIndex !== null ? Math.max(padding.top + 8, yFor(Number(selectedPoint?.[series[0]?.key] || yMax)) - 42) : 0;

  return (
    <div className="rounded-lg bg-[#0b1829] p-3 text-slate-100">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="h-full min-h-[250px] w-full"
        role="img"
        aria-label={`${productName} ${chart.title}`}
        style={{ fontFamily: 'var(--font-zh), sans-serif' }}
        onMouseLeave={() => setHoverIndex(null)}
      >
        <rect x="0" y="0" width={width} height={height} rx="10" fill="#0b1829" />
        <text x="18" y="31" fill="#f8fafc" fontSize="16" fontWeight="700">{productName}</text>
        <text x="120" y="31" fill="#cbd5e1" fontSize="13">{chart.title}</text>
        <text x={width - 100} y="31" fill="#94a3b8" fontSize="12">{language === 'en' ? 'Report Week' : '本周'}</text>

        <g transform="translate(18 52)">
          {series.map((item, index) => (
            <g key={item.key} transform={`translate(${index * 150} 0)`}>
              <line x1="0" y1="0" x2="16" y2="0" stroke={item.color || '#3b82f6'} strokeWidth="2" />
              <text x="23" y="4" fill="#dbe3ee" fontSize="12">{item.label}</text>
            </g>
          ))}
        </g>

        {gridValues.map(value => {
          const y = yFor(value);
          return (
            <g key={value}>
              <line x1={padding.left} x2={width - padding.right} y1={y} y2={y} stroke="#1e3148" strokeWidth="1" />
              <text x={padding.left - 10} y={y + 4} fill="#718299" fontSize="11" textAnchor="end">{formatAxis(value)}</text>
            </g>
          );
        })}

        {activePoints.map((point, index) => (
          index % labelStep === 0 || index === activePoints.length - 1 ? (
            <text key={`${point.period}-${index}`} x={xFor(index)} y={height - 12} fill="#718299" fontSize="11" textAnchor="middle">
              {String(point.label || point.period)}
            </text>
          ) : null
        ))}

        {series.map(item => {
          const path = activePoints
            .map((point, index) => {
              const value = Number(point[item.key]);
              if (!Number.isFinite(value)) return '';
              return `${index === 0 ? 'M' : 'L'} ${xFor(index)} ${yFor(value)}`;
            })
            .filter(Boolean)
            .join(' ');
          return (
            <g key={item.key}>
              <path d={path} fill="none" stroke={item.color || '#3b82f6'} strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
              {activePoints.map((point, index) => {
                const value = Number(point[item.key]);
                if (!Number.isFinite(value)) return null;
                return <circle key={`${item.key}-${index}`} cx={xFor(index)} cy={yFor(value)} r={hoverIndex === index ? 4 : 2.6} fill={item.color || '#3b82f6'} />;
              })}
            </g>
          );
        })}

        {hoverIndex !== null && (
          <>
            <line x1={xFor(hoverIndex)} x2={xFor(hoverIndex)} y1={padding.top} y2={height - padding.bottom} stroke="#e2e8f0" strokeWidth="1.2" opacity="0.75" />
            {selectedPoint && (
              <g pointerEvents="none">
                <rect x={tooltipX} y={tooltipY} width="170" height={38 + series.length * 20} rx="8" fill="#243247" stroke="#3b4b63" />
                <text x={tooltipX + 14} y={tooltipY + 23} fill="#e2e8f0" fontSize="13" fontWeight="700">{String(selectedPoint.label || selectedPoint.period)}</text>
                {series.map((item, index) => {
                  const value = Number(selectedPoint[item.key]);
                  return (
                    <text key={item.key} x={tooltipX + 14} y={tooltipY + 46 + index * 20} fill={item.color || '#3b82f6'} fontSize="12" fontWeight="700">
                      {item.label}: {Number.isFinite(value) ? value.toLocaleString(undefined, { maximumFractionDigits: 2 }) : '--'}
                    </text>
                  );
                })}
              </g>
            )}
          </>
        )}

        {activePoints.map((point, index) => (
          <rect
            key={`hit-${point.period}-${index}`}
            x={xFor(index) - plotWidth / Math.max(activePoints.length - 1, 1) / 2}
            y={padding.top}
            width={Math.max(12, plotWidth / Math.max(activePoints.length - 1, 1))}
            height={plotHeight}
            fill="transparent"
            onMouseEnter={() => setHoverIndex(index)}
            onFocus={() => setHoverIndex(index)}
          />
        ))}
      </svg>
    </div>
  );
};

interface ReportListItemProps {
  report: WeeklyReportSummary;
  isSelected: boolean;
  onSelect: (r: WeeklyReportSummary) => void;
  onDelete: (r: WeeklyReportSummary) => void;
  deleting: boolean;
}

const ReportListItem: React.FC<ReportListItemProps> = ({ report, isSelected, onSelect, onDelete, deleting }) => {
  const { title, status, created_at, context } = report;
  const dateRange = fmtDateRange(context?.week_start, context?.week_end);
  const count = context?.products_count;

  const statusColors: Record<string, string> = {
    draft: 'bg-yellow-100 text-yellow-700 border-yellow-200',
    final: 'bg-green-100 text-green-700 border-green-200',
    error: 'bg-red-100 text-red-700 border-red-200',
  };
  const statusLabel: Record<string, string> = {
    draft: '草稿', final: '已完成', error: '错误',
  };
  const colorClass = statusColors[status] ?? 'bg-slate-100 text-slate-600 border-slate-200';
  const label = statusLabel[status] ?? status;

  return (
    <div className={`relative group border-b border-slate-100 ${isSelected ? 'bg-blue-50 border-l-2 border-l-blue-500' : 'hover:bg-blue-50'} transition-colors`}>
      <button
        className="w-full text-left px-3 py-2 pr-11"
        onClick={() => onSelect(report)}
      >
        <div className="flex items-start justify-between gap-2">
          <span className="text-xs font-medium text-slate-800 line-clamp-2 flex-1">{title}</span>
          <span className={`text-[10px] px-1.5 py-0.5 rounded border flex-shrink-0 ${colorClass}`}>{label}</span>
        </div>
        <div className="flex items-center gap-3 mt-1">
          <span className="text-[11px] text-slate-500">{dateRange}</span>
          {count != null && (
            <span className="text-[11px] text-slate-400">{count} 个产品</span>
          )}
        </div>
        <div className="text-[10px] text-slate-400 mt-0.5">{fmtDate(created_at)}</div>
      </button>
      <button
        className={`absolute top-2 right-2 p-1.5 rounded-md border shadow-sm transition-all ${isSelected
          ? 'opacity-100 bg-white text-slate-600 border-slate-200'
          : 'opacity-95 md:opacity-60 md:group-hover:opacity-100 bg-white/90 text-slate-500 border-slate-200'
          } hover:text-red-600 hover:bg-red-50 hover:border-red-200`}
        title="删除周报"
        disabled={deleting}
        onClick={(e) => { e.stopPropagation(); onDelete(report); }}
      >
        <svg xmlns="http://www.w3.org/2000/svg" className="h-3.5 w-3.5" viewBox="0 0 20 20" fill="currentColor">
          <path fillRule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" clipRule="evenodd" />
        </svg>
      </button>
    </div>
  );
};

// ─── main component ───────────────────────────────────────────────────────────

export const WeeklyReportsTab: React.FC = () => {
  const { language } = useLanguage();
  const [reports, setReports] = useState<WeeklyReportSummary[]>([]);
  const [selected, setSelected] = useState<WeeklyReportSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  // Edit mode state
  const [isEditing, setIsEditing] = useState(false);
  const [editTitle, setEditTitle] = useState('');
  const [editMarkdown, setEditMarkdown] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [textEditingSection, setTextEditingSection] = useState<ProductReportSection | null>(null);
  const [textDraft, setTextDraft] = useState<TextDraft>({ marketAnalysis: '', forecast: '', confidence: '' });
  const [configuringSection, setConfiguringSection] = useState<ProductReportSection | null>(null);
  const [chartDraft, setChartDraft] = useState<ChartDraft | null>(null);

  const loadReports = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.listWeeklyReports();
      const sorted = (res.reports ?? []).sort((a, b) =>
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      );
      setReports(sorted);
      setSelected(current => {
        if (sorted.length === 0) return null;
        if (!current) return sorted[0];
        return sorted.find(report => report.id === current.id) ?? sorted[0];
      });
    } catch (e: any) {
      setError(e?.message ?? '加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadReports(); }, [loadReports]);

  const handleGenerate = async () => {
    setGenerating(true);
    setError(null);
    try {
      const result: WeeklyReportResponse = await api.generateWeeklyReport({ language });
      const newEntry: WeeklyReportSummary = {
        id: String(result.id || result.report_id),
        title: result.title,
        status: 'draft',
        created_at: result.generated_at,
        context: result.context ?? {
          report_id: result.report_id,
          week_start: result.week_start,
          week_end: result.week_end,
          products_count: result.products_count,
          generated_at: result.generated_at,
        },
        markdown: result.markdown,
      };
      setReports(prev => [newEntry, ...prev]);
      setSelected(newEntry);
      setIsEditing(false);
    } catch (e: any) {
      setError(`生成失败: ${e?.message ?? '未知错误'}`);
    } finally {
      setGenerating(false);
    }
  };

  const handleDelete = async (report: WeeklyReportSummary) => {
    const generatedReportId = String(report.id || '');
    if (!generatedReportId) return;
    if (!window.confirm('确定要删除这份周报吗？此操作不可撤销。')) return;
    setDeletingId(generatedReportId);
    try {
      await api.deleteWeeklyReport(generatedReportId);
      setReports(prev => prev.filter(r => String(r.id) !== generatedReportId));
      if (String(selected?.id || '') === generatedReportId) {
        setSelected(null);
        setIsEditing(false);
      }
    } catch (e: any) {
      setError(`删除失败: ${e?.message ?? '未知错误'}`);
    } finally {
      setDeletingId(null);
    }
  };

  const handleStartEdit = () => {
    if (!selected) return;
    setEditTitle(selected.title ?? '');
    setEditMarkdown(selected.markdown ?? '');
    setSaveError(null);
    setIsEditing(true);
    setTimeout(() => textareaRef.current?.focus(), 50);
  };

  const handleCancelEdit = () => {
    setIsEditing(false);
    setSaveError(null);
  };

  const handleSave = async () => {
    const generatedReportId = String(selected?.id || '');
    if (!generatedReportId) { setSaveError('无法确定报告 ID，请刷新后重试'); return; }
    if (!editTitle.trim()) { setSaveError('标题不能为空'); return; }
    if (!editMarkdown.trim()) { setSaveError('内容不能为空'); return; }
    setSaving(true);
    setSaveError(null);
    try {
      await api.updateWeeklyReport(generatedReportId, { title: editTitle.trim(), markdown: editMarkdown.trim() });
      const updated: WeeklyReportSummary = {
        ...selected!,
        title: editTitle.trim(),
        markdown: editMarkdown.trim(),
      };
      setSelected(updated);
      setReports(prev => prev.map(r => r.id === selected!.id ? updated : r));
      setIsEditing(false);
    } catch (e: any) {
      setSaveError(`保存失败: ${e?.message ?? '未知错误'}`);
    } finally {
      setSaving(false);
    }
  };

  const sanitize = (md: string) => md
    .replace(/>\s*_?本报告由\s*EDIA\s*自动生成[^\n]*\n?/g, '')
    .replace(/>\s*_?预测值来自\s*Forecasting\s*Tier[^\n]*\n?/g, '')
    .replace(/>\s*_?文本由\s*Deepseek[^\n]*\n?/g, '')
    .replace(/>\s*_?如有异议[^\n]*\n?/g, '');

  const rawMarkdown = selected?.markdown ?? '';
  const markdown = sanitize(rawMarkdown);
  const previewMarkdown = sanitize(isEditing ? editMarkdown : rawMarkdown);
  const productCharts = selected?.context?.weekly_chart_payload?.products ?? {};
  const productSections = selected ? parseProductSections(markdown, productCharts) : [];
  const canRenderStructuredReport = selected && productSections.length > 0;

  const ImageComponent = ({ src, alt, ...rest }: React.ImgHTMLAttributes<HTMLImageElement>) => {
    const imgSrc = src?.startsWith('/api/reports-static/') ? `${API_BASE_URL}${src}` : src;
    return (
      <img
        src={imgSrc}
        alt={alt ?? ''}
        style={{ maxWidth: '100%', height: 'auto', borderRadius: 4, display: 'block', margin: '12px 0' }}
        {...rest}
      />
    );
  };

  const applyUpdatedReport = (updated: WeeklyReportSummary) => {
    setSelected(updated);
    setReports(prev => prev.map(report => report.id === updated.id ? updated : report));
  };

  const handleStartSectionTextEdit = (section: ProductReportSection) => {
    const fallbackText = resolveDisplayText(section, productCharts[section.slug], language === 'en');
    setTextEditingSection(section);
    setTextDraft({
      marketAnalysis: fallbackText.marketAnalysis,
      forecast: fallbackText.forecast,
      confidence: fallbackText.confidence,
    });
    setSaveError(null);
  };

  const handleSaveSectionText = async () => {
    if (!selected || !textEditingSection) return;
    const generatedReportId = String(selected.id || '');
    if (!generatedReportId) return;
    if (!textDraft.marketAnalysis.trim() || !textDraft.forecast.trim()) {
      setSaveError('Market analysis and forecast are required.');
      return;
    }
    setSaving(true);
    setSaveError(null);
    try {
      const nextMarkdown = replaceProductSectionText(rawMarkdown, textEditingSection, textDraft, language === 'en');
      await api.updateWeeklyReport(generatedReportId, { title: selected.title, markdown: nextMarkdown });
      applyUpdatedReport({ ...selected, markdown: nextMarkdown });
      setTextEditingSection(null);
    } catch (e: any) {
      setSaveError(`保存失败: ${e?.message ?? '未知错误'}`);
    } finally {
      setSaving(false);
    }
  };

  const handleStartConfigure = (section: ProductReportSection) => {
    const product = productCharts[section.slug];
    if (!product?.yearly || !product?.monthly) return;
    const yearly = JSON.parse(JSON.stringify(product.yearly)) as WeeklyReportSingleChart;
    const monthly = JSON.parse(JSON.stringify(product.monthly)) as WeeklyReportSingleChart;
    setConfiguringSection(section);
    setChartDraft({
      yearly,
      monthly,
      yearlyCsv: pointsToCsv(yearly),
      monthlyCsv: pointsToCsv(monthly),
    });
    setSaveError(null);
  };

  const updateChartDraftPoints = (
    chartType: ChartType,
    updater: (points: WeeklyReportChartPoint[], chart: WeeklyReportSingleChart) => WeeklyReportChartPoint[],
  ) => {
    setChartDraft(prev => {
      if (!prev) return prev;
      const csvKey = chartType === 'yearly' ? 'yearlyCsv' : 'monthlyCsv';
      const chart = prev[chartType];
      const currentPoints = csvToPoints(prev[csvKey], chart);
      const nextChart = { ...chart, points: updater(currentPoints, chart) };
      return {
        ...prev,
        [chartType]: nextChart,
        [csvKey]: pointsToCsv(nextChart),
      };
    });
  };

  const updateChartCell = (
    chartType: ChartType,
    rowIndex: number,
    key: string,
    value: string,
  ) => {
    updateChartDraftPoints(chartType, points => points.map((point, index) => {
      if (index !== rowIndex) return point;
      if (key === 'period' || key === 'label') {
        return { ...point, [key]: value };
      }
      return { ...point, [key]: value.trim() === '' ? null : Number(value) };
    }));
  };

  const addChartRow = (chartType: ChartType) => {
    updateChartDraftPoints(chartType, (points, chart) => {
      const last = points[points.length - 1];
      const next: WeeklyReportChartPoint = {
        period: last?.period ? String(last.period) : '',
        label: last?.label ? String(last.label) : '',
      };
      chart.series.forEach(series => {
        next[series.key] = last?.[series.key] ?? null;
      });
      return [...points, next];
    });
  };

  const removeChartRow = (chartType: ChartType, rowIndex: number) => {
    updateChartDraftPoints(chartType, points => points.filter((_, index) => index !== rowIndex));
  };

  const validateChart = (chart: WeeklyReportSingleChart): string | null => {
    if (!chart.points?.length) return language === 'en' ? 'Each chart needs at least one row.' : '每个图表至少需要一行数据。';
    const hasNumeric = chart.points.some(point => chart.series.some(series => Number.isFinite(Number(point[series.key]))));
    if (!hasNumeric) return language === 'en' ? 'Each chart needs at least one numeric value.' : '每个图表至少需要一个有效数值。';
    const badValue = chart.points.some(point => chart.series.some(series => {
      const value = point[series.key];
      return value !== null && value !== undefined && value !== '' && !Number.isFinite(Number(value));
    }));
    if (badValue) return language === 'en' ? 'Numeric cells must be blank or valid numbers.' : '数值单元格必须为空或有效数字。';
    return null;
  };

  const handleSaveChartConfig = async () => {
    if (!selected || !configuringSection || !chartDraft) return;
    const reportId = String(selected.id || '');
    if (!reportId) return;
    setSaving(true);
    setSaveError(null);
    try {
      const yearly = { ...chartDraft.yearly, points: csvToPoints(chartDraft.yearlyCsv, chartDraft.yearly) };
      const monthly = { ...chartDraft.monthly, points: csvToPoints(chartDraft.monthlyCsv, chartDraft.monthly) };
      const validationError = validateChart(yearly) || validateChart(monthly);
      if (validationError) {
        setSaveError(validationError);
        return;
      }
      await api.updateWeeklyReportChartData(reportId, configuringSection.slug, 'yearly', yearly);
      await api.updateWeeklyReportChartData(reportId, configuringSection.slug, 'monthly', monthly);
      const regeneratedText = buildRegeneratedText(configuringSection, yearly, monthly, language === 'en');
      const nextMarkdown = replaceProductSectionText(rawMarkdown, configuringSection, regeneratedText, language === 'en');
      await api.updateWeeklyReport(reportId, { title: selected.title, markdown: nextMarkdown });
      const nextContext = {
        ...selected.context,
        weekly_chart_payload: {
          ...(selected.context?.weekly_chart_payload ?? {}),
          products: {
            ...productCharts,
            [configuringSection.slug]: {
              ...productCharts[configuringSection.slug],
              yearly,
              monthly,
            },
          },
        },
      };
      applyUpdatedReport({ ...selected, markdown: nextMarkdown, context: nextContext });
      setConfiguringSection(null);
      setChartDraft(null);
    } catch (e: any) {
      setSaveError(`保存并重新生成失败: ${e?.message ?? '未知错误'}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <div className="flex flex-1 overflow-hidden">
        {/* ── Left: report list ─────────────────────────────────────── */}
        <div className="w-64 flex-shrink-0 border-r border-slate-200 flex flex-col bg-white">
          <div className="p-3 border-b border-slate-200 flex items-center justify-between">
            <span className="text-xs font-semibold text-slate-700">周报列表</span>
            <button
              onClick={loadReports}
              disabled={loading}
              title="刷新"
              className="text-[11px] text-blue-600 hover:text-blue-800 disabled:opacity-40"
            >
              {loading ? '加载中…' : '刷新'}
            </button>
          </div>

          <div className="p-2 border-b border-slate-100">
            <button
              onClick={handleGenerate}
              disabled={generating}
              className="w-full py-1.5 px-2 text-xs font-medium rounded border border-blue-300 text-blue-700 bg-blue-50 hover:bg-blue-100 disabled:opacity-50 transition-colors"
            >
              {generating ? '正在生成…' : '生成本周报告'}
            </button>
            {error && <p className="text-[10px] text-red-600 mt-1 px-1">{error}</p>}
          </div>

          <div className="flex-1 overflow-y-auto">
            {loading && reports.length === 0 ? (
              <div className="p-4 text-center text-xs text-slate-400">加载中…</div>
            ) : reports.length === 0 ? (
              <div className="p-4 text-center text-xs text-slate-400">暂无周报，点击上方"生成本周报告"</div>
            ) : (
              reports.map(r => (
                <ReportListItem
                  key={r.id}
                  report={r}
                  isSelected={selected?.id === r.id}
                  onSelect={(rep) => { setSelected(rep); setIsEditing(false); }}
                  onDelete={handleDelete}
                  deleting={deletingId === String(r.id)}
                />
              ))
            )}
          </div>
        </div>

        {/* ── Right: viewer / editor ────────────────────────────────── */}
        <div className="flex-1 flex flex-col overflow-hidden bg-slate-50">

          {/* Toolbar */}
          {selected && (
            <div className="flex-shrink-0 flex items-center justify-between px-4 py-2 border-b border-slate-200 bg-white gap-3">
              {isEditing ? (
                <>
                  <span className="text-xs text-slate-500 font-medium">编辑模式 — 左侧编辑，右侧实时预览</span>
                  <div className="flex items-center gap-2">
                    {saveError && <span className="text-[11px] text-red-600">{saveError}</span>}
                    <button
                      onClick={handleCancelEdit}
                      disabled={saving}
                      className="px-3 py-1 text-xs rounded border border-slate-300 text-slate-600 hover:bg-slate-100 disabled:opacity-40"
                    >
                      取消
                    </button>
                    <button
                      onClick={handleSave}
                      disabled={saving}
                      className="px-3 py-1 text-xs rounded border border-green-400 bg-green-50 text-green-700 hover:bg-green-100 disabled:opacity-40 font-medium"
                    >
                      {saving ? '保存中…' : '保存'}
                    </button>
                  </div>
                </>
              ) : (
                <>
                  <span className="text-xs text-slate-500 truncate max-w-xs">{selected.title}</span>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={handleStartEdit}
                      className="flex items-center gap-1 px-3 py-1 text-xs rounded border border-slate-300 text-slate-600 hover:bg-slate-100 font-medium"
                    >
                      <svg xmlns="http://www.w3.org/2000/svg" className="h-3.5 w-3.5" viewBox="0 0 20 20" fill="currentColor">
                        <path d="M13.586 3.586a2 2 0 112.828 2.828l-.793.793-2.828-2.828.793-.793zM11.379 5.793L3 14.172V17h2.828l8.38-8.379-2.83-2.828z" />
                      </svg>
                      编辑报告
                    </button>
                  </div>
                </>
              )}
            </div>
          )}

          {/* Content area */}
          <div className="flex-1 overflow-hidden flex">

            {isEditing ? (
              /* ── Split edit panel ── */
              <>
                {/* Left: editor */}
                <div className="w-1/2 flex flex-col border-r border-slate-200 bg-white overflow-hidden">
                  <div className="flex-shrink-0 px-4 pt-3 pb-2 border-b border-slate-100">
                    <label className="text-[11px] text-slate-500 font-medium uppercase tracking-wide">标题</label>
                    <input
                      type="text"
                      value={editTitle}
                      onChange={e => setEditTitle(e.target.value)}
                      className="mt-1 w-full px-2 py-1.5 text-sm font-semibold border border-slate-200 rounded focus:outline-none focus:ring-1 focus:ring-blue-400"
                      placeholder="报告标题"
                    />
                  </div>
                  <div className="flex-shrink-0 px-4 pt-2 pb-1">
                    <label className="text-[11px] text-slate-500 font-medium uppercase tracking-wide">Markdown 内容</label>
                    <p className="text-[10px] text-slate-400 mt-0.5">支持 Markdown 格式。图片链接格式: <code>![alt](url)</code></p>
                  </div>
                  <textarea
                    ref={textareaRef}
                    value={editMarkdown}
                    onChange={e => setEditMarkdown(e.target.value)}
                    className="flex-1 mx-4 mb-4 p-3 text-xs font-mono border border-slate-200 rounded resize-none focus:outline-none focus:ring-1 focus:ring-blue-400 bg-slate-50 overflow-y-auto"
                    spellCheck={false}
                    placeholder="在此输入 Markdown 内容…"
                  />
                </div>

                {/* Right: live preview */}
                <div className="w-1/2 overflow-y-auto p-4 bg-slate-50">
                  <div className="sticky top-0 bg-slate-50 pb-2 mb-2 border-b border-slate-100">
                    <span className="text-[11px] text-slate-400 font-medium uppercase tracking-wide">实时预览</span>
                  </div>
                  <article className="prose prose-sm max-w-none bg-white rounded-lg shadow-sm p-5">
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm]}
                      components={{ img: ImageComponent }}
                    >
                      {previewMarkdown}
                    </ReactMarkdown>
                  </article>
                </div>
              </>
            ) : (
              /* ── View mode ── */
              <div className="flex-1 overflow-y-auto p-6">
                {generating && (
                  <div className="flex items-center gap-2 text-sm text-blue-600 mb-4">
                    <svg className="animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                    </svg>
                    正在生成周报，请稍候（约 1–2 分钟）…
                  </div>
                )}

                {!selected && !generating && (
                  <div className="text-center text-slate-400 text-sm mt-20">
                    请从左侧选择一份周报，或点击"生成本周报告"
                  </div>
                )}

                {selected && markdown && canRenderStructuredReport && (
                  <article className="max-w-none rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
                    <section className="mb-6 border-b border-slate-100 pb-4">
                      <h2 className="flex items-center gap-2 text-lg font-bold text-slate-900">
                        <span className="h-5 w-1 rounded-full bg-indigo-600" />
                        {language === 'en' ? '2. Product Analysis' : '2. 产品分析'}
                      </h2>
                    </section>

                    <div className="space-y-8">
                      {productSections.map(section => {
                        const productChart = productCharts[section.slug];
                        const displayText = resolveDisplayText(section, productChart, language === 'en');
                        return (
                          <section key={section.slug} className="border-b border-slate-100 pb-7 last:border-b-0 last:pb-0">
                            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                              <h3 className="flex items-center gap-2 text-base font-bold text-slate-900">
                                <span className="h-2.5 w-2.5 rounded-full bg-indigo-600" />
                                {section.index}. {section.title}
                              </h3>
                              <div className="flex items-center gap-2">
                                <button
                                  type="button"
                                  onClick={() => handleStartSectionTextEdit(section)}
                                  className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 shadow-sm hover:bg-slate-50"
                                >
                                  <svg className="h-3.5 w-3.5" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                                    <path d="M4 3a2 2 0 00-2 2v10a2 2 0 002 2h7.5a1 1 0 100-2H4V5h12v4.5a1 1 0 102 0V5a2 2 0 00-2-2H4z" />
                                    <path d="M14.586 10.586a2 2 0 112.828 2.828l-3.621 3.621a1 1 0 01-.45.263l-2 .5a1 1 0 01-1.212-1.212l.5-2a1 1 0 01.263-.45l3.692-3.55z" />
                                  </svg>
                                  {language === 'en' ? 'Edit Text' : '编辑文字'}
                                </button>
                                <button
                                  type="button"
                                  onClick={() => handleStartConfigure(section)}
                                  className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 shadow-sm hover:bg-slate-50"
                                >
                                  <svg className="h-3.5 w-3.5" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                                    <path fillRule="evenodd" d="M11.49 3.17c-.38-1.56-2.6-1.56-2.98 0a1.53 1.53 0 01-2.29.95c-1.37-.84-2.94.73-2.1 2.1.54.89.06 2.05-.95 2.29-1.56.38-1.56 2.6 0 2.98 1.01.24 1.49 1.4.95 2.29-.84 1.37.73 2.94 2.1 2.1.89-.54 2.05-.06 2.29.95.38 1.56 2.6 1.56 2.98 0 .24-1.01 1.4-1.49 2.29-.95 1.37.84 2.94-.73 2.1-2.1-.54-.89-.06-2.05.95-2.29 1.56-.38 1.56-2.6 0-2.98-1.01-.24-1.49-1.4-.95-2.29.84-1.37-.73-2.94-2.1-2.1-.89.54-2.05.06-2.29-.95zM10 13a3 3 0 100-6 3 3 0 000 6z" clipRule="evenodd" />
                                  </svg>
                                  {language === 'en' ? 'Configure' : '配置'}
                                </button>
                              </div>
                            </div>

                            <div className="mb-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
                              <DirectWeeklyReportChart chart={productChart?.yearly} productName={productChart?.product_name_en || section.title} language={language} />
                              <DirectWeeklyReportChart chart={productChart?.monthly} productName={productChart?.product_name_en || section.title} language={language} />
                            </div>

                            <div className="space-y-3 text-sm leading-relaxed text-slate-700">
                              {section.priceSnapshot && (
                                <div>
                                  <p className="mb-1 font-semibold text-slate-700">{language === 'en' ? 'Price Snapshot:' : '价格快照：'}</p>
                                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{section.priceSnapshot}</ReactMarkdown>
                                </div>
                              )}
                              <p>
                                <span className="font-semibold text-slate-900">{language === 'en' ? 'Market Analysis:' : '市场分析：'} </span>
                                {displayText.marketAnalysis}
                              </p>
                              <p>
                                <span className="bg-blue-100 font-semibold text-slate-900">{language === 'en' ? 'Forecast:' : '预测：'}</span>{' '}
                                {displayText.forecast}
                              </p>
                              {(displayText.confidence || section.source) && (
                                <p className="rounded bg-slate-50 px-3 py-2 text-xs italic text-slate-500">
                                  {displayText.confidence && `“${language === 'en' ? 'Confidence' : '置信度'}: ${displayText.confidence}`}
                                  {displayText.confidence && section.source && '   '}
                                  {section.source && `${language === 'en' ? 'Source' : '预测来源'}: ${section.source}`}
                                  {displayText.confidence && '”'}
                                </p>
                              )}
                            </div>
                          </section>
                        );
                      })}
                    </div>
                  </article>
                )}

                {selected && markdown && !canRenderStructuredReport && (
                  <article className="prose prose-sm max-w-none bg-white rounded-lg shadow-sm p-6">
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm]}
                      components={{ img: ImageComponent }}
                    >
                      {markdown}
                    </ReactMarkdown>
                  </article>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {textEditingSection && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 px-4">
          <div className="flex max-h-[88vh] w-full max-w-xl flex-col overflow-hidden rounded-xl bg-white shadow-2xl">
            <div className="flex items-start justify-between border-b border-slate-100 px-5 py-4">
              <div>
                <h3 className="text-lg font-bold text-slate-900">{language === 'en' ? 'Edit Text' : '编辑文字'}</h3>
                <p className="text-sm text-slate-500">{textEditingSection.title}</p>
              </div>
              <button type="button" onClick={() => setTextEditingSection(null)} className="text-2xl leading-none text-slate-400 hover:text-slate-600">×</button>
            </div>
            <div className="flex-1 space-y-4 overflow-y-auto px-5 py-5">
              <label className="block">
                <span className="mb-1 block text-sm font-semibold text-slate-700">{language === 'en' ? 'Market Analysis' : '市场分析'}</span>
                <textarea
                  value={textDraft.marketAnalysis}
                  onChange={event => setTextDraft(prev => ({ ...prev, marketAnalysis: event.target.value }))}
                  className="h-28 w-full resize-y rounded-lg border border-slate-200 px-3 py-2 text-sm leading-relaxed text-slate-700 outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100"
                />
              </label>
              <label className="block">
                <span className="mb-1 block text-sm font-semibold text-slate-700">{language === 'en' ? 'Forecast' : '预测'}</span>
                <textarea
                  value={textDraft.forecast}
                  onChange={event => setTextDraft(prev => ({ ...prev, forecast: event.target.value }))}
                  className="h-24 w-full resize-y rounded-lg border border-slate-200 px-3 py-2 text-sm leading-relaxed text-slate-700 outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100"
                />
              </label>
              <label className="block">
                <span className="mb-1 block text-sm font-semibold text-slate-700">{language === 'en' ? 'Confidence' : '置信度'}</span>
                <input
                  value={textDraft.confidence}
                  onChange={event => setTextDraft(prev => ({ ...prev, confidence: event.target.value }))}
                  className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100"
                  placeholder={language === 'en' ? 'Low (2/5)' : '低 (2/5)'}
                />
              </label>
              {saveError && <p className="text-sm text-red-600">{saveError}</p>}
            </div>
            <div className="flex justify-end gap-2 border-t border-slate-100 bg-slate-50 px-5 py-4">
              <button type="button" onClick={() => setTextEditingSection(null)} disabled={saving} className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-50">
                {language === 'en' ? 'Cancel' : '取消'}
              </button>
              <button type="button" onClick={handleSaveSectionText} disabled={saving} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50">
                {saving ? (language === 'en' ? 'Saving...' : '保存中...') : (language === 'en' ? 'Save' : '保存')}
              </button>
            </div>
          </div>
        </div>
      )}

      {configuringSection && chartDraft && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 px-4">
          <div className="flex max-h-[94vh] w-full max-w-6xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl">
            <div className="flex items-start justify-between border-b border-slate-100 px-5 py-4">
              <div>
                <h3 className="text-lg font-bold text-slate-900">{language === 'en' ? 'Configure Product Section' : '配置产品板块'}</h3>
                <p className="text-sm text-slate-500">{configuringSection.title}</p>
                <p className="mt-1 text-xs text-slate-400">
                  {language === 'en'
                    ? 'Edit cells like a spreadsheet, preview figures live, then regenerate charts and this product analysis.'
                    : '像表格一样修改数据，实时预览图形，然后重新生成图表和该产品分析。'}
                </p>
              </div>
              <button type="button" onClick={() => { setConfiguringSection(null); setChartDraft(null); }} className="text-2xl leading-none text-slate-400 hover:text-slate-600">×</button>
            </div>
            <div className="flex-1 space-y-5 overflow-y-auto bg-slate-50 px-5 py-5">
              {(['yearly', 'monthly'] as const).map(chartType => {
                const rawChart = chartDraft[chartType];
                const csvKey = chartType === 'yearly' ? 'yearlyCsv' : 'monthlyCsv';
                const chart = editableChartFromCsv(rawChart, chartDraft[csvKey]);
                const summary = chartSummary(chart);
                return (
                  <section key={chartType} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                    <div className="mb-3 flex items-center justify-between gap-3">
                      <div>
                        <h4 className="text-sm font-bold text-slate-800">
                          {chartType === 'yearly'
                            ? (language === 'en' ? 'Figure 1 — 12-Month Price Trend' : '图 1 — 12个月价格走势')
                            : (language === 'en' ? 'Figure 2 — Daily Prices' : '图 2 — 日价格走势')}
                        </h4>
                        <p className="text-xs text-slate-400">{chart.title}</p>
                      </div>
                      <div className="flex flex-wrap items-center justify-end gap-2 text-xs">
                        <span className="rounded-full bg-indigo-50 px-2 py-1 font-medium text-indigo-700">{summary.rows} rows</span>
                        <span className="rounded-full bg-slate-100 px-2 py-1 text-slate-600">{language === 'en' ? 'Min' : '最小'} {formatCompactNumber(summary.min)}</span>
                        <span className="rounded-full bg-slate-100 px-2 py-1 text-slate-600">{language === 'en' ? 'Max' : '最大'} {formatCompactNumber(summary.max)}</span>
                      </div>
                    </div>
                    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(360px,0.8fr)]">
                      <div className="space-y-4">
                        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                          <label className="block">
                            <span className="mb-1 block text-xs font-semibold text-slate-500">{language === 'en' ? 'Title' : '标题'}</span>
                            <input
                              value={chart.title || ''}
                              onChange={event => setChartDraft(prev => prev ? ({ ...prev, [chartType]: { ...prev[chartType], title: event.target.value } }) : prev)}
                              className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100"
                            />
                          </label>
                          <label className="block">
                            <span className="mb-1 block text-xs font-semibold text-slate-500">{language === 'en' ? 'Unit' : '单位'}</span>
                            <input
                              value={chart.unit || ''}
                              onChange={event => setChartDraft(prev => prev ? ({ ...prev, [chartType]: { ...prev[chartType], unit: event.target.value } }) : prev)}
                              className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100"
                            />
                          </label>
                        </div>
                        <div className="space-y-2">
                          <span className="block text-xs font-semibold text-slate-500">{language === 'en' ? 'Series' : '数据系列'}</span>
                          {chart.series.map((series, index) => (
                            <div key={`${chartType}-${series.key}`} className="grid grid-cols-[1fr_1fr_100px] gap-2">
                              <input value={series.key} readOnly className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-500" />
                              <input
                                value={series.label}
                                onChange={event => setChartDraft(prev => {
                                  if (!prev) return prev;
                                  const nextSeries = [...prev[chartType].series];
                                  nextSeries[index] = { ...nextSeries[index], label: event.target.value };
                                  const nextChart = { ...prev[chartType], series: nextSeries };
                                  return { ...prev, [chartType]: nextChart, [csvKey]: pointsToCsv(nextChart) };
                                })}
                                className="rounded-lg border border-slate-200 px-3 py-2 text-xs outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100"
                              />
                              <input
                                value={series.color}
                                onChange={event => setChartDraft(prev => {
                                  if (!prev) return prev;
                                  const nextSeries = [...prev[chartType].series];
                                  nextSeries[index] = { ...nextSeries[index], color: event.target.value };
                                  const nextChart = { ...prev[chartType], series: nextSeries };
                                  return { ...prev, [chartType]: nextChart, [csvKey]: pointsToCsv(nextChart) };
                                })}
                                className="rounded-lg border border-slate-200 px-3 py-2 text-xs outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100"
                              />
                            </div>
                          ))}
                        </div>
                        <div>
                          <div className="mb-2 flex items-center justify-between">
                            <span className="text-xs font-semibold text-slate-500">{language === 'en' ? 'Editable Data' : '可编辑数据'}</span>
                            <button
                              type="button"
                              onClick={() => addChartRow(chartType)}
                              className="rounded-lg border border-indigo-200 bg-indigo-50 px-2.5 py-1 text-xs font-semibold text-indigo-700 hover:bg-indigo-100"
                            >
                              {language === 'en' ? '+ Add row' : '+ 添加行'}
                            </button>
                          </div>
                          <div className="max-h-64 overflow-auto rounded-xl border border-slate-200">
                            <table className="min-w-full text-left text-xs">
                              <thead className="sticky top-0 bg-slate-100 text-slate-500">
                                <tr>
                                  <th className="px-2 py-2 font-semibold">period</th>
                                  <th className="px-2 py-2 font-semibold">label</th>
                                  {chart.series.map(series => <th key={series.key} className="px-2 py-2 font-semibold">{series.key}</th>)}
                                  <th className="px-2 py-2 text-right font-semibold">{language === 'en' ? 'Action' : '操作'}</th>
                                </tr>
                              </thead>
                              <tbody className="divide-y divide-slate-100 bg-white">
                                {chart.points.map((point, rowIndex) => (
                                  <tr key={`${chartType}-${rowIndex}`}>
                                    <td className="px-2 py-1.5">
                                      <input value={String(point.period || '')} onChange={event => updateChartCell(chartType, rowIndex, 'period', event.target.value)} className="w-24 rounded border border-slate-200 px-2 py-1 outline-none focus:border-indigo-400" />
                                    </td>
                                    <td className="px-2 py-1.5">
                                      <input value={String(point.label || '')} onChange={event => updateChartCell(chartType, rowIndex, 'label', event.target.value)} className="w-24 rounded border border-slate-200 px-2 py-1 outline-none focus:border-indigo-400" />
                                    </td>
                                    {chart.series.map(series => (
                                      <td key={series.key} className="px-2 py-1.5">
                                        <input
                                          value={point[series.key] == null ? '' : String(point[series.key])}
                                          onChange={event => updateChartCell(chartType, rowIndex, series.key, event.target.value)}
                                          className="w-28 rounded border border-slate-200 px-2 py-1 font-mono outline-none focus:border-indigo-400"
                                        />
                                      </td>
                                    ))}
                                    <td className="px-2 py-1.5 text-right">
                                      <button type="button" onClick={() => removeChartRow(chartType, rowIndex)} className="rounded px-2 py-1 text-xs font-medium text-red-600 hover:bg-red-50">
                                        {language === 'en' ? 'Delete' : '删除'}
                                      </button>
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </div>
                        <details className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                          <summary className="cursor-pointer text-xs font-semibold text-slate-600">{language === 'en' ? 'Bulk CSV editor' : '批量 CSV 编辑'}</summary>
                          <textarea
                            value={chartDraft[csvKey]}
                            onChange={event => setChartDraft(prev => prev ? ({ ...prev, [csvKey]: event.target.value }) : prev)}
                            className="mt-3 h-32 w-full resize-y rounded-lg border border-slate-200 bg-white px-3 py-2 font-mono text-xs leading-relaxed text-slate-700 outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100"
                            spellCheck={false}
                          />
                        </details>
                      </div>
                      <div className="space-y-2">
                        <span className="block text-xs font-semibold text-slate-500">{language === 'en' ? 'Live Preview' : '实时预览'}</span>
                        <DirectWeeklyReportChart chart={chart} productName={configuringSection.title} language={language} />
                        <p className="text-[11px] text-slate-400">
                          {language === 'en'
                            ? 'The preview uses your unsaved edits. Save & Regenerate updates the PNG, report context, and product analysis text.'
                            : '预览会使用尚未保存的编辑。保存并重新生成后，会更新 PNG、报告上下文和产品分析文字。'}
                        </p>
                      </div>
                    </div>
                  </section>
                );
              })}
              {saveError && <p className="text-sm text-red-600">{saveError}</p>}
            </div>
            <div className="flex justify-end gap-2 border-t border-slate-100 bg-slate-50 px-5 py-4">
              <button type="button" onClick={() => { setConfiguringSection(null); setChartDraft(null); }} disabled={saving} className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-50">
                {language === 'en' ? 'Cancel' : '取消'}
              </button>
              <button type="button" onClick={handleSaveChartConfig} disabled={saving} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50">
                {saving ? (language === 'en' ? 'Regenerating...' : '重新生成中...') : (language === 'en' ? 'Save & Regenerate' : '保存并重新生成')}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
};
