/**
 * Professional Report Authoring Interface
 * 
 * Enterprise-grade report builder with:
 * - Template gallery
 * - Visual query builder with product/date/filter selection
 * - Live data preview
 * - AI-powered suggestions
 * - Drag-and-drop blocks
 * - Full language support (English/Chinese)
 */

import React, { useState, useEffect } from 'react';
import { useLanguage } from '../lib/language';
import { AdvancedBlockConfigPanel } from './AdvancedBlockConfigPanel';
import {
  api,
  API_BASE_URL,
  type ReportSchedule,
  type ReportBlock as ApiReportBlock,
  type ReportExportFormat,
} from '../services/api';

interface ReportBlock {
  id: string;
  type: string;
  title: string;
  config: BlockConfig;
  order: number;
}

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

interface Template {
  id: string;
  name: string;
  name_en: string;
  description: string;
  category: string;
  thumbnail: string;
  estimated_time: string;
  blocks: any[];
  tags: string[];
  is_user_template?: boolean;
  is_system_template?: boolean;
  is_pinned?: boolean;
  auto_schedule_enabled?: boolean;
  auto_schedule_day?: string;
  auto_schedule_time?: string;
}

interface DataSource {
  id: string;
  name: string;
  name_en: string;
  description: string;
  schema: Record<string, any>;
  sample_count: number;
  last_updated: string;
}

interface ReportCopilotBlock {
  type: string;
  title: string;
  reason: string;
  config?: BlockConfig;
}

interface ReportCopilotPlan {
  contract: string;
  objective: string;
  language: string;
  recommended_template: {
    id: string | null;
    name: string;
    reason: string;
  };
  recommended_blocks: ReportCopilotBlock[];
  required_data: string[];
  missing_data: string[];
  missing_sections: string[];
  readiness: {
    score: number;
    status: string;
    summary: string;
  };
  quality_gates: Record<string, string>;
  next_actions: string[];
}

export const ProfessionalReportAuthoring: React.FC = () => {
  const { language } = useLanguage();
  const [activeView, setActiveView] = useState<'templates' | 'builder'>('templates');
  const [selectedTemplate, setSelectedTemplate] = useState<Template | null>(null);
  const [reportBlocks, setReportBlocks] = useState<ReportBlock[]>([]);
  const [reportTitle, setReportTitle] = useState<string>('');
  const [templates, setTemplates] = useState<Template[]>([]);
  const [dataSources, setDataSources] = useState<DataSource[]>([]);
  const [showDataSourcePanel, setShowDataSourcePanel] = useState(false);
  const [blockInConfig, setBlockInConfig] = useState<ReportBlock | null>(null);
  const [previewData, setPreviewData] = useState<any>(null);
  const [suggestions, setSuggestions] = useState<any[]>([]);
  const [copilotObjective, setCopilotObjective] = useState<string>('');
  const [copilotPlan, setCopilotPlan] = useState<ReportCopilotPlan | null>(null);
  const [copilotLoading, setCopilotLoading] = useState(false);
  const [copilotError, setCopilotError] = useState<string>('');
  const [viewMode, setViewMode] = useState<'edit' | 'split' | 'preview'>('edit');
  const [previewZoom, setPreviewZoom] = useState<number>(100);
  const [blockDataCache, setBlockDataCache] = useState<Map<string, any>>(new Map());
  const [lastRefreshedAt, setLastRefreshedAt] = useState<Map<string, Date>>(new Map());
  const [linkedTemplateId, setLinkedTemplateId] = useState<string | null>(null);
  const [templateSchedule, setTemplateSchedule] = useState<ReportSchedule | null>(null);
  const [hiddenTemplateIds, setHiddenTemplateIds] = useState<string[]>([]);
  const [isBusy, setIsBusy] = useState(false);
  const [statusMessage, setStatusMessage] = useState<string>('');
  const [erpProducts, setErpProducts] = useState<{ product_id: string; name_en: string; name_zh: string }[]>([]);
  const [hasErpAccess, setHasErpAccess] = useState(false);

  const isEnglish = language === 'en';

  // Fetch templates on mount
  useEffect(() => {
    fetchTemplates();
    fetchDataSources();
    fetchErpCatalog();
  }, []);

  // Fetch suggestions when blocks change
  useEffect(() => {
    if (reportBlocks.length > 0) {
      fetchSuggestions();
    }
  }, [reportBlocks]);

  // Fetch real data for all blocks when entering preview mode
  useEffect(() => {
    if (viewMode === 'preview' || viewMode === 'split') {
      fetchAllBlockData();
    }
  }, [viewMode, reportBlocks]);

  const fetchTemplates = async () => {
    try {
      const [galleryData, hiddenTemplates] = await Promise.all([
        api.getReportAuthoringTemplates(),
        api.listHiddenGalleryTemplates().catch(() => ({ hidden_template_ids: [] as string[] })),
      ]);
      setTemplates((galleryData.templates || []) as Template[]);
      setHiddenTemplateIds(hiddenTemplates.hidden_template_ids || []);
    } catch (error) {
      console.error('Failed to fetch templates:', error);
      setTemplates([]);
    }
  };

  const fetchDataSources = async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/api/report-authoring/data-sources`, {
        credentials: 'include'
      });
      const data = await response.json();
      setDataSources(data.data_sources);
    } catch (error) {
      console.error('Failed to fetch data sources:', error);
    }
  };

  const fetchErpCatalog = async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/api/erp/catalog`, {
        credentials: 'include'
      });
      if (response.status === 403 || response.status === 401) {
        setHasErpAccess(false);
        return;
      }
      const data = await response.json();
      setHasErpAccess(true);
      setErpProducts((data.products || []).map((p: any) => ({
        product_id: p.product_id,
        name_en: p.name_en,
        name_zh: p.name_zh,
      })));
    } catch {
      setHasErpAccess(false);
    }
  };

  const fetchSuggestions = async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/api/report-authoring/suggest-blocks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          current_blocks: reportBlocks,
          context: {}
        })
      });
      const data = await response.json();
      setSuggestions(data.suggestions);
    } catch (error) {
      console.error('Failed to fetch suggestions:', error);
    }
  };

  const buildCopilotPlan = async () => {
    const objective = copilotObjective.trim()
      || reportTitle.trim()
      || selectedTemplate?.description
      || (isEnglish ? 'Boss-ready market and ERP report' : '面向老板的市场和 ERP 报告');

    try {
      setCopilotLoading(true);
      setCopilotError('');
      const response = await fetch(`${API_BASE_URL}/api/report-authoring/copilot-plan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          objective,
          current_blocks: reportBlocks,
          language,
        }),
      });

      if (!response.ok) {
        throw new Error(`Copilot plan failed: ${response.status}`);
      }

      const data = await response.json();
      setCopilotPlan(data);
      if (!copilotObjective.trim()) {
        setCopilotObjective(objective);
      }
    } catch (error) {
      console.error('Failed to build report copilot plan:', error);
      setCopilotError(isEnglish ? 'Unable to build plan. Please try again.' : '无法生成计划，请重试。');
    } finally {
      setCopilotLoading(false);
    }
  };

  const fetchAllBlockData = async () => {
    const newCache = new Map();
    const newRefreshedAt = new Map<string, Date>(lastRefreshedAt); // keep existing timestamps

    for (const block of reportBlocks) {
      // Skip blocks without configuration
      const hasProductsOrMetrics = block.config && (block.config.products || block.config.metrics);
      const hasCustomSQL = block.type === 'custom_sql' && block.config && block.config.sql_query && block.config.sql_query.trim().length > 0;
      const hasErpConfig = block.type.startsWith('erp_') && block.config && (block.config as any).product_id;
      if (!block.config || (!hasProductsOrMetrics && !hasCustomSQL && !hasErpConfig)) {
        continue;
      }

      try {
        const response = await fetch(`${API_BASE_URL}/api/report-authoring/preview-block`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify({
            type: block.type,
            config: block.config,
            limit: 20
          })
        });

        if (response.ok) {
          const realData = await response.json();
          newCache.set(block.id, realData);
          newRefreshedAt.set(block.id, new Date()); // record refresh time
        } else {
          // Use sample data as fallback (custom_sql has no meaningful sample)
          if (block.type !== 'custom_sql') {
            const sampleData = generateSampleData(block, language);
            newCache.set(block.id, sampleData);
          }
        }
      } catch (error) {
        console.error(`Failed to fetch data for block ${block.id}:`, error);
        // Use sample data as fallback (custom_sql has no meaningful sample)
        if (block.type !== 'custom_sql') {
          const sampleData = generateSampleData(block, language);
          newCache.set(block.id, sampleData);
        }
      }
    }

    setBlockDataCache(newCache);
    setLastRefreshedAt(newRefreshedAt);
  };

  const deleteTemplate = async (template: Template, event: React.MouseEvent) => {
    event.stopPropagation(); // Prevent card click

    const templateId = template.id;
    if (!templateId) {
      return;
    }

    const isGalleryTemplate = !template.is_user_template;
    const isUserTemplate = !!template.is_user_template;

    // Enhanced warning for scheduled templates and system templates
    let confirmMessage: string;

    if (isGalleryTemplate) {
      // Extra warning for system gallery templates
      confirmMessage = isEnglish
        ? `⚠️ DELETE SYSTEM TEMPLATE?\n\nYou are about to delete the built-in template:\n"${template.name}"\n\nThis is a SYSTEM template. Deleting it will hide it from the gallery permanently.\n\nAre you sure?`
        : `⚠️ 删除系统模板？\n\n您即将删除内置模板：\n"${template.name}"\n\n这是系统模板。删除后将永久从模板库中隐藏。\n\n确定要删除吗？`;
    } else if (template.auto_schedule_enabled && template.auto_schedule_day && template.auto_schedule_time) {
      confirmMessage = isEnglish
        ? `Delete "${template.name}"?\n\nThis template has an active schedule:\n• ${template.auto_schedule_day} at ${template.auto_schedule_time}\n\nDeleting this template will also remove its schedule. This action cannot be undone.`
        : `确定要删除"${template.name}"吗？\n\n此模板有活动的计划：\n• 每周${template.auto_schedule_day === 'Monday' ? '一' : template.auto_schedule_day} ${template.auto_schedule_time}\n\n删除模板将同时删除其计划。此操作无法撤销。`;
    } else {
      confirmMessage = isEnglish
        ? `Are you sure you want to delete "${template.name}"? This action cannot be undone.`
        : `确定要删除"${template.name}"吗？此操作无法撤销。`;
    }

    if (!window.confirm(confirmMessage)) {
      return;
    }

    try {
      setIsBusy(true);
      setStatusMessage(isEnglish ? 'Deleting template...' : '正在删除模板...');

      if (isGalleryTemplate) {
        // For gallery templates, hide them via API (stored in database)
        await api.hideGalleryTemplate(template.id);
      } else if (isUserTemplate) {
        // For user templates, delete from server
        await api.deleteReportTemplate(template.id);
      }

      // Refresh template list (server will filter hidden templates automatically)
      await fetchTemplates();

      setStatusMessage(isEnglish ? 'Template deleted successfully' : '模板已成功删除');
      setTimeout(() => setStatusMessage(''), 3000);
    } catch (error: any) {
      console.error('Failed to delete template:', error);

      // Provide specific error messages
      let errorMsg: string;
      if (error?.message?.includes('403') || error?.message?.includes('Forbidden')) {
        errorMsg = isEnglish
          ? 'Permission denied. Only administrators can delete templates.'
          : '权限不足，只有管理员可以删除模板。';
      } else if (error?.message?.includes('404')) {
        errorMsg = isEnglish
          ? 'Template not found. It may have been already deleted.'
          : '模板未找到，可能已被删除。';
      } else {
        errorMsg = isEnglish
          ? 'Failed to delete template. Please try again.'
          : '删除模板失败，请重试。';
      }

      alert(errorMsg);
    } finally {
      setIsBusy(false);
    }
  };

  const toggleTemplatePin = async (template: Template, event: React.MouseEvent) => {
    event.stopPropagation();
    try {
      setIsBusy(true);
      if (template.is_pinned) {
        await api.unpinReportTemplate(template.id);
      } else {
        await api.pinReportTemplate(template.id);
      }
      await fetchTemplates();
    } catch (error) {
      console.error('Failed to toggle template pin:', error);
      alert(isEnglish ? 'Failed to update template pin.' : '更新模板置顶失败。');
    } finally {
      setIsBusy(false);
    }
  };

  const restoreHiddenTemplates = async () => {
    if (hiddenTemplateIds.length === 0) {
      return;
    }
    try {
      setIsBusy(true);
      await Promise.all(hiddenTemplateIds.map((templateId) => api.unhideGalleryTemplate(templateId)));
      await fetchTemplates();
      setStatusMessage(isEnglish ? 'Hidden templates restored.' : '已恢复隐藏模板。');
      setTimeout(() => setStatusMessage(''), 3000);
    } catch (error) {
      console.error('Failed to restore hidden templates:', error);
      alert(isEnglish ? 'Failed to restore hidden templates.' : '恢复隐藏模板失败。');
    } finally {
      setIsBusy(false);
    }
  };

  const startBlankReport = () => {
    setSelectedTemplate(null);
    setReportBlocks([]);
    setReportTitle('');
    setLinkedTemplateId(null);
    setTemplateSchedule(null);
    setBlockDataCache(new Map());
    setLastRefreshedAt(new Map());
    setPreviewData(null);
    setSuggestions([]);
    setCopilotObjective('');
    setCopilotPlan(null);
    setCopilotError('');
    setBlockInConfig(null);
    setViewMode('edit');
    setActiveView('builder');
  };

  const useTemplate = (template: Template) => {
    const blocks: ReportBlock[] = template.blocks.map((block, index) => ({
      id: `block_${Date.now()}_${index}`,
      type: block.type,
      title: block.title || '',
      config: block.config || {},
      order: index
    }));

    setReportBlocks(blocks);
    setSelectedTemplate(template);
    setReportTitle(isEnglish ? template.name_en : template.name);
    setCopilotObjective(isEnglish ? template.description : template.description);
    setCopilotPlan(null);
    setCopilotError('');
    setLinkedTemplateId(template.is_user_template ? template.id : null);
    if (template.is_user_template) {
      loadTemplateSchedule(template.id);
    } else {
      setTemplateSchedule(null);
    }
    setActiveView('builder');
  };

  const toApiTemplateBlocks = (): ApiReportBlock[] => {
    return reportBlocks.map((block, index) => ({
      id: block.id,
      order: typeof block.order === 'number' ? block.order : index,
      type: block.type,
      title: block.title || getBlockTypeLabel(block.type, language),
      definition: block.config || {},
      rendered: blockDataCache.get(block.id)?.rendered || blockDataCache.get(block.id),
    }));
  };

  const updateCacheFromGeneratedBlocks = (generatedBlocks: ApiReportBlock[] | undefined) => {
    if (!generatedBlocks || generatedBlocks.length === 0) {
      return;
    }
    const nextCache = new Map<string, any>();
    generatedBlocks.forEach((block) => {
      if (block.rendered) {
        nextCache.set(block.id, block.rendered);
      }
    });
    setBlockDataCache(nextCache);
  };

  const loadTemplateSchedule = async (templateId: string) => {
    try {
      const schedule = await api.getReportTemplateSchedule(templateId);
      setTemplateSchedule(schedule);
    } catch (error) {
      console.error('Failed to load template schedule:', error);
      setTemplateSchedule(null);
    }
  };

  const saveAsTemplate = async (): Promise<string | null> => {
    if (reportBlocks.length === 0) {
      alert(isEnglish ? 'Add at least one block before saving a template.' : '请先至少添加一个模块后再保存模板。');
      return null;
    }
    try {
      setIsBusy(true);
      const payload = {
        id: linkedTemplateId || undefined,
        name: reportTitle?.trim() || (isEnglish ? 'Untitled Report Template' : '未命名报告模板'),
        description: selectedTemplate?.description || '',
        time_period: 'weekly',
        audience: '',
        schedule_hint: isEnglish ? 'Weekly Monday regeneration' : '每周一自动生成',
        sections: reportBlocks.map((b, i) => b.title || `${getBlockTypeLabel(b.type, language)} ${i + 1}`),
        template_title: reportTitle?.trim() || (isEnglish ? 'Untitled Report' : '未命名报告'),
        template_markdown: '',
        template_blocks: toApiTemplateBlocks(),
      };
      const saved = await api.saveReportTemplate(payload);
      setLinkedTemplateId(saved.id);
      await loadTemplateSchedule(saved.id);
      await fetchTemplates();
      setStatusMessage(isEnglish ? '✓ Template saved successfully.' : '✓ 模板已成功保存。');
      setTimeout(() => setStatusMessage(''), 4000);
      return saved.id;
    } catch (error) {
      console.error('Failed to save template:', error);
      alert(isEnglish ? 'Failed to save template.' : '保存模板失败。');
      return null;
    } finally {
      setIsBusy(false);
    }
  };

  const refreshData = async () => {
    const configuredBlocks = reportBlocks.filter(block => {
      const hasProductsOrMetrics = block.config && (block.config.products || block.config.metrics);
      const hasCustomSQL = block.type === 'custom_sql' && block.config?.sql_query?.trim().length > 0;
      return hasProductsOrMetrics || hasCustomSQL;
    });
    if (configuredBlocks.length === 0) {
      setStatusMessage(isEnglish ? 'No configured blocks to refresh. Add and configure a block first.' : '没有已配置的模块，请先添加并配置模块。');
      setTimeout(() => setStatusMessage(''), 4000);
      return;
    }
    try {
      setIsBusy(true);
      setStatusMessage(isEnglish ? 'Refreshing data from live sources...' : '正在从实时数据源刷新...');
      await fetchAllBlockData();
      setStatusMessage(isEnglish ? `✓ Data refreshed — ${configuredBlocks.length} block(s) updated.` : `✓ 数据已刷新 — 已更新 ${configuredBlocks.length} 个模块。`);
      setTimeout(() => setStatusMessage(''), 5000);
    } catch (error) {
      console.error('Failed to refresh block data:', error);
      setStatusMessage(isEnglish ? '✗ Failed to refresh data. Please try again.' : '✗ 数据刷新失败，请重试。');
      setTimeout(() => setStatusMessage(''), 5000);
    } finally {
      setIsBusy(false);
    }
  };

  const updateWeeklySchedule = async (enabled: boolean) => {
    try {
      setIsBusy(true);
      const templateId = linkedTemplateId || (await saveAsTemplate());
      if (!templateId) {
        return;
      }
      const schedule = await api.updateReportTemplateSchedule(templateId, {
        enabled,
        hour: templateSchedule?.hour ?? 7,
        minute: templateSchedule?.minute ?? 0,
      });
      setTemplateSchedule(schedule);
      const msg = enabled
        ? (isEnglish ? '✓ Monday Auto-Run enabled — report will generate every Monday at 07:00 (Shanghai).' : '✓ 已启用每周一自动生成 — 每周一 07:00（上海时间）自动运行。')
        : (isEnglish ? '✓ Monday Auto-Run disabled.' : '✓ 每周一自动生成已禁用。');
      setStatusMessage(msg);
      setTimeout(() => setStatusMessage(''), 5000);
    } catch (error) {
      console.error('Failed to update weekly schedule:', error);
      setStatusMessage(isEnglish ? '✗ Failed to update schedule. Please try again.' : '✗ 更新计划失败，请重试。');
      setTimeout(() => setStatusMessage(''), 5000);
    } finally {
      setIsBusy(false);
    }
  };

  const addBlock = (type: string, config: any = {}) => {
    const newBlock: ReportBlock = {
      id: `block_${Date.now()}`,
      type,
      title: '',
      config,
      order: reportBlocks.length
    };

    setReportBlocks([...reportBlocks, newBlock]);
  };

  const deleteBlock = (blockId: string) => {
    setReportBlocks(reportBlocks.filter(b => b.id !== blockId));
  };

  const configureBlock = (block: ReportBlock) => {
    setBlockInConfig(block);
    setShowDataSourcePanel(true);
  };

  const previewBlock = async (block: ReportBlock) => {
    // Check if block is configured
    const hasProductsOrMetrics = block.config && (block.config.products || block.config.metrics || block.config.data_source);
    const hasCustomSQL = block.config && block.config.sql_query && block.config.sql_query.trim().length > 0;

    const hasErpConfigured = block.type.startsWith('erp_') && block.config && (block.config as any).product_id;
    if (!block.config || (!hasProductsOrMetrics && !hasCustomSQL && !hasErpConfigured)) {
      alert(isEnglish ? 'Please configure the block first by clicking ⚙️' : '请先点击 ⚙️ 配置此模块');
      return;
    }

    console.log('🔍 Preview Block - Fetching REAL data from API...');
    console.log('Block config:', block.config);

    try {
      const controller = new AbortController();
      const timeoutId = window.setTimeout(() => controller.abort(), 15000);

      let response: Response;
      try {
        // Fetch REAL data from backend API
        response = await fetch(`${API_BASE_URL}/api/report-authoring/preview-block`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          signal: controller.signal,
          body: JSON.stringify({
            type: block.type,
            config: block.config,
            limit: 20
          })
        });
      } finally {
        window.clearTimeout(timeoutId);
      }

      console.log('API Response status:', response.status);

      if (!response.ok) {
        const errorText = await response.text();
        console.error('API Error:', errorText);
        throw new Error(`API returned ${response.status}: ${errorText}`);
      }

      const realData = await response.json();
      console.log('✅ SUCCESS: Received REAL data from database:', realData);

      // Cache the data for this block
      const newCache = new Map(blockDataCache);
      newCache.set(block.id, realData);
      setBlockDataCache(newCache);

      setPreviewData(realData);
    } catch (error) {
      console.error('❌ Preview API FAILED:', error);
      if (error instanceof DOMException && error.name === 'AbortError') {
        console.warn('⚠️ Preview request timed out after 15s');
      }
      console.warn('⚠️ Falling back to MOCK sample data');
      // Fallback to sample data if API fails
      const sampleData = generateSampleData(block, language);
      setPreviewData(sampleData);
    }
  };

  const exportReport = async (format: string) => {
    try {
      setIsBusy(true);
      const response = await fetch(`${API_BASE_URL}/api/reports/export-professional`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          title: reportTitle?.trim() || selectedTemplate?.name || 'Custom Report',
          generated_at: (lastRefreshedAt.size > 0 ? Array.from(lastRefreshedAt.values()).reduce((a, b) => a > b ? a : b).toISOString() : new Date().toISOString()),
          format,
          blocks: toApiTemplateBlocks().map(block => ({
            type: block.type,
            title: block.title,
            definition: block.definition,
            rendered: block.rendered,
            order: block.order
          }))
        })
      });

      if (!response.ok) {
        throw new Error('Export failed');
      }

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `report_${Date.now()}.${format}`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      window.URL.revokeObjectURL(url);
      setStatusMessage(isEnglish ? 'Professional export completed.' : '专业导出已完成。');
    } catch (error) {
      console.error('Export failed:', error);
      alert('导出失败');
    } finally {
      setIsBusy(false);
    }
  };

  return (
    <div className="professional-report-authoring">
      {activeView === 'templates' ? (
        <TemplateGallery
          templates={templates}
          onSelectTemplate={useTemplate}
          onStartBlank={startBlankReport}
          onDeleteTemplate={deleteTemplate}
          onTogglePin={toggleTemplatePin}
          onRestoreHidden={restoreHiddenTemplates}
          hiddenTemplateCount={hiddenTemplateIds.length}
        />
      ) : (
        <ReportBuilder
          blocks={reportBlocks}
          reportTitle={reportTitle}
          onReportTitleChange={setReportTitle}
          dataSources={dataSources}
          suggestions={suggestions}
          copilotObjective={copilotObjective}
          copilotPlan={copilotPlan}
          copilotLoading={copilotLoading}
          copilotError={copilotError}
          onCopilotObjectiveChange={setCopilotObjective}
          onBuildCopilotPlan={buildCopilotPlan}
          viewMode={viewMode}
          onViewModeChange={setViewMode}
          previewZoom={previewZoom}
          onZoomChange={setPreviewZoom}
          onAddBlock={addBlock}
          onConfigureBlock={configureBlock}
          onPreviewBlock={previewBlock}
          onDeleteBlock={deleteBlock}
          onExport={exportReport}
          onRefreshData={refreshData}
          onSaveTemplate={saveAsTemplate}
          onToggleWeeklySchedule={updateWeeklySchedule}
          weeklyScheduleEnabled={templateSchedule?.enabled || false}
          nextRunAt={templateSchedule?.next_run_at || null}
          isBusy={isBusy}
          onBackToTemplates={() => setActiveView('templates')}
          language={language}
          blockDataCache={blockDataCache}
          lastRefreshedAt={lastRefreshedAt}
          statusMessage={statusMessage}
          hasErpAccess={hasErpAccess}
          erpProducts={erpProducts}
        />
      )}

      {showDataSourcePanel && blockInConfig && (
        <AdvancedBlockConfigPanel
          blockType={blockInConfig.type}
          initialConfig={blockInConfig.config}
          onSave={(updatedConfig) => {
            setReportBlocks(reportBlocks.map(b =>
              b.id === blockInConfig.id ? { ...b, config: updatedConfig } : b
            ));
            setShowDataSourcePanel(false);
            setBlockInConfig(null);
          }}
          onCancel={() => {
            setShowDataSourcePanel(false);
            setBlockInConfig(null);
          }}
          onPreview={async (config) => {
            await previewBlock({ ...blockInConfig, config });
          }}
        />
      )}

      {previewData && (
        <DataPreviewModal
          data={previewData}
          onClose={() => setPreviewData(null)}
        />
      )}

      <style>{`
        .professional-report-authoring {
          height: 100vh;
          background: #f8f9fa;
        }
      `}</style>
    </div>
  );
};

// Template Gallery Component
const TemplateGallery: React.FC<{
  templates: Template[];
  onSelectTemplate: (template: Template) => void;
  onStartBlank: () => void;
  onDeleteTemplate: (template: Template, event: React.MouseEvent) => void;
  onTogglePin: (template: Template, event: React.MouseEvent) => void;
  onRestoreHidden: () => void;
  hiddenTemplateCount: number;
}> = ({ templates, onSelectTemplate, onStartBlank, onDeleteTemplate, onTogglePin, onRestoreHidden, hiddenTemplateCount }) => {
  const { language } = useLanguage();
  const [filter, setFilter] = useState<string>('all');

  const isEnglish = language === 'en';

  // Add null check for templates
  const safeTemplates = [...(templates || [])].sort((left, right) => {
    const pinDelta = Number(Boolean(right.is_pinned)) - Number(Boolean(left.is_pinned));
    if (pinDelta !== 0) {
      return pinDelta;
    }
    const systemDelta = Number(Boolean(right.is_system_template)) - Number(Boolean(left.is_system_template));
    if (systemDelta !== 0) {
      return systemDelta;
    }
    return left.name.localeCompare(right.name);
  });
  const filteredTemplates = safeTemplates.filter(t =>
    filter === 'all' || t.category === filter
  );

  const categories = ['all', ...new Set(safeTemplates.map(t => t.category))];

  return (
    <div className="template-gallery">
      <div className="gallery-header">
        <div className="header-content">
          <h1>{isEnglish ? 'Create Report' : '创建报告'}</h1>
          <p>{isEnglish ? 'Start quickly from templates, or customize from scratch' : '从模板快速开始，或从空白开始自定义'}</p>
        </div>

        <button className="btn-new-blank" onClick={onStartBlank}>
          <span className="icon">+</span>
          {isEnglish ? 'Start from Blank' : '从空白开始'}
        </button>
      </div>

      <div className="gallery-filters">
        {categories.map(cat => (
          <button
            key={cat}
            className={`filter-btn ${filter === cat ? 'active' : ''}`}
            onClick={() => setFilter(cat)}
          >
            {cat === 'all' ? (isEnglish ? 'All Templates' : '全部模板') : cat}
          </button>
        ))}
      </div>

      <div className="template-grid">
        {filteredTemplates.length === 0 && (
          <div className="template-empty-state">
            <h3>{isEnglish ? 'No templates available' : '暂无可用模板'}</h3>
            <p>
              {hiddenTemplateCount > 0
                ? (isEnglish ? 'All visible templates are currently hidden. Restore them or start from a blank report.' : '当前可见模板都已被隐藏。您可以恢复隐藏模板，或从空白报告开始。')
                : (isEnglish ? 'Templates are loaded from the database. Create one from a blank report to save your first reusable template.' : '模板现在从数据库加载。您可以先从空白报告开始并保存第一个可复用模板。')}
            </p>
            {hiddenTemplateCount > 0 && (
              <button className="btn-restore-hidden" onClick={onRestoreHidden}>
                {isEnglish ? `Restore Hidden Templates (${hiddenTemplateCount})` : `恢复隐藏模板（${hiddenTemplateCount}）`}
              </button>
            )}
          </div>
        )}
        {filteredTemplates.map(template => (
          <div
            key={template.id}
            className="template-card"
            onClick={() => onSelectTemplate(template)}
          >
            <div className="template-thumbnail">
              <img
                src={template.thumbnail || '/logo2.png'}
                alt={template.name}
                onError={(event) => {
                  const image = event.currentTarget;
                  if (image.dataset.fallbackApplied === 'true') {
                    return;
                  }
                  image.dataset.fallbackApplied = 'true';
                  image.src = '/logo2.png';
                }}
              />
              <button
                className={`btn-pin-template ${template.is_pinned ? 'active' : ''}`}
                onClick={(e) => onTogglePin(template, e)}
                title={template.is_pinned
                  ? (isEnglish ? 'Unpin template' : '取消置顶模板')
                  : (isEnglish ? 'Pin template' : '置顶模板')}
              >
                {template.is_pinned ? '★' : '☆'}
              </button>
              {template.auto_schedule_enabled && (
                <div className="schedule-badge">
                  <span className="schedule-icon">⏰</span>
                  <span className="schedule-text">
                    {isEnglish
                      ? `Every ${template.auto_schedule_day} at ${template.auto_schedule_time}`
                      : `每周${template.auto_schedule_day === 'Monday' ? '一' : template.auto_schedule_day} ${template.auto_schedule_time}`}
                  </span>
                </div>
              )}
              <div className="template-overlay">
                <button className="btn-use-template">使用模板</button>
                {template.id && (
                  <button
                    className="btn-delete-template"
                    onClick={(e) => onDeleteTemplate(template, e)}
                    title={isEnglish ? 'Delete template' : '删除模板'}
                  >
                    🗑️
                  </button>
                )}
              </div>
            </div>

            <div className="template-info">
              <h3>{template.name}</h3>
              <p className="template-desc">
                {template.description || (template.is_user_template
                  ? (isEnglish ? 'Saved custom report template' : '已保存的自定义报告模板')
                  : (isEnglish ? 'Reusable report template' : '可复用报告模板'))}
              </p>

              <div className="template-meta">
                <span className="meta-time">
                  <span className="icon">⏱</span>
                  {template.estimated_time}
                </span>
                {template.is_pinned && (
                  <span className="meta-pinned">
                    <span className="icon">★</span>
                    {isEnglish ? 'Pinned' : '已置顶'}
                  </span>
                )}
                <span className="meta-blocks">
                  {template.blocks.length} 个模块
                </span>
              </div>

              <div className="template-tags">
                {template.tags.map(tag => (
                  <span key={tag} className="tag">{tag}</span>
                ))}
              </div>
            </div>
          </div>
        ))}
      </div>

      <style>{`
        .template-gallery {
          padding: 40px;
          max-width: 1400px;
          margin: 0 auto;
        }

        .gallery-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 32px;
        }

        .header-content h1 {
          font-size: 32px;
          font-weight: 600;
          color: #1e293b;
          margin-bottom: 8px;
        }

        .header-content p {
          font-size: 16px;
          color: #64748b;
        }

        .btn-new-blank {
          padding: 12px 24px;
          background: linear-gradient(135deg, #1e3a8a 0%, #3b82f6 100%);
          color: white;
          border: none;
          border-radius: 8px;
          font-size: 15px;
          font-weight: 500;
          cursor: pointer;
          display: flex;
          align-items: center;
          gap: 8px;
          transition: opacity 0.2s;
        }

        .btn-new-blank:hover {
          opacity: 0.9;
        }

        .btn-new-blank .icon {
          font-size: 20px;
        }

        .gallery-filters {
          display: flex;
          gap: 12px;
          margin-bottom: 32px;
        }

        .filter-btn {
          padding: 8px 16px;
          background: white;
          border: 1px solid #e5e7eb;
          border-radius: 6px;
          font-size: 14px;
          color: #64748b;
          cursor: pointer;
          transition: all 0.2s;
        }

        .filter-btn:hover {
          border-color: #3b82f6;
          color: #3b82f6;
        }

        .filter-btn.active {
          background: #3b82f6;
          border-color: #3b82f6;
          color: white;
        }

        .template-grid {
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
          gap: 24px;
        }

        .template-empty-state {
          grid-column: 1 / -1;
          padding: 48px 32px;
          border: 1px dashed #cbd5e1;
          border-radius: 16px;
          background: linear-gradient(180deg, #f8fafc 0%, #ffffff 100%);
          text-align: center;
        }

        .template-empty-state h3 {
          font-size: 22px;
          color: #1e293b;
          margin-bottom: 10px;
        }

        .template-empty-state p {
          max-width: 560px;
          margin: 0 auto 20px;
          color: #64748b;
          line-height: 1.6;
        }

        html.dark .template-gallery {
          background: var(--app-bg);
          min-height: calc(100vh - 56px);
        }

        html.dark .header-content h1 {
          color: #ffffff;
        }

        html.dark .header-content p {
          color: #c1c2c5;
        }

        html.dark .filter-btn {
          background: rgb(28 31 36);
          border-color: #373a40;
          color: #c1c2c5;
        }

        html.dark .filter-btn:hover {
          border-color: #3b82f6;
          color: #93c5fd;
        }

        html.dark .filter-btn.active {
          background: #3b82f6;
          border-color: #3b82f6;
          color: #ffffff;
        }

        html.dark .template-empty-state {
          border-color: #373a40;
          background: linear-gradient(180deg, rgb(28 31 36) 0%, rgb(37 38 43) 100%);
        }

        html.dark .template-empty-state h3 {
          color: #ffffff;
        }

        html.dark .template-empty-state p {
          color: #c1c2c5;
        }

        html.dark .template-card {
          background: rgb(28 31 36);
          border-color: #373a40;
        }

        html.dark .template-card h3,
        html.dark .template-card p {
          color: #e5e7eb;
        }

        .btn-restore-hidden {
          padding: 10px 18px;
          border: none;
          border-radius: 8px;
          background: #1d4ed8;
          color: white;
          font-size: 14px;
          font-weight: 600;
          cursor: pointer;
        }

        .btn-restore-hidden:hover {
          background: #1e40af;
        }

        .template-card {
          background: white;
          border-radius: 12px;
          overflow: hidden;
          cursor: pointer;
          transition: all 0.3s;
          border: 1px solid #e5e7eb;
        }

        .template-card:hover {
          box-shadow: 0 10px 30px rgba(0, 0, 0, 0.1);
          transform: translateY(-2px);
        }

        .template-thumbnail {
          position: relative;
          width: 100%;
          height: 200px;
          background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
          overflow: hidden;
        }

        .template-thumbnail img {
          width: 100%;
          height: 100%;
          object-fit: cover;
        }

        .schedule-badge {
          position: absolute;
          top: 12px;
          right: 12px;
          background: rgba(34, 197, 94, 0.95);
          color: white;
          padding: 6px 12px;
          border-radius: 6px;
          font-size: 12px;
          font-weight: 500;
          display: flex;
          align-items: center;
          gap: 6px;
          box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
          z-index: 1;
        }

        .btn-pin-template {
          position: absolute;
          top: 12px;
          left: 12px;
          width: 36px;
          height: 36px;
          border: none;
          border-radius: 999px;
          background: rgba(255, 255, 255, 0.92);
          color: #475569;
          font-size: 18px;
          cursor: pointer;
          display: flex;
          align-items: center;
          justify-content: center;
          box-shadow: 0 2px 10px rgba(15, 23, 42, 0.15);
          z-index: 2;
          transition: transform 0.2s, background 0.2s, color 0.2s;
        }

        .btn-pin-template:hover {
          transform: scale(1.05);
          background: white;
        }

        .btn-pin-template.active {
          background: #fbbf24;
          color: #7c2d12;
        }

        .schedule-icon {
          font-size: 14px;
        }

        .schedule-text {
          white-space: nowrap;
        }

        .template-overlay {
          position: absolute;
          inset: 0;
          background: rgba(0, 0, 0, 0.6);
          display: flex;
          align-items: center;
          justify-content: center;
          opacity: 0;
          transition: opacity 0.3s;
        }

        .template-card:hover .template-overlay {
          opacity: 1;
        }

        .btn-use-template {
          padding: 10px 20px;
          background: white;
          color: #1e3a8a;
          border: none;
          border-radius: 6px;
          font-weight: 500;
          cursor: pointer;
        }

        .btn-delete-template {
          padding: 8px 12px;
          background: #ef4444;
          color: white;
          border: none;
          border-radius: 6px;
          font-size: 16px;
          cursor: pointer;
          margin-left: 8px;
          transition: all 0.2s;
        }

        .btn-delete-template:hover {
          background: #dc2626;
          transform: scale(1.05);
        }

        .template-info {
          padding: 20px;
        }

        .template-info h3 {
          font-size: 18px;
          font-weight: 600;
          color: #1e293b;
          margin-bottom: 8px;
        }

        .template-desc {
          font-size: 14px;
          color: #64748b;
          margin-bottom: 16px;
          line-height: 1.5;
        }

        .template-meta {
          display: flex;
          flex-wrap: wrap;
          gap: 16px;
          margin-bottom: 12px;
          font-size: 13px;
          color: #64748b;
        }

        .meta-time {
          display: flex;
          align-items: center;
          gap: 4px;
        }

        .meta-pinned {
          display: flex;
          align-items: center;
          gap: 4px;
          color: #b45309;
          font-weight: 600;
        }

        .template-tags {
          display: flex;
          flex-wrap: wrap;
          gap: 6px;
        }

        .tag {
          padding: 4px 10px;
          background: #f1f5f9;
          color: #475569;
          border-radius: 4px;
          font-size: 12px;
        }
      `}</style>
    </div>
  );
};

// Report Builder Component
const ReportBuilder: React.FC<{
  blocks: ReportBlock[];
  reportTitle: string;
  onReportTitleChange: (title: string) => void;
  dataSources: DataSource[];
  suggestions: any[];
  copilotObjective: string;
  copilotPlan: ReportCopilotPlan | null;
  copilotLoading: boolean;
  copilotError: string;
  onCopilotObjectiveChange: (objective: string) => void;
  onBuildCopilotPlan: () => void;
  viewMode: 'edit' | 'split' | 'preview';
  onViewModeChange: (mode: 'edit' | 'split' | 'preview') => void;
  previewZoom: number;
  onZoomChange: (zoom: number) => void;
  onAddBlock: (type: string, config?: any) => void;
  onConfigureBlock: (block: ReportBlock) => void;
  onPreviewBlock: (block: ReportBlock) => void;
  onDeleteBlock: (blockId: string) => void;
  onExport: (format: string) => void;
  onRefreshData: () => void;
  onSaveTemplate: () => void;
  onToggleWeeklySchedule: (enabled: boolean) => void;
  weeklyScheduleEnabled: boolean;
  nextRunAt: string | null;
  isBusy: boolean;
  onBackToTemplates: () => void;
  language: string;
  blockDataCache: Map<string, any>;
  lastRefreshedAt: Map<string, Date>;
  statusMessage: string;
  hasErpAccess: boolean;
  erpProducts: { product_id: string; name_en: string; name_zh: string }[];
}> = ({
  blocks,
  reportTitle,
  onReportTitleChange,
  dataSources,
  suggestions,
  copilotObjective,
  copilotPlan,
  copilotLoading,
  copilotError,
  onCopilotObjectiveChange,
  onBuildCopilotPlan,
  viewMode,
  onViewModeChange,
  previewZoom,
  onZoomChange,
  onAddBlock,
  onConfigureBlock,
  onPreviewBlock,
  onDeleteBlock,
  onExport,
  onRefreshData,
  onSaveTemplate,
  onToggleWeeklySchedule,
  weeklyScheduleEnabled,
  nextRunAt,
  isBusy,
  onBackToTemplates,
  language,
  blockDataCache,
  lastRefreshedAt,
  statusMessage,
  hasErpAccess,
  erpProducts,
}) => {
    const isEnglish = language === 'en';

    /** Format a Date to compact "Apr 27, 09:14" */
    const formatRefreshedAt = (d: Date): string => {
      return d.toLocaleString(isEnglish ? 'en-US' : 'zh-CN', {
        month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false
      });
    };

    /** Resolve a date_range config to a human-readable "Apr 20 – Apr 27" string */
    const resolveDateRangeLabel = (dateRange: any): string | null => {
      if (!dateRange) return null;
      const today = new Date();
      const fmt = (d: Date) => d.toLocaleDateString(isEnglish ? 'en-US' : 'zh-CN', { month: 'short', day: 'numeric' });
      if (dateRange.type === 'relative' && dateRange.relative) {
        const { value, unit } = dateRange.relative;
        const end = new Date(today);
        const start = new Date(today);
        if (value === 0) return fmt(today);
        if (unit === 'days') start.setDate(start.getDate() - value);
        else if (unit === 'weeks') start.setDate(start.getDate() - value * 7);
        else if (unit === 'months') start.setMonth(start.getMonth() - value);
        return `${fmt(start)} – ${fmt(end)}`;
      }
      if (dateRange.type === 'range' && dateRange.start && dateRange.end) {
        return `${fmt(new Date(dateRange.start))} – ${fmt(new Date(dateRange.end))}`;
      }
      return null;
    };

    /** Format next_run_at ISO string to "Mon May 4, 07:00" */
    const formatNextRun = (iso: string): string => {
      const d = new Date(iso);
      return d.toLocaleString(isEnglish ? 'en-US' : 'zh-CN', {
        weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false
      });
    };

    return (
      <div className="report-builder">
        <div className="builder-header">
          <button className="btn-back" onClick={onBackToTemplates}>
            ← {isEnglish ? 'Back to Templates' : '返回模板'}
          </button>

          <div className="view-mode-toggle">
            <button
              className={`mode-btn ${viewMode === 'edit' ? 'active' : ''}`}
              onClick={() => onViewModeChange('edit')}
              title={isEnglish ? 'Edit Mode' : '编辑模式'}
            >
              ✏️ {isEnglish ? 'Edit' : '编辑'}
            </button>
            <button
              className={`mode-btn ${viewMode === 'split' ? 'active' : ''}`}
              onClick={() => onViewModeChange('split')}
              title={isEnglish ? 'Split View' : '分屏视图'}
            >
              ⬌ {isEnglish ? 'Split' : '分屏'}
            </button>
            <button
              className={`mode-btn ${viewMode === 'preview' ? 'active' : ''}`}
              onClick={() => onViewModeChange('preview')}
              title={isEnglish ? 'Preview Mode' : '预览模式'}
            >
              👁 {isEnglish ? 'Preview' : '预览'}
            </button>
          </div>

          <div className="header-actions">
            {(viewMode === 'split' || viewMode === 'preview') && (
              <div className="zoom-controls">
                <button onClick={() => onZoomChange(Math.max(50, previewZoom - 10))}>−</button>
                <span>{previewZoom}%</span>
                <button onClick={() => onZoomChange(Math.min(200, previewZoom + 10))}>+</button>
              </div>
            )}
            <button className="btn-action" onClick={onRefreshData} disabled={isBusy}>
              {isEnglish ? 'Refresh Data' : '刷新数据'}
            </button>
            <button className="btn-action" onClick={onSaveTemplate} disabled={isBusy}>
              {isEnglish ? 'Save Template' : '保存模板'}
            </button>
            <button
              className={`btn-action ${weeklyScheduleEnabled ? 'primary' : ''}`}
              onClick={() => onToggleWeeklySchedule(!weeklyScheduleEnabled)}
              disabled={isBusy}
              title={weeklyScheduleEnabled && nextRunAt
                ? (isEnglish ? `Next run: ${formatNextRun(nextRunAt)}` : `下次运行: ${formatNextRun(nextRunAt)}`)
                : undefined}
            >
              {weeklyScheduleEnabled
                ? (isEnglish
                  ? `✓ Auto-Run ON${nextRunAt ? ` · Next: ${formatNextRun(nextRunAt)}` : ''}`
                  : `✓ 自动运行 ON${nextRunAt ? ` · ${formatNextRun(nextRunAt)}` : ''}`)
                : (isEnglish ? 'Enable Monday Auto-Run' : '开启周一自动生成')}
            </button>
            <button className="btn-action" onClick={() => onExport('html')}>
              {isEnglish ? 'Export HTML' : '导出 HTML'}
            </button>
            <button className="btn-action" onClick={() => onExport('pdf')}>
              {isEnglish ? 'Export PDF' : '导出 PDF'}
            </button>
            <button className="btn-action primary" onClick={() => onExport('docx')}>
              {isEnglish ? 'Export Word' : '导出 Word'}
            </button>
          </div>
        </div>

        {statusMessage && (
          <div className="builder-status-banner">
            {statusMessage}
          </div>
        )}

        <div className="builder-content" data-view-mode={viewMode}>
          {/* Edit Mode: Show blocks panel, canvas, and properties */}
          {viewMode === 'edit' && (
            <>
              <div className="blocks-panel">
                <h3>{isEnglish ? 'Content Modules' : '内容模块'}</h3>

                <div className="block-categories">
                  <div className="block-category">
                    <h4>{isEnglish ? 'DATA MODULES' : '数据模块'}</h4>
                    <button className="block-btn" onClick={() => onAddBlock('kpi_cards')}>
                      <span className="icon">📊</span>
                      {isEnglish ? 'Key Indicator Cards' : '关键指标卡片'}
                    </button>
                    <button className="block-btn" onClick={() => onAddBlock('data_table')}>
                      <span className="icon">📋</span>
                      {isEnglish ? 'Data Table' : '数据表格'}
                    </button>
                    <button className="block-btn" onClick={() => onAddBlock('chart')}>
                      <span className="icon">📈</span>
                      {isEnglish ? 'Chart' : '图表'}
                    </button>
                    <button className="block-btn" onClick={() => onAddBlock('custom_sql')}>
                      <span className="icon">🔍</span>
                      {isEnglish ? 'Custom SQL Query' : '自定义查询'}
                    </button>
                  </div>

                  <div className="block-category">
                    <h4>{isEnglish ? 'CONTENT MODULES' : '内容模块'}</h4>
                    <button className="block-btn" onClick={() => onAddBlock('text')}>
                      <span className="icon">📝</span>
                      {isEnglish ? 'Text Description' : '文字说明'}
                    </button>
                    <button className="block-btn" onClick={() => onAddBlock('image')}>
                      <span className="icon">🖼</span>
                      {isEnglish ? 'Image' : '图片'}
                    </button>
                  </div>

                  {hasErpAccess && (
                    <div className="block-category erp-category">
                      <h4>{isEnglish ? 'ERP / SALES DATA' : 'ERP / 销售数据'}</h4>
                      <button className="block-btn" onClick={() => onAddBlock('erp_sales_summary')}>
                        <span className="icon">💰</span>
                        {isEnglish ? 'Sales Summary' : '销售汇总'}
                      </button>
                      <button className="block-btn" onClick={() => onAddBlock('erp_customer_breakdown')}>
                        <span className="icon">🏢</span>
                        {isEnglish ? 'Customer Breakdown' : '客户明细'}
                      </button>
                      <button className="block-btn" onClick={() => onAddBlock('erp_vs_market')}>
                        <span className="icon">📊</span>
                        {isEnglish ? 'Actual vs Market Price' : '成交价 vs 市场价'}
                      </button>
                      <button className="block-btn" onClick={() => onAddBlock('erp_delivery_status')}>
                        <span className="icon">🚚</span>
                        {isEnglish ? 'Delivery Status' : '交货状态'}
                      </button>
                      <button className="block-btn" onClick={() => onAddBlock('erp_monthly_trend')}>
                        <span className="icon">📅</span>
                        {isEnglish ? 'Monthly Sales Trend' : '月度销售走势'}
                      </button>
                    </div>
                  )}
                </div>

                <div className="report-copilot-panel">
                  <h4>{isEnglish ? 'Report Copilot' : '报告助手'}</h4>
                  <textarea
                    value={copilotObjective}
                    onChange={(event) => onCopilotObjectiveChange(event.target.value)}
                    placeholder={isEnglish ? 'Describe the report goal...' : '描述报告目标...'}
                    rows={3}
                  />
                  <button className="copilot-run-btn" onClick={onBuildCopilotPlan} disabled={copilotLoading}>
                    {copilotLoading ? (isEnglish ? 'Planning...' : '生成中...') : (isEnglish ? 'Build Plan' : '生成计划')}
                  </button>
                  {copilotError && <div className="copilot-error">{copilotError}</div>}
                  {copilotPlan && (
                    <div className="copilot-plan">
                      <div className="copilot-readiness">
                        <span>{isEnglish ? 'Readiness' : '就绪度'}</span>
                        <strong>{copilotPlan.readiness.score}%</strong>
                        <em>{copilotPlan.readiness.status}</em>
                      </div>
                      <p className="copilot-summary">{copilotPlan.readiness.summary}</p>
                      <div className="copilot-template">
                        <div className="suggestion-title">
                          {isEnglish ? 'Template' : '推荐模板'}: {copilotPlan.recommended_template.name}
                        </div>
                        <div className="suggestion-reason">{copilotPlan.recommended_template.reason}</div>
                      </div>
                      {(copilotPlan.required_data.length > 0 || copilotPlan.missing_data.length > 0) && (
                        <div className="copilot-data-row">
                          <div>
                            <span>{isEnglish ? 'Required' : '需要数据'}</span>
                            <strong>{copilotPlan.required_data.join(', ') || '-'}</strong>
                          </div>
                          <div>
                            <span>{isEnglish ? 'Missing' : '缺口'}</span>
                            <strong>{copilotPlan.missing_data.join(', ') || '-'}</strong>
                          </div>
                        </div>
                      )}
                      {copilotPlan.recommended_blocks.length > 0 && (
                        <div className="copilot-blocks">
                          {copilotPlan.recommended_blocks.map((block, index) => (
                            <button
                              key={`${block.type}_${index}`}
                              className="suggestion-btn"
                              onClick={() => onAddBlock(block.type, block.config)}
                            >
                              <div className="suggestion-title">{block.title}</div>
                              <div className="suggestion-reason">{block.reason}</div>
                            </button>
                          ))}
                        </div>
                      )}
                      {copilotPlan.next_actions.length > 0 && (
                        <ul className="copilot-actions">
                          {copilotPlan.next_actions.slice(0, 3).map((action, index) => (
                            <li key={`${action}_${index}`}>{action}</li>
                          ))}
                        </ul>
                      )}
                    </div>
                  )}
                </div>

                {suggestions && suggestions.length > 0 && (
                  <div className="suggestions">
                    <h4>💡 {isEnglish ? 'Smart Suggestions' : '智能建议'}</h4>
                    {suggestions.map((suggestion, index) => (
                      <button
                        key={index}
                        className="suggestion-btn"
                        onClick={() => onAddBlock(suggestion.type, suggestion.config)}
                      >
                        <div className="suggestion-title">{suggestion.title}</div>
                        <div className="suggestion-reason">{suggestion.reason}</div>
                      </button>
                    ))}
                  </div>
                )}
              </div>

              <div className="canvas-panel">
                <div className="canvas-header">
                  <input
                    type="text"
                    className="report-title-input"
                    placeholder={isEnglish ? "Enter report title..." : "输入报告标题..."}
                    value={reportTitle ?? (isEnglish ? "Untitled Report" : "未命名报告")}
                    onChange={(e) => onReportTitleChange(e.target.value)}
                  />
                </div>

                <div className="canvas-blocks">
                  {blocks.length === 0 ? (
                    <div className="empty-state">
                      <div className="empty-icon">📄</div>
                      <h3>{isEnglish ? 'Start Building Your Report' : '开始构建你的报告'}</h3>
                      <p>{isEnglish ? 'Select modules from the left to add to your report' : '从左侧选择模块添加到报告中'}</p>
                    </div>
                  ) : (
                    blocks.map(block => (
                      <div key={block.id} className="canvas-block">
                        <div className="block-header">
                          <div className="block-header-left">
                            <span className="block-type-label">{getBlockTypeLabel(block.type, language)}</span>
                            {block.config?.date_range && (() => {
                              const label = resolveDateRangeLabel(block.config.date_range);
                              return label ? <span className="block-date-badge">{label}</span> : null;
                            })()}
                            {lastRefreshedAt.get(block.id) && (
                              <span className="block-refresh-time">
                                {isEnglish ? 'Refreshed' : '已刷新'}: {formatRefreshedAt(lastRefreshedAt.get(block.id)!)}
                              </span>
                            )}
                          </div>
                          <div className="block-actions">
                            <button onClick={() => onConfigureBlock(block)} title={language === 'en' ? 'Configure' : '配置'}>⚙️</button>
                            <button onClick={() => onPreviewBlock(block)} title={language === 'en' ? 'Preview' : '预览'}>👁</button>
                            <button onClick={() => onDeleteBlock(block.id)} title={language === 'en' ? 'Delete' : '删除'}>🗑</button>
                          </div>
                        </div>

                        <div className="block-content">
                          {renderBlockPreview(block, language)}
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </div>

              <div className="properties-panel">
                <h3>{isEnglish ? 'Properties' : '属性'}</h3>
                <div className="properties-content">
                  <p className="help-text">{isEnglish ? 'Select a module to configure its properties' : '选择一个模块以配置其属性'}</p>
                </div>
              </div>
            </>
          )}

          {/* Split Mode: Editor on left, preview on right */}
          {viewMode === 'split' && (
            <>
              <div className="split-editor">
                <div className="canvas-header">
                  <input
                    type="text"
                    className="report-title-input"
                    placeholder={isEnglish ? "Enter report title..." : "输入报告标题..."}
                    value={reportTitle ?? (isEnglish ? "Untitled Report" : "未命名报告")}
                    onChange={(e) => onReportTitleChange(e.target.value)}
                  />
                </div>

                <div className="canvas-blocks">
                  {blocks.length === 0 ? (
                    <div className="empty-state">
                      <div className="empty-icon">📄</div>
                      <h3>{isEnglish ? 'Start Building Your Report' : '开始构建你的报告'}</h3>
                      <p>{isEnglish ? 'Click the ✏️ Edit button to add modules' : '点击 ✏️ 编辑按钮添加模块'}</p>
                    </div>
                  ) : (
                    blocks.map(block => (
                      <div key={block.id} className="canvas-block">
                        <div className="block-header">
                          <div className="block-header-left">
                            <span className="block-type-label">{getBlockTypeLabel(block.type, language)}</span>
                            {block.config?.date_range && (() => {
                              const label = resolveDateRangeLabel(block.config.date_range);
                              return label ? <span className="block-date-badge">{label}</span> : null;
                            })()}
                            {lastRefreshedAt.get(block.id) && (
                              <span className="block-refresh-time">
                                {isEnglish ? 'Refreshed' : '已刷新'}: {formatRefreshedAt(lastRefreshedAt.get(block.id)!)}
                              </span>
                            )}
                          </div>
                          <div className="block-actions">
                            <button onClick={() => onConfigureBlock(block)} title={language === 'en' ? 'Configure' : '配置'}>⚙️</button>
                            <button onClick={() => onDeleteBlock(block.id)} title={language === 'en' ? 'Delete' : '删除'}>🗑</button>
                          </div>
                        </div>

                        <div className="block-content">
                          {renderBlockPreview(block, language)}
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </div>

              <div className="split-preview">
                <ProfessionalReportPreview
                  title={reportTitle || (isEnglish ? "Untitled Report" : "未命名报告")}
                  blocks={blocks}
                  zoom={previewZoom}
                  language={language}
                  blockDataCache={blockDataCache}
                />
              </div>
            </>
          )}

          {/* Preview Mode: Full-screen preview only */}
          {viewMode === 'preview' && (
            <div className="fullscreen-preview">
              <ProfessionalReportPreview
                title={reportTitle || (isEnglish ? "Untitled Report" : "未命名报告")}
                blocks={blocks}
                zoom={previewZoom}
                language={language}
                blockDataCache={blockDataCache}
              />
            </div>
          )}
        </div>

        <style>{`
        .report-builder {
          height: 100vh;
          display: flex;
          flex-direction: column;
        }

        .builder-status-banner {
          padding: 8px 24px;
          background: #eff6ff;
          color: #1e40af;
          border-bottom: 1px solid #bfdbfe;
          font-size: 13px;
          font-weight: 500;
        }

        .builder-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding: 16px 24px;
          background: white;
          border-bottom: 1px solid #e5e7eb;
        }

        .btn-back {
          padding: 8px 16px;
          background: transparent;
          border: none;
          color: #64748b;
          cursor: pointer;
          font-size: 14px;
        }

        .header-actions {
          display: flex;
          gap: 12px;
        }

        .btn-action {
          padding: 8px 16px;
          background: white;
          border: 1px solid #e5e7eb;
          border-radius: 6px;
          font-size: 14px;
          cursor: pointer;
        }

        .btn-action:disabled {
          opacity: 0.6;
          cursor: not-allowed;
        }

        .btn-action.primary {
          background: #3b82f6;
          color: white;
          border-color: #3b82f6;
        }

        .view-mode-toggle {
          display: flex;
          gap: 4px;
          background: #f1f5f9;
          padding: 4px;
          border-radius: 8px;
        }

        .mode-btn {
          padding: 8px 16px;
          background: transparent;
          border: none;
          border-radius: 6px;
          cursor: pointer;
          font-size: 14px;
          font-weight: 500;
          color: #64748b;
          transition: all 0.2s;
          display: flex;
          align-items: center;
          gap: 6px;
        }

        .mode-btn:hover {
          background: rgba(59, 130, 246, 0.1);
          color: #3b82f6;
        }

        .mode-btn.active {
          background: white;
          color: #3b82f6;
          box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
        }

        .zoom-controls {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 6px 12px;
          background: #f1f5f9;
          border-radius: 6px;
          font-size: 14px;
        }

        .zoom-controls button {
          width: 28px;
          height: 28px;
          background: white;
          border: 1px solid #cbd5e1;
          border-radius: 4px;
          cursor: pointer;
          font-size: 16px;
          display: flex;
          align-items: center;
          justify-content: center;
          transition: all 0.2s;
        }

        .zoom-controls button:hover {
          background: #3b82f6;
          color: white;
          border-color: #3b82f6;
        }

        .zoom-controls span {
          min-width: 50px;
          text-align: center;
          font-weight: 500;
          color: #475569;
        }

        /* Edit Mode Layout */
        .builder-content[data-view-mode="edit"] {
          flex: 1;
          display: grid;
          grid-template-columns: 280px 1fr 320px;
          overflow: hidden;
        }

        /* Split Mode Layout */
        .builder-content[data-view-mode="split"] {
          flex: 1;
          display: grid;
          grid-template-columns: 1fr 1fr;
          overflow: hidden;
        }

        .split-editor,
        .split-preview {
          overflow-y: auto;
        }

        .split-editor {
          border-right: 2px solid #e5e7eb;
          background: white;
        }

        .split-preview {
          background: #e5e7eb;
        }

        /* Preview Mode Layout */
        .builder-content[data-view-mode="preview"] {
          flex: 1;
          overflow: hidden;
        }

        .fullscreen-preview {
          width: 100%;
          height: 100%;
          overflow: auto;
        }

        .blocks-panel {
          background: #f8f9fa;
          padding: 20px;
          overflow-y: auto;
          border-right: 1px solid #e5e7eb;
        }

        .blocks-panel h3 {
          font-size: 16px;
          font-weight: 600;
          margin-bottom: 16px;
          color: #1e293b;
        }

        .block-category {
          margin-bottom: 24px;
        }

        .block-category h4 {
          font-size: 13px;
          font-weight: 600;
          color: #64748b;
          margin-bottom: 12px;
          text-transform: uppercase;
          letter-spacing: 0.5px;
        }

        .erp-category {
          border-top: 2px solid #fbbf24;
          padding-top: 20px;
        }

        .erp-category h4 {
          color: #b45309;
        }

        .erp-category .block-btn {
          background: #fffbeb;
          border-color: #fde68a;
        }

        .erp-category .block-btn:hover {
          border-color: #f59e0b;
          background: #fef3c7;
        }

        .block-btn {
          width: 100%;
          padding: 12px;
          background: white;
          border: 1px solid #e5e7eb;
          border-radius: 6px;
          text-align: left;
          cursor: pointer;
          margin-bottom: 8px;
          display: flex;
          align-items: center;
          gap: 10px;
          font-size: 14px;
          transition: all 0.2s;
        }

        .block-btn:hover {
          border-color: #3b82f6;
          background: #eff6ff;
        }

        .report-copilot-panel {
          margin-top: 24px;
          padding-top: 24px;
          border-top: 1px solid #e5e7eb;
        }

        .report-copilot-panel h4 {
          font-size: 13px;
          font-weight: 600;
          color: #0f766e;
          margin-bottom: 12px;
          text-transform: uppercase;
          letter-spacing: 0.5px;
        }

        .report-copilot-panel textarea {
          width: 100%;
          min-height: 78px;
          resize: vertical;
          border: 1px solid #cbd5e1;
          border-radius: 6px;
          padding: 10px;
          font-size: 13px;
          line-height: 1.5;
          color: #0f172a;
          background: white;
          margin-bottom: 10px;
        }

        .copilot-run-btn {
          width: 100%;
          padding: 10px 12px;
          border: 1px solid #0f766e;
          border-radius: 6px;
          background: #0f766e;
          color: white;
          font-weight: 600;
          cursor: pointer;
          margin-bottom: 10px;
        }

        .copilot-run-btn:disabled {
          opacity: 0.6;
          cursor: not-allowed;
        }

        .copilot-error {
          color: #b91c1c;
          font-size: 12px;
          margin-bottom: 10px;
        }

        .copilot-plan {
          background: #ecfdf5;
          border: 1px solid #99f6e4;
          border-radius: 6px;
          padding: 12px;
        }

        .copilot-readiness {
          display: grid;
          grid-template-columns: 1fr auto;
          gap: 4px 8px;
          align-items: center;
          margin-bottom: 8px;
        }

        .copilot-readiness span,
        .copilot-data-row span {
          font-size: 11px;
          color: #475569;
          text-transform: uppercase;
          letter-spacing: 0.4px;
        }

        .copilot-readiness strong {
          color: #0f766e;
          font-size: 18px;
        }

        .copilot-readiness em {
          grid-column: 1 / -1;
          color: #115e59;
          font-size: 12px;
          font-style: normal;
        }

        .copilot-summary {
          color: #334155;
          font-size: 12px;
          line-height: 1.5;
          margin: 0 0 10px;
        }

        .copilot-template,
        .copilot-data-row,
        .copilot-blocks {
          margin-top: 10px;
        }

        .copilot-data-row {
          display: grid;
          grid-template-columns: 1fr;
          gap: 8px;
        }

        .copilot-data-row strong {
          display: block;
          color: #0f172a;
          font-size: 12px;
          font-weight: 600;
          margin-top: 2px;
          overflow-wrap: anywhere;
        }

        .copilot-actions {
          margin: 10px 0 0;
          padding-left: 18px;
          color: #334155;
          font-size: 12px;
          line-height: 1.5;
        }

        .suggestions {
          margin-top: 24px;
          padding-top: 24px;
          border-top: 1px solid #e5e7eb;
        }

        .suggestion-btn {
          width: 100%;
          padding: 12px;
          background: linear-gradient(135deg, #fef3c7 0%, #fde68a 100%);
          border: 1px solid #fbbf24;
          border-radius: 6px;
          text-align: left;
          cursor: pointer;
          margin-bottom: 8px;
        }

        .suggestion-title {
          font-weight: 500;
          color: #92400e;
          margin-bottom: 4px;
        }

        .suggestion-reason {
          font-size: 12px;
          color: #b45309;
        }

        .canvas-panel {
          background: white;
          overflow-y: auto;
        }

        .canvas-header {
          padding: 32px 48px 24px;
          border-bottom: 1px solid #e5e7eb;
        }

        .report-title-input {
          width: 100%;
          font-size: 28px;
          font-weight: 600;
          border: none;
          outline: none;
          color: #1e293b;
        }

        .canvas-blocks {
          padding: 24px 48px;
        }

        .empty-state {
          text-align: center;
          padding: 80px 20px;
        }

        .empty-icon {
          font-size: 64px;
          margin-bottom: 16px;
        }

        .empty-state h3 {
          font-size: 20px;
          color: #1e293b;
          margin-bottom: 8px;
        }

        .empty-state p {
          color: #64748b;
        }

        .canvas-block {
          background: #f8f9fa;
          border: 2px dashed #e5e7eb;
          border-radius: 8px;
          padding: 20px;
          margin-bottom: 16px;
        }

        .block-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 12px;
        }

        .block-header-left {
          display: flex;
          align-items: center;
          gap: 8px;
          flex-wrap: wrap;
        }

        .block-date-badge {
          font-size: 11px;
          font-weight: 500;
          background: #eff6ff;
          color: #2563eb;
          border: 1px solid #bfdbfe;
          border-radius: 10px;
          padding: 1px 8px;
          white-space: nowrap;
        }

        .block-refresh-time {
          font-size: 11px;
          color: #94a3b8;
          white-space: nowrap;
        }

        .block-type-label {
          font-size: 13px;
          font-weight: 500;
          color: #3b82f6;
        }

        .block-actions {
          display: flex;
          gap: 8px;
        }

        .block-actions button {
          padding: 4px 8px;
          background: white;
          border: 1px solid #e5e7eb;
          border-radius: 4px;
          cursor: pointer;
          font-size: 14px;
        }

        .properties-panel {
          background: #f8f9fa;
          padding: 20px;
          border-left: 1px solid #e5e7eb;
          overflow-y: auto;
        }

        .properties-panel h3 {
          font-size: 16px;
          font-weight: 600;
          margin-bottom: 16px;
          color: #1e293b;
        }

        .help-text {
          font-size: 14px;
          color: #64748b;
          line-height: 1.6;
        }
      `}</style>
      </div>
    );
  };

// Professional Report Preview Component
const ProfessionalReportPreview: React.FC<{
  title: string;
  blocks: ReportBlock[];
  zoom: number;
  language: string;
  blockDataCache: Map<string, any>;
}> = ({ title, blocks, zoom, language, blockDataCache }) => {
  return (
    <div className="report-preview-container">
      <div
        className="report-preview-page"
        style={{ transform: `scale(${zoom / 100})`, transformOrigin: 'top center' }}
      >
        {/* Report Header */}
        <div className="preview-header">
          <h1 className="preview-title">{title}</h1>
          <div className="preview-meta">
            <span>{new Date().toLocaleDateString(language === 'zh' ? 'zh-CN' : 'en-US', {
              year: 'numeric',
              month: 'long',
              day: 'numeric'
            })}</span>
          </div>
        </div>

        {/* Report Blocks */}
        <div className="preview-content">
          {blocks.length === 0 ? (
            <div className="preview-empty">
              <p>{language === 'en' ? 'No content yet. Add blocks to see them here.' : '暂无内容。添加模块以在此查看。'}</p>
            </div>
          ) : (
            blocks.map((block, index) => (
              <div key={block.id} className="preview-block">
                {renderProfessionalBlock(block, language, index + 1, blockDataCache.get(block.id))}
              </div>
            ))
          )}
        </div>

        {/* Report Footer */}
        <div className="preview-footer">
          <div className="footer-line"></div>
          <div className="footer-text">
            <span>{language === 'en' ? 'EDIA Intelligence Platform' : 'EDIA智能决策平台'}</span>
            <span>{language === 'en' ? 'Page' : '第'} 1 {language === 'en' ? 'of 1' : '页'}</span>
          </div>
        </div>
      </div>

      <style>{`
        .report-preview-container {
          width: 100%;
          height: 100%;
          overflow: auto;
          background: #e5e7eb;
          padding: 40px 20px;
          display: flex;
          justify-content: center;
        }

        .report-preview-page {
          width: 210mm;
          min-height: 297mm;
          background: white;
          box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
          padding: 40px 50px;
          margin: 0 auto;
          transition: transform 0.2s;
        }

        .preview-header {
          border-bottom: 4px solid #1e3a8a;
          padding-bottom: 24px;
          margin-bottom: 32px;
        }

        .preview-title {
          font-size: 32px;
          font-weight: 700;
          color: #1e293b;
          margin: 0 0 12px 0;
          line-height: 1.2;
        }

        .preview-meta {
          font-size: 14px;
          color: #64748b;
        }

        .preview-content {
          min-height: 600px;
        }

        .preview-block {
          margin-bottom: 32px;
          page-break-inside: avoid;
        }

        .preview-empty {
          text-align: center;
          padding: 100px 20px;
          color: #94a3b8;
          font-size: 16px;
        }

        .preview-footer {
          margin-top: 48px;
          padding-top: 24px;
        }

        .footer-line {
          width: 100%;
          height: 2px;
          background: linear-gradient(90deg, #1e3a8a 0%, #3b82f6 100%);
          margin-bottom: 12px;
        }

        .footer-text {
          display: flex;
          justify-content: space-between;
          align-items: center;
          font-size: 12px;
          color: #64748b;
        }
      `}</style>
    </div>
  );
};

// Helper Components
const DataSourceConfigPanel: React.FC<{
  block: ReportBlock;
  dataSources: DataSource[];
  onClose: () => void;
  onSave: (block: ReportBlock) => void;
}> = ({ block, dataSources, onClose, onSave }) => {
  const [selectedSource, setSelectedSource] = useState<string>('');
  const [selectedColumns, setSelectedColumns] = useState<string[]>([]);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-panel" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>配置数据源</h2>
          <button onClick={onClose}>✕</button>
        </div>

        <div className="modal-content">
          <div className="form-group">
            <label>选择数据源</label>
            <select
              value={selectedSource}
              onChange={(e) => setSelectedSource(e.target.value)}
              className="form-select"
            >
              <option value="">请选择...</option>
              {dataSources.map(ds => (
                <option key={ds.id} value={ds.id}>
                  {ds.name} ({ds.name_en})
                </option>
              ))}
            </select>
          </div>

          {selectedSource && (
            <div className="form-group">
              <label>选择字段</label>
              <div className="checkbox-group">
                {Object.keys(dataSources.find(ds => ds.id === selectedSource)?.schema || {}).map(field => (
                  <label key={field} className="checkbox-label">
                    <input
                      type="checkbox"
                      checked={selectedColumns.includes(field)}
                      onChange={(e) => {
                        if (e.target.checked) {
                          setSelectedColumns([...selectedColumns, field]);
                        } else {
                          setSelectedColumns(selectedColumns.filter(f => f !== field));
                        }
                      }}
                    />
                    {field}
                  </label>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="modal-footer">
          <button className="btn-secondary" onClick={onClose}>取消</button>
          <button
            className="btn-primary"
            onClick={() => {
              onSave({
                ...block,
                config: {
                  ...block.config,
                  data_source: selectedSource,
                  columns: selectedColumns
                }
              });
            }}
          >
            保存
          </button>
        </div>

        <style>{`
          .modal-overlay {
            position: fixed;
            inset: 0;
            background: rgba(0, 0, 0, 0.5);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 1000;
          }

          .modal-panel {
            background: white;
            border-radius: 12px;
            width: 600px;
            max-height: 80vh;
            display: flex;
            flex-direction: column;
          }

          .modal-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 24px;
            border-bottom: 1px solid #e5e7eb;
          }

          .modal-header h2 {
            font-size: 20px;
            font-weight: 600;
          }

          .modal-header button {
            background: none;
            border: none;
            font-size: 24px;
            cursor: pointer;
            color: #64748b;
          }

          .modal-content {
            flex: 1;
            padding: 24px;
            overflow-y: auto;
          }

          .form-group {
            margin-bottom: 24px;
          }

          .form-group label {
            display: block;
            font-weight: 500;
            margin-bottom: 8px;
            color: #1e293b;
          }

          .form-select {
            width: 100%;
            padding: 10px;
            border: 1px solid #e5e7eb;
            border-radius: 6px;
            font-size: 14px;
          }

          .checkbox-group {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 12px;
          }

          .checkbox-label {
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 14px;
            cursor: pointer;
          }

          .modal-footer {
            padding: 24px;
            border-top: 1px solid #e5e7eb;
            display: flex;
            justify-content: flex-end;
            gap: 12px;
          }

          .btn-secondary {
            padding: 10px 20px;
            background: white;
            border: 1px solid #e5e7eb;
            border-radius: 6px;
            cursor: pointer;
          }

          .btn-primary {
            padding: 10px 20px;
            background: #3b82f6;
            color: white;
            border: none;
            border-radius: 6px;
            cursor: pointer;
          }
        `}</style>
      </div>
    </div>
  );
};

const DataPreviewModal: React.FC<{
  data: any;
  onClose: () => void;
}> = ({ data, onClose }) => {
  const { language } = useLanguage();
  const isEnglish = language === 'en';

  const renderPreviewContent = () => {
    if (!data) return null;

    if (data.type === 'kpi_cards') {
      return (
        <div>
          <h3 style={{ marginBottom: '20px', color: '#1e293b' }}>{data.title}</h3>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '16px' }}>
            {data.data.map((kpi: any, index: number) => (
              <div key={index} style={{
                padding: '20px',
                background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
                color: 'white',
                borderRadius: '12px',
                boxShadow: '0 4px 12px rgba(0, 0, 0, 0.1)'
              }}>
                <div style={{ fontSize: '13px', opacity: 0.9, marginBottom: '8px' }}>{kpi.label}</div>
                <div style={{ fontSize: '28px', fontWeight: '700', marginBottom: '8px' }}>{kpi.value}</div>
                <div style={{
                  fontSize: '14px',
                  color: kpi.trend === 'up' ? '#4ade80' : '#f87171',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '4px'
                }}>
                  <span>{kpi.trend === 'up' ? '↑' : '↓'}</span>
                  <span>{kpi.change}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      );
    }

    if (data.type === 'data_table') {
      return (
        <div>
          <h3 style={{ marginBottom: '20px', color: '#1e293b' }}>{data.title}</h3>
          <div style={{ overflow: 'auto', maxHeight: '400px' }}>
            <table style={{
              width: '100%',
              borderCollapse: 'collapse',
              fontSize: '14px'
            }}>
              <thead>
                <tr style={{ background: '#f8fafc', borderBottom: '2px solid #e2e8f0' }}>
                  {data.columns.map((col: any) => (
                    <th key={col.key} style={{
                      padding: '12px 16px',
                      textAlign: col.key.includes('price') || col.key.includes('change') || col.key.includes('value') ? 'right' : 'left',
                      fontWeight: '600',
                      color: '#475569'
                    }}>
                      {col.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row: any, rowIndex: number) => {
                  const isSummaryRow = row.product?.includes('汇总') || row.market === '全市场';
                  return (
                    <tr key={rowIndex} style={{
                      borderBottom: '1px solid #e2e8f0',
                      background: isSummaryRow ? '#fef3c7' : (rowIndex % 2 === 0 ? 'white' : '#f8fafc'),
                      fontWeight: isSummaryRow ? '600' : 'normal'
                    }}>
                      {data.columns.map((col: any) => {
                        const cellValue = row[col.key];
                        const isChangeCol = col.key === 'change' || col.key === 'change_pct' || col.key === 'change_value';
                        const isPriceCol = col.key.includes('price');
                        const isPositive = typeof cellValue === 'string' && cellValue.startsWith('+');
                        const isNegative = typeof cellValue === 'string' && cellValue.startsWith('-');

                        return (
                          <td key={col.key} style={{
                            padding: '12px 16px',
                            color: isChangeCol ? (isPositive ? '#22c55e' : isNegative ? '#ef4444' : '#334155') : '#334155',
                            textAlign: isPriceCol || isChangeCol ? 'right' : 'left',
                            fontWeight: isChangeCol ? '500' : 'normal'
                          }}>
                            {cellValue}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      );
    }

    if (data.type === 'custom_sql') {
      if (data.error) {
        return (
          <div>
            <h3 style={{ marginBottom: '20px', color: '#1e293b' }}>{data.title}</h3>
            <div style={{
              padding: '12px',
              background: '#fee2e2',
              border: '1px solid #fca5a5',
              borderRadius: '6px',
              color: '#991b1b'
            }}>
              <strong>{isEnglish ? 'Query Error:' : '查询错误：'}</strong> {data.error}
            </div>
          </div>
        );
      }

      if (data.rows && Array.isArray(data.rows) && data.columns && Array.isArray(data.columns)) {
        return (
          <div>
            <h3 style={{ marginBottom: '20px', color: '#1e293b' }}>{data.title}</h3>
            <div style={{ overflow: 'auto', maxHeight: '400px' }}>
              <table style={{
                width: '100%',
                borderCollapse: 'collapse',
                fontSize: '14px'
              }}>
                <thead>
                  <tr style={{ background: '#f8fafc', borderBottom: '2px solid #e2e8f0' }}>
                    {data.columns.map((col: any, idx: number) => {
                      const colKey = typeof col === 'string' ? col : col.key;
                      const colLabel = typeof col === 'string' ? col : (col.label || col.key || `col_${idx}`);
                      const alignRight = colKey && (colKey.toLowerCase().includes('price') || colKey.toLowerCase().includes('value') || colKey.toLowerCase().includes('rate'));

                      return (
                        <th key={colKey || idx} style={{
                          padding: '12px 16px',
                          textAlign: alignRight ? 'right' : 'left',
                          fontWeight: '600',
                          color: '#475569'
                        }}>
                          {colLabel}
                        </th>
                      );
                    })}
                  </tr>
                </thead>
                <tbody>
                  {data.rows.map((row: any, rowIndex: number) => (
                    <tr key={rowIndex} style={{
                      borderBottom: '1px solid #e2e8f0',
                      background: rowIndex % 2 === 0 ? 'white' : '#f8fafc'
                    }}>
                      {data.columns.map((col: any, cellIdx: number) => {
                        const colKey = typeof col === 'string' ? col : col.key;
                        const rawValue = colKey ? row[colKey] : '';
                        const cellValue = rawValue === null || rawValue === undefined || rawValue === '' ? '/' : rawValue;
                        const alignRight = colKey && (colKey.toLowerCase().includes('price') || colKey.toLowerCase().includes('value') || colKey.toLowerCase().includes('rate'));

                        return (
                          <td key={`${rowIndex}-${cellIdx}`} style={{
                            padding: '12px 16px',
                            color: '#334155',
                            textAlign: alignRight ? 'right' : 'left'
                          }}>
                            {String(cellValue)}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        );
      }

      return (
        <div style={{
          padding: '16px',
          background: '#eff6ff',
          border: '2px dashed #3b82f6',
          borderRadius: '8px',
          color: '#1e40af',
          textAlign: 'center'
        }}>
          {data.message || (isEnglish ? 'No preview data yet' : '暂无预览数据')}
        </div>
      );
    }

    if (data.type === 'chart') {
      return (
        <div>
          <h3 style={{ marginBottom: '20px', color: '#1e293b' }}>{data.title}</h3>
          <div style={{
            padding: '20px',
            background: '#f8fafc',
            borderRadius: '8px',
            border: '1px solid #e2e8f0'
          }}>
            <div style={{ fontSize: '14px', color: '#64748b', marginBottom: '16px' }}>
              {isEnglish ? `Chart Type: ${data.chart_type}` : `图表类型: ${data.chart_type}`}
            </div>
            {data.series.map((series: any, index: number) => (
              <div key={index} style={{ marginBottom: '20px' }}>
                <div style={{
                  fontSize: '14px',
                  fontWeight: '600',
                  color: '#1e293b',
                  marginBottom: '12px',
                  padding: '8px 12px',
                  background: 'white',
                  borderRadius: '6px',
                  borderLeft: '4px solid #3b82f6'
                }}>
                  {series.name}
                </div>
                <div style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))',
                  gap: '12px',
                  padding: '12px',
                  background: 'white',
                  borderRadius: '6px'
                }}>
                  {series.data.map((point: any, pointIndex: number) => (
                    <div key={pointIndex} style={{
                      padding: '8px',
                      background: '#f1f5f9',
                      borderRadius: '4px',
                      textAlign: 'center'
                    }}>
                      <div style={{ fontSize: '11px', color: '#64748b', marginBottom: '4px' }}>
                        {point.date}
                      </div>
                      <div style={{ fontSize: '16px', fontWeight: '600', color: '#1e293b' }}>
                        ¥{point.value.toFixed(0)}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      );
    }

    // Default JSON view
    return <pre style={{ fontSize: '13px', lineHeight: '1.6' }}>{JSON.stringify(data, null, 2)}</pre>;
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-panel preview-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>{isEnglish ? 'Data Preview' : '数据预览'}</h2>
          <button onClick={onClose}>✕</button>
        </div>

        <div className="modal-content">
          {renderPreviewContent()}
        </div>

        <div className="modal-footer">
          <div style={{ fontSize: '12px', color: '#64748b', fontStyle: 'italic' }}>
            {isEnglish ? '📊 Sample data for preview. Actual export will use real data.' : '📊 示例数据预览。实际导出将使用真实数据。'}
          </div>
        </div>

        <style>{`
          .modal-overlay {
            position: fixed;
            inset: 0;
            background: rgba(0, 0, 0, 0.5);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 1000;
          }

          .modal-panel {
            background: white;
            border-radius: 12px;
            box-shadow: 0 20px 50px rgba(0, 0, 0, 0.3);
            max-height: 80vh;
            display: flex;
            flex-direction: column;
          }

          .preview-modal {
            width: 900px;
            max-width: 90vw;
          }

          .modal-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 20px 24px;
            border-bottom: 1px solid #e5e7eb;
          }

          .modal-header h2 {
            margin: 0;
            font-size: 20px;
            font-weight: 600;
            color: #1e293b;
          }

          .modal-header button {
            background: none;
            border: none;
            font-size: 24px;
            color: #94a3b8;
            cursor: pointer;
            padding: 0;
            width: 32px;
            height: 32px;
            display: flex;
            align-items: center;
            justify-content: center;
            border-radius: 4px;
            transition: all 0.2s;
          }

          .modal-header button:hover {
            background: #f1f5f9;
            color: #475569;
          }

          .modal-content {
            flex: 1;
            overflow: auto;
            padding: 24px;
          }

          .modal-footer {
            padding: 16px 24px;
            border-top: 1px solid #e5e7eb;
            background: #f8fafc;
            border-radius: 0 0 12px 12px;
          }

          pre {
            background: #f8f9fa;
            padding: 16px;
            border-radius: 6px;
            overflow: auto;
            font-size: 13px;
          }
        `}</style>
      </div>
    </div>
  );
};

// Helper functions
function generateSampleData(block: ReportBlock, language: string = 'zh'): any {
  const isEnglish = language === 'en';
  const config = block.config;

  // Real EDIA product names
  const productNames: Record<string, { en: string; zh: string }> = {
    crude_oil: { en: 'Brent Crude Oil', zh: '布伦特原油' },
    naphtha: { en: 'Japanese Naphtha', zh: '日本石脑油' },
    cracked_c5: { en: 'Cracked C5', zh: '工业用裂解碳五' },
    cracked_c9: { en: 'Cracked C9', zh: '工业用裂解碳九' },
    isoprene: { en: 'Isoprene', zh: '异戊二烯' },
    piperylene: { en: 'Piperylene', zh: '间戊二烯' },
    dcpd: { en: 'DCPD', zh: '双环戊二烯' },
    raffinate_c5: { en: 'Raffinate C5', zh: '抽余碳五' },
    sis: { en: 'SIS', zh: 'SIS' },
    c5_resin: { en: 'C5 Resin', zh: '碳五石油树脂' },
    blowing_agent: { en: 'Blowing Agent', zh: '戊烷发泡剂' },
    styrene: { en: 'Styrene', zh: '苯乙烯' }
  };

  if (block.type === 'kpi_cards' && config.metrics) {
    // Generate KPI card data
    const kpis = config.metrics.map((metric: string) => {
      const basePrice = Math.random() * 5000 + 3000;
      const change = (Math.random() - 0.5) * 10;

      return {
        metric: metric,
        label: metric === 'price' ? (isEnglish ? 'Current Price' : '当前价格') :
          metric === 'price_change' ? (isEnglish ? 'Price Change' : '价格变动') :
            metric === 'volume' ? (isEnglish ? 'Trading Volume' : '交易量') :
              (isEnglish ? 'Profit Margin' : '利润率'),
        value: metric === 'price' ? `¥${basePrice.toFixed(2)}` :
          metric === 'price_change' ? `${change >= 0 ? '+' : ''}${change.toFixed(2)}%` :
            metric === 'volume' ? `${(Math.random() * 10000).toFixed(0)} tons` :
              `${(Math.random() * 15 + 5).toFixed(1)}%`,
        trend: change >= 0 ? 'up' : 'down',
        change: `${change >= 0 ? '+' : ''}${change.toFixed(2)}%`
      };
    });

    return {
      type: 'kpi_cards',
      title: isEnglish ? 'Key Performance Indicators' : '关键绩效指标',
      data: kpis
    };
  }

  if (block.type === 'data_table' && config.products) {
    // Generate table data for products
    const today = new Date();
    const rows = config.products.map((productId: string, index: number) => {
      const date = new Date(today);
      date.setDate(date.getDate() - index);
      const basePrice = Math.random() * 5000 + 3000;
      const change = (Math.random() - 0.5) * 10;

      return {
        date: date.toISOString().split('T')[0],
        product: productNames[productId] ? (isEnglish ? productNames[productId].en : productNames[productId].zh) : productId,
        price: `¥${basePrice.toFixed(2)}`,
        change: `${change >= 0 ? '+' : ''}${change.toFixed(2)}%`,
        volume: `${(Math.random() * 1000 + 500).toFixed(0)}`,
        margin: `${(Math.random() * 15 + 5).toFixed(2)}%`
      };
    });

    return {
      type: 'data_table',
      title: isEnglish ? 'Market Price Data' : '市场价格数据',
      columns: [
        { key: 'date', label: isEnglish ? 'Date' : '日期' },
        { key: 'product', label: isEnglish ? 'Product' : '产品' },
        { key: 'price', label: isEnglish ? 'Price (¥/ton)' : '价格 (¥/吨)' },
        { key: 'change', label: isEnglish ? 'Change' : '涨跌' },
        { key: 'volume', label: isEnglish ? 'Volume (tons)' : '成交量 (吨)' },
        { key: 'margin', label: isEnglish ? 'Margin' : '利润率' }
      ],
      rows: rows
    };
  }

  if (block.type === 'chart' && config.products && config.products.length > 0) {
    // Generate chart data
    const days = 7;
    const today = new Date();
    const seriesData = config.products.map((productId: string) => {
      const data = [];
      let basePrice = Math.random() * 5000 + 3000;

      for (let i = days - 1; i >= 0; i--) {
        const date = new Date(today);
        date.setDate(date.getDate() - i);
        basePrice += (Math.random() - 0.5) * 200;
        data.push({
          date: date.toISOString().split('T')[0],
          value: basePrice
        });
      }

      return {
        name: productNames[productId] ? (isEnglish ? productNames[productId].en : productNames[productId].zh) : productId,
        data: data
      };
    });

    return {
      type: 'chart',
      chart_type: config.chart_type || 'bar',
      title: isEnglish ? `${config.chart_type} Chart - Price Trend` : `${config.chart_type === 'line' ? '折线图' : config.chart_type === 'bar' ? '柱状图' : config.chart_type === 'area' ? '面积图' : '散点图'} - 价格走势`,
      series: seriesData
    };
  }

  if (block.type === 'custom_sql') {
    // For custom SQL, show a message that data needs to be previewed
    return {
      type: 'custom_sql',
      title: config.title || (isEnglish ? 'Custom Query' : '自定义查询'),
      message: isEnglish ? 'Click 👁 Preview Data to execute your query and see results' : '点击 👁 预览数据以执行查询并查看结果',
      config: config
    };
  }

  // Default fallback
  return {
    type: block.type,
    message: isEnglish ? 'Sample data preview' : '示例数据预览',
    config: config
  };
}

function getBlockTypeLabel(type: string, language: string = 'zh'): string {
  const isEnglish = language === 'en';
  const labels: Record<string, { en: string; zh: string }> = {
    kpi_cards: { en: 'Key Indicators', zh: '关键指标' },
    data_table: { en: 'Data Table', zh: '数据表格' },
    chart: { en: 'Chart', zh: '图表' },
    chart_row: { en: 'Chart Row', zh: '图表组' },
    text: { en: 'Text', zh: '文字说明' },
    image: { en: 'Image', zh: '图片' },
    custom_sql: { en: 'Custom Query', zh: '自定义查询' },
    erp_sales_summary: { en: 'Sales Summary', zh: '销售汇总' },
    erp_customer_breakdown: { en: 'Customer Breakdown', zh: '客户明细' },
    erp_vs_market: { en: 'Actual vs Market Price', zh: '成交价 vs 市场价' },
    erp_delivery_status: { en: 'Delivery Status', zh: '交货状态' },
    erp_monthly_trend: { en: 'Monthly Sales Trend', zh: '月度销售走势' },
  };
  return labels[type] ? (isEnglish ? labels[type].en : labels[type].zh) : type;
}

function formatKpiDisplayValue(value: any): string {
  if (value == null || value === '') {
    return '-';
  }
  if (typeof value === 'number') {
    if (Number.isInteger(value)) {
      return value.toLocaleString('en-US');
    }
    return value.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 2 });
  }

  if (typeof value === 'string') {
    const trimmed = value.trim();
    if (/^-?\d+(\.\d+)?$/.test(trimmed)) {
      const numericValue = Number(trimmed);
      return formatKpiDisplayValue(numericValue);
    }
    return trimmed;
  }

  return String(value);
}

function parseKpiChangeValue(kpi: any): number | null {
  if (typeof kpi?.change_value === 'number' && Number.isFinite(kpi.change_value)) {
    return kpi.change_value;
  }
  if (typeof kpi?.delta_pct === 'number' && Number.isFinite(kpi.delta_pct)) {
    return kpi.delta_pct;
  }
  if (typeof kpi?.change === 'string') {
    const numericValue = Number(kpi.change.replace('%', '').trim());
    if (Number.isFinite(numericValue)) {
      return numericValue;
    }
  }
  return null;
}

function getKpiCardDisplay(kpi: any, isEnglish: boolean) {
  const changeValue = parseKpiChangeValue(kpi);
  const trend = kpi?.trend === 'up' || kpi?.trend === 'down' || kpi?.trend === 'neutral'
    ? kpi.trend
    : (changeValue == null || changeValue === 0 ? 'neutral' : changeValue > 0 ? 'up' : 'down');
  const primaryLabel = kpi?.label || kpi?.label_en || '-';
  const secondaryLabel = kpi?.label_en && kpi.label_en !== primaryLabel ? kpi.label_en : '';
  const unit = isEnglish
    ? (kpi?.unit_en || kpi?.unit_zh || kpi?.unit || '')
    : (kpi?.unit_zh || kpi?.unit || kpi?.unit_en || '');
  const valueDisplay = kpi?.value_display || formatKpiDisplayValue(kpi?.value);
  const comparison = isEnglish
    ? (kpi?.comparison_label_en || kpi?.comparison_label || 'vs period start')
    : (kpi?.comparison_label || 'vs period start');
  let changeText = isEnglish ? 'Flat' : '持平';
  if (trend !== 'neutral') {
    if (changeValue != null) {
      changeText = `${Math.abs(changeValue).toFixed(1)}%`;
    } else if (typeof kpi?.change === 'string' && kpi.change.trim()) {
      changeText = kpi.change.replace(/^[+-]/, '').trim();
    }
  }

  const rangeItems: Array<{ label: string; value: string }> = [];
  const highValue = kpi?.high_display || formatKpiDisplayValue(kpi?.high);
  const lowValue = kpi?.low_display || formatKpiDisplayValue(kpi?.low);
  if (kpi?.high != null || kpi?.high_display) {
    rangeItems.push({ label: '区间高', value: highValue });
  }
  if (kpi?.low != null || kpi?.low_display) {
    rangeItems.push({ label: '区间低', value: lowValue });
  }

  return {
    arrow: trend === 'up' ? '▲' : trend === 'down' ? '▼' : '—',
    changeText,
    comparison,
    primaryLabel,
    rangeItems,
    secondaryLabel,
    trend,
    unit,
    valueDisplay,
  };
}

function renderBlockPreview(block: ReportBlock, language: string = 'zh'): React.ReactNode {
  const isEnglish = language === 'en';
  const config = block.config || {};

  // ERP block preview
  if (block.type.startsWith('erp_')) {
    const productId = (config as any).product_id;
    const periodDays = (config as any).period_days || 90;
    const erp_product_names: Record<string, { en: string; zh: string }> = {
      dcpd: { en: 'DCPD', zh: '双环戊二烯' },
      cracked_c5: { en: 'Cracked C5', zh: '工业用裂解碳五' },
      cracked_c9: { en: 'Cracked C9', zh: '工业用裂解碳九' },
      isoprene: { en: 'Isoprene', zh: '异戊二烯' },
      piperylene: { en: 'Piperylene', zh: '间戊二烯' },
      raffinate_c5: { en: 'Raffinate C5', zh: '抽余碳五' },
      sis: { en: 'SIS', zh: 'SIS' },
      c5_resin: { en: 'C5 Petroleum Resin', zh: '碳五石油树脂' },
      blowing_agent: { en: 'Blowing Agent', zh: '戊烷发泡剂' },
      hex: { en: 'Industrial Hexane', zh: '工业己烷' },
      trimethylbenzene: { en: 'Trimethylbenzene', zh: '混三甲苯' },
      eth_carbon: { en: 'Ethylene Carbon Black', zh: '乙烯炭黑料' },
    };
    if (!productId) {
      return (
        <div style={{ padding: '20px', textAlign: 'center', color: '#94a3b8' }}>
          <p>🔒 {isEnglish ? 'Click ⚙️ to select a product for this ERP block' : '点击 ⚙️ 选择ERP数据产品'}</p>
        </div>
      );
    }
    const productLabel = erp_product_names[productId]
      ? (isEnglish ? erp_product_names[productId].en : erp_product_names[productId].zh)
      : productId;
    return (
      <div style={{ padding: '14px', background: '#fffbeb', borderRadius: '6px', border: '1px solid #fde68a' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
          <span style={{ fontSize: '18px' }}>🔒</span>
          <span style={{ fontWeight: '600', color: '#92400e', fontSize: '14px' }}>
            {isEnglish ? 'ERP / Sales Data Block' : 'ERP / 销售数据模块'}
          </span>
        </div>
        <div style={{ fontSize: '13px', color: '#78350f' }}>
          <span style={{ marginRight: '16px' }}>📦 {productLabel}</span>
          <span>📅 {isEnglish ? `Last ${periodDays} days` : `近${periodDays}天`}</span>
        </div>
        <div style={{ fontSize: '11px', color: '#b45309', marginTop: '8px', fontStyle: 'italic' }}>
          {isEnglish ? 'Click 👁 to preview data' : '点击 👁 预览数据'}
        </div>
      </div>
    );
  }

  // If block is not configured, show placeholder
  if (!config.products && !config.metrics && !config.data_source) {
    return (
      <div className="block-preview-placeholder" style={{ padding: '20px', textAlign: 'center', color: '#94a3b8' }}>
        <p>{isEnglish ? `Click ⚙️ to configure this ${getBlockTypeLabel(block.type, language)}` : `点击 ⚙️ 配置此${getBlockTypeLabel(block.type, language)}`}</p>
      </div>
    );
  }

  // Product names mapping (Real EDIA products)
  const productNames: Record<string, { en: string; zh: string }> = {
    crude_oil: { en: 'Brent Crude Oil', zh: '布伦特原油' },
    naphtha: { en: 'Japanese Naphtha', zh: '日本石脑油' },
    cracked_c5: { en: 'Cracked C5', zh: '工业用裂解碳五' },
    cracked_c9: { en: 'Cracked C9', zh: '工业用裂解碳九' },
    isoprene: { en: 'Isoprene', zh: '异戊二烯' },
    piperylene: { en: 'Piperylene', zh: '间戊二烯' },
    dcpd: { en: 'DCPD', zh: '双环戊二烯' },
    raffinate_c5: { en: 'Raffinate C5', zh: '抽余碳五' },
    sis: { en: 'SIS', zh: 'SIS' },
    c5_resin: { en: 'C5 Resin', zh: '碳五石油树脂' },
    blowing_agent: { en: 'Blowing Agent', zh: '戊烷发泡剂' },
    styrene: { en: 'Styrene', zh: '苯乙烯' }
  };

  const metricNames: Record<string, { en: string; zh: string }> = {
    price: { en: 'Current Price', zh: '当前价格' },
    price_change: { en: 'Price Change', zh: '价格变动' },
    volume: { en: 'Trading Volume', zh: '交易量' },
    margin: { en: 'Profit Margin', zh: '利润率' }
  };

  const chartTypeNames: Record<string, { en: string; zh: string }> = {
    line: { en: 'Line Chart', zh: '折线图' },
    bar: { en: 'Bar Chart', zh: '柱状图' },
    area: { en: 'Area Chart', zh: '面积图' },
    scatter: { en: 'Scatter Plot', zh: '散点图' }
  };

  return (
    <div className="block-preview-configured" style={{ padding: '16px', background: '#f8fafc', borderRadius: '6px' }}>
      {/* Show selected products */}
      {config.products && config.products.length > 0 && (
        <div style={{ marginBottom: '12px' }}>
          <div style={{ fontSize: '12px', fontWeight: '600', color: '#64748b', marginBottom: '6px' }}>
            {isEnglish ? 'Products:' : '产品：'}
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
            {config.products.map((productId: string) => (
              <span
                key={productId}
                style={{
                  padding: '4px 10px',
                  background: '#3b82f6',
                  color: 'white',
                  borderRadius: '4px',
                  fontSize: '13px',
                  fontWeight: '500'
                }}
              >
                {productNames[productId] ? (isEnglish ? productNames[productId].en : productNames[productId].zh) : productId}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Show SQL query for custom_sql blocks */}
      {block.type === 'custom_sql' && config.sql_query && (
        <div style={{ marginBottom: '12px' }}>
          <div style={{ fontSize: '12px', fontWeight: '600', color: '#64748b', marginBottom: '6px' }}>
            {isEnglish ? 'SQL Query:' : 'SQL 查询：'}
          </div>
          <pre style={{
            padding: '8px',
            background: '#f1f5f9',
            borderRadius: '4px',
            fontSize: '11px',
            fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
            overflowX: 'auto',
            maxHeight: '80px',
            color: '#475569'
          }}>
            {config.sql_query}
          </pre>
        </div>
      )}

      {/* Show date range */}
      {config.date_range && (
        <div style={{ marginBottom: '12px' }}>
          <div style={{ fontSize: '12px', fontWeight: '600', color: '#64748b', marginBottom: '6px' }}>
            {isEnglish ? 'Date Range:' : '日期范围：'}
          </div>
          <div style={{ fontSize: '13px', color: '#475569' }}>
            {config.date_range.type === 'single' && config.date_range.value && config.date_range.value}
            {config.date_range.type === 'range' && config.date_range.start && config.date_range.end &&
              `${config.date_range.start} ~ ${config.date_range.end}`}
            {config.date_range.type === 'relative' && config.date_range.relative && (
              <>
                {config.date_range.relative.value === 0 && config.date_range.relative.unit === 'days' && (isEnglish ? 'Today' : '今天')}
                {config.date_range.relative.value === 7 && config.date_range.relative.unit === 'days' && (isEnglish ? 'Last 7 Days' : '过去7天')}
                {config.date_range.relative.value === 30 && config.date_range.relative.unit === 'days' && (isEnglish ? 'Last 30 Days' : '过去30天')}
                {config.date_range.relative.value === 3 && config.date_range.relative.unit === 'months' && (isEnglish ? 'Last 3 Months' : '过去3个月')}
                {!(
                  (config.date_range.relative.value === 0 && config.date_range.relative.unit === 'days') ||
                  (config.date_range.relative.value === 7 && config.date_range.relative.unit === 'days') ||
                  (config.date_range.relative.value === 30 && config.date_range.relative.unit === 'days') ||
                  (config.date_range.relative.value === 3 && config.date_range.relative.unit === 'months')
                ) && `${isEnglish ? 'Last' : '过去'} ${config.date_range.relative.value} ${config.date_range.relative.unit}`}
              </>
            )}
          </div>
        </div>
      )}

      {/* Show metrics for KPI cards */}
      {block.type === 'kpi_cards' && config.metrics && config.metrics.length > 0 && (
        <div style={{ marginBottom: '12px' }}>
          <div style={{ fontSize: '12px', fontWeight: '600', color: '#64748b', marginBottom: '6px' }}>
            {isEnglish ? 'Metrics:' : '指标：'}
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
            {config.metrics.map((metric: string) => (
              <span
                key={metric}
                style={{
                  padding: '4px 10px',
                  background: '#10b981',
                  color: 'white',
                  borderRadius: '4px',
                  fontSize: '13px',
                  fontWeight: '500'
                }}
              >
                {metricNames[metric] ? (isEnglish ? metricNames[metric].en : metricNames[metric].zh) : metric}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Show chart type */}
      {block.type === 'chart' && config.chart_type && (
        <div style={{ marginBottom: '12px' }}>
          <div style={{ fontSize: '12px', fontWeight: '600', color: '#64748b', marginBottom: '6px' }}>
            {isEnglish ? 'Chart Type:' : '图表类型：'}
          </div>
          <div style={{ fontSize: '13px', color: '#475569' }}>
            {chartTypeNames[config.chart_type] ? (isEnglish ? chartTypeNames[config.chart_type].en : chartTypeNames[config.chart_type].zh) : config.chart_type}
          </div>
        </div>
      )}

      {/* Configuration summary */}
      <div style={{ fontSize: '11px', color: '#94a3b8', marginTop: '12px', fontStyle: 'italic' }}>
        {isEnglish ? 'Click 👁 to preview data' : '点击 👁 预览数据'}
      </div>
    </div>
  );
}

// Render block in professional export format (for preview)
function renderProfessionalBlock(block: ReportBlock, language: string, blockNumber: number, cachedData?: any): React.ReactNode {
  const isEnglish = language === 'en';
  // Use cached data if available, otherwise generate sample data
  const sampleData = cachedData || generateSampleData(block, language);

  // Early return if no sample data
  if (!sampleData) {
    return (
      <div className="professional-block">
        <h2 className="block-title">
          {blockNumber}. {getBlockTypeLabel(block.type, language)}
        </h2>
        <div className="no-data-message">
          {isEnglish ? 'No data configured' : '未配置数据'}
        </div>
      </div>
    );
  }

  return (
    <div className="professional-block">
      <h2 className="block-title">
        {blockNumber}. {getBlockTypeLabel(block.type, language)}
      </h2>

      {/* Render KPI Cards - data is in sampleData.data */}
      {block.type === 'kpi_cards' && sampleData.data && Array.isArray(sampleData.data) && (
        <>
          {sampleData.period_label && (
            <div className="kpi-period-label">
              {isEnglish ? 'Period' : '统计区间'}: {sampleData.period_label}
            </div>
          )}
          <div className="kpi-cards-grid">
            {sampleData.data.map((kpi: any, idx: number) => {
              const cardDisplay = getKpiCardDisplay(kpi, isEnglish);
              return (
                <div key={idx} className={`kpi-card ${cardDisplay.trend}`}>
                  <div className="kpi-card-head">
                    <div className="kpi-label">{cardDisplay.primaryLabel}</div>
                    {cardDisplay.secondaryLabel && (
                      <div className="kpi-label-secondary">{cardDisplay.secondaryLabel}</div>
                    )}
                  </div>
                  <div className="kpi-value-row">
                    <div className="kpi-value">{cardDisplay.valueDisplay}</div>
                    {cardDisplay.unit && <div className="kpi-unit">{cardDisplay.unit}</div>}
                  </div>
                  <div className="kpi-change-row">
                    <div className={`kpi-change ${cardDisplay.trend}`}>
                      <span className="kpi-change-arrow">{cardDisplay.arrow}</span>
                      <span>{cardDisplay.changeText}</span>
                    </div>
                    <div className="kpi-comparison">{cardDisplay.comparison}</div>
                  </div>
                  {cardDisplay.rangeItems.length > 0 && (
                    <div className="kpi-high-low">
                      {cardDisplay.rangeItems.map((rangeItem) => (
                        <span key={`${rangeItem.label}-${rangeItem.value}`} className="kpi-range-item">
                          <span className="kpi-range-label">{rangeItem.label}</span>
                          <span>{rangeItem.value}</span>
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}

      {/* Render Data Table - columns and rows are in sampleData */}
      {block.type === 'data_table' && sampleData.rows && Array.isArray(sampleData.rows) && (
        <div className="data-table-container">
          <table className="data-table">
            <thead>
              <tr>
                {sampleData.columns.map((col: any, idx: number) => (
                  <th key={idx}>{col.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sampleData.rows.map((row: any, rowIdx: number) => (
                <tr key={rowIdx}>
                  {sampleData.columns.map((col: any, cellIdx: number) => (
                    <td key={cellIdx}>{row[col.key]}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Render Custom SQL Query Results */}
      {block.type === 'custom_sql' && !sampleData.error && sampleData.rows && Array.isArray(sampleData.rows) && (
        <div className="data-table-container">
          <table className="data-table">
            <thead>
              <tr>
                {sampleData.columns.map((col: any, idx: number) => (
                  <th key={idx}>{col.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sampleData.rows.map((row: any, rowIdx: number) => (
                <tr key={rowIdx}>
                  {sampleData.columns.map((col: any, cellIdx: number) => (
                    <td key={cellIdx}>{row[col.key]}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Show error message for custom SQL */}
      {block.type === 'custom_sql' && sampleData.error && (
        <div style={{
          padding: '12px',
          background: '#fee2e2',
          border: '1px solid #fca5a5',
          borderRadius: '6px',
          color: '#991b1b'
        }}>
          <strong>{isEnglish ? 'Query Error:' : '查询错误：'}</strong> {sampleData.error}
        </div>
      )}

      {/* Show message for custom SQL when no data yet */}
      {block.type === 'custom_sql' && !sampleData.error && !sampleData.rows && sampleData.message && (
        <div style={{
          padding: '16px',
          background: '#eff6ff',
          border: '2px dashed #3b82f6',
          borderRadius: '8px',
          color: '#1e40af',
          textAlign: 'center'
        }}>
          <div style={{ fontSize: '24px', marginBottom: '8px' }}>👁</div>
          <div>{sampleData.message}</div>
        </div>
      )}

      {/* Render Chart - series data is in sampleData.series */}
      {block.type === 'chart' && sampleData.series && Array.isArray(sampleData.series) && (() => {
        if (sampleData.series.length === 0) {
          return (
            <div style={{ padding: '32px', textAlign: 'center', color: '#94a3b8', background: '#f8fafc', borderRadius: '8px', border: '1px dashed #e2e8f0' }}>
              <div style={{ fontSize: '28px', marginBottom: '8px' }}>📊</div>
              <div style={{ fontSize: '13px' }}>{isEnglish ? 'No data available for the selected products and date range.' : '所选产品和时间范围内暂无数据。'}</div>
            </div>
          );
        }
        const chartType = sampleData.chart_type || block.config?.chart_type || 'line';
        const width = 700;
        const height = 300;
        const padding = { top: 20, right: 20, bottom: 40, left: 60 };
        const chartWidth = width - padding.left - padding.right;
        const chartHeight = height - padding.top - padding.bottom;

        // Get all data points to calculate scales
        const allValues: number[] = [];
        sampleData.series.forEach((series: any) => {
          series.data.forEach((point: any) => allValues.push(point.value));
        });

        const minValue = Math.min(...allValues);
        const maxValue = Math.max(...allValues);
        const numPoints = sampleData.series[0]?.data?.length || 0;

        // Bar charts must start from 0 so bars are proportional; line/area/scatter can zoom in
        const isBarChart = chartType === 'bar';
        const yMin = isBarChart ? 0 : minValue;
        const valueRange = maxValue - yMin || 1;

        // Scale functions
        const scaleY = (value: number) => {
          return chartHeight - ((value - yMin) / valueRange) * chartHeight;
        };

        const scaleX = (index: number) => {
          // For bar charts, distribute points evenly across the full width
          if (chartType === 'bar') {
            return (index + 0.5) * (chartWidth / Math.max(numPoints, 1));
          }
        };

        // Generate Y-axis ticks
        const numTicks = 5;
        const yTicks = Array.from({ length: numTicks }, (_, i) => {
          const value = yMin + (valueRange * i / (numTicks - 1));
          return { value, y: scaleY(value) };
        });

        return (
          <div className="chart-container">
            <svg width={width} height={height} className="chart-svg">
              {/* Grid lines */}
              {yTicks.map((tick, idx) => (
                <line
                  key={`grid-${idx}`}
                  x1={padding.left}
                  y1={padding.top + tick.y}
                  x2={padding.left + chartWidth}
                  y2={padding.top + tick.y}
                  stroke="#e5e7eb"
                  strokeWidth="1"
                />
              ))}

              {/* Y-axis labels */}
              {yTicks.map((tick, idx) => (
                <text
                  key={`ylabel-${idx}`}
                  x={padding.left - 10}
                  y={padding.top + tick.y + 4}
                  textAnchor="end"
                  fontSize="11"
                  fill="#64748b"
                >
                  {tick.value.toFixed(0)}
                </text>
              ))}

              {/* X-axis labels (show every other point to avoid crowding) */}
              {sampleData.series[0]?.data?.map((point: any, idx: number) => {
                if (idx % Math.ceil(numPoints / 6) === 0 || idx === numPoints - 1) {
                  return (
                    <text
                      key={`xlabel-${idx}`}
                      x={padding.left + scaleX(idx)}
                      y={height - padding.bottom + 20}
                      textAnchor="middle"
                      fontSize="10"
                      fill="#64748b"
                    >
                      {new Date(point.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                    </text>
                  );
                }
                return null;
              })}

              {/* Render based on chart type */}
              {sampleData.series.map((series: any, seriesIdx: number) => {
                const color = `hsl(${seriesIdx * 360 / sampleData.series.length}, 70%, 50%)`;

                if (chartType === 'line') {
                  // Line chart
                  const pathData = series.data.map((point: any, idx: number) => {
                    const x = padding.left + scaleX(idx);
                    const y = padding.top + scaleY(point.value);
                    return `${idx === 0 ? 'M' : 'L'} ${x} ${y}`;
                  }).join(' ');

                  return (
                    <g key={seriesIdx}>
                      <path
                        d={pathData}
                        fill="none"
                        stroke={color}
                        strokeWidth="2.5"
                        strokeLinejoin="round"
                      />
                      {series.data.map((point: any, idx: number) => (
                        <circle
                          key={idx}
                          cx={padding.left + scaleX(idx)}
                          cy={padding.top + scaleY(point.value)}
                          r="3"
                          fill={color}
                        />
                      ))}
                    </g>
                  );
                } else if (chartType === 'area') {
                  // Area chart
                  const pathData = series.data.map((point: any, idx: number) => {
                    const x = padding.left + scaleX(idx);
                    const y = padding.top + scaleY(point.value);
                    return `${idx === 0 ? 'M' : 'L'} ${x} ${y}`;
                  }).join(' ');

                  const closedPath = `${pathData} L ${padding.left + scaleX(series.data.length - 1)} ${padding.top + chartHeight} L ${padding.left} ${padding.top + chartHeight} Z`;

                  return (
                    <g key={seriesIdx}>
                      <path
                        d={closedPath}
                        fill={color}
                        fillOpacity="0.2"
                        stroke={color}
                        strokeWidth="2"
                      />
                      <path
                        d={pathData}
                        fill="none"
                        stroke={color}
                        strokeWidth="2"
                      />
                    </g>
                  );
                } else if (chartType === 'bar') {
                  // Bar chart — each position is one slot of width chartWidth/numPoints
                  const slotWidth = chartWidth / Math.max(numPoints, 1);
                  const totalSeries = sampleData.series.length;
                  const barWidth = (slotWidth * 0.8) / Math.max(totalSeries, 1);

                  return (
                    <g key={seriesIdx}>
                      {series.data.map((point: any, idx: number) => {
                        const slotCenterX = padding.left + scaleX(idx);
                        const groupOffset = (totalSeries * barWidth) / 2;
                        const barX = slotCenterX - groupOffset + seriesIdx * barWidth;
                        const barHeight = Math.max(1, chartHeight - scaleY(point.value));
                        const barY = padding.top + scaleY(point.value);

                        return (
                          <rect
                            key={idx}
                            x={barX}
                            y={barY}
                            width={barWidth}
                            height={barHeight}
                            fill={color}
                            opacity="0.85"
                          />
                        );
                      })}
                    </g>
                  );
                } else if (chartType === 'scatter') {
                  // Scatter plot
                  return (
                    <g key={seriesIdx}>
                      {series.data.map((point: any, idx: number) => (
                        <circle
                          key={idx}
                          cx={padding.left + scaleX(idx)}
                          cy={padding.top + scaleY(point.value)}
                          r="5"
                          fill={color}
                          opacity="0.7"
                        />
                      ))}
                    </g>
                  );
                }
                return null;
              })}

              {/* Axes */}
              <line
                x1={padding.left}
                y1={padding.top}
                x2={padding.left}
                y2={padding.top + chartHeight}
                stroke="#94a3b8"
                strokeWidth="2"
              />
              <line
                x1={padding.left}
                y1={padding.top + chartHeight}
                x2={padding.left + chartWidth}
                y2={padding.top + chartHeight}
                stroke="#94a3b8"
                strokeWidth="2"
              />
            </svg>

            {/* Legend */}
            <div className="chart-legend">
              {sampleData.series.map((series: any, idx: number) => (
                <span key={idx} className="legend-item">
                  <span className="legend-dot" style={{ background: `hsl(${idx * 360 / sampleData.series.length}, 70%, 50%)` }}></span>
                  {series.name}
                </span>
              ))}
            </div>
          </div>
        );
      })()}

      {block.type === 'text' && (
        <div className="text-block">
          {block.config?.content ? (
            <div style={{ whiteSpace: 'pre-wrap', lineHeight: '1.75', color: '#334155', fontSize: '14px' }}>
              {block.config.content}
            </div>
          ) : (
            <p style={{ color: '#94a3b8', fontStyle: 'italic', fontSize: '14px' }}>
              {isEnglish
                ? 'Click ⚙ to add text content to this block.'
                : '点击 ⚙ 为此模块添加文本内容。'}
            </p>
          )}
        </div>
      )}

      {block.type === 'image' && (
        <div className="image-placeholder">
          {block.config?.image_url ? (
            <div style={{ textAlign: 'center' }}>
              <img
                src={block.config.image_url}
                alt={block.config.alt_text || ''}
                style={{ maxWidth: '100%', maxHeight: '400px', objectFit: 'contain', borderRadius: '8px' }}
              />
              {block.config.caption && (
                <p style={{ marginTop: '8px', fontSize: '12px', color: '#64748b', fontStyle: 'italic' }}>
                  {block.config.caption}
                </p>
              )}
            </div>
          ) : (
            <>
              <div className="image-icon">🖼</div>
              <p style={{ color: '#94a3b8', fontStyle: 'italic', fontSize: '14px' }}>
                {isEnglish ? 'Click ⚙ to add an image URL.' : '点击 ⚙ 添加图片链接。'}
              </p>
            </>
          )}
        </div>
      )}

      {/* ERP Block Rendering — chart + table */}
      {block.type.startsWith('erp_') && (() => {
        if (!cachedData || cachedData.error) {
          return (
            <div style={{ padding: '24px', textAlign: 'center', background: '#fffbeb', border: '1px solid #fde68a', borderRadius: '8px' }}>
              {cachedData?.error
                ? <div style={{ color: '#991b1b' }}><strong>{isEnglish ? 'Error:' : '错误：'}</strong> {cachedData.error}</div>
                : <div style={{ color: '#92400e' }}>🔒 {isEnglish ? 'Click 👁 to load ERP sales data' : '点击 👁 加载ERP销售数据'}</div>}
            </div>
          );
        }
        const erpRows: any[] = cachedData.data || [];
        const cols: string[] = cachedData.columns || [];
        const productLabel = isEnglish ? cachedData.product_name_en : cachedData.product_name_zh;
        const colLabels: Record<string, { en: string; zh: string }> = {
          month: { en: 'Month', zh: '月份' },
          date: { en: 'Date', zh: '日期' },
          customer: { en: 'Customer', zh: '客户' },
          total_quantity: { en: 'Volume (t)', zh: '销量(吨)' },
          total_amount: { en: 'Revenue (¥)', zh: '金额(元)' },
          avg_sale_price: { en: 'Avg Price', zh: '均价' },
          order_count: { en: 'Orders', zh: '订单数' },
          quantity: { en: 'Qty (t)', zh: '数量(吨)' },
          sale_price: { en: 'Sale Price', zh: '成交价' },
          amount: { en: 'Amount (¥)', zh: '金额(元)' },
        };
        const getColLabel = (k: string) => colLabels[k] ? (isEnglish ? colLabels[k].en : colLabels[k].zh) : k;
        const fmtNum = (v: any) => {
          const n = parseFloat(v);
          if (isNaN(n)) return String(v ?? '—');
          if (n >= 1000000) return (n / 1000000).toFixed(2) + 'M';
          if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
          return n.toFixed(2);
        };
        const COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#06b6d4', '#ec4899', '#84cc16'];

        const renderBarChart = (xKey: string, yKey: string, yLabel: string) => {
          const values = erpRows.map((r) => parseFloat(r[yKey]) || 0);
          if (values.length === 0) return null;
          const maxV = Math.max(...values, 1);
          const svgW = 620, svgH = 220, pad = { top: 20, right: 20, bottom: 50, left: 70 };
          const cW = svgW - pad.left - pad.right, cH = svgH - pad.top - pad.bottom;
          const bW = Math.max(6, (cW / values.length) * 0.7);
          return (
            <svg width={svgW} height={svgH} style={{ display: 'block', margin: '12px 0', maxWidth: '100%' }}>
              {[0, 0.25, 0.5, 0.75, 1].map((f, i) => {
                const val = maxV * f, y = pad.top + cH - f * cH;
                return <g key={i}>
                  <line x1={pad.left} y1={y} x2={pad.left + cW} y2={y} stroke="#e5e7eb" strokeWidth="1" />
                  <text x={pad.left - 6} y={y + 4} textAnchor="end" fontSize="10" fill="#6b7280">{fmtNum(val)}</text>
                </g>;
              })}
              <line x1={pad.left} y1={pad.top} x2={pad.left} y2={pad.top + cH} stroke="#94a3b8" strokeWidth="2" />
              <line x1={pad.left} y1={pad.top + cH} x2={pad.left + cW} y2={pad.top + cH} stroke="#94a3b8" strokeWidth="2" />
              {erpRows.map((row, i) => {
                const v = parseFloat(row[yKey]) || 0;
                const bH = Math.max(1, (v / maxV) * cH);
                const x = pad.left + (i + 0.5) * (cW / values.length) - bW / 2;
                const y = pad.top + cH - bH;
                const label = String(row[xKey] || '').slice(-7);
                return <g key={i}>
                  <rect x={x} y={y} width={bW} height={bH} fill={COLORS[i % COLORS.length]} opacity={0.85} rx={2} />
                  {i % Math.max(1, Math.ceil(values.length / 10)) === 0 && (
                    <text x={x + bW / 2} y={pad.top + cH + 16} textAnchor="middle" fontSize="9" fill="#6b7280">{label}</text>
                  )}
                </g>;
              })}
              <text transform={`translate(14,${pad.top + cH / 2}) rotate(-90)`} textAnchor="middle" fontSize="11" fill="#6b7280">{yLabel}</text>
            </svg>
          );
        };

        const renderPieChart = (valueKey: string, labelKey: string) => {
          const top8 = erpRows.slice(0, 8);
          const values = top8.map((r) => parseFloat(r[valueKey]) || 0);
          const total = values.reduce((a, b) => a + b, 0);
          if (total === 0) return null;
          const cx = 130, cy = 105, r = 90;
          let angle = -Math.PI / 2;
          return (
            <svg width={460} height={220} style={{ display: 'block', margin: '12px 0', maxWidth: '100%' }}>
              {top8.map((row, i) => {
                const sweep = (values[i] / total) * 2 * Math.PI;
                const x1 = cx + r * Math.cos(angle), y1 = cy + r * Math.sin(angle);
                angle += sweep;
                const x2 = cx + r * Math.cos(angle), y2 = cy + r * Math.sin(angle);
                const pct = ((values[i] / total) * 100).toFixed(1);
                const lbl = String(row[labelKey] || '').slice(0, 14);
                return <g key={i}>
                  <path d={`M ${cx} ${cy} L ${x1} ${y1} A ${r} ${r} 0 ${sweep > Math.PI ? 1 : 0} 1 ${x2} ${y2} Z`} fill={COLORS[i % COLORS.length]} stroke="white" strokeWidth="2" />
                  <rect x={270} y={10 + i * 26} width={12} height={12} fill={COLORS[i % COLORS.length]} rx={2} />
                  <text x={288} y={21 + i * 26} fontSize="11" fill="#374151">{lbl}: {pct}%</text>
                </g>;
              })}
            </svg>
          );
        };

        const renderLineChart = (xKey: string, yKey: string, yLabel: string) => {
          const filtered = erpRows.filter((r) => parseFloat(r[yKey]) > 0);
          if (filtered.length < 2) return null;
          const values = filtered.map((r) => parseFloat(r[yKey]));
          const minV = Math.min(...values), maxV = Math.max(...values);
          const vRange = maxV - minV || 1;
          const svgW = 620, svgH = 220, pad = { top: 20, right: 20, bottom: 50, left: 70 };
          const cW = svgW - pad.left - pad.right, cH = svgH - pad.top - pad.bottom;
          const scX = (i: number) => pad.left + (i / Math.max(filtered.length - 1, 1)) * cW;
          const scY = (v: number) => pad.top + cH - ((v - minV) / vRange) * cH;
          const pathD = filtered.map((r, i) => `${i === 0 ? 'M' : 'L'} ${scX(i)} ${scY(parseFloat(r[yKey]))}`).join(' ');
          return (
            <svg width={svgW} height={svgH} style={{ display: 'block', margin: '12px 0', maxWidth: '100%' }}>
              {[0, 0.25, 0.5, 0.75, 1].map((f, i) => {
                const val = minV + vRange * f, y = scY(val);
                return <g key={i}>
                  <line x1={pad.left} y1={y} x2={pad.left + cW} y2={y} stroke="#e5e7eb" strokeWidth="1" />
                  <text x={pad.left - 6} y={y + 4} textAnchor="end" fontSize="10" fill="#6b7280">{val.toFixed(0)}</text>
                </g>;
              })}
              <line x1={pad.left} y1={pad.top} x2={pad.left} y2={pad.top + cH} stroke="#94a3b8" strokeWidth="2" />
              <line x1={pad.left} y1={pad.top + cH} x2={pad.left + cW} y2={pad.top + cH} stroke="#94a3b8" strokeWidth="2" />
              <path d={pathD} fill="none" stroke="#3b82f6" strokeWidth="2.5" strokeLinejoin="round" />
              {filtered.map((r, i) => {
                if (i % Math.max(1, Math.ceil(filtered.length / 8)) !== 0 && i !== filtered.length - 1) return null;
                return <g key={i}>
                  <circle cx={scX(i)} cy={scY(parseFloat(r[yKey]))} r={3} fill="#3b82f6" />
                  <text x={scX(i)} y={pad.top + cH + 16} textAnchor="middle" fontSize="9" fill="#6b7280">{String(r[xKey] || '').slice(-7)}</text>
                </g>;
              })}
              <text transform={`translate(14,${pad.top + cH / 2}) rotate(-90)`} textAnchor="middle" fontSize="11" fill="#6b7280">{yLabel}</text>
            </svg>
          );
        };

        const qtyYLabel = isEnglish ? 'Volume (t)' : '销量(吨)';
        const priceYLabel = isEnglish ? 'Price (¥/t)' : '价格(元/吨)';

        return (
          <div style={{ padding: '0 0 12px 0' }}>
            <div style={{ padding: '8px 12px', background: '#fffbeb', border: '1px solid #fde68a', borderRadius: '6px', marginBottom: '12px', display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px', color: '#92400e' }}>
              <span>🔒</span>
              <span><strong>{isEnglish ? 'ERP / Sales Data' : 'ERP销售数据'}:</strong> {productLabel}</span>
              <span style={{ marginLeft: 'auto', color: '#b45309', fontSize: '11px' }}>{erpRows.length} {isEnglish ? 'records' : '条记录'}</span>
            </div>
            {(block.type === 'erp_sales_summary' || block.type === 'erp_monthly_trend') &&
              renderBarChart(cols.includes('month') ? 'month' : 'date', cols.includes('total_quantity') ? 'total_quantity' : 'quantity', qtyYLabel)}
            {block.type === 'erp_customer_breakdown' &&
              renderPieChart('total_quantity', 'customer')}
            {block.type === 'erp_delivery_status' && cols.includes('quantity') &&
              renderBarChart('date', 'quantity', qtyYLabel)}
            {block.type === 'erp_vs_market' &&
              renderLineChart(cols.includes('date') ? 'date' : 'month', cols.includes('sale_price') ? 'sale_price' : 'avg_sale_price', priceYLabel)}
            <div className="data-table-container">
              <table className="data-table">
                <thead><tr>{cols.map((c) => <th key={c}>{getColLabel(c)}</th>)}</tr></thead>
                <tbody>
                  {erpRows.slice(0, 50).map((row, ri) => (
                    <tr key={ri}>
                      {cols.map((c) => (
                        <td key={c} style={{ fontVariantNumeric: 'tabular-nums' }}>
                          {typeof row[c] === 'number' ? fmtNum(row[c]) : String(row[c] ?? '—')}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
              {erpRows.length > 50 && (
                <div style={{ textAlign: 'center', padding: '6px', fontSize: '12px', color: '#94a3b8' }}>
                  {isEnglish ? `Showing 50 of ${erpRows.length} rows` : `显示前50条（共${erpRows.length}条）`}
                </div>
              )}
            </div>
          </div>
        );
      })()}

      <style>{`
        .professional-block {
          margin-bottom: 32px;
        }

        .no-data-message {
          padding: 40px 20px;
          text-align: center;
          color: #94a3b8;
          font-size: 14px;
          font-style: italic;
        }

        .block-title {
          font-size: 20px;
          font-weight: 600;
          color: #1e293b;
          margin: 0 0 16px 0;
          padding-bottom: 8px;
          border-bottom: 2px solid #e5e7eb;
        }

        .kpi-cards-grid {
          display: grid;
          grid-template-columns: repeat(4, minmax(0, 1fr));
          gap: 0;
          margin: 16px 0 10px;
          border-top: 1px solid #d6ccb7;
          border-bottom: 1px solid #d6ccb7;
          background: #ffffff;
        }

        .kpi-card {
          background: #ffffff;
          color: #111827;
          padding: 20px 24px 18px;
          min-height: 148px;
          border-right: 1px solid #e8ddc8;
          display: flex;
          flex-direction: column;
          gap: 10px;
        }

        .kpi-card:nth-child(4n) {
          border-right: none;
        }

        .kpi-card-head {
          display: flex;
          flex-wrap: wrap;
          align-items: baseline;
          gap: 8px;
        }

        .kpi-label {
          font-size: 13px;
          color: #374151;
          font-weight: 700;
          letter-spacing: 0.02em;
        }

        .kpi-label-secondary {
          font-size: 9px;
          color: #7b808a;
          font-weight: 700;
          letter-spacing: 0.1em;
          text-transform: uppercase;
        }

        .kpi-value-row {
          display: flex;
          flex-wrap: wrap;
          align-items: flex-end;
          gap: 8px;
        }

        .kpi-value {
          font-size: 34px;
          font-weight: 700;
          line-height: 0.98;
          letter-spacing: -0.03em;
          font-variant-numeric: tabular-nums;
        }

        .kpi-unit {
          font-size: 11px;
          color: #6b7280;
          padding-bottom: 4px;
          letter-spacing: 0.03em;
          text-transform: uppercase;
        }

        .kpi-change-row {
          display: flex;
          align-items: center;
          flex-wrap: wrap;
          gap: 8px;
        }

        .kpi-change {
          font-size: 14px;
          font-weight: 700;
          display: inline-flex;
          align-items: center;
          gap: 6px;
        }

        .kpi-change.up {
          color: #0f766e;
        }

        .kpi-change.down {
          color: #a63d2a;
        }

        .kpi-change.neutral {
          color: #6b7280;
        }

        .kpi-change-arrow {
          font-size: 18px;
          line-height: 1;
        }

        .kpi-comparison {
          font-size: 11px;
          color: #6b7280;
          font-weight: 500;
        }

        .kpi-period-label {
          font-size: 12px;
          color: #94a3b8;
          margin: 4px 0 6px;
          font-style: italic;
        }

        .kpi-high-low {
          display: flex;
          flex-wrap: wrap;
          gap: 10px;
          margin-top: auto;
          font-size: 10px;
          color: #6b7280;
        }

        .kpi-range-item {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          white-space: nowrap;
        }

        .kpi-range-label {
          color: #9ca3af;
          font-weight: 600;
        }

        @media (max-width: 1200px) {
          .kpi-cards-grid {
            grid-template-columns: repeat(2, minmax(0, 1fr));
          }

          .kpi-card:nth-child(4n) {
            border-right: 1px solid #e8ddc8;
          }

          .kpi-card:nth-child(2n) {
            border-right: none;
          }
        }

        @media (max-width: 720px) {
          .kpi-cards-grid {
            grid-template-columns: 1fr;
          }

          .kpi-card,
          .kpi-card:nth-child(4n),
          .kpi-card:nth-child(2n) {
            border-right: none;
            border-bottom: 1px solid #e8ddc8;
          }

          .kpi-card:last-child {
            border-bottom: none;
          }

          .kpi-value {
            font-size: 30px;
          }
        }

        .data-table-container {
          overflow-x: auto;
          margin: 16px 0;
        }

        .data-table {
          width: 100%;
          border-collapse: collapse;
          font-size: 14px;
        }

        .data-table th {
          background: #f8fafc;
          color: #1e293b;
          font-weight: 600;
          padding: 12px;
          text-align: left;
          border-bottom: 2px solid #e5e7eb;
        }

        .data-table td {
          padding: 10px 12px;
          border-bottom: 1px solid #e5e7eb;
          color: #475569;
        }

        .data-table tbody tr:hover {
          background: #f8fafc;
        }

        .chart-container {
          margin: 16px 0;
          padding: 20px;
          background: #ffffff;
          border: 1px solid #e5e7eb;
          border-radius: 8px;
          display: flex;
          flex-direction: column;
          align-items: center;
        }

        .chart-svg {
          display: block;
          margin-bottom: 12px;
        }

        .chart-placeholder {
          background: #f8fafc;
          border: 2px dashed #cbd5e1;
          border-radius: 8px;
          padding: 60px 20px;
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          gap: 12px;
        }

        .chart-icon {
          font-size: 48px;
        }

        .chart-info {
          text-align: center;
        }

        .chart-type {
          font-weight: 600;
          color: #475569;
          margin-bottom: 4px;
          text-transform: capitalize;
        }

        .chart-desc {
          font-size: 13px;
          color: #94a3b8;
          margin-bottom: 12px;
        }

        .chart-legend {
          display: flex;
          flex-wrap: wrap;
          gap: 16px;
          justify-content: center;
          margin-top: 8px;
        }

        .legend-item {
          display: flex;
          align-items: center;
          gap: 6px;
          font-size: 13px;
          color: #475569;
        }

        .legend-dot {
          width: 12px;
          height: 12px;
          border-radius: 50%;
          display: inline-block;
        }

        .text-block {
          margin: 16px 0;
          padding: 16px;
          background: #f8fafc;
          border-left: 4px solid #3b82f6;
          border-radius: 4px;
        }

        .text-block p {
          margin: 0;
          line-height: 1.7;
          color: #475569;
        }

        .image-placeholder {
          background: #f8fafc;
          border: 2px dashed #cbd5e1;
          border-radius: 8px;
          padding: 80px 20px;
          text-align: center;
          margin: 16px 0;
        }

        .image-icon {
          font-size: 64px;
          margin-bottom: 12px;
        }

        .image-placeholder p {
          color: #94a3b8;
          margin: 0;
        }
      `}</style>
    </div>
  );
}

export default ProfessionalReportAuthoring;
