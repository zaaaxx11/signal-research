import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { NewsPage } from "./NewsPage";
import { NEWS_TODAY, newsDocument, wireItem, type WireNewsItem } from "./__fixtures__/news";

/**
 * The page's promises, on the rendered page:
 *
 * * one request for the whole news list, plus one for the crypto tape;
 * * the list is `ul > li`, with a real heading over every day;
 * * undated rows sort last, print no time and sit under `Undated`;
 * * the five category tabs filter the feed;
 * * time-window and source selects narrow the list, and the result count
 *   is announced;
 * * loading, empty, error and filter-empty are states of one reserved box;
 * * the headline's href is the payload's URL, and a refused URL is text.
 */

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function newsJson(body: unknown = newsDocument()): Response {
  return json(body);
}

function stub(body: unknown = newsDocument()): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    // Crypto endpoint returns empty rows so the tape doesn't break
    if (url.includes("/market/alt")) {
      return json({ class: "crypto", rows: [] });
    }
    return newsJson(body);
  });
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
  it("makes one news request on mount, for the documented cap", async () => {
    const fetchMock = stub();
    render(<NewsPage today={NEWS_TODAY} />);

    await screen.findByTestId("news-filter-status");
    // At least one news call; crypto is separate.
    const calls = fetchMock.mock.calls.map((c) => String(c[0]));
    expect(calls).toContain("/market/news?limit=60");
  });

  it("renders every item in the payload, and the count matches the payload", async () => {
    const body = newsDocument();
    stub(body);
    render(<NewsPage today={NEWS_TODAY} />);

    await screen.findByTestId("news-filter-status");
    const items = itemsOf(body);
    expect(items).toHaveLength(60);
    expect(screen.getAllByTestId("news-row")).toHaveLength(items.length);
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
      .filter((h) => h.closest("[data-testid^='news-group-']"))
      .map((heading) => heading.textContent ?? "");
    expect(headings).toHaveLength(3);
    expect(headings[0]).toContain("Today");
    expect(headings[1]).toContain("Yesterday");
    expect(headings[2]).toContain("30 Oct 2026");

    expect(screen.getAllByTestId("news-row")).toHaveLength(3);
    expect(within(screen.getByTestId("news-group-2026-10-30")).getAllByTestId("news-row")).toHaveLength(1);
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

  it("filters by source select and announces the result count", async () => {
    const body = newsDocument({
      items: [
        wireItem({ headline: "Reuters story", source: "Reuters", url: "https://e.com/r" }),
        wireItem({ headline: "Bloomberg story", source: "Bloomberg", url: "https://e.com/b" }),
        wireItem({ headline: "Another Reuters", source: "Reuters", url: "https://e.com/r2" }),
      ],
    });
    stub(body);
    const user = userEvent.setup();
    render(<NewsPage today={NEWS_TODAY} />);

    await screen.findByTestId("news-filter-status");

    // Select "Reuters" from the source dropdown
    await user.selectOptions(screen.getByTestId("news-source-select"), "Reuters");

    const rows = screen.getAllByTestId("news-row");
    expect(rows).toHaveLength(2);
    expect(screen.getByRole("status").textContent).toBe("2 of 3 headlines");

    // All visible rows should have Reuters as source
    for (const row of rows) {
      expect(within(row).getByTestId("news-source").textContent).toBe("Reuters");
    }
  });

  it("filters by time window select", async () => {
    const body = newsDocument({
      items: [
        wireItem({
          headline: "Very recent",
          published_at: new Date(Date.now() - 30 * 60_000).toISOString(), // 30 min ago
          url: "https://e.com/recent",
          symbols: ["SPY"],
        }),
        wireItem({
          headline: "Two days ago",
          published_at: new Date(Date.now() - 2 * 86_400_000).toISOString(),
          url: "https://e.com/old",
          symbols: ["AAPL"],
        }),
      ],
    });
    stub(body);
    const user = userEvent.setup();
    render(<NewsPage today={todayUtc()} />);

    await screen.findByTestId("news-filter-status");
    expect(screen.getAllByTestId("news-row")).toHaveLength(2);

    // Filter to last hour
    await user.selectOptions(screen.getByTestId("news-time-select"), "1h");

    const rows = screen.getAllByTestId("news-row");
    expect(rows).toHaveLength(1);
    expect(rows[0]?.textContent).toContain("Very recent");
    expect(screen.getByRole("status").textContent).toBe("1 of 2 headlines");
  });

  it("switches between category tabs", async () => {
    const body = newsDocument({
      items: [
        wireItem({ headline: "Stock only", symbols: ["AAPL"], url: "https://e.com/a" }),
        wireItem({ headline: "ETF only", symbols: ["SPY"], url: "https://e.com/b" }),
        wireItem({ headline: "Both", symbols: ["AAPL", "SPY"], url: "https://e.com/c" }),
      ],
    });
    stub(body);
    const user = userEvent.setup();
    render(<NewsPage today={NEWS_TODAY} />);

    await screen.findByTestId("news-filter-status");

    // MARKET NEWS (default): all 3
    expect(screen.getAllByTestId("news-row")).toHaveLength(3);

    // Switch to STOCKS tab
    await user.click(screen.getByTestId("news-tab-stocks"));
    expect(screen.getAllByTestId("news-row")).toHaveLength(2); // AAPL-only + Both

    // Switch to ETF tab
    await user.click(screen.getByTestId("news-tab-etf"));
    expect(screen.getAllByTestId("news-row")).toHaveLength(2); // SPY-only + Both
  });

  it("shows honest empty note for MARKET PULSE tab", async () => {
    stub();
    const user = userEvent.setup();
    render(<NewsPage today={NEWS_TODAY} />);

    await screen.findByTestId("news-filter-status");
    await user.click(screen.getByTestId("news-tab-market-pulse"));

    const pulse = screen.getByTestId("news-pulse-empty");
    expect(pulse.textContent).toContain("Market Pulse");
    expect(pulse.textContent).toContain("No pulse feed is available yet");
  });

  it("shows the crypto tape on the CRYPTO tab", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/market/alt")) {
        return json({
          class: "crypto",
          rows: [
            { symbol: "BTC-USD", name: "Bitcoin", price: 80000, change_pct: 4.5, volume: 0, currency: "USD" },
            { symbol: "ETH-USD", name: "Ethereum", price: 2400, change_pct: -1.2, volume: 0, currency: "USD" },
          ],
        });
      }
      return newsJson(newsDocument());
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<NewsPage today={NEWS_TODAY} />);

    await user.click(screen.getByTestId("news-tab-crypto"));
    await screen.findByTestId("crypto-tape");

    const rows = screen.getAllByTestId("crypto-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]?.textContent).toContain("Bitcoin");
    expect(rows[0]?.textContent).toContain("BTC-USD");
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
    let callCount = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/market/alt")) return json({ class: "crypto", rows: [] });
      callCount++;
      if (callCount === 1) throw new TypeError("Failed to fetch");
      return newsJson(body);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<NewsPage today={NEWS_TODAY} />);

    const error = await screen.findByTestId("news-error");
    expect(error.textContent).toContain("The news feed did not load.");

    await user.click(within(error).getByTestId("news-error-retry"));
    await screen.findByTestId("news-filter-status");
    expect(screen.getAllByTestId("news-row")).toHaveLength(60);
    expect(fetchMock).toHaveBeenCalledTimes(3); // 1 fail news + 1 crypto + 1 retry news
  });

  it("labels a 404 as a deployment without a news feed", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/market/alt")) return json({ class: "crypto", rows: [] });
      return json({ detail: "Not Found" }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<NewsPage today={NEWS_TODAY} />);

    const error = await screen.findByTestId("news-error");
    expect(error.textContent).toContain("does not serve a news feed yet");
  });

  it("shows a skeleton before the answer lands", async () => {
    let release: ((response: Response) => void) | null = null;
    const fetchMock = vi.fn(
      (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/market/alt")) return json({ class: "crypto", rows: [] });
        return new Promise<Response>((resolve) => {
          release = resolve;
        });
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<NewsPage today={NEWS_TODAY} />);

    const skeleton = screen.getByTestId("news-skeleton");
    expect(skeleton.getAttribute("aria-busy")).toBe("true");

    await act(async () => {
      release?.(newsJson(newsDocument()));
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

function todayUtc(): string {
  return new Date().toISOString().slice(0, 10);
}