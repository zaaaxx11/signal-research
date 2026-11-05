import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { json } from "./__fixtures__/canvas";
import { SMALL_WIRE } from "./__fixtures__/payload";
import { mapPath, useMapData, type Timeframe } from "./useMapData";

/**
 * The read: one request, an abort on every change, and no stale answer.
 *
 * Adversarial (c) lives here — a 250 KB payload is big enough that a second
 * request can land before the first, and two answers for one state would either
 * paint the older map or paint twice.
 */

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useMapData", () => {
  it("asks the map route once, with the region and timeframe", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(json(SMALL_WIRE)));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useMapData({ region: "us", timeframe: "1d" }));
    expect(result.current.status).toBe("loading");

    await waitFor(() => expect(result.current.status).toBe("ready"));
    const [path] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(path).toBe("/market/map?region=us&timeframe=1d&shape=2");
    expect(result.current.payload?.sectors).toHaveLength(3);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("builds the path from the query, encoded", () => {
    expect(mapPath("us", "1d")).toBe("/market/map?region=us&timeframe=1d&shape=2");
    expect(mapPath("eu", "1w")).toBe("/market/map?region=eu&timeframe=1w&shape=2");
  });

  it("aborts the first request when the timeframe changes", async () => {
    const signals: AbortSignal[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((_path: string, init: RequestInit) => {
        signals.push(init.signal as AbortSignal);
        return new Promise<Response>(() => {});
      }),
    );

    const { rerender } = renderHook(
      ({ timeframe }: { timeframe: Timeframe }) => useMapData({ timeframe }),
      { initialProps: { timeframe: "1d" } },
    );
    await waitFor(() => expect(signals).toHaveLength(1));

    rerender({ timeframe: "1w" });
    await waitFor(() => expect(signals).toHaveLength(2));

    expect(signals[0]?.aborted).toBe(true);
    expect(signals[1]?.aborted).toBe(false);
  });

  it("ignores a straggler that resolves after the abort", async () => {
    let releaseFirst: (response: Response) => void = () => {};
    const first = new Promise<Response>((resolve) => {
      releaseFirst = resolve;
    });
    const second = Promise.resolve(json(SMALL_WIRE));
    const fetchMock = vi.fn().mockImplementationOnce(() => first).mockImplementationOnce(() => second);
    vi.stubGlobal("fetch", fetchMock);

    const { result, rerender } = renderHook(
      ({ timeframe }: { timeframe: Timeframe }) => useMapData({ timeframe }),
      { initialProps: { timeframe: "1d" } },
    );
    rerender({ timeframe: "1w" });
    await waitFor(() => expect(result.current.status).toBe("ready"));

    // The first answer arrives afterwards, with a document that would be a lie.
    releaseFirst(json({ ...SMALL_WIRE, mapped: 1, sectors: [] }));
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(result.current.payload?.mapped).toBe(7);
    expect(result.current.payload?.sectors).toHaveLength(3);
  });

  it("surfaces the API's message and retries on request", async () => {
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(() =>
        Promise.resolve(json({ errorMessage: "scanner offline" }, 503)),
      )
      .mockImplementationOnce(() => Promise.resolve(json(SMALL_WIRE)));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useMapData());
    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current.error).toBe("scanner offline");
    expect(result.current.statusCode).toBe(503);

    result.current.reload();
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("reports an unreadable document as an error, not as an empty map", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => Promise.resolve(json({ sectors: "nope" }))));
    const { result } = renderHook(() => useMapData());
    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current.error).toContain("Unreadable map");
    expect(result.current.payload).toBeNull();
  });
});