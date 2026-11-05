import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { InsiderPage } from "./InsiderPage";

const SAMPLE_ROWS = [
  { symbol:"AAPL", owner:"Cook Tim", owner_type:"insider" as const, relationship:"CEO", transaction_type:"Sale" as const, shares:10000, value:1750000, filing_date:"2026-11-04" },
  { symbol:"MSFT", owner:"Nadella Satya", owner_type:"insider" as const, relationship:"CEO", transaction_type:"Buy" as const, shares:5000, value:1800000, filing_date:"2026-11-03" },
];

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), { status:200, headers:{"content-type":"application/json"} });
}

function stubFeed(rows = SAMPLE_ROWS, topW = rows, top10: typeof SAMPLE_ROWS = []) {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(json({
    as_of: "2026-11-04T12:00:00Z",
    owner_type: "insiders",
    transaction_type: "all",
    total: rows.length,
    rows,
    top_weekly: topW,
    top_10_owner_weekly: top10,
  }));
}

describe("InsiderPage", () => {
  afterEach(() => { vi.restoreAllMocks(); });

  it("renders SectionTitle heading", async () => {
    stubFeed();
    render(<InsiderPage />);
    await screen.findByTestId("insider-table");
    const headings = screen.getAllByRole("heading");
    const title = headings.find((h) => h.textContent?.includes("Insider Trading"));
    expect(title).toBeTruthy();
  });

  it("shows skeleton while loading", () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() => new Promise(() => {}));
    render(<InsiderPage />);
    expect(screen.getByTestId("insider-skeleton")).toBeTruthy();
  });

  it("renders rows with correct data and transaction badges", async () => {
    stubFeed();
    render(<InsiderPage />);
    const rows = await screen.findAllByTestId("insider-row");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("AAPL");
    expect(rows[1].textContent).toContain("Buy");
  });

  it("switches owner type tabs and refetches", async () => {
    stubFeed();
    render(<InsiderPage />);
    await screen.findByTestId("insider-table");
    await userEvent.click(screen.getByTestId("insider-tab-managers"));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  });

  it("switches transaction filter and refetches", async () => {
    stubFeed();
    render(<InsiderPage />);
    await screen.findByTestId("insider-table");
    await userEvent.click(screen.getByTestId("insider-filter-buy"));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  });

  it("renders sidebar blocks", async () => {
    stubFeed(SAMPLE_ROWS, SAMPLE_ROWS, SAMPLE_ROWS.slice(0,1));
    render(<InsiderPage />);
    await screen.findByTestId("insider-table");
    expect(screen.getByTestId("insider-top-weekly")).toBeTruthy();
    expect(screen.getByTestId("insider-top-10-owner")).toBeTruthy();
  });

  it("shows empty sidebar when no trades", async () => {
    stubFeed(SAMPLE_ROWS, [], []);
    render(<InsiderPage />);
    await screen.findByTestId("insider-table");
    expect(screen.getByTestId("insider-top-10-owner").textContent).toContain("No trades");
  });

  it("shows empty table when zero rows", async () => {
    stubFeed([]);
    render(<InsiderPage />);
    await waitFor(() => expect(screen.getByTestId("insider-empty")).toBeTruthy());
  });

  it("shows error and retry on failure", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("server down"));
    render(<InsiderPage />);
    await screen.findByTestId("insider-error");
    expect(screen.getByTestId("insider-error-retry")).toBeTruthy();
  });

  it("includes data-quote attributes on rows", async () => {
    stubFeed();
    render(<InsiderPage />);
    const rows = await screen.findAllByTestId("insider-row");
    const attr = rows[0].getAttribute("data-quote");
    expect(attr).toBeTruthy();
    expect(JSON.parse(attr!).symbol).toBe("AAPL");
  });
});