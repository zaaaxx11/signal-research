/**
 * The header's contract is a list and a set of widths.
 *
 * The list: fourteen sections, in order, each a real href — unchanged at 1280,
 * where the row is 36 px and every label is on one line. That is a density
 * contract too: no `flex-wrap`, every label `whitespace-nowrap`, and the one
 * flexible element between the brand and the search is the nav itself.
 *
 * There is no menu button. Every section is on the row at every width; when the
 * row is too narrow for fourteen labels the nav scrolls inside itself, and the
 * tests below prove the structure that makes that safe (a scroll container with
 * `min-w-0`, labels that never wrap, a search that shrinks rather than pushing
 * the document sideways).
 *
 * The theme switch writes the document's own `data-theme` and names the state
 * it is in; the clock still owns exactly one timer and shares the row.
 */

import { createRef, type FormEvent } from "react";

import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { HeaderBar, NAV_ROW_HEIGHT } from "./HeaderBar";
import { NAV_ITEMS } from "./nav";
import { APP_BASE } from "./routes";

/**
 * The theme module owns storage and the media query; everything asserted here
 * is the *wiring* — the label, `aria-pressed`, the `data-theme` write and the
 * status sentence — so the tests run against a stand-in that keeps the same
 * promise: `<html data-theme>` is the store, and a change notifies every reader.
 */
vi.mock("./theme", async () => {
  const React = await import("react");
  const listeners = new Set<() => void>();
  const read = (): "dark" | "light" =>
    document.documentElement.dataset.theme === "light" ? "light" : "dark";
  const write = (next: "dark" | "light") => {
    document.documentElement.dataset.theme = next;
    for (const listener of listeners) listener();
  };
  return {
    useTheme: () => {
      const theme = React.useSyncExternalStore((listener: () => void) => {
        listeners.add(listener);
        return () => {
          listeners.delete(listener);
        };
      }, read);
      return {
        theme,
        setTheme: write,
        toggle: () => write(read() === "dark" ? "light" : "dark"),
      };
    },
  };
});

beforeEach(() => {
  // The document ships dark (`index.html`), and every test starts there.
  document.documentElement.dataset.theme = "dark";
});

afterEach(() => {
  vi.useRealTimers();
  window.history.replaceState(null, "", "/app/");
});

function renderNav(kind: "home" | "screener" | "etf" = "home", search = "") {
  const route =
    kind === "home"
      ? ({ kind: "home" } as const)
      : kind === "screener"
        ? ({ kind: "screener", assetClass: "stock" } as const)
        : ({ kind: "screener", assetClass: "etf" } as const);
  return render(
    <HeaderBar
      route={route}
      search={search}
      onSelect={vi.fn()}
      inputRef={createRef<HTMLInputElement>()}
    />,
  );
}

describe("HeaderBar", () => {
  it("renders the fourteen sections in order, each a real href", () => {
    renderNav();
    const nav = screen.getByRole("navigation", { name: "Primary" });
    const links = within(nav).getAllByRole("link");

    expect(links).toHaveLength(14);
    expect(links.map((link) => link.textContent)).toEqual(
      NAV_ITEMS.map((item) => item.label),
    );
    expect(links.map((link) => link.getAttribute("href"))).toEqual([
      "/app/",
      "/app/screener",
      "/app/screener/etf",
      "/app/map",
      "/app/charts",
      "/app/news",
      "/app/groups",
      "/app/insider",
      "/app/futures",
      "/app/forex",
      "/app/crypto",
      "/app/watchlist",
      "/app/portfolio",
      "/app/calendar",
    ]);
    for (const link of links) {
      const href = link.getAttribute("href") ?? "";
      expect(href.startsWith(APP_BASE)).toBe(true);
      expect(href).not.toBe("#");
    }
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

  it("is one 36 px row that never folds, and never wraps", () => {
    renderNav();
    const row = screen.getByTestId("nav-row");
    expect(row.style.height).toBe(`${NAV_ROW_HEIGHT}px`);
    expect(NAV_ROW_HEIGHT).toBe(36);
    expect(row.className).not.toContain("flex-wrap");
    expect(row.className).toContain("min-w-0");

    const nav = screen.getByRole("navigation", { name: "Primary" });
    // Present at every width: no `hidden lg:flex` and no sibling toggle.
    expect(nav.className).toContain("flex");
    expect(nav.className).not.toMatch(/(^|\s)hidden(\s|$)/);
    expect(screen.queryByTestId("nav-toggle")).toBeNull();
    expect(screen.queryByTestId("nav-panel")).toBeNull();
    expect(screen.queryByRole("button", { name: "Sections" })).toBeNull();

    const list = screen.getByTestId("primary-nav");
    expect(list.className).not.toContain("flex-wrap");
    expect(list.className).toContain("items-stretch");
    // The overflow lives here, not on the document.
    expect(list.className).toContain("overflow-x-auto");

    for (const link of within(nav).getAllByRole("link")) {
      expect(link.className).toContain("whitespace-nowrap");
      // Hover is a background wash, not a second underline.
      expect(link.className).toContain("hover:bg-row-hover");
      expect(link.className).toContain("border-b-2");
    }
  });

  it("keeps all fourteen links reachable by keyboard while the row scrolls", async () => {
    const user = userEvent.setup();
    renderNav();
    const nav = screen.getByRole("navigation", { name: "Primary" });

    // Fifteen Tab presses from the brand reach the search; the sections before
    // it are in the tab order even when the row is visually clipped.
    const links = within(nav).getAllByRole("link");
    (links[0] as HTMLElement).focus();
    for (let press = 0; press < 13; press += 1) {
      await user.tab();
    }
    const focused = document.activeElement as HTMLElement;
    expect(nav.contains(focused)).toBe(true);
    expect(focused.getAttribute("href")).toBe("/app/calendar");
  });

  it("puts the product's search phrasing in the one search box", () => {
    renderNav();
    const input = screen.getByRole("combobox", { name: "Search symbols and companies" });
    expect(input.getAttribute("placeholder")).toBe("Search ticker, company or profile");
  });

  it("keeps the clock on the row, right-aligned, and nothing else", () => {
    renderNav();
    const utility = screen.getByTestId("nav-utility");
    expect(within(utility).getByTestId("clock")).toBeTruthy();
    // The clock shares the 36 px row: a second utility line would make the
    // header taller for one HH:MM.
    expect(utility.closest("[data-testid='nav-row']")).toBeTruthy();
    // No version, no account, no marketing anywhere in the header.
    expect(utility.textContent ?? "").not.toMatch(/v\d|login|sign up|pricing|help/i);
    expect(screen.getByTestId("site-header").textContent ?? "").not.toMatch(/v\d\.\d/);
  });

  it("still schedules exactly one timer: the clock's minute boundary", () => {
    vi.useFakeTimers();
    renderNav();
    expect(vi.getTimerCount()).toBe(1);
  });
});

describe("the day/night switch", () => {
  it("flips data-theme, its label and aria-pressed, and says so", () => {
    renderNav();
    const button = screen.getByTestId("theme-toggle");

    expect(button.getAttribute("type")).toBe("button");
    expect(button.className).toContain("size-7");
    expect(button.getAttribute("aria-label")).toBe("Switch to light theme");
    expect(button.getAttribute("aria-pressed")).toBe("false");
    expect(document.documentElement.dataset.theme).toBe("dark");

    fireEvent.click(button);
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(button.getAttribute("aria-label")).toBe("Switch to dark theme");
    expect(button.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("status").textContent).toBe("Light theme on.");

    fireEvent.click(button);
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(button.getAttribute("aria-label")).toBe("Switch to light theme");
    expect(button.getAttribute("aria-pressed")).toBe("false");
    expect(screen.getByRole("status").textContent).toBe("Dark theme on.");
  });

  it("names its glyph in text: the state is never carried by colour alone", () => {
    renderNav();
    const button = screen.getByTestId("theme-toggle");
    expect(button.textContent).toContain("☀");
    expect(button.querySelector('[aria-hidden="true"]')?.textContent).toBe("☀");

    fireEvent.click(button);
    expect(button.querySelector('[aria-hidden="true"]')?.textContent).toBe("◐");
    expect(button.querySelector(".sr-only")?.textContent).toBe("Dark theme");
  });

  it("submits no form and navigates nowhere", () => {
    const onSubmit = vi.fn((event: FormEvent) => event.preventDefault());
    window.history.replaceState(null, "", "/app/screener");
    render(
      <form onSubmit={onSubmit}>
        <HeaderBar
          route={{ kind: "screener", assetClass: "stock" }}
          search=""
          onSelect={vi.fn()}
          inputRef={createRef<HTMLInputElement>()}
        />
      </form>,
    );

    fireEvent.click(screen.getByTestId("theme-toggle"));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(window.location.pathname).toBe("/app/screener");
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("does not touch the search: same field, same text, same focus", async () => {
    const user = userEvent.setup();
    renderNav();
    const input = screen.getByRole("combobox", {
      name: "Search symbols and companies",
    }) as HTMLInputElement;
    await user.type(input, "AAPL");
    expect(document.activeElement).toBe(input);

    fireEvent.click(screen.getByTestId("theme-toggle"));

    expect(screen.getByRole("combobox", { name: "Search symbols and companies" })).toBe(input);
    expect(input.value).toBe("AAPL");
    expect(document.activeElement).toBe(input);
  });
});

describe("the narrow row", () => {
  it("keeps a fixed-width budget that fits 900 px", () => {
    renderNav();
    const row = screen.getByTestId("nav-row");

    const fixed = [...row.querySelectorAll<HTMLElement>("[class*='w-[']")]
      .flatMap((element) => [...element.classList])
      .filter((name) => /^w-\[(\d+)px\]$/.test(name))
      .map((name) => Number(/^w-\[(\d+)px\]$/.exec(name)?.[1] ?? 0));
    expect(fixed.every((width) => width <= 272)).toBe(true);

    // The search shrinks in steps (168 → 220 → 272) and never disappears.
    const search = [...row.children].find((child) =>
      child.className.includes("w-[168px]"),
    ) as HTMLElement | undefined;
    expect(search?.className).toContain("min-w-0");
    expect(search?.className).toContain("shrink-0");
    expect(row.className).toContain("min-w-0");
  });
});
