import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { filterSchemaFixture } from "./__fixtures__/schema";
import { buildRegistry } from "./registry";
import { CommandPalette, matchFilters } from "./CommandPalette";

const registry = buildRegistry(filterSchemaFixture());

function renderPalette(onPick = vi.fn(), onClose = vi.fn()) {
  render(<CommandPalette open registry={registry} onClose={onClose} onPick={onPick} />);
  return { onPick, onClose, input: screen.getByRole("combobox") };
}

describe("the ⌘K filter palette", () => {
  it("finds a filter by its label", async () => {
    const user = userEvent.setup();
    const { onPick, input } = renderPalette();

    await user.type(input, "Performance");
    const options = screen.getAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual(
      expect.arrayContaining([expect.stringContaining("Performance")]),
    );

    await user.keyboard("{Enter}");
    expect(onPick).toHaveBeenCalledWith("ta_perf");
  });

  it("finds a filter by a catalogue alias", async () => {
    const user = userEvent.setup();
    const { onPick, input } = renderPalette();

    await user.type(input, "market_cap");
    const options = screen.getAllByRole("option");
    expect(options).toHaveLength(1);
    expect(options[0]?.textContent).toContain("Market Cap.");
    expect(options[0]?.textContent).toContain("alias market_cap");

    await user.keyboard("{Enter}");
    expect(onPick).toHaveBeenCalledWith("cap");
  });

  it("ranks aliases into the corpus for a short spelling like pe", () => {
    const matches = matchFilters(registry, "pe");
    expect(matches.some((match) => match.entry.key === "fa_pe")).toBe(true);
  });

  it("(b) cannot reach a refused key by its name or label", async () => {
    const user = userEvent.setup();
    const { input } = renderPalette();

    await user.type(input, "idx");
    expect(screen.queryAllByRole("option")).toHaveLength(0);
    expect(screen.getByRole("status").textContent).toMatch(/No filter matches/);

    await user.clear(input);
    await user.type(input, "Index");
    expect(screen.queryAllByRole("option")).toHaveLength(0);

    await user.clear(input);
    await user.type(input, "Theme");
    expect(screen.queryAllByRole("option")).toHaveLength(0);
  });

  it("is keyboard operable: arrows move the active option, Enter picks it", async () => {
    const user = userEvent.setup();
    const { onPick, input } = renderPalette();

    // An empty query lists the whole registry in schema order; the second
    // entry is Sector.
    expect(screen.getAllByRole("option")).toHaveLength(50);
    await user.type(input, "{ArrowDown}");
    const active = screen.getAllByRole("option")[1];
    expect(active?.getAttribute("aria-selected")).toBe("true");
    await user.keyboard("{Enter}");
    expect(onPick).toHaveBeenCalledWith(active?.getAttribute("data-filter-key"));
  });

  it("closes on Escape", async () => {
    const user = userEvent.setup();
    const { onClose, input } = renderPalette();
    await user.type(input, "{Escape}");
    expect(onClose).toHaveBeenCalled();
  });
});
