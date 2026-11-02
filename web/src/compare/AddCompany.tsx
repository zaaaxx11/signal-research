/**
 * "Add company": a search that appends one column.
 *
 * The page is reached from a selection made elsewhere, so the one thing it must
 * not ask for again is to go back and re-select. The control opens the shell's
 * own ticker search (`@/shell/SymbolSearch`) — not a second lookup — and adds
 * the hit to the end of the list. At ten the button is disabled rather than
 * hiding: a control that vanishes at the ceiling teaches the reader that the
 * comparison lost a company.
 *
 * The panel is absolutely placed because it must overhang the table frame; the
 * page header is not a clipping context, and the search's own list is portaled
 * to the body, so neither can be cut off by the table's `overflow-x`.
 */

import * as React from "react";

import { Icon } from "@/components/Icon";
import { SymbolSearch } from "@/shell/SymbolSearch";

import { MAX_COMPARE } from "./symbols";

/** The toolbar's control rhythm: 24 px tall, 10 px uppercase mono, one hairline. */
const CONTROL =
  "flex h-7 items-center gap-1 rounded-sm border border-line-control px-2 font-mono text-label uppercase tracking-[0.08em] text-fg-muted transition-colors hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:text-fg-muted";

export interface AddCompanyProps {
  readonly symbols: readonly string[];
  /** Called with a ticker the search returned; the page owns the URL. */
  readonly onAdd: (symbol: string) => void;
}

export function AddCompany({ symbols, onAdd }: AddCompanyProps) {
  const [open, setOpen] = React.useState(false);
  const full = symbols.length >= MAX_COMPARE;

  // Closing on the ceiling matters when the tenth company arrives from the
  // search itself: the input would otherwise vanish mid-keystroke.
  React.useEffect(() => {
    if (full) setOpen(false);
  }, [full]);

  return (
    <div className="relative flex flex-col items-end gap-1 max-[900px]:items-stretch">
      <button
        type="button"
        data-testid="compare-add"
        disabled={full}
        aria-expanded={open}
        aria-controls={open ? "compare-add-panel" : undefined}
        title={full ? `A comparison holds ${MAX_COMPARE} companies` : "Add a ticker to the comparison"}
        onClick={() => setOpen((value) => !value)}
        className={CONTROL}
      >
        <Icon name="plus" />
        Add company
      </button>

      {full ? (
        <p data-testid="compare-full" className="text-label text-fg-subtle">
          {MAX_COMPARE} of {MAX_COMPARE} — remove one to add another.
        </p>
      ) : null}

      {open && !full ? (
        <div
          id="compare-add-panel"
          data-testid="compare-add-panel"
          className="absolute right-0 top-[calc(100%+2px)] z-30 w-[280px] rounded-md border border-line-strong bg-popover p-2 shadow-[0_16px_40px_-12px_rgba(0,0,0,0.85)] max-[900px]:left-0 max-[900px]:right-auto max-[900px]:w-[calc(100vw-0.75rem)]"
        >
          <SymbolSearch
            label="Search a ticker to add to the comparison"
            idPrefix="compare-add"
            placeholder="Search a ticker…"
            showHint={false}
            onSelect={(hit) => {
              onAdd(hit.symbol);
              setOpen(false);
            }}
          />
        </div>
      ) : null}
    </div>
  );
}
