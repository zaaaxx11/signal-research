/**
 * The crypto block: a pulse strip over one scrolling table.
 *
 * **The pulse** is the market at large — total capitalisation, BTC's share of
 * it, and the day's move in it — from `GET /market/crypto/global`. One row of
 * three figures on a sunken plane; when the global document did not answer,
 * the strip prints why instead of three em dashes, because "the source failed"
 * and "the market did nothing" are different facts.
 *
 * **The table** is the coin list the desk reads: mark, name, price, the 24h
 * and 7-day moves, capitalisation, rank. Every row spreads `quoteProps` —
 * `data-quote` on the row itself, whose JSON the global HoverBubble reads —
 * so hovering a coin anywhere in the row puts its chart in the bubble; the
 * `kind` says crypto, and the note carries the cap the chart cannot see.
 *
 * The logo is meta's own `image` URL when it is an `http(s)` link, sanitised
 * at parse; a coin with no image, or none at all, draws the local monogram
 * (`LogoMark`) instead of a broken `<img>`. A missing logo is not a missing
 * row: identity is the symbol, and it is always printed.
 *
 * Two partial failures stay partial. Meta missing: the table still prices and
 * the cap/rank/7d columns print em dashes over one quiet sentence. Global
 * missing: the table stands and the strip names the absence. Both are counted
 * (`42 of 50 symbols shaded by meta`) so a coverage gap cannot pose as a
 * complete read.
 */

import * as React from "react";

import { formatMoney, formatNumber, formatPercent } from "@/grid/format";
import { LogoMark } from "@/grid/LogoMark";
import { quoteProps } from "@/lib/quoteAttrs";
import { pricePrecision } from "@/alt/alt";
import { Card, SkeletonBar } from "./HomeBlock";
import { cn } from "@/lib/utils";

import { Delta } from "./Values";
import type { CryptoRow, CryptoGlobal } from "../crypto";
import type { CryptoStatus } from "../useHomeCrypto";

/** The table mirrors the pulse's unit for caps: `3.42T`, not full digits. */
function compactCap(value: number | null): string {
  if (value === null) return "—";
  return formatMoney(value, true);
}

/**
 * How many coins the home card shows: the top of the list, as a peek. The full
 * universe is the crypto page, which the section's "Crypto page" link opens.
 * Capped so the home board never carries a coin list that scrolls of its own.
 */
const CRYPTO_HOME_LIMIT = 12;

/** `54.31%` — a share, never a signed move. */
function dominance(value: number | null): string {
  if (value === null) return "—";
  return formatPercent(value, 2);
}

export interface CryptoBlockProps {
  readonly status: CryptoStatus;
  readonly rows: readonly CryptoRow[];
  readonly global: CryptoGlobal | null;
  /** The shaded sources that did not answer: `meta`, `global`. */
  readonly missing: readonly string[];
  /** How many of the table's symbols the meta map echoed. */
  readonly measured: number | null;
  readonly error: string | null;
  readonly onRetry: () => void;
  readonly className?: string;
}

/** The pulse strip's one figure, label above value. */
function Pulse({
  label,
  children,
  tone,
}: {
  readonly label: string;
  readonly children: React.ReactNode;
  readonly tone?: "up" | "down" | "flat";
}) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <span className="truncate text-[9px] uppercase tracking-[0.05em] text-fg-subtle">
        {label}
      </span>
      <span className={cn("nums min-w-0 truncate text-[12px]", tone === "up" ? "text-up" : tone === "down" ? "text-down" : tone === "flat" ? "text-flat" : "text-fg")}>
        {children}
      </span>
    </div>
  );
}

/** A crypto row's logo cell: meta's image when it is a credible URL, else the monogram. */
function CoinMark({ row }: { readonly row: CryptoRow }) {
  const [broken, setBroken] = React.useState(false);
  const showImage = row.image !== null && !broken;
  return (
    <span className="relative inline-flex size-4 shrink-0 items-center justify-center overflow-hidden rounded-full bg-surface-sunken">
      {showImage ? (
        <img
          src={row.image}
          alt=""
          title={row.symbol}
          loading="lazy"
          onError={() => setBroken(true)}
          className="size-4 rounded-full object-cover"
        />
      ) : (
        <LogoMark symbol={row.symbol} size={16} />
      )}
    </span>
  );
}

/** One `data-quote` row: the facts a HoverBubble chart is drawn from. */
function quoteInfo(row: CryptoRow): { [k: string]: string } {
  const note =
    row.marketCap === null
      ? undefined
      : `MCAP ${compactCap(row.marketCap)}${row.marketCapRank === null ? "" : ` · #${formatNumber(row.marketCapRank, 0)}`}`;
  return quoteProps({
    symbol: row.symbol,
    name: row.name ?? row.symbol,
    price: row.price,
    changePct: row.changePct,
    kind: "crypto",
    note,
  }) as { [k: string]: string };
}

export function CryptoBlock({
  status,
  rows,
  global,
  missing,
  measured,
  error,
  onRetry,
  className,
}: CryptoBlockProps) {
  if (status === "error") {
    return (
      <div data-testid="crypto-error" className="rounded-sm border border-line border-l-2 border-l-alert bg-surface px-3 py-2">
        <p className="min-w-0 truncate text-[12px] text-alert">{error ?? "Crypto did not load."}</p>
        <button type="button" onClick={onRetry} data-testid="crypto-retry" className="mt-1 rounded-sm border border-line px-2 py-0.5 text-[11px] text-fg transition-[opacity] duration-150 hover:opacity-80">
          Retry
        </button>
      </div>
    );
  }

  const loading = status === "loading";
  const coins = rows.slice(0, CRYPTO_HOME_LIMIT);
  const pulse = (
    <div data-testid="crypto-pulse" className="grid shrink-0 grid-cols-3 gap-3 border-b border-line px-2 py-1.5">
      {loading ? (
        <>
          <SkeletonBar width="70%" height={9} />
          <SkeletonBar width="54%" height={9} />
          <SkeletonBar width="62%" height={9} />
        </>
      ) : global === null ? (
        <p className="col-span-3 text-[10px] text-fg-subtle">
          Market pulse unavailable — the global crypto document did not answer this request.
        </p>
      ) : (
        <>
          <Pulse label="Total market cap">{compactCap(global.totalMarketCapUsd)}</Pulse>
          <Pulse label="BTC dominance">{dominance(global.btcDominance)}</Pulse>
          <Pulse
            label="MCAP 24h"
            tone={global.marketCapChangePct24h === null ? undefined : global.marketCapChangePct24h > 0 ? "up" : global.marketCapChangePct24h < 0 ? "down" : "flat"}
          >
            <Delta value={global.marketCapChangePct24h} missing="24h market cap change" className="text-[12px]" />
          </Pulse>
        </>
      )}
    </div>
  );

  return (
    <Card id="crypto" title="Crypto" hideTitle className={cn("bg-surface", className)}>
      {pulse}
      {loading ? (
        <div aria-hidden="true" data-testid="crypto-skeleton" className="flex flex-col gap-2 p-2 [&>span]:animate-pulse">
          {Array.from({ length: 8 }, (_, index) => (
            <SkeletonBar key={index} width={`${88 - (index % 4) * 9}%`} height={10} />
          ))}
        </div>
      ) : rows.length === 0 ? (
        <p className="px-2 py-2 text-[11px] text-fg-subtle">No crypto prices in this response.</p>
      ) : (
        <>
          {missing.includes("meta") && measured === 0 ? (
            <p className="px-2 text-[10px] text-fg-subtle">
              Meta is unavailable — caps, ranks and 7-day moves print without it.
            </p>
          ) : null}
          {rows.length > coins.length ? (
            <p className="px-2 pt-1 text-[10px] text-fg-subtle">
              Top {coins.length} of {rows.length} coins. Full list on the crypto page.
            </p>
          ) : null}
          <div data-testid="crypto-coins" className="min-h-0 min-w-0">
            <table className="w-full table-fixed border-collapse text-[11px]">
              <caption className="sr-only">Crypto: price, 24h, 7 days, capitalisation, rank</caption>
              <thead>
                <tr className="h-5 border-b border-line text-[10px] uppercase tracking-[0.05em] text-fg-subtle">
                  <th scope="col" className="px-2 text-left font-medium">Coin</th>
                  <th scope="col" className="px-2 text-right font-medium">Price</th>
                  <th scope="col" className="px-2 text-right font-medium">24h</th>
                  <th scope="col" className="px-2 text-right font-medium">7d</th>
                  <th scope="col" className="px-2 text-right font-medium">MCAP</th>
                  <th scope="col" className="px-2 text-right font-medium">Rank</th>
                </tr>
              </thead>
              <tbody>
                {coins.map((row) => (
                  <tr
                    key={row.symbol}
                    data-crypto-row={row.symbol}
                    {...quoteInfo(row)}
                    className="h-6 border-b border-line/60 odd:bg-row-alt transition-[opacity] duration-150 hover:bg-row-hover"
                  >
                    <td className="min-w-0 px-2">
                      <span className="flex min-w-0 items-center gap-1.5">
                        <CoinMark row={row} />
                        <span className="min-w-0 truncate font-medium text-fg" title={row.name ?? row.symbol}>
                          {row.name ?? row.symbol}
                        </span>
                        <span className="shrink-0 font-mono text-[10px] text-fg-subtle">{row.symbol}</span>
                      </span>
                    </td>
                    <td className="px-2 text-right">
                      <span className="nums" title={`$${row.price}`}>
                        {formatMoney(row.price, false, pricePrecision(row.price))}
                      </span>
                    </td>
                    <td className="px-2 text-right">
                      <Delta value={row.changePct} className="text-[10px]" />
                    </td>
                    <td className="px-2 text-right">
                      <Delta value={row.pct7d} missing="7d change" className="text-[10px]" />
                    </td>
                    <td className="px-2 text-right">
                      <span className="nums truncate text-fg-muted" title={row.marketCap === null ? "Market cap unavailable — the meta source did not answer" : undefined}>
                        {compactCap(row.marketCap)}
                      </span>
                    </td>
                    <td className="px-2 text-right text-fg-muted">
                      {row.marketCapRank === null ? (
                        <span data-testid="crypto-caps-fallback" data-null="true" title="Rank unavailable — the meta source did not answer">—</span>
                      ) : (
                        formatNumber(row.marketCapRank, 0)
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Card>
  );
}
