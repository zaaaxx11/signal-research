/**
 * The overview snapshot's contract: six columns, fourteen rows, 84 drawn cells.
 *
 * The count is the point. A grid that quietly shortens itself when a symbol
 * reports less is a grid that hides the absence; this file pins the shape and
 * then checks the two formatting rules that matter — money in compact form and
 * an unreported cell as the em dash.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SnapshotGrid } from "./SnapshotGrid";
import { SNAPSHOT_CELLS, SNAPSHOT_COLUMNS, SNAPSHOT_ROWS, snapshotCell } from "./snapshot";
import { rowPayload, snapshotMetrics } from "./__fixtures__/ticker";

function grid() {
  const rows = rowPayload()["rows"] as Array<Record<string, unknown>>;
  return render(
    <SnapshotGrid sources={{ metrics: snapshotMetrics(), row: rows[0] ?? null }} />,
  );
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
    const cell = screen.getByTestId("snapshot-grid").querySelector('[data-snapshot-cell="market_cap"]');
    expect(cell?.textContent).toBe("$5.18T");

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
    const cell = screen
      .getByTestId("snapshot-grid")
      .querySelector('[data-snapshot-cell="change"]');
    expect(cell?.textContent).toBe("+1.24%");
    expect(cell?.getAttribute("data-tone")).toBe("up");
  });

  it("never turns a reported zero into an em dash", () => {
    const cell = snapshotCell("change", { row: { change: 0 } });
    expect(cell.text).toBe("0.00%");
    expect(cell.absent).toBe(false);
    expect(cell.tone).toBeNull();
  });
});
