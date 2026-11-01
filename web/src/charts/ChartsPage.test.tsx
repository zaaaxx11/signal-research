import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { formatChange, formatPrice } from "@/home/signals";

import { installCanvas } from "./__fixtures__/canvas";
import { chartResponder, wireFor } from "./__fixtures__/chart";
import { CHART_INDICES, ChartsPage } from "./ChartsPage";

/**
 * The gallery end to end: five independent reads, five cards, and a field that
 * replaces the gallery with one instrument.
 *
 * The request assertions are the contract most likely to regress: one request
 * per index on mount, one per typed symbol, and no request for an empty field.
 */

beforeEach(() => {
  installCanvas({ width: 520, height: 132 });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function serveAll(): ReturnType<typeof vi.fn> {
  const mock = vi.fn(chartResponder({}, (symbol, range) => wireFor(symbol, range)));
  vi.stubGlobal("fetch", mock);
  return mock;
}

async function waitForGallery(): Promise<void> {
  await waitFor(() => expect(screen.getAllByTestId("chart-canvas")).toHaveLength(5));
}

describe("ChartsPage — gallery", () => {
  it("lists the five indices the nav promises, each with its chart", async () => {
    serveAll();
    render(<ChartsPage />);

    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Charts");
    expect(screen.getByTestId("charts-gallery")).toBeTruthy();

    for (const index of CHART_INDICES) {
      const card = screen.getByTestId(`chart-card-${index.symbol}`);
      expect(within(card).getByTestId(`chart-card-name-${index.symbol}`).textContent).toBe(index.name);
      expect(within(card).getByTestId("chart")).toBeTruthy();
    }

    await waitForGallery();
    expect(screen.getByLabelText("S&P 500 chart")).toBeTruthy();
    expect(screen.getByLabelText("VIX chart")).toBeTruthy();
  });

  it("asks once per index, on the 1-day range", async () => {
    const mock = serveAll();
    render(<ChartsPage />);
    await waitForGallery();

    expect(mock).toHaveBeenCalledTimes(5);
    const paths = mock.mock.calls.map((call) => String(call[0]));
    expect(new Set(paths)).toEqual(
      new Set([
        "/market/chart/%5EGSPC?range=1d",
        "/market/chart/%5ENDX?range=1d",
        "/market/chart/%5EDJI?range=1d",
        "/market/chart/%5ERUT?range=1d",
        "/market/chart/%5EVIX?range=1d",
      ]),
    );
  });

  it("prints each card's last close and change against the prior close", async () => {
    serveAll();
    render(<ChartsPage />);
    await waitForGallery();

    const wire = wireFor("^GSPC", "1d");
    const bars = wire.bars as Array<{ c: number }>;
    const last = bars[bars.length - 1] as { c: number };
    const previous = wire.previous_close as number;
    const percent = ((last.c - previous) / previous) * 100;

    const card = screen.getByTestId("chart-card-^GSPC");
    await waitFor(() =>
      expect(within(card).getByTestId("chart-card-last-^GSPC").textContent).not.toBe("—"),
    );
    expect(within(card).getByTestId("chart-card-last-^GSPC").textContent).toBe(
      formatPrice(last.c),
    );
    expect(within(card).getByTestId("chart-card-change-^GSPC").textContent).toBe(
      formatChange(percent),
    );
  });

  it("keeps the other cards when one symbol 404s", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        chartResponder(
          { "^VIX|1d": { status: 404, body: { errorMessage: "unknown symbol" } } },
          (symbol, range) => wireFor(symbol, range),
        ),
      ),
    );
    render(<ChartsPage />);

    await waitFor(() => expect(screen.getAllByTestId("chart-canvas")).toHaveLength(4));
    const vix = screen.getByTestId("chart-card-^VIX");
    expect(within(vix).getByTestId("chart-error").textContent).toContain("No chart for ^VIX");
    expect(within(screen.getByTestId("chart-card-^GSPC")).getByTestId("chart-canvas")).toBeTruthy();
  });
});

describe("ChartsPage — one symbol", () => {
  it("swaps the gallery for a single series when the field is submitted", async () => {
    const mock = serveAll();
    render(<ChartsPage />);
    await waitForGallery();

    const input = screen.getByLabelText("Symbol");
    fireEvent.change(input, { target: { value: "aapl" } });
    fireEvent.click(screen.getByRole("button", { name: "Chart" }));

    const single = await screen.findByTestId("charts-single");
    expect(within(single).getByRole("heading", { level: 2 }).textContent).toBe("AAPL");
    expect(screen.queryByTestId("charts-gallery")).toBeNull();
    expect(screen.getByTestId("charts-page").getAttribute("data-view")).toBe("single");

    await waitFor(() => expect(mock).toHaveBeenCalledTimes(6));
    expect(String(mock.mock.calls[5]?.[0])).toBe("/market/chart/AAPL?range=1d");
    expect(await screen.findByTestId("chart-canvas")).toBeTruthy();
  });

  it("does not swap or fetch for an empty field", async () => {
    const mock = serveAll();
    render(<ChartsPage />);
    await waitForGallery();

    fireEvent.click(screen.getByRole("button", { name: "Chart" }));
    expect(screen.getByTestId("charts-gallery")).toBeTruthy();
    expect(mock).toHaveBeenCalledTimes(5);
  });

  it("returns to the gallery from the single view", async () => {
    const mock = serveAll();
    render(<ChartsPage />);
    await waitForGallery();

    fireEvent.change(screen.getByLabelText("Symbol"), { target: { value: "MSFT" } });
    fireEvent.click(screen.getByRole("button", { name: "Chart" }));
    await screen.findByTestId("charts-single");

    fireEvent.click(screen.getByTestId("charts-back"));
    expect(screen.getByTestId("charts-gallery")).toBeTruthy();
    await waitFor(() => expect(mock).toHaveBeenCalledTimes(11));
    await waitForGallery();
  });

  it("opens on a deep-link symbol when one is supplied", async () => {
    serveAll();
    render(<ChartsPage initialSymbol="^ndx" />);
    expect(screen.getByTestId("charts-page").getAttribute("data-view")).toBe("single");
    expect(screen.getByTestId("charts-single").textContent).toContain("^NDX");
    await waitFor(() =>
      expect(screen.getByTestId("chart").getAttribute("data-symbol")).toBe("^NDX"),
    );
  });
});
