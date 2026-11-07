import { useState, type FormEvent } from "react";
import { api } from "../api/endpoints";
import type { OfferingResource, QuoteResponse } from "../api/types";
import { Icon } from "../components/Icon";
import {
  Alert,
  ErrorState,
  LoadingState,
  PageHeader,
} from "../components/Primitives";
import { StatusBadge } from "../components/StatusBadge";
import { useAuth } from "../auth/context";
import { useApiQuery } from "../hooks/useApiQuery";
import { useI18n } from "../i18n/I18nProvider";
import { userErrorMessage } from "../lib/errors";
import { formatDateTime } from "../lib/format";
import { formatAmount, formatMoney, formatRate } from "../lib/money";

const COMPLETENESS_TONE = {
  complete: "success",
  partial: "warning",
  provisional: "warning",
} as const;

function completenessLabel(
  t: ReturnType<typeof useI18n>["t"],
  value: string | undefined,
): string {
  if (value === "partial") return t("estimates.completeness.partial");
  if (value === "provisional") return t("estimates.completeness.provisional");
  return t("estimates.completeness.complete");
}

function offeringShape(offering: OfferingResource | undefined): string {
  if (!offering) return "—";
  const parts = [
    `${offering.cpu_count} vCPU`,
    `${offering.memory_gib} GiB`,
    offering.gpu_count ? `${offering.gpu_count} GPU` : "",
  ].filter(Boolean);
  return parts.join(" / ");
}

function EstimateResult({ result }: { result: QuoteResponse }) {
  const { t, locale } = useI18n();
  const { quote, summary, estimates } = result;
  const monthEstimate =
    locale === "zh-CN" ? estimates.month_zh : estimates.month_en;
  const completeness = monthEstimate.completeness;
  const currency = quote.currency;

  return (
    <section className="panel result-panel" aria-labelledby="estimate-result-heading">
      <div className="result-heading">
        <div>
          <h2 id="estimate-result-heading">{t("estimates.results")}</h2>
          <p className="part-subtitle">
            {quote.offering_name}
            {quote.shape ? ` · ${quote.shape.cpu_count} vCPU / ${quote.shape.memory_gib} GiB` : ""}
          </p>
        </div>
        <StatusBadge state={quote.status} />
      </div>

      <div className="disclaimer-banner" role="note">
        <span className="disclaimer-icon" aria-hidden="true">
          <Icon name="alert" size={20} />
        </span>
        <div>
          <p className="disclaimer-zh">{t("estimates.disclaimer.zh")}</p>
          {locale !== "zh-CN" ? (
            <p className="disclaimer-en">{t("estimates.disclaimer.en")}</p>
          ) : null}
        </div>
      </div>

      <dl className="estimate-tiles">
        <div>
          <dt>{t("estimates.hourly")}</dt>
          <dd>{formatMoney(summary.hourly, currency)}</dd>
        </div>
        <div>
          <dt>{t("estimates.monthly")}</dt>
          <dd>{formatMoney(summary.monthly, currency)}</dd>
          <p>{monthEstimate.period_label}</p>
        </div>
        <div>
          <dt>{t("estimates.halfYear")}</dt>
          <dd>{formatMoney(summary.half_year, currency)}</dd>
          <p>{estimates.half_year_zh.period_label}</p>
        </div>
        <div>
          <dt>{t("estimates.forecast")}</dt>
          <dd>{formatMoney(estimates.forecast_zh.total, currency)}</dd>
          <p>{t("estimates.forecastHelp")}</p>
        </div>
      </dl>

      <div className="estimate-meta">
        <span>
          {t("estimates.completeness")}:{" "}
          <StatusBadge
            label={completenessLabel(t, completeness)}
            tone={COMPLETENESS_TONE[completeness as keyof typeof COMPLETENESS_TONE] ?? "neutral"}
          />
        </span>
        <span>{t("estimates.pricingVersion")}: {quote.pricing_version}</span>
        <span>{t("estimates.rateCard")}: {quote.rate_card_version}</span>
        <span>{t("estimates.generatedAt")}: {formatDateTime(monthEstimate.generated_at, locale)}</span>
      </div>

      {quote.status !== "priced" ? (
        <Alert tone="warning" title={t("estimates.unpricedReasons")}>
          <ul className="plain-list">
            {quote.unpriced_reasons.map((reason, index) => (
              <li key={`${reason}-${index}`}>{reason}</li>
            ))}
          </ul>
        </Alert>
      ) : null}

      <div className="two-column">
        <div>
          <h3>{t("estimates.lineItems")}</h3>
          {quote.line_items.length > 0 ? (
            <div className="table-shell">
              <table className="data-table">
                <caption className="visually-hidden">{t("estimates.lineItems")}</caption>
                <thead>
                  <tr>
                    <th scope="col">{t("estimates.label")}</th>
                    <th scope="col">{t("estimates.quantity")}</th>
                    <th scope="col">{t("estimates.rate")}</th>
                    <th scope="col">{t("estimates.amount")}</th>
                  </tr>
                </thead>
                <tbody>
                  {quote.line_items.map((line, index) => (
                    <tr key={`${line.kind}-${index}`}>
                      <td>
                        {line.label}
                        {line.detail ? <span className="part-meta">{line.detail}</span> : null}
                      </td>
                      <td>{formatAmount(line.quantity, { minFractionDigits: 0, maxFractionDigits: 4 })} {line.unit}</td>
                      <td>{formatRate(line.unit_rate, currency)}</td>
                      <td>{formatMoney(line.amount, currency)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="help-text">{t("app.none")}</p>
          )}
        </div>

        <div>
          <h3>{t("estimates.assumptions")}</h3>
          <ul className="plain-list">
            {monthEstimate.assumptions.map((assumption, index) => (
              <li key={`${assumption}-${index}`}>{assumption}</li>
            ))}
            <li>{t("estimates.monthHelp")}</li>
            <li>{t("estimates.forecastHelp")}</li>
          </ul>
          <p className="part-meta">{t("estimates.syntheticNote")}</p>
        </div>
      </div>
    </section>
  );
}

export function EstimatesPage() {
  const { t } = useI18n();
  const { session } = useAuth();
  const accountId = session?.principal.cloudstack_account_id ?? "";
  const accountLabel = session?.principal.account ?? "";
  const offeringsQuery = useApiQuery(
    (signal) => api.getResources("offering", signal),
    [],
  );
  const [offeringId, setOfferingId] = useState("");
  const [quoting, setQuoting] = useState(false);
  const [result, setResult] = useState<QuoteResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const offerings = offeringsQuery.data?.items.offerings ?? [];
  const selected = offerings.find((offering) => offering.id === offeringId);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!offeringId || quoting) {
      setError(t("estimates.offeringRequired"));
      return;
    }
    setQuoting(true);
    setError(null);
    try {
      const quote = await api.createQuote(offeringId, accountId);
      setResult(quote);
    } catch (caught) {
      setError(userErrorMessage(caught, t));
    } finally {
      setQuoting(false);
    }
  };

  return (
    <section className="page" aria-labelledby="estimates-title">
      <PageHeader title={t("estimates.title")} description={t("estimates.subtitle")} />

      <div className="builder-layout">
        <section className="panel" aria-labelledby="estimate-builder-heading">
          <h2 id="estimate-builder-heading">{t("estimates.builder")}</h2>
          <p className="help-text">{t("estimates.builderHelp")}</p>

          {offeringsQuery.status === "error" ? (
            <ErrorState message={t("estimates.offeringsError")} onRetry={offeringsQuery.reload} />
          ) : offeringsQuery.initialLoading ? (
            <LoadingState />
          ) : (
            <form className="stacked-form" onSubmit={(event) => void submit(event)}>
              <div className="field">
                <label htmlFor="offering-select">{t("estimates.offering")}</label>
                <select
                  id="offering-select"
                  value={offeringId}
                  onChange={(event) => setOfferingId(event.target.value)}
                  disabled={quoting || offerings.length === 0}
                  required
                >
                  <option value="">{t("estimates.selectOffering")}</option>
                  {offerings.map((offering) => (
                    <option key={offering.id} value={offering.id}>
                      {offering.name} — {offeringShape(offering)}
                    </option>
                  ))}
                </select>
                {offerings.length === 0 ? (
                  <p className="help-text">{t("estimates.noOfferings")}</p>
                ) : null}
              </div>

              <div className="field">
                <label htmlFor="quote-account">{t("estimates.account")}</label>
                <input
                  id="quote-account"
                  type="text"
                  value={accountLabel ? `${accountLabel} (${accountId})` : accountId}
                  readOnly
                  aria-describedby="quote-account-help"
                />
                <p id="quote-account-help" className="help-text">
                  {t("estimates.accountHelp")}
                </p>
              </div>

              {selected ? (
                <div className="selected-offering">
                  <p className="part-meta">{t("estimates.selectedOffering")}</p>
                  <p className="resource-name">{selected.name}</p>
                  <p className="part-meta">{offeringShape(selected)}</p>
                </div>
              ) : null}

              {error ? <Alert tone="danger">{error}</Alert> : null}

              <button
                type="submit"
                className="btn btn-primary"
                disabled={!offeringId || quoting || !accountId}
              >
                <Icon name="estimates" size={16} />
                {quoting ? t("estimates.quoting") : t("estimates.quote")}
              </button>
              <p className="part-meta">
                {t("estimates.period")}: {result?.current_period.label ?? "—"}
              </p>
            </form>
          )}
        </section>

        <section className="estimate-note" aria-labelledby="estimate-disclaimer-heading">
          <h2 id="estimate-disclaimer-heading">{t("estimates.disclaimerLabel")}</h2>
          <p className="disclaimer-zh prominent">{t("estimates.disclaimer.zh")}</p>
          <p className="disclaimer-en">{t("estimates.disclaimer.en")}</p>
          <ul className="plain-list">
            <li>{t("estimates.monthHelp")}</li>
            <li>{t("estimates.forecastHelp")}</li>
            <li>{t("estimates.halfYearHelp")}</li>
          </ul>
        </section>
      </div>

      {result ? (
        <EstimateResult result={result} />
      ) : (
        <div className="state-block state-empty">
          <Icon name="estimates" size={22} />
          <p className="state-title">{t("estimates.results")}</p>
          <p className="state-help">{t("estimates.builderHelp")}</p>
        </div>
      )}
    </section>
  );
}
