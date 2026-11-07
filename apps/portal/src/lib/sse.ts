/**
 * Parsing for the server-sent events emitted by the Conductor API.
 *
 * Runs send named events (status, message, plan, done, error) whose JSON
 * payload is a RunEvent: `{ event_id, event, data, created_at }`. Operations
 * send named `operation` events whose payload is an OperationEvent. Both forms
 * keep the useful fields under `.data`, with the envelope itself available for
 * state and identifiers.
 */

export type JsonObject = Record<string, unknown>;

export interface ParsedSseEvent {
  /** Monotonic event id, used for reconnection and de-duplication. */
  id: number | null;
  /** Named SSE event, or the payload's `event` field as a fallback. */
  name: string;
  /** The full JSON envelope. */
  payload: JsonObject;
  /** The event-specific data, or the envelope when it has no `data` object. */
  data: JsonObject;
  raw: string;
}

export function isJsonObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function parseSseEnvelope(
  eventName: string,
  rawData: string,
  lastEventId?: string,
): ParsedSseEvent | null {
  let payload: unknown;
  try {
    payload = JSON.parse(rawData);
  } catch {
    return null;
  }
  if (!isJsonObject(payload)) return null;

  const payloadEvent = typeof payload.event === "string" ? payload.event : "";
  const name = (payloadEvent || eventName || "message").slice(0, 64);

  let idFromHeader: number | null = null;
  if (lastEventId) {
    const parsed = Number.parseInt(lastEventId, 10);
    if (Number.isFinite(parsed) && parsed >= 0) idFromHeader = parsed;
  }
  const idFromPayload =
    typeof payload.event_id === "number" && Number.isFinite(payload.event_id)
      ? payload.event_id
      : null;
  const id = idFromPayload ?? idFromHeader;

  const data = isJsonObject(payload.data) ? payload.data : payload;
  return { id, name, payload, data, raw: rawData };
}

/** Remembers event ids so an SSE replay after reconnection cannot repeat work. */
export class SseEventTracker {
  private readonly seen = new Set<number>();

  /** Returns true when the event should be handled. */
  accept(event: ParsedSseEvent): boolean {
    if (event.id === null) return true;
    if (this.seen.has(event.id)) return false;
    this.seen.add(event.id);
    return true;
  }

  reset(): void {
    this.seen.clear();
  }
}

export const RUN_EVENT_NAMES = ["status", "message", "plan", "done", "error"] as const;
export const OPERATION_EVENT_NAMES = ["operation", "error"] as const;

const TERMINAL_OPERATION_STATES = new Set([
  "succeeded",
  "failed",
  "partially_succeeded",
  "cancelled",
]);

export function isTerminalOperationPayload(payload: JsonObject): boolean {
  return typeof payload.state === "string" && TERMINAL_OPERATION_STATES.has(payload.state);
}
