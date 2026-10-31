/**
 * The 14 sections, in the order the product states them.
 *
 * Each one is a real path. A section with no surface yet points at
 * `/soon/{slug}` — one page that names it — so the nav never contains a link
 * that 404s or a `href="#"` that does nothing. The list is data, not markup,
 * because three things read it and must agree: the nav itself, the route test
 * that proves every href resolves, and the soon page's title.
 */

import type { Route } from "./routes";

export interface NavItem {
  readonly label: string;
  /** A route path, mount excluded: `appHref` adds `/app`. */
  readonly path: string;
}

export const NAV_ITEMS: readonly NavItem[] = [
  { label: "Home", path: "/" },
  { label: "Stock Screener", path: "/screener" },
  { label: "ETF Screener", path: "/screener/etf" },
  { label: "Maps", path: "/soon/maps" },
  { label: "Charts", path: "/soon/charts" },
  // The news surface has no page of its own yet: the view catalogue does not
  // carry a `news` view, so pointing at the screener would open the wrong
  // content under the right name. The soon page names it honestly.
  { label: "News", path: "/soon/news" },
  { label: "Groups", path: "/soon/groups" },
  { label: "Insider", path: "/soon/insider" },
  { label: "Futures", path: "/soon/futures" },
  { label: "Forex", path: "/soon/forex" },
  { label: "Crypto", path: "/soon/crypto" },
  { label: "Watchlist", path: "/soon/watchlist" },
  { label: "Portfolio", path: "/soon/portfolio" },
  { label: "Calendar", path: "/soon/calendar" },
] as const;

/** The path part of an href, for comparing against a route. */
export function hrefPath(path: string): string {
  const [pathname] = path.split("?");
  return pathname ?? "/";
}

/**
 * Whether a nav item names the current screen.
 *
 * `search` is only consulted for the one item whose destination is a view
 * (`/screener?view=news`); every other item is decided by its path alone.
 */
export function isActive(item: NavItem, route: Route, search: string): boolean {
  if (item.path === "/") return route.kind === "home";
  if (item.path === "/screener") {
    return route.kind === "screener" && route.assetClass === "stock" && !isNews(search);
  }
  if (item.path === "/screener/etf") {
    return route.kind === "screener" && route.assetClass === "etf";
  }
  if (item.path === "/screener?view=news") {
    return route.kind === "screener" && route.assetClass === "stock" && isNews(search);
  }
  if (item.path.startsWith("/soon/")) {
    const slug = item.path.slice("/soon/".length);
    return route.kind === "soon" && route.slug === slug;
  }
  return false;
}

function isNews(search: string): boolean {
  return new URLSearchParams(search).get("view") === "news";
}
