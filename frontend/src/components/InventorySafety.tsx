import React, { useEffect, useState } from 'react';
import { useLanguage } from '../lib/language';
import { API_BASE_URL, fetchWithRetry } from '../services/api';

interface InventoryItem {
  product_id: string;
  name_cn: string;
  inventory_tons: number;
  inventory_days: number;
  safety_min: number;
  safety_max: number;
  sales_30d: number;
  daily_consumption: number;
  status: 'danger' | 'warning' | 'safe' | 'excess';
}

const EXCLUDED_INVENTORY_PRODUCTS = new Set(['hydrogenated_c9', 'trimethylbenzene']);
const EXCLUDED_INVENTORY_NAMES = new Set(['加氢碳九', '混三甲苯']);

const SafeZoneBar: React.FC<{ item: InventoryItem }> = ({ item }) => {
  // Bar shows: 0 --- safety_min (red zone) --- safety_max (green zone) --- cap
  const cap = Math.max(item.safety_max * 1.5, item.inventory_tons * 1.1, 1);
  const minPct = (item.safety_min / cap) * 100;
  const maxPct = (item.safety_max / cap) * 100;
  const curPct = Math.min((item.inventory_tons / cap) * 100, 100);

  const markerColor =
    item.status === 'danger' ? '#dc2626' :
      item.status === 'warning' ? '#d97706' :
        item.status === 'safe' ? '#16a34a' : '#2563eb';

  return (
    <div className="relative h-6 w-full">
      {/* Background zones */}
      <div className="absolute inset-0 flex rounded-full overflow-hidden">
        {/* Danger zone: 0 → safety_min */}
        <div className="h-full bg-red-100" style={{ width: `${minPct}%` }} />
        {/* Safe zone: safety_min → safety_max */}
        <div className="h-full bg-green-100" style={{ width: `${maxPct - minPct}%` }} />
        {/* Excess zone: safety_max → cap */}
        <div className="h-full bg-blue-50" style={{ width: `${100 - maxPct}%` }} />
      </div>
      {/* Min marker */}
      <div
        className="absolute top-0 h-full w-px bg-red-300"
        style={{ left: `${minPct}%` }}
      />
      {/* Max marker */}
      <div
        className="absolute top-0 h-full w-px bg-green-400"
        style={{ left: `${maxPct}%` }}
      />
      {/* Current inventory indicator */}
      <div
        className="absolute top-1/2 -translate-y-1/2 w-3 h-3 rounded-full border-2 border-white shadow-md z-10"
        style={{ left: `${curPct}%`, marginLeft: -6, backgroundColor: markerColor }}
      />
      {/* Value label */}
      <div
        className="absolute top-1/2 -translate-y-1/2 text-[10px] font-bold z-10"
        style={{
          left: `${Math.min(curPct + 2, 85)}%`,
          color: markerColor,
        }}
      >
        {item.inventory_tons.toLocaleString()}t
      </div>
    </div>
  );
};

export const InventorySafety: React.FC = () => {
  const { language, locale } = useLanguage();
  const text = language === 'en'
    ? {
      status: {
        danger: 'Critical',
        warning: 'Warning',
        safe: 'Safe',
        excess: 'Excess',
      },
      loading: 'Loading inventory data...',
      empty: 'No inventory data available.',
      criticalCount: (count: number) => `${count} critical items`,
      warningCount: (count: number) => `${count} warning items`,
      summary: 'Green indicates the safety zone | Source: live ERP inventory',
      productName: 'Product',
      inventoryTons: 'Inventory (t)',
      inventoryDays: 'Days of Supply',
      safeRange: 'Safety Range',
      sales30d: '30d Sales',
      dailyUse: 'Daily Use',
      state: 'Status',
      daysSuffix: 'd',
      lowerBound: 'Min',
      upperBound: 'Max',
      dailyUnit: 't/day',
    }
    : {
      status: {
        danger: '危险',
        warning: '预警',
        safe: '安全',
        excess: '过剩',
      },
      loading: '加载库存数据...',
      empty: '暂无库存数据',
      criticalCount: (count: number) => `${count} 项危险`,
      warningCount: (count: number) => `${count} 项预警`,
      summary: '安全区间以绿色标示 | 数据来源: ERP 实时库存',
      productName: '产品名称',
      inventoryTons: '库存 (吨)',
      inventoryDays: '可用天数',
      safeRange: '安全区间',
      sales30d: '30日销量',
      dailyUse: '日均消耗',
      state: '状态',
      daysSuffix: '天',
      lowerBound: '下限',
      upperBound: '上限',
      dailyUnit: 't/日',
    };
  const statusConfig: Record<string, { label: string; color: string; bg: string; border: string }> = {
    danger: { label: text.status.danger, color: 'text-red-700', bg: 'bg-red-100', border: 'border-red-200' },
    warning: { label: text.status.warning, color: 'text-amber-700', bg: 'bg-amber-100', border: 'border-amber-200' },
    safe: { label: text.status.safe, color: 'text-green-700', bg: 'bg-green-100', border: 'border-green-200' },
    excess: { label: text.status.excess, color: 'text-blue-700', bg: 'bg-blue-100', border: 'border-blue-200' },
  };
  const [items, setItems] = useState<InventoryItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      try {
        const res = await fetchWithRetry(`${API_BASE_URL}/api/v2/inventory/safety`);
        const json = await res.json();
        if (mounted) {
          const filteredItems = (json.items || []).filter((item: InventoryItem) => (
            !EXCLUDED_INVENTORY_PRODUCTS.has(item.product_id)
            && !EXCLUDED_INVENTORY_NAMES.has(item.name_cn)
          ));
          setItems(filteredItems);
          setLoading(false);
        }
      } catch {
        if (mounted) setLoading(false);
      }
    };
    load();
    const interval = setInterval(load, 60000);
    return () => { mounted = false; clearInterval(interval); };
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <div className="animate-pulse text-sm text-slate-400">{text.loading}</div>
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className="flex items-center justify-center py-16">
        <div className="text-sm text-slate-400">{text.empty}</div>
      </div>
    );
  }

  // Count statuses for summary
  const dangerCount = items.filter(i => i.status === 'danger').length;
  const warningCount = items.filter(i => i.status === 'warning').length;

  return (
    <div className="flex flex-col p-4">
      {/* Summary badges */}
      <div className="flex items-center gap-3 mb-3 flex-wrap">
        {dangerCount > 0 && (
          <span className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-red-100 border border-red-200 text-red-700 text-xs font-semibold">
            <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
            {text.criticalCount(dangerCount)}
          </span>
        )}
        {warningCount > 0 && (
          <span className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-amber-100 border border-amber-200 text-amber-700 text-xs font-semibold">
            <span className="w-2 h-2 rounded-full bg-amber-500" />
            {text.warningCount(warningCount)}
          </span>
        )}
        <span className="text-[10px] text-slate-400 ml-auto">{text.summary}</span>
      </div>

      {/* Table */}
      <div className="border border-slate-200 rounded-lg overflow-x-auto">
        {/* Header */}
        <div className="grid grid-cols-12 gap-2 px-4 py-2.5 min-w-[920px] bg-slate-100 border-b border-slate-200 text-[11px] font-bold text-slate-500 uppercase tracking-wider sticky top-0 z-10">
          <div className="col-span-2">{text.productName}</div>
          <div className="col-span-1 text-right">{text.inventoryTons}</div>
          <div className="col-span-1 text-right">{text.inventoryDays}</div>
          <div className="col-span-5 text-center">{text.safeRange}</div>
          <div className="col-span-1 text-right">{text.sales30d}</div>
          <div className="col-span-1 text-right">{text.dailyUse}</div>
          <div className="col-span-1 text-center">{text.state}</div>
        </div>

        {/* Rows */}
        {items.map((item, idx) => {
          const sc = statusConfig[item.status];
          return (
            <div
              key={item.product_id}
              className={`grid grid-cols-12 gap-2 px-4 py-3 min-w-[920px] border-b border-slate-100 items-center transition-colors hover:bg-slate-50 ${idx % 2 === 0 ? 'bg-white' : 'bg-slate-50/30'}`}
            >
              <div className="col-span-2 font-semibold text-slate-800 text-sm">{item.name_cn}</div>
              <div className="col-span-1 text-right font-mono text-sm font-bold text-slate-700">
                {item.inventory_tons.toLocaleString(locale)}
              </div>
              <div className={`col-span-1 text-right font-mono text-sm font-bold ${item.inventory_days <= 3 ? 'text-red-600' : item.inventory_days <= 7 ? 'text-amber-600' : 'text-slate-700'
                }`}>
                {item.inventory_days}{text.daysSuffix}
              </div>
              <div className="col-span-5 px-2">
                <SafeZoneBar item={item} />
                <div className="flex text-[9px] text-slate-400 mt-0.5 px-0.5">
                  <span className="flex-none">0</span>
                  <span className="flex-1 text-center">{text.lowerBound} {item.safety_min}t</span>
                  <span className="flex-none">{text.upperBound} {item.safety_max}t</span>
                </div>
              </div>
              <div className="col-span-1 text-right font-mono text-xs text-slate-600">
                {item.sales_30d > 0 ? item.sales_30d.toLocaleString(locale) : <span className="text-slate-300">—</span>}
              </div>
              <div className="col-span-1 text-right font-mono text-xs text-slate-600">
                {item.daily_consumption}{text.dailyUnit}
              </div>
              <div className="col-span-1 flex justify-center">
                <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold border ${sc.color} ${sc.bg} ${sc.border}`}>
                  {sc.label}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
