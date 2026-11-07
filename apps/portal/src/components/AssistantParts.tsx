import { Link } from "react-router-dom";
import type { MessagePartDto } from "../api/types";
import { Icon } from "../components/Icon";
import { Alert } from "../components/Primitives";
import { StatusBadge } from "../components/StatusBadge";
import { useConfig } from "../config/ConfigProvider";
import { useI18n } from "../i18n/I18nProvider";
import { formatDateTime, formatDuration } from "../lib/format";
import { formatMoney } from "../lib/money";
import { safeDocumentUrl, safeSameOriginPath } from "../lib/safe";

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function money(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function isInstance(value: unknown): value is Record<string, unknown> {
  const item = record(value);
  return Boolean(item && typeof item.id === "string" && typeof item.name === "string");
}

export function QuotePart({ data }: { data: Record<string, unknown> }) {
  const { t, locale } = useI18n();
  const quote = record(data.quote);
  const summary = record(data.summary);
  const currency =
    text(summary?.currency) ?? text(quote?.currency) ?? "";
  const disclaimerDefault = locale === "zh-CN" ? t("estimates.disclaimer.zh") : t("estimates.disclaimer.en");
  const disclaimer =
    (locale === "zh-CN" ? text(data.disclaimer_zh) : null) ?? disclaimerDefault;

  return (
    <section className="part-card quote-card" aria-label={t("workspace.quoteCardTitle")}>
      <header className="part-card-header">
        <span className="part-icon" aria-hidden="true">
          <Icon name="estimates" size={18} />
        </span>
        <div>
          <h3>{t("workspace.quoteCardTitle")}</h3>
          {quote && text(quote.offering_name) ? (
            <p className="part-subtitle">{text(quote.offering_name)}</p>
          ) : null}
        </div>
      </header>

      <dl className="stat-grid">
        <div>
          <dt>{t("workspace.hourly")}</dt>
          <dd>{formatMoney(money(summary?.hourly) ?? money(quote?.total_hourly), currency)}</dd>
        </div>
        <div>
          <dt>{t("workspace.monthly")}</dt>
          <dd>{formatMoney(money(summary?.monthly), currency)}</dd>
        </div>
        <div>
          <dt>{t("workspace.halfYear")}</dt>
          <dd>{formatMoney(money(summary?.half_year), currency)}</dd>
        </div>
      </dl>

      {summary?.partial === true || text(quote?.status) === "unpriced" ? (
        <p className="inline-warning">{t("plans.partialQuote")}</p>
      ) : null}
      <p className="disclaimer">{disclaimer}</p>
    </section>
  );
}

export function PlanPart({ data }: { data: Record<string, unknown> }) {
  const { t, locale } = useI18n();
  const planId = text(data.plan_id);
  const approvalUrl = safeSameOriginPath(data.approval_url);
  const action = text(data.action) ?? t("app.unknown");
  const digest = text(data.digest);
  const currency = text(data.currency) ?? "";
  const hourly = money(data.hourly);
  const expiresAt = text(data.expires_at);
  const effects = Array.isArray(data.effects) ? data.effects : [];
  const approvalHref = planId
    ? `/plans/${encodeURIComponent(planId)}`
    : approvalUrl && approvalUrl.startsWith("/plans/")
      ? approvalUrl
      : null;

  return (
    <section className="part-card plan-card" aria-label={t("workspace.planCardTitle")}>
      <header className="part-card-header">
        <span className="part-icon" aria-hidden="true">
          <Icon name="plans" size={18} />
        </span>
        <div>
          <h3>{t("workspace.planCardTitle")}</h3>
          <p className="part-subtitle">{action.replaceAll("_", " ")}</p>
        </div>
        <StatusBadge state="awaiting_approval" />
      </header>

      <p>{t("workspace.planApprovalHint")}</p>

      <dl className="stat-grid">
        <div>
          <dt>{t("workspace.hourly")}</dt>
          <dd>{formatMoney(hourly, currency)}</dd>
        </div>
        <div>
          <dt>{t("app.expires")}</dt>
          <dd>{formatDateTime(expiresAt, locale)}</dd>
        </div>
      </dl>

      {effects.length > 0 ? (
        <ul className="effect-list">
          {effects.slice(0, 4).map((effect, index) => {
            const item = record(effect);
            const summary = locale === "zh-CN" ? text(item?.summary_zh) : text(item?.summary_en);
            const fallback = locale === "zh-CN" ? text(item?.summary_en) : text(item?.summary_zh);
            return <li key={`${index}-${summary ?? fallback ?? "effect"}`}>{summary ?? fallback ?? "—"}</li>;
          })}
        </ul>
      ) : null}

      {digest ? (
        <p className="part-meta mono break-all">
          {t("plans.digest")}: {digest}
        </p>
      ) : null}

      {approvalHref ? (
        <Link className="btn btn-primary btn-small" to={approvalHref}>
          {t("workspace.openPlan")}
        </Link>
      ) : null}
    </section>
  );
}

function ConsoleResource({ launch }: { launch: Record<string, unknown> }) {
  const { t, locale } = useI18n();
  const { config } = useConfig();
  const url = safeSameOriginPath(launch.launch_url);
  const expiresAt = text(launch.expires_at);
  const vmName = text(launch.vm_name) ?? text(launch.vm_id) ?? "";

  return (
    <section className="part-card resource-card" aria-label={t("workspace.consoleCardTitle")}>
      <header className="part-card-header">
        <span className="part-icon" aria-hidden="true">
          <Icon name="monitor" size={18} />
        </span>
        <div>
          <h3>{t("workspace.consoleCardTitle")}</h3>
          {vmName ? <p className="part-subtitle">{vmName}</p> : null}
        </div>
      </header>
      <p>{t("workspace.consoleReady")}</p>
      {expiresAt ? (
        <p className="part-meta">{t("workspace.consoleLinkExpires", { date: formatDateTime(expiresAt, locale) })}</p>
      ) : null}
      {config.features.console ? (
        url ? (
          <a
            className="btn btn-primary btn-small"
            href={url}
            target="_blank"
            rel="noopener noreferrer"
          >
            <Icon name="monitor" size={16} />
            {t("workspace.openConsole")}
          </a>
        ) : null
      ) : (
        <p className="inline-warning">{t("resources.consoleDisabled")}</p>
      )}
    </section>
  );
}

function InstanceResourceList({ items }: { items: unknown[] }) {
  const { t } = useI18n();
  const instances = items.filter(isInstance);

  return (
    <section className="part-card resource-card" aria-label={t("workspace.resourceCardTitle")}>
      <header className="part-card-header">
        <span className="part-icon" aria-hidden="true">
          <Icon name="resources" size={18} />
        </span>
        <div>
          <h3>{t("workspace.resourceCardTitle")}</h3>
          <p className="part-subtitle">{t("workspace.resourceCount", { count: instances.length })}</p>
        </div>
      </header>
      {instances.length > 0 ? (
        <ul className="resource-list">
          {instances.slice(0, 8).map((instance) => {
            const ips = Array.isArray(instance.ip_addresses)
              ? instance.ip_addresses.filter((ip): ip is string => typeof ip === "string")
              : [];
            return (
              <li key={String(instance.id)}>
                <div>
                  <p className="resource-name">{String(instance.name)}</p>
                  <p className="part-meta">
                    {text(instance.offering_name) ?? "—"}
                    {ips.length ? ` · ${ips.join(", ")}` : ""}
                  </p>
                </div>
                <StatusBadge label={String(instance.state ?? t("app.unknown"))} />
              </li>
            );
          })}
        </ul>
      ) : null}
      <Link className="text-link" to="/resources">
        {t("nav.resources")}
        <Icon name="chevronRight" size={15} />
      </Link>
    </section>
  );
}

export function ResourcePart({ data }: { data: Record<string, unknown> }) {
  if (data.kind === "console") {
    const launch = record(data.launch);
    if (launch) return <ConsoleResource launch={launch} />;
  }
  if (data.kind === "instances" && Array.isArray(data.items)) {
    return <InstanceResourceList items={data.items} />;
  }
  return null;
}

export function ProgressPart({ data }: { data: Record<string, unknown> }) {
  const { t } = useI18n();
  if (Array.isArray(data.missing) && data.missing.length > 0) {
    const fields = data.missing.filter((item): item is string => typeof item === "string");
    return (
      <Alert tone="warning" title={t("workspace.moreInfoNeeded")}>
        {t("workspace.missingInfo", { fields: fields.join(", ") })}
      </Alert>
    );
  }
  if (text(data.error)) {
    return (
      <Alert tone="danger" title={t("workspace.errorEvent")}>
        <span className="mono break-all">{text(data.error)}</span>
      </Alert>
    );
  }
  return null;
}

export function LinkPart({
  data,
  kind,
}: {
  data: Record<string, unknown>;
  kind: "operation" | "approval";
}) {
  const { t } = useI18n();
  const operationId = text(data.operation_id) ?? text(data.operationId);
  const planId = text(data.plan_id) ?? text(data.planId);
  const href = operationId
    ? `/operations/${encodeURIComponent(operationId)}`
    : planId
      ? `/plans/${encodeURIComponent(planId)}`
      : null;

  return (
    <section className="part-card link-card">
      <p>{kind === "operation" ? t("workspace.operationCardTitle") : t("workspace.approvalCardTitle")}</p>
      {href ? (
        <Link className="text-link" to={href}>
          {t("app.view")}
          <Icon name="chevronRight" size={15} />
        </Link>
      ) : null}
    </section>
  );
}

export function CitationList({ citations }: { citations: Array<Record<string, unknown>> }) {
  const { t } = useI18n();
  if (citations.length === 0) return null;
  return (
    <aside className="citations" aria-label={t("workspace.sources")}>
      <h3>
        <Icon name="docs" size={16} />
        {t("workspace.sources")}
      </h3>
      <ol>
        {citations.map((citation, index) => {
          const title = text(citation.title) ?? t("app.unknown");
          const url = safeDocumentUrl(citation.source_url);
          const version = text(citation.version);
          return (
            <li key={`${title}-${index}`}>
              {url ? (
                <a href={url} target="_blank" rel="noopener noreferrer">
                  {title}
                  <Icon name="external" size={13} />
                </a>
              ) : (
                <span>{title}</span>
              )}
              {version ? <span className="citation-version">{t("docs.version")} {version}</span> : null}
            </li>
          );
        })}
      </ol>
    </aside>
  );
}

export function PartRenderer({ part }: { part: MessagePartDto }) {
  const data = record(part.data) ?? {};
  switch (part.type) {
    case "text":
      return text(part.text) ? <p className="message-text">{part.text}</p> : null;
    case "quote":
      return <QuotePart data={data} />;
    case "plan":
      return <PlanPart data={data} />;
    case "resource":
      return <ResourcePart data={data} />;
    case "citation":
      return null; // Citations are consolidated into CitationList.
    case "progress":
      return <ProgressPart data={data} />;
    case "operation":
      return <LinkPart data={data} kind="operation" />;
    case "approval":
      return <LinkPart data={data} kind="approval" />;
    default:
      return text(part.text) ? <p className="message-text">{part.text}</p> : null;
  }
}

export function DurationBadge({ seconds }: { seconds: number | null | undefined }) {
  const { t } = useI18n();
  return (
    <span className="meta-item">
      <Icon name="clock" size={15} />
      {t("plans.downtime")}: {formatDuration(seconds, t)}
    </span>
  );
}

export function effectSummary(
  effect: Record<string, unknown>,
  locale: "zh-CN" | "en",
): string {
  const zh = text(effect.summary_zh);
  const en = text(effect.summary_en);
  return (locale === "en" ? en || zh : zh || en) ?? "—";
}
