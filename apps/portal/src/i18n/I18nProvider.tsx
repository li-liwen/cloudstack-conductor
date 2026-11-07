import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { messages, type MessageKey } from "./messages";
import {
  isLocale,
  LOCALE_STORAGE_KEY,
  persistLocale,
  readStoredLocale,
  type Locale,
} from "./locale";

export type TFunction = (
  key: MessageKey,
  params?: Record<string, string | number>,
) => string;

export interface I18nContextValue {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: TFunction;
  availableLocales: Locale[];
}

const I18nContext = createContext<I18nContextValue | null>(null);

function interpolate(template: string, params?: Record<string, string | number>): string {
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) =>
    Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : match,
  );
}

export function resolveInitialLocale(defaultLocale: Locale): Locale {
  const stored = readStoredLocale();
  if (stored) return stored;
  return isLocale(defaultLocale) ? defaultLocale : "zh-CN";
}

interface I18nProviderProps {
  children: ReactNode;
  defaultLocale?: Locale;
  availableLocales?: Locale[];
}

export function I18nProvider({
  children,
  defaultLocale = "zh-CN",
  availableLocales = ["zh-CN", "en"],
}: I18nProviderProps) {
  const [locale, setLocaleState] = useState<Locale>(() => resolveInitialLocale(defaultLocale));

  useEffect(() => {
    document.documentElement.lang = locale;
    document.documentElement.dir = "ltr";
  }, [locale]);

  const setLocale = useCallback((next: Locale) => {
    if (!isLocale(next)) return;
    setLocaleState(next);
    persistLocale(next);
  }, []);

  const t = useCallback<TFunction>(
    (key, params) => {
      const table = messages[locale] as Record<MessageKey, string>;
      const fallback = messages["zh-CN"] as Record<MessageKey, string>;
      const template = table[key] ?? fallback[key] ?? key;
      return interpolate(template, params);
    },
    [locale],
  );

  const value = useMemo<I18nContextValue>(
    () => ({ locale, setLocale, t, availableLocales }),
    [locale, setLocale, t, availableLocales],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue {
  const value = useContext(I18nContext);
  if (!value) throw new Error("useI18n must be used inside an I18nProvider");
  return value;
}

export { LOCALE_STORAGE_KEY };
