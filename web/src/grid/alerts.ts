/**
 * Price alerts, kept in this browser.
 *
 * The product has no accounts and no alert service, so an alert here is a note
 * the reader writes for themselves: symbol, a condition, a value. It is stored
 * under one versioned key, read back tolerantly, and shown in the same dialog
 * that writes it — the honest version of "set alert" on a deployment that
 * cannot send mail.
 *
 * One store, not two readers: the dialog writes through `alertsStore`, and a
 * row's bell reads the same store through `useSyncExternalStore`. That is what
 * makes the bell fill on save and empty on the last delete without a reload —
 * there is no second copy of "has an alert" that could disagree.
 */

import { createLocalStore } from "@/lists/storage";

/** The one key this feature owns. */
export const ALERTS_KEY = "ztockz.alerts.v1";

export const ALERT_CONDITIONS = [
  "price_above",
  "price_below",
  "change_above",
  "change_below",
  "volume_above",
] as const;

export type AlertCondition = (typeof ALERT_CONDITIONS)[number];

export const ALERT_LABELS: Record<AlertCondition, string> = {
  price_above: "Price above",
  price_below: "Price below",
  change_above: "Change % above",
  change_below: "Change % below",
  volume_above: "Volume above",
};

export interface PriceAlert {
  id: string;
  symbol: string;
  condition: AlertCondition;
  value: number;
  note: string;
  createdAt: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Everything the stored document carries that this build can still use. */
export function decodeAlerts(raw: unknown): PriceAlert[] {
  if (!isRecord(raw) || raw["version"] !== 1 || !Array.isArray(raw["alerts"])) return [];
  const alerts: PriceAlert[] = [];
  for (const entry of raw["alerts"]) {
    if (!isRecord(entry)) continue;
    const id = typeof entry["id"] === "string" ? entry["id"] : null;
    const symbol = typeof entry["symbol"] === "string" ? entry["symbol"].trim().toUpperCase() : null;
    const condition = entry["condition"];
    const value = entry["value"];
    if (id === null || symbol === null || symbol.length === 0) continue;
    if (typeof condition !== "string" || !(ALERT_CONDITIONS as readonly string[]).includes(condition)) {
      continue;
    }
    if (typeof value !== "number" || !Number.isFinite(value)) continue;
    alerts.push({
      id,
      symbol,
      condition: condition as AlertCondition,
      value,
      note: typeof entry["note"] === "string" ? entry["note"].slice(0, 120) : "",
      createdAt: typeof entry["createdAt"] === "string" ? entry["createdAt"] : "",
    });
  }
  return alerts;
}

/** The text form; a hand-edited key reads as empty, not as a crash. */
export function parseAlerts(raw: string | null): PriceAlert[] {
  if (raw === null || raw.length === 0) return [];
  try {
    return decodeAlerts(JSON.parse(raw) as unknown);
  } catch {
    return [];
  }
}

/** The document the key holds — `version` first, as every older build wrote it. */
export function alertsDocument(alerts: readonly PriceAlert[]): {
  version: 1;
  alerts: PriceAlert[];
} {
  return { version: 1, alerts: [...alerts] };
}

export function serializeAlerts(alerts: readonly PriceAlert[]): string {
  return JSON.stringify(alertsDocument(alerts));
}

let sequence = 0;
export function resetAlertSequence(): void {
  sequence = 0;
}

export function nextAlertId(symbol: string): string {
  sequence += 1;
  return `${symbol.toLowerCase()}-${sequence}`;
}

/**
 * The one store every surface reads.
 *
 * `decodeAlerts`/`alertsDocument` are the codec pair, so a write still stores
 * `{version: 1, alerts}` and a `storage` event from another tab still resets
 * the cache before notifying. `hasAlert` is the question a bell asks.
 */
export const alertsStore = createLocalStore<PriceAlert[]>(
  ALERTS_KEY,
  decodeAlerts,
  alertsDocument,
);

/** Does this symbol have at least one saved alert? */
export function hasAlert(
  symbol: string,
  alerts: readonly PriceAlert[] = alertsStore.read(),
): boolean {
  return countAlerts(symbol, alerts) > 0;
}

/** How many alerts this symbol has — the number the bell's label states. */
export function countAlerts(symbol: string, alerts: readonly PriceAlert[]): number {
  const wanted = symbol.trim().toUpperCase();
  return alerts.filter((alert) => alert.symbol === wanted).length;
}

/** One alert as the list prints it. */
export function describeAlert(alert: PriceAlert): string {
  return `${alert.symbol} · ${ALERT_LABELS[alert.condition]} ${alert.value}`;
}
