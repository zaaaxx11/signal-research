/**
 * `/stock/{symbol}`: the route's only job is to mount the ticker page for the
 * URL's symbol, full height, with one boundary that can retry a failed chunk.
 *
 * The page itself is mocked here (it owns seven reads and a chart); what this
 * file owns is the wiring — the symbol arrives from the route, the page gets
 * it, and the shell's test id is on the document.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { StockPage } from "./StockPage";

vi.mock("./TickerPage", () => ({
  default: ({ symbol }: { symbol: string }) => (
    <div data-testid="ticker-page" data-symbol={symbol}>
      Ticker page for {symbol}
    </div>
  ),
}));

describe("StockPage", () => {
  it("mounts the ticker page for the URL's symbol", async () => {
    render(<StockPage symbol="AAPL" onClose={vi.fn()} />);

    const page = await screen.findByTestId("ticker-page", undefined, { timeout: 5_000 });
    expect(page.getAttribute("data-symbol")).toBe("AAPL");
    expect(screen.getByTestId("stock-page").getAttribute("data-symbol")).toBe("AAPL");
  });

  it("gives the page a full-height flex column, not a docked card", async () => {
    render(<StockPage symbol="MSFT" />);
    await screen.findByTestId("ticker-page", undefined, { timeout: 5_000 });
    expect(screen.getByTestId("stock-page").className).toContain("flex-1");
  });
});
