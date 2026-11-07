export type Locale = "zh-CN" | "en";

export const LOCALES: readonly Locale[] = ["zh-CN", "en"] as const;
export const LOCALE_STORAGE_KEY = "conductor.locale";

export function isLocale(value: unknown): value is Locale {
  return value === "zh-CN" || value === "en";
}

export function localeLabel(locale: Locale): string {
  return locale === "zh-CN" ? "中文" : "English";
}

/** Read the persisted language choice. Returns null when none is stored. */
export function readStoredLocale(): Locale | null {
  try {
    const value = window.localStorage.getItem(LOCALE_STORAGE_KEY);
    return isLocale(value) ? value : null;
  } catch {
    return null;
  }
}

export function persistLocale(locale: Locale): void {
  try {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, locale);
  } catch {
    // Storage can be unavailable in private browsing; the in-memory choice still applies.
  }
}

/** Pick a localized pair supplied by the API (e.g. message_zh / message_en). */
export function pickLocalized(
  locale: Locale,
  zh: string | null | undefined,
  en: string | null | undefined,
): string {
  return (locale === "en" ? en || zh : zh || en) ?? "";
}
