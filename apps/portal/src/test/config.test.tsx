import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ConfigProvider, useConfig } from "../config/ConfigProvider";

function Probe() {
  const { config, status } = useConfig();
  return (
    <div>
      <span data-testid="status">{status}</span>
      <span data-testid="product">{config.branding.product_name}</span>
      <span data-testid="company">{config.branding.company_name}</span>
      <span data-testid="logo">{config.branding.logo_url}</span>
    </div>
  );
}

function fetcherReturning(payload: unknown): typeof fetch {
  return vi.fn(async () => ({
    ok: true,
    status: 200,
    json: async () => payload,
  })) as unknown as typeof fetch;
}

describe("runtime configuration and branding", () => {
  it("loads runtime branding, sets the title, favicon, and CSS variables", async () => {
    const fetcher = fetcherReturning({
      product: "cloudstack-conductor",
      version: "1.2.3",
      environment: "demo",
      branding: {
        product_name: "Example Cloud Conductor",
        company_name: "Example Cloud",
        short_name: "Conductor",
        tagline_zh: "示例控制平面",
        tagline_en: "Example control plane",
        primary_color: "#123456",
        accent_color: "#00AA88",
        logo_url: "/branding/logo.svg",
        logo_mark_url: "/branding/logo-mark.svg",
        favicon_url: "/branding/favicon.svg",
        locale_default: "en",
        locales: ["zh-CN", "en"],
      },
      auth: { mode: "demo", login_url: "/api/v1/auth/login", logout_url: "/api/v1/auth/logout" },
      features: { mcp: true, console: true, guest_tasks: false, usage: false },
      limits: { approval_ttl_minutes: 10 },
    });

    render(
      <ConfigProvider fetcher={fetcher}>
        <Probe />
      </ConfigProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("ready"));
    expect(screen.getByTestId("product")).toHaveTextContent("Example Cloud Conductor");
    expect(screen.getByTestId("company")).toHaveTextContent("Example Cloud");
    expect(screen.getByTestId("logo")).toHaveTextContent("/branding/logo.svg");
    expect(document.title).toBe("Example Cloud Conductor");
    expect(document.documentElement.style.getPropertyValue("--brand-primary")).toBe("#123456");
    expect(document.documentElement.style.getPropertyValue("--brand-accent")).toBe("#00AA88");
    expect(document.documentElement.style.getPropertyValue("--brand-contrast")).toBe("#FFFFFF");
    expect(document.querySelector<HTMLLinkElement>('link[rel="icon"]')?.href).toContain(
      "/branding/favicon.svg",
    );
    expect(fetcher).toHaveBeenCalledWith(
      "/api/v1/config",
      expect.objectContaining({ credentials: "same-origin" }),
    );
  });

  it("falls back to generic branding and rejects unsafe asset URLs", async () => {
    const fetcher = fetcherReturning({
      branding: {
        product_name: "Runtime Product",
        primary_color: "not-a-color",
        accent_color: "#00AA88",
        logo_url: "https://example.com/logo.svg",
        locale_default: "fr",
      },
    });

    render(
      <ConfigProvider fetcher={fetcher}>
        <Probe />
      </ConfigProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("ready"));
    expect(screen.getByTestId("product")).toHaveTextContent("Runtime Product");
    // External or protocol-relative branding assets are never rendered.
    expect(screen.getByTestId("logo")).toHaveTextContent("/branding/logo.svg");
    expect(document.documentElement.style.getPropertyValue("--brand-primary")).toBe("#0D5EF4");
  });

  it("keeps the generic defaults and reports a safe error when config cannot load", async () => {
    const fetcher = vi.fn(async () => ({
      ok: false,
      status: 503,
      json: async () => ({ code: "portal_unavailable" }),
    })) as unknown as typeof fetch;

    render(
      <ConfigProvider fetcher={fetcher}>
        <Probe />
      </ConfigProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("error"));
    expect(screen.getByTestId("product")).toHaveTextContent("CloudStack Conductor");
    expect(document.title).toBe("CloudStack Conductor");
    expect(document.body.textContent).not.toContain("portal_unavailable");
  });
});
