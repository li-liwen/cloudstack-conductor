import { useState, type FormEvent } from "react";
import { api, type MappingUpsertInput } from "../api/endpoints";
import type {
  AuditRowDto,
  IdentityMappingDto,
  MigrationRowDto,
  OperatorHealthResponse,
  OperatorPricingResponse,
  OverrideRequest,
  PriceOverrideDto,
  UsageWindowDto,
} from "../api/types";
import { Icon } from "../components/Icon";
import {
  Alert,
  DefinitionItem,
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
} from "../components/Primitives";
import { StatusBadge } from "../components/StatusBadge";
import { Tabs } from "../components/Tabs";
import { useApiQuery } from "../hooks/useApiQuery";
import { useI18n } from "../i18n/I18nProvider";
import { userErrorMessage } from "../lib/errors";
import { formatDateTime, formatJson } from "../lib/format";
import { formatPercent, formatRate } from "../lib/money";
import { roleLabel, statusLabel } from "../lib/status";

type OperatorTab = "mappings" | "pricing" | "migrations" | "health" | "audit";

const MAPPING_STAGES = [
  "unmigrated",
  "legacy_credentials_verified",
  "idp_credentials_ready",
  "required_mfa_ready",
  "cloudstack_saml_authorized",
  "complete",
] as const;

const OPERATOR_ROLES = ["user", "domain_admin", "operator"] as const;

function objectField(value: unknown, key: string): unknown {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return undefined;
  return (value as Record<string, unknown>)[key];
}

function boolField(value: unknown, key: string): boolean | null {
  const item = objectField(value, key);
  return typeof item === "boolean" ? item : null;
}

function stringField(value: unknown, key: string): string | null {
  const item = objectField(value, key);
  return typeof item === "string" ? item : null;
}

function numberField(value: unknown, key: string): number | null {
  const item = objectField(value, key);
  return typeof item === "number" ? item : null;
}

// -- identity mappings ------------------------------------------------------

function MappingForm({ onSaved }: { onSaved: () => void }) {
  const { t } = useI18n();
  const [form, setForm] = useState({
    subject: "",
    cloud_id: "",
    cloudstack_user_id: "",
    cloudstack_account_id: "",
    cloudstack_domain_id: "",
    cloudstack_domain: "",
    cloudstack_username: "",
    account: "",
    display_name: "",
    email: "",
    mapping_version: "1",
    stage: "unmigrated",
  });
  const [roles, setRoles] = useState<string[]>(["user"]);
  const [mfaRequired, setMfaRequired] = useState(false);
  const [legacy2fa, setLegacy2fa] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const setField = (key: keyof typeof form, value: string) => {
    setForm((previous) => ({ ...previous, [key]: value }));
  };

  const toggleRole = (role: string) => {
    setRoles((previous) =>
      previous.includes(role) ? previous.filter((item) => item !== role) : [...previous, role],
    );
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    const required: Array<keyof typeof form> = [
      "subject",
      "cloud_id",
      "cloudstack_user_id",
      "cloudstack_account_id",
      "cloudstack_domain_id",
      "cloudstack_domain",
      "cloudstack_username",
      "account",
    ];
    if (required.some((key) => !form[key].trim()) || roles.length === 0) {
      setError(t("operator.mappings.required"));
      return;
    }
    if (!confirmed) {
      setError(t("operator.mappings.advancedConfirm"));
      return;
    }
    const input: MappingUpsertInput = {
      subject: form.subject.trim(),
      cloud_id: form.cloud_id.trim(),
      cloudstack_user_id: form.cloudstack_user_id.trim(),
      cloudstack_account_id: form.cloudstack_account_id.trim(),
      cloudstack_domain_id: form.cloudstack_domain_id.trim(),
      cloudstack_domain: form.cloudstack_domain.trim(),
      cloudstack_username: form.cloudstack_username.trim(),
      account: form.account.trim(),
      display_name: form.display_name.trim(),
      mapping_version: form.mapping_version.trim() || "1",
      stage: form.stage,
      roles,
      mfa_required: mfaRequired,
      legacy_2fa: legacy2fa,
    };
    if (form.email.trim()) input.email = form.email.trim();

    setBusy(true);
    try {
      await api.upsertMapping(input);
      setConfirmed(false);
      onSaved();
    } catch (caught) {
      setError(userErrorMessage(caught, t));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="stacked-form mapping-form" onSubmit={(event) => void submit(event)}>
      <div className="form-grid">
        <div className="field">
          <label htmlFor="mapping-subject">{t("operator.mappings.subject")}</label>
          <input id="mapping-subject" value={form.subject} onChange={(event) => setField("subject", event.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="mapping-cloud">{t("operator.mappings.cloud")}</label>
          <input id="mapping-cloud" value={form.cloud_id} onChange={(event) => setField("cloud_id", event.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="mapping-cs-user">{t("operator.mappings.cloudstackUser")}</label>
          <input id="mapping-cs-user" value={form.cloudstack_username} onChange={(event) => setField("cloudstack_username", event.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="mapping-cs-user-id">{t("operator.mappings.cloudstackUserId")}</label>
          <input id="mapping-cs-user-id" value={form.cloudstack_user_id} onChange={(event) => setField("cloudstack_user_id", event.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="mapping-cs-account-id">{t("operator.mappings.cloudstackAccountId")}</label>
          <input id="mapping-cs-account-id" value={form.cloudstack_account_id} onChange={(event) => setField("cloudstack_account_id", event.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="mapping-domain">{t("operator.mappings.domain")}</label>
          <input id="mapping-domain" value={form.cloudstack_domain} onChange={(event) => setField("cloudstack_domain", event.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="mapping-domain-id">{t("operator.mappings.domainId")}</label>
          <input id="mapping-domain-id" value={form.cloudstack_domain_id} onChange={(event) => setField("cloudstack_domain_id", event.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="mapping-account">{t("operator.mappings.account")}</label>
          <input id="mapping-account" value={form.account} onChange={(event) => setField("account", event.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="mapping-display-name">{t("account.displayName")}</label>
          <input id="mapping-display-name" value={form.display_name} onChange={(event) => setField("display_name", event.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="mapping-email">{t("account.email")}</label>
          <input id="mapping-email" type="email" value={form.email} onChange={(event) => setField("email", event.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="mapping-version">{t("account.mappingVersion")}</label>
          <input id="mapping-version" value={form.mapping_version} maxLength={32} onChange={(event) => setField("mapping_version", event.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="mapping-stage">{t("operator.mappings.stage")}</label>
          <select id="mapping-stage" value={form.stage} onChange={(event) => setField("stage", event.target.value)}>
            {MAPPING_STAGES.map((stage) => (
              <option key={stage} value={stage}>
                {statusLabel(t, stage)}
              </option>
            ))}
          </select>
        </div>
      </div>

      <fieldset className="fieldset">
        <legend>{t("operator.mappings.roles")}</legend>
        <div className="checkbox-group">
          {OPERATOR_ROLES.map((role) => (
            <label key={role} className="checkbox-row">
              <input
                type="checkbox"
                checked={roles.includes(role)}
                onChange={() => toggleRole(role)}
              />
              <span>{roleLabel(t, role)}</span>
            </label>
          ))}
        </div>
      </fieldset>

      <div className="checkbox-group">
        <label className="checkbox-row">
          <input type="checkbox" checked={mfaRequired} onChange={(event) => setMfaRequired(event.target.checked)} />
          <span>{t("operator.mappings.mfa")}</span>
        </label>
        <label className="checkbox-row">
          <input type="checkbox" checked={legacy2fa} onChange={(event) => setLegacy2fa(event.target.checked)} />
          <span>{t("operator.mappings.legacy2fa")}</span>
        </label>
      </div>

      <label className="checkbox-row">
        <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
        <span>{t("operator.mappings.advancedConfirm")}</span>
      </label>

      {error ? <Alert tone="danger">{error}</Alert> : null}

      <button type="submit" className="btn btn-primary" disabled={busy || !confirmed}>
        <Icon name="save" size={16} />
        {busy ? t("operator.mappings.submitting") : t("operator.mappings.submit")}
      </button>
    </form>
  );
}

function MappingsTab() {
  const { t, locale } = useI18n();
  const query = useApiQuery((signal) => api.listMappings(signal), []);
  const [savedNotice, setSavedNotice] = useState(false);

  return (
    <section className="panel" aria-labelledby="operator-mappings-heading">
      <h2 id="operator-mappings-heading">{t("operator.mappings.title")}</h2>
      <p className="help-text">{t("operator.mappings.subtitle")}</p>
      <p className="help-text">{t("operator.mappings.help")}</p>

      {savedNotice ? <Alert tone="success">{t("operator.mappings.saved")}</Alert> : null}

      {query.initialLoading ? (
        <LoadingState />
      ) : query.status === "error" ? (
        <ErrorState message={t("operator.mappings.error")} onRetry={query.reload} />
      ) : (query.data?.items.length ?? 0) === 0 ? (
        <EmptyState title={t("operator.mappings.empty")} />
      ) : (
        <div className="table-shell">
          <table className="data-table">
            <caption className="visually-hidden">{t("operator.mappings.title")}</caption>
            <thead>
              <tr>
                <th scope="col">{t("operator.mappings.subject")}</th>
                <th scope="col">{t("operator.mappings.cloudstackUser")}</th>
                <th scope="col">{t("operator.mappings.domain")}</th>
                <th scope="col">{t("operator.mappings.account")}</th>
                <th scope="col">{t("operator.mappings.stage")}</th>
                <th scope="col">{t("operator.mappings.roles")}</th>
                <th scope="col">{t("operator.mappings.mfa")}</th>
                <th scope="col">{t("operator.mappings.updated")}</th>
              </tr>
            </thead>
            <tbody>
              {query.data?.items.map((mapping: IdentityMappingDto) => (
                <tr key={mapping.subject}>
                  <td>
                    <span className="mono break-all">{mapping.subject}</span>
                    {mapping.display_name ? <span className="part-meta">{mapping.display_name}</span> : null}
                  </td>
                  <td>
                    {mapping.cloudstack_username}
                    <span className="part-meta mono break-all">{mapping.cloudstack_user_id}</span>
                  </td>
                  <td>{mapping.cloudstack_domain}</td>
                  <td>{mapping.account}</td>
                  <td><StatusBadge state={mapping.stage} /></td>
                  <td>{mapping.roles.map((role) => roleLabel(t, role)).join(", ") || "—"}</td>
                  <td>{mapping.mfa_required ? t("app.yes") : t("app.no")}</td>
                  <td className="nowrap">{formatDateTime(mapping.updated_at, locale)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <details className="details-block">
        <summary>{t("operator.mappings.advancedTitle")}</summary>
        <p className="help-text">{t("operator.mappings.advancedHelp")}</p>
        <MappingForm
          onSaved={() => {
            setSavedNotice(true);
            query.reload();
          }}
        />
      </details>
    </section>
  );
}

// -- pricing ----------------------------------------------------------------

function OverrideForm({ onCreated }: { onCreated: () => void }) {
  const { t } = useI18n();
  const [scope, setScope] = useState<"account" | "domain">("account");
  const [scopeId, setScopeId] = useState("");
  const [discount, setDiscount] = useState("");
  const [effectiveFrom, setEffectiveFrom] = useState("");
  const [effectiveTo, setEffectiveTo] = useState("");
  const [version, setVersion] = useState("1");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState(false);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    setCreated(false);
    if (!scopeId.trim()) {
      setError(t("operator.pricing.invalidScopeId"));
      return;
    }
    if (!/^\d+(\.\d{1,2})?$/.test(discount.trim())) {
      setError(t("operator.pricing.invalidDiscount"));
      return;
    }
    const numericDiscount = Number(discount);
    if (!Number.isFinite(numericDiscount) || numericDiscount < 0 || numericDiscount > 100) {
      setError(t("operator.pricing.invalidDiscount"));
      return;
    }
    let fromIso: string | undefined;
    let toIso: string | undefined;
    if (effectiveFrom) {
      const date = new Date(effectiveFrom);
      if (Number.isNaN(date.getTime())) {
        setError(t("error.invalid"));
        return;
      }
      fromIso = date.toISOString();
    }
    if (effectiveTo) {
      const date = new Date(effectiveTo);
      if (Number.isNaN(date.getTime())) {
        setError(t("error.invalid"));
        return;
      }
      toIso = date.toISOString();
    }
    if (fromIso && toIso && new Date(toIso) <= new Date(fromIso)) {
      setError(t("error.invalid"));
      return;
    }
    const input: OverrideRequest = {
      scope,
      scope_id: scopeId.trim(),
      discount_percent: discount.trim(),
      version: version.trim() || "1",
    };
    if (fromIso) input.effective_from = fromIso;
    if (toIso) input.effective_to = toIso;
    if (note.trim()) input.note = note.trim();

    setBusy(true);
    try {
      await api.createPriceOverride(input);
      setCreated(true);
      setScopeId("");
      setDiscount("");
      setNote("");
      setEffectiveFrom("");
      setEffectiveTo("");
      setVersion("1");
      onCreated();
    } catch (caught) {
      setError(userErrorMessage(caught, t));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="stacked-form" onSubmit={(event) => void submit(event)}>
      <h3>{t("operator.pricing.overrideFormTitle")}</h3>
      <p className="help-text">{t("operator.pricing.overrideFormHelp")}</p>
      <div className="form-grid">
        <div className="field">
          <label htmlFor="override-scope">{t("operator.pricing.scope")}</label>
          <select
            id="override-scope"
            value={scope}
            onChange={(event) => setScope(event.target.value as "account" | "domain")}
          >
            <option value="account">{t("operator.pricing.scopeAccount")}</option>
            <option value="domain">{t("operator.pricing.scopeDomain")}</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor="override-scope-id">{t("operator.pricing.scopeId")}</label>
          <input
            id="override-scope-id"
            value={scopeId}
            onChange={(event) => setScopeId(event.target.value)}
            required
          />
        </div>
        <div className="field">
          <label htmlFor="override-discount">{t("operator.pricing.discountPercent")}</label>
          <input
            id="override-discount"
            type="number"
            min="0"
            max="100"
            step="0.01"
            value={discount}
            onChange={(event) => setDiscount(event.target.value)}
            required
          />
        </div>
        <div className="field">
          <label htmlFor="override-version">{t("operator.pricing.version")}</label>
          <input
            id="override-version"
            value={version}
            maxLength={32}
            onChange={(event) => setVersion(event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="override-from">{t("operator.pricing.effectiveFrom")}</label>
          <input
            id="override-from"
            type="datetime-local"
            value={effectiveFrom}
            onChange={(event) => setEffectiveFrom(event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="override-to">{t("operator.pricing.effectiveTo")}</label>
          <input
            id="override-to"
            type="datetime-local"
            value={effectiveTo}
            onChange={(event) => setEffectiveTo(event.target.value)}
          />
        </div>
      </div>
      <div className="field">
        <label htmlFor="override-note">{t("operator.pricing.note")}</label>
        <input
          id="override-note"
          value={note}
          maxLength={500}
          onChange={(event) => setNote(event.target.value)}
        />
      </div>

      {error ? <Alert tone="danger">{error}</Alert> : null}
      {created ? <Alert tone="success">{t("operator.pricing.overrideCreated")}</Alert> : null}

      <button type="submit" className="btn btn-primary" disabled={busy}>
        <Icon name="plus" size={16} />
        {busy ? t("operator.pricing.creatingOverride") : t("operator.pricing.createOverride")}
      </button>
    </form>
  );
}

function RateCardsTable({ data }: { data: OperatorPricingResponse | null }) {
  const { t, locale } = useI18n();
  const cards = data?.rate_cards ?? [];
  if (cards.length === 0) return <p className="help-text">{t("operator.pricing.noRateCards")}</p>;
  return (
    <div className="table-shell">
      <table className="data-table">
        <caption className="visually-hidden">{t("operator.pricing.rateCards")}</caption>
        <thead>
          <tr>
            <th scope="col">{t("operator.pricing.version")}</th>
            <th scope="col">{t("estimates.currency")}</th>
            <th scope="col">{t("operator.pricing.cpuHour")}</th>
            <th scope="col">{t("operator.pricing.memoryHour")}</th>
            <th scope="col">{t("operator.pricing.gpuHour")}</th>
            <th scope="col">{t("operator.pricing.ssdHour")}</th>
            <th scope="col">{t("operator.pricing.hddHour")}</th>
            <th scope="col">{t("operator.pricing.effectiveFrom")}</th>
          </tr>
        </thead>
        <tbody>
          {cards.map((card) => (
            <tr key={card.version}>
              <td className="mono">{card.version}</td>
              <td>{card.currency}</td>
              <td>{formatRate(card.cpu_hour, card.currency)}</td>
              <td>{formatRate(card.memory_gib_hour, card.currency)}</td>
              <td>{formatRate(card.gpu_hour, card.currency)}</td>
              <td>{formatRate(card.ssd_gib_hour, card.currency)}</td>
              <td>{formatRate(card.hdd_gib_hour, card.currency)}</td>
              <td className="nowrap">{formatDateTime(card.effective_from, locale)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function BindingsTable({ data }: { data: OperatorPricingResponse | null }) {
  const { t, locale } = useI18n();
  const bindings = data?.bindings ?? [];
  if (bindings.length === 0) return <p className="help-text">{t("operator.pricing.noBindings")}</p>;
  return (
    <div className="table-shell">
      <table className="data-table">
        <caption className="visually-hidden">{t("operator.pricing.bindings")}</caption>
        <thead>
          <tr>
            <th scope="col">{t("operator.pricing.offering")}</th>
            <th scope="col">{t("operator.pricing.shape")}</th>
            <th scope="col">{t("operator.pricing.rateCardVersion")}</th>
            <th scope="col">{t("operator.pricing.effectiveFrom")}</th>
            <th scope="col">{t("operator.pricing.effectiveTo")}</th>
          </tr>
        </thead>
        <tbody>
          {bindings.map((binding) => (
            <tr key={`${binding.offering_id}-${binding.rate_card_version}`}>
              <td>
                {binding.offering_name}
                <span className="part-meta mono break-all">{binding.offering_id}</span>
              </td>
              <td>
                {binding.shape.cpu_count} vCPU / {binding.shape.memory_gib} GiB
                {binding.shape.gpu_count ? ` / ${binding.shape.gpu_count} GPU` : ""}
              </td>
              <td>{binding.rate_card_version}</td>
              <td className="nowrap">{formatDateTime(binding.effective_from, locale)}</td>
              <td className="nowrap">{formatDateTime(binding.effective_to, locale)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function OverridesTable({ data }: { data: OperatorPricingResponse | null }) {
  const { t, locale } = useI18n();
  const overrides = data?.overrides ?? [];
  if (overrides.length === 0) return <p className="help-text">{t("operator.pricing.noOverrides")}</p>;
  return (
    <div className="table-shell">
      <table className="data-table">
        <caption className="visually-hidden">{t("operator.pricing.overrides")}</caption>
        <thead>
          <tr>
            <th scope="col">{t("operator.pricing.scope")}</th>
            <th scope="col">{t("operator.pricing.scopeId")}</th>
            <th scope="col">{t("operator.pricing.discount")}</th>
            <th scope="col">{t("operator.pricing.version")}</th>
            <th scope="col">{t("operator.pricing.effectiveFrom")}</th>
            <th scope="col">{t("operator.pricing.effectiveTo")}</th>
          </tr>
        </thead>
        <tbody>
          {overrides.map((override: PriceOverrideDto) => (
            <tr key={override.id}>
              <td>
                {override.scope === "domain"
                  ? t("operator.pricing.scopeDomain")
                  : t("operator.pricing.scopeAccount")}
              </td>
              <td className="mono break-all">{override.scope_id}</td>
              <td>{formatPercent(override.discount_percent)}</td>
              <td className="mono">{override.version}</td>
              <td className="nowrap">{formatDateTime(override.effective_from, locale)}</td>
              <td className="nowrap">{formatDateTime(override.effective_to, locale)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PricingTab() {
  const { t, locale } = useI18n();
  const query = useApiQuery((signal) => api.getOperatorPricing(signal), []);

  return (
    <section className="panel" aria-labelledby="operator-pricing-heading">
      <h2 id="operator-pricing-heading">{t("operator.pricing.title")}</h2>
      <p className="help-text">{t("operator.pricing.subtitle")}</p>
      <p className="help-text">{t("operator.pricing.immutableNote")}</p>

      {query.initialLoading ? (
        <LoadingState />
      ) : query.status === "error" ? (
        <ErrorState message={t("operator.loadError")} onRetry={query.reload} />
      ) : (
        <>
          <h3>{t("operator.pricing.publication")}</h3>
          {query.data?.publication ? (
            <dl className="definition-grid">
              <DefinitionItem label={t("operator.pricing.version")}>
                {query.data.publication.version}
              </DefinitionItem>
              <DefinitionItem label={t("operator.pricing.publishedAt")}>
                {formatDateTime(query.data.publication.published_at, locale)}
              </DefinitionItem>
              <DefinitionItem label={t("operator.pricing.publishedBy")}>
                {query.data.publication.published_by}
              </DefinitionItem>
              <DefinitionItem label={t("operator.pricing.checksum")} mono>
                {query.data.publication.checksum}
              </DefinitionItem>
            </dl>
          ) : (
            <p className="help-text">{t("operator.pricing.noPublication")}</p>
          )}

          <h3>{t("operator.pricing.rateCards")}</h3>
          <RateCardsTable data={query.data} />

          <h3>{t("operator.pricing.bindings")}</h3>
          <BindingsTable data={query.data} />

          <h3>{t("operator.pricing.overrides")}</h3>
          <OverridesTable data={query.data} />

          <div className="divider" />
          <OverrideForm onCreated={() => query.reload()} />
        </>
      )}
    </section>
  );
}

// -- migrations -------------------------------------------------------------

function MigrationsTab() {
  const { t } = useI18n();
  const query = useApiQuery((signal) => api.listMigrations(signal), []);

  return (
    <section className="panel" aria-labelledby="operator-migrations-heading">
      <h2 id="operator-migrations-heading">{t("operator.migrations.title")}</h2>
      <p className="help-text">{t("operator.migrations.subtitle")}</p>
      <p className="help-text">{t("operator.migrations.readOnly")}</p>

      {query.initialLoading ? (
        <LoadingState />
      ) : query.status === "error" ? (
        <ErrorState message={t("operator.migrations.error")} onRetry={query.reload} />
      ) : (query.data?.items.length ?? 0) === 0 ? (
        <EmptyState title={t("operator.migrations.empty")} />
      ) : (
        <div className="table-shell">
          <table className="data-table">
            <caption className="visually-hidden">{t("operator.migrations.title")}</caption>
            <thead>
              <tr>
                <th scope="col">{t("operator.migrations.subject")}</th>
                <th scope="col">{t("operator.migrations.cloudstackUser")}</th>
                <th scope="col">{t("operator.migrations.domain")}</th>
                <th scope="col">{t("operator.migrations.mappingStage")}</th>
                <th scope="col">{t("operator.migrations.passwordStage")}</th>
                <th scope="col">{t("operator.migrations.attempts")}</th>
                <th scope="col">{t("operator.migrations.mfa")}</th>
                <th scope="col">{t("operator.migrations.legacy2fa")}</th>
              </tr>
            </thead>
            <tbody>
              {query.data?.items.map((row: MigrationRowDto) => (
                <tr key={row.subject}>
                  <td className="mono break-all">{row.subject}</td>
                  <td>{row.cloudstack_username}</td>
                  <td>{row.domain}</td>
                  <td><StatusBadge state={row.mapping_stage} /></td>
                  <td>{row.password_stage}</td>
                  <td>{row.attempts}</td>
                  <td>{row.mfa_required ? t("app.yes") : t("app.no")}</td>
                  <td>{row.legacy_2fa ? t("app.yes") : t("app.no")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// -- health -----------------------------------------------------------------

function UsageWindows({ windows }: { windows: UsageWindowDto[] }) {
  const { t, locale } = useI18n();
  if (windows.length === 0) return <p className="help-text">{t("operator.health.noUsageWindows")}</p>;
  return (
    <div className="table-shell">
      <table className="data-table">
        <caption className="visually-hidden">{t("operator.health.usageWindows")}</caption>
        <thead>
          <tr>
            <th scope="col">{t("operator.health.window")}</th>
            <th scope="col">{t("operator.audit.when")}</th>
            <th scope="col">{t("app.status")}</th>
            <th scope="col">{t("operator.health.recordsSeen")}</th>
            <th scope="col">{t("operator.health.recordsStored")}</th>
            <th scope="col">{t("operator.health.duplicates")}</th>
          </tr>
        </thead>
        <tbody>
          {windows.map((window) => (
            <tr key={window.window_id}>
              <td className="mono break-all">{window.window_id}</td>
              <td className="nowrap">
                {formatDateTime(window.started_at, locale)} → {formatDateTime(window.ended_at, locale)}
              </td>
              <td>{window.status}</td>
              <td>{window.records_seen}</td>
              <td>{window.records_stored}</td>
              <td>{window.duplicates}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Counts({ health }: { health: OperatorHealthResponse }) {
  const { t } = useI18n();
  const labels: Record<string, string> = {
    identity_mappings: t("operator.health.identityMappings"),
    plans: t("operator.health.plans"),
    operations: t("operator.health.operations"),
    knowledge_documents: t("operator.health.knowledgeDocuments"),
    knowledge_chunks: t("operator.health.knowledgeChunks"),
  };
  return (
    <dl className="definition-grid">
      {Object.entries(health.counts).map(([key, value]) => (
        <DefinitionItem key={key} label={labels[key] ?? key}>
          {value}
        </DefinitionItem>
      ))}
    </dl>
  );
}

function HealthTab() {
  const { t, locale } = useI18n();
  const query = useApiQuery((signal) => api.getOperatorHealth(signal), []);

  if (query.initialLoading) {
    return (
      <section className="panel" aria-labelledby="operator-health-heading">
        <h2 id="operator-health-heading">{t("operator.health.title")}</h2>
        <LoadingState />
      </section>
    );
  }
  if (query.status === "error" || !query.data) {
    return (
      <section className="panel" aria-labelledby="operator-health-heading">
        <h2 id="operator-health-heading">{t("operator.health.title")}</h2>
        <ErrorState message={t("operator.health.error")} onRetry={query.reload} />
      </section>
    );
  }
  const health = query.data;
  const execution = health.execution;
  const reachable = boolField(execution, "reachable");
  const version = stringField(execution, "cloudstack_version");
  const zones = numberField(execution, "zones");
  const securityGroups = boolField(execution, "security_groups_enabled");
  const errorCode = stringField(execution, "code");

  return (
    <section className="panel" aria-labelledby="operator-health-heading">
      <h2 id="operator-health-heading">{t("operator.health.title")}</h2>
      <p className="help-text">{t("operator.health.subtitle")}</p>

      <div className="health-grid">
        <div className="health-card">
          <h3>{t("operator.health.database")}</h3>
          <StatusBadge
            label={health.database ? t("operator.health.reachable") : t("operator.health.unreachable")}
            tone={health.database ? "success" : "danger"}
          />
        </div>
        <div className="health-card">
          <h3>{t("operator.health.execution")}</h3>
          <StatusBadge
            label={reachable ? t("operator.health.reachable") : t("operator.health.unreachable")}
            tone={reachable ? "success" : "danger"}
          />
          {!reachable && errorCode ? (
            <p className="part-meta mono break-all">{errorCode}</p>
          ) : null}
        </div>
      </div>

      {reachable ? (
        <dl className="definition-grid">
          <DefinitionItem label={t("operator.health.cloudstackVersion")}>
            {version ?? "—"}
          </DefinitionItem>
          <DefinitionItem label={t("operator.health.zones")}>
            {zones ?? "—"}
          </DefinitionItem>
          <DefinitionItem label={t("operator.health.securityGroups")}>
            {securityGroups === null ? "—" : securityGroups ? t("app.yes") : t("app.no")}
          </DefinitionItem>
        </dl>
      ) : null}

      <h3>{t("operator.health.counts")}</h3>
      <Counts health={health} />

      <h3>{t("operator.health.usageWindows")}</h3>
      <UsageWindows windows={health.usage_windows} />

      <h3>{t("operator.health.recentAudit")}</h3>
      {health.recent_audit.length === 0 ? (
        <p className="help-text">{t("operator.health.noAudit")}</p>
      ) : (
        <div className="table-shell">
          <table className="data-table">
            <caption className="visually-hidden">{t("operator.health.recentAudit")}</caption>
            <thead>
              <tr>
                <th scope="col">{t("operator.audit.when")}</th>
                <th scope="col">{t("operator.audit.actor")}</th>
                <th scope="col">{t("operator.audit.action")}</th>
                <th scope="col">{t("operator.audit.outcome")}</th>
              </tr>
            </thead>
            <tbody>
              {health.recent_audit.map((entry, index) => (
                <tr key={`${entry.occurred_at}-${index}`}>
                  <td className="nowrap">{formatDateTime(entry.occurred_at, locale)}</td>
                  <td className="mono break-all">{entry.actor}</td>
                  <td>{entry.action}</td>
                  <td>{entry.outcome}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// -- audit ------------------------------------------------------------------

function AuditTab() {
  const { t, locale } = useI18n();
  const query = useApiQuery((signal) => api.listAudit(signal), []);

  return (
    <section className="panel" aria-labelledby="operator-audit-heading">
      <h2 id="operator-audit-heading">{t("operator.audit.title")}</h2>
      <p className="help-text">{t("operator.audit.subtitle")}</p>

      {query.initialLoading ? (
        <LoadingState />
      ) : query.status === "error" ? (
        <ErrorState message={t("operator.audit.error")} onRetry={query.reload} />
      ) : (query.data?.items.length ?? 0) === 0 ? (
        <EmptyState title={t("operator.audit.empty")} />
      ) : (
        <div className="table-shell">
          <table className="data-table">
            <caption className="visually-hidden">{t("operator.audit.title")}</caption>
            <thead>
              <tr>
                <th scope="col">{t("operator.audit.when")}</th>
                <th scope="col">{t("operator.audit.actor")}</th>
                <th scope="col">{t("operator.audit.action")}</th>
                <th scope="col">{t("operator.audit.target")}</th>
                <th scope="col">{t("operator.audit.outcome")}</th>
                <th scope="col">{t("operator.audit.detail")}</th>
              </tr>
            </thead>
            <tbody>
              {query.data?.items.map((row: AuditRowDto) => (
                <tr key={row.id}>
                  <td className="nowrap">{formatDateTime(row.occurred_at, locale)}</td>
                  <td className="mono break-all">{row.actor}</td>
                  <td>{row.action}</td>
                  <td>
                    <span className="mono break-all">
                      {row.target_type}:{row.target_id}
                    </span>
                  </td>
                  <td>{row.outcome}</td>
                  <td>
                    {row.detail === null || row.detail === undefined ? (
                      <span className="help-text">{t("operator.audit.noDetail")}</span>
                    ) : (
                      <details className="details-block">
                        <summary>{t("operator.audit.detail")}</summary>
                        <pre className="code-block">{formatJson(row.detail)}</pre>
                      </details>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// -- page -------------------------------------------------------------------

export function OperatorPage() {
  const { t } = useI18n();
  const [tab, setTab] = useState<OperatorTab>("mappings");
  const [refreshNonce, setRefreshNonce] = useState(0);

  const tabs = [
    { id: "mappings", label: t("operator.tab.mappings") },
    { id: "pricing", label: t("operator.tab.pricing") },
    { id: "migrations", label: t("operator.tab.migrations") },
    { id: "health", label: t("operator.tab.health") },
    { id: "audit", label: t("operator.tab.audit") },
  ];

  return (
    <section className="page" aria-labelledby="operator-title">
      <PageHeader
        title={t("operator.title")}
        description={t("operator.subtitle")}
        actions={
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => setRefreshNonce((value) => value + 1)}
            title={t("operator.refresh")}
          >
            <Icon name="refresh" size={16} />
            {t("app.refresh")}
          </button>
        }
      />

      <Alert tone="info">{t("operator.readOnlyNotice")}</Alert>

      <Tabs items={tabs} active={tab} onChange={(id) => setTab(id as OperatorTab)} label={t("operator.tabs.label")} />

      <div
        key={`${tab}-${refreshNonce}`}
        id={`panel-${tab}`}
        role="tabpanel"
        aria-labelledby={`tab-${tab}`}
        tabIndex={0}
        className="tab-panel"
      >
        {tab === "mappings" ? <MappingsTab /> : null}
        {tab === "pricing" ? <PricingTab /> : null}
        {tab === "migrations" ? <MigrationsTab /> : null}
        {tab === "health" ? <HealthTab /> : null}
        {tab === "audit" ? <AuditTab /> : null}
      </div>
    </section>
  );
}
