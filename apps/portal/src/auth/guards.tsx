import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useI18n } from "../i18n/I18nProvider";
import { isOperator, useAuth } from "./context";

function RouteLoading() {
  const { t } = useI18n();
  return (
    <div className="route-loading" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <span>{t("app.loading")}</span>
    </div>
  );
}

export function RequireAuth({ children }: { children: ReactNode }) {
  const auth = useAuth();
  const location = useLocation();

  if (auth.status === "loading") return <RouteLoading />;
  if (!auth.session) {
    const from = `${location.pathname}${location.search}`;
    return <Navigate to="/login" replace state={{ from }} />;
  }
  return <>{children}</>;
}

export function RequireOperator({ children }: { children: ReactNode }) {
  const auth = useAuth();
  const { t } = useI18n();
  if (!isOperator(auth.session)) {
    return (
      <section className="page" aria-labelledby="operator-denied-title">
        <div className="alert alert-danger" role="alert">
          <h2 id="operator-denied-title">{t("operator.title")}</h2>
          <p>{t("operator.accessDenied")}</p>
        </div>
      </section>
    );
  }
  return <>{children}</>;
}
