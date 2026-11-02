import type * as React from "react";

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ColumnPicker } from "./ColumnPicker";

/**
 * The picker's contract: every key is offered and toggles, the count is the
 * panel's count, and a refusal is on screen rather than swallowed.
 */

const KEYS = ["price", "pe", "peg", "market_cap"];

/** The ten extra keys the picker offers in its own Computed group. */
const COMPUTED = [
  "ema_13",
  "ema_21",
  "ema_50",
  "ema_100",
  "ema_200",
  "sma_13",
  "sma_21",
  "sma_100",
  "momentum",
  "perf_wtd",
];

function setup(overrides: Partial<React.ComponentProps<typeof ColumnPicker>> = {}) {
  const onToggle = vi.fn();
  const onReset = vi.fn();
  const onClose = vi.fn();
  render(
    <ColumnPicker
      open
      onClose={onClose}
      keys={KEYS}
      selected={["price"]}
      onToggle={onToggle}
      onSelectAll={vi.fn()}
      onClearAll={vi.fn()}
      onReset={onReset}
      custom={false}
      notice={null}
      {...overrides}
    />,
  );
  return { onToggle, onReset, onClose };
}

describe("ColumnPicker", () => {
  it("lists every catalogue key and reports the selection against the cap", () => {
    setup();
    const dialog = screen.getByTestId("column-picker");
    const list = within(dialog).getByRole("list", { name: "Catalogue columns" });
    expect(within(list).getAllByRole("checkbox")).toHaveLength(KEYS.length);
    expect(within(dialog).getByTestId("column-picker-count").textContent).toBe("1 / 40");
    // The header's select-all box is not one of the catalogue's rows.
    expect(within(dialog).getByRole("checkbox", { name: "Select all columns" })).toBeTruthy();
    expect((within(dialog).getByRole("checkbox", { name: /Price/ }) as HTMLInputElement).checked).toBe(true);
    expect((within(dialog).getByRole("checkbox", { name: /P\/E/ }) as HTMLInputElement).checked).toBe(false);
  });

  it("adds and removes by key", async () => {
    const { onToggle } = setup();
    const user = userEvent.setup();
    await user.click(screen.getByRole("checkbox", { name: /P\/E \(pe\)/ }));
    expect(onToggle).toHaveBeenCalledWith("pe");
    await user.click(screen.getByRole("checkbox", { name: /Price \(price\)/ }));
    expect(onToggle).toHaveBeenLastCalledWith("price");
  });

  it("finds a key by its label or its exact name", async () => {
    setup();
    const user = userEvent.setup();
    const search = screen.getByRole("searchbox", { name: "Find a column" });

    await user.type(search, "market");
    const list = screen.getByRole("list", { name: "Catalogue columns" });
    expect(within(list).getAllByRole("checkbox")).toHaveLength(1);
    expect(screen.getByRole("checkbox", { name: /Market Cap/ })).toBeTruthy();

    await user.clear(search);
    await user.type(search, "peg");
    expect(within(list).getAllByRole("checkbox")).toHaveLength(1);
  });

  it("shows the cap sentence it is given", () => {
    setup({ notice: "A panel holds at most 40 columns; remove one before adding “beta”." });
    expect(screen.getByTestId("column-picker-notice").textContent).toContain("at most 40");
  });

  it("resets only when the selection is custom, and closes on Escape", async () => {
    const user = userEvent.setup();
    const first = setup();
    expect((screen.getByRole("button", { name: "Reset to view" }) as HTMLButtonElement).disabled).toBe(true);
    await user.click(screen.getByRole("button", { name: "Reset to view" }));
    expect(first.onReset).not.toHaveBeenCalled();

    await user.keyboard("{Escape}");
    expect(first.onClose).toHaveBeenCalled();
    first.onClose.mockClear();

    // A custom selection offers the reset, and it reaches the shell.
    setup({ custom: true, selected: ["price", "pe"] });
    const dialogs = screen.getAllByTestId("column-picker");
    const reset = within(dialogs[dialogs.length - 1] as HTMLElement).getByRole("button", {
      name: "Reset to view",
    });
    expect((reset as HTMLButtonElement).disabled).toBe(false);
  });
});

describe("ColumnPicker — Computed group", () => {
  it("lists exactly the ten wired keys, every one enabled, and no per-symbol group", () => {
    setup({ computedKeys: COMPUTED });
    const dialog = screen.getByTestId("column-picker");
    const group = screen.getByTestId("column-picker-computed-group");
    const list = within(group).getByRole("list", { name: "Computed columns" });
    const boxes = within(list).getAllByRole("checkbox") as HTMLInputElement[];

    expect(within(group).getByText("Computed")).toBeTruthy();
    expect(boxes).toHaveLength(10);
    expect(boxes.map((box) => box.getAttribute("aria-label"))).toEqual([
      "EMA 13 (ema_13)",
      "EMA 21 (ema_21)",
      "EMA 50 (ema_50)",
      "EMA 100 (ema_100)",
      "EMA 200 (ema_200)",
      "SMA 13 (sma_13)",
      "SMA 21 (sma_21)",
      "SMA 100 (sma_100)",
      "Momentum (price change, 10d) (momentum)",
      "Perf WTD (perf_wtd)",
    ]);
    expect(boxes.every((box) => !box.disabled)).toBe(true);

    // The disabled per-symbol group is gone, not merely collapsed: no checkbox
    // in the dialog is disabled, and the group is not rendered under a synonym.
    const everyBox = within(dialog).getAllByRole("checkbox") as HTMLInputElement[];
    expect(everyBox.filter((box) => box.disabled)).toHaveLength(0);
    expect(within(dialog).queryByText(/per-symbol/i)).toBeNull();
    expect(within(dialog).queryByTestId("column-picker-symbol-group")).toBeNull();
  });

  it("keeps the catalogue list and the Computed group disjoint", () => {
    setup({ computedKeys: COMPUTED });
    const catalogue = screen.getByRole("list", { name: "Catalogue columns" });
    const computed = screen.getByRole("list", { name: "Computed columns" });
    const catalogueKeys = within(catalogue)
      .getAllByRole("checkbox")
      .map((box) => box.getAttribute("aria-label"));
    const computedKeys = within(computed)
      .getAllByRole("checkbox")
      .map((box) => box.getAttribute("aria-label"));

    expect(catalogueKeys).toHaveLength(KEYS.length);
    expect(new Set([...catalogueKeys, ...computedKeys]).size).toBe(
      KEYS.length + COMPUTED.length,
    );
  });

  it("adds a computed key by its public name", async () => {
    const { onToggle } = setup({ computedKeys: COMPUTED });
    const user = userEvent.setup();
    await user.click(screen.getByRole("checkbox", { name: /EMA 13 \(ema_13\)/ }));
    expect(onToggle).toHaveBeenCalledWith("ema_13");
    await user.click(screen.getByRole("checkbox", { name: /Perf WTD \(perf_wtd\)/ }));
    expect(onToggle).toHaveBeenLastCalledWith("perf_wtd");
  });

  it("finds computed keys by label or key without leaking them into the catalogue", async () => {
    setup({ computedKeys: COMPUTED });
    const user = userEvent.setup();
    const search = screen.getByRole("searchbox", { name: "Find a column" });

    await user.type(search, "ema");
    expect(
      within(screen.getByRole("list", { name: "Catalogue columns" })).queryAllByRole(
        "checkbox",
      ),
    ).toHaveLength(0);
    expect(
      within(screen.getByRole("list", { name: "Computed columns" })).getAllByRole(
        "checkbox",
      ),
    ).toHaveLength(5);

    await user.clear(search);
    await user.type(search, "momentum");
    expect(screen.getByRole("checkbox", { name: /Momentum \(price change, 10d\)/ })).toBeTruthy();
  });

  it("counts Computed keys against the same 40-column panel", () => {
    setup({
      computedKeys: COMPUTED,
      selected: [...KEYS, ...COMPUTED],
    });
    expect(screen.getByTestId("column-picker-count").textContent).toBe("14 / 40");
  });
});
