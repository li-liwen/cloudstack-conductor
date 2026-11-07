import type { Locale } from "../i18n/locale";
import type { TFunction } from "../i18n/I18nProvider";

const DATE_LOCALE: Record<Locale, string> = {
  "zh-CN": "zh-CN",
  en: "en-GB",
};

function validDate(value: string | number | Date | null | undefined): Date | null {
  if (value === null || value === undefined || value === "") return null;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatDateTime(
  value: string | number | Date | null | undefined,
  locale: Locale,
): string {
  const date = validDate(value);
  if (!date) return "—";
  try {
    return new Intl.DateTimeFormat(DATE_LOCALE[locale], {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(date);
  } catch {
    return date.toISOString();
  }
}

export function formatDate(
  value: string | number | Date | null | undefined,
  locale: Locale,
): string {
  const date = validDate(value);
  if (!date) return "—";
  try {
    return new Intl.DateTimeFormat(DATE_LOCALE[locale], { dateStyle: "medium" }).format(date);
  } catch {
    return date.toISOString().slice(0, 10);
  }
}

/** Human-friendly downtime from a non-negative number of seconds. */
export function formatDuration(seconds: number | null | undefined, t: TFunction): string {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) return "—";
  const total = Math.round(seconds);
  if (total === 0) return t("duration.none");

  const days = Math.floor(total / 86_400);
  const hours = Math.floor((total % 86_400) / 3_600);
  const minutes = Math.floor((total % 3_600) / 60);
  const secs = total % 60;
  const parts: string[] = [];

  if (days) parts.push(t("duration.day", { count: days }));
  if (hours) parts.push(t("duration.hour", { count: hours }));
  if (minutes) parts.push(t("duration.minute", { count: minutes }));
  if (secs) parts.push(t("duration.second", { count: secs }));
  return parts.slice(0, 2).join(" ");
}

export function formatCount(value: number | null | undefined, locale: Locale): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  try {
    return new Intl.NumberFormat(DATE_LOCALE[locale]).format(value);
  } catch {
    return String(value);
  }
}

/** Safe, bounded JSON preview for audit details and operation results. */
export function formatJson(value: unknown, maxLength = 4000): string {
  if (value === null || value === undefined) return "—";
  try {
    const text = JSON.stringify(value, null, 2);
    if (!text) return "—";
    return text.length > maxLength ? `${text.slice(0, maxLength)}…` : text;
  } catch {
    return "—";
  }
}

export function truncateMiddle(value: string, visible = 8): string {
  if (value.length <= visible * 2 + 1) return value;
  return `${value.slice(0, visible)}…${value.slice(-visible)}`;
}
