/**
 * The masthead's contract, proved without a network.
 *
 * Four separate promises: the filled heading and its centred row, the
 * disclosure (chevron, animation, Escape), the server-shaped panel (one field
 * per control), and the two column vocabularies — the reader's own saved sets
 * and the product's built-in tabs.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";


import type { WireFilter } from "@/filters/types";
import { VIEW_FIXTURE } from "@/grid/__fixtures__/views";

import { ColumnPresetsDialog } from "./ColumnPresetsDialog";
import { FILTER_PANEL_ID, ScreenerHeader, type ScreenerHeaderProps } from "./ScreenerHeader";
import { COLUMN_PRESETS, type ResolvedPreset } from "./columnPresets";
import { resetPresetSequence, useUserColumnPresets, USER_PRESETS_KEY } from "./userPresets";
import { CAP_MEGA, EXCH_NYSE, readyDocument } from "./__fixtures__/filters";

/** Every key the served catalogue carries, plus the ones only the wide view has. */
const CATALOGUE = [
  ...new Set([
    ...VIEW_FIXTURE.flatMap((view) => view.columns),
    "ticker",
    "news_time",
    "news_title",
    "news_url",
    "daily_digest",
  ]),
];

type BaseProps = Omit<ScreenerHeaderProps, "userPresets">;

/** The header inside the real preset store, so create/rename/delete are real. */
function Harness({ props }: { props: BaseProps }) {
  const userPresets = useUserColumnPresets();
  return <ScreenerHeader {...props} userPresets={userPresets} />;
}

function setup(overrides: Partial<BaseProps> = {}) {
  const spies = {
    onSelectPreset: vi.fn<(preset: ResolvedPreset) => void>(),
    onOpenColumnPicker: vi.fn<() => void>(),
    onApplyColumns: vi.fn<(columns: readonly string[]) => void>(),
    onSortChange: vi.fn<(sort: string) => void>(),
    onSignalChange: vi.fn<(signal: string | null) => void>(),
    onAddFilter: vi.fn<(filter: WireFilter) => void>(),
    onClearFilters: vi.fn<() => void>(),
    onSelectSymbol: vi.fn<(symbol: string) => void>(),
  };
  const props: BaseProps = {
    title: "Stock screener",
    views: VIEW_FIXTURE,
    catalogue: CATALOGUE,
    view: "today",
    columns: VIEW_FIXTURE[0]?.columns ?? [],
    sort: "market_cap",
    signal: null,
    filters: [],
    vocabulary: readyDocument(),
    wide: null,
    ...spies,
    ...overrides,
  };
  const result = render(<Harness props={props} />);
  return { ...result, props, spies };
}

beforeEach(() => {
  window.localStorage.clear();
  resetPresetSequence();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("the screener masthead", () => {
  it("is one filled h1 with its controls centred and the filter trigger on the right", () => {
    setup();

    const heading = screen.getByTestId("screener-title");
    expect(heading.tagName).toBe("H1");
    expect(heading.textContent).toBe("Stock screener");
    expect(heading.className).toContain("bg-accent-strong");
    expect(heading.className).toContain("text-accent-fg");
    expect(document.querySelectorAll("h1")).toHaveLength(1);

    // The band: one row, its contents centred, and the trigger last.
    const band = heading.closest("div.bg-accent-strong") as HTMLElement;
    expect(band).toBeTruthy();
    const row = band.firstElementChild as HTMLElement;
    expect(row.className).toContain("mx-auto");
    const centred = row.firstElementChild as HTMLElement;
    expect(centred.className).toContain("justify-center");
    expect(centred.className).toContain("flex-1");
    expect(row.lastElementChild).toBe(screen.getByTestId("filter-button"));

    const header = within(screen.getByTestId("screener-header"));
    for (const name of ["Your column presets", "Order by", "Signal", "Ticker search"]) {
      expect(header.getByRole("combobox", { name })).toBeTruthy();
    }
  });

  it("keeps the built-in tabs mounted below the panel whether it is open or shut", async () => {
    const user = userEvent.setup();
    setup();

    const toolbar = screen.getByRole("toolbar", { name: "Built-in column presets" });
    expect(toolbar.querySelectorAll("button")).toHaveLength(COLUMN_PRESETS.length);
    expect(document.querySelector('[data-testid="filter-panel"]')).toBeNull();

    await user.click(screen.getByTestId("filter-button"));
    const panel = screen.getByTestId("filter-panel");
    expect(screen.getByRole("toolbar", { name: "Built-in column presets" })).toBe(toolbar);
    // The panel is above the tabs; opening it pushes them down, not away.
    expect(
      panel.compareDocumentPosition(toolbar) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });
});

describe("the filter disclosure", () => {
  it("toggles with aria-expanded and a chevron that points down when shut", async () => {
    const user = userEvent.setup();
    setup();

    const button = screen.getByTestId("filter-button");
    const chevron = screen.getByTestId("filter-chevron");
    expect(button.getAttribute("aria-expanded")).toBe("false");
    expect(button.getAttribute("aria-controls")).toBe(FILTER_PANEL_ID);
    expect(chevron.getAttribute("data-open")).toBe("false");
    expect(chevron.getAttribute("class")).not.toContain("rotate-180");

    await user.click(button);
    expect(button.getAttribute("aria-expanded")).toBe("true");
    expect(chevron.getAttribute("data-open")).toBe("true");
    expect(chevron.getAttribute("class")).toContain("rotate-180");
    expect(screen.getByTestId("filter-panel").id).toBe(FILTER_PANEL_ID);

    await user.click(button);
    expect(button.getAttribute("aria-expanded")).toBe("false");
    expect(chevron.getAttribute("data-open")).toBe("false");
    expect(screen.queryByTestId("filter-panel")).toBeNull();
  });

  it("opens on Enter, closes on Escape and gives focus back", async () => {
    const user = userEvent.setup();
    setup();

    const button = screen.getByTestId("filter-button");
    button.focus();
    await user.keyboard("{Enter}");
    expect(button.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByTestId("filter-panel")).toBeTruthy();

    await user.keyboard(" ");
    expect(button.getAttribute("aria-expanded")).toBe("false");
    await user.keyboard(" ");
    expect(button.getAttribute("aria-expanded")).toBe("true");

    // From inside the panel: a reader has tabbed to a control and Escape must
    // still mean "close this".
    const select = screen.getByTestId("control-select-cap");
    select.focus();
    await user.keyboard("{Escape}");
    expect(button.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByTestId("filter-panel")).toBeNull();
    expect(document.activeElement).toBe(button);
  });

  it("animates open over 200 ms and eases out", async () => {
    const user = userEvent.setup();
    setup();

    await user.click(screen.getByTestId("filter-button"));
    const panel = screen.getByTestId("filter-panel");
    expect(panel.getAttribute("data-motion")).toBe("animated");
    expect(panel.className).toContain("transition-[grid-template-rows,opacity]");
    expect(panel.className).toContain("duration-200");
    expect(panel.className).toContain("ease-out");
    await waitFor(() => expect(panel.className).toContain("grid-rows-[1fr]"));
  });

  it("skips the transition entirely when the reader prefers reduced motion", async () => {
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: query === "(prefers-reduced-motion: reduce)",
      media: query,
      onchange: null,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    }));

    const user = userEvent.setup();
    setup();
    await user.click(screen.getByTestId("filter-button"));

    const panel = screen.getByTestId("filter-panel");
    expect(panel.getAttribute("data-motion")).toBe("instant");
    expect(panel.className).not.toContain("transition-[grid-template-rows,opacity]");
    expect(panel.className).toContain("grid-rows-[1fr]");
  });
});

describe("the panel is one field per control", () => {
  it("draws a labelled select for every selectable control in Descriptive", async () => {
    const user = userEvent.setup();
    setup();
    await user.click(screen.getByTestId("filter-button"));
    await user.click(screen.getByRole("button", { name: "Descriptive" }));

    const section = document.querySelector<HTMLElement>(
      `#${FILTER_PANEL_ID} section[aria-label="Descriptive"]`,
    )!;
    const selects = [...section.querySelectorAll("select")].filter(
      (select) => !select.disabled && select.getAttribute("aria-label"),
    );
    expect(selects.length).toBeGreaterThanOrEqual(15);

    const labels = selects.map((select) => select.getAttribute("aria-label"));
    expect(new Set(labels).size).toBe(labels.length);
    for (const name of [
      "Index",
      "Market Cap.",
      "Exchange",
      "Dividend Yield",
      "Short Float",
      "Analyst Recom.",
      "Option/Short",
      "Earnings Date",
      "Average Volume",
      "Relative Volume",
      "Price",
      "Target Price",
      "IPO Date",
      "Shares Outstanding",
      "Float",
    ]) {
      expect(labels, `${name} has no field of its own`).toContain(name);
    }

    // Each select carries its own control's values, not a merged vocabulary.
    const cap = screen.getByTestId("control-select-cap");
    expect(within(cap).getByRole("option", { name: /Mega \(\$200bln/ })).toBeTruthy();
    expect(within(cap).queryByRole("option", { name: /S&P 500/ })).toBeNull();
    const index = screen.getByTestId("control-select-idx");
    expect(within(index).getByRole("option", { name: "S&P 500" })).toBeTruthy();
  });

  it("shows a disabled control with its reason, and inputs for text and number", async () => {
    const user = userEvent.setup();
    setup();
    await user.click(screen.getByTestId("filter-button"));
    await user.click(screen.getByRole("button", { name: "Descriptive" }));

    const theme = within(screen.getByTestId("control-theme")).getByRole("combobox", {
      name: "Theme",
    });
    expect((theme as HTMLSelectElement).disabled).toBe(true);
    expect(theme.getAttribute("title")).toMatch(/no row carries a theme/);

    // A select with nothing published says so instead of opening an empty menu.
    const sector = within(screen.getByTestId("control-sec")).getByRole("combobox", {
      name: "Sector",
    });
    expect((sector as HTMLSelectElement).disabled).toBe(true);

    const industry = screen.getByTestId("control-input-industry") as HTMLInputElement;
    expect(industry.type).toBe("text");
    const volume = screen.getByTestId("control-input-sh_curvol") as HTMLInputElement;
    expect(volume.type).toBe("number");
  });

  it("writes the published predicate for an option, an input and a number", async () => {
    const user = userEvent.setup();
    const { spies } = setup();
    await user.click(screen.getByTestId("filter-button"));
    await user.click(screen.getByRole("button", { name: "Descriptive" }));

    await user.selectOptions(screen.getByTestId("control-select-cap"), CAP_MEGA);
    expect(spies.onAddFilter).toHaveBeenLastCalledWith({ key: "cap", op: "eq", value: "mega" });

    await user.selectOptions(screen.getByTestId("control-select-exch"), EXCH_NYSE);
    expect(spies.onAddFilter).toHaveBeenLastCalledWith({
      key: "exch",
      op: "in",
      value: ["nyse"],
    });

    await user.type(screen.getByTestId("control-input-industry"), "Semiconductors{Enter}");
    expect(spies.onAddFilter).toHaveBeenLastCalledWith({
      key: "industry",
      op: "contains",
      value: "Semiconductors",
    });

    await user.type(screen.getByTestId("control-input-sh_curvol"), "1000000");
    await user.click(screen.getByRole("button", { name: "Apply Current Volume" }));
    expect(spies.onAddFilter).toHaveBeenLastCalledWith({
      key: "sh_curvol",
      op: "gte",
      value: 1_000_000,
    });
    expect(spies.onAddFilter).toHaveBeenCalledTimes(4);
  });

  it("shows every group under a small heading under All, then narrows to one", async () => {
    const user = userEvent.setup();
    setup();
    await user.click(screen.getByTestId("filter-button"));

    const panel = screen.getByTestId("filter-panel");
    for (const heading of ["Descriptive", "Fundamental", "Technical", "News", "ETF"]) {
      expect(within(panel).getByRole("heading", { name: new RegExp(heading) })).toBeTruthy();
    }
    expect(panel.querySelectorAll("section[aria-label]").length).toBe(5);
    expect(panel.querySelectorAll("select").length).toBeGreaterThan(18);

    await user.click(screen.getByRole("button", { name: "Technical" }));
    expect(screen.getByTestId("filter-panel").querySelectorAll("section[aria-label]").length).toBe(1);
    expect(screen.getByTestId("control-select-rsi")).toBeTruthy();
    expect(screen.queryByTestId("control-select-cap")).toBeNull();

    await user.click(screen.getByRole("button", { name: "All" }));
    expect(screen.getByTestId("control-select-cap")).toBeTruthy();
    expect(screen.queryByTestId("control-select-all")).toBeNull();
  });
});

describe("the two column vocabularies", () => {
  it("keeps the eighteen built-in tabs as their own control", async () => {
    const user = userEvent.setup();
    const { spies } = setup();

    const toolbar = screen.getByRole("toolbar", { name: "Built-in column presets" });
    const buttons = [...toolbar.querySelectorAll<HTMLButtonElement>("button")];
    expect(buttons.map((button) => (button.textContent ?? "").trim())).toEqual(
      COLUMN_PRESETS.map((preset) => preset.label),
    );
    expect(buttons.filter((button) => button.tabIndex === 0)).toHaveLength(1);

    await user.click(screen.getByTestId("preset-valuation"));
    expect(spies.onSelectPreset).toHaveBeenCalledTimes(1);
    expect(spies.onSelectPreset.mock.calls[0]?.[0].viewName).toBe("value");
    // A tab is columns, never a filter.
    expect(spies.onAddFilter).not.toHaveBeenCalled();
    expect(spies.onApplyColumns).not.toHaveBeenCalled();
  });

  it("disables a tab whose view the schema no longer serves instead of redirecting it", async () => {
    const user = userEvent.setup();
    const { spies } = setup({ views: VIEW_FIXTURE.filter((view) => view.name !== "today") });

    const summary = screen.getByTestId("preset-summary") as HTMLButtonElement;
    expect(summary.disabled).toBe(true);
    expect(summary.getAttribute("title")).toMatch(/today/);
    expect(summary.getAttribute("title")).toMatch(/not served|unavailable/i);
    expect(summary.getAttribute("aria-label")).toMatch(/unavailable/i);

    await user.click(summary);
    expect(spies.onSelectPreset).not.toHaveBeenCalled();
    expect(spies.onOpenColumnPicker).not.toHaveBeenCalled();
  });

  it("reaches every column picker from the header, not from a tab", async () => {
    const user = userEvent.setup();
    const { spies } = setup();

    const edit = screen.getByTestId("open-column-picker");
    expect(edit.getAttribute("title")).toBeTruthy();
    await user.click(edit);
    expect(spies.onOpenColumnPicker).toHaveBeenCalledTimes(1);

    // The Custom tab is a column set like its siblings: it never opens the card.
    await user.click(screen.getByTestId("preset-custom"));
    expect(spies.onOpenColumnPicker).toHaveBeenCalledTimes(1);
  });

  it("opens with one saved set: My stock presets", () => {
    setup();
    const select = screen.getByTestId("columns-select") as HTMLSelectElement;
    expect([...select.options].map((option) => option.textContent)).toEqual(["My stock presets"]);
    expect(select.value).toBe("my-stock-presets");
  });

  it("creates, applies, renames and deletes a set, and remembers it in storage", async () => {
    const user = userEvent.setup();
    const { spies } = setup();

    await user.click(screen.getByTestId("manage-column-presets"));
    const dialog = screen.getByRole("dialog", { name: "Column presets" });
    const name = within(dialog).getByRole("textbox", { name: "Preset name" });
    await user.clear(name);
    await user.type(name, "Cheap tech");
    await user.click(within(dialog).getByRole("button", { name: "Save preset" }));

    expect(window.localStorage.getItem(USER_PRESETS_KEY)).toContain("Cheap tech");
    const select = screen.getByTestId("columns-select") as HTMLSelectElement;
    expect([...select.options].map((option) => option.textContent)).toEqual([
      "My stock presets",
      `Cheap tech · ${VIEW_FIXTURE[0]!.columns.length}`,
    ]);
    // Saving applies nothing by itself; the columns are already on screen.
    expect(spies.onApplyColumns).not.toHaveBeenCalled();

    // Applying it from the select writes its columns to the screen.
    await user.selectOptions(select, select.options[1]!.value);
    expect(spies.onApplyColumns).toHaveBeenCalledWith(VIEW_FIXTURE[0]!.columns);

    // Rename it.
    const row = within(dialog).getByText("Cheap tech").closest("li") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Rename" }));
    const rename = within(row).getByRole("textbox", { name: /New name for Cheap tech/ });
    await user.clear(rename);
    await user.type(rename, "Cheap tech v2");
    await user.click(within(row).getByRole("button", { name: "Save name" }));
    expect(
      (screen.getByTestId("columns-select") as HTMLSelectElement).options[1]!.textContent,
    ).toContain("Cheap tech v2");

    // Delete it: two presses, the second one says Confirm.
    const rowAfter = within(dialog).getByText("Cheap tech v2").closest("li") as HTMLElement;
    await user.click(within(rowAfter).getByRole("button", { name: "Delete" }));
    await user.click(within(rowAfter).getByRole("button", { name: "Confirm" }));
    expect([...(screen.getByTestId("columns-select") as HTMLSelectElement).options]).toHaveLength(1);
    expect(window.localStorage.getItem(USER_PRESETS_KEY)).not.toContain("Cheap tech v2");
    // Deleting the selected set falls back to the default entry.
    expect((screen.getByTestId("columns-select") as HTMLSelectElement).value).toBe(
      "my-stock-presets",
    );
  });

  it("keeps the default set un-renamable and un-deletable", async () => {
    const user = userEvent.setup();
    setup();
    await user.click(screen.getByTestId("manage-column-presets"));
    const dialog = screen.getByRole("dialog", { name: "Column presets" });
    const row = within(dialog).getByText("My stock presets").closest("li") as HTMLElement;
    expect(
      (within(row).getByRole("button", { name: "Rename" }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(
      (within(row).getByRole("button", { name: "Delete" }) as HTMLButtonElement).disabled,
    ).toBe(true);
    // "Use" is disabled until the default has columns saved into it.
    expect((within(row).getByRole("button", { name: "Use" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it("carries Order by and Signal into the one screen state", async () => {
    const user = userEvent.setup();
    const { spies } = setup();

    await user.selectOptions(screen.getByTestId("order-select"), "gainers");
    expect(spies.onSortChange).toHaveBeenCalledWith("gainers");

    await user.selectOptions(screen.getByTestId("signal-select"), "top-gainers");
    expect(spies.onSignalChange).toHaveBeenCalledWith("top-gainers");

    await user.selectOptions(screen.getByTestId("signal-select"), "");
    expect(spies.onSignalChange).toHaveBeenLastCalledWith(null);
  });

  it("sends no vendor name through any label the reader sees", () => {
    setup();
    const text = document.body.textContent?.toLowerCase() ?? "";
    for (const vendor of ["finviz", "tradingview", "yahoo", "bloomberg"]) {
      expect(text).not.toContain(vendor);
    }
  });
});

describe("ColumnPresetsDialog", () => {
  it("refuses an empty name instead of saving an unnamed set", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn();
    render(
      <ColumnPresetsDialog
        open
        onClose={vi.fn()}
        presets={[
          {
            id: "my-stock-presets",
            name: "My stock presets",
            columns: [],
            builtin: true,
            saved: false,
          },
        ]}
        currentColumns={["price"]}
        onSave={onSave}
        onRename={vi.fn()}
        onDelete={vi.fn()}
        onUse={vi.fn()}
      />,
    );
    const name = screen.getByRole("textbox", { name: "Preset name" });
    await user.clear(name);
    await user.click(screen.getByRole("button", { name: "Save preset" }));
    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toMatch(/name/i);
  });
});
