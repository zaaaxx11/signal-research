/**
 * The 14 sections, in the order the product states them.
 *
 * Each one is a real path with a real page. There is no placeholder section
 * and no `href="#"`: the list is data, not markup, because two things read it
 * and must agree — the nav itself and the route test that proves every href
 * resolves.
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
  { label: "Maps", path: "/map" },
  { label: "Charts", path: "/charts" },
  { label: "News", path: "/news" },
  { label: "Groups", path: "/groups" },
  { label: "Insider", path: "/insider" },
  { label: "Futures", path: "/futures" },
  { label: "Forex", path: "/forex" },
  { label: "Crypto", path: "/crypto" },
  { label: "Watchlist", path: "/watchlist" },
  { label: "Portfolio", path: "/portfolio" },
  { label: "Calendar", path: "/calendar" },
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
  return false;
}

function isNews(search: string): boolean {
  return new URLSearchParams(search).get("view") === "news";
}
