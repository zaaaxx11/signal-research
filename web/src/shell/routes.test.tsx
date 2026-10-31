import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { NAV_ITEMS } from "./nav";
import {
  APP_BASE,
  ROUTE_PATHS,
  SOON_SLUGS,
  Link,
  appHref,
  navigate,
  parseRoute,
  routePath,
  useRoute,
} from "./routes";

/**
 * The route table is the product's map: fourteen nav items, five destinations,
 * and no path that lands on nothing.
 *
 * These tests are about URLs, not pixels. `appHref` carries the `/app` mount
 * because that mount is the only one the server answers with the shell — a
 * `/screener` href at the origin root is a 404, and a nav full of those is a
 * nav full of dead links. Parsing is forgiving about the mount so a link from
 * either URL still renders.
 */

beforeEach(() => {
  window.history.replaceState(null, "", "/");
});

describe("appHref", () => {
  it("carries the mount and keeps `/` a directory", () => {
    expect(APP_BASE).toBe("/app");
    expect(appHref("/")).toBe("/app/");
    expect(appHref("/screener")).toBe("/app/screener");
    expect(appHref("/screener/etf")).toBe("/app/screener/etf");
    expect(appHref("/soon/maps")).toBe("/app/soon/maps");
  });

  it("refuses a relative path", () => {
    expect(() => appHref("screener")).toThrow(/must start with/);
  });
});

describe("routePath", () => {
  it("strips the mount and a trailing slash", () => {
    expect(routePath("/app")).toBe("/");
    expect(routePath("/app/")).toBe("/");
    expect(routePath("/app/screener/")).toBe("/screener");
    expect(routePath("/screener")).toBe("/screener");
  });
});

describe("parseRoute", () => {
  it("names Home for the front door and the mount root", () => {
    expect(parseRoute("/")).toEqual({ kind: "home" });
    expect(parseRoute("/app")).toEqual({ kind: "home" });
    expect(parseRoute("/app/")).toEqual({ kind: "home" });
  });

  it("splits the two screener routes by asset class", () => {
    expect(parseRoute("/app/screener")).toEqual({ kind: "screener", assetClass: "stock" });
    expect(parseRoute("/app/screener/etf")).toEqual({ kind: "screener", assetClass: "etf" });
  });

  it("reads a ticker, dots included, and refuses a broken one", () => {
    expect(parseRoute("/app/stock/aapl")).toEqual({ kind: "stock", symbol: "AAPL" });
    expect(parseRoute("/app/stock/BRK.B")).toEqual({ kind: "stock", symbol: "BRK.B" });
    expect(parseRoute("/app/stock/")).toEqual({ kind: "notfound", path: "/stock" });
    expect(parseRoute("/app/stock/../etc")).toEqual({ kind: "notfound", path: "/stock/../etc" });
  });

  it("names every unbuilt section, and nothing else", () => {
    for (const slug of SOON_SLUGS) {
      expect(parseRoute(`/app/soon/${slug}`)).toEqual({ kind: "soon", slug });
    }
    expect(parseRoute("/app/soon/login").kind).toBe("notfound");
    expect(parseRoute("/app/soon/signup").kind).toBe("notfound");
  });

  it("returns a not-found route for anything else", () => {
    expect(parseRoute("/app/nope")).toEqual({ kind: "notfound", path: "/nope" });
    expect(parseRoute("/app/screener/etf/extra").kind).toBe("notfound");
  });
});

describe("the nav table", () => {
  it("is exactly the fourteen product sections, in order", () => {
    expect(NAV_ITEMS.map((item) => item.label)).toEqual([
      "Home",
      "Stock Screener",
      "ETF Screener",
      "Maps",
      "Charts",
      "News",
      "Groups",
      "Insider",
      "Futures",
      "Forex",
      "Crypto",
      "Watchlist",
      "Portfolio",
      "Calendar",
    ]);
  });

  it("sends every item to a route this build answers", () => {
    for (const item of NAV_ITEMS) {
      const href = appHref(item.path);
      expect(href.startsWith(APP_BASE)).toBe(true);
      expect(href).not.toBe("#");

      const pathname = href.slice(APP_BASE.length).split("?")[0] ?? "";
      const built = (ROUTE_PATHS as readonly string[]).includes(pathname);
      const soon = /^\/soon\/[a-z-]+$/.test(pathname);
      expect(built || soon, `${item.label} → ${href} is neither a route nor /soon/*`).toBe(true);

      // And the parser agrees: no item may land on the 404 page.
      expect(parseRoute(pathname).kind, `${item.label} → ${pathname}`).not.toBe("notfound");
    }
  });
});

function Where() {
  const route = useRoute();
  return <p data-testid="where">{route.kind}</p>;
}

describe("Link", () => {
  it("keeps a real href and routes the plain click", async () => {
    const user = userEvent.setup();
    render(
      <div>
        <Link to="/soon/maps">Maps</Link>
        <Where />
      </div>,
    );

    const anchor = screen.getByRole("link", { name: "Maps" });
    expect(anchor.getAttribute("href")).toBe("/app/soon/maps");
    expect(screen.getByTestId("where").textContent).toBe("home");

    await user.click(anchor);
    expect(window.location.pathname).toBe("/app/soon/maps");
    expect(screen.getByTestId("where").textContent).toBe("soon");
  });

  it("leaves a modified click to the browser", async () => {
    const user = userEvent.setup();
    const before = window.location.pathname;
    render(<Link to="/screener">Screener</Link>);

    await user.keyboard("{Meta>}");
    await user.click(screen.getByRole("link", { name: "Screener" }));
    await user.keyboard("{/Meta}");

    // No pushState: the browser's own "open in a new tab" owns this click. In
    // jsdom the navigation itself is a no-op, which is why the assertion is on
    // the URL staying put rather than on a new document.
    expect(window.location.pathname).toBe(before);
  });
});

describe("navigate", () => {
  it("writes the URL, and replace does not grow the history", () => {
    const start = window.history.length;
    navigate("/screener/etf");
    expect(window.location.pathname).toBe("/app/screener/etf");

    navigate("/screener", { replace: true });
    expect(window.location.pathname).toBe("/app/screener");
    // pushState added one entry, replaceState added none.
    expect(window.history.length).toBe(start + 1);
  });
});
