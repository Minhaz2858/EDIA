import {
  AlertTriangle,
  Bell,
  Braces,
  CheckCircle2,
  Clock3,
  Database,
  FileDown,
  Filter,
  History,
  KeyRound,
  Languages,
  Moon,
  PackagePlus,
  Sun,
  MoreHorizontal,
  Plus,
  RefreshCw,
  ScrollText,
  Search,
  Shield,
  Trash2,
  Upload,
  UserPlus,
  Users,
} from "lucide-react";
import React, { useEffect, useMemo, useState } from "react";
import { useLanguage } from "../lib/language";
import { useTheme } from "../lib/theme";
import { PlatformBuilder } from "./PlatformBuilder";
import { ProductCatalogManager } from "./ProductCatalogManager";
import {
  AccessModelConfig,
  AccessUserTemplateConfig,
  api,
  AuthUser,
  DepartmentTemplateConfig,
  EventModerationItem,
  EventReviewStatus,
  EventReviewSummaryResponse,
  FeatureAccessConfig,
  Message,
  OrganizationTemplateConfig,
  RoleTemplateConfig,
  Session,
} from "../services/api";

type SettingsTab =
  | "users"
  | "data"
  | "security"
  | "logs"
  | "language"
  | "appearance"
  | "accessModel"
  | "newsTable"
  | "products"
  | "platform";

interface SettingsCenterProps {
  initialTab?: SettingsTab;
  currentUser?: AuthUser | null;
  onCurrentUserChange?: (user: AuthUser) => void;
}

const ADMIN_ONLY_SETTINGS_TABS = new Set<SettingsTab>([
  "newsTable",
  "products",
  "platform",
]);

function canViewSettingsTab(tab: SettingsTab, isAdmin: boolean): boolean {
  return isAdmin || !ADMIN_ONLY_SETTINGS_TABS.has(tab);
}

function normalizeSettingsTab(tab: SettingsTab, isAdmin: boolean): SettingsTab {
  return canViewSettingsTab(tab, isAdmin) ? tab : "users";
}

interface LogRow {
  type: string;
  user: string;
  timestamp: string;
}

interface ConversationRow {
  id: string;
  title: string;
  messages: string;
  status: "active" | "archived";
  product_context: string;
}

type OrganizationTemplate = OrganizationTemplateConfig;
type DepartmentTemplate = DepartmentTemplateConfig;
type RoleTemplate = RoleTemplateConfig;
type UserAccessTemplate = AccessUserTemplateConfig;

interface AdminControlAction {
  action: string;
  example: string;
}

const DEFAULT_FEATURE_ACCESS: FeatureAccessConfig = {
  chatbot: true,
  dashboardUpstream: false,
  dashboardMidstream: false,
  dashboardDownstream: false,
  dashboardComparison: false,
  report: false,
};

const ALL_FEATURE_ACCESS: FeatureAccessConfig = {
  chatbot: true,
  dashboardUpstream: true,
  dashboardMidstream: true,
  dashboardDownstream: true,
  dashboardComparison: true,
  report: true,
};

const FEATURE_KEYS: Array<keyof FeatureAccessConfig> = [
  "chatbot",
  "dashboardUpstream",
  "dashboardMidstream",
  "dashboardDownstream",
  "dashboardComparison",
  "report",
];

const DEFAULT_ORGANIZATIONS: OrganizationTemplate[] = [
  {
    id: "org-ecisco",
    name: "Ecisco",
    productChain: "C5/C9",
    regionCoverage: "East China, South China",
  },
];

const DEFAULT_DEPARTMENTS: DepartmentTemplate[] = [
  { id: "dept-exec", name: "Executive Office", organizationId: "org-ecisco" },
  {
    id: "dept-market",
    name: "Market Intelligence",
    organizationId: "org-ecisco",
  },
  { id: "dept-sales", name: "Sales", organizationId: "org-ecisco" },
  { id: "dept-finance", name: "Finance", organizationId: "org-ecisco" },
  { id: "dept-production", name: "Production", organizationId: "org-ecisco" },
];

const DEFAULT_ROLES: RoleTemplate[] = [
  {
    id: "role-ceo",
    name: "CEO",
    organizationId: "org-ecisco",
    departmentId: "dept-exec",
    productScope: "All products",
    regionScope: "All regions",
    featureAccess: { ...ALL_FEATURE_ACCESS },
    dataAccess: "All enterprise data",
    dashboardTemplate: "Executive command center",
  },
  {
    id: "role-market-analyst",
    name: "Market Analyst",
    organizationId: "org-ecisco",
    departmentId: "dept-market",
    productScope: "Upstream and market indicators",
    regionScope: "China market view",
    featureAccess: {
      chatbot: true,
      dashboardUpstream: true,
      dashboardMidstream: true,
      dashboardDownstream: false,
      dashboardComparison: true,
      report: true,
    },
    dataAccess: "Market and upstream intelligence",
    dashboardTemplate: "Market pulse board",
  },
  {
    id: "role-sales-south",
    name: "Sales Manager",
    organizationId: "org-ecisco",
    departmentId: "dept-sales",
    productScope: "C5/C9 downstream portfolio",
    regionScope: "South China",
    featureAccess: {
      chatbot: true,
      dashboardUpstream: false,
      dashboardMidstream: true,
      dashboardDownstream: true,
      dashboardComparison: true,
      report: true,
    },
    dataAccess: "Sales pipeline and customer demand",
    dashboardTemplate: "Regional sales cockpit",
  },
  {
    id: "role-cfo",
    name: "CFO",
    organizationId: "org-ecisco",
    departmentId: "dept-finance",
    productScope: "All products",
    regionScope: "All regions",
    featureAccess: {
      chatbot: true,
      dashboardUpstream: true,
      dashboardMidstream: true,
      dashboardDownstream: true,
      dashboardComparison: false,
      report: true,
    },
    dataAccess: "Financial and profitability data",
    dashboardTemplate: "Finance performance board",
  },
  {
    id: "role-production",
    name: "Production Manager",
    organizationId: "org-ecisco",
    departmentId: "dept-production",
    productScope: "Own facility products only",
    regionScope: "Plant operations",
    featureAccess: {
      chatbot: true,
      dashboardUpstream: false,
      dashboardMidstream: true,
      dashboardDownstream: false,
      dashboardComparison: false,
      report: false,
    },
    dataAccess: "Operations data only",
    dashboardTemplate: "Production and capacity board",
  },
];

const DEFAULT_USER_ACCESS: UserAccessTemplate[] = [
  {
    id: "ua-president-liu",
    username: "President Liu",
    password: "CHANGE_ME_ON_FIRST_LOGIN",
    organizationId: "org-ecisco",
    departmentId: "dept-exec",
    roleId: "role-ceo",
    featureAccess: { ...ALL_FEATURE_ACCESS },
    isActive: true,
    oneTimeOverride: "",
  },
  {
    id: "ua-gao",
    username: "Gao",
    password: "CHANGE_ME_ON_FIRST_LOGIN",
    organizationId: "org-ecisco",
    departmentId: "dept-market",
    roleId: "role-market-analyst",
    featureAccess: {
      chatbot: true,
      dashboardUpstream: true,
      dashboardMidstream: true,
      dashboardDownstream: false,
      dashboardComparison: true,
      report: true,
    },
    isActive: true,
    oneTimeOverride: "",
  },
  {
    id: "ua-zhang-wei",
    username: "Zhang Wei",
    password: "CHANGE_ME_ON_FIRST_LOGIN",
    organizationId: "org-ecisco",
    departmentId: "dept-sales",
    roleId: "role-sales-south",
    featureAccess: {
      chatbot: true,
      dashboardUpstream: false,
      dashboardMidstream: true,
      dashboardDownstream: true,
      dashboardComparison: true,
      report: true,
    },
    isActive: true,
    oneTimeOverride: "",
  },
  {
    id: "ua-li-min",
    username: "Li Min",
    password: "CHANGE_ME_ON_FIRST_LOGIN",
    organizationId: "org-ecisco",
    departmentId: "dept-finance",
    roleId: "role-cfo",
    featureAccess: {
      chatbot: true,
      dashboardUpstream: true,
      dashboardMidstream: true,
      dashboardDownstream: true,
      dashboardComparison: false,
      report: true,
    },
    isActive: true,
    oneTimeOverride: "",
  },
];

function cloneFeatureAccess(
  source?: Partial<FeatureAccessConfig> | null,
): FeatureAccessConfig {
  return {
    chatbot: Boolean(source?.chatbot),
    dashboardUpstream: Boolean(source?.dashboardUpstream),
    dashboardMidstream: Boolean(source?.dashboardMidstream),
    dashboardDownstream: Boolean(source?.dashboardDownstream),
    dashboardComparison: Boolean(source?.dashboardComparison),
    report: Boolean(source?.report),
  };
}

function featureAccessSummary(
  features: FeatureAccessConfig,
  labels: Record<keyof FeatureAccessConfig, string>,
): string {
  const enabled = FEATURE_KEYS.filter((key) => features[key]).map(
    (key) => labels[key],
  );
  return enabled.length ? enabled.join(", ") : "No features";
}

function normalizeAccessModelPayload(
  payload: AccessModelConfig,
): AccessModelConfig {
  const organizations =
    Array.isArray(payload.organizations) && payload.organizations.length > 0
      ? payload.organizations.map((org) => ({
        id: org.id,
        name: org.name || "",
        productChain: org.productChain || "",
        regionCoverage: org.regionCoverage || "",
      }))
      : DEFAULT_ORGANIZATIONS;
  const fallbackOrgId = organizations[0]?.id || "";

  const departments =
    Array.isArray(payload.departments) && payload.departments.length > 0
      ? payload.departments.map((dept) => ({
        id: dept.id,
        name: dept.name || "",
        organizationId:
          (dept as Partial<DepartmentTemplate>).organizationId ||
          fallbackOrgId,
      }))
      : DEFAULT_DEPARTMENTS;
  const fallbackDepartmentId = departments[0]?.id || "";

  const roles =
    Array.isArray(payload.roles) && payload.roles.length > 0
      ? payload.roles.map((role) => ({
        id: role.id,
        name: role.name || "",
        organizationId:
          (role as Partial<RoleTemplate>).organizationId ||
          departments.find(
            (dept) =>
              dept.id === (role as Partial<RoleTemplate>).departmentId,
          )?.organizationId ||
          fallbackOrgId,
        departmentId:
          (role as Partial<RoleTemplate>).departmentId ||
          fallbackDepartmentId,
        productScope: role.productScope || "",
        regionScope: role.regionScope || "",
        featureAccess:
          typeof role.featureAccess === "string"
            ? { ...DEFAULT_FEATURE_ACCESS }
            : cloneFeatureAccess(role.featureAccess),
        dataAccess: role.dataAccess || "",
        dashboardTemplate: role.dashboardTemplate || "",
      }))
      : DEFAULT_ROLES;
  const fallbackRoleId = roles[0]?.id || "";

  const accessUsers =
    Array.isArray(payload.accessUsers) && payload.accessUsers.length > 0
      ? payload.accessUsers.map((user) => ({
        id: user.id,
        username: user.username || "",
        password: user.password || "",
        organizationId:
          (user as Partial<UserAccessTemplate>).organizationId ||
          departments.find((dept) => dept.id === user.departmentId)
            ?.organizationId ||
          fallbackOrgId,
        departmentId: user.departmentId || fallbackDepartmentId,
        roleId: user.roleId || fallbackRoleId,
        featureAccess:
          typeof user.featureAccess === "string" || !user.featureAccess
            ? cloneFeatureAccess(
              roles.find((role) => role.id === user.roleId)
                ?.featureAccess || DEFAULT_FEATURE_ACCESS,
            )
            : cloneFeatureAccess(user.featureAccess),
        isActive: Boolean(user.isActive),
        oneTimeOverride: user.oneTimeOverride || "",
      }))
      : DEFAULT_USER_ACCESS;

  return {
    organizations,
    departments,
    roles,
    accessUsers,
    updated_at: payload.updated_at,
    updated_by: payload.updated_by,
  };
}

const ADMIN_CONTROL_ACTIONS: AdminControlAction[] = [
  {
    action: "Change a user's role",
    example:
      "Move Gao from Market Analyst to Senior Analyst with contract access.",
  },
  {
    action: "Add a product to a role",
    example: "Sales starts selling DCPD, add DCPD to Sales Manager role.",
  },
  {
    action: "Remove a feature",
    example:
      "Disable Pricing Override for all non-executive roles in one click.",
  },
  {
    action: "Restrict a region",
    example: "Create a narrower Huizhou-only region scope for a new hire.",
  },
  {
    action: "Lock a report",
    example:
      "Restrict Q4 financial report visibility to CFO and CEO roles only.",
  },
  {
    action: "Temporarily suspend a user",
    example:
      "Deactivate users on leave and reactivate later with no data loss.",
  },
  {
    action: "Add a new department",
    example: "Create R&D, create role templates, then assign users.",
  },
  {
    action: "Change dashboard layout",
    example: "Expand the CEO forecast panel and reduce inventory panel size.",
  },
  {
    action: "Grant one-time exception",
    example: "Allow temporary competitor access for one deal and remove after.",
  },
  {
    action: "View user activity",
    example:
      "Review what Zhang Wei queried/exported last week with full audit trail.",
  },
];

function createLocalId(prefix: string): string {
  return `${prefix}-${Math.random().toString(36).slice(2, 8)}`;
}

const LOG_ROWS: LogRow[] = [
  {
    type: "app.user.query",
    user: "minhaz249602@gmail.com",
    timestamp: "3/14/2026, 8:29:40 AM",
  },
  {
    type: "app.security.check_run",
    user: "minhaz249602@gmail.com",
    timestamp: "3/14/2026, 8:27:56 AM",
  },
  {
    type: "app.entity.query",
    user: "minhaz249602@gmail.com",
    timestamp: "3/14/2026, 8:26:36 AM",
  },
  {
    type: "app.entity.updated",
    user: "minhaz249602@gmail.com",
    timestamp: "3/14/2026, 8:26:35 AM",
  },
  {
    type: "app.integration.executed",
    user: "minhaz249602@gmail.com",
    timestamp: "3/14/2026, 8:26:34 AM",
  },
];

function truncate(text: string, max = 100): string {
  if (!text) return "";
  return text.length > max ? `${text.slice(0, max - 3)}...` : text;
}

function extractProductContext(messages: Message[]): string {
  const keys = [
    "product_context",
    "product",
    "product_name",
    "product_cn",
    "products",
    "selected_products",
  ];

  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i] as Message & {
      context?: Record<string, any> | null;
    };
    const ctx = msg.context;
    if (!ctx || typeof ctx !== "object") continue;

    for (const key of keys) {
      const val = (ctx as Record<string, any>)[key];
      if (typeof val === "string" && val.trim()) return val.trim();
      if (Array.isArray(val) && val.length > 0) {
        return val.map((x) => String(x)).join(", ");
      }
    }
  }

  return "-";
}

function summarizeMessages(messages: Message[]): string {
  if (!messages.length) return "[]";

  const compact = messages.slice(0, 3).map((m) => ({
    role: m.role,
    content: truncate(m.content || "", 60),
  }));

  return JSON.stringify(compact);
}

function toConversationRow(
  session: Session,
  messages: Message[],
): ConversationRow {
  return {
    id: session.id,
    title: session.title || "Untitled",
    messages: summarizeMessages(messages),
    status: "active",
    product_context: extractProductContext(messages),
  };
}

const TABS: Array<{
  id: SettingsTab;
  label: string;
  icon: React.ReactNode;
  description: string;
}> = [
    {
      id: "users",
      label: "Users",
      icon: <Users size={16} />,
      description: "Manage app users and role access.",
    },
    {
      id: "data",
      label: "Data (Conversation)",
      icon: <Database size={16} />,
      description:
        "Control conversation retention, export, and deletion policies.",
    },
    {
      id: "security",
      label: "Security",
      icon: <Shield size={16} />,
      description: "Configure MFA, session, and key management policies.",
    },
    {
      id: "logs",
      label: "Logs",
      icon: <ScrollText size={16} />,
      description: "Search and monitor system audit logs.",
    },
    {
      id: "language",
      label: "Language",
      icon: <Languages size={16} />,
      description: "Choose your interface and response language.",
    },
    {
      id: "appearance",
      label: "Appearance",
      icon: <Sun size={16} />,
      description: "Choose light or dark interface theme.",
    },
    {
      id: "accessModel",
      label: "Access Model",
      icon: <Braces size={16} />,
      description:
        "Build organizations, departments, roles, and user access templates.",
    },
    {
      id: "newsTable",
      label: "News Table",
      icon: <Bell size={16} />,
      description: "View Kimi-reviewed intelligence events stored in DB.",
    },
    {
      id: "products",
      label: "Product Catalog",
      icon: <PackagePlus size={16} />,
      description: "Manage custom upstream, midstream, and downstream products.",
    },
    {
      id: "platform",
      label: "Platform Builder",
      icon: <Braces size={16} />,
      description: "Configure v2 platform primitives and semantic resources.",
    },
  ];

function roleBadge(isAdmin: boolean): string {
  if (isAdmin) return "bg-blue-50 text-blue-700 border-blue-100";
  return "bg-slate-50 text-slate-700 border-slate-100";
}

export const SettingsCenter: React.FC<SettingsCenterProps> = ({
  initialTab = "users",
  currentUser = null,
  onCurrentUserChange,
}) => {
  const { language, setLanguage } = useLanguage();
  const text =
    language === "en"
      ? {
        tabs: {
          users: {
            label: "Users",
            description: "Manage app users and role access.",
          },
          data: {
            label: "Data (Conversation)",
            description:
              "Control conversation retention, export, and deletion policies.",
          },
          security: {
            label: "Security",
            description:
              "Configure MFA, session, and key management policies.",
          },
          logs: {
            label: "Logs",
            description: "Search and monitor system audit logs.",
          },
          language: {
            label: "Language",
            description: "Choose your interface and response language.",
          },
          appearance: {
            label: "Appearance",
            description: "Choose light or dark interface theme.",
          },
          accessModel: {
            label: "Access Model",
            description:
              "Build organizations, departments, roles, and user access templates.",
          },
          newsTable: {
            label: "News Table",
            description:
              "View Kimi-reviewed intelligence events stored in DB.",
          },
          products: {
            label: "Product Catalog",
            description:
              "Manage custom upstream, midstream, and downstream products.",
          },
          platform: {
            label: "Platform Builder",
            description:
              "Configure v2 platform primitives and semantic resources.",
          },
        },
        title: "Settings",
        subtitle:
          "Configure users, data governance, security policies, logs, and language preferences.",
        usersTitle: "Users",
        usersSubtitle: "Manage login accounts for the application.",
        adminHelp:
          "You are signed in as admin. You can create additional users and choose whether they are admins.",
        readonlyHelp: "Read-only mode. Only admin users can create accounts.",
        adminAccess: "Admin Access",
        username: "Username",
        initialPassword: "Initial temporary password",
        initialPasswordHint:
          "The user will be required to change this password after the first login.",
        adminUser: "Admin user",
        createUser: "Create User",
        creating: "Creating...",
        userToReset: "User to reset",
        oneTimePassword: "One-time temporary password",
        oneTimeHint:
          "Admin can set a temporary password, but cannot view the user's future password after the user changes it.",
        noUsersLoaded: "No users loaded",
        useSecurityTab: "(use Security tab)",
        applying: "Applying...",
        setTempPassword: "Set Temporary Password",
        bestPractice:
          "Best practice: admin sets a one-time temporary password, the user logs in, and the system forces the user to choose a new private password immediately.",
        searchUsers: "Search by username or creator",
        allRoles: "all roles",
        roleAdmin: "admin",
        roleUser: "user",
        created: "Created",
        createdBy: "Created By",
        status: "Status",
        nextLogin: "Next Login",
        actions: "Actions",
        loginId: "Login ID",
        active: "active",
        disabled: "disabled",
        mustChangePassword: "Must change password",
        normal: "Normal",
        updating: "Updating...",
        disable: "Disable",
        reactivate: "Reactivate",
        loadingUsers: "Loading users...",
        noUsers: "No users found for current filters.",
        dataTitle: "Data (Conversation)",
        dataSubtitle:
          "Manage conversation records, search history, and cleanup actions.",
        conversation: "Conversation",
        import: "Import",
        export: "Export",
        schema: "Schema",
        recentlyDeleted: "Recently Deleted",
        refresh: "Refresh",
        deleteAll: "Delete All",
        add: "Add",
        searchConversations: "Search by title, product_context",
        filters: "Filters",
        loadingConversation: "Loading conversation history...",
        conversationTitle: "title",
        conversationMessages: "messages",
        conversationStatus: "status",
        conversationProductContext: "product_context",
        noConversation: "No conversation records found.",
        deleteConversation: "Delete conversation",
        securityTitle: "Security",
        securitySubtitle:
          "Configure sign-in hardening, sessions, and key governance.",
        changePassword: "Change Password",
        changePasswordHint: "Update the password for",
        currentUser: "current user",
        currentPassword: "Current password",
        newPassword: "New password",
        confirmNewPassword: "Confirm new password",
        updatePassword: "Update Password",
        enforceMfa: "Enforce MFA for Admin/Owner",
        enforceMfaHint: "Require second factor on privileged accounts.",
        sessionTimeout: "Session timeout (minutes)",
        lockoutThreshold: "Failed login lockout threshold",
        keyRotation: "API key rotation (days)",
        logsTitle: "Logs Explorer",
        logsSubtitle: "Monitor system logs across your application.",
        filterByEmail: "Filter by email...",
        allTypes: "All Types",
        errorsOnly: "Errors only",
        type: "Type",
        user: "User",
        timestamp: "Timestamp",
        languageTitle: "Language",
        languageSubtitle:
          "Choose how the application UI and AI responses are displayed for your account on this browser.",
        languageCurrent: "Current language",
        languageEnglish: "English",
        languageChinese: "Chinese",
        languageEnglishHint:
          "Show navigation, dashboard labels, settings, and chat requests in English.",
        languageChineseHint:
          "以中文显示导航、驾驶舱标签、设置界面和聊天请求。",
        languageSaved:
          "Language changes are applied immediately and stored in your browser preferences.",
        appearanceTitle: "Appearance",
        appearanceSubtitle:
          "Choose how the application interface looks on this browser.",
        appearanceCurrent: "Current theme",
        appearanceLight: "Light",
        appearanceDark: "Dark",
        appearanceLightHint:
          "Bright panels and light backgrounds across chat, dashboard, and settings.",
        appearanceDarkHint:
          "Dark panels and reduced glare for low-light environments.",
        appearanceSaved:
          "Theme changes are applied immediately and stored in your browser preferences.",
      }
      : {
        tabs: {
          users: { label: "用户", description: "管理系统用户和角色权限。" },
          data: {
            label: "数据（对话）",
            description: "管理对话保留、导出和删除策略。",
          },
          security: {
            label: "安全",
            description: "配置 MFA、会话和密钥管理策略。",
          },
          logs: { label: "日志", description: "搜索并监控系统审计日志。" },
          language: { label: "语言", description: "选择界面与回复语言。" },
          appearance: { label: "外观", description: "选择浅色或深色界面主题。" },
          accessModel: {
            label: "访问模型",
            description: "构建组织、部门、角色和用户访问模板。",
          },
          newsTable: {
            label: "新闻表",
            description: "查看 Kimi 自动审核的数据库情报事件。",
          },
          products: {
            label: "产品目录",
            description: "管理上游、中游、下游自定义产品。",
          },
          platform: {
            label: "平台构建器",
            description: "配置 v2 平台原语与语义资源。",
          },
        },
        title: "设置",
        subtitle: "配置用户、数据治理、安全策略、日志和语言偏好。",
        usersTitle: "用户",
        usersSubtitle: "管理应用登录账户。",
        adminHelp:
          "您当前以管理员身份登录，可以创建新用户并选择其是否为管理员。",
        readonlyHelp: "当前为只读模式，仅管理员可以创建账户。",
        adminAccess: "管理员权限",
        username: "用户名",
        initialPassword: "初始临时密码",
        initialPasswordHint: "该用户首次登录后将被强制修改密码。",
        adminUser: "管理员用户",
        createUser: "创建用户",
        creating: "创建中...",
        userToReset: "重置目标用户",
        oneTimePassword: "一次性临时密码",
        oneTimeHint:
          "管理员可以设置临时密码，但无法查看用户之后自行修改的新密码。",
        noUsersLoaded: "暂无用户数据",
        useSecurityTab: "（请使用安全标签）",
        applying: "应用中...",
        setTempPassword: "设置临时密码",
        bestPractice:
          "最佳实践：管理员设置一次性临时密码，用户登录后，系统立即强制其设置新的私人密码。",
        searchUsers: "按用户名或创建者搜索",
        allRoles: "全部角色",
        roleAdmin: "管理员",
        roleUser: "用户",
        created: "创建时间",
        createdBy: "创建者",
        status: "状态",
        nextLogin: "下次登录",
        actions: "操作",
        loginId: "登录 ID",
        active: "启用",
        disabled: "停用",
        mustChangePassword: "必须修改密码",
        normal: "正常",
        updating: "更新中...",
        disable: "停用",
        reactivate: "重新启用",
        loadingUsers: "正在加载用户...",
        noUsers: "当前筛选条件下没有用户。",
        dataTitle: "数据（对话）",
        dataSubtitle: "管理对话记录、检索历史和清理操作。",
        conversation: "对话",
        import: "导入",
        export: "导出",
        schema: "结构",
        recentlyDeleted: "最近删除",
        refresh: "刷新",
        deleteAll: "全部删除",
        add: "新增",
        searchConversations: "按标题或 product_context 搜索",
        filters: "筛选",
        loadingConversation: "正在加载对话历史...",
        conversationTitle: "标题",
        conversationMessages: "消息",
        conversationStatus: "状态",
        conversationProductContext: "产品上下文",
        noConversation: "未找到对话记录。",
        deleteConversation: "删除对话",
        securityTitle: "安全",
        securitySubtitle: "配置登录加固、会话和密钥治理。",
        changePassword: "修改密码",
        changePasswordHint: "为以下账户更新密码：",
        currentUser: "当前用户",
        currentPassword: "当前密码",
        newPassword: "新密码",
        confirmNewPassword: "确认新密码",
        updatePassword: "更新密码",
        enforceMfa: "管理员/负责人强制启用 MFA",
        enforceMfaHint: "对高权限账户要求二次验证。",
        sessionTimeout: "会话超时（分钟）",
        lockoutThreshold: "登录失败锁定阈值",
        keyRotation: "API 密钥轮换（天）",
        logsTitle: "日志浏览",
        logsSubtitle: "监控整个应用的系统日志。",
        filterByEmail: "按邮箱筛选...",
        allTypes: "全部类型",
        errorsOnly: "仅错误",
        type: "类型",
        user: "用户",
        timestamp: "时间戳",
        languageTitle: "语言",
        languageSubtitle:
          "选择当前浏览器下此账户的应用界面和 AI 回复显示语言。",
        languageCurrent: "当前语言",
        languageEnglish: "英文",
        languageChinese: "中文",
        languageEnglishHint:
          "用英文显示导航、驾驶舱标签、设置界面和聊天请求。",
        languageChineseHint:
          "用中文显示导航、驾驶舱标签、设置界面和聊天请求。",
        languageSaved: "语言切换会立即生效，并保存在浏览器偏好中。",
        appearanceTitle: "外观",
        appearanceSubtitle: "选择当前浏览器下此账户的应用界面显示主题。",
        appearanceCurrent: "当前主题",
        appearanceLight: "浅色",
        appearanceDark: "深色",
        appearanceLightHint: "聊天、驾驶舱和设置界面使用明亮面板与浅色背景。",
        appearanceDarkHint: "深色面板，适合弱光环境，减轻眩光。",
        appearanceSaved: "主题切换会立即生效，并保存在浏览器偏好中。",
      };
  const { theme, setTheme } = useTheme();
  const [tab, setTab] = useState<SettingsTab>(() =>
    normalizeSettingsTab(initialTab, Boolean(currentUser?.is_admin)),
  );
  const [users, setUsers] = useState<AuthUser[]>([]);
  const [isUsersLoading, setIsUsersLoading] = useState(false);
  const [usersError, setUsersError] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState("");
  const [roleFilter, setRoleFilter] = useState("all roles");
  const [newUsername, setNewUsername] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newUserIsAdmin, setNewUserIsAdmin] = useState(false);
  const [isCreatingUser, setIsCreatingUser] = useState(false);
  const [resetUserId, setResetUserId] = useState("");
  const [resetPasswordValue, setResetPasswordValue] = useState("");
  const [isResettingUserPassword, setIsResettingUserPassword] = useState(false);
  const [resetPasswordStatus, setResetPasswordStatus] = useState<string | null>(
    null,
  );
  const [resetPasswordError, setResetPasswordError] = useState<string | null>(
    null,
  );
  const [isUpdatingUserStatus, setIsUpdatingUserStatus] = useState<
    string | null
  >(null);

  const [conversationSearch, setConversationSearch] = useState("");
  const [showConversationMenu, setShowConversationMenu] = useState(false);
  const [conversations, setConversations] = useState<ConversationRow[]>([]);
  const [recentlyDeleted, setRecentlyDeleted] = useState<ConversationRow[]>([]);
  const [isConversationLoading, setIsConversationLoading] = useState(false);
  const [conversationError, setConversationError] = useState<string | null>(
    null,
  );

  const [enforceMfaAdmins, setEnforceMfaAdmins] = useState(true);
  const [sessionTimeout, setSessionTimeout] = useState("60");
  const [lockoutThreshold, setLockoutThreshold] = useState("5");
  const [apiKeyRotationDays, setApiKeyRotationDays] = useState("90");
  const [currentPassword, setCurrentPassword] = useState("");
  const [nextPassword, setNextPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [passwordStatus, setPasswordStatus] = useState<string | null>(null);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [isChangingPassword, setIsChangingPassword] = useState(false);

  const [errorsOnly, setErrorsOnly] = useState(false);
  const [organizations, setOrganizations] = useState<OrganizationTemplate[]>(
    DEFAULT_ORGANIZATIONS,
  );
  const [departments, setDepartments] =
    useState<DepartmentTemplate[]>(DEFAULT_DEPARTMENTS);
  const [roles, setRoles] = useState<RoleTemplate[]>(DEFAULT_ROLES);
  const [accessUsers, setAccessUsers] =
    useState<UserAccessTemplate[]>(DEFAULT_USER_ACCESS);
  const [previewUserId, setPreviewUserId] = useState(
    DEFAULT_USER_ACCESS[0]?.id || "",
  );

  const [newOrgName, setNewOrgName] = useState("");
  const [newOrgProductChain, setNewOrgProductChain] = useState("");
  const [newOrgRegionCoverage, setNewOrgRegionCoverage] = useState("");
  const [newDepartmentOrgId, setNewDepartmentOrgId] = useState(
    DEFAULT_ORGANIZATIONS[0]?.id || "",
  );
  const [newDepartmentName, setNewDepartmentName] = useState("");
  const [newRoleName, setNewRoleName] = useState("");
  const [newRoleOrgId, setNewRoleOrgId] = useState(
    DEFAULT_ORGANIZATIONS[0]?.id || "",
  );
  const [newRoleDepartmentId, setNewRoleDepartmentId] = useState(
    DEFAULT_DEPARTMENTS[0]?.id || "",
  );
  const [newRoleProductScope, setNewRoleProductScope] = useState("");
  const [newRoleRegionScope, setNewRoleRegionScope] = useState("");
  const [newRoleDataAccess, setNewRoleDataAccess] = useState("");
  const [newRoleDashboard, setNewRoleDashboard] = useState("");
  const [newRoleFeatures, setNewRoleFeatures] = useState<FeatureAccessConfig>({
    ...DEFAULT_FEATURE_ACCESS,
  });
  const [cloneRoleId, setCloneRoleId] = useState("");

  const [newAccessUsername, setNewAccessUsername] = useState("");
  const [newAccessPassword, setNewAccessPassword] = useState("");
  const [newAccessOrganizationId, setNewAccessOrganizationId] = useState(
    DEFAULT_ORGANIZATIONS[0]?.id || "",
  );
  const [newAccessDepartmentId, setNewAccessDepartmentId] = useState(
    DEFAULT_DEPARTMENTS[0]?.id || "",
  );
  const [newAccessRoleId, setNewAccessRoleId] = useState(
    DEFAULT_ROLES[0]?.id || "",
  );
  const [newAccessFeatures, setNewAccessFeatures] =
    useState<FeatureAccessConfig>({ ...DEFAULT_FEATURE_ACCESS });
  const [isAccessModelLoading, setIsAccessModelLoading] = useState(false);
  const [isAccessModelSaving, setIsAccessModelSaving] = useState(false);
  const [accessModelStatus, setAccessModelStatus] = useState<string | null>(
    null,
  );
  const [accessModelError, setAccessModelError] = useState<string | null>(null);
  const [newsRows, setNewsRows] = useState<EventModerationItem[]>([]);
  const [newsCount, setNewsCount] = useState(0);
  const [newsHours, setNewsHours] = useState(168);
  const [newsLimit, setNewsLimit] = useState(200);
  const [newsMinUsefulness, setNewsMinUsefulness] = useState(0);
  const [newsReviewStatus, setNewsReviewStatus] =
    useState<EventReviewStatus>("all");
  const [newsSummary, setNewsSummary] =
    useState<EventReviewSummaryResponse | null>(null);
  const [newsLastUpdatedAt, setNewsLastUpdatedAt] = useState<string | null>(
    null,
  );
  const [isNewsLoading, setIsNewsLoading] = useState(false);
  const [newsError, setNewsError] = useState<string | null>(null);
  const currentUserIsAdmin = Boolean(currentUser?.is_admin);
  const tr = (en: string, zh: string) => (language === "en" ? en : zh);

  const formatDateTime = (value?: string | null) => {
    if (!value) return "-";
    const normalized = value.includes("T") ? value : value.replace(" ", "T");
    const hasTimezone = /([zZ]|[+-]\d{2}:?\d{2})$/.test(normalized);
    const parseValue = hasTimezone ? normalized : `${normalized}Z`;
    const parsed = new Date(parseValue);
    if (Number.isNaN(parsed.getTime())) {
      return value;
    }
    return parsed.toLocaleString(language === "en" ? "en-US" : "zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
      timeZone: "Asia/Shanghai",
    });
  };

  const featureLabels = useMemo<Record<keyof FeatureAccessConfig, string>>(
    () =>
      language === "en"
        ? {
          chatbot: "Chatbot",
          dashboardUpstream: "Dashboard Upstream",
          dashboardMidstream: "Dashboard Midstream",
          dashboardDownstream: "Dashboard Downstream",
          dashboardComparison: "Dashboard Comparison",
          report: "Report",
        }
        : {
          chatbot: "智能对话",
          dashboardUpstream: "驾驶舱上游",
          dashboardMidstream: "驾驶舱中游",
          dashboardDownstream: "驾驶舱下游",
          dashboardComparison: "驾驶舱对比",
          report: "报告",
        },
    [language],
  );

  const roleDepartments = useMemo(
    () =>
      departments.filter(
        (department) => department.organizationId === newRoleOrgId,
      ),
    [departments, newRoleOrgId],
  );
  const userDepartments = useMemo(
    () =>
      departments.filter(
        (department) => department.organizationId === newAccessOrganizationId,
      ),
    [departments, newAccessOrganizationId],
  );
  const userRoles = useMemo(
    () =>
      roles.filter(
        (role) =>
          role.organizationId === newAccessOrganizationId &&
          role.departmentId === newAccessDepartmentId,
      ),
    [roles, newAccessOrganizationId, newAccessDepartmentId],
  );

  const buildAccessModelPayload = (): AccessModelConfig => ({
    organizations,
    departments,
    roles,
    accessUsers,
  });

  const applyAccessModelPayload = (payload: AccessModelConfig) => {
    const normalized = normalizeAccessModelPayload(payload);
    const nextOrganizations = normalized.organizations;
    const nextDepartments = normalized.departments;
    const nextRoles = normalized.roles;
    const nextAccessUsers = normalized.accessUsers;

    setOrganizations(nextOrganizations);
    setDepartments(nextDepartments);
    setRoles(nextRoles);
    setAccessUsers(nextAccessUsers);
    setNewDepartmentOrgId(nextOrganizations[0]?.id || "");
    setNewRoleOrgId(nextOrganizations[0]?.id || "");
    setNewRoleDepartmentId(nextDepartments[0]?.id || "");
    setNewAccessOrganizationId(nextOrganizations[0]?.id || "");
    setNewAccessDepartmentId(nextDepartments[0]?.id || "");
    setNewAccessRoleId(nextRoles[0]?.id || "");
    setNewRoleFeatures(
      cloneFeatureAccess(nextRoles[0]?.featureAccess || DEFAULT_FEATURE_ACCESS),
    );
    setNewAccessFeatures(
      cloneFeatureAccess(nextRoles[0]?.featureAccess || DEFAULT_FEATURE_ACCESS),
    );
  };

  const updateOrganization = (
    id: string,
    key: keyof Omit<OrganizationTemplate, "id">,
    value: string,
  ) => {
    setOrganizations((prev) =>
      prev.map((row) => (row.id === id ? { ...row, [key]: value } : row)),
    );
  };

  const addOrganization = () => {
    if (!currentUserIsAdmin || !newOrgName.trim()) return;
    const createdName = newOrgName.trim();
    const nextOrgId = createLocalId("org");
    setOrganizations((prev) => [
      ...prev,
      {
        id: nextOrgId,
        name: createdName,
        productChain: newOrgProductChain.trim() || "Custom",
        regionCoverage: newOrgRegionCoverage.trim() || "Custom",
      },
    ]);
    setNewDepartmentOrgId(nextOrgId);
    setNewRoleOrgId(nextOrgId);
    setNewAccessOrganizationId(nextOrgId);
    setNewOrgName("");
    setNewOrgProductChain("");
    setNewOrgRegionCoverage("");
    setAccessModelError(null);
    setAccessModelStatus(
      `Added organization "${createdName}" to the draft. Click Save Template to persist it.`,
    );
  };

  const deleteOrganization = (id: string) => {
    if (!currentUserIsAdmin) return;
    setOrganizations((prev) => prev.filter((row) => row.id !== id));
    setDepartments((prev) => prev.filter((row) => row.organizationId !== id));
    setRoles((prev) => prev.filter((row) => row.organizationId !== id));
    setAccessUsers((prev) => prev.filter((row) => row.organizationId !== id));
  };

  const updateDepartment = (
    id: string,
    key: keyof Omit<DepartmentTemplate, "id">,
    value: string,
  ) => {
    setDepartments((prev) =>
      prev.map((row) => (row.id === id ? { ...row, [key]: value } : row)),
    );
  };

  const addDepartment = () => {
    if (!currentUserIsAdmin || !newDepartmentName.trim() || !newDepartmentOrgId)
      return;
    const createdName = newDepartmentName.trim();
    const nextDepartmentId = createLocalId("dept");
    setDepartments((prev) => [
      ...prev,
      {
        id: nextDepartmentId,
        name: createdName,
        organizationId: newDepartmentOrgId,
      },
    ]);
    setNewRoleOrgId(newDepartmentOrgId);
    setNewRoleDepartmentId(nextDepartmentId);
    setNewAccessOrganizationId(newDepartmentOrgId);
    setNewAccessDepartmentId(nextDepartmentId);
    setNewDepartmentName("");
    setAccessModelError(null);
    setAccessModelStatus(
      `Added department "${createdName}" to the draft. Click Save Template to persist it.`,
    );
  };

  const deleteDepartment = (id: string) => {
    if (!currentUserIsAdmin) return;
    setDepartments((prev) => prev.filter((row) => row.id !== id));
    setRoles((prev) => prev.filter((row) => row.departmentId !== id));
    setAccessUsers((prev) =>
      prev.map((row) =>
        row.departmentId === id
          ? { ...row, departmentId: "", roleId: "" }
          : row,
      ),
    );
  };

  const updateRole = (
    id: string,
    key: keyof Omit<RoleTemplate, "id">,
    value: string,
  ) => {
    setRoles((prev) =>
      prev.map((row) => {
        if (row.id !== id) return row;
        if (key === "organizationId") {
          const nextDepartmentId =
            departments.find(
              (department) => department.organizationId === value,
            )?.id || "";
          return {
            ...row,
            organizationId: value,
            departmentId: nextDepartmentId,
          };
        }
        return { ...row, [key]: value };
      }),
    );
  };

  const updateRoleFeature = (
    id: string,
    feature: keyof FeatureAccessConfig,
    checked: boolean,
  ) => {
    setRoles((prev) =>
      prev.map((row) =>
        row.id === id
          ? {
            ...row,
            featureAccess: { ...row.featureAccess, [feature]: checked },
          }
          : row,
      ),
    );
    setAccessUsers((prev) =>
      prev.map((row) => {
        if (row.roleId !== id) return row;
        return {
          ...row,
          featureAccess: { ...row.featureAccess, [feature]: checked },
        };
      }),
    );
  };

  const addRole = () => {
    if (
      !currentUserIsAdmin ||
      !newRoleName.trim() ||
      !newRoleOrgId ||
      !newRoleDepartmentId
    )
      return;
    const createdName = newRoleName.trim();
    const nextRoleId = createLocalId("role");
    setRoles((prev) => [
      ...prev,
      {
        id: nextRoleId,
        name: createdName,
        organizationId: newRoleOrgId,
        departmentId: newRoleDepartmentId,
        productScope: newRoleProductScope.trim() || "Custom",
        regionScope: newRoleRegionScope.trim() || "Custom",
        featureAccess: cloneFeatureAccess(newRoleFeatures),
        dataAccess: newRoleDataAccess.trim() || "Custom",
        dashboardTemplate: newRoleDashboard.trim() || "Custom",
      },
    ]);
    setNewAccessOrganizationId(newRoleOrgId);
    setNewAccessDepartmentId(newRoleDepartmentId);
    setNewAccessRoleId(nextRoleId);
    setNewAccessFeatures(cloneFeatureAccess(newRoleFeatures));
    setNewRoleName("");
    setNewRoleProductScope("");
    setNewRoleRegionScope("");
    setNewRoleFeatures({ ...DEFAULT_FEATURE_ACCESS });
    setNewRoleDataAccess("");
    setNewRoleDashboard("");
    setAccessModelError(null);
    setAccessModelStatus(
      `Added role "${createdName}" to the draft. Click Save Template to persist it.`,
    );
  };

  const cloneRole = () => {
    if (!currentUserIsAdmin || !cloneRoleId) return;
    const source = roles.find((row) => row.id === cloneRoleId);
    if (!source) return;
    setRoles((prev) => [
      ...prev,
      {
        ...source,
        id: createLocalId("role"),
        name: `${source.name} (Copy)`,
        featureAccess: cloneFeatureAccess(source.featureAccess),
      },
    ]);
    setAccessModelError(null);
    setAccessModelStatus(
      `Cloned role "${source.name}" into the draft. Click Save Template to persist it.`,
    );
  };

  const deleteRole = (id: string) => {
    if (!currentUserIsAdmin) return;
    setRoles((prev) => prev.filter((row) => row.id !== id));
    setAccessUsers((prev) =>
      prev.map((row) => (row.roleId === id ? { ...row, roleId: "" } : row)),
    );
  };

  const updateAccessUser = (
    id: string,
    key: keyof Omit<UserAccessTemplate, "id">,
    value: string | boolean | FeatureAccessConfig,
  ) => {
    setAccessUsers((prev) =>
      prev.map((row) => {
        if (row.id !== id) return row;
        if (key === "organizationId") {
          return {
            ...row,
            organizationId: String(value),
            departmentId: "",
            roleId: "",
          };
        }
        if (key === "departmentId") {
          return { ...row, departmentId: String(value), roleId: "" };
        }
        return { ...row, [key]: value };
      }),
    );
  };

  const updateAccessUserFeature = (
    id: string,
    feature: keyof FeatureAccessConfig,
    checked: boolean,
  ) => {
    setAccessUsers((prev) =>
      prev.map((row) =>
        row.id === id
          ? {
            ...row,
            featureAccess: { ...row.featureAccess, [feature]: checked },
          }
          : row,
      ),
    );
  };

  const addAccessUser = () => {
    if (
      !currentUserIsAdmin ||
      !newAccessUsername.trim() ||
      !newAccessPassword.trim() ||
      !newAccessOrganizationId ||
      !newAccessDepartmentId ||
      !newAccessRoleId
    )
      return;
    const createdName = newAccessUsername.trim();
    setAccessUsers((prev) => [
      ...prev,
      {
        id: createLocalId("ua"),
        username: createdName,
        password: newAccessPassword,
        organizationId: newAccessOrganizationId,
        departmentId: newAccessDepartmentId,
        roleId: newAccessRoleId,
        featureAccess: cloneFeatureAccess(newAccessFeatures),
        isActive: true,
        oneTimeOverride: "",
      },
    ]);
    setNewAccessUsername("");
    setNewAccessPassword("");
    setNewAccessFeatures(
      cloneFeatureAccess(
        roles.find((role) => role.id === newAccessRoleId)?.featureAccess ||
        DEFAULT_FEATURE_ACCESS,
      ),
    );
    setAccessModelError(null);
    setAccessModelStatus(
      `Added user template "${createdName}" to the draft. Click Save Template to persist it.`,
    );
  };

  const deleteAccessUser = (id: string) => {
    if (!currentUserIsAdmin) return;
    setAccessUsers((prev) => prev.filter((row) => row.id !== id));
  };

  const selectedPreviewUser = useMemo(() => {
    return accessUsers.find((row) => row.id === previewUserId) || null;
  }, [accessUsers, previewUserId]);

  const selectedPreviewRole = useMemo(() => {
    if (!selectedPreviewUser) return null;
    return roles.find((row) => row.id === selectedPreviewUser.roleId) || null;
  }, [roles, selectedPreviewUser]);

  const selectedPreviewDepartment = useMemo(() => {
    if (!selectedPreviewUser) return null;
    return (
      departments.find((row) => row.id === selectedPreviewUser.departmentId) ||
      null
    );
  }, [departments, selectedPreviewUser]);

  const selectedPreviewOrganization = useMemo(() => {
    if (!selectedPreviewUser) return null;
    return (
      organizations.find(
        (row) => row.id === selectedPreviewUser.organizationId,
      ) || null
    );
  }, [organizations, selectedPreviewUser]);

  const previewExperience = useMemo(() => {
    if (!selectedPreviewUser || !selectedPreviewRole)
      return "Select a user profile to preview their EDIA view.";
    const customSummary = `${selectedPreviewRole.name}: ${selectedPreviewRole.productScope}; ${selectedPreviewRole.regionScope}; ${featureAccessSummary(selectedPreviewUser.featureAccess, featureLabels)}.`;
    return customSummary;
  }, [selectedPreviewUser, selectedPreviewRole, featureLabels]);

  useEffect(() => {
    const nextDepartments = departments.filter(
      (department) => department.organizationId === newRoleOrgId,
    );
    if (
      !nextDepartments.some(
        (department) => department.id === newRoleDepartmentId,
      )
    ) {
      setNewRoleDepartmentId(nextDepartments[0]?.id || "");
    }
  }, [departments, newRoleOrgId, newRoleDepartmentId]);

  useEffect(() => {
    const nextDepartments = departments.filter(
      (department) => department.organizationId === newAccessOrganizationId,
    );
    if (
      !nextDepartments.some(
        (department) => department.id === newAccessDepartmentId,
      )
    ) {
      setNewAccessDepartmentId(nextDepartments[0]?.id || "");
    }
  }, [departments, newAccessOrganizationId, newAccessDepartmentId]);

  useEffect(() => {
    const nextRoles = roles.filter(
      (role) =>
        role.organizationId === newAccessOrganizationId &&
        role.departmentId === newAccessDepartmentId,
    );
    if (!nextRoles.some((role) => role.id === newAccessRoleId)) {
      const fallbackRole = nextRoles[0];
      setNewAccessRoleId(fallbackRole?.id || "");
      setNewAccessFeatures(
        cloneFeatureAccess(
          fallbackRole?.featureAccess || DEFAULT_FEATURE_ACCESS,
        ),
      );
    }
  }, [roles, newAccessOrganizationId, newAccessDepartmentId, newAccessRoleId]);

  useEffect(() => {
    if (!accessUsers.length) {
      setPreviewUserId("");
      return;
    }
    if (!accessUsers.some((row) => row.id === previewUserId)) {
      setPreviewUserId(accessUsers[0].id);
    }
  }, [accessUsers, previewUserId]);

  useEffect(() => {
    setTab(normalizeSettingsTab(initialTab, currentUserIsAdmin));
  }, [initialTab, currentUserIsAdmin]);

  const loadUsers = async () => {
    if (!currentUser?.is_admin) {
      setUsers([]);
      return;
    }

    setIsUsersLoading(true);
    setUsersError(null);
    try {
      const rows = await api.getUsers();
      setUsers(rows);
      setResetUserId((current) => {
        const resetCandidates = rows.filter(
          (user) => user.id !== currentUser?.id,
        );
        if (current && resetCandidates.some((user) => user.id === current)) {
          return current;
        }
        return resetCandidates[0]?.id || "";
      });
    } catch (err) {
      console.error("Failed to load users", err);
      setUsersError("Unable to load users right now.");
    } finally {
      setIsUsersLoading(false);
    }
  };

  const loadAccessModel = async () => {
    if (!currentUserIsAdmin) return;
    setIsAccessModelLoading(true);
    setAccessModelError(null);
    try {
      const payload = await api.getAccessModel();
      applyAccessModelPayload(payload);
      if (payload.updated_at) {
        setAccessModelStatus(
          `Loaded saved template from ${payload.updated_at}${payload.updated_by ? ` by ${payload.updated_by}` : ""}.`,
        );
      } else {
        setAccessModelStatus("Loaded access template.");
      }
    } catch (err) {
      console.error("Failed to load access model", err);
      setAccessModelError(
        err instanceof Error ? err.message : "Failed to load access model.",
      );
    } finally {
      setIsAccessModelLoading(false);
    }
  };

  const loadNewsModeration = async () => {
    if (!currentUserIsAdmin) return;
    setIsNewsLoading(true);
    setNewsError(null);
    try {
      const [queue, summary, status] = await Promise.all([
        api.getEventModerationQueue({
          hours: newsHours,
          review_status: newsReviewStatus,
          min_usefulness_score: newsMinUsefulness,
          limit: newsLimit,
        }),
        api.getEventReviewSummary(newsHours),
        api.getIntelligenceStatus(),
      ]);
      setNewsRows(queue.events || []);
      setNewsCount(queue.count || 0);
      setNewsSummary(summary);
      setNewsLastUpdatedAt(
        status?.agent?.last_success_at || status?.agent?.last_completed_at || null,
      );
    } catch (err) {
      console.error("Failed to load news moderation queue", err);
      setNewsError(
        err instanceof Error
          ? err.message
          : "Failed to load intelligence events.",
      );
    } finally {
      setIsNewsLoading(false);
    }
  };

  const saveAccessModel = async () => {
    if (!currentUserIsAdmin) return;
    setIsAccessModelSaving(true);
    setAccessModelError(null);
    try {
      const payload = await api.updateAccessModel(buildAccessModelPayload());
      setAccessModelStatus(
        `Saved at ${payload.updated_at || "now"}${payload.updated_by ? ` by ${payload.updated_by}` : ""}.`,
      );
    } catch (err) {
      console.error("Failed to save access model", err);
      setAccessModelError(
        err instanceof Error ? err.message : "Failed to save access model.",
      );
    } finally {
      setIsAccessModelSaving(false);
    }
  };

  const loadConversationHistory = async () => {
    setIsConversationLoading(true);
    setConversationError(null);
    try {
      const sessions = await api.getSessions();
      const rows = await Promise.all(
        sessions.map(async (session) => {
          const history = await api.getSessionHistory(session.id);
          return toConversationRow(session, history);
        }),
      );
      setConversations(rows);
    } catch (err) {
      console.error("Failed to load conversation history", err);
      setConversationError("Unable to load chat history right now.");
    } finally {
      setIsConversationLoading(false);
    }
  };

  useEffect(() => {
    if (tab === "data") {
      loadConversationHistory();
    }
  }, [tab]);

  useEffect(() => {
    if (tab === "users") {
      loadUsers();
    }
  }, [tab, currentUser?.id]);

  useEffect(() => {
    if (tab === "accessModel") {
      loadAccessModel();
    }
  }, [tab, currentUser?.id]);

  useEffect(() => {
    if (tab === "newsTable") {
      loadNewsModeration();
    }
  }, [tab, currentUser?.id, newsHours, newsReviewStatus, newsMinUsefulness, newsLimit]);

  useEffect(() => {
    if (!canViewSettingsTab(tab, currentUserIsAdmin)) {
      setTab(normalizeSettingsTab(tab, currentUserIsAdmin));
    }
  }, [currentUserIsAdmin, tab]);

  const localizedTabs = useMemo(
    () =>
      TABS.map((item) => ({
        ...item,
        label: text.tabs[item.id].label,
        description: text.tabs[item.id].description,
      })).filter((item) => canViewSettingsTab(item.id, currentUserIsAdmin)),
    [text, currentUserIsAdmin],
  );
  const active = useMemo(
    () => localizedTabs.find((x) => x.id === tab) || localizedTabs[0],
    [tab, localizedTabs],
  );
  const activeTab = active?.id || "users";

  const filteredUsers = useMemo(() => {
    return users.filter((u) => {
      const q = searchTerm.trim().toLowerCase();
      const passSearch =
        !q ||
        u.username.toLowerCase().includes(q) ||
        (u.created_by || "").toLowerCase().includes(q);
      const passRole =
        roleFilter === "all roles" ||
        (roleFilter === "admin" ? u.is_admin : !u.is_admin);
      return passSearch && passRole;
    });
  }, [users, searchTerm, roleFilter]);

  const filteredConversations = useMemo(() => {
    const q = conversationSearch.trim().toLowerCase();
    if (!q) return conversations;
    return conversations.filter((c) => {
      return (
        c.title.toLowerCase().includes(q) ||
        c.product_context.toLowerCase().includes(q)
      );
    });
  }, [conversations, conversationSearch]);

  const handleCreateUser = async () => {
    if (!currentUserIsAdmin || !newUsername.trim() || !newPassword.trim())
      return;

    setIsCreatingUser(true);
    setUsersError(null);
    try {
      await api.createUser(newUsername.trim(), newPassword, newUserIsAdmin);
      setNewUsername("");
      setNewPassword("");
      setNewUserIsAdmin(false);
      await loadUsers();
    } catch (err) {
      console.error("Failed to create user", err);
      setUsersError(
        err instanceof Error ? err.message : "Failed to create user.",
      );
    } finally {
      setIsCreatingUser(false);
    }
  };

  const handleChangePassword = async () => {
    if (!currentPassword || !nextPassword || !confirmPassword) {
      setPasswordError("All password fields are required.");
      setPasswordStatus(null);
      return;
    }

    if (nextPassword !== confirmPassword) {
      setPasswordError("New password and confirmation do not match.");
      setPasswordStatus(null);
      return;
    }

    setIsChangingPassword(true);
    setPasswordError(null);
    setPasswordStatus(null);
    try {
      const response = await api.changePassword({
        current_password: currentPassword,
        new_password: nextPassword,
      });
      onCurrentUserChange?.(response.user);
      setCurrentPassword("");
      setNextPassword("");
      setConfirmPassword("");
      setPasswordStatus(
        "Password updated. Your session was rotated to the new credentials.",
      );
    } catch (err) {
      console.error("Failed to change password", err);
      setPasswordError(
        err instanceof Error ? err.message : "Failed to change password.",
      );
    } finally {
      setIsChangingPassword(false);
    }
  };

  const handleResetUserPassword = async () => {
    if (!currentUserIsAdmin || !resetUserId || !resetPasswordValue.trim()) {
      setResetPasswordError("Select a user and enter a new password.");
      setResetPasswordStatus(null);
      return;
    }

    setIsResettingUserPassword(true);
    setResetPasswordError(null);
    setResetPasswordStatus(null);
    try {
      const response = await api.resetUserPassword(
        resetUserId,
        resetPasswordValue.trim(),
      );
      setResetPasswordValue("");
      setResetPasswordStatus(response.message);
      await loadUsers();
    } catch (err) {
      console.error("Failed to reset user password", err);
      setResetPasswordError(
        err instanceof Error ? err.message : "Failed to reset user password.",
      );
    } finally {
      setIsResettingUserPassword(false);
    }
  };

  const handleToggleUserStatus = async (user: AuthUser) => {
    if (!currentUserIsAdmin) return;

    setIsUpdatingUserStatus(user.id);
    setUsersError(null);
    setResetPasswordError(null);
    setResetPasswordStatus(null);
    try {
      const response = await api.updateUserStatus(user.id, !user.is_active);
      setResetPasswordStatus(response.message);
      await loadUsers();
    } catch (err) {
      console.error("Failed to update user status", err);
      setUsersError(
        err instanceof Error ? err.message : "Failed to update user status.",
      );
    } finally {
      setIsUpdatingUserStatus(null);
    }
  };

  const handleDeleteConversation = (id: string) => {
    setConversations((prev) => {
      const target = prev.find((x) => x.id === id);
      if (!target) return prev;
      setRecentlyDeleted((old) => [target, ...old].slice(0, 20));
      return prev.filter((x) => x.id !== id);
    });
  };

  const handleDeleteAllConversations = () => {
    setRecentlyDeleted((old) => [...conversations, ...old].slice(0, 50));
    setConversations([]);
    setShowConversationMenu(false);
  };

  const accessModelBuilderPanel = (
    <div className="rounded-xl border border-slate-200 bg-white p-5 md:p-6 space-y-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-xl font-semibold text-slate-900">
            {tr("Admin Access Model Builder", "管理员访问模型构建器")}
          </h2>
          <p className="text-sm text-slate-600">
            {tr(
              "Configure organizations, departments, roles, and user assignments. Admin can add, edit, clone, and delete everything.",
              "配置组织、部门、角色和用户分配。管理员可新增、编辑、克隆和删除所有配置。",
            )}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={loadAccessModel}
            disabled={!currentUserIsAdmin || isAccessModelLoading}
            className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 disabled:bg-slate-100 disabled:text-slate-400"
          >
            {isAccessModelLoading
              ? tr("Loading...", "加载中...")
              : tr("Reload", "重新加载")}
          </button>
          <button
            type="button"
            onClick={saveAccessModel}
            disabled={!currentUserIsAdmin || isAccessModelSaving}
            className="rounded-lg bg-slate-900 px-3 py-2 text-sm text-white disabled:bg-slate-200 disabled:text-slate-500"
          >
            {isAccessModelSaving
              ? tr("Saving...", "保存中...")
              : tr("Save Template", "保存模板")}
          </button>
        </div>
      </div>

      {accessModelStatus && (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-800">
          {accessModelStatus}
        </div>
      )}

      {accessModelError && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">
          {accessModelError}
        </div>
      )}

      <div className="rounded-lg border border-slate-200 bg-white p-4 space-y-3">
        <h4 className="text-sm font-semibold text-slate-900">
          {tr(
            "Step 1: Admin Creates the Organization",
            "步骤1：管理员创建组织",
          )}
        </h4>
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_1fr_1fr_auto] gap-2 items-end">
          <input
            type="text"
            placeholder={tr("Organization name", "组织名称")}
            value={newOrgName}
            onChange={(e) => setNewOrgName(e.target.value)}
            disabled={!currentUserIsAdmin}
            className="rounded-lg border border-slate-200 px-3 py-2 text-sm disabled:bg-slate-100"
          />
          <input
            type="text"
            placeholder={tr("Product chain", "产品链")}
            value={newOrgProductChain}
            onChange={(e) => setNewOrgProductChain(e.target.value)}
            disabled={!currentUserIsAdmin}
            className="rounded-lg border border-slate-200 px-3 py-2 text-sm disabled:bg-slate-100"
          />
          <input
            type="text"
            placeholder={tr("Region coverage", "区域覆盖")}
            value={newOrgRegionCoverage}
            onChange={(e) => setNewOrgRegionCoverage(e.target.value)}
            disabled={!currentUserIsAdmin}
            className="rounded-lg border border-slate-200 px-3 py-2 text-sm disabled:bg-slate-100"
          />
          <button
            type="button"
            onClick={addOrganization}
            disabled={!currentUserIsAdmin || !newOrgName.trim()}
            className="rounded-lg bg-slate-900 text-white px-3 py-2 text-sm disabled:bg-slate-200 disabled:text-slate-500"
          >
            {tr("Add Organization", "新增组织")}
          </button>
        </div>
        <div className="space-y-2">
          {organizations.map((org) => (
            <div
              key={org.id}
              className="grid grid-cols-1 lg:grid-cols-[1fr_1fr_1fr_auto] gap-2 items-center"
            >
              <input
                type="text"
                value={org.name}
                onChange={(e) =>
                  updateOrganization(org.id, "name", e.target.value)
                }
                disabled={!currentUserIsAdmin}
                className="rounded-lg border border-slate-200 px-3 py-2 text-sm disabled:bg-slate-100"
              />
              <input
                type="text"
                value={org.productChain}
                onChange={(e) =>
                  updateOrganization(org.id, "productChain", e.target.value)
                }
                disabled={!currentUserIsAdmin}
                className="rounded-lg border border-slate-200 px-3 py-2 text-sm disabled:bg-slate-100"
              />
              <input
                type="text"
                value={org.regionCoverage}
                onChange={(e) =>
                  updateOrganization(org.id, "regionCoverage", e.target.value)
                }
                disabled={!currentUserIsAdmin}
                className="rounded-lg border border-slate-200 px-3 py-2 text-sm disabled:bg-slate-100"
              />
              <button
                type="button"
                onClick={() => deleteOrganization(org.id)}
                disabled={!currentUserIsAdmin}
                className="rounded-lg border border-rose-200 text-rose-600 px-3 py-2 text-sm disabled:bg-slate-100 disabled:text-slate-400"
              >
                {tr("Delete", "删除")}
              </button>
            </div>
          ))}
        </div>
      </div>

      <div className="rounded-lg border border-slate-200 bg-white p-4 space-y-3">
        <h4 className="text-sm font-semibold text-slate-900">
          {tr(
            "Step 2: Departments Must Belong to One Organization",
            "步骤2：部门必须归属一个组织",
          )}
        </h4>
        <div className="grid grid-cols-1 md:grid-cols-[1fr_1fr_auto] gap-2">
          <select
            value={newDepartmentOrgId}
            onChange={(e) => setNewDepartmentOrgId(e.target.value)}
            disabled={!currentUserIsAdmin}
            className="rounded-lg border border-slate-200 px-3 py-2 text-sm bg-white disabled:bg-slate-100"
          >
            {organizations.map((org) => (
              <option key={org.id} value={org.id}>
                {org.name}
              </option>
            ))}
          </select>
          <input
            type="text"
            placeholder={tr("Department name", "部门名称")}
            value={newDepartmentName}
            onChange={(e) => setNewDepartmentName(e.target.value)}
            disabled={!currentUserIsAdmin}
            className="rounded-lg border border-slate-200 px-3 py-2 text-sm disabled:bg-slate-100"
          />
          <button
            type="button"
            onClick={addDepartment}
            disabled={!currentUserIsAdmin || !newDepartmentName.trim()}
            className="rounded-lg bg-slate-900 text-white px-3 py-2 text-sm disabled:bg-slate-200 disabled:text-slate-500"
          >
            {tr("Add Department", "新增部门")}
          </button>
        </div>
        <div className="grid grid-cols-1 gap-2">
          {departments.map((department) => (
            <div
              key={department.id}
              className="grid grid-cols-1 md:grid-cols-[1fr_1fr_auto] gap-2 items-center"
            >
              <select
                value={department.organizationId}
                onChange={(e) =>
                  updateDepartment(
                    department.id,
                    "organizationId",
                    e.target.value,
                  )
                }
                disabled={!currentUserIsAdmin}
                className="rounded-lg border border-slate-200 px-3 py-2 text-sm bg-white disabled:bg-slate-100"
              >
                {organizations.map((org) => (
                  <option key={org.id} value={org.id}>
                    {org.name}
                  </option>
                ))}
              </select>
              <input
                type="text"
                value={department.name}
                onChange={(e) =>
                  updateDepartment(department.id, "name", e.target.value)
                }
                disabled={!currentUserIsAdmin}
                className="flex-1 rounded-lg border border-slate-200 px-3 py-2 text-sm disabled:bg-slate-100"
              />
              <button
                type="button"
                onClick={() => deleteDepartment(department.id)}
                disabled={!currentUserIsAdmin}
                className="rounded-lg border border-rose-200 text-rose-600 px-3 py-2 text-sm disabled:bg-slate-100 disabled:text-slate-400"
              >
                {tr("Delete", "删除")}
              </button>
            </div>
          ))}
        </div>
      </div>

      <div className="rounded-lg border border-slate-200 bg-white p-4 space-y-3">
        <h4 className="text-sm font-semibold text-slate-900">
          {tr(
            "Step 3: Roles Must Belong to One Department",
            "步骤3：角色必须归属一个部门",
          )}
        </h4>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
          <select
            value={newRoleOrgId}
            onChange={(e) => setNewRoleOrgId(e.target.value)}
            disabled={!currentUserIsAdmin}
            className="rounded-lg border border-slate-200 px-3 py-2 text-sm bg-white disabled:bg-slate-100"
          >
            {organizations.map((org) => (
              <option key={org.id} value={org.id}>
                {org.name}
              </option>
            ))}
          </select>
          <select
            value={newRoleDepartmentId}
            onChange={(e) => setNewRoleDepartmentId(e.target.value)}
            disabled={!currentUserIsAdmin || roleDepartments.length === 0}
            className="rounded-lg border border-slate-200 px-3 py-2 text-sm bg-white disabled:bg-slate-100"
          >
            <option value="">{tr("Select department", "选择部门")}</option>
            {roleDepartments.map((department) => (
              <option key={department.id} value={department.id}>
                {department.name}
              </option>
            ))}
          </select>
          <input
            type="text"
            placeholder={tr("Role name", "角色名称")}
            value={newRoleName}
            onChange={(e) => setNewRoleName(e.target.value)}
            disabled={!currentUserIsAdmin}
            className="rounded-lg border border-slate-200 px-3 py-2 text-sm disabled:bg-slate-100"
          />
          <input
            type="text"
            placeholder={tr("Product scope", "产品范围")}
            value={newRoleProductScope}
            onChange={(e) => setNewRoleProductScope(e.target.value)}
            disabled={!currentUserIsAdmin}
            className="rounded-lg border border-slate-200 px-3 py-2 text-sm disabled:bg-slate-100"
          />
          <input
            type="text"
            placeholder={tr("Region scope", "区域范围")}
            value={newRoleRegionScope}
            onChange={(e) => setNewRoleRegionScope(e.target.value)}
            disabled={!currentUserIsAdmin}
            className="rounded-lg border border-slate-200 px-3 py-2 text-sm disabled:bg-slate-100"
          />
          <input
            type="text"
            placeholder={tr("Data access", "数据权限")}
            value={newRoleDataAccess}
            onChange={(e) => setNewRoleDataAccess(e.target.value)}
            disabled={!currentUserIsAdmin}
            className="rounded-lg border border-slate-200 px-3 py-2 text-sm disabled:bg-slate-100"
          />
          <input
            type="text"
            placeholder={tr("Dashboard template", "驾驶舱模板")}
            value={newRoleDashboard}
            onChange={(e) => setNewRoleDashboard(e.target.value)}
            disabled={!currentUserIsAdmin}
            className="rounded-lg border border-slate-200 px-3 py-2 text-sm disabled:bg-slate-100"
          />
        </div>
        <div className="rounded-lg border border-slate-200 bg-slate-50/70 p-3">
          <div className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
            {tr("Role feature template", "角色功能模板")}
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {FEATURE_KEYS.map((featureKey) => (
              <label
                key={featureKey}
                className="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700"
              >
                <input
                  type="checkbox"
                  checked={newRoleFeatures[featureKey]}
                  onChange={(e) =>
                    setNewRoleFeatures((prev) => ({
                      ...prev,
                      [featureKey]: e.target.checked,
                    }))
                  }
                  disabled={!currentUserIsAdmin}
                />
                {featureLabels[featureKey]}
              </label>
            ))}
          </div>
        </div>
        <div className="flex flex-col sm:flex-row gap-2">
          <button
            type="button"
            onClick={addRole}
            disabled={
              !currentUserIsAdmin || !newRoleName.trim() || !newRoleDepartmentId
            }
            className="rounded-lg bg-slate-900 text-white px-3 py-2 text-sm disabled:bg-slate-200 disabled:text-slate-500"
          >
            {tr("Add Role", "新增角色")}
          </button>
          <select
            value={cloneRoleId}
            onChange={(e) => setCloneRoleId(e.target.value)}
            disabled={!currentUserIsAdmin || roles.length === 0}
            className="rounded-lg border border-slate-200 px-3 py-2 text-sm bg-white disabled:bg-slate-100"
          >
            <option value="">
              {tr("Select role to clone", "选择要克隆的角色")}
            </option>
            {roles.map((role) => (
              <option key={role.id} value={role.id}>
                {role.name}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={cloneRole}
            disabled={!currentUserIsAdmin || !cloneRoleId}
            className="rounded-lg border border-slate-300 bg-white text-slate-700 px-3 py-2 text-sm disabled:bg-slate-100 disabled:text-slate-400"
          >
            {tr("Clone Role", "克隆角色")}
          </button>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1080px] text-sm">
            <thead className="bg-slate-100/70 text-slate-600">
              <tr>
                <th className="text-left px-3 py-2">
                  {tr("Organization", "组织")}
                </th>
                <th className="text-left px-3 py-2">
                  {tr("Department", "部门")}
                </th>
                <th className="text-left px-3 py-2">{tr("Role", "角色")}</th>
                <th className="text-left px-3 py-2">
                  {tr("Products", "产品")}
                </th>
                <th className="text-left px-3 py-2">{tr("Region", "区域")}</th>
                <th className="text-left px-3 py-2">
                  {tr("Feature access", "功能权限")}
                </th>
                <th className="text-left px-3 py-2">{tr("Data", "数据")}</th>
                <th className="text-left px-3 py-2">
                  {tr("Dashboard", "驾驶舱")}
                </th>
                <th className="text-left px-3 py-2">{tr("Action", "操作")}</th>
              </tr>
            </thead>
            <tbody>
              {roles.map((role) => (
                <tr key={role.id} className="border-t border-slate-100">
                  <td className="px-3 py-2">
                    <select
                      value={role.organizationId}
                      onChange={(e) =>
                        updateRole(role.id, "organizationId", e.target.value)
                      }
                      disabled={!currentUserIsAdmin}
                      className="w-full rounded border border-slate-200 px-2 py-1.5 bg-white disabled:bg-slate-100"
                    >
                      {organizations.map((org) => (
                        <option key={org.id} value={org.id}>
                          {org.name}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="px-3 py-2">
                    <select
                      value={role.departmentId}
                      onChange={(e) =>
                        updateRole(role.id, "departmentId", e.target.value)
                      }
                      disabled={!currentUserIsAdmin}
                      className="w-full rounded border border-slate-200 px-2 py-1.5 bg-white disabled:bg-slate-100"
                    >
                      {departments
                        .filter(
                          (department) =>
                            department.organizationId === role.organizationId,
                        )
                        .map((department) => (
                          <option key={department.id} value={department.id}>
                            {department.name}
                          </option>
                        ))}
                    </select>
                  </td>
                  <td className="px-3 py-2">
                    <input
                      type="text"
                      value={role.name}
                      onChange={(e) =>
                        updateRole(role.id, "name", e.target.value)
                      }
                      disabled={!currentUserIsAdmin}
                      className="w-full rounded border border-slate-200 px-2 py-1.5 disabled:bg-slate-100"
                    />
                  </td>
                  <td className="px-3 py-2">
                    <input
                      type="text"
                      value={role.productScope}
                      onChange={(e) =>
                        updateRole(role.id, "productScope", e.target.value)
                      }
                      disabled={!currentUserIsAdmin}
                      className="w-full rounded border border-slate-200 px-2 py-1.5 disabled:bg-slate-100"
                    />
                  </td>
                  <td className="px-3 py-2">
                    <input
                      type="text"
                      value={role.regionScope}
                      onChange={(e) =>
                        updateRole(role.id, "regionScope", e.target.value)
                      }
                      disabled={!currentUserIsAdmin}
                      className="w-full rounded border border-slate-200 px-2 py-1.5 disabled:bg-slate-100"
                    />
                  </td>
                  <td className="px-3 py-2">
                    <div className="grid grid-cols-2 gap-1 min-w-[220px]">
                      {FEATURE_KEYS.map((featureKey) => (
                        <label
                          key={featureKey}
                          className="inline-flex items-center gap-1 text-xs text-slate-700"
                        >
                          <input
                            type="checkbox"
                            checked={role.featureAccess[featureKey]}
                            onChange={(e) =>
                              updateRoleFeature(
                                role.id,
                                featureKey,
                                e.target.checked,
                              )
                            }
                            disabled={!currentUserIsAdmin}
                          />
                          <span>{featureLabels[featureKey]}</span>
                        </label>
                      ))}
                    </div>
                  </td>
                  <td className="px-3 py-2">
                    <input
                      type="text"
                      value={role.dataAccess}
                      onChange={(e) =>
                        updateRole(role.id, "dataAccess", e.target.value)
                      }
                      disabled={!currentUserIsAdmin}
                      className="w-full rounded border border-slate-200 px-2 py-1.5 disabled:bg-slate-100"
                    />
                  </td>
                  <td className="px-3 py-2">
                    <input
                      type="text"
                      value={role.dashboardTemplate}
                      onChange={(e) =>
                        updateRole(role.id, "dashboardTemplate", e.target.value)
                      }
                      disabled={!currentUserIsAdmin}
                      className="w-full rounded border border-slate-200 px-2 py-1.5 disabled:bg-slate-100"
                    />
                  </td>
                  <td className="px-3 py-2">
                    <button
                      type="button"
                      onClick={() => deleteRole(role.id)}
                      disabled={!currentUserIsAdmin}
                      className="rounded-lg border border-rose-200 text-rose-600 px-2 py-1.5 text-xs disabled:bg-slate-100 disabled:text-slate-400"
                    >
                      {tr("Delete", "删除")}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="rounded-lg border border-slate-200 bg-white p-4 space-y-3">
        <h4 className="text-sm font-semibold text-slate-900">
          {tr(
            "Step 4: Create Users Under One Organization, One Department, One Role",
            "步骤4：在一个组织、一个部门和一个角色下创建用户",
          )}
        </h4>
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-[1.1fr_1fr_1fr_1fr_1fr_auto] gap-2 items-end">
          <input
            type="text"
            placeholder={tr("Username", "用户名")}
            value={newAccessUsername}
            onChange={(e) => setNewAccessUsername(e.target.value)}
            disabled={!currentUserIsAdmin}
            className="rounded-lg border border-slate-200 px-3 py-2 text-sm disabled:bg-slate-100"
          />
          <input
            type="text"
            placeholder={tr("Password", "密码")}
            value={newAccessPassword}
            onChange={(e) => setNewAccessPassword(e.target.value)}
            disabled={!currentUserIsAdmin}
            className="rounded-lg border border-slate-200 px-3 py-2 text-sm disabled:bg-slate-100"
          />
          <select
            value={newAccessOrganizationId}
            onChange={(e) => setNewAccessOrganizationId(e.target.value)}
            disabled={!currentUserIsAdmin}
            className="rounded-lg border border-slate-200 px-3 py-2 text-sm bg-white disabled:bg-slate-100"
          >
            {organizations.map((org) => (
              <option key={org.id} value={org.id}>
                {org.name}
              </option>
            ))}
          </select>
          <select
            value={newAccessDepartmentId}
            onChange={(e) => setNewAccessDepartmentId(e.target.value)}
            disabled={!currentUserIsAdmin}
            className="rounded-lg border border-slate-200 px-3 py-2 text-sm bg-white disabled:bg-slate-100"
          >
            <option value="">{tr("Select department", "选择部门")}</option>
            {userDepartments.map((department) => (
              <option key={department.id} value={department.id}>
                {department.name}
              </option>
            ))}
          </select>
          <select
            value={newAccessRoleId}
            onChange={(e) => {
              const nextRoleId = e.target.value;
              setNewAccessRoleId(nextRoleId);
              setNewAccessFeatures(
                cloneFeatureAccess(
                  roles.find((role) => role.id === nextRoleId)?.featureAccess ||
                  DEFAULT_FEATURE_ACCESS,
                ),
              );
            }}
            disabled={!currentUserIsAdmin}
            className="rounded-lg border border-slate-200 px-3 py-2 text-sm bg-white disabled:bg-slate-100"
          >
            <option value="">{tr("Select role", "选择角色")}</option>
            {userRoles.map((role) => (
              <option key={role.id} value={role.id}>
                {role.name}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={addAccessUser}
            disabled={
              !currentUserIsAdmin ||
              !newAccessUsername.trim() ||
              !newAccessPassword.trim() ||
              !newAccessOrganizationId ||
              !newAccessDepartmentId ||
              !newAccessRoleId
            }
            className="rounded-lg bg-slate-900 text-white px-3 py-2 text-sm disabled:bg-slate-200 disabled:text-slate-500"
          >
            {tr("Add User", "新增用户")}
          </button>
        </div>
        <div className="rounded-lg border border-slate-200 bg-slate-50/70 p-3">
          <div className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
            {tr("Access features for this user", "该用户的功能权限")}
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {FEATURE_KEYS.map((featureKey) => (
              <label
                key={featureKey}
                className="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700"
              >
                <input
                  type="checkbox"
                  checked={newAccessFeatures[featureKey]}
                  onChange={(e) =>
                    setNewAccessFeatures((prev) => ({
                      ...prev,
                      [featureKey]: e.target.checked,
                    }))
                  }
                  disabled={!currentUserIsAdmin}
                />
                {featureLabels[featureKey]}
              </label>
            ))}
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1200px] text-sm">
            <thead className="bg-slate-100/70 text-slate-600">
              <tr>
                <th className="text-left px-3 py-2">{tr("User", "用户")}</th>
                <th className="text-left px-3 py-2">
                  {tr("Password", "密码")}
                </th>
                <th className="text-left px-3 py-2">
                  {tr("Organization", "组织")}
                </th>
                <th className="text-left px-3 py-2">
                  {tr("Department", "部门")}
                </th>
                <th className="text-left px-3 py-2">{tr("Role", "角色")}</th>
                <th className="text-left px-3 py-2">
                  {tr("Access features", "功能权限")}
                </th>
                <th className="text-left px-3 py-2">
                  {tr("Override", "临时覆盖")}
                </th>
                <th className="text-left px-3 py-2">{tr("Status", "状态")}</th>
                <th className="text-left px-3 py-2">{tr("Action", "操作")}</th>
              </tr>
            </thead>
            <tbody>
              {accessUsers.map((row) => (
                <tr key={row.id} className="border-t border-slate-100">
                  <td className="px-3 py-2">
                    <input
                      type="text"
                      value={row.username}
                      onChange={(e) =>
                        updateAccessUser(row.id, "username", e.target.value)
                      }
                      disabled={!currentUserIsAdmin}
                      className="w-full rounded border border-slate-200 px-2 py-1.5 disabled:bg-slate-100"
                    />
                  </td>
                  <td className="px-3 py-2">
                    <input
                      type="text"
                      value={row.password}
                      onChange={(e) =>
                        updateAccessUser(row.id, "password", e.target.value)
                      }
                      disabled={!currentUserIsAdmin}
                      className="w-full rounded border border-slate-200 px-2 py-1.5 disabled:bg-slate-100"
                    />
                  </td>
                  <td className="px-3 py-2">
                    <select
                      value={row.organizationId}
                      onChange={(e) =>
                        updateAccessUser(
                          row.id,
                          "organizationId",
                          e.target.value,
                        )
                      }
                      disabled={!currentUserIsAdmin}
                      className="w-full rounded border border-slate-200 px-2 py-1.5 bg-white disabled:bg-slate-100"
                    >
                      {organizations.map((org) => (
                        <option key={org.id} value={org.id}>
                          {org.name}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="px-3 py-2">
                    <select
                      value={row.departmentId}
                      onChange={(e) =>
                        updateAccessUser(row.id, "departmentId", e.target.value)
                      }
                      disabled={!currentUserIsAdmin}
                      className="w-full rounded border border-slate-200 px-2 py-1.5 bg-white disabled:bg-slate-100"
                    >
                      <option value="">{tr("Unassigned", "未分配")}</option>
                      {departments
                        .filter(
                          (department) =>
                            department.organizationId === row.organizationId,
                        )
                        .map((department) => (
                          <option key={department.id} value={department.id}>
                            {department.name}
                          </option>
                        ))}
                    </select>
                  </td>
                  <td className="px-3 py-2">
                    <select
                      value={row.roleId}
                      onChange={(e) =>
                        updateAccessUser(row.id, "roleId", e.target.value)
                      }
                      disabled={!currentUserIsAdmin}
                      className="w-full rounded border border-slate-200 px-2 py-1.5 bg-white disabled:bg-slate-100"
                    >
                      <option value="">{tr("Unassigned", "未分配")}</option>
                      {roles
                        .filter(
                          (role) =>
                            role.organizationId === row.organizationId &&
                            role.departmentId === row.departmentId,
                        )
                        .map((role) => (
                          <option key={role.id} value={role.id}>
                            {role.name}
                          </option>
                        ))}
                    </select>
                  </td>
                  <td className="px-3 py-2">
                    <div className="grid grid-cols-2 gap-1 min-w-[220px]">
                      {FEATURE_KEYS.map((featureKey) => (
                        <label
                          key={featureKey}
                          className="inline-flex items-center gap-1 text-xs text-slate-700"
                        >
                          <input
                            type="checkbox"
                            checked={row.featureAccess[featureKey]}
                            onChange={(e) =>
                              updateAccessUserFeature(
                                row.id,
                                featureKey,
                                e.target.checked,
                              )
                            }
                            disabled={!currentUserIsAdmin}
                          />
                          <span>{featureLabels[featureKey]}</span>
                        </label>
                      ))}
                    </div>
                  </td>
                  <td className="px-3 py-2">
                    <input
                      type="text"
                      value={row.oneTimeOverride}
                      onChange={(e) =>
                        updateAccessUser(
                          row.id,
                          "oneTimeOverride",
                          e.target.value,
                        )
                      }
                      placeholder={tr("One-time exception", "一次性例外说明")}
                      disabled={!currentUserIsAdmin}
                      className="w-full rounded border border-slate-200 px-2 py-1.5 disabled:bg-slate-100"
                    />
                  </td>
                  <td className="px-3 py-2">
                    <button
                      type="button"
                      onClick={() =>
                        updateAccessUser(row.id, "isActive", !row.isActive)
                      }
                      disabled={!currentUserIsAdmin}
                      className={`rounded-lg px-2 py-1.5 text-xs border ${row.isActive ? "border-emerald-200 text-emerald-700" : "border-amber-200 text-amber-700"} disabled:bg-slate-100 disabled:text-slate-400`}
                    >
                      {row.isActive
                        ? tr("Active", "启用")
                        : tr("Suspended", "停用")}
                    </button>
                  </td>
                  <td className="px-3 py-2">
                    <button
                      type="button"
                      onClick={() => deleteAccessUser(row.id)}
                      disabled={!currentUserIsAdmin}
                      className="rounded-lg border border-rose-200 text-rose-600 px-2 py-1.5 text-xs disabled:bg-slate-100 disabled:text-slate-400"
                    >
                      {tr("Delete", "删除")}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="rounded-lg border border-slate-200 bg-white p-4 space-y-3">
        <h4 className="text-sm font-semibold text-slate-900">
          {tr(
            "Step 5: Preview the EDIA Experience by User",
            "步骤5：按用户预览 EDIA 体验",
          )}
        </h4>
        <div className="grid grid-cols-1 md:grid-cols-[1fr_2fr] gap-3">
          <div>
            <label className="block text-xs font-medium text-slate-500 mb-1">
              {tr("Preview user", "预览用户")}
            </label>
            <select
              value={previewUserId}
              onChange={(e) => setPreviewUserId(e.target.value)}
              className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm bg-white"
            >
              {accessUsers.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.username}
                </option>
              ))}
            </select>
          </div>
          <div className="rounded-lg border border-blue-100 bg-blue-50 p-3 text-sm text-blue-900">
            <p className="font-medium">
              {tr("Assigned organization", "所属组织")}:{" "}
              {selectedPreviewOrganization?.name || tr("Unassigned", "未分配")}
            </p>
            <p className="font-medium">
              {tr("Assigned department", "所属部门")}:{" "}
              {selectedPreviewDepartment?.name || tr("Unassigned", "未分配")}
            </p>
            <p className="font-medium">
              {tr("Assigned role", "所属角色")}:{" "}
              {selectedPreviewRole?.name || tr("Unassigned", "未分配")}
            </p>
            <p className="mt-2">{previewExperience}</p>
          </div>
        </div>
      </div>

      <div className="rounded-lg border border-slate-200 bg-white p-4 space-y-3">
        <h4 className="text-sm font-semibold text-slate-900">
          {tr(
            "Step 6: Admin Can Change Anything, Anytime",
            "步骤6：管理员可随时调整任意配置",
          )}
        </h4>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[920px] text-sm">
            <thead className="bg-slate-100/70 text-slate-600">
              <tr>
                <th className="text-left px-3 py-2">{tr("Action", "操作")}</th>
                <th className="text-left px-3 py-2">{tr("Example", "示例")}</th>
              </tr>
            </thead>
            <tbody>
              {ADMIN_CONTROL_ACTIONS.map((row) => (
                <tr key={row.action} className="border-t border-slate-100">
                  <td className="px-3 py-2 font-medium text-slate-900">
                    {row.action}
                  </td>
                  <td className="px-3 py-2 text-slate-700">{row.example}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-xs text-slate-500">
          {tr(
            "Nothing changes in EDIA without admin permission. Admin builds it, controls it, and can change it at any time.",
            "未经管理员授权，EDIA 不会发生任何配置变更。管理员可以搭建、控制并随时修改。",
          )}
        </p>
      </div>
    </div>
  );

  const newsTablePanel = (
    <div className="rounded-xl border border-slate-200 bg-white p-5 md:p-6 space-y-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-xl font-semibold text-slate-900">
            {tr("Admin News Table", "管理员新闻表")}
          </h2>
          <p className="text-sm text-slate-600">
            {tr(
              "View and filter intelligence events stored in the database. Kimi handles review automatically.",
              "查看和筛选数据库中的情报事件。Kimi 自动完成审核判断。",
            )}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <div className="text-xs text-slate-500">
            {tr("Last updated (Shanghai)", "最后更新时间（上海）")}: {formatDateTime(newsLastUpdatedAt)}
          </div>
          <button
            type="button"
            onClick={loadNewsModeration}
            disabled={!currentUserIsAdmin || isNewsLoading}
            className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 disabled:bg-slate-100 disabled:text-slate-400"
          >
            {isNewsLoading ? tr("Loading...", "加载中...") : tr("Refresh", "刷新")}
          </button>
        </div>
      </div>

      {newsError && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">
          {newsError}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-4 gap-2">
        <select
          value={newsReviewStatus}
          onChange={(e) => setNewsReviewStatus(e.target.value as EventReviewStatus)}
          className="rounded-lg border border-slate-200 px-3 py-2 text-sm bg-white"
        >
          <option value="all">{tr("All statuses", "全部状态")}</option>
          <option value="approved">{tr("Approved", "已批准")}</option>
          <option value="pending">{tr("Pending", "待审核")}</option>
          <option value="rejected">{tr("Rejected", "已拒绝")}</option>
        </select>
        <input
          type="number"
          min={1}
          value={newsHours}
          onChange={(e) => setNewsHours(Math.max(1, Number(e.target.value) || 1))}
          className="rounded-lg border border-slate-200 px-3 py-2 text-sm"
          placeholder={tr("Hours", "小时")}
        />
        <input
          type="number"
          min={0}
          max={5}
          value={newsMinUsefulness}
          onChange={(e) =>
            setNewsMinUsefulness(Math.max(0, Math.min(5, Number(e.target.value) || 0)))
          }
          className="rounded-lg border border-slate-200 px-3 py-2 text-sm"
          placeholder={tr("Min usefulness", "最低有效分")}
        />
        <input
          type="number"
          min={1}
          max={1000}
          value={newsLimit}
          onChange={(e) =>
            setNewsLimit(Math.max(1, Math.min(1000, Number(e.target.value) || 1)))
          }
          className="rounded-lg border border-slate-200 px-3 py-2 text-sm"
          placeholder={tr("Limit", "数量")}
        />
      </div>

      <div className="grid grid-cols-2 md:grid-cols-6 gap-2 text-xs">
        <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
          <div className="text-slate-500">{tr("Total", "总数")}</div>
          <div className="text-slate-900 font-semibold">{newsCount}</div>
        </div>
        <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
          <div className="text-slate-500">{tr("Approved", "已批准")}</div>
          <div className="text-slate-900 font-semibold">{newsSummary?.approved_count ?? 0}</div>
        </div>
        <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
          <div className="text-slate-500">{tr("Pending", "待审核")}</div>
          <div className="text-slate-900 font-semibold">{newsSummary?.pending_count ?? 0}</div>
        </div>
        <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
          <div className="text-slate-500">{tr("Rejected", "已拒绝")}</div>
          <div className="text-slate-900 font-semibold">{newsSummary?.rejected_count ?? 0}</div>
        </div>
        <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
          <div className="text-slate-500">{tr("Avg usefulness", "平均有效分")}</div>
          <div className="text-slate-900 font-semibold">{newsSummary?.usefulness_avg ?? 0}</div>
        </div>
        <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
          <div className="text-slate-500">{tr("Window (hours)", "窗口(小时)")}</div>
          <div className="text-slate-900 font-semibold">{newsSummary?.hours ?? newsHours}</div>
        </div>
      </div>

      <div className="overflow-x-auto rounded-lg border border-slate-200">
        <table className="w-full min-w-[980px] text-sm">
          <thead className="bg-slate-100/70 text-slate-600">
            <tr>
              <th className="text-left px-3 py-2">{tr("Detected (Shanghai)", "发现时间（上海）")}</th>
              <th className="text-left px-3 py-2">{tr("Type", "类型")}</th>
              <th className="text-left px-3 py-2">{tr("Headline", "标题")}</th>
              <th className="text-left px-3 py-2">{tr("Key information", "关键信息")}</th>
              <th className="text-left px-3 py-2">{tr("Source", "来源")}</th>
              <th className="text-left px-3 py-2">{tr("Impact", "影响")}</th>
            </tr>
          </thead>
          <tbody>
            {newsRows.map((row) => (
              <tr key={row.event_id} className="border-t border-slate-100 align-top">
                <td className="px-3 py-2 text-xs text-slate-600">{formatDateTime(row.detected_at)}</td>
                <td className="px-3 py-2 text-slate-700">{row.event_type}</td>
                <td className="px-3 py-2 text-slate-900 max-w-[420px] whitespace-pre-wrap break-words">
                  {row.headline || "-"}
                </td>
                <td className="px-3 py-2 text-xs text-slate-700 max-w-[280px] whitespace-pre-wrap break-words">
                  <div className="text-slate-800">
                    {(row.raw_quote || row.headline || "-").trim()}
                  </div>
                  {row.key_entities && row.key_entities.length > 0 ? (
                    <div className="mt-1 text-slate-500">{row.key_entities.join(", ")}</div>
                  ) : null}
                  {row.causal_chain_hint && !row.causal_chain_hint.startsWith("Fallback extraction:") ? (
                    <div className="mt-1 text-slate-500">{row.causal_chain_hint}</div>
                  ) : null}
                </td>
                <td className="px-3 py-2 text-xs">
                  {row.source_url ? (
                    <a
                      href={row.source_url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-blue-700 hover:underline break-all"
                    >
                      {row.source_url}
                    </a>
                  ) : (
                    <span className="text-slate-400">-</span>
                  )}
                </td>
                <td className="px-3 py-2 text-slate-700">{row.impact_magnitude ?? 0}</td>
              </tr>
            ))}
            {!isNewsLoading && newsRows.length === 0 && (
              <tr className="border-t border-slate-100">
                <td className="px-3 py-6 text-sm text-slate-500" colSpan={6}>
                  {tr("No events for the current filters.", "当前筛选条件下无事件。")}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );

  return (
    <div className="flex-1 overflow-y-auto bg-slate-50/60">
      <div className="max-w-7xl mx-auto p-4 md:p-6 space-y-5">
        <div className="rounded-xl border border-slate-200 bg-white p-5 md:p-6">
          <h1 className="text-2xl font-semibold text-slate-900">
            {text.title}
          </h1>
          <p className="text-sm text-slate-500 mt-1">{text.subtitle}</p>

          <div className="mt-5 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-2">
            {localizedTabs.map((item) => {
              const selected = activeTab === item.id;
              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => setTab(item.id)}
                  className={
                    "rounded-lg border px-3 py-3 text-left transition-colors " +
                    (selected
                      ? "border-blue-200 bg-blue-50 text-blue-800"
                      : "border-slate-200 bg-white text-slate-700 hover:bg-slate-50")
                  }
                >
                  <div className="flex items-center gap-2 text-sm font-medium">
                    {item.icon}
                    <span>{item.label}</span>
                  </div>
                  <p className="text-xs mt-1 text-slate-500">
                    {item.description}
                  </p>
                </button>
              );
            })}
          </div>
        </div>

        {activeTab === "users" && (
          <div className="rounded-xl border border-slate-200 bg-white">
            <div className="p-5 border-b border-slate-100 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h2 className="text-xl font-semibold text-slate-900">
                  {text.usersTitle}
                </h2>
                <p className="text-sm text-slate-500">{text.usersSubtitle}</p>
                <p className="text-xs mt-1 text-slate-500">
                  {currentUserIsAdmin ? text.adminHelp : text.readonlyHelp}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <button className="inline-flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 hover:bg-slate-50">
                  <Filter size={16} />
                </button>
                <button
                  type="button"
                  onClick={() => setTab("accessModel")}
                  className={`inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium ${currentUserIsAdmin
                    ? "bg-slate-900 text-white hover:bg-slate-800"
                    : "bg-slate-200 text-slate-500 cursor-not-allowed"
                    }`}
                  disabled={!currentUserIsAdmin}
                >
                  <UserPlus size={16} />
                  {text.adminAccess}
                </button>
              </div>
            </div>

            <div className="px-4 pt-4 text-xs font-semibold uppercase tracking-wide text-slate-500">
              Platform Login Accounts
            </div>

            <div className="p-4 border-b border-slate-100 grid grid-cols-1 lg:grid-cols-[1.4fr_1.4fr_auto_auto] gap-3 items-end">
              <div>
                <label className="block text-xs font-medium text-slate-500 mb-1">
                  {text.username}
                </label>
                <input
                  type="text"
                  placeholder={language === "en" ? "new username" : "新用户名"}
                  value={newUsername}
                  onChange={(e) => setNewUsername(e.target.value)}
                  disabled={!currentUserIsAdmin || isCreatingUser}
                  className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-100 disabled:bg-slate-100 disabled:text-slate-400"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-500 mb-1">
                  {text.initialPassword}
                </label>
                <input
                  type="password"
                  placeholder={
                    language === "en"
                      ? "set a one-time password"
                      : "设置一次性密码"
                  }
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  disabled={!currentUserIsAdmin || isCreatingUser}
                  className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-100 disabled:bg-slate-100 disabled:text-slate-400"
                />
                <p className="mt-1 text-xs text-slate-500">
                  {text.initialPasswordHint}
                </p>
              </div>
              <label className="inline-flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700">
                <input
                  type="checkbox"
                  checked={newUserIsAdmin}
                  onChange={(e) => setNewUserIsAdmin(e.target.checked)}
                  disabled={!currentUserIsAdmin || isCreatingUser}
                />
                {text.adminUser}
              </label>
              <button
                type="button"
                onClick={handleCreateUser}
                disabled={
                  !currentUserIsAdmin ||
                  isCreatingUser ||
                  !newUsername.trim() ||
                  !newPassword.trim()
                }
                className="inline-flex items-center justify-center gap-2 rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:bg-slate-200 disabled:text-slate-500"
              >
                <Plus size={16} />
                {isCreatingUser ? text.creating : text.createUser}
              </button>
            </div>

            <div className="p-4 border-b border-slate-100 grid grid-cols-1 lg:grid-cols-[1.2fr_1.4fr_auto] gap-3 items-end">
              <div>
                <label className="block text-xs font-medium text-slate-500 mb-1">
                  {text.userToReset}
                </label>
                <select
                  value={resetUserId}
                  onChange={(e) => setResetUserId(e.target.value)}
                  disabled={
                    !currentUserIsAdmin ||
                    isResettingUserPassword ||
                    users.length === 0
                  }
                  className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm bg-white disabled:bg-slate-100 disabled:text-slate-400"
                >
                  {users.length === 0 ? (
                    <option value="">{text.noUsersLoaded}</option>
                  ) : (
                    users.map((user) => (
                      <option
                        key={user.id}
                        value={user.id}
                        disabled={user.id === currentUser?.id}
                      >
                        {user.username}
                        {user.id === currentUser?.id
                          ? ` ${text.useSecurityTab}`
                          : ""}
                      </option>
                    ))
                  )}
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-500 mb-1">
                  {text.oneTimePassword}
                </label>
                <input
                  type="password"
                  placeholder={
                    language === "en"
                      ? "set a one-time password"
                      : "设置一次性密码"
                  }
                  value={resetPasswordValue}
                  onChange={(e) => setResetPasswordValue(e.target.value)}
                  disabled={!currentUserIsAdmin || isResettingUserPassword}
                  className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-100 disabled:bg-slate-100 disabled:text-slate-400"
                />
                <p className="mt-1 text-xs text-slate-500">
                  {text.oneTimeHint}
                </p>
              </div>
              <button
                type="button"
                onClick={handleResetUserPassword}
                disabled={
                  !currentUserIsAdmin ||
                  isResettingUserPassword ||
                  !resetUserId ||
                  !resetPasswordValue.trim()
                }
                className="inline-flex items-center justify-center gap-2 rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:bg-slate-100 disabled:text-slate-400"
              >
                <KeyRound size={16} />
                {isResettingUserPassword ? text.applying : text.setTempPassword}
              </button>
            </div>

            <div className="px-4 pt-3 text-xs text-slate-500">
              {text.bestPractice}
            </div>

            {usersError && (
              <div className="px-4 pt-4 text-sm text-rose-600">
                {usersError}
              </div>
            )}
            {resetPasswordError && (
              <div className="px-4 pt-2 text-sm text-rose-600">
                {resetPasswordError}
              </div>
            )}
            {resetPasswordStatus && (
              <div className="px-4 pt-2 text-sm text-emerald-700">
                {resetPasswordStatus}
              </div>
            )}

            <div className="p-4 border-b border-slate-100 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="relative w-full sm:max-w-sm">
                <Search
                  size={16}
                  className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
                />
                <input
                  type="text"
                  placeholder={text.searchUsers}
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  className="w-full rounded-lg border border-slate-200 pl-9 pr-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-100"
                />
              </div>
              <select
                value={roleFilter}
                onChange={(e) => setRoleFilter(e.target.value)}
                className="rounded-lg border border-slate-200 px-3 py-2 text-sm bg-white"
              >
                <option>{text.allRoles}</option>
                <option>{text.roleAdmin}</option>
                <option>{text.roleUser}</option>
              </select>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full min-w-[920px] text-sm">
                <thead className="bg-slate-100/70 text-slate-600">
                  <tr>
                    <th className="text-left px-6 py-3 font-medium">
                      {text.username}
                    </th>
                    <th className="text-left px-6 py-3 font-medium">
                      {language === "en" ? "Role" : "角色"}
                    </th>
                    <th className="text-left px-6 py-3 font-medium">
                      {text.created}
                    </th>
                    <th className="text-left px-6 py-3 font-medium">
                      {text.createdBy}
                    </th>
                    <th className="text-left px-6 py-3 font-medium">
                      {text.status}
                    </th>
                    <th className="text-left px-6 py-3 font-medium">
                      {text.nextLogin}
                    </th>
                    <th className="text-left px-6 py-3 font-medium">
                      {text.actions}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {isUsersLoading && (
                    <tr className="border-t border-slate-100">
                      <td
                        className="px-6 py-6 text-sm text-slate-500"
                        colSpan={7}
                      >
                        {text.loadingUsers}
                      </td>
                    </tr>
                  )}
                  {filteredUsers.map((row) => (
                    <tr key={row.id} className="border-t border-slate-100">
                      <td className="px-6 py-4">
                        <p className="font-medium text-slate-900">
                          {row.username}
                        </p>
                        <p className="text-xs text-slate-500">{text.loginId}</p>
                      </td>
                      <td className="px-6 py-4">
                        <span
                          className={`inline-flex rounded-md border px-2 py-1 text-xs font-medium ${roleBadge(row.is_admin)}`}
                        >
                          {row.is_admin ? text.roleAdmin : text.roleUser}
                        </span>
                      </td>
                      <td className="px-6 py-4">
                        <span className="text-slate-700">
                          {row.created_at || "-"}
                        </span>
                      </td>
                      <td className="px-6 py-4 text-slate-700">
                        {row.created_by || "system"}
                      </td>
                      <td className="px-6 py-4">
                        <span
                          className={`inline-flex items-center gap-1 text-xs ${row.is_active ? "text-emerald-700" : "text-amber-700"}`}
                        >
                          <CheckCircle2 size={14} />
                          {row.is_active ? text.active : text.disabled}
                        </span>
                      </td>
                      <td className="px-6 py-4 text-slate-700">
                        {row.must_change_password
                          ? text.mustChangePassword
                          : text.normal}
                      </td>
                      <td className="px-6 py-4">
                        <button
                          type="button"
                          onClick={() => handleToggleUserStatus(row)}
                          disabled={
                            !currentUserIsAdmin ||
                            currentUser?.id === row.id ||
                            isUpdatingUserStatus === row.id
                          }
                          className="inline-flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:bg-slate-100 disabled:text-slate-400"
                        >
                          {isUpdatingUserStatus === row.id
                            ? text.updating
                            : row.is_active
                              ? text.disable
                              : text.reactivate}
                        </button>
                      </td>
                    </tr>
                  ))}
                  {!isUsersLoading && filteredUsers.length === 0 && (
                    <tr className="border-t border-slate-100">
                      <td
                        className="px-6 py-6 text-sm text-slate-500"
                        colSpan={7}
                      >
                        {text.noUsers}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}
        {activeTab === "data" && (
          <div className="rounded-xl border border-slate-200 bg-white p-5 md:p-6 space-y-6">
            <div>
              <h2 className="text-xl font-semibold text-slate-900">
                {text.dataTitle}
              </h2>
              <p className="text-sm text-slate-500">{text.dataSubtitle}</p>
            </div>

            <div className="rounded-xl border border-slate-200 bg-white overflow-hidden">
              <div className="p-4 border-b border-slate-100 flex items-center justify-between gap-3">
                <h3 className="text-3xl font-semibold text-slate-900">
                  {text.conversation}
                </h3>
                <div className="flex items-center gap-2">
                  <div className="relative">
                    <button
                      type="button"
                      onClick={() => setShowConversationMenu((v) => !v)}
                      className="h-9 w-9 inline-flex items-center justify-center rounded-lg border border-slate-200 hover:bg-slate-50"
                    >
                      <MoreHorizontal size={16} />
                    </button>

                    {showConversationMenu && (
                      <div className="absolute right-0 mt-2 w-44 rounded-lg border border-slate-200 bg-white shadow-lg z-10 p-1 text-sm">
                        <button className="w-full text-left px-3 py-2 rounded hover:bg-slate-50 inline-flex items-center gap-2">
                          <Upload size={14} />
                          {text.import}
                        </button>
                        <button className="w-full text-left px-3 py-2 rounded hover:bg-slate-50 inline-flex items-center gap-2">
                          <FileDown size={14} />
                          {text.export}
                        </button>
                        <button className="w-full text-left px-3 py-2 rounded hover:bg-slate-50 inline-flex items-center gap-2">
                          <Braces size={14} />
                          {text.schema}
                        </button>
                        <button className="w-full text-left px-3 py-2 rounded hover:bg-slate-50 inline-flex items-center gap-2">
                          <History size={14} />
                          {text.recentlyDeleted} ({recentlyDeleted.length})
                        </button>
                        <button
                          className="w-full text-left px-3 py-2 rounded hover:bg-slate-50 inline-flex items-center gap-2"
                          onClick={() => {
                            setShowConversationMenu(false);
                            loadConversationHistory();
                          }}
                        >
                          <RefreshCw size={14} />
                          {text.refresh}
                        </button>
                        <button
                          className="w-full text-left px-3 py-2 rounded text-rose-600 hover:bg-rose-50 inline-flex items-center gap-2"
                          onClick={handleDeleteAllConversations}
                        >
                          <Trash2 size={14} />
                          {text.deleteAll}
                        </button>
                      </div>
                    )}
                  </div>

                  <button className="inline-flex items-center gap-2 rounded-lg bg-slate-900 text-white px-4 py-2 text-sm font-medium hover:bg-slate-800">
                    <Plus size={16} />
                    {text.add}
                  </button>
                </div>
              </div>

              <div className="p-4 border-b border-slate-100 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="relative w-full sm:max-w-sm">
                  <Search
                    size={16}
                    className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
                  />
                  <input
                    type="text"
                    placeholder={text.searchConversations}
                    value={conversationSearch}
                    onChange={(e) => setConversationSearch(e.target.value)}
                    className="w-full rounded-lg border border-slate-200 pl-9 pr-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-100"
                  />
                </div>

                <button className="inline-flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 hover:bg-slate-50 w-fit">
                  <Filter size={16} />
                  {text.filters}
                </button>
              </div>

              {conversationError && (
                <div className="px-4 pb-2 text-sm text-rose-700">
                  {conversationError}
                </div>
              )}

              {isConversationLoading && (
                <div className="px-4 pb-2 text-sm text-slate-500">
                  {text.loadingConversation}
                </div>
              )}

              <div className="overflow-x-auto">
                <table className="w-full min-w-[900px] text-sm">
                  <thead className="bg-slate-100/70 text-slate-600">
                    <tr>
                      <th className="text-left px-4 py-3 font-medium">
                        {text.conversationTitle}
                      </th>
                      <th className="text-left px-4 py-3 font-medium">
                        {text.conversationMessages}
                      </th>
                      <th className="text-left px-4 py-3 font-medium">
                        {text.conversationStatus}
                      </th>
                      <th className="text-left px-4 py-3 font-medium">
                        {text.conversationProductContext}
                      </th>
                      <th className="text-left px-4 py-3 font-medium">
                        Action
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredConversations.map((row) => (
                      <tr
                        key={row.id}
                        className="border-t border-slate-100 align-top"
                      >
                        <td className="px-4 py-3 text-slate-900 font-medium">
                          {row.title}
                        </td>
                        <td className="px-4 py-3 text-slate-600 max-w-[380px] whitespace-pre-wrap break-words">
                          {row.messages}
                        </td>
                        <td className="px-4 py-3">
                          <span
                            className={`inline-flex rounded-md border px-2 py-1 text-xs font-medium ${row.status === "active" ? "border-emerald-200 text-emerald-700" : "border-slate-200 text-slate-600"}`}
                          >
                            {row.status === "active" ? text.active : row.status}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-slate-600">
                          {row.product_context || "-"}
                        </td>
                        <td className="px-4 py-3">
                          <button
                            type="button"
                            onClick={() => handleDeleteConversation(row.id)}
                            className="inline-flex items-center gap-2 rounded-lg border border-rose-200 px-3 py-1.5 text-xs font-medium text-rose-600 hover:bg-rose-50"
                          >
                            <Trash2 size={14} />
                            {text.deleteConversation}
                          </button>
                        </td>
                      </tr>
                    ))}
                    {!isConversationLoading &&
                      filteredConversations.length === 0 && (
                        <tr className="border-t border-slate-100">
                          <td
                            className="px-4 py-6 text-sm text-slate-500"
                            colSpan={5}
                          >
                            {text.noConversation}
                          </td>
                        </tr>
                      )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}

        {activeTab === "security" && (
          <div className="rounded-xl border border-slate-200 bg-white p-5 md:p-6 space-y-6">
            <div>
              <h2 className="text-xl font-semibold text-slate-900">
                {text.securityTitle}
              </h2>
              <p className="text-sm text-slate-500">
                {text.changePasswordHint}{" "}
                {currentUser?.username || text.roleUser}
              </p>
            </div>

            <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-5 space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div>
                  <label className="block text-xs font-medium text-slate-500 mb-1">
                    {text.currentPassword}
                  </label>
                  <input
                    type="password"
                    value={currentPassword}
                    onChange={(e) => setCurrentPassword(e.target.value)}
                    className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-500 mb-1">
                    {text.newPassword}
                  </label>
                  <input
                    type="password"
                    value={nextPassword}
                    onChange={(e) => setNextPassword(e.target.value)}
                    className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-500 mb-1">
                    {text.confirmNewPassword}
                  </label>
                  <input
                    type="password"
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm"
                  />
                </div>
              </div>

              {passwordError && (
                <div className="text-sm text-rose-600">{passwordError}</div>
              )}
              {passwordStatus && (
                <div className="text-sm text-emerald-700">{passwordStatus}</div>
              )}

              <div className="flex justify-end">
                <button
                  type="button"
                  onClick={handleChangePassword}
                  disabled={isChangingPassword}
                  className="rounded-lg bg-slate-900 text-white px-4 py-2 text-sm font-medium hover:bg-slate-800 disabled:bg-slate-300 disabled:text-slate-500"
                >
                  {isChangingPassword ? text.updating : text.updatePassword}
                </button>
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <label className="rounded-lg border border-slate-200 p-4 flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-slate-800">
                    {text.enforceMfa}
                  </p>
                  <p className="text-xs text-slate-500">
                    {text.enforceMfaHint}
                  </p>
                </div>
                <input
                  type="checkbox"
                  checked={enforceMfaAdmins}
                  onChange={() => setEnforceMfaAdmins((v) => !v)}
                  className="h-4 w-4"
                />
              </label>

              <div className="rounded-lg border border-slate-200 p-4">
                <div className="flex items-center gap-2 text-sm font-medium text-slate-800">
                  <Clock3 size={16} /> {text.sessionTimeout}
                </div>
                <input
                  value={sessionTimeout}
                  onChange={(e) => setSessionTimeout(e.target.value)}
                  className="mt-3 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm"
                />
              </div>

              <div className="rounded-lg border border-slate-200 p-4">
                <div className="flex items-center gap-2 text-sm font-medium text-slate-800">
                  <AlertTriangle size={16} /> {text.lockoutThreshold}
                </div>
                <input
                  value={lockoutThreshold}
                  onChange={(e) => setLockoutThreshold(e.target.value)}
                  className="mt-3 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm"
                />
              </div>

              <div className="rounded-lg border border-slate-200 p-4">
                <div className="flex items-center gap-2 text-sm font-medium text-slate-800">
                  <KeyRound size={16} /> {text.keyRotation}
                </div>
                <input
                  value={apiKeyRotationDays}
                  onChange={(e) => setApiKeyRotationDays(e.target.value)}
                  className="mt-3 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm"
                />
              </div>
            </div>
          </div>
        )}

        {activeTab === "logs" && (
          <div className="rounded-xl border border-slate-200 bg-white p-5 md:p-6 space-y-4">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h2 className="text-xl font-semibold text-slate-900">
                  {text.logsTitle}
                </h2>
                <p className="text-sm text-slate-500">{text.logsSubtitle}</p>
              </div>
              <button className="inline-flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm hover:bg-slate-50 w-fit">
                <RefreshCw size={15} />
                {text.refresh}
              </button>
            </div>

            <div className="rounded-lg border border-slate-200">
              <div className="p-3 border-b border-slate-100 grid grid-cols-1 md:grid-cols-4 gap-2">
                <div className="relative md:col-span-2">
                  <Search
                    size={15}
                    className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
                  />
                  <input
                    type="text"
                    placeholder={text.filterByEmail}
                    className="w-full rounded-lg border border-slate-200 pl-8 pr-3 py-2 text-sm"
                  />
                </div>
                <select className="rounded-lg border border-slate-200 px-3 py-2 text-sm">
                  <option>{text.allTypes}</option>
                </select>
                <label className="rounded-lg border border-slate-200 px-3 py-2 text-sm flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={errorsOnly}
                    onChange={() => setErrorsOnly((v) => !v)}
                  />
                  {text.errorsOnly}
                </label>
              </div>

              <div className="max-h-[420px] overflow-auto">
                <table className="w-full min-w-[760px] text-sm">
                  <thead className="bg-slate-100/70 text-slate-600 sticky top-0">
                    <tr>
                      <th className="text-left px-4 py-2 font-medium">
                        {text.type}
                      </th>
                      <th className="text-left px-4 py-2 font-medium">
                        {text.user}
                      </th>
                      <th className="text-left px-4 py-2 font-medium">
                        {text.timestamp}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {LOG_ROWS.filter(
                      (row) => !errorsOnly || row.type.includes("error"),
                    ).map((row, i) => (
                      <tr
                        key={`${row.type}-${i}`}
                        className="border-t border-slate-100"
                      >
                        <td className="px-4 py-3 text-slate-800">{row.type}</td>
                        <td className="px-4 py-3 text-slate-700">{row.user}</td>
                        <td className="px-4 py-3 text-slate-600">
                          {row.timestamp}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}

        {activeTab === "newsTable" && currentUserIsAdmin && newsTablePanel}

        {activeTab === "products" && currentUserIsAdmin && (
          <ProductCatalogManager currentUserIsAdmin={currentUserIsAdmin} />
        )}

        {activeTab === "platform" && (
          <PlatformBuilder currentUserIsAdmin={currentUserIsAdmin} />
        )}

        {activeTab === "appearance" && (
          <div className="rounded-xl border border-slate-200 bg-white p-5 md:p-6 space-y-6">
            <div>
              <h2 className="text-xl font-semibold text-slate-900">
                {text.appearanceTitle}
              </h2>
              <p className="text-sm text-slate-500">{text.appearanceSubtitle}</p>
            </div>

            <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-5 space-y-4">
              <div className="text-sm text-slate-600">
                <span className="font-medium text-slate-800">
                  {text.appearanceCurrent}:
                </span>{" "}
                {theme === "light"
                  ? text.appearanceLight
                  : text.appearanceDark}
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <button
                  type="button"
                  onClick={() => setTheme("light")}
                  className={`rounded-xl border p-4 text-left transition-colors ${theme === "light" ? "border-blue-300 bg-blue-50" : "border-slate-200 bg-white hover:bg-slate-50"}`}
                >
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="flex items-center gap-2 text-base font-semibold text-slate-900">
                        <Sun size={18} />
                        {text.appearanceLight}
                      </div>
                      <div className="text-sm text-slate-500 mt-1">
                        {text.appearanceLightHint}
                      </div>
                    </div>
                    <div
                      className={`h-4 w-4 rounded-full border ${theme === "light" ? "border-blue-500 bg-blue-500" : "border-slate-300 bg-white"}`}
                    />
                  </div>
                </button>

                <button
                  type="button"
                  onClick={() => setTheme("dark")}
                  className={`rounded-xl border p-4 text-left transition-colors ${theme === "dark" ? "border-blue-300 bg-blue-50" : "border-slate-200 bg-white hover:bg-slate-50"}`}
                >
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="flex items-center gap-2 text-base font-semibold text-slate-900">
                        <Moon size={18} />
                        {text.appearanceDark}
                      </div>
                      <div className="text-sm text-slate-500 mt-1">
                        {text.appearanceDarkHint}
                      </div>
                    </div>
                    <div
                      className={`h-4 w-4 rounded-full border ${theme === "dark" ? "border-blue-500 bg-blue-500" : "border-slate-300 bg-white"}`}
                    />
                  </div>
                </button>
              </div>

              <p className="text-xs text-slate-500">{text.appearanceSaved}</p>
            </div>
          </div>
        )}

        {activeTab === "language" && (
          <div className="rounded-xl border border-slate-200 bg-white p-5 md:p-6 space-y-6">
            <div>
              <h2 className="text-xl font-semibold text-slate-900">
                {text.languageTitle}
              </h2>
              <p className="text-sm text-slate-500">{text.languageSubtitle}</p>
            </div>

            <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-5 space-y-4">
              <div className="text-sm text-slate-600">
                <span className="font-medium text-slate-800">
                  {text.languageCurrent}:
                </span>{" "}
                {language === "en"
                  ? text.languageEnglish
                  : text.languageChinese}
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <button
                  type="button"
                  onClick={() => setLanguage("en")}
                  className={`rounded-xl border p-4 text-left transition-colors ${language === "en" ? "border-blue-300 bg-blue-50" : "border-slate-200 bg-white hover:bg-slate-50"}`}
                >
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="text-base font-semibold text-slate-900">
                        {text.languageEnglish}
                      </div>
                      <div className="text-sm text-slate-500 mt-1">
                        {text.languageEnglishHint}
                      </div>
                    </div>
                    <div
                      className={`h-4 w-4 rounded-full border ${language === "en" ? "border-blue-500 bg-blue-500" : "border-slate-300 bg-white"}`}
                    />
                  </div>
                </button>

                <button
                  type="button"
                  onClick={() => setLanguage("zh")}
                  className={`rounded-xl border p-4 text-left transition-colors ${language === "zh" ? "border-blue-300 bg-blue-50" : "border-slate-200 bg-white hover:bg-slate-50"}`}
                >
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="text-base font-semibold text-slate-900">
                        {text.languageChinese}
                      </div>
                      <div className="text-sm text-slate-500 mt-1">
                        {text.languageChineseHint}
                      </div>
                    </div>
                    <div
                      className={`h-4 w-4 rounded-full border ${language === "zh" ? "border-blue-500 bg-blue-500" : "border-slate-300 bg-white"}`}
                    />
                  </div>
                </button>
              </div>

              <p className="text-xs text-slate-500">{text.languageSaved}</p>
            </div>
          </div>
        )}

        {activeTab === "accessModel" && accessModelBuilderPanel}
      </div>
    </div>
  );
};
