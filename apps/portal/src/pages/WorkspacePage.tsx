import { useEffect, useRef, useState } from "react";
import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  ThreadPrimitive,
  useExternalStoreRuntime,
  type ThreadMessage,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import { ApiError } from "../api/client";
import { api } from "../api/endpoints";
import type { ConversationDto, MessageDto, RunDto } from "../api/types";
import {
  CitationList,
  PartRenderer,
  PlanPart,
} from "../components/AssistantParts";
import { Icon } from "../components/Icon";
import { Brand } from "../components/Brand";
import {
  Alert,
  ErrorState,
  LoadingState,
  PageHeader,
} from "../components/Primitives";
import { useConfig } from "../config/ConfigProvider";
import { useApiQuery } from "../hooks/useApiQuery";
import { useEventStream } from "../hooks/useEventStream";
import { useI18n } from "../i18n/I18nProvider";
import { userErrorMessage } from "../lib/errors";
import { formatDateTime } from "../lib/format";
import { citationsOf, mergeMessages, messageText, textParts } from "../lib/messages";
import { pickLocalized } from "../i18n/locale";
import { RUN_EVENT_NAMES, type ParsedSseEvent } from "../lib/sse";

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function parseMessageDto(value: unknown): MessageDto | null {
  const item = record(value);
  if (
    item &&
    typeof item.message_id === "string" &&
    typeof item.conversation_id === "string" &&
    typeof item.created_at === "string" &&
    Array.isArray(item.parts)
  ) {
    return value as MessageDto;
  }
  return null;
}

function appendPartText(part: unknown): string {
  const item = record(part);
  return item && item.type === "text" && typeof item.text === "string" ? item.text : "";
}

function extractAppendText(content: unknown): string {
  if (typeof content === "string") return content.trim();
  if (Array.isArray(content)) {
    return content
      .map(appendPartText)
      .filter(Boolean)
      .join("\n")
      .trim();
  }
  return "";
}

function threadMessageFrom(message: MessageDto): ThreadMessageLike {
  const text = messageText(message);
  const createdAt = new Date(message.created_at);
  return {
    id: message.message_id,
    role:
      message.role === "user"
        ? "user"
        : message.role === "system"
          ? "system"
          : "assistant",
    content: text ? [{ type: "text" as const, text }] : [],
    createdAt: Number.isNaN(createdAt.getTime()) ? undefined : createdAt,
    status: { type: "complete", reason: "stop" },
    metadata: {
      custom: {
        parts: message.parts,
        citations: citationsOf(message),
      },
    },
  };
}

function customMetadata(message: ThreadMessage): {
  parts: MessageDto["parts"];
  citations: Array<Record<string, unknown>>;
} {
  const custom = message.metadata?.custom as
    | { parts?: unknown; citations?: unknown }
    | undefined;
  return {
    parts: Array.isArray(custom?.parts) ? (custom.parts as MessageDto["parts"]) : [],
    citations: Array.isArray(custom?.citations)
      ? (custom.citations as Array<Record<string, unknown>>)
      : [],
  };
}

function contentText(message: ThreadMessage): string {
  return message.content
    .map((part) => (part.type === "text" ? part.text : ""))
    .filter(Boolean)
    .join("\n");
}

function MessageRow({ message }: { message: ThreadMessage }) {
  const { t, locale } = useI18n();
  const { config } = useConfig();
  const { parts, citations } = customMetadata(message);
  const isUser = message.role === "user";
  const isOptimistic = message.metadata?.isOptimistic === true;
  const author = isUser
    ? t("workspace.you")
    : message.role === "system"
      ? t("workspace.system")
      : config.branding.short_name;

  if (isOptimistic) {
    return (
      <article className="message message-assistant" aria-label={t("workspace.messageFrom", { role: author })}>
        <span className="message-avatar" aria-hidden="true">
          <img src={config.branding.logo_mark_url} alt="" width={28} height={28} />
        </span>
        <div className="message-body">
          <p className="typing-indicator">
            <span className="typing-dot" aria-hidden="true" />
            <span className="typing-dot" aria-hidden="true" />
            <span className="typing-dot" aria-hidden="true" />
            <span className="visually-hidden">{t("workspace.typing")}</span>
          </p>
        </div>
      </article>
    );
  }

  const fallback = parts.length === 0 ? contentText(message) : "";
  return (
    <article
      className={`message message-${isUser ? "user" : message.role === "system" ? "system" : "assistant"}`}
      aria-label={t("workspace.messageFrom", { role: author })}
    >
      <span className="message-avatar" aria-hidden="true">
        {isUser ? (
          <Icon name="account" size={18} />
        ) : (
          <img src={config.branding.logo_mark_url} alt="" width={28} height={28} />
        )}
      </span>
      <div className="message-body">
        <header className="message-head">
          <span className="message-author">{author}</span>
          {message.createdAt ? (
            <time dateTime={message.createdAt.toISOString()}>
              {formatDateTime(message.createdAt, locale)}
            </time>
          ) : null}
        </header>
        {isUser ? (
          <p className="message-text">{textParts(parts) || fallback || contentText(message)}</p>
        ) : parts.length > 0 ? (
          parts.map((part, index) => (
            <PartRenderer key={`${part.type}-${index}`} part={part} />
          ))
        ) : (
          fallback && <p className="message-text">{fallback}</p>
        )}
        {!isUser ? <CitationList citations={citations} /> : null}
      </div>
    </article>
  );
}

function ConversationListItem({
  conversation,
  selected,
  onSelect,
}: {
  conversation: ConversationDto;
  selected: boolean;
  onSelect: () => void;
}) {
  const { t, locale } = useI18n();
  const title = conversation.title?.trim() || t("workspace.untitledConversation");
  return (
    <li>
      <button
        type="button"
        className={`conversation-item${selected ? " is-selected" : ""}`}
        aria-current={selected ? "true" : undefined}
        onClick={onSelect}
        title={title}
      >
        <span className="conversation-title">{title}</span>
        <time dateTime={conversation.updated_at}>
          {formatDateTime(conversation.updated_at, locale)}
        </time>
      </button>
    </li>
  );
}

function ConnectionIndicator({
  status,
  onReconnect,
}: {
  status: ReturnType<typeof useEventStream>["status"];
  onReconnect: () => void;
}) {
  const { t } = useI18n();
  if (status === "idle") return null;
  const label =
    status === "open"
      ? t("workspace.live")
      : status === "connecting" || status === "reconnecting"
        ? t("workspace.reconnecting")
        : status === "failed"
          ? t("workspace.streamFailed")
          : t("workspace.streamClosed");

  return (
    <span className={`live-indicator is-${status}`}>
      <span className="live-dot" aria-hidden="true" />
      <span role="status" aria-live="polite">
        {label}
      </span>
      {status === "failed" ? (
        <button type="button" className="btn btn-ghost btn-small" onClick={onReconnect}>
          <Icon name="refresh" size={14} />
          {t("workspace.reconnect")}
        </button>
      ) : null}
    </span>
  );
}

export function WorkspacePage() {
  const { t, locale } = useI18n();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [messages, setMessages] = useState<MessageDto[]>([]);
  const [activeRun, setActiveRun] = useState<RunDto | null>(null);
  const [turnActive, setTurnActive] = useState(false);
  const [sending, setSending] = useState(false);
  const [creating, setCreating] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);
  const [failedText, setFailedText] = useState<string | null>(null);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [streamStatus, setStreamStatus] = useState<{ zh?: string; en?: string } | null>(null);
  const [streamPlan, setStreamPlan] = useState<Record<string, unknown> | null>(null);

  const loadedConversationRef = useRef<string | null>(null);
  const selectedIdRef = useRef<string | null>(null);
  selectedIdRef.current = selectedId;

  const conversationsQuery = useApiQuery((signal) => api.listConversations(signal), []);
  const conversationQuery = useApiQuery(
    (signal) => (selectedId ? api.getConversation(selectedId, signal) : Promise.resolve(null)),
    [selectedId],
    { enabled: Boolean(selectedId) },
  );

  const resetThread = () => {
    loadedConversationRef.current = null;
    setMessages([]);
    setActiveRun(null);
    setTurnActive(false);
    setStreamStatus(null);
    setStreamPlan(null);
    setStreamError(null);
    setSendError(null);
    setFailedText(null);
  };

  // Select the most recent conversation on first load.
  useEffect(() => {
    if (selectedId) return;
    const items = conversationsQuery.data?.items ?? [];
    if (items.length > 0) {
      const first = items[0].conversation_id;
      loadedConversationRef.current = null;
      setSelectedId(first);
    }
  }, [conversationsQuery.data, selectedId]);

  // Hydrate from the server, merging so a replayed SSE message cannot duplicate.
  useEffect(() => {
    const data = conversationQuery.data;
    if (!data) return;
    const id = data.conversation.conversation_id;
    if (loadedConversationRef.current !== id) {
      loadedConversationRef.current = id;
      setMessages(data.messages);
      const running = [...data.runs]
        .reverse()
        .find((run) => run.state === "queued" || run.state === "running");
      setActiveRun(running ?? null);
      setTurnActive(Boolean(running));
      setStreamStatus(null);
      setStreamPlan(null);
      setStreamError(null);
    } else {
      setMessages((previous) => mergeMessages(previous, data.messages));
    }
  }, [conversationQuery.data]);

  const selectConversation = (conversationId: string) => {
    if (conversationId === selectedId) return;
    resetThread();
    setSelectedId(conversationId);
  };

  const createConversation = async (): Promise<ConversationDto | null> => {
    if (creating) return null;
    setCreating(true);
    setActionError(null);
    try {
      const created = await api.createConversation();
      resetThread();
      // Mark the new thread as loaded so the first detail fetch only merges
      // messages and never clears a run started from this screen.
      loadedConversationRef.current = created.conversation_id;
      setSelectedId(created.conversation_id);
      conversationsQuery.reload();
      return created;
    } catch (error) {
      setActionError(userErrorMessage(error, t));
      return null;
    } finally {
      setCreating(false);
    }
  };

  const sendMessage = async (rawText: string) => {
    const text = rawText.trim();
    if (!text || turnActive || sending) return;
    setSending(true);
    setSendError(null);
    setFailedText(null);
    setStreamError(null);
    setStreamStatus(null);
    setStreamPlan(null);

    let conversationId = selectedId;
    try {
      if (!conversationId) {
        const created = await api.createConversation();
        conversationId = created.conversation_id;
        resetThread();
        loadedConversationRef.current = conversationId;
        selectedIdRef.current = conversationId;
        setSelectedId(conversationId);
        conversationsQuery.reload();
      }
      const response = await api.sendMessage(conversationId, { message: text, locale });
      conversationsQuery.reload();
      if (selectedIdRef.current !== conversationId) return;
      setMessages((previous) => mergeMessages(previous, [response.message]));
      setActiveRun(response.run);
      setTurnActive(true);
    } catch (error) {
      setSendError(userErrorMessage(error, t));
      setFailedText(text);
    } finally {
      setSending(false);
    }
  };

  const sendRef = useRef(sendMessage);
  sendRef.current = sendMessage;

  const handleRunEvent = (event: ParsedSseEvent) => {
    if (event.name === "status") {
      const zh = typeof event.data.message_zh === "string" ? event.data.message_zh : undefined;
      const en = typeof event.data.message_en === "string" ? event.data.message_en : undefined;
      setStreamStatus(zh || en ? { zh, en } : null);
      return;
    }
    if (event.name === "message") {
      const message = parseMessageDto(event.data);
      if (message) {
        setMessages((previous) => mergeMessages(previous, [message]));
        setStreamStatus(null);
      }
      return;
    }
    if (event.name === "plan") {
      if (record(event.data)) setStreamPlan(event.data);
      return;
    }
    if (event.name === "done") {
      const code = typeof event.data.error === "string" ? event.data.error : "";
      if (code) {
        setStreamError(
          `${userErrorMessage(new ApiError(500, code, ""), t)} (${code})`,
        );
      }
      setTurnActive(false);
      setActiveRun(null);
      setStreamStatus(null);
      conversationQuery.reload();
      conversationsQuery.reload();
      return;
    }
    if (event.name === "error") {
      const code = typeof event.data.code === "string" ? event.data.code : "";
      const friendly = code
        ? userErrorMessage(new ApiError(500, code, ""), t)
        : t("workspace.errorEvent");
      setStreamError(code ? `${friendly} (${code})` : friendly);
      setTurnActive(false);
      setActiveRun(null);
      setStreamStatus(null);
      conversationQuery.reload();
      conversationsQuery.reload();
    }
  };

  const streamUrl =
    activeRun && turnActive
      ? `/api/v1/runs/${encodeURIComponent(activeRun.run_id)}/events`
      : null;
  const stream = useEventStream({
    url: streamUrl,
    eventNames: RUN_EVENT_NAMES,
    onEvent: handleRunEvent,
    shouldClose: (event) => event.name === "done" || event.name === "error",
  });

  const runtime = useExternalStoreRuntime<MessageDto>({
    messages,
    convertMessage: threadMessageFrom,
    isRunning: turnActive || sending,
    isSendDisabled: turnActive || sending || creating,
    onNew: async (appendMessage) => {
      const text = extractAppendText(appendMessage.content);
      if (text) await sendRef.current(text);
    },
  });

  const conversation =
    conversationQuery.data?.conversation.conversation_id === selectedId
      ? conversationQuery.data.conversation
      : null;
  const conversationTitle =
    conversation?.title?.trim() || t("workspace.untitledConversation");
  const planIds = new Set<string>();
  for (const message of messages) {
    for (const part of message.parts) {
      if (part.type === "plan" && part.data && typeof part.data.plan_id === "string") {
        planIds.add(part.data.plan_id);
      }
    }
  }
  const transientPlan =
    streamPlan &&
    typeof streamPlan.plan_id === "string" &&
    !planIds.has(streamPlan.plan_id)
      ? streamPlan
      : null;
  const loadingConversation =
    Boolean(selectedId) &&
    !conversation &&
    conversationQuery.status !== "error";
  const statusText = streamStatus
    ? pickLocalized(locale, streamStatus.zh, streamStatus.en)
    : turnActive
      ? t("workspace.responding")
      : "";

  return (
    <section className="page workspace-page" aria-labelledby="workspace-title">
      <PageHeader
        title={t("workspace.title")}
        description={t("workspace.subtitle")}
        actions={
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => {
              conversationsQuery.reload();
              conversationQuery.reload();
            }}
          >
            <Icon name="refresh" size={16} />
            {t("app.refresh")}
          </button>
        }
      />

      <div className="workspace-layout">
        <aside className="conversation-sidebar" aria-labelledby="conversation-list-title">
          <div className="conversation-sidebar-head">
            <h2 id="conversation-list-title">{t("workspace.conversations")}</h2>
            <button
              type="button"
              className="btn btn-secondary btn-small"
              onClick={() => void createConversation()}
              disabled={creating}
            >
              <Icon name="plus" size={15} />
              {creating ? t("workspace.creatingConversation") : t("workspace.newConversation")}
            </button>
          </div>

          {actionError ? (
            <Alert tone="danger" onDismiss={() => setActionError(null)}>
              {actionError}
            </Alert>
          ) : null}

          {conversationsQuery.initialLoading ? (
            <LoadingState label={t("app.loading")} />
          ) : conversationsQuery.status === "error" ? (
            <ErrorState
              message={userErrorMessage(conversationsQuery.error, t)}
              onRetry={conversationsQuery.reload}
            />
          ) : (conversationsQuery.data?.items.length ?? 0) === 0 ? (
            <p className="sidebar-empty">{t("workspace.noConversations")}</p>
          ) : (
            <nav aria-label={t("workspace.conversations")}>
              <ul className="conversation-list">
                {conversationsQuery.data?.items.map((item) => (
                  <ConversationListItem
                    key={item.conversation_id}
                    conversation={item}
                    selected={item.conversation_id === selectedId}
                    onSelect={() => selectConversation(item.conversation_id)}
                  />
                ))}
              </ul>
            </nav>
          )}
        </aside>

        <section className="conversation-panel" aria-label={t("workspace.title")}>
          <header className="conversation-header">
            <div className="conversation-header-copy">
              <h2>{selectedId ? conversationTitle : t("workspace.noConversationSelected")}</h2>
              {activeRun ? <p className="part-meta mono break-all">{activeRun.run_id}</p> : null}
            </div>
            <div className="conversation-header-tools">
              <ConnectionIndicator status={stream.status} onReconnect={stream.reconnect} />
              <button
                type="button"
                className="icon-button"
                aria-label={t("workspace.refreshConversation")}
                title={t("workspace.refreshConversation")}
                onClick={() => {
                  conversationQuery.reload();
                  conversationsQuery.reload();
                }}
                disabled={!selectedId}
              >
                <Icon name="refresh" size={18} />
              </button>
            </div>
          </header>

          {conversationQuery.status === "error" ? (
            <ErrorState
              message={t("workspace.conversationLoadError")}
              onRetry={conversationQuery.reload}
            />
          ) : loadingConversation ? (
            <div className="thread-viewport">
              <LoadingState />
            </div>
          ) : (
            <AssistantRuntimeProvider runtime={runtime}>
              <ThreadPrimitive.Root className="thread-root">
                <ThreadPrimitive.Viewport
                  className="thread-viewport"
                  aria-label={t("workspace.messageListLabel")}
                >
                  {messages.length === 0 && !turnActive ? (
                    <div className="workspace-empty">
                      <div className="workspace-empty-mark" aria-hidden="true">
                        <Brand compact />
                      </div>
                      <h3>{t("workspace.emptyTitle")}</h3>
                      <p>{t("workspace.emptyBody")}</p>
                      <ul>
                        <li>{t("workspace.emptyBullet1")}</li>
                        <li>{t("workspace.emptyBullet2")}</li>
                        <li>{t("workspace.emptyBullet3")}</li>
                      </ul>
                    </div>
                  ) : (
                    <ThreadPrimitive.Messages>
                      {({ message }) => <MessageRow key={message.id} message={message} />}
                    </ThreadPrimitive.Messages>
                  )}

                  {transientPlan ? (
                    <div className="stream-activity" aria-live="polite">
                      <PlanPart data={transientPlan} />
                    </div>
                  ) : null}
                </ThreadPrimitive.Viewport>

                <div className="composer-dock">
                  {statusText ? (
                    <p className="stream-status" role="status" aria-live="polite">
                      <span className="stream-status-dot" aria-hidden="true" />
                      {statusText}
                    </p>
                  ) : null}

                  {streamError ? (
                    <Alert tone="danger" onDismiss={() => setStreamError(null)}>
                      {streamError}
                    </Alert>
                  ) : null}
                  {sendError ? (
                    <Alert tone="danger" onDismiss={() => setSendError(null)}>
                      <span>{sendError}</span>
                      {failedText ? (
                        <button
                          type="button"
                          className="btn btn-secondary btn-small inline-retry"
                          onClick={() => void sendMessage(failedText)}
                        >
                          {t("app.retry")}
                        </button>
                      ) : null}
                    </Alert>
                  ) : null}

                  <ComposerPrimitive.Root className="composer-form">
                    <label className="visually-hidden" htmlFor="workspace-composer">
                      {t("workspace.composerPlaceholder")}
                    </label>
                    <ComposerPrimitive.Input
                      id="workspace-composer"
                      className="composer-input"
                      placeholder={
                        turnActive || sending
                          ? t("workspace.responding")
                          : t("workspace.composerPlaceholder")
                      }
                      aria-label={t("workspace.composerPlaceholder")}
                      submitMode="enter"
                      rows={1}
                      disabled={turnActive || sending}
                    />
                    <ComposerPrimitive.Send
                      className="btn btn-primary composer-send"
                      aria-label={t("workspace.send")}
                    >
                      <Icon name="send" size={16} />
                      <span>{sending ? t("workspace.sending") : t("workspace.send")}</span>
                    </ComposerPrimitive.Send>
                  </ComposerPrimitive.Root>
                  <p className="composer-hint">
                    {turnActive ? t("workspace.responding") : t("workspace.composerHint")}
                  </p>
                </div>
              </ThreadPrimitive.Root>
            </AssistantRuntimeProvider>
          )}
        </section>
      </div>
    </section>
  );
}
