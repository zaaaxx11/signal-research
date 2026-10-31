import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ScreenRow, ViewDefinition } from "@/data/screens";
import { requestBody } from "@/data/screens";

import { logoPath, LOGO_SIZE } from "./logo";
import { ScreenPanel } from "./ScreenPanel";

/**
 * The identity lane's mark.
 *
 * The contract is a *URL*, not a payload: every row draws `/logos/<symbol>`,
 * and the row's own bytes never carry a logo. That is what keeps a 100-row
 * panel inside the API's 60 KB budget — a monogram data URI is ~640 B, so a
 * hundred of them is 64 KB on their own, and the API would pay for them in
 * rows.
 */

const VIEW: ViewDefinition = {
  name: "today",
  label: "Today",
  job: "What is moving right now?",
  columns: ["price"],
};

function rows(symbols: readonly string[]): ScreenRow[] {
  return symbols.map((symbol, index) => ({ symbol, price: 100 + index }));
}

function twenty(prefix = "A"): ScreenRow[] {
  return rows(Array.from({ length: 20 }, (_, index) => `${prefix}${index}`));
}

function answer(list: ScreenRow[], budgetBytes = 58_112): Response {
  return new Response(
    JSON.stringify({
      as_of: "2026-10-31T01:28:01Z",
      limit: 20,
      total: 5_600,
      universe_total: 5_600,
      returned: list.length,
      columns: VIEW.columns,
      rows: list,
      budget: { limit_bytes: 60_000, bytes: budgetBytes, truncated: false, dropped_fields: [] },
    }),
    { status: 200, headers: { "content-type": "application/json" } },
  );
}

function bodyOf(call: unknown): Record<string, unknown> {
  const [, init] = call as [string, RequestInit];
  return JSON.parse(String(init.body)) as Record<string, unknown>;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("logoPath", () => {
  it("names the symbol as one path segment", () => {
    expect(logoPath("AAPL")).toBe("/logos/AAPL");
    // A dot is a legal path character: BRK.B is the spelling the route serves,
    // so it is not escaped into an equivalent the endpoint never sees.
    expect(logoPath("BRK.B")).toBe("/logos/BRK.B");
  });

  it("percent-encodes anything that would change the path's structure", () => {
    expect(logoPath("A/B")).toBe("/logos/A%2FB");
    expect(logoPath("A B")).toBe("/logos/A%20B");
    expect(logoPath("A?B")).toBe("/logos/A%3FB");
    expect(logoPath("^GSPC")).toBe("/logos/%5EGSPC");
  });
});

describe("the identity lane", () => {
  it("gives every row on the page an image whose src is exactly /logos/<symbol>", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(answer(twenty())));
    render(
      <ScreenPanel
        view={VIEW}
        selectedSymbol={null}
        onActivate={vi.fn()}
        onClear={vi.fn()}
        containerWidth={1280}
      />,
    );

    await waitFor(() =>
      expect(screen.getByTestId("grid-table").querySelectorAll("tbody tr[data-symbol]")).toHaveLength(
        20,
      ),
    );

    const images = screen.getByTestId("grid-table").querySelectorAll("tbody tr[data-symbol] img");
    expect(images).toHaveLength(20);

    const cells = screen.getByTestId("grid-table").querySelectorAll("tbody tr[data-symbol]");
    cells.forEach((cell, index) => {
      const image = cell.querySelector("img");
      expect(image?.getAttribute("src")).toBe(`/logos/A${index}`);
    });
  });

  it("reserves a 20 px box, loads lazily and decodes off the main thread", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(answer(rows(["AAPL", "BRK.B"]))));
    render(
      <ScreenPanel
        view={VIEW}
        selectedSymbol={null}
        onActivate={vi.fn()}
        onClear={vi.fn()}
        containerWidth={1280}
      />,
    );

    await waitFor(() => expect(screen.getByTestId("grid-table")).toBeTruthy());

    const image = screen.getByTestId("logo-img-AAPL");
    expect(image.getAttribute("src")).toBe("/logos/AAPL");
    expect(image.getAttribute("alt")).toBe("");
    expect(Number(image.getAttribute("width"))).toBe(LOGO_SIZE);
    expect(LOGO_SIZE).toBeGreaterThanOrEqual(20);
    expect(LOGO_SIZE).toBeLessThanOrEqual(24);
    expect(image.getAttribute("loading")).toBe("lazy");
    expect(image.getAttribute("decoding")).toBe("async");

    // A dotted symbol survives into the DOM unchanged.
    expect(screen.getByTestId("logo-img-BRK.B").getAttribute("src")).toBe("/logos/BRK.B");

    // The box is the wrapper's, not the image's: it exists before the bytes do.
    const box = image.parentElement as HTMLElement;
    expect(box.style.width).toBe(`${LOGO_SIZE}px`);
    expect(box.style.height).toBe(`${LOGO_SIZE}px`);
  });

  it("draws the monogram while the image is loading, and the image once it lands", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(answer(rows(["AAPL"]))));
    render(
      <ScreenPanel
        view={VIEW}
        selectedSymbol={null}
        onActivate={vi.fn()}
        onClear={vi.fn()}
        containerWidth={1280}
      />,
    );

    await waitFor(() => expect(screen.getByTestId("logo-img-AAPL")).toBeTruthy());
    expect(screen.getByTestId("monogram-AAPL").textContent).toBe("A");
    expect(screen.getByTestId("logo-img-AAPL").parentElement?.getAttribute("data-logo-state")).toBe(
      "loading",
    );

    fireEvent.load(screen.getByTestId("logo-img-AAPL"));

    await waitFor(() => expect(screen.queryByTestId("monogram-AAPL")).toBeNull());
    expect(screen.getByTestId("logo-img-AAPL").getAttribute("src")).toBe("/logos/AAPL");
    await waitFor(() =>
      expect(screen.getByTestId("logo-img-AAPL").parentElement?.getAttribute("data-logo-state")).toBe(
        "loaded",
      ),
    );
  });

  it("(b) a 404 or a 500 leaves the monogram, never a broken image or a moving row", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(answer(rows(["AAPL", "MSFT"]))));
    render(
      <ScreenPanel
        view={VIEW}
        selectedSymbol={null}
        onActivate={vi.fn()}
        onClear={vi.fn()}
        containerWidth={1280}
      />,
    );

    await waitFor(() => expect(screen.getByTestId("logo-img-AAPL")).toBeTruthy());

    const row = screen.getByTestId("grid-table").querySelector('tbody tr[data-symbol="AAPL"]')!;
    const before = row.getBoundingClientRect();

    fireEvent.error(screen.getByTestId("logo-img-AAPL"));

    // No <img> at all: an errored image is what a browser draws as a broken
    // glyph, and the monogram is a better answer than a glyph.
    await waitFor(() => expect(row.querySelector("img")).toBeNull());
    expect(screen.getByTestId("monogram-AAPL").textContent).toBe("A");
    expect(row.querySelector("[data-logo-state]")?.getAttribute("data-logo-state")).toBe("failed");

    // The box did not move: the wrapper still declares the reserved 20 px, so
    // the failure cannot reflow the lane.
    const box = row.querySelector("[data-logo-state]") as HTMLElement;
    expect(box.style.width).toBe(`${LOGO_SIZE}px`);
    expect(box.style.height).toBe(`${LOGO_SIZE}px`);
    expect(row.getBoundingClientRect().height).toBe(before.height);

    // The neighbour that did not fail is untouched.
    expect(screen.getByTestId("logo-img-MSFT")).toBeTruthy();
  });
});

describe("the panel budget", () => {
  it("never asks the API for the logo column, even when a selection names it", async () => {
    const fetchMock = vi.fn().mockResolvedValue(answer(twenty()));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <ScreenPanel
        view={VIEW}
        selectedSymbol={null}
        onActivate={vi.fn()}
        onClear={vi.fn()}
        containerWidth={1280}
        columns={["price", "logo"]}
      />,
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    const body = bodyOf(fetchMock.mock.calls[0]);
    expect(body["columns"]).toEqual(["price"]);
    expect((body["columns"] as string[]).includes("logo")).toBe(false);
    // Not just the column list: the serialized body has no logo key anywhere.
    expect(JSON.stringify(body)).not.toContain("logo");
  });

  it("sends exactly the canonical body for a twenty-row panel — no extra field, no growth", async () => {
    const fetchMock = vi.fn().mockResolvedValue(answer(twenty()));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <ScreenPanel
        view={VIEW}
        selectedSymbol={null}
        onActivate={vi.fn()}
        onClear={vi.fn()}
        containerWidth={1280}
      />,
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    const sent = String(init.body);

    const canonical = JSON.stringify(
      requestBody({
        view: VIEW.name,
        columns: VIEW.columns,
        limit: 20,
        offset: 0,
        sort: "market_cap",
        filters: [],
      }),
    );
    expect(sent).toBe(canonical);
    expect(sent.length).toBeLessThan(2_048);

    // Why that matters, as arithmetic rather than opinion: a hundred marks at
    // the ~640 B a monogram data URI costs is more than the whole 60 KB panel
    // budget, and the API pays for an overspend by dropping rows.
    const MONOGRAM_BYTES = 640;
    const PANEL_BUDGET_BYTES = 60_000;
    expect(100 * MONOGRAM_BYTES).toBeGreaterThan(PANEL_BUDGET_BYTES);

    // And the answer the panel draws is untouched: a full page, no budget cut.
    expect(
      screen.getByTestId("grid-table").querySelectorAll("tbody tr[data-symbol]"),
    ).toHaveLength(20);
    expect(screen.queryByText(/budget cut rows/)).toBeNull();
  });
});
