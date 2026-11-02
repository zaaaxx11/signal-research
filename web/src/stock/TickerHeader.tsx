/**
 * The header band: who the instrument is, and what it costs.
 *
 * Two rows. The first is identity — ticker, company, sector · industry · index
 * — and the second is the quote: last price and change on the left, volume,
 * market cap and previous close on the right. A field the deployment cannot
 * answer is the em dash, never a zero and never a blank that reads as "nothing
 * to report".
 *
 * The change carries its direction in the glyph and the sign, so colour is
 * decoration rather than the only channel. The price is mono and tabular; the
 * labels are 10 px uppercase, the numbers 13 px.
 *
 * The band is otherwise read-only, so the page's one action arrives through
 * `actions` and is dropped beside the price: the header does not decide what
 * can be done to an instrument, it only knows where that answer belongs.
 */

import * as React from "react";

import { formatMoney } from "@/grid/format";
import { changeTone, formatChange, formatPrice, formatVolume } from "@/home/signals";
import { cn } from "@/lib/utils";
import { Link } from "@/shell/routes";

import type { HeaderView } from "./useTicker";

export interface TickerHeaderProps {
  readonly header: HeaderView;
  /**
   * The page's action for this instrument, drawn beside the quote.
   *
   * A slot rather than a callback: the header stays a picture of the ticker,
   * and the page that owns the dialog owns the button that opens it.
   */
  readonly actions?: React.ReactNode;
}

function Stat({
  label,
  value,
  testId,
}: {
  label: string;
  value: string;
  testId: string;
}): React.ReactElement {
  return (
    <div className="flex min-w-0 flex-col justify-end">
      <span className="text-label uppercase tracking-[0.08em] text-fg-subtle">{label}</span>
      <span
        data-testid={testId}
        className="nums truncate font-mono text-ui leading-6 tabular-nums text-fg"
        title={value}
      >
        {value}
      </span>
    </div>
  );
}

export function TickerHeader({ header, actions }: TickerHeaderProps): React.ReactElement {
  const tone = changeTone(header.change);
  const identity = [header.sector, header.industry, header.indexMembership].filter(
    (entry): entry is string => entry !== null && entry.length > 0,
  );

  return (
    <header
      data-testid="ticker-header"
      className="shrink-0 border-b border-line bg-surface px-4 pb-2.5 pt-2"
    >
      <div className="mb-1 flex items-center gap-2">
        <Link to="/screener" className="text-label uppercase tracking-[0.08em] text-fg-muted hover:text-brand">
          ← Screener
        </Link>
        <span aria-hidden="true" className="text-fg-subtle">
          /
        </span>
        <span className="text-label uppercase tracking-[0.08em] text-fg-subtle">Ticker</span>
      </div>

      <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1
          data-testid="header-symbol"
          className="nums shrink-0 font-mono text-[22px] font-semibold leading-7 tracking-tight text-fg"
        >
          {header.symbol}
        </h1>
        <p
          data-testid="header-company"
          className="min-w-0 max-w-full truncate text-title leading-7 text-fg"
          title={header.company ?? undefined}
        >
          {header.company ?? "—"}
        </p>
        {identity.length > 0 ? (
          <p
            data-testid="header-identity"
            className="flex min-w-0 flex-wrap items-baseline gap-x-1.5 text-data text-fg-muted"
          >
            {identity.map((entry, index) => (
              <React.Fragment key={entry}>
                {index > 0 ? (
                  <span aria-hidden="true" className="text-fg-subtle">
                    ·
                  </span>
                ) : null}
                <span className="truncate">{entry}</span>
              </React.Fragment>
            ))}
          </p>
        ) : null}
      </div>

      <div className="mt-1.5 flex flex-wrap items-end justify-between gap-x-6 gap-y-2">
        <div className="flex items-baseline gap-2.5">
          <span
            data-testid="header-price"
            className="nums font-mono text-[22px] font-semibold leading-7 tabular-nums text-fg"
          >
            {formatPrice(header.price)}
          </span>
          <span
            data-testid="header-change"
            className={cn(
              "nums font-mono text-ui leading-6 tabular-nums",
              tone === "up" && "text-up",
              tone === "down" && "text-down",
              tone === "flat" && "text-fg-muted",
            )}
          >
            {tone === "up" ? "▲ " : tone === "down" ? "▼ " : ""}
            {formatChange(header.change)}
          </span>
          {actions ? (
            <span className="ml-0.5 inline-flex self-center">{actions}</span>
          ) : null}
        </div>

        <div className="flex flex-wrap items-end gap-x-6 gap-y-1">
          <Stat label="Volume" value={formatVolume(header.volume)} testId="header-volume" />
          <Stat
            label="Market cap"
            value={header.marketCap === null ? "—" : formatMoney(header.marketCap, true)}
            testId="header-market-cap"
          />
          <Stat label="Prev close" value={formatPrice(header.prevClose)} testId="header-prev-close" />
        </div>
      </div>
    </header>
  );
}
