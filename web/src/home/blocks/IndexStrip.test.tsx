import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { IndexStrip } from "./IndexStrip";
import { LOADING, READY, readHome } from "../home";
import { homeDocument, stubCanvas, stubResizeObserver } from "../__fixtures__/home";

/**
 * The strip's promises: five index cards and three sparks from one document,
 * eight tiles that never wrap, and two halves that fail independently.
 */

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
