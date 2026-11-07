/**
 * Same-origin API client.
 *
 * - credentials are always sent with the request (signed session cookie)
 * - mutating requests carry the CSRF token from GET /api/v1/auth/session
 * - a 401 invokes the registered handler so the UI can return to /login
 * - only the API's safe `message` field is surfaced; response bodies are never
 *   rendered as HTML or raw diagnostic text
 */

const API_PREFIX = "/api/v1";
const CSRF_HEADER = "X-CSRF-Token";
const CSRF_COOKIE = "conductor_csrf";
const SAFE_MESSAGE_LIMIT = 500;

export interface ApiRequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  query?: Record<string, string | number | boolean | null | undefined>;
  signal?: AbortSignal;
  /** Defaults to sending CSRF for mutating methods. Operator read APIs need it too. */
  csrf?: boolean;
  /** Set false for the initial session probe, which must not trigger a redirect. */
  handleUnauthorized?: boolean;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly fields: string[];

  constructor(status: number, code: string, message: string, fields: string[] = []) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.fields = fields;
  }
}

let csrfToken: string | null = null;
let unauthorizedHandler: (() => void) | null = null;

export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

export function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const prefix = `${name}=`;
  for (const item of document.cookie.split(";")) {
    const cookie = item.trim();
    if (cookie.startsWith(prefix)) {
      try {
        return decodeURIComponent(cookie.slice(prefix.length));
      } catch {
        return cookie.slice(prefix.length);
      }
    }
  }
  return null;
}

export function getCsrfToken(): string | null {
  return csrfToken || readCookie(CSRF_COOKIE);
}

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

export function buildQuery(
  query: ApiRequestOptions["query"],
): string {
  if (!query) return "";
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== null && value !== undefined && value !== "") {
      params.set(key, String(value));
    }
  }
  const text = params.toString();
  return text ? `?${text}` : "";
}

export function apiUrl(path: string, query?: ApiRequestOptions["query"]): string {
  return `${API_PREFIX}${path}${buildQuery(query)}`;
}

function isMutation(method: string): boolean {
  return method === "POST" || method === "PUT" || method === "PATCH" || method === "DELETE";
}

function safeApiMessage(payload: unknown): { code: string; message: string; fields: string[] } {
  if (typeof payload === "object" && payload !== null) {
    const record = payload as Record<string, unknown>;
    const code = typeof record.code === "string" ? record.code.slice(0, 80) : "request_failed";
    const message =
      typeof record.message === "string" && record.message.trim()
        ? record.message.trim().slice(0, SAFE_MESSAGE_LIMIT)
        : "";
    const fields = Array.isArray(record.fields)
      ? record.fields.filter((field): field is string => typeof field === "string").slice(0, 20)
      : [];
    return { code, message, fields };
  }
  return { code: "request_failed", message: "", fields: [] };
}

async function readResponseBody(response: Response): Promise<unknown> {
  if (response.status === 204 || response.status === 205) return null;
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    // A non-JSON response (for example a proxy error page) is never shown.
    return null;
  }
}

export async function apiRequest<T>(
  path: string,
  options: ApiRequestOptions = {},
): Promise<T> {
  const method = options.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json" };
  const mutating = isMutation(method);
  const shouldSendCsrf = options.csrf ?? mutating;
  if (shouldSendCsrf) {
    const token = getCsrfToken();
    if (token) headers[CSRF_HEADER] = token;
  }
  let body: string | undefined;
  if (options.body !== undefined && mutating) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }

  let response: Response;
  try {
    response = await fetch(apiUrl(path, options.query), {
      method,
      headers,
      body,
      credentials: "same-origin",
      cache: "no-store",
      signal: options.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, "network_error", "The network request could not be completed.");
  }

  const payload = await readResponseBody(response);

  if (response.status === 401) {
    if (options.handleUnauthorized !== false) unauthorizedHandler?.();
    const safe = safeApiMessage(payload);
    throw new ApiError(401, safe.code || "authentication_required", safe.message, safe.fields);
  }

  if (!response.ok) {
    const safe = safeApiMessage(payload);
    throw new ApiError(
      response.status,
      safe.code,
      safe.message || "The request could not be completed.",
      safe.fields,
    );
  }

  return payload as T;
}
