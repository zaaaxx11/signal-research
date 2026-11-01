import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { HomePage } from "./HomePage";
import { homeDocument, stubCanvas, stubResizeObserver } from "./__fixtures__/home";

/**
 * The page's whole contract, on the rendered dashboard:
 *
 * * **one** request paints every block — never one per card;
 * * six blocks and a footer, each with a level-2 heading, under one hidden `h1`;
 * * a `null` block prints the reason from `unavailable` and draws no fake zeroes;
 * * a failure names itself per block and one Retry refetches exactly once;
 * * the skeletons occupy the same boxes the content will.
 */

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function stub(body: unknown = homeDocument()) {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => json(body));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  stubCanvas();
  stubResizeObserver(320, 200);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const BLOCKS = [
  "strip",
  "breadth",
  "movers",
  "map",
  "signals",
  "insider",
  "news",
] as const;

describe("HomePage", () => {
  it("paints every block from one request", async () => {
    const fetchMock = stub();
    render(<HomePage />);

    await waitFor(() => expect(screen.getByTestId("home-grid").getAttribute("data-status")).toBe("ready"));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe("/market/home?region=us");

    for (const block of BLOCKS) {
      expect(screen.getByTestId(`home-${block}`), block).toBeTruthy();
    }
    expect(screen.getByTestId("home-footer").tagName).toBe("FOOTER");
    // And nothing else was asked for: no per-block endpoints, no health poll.
    const urls = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(urls.every((url) => url === "/market/home?region=us")).toBe(true);
  });

  it("has a hidden h1 and a level-2 heading on every card", async () => {
    stub();
    render(<HomePage />);
    await waitFor(() => expect(screen.getByTestId("home-grid").getAttribute("data-status")).toBe("ready"));

    const h1s = document.querySelectorAll("h1");
    expect(h1s).toHaveLength(1);
    expect(h1s[0]?.className).toContain("sr-only");
    expect(h1s[0]?.textContent).toBe("Market overview");

    for (const block of BLOCKS) {
      const headings = screen.getByTestId(`home-${block}`).querySelectorAll("h2");
      // Breadth is a strip of figures, not a titled section: it is the one
      // block without a heading, on purpose.
      expect(headings, block).toHaveLength(block === "breadth" ? 0 : 1);
    }
    expect(document.querySelectorAll("h3").length).toBeGreaterThan(5);
  });

  it("draws the movers table and the map's trim, and the footer's word", async () => {
    stub();
    render(<HomePage />);
    await waitFor(() => expect(screen.getByTestId("home-grid").getAttribute("data-status")).toBe("ready"));

    expect(document.querySelectorAll("tbody tr[data-mover-row]")).toHaveLength(40);
    expect(screen.getByTestId("map-tiles").textContent).toBe("600 of 5,573 tiles");
    expect(screen.getByTestId("home-footer").textContent).toBe("ztockz");
  });

  it("shows a null block's reason from the payload, with no fake zeroes", async () => {
    stub(homeDocument({ filings: null, unavailable: ["filings"] }));
    render(<HomePage />);

    const insider = await screen.findByTestId("home-insider");
    await waitFor(() =>
      expect(insider.textContent).toContain(
        "Filings unavailable — the source did not answer this request.",
      ),
    );

    expect(insider.querySelectorAll("[data-filing]")).toHaveLength(0);
    expect(screen.getByTestId("insider-all").getAttribute("href")).toBe("/app/insider");
    // The other blocks still painted.
    expect(document.querySelectorAll("tbody tr[data-mover-row]")).toHaveLength(40);
  });

  it("says a missing map has no sectors instead of drawing an empty canvas", async () => {
    stub(homeDocument({ map: { sectors: [], tiles: 0, tiles_total: 5573 } }));
    render(<HomePage />);

    await waitFor(() => expect(screen.getByTestId("home-grid").getAttribute("data-status")).toBe("ready"));

    const map = screen.getByTestId("home-map");
    expect(map.querySelector("canvas")).toBeNull();
    expect(screen.getByTestId("map-empty").textContent).toContain("No sector carried a value");
    expect(screen.getByTestId("map-tiles").textContent).toBe("0 of 5,573 tiles");
  });

  it("renders a headline containing a script tag as text", async () => {
    stub(
      homeDocument({
        news: [
          {
            headline: "Markets <script>alert(1)</script> steady",
            url: "https://example.com/x",
            source: "AP",
            published_at: "2026-10-31T12:00:00Z",
          },
        ],
      }),
    );
    const { container } = render(<HomePage />);

    await waitFor(() => expect(screen.getByTestId("home-grid").getAttribute("data-status")).toBe("ready"));

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
    vi.stubGlobal("fetch", vi.fn(async () => pending));

    render(<HomePage />);

    expect(screen.getByTestId("home-grid").getAttribute("aria-busy")).toBe("true");
    expect(screen.getByTestId("home-movers").style.height).toBe("420px");
    expect(screen.getByTestId("home-map").style.height).toBe("420px");
    expect(screen.getByTestId("home-breadth").style.minHeight).toBe("76px");
    expect(screen.getByTestId("home-insider").style.minHeight).toBe("92px");
    expect(screen.getByTestId("movers-skeleton")).toBeTruthy();
    expect(screen.getByTestId("strip-skeleton")).toBeTruthy();

    release(json(homeDocument()));
    await waitFor(() => expect(screen.getByTestId("home-grid").getAttribute("data-status")).toBe("ready"));

    // Same boxes after the answer: the page does not move when data lands.
    expect(screen.getByTestId("home-movers").style.height).toBe("420px");
    expect(screen.getByTestId("home-map").style.height).toBe("420px");
    expect(screen.getByTestId("home-breadth").style.minHeight).toBe("76px");
  });

  it("names a failed read per block and retries the whole page once", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json({ errorMessage: "home aggregate offline" }, 503))
      .mockResolvedValueOnce(json(homeDocument()));
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    render(<HomePage />);

    const alert = await screen.findByTestId("home-error");
    expect(alert.getAttribute("role")).toBe("alert");
    expect(alert.textContent).toContain("home aggregate offline");

    // Every block says so in place; the page is not blank.
    for (const block of BLOCKS) {
      expect(within(screen.getByTestId(`home-${block}`)).getAllByText(/did not load/i).length, block)
        .toBeGreaterThan(0);
    }

    await user.click(within(alert).getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.queryByTestId("home-error")).toBeNull());
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(screen.getByTestId("home-grid").getAttribute("data-status")).toBe("ready"));
    expect(document.querySelectorAll("tbody tr[data-mover-row]")).toHaveLength(40);
  });

  it("aborts its request when the page goes away", async () => {
    const fetchMock = stub();
    const { unmount } = render(<HomePage />);
    await waitFor(() => expect(screen.getByTestId("home-grid").getAttribute("data-status")).toBe("ready"));

    const signal = (fetchMock.mock.calls[0]?.[1] as RequestInit | undefined)?.signal;
    expect(signal?.aborted).toBe(false);
    unmount();
    expect(signal?.aborted).toBe(true);
  });

  it("keeps internals off the page", async () => {
    stub();
    render(<HomePage />);
    await waitFor(() => expect(screen.getByTestId("home-grid").getAttribute("data-status")).toBe("ready"));

    const text = screen.getByTestId("home").textContent ?? "";
    expect(text).not.toMatch(/v\d+\.\d+\.\d+/);
    expect(text).not.toMatch(/depth store|snapshot ok|method:|engine/i);
    expect(screen.getByTestId("home-grid").className).toContain("min-[900px]:grid-cols-12");
  });
});
