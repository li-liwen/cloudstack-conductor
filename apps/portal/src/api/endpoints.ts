import { apiRequest } from "./client";
import type {
  ActionPlanDto,
  ApprovePlanResponse,
  AuditResponse,
  ConnectionsResponse,
  ConsoleLaunchDto,
  ConversationDetailDto,
  ConversationsResponse,
  DocsResponse,
  IdentityMappingDto,
  KnowledgeDocumentDto,
  MappingsResponse,
  MigrationsResponse,
  OperationDto,
  OperationEventsResponse,
  OperatorHealthResponse,
  OperatorPricingResponse,
  OverrideRequest,
  PlansResponse,
  PlanViewDto,
  PriceOverrideDto,
  QuoteResponse,
  ResourcesResponse,
  SearchResponseDto,
  SendMessageResponse,
  SessionInfo,
  InstanceResource,
} from "./types";

const segment = (value: string): string => encodeURIComponent(value);

export interface LogoutResponse {
  ok: boolean;
  logout_url?: string;
}

export interface SendMessageInput {
  message: string;
  locale: "zh-CN" | "en";
}

export interface MappingUpsertInput {
  subject: string;
  cloud_id: string;
  cloudstack_user_id: string;
  cloudstack_account_id: string;
  cloudstack_domain_id: string;
  cloudstack_domain: string;
  cloudstack_username: string;
  account: string;
  display_name?: string;
  email?: string;
  domain_ancestors?: string[];
  roles?: string[];
  project_ids?: string[];
  delegated_account_ids?: string[];
  mapping_version?: string;
  stage?: string;
  mfa_required?: boolean;
  legacy_2fa?: boolean;
}

export const api = {
  // -- authentication ------------------------------------------------------
  getSession(signal?: AbortSignal): Promise<SessionInfo> {
    return apiRequest<SessionInfo>("/auth/session", {
      signal,
      handleUnauthorized: false,
    });
  },

  demoLogin(subject: string, signal?: AbortSignal): Promise<SessionInfo> {
    return apiRequest<SessionInfo>("/auth/demo-login", {
      method: "POST",
      body: { subject },
      csrf: false,
      handleUnauthorized: false,
      signal,
    });
  },

  logout(): Promise<LogoutResponse> {
    return apiRequest<LogoutResponse>("/auth/logout", { method: "POST" });
  },

  // -- conversations -------------------------------------------------------
  listConversations(signal?: AbortSignal): Promise<ConversationsResponse> {
    return apiRequest<ConversationsResponse>("/conversations", {
      query: { limit: 100 },
      signal,
    });
  },

  createConversation(signal?: AbortSignal) {
    return apiRequest<import("./types").ConversationDto>("/conversations", {
      method: "POST",
      body: {},
      signal,
    });
  },

  getConversation(conversationId: string, signal?: AbortSignal): Promise<ConversationDetailDto> {
    return apiRequest<ConversationDetailDto>(`/conversations/${segment(conversationId)}`, {
      signal,
    });
  },

  sendMessage(
    conversationId: string,
    input: SendMessageInput,
    signal?: AbortSignal,
  ): Promise<SendMessageResponse> {
    return apiRequest<SendMessageResponse>(
      `/conversations/${segment(conversationId)}/messages`,
      {
        method: "POST",
        body: { message: input.message, locale: input.locale },
        signal,
      },
    );
  },

  getRun(runId: string, signal?: AbortSignal) {
    return apiRequest<import("./types").RunDto>(`/runs/${segment(runId)}`, { signal });
  },

  // -- plans ---------------------------------------------------------------
  listPlans(signal?: AbortSignal): Promise<PlansResponse> {
    return apiRequest<PlansResponse>("/plans", { query: { limit: 100 }, signal });
  },

  getPlan(planId: string, signal?: AbortSignal): Promise<PlanViewDto> {
    return apiRequest<PlanViewDto>(`/plans/${segment(planId)}`, { signal });
  },

  approvePlan(planId: string, digest: string, note?: string): Promise<ApprovePlanResponse> {
    return apiRequest<ApprovePlanResponse>(`/plans/${segment(planId)}/approve`, {
      method: "POST",
      body: note ? { digest, note } : { digest },
    });
  },

  rejectPlan(planId: string, note?: string): Promise<{ plan: ActionPlanDto }> {
    return apiRequest<{ plan: ActionPlanDto }>(`/plans/${segment(planId)}/reject`, {
      method: "POST",
      body: note ? { note } : {},
    });
  },

  // -- operations ----------------------------------------------------------
  listOperations(signal?: AbortSignal): Promise<{ items: OperationDto[] }> {
    return apiRequest<{ items: OperationDto[] }>("/operations", {
      query: { limit: 100 },
      signal,
    });
  },

  getOperation(operationId: string, signal?: AbortSignal): Promise<OperationDto> {
    return apiRequest<OperationDto>(`/operations/${segment(operationId)}`, { signal });
  },

  getOperationEvents(operationId: string, signal?: AbortSignal): Promise<OperationEventsResponse> {
    return apiRequest<OperationEventsResponse>(
      `/operations/${segment(operationId)}/events`,
      { signal },
    );
  },

  cancelOperation(operationId: string): Promise<OperationDto> {
    return apiRequest<OperationDto>(`/operations/${segment(operationId)}/cancel`, {
      method: "POST",
    });
  },

  reconcileOperation(operationId: string): Promise<OperationDto> {
    return apiRequest<OperationDto>(`/operations/${segment(operationId)}/reconcile`, {
      method: "POST",
    });
  },

  // -- resources, quotes, console -----------------------------------------
  getResources(kind: string, signal?: AbortSignal): Promise<ResourcesResponse> {
    return apiRequest<ResourcesResponse>("/resources", { query: { kind }, signal });
  },

  getInstance(instanceId: string, signal?: AbortSignal): Promise<InstanceResource> {
    return apiRequest<InstanceResource>(`/instances/${segment(instanceId)}`, { signal });
  },

  openConsole(instanceId: string): Promise<ConsoleLaunchDto> {
    return apiRequest<ConsoleLaunchDto>(`/instances/${segment(instanceId)}/console`, {
      method: "POST",
    });
  },

  createQuote(offeringId: string, accountId: string, signal?: AbortSignal): Promise<QuoteResponse> {
    return apiRequest<QuoteResponse>("/quotes", {
      method: "POST",
      body: { offering_id: offeringId, account_id: accountId },
      signal,
    });
  },

  // -- documentation -------------------------------------------------------
  listDocs(signal?: AbortSignal): Promise<DocsResponse> {
    return apiRequest<DocsResponse>("/docs", { signal });
  },

  searchDocs(query: string, signal?: AbortSignal): Promise<SearchResponseDto> {
    return apiRequest<SearchResponseDto>("/docs/search", {
      query: { q: query },
      signal,
    });
  },

  getDoc(documentId: string, signal?: AbortSignal): Promise<KnowledgeDocumentDto> {
    return apiRequest<KnowledgeDocumentDto>(`/docs/${segment(documentId)}`, { signal });
  },

  // -- MCP connections -----------------------------------------------------
  listConnections(signal?: AbortSignal): Promise<ConnectionsResponse> {
    return apiRequest<ConnectionsResponse>("/connections", { signal });
  },

  revokeConnection(connectionId: string): Promise<{ ok: boolean }> {
    return apiRequest<{ ok: boolean }>(`/connections/${segment(connectionId)}`, {
      method: "DELETE",
    });
  },

  // -- operator ------------------------------------------------------------
  // The operator router requires CSRF on reads as well as writes.
  listMappings(signal?: AbortSignal): Promise<MappingsResponse> {
    return apiRequest<MappingsResponse>("/operator/mappings", {
      query: { limit: 200 },
      csrf: true,
      signal,
    });
  },

  upsertMapping(input: MappingUpsertInput): Promise<IdentityMappingDto> {
    return apiRequest<IdentityMappingDto>("/operator/mappings", {
      method: "POST",
      body: input,
    });
  },

  getOperatorPricing(signal?: AbortSignal): Promise<OperatorPricingResponse> {
    return apiRequest<OperatorPricingResponse>("/operator/pricing", {
      csrf: true,
      signal,
    });
  },

  createPriceOverride(input: OverrideRequest): Promise<PriceOverrideDto> {
    return apiRequest<PriceOverrideDto>("/operator/pricing/overrides", {
      method: "POST",
      body: input,
    });
  },

  listMigrations(signal?: AbortSignal): Promise<MigrationsResponse> {
    return apiRequest<MigrationsResponse>("/operator/migrations", {
      csrf: true,
      signal,
    });
  },

  getOperatorHealth(signal?: AbortSignal): Promise<OperatorHealthResponse> {
    return apiRequest<OperatorHealthResponse>("/operator/health", {
      csrf: true,
      signal,
    });
  },

  listAudit(signal?: AbortSignal): Promise<AuditResponse> {
    return apiRequest<AuditResponse>("/operator/audit", {
      query: { limit: 200 },
      csrf: true,
      signal,
    });
  },
};
