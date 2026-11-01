/**
 * The Insider page: a way in, and the filings behind one company.
 *
 * The tests hold the two claims this page makes and nothing else: it reads the
 * company list once, and it reads one company's filings once, from the SEC
 * endpoint — never a ranked total it did not receive.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { InsiderPage } from "./InsiderPage";
import { INSIDER_DETAIL_NOTE } from "./InsiderDetail";

const SIGNALS = "/market/signals?region=us&per=20";

function signalsPayload(symbols: readonly string[]) {
  return {
    as_of: "2026-10-31T21:00:00Z",
    region: "us",
    universe_total: 5576,
    per: 20,
    groups: [
      { id: "gainers", label: "Gainers", available: true, rows: [] },
      {
        id: "most_active",
        label: "Most Active",
        available: true,
        rows: symbols.map((symbol, index) => ({
          symbol,
          name: `${symbol} Holdings ${index + 1}`,
          price: 10 + index,
          change_pct: index % 2 === 0 ? 1.5 : -1.5,
          volume: 1_000_000,
        })),
      },
    ],
    method: "test",
  };
}

const FILINGS = {
  symbol: "AAPL",
  as_of: "2026-10-29",
  count: 2,
  items: [
    {
      accession: "0001140361-26-041201",
      filed: "2026-10-29",
      period_of_report: "2026-10-27",
      owner_name: "Newstead Jennifer",
      owner_cik: "0001780525",
      role: "Officer: SVP, General Counsel",
      security: "Common Stock",
      transaction_date: "2026-10-27",
      code: "S",
      shares: 2399,
      price: 262.14,
      shares_after: 32396,
    },
    {
      accession: "0001140361-26-041199",
      filed: "2026-10-28",
      owner_name: "Parekh Kevan",
      role: null,
      shares: 1000,
    },
  ],
  provider_errors: [],
};

function stub(options: { symbols?: readonly string[]; filings?: unknown } = {}) {
  const calls: string[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    calls.push(url);
    if (url.startsWith(SIGNALS)) {
      return new Response(JSON.stringify(signalsPayload(options.symbols ?? ["AAPL", "MSFT", "AAPL"])), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
    if (url.includes("/engine/insider/")) {
      return new Response(JSON.stringify(options.filings ?? FILINGS), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
    return new Response("{}", { status: 404, headers: { "content-type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("InsiderPage", () => {
  it("names the surface and says the rows are filings", async () => {
    stub();
    render(<InsiderPage />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Insider filings");
    expect(screen.getByText(/Form 4 filing/i)).toBeTruthy();
    await screen.findByTestId("insider-entry-AAPL");
  });

  it("reads the company list once, and dedupes it", async () => {
    const { calls } = stub();
    render(<InsiderPage />);
    await screen.findByTestId("insider-entry-AAPL");
    expect(calls.filter((url) => url.startsWith(SIGNALS))).toHaveLength(1);
    expect(screen.getAllByTestId(/^insider-entry-/)).toHaveLength(2);
    expect(screen.queryByTestId("insider-entry-MISSING")).toBeNull();
  });

  it("reads one company's filings on click, and only then", async () => {
    const { calls } = stub();
    const user = userEvent.setup();
    render(<InsiderPage />);
    await screen.findByTestId("insider-entry-AAPL");
    expect(calls.some((url) => url.includes("/engine/insider/"))).toBe(false);

    await user.click(screen.getByTestId("insider-entry-AAPL"));
    const detail = await screen.findByTestId("insider-detail");
    await waitFor(() => expect(within(detail).getAllByTestId("insider-filing")).toHaveLength(2));
    expect(calls.filter((url) => url.includes("/engine/insider/"))).toHaveLength(1);
    expect(within(detail).getByText(/Newstead Jennifer/)).toBeTruthy();
    expect(within(detail).getByText(/Role not reported/)).toBeTruthy();
    expect(within(detail).getByTestId("insider-detail-note").textContent).toBe(INSIDER_DETAIL_NOTE);
    expect(detail.textContent).not.toContain("undefined");
  });

  it("opens a typed symbol without the list", async () => {
    const { calls } = stub({ filings: { ...FILINGS, symbol: "TSLA", items: [] } });
    const user = userEvent.setup();
    render(<InsiderPage />);
    await screen.findByTestId("insider-entry-AAPL");

    await user.type(screen.getByLabelText("Symbol"), "tsla");
    await user.click(screen.getByRole("button", { name: "Open" }));

    await screen.findByTestId("insider-detail");
    expect(calls.some((url) => url.endsWith("/engine/insider/TSLA"))).toBe(true);
    expect(await screen.findByTestId("insider-detail-empty")).toBeTruthy();
  });

  it("closes with Escape and returns focus to the row that opened it", async () => {
    stub();
    const user = userEvent.setup();
    render(<InsiderPage />);
    const row = await screen.findByTestId("insider-entry-AAPL");

    await user.click(row);
    await screen.findByTestId("insider-detail");
    await user.keyboard("{Escape}");

    await waitFor(() => expect(screen.queryByTestId("insider-detail")).toBeNull());
    expect(document.activeElement).toBe(row);
  });

  it("marks the open row with aria-current, one at a time", async () => {
    stub();
    const user = userEvent.setup();
    render(<InsiderPage />);
    const aapl = await screen.findByTestId("insider-entry-AAPL");
    const msft = screen.getByTestId("insider-entry-MSFT");

    await user.click(aapl);
    await screen.findByTestId("insider-detail");
    expect(aapl.getAttribute("aria-current")).toBe("true");
    expect(msft.getAttribute("aria-current")).toBeNull();
  });

  it("says so when the company list fails, and retries", async () => {
    let attempt = 0;
    vi.stubGlobal("fetch", vi.fn(async () => {
      attempt += 1;
      if (attempt === 1) return new Response("nope", { status: 503 });
      return new Response(JSON.stringify(signalsPayload(["AAPL"])), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }));
    const user = userEvent.setup();
    render(<InsiderPage />);

    expect(await screen.findByText(/could not be loaded/i)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByTestId("insider-entry-AAPL")).toBeTruthy();
  });

  it("offers the symbol field when the list is empty", async () => {
    stub({ symbols: [] });
    render(<InsiderPage />);
    expect(await screen.findByText(/Type a symbol instead/i)).toBeTruthy();
    expect(screen.getByLabelText("Symbol")).toBeTruthy();
  });
});
