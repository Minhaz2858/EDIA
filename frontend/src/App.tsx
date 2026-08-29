import {
  BarChart3,
  Bell,
  BookmarkPlus,
  ChevronDown,
  FileText,
  Languages,
  LayoutDashboard,
  LogOut,
  MessageSquare,
  Moon,
  Settings,
  Sun,
  Users,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { LiveEventProvider } from "./context/LiveEventContext";
import { ChatInput } from "./components/ChatInput";
import {
  ChatMessage,
  ParsedRef,
  PreviewData,
  ReferencePanel,
} from "./components/ChatMessage";
import { Dashboard } from "./components/Dashboard";
import { DecisionTracker } from "./components/DecisionTracker";
import { Empty } from "./components/Empty";
import { ForcePasswordChangeScreen } from "./components/ForcePasswordChangeScreen";
import { LoginScreen } from "./components/LoginScreen";
import { AlertItem, ProactiveAlerts } from "./components/ProactiveAlerts";
import { ReportView } from "./components/ReportView";
import { SettingsCenter } from "./components/SettingsCenter";
import { Sidebar } from "./components/Sidebar";
import { ProfessionalReportAuthoring } from "./components/ProfessionalReportAuthoring";
import { LanguageProvider, useLanguage } from "./lib/language";
import { ThemeProvider, useTheme } from "./lib/theme";
import { loadUserPrefs } from "./lib/userPrefs";
import {
  api,
  API_BASE_URL,
  AuthUser,
  ChatAttachmentContext,
  ChatStreamEvent,
  ChatMode,
  CreateDecisionLogRequest,
  exportChatMessage,
  GeneratedReport,
  GeneratedReportSummary,
  getAuthToken,
  ReportBlock,
  ReportExportFormat,
  ReportSchedule,
  ReportTemplate,
  Session,
} from "./services/api";

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  taskType?: string;
  context?: any;
  llmRoute?: string;
  status?: "pending" | "success" | "partial" | "error";
  qualityFlags?: Array<{
    code: string;
    severity: string;
    message: string;
    source: string;
  }>;
  alertsPrepended?: string[];
  enhancementApplied?: Record<string, string>;
  resolvedMode?: string;
}

type ViewMode = "chat" | "dashboard" | "report" | "report-authoring" | "settings" | "tracker";
type SettingsTab =
  | "users"
  | "data"
  | "security"
  | "logs"
  | "language"
  | "appearance";

const CHAT_CACHE_VERSION = "v1";

interface CachedChatState {
  sessions: Session[];
  currentSessionId: string | null;
  savedAt: number;
}

function buildChatCacheKey(userId: string, suffix: string): string {
  return `edia_chat_cache_${CHAT_CACHE_VERSION}_${userId}_${suffix}`;
}

function readJsonCache<T>(key: string): T | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return null;
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

function writeJsonCache(key: string, value: unknown) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Ignore quota and serialization failures.
  }
}

function removeChatCache(userId: string) {
  if (typeof window === "undefined") return;
  const prefix = `edia_chat_cache_${CHAT_CACHE_VERSION}_${userId}_`;
  const keysToDelete: string[] = [];

  for (let index = 0; index < window.localStorage.length; index += 1) {
    const key = window.localStorage.key(index);
    if (key?.startsWith(prefix)) {
      keysToDelete.push(key);
    }
  }

  keysToDelete.forEach((key) => window.localStorage.removeItem(key));
}

function formatLoginError(error: unknown, language: string): string {
  const fallback = language === "en" ? "Login failed" : "登录失败";
  if (!(error instanceof Error)) {
    return fallback;
  }

  const diagnosticError = error as Error & {
    apiBaseUrl?: string;
  };
  const apiBaseUrl = diagnosticError.apiBaseUrl || API_BASE_URL;

  if (error.name === "BackendTimeoutError") {
    return language === "en"
      ? `Login timed out while waiting for ${apiBaseUrl}. The backend is online but too slow to answer.`
      : `登录等待 ${apiBaseUrl} 超时。后端在线，但响应过慢。`;
  }

  if (error.name === "BackendUnavailableError") {
    return language === "en"
      ? `Cannot reach backend ${apiBaseUrl}. Check the backend service, then retry.`
      : `无法连接后端 ${apiBaseUrl}。请检查后端服务后重试。`;
  }

  const message = error.message || "";
  if (error.name === "AbortError" || /aborted|timeout|timed out/i.test(message)) {
    return language === "en"
      ? "Login request timed out. Please confirm the backend service is running, then try again."
      : "登录请求超时，请确认后端服务正在运行后重试。";
  }

  return message || fallback;
}

function formatStreamingStageLabel(
  label: string,
  phase: string,
  language: string,
): string {
  if (!label) {
    return label;
  }

  if (phase === "started") {
    return language === "en" ? `${label} (in progress)` : `${label}（进行中）`;
  }

  return label;
}

function readCachedChatState(userId: string): CachedChatState | null {
  return readJsonCache<CachedChatState>(buildChatCacheKey(userId, "state"));
}

function writeCachedChatState(
  userId: string,
  payload: Omit<CachedChatState, "savedAt">,
) {
  writeJsonCache(buildChatCacheKey(userId, "state"), {
    ...payload,
    savedAt: Date.now(),
  });
}

function readCachedSessionHistory(
  userId: string,
  sessionId: string,
): ChatMessage[] | null {
  return readJsonCache<ChatMessage[]>(
    buildChatCacheKey(userId, `history_${sessionId}`),
  );
}

function writeCachedSessionHistory(
  userId: string,
  sessionId: string,
  messages: ChatMessage[],
) {
  writeJsonCache(buildChatCacheKey(userId, `history_${sessionId}`), messages);
}

function removeCachedSessionHistory(userId: string, sessionId: string) {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(
    buildChatCacheKey(userId, `history_${sessionId}`),
  );
}

function viewSwitcherClass(active: boolean): string {
  const base =
    "px-3 py-1.5 rounded-lg text-sm font-medium flex items-center gap-2 transition-colors";
  return active
    ? `${base} view-switcher-active bg-stone-900 text-white`
    : `${base} view-switcher-inactive text-slate-700 hover:bg-stone-100`;
}

function AppContent() {
  const { language } = useLanguage();
  const { toggleTheme, isDark } = useTheme();
  const text =
    language === "en"
      ? {
        checkingSession: "Checking session...",
        chat: "Chat",
        dashboard: "Dashboard",
        report: "Reports",
        administrator: "Administrator",
        user: "User",
        myProfile: "My Profile",
        administratorAccount: "Administrator account",
        standardAccount: "Standard account",
        settings: "Settings",
        language: "Language",
        teamPermissions: "Team & Permissions",
        signOut: "Sign out",
        switchToDark: "Switch to dark mode",
        switchToLight: "Switch to light mode",
        requestTimedOut:
          "⚠️ Request failed while the analysis was running. Please retry shortly.",
        unableToConnect:
          "⚠️ Unable to connect to the server. Please check the service and refresh the page.",
      }
      : {
        checkingSession: "检查会话中...",
        chat: "智能对话",
        dashboard: "驾驶舱",
        report: "报告",
        administrator: "管理员",
        user: "普通用户",
        myProfile: "我的资料",
        administratorAccount: "管理员账户",
        standardAccount: "标准账户",
        settings: "设置",
        language: "语言",
        teamPermissions: "团队与权限",
        signOut: "退出登录",
        switchToDark: "切换到深色模式",
        switchToLight: "切换到浅色模式",
        requestTimedOut: "⚠️ 分析请求失败，请稍后重试。",
        unableToConnect: "⚠️ 无法连接到服务器，请检查服务状态后刷新页面。",
      };

  const [currentUser, setCurrentUser] = useState<AuthUser | null>(null);
  const [authState, setAuthState] = useState<
    "loading" | "authenticated" | "unauthenticated"
  >("loading");
  const [authError, setAuthError] = useState<string | null>(null);
  const [isSigningIn, setIsSigningIn] = useState(false);
  const [isUpdatingForcedPassword, setIsUpdatingForcedPassword] =
    useState(false);
  const [viewMode, setViewMode] = useState<ViewMode>("chat");
  const [weeklyReportToast, setWeeklyReportToast] = useState(false);
  const [hasVisitedDashboard, setHasVisitedDashboard] = useState(false);
  const [chatMode, setChatMode] = useState<ChatMode>("auto");
  const [resolvedChatMode, setResolvedChatMode] = useState<Exclude<
    ChatMode,
    "auto"
  > | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  const [isUserMenuOpen, setIsUserMenuOpen] = useState(false);
  const [settingsTab, setSettingsTab] = useState<SettingsTab>("users");
  const [composerText, setComposerText] = useState("");
  const [focusToken, setFocusToken] = useState(0);
  const [generatedReport, setGeneratedReport] =
    useState<GeneratedReport | null>(null);
  const [generatedReports, setGeneratedReports] = useState<
    GeneratedReportSummary[]
  >([]);
  const [isGeneratingReport, setIsGeneratingReport] = useState(false);
  const [isSavingReport, setIsSavingReport] = useState(false);
  const [isUpdatingReportStatus, setIsUpdatingReportStatus] = useState(false);
  const [reportSchedules, setReportSchedules] = useState<ReportSchedule[]>([]);
  const [isScheduleSaving, setIsScheduleSaving] = useState(false);
  const [reportTemplates, setReportTemplates] = useState<ReportTemplate[]>([]);
  const [isTemplateSaving, setIsTemplateSaving] = useState(false);
  const [templateSchedules, setTemplateSchedules] = useState<
    Record<string, ReportSchedule>
  >({});
  // Reference panel: stores refs of the currently open message
  const [refPanel, setRefPanel] = useState<{
    refs: ParsedRef[];
    msgId: string;
  } | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const streamBufferRef = useRef<string>("");
  const streamFlushTimerRef = useRef<number | null>(null);
  const activeStreamAbortRef = useRef<AbortController | null>(null);
  const userMenuRef = useRef<HTMLDivElement>(null);
  const hydratedChatCacheUserIdRef = useRef<string | null>(null);

  const unreadScheduledReportCount = generatedReports.filter(
    (report) => report.is_unread && report.report_type.startsWith("scheduled_"),
  ).length;

  // Proactive alerts state
  const [isAlertPanelOpen, setIsAlertPanelOpen] = useState(false);
  const [alertItems, setAlertItems] = useState<AlertItem[]>([]);
  const [alertUnreadCount, setAlertUnreadCount] = useState(0);
  const [alertLastScan, setAlertLastScan] = useState<string | null>(null);
  // Tracks IDs dismissed by the user so the next poll doesn't flash them back
  const pendingDismissIds = useRef<Set<string>>(new Set());

  // Restore authenticated session on mount
  useEffect(() => {
    let isMounted = true;

    const bootstrapAuth = async () => {
      try {
        const user = await api.getCurrentUser();
        if (!isMounted) return;
        setCurrentUser(user);
        setAuthState("authenticated");
      } catch {
        if (!isMounted) return;
        setCurrentUser(null);
        setAuthState("unauthenticated");
      }
    };

    bootstrapAuth();

    return () => {
      isMounted = false;
    };
  }, []);

  useEffect(() => {
    if (viewMode === "dashboard") {
      setHasVisitedDashboard(true);
    }
  }, [viewMode]);

  useEffect(() => {
    if (
      authState !== "authenticated" ||
      !currentUser ||
      currentUser.must_change_password
    ) {
      setSessions([]);
      setGeneratedReports([]);
      setGeneratedReport(null);
      setReportSchedules([]);
      setReportTemplates([]);
      setTemplateSchedules({});
      return;
    }
    loadSessions();
    loadGeneratedReports();
    loadReportSchedules();
    loadReportTemplates();
  }, [authState, currentUser?.id, currentUser?.must_change_password]);

  useEffect(() => {
    if (
      authState !== "authenticated" ||
      !currentUser ||
      currentUser.must_change_password
    ) {
      hydratedChatCacheUserIdRef.current = null;
      return;
    }

    if (hydratedChatCacheUserIdRef.current === currentUser.id) {
      return;
    }
    hydratedChatCacheUserIdRef.current = currentUser.id;

    const cachedState = readCachedChatState(currentUser.id);
    if (!cachedState) return;

    if (sessions.length === 0 && cachedState.sessions.length > 0) {
      setSessions(cachedState.sessions);
    }

    if (!currentSessionId && cachedState.currentSessionId) {
      const cachedMessages = readCachedSessionHistory(
        currentUser.id,
        cachedState.currentSessionId,
      );

      setCurrentSessionId(cachedState.currentSessionId);
      setMessages(cachedMessages ?? []);
    }
  }, [authState, currentUser, sessions.length]);

  useEffect(() => {
    if (
      authState !== "authenticated" ||
      !currentUser ||
      currentUser.must_change_password
    ) {
      return;
    }

    writeCachedChatState(currentUser.id, {
      sessions,
      currentSessionId,
    });
  }, [authState, currentSessionId, currentUser, sessions]);

  useEffect(() => {
    if (
      authState !== "authenticated" ||
      !currentUser ||
      currentUser.must_change_password ||
      !currentSessionId
    ) {
      return;
    }

    writeCachedSessionHistory(currentUser.id, currentSessionId, messages);
  }, [authState, currentSessionId, currentUser, messages]);

  useEffect(() => {
    if (authState !== "authenticated" || currentUser?.must_change_password) {
      return;
    }

    const refreshReports = () => {
      loadGeneratedReports();
      loadReportSchedules();
    };

    refreshReports();
    const timer = window.setInterval(refreshReports, 60 * 1000);
    return () => window.clearInterval(timer);
  }, [authState, currentUser?.must_change_password]);

  // Poll /api/alerts every 2 minutes and on mount
  useEffect(() => {
    if (authState !== "authenticated" || currentUser?.must_change_password) {
      setAlertItems([]);
      setAlertUnreadCount(0);
      setAlertLastScan(null);
      return;
    }

    const fetchAlerts = async () => {
      try {
        const data = await api.getAlerts();
        const incoming: AlertItem[] = data.alerts ?? [];
        // Filter out any IDs the user already dismissed locally
        const filtered = incoming.filter(
          (a) => !pendingDismissIds.current.has(a.id),
        );
        // Once MySQL no longer returns an ID we can stop tracking it
        pendingDismissIds.current.forEach((id) => {
          if (!incoming.find((a) => a.id === id))
            pendingDismissIds.current.delete(id);
        });
        setAlertItems(filtered);
        setAlertUnreadCount(data.unread_count ?? 0);
        setAlertLastScan(data.last_scan ?? null);
      } catch {
        // Ignore intermittent auth/network errors here; the session bootstrap handles the main login state.
      }
    };
    fetchAlerts();
    const timer = window.setInterval(fetchAlerts, 2 * 60 * 1000);
    return () => window.clearInterval(timer);
  }, [authState, currentUser?.must_change_password]);

  // Scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Close user menu on outside click
  useEffect(() => {
    const onDocumentClick = (event: MouseEvent) => {
      if (!userMenuRef.current) return;
      if (!userMenuRef.current.contains(event.target as Node)) {
        setIsUserMenuOpen(false);
      }
    };

    document.addEventListener("mousedown", onDocumentClick);
    return () => document.removeEventListener("mousedown", onDocumentClick);
  }, []);

  // Morning briefing callback (SSE connection now managed by LiveEventContext)
  const handleMorningBriefing = (payload: { markdown?: string; date?: string }) => {
    if (payload.markdown) {
      setMessages((prev) => [
        ...prev,
        {
          id: `briefing-${Date.now()}`,
          role: "assistant" as const,
          content: payload.markdown!,
          taskType: "morning_briefing",
          status: "success" as const,
        },
      ]);
    }
  };

  const loadSessions = async () => {
    try {
      const data = await api.getSessions();
      setSessions(data);
    } catch (error) {
      console.error("Failed to load sessions", error);
    }
  };

  const loadGeneratedReports = async () => {
    try {
      const data = await api.listGeneratedReports();
      setGeneratedReports(data);
    } catch (error) {
      console.error("Failed to load generated reports", error);
    }
  };

  const loadReportSchedules = async () => {
    try {
      const data = await api.listReportSchedules();
      setReportSchedules(data);
    } catch (error) {
      console.error("Failed to load report schedules", error);
    }
  };

  const loadReportTemplates = async () => {
    try {
      const data = await api.listReportTemplates();
      setReportTemplates(data);
      const schedulePairs = await Promise.all(
        data.map(async (template) => {
          try {
            const schedule = await api.getReportTemplateSchedule(template.id);
            return [template.id, schedule] as const;
          } catch {
            return null;
          }
        }),
      );
      const nextMap: Record<string, ReportSchedule> = {};
      for (const item of schedulePairs) {
        if (!item) continue;
        const [templateId, schedule] = item;
        nextMap[templateId] = schedule;
      }
      setTemplateSchedules(nextMap);
    } catch (error) {
      console.error("Failed to load report templates", error);
    }
  };

  const handleSelectSession = async (sessionId: string) => {
    setCurrentSessionId(sessionId);
    setGeneratedReport(null);
    setRefPanel(null);
    setViewMode("chat"); // Switch to chat mode when selecting a session
    const cachedMessages = currentUser
      ? readCachedSessionHistory(currentUser.id, sessionId)
      : null;
    setMessages(cachedMessages ?? []);
    setIsLoading(true);
    try {
      const history = await api.getSessionHistory(sessionId);
      const nextMessages = history.map((msg) => ({
        id: msg.id,
        role: msg.role,
        content: msg.content,
        context: (msg as any).context || undefined,
        llmRoute: (msg as any).context?.llm_route || undefined,
        qualityFlags: (msg as any).qualityFlags || (msg as any).quality_flags || (msg as any).context?.quality_flags || [],
        alertsPrepended: (msg as any).alertsPrepended || (msg as any).alerts_prepended || (msg as any).context?.alerts_prepended || [],
        enhancementApplied: (msg as any).enhancementApplied || (msg as any).enhancement_applied || (
          (msg as any).context?.response_quality?.executive_answer_quality
            ? { executive_answer_quality: (msg as any).context.response_quality.executive_answer_quality }
            : {}
        ),
      }));
      setMessages(nextMessages);
      if (currentUser) {
        writeCachedSessionHistory(currentUser.id, sessionId, nextMessages);
      }
      // On mobile, close sidebar after selection
      if (window.innerWidth < 768) {
        setIsSidebarOpen(false);
      }
    } catch (error) {
      console.error("Failed to load history", error);
    } finally {
      setIsLoading(false);
    }
  };

  const handleNewSession = () => {
    setCurrentSessionId(null);
    setMessages([]);
    setComposerText("");
    setResolvedChatMode(null);
    setGeneratedReport(null);
    setRefPanel(null);
    setViewMode("chat"); // Switch to chat mode for new session
    if (window.innerWidth < 768) {
      setIsSidebarOpen(false);
    }
  };

  const handleRenameSession = async (sessionId: string, nextTitle: string) => {
    try {
      await api.renameSession(sessionId, nextTitle);
      setSessions((prev) =>
        prev.map((s) => (s.id === sessionId ? { ...s, title: nextTitle } : s)),
      );
    } catch (error) {
      console.error("Failed to rename session", error);
      window.alert(
        error instanceof Error ? error.message : "Failed to rename session",
      );
    }
  };

  const handleDeleteSession = async (sessionId: string) => {
    try {
      await api.deleteSession(sessionId);
      setSessions((prev) => prev.filter((s) => s.id !== sessionId));
      if (currentUser) {
        removeCachedSessionHistory(currentUser.id, sessionId);
      }

      if (currentSessionId === sessionId) {
        setCurrentSessionId(null);
        setMessages([]);
        setGeneratedReport(null);
        setRefPanel(null);
      }
    } catch (error) {
      console.error("Failed to delete session", error);
      window.alert(
        error instanceof Error ? error.message : "Failed to delete session",
      );
    }
  };

  const handleSendMessage = async (
    content: string,
    attachmentContexts: ChatAttachmentContext[] = [],
  ) => {
    const streamStartedAt = performance.now();
    let firstDeltaAt: number | null = null;
    const streamController = new AbortController();
    activeStreamAbortRef.current = streamController;

    const updatePendingStreamMeta = (patch: Record<string, any>) => {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === pendingAiId
            ? {
              ...m,
              context: {
                ...(m.context || {}),
                _stream: {
                  ...((m.context || {})._stream || {}),
                  ...patch,
                },
              },
            }
            : m,
        ),
      );
    };

    const flushStreamBuffer = (messageId: string) => {
      if (!streamBufferRef.current) {
        streamFlushTimerRef.current = null;
        return;
      }

      const bufferedDelta = streamBufferRef.current;
      streamBufferRef.current = "";
      streamFlushTimerRef.current = null;

      setMessages((prev) =>
        prev.map((m) =>
          m.id === messageId
            ? {
              ...m,
              content: `${m.content}${bufferedDelta}`,
            }
            : m,
        ),
      );
    };

    const scheduleStreamFlush = (messageId: string, immediate = false) => {
      if (immediate) {
        if (streamFlushTimerRef.current !== null) {
          window.clearTimeout(streamFlushTimerRef.current);
          streamFlushTimerRef.current = null;
        }
        flushStreamBuffer(messageId);
        return;
      }
      if (streamFlushTimerRef.current !== null) return;
      streamFlushTimerRef.current = window.setTimeout(() => {
        flushStreamBuffer(messageId);
      }, 32);
    };

    setComposerText("");
    if (chatMode === "auto") {
      setResolvedChatMode(null);
    }
    const tempId = Date.now().toString();
    const userMsg: ChatMessage = { id: tempId, role: "user", content };
    const pendingAiId = `${tempId}-ai`;
    const pendingAiMsg: ChatMessage = {
      id: pendingAiId,
      role: "assistant",
      content: "",
      status: "pending",
      context: {
        _stream: {
          status: "preparing",
          startedAtMs: streamStartedAt,
          currentStage: "prepare",
          currentStageLabel: language === "en" ? "Preparing analysis..." : "准备分析中...",
          stages: [
            {
              stage: "prepare",
              label: language === "en" ? "Preparing analysis..." : "准备分析中...",
              elapsedMs: 0,
            },
          ],
        },
      },
    };

    setMessages((prev) => [...prev, userMsg, pendingAiMsg]);
    setIsLoading(true);
    streamBufferRef.current = "";

    try {
      let streamedContent = "";

      const response = await api.sendMessageStream(
        content,
        currentSessionId || undefined,
        loadUserPrefs(language),
        attachmentContexts,
        (delta) => {
          if (firstDeltaAt === null) {
            firstDeltaAt = performance.now();
            updatePendingStreamMeta({
              status: "streaming",
              ttftMs: Math.max(0, Math.round(firstDeltaAt - streamStartedAt)),
              currentStage: "generate_report",
              currentStageLabel:
                language === "en"
                  ? "Streaming final answer..."
                  : "正在流式生成答案...",
            });
          }
          streamedContent += delta;
          const isFirstChunk = streamBufferRef.current.length === 0;
          streamBufferRef.current += delta;
          scheduleStreamFlush(pendingAiId, isFirstChunk);
        },
        (event: ChatStreamEvent) => {
          if (event.event === "stage") {
            const stage = String(event.data?.stage || "");
            const label = String(event.data?.label || "");
            const phase = String(event.data?.phase || "completed");
            const elapsed = Number(event.data?.elapsed_ms || 0);
            setMessages((prev) =>
              prev.map((m) => {
                if (m.id !== pendingAiId) return m;
                const stream = ((m.context || {})._stream || {}) as Record<string, any>;
                const prevStages = Array.isArray(stream.stages) ? [...stream.stages] : [];
                const last = prevStages.length > 0 ? prevStages[prevStages.length - 1] : null;
                const normalizedElapsed = Number.isFinite(elapsed)
                  ? Math.max(0, Math.round(elapsed))
                  : undefined;
                const shouldAppend = !last || String(last.stage || "") !== stage;
                if (shouldAppend) {
                  prevStages.push({
                    stage,
                    label,
                    phase,
                    elapsedMs: normalizedElapsed,
                  });
                } else {
                  prevStages[prevStages.length - 1] = {
                    ...last,
                    label: label || String(last.label || ""),
                    phase,
                    elapsedMs:
                      normalizedElapsed !== undefined
                        ? normalizedElapsed
                        : (last as any)?.elapsedMs,
                  };
                }

                return {
                  ...m,
                  context: {
                    ...(m.context || {}),
                    _stream: {
                      ...stream,
                      stages: prevStages,
                    },
                  },
                };
              }),
            );
            updatePendingStreamMeta({
              status: "preparing",
              currentStage: stage,
              currentStageLabel: formatStreamingStageLabel(label, phase, language),
              lastStageElapsedMs: phase === "completed" && Number.isFinite(elapsed)
                ? Math.round(elapsed)
                : undefined,
            });
          } else if (event.event === "meta") {
            const streamSessionId = String(event.data?.session_id || "");
            if (streamSessionId) {
              updatePendingStreamMeta({ sessionId: streamSessionId });
              if (!currentSessionId) {
                setCurrentSessionId(streamSessionId);
                void loadSessions();
              }
            }
          } else if (event.event === "preview") {
            // Fast planner-based preview arrives before the full LLM answer.
            // Store it so ChatMessage can render a "Quick Take" card immediately.
            const previewPayload = event.data as PreviewData;
            setMessages((prev) =>
              prev.map((m) => {
                if (m.id !== pendingAiId) return m;
                return {
                  ...m,
                  context: {
                    ...(m.context || {}),
                    previewData: previewPayload,
                  },
                };
              }),
            );
          }
        },
        chatMode,
        streamController.signal,
      );

      setResolvedChatMode(response.resolved_chat_mode || null);

      if (streamFlushTimerRef.current !== null) {
        window.clearTimeout(streamFlushTimerRef.current);
        streamFlushTimerRef.current = null;
      }
      flushStreamBuffer(pendingAiId);

      const resolvedStatus =
        response.status === "error"
          ? "error"
          : response.status === "partial"
            ? "partial"
            : "success";
      const finalResponseMarkdown = String(response.markdown || "");
      const streamedTrimmed = String(streamedContent || "").trim();
      const finalTrimmed = finalResponseMarkdown.trim();
      const resolvedContent =
        streamedTrimmed.length >= 40
          ? streamedContent
          : resolvedStatus === "success" && finalTrimmed
            ? finalResponseMarkdown
            : streamedContent ||
            finalResponseMarkdown ||
            response.error_message ||
            text.unableToConnect;

      setMessages((prev) =>
        prev.map((m) =>
          m.id === pendingAiId
            ? {
              ...m,
              role: "assistant",
              content: resolvedContent,
              taskType: response.task_type,
              llmRoute:
                response.llm_route ||
                response.context?.llm_route ||
                undefined,
              status: resolvedStatus,
              qualityFlags: response.quality_flags || [],
              alertsPrepended: response.alerts_prepended || [],
              enhancementApplied: response.enhancement_applied || {},
              resolvedMode: response.resolved_chat_mode || undefined,
              context: {
                ...(response.context || {}),
                _stream: {
                  ...((m.context || {})._stream || {}),
                  status: "completed",
                  currentStage: "completed",
                  currentStageLabel:
                    language === "en" ? "Completed" : "已完成",
                  totalMs: Math.max(0, Math.round(performance.now() - streamStartedAt)),
                },
              },
            }
            : m,
        ),
      );

      // Weekly report: show a toast/banner pointing user to Reports > 周报 tab
      if (response.task_type === "weekly_report") {
        setWeeklyReportToast(true);
        setTimeout(() => setWeeklyReportToast(false), 12000);
      }

      if (!currentSessionId && response.session_id) {
        setCurrentSessionId(response.session_id);
        await loadSessions();
      }
    } catch (error: any) {
      if (streamFlushTimerRef.current !== null) {
        window.clearTimeout(streamFlushTimerRef.current);
        streamFlushTimerRef.current = null;
      }
      flushStreamBuffer(pendingAiId);
      console.error(error);
      const stoppedByUser =
        streamController.signal.aborted &&
        (error?.name === "AbortError" || /abort/i.test(String(error?.message || "")));

      if (stoppedByUser) {
        const stoppedMessage = language === "en" ? "Response stopped." : "已停止生成。";
        const stoppedLabel = language === "en" ? "Stopped" : "已停止";
        setMessages((prev) =>
          prev.map((m) =>
            m.id === pendingAiId
              ? {
                ...m,
                role: "assistant",
                content: String(m.content || "").trim() ? m.content : stoppedMessage,
                status: "partial",
                context: {
                  ...(m.context || {}),
                  _stream: {
                    ...((m.context || {})._stream || {}),
                    status: "stopped",
                    currentStage: "stopped",
                    currentStageLabel: stoppedLabel,
                    totalMs: Math.max(0, Math.round(performance.now() - streamStartedAt)),
                  },
                },
              }
              : m,
          ),
        );
        return;
      }

      const detail = String(error?.message || "");
      const errMsg =
        error?.name === "AbortError"
          ? text.requestTimedOut
          : detail || text.unableToConnect;
      setMessages((prev) =>
        prev.map((m) =>
          m.id === pendingAiId
            ? {
              ...m,
              role: "assistant",
              content: errMsg,
              status: "error",
              context: {
                ...(m.context || {}),
                _stream: {
                  ...((m.context || {})._stream || {}),
                  status: "error",
                  totalMs: Math.max(0, Math.round(performance.now() - streamStartedAt)),
                },
              },
            }
            : m,
        ),
      );
    } finally {
      streamBufferRef.current = "";
      if (activeStreamAbortRef.current === streamController) {
        activeStreamAbortRef.current = null;
      }
      setIsLoading(false);
    }
  };

  const handleStopStreaming = () => {
    activeStreamAbortRef.current?.abort();
  };

  const handleSuggestionToInput = (text: string) => {
    setComposerText(text);
    setFocusToken((prev) => prev + 1);
  };

  const handleGenerateReport = async () => {
    if (!currentSessionId || isGeneratingReport) return;
    setIsGeneratingReport(true);
    try {
      const createdReport = await api.createGeneratedReport(currentSessionId);
      setGeneratedReport(createdReport);
      await loadGeneratedReports();
      setViewMode("report");
    } catch (error) {
      console.error("Failed to generate report", error);
      window.alert(
        error instanceof Error ? error.message : "Failed to generate report",
      );
    } finally {
      setIsGeneratingReport(false);
    }
  };

  const triggerDownload = (blob: Blob, filename: string) => {
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => window.URL.revokeObjectURL(url), 1000);
  };

  const handleExportGeneratedReport = async (
    reportId: string,
    format: ReportExportFormat,
  ) => {
    try {
      const download = await api.exportGeneratedReport(reportId, format);
      triggerDownload(download.blob, download.filename);
      await loadGeneratedReports();
    } catch (error) {
      console.error("Failed to export report", error);
      window.alert(
        error instanceof Error ? error.message : "Failed to export report",
      );
    }
  };

  const handleSaveGeneratedReport = async (
    reportId: string,
    payload: { title: string; markdown: string; blocks?: ReportBlock[] },
  ) => {
    setIsSavingReport(true);
    try {
      const updated = await api.updateGeneratedReport(reportId, payload);
      setGeneratedReport(updated);
      await loadGeneratedReports();
    } catch (error) {
      console.error("Failed to save report", error);
      window.alert(
        error instanceof Error ? error.message : "Failed to save report",
      );
    } finally {
      setIsSavingReport(false);
    }
  };

  const handleRefreshGeneratedReport = async (reportId: string) => {
    setIsGeneratingReport(true);
    try {
      const refreshed = await api.refreshGeneratedReport(reportId);
      setGeneratedReport(refreshed.report);
      await loadGeneratedReports();
    } catch (error) {
      console.error("Failed to refresh report blocks", error);
      window.alert(
        error instanceof Error
          ? error.message
          : "Failed to refresh report blocks",
      );
    } finally {
      setIsGeneratingReport(false);
    }
  };

  const handleUpdateGeneratedReportStatus = async (
    reportId: string,
    status: "draft" | "approved",
  ) => {
    setIsUpdatingReportStatus(true);
    try {
      const updated = await api.updateGeneratedReportStatus(reportId, {
        status,
      });
      setGeneratedReport(updated);
      await loadGeneratedReports();
    } catch (error) {
      console.error("Failed to update report status", error);
      window.alert(
        error instanceof Error
          ? error.message
          : "Failed to update report status",
      );
    } finally {
      setIsUpdatingReportStatus(false);
    }
  };

  const handleExportCurrentSessionReport = async (
    format: ReportExportFormat,
  ) => {
    if (!currentSessionId || isGeneratingReport) return;
    setIsGeneratingReport(true);
    try {
      const createdReport = await api.createGeneratedReport(currentSessionId);
      setGeneratedReport(createdReport);
      await loadGeneratedReports();
      const download = await api.exportGeneratedReport(
        createdReport.id,
        format,
      );
      triggerDownload(download.blob, download.filename);
    } catch (error) {
      console.error("Failed to export current session report", error);
      window.alert(
        error instanceof Error
          ? error.message
          : "Failed to export current session report",
      );
    } finally {
      setIsGeneratingReport(false);
    }
  };

  const handleAddCurrentSessionToWeeklyReport = async () => {
    if (!currentSessionId || isGeneratingReport) return;
    setIsGeneratingReport(true);
    try {
      const draftReport = await api.addSessionToWeeklyReport(currentSessionId);
      setGeneratedReport(draftReport);
      setViewMode("report");
      await loadGeneratedReports();
    } catch (error) {
      console.error("Failed to add session to weekly report", error);
      window.alert(
        error instanceof Error
          ? error.message
          : "Failed to add session to weekly report",
      );
    } finally {
      setIsGeneratingReport(false);
    }
  };

  const handleSaveToDecisionLog = async (messageContent: string) => {
    // Extract product and price info from message text heuristically.
    const priceRangeMatch = messageContent.match(
      /(?:[¥￥\$]?\s*)([\d,]{3,}(?:\.\d+)?)\s*[–\-—~～]\s*(?:[¥￥\$]?\s*)([\d,]{3,}(?:\.\d+)?)/,
    );
    const singlePriceMatch = messageContent.match(
      /(?:价格|报价|price|建议价|recommended price)[^\d]{0,12}([\d,]{3,}(?:\.\d+)?)/i,
    );

    const parsedLow = priceRangeMatch
      ? parseFloat(priceRangeMatch[1].replace(/,/g, ""))
      : singlePriceMatch
        ? parseFloat(singlePriceMatch[1].replace(/,/g, ""))
        : 0;
    const parsedHigh = priceRangeMatch
      ? parseFloat(priceRangeMatch[2].replace(/,/g, ""))
      : parsedLow;

    const low = Number.isFinite(parsedLow) ? parsedLow : 0;
    const high = Number.isFinite(parsedHigh) ? parsedHigh : low;

    const confidenceMatch = messageContent.match(
      /(confidence|置信度)[^\d]{0,10}(\d{1,3})(?:\s*%|\b)/i,
    );
    const confidencePct = confidenceMatch
      ? Math.max(0, Math.min(100, parseFloat(confidenceMatch[2])))
      : null;

    const lower = messageContent.toLowerCase();
    const productMap: Array<{ id: string; label: string; hints: string[] }> = [
      { id: "naphtha", label: "Naphtha", hints: ["naphtha", "石脑油"] },
      {
        id: "cracked_c5",
        label: "Cracked C5",
        hints: ["cracked c5", "裂解c5"],
      },
      {
        id: "cracked_c9",
        label: "Cracked C9",
        hints: ["cracked c9", "裂解c9"],
      },
      {
        id: "isoprene",
        label: "Isoprene",
        hints: ["isoprene", "异戊二烯", "ip"],
      },
      {
        id: "piperylene",
        label: "Piperylene",
        hints: ["piperylene", "间戊二烯", "pip"],
      },
      { id: "dcpd", label: "DCPD", hints: ["dcpd", "双环戊二烯"] },
      { id: "sis", label: "SIS", hints: ["sis", "苯乙烯-异戊二烯-苯乙烯"] },
      { id: "c5_resin", label: "C5 Resin", hints: ["c5 resin", "c5树脂"] },
      { id: "c9_resin", label: "C9 Resin", hints: ["c9 resin", "c9树脂"] },
      {
        id: "blowing_agent",
        label: "Blowing Agent",
        hints: ["blowing agent", "发泡剂"],
      },
    ];
    const inferredProduct =
      productMap.find((item) =>
        item.hints.some((hint) => lower.includes(hint)),
      ) ?? null;

    const inferPriceUnit = (): string => {
      if (/(usd|us\$|\$|美元)\s*(?:\/|per)?\s*(ton|t|mt|吨)?/i.test(messageContent)) {
        return inferredProduct?.id === "crude_oil" ? "USD/bbl" : "USD/ton";
      }
      if (/(cny|rmb|¥|￥|人民币|元\s*(?:\/|每)?\s*(吨|ton|t)?)/i.test(messageContent)) {
        return "CNY/ton";
      }
      if (inferredProduct?.id === "crude_oil") return "USD/bbl";
      if (inferredProduct?.id === "naphtha") return "USD/ton";
      return "CNY/ton";
    };

    let inferredAction = "hold";
    if (/(raise|increase|提价|上调|tighten|bullish)/i.test(messageContent)) {
      inferredAction = "raise";
    } else if (/(lower|cut|discount|降价|下调|bearish)/i.test(messageContent)) {
      inferredAction = "lower";
    }

    const req: CreateDecisionLogRequest = {
      product_id: inferredProduct?.id ?? "unknown",
      product_name: inferredProduct?.label ?? "From chat",
      ai_price_low: low,
      ai_price_high: high,
      price_unit: inferPriceUnit(),
      ai_action: inferredAction,
      ai_confidence:
        confidencePct == null || Number.isNaN(confidencePct)
          ? null
          : confidencePct / 100,
      ai_reasoning: messageContent.slice(0, 2000),
      session_id: currentSessionId ?? undefined,
    };
    try {
      await api.createDecisionLog(req);
      setViewMode("tracker");
    } catch (err) {
      console.error("Failed to save decision log", err);
      const detail = err instanceof Error ? err.message : "";
      window.alert(
        language === "en"
          ? `Failed to save${detail ? `: ${detail}` : ""}`
          : `保存失败${detail ? `：${detail}` : ""}`,
      );
    }
  };

  const handleOpenGeneratedReport = async (reportId: string) => {
    if (isGeneratingReport) return;
    setIsGeneratingReport(true);
    try {
      const report = await api.getGeneratedReport(reportId);
      setGeneratedReport(report);
      setViewMode("report");
      await loadGeneratedReports();
    } catch (error) {
      console.error("Failed to open generated report", error);
      window.alert(
        error instanceof Error
          ? error.message
          : "Failed to open generated report",
      );
    } finally {
      setIsGeneratingReport(false);
    }
  };

  const handleSaveReportSchedule = async (
    reportType: string,
    payload: { enabled: boolean; hour: number; minute: number },
  ) => {
    setIsScheduleSaving(true);
    try {
      const schedule = await api.updateReportSchedule(reportType, payload);
      setReportSchedules((prev) => {
        const next = prev.filter((item) => item.report_type !== reportType);
        return [...next, schedule].sort((left, right) =>
          left.report_type.localeCompare(right.report_type),
        );
      });
    } catch (error) {
      console.error("Failed to save report schedule", error);
      window.alert(
        error instanceof Error
          ? error.message
          : "Failed to save report schedule",
      );
    } finally {
      setIsScheduleSaving(false);
    }
  };

  const handleRunReportNow = async (reportType: string) => {
    setIsScheduleSaving(true);
    try {
      const report = await api.runReportNow(reportType);
      setGeneratedReport(report);
      setViewMode("report");
      await loadGeneratedReports();
      await loadReportSchedules();
    } catch (error) {
      console.error("Failed to run report", error);
      window.alert(
        error instanceof Error ? error.message : "Failed to run report",
      );
    } finally {
      setIsScheduleSaving(false);
    }
  };

  const handleGenerateReportFromTemplate = async (templateId: string) => {
    setIsGeneratingReport(true);
    try {
      const report = await api.generateReportFromTemplate(templateId);
      setGeneratedReport(report);
      setViewMode("report");
      await loadGeneratedReports();
    } catch (error) {
      console.error("Failed to generate report from template", error);
      window.alert(
        error instanceof Error
          ? error.message
          : "Failed to generate report from template",
      );
    } finally {
      setIsGeneratingReport(false);
    }
  };

  const handleSaveReportTemplate = async (payload: {
    id?: string;
    name: string;
    description?: string;
    time_period: string;
    audience?: string;
    schedule_hint?: string;
    sections: string[];
    template_title?: string;
    template_markdown?: string;
    template_blocks?: ReportBlock[];
  }) => {
    setIsTemplateSaving(true);
    try {
      await api.saveReportTemplate(payload);
      await loadReportTemplates();
    } catch (error) {
      console.error("Failed to save report template", error);
      window.alert(
        error instanceof Error
          ? error.message
          : "Failed to save report template",
      );
    } finally {
      setIsTemplateSaving(false);
    }
  };

  const handleDeleteReportTemplate = async (templateId: string) => {
    setIsTemplateSaving(true);
    try {
      await api.deleteReportTemplate(templateId);
      await loadReportTemplates();
    } catch (error) {
      console.error("Failed to delete report template", error);
      window.alert(
        error instanceof Error
          ? error.message
          : "Failed to delete report template",
      );
    } finally {
      setIsTemplateSaving(false);
    }
  };

  const handleSaveTemplateSchedule = async (
    templateId: string,
    payload: { enabled: boolean; hour: number; minute: number },
  ) => {
    setIsScheduleSaving(true);
    try {
      const schedule = await api.updateReportTemplateSchedule(templateId, payload);
      setTemplateSchedules((prev) => ({ ...prev, [templateId]: schedule }));
    } catch (error) {
      console.error("Failed to save template schedule", error);
      window.alert(
        error instanceof Error
          ? error.message
          : "Failed to save template schedule",
      );
    } finally {
      setIsScheduleSaving(false);
    }
  };

  const handleRunTemplateNow = async (templateId: string) => {
    setIsScheduleSaving(true);
    try {
      const report = await api.runReportTemplateNow(templateId);
      setGeneratedReport(report);
      setViewMode("report");
      await loadGeneratedReports();
      await loadReportTemplates();
    } catch (error) {
      console.error("Failed to run template now", error);
      window.alert(
        error instanceof Error ? error.message : "Failed to run template now",
      );
    } finally {
      setIsScheduleSaving(false);
    }
  };

  const handleInvestigateAlert = (question: string) => {
    setIsAlertPanelOpen(false);
    setViewMode("chat");
    setComposerText(question);
    setFocusToken((prev) => prev + 1);
  };

  const handleOpenAlertPanel = () => {
    setIsAlertPanelOpen(true);
    // Mark all as read — badge resets to 0
    api
      .markAlertsRead()
      .then(() => setAlertUnreadCount(0))
      .catch(() => {
        /* ignore */
      });
  };

  const handleDismissAlert = (alertId: string) => {
    // Immediately remove from UI and guard against poll race
    pendingDismissIds.current.add(alertId);
    setAlertItems((prev) => prev.filter((a) => a.id !== alertId));
    api.dismissAlert(alertId).catch(() => {
      // If the API call failed, remove from pending so poll can restore it
      pendingDismissIds.current.delete(alertId);
    });
  };

  const handleLogin = async (username: string, password: string) => {
    setIsSigningIn(true);
    setAuthError(null);
    try {
      const response = await api.login(username, password);
      setCurrentUser(response.user);
      setAuthState("authenticated");
      setViewMode("chat");
      setMessages([]);
      setCurrentSessionId(null);
      if (!response.user.must_change_password) {
        await loadSessions();
      }
    } catch (error) {
      console.error("Login failed", error);
      setAuthError(formatLoginError(error, language));
      setAuthState("unauthenticated");
      setCurrentUser(null);
    } finally {
      setIsSigningIn(false);
    }
  };

  const handleSignOut = async () => {
    const cachedUserId = currentUser?.id ?? null;
    await api.logout();
    setCurrentUser(null);
    setAuthState("unauthenticated");
    setAuthError(null);
    setMessages([]);
    setSessions([]);
    setCurrentSessionId(null);
    setGeneratedReports([]);
    setGeneratedReport(null);
    setReportSchedules([]);
    setViewMode("chat");
    setIsAlertPanelOpen(false);
    setAlertItems([]);
    setAlertUnreadCount(0);
    pendingDismissIds.current.clear();
    if (cachedUserId) {
      removeChatCache(cachedUserId);
    }
  };

  const handleForcedPasswordChange = async (
    currentPassword: string,
    newPassword: string,
  ) => {
    setIsUpdatingForcedPassword(true);
    setAuthError(null);
    try {
      const response = await api.changePassword({
        current_password: currentPassword,
        new_password: newPassword,
      });
      setCurrentUser(response.user);
      setAuthState("authenticated");
    } catch (error) {
      console.error("Forced password change failed", error);
      setAuthError(
        error instanceof Error ? error.message : "Password update failed",
      );
    } finally {
      setIsUpdatingForcedPassword(false);
    }
  };

  const handleUserMenuAction = (
    action: "settings" | "language" | "team" | "signout",
  ) => {
    setIsUserMenuOpen(false);

    if (action === "settings") {
      setSettingsTab("security");
      setViewMode("settings");
      return;
    }

    if (action === "language") {
      setSettingsTab("language");
      setViewMode("settings");
      return;
    }

    if (action === "team") {
      setSettingsTab("users");
      setViewMode("settings");
      return;
    }

    if (action === "signout") {
      handleSignOut();
      return;
    }

    console.info(`[UserMenu] ${action} clicked`);
  };

  if (authState === "loading") {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-50 text-slate-600">
        {text.checkingSession}
      </div>
    );
  }

  if (authState !== "authenticated" || !currentUser) {
    return (
      <LoginScreen
        onLogin={handleLogin}
        error={authError}
        isLoading={isSigningIn}
      />
    );
  }

  if (currentUser.must_change_password) {
    return (
      <ForcePasswordChangeScreen
        username={currentUser.username}
        onSubmit={handleForcedPasswordChange}
        isLoading={isUpdatingForcedPassword}
        error={authError}
      />
    );
  }

  return (
    <LiveEventProvider authState={authState} onMorningBriefing={handleMorningBriefing}>
    <div className="app-shell relative flex flex-col h-screen overflow-hidden bg-stone-50">
      {/* Top bar with logo and view switcher - full width */}
      <div className="relative h-14 shrink-0 hidden md:flex items-center justify-between px-4 bg-stone-50 border-b border-stone-200 z-20">
        <div className="flex items-center gap-3">
          {/* Logo */}
          <img src="/logo.png" alt="Logo" className="h-8 object-contain" />

          {/* View Switcher */}
          <div className="view-switcher-track flex rounded-xl border border-stone-200 bg-white p-1">
            <button
              onClick={() => setViewMode("chat")}
              className={viewSwitcherClass(viewMode === "chat")}
            >
              <MessageSquare size={16} /> {text.chat}
            </button>
            <button
              onClick={() => setViewMode("dashboard")}
              className={viewSwitcherClass(viewMode === "dashboard")}
            >
              <LayoutDashboard size={16} /> {text.dashboard}
            </button>
            <button
              onClick={() => setViewMode("report")}
              className={`relative ${viewSwitcherClass(viewMode === "report")}`}
            >
              <FileText size={16} /> {text.report}
              {unreadScheduledReportCount > 0 && (
                <span className="absolute -top-1 -right-1 min-w-[18px] h-[18px] flex items-center justify-center rounded-full bg-slate-900 px-1 text-[10px] font-bold leading-none text-white">
                  {unreadScheduledReportCount > 9
                    ? "9+"
                    : unreadScheduledReportCount}
                </span>
              )}
            </button>
            <button
              onClick={() => setViewMode("tracker")}
              className={viewSwitcherClass(viewMode === "tracker")}
            >
              <BookmarkPlus size={16} />{" "}
              {language === "en" ? "Decision Log" : "决策日志"}
            </button>
            <button
              onClick={() => setViewMode("report-authoring")}
              className={viewSwitcherClass(viewMode === "report-authoring")}
            >
              <FileText size={16} />
              {language === "en" ? "Report Builder" : "报告编辑器"}
            </button>
          </div>
        </div>

        {/* Right side: Theme + Bell + User Menu */}
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={toggleTheme}
            className="p-2 rounded-lg text-slate-600 hover:text-slate-800 hover:bg-slate-100 transition-colors"
            aria-label={isDark ? text.switchToLight : text.switchToDark}
            title={isDark ? text.switchToLight : text.switchToDark}
          >
            {isDark ? <Sun size={20} /> : <Moon size={20} />}
          </button>

          {/* Bell / Alert Button */}
          <button
            type="button"
            onClick={handleOpenAlertPanel}
            className="relative p-2 rounded-lg text-slate-600 hover:text-slate-800 hover:bg-slate-100 transition-colors"
            aria-label="Open alerts"
          >
            <Bell size={20} />
            {alertUnreadCount > 0 && (
              <span className="absolute -top-0.5 -right-0.5 min-w-[18px] h-[18px] flex items-center justify-center bg-red-500 text-white text-[10px] font-bold rounded-full px-1 leading-none">
                {alertUnreadCount > 9 ? "9+" : alertUnreadCount}
              </span>
            )}
          </button>

          {/* User Menu */}
          <div className="relative" ref={userMenuRef}>
            <button
              type="button"
              onClick={() => setIsUserMenuOpen((prev) => !prev)}
              className="flex items-center gap-2.5 px-3 py-1.5 rounded-lg hover:bg-slate-50 cursor-pointer transition-colors"
            >
              <div className="w-8 h-8 rounded-full bg-stone-200" />
              <div className="leading-tight text-left">
                <p className="text-sm font-medium text-slate-700">
                  {currentUser.username}
                </p>
                <p className="text-xs text-slate-500">
                  {currentUser.is_admin ? text.administrator : text.user}
                </p>
              </div>
              <ChevronDown
                size={16}
                className={`text-slate-500 transition-transform ${isUserMenuOpen ? "rotate-180" : ""}`}
              />
            </button>

            {isUserMenuOpen && (
              <div className="absolute right-0 top-12 w-72 bg-white border border-slate-200 rounded-xl shadow-lg p-2 z-40">
                <button
                  type="button"
                  className="w-full flex items-center gap-3 p-3 rounded-lg hover:bg-slate-50 transition-colors text-left"
                >
                  <div className="w-10 h-10 rounded-full bg-stone-200 shrink-0" />
                  <div>
                    <p className="text-sm font-semibold text-slate-800">
                      {text.myProfile}
                    </p>
                    <p className="text-sm text-slate-600">
                      {currentUser.username}
                    </p>
                    <p className="text-xs text-slate-500">
                      {currentUser.is_admin
                        ? text.administratorAccount
                        : text.standardAccount}
                    </p>
                  </div>
                </button>

                <div className="my-1 h-px bg-slate-100" />

                <button
                  type="button"
                  onClick={() => handleUserMenuAction("settings")}
                  className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm text-slate-700 hover:bg-slate-50 transition-colors"
                >
                  <Settings size={16} className="text-slate-500" />
                  {text.settings}
                </button>

                <button
                  type="button"
                  onClick={() => handleUserMenuAction("language")}
                  className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm text-slate-700 hover:bg-slate-50 transition-colors"
                >
                  <Languages size={16} className="text-slate-500" />
                  {text.language}
                </button>

                <button
                  type="button"
                  onClick={() => handleUserMenuAction("team")}
                  className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm text-slate-700 hover:bg-slate-50 transition-colors"
                >
                  <Users size={16} className="text-slate-500" />
                  {text.teamPermissions}
                </button>

                <div className="my-1 h-px bg-slate-100" />

                <button
                  type="button"
                  onClick={() => handleUserMenuAction("signout")}
                  className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm text-rose-600 hover:bg-rose-50 transition-colors"
                >
                  <LogOut size={16} />
                  {text.signOut}
                </button>
              </div>
            )}
          </div>
        </div>
        {/* end Right side: Bell + User Menu */}
      </div>

      {/* Content area below top bar */}
      <div className="relative flex flex-1 overflow-hidden">
        {/* Sidebar only visible on chat page */}
        {viewMode === "chat" && (
          <Sidebar
            sessions={sessions}
            currentSessionId={currentSessionId}
            onSelectSession={handleSelectSession}
            onNewSession={handleNewSession}
            onRenameSession={handleRenameSession}
            onDeleteSession={handleDeleteSession}
            isOpen={isSidebarOpen}
            onToggle={() => setIsSidebarOpen(!isSidebarOpen)}
          />
        )}

        <main className="flex-1 min-w-0 flex flex-col h-full relative transition-all duration-300">
          {/* Weekly report toast */}
          {weeklyReportToast && (
            <div className="absolute top-3 left-1/2 -translate-x-1/2 z-50 flex items-center gap-3 bg-blue-700 text-white text-sm px-4 py-2 rounded-lg shadow-lg">
              <span>📊 报告已生成 — 前往「报告 &gt; 周报」查看</span>
              <button
                className="underline text-blue-200 hover:text-white text-xs"
                onClick={() => { setViewMode('report'); setWeeklyReportToast(false); }}
              >
                立即查看
              </button>
              <button
                className="ml-1 text-blue-200 hover:text-white"
                onClick={() => setWeeklyReportToast(false)}
                aria-label="关闭"
              >
                ✕
              </button>
            </div>
          )}
          {/* Content Area */}
          {hasVisitedDashboard && (
            <div
              className={
                viewMode === "dashboard"
                  ? "flex-1 min-w-0 flex flex-col overflow-y-auto"
                  : "hidden"
              }
            >
              <Dashboard isActive={viewMode === "dashboard"} />
            </div>
          )}
          {viewMode === "dashboard" ? null : viewMode === "settings" ? (
            <div className="flex-1 flex flex-col overflow-hidden">
              <SettingsCenter
                initialTab={settingsTab}
                currentUser={currentUser}
                onCurrentUserChange={setCurrentUser}
              />
            </div>
          ) : viewMode === "tracker" ? (
            <div className="flex-1 flex flex-col overflow-hidden">
              <DecisionTracker currentUser={currentUser} />
            </div>
          ) : viewMode === "report-authoring" ? (
            <div className="report-authoring-shell flex-1 flex flex-col overflow-hidden bg-stone-50">
              <ProfessionalReportAuthoring />
            </div>
          ) : viewMode === "report" ? (
            <div className="flex-1 flex flex-col overflow-hidden">
              <ReportView />
            </div>
          ) : (
            <>
              {/* Chat + optional right reference panel */}
              <div className="flex-1 flex overflow-hidden min-h-0">
                {/* Messages + Input column */}
                <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
                  {/* Messages Area */}
                  <div className="flex-1 overflow-y-auto scrollbar-thin scrollbar-thumb-slate-200">
                    <div className="max-w-6xl mx-auto px-4 py-8 min-h-full flex flex-col">
                      {messages.length === 0 ? (
                        <div className="flex-1 flex flex-col justify-center">
                          <Empty
                            onSelectSuggestion={handleSendMessage}
                            currentUser={currentUser}
                            alerts={alertItems}
                          />
                        </div>
                      ) : (
                        <div className="space-y-6 pb-4">
                          {messages.map((msg) => (
                            <ChatMessage
                              key={msg.id}
                              message={msg}
                              onSelectSuggestion={handleSendMessage}
                              onOpenRefs={(refs) =>
                                setRefPanel((prev) =>
                                  prev?.msgId === msg.id
                                    ? null
                                    : { refs, msgId: msg.id },
                                )
                              }
                              refSourceOpen={refPanel?.msgId === msg.id}
                              onGenerateReport={handleGenerateReport}
                              onExportReport={handleExportCurrentSessionReport}
                              onAddToWeeklyReport={
                                handleAddCurrentSessionToWeeklyReport
                              }
                              onSaveToDecisionLog={handleSaveToDecisionLog}
                              canGenerateReport={
                                Boolean(currentSessionId) && !isGeneratingReport
                              }
                              resolvedMode={msg.resolvedMode}
                              onExportMessage={async (format) => {
                                const titleLine = (msg.content || "")
                                  .split("\n")[0]
                                  .replace(/^#+\s*/, "")
                                  .slice(0, 80);
                                await exportChatMessage(
                                  msg.content,
                                  titleLine || "Chat Export",
                                  format,
                                  msg.context?.visualizations || [],
                                );
                              }}
                            />
                          ))}
                          <div ref={messagesEndRef} />
                        </div>
                      )}
                    </div>
                  </div>

                  {/* Input Area */}
                  <div className="bg-gradient-to-b from-white/60 to-white/95 backdrop-blur-md px-6 py-4">
                    <div className="max-w-6xl mx-auto px-4 pl-[64px]">
                      <ChatInput
                        onSend={handleSendMessage}
                        onStop={handleStopStreaming}
                        isStreaming={isLoading}
                        disabled={isLoading}
                        value={composerText}
                        onChange={setComposerText}
                        focusToken={focusToken}
                        mode={chatMode}
                        resolvedMode={resolvedChatMode}
                        onModeChange={setChatMode}
                      />
                    </div>
                  </div>
                </div>
                {/* end left column */}

                {/* Right reference panel — slides in, same height as chat area */}
                {refPanel && (
                  <div className="w-[360px] flex-shrink-0 border-l border-slate-200 flex flex-col overflow-hidden animate-in slide-in-from-right duration-200">
                    <ReferencePanel
                      refs={refPanel.refs}
                      onClose={() => setRefPanel(null)}
                    />
                  </div>
                )}
              </div>
              {/* end flex row */}
            </>
          )}
        </main>
      </div>

      {isAlertPanelOpen && (
        <ProactiveAlerts
          alerts={alertItems}
          lastScan={alertLastScan}
          onClose={() => setIsAlertPanelOpen(false)}
          onInvestigate={handleInvestigateAlert}
          onDismiss={handleDismissAlert}
        />
      )}
    </div>
    </LiveEventProvider>
  );
}

function App() {
  return (
    <ThemeProvider>
      <LanguageProvider>
        <AppContent />
      </LanguageProvider>
    </ThemeProvider>
  );
}

export default App;
