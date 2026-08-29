/**
 * Advanced Block Configuration Panel
 * 
 * Professional visual query builder with:
 * - Product selection (multi-select with checkboxes)
 * - Date range picker (presets + custom)
 * - Region/company filters
 * - Metric selection
 * - Live preview
 */

import React, { useState, useEffect, useMemo, useRef } from 'react';
import { useLanguage } from '../lib/language';
import { api, type PlatformProduct } from '../services/api';

interface BlockConfig {
  data_source?: string;
  products?: string[];
  date_range?: DateRange;
  regions?: string[];
  companies?: string[];
  metrics?: string[];
  filters?: Record<string, any>;
  aggregation?: string;
  chart_type?: string;
  columns?: string[];
  title?: string;
  sql_query?: string;
  // text block
  content?: string;
  // image block
  asset_id?: string;
  image_url?: string;
  alt_text?: string;
  caption?: string;
}

interface DateRange {
  type: 'single' | 'range' | 'relative';
  value?: string;
  start?: string;
  end?: string;
  relative?: {
    value: number;
    unit: 'days' | 'weeks' | 'months';
  };
}

interface AdvancedBlockConfigPanelProps {
  blockType: string;
  initialConfig: BlockConfig;
  onSave: (config: BlockConfig) => void;
  onCancel: () => void;
  onPreview: (config: BlockConfig) => Promise<void> | void;
}

// Available products in the system (Real EDIA products)
const AVAILABLE_PRODUCTS = [
  // Upstream Feedstocks
  { id: 'crude_oil', name_en: 'Brent Crude Oil', name_zh: '布伦特原油', category: 'upstream' },
  { id: 'naphtha', name_en: 'Japanese Naphtha', name_zh: '日本石脑油', category: 'upstream' },
  { id: 'cracked_c5', name_en: 'Cracked C5', name_zh: '工业用裂解碳五', category: 'upstream' },
  { id: 'cracked_c9', name_en: 'Cracked C9', name_zh: '工业用裂解碳九', category: 'upstream' },
  // Midstream Products
  { id: 'isoprene', name_en: 'Isoprene', name_zh: '异戊二烯', category: 'midstream' },
  { id: 'piperylene', name_en: 'Piperylene', name_zh: '间戊二烯', category: 'midstream' },
  { id: 'dcpd', name_en: 'DCPD', name_zh: '双环戊二烯', category: 'midstream' },
  { id: 'raffinate_c5', name_en: 'Raffinate C5', name_zh: '抽余碳五', category: 'midstream' },
  // Downstream Products
  { id: 'sis', name_en: 'SIS', name_zh: 'SIS', category: 'downstream' },
  { id: 'c5_resin', name_en: 'C5 Resin', name_zh: '碳五石油树脂', category: 'downstream' },
  { id: 'blowing_agent', name_en: 'Blowing Agent', name_zh: '戊烷发泡剂', category: 'downstream' },
  { id: 'styrene', name_en: 'Styrene', name_zh: '苯乙烯', category: 'downstream' },
];

const DATE_PRESETS = [
  { id: 'today', label_en: 'Today', label_zh: '今天', value: 0, unit: 'days' as const },
  { id: 'last_7_days', label_en: 'Last 7 Days', label_zh: '最近7天', value: 7, unit: 'days' as const },
  { id: 'last_30_days', label_en: 'Last 30 Days', label_zh: '最近30天', value: 30, unit: 'days' as const },
  { id: 'last_3_months', label_en: 'Last 3 Months', label_zh: '最近3个月', value: 3, unit: 'months' as const },
  { id: 'custom', label_en: 'Custom Range', label_zh: '自定义范围', value: 0, unit: 'days' as const },
];

// ERP products available in sale_erp_v_*_data tables
const ERP_PRODUCTS = [
  { product_id: 'dcpd', name_en: 'DCPD', name_zh: '双环戊二烯' },
  { product_id: 'cracked_c5', name_en: 'Cracked C5', name_zh: '工业用裂解碳五' },
  { product_id: 'cracked_c9', name_en: 'Cracked C9', name_zh: '工业用裂解碳九' },
  { product_id: 'isoprene', name_en: 'Isoprene', name_zh: '异戊二烯' },
  { product_id: 'piperylene', name_en: 'Piperylene', name_zh: '间戊二烯' },
  { product_id: 'raffinate_c5', name_en: 'Raffinate C5', name_zh: '抽余碳五' },
  { product_id: 'sis', name_en: 'SIS', name_zh: 'SIS' },
  { product_id: 'c5_resin', name_en: 'C5 Petroleum Resin', name_zh: '碳五石油树脂' },
  { product_id: 'blowing_agent', name_en: 'Blowing Agent', name_zh: '戊烷发泡剂' },
  { product_id: 'hex', name_en: 'Industrial Hexane', name_zh: '工业己烷' },
  { product_id: 'trimethylbenzene', name_en: 'Trimethylbenzene', name_zh: '混三甲苯' },
  { product_id: 'eth_carbon', name_en: 'Ethylene Carbon Black', name_zh: '乙烯炭黑料' },
];

// Date presets for ERP blocks (period_days)
const ERP_DATE_PRESETS = [
  { days: 30, label_en: 'Last 30 Days', label_zh: '近30天' },
  { days: 90, label_en: 'Last 3 Months', label_zh: '近3个月' },
  { days: 180, label_en: 'Last 6 Months', label_zh: '近6个月' },
  { days: 365, label_en: 'Last 12 Months', label_zh: '近12个月' },
];

// Available regions/markets for price data
const AVAILABLE_REGIONS = [
  { id: 'east_china', name_en: 'East China Market', name_zh: '华东市场' },
  { id: 'south_china', name_en: 'South China Market', name_zh: '华南市场' },
  { id: 'wuhan', name_en: 'Central China (Wuhan)', name_zh: '华中(武汉)' },
  { id: 'sinopec_sh', name_en: 'Shanghai Petrochemical', name_zh: '上海石化' },
  { id: 'maoming', name_en: 'Maoming Petrochemical', name_zh: '茂名石化' },
  { id: 'yangzi', name_en: 'Yangzi Petrochemical', name_zh: '扬子石化' },
  { id: 'brent', name_en: 'International (Brent)', name_zh: '国际(布伦特)' },
  { id: 'japan_cif', name_en: 'Japan CIF', name_zh: '日本到岸' },
];

/**
 * Which regions have actual price data for each product.
 * Derived from the external MySQL DB schema (external_mysql_schema.txt).
 * 
 * 异戊二烯  (isoprene):         east_china, sinopec_sh, wuhan, south_china
 * 间戊二烯  (piperylene):        east_china, sinopec_sh, wuhan
 * 双环戊二烯(dcpd):              sinopec_sh, east_china, wuhan
 * 发泡剂    (blowing_agent):     east_china, wuhan, south_china
 * 裂解C5   (cracked_c5):        yangzi, maoming, japan_cif
 * SIS      (sis):               sinopec_sh  (Baling 1105 + Isoprene-Shanghai)
 * 苯乙烯   (styrene):            sinopec_sh, maoming
 * 间戊二烯  (c5_resin):          east_china, sinopec_sh  (proxy from piperylene / cracked c5 tables)
 * crude_oil / naphtha:          brent, japan_cif
 * cracked_c9 / raffinate_c5:   yangzi, maoming  (from cracked C5 family)
 */
const PRODUCT_AVAILABLE_REGIONS: Record<string, string[]> = {
  crude_oil: ['brent'],
  naphtha: ['brent', 'japan_cif'],
  cracked_c5: ['yangzi', 'maoming', 'japan_cif'],
  cracked_c9: ['yangzi', 'maoming'],
  isoprene: ['east_china', 'sinopec_sh', 'wuhan', 'south_china'],
  piperylene: ['east_china', 'sinopec_sh', 'wuhan'],
  dcpd: ['sinopec_sh', 'east_china', 'wuhan'],
  raffinate_c5: ['yangzi', 'maoming'],
  sis: ['sinopec_sh'],
  c5_resin: ['east_china', 'sinopec_sh'],
  blowing_agent: ['east_china', 'wuhan', 'south_china'],
  styrene: ['sinopec_sh', 'maoming'],
};

/**
 * Given a list of selected product IDs, return the set of region IDs
 * that have data for AT LEAST ONE of those products.
 */
function getAvailableRegionsForProducts(productIds: string[]): string[] {
  if (!productIds || productIds.length === 0) return AVAILABLE_REGIONS.map(r => r.id);
  const available = new Set<string>();
  for (const pid of productIds) {
    const regions = PRODUCT_AVAILABLE_REGIONS[pid] || [];
    if (regions.length === 0) {
      AVAILABLE_REGIONS.forEach(r => available.add(r.id));
      continue;
    }
    regions.forEach(r => available.add(r));
  }
  return Array.from(available);
}

export const AdvancedBlockConfigPanel: React.FC<AdvancedBlockConfigPanelProps> = ({
  blockType,
  initialConfig,
  onSave,
  onCancel,
  onPreview,
}) => {
  const { language } = useLanguage();
  const [config, setConfig] = useState<BlockConfig>(initialConfig);
  const [datePreset, setDatePreset] = useState('last_7_days');
  const [showCustomDate, setShowCustomDate] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [uploadingImage, setUploadingImage] = useState(false);
  const [uploadError, setUploadError] = useState('');
  const [catalogProducts, setCatalogProducts] = useState<PlatformProduct[]>([]);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const isEnglish = language === 'en';

  useEffect(() => {
    api.listAvailablePlatformProducts({ displayIn: 'reports', requireData: true })
      .then(setCatalogProducts)
      .catch(() => setCatalogProducts([]));
  }, []);

  const availableProducts = useMemo(() => {
    const byId = new Map(AVAILABLE_PRODUCTS.map(product => [product.id, product]));
    catalogProducts.forEach(product => {
      byId.set(product.id, {
        id: product.id,
        name_en: product.name_en,
        name_zh: product.name_cn,
        category: product.product_type || 'downstream',
      });
    });
    return Array.from(byId.values());
  }, [catalogProducts]);

  // Derived: which regions have data for currently selected products
  const availableRegionIds = getAvailableRegionsForProducts(config.products || []);

  // When products change, auto-select available regions and deselect unavailable ones
  useEffect(() => {
    if (!config.products || config.products.length === 0) return;
    const available = getAvailableRegionsForProducts(config.products);
    // Deselect any currently selected regions that are no longer available,
    // and auto-select all available ones if nothing was previously selected.
    const currentRegions = config.regions || [];
    const validCurrentRegions = currentRegions.filter(r => available.includes(r));
    // If user hadn't made any selection yet, auto-select all available
    const newRegions = validCurrentRegions.length > 0 ? validCurrentRegions : available;
    setConfig(prev => ({ ...prev, regions: newRegions }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(config.products)]);

  const withPreviewTimeout = async (task: Promise<void> | void, timeoutMs = 15000) => {
    let timer: number | undefined;
    try {
      await Promise.race([
        Promise.resolve(task),
        new Promise<void>((_, reject) => {
          timer = window.setTimeout(() => {
            reject(new Error('Preview request timed out'));
          }, timeoutMs);
        }),
      ]);
    } finally {
      if (timer !== undefined) {
        window.clearTimeout(timer);
      }
    }
  };

  // Product categories
  const productsByCategory = {
    upstream: availableProducts.filter(p => p.category === 'upstream'),
    midstream: availableProducts.filter(p => p.category === 'midstream'),
    downstream: availableProducts.filter(p => p.category === 'downstream'),
  };

  const handleProductToggle = (productId: string) => {
    const current = config.products || [];
    const updated = current.includes(productId)
      ? current.filter(id => id !== productId)
      : [...current, productId];
    setConfig({ ...config, products: updated });
  };

  const handleRegionToggle = (regionId: string) => {
    const current = config.regions || [];
    const updated = current.includes(regionId)
      ? current.filter(id => id !== regionId)
      : [...current, regionId];
    setConfig({ ...config, regions: updated });
  };

  const handleDatePresetChange = (presetId: string) => {
    setDatePreset(presetId);

    if (presetId === 'custom') {
      setShowCustomDate(true);
      setConfig({
        ...config,
        date_range: {
          type: 'range',
          start: '',
          end: '',
        },
      });
    } else {
      setShowCustomDate(false);
      const preset = DATE_PRESETS.find(p => p.id === presetId);
      if (preset) {
        setConfig({
          ...config,
          date_range: {
            type: 'relative',
            relative: {
              value: preset.value,
              unit: preset.unit,
            },
          },
        });
      }
    }
  };

  const handlePreview = async () => {
    setPreviewLoading(true);
    try {
      await withPreviewTimeout(onPreview(config));
    } catch (error) {
      console.error('Preview failed:', error);
    } finally {
      setPreviewLoading(false);
    }
  };

  const handleSaveConfiguration = async () => {
    // For custom_sql blocks, automatically preview the data before saving
    // This ensures the data is cached and will display in the report
    if (blockType === 'custom_sql' && config.sql_query && config.sql_query.trim().length > 0) {
      setPreviewLoading(true);
      try {
        // Fetch and cache the data first
        await withPreviewTimeout(onPreview(config));
      } catch (error) {
        console.error('Failed to preview custom SQL:', error);
        // Continue with save even if preview fails - user can preview again later
      } finally {
        setPreviewLoading(false);
      }
    }

    // Save the configuration
    onSave(config);
  };

  const handleImageUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }
    try {
      setUploadingImage(true);
      setUploadError('');
      const uploaded = await api.uploadReportImage(file);
      setConfig((prev) => ({
        ...prev,
        asset_id: uploaded.id,
        image_url: uploaded.url,
        alt_text: prev.alt_text || file.name,
      }));
    } catch (error) {
      console.error('Image upload failed:', error);
      setUploadError(isEnglish ? 'Image upload failed. Please try again.' : '图片上传失败，请重试。');
    } finally {
      setUploadingImage(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  };

  const isValid = () => {
    if (blockType === 'custom_sql') {
      return config.sql_query && config.sql_query.trim().length > 0;
    }
    if (blockType === 'text') {
      return (config.content || '').trim().length > 0;
    }
    if (blockType === 'image') {
      return (config.image_url || '').trim().length > 0;
    }
    if (blockType.startsWith('erp_')) {
      return !!(config as any).product_id;
    }
    if (blockType === 'kpi_cards' || blockType === 'data_table' || blockType === 'chart') {
      return (config.products && config.products.length > 0) ||
        (config.metrics && config.metrics.length > 0);
    }
    return true;
  };

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-2xl w-[900px] max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200">
          <div>
            <h2 className="text-xl font-semibold text-slate-900">
              {blockType === 'text'
                ? (isEnglish ? 'Edit Text Block' : '编辑文本模块')
                : blockType === 'image'
                  ? (isEnglish ? 'Configure Image Block' : '配置图片模块')
                  : blockType.startsWith('erp_')
                    ? (isEnglish ? 'Configure ERP / Sales Block' : '配置 ERP / 销售数据模块')
                    : (isEnglish ? 'Configure Data Block' : '配置数据模块')}
            </h2>
            <p className="text-sm text-slate-500 mt-1">
              {blockType === 'text'
                ? (isEnglish ? 'Add narrative content, analysis, or commentary' : '添加叙述性内容、分析或评论')
                : blockType === 'image'
                  ? (isEnglish ? 'Add an image by URL or upload a file' : '通过 URL 或上传文件添加图片')
                  : blockType.startsWith('erp_')
                    ? (isEnglish ? 'Select a product and time range to pull live ERP sales data' : '选择产品和时间范围，拉取实时ERP销售数据')
                    : (isEnglish ? 'Select products, date range, and metrics for your report block' : '为报告模块选择产品、日期范围和指标')}
            </p>
          </div>
          <button
            onClick={onCancel}
            className="text-slate-400 hover:text-slate-600 text-2xl"
          >
            ✕
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-6 py-6">
          <div className="space-y-6">
            {/* Text Block Configuration */}
            {blockType === 'text' && (
              <div className="space-y-4">
                <div className="space-y-2">
                  <label className="block text-sm font-semibold text-slate-700">
                    {isEnglish ? '1. Block Title (Optional)' : '1. 模块标题（可选）'}
                  </label>
                  <input
                    type="text"
                    value={config.title || ''}
                    onChange={(e) => setConfig({ ...config, title: e.target.value })}
                    placeholder={isEnglish ? 'e.g. Market Commentary' : '例如：市场评论'}
                    className="w-full px-3 py-2 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>
                <div className="space-y-2">
                  <label className="block text-sm font-semibold text-slate-700">
                    {isEnglish ? '2. Content' : '2. 内容'}
                    <span className="text-red-500 ml-1">*</span>
                  </label>
                  <textarea
                    value={config.content || ''}
                    onChange={(e) => setConfig({ ...config, content: e.target.value })}
                    placeholder={isEnglish
                      ? 'Write your narrative, analysis, or commentary here...'
                      : '在此处撰写叙述、分析或评论...'}
                    rows={10}
                    className="w-full px-3 py-2 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 resize-y text-sm leading-relaxed"
                  />
                  <p className="text-xs text-slate-400">
                    {isEnglish
                      ? 'Supports plain text. Press Enter for new paragraphs.'
                      : '支持纯文本。按 Enter 键换行。'}
                  </p>
                </div>
              </div>
            )}

            {/* Image Block Configuration */}
            {blockType === 'image' && (
              <div className="space-y-4">
                <div className="space-y-2">
                  <label className="block text-sm font-semibold text-slate-700">
                    {isEnglish ? '1. Upload From Desktop' : '1. 从桌面上传'}
                  </label>
                  <div className="flex items-center gap-3">
                    <button
                      type="button"
                      onClick={() => fileInputRef.current?.click()}
                      className="px-4 py-2 rounded-lg bg-blue-600 text-white text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
                      disabled={uploadingImage}
                    >
                      {uploadingImage
                        ? (isEnglish ? 'Uploading...' : '上传中...')
                        : (isEnglish ? 'Upload Image' : '上传图片')}
                    </button>
                    <span className="text-xs text-slate-500">
                      {isEnglish ? 'PNG, JPG, WEBP, or GIF up to 10MB' : '支持 PNG、JPG、WEBP、GIF，最大 10MB'}
                    </span>
                  </div>
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept="image/png,image/jpeg,image/jpg,image/webp,image/gif"
                    className="hidden"
                    onChange={handleImageUpload}
                  />
                  {uploadError && (
                    <p className="text-sm text-red-600">{uploadError}</p>
                  )}
                </div>
                <div className="space-y-2">
                  <label className="block text-sm font-semibold text-slate-700">
                    {isEnglish ? '2. Image URL' : '2. 图片链接'}
                    <span className="text-red-500 ml-1">*</span>
                  </label>
                  <input
                    type="url"
                    value={config.image_url || ''}
                    onChange={(e) => setConfig({ ...config, image_url: e.target.value })}
                    placeholder="https://example.com/image.png"
                    className="w-full px-3 py-2 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 font-mono text-sm"
                  />
                </div>
                {config.image_url && (
                  <div className="rounded-lg border border-slate-200 overflow-hidden bg-slate-50 flex items-center justify-center" style={{ minHeight: 160 }}>
                    <img
                      src={config.image_url}
                      alt={config.alt_text || 'preview'}
                      className="max-h-64 max-w-full object-contain"
                      onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
                    />
                  </div>
                )}
                <div className="space-y-2">
                  <label className="block text-sm font-semibold text-slate-700">
                    {isEnglish ? '3. Alt Text' : '3. 替代文本'}
                  </label>
                  <input
                    type="text"
                    value={config.alt_text || ''}
                    onChange={(e) => setConfig({ ...config, alt_text: e.target.value })}
                    placeholder={isEnglish ? 'Describe the image...' : '描述图片内容...'}
                    className="w-full px-3 py-2 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>
                <div className="space-y-2">
                  <label className="block text-sm font-semibold text-slate-700">
                    {isEnglish ? '4. Caption (Optional)' : '4. 图注（可选）'}
                  </label>
                  <input
                    type="text"
                    value={config.caption || ''}
                    onChange={(e) => setConfig({ ...config, caption: e.target.value })}
                    placeholder={isEnglish ? 'Figure 1: ...' : '图1：...'}
                    className="w-full px-3 py-2 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>
              </div>
            )}

            {/* Custom SQL Query Configuration */}
            {blockType === 'custom_sql' && (
              <div className="space-y-4">
                <div className="space-y-2">
                  <label className="block text-sm font-semibold text-slate-700">
                    {isEnglish ? '1. Block Title' : '1. 模块标题'}
                  </label>
                  <input
                    type="text"
                    value={config.title || ''}
                    onChange={(e) => setConfig({ ...config, title: e.target.value })}
                    placeholder={isEnglish ? 'Enter block title...' : '输入模块标题...'}
                    className="w-full px-3 py-2 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>

                <div className="space-y-2">
                  <label className="block text-sm font-semibold text-slate-700">
                    {isEnglish ? '2. SQL Query' : '2. SQL 查询'}
                  </label>
                  <p className="text-xs text-slate-500">
                    {isEnglish
                      ? 'Only SELECT queries are allowed. Query will be limited to prevent excessive data retrieval.'
                      : '仅允许 SELECT 查询。查询将被限制以防止检索过多数据。'}
                  </p>
                  <textarea
                    value={config.sql_query || ''}
                    onChange={(e) => setConfig({ ...config, sql_query: e.target.value })}
                    placeholder={isEnglish
                      ? 'SELECT * FROM market_prices WHERE product = "crude_oil" ORDER BY date DESC'
                      : 'SELECT * FROM market_prices WHERE product = "crude_oil" ORDER BY date DESC'}
                    rows={8}
                    className="w-full px-3 py-2 border border-slate-300 rounded-lg font-mono text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    style={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace' }}
                  />
                </div>

                <div className="p-4 bg-blue-50 border border-blue-200 rounded-lg">
                  <div className="flex gap-2">
                    <span className="text-blue-600 font-semibold">💡</span>
                    <div className="flex-1 text-sm text-blue-900">
                      <p className="font-semibold mb-1">
                        {isEnglish ? 'Example Queries:' : '示例查询：'}
                      </p>
                      <ul className="list-disc list-inside space-y-1 text-xs">
                        <li><code>SELECT date, product, price FROM market_prices WHERE date &gt;= '2026-01-01'</code></li>
                        <li><code>SELECT product, AVG(price) as avg_price FROM market_prices GROUP BY product</code></li>
                        <li><code>SELECT * FROM inventory_status ORDER BY update_time DESC LIMIT 10</code></li>
                      </ul>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* ERP Block Configuration */}
            {blockType.startsWith('erp_') && (
              <div className="space-y-6">
                {/* ERP Product Selector */}
                <div className="space-y-2">
                  <label className="block text-sm font-semibold text-slate-700">
                    {isEnglish ? '1. Select Product' : '1. 选择产品'}
                    <span className="text-red-500 ml-1">*</span>
                  </label>
                  <select
                    className="w-full px-3 py-2 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm"
                    value={(config as any).product_id || ''}
                    onChange={(e) => setConfig({ ...config, product_id: e.target.value } as any)}
                  >
                    <option value="">{isEnglish ? '— Select a product —' : '— 请选择产品 —'}</option>
                    {ERP_PRODUCTS.map(p => (
                      <option key={p.product_id} value={p.product_id}>
                        {isEnglish ? p.name_en : p.name_zh}
                      </option>
                    ))}
                  </select>
                </div>

                {/* Date Range */}
                <div className="space-y-2">
                  <label className="block text-sm font-semibold text-slate-700">
                    {isEnglish ? '2. Date Range' : '2. 时间范围'}
                  </label>
                  <div className="flex flex-wrap gap-2">
                    {ERP_DATE_PRESETS.map(preset => {
                      const isActive = (config as any).period_days === preset.days;
                      return (
                        <button
                          key={preset.days}
                          type="button"
                          onClick={() => setConfig({ ...config, period_days: preset.days } as any)}
                          className={`px-3 py-1.5 rounded-lg text-sm font-medium border-2 transition-all ${isActive
                            ? 'border-blue-500 bg-blue-50 text-blue-700'
                            : 'border-slate-200 text-slate-600 hover:border-blue-300'
                            }`}
                        >
                          {isEnglish ? preset.label_en : preset.label_zh}
                        </button>
                      );
                    })}
                  </div>
                </div>

                {/* Group By (for applicable blocks) */}
                {(blockType === 'erp_sales_summary' || blockType === 'erp_monthly_trend') && (
                  <div className="space-y-2">
                    <label className="block text-sm font-semibold text-slate-700">
                      {isEnglish ? '3. Group By' : '3. 分组方式'}
                    </label>
                    <div className="flex gap-2">
                      {[
                        { value: 'month', label_en: 'By Month', label_zh: '按月' },
                        { value: 'date', label_en: 'By Day', label_zh: '按天' },
                      ].map(opt => (
                        <button
                          key={opt.value}
                          type="button"
                          onClick={() => setConfig({ ...config, group_by: opt.value } as any)}
                          className={`px-3 py-1.5 rounded-lg text-sm font-medium border-2 transition-all ${((config as any).group_by || 'month') === opt.value
                            ? 'border-blue-500 bg-blue-50 text-blue-700'
                            : 'border-slate-200 text-slate-600 hover:border-blue-300'
                            }`}
                        >
                          {isEnglish ? opt.label_en : opt.label_zh}
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {/* Info banner */}
                <div className="p-3 bg-amber-50 border border-amber-200 rounded-lg text-xs text-amber-800 flex gap-2">
                  <span>🔒</span>
                  <span>
                    {isEnglish
                      ? 'ERP data contains actual transaction prices and customer information. Only authorized users can view reports with ERP blocks.'
                      : 'ERP数据包含实际成交价格和客户信息，仅授权用户可查看含ERP模块的报告。'}
                  </span>
                </div>
              </div>
            )}

            {/* Product Selection */}
            {(blockType === 'kpi_cards' || blockType === 'data_table' || blockType === 'chart') && (
              <div className="space-y-3">
                <label className="block text-sm font-semibold text-slate-700">
                  {isEnglish ? '1. Select Products' : '1. 选择产品'}
                  <span className="text-red-500 ml-1">*</span>
                </label>

                {/* Upstream Products */}
                <div className="space-y-2">
                  <div className="text-xs font-medium text-slate-500 uppercase tracking-wide">
                    {isEnglish ? 'Upstream' : '上游'}
                  </div>
                  <div className="grid grid-cols-3 gap-3">
                    {productsByCategory.upstream.map(product => (
                      <label
                        key={product.id}
                        className={`flex items-center gap-2 px-3 py-2 rounded-lg border-2 cursor-pointer transition-all ${config.products?.includes(product.id)
                          ? 'border-blue-500 bg-blue-50'
                          : 'border-slate-200 hover:border-blue-300'
                          }`}
                      >
                        <input
                          type="checkbox"
                          checked={config.products?.includes(product.id) || false}
                          onChange={() => handleProductToggle(product.id)}
                          className="w-4 h-4 text-blue-600 rounded"
                        />
                        <span className="text-sm font-medium text-slate-700">
                          {isEnglish ? product.name_en : product.name_zh}
                        </span>
                      </label>
                    ))}
                  </div>
                </div>

                {/* Midstream Products */}
                <div className="space-y-2">
                  <div className="text-xs font-medium text-slate-500 uppercase tracking-wide">
                    {isEnglish ? 'Midstream' : '中游'}
                  </div>
                  <div className="grid grid-cols-3 gap-3">
                    {productsByCategory.midstream.map(product => (
                      <label
                        key={product.id}
                        className={`flex items-center gap-2 px-3 py-2 rounded-lg border-2 cursor-pointer transition-all ${config.products?.includes(product.id)
                          ? 'border-blue-500 bg-blue-50'
                          : 'border-slate-200 hover:border-blue-300'
                          }`}
                      >
                        <input
                          type="checkbox"
                          checked={config.products?.includes(product.id) || false}
                          onChange={() => handleProductToggle(product.id)}
                          className="w-4 h-4 text-blue-600 rounded"
                        />
                        <span className="text-sm font-medium text-slate-700">
                          {isEnglish ? product.name_en : product.name_zh}
                        </span>
                      </label>
                    ))}
                  </div>
                </div>

                {/* Downstream Products */}
                <div className="space-y-2">
                  <div className="text-xs font-medium text-slate-500 uppercase tracking-wide">
                    {isEnglish ? 'Downstream' : '下游'}
                  </div>
                  <div className="grid grid-cols-3 gap-3">
                    {productsByCategory.downstream.map(product => (
                      <label
                        key={product.id}
                        className={`flex items-center gap-2 px-3 py-2 rounded-lg border-2 cursor-pointer transition-all ${config.products?.includes(product.id)
                          ? 'border-blue-500 bg-blue-50'
                          : 'border-slate-200 hover:border-blue-300'
                          }`}
                      >
                        <input
                          type="checkbox"
                          checked={config.products?.includes(product.id) || false}
                          onChange={() => handleProductToggle(product.id)}
                          className="w-4 h-4 text-blue-600 rounded"
                        />
                        <span className="text-sm font-medium text-slate-700">
                          {isEnglish ? product.name_en : product.name_zh}
                        </span>
                      </label>
                    ))}
                  </div>
                </div>

                {config.products && config.products.length > 0 && (
                  <div className="text-sm text-blue-600 bg-blue-50 px-3 py-2 rounded-lg">
                    {isEnglish
                      ? `${config.products.length} product${config.products.length > 1 ? 's' : ''} selected`
                      : `已选择 ${config.products.length} 个产品`}
                  </div>
                )}
              </div>
            )}

            {/* Region/Market Selection - Only for data_table blocks */}
            {blockType === 'data_table' && (
              <div className="space-y-3">
                <label className="block text-sm font-semibold text-slate-700">
                  {isEnglish ? 'Regions/Markets (Optional)' : '地区/市场 (可选)'}
                </label>
                <p className="text-xs text-slate-500">
                  {isEnglish
                    ? config.products && config.products.length > 0
                      ? 'Regions with data for your selected product(s) are pre-selected. Greyed regions have no data.'
                      : 'Select a product first to see which regions have data available.'
                    : config.products && config.products.length > 0
                      ? '有数据的地区已自动选中。灰色地区对所选产品无数据。'
                      : '请先选择产品，系统将自动显示有数据的地区。'}
                </p>

                <div className="grid grid-cols-3 gap-3">
                  {AVAILABLE_REGIONS.map(region => {
                    const hasData = availableRegionIds.includes(region.id);
                    const isChecked = config.regions?.includes(region.id) || false;
                    const noProductSelected = !config.products || config.products.length === 0;
                    const disabled = !hasData && !noProductSelected;
                    return (
                      <label
                        key={region.id}
                        title={
                          disabled
                            ? (isEnglish ? 'No data for selected product(s)' : '所选产品无此地区数据')
                            : undefined
                        }
                        className={`flex items-center gap-2 px-3 py-2 rounded-lg border-2 transition-all
                          ${disabled
                            ? 'border-slate-100 bg-slate-50 cursor-not-allowed opacity-40'
                            : isChecked
                              ? 'border-green-500 bg-green-50 cursor-pointer'
                              : 'border-slate-200 hover:border-green-300 cursor-pointer'
                          }`}
                      >
                        <input
                          type="checkbox"
                          checked={isChecked}
                          disabled={disabled}
                          onChange={() => !disabled && handleRegionToggle(region.id)}
                          className="w-4 h-4 text-green-600 rounded"
                        />
                        <span className={`text-sm font-medium ${disabled ? 'text-slate-400' : 'text-slate-700'}`}>
                          {isEnglish ? region.name_en : region.name_zh}
                        </span>
                        {disabled && (
                          <span className="ml-auto text-[10px] text-slate-400 leading-tight">
                            {isEnglish ? 'No data' : '无数据'}
                          </span>
                        )}
                      </label>
                    );
                  })}
                </div>

                {config.regions && config.regions.length > 0 && (
                  <div className="text-sm text-green-600 bg-green-50 px-3 py-2 rounded-lg">
                    {isEnglish
                      ? `${config.regions.length} region${config.regions.length > 1 ? 's' : ''} selected`
                      : `已选择 ${config.regions.length} 个地区`}
                  </div>
                )}
              </div>
            )}

            {/* Date Range Selection - Only for data blocks */}
            {(blockType === 'kpi_cards' || blockType === 'data_table' || blockType === 'chart') && (
              <div className="space-y-3">
                <label className="block text-sm font-semibold text-slate-700">
                  {isEnglish ? '2. Select Date Range' : '2. 选择日期范围'}
                  <span className="text-red-500 ml-1">*</span>
                </label>

                <div className="grid grid-cols-3 gap-3">
                  {DATE_PRESETS.map(preset => (
                    <button
                      key={preset.id}
                      onClick={() => handleDatePresetChange(preset.id)}
                      className={`px-4 py-3 rounded-lg border-2 font-medium text-sm transition-all ${datePreset === preset.id
                        ? 'border-blue-500 bg-blue-50 text-blue-700'
                        : 'border-slate-200 text-slate-700 hover:border-blue-300'
                        }`}
                    >
                      {isEnglish ? preset.label_en : preset.label_zh}
                    </button>
                  ))}
                </div>

                {/* Resolved date range preview — updates every time user opens this dialog */}
                {!showCustomDate && config.date_range?.type === 'relative' && config.date_range?.relative && (() => {
                  const today = new Date();
                  const fmt = (d: Date) => d.toLocaleDateString(isEnglish ? 'en-US' : 'zh-CN', { month: 'short', day: 'numeric', year: 'numeric' });
                  const { value, unit } = config.date_range.relative;
                  const end = new Date(today);
                  const start = new Date(today);
                  if (value === 0) {
                    return (
                      <p className="text-xs text-blue-600 bg-blue-50 rounded-lg px-3 py-2 mt-1 border border-blue-100">
                        ℹ {isEnglish ? `Will query: ${fmt(today)}` : `将查询: ${fmt(today)}`}
                      </p>
                    );
                  }
                  if (unit === 'days') start.setDate(start.getDate() - value);
                  else if (unit === 'weeks') start.setDate(start.getDate() - value * 7);
                  else if (unit === 'months') start.setMonth(start.getMonth() - value);
                  return (
                    <p className="text-xs text-blue-600 bg-blue-50 rounded-lg px-3 py-2 mt-1 border border-blue-100">
                      ℹ {isEnglish
                        ? `Will query: ${fmt(start)} – ${fmt(end)} (rolls forward each time you refresh)`
                        : `将查询: ${fmt(start)} – ${fmt(end)}（每次刷新时自动滚动到当天）`}
                    </p>
                  );
                })()}

                {showCustomDate && (
                  <div className="grid grid-cols-2 gap-3 mt-3">
                    <div>
                      <label className="block text-xs font-medium text-slate-600 mb-1">
                        {isEnglish ? 'Start Date' : '开始日期'}
                      </label>
                      <input
                        type="date"
                        value={config.date_range?.start || ''}
                        onChange={(e) => setConfig({
                          ...config,
                          date_range: { ...config.date_range, type: 'range', start: e.target.value } as DateRange,
                        })}
                        className="w-full px-3 py-2 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-slate-600 mb-1">
                        {isEnglish ? 'End Date' : '结束日期'}
                      </label>
                      <input
                        type="date"
                        value={config.date_range?.end || ''}
                        onChange={(e) => setConfig({
                          ...config,
                          date_range: { ...config.date_range, type: 'range', end: e.target.value } as DateRange,
                        })}
                        className="w-full px-3 py-2 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                      />
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Chart Type (for chart blocks) */}
            {blockType === 'chart' && (
              <div className="space-y-3">
                <label className="block text-sm font-semibold text-slate-700">
                  {isEnglish ? '3. Chart Type' : '3. 图表类型'}
                </label>

                <div className="grid grid-cols-4 gap-3">
                  {[
                    { id: 'line', label_en: 'Line', label_zh: '折线图', icon: '📈' },
                    { id: 'bar', label_en: 'Bar', label_zh: '柱状图', icon: '📊' },
                    { id: 'area', label_en: 'Area', label_zh: '面积图', icon: '📉' },
                    { id: 'scatter', label_en: 'Scatter', label_zh: '散点图', icon: '⚫' },
                  ].map(type => (
                    <button
                      key={type.id}
                      onClick={() => setConfig({ ...config, chart_type: type.id })}
                      className={`px-4 py-3 rounded-lg border-2 font-medium text-sm transition-all ${config.chart_type === type.id
                        ? 'border-blue-500 bg-blue-50 text-blue-700'
                        : 'border-slate-200 text-slate-700 hover:border-blue-300'
                        }`}
                    >
                      <div className="text-2xl mb-1">{type.icon}</div>
                      {isEnglish ? type.label_en : type.label_zh}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Metrics Selection (for KPI cards) */}
            {blockType === 'kpi_cards' && (
              <div className="space-y-3">
                <label className="block text-sm font-semibold text-slate-700">
                  {isEnglish ? '3. Select Metrics' : '3. 选择指标'}
                </label>

                <div className="grid grid-cols-2 gap-3">
                  {[
                    { id: 'price', label_en: 'Current Price', label_zh: '当前价格' },
                    { id: 'price_change', label_en: 'Price Change', label_zh: '价格变化' },
                    { id: 'volume', label_en: 'Trading Volume', label_zh: '交易量' },
                    { id: 'margin', label_en: 'Profit Margin', label_zh: '利润率' },
                  ].map(metric => (
                    <label
                      key={metric.id}
                      className={`flex items-center gap-2 px-3 py-2 rounded-lg border-2 cursor-pointer transition-all ${config.metrics?.includes(metric.id)
                        ? 'border-blue-500 bg-blue-50'
                        : 'border-slate-200 hover:border-blue-300'
                        }`}
                    >
                      <input
                        type="checkbox"
                        checked={config.metrics?.includes(metric.id) || false}
                        onChange={() => {
                          const current = config.metrics || [];
                          const updated = current.includes(metric.id)
                            ? current.filter(id => id !== metric.id)
                            : [...current, metric.id];
                          setConfig({ ...config, metrics: updated });
                        }}
                        className="w-4 h-4 text-blue-600 rounded"
                      />
                      <span className="text-sm font-medium text-slate-700">
                        {isEnglish ? metric.label_en : metric.label_zh}
                      </span>
                    </label>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-6 py-4 border-t border-slate-200 bg-slate-50">
          <button
            onClick={handlePreview}
            disabled={!isValid() || previewLoading || blockType === 'text' || blockType === 'image'}
            className={`px-4 py-2 rounded-lg border border-slate-300 text-slate-700 font-medium hover:bg-white disabled:opacity-50 disabled:cursor-not-allowed ${(blockType === 'text' || blockType === 'image') ? 'invisible' : ''
              }`}
          >
            {previewLoading
              ? (isEnglish ? 'Loading...' : '加载中...')
              : (isEnglish ? '👁 Preview Data' : '👁 预览数据')}
          </button>

          <div className="flex gap-3">
            <button
              onClick={onCancel}
              className="px-6 py-2 rounded-lg border border-slate-300 text-slate-700 font-medium hover:bg-white"
            >
              {isEnglish ? 'Cancel' : '取消'}
            </button>
            <button
              onClick={handleSaveConfiguration}
              disabled={!isValid() || previewLoading}
              className="px-6 py-2 rounded-lg bg-blue-600 text-white font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {previewLoading
                ? (isEnglish ? 'Saving...' : '保存中...')
                : (isEnglish ? 'Save Configuration' : '保存配置')}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
