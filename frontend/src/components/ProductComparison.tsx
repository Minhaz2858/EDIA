import React, { useEffect, useState, useMemo } from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip, ResponsiveContainer } from 'recharts';
import { ArrowUp, ArrowDown, RefreshCw, ChevronDown, ChevronUp, TrendingUp, BarChart3, Activity, Lightbulb } from 'lucide-react';
import { API_BASE_URL, fetchWithRetry } from '../services/api';
import { getChartTheme, rechartsTooltipStyle } from '../lib/chartTheme';
import { useLanguage } from '../lib/language';
import { useTheme } from '../lib/theme';
import { formatSignedPriceChange, getPriceChangeArrow, getPriceChangeLineColor, getPriceChangeTextClass } from '../lib/priceChange';

interface ComparisonProduct {
  id: string;
  name: string;
  name_en: string;
  price: number;
  change_pct: number;
  high: number;
  low: number;
  volume: number | null;
  unit: string;
  sparkline: number[];
  history: { date: string; price: number }[];
}

const PRODUCT_COLORS: Record<string, string> = {
  crude_oil: '#3b82f6',
  naphtha: '#ef4444',
  cracked_c5: '#8b5cf6',
  cracked_c9: '#06b6d4',
  isoprene: '#ec4899',
  piperylene: '#f97316',
  dcpd: '#10b981',
  raffinate_c5: '#6366f1',
  sis: '#0ea5e9',
  c5_resin: '#e11d48',
  c9_resin: '#a855f7',
  blowing_agent: '#14b8a6',
  styrene: '#f59e0b',
  hydrogenated_c9: '#84cc16',
  trimethylbenzene: '#64748b',
};

const DEFAULT_COLORS = [
  '#3b82f6', '#ef4444', '#10b981', '#f59e0b', '#8b5cf6',
  '#ec4899', '#06b6d4', '#f97316', '#84cc16', '#e11d48',
  '#0ea5e9', '#14b8a6', '#6366f1', '#a855f7', '#64748b',
];

function getColor(id: string, idx: number): string {
  return PRODUCT_COLORS[id] || DEFAULT_COLORS[idx % DEFAULT_COLORS.length];
}

function formatDateTick(value: unknown): string {
  if (typeof value !== 'string') return '';
  return value.length > 5 ? value.slice(5) : value;
}

/* Tiny inline SVG sparkline */
const Sparkline: React.FC<{ data: number[]; color: string; width?: number; height?: number }> = ({
  data, color, width = 80, height = 28,
}) => {
  if (!data.length) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const points = data
    .map((v, i) => {
      const x = (i / (data.length - 1)) * width;
      const y = height - ((v - min) / range) * (height - 4) - 2;
      return `${x},${y}`;
    })
    .join(' ');
  return (
    <svg width={width} height={height} className="inline-block">
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

export const ProductComparison: React.FC = () => {
  const { language, locale } = useLanguage();
  const { isDark } = useTheme();
  const chart = useMemo(() => getChartTheme(isDark), [isDark]);
  const tooltipStyle = useMemo(
    () => ({ ...rechartsTooltipStyle(chart, isDark), maxHeight: '300px', overflowY: 'auto' as const }),
    [chart, isDark],
  );
  const text = language === 'en'
    ? {
      loading: 'Loading product comparison data...',
      title: 'Product Price Comparison',
      subtitle: 'Select products to compare their price trends.',
      selected: 'Selected',
      items: 'items',
      selectAll: 'Select all',
      clearAll: 'Clear all',
      pick: 'Pick',
      productName: 'Product',
      latestPrice: 'Latest Price (CNY/t)',
      changePct: 'Change',
      trend12w: '12W Trend',
      highLow: 'High / Low',
      volume: 'Volume',
      trendChart: 'Comparison Trend',
      daysLabel: (days: 7 | 15 | 30 | 365) => (days === 365 ? '1Y' : `${days}D`),
      benchmarkHint: 'Indexed to the first visible price point at 100',
      rawHint: 'Showing raw prices (CNY/t)',
      benchmarkOn: '✓ Indexed Mode',
      benchmarkOff: 'Toggle Indexed Mode',
      analysisOn: '✓ Comparative Analysis',
      analysisOff: 'Comparative Analysis',
      selectOne: 'Select at least one product to view the comparison chart.',
      dateLabel: 'Date',
      indexAxis: 'Index (base=100)',
      reportTitle: 'Comparative Analysis Report',
      summaryStats: 'Summary Metrics',
      priceRankTitle: 'Price rank',
      volatilityRankTitle: 'Volatility rank',
      currentPrice: 'Latest',
      averagePrice: 'Average',
      highest: 'High',
      lowest: 'Low',
      volatility: 'Volatility',
      correlation: 'Price Correlation',
      insights: 'Insights',
      strong: 'Strong',
      medium: 'Medium',
      weak: 'Weak',
      notAvailable: 'N/A',
      correlationSummary: (a: string, b: string, strength: string, value: number, takeaway: string) =>
        `${a} and ${b} show the ${strength.toLowerCase()} correlation in the selected window (${value >= 0 ? '+' : ''}${value.toFixed(2)}), which suggests ${takeaway}.`,
      highestLowestSummary: (highName: string, highPrice: number, lowName: string, lowPrice: number, spread: number) =>
        `💰 ${highName} has the highest latest price at ${highPrice.toLocaleString(locale)} CNY/t, while ${lowName} is the lowest at ${lowPrice.toLocaleString(locale)} CNY/t. The spread is ${spread.toLocaleString(locale)} CNY/t.`,
      performersSummary: (bestName: string, bestPct: number, worstName: string, worstPct: number) =>
        `📈 ${bestName} is the strongest mover at ${bestPct > 0 ? '+' : ''}${bestPct.toFixed(2)}%, while ${worstName} is the weakest at ${worstPct > 0 ? '+' : ''}${worstPct.toFixed(2)}%.`,
      volatilitySummary: (highName: string, highVol: number, lowName: string, lowVol: number) =>
        `📊 ${highName} is the most volatile at ${highVol.toFixed(1)}%, while ${lowName} is the most stable at ${lowVol.toFixed(1)}%.`,
      wideRangeSummary: (names: string[]) =>
        `⚠️ ${names.join(', ')} show materially wider trading ranges than the group average and deserve closer monitoring.`,
      consistent: 'their price moves are moving together',
      opposite: 'their price moves are moving in opposite directions',
      limited: 'the relationship is limited',
    }
    : {
      loading: '加载产品数据...',
      title: '产品价格对比',
      subtitle: '勾选产品查看对比走势图',
      selected: '已选',
      items: '个',
      selectAll: '全选',
      clearAll: '取消全选',
      pick: '选',
      productName: '产品名称',
      latestPrice: '最新价 (元/吨)',
      changePct: '涨跌幅',
      trend12w: '12周走势',
      highLow: '最高 / 最低',
      volume: '成交量',
      trendChart: '对比走势图',
      daysLabel: (days: 7 | 15 | 30 | 365) => (days === 365 ? '1年' : `${days}天`),
      benchmarkHint: '以首周价格为基准100，显示相对变化',
      rawHint: '显示原始价格(元/吨)',
      benchmarkOn: '✓ 基准化模式',
      benchmarkOff: '切换基准化',
      analysisOn: '✓ 对比分析',
      analysisOff: '对比分析',
      selectOne: '请至少选择一个产品以查看对比走势图',
      dateLabel: '日期',
      indexAxis: '指数 (基期=100)',
      reportTitle: '对比分析报告',
      summaryStats: '综合指标对比',
      priceRankTitle: '价格排名',
      volatilityRankTitle: '波动排名',
      currentPrice: '最新价',
      averagePrice: '均价',
      highest: '最高',
      lowest: '最低',
      volatility: '波动率',
      correlation: '价格关联性',
      insights: '智能洞察',
      strong: '强',
      medium: '中等',
      weak: '弱',
      notAvailable: 'N/A',
      correlationSummary: (a: string, b: string, strength: string, value: number, takeaway: string) =>
        `🔗 ${a} 和 ${b} 价格相关性${strength}（相关系数 ${value.toFixed(2)}），表明${takeaway}。`,
      highestLowestSummary: (highName: string, highPrice: number, lowName: string, lowPrice: number, spread: number) =>
        `💰 最新价格最高的产品是 ${highName}（${highPrice.toLocaleString(locale)} 元/吨），最低的是 ${lowName}（${lowPrice.toLocaleString(locale)} 元/吨），价差为 ${spread.toLocaleString(locale)} 元/吨。`,
      performersSummary: (bestName: string, bestPct: number, worstName: string, worstPct: number) =>
        `📈 涨幅最大: ${bestName}（${bestPct > 0 ? '+' : ''}${bestPct.toFixed(2)}%），跌幅最大: ${worstName}（${worstPct > 0 ? '+' : ''}${worstPct.toFixed(2)}%）。`,
      volatilitySummary: (highName: string, highVol: number, lowName: string, lowVol: number) =>
        `📊 波动率最高: ${highName}（${highVol.toFixed(1)}%），最稳定: ${lowName}（${lowVol.toFixed(1)}%）。`,
      wideRangeSummary: (names: string[]) =>
        `⚠️ ${names.join('、')} 的价格波动幅度显著高于平均水平，建议重点关注。`,
      consistent: '两者价格走势较为一致',
      opposite: '两者价格走势呈现反向',
      limited: '两者价格走势关联较弱',
    };
  const displayName = (product: ComparisonProduct) => (language === 'en' ? product.name_en || product.name : product.name);
  const [products, setProducts] = useState<ComparisonProduct[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [benchmarking, setBenchmarking] = useState(false);
  const [timeRange, setTimeRange] = useState<7 | 15 | 30 | 365>(365);
  const [loading, setLoading] = useState(true);
  const [sortField, setSortField] = useState<'name' | 'price' | 'change_pct' | 'volume'>('name');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');
  const [showAnalysis, setShowAnalysis] = useState(false);

  const loadData = async () => {
    setLoading(true);
    try {
      const res = await fetchWithRetry(`${API_BASE_URL}/api/v2/market/comparison`);
      const data: ComparisonProduct[] = await res.json();
      setProducts(data);
      // Default: select all
      if (selected.size === 0) {
        setSelected(new Set(data.map(p => p.id)));
      }
    } catch (e) {
      console.error('Failed to load comparison data', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadData(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const toggleProduct = (id: string) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectAll = () => setSelected(new Set(products.map(p => p.id)));
  const deselectAll = () => setSelected(new Set());

  const handleSort = (field: typeof sortField) => {
    if (sortField === field) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortDir('asc');
    }
  };

  const sortedProducts = useMemo(() => {
    const copy = [...products];
    copy.sort((a, b) => {
      let cmp = 0;
      if (sortField === 'name') cmp = displayName(a).localeCompare(displayName(b), locale);
      else if (sortField === 'price') cmp = a.price - b.price;
      else if (sortField === 'change_pct') cmp = a.change_pct - b.change_pct;
      else if (sortField === 'volume') cmp = (a.volume ?? 0) - (b.volume ?? 0);
      return sortDir === 'asc' ? cmp : -cmp;
    });
    return copy;
  }, [products, sortField, sortDir, locale]);

  // Compute cutoff date from timeRange
  const cutoffDate = useMemo(() => {
    const d = new Date();
    d.setDate(d.getDate() - timeRange);
    return d.toISOString().slice(0, 10);
  }, [timeRange]);

  // Build merged chart data from selected products
  const chartData = useMemo(() => {
    const selectedProducts = products.filter(p => selected.has(p.id));
    if (!selectedProducts.length) return [];

    // Collect all unique dates within range
    const dateSet = new Set<string>();
    selectedProducts.forEach(p =>
      p.history.forEach(h => {
        if (h.date >= cutoffDate) dateSet.add(h.date);
      }),
    );
    const allDates = Array.from(dateSet).sort();

    // Build price lookup
    const priceMaps = new Map<string, Map<string, number>>();
    selectedProducts.forEach(p => {
      const m = new Map<string, number>();
      p.history.forEach(h => m.set(h.date, h.price));
      priceMaps.set(p.id, m);
    });

    // First price in visible window per product for benchmarking
    const firstPrices = new Map<string, number>();
    if (benchmarking && allDates.length > 0) {
      selectedProducts.forEach(p => {
        for (const d of allDates) {
          const v = priceMaps.get(p.id)?.get(d);
          if (v && v > 0) { firstPrices.set(p.id, v); break; }
        }
      });
    }

    return allDates.map(date => {
      const point: Record<string, string | number | null> = { date };
      selectedProducts.forEach(p => {
        const raw = priceMaps.get(p.id)?.get(date);
        if (raw != null) {
          if (benchmarking) {
            const base = firstPrices.get(p.id);
            point[p.id] = base ? Math.round((raw / base) * 10000) / 100 : null;
          } else {
            point[p.id] = raw;
          }
        }
      });
      return point;
    });
  }, [products, selected, benchmarking, cutoffDate]);

  const sortIndicator = (field: typeof sortField) => {
    if (sortField !== field) return <span className="text-slate-300 ml-0.5">↕</span>;
    return <span className="text-blue-500 ml-0.5">{sortDir === 'asc' ? '↑' : '↓'}</span>;
  };

  /* ── Comparative analysis computations ── */
  const selectedProducts = useMemo(
    () => products.filter(p => selected.has(p.id)),
    [products, selected],
  );

  interface ProductStats {
    id: string;
    name: string;
    price: number;
    change_pct: number;
    high: number;
    low: number;
    range: number;
    volatility: number;           // (high-low)/avg * 100
    avgPrice: number;
    color: string;
    filteredHistory: number[];    // prices within time range
  }

  const analysisStats = useMemo<ProductStats[]>(() => {
    return selectedProducts.map((p, idx) => {
      const prices = p.history
        .filter(h => h.date >= cutoffDate)
        .map(h => h.price)
        .filter(v => v > 0);
      const avg = prices.length ? prices.reduce((s, v) => s + v, 0) / prices.length : p.price;
      const hi = prices.length ? Math.max(...prices) : p.high;
      const lo = prices.length ? Math.min(...prices) : p.low;
      const range = hi - lo;
      return {
        id: p.id,
        name: displayName(p),
        price: p.price,
        change_pct: p.change_pct,
        high: hi,
        low: lo,
        range,
        volatility: avg > 0 ? (range / avg) * 100 : 0,
        avgPrice: avg,
        color: getColor(p.id, products.indexOf(p)),
        filteredHistory: prices,
      };
    });
  }, [selectedProducts, cutoffDate, products]);

  // Pearson correlation between two arrays
  const pearson = (a: number[], b: number[]): number | null => {
    const n = Math.min(a.length, b.length);
    if (n < 3) return null;
    const x = a.slice(0, n), y = b.slice(0, n);
    const mx = x.reduce((s, v) => s + v, 0) / n;
    const my = y.reduce((s, v) => s + v, 0) / n;
    let num = 0, dx = 0, dy = 0;
    for (let i = 0; i < n; i++) {
      const xi = x[i] - mx, yi = y[i] - my;
      num += xi * yi;
      dx += xi * xi;
      dy += yi * yi;
    }
    const denom = Math.sqrt(dx * dy);
    return denom > 0 ? num / denom : null;
  };

  const correlationMatrix = useMemo(() => {
    if (analysisStats.length < 2) return [];
    const result: { a: string; b: string; aName: string; bName: string; r: number | null }[] = [];
    for (let i = 0; i < analysisStats.length; i++) {
      for (let j = i + 1; j < analysisStats.length; j++) {
        result.push({
          a: analysisStats[i].id,
          b: analysisStats[j].id,
          aName: analysisStats[i].name,
          bName: analysisStats[j].name,
          r: pearson(analysisStats[i].filteredHistory, analysisStats[j].filteredHistory),
        });
      }
    }
    return result.sort((a, b) => Math.abs(b.r ?? 0) - Math.abs(a.r ?? 0));
  }, [analysisStats]);

  const insights = useMemo<string[]>(() => {
    if (analysisStats.length < 2) return [];
    const lines: string[] = [];

    // price ranking
    const byPrice = [...analysisStats].sort((a, b) => b.price - a.price);
    lines.push(text.highestLowestSummary(
      byPrice[0].name,
      byPrice[0].price,
      byPrice[byPrice.length - 1].name,
      byPrice[byPrice.length - 1].price,
      byPrice[0].price - byPrice[byPrice.length - 1].price,
    ));

    // best/worst performer
    const byPerf = [...analysisStats].sort((a, b) => b.change_pct - a.change_pct);
    const best = byPerf[0];
    const worst = byPerf[byPerf.length - 1];
    lines.push(text.performersSummary(best.name, best.change_pct, worst.name, worst.change_pct));

    // volatility
    const byVol = [...analysisStats].sort((a, b) => b.volatility - a.volatility);
    lines.push(text.volatilitySummary(byVol[0].name, byVol[0].volatility, byVol[byVol.length - 1].name, byVol[byVol.length - 1].volatility));

    // correlation insight
    if (correlationMatrix.length > 0) {
      const top = correlationMatrix[0];
      if (top.r !== null) {
        const strength = Math.abs(top.r) >= 0.8 ? text.strong : Math.abs(top.r) >= 0.5 ? text.medium : text.weak;
        const takeaway = top.r >= 0.5 ? text.consistent : top.r <= -0.5 ? text.opposite : text.limited;
        lines.push(text.correlationSummary(top.aName, top.bName, strength, top.r, takeaway));
      }
    }

    // price range comparison
    const avgRange = analysisStats.reduce((s, p) => s + p.range, 0) / analysisStats.length;
    const wideRange = analysisStats.filter(p => p.range > avgRange * 1.5);
    if (wideRange.length > 0) {
      lines.push(text.wideRangeSummary(wideRange.map(p => p.name)));
    }

    return lines;
  }, [analysisStats, correlationMatrix, text, locale]);

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center bg-white">
        <RefreshCw className="w-5 h-5 animate-spin text-slate-400" />
        <span className="ml-2 text-sm text-slate-500">{text.loading}</span>
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-col bg-slate-100 lg:h-[calc(100vh-108px)]">
      {/* Header */}
      <div className="bg-white border-b border-slate-200 px-4 py-3 flex items-center justify-between flex-shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-base font-bold text-slate-800">📊 {text.title}</span>
          <span className="text-xs text-slate-400">{text.subtitle}</span>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-slate-500">
            {text.selected} <span className="text-blue-600 font-bold">{selected.size}</span> / {products.length} {text.items}
          </span>
          <button onClick={selectAll} className="text-xs text-blue-500 hover:underline">{text.selectAll}</button>
          <button onClick={deselectAll} className="text-xs text-blue-500 hover:underline">{text.clearAll}</button>
          <button onClick={loadData} className="p-1 rounded hover:bg-slate-100">
            <RefreshCw className="w-3.5 h-3.5 text-slate-400" />
          </button>
        </div>
      </div>

      {/* Main body: LEFT column (table + chart) | RIGHT column (analysis panel) */}
      <div className="flex min-h-0 flex-col lg:flex-1 lg:flex-row">

        {/* ── LEFT COLUMN ── */}
        <div className={`flex min-h-0 flex-col ${showAnalysis && selected.size >= 2 ? 'w-full lg:flex-1 lg:min-w-0' : 'w-full'}`}>

          {/* Product Table — full height, no scroll cap */}
          <div className="bg-white border-b border-slate-200 overflow-x-auto flex-shrink-0">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 sticky top-0 z-10">
                <tr className="text-xs text-slate-500 border-b border-slate-200">
                  <th className="px-3 py-2 w-10 text-left">{text.pick}</th>
                  <th
                    className="px-3 py-2 text-left cursor-pointer hover:text-blue-600 select-none"
                    onClick={() => handleSort('name')}
                  >
                    {text.productName} {sortIndicator('name')}
                  </th>
                  <th
                    className="px-3 py-2 text-right cursor-pointer hover:text-blue-600 select-none"
                    onClick={() => handleSort('price')}
                  >
                    {text.latestPrice} {sortIndicator('price')}
                  </th>
                  <th
                    className="px-3 py-2 text-right cursor-pointer hover:text-blue-600 select-none"
                    onClick={() => handleSort('change_pct')}
                  >
                    {text.changePct} {sortIndicator('change_pct')}
                  </th>
                  <th className="px-3 py-2 text-center">{text.trend12w}</th>
                  <th className="px-3 py-2 text-right">{text.highLow}</th>
                  <th
                    className="px-3 py-2 text-right cursor-pointer hover:text-blue-600 select-none"
                    onClick={() => handleSort('volume')}
                  >
                    {text.volume} {sortIndicator('volume')}
                  </th>
                </tr>
              </thead>
              <tbody>
                {sortedProducts.map((p, idx) => {
                  const color = getColor(p.id, idx);
                  const isSelected = selected.has(p.id);
                  return (
                    <tr
                      key={p.id}
                      className={`border-b border-slate-100 transition-colors hover:bg-blue-50/50 ${isSelected ? 'bg-white' : 'bg-slate-50/30'}`}
                    >
                      <td className="px-3 py-2">
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => toggleProduct(p.id)}
                          className="w-4 h-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500 cursor-pointer"
                        />
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex items-center gap-2">
                          <span
                            className="w-2.5 h-2.5 rounded-full inline-block flex-shrink-0"
                            style={{ backgroundColor: color }}
                          />
                          <span className="font-medium text-slate-700">{displayName(p)}</span>
                        </div>
                      </td>
                      <td className="px-3 py-2 text-right font-mono font-semibold text-slate-800">
                        {p.price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                      </td>
                      <td className="px-3 py-2 text-right">
                        <span className={`inline-flex items-center gap-0.5 font-mono font-semibold ${getPriceChangeTextClass(p.change_pct)}`}>
                          {getPriceChangeArrow(p.change_pct) === 'up' ? (
                            <ArrowUp className="w-3 h-3" />
                          ) : getPriceChangeArrow(p.change_pct) === 'down' ? (
                            <ArrowDown className="w-3 h-3" />
                          ) : null}
                          {formatSignedPriceChange(p.change_pct, { minimumFractionDigits: 2, maximumFractionDigits: 2 }, locale, '%')}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-center">
                        <Sparkline data={p.sparkline} color={getPriceChangeLineColor(p.change_pct)} />
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-xs text-slate-600">
                        {p.high.toLocaleString(undefined, { minimumFractionDigits: 2 })} / {p.low.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-xs text-slate-500">
                        {p.volume != null ? `${Math.round(p.volume / 1000)}K` : '--'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Chart area */}
          <div className="flex min-h-0 flex-col">
            {/* Chart Legend Row */}
            <div className="bg-white border-b border-slate-200 px-4 py-2 flex items-center justify-between flex-wrap gap-2 flex-shrink-0">
              <div className="flex items-center gap-3 flex-wrap">
                <span className="text-xs font-bold text-slate-600">对比走势图</span>
                <span className="text-xs font-bold text-slate-600">{text.trendChart}</span>
                {products.filter(p => selected.has(p.id)).map((p) => (
                  <span key={p.id} className="flex items-center gap-1 text-[11px] text-slate-600">
                    <span className="inline-block w-4 h-0.5" style={{ backgroundColor: getColor(p.id, products.indexOf(p)) }} />
                    {displayName(p)}
                  </span>
                ))}
              </div>
              <div className="flex items-center gap-3">
                {/* Time range selector */}
                <div className="flex items-center rounded border border-slate-300 bg-white p-0.5 text-[11px]">
                  {([7, 15, 30, 365] as const).map((days) => (
                    <button
                      key={days}
                      onClick={() => setTimeRange(days)}
                      className={`rounded px-2 py-0.5 transition-colors ${timeRange === days ? 'bg-blue-100 text-blue-700' : 'text-slate-500 hover:bg-slate-100'}`}
                    >
                      {text.daysLabel(days)}
                    </button>
                  ))}
                </div>
                <span className="text-[11px] text-slate-400">
                  {benchmarking ? text.benchmarkHint : text.rawHint}
                </span>
                <button
                  onClick={() => setBenchmarking(v => !v)}
                  className={`px-3 py-1 rounded text-xs font-medium transition-colors border ${benchmarking
                    ? 'bg-blue-600 text-white border-blue-600'
                    : 'bg-white text-slate-600 border-slate-300 hover:bg-slate-50'
                    }`}
                >
                  {benchmarking ? text.benchmarkOn : text.benchmarkOff}
                </button>
                {/* Analysis toggle button */}
                {selected.size >= 2 && (
                  <button
                    onClick={() => setShowAnalysis(v => !v)}
                    className={`px-3 py-1 rounded text-xs font-medium transition-colors border flex items-center gap-1.5 ${showAnalysis
                      ? 'bg-blue-600 text-white border-blue-600'
                      : 'bg-white text-blue-600 border-blue-300 hover:bg-blue-50'
                      }`}
                  >
                    <BarChart3 className="w-3.5 h-3.5" />
                    {showAnalysis ? text.analysisOn : text.analysisOff}
                  </button>
                )}
              </div>
            </div>

            {/* Comparison Chart */}
            <div className="h-[320px] edia-panel border border-slate-200 rounded-lg p-4 sm:h-[380px] lg:h-[420px] lg:flex-shrink-0">
              {selected.size === 0 ? (
                <div className="flex items-center justify-center h-full text-slate-400 text-sm">
                  {text.selectOne}
                </div>
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={chartData}>
                    <CartesianGrid strokeDasharray="3 3" stroke={chart.grid} vertical={false} />
                    <XAxis
                      dataKey="date"
                      tick={{ fontSize: 10, fill: chart.tick }}
                      tickFormatter={(val) => formatDateTick(val)}
                      axisLine={{ stroke: chart.axis }}
                      tickLine={false}
                      minTickGap={40}
                    />
                    <YAxis
                      orientation="right"
                      tick={{ fontSize: 10, fill: chart.tick }}
                      axisLine={false}
                      tickLine={false}
                      tickCount={8}
                      domain={benchmarking ? ['auto', 'auto'] : ['auto', 'auto']}
                      tickFormatter={(val) =>
                        benchmarking
                          ? `${val}`
                          : val >= 1000
                            ? `${(val / 1000).toFixed(1)}K`
                            : `${val}`
                      }
                    />
                    <RechartsTooltip
                      contentStyle={tooltipStyle}
                      formatter={(value: number, name: string) => {
                        const product = products.find(p => p.id === name);
                        const label = product ? displayName(product) : name;
                        if (benchmarking) {
                          return [`${value}`, label];
                        }
                        return [typeof value === 'number' ? value.toLocaleString(locale) : value, label];
                      }}
                      labelFormatter={(label) => `${text.dateLabel}: ${label}`}
                    />
                    {products
                      .filter(p => selected.has(p.id))
                      .map((p) => (
                        <Line
                          key={p.id}
                          type="monotone"
                          dataKey={p.id}
                          stroke={getColor(p.id, products.indexOf(p))}
                          strokeWidth={1.5}
                          dot={false}
                          connectNulls
                          name={p.id}
                        />
                      ))}
                  </LineChart>
                </ResponsiveContainer>
              )}
              {/* Y-axis Label */}
              {benchmarking && selected.size > 0 && (
                <div className="absolute right-6 top-1/2 -translate-y-1/2 text-[10px] text-slate-400 writing-mode-vertical transform rotate-90 origin-center pointer-events-none">
                  {text.indexAxis}
                </div>
              )}
            </div>
            {/* end chart */}
          </div>
          {/* end chart area wrapper */}
        </div>
        {/* end left column */}

        {/* ── RIGHT COLUMN: Analysis Panel ── */}
        {showAnalysis && selected.size >= 2 && (
          <div className="flex w-full flex-col border-t border-slate-200 bg-slate-50 lg:h-full lg:min-h-0 lg:w-[320px] lg:flex-shrink-0 lg:border-l lg:border-t-0 xl:w-[380px]">
            {/* Panel Header */}
            <div className="z-10 flex items-center justify-between border-b border-slate-200 bg-white px-4 py-3 lg:sticky lg:top-0">
              <div className="flex items-center gap-2">
                <BarChart3 className="w-4 h-4 text-blue-600" />
                <h3 className="text-sm font-bold text-slate-700">{text.reportTitle}</h3>
              </div>
              <button
                onClick={() => setShowAnalysis(false)}
                className="p-1 rounded hover:bg-slate-100 text-slate-400 hover:text-slate-600"
              >
                <ChevronDown className="w-4 h-4 rotate-90" />
              </button>
            </div>

            <div className="lg:min-h-0 lg:flex-1 lg:overflow-y-auto lg:overscroll-contain">

              {/* Section 1: Summary Statistics (vertical cards instead of wide table) */}
              <div className="p-3">
                <div className="flex items-center gap-2 mb-2">
                  <TrendingUp className="w-3.5 h-3.5 text-blue-600" />
                  <h4 className="text-xs font-bold text-slate-700">{text.summaryStats}</h4>
                </div>
                <div className="space-y-2">
                  {(() => {
                    const priceRank = [...analysisStats].sort((a, b) => b.price - a.price).map(p => p.id);
                    const volRank = [...analysisStats].sort((a, b) => b.volatility - a.volatility).map(p => p.id);
                    return analysisStats.map((s) => (
                      <div key={s.id} className="bg-white rounded-lg border border-slate-200 p-3">
                        {/* Product header */}
                        <div className="flex items-center justify-between mb-2">
                          <div className="flex items-center gap-1.5">
                            <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: s.color }} />
                            <span className="text-xs font-semibold text-slate-700">{s.name}</span>
                          </div>
                          <div className="flex items-center gap-1">
                            <span className={`inline-flex items-center justify-center w-5 h-5 rounded-full text-[9px] font-bold ${priceRank.indexOf(s.id) === 0 ? 'bg-amber-100 text-amber-700' : 'bg-slate-100 text-slate-500'}`}
                              title={text.priceRankTitle}
                            >
                              {priceRank.indexOf(s.id) + 1}
                            </span>
                            <span className={`inline-flex items-center justify-center w-5 h-5 rounded-full text-[9px] font-bold ${volRank.indexOf(s.id) === 0 ? 'bg-red-100 text-red-700' : 'bg-slate-100 text-slate-500'}`}
                              title={text.volatilityRankTitle}
                            >
                              {volRank.indexOf(s.id) + 1}
                            </span>
                          </div>
                        </div>
                        {/* Metrics grid */}
                        <div className="grid grid-cols-3 gap-x-3 gap-y-1 text-[11px]">
                          <div>
                            <span className="text-slate-400">{text.currentPrice}</span>
                            <div className="font-mono font-semibold text-slate-800">{s.price.toLocaleString(locale, { minimumFractionDigits: 2 })}</div>
                          </div>
                          <div>
                            <span className="text-slate-400">{text.averagePrice}</span>
                            <div className="font-mono text-slate-600">{s.avgPrice.toLocaleString(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
                          </div>
                          <div>
                            <span className="text-slate-400">{text.changePct}</span>
                            <div className={`font-mono font-semibold flex items-center gap-0.5 ${getPriceChangeTextClass(s.change_pct)}`}>
                              {getPriceChangeArrow(s.change_pct) === 'up' ? <ArrowUp className="w-3 h-3" /> : getPriceChangeArrow(s.change_pct) === 'down' ? <ArrowDown className="w-3 h-3" /> : null}
                              {formatSignedPriceChange(s.change_pct, { minimumFractionDigits: 2, maximumFractionDigits: 2 }, locale, '%')}
                            </div>
                          </div>
                          <div>
                            <span className="text-slate-400">{text.highest}</span>
                            <div className="font-mono text-green-600">{s.high.toLocaleString(locale, { minimumFractionDigits: 2 })}</div>
                          </div>
                          <div>
                            <span className="text-slate-400">{text.lowest}</span>
                            <div className="font-mono text-red-500">{s.low.toLocaleString(locale, { minimumFractionDigits: 2 })}</div>
                          </div>
                          <div>
                            <span className="text-slate-400">{text.volatility}</span>
                            <div className={`font-mono font-semibold ${s.volatility > 10 ? 'text-red-600' : s.volatility > 5 ? 'text-amber-600' : 'text-green-600'}`}>
                              {s.volatility.toFixed(1)}%
                            </div>
                          </div>
                        </div>
                      </div>
                    ));
                  })()}
                </div>
              </div>

              {/* Section 2: Correlation Matrix */}
              {correlationMatrix.length > 0 && (
                <div className="px-3 pb-3">
                  <div className="flex items-center gap-2 mb-2">
                    <Activity className="w-3.5 h-3.5 text-blue-600" />
                    <h4 className="text-xs font-bold text-slate-700">{text.correlation}</h4>
                  </div>
                  <div className="space-y-2">
                    {correlationMatrix.map(({ aName, bName, r }, idx) => {
                      const val = r ?? 0;
                      const absVal = Math.abs(val);
                      const bgColor = val >= 0.7 ? 'bg-green-50 border-green-200'
                        : val >= 0.3 ? 'bg-blue-50 border-blue-200'
                          : val >= -0.3 ? 'bg-slate-50 border-slate-200'
                            : val >= -0.7 ? 'bg-amber-50 border-amber-200'
                              : 'bg-red-50 border-red-200';
                      const textColor = val >= 0.7 ? 'text-green-700'
                        : val >= 0.3 ? 'text-blue-700'
                          : val >= -0.3 ? 'text-slate-600'
                            : val >= -0.7 ? 'text-amber-700'
                              : 'text-red-700';
                      const label = absVal >= 0.8 ? text.strong : absVal >= 0.5 ? text.medium : text.weak;
                      return (
                        <div key={idx} className={`rounded-lg border p-2.5 ${bgColor}`}>
                          <div className="flex items-center justify-between">
                            <span className="text-[11px] text-slate-500 truncate flex-1" title={`${aName} vs ${bName}`}>
                              {aName} ↔ {bName}
                            </span>
                            <div className="flex items-center gap-2 ml-2">
                              <span className={`text-sm font-bold font-mono ${textColor}`}>
                                {r !== null ? (r >= 0 ? '+' : '') + r.toFixed(2) : text.notAvailable}
                              </span>
                              <span className={`text-[9px] font-medium px-1.5 py-0.5 rounded ${textColor} bg-white/50`}>
                                {label}
                              </span>
                            </div>
                          </div>
                          <div className="mt-1.5 h-1 bg-slate-200 rounded-full overflow-hidden">
                            <div
                              className={`h-full rounded-full transition-all ${val >= 0 ? 'bg-green-400' : 'bg-red-400'}`}
                              style={{ width: `${absVal * 100}%` }}
                            />
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Section 3: Smart Insights */}
              {insights.length > 0 && (
                <div className="px-3 pb-3">
                  <div className="flex items-center gap-2 mb-2">
                    <Lightbulb className="w-3.5 h-3.5 text-amber-500" />
                    <h4 className="text-xs font-bold text-slate-700">{text.insights}</h4>
                  </div>
                  <div className="bg-white rounded-lg border border-slate-200 p-3 space-y-2">
                    {insights.map((line, i) => (
                      <p key={i} className="text-[11px] text-slate-600 leading-relaxed">{line}</p>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
        {/* end main body flex row */}
      </div>
      {/* end root */}
    </div>
  );
};
