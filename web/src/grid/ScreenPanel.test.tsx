import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("1–20 of 5,600"));

    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    const body = JSON.parse(String(init.body)) as {
      criteria: { limit: number; offset: number };
    };
    // The request body is the contract: never 100, and the page step is 20.
    expect(body.criteria.limit).toBe(20);
    expect(body.criteria.offset).toBe(0);
    expect(screen.getByRole("button", { name: "Next" })).toBeTruthy();
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

    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("1–20 of 5,600"));

    await user.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("21–40 of 5,600"));

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

    await user.click(screen.getByRole("button", { name: "Check again" }));
    await waitFor(() => expect(screen.getByTestId("stale-badge")).toBeTruthy());

    expect(screen.getByTestId("stale-badge").textContent).toContain("304");
    expect(screen.getByTestId("grid-table").querySelectorAll("tbody tr[data-symbol]")).toHaveLength(20);
    expect(screen.getByTestId("range").textContent).toBe("1–20 of 5,600");
  });

  it("sends the chosen sort and returns to the first page", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(page(20, "A", 'W/"p1"'))
      .mockResolvedValueOnce(page(20, "T", 'W/"p2"'));
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    render(<ScreenPanel view={VIEW} selectedSymbol={null} onActivate={vi.fn()} onClear={vi.fn()} containerWidth={1280} />);
    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("1–20 of 5,600"));

    // The toolbar's sort is three toggle buttons now; pressing one is the
    // choice, and `aria-pressed` says which one is on.
    const group = screen.getByRole("group", { name: "Sort by" });
    expect(within(group).getAllByRole("button").map((button) => button.textContent)).toEqual([
      "Market cap",
      "Turnover",
      "Gainers",
    ]);
    await user.click(within(group).getByRole("button", { name: "Turnover" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    const [, init] = fetchMock.mock.calls[1] as unknown as [string, RequestInit];
    const body = JSON.parse(String(init.body));
    expect(body.criteria.sort).toBe("turnover");
    expect(body.criteria.offset).toBe(0);
    expect(within(group).getByRole("button", { name: "Turnover" }).getAttribute("aria-pressed")).toBe(
      "true",
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
