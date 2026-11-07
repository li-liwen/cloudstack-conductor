import { Route, Routes } from "react-router-dom";
import { AuthProvider } from "./auth/AuthProvider";
import { RequireAuth, RequireOperator } from "./auth/guards";
import { AppShell } from "./components/AppShell";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { ConfigProvider, useConfig } from "./config/ConfigProvider";
import { I18nProvider } from "./i18n/I18nProvider";
import { AccountPage } from "./pages/AccountPage";
import { ConnectionsPage } from "./pages/ConnectionsPage";
import { ConsolePage } from "./pages/ConsolePage";
import { DocsPage } from "./pages/DocsPage";
import { EstimatesPage } from "./pages/EstimatesPage";
import { LoginPage } from "./pages/LoginPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { OperationDetailPage } from "./pages/OperationDetailPage";
import { OperationsPage } from "./pages/OperationsPage";
import { OperatorPage } from "./pages/OperatorPage";
import { PlanDetailPage } from "./pages/PlanDetailPage";
import { PlansPage } from "./pages/PlansPage";
import { ResourcesPage } from "./pages/ResourcesPage";
import { WorkspacePage } from "./pages/WorkspacePage";

function BootScreen({ productName, logo }: { productName: string; logo: string }) {
  return (
    <div className="boot-screen" role="status" aria-live="polite" aria-busy="true">
      <img className="boot-logo" src={logo} alt="" width={56} height={56} />
      <p className="boot-product">{productName}</p>
      <span className="spinner" aria-hidden="true" />
    </div>
  );
}

function ProtectedShell() {
  return (
    <RequireAuth>
      <AppShell />
    </RequireAuth>
  );
}

function ConfiguredApp() {
  const { config, status } = useConfig();

  if (status === "loading") {
    return (
      <BootScreen
        productName={config.branding.product_name}
        logo={config.branding.logo_mark_url}
      />
    );
  }

  return (
    <I18nProvider
      defaultLocale={config.branding.locale_default}
      availableLocales={config.branding.locales}
    >
      <AuthProvider>
        <ErrorBoundary>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route
              path="/console/:sessionId"
              element={
                <RequireAuth>
                  <ConsolePage />
                </RequireAuth>
              }
            />
            <Route element={<ProtectedShell />}>
              <Route index element={<WorkspacePage />} />
              <Route path="plans" element={<PlansPage />} />
              <Route path="plans/:id" element={<PlanDetailPage />} />
              <Route path="resources" element={<ResourcesPage />} />
              <Route path="operations" element={<OperationsPage />} />
              <Route path="operations/:id" element={<OperationDetailPage />} />
              <Route path="estimates" element={<EstimatesPage />} />
              <Route path="docs" element={<DocsPage />} />
              <Route path="connections" element={<ConnectionsPage />} />
              <Route path="account" element={<AccountPage />} />
              <Route
                path="operator"
                element={
                  <RequireOperator>
                    <OperatorPage />
                  </RequireOperator>
                }
              />
              <Route path="*" element={<NotFoundPage />} />
            </Route>
          </Routes>
        </ErrorBoundary>
      </AuthProvider>
    </I18nProvider>
  );
}

export function App() {
  return (
    <ConfigProvider>
      <ConfiguredApp />
    </ConfigProvider>
  );
}
