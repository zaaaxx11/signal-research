import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { requestBody, useScreens, useViewCatalogue, clearPageCache, type ScreensQuery } from "./screens";

/**
 * The data layer's promises, one test each:
 *
 * * a 200 stores the ETag, and the next refresh presents it;
 * * a 304 keeps the rows on screen and says it did not refresh them;
 * * a 422 surfaces the API's sentence, not a blank table;
 * * a new query aborts the old one and hides rows from the old view;
 * * with 100 rows out of order, nothing is merged by position.
 */

const TODAY: ScreensQuery = {
  view: "today",
  columns: ["price", "change"],
  limit: 20,
  offset: 0,
  sort: "market_cap",
};

const VALUE: ScreensQuery = {
  view: "value",
  columns: ["market_cap", "pe"],
  limit: 20,
  offset: 0,
  sort: "market_cap",
};

function payload(rows: Array<Record<string, unknown>>, overrides: Record<string, unknown> = {}) {
  return {
    mode: "criteria",
    region: "us",
    source: "tradingview",
    as_of: "2026-10-31T01:28:01.997369+00:00",
    sort: "market_cap",
    limit: 20,
    universe_total: 5600,
    total: 5600,
    returned: rows.length,
    columns: ["price"],
    rows,
    budget: { limit_bytes: 60000, bytes: 100, truncated: false, dropped_fields: [], rows_dropped: 0 },
    ...overrides,
  };
}

function answer(body: unknown, init: { status?: number; etag?: string } = {}) {
  return new Response(JSON.stringify(body), {
    status: init.status ?? 200,
    headers: {
      "content-type": "application/json",
      ...(init.etag ? { etag: init.etag } : {}),
    },
  });
}

function notModified(etag: string) {
  return new Response(null, { status: 304, headers: { etag } });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useScreens", () => {
  it("POSTs the selection and keeps the weak ETag", async () => {
    const fetchMock = vi.fn(async () => answer(payload([{ symbol: "AAPL", price: 344.63 }]), { etag: 'W/"select-1"' }));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useScreens(TODAY));
    await waitFor(() => expect(result.current.status).toBe("ready"));

    expect(result.current.rows).toEqual([{ symbol: "AAPL", price: 344.63 }]);
    expect(result.current.etag).toBe('W/"select-1"');
    expect(result.current.meta?.total).toBe(5600);

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/screens/select");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({
      mode: "criteria",
      region: "us",
      asset_class: "stock",
      columns: ["price", "change"],
      criteria: { limit: 20, offset: 0, filters: [], sort: "market_cap" },
    });
    expect((init.headers as Record<string, string>)["if-none-match"]).toBeUndefined();
  });

  it("labels a page with the offset it asked for when the wire omits it", async () => {
    // The live route does not echo `offset`. A second page that trusted the
    // body would print "1–20" while showing rows 21–40.
    const fetchMock = vi.fn(async () => answer(payload([{ symbol: "ADI", price: 250.1 }])));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useScreens({ ...TODAY, offset: 20 }));
    await waitFor(() => expect(result.current.status).toBe("ready"));

    expect(result.current.meta?.offset).toBe(20);
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(JSON.parse(String(init.body)).criteria.offset).toBe(20);
  });

  it("treats a 304 as 'keep the rows', sends If-None-Match and marks the page unrefreshed", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(answer(payload([{ symbol: "AAPL", price: 344.63 }]), { etag: 'W/"select-1"' }))
      .mockResolvedValueOnce(notModified('W/"select-1"'));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useScreens(TODAY));
    await waitFor(() => expect(result.current.status).toBe("ready"));

    act(() => result.current.refresh());
    await waitFor(() => expect(result.current.revalidated).toBe(true));

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const [, init] = fetchMock.mock.calls[1] as unknown as [string, RequestInit];
    expect((init.headers as Record<string, string>)["if-none-match"]).toBe('W/"select-1"');

    // The 304 must not clear the grid.
    expect(result.current.rows).toHaveLength(1);
    expect(result.current.rows[0]?.symbol).toBe("AAPL");
    expect(result.current.status).toBe("ready");
    expect(result.current.checkedAt).not.toBeNull();
  });

  it("surfaces the API's message on a 422", async () => {
    const message = "unknown sort 'bogus'; expected one of market_cap, turnover, gainers";
    const fetchMock = vi.fn(async () =>
      answer({ error: "invalid_selection", errorMessage: message }, { status: 422 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useScreens({ ...TODAY, sort: "market_cap" }));
    await waitFor(() => expect(result.current.status).toBe("error"));

    expect(result.current.error).toEqual({ status: 422, message });
    expect(result.current.rows).toHaveLength(0);
  });

  it("aborts the in-flight request when the view changes and ignores its late answer", async () => {
    let releaseToday: (value: Response) => void = () => {};
    let releaseValue: (value: Response) => void = () => {};
    const todayPromise = new Promise<Response>((resolve) => {
      releaseToday = resolve;
    });
    const valuePromise = new Promise<Response>((resolve) => {
      releaseValue = resolve;
    });

    const fetchMock = vi
      .fn()
      .mockImplementationOnce(() => todayPromise)
      .mockImplementationOnce(() => valuePromise);
    vi.stubGlobal("fetch", fetchMock);

    const { result, rerender } = renderHook(({ query }) => useScreens(query), {
      initialProps: { query: TODAY as ScreensQuery | null },
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [firstUrl, firstInit] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(firstUrl).toBe("/screens/select");
    const firstSignal = firstInit.signal as AbortSignal;
    expect(firstSignal.aborted).toBe(false);

    rerender({ query: VALUE });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    expect(firstSignal.aborted).toBe(true);
    expect(result.current.view).toBe("value");
    // The previous view's rows are gone before the new answer arrives.
    expect(result.current.rows).toHaveLength(0);

    // The aborted answer lands late; it must not paint today's rows under
    // value's header.
    await act(async () => {
      releaseToday(answer(payload([{ symbol: "AAPL", price: 344.63 }])));
      await Promise.resolve();
    });
    expect(result.current.rows).toHaveLength(0);

    await act(async () => {
      releaseValue(answer(payload([{ symbol: "NVDA", market_cap: 4.9e12, pe: 25.8 }]), { etag: 'W/"v"' }));
      await Promise.resolve();
    });
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.view).toBe("value");
    expect(result.current.rows[0]?.symbol).toBe("NVDA");
  });

  it("does not revalidate a view whose rows were cleared by a switch", async () => {
    // Switching away clears the rows; a tag from the previous visit must not
    // turn the fresh load into a 304 with nothing to paint.
    let releaseValue: (value: Response) => void = () => {};
    const valuePromise = new Promise<Response>((resolve) => {
      releaseValue = resolve;
    });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(answer(payload([{ symbol: "AAPL", price: 1 }]), { etag: 'W/"today"' }))
      .mockImplementationOnce(() => valuePromise)
      .mockResolvedValueOnce(answer(payload([{ symbol: "AAPL", price: 2 }]), { etag: 'W/"today-2"' }));
    vi.stubGlobal("fetch", fetchMock);

    const { result, rerender } = renderHook(({ query }) => useScreens(query), {
      initialProps: { query: TODAY as ScreensQuery | null },
    });
    await waitFor(() => expect(result.current.status).toBe("ready"));

    rerender({ query: VALUE });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    // A returning visit is answered from the page cache by default; this test
    // is about the tag a *request* may present, so the cache is emptied to
    // make the return visit a request again.
    clearPageCache();
    rerender({ query: TODAY });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));

    const [, third] = fetchMock.mock.calls[2] as unknown as [string, RequestInit];
    expect((third.headers as Record<string, string>)["if-none-match"]).toBeUndefined();

    await act(async () => {
      releaseValue(answer(payload([{ symbol: "NVDA" }]), { etag: 'W/"v"' }));
      await Promise.resolve();
    });
    await waitFor(() => expect(result.current.rows[0]?.price).toBe(2));
    expect(result.current.view).toBe("today");
  });

  it("clears the previous view's rows the moment the view changes", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(answer(payload([{ symbol: "AAPL", price: 344.63 }])))
      .mockImplementationOnce(() => new Promise<Response>(() => {}));
    vi.stubGlobal("fetch", fetchMock);

    const { result, rerender } = renderHook(({ query }) => useScreens(query), {
      initialProps: { query: TODAY as ScreensQuery | null },
    });
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.rows).toHaveLength(1);

    rerender({ query: VALUE });
    await waitFor(() => expect(result.current.view).toBe("value"));
    expect(result.current.rows).toHaveLength(0);
    expect(result.current.status).toBe("loading");
  });

  it("keeps rows in wire order and never merges by index", async () => {
    const rows = [
      { symbol: "ZZZ", price: 9.5 },
      { symbol: "AAA", price: 1.25 },
      { symbol: "MMM", price: 3.75 },
    ];
    const fetchMock = vi.fn(async () => answer(payload(rows)));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useScreens(TODAY));
    await waitFor(() => expect(result.current.status).toBe("ready"));

    // A row is its own map: no positional lookup, no re-sorting behind the
    // server's back. Symbol ZZZ still carries ZZZ's price.
    expect(result.current.rows.map((row) => [row.symbol, row.price])).toEqual([
      ["ZZZ", 9.5],
      ["AAA", 1.25],
      ["MMM", 3.75],
    ]);
  });

  it("drops rows that cannot be keyed by a symbol", async () => {
    const fetchMock = vi.fn(async () =>
      answer(payload([{ symbol: "AAPL", price: 1 }, { price: 2 }, { symbol: "" }])),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useScreens(TODAY));
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.rows).toHaveLength(1);
  });

  it("does nothing while the view catalogue is still loading", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useScreens(null));
    expect(result.current.status).toBe("idle");
    expect(result.current.rows).toHaveLength(0);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

/**
 * F1 — a `304` may only be honoured when the rows on screen are the answer to
 * *this* request. The auditor's three-click reproduction is a server that is
 * right and a client that holds the wrong body under a valid tag, so these
 * tests stand in a server that does exactly what `POST /screens/select` does:
 * the tag is the canonical request, and only a matching tag is a 304.
 *
 * Every assertion about the tag is an assertion on the request's
 * `if-none-match` header, never on rendered text.
 */
function conditionalServer() {
  const fetchMock = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body ?? "{}")) as {
      criteria?: { sort?: string; offset?: number; limit?: number; filters?: unknown[] };
    };
    const criteria = body.criteria ?? {};
    const etag = `W/"${JSON.stringify(criteria)}"`;
    const headers = (init?.headers ?? {}) as Record<string, string>;
    if (headers["if-none-match"] === etag) {
      return new Response(null, { status: 304, headers: { etag } });
    }
    // The row's symbol names the query that produced it, so a page that shows
    // another query's rows is visible in the data as well as in the header.
    const rows = [
      { symbol: `${criteria.sort ?? "default"}@${criteria.offset ?? 0}`, price: 1 },
    ];
    return answer(payload(rows, { sort: criteria.sort ?? null }), { etag });
  });
  return fetchMock;
}

function headerOf(call: [unknown, RequestInit] | undefined): Record<string, string> {
  return (call?.[1]?.headers ?? {}) as Record<string, string>;
}

describe("useScreens — the page cache", () => {
  it("answers a page it has already seen without a request", async () => {
    const fetchMock = conditionalServer();
    vi.stubGlobal("fetch", fetchMock);

    const page1: ScreensQuery = { ...TODAY, offset: 0 };
    const page2: ScreensQuery = { ...TODAY, offset: 20 };

    const { result, rerender } = renderHook(({ query }) => useScreens(query), {
      initialProps: { query: page1 as ScreensQuery | null },
    });
    await waitFor(() => expect(result.current.status).toBe("ready"));

    rerender({ query: page2 });
    await waitFor(() => expect(result.current.meta?.offset).toBe(20));
    expect(fetchMock).toHaveBeenCalledTimes(2);

    rerender({ query: page1 });
    // No third request: the page is drawn from the tab's own cache, in the
    // commit that changed the query.
    await waitFor(() => expect(result.current.rows[0]?.symbol).toBe("market_cap@0"));
    expect(result.current.fromCache).toBe(true);
    expect(result.current.status).toBe("ready");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("reads the server again when the reader asks to refresh", async () => {
    const fetchMock = conditionalServer();
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useScreens({ ...TODAY, offset: 0 }));
    await waitFor(() => expect(result.current.status).toBe("ready"));

    await act(async () => {
      result.current.refresh();
      await Promise.resolve();
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    // A refresh is not a cache read, even though the rows are identical.
    expect(result.current.fromCache).toBe(false);
  });
});

describe("useScreens — the tag belongs to the rows (F1)", () => {
  it("sort A → B → A never presents A's tag while B's rows are on screen", async () => {
    const fetchMock = conditionalServer();
    vi.stubGlobal("fetch", fetchMock);

    const sortA: ScreensQuery = { ...TODAY, sort: "market_cap" };
    const sortB: ScreensQuery = { ...TODAY, sort: "turnover" };

    const { result, rerender } = renderHook(({ query }) => useScreens(query), {
      initialProps: { query: sortA as ScreensQuery | null },
    });
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.rows[0]?.symbol).toBe("market_cap@0");

    rerender({ query: sortB });
    await waitFor(() => expect(result.current.rows[0]?.symbol).toBe("turnover@0"));

    // Return to A as a request, so the tag discipline is what is under test
    // and not the page cache (which is proved by its own test below).
    clearPageCache();
    rerender({ query: sortA });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));

    // The rows on screen are turnover's. Market cap's tag is valid for the
    // *request* and a lie about the *body*, so it must not be sent.
    expect(headerOf(fetchMock.mock.calls[2] as [unknown, RequestInit])["if-none-match"]).toBeUndefined();

    await waitFor(() => expect(result.current.rows[0]?.symbol).toBe("market_cap@0"));
    expect(result.current.revalidated).toBe(false);
  });

  it("Next → Prev does not answer page 1 with page 2's rows", async () => {
    const fetchMock = conditionalServer();
    vi.stubGlobal("fetch", fetchMock);

    const page1: ScreensQuery = { ...TODAY, offset: 0 };
    const page2: ScreensQuery = { ...TODAY, offset: 20 };

    const { result, rerender } = renderHook(({ query }) => useScreens(query), {
      initialProps: { query: page1 as ScreensQuery | null },
    });
    await waitFor(() => expect(result.current.status).toBe("ready"));

    rerender({ query: page2 });
    await waitFor(() => expect(result.current.meta?.offset).toBe(20));
    expect(result.current.rows[0]?.symbol).toBe("market_cap@20");

    clearPageCache();
    rerender({ query: page1 });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));

    expect(headerOf(fetchMock.mock.calls[2] as [unknown, RequestInit])["if-none-match"]).toBeUndefined();

    // The footer and the body agree again: 1–page rows, page 1.
    await waitFor(() => expect(result.current.meta?.offset).toBe(0));
    expect(result.current.rows[0]?.symbol).toBe("market_cap@0");
  });

  it("still revalidates the query that is on screen", async () => {
    // The fix must not disable conditional requests; it must key them.
    const fetchMock = conditionalServer();
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useScreens(TODAY));
    await waitFor(() => expect(result.current.status).toBe("ready"));

    act(() => result.current.refresh());
    await waitFor(() => expect(result.current.revalidated).toBe(true));

    const [, init] = fetchMock.mock.calls[1] as unknown as [string, RequestInit];
    expect(headerOf([null, init])["if-none-match"]).toBe(
      `W/"${JSON.stringify(requestBody(TODAY).criteria)}"`,
    );
    // A 304 keeps the rows.
    expect(result.current.rows[0]?.symbol).toBe("market_cap@0");
    expect(result.current.status).toBe("ready");
  });

  /** Adversarial (a): a filter-only change is a different query. */
  it("keys the request by its filters, not only by the view", async () => {
    const fetchMock = conditionalServer();
    vi.stubGlobal("fetch", fetchMock);

    const cheap: ScreensQuery = {
      ...TODAY,
      filters: [{ field: "pe", op: "lt", value: 20 }],
    };
    const dearer: ScreensQuery = {
      ...TODAY,
      filters: [{ field: "pe", op: "lt", value: 40 }],
    };

    const { result, rerender } = renderHook(({ query }) => useScreens(query), {
      initialProps: { query: cheap as ScreensQuery | null },
    });
    await waitFor(() => expect(result.current.status).toBe("ready"));

    rerender({ query: dearer });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    const [, second] = fetchMock.mock.calls[1] as unknown as [string, RequestInit];
    const secondBody = JSON.parse(String(second.body)) as { criteria: Record<string, unknown> };
    expect(secondBody.criteria["filters"]).toEqual([{ field: "pe", op: "lt", value: 40 }]);
    // Different filters, different body, therefore no tag from the first query.
    expect(headerOf([null, second])["if-none-match"]).toBeUndefined();

    // ...and the filter set that *is* on screen still revalidates cleanly.
    act(() => result.current.refresh());
    await waitFor(() => expect(result.current.revalidated).toBe(true));
    const [, third] = fetchMock.mock.calls[2] as unknown as [string, RequestInit];
    expect(headerOf([null, third])["if-none-match"]).toBe(
      `W/"${JSON.stringify(secondBody.criteria)}"`,
    );
  });

  /** A 304 that cannot be honoured is never painted as a 200. */
  it("refuses an unhonourable 304 and asks again without a tag", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(notModified('W/"not-ours"'))
      .mockResolvedValueOnce(answer(payload([{ symbol: "AAPL", price: 344.63 }]), { etag: 'W/"real"' }));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useScreens(TODAY));
    await waitFor(() => expect(result.current.status).toBe("ready"));

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(headerOf(fetchMock.mock.calls[1] as [unknown, RequestInit])["if-none-match"]).toBeUndefined();
    expect(result.current.rows[0]?.symbol).toBe("AAPL");
    expect(result.current.revalidated).toBe(false);
  });
});

describe("useViewCatalogue", () => {
  it("reads the views the schema serves", async () => {
    const views = [
      { name: "today", label: "Today", job: "What is moving?", columns: ["price", "change"] },
      { name: "value", label: "Value", job: "Is it cheap?", columns: ["pe"] },
    ];
    const fetchMock = vi.fn(async (_input: RequestInfo | URL) => answer({ type: "object", views }));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useViewCatalogue());
    await waitFor(() => expect(result.current.status).toBe("ready"));

    expect(fetchMock.mock.calls[0]?.[0]).toBe("/screens/select/schema");
    expect(result.current.views.map((view) => view.name)).toEqual(["today", "value"]);
  });

  it("reports a catalogue that could not be read", async () => {
    const fetchMock = vi.fn(async () => answer({ detail: "not found" }, { status: 404 }));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useViewCatalogue());
    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current.error).toBe("not found");
    expect(result.current.views).toHaveLength(0);
  });
});
