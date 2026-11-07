import { useState } from "react";
import { api } from "../api/endpoints";
import type { ConnectionItemDto, MCPAuditEntryDto } from "../api/types";
import { ConfirmButton } from "../components/Actions";
import { Icon } from "../components/Icon";
import {
  Alert,
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
} from "../components/Primitives";
import { StatusBadge } from "../components/StatusBadge";
import { useConfig } from "../config/ConfigProvider";
import { useApiQuery } from "../hooks/useApiQuery";
import { useI18n } from "../i18n/I18nProvider";
import { formatDateTime } from "../lib/format";
import { scopeLabel } from "../lib/status";

function AuditList({ entries }: { entries: MCPAuditEntryDto[] }) {
  const { t, locale } = useI18n();
  if (entries.length === 0) return <p className="help-text">{t("connections.noAudit")}</p>;
  return (
    <div className="table-shell">
      <table className="data-table">
        <caption className="visually-hidden">{t("connections.audit")}</caption>
        <thead>
          <tr>
            <th scope="col">{t("operator.audit.when")}</th>
            <th scope="col">{t("connections.event")}</th>
            <th scope="col">{t("connections.tool")}</th>
            <th scope="col">{t("connections.outcome")}</th>
            <th scope="col">{t("operator.audit.detail")}</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry) => (
            <tr key={entry.entry_id}>
              <td className="nowrap">{formatDateTime(entry.occurred_at, locale)}</td>
              <td>{entry.event}</td>
              <td>{entry.tool_name ?? "—"}</td>
              <td>{entry.outcome ?? "—"}</td>
              <td>{entry.detail ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ConnectionCard({ item }: { item: ConnectionItemDto }) {
  const { t, locale } = useI18n();
  const { connection, audit } = item;
  const [revoked, setRevoked] = useState(false);
  const expired = connection.state === "active" && Date.parse(connection.expires_at) <= Date.now();
  const state = revoked ? "revoked" : expired ? "expired" : connection.state;

  return (
    <article className="panel connection-card" aria-labelledby={`connection-${connection.connection_id}`}>
      <div className="result-heading">
        <div>
          <h2 id={`connection-${connection.connection_id}`}>{connection.client_name}</h2>
          <p className="part-meta mono break-all">{connection.client_id}</p>
        </div>
        <StatusBadge state={state} />
      </div>

      <dl className="definition-grid">
        <div className="definition-item">
          <dt>{t("connections.connectionId")}</dt>
          <dd className="mono break-all">{connection.connection_id}</dd>
        </div>
        <div className="definition-item">
          <dt>{t("app.created")}</dt>
          <dd>{formatDateTime(connection.created_at, locale)}</dd>
        </div>
        <div className="definition-item">
          <dt>{t("connections.lastUsed")}</dt>
          <dd>{connection.last_used_at ? formatDateTime(connection.last_used_at, locale) : t("app.never")}</dd>
        </div>
        <div className="definition-item">
          <dt>{t("connections.expires")}</dt>
          <dd>{formatDateTime(connection.expires_at, locale)}</dd>
        </div>
        {connection.revoked_at ? (
          <div className="definition-item">
            <dt>{t("connections.revokedAt")}</dt>
            <dd>{formatDateTime(connection.revoked_at, locale)}</dd>
          </div>
        ) : null}
        {connection.revoked_reason ? (
          <div className="definition-item">
            <dt>{t("connections.revokedReason")}</dt>
            <dd>{connection.revoked_reason}</dd>
          </div>
        ) : null}
      </dl>

      <h3>{t("connections.scopes")}</h3>
      {connection.scopes.length > 0 ? (
        <ul className="scope-list" aria-label={t("connections.scopes")}>
          {connection.scopes.map((scope) => (
            <li key={scope} className="scope-chip">{scopeLabel(t, scope)}</li>
          ))}
        </ul>
      ) : (
        <p className="help-text">{t("connections.noScopes")}</p>
      )}

      <p className="help-text">{t("connections.expiresHelp")}</p>

      {connection.state === "active" ? (
        <div className="connection-actions">
          {revoked ? (
            <Alert tone="success">{t("status.revoked")}</Alert>
          ) : (
            <ConfirmButton
              label={
                <>
                  <Icon name="close" size={16} />
                  {t("connections.revoke")}
                </>
              }
              message={t("connections.revokeConfirm")}
              confirmLabel={t("connections.revoke")}
              busyLabel={t("connections.revoking")}
              onConfirm={async () => {
                await api.revokeConnection(connection.connection_id);
                setRevoked(true);
              }}
            />
          )}
          <p className="help-text">{t("connections.revokeHelp")}</p>
        </div>
      ) : null}

      <details className="details-block" aria-label={t("connections.audit.sectionLabel", { name: connection.client_name })}>
        <summary>
          {t("connections.audit")}
          <span className="part-meta"> {audit.length}</span>
        </summary>
        <AuditList entries={audit} />
      </details>
    </article>
  );
}

export function ConnectionsPage() {
  const { t } = useI18n();
  const { config } = useConfig();
  const query = useApiQuery((signal) => api.listConnections(signal), []);

  return (
    <section className="page" aria-labelledby="connections-title">
      <PageHeader
        title={t("connections.title")}
        description={t("connections.subtitle")}
        actions={
          <button type="button" className="btn btn-secondary" onClick={query.reload}>
            <Icon name="refresh" size={16} />
            {t("app.refresh")}
          </button>
        }
      />

      {!config.features.mcp ? (
        <Alert tone="warning">{t("connections.disabledNotice")}</Alert>
      ) : null}

      {query.initialLoading ? (
        <LoadingState />
      ) : query.status === "error" ? (
        <ErrorState message={t("connections.loadError")} onRetry={query.reload} />
      ) : (query.data?.items.length ?? 0) === 0 ? (
        <EmptyState title={t("connections.empty")} />
      ) : (
        <div className="card-stack">
          {query.data?.items.map((item) => (
            <ConnectionCard key={item.connection.connection_id} item={item} />
          ))}
        </div>
      )}
    </section>
  );
}
