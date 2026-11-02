/**
 * Add to: the ticker search that stands in front of the two lists.
 *
 * The toolbar's star adds the row the reader already has open. When nothing is
 * open it must not guess — "the first row on this page" is not a selection —
 * so choosing Watchlist or Portfolio opens this box instead, and the first
 * thing in it is the same suggestions component the header uses. The whole
 * universe is searchable, not just the twenty rows on screen.
 *
 * A watchlist row is one symbol, so choosing a hit writes it immediately. A
 * portfolio holding is a symbol **and** a quantity and a cost; asking for them
 * here is what keeps a holding from arriving at zero cost and reading as a
 * 100% gain on the Portfolio page.
 */

import * as React from "react";

import { Icon } from "@/components/Icon";
import type { SymbolHit } from "@/lib/api";
import { SymbolSearch } from "@/shell/SymbolSearch";
import { cn } from "@/lib/utils";

import { DialogShell } from "./DialogShell";
import type { AddToList } from "./TableToolbar";

export interface AddToInput {
  symbol: string;
  list: AddToList;
  quantity?: number;
  cost?: number;
}

export interface AddToDialogProps {
  open: boolean;
  onClose: () => void;
  list: AddToList;
  /** A ticker already chosen by the row selection, shown ready to add. */
  initialSymbol?: SymbolHit | null;
  /** Persist the choice; the answer is the sentence the reader sees. */
  onAdd: (input: AddToInput) => { ok: boolean; message: string };
}

const LIST_LABEL: Record<AddToList, string> = {
  watchlist: "Watchlist",
  portfolio: "Portfolio",
};

export function AddToDialog({ open, onClose, list, initialSymbol = null, onAdd }: AddToDialogProps) {
  const [hit, setHit] = React.useState<SymbolHit | null>(initialSymbol);
  const [quantity, setQuantity] = React.useState("1");
  const [cost, setCost] = React.useState("");
  const [result, setResult] = React.useState<{ ok: boolean; message: string } | null>(null);

  React.useEffect(() => {
    if (!open) return;
    setHit(initialSymbol);
    setQuantity("1");
    setCost("");
    setResult(null);
  }, [open, list, initialSymbol]);

  const choose = (next: SymbolHit) => {
    setHit(next);
    setResult(null);
    if (list !== "watchlist") return;
    // One symbol is the whole row: write it now, say so, and stay open so a
    // reader can add a second ticker without reopening the menu.
    setResult(onAdd({ symbol: next.symbol, list }));
  };

  const addHolding = (event: React.FormEvent) => {
    event.preventDefault();
    if (!hit) {
      setResult({ ok: false, message: "Search a ticker first." });
      return;
    }
    const shares = Number(quantity);
    const average = Number(cost);
    if (!Number.isFinite(shares) || shares <= 0) {
      setResult({ ok: false, message: "Quantity must be a number greater than zero." });
      return;
    }
    if (cost.trim().length === 0 || !Number.isFinite(average) || average < 0) {
      setResult({ ok: false, message: "Average cost must be zero or more." });
      return;
    }
    setResult(onAdd({ symbol: hit.symbol, list, quantity: shares, cost: average }));
  };

  return (
    <DialogShell
      open={open}
      onClose={onClose}
      size="sm"
      title={`Add to ${LIST_LABEL[list].toLowerCase()}`}
      description={
        list === "watchlist"
          ? "Search the whole market; a hit is written to this browser's watchlist."
          : "Search the whole market, then enter the position this browser should hold."
      }
    >
      <div className="flex flex-col gap-2">
        <div className="flex flex-col gap-0.5">
          <span className="flex items-center gap-1 font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
            <Icon name="search" />
            Ticker
          </span>
          <SymbolSearch
            label="Search a ticker to add"
            idPrefix="add-to-search"
            placeholder="Search a ticker…"
            showHint={false}
            onSelect={choose}
          />
        </div>

        {hit ? (
          <div className="flex items-center gap-2 rounded-sm border border-line bg-surface-raised px-2 py-1.5">
            <span className="font-mono text-data font-semibold text-fg">{hit.symbol}</span>
            <span className="min-w-0 flex-1 truncate text-data text-fg-muted">{hit.name}</span>
            <span className="font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
              {hit.exchange}
            </span>
          </div>
        ) : (
          <p className="text-data text-fg-subtle">
            Start typing a symbol or a company name — the suggestions cover every listed
            ticker, not only this page.
          </p>
        )}

        {hit && list === "portfolio" ? (
          <form onSubmit={addHolding} className="flex flex-col gap-2">
            <div className="flex items-end gap-2">
              <label className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className="font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
                  Quantity
                </span>
                <input
                  aria-label="Quantity"
                  type="number"
                  inputMode="decimal"
                  min="0"
                  step="any"
                  value={quantity}
                  onChange={(event) => setQuantity(event.target.value)}
                  className="h-7 rounded-sm border border-line-control bg-surface-sunken px-1.5 font-mono text-data text-fg"
                />
              </label>
              <label className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className="font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
                  Average cost
                </span>
                <input
                  aria-label="Average cost"
                  type="number"
                  inputMode="decimal"
                  min="0"
                  step="any"
                  value={cost}
                  placeholder="0.00"
                  onChange={(event) => setCost(event.target.value)}
                  className="h-7 rounded-sm border border-line-control bg-surface-sunken px-1.5 font-mono text-data text-fg"
                />
              </label>
            </div>
            <button
              type="submit"
              className="flex h-7 items-center gap-1.5 self-start rounded-sm border border-line-control bg-surface-raised px-2 text-ui text-fg transition-colors hover:border-brand"
            >
              <Icon name="plus" />
              Add holding
            </button>
          </form>
        ) : null}

        {result ? (
          <p
            role="status"
            aria-live="polite"
            className={cn("text-data", result.ok ? "text-up" : "text-alert")}
          >
            {result.message}
          </p>
        ) : null}

        <div className="mt-1 flex justify-end border-t border-line pt-2">
          <button
            type="button"
            onClick={onClose}
            className="h-7 rounded-sm border border-line-control px-2 text-ui text-fg-muted transition-colors hover:text-fg"
          >
            Done
          </button>
        </div>
      </div>
    </DialogShell>
  );
}
