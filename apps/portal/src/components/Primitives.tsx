import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useI18n } from "../i18n/I18nProvider";
import { Icon } from "./Icon";

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="spinner-wrap">
      <span className="spinner" aria-hidden="true" />
      {label ? <span className="visually-hidden">{label}</span> : null}
    </span>
  );
}

export function LoadingState({ label }: { label?: string }) {
  const { t } = useI18n();
  const text = label ?? t("app.loading");
  return (
    <div className="state-block" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <p>{text}</p>
    </div>
  );
}

export function ErrorState({
  message,
  onRetry,
  retryLabel,
}: {
  message: string;
  onRetry?: () => void;
  retryLabel?: string;
}) {
  const { t } = useI18n();
  return (
    <div className="state-block state-error" role="alert">
      <Icon name="alert" size={22} />
      <p>{message}</p>
      {onRetry ? (
        <button type="button" className="btn btn-secondary" onClick={onRetry}>
          <Icon name="refresh" size={16} />
          {retryLabel ?? t("app.retry")}
        </button>
      ) : null}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="state-block state-empty">
      <Icon name="info" size={22} />
      <p className="state-title">{title}</p>
      {children ? <div className="state-help">{children}</div> : null}
    </div>
  );
}

export type AlertTone = "info" | "success" | "warning" | "danger";

export function Alert({
  tone = "info",
  title,
  children,
  onDismiss,
}: {
  tone?: AlertTone;
  title?: string;
  children: ReactNode;
  onDismiss?: () => void;
}) {
  const { t } = useI18n();
  return (
    <div className={`alert alert-${tone}`} role={tone === "danger" ? "alert" : "status"}>
      <div className="alert-icon" aria-hidden="true">
        <Icon name={tone === "success" ? "check" : tone === "info" ? "info" : "alert"} size={18} />
      </div>
      <div className="alert-body">
        {title ? <p className="alert-title">{title}</p> : null}
        <div className="alert-text">{children}</div>
      </div>
      {onDismiss ? (
        <button
          type="button"
          className="icon-button alert-dismiss"
          aria-label={t("app.dismiss")}
          onClick={onDismiss}
        >
          <Icon name="close" size={16} />
        </button>
      ) : null}
    </div>
  );
}

export function PageHeader({
  title,
  description,
  actions,
  backTo,
  backLabel,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
  backTo?: string;
  backLabel?: string;
}) {
  const { t } = useI18n();
  return (
    <header className="page-header">
      {backTo ? (
        <Link className="back-link" to={backTo}>
          <Icon name="arrowLeft" size={16} />
          {backLabel ?? t("app.back")}
        </Link>
      ) : null}
      <div className="page-header-row">
        <div className="page-header-copy">
          <h1>{title}</h1>
          {description ? <p className="page-description">{description}</p> : null}
        </div>
        {actions ? <div className="page-actions">{actions}</div> : null}
      </div>
    </header>
  );
}

export function DefinitionItem({
  label,
  children,
  mono = false,
}: {
  label: string;
  children: ReactNode;
  mono?: boolean;
}) {
  return (
    <div className="definition-item">
      <dt>{label}</dt>
      <dd className={mono ? "mono break-all" : undefined}>{children}</dd>
    </div>
  );
}
