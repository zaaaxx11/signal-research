import * as React from "react";

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ColumnPicker } from "./ColumnPicker";
import { COLUMN_CAP, selectAllColumns } from "./columnSelection";

/**
 * The header's select-all box.
 *
 * The catalogue is 116 keys and a panel holds 40, so "select all" is a cut with
 * a sentence attached, not a switch that can quietly swallow 76 columns. The
 * box is tri-state: ticked when the panel is as full as it can be, mixed while
 * it is partial (it never reads empty, because the identity columns are always
 * in), and unticking it puts the panel back on the identity floor.
 */

/** A catalogue that cannot fit one panel: 43 keys plus row number and company. */
const KEYS = [
  ...Array.from({ length: 43 }, (_, index) => `k${String(index).padStart(2, "0")}`),
  "company",
  "no",
];

/** The screen's wiring, in miniature: one state, written the same way. */
function Harness({
  keys = KEYS,
  initial = [],
}: {
  keys?: readonly string[];
  initial?: readonly string[];
}) {
  const [selected, setSelected] = React.useState<readonly string[]>([...initial]);

  return (
    <ColumnPicker
      open
      onClose={vi.fn()}
      keys={keys}
      selected={selected}
      onToggle={(key) =>
        setSelected((previous) =>
          previous.includes(key) ? previous.filter((entry) => entry !== key) : [...previous, key],
        )
      }
      onSelectAll={(visibleKeys) => {
        setSelected((previous) => {
          const merged = [...new Set([...previous, ...visibleKeys])];
          return selectAllColumns(merged);
        });
      }}
      onClearAll={(visibleKeys) => {
        const toRemove = new Set(visibleKeys);
        setSelected((previous) => previous.filter((k) => !toRemove.has(k)));
      }}
      onReset={vi.fn()}
      custom
      notice={null}
    />
  );
}

function count(): string {
  return screen.getByTestId("column-picker-count").textContent ?? "";
}

function selectAll(): HTMLInputElement {
  return screen.getByRole("checkbox", { name: "Select all columns" }) as HTMLInputElement;
}

function box(name: string): HTMLInputElement {
  return screen.getByRole("checkbox", { name }) as HTMLInputElement;
}

function catalogue(): HTMLElement {
  return screen.getByRole("list", { name: "Catalogue columns" });
}

function tickedCount(): number {
  return within(catalogue())
    .getAllByRole("checkbox")
    .filter((input) => (input as HTMLInputElement).checked).length;
}

describe("ColumnPicker select all", () => {
  it("ticks every offered column up to the cap and says what it left out", async () => {
    const user = userEvent.setup();
    render(<Harness initial={["k00"]} />);

    // One of 45: mixed, not empty.
    expect(selectAll().checked).toBe(false);
    expect(selectAll().indeterminate).toBe(true);
    expect(count()).toBe("1 / 40");
    expect(screen.queryByTestId("column-picker-cap-note")).toBeNull();

    await user.click(selectAll());

    expect(count()).toBe("40 / 40");
    expect(selectAll().checked).toBe(true);
    expect(selectAll().indeterminate).toBe(false);
    expect(tickedCount()).toBe(COLUMN_CAP);
    expect(screen.getByTestId("column-picker-cap-note").textContent).toBe(
      "Showing the first 40 of 45 columns",
    );
    // The cut keeps the identity columns and fills in catalogue order.
    expect(box("No (no)").checked).toBe(true);
    expect(box("Company (company)").checked).toBe(true);
  });

  it("clears every visible column when the box is unticked", async () => {
    const user = userEvent.setup();
    render(<Harness initial={["k00", "k01"]} />);

    await user.click(selectAll());
    expect(tickedCount()).toBe(COLUMN_CAP);

    await user.click(selectAll());

    // All visible keys were removed; the selection is empty.
    expect(count()).toBe("0 / 40");
    expect(selectAll().checked).toBe(false);
    expect(selectAll().indeterminate).toBe(false);
    expect(screen.queryByTestId("column-picker-cap-note")).toBeNull();
  });

  it("is mixed while the selection is partial and checked once it is whole", async () => {
    const user = userEvent.setup();
    render(<Harness keys={["no", "company", "pe"]} initial={["no", "company"]} />);

    expect(selectAll().indeterminate).toBe(true);
    expect(count()).toBe("2 / 40");

    await user.click(box("P/E (pe)"));
    expect(count()).toBe("3 / 40");
    expect(selectAll().checked).toBe(true);
    expect(selectAll().indeterminate).toBe(false);

    // Unticking one column from a whole panel makes it mixed again.
    await user.click(box("P/E (pe)"));
    expect(count()).toBe("2 / 40");
    expect(selectAll().indeterminate).toBe(true);
  });

  it("is reachable from the keyboard and disabled when nothing is offered", async () => {
    const user = userEvent.setup();
    const first = render(<Harness />);

    // The dialog opens with focus in the search box; the select-all box is two
    // Shift+Tabs back, before the list.
    await waitFor(() =>
      expect(document.activeElement).toBe(
        screen.getByRole("searchbox", { name: "Find a column" }),
      ),
    );
    await user.tab({ shift: true });
    await user.tab({ shift: true });
    expect(document.activeElement).toBe(selectAll());

    await user.keyboard(" ");
    expect(count()).toBe("40 / 40");

    first.unmount();
    render(<Harness keys={[]} />);
    expect(selectAll().disabled).toBe(true);
    expect(selectAll().checked).toBe(false);
  });

  it("does not claim a cut when the panel is full of hand-picked columns", () => {
    // The last 40 keys, not the catalogue's first 40: nothing was left out by
    // this control, so the sentence would be false.
    const handPicked = KEYS.filter((key) => key !== "no").slice(-COLUMN_CAP);
    render(<Harness initial={handPicked} />);

    expect(count()).toBe("40 / 40");
    expect(screen.queryByTestId("column-picker-cap-note")).toBeNull();
  });

  it("toggles only the search-matched columns when a query is active", async () => {
    const user = userEvent.setup();
    render(<Harness initial={["k00", "k01"]} />);

    const search = screen.getByRole("searchbox", { name: "Find a column" });
    await user.type(search, "k0");

    // Only k00-k09 are visible (10 keys starting with k0)
    // Current: k00, k01 ticked → partial among the visible set
    expect(selectAll().indeterminate).toBe(true);

    // Click select-all → should tick all 10 visible keys
    await user.click(selectAll());
    expect(count()).toBe("10 / 40");
    // All 10 visible are ticked
    expect(selectAll().checked).toBe(true);
    expect(selectAll().indeterminate).toBe(false);

    // Clear search → full catalogue visible again, select-all should be mixed
    await user.clear(search);
    expect(selectAll().indeterminate).toBe(true);

    // Click select-all with no filter → full panel
    await user.click(selectAll());
    expect(count()).toBe("40 / 40");
  });

  it("select-all is mixed when search results are partially ticked", async () => {
    const user = userEvent.setup();
    render(<Harness keys={["pe", "peg", "price", "market_cap", "beta"]} initial={["pe"]} />);

    const search = screen.getByRole("searchbox", { name: "Find a column" });
    await user.type(search, "p");

    // "p" matches: pe, peg, price, market_cap, beta (all contain "p" in key)
    // Only "pe" is ticked → mixed
    expect(selectAll().indeterminate).toBe(true);
    expect(selectAll().checked).toBe(false);

    // Tick all visible by clicking select-all
    await user.click(selectAll());
    expect(selectAll().checked).toBe(true);
    expect(selectAll().indeterminate).toBe(false);
    // All 5 keys are now ticked
    expect(box("PEG (peg)").checked).toBe(true);
    expect(box("Price (price)").checked).toBe(true);
  });

  it("clears only search-matched columns when deselecting with a query", async () => {
    const user = userEvent.setup();
    render(<Harness initial={["k00", "k01", "k10", "k11"]} />);

    const search = screen.getByRole("searchbox", { name: "Find a column" });
    await user.type(search, "k1");

    // Visible: k10-k19 (10 keys). k00, k01 are NOT visible but still selected.
    // In the visible set: k10, k11 are ticked → partial
    expect(selectAll().indeterminate).toBe(true);

    // Click select-all → all 10 visible (k10-k19) get ticked, plus k00, k01 = 12
    await user.click(selectAll());
    expect(selectAll().checked).toBe(true);
    expect(count()).toBe("12 / 40");

    // Now deselect → only the visible ones (k10-k19) are removed
    await user.click(selectAll());
    // k00, k01 remain selected (they were never visible during the operation)
    expect(count()).toBe("2 / 40");
  });

  it("shows the cap note when search results exceed 40 and all are selected", async () => {
    const user = userEvent.setup();
    // 45 keys, all match the empty query
    render(<Harness initial={[]} />);

    await user.click(selectAll());
    expect(count()).toBe("40 / 40");
    expect(screen.getByTestId("column-picker-cap-note").textContent).toBe(
      "Showing the first 40 of 45 columns",
    );
  });
});
