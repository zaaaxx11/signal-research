/**
 * Set alert: the small form the row or ticker page opens for one symbol.
 *
 * The symbol is already known — a row's bell names it, the ticker page's
 * header names it — so the dialog has no ticker search. It asks only for the
 * condition, the value and an optional note, and lists the alerts already
 * saved in this browser.
 */

import * as React from "react";

import { Icon } from "@/components/Icon";
import { cn } from "@/lib/utils";

import {
  ALERT_CONDITIONS,
  ALERT_LABELS,
  alertsStore,
  describeAlert,
  nextAlertId,
  type AlertCondition,
  type PriceAlert,
} from "./alerts";
import { DialogShell } from "./DialogShell";

export interface AlertDialogProps {
  open: boolean;
  onClose: () => void;
  /** The selected symbol, or the first row's when nothing is selected. */
  symbol: string | null;
}

export function AlertDialog({ open, onClose, symbol }: AlertDialogProps) {
  const [target, setTarget] = React.useState(symbol ?? "");
  const [condition, setCondition] = React.useState<AlertCondition>("price_above");
  const [value, setValue] = React.useState("");
  const [note, setNote] = React.useState("");
  /**
   * The saved list, read from the one store rather than a copy of it.
   *
   * A copy is what made the row's bell stand still after a save: the dialog
   * wrote storage, the grid had already rendered, and nothing told it. Reading
   * the same store through `useSyncExternalStore` means the dialog, the row and
   * the ticker band cannot disagree about whether this symbol has an alert.
   */
  const alerts = React.useSyncExternalStore(
    alertsStore.subscribe,
    alertsStore.read,
    alertsStore.read,
  );
  const [error, setError] = React.useState<string | null>(null);
  const [saved, setSaved] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!open) return;
    setTarget(symbol ?? "");
    setValue("");
    setNote("");
    setError(null);
    setSaved(null);
  }, [open, symbol]);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const name = target.trim().toUpperCase();
    if (name.length === 0) {
      setError("Which symbol should the alert watch?");
      return;
    }
    const number = Number(value);
    if (!Number.isFinite(number)) {
      setError("The alert needs a number to compare against.");
      return;
    }
    const alert: PriceAlert = {
      id: nextAlertId(name),
      symbol: name,
      condition,
      value: number,
      note: note.trim().slice(0, 120),
      createdAt: new Date().toISOString(),
    };
    const next = [...alerts, alert].slice(-50);
    alertsStore.write(next);
    setValue("");
    setNote("");
    setError(null);
    setSaved(`${name} · ${ALERT_LABELS[condition]} ${number}`);
  };

  const remove = (id: string) => {
    const next = alerts.filter((alert) => alert.id !== id);
    alertsStore.write(next);
    // The "Saved: …" line describes an alert that no longer exists.
    setSaved(null);
  };

  return (
    <DialogShell
      open={open}
      onClose={onClose}
      size="sm"
      title="Set alert"
      description="Kept in this browser — this deployment has no mail transport."
    >
      <form onSubmit={submit} className="flex flex-col gap-2">
        <div className="flex items-baseline gap-2 rounded-sm border border-line bg-surface-raised px-2 py-1.5">
          <span className="font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">Symbol</span>
          <span data-testid="alert-target" className="font-mono text-data font-semibold text-fg">
            {target || "—"}
          </span>
          <span className="ml-auto text-data text-fg-subtle">Named by the row or page</span>
        </div>

        <div className="flex items-end gap-2">
          <label className="flex min-w-0 flex-1 flex-col gap-0.5">
            <span className="font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
              Condition
            </span>
            <select
              aria-label="Condition"
              value={condition}
              onChange={(event) => setCondition(event.target.value as AlertCondition)}
              className="h-7 rounded-sm border border-line-control bg-surface-sunken px-1.5 text-data text-fg"
            >
              {ALERT_CONDITIONS.map((option) => (
                <option key={option} value={option}>
                  {ALERT_LABELS[option]}
                </option>
              ))}
            </select>
          </label>
          <label className="flex w-24 flex-col gap-0.5">
            <span className="font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">Value</span>
            <input
              aria-label="Value"
              type="number"
              inputMode="decimal"
              value={value}
              onChange={(event) => setValue(event.target.value)}
              placeholder="200"
              className="h-7 rounded-sm border border-line-control bg-surface-sunken px-1.5 text-data text-fg"
            />
          </label>
        </div>

        <label className="flex flex-col gap-0.5">
          <span className="font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
            Note (optional)
          </span>
          <input
            aria-label="Note"
            value={note}
            onChange={(event) => setNote(event.target.value.slice(0, 120))}
            className="h-7 rounded-sm border border-line-control bg-surface-sunken px-1.5 text-data text-fg"
          />
        </label>

        {error ? (
          <p role="alert" className="text-data text-alert">
            {error}
          </p>
        ) : null}

        <div className="flex items-center gap-2">
          <button
            type="submit"
            className="flex h-7 items-center gap-1.5 rounded-sm border border-line-control bg-surface-raised px-2 text-ui text-fg transition-colors hover:border-brand"
          >
            <Icon name="bell" />
            Save alert
          </button>
          <span aria-live="polite" className="text-data text-fg-subtle">
            {saved ? `Saved: ${saved}` : ""}
          </span>
        </div>
      </form>

      <div className="mt-3 border-t border-line pt-2">
        <p className="mb-1 font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
          Saved alerts · {alerts.length}
        </p>
        {alerts.length === 0 ? (
          <p className="text-data text-fg-subtle">No alerts yet.</p>
        ) : (
          <ul aria-label="Saved alerts" className="flex max-h-[140px] flex-col gap-1 overflow-y-auto">
            {alerts.map((alert) => (
              <li
                key={alert.id}
                className={cn(
                  "flex items-center gap-2 rounded-sm border border-line bg-surface-raised px-2 py-1",
                )}
              >
                <span className="min-w-0 flex-1 truncate text-data text-fg" title={describeAlert(alert)}>
                  {describeAlert(alert)}
                  {alert.note ? <span className="ml-1 text-fg-subtle">· {alert.note}</span> : null}
                </span>
                <button
                  type="button"
                  onClick={() => remove(alert.id)}
                  aria-label={`Delete alert: ${describeAlert(alert)}`}
                  title="Delete this alert"
                  className="flex h-5 w-5 items-center justify-center rounded-sm text-fg-muted transition-colors hover:bg-row-hover hover:text-fg"
                >
                  <Icon name="x" size={12} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </DialogShell>
  );
}
