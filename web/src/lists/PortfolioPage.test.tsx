/**
 * The Portfolio page's promises, on the rendered page.
 *
 * The fixture is the one in `pnl.test.ts`, drawn: AAPL 10 @ 100.00 against
 * 110.00, MSFT 2.5 @ 400.10 against 390.04 — a gain and a loss — so the total
 * (+74.85 on 2000.25, +3.74%) can be checked with a calculator rather than with
 * the code under test.
 *
 * The fourth holding is the adversarial one: TSLA with no quote. It may not
 * contribute a zero to the total, its cost basis may not be counted on its own,
 * and the total has to say it covers two rows of three.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LOCAL_ONLY_SENTENCE } from "./LocalNotice";
import { PortfolioPage } from "./PortfolioPage";
import {
  PORTFOLIO_KEY,
  createLocalStore,
  decodePortfolio,
  portfolioStore,
  watchlistStore,
} from "./storage";

function quoteRow(symbol: string, price: number, changePct = 0): Record<string, unknown> {
  return { symbol, name: null, price, change_pct: changePct, currency: "USD", class: "stock" };
}

function doc(rows: Record<string, unknown>[], missing: string[] = []) {
  return { as_of: "2026-10-31T23:40:00+00:00", rows, missing };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function stub(body: unknown = doc([])) {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL) => json(body));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function asked(call: unknown): string[] {
  const query = String(call).split("?")[1] ?? "";
  return (new URLSearchParams(query).get("symbols") ?? "").split(",").filter(Boolean);
}

const FIXTURE = [
  { symbol: "AAPL", quantity: 10, cost: 100 },
  { symbol: "MSFT", quantity: 2.5, cost: 400.1 },
];

const PRICES = doc([quoteRow("AAPL", 110), quoteRow("MSFT", 390.04)]);

beforeEach(() => {
  window.localStorage.clear();
  watchlistStore.write([]);
  portfolioStore.write([]);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("PortfolioPage", () => {
  it("draws an empty state that teaches the three fields, and asks for nothing", () => {
    const fetchMock = stub();
    render(<PortfolioPage />);

    const empty = screen.getByTestId("portfolio-empty");
    expect(empty.textContent).toMatch(/no holdings/i);
    expect(empty.textContent).toMatch(/how many shares/i);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.queryByTestId("portfolio-table")).toBeNull();
  });

  it("says the holdings are this browser's, in words", () => {
    stub();
    render(<PortfolioPage />);
    expect(screen.getByText(LOCAL_ONLY_SENTENCE)).toBeTruthy();
  });

  it("draws last, market value, cost basis and P&L to the cent, and totals them", async () => {
    portfolioStore.write(FIXTURE);
    const fetchMock = stub(PRICES);

    render(<PortfolioPage />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const aapl = await screen.findByTestId("portfolio-row-AAPL");

    await waitFor(() => expect(aapl.textContent).toContain("$110.00"));
    expect(screen.getByTestId("portfolio-value-AAPL").textContent).toBe("$1,100.00");
    expect(screen.getByTestId("portfolio-basis-AAPL").textContent).toBe("$1,000.00");
    expect(screen.getByTestId("portfolio-pnl-AAPL").textContent).toBe("+$100.00");
    expect(screen.getByTestId("portfolio-pct-AAPL").textContent).toBe("+10.00%");

    const msft = screen.getByTestId("portfolio-row-MSFT");
    expect(screen.getByTestId("portfolio-value-MSFT").textContent).toBe("$975.10");
    expect(screen.getByTestId("portfolio-basis-MSFT").textContent).toBe("$1,000.25");
    expect(screen.getByTestId("portfolio-pnl-MSFT").textContent).toBe("-$25.15");
    expect(screen.getByTestId("portfolio-pct-MSFT").textContent).toBe("-2.51%");
    expect(msft.getAttribute("data-missing")).toBe("false");

    const total = screen.getByTestId("portfolio-total");
    expect(total.getAttribute("data-covered")).toBe("2");
    expect(total.getAttribute("data-total")).toBe("2");
    expect(total.textContent).toMatch(/2 of 2/);
    expect(screen.getByTestId("total-market-value").textContent).toBe("$2,075.10");
    expect(screen.getByTestId("total-cost-basis").textContent).toBe("$2,000.25");
    expect(screen.getByTestId("total-pnl").textContent).toBe("+$74.85");
    expect(screen.getByTestId("total-pnl-pct").textContent).toBe("+3.74%");
  });

  it("excludes an unpriced holding from both sides of the total, and counts it", async () => {
    portfolioStore.write([...FIXTURE, { symbol: "TSLA", quantity: 1, cost: 200 }]);
    stub(doc([quoteRow("AAPL", 110), quoteRow("MSFT", 390.04)], ["TSLA"]));

    render(<PortfolioPage />);

    const tsla = await screen.findByTestId("portfolio-row-TSLA");
    await waitFor(() => expect(tsla.textContent).toContain("Unavailable"));
    expect(tsla.getAttribute("data-missing")).toBe("true");
    expect(screen.getByTestId("portfolio-value-TSLA").textContent).toBe("—");
    expect(screen.getByTestId("portfolio-pnl-TSLA").textContent).toBe("—");

    const total = screen.getByTestId("portfolio-total");
    expect(total.getAttribute("data-covered")).toBe("2");
    expect(total.getAttribute("data-total")).toBe("3");
    expect(total.textContent).toMatch(/2 of 3/);
    // 200.00 of TSLA cost basis is in neither sum: 2075.10 - 2000.25 = 74.85.
    expect(screen.getByTestId("total-market-value").textContent).toBe("$2,075.10");
    expect(screen.getByTestId("total-cost-basis").textContent).toBe("$2,000.25");
    expect(screen.getByTestId("total-pnl").textContent).toBe("+$74.85");
    expect(screen.getByTestId("portfolio-partial").textContent).toMatch(/1 holding/);
  });

  it("draws no total at all when no holding has a quote", async () => {
    portfolioStore.write([{ symbol: "TSLA", quantity: 1, cost: 200 }]);
    stub(doc([], ["TSLA"]));

    render(<PortfolioPage />);

    const total = await screen.findByTestId("portfolio-total");
    await waitFor(() => expect(total.getAttribute("data-covered")).toBe("0"));
    expect(total.textContent).toMatch(/no holding has a quote/i);
    expect(screen.getByTestId("total-market-value").textContent).toBe("—");
    expect(screen.getByTestId("total-pnl").textContent).toBe("—");
  });

  it("adds a holding from the form and asks once for the new set", async () => {
    const fetchMock = stub(doc([quoteRow("AAPL", 110)]));
    const user = userEvent.setup();
    render(<PortfolioPage />);

    await user.type(screen.getByLabelText("Symbol"), "aapl");
    await user.type(screen.getByLabelText("Quantity"), "10");
    await user.type(screen.getByLabelText("Average cost"), "100");
    await user.click(screen.getByRole("button", { name: "Add" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(asked(fetchMock.mock.calls[0]?.[0])).toEqual(["AAPL"]);
    expect(await screen.findByTestId("portfolio-row-AAPL")).toBeTruthy();
    expect(JSON.parse(window.localStorage.getItem(PORTFOLIO_KEY) ?? "null")).toEqual([
      { symbol: "AAPL", quantity: 10, cost: 100 },
    ]);
  });

  it("refuses a zero or negative quantity at the input, and stores nothing", async () => {
    const fetchMock = stub();
    const user = userEvent.setup();
    render(<PortfolioPage />);

    for (const quantity of ["0", "-2"]) {
      await user.clear(screen.getByLabelText("Quantity"));
      await user.type(screen.getByLabelText("Symbol"), "AAPL");
      await user.type(screen.getByLabelText("Quantity"), quantity);
      await user.type(screen.getByLabelText("Average cost"), "100");
      await user.click(screen.getByRole("button", { name: "Add" }));

      const alert = await screen.findByTestId("portfolio-error");
      expect(alert.textContent).toMatch(/greater than zero/i);
      expect((screen.getByLabelText("Quantity") as HTMLInputElement).getAttribute("aria-invalid"))
        .toBe("true");
      expect(screen.queryByTestId("portfolio-row-AAPL")).toBeNull();
      expect(JSON.parse(window.localStorage.getItem(PORTFOLIO_KEY) ?? "null")).toEqual([]);
      expect(fetchMock).not.toHaveBeenCalled();
    }
  });

  it("does not colour a P&L of exactly zero", async () => {
    portfolioStore.write([{ symbol: "ASML", quantity: 1, cost: 50 }]);
    stub(doc([quoteRow("ASML", 50)]));

    render(<PortfolioPage />);

    const cell = await screen.findByTestId("portfolio-pnl-ASML");
    await waitFor(() => expect(cell.textContent).toBe("$0.00"));

    // The sign and the tone live on the value's own span, as the grid's cells do.
    const pnl = within(cell).getByText("$0.00");
    expect(pnl.getAttribute("data-sign")).toBe("0");
    expect(pnl.className).toContain("text-flat");
    expect(pnl.className).not.toContain("text-up");
    expect(pnl.className).not.toContain("text-down");

    const pctCell = screen.getByTestId("portfolio-pct-ASML");
    const pct = within(pctCell).getByText("0.00%");
    expect(pct.getAttribute("data-sign")).toBe("0");
    expect(pct.className).not.toContain("text-up");
  });

  it("refetches once when the set changes and drops the removed symbol", async () => {
    portfolioStore.write(FIXTURE);
    const fetchMock = stub(PRICES);
    const user = userEvent.setup();
    render(<PortfolioPage />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(asked(fetchMock.mock.calls[0]?.[0])).toEqual(["AAPL", "MSFT"]);

    await user.click(screen.getByRole("button", { name: "Remove MSFT" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(asked(fetchMock.mock.calls[1]?.[0])).toEqual(["AAPL"]);
    expect(screen.queryByTestId("portfolio-row-MSFT")).toBeNull();
  });

  it("survives a reload with the same holdings", async () => {
    stub(doc([quoteRow("AAPL", 110)]));
    const user = userEvent.setup();
    const first = render(<PortfolioPage />);

    await user.type(screen.getByLabelText("Symbol"), "AAPL");
    await user.type(screen.getByLabelText("Quantity"), "10");
    await user.type(screen.getByLabelText("Average cost"), "100");
    await user.click(screen.getByRole("button", { name: "Add" }));
    await screen.findByTestId("portfolio-row-AAPL");

    expect(createLocalStore(PORTFOLIO_KEY, decodePortfolio).read()).toEqual([
      { symbol: "AAPL", quantity: 10, cost: 100 },
    ]);

    first.unmount();
    render(<PortfolioPage />);
    expect(await screen.findByTestId("portfolio-row-AAPL")).toBeTruthy();
  });

  it("refuses a second row for a symbol it already holds", async () => {
    portfolioStore.write(FIXTURE);
    stub(PRICES);
    const user = userEvent.setup();
    render(<PortfolioPage />);

    await user.type(screen.getByLabelText("Symbol"), "AAPL");
    await user.type(screen.getByLabelText("Quantity"), "1");
    await user.type(screen.getByLabelText("Average cost"), "1");
    await user.click(screen.getByRole("button", { name: "Add" }));

    const alert = await screen.findByTestId("portfolio-error");
    expect(alert.textContent).toMatch(/already a holding/i);
    expect(screen.getAllByTestId("portfolio-row-AAPL")).toHaveLength(1);
  });
});
