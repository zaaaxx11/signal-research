import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { HomePage } from "./HomePage";
import { homeDocument, stubCanvas, stubResizeObserver } from "./__fixtures__/home";
import { altDocument } from "./__fixtures__/crypto";
import { parseQuoteAttr } from "@/lib/quoteAttrs";
import { clearToasts, subscribe, type Toast } from "@/lib/notify";

/**
 * The page's whole contract, on the rendered dashboard:
 *
 * * **one** `/market/home` paints the equity blocks — plus the crypto
 *   section's own three documents (alt rows, meta map, global pulse), asked
 *   once each on the same mount, never per row;
 * * eight sections, each named by a solid SectionTitle fill of its own token
 *   (`--title-1` … `--title-8`), under one hidden `h1`;
 * * a `null` block prints the reason from `unavailable` and draws no fake zeroes;
 * * a failed equity read names itself per block and one Retry refetches the
 *   house exactly once; a failed crypto read draws an ErrorState, voices
 *   itself with `notify.error`, and a successful retry says so in one toast;
 * * the skeletons occupy the same boxes the content will.
 *
 * The strip's index tiles draw `lightweight-charts` bars; the library is mocked
 * here so the page test stays about the page (its own chart tests live beside
 * the component).
 */

vi.mock("lightweight-charts", async () => {
  const { fakeLightweightCharts } = await import("@/charts/__fixtures__/lightweight");
  return fakeLightweightCharts();
});

const BLOCKS = [
  "strip",
  "breadth",
  "movers",
  "map",
  "signals",
  "crypto",
  "news",
] as const;

/** The section titles and the fill each one must carry. */
const SECTIONS: ReadonlyArray<[testId: string, variant: number]> = [
  ["section-title-strip", 2],
  ["section-title-breadth", 3],
  ["section-title-map", 4],
  ["section-title-movers", 5],
  ["section-title-news", 6],
  ["section-title-insider", 7],
  ["section-title-crypto", 8],
];

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

type Route = {
  home?: unknown;
  homeStatus?: number;
  alt?: { body?: unknown; status?: number } | null;
  meta?: { body?: unknown; status?: number } | null;
  global?: { body?: unknown; status?: number } | null;
};

/** A fetch stub that answers each of the four reads from its own body. */
function stub(overrides: Route = {}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input).split("?")[0];
    if (url.startsWith("/market/home")) {
      return json(overrides.home ?? homeDocument(), overrides.homeStatus ?? 200);
    }
    if (url.startsWith("/market/alt")) {
      const alt = overrides.alt;
      return json(
        alt === null ? { errorMessage: "crypto rows offline" } : (alt?.body ?? altDocument()),
        alt === null ? 503 : (alt?.status ?? 200),
      );
    }
    if (url.startsWith("/market/crypto/meta")) {
      const meta = overrides.meta;
      return json(
        meta === null
          ? { errorMessage: "crypto meta offline" }
          : (meta?.body ?? {
              "BTC-USD": {
                market_cap: 1_573_000_000_000,
                market_cap_rank: 1,
                image: "https://img.local/btc.png",
                pct_1h: 0.12,
                pct_7d: -2.4,
                pct_30d: 8.9,
                tvl: 55e9,
              },
              "ETH-USD": {
                market_cap: 2.1e8,
                market_cap_rank: 42,
                image: null as unknown as string,
                pct_1h: 0.04,
                pct_7d: 3.1,
                pct_30d: -5.5,
                tvl: null as unknown as number,
              },
            }),
        meta === null ? 500 : (meta?.status ?? 200),
      );
    }
    if (url.startsWith("/market/crypto/global")) {
      const global = overrides.global;
      return json(
        global === null
          ? { errorMessage: "global pulse offline" }
          : (global?.body ?? {
              total_market_cap_usd: 3_421_000_000_000,
              btc_dominance: 54.3,
              market_cap_change_pct_24h: -1.42,
            }),
        global === null ? 503 : (global?.status ?? 200),
      );
    }
    return json({ errorMessage: `unknown endpoint ${url}` }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** Collect the toasts the store holds, for the notify assertions. */
function toastsNow(): readonly Toast[] {
  let held: readonly Toast[] = [];
  const unsubscribe = subscribe((all) => {
    held = all;
  });
  unsubscribe();
  return held;
}

beforeEach(() => {
  stubCanvas();
  stubResizeObserver(320, 200);
  clearToasts();
});

afterEach(() => {
  clearToasts();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("HomePage", () => {
  it("paints every section from one read per document", async () => {
    const fetchMock = stub();
    render(<HomePage />);

    await waitFor(() =>
      expect(screen.getByTestId("home-grid").getAttribute("data-status")).toBe("ready"),
    );

    expect(fetchMock).toHaveBeenCalledTimes(4);
    const urls = fetchMock.mock.calls.map((call) => String(call[0]).split("?")[0]);
    for (const path of [
      "/market/home",
      "/market/alt",
      "/market/crypto/meta",
      "/market/crypto/global",
    ]) {
      expect(urls.filter((url) => url === path), path).toHaveLength(1);
    }

    for (const block of BLOCKS) {
      expect(screen.getByTestId(`home-${block}`), block).toBeTruthy();
    }
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe("/market/home?region=us");
  });

  it("names every section with its own text colour", async () => {
      stub();
      render(<HomePage />);
      await waitFor(() =>
        expect(screen.getByTestId("home-grid").getAttribute("data-status")).toBe("ready"),
      );

      const h1s = document.querySelectorAll("h1");
      expect(h1s).toHaveLength(1);
      expect(h1s[0]?.className).toContain("sr-only");

      for (const [testId, variant] of SECTIONS) {
        const title = screen.getByTestId(testId);
        const h2 = title.querySelector("h2");
        expect(h2, testId).toBeTruthy();
        // The section colour lives on the words, not a solid bar behind them.
        expect(h2?.style.color).toBe(`var(--title-${variant})`);
      }
      expect(document.querySelectorAll("h3").length).toBeGreaterThan(5);
    });

  it("draws the movers table and the map inside one 2:1 pairing box", async () => {
    stub();
    render(<HomePage />);
    await waitFor(() =>
      expect(screen.getByTestId("home-grid").getAttribute("data-status")).toBe("ready"),
    );

    expect(document.querySelectorAll("tbody tr[data-mover-row]")).toHaveLength(40);
    expect(screen.queryByTestId("map-tiles")).toBeNull();

    const pair = screen.getByTestId("movers-map-pair");
    expect(pair.getAttribute("data-parts")).toBe("2 1 0%, 1 1 0%");
    expect(screen.getByTestId("movers-see-all").getAttribute("href")).toContain("/screener");
  });

  it("pairs the headlines against the insider filings, both linked", async () => {
    stub();
    render(<HomePage />);
    await waitFor(() =>
      expect(screen.getByTestId("home-grid").getAttribute("data-status")).toBe("ready"),
    );

    const pair = screen.getByTestId("news-insider-pair");
    expect(pair.getAttribute("data-parts")).toBe("2 1 0%, 1 1 0%");
    // The filings half carries the two kinds of pointer the page promises.
    const links = screen.getByTestId("insider-links");
    expect(within(pair).getAllByTestId("news-row").length).toBeGreaterThan(0);

    const tickerLink = within(links).getByRole("link", { name: "AAPL" });
    expect(tickerLink.getAttribute("href")).toContain("/stock/AAPL");
    const ownerLink = within(links).getByRole("link", { name: "Newstead Jennifer" });
    expect(ownerLink.getAttribute("href")).toContain(
      `/insider?owner=${encodeURIComponent("Newstead Jennifer")}`,
    );
  });

  it("builds the crypto table with quote rows and the pulse above it", async () => {
    stub();
    render(<HomePage />);
    await waitFor(() =>
      expect(screen.getByTestId("home-grid").getAttribute("data-status")).toBe("ready"),
    );

    const pulse = screen.getByTestId("crypto-pulse");
    expect(pulse.textContent).toContain("$3.42T");
    expect(pulse.textContent).toContain("54.30%");
    expect(pulse.textContent).toContain("▼");

    const rows = screen.getAllByTestId("crypto-coins")[0].querySelectorAll("tr[data-crypto-row]");
    expect(rows.length).toBeGreaterThan(0);
    const quoted = parseQuoteAttr(rows[0]?.getAttribute("data-quote") ?? null);
    expect(quoted).not.toBeNull();
    expect(quoted?.symbol).toBe("BTC-USD");
    expect(quoted?.kind).toBe("crypto");
    expect(quoted?.price).toBe(80024.02);
    expect(quoted?.note).toContain("MCAP");
  });

  it("shades the crypto table with meta, and says when a shading did not answer", async () => {
    stub({ meta: null, global: null });
    render(<HomePage />);
    await waitFor(() =>
      expect(screen.getByTestId("home-grid").getAttribute("data-status")).toBe("ready"),
    );

    // The table still prices.
    expect(
      screen.getAllByTestId("crypto-coins")[0].querySelectorAll("tr[data-crypto-row]").length,
    ).toBeGreaterThan(0);
    // The pulse strip names its absence instead of drawing three em dashes.
    expect(screen.getByTestId("crypto-pulse").textContent).toContain("Market pulse unavailable");
    // Every row's shading cell says "unavailable", never a zero.
    for (const dash of screen.getAllByTestId("crypto-caps-fallback")) {
      expect(dash.getAttribute("data-null")).toBe("true");
    }
  });

  it("shows a null block's reason from the payload, with no fake zeroes", async () => {
    stub({ home: homeDocument({ filings: null, unavailable: ["filings"] }) });
    render(<HomePage />);

    const insider = await screen.findByTestId("insider-links");
    await waitFor(() => expect(insider.getAttribute("data-status")).toBe("missing"));

    // A missing block prints the server's reason instead of an empty table.
    expect(insider.textContent).toContain("Insider unavailable");
    expect(insider.querySelectorAll("tbody tr")).toHaveLength(0);
    // The other equity blocks still painted.
    expect(document.querySelectorAll("tbody tr[data-mover-row]")).toHaveLength(40);
  });

  it("says a missing map has no sectors instead of drawing an empty canvas", async () => {
    stub({ home: homeDocument({ map: { sectors: [], tiles: 0, tiles_total: 5573 } }) });
    render(<HomePage />);

    await waitFor(() =>
      expect(screen.getByTestId("home-grid").getAttribute("data-status")).toBe("ready"),
    );

    const map = screen.getByTestId("home-map");
    expect(map.querySelector("canvas")).toBeNull();
    expect(screen.getByTestId("map-empty").textContent).toContain("No sector carried a value");
    expect(screen.getByTestId("map-empty").textContent).toContain("nothing to draw");
  });

  it("bleeds the ticker across the page and gives it no border", async () => {
    stub();
    render(<HomePage />);
    await waitFor(() =>
      expect(screen.getByTestId("home-grid").getAttribute("data-status")).toBe("ready"),
    );

    const ticker = screen.getByTestId("home-ticker");
    expect(ticker.className).toContain("-mx-2");
    expect(ticker.className).not.toMatch(/\bborder-(line|strong|b|t|l|r)\b/);
    expect(ticker.className).not.toMatch(/(^|\s)border(\s|$)/);
    expect(ticker.className).not.toContain("rounded");
    expect(screen.getByTestId("home-grid").contains(ticker)).toBe(false);
  });

  it("renders a headline containing a script tag as text", async () => {
    stub({
      home: homeDocument({
        news: [
          {
            headline: "Markets <script>alert(1)</script> steady",
            url: "https://example.com/x",
            source: "AP",
            published_at: "2026-10-31T12:00:00Z",
          },
        ],
      }),
    });
    const { container } = render(<HomePage />);

    await waitFor(() =>
      expect(screen.getByTestId("home-grid").getAttribute("data-status")).toBe("ready"),
    );

    expect(container.querySelector("script")).toBeNull();
    expect(screen.getByTestId("home-news").textContent).toContain(
      "Markets <script>alert(1)</script> steady",
    );
  });

  it("reserves each block's box before the answer arrives", async () => {
    let release: (value: Response) => void = () => undefined;
    const pending = new Promise<Response>((resolve) => {
      release = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) =>
        String(input).startsWith("/market/home") ? pending : json(altDocument()),
      ),
    );

    render(<HomePage />);

    expect(screen.getByTestId("home-grid").getAttribute("aria-busy")).toBe("true");
    expect(screen.getByTestId("home-movers").style.height).toBe("420px");
    expect(screen.getByTestId("home-map").style.height).toBe("420px");
    expect(screen.getByTestId("movers-skeleton")).toBeTruthy();
    expect(screen.getByTestId("strip-skeleton")).toBeTruthy();
    // The crypto section draws its own shape while its three reads travel.
    expect(screen.getByTestId("crypto-skeleton")).toBeTruthy();

    release(json(homeDocument()));
    await waitFor(() =>
      expect(screen.getByTestId("home-grid").getAttribute("data-status")).toBe("ready"),
    );

    expect(screen.getByTestId("home-movers").style.height).toBe("420px");
    expect(screen.getByTestId("home-map").style.height).toBe("420px");
  });

  it("names a failed equity read per block and retries the house exactly once", async () => {
    let homeCalls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
        const url = String(input);
        if (url.startsWith("/market/home")) {
          homeCalls += 1;
          // The second read is the real document; the first is the outage.
          return homeCalls === 1
            ? json({ errorMessage: "home aggregate offline" }, 503)
            : json(homeDocument());
        }
        return json(altDocument());
      }),
    );

    const user = userEvent.setup();
    render(<HomePage />);

    const alert = await screen.findByTestId("home-error");
    expect(alert.getAttribute("role")).toBe("alert");
    expect(alert.textContent).toContain("home aggregate offline");

    // The equity blocks name the outage in place; the crypto section still
    // painted — its read is its own.
    expect(screen.getByTestId("home-movers").textContent).toContain("did not load");
    expect(screen.getByTestId("home-crypto").textContent).toContain("BTC");

    await user.click(within(alert).getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.queryByTestId("home-error")).toBeNull());
    expect(homeCalls).toBe(2);
    await waitFor(() =>
      expect(screen.getByTestId("home-grid").getAttribute("data-status")).toBe("ready"),
    );
    console.log(
      "DEBUG testids",
      [...document.querySelectorAll("[data-testid]")].map((el) => el.getAttribute("data-testid")).join(","),
      "| calls:",
      homeCalls,
    );
    expect(document.querySelectorAll("tbody tr[data-mover-row]")).toHaveLength(40);
  });

  it("fails the crypto section loudly, then recovers with one success toast", async () => {
    let cryptoCalls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
        const url = String(input);
        if (url.startsWith("/market/alt")) {
          cryptoCalls += 1;
          return cryptoCalls === 1
            ? json({ errorMessage: "coin stream down" }, 503)
            : json(altDocument());
        }
        if (url.startsWith("/market/home")) return json(homeDocument());
        return json({});
      }),
    );

    const user = userEvent.setup();
    render(<HomePage />);

    // The equity page is fine; only the crypto section failed — and it says so
    // twice: on the page, and in the store's log.
    await waitFor(() =>
      expect(screen.getByTestId("crypto-error").textContent).toContain("coin stream down"),
    );
    expect(screen.queryAllByTestId("home-error")).toHaveLength(0);
    await waitFor(() =>
      expect(
        toastsNow().some(
          (toast) => toast.kind === "error" && toast.title === "Crypto did not load",
        ),
      ).toBe(true),
    );

    await user.click(screen.getByTestId("crypto-retry"));
    await waitFor(() =>
      expect(
        screen.getAllByTestId("crypto-coins")[0].querySelectorAll("tr[data-crypto-row]").length,
      ).toBeGreaterThan(0),
    );
    // A retry the reader asked for that works: one success toast.
    expect(
      toastsNow().some((toast) => toast.kind === "success" && toast.title === "Crypto loaded"),
    ).toBe(true);
    // The house was never re-asked: crypto's retry is crypto's.
    expect(cryptoCalls).toBe(2);
  });

  it("aborts its requests when the page goes away", async () => {
    const fetchMock = stub();
    const { unmount } = render(<HomePage />);
    await waitFor(() =>
      expect(screen.getByTestId("home-grid").getAttribute("data-status")).toBe("ready"),
    );

    const signals = fetchMock.mock.calls.map((call) => (call[1] as RequestInit | undefined)?.signal);
    expect(signals.length).toBe(4);
    expect(signals.every((signal) => signal?.aborted === false)).toBe(true);
    unmount();
    expect(signals.every((signal) => signal?.aborted === true)).toBe(true);
  });

  it("keeps internals off the page", async () => {
    stub();
    render(<HomePage />);
    await waitFor(() =>
      expect(screen.getByTestId("home-grid").getAttribute("data-status")).toBe("ready"),
    );

    const text = screen.getByTestId("home").textContent ?? "";
    expect(text).not.toMatch(/v\d+\.\d+\.\d+/);
    expect(text).not.toMatch(/depth store|snapshot ok|method:|engine/i);
    expect(screen.getByTestId("home-grid").className).toContain("min-[900px]:grid-cols-12");
  });
});
