import type { MessageDto, MessagePartDto } from "../api/types";

/** Merge messages by their durable id, preserving chronological order. */
export function mergeMessages(
  existing: readonly MessageDto[],
  incoming: readonly MessageDto[],
): MessageDto[] {
  const byId = new Map<string, MessageDto>();
  for (const message of existing) byId.set(message.message_id, message);
  for (const message of incoming) byId.set(message.message_id, message);
  return [...byId.values()].sort((left, right) => {
    const a = Date.parse(left.created_at);
    const b = Date.parse(right.created_at);
    if (Number.isNaN(a) || Number.isNaN(b) || a === b) return 0;
    return a - b;
  });
}

export function textParts(parts: readonly MessagePartDto[] | null | undefined): string {
  if (!Array.isArray(parts)) return "";
  return parts
    .filter((part) => part.type === "text" && typeof part.text === "string")
    .map((part) => part.text)
    .join("\n\n")
    .trim();
}

export function messageText(message: MessageDto | null | undefined): string {
  return textParts(message?.parts);
}

export function planParts(message: MessageDto | null | undefined): MessagePartDto[] {
  return (message?.parts ?? []).filter((part) => part?.type === "plan");
}

/** Extract a citation list from both message citations and citation parts. */
export function citationsOf(message: MessageDto): Array<Record<string, unknown>> {
  const citations: Array<Record<string, unknown>> = [];
  for (const citation of message.citations ?? []) {
    if (citation && typeof citation === "object") citations.push(citation);
  }
  for (const part of message.parts ?? []) {
    if (part.type === "citation" && part.data && typeof part.data === "object") {
      citations.push(part.data);
    }
  }
  const seen = new Set<string>();
  return citations.filter((citation) => {
    const key = `${String(citation.title ?? "")}|${String(citation.source_url ?? "")}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
