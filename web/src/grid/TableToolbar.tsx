/**
 * The toolbar over the table: counts, refresh, the three row actions, export,
 * page size and paging.
 *
 * It is deliberately not a card: no border around the bar, only each control's
 * own 1px edge, and the whole row is centred on the content width. Everything
 * here is a real control — a button does its action, a select shows its value —
 * and the readout on the left is the one thing that is only text.
 *
 * Two popovers, one machine: **Add to** (Watchlist / Portfolio) and **Export**
 * (CSV / XLSX / JSON / API). They share one open state so opening the second
 * closes the first, one outside-click listener and one Escape, and each moves
 * focus to its first item on open and back to its trigger on close. A menu that
 * only works with a pointer is a menu half the readers cannot use.
 *
 * Every control carries a glyph. The rule is the one the owner set: if a
 * first-time reader could not guess what the control does from its word alone,
 * the word is preceded by a recognisable icon. The glyphs are decorative (the
 * words are the names) except on the two paging arrows, which are icon-only
 * and therefore named by `aria-label`.
 */

import * as React from "react";

import { Icon } from "@/components/Icon";
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

/** The two lists a row action can write to. */
export type AddToList = "watchlist" | "portfolio";

/** One control's edge and type, shared so the bar has a single rhythm. */
const CONTROL =
  "flex h-6 items-center gap-1 rounded-sm border border-line-control px-2 font-mono text-label uppercase tracking-[0.08em] text-fg-muted transition-colors hover:text-fg disabled:opacity-40";

const MENU_ITEM =
  "flex w-full items-center gap-1.5 px-2.5 py-1 text-left font-mono text-label uppercase tracking-[0.08em] text-fg-muted transition-colors hover:bg-row-hover hover:text-fg";

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
  /**
   * Add a ticker to one of the two lists. With a row hovered or focused the
   * panel acts on it; with nothing pointed at it opens the ticker search first,
   * so the action never has to guess which symbol was meant.
   */
  onAddTo: (list: AddToList) => void;
  /** The row the pointer or keyboard is on, named in the Add to control's hint. */
  selectedSymbol: string | null;
  onCompare: () => void;
  /** Compare mode is on: the table draws its transient checkbox lane. */
  compareMode?: boolean;
  /** How many symbols are ticked, for the Done control. */
  compareCount?: number;
  /** The ceiling the count is measured against. */
  compareMax?: number;
  /** Confirm the comparison; the panel navigates to `/compare`. */
  onCompareDone?: () => void;
  /** Leave compare mode without navigating. */
  onCompareCancel?: () => void;
  onAlert: () => void;
  onExport: (choice: ExportChoice) => void;
  /** True when the rows on screen came from the tab's page cache. */
  cached?: boolean;
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
  onAddTo,
  selectedSymbol,
  onCompare,
  compareMode = false,
  compareCount = 0,
  compareMax = 10,
  onCompareDone,
  onCompareCancel,
  onAlert,
  onExport,
  cached = false,
  statusText,
  notice,
  className,
}: TableToolbarProps) {
  const [menu, setMenu] = React.useState<"add" | "export" | null>(null);
  const addRef = React.useRef<HTMLDivElement>(null);
  const exportRef = React.useRef<HTMLDivElement>(null);
  const addTrigger = React.useRef<HTMLButtonElement>(null);
  const exportTrigger = React.useRef<HTMLButtonElement>(null);

  React.useEffect(() => {
    if (menu === null) return;
    const root = menu === "add" ? addRef.current : exportRef.current;
    const onPointerDown = (event: PointerEvent) => {
      if (root && !root.contains(event.target as Node)) setMenu(null);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      const trigger = menu === "add" ? addTrigger.current : exportTrigger.current;
      setMenu(null);
      trigger?.focus();
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [menu]);

  // Focus the first item of a freshly opened menu: a keyboard reader should be
  // able to open it with Enter and choose without another Tab.
  React.useEffect(() => {
    if (menu === null) return;
    const root = menu === "add" ? addRef.current : exportRef.current;
    root?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
  }, [menu]);

  const pickAdd = (list: AddToList) => {
    setMenu(null);
    addTrigger.current?.focus();
    onAddTo(list);
  };

  const pickExport = (choice: ExportChoice) => {
    setMenu(null);
    exportTrigger.current?.focus();
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
      <span className="flex shrink-0 items-center gap-1">
        <span
          data-testid="range"
          className="nums font-mono text-data text-fg-muted"
          title="Page position / rows in the filtered screen"
        >
          {countReadout(pageIndex, total)}
        </span>
        {cached ? (
          <span
            data-testid="cache-hint"
            title="These rows were served from this tab's page cache; no request was made."
            className="rounded-sm border border-line px-1 font-mono text-label uppercase tracking-[0.08em] text-fg-subtle"
          >
            cached
          </span>
        ) : null}
      </span>

      <span className="flex items-center gap-1">
        <button
          type="button"
          onClick={onRefresh}
          disabled={refreshing}
          aria-label="Refresh the screen"
          title="Ask the API for this page again"
          className={CONTROL}
        >
          <Icon name="refresh" />
          Refresh
        </button>
        <label className="flex items-center gap-1">
          <span className="sr-only">Auto refresh</span>
          <Icon name="clock" className="text-fg-subtle" />
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

      <div ref={addRef} className="relative">
        <button
          ref={addTrigger}
          type="button"
          data-testid="add-to-button"
          aria-haspopup="menu"
          aria-expanded={menu === "add"}
          onClick={() => setMenu((value) => (value === "add" ? null : "add"))}
          title={
            selectedSymbol
              ? `Add ${selectedSymbol} to a watchlist or a portfolio`
              : "No row hovered or focused — picking a list opens the ticker search first"
          }
          className={CONTROL}
        >
          <Icon name="star" />
          Add to
          <Icon name="chevron-down" size={12} />
        </button>
        {menu === "add" ? (
          <ul
            role="menu"
            aria-label="Add to"
            data-testid="add-to-menu"
            className="absolute left-0 top-[calc(100%+4px)] z-30 min-w-[150px] overflow-hidden rounded-sm border border-line-strong bg-surface-raised py-0.5 shadow-lg"
          >
            <li role="none">
              <button type="button" role="menuitem" onClick={() => pickAdd("watchlist")} className={MENU_ITEM}>
                <Icon name="star" />
                Watchlist
              </button>
            </li>
            <li role="none">
              <button type="button" role="menuitem" onClick={() => pickAdd("portfolio")} className={MENU_ITEM}>
                <Icon name="list" />
                Portfolio
              </button>
            </li>
          </ul>
        ) : null}
      </div>

      <button
        type="button"
        onClick={onCompare}
        aria-label="Compare rows"
        aria-pressed={compareMode}
        title={
          compareMode
            ? `Tick up to ${compareMax} companies, then confirm`
            : `Pick up to ${compareMax} companies to compare side by side`
        }
        className={cn(
          CONTROL,
          compareMode && "border-brand bg-row-selected text-fg",
        )}
      >
        <Icon name="split" />
        Compare
      </button>

      {compareMode ? (
        <span className="flex items-center gap-1" data-testid="compare-mode">
          <span
            data-testid="compare-count"
            className="nums font-mono text-label uppercase tracking-[0.08em] text-fg-muted"
          >
            {compareCount}/{compareMax}
          </span>
          <button
            type="button"
            data-testid="compare-done"
            onClick={onCompareDone}
            disabled={compareCount === 0}
            aria-label={`Confirm comparison of ${compareCount} symbol${compareCount === 1 ? "" : "s"}`}
            title="Open the comparison page with the ticked symbols"
            className={cn(CONTROL, "border-brand text-fg")}
          >
            Done ({compareCount})
          </button>
          <button
            type="button"
            onClick={onCompareCancel}
            aria-label="Cancel comparison"
            title="Leave compare mode without opening the page"
            className={CONTROL}
          >
            <Icon name="x" />
            Cancel
          </button>
        </span>
      ) : null}

      <button
        type="button"
        onClick={onAlert}
        aria-label="Set alert"
        title="Write a price alert for the row under the pointer or keyboard, or search one"
        className={CONTROL}
      >
        <Icon name="bell" />
        Set alert
      </button>

      <div ref={exportRef} className="relative">
        <button
          ref={exportTrigger}
          type="button"
          data-testid="export-button"
          aria-haspopup="menu"
          aria-expanded={menu === "export"}
          onClick={() => setMenu((value) => (value === "export" ? null : "export"))}
          title="Download this screen as CSV, XLSX or JSON, or copy the API body"
          className={CONTROL}
        >
          <Icon name="download" />
          Export
          <Icon name="chevron-down" size={12} />
        </button>
        {menu === "export" ? (
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
                  onClick={() => pickExport(choice)}
                  className={MENU_ITEM}
                >
                  <Icon name={choice === "api" ? "split" : "download"} />
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
        <Icon name="list" className="text-fg-subtle" />
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
          data-testid="toolbar-prev"
          onClick={onPrev}
          disabled={!canPrev}
          aria-label="Previous page"
          title="Previous page"
          className={CONTROL}
        >
          <Icon name="chevron-left" />
          Prev
        </button>
        <button
          type="button"
          data-testid="toolbar-next"
          onClick={onNext}
          disabled={!canNext}
          aria-label="Next page"
          title="Next page"
          className={CONTROL}
        >
          Next
          <Icon name="chevron-right" />
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
