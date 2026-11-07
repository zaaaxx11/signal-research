/**
 * The site footer's contract: a footer landmark on every route, three link
 * columns of real paths, one legal line, and no internal chrome.
 *
 * It is rendered from the app shell (`app.tsx`), so this test proves the
 * component in isolation: the links are real, the disclaimer is present, and a
 * version / health / "as of" read is not.
 */

import { render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { SiteFooter } from "./SiteFooter";
import { APP_BASE, ROUTE_PATHS } from "./routes";

beforeEach(() => {
  window.history.replaceState(null, "", "/app/");
});

afterEach(() => {
  window.history.replaceState(null, "", "/");
});

describe("SiteFooter", () => {
  it("is a footer landmark, not a div", () => {
    render(<SiteFooter />);
    const footer = screen.getByTestId("site-footer");
    expect(footer.tagName).toBe("FOOTER");
    expect(footer.className).not.toMatch(/overflow-x-auto/);
  });

  it("links to every market route, each a real path the server answers", () => {
    render(<SiteFooter />);
    const footer = screen.getByTestId("site-footer");

    // Every internal link resolves to a mount-prefixed href, never a dead "#".
    const links = within(footer).getAllByRole("link");
    for (const link of links) {
      const href = link.getAttribute("href") ?? "";
      expect(href.startsWith(APP_BASE)).toBe(true);
      expect(href).not.toBe("#");
    }

    // The routes the footer promises are a subset of the shell's real paths.
    const hrefs = links.map((link) => link.getAttribute("href"));
    for (const path of ["/screener", "/map", "/crypto", "/futures", "/watchlist"]) {
      expect(hrefs).toContain(`${APP_BASE}${path}`);
    }
    expect(ROUTE_PATHS).toContain("/compare");
  });

  it("carries the brand and one disclaimer, and names no upstream source", () => {
    render(<SiteFooter />);
    const footer = screen.getByTestId("site-footer");
    const text = footer.textContent ?? "";
    expect(text).toContain("Ztockz");
    expect(text).toMatch(/not investment advice/i);

    // The supply chain is a kitchen secret: no "Powered by", no vendor name,
    // in any case. The footer is the surface a screenshot of the product shows.
    expect(text).not.toMatch(/powered by/i);
    for (const vendor of [
      "yahoo",
      "coingecko",
      "finnhub",
      "edgar",
      "sec ",
      "stooq",
      "finviz",
      "tradingview",
      "nasdaq",
      "bloomberg",
      "reuters",
      "marketbeat",
    ]) {
      expect(text.toLowerCase()).not.toContain(vendor);
    }
  });

  it("prints no version, no health check and no marketing link", () => {
    render(<SiteFooter />);
    const footer = screen.getByTestId("site-footer");
    const text = footer.textContent ?? "";
    expect(text).not.toMatch(/v\d+\.\d+/);
    expect(text).not.toMatch(/as of \d/i);
    expect(text).not.toMatch(/health|engine/i);
    for (const forbidden of ["Login", "Log in", "Sign up free", "Pricing", "Help"]) {
      expect(screen.queryByRole("link", { name: forbidden })).toBeNull();
    }
  });
});
