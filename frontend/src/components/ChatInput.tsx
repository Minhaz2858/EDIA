import {
  ArrowUp,
  ChevronDown,
  Mic,
  Paperclip,
  Sparkles,
  X,
} from "lucide-react";
import React, {
  ClipboardEvent,
  KeyboardEvent,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useLanguage } from "../lib/language";
import {
  api,
  ChatAttachmentContext,
  ChatMode,
  ComposerSuggestionPayload,
} from "../services/api";

type SpeechRecognitionAlternative = {
  transcript: string;
};

type SpeechRecognitionResultLike = {
  isFinal: boolean;
  0: SpeechRecognitionAlternative;
};

type SpeechRecognitionEventLike = {
  resultIndex: number;
  results: ArrayLike<SpeechRecognitionResultLike>;
};

type SpeechRecognitionLike = {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onstart: null | (() => void);
  onresult: null | ((event: SpeechRecognitionEventLike) => void);
  onerror: null | ((event: { error?: string }) => void);
  onend: null | (() => void);
  start: () => void;
  stop: () => void;
};

type SpeechRecognitionConstructor = new () => SpeechRecognitionLike;

declare global {
  interface Window {
    SpeechRecognition?: SpeechRecognitionConstructor;
    webkitSpeechRecognition?: SpeechRecognitionConstructor;
  }
}

interface ChatInputProps {
  onSend: (
    message: string,
    attachmentContexts?: ChatAttachmentContext[],
  ) => void;
  onStop?: () => void;
  isStreaming?: boolean;
  disabled?: boolean;
  value?: string;
  onChange?: (value: string) => void;
  focusToken?: number;
  mode?: ChatMode;
  resolvedMode?: Exclude<ChatMode, "auto"> | null;
  onModeChange?: (mode: ChatMode) => void;
}

type SuggestionTemplate = {
  id: string;
  text: string;
  matchers?: string[];
  title?: string;
  reason?: string;
  source?: string;
};

type ShortcutTemplate = {
  key: string;
  label: string;
  template?: string;
  options?: SuggestionTemplate[];
};

export const ChatInput: React.FC<ChatInputProps> = ({
  onSend,
  onStop,
  isStreaming = false,
  disabled,
  value,
  onChange,
  focusToken,
  mode = "auto",
  resolvedMode,
  onModeChange,
}) => {
  const { language } = useLanguage();
  const text =
    language === "en"
      ? {
        placeholder: "Ask anything about prices, inventory, sales, competitors, or market trends...",
        shortcut: "Press Enter to send · Shift+Enter for new line",
        quickPrompt: "Quick tags",
        topicOptions: "Topic options",
        filesPrompt: "Files become context for the next question only",
        upload: "Upload file",
        uploading: "Uploading...",
        uploadHint: "Supports TXT, CSV, XLSX, PDF, MD, JSON up to 5 MB",
        removeFile: "Remove file",
        suggestions: "Smart suggestions",
        showSuggestions: "Show suggestions",
        hideSuggestions: "Hide suggestions",
        uploadFallbackPrompt:
          "Please analyze the uploaded file context and highlight key signals.",
        uploadFailed:
          "File upload failed. Please try a smaller or supported file.",
        activeTopic: "Selected topic",
        voiceInput: "Voice input",
        preparingVoice: "Preparing mic...",
        listening: "Listening...",
        stopListening: "Stop listening",
        stopGenerating: "Stop",
        assistantResponding:
          "Assistant is responding. You can stop generation at any time.",
        voiceUnsupported:
          "Voice input is not available in this browser. Use Chrome or Edge on HTTPS or localhost.",
        voiceDenied:
          "Microphone access was denied. Check browser permissions and try again.",
        voiceSetupFailed:
          "Microphone could not be initialized. Check browser microphone permissions and try again.",
        voiceFailed:
          "Voice recognition stopped unexpectedly. Please try again.",
        assistantWorking:
          "Suggestions and quick tags are hidden while the assistant is answering.",
        modeAuto: "Auto",
        modeChat: "Chat",
        modeAnalysis: "Analysis",
        modeReport: "Report",
        modeDeepResearch: "Deep Research",
        modeResolvedPrefix: "Auto",
        freeFormHint: "Try: 'Why is our isoprene margin lower than market trend?' or 'Show inventory vs last month'",
      }
      : {
        placeholder: "随意提问：价格、库存、销售、竞品、市场趋势……",
        shortcut: "按 Enter 发送 · Shift+Enter 换行",
        quickPrompt: "快捷标签",
        topicOptions: "分类选项",
        filesPrompt: "文件仅作为下一次提问的上下文",
        upload: "上传文件",
        uploading: "上传中...",
        uploadHint: "支持 TXT、CSV、XLSX、PDF、MD、JSON，最大 5 MB",
        removeFile: "移除文件",
        suggestions: "智能联想",
        showSuggestions: "显示联想",
        hideSuggestions: "隐藏联想",
        uploadFallbackPrompt:
          "请结合已上传文件内容进行分析，并提炼关键结论。",
        uploadFailed: "文件上传失败，请尝试更小或受支持的文件。",
        activeTopic: "已选标签",
        voiceInput: "语音输入",
        preparingVoice: "正在准备麦克风...",
        listening: "正在听写...",
        stopListening: "停止听写",
        stopGenerating: "停止",
        assistantResponding: "助手正在回答，你可以随时停止生成。",
        voiceUnsupported:
          "当前浏览器不支持语音输入，请使用 HTTPS 或 localhost 下的 Chrome / Edge。",
        voiceDenied: "麦克风权限被拒绝，请检查浏览器设置后重试。",
        voiceSetupFailed: "麦克风初始化失败，请检查浏览器权限后重试。",
        voiceFailed: "语音识别意外中断，请重试。",
        assistantWorking: "助手回答期间已隐藏联想和快捷标签。",
        modeAuto: "自动",
        modeChat: "对话",
        modeAnalysis: "分析",
        modeReport: "报告",
        modeDeepResearch: "深度研究",
        modeResolvedPrefix: "自动",
        freeFormHint: "试试：'为什么我们的异戊二烯利润低于市场走势？' 或 '显示库存与上月对比'",
      };
  const [internalInput, setInternalInput] = useState("");
  const [attachments, setAttachments] = useState<ChatAttachmentContext[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const [isPreparingVoice, setIsPreparingVoice] = useState(false);
  const [showSmartSuggestions, setShowSmartSuggestions] = useState(false);
  const [isListening, setIsListening] = useState(false);
  const [activeTopicKey, setActiveTopicKey] = useState<string | null>(null);
  const [composerSuggestions, setComposerSuggestions] = useState<
    ComposerSuggestionPayload[]
  >([]);
  const [showModeDropdown, setShowModeDropdown] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const voiceBaseInputRef = useRef("");
  const modeDropdownRef = useRef<HTMLDivElement>(null);
  const lastPasteEventAtRef = useRef(0);

  const controlled = typeof value === "string";
  const input = controlled ? (value as string) : internalInput;
  const deferredInput = useDeferredValue(input);
  const speechSupported =
    typeof window !== "undefined" &&
    Boolean(window.SpeechRecognition || window.webkitSpeechRecognition);
  const recognitionLanguage = language === "en" ? "en-US" : "zh-CN";
  const showInteractiveControls = !disabled || isStreaming;
  const canStop = Boolean(isStreaming && onStop);
  const modeLabel =
    mode === "auto"
      ? resolvedMode
        ? `${text.modeResolvedPrefix}: ${resolvedMode === "chat"
          ? text.modeChat
          : resolvedMode === "analysis"
            ? text.modeAnalysis
            : resolvedMode === "report"
              ? text.modeReport
              : text.modeDeepResearch
        }`
        : text.modeAuto
      : mode === "chat"
        ? text.modeChat
        : mode === "analysis"
          ? text.modeAnalysis
          : mode === "report"
            ? text.modeReport
            : text.modeDeepResearch;

  const setInput = (next: string) => {
    if (controlled) {
      onChange?.(next);
      return;
    }
    setInternalInput(next);
  };

  const insertTextAtCursor = (textToInsert: string) => {
    if (!textToInsert) {
      return;
    }
    const textarea = textareaRef.current;
    const selectionStart = textarea?.selectionStart ?? input.length;
    const selectionEnd = textarea?.selectionEnd ?? selectionStart;
    const nextInput = `${input.slice(0, selectionStart)}${textToInsert}${input.slice(selectionEnd)}`;
    setInput(nextInput);
    requestAnimationFrame(() => {
      const nextCursor = selectionStart + textToInsert.length;
      textareaRef.current?.setSelectionRange(nextCursor, nextCursor);
      textareaRef.current?.focus();
    });
  };

  const quickTemplates = useMemo<ShortcutTemplate[]>(() => {
    if (language === "en") {
      return [
        {
          key: "#market",
          label: "#Market",
          options: [
            {
              id: "market-trend",
              matchers: ["market"],
              text: "Analyze the latest C5 and C9 market trend, the 7-day move, and the main price drivers.",
            },
            {
              id: "market-outlook",
              matchers: ["market"],
              text: "Give me a one-week market outlook with upside, downside, and key watchpoints.",
            },
            {
              id: "market-chain",
              matchers: ["market"],
              text: "Map the current market chain transmission from feedstock to downstream pricing.",
            },
          ],
        },
        {
          key: "#cost",
          label: "#Cost",
          options: [
            {
              id: "cost-breakdown",
              matchers: ["cost"],
              text: "Break down this week's cost support using feedstock, energy, and conversion margin changes.",
            },
            {
              id: "cost-bridge-detail",
              matchers: ["cost"],
              text: "Build a detailed cost bridge and show which items are putting the most pressure on margin.",
            },
            {
              id: "cost-price-link",
              matchers: ["cost"],
              text: "Explain how current cost changes should affect our pricing strategy this week.",
            },
          ],
        },
        {
          key: "#competitor",
          label: "#Competitor",
          options: [
            {
              id: "competitor-ops",
              matchers: ["competitor"],
              text: "Summarize competitor plant operations, price moves, and likely impact on our win rate.",
            },
            {
              id: "competitor-orders",
              matchers: ["competitor"],
              text: "Which competitor actions are most likely to affect our order capture this week?",
            },
            {
              id: "competitor-risk",
              matchers: ["competitor"],
              text: "List the competitive risks and opportunities by region and product line.",
            },
          ],
        },
        {
          key: "#risk",
          label: "#Risk",
          options: [
            {
              id: "risk-top",
              matchers: ["risk"],
              text: "List the top supply, demand, and pricing risks for the next two weeks and how we should respond.",
            },
            {
              id: "risk-alerts",
              matchers: ["risk"],
              text: "Highlight the most urgent alerts and explain which ones require action today.",
            },
            {
              id: "risk-scenarios",
              matchers: ["risk"],
              text: "Build best, base, and worst-case scenarios for the next two weeks.",
            },
          ],
        },
        {
          key: "#weekly-report",
          label: "Weekly Report",
          options: [
            {
              id: "weekly-summary",
              matchers: ["weekly"],
              text: "Draft this week's management weekly report with market, sales, risk, and action summary.",
            },
            {
              id: "weekly-exec",
              matchers: ["weekly"],
              text: "Create an executive weekly report with 5 key takeaways and 3 recommended actions.",
            },
            {
              id: "weekly-product",
              matchers: ["weekly"],
              text: "Generate a product-level weekly report covering price, volume, margin, and abnormal events.",
            },
          ],
        },
        {
          key: "#deep-research",
          label: "Deep Market Research",
          options: [
            {
              id: "deep-supply-demand",
              matchers: ["research"],
              text: "Produce a deep market research note covering supply, demand, inventories, and pricing structure.",
            },
            {
              id: "deep-industry",
              matchers: ["research"],
              text: "Build an industry deep-dive on the current chain structure, major players, and turning points.",
            },
            {
              id: "deep-forecast",
              matchers: ["research"],
              text: "Write a deep market research report with short-term forecast, risks, and decision implications.",
            },
          ],
        },
        {
          key: "#sales-report",
          label: "Sales Report",
          options: [
            {
              id: "sales-summary",
              matchers: ["sales"],
              text: "Generate a weekly sales report with revenue, volume, price realization, and win/loss highlights.",
            },
            {
              id: "sales-region",
              matchers: ["sales"],
              text: "Create a regional sales report comparing target, actual, and gap by team.",
            },
            {
              id: "sales-customer",
              matchers: ["sales"],
              text: "Build a customer sales report showing growth, churn risk, and next-step actions.",
            },
          ],
        },
        {
          key: "#market-data",
          label: "Market Data",
          options: [
            {
              id: "market-data-summary",
              matchers: ["data"],
              text: "Summarize the latest market data changes for price, spread, inventory, and operating rate.",
            },
            {
              id: "market-data-anomaly",
              matchers: ["data"],
              text: "Find anomalies in the latest market data and explain what may be driving them.",
            },
            {
              id: "market-data-dashboard",
              matchers: ["data"],
              text: "Turn the latest market data into a concise dashboard-style narrative for management.",
            },
          ],
        },
        {
          key: "#sales-data",
          label: "Sales Data",
          options: [
            {
              id: "sales-data-summary",
              matchers: ["data"],
              text: "Summarize the latest sales data by product, region, and customer segment.",
            },
            {
              id: "sales-data-gap",
              matchers: ["data"],
              text: "Identify sales data gaps versus target and explain the biggest performance drivers.",
            },
            {
              id: "sales-data-action",
              matchers: ["data"],
              text: "Convert recent sales data into action priorities for the account and regional teams.",
            },
          ],
        },
      ];
    }

    return [
      {
        key: "#行情",
        label: "#行情",
        options: [
          {
            id: "market-trend",
            matchers: ["行情"],
            text: "分析一下C5和C9最新行情，包含7日涨跌幅、关键驱动和短期判断。",
          },
          {
            id: "market-outlook",
            matchers: ["行情"],
            text: "给我一版未来一周行情展望，分别说明上行、下行和重点关注点。",
          },
          {
            id: "market-chain",
            matchers: ["行情"],
            text: "梳理从原料到下游的价格传导链，并说明当前最关键的影响环节。",
          },
        ],
      },
      {
        key: "#成本",
        label: "#成本",
        options: [
          {
            id: "cost-breakdown",
            matchers: ["成本"],
            text: "结合原料、能耗和加工利润，分析本周成本支撑变化。",
          },
          {
            id: "cost-bridge-detail",
            matchers: ["成本"],
            text: "做一版详细成本桥，说明本周利润压力主要来自哪些环节。",
          },
          {
            id: "cost-price-link",
            matchers: ["成本"],
            text: "说明当前成本变化对本周报价策略和利润底线的影响。",
          },
        ],
      },
      {
        key: "#竞品",
        label: "#竞品",
        options: [
          {
            id: "competitor-ops",
            matchers: ["竞品"],
            text: "梳理主要竞品装置运行、报价变化，以及对我们成交的影响。",
          },
          {
            id: "competitor-orders",
            matchers: ["竞品"],
            text: "哪些竞品动作最可能影响我们本周接单与价格竞争？",
          },
          {
            id: "competitor-risk",
            matchers: ["竞品"],
            text: "按区域和产品线列出竞争风险与机会。",
          },
        ],
      },
      {
        key: "#风险",
        label: "#风险",
        options: [
          {
            id: "risk-top",
            matchers: ["风险"],
            text: "列出未来两周最值得关注的供应、需求和价格风险，并给出应对建议。",
          },
          {
            id: "risk-alerts",
            matchers: ["风险"],
            text: "指出当前最紧急的预警，并说明今天是否需要立即动作。",
          },
          {
            id: "risk-scenarios",
            matchers: ["风险"],
            text: "给出未来两周的乐观、中性、悲观三种情景判断。",
          },
        ],
      },
      {
        key: "#周报",
        label: "周报",
        options: [
          {
            id: "weekly-summary",
            matchers: ["周报"],
            text: "生成本周经营周报，覆盖行情、销售、风险和行动建议。",
          },
          {
            id: "weekly-exec",
            matchers: ["周报"],
            text: "生成一版管理层周报，提炼5个重点结论和3个行动建议。",
          },
          {
            id: "weekly-product",
            matchers: ["周报"],
            text: "按产品输出周报，覆盖价格、销量、利润和异常事件。",
          },
        ],
      },
      {
        key: "#深度市场研究",
        label: "深度市场研究",
        options: [
          {
            id: "deep-supply-demand",
            matchers: ["研究"],
            text: "做一版深度市场研究，覆盖供需、库存、价格结构和关键变量。",
          },
          {
            id: "deep-industry",
            matchers: ["研究"],
            text: "输出产业链深度研究，说明当前格局、核心玩家和拐点。",
          },
          {
            id: "deep-forecast",
            matchers: ["研究"],
            text: "形成一版深度市场研究报告，包含短期预测、风险和经营启示。",
          },
        ],
      },
      {
        key: "#销售报告",
        label: "销售报告",
        options: [
          {
            id: "sales-summary",
            matchers: ["销售"],
            text: "生成销售周报，覆盖收入、销量、成交价格和赢单输单情况。",
          },
          {
            id: "sales-region",
            matchers: ["销售"],
            text: "生成区域销售报告，对比目标、实际和差距。",
          },
          {
            id: "sales-customer",
            matchers: ["销售"],
            text: "输出客户销售报告，识别增长点、流失风险和下一步动作。",
          },
        ],
      },
      {
        key: "#市场数据",
        label: "市场数据",
        options: [
          {
            id: "market-data-summary",
            matchers: ["数据"],
            text: "总结最新市场数据变化，包括价格、价差、库存和开工率。",
          },
          {
            id: "market-data-anomaly",
            matchers: ["数据"],
            text: "识别最新市场数据中的异常点，并解释可能原因。",
          },
          {
            id: "market-data-dashboard",
            matchers: ["数据"],
            text: "把最新市场数据整理成管理层可读的简明看板结论。",
          },
        ],
      },
      {
        key: "#销售数据",
        label: "销售数据",
        options: [
          {
            id: "sales-data-summary",
            matchers: ["数据"],
            text: "按产品、区域和客户分层总结最新销售数据。",
          },
          {
            id: "sales-data-gap",
            matchers: ["数据"],
            text: "识别销售数据相对目标的主要差距，并说明核心驱动。",
          },
          {
            id: "sales-data-action",
            matchers: ["数据"],
            text: "根据最近销售数据提炼业务团队的优先行动项。",
          },
        ],
      },
    ];
  }, [language]);

  const activeTopic = useMemo(
    () => quickTemplates.find((item) => item.key === activeTopicKey) || null,
    [activeTopicKey, quickTemplates],
  );

  const activeTopicOptions = activeTopic?.options || [];

  const contextualTopicSuggestions = useMemo<SuggestionTemplate[]>(() => {
    return activeTopicOptions.map((option) => ({
      ...option,
      title: activeTopic?.label,
      reason: option.reason || text.activeTopic,
      source: "topic",
    }));
  }, [activeTopic?.label, activeTopicOptions, text.activeTopic]);

  const smartSuggestionLibrary = useMemo<SuggestionTemplate[]>(() => {
    if (language === "en") {
      return [
        {
          id: "c5-trend",
          matchers: ["c5"],
          text: "What is the latest C5 price trend, 7-day change, and near-term outlook?",
        },
        {
          id: "c5-margin",
          matchers: ["c5", "margin"],
          text: "How does the current C5-to-C9 spread affect downstream margin this week?",
        },
        {
          id: "c9-trend",
          matchers: ["c9"],
          text: "Summarize C9 market movement, supply changes, and the most important watchpoints.",
        },
        {
          id: "cost-bridge",
          matchers: ["cost", "feedstock"],
          text: "Build a cost bridge from feedstock to finished product and identify the biggest pressure point.",
        },
        {
          id: "risk-watch",
          matchers: ["risk"],
          text: "What are the top operating and pricing risks I should monitor over the next 14 days?",
        },
        {
          id: "competitor-watch",
          matchers: ["competitor"],
          text: "Which competitor actions could disrupt our pricing or order capture this week?",
        },
      ];
    }

    return [
      {
        id: "c5-trend",
        matchers: ["c5"],
        text: "C5本周价格走势、7日涨跌幅和关键驱动是什么？",
      },
      {
        id: "c5-spread",
        matchers: ["c5", "价差"],
        text: "C5与C9价差变化对本周利润和报价策略有何影响？",
      },
      {
        id: "c9-trend",
        matchers: ["c9"],
        text: "C9当前行情、供应变化和未来一周关注点是什么？",
      },
      {
        id: "cost-bridge",
        matchers: ["成本", "原料"],
        text: "请做一版成本桥分析，说明原料到成品的主要压力点。",
      },
      {
        id: "risk-watch",
        matchers: ["风险"],
        text: "未来14天最需要重点关注的供应、需求和价格风险有哪些？",
      },
      {
        id: "competitor-watch",
        matchers: ["竞品", "竞对"],
        text: "主要竞品装置和报价变化，对我们接单与利润的影响是什么？",
      },
    ];
  }, [language]);

  useEffect(() => {
    if (!showSmartSuggestions) {
      return;
    }

    let cancelled = false;

    api
      .getComposerSuggestions(language === "en" ? "en" : "zh")
      .then((response) => {
        if (!cancelled) {
          setComposerSuggestions(response.suggestions || []);
        }
      })
      .catch((error) => {
        console.error("Failed to load composer suggestions", error);
        if (!cancelled) {
          setComposerSuggestions([]);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [language, showSmartSuggestions]);

  const remoteSuggestionLibrary = useMemo<SuggestionTemplate[]>(() => {
    return composerSuggestions.map((suggestion) => ({
      id: suggestion.id,
      title: suggestion.title,
      text: suggestion.prompt,
      reason: suggestion.reason,
      source: suggestion.source,
      matchers: [suggestion.title, suggestion.reason, suggestion.source],
    }));
  }, [composerSuggestions]);

  const recommendationPool = useMemo<SuggestionTemplate[]>(() => {
    const seen = new Set<string>();
    return [
      ...contextualTopicSuggestions,
      ...remoteSuggestionLibrary,
      ...smartSuggestionLibrary,
    ].filter((item) => {
      const key = `${item.id}:${item.text}`;
      if (seen.has(key)) {
        return false;
      }
      seen.add(key);
      return true;
    });
  }, [
    contextualTopicSuggestions,
    remoteSuggestionLibrary,
    smartSuggestionLibrary,
  ]);

  const smartSuggestions = useMemo(() => {
    if (!showSmartSuggestions || disabled) {
      return [];
    }

    const normalized = deferredInput.trim().toLowerCase();
    const tokens = normalized.split(/\s+/).filter(Boolean);

    const ranked = recommendationPool
      .map((item) => {
        const searchable = [
          item.title,
          item.text,
          item.reason,
          item.source,
          ...(item.matchers || []),
        ]
          .filter(Boolean)
          .join(" ")
          .toLowerCase();

        let score = 0;
        if (item.source === "topic") score += 45;
        if (item.source && item.source !== "topic") score += 18;
        if (activeTopic && item.title === activeTopic.label) score += 30;

        if (!normalized) {
          score += item.source === "topic" ? 25 : 10;
        } else {
          if (searchable.includes(normalized)) score += 80;
          for (const token of tokens) {
            if (searchable.includes(token)) score += 14;
          }
        }

        return { item, score, searchable };
      })
      .filter(({ score, searchable }) => {
        if (!normalized) return true;
        return score > 0 || searchable.includes(normalized);
      })
      .sort((left, right) => right.score - left.score);

    if (!normalized) {
      return ranked.slice(0, 4).map(({ item }) => item);
    }

    const directMatches = ranked.filter(({ searchable }) =>
      searchable.includes(normalized),
    );
    if (directMatches.length > 0) {
      return directMatches.slice(0, 4).map(({ item }) => item);
    }

    return ranked.slice(0, 3).map(({ item }) => item);
  }, [
    activeTopic,
    deferredInput,
    disabled,
    recommendationPool,
    showSmartSuggestions,
  ]);

  useEffect(() => {
    if (!disabled) {
      return;
    }

    setShowSmartSuggestions(false);
    setActiveTopicKey(null);
    recognitionRef.current?.stop();
    setIsListening(false);
    setIsPreparingVoice(false);
  }, [disabled]);

  useEffect(() => {
    if (!showModeDropdown) return;
    const handler = (e: MouseEvent) => {
      if (
        modeDropdownRef.current &&
        !modeDropdownRef.current.contains(e.target as Node)
      ) {
        setShowModeDropdown(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [showModeDropdown]);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 200) + "px";
  }, [input]);

  useEffect(() => {
    if (focusToken === undefined) return;
    textareaRef.current?.focus();
  }, [focusToken]);

  useEffect(() => {
    const RecognitionCtor =
      window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!RecognitionCtor) {
      recognitionRef.current = null;
      return;
    }

    const recognition = new RecognitionCtor();
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.lang = recognitionLanguage;
    recognition.onstart = () => {
      setVoiceError(null);
      setIsListening(true);
    };
    recognition.onresult = (event) => {
      let transcript = "";
      for (
        let index = event.resultIndex;
        index < event.results.length;
        index += 1
      ) {
        transcript += event.results[index][0]?.transcript || "";
      }
      const prefix = voiceBaseInputRef.current.trim();
      const spoken = transcript.trim();
      setInput(
        prefix && spoken ? `${prefix}\n${spoken}` : `${prefix}${spoken}`,
      );
    };
    recognition.onerror = (event) => {
      const errorCode = String(event.error || "").toLowerCase();
      if (errorCode === "not-allowed" || errorCode === "service-not-allowed") {
        setVoiceError(text.voiceDenied);
      } else if (errorCode && errorCode !== "aborted") {
        setVoiceError(text.voiceFailed);
      }
      setIsListening(false);
    };
    recognition.onend = () => {
      setIsListening(false);
    };
    recognitionRef.current = recognition;

    return () => {
      recognition.onstart = null;
      recognition.onresult = null;
      recognition.onerror = null;
      recognition.onend = null;
      recognition.stop();
      recognitionRef.current = null;
    };
  }, [recognitionLanguage, text.voiceDenied, text.voiceFailed]);

  const insertTemplate = (template: string) => {
    const nextValue = input.trim() ? `${input.trim()}\n${template}` : template;
    setInput(nextValue);
    textareaRef.current?.focus();
  };

  const handleTopicClick = (topic: ShortcutTemplate) => {
    if (topic.options && topic.options.length > 0) {
      setActiveTopicKey((prev) => (prev === topic.key ? null : topic.key));
      textareaRef.current?.focus();
      return;
    }

    if (topic.template) {
      insertTemplate(topic.template);
    }
  };

  const handleFileButtonClick = () => {
    if (disabled || isUploading) {
      return;
    }
    fileInputRef.current?.click();
  };

  const handleFileSelection = async (
    event: React.ChangeEvent<HTMLInputElement>,
  ) => {
    const files = Array.from(event.target.files || []);
    if (files.length === 0) {
      return;
    }

    setUploadError(null);
    setIsUploading(true);

    try {
      const uploadedItems: ChatAttachmentContext[] = [];
      for (const file of files.slice(0, Math.max(0, 3 - attachments.length))) {
        const uploaded = await api.uploadChatContextFile(file);
        uploadedItems.push(uploaded);
      }
      if (uploadedItems.length > 0) {
        setAttachments((prev) => [...prev, ...uploadedItems]);
      }
    } catch (error) {
      console.error("Failed to upload chat context file", error);
      setUploadError(
        error instanceof Error ? error.message : text.uploadFailed,
      );
    } finally {
      setIsUploading(false);
      event.target.value = "";
    }
  };

  const removeAttachment = (attachmentId: string) => {
    setAttachments((prev) => prev.filter((item) => item.id !== attachmentId));
  };

  const ensureMicrophoneAccess = async () => {
    if (
      typeof navigator === "undefined" ||
      !navigator.mediaDevices ||
      !navigator.mediaDevices.getUserMedia
    ) {
      return;
    }

    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    stream.getTracks().forEach((track) => track.stop());
  };

  const handleSend = () => {
    const message =
      input.trim() || (attachments.length > 0 ? text.uploadFallbackPrompt : "");
    if (message && !disabled && !isUploading) {
      recognitionRef.current?.stop();
      setIsListening(false);
      onSend(message, attachments);
      setInput("");
      setAttachments([]);
      setUploadError(null);
      setVoiceError(null);
    }
  };

  const handleVoiceInput = async () => {
    if (disabled) {
      return;
    }
    if (!speechSupported || !recognitionRef.current) {
      setVoiceError(text.voiceUnsupported);
      return;
    }
    if (isListening) {
      recognitionRef.current.stop();
      setIsListening(false);
      return;
    }

    voiceBaseInputRef.current = input.trim();
    setVoiceError(null);
    textareaRef.current?.focus();
    try {
      setIsPreparingVoice(true);
      await ensureMicrophoneAccess();
      recognitionRef.current.lang = recognitionLanguage;
      recognitionRef.current.start();
    } catch (error) {
      console.error("Failed to start speech recognition", error);
      const message =
        error instanceof DOMException && error.name === "NotAllowedError"
          ? text.voiceDenied
          : text.voiceSetupFailed;
      setVoiceError(message);
      setIsListening(false);
    } finally {
      setIsPreparingVoice(false);
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === "v") {
      const valueBeforePaste = e.currentTarget.value;
      window.setTimeout(async () => {
        if (Date.now() - lastPasteEventAtRef.current < 250) {
          return;
        }
        if (textareaRef.current?.value !== valueBeforePaste) {
          return;
        }
        try {
          const clipboardText = await navigator.clipboard?.readText?.();
          insertTextAtCursor(clipboardText || "");
        } catch {
          // Browser denied programmatic clipboard access; native paste remains the fallback.
        }
      }, 0);
      return;
    }

    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handlePaste = (e: ClipboardEvent<HTMLTextAreaElement>) => {
    lastPasteEventAtRef.current = Date.now();
    const pastedText = e.clipboardData.getData("text/plain");
    if (!pastedText) {
      return;
    }
    e.preventDefault();
    insertTextAtCursor(pastedText);
  };

  const canSend =
    (!!input.trim() || attachments.length > 0) && !disabled && !isUploading;

  return (
    <div className="relative w-full">
      <div
        className={`chat-input-shell relative rounded-[1.75rem] border bg-white transition-colors duration-200 shadow-sm ${disabled ? "border-stone-200 opacity-70" : "border-stone-200 focus-within:border-stone-300"}`}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".txt,.csv,.xlsx,.xls,.pdf,.md,.json"
          multiple
          className="hidden"
          onChange={handleFileSelection}
        />

        {showInteractiveControls && (
          <div className="relative z-10 px-4 pt-4">
            <div
              className="flex items-center gap-2 overflow-x-auto pb-0.5"
              style={{ scrollbarWidth: "none" }}
            >
              <button
                type="button"
                onClick={() => setShowSmartSuggestions((prev) => !prev)}
                disabled={disabled}
                title={
                  showSmartSuggestions
                    ? text.hideSuggestions
                    : text.showSuggestions
                }
                className={`flex-shrink-0 inline-flex items-center justify-center w-8 h-8 rounded-full border transition disabled:cursor-not-allowed disabled:opacity-50 ${showSmartSuggestions
                  ? "border-slate-300 bg-stone-100 text-slate-800"
                  : "border-slate-200 bg-white text-slate-500 hover:border-slate-300 hover:bg-stone-50"
                  }`}
              >
                <Sparkles size={13} />
              </button>
              {quickTemplates.map((item) => (
                <button
                  key={item.key}
                  type="button"
                  onClick={() => handleTopicClick(item)}
                  disabled={disabled}
                  className={`flex-shrink-0 rounded-full border px-2.5 py-1 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${activeTopicKey === item.key
                    ? "border-slate-700 bg-slate-800 text-white"
                    : "border-slate-200 bg-white text-slate-500 hover:border-slate-300 hover:bg-stone-50 hover:text-slate-700"
                    }`}
                >
                  {item.label}
                </button>
              ))}
            </div>
          </div>
        )}

        {showInteractiveControls && activeTopicOptions.length > 0 && (
          <div className="relative z-10 px-4 pt-3">
            <div className="rounded-[1.25rem] border border-stone-200 bg-stone-50 p-3">
              <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">
                <span>{text.topicOptions}</span>
                <span className="rounded-full border border-stone-200 bg-white px-2 py-1 text-xs text-slate-700">
                  {activeTopic?.label}
                </span>
              </div>
              <div className="flex flex-wrap gap-2">
                {activeTopicOptions.map((option) => (
                  <button
                    key={option.id}
                    type="button"
                    onClick={() => insertTemplate(option.text)}
                    className="rounded-2xl border border-stone-200 bg-white px-3 py-2.5 text-left text-sm font-medium text-slate-800 transition hover:border-stone-300 hover:bg-stone-50"
                  >
                    {option.text}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}

        {attachments.length > 0 && (
          <div className="relative z-10 px-4 pt-3">
            <div className="flex flex-wrap gap-2">
              {attachments.map((attachment) => (
                <div
                  key={attachment.id}
                  className="flex max-w-full items-start gap-2 rounded-2xl border border-stone-200 bg-stone-50 px-3 py-2.5 text-left text-sm text-slate-700"
                >
                  <div className="min-w-0">
                    <div className="truncate font-semibold text-slate-900">
                      {attachment.filename}
                    </div>
                    <div className="line-clamp-2 text-xs text-slate-600">
                      {attachment.summary}
                    </div>
                  </div>
                  <button
                    type="button"
                    aria-label={text.removeFile}
                    onClick={() => removeAttachment(attachment.id)}
                    className="rounded-full p-1 text-slate-500 transition hover:bg-white hover:text-slate-800"
                  >
                    <X size={14} />
                  </button>
                </div>
              ))}
            </div>
            <div className="mt-2 text-xs text-slate-500">
              {text.filesPrompt}
            </div>
          </div>
        )}

        {showInteractiveControls && activeTopic && (
          <div className="relative z-10 px-5 pt-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">
                {text.activeTopic}
              </span>
              <button
                type="button"
                onClick={() => setActiveTopicKey(null)}
                className="inline-flex items-center gap-2 rounded-full bg-slate-900 px-3 py-1.5 text-sm font-semibold text-white"
              >
                <span>{activeTopic.label}</span>
                <X size={12} />
              </button>
            </div>
          </div>
        )}

        <div className="relative z-10">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            placeholder={text.placeholder}
            className="w-full px-5 pt-4 pb-2 text-slate-800 bg-transparent border-none resize-none focus:ring-0 focus:outline-none min-h-[88px] max-h-[200px] placeholder:text-slate-500 text-base leading-relaxed"
            rows={1}
            disabled={disabled}
          />
          {!input.trim() && !disabled && (
            <div className="px-5 pb-1 text-xs text-slate-400">
              {text.freeFormHint}
            </div>
          )}
        </div>

        {(showSmartSuggestions || uploadError || voiceError || disabled) && (
          <div className="relative z-10 px-4 pb-2">
            {disabled && (
              <div className="mb-2 text-sm text-slate-500">
                {canStop ? text.assistantResponding : text.assistantWorking}
              </div>
            )}

            {showSmartSuggestions && smartSuggestions.length > 0 && (
              <div className="mb-2 rounded-2xl border border-stone-200 bg-stone-50 p-3">
                <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.18em] text-slate-700">
                  <Sparkles size={14} />
                  <span>{text.suggestions}</span>
                </div>
                <div className="flex flex-wrap gap-2">
                  {smartSuggestions.map((suggestion) => (
                    <button
                      key={suggestion.id}
                      type="button"
                      onClick={() => {
                        setInput(suggestion.text);
                        setShowSmartSuggestions(false);
                      }}
                      className="rounded-2xl border border-stone-200 bg-white px-3 py-2.5 text-left text-sm font-medium text-slate-800 transition hover:border-stone-300 hover:bg-stone-50"
                    >
                      {suggestion.title && (
                        <div className="mb-1 text-xs font-semibold uppercase tracking-[0.12em] text-slate-600">
                          {suggestion.title}
                        </div>
                      )}
                      <div>{suggestion.text}</div>
                      {suggestion.reason && (
                        <div className="mt-1 text-xs font-normal text-slate-500">
                          {suggestion.reason}
                        </div>
                      )}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {uploadError && (
              <div className="text-sm text-rose-600">{uploadError}</div>
            )}
            {voiceError && (
              <div className="mt-1 text-sm text-rose-600">{voiceError}</div>
            )}
          </div>
        )}

        <div className="relative z-10 flex items-center gap-2 px-4 pb-3 pt-1">
          <button
            type="button"
            onClick={handleFileButtonClick}
            disabled={disabled || isUploading || attachments.length >= 3}
            title={text.uploadHint}
            className="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 transition hover:border-slate-300 hover:bg-stone-50 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <Paperclip size={14} />
            <span>{isUploading ? text.uploading : text.upload}</span>
          </button>
          <button
            type="button"
            onClick={handleVoiceInput}
            disabled={disabled || !speechSupported || isPreparingVoice}
            title={isListening ? text.stopListening : text.voiceInput}
            className={`inline-flex items-center gap-2 rounded-xl border px-4 py-2.5 text-sm font-semibold transition disabled:cursor-not-allowed disabled:opacity-100 ${isListening
              ? "border-rose-300 bg-rose-50 text-rose-700 shadow-[0_0_0_3px_rgba(251,113,133,0.12)]"
              : speechSupported
                ? "border-slate-200 bg-stone-50 text-slate-700 hover:border-slate-300 hover:bg-stone-100"
                : "border-slate-200 bg-slate-100 text-slate-500"
              }`}
          >
            <Mic size={14} />
            <span>
              {isPreparingVoice
                ? text.preparingVoice
                : isListening
                  ? text.listening
                  : text.voiceInput}
            </span>
          </button>
          <div className="flex items-center gap-2 ml-auto">
            {onModeChange && (
              <div className="relative" ref={modeDropdownRef}>
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => setShowModeDropdown((prev) => !prev)}
                  className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm font-semibold text-slate-700 transition hover:border-slate-300 hover:bg-stone-50 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  <span>{modeLabel}</span>
                  <ChevronDown
                    size={13}
                    className={`transition-transform duration-150 ${showModeDropdown ? "rotate-180" : ""
                      }`}
                  />
                </button>
                {showModeDropdown && (
                  <div className="absolute bottom-full right-0 mb-2 w-44 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-lg z-50">
                    {(
                      [
                        { key: "auto" as const, label: text.modeAuto },
                        { key: "chat" as const, label: text.modeChat },
                        { key: "analysis" as const, label: text.modeAnalysis },
                        { key: "report" as const, label: text.modeReport },
                        {
                          key: "deep_research" as const,
                          label: text.modeDeepResearch,
                        },
                      ] as { key: ChatMode; label: string }[]
                    ).map(({ key, label }) => (
                      <button
                        key={key}
                        type="button"
                        onClick={() => {
                          onModeChange(key);
                          setShowModeDropdown(false);
                        }}
                        className={`w-full px-4 py-2.5 text-left text-sm font-semibold transition hover:bg-stone-50 ${mode === key
                          ? "text-slate-900 bg-stone-50"
                          : "text-slate-600"
                          }`}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
            {canStop ? (
              <button
                type="button"
                onClick={onStop}
                title={text.stopGenerating}
                className="inline-flex items-center justify-center gap-2 h-11 px-4 rounded-xl bg-rose-600 text-white font-semibold transition-all duration-200 hover:bg-rose-500 active:scale-95"
              >
                <X size={16} strokeWidth={2.5} />
                <span>{text.stopGenerating}</span>
              </button>
            ) : (
              <button
                onClick={handleSend}
                disabled={!canSend}
                title={text.shortcut}
                className={`flex items-center justify-center w-11 h-11 rounded-xl transition-all duration-200 ${canSend
                  ? "bg-slate-900 text-white hover:bg-slate-800 active:scale-95"
                  : "bg-slate-100 text-slate-500 cursor-not-allowed"
                  }`}
              >
                <ArrowUp size={18} strokeWidth={2.5} />
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
