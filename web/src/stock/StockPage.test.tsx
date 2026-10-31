/**
 * `/stock/{symbol}`: the route's only job is to mount the real panel, full
 * page, and give the reader a way back.
 *
 * The chunk is mocked here (a detail panel has four tabs and its own reads);
 * what this file owns is the wiring — the symbol arrives from the URL, the panel
 * is told it is a page rather than a dock, and closing it leaves the route.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { StockPage } from "./StockPage";

vi.mock("@/detail", () => ({
  default: ({
    symbol,
    onClose,
    variant,
  }: {
    symbol: string | null;
    onClose: () => void;
    variant?: string;
  }) => (
    <div data-testid="detail-panel" data-symbol={symbol} data-variant={variant} role="complementary" aria-label={`Details for ${symbol}`}>
      <button type="button" onClick={onClose}>
        Close details
      </button>
    </div>
  ),
}));

describe("StockPage", () => {
  it("renders the detail panel for the URL's symbol, in page mode", async () => {
    render(<StockPage symbol="AAPL" onClose={vi.fn()} />);

    const panel = await screen.findByTestId("detail-panel", undefined, { timeout: 5_000 });
    expect(panel.getAttribute("data-symbol")).toBe("AAPL");
    expect(panel.getAttribute("data-variant")).toBe("page");
    expect(screen.getByTestId("stock-page").getAttribute("data-symbol")).toBe("AAPL");
  });

  it("offers a way back to the signals and follows the panel's close", async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<StockPage symbol="MSFT" onClose={onClose} />);

    const back = screen.getByRole("link", { name: "← Signals" });
    expect(back.getAttribute("href")).toBe("/app/");

    await screen.findByTestId("detail-panel", undefined, { timeout: 5_000 });
    await user.click(screen.getByRole("button", { name: "Close details" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });
});
