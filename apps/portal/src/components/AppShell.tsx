import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useAuth, isOperator } from "../auth/context";
import { useConfig } from "../config/ConfigProvider";
import { useI18n } from "../i18n/I18nProvider";
import type { MessageKey } from "../i18n/messages";
import { userErrorMessage } from "../lib/errors";
import { roleLabel } from "../lib/status";
import { Brand } from "./Brand";
import { Icon, type IconName } from "./Icon";
import { LanguageSwitcher } from "./LanguageSwitcher";

interface NavItem {
  to: string;
  end?: boolean;
  label: MessageKey;
  icon: IconName;
}

const PRIMARY_NAV: NavItem[] = [
  { to: "/", end: true, label: "nav.workspace", icon: "workspace" },
  { to: "/plans", label: "nav.plans", icon: "plans" },
  { to: "/resources", label: "nav.resources", icon: "resources" },
  { to: "/operations", label: "nav.operations", icon: "operations" },
  { to: "/estimates", label: "nav.estimates", icon: "estimates" },
  { to: "/docs", label: "nav.docs", icon: "docs" },
  { to: "/connections", label: "nav.connections", icon: "connections" },
  { to: "/account", label: "nav.account", icon: "account" },
];

export function AppShell() {
  const { config, status: configStatus, reload } = useConfig();
  const auth = useAuth();
  const { t } = useI18n();
  const navigate = useNavigate();
  const location = useLocation();
  const [navOpen, setNavOpen] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  const [signOutError, setSignOutError] = useState<string | null>(null);

  const session = auth.session;
  const operator = isOperator(session);
  const principal = session?.principal;
  const displayName = principal?.display_name || principal?.username || "";
  const environmentLabel = config.branding.environment_label ?? config.environment;

  useEffect(() => {
    setNavOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    if (!navOpen) return undefined;
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setNavOpen(false);
    };
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [navOpen]);

  const handleSignOut = async () => {
    setSigningOut(true);
    setSignOutError(null);
    try {
      await auth.logout();
      navigate("/login", { replace: true });
    } catch (error) {
      setSignOutError(userErrorMessage(error, t));
    } finally {
      setSigningOut(false);
    }
  };

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        {t("app.skipToContent")}
      </a>

      <header className="app-header">
        <button
          type="button"
          className="icon-button nav-toggle"
          aria-expanded={navOpen}
          aria-controls="app-sidebar"
          aria-label={navOpen ? t("nav.closeMenu") : t("nav.openMenu")}
          onClick={() => setNavOpen((value) => !value)}
        >
          <Icon name={navOpen ? "close" : "menu"} size={22} />
        </button>

        <Link to="/" className="brand-link" aria-label={t("nav.brandHome")}>
          <Brand />
        </Link>

        <div className="header-actions">
          <span className="environment-chip" title={t("app.environment")}>
            <span className="visually-hidden">{t("app.environment")}: </span>
            {environmentLabel}
          </span>
          <LanguageSwitcher id="header-locale" />
          <button
            type="button"
            className="btn btn-ghost sign-out-button"
            onClick={() => void handleSignOut()}
            disabled={signingOut}
            aria-label={signingOut ? t("account.loggingOut") : t("app.signOut")}
          >
            <Icon name="logout" size={17} />
            <span>{signingOut ? t("account.loggingOut") : t("app.signOut")}</span>
          </button>
        </div>
      </header>

      {configStatus === "error" ? (
        <div className="config-banner" role="status">
          <Icon name="alert" size={16} />
          <p>{t("app.configError")}</p>
          <button type="button" className="btn btn-secondary btn-small" onClick={reload}>
            {t("app.retry")}
          </button>
        </div>
      ) : null}

      {signOutError ? (
        <div className="config-banner config-banner-danger" role="alert">
          <Icon name="alert" size={16} />
          <p>{signOutError}</p>
        </div>
      ) : null}

      <div className="app-body">
        <aside
          id="app-sidebar"
          className={`app-sidebar${navOpen ? " is-open" : ""}`}
          aria-label={t("nav.primary")}
        >
          <nav className="sidebar-nav" aria-label={t("nav.primary")}>
            <ul>
              {PRIMARY_NAV.map((item) => (
                <li key={item.to}>
                  <NavLink
                    to={item.to}
                    end={item.end}
                    className={({ isActive }) => `nav-link${isActive ? " is-active" : ""}`}
                  >
                    <Icon name={item.icon} size={19} />
                    <span>{t(item.label)}</span>
                  </NavLink>
                </li>
              ))}
              {operator ? (
                <li>
                  <NavLink
                    to="/operator"
                    className={({ isActive }) => `nav-link${isActive ? " is-active" : ""}`}
                  >
                    <Icon name="operator" size={19} />
                    <span>{t("nav.operator")}</span>
                  </NavLink>
                </li>
              ) : null}
            </ul>
          </nav>

          {principal ? (
            <div className="sidebar-account">
              <p className="sidebar-account-name">{displayName}</p>
              <p className="sidebar-account-meta">
                {principal.cloudstack_username || principal.username}
                {" · "}
                {principal.roles.map((role) => roleLabel(t, role)).join(", ")}
              </p>
              <p className="sidebar-account-meta">
                {principal.account}
                {principal.cloudstack_domain ? ` · ${principal.cloudstack_domain}` : ""}
              </p>
            </div>
          ) : null}
        </aside>

        {navOpen ? (
          <button
            type="button"
            className="sidebar-backdrop"
            aria-label={t("nav.closeMenu")}
            onClick={() => setNavOpen(false)}
          />
        ) : null}

        <main id="main-content" className="app-main" tabIndex={-1}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
