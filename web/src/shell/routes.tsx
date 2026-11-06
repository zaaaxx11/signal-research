/**
 * The shell's URLs, and the one place that knows them.
 *
 * The app is served by FastAPI under `/app` (`vite.config.ts` sets that base),
 * and that mount is the only one with an SPA fallback: `/app/<anything>` serves
 * `index.html`, while `/` serves it once for the front door. So a nav href has
 * to carry the mount — `/screener` at the origin root is a 404, `/app/screener`
 * is the shell. `appHref` is that rule, stated once.
 *
 * Parsing is deliberately forgiving about the mount: `/screener` and
 * `/app/screener` both resolve to the screener. The server serves the shell at
 * both URLs (the front door is `/`), and a route table that only understood one
 * of them would turn the other into a dead end.
 *
 * There is no router dependency. The surface is five destinations, the
 * navigation is a handful of anchors, and the browser already owns history:
 * `pushState` writes the URL, `popstate` reads it back, `useSyncExternalStore`
 * re-renders. A `<Link>` keeps the real `href` (middle-click and "open in new
 * tab" must work, and they must hit a URL the server can answer), and
 * intercepts only the plain left click that a client can honour without a
 * round trip.
 */

import * as React from "react";

/**
 * The mount, without a trailing slash: `/app`, or `""` if the base is root.
 *
 * `vite` reports `/app/` in dev and in the build, but Vitest resolves
 * `BASE_URL` to `/` because its server has no mount. The mount is a property of
 * the deployment, not of the test runner — `app/main.py` serves the shell at
 * `/app` and answers every path under it — so a run that reports `/` is told
 * the real one rather than being allowed to emit hrefs that 404 in production.
 */
const ENV_BASE = import.meta.env.BASE_URL || "/app/";
export const APP_BASE = ENV_BASE === "/" ? "/app" : ENV_BASE.replace(/\/+$/, "");

/** The paths this build answers. Used by the nav test to prove no dead links. */
export const ROUTE_PATHS = [
  "/",
  "/screener",
  "/screener/etf",
  "/compare",
  "/map",
  "/groups",
  "/calendar",
  "/charts",
  "/insider",
  "/crypto",
  "/futures",
  "/forex",
  "/watchlist",
  "/portfolio",
  "/news",
] as const;

export type Route =
  | { kind: "home" }
  | { kind: "screener"; assetClass: "stock" | "etf" }
  | { kind: "compare" }
  | { kind: "stock"; symbol: string }
  | { kind: "map" }
  | { kind: "groups" }
  | { kind: "calendar" }
  | { kind: "charts" }
  | { kind: "insider" }
  | { kind: "insiderDetail"; symbol: string }
  | { kind: "crypto" }
  | { kind: "cryptoDetail"; symbol: string }
  | { kind: "futures" }
  | { kind: "forex" }
  | { kind: "watchlist" }
  | { kind: "portfolio" }
  | { kind: "news" }
  | { kind: "notfound"; path: string };

/** A route's href, mount included. `/` becomes `/app/`, not `/app`. */
export function appHref(path: string): string {
  if (!path.startsWith("/")) throw new Error(`route paths must start with "/": ${path}`);
  return `${APP_BASE}${path}`;
}

/** Strip the mount and any trailing slash: `/app/screener/` → `/screener`. */
export function routePath(pathname: string): string {
  let path = pathname;
  if (APP_BASE && (path === APP_BASE || path.startsWith(`${APP_BASE}/`))) {
    path = path.slice(APP_BASE.length) || "/";
  }
  if (path.length > 1 && path.endsWith("/")) path = path.replace(/\/+$/, "");
  return path.length > 0 ? path : "/";
}

/** A ticker as a path segment: letters, digits, dot and dash. */
const SYMBOL_PATTERN = /^[A-Za-z0-9][A-Za-z0-9.\-]{0,15}$/;

export function parseRoute(pathname: string): Route {
  const path = routePath(pathname);

  if (path === "/") return { kind: "home" };
  if (path === "/screener") return { kind: "screener", assetClass: "stock" };
  if (path === "/screener/etf") return { kind: "screener", assetClass: "etf" };
  // The query string (`?symbols=A,B`) is the page's state, not a route: the
  // shell parses the path and the compare page reads its own selection, so a
  // shared link and a hand-typed one land on the same route.
  if (path === "/compare") return { kind: "compare" };
  if (path === "/map") return { kind: "map" };
  if (path === "/groups") return { kind: "groups" };
  if (path === "/calendar") return { kind: "calendar" };
  if (path === "/charts") return { kind: "charts" };
  if (path === "/insider") return { kind: "insider" };
  if (path.startsWith("/insider/")) {
    const raw = path.slice("/insider/".length);
    let symbol = raw;
    try {
      symbol = decodeURIComponent(raw);
    } catch {
      return { kind: "notfound", path };
    }
    if (SYMBOL_PATTERN.test(symbol)) return { kind: "insiderDetail", symbol: symbol.toUpperCase() };
    return { kind: "notfound", path };
  }
  if (path === "/crypto") return { kind: "crypto" };
  if (path === "/futures") return { kind: "futures" };
  if (path === "/forex") return { kind: "forex" };
  if (path === "/watchlist") return { kind: "watchlist" };
  if (path === "/portfolio") return { kind: "portfolio" };
  if (path === "/news") return { kind: "news" };

  if (path.startsWith("/stock/")) {
    const raw = path.slice("/stock/".length);
    let symbol = raw;
    try {
      symbol = decodeURIComponent(raw);
    } catch {
      return { kind: "notfound", path };
    }
    if (SYMBOL_PATTERN.test(symbol)) return { kind: "stock", symbol: symbol.toUpperCase() };
    return { kind: "notfound", path };
  }

  if (path.startsWith("/crypto/")) {
    const raw = path.slice("/crypto/".length);
    let symbol = raw;
    try {
      symbol = decodeURIComponent(raw);
    } catch {
      return { kind: "notfound", path };
    }
    if (SYMBOL_PATTERN.test(symbol))
      return { kind: "cryptoDetail", symbol: symbol.toUpperCase() };
    return { kind: "notfound", path };
  }

  return { kind: "notfound", path };
}

/* ---------------------------------------------------------------------------
 * Navigation
 * ------------------------------------------------------------------------ */

const listeners = new Set<() => void>();

function notify(): void {
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  window.addEventListener("popstate", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("popstate", listener);
  };
}

function snapshot(): string {
  return `${window.location.pathname}${window.location.search}`;
}

/** Write a URL and re-render every route reader. */
export function navigate(to: string, options: { replace?: boolean } = {}): void {
  const url = to.startsWith("/") ? appHref(to) : to;
  if (options.replace) window.history.replaceState(null, "", url);
  else window.history.pushState(null, "", url);
  notify();
}

/** The current pathname + search, and the route it names. */
export function useRoute(): Route {
  const location = React.useSyncExternalStore(subscribe, snapshot, snapshot);
  const pathname = location.split("?")[0] ?? "/";
  return React.useMemo(() => parseRoute(pathname), [pathname]);
}

/** The current query string, for a nav item whose route is a view. */
export function useSearch(): string {
  const location = React.useSyncExternalStore(subscribe, snapshot, snapshot);
  return location.includes("?") ? (location.split("?").slice(1).join("?") ?? "") : "";
}

export interface LinkProps extends Omit<React.AnchorHTMLAttributes<HTMLAnchorElement>, "href"> {
  /** A route path, without the mount: `/screener`, `/stock/AAPL`. */
  to: string;
  replace?: boolean;
}

/**
 * An anchor that keeps its href and routes in place.
 *
 * A modified click (⌘/Ctrl/Shift/Alt, middle button) is left alone: the browser
 * opens a real URL, which the server answers with the shell. Only a plain left
 * click is turned into a `pushState`, and only when nothing upstream has
 * already claimed the event.
 */
export function Link({ to, replace = false, onClick, ...rest }: LinkProps): React.ReactElement {
  return (
    <a
      href={appHref(to)}
      onClick={(event) => {
        onClick?.(event);
        if (event.defaultPrevented) return;
        if (event.button !== 0) return;
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        navigate(to, { replace });
      }}
      {...rest}
    />
  );
}
