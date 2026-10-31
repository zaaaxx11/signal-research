import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LIVE_BREADTH } from "./__fixtures__/breadth";
import { SectorBlock } from "./SectorBlock";
import type { SectorBucket } from "./report";

/**
 * The sector block is presentational, so these tests mount it directly: no
 * fetch, no loading state — just the document in, the labelled list out.
 *
 * The adversarial cases live here: a null average (must print `n/a`, must sort
 * last, must not be painted as a direction), a label long enough to break a
 * strip (must clip, must stay readable), and the sign-versus-colour invariant.
 */

function bucket(overrides: Partial<SectorBucket>): SectorBucket {
  return { name: "Finance", avg_change_pct: -1.1, count: 10, advancing: 4, declining: 6, ...overrides };
}

const THREE = [
  bucket({ name: "Finance", avg_change_pct: -1.0998, count: 1610, advancing: 452, declining: 1039 }),
  bucket({ name: "Energy Minerals", avg_change_pct: 0.6927, count: 102, advancing: 67, declining: 34 }),
  bucket({ name: "Utilities", avg_change_pct: 0.0199, count: 119, advancing: 68, declining: 50 }),
];

function names(): string[] {
  return screen.getAllByTestId("sector-name").map((cell) => cell.textContent ?? "");
}

describe("SectorBlock", () => {
  it("is a real list of labelled cells, not a drawing of one", () => {
    render(<SectorBlock sectors={THREE} />);

    expect(screen.getByRole("heading", { name: "Sectors" })).toBeTruthy();
    expect(screen.getByRole("list")).toBeTruthy();
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
    // Each cell's numbers carry real labels; the visible legend states the
    // column order once, the way a table header would.
    expect(screen.getAllByText("names")).toHaveLength(3);
    expect(screen.getAllByText("advancing")).toHaveLength(3);
    expect(screen.getAllByText("declining")).toHaveLength(3);
    expect(screen.getByText(/avg · names · adv \/ dec/)).toBeTruthy();
  });

  it("sorts by average change, strongest first", () => {
    render(<SectorBlock sectors={THREE} />);

    expect(names()).toEqual(["Energy Minerals", "Utilities", "Finance"]);
    expect(screen.getAllByTestId("sector-avg").map((cell) => cell.textContent)).toEqual([
      "+0.69%",
      "+0.02%",
      "-1.10%",
    ]);
  });

  it("prints each sector's own count, advancers and decliners", () => {
    render(<SectorBlock sectors={THREE} />);

    expect(screen.getAllByTestId("sector-count").map((cell) => cell.textContent)).toEqual([
      "102",
      "119",
      "1,610",
    ]);
    expect(screen.getAllByTestId("sector-adv")[2]?.textContent).toBe("452");
    expect(screen.getAllByTestId("sector-dec")[2]?.textContent).toBe("1,039");
  });

  it("renders every sector of the live snapshot, and no more", () => {
    render(<SectorBlock sectors={LIVE_BREADTH.sectors} />);

    expect(screen.getAllByRole("listitem")).toHaveLength(21);
    expect(screen.getByText(/21 sectors · sorted by average change/)).toBeTruthy();
  });

  it("renders a null average as n/a and keeps it out of the sort", () => {
    render(
      <SectorBlock
        sectors={[
          bucket({ name: "Unmeasured", avg_change_pct: null, count: 5000, advancing: 0, declining: 0 }),
          bucket({ name: "Measured", avg_change_pct: -0.5, count: 2, advancing: 1, declining: 1 }),
        ]}
      />,
    );

    // The unmeasured bucket is also the biggest; size must not float it up.
    expect(names()).toEqual(["Measured", "Unmeasured"]);

    const cells = screen.getAllByTestId("sector-avg");
    expect(cells[1]?.textContent).toBe("n/a");
    // Neutral ink, not a direction: `n/a` is not a flat market.
    expect(cells[1]?.className).not.toMatch(/text-(up|down|flat)/);
    expect(cells[1]?.getAttribute("title")).toContain("no row in this sector");
    expect(screen.getAllByTestId("sector-cell")[1]?.getAttribute("data-sign")).toBe("absent");
  });

  it("never carries the sign in colour alone", () => {
    render(<SectorBlock sectors={LIVE_BREADTH.sectors} />);

    const cells = screen.getAllByTestId("sector-cell");
    for (const cell of cells) {
      const average = cell.querySelector('[data-testid="sector-avg"]');
      const text = average?.textContent ?? "";
      const tokens = average?.className ?? "";

      if (tokens.includes("text-up")) expect(text.startsWith("+")).toBe(true);
      if (tokens.includes("text-down")) expect(text.startsWith("-")).toBe(true);
      if (tokens.includes("text-flat")) expect(text).toBe("0.00%");
    }
  });

  it("paints an exactly flat average as flat, with no sign and no direction", () => {
    render(<SectorBlock sectors={[bucket({ avg_change_pct: 0 })]} />);

    const average = screen.getByTestId("sector-avg");
    expect(average.textContent).toBe("0.00%");
    expect(average.className).toContain("text-flat");
    expect(screen.getByTestId("sector-cell").getAttribute("data-sign")).toBe("0");
  });

  it("clips a very long sector name instead of widening the strip", () => {
    const long = "Consumer Non-Durables, Agricultural Commodities and Unclassified Holdings (Provider Label)";
    render(<SectorBlock sectors={[bucket({ name: long })]} />);

    const label = screen.getByTestId("sector-name");
    // The text is intact (selectable, searchable); only its box is clipped.
    expect(label.textContent).toBe(long);
    expect(label.getAttribute("title")).toBe(long);
    expect(label.className).toContain("truncate");

    const cell = screen.getByTestId("sector-cell");
    expect(cell.className).toContain("overflow-hidden");
    expect(cell.className).toContain("min-w-0");
    expect(screen.getByTestId("sector-avg").textContent).toBe("-1.10%");
  });

  it("names the server's null bucket instead of dropping it", () => {
    render(<SectorBlock sectors={[bucket({ name: null, avg_change_pct: -2 })]} />);

    expect(screen.getByTestId("sector-name").textContent).toBe("Unspecified");
  });

  it("explains an empty sector list rather than showing an empty grid", () => {
    render(<SectorBlock sectors={[]} />);

    expect(screen.getByTestId("sector-empty").textContent).toContain("No sector detail");
    expect(screen.queryByRole("list")).toBeNull();
    expect(screen.getByText(/0 sectors/)).toBeTruthy();
  });

  it("counts the rows the scan could not attribute, when it reports any", () => {
    render(<SectorBlock sectors={THREE} sectorMissing={12} />);

    expect(screen.getByText(/12 unattributed/)).toBeTruthy();
  });
});
