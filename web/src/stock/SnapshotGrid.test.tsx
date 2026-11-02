/**
 * The overview snapshot's contract: six columns, fourteen rows, 84 drawn cells.
 *
 * The count is the point. A grid that quietly shortens itself when a symbol
 * reports less is a grid that hides the absence; this file pins the shape and
 * then checks the two formatting rules that matter — money in compact form and
 * an unreported cell as the em dash.
 *
 * Since `/engine/snapshot/{symbol}` landed, the grid has a second source and a
 * stricter one: when the document is present it is the only source, and its
 * `units` decide the shape of a number. That last rule is what stops 0.7654
 * (a ratio) from being painted as 76.54.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SnapshotGrid } from "./SnapshotGrid";
import {
  SNAPSHOT_CELLS,
  SNAPSHOT_COLUMNS,
  SNAPSHOT_ROWS,
  parseSnapshot,
  snapshotCell,
  type SnapshotDocument,
  type SnapshotUnit,
} from "./snapshot";
import { rowPayload, snapshotMetrics, snapshotPayload } from "./__fixtures__/ticker";

function grid() {
  return render(<SnapshotGrid sources={{ metrics: snapshotMetrics(), row: row() }} />);
}

/** The `POST /screens/select` row the page already holds. */
function row(): Record<string, unknown> {
  const rows = rowPayload()["rows"] as Array<Record<string, unknown>>;
  return rows[0] ?? {};
}

/** The endpoint's document, optionally with one value replaced. */
function document(overrides: Record<string, unknown> = {}): SnapshotDocument {
  const parsed = parseSnapshot(snapshotPayload());
  if (parsed === null) throw new Error("the snapshot fixture is not a document");
  return { ...parsed, values: { ...parsed.values, ...overrides } };
}

function cell(key: string): Element | null {
  return screen.getByTestId("snapshot-grid").querySelector(`[data-snapshot-cell="${key}"]`);
}

describe("SnapshotGrid", () => {
  it("draws six columns and 84 cells from the detail payload", () => {
    grid();
    const root = screen.getByTestId("snapshot-grid");
    expect(root.querySelectorAll("[data-snapshot-column]")).toHaveLength(6);
    expect(root.querySelectorAll("[data-snapshot-cell]")).toHaveLength(84);
    expect(root.getAttribute("data-columns")).toBe("6");
    expect(root.getAttribute("data-rows")).toBe("14");
  });

  it("keeps the catalogue honest: six columns of fourteen unique keys", () => {
    expect(SNAPSHOT_COLUMNS).toHaveLength(6);
    const keys = SNAPSHOT_COLUMNS.flatMap((column) => column.keys);
    expect(keys).toHaveLength(SNAPSHOT_ROWS * 6);
    expect(new Set(keys).size).toBe(SNAPSHOT_CELLS);
  });

  it("prints a reported market cap compactly and an absent cell as an em dash", () => {
    grid();
    const marketCap = screen
      .getByTestId("snapshot-grid")
      .querySelector('[data-snapshot-cell="market_cap"]');
    expect(marketCap?.textContent).toBe("$5.18T");

    // No dividend-growth key exists in the fundamentals fixture: the row is
    // still drawn, and its value is the placeholder — never 0, never blank.
    const absent = screen
      .getByTestId("snapshot-grid")
      .querySelector('[data-snapshot-cell="dividend_growth_3y"]');
    expect(absent?.textContent).toBe("—");
    expect(absent?.getAttribute("data-absent")).toBe("true");
  });

  it("carries the change's direction in the sign and the tone", () => {
    grid();
    const change = screen
      .getByTestId("snapshot-grid")
      .querySelector('[data-snapshot-cell="change"]');
    expect(change?.textContent).toBe("+1.24%");
    expect(change?.getAttribute("data-tone")).toBe("up");
  });

  it("never turns a reported zero into an em dash", () => {
    const zero = snapshotCell("change", { row: { change: 0 } });
    expect(zero.text).toBe("0.00%");
    expect(zero.absent).toBe(false);
    expect(zero.tone).toBeNull();
  });

  it("fills the grid from the snapshot document, not from the older sources", () => {
    render(
      <SnapshotGrid
        sources={{ snapshot: document(), metrics: snapshotMetrics(), row: row() }}
      />,
    );
    // The fixture's fundamentals carry no P/E; the snapshot document does.
    expect(cell("pe")?.textContent).toBe("34.12");
    expect(cell("forward_pe")?.textContent).toBe("29.40");
    expect(cell("ev_ebitda")?.textContent).toBe("24.18");
    expect(cell("market_cap")?.textContent).toBe("$5.18T");
    expect(cell("optionable")?.textContent).toBe("Yes");
    expect(cell("dividend_ex_date")?.textContent).toBe("2026-11-07");
  });

  it("prints an em dash only for a genuinely null value", () => {
    render(<SnapshotGrid sources={{ snapshot: document(), metrics: snapshotMetrics() }} />);
    // `missing` on the document, and a stale metric that must not fill it in.
    expect(cell("dividend_growth_3y")?.textContent).toBe("—");
    expect(cell("dividend_growth_3y")?.getAttribute("data-absent")).toBe("true");
  });

  it("honours a ratio unit: 0.7654 prints 0.77, never 76.54", () => {
    render(<SnapshotGrid sources={{ snapshot: document() }} />);
    expect(cell("debt_to_equity")?.textContent).toBe("0.77");
    expect(cell("quick_ratio")?.textContent).toBe("0.93");
    expect(cell("current_ratio")?.textContent).toBe("1.00");
    expect(cell("beta")?.textContent).toBe("1.08");
  });

  it("prints every ratio with two decimals, including RSI's one-decimal column", () => {
    const base = document({ rsi: 55.3486, beta: 0.6782 });
    render(<SnapshotGrid sources={{ snapshot: base }} />);
    expect(cell("rsi")?.textContent).toBe("55.35");
    expect(cell("beta")?.textContent).toBe("0.68");
  });

  it("honours a percent unit: the same fact carries its mark, not a ratio shape", () => {
    const base = document({ debt_to_equity: 76.54 });
    const units: Record<string, SnapshotUnit> = { ...base.units, debt_to_equity: "percent" };
    render(<SnapshotGrid sources={{ snapshot: { ...base, units } }} />);
    expect(cell("debt_to_equity")?.textContent).toBe("76.54%");
    expect(cell("payout_ratio")?.textContent).toBe("11.93%");
  });

  it("keeps the bool unit through the parser and prints Yes/No from the value", () => {
    const parsed = parseSnapshot(snapshotPayload());
    expect(parsed?.units["optionable"]).toBe("bool");

    const yes = snapshotCell("optionable", { snapshot: parsed ?? undefined });
    expect(yes.text).toBe("Yes");
    expect(yes.absent).toBe(false);

    const no = snapshotCell("optionable", {
      snapshot: { ...(parsed as SnapshotDocument), values: { optionable: false } },
    });
    expect(no.text).toBe("No");
    expect(no.absent).toBe(false);
  });

  it("treats an empty index list as an answer, not an em dash", () => {
    const base = document({ index_membership: [] });
    const cell = snapshotCell("index_membership", { snapshot: base });
    expect(cell.text).toBe("None");
    expect(cell.absent).toBe(false);
  });

  it("names a failed snapshot read in one line above the grid", () => {
    render(
      <SnapshotGrid
        sources={{}}
        notice="Snapshot unavailable — snapshot_not_found: not deployed. Showing fundamentals and the latest scan row."
      />,
    );
    const notice = screen.getByTestId("snapshot-notice");
    expect(notice.textContent).toContain("snapshot_not_found");
    expect(notice.parentElement?.firstElementChild).toBe(notice);
    expect(notice.parentElement?.querySelectorAll("[data-snapshot-cell]")).toHaveLength(84);
  });
});
