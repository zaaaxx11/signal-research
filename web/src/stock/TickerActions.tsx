/**
 * The ticker band's actions: Compare, Add to, Set alert.
 *
 * A screener row is anonymous until the reader picks it, so every one of those
 * flows starts with a search box. A ticker page is the opposite: the symbol is
 * the address. So all three dialogs open already seeded with it, and the only
 * work left is naming the *other* symbols (compare), the quantity and cost
 * (portfolio) or the condition (alert).
 *
 * The flows themselves are the screener's own components — `CompareDialog`,
 * `AddToDialog`, `AlertDialog` — not copies: the five-symbol ceiling, the
 * holdings validator and the alert key cannot drift between two surfaces.
 * The storage writes go through the same `addSymbol`/`addHolding` validators
 * the toolbar uses, for the same reason: a holding invented at cost 0 would
 * read as a 100% gain.
 *
 * One group, one rhythm: three 24 px controls, each an icon plus a word. Below
 * 900 px the words go and `aria-label` carries the name, exactly as the row
 * actions and the snapshot grid collapse at that breakpoint.
 *
 * The star and the bell are not decoration: each mirrors a store. The star is
 * filled when the symbol is on either list, and its label says which one; the
 * bell is filled and counted when the symbol has a saved alert. Both subscribe
 * here, so a write from this page's dialog or from another page flips them at
 * once — and the words, not the ink, are what a screen reader hears.
 */

import * as React from "react";

import { Icon } from "@/components/Icon";
import type { ScreenRow } from "@/data/screens";
import { AddToDialog, type AddToInput } from "@/grid/AddToDialog";
import { AlertDialog } from "@/grid/AlertDialog";
import { alertsStore, countAlerts } from "@/grid/alerts";
import { CompareDialog } from "@/grid/CompareDialog";
import type { AddToList } from "@/grid/TableToolbar";
import type { SymbolHit } from "@/lib/api";
import { addHolding, addSymbol, MAX_SYMBOLS, portfolioStore, watchlistStore } from "@/lists/storage";

/**
 * The rows one ticker-page comparison prints.
 *
 * Identity first — a symbol that is not on this page still answers these three
 * — then the quote and the ownership ratios: the numbers a reader comparing two
 * companies actually holds in their head. The grid's own view columns are the
 * server's list; this page has one instrument, not a screen, so the order is
 * chosen here.
 */
export const TICKER_COMPARE_COLUMNS = [
  "symbol",
  "company",
  "price",
  "change",
  "market_cap",
  "volume",
  "prev_close",
  "short_ratio",
  "insider_own",
  "inst_own",
  "short_float",
] as const;

/** One control's edge and type, matching the screener toolbar's rhythm. */
const CONTROL =
  "flex h-6 items-center gap-1 rounded-sm border border-line-control px-2 font-mono text-label uppercase tracking-[0.08em] text-fg-muted transition-colors hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus";

const MENU_ITEM =
  "flex w-full items-center gap-1.5 px-2.5 py-1 text-left font-mono text-label uppercase tracking-[0.08em] text-fg-muted transition-colors hover:bg-row-hover hover:text-fg focus-visible:bg-row-hover focus-visible:text-fg focus-visible:outline-none";

export interface TickerActionsProps {
  /** The instrument the page is showing; every flow opens on it. */
  readonly symbol: string;
  /** Its company name, for the add dialog's identity row. */
  readonly name: string | null;
  /** Its exchange, for the same row. */
  readonly exchange: string | null;
  /**
   * Its scan row, when the selection route has answered.
   *
   * Null is a real state — a slow read, or a deployment without the route —
   * and it is not a reason to disable the actions: the seeded symbol still
   * heads a column and its identity still comes from the ticker. The row is
   * read when Compare opens, so one that arrives later is used by the next
   * comparison rather than re-seeded into the current one.
   */
  readonly row: ScreenRow | null;
  /** The band's one confirmation line; null clears it. */
  readonly onNotice: (notice: TickerNotice | null) => void;
}

/**
 * A written row's answer, or a validator's refusal.
 *
 * The tone travels with the sentence: "AAPL is on the watchlist." and "AAPL is
 * already on this list." must not look the same.
 */
export interface TickerNotice {
  readonly message: string;
  readonly ok: boolean;
}

export function TickerActions({
  symbol,
  name,
  exchange,
  row,
  onNotice,
}: TickerActionsProps): React.ReactElement {
  const [comparing, setComparing] = React.useState(false);
  const [menuOpen, setMenuOpen] = React.useState(false);
  const [addList, setAddList] = React.useState<AddToList | null>(null);
  const [alerting, setAlerting] = React.useState(false);
  /**
   * The row Compare was opened with, frozen at the click.
   *
   * The dialog's seeding effect keys on the rows' identity, so a read that
   * lands while the comparison is open would re-seed the list and erase the
   * symbols the reader has just added. Snapshotting once means a late row is
   * picked up by the next comparison instead of quietly undoing this one.
   */
  const [seedRow, setSeedRow] = React.useState<ScreenRow | null>(null);

  // The header star is the same fact as a row's star: storage is the truth.
  // A write from the row, this page, the watchlist page or another tab fills
  // it; removing the symbol empties it again.
  const watchlist = React.useSyncExternalStore(
    watchlistStore.subscribe,
    watchlistStore.read,
    watchlistStore.read,
  );
  const holdings = React.useSyncExternalStore(
    portfolioStore.subscribe,
    portfolioStore.read,
    portfolioStore.read,
  );
  // The bell's fact: an alert for this symbol, from the same store the dialog
  // writes. Saving flips it on the next render; deleting the last one flips it
  // back. No local "has alert" flag can drift from what is stored.
  const alerts = React.useSyncExternalStore(
    alertsStore.subscribe,
    alertsStore.read,
    alertsStore.read,
  );

  const inWatchlist = watchlist.includes(symbol);
  const inPortfolio = holdings.some((holding) => holding.symbol === symbol);
  const starred = inWatchlist || inPortfolio;
  /** Which list fills the star — the label says it, not just the fill. */
  const starState = inWatchlist
    ? inPortfolio
      ? "in watchlist and portfolio"
      : "in watchlist"
    : inPortfolio
      ? "in portfolio"
      : null;
  const starLabel =
    starState === null ? `Add to list: ${symbol}` : `Add to list: ${symbol} (${starState})`;
  const starTitle =
    starState === null
      ? `Add ${symbol} to the watchlist or the portfolio`
      : inWatchlist && inPortfolio
        ? `${symbol} is in the watchlist and the portfolio`
        : inWatchlist
          ? `${symbol} is in the watchlist`
          : `${symbol} is in the portfolio`;

  const activeAlerts = React.useMemo(() => countAlerts(symbol, alerts), [symbol, alerts]);
  const bellLabel =
    activeAlerts === 0
      ? `Set alert for ${symbol}`
      : `Set alert for ${symbol} (${activeAlerts} active)`;
  const bellTitle =
    activeAlerts === 0
      ? `Write a price alert for ${symbol}`
      : `${activeAlerts} alert${activeAlerts === 1 ? "" : "s"} for ${symbol} — review or add another`;

  const addTrigger = React.useRef<HTMLButtonElement>(null);
  const menu = React.useRef<HTMLDivElement>(null);

  // Both props are read by the dialog's seeding effect, which keys on identity:
  // a fresh array on every render would re-seed for the same reason.
  const initialSymbols = React.useMemo<readonly string[]>(() => [symbol], [symbol]);
  const rows = React.useMemo<readonly ScreenRow[]>(
    () => (seedRow === null ? [] : [seedRow]),
    [seedRow],
  );
  const seed = React.useMemo<SymbolHit>(
    () => ({ symbol, name: name ?? "", exchange: exchange ?? "" }),
    [symbol, name, exchange],
  );

  /**
   * The two list writes, validated by the same functions the toolbar calls.
   *
   * The answer is the sentence the reader sees: a success, or the validator's
   * own reason (already on the list, the list is full, a quantity of zero).
   */
  const write = React.useCallback((input: AddToInput) => {
    if (input.list === "watchlist") {
      const edit = addSymbol(watchlistStore.read(), input.symbol, MAX_SYMBOLS);
      if (!edit.ok) return { ok: false, message: edit.error };
      watchlistStore.write(edit.symbols);
      return { ok: true, message: `${input.symbol} is on the watchlist.` };
    }
    const edit = addHolding(portfolioStore.read(), {
      symbol: input.symbol,
      quantity: input.quantity === undefined ? "" : String(input.quantity),
      cost: input.cost === undefined ? "" : String(input.cost),
    });
    if (!edit.ok) return { ok: false, message: edit.error };
    portfolioStore.write(edit.holdings);
    return { ok: true, message: `${input.symbol} is in the portfolio.` };
  }, []);

  const pickList = (list: AddToList) => {
    setMenuOpen(false);
    // The menu item that was clicked is about to unmount, so hand focus back to
    // the trigger now: the dialog captures its opener on mount and restores to
    // it on close, and a removed node cannot be restored to.
    addTrigger.current?.focus();
    onNotice(null);
    if (list === "watchlist") {
      // One symbol is the whole row: write it, say so, no dialog to dismiss.
      onNotice(write({ symbol, list }));
      return;
    }
    setAddList(list);
  };

  /**
   * Menu mechanics: focus the first item, close on Escape (focus back to the
   * trigger) and on a pointer landing anywhere else. A menu that only answers
   * a mouse is half a menu.
   */
  React.useEffect(() => {
    if (!menuOpen) return;
    const items = () =>
      Array.from(menu.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []);
    items()[0]?.focus();

    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (menu.current?.contains(target) || addTrigger.current?.contains(target)) return;
      setMenuOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setMenuOpen(false);
      addTrigger.current?.focus();
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [menuOpen]);

  const onMenuKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Tab") {
      // The menu is not a trap: leaving it is one keypress, and it closes
      // behind the reader rather than hovering over the page.
      setMenuOpen(false);
      return;
    }
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const items = Array.from(
      menu.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [],
    );
    if (items.length === 0) return;
    const current = items.indexOf(document.activeElement as HTMLElement);
    const step = event.key === "ArrowDown" ? 1 : -1;
    items[(current + step + items.length) % items.length]?.focus();
  };

  return (
    <>
      <div data-testid="ticker-actions" className="flex items-center gap-1.5">
        <button
          type="button"
          data-testid="ticker-compare"
          data-symbol={symbol}
          aria-label={`Compare ${symbol}`}
          aria-haspopup="dialog"
          title={`Compare ${symbol} with up to four other tickers`}
          onClick={() => {
            onNotice(null);
            setSeedRow(row);
            setComparing(true);
          }}
          className={CONTROL}
        >
          <Icon name="split" />
          <span className="max-[900px]:hidden">Compare</span>
        </button>

        <div className="relative">
          <button
            ref={addTrigger}
            type="button"
            data-testid="ticker-add"
            data-starred={starred ? "true" : "false"}
            aria-label={starLabel}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            title={starTitle}
            onClick={() => setMenuOpen((open) => !open)}
            className={CONTROL}
          >
            <Icon
              name="star"
              className={starred ? "fill-current text-alert" : undefined}
              data-starred={starred ? "true" : "false"}
            />
            <span className="max-[900px]:hidden">Add to</span>
          </button>

          {menuOpen ? (
            <div
              ref={menu}
              role="menu"
              aria-label={`Add to a list: ${symbol}`}
              onKeyDown={onMenuKeyDown}
              className="absolute left-0 top-full z-50 mt-1 flex w-40 max-w-[calc(100vw-2rem)] flex-col gap-0.5 rounded-sm border border-line-strong bg-surface p-0.5 shadow-lg"
            >
              <button
                type="button"
                role="menuitem"
                onClick={() => pickList("watchlist")}
                className={MENU_ITEM}
              >
                <Icon name="star" />
                Watchlist
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={() => pickList("portfolio")}
                className={MENU_ITEM}
              >
                <Icon name="list" />
                Portfolio
              </button>
            </div>
          ) : null}
        </div>

        <button
          type="button"
          data-testid="ticker-alert"
          data-alerted={activeAlerts > 0 ? "true" : "false"}
          data-alert-count={activeAlerts}
          aria-label={bellLabel}
          aria-haspopup="dialog"
          title={bellTitle}
          onClick={() => {
            onNotice(null);
            setAlerting(true);
          }}
          className={CONTROL}
        >
          <Icon
            name="bell"
            className={activeAlerts > 0 ? "fill-current text-accent-text" : undefined}
          />
          <span className="max-[900px]:hidden">Set alert</span>
        </button>
      </div>

      <CompareDialog
        open={comparing}
        onClose={() => setComparing(false)}
        rows={rows}
        columns={TICKER_COMPARE_COLUMNS}
        viewLabel={symbol}
        initialSymbols={initialSymbols}
      />

      {addList === null ? null : (
        <AddToDialog
          open
          list={addList}
          initialSymbol={seed}
          onClose={() => setAddList(null)}
          onAdd={write}
        />
      )}

      {alerting ? <AlertDialog open onClose={() => setAlerting(false)} symbol={symbol} /> : null}
    </>
  );
}
