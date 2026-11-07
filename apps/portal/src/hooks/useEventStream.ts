import { useCallback, useEffect, useRef, useState } from "react";
import { parseSseEnvelope, SseEventTracker, type ParsedSseEvent } from "../lib/sse";

export type StreamStatus =
  | "idle"
  | "connecting"
  | "open"
  | "reconnecting"
  | "closed"
  | "failed";

interface TrackerState {
  key: string;
  tracker: SseEventTracker;
  lastEventId: string;
}

export interface UseEventStreamOptions {
  /** URL relative to the application origin, or null to stay disconnected. */
  url: string | null;
  enabled?: boolean;
  eventNames: readonly string[];
  onEvent: (event: ParsedSseEvent) => void;
  /** Close the stream after a terminal event (for example `done`). */
  shouldClose?: (event: ParsedSseEvent) => boolean;
  maxRetries?: number;
}

export interface UseEventStreamResult {
  status: StreamStatus;
  reconnect: () => void;
  lastEventId: string;
}

/**
 * Named-event SSE client built on EventSource.
 *
 * It tracks the last event id, reconnects with `?after=` (the API accepts it in
 * addition to Last-Event-ID), and de-duplicates replayed events so a reconnect
 * cannot render the same message twice. EventSource itself is same-origin with
 * credentials; no token is ever placed in the URL.
 */
export function useEventStream({
  url,
  enabled = true,
  eventNames,
  onEvent,
  shouldClose,
  maxRetries = 8,
}: UseEventStreamOptions): UseEventStreamResult {
  const [status, setStatus] = useState<StreamStatus>(url && enabled ? "connecting" : "idle");
  const [nonce, setNonce] = useState(0);
  const onEventRef = useRef(onEvent);
  const shouldCloseRef = useRef(shouldClose);
  onEventRef.current = onEvent;
  shouldCloseRef.current = shouldClose;

  const key = url ?? "";
  const trackerRef = useRef<TrackerState>({
    key: "",
    tracker: new SseEventTracker(),
    lastEventId: "",
  });
  if (trackerRef.current.key !== key) {
    trackerRef.current = { key, tracker: new SseEventTracker(), lastEventId: "" };
  }

  const eventKey = eventNames.join(",");

  useEffect(() => {
    if (!url || !enabled) {
      setStatus("idle");
      return undefined;
    }
    if (typeof EventSource === "undefined") {
      setStatus("failed");
      return undefined;
    }

    const trackerState = trackerRef.current;
    const names = eventKey.split(",").filter(Boolean);
    let cancelled = false;
    let source: EventSource | null = null;
    let attempts = 0;
    let finished = false;

    setStatus(attempts === 0 ? "connecting" : "reconnecting");
    const after = trackerState.lastEventId;
    const target = after
      ? `${url}${url.includes("?") ? "&" : "?"}after=${encodeURIComponent(after)}`
      : url;

    try {
      source = new EventSource(target, { withCredentials: true });
    } catch {
      setStatus("failed");
      return undefined;
    }
    const es = source;

    es.onopen = () => {
      if (cancelled || finished) return;
      setStatus("open");
    };

    const handle = (message: MessageEvent<string>): void => {
      if (cancelled || finished) return;
      if (typeof message.data !== "string") return;
      if (message.lastEventId) trackerState.lastEventId = message.lastEventId;
      const parsed = parseSseEnvelope(
        message.type,
        message.data,
        message.lastEventId || trackerState.lastEventId,
      );
      if (!parsed || !trackerState.tracker.accept(parsed)) return;
      // A delivered event proves the relay is healthy; reset the failure count.
      attempts = 0;
      onEventRef.current(parsed);
      if (shouldCloseRef.current?.(parsed)) {
        finished = true;
        es.close();
        setStatus("closed");
      }
    };

    for (const name of names) {
      es.addEventListener(name, handle as EventListener);
    }

    es.addEventListener("error", (event: Event) => {
      if (cancelled || finished) return;
      // A server-sent named `error` event is a MessageEvent with a payload;
      // a transport error is a plain Event. Only the latter retries.
      if (event instanceof MessageEvent && typeof (event as MessageEvent<string>).data === "string") {
        handle(event as MessageEvent<string>);
        return;
      }
      attempts += 1;
      if (attempts > maxRetries) {
        es.close();
        setStatus("failed");
        return;
      }
      setStatus("reconnecting");
      // Keep the EventSource open: the browser reconnects automatically and
      // sends the standard Last-Event-ID header from the last received id.
      // A manual reconnect additionally passes ?after= for servers that
      // accept an explicit resume cursor.
    });

    return () => {
      cancelled = true;
      es.close();
    };
    // eventKey is the stable representation of eventNames.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url, enabled, eventKey, maxRetries, nonce]);

  const reconnect = useCallback(() => setNonce((value) => value + 1), []);
  return { status, reconnect, lastEventId: trackerRef.current.lastEventId };
}
