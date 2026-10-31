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
    expect(within(dialog).getAllByRole("checkbox")).toHaveLength(KEYS.length);
    expect(within(dialog).getByTestId("column-picker-count").textContent).toBe("1 / 40");
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
    expect(screen.getAllByRole("checkbox")).toHaveLength(1);
    expect(screen.getByRole("checkbox", { name: /Market Cap/ })).toBeTruthy();

    await user.clear(search);
    await user.type(search, "peg");
    expect(screen.getAllByRole("checkbox")).toHaveLength(1);
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
