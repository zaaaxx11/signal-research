/**
 * Home: two columns of signal cards.
 *
 * Eleven groups the market can answer, two it cannot, all thirteen in one grid
 * fed by one request. There is no page title in the flow and no summary bar:
 * the nav says where the reader is, and the cards are the page. The heading
 * exists for assistive technology only, because a `<main>` without a name is a
 * region a screen-reader user has to enter to identify.
 *
 * The grid collapses to one column below 1280 px. At that width a card's five
 * columns (ticker, last, change, volume, signal) are still readable; two of
 * them side by side would be a 600 px table in a 620 px viewport.
 */

import { SignalCard } from "./SignalCard";
import { SIGNAL_GROUPS } from "./signals";
import { useSignals } from "./useSignals";

export function HomePage() {
  const signals = useSignals();

  return (
    <main
      data-testid="home"
      className="min-h-0 flex-1 overflow-y-auto bg-bg px-2 py-2"
    >
      <h1 className="sr-only">Market signals</h1>

      {signals.status === "error" ? (
        <div
          role="alert"
          data-testid="signals-error"
          className="mb-2 flex items-center justify-between gap-3 rounded-md border border-line border-l-2 border-l-alert bg-surface px-3 py-1.5"
        >
          <p className="min-w-0 truncate text-data text-fg">
            Market signals did not load. {signals.error}
          </p>
          <button
            type="button"
            onClick={signals.retry}
            className="shrink-0 rounded-sm border border-line px-2 py-0.5 text-data text-fg-muted transition-colors hover:border-line-strong hover:text-fg"
          >
            Retry
          </button>
        </div>
      ) : null}

      <div
        data-testid="signal-grid"
        className="grid grid-cols-1 gap-2 xl:grid-cols-2"
      >
        {SIGNAL_GROUPS.map((group) => (
          <SignalCard
            key={group.key}
            group={group}
            rows={signals.report?.groups[group.key] ?? []}
            loading={signals.status === "loading"}
            errored={signals.status === "error"}
            note={signals.report?.notes[group.key] ?? null}
          />
        ))}
      </div>
    </main>
  );
}
