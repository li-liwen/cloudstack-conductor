import { isLocale, LOCALES, type Locale } from "../i18n/locale";

/**
 * The public runtime configuration served by GET /api/v1/config.
 *
 * This file intentionally contains no deployment identity: the defaults are
 * generic, and every branded value arrives at runtime. Deployment identity and
 * secrets must never be compiled into the bundle.
 */

export interface Branding {
  product_name: string;
  company_name: string;
  short_name: string;
  tagline_zh: string;
  tagline_en: string;
  primary_color: string;
  accent_color: string;
  logo_url: string;
  logo_mark_url: string;
  favicon_url: string;
  support_url: string | null;
  docs_url: string | null;
  environment_label: string | null;
  locale_default: Locale;
  locales: Locale[];
}

export interface RuntimeConfig {
  product: string;
  version: string;
  environment: string;
  branding: Branding;
  auth: {
    mode: string;
    login_url: string;
    logout_url: string;
  };
  features: {
    mcp: boolean;
    console: boolean;
    guest_tasks: boolean;
    usage: boolean;
  };
  limits: {
    approval_ttl_minutes: number;
  };
}

export const DEFAULT_BRANDING: Branding = {
  product_name: "CloudStack Conductor",
  company_name: "CloudStack Conductor",
  short_name: "Conductor",
  tagline_zh: "面向 Apache CloudStack 的多租户智能控制平面",
  tagline_en: "A tenant-aware agent and MCP control plane for Apache CloudStack",
  primary_color: "#0D5EF4",
  accent_color: "#00BFA0",
  logo_url: "/branding/logo.svg",
  logo_mark_url: "/branding/logo-mark.svg",
  favicon_url: "/branding/favicon.svg",
  support_url: null,
  docs_url: null,
  environment_label: null,
  locale_default: "zh-CN",
  locales: ["zh-CN", "en"],
};

export function createDefaultConfig(): RuntimeConfig {
  return {
    product: "cloudstack-conductor",
    version: "0.0.0",
    environment: "development",
    branding: { ...DEFAULT_BRANDING, locales: [...DEFAULT_BRANDING.locales] },
    auth: { mode: "oidc", login_url: "/api/v1/auth/login", logout_url: "/api/v1/auth/logout" },
    features: { mcp: true, console: true, guest_tasks: false, usage: false },
    limits: { approval_ttl_minutes: 10 },
  };
}

function objectValue(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function text(value: unknown, fallback: string, maxLength = 200): string {
  if (typeof value !== "string") return fallback;
  const trimmed = value.trim();
  if (!trimmed) return fallback;
  return trimmed.slice(0, maxLength);
}

function nullableText(value: unknown, maxLength = 500): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (!trimmed) return null;
  return trimmed.slice(0, maxLength);
}

function boolValue(value: unknown, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function numberValue(value: unknown, fallback: number, min: number, max: number): number {
  if (typeof value !== "number" || !Number.isFinite(value)) return fallback;
  return Math.min(max, Math.max(min, Math.round(value)));
}

/** Accept a same-origin application path; reject protocol-relative and external URLs. */
function assetPath(value: unknown, fallback: string): string {
  if (
    typeof value === "string" &&
    value.startsWith("/") &&
    !value.startsWith("//") &&
    !value.includes("\\") &&
    value.length <= 500
  ) {
    return value;
  }
  return fallback;
}

/** Accept only an application-relative auth path. */
function authPath(value: unknown, fallback: string): string {
  return assetPath(value, fallback);
}

function hexColor(value: unknown, fallback: string): string {
  return typeof value === "string" && /^#[0-9a-fA-F]{6}$/.test(value) ? value : fallback;
}

function safeHttpUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" && url.protocol !== "http:") return null;
    return url.toString().slice(0, 1000);
  } catch {
    return null;
  }
}

/**
 * Normalize an untrusted runtime configuration payload.
 *
 * The API validates this document, but the portal still refuses malformed
 * values (for example script URLs, protocol-relative assets, or bad colors)
 * and falls back to generic defaults.
 */
export function normalizeRuntimeConfig(raw: unknown): RuntimeConfig {
  const defaults = createDefaultConfig();
  const root = objectValue(raw);
  const brand = objectValue(root.branding);
  const auth = objectValue(root.auth);
  const features = objectValue(root.features);
  const limits = objectValue(root.limits);

  const locales = Array.isArray(brand.locales)
    ? brand.locales.filter(isLocale)
    : [...LOCALES];
  const uniqueLocales = [...new Set(locales.length ? locales : LOCALES)];
  const requestedDefault = isLocale(brand.locale_default) ? brand.locale_default : null;
  const localeDefault =
    requestedDefault && uniqueLocales.includes(requestedDefault)
      ? requestedDefault
      : uniqueLocales[0] ?? "zh-CN";

  return {
    product: text(root.product, defaults.product, 80),
    version: text(root.version, defaults.version, 40),
    environment: text(root.environment, defaults.environment, 40),
    branding: {
      product_name: text(brand.product_name, defaults.branding.product_name, 120),
      company_name: text(brand.company_name, defaults.branding.company_name, 120),
      short_name: text(brand.short_name, defaults.branding.short_name, 60),
      tagline_zh: text(brand.tagline_zh, defaults.branding.tagline_zh, 300),
      tagline_en: text(brand.tagline_en, defaults.branding.tagline_en, 300),
      primary_color: hexColor(brand.primary_color, defaults.branding.primary_color),
      accent_color: hexColor(brand.accent_color, defaults.branding.accent_color),
      logo_url: assetPath(brand.logo_url, defaults.branding.logo_url),
      logo_mark_url: assetPath(brand.logo_mark_url, defaults.branding.logo_mark_url),
      favicon_url: assetPath(brand.favicon_url, defaults.branding.favicon_url),
      support_url: safeHttpUrl(brand.support_url),
      docs_url: safeHttpUrl(brand.docs_url),
      environment_label: nullableText(brand.environment_label, 80),
      locale_default: localeDefault,
      locales: uniqueLocales,
    },
    auth: {
      mode: text(auth.mode, defaults.auth.mode, 20),
      login_url: authPath(auth.login_url, defaults.auth.login_url),
      logout_url: authPath(auth.logout_url, defaults.auth.logout_url),
    },
    features: {
      mcp: boolValue(features.mcp, defaults.features.mcp),
      console: boolValue(features.console, defaults.features.console),
      guest_tasks: boolValue(features.guest_tasks, defaults.features.guest_tasks),
      usage: boolValue(features.usage, defaults.features.usage),
    },
    limits: {
      approval_ttl_minutes: numberValue(
        limits.approval_ttl_minutes,
        defaults.limits.approval_ttl_minutes,
        1,
        24 * 60,
      ),
    },
  };
}
