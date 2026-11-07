import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/endpoints";
import type { OperationEventDto } from "../api/types";
import { ConfirmButton } from "../components/Actions";
import { Icon } from "../components/Icon";
import {
  Alert,
  DefinitionItem,
  ErrorState,
  LoadingState,
  PageHeader,
} from "../components/Primitives";
import { StatusBadge } from "../components/StatusBadge";
import { useApiQuery } from "../hooks/useApiQuery";
import { useEventStream } from "../hooks/useEventStream";
import { useI18n } from "../i18n/I18nProvider";
import { pickLocalized } from "../i18n/locale";
import { formatDateTime, formatJson } from "../lib/format";
import {
  isTerminalOperationPayload,
  OPERATION_EVENT_NAMES,
  type ParsedSseEvent,
} from "../lib/sse";

const TERMINAL_STATES = new Set([
  "succeeded",
  "failed",
  "partially_succeeded",
  "cancelled",
]);

function mergeEvents(
  existing: readonly OperationEventDto[],
  incoming: readonly OperationEventDto[],
): OperationEventDto[] {
  const byId = new Map<number, OperationEventDto>();
  for (const event of existing) byId.set(event.event_id, event);
  for (const event of incoming) byId.set(event.event_id, event);
  return [...byId.values()].sort((left, right) => left.event_id - right.event_id);
}

function parseOperationEvent(value: unknown): OperationEventDto | null {
  if (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    typeof (value as Record<string, unknown>).event_id === "number" &&
    typeof (value as Record<string, unknown>).state === "string"
  ) {
    return value as OperationEventDto;
  }
  return null;
}

export function OperationDetailPage() {
  const { t, locale } = useI18n();
  const { id = "" } = useParams();
  const query = useApiQuery(
    (signal) => (id ? api.getOperation(id, signal) : Promise.resolve(null)),
    [id],
    { enabled: Boolean(id) },
  );
  const eventsQuery = useApiQuery(
    (signal) => (id ? api.getOperationEvents(id, signal) : Promise.resolve(null)),
    [id],
    { enabled: Boolean(id) },
  );

  const [events, setEvents] = useState<OperationEventDto[]>([]);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  useEffect(() => {
    setEvents([]);
    setStreamError(null);
    setActionMessage(null);
  }, [id]);

  useEffect(() => {
    const data = eventsQuery.data;
    if (!data) return;
    setEvents((previous) => mergeEvents(previous, data.items));
  }, [eventsQuery.data]);

  const operation = query.data;

  const handleStreamEvent = (event: ParsedSseEvent) => {
    if (event.name === "error") {
      setStreamError(t("operations.streamFailed"));
      return;
    }
    if (event.name !== "operation") return;
    const parsed = parseOperationEvent(event.payload);
    if (parsed) {
      setEvents((previous) => mergeEvents(previous, [parsed]));
    }
    query.reload();
  };

  const streamUrl =
    id && operation && !TERMINAL_STATES.has(operation.state)
      ? `/api/v1/operations/${encodeURIComponent(id)}/stream`
      : null;
  const stream = useEventStream({
    url: streamUrl,
    eventNames: OPERATION_EVENT_NAMES,
    onEvent: handleStreamEvent,
    shouldClose: (event) =>
      event.name === "error" || isTerminalOperationPayload(event.payload),
  });

  if (!id) {
    return (
      <section className="page">
        <ErrorState message={t("operations.notFound")} />
      </section>
    );
  }

  if (query.initialLoading) {
    return (
      <section className="page">
        <PageHeader
          title={t("operations.detailTitle")}
          backTo="/operations"
          backLabel={t("operations.backToOperations")}
        />
        <LoadingState />
      </section>
    );
  }

  if (query.status === "error" || !operation) {
    return (
      <section className="page">
        <PageHeader
          title={t("operations.detailTitle")}
          backTo="/operations"
          backLabel={t("operations.backToOperations")}
        />
        <ErrorState message={t("operations.loadErrorDetail")} onRetry={query.reload} />
      </section>
    );
  }

  const terminal = TERMINAL_STATES.has(operation.state);
  const canCancel = !terminal && operation.state !== "cancel_requested";
  const canReconcile = operation.state === "needs_reconciliation";

  const cancel = async () => {
    setActionMessage(null);
    await api.cancelOperation(operation.operation_id);
    setActionMessage(t("operations.cancelRequested"));
    query.reload();
  };

  const reconcile = async () => {
    setActionMessage(null);
    await api.reconcileOperation(operation.operation_id);
    setActionMessage(t("operations.reconciled"));
    query.reload();
  };

  return (
    <section className="page" aria-labelledby="operation-detail-title">
      <PageHeader
        title={t("operations.detailTitle")}
        description={operation.operation_id}
        backTo="/operations"
        backLabel={t("operations.backToOperations")}
        actions={
          <div className="inline-actions">
            <StatusBadge state={operation.state} />
            {stream.status !== "idle" ? (
              <span className={`live-indicator is-${stream.status}`}>
                <span className="live-dot" aria-hidden="true" />
                <span role="status" aria-live="polite">
                  {stream.status === "open"
                    ? t("operations.live")
                    : stream.status === "connecting" || stream.status === "reconnecting"
                      ? t("operations.reconnecting")
                      : stream.status === "failed"
                        ? t("operations.streamFailed")
                        : t("operations.streamClosed")}
                </span>
                {stream.status === "failed" ? (
                  <button type="button" className="btn btn-ghost btn-small" onClick={stream.reconnect}>
                    <Icon name="refresh" size={14} />
                    {t("operations.reconnect")}
                  </button>
                ) : null}
              </span>
            ) : null}
          </div>
        }
      />

      {actionMessage ? <Alert tone="success">{actionMessage}</Alert> : null}
      {streamError ? (
        <Alert tone="danger" onDismiss={() => setStreamError(null)}>
          {streamError}
        </Alert>
      ) : null}

      <div className="detail-grid">
        <section className="panel" aria-labelledby="operation-summary-heading">
          <h2 id="operation-summary-heading">{t("operations.detailTitleLong")}</h2>
          <dl className="definition-grid">
            <DefinitionItem label={t("operations.action")}>
              <span className="mono">{operation.action}</span>
            </DefinitionItem>
            <DefinitionItem label={t("operations.state")}>
              <StatusBadge state={operation.state} />
            </DefinitionItem>
            <DefinitionItem label={t("operations.plan")}>
              <Link className="text-link" to={`/plans/${encodeURIComponent(operation.plan_id)}`}>
                {operation.plan_id}
              </Link>
            </DefinitionItem>
            <DefinitionItem label={t("operations.approval")}>
              {operation.approval_id ?? "—"}
            </DefinitionItem>
            <DefinitionItem label={t("operations.attempt")}>{operation.attempt}</DefinitionItem>
            <DefinitionItem label={t("operations.started")}>
              {formatDateTime(operation.started_at, locale)}
            </DefinitionItem>
            <DefinitionItem label={t("operations.finished")}>
              {formatDateTime(operation.finished_at, locale)}
            </DefinitionItem>
            <DefinitionItem label={t("operations.cancelRequested")}>
              {formatDateTime(operation.cancel_requested_at, locale)}
            </DefinitionItem>
            {operation.error_code ? (
              <DefinitionItem label={t("operations.error")} mono>
                {operation.error_code}
              </DefinitionItem>
            ) : null}
            {operation.error_message ? (
              <DefinitionItem label={t("operations.errorMessage")}>
                {operation.error_message}
              </DefinitionItem>
            ) : null}
            {operation.needs_reconciliation_reason ? (
              <DefinitionItem label={t("operations.needsReconciliationReason")}>
                {operation.needs_reconciliation_reason}
              </DefinitionItem>
            ) : null}
          </dl>

          <div className="inline-actions action-row">
            <ConfirmButton
              label={
                <>
                  <Icon name="close" size={16} />
                  {t("operations.cancel")}
                </>
              }
              message={t("operations.cancelConfirm")}
              confirmLabel={t("operations.cancel")}
              busyLabel={t("operations.cancelling")}
              onConfirm={cancel}
              disabled={!canCancel}
              variant="secondary"
            />
            <ConfirmButton
              label={
                <>
                  <Icon name="check" size={16} />
                  {t("operations.reconcile")}
                </>
              }
              message={t("operations.reconcileConfirm")}
              confirmLabel={t("operations.reconcile")}
              busyLabel={t("operations.reconciling")}
              onConfirm={reconcile}
              disabled={!canReconcile}
              variant="secondary"
            />
          </div>
          <p className="help-text">{t("operations.cancelHelp")}</p>
          <p className="help-text">{t("operations.reconcileHelp")}</p>
        </section>

        <section className="panel" aria-labelledby="operation-timeline-heading">
          <h2 id="operation-timeline-heading">{t("operations.timeline")}</h2>
          {operation.steps.length === 0 ? (
            <p className="help-text">{t("operations.noSteps")}</p>
          ) : (
            <ol className="timeline" aria-label={t("operations.timeline")}>
              {operation.steps.map((step) => (
                <li key={step.step_id} className="timeline-item">
                  <span className={`timeline-dot tone-${step.state}`} aria-hidden="true" />
                  <div className="timeline-content">
                    <div className="timeline-head">
                      <span className="timeline-name">{step.name}</span>
                      <StatusBadge state={step.state} />
                    </div>
                    {step.detail_zh || step.detail_en ? (
                      <p>{pickLocalized(locale, step.detail_zh, step.detail_en)}</p>
                    ) : null}
                    <p className="part-meta">
                      {formatDateTime(step.started_at, locale)}
                      {step.finished_at ? ` → ${formatDateTime(step.finished_at, locale)}` : ""}
                    </p>
                  </div>
                </li>
              ))}
            </ol>
          )}
        </section>

        <section className="panel" aria-labelledby="operation-events-heading">
          <h2 id="operation-events-heading">{t("operations.activity")}</h2>
          {events.length === 0 ? (
            <p className="help-text">{t("operations.noActivity")}</p>
          ) : (
            <ol className="event-log" aria-label={t("operations.activity")}>
              {events.map((event) => (
                <li key={event.event_id} className="event-log-item">
                  <div className="event-log-head">
                    <StatusBadge state={event.state} />
                    <time dateTime={event.created_at}>
                      {formatDateTime(event.created_at, locale)}
                    </time>
                  </div>
                  <p>{pickLocalized(locale, event.message_zh, event.message_en)}</p>
                  {Object.keys(event.data).length > 0 ? (
                    <details className="details-block">
                      <summary>{t("operations.technicalDetails")}</summary>
                      <pre className="code-block">{formatJson(event.data)}</pre>
                    </details>
                  ) : null}
                </li>
              ))}
            </ol>
          )}
        </section>

        <section className="panel" aria-labelledby="operation-scope-heading">
          <h2 id="operation-scope-heading">{t("operations.accountContext")}</h2>
          <dl className="definition-grid">
            <DefinitionItem label={t("operations.tenant")} mono>
              {operation.tenant_key}
            </DefinitionItem>
            <DefinitionItem label={t("operations.cloud")}>{operation.cloud_id}</DefinitionItem>
            <DefinitionItem label={t("operations.account")}>{operation.account_id}</DefinitionItem>
            <DefinitionItem label={t("operations.planDigest")} mono>
              {operation.plan_digest}
            </DefinitionItem>
            {operation.idempotency_key ? (
              <DefinitionItem label={t("operations.idempotency")} mono>
                {operation.idempotency_key}
              </DefinitionItem>
            ) : null}
            {operation.request_id ? (
              <DefinitionItem label={t("operations.requestId")} mono>
                {operation.request_id}
              </DefinitionItem>
            ) : null}
          </dl>
          {Object.keys(operation.result).length > 0 ? (
            <details className="details-block">
              <summary>{t("operations.result")}</summary>
              <pre className="code-block">{formatJson(operation.result)}</pre>
            </details>
          ) : null}
        </section>
      </div>
    </section>
  );
}
