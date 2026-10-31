/**
 * The shell's top: 14 sections, the symbol search, and the clock.
 *
 * Density is the point. Finviz's nav is 36 px tall with 12 px/500 labels,
 * separated by whitespace rather than rules; the active section is marked by a
 * 2 px rose underline and a hover is a row-tinted wash. The row does not wrap:
 * every label is `whitespace-nowrap`, the search is a fixed-width box, and the
 * nav is the only flexible thing between them. Measured in Chromium at
 * 1280 × 800: the labels run 115 → 950 px on one 36 px row, the search box
 * 1000 → 1272 px, and the document has no horizontal overflow (scrollWidth
 * equals innerWidth). The test file asserts the structure that keeps it that
 * way; the numbers are from `getBoundingClientRect` on the rendered page.
 *
 * The second line carries the clock, right-aligned, and nothing else. There is
 * no account chrome and no marketing: this build has no sessions, so a "Log in"
 * link would be a promise the product cannot keep.
 */

import * as React from "react";

import { SymbolSearch } from "./SymbolSearch";
import { Clock } from "./Clock";
import { NAV_ITEMS, isActive } from "./nav";
import { APP_BASE, Link, type Route } from "./routes";
import type { SymbolHit } from "@/lib/api";
import { cn } from "@/lib/utils";

export interface SiteNavProps {
  route: Route;
  /** The current query string; only the News item reads it. */
  search: string;
  onSelect: (hit: SymbolHit) => void;
  inputRef: React.RefObject<HTMLInputElement | null>;
}

/** The nav's row height, stated once so the CSS and the test agree. */
export const NAV_ROW_HEIGHT = 36;

export function SiteNav({ route, search, onSelect, inputRef }: SiteNavProps) {
  return (
    <header
      data-testid="site-header"
      data-base={APP_BASE}
      className="shrink-0 border-b border-line bg-surface"
    >
      <div
        data-testid="nav-row"
        style={{ height: NAV_ROW_HEIGHT }}
        className="flex min-w-0 items-stretch gap-2 px-2"
      >
        <Link
          to="/"
          aria-label="Ztockz home"
          className="flex shrink-0 items-center gap-1.5 pr-1 text-[13px] font-semibold uppercase tracking-[0.16em] text-fg"
        >
          <span
            aria-hidden="true"
            className="flex size-[18px] items-center justify-center rounded-[3px] border border-line-strong bg-surface-sunken font-mono text-[11px] font-bold leading-none text-up"
          >
            Z
          </span>
          Ztockz
        </Link>

        <nav aria-label="Primary" className="min-w-0 flex-1">
          <ul data-testid="primary-nav" className="flex h-full items-stretch overflow-x-auto">
            {NAV_ITEMS.map((item) => {
              const active = isActive(item, route, search);
              return (
                <li key={item.label} className="flex">
                  <Link
                    to={item.path}
                    aria-current={active ? "page" : undefined}
                    data-nav-item={item.label}
                    data-active={active ? "true" : "false"}
                    className={cn(
                      // 2 px underline inside the 36 px row: the border replaces
                      // padding, so the label never moves when it becomes active.
                      "flex items-center whitespace-nowrap border-b-2 px-1.5 text-[12px] font-medium leading-none transition-colors hover:bg-row-hover",
                      active
                        ? "border-[color:var(--accent)] text-fg"
                        : "border-transparent text-fg-muted hover:text-fg",
                    )}
                  >
                    {item.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>

        <div className="flex w-[272px] shrink-0 items-center">
          <SymbolSearch
            onSelect={onSelect}
            inputRef={inputRef}
            placeholder="Search ticker, company or profile"
            showHint={false}
          />
        </div>
      </div>

      <div
        data-testid="nav-utility"
        className="flex h-[22px] items-center justify-end px-3"
      >
        <Clock />
      </div>
    </header>
  );
}
