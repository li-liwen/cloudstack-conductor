import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Brand } from "../components/Brand";
import { Icon } from "../components/Icon";
import { LanguageSwitcher } from "../components/LanguageSwitcher";
import { Alert, PageHeader } from "../components/Primitives";
import { StatusBadge } from "../components/StatusBadge";
import { useConfig } from "../config/ConfigProvider";
import { useI18n } from "../i18n/I18nProvider";

type RelayState = "connecting" | "active" | "closed" | "error";

export function ConsolePage() {
  const { t } = useI18n();
  const { config } = useConfig();
  const { sessionId = "" } = useParams();
  const enabled = config.features.console;
  const [state, setState] = useState<RelayState>("connecting");
  const [closeCode, setCloseCode] = useState<number | null>(null);
  const [messageCount, setMessageCount] = useState(0);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!enabled || !sessionId || typeof WebSocket === "undefined") {
      setState(enabled ? "error" : "closed");
      return undefined;
    }
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    const url = `${scheme}://${window.location.host}/api/v1/console/ws/${encodeURIComponent(sessionId)}`;
    let socket: WebSocket;
    try {
      socket = new WebSocket(url);
    } catch {
      setState("error");
      return undefined;
    }
    setState("connecting");
    setCloseCode(null);
    setMessageCount(0);

    socket.onopen = () => setState("active");
    socket.onmessage = () => {
      // The relay frames are intentionally not decoded in this build.
      setMessageCount((count) => count + 1);
    };
    socket.onerror = () => setState("error");
    socket.onclose = (event) => {
      setCloseCode(event.code);
      setState(event.code === 1000 || event.wasClean ? "closed" : "error");
    };

    return () => {
      socket.onopen = null;
      socket.onmessage = null;
      socket.onerror = null;
      socket.onclose = null;
      try {
        socket.close(1000, "page closed");
      } catch {
        // Ignore close failures while navigating away.
      }
    };
  }, [enabled, sessionId, attempt]);

  const statusLabel =
    state === "active"
      ? t("console.active")
      : state === "connecting"
        ? t("console.connecting")
        : state === "closed"
          ? t("console.closed")
          : t("console.error");

  return (
    <div className="console-shell">
      <header className="console-header">
        <Link to="/" className="brand-link" aria-label={t("nav.brandHome")}>
          <Brand />
        </Link>
        <div className="console-header-actions">
          <LanguageSwitcher id="console-locale" />
          <Link className="btn btn-secondary" to="/resources">
            <Icon name="arrowLeft" size={16} />
            {t("console.back")}
          </Link>
        </div>
      </header>

      <main className="console-main">
        <PageHeader
          title={t("console.title")}
          description={t("console.subtitle")}
          actions={<StatusBadge label={statusLabel} tone={state === "active" ? "success" : state === "error" ? "danger" : "neutral"} />}
        />

        {!enabled ? <Alert tone="warning">{t("console.featureDisabled")}</Alert> : null}

        <section className="console-stage" aria-labelledby="console-stage-heading">
          <h2 id="console-stage-heading" className="visually-hidden">
            {t("console.title")}
          </h2>
          <div className={`console-placeholder is-${state}`}>
            <span className="console-pulse" aria-hidden="true" />
            <p className="console-state" role="status" aria-live="polite">
              {statusLabel}
            </p>
            {state === "active" ? (
              <p className="console-active-note">{t("console.connectedHelp")}</p>
            ) : state === "error" ? (
              <p className="console-active-note">
                {closeCode === 4401 ? t("console.unavailable") : t("console.wsError")}
              </p>
            ) : (
              <p className="console-active-note">{t("console.relayNote")}</p>
            )}
            <div className="console-actions">
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => setAttempt((value) => value + 1)}
                disabled={!enabled || !sessionId}
              >
                <Icon name="refresh" size={16} />
                {t("console.reconnect")}
              </button>
            </div>
          </div>
        </section>

        <div className="detail-grid">
          <section className="panel" aria-labelledby="console-session-heading">
            <h2 id="console-session-heading">{t("console.session")}</h2>
            <dl className="definition-grid">
              <div className="definition-item">
                <dt>{t("console.session")}</dt>
                <dd className="mono break-all">{sessionId || "—"}</dd>
              </div>
              <div className="definition-item">
                <dt>{t("console.statusLabel")}</dt>
                <dd>{statusLabel}</dd>
              </div>
              <div className="definition-item">
                <dt>{t("console.messages")}</dt>
                <dd>{messageCount}</dd>
              </div>
              {closeCode !== null ? (
                <div className="definition-item">
                  <dt>{t("console.connectionCode")}</dt>
                  <dd className="mono">{closeCode}</dd>
                </div>
              ) : null}
            </dl>
            <p className="help-text">{t("console.sessionHelp")}</p>
            <p className="help-text">{t("console.sessionNote")}</p>
          </section>

          <section className="panel" aria-labelledby="console-notes-heading">
            <h2 id="console-notes-heading">{t("console.notes")}</h2>
            <ul className="security-notes">
              <li>{t("console.relayNote")}</li>
              <li>{t("console.connectedHelp")}</li>
              <li>{t("console.expires")}</li>
              <li>{t("console.expiresHelp")}</li>
              <li>{t("console.sessionNote")}</li>
            </ul>
          </section>
        </div>
      </main>
    </div>
  );
}
