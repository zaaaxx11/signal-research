import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ViewDefinition } from "@/data/screens";

import { TODAY_COLUMNS } from "./__fixtures__/today100";
import { ScreenPanel } from "./ScreenPanel";

/**
 * The panel owns the promises the toolbar makes: the row range it prints, the
 * page it asks for, and the badge it shows when a refresh was answered 304
 * instead of with new rows.
 */

const VIEW: ViewDefinition = {
  name: "today",
  label: "Today",
  job: "What is moving right now?",
  columns: [...TODAY_COLUMNS],
};

function rows(count: number, prefix = "S") {
  return Array.from({ length: count }, (_, index) => ({
    symbol: `${prefix}${index}`,
    price: 100 + index,
  }));
}

function answer(body: unknown, init: { status?: number; etag?: string } = {}) {
  return new Response(JSON.stringify(body), {
    status: init.status ?? 200,
    headers: {
      "content-type": "application/json",
      ...(init.etag ? { etag: init.etag } : {}),
    },
  });
}

function page(count: number, prefix: string, etag: string) {
  return answer(
    {
      as_of: "2026-10-31T01:28:01Z",
      limit: 20,
      total: 5600,
      universe_total: 5600,
      returned: count,
      columns: TODAY_COLUMNS,
      rows: rows(count, prefix),
      budget: { truncated: false, dropped_fields: [] },
    },
    { etag },
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

/**
 * The shell's half of the sort contract: one sort state, passed down and
 * written back. The panel itself is a renderer — it marks the header and asks
 * for the change; the owner decides.
 */
function SortHarness() {
  const [sort, setSort] = React.useState("market_cap");
  return (
    <ScreenPanel
      view={VIEW}
      selectedSymbol={null}
      onActivate={vi.fn()}
      onClear={vi.fn()}
      containerWidth={1280}
      sort={sort}
      onSortChange={setSort}
    />
  );
}

describe("ScreenPanel", () => {
  it("asks the API for twenty rows, and the pager's arithmetic follows", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(page(20, "A", 'W/"p1"'));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <ScreenPanel
        view={VIEW}
        selectedSymbol={null}
        onActivate={vi.fn()}
        onClear={vi.fn()}
        containerWidth={1280}
      />,
    );

    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("1/5,600 Total"));

    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    const body = JSON.parse(String(init.body)) as {
      criteria: { limit: number; offset: number };
    };
    // The request body is the contract: never 100, and the page step is 20.
    expect(body.criteria.limit).toBe(20);
    expect(body.criteria.offset).toBe(0);
    expect(screen.getByRole("button", { name: "Next page" })).toBeTruthy();
    expect(screen.getByText("1/280")).toBeTruthy();
  });

  it("asks for the next page by offset and prints the range the API answered", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(page(20, "A", 'W/"p1"'))
      .mockResolvedValueOnce(page(20, "B", 'W/"p2"'));
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    render(<ScreenPanel view={VIEW} selectedSymbol={null} onActivate={vi.fn()} onClear={vi.fn()} containerWidth={1280} />);

    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("1/5,600 Total"));

    await user.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("2/5,600 Total"));

    const [, init] = fetchMock.mock.calls[1] as unknown as [string, RequestInit];
    expect(JSON.parse(String(init.body)).criteria.offset).toBe(20);
    // The second page really shows the second page's rows.
    expect(screen.getByTestId("grid-table").querySelector("tbody tr[data-symbol]")?.getAttribute("data-symbol")).toBe("B0");
  });

  it("keeps the grid and shows the badge when a refresh is answered 304", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(page(20, "A", 'W/"p1"'))
      .mockResolvedValueOnce(new Response(null, { status: 304, headers: { etag: 'W/"p1"' } }));
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    render(<ScreenPanel view={VIEW} selectedSymbol={null} onActivate={vi.fn()} onClear={vi.fn()} containerWidth={1280} />);
    await waitFor(() => expect(screen.getByTestId("grid-table").querySelectorAll("tbody tr[data-symbol]")).toHaveLength(20));

    await user.click(screen.getByRole("button", { name: "Refresh the screen" }));
    await waitFor(() => expect(screen.getByTestId("stale-badge")).toBeTruthy());

    expect(screen.getByTestId("stale-badge").textContent).toContain("304");
    expect(screen.getByTestId("grid-table").querySelectorAll("tbody tr[data-symbol]")).toHaveLength(20);
    expect(screen.getByTestId("range").textContent).toBe("1/5,600 Total");
  });

  it("sends the chosen sort and returns to the first page", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(page(20, "A", 'W/"p1"'))
      .mockResolvedValueOnce(page(20, "T", 'W/"p2"'));
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    render(<SortHarness />);
    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("1/5,600 Total"));

    // The grid's own header is the sort control: pressing "Volume" asks the
    // server to rank the whole screen by volume, and `aria-sort` says so.
    const header = () => screen.getByRole("columnheader", { name: /Volume/ });
    expect(header().getAttribute("aria-sort")).toBe("none");
    await user.click(within(header()).getByRole("button", { name: /Volume/ }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    const [, init] = fetchMock.mock.calls[1] as unknown as [string, RequestInit];
    const body = JSON.parse(String(init.body));
    expect(body.criteria.sort).toBe("volume");
    expect(body.criteria.offset).toBe(0);
    expect(header().getAttribute("aria-sort")).toBe(
      "descending",
    );
  });

  it("announces the API's message when the panel cannot load", async () => {
    const fetchMock = vi.fn(async () =>
      answer(
        { error: "invalid_selection", errorMessage: "unknown sort 'bogus'" },
        { status: 422 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<ScreenPanel view={VIEW} selectedSymbol={null} onActivate={vi.fn()} onClear={vi.fn()} containerWidth={1280} />);
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("unknown sort 'bogus'"));
  });
});
