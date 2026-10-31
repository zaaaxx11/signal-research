import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as React from "react";
import { describe, expect, it, vi } from "vitest";

import { filterSchemaFixture } from "./__fixtures__/schema";
import { buildRegistry } from "./registry";
import { FilterDrawer } from "./FilterDrawer";

const registry = buildRegistry(filterSchemaFixture());

function renderDrawer(props: Partial<React.ComponentProps<typeof FilterDrawer>> = {}) {
  const handlers = {
    onClose: vi.fn(),
    onAdd: vi.fn(),
    onRemove: vi.fn(),
    onClear: vi.fn(),
    onClearKey: vi.fn(),
    onApplyBucket: vi.fn(),
  };
  render(
    <FilterDrawer
      open
      registry={registry}
      filters={[]}
      focusKey={null}
      {...handlers}
      {...props}
    />,
  );
  return handlers;
}

describe("the filter drawer", () => {
  it("offers exactly the schema's 50 usable keys and no refused one", async () => {
    const user = userEvent.setup();
    renderDrawer();
    const drawer = screen.getByRole("dialog", { name: "Filters" });

    // The eight primary presets are visible; the rest sit behind one disclosure.
    const presetKeys = () =>
      new Set(
        Array.from(drawer.querySelectorAll<HTMLElement>("[data-filter-key]")).map(
          (element) => element.getAttribute("data-filter-key") ?? "",
        ),
      );
    expect(presetKeys().size).toBe(8);

    await user.click(within(drawer).getByRole("button", { name: "More numeric presets" }));
    expect(presetKeys().size).toBe(10);

    const picker = within(drawer).getByLabelText("Add a filter") as HTMLSelectElement;
    const optionKeys = Array.from(picker.options)
      .map((option) => option.value)
      .filter((value) => value.length > 0);

    expect(optionKeys).toHaveLength(50);
    expect(new Set([...presetKeys(), ...optionKeys]).size).toBe(50);
    expect(new Set(optionKeys)).toEqual(registry.usableKeys);
    expect(optionKeys).not.toContain("idx");
    expect(optionKeys).not.toContain("theme");
  });

  it("drops a preset whose key the schema stops offering", () => {
    const schema = filterSchemaFixture();
    schema.keys["cap"] = {
      label: "Market Cap.",
      tab: "descriptive",
      field: null,
      reason: "needs a market-cap column this build does not carry",
    };
    const withoutCap = buildRegistry(schema);

    render(
      <FilterDrawer
        open
        registry={withoutCap}
        filters={[]}
        focusKey={null}
        onClose={vi.fn()}
        onAdd={vi.fn()}
        onRemove={vi.fn()}
        onClear={vi.fn()}
        onClearKey={vi.fn()}
        onApplyBucket={vi.fn()}
      />,
    );

    expect(screen.queryByTestId("preset-cap")).toBeNull();
    expect(screen.queryByRole("button", { name: "$2–10B" })).toBeNull();
    const picker = screen.getByLabelText("Add a filter") as HTMLSelectElement;
    const keys = Array.from(picker.options).map((option) => option.value);
    expect(keys).not.toContain("cap");
    expect(keys.filter(Boolean)).toHaveLength(49);
  });

  it("has no Apply button: a bucket press is the request", async () => {
    const user = userEvent.setup();
    const { onApplyBucket } = renderDrawer();
    const drawer = screen.getByRole("dialog", { name: "Filters" });

    expect(screen.queryByRole("button", { name: /apply/i })).toBeNull();
    expect(within(drawer).queryByText(/^apply$/i)).toBeNull();

    await user.click(within(drawer).getByRole("button", { name: "$2–10B" }));
    expect(onApplyBucket).toHaveBeenCalledTimes(1);
    const [group, bucket] = onApplyBucket.mock.calls[0] as [ { key: string }, { id: string; label: string } ];
    expect(group.key).toBe("cap");
    expect(bucket.id).toBe("small");
    expect(bucket.label).toBe("$2–10B");
  });

  it("marks the active bucket and offers `Any` to clear the field", async () => {
    const user = userEvent.setup();
    const { onClearKey } = renderDrawer({
      filters: [{ id: "a", key: "cap", op: "gte", value: 2_000_000_000 }],
    });
    const drawer = screen.getByRole("dialog", { name: "Filters" });
    expect(within(drawer).getByRole("button", { name: "$2–10B" }).getAttribute("aria-pressed")).toBe(
      "false",
    );
    await user.click(within(drawer).getAllByRole("button", { name: "Any" })[0] as HTMLElement);
    expect(onClearKey).toHaveBeenCalledWith("cap");
  });

  it("scrolls to and focuses a preset chosen in ⌘K", () => {
    renderDrawer({ focusKey: "cap" });
    const section = screen.getByTestId("preset-cap");
    expect(document.activeElement).toBe(within(section).getByRole("button", { name: "Under $2B" }));
  });

  it("reveals and focuses a secondary preset chosen in ⌘K", () => {
    renderDrawer({ focusKey: "fa_roe" });
    const section = screen.getByTestId("preset-fa_roe");
    expect(document.activeElement).toBe(
      within(section).getByRole("button", { name: "Under 0%" }),
    );
  });

  it("opens the builder at a non-preset key chosen in ⌘K, with its value focused", () => {
    renderDrawer({ focusKey: "sec" });
    const value = screen.getByLabelText(/^Sector value/);
    expect(document.activeElement).toBe(value);
  });

  it("adds a compiled rule through the builder", async () => {
    const user = userEvent.setup();
    const { onAdd } = renderDrawer();
    const drawer = screen.getByRole("dialog", { name: "Filters" });

    await user.click(within(drawer).getByRole("button", { name: "Add condition" }));
    const field = within(drawer).getByLabelText("Filter 1 field");
    await user.selectOptions(field, "sec");
    await user.type(within(drawer).getByLabelText(/^Sector value/), "Technology");
    await user.click(within(drawer).getByRole("button", { name: "Add to screen" }));

    expect(onAdd).toHaveBeenCalledWith([{ key: "sec", op: "eq", value: "Technology" }]);
  });

  it("never adds a second live region (F12)", () => {
    renderDrawer();
    expect(screen.queryAllByRole("status")).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// F4 — the dialog is a real focus trap
// ---------------------------------------------------------------------------

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function focusablesIn(dialog: HTMLElement): HTMLElement[] {
  return Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (element) => element.tabIndex >= 0,
  );
}

function TrapShell({ onClose }: { onClose?: () => void } = {}) {
  const [open, setOpen] = React.useState(false);
  return (
    <div>
      <button type="button">outside before</button>
      <button type="button" onClick={() => setOpen(true)}>
        Filters
      </button>
      <FilterDrawer
        open={open}
        registry={registry}
        filters={[]}
        focusKey={null}
        onClose={() => {
          setOpen(false);
          onClose?.();
        }}
        onAdd={vi.fn()}
        onRemove={vi.fn()}
        onClear={vi.fn()}
        onClearKey={vi.fn()}
        onApplyBucket={vi.fn()}
      />
      <button type="button">outside after</button>
    </div>
  );
}

describe("F4 — the drawer keeps Tab inside itself", () => {
  it("is announced as a modal dialog", () => {
    renderDrawer();
    expect(screen.getByRole("dialog", { name: "Filters" }).getAttribute("aria-modal")).toBe("true");
  });

  it("never lets focus escape, however many times Tab is pressed", async () => {
    const user = userEvent.setup();
    render(<TrapShell />);
    await user.click(screen.getByRole("button", { name: "Filters" }));

    const dialog = screen.getByRole("dialog", { name: "Filters" });
    const count = focusablesIn(dialog).length;
    expect(count).toBeGreaterThan(10);

    for (let step = 0; step < count + 5; step += 1) {
      await user.tab();
      expect(
        dialog.contains(document.activeElement),
        `focus escaped after tab ${step + 1} to ${document.activeElement?.outerHTML.slice(0, 80)}`,
      ).toBe(true);
    }
  });

  it("adversarial (c): Shift+Tab from the first control wraps to the last, and Tab from the last wraps to the first", async () => {
    const user = userEvent.setup();
    render(<TrapShell />);
    await user.click(screen.getByRole("button", { name: "Filters" }));

    const dialog = screen.getByRole("dialog", { name: "Filters" });
    const items = focusablesIn(dialog);
    const first = items[0] as HTMLElement;
    const last = items[items.length - 1] as HTMLElement;

    first.focus();
    expect(document.activeElement).toBe(first);
    await user.tab({ shift: true });
    expect(document.activeElement).toBe(last);
    expect(dialog.contains(document.activeElement)).toBe(true);

    await user.tab();
    expect(document.activeElement).toBe(first);
  });
});

// ---------------------------------------------------------------------------
// F5 — Escape closes and hands focus back to the opener
// ---------------------------------------------------------------------------

describe("F5 — closing restores focus to the trigger", () => {
  it("Escape returns focus to the button that opened the drawer", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<TrapShell onClose={onClose} />);

    const trigger = screen.getByRole("button", { name: "Filters" });
    trigger.focus();
    await user.click(trigger);

    const dialog = screen.getByRole("dialog", { name: "Filters" });
    expect(dialog.contains(document.activeElement)).toBe(true);

    await user.keyboard("{Escape}");

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("dialog", { name: "Filters" })).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("the Close button also returns focus to the trigger", async () => {
    const user = userEvent.setup();
    render(<TrapShell />);

    const trigger = screen.getByRole("button", { name: "Filters" });
    await user.click(trigger);
    await user.click(screen.getByRole("button", { name: "Close filters" }));

    expect(document.activeElement).toBe(trigger);
  });
});

// ---------------------------------------------------------------------------
// F10 — a half-built rule survives a close
// ---------------------------------------------------------------------------

describe("F10 — closing the drawer cannot lose an edit", () => {
  it("keeps a half-built builder row across close and reopen", async () => {
    const user = userEvent.setup();
    const handlers = {
      onClose: vi.fn(),
      onAdd: vi.fn(),
      onRemove: vi.fn(),
      onClear: vi.fn(),
      onClearKey: vi.fn(),
      onApplyBucket: vi.fn(),
    };
    const { rerender } = render(
      <FilterDrawer
        open
        registry={registry}
        filters={[]}
        focusKey={null}
        {...handlers}
      />,
    );

    const drawer = screen.getByRole("dialog", { name: "Filters" });
    await user.click(within(drawer).getByRole("button", { name: "Add condition" }));
    await user.selectOptions(within(drawer).getByLabelText("Filter 1 field"), "cap");
    await user.type(within(drawer).getByLabelText(/^Market Cap\. value/), "12");

    rerender(
      <FilterDrawer open={false} registry={registry} filters={[]} focusKey={null} {...handlers} />,
    );
    // Closed means gone from the accessibility tree, not gone from the DOM.
    expect(screen.queryByRole("dialog", { name: "Filters" })).toBeNull();

    rerender(<FilterDrawer open registry={registry} filters={[]} focusKey={null} {...handlers} />);
    expect(screen.getByLabelText(/^Market Cap\. value/)).toHaveProperty("value", "12");
  });
});

// ---------------------------------------------------------------------------
// F11 — two filters on one key are distinguishable in the "On screen" list
// ---------------------------------------------------------------------------

describe("F11 — the on-screen list names each predicate", () => {
  it("gives two Market Cap rows two different names and two different remove labels", () => {
    renderDrawer({
      filters: [
        { id: "a", key: "cap", op: "gte", value: 2_000_000_000 },
        { id: "b", key: "cap", op: "lt", value: 10_000_000_000 },
      ],
    });

    const section = screen.getByRole("region", { name: "Selected filters" });
    expect(within(section).getByText("Market Cap. ≥ $2B")).toBeTruthy();
    expect(within(section).getByText("Market Cap. < $10B")).toBeTruthy();

    const removes = within(section).getAllByRole("button");
    const names = removes.map((button) => button.getAttribute("aria-label"));
    expect(names).toEqual([
      "Remove Market Cap. ≥ $2B",
      "Remove Market Cap. < $10B",
    ]);
    expect(new Set(names).size).toBe(2);
  });
});
