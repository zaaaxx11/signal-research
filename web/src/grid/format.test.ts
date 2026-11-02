import { render } from "@testing-library/react";
import * as React from "react";
import { describe, expect, it } from "vitest";

import { CellValue } from "./cellRenderers";
import { columnSpec } from "./columnMeta";
import {
  NULL_PLACEHOLDER,
  formatCell,
  formatCompact,
  formatDateValue,
  formatMoney,
  formatNumber,
  formatPercent,
  formatSignedPercent,
  isAbsent,
  signOf,
} from "./format";

/**
 * The formatting contract the grid depends on:
 *
 * * absent is not zero (and zero is not absent);
 * * a delta always carries its sign in the text, not only in the colour;
 * * compact money uses $B/$M/$K (and $T, because market cap reaches it).
 */

describe("formatCompact", () => {
  it("uses T/B/M/K suffixes", () => {
    expect(formatCompact(5029592198492)).toBe("5.03T");
    expect(formatCompact(4931824084531)).toBe("4.93T");
    expect(formatCompact(128930000000)).toBe("128.93B");
    expect(formatCompact(67840642)).toBe("67.84M");
    expect(formatCompact(150000)).toBe("150K");
    expect(formatCompact(5600)).toBe("5.6K");
  });

  it("keeps the sign outside the currency mark", () => {
    expect(formatMoney(-344.63)).toBe("-$344.63");
    expect(formatMoney(344.63)).toBe("$344.63");
    expect(formatMoney(-1230000000, true)).toBe("-$1.23B");
    expect(formatMoney(0)).toBe("$0.00");
  });

  it("formats percentages to two places", () => {
    expect(formatPercent(48.652915559002)).toBe("48.65%");
    expect(formatPercent(-1.2266773)).toBe("-1.23%");
    expect(formatPercent(0)).toBe("0.00%");
    expect(formatSignedPercent(1.2266)).toBe("+1.23%");
    expect(formatSignedPercent(-1.2266)).toBe("-1.23%");
    expect(formatSignedPercent(0)).toBe("0.00%");
  });

  it("reads unix seconds as a calendar date", () => {
    expect(formatDateValue(1793655000)).toBe("2026-11-02");
    expect(formatDateValue("2026-08-10")).toBe("2026-08-10");
    expect(formatDateValue("2026-08-10T00:00:00Z")).toBe("2026-08-10T00:00:00Z");
  });

  it("signs are three-valued, so flat is not down", () => {
    expect(signOf(2)).toBe(1);
    expect(signOf(-2)).toBe(-1);
    expect(signOf(0)).toBe(0);
    expect(signOf(-0)).toBe(0);
  });
});

describe("formatCell", () => {
  const money = columnSpec("price");
  const delta = columnSpec("change");
  const percent = columnSpec("gross_margin");
  const integer = columnSpec("volume");
  const date = columnSpec("earnings_date");

  it("renders every flavour of absent as the placeholder", () => {
    for (const value of [null, undefined, "", Number.NaN, Number.POSITIVE_INFINITY]) {
      expect(isAbsent(value)).toBe(true);
      const cell = formatCell(value, money);
      expect(cell.absent).toBe(true);
      expect(cell.text).toBe(NULL_PLACEHOLDER);
      expect(cell.sign).toBeNull();
    }
  });

  it("renders a real zero as a zero, not as absent", () => {
    expect(formatCell(0, money)).toMatchObject({ text: "$0.00", absent: false });
    expect(formatCell(0, delta)).toMatchObject({ text: "0.00%", absent: false, sign: 0 });
    expect(formatCell(0, percent)).toMatchObject({ text: "0.00%", absent: false });
    expect(formatCell(0, integer)).toMatchObject({ text: "0", absent: false });
    // A *timestamp* of 0 is the one zero that is not a value: it is the
    // "unknown" sentinel, and 1970-01-01 would be a plausible-looking lie (F9).
    expect(formatCell(0, date)).toMatchObject({ absent: true });
  });

  it("keeps the sign on a delta", () => {
    expect(formatCell(-1.2266773, delta)).toMatchObject({ text: "-1.23%", sign: -1 });
    expect(formatCell(4.1807, delta)).toMatchObject({ text: "+4.18%", sign: 1 });
  });

  it("compacts money and integers", () => {
    expect(formatCell(5029592198492, columnSpec("market_cap")).text).toBe("$5.03T");
    expect(formatCell(67840642, integer).text).toBe("67.84M");
    expect(formatCell(150000, columnSpec("employees")).text).toBe("150K");
  });

  it("does not turn a string in a numeric column into zero", () => {
    const cell = formatCell("n/a", money);
    expect(cell.text).toBe("n/a");
    expect(cell.absent).toBe(false);
  });

  it("keeps text and badge values verbatim", () => {
    expect(formatCell("Electronic Technology", columnSpec("sector")).text).toBe(
      "Electronic Technology",
    );
    expect(formatCell("Yes", columnSpec("optionable")).text).toBe("Yes");
  });

  it("has a title for every non-absent cell", () => {
    expect(formatCell(344.63, money).title).toBe("$344.63");
    expect(formatCell(5029592198492, columnSpec("market_cap")).title).toContain("USD");
  });
});

/**
 * F5 — a non-zero value below the last printed digit.
 *
 * `$0.00` for a real `0.004` is a number printed as a different number. The
 * declaration is "`<` the smallest print", never the zero it rounds to; a true
 * zero keeps printing as zero, and a null keeps printing as absent.
 */
describe("sub-cent money (F5)", () => {
  const money = columnSpec("price");
  const fourDp = { ...columnSpec("price"), precision: 4 };
  const marketCap = columnSpec("market_cap");

  it("prints a sub-cent value as '<$0.01', never '$0.00'", () => {
    expect(formatCell(0.004, money)).toMatchObject({ text: "<$0.01", absent: false });
    expect(formatCell(1e-7, money)).toMatchObject({ text: "<$0.01", absent: false });
    expect(formatCell(-0.004, money)).toMatchObject({ text: "-<$0.01", absent: false });
    // A value that really prints at its last digit is not flagged.
    expect(formatCell(0.005, money).text).toBe("$0.01");
    expect(formatCell(0.01, money).text).toBe("$0.01");
  });

  it("uses the column's own precision: '<$0.0001' at 4 dp", () => {
    expect(formatCell(0.00004, fourDp)).toMatchObject({ text: "<$0.0001", absent: false });
    expect(formatCell(0.0001, fourDp).text).toBe("$0.0001");
  });

  it("keeps a true zero as zero", () => {
    expect(formatCell(0, money).text).toBe("$0.00");
    expect(formatCell(-0, money).text).toBe("$0.00");
    expect(formatCell(0, fourDp).text).toBe("$0.0000");
    expect(formatCell(0, marketCap).text).toBe("$0");
  });

  it("keeps an absent value absent", () => {
    for (const value of [null, undefined, "", Number.NaN]) {
      const cell = formatCell(value, money);
      expect(cell.absent).toBe(true);
      expect(cell.text).toBe(NULL_PLACEHOLDER);
      expect(cell.text).not.toContain("<");
    }
  });

  it("flags sub-cent compact money instead of printing '$0'", () => {
    expect(formatCell(1e-7, marketCap)).toMatchObject({ text: "<$0.01", absent: false });
    expect(formatCell(-1e-7, marketCap).text).toBe("-<$0.01");
    expect(formatCell(1e6, marketCap).text).toBe("$1M");
  });

  it("keeps the money title in step with the text", () => {
    expect(formatCell(0.004, money).title).toBe("<$0.01");
  });
});

/** F9 — a `0` timestamp is a sentinel, not 1970-01-01. */
describe("a zero timestamp (F9)", () => {
  const earnings = columnSpec("earnings_date");

  it("renders absent rather than a plausible date", () => {
    expect(formatCell(0, earnings)).toMatchObject({ text: NULL_PLACEHOLDER, absent: true });
    expect(formatCell("0", earnings).absent).toBe(true);
    expect(formatDateValue(0)).toBe(NULL_PLACEHOLDER);
  });

  it("still renders a real timestamp", () => {
    expect(formatCell(1793655000, earnings).text).toBe("2026-11-02");
    expect(formatDateValue(1793655000000)).toBe("2026-11-02");
  });
});

/**
 * F10 — the sign may never disagree with the magnitude it is printed beside.
 *
 * `-0.001` prints `-0.00%` and `▲ +0.00%`: a fall, and a zero, and a rise, all
 * in one cell. When the magnitude rounds to zero at the column's precision
 * there is no direction to claim, so the sign is 0 — the same value the cell's
 * arrow and screen-reader word read from.
 */
describe("a delta below its printed precision (F10)", () => {
  const delta = columnSpec("change");

  it("claims no direction it cannot print", () => {
    expect(formatCell(-0.001, delta)).toMatchObject({ text: "0.00%", sign: 0 });
    expect(formatCell(0.004, delta)).toMatchObject({ text: "0.00%", sign: 0 });
    expect(formatSignedPercent(-0.001)).toBe("0.00%");
    expect(formatSignedPercent(0.004)).toBe("0.00%");
  });

  it("keeps the sign wherever the magnitude is printed", () => {
    expect(formatCell(-1.2266773, delta)).toMatchObject({ text: "-1.23%", sign: -1 });
    expect(formatCell(1.2266773, delta)).toMatchObject({ text: "+1.23%", sign: 1 });
    expect(formatCell(-0.005, delta)).toMatchObject({ text: "-0.01%", sign: -1 });
  });
});

/** F13 — one formatter per precision, not one per cell. */
describe("formatter allocation (F13)", () => {
  /** Count `new Intl.NumberFormat(...)` without breaking the constructor. */
  function countConstructions(run: () => void): number {
    const Original = Intl.NumberFormat;
    let constructed = 0;
    const Wrapped = function (
      this: unknown,
      locales?: string | string[],
      options?: Intl.NumberFormatOptions,
    ) {
      constructed += 1;
      return new Original(locales, options);
    } as unknown as typeof Intl.NumberFormat;
    Wrapped.supportedLocalesOf = Original.supportedLocalesOf.bind(Original);
    Object.assign(Intl, { NumberFormat: Wrapped });
    try {
      run();
    } finally {
      Object.assign(Intl, { NumberFormat: Original });
    }
    return constructed;
  }

  it("constructs at most one formatter per precision, not one per call", () => {
    // Warm the cache for a precision nothing else uses, then spend it.
    formatNumber(1.5, 9);
    const constructed = countConstructions(() => {
      for (let index = 0; index < 50; index += 1) formatNumber(index + 0.25, 9);
    });
    expect(constructed).toBe(0);
    expect(formatNumber(1234.5, 9)).toBe("1,234.500000000");
  });

  it("still formats every distinct precision correctly through the cache", () => {
    expect(formatNumber(1.005, 2)).toBe("1.01");
    expect(formatNumber(1.005, 0)).toBe("1");
    expect(formatNumber(1.005, 3)).toBe("1.005");
  });
});

/**
 * A column that prints a *label* for a raw value — the exchange cell.
 *
 * The rule is the split: the reader sees `NASDAQ`, the wire, the title and the
 * DOM keep `XNAS`. The title and `data-value` are what make the label safe — a
 * copy, a test or a future export can recover the code, and the tooltip never
 * loses it either. Geometry is structural (one span, one line), so a label
 * cannot change how tall or how wide a row is.
 */
describe("a column with a display formatter", () => {
  const exchange = columnSpec("exchange");

  it("prints NASDAQ and keeps XNAS in the title", () => {
    expect(formatCell("XNAS", exchange)).toMatchObject({
      text: "NASDAQ",
      title: "NASDAQ (XNAS)",
      absent: false,
    });
    expect(formatCell("XNYS", exchange).text).toBe("NYSE");
  });

  it("prints an unknown code as itself and adds no parentheses", () => {
    expect(formatCell("XWAR", exchange)).toMatchObject({ text: "XWAR", title: "XWAR" });
  });

  it("leaves a name alone", () => {
    // The detail payload's vocabulary reaches the grid on the global tab.
    expect(formatCell("Nasdaq", exchange)).toMatchObject({ text: "Nasdaq", title: "Nasdaq" });
  });

  it("still renders an absent value as the em dash, never as a label", () => {
    expect(formatCell(null, exchange)).toMatchObject({
      text: NULL_PLACEHOLDER,
      absent: true,
    });
    expect(formatCell("", exchange).absent).toBe(true);
  });

  it("draws one span, keeps the code in data-value, and adds no wrapper", () => {
    const { container } = render(React.createElement(CellValue, { spec: exchange, value: "XNAS" }));
    const spans = container.querySelectorAll("span");
    expect(spans).toHaveLength(1);
    const span = spans[0] as HTMLElement;
    expect(span.textContent).toBe("NASDAQ");
    expect(span.getAttribute("data-value")).toBe("XNAS");
    expect(span.getAttribute("data-kind")).toBe("text");
    expect(span.getAttribute("title")).toBe("NASDAQ (XNAS)");
    // One line, left aligned: the same box every text cell has, so the label
    // cannot wrap into two lines and change the row's height.
    expect(span.className).toContain("block truncate text-left");
  });

  it("adds no data-value to a text column without a formatter", () => {
    const sector = columnSpec("sector");
    expect(sector.display).toBeUndefined();
    const { container } = render(
      React.createElement(CellValue, { spec: sector, value: "Electronic Technology" }),
    );
    const span = container.querySelector("span") as HTMLElement;
    expect(span.hasAttribute("data-value")).toBe(false);
    expect(span.textContent).toBe("Electronic Technology");
  });
});

describe("Computed columns formatting", () => {
  it("prints moving averages and momentum as prices at two decimals", () => {
    expect(formatCell(343.8211, columnSpec("ema_13")).text).toBe("$343.82");
    expect(formatCell(321.9515, columnSpec("sma_100")).text).toBe("$321.95");
    // `Mom` is a price difference, not a percent: the sign belongs to the
    // number, and no `%` may appear in the cell.
    const momentum = formatCell(-0.88, columnSpec("momentum"));
    expect(momentum.text).toBe("-$0.88");
    expect(momentum.text).not.toContain("%");
  });

  it("prints week-to-date performance as a signed percent at two decimals", () => {
    expect(formatCell(-0.5408, columnSpec("perf_wtd")).text).toBe("-0.54%");
    expect(formatCell(1.2, columnSpec("perf_wtd")).text).toBe("+1.20%");
    expect(formatCell(0, columnSpec("perf_wtd")).text).toBe("0.00%");
  });

  it("keeps absent computed cells absent rather than zero", () => {
    for (const key of ["ema_13", "momentum", "perf_wtd"]) {
      const cell = formatCell(null, columnSpec(key));
      expect(cell.absent).toBe(true);
      expect(cell.text).toBe("—");
    }
  });

  it("draws a computed percent cell with its sign in the DOM", () => {
    const { container } = render(
      React.createElement(CellValue, { spec: columnSpec("perf_wtd"), value: 1.2 }),
    );
    const cell = container.querySelector('[data-sign="1"]') as HTMLElement;
    // The sign is in the text and the arrow is aria-hidden: colour is never the
    // only carrier of direction.
    expect(cell.textContent).toContain("+1.20%");
    expect(cell.getAttribute("title")).toBe("+1.20%");
    expect(container.querySelector('[aria-hidden="true"]')?.textContent).toBe("▲");
  });
});
