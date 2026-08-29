import { ArrowDown, ArrowUp, ChevronLeft, Download, File, FileText, Image, RefreshCw, Sparkles } from 'lucide-react';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  LabelList,
  Line,
  Tooltip as RechartsTooltip, ResponsiveContainer,
  XAxis, YAxis,
} from 'recharts';
import * as XLSX from 'xlsx';
import { getChartTheme, rechartsTooltipStyle } from '../lib/chartTheme';
import { useTheme } from '../lib/theme';
import { API_BASE_URL, fetchWithRetry } from '../services/api';
import { exportWholeReport } from '../utils/reportExport';

interface ContractReport {
  start_date: string;
  end_date: string;
  available_materials: string[];
  org_name: string;
  summary: {
    contract_amount_yi: number;
    unfulfilled_orders: number;
    completion_rate: number;
    contract_qty: number;
    out_qty: number;
    not_out_qty: number;
    invoice_amount_wan: number;
    uninvoiced_amount_wan: number;
    amount_yoy: number;
    qty_yoy: number;
    unfulfilled_yoy: number;
    completion_rate_yoy: number;
    contract_qty_yoy: number;
    out_qty_yoy: number;
    not_out_qty_yoy: number;
    invoice_yoy: number;
    uninvoiced_yoy: number;
  };
  unshipped_contracts: {
    product: string;
    product_type: string;
    bill_no: string;
    customer: string;
    delivery_date: string;
    contract_qty: number;
    unshipped_qty: number;
  }[];
  execution_chart: {
    product: string;
    contract_qty: number;
    out_qty: number;
    execution_rate: number;
  }[];
  ai_analysis?: {
    chart_top: string;
    chart_bottom: string;
    summary_paragraph: string;
    summary_bullets: string[];
    unshipped_analysis: string;
  };
}

const REPORT_ORGS = [
  { id: 'huizhou', name: '惠州伊斯科 (Huizhou Isco)' },
  { id: 'guangdong', name: '广东伊斯科 (Guangdong Isco)' },
];
const DEFAULT_REPORT_ORG = 'huizhou';

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

const KPICard: React.FC<{ label: string; value: string | number; yoy?: number; color?: string }> = ({ label, value, yoy, color }) => (
  <div className="bg-white rounded-lg border border-slate-200 p-4 flex flex-col justify-between">
    <div className="text-xs text-slate-500 mb-1.5">{label}</div>
    <div className={`text-2xl font-bold mb-1 ${color || 'text-blue-700'}`}>
      {typeof value === 'number' ? value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : value}
    </div>
    {yoy !== undefined && <YoYBadge value={yoy} />}
  </div>
);

/* ── Three-dot Module Menu ── */
interface ModuleMenuProps {
  moduleRef: React.RefObject<HTMLDivElement | null>;
  onDrillDown?: () => void;
  onExcelDownload?: () => void;
  hasDrillDown?: boolean;
}

const ModuleMenu: React.FC<ModuleMenuProps> = () => null;

/* ── Drill-Down Detail Views ── */
interface DrillDownProps {
  data: ContractReport;
  module: string;
  onBack: () => void;
}

const DrillDownView: React.FC<DrillDownProps> = ({ data, module, onBack }) => {
  const titleMap: Record<string, string> = {
    kpi: 'KPI 指标明细',
    unshipped: '未出库合同明细',
    chart: '各产品执行明细',
    summary: '总结分析明细',
  };

  return (
    <div className="flex-1 overflow-auto bg-slate-50 p-5">
      <div className="flex items-center gap-3 mb-4">
        <button onClick={onBack}
          className="flex items-center gap-1 px-3 py-1.5 bg-white border border-slate-300 rounded-lg text-sm text-slate-600 hover:bg-slate-50 shadow-sm">
          <ChevronLeft className="w-4 h-4" /> 返回上级
        </button>
        <h2 className="text-lg font-bold text-slate-800">{titleMap[module] || '明细数据'}</h2>
      </div>

      {module === 'kpi' && (
        <div className="bg-white rounded-lg border border-slate-200 p-5">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="bg-slate-50 border-b border-slate-200">
                <th className="text-left px-3 py-2 font-semibold text-slate-600">指标名称</th>
                <th className="text-right px-3 py-2 font-semibold text-slate-600">当前值</th>
                <th className="text-right px-3 py-2 font-semibold text-slate-600">同比变化</th>
              </tr>
            </thead>
            <tbody>
              {[
                { name: '合同金额（亿元）', value: data.summary.contract_amount_yi, yoy: data.summary.amount_yoy },
                { name: '未执行订单数量', value: data.summary.unfulfilled_orders, yoy: data.summary.unfulfilled_yoy },
                { name: '合同执行完成率（%）', value: data.summary.completion_rate, yoy: data.summary.completion_rate_yoy },
                { name: '合同数量（吨）', value: data.summary.contract_qty, yoy: data.summary.contract_qty_yoy },
                { name: '已出数量（吨）', value: data.summary.out_qty, yoy: data.summary.out_qty_yoy },
                { name: '未出数量（吨）', value: data.summary.not_out_qty, yoy: data.summary.not_out_qty_yoy },
                { name: '开票金额（万元）', value: data.summary.invoice_amount_wan, yoy: data.summary.invoice_yoy },
                { name: '未开票金额（万元）', value: data.summary.uninvoiced_amount_wan, yoy: data.summary.uninvoiced_yoy },
              ].map((row, i) => (
                <tr key={i} className={`border-b border-slate-100 ${i % 2 === 0 ? 'bg-white' : 'bg-slate-50/30'}`}>
                  <td className="px-3 py-2.5 text-slate-700 font-medium">{row.name}</td>
                  <td className="text-right px-3 py-2.5 font-mono text-slate-800">{row.value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                  <td className="text-right px-3 py-2.5">
                    <YoYBadge value={row.yoy} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {module === 'unshipped' && (
        <div className="bg-white rounded-lg border border-slate-200 p-5">
          <div className="text-sm text-slate-500 mb-3">共 {data.unshipped_contracts.length} 条合同记录（未出库≥30吨）</div>
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="bg-slate-50 border-b border-slate-200">
                <th className="text-left px-3 py-2 font-semibold text-slate-600">产品名称</th>
                <th className="text-left px-3 py-2 font-semibold text-slate-600">产品类型</th>
                <th className="text-left px-3 py-2 font-semibold text-slate-600">合同编号</th>
                <th className="text-left px-3 py-2 font-semibold text-slate-600">客户名称</th>
                <th className="text-left px-3 py-2 font-semibold text-slate-600">交货日期</th>
                <th className="text-right px-3 py-2 font-semibold text-slate-600">合同数量（吨）</th>
                <th className="text-right px-3 py-2 font-semibold text-slate-600">未出数量（吨）</th>
              </tr>
            </thead>
            <tbody>
              {data.unshipped_contracts.map((c, i) => (
                <tr key={i} className={`border-b border-slate-100 ${i % 2 === 0 ? 'bg-white' : 'bg-slate-50/30'}`}>
                  <td className="px-3 py-2.5 text-slate-700 font-medium">{c.product}</td>
                  <td className="px-3 py-2.5 text-slate-500">{c.product_type}</td>
                  <td className="px-3 py-2.5 font-mono text-slate-500 text-xs">{c.bill_no}</td>
                  <td className="px-3 py-2.5 text-slate-600">{c.customer}</td>
                  <td className="px-3 py-2.5 text-slate-500">{c.delivery_date}</td>
                  <td className="text-right px-3 py-2.5 font-mono">{c.contract_qty.toLocaleString()}</td>
                  <td className={`text-right px-3 py-2.5 font-mono font-bold ${c.unshipped_qty >= 100 ? 'text-red-500' : c.unshipped_qty >= 60 ? 'text-orange-500' : 'text-yellow-600'
                    }`}>{c.unshipped_qty.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {module === 'chart' && (
        <div className="bg-white rounded-lg border border-slate-200 p-5">
          <div className="text-sm text-slate-500 mb-3">共 {data.execution_chart.length} 个产品执行记录</div>
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="bg-slate-50 border-b border-slate-200">
                <th className="text-left px-3 py-2 font-semibold text-slate-600">产品名称</th>
                <th className="text-right px-3 py-2 font-semibold text-slate-600">合同数量（吨）</th>
                <th className="text-right px-3 py-2 font-semibold text-slate-600">已出数量（吨）</th>
                <th className="text-right px-3 py-2 font-semibold text-slate-600">未出数量（吨）</th>
                <th className="text-right px-3 py-2 font-semibold text-slate-600">执行率</th>
              </tr>
            </thead>
            <tbody>
              {data.execution_chart.map((e, i) => (
                <tr key={i} className={`border-b border-slate-100 ${i % 2 === 0 ? 'bg-white' : 'bg-slate-50/30'}`}>
                  <td className="px-3 py-2.5 text-slate-700 font-medium">{e.product}</td>
                  <td className="text-right px-3 py-2.5 font-mono">{e.contract_qty.toLocaleString(undefined, { maximumFractionDigits: 2 })}</td>
                  <td className="text-right px-3 py-2.5 font-mono">{e.out_qty.toLocaleString(undefined, { maximumFractionDigits: 2 })}</td>
                  <td className="text-right px-3 py-2.5 font-mono text-orange-600">{(e.contract_qty - e.out_qty).toLocaleString(undefined, { maximumFractionDigits: 2 })}</td>
                  <td className={`text-right px-3 py-2.5 font-mono font-bold ${e.execution_rate >= 90 ? 'text-green-600' : e.execution_rate >= 70 ? 'text-orange-500' : 'text-red-500'
                    }`}>{e.execution_rate}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {module === 'summary' && (
        <div className="bg-white rounded-lg border border-slate-200 p-5">
          <h3 className="text-base font-bold text-slate-800 mb-3">AI 分析详情</h3>
          {data.ai_analysis ? (
            <div className="space-y-4">
              <div>
                <div className="text-sm font-semibold text-slate-600 mb-1">图表分析（上）</div>
                <p className="text-sm text-slate-700 leading-relaxed bg-slate-50 rounded-lg p-3">{data.ai_analysis.chart_top}</p>
              </div>
              <div>
                <div className="text-sm font-semibold text-slate-600 mb-1">图表分析（下）</div>
                <p className="text-sm text-slate-700 leading-relaxed bg-slate-50 rounded-lg p-3">{data.ai_analysis.chart_bottom}</p>
              </div>
              <div>
                <div className="text-sm font-semibold text-slate-600 mb-1">总结段落</div>
                <p className="text-sm text-slate-700 leading-relaxed bg-slate-50 rounded-lg p-3">{data.ai_analysis.summary_paragraph}</p>
              </div>
              <div>
                <div className="text-sm font-semibold text-slate-600 mb-1">关键要点（{data.ai_analysis.summary_bullets.length}条）</div>
                <div className="bg-slate-50 rounded-lg p-3">
                  <ul className="space-y-2">
                    {data.ai_analysis.summary_bullets.map((b, i) => (
                      <li key={i} className="flex gap-2 items-start">
                        <span className="mt-1.5 w-2 h-2 rounded-full bg-blue-500 flex-shrink-0" />
                        <span className="text-sm text-slate-700 leading-relaxed">{b}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
              <div>
                <div className="text-sm font-semibold text-slate-600 mb-1">未出库合同风险分析</div>
                <p className="text-sm text-slate-700 leading-relaxed bg-slate-50 rounded-lg p-3">{data.ai_analysis.unshipped_analysis}</p>
              </div>
            </div>
          ) : (
            <p className="text-sm text-slate-400">暂无AI分析数据</p>
          )}
        </div>
      )}
    </div>
  );
};

export const ContractPerformanceReport: React.FC = () => {
  const { isDark } = useTheme();
  const chart = useMemo(() => getChartTheme(isDark), [isDark]);
  const tooltipStyle = useMemo(
    () => rechartsTooltipStyle(chart, isDark),
    [chart, isDark],
  );
  const contractBarFill = isDark ? '#3f4654' : '#e2e8f0';
  const [data, setData] = useState<ContractReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedOrg, setSelectedOrg] = useState(DEFAULT_REPORT_ORG);
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [selectedMaterial, setSelectedMaterial] = useState('');
  const [lastUpdate, setLastUpdate] = useState('');
  const [drillDown, setDrillDown] = useState<string | null>(null);
  const [aiLoading, setAiLoading] = useState(false);

  // Module refs for fullscreen & image export
  const kpiRef = useRef<HTMLDivElement>(null);
  const unshippedRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<HTMLDivElement>(null);
  const summaryRef = useRef<HTMLDivElement>(null);
  const chartAnalysisRef = useRef<HTMLDivElement>(null);
  const aiRequestSeqRef = useRef(0);
  const reportRootRef = useRef<HTMLDivElement>(null);
  const exportMenuRef = useRef<HTMLDivElement>(null);
  const [exportOpen, setExportOpen] = useState(false);
  const [isExportingReport, setIsExportingReport] = useState(false);

  // Excel export helpers
  const exportExcel = useCallback((rows: Record<string, unknown>[], filename: string) => {
    const ws = XLSX.utils.json_to_sheet(rows);
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, 'Sheet1');
    XLSX.writeFile(wb, `${filename}.xlsx`);
  }, []);

  const loadData = async (
    sd?: string,
    ed?: string,
    material?: string,
    orgId: string = DEFAULT_REPORT_ORG,
  ) => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      params.set('org', orgId);
      if (sd) params.set('start_date', sd);
      if (ed) params.set('end_date', ed);
      if (material) params.set('material', material);

      // Phase 1: Load structured data (fast — DB only, no LLM)
      const res = await fetchWithRetry(`${API_BASE_URL}/api/v2/reports/contract-performance?${params}`);
      const json = await res.json();
      setData(json);
      if (!sd) {
        setStartDate(json.start_date);
        setEndDate(json.end_date);
      }
      setLastUpdate(new Date().toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }));
      setLoading(false);

      // Phase 2: Stream AI analysis in background so text appears progressively
      setAiLoading(true);
      const aiReqId = ++aiRequestSeqRef.current;
      const aiParams = new URLSearchParams();
      aiParams.set('org', orgId);
      aiParams.set('start_date', json.start_date);
      aiParams.set('end_date', json.end_date);
      aiParams.set('stream', 'true');
      if (material) aiParams.set('material', material);

      setData(prev => prev ? {
        ...prev,
        ai_analysis: {
          chart_top: '',
          chart_bottom: '',
          summary_paragraph: '',
          summary_bullets: [],
          unshipped_analysis: '',
        },
      } : prev);

      const parseEvent = (raw: string): { event: string; data: any } | null => {
        const lines = raw.split('\n').filter(Boolean);
        let event = 'message';
        let dataLine = '';
        for (const line of lines) {
          if (line.startsWith('event:')) event = line.slice('event:'.length).trim();
          else if (line.startsWith('data:')) dataLine += line.slice('data:'.length).trim();
        }
        if (!dataLine) return null;
        try {
          return { event, data: JSON.parse(dataLine) };
        } catch {
          return null;
        }
      };

      fetch(`${API_BASE_URL}/api/v2/reports/contract-performance/ai-analysis?${aiParams}`)
        .then(async (response) => {
          if (!response.ok || !response.body) {
            throw new Error(response.statusText || 'AI stream failed');
          }

          const reader = response.body.getReader();
          const decoder = new TextDecoder('utf-8');
          let buffer = '';

          while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });

            const parts = buffer.split('\n\n');
            buffer = parts.pop() || '';

            for (const part of parts) {
              const ev = parseEvent(part);
              if (!ev) continue;
              if (aiRequestSeqRef.current !== aiReqId) return;

              if (ev.event === 'delta') {
                const { field, text } = ev.data as { field: string; text: string };
                const textFields = ['chart_top', 'chart_bottom', 'summary_paragraph', 'unshipped_analysis'];
                if (field && text && textFields.includes(field)) {
                  setData(prev => {
                    if (!prev) return prev;
                    const cur = prev.ai_analysis ?? { chart_top: '', chart_bottom: '', summary_paragraph: '', summary_bullets: [], unshipped_analysis: '' };
                    return {
                      ...prev,
                      ai_analysis: {
                        ...cur,
                        [field]: ((cur as any)[field] || '') + text,
                      },
                    };
                  });
                }
              } else if (ev.event === 'partial') {
                setData(prev => prev ? {
                  ...prev,
                  ai_analysis: {
                    chart_top: prev.ai_analysis?.chart_top || '',
                    chart_bottom: prev.ai_analysis?.chart_bottom || '',
                    summary_paragraph: prev.ai_analysis?.summary_paragraph || '',
                    summary_bullets: prev.ai_analysis?.summary_bullets || [],
                    unshipped_analysis: prev.ai_analysis?.unshipped_analysis || '',
                    ...ev.data,
                  },
                } : prev);
              } else if (ev.event === 'done') {
                setData(prev => prev ? { ...prev, ai_analysis: ev.data } : prev);
              } else if (ev.event === 'error') {
                throw new Error(String(ev.data?.message || 'AI stream error'));
              }
            }
          }
        })
        .catch(e => console.error('AI analysis stream failed:', e))
        .finally(() => {
          if (aiRequestSeqRef.current === aiReqId) setAiLoading(false);
        });
    } catch (e) {
      console.error('Failed to load contract report', e);
      setLoading(false);
    }
  };

  useEffect(() => { loadData(undefined, undefined, undefined, DEFAULT_REPORT_ORG); }, []);

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

  const handleQuery = () => loadData(startDate, endDate, selectedMaterial, selectedOrg);
  const handleOrgChange = (orgId: string) => {
    setSelectedOrg(orgId);
    loadData(startDate || data?.start_date, endDate || data?.end_date, selectedMaterial, orgId);
  };
  const handleReset = () => {
    setSelectedOrg(DEFAULT_REPORT_ORG);
    setSelectedMaterial('');
    setStartDate('');
    setEndDate('');
    loadData(undefined, undefined, undefined, DEFAULT_REPORT_ORG);
  };

  const handleExportWholeReport = async (format: 'image' | 'pdf' | 'word') => {
    const el = reportRootRef.current;
    if (!el) return;
    setIsExportingReport(true);
    setExportOpen(false);
    try {
      await exportWholeReport(
        el,
        `contract-performance-report-${Date.now()}`,
        format,
      );
    } catch (e) {
      console.error('Failed to export whole report', e);
    } finally {
      setIsExportingReport(false);
    }
  };

  const chartData = useMemo(() => {
    if (!data) return [];
    return data.execution_chart.map(e => ({
      name: e.product,
      已出数量: Math.round(e.out_qty),
      合同数量: Math.round(e.contract_qty),
      执行率: e.execution_rate,
    }));
  }, [data]);

  /* AI-generated analysis from backend */
  const chartAnalysis = useMemo(() => {
    if (!data?.ai_analysis) return { top: '', bottom: '' };
    return { top: data.ai_analysis.chart_top, bottom: data.ai_analysis.chart_bottom };
  }, [data]);

  const summaryAnalysis = useMemo(() => {
    if (!data?.ai_analysis) return { paragraph: '', bullets: [] as string[] };
    return {
      paragraph: data.ai_analysis.summary_paragraph,
      bullets: data.ai_analysis.summary_bullets || [],
    };
  }, [data]);

  const unshippedAnalysis = useMemo(() => {
    if (!data?.ai_analysis) return '';
    return data.ai_analysis.unshipped_analysis;
  }, [data]);

  // Loading overlay component for individual modules
  const LoadingOverlay: React.FC<{ show: boolean }> = ({ show }) => {
    if (!show) return null;
    return (
      <div className="absolute inset-0 bg-white/80 backdrop-blur-[1px] z-20 flex items-center justify-center rounded-lg">
        <div className="flex items-center gap-2 text-slate-500 text-xs">
          <div className="animate-spin w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full" />
          数据加载中...
        </div>
      </div>
    );
  };

  // AI analysis shimmer placeholder
  const AiShimmer: React.FC<{ lines?: number }> = ({ lines = 2 }) => (
    <div className="space-y-2 animate-pulse">
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i} className="flex items-center gap-2">
          <Sparkles className="w-3 h-3 text-blue-400 flex-shrink-0 animate-pulse" />
          <div className={`h-3 bg-blue-100 rounded ${i === lines - 1 ? 'w-2/3' : 'w-full'}`} />
        </div>
      ))}
      <div className="text-[10px] text-blue-400">AI 分析生成中...</div>
    </div>
  );

  if (loading && !data) {
    return (
      <div className="flex-1 flex items-center justify-center text-slate-400">
        <div className="animate-spin w-6 h-6 border-2 border-blue-500 border-t-transparent rounded-full mr-2" />
        加载中...
      </div>
    );
  }

  if (!data) return <div className="flex-1 flex items-center justify-center text-slate-400">无数据</div>;

  // Drill-down view
  if (drillDown) {
    return <DrillDownView data={data} module={drillDown} onBack={() => setDrillDown(null)} />;
  }

  const s = data.summary;

  const kpiRow1 = [
    { label: '合同金额（亿元）', value: s.contract_amount_yi, yoy: s.amount_yoy, color: 'text-blue-600' },
    { label: '未执行订单数量', value: s.unfulfilled_orders, yoy: s.unfulfilled_yoy, color: 'text-blue-700' },
    { label: '合同执行完成率', value: `${s.completion_rate}%`, yoy: s.completion_rate_yoy, color: 'text-blue-600' },
    { label: '合同数量（吨）', value: s.contract_qty, yoy: s.contract_qty_yoy, color: 'text-green-600' },
  ];

  const kpiRow2 = [
    { label: '已出数量（吨）', value: s.out_qty, yoy: s.out_qty_yoy, color: 'text-slate-800' },
    { label: '未出数量（吨）', value: s.not_out_qty, yoy: s.not_out_qty_yoy, color: 'text-green-600' },
    { label: '开票金额（万元）', value: s.invoice_amount_wan, yoy: s.invoice_yoy, color: 'text-green-600' },
    { label: '未开票金额（万元）', value: s.uninvoiced_amount_wan, yoy: s.uninvoiced_yoy, color: 'text-green-600' },
  ];

  return (
    <div ref={reportRootRef} className="flex-1 overflow-auto bg-slate-50 p-5">
      {/* ── Header ── */}
      <div className="flex items-center justify-between mb-5">
        <h1 className="text-xl font-bold text-blue-700">销售合同执行情况</h1>
        <div className="text-xs text-slate-400">最后更新时间：{lastUpdate}</div>
      </div>

      {/* ── Filters ── */}
      <div className="flex items-center gap-4 mb-3 bg-white rounded-lg border border-slate-200 px-4 py-2.5">
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
          <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)}
            className="border border-slate-300 rounded px-2 py-1 text-sm bg-white" />
          <span className="text-slate-400 text-xs">至</span>
          <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)}
            className="border border-slate-300 rounded px-2 py-1 text-sm bg-white" />
        </div>
        <div className="flex items-center gap-1.5 text-sm">
          <span className="text-slate-500">物料分类:</span>
          <select value={selectedMaterial} onChange={(e) => setSelectedMaterial(e.target.value)}
            className="border border-slate-300 rounded px-2 py-1 text-sm bg-white">
            <option value="">全部</option>
            {data.available_materials.map(m => <option key={m} value={m}>{m}</option>)}
          </select>
        </div>
        <div className="flex-1" />
        <button onClick={handleQuery} disabled={loading} className="px-4 py-1.5 bg-blue-600 text-white rounded text-sm font-medium hover:bg-blue-700 disabled:opacity-60 disabled:cursor-not-allowed flex items-center gap-1.5">
          {loading && <div className="animate-spin w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full" />}
          查询
        </button>
        <button onClick={handleReset} disabled={loading} className="px-4 py-1.5 bg-white text-slate-600 border border-slate-300 rounded text-sm font-medium hover:bg-slate-50 flex items-center gap-1 disabled:opacity-60 disabled:cursor-not-allowed">
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
                <Image className="w-3.5 h-3.5" /> 导出整页图片
              </button>
            </div>
          )}
        </div>
      </div>

      <div className="grid grid-cols-12 gap-4 items-start">
        <div className="col-span-8 flex flex-col gap-4">
          {/* KPI cards */}
          <div className="relative" ref={kpiRef}>
            <div className="absolute top-2 right-2 z-30">
              <ModuleMenu
                moduleRef={kpiRef}
                onDrillDown={() => setDrillDown('kpi')}
                onExcelDownload={() => exportExcel([
                  { 指标: '合同金额（亿元）', 值: s.contract_amount_yi, 同比: s.amount_yoy },
                  { 指标: '未执行订单数量', 值: s.unfulfilled_orders, 同比: s.unfulfilled_yoy },
                  { 指标: '合同执行完成率（%）', 值: s.completion_rate, 同比: s.completion_rate_yoy },
                  { 指标: '合同数量（吨）', 值: s.contract_qty, 同比: s.contract_qty_yoy },
                  { 指标: '已出数量（吨）', 值: s.out_qty, 同比: s.out_qty_yoy },
                  { 指标: '未出数量（吨）', 值: s.not_out_qty, 同比: s.not_out_qty_yoy },
                  { 指标: '开票金额（万元）', 值: s.invoice_amount_wan, 同比: s.invoice_yoy },
                  { 指标: '未开票金额（万元）', 值: s.uninvoiced_amount_wan, 同比: s.uninvoiced_yoy },
                ], 'KPI指标')}
              />
            </div>
            <LoadingOverlay show={loading} />
            <div className="grid grid-cols-4 gap-3 mb-3">
              {kpiRow1.map(k => (
                <KPICard key={k.label} label={k.label} value={k.value} yoy={k.yoy} />
              ))}
            </div>
            <div className="grid grid-cols-4 gap-3">
              {kpiRow2.map(k => (
                <KPICard key={k.label} label={k.label} value={k.value} yoy={k.yoy} color={k.color} />
              ))}
            </div>
          </div>

          {/* Chart */}
          <div className="bg-white rounded-lg border border-slate-200 p-4 pb-5 relative" ref={chartRef}>
            <LoadingOverlay show={loading} />
            <div className="flex items-center justify-between">
              <div className="text-base font-bold text-slate-800 mb-1">各产品合同执行情况</div>
              <ModuleMenu
                moduleRef={chartRef}
                onDrillDown={() => setDrillDown('chart')}
                onExcelDownload={() => exportExcel(
                  data.execution_chart.map(e => ({
                    产品名称: e.product,
                    合同数量: e.contract_qty,
                    已出数量: e.out_qty,
                    未出数量: e.contract_qty - e.out_qty,
                    执行率: `${e.execution_rate}%`,
                  })), '各产品合同执行'
                )}
              />
            </div>
            <p className="text-xs text-slate-500 leading-relaxed mb-2">{aiLoading && !chartAnalysis.top ? <AiShimmer lines={1} /> : chartAnalysis.top}</p>

            <div className="flex items-center justify-center gap-6 mb-2 text-xs text-slate-500">
              <span className="flex items-center gap-1.5"><span className="w-3 h-3 rounded-sm bg-blue-500 inline-block" /> 已出数量</span>
              <span className="flex items-center gap-1.5"><span className="w-3 h-3 rounded-sm bg-slate-200 inline-block" /> 合同数量</span>
              <span className="flex items-center gap-1.5"><span className="w-6 h-0.5 bg-green-400 inline-block" style={{ position: 'relative', top: '-1px' }} /> 执行率</span>
            </div>

            <div className="flex justify-between mb-1 text-[10px] text-slate-400">
              <span>数量（吨）</span>
              <span>执行率</span>
            </div>

            <div style={{ height: Math.max(320, chartData.length * 40) + 'px' }}>
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={chartData} barGap={-32} margin={{ top: 20, right: 40, bottom: 20, left: 10 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={chart.grid} vertical={false} />
                  <XAxis dataKey="name" tick={{ fontSize: 9, fill: chart.tick }} axisLine={{ stroke: chart.axis }} tickLine={false} interval={0} angle={-15} textAnchor="end" height={50} />
                  <YAxis yAxisId="left" tick={{ fontSize: 10, fill: chart.tickMuted }} axisLine={false} tickLine={false} />
                  <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 10, fill: chart.tickMuted }} axisLine={false} tickLine={false}
                    domain={[0, 100]} tickFormatter={(v: number) => `${v}%`} />
                  <RechartsTooltip
                    contentStyle={tooltipStyle}
                    formatter={(value: number, name: string) => {
                      if (name === '执行率') return [`${value}%`, name];
                      return [value.toLocaleString() + ' 吨', name];
                    }}
                  />
                  <Bar yAxisId="left" dataKey="合同数量" fill={contractBarFill} radius={[3, 3, 0, 0]} barSize={32}>
                    <LabelList dataKey="合同数量" position="top" style={{ fontSize: 9, fill: chart.tick, fontWeight: 500 }} />
                  </Bar>
                  <Bar yAxisId="left" dataKey="已出数量" fill="#3b82f6" radius={[3, 3, 0, 0]} barSize={32}>
                    <LabelList dataKey="已出数量" position="insideTop" style={{ fontSize: 9, fill: '#ffffff', fontWeight: 600 }} offset={6} />
                  </Bar>
                  <Line yAxisId="right" type="monotone" dataKey="执行率" stroke="#86efac" strokeWidth={2.5}
                    dot={{ r: 4, fill: '#86efac', stroke: '#86efac' }}
                    label={{ position: 'top', fontSize: 10, fill: '#22c55e', fontWeight: 600, formatter: (v: number) => `${v}%` }}
                  />
                </ComposedChart>
              </ResponsiveContainer>
            </div>

            <p className="mt-3 text-xs text-slate-500 leading-relaxed">{aiLoading && !chartAnalysis.bottom ? <AiShimmer lines={1} /> : chartAnalysis.bottom}</p>
          </div>

          <div className="bg-white rounded-lg border border-slate-200 p-5 relative" ref={chartAnalysisRef}>
            <LoadingOverlay show={loading} />
            <div className="flex items-center justify-between mb-3">
              <div className="text-base font-bold text-slate-800">图表分析</div>
              <ModuleMenu
                moduleRef={chartAnalysisRef}
                hasDrillDown={false}
                onExcelDownload={() => exportExcel([{
                  分析上: chartAnalysis.top,
                  分析下: chartAnalysis.bottom,
                }], '图表分析')}
              />
            </div>
            {aiLoading && !chartAnalysis.top ? (
              <AiShimmer lines={2} />
            ) : (
              <>
                <p className="text-sm text-slate-700 leading-relaxed">{chartAnalysis.top}</p>
                <p className="text-sm text-slate-700 leading-relaxed mt-2">{chartAnalysis.bottom}</p>
              </>
            )}
          </div>
        </div>

        <div className="col-span-4 flex flex-col gap-4">
          {/* Unshipped contracts table */}
          <div className="bg-white rounded-lg border border-slate-200 p-4 flex flex-col relative" ref={unshippedRef}>
            <LoadingOverlay show={loading} />
            <div className="flex items-center justify-between mb-3">
              <div className="text-sm font-bold text-slate-800">未出库数量30吨以上的合同（按产品）</div>
              <ModuleMenu
                moduleRef={unshippedRef}
                onDrillDown={() => setDrillDown('unshipped')}
                onExcelDownload={() => exportExcel(
                  data.unshipped_contracts.map(c => ({
                    产品名称: c.product,
                    产品类型: c.product_type,
                    合同编号: c.bill_no,
                    客户名称: c.customer,
                    交货日期: c.delivery_date,
                    合同数量: c.contract_qty,
                    未出数量: c.unshipped_qty,
                  })), '未出库合同'
                )}
              />
            </div>
            <div className="overflow-auto flex-1" style={{ maxHeight: '340px' }}>
              <table className="w-full text-[11px] border-collapse">
                <thead className="sticky top-0 z-10">
                  <tr className="bg-slate-50 border-b border-slate-200">
                    <th className="text-left px-2 py-2 font-semibold text-slate-500">产品名称</th>
                    <th className="text-left px-2 py-2 font-semibold text-slate-500">合同编号</th>
                    <th className="text-left px-2 py-2 font-semibold text-slate-500">客户名称</th>
                    <th className="text-left px-2 py-2 font-semibold text-slate-500">交货日期</th>
                    <th className="text-right px-2 py-2 font-semibold text-slate-500">合同数量</th>
                    <th className="text-right px-2 py-2 font-semibold text-slate-500">未出数量</th>
                  </tr>
                </thead>
                <tbody>
                  {(() => {
                    const rows: React.ReactNode[] = [];
                    let lastProduct = '';
                    data.unshipped_contracts.forEach((c, i) => {
                      const showProduct = c.product !== lastProduct;
                      if (showProduct) lastProduct = c.product;
                      rows.push(
                        <tr key={i} className={`border-b border-slate-100 ${i % 2 === 0 ? 'bg-white' : 'bg-slate-50/30'}`}>
                          <td className="px-2 py-2 text-slate-700 font-medium" title={c.product}>
                            {showProduct ? c.product : ''}
                          </td>
                          <td className="px-2 py-2 font-mono text-slate-500 text-[10px]">{c.bill_no}</td>
                          <td className="px-2 py-2 text-slate-600 truncate max-w-[90px]" title={c.customer}>
                            {c.customer.length > 10 ? c.customer.slice(0, 10) + '…' : c.customer}
                          </td>
                          <td className="px-2 py-2 text-slate-500">{c.delivery_date}</td>
                          <td className="text-right px-2 py-2 font-mono text-slate-600">{c.contract_qty.toLocaleString()}吨</td>
                          <td className={`text-right px-2 py-2 font-mono font-bold ${c.unshipped_qty >= 100 ? 'text-red-500' : c.unshipped_qty >= 60 ? 'text-orange-500' : 'text-yellow-600'
                            }`}>{c.unshipped_qty.toLocaleString()}吨</td>
                        </tr>
                      );
                    });
                    if (data.unshipped_contracts.length === 0) {
                      rows.push(
                        <tr key="empty"><td colSpan={6} className="text-center py-6 text-slate-400">暂无数据</td></tr>
                      );
                    }
                    return rows;
                  })()}
                </tbody>
              </table>
            </div>
            {aiLoading && !unshippedAnalysis ? (
              <div className="mt-3"><AiShimmer lines={2} /></div>
            ) : unshippedAnalysis ? (
              <p className="mt-3 text-[11px] text-blue-600 leading-relaxed">
                {unshippedAnalysis}
              </p>
            ) : null}
          </div>

          {/* Summary analysis panel */}
          <div className="bg-white rounded-lg border border-slate-200 p-4 relative" ref={summaryRef}>
            <LoadingOverlay show={loading} />
            <div className="flex items-center justify-between mb-3">
              <div className="text-sm font-semibold text-slate-700">销售合同执行情况总结分析</div>
              <ModuleMenu
                moduleRef={summaryRef}
                onDrillDown={() => setDrillDown('summary')}
                onExcelDownload={() => exportExcel([{
                  总结: summaryAnalysis.paragraph,
                  要点: summaryAnalysis.bullets.join('\n'),
                }], '总结分析')}
              />
            </div>
            {aiLoading && !summaryAnalysis.paragraph ? (
              <div className="mb-3"><AiShimmer lines={3} /></div>
            ) : (
              <p className="text-xs text-slate-600 leading-relaxed mb-3">
                {summaryAnalysis.paragraph}
              </p>
            )}

            {/* Bullet points in rounded box */}
            <div className="bg-slate-50 rounded-lg p-3">
              {aiLoading && summaryAnalysis.bullets.length === 0 ? (
                <AiShimmer lines={5} />
              ) : (
                <ul className="space-y-2">
                  {summaryAnalysis.bullets.map((bullet, i) => (
                    <li key={i} className="flex gap-2 items-start">
                      <span className="mt-1.5 w-2 h-2 rounded-full bg-blue-500 flex-shrink-0" />
                      <span className="text-xs text-slate-700 leading-relaxed">{bullet}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </div>
      </div>

    </div>
  );
};
