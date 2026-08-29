import React, { useEffect, useState } from "react";
import { useLanguage } from "../lib/language";
import {
  AlertRule,
  api,
  DataSource,
  Dataset,
  PlatformProduct,
} from "../services/api";

interface PlatformBuilderProps {
  currentUserIsAdmin: boolean;
}

export const PlatformBuilder: React.FC<PlatformBuilderProps> = ({
  currentUserIsAdmin,
}) => {
  const { language } = useLanguage();
  const tr = (en: string, zh: string) => (language === "en" ? en : zh);

  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [alertRules, setAlertRules] = useState<AlertRule[]>([]);
  const [dataSources, setDataSources] = useState<DataSource[]>([]);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [products, setProducts] = useState<PlatformProduct[]>([]);

  // New / edit product form state
  const [editingProductId, setEditingProductId] = useState<string | null>(null);
  const [newProductNameEn, setNewProductNameEn] = useState("");
  const [newProductNameCn, setNewProductNameCn] = useState("");
  const [newProductType, setNewProductType] = useState("downstream");
  const [newProductUnit, setNewProductUnit] = useState("CNY/ton");
  const [newProductDescription, setNewProductDescription] = useState("");
  const [newProductAliases, setNewProductAliases] = useState("");
  const [newProductDataTable, setNewProductDataTable] = useState("");
  const [newProductDataKey, setNewProductDataKey] = useState("");
  const [newProductDisplayInDashboard, setNewProductDisplayInDashboard] = useState(false);
  const [newProductDisplayInReports, setNewProductDisplayInReports] = useState(false);
  const [tableColumns, setTableColumns] = useState<{ name: string; type: string }[]>([]);
  const [isInspecting, setIsInspecting] = useState(false);

  const resetProductForm = () => {
    setEditingProductId(null);
    setNewProductNameEn("");
    setNewProductNameCn("");
    setNewProductType("downstream");
    setNewProductUnit("CNY/ton");
    setNewProductDescription("");
    setNewProductAliases("");
    setNewProductDataTable("");
    setNewProductDataKey("");
    setNewProductDisplayInDashboard(false);
    setNewProductDisplayInReports(false);
    setTableColumns([]);
  };

  const startEditProduct = (item: PlatformProduct) => {
    setEditingProductId(item.id);
    setNewProductNameEn(item.name_en);
    setNewProductNameCn(item.name_cn);
    setNewProductType(item.product_type);
    setNewProductUnit(item.unit);
    setNewProductDescription(item.description ?? "");
    setNewProductAliases(item.aliases.join(", "));
    setNewProductDataTable(item.data_table ?? "");
    setNewProductDataKey(item.data_key ?? "");
    setNewProductDisplayInDashboard(item.display_in.includes("dashboard"));
    setNewProductDisplayInReports(item.display_in.includes("reports"));
    setTableColumns([]);
    if (item.data_table) {
      api.inspectTable(item.data_table)
        .then((res) => setTableColumns(res.columns))
        .catch(() => setTableColumns([]));
    }
  };

  const doInspectTable = async (tableName: string) => {
    if (!tableName.trim()) return;
    setIsInspecting(true);
    setTableColumns([]);
    setError(null);
    try {
      const res = await api.inspectTable(tableName.trim());
      setTableColumns(res.columns);
    } catch (err) {
      setError(err instanceof Error ? err.message : `Table '${tableName}' not found`);
      setTableColumns([]);
    } finally {
      setIsInspecting(false);
    }
  };

  const [newDataSourceName, setNewDataSourceName] = useState("");
  const [newDataSourceType, setNewDataSourceType] = useState("mysql");
  const [newDatasetSourceId, setNewDatasetSourceId] = useState("");
  const [newDatasetName, setNewDatasetName] = useState("");
  const [newDatasetRef, setNewDatasetRef] = useState("");
  const [newRuleName, setNewRuleName] = useState("");
  const [newRuleMetric, setNewRuleMetric] = useState("");

  const loadAll = async () => {
    if (!currentUserIsAdmin) return;
    setIsLoading(true);
    setError(null);
    try {
      const [nextAlertRules, nextSources, nextDatasets, nextProducts] =
        await Promise.all([
          api.listPlatformAlertRules(),
          api.listPlatformDataSources(),
          api.listPlatformDatasets(),
          api.listPlatformProducts(),
        ]);
      setAlertRules(nextAlertRules);
      setDataSources(nextSources);
      setDatasets(nextDatasets);
      setProducts(nextProducts);
      setNewDatasetSourceId((current) => {
        if (current && nextSources.some((item) => item.id === current)) {
          return current;
        }
        return nextSources[0]?.id || "";
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load platform config");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadAll();
  }, [currentUserIsAdmin]);

  const saveDataSource = async () => {
    if (!newDataSourceName.trim()) return;
    setIsSaving(true);
    setError(null);
    try {
      await api.savePlatformDataSource({
        name: newDataSourceName.trim(),
        connector_type: newDataSourceType,
        connection_config: {},
      });
      setNewDataSourceName("");
      setStatus(tr("Saved data source", "已保存数据源"));
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save data source");
    } finally {
      setIsSaving(false);
    }
  };

  const saveDataset = async () => {
    if (!newDatasetSourceId || !newDatasetName.trim() || !newDatasetRef.trim()) return;
    setIsSaving(true);
    setError(null);
    try {
      await api.savePlatformDataset({
        source_id: newDatasetSourceId,
        friendly_name: newDatasetName.trim(),
        physical_reference: newDatasetRef.trim(),
        tags: [],
        schema_snapshot: {},
      });
      setNewDatasetName("");
      setNewDatasetRef("");
      setStatus(tr("Saved dataset", "已保存数据集"));
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save dataset");
    } finally {
      setIsSaving(false);
    }
  };

  const saveAlertRule = async () => {
    if (!newRuleName.trim() || !newRuleMetric.trim()) return;
    setIsSaving(true);
    setError(null);
    try {
      await api.savePlatformAlertRule({
        name: newRuleName.trim(),
        metric_expression: newRuleMetric.trim(),
        condition_type: "threshold_above",
        condition_params: { threshold: 0 },
        severity: "medium",
        category: "custom",
        notification_channels: [{ type: "in_app" }],
      });
      setNewRuleName("");
      setNewRuleMetric("");
      setStatus(tr("Saved alert rule", "已保存预警规则"));
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save alert rule");
    } finally {
      setIsSaving(false);
    }
  };

  const removeDataSource = async (id: string) => {
    await api.deletePlatformDataSource(id);
    await loadAll();
  };

  const removeDataset = async (id: string) => {
    await api.deletePlatformDataset(id);
    await loadAll();
  };

  const removeAlertRule = async (id: string) => {
    await api.deletePlatformAlertRule(id);
    await loadAll();
  };

  if (!currentUserIsAdmin) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white p-5 text-sm text-slate-600">
        {tr(
          "Platform configuration is admin-only.",
          "平台配置仅管理员可用。",
        )}
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 md:p-6 space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-xl font-semibold text-slate-900">
            {tr("Platform Builder", "平台构建器")}
          </h2>
          <p className="text-sm text-slate-500">
            {tr(
              "Configure alert rules, data sources, datasets, and products.",
              "配置预警规则、数据源、数据集和产品。",
            )}
          </p>
        </div>
        <button
          type="button"
          onClick={loadAll}
          disabled={isLoading}
          className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 disabled:bg-slate-100"
        >
          {isLoading ? tr("Loading...", "加载中...") : tr("Refresh", "刷新")}
        </button>
      </div>

      {status && (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-800">
          {status}
        </div>
      )}
      {error && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">
          {error}
        </div>
      )}

      <div className="rounded-lg border border-slate-200 p-4 space-y-3">
        <div>
          <h3 className="font-medium text-slate-900">{tr("Products", "产品管理")}</h3>
          <p className="text-xs text-slate-500 mt-0.5">
            {tr(
              "Registered products are always accessible to the chat assistant. Choose where they also appear visually.",
              "已注册的产品始终可供聊天助手访问。选择它们还将在哪些界面显示。",
            )}
          </p>
        </div>

        {/* Names */}
        <div id="product-form-top" className="grid grid-cols-1 md:grid-cols-2 gap-2">
          <input
            value={newProductNameEn}
            onChange={(e) => setNewProductNameEn(e.target.value)}
            placeholder={tr("Product name (EN) *", "英文名称 *")}
            className="rounded border border-slate-200 px-3 py-2 text-sm"
          />
          <input
            value={newProductNameCn}
            onChange={(e) => setNewProductNameCn(e.target.value)}
            placeholder={tr("Product name (CN) *", "中文名称 *")}
            className="rounded border border-slate-200 px-3 py-2 text-sm"
          />
        </div>

        {/* Table name + Inspect */}
        <div className="flex gap-2">
          <input
            value={newProductDataTable}
            onChange={(e) => { setNewProductDataTable(e.target.value); setTableColumns([]); }}
            onKeyDown={(e) => { if (e.key === "Enter") doInspectTable(newProductDataTable); }}
            placeholder={tr("Database table name (e.g. lz_market_prices)", "数据库表名（如 lz_market_prices）")}
            className="flex-1 rounded border border-slate-200 px-3 py-2 text-sm font-mono"
          />
          <button
            type="button"
            onClick={() => doInspectTable(newProductDataTable)}
            disabled={isInspecting || !newProductDataTable.trim()}
            className="rounded border border-slate-300 bg-slate-50 px-3 py-2 text-sm text-slate-700 hover:bg-slate-100 disabled:bg-slate-100 disabled:text-slate-400 whitespace-nowrap"
          >
            {isInspecting ? tr("Inspecting…", "分析中…") : tr("Inspect ▶", "分析表 ▶")}
          </button>
        </div>

        {/* Discovered columns */}
        {tableColumns.length > 0 && (
          <div className="rounded-lg bg-slate-50 border border-slate-200 p-3 space-y-2">
            <p className="text-xs font-medium text-slate-600">
              {tableColumns.length} {tr("columns found in", "列发现于")} <code className="font-mono text-slate-800">{newProductDataTable}</code>:
            </p>
            <div className="flex flex-wrap gap-1">
              {tableColumns.map((col) => (
                <span
                  key={col.name}
                  className="inline-flex items-center gap-1 rounded bg-white border border-slate-200 px-2 py-0.5 text-xs font-mono text-slate-700"
                >
                  {col.name}
                  <span className="text-slate-400 text-[10px]">{col.type.split("(")[0]}</span>
                </span>
              ))}
            </div>
            <div className="flex items-center gap-2">
              <label className="text-xs text-slate-600 whitespace-nowrap shrink-0">
                {tr("Price / value column:", "价格/数值列：")}
              </label>
              <select
                value={newProductDataKey}
                onChange={(e) => setNewProductDataKey(e.target.value)}
                className="flex-1 rounded border border-slate-200 px-2 py-1.5 text-xs bg-white font-mono"
              >
                <option value="">{tr("— select column —", "— 选择列 —")}</option>
                {tableColumns.map((col) => (
                  <option key={col.name} value={col.name}>
                    {col.name} ({col.type.split("(")[0]})
                  </option>
                ))}
              </select>
            </div>
          </div>
        )}

        {/* Manual data key when no columns discovered */}
        {tableColumns.length === 0 && newProductDataTable.trim() && (
          <input
            value={newProductDataKey}
            onChange={(e) => setNewProductDataKey(e.target.value)}
            placeholder={tr("Price column name (or click Inspect to auto-discover)", "价格列名（或点击分析自动发现）")}
            className="w-full rounded border border-slate-200 px-3 py-2 text-sm font-mono"
          />
        )}

        {/* Aliases */}
        <input
          value={newProductAliases}
          onChange={(e) => setNewProductAliases(e.target.value)}
          placeholder={tr(
            "Chat aliases — comma separated (e.g. brent, 布伦特, north sea crude)",
            "聊天别名，逗号分隔，供AI识别（如：brent, 布伦特, 北海原油）",
          )}
          className="w-full rounded border border-slate-200 px-3 py-2 text-sm"
        />

        {/* Description */}
        <textarea
          value={newProductDescription}
          onChange={(e) => setNewProductDescription(e.target.value)}
          placeholder={tr(
            "Product description (optional) — e.g. Cracked C5 fraction, used in resin production",
            "产品描述（可选）— 如：裂解C5馏分，用于树脂生产",
          )}
          rows={2}
          className="w-full rounded border border-slate-200 px-3 py-2 text-sm resize-none"
        />

        {/* Display location */}
        <div className="rounded-lg border border-slate-100 bg-slate-50 p-3 space-y-2">
          <p className="text-xs font-medium text-slate-700">{tr("Show in:", "显示位置：")}</p>
          <div className="flex flex-wrap gap-5 items-center">
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input
                type="checkbox"
                checked={newProductDisplayInDashboard}
                onChange={(e) => setNewProductDisplayInDashboard(e.target.checked)}
                className="rounded"
              />
              📊 {tr("Dashboard", "仪表盘")}
            </label>
            {newProductDisplayInDashboard && (
              <div className="flex items-center gap-2">
                <span className="text-xs text-slate-500">{tr("Tab:", "标签页：")}</span>
                <select
                  value={newProductType}
                  onChange={(e) => setNewProductType(e.target.value)}
                  className="rounded border border-slate-200 px-2 py-1 text-xs bg-white"
                >
                  <option value="upstream">{tr("Upstream Feedstocks", "上游原料")}</option>
                  <option value="midstream">{tr("Midstream Products", "中游产品")}</option>
                  <option value="downstream">{tr("Downstream Products", "下游产品")}</option>
                </select>
              </div>
            )}
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input
                type="checkbox"
                checked={newProductDisplayInReports}
                onChange={(e) => setNewProductDisplayInReports(e.target.checked)}
                className="rounded"
              />
              📄 {tr("Reports", "报告")}
            </label>
          </div>
          <p className="text-xs text-slate-500">
            💬 {tr(
              "Chat assistant always has access to all registered products regardless of this setting.",
              "聊天助手无论此设置如何，始终可访问所有已注册产品。",
            )}
          </p>
        </div>

        {/* Save / Cancel */}
        <div className="flex gap-2">
          <button
            type="button"
            disabled={isSaving || !newProductNameEn.trim() || !newProductNameCn.trim()}
            onClick={async () => {
              setIsSaving(true);
              setError(null);
              const displayIn: string[] = [];
              if (newProductDisplayInDashboard) displayIn.push("dashboard");
              if (newProductDisplayInReports) displayIn.push("reports");
              const payload = {
                name_en: newProductNameEn.trim(),
                name_cn: newProductNameCn.trim(),
                product_type: newProductType,
                unit: newProductUnit.trim() || "CNY/ton",
                description: newProductDescription.trim() || undefined,
                aliases: newProductAliases.split(",").map((a) => a.trim()).filter(Boolean),
                data_table: newProductDataTable.trim() || undefined,
                data_key: newProductDataKey.trim() || undefined,
                display_in: displayIn,
                schema_snapshot: tableColumns.length > 0 ? { columns: tableColumns } : undefined,
              };
              try {
                if (editingProductId) {
                  await api.updatePlatformProduct(editingProductId, payload);
                  setStatus(tr("Product updated", "产品已更新"));
                } else {
                  await api.savePlatformProduct(payload);
                  setStatus(tr("Product saved", "产品已保存"));
                }
                resetProductForm();
                await loadAll();
              } catch (err) {
                setError(err instanceof Error ? err.message : "Failed to save product");
              } finally {
                setIsSaving(false);
              }
            }}
            className="rounded bg-slate-900 px-4 py-2 text-sm text-white disabled:bg-slate-200"
          >
            {editingProductId ? tr("Save Changes", "保存更改") : tr("Add Product", "新增产品")}
          </button>
          {editingProductId && (
            <button
              type="button"
              onClick={resetProductForm}
              className="rounded border border-slate-200 px-3 py-2 text-sm"
            >
              {tr("Cancel", "取消")}
            </button>
          )}
        </div>

        {/* Product table */}
        {products.length > 0 ? (
          <div className="overflow-x-auto rounded-lg border border-slate-200">
            <table className="w-full text-xs text-left border-collapse">
              <thead>
                <tr className="bg-slate-900 text-slate-100">
                  <th className="px-3 py-2.5 font-semibold text-slate-400 whitespace-nowrap w-8">#</th>
                  <th className="px-3 py-2.5 font-semibold whitespace-nowrap">{tr("Product CN", "中文名称")}</th>
                  <th className="px-3 py-2.5 font-semibold whitespace-nowrap">{tr("Product EN", "英文名称")}</th>
                  <th className="px-3 py-2.5 font-semibold whitespace-nowrap">{tr("Category", "分类")}</th>
                  <th className="px-3 py-2.5 font-semibold whitespace-nowrap">{tr("Description", "描述")}</th>
                  <th className="px-3 py-2.5 font-semibold whitespace-nowrap">{tr("Chat Aliases", "聊天别名")}</th>
                  <th className="px-3 py-2.5 font-semibold whitespace-nowrap">{tr("Table Name", "数据表")}</th>
                  <th className="px-3 py-2.5 font-semibold whitespace-nowrap">{tr("Inspect", "检查")}</th>
                  <th className="px-3 py-2.5 font-semibold whitespace-nowrap">{tr("Data Key", "数据键")}</th>
                  <th className="px-3 py-2.5 font-semibold whitespace-nowrap">{tr("Data Show Point", "显示位置")}</th>
                  <th className="px-3 py-2.5 font-semibold whitespace-nowrap">{tr("Actions", "操作")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {products.map((item, idx) => (
                  <tr
                    key={item.id}
                    className={
                      editingProductId === item.id
                        ? "bg-amber-50 border-l-4 border-l-amber-400"
                        : idx % 2 === 0
                          ? "bg-white hover:bg-blue-50 transition-colors"
                          : "bg-slate-50 hover:bg-blue-50 transition-colors"
                    }
                  >
                    <td className="px-3 py-2 text-slate-400">{idx + 1}</td>
                    <td className="px-3 py-2 font-semibold text-slate-900 whitespace-nowrap">{item.name_cn}</td>
                    <td className="px-3 py-2 text-slate-700 whitespace-nowrap">{item.name_en}</td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      {item.product_type === "upstream" ? (
                        <span className="inline-block rounded-full px-2 py-0.5 text-[10px] font-semibold bg-orange-100 text-orange-700">Upstream</span>
                      ) : item.product_type === "midstream" ? (
                        <span className="inline-block rounded-full px-2 py-0.5 text-[10px] font-semibold bg-blue-100 text-blue-700">Midstream</span>
                      ) : (
                        <span className="inline-block rounded-full px-2 py-0.5 text-[10px] font-semibold bg-green-100 text-green-700">Downstream</span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-slate-500 max-w-[220px]">
                      {item.description ? (
                        <span className="line-clamp-2 leading-relaxed">{item.description}</span>
                      ) : (
                        <span className="text-slate-300 italic">—</span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-slate-500 max-w-[200px]">
                      {item.aliases.length > 0 ? (
                        <span className="line-clamp-2">{item.aliases.join(", ")}</span>
                      ) : (
                        <span className="text-slate-300 italic">—</span>
                      )}
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      {item.data_table ? (
                        <code className="bg-slate-100 rounded px-1.5 py-0.5 font-mono text-slate-700">{item.data_table}</code>
                      ) : (
                        <span className="text-slate-300 italic">—</span>
                      )}
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      {item.data_table ? (
                        <button
                          type="button"
                          onClick={() => startEditProduct(item)}
                          className="rounded border border-emerald-300 bg-emerald-50 px-2 py-1 text-[11px] font-medium text-emerald-800 hover:bg-emerald-100 transition-colors"
                        >
                          {tr("Inspect", "检查")}
                        </button>
                      ) : (
                        <span className="text-slate-300 italic">—</span>
                      )}
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      {item.data_key ? (
                        <code className="font-mono text-slate-600">{item.data_key}</code>
                      ) : (
                        <span className="text-slate-300 italic">—</span>
                      )}
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      <div className="flex flex-wrap gap-1">
                        <span className="inline-flex items-center rounded bg-blue-50 border border-blue-100 px-1.5 py-0.5 text-[10px] font-medium text-blue-700">
                          💬 Chat
                        </span>
                        {item.display_in.includes("dashboard") && (
                          <span className="inline-flex items-center rounded bg-emerald-50 border border-emerald-200 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700">
                            📊 Dashboard
                          </span>
                        )}
                        {item.display_in.includes("reports") && (
                          <span className="inline-flex items-center rounded bg-violet-50 border border-violet-200 px-1.5 py-0.5 text-[10px] font-medium text-violet-700">
                            📄 Reports
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      <div className="flex gap-3">
                        <button
                          type="button"
                          onClick={() => {
                            startEditProduct(item);
                            const el = document.getElementById("product-form-top");
                            if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
                          }}
                          className="text-slate-600 hover:text-slate-900 underline text-[11px]"
                        >
                          {tr("Edit", "编辑")}
                        </button>
                        <button
                          type="button"
                          onClick={async () => {
                            setIsSaving(true);
                            try {
                              await api.deletePlatformProduct(item.id);
                              if (editingProductId === item.id) resetProductForm();
                              await loadAll();
                            } catch (err) {
                              setError(err instanceof Error ? err.message : "Failed to delete product");
                            } finally {
                              setIsSaving(false);
                            }
                          }}
                          className="text-rose-600 hover:text-rose-800 underline text-[11px]"
                        >
                          {tr("Delete", "删除")}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-slate-400 italic text-center py-6 border border-dashed border-slate-200 rounded-lg">
            {tr(
              "No products registered yet. Use the form above to add your first product.",
              "尚未注册任何产品。使用上方表单添加第一个产品。"
            )}
          </p>
        )}
      </div>
    </div>
  );
};
