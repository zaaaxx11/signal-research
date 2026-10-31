/**
 * The symbol detail panel: docked right, resizable, four tabs.
 *
 * ## Docking, not overlaying
 *
 * This component is a **flex sibling**, never `position: fixed` or `absolute:
 * 0` — it takes its width out of the row, so the grid's container-fit solver
 * re-runs and the comparison context stays visible. Drop it into a `flex`
 * container beside the grid and give it the symbol:
 *
 * ```tsx
 * <div className="flex min-h-0 flex-1 gap-2">
 *   <ScreenPanel … />
 *   <DetailPanel symbol={symbol} onClose={() => setSymbol(null)} />
 * </div>
 * ```
 *
 * With `symbol === null` it renders nothing, so the grid gets the full width
 * back without the parent guessing at a placeholder.
 *
 * ## Resizing
 *
 * The separator on the leading edge is a real `role="separator"` with
 * `aria-valuenow`; pointer drag and Arrow/Page/Home/End all clamp to 360–560 px,
 * the same limits the plan's §7 asks for. The width lives here, outside the
 * keyed body, so switching symbols keeps the reader's chosen width while the
 * data tree is replaced whole.
 *
 * ## Keys and focus
 *
 * Escape closes the panel and returns focus to the grid row for the symbol
 * (`tr[data-symbol]`), because focus was on the row before the panel opened and
 * a keyboard user should not be dropped at the top of the document. It is one
 * document-level listener, armed from four origins: inside the panel, on the
 * selected grid row (which closes the panel itself and keeps focus on the row),
 * on the body, and in the shell's symbol search (which handles Escape only
 * while its own result list is open). The panel is a complementary landmark
 * named "Details for AAPL", not a modal: it does not trap focus, and it stands
 * down if a real dialog (the filter drawer) is open — one keystroke closes the
 * dialog, never both.
 */

import * as React from "react";
import { X } from "lucide-react";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { FundamentalsTab } from "./FundamentalsTab";
import { InsidersTab } from "./InsidersTab";
import { NewsTab } from "./NewsTab";
import { OwnershipTab } from "./OwnershipTab";
import { useDetail } from "./useDetail";

export const MIN_PANEL_WIDTH = 360;
export const MAX_PANEL_WIDTH = 560;
export const DEFAULT_PANEL_WIDTH = 420;
export const RESIZE_STEP = 16;
export const RESIZE_PAGE = 64;

export function clampPanelWidth(width: number): number {
  if (!Number.isFinite(width)) return DEFAULT_PANEL_WIDTH;
  return Math.min(MAX_PANEL_WIDTH, Math.max(MIN_PANEL_WIDTH, Math.round(width)));
}

export interface DetailPanelProps {
  /** The row the reader selected; `null` closes the panel. */
  symbol: string | null;
  /** Called by Escape and the close button; the parent owns the selection. */
  onClose: () => void;
}

/** The grid row for a symbol, without building a selector from user data. */
function findGridRow(symbol: string): HTMLElement | null {
  const candidates = document.querySelectorAll<HTMLElement>("[data-symbol][tabindex]");
  for (const candidate of candidates) {
    if (candidate.getAttribute("data-symbol") === symbol) return candidate;
  }
  return null;
}

/** A dialog that is actually on screen — `hidden` ancestors do not count. */
function dialogIsOpen(): boolean {
  const dialogs = document.querySelectorAll<HTMLElement>('[role="dialog"]');
  for (const dialog of dialogs) {
    if (dialog.closest("[hidden]")) continue;
    if (dialog.getAttribute("aria-hidden") === "true") continue;
    if (dialog.getAttribute("data-state") === "closed") continue;
    return true;
  }
  return false;
}

export function DetailPanel({ symbol, onClose }: DetailPanelProps): React.ReactElement | null {
  const [width, setWidth] = React.useState(DEFAULT_PANEL_WIDTH);
  const [dragging, setDragging] = React.useState(false);
  const panelRef = React.useRef<HTMLElement | null>(null);
  const panelId = React.useId();
  const dragRef = React.useRef<{ startX: number; startWidth: number } | null>(null);

  const changeWidth = React.useCallback((next: number) => setWidth(clampPanelWidth(next)), []);

  /**
   * Close, and by default put focus back on the grid row for the symbol.
   *
   * `restoreFocus: false` is for the one origin that is neither the panel nor
   * the row: the shell's search field. A reader who escapes out of the panel
   * while typing a symbol keeps the caret — pulling focus into the grid from a
   * text input is the kind of "help" that loses a half-typed ticker.
   */
  const close = React.useCallback((options?: { restoreFocus?: boolean }) => {
    if (symbol && (options?.restoreFocus ?? true)) {
      const row = findGridRow(symbol);
      row?.focus();
      onClose();
      if (!row && typeof requestAnimationFrame === "function") {
        // The row may not be mounted yet (a page turn is landing); one frame is
        // enough for the parent's state to settle, and only then is there a row
        // to return focus to.
        requestAnimationFrame(() => findGridRow(symbol)?.focus());
      }
      return;
    }
    onClose();
  }, [symbol, onClose]);

  // Escape is document-level because focus may sit on the tab, a metric, or the
  // body. It stays out of the way of a role="dialog" (Radix's filter drawer) and
  // of a focused control outside the panel that owns its own Escape handling.
  React.useEffect(() => {
    if (!symbol) return;
    /**
     * Whether a dialog was on screen when this keystroke *started*.
     *
     * Read in the capture phase, which runs before every bubble listener on
     * document — including the drawer's own. A dialog that closes itself on
     * this Escape is unmounted by the time the bubble handler runs, so asking
     * `dialogIsOpen()` there would answer "no dialog" and the panel would close
     * as a second casualty of one key (adversarial d). The capture snapshot
     * cannot be raced, whatever order the listeners were registered in.
     */
    let dialogOpenAtKeydown: boolean | null = null;
    const onCapture = (event: KeyboardEvent) => {
      if (event.key === "Escape") dialogOpenAtKeydown = dialogIsOpen();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      if (dialogOpenAtKeydown ?? dialogIsOpen()) return;

      const panel = panelRef.current;
      const target = event.target;
      const inside = target instanceof Node && panel !== null && panel.contains(target);
      const active = document.activeElement;
      const activeInside = active instanceof Node && panel !== null && panel.contains(active);
      const idle = active === null || active === document.body;
      const onGridRow = active instanceof HTMLElement && active.matches("tr[data-symbol]");
      // F7: the shell's symbol search is a closeable origin, not a control that
      // owns Escape. It handles the key itself only while its result list is
      // open (it sets `aria-expanded`); with the list closed nothing else would
      // close the panel and the key looked dead.
      const inSearch =
        active instanceof HTMLElement &&
        active.matches('input[role="combobox"]') &&
        active.getAttribute("aria-expanded") !== "true";
      if (!inside && !activeInside && !idle && !onGridRow && !inSearch) return;

      const restoreFocus = inside || activeInside || idle || onGridRow;
      event.preventDefault();
      close({ restoreFocus });
    };
    document.addEventListener("keydown", onCapture, true);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onCapture, true);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [symbol, close]);

  // A drag survives the pointer leaving the 8 px handle: the window owns the
  // move/up pair, which also covers the jsdom test that has no pointer capture.
  React.useEffect(() => {
    if (!dragging) return;
    const onMove = (event: PointerEvent) => {
      const drag = dragRef.current;
      if (!drag) return;
      changeWidth(drag.startWidth + (drag.startX - event.clientX));
    };
    const stop = () => {
      dragRef.current = null;
      setDragging(false);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", stop);
    window.addEventListener("pointercancel", stop);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
    };
  }, [dragging, changeWidth]);

  const onResizeKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    switch (event.key) {
      case "ArrowLeft":
        event.preventDefault();
        changeWidth(width + RESIZE_STEP);
        break;
      case "ArrowRight":
        event.preventDefault();
        changeWidth(width - RESIZE_STEP);
        break;
      case "Home":
        event.preventDefault();
        changeWidth(MIN_PANEL_WIDTH);
        break;
      case "End":
        event.preventDefault();
        changeWidth(MAX_PANEL_WIDTH);
        break;
      case "PageUp":
        event.preventDefault();
        changeWidth(width + RESIZE_PAGE);
        break;
      case "PageDown":
        event.preventDefault();
        changeWidth(width - RESIZE_PAGE);
        break;
      default:
        break;
    }
  };

  const onResizeStart = (event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    dragRef.current = { startX: event.clientX, startWidth: width };
    setDragging(true);
    try {
      event.currentTarget.setPointerCapture?.(event.pointerId);
    } catch {
      // Pointer capture is a convenience; the window listeners are the contract.
    }
  };

  if (!symbol) return null;

  return (
    <aside
      id={panelId}
      ref={panelRef}
      role="complementary"
      aria-label={`Details for ${symbol}`}
      data-testid="detail-panel"
      data-symbol={symbol}
      data-docked="right"
      style={{
        width: `${width}px`,
        minWidth: `${MIN_PANEL_WIDTH}px`,
        maxWidth: `${MAX_PANEL_WIDTH}px`,
      }}
      className="relative flex h-full min-h-0 flex-none flex-col border-l border-line-strong bg-surface"
    >
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize details panel"
        aria-controls={panelId}
        aria-valuemin={MIN_PANEL_WIDTH}
        aria-valuemax={MAX_PANEL_WIDTH}
        aria-valuenow={width}
        aria-valuetext={`${width} pixels`}
        tabIndex={0}
        data-testid="detail-resizer"
        data-dragging={dragging ? "true" : "false"}
        onPointerDown={onResizeStart}
        onKeyDown={onResizeKeyDown}
        className="absolute inset-y-0 left-0 z-10 w-2 cursor-col-resize bg-transparent hover:bg-surface-raised focus-visible:bg-surface-raised"
      />

      <header className="flex h-9 shrink-0 items-center justify-between gap-2 border-b border-line px-3">
        <h2 className="min-w-0 truncate text-ui font-semibold text-fg" title={symbol}>
          {symbol}
        </h2>
        <button
          type="button"
          onClick={() => close()}
          aria-label="Close details"
          title="Close details"
          className="inline-flex size-6 shrink-0 items-center justify-center rounded-sm text-fg-muted hover:bg-row-hover hover:text-fg"
        >
          <X aria-hidden="true" className="size-3.5" />
        </button>
      </header>

      <Tabs defaultValue="fundamentals" className="flex min-h-0 flex-1 flex-col gap-0">
        <div className="shrink-0 border-b border-line px-2 py-1">
          <TabsList variant="line" className="h-8 w-full justify-between gap-0">
            <TabsTrigger value="fundamentals" className="flex-1 px-1.5 text-data">
              Fundamentals
            </TabsTrigger>
            <TabsTrigger value="ownership" className="flex-1 px-1.5 text-data">
              Ownership
            </TabsTrigger>
            <TabsTrigger value="insiders" className="flex-1 px-1.5 text-data">
              Insiders
            </TabsTrigger>
            <TabsTrigger value="news" className="flex-1 px-1.5 text-data">
              News
            </TabsTrigger>
          </TabsList>
        </div>

        {/* Keyed by symbol: a new selection is a new tree, so the old symbol's
            data cannot survive the header change for even one frame. */}
        <DetailBody key={symbol} symbol={symbol} />
      </Tabs>
    </aside>
  );
}

function DetailBody({ symbol }: { symbol: string }): React.ReactElement {
  const detail = useDetail(symbol);

  return (
    <>
      <TabsContent
        value="fundamentals"
        data-testid="tabpanel-fundamentals"
        tabIndex={0}
        className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-3"
      >
        <FundamentalsTab symbol={symbol} state={detail.fundamentals} onRetry={detail.fundamentals.retry} />
      </TabsContent>
      <TabsContent
        value="ownership"
        data-testid="tabpanel-ownership"
        tabIndex={0}
        className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-3"
      >
        <OwnershipTab symbol={symbol} state={detail.ownership} onRetry={detail.ownership.retry} />
      </TabsContent>
      <TabsContent
        value="insiders"
        data-testid="tabpanel-insiders"
        tabIndex={0}
        className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-3"
      >
        <InsidersTab symbol={symbol} state={detail.insiders} onRetry={detail.insiders.retry} />
      </TabsContent>
      <TabsContent
        value="news"
        data-testid="tabpanel-news"
        tabIndex={0}
        className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-3"
      >
        <NewsTab symbol={symbol} state={detail.news} onRetry={detail.news.retry} />
      </TabsContent>
    </>
  );
}

export default DetailPanel;
