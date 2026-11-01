/**
 * The pager under the table: the page numbers and a jump.
 *
 * Prev/Next live in the toolbar above, once: this bar is the map, not a second
 * pair of arrows with the same name. The numbers are a window, not a list —
 * `1 … 4 5 6 … 281` is the whole of a 281-page screen, and the same function is
 * tested on its own in `pager.test.ts`. A number is a button whose `aria-current`
 * says where you are; the jump field takes a page number and clamps it to what
 * exists, so a typed 999 lands on the last page instead of a dead request.
 */

import * as React from "react";

import { cn } from "@/lib/utils";

import { clampPage, pageItems } from "./pager";

export interface PagerProps {
  pageIndex: number;
  pageCount: number;
  onPage: (page: number) => void;
  /** True while a request is out; the controls wait rather than queue. */
  busy: boolean;
}

export function Pager({ pageIndex, pageCount, onPage, busy }: PagerProps) {
  const [draft, setDraft] = React.useState("");
  const current = Math.min(pageIndex + 1, pageCount);
  const go = (page: number) => {
    const next = clampPage(page, pageCount);
    if (next !== current) onPage(next - 1);
  };

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const page = Number(draft);
    if (!Number.isFinite(page) || page <= 0) return;
    go(page);
    setDraft("");
  };

  return (
    <nav
      data-testid="pager"
      aria-label="Screen pages"
      className="mx-auto flex w-full max-w-[1680px] flex-wrap items-center justify-center gap-1 px-3 py-1.5"
    >
      <ul className="flex flex-wrap items-center gap-0.5">
        {pageItems(current, pageCount).map((item, index) =>
          item === "gap" ? (
            <li key={`gap-${index}`} aria-hidden="true" className="px-1 font-mono text-data text-fg-subtle">
              …
            </li>
          ) : (
            <li key={item}>
              <button
                type="button"
                onClick={() => go(item)}
                disabled={busy}
                aria-label={`Page ${item}${item === current ? ", current page" : ""}`}
                aria-current={item === current ? "page" : undefined}
                data-testid={`page-${item}`}
                className={cn(
                  "h-6 min-w-6 rounded-sm border px-1.5 font-mono text-data transition-colors disabled:opacity-40",
                  item === current
                    ? "border-brand bg-row-selected text-fg"
                    : "border-transparent text-fg-muted hover:border-line-control hover:text-fg",
                )}
              >
                {item}
              </button>
            </li>
          ),
        )}
      </ul>

      <form onSubmit={submit} className="flex items-center gap-1">
        <label className="flex items-center gap-1 font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
          Go to
          <input
            aria-label="Go to page"
            data-testid="go-to-page"
            inputMode="numeric"
            value={draft}
            onChange={(event) => setDraft(event.target.value.replace(/[^0-9]/g, "").slice(0, 7))}
            placeholder={String(current)}
            className="h-6 w-12 rounded-sm border border-line-control bg-surface-sunken px-1 text-center font-mono text-data text-fg"
          />
        </label>
        <button
          type="submit"
          disabled={busy || draft.length === 0}
          className="h-6 rounded-sm border border-line-control px-1.5 font-mono text-label uppercase tracking-[0.08em] text-fg-muted transition-colors hover:text-fg disabled:opacity-40"
        >
          Go
        </button>
      </form>
    </nav>
  );
}
