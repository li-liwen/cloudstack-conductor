import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth/context";
import { Icon } from "../components/Icon";
import { Alert, DefinitionItem, PageHeader } from "../components/Primitives";
import { useI18n } from "../i18n/I18nProvider";
import { userErrorMessage } from "../lib/errors";
import { formatDateTime } from "../lib/format";
import { roleLabel } from "../lib/status";

function YesNo({ value }: { value: boolean }) {
  const { t } = useI18n();
  return <>{value ? t("app.yes") : t("app.no")}</>;
}

export function AccountPage() {
  const { t, locale } = useI18n();
  const auth = useAuth();
  const navigate = useNavigate();
  const [signingOut, setSigningOut] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const session = auth.session;

  if (!session) return null;
  const principal = session.principal;

  const handleLogout = async () => {
    setSigningOut(true);
    setError(null);
    try {
      const externalLogout = await auth.logout();
      if (externalLogout) {
        window.location.assign(externalLogout);
      } else {
        navigate("/login", { replace: true });
      }
    } catch (caught) {
      setError(userErrorMessage(caught, t));
    } finally {
      setSigningOut(false);
    }
  };

  return (
    <section className="page" aria-labelledby="account-title">
      <PageHeader
        title={t("account.title")}
        description={t("account.subtitle")}
        actions={
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => void handleLogout()}
            disabled={signingOut}
          >
            <Icon name="logout" size={16} />
            {signingOut ? t("account.loggingOut") : t("account.logout")}
          </button>
        }
      />

      {error ? <Alert tone="danger">{error}</Alert> : null}

      <div className="detail-grid">
        <section className="panel" aria-labelledby="account-profile-heading">
          <h2 id="account-profile-heading">{t("account.profile")}</h2>
          <p className="help-text">
            {t("account.signedInAs", {
              name: principal.display_name || principal.username,
            })}
          </p>
          <dl className="definition-grid">
            <DefinitionItem label={t("account.subject")} mono>
              {principal.subject}
            </DefinitionItem>
            <DefinitionItem label={t("account.username")}>{principal.username}</DefinitionItem>
            <DefinitionItem label={t("account.displayName")}>
              {principal.display_name || t("account.notSet")}
            </DefinitionItem>
            <DefinitionItem label={t("account.email")}>
              {principal.email ?? t("account.notSet")}
            </DefinitionItem>
            <DefinitionItem label={t("account.roles")}>
              {principal.roles.map((role) => roleLabel(t, role)).join(", ") || "—"}
            </DefinitionItem>
            <DefinitionItem label={t("account.groups")}>
              {principal.groups.length > 0 ? principal.groups.join(", ") : t("account.notSet")}
            </DefinitionItem>
            <DefinitionItem label={t("account.mappingVersion")}>
              {principal.mapping_version}
            </DefinitionItem>
          </dl>
          <p className="help-text">{t("account.identityNote")}</p>
        </section>

        <section className="panel" aria-labelledby="account-session-heading">
          <h2 id="account-session-heading">{t("account.session")}</h2>
          <dl className="definition-grid">
            <DefinitionItem label={t("account.expiresAt")}>
              {formatDateTime(session.expires_at, locale)}
            </DefinitionItem>
            <DefinitionItem label={t("account.mfa")}>
              {session.mfa ? t("account.mfaEnabled") : t("account.mfaNotEnabled")}
            </DefinitionItem>
            <DefinitionItem label={t("account.amr")}>
              {principal.amr.length > 0 ? principal.amr.join(", ") : t("account.notSet")}
            </DefinitionItem>
            <DefinitionItem label={t("account.sessionActive")}>
              <YesNo value={auth.status === "authenticated"} />
            </DefinitionItem>
          </dl>
          <p className="help-text">{t("account.sessionNote")}</p>
        </section>

        <section className="panel" aria-labelledby="account-cloud-heading">
          <h2 id="account-cloud-heading">{t("account.cloud")}</h2>
          <dl className="definition-grid">
            <DefinitionItem label={t("account.cloud")}>{principal.cloud_id}</DefinitionItem>
            <DefinitionItem label={t("account.account")}>{principal.account}</DefinitionItem>
            <DefinitionItem label={t("account.domain")}>{principal.cloudstack_domain}</DefinitionItem>
            <DefinitionItem label={t("account.cloudstackUser")} mono>
              {principal.cloudstack_user_id}
            </DefinitionItem>
            <DefinitionItem label={t("account.cloudstackAccount")} mono>
              {principal.cloudstack_account_id}
            </DefinitionItem>
            <DefinitionItem label={t("account.projectIds")}>
              {principal.project_ids.length > 0 ? principal.project_ids.join(", ") : t("account.notSet")}
            </DefinitionItem>
            <DefinitionItem label={t("account.delegatedAccounts")}>
              {principal.delegated_account_ids.length > 0
                ? principal.delegated_account_ids.join(", ")
                : t("account.notSet")}
            </DefinitionItem>
          </dl>
          <p className="help-text">{t("account.identityNote")}</p>
        </section>

        <section className="panel" aria-labelledby="account-security-heading">
          <h2 id="account-security-heading">{t("account.security")}</h2>
          <ul className="security-notes">
            <li>{t("account.mfaNote")}</li>
            <li>{t("account.sessionNote")}</li>
            <li>{t("account.approvalNote")}</li>
            <li>{t("account.consoleNote")}</li>
            <li>{t("account.connectionsNote")}</li>
            <li>{t("account.csrfNote")}</li>
          </ul>
          <p className="help-text">{t("account.securityContact")}</p>
        </section>
      </div>
    </section>
  );
}
