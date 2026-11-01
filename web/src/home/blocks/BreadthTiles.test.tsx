import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { BreadthTiles, share } from "./BreadthTiles";
import { LOADING, READY, readHome } from "../home";
import { homeDocument } from "../__fixtures__/home";

/**
 * Four opposed pairs, each carrying its share and its count — the shape a reader
 * of a market terminal already knows. A value the response did not carry is an em
 * dash with a title, never a zero: "no new lows" and "we did not measure new
 * lows" are different sentences.
 */

describe("share", () => {
  it("is the count over its own denominator, one decimal", () => {
    expect(share(1557, 5576)).toBe("27.9%");
    expect(share(0, 100)).toBe("0.0%");
  });
  it("refuses to divide by nothing", () => {
    expect(share(10, null)).toBe("—");
    expect(share(null, 100)).toBe("—");
    expect(share(10, 0)).toBe("—");
  });
});

describe("BreadthTiles", () => {
  it("draws the four Finviz pairs with shares and comma-formatted counts", () => {
    const report = readHome(homeDocument());
    render(<BreadthTiles state={READY} breadth={report.breadth} />);

    const advancing = screen.getByTestId("breadth-tile-advancing");
    expect(advancing.textContent).toContain("Advancing");
    expect(advancing.textContent).toContain("27.9%"); // 1,557 / 5,576
    expect(advancing.textContent).toContain("1,557");

    expect(screen.getByTestId("breadth-tile-declining").textContent).toContain("70.5%");
    expect(screen.getByTestId("breadth-tile-newHighs").textContent).toContain("0.8%");
    expect(screen.getByTestId("breadth-tile-newLows").textContent).toContain("1.0%");

    // The SMA pairs divide by the rows that carry a measurement.
    const above = screen.getByTestId("breadth-tile-above50");
    expect(above.textContent).toContain("Above SMA50");
    expect(above.textContent).toContain("37.7%"); // 2,104 / 5,576
    expect(screen.getByTestId("breadth-tile-below50").textContent).toContain("62.3%");
    expect(screen.getByTestId("breadth-tile-above200").textContent).toContain("Above SMA200");
    expect(screen.getByTestId("breadth-tile-above200").textContent).toContain("39.3%");
    expect(screen.getByTestId("breadth-tile-below200").textContent).toContain("Below SMA200");

    expect(document.querySelectorAll('[data-testid^="breadth-tile-"]')).toHaveLength(8);
  });

  it("prints the universe once, as the denominator note", () => {
    const report = readHome(homeDocument());
    render(<BreadthTiles state={READY} breadth={report.breadth} />);

    const text = screen.getByTestId("home-breadth").textContent ?? "";
    expect(text).toContain("5,576");
    expect(text).toContain("companies in the universe");
    expect(text).toContain("measured");
  });

  it("prints an em dash with a title when a value is missing", () => {
    const report = readHome(
      homeDocument({ breadth: { ...(homeDocument().breadth as object), new_lows: null } }),
    );
    render(<BreadthTiles state={READY} breadth={report.breadth} />);

    const tile = screen.getByTestId("breadth-tile-newLows");
    expect(tile.textContent).toContain("—");
    expect(tile.querySelector("[title]")?.getAttribute("title")).toContain("new low unavailable");
  });

  it("says why the block is missing instead of drawing eight zeroes", () => {
    render(
      <BreadthTiles
        state={{ kind: "missing", reason: "Breadth unavailable — the source did not answer this request." }}
        breadth={null}
      />,
    );

    expect(screen.getByTestId("breadth-reason").textContent).toContain("did not answer");
    expect(document.querySelectorAll('[data-testid^="breadth-tile-"]')).toHaveLength(0);
  });

  it("reserves the same box while loading", () => {
    render(<BreadthTiles state={LOADING} breadth={null} />);

    expect(screen.getByTestId("breadth-skeleton").children).toHaveLength(8);
    expect(screen.getByTestId("home-breadth").style.minHeight).toBe("76px");
  });
});
