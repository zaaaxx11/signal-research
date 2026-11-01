import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MajorsBlock, majorIndices } from "./MajorsBlock";
import { LOADING, READY, readHome } from "../home";
import { homeDocument } from "../__fixtures__/home";

/**
 * "Major" is a reading of the day, not a list someone typed: the three largest
 * absolute moves. A tie keeps the wire's order, and an index with no change
 * sorts last rather than winning by being absent.
 */

describe("majorIndices", () => {
  it("takes the three largest absolute moves", () => {
    const report = readHome(homeDocument());
    const majors = majorIndices(report.indices).map((index) => index.symbol);

    expect(majors).toEqual(["^VIX", "^IXIC", "^GSPC"]);
  });

  it("puts an index with no change behind the ones that moved", () => {
    const report = readHome(
      homeDocument({
        indices: [
          { symbol: "FLAT", label: "Unmoved", price: 1, change_pct: null, closes: [] },
          { symbol: "DOWN", label: "Down", price: 1, change_pct: -0.4, closes: [] },
        ],
      }),
    );

    expect(majorIndices(report.indices).map((index) => index.symbol)).toEqual(["DOWN", "FLAT"]);
  });
});

describe("MajorsBlock", () => {
  it("draws three rows with a price and a signed change", () => {
    const report = readHome(homeDocument());
    render(<MajorsBlock state={READY} indices={report.indices} />);

    const rows = document.querySelectorAll("[data-major]");
    expect(rows).toHaveLength(3);
    expect(rows[0]?.getAttribute("data-major")).toBe("^VIX");
    expect(rows[0]?.textContent).toContain("VIX");
    expect(rows[0]?.textContent).toContain("$18.42");
    expect(rows[0]?.textContent).toContain("+6.28%");
    expect(rows[0]?.textContent).toContain("▲");
  });

  it("says the index list was empty rather than drawing nothing", () => {
    render(<MajorsBlock state={READY} indices={[]} />);

    expect(screen.getByTestId("home-majors").textContent).toContain("No index quotes");
  });

  it("prints the reason when the index block is missing", () => {
    render(<MajorsBlock state={{ kind: "missing", reason: "Indices unavailable." }} indices={null} />);

    expect(screen.getByTestId("majors-reason").textContent).toBe("Indices unavailable.");
  });

  it("reserves three rows while loading", () => {
    render(<MajorsBlock state={LOADING} indices={null} />);

    expect(screen.getByTestId("majors-skeleton").children).toHaveLength(3);
  });
});
