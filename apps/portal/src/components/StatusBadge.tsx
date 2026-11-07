import { useI18n } from "../i18n/I18nProvider";
import { statusLabel, statusTone, type StatusTone } from "../lib/status";

export function StatusBadge({
  state,
  label,
  tone,
}: {
  state?: string | null;
  label?: string;
  tone?: StatusTone;
}) {
  const { t } = useI18n();
  const resolvedTone = tone ?? statusTone(state);
  const text = label ?? statusLabel(t, state);
  return <span className={`status-badge tone-${resolvedTone}`}>{text}</span>;
}
