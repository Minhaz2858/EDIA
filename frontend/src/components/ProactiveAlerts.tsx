import {
  Bell,
  Clock,
  Search,
  TrendingDown,
  TrendingUp,
  X,
} from "lucide-react";
import { useEffect, useRef } from "react";
import { useLanguage } from '../lib/language';
import { formatSignedPriceChange, getPriceChangeTextClass } from '../lib/priceChange';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface AlertItem {
  id: string;
  type: "price_spike" | "price_drop";
  severity: "high" | "medium" | "low";
  product: string;
  product_name: string;
  message: string;
  change_7d_pct: number;
  price: number;
  timestamp: string;       // date string e.g. "2026-03-15"
  triggered_at: string;   // full ISO datetime
  read_at: string | null;
  source_text?: string;
  source?: {
    system?: string;
    table?: string;
    channel?: string;
    product_id?: string;
    price_type?: string;
    as_of_date?: string | null;
    alert_detector?: string;
  };
}

interface Props {
  alerts: AlertItem[];
  lastScan: string | null;
  onClose: () => void;
  onInvestigate: (question: string) => void;
  onDismiss: (alertId: string) => void;
}

// ---------------------------------------------------------------------------
// Chinese Product Name Map
// ---------------------------------------------------------------------------

const PRODUCT_NAMES = {
  zh: {
    crude_oil: "原油",
    naphtha: "石脑油",
    cracked_c5: "裂解碳五",
    cracked_c9: "裂解碳九",
    isoprene: "异戊二烯",
    piperylene: "间戊二烯",
    sis: "SIS热塑弹性体",
    c5_resin: "C5石油树脂",
    c9_resin: "C9石油树脂",
    raffinate_c5: "抽余碳五",
    styrene: "苯乙烯",
    blowing_agent: "发泡剂",
    dcpd: "双环戊二烯",
    hydrogenated_c9: "加氢碳九",
  },
  en: {
    crude_oil: "Crude Oil",
    naphtha: "Naphtha",
    cracked_c5: "Cracked C5",
    cracked_c9: "Cracked C9",
    isoprene: "Isoprene",
    piperylene: "Piperylene",
    sis: "SIS Thermoplastic Elastomer",
    c5_resin: "C5 Resin",
    c9_resin: "C9 Resin",
    raffinate_c5: "Raffinate C5",
    styrene: "Styrene",
    blowing_agent: "Blowing Agent",
    dcpd: "DCPD",
    hydrogenated_c9: "Hydrogenated C9",
  },
} as const;

// ---------------------------------------------------------------------------
// Investigation Question Builder
// ---------------------------------------------------------------------------

function buildQuestion(alert: AlertItem, language: 'zh' | 'en'): string {
  const productName = PRODUCT_NAMES[language][alert.product as keyof typeof PRODUCT_NAMES.zh] || alert.product_name;
  const pct = alert.change_7d_pct;
  const sign = pct >= 0 ? "+" : "";
  const changeStr = language === 'en'
    ? `${alert.type === 'price_spike' ? '7d increase' : '7d decrease'} ${sign}${pct.toFixed(1)}%`
    : `7日${alert.type === "price_spike" ? "涨幅" : "跌幅"}${sign}${pct.toFixed(1)}%`;

  if (alert.product === "crude_oil" || alert.product === "naphtha") {
    return language === 'en'
      ? `${productName} ${changeStr} (current ${alert.price.toLocaleString()} CNY/ton). What is the cost pass-through impact on our C5/C9 product line and pricing strategy? Should we adjust quotes now?`
      : `${productName}${changeStr}（当前${alert.price.toLocaleString()}元/吨），对我们C5/C9产品线的成本传导和报价策略有何具体影响？当前是否需要调整报价？`;
  }
  if (alert.type === "price_spike") {
    return language === 'en'
      ? `${productName} ${changeStr} (current ${alert.price.toLocaleString()} CNY/ton). Have we entered a price increase window, and how should inventory level and shipment pace be coordinated?`
      : `${productName}${changeStr}（当前${alert.price.toLocaleString()}元/吨），当前是否进入提价窗口？库存水位和出货节奏应如何配合？`;
  }
  return language === 'en'
    ? `${productName} ${changeStr} (current ${alert.price.toLocaleString()} CNY/ton). How should current inventory be handled, do quotes need to follow downward, and how should shipment pace be adjusted?`
    : `${productName}${changeStr}（当前${alert.price.toLocaleString()}元/吨），当前库存应如何处置？报价是否需要跟进下调？出货节奏如何调整？`;
}

// ---------------------------------------------------------------------------
// Severity config
// ---------------------------------------------------------------------------

const SEVERITY_CONFIG = {
  high: {
    badge: "bg-red-100 text-red-700 border border-red-200",
    border: "border-l-red-500",
    label: "HIGH",
  },
  medium: {
    badge: "bg-amber-100 text-amber-700 border border-amber-200",
    border: "border-l-amber-400",
    label: "MEDIUM",
  },
  low: {
    badge: "bg-slate-100 text-slate-600 border border-slate-200",
    border: "border-l-slate-400",
    label: "LOW",
  },
};

// ---------------------------------------------------------------------------
// Alert Card
// ---------------------------------------------------------------------------

function AlertCard({
  alert,
  language,
  onInvestigate,
  onDismiss,
}: {
  alert: AlertItem;
  language: 'zh' | 'en';
  onInvestigate: (q: string) => void;
  onDismiss: (id: string) => void;
}) {
  const text = language === 'en'
    ? {
      high: 'HIGH',
      medium: 'MEDIUM',
      low: 'LOW',
      priceSpike: 'price spike',
      priceDrop: 'price drop',
      price: 'Price',
      move7d: '7d Change',
      perTon: '/ton',
      investigate: 'Investigate',
      dismissAlert: 'Dismiss alert',
      source: 'Source',
      increaseDescription: 'price increased over the last 7 days beyond the normal range. Historically, this magnitude often signals follow-on cost pressure.',
      decreaseDescription: 'price fell over the last 7 days, indicating weaker market sentiment. Inventory response should be reassessed.',
    }
    : {
      high: 'HIGH',
      medium: 'MEDIUM',
      low: 'LOW',
      priceSpike: '价格急涨',
      priceDrop: '价格下跌',
      price: '价格',
      move7d: '7日变幅',
      perTon: '/吨',
      investigate: 'Investigate',
      dismissAlert: 'Dismiss alert',
      source: '来源',
      increaseDescription: '价格在过去7天内上涨',
      decreaseDescription: '价格在过去7天内下跌',
    };
  const sev = SEVERITY_CONFIG[alert.severity] ?? SEVERITY_CONFIG.low;
  const isSpike = alert.type === "price_spike";
  const pct = alert.change_7d_pct;
  const pctDisplay = formatSignedPriceChange(pct, { minimumFractionDigits: 1, maximumFractionDigits: 1 }, undefined, "%");
  const productName = PRODUCT_NAMES[language][alert.product as keyof typeof PRODUCT_NAMES.zh] || alert.product_name;
  const sourceText = alert.source_text || "-";

  return (
    <div
      className={`relative bg-white rounded-xl border border-slate-200 border-l-4 ${sev.border} shadow-sm p-4 flex flex-col gap-3`}
    >
      {/* Dismiss */}
      <button
        type="button"
        onClick={() => onDismiss(alert.id)}
        className="absolute top-3 right-3 text-slate-400 hover:text-slate-600 transition-colors"
        aria-label={text.dismissAlert}
      >
        <X size={14} />
      </button>

      {/* Header */}
      <div className="flex items-start gap-2.5 pr-5">
        <div
          className={`mt-0.5 shrink-0 ${getPriceChangeTextClass(pct)}`}
        >
          {isSpike ? <TrendingUp size={18} /> : <TrendingDown size={18} />}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-semibold text-sm text-slate-800">
              {productName} {isSpike ? text.priceSpike : text.priceDrop}
            </span>
            <span
              className={`text-[11px] font-semibold px-1.5 py-0.5 rounded-full ${sev.badge}`}
            >
              {sev.label}
            </span>
          </div>
          <p className="text-xs text-slate-500 mt-0.5 leading-tight">
            {alert.product_name} · {alert.timestamp}
          </p>
          <p className="text-[11px] text-slate-400 mt-0.5 leading-tight break-all">
            {text.source}: {sourceText}
          </p>
        </div>
      </div>

      {/* Description */}
      <p className="text-xs text-slate-600 leading-relaxed">
        {language === 'en'
          ? isSpike
            ? `${productName} ${text.increaseDescription}`
            : `${productName} ${text.decreaseDescription}`
          : isSpike
            ? `${productName}${text.increaseDescription}${pctDisplay}，超出正常波动区间。历史上这一幅度往往预示后续成本压力。`
            : `${productName}${text.decreaseDescription}${Math.abs(pct).toFixed(1)}%，市场情绪偏弱。需评估库存应对策略。`}
      </p>

      {/* Metrics */}
      <div className="grid grid-cols-2 gap-2">
        <div className="bg-slate-50 rounded-lg p-2.5">
          <p className="text-[10px] text-slate-400 uppercase tracking-wide mb-0.5">
            {text.price}
          </p>
          <p className="text-sm font-bold text-slate-800">
            ¥{alert.price.toLocaleString()}
            <span className="text-[10px] font-normal text-slate-400 ml-1">
              {text.perTon}
            </span>
          </p>
        </div>
        <div className="bg-slate-50 rounded-lg p-2.5">
          <p className="text-[10px] text-slate-400 uppercase tracking-wide mb-0.5">
            {text.move7d}
          </p>
          <p
            className={`text-sm font-bold ${getPriceChangeTextClass(pct)}`}
          >
            {pctDisplay}
          </p>
        </div>
      </div>

      {/* Investigate Button */}
      <button
        type="button"
        onClick={() => onInvestigate(buildQuestion(alert, language))}
        className="w-full flex items-center justify-center gap-2 bg-slate-800 hover:bg-slate-700 active:bg-slate-900 text-white text-xs font-semibold py-2.5 rounded-lg transition-colors"
      >
        <Search size={13} />
        {text.investigate}
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Panel
// ---------------------------------------------------------------------------

export function ProactiveAlerts({ alerts, lastScan, onClose, onInvestigate, onDismiss }: Props) {
  const { language } = useLanguage();
  const text = language === 'en'
    ? {
      unknown: 'Unknown',
      aria: 'Proactive Alerts',
      title: 'Alerts',
      recentScan: 'Last scan',
      highRisk: 'high risk',
      mediumRisk: 'medium risk',
      closePanel: 'Close alerts panel',
      noAlerts: 'No market alerts',
      noAlertsHint: 'Prices are within the normal range.',
      footer: 'Click Investigate to jump into chat with the analysis question prefilled.',
    }
    : {
      unknown: '未知',
      aria: '预警通知',
      title: '预警通知',
      recentScan: '最近扫描',
      highRisk: '高危',
      mediumRisk: '中危',
      closePanel: '关闭预警面板',
      noAlerts: '暂无市场预警',
      noAlertsHint: '价格在正常波动范围内',
      footer: '点击 Investigate 将直接跳转到智能对话并自动填入分析问题',
    };
  const panelRef = useRef<HTMLDivElement>(null);

  // alerts prop is already filtered by parent (dismissed ones removed via API)

  // Close on Escape key
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const highCount = alerts.filter((a) => a.severity === "high").length;
  const medCount = alerts.filter((a) => a.severity === "medium").length;

  const formatScanTime = (iso: string | null) => {
    if (!iso) return text.unknown;
    try {
      const d = new Date(iso);
      return d.toLocaleTimeString(language === 'en' ? 'en-US' : 'zh-CN', {
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch {
      return iso;
    }
  };

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/30 z-40 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Slide-out Panel */}
      <div
        ref={panelRef}
        className="fixed right-0 top-14 bottom-0 w-96 bg-white shadow-2xl z-50 flex flex-col border-l border-slate-200"
        role="dialog"
        aria-label={text.aria}
      >
        {/* Panel Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100 shrink-0">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-slate-800 flex items-center justify-center">
              <Bell size={15} className="text-white" />
            </div>
            <div>
              <h2 className="text-sm font-bold text-slate-800">{text.title}</h2>
              <p className="text-[10px] text-slate-400 flex items-center gap-1 mt-0.5">
                <Clock size={10} />
                {text.recentScan} {formatScanTime(lastScan)}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {highCount > 0 && (
              <span className="text-[11px] font-semibold bg-red-100 text-red-700 border border-red-200 px-2 py-0.5 rounded-full">
                {highCount} {text.highRisk}
              </span>
            )}
            {medCount > 0 && (
              <span className="text-[11px] font-semibold bg-amber-100 text-amber-700 border border-amber-200 px-2 py-0.5 rounded-full">
                {medCount} {text.mediumRisk}
              </span>
            )}
            <button
              type="button"
              onClick={onClose}
              className="ml-1 text-slate-400 hover:text-slate-700 transition-colors p-1 rounded-lg hover:bg-slate-100"
              aria-label={text.closePanel}
            >
              <X size={18} />
            </button>
          </div>
        </div>

        {/* Alert List */}
        <div className="flex-1 overflow-y-auto p-4 space-y-3 scrollbar-thin scrollbar-thumb-slate-200">
          {alerts.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-center py-16 text-slate-400">
              <Bell size={36} className="mb-3 opacity-30" />
              <p className="text-sm font-medium">{text.noAlerts}</p>
              <p className="text-xs mt-1">{text.noAlertsHint}</p>
            </div>
          ) : (
            alerts.map((alert) => (
              <AlertCard
                key={alert.id}
                alert={alert}
                language={language}
                onInvestigate={(q) => {
                  onInvestigate(q);
                  onClose();
                }}
                onDismiss={onDismiss}
              />
            ))
          )}
        </div>

        {/* Footer hint */}
        {alerts.length > 0 && (
          <div className="shrink-0 px-5 py-3 border-t border-slate-100 bg-slate-50">
            <p className="text-[10px] text-slate-400 text-center">
              {text.footer}
            </p>
          </div>
        )}
      </div>
    </>
  );
}
