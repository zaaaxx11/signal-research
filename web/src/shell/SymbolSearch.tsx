import * as React from "react";
import { createPortal } from "react-dom";

import { Icon } from "@/components/Icon";
import { Skeleton } from "@/components/ui/skeleton";
import { searchSymbols, type SymbolHit } from "@/lib/api";
import { rankSuggestions } from "@/shell/symbolRank";
import { cn } from "@/lib/utils";

/**
 * Debounce window. 220 ms is one keystroke-burst at 8–10 keys/s: long enough
 * that "apple" is one request, short enough that the list feels attached to
 * the caret. It is a constant, not a prop, because a second search box with a
 * different cadence would be a second interaction model.
 */
export const SEARCH_DEBOUNCE_MS = 220;

/**
 * How many rows one lookup asks for.
 *
 * The endpoint clamps `limit` at `SEARCH_LIMIT_MAX = 20` (`app/api/discovery.py`),
 * so asking for 100 would be a request for a page the API will never send; the
 * number here is the API's own ceiling. The panel renders all of it and scrolls,
 * rather than showing the first eight and letting the reader believe that is
 * the whole universe.
 */
export const SEARCH_LIMIT = 20;

/** How many rows PageDown/PageUp move: one panel-full at 28 px rows. */
const PAGE_ROWS = 8;

type Phase = "idle" | "loading" | "ready" | "error";

interface State {
  phase: Phase;
  hits: SymbolHit[];
  message: string | null;
  /**
   * The trimmed query these hits answer. The list outlives the text that
   * produced it (the debounce window, a slow response), so "is this list the
   * current question?" is a property of the data, not of `phase`.
   */
  query: string | null;
}

const IDLE: State = { phase: "idle", hits: [], message: null, query: null };

function optionId(prefix: string, index: number): string {
  return `${prefix}-option-${index}`;
}

export interface SymbolSearchProps {
  onSelect?: (hit: SymbolHit) => void;
  className?: string;
  /** Owned by the shell so "/" and ⌘K can focus the one search box. */
  inputRef?: React.RefObject<HTMLInputElement | null>;
  /**
   * The field's hint text. The default is the shell's original wording; the
   * nav passes the product's wider phrasing because it is the front door and
   * the search also opens a company or a profile page.
   */
  placeholder?: string;
  /** The decorative "/" key hint; the nav hides it to keep the field wide. */
  showHint?: boolean;
  /**
   * The box's accessible name.
   *
   * Two search boxes can be on screen at once — the nav's and the screener
   * header's ticker lookup — and a shared name would make "combobox" ambiguous
   * to a screen reader and to a test. Each instance names itself.
   */
  label?: string;
  /**
   * The prefix of this instance's element ids.
   *
   * `aria-controls` and `aria-activedescendant` point at ids; two instances
   * with the same ids describe the wrong list. The default keeps the nav's
   * historical spelling so a link, a test or a screen reader sees no change.
   */
  idPrefix?: string;
}

/**
 * Symbol lookup: debounced, keyboard-navigable, exchange-labelled.
 *
 * The result list is a listbox the input owns (combobox pattern): one tab stop,
 * arrows move `aria-activedescendant`, Enter commits, Escape dismisses without
 * destroying the query. Nothing here fetches on a blank query — the API answers
 * that with 422, and a UI that asks a question it knows is invalid is a bug
 * that looks like a feature.
 *
 * **Geometry: the panel leaves this subtree.** An `absolute` panel is only as
 * good as its nearest clipping ancestor, and the screener header lives inside
 * panes that clip and stack (the table frame is `overflow-hidden`, the filter
 * disclosure is a grid animation). A dropdown anchored to a box inside those
 * was cut off at the header's bottom edge — the rows existed, the reader could
 * not see them. So the panel is a portal (on `document.body`, or on the owning
 * `[role="dialog"]` when the field lives in a modal) with `position: fixed`,
 * placed from the input's `getBoundingClientRect()`: full
 * width of the field, six pixels under it, 320 px of its own scroll (~10 rows
 * at 28 px), and flipped above the field when the viewport has no room below.
 * Re-measured on resize and on scroll (capture, so the table's own scroller
 * counts) because a fixed box does not travel with its anchor.
 */

/** The panel's own box, in px. Fixed numbers, like the exchange menu's. */
const PANEL_GAP = 6;
const PANEL_MARGIN = 8;
const PANEL_MAX_HEIGHT = 320;
/** Below this much room the panel prefers the space above the field. */
const PANEL_MIN_HEIGHT = 180;
/** Used only when the anchor reports no width (a test DOM, a hidden shell). */
const PANEL_FALLBACK_WIDTH = 240;

interface Placement {
  left: number;
  width: number;
  /** Set when the panel hangs below the field… */
  top: number | null;
  /** …and this when there was no room below and it hangs above it. */
  bottom: number | null;
  maxHeight: number;
  above: boolean;
}

function samePlacement(a: Placement | null, b: Placement): boolean {
  return (
    a !== null &&
    a.left === b.left &&
    a.width === b.width &&
    a.top === b.top &&
    a.bottom === b.bottom &&
    a.maxHeight === b.maxHeight &&
    a.above === b.above
  );
}
export function SymbolSearch({
  onSelect,
  className,
  inputRef,
  placeholder = "Symbol or company",
  showHint = true,
  label = "Search symbols and companies",
  idPrefix = "symbol-search",
}: SymbolSearchProps) {
  const listboxId = `${idPrefix}-listbox`;
  const hintId = `${idPrefix}-hint`;
  const [query, setQuery] = React.useState("");
  const [state, setState] = React.useState<State>(IDLE);
  const [open, setOpen] = React.useState(false);
  const [active, setActive] = React.useState(0);
  const ownRef = React.useRef<HTMLInputElement>(null);
  const boxRef = inputRef ?? ownRef;
  const rootRef = React.useRef<HTMLDivElement>(null);
  /** The portaled panel; the outside-click test must forgive a pointer inside it. */
  const panelRef = React.useRef<HTMLDivElement>(null);
  /** Each option's node, so the highlight can be scrolled into the panel. */
  const optionRefs = React.useRef<Array<HTMLLIElement | null>>([]);
  const [placement, setPlacement] = React.useState<Placement | null>(null);
  /**
   * The symbol the reader just committed, if the box still holds it.
   *
   * `commit` writes the symbol into the box, which is also the search text. The
   * debounced effect cannot tell "the reader typed AAPL" from "we put AAPL
   * there" — so without this, picking a hit re-asks the API for the symbol it
   * just opened and drops a fresh list back over the panel 220 ms later. A
   * keyboard user commits with Enter, sees the list close, and then watches it
   * reopen on top of the thing they chose.
   *
   * Any keystroke clears it (`onChange`), so typing the same symbol by hand
   * still searches.
   */
  const committedRef = React.useRef<string | null>(null);
  const trimmed = query.trim();

  // A combobox that stays open after a click elsewhere is a modal that forgot
  // it was modal. Closing on pointerdown outside is the least surprising rule:
  // it does not depend on focus order, and it does not fire for the click that
  // selects an option (which is inside the root).
  React.useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node | null;
      if (!target) return;
      // The panel is portaled out of `rootRef`, so "inside the widget" is two
      // nodes now: the field and the list. Without the panel here, the
      // pointerdown that starts a click on an option would tear the list down
      // before the mousedown could commit it — click-to-choose would be dead.
      if (rootRef.current?.contains(target) || panelRef.current?.contains(target)) return;
      setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  React.useEffect(() => {
    if (trimmed.length === 0) {
      setState(IDLE);
      setOpen(false);
      return;
    }

    // We put this text in the box; it is an answer, not a new question.
    if (committedRef.current !== null && trimmed === committedRef.current) {
      setState(IDLE);
      setOpen(false);
      return;
    }

    const controller = new AbortController();
    let cancelled = false;

    const timer = window.setTimeout(() => {
      setState((previous) => ({ ...previous, phase: "loading" }));
      searchSymbols(trimmed, controller.signal, SEARCH_LIMIT)
        .then((hits) => {
          if (cancelled) return;
          // The server ranks; this pass puts a symbol that merely *contains*
          // the letters below every symbol that starts with them, which is how
          // a suggestion list is read. Same rows, no second request.
          setState({ phase: "ready", hits: rankSuggestions(hits, trimmed), message: null, query: trimmed });
          setActive(0);
          setOpen(true);
        })
        .catch((error: unknown) => {
          if (cancelled || controller.signal.aborted) return;
          setState({
            phase: "error",
            hits: [],
            message: error instanceof Error ? error.message : "Search failed",
            query: trimmed,
          });
          setOpen(true);
        });
    }, SEARCH_DEBOUNCE_MS);

    return () => {
      cancelled = true;
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [trimmed]);

  const commit = React.useCallback(
    (hit: SymbolHit) => {
      committedRef.current = hit.symbol;
      setQuery(hit.symbol);
      setOpen(false);
      onSelect?.(hit);
      boxRef.current?.focus();
    },
    [onSelect],
  );

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    const count = state.hits.length;
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        if (!open && count > 0) {
          setOpen(true);
          setActive(0);
        } else if (count > 0) {
          setActive((index) => Math.min(index + 1, count - 1));
        }
        break;
      case "ArrowUp":
        event.preventDefault();
        setActive((index) => Math.max(index - 1, 0));
        break;
      case "Home":
        if (open) {
          event.preventDefault();
          setActive(0);
        }
        break;
      case "PageDown":
        if (open && count > 0) {
          event.preventDefault();
          setActive((index) => Math.min(index + PAGE_ROWS, count - 1));
        }
        break;
      case "PageUp":
        if (open && count > 0) {
          event.preventDefault();
          setActive((index) => Math.max(index - PAGE_ROWS, 0));
        }
        break;
      case "End":
        if (open) {
          event.preventDefault();
          setActive(Math.max(count - 1, 0));
        }
        break;
      case "Enter": {
        // Three conditions, all load-bearing: the list is open, it answers the
        // text in the box, and it finished loading. Before the fix only `open`
        // was checked, so Enter during the debounce window committed the
        // previous query's first hit.
        if (!open || !fresh || state.phase !== "ready") break;
        const hit = state.hits[active];
        if (!hit) break;
        event.preventDefault();
        commit(hit);
        break;
      }
      case "Escape":
        if (open) {
          event.preventDefault();
          // The list is the innermost open thing; a modal that closed with it
          // would take the field and the typed query away on the same key. The
          // dialog's own Escape handler is an ancestor listener, so the key
          // must stop here.
          event.stopPropagation();
          setOpen(false);
        }
        break;
      default:
        break;
    }
  };

  const clear = () => {
    committedRef.current = null;
    setQuery("");
    setState(IDLE);
    setOpen(false);
    boxRef.current?.focus();
  };

  const showList = open && state.phase !== "idle";

  /**
   * Where the panel sits, read from the field's own box.
   *
   * The panel used to be `absolute inset-x-0 top-full` inside the field, which
   * is only correct while every ancestor lets it through — the screener's
   * header does not. A fixed box is measured instead: the field's width, six
   * pixels below its bottom edge, clamped inside the viewport, and moved above
   * the field when below it there is less room than a usable list.
   */
  const measure = React.useCallback(() => {
    const anchor = rootRef.current;
    if (!anchor || typeof window === "undefined") return;
    const rect = anchor.getBoundingClientRect();
    const viewportWidth = window.innerWidth || PANEL_FALLBACK_WIDTH + PANEL_MARGIN * 2;
    const viewportHeight = window.innerHeight;
    const width = Math.min(
      Math.round(rect.width) || PANEL_FALLBACK_WIDTH,
      Math.max(PANEL_FALLBACK_WIDTH, viewportWidth - PANEL_MARGIN * 2),
    );
    const left = Math.min(
      Math.max(Math.round(rect.left), PANEL_MARGIN),
      Math.max(PANEL_MARGIN, viewportWidth - width - PANEL_MARGIN),
    );
    const below = viewportHeight - rect.bottom - PANEL_GAP - PANEL_MARGIN;
    const above = rect.top - PANEL_GAP - PANEL_MARGIN;
    // Flip only when the space above is genuinely the better half; a field in
    // the middle of a tall window keeps its list below it.
    const flipped = below < PANEL_MIN_HEIGHT && above > below;
    const room = Math.max(96, flipped ? above : below);
    const next: Placement = {
      left,
      width,
      top: flipped ? null : Math.round(rect.bottom) + PANEL_GAP,
      bottom: flipped ? Math.round(viewportHeight - rect.top) + PANEL_GAP : null,
      maxHeight: Math.min(PANEL_MAX_HEIGHT, room),
      above: flipped,
    };
    setPlacement((previous) => (samePlacement(previous, next) ? previous : next));
  }, []);

  React.useLayoutEffect(() => {
    if (!showList) return;
    // Before paint: the panel must never flash at the top-left corner.
    measure();
    const onScroll = (event: Event) => {
      const target = event.target;
      // The list's own scroll is not the page moving; re-anchoring on it would
      // re-render the panel on every wheel tick for no visual change.
      if (target instanceof Node && panelRef.current?.contains(target)) return;
      measure();
    };
    window.addEventListener("resize", measure);
    // Capture, so the table's scroller counts even though `scroll` does not
    // bubble: a fixed box does not travel with the field on its own.
    window.addEventListener("scroll", onScroll, true);
    return () => {
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", onScroll, true);
    };
  }, [showList, measure]);

  /**
   * Whether the rendered list belongs to the text currently in the box. A
   * keystroke changes `trimmed` before the debounce fires, so for one window
   * the list is a true answer to a question the user has already withdrawn.
   * Nothing may be committed from it — silently selecting APP while the box
   * reads "zzzz" is the failure this flag exists to prevent.
   */
  const fresh = state.query !== null && state.query === trimmed;
  /**
   * Only a fresh list may show a selection: a highlighted row is a promise
   * that Enter will commit it, and while the text is stale that promise is
   * false. `active` still tracks the pointer/arrow position underneath, so
   * the highlight reappears on the row the user was already on when the new
   * results land.
   */
  const highlighted = fresh && state.phase === "ready" ? active : -1;

  // Keep the highlight inside the panel the way a table keeps the selected row
  // on screen: `nearest` scrolls only when the row left the box, so arrowing
  // down ten of twenty rows moves the list once instead of re-centring it.
  React.useEffect(() => {
    if (highlighted < 0) return;
    optionRefs.current[highlighted]?.scrollIntoView?.({ block: "nearest" });
  }, [highlighted]);

  const activeId =
    showList && highlighted >= 0 && state.hits[highlighted]
      ? optionId(idPrefix, highlighted)
      : undefined;
  const statusText =
    state.phase === "loading"
      ? "Searching"
      : state.phase === "error"
        ? "Search unavailable"
        : state.phase === "ready"
          ? `${state.hits.length} ${state.hits.length === 1 ? "result" : "results"}`
          : "";

  /**
   * Where the panel is portaled.
   *
   * On the page, `document.body`: nothing above the field may clip it. Inside a
   * modal, the dialog element itself — a list outside `[role="dialog"]` would be
   * outside the modal's focus scope (and DialogShell's Tab trap) even though it
   * is on screen. The dialog is a plain fixed overlay with no transform, so the
   * panel's `position: fixed` still escapes its `overflow-hidden`, and the list
   * stays queryable as part of the dialog.
   */
  const portalHost =
    !showList || typeof document === "undefined"
      ? null
      : (rootRef.current?.closest<HTMLElement>('[role="dialog"]') ?? document.body);

  return (
    <div ref={rootRef} className={cn("relative w-full", className)}>
      <div
        className={cn(
          "search-shell flex h-8 items-center gap-2 rounded-md border border-line-control bg-surface-sunken px-2.5 transition-colors",
          // The input inside suppresses its own outline (a 1 px ring around an
          // inner field reads as a box inside a box); the shell carries the
          // focus indicator instead, drawn outside the border so it never
          // shifts layout.
          "focus-within:border-focus focus-within:outline-2 focus-within:outline-offset-1 focus-within:outline-focus",
          showList && "border-focus",
        )}
      >
        <Icon name="search" className="text-fg-subtle" />
        <input
          ref={boxRef}
          type="text"
          role="combobox"
          aria-label={label}
          // The listbox is the input's popup; without `aria-haspopup` the
          // relationship was only declared once the list was already open.
          aria-haspopup="listbox"
          aria-describedby={hintId}
          aria-expanded={showList}
          aria-controls={showList ? listboxId : undefined}
          aria-autocomplete="list"
          aria-activedescendant={activeId}
          autoComplete="off"
          autoCorrect="off"
          spellCheck={false}
          placeholder={placeholder}
          value={query}
          onChange={(event) => {
            committedRef.current = null;
            setQuery(event.target.value);
          }}
          onKeyDown={onKeyDown}
          className="h-full min-w-0 flex-1 bg-transparent text-ui text-fg placeholder:text-fg-subtle focus-visible:outline-none"
        />
        {query.length > 0 ? (
          <button
            type="button"
            aria-label="Clear search"
            onClick={clear}
            className="rounded-sm p-0.5 text-fg-subtle transition-colors hover:text-fg"
          >
            <Icon name="x" />
          </button>
        ) : showHint ? (
          <kbd
            aria-hidden="true"
            className="hidden shrink-0 rounded-sm border border-line px-1 font-mono text-label text-fg-subtle sm:block"
          >
            /
          </kbd>
        ) : null}
      </div>

      <p id={hintId} className="sr-only">
        Type a symbol or company name. Use Up and Down to move through the
        results, Enter to open the highlighted one, Escape to close the list.
      </p>
      <p aria-live="polite" className="sr-only">
        {statusText}
      </p>

      {portalHost !== null && showList
        ? createPortal(
            <div
              ref={panelRef}
              data-testid="symbol-search-panel"
              data-placement={placement?.above ? "above" : "below"}
              style={{
                left: placement?.left ?? PANEL_MARGIN,
                width: placement?.width ?? undefined,
                top: placement?.top ?? undefined,
                bottom: placement?.bottom ?? undefined,
                maxHeight: placement?.maxHeight ?? PANEL_MAX_HEIGHT,
              }}
              className="fixed z-50 flex max-w-[calc(100vw-1rem)] flex-col overflow-hidden rounded-md border border-line-strong bg-popover shadow-[0_16px_40px_-12px_rgba(0,0,0,0.85)]"
            >
              <ul
                id={listboxId}
                role="listbox"
                aria-label="Symbol results"
                // One row per line, always: `flex-col` and `w-full` are stated
                // here rather than inherited, because an ancestor that laid
                // this list out as a row is precisely the bug this fixes.
                className="flex max-h-[320px] min-h-0 w-full flex-col overflow-y-auto overscroll-contain py-1 [scrollbar-color:var(--border-strong)_transparent] [scrollbar-width:thin] [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-line-strong [&::-webkit-scrollbar-track]:bg-transparent [&::-webkit-scrollbar]:w-2"
              >
                {state.phase === "loading"
                  ? [0, 1, 2].map((row) => (
                      <li key={row} className="px-3 py-1.5" aria-hidden="true">
                        <Skeleton className="h-[17px] w-full opacity-40" />
                      </li>
                    ))
                  : null}

                {state.hits.map((hit, index) => (
                  <li
                    key={hit.symbol}
                    ref={(node) => {
                      optionRefs.current[index] = node;
                    }}
                    id={optionId(idPrefix, index)}
                    role="option"
                    aria-selected={index === highlighted}
                    onMouseEnter={() => setActive(index)}
                    onMouseDown={(event) => {
                      // Keep focus in the input: a click must not blur before click.
                      // A pointer click is an explicit choice of the row under the
                      // cursor, so it may commit a list that Enter would refuse as
                      // stale: the user is pointing at the symbol they can see.
                      event.preventDefault();
                      commit(hit);
                    }}
                    className={cn(
                      "flex h-7 w-full cursor-pointer items-center gap-3 whitespace-nowrap px-3 text-left",
                      index === highlighted ? "bg-row-selected" : "bg-transparent",
                    )}
                  >
                    <span className="w-16 shrink-0 truncate font-mono text-data font-semibold text-fg">
                      {hit.symbol}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-data text-fg-muted">
                      {hit.name}
                    </span>
                    <span className="shrink-0 font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
                      {hit.exchange}
                    </span>
                  </li>
                ))}
              </ul>

              {state.phase === "ready" && state.hits.length === 0 ? (
                <p className="px-3 py-2.5 text-data text-fg-muted">
                  No symbol or company matches “{trimmed}”.
                </p>
              ) : null}

              {/* A full page is a page, not the universe. The endpoint returns
                  rows without a total, so this says "first N" and how to narrow
                  it rather than inventing a count of what it did not send. */}
              {state.phase === "ready" && state.hits.length >= SEARCH_LIMIT ? (
                <p
                  data-testid="symbol-search-more"
                  className="shrink-0 border-t border-line bg-surface-sunken px-3 py-1 text-label text-fg-subtle"
                >
                  First {state.hits.length} matches — keep typing to narrow the list.
                </p>
              ) : null}

              {state.phase === "error" ? (
                <p className="px-3 py-2.5 text-data text-down">{state.message}</p>
              ) : null}

              {state.phase === "ready" && state.hits.length > 0 ? (
                <div className="flex shrink-0 items-center justify-between border-t border-line px-3 py-1 text-label uppercase tracking-[0.08em] text-fg-subtle">
                  <span>↑↓ move · pgup/pgdn</span>
                  <span className="font-mono">⏎ open · esc close</span>
                </div>
              ) : null}
            </div>,
            portalHost,
          )
        : null}
    </div>
  );
}
