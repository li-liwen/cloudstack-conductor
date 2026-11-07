import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  createDefaultConfig,
  normalizeRuntimeConfig,
  type RuntimeConfig,
} from "./types";

export type ConfigStatus = "loading" | "ready" | "error";

export interface ConfigContextValue {
  config: RuntimeConfig;
  status: ConfigStatus;
  reload: () => void;
}

const ConfigContext = createContext<ConfigContextValue | null>(null);

function relativeLuminance(hex: string): number {
  const channels = [1, 3, 5].map((start) => {
    const value = Number.parseInt(hex.slice(start, start + 2), 16) / 255;
    return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

/** Apply runtime branding to the document without hardcoding deployment identity. */
export function applyBranding(config: RuntimeConfig): void {
  const { branding } = config;
  const root = document.documentElement;

  document.title = branding.environment_label
    ? `${branding.product_name} — ${branding.environment_label}`
    : branding.product_name;

  root.style.setProperty("--brand-primary", branding.primary_color);
  root.style.setProperty("--brand-accent", branding.accent_color);
  // Keep button text readable for any runtime brand color.
  root.style.setProperty(
    "--brand-contrast",
    relativeLuminance(branding.primary_color) > 0.45 ? "#0B1220" : "#FFFFFF",
  );
  root.dataset.brand = config.product;
  root.dataset.environment = config.environment;

  const iconHref = branding.favicon_url;
  let icon = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
  if (!icon) {
    icon = document.createElement("link");
    icon.rel = "icon";
    document.head.appendChild(icon);
  }
  icon.href = iconHref;
  icon.type = iconHref.endsWith(".svg") ? "image/svg+xml" : "image/png";

  let theme = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
  if (!theme) {
    theme = document.createElement("meta");
    theme.name = "theme-color";
    document.head.appendChild(theme);
  }
  theme.content = branding.primary_color;
}

interface ConfigProviderProps {
  children: ReactNode;
  /** Injectable fetch for tests; defaults to the global fetch. */
  fetcher?: typeof fetch;
}

export function ConfigProvider({ children, fetcher }: ConfigProviderProps) {
  const [config, setConfig] = useState<RuntimeConfig>(() => createDefaultConfig());
  const [status, setStatus] = useState<ConfigStatus>("loading");
  const [attempt, setAttempt] = useState(0);
  const doFetch = fetcher ?? fetch;

  const reload = useCallback(() => {
    setStatus("loading");
    setAttempt((value) => value + 1);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;

    async function load(): Promise<void> {
      try {
        const response = await doFetch("/api/v1/config", {
          credentials: "same-origin",
          cache: "no-store",
          headers: { Accept: "application/json" },
          signal: controller.signal,
        });
        if (!response.ok) throw new Error("config request failed");
        const payload: unknown = await response.json();
        if (!active) return;
        setConfig(normalizeRuntimeConfig(payload));
        setStatus("ready");
      } catch {
        if (!active || controller.signal.aborted) return;
        // Keep the generic defaults so the login screen and navigation remain
        // usable; surface a safe banner instead of an internal error.
        setStatus("error");
      }
    }

    void load();
    return () => {
      active = false;
      controller.abort();
    };
  }, [doFetch, attempt]);

  useEffect(() => {
    applyBranding(config);
  }, [config]);

  const value = useMemo<ConfigContextValue>(
    () => ({ config, status, reload }),
    [config, status, reload],
  );

  return <ConfigContext.Provider value={value}>{children}</ConfigContext.Provider>;
}

export function useConfig(): ConfigContextValue {
  const value = useContext(ConfigContext);
  if (!value) throw new Error("useConfig must be used inside a ConfigProvider");
  return value;
}
