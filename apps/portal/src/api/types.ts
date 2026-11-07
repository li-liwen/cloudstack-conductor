/** API DTOs mirroring the Conductor FastAPI contract. Money is always a string. */

export type Money = string;
export type PrincipalRole = "user" | "domain_admin" | "operator" | string;
export type LocaleCode = "zh-CN" | "en";

export interface Principal {
  subject: string;
  username: string;
  display_name: string;
  email: string | null;
  cloud_id: string;
  cloudstack_user_id: string;
  cloudstack_account_id: string;
  cloudstack_domain_id: string;
  cloudstack_domain: string;
  cloudstack_username: string;
  account: string;
  domain_ancestors: string[];
  roles: PrincipalRole[];
  project_ids: string[];
  delegated_account_ids: string[];
  groups: string[];
  amr: string[];
  mapping_version: string;
}

export interface SessionInfo {
  principal: Principal;
  expires_at: string;
  csrf_token: string;
  mfa: boolean;
}

export interface ConversationDto {
  conversation_id: string;
  principal_subject: string;
  tenant_key: string;
  title: string;
  created_at: string;
  updated_at: string;
  archived: boolean;
  cloud_context: Record<string, string>;
}

export interface MessagePartDto {
  type: string;
  text?: string | null;
  data?: Record<string, unknown> | null;
}

export interface MessageDto {
  message_id: string;
  conversation_id: string;
  role: "user" | "assistant" | "system" | "tool" | string;
  parts: MessagePartDto[];
  created_at: string;
  run_id?: string | null;
  citations: Array<Record<string, unknown>>;
}

export interface RunDto {
  run_id: string;
  conversation_id: string;
  principal_subject: string;
  state: "queued" | "running" | "awaiting_approval" | "completed" | "failed" | "cancelled" | string;
  created_at: string;
  updated_at: string;
  plan_id: string | null;
  operation_id: string | null;
  error: string | null;
  user_message_id: string | null;
  assistant_message_id: string | null;
}

export interface ConversationDetailDto {
  conversation: ConversationDto;
  messages: MessageDto[];
  runs: RunDto[];
}

export interface ConversationsResponse {
  items: ConversationDto[];
}

export interface SendMessageResponse {
  run: RunDto;
  message: MessageDto;
}

export interface ComputeShape {
  cpu_count: number;
  memory_gib: string;
  gpu_count: number;
  ssd_gib: string;
  hdd_gib: string;
  cpu_speed_mhz?: number | null;
  name?: string | null;
}

export interface QuoteLineDto {
  kind: "compute" | "storage" | "discount" | string;
  label: string;
  quantity: string;
  unit: string;
  unit_rate: Money;
  amount: Money;
  detail: string | null;
}

export interface PriceQuoteDto {
  quote_id: string;
  cloud_id: string;
  account_id: string;
  offering_id: string;
  offering_name: string;
  shape: ComputeShape | null;
  currency: string;
  rate_card_version: string;
  pricing_version: string;
  effective_at: string;
  compute_hourly: Money;
  storage_hourly: Money;
  discount_hourly: Money;
  total_hourly: Money;
  status: "priced" | "partial" | "unpriced" | string;
  line_items: QuoteLineDto[];
  unpriced_reasons: string[];
  override_id: string | null;
  notes: string[];
}

export interface PlanResourceDto {
  kind: string;
  resource_id: string;
  name: string | null;
  account_id: string | null;
  project_id: string | null;
}

export interface PlanEffectDto {
  kind: string;
  summary_zh: string;
  summary_en: string;
  resource_id: string | null;
  cost_delta_hourly: Money | null;
  data_at_risk: string[];
}

export interface ActionPlanDto {
  plan_id: string;
  principal_subject: string;
  tenant_key: string;
  cloud_id: string;
  account_id: string;
  project_id: string | null;
  action: string;
  parameters: Record<string, unknown>;
  resources: PlanResourceDto[];
  quote: PriceQuoteDto | null;
  pricing_version: string | null;
  preconditions: string[];
  effects: PlanEffectDto[];
  downtime_seconds: number | null;
  data_effects: string[];
  approval_requirement: "none" | "required" | "explicit" | string;
  state: "draft" | "awaiting_approval" | "approved" | "rejected" | "expired" | "executed" | "cancelled" | string;
  digest: string;
  created_at: string;
  expires_at: string;
  explanation_zh: string;
  explanation_en: string;
  request_id: string | null;
  idempotency_key: string | null;
}

export interface ApprovalDto {
  approval_id: string;
  plan_id: string;
  digest: string;
  approved_by: string;
  approved_at: string;
  expires_at: string;
  method: "browser_session" | "operator_override" | string;
  note: string | null;
  client_fingerprint: string | null;
}

export interface QuoteSummaryDto {
  currency: string;
  hourly: Money;
  monthly: Money;
  half_year: Money;
  status: string;
  partial: boolean;
  pricing_version?: string;
}

export interface PlanViewDto {
  plan: ActionPlanDto;
  approval: ApprovalDto | null;
  quote_summary: QuoteSummaryDto | null;
  can_approve: boolean;
  approval_ttl_seconds: number;
}

export interface PlansResponse {
  items: ActionPlanDto[];
}

export interface ApprovePlanResponse {
  plan: ActionPlanDto;
  operation: OperationDto;
}

export interface OperationStepDto {
  step_id: string;
  name: string;
  state: "pending" | "running" | "succeeded" | "failed" | "skipped" | "unknown" | string;
  started_at: string | null;
  finished_at: string | null;
  detail_zh: string | null;
  detail_en: string | null;
  cloudstack_job_id: string | null;
  result: Record<string, unknown>;
}

export interface OperationDto {
  operation_id: string;
  plan_id: string;
  principal_subject: string;
  tenant_key: string;
  cloud_id: string;
  account_id: string;
  project_id: string | null;
  action: string;
  state:
    | "draft"
    | "awaiting_approval"
    | "queued"
    | "running"
    | "succeeded"
    | "failed"
    | "partially_succeeded"
    | "cancel_requested"
    | "cancelled"
    | "needs_reconciliation"
    | string;
  created_at: string;
  updated_at: string;
  approval_id: string | null;
  plan_digest: string;
  idempotency_key: string | null;
  cloudstack_job_id: string | null;
  resource_ids: string[];
  steps: OperationStepDto[];
  result: Record<string, unknown>;
  error_code: string | null;
  error_message: string | null;
  attempt: number;
  started_at: string | null;
  finished_at: string | null;
  cancel_requested_at: string | null;
  needs_reconciliation_reason: string | null;
  request_id: string | null;
}

export interface OperationsResponse {
  items: OperationDto[];
}

export interface OperationEventDto {
  event_id: number;
  operation_id: string;
  state: string;
  message_zh: string;
  message_en: string;
  created_at: string;
  data: Record<string, unknown>;
}

export interface OperationEventsResponse {
  items: OperationEventDto[];
}

export interface InstanceResource {
  id: string;
  name: string;
  state: string;
  ip_addresses: string[];
  offering_id: string | null;
  offering_name: string | null;
  template_id: string | null;
  template_name: string | null;
  zone_id: string | null;
  zone_name: string | null;
  account_id: string | null;
  account: string | null;
  created: string | null;
  running: boolean;
}

export interface VolumeResource {
  id: string;
  name: string;
  type: string;
  size_gib: string;
  state: string;
  attached_to: string | null;
  vm_name: string | null;
  disk_offering_id: string | null;
  disk_offering_name: string | null;
  zone_id: string | null;
  account_id: string | null;
  created: string | null;
}

export interface NetworkResource {
  id: string;
  name: string;
  type: string;
  state: string;
  cidr: string | null;
  gateway: string | null;
  vlan: string | null;
  zone_id: string | null;
  account_id: string | null;
  is_default: boolean;
}

export interface OfferingResource {
  id: string;
  name: string;
  cpu_count: number;
  cpu_speed_mhz: number;
  memory_gib: string;
  gpu_count: number;
}

export interface TemplateResource {
  id: string;
  name: string;
  os_type: string;
  format: string;
  hypervisor: string;
  ready: boolean;
  public: boolean;
}

export interface ResourceItems {
  instances?: InstanceResource[];
  volumes?: VolumeResource[];
  networks?: NetworkResource[];
  offerings?: OfferingResource[];
  templates?: TemplateResource[];
}

export interface ResourcesResponse {
  items: ResourceItems;
  refreshed_at: string;
}

export interface ConsoleLaunchDto {
  launch_url: string;
  expires_at: string;
  vm_id: string;
  vm_name: string | null;
}

export interface CostEstimateDto {
  estimate_id: string;
  period: "month" | "half_year" | "forecast" | string;
  period_label: string;
  currency: string;
  starts_at: string;
  ends_at: string;
  billable_hours: string;
  compute: Money;
  storage: Money;
  discount: Money;
  total: Money;
  completeness: "complete" | "partial" | "provisional" | string;
  pricing_version: string;
  generated_at: string;
  assumptions: string[];
  disclaimer_zh: string;
  disclaimer_en: string;
}

export interface BillingPeriodDto {
  kind: string;
  label: string;
  start_date: string;
  end_date: string;
  starts_at: string;
  ends_at: string;
}

export interface QuoteResponse {
  quote: PriceQuoteDto;
  summary: QuoteSummaryDto;
  estimates: {
    month_zh: CostEstimateDto;
    month_en: CostEstimateDto;
    half_year_zh: CostEstimateDto;
    forecast_zh: CostEstimateDto;
  };
  current_period: BillingPeriodDto;
}

export interface KnowledgeDocumentDto {
  document_id: string;
  title: string;
  version: string;
  source_url: string;
  section: string | null;
  content_hash: string;
  language: string;
  license_note: string | null;
  ingested_at: string;
  visibility: string;
}

export interface DocsResponse {
  items: KnowledgeDocumentDto[];
}

export interface SearchHitDto {
  chunk_id: string;
  document_id: string;
  title: string;
  section: string | null;
  source_url: string;
  version: string;
  snippet: string;
  score: number;
  lexical_score: number;
  vector_score: number;
  chunk_ordinal: number;
}

export interface SearchResponseDto {
  query: string;
  hits: SearchHitDto[];
  performed_at: string;
  retrieval: string;
}

export interface MCPConnectionDto {
  connection_id: string;
  principal_subject: string;
  cloud_id: string;
  account_id: string;
  project_id: string | null;
  client_id: string;
  client_name: string;
  scopes: string[];
  state: "active" | "revoked" | "expired" | string;
  created_at: string;
  last_used_at: string | null;
  expires_at: string;
  revoked_at: string | null;
  revoked_reason: string | null;
  authorization_lifetime_days: number;
}

export interface MCPAuditEntryDto {
  entry_id: string;
  connection_id: string;
  occurred_at: string;
  event: string;
  detail: string | null;
  tool_name: string | null;
  outcome: string | null;
}

export interface ConnectionItemDto {
  connection: MCPConnectionDto;
  audit: MCPAuditEntryDto[];
}

export interface ConnectionsResponse {
  items: ConnectionItemDto[];
}

export interface IdentityMappingDto {
  subject: string;
  cloud_id: string;
  cloudstack_user_id: string;
  cloudstack_account_id: string;
  cloudstack_domain_id: string;
  cloudstack_domain: string;
  cloudstack_username: string;
  account: string;
  domain_ancestors: string[];
  mapping_version: string;
  stage: string;
  saml_provider_id: string | null;
  roles: string[];
  project_ids: string[];
  delegated_account_ids: string[];
  mfa_required: boolean;
  legacy_2fa: boolean;
  display_name: string;
  email: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface MappingsResponse {
  items: IdentityMappingDto[];
}

export interface RateCardDto {
  version: string;
  currency: string;
  effective_from: string;
  effective_to: string | null;
  cpu_hour: Money;
  memory_gib_hour: Money;
  gpu_hour: Money;
  ssd_gib_hour: Money;
  hdd_gib_hour: Money;
  source_checksum: string | null;
}

export interface OfferingBindingDto {
  offering_id: string;
  offering_name: string;
  shape: ComputeShape;
  rate_card_version: string;
  effective_from: string;
  effective_to: string | null;
  mapping_version: string;
  source_reference: string | null;
}

export interface PriceOverrideDto {
  id: string;
  scope: "account" | "domain" | string;
  scope_id: string;
  discount_percent: string;
  effective_from: string;
  effective_to: string | null;
  version: string;
}

export interface OperatorPricingResponse {
  publication: {
    version: string;
    published_at: string;
    published_by: string;
    checksum: string;
  } | null;
  rate_cards: RateCardDto[];
  bindings: OfferingBindingDto[];
  overrides: PriceOverrideDto[];
}

export interface MigrationRowDto {
  subject: string;
  cloudstack_username: string;
  domain: string;
  mapping_stage: string;
  password_stage: string;
  attempts: number;
  legacy_2fa: boolean;
  mfa_required: boolean;
}

export interface MigrationsResponse {
  items: MigrationRowDto[];
}

export interface UsageWindowDto {
  window_id: string;
  cloud_id: string;
  started_at: string;
  ended_at: string;
  status: string;
  records_seen: number;
  records_stored: number;
  duplicates: number;
  gaps: Array<{ start_at: string; end_at: string; reason: string }>;
  created_at: string;
}

export interface OperatorHealthResponse {
  database: boolean;
  execution: Record<string, unknown> | null;
  counts: Record<string, number>;
  usage_windows: UsageWindowDto[];
  recent_audit: Array<{
    occurred_at: string;
    actor: string;
    action: string;
    outcome: string;
  }>;
}

export interface AuditRowDto {
  id: string;
  occurred_at: string;
  actor: string;
  action: string;
  target_type: string;
  target_id: string;
  outcome: string;
  detail: unknown;
}

export interface AuditResponse {
  items: AuditRowDto[];
}

export interface OverrideRequest {
  scope: "account" | "domain";
  scope_id: string;
  discount_percent: string;
  effective_from?: string;
  effective_to?: string;
  version?: string;
  note?: string;
}

/** The in-memory shape attached to assistant-ui messages for rich rendering. */
export interface RichMessageMetadata {
  parts: MessagePartDto[];
  citations: Array<Record<string, unknown>>;
}
