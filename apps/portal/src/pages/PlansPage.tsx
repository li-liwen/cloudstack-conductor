import { Link } from "react-router-dom";
import { api } from "../api/endpoints";
import type { ActionPlanDto } from "../api/types";
import { Icon } from "../components/Icon";
import { EmptyState, ErrorState, LoadingState, PageHeader } from "../components/Primitives";
import { StatusBadge } from "../components/StatusBadge";
import { useApiQuery } from "../hooks/useApiQuery";
import { useI18n } from "../i18n/I18nProvider";
import { formatDateTime } from "../lib/format";
import { formatMoney } from "../lib/money";

function PlanRow({ plan }: { plan: ActionPlanDto }) {
  const { t, locale } = useI18n();
  return (
    <tr>
      <td className="mono break-all">
        <Link to={`/plans/${encodeURIComponent(plan.plan_id)}`} className="text-link">
          {plan.plan_id}
        </Link>
      </td>
      <td>
        <span className="action-name">{plan.action.replaceAll("_", " ")}</span>
      </td>
      <td>
        <StatusBadge state={plan.state} />
      </td>
      <td>{plan.account_id}</td>
      <td className="nowrap">{formatDateTime(plan.created_at, locale)}</td>
      <td className="nowrap">{formatDateTime(plan.expires_at, locale)}</td>
      <td className="nowrap">
        {plan.quote ? formatMoney(plan.quote.total_hourly, plan.quote.currency) : "—"}
      </td>
      <td>
        <Link
          className="btn btn-secondary btn-small"
          to={`/plans/${encodeURIComponent(plan.plan_id)}`}
          aria-label={`${t("plans.view")}: ${plan.plan_id}`}
        >
          {t("plans.view")}
          <Icon name="chevronRight" size={14} />
        </Link>
      </td>
    </tr>
  );
}

export function PlansPage() {
  const { t } = useI18n();
  const query = useApiQuery((signal) => api.listPlans(signal), []);

  return (
    <section className="page" aria-labelledby="plans-title">
      <PageHeader
        title={t("plans.title")}
        description={t("plans.subtitle")}
        actions={
          <button type="button" className="btn btn-secondary" onClick={query.reload}>
            <Icon name="refresh" size={16} />
            {t("app.refresh")}
          </button>
        }
      />

      {query.initialLoading ? (
        <LoadingState />
      ) : query.status === "error" ? (
        <ErrorState message={t("plans.loadError")} onRetry={query.reload} />
      ) : (query.data?.items.length ?? 0) === 0 ? (
        <EmptyState title={t("plans.empty")}>
          <p>{t("plans.emptyHelp")}</p>
          <Link className="text-link" to="/">
            {t("nav.workspace")}
            <Icon name="chevronRight" size={15} />
          </Link>
        </EmptyState>
      ) : (
        <div className="table-shell">
          <table className="data-table">
            <caption className="visually-hidden">{t("plans.listLabel")}</caption>
            <thead>
              <tr>
                <th scope="col">{t("plans.planId")}</th>
                <th scope="col">{t("plans.action")}</th>
                <th scope="col">{t("plans.state")}</th>
                <th scope="col">{t("plans.account")}</th>
                <th scope="col">{t("plans.created")}</th>
                <th scope="col">{t("plans.expires")}</th>
                <th scope="col">{t("plans.hourly")}</th>
                <th scope="col">{t("app.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {query.data?.items.map((plan) => <PlanRow key={plan.plan_id} plan={plan} />)}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
