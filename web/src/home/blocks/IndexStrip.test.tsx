import { render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { IndexStrip } from "./IndexStrip";
import { LOADING, READY, readHome } from "../home";
import { homeDocument, stubCanvas, stubResizeObserver } from "../__fixtures__/home";

/**
 * The strip's promises: five index cards and three sparks from one document,
 * eight tiles that never wrap, and two halves that fail independently.
 *
 * The index tiles carry `lightweight-charts` bars now, which is a canvas
 * library: it is mocked here (`charts/__fixtures__/lightweight`) so the test
 * can prove what the tile owns — one chart per index, 36 px tall, with the
 * same bars in a visually hidden table — rather than asserting pixels jsdom
 * cannot paint.
 */

vi.mock("lightweight-charts", async () => {
  const { fakeLightweightCharts } = await import("@/charts/__fixtures__/lightweight");
  return fakeLightweightCharts();
});

beforeEach(() => {
  stubCanvas();
  stubResizeObserver(220, 18);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const REASON = "Indices unavailable — the source did not answer this request.";

describe("IndexStrip", () => {
  it("draws five index cards and three spark cards", () => {
    const report = readHome(homeDocument());
    render(
      <IndexStrip
        indicesState={READY}
        indices={report.indices}
        sparksState={READY}
        sparks={report.sparks}
      />,
    );

    for (const symbol of ["^GSPC", "^IXIC", "^DJI", "^RUT", "^VIX"]) {
      expect(screen.getByTestId(`index-card-${symbol}`)).toBeTruthy();
    }
    for (const symbol of ["AAPL", "MSFT", "NVDA"]) {
      expect(screen.getByTestId(`spark-card-${symbol}`)).toBeTruthy();
    }
    expect(document.querySelectorAll('[data-testid^="index-card-"]')).toHaveLength(5);
    expect(document.querySelectorAll('[data-testid^="spark-card-"]')).toHaveLength(3);
  });

  it("prints the index label, last and a signed change with its arrow", () => {
    const report = readHome(homeDocument());
    render(
      <IndexStrip indicesState={READY} indices={report.indices} sparksState={READY} sparks={report.sparks} />,
    );

    const card = screen.getByTestId("index-card-^GSPC");
    expect(card.textContent).toContain("S&P 500");
    expect(card.textContent).toContain("$7,580.07");
    expect(card.textContent).toContain("-1.82%");
    expect(card.textContent).toContain("▼");
    // The screen-reader word is in the DOM, not only in the colour.
    expect(card.textContent).toContain("down");
  });

  it("gives every spark its volume and a labelled canvas", () => {
    const report = readHome(homeDocument());
    render(
      <IndexStrip indicesState={READY} indices={report.indices} sparksState={READY} sparks={report.sparks} />,
    );

    const card = screen.getByTestId("spark-card-AAPL");
    expect(card.textContent).toContain("Apple Inc.");
    expect(card.textContent).toContain("42.42M");
    expect(screen.getByTestId("sparkline-AAPL").getAttribute("role")).toBe("img");
    expect(screen.getByTestId("sparkline-AAPL").getAttribute("aria-label")).toContain(
      "AAPL: last $344.63",
    );
  });

  it("gives every index tile a 36 px bar chart and its data table", async () => {
    const report = readHome(homeDocument());
    render(
      <IndexStrip indicesState={READY} indices={report.indices} sparksState={READY} sparks={report.sparks} />,
    );

    for (const symbol of ["^GSPC", "^IXIC", "^DJI", "^RUT", "^VIX"]) {
      const card = screen.getByTestId(`index-card-${symbol}`);
      const chart = await within(card).findByTestId("chart-bars");

      expect(chart.getAttribute("data-height")).toBe("36");
      expect(chart.getAttribute("data-bars")).toBe("30");
      // The canvas is opaque; the table is the same 30 closes for a reader
      // who is not looking at it.
      const table = within(card).getByTestId("chart-bars-table");
      expect(table.tagName).toBe("TABLE");
      expect(table.closest(".sr-only")).not.toBeNull();
      expect(table.querySelectorAll("tbody tr")).toHaveLength(30);
      expect(within(card).getByRole("img").getAttribute("aria-label")).toContain("30 closes");
    }

    // Five canvases, not eight: the spark cards keep the lightweight painter.
    expect(document.querySelectorAll('[data-testid="chart-bars"]')).toHaveLength(5);
    expect(document.querySelectorAll('[data-testid^="sparkline-"]')).toHaveLength(3);
  });

  it("caps the strip at the five index tiles, not the eight tiles", async () => {
    const report = readHome(homeDocument());
    render(
      <IndexStrip indicesState={READY} indices={report.indices} sparksState={READY} sparks={report.sparks} />,
    );

    // Wait for the lazy chunk, then count: a chart per index and no more.
    await within(screen.getByTestId("index-card-^GSPC")).findByTestId("chart-bars");
    expect(document.querySelectorAll('[data-testid="chart-bars"]')).toHaveLength(5);
    expect(document.querySelectorAll('[data-testid^="spark-card-"]')).toHaveLength(3);
  });

  it("keeps the sparks when the indices half failed", () => {
    const report = readHome(homeDocument({ indices: null, unavailable: ["indices"] }));
    render(
      <IndexStrip
        indicesState={{ kind: "missing", reason: REASON }}
        indices={report.indices}
        sparksState={READY}
        sparks={report.sparks}
      />,
    );

    expect(screen.getByTestId("indices-reason").textContent).toBe(REASON);
    expect(screen.queryByTestId("index-card-^GSPC")).toBeNull();
    expect(document.querySelectorAll('[data-testid^="spark-card-"]')).toHaveLength(3);
  });

  it("keeps the indices when the sparks half failed", () => {
    const report = readHome(homeDocument({ sparks: null, unavailable: ["sparks"] }));
    render(
      <IndexStrip
        indicesState={READY}
        indices={report.indices}
        sparksState={{ kind: "missing", reason: "Tickers unavailable." }}
        sparks={report.sparks}
      />,
    );

    expect(screen.getByTestId("sparks-reason").textContent).toBe("Tickers unavailable.");
    expect(document.querySelectorAll('[data-testid^="index-card-"]')).toHaveLength(5);
  });

  it("says an empty index list is empty, not broken", () => {
    render(<IndexStrip indicesState={READY} indices={[]} sparksState={READY} sparks={[]} />);

    expect(screen.getByTestId("home-strip").textContent).toContain("No index quotes in this response.");
  });

  it("reserves eight tiles before the answer arrives", () => {
    render(<IndexStrip indicesState={LOADING} indices={null} sparksState={LOADING} sparks={null} />);

    const skeleton = screen.getByTestId("strip-skeleton");
    expect(skeleton.children).toHaveLength(8);
    expect(screen.getByTestId("home-strip").style.minHeight).toBe("84px");
    expect(screen.getByTestId("home-strip").querySelector("h2")?.textContent).toBe("Market strip");
  });
});
