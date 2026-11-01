import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MoversBlock, moverRows } from "./MoversBlock";
import { LOADING, READY, readHome } from "../home";
import { homeDocument, moverRow } from "../__fixtures__/home";

/**
 * The movers table's promises:
 *
 * * it is a table with a caption and four column headers — a screen reader hears
 *   what the numbers are before it reads them;
 * * forty rows are drawn, all of them, in a fixed 420 px box (adversarial: a
 *   forty-row list must not push the map off a 900 px page);
 * * every row names its screen, and every ticker is a real link;
 * * a missing block prints the reason and no table; an empty one says so.
 */

describe("moverRows", () => {
  it("orders gainers strongest-first and new lows weakest-first", () => {
    const report = readHome(homeDocument());
    const rows = moverRows(report.movers);

    expect(rows).toHaveLength(40);
    expect(rows[0]?.symbol).toBe("NVDA");
    expect(rows[0]?.group).toBe("Top gainers");
    const lows = rows.filter((row) => row.group === "New low");
    expect(lows[0]?.symbol).toBe("INTC");
    expect(lows[0]?.changePct).toBeCloseTo(-4.2, 5);
  });

  it("never exceeds forty rows even if a group is flooded", () => {
    const flood = Array.from({ length: 60 }, (_, index) => moverRow(`G${index}`, 1));
    const report = readHome(homeDocument({ movers: { gainers: flood } }));

    expect(moverRows(report.movers)).toHaveLength(40);
  });
});

describe("MoversBlock", () => {
  it("draws forty rows in a table with a caption and four headers", () => {
    const report = readHome(homeDocument());
    render(<MoversBlock state={READY} movers={report.movers} />);

    const card = screen.getByTestId("home-movers");
    const table = card.querySelector("table");
    expect(table).not.toBeNull();
    expect(table?.querySelector("caption")?.textContent).toContain("Movers");
    expect([...(table?.querySelectorAll("thead th") ?? [])].map((th) => th.textContent)).toEqual([
      "Ticker",
      "Last",
      "Change",
      "Signal",
    ]);
    expect(card.querySelectorAll("tbody tr[data-mover-row]")).toHaveLength(40);
    expect(card.style.height).toBe("420px");
  });

  it("names the screen each row came from and links the ticker", () => {
    const report = readHome(homeDocument());
    render(<MoversBlock state={READY} movers={report.movers} />);

    const row = document.querySelector<HTMLTableRowElement>('tr[data-mover-row="NVDA"]');
    expect(row).not.toBeNull();
    expect(row?.textContent).toContain("Top gainers");
    const link = row?.querySelector("a");
    expect(link?.getAttribute("href")).toBe("/app/stock/NVDA");
    expect(link?.querySelector("img")?.getAttribute("src")).toBe("/logos/NVDA");
  });

  it("carries the sign and the arrow in the text, not only in the colour", () => {
    const report = readHome(homeDocument());
    render(<MoversBlock state={READY} movers={report.movers} />);

    const row = document.querySelector<HTMLTableRowElement>('tr[data-mover-row="NVDA"]');
    expect(row?.textContent).toContain("▲");
    expect(row?.textContent).toContain("+12.00%");
    expect(row?.textContent).toContain("up");
  });

  it("offers a door to the screener", () => {
    const report = readHome(homeDocument());
    render(<MoversBlock state={READY} movers={report.movers} />);

    expect(screen.getByTestId("movers-see-all").getAttribute("href")).toBe("/app/screener");
  });

  it("prints the reason and no table when the block is missing", () => {
    render(
      <MoversBlock
        state={{ kind: "missing", reason: "Movers unavailable — the source did not answer this request." }}
        movers={null}
      />,
    );

    expect(screen.getByTestId("movers-reason").textContent).toContain("did not answer");
    expect(screen.getByTestId("home-movers").querySelector("table")).toBeNull();
  });

  it("says the screens matched nothing when all four groups are empty", () => {
    const report = readHome(homeDocument({ movers: { gainers: [], new_high: [], overbought: [], new_low: [] } }));
    render(<MoversBlock state={READY} movers={report.movers} />);

    expect(within(screen.getByTestId("home-movers")).getByText(/No names matched/)).toBeTruthy();
  });

  it("reserves twelve skeleton rows in the same 420 px box", () => {
    render(<MoversBlock state={LOADING} movers={null} />);

    expect(screen.getByTestId("movers-skeleton").children).toHaveLength(12);
    expect(screen.getByTestId("home-movers").style.height).toBe("420px");
  });
});
