import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App, DETAIL_CHUNK_ATTEMPTS, detailChunk } from "@/app";
import { DetailPanel } from "@/detail";
import { filterSchemaWire } from "@/filters/__fixtures__/schema";
import { resetFilterSchemaCache } from "@/filters/schema";

/**
 * F1 — a detail chunk that never arrives must cost the panel, not the session.
 *
 * The composition review reproduced this against the production build: a
 * `404` on `assets/index-*.js` (a deploy rotated the hash while the tab was
 * open) emptied `#root` — grid, tabs, status line and footer gone, every
 * subsequent click dead. There was no boundary above `React.lazy`.
 *
 * The failure is simulated by replacing the one dynamic import's factory
 * (`detailChunk.load`), which is the smallest honest seam: the rejection the
 * panel sees is byte-for-byte the one a browser produces
 * (`TypeError: Failed to fetch dynamically imported module`).
 */

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function page(count: number) {
  return {
    as_of: "2026-10-31T01:28:01Z",
    limit: 100,
    total: 5_600,
    universe_total: 5_600,
    returned: count,
    columns: [],
    rows: Array.from({ length: count }, (_, index) => ({ symbol: `S${index}`, price: index })),
    budget: { truncated: false, dropped_fields: [] },
  };
}

/** Everything the shell needs to reach a 100-row grid, and nothing else. */
function shellApi() {
  const selects: Array<Record<string, unknown>> = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/screens/select/schema") return json(filterSchemaWire());
    if (url === "/health") {
      return json({
        status: "ok",
        version: "0.1.0",
        checked_at: "2026-10-31T01:28:01Z",
        elapsed_ms: 1,
        startup_error: null,
        checks: {},
      });
    }
    if (url === "/screens/select" && init?.method === "POST") {
      selects.push(JSON.parse(String(init.body)) as Record<string, unknown>);
      return json(page(100));
    }
    return json({ errorMessage: `no route for ${url}` }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, selects };
}

const CHUNK_ERROR = "Failed to fetch dynamically imported module";

function rowFor(symbol: string): HTMLElement {
  const row = screen
    .getByTestId("grid-table")
    .querySelector<HTMLElement>(`tbody tr[data-symbol="${symbol}"]`);
  expect(row).not.toBeNull();
  return row as HTMLElement;
}

const originalLoad = detailChunk.load;

/**
 * jsdom has no layout: every element measures 0 × 0, so the grid's container
 * fit never solves and the table (and its rows) never exist. Give the shell a
 * 1 280 px viewport the way a real one would — this is the harness's only lie,
 * and it is the same lie a browser makes true.
 */
beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    width: 1280,
    height: 800,
    top: 0,
    left: 0,
    right: 1280,
    bottom: 800,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  } as DOMRect);
  resetFilterSchemaCache();
  window.history.replaceState(null, "", "/app/");
});

afterEach(() => {
  detailChunk.load = originalLoad;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("F1 — a failed detail chunk", () => {
  it("leaves the shell, the grid and the tabs standing, and names the panel", async () => {
    const api = shellApi();
    const user = userEvent.setup();
    const load = vi.fn<typeof detailChunk.load>(async () => {
      throw new Error(CHUNK_ERROR);
    });
    detailChunk.load = load;

    render(<App />);
    await waitFor(() => expect(screen.getByTestId("range")).toBeTruthy());

    const grid = await screen.findByTestId("grid-table");
    expect(grid.querySelectorAll("tbody tr[data-symbol]")).toHaveLength(100);

    await user.click(rowFor("S0"));

    // The boundary is where the rejection lands — not React's root.
    const crash = await screen.findByTestId("grid-crash", undefined, { timeout: 4_000 });
    expect(within(crash).getByText(/Details panel could not be drawn/)).toBeTruthy();
    expect(crash.textContent).toContain(CHUNK_ERROR);

    // Bounded: the first import, then exactly one retry, then the message.
    expect(DETAIL_CHUNK_ATTEMPTS).toBe(2);
    expect(load).toHaveBeenCalledTimes(2);

    // The session survived: the same grid node, all its rows, the tabs, the
    // top bar and the footer are still in the DOM.
    expect(screen.getByTestId("grid-table")).toBe(grid);
    expect(grid.querySelectorAll("tbody tr[data-symbol]")).toHaveLength(100);
    expect(screen.getAllByRole("tab").length).toBeGreaterThan(0);
    expect(
      screen.getByRole("combobox", { name: "Search symbols and companies" }),
    ).toBeTruthy();
    expect(screen.getByText(/same-origin api only/)).toBeTruthy();

    // And it is still a working page: a view tab is one more selection.
    const before = api.selects.length;
    await user.click(screen.getAllByRole("tab")[1] as HTMLElement);
    await waitFor(() => expect(api.selects.length).toBe(before + 1));
  });

  it("recovers when the retry lands: one rejection is not a dead panel", async () => {
    shellApi();
    const user = userEvent.setup();
    const load = vi.fn<typeof detailChunk.load>(async () => ({ default: DetailPanel }));
    load.mockRejectedValueOnce(new Error(CHUNK_ERROR));
    detailChunk.load = load;

    render(<App />);
    await screen.findByTestId("grid-table");
    await user.click(rowFor("S0"));

    expect(await screen.findByTestId("detail-panel", undefined, { timeout: 4_000 })).toBeTruthy();
    expect(load).toHaveBeenCalledTimes(2);
    expect(screen.queryByTestId("grid-crash")).toBeNull();
    expect(screen.getByTestId("grid-table")).toBeTruthy();

    // Adversarial (c): the open row marks itself with `aria-current`; the view
    // strip marks itself with `aria-selected` (role=tab). One current item in
    // the document, so nothing can mistake the row cursor for the tab strip.
    const current = document.querySelectorAll("[aria-current]");
    expect(current).toHaveLength(1);
    expect(current[0]?.getAttribute("data-symbol")).toBe("S0");
    const selectedTab = document.querySelector('[role="tab"][aria-selected="true"]');
    expect(selectedTab).not.toBeNull();
    expect(selectedTab?.hasAttribute("aria-current")).toBe(false);
  });

  it("a permanently broken chunk stays bounded when the reader presses Try again", async () => {
    shellApi();
    const user = userEvent.setup();
    const load = vi.fn<typeof detailChunk.load>(async () => {
      throw new Error(CHUNK_ERROR);
    });
    detailChunk.load = load;

    render(<App />);
    await screen.findByTestId("grid-table");
    await user.click(rowFor("S0"));

    const crash = await screen.findByTestId("grid-crash", undefined, { timeout: 4_000 });
    expect(load).toHaveBeenCalledTimes(2);

    await user.click(within(crash).getByRole("button", { name: "Try again" }));

    // One more press is one more load: two attempts, never a loop.
    await waitFor(() => expect(load).toHaveBeenCalledTimes(4));
    expect(await screen.findByTestId("grid-crash", undefined, { timeout: 4_000 })).toBeTruthy();
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(load).toHaveBeenCalledTimes(4);
    expect(screen.getByTestId("grid-table")).toBeTruthy();
  });
});

describe("F5 — the closed detail slot", () => {
  it("exposes no landmark at all when no symbol is open", async () => {
    shellApi();
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("range")).toBeTruthy());

    expect(document.querySelector("#detail-slot")).toBeNull();
    expect(document.querySelectorAll("aside")).toHaveLength(0);
    expect(screen.queryByRole("complementary")).toBeNull();
  });
});
