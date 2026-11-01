/**
 * The shell's header: brand, fourteen sections, the symbol search, the day/night
 * switch, and the clock — one row, always.
 *
 * The numbers are the contract: 36 px tall, 12 px/500 labels separated by
 * whitespace rather than rules, the active section marked by a 2 px rose
 * underline, the search a fixed 272 px box above 1024 px and a flexible one
 * below, and the nav the only thing that may scroll between them.
 *
 * There is no menu button. The operator asked for the row the product had
 * before: every section visible at every width, with the nav scrolling *inside
 * itself* when there is not room for fourteen labels. A row that scrolls is
 * still a row you can read; a row behind a `☰` hides nine sections behind a
 * gesture and calls that navigation.
 *
 * The two things a reader came for stay on the row at every width: where they
 * are (the brand link) and how to search (the field). The clock sits at the end
 * of the same row, so the header is one 36 px strip rather than a strip plus a
 * utility line.
 *
 * The theme button is 28 px, names the theme it will switch *to*, and presses
 * its glyph and its `role="status"` sentence — never colour alone, never a form
 * submit, never a navigation.
 */

import * as React from "react";

import { Clock } from "./Clock";
import { SymbolSearch } from "./SymbolSearch";
import { NAV_ITEMS, isActive, type NavItem } from "./nav";
import { APP_BASE, Link, type Route } from "./routes";
import { useTheme } from "./theme";
import type { SymbolHit } from "@/lib/api";
import { cn } from "@/lib/utils";

/** The nav's row height, stated once so the CSS and the test agree. */
export const NAV_ROW_HEIGHT = 36;

export interface HeaderBarProps {
  route: Route;
  /** The current query string; only the News item reads it. */
  search: string;
  onSelect: (hit: SymbolHit) => void;
  inputRef: React.RefObject<HTMLInputElement | null>;
}

const ROW_LINK =
  "flex items-center whitespace-nowrap border-b-2 px-1.5 text-[12px] font-medium leading-none transition-colors hover:bg-row-hover";

/**
 * One section link.
 *
 * The active item is marked by the 2 px underline and `aria-current`, never by
 * colour alone; the underline sits on the bottom edge of a flex cell because at
 * the bottom of a box it would read as a border.
 */
function SectionLink({
  item,
  route,
  search,
}: {
  item: NavItem;
  route: Route;
  search: string;
}) {
  const active = isActive(item, route, search);
  return (
    <Link
      to={item.path}
      aria-current={active ? "page" : undefined}
      data-nav-item={item.label}
      data-active={active ? "true" : "false"}
      className={cn(
        ROW_LINK,
        active
          ? "border-[color:var(--accent)] text-fg"
          : "border-transparent text-fg-muted hover:text-fg",
      )}
    >
      {item.label}
    </Link>
  );
}

export function HeaderBar({ route, search, onSelect, inputRef }: HeaderBarProps) {
  const { theme, toggle } = useTheme();
  const light = theme === "light";

  /**
   * The last theme change, as a sentence. `null` until the first press: the
   * shell has exactly one `role="status"` at rest (the grid's row range), and a
   * second, permanently empty live region in the header would make that count
   * two. The sentence is rendered with the change it describes.
   */
  const [announcement, setAnnouncement] = React.useState<string | null>(null);

  const onTheme = () => {
    // Read the state that is on screen, not the one the setter will hold: the
    // sentence has to name the theme the reader just asked for.
    setAnnouncement(light ? "Dark theme on." : "Light theme on.");
    toggle();
  };

  return (
    <header
      data-testid="site-header"
      data-base={APP_BASE}
      className="relative shrink-0 border-b border-line bg-surface"
    >
      <div
        data-testid="nav-row"
        style={{ height: NAV_ROW_HEIGHT }}
        className="flex min-w-0 items-stretch gap-2 px-2"
      >
        <Link
          to="/"
          aria-label="Ztockz home"
          className="flex shrink-0 items-center gap-1.5 pr-1 text-[13px] font-semibold text-fg"
        >
          <span
            aria-hidden="true"
            className="flex size-[18px] items-center justify-center rounded-[3px] border border-line-strong bg-surface-sunken font-mono text-[11px] font-bold leading-none text-up"
          >
            Z
          </span>
          {/* Below 640 px the 18 px monogram is the whole brand: five letters of
              tracking is 60 px the search field needs more. */}
          <span className="hidden uppercase tracking-[0.16em] sm:inline">ZTOCKZ</span>
        </Link>

        {/*
         * The nav is always present and never wraps. When the row is wider than
         * the space between the brand and the search, the list scrolls
         * sideways: the labels stay 36 px tall, the wheel reaches the tail of
         * the row, and tabbing to a clipped label scrolls it into view. The
         * scrollbar is suppressed because a 15 px horizontal bar inside a 36 px
         * strip would eat the row's own height and re-flow the header.
         */}
        <nav aria-label="Primary" className="flex min-w-0 flex-1">
          <ul
            data-testid="primary-nav"
            className="flex h-full items-stretch overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
          >
            {NAV_ITEMS.map((item) => (
              <li key={item.label} className="flex">
                <SectionLink item={item} route={route} search={search} />
              </li>
            ))}
          </ul>
        </nav>

        {/* The search never hides: 272 px when there is room, flexible when
            there is not, and never the thing that pushes the row sideways. */}
        <div className="flex w-[168px] min-w-0 shrink-0 items-center sm:w-[220px] lg:w-[272px]">
          <SymbolSearch
            onSelect={onSelect}
            inputRef={inputRef}
            placeholder="Search ticker, company or profile"
            showHint={false}
          />
        </div>

        <button
          type="button"
          data-testid="theme-toggle"
          aria-label={light ? "Switch to dark theme" : "Switch to light theme"}
          aria-pressed={light}
          onClick={onTheme}
          className="flex size-7 shrink-0 items-center justify-center self-center rounded-md border border-line-control bg-surface-sunken text-fg-muted transition-colors hover:bg-row-hover hover:text-fg"
        >
          {/* A shape, not a tint: the glyph says which way the switch goes even
              in a screenshot with the colour turned off. */}
          <span aria-hidden="true" className="text-[13px] leading-none">
            {light ? "◐" : "☀"}
          </span>
          <span className="sr-only">{light ? "Dark theme" : "Light theme"}</span>
        </button>

        {/* The clock shares the row: a second 22 px line under a 36 px strip was
            a utility bar wearing header clothes, and it made the header 58 px
            tall for one HH:MM. */}
        <div
          data-testid="nav-utility"
          className="hidden shrink-0 items-center pr-0.5 min-[900px]:flex"
        >
          <Clock />
        </div>
      </div>

      {announcement ? (
        <p role="status" aria-live="polite" data-testid="theme-status" className="sr-only">
          {announcement}
        </p>
      ) : null}
    </header>
  );
}
