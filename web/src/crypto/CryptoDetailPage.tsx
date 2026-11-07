/**
 * `/crypto/{symbol}` — the crypto detail page.
 *
 * One page for every crypto symbol the alt table links to. It reads price from
 * `/market/alt?class=crypto`, meta from `/market/crypto/meta`, and the chart
 * from `/market/chart/{symbol}`. Everything is keyed by the Yahoo symbol
 * (e.g. BTC-USD).
 *
 * The meta read is already loaded by the crypto list page through
 * `useCryptoEnrich`; this page does its own read so a deep link to
 * `/crypto/BTC-USD` works without the list page having been visited first.
 *
 * The CoinGecko `/coins/{id}` description endpoint is not wired yet, so the
 * description is the coin name from meta — a single line, not a long block.
 */

import * as React from "react";

import { Chart } from "@/charts/Chart";
import { SectionTitle } from "@/components/SectionTitle";
import { ErrorState } from "@/components/ErrorState";
import { Link } from "@/shell/routes";
import { cn } from "@/lib/utils";
import { quoteProps } from "@/lib/quoteAttrs";
import { notify } from "@/lib/notify";
import { getJson } from "@/lib/api";
import { formatChange, formatPrice, formatVolume } from "@/home/signals";

import {
  fetchCoinDetail,
  fetchCryptoMeta,
  formatMarketCap,
  formatSignedPct,
  type CoinDetailDoc,
  type CryptoMetaDoc,
  type CryptoMetaRow,
} from "@/alt/cryptoEnrich";

/* ---------------------------------------------------------------------------
 * Data hooks
 * ------------------------------------------------------------------------ */

interface PriceData {
  price: number | null;
  changePct: number | null;
  volume: number | null;
  name: string | null;
}

interface ReadState<T> {
  status: "loading" | "ready" | "error";
  data: T | null;
  error: string | null;
  reload: () => void;
}

/** Fetch the current price for one crypto symbol from the alt endpoint. */
function useCryptoPrice(symbol: string): ReadState<PriceData> {
  const [attempt, setAttempt] = React.useState(0);
  const [state, setState] = React.useState<ReadState<PriceData>>({
    status: "loading",
    data: null,
    error: null,
    reload: () => setAttempt((v) => v + 1),
  });

  React.useEffect(() => {
    const controller = new AbortController();
    let live = true;
    setState((prev) => ({ ...prev, status: "loading", error: null }));

    getJson(`/market/alt?class=crypto&per=200`, controller.signal)
      .then((body) => {
        if (!live) return;
        const rows = (body as { rows?: unknown[] })?.rows ?? [];
        const row = (rows as Record<string, unknown>[]).find(
          (r) => r["symbol"] === symbol,
        );
        if (!row) {
          setState({
            status: "error",
            data: null,
            error: `${symbol} not found in crypto quotes`,
            reload: () => setAttempt((v) => v + 1),
          });
          return;
        }
        setState({
          status: "ready",
          data: {
            price:
              typeof row["price"] === "number" ? row["price"] : null,
            changePct:
              typeof row["change_pct"] === "number"
                ? row["change_pct"]
                : null,
            volume:
              typeof row["volume"] === "number" ? row["volume"] : null,
            name: typeof row["name"] === "string" ? row["name"] : null,
          },
          error: null,
          reload: () => setAttempt((v) => v + 1),
        });
      })
      .catch((cause: unknown) => {
        if (!live || controller.signal.aborted) return;
        setState({
          status: "error",
          data: null,
          error:
            cause instanceof Error ? cause.message : "Failed to fetch price",
          reload: () => setAttempt((v) => v + 1),
        });
      });

    return () => {
      live = false;
      controller.abort();
    };
  }, [symbol, attempt]);

  return state;
}

/** Fetch meta for all cryptos, then pick the row for this symbol. */
function useCryptoMetaRow(symbol: string): {
  status: "loading" | "ready" | "error";
  row: CryptoMetaRow | null;
  doc: CryptoMetaDoc | null;
  error: string | null;
  reload: () => void;
} {
  const [attempt, setAttempt] = React.useState(0);
  const [state, setState] = React.useState<{
    status: "loading" | "ready" | "error";
    row: CryptoMetaRow | null;
    doc: CryptoMetaDoc | null;
    error: string | null;
    reload: () => void;
  }>({
    status: "loading",
    row: null,
    doc: null,
    error: null,
    reload: () => setAttempt((v) => v + 1),
  });

  React.useEffect(() => {
    const controller = new AbortController();
    let live = true;
    setState((prev) => ({ ...prev, status: "loading", error: null }));

    fetchCryptoMeta(controller.signal)
      .then((doc) => {
        if (!live) return;
        if (!doc) {
          setState({
            status: "error",
            row: null,
            doc: null,
            error: "Crypto market data unavailable",
            reload: () => setAttempt((v) => v + 1),
          });
          return;
        }
        const row = doc.rows[symbol] ?? null;
        setState({
          status: "ready",
          row,
          doc,
          error: null,
          reload: () => setAttempt((v) => v + 1),
        });
      })
      .catch(() => {
        if (!live) return;
        setState({
          status: "error",
          row: null,
          doc: null,
          error: "Failed to fetch crypto meta",
          reload: () => setAttempt((v) => v + 1),
        });
      });

    return () => {
      live = false;
      controller.abort();
    };
  }, [symbol, attempt]);

  return state;
}

/* ---------------------------------------------------------------------------
 * Formatters
 * ------------------------------------------------------------------------ */

/** Fetch one coin's full CoinGecko record (About / Links / Community). */
function useCoinDetail(symbol: string): {
  status: "loading" | "ready" | "error";
  data: CoinDetailDoc | null;
  reload: () => void;
} {
  const [attempt, setAttempt] = React.useState(0);
  const [state, setState] = React.useState<{
    status: "loading" | "ready" | "error";
    data: CoinDetailDoc | null;
    reload: () => void;
  }>({
    status: "loading",
    data: null,
    reload: () => setAttempt((v) => v + 1),
  });

  React.useEffect(() => {
    const controller = new AbortController();
    let live = true;
    setState((prev) => ({ ...prev, status: "loading" }));

    fetchCoinDetail(symbol, controller.signal)
      .then((data) => {
        if (!live) return;
        setState({
          status: "ready",
          data,
          reload: () => setAttempt((v) => v + 1),
        });
      })
      .catch(() => {
        if (!live) return;
        setState({
          status: "error",
          data: null,
          reload: () => setAttempt((v) => v + 1),
        });
      });

    return () => {
      live = false;
      controller.abort();
    };
  }, [symbol, attempt]);

  return state;
}

function formatBigNumber(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  if (value >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(2)}B`;
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return value.toLocaleString("en-US", { maximumFractionDigits: 0 });
}

function formatSupply(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  if (value >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(2)}B`;
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(2)}K`;
  return value.toLocaleString("en-US", { maximumFractionDigits: 0 });
}

/* ---------------------------------------------------------------------------
 * Skeleton
 * ------------------------------------------------------------------------ */

function Skeleton() {
  return (
    <div data-testid="crypto-detail-skeleton" aria-busy="true" className="flex-1 p-4">
      <div className="mb-4 animate-pulse space-y-3">
        <div className="h-6 w-20 rounded-sm bg-surface-raised" />
        <div className="flex items-center gap-3">
          <div className="size-8 rounded-sm bg-surface-raised" />
          <div className="h-7 w-32 rounded-sm bg-surface-raised" />
          <div className="h-6 w-24 rounded-sm bg-surface-raised" />
        </div>
        <div className="h-8 w-40 rounded-sm bg-surface-raised" />
      </div>
      <div className="mb-4 animate-pulse space-y-2">
        <div className="h-[280px] w-full rounded-md bg-surface-raised" />
      </div>
      <div className="animate-pulse space-y-2">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {Array.from({ length: 8 }, (_, i) => (
            <div key={i} className="h-16 rounded-md bg-surface-raised" />
          ))}
        </div>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------------------
 * Stats grid
 * ------------------------------------------------------------------------ */

interface StatCardProps {
  label: string;
  value: string;
  sub?: string;
  testId: string;
}

function StatCard({ label, value, sub, testId }: StatCardProps) {
  return (
    <div
      data-testid={testId}
      className="rounded-md border border-line bg-surface px-3 py-2"
    >
      <p className="text-label uppercase tracking-[0.06em] text-fg-subtle">
        {label}
      </p>
      <p className="mt-0.5 nums text-data font-medium text-fg">{value}</p>
      {sub ? (
        <p className="mt-0.5 text-label text-fg-muted">{sub}</p>
      ) : null}
    </div>
  );
}

/* ---------------------------------------------------------------------------
 * Header
 * ------------------------------------------------------------------------ */

function Header({
  symbol,
  metaRow,
  priceData,
}: {
  symbol: string;
  metaRow: CryptoMetaRow | null;
  priceData: PriceData | null;
}) {
  const name = metaRow?.name ?? priceData?.name ?? symbol;
  const price = priceData?.price ?? null;
  const changePct = priceData?.changePct ?? null;
  const image = metaRow?.image ?? null;

  const tone =
    changePct === null ? "flat" : changePct > 0 ? "up" : changePct < 0 ? "down" : "flat";

  const quoteAttr = quoteProps({
    symbol,
    name: metaRow?.name ?? undefined,
    price,
    changePct,
    kind: "crypto",
  });

  return (
    <header
      data-testid="crypto-detail-header"
      className="shrink-0 border-b border-line bg-surface px-4 py-3"
      {...quoteAttr}
    >
      {/* Breadcrumb */}
      <div className="mb-2 flex items-center gap-2">
        <Link
          to="/crypto"
          className="text-label uppercase tracking-[0.08em] text-fg-muted hover:text-brand"
        >
          ← Crypto
        </Link>
        <span aria-hidden="true" className="text-fg-subtle">
          /
        </span>
        <span className="text-label uppercase tracking-[0.08em] text-fg-subtle">
          {symbol}
        </span>
      </div>

      {/* Identity row */}
      <div className="flex items-center gap-3">
        {image ? (
          <img
            src={image}
            alt=""
            width={32}
            height={32}
            className="size-8 shrink-0 rounded-full object-contain"
            onError={(e) => {
              (e.target as HTMLImageElement).style.display = "none";
            }}
          />
        ) : null}
        <div className="min-w-0">
          <h1
            data-testid="crypto-detail-name"
            className="text-title font-semibold text-fg"
          >
            {name}
          </h1>
          <p
            data-testid="crypto-detail-symbol"
            className="font-mono text-data text-fg-muted"
          >
            {symbol}
          </p>
        </div>
        {metaRow?.market_cap_rank != null ? (
          <span
            data-testid="crypto-detail-rank"
            className="ml-auto shrink-0 rounded-sm border border-line px-2 py-0.5 font-mono text-label text-fg-muted"
          >
            #{metaRow.market_cap_rank}
          </span>
        ) : null}
      </div>

      {/* Price row */}
      <div className="mt-2 flex items-baseline gap-2.5">
        <span
          data-testid="crypto-detail-price"
          className="nums font-mono text-[22px] font-semibold leading-7 tabular-nums text-fg"
        >
          {formatPrice(price)}
        </span>
        <span
          data-testid="crypto-detail-change"
          className={cn(
            "nums font-mono text-ui leading-6 tabular-nums",
            tone === "up" && "text-up",
            tone === "down" && "text-down",
            tone === "flat" && "text-fg-muted",
          )}
        >
          {tone === "up" ? "▲ " : tone === "down" ? "▼ " : ""}
          {formatChange(changePct)}
        </span>
      </div>
    </header>
  );
}

/* ---------------------------------------------------------------------------
 * Main page
 * ------------------------------------------------------------------------ */

export interface CryptoDetailPageProps {
  readonly symbol: string;
}

export function CryptoDetailPage({
  symbol,
}: CryptoDetailPageProps): React.ReactElement {
  const price = useCryptoPrice(symbol);
  const meta = useCryptoMetaRow(symbol);
  const detail = useCoinDetail(symbol);
  const coin = detail.data;

  // Notify on errors — but a price miss is silent when the CoinGecko meta
  // carries a fallback price (the supplement coins Yahoo no longer quotes).
  React.useEffect(() => {
    if (price.status === "error" && price.error && meta.row?.current_price == null) {
      notify.error("Price read failed", price.error);
    }
  }, [price.status, price.error, meta.row?.current_price]);

  React.useEffect(() => {
    if (meta.status === "error" && meta.error) {
      notify.warn("Crypto meta unavailable", meta.error);
    }
  }, [meta.status, meta.error]);

  // The effective price: Yahoo's live quote when it has the coin, otherwise the
  // CoinGecko meta's current_price (the list + detail surface every catalogue
  // symbol, even the ones Yahoo dropped).
  const metaRow = meta.row;
  const effPrice = price.data?.price ?? metaRow?.current_price ?? null;
  const effChange = price.data?.changePct ?? metaRow?.pct_24h ?? null;

  // A price miss is only fatal when the meta carries no fallback price either.
  const priceFallback = metaRow?.current_price ?? null;

  // Show skeleton while the primary reads are loading.
  if (price.status === "loading" || meta.status === "loading") {
    return <Skeleton />;
  }

  // Show error only if the price read failed AND the meta has no fallback.
  if (
    price.status === "error" &&
    priceFallback === null &&
    meta.status === "error"
  ) {
    return (
      <main
        data-testid="crypto-detail-page"
        data-symbol={symbol}
        className="flex min-w-0 flex-1 flex-col"
      >
        <ErrorState
          title="Could not load crypto data"
          error={`${symbol}: ${price.error ?? meta.error ?? "Unknown error"}`}
          onRetry={() => {
            price.reload();
            meta.reload();
          }}
          testId="crypto-detail-error"
        />
      </main>
    );
  }

  const headerPrice: PriceData = {
    price: effPrice,
    changePct: effChange,
    volume: price.data?.volume ?? null,
    name: price.data?.name ?? metaRow?.name ?? symbol,
  };

  return (
    <main
      data-testid="crypto-detail-page"
      data-symbol={symbol}
      className="flex min-w-0 flex-1 flex-col bg-bg"
    >
      <Header symbol={symbol} metaRow={metaRow} priceData={headerPrice} />

      <div className="flex-1 overflow-y-auto p-4">
        {/* Chart */}
        <section
          data-testid="crypto-detail-chart-section"
          className="mb-4"
        >
          <SectionTitle variant={1} testId="crypto-detail-chart-title">
            Price Chart
          </SectionTitle>
          <Chart
            symbol={symbol}
            initialRange="1d"
            height={320}
            renderer="bars"
            showVolume
            className="rounded-md border border-line"
          />
        </section>

        {/* About — the real CoinGecko description when available */}
        <section data-testid="crypto-detail-description" className="mb-4">
          <SectionTitle variant={2} testId="crypto-detail-desc-title">
            About
          </SectionTitle>
          <div className="rounded-md border border-line bg-surface px-3 py-2.5 text-data text-fg-muted leading-relaxed">
            {coin?.available && coin.description ? (
              <p>{coin.description}</p>
            ) : metaRow?.name ? (
              <p>{metaRow.name}</p>
            ) : null}
            {coin?.available && coin.categories.length > 0 ? (
              <div
                data-testid="crypto-detail-categories"
                className="mt-2.5 flex flex-wrap gap-1.5"
              >
                {coin.categories.map((cat) => (
                  <span
                    key={cat}
                    className="rounded-sm border border-line px-1.5 py-0.5 text-label text-fg-muted"
                  >
                    {cat}
                  </span>
                ))}
              </div>
            ) : null}
          </div>
        </section>

        {/* Links + Community — social proof and where to follow */}
        {coin?.available &&
        (coin.website ||
          coin.twitter ||
          coin.twitter_followers != null ||
          coin.telegram_members != null ||
          coin.reddit_subscribers != null ||
          coin.sentiment_up_pct != null) ? (
          <section data-testid="crypto-detail-community" className="mb-4">
            <SectionTitle variant={4} testId="crypto-detail-community-title">
              Links &amp; Community
            </SectionTitle>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {coin.website ? (
                <a
                  data-testid="crypto-detail-website"
                  href={coin.website}
                  target="_blank"
                  rel="noreferrer"
                  className="rounded-md border border-line bg-surface px-3 py-2 text-data text-fg-muted hover:border-brand hover:text-brand"
                >
                  🌐 Website
                </a>
              ) : (
                <div className="rounded-md border border-line bg-surface px-3 py-2 text-data text-fg-subtle">
                  Website
                </div>
              )}
              {coin.twitter ? (
                <a
                  data-testid="crypto-detail-twitter"
                  href={`https://x.com/${coin.twitter}`}
                  target="_blank"
                  rel="noreferrer"
                  className="rounded-md border border-line bg-surface px-3 py-2 text-data text-fg-muted hover:border-brand hover:text-brand"
                >
                  𝕏 @{coin.twitter}
                </a>
              ) : (
                <div className="rounded-md border border-line bg-surface px-3 py-2 text-data text-fg-subtle">
                  Twitter
                </div>
              )}
              <StatCard
                label="Twitter Followers"
                value={formatBigNumber(coin.twitter_followers)}
                testId="crypto-stat-twitter-followers"
              />
              {coin.sentiment_up_pct != null ? (
                <StatCard
                  label="Sentiment"
                  value={`${coin.sentiment_up_pct.toFixed(1)}% bullish`}
                  sub={coin.telegram_members != null || coin.reddit_subscribers != null ? undefined : "votes"}
                  testId="crypto-stat-sentiment"
                />
              ) : (
                <StatCard
                  label="Reddit"
                  value={coin.reddit_subscribers != null ? formatBigNumber(coin.reddit_subscribers) : "—"}
                  sub={coin.telegram_members != null ? `TG ${formatBigNumber(coin.telegram_members)}` : undefined}
                  testId="crypto-stat-reddit"
                />
              )}
            </div>
          </section>
        ) : null}

        {/* Stats grid */}
        <section data-testid="crypto-detail-stats" className="mb-4">
          <SectionTitle variant={3} testId="crypto-detail-stats-title">
            Stats
          </SectionTitle>

          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <StatCard
              label="Market Cap"
              value={formatMarketCap(metaRow?.market_cap ?? null) ?? "—"}
              testId="crypto-stat-market-cap"
            />

            <StatCard
              label="24h High"
              value={
                metaRow?.high_24h != null
                  ? formatPrice(metaRow.high_24h)
                  : "—"
              }
              testId="crypto-stat-high-24h"
            />

            <StatCard
              label="24h Low"
              value={
                metaRow?.low_24h != null
                  ? formatPrice(metaRow.low_24h)
                  : "—"
              }
              testId="crypto-stat-low-24h"
            />

            <StatCard
              label="All-Time High"
              value={
                metaRow?.ath != null ? formatPrice(metaRow.ath) : "—"
              }
              sub={
                metaRow?.ath_change_pct != null
                  ? `${formatSignedPct(metaRow.ath_change_pct)} from ATH`
                  : undefined
              }
              testId="crypto-stat-ath"
            />

            <StatCard
              label="Circulating Supply"
              value={formatSupply(metaRow?.circulating_supply ?? null)}
              testId="crypto-stat-supply"
            />

            {metaRow?.tvl != null ? (
              <StatCard
                label="TVL"
                value={formatMarketCap(metaRow.tvl) ?? "—"}
                testId="crypto-stat-tvl"
              />
            ) : null}

            <StatCard
              label="1h Change"
              value={
                metaRow?.pct_1h != null
                  ? formatSignedPct(metaRow.pct_1h) ?? "—"
                  : "—"
              }
              testId="crypto-stat-pct-1h"
            />

            <StatCard
              label="7d Change"
              value={
                metaRow?.pct_7d != null
                  ? formatSignedPct(metaRow.pct_7d) ?? "—"
                  : "—"
              }
              testId="crypto-stat-pct-7d"
            />

            <StatCard
              label="30d Change"
              value={
                metaRow?.pct_30d != null
                  ? formatSignedPct(metaRow.pct_30d) ?? "—"
                  : "—"
              }
              testId="crypto-stat-pct-30d"
            />

            <StatCard
              label="24h Volume"
              value={
                price.data?.volume != null
                  ? formatVolume(price.data.volume)
                  : "—"
              }
              testId="crypto-stat-volume"
            />
          </div>
        </section>

        {/* Back link */}
        <div className="mt-4 border-t border-line pt-4">
          <Link
            to="/crypto"
            data-testid="crypto-detail-back"
            className="text-label uppercase tracking-[0.08em] text-fg-muted hover:text-brand"
          >
            ← Back to Crypto List
          </Link>
        </div>
      </div>
    </main>
  );
}