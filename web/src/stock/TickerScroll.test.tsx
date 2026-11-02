/**
 * The ticker page scrolls with the document, not inside it.
 *
 * A page that nests `overflow-y-auto` inside an `overflow-hidden` flex column
 * gives the reader a scrollbar that ends at the panel's edge: the mouse wheel
 * over the header does nothing, the page cannot be scrolled past the panel, and
 * the document's own scrollbar is missing. The rule this file pins is simple —
 * the document is the only vertical scroller, so no ancestor of the tab panel
 * may clip, and no wrapper may pin a fixed height.
 */

import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { StockPage } from "./StockPage";
import { resetSnapshotCache } from "@/detail/useDetail";
import { stubTickerApi } from "./__fixtures__/ticker";

/** Anything that would make this element a scroller or a clipper. */
const CLIPS = /overflow-(hidden|auto|y-auto|scroll|y-scroll|clip)/;
/** A height that cannot grow with its content. */
const FIXED_HEIGHT = /(^|\s)(h-screen|h-dvh|h-full|max-h-\S+)(\s|$)/;

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ticker page scrolling", () => {
  it("leaves the document as the one scroll container", async () => {
    resetSnapshotCache();
    window.history.replaceState(null, "", "/app/stock/AAPL");
    stubTickerApi();

    render(<StockPage symbol="AAPL" />);

    const panel = await screen.findByRole("tabpanel", undefined, { timeout: 5_000 });
    const root = screen.getByTestId("ticker-page");
    const page = screen.getByTestId("stock-page");

    // The page's own frame: no internal scroller, no fixed height.
    expect(root.getAttribute("class") ?? "").not.toMatch(CLIPS);
    expect(root.getAttribute("class") ?? "").not.toMatch(FIXED_HEIGHT);

    // The tab panel is content, not a viewport.
    expect(panel.getAttribute("class") ?? "").not.toMatch(CLIPS);

    // And nothing between the panel and the route's <main> clips it either:
    // a single `overflow-hidden` wrapper is enough to end the document scroll.
    const trail: Element[] = [];
    for (
      let node: Element | null = panel.parentElement;
      node !== null && node !== document.body;
      node = node.parentElement
    ) {
      trail.push(node);
      expect(node.getAttribute("class") ?? "").not.toMatch(CLIPS);
    }
    expect(trail).toContain(root);
    expect(trail).toContain(page);
  });
});
