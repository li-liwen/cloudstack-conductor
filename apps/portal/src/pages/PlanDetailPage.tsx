import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/endpoints";
import type { ApprovePlanResponse, OperationDto, PlanEffectDto } from "../api/types";
import { ConfirmButton, CopyButton } from "../components/Actions";
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
import { useI18n } from "../i18n/I18nProvider";
import { pickLocalized } from "../i18n/locale";
import { userErrorMessage } from "../lib/errors";
import { formatDateTime, formatDuration, formatJson } from "../lib/format";
import { formatMoney } from "../lib/money";
import { approvalLabel } from "../lib/status";

function EffectItem({
  effect,
  locale,
  currency,
}: {
  effect: PlanEffectDto;
  locale: "zh-CN" | "en";
  currency: string;
}) {
  const { t } = useI18n();
  return (
    <li className="effect-item">
      <div className="effect-item-head">
        <span className="effect-kind">{effect.kind.replaceAll("_", " ")}</span>
        <span className="effect-summary">
          {pickLocalized(locale, effect.summary_zh, effect.summary_en)}
        </span>
      </div>
      {effect.resource_id ? (
        <p className="part-meta mono break-all">{effect.resource_id}</p>
      ) : null}
      {effect.cost_delta_hourly !== null ? (
        <p className="part-meta">
          {t("plans.costDelta")}: {formatMoney(effect.cost_delta_hourly, currency)}
        </p>
      ) : null}
      {effect.data_at_risk.length > 0 ? (
        <p className="part-meta">{effect.data_at_risk.join(", ")}</p>
      ) : null}
    </li>
  );
}

function OperationSummary({ operation }: { operation: OperationDto }) {
  const { t } = useI18n();
  return (
    <Alert tone="success" title={t("plans.operationCreated")}>
      <p className="mono break-all">{operation.operation_id}</p>
      <div className="inline-actions">
        <StatusBadge state={operation.state} />
        <Link className="text-link" to={`/operations/${encodeURIComponent(operation.operation_id)}`}>
          {t("plans.viewOperation")}
          <Icon name="chevronRight" size={15} />
        </Link>
      </div>
    </Alert>
  );
}

export function PlanDetailPage() {
  const { t, locale } = useI18n();
  const { id = "" } = useParams();
  const query = useApiQuery((signal) => api.getPlan(id, signal), [id], {
    enabled: Boolean(id),
  });
  const [confirmed, setConfirmed] = useState(false);
  const [approving, setApproving] = useState(false);
  const [approveError, setApproveError] = useState<string | null>(null);
  const [approvalResult, setApprovalResult] = useState<ApprovePlanResponse | null>(null);
  const [rejected, setRejected] = useState(false);
  const [rejectNote, setRejectNote] = useState("");

  if (!id) {
    return (
      <section className="page">
        <ErrorState message={t("plans.notFound")} />
      </section>
    );
  }

  if (query.initialLoading) {
    return (
      <section className="page">
        <PageHeader title={t("plans.detailTitle")} backTo="/plans" backLabel={t("plans.backToPlans")} />
        <LoadingState />
      </section>
    );
  }

  if (query.status === "error" || !query.data) {
    return (
      <section className="page">
        <PageHeader title={t("plans.detailTitle")} backTo="/plans" backLabel={t("plans.backToPlans")} />
        <ErrorState message={t("plans.loadErrorDetail")} onRetry={query.reload} />
      </section>
    );
  }

  const view = query.data;
  const plan = view.plan;
  const summary = view.quote_summary;
  const currency = summary?.currency ?? plan.quote?.currency ?? "";
  const explanation = pickLocalized(locale, plan.explanation_zh, plan.explanation_en);
  const awaitingApproval = plan.state === "awaiting_approval";
  const approval = view.approval;
  const canApprove = awaitingApproval && view.can_approve;

  const approve = async () => {
    if (!confirmed || approving) return;
    setApproving(true);
    setApproveError(null);
    try {
      const result = await api.approvePlan(plan.plan_id, plan.digest);
      setApprovalResult(result);
      setConfirmed(false);
      query.reload();
    } catch (error) {
      setApproveError(userErrorMessage(error, t));
    } finally {
      setApproving(false);
    }
  };

  return (
    <section className="page" aria-labelledby="plan-detail-title">
      <PageHeader
        title={t("plans.detailTitle")}
        description={plan.plan_id}
        backTo="/plans"
        backLabel={t("plans.backToPlans")}
        actions={<StatusBadge state={plan.state} />}
      />

      {approvalResult ? <OperationSummary operation={approvalResult.operation} /> : null}
      {rejected ? <Alert tone="warning">{t("plans.rejected")}</Alert> : null}
      {approveError ? <Alert tone="danger">{approveError}</Alert> : null}

      <div className="detail-grid">
        <section className="panel" aria-labelledby="plan-overview-heading">
          <h2 id="plan-overview-heading">{t("plans.detailTitle")}</h2>
          <dl className="definition-grid">
            <DefinitionItem label={t("plans.action")}>
              <span className="mono">{plan.action}</span>
            </DefinitionItem>
            <DefinitionItem label={t("plans.currentState")}>
              <StatusBadge state={plan.state} />
            </DefinitionItem>
            <DefinitionItem label={t("plans.account")}>{plan.account_id}</DefinitionItem>
            <DefinitionItem label={t("plans.created")}>
              {formatDateTime(plan.created_at, locale)}
            </DefinitionItem>
            <DefinitionItem label={t("plans.expires")}>
              {formatDateTime(plan.expires_at, locale)}
            </DefinitionItem>
            <DefinitionItem label={t("plans.approval")}>
              {approvalLabel(t, plan.approval_requirement)}
            </DefinitionItem>
            <DefinitionItem label={t("plans.downtime")}>
              {formatDuration(plan.downtime_seconds, t)}
            </DefinitionItem>
            <DefinitionItem label={t("plans.pricingVersion")}>
              {plan.pricing_version ?? "—"}
            </DefinitionItem>
          </dl>

          <h3>{t("plans.digest")}</h3>
          <div className="digest-box">
            <code className="digest-value break-all">{plan.digest || "—"}</code>
            {plan.digest ? (
              <CopyButton
                value={plan.digest}
                label={t("plans.copyDigest")}
                copiedLabel={t("plans.digestCopied")}
              />
            ) : null}
          </div>
          <p className="help-text">{t("plans.preparedBy")}</p>

          {explanation ? (
            <>
              <h3>{t("plans.explanation")}</h3>
              <p className="pre-wrap">{explanation}</p>
            </>
          ) : null}
        </section>

        <section className="panel" aria-labelledby="plan-approval-heading">
          <h2 id="plan-approval-heading">{t("plans.approval")}</h2>
          <p className="help-text">{t("plans.approvalHelp")}</p>

          {approval ? (
            <dl className="definition-grid">
              <DefinitionItem label={t("plans.approvedBy")}>{approval.approved_by}</DefinitionItem>
              <DefinitionItem label={t("plans.approvedAt")}>
                {formatDateTime(approval.approved_at, locale)}
              </DefinitionItem>
              <DefinitionItem label={t("plans.approvalExpires")}>
                {formatDateTime(approval.expires_at, locale)}
              </DefinitionItem>
              <DefinitionItem label={t("plans.approvalMethod")}>{approval.method}</DefinitionItem>
              <DefinitionItem label={t("plans.digest")}>
                <span className="mono break-all">{approval.digest}</span>
              </DefinitionItem>
              {approval.note ? (
                <DefinitionItem label={t("plans.note")}>{approval.note}</DefinitionItem>
              ) : null}
            </dl>
          ) : null}

          {canApprove ? (
            <div className="approval-box">
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={confirmed}
                  onChange={(event) => setConfirmed(event.target.checked)}
                />
                <span>{t("plans.approvalBoundToDigest")}</span>
              </label>
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => void approve()}
                disabled={!confirmed || approving}
              >
                <Icon name="check" size={16} />
                {approving ? t("plans.approving") : t("plans.approve")}
              </button>

              <div className="divider" />
              <div className="field">
                <label htmlFor="reject-note">{t("plans.rejectNoteLabel")}</label>
                <input
                  id="reject-note"
                  type="text"
                  value={rejectNote}
                  maxLength={500}
                  placeholder={t("plans.rejectNotePlaceholder")}
                  onChange={(event) => setRejectNote(event.target.value)}
                />
              </div>
              <ConfirmButton
                label={
                  <>
                    <Icon name="close" size={16} />
                    {t("plans.reject")}
                  </>
                }
                message={t("plans.rejectConfirm")}
                confirmLabel={t("plans.reject")}
                busyLabel={t("plans.rejecting")}
                onConfirm={async () => {
                  await api.rejectPlan(plan.plan_id, rejectNote.trim() || undefined);
                  setRejected(true);
                  query.reload();
                }}
              />
            </div>
          ) : awaitingApproval ? (
            <Alert tone="warning">{t("plans.planExpiredHelp")}</Alert>
          ) : (
            <p className="help-text">{t("plans.cannotApprove")}</p>
          )}
        </section>

        <section className="panel" aria-labelledby="plan-quote-heading">
          <h2 id="plan-quote-heading">{t("plans.quoteSummary")}</h2>
          {summary ? (
            <>
              <dl className="stat-grid stat-grid-wide">
                <div>
                  <dt>{t("workspace.hourly")}</dt>
                  <dd>{formatMoney(summary.hourly, currency)}</dd>
                </div>
                <div>
                  <dt>{t("workspace.monthly")}</dt>
                  <dd>{formatMoney(summary.monthly, currency)}</dd>
                </div>
                <div>
                  <dt>{t("workspace.halfYear")}</dt>
                  <dd>{formatMoney(summary.half_year, currency)}</dd>
                </div>
              </dl>
              {summary.partial ? <Alert tone="warning">{t("plans.partialQuote")}</Alert> : null}
              <dl className="definition-grid">
                <DefinitionItem label={t("estimates.status")}>
                  <StatusBadge state={summary.status} />
                </DefinitionItem>
                <DefinitionItem label={t("plans.pricingVersion")}>
                  {plan.pricing_version ?? summary.pricing_version ?? "—"}
                </DefinitionItem>
                {plan.quote ? (
                  <DefinitionItem label={t("estimates.rateCard")}>
                    {plan.quote.rate_card_version}
                  </DefinitionItem>
                ) : null}
              </dl>
            </>
          ) : (
            <p className="help-text">{t("plans.quoteUnavailable")}</p>
          )}
        </section>

        <section className="panel" aria-labelledby="plan-effects-heading">
          <h2 id="plan-effects-heading">{t("plans.effects")}</h2>
          <p className="help-text">{t("plans.effectsHelp")}</p>
          {plan.effects.length > 0 ? (
            <ul className="effect-list effect-list-detailed">
              {plan.effects.map((effect, index) => (
                <EffectItem
                  key={`${effect.kind}-${index}`}
                  effect={effect}
                  locale={locale}
                  currency={currency}
                />
              ))}
            </ul>
          ) : (
            <p className="help-text">{t("plans.noEffects")}</p>
          )}

          <h3>{t("plans.downtime")}</h3>
          <p>{formatDuration(plan.downtime_seconds, t)}</p>

          <h3>{t("plans.dataEffects")}</h3>
          {plan.data_effects.length > 0 ? (
            <ul className="plain-list">
              {plan.data_effects.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}
            </ul>
          ) : (
            <p className="help-text">{t("plans.noDataEffects")}</p>
          )}
        </section>

        <section className="panel" aria-labelledby="plan-resources-heading">
          <h2 id="plan-resources-heading">{t("plans.resources")}</h2>
          {plan.resources.length > 0 ? (
            <ul className="plain-list">
              {plan.resources.map((resource) => (
                <li key={`${resource.kind}-${resource.resource_id}`}>
                  <span className="resource-name">{resource.name ?? resource.resource_id}</span>
                  <span className="part-meta">
                    {resource.kind}
                    {resource.account_id ? ` · ${resource.account_id}` : ""}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="help-text">{t("plans.noResources")}</p>
          )}

          <h3>{t("plans.preconditions")}</h3>
          {plan.preconditions.length > 0 ? (
            <ul className="plain-list">
              {plan.preconditions.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}
            </ul>
          ) : (
            <p className="help-text">{t("plans.noPreconditions")}</p>
          )}

          <details className="details-block">
            <summary>{t("plans.parameters")}</summary>
            <dl className="definition-grid">
              {Object.entries(plan.parameters).map(([key, value]) => (
                <DefinitionItem key={key} label={key} mono>
                  {typeof value === "object" && value !== null ? (
                    <pre className="code-block">{formatJson(value)}</pre>
                  ) : (
                    String(value)
                  )}
                </DefinitionItem>
              ))}
            </dl>
          </details>
        </section>
      </div>
    </section>
  );
}
