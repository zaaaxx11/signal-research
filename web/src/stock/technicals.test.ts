/**
 * The technicals document's pure rules: what the endpoint said, which unit
 * each key means, and how the value is written.
 *
 * The DOM tests prove the block paints these; this file proves the formatting
 * itself — especially the two facts that are easy to get wrong and expensive to
 * see. A percent is already in points (5.83 means 5.83 %, never 583 %), and a
 * missing value is the em dash, not a zero.
 */

import { describe, expect, it } from "vitest";

import {
  MA_WINDOWS,
  RISK_ROWS,
  formatMetric,
  parseRisk,
  parseTechnicals,
} from "./technicals";
import { riskPayload, technicalsPayload } from "./__fixtures__/ticker";

describe("parseTechnicals", () => {
  it("rejects a body with no values at all", () => {
    expect(parseTechnicals({ symbol: "AAPL" }, "AAPL")).toBeNull();
    expect(parseTechnicals(null, "AAPL")).toBeNull();
  });

  it("keeps the endpoint's own units and session date", () => {
    const document = parseTechnicals(technicalsPayload(), "AAPL");
    expect(document).not.toBeNull();
    expect(document?.asOf).toBe("2026-10-31");
    expect(document?.units["sma_13"]).toBe("price");
    expect(document?.units["ema_200"]).toBe("price");
    expect(document?.units["rsi_14"]).toBe("index");
    expect(document?.units["perf_mtd"]).toBe("percent");
  });

  it("falls back to the key's own unit when the body omits units", () => {
    const body = technicalsPayload();
    delete body["units"];
    const document = parseTechnicals(body, "AAPL");
    expect(document?.units["sma_50"]).toBe("price");
    expect(document?.units["rsi_14"]).toBe("index");
    expect(document?.units["momentum_10"]).toBe("percent");
  });
});

describe("parseRisk", () => {
  it("carries the declared risk units when the endpoint sends them", () => {
    const document = parseRisk(riskPayload(), "AAPL");
    expect(document?.units["alpha"]).toBe("percent");
    expect(document?.units["beta"]).toBe("ratio");
    expect(document?.units["max_drawdown"]).toBe("percent");
  });

  it("fills the fallback map when the endpoint sends no units", () => {
    const body = riskPayload();
    delete body["units"];
    const document = parseRisk(body, "AAPL");
    expect(document?.units["alpha"]).toBe("percent");
    expect(document?.units["volatility"]).toBe("percent");
    expect(document?.units["beta"]).toBe("ratio");
    expect(document?.units["r_squared"]).toBe("ratio");
  });

  it("rejects a body without values", () => {
    expect(parseRisk({ symbol: "AAPL" }, "AAPL")).toBeNull();
  });
});

describe("formatMetric", () => {
  it("prints a signed percent with its sign and direction", () => {
    expect(formatMetric(5.83, "percent", { signed: true })).toEqual({
      text: "+5.83%",
      tone: "up",
      absent: false,
    });
    expect(formatMetric(-18.35, "percent", { signed: true })).toEqual({
      text: "-18.35%",
      tone: "down",
      absent: false,
    });
  });

  it("leaves an unsigned percent unsigned", () => {
    expect(formatMetric(21.43, "percent")).toEqual({
      text: "21.43%",
      tone: null,
      absent: false,
    });
  });

  it("prints prices and ratios at two decimals", () => {
    expect(formatMetric(341.2, "price").text).toBe("341.20");
    expect(formatMetric(1.08, "ratio").text).toBe("1.08");
    expect(formatMetric(58.42, "index").text).toBe("58.42");
  });

  it("prints an em dash for null, undefined and non-finite values", () => {
    for (const value of [null, undefined, Number.NaN, Number.POSITIVE_INFINITY]) {
      expect(formatMetric(value, "price")).toEqual({
        text: "—",
        tone: null,
        absent: true,
      });
    }
  });

  it("keeps a reported zero as zero, with no claimed direction", () => {
    expect(formatMetric(0, "percent", { signed: true })).toEqual({
      text: "0.00%",
      tone: null,
      absent: false,
    });
  });

  it("names the five moving-average windows and the six risk rows", () => {
    expect([...MA_WINDOWS]).toEqual([13, 21, 50, 100, 200]);
    expect(RISK_ROWS.map((row) => row.key)).toEqual([
      "alpha",
      "beta",
      "volatility",
      "sharpe",
      "max_drawdown",
      "r_squared",
    ]);
  });
});
