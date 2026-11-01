/**
 * The shell's header: brand, fourteen sections, the symbol search, the day/night
 * switch, and the clock.
 *
 * One row at ≥1024 px, and the numbers are the contract: 36 px tall, 12 px/500
 * labels separated by whitespace rather than rules, the active section marked by
 * a 2 px rose underline, the search a fixed 272 px box, and the nav the only
 * flexible thing between them. The row does not wrap — every label is
 * `whitespace-nowrap`, so a viewport too narrow for the row makes the nav
 * scroll *inside itself* rather than push the document sideways.
 *
 * Below 1024 px the fourteen labels fold behind the `☰` toggle, because a row
 * that would clip nine sections is worse than a menu that says it has fourteen.
 * The two things a reader came for stay on the row at every width: where they
 * are (the brand link) and how to search (the field, which flexes rather than
 * hides). The panel is a real element with the `hidden` attribute — not an
 * unmounted subtree — so "collapsed" is a state a screen reader and the tab
 * order can both observe, and it is absolutely positioned, so opening it moves
 * nothing below it: zero layout shift, one paint.
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

/** The folding panel's id, so `aria-controls` names a real element. */
export const NAV_PANEL_ID = "primary-nav-panel";

export interface HeaderBarProps {
  route: Route;
  /** The current query string; only the News item reads it. */
  search: string;
  onSelect: (hit: SymbolHit) => void;
  inputRef: React.RefObject<HTMLInputElement | null>;
}

const ROW_LINK =
  "flex items-center whitespace-nowrap border-b-2 px-1.5 text-[12px] font-medium leading-none transition-colors hover:bg-row-hover";
const PANEL_LINK =
  "flex items-center whitespace-nowrap border-l-2 px-2.5 py-2 text-[12px] font-medium leading-none transition-colors hover:bg-row-hover";

/**
 * One section link, in either of its two presentations.
 *
 * `row` is the 36 px strip; `panel` is the folding menu. They share the label,
 * the href, the `aria-current` rule and the hover wash; they differ only in
 * which edge carries the 2 px accent, because an underline at the bottom of a
 * grid cell is not an underline.
 */
function SectionLink({
  item,
  route,
  search,
  variant,
  onClick,
}: {
  item: NavItem;
  route: Route;
  search: string;
  variant: "row" | "panel";
  onClick?: () => void;
}) {
  const active = isActive(item, route, search);
  return (
    <Link
      to={item.path}
      aria-current={active ? "page" : undefined}
      data-nav-item={item.label}
      data-active={active ? "true" : "false"}
      onClick={onClick}
      className={cn(
        variant === "row" ? ROW_LINK : PANEL_LINK,
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

  const [open, setOpen] = React.useState(false);
  /**
   * The last theme change, as a sentence. `null` until the first press: the
   * shell has exactly one `role="status"` at rest (the grid's row range), and
   * a second, permanently empty live region in the header would make that
   * count two. The sentence is rendered with the change it describes.
   */
  const [announcement, setAnnouncement] = React.useState<string | null>(null);
  const toggleRef = React.useRef<HTMLButtonElement>(null);

  const closePanel = React.useCallback(() => {
    setOpen(false);
    // Escape is a retreat, not a dismissal: the control that opened the panel
    // is where the reader's hands were, so focus goes back there.
    toggleRef.current?.focus();
  }, []);

  const onPanelKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "Escape") return;
    event.preventDefault();
    event.stopPropagation();
    closePanel();
  };

  const onToggleKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (event.key !== "Escape" || !open) return;
    event.preventDefault();
    closePanel();
  };

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

        {/* The row is the wide presentation; the panel below is the narrow one. */}
        <nav aria-label="Primary" className="hidden min-w-0 flex-1 lg:flex">
          {/*
           * At 1024–1279 px the fourteen labels are wider than the space the
           * search leaves them, so the row scrolls sideways instead of pushing
           * the document. The scrollbar is suppressed on purpose: a 15 px
           * horizontal scrollbar inside a 36 px row would eat the row's own
           * height and re-flow the header. The labels stay 36 px tall, the
           * wheel reaches the tail of the row, and tabbing to a clipped label
           * scrolls it into view.
           */}
          <ul
            data-testid="primary-nav"
            className="flex h-full items-stretch overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
          >
            {NAV_ITEMS.map((item) => (
              <li key={item.label} className="flex">
                <SectionLink item={item} route={route} search={search} variant="row" />
              </li>
            ))}
          </ul>
        </nav>

        {/* The search never folds: it flexes below 1024 and holds 272 px above. */}
        <div className="flex min-w-0 flex-1 items-center lg:w-[272px] lg:flex-none">
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

        <button
          ref={toggleRef}
          type="button"
          data-testid="nav-toggle"
          aria-label="Sections"
          aria-expanded={open}
          aria-controls={NAV_PANEL_ID}
          onClick={() => setOpen((value) => !value)}
          onKeyDown={onToggleKeyDown}
          className="flex size-7 shrink-0 items-center justify-center self-center rounded-md border border-line-control bg-surface-sunken text-fg-muted transition-colors hover:bg-row-hover hover:text-fg lg:hidden"
        >
          <span aria-hidden="true" className="flex flex-col gap-[3px]">
            <span className="block h-px w-3.5 bg-current" />
            <span className="block h-px w-3.5 bg-current" />
            <span className="block h-px w-3.5 bg-current" />
          </span>
          <span className="sr-only">Sections</span>
        </button>
      </div>

      {/*
       * The folding panel. `hidden` when collapsed, so its fourteen links are
       * out of the tab order and out of the accessibility tree — a collapsed
       * menu you can still tab into is a trap. It is absolutely positioned under
       * the header, so opening it cannot move the page: no reflow, no second
       * jump while the browser settles.
       */}
      <div
        id={NAV_PANEL_ID}
        data-testid="nav-panel"
        hidden={!open}
        onKeyDown={onPanelKeyDown}
        className="absolute inset-x-0 top-full z-40 border-b border-line-strong bg-surface-raised shadow-[0_16px_40px_-12px_rgba(0,0,0,0.65)] lg:hidden"
      >
        <nav aria-label="Sections">
          <ul
            data-testid="nav-panel-list"
            className="grid grid-cols-1 gap-x-2 p-2 sm:grid-cols-2"
          >
            {NAV_ITEMS.map((item) => (
              <li key={item.label} className="flex min-w-0">
                <SectionLink
                  item={item}
                  route={route}
                  search={search}
                  variant="panel"
                  onClick={() => setOpen(false)}
                />
              </li>
            ))}
          </ul>
        </nav>
      </div>

      {announcement ? (
        <p role="status" aria-live="polite" data-testid="theme-status" className="sr-only">
          {announcement}
        </p>
      ) : null}

      <div data-testid="nav-utility" className="flex h-[22px] items-center justify-end px-3">
        <Clock />
      </div>
    </header>
  );
}
