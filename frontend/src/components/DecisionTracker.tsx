import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Clock,
  Pencil,
  PenSquare,
  Trash2,
  XCircle,
} from "lucide-react";
import React, { useCallback, useEffect, useState } from "react";
import { useLanguage } from "../lib/language";
import {
  api,
  AuthUser,
  DecisionAccuracyStats,
  DecisionLogEntry,
  DecisionOutcome,
} from "../services/api";

// ─── helpers ──────────────────────────────────────────────────────────────────

function fmt(n: number | null | undefined, decimals = 0): string {
  if (n == null) return "—";
  return n.toLocaleString("zh-CN", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function normalizePriceUnit(unit: string | null | undefined): string | null {
  const raw = String(unit || "").trim().toLowerCase();
  if (!raw) return null;
  if (raw.includes("usd") || raw.includes("$") || raw.includes("美元")) {
    return raw.includes("bbl") || raw.includes("barrel") ? "USD/bbl" : "USD/ton";
  }
  if (
    raw.includes("cny") ||
    raw.includes("rmb") ||
    raw.includes("¥") ||
    raw.includes("￥") ||
    raw.includes("人民币") ||
    raw.includes("元")
  ) {
    return "CNY/ton";
  }
  return String(unit || "").trim();
}

function fmtPrice(
  value: number | null | undefined,
  unit: string | null | undefined,
  decimals = 0,
): string {
  if (value == null) return "—";
  const normalized = normalizePriceUnit(unit);
  if (normalized?.startsWith("USD/")) return `$${fmt(value, decimals)}`;
  if (normalized === "CNY/ton") return `¥${fmt(value, decimals)}`;
  return normalized ? `${fmt(value, decimals)} ${normalized}` : fmt(value, decimals);
}

function unitSuffix(unit: string | null | undefined): string {
  const normalized = normalizePriceUnit(unit);
  if (normalized === "CNY/ton") return "¥/t";
  if (normalized === "USD/ton") return "$/t";
  if (normalized === "USD/bbl") return "$/bbl";
  return normalized || "";
}

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const OUTCOME_LABELS: Record<DecisionOutcome, { en: string; zh: string }> = {
  pending: { en: "Pending", zh: "待定" },
  deal: { en: "Deal", zh: "成交" },
  no_deal: { en: "No Deal", zh: "未成交" },
  above_range: { en: "Above Range", zh: "高于区间" },
  below_range: { en: "Below Range", zh: "低于区间" },
};

const STATUS_BADGE: Record<
  string,
  { label: { en: string; zh: string }; classes: string }
> = {
  pending: {
    label: { en: "Pending", zh: "待执行" },
    classes: "bg-amber-50 text-amber-700 border border-amber-200",
  },
  executed: {
    label: { en: "Executed", zh: "已执行" },
    classes: "bg-blue-50 text-blue-700 border border-blue-200",
  },
  closed: {
    label: { en: "Closed", zh: "已关闭" },
    classes: "bg-slate-50 text-slate-600 border border-slate-200",
  },
};

// ─── sub-components ───────────────────────────────────────────────────────────

const StatusBadge: React.FC<{ status: string; lang: "en" | "zh" }> = ({
  status,
  lang,
}) => {
  const cfg = STATUS_BADGE[status] ?? STATUS_BADGE.pending;
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${cfg.classes}`}
    >
      {cfg.label[lang]}
    </span>
  );
};

const OutcomeBadge: React.FC<{
  outcome: DecisionOutcome;
  lang: "en" | "zh";
}> = ({ outcome, lang }) => {
  const label = OUTCOME_LABELS[outcome]?.[lang] ?? outcome;
  const colorMap: Record<string, string> = {
    deal: "text-emerald-700 bg-emerald-50 border-emerald-200",
    no_deal: "text-red-600 bg-red-50 border-red-200",
    above_range: "text-orange-600 bg-orange-50 border-orange-200",
    below_range: "text-purple-600 bg-purple-50 border-purple-200",
    pending: "text-slate-500 bg-slate-50 border-slate-200",
  };
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium border ${colorMap[outcome] ?? colorMap.pending}`}
    >
      {label}
    </span>
  );
};

// ─── execute form ─────────────────────────────────────────────────────────────

const ExecuteForm: React.FC<{
  entryId: string;
  currentUser: AuthUser | null;
  onDone: (entry: DecisionLogEntry) => void;
  lang: "en" | "zh";
}> = ({ entryId, currentUser, onDone, lang }) => {
  const [price, setPrice] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const t =
    lang === "en"
      ? {
        label: "Executed Price (¥/t)",
        btn: "Mark Executed",
        cancel: "Cancel",
      }
      : { label: "执行报价 (¥/吨)", btn: "标记已执行", cancel: "取消" };

  const handle = async () => {
    const p = parseFloat(price);
    if (isNaN(p) || p <= 0) {
      setErr(lang === "en" ? "Enter a valid price" : "请输入有效价格");
      return;
    }
    setSaving(true);
    setErr(null);
    try {
      const updated = await api.markDecisionExecuted(
        entryId,
        p,
        currentUser?.username,
      );
      onDone(updated);
    } catch {
      setErr(lang === "en" ? "Save failed" : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mt-3 p-3 rounded-lg bg-blue-50 border border-blue-100 space-y-2">
      <label className="block text-xs font-medium text-blue-800">
        {t.label}
      </label>
      <div className="flex gap-2">
        <input
          type="number"
          value={price}
          onChange={(e) => setPrice(e.target.value)}
          placeholder="e.g. 7800"
          className="flex-1 rounded border border-blue-200 bg-white px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
        />
        <button
          onClick={handle}
          disabled={saving}
          className="rounded-md bg-blue-600 px-3 py-1 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50 transition-colors"
        >
          {saving ? "…" : t.btn}
        </button>
      </div>
      {err && <p className="text-xs text-red-600">{err}</p>}
    </div>
  );
};

// ─── outcome form ──────────────────────────────────────────────────────────────

const OutcomeForm: React.FC<{
  entryId: string;
  onDone: (entry: DecisionLogEntry) => void;
  lang: "en" | "zh";
}> = ({ entryId, onDone, lang }) => {
  const [outcome, setOutcome] = useState<DecisionOutcome>("deal");
  const [reason, setReason] = useState("");
  const [competitorPrice, setCompetitorPrice] = useState("");
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const t =
    lang === "en"
      ? {
        outcomeLabel: "Outcome",
        reasonLabel: "Reason",
        reasonPh: "e.g. Client accepted",
        compLabel: "Competitor Price (¥/t)",
        compPh: "optional",
        noteLabel: "Note",
        notePh: "optional",
        btn: "Save Outcome",
      }
      : {
        outcomeLabel: "成交结果",
        reasonLabel: "原因",
        reasonPh: "例：客户接受报价",
        compLabel: "竞争对手价格 (¥/吨)",
        compPh: "选填",
        noteLabel: "备注",
        notePh: "选填",
        btn: "保存结果",
      };

  const handle = async () => {
    setSaving(true);
    setErr(null);
    try {
      const cp = competitorPrice ? parseFloat(competitorPrice) : undefined;
      const updated = await api.markDecisionOutcome(entryId, outcome, {
        outcome_reason: reason || undefined,
        outcome_competitor_price: cp && !isNaN(cp) ? cp : undefined,
        outcome_note: note || undefined,
      });
      onDone(updated);
    } catch {
      setErr(lang === "en" ? "Save failed" : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mt-3 p-3 rounded-lg bg-slate-50 border border-slate-200 space-y-2">
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="block text-xs font-medium text-slate-700 mb-1">
            {t.outcomeLabel}
          </label>
          <select
            value={outcome}
            onChange={(e) => setOutcome(e.target.value as DecisionOutcome)}
            className="w-full rounded border border-slate-300 bg-white px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-slate-400"
          >
            {(
              [
                "deal",
                "no_deal",
                "above_range",
                "below_range",
              ] as DecisionOutcome[]
            ).map((o) => (
              <option key={o} value={o}>
                {OUTCOME_LABELS[o][lang]}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-xs font-medium text-slate-700 mb-1">
            {t.compLabel}
          </label>
          <input
            type="number"
            value={competitorPrice}
            onChange={(e) => setCompetitorPrice(e.target.value)}
            placeholder={t.compPh}
            className="w-full rounded border border-slate-300 bg-white px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-slate-400"
          />
        </div>
      </div>
      <div>
        <label className="block text-xs font-medium text-slate-700 mb-1">
          {t.reasonLabel}
        </label>
        <input
          type="text"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder={t.reasonPh}
          className="w-full rounded border border-slate-300 bg-white px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-slate-400"
        />
      </div>
      <div>
        <label className="block text-xs font-medium text-slate-700 mb-1">
          {t.noteLabel}
        </label>
        <input
          type="text"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder={t.notePh}
          className="w-full rounded border border-slate-300 bg-white px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-slate-400"
        />
      </div>
      <div className="flex justify-end">
        <button
          onClick={handle}
          disabled={saving}
          className="rounded-md bg-slate-700 px-3 py-1 text-xs font-medium text-white hover:bg-slate-800 disabled:opacity-50 transition-colors"
        >
          {saving ? "…" : t.btn}
        </button>
      </div>
      {err && <p className="text-xs text-red-600">{err}</p>}
    </div>
  );
};

// ─── edit form ────────────────────────────────────────────────────────────────

const EditForm: React.FC<{
  entry: DecisionLogEntry;
  lang: "en" | "zh";
  onDone: (updated: DecisionLogEntry) => void;
  onCancel: () => void;
}> = ({ entry, lang, onDone, onCancel }) => {
  const [low, setLow] = useState(String(entry.ai_price_low));
  const [high, setHigh] = useState(String(entry.ai_price_high));
  const [action, setAction] = useState(entry.ai_action);
  const [execPrice, setExecPrice] = useState(
    entry.executed_price != null ? String(entry.executed_price) : "",
  );
  const [note, setNote] = useState(entry.outcome_note ?? "");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const t =
    lang === "en"
      ? {
        low: "AI Range Low (¥/t)",
        high: "AI Range High (¥/t)",
        action: "Action",
        exec: "Executed Price (¥/t)",
        note: "Note",
        save: "Save",
        cancel: "Cancel",
      }
      : {
        low: "AI 低价 (¥/吨)",
        high: "AI 高价 (¥/吨)",
        action: "建议操作",
        exec: "执行价格 (¥/吨)",
        note: "备注",
        save: "保存",
        cancel: "取消",
      };

  const handle = async () => {
    const pLow = parseFloat(low);
    const pHigh = parseFloat(high);
    if (isNaN(pLow) || isNaN(pHigh) || pLow <= 0 || pHigh <= pLow) {
      setErr(
        lang === "en" ? "Enter a valid price range" : "请输入有效价格区间",
      );
      return;
    }
    setSaving(true);
    setErr(null);
    try {
      const patch: Parameters<typeof api.updateDecisionEntry>[1] = {
        ai_price_low: pLow,
        ai_price_high: pHigh,
        ai_action: action || undefined,
        outcome_note: note !== "" ? note : undefined,
      };
      if (execPrice) {
        const ep = parseFloat(execPrice);
        if (!isNaN(ep) && ep > 0) patch.executed_price = ep;
      }
      const updated = await api.updateDecisionEntry(entry.id, patch);
      onDone(updated);
    } catch (e) {
      setErr(
        e instanceof Error
          ? e.message
          : lang === "en"
            ? "Save failed"
            : "保存失败",
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="px-4 py-3 bg-amber-50/60 border-t border-amber-100 space-y-2">
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="block text-xs font-medium text-amber-800 mb-1">
            {t.low}
          </label>
          <input
            type="number"
            value={low}
            onChange={(e) => setLow(e.target.value)}
            className="w-full rounded border border-amber-200 bg-white px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-amber-400"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-amber-800 mb-1">
            {t.high}
          </label>
          <input
            type="number"
            value={high}
            onChange={(e) => setHigh(e.target.value)}
            className="w-full rounded border border-amber-200 bg-white px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-amber-400"
          />
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="block text-xs font-medium text-amber-800 mb-1">
            {t.action}
          </label>
          <input
            type="text"
            value={action}
            onChange={(e) => setAction(e.target.value)}
            className="w-full rounded border border-amber-200 bg-white px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-amber-400"
          />
        </div>
        {entry.status !== "pending" && (
          <div>
            <label className="block text-xs font-medium text-amber-800 mb-1">
              {t.exec}
            </label>
            <input
              type="number"
              value={execPrice}
              onChange={(e) => setExecPrice(e.target.value)}
              placeholder={lang === "en" ? "optional" : "选填"}
              className="w-full rounded border border-amber-200 bg-white px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-amber-400"
            />
          </div>
        )}
      </div>
      <div>
        <label className="block text-xs font-medium text-amber-800 mb-1">
          {t.note}
        </label>
        <input
          type="text"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder={lang === "en" ? "optional" : "选填"}
          className="w-full rounded border border-amber-200 bg-white px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-amber-400"
        />
      </div>
      {err && <p className="text-xs text-red-600">{err}</p>}
      <div className="flex justify-end gap-2 pt-1">
        <button
          onClick={onCancel}
          className="rounded-md bg-white border border-slate-200 px-3 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 transition-colors"
        >
          {t.cancel}
        </button>
        <button
          onClick={handle}
          disabled={saving}
          className="rounded-md bg-amber-600 px-3 py-1 text-xs font-medium text-white hover:bg-amber-700 disabled:opacity-50 transition-colors"
        >
          {saving ? "…" : t.save}
        </button>
      </div>
    </div>
  );
};

// ─── entry row ────────────────────────────────────────────────────────────────

const EntryRow: React.FC<{
  entry: DecisionLogEntry;
  currentUser: AuthUser | null;
  lang: "en" | "zh";
  onUpdate: (updated: DecisionLogEntry) => void;
  onDelete: (id: string) => void;
}> = ({ entry, currentUser, lang, onUpdate, onDelete }) => {
  const [expanded, setExpanded] = useState(false);
  const [showExecute, setShowExecute] = useState(false);
  const [showOutcome, setShowOutcome] = useState(false);
  const [showEdit, setShowEdit] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteErr, setDeleteErr] = useState<string | null>(null);

  const t =
    lang === "en"
      ? {
        aiRange: "AI Range",
        action: "Action",
        executedAt: "Executed",
        markExecuted: "Mark Executed",
        markOutcome: "Record Outcome",
        reasoning: "AI Reasoning",
        error: "Price Error",
        inRange: "In range",
        outRange: "Out of range",
        accuracy: "Accuracy",
        confidence: "Confidence",
      }
      : {
        aiRange: "AI 报价区间",
        action: "建议操作",
        executedAt: "执行时间",
        markExecuted: "标记执行",
        markOutcome: "记录结果",
        reasoning: "AI 推理",
        error: "价格误差",
        inRange: "在区间内",
        outRange: "超出区间",
        accuracy: "精度",
        confidence: "置信度",
      };

  return (
    <div className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
      {/* Header row */}
      <div
        className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-slate-50 transition-colors"
        onClick={() => setExpanded((p) => !p)}
      >
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-medium text-sm text-slate-800 truncate">
              {entry.product_name || entry.product_id}
            </span>
            <StatusBadge status={entry.status} lang={lang} />
            {entry.outcome !== "pending" && (
              <OutcomeBadge outcome={entry.outcome} lang={lang} />
            )}
          </div>
          <div className="text-xs text-slate-500 mt-0.5">
            {fmtDate(entry.created_at)}
            {entry.source && (
              <span className="ml-2 rounded bg-slate-100 px-1 text-slate-400 text-[10px]">
                {entry.source}
              </span>
            )}
          </div>
        </div>

        <div className="text-right flex-shrink-0">
          <div className="text-sm font-mono font-semibold text-slate-700">
            {fmtPrice(entry.ai_price_low, entry.price_unit)} – {fmtPrice(entry.ai_price_high, entry.price_unit)}
          </div>
          {entry.executed_price != null && (
            <div className="text-xs text-slate-500">
              {lang === "en" ? "Exec" : "执行"}: {fmtPrice(entry.executed_price, entry.price_unit)}
            </div>
          )}
        </div>

        {/* Edit / Delete icon buttons */}
        <div
          className="flex items-center gap-0.5 flex-shrink-0"
          onClick={(e) => e.stopPropagation()}
        >
          <button
            title={lang === "en" ? "Edit entry" : "编辑"}
            onClick={() => {
              setShowEdit((p) => !p);
              setConfirmDelete(false);
            }}
            className={`p-1.5 rounded transition-colors ${showEdit ? "bg-amber-100 text-amber-700" : "text-slate-400 hover:text-amber-600 hover:bg-amber-50"}`}
          >
            <Pencil size={13} />
          </button>
          <button
            title={lang === "en" ? "Delete entry" : "删除"}
            onClick={() => {
              setConfirmDelete((p) => !p);
              setShowEdit(false);
              setDeleteErr(null);
            }}
            className={`p-1.5 rounded transition-colors ${confirmDelete ? "bg-red-100 text-red-700" : "text-slate-400 hover:text-red-600 hover:bg-red-50"}`}
          >
            <Trash2 size={13} />
          </button>
        </div>

        <div className="text-slate-400 flex-shrink-0">
          {expanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
        </div>
      </div>

      {/* Edit form — shown independently of expand */}
      {showEdit && (
        <EditForm
          entry={entry}
          lang={lang}
          onDone={(updated) => {
            setShowEdit(false);
            onUpdate(updated);
          }}
          onCancel={() => setShowEdit(false)}
        />
      )}

      {/* Delete confirmation — shown independently of expand */}
      {confirmDelete && (
        <div className="border-t border-red-100 px-4 py-2.5 bg-red-50 flex items-center justify-between gap-3 flex-wrap">
          <p className="text-xs font-medium text-red-700">
            {lang === "en"
              ? "Permanently delete this entry?"
              : "确认永久删除此条记录？"}
          </p>
          <div className="flex items-center gap-2">
            {deleteErr && (
              <span className="text-xs text-red-600">{deleteErr}</span>
            )}
            <button
              onClick={() => {
                setConfirmDelete(false);
                setDeleteErr(null);
              }}
              className="rounded-md bg-white border border-slate-200 px-2 py-1 text-xs text-slate-700 hover:bg-slate-50 transition-colors"
            >
              {lang === "en" ? "Cancel" : "取消"}
            </button>
            <button
              disabled={deleting}
              onClick={async () => {
                setDeleting(true);
                setDeleteErr(null);
                try {
                  await api.deleteDecisionEntry(entry.id);
                  onDelete(entry.id);
                } catch {
                  setDeleteErr(lang === "en" ? "Delete failed" : "删除失败");
                  setDeleting(false);
                }
              }}
              className="rounded-md bg-red-600 px-2 py-1 text-xs font-medium text-white hover:bg-red-700 disabled:opacity-50 transition-colors"
            >
              {deleting ? "…" : lang === "en" ? "Delete" : "删除"}
            </button>
          </div>
        </div>
      )}

      {/* Expanded detail */}
      {expanded && (
        <div className="border-t border-slate-100 px-4 py-3 space-y-3 bg-slate-50/50">
          <div className="grid grid-cols-2 gap-3 text-xs">
            <div>
              <span className="text-slate-500">{t.action}</span>
              <p className="font-medium text-slate-800 capitalize mt-0.5">
                {entry.ai_action}
              </p>
            </div>
            <div>
              <span className="text-slate-500">{t.confidence}</span>
              <p className="font-medium text-slate-800 mt-0.5">
                {entry.ai_confidence != null
                  ? `${(entry.ai_confidence * 100).toFixed(0)}%`
                  : "—"}
              </p>
            </div>
            {entry.executed_price != null && (
              <>
                <div>
                  <span className="text-slate-500">{t.executedAt}</span>
                  <p className="font-medium text-slate-800 mt-0.5">
                    {fmtDate(entry.executed_at)}
                    {entry.executed_by && ` · ${entry.executed_by}`}
                  </p>
                </div>
                <div>
                  <span className="text-slate-500">{t.error}</span>
                  <p className="font-medium mt-0.5">
                    {entry.price_error_cny != null ? (
                      <span
                        className={
                          Math.abs(entry.price_error_cny) < 200
                            ? "text-emerald-600"
                            : "text-red-600"
                        }
                      >
                        {entry.price_error_cny > 0 ? "+" : ""}
                        {fmt(entry.price_error_cny)} {unitSuffix(entry.price_unit)}
                      </span>
                    ) : (
                      "—"
                    )}
                    {entry.price_in_range != null && (
                      <span
                        className={`ml-2 text-[10px] rounded-full px-1.5 py-0.5 ${entry.price_in_range ? "bg-emerald-50 text-emerald-600" : "bg-red-50 text-red-500"}`}
                      >
                        {entry.price_in_range ? t.inRange : t.outRange}
                      </span>
                    )}
                  </p>
                </div>
              </>
            )}
          </div>

          {entry.ai_reasoning && (
            <div className="text-xs">
              <span className="text-slate-500">{t.reasoning}</span>
              <p className="mt-1 text-slate-700 leading-relaxed whitespace-pre-wrap line-clamp-4">
                {entry.ai_reasoning}
              </p>
            </div>
          )}

          {entry.outcome_note && (
            <div className="text-xs">
              <span className="text-slate-500">
                {lang === "en" ? "Note" : "备注"}
              </span>
              <p className="mt-0.5 text-slate-700">{entry.outcome_note}</p>
            </div>
          )}

          {/* Action buttons */}
          <div className="flex gap-2 flex-wrap pt-1">
            {entry.status === "pending" && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  setShowExecute((p) => !p);
                  setShowOutcome(false);
                }}
                className="inline-flex items-center gap-1 rounded-md bg-white border border-blue-200 px-2 py-1 text-xs text-blue-700 hover:bg-blue-50 transition-colors"
              >
                <PenSquare className="w-3.5 h-3.5" />
                {t.markExecuted}
              </button>
            )}
            {(entry.status === "pending" || entry.status === "executed") && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  setShowOutcome((p) => !p);
                  setShowExecute(false);
                }}
                className="inline-flex items-center gap-1 rounded-md bg-white border border-slate-200 px-2 py-1 text-xs text-slate-700 hover:bg-slate-100 transition-colors"
              >
                <CheckCircle2 className="w-3.5 h-3.5" />
                {t.markOutcome}
              </button>
            )}
          </div>

          {showExecute && (
            <ExecuteForm
              entryId={entry.id}
              currentUser={currentUser}
              lang={lang}
              onDone={(updated) => {
                setShowExecute(false);
                onUpdate(updated);
              }}
            />
          )}
          {showOutcome && (
            <OutcomeForm
              entryId={entry.id}
              lang={lang}
              onDone={(updated) => {
                setShowOutcome(false);
                onUpdate(updated);
              }}
            />
          )}
        </div>
      )}
    </div>
  );
};

// ─── accuracy stats panel ─────────────────────────────────────────────────────

const AccuracyPanel: React.FC<{
  stats: DecisionAccuracyStats;
  lang: "en" | "zh";
}> = ({ stats, lang }) => {
  const t =
    lang === "en"
      ? {
        title: "Model Accuracy",
        total: "Decisions",
        hitRate: "In-Range Rate",
        mae: "Avg Error (¥/t)",
        dealRate: "Deal Rate",
        overall: "Overall",
      }
      : {
        title: "模型精度统计",
        total: "决策数",
        hitRate: "命中率",
        mae: "平均误差 (¥/吨)",
        dealRate: "成交率",
        overall: "综合",
      };

  return (
    <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-4 space-y-3">
      <h3 className="text-sm font-semibold text-slate-700">{t.title}</h3>

      {/* Overall row */}
      <div className="grid grid-cols-4 gap-3">
        {[
          { label: t.total, value: fmt(stats.overall.total_decisions) },
          {
            label: t.hitRate,
            value:
              stats.overall.hit_rate_pct != null
                ? `${stats.overall.hit_rate_pct.toFixed(1)}%`
                : "—",
          },
          {
            label: t.mae,
            value:
              stats.overall.mean_abs_error_cny != null
                ? `¥${fmt(stats.overall.mean_abs_error_cny, 0)}`
                : "—",
          },
          {
            label: t.dealRate,
            value:
              stats.overall.deal_rate_pct != null
                ? `${stats.overall.deal_rate_pct.toFixed(1)}%`
                : "—",
          },
        ].map(({ label, value }) => (
          <div key={label} className="rounded-lg bg-slate-50 p-3 text-center">
            <p className="text-[11px] text-slate-500">{label}</p>
            <p className="text-base font-bold text-slate-800 mt-1">{value}</p>
          </div>
        ))}
      </div>

      {/* Per-product table */}
      {stats.products.length > 0 && (
        <table className="w-full text-xs border-collapse mt-1">
          <thead>
            <tr className="text-slate-500 border-b border-slate-100">
              <th className="py-1 text-left font-medium">
                {lang === "en" ? "Product" : "产品"}
              </th>
              <th className="py-1 text-right font-medium">{t.total}</th>
              <th className="py-1 text-right font-medium">{t.hitRate}</th>
              <th className="py-1 text-right font-medium">{t.mae}</th>
              <th className="py-1 text-right font-medium">{t.dealRate}</th>
            </tr>
          </thead>
          <tbody>
            {stats.products.map((p) => (
              <tr
                key={p.product_id}
                className="border-b border-slate-50 hover:bg-slate-50"
              >
                <td className="py-1.5 text-slate-700">
                  {p.product_name || p.product_id}
                </td>
                <td className="py-1.5 text-right text-slate-600">
                  {fmt(p.total_decisions)}
                </td>
                <td className="py-1.5 text-right font-medium">
                  <span
                    className={
                      p.hit_rate_pct != null && p.hit_rate_pct >= 70
                        ? "text-emerald-600"
                        : "text-slate-600"
                    }
                  >
                    {p.hit_rate_pct != null
                      ? `${p.hit_rate_pct.toFixed(1)}%`
                      : "—"}
                  </span>
                </td>
                <td className="py-1.5 text-right text-slate-600">
                  {p.mean_abs_error_cny != null
                    ? `¥${fmt(p.mean_abs_error_cny, 0)}`
                    : "—"}
                </td>
                <td className="py-1.5 text-right text-slate-600">
                  {p.deal_rate_pct != null
                    ? `${p.deal_rate_pct.toFixed(1)}%`
                    : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
};

// ─── main component ───────────────────────────────────────────────────────────

export const DecisionTracker: React.FC<{
  currentUser: AuthUser | null;
}> = ({ currentUser }) => {
  const { language } = useLanguage();
  const lang = language as "en" | "zh";

  const [entries, setEntries] = useState<DecisionLogEntry[]>([]);
  const [accuracy, setAccuracy] = useState<DecisionAccuracyStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [daysFilter, setDaysFilter] = useState<number>(30);
  const [showAccuracy, setShowAccuracy] = useState(false);

  const t =
    lang === "en"
      ? {
        title: "Decision Log",
        subtitle: "Closed-loop tracking for AI pricing recommendations",
        filter: "Filter",
        all: "All",
        pending: "Pending",
        executed: "Executed",
        closed: "Closed",
        days: "days",
        accuracy: "View Accuracy",
        hideAccuracy: "Hide Accuracy",
        empty: "No decisions recorded yet",
        emptyHint:
          "Use the 'Save to Log' button on AI pricing messages to log decisions.",
        refresh: "Refresh",
        loading: "Loading...",
      }
      : {
        title: "决策追踪",
        subtitle: "AI 报价建议闭环跟踪",
        filter: "筛选",
        all: "全部",
        pending: "待执行",
        executed: "已执行",
        closed: "已关闭",
        days: "天",
        accuracy: "查看精度",
        hideAccuracy: "隐藏精度",
        empty: "暂无决策记录",
        emptyHint: '在 AI 报价消息上点击"存入决策日志"按钮以记录决策。',
        refresh: "刷新",
        loading: "加载中...",
      };

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params: { status?: string; days?: number } = { days: daysFilter };
      if (statusFilter !== "all") params.status = statusFilter;
      const [e, a] = await Promise.all([
        api.listDecisionLog(params),
        api.getDecisionAccuracy(),
      ]);
      setEntries(e);
      setAccuracy(a);
    } catch {
      setError(lang === "en" ? "Failed to load data" : "数据加载失败");
    } finally {
      setLoading(false);
    }
  }, [daysFilter, statusFilter, lang]);

  useEffect(() => {
    load();
  }, [load]);

  const handleUpdate = useCallback((updated: DecisionLogEntry) => {
    setEntries((prev) => prev.map((e) => (e.id === updated.id ? updated : e)));
    // refresh accuracy stats quietly
    api
      .getDecisionAccuracy()
      .then(setAccuracy)
      .catch(() => { });
  }, []);

  const handleDelete = useCallback((id: string) => {
    setEntries((prev) => prev.filter((e) => e.id !== id));
  }, []);

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-4xl mx-auto space-y-5">
        {/* Header */}
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <h1 className="text-xl font-bold text-slate-800">{t.title}</h1>
            <p className="text-sm text-slate-500 mt-0.5">{t.subtitle}</p>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <button
              onClick={() => setShowAccuracy((p) => !p)}
              className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 transition-colors"
            >
              {showAccuracy ? t.hideAccuracy : t.accuracy}
            </button>
            <button
              onClick={load}
              disabled={loading}
              className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 transition-colors disabled:opacity-50"
            >
              {t.refresh}
            </button>
          </div>
        </div>

        {/* Filters */}
        <div className="flex items-center gap-3 flex-wrap text-sm">
          <span className="text-slate-500 text-xs font-medium">
            {t.filter}:
          </span>
          {(["all", "pending", "executed", "closed"] as const).map((s) => (
            <button
              key={s}
              onClick={() => setStatusFilter(s)}
              className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${statusFilter === s ? "bg-slate-800 text-white" : "bg-white border border-slate-200 text-slate-600 hover:bg-slate-50"}`}
            >
              {t[s]}
            </button>
          ))}
          <div className="ml-auto flex items-center gap-1">
            {[7, 30, 90].map((d) => (
              <button
                key={d}
                onClick={() => setDaysFilter(d)}
                className={`rounded px-2 py-1 text-xs transition-colors ${daysFilter === d ? "bg-slate-800 text-white" : "bg-white border border-slate-200 text-slate-600 hover:bg-slate-50"}`}
              >
                {d}
                {t.days}
              </button>
            ))}
          </div>
        </div>

        {/* Accuracy panel */}
        {showAccuracy && accuracy && (
          <AccuracyPanel stats={accuracy} lang={lang} />
        )}

        {/* Content */}
        {loading ? (
          <div className="flex items-center justify-center py-16 text-slate-400 gap-2">
            <Clock className="w-5 h-5 animate-spin" />
            <span className="text-sm">{t.loading}</span>
          </div>
        ) : error ? (
          <div className="flex items-center gap-2 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-600">
            <AlertCircle className="w-5 h-5 flex-shrink-0" />
            {error}
          </div>
        ) : entries.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-center gap-3">
            <XCircle className="w-10 h-10 text-slate-200" />
            <p className="text-sm font-medium text-slate-400">{t.empty}</p>
            <p className="text-xs text-slate-400 max-w-xs">{t.emptyHint}</p>
          </div>
        ) : (
          <div className="space-y-3">
            {entries.map((entry) => (
              <EntryRow
                key={entry.id}
                entry={entry}
                currentUser={currentUser}
                lang={lang}
                onUpdate={handleUpdate}
                onDelete={handleDelete}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
