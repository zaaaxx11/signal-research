import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { BreadthTiles } from "./BreadthTiles";
import { LOADING, READY, readHome } from "../home";
import { homeDocument } from "../__fixtures__/home";

/**
 * Six tiles, six facts, one denominator. A value the response did not carry is
 * an em dash with a title that names it — never a zero, because "no new lows"
 * and "we did not measure new lows" are different sentences.
 */

describe("BreadthTiles", () => {
  it("draws six tiles with comma-formatted counts", () => {
    const report = readHome(homeDocument());
    render(<BreadthTiles state={READY} breadth={report.breadth} />);

    expect(screen.getByTestId("breadth-tile-advancing").textContent).toContain("1,557");
    expect(screen.getByTestId("breadth-tile-declining").textContent).toContain("3,932");
    expect(screen.getByTestId("breadth-tile-newHighs").textContent).toContain("45");
    expect(screen.getByTestId("breadth-tile-newLows").textContent).toContain("54");
    expect(screen.getByTestId("breadth-tile-upVolume").textContent).toContain("6.91B");
    expect(screen.getByTestId("breadth-tile-downVolume").textContent).toContain("11.20B");
    expect(document.querySelectorAll('[data-testid^="breadth-tile-"]')).toHaveLength(6);
  });

  it("prints the universe once, as the denominator for every tile", () => {
    const report = readHome(homeDocument());
    render(<BreadthTiles state={READY} breadth={report.breadth} />);

    const text = screen.getByTestId("home-breadth").textContent ?? "";
    expect(text).toContain("5,576 names in the universe");
    expect(text).toContain("189 unchanged");
  });

  it("prints an em dash with a title when a tile has no value", () => {
    const report = readHome(homeDocument({ breadth: { ...(homeDocument().breadth as object), new_lows: null } }));
    render(<BreadthTiles state={READY} breadth={report.breadth} />);

    const tile = screen.getByTestId("breadth-tile-newLows");
    expect(tile.textContent).toContain("—");
    expect(tile.querySelector("[title]")?.getAttribute("title")).toContain("new lows unavailable");
  });

  it("says why the block is missing instead of drawing six zeroes", () => {
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

    expect(screen.getByTestId("breadth-skeleton").children).toHaveLength(6);
    expect(screen.getByTestId("home-breadth").style.minHeight).toBe("76px");
  });
});
