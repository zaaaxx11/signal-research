/**
 * `/portfolio`: holdings the reader typed, priced against the same quote call
 * the watchlist uses.
 *
 * The arithmetic lives in `pnl.ts` so it can be tested without a DOM; this file
 * is the part that decides what a missing number *looks* like. Three rules:
 *
 * * **A row with no quote says "Unavailable" and its market value and P&L are em
 *   das.** The cost basis still prints — it is what the reader paid, not a market
 *   fact — but it is in neither side of the total.
 * * **The total sums only priced rows and says how many.** "2 of 3 priced" is
 *   part of the total, not a footnote, and a line below names the holdings that
 *   are outside it. A missing price counted as zero would turn a gain into a
 *   loss.
 * * **A row is refused, not repaired.** A quantity of 0 or −2, a cost that is not
 *   a number, a second row for a symbol already held: each is an error next to
 *   the field, and nothing is written. Refusing at the input is the only honest
 *   place — a stored row would be hidden by the table and still count.
 *
 * The whole list is this browser's (there is no account in this product), which
 * the line under the title says.
 */

import * as React from "react";

import { LogoMark } from "@/grid/LogoMark";
import { Link } from "@/shell/routes";

import {
  LastPrice,
  Money,
  PnlMoney,
  PnlPercent,
  Quantity,
  QuotesFailure,
  quoteCellState,
  type QuoteCellState,
} from "./cells";
import { LocalNotice } from "./LocalNotice";
import { priceHoldings, totalOf } from "./pnl";
import { addHolding, portfolioStore, removeHolding, type HoldingInput } from "./storage";
import { useLocalValue } from "./useLocalValue";
import { useQuotes } from "./useQuotes";

type Field = "symbol" | "quantity" | "cost";

interface FormError {
  readonly field: Field | "duplicate";
  readonly message: string;
}

const EMPTY_FORM: HoldingInput = { symbol: "", quantity: "", cost: "" };

function FieldInput({
  id,
  label,
  value,
  invalid,
  inputMode,
  placeholder,
  width,
  onChange,
}: {
  readonly id: string;
  readonly label: string;
  readonly value: string;
  readonly invalid: boolean;
  readonly inputMode?: "text" | "decimal";
  readonly placeholder: string;
  readonly width: string;
  readonly onChange: (next: string) => void;
}) {
  return (
    <div className={width}>
      <label
        htmlFor={id}
        className="block text-label uppercase tracking-[0.06em] text-fg-subtle"
      >
        {label}
      </label>
      <input
        id={id}
        name={id}
        value={value}
        inputMode={inputMode}
        placeholder={placeholder}
        autoComplete="off"
        spellCheck={false}
        aria-invalid={invalid}
        onChange={(event) => onChange(event.target.value)}
        className={`focus-ring mt-0.5 h-7 w-full rounded-sm border bg-surface px-2 font-mono text-data text-fg placeholder:text-fg-subtle ${
          invalid ? "border-alert" : "border-line-control"
        }`}
      />
    </div>
  );
}

export function PortfolioPage() {
  const holdings = useLocalValue(portfolioStore);
  const symbols = React.useMemo(() => holdings.map((holding) => holding.symbol), [holdings]);
  const quotes = useQuotes(symbols);

  const rows = React.useMemo(
    () => priceHoldings(holdings, quotes.report?.bySymbol ?? null),
    [holdings, quotes.report],
  );
  const total = React.useMemo(() => totalOf(rows), [rows]);

  const [form, setForm] = React.useState<HoldingInput>(EMPTY_FORM);
  const [error, setError] = React.useState<FormError | null>(null);

  const submit = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const result = addHolding(holdings, form);
    if (!result.ok) {
      setError({ field: result.field, message: result.error });
      return;
    }
    portfolioStore.write(result.holdings);
    setForm(EMPTY_FORM);
    setError(null);
  };

  const remove = (symbol: string): void => {
    portfolioStore.write(removeHolding(holdings, symbol));
  };

  /**
   * The total's own state. While the request is in flight the total is not zero —
   * it is unknown — and once it lands with nothing priced it is still unknown, not
   * $0.00.
   */
  const totalState: QuoteCellState =
    quotes.status === "error"
      ? "failed"
      : quotes.status !== "ready"
        ? "loading"
        : total.covered === 0
          ? "missing"
          : "priced";

  const unpriced = total.total - total.covered;
  const totalLabel =
    totalState === "loading"
      ? "Total"
      : totalState === "failed"
        ? "Total — quotes did not load"
        : total.covered === 0
          ? "Total — no holding has a quote"
          : `Total — ${total.covered} of ${total.total} priced`;

  return (
    <main data-testid="portfolio-page" className="min-h-0 flex-1 overflow-y-auto bg-bg px-2 py-2">
      <div className="flex min-w-0 items-baseline justify-between gap-3 px-0.5">
        <h1 className="text-ui font-medium text-fg">Portfolio</h1>
        <p
          data-testid="portfolio-count"
          className="font-mono text-label uppercase tracking-[0.08em] text-fg-subtle"
        >
          {holdings.length === 0
            ? "Empty"
            : `${holdings.length} holding${holdings.length === 1 ? "" : "s"}`}
        </p>
      </div>

      <LocalNotice />

      <form onSubmit={submit} noValidate className="mt-2 flex flex-wrap items-end gap-1.5">
        <FieldInput
          id="portfolio-symbol"
          label="Symbol"
          value={form.symbol}
          invalid={error?.field === "symbol" || error?.field === "duplicate"}
          placeholder="AAPL"
          width="w-36"
          onChange={(next) => {
            setForm((previous) => ({ ...previous, symbol: next }));
            setError(null);
          }}
        />
        <FieldInput
          id="portfolio-quantity"
          label="Quantity"
          value={form.quantity}
          invalid={error?.field === "quantity"}
          inputMode="decimal"
          placeholder="10"
          width="w-28"
          onChange={(next) => {
            setForm((previous) => ({ ...previous, quantity: next }));
            setError(null);
          }}
        />
        <FieldInput
          id="portfolio-cost"
          label="Average cost"
          value={form.cost}
          invalid={error?.field === "cost"}
          inputMode="decimal"
          placeholder="100.00"
          width="w-32"
          onChange={(next) => {
            setForm((previous) => ({ ...previous, cost: next }));
            setError(null);
          }}
        />
        <button
          type="submit"
          className="focus-ring h-7 rounded-sm border border-line px-2.5 text-data text-fg-muted transition-colors hover:border-line-strong hover:text-fg"
        >
          Add
        </button>
      </form>

      {error !== null ? (
        <p role="alert" data-testid="portfolio-error" className="mt-1 text-data text-alert">
          {error.message}
        </p>
      ) : null}

      {holdings.length === 0 ? (
        <div
          data-testid="portfolio-empty"
          className="mt-2 rounded-md border border-line bg-surface px-3 py-3"
        >
          <p className="text-data text-fg">No holdings yet.</p>
          <p className="mt-0.5 text-data text-fg-muted">
            Add a ticker, how many shares, and what you paid per share. Market value and P&amp;L
            appear once a quote arrives. Nothing here leaves this browser.
          </p>
        </div>
      ) : (
        <>
          {quotes.status === "error" ? (
            <QuotesFailure error={quotes.error} onRetry={quotes.retry} />
          ) : null}

          <div className="mt-2 overflow-x-auto rounded-md border border-line bg-surface">
            <table
              data-testid="portfolio-table"
              className="w-full min-w-[760px] table-fixed border-collapse text-data"
            >
              <caption className="sr-only">
                Holdings you entered in this browser. Market value, cost basis and P&amp;L are
                computed from the latest quote.
              </caption>
              <colgroup>
                <col className="w-[24%]" />
                <col className="w-[8%]" />
                <col className="w-[10%]" />
                <col className="w-[10%]" />
                <col className="w-[13%]" />
                <col className="w-[12%]" />
                <col className="w-[11%]" />
                <col className="w-[8%]" />
                <col className="w-[4%]" />
              </colgroup>
              <thead>
                <tr className="h-[26px] border-b border-line-strong text-label uppercase tracking-[0.05em] text-fg-subtle">
                  <th scope="col" className="px-2 text-left font-medium">
                    Symbol
                  </th>
                  <th scope="col" className="px-2 text-right font-medium">
                    Qty
                  </th>
                  <th scope="col" className="px-2 text-right font-medium">
                    Avg cost
                  </th>
                  <th scope="col" className="px-2 text-right font-medium">
                    Last
                  </th>
                  <th scope="col" className="px-2 text-right font-medium">
                    Market value
                  </th>
                  <th scope="col" className="px-2 text-right font-medium">
                    Cost basis
                  </th>
                  <th scope="col" className="px-2 text-right font-medium">
                    P&amp;L
                  </th>
                  <th scope="col" className="px-2 text-right font-medium">
                    P&amp;L %
                  </th>
                  <th scope="col" className="px-2 text-right font-medium">
                    <span className="sr-only">Remove</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const symbol = row.holding.symbol;
                  const state = quoteCellState(quotes.status, row.quote);
                  return (
                    <tr
                      key={symbol}
                      data-testid={`portfolio-row-${symbol}`}
                      data-symbol={symbol}
                      data-missing={state === "missing" ? "true" : "false"}
                      className="h-6 border-b border-line/60 odd:bg-row-alt hover:bg-row-hover"
                    >
                      <td className="min-w-0 px-2">
                        <span className="flex min-w-0 items-center gap-1.5">
                          <LogoMark symbol={symbol} size={16} />
                          <Link
                            to={`/stock/${symbol}`}
                            className="font-mono text-data font-semibold text-fg hover:text-accent-text"
                          >
                            {symbol}
                          </Link>
                          {row.quote?.name ? (
                            <span className="truncate text-fg-subtle">{row.quote.name}</span>
                          ) : null}
                        </span>
                      </td>
                      <td className="px-2 text-right">
                        <Quantity value={row.holding.quantity} />
                      </td>
                      <td className="px-2 text-right">
                        <Money value={row.holding.cost} />
                      </td>
                      <td data-testid={`portfolio-last-${symbol}`} className="px-2 text-right">
                        <LastPrice state={state} quote={row.quote} />
                      </td>
                      <td data-testid={`portfolio-value-${symbol}`} className="px-2 text-right">
                        <Money state={state} value={row.marketValue} />
                      </td>
                      <td data-testid={`portfolio-basis-${symbol}`} className="px-2 text-right">
                        <Money value={row.costBasis} />
                      </td>
                      <td data-testid={`portfolio-pnl-${symbol}`} className="px-2 text-right">
                        <PnlMoney state={state} value={row.pnl} />
                      </td>
                      <td data-testid={`portfolio-pct-${symbol}`} className="px-2 text-right">
                        <PnlPercent state={state} value={row.pnlPct} />
                      </td>
                      <td className="px-2 text-right">
                        <button
                          type="button"
                          aria-label={`Remove ${symbol}`}
                          onClick={() => remove(symbol)}
                          className="focus-ring inline-flex h-5 w-5 items-center justify-center rounded-[2px] border border-line text-label leading-none text-fg-muted transition-colors hover:border-line-strong hover:text-fg"
                        >
                          <span aria-hidden="true">×</span>
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
              <tfoot>
                <tr
                  data-testid="portfolio-total"
                  data-covered={total.covered}
                  data-total={total.total}
                  className="h-7 border-t border-line-strong bg-surface-raised"
                >
                  <td colSpan={4} className="px-2 text-data font-medium text-fg">
                    {totalLabel}
                  </td>
                  <td data-testid="total-market-value" className="px-2 text-right">
                    <Money state={totalState} value={total.marketValue} />
                  </td>
                  <td data-testid="total-cost-basis" className="px-2 text-right">
                    <Money state={totalState} value={total.costBasis} />
                  </td>
                  <td data-testid="total-pnl" className="px-2 text-right">
                    <PnlMoney state={totalState} value={total.pnl} />
                  </td>
                  <td data-testid="total-pnl-pct" className="px-2 text-right">
                    <PnlPercent state={totalState} value={total.pnlPct} />
                  </td>
                  <td />
                </tr>
              </tfoot>
            </table>
          </div>

          {quotes.status === "ready" && unpriced > 0 && total.covered > 0 ? (
            <p data-testid="portfolio-partial" className="mt-1 px-0.5 text-data text-fg-muted">
              {unpriced} holding{unpriced === 1 ? "" : "s"} without a quote{" "}
              {unpriced === 1 ? "is" : "are"} not in this total.
            </p>
          ) : null}
        </>
      )}
    </main>
  );
}
