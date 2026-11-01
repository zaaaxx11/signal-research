/**
 * The served document's reader: what it tolerates, what it refuses, and the one
 * read per session.
 *
 * Tiers and entitlements are optional on purpose — a deployment that has not
 * published them yet must still draw a working panel — so the parse tests feed
 * documents with and without those fields.
 */

import { describe, expect, it, vi, afterEach } from "vitest";

import { buildRegistry } from "@/filters/registry";
import { filterSchemaFixture } from "@/filters/__fixtures__/schema";
import { encodeScreenState, emptyScreenState, parseScreenState } from "@/filters/urlState";
import { makeFilter } from "@/filters/state";

import {
  FILTERS_PATH,
  FILTERS_TIMEOUT_MS,
  isFilterControl,
  loadFiltersDocument,
  optionFilter,
  optionValue,
  parseFiltersDocument,
  publishedFilters,
  resetFiltersDocumentCache,
} from "./filtersDocument";
import { filterDocument, filterDocumentWire } from "./__fixtures__/filters";

afterEach(() => {
  vi.unstubAllGlobals();
  resetFiltersDocumentCache();
});

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("the filter document", () => {
  it("reads groups, controls and options, and fills the two request settings", () => {
    const doc = filterDocument();

    expect(doc.version).toBe("screener-test");
    expect(doc.groups.map((group) => group.id)).toEqual([
      "descriptive",
      "fundamental",
      "technical",
      "news",
      "etf",
    ]);
    expect(doc.groups.every((group) => group.controls.length > 0)).toBe(true);

    // `order` and `signal` are controls with no key: they are request settings,
    // not filters, and the header must not offer them as predicates.
    expect(doc.order?.id).toBe("order");
    expect(doc.signal?.id).toBe("signal");
    expect(isFilterControl(doc.order!)).toBe(false);
    expect(isFilterControl(doc.signal!)).toBe(false);

    const cap = doc.groups[0]?.controls.find((control) => control.id === "cap");
    expect(cap?.tier).toBe("premium"); // optional, read when present
    expect(isFilterControl(cap!)).toBe(true);
    expect(cap?.options[0]?.available).toBe(true);
    expect(cap?.options[1]?.available).toBe(false);
    expect(cap?.options[1]?.reason).toBeTruthy();
  });

  it("survives a document with no options, no tier and no request settings", () => {
    const doc = parseFiltersDocument({
      groups: [{ id: "descriptive", label: "Descriptive" }],
    });
    expect(doc.groups).toHaveLength(1);
    expect(doc.groups[0]?.controls).toEqual([]);
    expect(doc.order).toBeNull();
    expect(doc.signal).toBeNull();
    expect(doc.version).toBe("");
  });

  it("refuses a body with no groups, rather than pretending nothing can be filtered", () => {
    expect(() => parseFiltersDocument({})).toThrow(/no groups/);
    expect(() => parseFiltersDocument({ groups: [] })).toThrow(/no groups/);
    expect(() => parseFiltersDocument(null)).toThrow(/not an object/);
  });

  it("reads one request per session, and retries after a failure", async () => {
    const fetchMock = vi.fn(async () => json(filterDocumentWire()));
    vi.stubGlobal("fetch", fetchMock);

    const [first, second] = await Promise.all([loadFiltersDocument(), loadFiltersDocument()]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(FILTERS_PATH, expect.anything());
    expect(first).toBe(second);

    await loadFiltersDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    resetFiltersDocumentCache();
    vi.stubGlobal("fetch", vi.fn(async () => json({ errorMessage: "nope" }, 500)));
    await expect(loadFiltersDocument()).rejects.toThrow();
    // A failure clears the cache, so the retry really retries.
    const retry = vi.fn(async () => json(filterDocumentWire()));
    vi.stubGlobal("fetch", retry);
    await loadFiltersDocument();
    expect(retry).toHaveBeenCalledTimes(1);
  });

  it("gives up on a read that never settles, so the screen cannot hang on it", async () => {
    vi.useFakeTimers();
    try {
      vi.stubGlobal(
        "fetch",
        vi.fn(
          (_input: RequestInfo | URL, init?: RequestInit) =>
            new Promise<Response>((_resolve, reject) => {
              init?.signal?.addEventListener("abort", () =>
                reject(new DOMException("aborted", "AbortError")),
              );
            }),
        ),
      );
      // Attach the rejection handler before the timer fires: a promise that
      // rejects with nobody listening is an unhandled rejection, and a test
      // that causes one fails the whole run.
      const pending = loadFiltersDocument().catch((error: unknown) => error);
      await vi.advanceTimersByTimeAsync(FILTERS_TIMEOUT_MS + 50);
      const error = await pending;
      expect(error).toBeInstanceOf(Error);
      expect((error as Error).message).toMatch(/took too long/);
    } finally {
      vi.useRealTimers();
    }
  });

  it("compiles one option into the predicate its control published", () => {
    const doc = filterDocument();
    const cap = doc.groups[0]?.controls.find((control) => control.id === "cap")!;
    const exch = doc.groups[0]?.controls.find((control) => control.id === "exch")!;
    const etf = doc.groups[4]?.controls[0]!;

    expect(optionValue(cap.options[0]!)).toBe("mega");
    expect(optionFilter(cap, cap.options[0]!)).toEqual({ key: "cap", op: "eq", value: "mega" });
    // A multi-value control takes the list its `in` compiles.
    expect(optionFilter(exch, exch.options[0]!)).toEqual({
      key: "exch",
      op: "in",
      value: ["nyse"],
    });
    // An unavailable option is not a predicate, and a keyless control is not a
    // filter: both produce nothing rather than a value nobody can send.
    expect(optionFilter(cap, cap.options[1]!)).toBeNull();
    expect(optionFilter(doc.order!, doc.order!.options[0]!)).toBeNull();
    expect(optionFilter(etf, { id: "x", label: "x", token: null, predicate: null, available: true, reason: null })).toBeNull();
  });

  it("names every published value, so a chip can print a word instead of a token", () => {
    const published = publishedFilters(filterDocument());
    expect(published.get("cap")?.label).toBe("Market Cap.");
    expect(published.get("cap")?.values.get("mega")).toBe("Mega ($200bln and more)");
    expect(published.get("exch")?.values.get("nyse")).toBe("NYSE");
    expect(publishedFilters(null).size).toBe(0);
  });

  it("makes a published value survive a reload, and still drops an invented one", () => {
    const registry = buildRegistry(filterSchemaFixture(), publishedFilters(filterDocument()));
    const state = {
      ...emptyScreenState(),
      view: "today",
      filters: [makeFilter("cap", "eq", "mega")],
    };

    const query = encodeScreenState(state);
    expect(query).toContain("f=cap%3Aeq%3Amega");

    const parsed = parseScreenState(`?${query}`, registry, ["today", "value"]);
    expect(parsed.dropped).toEqual([]);
    expect(parsed.state.filters).toHaveLength(1);
    expect(parsed.state.filters[0]).toMatchObject({ key: "cap", op: "eq", value: "mega" });

    // A value the document never published is still refused: the link is the
    // one input a stranger writes.
    const invented = parseScreenState("?f=cap%3Aeq%3Amoon", registry, ["today"]);
    expect(invented.state.filters).toEqual([]);
    expect(invented.dropped).toEqual(["cap"]);

    // And the key the schema refuses but the document publishes is honoured.
    const indexFilter = parseScreenState(
      `?f=idx%3Aeq%3Asp500`,
      buildRegistry(filterSchemaFixture(), publishedFilters(filterDocument())),
      ["today"],
    );
    expect(indexFilter.state.filters).toHaveLength(1);
    expect(indexFilter.state.filters[0]).toMatchObject({ key: "idx", op: "eq", value: "sp500" });
  });

  it("shows the published label on a chip, and the raw value only when it must", () => {
    const registry = buildRegistry(filterSchemaFixture(), publishedFilters(filterDocument()));
    expect(registry.published.get("cap")?.values.get("mega")).toBe("Mega ($200bln and more)");
    // A key with no published vocabulary keeps the schema's own fallback.
    expect(registry.published.has("sec")).toBe(true);
    expect(registry.published.get("sec")?.values.size).toBe(0);
  });
});
