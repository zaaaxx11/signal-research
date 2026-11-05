/**
 * The ticker page: five tabs, a deep link, a keyboard, and the composite
 * Overview.
 *
 * The reads are stubbed at the network seam, so what this file proves is the
 * page's own contract — the address selects the tab, ArrowRight moves it, the
 * Overview draws six columns of fourteen cells, and a 404 from the statement
 * collector becomes a sentence.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { resetSnapshotCache } from "@/detail/useDetail";

import { TickerPage } from "./TickerPage";
import {
  companyPayload,
  json,
  notFound,
  profilePayload,
  snapshotPayload,
  stubTickerApi,
  statementsPayload,
} from "./__fixtures__/ticker";

function snapshotCell(key: string): Element | null {
  return screen.getByTestId("snapshot-grid").querySelector(`[data-snapshot-cell="${key}"]`);
}

beforeEach(() => {
  resetSnapshotCache();
  window.history.replaceState(null, "", "/app/stock/AAPL");
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("TickerPage", () => {
  it("opens on Overview with the band, the snapshot and the company profile", async () => {
    stubTickerApi();
    render(<TickerPage symbol="AAPL" />);

    await waitFor(() => expect(screen.getByTestId("header-price").textContent).toBe("$343.36"));

    expect(screen.getByTestId("header-company").textContent).toBe("Apple Inc.");
    expect(screen.getByTestId("header-change").textContent).toContain("+1.24%");
    expect(screen.getByTestId("header-market-cap").textContent).toBe("$5.18T");

    const overview = screen.getByRole("tab", { name: "Overview" });
    expect(overview.getAttribute("aria-selected")).toBe("true");

    const panel = screen.getByRole("tabpanel");
    expect(overview.getAttribute("aria-controls")).toBe(panel.id);
    expect(panel.getAttribute("aria-labelledby")).toBe(overview.id);

    const grid = screen.getByTestId("snapshot-grid");
    expect(grid.querySelectorAll("[data-snapshot-column]")).toHaveLength(6);
    expect(grid.querySelectorAll("[data-snapshot-cell]")).toHaveLength(84);

    expect((await screen.findByTestId("company-empty")).textContent).toBe(
      "No company profile available.",
    );
  });

  it("deep-links ?tab=statements and draws the collected tables", async () => {
    window.history.replaceState(null, "", "/app/stock/AAPL?tab=statements");
    stubTickerApi();

    render(<TickerPage symbol="AAPL" />);

    expect(screen.getByRole("tab", { name: "Financials" }).getAttribute("aria-selected")).toBe(
      "true",
    );
    const income = await screen.findByTestId("statements-income", undefined, { timeout: 4_000 });
    expect(income.querySelectorAll("tbody tr[data-statement-row]").length).toBe(3);
    expect(screen.queryByTestId("snapshot-grid")).toBeNull();
  });

  it("shows a statement 404's reason, not an empty table", async () => {
    window.history.replaceState(null, "", "/app/stock/ZZZZ?tab=statements");
    const reason = "no_statements: SEC companyfacts are not on file for ZZZZ";
    stubTickerApi({ statements: () => notFound(reason) });

    render(<TickerPage symbol="ZZZZ" />);

    const message = await screen.findByTestId("statements-404", undefined, { timeout: 4_000 });
    expect(message.textContent).toContain(reason);
  });

  it("moves the tab strip with the arrow keys and writes the URL", async () => {
    stubTickerApi();
    const user = userEvent.setup();
    render(<TickerPage symbol="AAPL" />);

    const overview = screen.getByRole("tab", { name: "Overview" });
    overview.focus();
    await user.keyboard("{ArrowRight}");

    const financials = screen.getByRole("tab", { name: "Financials" });
    expect(financials.getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(financials);
    expect(window.location.search).toBe("?tab=statements");
    expect(screen.getByTestId("ticker-page").getAttribute("data-tab")).toBe("statements");
  });

  it("follows a tab click and keeps the address in step", async () => {
    stubTickerApi();
    const user = userEvent.setup();
    render(<TickerPage symbol="AAPL" />);

    await user.click(screen.getByRole("tab", { name: "News" }));
    expect(window.location.search).toBe("?tab=news");
    expect(screen.getByRole("tab", { name: "News" }).getAttribute("aria-selected")).toBe("true");
    await screen.findByTestId("news-loaded", undefined, { timeout: 4_000 });
  });

  it("uses the profile document when the deployment serves it", async () => {
    stubTickerApi({ profile: () => new Response(JSON.stringify(profilePayload()), { status: 200 }) });
    render(<TickerPage symbol="AAPL" />);

    await waitFor(() =>
      expect(screen.getByTestId("header-identity").textContent).toContain("NDX, S&P 500"),
    );
  });

  it("shows the company profile in Overview only", async () => {
    stubTickerApi({ company: () => json(companyPayload()) });
    const user = userEvent.setup();
    render(<TickerPage symbol="AAPL" />);

    await screen.findByTestId("company-description", undefined, { timeout: 4_000 });
    expect(screen.getByTestId("company-officers").querySelectorAll("li")).toHaveLength(5);

    await user.click(screen.getByRole("tab", { name: "Financials" }));
    await screen.findByTestId("statements-income", undefined, { timeout: 4_000 });
    expect(screen.queryByTestId("company-profile")).toBeNull();
  });

  it("renders a statements document only after its tab is opened", async () => {
    const fetchMock = stubTickerApi({ statements: () => new Response(JSON.stringify(statementsPayload()), { status: 200 }) });
    render(<TickerPage symbol="AAPL" />);
    await screen.findByTestId("snapshot-grid");

    expect(
      fetchMock.mock.calls.some((call) => String((call as unknown[])[0]).includes("/engine/statements/")),
    ).toBe(false);
  });

  it("fills the snapshot from /engine/snapshot once per symbol", async () => {
    const fetchMock = stubTickerApi({ snapshot: () => json(snapshotPayload()) });
    render(<TickerPage symbol="AAPL" />);

    await waitFor(() => expect(snapshotCell("pe")?.textContent).toBe("34.12"), { timeout: 4_000 });
    expect(snapshotCell("forward_pe")?.textContent).toBe("29.40");
    expect(snapshotCell("market_cap")?.textContent).toBe("$5.18T");
    expect(snapshotCell("debt_to_equity")?.textContent).toBe("0.77");
    expect(screen.queryByTestId("snapshot-notice")).toBeNull();

    const reads = fetchMock.mock.calls.filter((call) =>
      String((call as unknown[])[0]).includes("/engine/snapshot/"),
    );
    expect(reads).toHaveLength(1);
    expect(String((reads[0] as unknown[])[0])).toBe("/engine/snapshot/AAPL");
  });

  it("shows the missing endpoint's reason in one line and keeps the fallback", async () => {
    stubTickerApi({ snapshot: () => notFound("snapshot_not_found: no snapshot for AAPL") });
    render(<TickerPage symbol="AAPL" />);

    const notice = await screen.findByTestId("snapshot-notice");
    expect(notice.textContent).toContain("snapshot_not_found: no snapshot for AAPL");
    expect(notice.querySelectorAll("br, p, div")).toHaveLength(0);

    expect(screen.getByTestId("snapshot-grid").querySelectorAll("[data-snapshot-cell]")).toHaveLength(84);
    await waitFor(() => expect(snapshotCell("market_cap")?.textContent).toBe("$5.18T"));
  });

  it("shows a technicals 404's reason without breaking the rest of Overview", async () => {
    stubTickerApi({
      technicals: () => notFound("technicals_unavailable: no daily close series for ZZZZ"),
    });
    render(<TickerPage symbol="AAPL" />);

    const failure = await screen.findByTestId("technicals-error", undefined, { timeout: 4_000 });
    expect(failure.textContent).toContain(
      "technicals_unavailable: no daily close series for ZZZZ",
    );

    expect(screen.getByTestId("header-price").textContent).toBe("$343.36");
    expect(screen.getByTestId("snapshot-grid").querySelectorAll("[data-snapshot-cell]")).toHaveLength(84);
    expect(
      screen
        .getByTestId("technicals-block")
        .querySelector('[data-technical="alpha"]')?.textContent,
    ).toBe("+4.82%");
  });

  it("draws the five tabs in order", async () => {
    stubTickerApi();
    render(<TickerPage symbol="AAPL" />);

    const tabs = screen.getAllByRole("tab");
    expect(tabs).toHaveLength(5);
    expect(tabs[0]).toHaveTextContent("Overview");
    expect(tabs[1]).toHaveTextContent("Financials");
    expect(tabs[2]).toHaveTextContent("Peers");
    expect(tabs[3]).toHaveTextContent("Insider");
    expect(tabs[4]).toHaveTextContent("News");
  });
});