import {
  CheckCircle2,
  Database,
  Download,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import React, { useEffect, useMemo, useState } from "react";
import { useLanguage } from "../lib/language";
import { api, PlatformProduct, PlatformProductImportPreview, ProductDataMappingPreview, SavePlatformProductRequest } from "../services/api";

interface ProductCatalogManagerProps {
  currentUserIsAdmin: boolean;
}

type ProductModule = "upstream" | "midstream" | "downstream";
type ProductDataSource = "market_prices" | "erp" | "other";

interface ProductFormState {
  name_en: string;
  name_cn: string;
  product_type: ProductModule;
  unit: string;
  aliases: string;
  data_source: ProductDataSource;
  data_table: string;
  data_key: string;
  description: string;
  showDashboard: boolean;
  showReports: boolean;
}

type CatalogProduct = PlatformProduct & {
  source: "system" | "custom";
  description_zh?: string | null;
};

const EMPTY_FORM: ProductFormState = {
  name_en: "",
  name_cn: "",
  product_type: "downstream",
  unit: "CNY/ton",
  aliases: "",
  data_source: "market_prices",
  data_table: "",
  data_key: "",
  description: "",
  showDashboard: true,
  showReports: false,
};

const HIDDEN_SYSTEM_PRODUCTS_KEY = "edia.productCatalog.hiddenSystemProducts.v1";

const MODULES: ProductModule[] = ["upstream", "midstream", "downstream"];

const SYSTEM_PRODUCTS: CatalogProduct[] = [
  {
    id: "crude_oil",
    org_id: "system",
    name_en: "Brent Crude Oil",
    name_cn: "布伦特原油",
    product_type: "upstream",
    unit: "USD/bbl",
    aliases: ["brent", "crude oil", "原油", "布伦特"],
    description: "Global crude oil benchmark used as an upstream cost driver for naphtha, C5/C9 feedstocks, and downstream petrochemical pricing.",
    description_zh: "全球原油基准价格，用作石脑油、C5/C9 原料以及下游石化价格分析的上游成本驱动因素。",
    data_table: "market_prices",
    data_key: "crude_oil",
    display_in: ["dashboard", "reports"],
    created_by: "system",
    created_at: "system",
    updated_at: "system",
    source: "system",
  },
  {
    id: "naphtha",
    org_id: "system",
    name_en: "Japanese Naphtha",
    name_cn: "日本石脑油",
    product_type: "upstream",
    unit: "CNY/ton",
    aliases: ["naphtha", "石脑油"],
    description: "Petrochemical feedstock produced from crude oil and used as a key upstream input for cracking and C5/C9 value-chain cost analysis.",
    description_zh: "由原油加工得到的石化原料，是裂解及 C5/C9 产业链成本分析中的关键上游投入。",
    data_table: "market_prices",
    data_key: "naphtha",
    display_in: ["dashboard", "reports"],
    created_by: "system",
    created_at: "system",
    updated_at: "system",
    source: "system",
  },
  {
    id: "cracked_c5",
    org_id: "system",
    name_en: "Cracked C5",
    name_cn: "工业用裂解碳五",
    product_type: "upstream",
    unit: "CNY/ton",
    aliases: ["mixed c5", "c5", "裂解碳五", "混合碳五"],
    description: "Mixed C5 fraction from cracking units, used as upstream feedstock for isoprene, piperylene, DCPD, raffinate C5, SIS, and C5 resin analysis.",
    description_zh: "裂解装置产生的混合 C5 馏分，用作异戊二烯、间戊二烯、双环戊二烯、抽余碳五、SIS 和 C5 树脂分析的上游原料。",
    data_table: "market_prices",
    data_key: "cracked_c5",
    display_in: ["dashboard", "reports"],
    created_by: "system",
    created_at: "system",
    updated_at: "system",
    source: "system",
  },
  {
    id: "cracked_c9",
    org_id: "system",
    name_en: "Cracked C9",
    name_cn: "工业用裂解碳九",
    product_type: "upstream",
    unit: "CNY/ton",
    aliases: ["mixed c9", "c9", "裂解碳九", "混合碳九"],
    description: "Mixed C9 aromatic fraction from cracking units, tracked as an upstream/intermediate feedstock for C9 resin and aromatic derivative analysis.",
    description_zh: "裂解装置产生的混合 C9 芳烃馏分，用于 C9 树脂及芳烃衍生品分析的上游/中间原料跟踪。",
    data_table: "market_prices",
    data_key: "cracked_c9",
    display_in: ["dashboard", "reports"],
    created_by: "system",
    created_at: "system",
    updated_at: "system",
    source: "system",
  },
  {
    id: "isoprene",
    org_id: "system",
    name_en: "Isoprene",
    name_cn: "异戊二烯",
    product_type: "midstream",
    unit: "CNY/ton",
    aliases: ["isoprene", "异戊二烯"],
    description: "C5 separation derivative used as a midstream monomer and a major raw material for SIS and synthetic rubber value-chain analysis.",
    description_zh: "C5 分离衍生品，作为中游单体，也是 SIS 和合成橡胶产业链分析的重要原料。",
    data_table: "market_prices",
    data_key: "isoprene",
    display_in: ["dashboard", "reports"],
    created_by: "system",
    created_at: "system",
    updated_at: "system",
    source: "system",
  },
  {
    id: "piperylene",
    org_id: "system",
    name_en: "Piperylene",
    name_cn: "间戊二烯",
    product_type: "midstream",
    unit: "CNY/ton",
    aliases: ["piperylene", "间戊二烯", "间戊"],
    description: "C5 separation derivative used in petroleum resin and specialty chemical production, tracked as a midstream C5 product.",
    description_zh: "C5 分离衍生品，用于石油树脂和特种化学品生产，作为中游 C5 产品跟踪。",
    data_table: "market_prices",
    data_key: "piperylene",
    display_in: ["dashboard", "reports"],
    created_by: "system",
    created_at: "system",
    updated_at: "system",
    source: "system",
  },
  {
    id: "dcpd",
    org_id: "system",
    name_en: "DCPD",
    name_cn: "双环戊二烯",
    product_type: "midstream",
    unit: "CNY/ton",
    aliases: ["dcpd", "双环戊二烯"],
    description: "Dicyclopentadiene, a C5 separation derivative used in resin, polymer, and specialty material markets.",
    description_zh: "双环戊二烯，C5 分离衍生品，用于树脂、聚合物和特种材料市场。",
    data_table: "market_prices",
    data_key: "dcpd",
    display_in: ["dashboard", "reports"],
    created_by: "system",
    created_at: "system",
    updated_at: "system",
    source: "system",
  },
  {
    id: "raffinate_c5",
    org_id: "system",
    name_en: "Raffinate C5",
    name_cn: "抽余碳五",
    product_type: "midstream",
    unit: "CNY/ton",
    aliases: ["raffinate c5", "抽余碳五", "抽余c5"],
    description: "Residual C5 stream after separation, used as a midstream product in C5 balance, supply, and downstream feedstock analysis.",
    description_zh: "C5 分离后的剩余物流，用于 C5 平衡、供应和下游原料分析的中游产品。",
    data_table: "market_prices",
    data_key: "raffinate_c5",
    display_in: ["dashboard", "reports"],
    created_by: "system",
    created_at: "system",
    updated_at: "system",
    source: "system",
  },
  {
    id: "sis",
    org_id: "system",
    name_en: "SIS",
    name_cn: "SIS",
    product_type: "downstream",
    unit: "CNY/ton",
    aliases: ["sis", "sis rubber", "sis橡胶"],
    description: "Styrene-isoprene-styrene thermoplastic elastomer downstream product, linked to isoprene cost and adhesive/rubber demand.",
    description_zh: "苯乙烯-异戊二烯-苯乙烯热塑性弹性体下游产品，与异戊二烯成本以及胶粘剂/橡胶需求相关。",
    data_table: "market_prices",
    data_key: "sis",
    display_in: ["dashboard", "reports"],
    created_by: "system",
    created_at: "system",
    updated_at: "system",
    source: "system",
  },
  {
    id: "c5_resin",
    org_id: "system",
    name_en: "C5 Resin",
    name_cn: "碳五石油树脂",
    product_type: "downstream",
    unit: "CNY/ton",
    aliases: ["c5 resin", "c5 petroleum resin", "碳五树脂"],
    description: "C5 petroleum resin downstream product used in adhesives, road-marking paint, rubber compounding, and related end-use demand analysis.",
    description_zh: "C5 石油树脂下游产品，用于胶粘剂、道路标线涂料、橡胶配混及相关终端需求分析。",
    data_table: "market_prices",
    data_key: "c5_resin",
    display_in: ["dashboard", "reports"],
    created_by: "system",
    created_at: "system",
    updated_at: "system",
    source: "system",
  },
  {
    id: "blowing_agent",
    org_id: "system",
    name_en: "Blowing Agent",
    name_cn: "戊烷发泡剂",
    product_type: "downstream",
    unit: "CNY/ton",
    aliases: ["blowing agent", "发泡剂", "戊烷发泡剂"],
    description: "Pentane blowing agent downstream product used in foam materials, insulation, and packaging applications.",
    description_zh: "戊烷发泡剂下游产品，用于泡沫材料、保温材料和包装应用。",
    data_table: "market_prices",
    data_key: "blowing_agent",
    display_in: ["dashboard", "reports"],
    created_by: "system",
    created_at: "system",
    updated_at: "system",
    source: "system",
  },
  {
    id: "styrene",
    org_id: "system",
    name_en: "Styrene",
    name_cn: "苯乙烯",
    product_type: "downstream",
    unit: "CNY/ton",
    aliases: ["styrene", "苯乙烯"],
    description: "Aromatic downstream product used in polymer and resin markets, tracked for downstream petrochemical price and demand signals.",
    description_zh: "芳烃类下游产品，用于聚合物和树脂市场，跟踪下游石化价格与需求信号。",
    data_table: "market_prices",
    data_key: "styrene",
    display_in: ["dashboard", "reports"],
    created_by: "system",
    created_at: "system",
    updated_at: "system",
    source: "system",
  },
];

function normalizeModule(value: string | null | undefined): ProductModule {
  if (value === "upstream" || value === "midstream" || value === "downstream") {
    return value;
  }
  if (value === "crude" || value === "feedstock" || value === "olefin") {
    return "upstream";
  }
  if (value === "c5_mix" || value === "c5_derivative" || value === "c9_derivative") {
    return "midstream";
  }
  return "downstream";
}

function splitAliases(value: string): string[] {
  return value
    .split(/[,，;；\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function inferDataSource(dataTable: string | null | undefined): ProductDataSource {
  const value = String(dataTable || "").trim().toLowerCase();
  if (!value || value === "market_prices") return "market_prices";
  if (value === "erp" || value.includes("erp") || value.startsWith("sale_")) return "erp";
  return "other";
}

function toForm(product: PlatformProduct): ProductFormState {
  return {
    name_en: product.name_en,
    name_cn: product.name_cn,
    product_type: normalizeModule(product.product_type),
    unit: product.unit || "CNY/ton",
    aliases: product.aliases.join(", "),
    data_source: inferDataSource(product.data_table),
    data_table: product.data_table || "",
    data_key: product.data_key || "",
    description: product.description || "",
    showDashboard: product.display_in.includes("dashboard"),
    showReports: product.display_in.includes("reports"),
  };
}

function readHiddenSystemProducts(): string[] {
  try {
    const raw = window.localStorage.getItem(HIDDEN_SYSTEM_PRODUCTS_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((item) => typeof item === "string") : [];
  } catch {
    return [];
  }
}

function writeHiddenSystemProducts(ids: string[]) {
  window.localStorage.setItem(HIDDEN_SYSTEM_PRODUCTS_KEY, JSON.stringify(Array.from(new Set(ids))));
}

export const ProductCatalogManager: React.FC<ProductCatalogManagerProps> = ({
  currentUserIsAdmin,
}) => {
  const { language } = useLanguage();
  const tr = (en: string, zh: string) => (language === "en" ? en : zh);

  const [products, setProducts] = useState<PlatformProduct[]>([]);
  const [form, setForm] = useState<ProductFormState>(EMPTY_FORM);
  const [isFormOpen, setIsFormOpen] = useState(false);
  const [isImportOpen, setIsImportOpen] = useState(false);
  const [editingProductId, setEditingProductId] = useState<string | null>(null);
  const [editingProductSource, setEditingProductSource] = useState<"system" | "custom" | null>(null);
  const [hiddenSystemProductIds, setHiddenSystemProductIds] = useState<string[]>(() => readHiddenSystemProducts());
  const [query, setQuery] = useState("");
  const [moduleFilter, setModuleFilter] = useState<"all" | ProductModule>("all");
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isImporting, setIsImporting] = useState(false);
  const [isPreviewingData, setIsPreviewingData] = useState(false);
  const [dataPreview, setDataPreview] = useState<ProductDataMappingPreview | null>(null);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importPreview, setImportPreview] = useState<PlatformProductImportPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  const labels = {
    upstream: tr("Upstream", "上游"),
    midstream: tr("Midstream", "中游"),
    downstream: tr("Downstream", "下游"),
  };

  const dataSourceLabels: Record<ProductDataSource, string> = {
    market_prices: tr("Market Prices", "市场价格"),
    erp: "ERP",
    other: tr("Other", "其他"),
  };

  const productPrimaryName = (product: CatalogProduct) => {
    return language === "en"
      ? product.name_en || product.name_cn
      : product.name_cn || product.name_en;
  };

  const productSecondaryName = (product: CatalogProduct) => {
    return language === "en"
      ? product.name_cn || product.name_en
      : product.name_en || product.name_cn;
  };

  const productDescription = (product: CatalogProduct) => {
    if (language === "zh" && product.description_zh) {
      return product.description_zh;
    }
    return product.description || "";
  };

  const loadProducts = async () => {
    if (!currentUserIsAdmin) return;
    setIsLoading(true);
    setError(null);
    try {
      const rows = await api.listPlatformProducts();
      setProducts(rows);
    } catch (err) {
      setError(err instanceof Error ? err.message : tr("Failed to load products", "加载产品失败"));
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadProducts();
  }, [currentUserIsAdmin]);

  const catalogProducts = useMemo<CatalogProduct[]>(() => {
    const customProducts = products.map((product) => ({ ...product, source: "custom" as const }));
    const customIds = new Set(customProducts.map((product) => product.id));
    return [
      ...SYSTEM_PRODUCTS.filter((product) => !customIds.has(product.id) && !hiddenSystemProductIds.includes(product.id)),
      ...customProducts,
    ];
  }, [hiddenSystemProductIds, products]);

  const filteredProducts = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return catalogProducts.filter((product) => {
      const productModule = normalizeModule(product.product_type);
      if (moduleFilter !== "all" && productModule !== moduleFilter) return false;
      if (!needle) return true;
      return [
        product.id,
        product.name_cn,
        product.name_en,
        product.product_type,
        product.unit,
        product.description || "",
        product.description_zh || "",
        product.aliases.join(" "),
        product.data_table || "",
        product.data_key || "",
      ]
        .join(" ")
        .toLowerCase()
        .includes(needle);
    });
  }, [catalogProducts, query, moduleFilter]);

  const stats = useMemo(() => {
    return MODULES.reduce<Record<ProductModule, number>>(
      (acc, productModule) => {
        acc[productModule] = catalogProducts.filter(
          (product) => normalizeModule(product.product_type) === productModule,
        ).length;
        return acc;
      },
      { upstream: 0, midstream: 0, downstream: 0 },
    );
  }, [catalogProducts]);

  const updateForm = <K extends keyof ProductFormState>(key: K, value: ProductFormState[K]) => {
    setForm((current) => {
      const next = { ...current, [key]: value };
      if (key === "data_source") {
        next.data_table = value === "market_prices" ? "market_prices" : value === "erp" ? "erp" : "";
      }
      return next;
    });
    if (key === "data_source" || key === "data_table" || key === "data_key") {
      setDataPreview(null);
    }
  };

  const resetForm = () => {
    setEditingProductId(null);
    setEditingProductSource(null);
    setForm(EMPTY_FORM);
    setDataPreview(null);
  };

  const openAddForm = () => {
    resetForm();
    setStatus(null);
    setError(null);
    setIsFormOpen(true);
  };

  const closeForm = () => {
    resetForm();
    setIsFormOpen(false);
  };

  const openImport = () => {
    setImportFile(null);
    setImportPreview(null);
    setStatus(null);
    setError(null);
    setIsImportOpen(true);
  };

  const closeImport = () => {
    setImportFile(null);
    setImportPreview(null);
    setIsImportOpen(false);
  };

  const downloadImportTemplate = async () => {
    setError(null);
    try {
      const blob = await api.downloadPlatformProductImportTemplate();
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "edia_product_import_template.csv";
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
      setStatus(tr("Import template downloaded.", "导入模板已下载。"));
    } catch (err) {
      setError(err instanceof Error ? err.message : tr("Failed to download template", "下载模板失败"));
    }
  };

  const previewImportFile = async () => {
    if (!importFile) {
      setError(tr("Choose a CSV or Excel file first.", "请先选择 CSV 或 Excel 文件。"));
      return;
    }
    setIsImporting(true);
    setError(null);
    setStatus(null);
    try {
      const preview = await api.previewPlatformProductImport(importFile);
      setImportPreview(preview);
      setStatus(tr("Import file validated.", "导入文件已校验。"));
    } catch (err) {
      setImportPreview(null);
      setError(err instanceof Error ? err.message : tr("Failed to preview import file", "导入预览失败"));
    } finally {
      setIsImporting(false);
    }
  };

  const commitImportFile = async () => {
    if (!importFile || !importPreview || importPreview.valid_rows <= 0) return;
    setIsImporting(true);
    setError(null);
    setStatus(null);
    try {
      const result = await api.importPlatformProducts(importFile);
      setStatus(tr(
        `Imported ${result.imported_rows} product(s).`,
        `已导入 ${result.imported_rows} 个产品。`,
      ));
      closeImport();
      await loadProducts();
    } catch (err) {
      setError(err instanceof Error ? err.message : tr("Failed to import products", "导入产品失败"));
    } finally {
      setIsImporting(false);
    }
  };

  const startEdit = (product: CatalogProduct) => {
    setEditingProductId(product.id);
    setEditingProductSource(product.source);
    setForm(toForm(product));
    setDataPreview(null);
    setStatus(null);
    setError(null);
    setIsFormOpen(true);
  };

  const buildPayload = (): SavePlatformProductRequest => {
    const displayIn: string[] = [];
    if (form.showDashboard) displayIn.push("dashboard");
    if (form.showReports) displayIn.push("reports");
    return {
      name_en: form.name_en.trim() || undefined,
      name_cn: form.name_cn.trim() || undefined,
      product_type: form.product_type,
      unit: form.unit.trim() || "CNY/ton",
      aliases: splitAliases(form.aliases),
      data_table: form.data_table.trim() || undefined,
      data_key: form.data_key.trim() || undefined,
      description: form.description.trim() || undefined,
      display_in: displayIn,
    };
  };

  const saveProduct = async () => {
    const payload = buildPayload();
    if ((!payload.name_en && !payload.name_cn) || !payload.description) {
      setError(tr("At least one product name and description are required.", "至少需要填写一个产品名称和描述。"));
      return;
    }

    setIsSaving(true);
    setError(null);
    setStatus(null);
    try {
      if (editingProductId) {
        if (editingProductSource === "system") {
          await api.savePlatformProduct({ ...payload, id: editingProductId });
          setHiddenSystemProductIds((current) => {
            const next = current.filter((id) => id !== editingProductId);
            writeHiddenSystemProducts(next);
            return next;
          });
          setStatus(tr("Built-in product customized.", "内置产品已自定义更新。"));
        } else {
          await api.updatePlatformProduct(editingProductId, payload);
          setStatus(tr("Product updated.", "产品已更新。"));
        }
      } else {
        await api.savePlatformProduct(payload);
        setStatus(tr("Product added.", "产品已新增。"));
      }
      resetForm();
      setIsFormOpen(false);
      await loadProducts();
    } catch (err) {
      setError(err instanceof Error ? err.message : tr("Failed to save product", "保存产品失败"));
    } finally {
      setIsSaving(false);
    }
  };

  const previewDataMapping = async () => {
    const dataTable = form.data_table.trim();
    const dataKey = form.data_key.trim();
    if (!dataTable || !dataKey) {
      setError(tr("Data table and data key are required for preview.", "预览需要填写数据表和数据键。"));
      return;
    }

    setIsPreviewingData(true);
    setError(null);
    setStatus(null);
    try {
      const preview = await api.previewPlatformProductDataMapping({
        data_table: dataTable,
        data_key: dataKey,
      });
      setDataPreview(preview);
    } catch (err) {
      setDataPreview(null);
      setError(err instanceof Error ? err.message : tr("Failed to preview data mapping", "预览数据映射失败"));
    } finally {
      setIsPreviewingData(false);
    }
  };

  const deleteProduct = async (product: CatalogProduct) => {
    const confirmed = window.confirm(
      product.source === "system"
        ? tr(
          `Remove ${product.name_en} from this catalog view?`,
          `确认从产品目录视图中移除 ${product.name_cn}？`,
        )
        : tr(
          `Delete ${product.name_en}? This removes the custom catalog entry.`,
          `确认删除 ${product.name_cn}？这会删除自定义产品目录记录。`,
        ),
    );
    if (!confirmed) return;

    setIsSaving(true);
    setError(null);
    setStatus(null);
    try {
      if (product.source === "system") {
        setHiddenSystemProductIds((current) => {
          const next = [...current, product.id];
          writeHiddenSystemProducts(next);
          return next;
        });
        if (editingProductId === product.id) resetForm();
        setStatus(tr("Built-in product removed from this catalog view.", "内置产品已从目录视图中移除。"));
      } else {
        await api.deletePlatformProduct(product.id);
        if (editingProductId === product.id) resetForm();
        setStatus(tr("Product deleted.", "产品已删除。"));
        await loadProducts();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : tr("Failed to delete product", "删除产品失败"));
    } finally {
      setIsSaving(false);
    }
  };

  if (!currentUserIsAdmin) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white p-5 text-sm text-slate-600">
        {tr("Product catalog management is admin-only.", "产品目录管理仅管理员可用。")}
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div className="rounded-xl border border-slate-200 bg-white p-5 md:p-6">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <h2 className="text-xl font-semibold text-slate-900">
              {tr("Product Catalog", "产品目录管理")}
            </h2>
            <p className="mt-1 max-w-3xl text-sm text-slate-500">
              {tr(
                "Add and maintain user-defined products for the upstream, midstream, and downstream modules.",
                "新增并维护上游、中游、下游模块中的用户自定义产品。",
              )}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={downloadImportTemplate}
              className="inline-flex items-center justify-center gap-2 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
            >
              <Download size={16} />
              {tr("Template", "下载模板")}
            </button>
            <button
              type="button"
              onClick={openImport}
              className="inline-flex items-center justify-center gap-2 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
            >
              <Upload size={16} />
              {tr("Import", "导入")}
            </button>
            <button
              type="button"
              onClick={openAddForm}
              className="inline-flex items-center justify-center gap-2 rounded-lg bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-800"
            >
              <Plus size={16} />
              {tr("Add Product", "新增产品")}
            </button>
            <button
              type="button"
              onClick={loadProducts}
              disabled={isLoading}
              className="inline-flex items-center justify-center gap-2 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:bg-slate-100 disabled:text-slate-400"
            >
              <RefreshCw size={16} className={isLoading ? "animate-spin" : ""} />
              {tr("Refresh", "刷新")}
            </button>
          </div>
        </div>

        <div className="mt-5 grid grid-cols-1 gap-3 md:grid-cols-3">
          {MODULES.map((productModule) => (
            <button
              key={productModule}
              type="button"
              onClick={() => setModuleFilter(productModule)}
              className="rounded-lg border border-slate-200 bg-slate-50 p-4 text-left hover:bg-white"
            >
              <div className="text-xs font-medium uppercase tracking-wide text-slate-500">
                {labels[productModule]}
              </div>
              <div className="mt-2 text-2xl font-semibold text-slate-900">
                {stats[productModule]}
              </div>
            </button>
          ))}
        </div>
      </div>

      {(status || error) && (
        <div
          className={
            "rounded-lg border px-4 py-3 text-sm " +
            (error
              ? "border-rose-200 bg-rose-50 text-rose-700"
              : "border-emerald-200 bg-emerald-50 text-emerald-800")
          }
        >
          {error || status}
        </div>
      )}

      {isImportOpen && (
        <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/45 p-4 sm:p-6">
          <section className="max-h-[calc(100vh-3rem)] w-full max-w-4xl overflow-y-auto rounded-xl border border-slate-200 bg-white p-5 shadow-2xl">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h3 className="text-base font-semibold text-slate-900">
                  {tr("Import Products", "导入产品")}
                </h3>
                <p className="mt-1 text-xs text-slate-500">
                  {tr(
                    "Upload the standard CSV or Excel template, review validation results, then import valid rows.",
                    "上传标准 CSV 或 Excel 模板，检查校验结果后导入有效行。",
                  )}
                </p>
              </div>
              <button
                type="button"
                onClick={closeImport}
                className="rounded-lg border border-slate-200 p-2 text-slate-500 hover:bg-slate-50"
                title={tr("Close", "关闭")}
              >
                <X size={16} />
              </button>
            </div>

            <div className="mt-5 grid gap-4 lg:grid-cols-[minmax(0,1fr)_220px]">
              <label className="block text-sm">
                <span className="font-medium text-slate-700">{tr("Import File", "导入文件")}</span>
                <input
                  type="file"
                  accept=".csv,.xlsx,.xls"
                  onChange={(event) => {
                    setImportFile(event.target.files?.[0] ?? null);
                    setImportPreview(null);
                  }}
                  className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none file:mr-3 file:rounded-md file:border-0 file:bg-slate-100 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-slate-700 focus:border-slate-400"
                />
              </label>
              <div className="flex flex-col gap-2 lg:pt-6">
                <button
                  type="button"
                  onClick={downloadImportTemplate}
                  className="inline-flex items-center justify-center gap-2 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
                >
                  <Download size={16} />
                  {tr("Download Template", "下载模板")}
                </button>
                <button
                  type="button"
                  onClick={previewImportFile}
                  disabled={!importFile || isImporting}
                  className="inline-flex items-center justify-center gap-2 rounded-lg bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:bg-slate-200 disabled:text-slate-500"
                >
                  <Upload size={16} />
                  {isImporting ? tr("Checking...", "校验中...") : tr("Preview Import", "预览导入")}
                </button>
              </div>
            </div>

            <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600">
              {tr(
                "Required: description and at least one product name. Module must be upstream, midstream, or downstream. Use display_in = dashboard;reports to surface valid mappings in Dashboard and Report Builder.",
                "必填：描述，以及至少一个产品名称。模块必须为 upstream、midstream 或 downstream。display_in 填 dashboard;reports 可在映射有效时显示到仪表盘和报告构建器。",
              )}
            </div>

            {importPreview && (
              <div className="mt-5 space-y-3">
                <div className="grid grid-cols-3 gap-3 text-sm">
                  <div className="rounded-lg border border-slate-200 p-3">
                    <div className="text-xs text-slate-500">{tr("Rows", "总行数")}</div>
                    <div className="mt-1 text-lg font-semibold text-slate-900">{importPreview.total_rows}</div>
                  </div>
                  <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-3">
                    <div className="text-xs text-emerald-700">{tr("Valid", "有效")}</div>
                    <div className="mt-1 text-lg font-semibold text-emerald-800">{importPreview.valid_rows}</div>
                  </div>
                  <div className="rounded-lg border border-rose-200 bg-rose-50 p-3">
                    <div className="text-xs text-rose-700">{tr("Invalid", "无效")}</div>
                    <div className="mt-1 text-lg font-semibold text-rose-800">{importPreview.invalid_rows}</div>
                  </div>
                </div>

                <div className="max-h-80 overflow-auto rounded-lg border border-slate-200">
                  <table className="w-full min-w-[900px] text-left text-xs">
                    <thead className="sticky top-0 bg-slate-50 font-semibold uppercase tracking-wide text-slate-500">
                      <tr>
                        <th className="px-3 py-2">{tr("Row", "行")}</th>
                        <th className="px-3 py-2">{tr("Product", "产品")}</th>
                        <th className="px-3 py-2">{tr("Module", "模块")}</th>
                        <th className="px-3 py-2">{tr("Data", "数据")}</th>
                        <th className="px-3 py-2">{tr("Status", "状态")}</th>
                        <th className="px-3 py-2">{tr("Messages", "消息")}</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100 bg-white">
                      {importPreview.rows.map((row) => (
                        <tr key={`${row.row_number}-${row.product_id}`}>
                          <td className="px-3 py-2 align-top font-mono text-slate-500">{row.row_number}</td>
                          <td className="px-3 py-2 align-top">
                            <div className="font-medium text-slate-900">{language === "en" ? row.name_en || row.name_cn : row.name_cn || row.name_en}</div>
                            <div className="font-mono text-slate-400">{row.product_id || "-"}</div>
                            {row.will_update && <div className="mt-1 text-amber-700">{tr("Will update existing product", "将更新已有产品")}</div>}
                          </td>
                          <td className="px-3 py-2 align-top text-slate-600">{labels[normalizeModule(row.product_type)]}</td>
                          <td className="px-3 py-2 align-top text-slate-600">
                            <div>{row.data_table || "-"}</div>
                            <code>{row.data_key || "-"}</code>
                            {row.data_row_count !== null && row.data_row_count !== undefined && (
                              <div className={row.data_available ? "text-emerald-700" : "text-amber-700"}>
                                {row.data_available ? tr("Data found", "找到数据") : tr("No data", "无数据")}: {row.data_row_count}
                              </div>
                            )}
                          </td>
                          <td className="px-3 py-2 align-top">
                            <span className={row.valid ? "rounded-full bg-emerald-50 px-2 py-1 font-medium text-emerald-700" : "rounded-full bg-rose-50 px-2 py-1 font-medium text-rose-700"}>
                              {row.valid ? tr("Valid", "有效") : tr("Invalid", "无效")}
                            </span>
                          </td>
                          <td className="px-3 py-2 align-top text-slate-600">
                            {[...row.errors, ...row.warnings].length ? (
                              <div className="space-y-1">
                                {row.errors.map((message) => <div key={message} className="text-rose-700">{message}</div>)}
                                {row.warnings.map((message) => <div key={message} className="text-amber-700">{message}</div>)}
                              </div>
                            ) : (
                              <span className="text-slate-400">-</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                <div className="flex flex-wrap justify-end gap-2">
                  <button
                    type="button"
                    onClick={closeImport}
                    className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
                  >
                    {tr("Cancel", "取消")}
                  </button>
                  <button
                    type="button"
                    onClick={commitImportFile}
                    disabled={isImporting || importPreview.valid_rows <= 0}
                    className="inline-flex items-center gap-2 rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:bg-slate-200 disabled:text-slate-500"
                  >
                    <CheckCircle2 size={16} />
                    {isImporting ? tr("Importing...", "导入中...") : tr("Import Valid Rows", "导入有效行")}
                  </button>
                </div>
              </div>
            )}
          </section>
        </div>
      )}

      {isFormOpen && (
        <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/45 p-4 sm:p-6">
          <section className="max-h-[calc(100vh-3rem)] w-full max-w-md overflow-y-auto rounded-xl border border-slate-200 bg-white p-5 shadow-2xl">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h3 className="text-base font-semibold text-slate-900">
                  {editingProductId ? tr("Edit Product", "编辑产品") : tr("Add Product", "新增产品")}
                </h3>
                <p className="mt-1 text-xs text-slate-500">
                  {tr(
                    "Descriptions teach EDIA what the product is and where it belongs.",
                    "产品描述用于告诉 EDIA 产品含义、用途和所属产业链位置。",
                  )}
                </p>
              </div>
              <button
                type="button"
                onClick={closeForm}
                className="rounded-lg border border-slate-200 p-2 text-slate-500 hover:bg-slate-50"
                title={tr("Close", "关闭")}
              >
                <X size={16} />
              </button>
            </div>

            <div className="mt-5 space-y-4">
              <div className="grid grid-cols-1 gap-3">
                <label className="block text-sm">
                  <span className="font-medium text-slate-700">{tr("Chinese Name", "中文名称")}</span>
                  <input
                    value={form.name_cn}
                    onChange={(event) => updateForm("name_cn", event.target.value)}
                    className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-slate-400"
                    placeholder={tr("e.g. 氢化碳五树脂", "例如：氢化碳五树脂")}
                  />
                </label>
                <label className="block text-sm">
                  <span className="font-medium text-slate-700">{tr("English Name", "英文名称")}</span>
                  <input
                    value={form.name_en}
                    onChange={(event) => updateForm("name_en", event.target.value)}
                    className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-slate-400"
                    placeholder={tr("e.g. Hydrogenated C5 Resin", "例如：Hydrogenated C5 Resin")}
                  />
                </label>
              </div>
              <p className="-mt-2 text-xs text-slate-500">
                {tr("Enter at least one product name. EDIA will use it for the missing language if needed.", "至少填写一个产品名称；如另一种语言为空，EDIA 会使用已填写的名称。")}
              </p>

              <div className="grid grid-cols-1 gap-3">
                <label className="block text-sm">
                  <span className="font-medium text-slate-700">{tr("Module", "所属模块")}</span>
                  <select
                    value={form.product_type}
                    onChange={(event) => updateForm("product_type", event.target.value as ProductModule)}
                    className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-slate-400"
                  >
                    {MODULES.map((productModule) => (
                      <option key={productModule} value={productModule}>
                        {labels[productModule]}
                      </option>
                    ))}
                  </select>
                  <span className="mt-1 block text-xs text-slate-500">
                    {tr("Module is the product's value-chain position. ERP is selected below as a data source.", "模块表示产品在产业链中的位置；ERP 请在下方数据来源中选择。")}
                  </span>
                </label>
                <label className="block text-sm">
                  <span className="font-medium text-slate-700">{tr("Unit", "单位")}</span>
                  <input
                    value={form.unit}
                    onChange={(event) => updateForm("unit", event.target.value)}
                    className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-slate-400"
                  />
                </label>
              </div>

              <label className="block text-sm">
                <span className="font-medium text-slate-700">{tr("Aliases", "别名")}</span>
                <input
                  value={form.aliases}
                  onChange={(event) => updateForm("aliases", event.target.value)}
                  className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-slate-400"
                  placeholder={tr("Comma or semicolon separated", "使用逗号或分号分隔")}
                />
              </label>

              <div className="grid grid-cols-1 gap-3">
                <label className="block text-sm">
                  <span className="font-medium text-slate-700">{tr("Data Source", "数据来源")}</span>
                  <select
                    value={form.data_source}
                    onChange={(event) => updateForm("data_source", event.target.value as ProductDataSource)}
                    className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-slate-400"
                  >
                    <option value="market_prices">{dataSourceLabels.market_prices}</option>
                    <option value="erp">ERP</option>
                    <option value="other">{dataSourceLabels.other}</option>
                  </select>
                </label>
                <label className="block text-sm">
                  <span className="font-medium text-slate-700">{tr("Data Table", "数据表")}</span>
                  <input
                    value={form.data_table}
                    onChange={(event) => updateForm("data_table", event.target.value)}
                    className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 font-mono text-sm outline-none focus:border-slate-400"
                    placeholder={form.data_source === "erp" ? "sale_erp_v_*_data" : "market_prices"}
                  />
                </label>
                <label className="block text-sm">
                  <span className="font-medium text-slate-700">{tr("Data Key", "数据键")}</span>
                  <input
                    value={form.data_key}
                    onChange={(event) => updateForm("data_key", event.target.value)}
                    className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 font-mono text-sm outline-none focus:border-slate-400"
                    placeholder={form.data_source === "erp" ? tr("ERP product or material key", "ERP 产品或物料键") : "hydrogenated_c5_resin"}
                  />
                </label>
              </div>

              <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between xl:flex-col xl:items-stretch">
                  <div>
                    <div className="text-sm font-medium text-slate-700">{tr("Data Preview", "数据预览")}</div>
                    <p className="mt-1 text-xs text-slate-500">
                      {tr("Check whether this mapping has usable price history before saving.", "保存前检查该映射是否存在可用价格历史。")}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={previewDataMapping}
                    disabled={isPreviewingData || !form.data_table.trim() || !form.data_key.trim()}
                    className="inline-flex items-center justify-center gap-2 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:bg-slate-100 disabled:text-slate-400"
                  >
                    <Database size={16} />
                    {isPreviewingData ? tr("Checking...", "检查中...") : tr("Preview Data", "预览数据")}
                  </button>
                </div>
                {dataPreview && (
                  <div className={`mt-3 rounded-lg border p-3 text-xs ${dataPreview.available ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-amber-200 bg-amber-50 text-amber-800"}`}>
                    <div className="flex items-center gap-2 font-medium">
                      {dataPreview.available ? <CheckCircle2 size={15} /> : <X size={15} />}
                      {dataPreview.available ? tr("Mapping available", "映射可用") : tr("No usable data found", "未找到可用数据")}
                    </div>
                    <div className="mt-2 grid grid-cols-1 gap-1 sm:grid-cols-2 xl:grid-cols-1">
                      <span>{tr("Rows", "行数")}: {dataPreview.row_count}</span>
                      {dataPreview.latest_date && <span>{tr("Latest date", "最新日期")}: {dataPreview.latest_date}</span>}
                      {dataPreview.latest_price !== null && dataPreview.latest_price !== undefined && (
                        <span>{tr("Latest price", "最新价格")}: {dataPreview.latest_price}</span>
                      )}
                      {dataPreview.price_type && <span>{tr("Price type", "价格类型")}: <code>{dataPreview.price_type}</code></span>}
                    </div>
                    <div className="mt-2 text-slate-600">{dataPreview.message}</div>
                  </div>
                )}
              </div>

              <label className="block text-sm">
                <span className="font-medium text-slate-700">{tr("Description *", "描述 *")}</span>
                <textarea
                  value={form.description}
                  onChange={(event) => updateForm("description", event.target.value)}
                  rows={3}
                  className="mt-1 w-full resize-none rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-slate-400"
                  placeholder={tr(
                    "What is this product, where is it in the value chain, and what is it used for?",
                    "说明该产品是什么、处于产业链哪个环节、主要用途是什么。",
                  )}
                />
                <span className="mt-1 block text-xs text-slate-500">
                  {tr(
                    "Example: C5 downstream resin used in adhesives and road-marking paint, linked to piperylene and mixed C5 costs.",
                    "示例：C5 下游树脂，用于胶粘剂和道路标线涂料，与间戊二烯和混合碳五成本相关。",
                  )}
                </span>
              </label>

              <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                <div className="text-sm font-medium text-slate-700">{tr("Display", "显示位置")}</div>
                <div className="mt-3 space-y-2">
                  <label className="flex items-center gap-2 text-sm text-slate-700">
                    <input
                      type="checkbox"
                      checked={form.showDashboard}
                      onChange={(event) => updateForm("showDashboard", event.target.checked)}
                      className="rounded border-slate-300"
                    />
                    {tr("Dashboard", "仪表盘")}
                  </label>
                  <label className="flex items-center gap-2 text-sm text-slate-700">
                    <input
                      type="checkbox"
                      checked={form.showReports}
                      onChange={(event) => updateForm("showReports", event.target.checked)}
                      className="rounded border-slate-300"
                    />
                    {tr("Reports", "报告")}
                  </label>
                </div>
              </div>

              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={saveProduct}
                  disabled={isSaving || (!form.name_en.trim() && !form.name_cn.trim()) || !form.description.trim()}
                  className="inline-flex items-center gap-2 rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:bg-slate-200 disabled:text-slate-500"
                >
                  {editingProductId ? <CheckCircle2 size={16} /> : <Plus size={16} />}
                  {isSaving
                    ? tr("Saving...", "保存中...")
                    : editingProductId
                      ? tr("Save Changes", "保存更改")
                      : tr("Add Product", "新增产品")}
                </button>
                {editingProductId && (
                  <button
                    type="button"
                    onClick={closeForm}
                    className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
                  >
                    {tr("Cancel", "取消")}
                  </button>
                )}
              </div>
            </div>
          </section>
        </div>
      )}

      <section className="rounded-xl border border-slate-200 bg-white">
        <div className="border-b border-slate-100 p-4 md:p-5">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <h3 className="text-base font-semibold text-slate-900">
                {tr("Product List", "产品列表")}
              </h3>
              <p className="mt-1 text-xs text-slate-500">
                {tr("Each product can be edited, updated, or removed from this catalog.", "每个产品都可以在此编辑、更新或移除。")}
              </p>
            </div>
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
              <div className="relative">
                <Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                <input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  className="w-full rounded-lg border border-slate-200 py-2 pl-9 pr-3 text-sm outline-none focus:border-slate-400 sm:w-64"
                  placeholder={tr("Search products", "搜索产品")}
                />
              </div>
              <select
                value={moduleFilter}
                onChange={(event) => setModuleFilter(event.target.value as "all" | ProductModule)}
                className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-slate-400"
              >
                <option value="all">{tr("All modules", "全部模块")}</option>
                {MODULES.map((productModule) => (
                  <option key={productModule} value={productModule}>
                    {labels[productModule]}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full min-w-[1100px] text-left text-sm">
            <thead className="border-b border-slate-100 bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-4 py-3">{tr("Product", "产品")}</th>
                <th className="px-4 py-3">{tr("Module", "模块")}</th>
                <th className="px-4 py-3">{tr("Description", "描述")}</th>
                <th className="px-4 py-3">{tr("Data Mapping", "数据映射")}</th>
                <th className="px-4 py-3">{tr("Aliases", "别名")}</th>
                <th className="px-4 py-3">{tr("Display", "显示")}</th>
                <th className="px-4 py-3 text-right">{tr("Actions", "操作")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {filteredProducts.map((product) => {
                const productModule = normalizeModule(product.product_type);
                const productDataSource = inferDataSource(product.data_table);
                const primaryName = productPrimaryName(product);
                const secondaryName = productSecondaryName(product);
                const description = productDescription(product);
                return (
                  <tr key={product.id} className={editingProductId === product.id ? "bg-amber-50" : "bg-white"}>
                    <td className="px-4 py-3 align-top">
                      <div className="font-semibold text-slate-900">{primaryName}</div>
                      {secondaryName && secondaryName !== primaryName && (
                        <div className="text-xs text-slate-500">{secondaryName}</div>
                      )}
                      <div className="mt-1 flex flex-wrap items-center gap-2 text-xs">
                        <span className="font-mono text-slate-400">{product.id}</span>
                        <span className={
                          "rounded-full border px-2 py-0.5 font-medium " +
                          (product.source === "system"
                            ? "border-slate-200 bg-slate-50 text-slate-600"
                            : "border-blue-100 bg-blue-50 text-blue-700")
                        }>
                          {product.source === "system" ? tr("Built-in", "内置") : tr("Custom", "自定义")}
                        </span>
                      </div>
                    </td>
                    <td className="px-4 py-3 align-top">
                      <span className="inline-flex rounded-full border border-slate-200 bg-slate-50 px-2 py-1 text-xs font-medium text-slate-700">
                        {labels[productModule]}
                      </span>
                    </td>
                    <td className="max-w-[300px] px-4 py-3 align-top text-xs leading-relaxed text-slate-600">
                      {description || <span className="text-slate-400">-</span>}
                    </td>
                    <td className="px-4 py-3 align-top text-xs text-slate-600">
                      {product.data_table || product.data_key ? (
                        <div className="space-y-1">
                          <span className={
                            "inline-flex rounded-full border px-2 py-0.5 text-[11px] font-medium " +
                            (productDataSource === "erp"
                              ? "border-amber-200 bg-amber-50 text-amber-700"
                              : productDataSource === "market_prices"
                                ? "border-sky-200 bg-sky-50 text-sky-700"
                                : "border-slate-200 bg-slate-50 text-slate-600")
                          }>
                            {dataSourceLabels[productDataSource]}
                          </span>
                          {product.data_table && (
                            <div className="flex items-center gap-1.5">
                              <Database size={13} className="text-slate-400" />
                              <code>{product.data_table}</code>
                            </div>
                          )}
                          {product.data_key && <code>{product.data_key}</code>}
                        </div>
                      ) : (
                        <span className="text-slate-400">-</span>
                      )}
                    </td>
                    <td className="max-w-[240px] px-4 py-3 align-top text-xs text-slate-600">
                      {product.aliases.length ? product.aliases.join(", ") : <span className="text-slate-400">-</span>}
                    </td>
                    <td className="px-4 py-3 align-top">
                      <div className="flex flex-wrap gap-1">
                        <span className="rounded border border-blue-100 bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700">
                          Chat
                        </span>
                        {product.display_in.includes("dashboard") && (
                          <span className="rounded border border-emerald-100 bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700">
                            {tr("Dashboard", "仪表盘")}
                          </span>
                        )}
                        {product.display_in.includes("reports") && (
                          <span className="rounded border border-violet-100 bg-violet-50 px-2 py-0.5 text-xs font-medium text-violet-700">
                            {tr("Reports", "报告")}
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3 align-top">
                      <div className="flex justify-end gap-2">
                        <button
                          type="button"
                          onClick={() => startEdit(product)}
                          className="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
                        >
                          <Pencil size={13} />
                          {tr("Edit", "编辑")}
                        </button>
                        <button
                          type="button"
                          onClick={() => deleteProduct(product)}
                          disabled={isSaving}
                          className="inline-flex items-center gap-1 rounded-lg border border-rose-200 px-2.5 py-1.5 text-xs font-medium text-rose-700 hover:bg-rose-50 disabled:opacity-60"
                        >
                          <Trash2 size={13} />
                          {tr("Remove", "移除")}
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
              {!isLoading && filteredProducts.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-4 py-10 text-center text-sm text-slate-500">
                    {tr("No products match the current filters.", "当前筛选条件下无产品。")}
                  </td>
                </tr>
              )}
              {isLoading && (
                <tr>
                  <td colSpan={7} className="px-4 py-10 text-center text-sm text-slate-500">
                    {tr("Loading products...", "产品加载中...")}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
};