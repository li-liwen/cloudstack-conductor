import { describe, expect, it } from "vitest";
import type { MessageDto } from "../api/types";
import { mergeMessages } from "../lib/messages";
import {
  isTerminalOperationPayload,
  parseSseEnvelope,
  SseEventTracker,
} from "../lib/sse";

const runEvent = {
  event_id: 7,
  run_id: "run-example",
  event: "message",
  data: {
    message_id: "msg-1",
    conversation_id: "conv-1",
    role: "assistant",
    parts: [{ type: "text", text: "Example answer" }],
    created_at: "2026-01-01T00:00:00Z",
    citations: [],
  },
  created_at: "2026-01-01T00:00:00Z",
};

describe("SSE parsing", () => {
  it("parses a run event envelope and exposes the inner data", () => {
    const parsed = parseSseEnvelope("message", JSON.stringify(runEvent), "7");
    expect(parsed).not.toBeNull();
    expect(parsed?.id).toBe(7);
    expect(parsed?.name).toBe("message");
    expect(parsed?.payload.run_id).toBe("run-example");
    expect(parsed?.data.message_id).toBe("msg-1");
  });

  it("falls back to Last-Event-ID and rejects malformed payloads", () => {
    const payload = { event_id: undefined, state: "running", data: {} };
    const parsed = parseSseEnvelope("status", JSON.stringify(payload), "42");
    expect(parsed?.id).toBe(42);
    expect(parseSseEnvelope("message", "{not json")).toBeNull();
    expect(parseSseEnvelope("message", "[]")).toBeNull();
    expect(parseSseEnvelope("message", "")).toBeNull();
  });

  it("recognizes terminal operation payloads", () => {
    expect(isTerminalOperationPayload({ state: "running" })).toBe(false);
    expect(isTerminalOperationPayload({ state: "needs_reconciliation" })).toBe(false);
    expect(isTerminalOperationPayload({ state: "succeeded" })).toBe(true);
    expect(isTerminalOperationPayload({ state: "cancelled" })).toBe(true);
  });
});

describe("SSE de-duplication", () => {
  it("accepts each event id once across a reconnect replay", () => {
    const tracker = new SseEventTracker();
    const first = parseSseEnvelope("message", JSON.stringify(runEvent));
    const replay = parseSseEnvelope("message", JSON.stringify(runEvent));
    expect(first).not.toBeNull();
    expect(replay).not.toBeNull();
    expect(tracker.accept(first!)).toBe(true);
    expect(tracker.accept(replay!)).toBe(false);

    const next = parseSseEnvelope("message", JSON.stringify({ ...runEvent, event_id: 8 }));
    expect(tracker.accept(next!)).toBe(true);
  });

  it("merges replayed messages by durable id instead of appending duplicates", () => {
    const assistant: MessageDto = {
      message_id: "msg-1",
      conversation_id: "conv-1",
      role: "assistant",
      parts: [{ type: "text", text: "First" }],
      created_at: "2026-01-01T00:00:00Z",
      citations: [],
    };
    const updated: MessageDto = { ...assistant, parts: [{ type: "text", text: "Updated" }] };
    const second: MessageDto = {
      ...assistant,
      message_id: "msg-2",
      created_at: "2026-01-01T00:01:00Z",
    };

    const merged = mergeMessages([assistant], [updated, second]);
    expect(merged).toHaveLength(2);
    expect(merged.find((message) => message.message_id === "msg-1")?.parts[0].text).toBe(
      "Updated",
    );
    expect(merged.map((message) => message.message_id)).toEqual(["msg-1", "msg-2"]);
  });
});
