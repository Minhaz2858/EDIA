export interface ChatResponse {
  markdown: string;
  status: string;
  context: Record<string, any>;
  task_type: string;
  session_id: string;
  error_message?: string | null;
  llm_route?: "local" | "external" | "deterministic" | null;
  resolved_chat_mode?: "chat" | "analysis" | "report" | "deep_research" | null;
  response_mode?: "conversational" | "concise" | "structured" | "full_report" | null;
  mode_reason?: string | null;
  quality_flags?: Array<{
    code: string;
    severity: string;
    message: string;
    source: string;
  }>;
  alerts_prepended?: string[];
  enhancement_applied?: Record<string, string>;
}

export interface ChatStreamEvent {
  event: string;
  data: any;
}

export interface ChatVisualizationSeries {
  key: string;
  label: string;
  color: string;
  area_opacity?: number;
  chart_type?: "bar" | "line"; // for composed charts
  /** Which Y axis to use (multi-axis charts). Defaults to "left". */
  axis?: "left" | "right" | "farRight";
  /** Per-series unit label shown in tooltip/legend. */
  unit?: string;
}

export interface ChatVisualization {
  kind:
    | "line"
    | "bar"
    | "multi_line"
    | "forecast_range"
    | "waterfall"
    | "pie"
    | "scatter"
    | "composed"
    | "multi_axis_composed"
    | "table";
  title: string;
  subtitle?: string;
  product_id?: string;
  unit?: string;
  x_key: string;
  series: ChatVisualizationSeries[];
  data: Record<string, string | number>[];
  summary?: Record<string, any>;
}

export interface SessionReportPreview {
  title: string;
  markdown: string;
  session_id: string;
  generated_at: string;
}

export interface WeeklyReportChartSeries {
  key: string;
  label: string;
  color: string;
}

export interface WeeklyReportChartPoint {
  period: string;
  label: string;
  [key: string]: string | number | null | undefined;
}

export interface WeeklyReportSingleChart {
  chart_type: "yearly" | "monthly";
  title: string;
  unit: string;
  series: WeeklyReportChartSeries[];
  points: WeeklyReportChartPoint[];
  forecast_band?: {
    low?: number | null;
    high?: number | null;
    period?: string | null;
    confidence_label?: string | null;
    model_version?: string | null;
    source?: string | null;
    snapshot_id?: string | null;
    as_of_date?: string | null;
    generated_at?: string | null;
    policy_id?: string | null;
    horizon?: string | null;
  };
  report_period?: string;
  anchor_month?: string;
  highlight?: {
    start?: string;
    end?: string;
  };
}

export interface WeeklyReportProductCharts {
  product_slug: string;
  product_name_zh: string;
  product_name_en: string;
  yearly: WeeklyReportSingleChart;
  monthly: WeeklyReportSingleChart;
}

export interface WeeklyReportChartPayload {
  version?: number;
  report_id?: string;
  week_start?: string;
  week_end?: string;
  language?: string;
  products?: Record<string, WeeklyReportProductCharts>;
}

export interface WeeklyReportContext {
  report_id?: string;
  week_start?: string;
  week_end?: string;
  products?: string[];
  products_count?: number;
  generated_at?: string;
  model_version?: string;
  forecast_model_version?: string;
  forecast_snapshots?: Record<string, {
    forecast_snapshot_id?: string | null;
    forecast_model_version?: string | null;
    policy_id?: string | null;
    generated_at?: string | null;
    as_of_date?: string | null;
    horizon?: string | null;
    source?: string | null;
  }>;
  weekly_chart_payload?: WeeklyReportChartPayload;
}

export interface WeeklyReportSummary {
  id: string;
  title: string;
  status: string;
  created_at: string;
  context: WeeklyReportContext;
  markdown?: string;
  is_unread?: boolean;
}

export interface WeeklyReportResponse {
  id: string;
  report_id: string;
  title: string;
  markdown: string;
  preview_url: string;
  generated_at: string;
  duration_sec: number;
  products_count: number;
  week_start: string;
  week_end: string;
  context: WeeklyReportContext;
}

export interface GeneratedReportSummary {
  id: string;
  session_id: string;
  report_type: string;
  title: string;
  summary: string;
  status: "draft" | "approved";
  approved_at?: string | null;
  approved_by?: string | null;
  is_unread: boolean;
  read_at?: string | null;
  created_at: string;
}

export interface GeneratedReport extends GeneratedReportSummary {
  markdown: string;
  blocks?: ReportBlock[];
  context?: WeeklyReportContext;
  last_refreshed_at?: string | null;
}

export interface ReportBlock {
  id: string;
  order?: number;
  type: string;
  title?: string;
  definition?: Record<string, any>;
  rendered?: Record<string, any>;
  error?: string | null;
  last_refreshed_at?: string | null;
}

export interface RefreshReportBlocksResponse {
  report: GeneratedReport;
  refreshed_block_count: number;
  refreshed_at: string;
}

export interface ReportRecipient {
  id: string;
  username: string;
  role_label: string;
}

export interface ReportDelivery {
  id: string;
  report_id: string;
  sender_user_id: string;
  delivery_mode: "share" | "email";
  recipient_ids: string[];
  recipient_labels: string[];
  message?: string | null;
  status: string;
  provider?: string | null;
  delivered_at: string;
}

export interface DeliverReportRequest {
  delivery_mode: "share" | "email";
  recipient_ids: string[];
  message?: string;
}

export interface ReportTemplate {
  id: string;
  name: string;
  description?: string | null;
  time_period: string;
  audience?: string | null;
  schedule_hint?: string | null;
  sections: string[];
  template_title?: string | null;
  template_markdown?: string | null;
  template_blocks?: ReportBlock[];
  created_at: string;
  updated_at: string;
  updated_by?: string | null;
  auto_schedule_enabled?: boolean;
  auto_schedule_day?: string | null;
  auto_schedule_time?: string | null;
}

export interface SaveReportTemplateRequest {
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
}

export interface ReportCategory {
  id: string;
  org_id: string;
  parent_id?: string | null;
  name: string;
  icon?: string | null;
  position: number;
  permissions: Record<string, any>;
  created_at: string;
  updated_at: string;
  created_by?: string | null;
}

export interface SaveReportCategoryRequest {
  id?: string;
  parent_id?: string | null;
  name: string;
  icon?: string | null;
  position?: number;
  permissions?: Record<string, any>;
}

export interface CockpitDefinition {
  id: string;
  name: string;
  org_id: string;
  department_id?: string | null;
  layout: Record<string, any>;
  status: "draft" | "published" | "archived" | string;
  version: number;
  is_default: boolean;
  permissions: Record<string, any>;
  created_by?: string | null;
  created_at: string;
  updated_at: string;
  published_at?: string | null;
}

export interface SaveCockpitDefinitionRequest {
  id?: string;
  name: string;
  department_id?: string | null;
  layout?: Record<string, any>;
  status?: "draft" | "published" | "archived" | string;
  version?: number;
  is_default?: boolean;
  permissions?: Record<string, any>;
}

export interface AlertRule {
  id: string;
  name: string;
  org_id: string;
  created_by?: string | null;
  metric_expression: string;
  condition_type: string;
  condition_params: Record<string, any>;
  severity: string;
  category: string;
  notification_channels: Array<Record<string, any>>;
  default_owner_id?: string | null;
  enabled: boolean;
  evaluation_interval_min: number;
  created_at: string;
  updated_at: string;
}

export interface SaveAlertRuleRequest {
  id?: string;
  name: string;
  metric_expression: string;
  condition_type: string;
  condition_params?: Record<string, any>;
  severity: string;
  category: string;
  notification_channels?: Array<Record<string, any>>;
  default_owner_id?: string | null;
  enabled?: boolean;
  evaluation_interval_min?: number;
}

export interface DataSource {
  id: string;
  org_id: string;
  name: string;
  connector_type: string;
  connection_config: Record<string, any>;
  status: string;
  last_tested_at?: string | null;
  created_by?: string | null;
  created_at: string;
  updated_at: string;
}

export interface SaveDataSourceRequest {
  id?: string;
  name: string;
  connector_type: string;
  connection_config?: Record<string, any>;
  status?: string;
  last_tested_at?: string | null;
}

export interface Dataset {
  id: string;
  source_id: string;
  org_id: string;
  friendly_name: string;
  physical_reference: string;
  description?: string | null;
  tags: string[];
  schema_snapshot: Record<string, any>;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface SaveDatasetRequest {
  id?: string;
  source_id: string;
  friendly_name: string;
  physical_reference: string;
  description?: string | null;
  tags?: string[];
  schema_snapshot?: Record<string, any>;
  status?: string;
}

export interface DatasetColumnMapping {
  dataset_id: string;
  physical_column: string;
  ontology_concept: string;
  transform?: string | null;
}

export interface SaveDatasetColumnMappingsRequest {
  mappings: Array<{
    physical_column: string;
    ontology_concept: string;
    transform?: string | null;
  }>;
}

export interface DatasetPermission {
  dataset_id: string;
  role_id: string;
  can_query: boolean;
  can_export: boolean;
  can_chat: boolean;
}

export interface SaveDatasetPermissionsRequest {
  permissions: Array<{
    role_id: string;
    can_query?: boolean;
    can_export?: boolean;
    can_chat?: boolean;
  }>;
}

export interface PlatformProduct {
  id: string;
  org_id: string;
  name_en: string;
  name_cn: string;
  product_type: string;
  unit: string;
  description?: string | null;
  aliases: string[];
  data_table?: string | null;
  data_key?: string | null;
  display_in: string[];
  created_by?: string | null;
  created_at: string;
  updated_at: string;
}

export interface SavePlatformProductRequest {
  id?: string;
  name_en?: string;
  name_cn?: string;
  product_type: string;
  unit?: string;
  description?: string;
  aliases?: string[];
  data_table?: string;
  data_key?: string;
  display_in?: string[];
}

export interface ProductDataMappingPreview {
  data_table: string;
  data_key: string;
  available: boolean;
  row_count: number;
  latest_date?: string | null;
  latest_price?: number | null;
  price_type?: string | null;
  message: string;
  mini_chart_data: Array<{
    date: string;
    price: number;
    price_type?: string;
  }>;
}

export interface PlatformProductImportRow {
  row_number: number;
  product_id: string;
  name_en: string;
  name_cn: string;
  product_type: string;
  unit: string;
  description: string;
  aliases: string[];
  data_source: string;
  data_table?: string | null;
  data_key?: string | null;
  display_in: string[];
  valid: boolean;
  errors: string[];
  warnings: string[];
  will_update: boolean;
  data_available?: boolean | null;
  data_row_count?: number | null;
}

export interface PlatformProductImportPreview {
  total_rows: number;
  valid_rows: number;
  invalid_rows: number;
  rows: PlatformProductImportRow[];
}

export interface PlatformProductImportCommit extends PlatformProductImportPreview {
  imported_rows: number;
  imported_products: PlatformProduct[];
}

export type ReportExportFormat = "pdf" | "docx" | "pptx";

// ── Decision Log ─────────────────────────────────────────────────────────────

export type DecisionOutcome =
  | "pending"
  | "deal"
  | "no_deal"
  | "above_range"
  | "below_range";
export type DecisionStatus = "pending" | "executed" | "closed";

export interface DecisionLogEntry {
  id: string;
  product_id: string;
  product_name: string;
  created_at: string;
  user_id: string | null;
  session_id: string | null;
  ai_price_low: number;
  ai_price_high: number;
  ai_price_mid: number;
  price_unit?: string | null;
  ai_action: string;
  ai_confidence: number | null;
  ai_reasoning: string;
  market_snapshot: Record<string, unknown>;
  executed_price: number | null;
  executed_at: string | null;
  executed_by: string | null;
  outcome: DecisionOutcome;
  outcome_reason: string | null;
  outcome_competitor_price: number | null;
  outcome_note: string | null;
  outcome_at: string | null;
  price_error_cny: number | null;
  price_in_range: 0 | 1 | null;
  status: DecisionStatus;
  source: string;
}

export interface CreateDecisionLogRequest {
  product_id: string;
  product_name: string;
  ai_price_low: number;
  ai_price_high: number;
  price_unit?: string | null;
  ai_action: string;
  ai_confidence: number | null;
  ai_reasoning: string;
  session_id?: string | null;
  market_snapshot?: Record<string, unknown>;
}

export interface DecisionAccuracyProduct {
  product_id: string;
  product_name: string;
  total_decisions: number;
  hit_rate_pct: number | null;
  mean_abs_error_cny: number | null;
  deal_rate_pct: number;
}

export interface DecisionAccuracyStats {
  products: DecisionAccuracyProduct[];
  overall: {
    total_decisions: number;
    hit_rate_pct: number | null;
    mean_abs_error_cny: number | null;
    deal_rate_pct: number | null;
  };
}

// ── End Decision Log ──────────────────────────────────────────────────────────

export interface ReportSchedule {
  id: string;
  report_type: string;
  report_label: string;
  enabled: boolean;
  weekday: number;
  hour: number;
  minute: number;
  timezone: string;
  last_run_at?: string | null;
  next_run_at?: string | null;
}

export interface ReportExportDownload {
  blob: Blob;
  filename: string;
}

export interface ResearchBriefValidateRequest {
  decision: string;
  target_customer: string;
  geography: string;
  category_or_substitute_set: string;
  time_horizon: string;
  must_answer_questions: string[];
  evidence_bar: "low" | "medium" | "high";
}

export interface ResearchBriefValidateResponse {
  ok: boolean;
  issues: string[];
  brief: ResearchBriefValidateRequest;
}

export interface ResearchSearchRequest {
  query: string;
  engines: string[];
  max_results_per_engine: number;
  include_news: boolean;
  timelimit: "d" | "w" | "m";
}

export interface ResearchSearchResultItem {
  title: string;
  url: string;
  snippet: string;
  source: string;
  published_date: string;
}

export interface ResearchSearchResponse {
  query: string;
  engines: string[];
  count: number;
  results: ResearchSearchResultItem[];
}

export interface EvidenceScoreItem {
  title: string;
  url: string;
  source_type?: string;
  published_date?: string;
  corroboration_count?: number;
}

export interface EvidenceScoreResponse {
  count: number;
  score: {
    score: number;
    confidence_label: "LOW" | "MEDIUM" | "HIGH";
    source_diversity: number;
    domain_diversity: number;
    freshness_days_median: number | null;
    notes: string[];
  };
}

export interface UpdateGeneratedReportRequest {
  title: string;
  markdown: string;
  blocks?: ReportBlock[];
}

export interface UpdateGeneratedReportStatusRequest {
  status: "draft" | "approved";
}

export interface UserPrefs {
  language?: "zh" | "en";
  delta_basis?: "wow" | "mom";
  default_format?: "full_report" | "structured" | "concise";
  preferred_products?: string[];
  chat_mode?: "chat" | "analysis" | "report" | "deep_research";
}

export type ChatMode =
  | "auto"
  | "chat"
  | "analysis"
  | "report"
  | "deep_research";

export interface AuthUser {
  id: string;
  username: string;
  is_admin: boolean;
  is_active: boolean;
  must_change_password: boolean;
  created_at: string;
  created_by?: string | null;
  organization_id?: string | null;
  organization_name?: string | null;
  department_id?: string | null;
  department_name?: string | null;
  role_id?: string | null;
  role_name?: string | null;
  feature_access?: FeatureAccessConfig | null;
}

export interface LoginResponse {
  token: string;
  user: AuthUser;
  expires_at: string;
}

export interface ChangePasswordRequest {
  current_password: string;
  new_password: string;
}

export interface ResetUserPasswordResponse {
  status: string;
  message: string;
  user: AuthUser;
  reset_by: string;
}

export interface UpdateUserStatusResponse {
  status: string;
  message: string;
  user: AuthUser;
}

export interface OrganizationTemplateConfig {
  id: string;
  name: string;
  productChain: string;
  regionCoverage: string;
}

export interface DepartmentTemplateConfig {
  id: string;
  name: string;
  organizationId: string;
}

export interface FeatureAccessConfig {
  chatbot: boolean;
  dashboardUpstream: boolean;
  dashboardMidstream: boolean;
  dashboardDownstream: boolean;
  dashboardComparison: boolean;
  report: boolean;
}

export interface RoleTemplateConfig {
  id: string;
  name: string;
  organizationId: string;
  departmentId: string;
  productScope: string;
  regionScope: string;
  featureAccess: FeatureAccessConfig;
  dataAccess: string;
  dashboardTemplate: string;
}

export interface AccessUserTemplateConfig {
  id: string;
  username: string;
  password: string;
  organizationId: string;
  departmentId: string;
  roleId: string;
  featureAccess: FeatureAccessConfig;
  isActive: boolean;
  oneTimeOverride: string;
}

export interface AccessModelConfig {
  organizations: OrganizationTemplateConfig[];
  departments: DepartmentTemplateConfig[];
  roles: RoleTemplateConfig[];
  accessUsers: AccessUserTemplateConfig[];
  updated_at?: string | null;
  updated_by?: string | null;
}

export type EventReviewStatus = "all" | "approved" | "pending" | "rejected";

export interface EventModerationItem {
  event_id: string;
  detected_at: string;
  headline: string;
  event_type: string;
  key_entities: string[];
  causal_chain_hint: string;
  raw_quote: string;
  source_url: string;
  impact_magnitude: number;
  review_status: "approved" | "pending" | "rejected";
  usefulness_score: number;
  reviewed_by: string;
  reviewed_at: string;
  review_note: string;
}

export interface EventModerationQueueResponse {
  events: EventModerationItem[];
  count: number;
  filters: {
    hours: number;
    review_status: EventReviewStatus;
    min_usefulness_score: number;
  };
}

export interface EventReviewSummaryResponse {
  hours: number;
  total_events: number;
  approved_count: number;
  pending_count: number;
  rejected_count: number;
  usefulness_avg: number;
  usefulness_distribution: Record<string, number>;
}

export interface IntelligenceStatusResponse {
  initialized: boolean;
  llm_mode: string;
  background_workers_enabled: boolean;
  database_only_mode: boolean;
  news_agent_enabled: boolean;
  stale_after_hours: number;
  agent: {
    last_started_at?: string | null;
    last_completed_at?: string | null;
    last_success_at?: string | null;
    last_articles_seen?: number;
    last_events_extracted?: number;
    last_events_stored?: number;
    cycle_in_progress?: boolean;
  } | null;
}

export interface Session {
  id: string;
  user_id: string;
  title: string;
  created_at: string;
}

export interface RenameSessionResponse {
  status: string;
  session_id: string;
  title: string;
}

export interface DeleteSessionResponse {
  status: string;
  session_id: string;
}

export interface Message {
  id: string;
  session_id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: string;
  context?: Record<string, any> | null;
}

export interface MarketProduct {
  id: string;
  name_cn: string;
  name_en: string;
  unit: string;
}

export interface MarketPoint {
  date: string;
  price: number;
}

export interface AlertItem {
  id: string;
  type: "price_spike" | "price_drop";
  product: string;
  product_name: string;
  severity: "high" | "medium" | "low";
  change_7d_pct: number;
  price: number;
  message: string;
  timestamp: string;
  triggered_at: string;
  read_at: string | null;
  source_text?: string;
  source?: {
    system?: string;
    table?: string;
    channel?: string;
    product_id?: string;
    price_type?: string;
    as_of_date?: string | null;
    alert_detector?: string;
  };
}

export interface AlertsResponse {
  alerts: AlertItem[];
  unread_count: number;
  last_scan?: string | null;
}

export interface RecommendationCardTag {
  label: string;
  tone: "hot" | "role" | "alert" | string;
}

export interface RecommendationCardPayload {
  id: string;
  icon_key: string;
  badge: string;
  prompt: string;
  color: string;
  reason: string;
  score: number;
  tags: RecommendationCardTag[];
}

export interface RecommendationCardsResponse {
  cards: RecommendationCardPayload[];
  role: string;
  role_label: string;
}

export interface ComposerSuggestionPayload {
  id: string;
  title: string;
  prompt: string;
  reason: string;
  source: string;
  icon_key: string;
  color: string;
  score: number;
  tags: RecommendationCardTag[];
  session_id?: string | null;
}

export interface ComposerSuggestionsResponse {
  suggestions: ComposerSuggestionPayload[];
  role: string;
  role_label: string;
  counts: Record<string, number>;
}

export interface ChatAttachmentContext {
  id: string;
  filename: string;
  file_type: string;
  size_bytes: number;
  content: string;
  excerpt: string;
  summary: string;
}

export interface ChatAttachmentUploadResponse extends ChatAttachmentContext {
  status: string;
}

const explicitApiBaseUrlRaw = (
  import.meta.env.VITE_API_BASE_URL as string | undefined
)?.trim();

const configuredLocalBackendPort = (
  import.meta.env.VITE_LOCAL_BACKEND_PORT as string | undefined
)?.trim() || "3000";

function readEnvInt(
  rawValue: string | undefined,
  fallback: number,
  minimum = 1,
): number {
  const parsed = Number.parseInt(String(rawValue || "").trim(), 10);
  if (Number.isFinite(parsed) && parsed >= minimum) {
    return parsed;
  }
  return fallback;
}

const AUTH_LOGIN_TIMEOUT_MS = readEnvInt(
  import.meta.env.VITE_AUTH_LOGIN_TIMEOUT_MS as string | undefined,
  12000,
);

const BACKEND_HEALTH_TIMEOUT_MS = readEnvInt(
  import.meta.env.VITE_BACKEND_HEALTH_TIMEOUT_MS as string | undefined,
  5000,
);

const BACKEND_HEALTH_RETRIES = readEnvInt(
  import.meta.env.VITE_BACKEND_HEALTH_RETRIES as string | undefined,
  1,
  0,
);

const frontendOnlyPorts = new Set(["3001", "5001", "5002", "5173", "9009", "9090"]);

function deriveApiBaseUrl(): string {
  const origin = window.location.origin;

  try {
    const current = new URL(origin);
    if (
      frontendOnlyPorts.has(current.port) &&
      current.port !== configuredLocalBackendPort
    ) {
      // On some machines localhost resolves to IPv6 (::1) while the backend
      // is bound to 127.0.0.1, which causes "Failed to fetch" during login.
      const backendHost =
        current.hostname === "localhost" ? "127.0.0.1" : current.hostname;
      return `${current.protocol}//${backendHost}:${configuredLocalBackendPort}`;
    }
  } catch {
    // Fall back to same-origin below.
  }

  return origin;
}

function deriveDirectBackendBaseUrl(): string | null {
  try {
    const current = new URL(window.location.origin);
    const backendHost =
      current.hostname === "localhost" ? "127.0.0.1" : current.hostname;
    return `${current.protocol}//${backendHost}:${configuredLocalBackendPort}`;
  } catch {
    return null;
  }
}

/** When nginx serves static files only, retry API calls on the configured backend port. */
async function fetchWithDirectBackendFallback(
  path: string,
  options: RequestInit & { timeout: number },
): Promise<Response> {
  const primaryUrl = `${API_BASE_URL}${path}`;
  const directBackendBase = deriveDirectBackendBaseUrl();
  const canFallback =
    !!directBackendBase &&
    directBackendBase !== API_BASE_URL &&
    path.startsWith("/");

  try {
    const res = await fetchWithTimeout(primaryUrl, options);
    if (canFallback && res.status === 404 && path.startsWith("/api/")) {
      return fetchWithTimeout(`${directBackendBase}${path}`, options);
    }
    return res;
  } catch (error) {
    if (!canFallback || !isRetryableNetworkError(error)) {
      throw error;
    }
    return fetchWithTimeout(`${directBackendBase}${path}`, options);
  }
}

// Use the configured backend port for local frontend-only servers, but keep
// same-origin behavior for reverse-proxy deployments (for example nginx or
// Vercel serving frontend and API from one host).
const derivedApiBaseUrl = deriveApiBaseUrl();

function normalizeApiBaseUrl(raw: string | undefined): string | undefined {
  if (!raw) {
    return undefined;
  }
  const trimmed = raw.trim().replace(/\/+$/, "");
  // Guard against env values like "https://host/api" or "/api" to avoid
  // generating URLs such as "/api/api/auth/login".
  return trimmed.endsWith("/api") ? trimmed.slice(0, -4) || "/" : trimmed;
}

const explicitApiBaseUrl = normalizeApiBaseUrl(explicitApiBaseUrlRaw);

export const API_BASE_URL = explicitApiBaseUrl || derivedApiBaseUrl;

function withAuthHeaders(url: string, opts: RequestInit = {}): RequestInit {
  const headers = new Headers(opts.headers || undefined);
  const token = getAuthToken();
  if (token && url.startsWith(API_BASE_URL) && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  return { ...opts, headers };
}

async function extractErrorMessage(res: Response): Promise<string> {
  let detail = res.statusText || "Request failed";
  try {
    const payload = await res.clone().json();
    if (typeof payload?.detail === "string") {
      detail = payload.detail;
    }
  } catch {
    // no-op
  }
  return detail;
}

class NonRetryableHttpError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "NonRetryableHttpError";
    this.status = status;
  }
}

type BackendPingStatus = "online" | "offline" | "aborted";

type DiagnosticError = Error & {
  apiBaseUrl?: string;
  backendStatus?: BackendPingStatus;
};

function buildDiagnosticError(
  name: "BackendTimeoutError" | "BackendUnavailableError",
  backendStatus: BackendPingStatus,
): Error {
  const error = new Error();
  error.name = name;

  const diagnosticError = error as DiagnosticError;
  diagnosticError.apiBaseUrl = API_BASE_URL;
  diagnosticError.backendStatus = backendStatus;

  if (name === "BackendTimeoutError") {
    error.message = `Backend at ${API_BASE_URL} did not finish login within ${AUTH_LOGIN_TIMEOUT_MS}ms.`;
    return error;
  }

  error.message = `Backend at ${API_BASE_URL} is unreachable or not responding.`;
  return error;
}

function isAbortLikeError(error: unknown): boolean {
  return (
    (error instanceof DOMException && error.name === "AbortError") ||
    (error instanceof Error && error.name === "AbortError")
  );
}

function isRetryableNetworkError(error: unknown): boolean {
  if (isAbortLikeError(error)) {
    return false;
  }
  if (error instanceof TypeError) {
    return true;
  }
  if (error instanceof Error) {
    const msg = error.message.toLowerCase();
    return (
      msg.includes("network") ||
      msg.includes("failed to fetch") ||
      msg.includes("econnreset") ||
      msg.includes("econnrefused") ||
      msg.includes("etimedout")
    );
  }
  return false;
}

/**
 * Fetch wrapper with authentication headers.
 * Enforces timeout via AbortController.
 */
export async function fetchWithTimeout(
  url: string,
  opts: RequestInit & { timeout?: number } = {},
): Promise<Response> {
  const { timeout, signal, ...fetchOpts } = opts;

  // Preserve existing behavior when timeout is not set.
  if (!timeout || timeout <= 0) {
    return await fetch(url, {
      ...withAuthHeaders(url, { ...fetchOpts, signal }),
    });
  }

  const controller = new AbortController();
  const onParentAbort = () => controller.abort();

  if (signal) {
    if (signal.aborted) {
      controller.abort();
    } else {
      signal.addEventListener("abort", onParentAbort, { once: true });
    }
  }

  const timer = window.setTimeout(() => controller.abort(), timeout);
  try {
    return await fetch(url, {
      ...withAuthHeaders(url, { ...fetchOpts, signal: controller.signal }),
    });
  } finally {
    window.clearTimeout(timer);
    if (signal) {
      signal.removeEventListener("abort", onParentAbort);
    }
  }
}

/** Fetch with retry + exponential backoff (default: 1 retry). */
export async function fetchWithRetry(
  url: string,
  opts: RequestInit & {
    timeout?: number;
    retries?: number;
    backoff?: number;
  } = {},
): Promise<Response> {
  const { retries = 1, backoff = 400, ...rest } = opts;
  let lastError: unknown;
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      const res = await fetchWithTimeout(url, rest);
      if (res.ok) return res;
      // Treat 5xx as retryable
      if (res.status >= 500 && attempt < retries) {
        await new Promise((r) => setTimeout(r, backoff * Math.pow(2, attempt)));
        continue;
      }
      if (res.status === 401) {
        clearAuthToken();
      }
      throw new NonRetryableHttpError(
        await extractErrorMessage(res),
        res.status,
      );
    } catch (e) {
      lastError = e;
      const retryable =
        !(e instanceof NonRetryableHttpError) && isRetryableNetworkError(e);
      if (attempt < retries && retryable) {
        await new Promise((r) => setTimeout(r, backoff * Math.pow(2, attempt)));
        continue;
      }
      throw e;
    }
  }
  throw lastError;
}

export async function pingBackend(): Promise<"online" | "offline" | "aborted"> {
  try {
    const res = await fetchWithDirectBackendFallback("/health", {
      method: "GET",
      headers: buildHeaders(false),
      timeout: BACKEND_HEALTH_TIMEOUT_MS,
    });
    return res.ok ? "online" : "offline";
  } catch (error) {
    if (
      (error instanceof DOMException && error.name === "AbortError") ||
      (error instanceof Error &&
        (error.name === "AbortError" || /aborted/i.test(error.message)))
    ) {
      return "aborted";
    }
    return "offline";
  }
}

const AUTH_TOKEN_STORAGE_KEY = "edia_auth_token";

export function getAuthToken(): string | null {
  return window.localStorage.getItem(AUTH_TOKEN_STORAGE_KEY);
}

export function setAuthToken(token: string): void {
  window.localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, token);
}

export function clearAuthToken(): void {
  window.localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
}

function buildHeaders(includeContentType = true): HeadersInit {
  const headers: Record<string, string> = {};
  if (includeContentType) {
    headers["Content-Type"] = "application/json";
  }

  const token = getAuthToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  return headers;
}

async function parseJsonResponse<T>(res: Response): Promise<T> {
  if (res.ok) {
    return await res.json();
  }

  let detail = res.statusText || "Request failed";
  try {
    const payload = await res.json();
    if (typeof payload?.detail === "string") {
      detail = payload.detail;
    }
  } catch {
    // no-op
  }

  if (res.status === 401) {
    clearAuthToken();
  }

  throw new Error(detail);
}

function resolveDownloadFilename(res: Response, fallbackName: string): string {
  const header = res.headers.get("Content-Disposition") || "";
  const quotedMatch = header.match(/filename="([^"]+)"/i);
  if (quotedMatch?.[1]) {
    return quotedMatch[1];
  }
  const plainMatch = header.match(/filename=([^;]+)/i);
  if (plainMatch?.[1]) {
    return plainMatch[1].trim();
  }
  return fallbackName;
}

/**
 * Export a single chat message's content as PDF or DOCX and trigger a browser download.
 */
export async function exportChatMessage(
  content: string,
  title: string,
  format: "pdf" | "docx",
  visualizations: ChatVisualization[],
): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/api/chat/export-message`, {
    method: "POST",
    headers: buildHeaders(),
    body: JSON.stringify({ content, title, format, visualizations }),
  });
  if (!res.ok) {
    throw new Error(await extractErrorMessage(res));
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = resolveDownloadFilename(
    res,
    `${title.replace(/[^a-zA-Z0-9\s\-_]/g, "").trim() || "chat_export"}.${format}`,
  );
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export const api = {
  login: async (username: string, password: string): Promise<LoginResponse> => {
    try {
      const requestOptions: RequestInit & { timeout: number } = {
        method: "POST",
        headers: buildHeaders(),
        body: JSON.stringify({ username, password }),
        timeout: AUTH_LOGIN_TIMEOUT_MS,
      };

      const res = await fetchWithDirectBackendFallback(
        "/api/auth/login",
        requestOptions,
      );

      const payload = await parseJsonResponse<LoginResponse>(res);
      setAuthToken(payload.token);
      return payload;
    } catch (error) {
      if (isAbortLikeError(error)) {
        const backendStatus = await pingBackend();
        throw buildDiagnosticError(
          backendStatus === "online"
            ? "BackendTimeoutError"
            : "BackendUnavailableError",
          backendStatus,
        );
      }

      if (isRetryableNetworkError(error)) {
        const backendStatus = await pingBackend();
        throw buildDiagnosticError("BackendUnavailableError", backendStatus);
      }

      throw error;
    }
  },

  logout: async (): Promise<void> => {
    const token = getAuthToken();
    try {
      if (token) {
        await fetch(`${API_BASE_URL}/api/auth/logout`, {
          method: "POST",
          headers: buildHeaders(false),
        });
      }
    } finally {
      clearAuthToken();
    }
  },

  getCurrentUser: async (): Promise<AuthUser> => {
    const res = await fetchWithTimeout(`${API_BASE_URL}/api/auth/me`, {
      headers: buildHeaders(false),
      timeout: 8000,
    });
    return await parseJsonResponse<AuthUser>(res);
  },

  getUsers: async (): Promise<AuthUser[]> => {
    const res = await fetch(`${API_BASE_URL}/api/auth/users`, {
      headers: buildHeaders(false),
    });
    return await parseJsonResponse<AuthUser[]>(res);
  },

  createUser: async (
    username: string,
    password: string,
    isAdmin: boolean,
  ): Promise<AuthUser> => {
    const res = await fetch(`${API_BASE_URL}/api/auth/users`, {
      method: "POST",
      headers: buildHeaders(),
      body: JSON.stringify({ username, password, is_admin: isAdmin }),
    });
    return await parseJsonResponse<AuthUser>(res);
  },

  changePassword: async (
    payload: ChangePasswordRequest,
  ): Promise<LoginResponse> => {
    const res = await fetch(`${API_BASE_URL}/api/auth/change-password`, {
      method: "POST",
      headers: buildHeaders(),
      body: JSON.stringify(payload),
    });
    const response = await parseJsonResponse<LoginResponse>(res);
    setAuthToken(response.token);
    return response;
  },

  resetUserPassword: async (
    userId: string,
    newPassword: string,
  ): Promise<ResetUserPasswordResponse> => {
    const res = await fetch(
      `${API_BASE_URL}/api/auth/users/${userId}/reset-password`,
      {
        method: "POST",
        headers: buildHeaders(),
        body: JSON.stringify({ new_password: newPassword }),
      },
    );
    return await parseJsonResponse<ResetUserPasswordResponse>(res);
  },

  updateUserStatus: async (
    userId: string,
    isActive: boolean,
  ): Promise<UpdateUserStatusResponse> => {
    const res = await fetch(`${API_BASE_URL}/api/auth/users/${userId}/status`, {
      method: "POST",
      headers: buildHeaders(),
      body: JSON.stringify({ is_active: isActive }),
    });
    return await parseJsonResponse<UpdateUserStatusResponse>(res);
  },

  getAccessModel: async (): Promise<AccessModelConfig> => {
    const res = await fetch(`${API_BASE_URL}/api/admin/access-model`, {
      headers: buildHeaders(false),
    });
    return await parseJsonResponse<AccessModelConfig>(res);
  },

  updateAccessModel: async (
    payload: AccessModelConfig,
  ): Promise<AccessModelConfig> => {
    const res = await fetch(`${API_BASE_URL}/api/admin/access-model`, {
      method: "PUT",
      headers: buildHeaders(),
      body: JSON.stringify(payload),
    });
    return await parseJsonResponse<AccessModelConfig>(res);
  },

  getEventModerationQueue: async (params?: {
    hours?: number;
    review_status?: EventReviewStatus;
    min_usefulness_score?: number;
    limit?: number;
  }): Promise<EventModerationQueueResponse> => {
    const qs = new URLSearchParams();
    if (typeof params?.hours === "number") {
      qs.set("hours", String(params.hours));
    }
    if (params?.review_status) {
      qs.set("review_status", params.review_status);
    }
    if (typeof params?.min_usefulness_score === "number") {
      qs.set("min_usefulness_score", String(params.min_usefulness_score));
    }
    if (typeof params?.limit === "number") {
      qs.set("limit", String(params.limit));
    }

    const query = qs.toString();
    const res = await fetch(
      `${API_BASE_URL}/api/events/moderation/queue${query ? `?${query}` : ""}`,
      {
        headers: buildHeaders(false),
      },
    );
    return await parseJsonResponse<EventModerationQueueResponse>(res);
  },

  reviewEvent: async (
    eventId: string,
    payload: {
      review_status: "approved" | "pending" | "rejected";
      usefulness_score: number;
      review_note?: string;
    },
  ): Promise<{
    status: string;
    event_id: string;
    review_status: "approved" | "pending" | "rejected";
    usefulness_score: number;
  }> => {
    const res = await fetch(`${API_BASE_URL}/api/events/${eventId}/review`, {
      method: "POST",
      headers: buildHeaders(),
      body: JSON.stringify(payload),
    });
    return await parseJsonResponse(res);
  },

  removeEvent: async (
    eventId: string,
    reviewNote = "",
  ): Promise<{ status: string; event_id: string; review_status: string }> => {
    const res = await fetch(`${API_BASE_URL}/api/events/${eventId}/remove`, {
      method: "POST",
      headers: buildHeaders(),
      body: JSON.stringify({ review_note: reviewNote }),
    });
    return await parseJsonResponse(res);
  },

  getEventReviewSummary: async (
    hours = 168,
  ): Promise<EventReviewSummaryResponse> => {
    const res = await fetch(
      `${API_BASE_URL}/api/events/review/summary?hours=${hours}`,
      {
        headers: buildHeaders(false),
      },
    );
    return await parseJsonResponse<EventReviewSummaryResponse>(res);
  },

  getIntelligenceStatus: async (): Promise<IntelligenceStatusResponse> => {
    const res = await fetch(`${API_BASE_URL}/api/intelligence/status`, {
      headers: buildHeaders(false),
    });
    return await parseJsonResponse<IntelligenceStatusResponse>(res);
  },

  getRecommendationCards: async (
    language: "zh" | "en",
  ): Promise<RecommendationCardsResponse> => {
    const res = await fetch(
      `${API_BASE_URL}/api/recommendations/cards?language=${language}`,
      {
        headers: buildHeaders(false),
      },
    );
    return await parseJsonResponse<RecommendationCardsResponse>(res);
  },

  getComposerSuggestions: async (
    language: "zh" | "en",
    limit = 8,
  ): Promise<ComposerSuggestionsResponse> => {
    const res = await fetch(
      `${API_BASE_URL}/api/composer/suggestions?language=${language}&limit=${limit}`,
      {
        headers: buildHeaders(false),
      },
    );
    return await parseJsonResponse<ComposerSuggestionsResponse>(res);
  },

  uploadChatContextFile: async (
    file: File,
  ): Promise<ChatAttachmentUploadResponse> => {
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch(`${API_BASE_URL}/api/chat/context-file`, {
      method: "POST",
      headers: buildHeaders(false),
      body: formData,
    });
    return await parseJsonResponse<ChatAttachmentUploadResponse>(res);
  },

  sendMessage: async (
    message: string,
    sessionId?: string,
    prefs?: UserPrefs,
    attachmentContexts?: ChatAttachmentContext[],
    chatMode?: ChatMode,
  ): Promise<ChatResponse> => {
    try {
      const response = await fetch(`${API_BASE_URL}/api/chat`, {
        method: "POST",
        headers: buildHeaders(),
        body: JSON.stringify({
          message,
          session_id: sessionId,
          prefs: prefs || {},
          attachment_contexts: attachmentContexts || [],
          chat_mode: chatMode && chatMode !== "auto" ? chatMode : undefined,
        }),
      });
      return await parseJsonResponse<ChatResponse>(response);
    } catch (error) {
      console.error("API Error:", error);
      throw error;
    }
  },

  generateSessionReportPreview: async (
    sessionId: string,
  ): Promise<SessionReportPreview> => {
    const res = await fetch(
      `${API_BASE_URL}/api/sessions/${sessionId}/report-preview`,
      {
        method: "POST",
        headers: buildHeaders(false),
      },
    );
    return await parseJsonResponse<SessionReportPreview>(res);
  },

  createGeneratedReport: async (
    sessionId: string,
  ): Promise<GeneratedReport> => {
    const res = await fetch(
      `${API_BASE_URL}/api/sessions/${sessionId}/reports`,
      {
        method: "POST",
        headers: buildHeaders(false),
      },
    );
    return await parseJsonResponse<GeneratedReport>(res);
  },

  addSessionToWeeklyReport: async (
    sessionId: string,
  ): Promise<GeneratedReport> => {
    const res = await fetch(
      `${API_BASE_URL}/api/sessions/${sessionId}/weekly-report`,
      {
        method: "POST",
        headers: buildHeaders(false),
      },
    );
    return await parseJsonResponse<GeneratedReport>(res);
  },

  listGeneratedReports: async (): Promise<GeneratedReportSummary[]> => {
    const res = await fetch(`${API_BASE_URL}/api/reports`, {
      headers: buildHeaders(false),
    });
    return await parseJsonResponse<GeneratedReportSummary[]>(res);
  },

  getGeneratedReport: async (reportId: string): Promise<GeneratedReport> => {
    const res = await fetch(`${API_BASE_URL}/api/reports/${reportId}`, {
      headers: buildHeaders(false),
    });
    return await parseJsonResponse<GeneratedReport>(res);
  },

  updateGeneratedReport: async (
    reportId: string,
    payload: UpdateGeneratedReportRequest,
  ): Promise<GeneratedReport> => {
    const res = await fetch(`${API_BASE_URL}/api/reports/${reportId}`, {
      method: "PATCH",
      headers: buildHeaders(),
      body: JSON.stringify(payload),
    });
    return await parseJsonResponse<GeneratedReport>(res);
  },

  deleteGeneratedReport: async (reportId: string): Promise<void> => {
    const res = await fetch(`${API_BASE_URL}/api/reports/${reportId}`, {
      method: "DELETE",
      headers: buildHeaders(false),
    });
    if (!res.ok) {
      throw new Error(`Failed to delete report: ${res.statusText}`);
    }
  },

  refreshGeneratedReport: async (
    reportId: string,
  ): Promise<RefreshReportBlocksResponse> => {
    const res = await fetch(`${API_BASE_URL}/api/reports/${reportId}/refresh`, {
      method: "POST",
      headers: buildHeaders(false),
    });
    return await parseJsonResponse<RefreshReportBlocksResponse>(res);
  },

  refreshGeneratedReportBlock: async (
    reportId: string,
    blockId: string,
  ): Promise<RefreshReportBlocksResponse> => {
    const res = await fetch(
      `${API_BASE_URL}/api/reports/${reportId}/refresh/${blockId}`,
      {
        method: "POST",
        headers: buildHeaders(false),
      },
    );
    return await parseJsonResponse<RefreshReportBlocksResponse>(res);
  },

  updateGeneratedReportStatus: async (
    reportId: string,
    payload: UpdateGeneratedReportStatusRequest,
  ): Promise<GeneratedReport> => {
    const res = await fetch(`${API_BASE_URL}/api/reports/${reportId}/status`, {
      method: "POST",
      headers: buildHeaders(),
      body: JSON.stringify(payload),
    });
    return await parseJsonResponse<GeneratedReport>(res);
  },

  exportGeneratedReport: async (
    reportId: string,
    format: ReportExportFormat,
  ): Promise<ReportExportDownload> => {
    const res = await fetch(
      `${API_BASE_URL}/api/reports/${reportId}/export/${format}`,
      {
        headers: buildHeaders(false),
      },
    );
    if (!res.ok) {
      throw new Error(await extractErrorMessage(res));
    }
    return {
      blob: await res.blob(),
      filename: resolveDownloadFilename(res, `report.${format}`),
    };
  },

  listReportSchedules: async (): Promise<ReportSchedule[]> => {
    const res = await fetch(`${API_BASE_URL}/api/report-schedules`, {
      headers: buildHeaders(false),
    });
    return await parseJsonResponse<ReportSchedule[]>(res);
  },

  getReportSchedule: async (reportType: string): Promise<ReportSchedule> => {
    const res = await fetch(
      `${API_BASE_URL}/api/report-schedules/${reportType}`,
      {
        headers: buildHeaders(false),
      },
    );
    return await parseJsonResponse<ReportSchedule>(res);
  },

  updateReportSchedule: async (
    reportType: string,
    payload: { enabled: boolean; hour: number; minute: number },
  ): Promise<ReportSchedule> => {
    const res = await fetch(
      `${API_BASE_URL}/api/report-schedules/${reportType}`,
      {
        method: "PUT",
        headers: buildHeaders(),
        body: JSON.stringify(payload),
      },
    );
    return await parseJsonResponse<ReportSchedule>(res);
  },

  runReportNow: async (reportType: string): Promise<GeneratedReport> => {
    const res = await fetch(
      `${API_BASE_URL}/api/report-schedules/${reportType}/run-now`,
      {
        method: "POST",
        headers: buildHeaders(false),
      },
    );
    return await parseJsonResponse<GeneratedReport>(res);
  },

  listReportRecipients: async (): Promise<ReportRecipient[]> => {
    const res = await fetch(`${API_BASE_URL}/api/report-recipients`, {
      headers: buildHeaders(false),
    });
    return await parseJsonResponse<ReportRecipient[]>(res);
  },

  listReportDeliveries: async (reportId: string): Promise<ReportDelivery[]> => {
    const res = await fetch(
      `${API_BASE_URL}/api/reports/${reportId}/deliveries`,
      {
        headers: buildHeaders(false),
      },
    );
    return await parseJsonResponse<ReportDelivery[]>(res);
  },

  deliverReport: async (
    reportId: string,
    payload: DeliverReportRequest,
  ): Promise<ReportDelivery> => {
    const res = await fetch(`${API_BASE_URL}/api/reports/${reportId}/deliver`, {
      method: "POST",
      headers: buildHeaders(),
      body: JSON.stringify(payload),
    });
    return await parseJsonResponse<ReportDelivery>(res);
  },

  listReportTemplates: async (): Promise<ReportTemplate[]> => {
    const res = await fetch(`${API_BASE_URL}/api/report-templates`, {
      headers: buildHeaders(false),
    });
    return await parseJsonResponse<ReportTemplate[]>(res);
  },

  saveReportTemplate: async (
    payload: SaveReportTemplateRequest,
  ): Promise<ReportTemplate> => {
    const res = await fetch(`${API_BASE_URL}/api/report-templates`, {
      method: "POST",
      headers: buildHeaders(),
      body: JSON.stringify(payload),
    });
    return await parseJsonResponse<ReportTemplate>(res);
  },

  deleteReportTemplate: async (
    templateId: string,
  ): Promise<{ status: string; template_id: string }> => {
    const res = await fetch(
      `${API_BASE_URL}/api/report-templates/${templateId}`,
      {
        method: "DELETE",
        headers: buildHeaders(false),
      },
    );
    return await parseJsonResponse<{ status: string; template_id: string }>(
      res,
    );
  },

  hideGalleryTemplate: async (
    templateId: string,
  ): Promise<{ status: string; template_id: string; hidden: boolean }> => {
    const res = await fetch(
      `${API_BASE_URL}/api/user/hidden-gallery-templates/${templateId}`,
      {
        method: "POST",
        headers: buildHeaders(false),
      },
    );
    return await parseJsonResponse<{ status: string; template_id: string; hidden: boolean }>(
      res,
    );
  },

  unhideGalleryTemplate: async (
    templateId: string,
  ): Promise<{ status: string; template_id: string; hidden: boolean }> => {
    const res = await fetch(
      `${API_BASE_URL}/api/user/hidden-gallery-templates/${templateId}`,
      {
        method: "DELETE",
        headers: buildHeaders(false),
      },
    );
    return await parseJsonResponse<{ status: string; template_id: string; hidden: boolean }>(
      res,
    );
  },

  listHiddenGalleryTemplates: async (): Promise<{ hidden_template_ids: string[] }> => {
    const res = await fetch(
      `${API_BASE_URL}/api/user/hidden-gallery-templates`,
      {
        method: "GET",
        headers: buildHeaders(false),
      },
    );
    return await parseJsonResponse<{ hidden_template_ids: string[] }>(res);
  },

  pinReportTemplate: async (
    templateId: string,
  ): Promise<{ status: string; template_id: string; pinned: boolean }> => {
    const res = await fetch(
      `${API_BASE_URL}/api/user/pinned-templates/${templateId}`,
      {
        method: "POST",
        headers: buildHeaders(false),
      },
    );
    return await parseJsonResponse<{ status: string; template_id: string; pinned: boolean }>(
      res,
    );
  },

  unpinReportTemplate: async (
    templateId: string,
  ): Promise<{ status: string; template_id: string; pinned: boolean }> => {
    const res = await fetch(
      `${API_BASE_URL}/api/user/pinned-templates/${templateId}`,
      {
        method: "DELETE",
        headers: buildHeaders(false),
      },
    );
    return await parseJsonResponse<{ status: string; template_id: string; pinned: boolean }>(
      res,
    );
  },

  uploadReportImage: async (
    file: File,
  ): Promise<{ id: string; url: string; filename: string; mime_type: string; file_size: number }> => {
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch(
      `${API_BASE_URL}/api/report-authoring/upload-image`,
      {
        method: "POST",
        headers: buildHeaders(false),
        body: formData,
      },
    );
    return await parseJsonResponse<{ id: string; url: string; filename: string; mime_type: string; file_size: number }>(
      res,
    );
  },

  getReportAuthoringTemplates: async (): Promise<{ templates: any[] }> => {
    const res = await fetch(
      `${API_BASE_URL}/api/report-authoring/templates`,
      {
        method: "GET",
        headers: buildHeaders(false),
      },
    );
    return await parseJsonResponse<{ templates: any[] }>(res);
  },

  generateReportFromTemplate: async (
    templateId: string,
  ): Promise<GeneratedReport> => {
    const res = await fetch(
      `${API_BASE_URL}/api/report-templates/${templateId}/generate`,
      {
        method: "POST",
        headers: buildHeaders(false),
      },
    );
    return await parseJsonResponse<GeneratedReport>(res);
  },

  getReportTemplateSchedule: async (
    templateId: string,
  ): Promise<ReportSchedule> => {
    const res = await fetch(
      `${API_BASE_URL}/api/report-templates/${templateId}/schedule`,
      {
        headers: buildHeaders(false),
      },
    );
    return await parseJsonResponse<ReportSchedule>(res);
  },

  updateReportTemplateSchedule: async (
    templateId: string,
    payload: { enabled: boolean; hour: number; minute: number },
  ): Promise<ReportSchedule> => {
    const res = await fetch(
      `${API_BASE_URL}/api/report-templates/${templateId}/schedule`,
      {
        method: "PUT",
        headers: buildHeaders(),
        body: JSON.stringify(payload),
      },
    );
    return await parseJsonResponse<ReportSchedule>(res);
  },

  runReportTemplateNow: async (templateId: string): Promise<GeneratedReport> => {
    const res = await fetch(
      `${API_BASE_URL}/api/report-templates/${templateId}/schedule/run-now`,
      {
        method: "POST",
        headers: buildHeaders(false),
      },
    );
    return await parseJsonResponse<GeneratedReport>(res);
  },

  listPlatformReportCategories: async (): Promise<ReportCategory[]> => {
    const res = await fetch(`${API_BASE_URL}/api/platform/report-categories`, {
      headers: buildHeaders(false),
    });
    return await parseJsonResponse<ReportCategory[]>(res);
  },

  savePlatformReportCategory: async (
    payload: SaveReportCategoryRequest,
  ): Promise<ReportCategory> => {
    const res = await fetch(`${API_BASE_URL}/api/platform/report-categories`, {
      method: "POST",
      headers: buildHeaders(),
      body: JSON.stringify(payload),
    });
    return await parseJsonResponse<ReportCategory>(res);
  },

  deletePlatformReportCategory: async (
    categoryId: string,
  ): Promise<{ status: string; category_id: string }> => {
    const res = await fetch(
      `${API_BASE_URL}/api/platform/report-categories/${categoryId}`,
      {
        method: "DELETE",
        headers: buildHeaders(false),
      },
    );
    return await parseJsonResponse<{ status: string; category_id: string }>(
      res,
    );
  },

  listPlatformCockpits: async (): Promise<CockpitDefinition[]> => {
    const res = await fetch(`${API_BASE_URL}/api/platform/cockpits`, {
      headers: buildHeaders(false),
    });
    return await parseJsonResponse<CockpitDefinition[]>(res);
  },

  savePlatformCockpit: async (
    payload: SaveCockpitDefinitionRequest,
  ): Promise<CockpitDefinition> => {
    const res = await fetch(`${API_BASE_URL}/api/platform/cockpits`, {
      method: "POST",
      headers: buildHeaders(),
      body: JSON.stringify(payload),
    });
    return await parseJsonResponse<CockpitDefinition>(res);
  },

  deletePlatformCockpit: async (
    cockpitId: string,
  ): Promise<{ status: string; cockpit_id: string }> => {
    const res = await fetch(`${API_BASE_URL}/api/platform/cockpits/${cockpitId}`, {
      method: "DELETE",
      headers: buildHeaders(false),
    });
    return await parseJsonResponse<{ status: string; cockpit_id: string }>(res);
  },

  listPlatformAlertRules: async (): Promise<AlertRule[]> => {
    const res = await fetch(`${API_BASE_URL}/api/platform/alert-rules`, {
      headers: buildHeaders(false),
    });
    return await parseJsonResponse<AlertRule[]>(res);
  },

  savePlatformAlertRule: async (
    payload: SaveAlertRuleRequest,
  ): Promise<AlertRule> => {
    const res = await fetch(`${API_BASE_URL}/api/platform/alert-rules`, {
      method: "POST",
      headers: buildHeaders(),
      body: JSON.stringify(payload),
    });
    return await parseJsonResponse<AlertRule>(res);
  },

  deletePlatformAlertRule: async (
    ruleId: string,
  ): Promise<{ status: string; rule_id: string }> => {
    const res = await fetch(`${API_BASE_URL}/api/platform/alert-rules/${ruleId}`, {
      method: "DELETE",
      headers: buildHeaders(false),
    });
    return await parseJsonResponse<{ status: string; rule_id: string }>(res);
  },

  listPlatformDataSources: async (): Promise<DataSource[]> => {
    const res = await fetch(`${API_BASE_URL}/api/platform/data-sources`, {
      headers: buildHeaders(false),
    });
    return await parseJsonResponse<DataSource[]>(res);
  },

  listPlatformProducts: async (): Promise<PlatformProduct[]> => {
    const res = await fetch(`${API_BASE_URL}/api/platform/products`, {
      headers: buildHeaders(false),
    });
    return await parseJsonResponse<PlatformProduct[]>(res);
  },

  listAvailablePlatformProducts: async (params?: {
    displayIn?: string;
    requireData?: boolean;
  }): Promise<PlatformProduct[]> => {
    const searchParams = new URLSearchParams();
    if (params?.displayIn) searchParams.set("display_in", params.displayIn);
    if (params?.requireData) searchParams.set("require_data", "true");
    const query = searchParams.toString();
    const res = await fetch(
      `${API_BASE_URL}/api/platform/products/available${query ? `?${query}` : ""}`,
      { headers: buildHeaders(false) },
    );
    return await parseJsonResponse<PlatformProduct[]>(res);
  },

  downloadPlatformProductImportTemplate: async (): Promise<Blob> => {
    const res = await fetch(`${API_BASE_URL}/api/platform/products/import-template`, {
      headers: buildHeaders(false),
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(text || `HTTP ${res.status}`);
    }
    return await res.blob();
  },

  previewPlatformProductImport: async (
    file: File,
  ): Promise<PlatformProductImportPreview> => {
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch(`${API_BASE_URL}/api/platform/products/import-preview`, {
      method: "POST",
      headers: buildHeaders(false),
      body: formData,
    });
    return await parseJsonResponse<PlatformProductImportPreview>(res);
  },

  importPlatformProducts: async (
    file: File,
  ): Promise<PlatformProductImportCommit> => {
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch(`${API_BASE_URL}/api/platform/products/import`, {
      method: "POST",
      headers: buildHeaders(false),
      body: formData,
    });
    return await parseJsonResponse<PlatformProductImportCommit>(res);
  },

  savePlatformProduct: async (
    payload: SavePlatformProductRequest,
  ): Promise<PlatformProduct> => {
    const res = await fetch(`${API_BASE_URL}/api/platform/products`, {
      method: "POST",
      headers: buildHeaders(),
      body: JSON.stringify(payload),
    });
    return await parseJsonResponse<PlatformProduct>(res);
  },

  previewPlatformProductDataMapping: async (
    payload: Pick<SavePlatformProductRequest, "data_table" | "data_key">,
  ): Promise<ProductDataMappingPreview> => {
    const res = await fetch(`${API_BASE_URL}/api/platform/products/preview-data-mapping`, {
      method: "POST",
      headers: buildHeaders(),
      body: JSON.stringify(payload),
    });
    return await parseJsonResponse<ProductDataMappingPreview>(res);
  },

  updatePlatformProduct: async (
    productId: string,
    payload: SavePlatformProductRequest,
  ): Promise<PlatformProduct> => {
    const res = await fetch(
      `${API_BASE_URL}/api/platform/products/${encodeURIComponent(productId)}`,
      {
        method: "PUT",
        headers: buildHeaders(),
        body: JSON.stringify(payload),
      },
    );
    return await parseJsonResponse<PlatformProduct>(res);
  },

  deletePlatformProduct: async (
    productId: string,
  ): Promise<void> => {
    const res = await fetch(
      `${API_BASE_URL}/api/platform/products/${encodeURIComponent(productId)}`,
      {
        method: "DELETE",
        headers: buildHeaders(false),
      },
    );
    if (!res.ok && res.status !== 204) {
      const text = await res.text();
      throw new Error(text || `HTTP ${res.status}`);
    }
  },

  inspectTable: async (
    tableName: string,
  ): Promise<{ table: string; columns: { name: string; type: string }[] }> => {
    const res = await fetch(
      `${API_BASE_URL}/api/platform/tables/${encodeURIComponent(tableName)}/inspect`,
      { headers: buildHeaders(false) },
    );
    if (!res.ok) {
      const errText = await res.text();
      throw new Error(errText || `HTTP ${res.status}`);
    }
    return await parseJsonResponse<{ table: string; columns: { name: string; type: string }[] }>(res);
  },

  getProductInsight: async (productId: string): Promise<{
    product_id: string;
    insight: {
      headline: string;
      key_drivers: string[];
      price_context: string;
      related_products: string[];
      watch_points: string[];
      data_suggestion: string;
    };
    error?: string;
  }> => {
    const res = await fetch(
      `${API_BASE_URL}/api/platform/products/${encodeURIComponent(productId)}/insight`,
      { method: "POST", headers: buildHeaders(false) },
    );
    return await parseJsonResponse(res);
  },

  savePlatformDataSource: async (
    payload: SaveDataSourceRequest,
  ): Promise<DataSource> => {
    const res = await fetch(`${API_BASE_URL}/api/platform/data-sources`, {
      method: "POST",
      headers: buildHeaders(),
      body: JSON.stringify(payload),
    });
    return await parseJsonResponse<DataSource>(res);
  },

  deletePlatformDataSource: async (
    sourceId: string,
  ): Promise<void> => {
    const res = await fetch(
      `${API_BASE_URL}/api/platform/data-sources/${sourceId}`,
      {
        method: "DELETE",
        headers: buildHeaders(false),
      },
    );
    if (!res.ok && res.status !== 204) {
      const text = await res.text();
      throw new Error(text || `HTTP ${res.status}`);
    }
  },

  listPlatformDatasets: async (sourceId?: string): Promise<Dataset[]> => {
    const query = sourceId
      ? `?source_id=${encodeURIComponent(sourceId)}`
      : "";
    const res = await fetch(`${API_BASE_URL}/api/platform/datasets${query}`, {
      headers: buildHeaders(false),
    });
    return await parseJsonResponse<Dataset[]>(res);
  },

  savePlatformDataset: async (payload: SaveDatasetRequest): Promise<Dataset> => {
    const res = await fetch(`${API_BASE_URL}/api/platform/datasets`, {
      method: "POST",
      headers: buildHeaders(),
      body: JSON.stringify(payload),
    });
    return await parseJsonResponse<Dataset>(res);
  },

  deletePlatformDataset: async (
    datasetId: string,
  ): Promise<void> => {
    const res = await fetch(`${API_BASE_URL}/api/platform/datasets/${datasetId}`, {
      method: "DELETE",
      headers: buildHeaders(false),
    });
    if (!res.ok && res.status !== 204) {
      const text = await res.text();
      throw new Error(text || `HTTP ${res.status}`);
    }
  },

  listDatasetMappings: async (
    datasetId: string,
  ): Promise<DatasetColumnMapping[]> => {
    const res = await fetch(
      `${API_BASE_URL}/api/platform/datasets/${datasetId}/mappings`,
      {
        headers: buildHeaders(false),
      },
    );
    return await parseJsonResponse<DatasetColumnMapping[]>(res);
  },

  replaceDatasetMappings: async (
    datasetId: string,
    payload: SaveDatasetColumnMappingsRequest,
  ): Promise<DatasetColumnMapping[]> => {
    const res = await fetch(
      `${API_BASE_URL}/api/platform/datasets/${datasetId}/mappings`,
      {
        method: "PUT",
        headers: buildHeaders(),
        body: JSON.stringify(payload),
      },
    );
    return await parseJsonResponse<DatasetColumnMapping[]>(res);
  },

  listDatasetPermissions: async (
    datasetId: string,
  ): Promise<DatasetPermission[]> => {
    const res = await fetch(
      `${API_BASE_URL}/api/platform/datasets/${datasetId}/permissions`,
      {
        headers: buildHeaders(false),
      },
    );
    return await parseJsonResponse<DatasetPermission[]>(res);
  },

  replaceDatasetPermissions: async (
    datasetId: string,
    payload: SaveDatasetPermissionsRequest,
  ): Promise<DatasetPermission[]> => {
    const res = await fetch(
      `${API_BASE_URL}/api/platform/datasets/${datasetId}/permissions`,
      {
        method: "PUT",
        headers: buildHeaders(),
        body: JSON.stringify(payload),
      },
    );
    return await parseJsonResponse<DatasetPermission[]>(res);
  },

  sendMessageStream: async (
    message: string,
    sessionId: string | undefined,
    prefs: UserPrefs | undefined,
    attachmentContexts: ChatAttachmentContext[] | undefined,
    onDelta: (delta: string) => void,
    onEvent?: (event: ChatStreamEvent) => void,
    chatMode?: ChatMode,
    signal?: AbortSignal,
  ): Promise<ChatResponse> => {
    try {
      const response = await fetch(`${API_BASE_URL}/api/chat/stream`, {
        method: "POST",
        headers: {
          ...buildHeaders(),
          Accept: "text/event-stream",
          "Cache-Control": "no-cache",
        },
        body: JSON.stringify({
          message,
          session_id: sessionId,
          prefs: prefs || {},
          attachment_contexts: attachmentContexts || [],
          chat_mode: chatMode && chatMode !== "auto" ? chatMode : undefined,
        }),
        signal,
      });

      if (!response.ok) {
        throw new Error(await extractErrorMessage(response));
      }
      if (!response.body) {
        throw new Error("Stream failed: empty response body");
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";

      const parseEvent = (raw: string): { event: string; data: any } | null => {
        const lines = raw.split(/\r?\n/).filter(Boolean);
        let event = "message";
        let dataLine = "";
        for (const line of lines) {
          if (line.startsWith("event:")) {
            event = line.slice("event:".length).trim();
          } else if (line.startsWith("data:")) {
            dataLine += line.slice("data:".length).trim();
          }
        }
        if (!dataLine) return null;
        try {
          return { event, data: JSON.parse(dataLine) };
        } catch {
          return null;
        }
      };

      while (true) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });

        // SSE frames may be separated by LF or CRLF blank lines.
        const parts = buffer.split(/\r?\n\r?\n/);
        buffer = parts.pop() || "";

        for (const part of parts) {
          const ev = parseEvent(part);
          if (!ev) continue;
          if (onEvent) onEvent(ev);
          if (ev.event === "delta") {
            const delta = String(ev.data?.delta || "");
            if (delta) onDelta(delta);
          } else if (ev.event === "done") {
            return ev.data as ChatResponse;
          } else if (ev.event === "error") {
            throw new Error(String(ev.data?.message || "Stream error"));
          }
        }

        if (done) {
          const trailing = parseEvent(buffer.trim());
          if (trailing && onEvent) onEvent(trailing);
          if (trailing?.event === "done") {
            return trailing.data as ChatResponse;
          }
          if (trailing?.event === "error") {
            throw new Error(String(trailing.data?.message || "Stream error"));
          }
          break;
        }
      }

      throw new Error("Stream ended without completion");
    } catch (error) {
      throw error;
    }
  },

  getSessions: async (): Promise<Session[]> => {
    const res = await fetch(`${API_BASE_URL}/api/sessions`, {
      headers: buildHeaders(false),
    });
    return await parseJsonResponse<Session[]>(res);
  },

  getSessionHistory: async (sessionId: string): Promise<Message[]> => {
    const res = await fetch(`${API_BASE_URL}/api/sessions/${sessionId}`, {
      headers: buildHeaders(false),
    });
    return await parseJsonResponse<Message[]>(res);
  },

  renameSession: async (
    sessionId: string,
    title: string,
  ): Promise<RenameSessionResponse> => {
    const res = await fetch(`${API_BASE_URL}/api/sessions/${sessionId}`, {
      method: "PATCH",
      headers: buildHeaders(),
      body: JSON.stringify({ title }),
    });
    return await parseJsonResponse<RenameSessionResponse>(res);
  },

  deleteSession: async (sessionId: string): Promise<DeleteSessionResponse> => {
    const res = await fetch(`${API_BASE_URL}/api/sessions/${sessionId}`, {
      method: "DELETE",
      headers: buildHeaders(false),
    });
    return await parseJsonResponse<DeleteSessionResponse>(res);
  },

  // --- Market data (real prices from external MySQL) ---

  getMarketProducts: async (): Promise<MarketProduct[]> => {
    const res = await fetch(`${API_BASE_URL}/api/v2/market/products`);
    if (!res.ok) throw new Error(res.statusText);
    return await res.json();
  },

  getMarketHistory: async (productId: string): Promise<MarketPoint[]> => {
    const res = await fetch(
      `${API_BASE_URL}/api/v2/market/history/${productId}`,
    );
    if (!res.ok) throw new Error(res.statusText);
    return await res.json();
  },

  getAlerts: async (): Promise<AlertsResponse> => {
    const res = await fetch(`${API_BASE_URL}/api/alerts`, {
      headers: buildHeaders(false),
    });
    return await parseJsonResponse<AlertsResponse>(res);
  },

  // ── Decision Log ─────────────────────────────────────────────────────────

  createDecisionLog: async (
    data: CreateDecisionLogRequest,
  ): Promise<DecisionLogEntry> => {
    const res = await fetch(`${API_BASE_URL}/api/decision-log`, {
      method: "POST",
      headers: buildHeaders(),
      body: JSON.stringify(data),
    });
    return await parseJsonResponse<DecisionLogEntry>(res);
  },

  listDecisionLog: async (params?: {
    product_id?: string;
    status?: string;
    days?: number;
  }): Promise<DecisionLogEntry[]> => {
    const qs = new URLSearchParams();
    if (params?.product_id) qs.set("product_id", params.product_id);
    if (params?.status) qs.set("status", params.status);
    if (params?.days) qs.set("days", String(params.days));
    const res = await fetch(
      `${API_BASE_URL}/api/decision-log${qs.toString() ? `?${qs}` : ""}`,
      { headers: buildHeaders(false) },
    );
    return await parseJsonResponse<DecisionLogEntry[]>(res);
  },

  markDecisionExecuted: async (
    id: string,
    executedPrice: number,
    executedBy?: string,
  ): Promise<DecisionLogEntry> => {
    const res = await fetch(`${API_BASE_URL}/api/decision-log/${id}/execute`, {
      method: "PATCH",
      headers: buildHeaders(),
      body: JSON.stringify({
        executed_price: executedPrice,
        executed_by: executedBy,
      }),
    });
    return await parseJsonResponse<DecisionLogEntry>(res);
  },

  markDecisionOutcome: async (
    id: string,
    outcome: DecisionOutcome,
    extras?: {
      outcome_reason?: string;
      outcome_competitor_price?: number;
      outcome_note?: string;
    },
  ): Promise<DecisionLogEntry> => {
    const res = await fetch(`${API_BASE_URL}/api/decision-log/${id}/outcome`, {
      method: "PATCH",
      headers: buildHeaders(),
      body: JSON.stringify({ outcome, ...extras }),
    });
    return await parseJsonResponse<DecisionLogEntry>(res);
  },

  getDecisionAccuracy: async (
    productId?: string,
  ): Promise<DecisionAccuracyStats> => {
    const qs = productId ? `?product_id=${productId}` : "";
    const res = await fetch(`${API_BASE_URL}/api/decision-log/accuracy${qs}`, {
      headers: buildHeaders(false),
    });
    return await parseJsonResponse<DecisionAccuracyStats>(res);
  },

  deleteDecisionEntry: async (id: string): Promise<void> => {
    const res = await fetch(`${API_BASE_URL}/api/decision-log/${id}`, {
      method: "DELETE",
      headers: buildHeaders(),
    });
    if (!res.ok) {
      const msg = await res.text().catch(() => res.statusText);
      throw new Error(msg || `HTTP ${res.status}`);
    }
  },

  updateDecisionEntry: async (
    id: string,
    patch: {
      ai_price_low?: number;
      ai_price_high?: number;
      price_unit?: string | null;
      ai_action?: string;
      ai_reasoning?: string;
      outcome_note?: string;
      executed_price?: number;
    },
  ): Promise<DecisionLogEntry> => {
    const res = await fetch(`${API_BASE_URL}/api/decision-log/${id}`, {
      method: "PATCH",
      headers: buildHeaders(),
      body: JSON.stringify(patch),
    });
    return await parseJsonResponse<DecisionLogEntry>(res);
  },

  validateResearchBrief: async (
    payload: ResearchBriefValidateRequest,
  ): Promise<ResearchBriefValidateResponse> => {
    const res = await fetch(
      `${API_BASE_URL}/api/intelligence/research/brief/validate`,
      {
        method: "POST",
        headers: buildHeaders(),
        body: JSON.stringify(payload),
      },
    );
    return await parseJsonResponse<ResearchBriefValidateResponse>(res);
  },

  runResearchSearch: async (
    payload: ResearchSearchRequest,
  ): Promise<ResearchSearchResponse> => {
    const res = await fetch(
      `${API_BASE_URL}/api/intelligence/research/search`,
      {
        method: "POST",
        headers: buildHeaders(),
        body: JSON.stringify(payload),
      },
    );
    return await parseJsonResponse<ResearchSearchResponse>(res);
  },

  scoreResearchEvidence: async (
    items: EvidenceScoreItem[],
  ): Promise<EvidenceScoreResponse> => {
    const res = await fetch(
      `${API_BASE_URL}/api/intelligence/research/evidence-score`,
      {
        method: "POST",
        headers: buildHeaders(),
        body: JSON.stringify({ items }),
      },
    );
    return await parseJsonResponse<EvidenceScoreResponse>(res);
  },

  // ── End Decision Log ──────────────────────────────────────────────────────

  markAlertsRead: async (): Promise<void> => {
    const res = await fetch(`${API_BASE_URL}/api/alerts/read`, {
      method: "POST",
      headers: buildHeaders(false),
    });
    await parseJsonResponse<{ status: string }>(res);
  },

  dismissAlert: async (alertId: string): Promise<void> => {
    const res = await fetch(`${API_BASE_URL}/api/alerts/dismiss/${alertId}`, {
      method: "POST",
      headers: buildHeaders(false),
    });
    await parseJsonResponse<{ status: string }>(res);
  },

  // ── Weekly Reports ────────────────────────────────────────────────────────

  generateWeeklyReport: async (params?: {
    week_start?: string;
    week_end?: string;
    language?: "zh" | "en";
  }): Promise<WeeklyReportResponse> => {
    const res = await fetch(`${API_BASE_URL}/api/reports/weekly`, {
      method: "POST",
      headers: buildHeaders(),
      body: JSON.stringify(params ?? {}),
    });
    return await parseJsonResponse<WeeklyReportResponse>(res);
  },

  listWeeklyReports: async (): Promise<{ reports: WeeklyReportSummary[]; total: number }> => {
    const res = await fetch(`${API_BASE_URL}/api/reports/weekly`, {
      headers: buildHeaders(false),
    });
    return await parseJsonResponse<{ reports: WeeklyReportSummary[]; total: number }>(res);
  },

  deleteWeeklyReport: async (reportId: string): Promise<void> => {
    const res = await fetch(`${API_BASE_URL}/api/reports/${reportId}`, {
      method: "DELETE",
      headers: buildHeaders(false),
    });
    await parseJsonResponse<{ status: string }>(res);
  },

  updateWeeklyReport: async (
    reportId: string,
    payload: { title: string; markdown: string }
  ): Promise<{ id: string; title: string; markdown: string }> => {
    const res = await fetch(`${API_BASE_URL}/api/reports/${reportId}`, {
      method: "PATCH",
      headers: buildHeaders(),
      body: JSON.stringify(payload),
    });
    return await parseJsonResponse<{ id: string; title: string; markdown: string }>(res);
  },

  regenerateWeeklyReportChart: async (
    reportId: string,
    productSlug: string,
    chartType: "yearly" | "monthly",
    language?: string
  ): Promise<{ chart_url: string; chart_type: string }> => {
    const res = await fetch(`${API_BASE_URL}/api/reports/${reportId}/regenerate-chart`, {
      method: "POST",
      headers: buildHeaders(),
      body: JSON.stringify({ product_slug: productSlug, chart_type: chartType, language: language ?? "zh" }),
    });
    return await parseJsonResponse<{ chart_url: string; chart_type: string }>(res);
  },

  updateWeeklyReportChartData: async (
    reportId: string,
    productSlug: string,
    chartType: "yearly" | "monthly",
    chart: WeeklyReportSingleChart,
  ): Promise<{ chart_url: string; chart_type: string }> => {
    const res = await fetch(`${API_BASE_URL}/api/reports/${reportId}/chart-data`, {
      method: "PATCH",
      headers: buildHeaders(),
      body: JSON.stringify({ product_slug: productSlug, chart_type: chartType, chart }),
    });
    return await parseJsonResponse<{ chart_url: string; chart_type: string }>(res);
  },

  // ── ERP Price Recommendations ─────────────────────────────────────────────

  submitErpPriceRecommendation: async (payload: {
    product_id: string;
    recommended_price: number;
    valid_from: string;
    valid_until?: string;
    rationale?: string;
    session_id?: string;
  }): Promise<{ recommendation_id: string; status: string }> => {
    const res = await fetch(`${API_BASE_URL}/api/erp/prices`, {
      method: "POST",
      headers: buildHeaders(),
      body: JSON.stringify(payload),
    });
    return await parseJsonResponse(res);
  },

  listErpPriceRecommendations: async (params?: {
    product_id?: string;
    status?: string;
  }): Promise<Array<{
    id: string;
    product_id: string;
    recommended_price: number;
    valid_from: string;
    valid_until?: string;
    rationale?: string;
    status: string;
    submitted_by: string;
    created_at: string;
  }>> => {
    const qs = new URLSearchParams();
    if (params?.product_id) qs.set("product_id", params.product_id);
    if (params?.status) qs.set("status", params.status);
    const query = qs.toString() ? `?${qs}` : "";
    const res = await fetch(`${API_BASE_URL}/api/erp/prices${query}`, {
      headers: buildHeaders(false),
    });
    return await parseJsonResponse<{ recommendations: Array<{
      id: string;
      product_id: string;
      recommended_price: number;
      valid_from: string;
      valid_until?: string;
      rationale?: string;
      status: string;
      submitted_by: string;
      created_at: string;
    }> }>(res).then(r => r.recommendations);
  },

  getErpPriceRecommendation: async (recId: string): Promise<{
    id: string;
    product_id: string;
    recommended_price: number;
    valid_from: string;
    valid_until?: string;
    rationale?: string;
    status: string;
    submitted_by: string;
    created_at: string;
  }> => {
    const res = await fetch(`${API_BASE_URL}/api/erp/prices/${recId}`, {
      headers: buildHeaders(false),
    });
    return await parseJsonResponse(res);
  },
};
