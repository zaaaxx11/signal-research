import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { NewsPage } from "./NewsPage";
import { NEWS_TODAY, newsDocument, wireItem, type WireNewsItem } from "./__fixtures__/news";

/**
 * The page's promises, on the rendered page:
 *
 * * one request for the whole list, and the row count is the payload's;
 * * the list is `ul > li`, with a real heading over every day;
 * * undated rows sort last, print no time and sit under `Undated`;
 * * the filter narrows by headline and by the symbol the chips print, and the
 *   result count is announced;
 * * loading, empty, error and filter-empty are states of one reserved box;
 * * the headline's href is the payload's URL, and a refused URL is text.
 */

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function stub(body: unknown = newsDocument()): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL) => json(body));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function itemsOf(body: Record<string, unknown>): WireNewsItem[] {
  return body["items"] as WireNewsItem[];
}

afterEach(() => {
  window.history.replaceState(null, "", "/");
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("NewsPage", () => {
  it("makes exactly one request on mount, for the documented cap", async () => {
    const fetchMock = stub();
    render(<NewsPage today={NEWS_TODAY} />);

    await screen.findByTestId("news-filter-status");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe("/market/news?limit=60");
  });

  it("renders every item in the payload, and the count matches the payload", async () => {
    const body = newsDocument();
    stub(body);
    render(<NewsPage today={NEWS_TODAY} />);

    await screen.findByTestId("news-filter-status");
    const items = itemsOf(body);
    expect(items).toHaveLength(60);
    expect(screen.getAllByTestId("news-row")).toHaveLength(items.length);
    // The count line is the payload's count, not a number the page invented.
    expect(screen.getByTestId("news-filter-status").textContent).toBe("60 headlines");
  });

  it("does not dedupe: the same headline on two URLs is two rows", async () => {
    const body = newsDocument({
      items: [
        wireItem({ headline: "Same words", url: "https://e.com/a" }),
        wireItem({ headline: "Same words", url: "https://e.com/b" }),
      ],
    });
    stub(body);
    render(<NewsPage today={NEWS_TODAY} />);

    await screen.findByTestId("news-filter-status");
    const rows = screen.getAllByTestId("news-row");
    expect(rows).toHaveLength(2);
    expect(screen.getByTestId("news-filter-status").textContent).toBe("2 headlines");
    expect(rows.map((row) => within(row).getByTestId("news-headline-link").getAttribute("href"))).toEqual([
      "https://e.com/a",
      "https://e.com/b",
    ]);
  });

  it("sorts an undated headline last, prints no time, and never calls it today", async () => {
    const body = newsDocument({
      items: [
        wireItem({ headline: "No stamp", published_at: null, url: "https://e.com/undated" }),
        wireItem({
          headline: "Stamped",
          published_at: "2026-11-01T08:00:00Z",
          url: "https://e.com/stamped",
        }),
      ],
    });
    stub(body);
    render(<NewsPage today={NEWS_TODAY} />);

    await screen.findByTestId("news-filter-status");
    const rows = screen.getAllByTestId("news-row");
    expect(rows).toHaveLength(2);

    const undated = rows[1];
    expect(undated?.textContent).toContain("No stamp");
    expect(within(undated as HTMLElement).queryByTestId("news-time")).toBeNull();

    const undatedGroup = screen.getByTestId("news-group-undated");
    expect(within(undatedGroup).getByRole("heading", { level: 2 }).textContent).toContain(
      "Undated",
    );
    expect(within(undatedGroup).getByTestId("news-row")).toBe(undated);

    // It is not under today's heading, and nothing in its row claims today.
    const todayGroup = screen.getByTestId("news-group-2026-11-01");
    expect(within(todayGroup).queryByText("No stamp")).toBeNull();
    expect(undated?.textContent).not.toContain("Today");
  });

  it("groups across midnight: Today, Yesterday, then the date", async () => {
    const body = newsDocument({
      items: [
        wireItem({
          headline: "after midnight",
          published_at: "2026-11-01T00:05:00Z",
          url: "https://e.com/0",
        }),
        wireItem({
          headline: "before midnight",
          published_at: "2026-10-31T23:55:00Z",
          url: "https://e.com/1",
        }),
        wireItem({
          headline: "the day before",
          published_at: "2026-10-30T12:00:00Z",
          url: "https://e.com/2",
        }),
      ],
    });
    stub(body);
    render(<NewsPage today={NEWS_TODAY} />);

    await screen.findByTestId("news-filter-status");
    const headings = screen
      .getAllByRole("heading", { level: 2 })
      .map((heading) => heading.textContent ?? "");
    expect(headings).toHaveLength(3);
    expect(headings[0]).toContain("Today");
    expect(headings[1]).toContain("Yesterday");
    expect(headings[2]).toContain("30 Oct 2026");

    // A day with one item still gets its heading and its list.
    expect(screen.getAllByTestId("news-row")).toHaveLength(3);
    expect(within(screen.getByTestId("news-group-2026-10-30")).getAllByTestId("news-row")).toHaveLength(
      1,
    );
  });

  it("draws a `ul` of `li` rows, each with a focusable headline link", async () => {
    stub();
    render(<NewsPage today={NEWS_TODAY} />);

    await screen.findByTestId("news-filter-status");
    for (const list of screen.getAllByTestId("news-list")) {
      expect(list.tagName).toBe("UL");
      for (const child of Array.from(list.children)) expect(child.tagName).toBe("LI");
    }
    const rows = screen.getAllByTestId("news-row");
    expect(rows.length).toBeGreaterThan(0);
    for (const row of rows) {
      const link = within(row).getByTestId("news-headline-link");
      expect(link.getAttribute("tabindex")).toBeNull();
      expect(link.getAttribute("href")).toMatch(/^https:\/\//);
    }
  });

  it("links the headline out with target=_blank and rel=noopener noreferrer", async () => {
    const body = newsDocument({
      items: [
        wireItem({ headline: "Outbound", url: "https://www.reuters.com/markets/aapl" }),
      ],
    });
    stub(body);
    render(<NewsPage today={NEWS_TODAY} />);

    await screen.findByTestId("news-filter-status");
    const link = screen.getByTestId("news-headline-link");
    expect(link.getAttribute("href")).toBe("https://www.reuters.com/markets/aapl");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
  });

  it("names the source and the symbols in the headline link's accessible name", async () => {
    const body = newsDocument({
      items: [
        wireItem({
          headline: "Apple beats the street",
          source: "Reuters",
          symbols: ["AAPL", "SPY"],
          url: "https://www.reuters.com/markets/aapl",
        }),
      ],
    });
    stub(body);
    render(<NewsPage today={NEWS_TODAY} />);

    await screen.findByTestId("news-filter-status");
    const link = screen.getByTestId("news-headline-link");
    const name = link.textContent ?? "";
    expect(name).toContain("Apple beats the street");
    expect(name).toContain("Source: Reuters.");
    expect(name).toContain("Symbols: AAPL, SPY.");
  });

  it("links each symbol chip to its stock page", async () => {
    const body = newsDocument({
      items: [wireItem({ headline: "Chips", symbols: ["AAPL", "SPY"] })],
    });
    stub(body);
    render(<NewsPage today={NEWS_TODAY} />);

    await screen.findByTestId("news-filter-status");
    const chips = screen.getAllByTestId("news-chip");
    expect(chips.map((chip) => chip.textContent)).toEqual(["AAPL", "SPY"]);
    expect(chips[0]?.getAttribute("href")).toBe("/app/stock/AAPL");
    expect(chips[0]?.getAttribute("aria-label")).toBe("AAPL stock page");
  });

  it("filters by the symbol chip text and announces the result count", async () => {
    const body = newsDocument();
    stub(body);
    const user = userEvent.setup();
    render(<NewsPage today={NEWS_TODAY} />);

    await screen.findByTestId("news-filter-status");
    const expected = itemsOf(body).filter((item) => item.symbols.includes("AAPL")).length;
    expect(expected).toBeGreaterThan(0);

    await user.type(screen.getByTestId("news-filter"), "aapl");
    expect(screen.getAllByTestId("news-row")).toHaveLength(expected);

    const status = screen.getByRole("status");
    expect(status.getAttribute("aria-live")).toBe("polite");
    expect(status.textContent).toBe(`${expected} of 60 match “aapl”`);

    // Every visible row really carries the symbol.
    for (const row of screen.getAllByTestId("news-row")) {
      expect(row.getAttribute("data-symbols")?.split(" ")).toContain("AAPL");
    }
  });

  it("filters by headline text", async () => {
    const body = newsDocument({
      items: [
        wireItem({
          headline: "Is Apple a Top Tech Stock to Buy Now?",
          url: "https://e.com/aapl",
        }),
        wireItem({ headline: "Treasury yields slip", url: "https://e.com/yields" }),
        wireItem({ headline: "Oil steadies", url: "https://e.com/oil" }),
      ],
    });
    stub(body);
    const user = userEvent.setup();
    render(<NewsPage today={NEWS_TODAY} />);

    await screen.findByTestId("news-filter-status");
    await user.type(screen.getByTestId("news-filter"), "top tech");

    const rows = screen.getAllByTestId("news-row");
    expect(rows).toHaveLength(1);
    expect(rows[0]?.textContent).toContain("Is Apple a Top Tech Stock to Buy Now?");
    expect(screen.getByRole("status").textContent).toBe("1 of 3 match “top tech”");
  });

  it("names the query when the filter matches nothing", async () => {
    stub();
    const user = userEvent.setup();
    render(<NewsPage today={NEWS_TODAY} />);

    await screen.findByTestId("news-filter-status");
    await user.type(screen.getByTestId("news-filter"), "zzzqqq");

    expect(screen.queryAllByTestId("news-row")).toHaveLength(0);
    const empty = screen.getByTestId("news-filter-empty");
    expect(empty.textContent).toContain("zzzqqq");
    expect(screen.getByRole("status").textContent).toBe("0 of 60 match “zzzqqq”");
  });

  it("says the feeds returned nothing when the list is empty", async () => {
    stub(newsDocument({ items: [] }));
    render(<NewsPage today={NEWS_TODAY} />);

    const empty = await screen.findByTestId("news-empty");
    expect(empty.textContent).toContain("The feeds returned no headlines.");
    expect(screen.queryByTestId("news-filter")).toBeNull();
  });

  it("shows an error, then retries into the list", async () => {
    const body = newsDocument();
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(json(body));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<NewsPage today={NEWS_TODAY} />);

    const error = await screen.findByTestId("news-error");
    expect(error.textContent).toContain("The news feed did not load.");

    await user.click(within(error).getByRole("button", { name: "Retry" }));
    await screen.findByTestId("news-filter-status");
    expect(screen.getAllByTestId("news-row")).toHaveLength(60);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("labels a 404 as a deployment without a news feed", async () => {
    const fetchMock = vi.fn(async () => json({ detail: "Not Found" }, 404));
    vi.stubGlobal("fetch", fetchMock);
    render(<NewsPage today={NEWS_TODAY} />);

    const error = await screen.findByTestId("news-error");
    expect(error.textContent).toContain("does not serve a news feed yet");
  });

  it("shows a reserved-height skeleton before the answer lands", async () => {
    let release: ((response: Response) => void) | null = null;
    const fetchMock = vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          release = resolve;
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<NewsPage today={NEWS_TODAY} />);

    const skeleton = screen.getByTestId("news-skeleton");
    expect(skeleton.getAttribute("aria-busy")).toBe("true");
    expect(skeleton.style.minHeight.endsWith("px")).toBe(true);
    expect(Number.parseInt(skeleton.style.minHeight, 10)).toBeGreaterThan(200);

    await act(async () => {
      release?.(json(newsDocument()));
    });
    await screen.findByTestId("news-filter-status");
    expect(screen.queryByTestId("news-skeleton")).toBeNull();
  });

  it("renders a headline containing <script> as text, not markup", async () => {
    const body = newsDocument({
      items: [
        wireItem({
          headline: 'Markets <script>alert("x")</script> after the bell',
          url: "https://e.com/script",
        }),
      ],
    });
    stub(body);
    render(<NewsPage today={NEWS_TODAY} />);

    await screen.findByTestId("news-filter-status");
    const row = screen.getByTestId("news-row");
    expect(row.textContent).toContain('<script>alert("x")</script>');
    // React escaped it: the string is text, and no script element was created.
    expect(document.querySelectorAll("script")).toHaveLength(0);
    expect(row.querySelector("script")).toBeNull();
  });

  it("refuses a javascript: URL: the headline is text, never a link", async () => {
    const body = newsDocument({
      items: [
        wireItem({ headline: "Do not run me", url: "javascript:alert(document.cookie)" }),
      ],
    });
    stub(body);
    render(<NewsPage today={NEWS_TODAY} />);

    await screen.findByTestId("news-filter-status");
    const row = screen.getByTestId("news-row");
    expect(within(row).queryByTestId("news-headline-link")).toBeNull();
    expect(within(row).getByTestId("news-plain").textContent).toContain("Do not run me");
    expect(document.querySelector('a[href^="javascript:"]')).toBeNull();
  });
});
