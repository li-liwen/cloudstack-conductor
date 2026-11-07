import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/context";
import { Brand } from "../components/Brand";
import { Alert, Spinner } from "../components/Primitives";
import { Icon } from "../components/Icon";
import { LanguageSwitcher } from "../components/LanguageSwitcher";
import { useConfig } from "../config/ConfigProvider";
import { useI18n } from "../i18n/I18nProvider";
import { userErrorMessage } from "../lib/errors";
import { safeSameOriginPath } from "../lib/safe";

function loginDestination(state: unknown): string {
  if (state && typeof state === "object" && "from" in state) {
    const from = (state as { from?: unknown }).from;
    return safeSameOriginPath(from) ?? "/";
  }
  return "/";
}

export function LoginPage() {
  const { config, status: configStatus, reload } = useConfig();
  const authState = useAuth();
  const { t, locale } = useI18n();
  const navigate = useNavigate();
  const location = useLocation();
  const [subject, setSubject] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const params = new URLSearchParams(location.search);
  const expired = params.get("reason") === "expired";
  const from = loginDestination(location.state);
  const branding = config.branding;
  const tagline = locale === "zh-CN" ? branding.tagline_zh : branding.tagline_en;
  const demoMode = config.auth.mode === "demo";
  const loginHref = `${config.auth.login_url}?next=${encodeURIComponent(from)}`;

  if (authState.status === "authenticated") {
    return <Navigate to={from} replace />;
  }

  const handleDemoLogin = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const value = subject.trim();
    if (!value) {
      setError(t("auth.subjectRequired"));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await authState.loginDemo(value);
      navigate(from, { replace: true });
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        setError(t("auth.loginFailed"));
      } else {
        setError(userErrorMessage(caught, t));
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-shell">
      <header className="login-topbar">
        <Brand />
        <div className="login-topbar-actions">
          {branding.environment_label ? (
            <span className="environment-chip">{branding.environment_label}</span>
          ) : null}
          <LanguageSwitcher id="login-locale" />
        </div>
      </header>

      {configStatus === "error" ? (
        <div className="login-config-banner">
          <div className="config-banner" role="status">
            <Icon name="alert" size={16} />
            <p>{t("app.configError")}</p>
            <button type="button" className="btn btn-secondary btn-small" onClick={reload}>
              {t("app.retry")}
            </button>
          </div>
        </div>
      ) : null}

      <main className="login-main">
        <section className="login-brand-panel" aria-labelledby="login-product-name">
          <div className="login-brand-mark" aria-hidden="true">
            <img src={branding.logo_mark_url} alt="" width={64} height={64} />
          </div>
          <p className="login-eyebrow">{branding.company_name}</p>
          <h1 id="login-product-name">{branding.product_name}</h1>
          <p className="login-tagline">{tagline}</p>
          <ul className="login-highlights">
            <li>{t("workspace.emptyBullet1")}</li>
            <li>{t("workspace.emptyBullet3")}</li>
            <li>{t("plans.approvalHelp")}</li>
          </ul>
        </section>

        <section className="login-card" aria-labelledby="login-title">
          <div className="login-card-heading">
            <h2 id="login-title">{t("auth.loginTitle")}</h2>
            <p>{t("auth.loginSubtitle")}</p>
          </div>

          {expired ? (
            <Alert tone="warning">{t("auth.expiredNotice")}</Alert>
          ) : null}
          {authState.sessionError ? (
            <Alert tone="danger">{t("auth.sessionError")}</Alert>
          ) : null}
          {error ? <Alert tone="danger">{error}</Alert> : null}

          {authState.status === "loading" ? (
            <div className="login-loading" role="status" aria-live="polite">
              <Spinner label={t("app.loading")} />
              <span>{t("app.loading")}</span>
            </div>
          ) : demoMode ? (
            <form className="login-form" onSubmit={(event) => void handleDemoLogin(event)}>
              <p className="login-mode-note">{t("auth.demoHelp")}</p>
              <div className="field">
                <label htmlFor="demo-subject">{t("auth.subjectLabel")}</label>
                <input
                  id="demo-subject"
                  name="subject"
                  type="text"
                  value={subject}
                  maxLength={255}
                  autoComplete="username"
                  placeholder={t("auth.subjectPlaceholder")}
                  onChange={(event) => setSubject(event.target.value)}
                  disabled={busy}
                  required
                />
              </div>
              <button
                type="submit"
                className="btn btn-primary btn-block"
                disabled={busy || !subject.trim()}
              >
                {busy ? t("auth.demoSubmitting") : t("auth.demoSubmit")}
              </button>
              <p className="login-mode-chip">
                <Icon name="info" size={15} />
                {t("auth.demoModeNote")}
              </p>
            </form>
          ) : (
            <div className="login-sso">
              <a className="btn btn-primary btn-block" href={loginHref}>
                <Icon name="lock" size={17} />
                {t("auth.ssoButton")}
              </a>
              <p className="login-mode-note">{t("auth.ssoHelp")}</p>
            </div>
          )}

          <p className="login-secure-note">
            <Icon name="lock" size={15} />
            {t("auth.secureNote")}
          </p>
        </section>
      </main>

      <footer className="login-footer">
        <span>{branding.company_name}</span>
        <span aria-hidden="true">·</span>
        <span>{t("auth.secureNote")}</span>
      </footer>
    </div>
  );
}
