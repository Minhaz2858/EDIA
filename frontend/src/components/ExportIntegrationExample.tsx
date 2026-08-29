import { useState } from "react";

type ExportFormat = "html" | "pdf" | "docx" | "pptx";

interface ReportData {
  title: string;
  blocks: unknown[];
}

interface ReportShape {
  title: string;
  kpis?: Array<{ label: string; value: string | number; change?: number; changePct?: number }>;
  priceData?: {
    title?: string;
    columns: Array<{ label: string; key: string }>;
    rows: Array<Record<string, unknown>>;
  };
  charts?: Array<{ title: string; imageUrl: string }>;
  content?: string;
}

export const ExportButton = ({ reportData }: { reportData: ReportData }) => {
  const [exporting, setExporting] = useState(false);
  const [format, setFormat] = useState<ExportFormat>("html");

  const handleExport = async () => {
    setExporting(true);
    try {
      const response = await fetch("/api/reports/export-professional", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        credentials: "include",
        body: JSON.stringify({
          title: reportData.title,
          generated_at: new Date().toISOString(),
          format,
          blocks: reportData.blocks,
        }),
      });

      if (!response.ok) {
        throw new Error(`Export failed: ${response.statusText}`);
      }

      // Download the file
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${reportData.title}_${Date.now()}.${format}`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      window.URL.revokeObjectURL(url);

      console.log("Export successful");
    } catch (error: unknown) {
      const errorMessage = error instanceof Error ? error.message : "Unknown error";
      console.error("Export failed:", error);
      alert(`Export failed: ${errorMessage}`);
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="flex items-center gap-2">
      <select
        value={format}
        onChange={(e) => setFormat(e.target.value as ExportFormat)}
        className="rounded border border-slate-300 bg-white px-3 py-2 text-sm"
      >
        <option value="html">HTML</option>
        <option value="pdf">PDF</option>
        <option value="docx">Word</option>
        <option value="pptx">PowerPoint</option>
      </select>

      <button
        onClick={handleExport}
        disabled={exporting}
        className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-60"
      >
        {exporting ? "导出中..." : `导出${format.toUpperCase()}`}
      </button>
    </div>
  );
};

/**
 * Example: Convert existing report to export blocks
 */
export const convertReportToBlocks = (report: ReportShape) => {
  const blocks: Array<Record<string, unknown>> = [];

  // Add KPI cards from dashboard data
  if (report.kpis && report.kpis.length > 0) {
    blocks.push({
      type: 'kpi_cards',
      cards: report.kpis.map((kpi) => ({
        label: kpi.label,
        value: kpi.value,
        delta: kpi.change ?? 0,
        delta_pct: kpi.changePct ?? 0,
      })),
    });
  }

  // Add price table
  if (report.priceData) {
    blocks.push({
      type: 'data_table',
      title: report.priceData.title || "市场价格",
      columns: report.priceData.columns,
      rows: report.priceData.rows,
    });
  }

  // Add charts
  if (report.charts && report.charts.length > 0) {
    if (report.charts.length >= 2) {
      // Side by side
      blocks.push({
        type: 'chart_row',
        charts: report.charts.slice(0, 2).map(chart => ({
          type: 'chart',
          title: chart.title,
          chart_url: chart.imageUrl,
        })),
      });
    } else {
      // Single chart
      report.charts.forEach((chart) => {
        blocks.push({
          type: "chart",
          title: chart.title,
          chart_url: chart.imageUrl,
        });
      });
    }
  }

  // Add text content
  if (report.content) {
    blocks.push({
      type: "text",
      content: report.content,
    });
  }

  return blocks;
};

/**
 * Example: Add export to existing ReportView component
 */
export const ExportIntegrationReportView = ({ report }: { report: ReportShape }) => {
  const reportData = {
    title: report.title,
    blocks: convertReportToBlocks(report),
  };

  return (
    <div className="report-view">
      <div className="report-header">
        <h1>{report.title}</h1>
        <ExportButton reportData={reportData} />
      </div>

      <div className="report-content">
        <pre className="rounded bg-slate-50 p-3 text-xs text-slate-700">
          {JSON.stringify(reportData, null, 2)}
        </pre>
      </div>
    </div>
  );
};

/**
 * Example: Create a market price report from API data
 */
export const createMarketPriceReport = async () => {
  // Fetch market data
  const response = await fetch('/api/market/prices');
  const data = await response.json();

  // Structure as blocks
  const reportData = {
    title: '市场价格周报',
    blocks: [
      {
        type: 'kpi_cards',
        cards: [
          {
            label: 'WTI',
            value: data.wti.price.toFixed(2),
            delta: data.wti.change,
            delta_pct: `${data.wti.changePct.toFixed(2)}%`,
          },
          {
            label: '布伦特',
            value: data.brent.price.toFixed(2),
            delta: data.brent.change,
            delta_pct: `${data.brent.changePct.toFixed(2)}%`,
          },
        ],
      },
      {
        type: 'data_table',
        title: '单位：元/吨',
        columns: [
          { label: '地区', key: 'region' },
          { label: '生产企业', key: 'company' },
          { label: '92#', key: 'price_92' },
          { label: '涨跌', key: 'delta_92' },
          { label: '95#', key: 'price_95' },
          { label: '涨跌', key: 'delta_95' },
        ],
        rows: data.regionalPrices,
      },
    ],
  };

  // Export
  const response2 = await fetch('/api/reports/export-professional', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ ...reportData, format: 'html' }),
  });

  const blob = await response2.blob();
  const url = window.URL.createObjectURL(blob);
  window.open(url, '_blank');
};
