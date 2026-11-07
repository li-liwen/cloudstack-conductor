/** URL sanitizers. Only same-origin paths and absolute HTTP(S) URLs may render as links. */

export function safeSameOriginPath(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (!trimmed.startsWith("/") || trimmed.startsWith("//") || trimmed.includes("\\")) return null;
  return trimmed.slice(0, 2000);
}

export function safeHttpUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (!trimmed) return null;
  if (trimmed.startsWith("/") && !trimmed.startsWith("//")) return trimmed;
  try {
    const url = new URL(trimmed);
    if (url.protocol !== "https:" && url.protocol !== "http:") return null;
    return url.toString().slice(0, 2000);
  } catch {
    return null;
  }
}

/** Links to public documentation must be HTTP(S); relative links are allowed. */
export function safeDocumentUrl(value: unknown): string | null {
  return safeHttpUrl(value);
}

export function isExternalUrl(value: string): boolean {
  return /^https?:\/\//i.test(value);
}
