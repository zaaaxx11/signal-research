/**
 * The options chain data layer and its pure formatters.
 *
 * `parseOptions` reads the `/engine/options/{symbol}` document: every 200
 * carries `delayed_minutes: 15`, a `selected_expiry`, an `expiry_dates` list
 * and the flat `strikes` for the selected expiry. A greek the exchange could
 * not compute is `null`, never `0`. The formatters are the precision contract
 * the table must not guess at — delta/gamma/theta/vega at four decimals with a
 * sign, implied volatility as a percent, premium as money — asserted without a
 * DOM.
 */

import { describe, expect, it } from "vitest";

import { optionsPayload } from "./__fixtures__/ticker";
import {
  formatGreek,
  formatImpliedVol,
  formatPremium,
  parseOptions,
  sortByStrike,
  strikesForExpiry,
} from "./options";

describe("parseOptions", () => {
  it("reads the chain document", () => {
    const chain = parseOptions(optionsPayload());
    expect(chain).not.toBeNull();
    expect(chain?.symbol).toBe("AAPL");
    expect(chain?.delayedMinutes).toBe(15);
    expect(chain?.selectedExpiry).toBe("2026-11-04");
    expect(chain?.asOf).toBe("2026-11-02T15:01:17Z");
    expect(chain?.expiryDates).toEqual(["2026-11-04", "2026-11-06"]);
    expect(chain?.strikes.length).toBe(4);
  });

  it("keeps a greek null, never inventing a zero", () => {
    const chain = parseOptions(optionsPayload({ nullGamma: true }));
    const noGamma = chain?.strikes.find((s) => s.strike === 290);
    expect(noGamma?.gamma).toBeNull();
  });

  it("is null when the body is not a chain document", () => {
    expect(parseOptions(null)).toBeNull();
    expect(parseOptions({})).toBeNull();
    expect(parseOptions({ symbol: "AAPL" })).toBeNull();
  });
});

describe("strikesForExpiry", () => {
  it("returns only the contracts for the chosen expiry", () => {
    const chain = parseOptions(optionsPayload({ thirdExpiry: true }));
    if (!chain) throw new Error("fixture did not parse");
    const strikes = strikesForExpiry(chain, "2026-11-06");
    expect(strikes.map((s) => s.strike)).toEqual([285]);
  });

  it("defaults to the selected expiry when none is given", () => {
    const chain = parseOptions(optionsPayload());
    if (!chain) throw new Error("fixture did not parse");
    const strikes = strikesForExpiry(chain);
    expect(strikes.map((s) => s.strike)).toEqual([275, 290]);
  });
});

describe("sortByStrike", () => {
  it("orders ascending regardless of input order", () => {
    const chain = parseOptions(optionsPayload({ shuffled: true }));
    if (!chain) throw new Error("fixture did not parse");
    const strikes = strikesForExpiry(chain, chain.selectedExpiry);
    const sorted = [...strikes].sort(sortByStrike);
    expect(sorted.map((s) => s.strike)).toEqual([275, 290]);
  });
});

describe("formatters", () => {
  it("renders a greek signed at four decimals", () => {
    expect(formatGreek(0.9877)).toBe("+0.9877");
    expect(formatGreek(-0.0312)).toBe("-0.0312");
    expect(formatGreek(0)).toBe("+0.0000");
  });

  it("renders a missing greek as the placeholder", () => {
    expect(formatGreek(null)).toBe("—");
    expect(formatGreek(undefined)).toBe("—");
  });

  it("renders implied volatility as a percent", () => {
    expect(formatImpliedVol(1.361)).toBe("136.10%");
    expect(formatImpliedVol(0.1425)).toBe("14.25%");
    expect(formatImpliedVol(null)).toBe("—");
  });

  it("renders premium as money", () => {
    expect(formatPremium(62.35)).toBe("$62.35");
    expect(formatPremium(1.5)).toBe("$1.50");
    expect(formatPremium(null)).toBe("—");
  });
});