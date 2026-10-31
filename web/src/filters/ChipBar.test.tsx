import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { filterSchemaFixture } from "./__fixtures__/schema";
import { buildRegistry } from "./registry";
import { ChipBar } from "./ChipBar";
import { resetFilterIds } from "./state";
import type { ActiveFilter } from "./types";

const registry = buildRegistry(filterSchemaFixture());

function filters(): ActiveFilter[] {
  resetFilterIds();
  return [
    { id: "a", key: "cap", op: "gte", value: 2_000_000_000 },
    { id: "b", key: "sec", op: "in", value: ["Technology", "Healthcare"] },
  ];
}

describe("the chip bar", () => {
  it("shows every active filter with a human label and value", () => {
    render(<ChipBar registry={registry} filters={filters()} onRemove={vi.fn()} onClear={vi.fn()} />);
    expect(screen.getByText("Market Cap.")).toBeTruthy();
    expect(screen.getByText("≥ $2B")).toBeTruthy();
    expect(screen.getByText("Sector")).toBeTruthy();
    expect(screen.getByText("in Technology, Healthcare")).toBeTruthy();
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  it("removes by id, not by value, so two filters on one key are addressable", async () => {
    const user = userEvent.setup();
    const onRemove = vi.fn();
    const onClear = vi.fn();
    render(<ChipBar registry={registry} filters={filters()} onRemove={onRemove} onClear={onClear} />);

    await user.click(screen.getByRole("button", { name: "Remove filter: Market Cap. ≥ $2B" }));
    expect(onRemove).toHaveBeenCalledWith("a");

    await user.click(screen.getByRole("button", { name: "Clear all" }));
    expect(onClear).toHaveBeenCalled();
  });

  it("renders nothing when nothing is filtered and there is no notice", () => {
    const { container } = render(
      <ChipBar registry={registry} filters={[]} onRemove={vi.fn()} onClear={vi.fn()} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("keeps the link's ignored-key notice dismissible", async () => {
    const user = userEvent.setup();
    const onDismiss = vi.fn();
    render(
      <ChipBar
        registry={registry}
        filters={[]}
        onRemove={vi.fn()}
        onClear={vi.fn()}
        notice="Ignored 1 filter from this link: idx — this build cannot apply it."
        onDismissNotice={onDismiss}
      />,
    );
    expect(screen.getByRole("status").textContent).toMatch(/idx/);
    await user.click(screen.getByRole("button", { name: "Dismiss filter notice" }));
    expect(onDismiss).toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// F6, F7, F8 — the rendered chip must say what the request says
// ---------------------------------------------------------------------------

describe("the chip's rendered text", () => {
  it("F6: a choice chip shows the option label the drawer shows", () => {
    render(
      <ChipBar
        registry={registry}
        filters={[{ id: "a", key: "ta_perf", op: "eq", value: "1w10u" }]}
        onRemove={vi.fn()}
        onClear={vi.fn()}
      />,
    );

    const bar = screen.getByTestId("chip-bar");
    expect(bar.textContent).toContain("Performance");
    expect(bar.textContent).toContain("1W down 10%+");
    expect(bar.textContent).not.toContain("1w10u");
  });

  it("F7: a non-preset key renders its unit", () => {
    render(
      <ChipBar
        registry={registry}
        filters={[
          { id: "a", key: "sh_short", op: "gte", value: 5 },
          { id: "b", key: "targetprice", op: "gte", value: 200 },
        ]}
        onRemove={vi.fn()}
        onClear={vi.fn()}
      />,
    );

    // The label and the value are separate spans, so the unit lives on the value.
    expect(screen.getByText("Short Float")).toBeTruthy();
    expect(screen.getByText("≥ 5%")).toBeTruthy();
    expect(screen.getByText("Target Price")).toBeTruthy();
    expect(screen.getByText("≥ $200")).toBeTruthy();
  });

  it("F8/F11: two caps a hair apart render two different chips and names", () => {
    render(
      <ChipBar
        registry={registry}
        filters={[
          { id: "a", key: "cap", op: "lt", value: 1_000_000_000 },
          { id: "b", key: "cap", op: "lt", value: 1_000_040_000 },
        ]}
        onRemove={vi.fn()}
        onClear={vi.fn()}
      />,
    );

    const bar = screen.getByTestId("chip-bar");
    expect(bar.textContent).toContain("< $1B");
    expect(bar.textContent).toContain("< $1.00004B");

    const names = screen
      .getAllByRole("button", { name: /^Remove filter: Market Cap/ })
      .map((button) => button.getAttribute("aria-label"));
    expect(names).toEqual([
      "Remove filter: Market Cap. < $1B",
      "Remove filter: Market Cap. < $1.00004B",
    ]);
    expect(new Set(names).size).toBe(2);
  });
});
