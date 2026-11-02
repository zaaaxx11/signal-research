/**
 * The Tickers control: one text field that becomes **one** list filter.
 *
 * A reader who wants ten names should not add ten chips. The field accepts the
 * two spellings people actually paste — `AAPL, MSFT` and `AAPL MSFT` — checks
 * every token against the ticker alphabet, upper-cases what it accepts and
 * sends one `{key: "symbol", op: "in", value: [...]}` predicate. The server
 * treats that value as a membership list, so the answer is those rows and no
 * others.
 *
 * Refusals are inline and loud: an invalid token is named under the field and
 * the draft is kept, because a control that silently drops `BRK B` teaches the
 * reader that the filter is applied when it is not. Eleven names are refused
 * for the same reason — the cap is a limit, not a truncation.
 */

import * as React from "react";

import type { WireFilter } from "@/filters/types";

/** How many names one Tickers filter may hold. */
export const TICKER_LIMIT = 10;

/** One ticker: a letter, then up to nine letters, digits, dots or dashes. */
export const TICKER_PATTERN = /^[A-Za-z][A-Za-z0-9.\-]{0,9}$/;

export interface ParsedTickers {
  /** Upper-cased, deduplicated, in the order the reader typed them. */
  symbols: string[];
  /** Tokens the alphabet refuses, exactly as typed. */
  invalid: string[];
}

/** Split a draft on commas and whitespace; validate and canonicalise each token. */
export function parseTickers(raw: string): ParsedTickers {
  const tokens = raw.split(/[\s,]+/).filter((token) => token.length > 0);
  const symbols: string[] = [];
  const invalid: string[] = [];

  for (const token of tokens) {
    if (!TICKER_PATTERN.test(token)) {
      invalid.push(token);
      continue;
    }
    const symbol = token.toUpperCase();
    if (!symbols.includes(symbol)) symbols.push(symbol);
  }
  return { symbols, invalid };
}

export interface TickersFieldProps {
  /** The one wire shape the field writes: `symbol in [...]`. */
  onAdd: (filter: WireFilter) => void;
}

export function TickersField({ onAdd }: TickersFieldProps) {
  const [draft, setDraft] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);

  const apply = () => {
    const { symbols, invalid } = parseTickers(draft);
    if (invalid.length > 0) {
      const names = invalid.map((token) => `“${token}”`).join(", ");
      setError(
        `Not a valid ticker: ${names}. Start with a letter, then up to 9 letters, digits, dots or dashes.`,
      );
      return;
    }
    if (symbols.length === 0) {
      setError("Enter at least one ticker.");
      return;
    }
    if (symbols.length > TICKER_LIMIT) {
      setError(
        `A Tickers filter holds at most ${TICKER_LIMIT} tickers; ${symbols.length} were given.`,
      );
      return;
    }
    setError(null);
    onAdd({ key: "symbol", op: "in", value: symbols });
    setDraft("");
  };

  return (
    <label className="flex min-w-0 flex-col gap-0.5" data-testid="control-symbol">
      <span className="truncate text-data text-fg-muted" title="Tickers">
        Tickers
      </span>
      <span className="flex items-center gap-1">
        <input
          aria-label="Tickers"
          data-testid="control-input-symbol"
          type="text"
          inputMode="text"
          value={draft}
          placeholder="AAPL, MSFT"
          title="One or more tickers, separated by commas or spaces. Enter applies; Escape clears."
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              apply();
              return;
            }
            if (event.key === "Escape") {
              // Clear the draft, not the panel: the panel's own Escape handler
              // lives on `window`, so this event must stop here.
              event.preventDefault();
              event.stopPropagation();
              setDraft("");
              setError(null);
            }
          }}
          className="h-7 min-w-0 flex-1 rounded-sm border border-line-control bg-surface-sunken px-1.5 text-data text-fg"
        />
        <button
          type="button"
          onClick={apply}
          aria-label="Apply Tickers"
          className="h-7 shrink-0 rounded-sm border border-line-control px-1.5 text-data text-fg-muted transition-colors hover:text-fg"
        >
          Set
        </button>
      </span>
      {error ? (
        <span role="alert" className="text-data text-alert">
          {error}
        </span>
      ) : null}
    </label>
  );
}
