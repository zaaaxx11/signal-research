import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  fundamentalsPayload,
  insiderPayload,
  json,
  newsPayload,
  screensPayload,
  selectPayload,
  stubApi,
} from "./__fixtures__/detail";
import { DetailPanel } from "./DetailPanel";
import { resetSnapshotCache } from "./useDetail";

/**
 * F2 — the snapshot is a property of the session, not of the symbol.
 *
 * The seam report measured three byte-identical `GET /screens?source=tradingview`
 * calls for AAPL → NVDA → MSFT, each one serialised in front of the ownership
 * POST. The id never changes inside a session, so it may be read once; what
 * must not happen is the cache outliving the snapshot it names, so a `404`
 * from the select route (the API's "that snapshot is gone") drops it and costs
 * exactly one re-read.
 */

const SNAPSHOT_URL = "/screens?source=tradingview";

function Harness(): React.ReactElement {
  const [symbol, setSymbol] = React.useState<string | null>("AAPL");
  return (
    <div>
      {["AAPL", "NVDA", "MSFT"].map((name) => (
        <button key={name} type="button" onClick={() => setSymbol(name)}>
          open {name}
        </button>
      ))}
      <DetailPanel symbol={symbol} onClose={() => setSymbol(null)} />
    </div>
  );
}

function requests(fetchMock: ReturnType<typeof vi.fn>, needle: string): string[] {
  return fetchMock.mock.calls
    .map((call) => String((call as unknown[])[0]))
    .filter((url) => url.includes(needle));
}

function selectBody(init?: RequestInit): Record<string, unknown> {
  return JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>;
}

beforeEach(() => {
  resetSnapshotCache();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("F2 — one snapshot read per session", () => {
  it("three row opens send one GET /screens and three selections", async () => {
    const selects: Array<Record<string, unknown>> = [];
    const fetchMock = stubApi({
      fundamentals: () => json(fundamentalsPayload()),
      insider: () => json(insiderPayload()),
      news: () => json(newsPayload()),
      screens: () => json(screensPayload()),
      select: (_url, init) => {
        const body = selectBody(init);
        selects.push(body);
        return json(selectPayload((body["symbols"] as string[])[0] ?? "AAPL"));
      },
    });
    const user = userEvent.setup();

    render(<Harness />);
    await screen.findByTestId("fundamentals-loaded");

    await user.click(screen.getByRole("button", { name: "open NVDA" }));
    await waitFor(() => expect(selects).toHaveLength(2));
    await user.click(screen.getByRole("button", { name: "open MSFT" }));
    await waitFor(() => expect(selects).toHaveLength(3));

    expect(requests(fetchMock, SNAPSHOT_URL)).toHaveLength(1);
    expect(selects.map((body) => body["symbols"])).toEqual([["AAPL"], ["NVDA"], ["MSFT"]]);
    // Every selection resolved against the one id the session read.
    expect(new Set(selects.map((body) => body["snapshot_id"])).size).toBe(1);
    expect(selects[0]?.["snapshot_id"]).toBe("scr_us_market_cap_test");
  });

  it("drops the cached id when the select route says the snapshot is gone", async () => {
    let screenReads = 0;
    const selectIds: unknown[] = [];
    const fetchMock = stubApi({
      fundamentals: () => json(fundamentalsPayload()),
      insider: () => json(insiderPayload()),
      news: () => json(newsPayload()),
      screens: () => {
        screenReads += 1;
        return json(screensPayload(`scr_${screenReads}`));
      },
      select: (_url, init) => {
        const body = selectBody(init);
        selectIds.push(body["snapshot_id"]);
        if (body["snapshot_id"] === "scr_1") {
          return json({ errorMessage: "snapshot scr_1 does not exist" }, 404);
        }
        return json(selectPayload((body["symbols"] as string[])[0] ?? "AAPL"));
      },
    });

    render(<DetailPanel symbol="AAPL" onClose={vi.fn()} />);
    await userEvent.setup().click(screen.getByRole("tab", { name: "Ownership" }));

    // The 404 is an expired snapshot, not an empty symbol: one re-read, one
    // retry, and the ownership tab renders.
    await screen.findByTestId("ownership-loaded", undefined, { timeout: 3_000 });
    expect(screenReads).toBe(2);
    expect(selectIds).toEqual(["scr_1", "scr_2"]);
    expect(requests(fetchMock, SNAPSHOT_URL)).toHaveLength(2);

    // Bounded: nothing keeps retrying after the second read succeeded.
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screenReads).toBe(2);
  });
});
