import React, { useEffect, useState } from 'react';
import { API_BASE_URL, fetchWithRetry } from '../services/api';
import { useLanguage, type Language } from '../lib/language';
import { NO_DATA_LABEL, formatSignedPriceChange, getPriceChangeBadgeClass, getPriceChangeTextClass } from '../lib/priceChange';

interface AdjustmentStep {
  step: number;
  label: string;
  value: number;
  delta: number;
  note: string;
}

interface PricingItem {
  product_id: string;
  name_cn: string;
  name_en: string;
  latest_market_price: number;
  recommended_price: number;
  price_range: [number, number];
  price_delta_vs_market: number;
  action: string;
  action_cn: string;
  confidence: number;
  pricing_formula: string;
  unit: string;
  adjustment_steps?: AdjustmentStep[];
}

const ACTION_STYLE: Record<string, { bg: string; border: string; text: string; icon: string }> = {
  raise_price: { bg: 'bg-red-50', border: 'border-red-200', text: 'text-red-700', icon: '▲' },
  hold: { bg: 'bg-slate-50', border: 'border-slate-200', text: 'text-slate-600', icon: '■' },
  reduce_price: { bg: 'bg-green-50', border: 'border-green-200', text: 'text-green-700', icon: '▼' },
  lower_aggressively: { bg: 'bg-emerald-50', border: 'border-emerald-300', text: 'text-emerald-700', icon: '▼▼' },
  raise_aggressively: { bg: 'bg-rose-50', border: 'border-rose-300', text: 'text-rose-700', icon: '▲▲' },
};

const FORMULA_LABELS: Record<Language, Record<string, string>> = {
  zh: {
    A: '成本加成',
    B: '竞品参考',
    C: 'SINOPEC锚定',
  },
  en: {
    A: 'Cost Plus',
    B: 'Competitor Reference',
    C: 'SINOPEC Anchor',
  },
};

const ACTION_LABELS: Record<Language, Record<string, string>> = {
  zh: {
    raise_price: '建议上调',
    hold: '建议持平',
    reduce_price: '建议下调',
    lower_aggressively: '建议大幅下调',
    raise_aggressively: '建议大幅上调',
  },
  en: {
    raise_price: 'Raise',
    hold: 'Hold',
    reduce_price: 'Lower',
    lower_aggressively: 'Lower Aggressively',
    raise_aggressively: 'Raise Aggressively',
  },
};

const ConfidenceBar: React.FC<{ value: number }> = ({ value }) => {
  const pct = Math.round(value * 100);
  const color = pct >= 70 ? 'bg-green-500' : pct >= 40 ? 'bg-amber-500' : 'bg-red-500';
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 bg-slate-200 rounded-full overflow-hidden">
        <div className={`h-full ${color} rounded-full transition-all`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[10px] font-mono text-slate-500 w-8 text-right">{pct}%</span>
    </div>
  );
};

interface PricingCardProps {
  item: PricingItem;
  language: Language;
  locale: string;
  text: {
    formula: string;
    currentMarketPrice: string;
    recommendedPrice: string;
    spread: string;
    range: string;
    confidence: string;
    collapseLogic: string;
    viewLogic: (count: number) => string;
  };
}

const PricingCard: React.FC<PricingCardProps> = ({ item, language, locale, text }) => {
  const [expanded, setExpanded] = useState(false);
  const style = ACTION_STYLE[item.action] || ACTION_STYLE.hold;
  const delta = item.price_delta_vs_market;
  const steps = item.adjustment_steps || [];
  const formulaLabel = FORMULA_LABELS[language][item.pricing_formula] || item.pricing_formula;
  const actionLabel = ACTION_LABELS[language][item.action] || (language === 'en' ? item.action : item.action_cn);
  const primaryName = language === 'en' ? item.name_en : item.name_cn;
  const secondaryName = language === 'en' ? item.name_cn : item.name_en;

  return (
    <div className={`self-start rounded-xl border-2 ${style.border} ${style.bg} p-4 sm:p-5 flex flex-col gap-3 shadow-sm hover:shadow-md transition-shadow`}>
      {/* Header */}
      <div className="flex items-start justify-between gap-2 flex-wrap">
        <div>
          <div className="text-base font-bold text-slate-800">{primaryName}</div>
          <div className="text-[11px] text-slate-400">{secondaryName} · {text.formula} {item.pricing_formula} ({formulaLabel})</div>
        </div>
        <span className={`px-3 py-1 rounded-full text-xs font-bold border ${style.text} ${style.bg} ${style.border}`}>
          {style.icon} {actionLabel}
        </span>
      </div>

      {/* Prices */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
        <div>
          <div className="text-[10px] text-slate-500 uppercase tracking-wider mb-0.5">{text.currentMarketPrice}</div>
          <div className="text-lg font-mono font-bold text-slate-700">
            ¥{item.latest_market_price.toLocaleString(locale)}
          </div>
        </div>
        <div>
          <div className="text-[10px] text-slate-500 uppercase tracking-wider mb-0.5">{text.recommendedPrice}</div>
          <div className={`text-xl font-mono font-black ${style.text}`}>
            ¥{item.recommended_price.toLocaleString(locale)}
          </div>
        </div>
      </div>

      {/* Delta & Range */}
      <div className="flex items-center justify-between text-xs gap-2 flex-wrap">
        <span className={`font-mono font-semibold ${getPriceChangeTextClass(delta)}`}>
          {text.spread}: {formatSignedPriceChange(delta, {}, locale)} /{item.unit}
        </span>
        <span className="text-slate-400">
          {text.range}: {item.price_range[0].toLocaleString(locale)} – {item.price_range[1].toLocaleString(locale)}
        </span>
      </div>

      {/* Confidence */}
      <div>
        <div className="text-[10px] text-slate-500 mb-1">{text.confidence}</div>
        <ConfidenceBar value={item.confidence} />
      </div>

      {/* Adjustment steps (expandable) */}
      {steps.length > 0 && (
        <div className="border-t border-slate-200/60 pt-2">
          <button
            onClick={() => setExpanded(!expanded)}
            className="text-[10px] text-blue-600 hover:text-blue-800 font-semibold flex items-center gap-1"
          >
            <span className={`inline-block transition-transform ${expanded ? 'rotate-90' : ''}`}>▶</span>
            {expanded ? text.collapseLogic : text.viewLogic(steps.length)}
          </button>
          {expanded && (
            <div className="mt-2 space-y-1">
              {steps.map((s) => (
                <div key={s.step} className="flex items-start gap-2 text-[10px]">
                  <span className="flex-none w-4 h-4 rounded-full bg-slate-200 text-slate-600 flex items-center justify-center text-[9px] font-bold mt-0.5">
                    {s.step}
                  </span>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-semibold text-slate-700 break-words">{s.label}</span>
                      {s.delta !== 0 && (
                        <span className={`font-mono text-[9px] px-1 rounded ${getPriceChangeBadgeClass(s.delta)}`}>
                          {formatSignedPriceChange(s.delta, {}, locale)}
                        </span>
                      )}
                      <span className="font-mono text-slate-500 ml-auto flex-none">→ ¥{s.value.toLocaleString(locale)}</span>
                    </div>
                    {s.note && <div className="text-slate-400 break-words">{s.note}</div>}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export const PricingActions: React.FC = () => {
  const { language, locale } = useLanguage();
  const text = language === 'en'
    ? {
      formula: 'Formula',
      currentMarketPrice: 'Market Price',
      recommendedPrice: 'Recommended Price',
      spread: 'Spread',
      range: 'Range',
      confidence: 'Confidence',
      collapseLogic: 'Hide pricing logic',
      viewLogic: (count: number) => `View pricing logic (${count} steps)`,
      computingAdvice: 'Computing pricing recommendations...',
      pricingEngineBusy: 'The pricing engine is still running. Please wait...',
      firstLoad: 'The first load runs the forecast model once (about 15s).',
      noData: NO_DATA_LABEL,
      lowerCount: (count: number) => `▼ ${count} lower-price recommendations`,
      raiseCount: (count: number) => `▲ ${count} raise-price recommendations`,
      holdCount: (count: number) => `■ ${count} hold recommendations`,
      summary: 'Based on the 9-step pricing engine | Source: market data + ERP + forecast model',
    }
    : {
      formula: '公式',
      currentMarketPrice: '当前市场价',
      recommendedPrice: '建议定价',
      spread: '差价',
      range: '区间',
      confidence: '决策置信度',
      collapseLogic: '收起调价逻辑',
      viewLogic: (count: number) => `查看调价逻辑 (${count}步)`,
      computingAdvice: '计算定价建议...',
      pricingEngineBusy: '定价引擎正在计算中，请稍候...',
      firstLoad: '首次加载需要运行预测模型 (约15秒)',
      noData: NO_DATA_LABEL,
      lowerCount: (count: number) => `▼ ${count} 项建议下调`,
      raiseCount: (count: number) => `▲ ${count} 项建议上调`,
      holdCount: (count: number) => `■ ${count} 项建议持平`,
      summary: '基于9步定价决策引擎 | 来源: 市场数据 + ERP + 预测模型',
    };
  const [items, setItems] = useState<PricingItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [computing, setComputing] = useState(false);

  useEffect(() => {
    let mounted = true;
    let pollTimer: ReturnType<typeof setTimeout> | null = null;

    const load = async () => {
      try {
        const res = await fetchWithRetry(`${API_BASE_URL}/api/v2/pricing/actions/downstream`);
        const json = await res.json();
        if (!mounted) return;

        if (json.computing && (!json.items || json.items.length === 0)) {
          // Backend is still computing — show computing state and poll faster
          setComputing(true);
          setLoading(false);
          pollTimer = setTimeout(load, 3000); // retry in 3s
        } else {
          setItems(json.items || []);
          setComputing(false);
          setLoading(false);
        }
      } catch {
        if (mounted) setLoading(false);
      }
    };
    load();
    const interval = setInterval(load, 120000);
    return () => { mounted = false; clearInterval(interval); if (pollTimer) clearTimeout(pollTimer); };
  }, []);

  if (loading) {
    return (
      <div className="flex min-h-[380px] items-center justify-center p-4">
        <div className="animate-pulse text-sm text-slate-400">{text.computingAdvice}</div>
      </div>
    );
  }

  if (computing) {
    return (
      <div className="flex min-h-[380px] items-center justify-center p-4">
        <div className="flex flex-col items-center gap-2">
          <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
          <div className="text-sm text-slate-500">{text.pricingEngineBusy}</div>
          <div className="text-[10px] text-slate-400">{text.firstLoad}</div>
        </div>
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className="flex min-h-[380px] items-center justify-center p-4">
        <div className="text-sm text-slate-400">{text.noData}</div>
      </div>
    );
  }

  return (
    <div className="flex min-h-[380px] flex-col overflow-auto p-4">
      {/* Summary */}
      <div className="mb-3 flex items-center gap-3 flex-wrap">
        {items.filter(i => i.action.includes('reduce') || i.action.includes('lower')).length > 0 && (
          <span className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-green-100 border border-green-200 text-green-700 text-xs font-semibold">
            {text.lowerCount(items.filter(i => i.action.includes('reduce') || i.action.includes('lower')).length)}
          </span>
        )}
        {items.filter(i => i.action.includes('raise')).length > 0 && (
          <span className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-red-100 border border-red-200 text-red-700 text-xs font-semibold">
            {text.raiseCount(items.filter(i => i.action.includes('raise')).length)}
          </span>
        )}
        {items.filter(i => i.action === 'hold').length > 0 && (
          <span className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-slate-100 border border-slate-200 text-slate-600 text-xs font-semibold">
            {text.holdCount(items.filter(i => i.action === 'hold').length)}
          </span>
        )}
        <span className="text-[10px] text-slate-400 ml-auto">{text.summary}</span>
      </div>

      {/* Card grid */}
      <div className="grid grid-cols-1 gap-4 items-start content-start auto-rows-max sm:grid-cols-2 xl:grid-cols-4">
        {items.map(item => (
          <PricingCard key={item.product_id} item={item} language={language} locale={locale} text={text} />
        ))}
      </div>
    </div>
  );
};
