import { SearchIcon, XIcon } from "lucide-react";
import * as React from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { searchSymbols, type SymbolHit } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Debounce window. 220 ms is one keystroke-burst at 8–10 keys/s: long enough
 * that "apple" is one request, short enough that the list feels attached to
 * the caret. It is a constant, not a prop, because a second search box with a
 * different cadence would be a second interaction model.
 */
export const SEARCH_DEBOUNCE_MS = 220;

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
const LISTBOX_ID = "symbol-search-listbox";
/** The keys this combobox answers to, said once instead of guessed at. */
const HINT_ID = "symbol-search-hint";

function optionId(index: number): string {
  return `symbol-search-option-${index}`;
}

export interface SymbolSearchProps {
  onSelect?: (hit: SymbolHit) => void;
  className?: string;
  /** Owned by the shell so "/" and ⌘K can focus the one search box. */
  inputRef?: React.RefObject<HTMLInputElement | null>;
}

/**
 * Symbol lookup: debounced, keyboard-navigable, exchange-labelled.
 *
 * The result list is a listbox the input owns (combobox pattern): one tab stop,
 * arrows move `aria-activedescendant`, Enter commits, Escape dismisses without
 * destroying the query. Nothing here fetches on a blank query — the API answers
 * that with 422, and a UI that asks a question it knows is invalid is a bug
 * that looks like a feature.
 */
export function SymbolSearch({ onSelect, className, inputRef }: SymbolSearchProps) {
  const [query, setQuery] = React.useState("");
  const [state, setState] = React.useState<State>(IDLE);
  const [open, setOpen] = React.useState(false);
  const [active, setActive] = React.useState(0);
  const ownRef = React.useRef<HTMLInputElement>(null);
  const boxRef = inputRef ?? ownRef;
  const rootRef = React.useRef<HTMLDivElement>(null);
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
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
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
      searchSymbols(trimmed, controller.signal)
        .then((hits) => {
          if (cancelled) return;
          setState({ phase: "ready", hits, message: null, query: trimmed });
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
  const activeId =
    showList && highlighted >= 0 && state.hits[highlighted]
      ? optionId(highlighted)
      : undefined;
  const statusText =
    state.phase === "loading"
      ? "Searching"
      : state.phase === "error"
        ? "Search unavailable"
        : state.phase === "ready"
          ? `${state.hits.length} ${state.hits.length === 1 ? "result" : "results"}`
          : "";

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
        <SearchIcon aria-hidden="true" className="size-3.5 shrink-0 text-fg-subtle" />
        <input
          ref={boxRef}
          type="text"
          role="combobox"
          aria-label="Search symbols and companies"
          // The listbox is the input's popup; without `aria-haspopup` the
          // relationship was only declared once the list was already open.
          aria-haspopup="listbox"
          aria-describedby={HINT_ID}
          aria-expanded={showList}
          aria-controls={showList ? LISTBOX_ID : undefined}
          aria-autocomplete="list"
          aria-activedescendant={activeId}
          autoComplete="off"
          autoCorrect="off"
          spellCheck={false}
          placeholder="Symbol or company"
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
            <XIcon aria-hidden="true" className="size-3.5" />
          </button>
        ) : (
          <kbd
            aria-hidden="true"
            className="hidden shrink-0 rounded-sm border border-line px-1 font-mono text-label text-fg-subtle sm:block"
          >
            /
          </kbd>
        )}
      </div>

      <p id={HINT_ID} className="sr-only">
        Type a symbol or company name. Use Up and Down to move through the
        results, Enter to open the highlighted one, Escape to close the list.
      </p>
      <p aria-live="polite" className="sr-only">
        {statusText}
      </p>

      {showList ? (
        <div className="absolute inset-x-0 top-[calc(100%+6px)] z-40 overflow-hidden rounded-md border border-line-strong bg-popover shadow-[0_16px_40px_-12px_rgba(0,0,0,0.85)]">
          <ul
            id={LISTBOX_ID}
            role="listbox"
            aria-label="Symbol results"
            className="max-h-[320px] overflow-y-auto py-1"
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
                id={optionId(index)}
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
                  "flex h-7 cursor-pointer items-center gap-3 px-3",
                  index === highlighted ? "bg-row-selected" : "bg-transparent",
                )}
              >
                <span className="w-16 shrink-0 font-mono text-data font-semibold text-fg">
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

          {state.phase === "error" ? (
            <p className="px-3 py-2.5 text-data text-down">{state.message}</p>
          ) : null}

          {state.phase === "ready" && state.hits.length > 0 ? (
            <div className="flex items-center justify-between border-t border-line px-3 py-1 text-label uppercase tracking-[0.08em] text-fg-subtle">
              <span>{state.hits.length} matches</span>
              <span className="font-mono">↑↓ move · ⏎ open · esc close</span>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
