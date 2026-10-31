import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CARD_HEIGHT } from "./SignalCard";
import { HomePage } from "./HomePage";
import { AVAILABLE_GROUPS, SIGNAL_GROUPS, UNAVAILABLE_REASON } from "./signals";

/**
 * Home's promises, on the rendered page:
 *
 * * one request for thirteen cards, never one per card;
 * * the eleven sourced groups each draw up to twenty rows with exactly five
 *   columns;
 * * every ticker is a link to its own page (a real href);
 * * the two unsourced groups say why and draw no rows;
 * * the skeleton and the table occupy the same box, so the answer cannot move
 *   the page.
 */

function groupRows(key: string, count = 20) {
  return Array.from({ length: count }, (_, index) => ({
    symbol: `${key.slice(0, 2).toUpperCase()}${index}`,
    name: `${key} company ${index}`,
    price: 10 + index,
    change_pct: index % 2 === 0 ? 1.5 : -1.5,
    volume: 1_000_000 * (index + 1),
  }));
}

function signalsDocument(count = 20) {
  return {
    as_of: "2026-10-31T20:00:00Z",
    groups: Object.fromEntries(
      AVAILABLE_GROUPS.map((group) => [group.key, { rows: groupRows(group.key, count) }]),
    ),
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function stub(body: unknown = signalsDocument()) {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL) => json(body));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("HomePage", () => {
  it("asks for signals once, and draws thirteen cards", async () => {
    const fetchMock = stub();
    render(<HomePage />);

    await waitFor(() =>
      expect(screen.getByTestId("signal-card-gainers").getAttribute("aria-busy")).toBeNull(),
    );

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe("/market/signals?region=us&per=20");

    const cards = document.querySelectorAll('[data-testid^="signal-card-"]');
    expect(cards).toHaveLength(SIGNAL_GROUPS.length);
    for (const group of SIGNAL_GROUPS) {
      expect(screen.getByTestId(`signal-card-${group.key}`)).toBeTruthy();
    }
  });

  it("draws the eleven sourced groups with the five columns and twenty rows", async () => {
    stub();
    render(<HomePage />);
    await waitFor(() => expect(screen.getByTestId("signal-card-gainers")).toBeTruthy());
    await waitFor(() =>
      expect(screen.getByTestId("signal-card-gainers").getAttribute("aria-busy")).toBeNull(),
    );

    for (const group of AVAILABLE_GROUPS) {
      const card = screen.getByTestId(`signal-card-${group.key}`);
      const headers = [...card.querySelectorAll("thead th")].map((th) => th.textContent);
      expect(headers, group.key).toEqual(["Ticker", "Last", "Change %", "Volume", "Signal"]);
      expect(card.querySelectorAll("tbody tr[data-signal-row]")).toHaveLength(20);
    }
  });

  it("links each ticker to its stock page and draws the 16 px mark", async () => {
    stub();
    render(<HomePage />);

    const card = screen.getByTestId("signal-card-gainers");
    await waitFor(() => expect(card.querySelectorAll("a[href]").length).toBe(20));

    const first = card.querySelector<HTMLAnchorElement>('tbody tr[data-signal-row="GA0"] a');
    expect(first).not.toBeNull();
    expect(first?.getAttribute("href")).toBe("/app/stock/GA0");
    expect(first?.textContent).toContain("GA0");

    const logo = card.querySelector<HTMLImageElement>('img[data-testid="logo-img-GA0"]');
    expect(logo).not.toBeNull();
    expect(logo?.getAttribute("src")).toBe("/logos/GA0");
    expect(logo?.getAttribute("width")).toBe("16");
    expect(logo?.getAttribute("height")).toBe("16");
    // The monogram is behind it, so a 404 still occupies the same box.
    expect(screen.getByTestId("monogram-GA0")).toBeTruthy();
  });

  it("says why Upgrades and Downgrades are empty, and draws no rows", () => {
    stub();
    render(<HomePage />);

    for (const key of ["upgrades", "downgrades"]) {
      const card = screen.getByTestId(`signal-card-${key}`);
      expect(card.getAttribute("data-available")).toBe("false");
      expect(within(card).getByTestId(`signal-reason-${key}`).textContent).toBe(
        UNAVAILABLE_REASON,
      );
      expect(card.querySelectorAll("tbody tr[data-signal-row]")).toHaveLength(0);
      expect(card.querySelector("table")).toBeNull();
    }
  });

  it("reserves every card's height before the answer arrives", async () => {
    let release: (value: Response) => void = () => undefined;
    const pending = new Promise<Response>((resolve) => {
      release = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => pending),
    );

    render(<HomePage />);
    const card = screen.getByTestId("signal-card-gainers");
    expect(card.style.minHeight).toBe(`${CARD_HEIGHT}px`);
    expect(card.getAttribute("aria-busy")).toBe("true");
    const skeleton = card.querySelector('[data-testid="signal-skeleton"]');
    expect(skeleton?.querySelectorAll("tr")).toHaveLength(20);

    release(json(signalsDocument()));
    await waitFor(() => expect(card.getAttribute("aria-busy")).toBeNull());
    // Same box, before and after: the page does not move when data lands.
    expect(card.style.minHeight).toBe(`${CARD_HEIGHT}px`);
  });

  it("shows the empty state for a group with no names, with the server's note on the row", async () => {
    const body = {
      groups: {
        gainers: { rows: [] },
        insider_buying: {
          rows: [],
          note: "no rows matched: insider_trans > 0; 0 of 5576 rows carry insider_trans",
        },
      },
    };
    stub(body);
    render(<HomePage />);

    const card = screen.getByTestId("signal-card-gainers");
    await waitFor(() => expect(card.textContent).toContain("No names in this signal right now."));
    expect(card.querySelectorAll("tbody tr[data-signal-row]")).toHaveLength(0);

    const insider = screen.getByTestId("signal-card-insider_buying");
    await waitFor(() => expect(insider.textContent).toContain("No names in this signal right now."));
    expect(insider.querySelector("tbody td")?.getAttribute("title")).toContain("insider_trans");
  });

  it("names a failed read and retries it", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json({ errorMessage: "scanner offline" }, 503))
      .mockResolvedValueOnce(json(signalsDocument()));
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    render(<HomePage />);

    const alert = await screen.findByTestId("signals-error");
    expect(alert.getAttribute("role")).toBe("alert");
    expect(alert.textContent).toContain("Market signals did not load");

    await user.click(within(alert).getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.queryByTestId("signals-error")).toBeNull());
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await waitFor(() =>
      expect(
        screen.getByTestId("signal-card-gainers").querySelectorAll("tbody tr[data-signal-row]"),
      ).toHaveLength(20),
    );
  });

  it("keeps internals off the page", async () => {
    stub();
    render(<HomePage />);
    await waitFor(() =>
      expect(screen.getByTestId("signal-card-gainers").getAttribute("aria-busy")).toBeNull(),
    );

    const text = screen.getByTestId("home").textContent ?? "";
    expect(text).not.toMatch(/v\d+\.\d+/);
    expect(text).not.toMatch(/depth store|snapshot ok|as of|method:|engine/i);
    // Two columns are a CSS grid, not two wrapper tables.
    expect(screen.getByTestId("signal-grid").className).toContain("xl:grid-cols-2");
  });
});
