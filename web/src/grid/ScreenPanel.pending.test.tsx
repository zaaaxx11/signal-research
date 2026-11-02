import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ViewDefinition } from "@/data/screens";

import { TODAY_COLUMNS } from "./__fixtures__/today100";
import { STALE_OPACITY } from "./DataTable";
import { PENDING_FLASH_DELAY_MS, pendingLabel } from "./pending";
import { ScreenPanel } from "./ScreenPanel";

/**
 * The panel's promise to the reader during a wait: *you will know something is
 * loading, and you will not lose the rows you were reading.*
 *
 * Every assertion here is made against a request the test resolves itself, so
 * the mid-flight DOM is a real state and not a guess about timing:
 *
 *  * the bar, the fade, the sentence and the shut controls exist *together*
 *    while the answer is out;
 *  * the 100 rows are the same DOM nodes before, during and after the turn —
 *    the table never unmounts;
 *  * a second click does not queue a second request;
 *  * a `304` and a failure both put the chrome away.
 */

const VIEW: ViewDefinition = {
  name: "today",
  label: "Today",
  job: "What is moving right now?",
  columns: [...TODAY_COLUMNS],
};

const VALUE: ViewDefinition = {
  name: "value",
  label: "Value",
  job: "Is it cheap?",
  columns: [...TODAY_COLUMNS],
};

const PANEL = {
  onActivate: vi.fn(),
  containerWidth: 1280,
};

const TOTAL = 5600;

function rows(count: number, prefix: string) {
  return Array.from({ length: count }, (_, index) => ({
    symbol: `${prefix}${index}`,
    price: 100 + index,
    change: index % 2 === 0 ? 1.25 : -0.75,
  }));
}

function page(count: number, prefix: string, etag: string, total = TOTAL): Response {
  return new Response(
    JSON.stringify({
      as_of: "2026-10-31T01:28:01Z",
      limit: 20,
      total,
      universe_total: total,
      returned: count,
      columns: TODAY_COLUMNS,
      rows: rows(count, prefix),
      budget: { truncated: false, dropped_fields: [] },
    }),
    { status: 200, headers: { "content-type": "application/json", etag } },
  );
}

function notModified(etag: string): Response {
  return new Response(null, { status: 304, headers: { etag } });
}

function failure(status: number, message: string): Response {
  return new Response(JSON.stringify({ error: "boom", errorMessage: message }), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** A promise the test resolves by hand, so "mid-flight" is a state, not a race. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const sleep = (ms: number) => new Promise((done) => setTimeout(done, ms));

function bodyRows(table: HTMLElement): HTMLTableRowElement[] {
  return [...table.querySelectorAll<HTMLTableRowElement>("tbody tr[data-symbol]")];
}

/** Live regions that currently say `text` — the "announced exactly once" gate. */
function announcers(text: string): Element[] {
  return [...document.querySelectorAll('[role="status"], [aria-live]')].filter((node) =>
    (node.textContent ?? "").includes(text),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("a page turn in flight", () => {
  it("holds the rows, dims them, and says which page is coming", async () => {
    const turn = deferred<Response>();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(page(20, "A", 'W/"p1"'))
      .mockImplementationOnce(() => turn.promise);
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    render(<ScreenPanel view={VIEW} {...PANEL} />);
    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("1/5,600 Total"));

    const table = screen.getByTestId("grid-table");
    const grid = screen.getByTestId("grid-scroll");
    const before = bodyRows(table);
    expect(before).toHaveLength(20);
    expect(table.style.opacity).toBe("1");
    expect(table.getAttribute("data-stale")).toBe("false");

    const next = screen.getByRole("button", { name: "Next page" }) as HTMLButtonElement;
    const prev = screen.getByRole("button", { name: "Previous page" }) as HTMLButtonElement;
    expect(next.disabled).toBe(false);

    const clickedAt = performance.now();
    await user.click(next);
    expect(fetchMock).toHaveBeenCalledTimes(2);

    // --- inside the flash window -------------------------------------------
    // Nothing has painted yet (that is the whole point of the delay), but the
    // controls are already shut and the region is already marked busy: those
    // two are correctness and must not wait 120 ms.
    const elapsed = performance.now() - clickedAt;
    await sleep(Math.max(0, 90 - elapsed));
    expect(screen.queryByTestId("fetch-bar")).toBeNull();
    expect(screen.getByTestId("fetch-status").textContent).toBe("");
    expect(table.style.opacity).toBe("1");
    expect(next.disabled).toBe(true);
    expect(prev.disabled).toBe(true);
    expect(grid.getAttribute("aria-busy")).toBe("true");

    // --- after it -----------------------------------------------------------
    await waitFor(() => expect(screen.getByTestId("fetch-bar")).toBeTruthy());

    const bar = screen.getByTestId("fetch-bar");
    expect(bar.getAttribute("aria-hidden")).toBe("true");
    expect(bar.getAttribute("data-motion")).toBe("full");
    expect(bar.className).toContain("h-0.5");
    expect(bar.className).toContain("top-0");
    expect(screen.getByTestId("fetch-bar-sweep").className).toContain("ztz-pending-sweep");

    expect(table.style.opacity).toBe(String(STALE_OPACITY));
    expect(table.getAttribute("data-stale")).toBe("true");
    expect(screen.getByTestId("fetch-status").textContent).toBe("Loading 21–40…");
    expect(announcers("Loading 21–40…")).toHaveLength(1);

    // One click, one request — and it is the next page, not the one after it.
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const [, init] = fetchMock.mock.calls[1] as unknown as [string, RequestInit];
    expect(JSON.parse(String(init.body)).criteria.offset).toBe(20);

    // The reader keeps the rows they were on: same nodes, same order.
    const during = bodyRows(table);
    expect(during).toHaveLength(20);
    during.forEach((row, index) => expect(row).toBe(before[index]));
    expect(screen.getByTestId("grid-table")).toBe(table);

    // --- the answer lands ---------------------------------------------------
    await act(async () => {
      turn.resolve(page(20, "B", 'W/"p2"'));
      await Promise.resolve();
    });
    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("2/5,600 Total"));

    expect(screen.queryByTestId("fetch-bar")).toBeNull();
    expect(screen.getByTestId("fetch-status").textContent).toBe("");
    expect(table.style.opacity).toBe("1");
    expect(table.getAttribute("data-stale")).toBe("false");
    expect(grid.getAttribute("aria-busy")).toBe("false");
    expect(next.disabled).toBe(false);
    expect(prev.disabled).toBe(false);

    // The same table, the same scroll box: a page turn is never a remount.
    expect(screen.getByTestId("grid-table")).toBe(table);
    expect(screen.getByTestId("grid-scroll")).toBe(grid);
    expect(bodyRows(table)[0]?.getAttribute("data-symbol")).toBe("B0");
  });

  it("paints nothing when the answer beats the flash threshold", async () => {
    // 10 ms: the reader's model of the click is "it already happened".
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(page(20, "A", 'W/"p1"'))
      .mockImplementationOnce(async () => {
        await sleep(10);
        return page(20, "B", 'W/"p2"');
      });
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    render(<ScreenPanel view={VIEW} {...PANEL} />);
    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("1/5,600 Total"));

    const table = screen.getByTestId("grid-table");
    await user.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("2/5,600 Total"));

    // Past the window: the timer was cancelled, not merely masked.
    await sleep(PENDING_FLASH_DELAY_MS + 120);
    expect(screen.queryByTestId("fetch-bar")).toBeNull();
    expect(table.style.opacity).toBe("1");
    expect(screen.getByTestId("fetch-status").textContent).toBe("");
    expect(screen.getByTestId("grid-scroll").getAttribute("aria-busy")).toBe("false");
  });

  it("turns one double click into one request", async () => {
    const turn = deferred<Response>();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(page(20, "A", 'W/"p1"'))
      .mockImplementationOnce(() => turn.promise)
      .mockResolvedValueOnce(page(20, "C", 'W/"p3"'));
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    render(<ScreenPanel view={VIEW} {...PANEL} />);
    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("1/5,600 Total"));

    await user.dblClick(screen.getByRole("button", { name: "Next page" }));
    expect(fetchMock).toHaveBeenCalledTimes(2);

    const [, first] = fetchMock.mock.calls[1] as unknown as [string, RequestInit];
    expect(JSON.parse(String(first.body)).criteria.offset).toBe(20);

    await act(async () => {
      turn.resolve(page(20, "B", 'W/"p2"'));
      await Promise.resolve();
    });
    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("2/5,600 Total"));

    // The second click of the double must not have queued 41–60 behind it.
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("grid-table").querySelector("tbody tr[data-symbol]")?.getAttribute("data-symbol")).toBe("B0");
  });

  it("collapses two clicks that land inside one commit", async () => {
    const turn = deferred<Response>();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(page(20, "A", 'W/"p1"'))
      .mockImplementationOnce(() => turn.promise)
      .mockResolvedValueOnce(page(20, "C", 'W/"p3"'));
    vi.stubGlobal("fetch", fetchMock);

    render(<ScreenPanel view={VIEW} {...PANEL} />);
    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("1/5,600 Total"));

    const next = screen.getByRole("button", { name: "Next page" }) as HTMLButtonElement;
    act(() => {
      next.click();
      next.click();
    });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const [, init] = fetchMock.mock.calls[1] as unknown as [string, RequestInit];
    expect(JSON.parse(String(init.body)).criteria.offset).toBe(20);
  });
});

describe("a refresh", () => {
  it("says 'Refreshing…' and clears everything on a 304 without touching the rows", async () => {
    const refresh = deferred<Response>();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(page(20, "A", 'W/"p1"'))
      .mockImplementationOnce(() => refresh.promise);
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    render(<ScreenPanel view={VIEW} {...PANEL} />);
    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("1/5,600 Total"));

    const table = screen.getByTestId("grid-table");
    const before = bodyRows(table);

    await user.click(screen.getByRole("button", { name: "Refresh the screen" }));
    await waitFor(() => expect(screen.getByTestId("fetch-bar")).toBeTruthy());
    expect(screen.getByTestId("fetch-status").textContent).toBe("Refreshing…");
    expect(table.style.opacity).toBe(String(STALE_OPACITY));
    expect(announcers("Refreshing…")).toHaveLength(1);

    await act(async () => {
      refresh.resolve(notModified('W/"p1"'));
      await Promise.resolve();
    });
    await waitFor(() => expect(screen.getByTestId("stale-badge")).toBeTruthy());

    expect(screen.queryByTestId("fetch-bar")).toBeNull();
    expect(screen.getByTestId("fetch-status").textContent).toBe("");
    expect(table.style.opacity).toBe("1");
    expect(table.getAttribute("data-stale")).toBe("false");

    const after = bodyRows(table);
    expect(after).toHaveLength(20);
    after.forEach((row, index) => expect(row).toBe(before[index]));
    expect(screen.getByTestId("range").textContent).toBe("1/5,600 Total");
    expect(announcers("Loading")).toHaveLength(0);
  });
});

describe("a first load", () => {
  it("keeps the skeleton — the bar is added, never a replacement", async () => {
    const first = deferred<Response>();
    const fetchMock = vi.fn().mockImplementationOnce(() => first.promise);
    vi.stubGlobal("fetch", fetchMock);

    render(<ScreenPanel view={VIEW} {...PANEL} />);
    await waitFor(() => expect(screen.getByTestId("fetch-bar")).toBeTruthy());

    const table = screen.getByTestId("grid-table");
    // Twelve ghost rows, no real ones, and nothing dimmed: there is no
    // previous answer to fade, so the skeleton is the whole story.
    expect(table.querySelectorAll("tbody tr[aria-hidden=true]")).toHaveLength(12);
    expect(table.querySelectorAll("tbody tr[data-symbol]")).toHaveLength(0);
    expect(table.style.opacity).toBe("1");
    expect(table.getAttribute("data-stale")).toBe("false");
    expect(screen.getByTestId("fetch-status").textContent).toBe("Loading 1–20…");
    expect(screen.getByTestId("range").textContent).toBe("—");
    expect((screen.getByRole("button", { name: "Next page" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Previous page" }) as HTMLButtonElement).disabled).toBe(true);

    await act(async () => {
      first.resolve(page(20, "A", 'W/"p1"'));
      await Promise.resolve();
    });
    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("1/5,600 Total"));
    expect(screen.queryByTestId("fetch-bar")).toBeNull();
    expect(table.querySelectorAll("tbody tr[data-symbol]")).toHaveLength(20);
  });
});

describe("a request that fails", () => {
  it("keeps the error and stops the bar", async () => {
    const turn = deferred<Response>();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(page(20, "A", 'W/"p1"'))
      .mockImplementationOnce(() => turn.promise);
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    render(<ScreenPanel view={VIEW} {...PANEL} />);
    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("1/5,600 Total"));
    const table = screen.getByTestId("grid-table");

    await user.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(screen.getByTestId("fetch-bar")).toBeTruthy());

    await act(async () => {
      turn.resolve(failure(503, "the scan is unavailable"));
      await Promise.resolve();
    });
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("503"));

    expect(screen.queryByTestId("fetch-bar")).toBeNull();
    expect(screen.getByTestId("fetch-status").textContent).toBe("");
    expect(table.style.opacity).toBe("1");
    expect(table.getAttribute("data-stale")).toBe("false");

    // It stays gone: a settled failure is not "still loading".
    await sleep(PENDING_FLASH_DELAY_MS + 100);
    expect(screen.queryByTestId("fetch-bar")).toBeNull();
    expect(screen.getByTestId("grid-scroll").getAttribute("aria-busy")).toBe("false");
  });
});

describe("reduced motion", () => {
  it("keeps the bar, the sentence and the fade, and drops the sweep", async () => {
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: query.includes("prefers-reduced-motion"),
      media: query,
      onchange: null,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    }));

    const turn = deferred<Response>();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(page(20, "A", 'W/"p1"'))
      .mockImplementationOnce(() => turn.promise);
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    render(<ScreenPanel view={VIEW} {...PANEL} />);
    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("1/5,600 Total"));

    const table = screen.getByTestId("grid-table");
    await user.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(screen.getByTestId("fetch-bar")).toBeTruthy());

    const bar = screen.getByTestId("fetch-bar");
    expect(bar.getAttribute("data-motion")).toBe("reduced");
    const sweep = screen.getByTestId("fetch-bar-sweep");
    expect(sweep.className).not.toContain("ztz-pending-sweep");
    expect(sweep.className).toContain("w-full");

    // The signals that carry the meaning survive the preference.
    expect(screen.getByTestId("fetch-status").textContent).toBe("Loading 21–40…");
    expect(table.style.opacity).toBe(String(STALE_OPACITY));
    expect(table.getAttribute("data-stale")).toBe("true");
    expect(
      (screen.getByRole("button", { name: "Next page" }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });
});

describe("a view switch", () => {
  it("aborts the page request and clears the pending chrome", async () => {
    const never = new Promise<Response>(() => undefined);
    const valueTurn = deferred<Response>();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(page(20, "A", 'W/"p1"'))
      .mockImplementationOnce(() => never)
      .mockImplementationOnce(() => valueTurn.promise);
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    const { rerender } = render(<ScreenPanel view={VIEW} {...PANEL} />);
    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("1/5,600 Total"));

    await user.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(screen.getByTestId("fetch-bar")).toBeTruthy());

    const abandoned = (fetchMock.mock.calls[1] as unknown as [string, RequestInit])[1];
    expect(abandoned.signal?.aborted).toBe(false);

    rerender(<ScreenPanel view={VALUE} {...PANEL} />);
    await waitFor(() => expect(abandoned.signal?.aborted).toBe(true));

    // A switch is a first load: the old view's rows are gone, so nothing is
    // dimmed — the skeleton carries it — and the bar is not stuck on.
    expect(screen.getByTestId("grid-scroll").getAttribute("aria-busy")).toBe("true");

    await act(async () => {
      valueTurn.resolve(page(2, "V", 'W/"v1"'));
      await Promise.resolve();
    });
    await waitFor(() => expect(screen.getByTestId("range").textContent).toBe("1/5,600 Total"));

    expect(screen.queryByTestId("fetch-bar")).toBeNull();
    expect(screen.getByTestId("fetch-status").textContent).toBe("");
    expect(screen.getByTestId("grid-table").style.opacity).toBe("1");
    expect(screen.getByTestId("grid-scroll").getAttribute("aria-busy")).toBe("false");
  });
});

describe("pendingLabel", () => {
  it("names the page, clamps the last one, and says 'Refreshing…' for a re-ask", () => {
    expect(pendingLabel({ refreshing: false, pageIndex: 1, limit: 20, total: TOTAL })).toBe(
      "Loading 21–40…",
    );
    expect(pendingLabel({ refreshing: false, pageIndex: 55, limit: 20, total: TOTAL })).toBe(
      "Loading 1101–1120…",
    );
    expect(pendingLabel({ refreshing: false, pageIndex: 0, limit: 20, total: 42 })).toBe(
      "Loading 1–20…",
    );
    expect(pendingLabel({ refreshing: true, pageIndex: 3, limit: 20, total: TOTAL })).toBe(
      "Refreshing…",
    );
  });
});
