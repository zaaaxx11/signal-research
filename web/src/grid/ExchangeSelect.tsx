/**
 * The exchange control: a multi-select menu in the toolbar, and the one chip
 * that says what it did.
 *
 * **Why a listbox and not a `<select>`.** Sector is one of twenty-one names and
 * one choice; exchange is four codes that carry 99.9% of the US universe, and
 * the canonical screen is *"NASDAQ or NYSE"*. With replacement semantics, two
 * `<select>`s could only ever send two `exch eq` rows, which the API ANDs into
 * zero — so the choice has to be a set. `in` is already first-class end to end
 * (op vocabulary, URL codec, chip text, API compile), which makes this a UI
 * cost, not a contract change.
 *
 * **What is printed and what is sent are different things.** The trigger reads
 * `All exchanges`, a venue's name, or `2 exchanges`; the chip reads
 * `Exchange: NASDAQ, NYSE`. The callbacks only ever receive codes (`XNAS`), and
 * every label goes through `lib/exchanges.ts` — the same leaf the grid cell and
 * the detail panel use, so the screen spells a venue one way.
 *
 * **A link may name a venue the list no longer has.** That code is offered
 * anyway — prepended, checked and marked `data-extra` — because a filter the
 * reader cannot see is a filter they cannot remove. It is still sent: the
 * control never rewrites the predicate it was handed.
 *
 * Keyboard: one tab stop (the trigger). The menu is a `role="listbox"`
 * `aria-multiselectable="true"` with `tabIndex={-1}`, focused on open and
 * navigated with `aria-activedescendant` — Space toggles and stays open (you are
 * building a set), Enter toggles and closes, Escape closes and returns focus,
 * Tab leaves, letters type ahead over the labels.
 *
 * Geometry: the menu is `position: fixed`, anchored to the trigger's rect and
 * clamped to the viewport. `absolute` would be clipped by the panel's
 * `overflow-hidden` (`ScreenPanel`), and a menu cut off at the toolbar's edge is
 * a menu that cannot show its last option.
 */

import * as React from "react";

import { exchangeLabel, exchangeTitle, sortExchanges } from "@/lib/exchanges";

import { cleanExchangeCode, type VenueBucket } from "./exchanges";
import { formatNumber } from "./format";

/** Which population the offered venues came from; printed beside the control. */
export type ExchangeSource = "market" | "rows" | "loading" | "none";

export interface ExchangeSelectProps {
  /** The market's venues (or, when the route is missing, the page's). */
  readonly venues: readonly VenueBucket[];
  readonly source: ExchangeSource;
  /** Chosen codes, never labels. `[]` is "All exchanges". */
  readonly value: readonly string[];
  readonly onChange: (codes: string[]) => void;
}

export const ALL_EXCHANGES = "All exchanges";

/** The menu's box, in px. Fixed: a menu that resizes with its widest item
 * jumps as breadth lands, and a 2 000-row count is four digits wide. */
const MENU_WIDTH = 264;
const MENU_MAX_HEIGHT = 288;
const ROW_HEIGHT = 26;
const MARGIN = 8;

/** What the source sentence says; the menu's tooltip reuses it, so the two
 * can never describe different populations. */
const SOURCE_TEXT: Record<ExchangeSource, string> = {
  market: "from /market/exchanges",
  rows: "from rows on screen",
  loading: "exchange list loading",
  none: "no exchange list",
};

interface MenuOption {
  readonly code: string;
  readonly label: string;
  /** `null` when the population has no count for this code (an extra). */
  readonly count: number | null;
  /** Chosen, but absent from the offered population. */
  readonly extra: boolean;
}

/**
 * The menu's rows: extras first, then the offered venues in canonical order.
 *
 * Extras go first on purpose — they are the only rows that are invisible
 * without help, and a link's code at the bottom of a 17-row menu is a code the
 * reader will not notice is on.
 */
export function exchangeMenuOptions(
  venues: readonly VenueBucket[],
  value: readonly string[],
): MenuOption[] {
  const counts = new Map<string, number>();
  for (const venue of venues) {
    const code = cleanExchangeCode(venue.code);
    if (code === null) continue;
    // A duplicate code is one venue; the larger count wins, so a repeated
    // bucket cannot inflate the menu.
    counts.set(code, Math.max(counts.get(code) ?? 0, venue.count || 0));
  }

  const chosen = sortExchanges(value);
  return [
    ...chosen
      .filter((code) => !counts.has(code))
      .map((code) => ({ code, label: exchangeLabel(code), count: null, extra: true })),
    ...sortExchanges([...counts.keys()]).map((code) => ({
      code,
      label: exchangeLabel(code),
      count: counts.get(code) ?? 0,
      extra: false,
    })),
  ];
}

function clamp(value: number, low: number, high: number): number {
  return Math.min(Math.max(value, low), Math.max(low, high));
}

export function ExchangeSelect({ venues, source, value, onChange }: ExchangeSelectProps) {
  const options = React.useMemo(() => exchangeMenuOptions(venues, value), [venues, value]);
  const selected = React.useMemo(() => sortExchanges(value), [value]);
  const selectedSet = React.useMemo(() => new Set(selected), [selected]);

  const [open, setOpen] = React.useState(false);
  const [active, setActive] = React.useState(0);
  const [position, setPosition] = React.useState({ top: MARGIN, left: MARGIN });
  const triggerRef = React.useRef<HTMLButtonElement>(null);
  const menuRef = React.useRef<HTMLDivElement>(null);
  /** Type-ahead: the letters typed in the last half second. */
  const typed = React.useRef({ text: "", at: 0 });

  const baseId = React.useId();
  const optionId = (index: number) => `${baseId}-${index}`;

  const activeIndex = options.length === 0 ? -1 : Math.min(active, options.length - 1);
  const count = selected.length;
  const triggerText =
    count === 0 ? ALL_EXCHANGES : count === 1 ? exchangeLabel(selected[0] as string) : `${count} exchanges`;

  const anchor = () => {
    const rect = triggerRef.current?.getBoundingClientRect();
    const height = Math.min(MENU_MAX_HEIGHT, options.length * ROW_HEIGHT + MARGIN);
    // Below the trigger, then pulled back inside the viewport: a menu anchored
    // to a control near the bottom edge would otherwise open past it.
    return {
      top: clamp((rect?.bottom ?? 0) + 4, MARGIN, window.innerHeight - height - MARGIN),
      left: clamp(rect?.left ?? MARGIN, MARGIN, window.innerWidth - MENU_WIDTH - MARGIN),
    };
  };

  const openMenu = () => {
    const firstChecked = options.findIndex((option) => selectedSet.has(option.code));
    setActive(firstChecked >= 0 ? firstChecked : 0);
    setPosition(anchor());
    setOpen(true);
  };

  const closeMenu = (restoreFocus: boolean) => {
    if (restoreFocus) triggerRef.current?.focus();
    setOpen(false);
  };

  const toggle = (code: string) => {
    onChange(selectedSet.has(code) ? selected.filter((item) => item !== code) : [...selected, code]);
  };

  // The menu owns the focus while it is open — the trigger keeps `aria-expanded`
  // and the menu keeps `aria-activedescendant`, so the reader never loses the
  // sentence that names the control.
  React.useEffect(() => {
    if (open) menuRef.current?.focus({ preventScroll: true });
  }, [open]);

  // A menu that survives a scroll, a resize or a click elsewhere would be
  // anchored to a rect that moved. `pointerdown` rather than `click` so the
  // dismissal lands before the thing underneath it activates.
  React.useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node | null;
      if (!target) return;
      if (menuRef.current?.contains(target) || triggerRef.current?.contains(target)) return;
      setOpen(false);
    };
    const dismiss = () => setOpen(false);

    document.addEventListener("pointerdown", onPointerDown, true);
    window.addEventListener("resize", dismiss);
    window.addEventListener("scroll", dismiss, true);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      window.removeEventListener("resize", dismiss);
      window.removeEventListener("scroll", dismiss, true);
    };
  }, [open]);

  const onTriggerKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    switch (event.key) {
      case "ArrowDown":
      case "ArrowUp":
      case "Enter":
      case " ":
        // The button would also fire a click for Enter/Space; the menu is
        // opened once, here, and the synthetic click is suppressed.
        event.preventDefault();
        if (open) closeMenu(false);
        else openMenu();
        break;
      case "Escape":
        if (open) {
          event.preventDefault();
          closeMenu(false);
        }
        break;
      default:
        break;
    }
  };

  const moveTo = (index: number) => {
    if (options.length > 0) setActive((index + options.length) % options.length);
  };

  const typeAhead = (key: string) => {
    const now = Date.now();
    const buffer = typed.current;
    const text = now - buffer.at > 500 ? key : buffer.text + key;
    typed.current = { text, at: now };
    const needle = text.toLowerCase();
    // Start one past the active row so repeating a letter cycles through the
    // labels that share it, which is what the pattern promises.
    for (let step = 1; step <= options.length; step += 1) {
      const index = (activeIndex + step + options.length) % options.length;
      const option = options[index];
      if (!option) continue;
      if (option.label.toLowerCase().startsWith(needle) || option.code.toLowerCase().startsWith(needle)) {
        setActive(index);
        return;
      }
    }
  };

  const onMenuKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        moveTo(activeIndex + 1);
        break;
      case "ArrowUp":
        event.preventDefault();
        moveTo(activeIndex - 1);
        break;
      case " ":
      case "Enter":
        event.preventDefault();
        if (options[activeIndex]) toggle((options[activeIndex] as MenuOption).code);
        // Space builds the set and stays; Enter commits and closes.
        if (event.key === "Enter") closeMenu(true);
        break;
      case "Escape":
        event.preventDefault();
        closeMenu(true);
        break;
      case "Tab":
        // Focus goes back to the trigger *before* the browser's default moves
        // it on, so the tab continues from the control instead of from a node
        // that is about to be unmounted.
        triggerRef.current?.focus();
        setOpen(false);
        break;
      default:
        if (event.key.length === 1 && !event.metaKey && !event.ctrlKey && !event.altKey) {
          typeAhead(event.key);
        }
        break;
    }
  };

  return (
    <span className="flex min-w-0 items-center gap-1">
      <span className="flex items-center gap-1 font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
        exchange
        <button
          ref={triggerRef}
          type="button"
          data-testid="exchange-trigger"
          aria-haspopup="listbox"
          aria-expanded={open}
          aria-controls={open ? `${baseId}-menu` : undefined}
          aria-label={`Exchange: ${triggerText}`}
          title={count === 0 ? "Any exchange" : selected.map((code) => exchangeTitle(code)).join(", ")}
          onClick={() => (open ? closeMenu(false) : openMenu())}
          onKeyDown={onTriggerKeyDown}
          className="flex max-w-[9rem] items-center truncate rounded-sm border border-line bg-surface-sunken px-1 py-0.5 font-mono text-label tracking-[0.06em] text-fg-muted"
        >
          <span data-testid="exchange-trigger-label" className="min-w-0 truncate">
            {triggerText}
          </span>
          <span aria-hidden="true" className="shrink-0 pl-1 text-fg-subtle">
            ▾
          </span>
        </button>
      </span>

      <span
        data-testid="exchange-source"
        data-source={source}
        title={`${options.length} exchange codes, ${SOURCE_TEXT[source]}.`}
        className="hidden shrink-0 font-mono text-label tracking-[0.04em] text-fg-subtle xl:inline"
      >
        {SOURCE_TEXT[source]}
      </span>

      {open ? (
        <div
          ref={menuRef}
          id={`${baseId}-menu`}
          role="listbox"
          aria-multiselectable="true"
          aria-label="Exchanges"
          tabIndex={-1}
          aria-activedescendant={activeIndex >= 0 ? optionId(activeIndex) : undefined}
          data-testid="exchange-menu"
          onKeyDown={onMenuKeyDown}
          style={{
            position: "fixed",
            top: position.top,
            left: position.left,
            width: MENU_WIDTH,
            maxHeight: MENU_MAX_HEIGHT,
          }}
          className="z-50 overflow-auto rounded-sm border border-line bg-surface-raised py-1 shadow-lg outline-none"
        >
          {options.length === 0 ? (
            <div data-testid="exchange-empty" className="px-2 py-1 text-data text-fg-subtle">
              {SOURCE_TEXT[source]}
            </div>
          ) : (
            options.map((option, index) => {
              const checked = selectedSet.has(option.code);
              return (
                <div
                  key={option.code}
                  id={optionId(index)}
                  role="option"
                  aria-selected={checked}
                  data-extra={option.extra ? "true" : undefined}
                  data-code={option.code}
                  title={
                    option.extra
                      ? `Not in the current exchange list; the link still sends ${option.code}.`
                      : undefined
                  }
                  // Keep the focus in the listbox: a pointerdown that moved it
                  // to the option would drop `aria-activedescendant` before the
                  // click lands.
                  onPointerDown={(event) => event.preventDefault()}
                  onClick={() => toggle(option.code)}
                  className={`flex cursor-pointer items-center gap-1.5 px-2 py-1 text-data hover:bg-row-hover ${
                    index === activeIndex ? "bg-row-hover text-fg" : "text-fg-muted"
                  }`}
                >
                  <span aria-hidden="true" className="w-3 shrink-0 text-accent-text">
                    {checked && "✓"}
                  </span>
                  <span className="min-w-0 flex-1 truncate">{option.label}</span>
                  {option.count === null ? null : (
                    <span
                      title={source === "rows" ? "rows on this page" : "rows in the last scan"}
                      className="shrink-0 text-label text-fg-subtle"
                    >
                      {formatNumber(option.count, 0)}
                    </span>
                  )}
                  <span className="shrink-0 font-mono text-label tracking-[0.06em] text-fg-subtle">
                    {option.code}
                  </span>
                </div>
              );
            })
          )}
        </div>
      ) : null}
    </span>
  );
}

export interface ExchangeChipProps {
  readonly codes: readonly string[];
  readonly onClear: () => void;
}

/**
 * The chosen set, removable, as **one** chip for **one** predicate.
 *
 * Two chips would suggest two filters, and the request carries one `in`. The
 * label is the name; the code stays in the title, so "which venues is this
 * sending" is answerable from the chip without opening anything.
 *
 * It is drawn in the toolbar's shared chip bar (`ScreenPanel`), never in
 * `ChipBar` — that bar draws the predicates the controls do not own, and the
 * same filter printed twice reads as two filters.
 */
export function ExchangeChip({ codes, onClear }: ExchangeChipProps) {
  const labels = codes.map((code) => exchangeLabel(code)).join(", ");

  return (
    <span
      data-testid="exchange-chip"
      className="inline-flex h-6 items-center gap-1 rounded-sm border border-line-strong bg-surface-raised pl-1.5 text-data text-fg"
    >
      <span className="text-fg-subtle">Exchange:</span>{" "}
      <span
        data-testid="exchange-chip-name"
        className="max-w-[18rem] truncate text-fg-muted"
        title={codes.map((code) => exchangeTitle(code)).join(", ")}
      >
        {labels}
      </span>
      <button
        type="button"
        aria-label={`Clear exchange filter: ${labels}`}
        onClick={onClear}
        className="h-5 w-5 rounded-sm text-fg-muted transition-colors hover:bg-row-hover hover:text-fg"
      >
        ×
      </button>
    </span>
  );
}
