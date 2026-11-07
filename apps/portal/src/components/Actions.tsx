import { useState, type ReactNode } from "react";
import { useI18n } from "../i18n/I18nProvider";
import { userErrorMessage } from "../lib/errors";
import { Icon } from "./Icon";

export function ConfirmButton({
  label,
  message,
  confirmLabel,
  cancelLabel,
  busyLabel,
  onConfirm,
  variant = "danger",
  disabled = false,
}: {
  label: ReactNode;
  message: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  busyLabel?: string;
  onConfirm: () => Promise<void> | void;
  variant?: "danger" | "secondary" | "primary";
  disabled?: boolean;
}) {
  const { t } = useI18n();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!confirming) {
    return (
      <button
        type="button"
        className={`btn btn-${variant}`}
        disabled={disabled}
        onClick={() => {
          setError(null);
          setConfirming(true);
        }}
      >
        {label}
      </button>
    );
  }

  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      await onConfirm();
      setConfirming(false);
    } catch (caught) {
      setError(userErrorMessage(caught, t));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="confirm-inline"
      role="group"
      aria-label={typeof label === "string" ? label : t("app.confirm")}
      onKeyDown={(event) => {
        if (event.key === "Escape" && !busy) {
          event.stopPropagation();
          setConfirming(false);
        }
      }}
    >
      <p className="confirm-message">{message}</p>
      {error ? (
        <p className="field-error" role="alert">
          {error}
        </p>
      ) : null}
      <div className="confirm-actions">
        <button type="button" className={`btn btn-${variant}`} disabled={busy} onClick={() => void confirm()}>
          {busy ? busyLabel ?? t("app.working") : confirmLabel ?? t("app.confirm")}
        </button>
        <button
          type="button"
          className="btn btn-secondary"
          disabled={busy}
          onClick={() => setConfirming(false)}
        >
          {cancelLabel ?? t("app.cancel")}
        </button>
      </div>
    </div>
  );
}

async function copyText(value: string): Promise<void> {
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return;
  }
  const area = document.createElement("textarea");
  area.value = value;
  area.setAttribute("readonly", "true");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  const copied = document.execCommand("copy");
  area.remove();
  if (!copied) throw new Error("copy failed");
}

export function CopyButton({
  value,
  label,
  copiedLabel,
}: {
  value: string;
  label: string;
  copiedLabel: string;
}) {
  const [status, setStatus] = useState<"idle" | "copied" | "error">("idle");
  const { t } = useI18n();

  const handleCopy = async () => {
    try {
      await copyText(value);
      setStatus("copied");
    } catch {
      setStatus("error");
    }
    window.setTimeout(() => setStatus("idle"), 2000);
  };

  return (
    <span className="copy-wrap">
      <button type="button" className="btn btn-secondary btn-small" onClick={() => void handleCopy()}>
        <Icon name="copy" size={15} />
        {status === "copied" ? copiedLabel : label}
      </button>
      <span className="visually-hidden" aria-live="polite">
        {status === "copied" ? copiedLabel : status === "error" ? t("error.generic") : ""}
      </span>
    </span>
  );
}
