/**
 * The alert store's promises:
 *
 * * one versioned key, and the document the dialog writes is the one the store
 *   reads back;
 * * `hasAlert` answers for a symbol, not for "the key is non-empty";
 * * a write tells every mounted subscriber, so a bell can fill without a
 *   reload and empty when the last alert is deleted;
 * * a corrupt or foreign document reads as empty rather than throwing.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

import type { PriceAlert } from "./alerts";
import {
  ALERTS_KEY,
  alertsStore,
  countAlerts,
  hasAlert,
  parseAlerts,
  serializeAlerts,
} from "./alerts";

function alert(symbol: string, id: string, value = 250): PriceAlert {
  return {
    id,
    symbol,
    condition: "price_above",
    value,
    note: "",
    createdAt: "2026-11-02T00:00:00.000Z",
  };
}

beforeEach(() => {
  window.localStorage.clear();
  alertsStore.write([]);
});

describe("the alert store", () => {
  it("answers hasAlert for one symbol and counts only that symbol", () => {
    alertsStore.write([alert("AAPL", "aapl-1"), alert("AAPL", "aapl-2"), alert("MSFT", "msft-1")]);

    expect(hasAlert("AAPL")).toBe(true);
    expect(hasAlert("MSFT")).toBe(true);
    expect(hasAlert("NVDA")).toBe(false);
    expect(countAlerts("AAPL", alertsStore.read())).toBe(2);
    expect(countAlerts("NVDA", alertsStore.read())).toBe(0);
  });

  it("notifies subscribers on save and again when the last alert is deleted", () => {
    const listener = vi.fn();
    const off = alertsStore.subscribe(listener);

    alertsStore.write([alert("AAPL", "aapl-1")]);
    expect(listener).toHaveBeenCalledTimes(1);
    expect(hasAlert("AAPL")).toBe(true);

    alertsStore.write([]);
    expect(listener).toHaveBeenCalledTimes(2);
    expect(hasAlert("AAPL")).toBe(false);

    off();
    alertsStore.write([alert("AAPL", "aapl-2")]);
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it("writes the versioned document the page and the dialog share", () => {
    alertsStore.write([alert("AAPL", "aapl-1", 250)]);

    expect(JSON.parse(window.localStorage.getItem(ALERTS_KEY) ?? "null")).toEqual({
      version: 1,
      alerts: [alert("AAPL", "aapl-1", 250)],
    });
    expect(serializeAlerts([])).toBe('{"version":1,"alerts":[]}');
  });

  it("reads a corrupt, foreign or old document as empty", () => {
    for (const junk of [
      "{not json",
      "",
      "null",
      "42",
      '["AAPL"]',
      '{"version":2,"alerts":[]}',
      '{"alerts":[{"id":"a","symbol":"AAPL","condition":"price_above","value":1}]}',
    ]) {
      expect(parseAlerts(junk)).toEqual([]);
    }
    expect(parseAlerts(null)).toEqual([]);
  });
});
