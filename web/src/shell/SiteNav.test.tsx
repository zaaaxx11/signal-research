/**
 * The header's contract is a list and a set of widths.
 *
 * The list: fourteen sections, in order, each a real href — unchanged at 1280,
 * where the row is 36 px and every label is on one line. That is a density
 * contract too: no `flex-wrap`, every label `whitespace-nowrap`, and the one
 * flexible element between the brand and the search is the nav itself.
 *
 * The widths: jsdom has no layout engine, so `scrollWidth` and the real row
 * height are measured in a browser (the hand-off records the numbers). What
 * this file can prove is the structure that makes the single row possible, and
 * the structure that makes the fold safe: the panel is a real element carrying
 * `hidden`, so a collapsed menu is out of both the tab order and the
 * accessibility tree; Escape is a retreat that puts focus back on the toggle;
 * the theme switch writes the document's own `data-theme` and names the state
 * it is in; and the clock still owns exactly one timer.
 */

import { createRef, type FormEvent } from "react";

import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { HeaderBar, NAV_PANEL_ID, NAV_ROW_HEIGHT } from "./HeaderBar";
import { NAV_ITEMS } from "./nav";
import { APP_BASE } from "./routes";

/**
 * The theme module is a sibling task (`shell/theme.ts`). Everything asserted
 * here is the *wiring* — the label, `aria-pressed`, the `data-theme` write and
 * the status sentence — so the tests run against a stand-in that keeps the same
 * promise the real module makes: `<html data-theme>` is the store, and a change
 * notifies every reader.
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
    // The widths change with the viewport; these do not. Pinned as a list, so a
    // re-order or a dropped mount is a failure and not a "close enough".
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

  it("is one 36 px row that cannot wrap, and folds below 1024", () => {
    renderNav();
    const row = screen.getByTestId("nav-row");
    expect(row.style.height).toBe(`${NAV_ROW_HEIGHT}px`);
    expect(NAV_ROW_HEIGHT).toBe(36);
    expect(row.className).not.toContain("flex-wrap");
    expect(row.className).toContain("min-w-0");

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

    // Below `lg` the row is gone and the toggle is the only way in; at and
    // above it the reverse. Exactly one of the two controls exists at 1024.
    const nav = screen.getByRole("navigation", { name: "Primary" });
    expect(nav.className).toContain("hidden");
    expect(nav.className).toContain("lg:flex");
    expect(screen.getByTestId("nav-toggle").className).toContain("lg:hidden");
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

  it("still schedules exactly one timer: the clock's minute boundary", () => {
    vi.useFakeTimers();
    renderNav();
    // Nothing in the header keeps its own interval. If the fold or the theme
    // switch added one, the reader's minute would start costing re-renders.
    expect(vi.getTimerCount()).toBe(1);
  });
});

describe("the fold", () => {
  it("is a real hidden panel, and its links are not in the tab order while closed", async () => {
    const user = userEvent.setup();
    renderNav();

    const panel = screen.getByTestId("nav-panel");
    const toggle = screen.getByTestId("nav-toggle");

    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(toggle.getAttribute("aria-controls")).toBe(NAV_PANEL_ID);
    // `aria-controls` names an element that exists — the panel, not a promise.
    expect(document.getElementById(NAV_PANEL_ID)).toBe(panel);
    expect(panel.hidden).toBe(true);

    // The anchors are in the DOM (a real element, not an unmounted subtree) …
    expect(panel.querySelectorAll("a[href]")).toHaveLength(14);
    // … and `hidden` is what keeps them out of the accessibility tree.
    expect(within(panel).queryAllByRole("link")).toHaveLength(0);
    for (const link of panel.querySelectorAll("a[href]")) {
      expect(link.closest("[hidden]")).toBe(panel);
    }

    // The adversarial half: fifteen Tab presses must not land inside a menu
    // nobody opened.
    for (let press = 0; press < 15; press += 1) {
      await user.tab();
      expect(panel.contains(document.activeElement)).toBe(false);
    }
  });

  it("opens on the toggle, flips aria-expanded, and stacks two columns at 640", async () => {
    const user = userEvent.setup();
    renderNav();

    const toggle = screen.getByTestId("nav-toggle");
    const panel = screen.getByTestId("nav-panel");
    expect(toggle.className).toContain("size-7");

    await user.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(panel.hidden).toBe(false);
    expect(within(panel).getAllByRole("link")).toHaveLength(14);

    const list = within(panel).getByTestId("nav-panel-list");
    expect(list.className).toContain("grid");
    expect(list.className).toContain("grid-cols-1");
    expect(list.className).toContain("sm:grid-cols-2");

    await user.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(panel.hidden).toBe(true);
  });

  it("cannot move the page: the panel is positioned, not stacked in flow", () => {
    renderNav();
    const panel = screen.getByTestId("nav-panel");
    const row = screen.getByTestId("nav-row");
    // Absolute under the header: opening it paints over content instead of
    // pushing it down and back (which is what a "jump twice" looks like).
    expect(panel.className).toContain("absolute");
    expect(panel.className).toContain("top-full");
    expect(panel.parentElement).toBe(row.parentElement);
  });

  it("closes on Escape and gives focus back to the toggle", async () => {
    const user = userEvent.setup();
    renderNav();

    const toggle = screen.getByTestId("nav-toggle");
    await user.click(toggle);
    const panel = screen.getByTestId("nav-panel");
    const links = within(panel).getAllByRole("link");
    links[9]?.focus();
    expect(document.activeElement).toBe(links[9]);

    await user.keyboard("{Escape}");
    expect(panel.hidden).toBe(true);
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(document.activeElement).toBe(toggle);
  });

  it("closes when a section is chosen, so the menu is not left over the page", async () => {
    const user = userEvent.setup();
    renderNav();
    await user.click(screen.getByTestId("nav-toggle"));
    const panel = screen.getByTestId("nav-panel");
    const link = within(panel).getAllByRole("link")[2] as HTMLElement;
    fireEvent.click(link);
    expect(panel.hidden).toBe(true);
    expect(screen.getByTestId("nav-toggle").getAttribute("aria-expanded")).toBe("false");
  });
});

describe("the day/night switch", () => {
  it("flips data-theme, its label and aria-pressed, and says so", () => {
    renderNav();
    const button = screen.getByTestId("theme-toggle");

    expect(button.getAttribute("type")).toBe("button");
    // 28 px, stated as the one class that means it.
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
    // The visible glyph is decoration; the sentence is the name.
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

    // Same node (the switch re-rendered, it did not remount the header), same
    // text, and focus is still in the field the reader was typing into.
    expect(screen.getByRole("combobox", { name: "Search symbols and companies" })).toBe(input);
    expect(input.value).toBe("AAPL");
    expect(document.activeElement).toBe(input);
  });
});

describe("the narrow row", () => {
  it("keeps a fixed-width budget that fits 900 px", () => {
    renderNav();
    const row = screen.getByTestId("nav-row");

    // Every fixed pixel width inside the row is either small enough to fit
    // beside the search at 900 px or scoped to `lg` and above (where it may be
    // 272 px, the measurement the 1280 px contract names).
    const fixed = [...row.querySelectorAll<HTMLElement>("[class*='w-[']")]
      .flatMap((element) => [...element.classList])
      .filter((name) => /^w-\[(\d+)px\]$/.test(name))
      .map((name) => Number(/^w-\[(\d+)px\]$/.exec(name)?.[1] ?? 0));
    expect(fixed.every((width) => width <= 272)).toBe(true);
    // The search is the flexible one below `lg`: it shrinks, it does not vanish.
    const search = [...row.children].find((child) =>
      child.className.includes("lg:w-[272px]"),
    ) as HTMLElement | undefined;
    expect(search?.className).toContain("min-w-0");
    expect(search?.className).toContain("flex-1");
    // Nothing in the row can force the document wider than the viewport.
    expect(row.className).toContain("min-w-0");
  });
});
