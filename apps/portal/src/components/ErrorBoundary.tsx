import { Component, type ReactNode } from "react";
import { useI18n } from "../i18n/I18nProvider";

interface BoundaryProps {
  fallback: ReactNode;
  children: ReactNode;
}

interface BoundaryState {
  failed: boolean;
}

class ErrorBoundaryInner extends Component<BoundaryProps, BoundaryState> {
  state: BoundaryState = { failed: false };

  static getDerivedStateFromError(): BoundaryState {
    return { failed: true };
  }

  componentDidCatch(): void {
    // Intentionally do not render or forward the error text: it may contain
    // internal detail that must never reach the UI.
  }

  render(): ReactNode {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

export function ErrorBoundary({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  const fallback = (
    <main className="fatal-error" role="alert">
      <h1>{t("error.generic")}</h1>
      <button type="button" className="btn btn-primary" onClick={() => window.location.reload()}>
        {t("app.retry")}
      </button>
    </main>
  );
  return <ErrorBoundaryInner fallback={fallback}>{children}</ErrorBoundaryInner>;
}
