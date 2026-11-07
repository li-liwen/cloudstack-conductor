import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import type { AuthContextValue } from "../auth/context";
import { AuthContext } from "../auth/context";
import { RequireAuth } from "../auth/guards";
import { I18nProvider } from "../i18n/I18nProvider";

const anonymous: AuthContextValue = {
  status: "anonymous",
  session: null,
  sessionError: false,
  refresh: async () => undefined,
  loginDemo: async () => undefined,
  logout: async () => null,
};

function renderProtectedRoute(auth: AuthContextValue) {
  return render(
    <MemoryRouter initialEntries={["/plans"]}>
      <I18nProvider>
        <AuthContext.Provider value={auth}>
          <Routes>
          <Route path="/login" element={<h1>Login screen</h1>} />
          <Route
            path="/plans"
            element={
              <RequireAuth>
                <h1>Plans screen</h1>
              </RequireAuth>
            }
          />
          </Routes>
        </AuthContext.Provider>
      </I18nProvider>
    </MemoryRouter>,
  );
}

describe("protected routes", () => {
  it("redirects an anonymous visitor to the login route", async () => {
    renderProtectedRoute(anonymous);
    expect(
      await screen.findByRole("heading", { name: "Login screen" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Plans screen" })).not.toBeInTheDocument();
  });

  it("renders the protected content for an authenticated session", () => {
    renderProtectedRoute({
      ...anonymous,
      status: "authenticated",
      session: {
        csrf_token: "example-token",
        expires_at: "2026-01-01T00:00:00Z",
        mfa: true,
        principal: {
          subject: "demo-user",
          username: "demo-user",
          display_name: "Demo User",
          email: null,
          cloud_id: "example-cloud",
          cloudstack_user_id: "00000000-0000-4000-8000-000000000001",
          cloudstack_account_id: "00000000-0000-4000-8000-000000000002",
          cloudstack_domain_id: "00000000-0000-4000-8000-000000000003",
          cloudstack_domain: "example",
          cloudstack_username: "demo-user",
          account: "demo-account",
          domain_ancestors: [],
          roles: ["user"],
          project_ids: [],
          delegated_account_ids: [],
          groups: [],
          amr: ["pwd"],
          mapping_version: "1",
        },
      },
    });
    expect(screen.getByRole("heading", { name: "Plans screen" })).toBeInTheDocument();
  });

  it("does not redirect while the session is still loading", () => {
    const refresh = vi.fn(async () => undefined);
    renderProtectedRoute({ ...anonymous, status: "loading", refresh });
    expect(screen.queryByRole("heading", { name: "Login screen" })).not.toBeInTheDocument();
    expect(screen.getByText("正在加载…")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Plans screen" })).not.toBeInTheDocument();
  });
});
