import { describe, expect, it } from "vitest";

import {
  MAX_CLOSES,
  MOVER_LIMIT,
  blockState,
  homePath,
  missingReason,
  readHome,
} from "./home";
import { homeDocument, moverRow } from "./__fixtures__/home";

/**
 * The parser's promises, without a DOM:
 *
 * * seven blocks, one document;
 * * `null` is a missing block and `[]` is an answer — they never collapse;
 * * forty movers, never forty-one;
 * * absent numbers stay absent;
 * * a feed URL a browser must not follow never reaches an anchor.
 */

describe("readHome", () => {
  it("reads every block of the document", () => {
    const report = readHome(homeDocument());

    expect(report.asOf).toBe("2026-10-31T20:00:00Z");
    expect(report.indices).toHaveLength(5);
    expect(report.indices?.[0]).toMatchObject({ symbol: "^GSPC", label: "S&P 500", price: 7580.07 });
    expect(report.indices?.[0]?.closes).toHaveLength(30);
    expect(report.breadth).toMatchObject({ universe: 5576, advancing: 1557, below50: 3472 });
    expect(report.sparks).toHaveLength(3);
    expect(report.movers?.total).toBe(MOVER_LIMIT);
    expect(report.map?.tiles).toBe(600);
    expect(report.map?.tilesTotal).toBe(5573);
    expect(report.map?.sectors).toHaveLength(7);
    expect(report.news).toHaveLength(3);
    expect(report.filings).toHaveLength(5);
    expect(report.unavailable).toEqual([]);
  });

  it("reads the flat array the server sends, grouped by its own field", () => {
    // The live document carries one array of forty rows, each naming its group.
    // A reader that only understood an object of four arrays would show an
    // empty block against a perfect payload.
    const flat = [
      { symbol: "ONEG", name: "OneConstruction", price: 1.63, change_pct: 108.15, group: "gainers" },
      { symbol: "OSPN", name: "OneSpan", price: 25.66, change_pct: 27.34, group: "new_high" },
      { symbol: "ENGS", name: "Energys", price: 4.53, change_pct: 1.34, group: "overbought" },
      { symbol: "ABOS", name: "Acumen", price: 0.21, change_pct: -90.83, group: "new_low" },
    ];
    const report = readHome(homeDocument({ movers: flat }));

    expect(report.movers?.gainers.map((row) => row.symbol)).toEqual(["ONEG"]);
    expect(report.movers?.newHigh.map((row) => row.symbol)).toEqual(["OSPN"]);
    expect(report.movers?.overbought.map((row) => row.symbol)).toEqual(["ENGS"]);
    expect(report.movers?.newLow.map((row) => row.symbol)).toEqual(["ABOS"]);
    expect(report.movers?.total).toBe(4);
  });

  it("keeps a missing block null and names it, rather than inventing an empty one", () => {
    const report = readHome(homeDocument({ filings: null, unavailable: ["filings"] }));

    expect(report.filings).toBeNull();
    expect(report.unavailable).toEqual(["filings"]);
    // The other six blocks still read: one dead source is not a dead document.
    expect(report.indices).toHaveLength(5);
    expect(report.news).toHaveLength(3);
  });

  it("treats an empty array as an answer, not as a failure", () => {
    const report = readHome(
      homeDocument({
        news: [],
        movers: { gainers: [], new_high: [], overbought: [], new_low: [] },
        unavailable: [],
      }),
    );

    expect(report.news).toEqual([]);
    expect(report.movers).not.toBeNull();
    expect(report.movers?.total).toBe(0);
    expect(report.unavailable).toEqual([]);
  });

  it("enforces the forty-row budget even when the wire sends more", () => {
    const flood = Array.from({ length: 40 }, (_, index) => moverRow(`G${index}`, 20 - index));
    const report = readHome(
      homeDocument({
        movers: { gainers: flood, new_high: flood, overbought: flood, new_low: flood },
      }),
    );

    expect(report.movers?.gainers).toHaveLength(40);
    expect(report.movers?.newHigh).toHaveLength(0);
    expect(report.movers?.total).toBe(MOVER_LIMIT);
  });

  it("keeps an absent number absent instead of turning it into zero", () => {
    const report = readHome(
      homeDocument({
        sparks: [{ symbol: "HALT", name: "Halted Inc.", price: null, change_pct: null, volume: null, closes: [] }],
      }),
    );

    expect(report.sparks?.[0]).toMatchObject({ price: null, changePct: null, volume: null });
  });

  it("refuses a feed URL a browser must not follow", () => {
    const report = readHome(
      homeDocument({
        news: [
          { headline: "bad", url: "javascript:alert(1)", source: "x", published_at: null },
          { headline: "good", url: "https://example.com/a", source: "y", published_at: null },
        ],
      }),
    );

    expect(report.news?.[0]?.url).toBeNull();
    expect(report.news?.[1]?.url).toBe("https://example.com/a");
  });

  it("drops rows it cannot key rather than rendering undefined", () => {
    const report = readHome(
      homeDocument({
        indices: [{ label: "no symbol" }, { symbol: "^OK", label: "Fine", price: 1, change_pct: 0, closes: [] }],
        filings: [{ owner: "No Symbol" }, { symbol: "AAPL", owner: "", role: "", filed: "" }],
      }),
    );

    expect(report.indices).toHaveLength(1);
    expect(report.indices?.[0]?.symbol).toBe("^OK");
    expect(report.filings).toHaveLength(2);
  });

  it("survives a body that is not a document", () => {
    for (const body of [null, undefined, 42, "nope", []]) {
      const report = readHome(body);
      expect(report.indices).toBeNull();
      expect(report.movers).toBeNull();
      expect(report.unavailable).toEqual([]);
    }
  });

  it("drops a map sector with no readable row and keeps a null average", () => {
    const report = readHome(
      homeDocument({
        map: {
          sectors: [
            { id: "Empty", value: -1, symbols: [{ s: "", c: 10, v: 1 }] },
            { id: "Utilities", value: null, symbols: [{ s: "NEE", c: 150.9, v: 0 }] },
          ],
          tiles: 2,
          tiles_total: 5573,
        },
      }),
    );

    expect(report.map?.sectors).toHaveLength(1);
    expect(report.map?.sectors[0]?.id).toBe("Utilities");
    expect(report.map?.sectors[0]?.value).toBeNull();
  });

  it("trims a long series from the front, keeping the newest point", () => {
    const long = Array.from({ length: MAX_CLOSES + 20 }, (_, index) => index);
    const report = readHome(
      homeDocument({ sparks: [{ symbol: "LONG", name: "", price: 1, change_pct: 0, volume: 1, closes: long }] }),
    );

    const closes = report.sparks?.[0]?.closes ?? [];
    expect(closes).toHaveLength(MAX_CLOSES);
    expect(closes[closes.length - 1]).toBe(MAX_CLOSES + 19);
  });
});

describe("blockState", () => {
  it("is loading, then ready, and never both", () => {
    expect(blockState("loading", null, "news", "Headlines").kind).toBe("loading");
    expect(blockState("ready", readHome(homeDocument()), "news", "Headlines").kind).toBe("ready");
  });

  it("names a failed block and its reason", () => {
    const report = readHome(homeDocument({ filings: null, unavailable: ["filings"] }));
    const state = blockState("ready", report, "filings", "Filings");

    expect(state.kind).toBe("missing");
    expect(state.reason).toBe("Filings unavailable — the source did not answer this request.");
  });

  it("says a block was absent from the document when it was not named", () => {
    const report = readHome(homeDocument({ map: null }));

    expect(missingReason(report, "map", "Map")).toBe(
      "Map unavailable — the response carried no map block.",
    );
  });

  it("turns a transport failure into one sentence for every block", () => {
    expect(blockState("error", null, "breadth", "Breadth")).toMatchObject({
      kind: "error",
      reason: "The dashboard did not load.",
    });
  });
});

describe("homePath", () => {
  it("is one root-absolute path with the region named", () => {
    expect(homePath()).toBe("/market/home?region=us");
    expect(homePath("us")).toBe("/market/home?region=us");
  });
});
