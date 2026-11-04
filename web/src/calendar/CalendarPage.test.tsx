import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CALENDAR_RESERVED_HEIGHT, CALENDAR_SKELETON_ROWS, CalendarPage } from "./CalendarPage";

/**
 * The Calendar page's promises, on the rendered page:
 *
 * * one request for every day in the window;
 * * days read forward, and neither an empty day nor a past day becomes a
 *   section even when the server sent it;
 * * each day states its true total, lists at most fifty names, and says how
 *   many it did not list;
 * * every name is a real link to its own page;
 * * loading, empty and error are states of one box, so the answer cannot move
 *   the page.
 */

const TODAY = "2026-11-02";

function symbols(count: number) {
  return Array.from({ length: count }, (_, index) => ({
    s: `SYM${index}`,
    n: `Company ${index}`,
  }));
}

function calendarDocument(overrides: Record<string, unknown> = {}) {
  return {
    as_of: "2026-11-01T22:10:00Z",
    region: "us",
    from: "2026-11-02",
    to: "2026-11-23",
    buckets: [
      { date: "2026-11-05", count: 3, symbols: symbols(3) },
      { date: "2026-11-02", count: 42, symbols: symbols(42) },
      // Neither of these may become a section.
      { date: "2026-11-03", count: 0, symbols: [] },
      { date: "2026-10-30", count: 5, symbols: symbols(5) },
    ],
    ...overrides,
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function stub(body: unknown = calendarDocument()) {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL) => json(body));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  window.history.replaceState(null, "", "/");
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("CalendarPage", () => {
  it("asks for the window exactly once", async () => {
    const fetchMock = stub();
    render(<CalendarPage today={TODAY} />);

    await screen.findByTestId("calendar-daylist");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe("/market/calendar?region=us&days=21&kind=earnings");
  });

  it("draws days forward and never draws a past day or an empty one", async () => {
    stub();
    render(<CalendarPage today={TODAY} />);
    await screen.findByTestId("calendar-daylist");

    const days = screen
      .getAllByTestId(/^calendar-bucket-\d{4}-\d{2}-\d{2}$/)
      .map((el) => el.getAttribute("data-day"));
    expect(days).toEqual(["2026-11-02", "2026-11-05"]);
    expect(screen.queryByTestId("calendar-bucket-2026-10-30")).toBeNull();
    expect(screen.queryByTestId("calendar-bucket-2026-11-03")).toBeNull();
  });

  it("states each day's true total, not the number of names it lists", async () => {
    stub();
    render(<CalendarPage today={TODAY} />);
    await screen.findByTestId("calendar-daylist");

    const monday = screen.getByTestId("calendar-bucket-2026-11-02");
    expect(monday.textContent).toContain("Mon 2 Nov");
    expect(monday.textContent).toContain("42 reports");
    expect(within(monday).getAllByTestId("calendar-symbol-row")).toHaveLength(42);

    const thursday = screen.getByTestId("calendar-bucket-2026-11-05");
    expect(thursday.textContent).toContain("Thu 5 Nov");
    expect(thursday.textContent).toContain("3 reports");
  });

  it("lists at most fifty names and says how many it left out", async () => {
    stub(
      calendarDocument({
        buckets: [{ date: "2026-11-06", count: 137, symbols: symbols(50) }],
      }),
    );
    render(<CalendarPage today={TODAY} />);
    await screen.findByTestId("calendar-daylist");

    const friday = screen.getByTestId("calendar-bucket-2026-11-06");
    expect(within(friday).getAllByTestId("calendar-symbol-row")).toHaveLength(50);
    expect(friday.textContent).toContain("137 reports");
    expect(screen.getByTestId("calendar-more-2026-11-06").textContent).toContain("+87 more");
  });

  it("names each row: a logo, the ticker, the company, and a link to its page", async () => {
    stub();
    render(<CalendarPage today={TODAY} />);
    await screen.findByTestId("calendar-daylist");

    const monday = screen.getByTestId("calendar-bucket-2026-11-02");
    const first = within(monday).getAllByTestId("calendar-symbol-row")[0];
    expect(first?.textContent).toContain("SYM0");
    expect(first?.textContent).toContain("Company 0");
    expect(first?.querySelector("[data-logo='SYM0']")).toBeTruthy();

    const link = within(monday).getByRole("link", { name: /SYM0/ });
    expect(link.getAttribute("href")).toBe("/app/stock/SYM0");
  });

  it("gives the rail one anchor per drawn day, pointing at that day", async () => {
    stub();
    render(<CalendarPage today={TODAY} />);
    await screen.findByTestId("calendar-daylist");

    const rail = screen.getByTestId("calendar-rail");
    const links = within(rail).getAllByRole("link");
    expect(links).toHaveLength(2);
    expect(links[0]?.getAttribute("href")).toBe("#day-2026-11-02");
    expect(links[0]?.textContent).toContain("Mon 2 Nov");
    expect(links[1]?.getAttribute("href")).toBe("#day-2026-11-05");

    // The target exists, so the anchor is not a dead fragment.
    for (const link of links) {
      const id = (link.getAttribute("href") ?? "").slice(1);
      expect(document.getElementById(id)).toBeTruthy();
    }
  });

  it("keeps every row keyboard reachable", async () => {
    stub();
    render(<CalendarPage today={TODAY} />);
    await screen.findByTestId("calendar-daylist");

    const monday = screen.getByTestId("calendar-bucket-2026-11-02");
    const link = within(monday).getAllByRole("link")[0];
    link?.focus();
    expect(document.activeElement).toBe(link);
  });

  it("names the window when there is nothing in it", async () => {
    stub(calendarDocument({ buckets: [] }));
    render(<CalendarPage today={TODAY} />);

    const empty = await screen.findByTestId("calendar-empty");
    expect(empty.textContent).toMatch(/no earnings events in the next 21 days/i);
    expect(screen.queryByTestId("calendar-daylist")).toBeNull();
  });

  it("is empty, not blank, when every bucket it received is past or empty", async () => {
    stub(
      calendarDocument({
        buckets: [
          { date: "2026-10-30", count: 5, symbols: symbols(5) },
          { date: "2026-11-03", count: 0, symbols: [] },
        ],
      }),
    );
    render(<CalendarPage today={TODAY} />);

    const empty = await screen.findByTestId("calendar-empty");
    expect(empty.textContent).toMatch(/no earnings events in the next 21 days/i);
  });

  it("reserves one box for loading and for the answer", async () => {
    let release: ((response: Response) => void) | undefined;
    const pending = new Promise<Response>((resolve) => {
      release = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(() => pending),
    );

    render(<CalendarPage today={TODAY} />);

    expect(screen.getAllByTestId("calendar-skeleton-row")).toHaveLength(CALENDAR_SKELETON_ROWS);
    expect(screen.getByTestId("calendar-frame").style.minHeight).toBe(
      `${CALENDAR_RESERVED_HEIGHT}px`,
    );

    release?.(json(calendarDocument()));
    await screen.findByTestId("calendar-daylist");
    expect(screen.getByTestId("calendar-frame").style.minHeight).toBe(
      `${CALENDAR_RESERVED_HEIGHT}px`,
    );
    expect(screen.queryByTestId("calendar-skeleton")).toBeNull();
  });

  it("keeps a failure actionable: it says so and retries on demand", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json({ errorMessage: "calendar upstream down" }, 503))
      .mockResolvedValueOnce(json(calendarDocument()));
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    render(<CalendarPage today={TODAY} />);

    const failure = await screen.findByTestId("calendar-error");
    expect(failure.getAttribute("role")).toBe("alert");
    expect(failure.textContent).toMatch(/did not load/i);
    expect(failure.textContent).toContain("calendar upstream down");

    await user.click(screen.getByRole("button", { name: /retry/i }));
    await screen.findByTestId("calendar-daylist");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("keeps the server's bookkeeping and the field names out of the copy", async () => {
    stub();
    render(<CalendarPage today={TODAY} />);
    await screen.findByTestId("calendar-daylist");

    const text = screen.getByTestId("calendar-page").textContent ?? "";
    expect(text).not.toMatch(/as_of|as of/i);
    expect(text).not.toContain("2026-11-01T22:10:00Z");
    expect(text).not.toMatch(/\b(finviz|provider|source|region|buckets?)\b/i);
  });
});
