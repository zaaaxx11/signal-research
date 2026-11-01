/**
 * Price alerts, kept in this browser.
 *
 * The product has no accounts and no alert service, so an alert here is a note
 * the reader writes for themselves: symbol, a condition, a value. It is stored
 * under one versioned key, read back tolerantly, and shown in the same dialog
 * that writes it — the honest version of "set alert" on a deployment that
 * cannot send mail.
 */

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
export function parseAlerts(raw: string | null): PriceAlert[] {
  if (raw === null || raw.length === 0) return [];
  let body: unknown;
  try {
    body = JSON.parse(raw) as unknown;
  } catch {
    return [];
  }
  if (!isRecord(body) || body["version"] !== 1 || !Array.isArray(body["alerts"])) return [];
  const alerts: PriceAlert[] = [];
  for (const entry of body["alerts"]) {
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

export function serializeAlerts(alerts: readonly PriceAlert[]): string {
  return JSON.stringify({ version: 1, alerts });
}

let sequence = 0;
export function resetAlertSequence(): void {
  sequence = 0;
}

export function nextAlertId(symbol: string): string {
  sequence += 1;
  return `${symbol.toLowerCase()}-${sequence}`;
}

export function readAlerts(): PriceAlert[] {
  try {
    return parseAlerts(window.localStorage.getItem(ALERTS_KEY));
  } catch {
    return [];
  }
}

export function writeAlerts(alerts: readonly PriceAlert[]): void {
  try {
    window.localStorage.setItem(ALERTS_KEY, serializeAlerts(alerts));
  } catch {
    // A browser that refuses storage keeps the alert for this session only.
  }
}

/** One alert as the list prints it. */
export function describeAlert(alert: PriceAlert): string {
  return `${alert.symbol} · ${ALERT_LABELS[alert.condition]} ${alert.value}`;
}
