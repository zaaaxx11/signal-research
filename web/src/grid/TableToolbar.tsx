/**
 * The toolbar over the table: counts, refresh, the three row actions, export,
 * page size and paging.
 *
 * It is deliberately not a card: no border around the bar, only each control's
 * own 1px edge, and the whole row is centred on the content width. Everything
 * here is a real control — a button does its action, a select shows its value —
 * and the readout on the left is the one thing that is only text.
 *
 * The export menu is the one popover: four `menuitem`s (CSV, XLSX, JSON, API),
 * closed by Escape, by choosing, or by a click outside. It is a menu rather
 * than a select because a select that acts on `change` cannot be re-chosen
 * without changing something else first.
 */

import * as React from "react";

import { cn } from "@/lib/utils";

import type { ExportChoice } from "./ExportDialog";
import { countReadout } from "./pager";

/** The page sizes the table offers, in the order the select lists them. */
export const ROWS_PER_PAGE = [20, 50, 100] as const;

/** The auto-refresh cadences; `0` is off. */
export const AUTO_REFRESH_OPTIONS = [
  { value: 0, label: "Off" },
  { value: 300_000, label: "Every 5 min" },
] as const;

export interface TableToolbarProps {
  pageIndex: number;
  /** The filtered total the API reported. */
  total: number;
  pageCount: number;
  rowsPerPage: number;
  onRowsPerPage: (rows: number) => void;
  onPrev: () => void;
  onNext: () => void;
  canPrev: boolean;
  canNext: boolean;
  onRefresh: () => void;
  refreshing: boolean;
  autoRefreshMs: number | null;
  onAutoRefresh: (ms: number | null) => void;
  onAddToWatchlist: () => void;
  onCompare: () => void;
  onAlert: () => void;
  onExport: (choice: ExportChoice) => void;
  /** The pending sentence shown in the one live region. */
  statusText: string;
  /** A short confirmation from the last action, announced politely. */
  notice: string | null;
  className?: string;
}

export function TableToolbar({
  pageIndex,
  total,
  pageCount,
  rowsPerPage,
  onRowsPerPage,
  onPrev,
  onNext,
  canPrev,
  canNext,
  onRefresh,
  refreshing,
  autoRefreshMs,
  onAutoRefresh,
  onAddToWatchlist,
  onCompare,
  onAlert,
  onExport,
  statusText,
  notice,
  className,
}: TableToolbarProps) {
  const [menuOpen, setMenuOpen] = React.useState(false);
  const menuRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (!menuOpen) return;
    const onPointerDown = (event: PointerEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) setMenuOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [menuOpen]);

  const pick = (choice: ExportChoice) => {
    setMenuOpen(false);
    onExport(choice);
  };

  return (
    <div
      data-testid="table-toolbar"
      className={cn(
        "mx-auto flex w-full max-w-[1680px] flex-wrap items-center justify-center gap-x-2 gap-y-1 px-2 py-1.5",
        className,
      )}
    >
      {/* The count: page position over the filtered total. */}
      <span
        data-testid="range"
        className="nums shrink-0 font-mono text-data text-fg-muted"
        title="Page position / rows in the filtered screen"
      >
        {countReadout(pageIndex, total)}
      </span>

      <span className="flex items-center gap-1">
        <button
          type="button"
          onClick={onRefresh}
          disabled={refreshing}
          aria-label="Refresh the screen"
          title="Ask the API for this page again"
          className="h-6 rounded-sm border border-line-control px-2 font-mono text-label uppercase tracking-[0.08em] text-fg-muted transition-colors hover:text-fg disabled:opacity-40"
        >
          Refresh
        </button>
        <label className="flex items-center gap-1">
          <span className="sr-only">Auto refresh</span>
          <select
            aria-label="Auto refresh"
            data-testid="auto-refresh"
            value={autoRefreshMs ?? 0}
            onChange={(event) => {
              const value = Number(event.target.value);
              onAutoRefresh(value > 0 ? value : null);
            }}
            title="Refresh this page on a timer"
            className="h-6 rounded-sm border border-line-control bg-surface-sunken px-1 font-mono text-label uppercase tracking-[0.08em] text-fg-muted"
          >
            {AUTO_REFRESH_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        {autoRefreshMs !== null ? (
          <span
            data-testid="auto-refresh-live"
            className="flex items-center gap-1 font-mono text-label uppercase tracking-[0.08em] text-up"
            title={`This page is refreshed every ${Math.round(autoRefreshMs / 60_000)} minutes.`}
          >
            <span aria-hidden="true" className="size-1.5 rounded-full bg-up" />
            live
          </span>
        ) : null}
      </span>

      <span className="h-4 w-px bg-line" aria-hidden="true" />

      <button
        type="button"
        onClick={onAddToWatchlist}
        aria-label="Add to watchlist"
        title="Add the selected row, or every row on this page when none is selected"
        className="h-6 rounded-sm border border-line-control px-2 font-mono text-label uppercase tracking-[0.08em] text-fg-muted transition-colors hover:text-fg"
      >
        Add to watchlist
      </button>

      <button
        type="button"
        onClick={onCompare}
        aria-label="Compare rows"
        title="Pick up to five rows from this page and compare them"
        className="h-6 rounded-sm border border-line-control px-2 font-mono text-label uppercase tracking-[0.08em] text-fg-muted transition-colors hover:text-fg"
      >
        Compare
      </button>

      <button
        type="button"
        onClick={onAlert}
        aria-label="Set alert"
        title="Write a price alert for the selected symbol"
        className="h-6 rounded-sm border border-line-control px-2 font-mono text-label uppercase tracking-[0.08em] text-fg-muted transition-colors hover:text-fg"
      >
        Set alert
      </button>

      <div ref={menuRef} className="relative">
        <button
          type="button"
          data-testid="export-button"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((value) => !value)}
          className="h-6 rounded-sm border border-line-control px-2 font-mono text-label uppercase tracking-[0.08em] text-fg-muted transition-colors hover:text-fg"
        >
          Export ▾
        </button>
        {menuOpen ? (
          <ul
            role="menu"
            aria-label="Export"
            data-testid="export-menu"
            className="absolute right-0 top-[calc(100%+4px)] z-30 min-w-[130px] overflow-hidden rounded-sm border border-line-strong bg-surface-raised py-0.5 shadow-lg"
          >
            {(["csv", "xlsx", "json", "api"] as const).map((choice) => (
              <li key={choice} role="none">
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => pick(choice)}
                  className="block w-full px-2.5 py-1 text-left font-mono text-label uppercase tracking-[0.08em] text-fg-muted transition-colors hover:bg-row-hover hover:text-fg"
                >
                  {choice === "api" ? "API" : choice.toUpperCase()}
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </div>

      <span className="h-4 w-px bg-line" aria-hidden="true" />

      <label className="flex items-center gap-1">
        <span className="sr-only">Rows per page</span>
        <select
          aria-label="Rows per page"
          data-testid="rows-per-page"
          value={rowsPerPage}
          onChange={(event) => onRowsPerPage(Number(event.target.value))}
          className="h-6 rounded-sm border border-line-control bg-surface-sunken px-1 font-mono text-label uppercase tracking-[0.08em] text-fg-muted"
        >
          {ROWS_PER_PAGE.map((option) => (
            <option key={option} value={option}>
              {option} / page
            </option>
          ))}
        </select>
      </label>

      <span id="screen-page-readout" className="nums shrink-0 font-mono text-data text-fg-muted">
        {pageIndex + 1}/{pageCount}
      </span>

      <span className="flex items-center gap-1">
        <button
          type="button"
          onClick={onPrev}
          disabled={!canPrev}
          aria-label="Previous page"
          className="h-6 rounded-sm border border-line-control px-2 font-mono text-label uppercase tracking-[0.08em] text-fg-muted transition-colors hover:text-fg disabled:opacity-40"
        >
          Prev
        </button>
        <button
          type="button"
          onClick={onNext}
          disabled={!canNext}
          aria-label="Next page"
          className="h-6 rounded-sm border border-line-control px-2 font-mono text-label uppercase tracking-[0.08em] text-fg-muted transition-colors hover:text-fg disabled:opacity-40"
        >
          Next
        </button>
      </span>

      {/*
        The **one** `role="status"` in the document. It is mounted empty and
        filled by a state change, because a region that appears *with* its
        sentence is not reliably announced; `aria-atomic` re-reads the whole
        sentence rather than a diff of it.
      */}
      <span
        role="status"
        aria-live="polite"
        aria-atomic="true"
        data-testid="fetch-status"
        className="min-w-0 truncate font-mono text-label uppercase tracking-[0.08em] text-accent-text"
      >
        {statusText}
      </span>

      <span aria-live="polite" data-testid="toolbar-notice" className="text-data text-fg-subtle">
        {notice ?? ""}
      </span>
    </div>
  );
}
