import {
  ArrowDown,
  ArrowUp,
  ChevronLeft,
  Download,
  File,
  FileText,
  Image as ImageIcon,
  RefreshCw
} from 'lucide-react';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  Tooltip as RechartsTooltip,
  ReferenceLine,
  ResponsiveContainer,
  XAxis, YAxis,
} from 'recharts';
import { getChartTheme, rechartsTooltipStyle } from '../lib/chartTheme';
import { useTheme } from '../lib/theme';
import { API_BASE_URL, fetchWithRetry } from '../services/api';
import { exportWholeReport } from '../utils/reportExport';

interface SalesReport {
  month: string;
  available_months: string[];
  available_materials: string[];
  org_name: string;
  summary: {
    total_sales_qty: number;
    total_sales_amount_wan: number;
    sales_qty_yoy: number;
    sales_amount_yoy: number;
  };
  customer_share: { name: string; value: number }[];
  product_growth: { product: string; qty: number; unit: string; yoy: number }[];
  weeks: string[];
  price_chart: Record<string, string | number | null>[];
  sales_chart: Record<string, string | number | null>[];
  sales_detail: {
    product: string;
    customer: string;
    weeks: { week: string; qty: number; price: number }[];
  }[];
  products: string[];
}

const PIE_COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#6b7280'];
const REPORT_ORGS = [
  { id: 'huizhou', name: '惠州伊斯科 (Huizhou Isco)' },
  { id: 'guangdong', name: '广东伊斯科 (Guangdong Isco)' },
];
const DEFAULT_REPORT_ORG = 'huizhou';

/* ── Three-dot Module Menu ── */
interface ModuleMenuProps {
  moduleRef: React.RefObject<HTMLDivElement | null>;
  title?: string;
  onDrillDown?: () => void;
  excelData?: Record<string, unknown>[];
  hasDrillDown?: boolean;
}

const ModuleMenu: React.FC<ModuleMenuProps> = () => null;

/* ── Drill-Down Detail Modal ── */
interface DrillDownProps {
  title: string;
  onBack: () => void;
  children: React.ReactNode;
}

const DrillDownView: React.FC<DrillDownProps> = ({ title, onBack, children }) => (
  <div className="flex-1 overflow-auto bg-slate-50 p-5">
    <div className="flex items-center gap-3 mb-4">
      <button onClick={onBack}
        className="flex items-center gap-1 px-3 py-1.5 bg-white border border-slate-300 rounded-lg text-sm text-slate-600 hover:bg-slate-50 shadow-sm">
        <ChevronLeft className="w-4 h-4" /> 返回上级
      </button>
      <h2 className="text-lg font-bold text-slate-800">{title}</h2>
    </div>
    {children}
  </div>
);

const YoYBadge: React.FC<{ value: number }> = ({ value }) => {
  if (value === 0) return <span className="text-xs text-slate-400">同比持平</span>;
  const isUp = value > 0;
  return (
    <span className={`inline-flex items-center gap-0.5 text-xs font-medium ${isUp ? 'text-red-500' : 'text-green-600'}`}>
      同比{isUp ? '增长' : '下降'}{Math.abs(value)}%
      {isUp ? <ArrowUp className="w-3 h-3" /> : <ArrowDown className="w-3 h-3" />}
    </span>
  );
};

export const CustomerSalesReport: React.FC = () => {
  const { isDark } = useTheme();
  const chart = useMemo(() => getChartTheme(isDark), [isDark]);
  const tooltipStyle = useMemo(
    () => rechartsTooltipStyle(chart, isDark),
    [chart, isDark],
  );
  const chartLabel = useMemo(
    () => (props: { x?: number; y?: number; value?: number }) => {
      if (!props.value || props.value === 0) return null;
      return (
        <text x={props.x} y={(props.y ?? 0) - 10} textAnchor="middle" fill={chart.tick} fontSize={9}>
          {Math.round(props.value)}
        </text>
      );
    },
    [chart.tick],
  );
  const [data, setData] = useState<SalesReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedOrg, setSelectedOrg] = useState(DEFAULT_REPORT_ORG);
  const [selectedMonth, setSelectedMonth] = useState('');
  const [selectedMaterial, setSelectedMaterial] = useState('');
  const [focusProduct, setFocusProduct] = useState('');
  const [lastUpdate, setLastUpdate] = useState('');
  const [drillDown, setDrillDown] = useState<string | null>(null);
  const kpiQtyRef = useRef<HTMLDivElement>(null);
  const kpiAmtRef = useRef<HTMLDivElement>(null);
  const priceChartRef = useRef<HTMLDivElement>(null);
  const pieRef = useRef<HTMLDivElement>(null);
  const growthRef = useRef<HTMLDivElement>(null);
  const salesChartRef = useRef<HTMLDivElement>(null);
  const salesDetailRef = useRef<HTMLDivElement>(null);
  const summaryRef = useRef<HTMLDivElement>(null);
  const reportRootRef = useRef<HTMLDivElement>(null);
  const exportMenuRef = useRef<HTMLDivElement>(null);
  const [exportOpen, setExportOpen] = useState(false);
  const [isExportingReport, setIsExportingReport] = useState(false);

  const loadData = async (month?: string, material?: string, orgId: string = DEFAULT_REPORT_ORG) => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      params.set('org', orgId);
      if (month) params.set('month', month);
      if (material) params.set('material', material);
      const res = await fetchWithRetry(`${API_BASE_URL}/api/v2/reports/customer-sales?${params}`);
      const json = await res.json();
      setData(json);
      if (!month) setSelectedMonth(json.month);
      setLastUpdate(new Date().toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }));
    } catch (e) {
      console.error('Failed to load customer sales report', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadData(undefined, undefined, DEFAULT_REPORT_ORG); }, []);

  useEffect(() => {
    if (!exportOpen) return;
    const handler = (e: MouseEvent) => {
      if (exportMenuRef.current && !exportMenuRef.current.contains(e.target as Node)) {
        setExportOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [exportOpen]);

  const handleQuery = () => loadData(selectedMonth, selectedMaterial, selectedOrg);
  const handleOrgChange = (orgId: string) => {
    setSelectedOrg(orgId);
    loadData(selectedMonth || data?.month, selectedMaterial, orgId);
  };
  const handleReset = () => {
    setSelectedOrg(DEFAULT_REPORT_ORG);
    setSelectedMaterial('');
    setSelectedMonth('');
    loadData(undefined, undefined, DEFAULT_REPORT_ORG);
  };

  const handleExportWholeReport = async (format: 'image' | 'pdf' | 'word') => {
    const el = reportRootRef.current;
    if (!el) return;
    setIsExportingReport(true);
    setExportOpen(false);
    try {
      await exportWholeReport(
        el,
        `customer-sales-report-${data.month || Date.now()}`,
        format,
      );
    } catch (e) {
      console.error('Failed to export whole report', e);
    } finally {
      setIsExportingReport(false);
    }
  };

  const productColors = useMemo(() => {
    if (!data) return {};
    const colors: Record<string, string> = {};
    const palette = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899', '#14b8a6', '#f97316', '#6366f1', '#84cc16'];
    data.products.forEach((p, i) => { colors[p] = palette[i % palette.length]; });
    return colors;
  }, [data]);

  const maxQty = useMemo(() => {
    if (!data) return 1;
    return Math.max(...data.product_growth.map(p => p.qty), 1);
  }, [data]);

  const monthLabel = useMemo(() => {
    if (!data) return '';
    try {
      const [y, m] = data.month.split('-');
      return `${y}年${parseInt(m)}月`;
    } catch { return data.month; }
  }, [data]);

  const defaultChartProducts = useMemo(() => {
    if (!data || !data.products.length) return [];
    const latest = data.sales_chart[data.sales_chart.length - 1] || {};
    const valueOf = (value: unknown) => (typeof value === 'number' ? value : Number(value) || 0);
    return [...data.products]
      .sort((a, b) => valueOf(latest[b]) - valueOf(latest[a]))
      .slice(0, Math.min(6, data.products.length));
  }, [data]);

  const visibleChartProducts = useMemo(() => {
    if (!data) return [];
    if (focusProduct && data.products.includes(focusProduct)) {
      return [focusProduct];
    }
    return defaultChartProducts;
  }, [data, focusProduct, defaultChartProducts]);

  useEffect(() => {
    if (!focusProduct || !data) return;
    if (!data.products.includes(focusProduct)) {
      setFocusProduct('');
    }
  }, [data, focusProduct]);

  const avgPrice = useMemo(() => {
    if (!data || !data.price_chart.length || !visibleChartProducts.length) return 0;
    const allVals = data.price_chart.flatMap(pt =>
      visibleChartProducts.map(p => pt[p]).filter((v): v is number => typeof v === 'number' && v > 0)
    );
    return allVals.length > 0 ? Math.round(allVals.reduce((a, b) => a + b, 0) / allVals.length) : 0;
  }, [data, visibleChartProducts]);

  const avgSalesQty = useMemo(() => {
    if (!data || !data.sales_chart.length || !visibleChartProducts.length) return 0;
    const allVals = data.sales_chart.flatMap(pt =>
      visibleChartProducts.map(p => pt[p]).filter((v): v is number => typeof v === 'number' && v > 0)
    );
    return allVals.length > 0 ? Math.round(allVals.reduce((a, b) => a + b, 0) / allVals.length) : 0;
  }, [data, visibleChartProducts]);

  const priceInsight = useMemo(() => {
    if (!data || !data.price_chart.length || !data.products.length) return '';
    const insightProducts = focusProduct ? [focusProduct] : data.products;
    let bestProduct = '', bestCount = 0;
    for (const p of insightProducts) {
      const count = data.price_chart.filter(pt => typeof pt[p] === 'number' && (pt[p] as number) > 0).length;
      if (count > bestCount) { bestCount = count; bestProduct = p; }
    }
    if (!bestProduct || bestCount < 2) return '';
    const vals = data.price_chart
      .map(pt => ({ week: pt.week as string, val: pt[bestProduct] as number }))
      .filter(v => typeof v.val === 'number' && v.val > 0);
    if (vals.length < 2) return '';
    const first = vals[0], last = vals[vals.length - 1];
    const peak = vals.reduce((a, b) => b.val > a.val ? b : a);
    const trough = vals.reduce((a, b) => b.val < a.val ? b : a);
    const peakPct = first.val > 0 ? ((peak.val - first.val) / first.val * 100).toFixed(1) : '0';
    const overallPct = first.val > 0 ? Math.abs((last.val - first.val) / first.val * 100).toFixed(1) : '0';
    if (peak.week === last.week) {
      return `${bestProduct}产品含税单价呈现上涨趋势，从${first.week}的${first.val.toLocaleString()}元/吨上涨至${last.week}的${last.val.toLocaleString()}元/吨（涨幅${overallPct}%），反映该产品可能存在短期供需紧张或原料成本上涨压力。`;
    } else if (trough.week === last.week) {
      return `${bestProduct}产品含税单价呈现下行趋势，从${first.week}的${first.val.toLocaleString()}元/吨下跌至${last.week}的${last.val.toLocaleString()}元/吨（跌幅${overallPct}%），反映该产品可能面临市场需求回落或竞争加剧。`;
    }
    return `${bestProduct}产品含税单价呈现明显波动趋势，从${first.week}的${first.val.toLocaleString()}元/吨攀升至${peak.week}的${peak.val.toLocaleString()}元/吨峰值（涨幅${peakPct}%），随后${last.val < peak.val ? '回落' : '回升'}至${last.val.toLocaleString()}元/吨，反映该产品在中旬可能存在短期供需紧张或原料成本上涨压力。`;
  }, [data, focusProduct]);

  const salesInsight = useMemo(() => {
    if (!data || !data.sales_chart.length || !data.products.length) return '';
    const insightProducts = focusProduct ? [focusProduct] : data.products;
    let bestProduct = '', bestCount = 0;
    for (const p of insightProducts) {
      const count = data.sales_chart.filter(pt => typeof pt[p] === 'number' && (pt[p] as number) > 0).length;
      if (count > bestCount) { bestCount = count; bestProduct = p; }
    }
    if (!bestProduct || bestCount < 2) return '';
    const vals = data.sales_chart
      .map(pt => ({ week: pt.week as string, val: pt[bestProduct] as number }))
      .filter(v => typeof v.val === 'number' && v.val > 0);
    if (vals.length < 2) return '';
    const avg = Math.round(vals.reduce((a, b) => a + b.val, 0) / vals.length);
    const max = vals.reduce((a, b) => b.val > a.val ? b : a);
    const min = vals.reduce((a, b) => b.val < a.val ? b : a);
    return `${bestProduct}产品销售量在${monthLabel}呈现波动趋势，周均销量${avg}吨，最高达${Math.round(max.val)}吨（${max.week}），最低为${Math.round(min.val)}吨（${min.week}），需关注销量波动对库存管理和生产计划的影响。`;
  }, [data, monthLabel, focusProduct]);

  if (loading && !data) {
    return (
      <div className="flex-1 flex items-center justify-center text-slate-400">
        <div className="animate-spin w-6 h-6 border-2 border-blue-500 border-t-transparent rounded-full mr-2" />
        加载中...
      </div>
    );
  }
  if (!data) return <div className="flex-1 flex items-center justify-center text-slate-400">无数据</div>;

  const totalShare = data.customer_share.reduce((s, c) => s + c.value, 0);

  const DRILL_TITLES: Record<string, string> = {
    kpi: 'KPI 总览',
    price_chart: '含税单价详情',
    customer_share: '客户销售量占比',
    growth: '销售量增长率',
    sales_chart: '每周销量详情',
    sales_detail: '销售情况明细',
    summary: '总结分析',
  };

  if (drillDown) {
    return (
      <DrillDownView title={DRILL_TITLES[drillDown] ?? drillDown} onBack={() => setDrillDown(null)}>
        <div className="bg-white rounded-lg border border-slate-200 overflow-hidden">
          {drillDown === 'kpi' && (
            <table className="w-full text-xs border-collapse">
              <thead>
                <tr className="bg-slate-50 border-b border-slate-200">
                  <th className="text-left px-4 py-2.5 font-semibold text-slate-600">指标</th>
                  <th className="text-right px-4 py-2.5 font-semibold text-slate-600">数值</th>
                  <th className="text-right px-4 py-2.5 font-semibold text-slate-600">单位</th>
                  <th className="text-right px-4 py-2.5 font-semibold text-slate-600">同比变化</th>
                </tr>
              </thead>
              <tbody>
                <tr className="border-b border-slate-100">
                  <td className="px-4 py-2.5">总销售量</td>
                  <td className="px-4 py-2.5 text-right font-mono">{data.summary.total_sales_qty.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                  <td className="px-4 py-2.5 text-right">吨</td>
                  <td className={`px-4 py-2.5 text-right font-medium ${data.summary.sales_qty_yoy >= 0 ? 'text-green-600' : 'text-red-600'}`}>{data.summary.sales_qty_yoy >= 0 ? '+' : ''}{data.summary.sales_qty_yoy}%</td>
                </tr>
                <tr className="bg-slate-50/50">
                  <td className="px-4 py-2.5">总销售额（发货口径）</td>
                  <td className="px-4 py-2.5 text-right font-mono">{data.summary.total_sales_amount_wan.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                  <td className="px-4 py-2.5 text-right">万元</td>
                  <td className={`px-4 py-2.5 text-right font-medium ${data.summary.sales_amount_yoy >= 0 ? 'text-green-600' : 'text-red-600'}`}>{data.summary.sales_amount_yoy >= 0 ? '+' : ''}{data.summary.sales_amount_yoy}%</td>
                </tr>
              </tbody>
            </table>
          )}
          {drillDown === 'customer_share' && (
            <table className="w-full text-xs border-collapse">
              <thead>
                <tr className="bg-slate-50 border-b border-slate-200">
                  <th className="text-left px-4 py-2.5 font-semibold text-slate-600">客户名称</th>
                  <th className="text-right px-4 py-2.5 font-semibold text-slate-600">销售量（吨）</th>
                  <th className="text-right px-4 py-2.5 font-semibold text-slate-600">占比 (%)</th>
                </tr>
              </thead>
              <tbody>
                {data.customer_share.map((c, i) => (
                  <tr key={i} className={`border-b border-slate-100 ${i % 2 === 0 ? 'bg-white' : 'bg-slate-50/50'}`}>
                    <td className="px-4 py-2">{c.name}</td>
                    <td className="px-4 py-2 text-right font-mono">{c.value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                    <td className="px-4 py-2 text-right">{totalShare > 0 ? ((c.value / totalShare) * 100).toFixed(1) : 0}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {drillDown === 'growth' && (
            <table className="w-full text-xs border-collapse">
              <thead>
                <tr className="bg-slate-50 border-b border-slate-200">
                  <th className="text-left px-4 py-2.5 font-semibold text-slate-600">产品名称</th>
                  <th className="text-right px-4 py-2.5 font-semibold text-slate-600">销售量</th>
                  <th className="text-right px-4 py-2.5 font-semibold text-slate-600">单位</th>
                  <th className="text-right px-4 py-2.5 font-semibold text-slate-600">同比变化 (%)</th>
                </tr>
              </thead>
              <tbody>
                {data.product_growth.map((p, i) => (
                  <tr key={i} className={`border-b border-slate-100 ${i % 2 === 0 ? 'bg-white' : 'bg-slate-50/50'}`}>
                    <td className="px-4 py-2">{p.product}</td>
                    <td className="px-4 py-2 text-right font-mono">{p.qty.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                    <td className="px-4 py-2 text-right">{p.unit}</td>
                    <td className={`px-4 py-2 text-right font-medium ${p.yoy >= 0 ? 'text-green-600' : 'text-red-600'}`}>{p.yoy >= 0 ? '+' : ''}{p.yoy}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {drillDown === 'price_chart' && (
            <div className="overflow-x-auto">
              <table className="w-full text-xs border-collapse">
                <thead>
                  <tr className="bg-slate-50 border-b border-slate-200">
                    <th className="text-left px-4 py-2.5 font-semibold text-slate-600">周次</th>
                    {data.products.map(p => <th key={p} className="text-right px-4 py-2.5 font-semibold text-slate-600">{p}（元/吨）</th>)}
                  </tr>
                </thead>
                <tbody>
                  {data.price_chart.map((pt, i) => (
                    <tr key={i} className={`border-b border-slate-100 ${i % 2 === 0 ? 'bg-white' : 'bg-slate-50/50'}`}>
                      <td className="px-4 py-2">{pt.week as string}</td>
                      {data.products.map(p => <td key={p} className="px-4 py-2 text-right font-mono">{typeof pt[p] === 'number' && (pt[p] as number) > 0 ? (pt[p] as number).toLocaleString() : '-'}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {drillDown === 'sales_chart' && (
            <div className="overflow-x-auto">
              <table className="w-full text-xs border-collapse">
                <thead>
                  <tr className="bg-slate-50 border-b border-slate-200">
                    <th className="text-left px-4 py-2.5 font-semibold text-slate-600">周次</th>
                    {data.products.map(p => <th key={p} className="text-right px-4 py-2.5 font-semibold text-slate-600">{p}（吨）</th>)}
                  </tr>
                </thead>
                <tbody>
                  {data.sales_chart.map((pt, i) => (
                    <tr key={i} className={`border-b border-slate-100 ${i % 2 === 0 ? 'bg-white' : 'bg-slate-50/50'}`}>
                      <td className="px-4 py-2">{pt.week as string}</td>
                      {data.products.map(p => <td key={p} className="px-4 py-2 text-right font-mono">{typeof pt[p] === 'number' && (pt[p] as number) > 0 ? (pt[p] as number).toLocaleString(undefined, { minimumFractionDigits: 2 }) : '-'}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {drillDown === 'sales_detail' && (
            <div className="overflow-x-auto max-h-[70vh] overflow-y-auto">
              <table className="w-full text-xs border-collapse">
                <thead className="sticky top-0 z-10">
                  <tr className="bg-slate-50 border-b border-slate-200">
                    <th className="text-left px-3 py-2.5 font-semibold text-slate-600 sticky left-0 bg-slate-50 min-w-[120px] border-r border-slate-200">产品名称</th>
                    <th className="text-left px-3 py-2.5 font-semibold text-slate-600 min-w-[150px] border-r border-slate-200">客户名称</th>
                    {data.weeks.map(w => (
                      <th key={w} colSpan={2} className="text-center px-2 py-2.5 font-semibold text-slate-600 border-l border-slate-200">{w}</th>
                    ))}
                  </tr>
                  <tr className="bg-slate-50 border-b border-slate-200">
                    <th className="sticky left-0 bg-slate-50 border-r border-slate-200" />
                    <th className="border-r border-slate-200" />
                    {data.weeks.map(w => (
                      <React.Fragment key={w}>
                        <th className="text-center px-1 py-1.5 text-[10px] text-slate-400 border-l border-slate-200 font-normal">销售量</th>
                        <th className="text-center px-1 py-1.5 text-[10px] text-slate-400 font-normal">含税单价</th>
                      </React.Fragment>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.sales_detail.map((row, idx) => (
                    <tr key={idx} className={`border-b border-slate-100 ${idx % 2 === 0 ? 'bg-white' : 'bg-slate-50/50'}`}>
                      <td className="px-3 py-2 font-medium text-slate-700 sticky left-0 bg-inherit border-r border-slate-100">{row.product}</td>
                      <td className="px-3 py-2 text-slate-600 border-r border-slate-100">{row.customer}</td>
                      {row.weeks.map(w => (
                        <React.Fragment key={w.week}>
                          <td className="text-right px-2 py-2 font-mono text-slate-600 border-l border-slate-100">{w.qty > 0 ? `${w.qty}吨` : ''}</td>
                          <td className="text-right px-2 py-2 font-mono text-slate-600">{w.price > 0 ? w.price.toLocaleString() : ''}</td>
                        </React.Fragment>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {drillDown === 'summary' && (
            <div className="p-5 text-sm text-slate-600 leading-relaxed space-y-4">
              <p>{monthLabel}总销售量{data.summary.total_sales_qty.toLocaleString()}吨（同比{data.summary.sales_qty_yoy >= 0 ? '+' : ''}{data.summary.sales_qty_yoy}%），总销售额{data.summary.total_sales_amount_wan.toLocaleString()}万元（同比{data.summary.sales_amount_yoy >= 0 ? '+' : ''}{data.summary.sales_amount_yoy}%）。</p>
              {data.customer_share.length > 0 && (
                <p>客户结构：{data.customer_share.map(c => `${c.name}（${totalShare > 0 ? ((c.value / totalShare) * 100).toFixed(1) : 0}%）`).join('、')}。</p>
              )}
              {priceInsight && <p>价格走势：{priceInsight}</p>}
              {salesInsight && <p>销量走势：{salesInsight}</p>}
              {data.product_growth.length > 0 && (
                <table className="w-full text-xs border-collapse mt-1">
                  <thead>
                    <tr className="bg-slate-50 border-b border-slate-200">
                      <th className="text-left px-3 py-2 font-semibold text-slate-600">产品</th>
                      <th className="text-right px-3 py-2 font-semibold text-slate-600">销售量</th>
                      <th className="text-right px-3 py-2 font-semibold text-slate-600">同比</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.product_growth.map((p, i) => (
                      <tr key={i} className={`border-b border-slate-100 ${i % 2 === 0 ? 'bg-white' : 'bg-slate-50/50'}`}>
                        <td className="px-3 py-1.5">{p.product}</td>
                        <td className="px-3 py-1.5 text-right font-mono">{p.qty.toLocaleString(undefined, { minimumFractionDigits: 2 })} {p.unit}</td>
                        <td className={`px-3 py-1.5 text-right font-medium ${p.yoy >= 0 ? 'text-green-600' : 'text-red-600'}`}>{p.yoy >= 0 ? '+' : ''}{p.yoy}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}
        </div>
      </DrillDownView>
    );
  }

  return (
    <div ref={reportRootRef} className="flex-1 overflow-auto bg-slate-50 p-5">
      {/* ── Header ── */}
      <div className="flex items-center justify-between mb-5">
        <h1 className="text-xl font-bold text-blue-700">客户销售情况</h1>
        <div className="text-xs text-slate-400">最后更新时间：{lastUpdate}</div>
      </div>

      {/* ── Filters ── */}
      <div className="flex items-center gap-4 mb-5 bg-white rounded-lg border border-slate-200 px-4 py-2.5">
        <div className="flex items-center gap-1.5 text-sm">
          <span className="text-slate-500">组织:</span>
          <select
            value={selectedOrg}
            onChange={(e) => handleOrgChange(e.target.value)}
            className="min-w-[180px] border border-slate-300 rounded px-2 py-1 text-sm bg-white"
          >
            {REPORT_ORGS.map((org) => (
              <option key={org.id} value={org.id}>{org.name}</option>
            ))}
          </select>
        </div>
        <div className="flex items-center gap-1.5 text-sm">
          <span className="text-slate-500">时间:</span>
          <select
            value={selectedMonth || data.month}
            onChange={(e) => setSelectedMonth(e.target.value)}
            className="border border-slate-300 rounded px-2 py-1 text-sm bg-white"
          >
            {data.available_months.map(m => {
              try {
                const [y, mo] = m.split('-');
                return <option key={m} value={m}>{y}年{parseInt(mo)}月</option>;
              } catch { return <option key={m} value={m}>{m}</option>; }
            })}
          </select>
        </div>
        <div className="flex items-center gap-1.5 text-sm">
          <span className="text-slate-500">物料分类:</span>
          <select
            value={selectedMaterial}
            onChange={(e) => setSelectedMaterial(e.target.value)}
            className="border border-slate-300 rounded px-2 py-1 text-sm bg-white"
          >
            <option value="">全部</option>
            {data.available_materials.map(m => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
        </div>
        <div className="flex-1" />
        <button onClick={handleQuery} className="px-4 py-1.5 bg-blue-600 text-white rounded text-sm font-medium hover:bg-blue-700">
          查询
        </button>
        <button onClick={handleReset} className="px-4 py-1.5 bg-white text-slate-600 border border-slate-300 rounded text-sm font-medium hover:bg-slate-50 flex items-center gap-1">
          <RefreshCw className="w-3.5 h-3.5" /> 重置
        </button>
        <div className="relative" ref={exportMenuRef}>
          <button
            onClick={() => setExportOpen((v) => !v)}
            disabled={isExportingReport}
            className="px-4 py-1.5 bg-white text-slate-700 border border-slate-300 rounded text-sm font-medium hover:bg-slate-50 flex items-center gap-1 disabled:opacity-60 disabled:cursor-not-allowed"
          >
            <Download className="w-3.5 h-3.5" />
            {isExportingReport ? '导出中...' : '导出报告'}
          </button>
          {exportOpen && (
            <div className="absolute right-0 top-10 w-44 bg-white rounded-lg shadow-lg border border-slate-200 py-1 z-50 text-xs">
              <button
                onClick={() => handleExportWholeReport('pdf')}
                className="w-full px-3 py-2 text-left hover:bg-slate-50 flex items-center gap-2 text-slate-700"
              >
                <FileText className="w-3.5 h-3.5" /> 导出 PDF
              </button>
              <button
                onClick={() => handleExportWholeReport('word')}
                className="w-full px-3 py-2 text-left hover:bg-slate-50 flex items-center gap-2 text-slate-700"
              >
                <File className="w-3.5 h-3.5" /> 导出 Word
              </button>
              <button
                onClick={() => handleExportWholeReport('image')}
                className="w-full px-3 py-2 text-left hover:bg-slate-50 flex items-center gap-2 text-slate-700"
              >
                <ImageIcon className="w-3.5 h-3.5" /> 导出整页图片
              </button>
            </div>
          )}
        </div>
      </div>

      {/* ── Row 1: KPI cards + Weekly price chart ── */}
      <div className="grid grid-cols-12 gap-4 mb-4">
        <div ref={kpiQtyRef} className="col-span-3 bg-white rounded-lg border border-slate-200 p-5 relative">
          <div className="flex items-center justify-between mb-2">
            <div className="text-sm text-slate-500">总销售量（吨）</div>
            <ModuleMenu moduleRef={kpiQtyRef} title="KPI-销售量" onDrillDown={() => setDrillDown('kpi')}
              excelData={[
                { 指标: '总销售量', 数值: data.summary.total_sales_qty, 单位: '吨', 同比变化: `${data.summary.sales_qty_yoy >= 0 ? '+' : ''}${data.summary.sales_qty_yoy}%` },
                { 指标: '总销售额', 数值: data.summary.total_sales_amount_wan, 单位: '万元', 同比变化: `${data.summary.sales_amount_yoy >= 0 ? '+' : ''}${data.summary.sales_amount_yoy}%` },
              ]} />
          </div>
          <div className="text-3xl font-bold text-blue-700 mb-1">{data.summary.total_sales_qty.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
          <YoYBadge value={data.summary.sales_qty_yoy} />
        </div>
        <div ref={kpiAmtRef} className="col-span-3 bg-white rounded-lg border border-slate-200 p-5 relative">
          <div className="flex items-center justify-between mb-2">
            <div className="text-sm text-slate-500">总销售额（发货口径，万元）</div>
            <ModuleMenu moduleRef={kpiAmtRef} title="KPI-销售额" onDrillDown={() => setDrillDown('kpi')}
              excelData={[
                { 指标: '总销售量', 数值: data.summary.total_sales_qty, 单位: '吨', 同比变化: `${data.summary.sales_qty_yoy >= 0 ? '+' : ''}${data.summary.sales_qty_yoy}%` },
                { 指标: '总销售额', 数值: data.summary.total_sales_amount_wan, 单位: '万元', 同比变化: `${data.summary.sales_amount_yoy >= 0 ? '+' : ''}${data.summary.sales_amount_yoy}%` },
              ]} />
          </div>
          <div className="text-3xl font-bold text-blue-700 mb-1">{data.summary.total_sales_amount_wan.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
          <YoYBadge value={data.summary.sales_amount_yoy} />
          <div className="mt-2 text-[11px] text-slate-400">区别于聊天中的ERP FAMOUNT不含税口径</div>
        </div>
        <div ref={priceChartRef} className="col-span-6 bg-white rounded-lg border border-slate-200 p-4 relative">
          <div className="flex items-center justify-between mb-1">
            <div className="text-sm font-semibold text-slate-700">各产品每周含税单价分析</div>
            <ModuleMenu moduleRef={priceChartRef} title="含税单价分析" onDrillDown={() => setDrillDown('price_chart')}
              excelData={data.price_chart.map(pt => ({ 周次: pt.week as string, ...Object.fromEntries(data.products.map(p => [p, pt[p] ?? ''])) }))} />
          </div>
          <div className="flex items-center gap-2 mb-2 text-[11px]">
            <span className="text-slate-500">图表聚焦:</span>
            <select
              value={focusProduct}
              onChange={(e) => setFocusProduct(e.target.value)}
              className="border border-slate-300 rounded px-2 py-1 text-[11px] bg-white"
            >
              <option value="">重点产品（Top 6）</option>
              {data.products.map((p) => (
                <option key={p} value={p}>{p}</option>
              ))}
            </select>
            <span className="text-slate-400">默认显示销量前6产品，便于阅读趋势</span>
          </div>
          <div className="flex flex-wrap gap-x-3 gap-y-0.5 mb-1 text-[10px] text-slate-500">
            {visibleChartProducts.map(p => (
              <span key={p} className="flex items-center gap-1">
                <span className="w-2.5 h-0.5 rounded" style={{ backgroundColor: productColors[p] }} />{p}
              </span>
            ))}
          </div>
          <div className="text-[10px] text-slate-400 mb-0.5">含税单价（元/吨）</div>
          <div className="h-[140px]">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={data.price_chart}>
                <CartesianGrid strokeDasharray="3 3" stroke={chart.grid} vertical={false} />
                <XAxis dataKey="week" tick={{ fontSize: 10, fill: chart.tickMuted }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fontSize: 10, fill: chart.tickMuted }} axisLine={false} tickLine={false} domain={['auto', 'auto']} />
                <RechartsTooltip contentStyle={tooltipStyle} />
                {avgPrice > 0 && <ReferenceLine y={avgPrice} stroke="#f97316" strokeWidth={1} label={{ value: `平均值: ${avgPrice}`, position: 'right', fontSize: 10, fill: '#f97316' }} />}
                {visibleChartProducts.map(p => (
                  <Line
                    key={p}
                    type="monotone"
                    dataKey={p}
                    stroke={productColors[p]}
                    strokeWidth={focusProduct ? 2.5 : 1.8}
                    dot={{ r: focusProduct ? 3 : 2 }}
                    connectNulls
                    label={focusProduct ? chartLabel : false}
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>
          {priceInsight && (
            <div className="text-[10px] text-slate-500 mt-2 leading-relaxed">
              {priceInsight}
            </div>
          )}
        </div>
      </div>

      {/* ── Row 2: Pie + Growth bars + Weekly sales chart ── */}
      <div className="grid grid-cols-12 gap-4 mb-4">
        <div ref={pieRef} className="col-span-3 bg-white rounded-lg border border-slate-200 p-4 relative">
          <div className="flex items-center justify-between mb-2">
            <div className="text-sm font-semibold text-slate-700">客户销售量占比</div>
            <ModuleMenu moduleRef={pieRef} title="客户销售量占比" onDrillDown={() => setDrillDown('customer_share')}
              excelData={data.customer_share.map(c => ({ 客户: c.name, 销售量吨: c.value, 占比: totalShare > 0 ? `${((c.value / totalShare) * 100).toFixed(1)}%` : '-' }))} />
          </div>
          <div className="flex flex-wrap gap-x-2 gap-y-0.5 mb-1 text-[10px] text-slate-500">
            {data.customer_share.map((c, i) => (
              <span key={c.name} className="flex items-center gap-1">
                <span className="w-2 h-2 rounded-full" style={{ backgroundColor: PIE_COLORS[i % PIE_COLORS.length] }} />
                {c.name.length > 10 ? c.name.slice(0, 10) + '…' : c.name}
              </span>
            ))}
          </div>
          <div className="h-[180px]">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={data.customer_share}
                  cx="50%" cy="50%"
                  innerRadius={50} outerRadius={75}
                  dataKey="value" nameKey="name"
                  label={({ percent }) => `${(percent * 100).toFixed(0)}%`}
                  labelLine={false}
                  style={{ fontSize: '11px', fontWeight: 500 }}
                >
                  {data.customer_share.map((_, i) => (
                    <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                  ))}
                </Pie>
                <RechartsTooltip formatter={(value: number, name: string) => [`${value.toLocaleString()} 吨 (${totalShare > 0 ? ((value / totalShare) * 100).toFixed(1) : 0}%)`, name]} />
              </PieChart>
            </ResponsiveContainer>
          </div>
          <div className="text-[10px] text-slate-400 mt-1">
            {data.customer_share.length > 0 && `${data.customer_share[0].name}以${totalShare > 0 ? ((data.customer_share[0].value / totalShare) * 100).toFixed(0) : 0}%的销售贡献占据主导地位。`}
          </div>
        </div>

        <div ref={growthRef} className="col-span-3 bg-white rounded-lg border border-slate-200 p-4 relative">
          <div className="flex items-center justify-between mb-3">
            <div className="text-sm font-semibold text-slate-700">销售量增长率</div>
            <ModuleMenu moduleRef={growthRef} title="销售量增长率" onDrillDown={() => setDrillDown('growth')}
              excelData={data.product_growth.map(p => ({ 产品: p.product, 销售量: p.qty, 单位: p.unit, 同比变化: `${p.yoy >= 0 ? '+' : ''}${p.yoy}%` }))} />
          </div>
          <div className="space-y-3 overflow-auto max-h-[240px] pr-1">
            {data.product_growth.slice(0, 8).map((p) => (
              <div key={p.product}>
                <div className="flex items-center justify-between text-xs mb-0.5">
                  <span className="text-slate-600 font-medium truncate max-w-[90px]" title={p.product}>{p.product}</span>
                  <span className="font-bold text-slate-800">{p.qty.toLocaleString(undefined, { minimumFractionDigits: 2 })}{p.unit}</span>
                </div>
                <div className="flex items-center gap-2">
                  <div className="flex-1 bg-slate-100 h-2.5 rounded-full overflow-hidden">
                    <div className="h-full rounded-full bg-blue-500" style={{ width: `${Math.min((p.qty / maxQty) * 100, 100)}%` }} />
                  </div>
                  <YoYBadge value={p.yoy} />
                </div>
              </div>
            ))}
          </div>
        </div>

        <div ref={salesChartRef} className="col-span-6 bg-white rounded-lg border border-slate-200 p-4 relative">
          <div className="flex items-center justify-between mb-1">
            <div className="text-sm font-semibold text-slate-700">各产品每周销量分析</div>
            <ModuleMenu moduleRef={salesChartRef} title="每周销量分析" onDrillDown={() => setDrillDown('sales_chart')}
              excelData={data.sales_chart.map(pt => ({ 周次: pt.week as string, ...Object.fromEntries(data.products.map(p => [p, pt[p] ?? ''])) }))} />
          </div>
          <div className="flex flex-wrap gap-x-3 gap-y-0.5 mb-1 text-[10px] text-slate-500">
            {visibleChartProducts.map(p => (
              <span key={p} className="flex items-center gap-1">
                <span className="w-2.5 h-0.5 rounded" style={{ backgroundColor: productColors[p] }} />{p}
              </span>
            ))}
          </div>
          <div className="text-[10px] text-slate-400 mb-0.5">销售量（吨）</div>
          <div className="h-[200px]">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={data.sales_chart}>
                <CartesianGrid strokeDasharray="3 3" stroke={chart.grid} vertical={false} />
                <XAxis dataKey="week" tick={{ fontSize: 10, fill: chart.tickMuted }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fontSize: 10, fill: chart.tickMuted }} axisLine={false} tickLine={false} />
                <RechartsTooltip contentStyle={tooltipStyle} />
                {avgSalesQty > 0 && <ReferenceLine y={avgSalesQty} stroke="#f97316" strokeWidth={1} label={{ value: `平均值: ${avgSalesQty}`, position: 'right', fontSize: 10, fill: '#f97316' }} />}
                {visibleChartProducts.map(p => (
                  <Line
                    key={p}
                    type="monotone"
                    dataKey={p}
                    stroke={productColors[p]}
                    strokeWidth={focusProduct ? 2.5 : 1.8}
                    dot={{ r: focusProduct ? 3 : 2.5, fill: productColors[p] }}
                    connectNulls
                    label={focusProduct ? chartLabel : false}
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>
          {salesInsight && (
            <div className="text-[10px] text-slate-500 mt-2 leading-relaxed">
              {salesInsight}
            </div>
          )}
        </div>
      </div>

      {/* ── Row 3: Sales detail table + Analysis summary ── */}
      <div className="grid grid-cols-12 gap-4">
        <div ref={salesDetailRef} className="col-span-7 bg-white rounded-lg border border-slate-200 p-4 border-l-4 border-l-blue-500 relative">
          <div className="flex items-center justify-between mb-3">
            <div className="text-sm font-semibold text-slate-700">销售情况</div>
            <ModuleMenu moduleRef={salesDetailRef} title="销售情况明细" onDrillDown={() => setDrillDown('sales_detail')}
              excelData={data.sales_detail.flatMap(r => r.weeks.map(w => ({ 产品: r.product, 客户: r.customer, 周次: w.week, 销售量吨: w.qty || '', 含税单价: w.price || '' })))} />
          </div>
          <div className="overflow-x-auto max-h-[400px] overflow-y-auto">
            <table className="w-full text-xs border-collapse">
              <thead className="sticky top-0 z-10">
                <tr className="bg-slate-50 border-b border-slate-200">
                  <th className="text-left px-3 py-2.5 font-semibold text-slate-600 sticky left-0 bg-slate-50 min-w-[120px] border-r border-slate-200">产品名称</th>
                  <th className="text-left px-3 py-2.5 font-semibold text-slate-600 min-w-[150px] border-r border-slate-200">客户名称</th>
                  {data.weeks.map(w => (
                    <th key={w} colSpan={2} className="text-center px-2 py-2.5 font-semibold text-slate-600 border-l border-slate-200">{w}</th>
                  ))}
                </tr>
                <tr className="bg-slate-50 border-b border-slate-200">
                  <th className="sticky left-0 bg-slate-50 border-r border-slate-200" />
                  <th className="border-r border-slate-200" />
                  {data.weeks.map(w => (
                    <React.Fragment key={w}>
                      <th className="text-center px-1 py-1.5 text-[10px] text-slate-400 border-l border-slate-200 font-normal">销售量</th>
                      <th className="text-center px-1 py-1.5 text-[10px] text-slate-400 font-normal">含税单价</th>
                    </React.Fragment>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.sales_detail.slice(0, 30).map((row, idx) => (
                  <tr key={idx} className={`border-b border-slate-100 ${idx % 2 === 0 ? 'bg-white' : 'bg-slate-50/50'}`}>
                    <td className="px-3 py-2 font-medium text-slate-700 sticky left-0 bg-inherit border-r border-slate-100" title={row.product}>{row.product}</td>
                    <td className="px-3 py-2 text-slate-600 border-r border-slate-100" title={row.customer}>{row.customer}</td>
                    {row.weeks.map((w) => (
                      <React.Fragment key={w.week}>
                        <td className="text-right px-2 py-2 font-mono text-slate-600 border-l border-slate-100">
                          {w.qty > 0 ? `${w.qty}吨` : ''}
                        </td>
                        <td className="text-right px-2 py-2 font-mono text-slate-600">
                          {w.price > 0 ? w.price.toLocaleString() : ''}
                        </td>
                      </React.Fragment>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {data.sales_detail.length > 0 && (() => {
            const topProduct = data.sales_detail[0]?.product || '';
            const productRows = data.sales_detail.filter(r => r.product === topProduct);
            const contractsWithQty = productRows.filter(r => r.weeks.some(w => w.qty > 0));
            const allQtys = productRows.flatMap(r => r.weeks.map(w => w.qty)).filter(q => q > 0);
            const maxQtyVal = allQtys.length > 0 ? Math.round(Math.max(...allQtys)) : 0;
            const weeksWithData = [...new Set(productRows.flatMap(r => r.weeks.filter(w => w.qty > 0).map(w => w.week)))];
            const dateRange = weeksWithData.length > 0 ? weeksWithData[weeksWithData.length - 1] : '';
            return (
              <div className="mt-3 text-[10px] text-slate-500 leading-relaxed">
                图表显示{topProduct}产品存在严重的未出库积压问题，
                {contractsWithQty.length > 0 && `${contractsWithQty.length}份合同未出数量均超过${allQtys.length > 0 ? Math.round(Math.min(...allQtys)) : 0}吨（最高达${maxQtyVal}吨），`}
                {dateRange && `且交货日期集中在${dateRange}，`}
                需立即排查生产或物流环节的瓶颈。
              </div>
            );
          })()}
        </div>

        <div ref={summaryRef} className="col-span-5 bg-white rounded-lg border border-slate-200 p-5 relative">
          <div className="flex items-center justify-between mb-4">
            <div className="text-sm font-bold text-slate-800">客户销售情况总结分析</div>
            <ModuleMenu moduleRef={summaryRef} title="总结分析" onDrillDown={() => setDrillDown('summary')}
              excelData={[{ 月份: monthLabel, 总销售量吨: data.summary.total_sales_qty, 总销售额万元: data.summary.total_sales_amount_wan, 销量同比: `${data.summary.sales_qty_yoy}%`, 销售额同比: `${data.summary.sales_amount_yoy}%` }]} />
          </div>
          <div className="text-xs text-slate-600 leading-relaxed space-y-3">
            <p>
              {monthLabel}总库存{data.summary.total_sales_qty.toLocaleString()}吨，
              同比{data.summary.sales_qty_yoy >= 0 ? '下降' : '增长'}{Math.abs(data.summary.sales_qty_yoy)}%，
              总销售额{data.summary.total_sales_amount_wan.toLocaleString()}万元。
            </p>
            <ul className="space-y-2.5 mt-3">
              <li className="flex gap-2">
                <span className="mt-1 w-2 h-2 rounded-full bg-blue-500 flex-shrink-0" />
                <span>优先清理液体类滞留库存（{data.products.slice(0, 2).join('/')}），调整采购节奏。</span>
              </li>
              <li className="flex gap-2">
                <span className="mt-1 w-2 h-2 rounded-full bg-blue-500 flex-shrink-0" />
                <span>
                  近{data.weeks.length || 4}天消耗量波动大
                  {data.sales_chart.length > 0 && (() => {
                    const vals = data.products.flatMap(p => data.sales_chart.map(pt => pt[p]).filter(v => typeof v === 'number' && v > 0)) as number[];
                    if (vals.length < 2) return '';
                    return `（${Math.min(...vals).toFixed(0)}-${Math.max(...vals).toFixed(0)}吨）`;
                  })()}，需关注短期订单关联。
                </span>
              </li>
              {data.customer_share.length > 0 && (
                <li className="flex gap-2">
                  <span className="mt-1 w-2 h-2 rounded-full bg-blue-500 flex-shrink-0" />
                  <span>
                    {data.customer_share[0].name}贡献{totalShare > 0 ? ((data.customer_share[0].value / totalShare) * 100).toFixed(0) : 0}%销量，优质客户主导经营。
                    {data.customer_share.length > 1 && ` ${data.customer_share[1]?.name}贡献${totalShare > 0 ? ((data.customer_share[1].value / totalShare) * 100).toFixed(0) : 0}%。`}
                  </span>
                </li>
              )}
              {data.product_growth.some(p => p.yoy < 0) && (
                <li className="flex gap-2">
                  <span className="mt-1 w-2 h-2 rounded-full bg-red-500 flex-shrink-0" />
                  <span className="text-red-600">
                    {data.product_growth.filter(p => p.yoy < 0).map(p => p.product).slice(0, 3).join('、')}
                    产品同比下降，需关注市场需求变化。
                  </span>
                </li>
              )}
              {data.product_growth.length > 0 && (
                <li className="flex gap-2">
                  <span className="mt-1 w-2 h-2 rounded-full bg-red-500 flex-shrink-0" />
                  <span className="text-red-600">
                    紧缺库存突出：{data.products.slice(0, 2).join('/')}类合计
                    {data.product_growth.slice(0, 2).reduce((s, p) => s + p.qty, 0).toLocaleString()}吨
                    {data.product_growth[0] && `（占比超${totalShare > 0 ? ((data.product_growth[0].qty / totalShare) * 100).toFixed(0) : 0}%）`}，
                    且同比上升{Math.abs(data.product_growth[0]?.yoy || 0)}%。
                  </span>
                </li>
              )}
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
};
