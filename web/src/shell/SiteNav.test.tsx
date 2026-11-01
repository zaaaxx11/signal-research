/**
 * The nav's contract is a list: fourteen sections, in order, each a real href.
 *
 * It is also a density contract. The row is 36 px with 12 px/500 labels, links
 * that do not wrap, and a fixed-width search, because a nav that wraps onto a
 * second line at 1280 px is a nav that has moved the content down. jsdom has no
 * layout engine, so the width itself is measured in a browser (the hand-off
 * records the number); what this file can prove is the structure that makes the
 * single row possible: no `flex-wrap`, every label `whitespace-nowrap`, and the
 * one flexible element is the nav itself.
 */

import { createRef } from "react";

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { NAV_ITEMS } from "./nav";
import { APP_BASE } from "./routes";
import { NAV_ROW_HEIGHT, SiteNav } from "./SiteNav";

function renderNav(kind: "home" | "screener" | "etf" = "home", search = "") {
  const route =
    kind === "home"
      ? ({ kind: "home" } as const)
      : kind === "screener"
        ? ({ kind: "screener", assetClass: "stock" } as const)
        : ({ kind: "screener", assetClass: "etf" } as const);
  return render(
    <SiteNav
      route={route}
      search={search}
      onSelect={vi.fn()}
      inputRef={createRef<HTMLInputElement>()}
    />,
  );
}

describe("SiteNav", () => {
  it("renders the fourteen sections in order, each a real path", () => {
    renderNav();
    const nav = screen.getByRole("navigation", { name: "Primary" });
    const links = within(nav).getAllByRole("link");

    expect(links).toHaveLength(14);
    expect(links.map((link) => link.textContent)).toEqual(
      NAV_ITEMS.map((item) => item.label),
    );

    for (const link of links) {
      const href = link.getAttribute("href") ?? "";
      expect(href.startsWith(APP_BASE)).toBe(true);
      expect(href).not.toBe("#");
      expect(href).not.toBe("");
    }
    expect(links[1]?.getAttribute("href")).toBe("/app/screener");
    expect(links[2]?.getAttribute("href")).toBe("/app/screener/etf");
    expect(links[3]?.getAttribute("href")).toBe("/app/map");
  });

  it("carries no account or marketing links", () => {
    renderNav();
    for (const forbidden of ["Login", "Log in", "Sign up free", "Pricing", "Help"]) {
      expect(screen.queryByRole("link", { name: forbidden })).toBeNull();
    }
    expect(
      screen.getByRole("navigation", { name: "Primary" }).textContent,
    ).not.toMatch(/login|sign up|pricing|help/i);
  });

  it("marks the current section once, with the accent underline", () => {
    const { unmount } = renderNav("screener");
    const active = screen.getAllByRole("link").filter(
      (link) => link.getAttribute("aria-current") === "page",
    );
    expect(active).toHaveLength(1);
    expect(active[0]?.textContent).toBe("Stock Screener");
    expect(active[0]?.className).toContain("border-[color:var(--accent)]");
    expect(active[0]?.className).toContain("text-[12px]");
    expect(active[0]?.className).toContain("font-medium");
    unmount();

    renderNav("etf");
    expect(
      screen.getAllByRole("link").find((link) => link.getAttribute("aria-current") === "page")
        ?.textContent,
    ).toBe("ETF Screener");
  });

  it("is one 36 px row that cannot wrap", () => {
    renderNav();
    const row = screen.getByTestId("nav-row");
    expect(row.style.height).toBe(`${NAV_ROW_HEIGHT}px`);
    expect(NAV_ROW_HEIGHT).toBe(36);
    expect(row.className).not.toContain("flex-wrap");

    const list = screen.getByTestId("primary-nav");
    expect(list.className).not.toContain("flex-wrap");
    expect(list.className).toContain("items-stretch");

    const links = within(screen.getByRole("navigation", { name: "Primary" })).getAllByRole(
      "link",
    );
    for (const link of links) {
      expect(link.className).toContain("whitespace-nowrap");
      // Hover is a background wash, not a second underline.
      expect(link.className).toContain("hover:bg-row-hover");
      expect(link.className).toContain("border-b-2");
    }
  });

  it("puts the product's search phrasing in the one search box", () => {
    renderNav();
    const input = screen.getByRole("combobox", { name: "Search symbols and companies" });
    expect(input.getAttribute("placeholder")).toBe("Search ticker, company or profile");
  });

  it("keeps the clock on the second line, right-aligned, and nothing else", () => {
    renderNav();
    const utility = screen.getByTestId("nav-utility");
    expect(utility.className).toContain("justify-end");
    expect(within(utility).getByTestId("clock")).toBeTruthy();
    // No version, no account, no marketing in the utility line either.
    expect(utility.textContent ?? "").not.toMatch(/v\d|login|sign up|pricing|help/i);
    expect(screen.queryByRole("link", { name: /pricing|help/i })).toBeNull();
  });
});
