/**
 * Home: one equity request, one crypto read, eight titled sections, a footer
 * that says the name.
 *
 * The operator's layout, in order: a hero bar; a strip of index cards and
 * three sparks; six breadth tiles; the movers table paired against the 1-day
 * map (2:1); the crypto block — a pulse strip over the coin table; the
 * headlines paired against the insider list (2:1); the four signal screens
 * beside the side tables; a plain footer.
 *
 * Every section names itself with an :class:`SectionTitle` — one solid fill
 * per kind, mapped 1:1: home `1`, indices `2`, map `4`, movers `5`, news `6`,
 * insider `7`, crypto `8`, breadth `3`. The fill is the token's own colour, so
 * a reader can tell sections apart by the heading's colour alone, and a block
 * under a light fill cannot borrow a neighbour's.
 *
 * It is one CSS grid of twelve columns from 900 px and one column below that.
 * Twelve, not four, because the 2:1 pairs are the page's real relationships:
 * both are a :class:`PairingBox` whose parts are weights, so the proportion
 * survives the responsive page and the halves collapse to a column below 900px
 * (`max-[899px]:flex-col!` — a stylesheet rule with `!important` outranks the
 * inline `flex-direction` the pair sets).
 *
 * Three rules the page enforces for every block, so no block has to remember
 * them: a block that failed says so in its own card (there is no global blank
 * page), a block that is loading draws a skeleton of its own shape, and a block
 * whose source is `null` prints the reason from the payload — never a zero.
 *
 * The `h1` is visually hidden on purpose: the nav says where the reader is, and
 * a visible "Home" heading above a dashboard is a label on a door you can see
 * through. The hero bar's solid title is a section header, not the page name.
 */

import * as React from "react";

import { SectionTitle } from "@/components/SectionTitle";
import { PairingBox } from "@/components/PairingBox";
import { ErrorState } from "@/components/ErrorState";
import { Link } from "@/shell/routes";
import { formatReadAt } from "@/alt/alt";

import { blockState } from "./home";
import { useHome } from "./useHome";
import { useHomeCrypto } from "./useHomeCrypto";
import { BreadthTiles } from "./blocks/BreadthTiles";
import { CryptoBlock } from "./blocks/CryptoBlock";
import { HeadlineTicker } from "./blocks/HeadlineTicker";
import { IndexStrip } from "./blocks/IndexStrip";
import { InsiderLinksBlock } from "./blocks/InsiderLinksBlock";
import { MapBlock } from "./blocks/MapBlock";
import { MoversBlock } from "./blocks/MoversBlock";
import { SignalsBlock } from "./blocks/SignalsBlock";
import { SideColumn } from "./blocks/SideTables";
import { NewsTable } from "./blocks/NewsTable";

/**
 * Spans, as literal class strings.
 *
 * Tailwind reads source text, so a span assembled at run time (`col-span-${n}`)
 * would compile to nothing. The two names below are the page's whole span
 * vocabulary and each one is written once.
 */
const SPAN = {
  full: "min-[900px]:col-span-12",
} as const;

export function HomePage() {
  const { status, report, error, retry } = useHome("us");
  const crypto = useHomeCrypto();

  const state = React.useMemo(
    () => ({
      indices: blockState(status, report, "indices", "Indices"),
      breadth: blockState(status, report, "breadth", "Breadth"),
      sparks: blockState(status, report, "sparks", "Tickers"),
      movers: blockState(status, report, "movers", "Movers"),
      map: blockState(status, report, "map", "Map"),
      news: blockState(status, report, "news", "Headlines"),
      filings: blockState(status, report, "filings", "Insider"),
      insiderLatest: blockState(status, report, "insider_latest", "Insider"),
      insiderTop: blockState(status, report, "insider_top", "Insider"),
    }),
    [status, report],
  );

  const news = report?.news ?? null;
  const movers = report?.movers ?? null;
  const asOf =
    report?.asOf !== null && report?.asOf !== undefined
      ? (formatReadAt(report.asOf) ?? report.asOf)
      : null;

  /** The pair's halves, as paired children: a headline list and the insider feed. */
  const newsCount = news === null ? null : `${news.length}`;
  void asOf;

  return (
    <main data-testid="home" className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto bg-bg px-2 py-2">
      <h1 className="sr-only">Market overview</h1>

      {status === "error" ? (
        <div className="mb-2">
          <ErrorState
            testId="home-error"
            title="The market dashboard did not load"
            error={error ?? "Request failed"}
            onRetry={retry}
          />
        </div>
      ) : null}

      {/* The ticker bleeds to the page edges: a strip above the grid, not a
          card inside it. */}
      <HeadlineTicker
        state={state.news}
        news={news}
        className="-mx-2 mb-1"
      />

      <div
        data-testid="home-grid"
        data-status={status}
        aria-busy={status === "loading" ? "true" : undefined}
        className="grid grid-cols-1 gap-2 min-[900px]:grid-cols-12"
      >
        {/* The page's own bar — variant 1, the "home" fill. Removed per Raja:
            the ticker + section titles already orient the page. */}

        {/* Indices rows — variant 2. One card, eight tiles, sideways scroll. */}
        <div className={SPAN.full}>
          <SectionTitle
            variant={2}
            testId="section-title-strip"
            hint="indices and sparks"
            right={<span className="shrink-0 text-[10px] text-fg-subtle">Delayed</span>}
          >
            Market strip
          </SectionTitle>
          <IndexStrip
            indicesState={state.indices}
            indices={report?.indices ?? null}
            sparksState={state.sparks}
            sparks={report?.sparks ?? null}
            hideTitle
          />
        </div>

        {/* Breadth — variant 3. The strip of figures under its own fill. */}
        <div className={SPAN.full}>
          <SectionTitle variant={3} testId="section-title-breadth" hint="advancers, extremes, averages">
            Breadth
          </SectionTitle>
          <BreadthTiles state={state.breadth} breadth={report?.breadth ?? null} />
        </div>

        {/* The 2:1 pair: movers (variant 5) against the map (variant 4). One
            PairingBox, two weights — the proportion the page's columns already
            spoke of, now one object. */}
        <div className={SPAN.full}>
          <PairingBox direction="row" parts={[2, 1]} gap={8} testId="movers-map-pair" className="min-w-0 max-[899px]:flex-col!">
            <div className="flex min-w-0 flex-col gap-2">
              <SectionTitle
                variant={5}
                testId="section-title-movers"
                hint="gainers, highs, overbought, lows"
                right={
                  state.movers.kind === "ready" && movers !== null && movers.total > 0 ? (
                    <div className="flex shrink-0 items-baseline gap-2">
                      <span className="text-[10px] text-fg-subtle">{movers.total}</span>
                      <Link
                        to="/screener"
                        className="text-[10px] text-accent-text hover:underline"
                        data-testid="movers-see-all"
                      >
                        See all
                      </Link>
                    </div>
                  ) : null
                }
              >
                Movers
              </SectionTitle>
              <MoversBlock state={state.movers} movers={movers} hideTitle />
            </div>
            <div className="flex min-w-0 flex-col gap-2">
              <SectionTitle variant={4} testId="section-title-map" hint="one day">
                Maps
              </SectionTitle>
              <MapBlock state={state.map} map={report?.map ?? null} hideTitle />
            </div>
          </PairingBox>
        </div>

        {/* Crypto — variant 8, its own read of three crypto documents. */}
        <div className={SPAN.full}>
          <SectionTitle
            variant={8}
            testId="section-title-crypto"
            hint="coins and the market pulse"
            right={
              <Link
                to="/crypto"
                className="shrink-0 text-[10px] text-accent-text hover:underline"
                data-testid="crypto-see-all"
              >
                Crypto page
              </Link>
            }
          >
            Crypto
          </SectionTitle>
          <CryptoBlock
            status={crypto.status}
            rows={crypto.rows}
            global={crypto.global}
            missing={crypto.missing}
            measured={crypto.measured}
            error={crypto.error}
            onRetry={crypto.retry}
          />
        </div>

        {/* The news pairing: headlines (variant 6) against insider filings
            (variant 7) — the headlines bigger at 2:1, the filings compact and
            every name in them a link. */}
        <div className={SPAN.full}>
          <PairingBox direction="row" parts={[2, 1]} gap={8} testId="news-insider-pair" className="min-w-0 max-[899px]:flex-col!">
            <div className="flex min-w-0 flex-col gap-2">
              <SectionTitle
                variant={6}
                testId="section-title-news"
                right={newsCount === null ? null : <span className="shrink-0 text-[10px] text-fg-subtle">{newsCount}</span>}
              >
                Headlines
              </SectionTitle>
              <NewsTable state={state.news} news={news} hideTitle className="min-h-[260px]" />
            </div>
            <div className="flex min-w-0 flex-col gap-2">
              <SectionTitle
                variant={7}
                testId="section-title-insider"
                hint="Form 4"
                right={
                  <Link
                    to="/insider"
                    className="shrink-0 text-[10px] text-accent-text hover:underline"
                    data-testid="insider-see-all"
                  >
                    Insider page
                  </Link>
                }
              >
                Insider
              </SectionTitle>
              <InsiderLinksBlock
                state={state.filings}
                rows={report?.filings ?? null}
                className="min-h-[260px]"
              />
            </div>
          </PairingBox>
        </div>

        {/* Signals beside the side tables: four derived counts and the passing
            checks a reader glances at. Neither is one of the page's titled
            kinds — they carry their names in their own tables, as before. */}
        <div className={`${SPAN.full} grid grid-cols-1 gap-2 min-[900px]:grid-cols-12`}>
          <SignalsBlock state={state.movers} movers={movers} className="min-[900px]:col-span-4" />
          <div className="min-w-0 min-[900px]:col-span-8">
            <SideColumn
              futures={report?.futures ?? null}
              forex={report?.forex ?? null}
              insiderTop={report?.insiderTop ?? null}
              economic={report?.economic ?? null}
            />
          </div>
        </div>
      </div>
    </main>
  );
}
