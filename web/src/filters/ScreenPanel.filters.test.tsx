import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ViewDefinition } from "@/data/screens";

import { filterSchemaFixture } from "./__fixtures__/schema";
import { buildRegistry } from "./registry";
import type { ActiveFilter } from "./types";
import { ScreenPanel } from "@/grid/ScreenPanel";

/**
 * The panel's half of the filter contract: the chip bar is the state, a chip's
 * × is a new request, and a filter that matches nothing keeps its chips on
 * screen (adversarial (c)) so the reader can undo it.
 */

const registry = buildRegistry(filterSchemaFixture());
const VIEW: ViewDefinition = {
  name: "today",
  label: "Today",
  job: "What is moving right now?",
  columns: ["price"],
};

function rows(count: number, prefix: string) {
  return Array.from({ length: count }, (_, index) => ({
    symbol: `${prefix}${index}`,
    price: 100 + index,
  }));
}

function answer(count: number, prefix: string, total = 5_600): Response {
  return new Response(
    JSON.stringify({
      as_of: "2026-10-31T01:28:01Z",
      limit: 20,
      total,
      universe_total: 5_600,
      returned: count,
      columns: ["price"],
      rows: rows(count, prefix),
      budget: { truncated: false, dropped_fields: [] },
    }),
    { status: 200, headers: { "content-type": "application/json" } },
  );
}

function bodyOf(call: unknown): Record<string, unknown> {
  const [, init] = call as [string, RequestInit];
  return JSON.parse(String(init.body)) as Record<string, unknown>;
}

function filtersOf(call: unknown): unknown {
  const body = bodyOf(call);
  return (body["criteria"] as Record<string, unknown>)["filters"];
}

afterEach(() => {
  vi.unstubAllGlobals();
});

function Harness({ initial }: { initial: ActiveFilter[] }) {
  const [filters, setFilters] = React.useState(initial);
  return (
    <ScreenPanel
      view={VIEW}
      onActivate={vi.fn()}
      containerWidth={1280}
      filters={filters}
      registry={registry}
      onRemoveFilter={(id) => setFilters((current) => current.filter((filter) => filter.id !== id))}
      onClearFilters={() => setFilters([])}
    />
  );
}

describe("ScreenPanel filters", () => {
  it("sends the chip's predicate and shows the first page after a filter change", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(answer(20, "A"))
      .mockResolvedValueOnce(answer(20, "B"));
    vi.stubGlobal("fetch", fetchMock);

    const { rerender } = render(
      <ScreenPanel
        view={VIEW}
        onActivate={vi.fn()}
        containerWidth={1280}
        filters={[]}
        registry={registry}
        onRemoveFilter={vi.fn()}
        onClearFilters={vi.fn()}
      />,
    );

    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("1/5,600 Total"));
    expect(filtersOf(fetchMock.mock.calls[0])).toEqual([]);

    rerender(
      <ScreenPanel
        view={VIEW}
        onActivate={vi.fn()}
        containerWidth={1280}
        filters={[{ id: "f1", key: "cap", op: "gte", value: 2_000_000_000 }]}
        registry={registry}
        onRemoveFilter={vi.fn()}
        onClearFilters={vi.fn()}
      />,
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(filtersOf(fetchMock.mock.calls[1])).toEqual([
      { key: "cap", op: "gte", value: 2_000_000_000 },
    ]);
    await waitFor(() =>
      expect(screen.getByTestId("grid-table").querySelectorAll("tbody tr[data-symbol]")).toHaveLength(
        20,
      ),
    );
    expect(screen.getByTestId("range").textContent).toBe("1/5,600 Total");
  });

  it("a chip's × is the request, and the grid follows", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(answer(20, "A"))
      .mockResolvedValueOnce(answer(20, "B"));
    vi.stubGlobal("fetch", fetchMock);

    render(<Harness initial={[{ id: "f1", key: "cap", op: "gte", value: 2_000_000_000 }]} />);

    await waitFor(() => expect(screen.getByTestId("chip-bar")).toBeTruthy());
    expect(filtersOf(fetchMock.mock.calls[0])).toEqual([
      { key: "cap", op: "gte", value: 2_000_000_000 },
    ]);

    await user.click(screen.getByRole("button", { name: /Remove filter: Market Cap/ }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(filtersOf(fetchMock.mock.calls[1])).toEqual([]);
    await waitFor(() =>
      expect(
        screen.getByTestId("grid-table").querySelector("tbody tr[data-symbol]")?.getAttribute("data-symbol"),
      ).toBe("B0"),
    );
    expect(screen.queryByTestId("chip-bar")).toBeNull();
  });

  it("(d) two filters on one key travel as two predicates, and one × leaves the other", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(answer(20, "A"))
      .mockResolvedValueOnce(answer(50, "B", 50));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <Harness
        initial={[
          { id: "f1", key: "cap", op: "gte", value: 2_000_000_000 },
          { id: "f2", key: "cap", op: "lt", value: 10_000_000_000 },
        ]}
      />,
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(filtersOf(fetchMock.mock.calls[0])).toEqual([
      { key: "cap", op: "gte", value: 2_000_000_000 },
      { key: "cap", op: "lt", value: 10_000_000_000 },
    ]);
    // The chips, not every `<li>` on the page: the pager is a list too.
    const chips = () => within(screen.getByTestId("chip-bar")).getAllByRole("listitem");
    expect(chips()).toHaveLength(2);

    await user.click(screen.getByRole("button", { name: /Remove filter: Market Cap.*≥ \$2B/ }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(filtersOf(fetchMock.mock.calls[1])).toEqual([
      { key: "cap", op: "lt", value: 10_000_000_000 },
    ]);
    expect(chips()).toHaveLength(1);
  });

  it("(c) a zero-row answer keeps the chips visible so the filter can be undone", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(answer(0, "Z", 0))
      .mockResolvedValueOnce(answer(20, "A"));
    vi.stubGlobal("fetch", fetchMock);

    render(<Harness initial={[{ id: "f1", key: "sec", op: "eq", value: "Nothing Inc." }]} />);

    await waitFor(() => expect(screen.getByText("No stocks match")).toBeTruthy());
    const chips = screen.getByTestId("chip-bar");
    expect(chips.textContent).toContain("Sector");
    expect(chips.textContent).toContain("Nothing Inc.");

    await user.click(screen.getByRole("button", { name: /Remove filter: Sector/ }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(filtersOf(fetchMock.mock.calls[1])).toEqual([]);
    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("1/5,600 Total"));
  });

  it("Clear all restores the unfiltered panel", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(answer(20, "A"))
      .mockResolvedValueOnce(answer(20, "B"));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <Harness
        initial={[
          { id: "f1", key: "cap", op: "gte", value: 2_000_000_000 },
          { id: "f2", key: "sec", op: "eq", value: "Technology" },
        ]}
      />,
    );

    await waitFor(() => expect(screen.getByTestId("chip-bar")).toBeTruthy());
    expect(filtersOf(fetchMock.mock.calls[0])).toHaveLength(2);

    await user.click(screen.getByRole("button", { name: "Clear all" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(filtersOf(fetchMock.mock.calls[1])).toEqual([]);
    expect(screen.queryByTestId("chip-bar")).toBeNull();
    await waitFor(() =>
      expect(
        screen.getByTestId("grid-table").querySelector("tbody tr[data-symbol]")?.getAttribute("data-symbol"),
      ).toBe("B0"),
    );
  });

  // -------------------------------------------------------------------------
  // F3 — a page past the end is bounded against the answer's own total
  // -------------------------------------------------------------------------

  it("F3: reconciles a page past the last one against meta.total", async () => {
    const fetchMock = vi.fn().mockResolvedValue(answer(20, "A"));
    vi.stubGlobal("fetch", fetchMock);
    const onPageChange = vi.fn();

    render(
      <ScreenPanel
        view={VIEW}
        onActivate={vi.fn()}
        containerWidth={1280}
        page={9_999}
        onPageChange={onPageChange}
        filters={[]}
        registry={registry}
      />,
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = bodyOf(fetchMock.mock.calls[0]);
    expect((body["criteria"] as { offset: number }).offset).toBe(199_980);

    // 5,600 rows at 20 a page is 280 pages: page index 9,999 cannot exist.
    await waitFor(() => expect(onPageChange).toHaveBeenCalledWith(279));
  });

  it("F3: the real last page is left alone (no reconcile loop)", async () => {
    const fetchMock = vi.fn().mockResolvedValue(answer(20, "A"));
    vi.stubGlobal("fetch", fetchMock);
    const onPageChange = vi.fn();

    render(
      <ScreenPanel
        view={VIEW}
        onActivate={vi.fn()}
        containerWidth={1280}
        page={279}
        onPageChange={onPageChange}
        filters={[]}
        registry={registry}
      />,
    );

    await waitFor(() => expect(screen.getByText("280/280")).toBeTruthy());
    expect(onPageChange).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
