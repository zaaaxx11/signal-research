/**
 * Insider: real Form 4 filings, one company at a time.
 *
 * This page used to rank companies by a vendor field called `insider_trans`.
 * That field cannot be read as a flow: TSLA 76.0, META 45.9, GME 8.7 — a
 * mixture of ownership levels, stale snapshots and outright junk, with no unit
 * that survives contact with a second company. A table headed "net insider
 * buying" built on it would publish an interpretation the data does not
 * support, so the ranking is gone.
 *
 * What is left is the evidence: SEC Form 4 filings, which name the owner, the
 * date, the transaction and the amount. They are read per company (the EDGAR
 * path is one company at a time), so this page offers two ways in — the most
 * active companies, and a symbol field for anything else — and then shows that
 * company's filings.
 *
 * The page never prints a total it did not receive. The filings panel reports
 * the endpoint's own count and marks `shown of total` when the two disagree.
 */

import * as React from "react";

import { formatCount } from "@/breadth/format";
import { LogoMark } from "@/grid/LogoMark";
import { getJson, ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

import { InsiderDetail } from "./InsiderDetail";

/** One row of the way in: a company a reader can open. */
interface Entry {
  readonly symbol: string;
  readonly name: string | null;
}

const SIGNALS_PATH = "/market/signals?region=us&per=20";
/** The one group used as an entry list. */
const ENTRY_GROUP = "most_active";

interface SignalsShape {
  readonly groups?: readonly {
    readonly id?: unknown;
    readonly rows?: readonly { readonly symbol?: unknown; readonly name?: unknown }[];
  }[];
}

/**
 * The most active companies, read from the one endpoint the home page already
 * uses. A missing group is an empty list, not an error: the symbol field is
 * still a complete way in.
 */
function readEntries(payload: unknown): Entry[] {
  const shape = payload as SignalsShape | null;
  const group = shape?.groups?.find((candidate) => candidate.id === ENTRY_GROUP);
  if (!group?.rows) return [];
  const seen = new Set<string>();
  const entries: Entry[] = [];
  for (const row of group.rows) {
    const symbol = typeof row.symbol === "string" ? row.symbol.trim().toUpperCase() : "";
    if (!symbol || seen.has(symbol)) continue;
    seen.add(symbol);
    entries.push({ symbol, name: typeof row.name === "string" ? row.name : null });
  }
  return entries;
}

type EntriesStatus = "loading" | "ready" | "error";

export function InsiderPage() {
  const [entries, setEntries] = React.useState<Entry[]>([]);
  const [status, setStatus] = React.useState<EntriesStatus>("loading");
  const [error, setError] = React.useState<string | null>(null);
  const [typed, setTyped] = React.useState("");
  const [selected, setSelected] = React.useState<string | null>(null);
  const [attempt, setAttempt] = React.useState(0);
  const triggerRef = React.useRef<HTMLButtonElement | null>(null);

  React.useEffect(() => {
    const controller = new AbortController();
    let live = true;
    setStatus("loading");
    setError(null);
    getJson(SIGNALS_PATH, controller.signal)
      .then((payload) => {
        if (!live) return;
        setEntries(readEntries(payload));
        setStatus("ready");
      })
      .catch((cause: unknown) => {
        if (!live) return;
        if (controller.signal.aborted) return;
        if (cause instanceof ApiError && cause.name === "AbortError") return;
        setError("The company list could not be loaded.");
        setStatus("error");
      });
    return () => {
      live = false;
      controller.abort();
    };
  }, [attempt]);

  const open = React.useCallback((symbol: string, trigger?: HTMLButtonElement | null) => {
    triggerRef.current = trigger ?? null;
    setSelected(symbol);
  }, []);

  const close = React.useCallback(() => {
    setSelected(null);
    // Focus goes back to the row that opened the panel; a reader who opened it
    // with the keyboard must not be dropped at the top of the document.
    triggerRef.current?.focus();
  }, []);

  // Escape closes the filings panel and puts focus back on the row that opened
  // it. It is a window listener rather than a handler on the panel because the
  // reader's focus may be anywhere in the page when they change their mind.
  React.useEffect(() => {
    if (selected === null) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      close();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [selected, close]);

  const pick = (event: React.FormEvent) => {
    event.preventDefault();
    const symbol = typed.trim().toUpperCase();
    if (!symbol) return;
    open(symbol, null);
    setTyped("");
  };

  return (
    <main className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <header className="shrink-0 border-b border-line bg-surface px-3 py-2">
        <h1 className="text-ui font-semibold text-fg">Insider filings</h1>
        <p className="mt-0.5 text-data text-fg-muted">
          Every row below is a Form 4 filing as reported to the SEC — who traded,
          when, and how much. Filings are read one company at a time.
        </p>
      </header>

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden xl:flex-row">
        <section
          aria-labelledby="insider-entries"
          className="flex min-h-0 w-full shrink-0 flex-col border-b border-line xl:w-[340px] xl:border-b-0 xl:border-r"
        >
          <div className="shrink-0 border-b border-line px-3 py-2">
            <form onSubmit={pick} className="flex items-center gap-2">
              <label htmlFor="insider-symbol" className="sr-only">
                Symbol
              </label>
              <input
                id="insider-symbol"
                value={typed}
                onChange={(event) => setTyped(event.target.value)}
                placeholder="Symbol, e.g. AAPL"
                autoComplete="off"
                spellCheck={false}
                className="h-7 w-full min-w-0 rounded-[3px] border border-line bg-bg px-2 text-data text-fg placeholder:text-fg-subtle focus:border-accent focus:outline-none"
              />
              <button
                type="submit"
                className="h-7 shrink-0 rounded-[3px] border border-line px-2 text-data text-fg-muted hover:text-fg"
              >
                Open
              </button>
            </form>
          </div>

          <h2 id="insider-entries" className="shrink-0 px-3 pt-2 text-label uppercase tracking-[0.08em] text-fg-subtle">
            Most active
          </h2>

          <div className="min-h-0 flex-1 overflow-y-auto px-1.5 pb-2">
            {status === "loading" ? (
              <p role="status" className="px-1.5 py-2 text-data text-fg-subtle">
                Loading companies…
              </p>
            ) : status === "error" ? (
              <div className="px-1.5 py-2">
                <p className="text-data text-fg-muted">{error}</p>
                <button
                  type="button"
                  onClick={() => setAttempt((value) => value + 1)}
                  className="mt-1 text-data text-accent-text hover:text-fg"
                >
                  Try again
                </button>
              </div>
            ) : entries.length === 0 ? (
              <p className="px-1.5 py-2 text-data text-fg-subtle">
                No companies to list. Type a symbol instead.
              </p>
            ) : (
              <ul>
                {entries.map((entry) => (
                  <li key={entry.symbol}>
                    <button
                      type="button"
                      data-testid={`insider-entry-${entry.symbol}`}
                      aria-current={selected === entry.symbol ? "true" : undefined}
                      onClick={(event) => open(entry.symbol, event.currentTarget)}
                      className={cn(
                        "flex w-full items-center gap-2 rounded-[2px] px-1.5 py-1 text-left hover:bg-surface-raised",
                        selected === entry.symbol && "bg-surface-raised",
                      )}
                    >
                      <LogoMark symbol={entry.symbol} size={14} />
                      <span className="w-[52px] shrink-0 font-mono text-data font-semibold text-fg">
                        {entry.symbol}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-data text-fg-muted" title={entry.name ?? undefined}>
                        {entry.name ?? "—"}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {entries.length > 0 ? (
            <p className="shrink-0 border-t border-line px-3 py-1 text-label text-fg-subtle">
              {formatCount(entries.length)} companies
            </p>
          ) : null}
        </section>

        <section aria-label="Filings" className="flex min-h-0 flex-1 flex-col overflow-hidden">
          {selected === null ? (
            <p className="p-3 text-data text-fg-subtle">
              Choose a company to read its filings.
            </p>
          ) : (
            <InsiderDetail symbol={selected} onClose={close} />
          )}
        </section>
      </div>
    </main>
  );
}
