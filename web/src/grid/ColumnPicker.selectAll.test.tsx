import * as React from "react";

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ColumnPicker } from "./ColumnPicker";
import { COLUMN_CAP, identityColumns, selectAllColumns } from "./columnSelection";

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
      onSelectAll={() => setSelected(selectAllColumns(keys))}
      onClearAll={() => setSelected(identityColumns(keys))}
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

  it("clears back to the identity columns when it is unticked", async () => {
    const user = userEvent.setup();
    render(<Harness initial={["k00", "k01"]} />);

    await user.click(selectAll());
    expect(tickedCount()).toBe(COLUMN_CAP);

    await user.click(selectAll());

    // Row number and company; the ticker lane is pinned and never a key.
    expect(count()).toBe("2 / 40");
    expect(tickedCount()).toBe(2);
    expect(box("No (no)").checked).toBe(true);
    expect(box("Company (company)").checked).toBe(true);
    expect(selectAll().checked).toBe(false);
    // Two of 45 is still partial: the box never claims the panel is empty.
    expect(selectAll().indeterminate).toBe(true);
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
});
