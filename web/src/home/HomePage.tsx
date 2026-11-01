/**
 * Home: one request, six blocks, and a footer that says the name.
 *
 * The operator's layout, in order: a headline bar; a strip of index cards and
 * three spark boxes; six breadth tiles; the movers table beside the 1-day map
 * (2:1); three small boxes — signals, headlines, majors; the insider strip; a
 * plain footer.
 *
 * It is one CSS grid of twelve columns from 900 px and one column below that.
 * Twelve, not four, because the 2:1 pair and the 5/4/3 row are the page's two
 * real relationships, and a grid that can express them in spans is a grid whose
 * breakpoints a reader can reason about. Below 900 px every block takes the full
 * width: a 2:1 pair at 720 px is a 480 px table beside a 240 px canvas, and both
 * halves would truncate the one fact they exist to show.
 *
 * Three rules the page enforces for every block, so no block has to remember
 * them: a block that failed says so in its own card (there is no global blank
 * page), a block that is loading draws a skeleton of its own shape, and a block
 * whose source is `null` prints the reason from the payload — never a zero.
 *
 * The `h1` is visually hidden on purpose: the nav says where the reader is, and
 * a visible "Home" heading above a dashboard is a label on a door you can see
 * through. It stays in the DOM because a `<main>` without a name is a region a
 * screen-reader user has to enter to identify, and heading navigation needs a
 * first heading to start from.
 */

import * as React from "react";

import { blockState } from "./home";
import { useHome } from "./useHome";
import { BreadthTiles } from "./blocks/BreadthTiles";
import { HomeFooter } from "./blocks/HomeFooter";
import { IndexStrip } from "./blocks/IndexStrip";
import { InsiderStrip } from "./blocks/InsiderStrip";
import { MapBlock } from "./blocks/MapBlock";
import { MoversBlock } from "./blocks/MoversBlock";
import { NewsTable } from "./blocks/NewsTable";
import { SignalsBlock } from "./blocks/SignalsBlock";

/**
 * Spans, as literal class strings.
 *
 * Tailwind reads source text, so a span assembled at run time (`col-span-${n}`)
 * would compile to nothing. The six names below are the page's whole layout
 * vocabulary and each one is written once.
 */
const SPAN = {
  full: "min-[900px]:col-span-12",
  movers: "min-[900px]:col-span-8",
  map: "min-[900px]:col-span-4",
} as const;

export function HomePage() {
  const { status, report, error, retry } = useHome("us");

  const state = React.useMemo(
    () => ({
      indices: blockState(status, report, "indices", "Indices"),
      breadth: blockState(status, report, "breadth", "Breadth"),
      sparks: blockState(status, report, "sparks", "Tickers"),
      movers: blockState(status, report, "movers", "Movers"),
      map: blockState(status, report, "map", "Map"),
      news: blockState(status, report, "news", "Headlines"),
      filings: blockState(status, report, "filings", "Filings"),
    }),
    [status, report],
  );

  return (
    <main data-testid="home" className="min-h-0 flex-1 overflow-y-auto bg-bg px-2 py-2">
      <h1 className="sr-only">Market overview</h1>

      {status === "error" ? (
        <div
          role="alert"
          data-testid="home-error"
          className="mb-2 flex items-center justify-between gap-3 rounded-md border border-line border-l-2 border-l-alert bg-surface px-3 py-1.5"
        >
          <p className="min-w-0 truncate text-[12px] text-fg">
            The market dashboard did not load. {error}
          </p>
          <button
            type="button"
            onClick={retry}
            className="shrink-0 rounded-sm border border-line px-2 py-0.5 text-[12px] text-fg-muted transition-colors hover:border-line-strong hover:text-fg"
          >
            Retry
          </button>
        </div>
      ) : null}

      <div
        data-testid="home-grid"
        data-status={status}
        aria-busy={status === "loading" ? "true" : undefined}
        className="grid grid-cols-1 gap-2 min-[900px]:grid-cols-12"
      >
        <IndexStrip
          indicesState={state.indices}
          indices={report?.indices ?? null}
          sparksState={state.sparks}
          sparks={report?.sparks ?? null}
          className={SPAN.full}
        />

        <BreadthTiles state={state.breadth} breadth={report?.breadth ?? null} className={SPAN.full} />

        <MoversBlock state={state.movers} movers={report?.movers ?? null} className={SPAN.movers} />
        <MapBlock state={state.map} map={report?.map ?? null} className={SPAN.map} />

        <SignalsBlock state={state.movers} movers={report?.movers ?? null} className={SPAN.full} />

        <InsiderStrip state={state.filings} filings={report?.filings ?? null} className={SPAN.full} />

        {/* The desk sits directly above the footer: one table, scrolled, full. */}
        <NewsTable state={state.news} news={report?.news ?? null} className={SPAN.full} />

        <div className={SPAN.full}>
          <HomeFooter />
        </div>
      </div>
    </main>
  );
}
