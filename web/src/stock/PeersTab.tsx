/**
 * Peers tab: a full table of same-sector companies.
 *
 * `GET /engine/peers/{symbol}` answers the industry peer group. Each row draws
 * the symbol (linked), company name, market cap and percent change. An absent
 * value is the em dash, never a zero that reads as "flat".
 *
 * The tab owns its own read. It does not reuse the overview's ten-peer
 * preview — this is the full list, and the reader who clicked "Peers" wants
 * every row the engine can name.
 */

import * as React from "react";

import { formatMoney } from "@/grid/format";
import { changeTone, formatChange } from "@/home/signals";
import { getJson } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Link } from "@/shell/routes";

import { parsePeers, type PeersData } from "./useTicker";

async function loadAllPeers(symbol: string, signal: AbortSignal): Promise<PeersData | null> {
  const body = await getJson(`/engine/peers/${encodeURIComponent(symbol)}`, signal);
  return parsePeers(body);
}

export interface PeersTabProps {
  readonly symbol: string;
}

export function PeersTab({ symbol }: PeersTabProps): React.ReactElement {
  const [attempt, setAttempt] = React.useState(0);
  const [state, setState] = React.useState<
    | { status: "loading" }
    | { status: "error"; error: string }
    | { status: "ready"; data: PeersData }
    | { status: "empty" }
  >({ status: "loading" });

  React.useEffect(() => {
    const controller = new AbortController();
    let live = true;
    setState({ status: "loading" });

    loadAllPeers(symbol, controller.signal)
      .then((data) => {
        if (!live) return;
        if (data === null || data.peers.length === 0) {
          setState({ status: "empty" });
        } else {
          setState({ status: "ready", data });
        }
      })
      .catch((error: unknown) => {
        if (!live || controller.signal.aborted) return;
        if (error instanceof DOMException && error.name === "AbortError") return;
        setState({
          status: "error",
          error: error instanceof Error ? error.message : "The peers request failed.",
        });
      });

    return () => {
      live = false;
      controller.abort();
    };
  }, [symbol, attempt]);

  const retry = React.useCallback(() => setAttempt((n) => n + 1), []);

  if (state.status === "loading") {
    return (
      <div aria-busy="true" data-testid="peers-loading" className="flex flex-col gap-0.5">
        <span className="sr-only">Loading peers for {symbol}…</span>
        {Array.from({ length: 10 }, (_, index) => (
          <div key={index} className="h-5 border-b border-line/60 last:border-b-0" />
        ))}
      </div>
    );
  }

  if (state.status === "error") {
    return (
      <div role="alert" data-testid="peers-error" className="rounded-sm border border-line border-l-2 border-l-alert bg-surface-sunken p-2">
        <p className="text-data font-medium text-fg">Could not load peers.</p>
        <p className="mt-1 break-words text-data text-fg-muted">{state.error}</p>
        <button
          type="button"
          onClick={retry}
          className="focus-ring mt-2 h-6 rounded-sm border border-line-control px-2 text-data text-fg-muted hover:text-fg"
        >
          Retry
        </button>
      </div>
    );
  }

  if (state.status === "empty") {
    return (
      <p data-testid="peers-empty" className="text-data text-fg-muted">
        No industry peers are in the loaded identity master for {symbol}.
      </p>
    );
  }

  return (
    <div data-testid="peers" className="overflow-x-auto">
      <table className="w-full border-collapse text-data">
        <caption className="sr-only">
          Industry peers for {state.data.symbol}, {state.data.peers.length} companies.
          Market cap and change are shown when the endpoint reports them.
        </caption>
        <thead>
          <tr className="border-b border-line">
            <th scope="col" className="py-1 pr-3 text-left text-label font-medium uppercase tracking-[0.06em] text-fg-subtle">
              Symbol
            </th>
            <th scope="col" className="py-1 pr-3 text-left text-label font-medium uppercase tracking-[0.06em] text-fg-subtle">
              Name
            </th>
            <th scope="col" className="py-1 pr-3 text-right text-label font-medium uppercase tracking-[0.06em] text-fg-subtle">
              Market cap
            </th>
            <th scope="col" className="py-1 text-right text-label font-medium uppercase tracking-[0.06em] text-fg-subtle">
              Change
            </th>
          </tr>
        </thead>
        <tbody>
          {state.data.peers.map((peer) => {
            const tone = peer.changePercent !== null ? changeTone(peer.changePercent) : "flat";
            return (
              <tr key={peer.symbol} className="h-5 border-b border-line/70 last:border-b-0 hover:bg-row-hover">
                <td className="nums whitespace-nowrap pr-3 font-mono font-medium">
                  <Link
                    to={`/stock/${encodeURIComponent(peer.symbol)}`}
                    data-peer={peer.symbol}
                    className="text-accent-text hover:text-brand"
                  >
                    {peer.symbol}
                  </Link>
                </td>
                <td className="max-w-[260px] truncate pr-3 text-fg" title={peer.name}>
                  {peer.name}
                </td>
                <td className="nums whitespace-nowrap pr-3 text-right font-mono text-fg-muted">
                  {peer.marketCap === null ? "—" : formatMoney(peer.marketCap, true)}
                </td>
                <td
                  className={cn(
                    "nums whitespace-nowrap text-right font-mono",
                    tone === "up" && "text-up",
                    tone === "down" && "text-down",
                    tone === "flat" && "text-fg-muted",
                  )}
                >
                  {peer.changePercent === null ? "—" : formatChange(peer.changePercent)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}