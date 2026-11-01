import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { json, wireFor } from "./__fixtures__/chart";
import type { ChartRange } from "./types";
import { chartPath, useChartData } from "./useChartData";

/**
 * The read: one request per (symbol, range), an abort on every change, and no
 * stale answer painted over a newer one.
 */

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("chartPath", () => {
  it("encodes the symbol and carries the range", () => {
    expect(chartPath("^GSPC", "1d")).toBe("/market/chart/%5EGSPC?range=1d");
    expect(chartPath(" aapl ", "1y")).toBe("/market/chart/AAPL?range=1y");
    expect(chartPath("BRK.B", "6mo")).toBe("/market/chart/BRK.B?range=6mo");
  });
});

describe("useChartData", () => {
  it("asks the chart route once and parses the series", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(json(wireFor("AAPL", "1d"))));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useChartData("AAPL", "1d"));
    expect(result.current.status).toBe("loading");

    await waitFor(() => expect(result.current.status).toBe("ready"));
    const [path] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(path).toBe("/market/chart/AAPL?range=1d");
    expect(result.current.series?.bars.length).toBeGreaterThan(0);
    expect(result.current.series?.range).toBe("1d");
    expect(result.current.series?.receivedAt).toBeGreaterThan(0);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("turns a 404 into 'No chart for X', not an empty series", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() => Promise.resolve(json({ errorMessage: "unknown symbol" }, 404))),
    );

    const { result } = renderHook(() => useChartData("NOPE", "1d"));
    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current.error).toBe("No chart for NOPE");
    expect(result.current.statusCode).toBe(404);
    expect(result.current.series).toBeNull();
  });

  it("aborts the first request when the range changes", async () => {
    const signals: AbortSignal[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((_path: string, init: RequestInit) => {
        signals.push(init.signal as AbortSignal);
        return new Promise<Response>(() => {});
      }),
    );

    const { rerender } = renderHook(({ range }: { range: ChartRange }) => useChartData("AAPL", range), {
      initialProps: { range: "1d" as ChartRange },
    });
    await waitFor(() => expect(signals).toHaveLength(1));

    rerender({ range: "5d" });
    await waitFor(() => expect(signals).toHaveLength(2));

    expect(signals[0]?.aborted).toBe(true);
    expect(signals[1]?.aborted).toBe(false);
  });

  it("ignores a straggler that resolves after the abort", async () => {
    let releaseFirst: (response: Response) => void = () => {};
    const first = new Promise<Response>((resolve) => {
      releaseFirst = resolve;
    });
    const second = Promise.resolve(json(wireFor("AAPL", "5d")));
    const fetchMock = vi.fn().mockImplementationOnce(() => first).mockImplementationOnce(() => second);
    vi.stubGlobal("fetch", fetchMock);

    const { result, rerender } = renderHook(({ range }: { range: ChartRange }) => useChartData("AAPL", range), {
      initialProps: { range: "1d" as ChartRange },
    });
    rerender({ range: "5d" });
    await waitFor(() => expect(result.current.status).toBe("ready"));

    releaseFirst(json(wireFor("AAPL", "1d")));
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(result.current.series?.range).toBe("5d");
  });

  it("relabels a malformed document as an error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => Promise.resolve(json({ bars: "nope" }))));
    const { result } = renderHook(() => useChartData("AAPL", "1d"));
    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current.error).toContain("Unreadable chart");
    expect(result.current.series).toBeNull();
  });

  it("retries on request", async () => {
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(() => Promise.resolve(json({ errorMessage: "upstream offline" }, 503)))
      .mockImplementationOnce(() => Promise.resolve(json(wireFor("AAPL", "1d"))));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useChartData("AAPL", "1d"));
    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current.error).toBe("upstream offline");

    result.current.reload();
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
