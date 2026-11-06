import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";

import { InsiderDetailPage } from "./InsiderDetailPage";

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

const SAMPLE = {
  symbol: "AAPL",
  as_of: "2026-11-04",
  count: 2,
  items: [
    {
      accession: "0000320193-26-000001",
      filed: "2026-11-04",
      owner_name: "Cook Tim",
      role: "CEO",
      url: "https://www.sec.gov/Archives/edgar/data/320193/000032019326000001/x.xml",
      transaction_date: "2026-11-03",
      code: "S",
      shares: 10000,
      price: 175.0,
      acquired_disposed: "D",
      shares_after: 3000000,
    },
    {
      accession: "0000320193-26-000002",
      filed: "2026-11-02",
      owner_name: "Maestri Luca",
      role: "CFO",
      url: null,
      transaction_date: "2026-11-01",
      code: "P",
      shares: 5000,
      price: 170.0,
      acquired_disposed: "A",
      shares_after: 100000,
    },
  ],
};

describe("InsiderDetailPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("titles the page with the ticker and links back to the insider list", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json(SAMPLE));
    render(<InsiderDetailPage symbol="AAPL" />);

    const h1 = await screen.findByRole("heading", { level: 1 });
    expect(h1.textContent).toContain("AAPL");
    expect(h1.textContent).toContain("Form 4 filings");

    const back = screen.getByTestId("insider-detail-back");
    expect(back.getAttribute("href")).toContain("/insider");
  });

  it("reads the company's filings from GET /engine/insider/{symbol}", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(json(SAMPLE));
    render(<InsiderDetailPage symbol="AAPL" />);

    const rows = await screen.findAllByTestId("insider-filing");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("AAPL");
    expect(rows[0].textContent).toContain("Cook Tim");
    expect(String(spy.mock.calls[0]?.[0])).toContain("engine/insider/AAPL");
  });

  it("prints the document's own count", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json(SAMPLE));
    render(<InsiderDetailPage symbol="AAPL" />);

    const count = await screen.findByTestId("insider-detail-count");
    expect(count.textContent).toContain("2");
  });

  it("names a company the engine does not know instead of drawing blank rows", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "unknown symbol" }), {
        status: 404,
        headers: { "content-type": "application/json" },
      }),
    );
    render(<InsiderDetailPage symbol="ZZZZ" />);

    const error = await screen.findByTestId("insider-detail-error");
    expect(error.textContent).toContain("ZZZZ");
  });
});
