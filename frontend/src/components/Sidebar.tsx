import {
  ChevronLeft,
  ChevronRight,
  MoreHorizontal,
  Pencil,
  Pin,
  Plus,
  Search,
  Trash2,
  X,
} from "lucide-react";
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useLanguage } from "../lib/language";
import { cn } from "../lib/utils";
import { Session } from "../services/api";

interface SidebarProps {
  sessions: Session[];
  currentSessionId: string | null;
  onSelectSession: (id: string) => void;
  onNewSession: () => void;
  onRenameSession: (id: string, nextTitle: string) => Promise<void> | void;
  onDeleteSession: (id: string) => Promise<void> | void;
  isOpen: boolean;
  onToggle: () => void;
}

type SessionBucket = "today" | "this_week" | "earlier";

type SessionTag =
  | "c5"
  | "c9"
  | "ethylene"
  | "crude_oil"
  | "naphtha"
  | "cracked_c5"
  | "cracked_c9"
  | "isoprene"
  | "piperylene"
  | "dcpd"
  | "raffinate_c5"
  | "sis"
  | "c5_resin"
  | "styrene"
  | "blowing_agent"
  | "general";

type DateFilter =
  | "all"
  | "today"
  | "this_week"
  | "last_week"
  | "last_month"
  | "last_2_months"
  | "last_3_months";

const PINNED_STORAGE_KEY = "edia_pinned_sessions_v1";

function parseDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

function getSessionBucket(createdAt: string): SessionBucket {
  const d = parseDate(createdAt);
  if (!d) return "earlier";

  const now = new Date();
  const startOfToday = new Date(
    now.getFullYear(),
    now.getMonth(),
    now.getDate(),
  );
  const startOfWeek = new Date(startOfToday);
  const day = startOfWeek.getDay();
  const mondayOffset = day === 0 ? -6 : 1 - day;
  startOfWeek.setDate(startOfWeek.getDate() + mondayOffset);

  if (d >= startOfToday) return "today";
  if (d >= startOfWeek) return "this_week";
  return "earlier";
}

function inferSessionTags(title: string): SessionTag[] {
  const t = (title || "").toLowerCase();
  const tags = new Set<SessionTag>();

  const rules: Array<[SessionTag, RegExp]> = [
    [
      "c5",
      /c5|碳五|裂解碳五|异戊二烯|间戊二烯|dcpd|双环戊二烯|抽余碳五|c5\s*resin|sis|戊烷/,
    ],
    ["c9", /c9|碳九|裂解碳九|加氢碳九|混三甲苯/],
    ["ethylene", /乙烯|ethylene/],
    ["crude_oil", /原油|crude|brent/],
    ["naphtha", /石脑油|naphtha/],
    ["cracked_c5", /裂解碳五|cracked\s*c5/],
    ["cracked_c9", /裂解碳九|cracked\s*c9/],
    ["isoprene", /异戊二烯|isoprene/],
    ["piperylene", /间戊二烯|piperylene/],
    ["dcpd", /dcpd|双环戊二烯/],
    ["raffinate_c5", /抽余碳五|raffinate\s*c5/],
    ["sis", /\bsis\b/],
    ["c5_resin", /碳五石油树脂|c5\s*resin/],
    ["styrene", /苯乙烯|styrene/],
    ["blowing_agent", /戊烷发泡剂|blowing\s*agent/],
  ];

  rules.forEach(([tag, pattern]) => {
    if (pattern.test(t)) tags.add(tag);
  });

  if (tags.size === 0) tags.add("general");
  return Array.from(tags);
}

function uniqueTags(sessions: Session[]): SessionTag[] {
  const seen = new Set<SessionTag>();
  sessions.forEach((s) =>
    inferSessionTags(s.title).forEach((tag) => seen.add(tag)),
  );
  return Array.from(seen);
}

const sessionAccentTone: Record<SessionTag, string> = {
  c5: "bg-slate-400",
  c9: "bg-slate-400",
  ethylene: "bg-slate-400",
  crude_oil: "bg-slate-500",
  naphtha: "bg-slate-400",
  cracked_c5: "bg-slate-400",
  cracked_c9: "bg-slate-400",
  isoprene: "bg-slate-400",
  piperylene: "bg-slate-400",
  dcpd: "bg-slate-400",
  raffinate_c5: "bg-slate-400",
  sis: "bg-slate-400",
  c5_resin: "bg-slate-400",
  styrene: "bg-slate-400",
  blowing_agent: "bg-slate-400",
  general: "bg-slate-500",
};

const filterTone: Record<"all" | SessionTag, string> = {
  all: "border-sky-300 bg-sky-50/90 shadow-sky-100",
  c5: "border-sky-300 bg-sky-50/90 shadow-sky-100",
  c9: "border-indigo-300 bg-indigo-50/90 shadow-indigo-100",
  ethylene: "border-emerald-300 bg-emerald-50/90 shadow-emerald-100",
  crude_oil: "border-zinc-300 bg-zinc-100/90 shadow-zinc-100",
  naphtha: "border-cyan-300 bg-cyan-50/90 shadow-cyan-100",
  cracked_c5: "border-blue-300 bg-blue-50/90 shadow-blue-100",
  cracked_c9: "border-violet-300 bg-violet-50/90 shadow-violet-100",
  isoprene: "border-teal-300 bg-teal-50/90 shadow-teal-100",
  piperylene: "border-lime-300 bg-lime-50/90 shadow-lime-100",
  dcpd: "border-amber-300 bg-amber-50/90 shadow-amber-100",
  raffinate_c5: "border-sky-300 bg-sky-50/90 shadow-sky-100",
  sis: "border-rose-300 bg-rose-50/90 shadow-rose-100",
  c5_resin: "border-indigo-300 bg-indigo-50/90 shadow-indigo-100",
  styrene: "border-orange-300 bg-orange-50/90 shadow-orange-100",
  blowing_agent: "border-emerald-300 bg-emerald-50/90 shadow-emerald-100",
  general: "border-slate-300 bg-slate-100/90 shadow-slate-100",
};

function formatSessionDate(createdAt: string, locale: string): string {
  const d = parseDate(createdAt);
  if (!d) return "--";
  return d.toLocaleString(locale, {
    month: "2-digit",
    day: "2-digit",
  });
}

function sortByRecency(rows: Session[]): Session[] {
  return [...rows].sort((a, b) => {
    const ta = parseDate(a.created_at)?.getTime() ?? 0;
    const tb = parseDate(b.created_at)?.getTime() ?? 0;
    return tb - ta;
  });
}

function matchesDateFilter(createdAt: string, filter: DateFilter): boolean {
  if (filter === "all") return true;
  const d = parseDate(createdAt);
  if (!d) return false;

  const now = new Date();
  const startOfToday = new Date(
    now.getFullYear(),
    now.getMonth(),
    now.getDate(),
  );
  const startOfWeek = new Date(startOfToday);
  const day = startOfWeek.getDay();
  const mondayOffset = day === 0 ? -6 : 1 - day;
  startOfWeek.setDate(startOfWeek.getDate() + mondayOffset);

  const startOfLastWeek = new Date(startOfWeek);
  startOfLastWeek.setDate(startOfLastWeek.getDate() - 7);

  const startOfMonth = new Date(now.getFullYear(), now.getMonth(), 1);
  const startOfLastMonth = new Date(now.getFullYear(), now.getMonth() - 1, 1);
  const startOfTwoMonthsAgo = new Date(
    now.getFullYear(),
    now.getMonth() - 2,
    1,
  );
  const startOfThreeMonthsAgo = new Date(
    now.getFullYear(),
    now.getMonth() - 3,
    1,
  );

  if (filter === "today") return d >= startOfToday;
  if (filter === "this_week") return d >= startOfWeek;
  if (filter === "last_week") return d >= startOfLastWeek && d < startOfWeek;
  if (filter === "last_month") return d >= startOfLastMonth && d < startOfMonth;
  if (filter === "last_2_months")
    return d >= startOfTwoMonthsAgo && d < startOfMonth;
  if (filter === "last_3_months")
    return d >= startOfThreeMonthsAgo && d < startOfMonth;
  return true;
}

export const Sidebar: React.FC<SidebarProps> = ({
  sessions,
  currentSessionId,
  onSelectSession,
  onNewSession,
  onRenameSession,
  onDeleteSession,
  isOpen,
  onToggle,
}) => {
  const { language } = useLanguage();
  const locale = language === "en" ? "en-US" : "zh-CN";
  const text =
    language === "en"
      ? {
        newChat: "New Chat",
        history: "History",
        pinned: "Pinned",
        today: "Today",
        thisWeek: "This Week",
        earlier: "Earlier",
        lastWeek: "Last Week",
        lastMonth: "Last Month",
        last2Months: "Last 2 Months",
        last3Months: "Last 3 Months",
        all: "All",
        c5: "C5",
        c9: "C9",
        ethylene: "Ethylene",
        crudeOil: "Crude Oil",
        naphtha: "Naphtha",
        crackedC5: "Cracked C5",
        crackedC9: "Cracked C9",
        isoprene: "Isoprene",
        piperylene: "Piperylene",
        dcpd: "DCPD",
        raffinateC5: "Raffinate C5",
        sis: "SIS",
        c5Resin: "C5 Resin",
        styrene: "Styrene",
        blowingAgent: "Blowing Agent",
        general: "General",
        noSessions: "No sessions",
        pin: "Pin",
        unpin: "Unpin",
        rename: "Rename",
        delete: "Delete Chat",
        renamePrompt: "Rename chat",
        deleteConfirm: "Delete this chat?",
        searchPlaceholder: "Search title or tag",
        clearSelection: "Clear",
        dateFilter: "Date",
        clearFilters: "Clear Filters",
        visibleCount: (count: number) => `${count} visible`,
        pinnedCount: (count: number) => `${count} pinned`,
        tagFilter: "Tag",
        actionsMenu: "Actions",
      }
      : {
        newChat: "新建会话",
        history: "历史记录",
        pinned: "置顶",
        today: "今天",
        thisWeek: "本周",
        earlier: "更早",
        lastWeek: "上周",
        lastMonth: "上月",
        last2Months: "近2个月",
        last3Months: "近3个月",
        all: "全部",
        c5: "C5",
        c9: "C9",
        ethylene: "乙烯",
        crudeOil: "原油",
        naphtha: "石脑油",
        crackedC5: "裂解C5",
        crackedC9: "裂解C9",
        isoprene: "异戊二烯",
        piperylene: "间戊二烯",
        dcpd: "DCPD",
        raffinateC5: "抽余C5",
        sis: "SIS",
        c5Resin: "C5树脂",
        styrene: "苯乙烯",
        blowingAgent: "发泡剂",
        general: "综合",
        noSessions: "暂无会话",
        pin: "置顶",
        unpin: "取消置顶",
        rename: "重命名",
        delete: "删除会话",
        renamePrompt: "重命名会话",
        deleteConfirm: "确认删除该会话？",
        searchPlaceholder: "搜索标题或标签",
        clearSelection: "清空",
        dateFilter: "日期",
        clearFilters: "清空筛选",
        visibleCount: (count: number) => `显示 ${count} 条`,
        pinnedCount: (count: number) => `置顶 ${count} 条`,
        tagFilter: "标签",
        actionsMenu: "操作",
      };

  const [pinnedIds, setPinnedIds] = useState<Set<string>>(new Set());
  const [activeTag, setActiveTag] = useState<"all" | SessionTag>("all");
  const [activeDateFilter, setActiveDateFilter] = useState<DateFilter>("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [openMenuSessionId, setOpenMenuSessionId] = useState<string | null>(
    null,
  );
  const [menuPosition, setMenuPosition] = useState<{ top: number; left: number } | null>(null);
  const menuTriggerRef = useRef<HTMLButtonElement | null>(null);
  const [openDateMenu, setOpenDateMenu] = useState(false);
  const [openTagMenu, setOpenTagMenu] = useState(false);

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(PINNED_STORAGE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return;
      setPinnedIds(new Set(parsed.filter((x) => typeof x === "string")));
    } catch {
      // ignore corrupt local value
    }
  }, []);

  useEffect(() => {
    try {
      window.localStorage.setItem(
        PINNED_STORAGE_KEY,
        JSON.stringify(Array.from(pinnedIds)),
      );
    } catch {
      // ignore storage failures
    }
  }, [pinnedIds]);

  // Close menu on click outside or Escape key
  useEffect(() => {
    if (!openMenuSessionId) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpenMenuSessionId(null);
    };
    const handleMouseDown = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (!target.closest("[data-session-menu]")) {
        setOpenMenuSessionId(null);
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    document.addEventListener("mousedown", handleMouseDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.removeEventListener("mousedown", handleMouseDown);
    };
  }, [openMenuSessionId]);

  useEffect(() => {
    const validIds = new Set(sessions.map((session) => session.id));
    setPinnedIds((prev) => {
      const next = new Set(Array.from(prev).filter((id) => validIds.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [sessions]);

  useEffect(() => {
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      if (!target.closest("[data-session-menu]")) {
        setOpenMenuSessionId(null);
      }
      if (!target.closest("[data-date-menu]")) {
        setOpenDateMenu(false);
      }
      if (!target.closest("[data-tag-menu]")) {
        setOpenTagMenu(false);
      }
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, []);

  const availableTags = useMemo(() => uniqueTags(sessions), [sessions]);

  const filteredSessions = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    return sessions.filter((s) => {
      const tags = inferSessionTags(s.title);
      const tagMatched = activeTag === "all" || tags.includes(activeTag);
      if (!tagMatched) return false;
      const dateMatched = matchesDateFilter(s.created_at, activeDateFilter);
      if (!dateMatched) return false;
      if (!q) return true;

      const tagsText = tags.join(" ");
      const hay = `${s.title || ""} ${tagsText}`.toLowerCase();
      return hay.includes(q);
    });
  }, [sessions, activeTag, activeDateFilter, searchQuery]);

  const sortedFilteredSessions = useMemo(
    () => sortByRecency(filteredSessions),
    [filteredSessions],
  );

  const pinnedSessions = useMemo(
    () => sortedFilteredSessions.filter((s) => pinnedIds.has(s.id)),
    [sortedFilteredSessions, pinnedIds],
  );

  const unpinnedSessions = useMemo(
    () => sortedFilteredSessions.filter((s) => !pinnedIds.has(s.id)),
    [sortedFilteredSessions, pinnedIds],
  );

  const grouped = useMemo(() => {
    const out: Record<SessionBucket, Session[]> = {
      today: [],
      this_week: [],
      earlier: [],
    };
    unpinnedSessions.forEach((s) =>
      out[getSessionBucket(s.created_at)].push(s),
    );
    return out;
  }, [unpinnedSessions]);

  const togglePin = (id: string) => {
    setPinnedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const hasActiveFilters =
    activeTag !== "all" ||
    activeDateFilter !== "all" ||
    searchQuery.trim().length > 0;

  const clearAllFilters = () => {
    setActiveTag("all");
    setActiveDateFilter("all");
    setSearchQuery("");
  };

  const tagLabelMap: Record<SessionTag, string> = {
    c5: text.c5,
    c9: text.c9,
    ethylene: text.ethylene,
    crude_oil: text.crudeOil,
    naphtha: text.naphtha,
    cracked_c5: text.crackedC5,
    cracked_c9: text.crackedC9,
    isoprene: text.isoprene,
    piperylene: text.piperylene,
    dcpd: text.dcpd,
    raffinate_c5: text.raffinateC5,
    sis: text.sis,
    c5_resin: text.c5Resin,
    styrene: text.styrene,
    blowing_agent: text.blowingAgent,
    general: text.general,
  };

  const primaryTagChips: SessionTag[] = ["c5", "c9"];
  const extraTagOptions = availableTags.filter(
    (tag) => !primaryTagChips.includes(tag),
  );
  const dateOverflowSelected = ["last_2_months", "last_3_months"].includes(
    activeDateFilter,
  );
  const tagOverflowSelected =
    activeTag !== "all" && !primaryTagChips.includes(activeTag);

  const renderSessionRow = (session: Session) => {
    const tags = inferSessionTags(session.title);
    const pinned = pinnedIds.has(session.id);
    const primaryTag = tags[0] ?? "general";
    const accentGradient = sessionAccentTone[primaryTag];
    const isActive = currentSessionId === session.id;

    return (
      <div key={session.id} className="relative group">
        <button
          type="button"
          onClick={() => onSelectSession(session.id)}
          className={cn(
            "sidebar-session-card relative w-full overflow-visible text-left px-3 py-2.5 pr-11 rounded-2xl transition-all duration-200 border shadow-sm ml-1",
            isActive && "is-active",
          )}
        >
          <div
            className={cn(
              "absolute inset-y-2 left-1 w-1.5 rounded-full",
              isActive
                ? "bg-blue-500"
                : `bg-gradient-to-b ${accentGradient}`,
            )}
          />
          <div className="min-w-0">
            <div className={cn("sidebar-session-title pr-1 text-sm leading-5 line-clamp-2", isActive ? "font-bold" : "font-semibold")}>
              {session.title}
            </div>
            <div className="mt-1.5 flex items-center gap-1.5 flex-wrap">
              {pinned && (
                <span className="sidebar-session-tag rounded-full px-2 py-0.5 text-[10px] font-medium">
                  {text.pinned}
                </span>
              )}
              {tags.slice(0, 2).map((tag) => (
                <span
                  key={`${session.id}-${tag}`}
                  className="sidebar-product-tag text-[10px] font-medium px-1.5 py-0.5 rounded-md"
                >
                  {tagLabelMap[tag]}
                </span>
              ))}
              <span className="sidebar-session-date rounded-full px-2 py-0.5 text-[10px] font-medium">
                {formatSessionDate(session.created_at, locale)}
              </span>
            </div>
          </div>
        </button>

        <div data-session-menu className={cn(
          "absolute right-2 top-2.5 z-30 transition-opacity duration-150",
          openMenuSessionId && openMenuSessionId !== session.id ? "opacity-0 pointer-events-none" : "opacity-100"
        )}>
          <button
            ref={openMenuSessionId === session.id ? menuTriggerRef : null}
            type="button"
            aria-label={text.actionsMenu}
            title={text.actionsMenu}
            onClick={(e) => {
              e.stopPropagation();
              const btn = e.currentTarget;
              const rect = btn.getBoundingClientRect();
              const menuWidth = Math.min(208, window.innerWidth - 16);
              const menuHeight = 170;
              const gap = 6;
              const padding = 8;

              const vw = window.innerWidth;
              const vh = window.innerHeight;

              // Center the menu under the button, then clamp to viewport
              let left = rect.left + rect.width / 2 - menuWidth / 2;
              let top = rect.bottom + gap;

              if (left < padding) left = padding;
              if (left + menuWidth > vw - padding) left = vw - menuWidth - padding;
              if (top + menuHeight > vh - padding) {
                top = rect.top - menuHeight - gap;
              }

              setMenuPosition({ top, left });
              setOpenMenuSessionId((prev) =>
                prev === session.id ? null : session.id,
              );
            }}
            className={cn(
              "sidebar-menu-trigger inline-flex h-8 w-8 items-center justify-center rounded-xl border shadow-sm transition-all hover:-translate-y-px",
              openMenuSessionId === session.id && "is-open",
            )}
          >
            <MoreHorizontal className="w-4 h-4" />
          </button>
        </div>
      </div>
    );
  };

  const renderGroup = (title: string, rows: Session[]) => {
    if (rows.length === 0) return null;
    return (
      <div className="space-y-1.5">
        <div className="sidebar-group-header text-[11px] font-semibold px-2 pt-2 uppercase tracking-[0.14em] flex items-center justify-between">
          <span>{title}</span>
          <span className="sidebar-group-count text-[10px] font-semibold px-1.5 py-0.5 rounded-full">
            {rows.length}
          </span>
        </div>
        <div className="sidebar-group-rail ml-0.5 border-l-2 pl-2.5 space-y-1.5">
          {rows.map(renderSessionRow)}
        </div>
      </div>
    );
  };

  return (
    <>
      {/* Mobile Overlay */}
      <div
        className={cn(
          "fixed inset-0 bg-black/50 z-20 md:hidden transition-opacity",
          isOpen ? "opacity-100" : "opacity-0 pointer-events-none",
        )}
        onClick={onToggle}
      />

      {/* Sidebar Container */}
      <div
        className={cn(
          "sidebar-root fixed md:relative z-30 h-full bg-stone-50 text-slate-800 transition-all duration-300 ease-in-out flex flex-col border-r border-stone-200",
          isOpen
            ? "w-72 translate-x-0"
            : "w-0 -translate-x-full md:w-16 md:translate-x-0",
        )}
      >
        {/* Header */}
        <div className={cn(
          "sidebar-header border-b flex items-center",
          isOpen ? "p-4 pt-3 gap-2" : "p-2 flex-col gap-2"
        )}>
          <button
            onClick={onNewSession}
            className={cn(
              "sidebar-new-chat flex items-center justify-center rounded-2xl border transition-colors duration-150",
              isOpen ? "flex-1 gap-3 px-4 py-3" : "w-10 h-10"
            )}
            title={text.newChat}
          >
            <span className={cn(
              "sidebar-new-chat-icon flex items-center justify-center rounded-full",
              isOpen ? "h-10 w-10" : "h-6 w-6"
            )}>
              <Plus className={cn(isOpen ? "w-5 h-5" : "w-4 h-4")} />
            </span>
            {isOpen && (
              <span className="text-2xl font-medium tracking-tight">
                {text.newChat}
              </span>
            )}
          </button>
          <button
            onClick={onToggle}
            className="sidebar-collapse-btn flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border transition-colors"
            aria-label={isOpen ? "Collapse sidebar" : "Expand sidebar"}
            title={isOpen ? "Collapse sidebar" : "Expand sidebar"}
          >
            {isOpen ? <ChevronLeft className="w-5 h-5" /> : <ChevronRight className="w-5 h-5" />}
          </button>
        </div>

        {/* Session List */}
        <div className="flex-1 overflow-y-auto scrollbar-thin scrollbar-thumb-slate-300">
          {isOpen ? (
            <div className="px-2 py-2 space-y-3">
              <div className="px-2 pt-1 text-[11px] text-slate-700 font-medium flex items-center gap-3">
                <span>{text.visibleCount(filteredSessions.length)}</span>
                <span>{text.pinnedCount(pinnedSessions.length)}</span>
                {hasActiveFilters && (
                  <button
                    onClick={clearAllFilters}
                    className="text-slate-700 hover:text-slate-900 underline underline-offset-2"
                  >
                    {text.clearFilters}
                  </button>
                )}
              </div>

              <div className="px-2 pb-2">
                <div className="relative">
                  <Search className="w-3.5 h-3.5 text-slate-900 absolute left-2.5 top-1/2 -translate-y-1/2" />
                  <input
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    placeholder={text.searchPlaceholder}
                    className="sidebar-search-input w-full pl-8 pr-8 py-2 text-[13px] rounded-xl border border-stone-200 bg-white text-slate-900 placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-stone-100 focus:border-stone-300"
                  />
                  {searchQuery && (
                    <button
                      onClick={() => setSearchQuery("")}
                      className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-900 hover:text-slate-900"
                      aria-label={text.clearSelection}
                    >
                      <X className="w-3.5 h-3.5" />
                    </button>
                  )}
                </div>
              </div>

              {/* Date filter */}
              <div className="px-2 pb-2 flex flex-wrap gap-1.5">
                <span className="text-[11px] text-slate-900 font-semibold self-center mr-1 uppercase tracking-[0.14em]">
                  {text.dateFilter}
                </span>
                <button
                  onClick={() => setActiveDateFilter("all")}
                  className={cn(
                    "sidebar-filter-pill text-[11px] font-medium px-2.5 py-1 rounded-xl border transition-all",
                    activeDateFilter === "all" && "is-active",
                  )}
                >
                  {text.all}
                </button>
                <button
                  onClick={() => setActiveDateFilter("today")}
                  className={cn(
                    "sidebar-filter-pill text-[11px] font-medium px-2.5 py-1 rounded-xl border transition-all",
                    activeDateFilter === "today" && "is-active",
                  )}
                >
                  {text.today}
                </button>
                <button
                  onClick={() => setActiveDateFilter("this_week")}
                  className={cn(
                    "sidebar-filter-pill text-[11px] font-medium px-2.5 py-1 rounded-xl border transition-all",
                    activeDateFilter === "this_week" && "is-active",
                  )}
                >
                  {text.thisWeek}
                </button>
                <button
                  onClick={() => setActiveDateFilter("last_week")}
                  className={cn(
                    "sidebar-filter-pill text-[11px] font-medium px-2.5 py-1 rounded-xl border transition-all",
                    activeDateFilter === "last_week" && "is-active",
                  )}
                >
                  {text.lastWeek}
                </button>
                <button
                  onClick={() => setActiveDateFilter("last_month")}
                  className={cn(
                    "sidebar-filter-pill text-[11px] font-medium px-2.5 py-1 rounded-xl border transition-all",
                    activeDateFilter === "last_month" && "is-active",
                  )}
                >
                  {text.lastMonth}
                </button>
                <div data-date-menu className="relative">
                  <button
                    onClick={() => setOpenDateMenu((v) => !v)}
                    className={cn(
                      "sidebar-filter-pill text-[11px] font-medium px-2.5 py-1 rounded-xl border transition-all",
                      dateOverflowSelected && "is-active",
                    )}
                  >
                    ...
                  </button>
                  {openDateMenu && (
                    <div className="sidebar-dropdown absolute left-0 top-7 z-30 min-w-[130px] rounded-lg border border-slate-200 bg-white shadow-lg p-1">
                      <button
                        onClick={() => {
                          setActiveDateFilter("last_2_months");
                          setOpenDateMenu(false);
                        }}
                        className={cn(
                          "w-full text-left px-2 py-1.5 text-[11px] rounded hover:bg-slate-100",
                          activeDateFilter === "last_2_months"
                            ? "text-slate-900 bg-blue-50"
                            : "text-slate-900",
                        )}
                      >
                        {text.last2Months}
                      </button>
                      <button
                        onClick={() => {
                          setActiveDateFilter("last_3_months");
                          setOpenDateMenu(false);
                        }}
                        className={cn(
                          "w-full text-left px-2 py-1.5 text-[11px] rounded hover:bg-slate-100",
                          activeDateFilter === "last_3_months"
                            ? "text-slate-900 bg-blue-50"
                            : "text-slate-900",
                        )}
                      >
                        {text.last3Months}
                      </button>
                    </div>
                  )}
                </div>
              </div>

              {/* Tag filter */}
              <div className="px-2 pb-2 flex flex-wrap gap-1.5">
                <span className="text-[11px] text-slate-900 font-semibold self-center mr-1 uppercase tracking-[0.14em]">
                  {text.tagFilter}
                </span>
                <button
                  onClick={() => setActiveTag("all")}
                  className={cn(
                    "sidebar-filter-pill text-[11px] font-medium px-2.5 py-1 rounded-xl border transition-all",
                    activeTag === "all" && "is-active",
                  )}
                >
                  {text.all}
                </button>
                {primaryTagChips.map((tag) => (
                  <button
                    key={tag}
                    onClick={() => setActiveTag(tag)}
                    className={cn(
                      "sidebar-filter-pill text-[11px] font-medium px-2.5 py-1 rounded-xl border transition-all",
                      activeTag === tag && "is-active",
                    )}
                  >
                    {tag === "c5"
                      ? text.c5
                      : tag === "c9"
                        ? text.c9
                        : tag === "ethylene"
                          ? text.ethylene
                          : text.general}
                  </button>
                ))}
                <div data-tag-menu className="relative">
                  <button
                    onClick={() => setOpenTagMenu((v) => !v)}
                    className={cn(
                      "sidebar-filter-pill text-[11px] font-medium px-2.5 py-1 rounded-xl border transition-all",
                      tagOverflowSelected && "is-active",
                    )}
                  >
                    ...
                  </button>
                  {openTagMenu && (
                    <div className="sidebar-dropdown absolute left-0 top-7 z-30 min-w-[150px] max-h-60 overflow-y-auto rounded-lg border border-slate-200 bg-white shadow-lg p-1">
                      {extraTagOptions.length === 0 ? (
                        <div className="px-2 py-1.5 text-[11px] text-slate-900">
                          {text.noSessions}
                        </div>
                      ) : (
                        extraTagOptions.map((tag) => (
                          <button
                            key={tag}
                            onClick={() => {
                              setActiveTag(tag);
                              setOpenTagMenu(false);
                            }}
                            className={cn(
                              "w-full text-left px-2 py-1.5 text-[11px] rounded hover:bg-slate-100",
                              activeTag === tag
                                ? `${filterTone[tag]} text-slate-900`
                                : "text-slate-900",
                            )}
                          >
                            {tagLabelMap[tag]}
                          </button>
                        ))
                      )}
                    </div>
                  )}
                </div>
              </div>

              {renderGroup(text.pinned, pinnedSessions)}
              {renderGroup(text.today, grouped.today)}
              {renderGroup(text.thisWeek, grouped.this_week)}
              {renderGroup(text.earlier, grouped.earlier)}

              {filteredSessions.length === 0 && (
                <div className="px-4 py-3 text-sm text-slate-900">
                  {text.noSessions}
                </div>
              )}
            </div>
          ) : null}
        </div>
      </div>

      {/* Toggle Button (Mobile only) */}
      <button
        onClick={onToggle}
        className={cn(
          "fixed top-4 left-4 z-40 p-2 bg-slate-900 text-white rounded-lg shadow-lg md:hidden",
          isOpen && "hidden",
        )}
      >
        <ChevronRight className="w-5 h-5" />
      </button>

      {/* Portal: Session Actions Menu */}
      {openMenuSessionId &&
        menuPosition &&
        createPortal(
          <div
            data-session-menu
            className="sidebar-dropdown fixed z-[9999] max-w-[min(13rem,calc(100vw-16px))] min-w-[180px] rounded-2xl border border-slate-200/80 bg-white/95 p-1.5 shadow-[0_24px_48px_rgba(15,23,42,0.18)] backdrop-blur-xl overflow-visible"
            style={{ top: menuPosition.top, left: menuPosition.left }}
          >
            <div className="absolute -top-1.5 left-1/2 -translate-x-1/2 h-3 w-3 rotate-45 border-l border-t border-slate-200/80 bg-white/95" />
            {(() => {
              const session = sessions.find((s) => s.id === openMenuSessionId);
              if (!session) return null;
              const isPinned = pinnedIds.has(session.id);
              return (
                <>
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      togglePin(session.id);
                      setOpenMenuSessionId(null);
                    }}
                    className="flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left text-[13px] font-medium text-slate-700 transition-colors hover:bg-slate-50 hover:text-slate-950"
                  >
                    <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-slate-100 text-slate-600">
                      <Pin className="w-3.5 h-3.5" />
                    </span>
                    <span>{isPinned ? text.unpin : text.pin}</span>
                  </button>
                  <button
                    type="button"
                    onClick={async (e) => {
                      e.stopPropagation();
                      const currentTitle = session.title || "";
                      const nextTitle = window.prompt(
                        text.renamePrompt,
                        currentTitle,
                      );
                      if (nextTitle === null) {
                        setOpenMenuSessionId(null);
                        return;
                      }
                      const trimmed = nextTitle.trim();
                      if (!trimmed || trimmed === currentTitle.trim()) {
                        setOpenMenuSessionId(null);
                        return;
                      }
                      await onRenameSession(session.id, trimmed);
                      setOpenMenuSessionId(null);
                    }}
                    className="flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left text-[13px] font-medium text-slate-700 transition-colors hover:bg-slate-50 hover:text-slate-950"
                  >
                    <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-slate-100 text-slate-600">
                      <Pencil className="w-3.5 h-3.5" />
                    </span>
                    <span>{text.rename}</span>
                  </button>
                  <div className="my-1 h-px bg-slate-200/80" />
                  <button
                    type="button"
                    onClick={async (e) => {
                      e.stopPropagation();
                      const confirmed = window.confirm(text.deleteConfirm);
                      if (!confirmed) {
                        setOpenMenuSessionId(null);
                        return;
                      }
                      await onDeleteSession(session.id);
                      setOpenMenuSessionId(null);
                    }}
                    className="flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left text-[13px] font-medium text-rose-600 transition-colors hover:bg-rose-50"
                  >
                    <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-rose-50 text-rose-500">
                      <Trash2 className="w-3.5 h-3.5" />
                    </span>
                    {text.delete}
                  </button>
                </>
              );
            })()}
          </div>,
          document.body,
        )}
    </>
  );
};
