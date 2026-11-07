import { Link } from "react-router-dom";
import { api } from "../api/endpoints";
import type { OperationDto } from "../api/types";
import { Icon } from "../components/Icon";
import { EmptyState, ErrorState, LoadingState, PageHeader } from "../components/Primitives";
import { StatusBadge } from "../components/StatusBadge";
import { useApiQuery } from "../hooks/useApiQuery";
import { useI18n } from "../i18n/I18nProvider";
import { formatDateTime } from "../lib/format";

function OperationRow({ operation }: { operation: OperationDto }) {
  const { t, locale } = useI18n();
  return (
    <tr>
      <td className="mono break-all">
        <Link to={`/operations/${encodeURIComponent(operation.operation_id)}`} className="text-link">
          {operation.operation_id}
        </Link>
      </td>
      <td className="action-name">{operation.action.replaceAll("_", " ")}</td>
      <td><StatusBadge state={operation.state} /></td>
      <td>{operation.account_id}</td>
      <td className="nowrap">{formatDateTime(operation.created_at, locale)}</td>
      <td className="nowrap">{formatDateTime(operation.updated_at, locale)}</td>
      <td>
        <Link
          className="btn btn-secondary btn-small"
          to={`/operations/${encodeURIComponent(operation.operation_id)}`}
          aria-label={`${t("operations.view")}: ${operation.operation_id}`}
        >
          {t("operations.view")}
          <Icon name="chevronRight" size={14} />
        </Link>
      </td>
    </tr>
  );
}

export function OperationsPage() {
  const { t } = useI18n();
  const query = useApiQuery((signal) => api.listOperations(signal), []);

  return (
    <section className="page" aria-labelledby="operations-title">
      <PageHeader
        title={t("operations.title")}
        description={t("operations.subtitle")}
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
        <ErrorState message={t("operations.loadError")} onRetry={query.reload} />
      ) : (query.data?.items.length ?? 0) === 0 ? (
        <EmptyState title={t("operations.empty")} />
      ) : (
        <div className="table-shell">
          <table className="data-table">
            <caption className="visually-hidden">{t("operations.listLabel")}</caption>
            <thead>
              <tr>
                <th scope="col">{t("operations.id")}</th>
                <th scope="col">{t("operations.action")}</th>
                <th scope="col">{t("operations.state")}</th>
                <th scope="col">{t("operations.account")}</th>
                <th scope="col">{t("operations.created")}</th>
                <th scope="col">{t("operations.updated")}</th>
                <th scope="col">{t("app.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {query.data?.items.map((operation) => (
                <OperationRow key={operation.operation_id} operation={operation} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
