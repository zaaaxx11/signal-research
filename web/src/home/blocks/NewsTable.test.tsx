import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { NewsTable, whenLabel } from "./NewsTable";
import { LOADING, READY, readHome } from "../home";
import { homeDocument } from "../__fixtures__/home";

/**
 * The desk is one table, and it is the page's whole news surface: every row is a
 * real link out, the box scrolls instead of growing the page, and a story the
 * feed did not date is not stamped with today.
 */

describe("whenLabel", () => {
  const now = new Date("2026-10-31T21:00:00Z");
  it("prints the time for today and the month-day for anything older", () => {
    expect(whenLabel("2026-10-31T19:42:00Z", now)).toBe("19:42");
    expect(whenLabel("2026-10-30T09:05:00Z", now)).toBe("Oct-30");
    expect(whenLabel("2026-01-02T09:05:00Z", now)).toBe("Jan-02");
  });
  it("never invents a date", () => {
    expect(whenLabel(null, now)).toBe("—");
    expect(whenLabel("not-a-date", now)).toBe("—");
  });
});

describe("NewsTable", () => {
  it("draws the top of the wire as a peek, with no scroll of its own", () => {
    const report = readHome(homeDocument());
    render(<NewsTable state={READY} news={report.news} />);

    const rows = screen.getAllByTestId("news-row");
    expect(rows).toHaveLength(8);
    expect(screen.getByTestId("news-table").className).not.toContain("overflow-y-auto");
    expect(rows[0]?.textContent).toContain("Futures slide");
    expect(rows[0]?.querySelector("a")?.getAttribute("target")).toBe("_blank");
    expect(rows[0]?.querySelector("a")?.getAttribute("rel")).toBe("noopener noreferrer");
  });

  it("prints a story with no URL as text, not a dead link", () => {
    const report = readHome(
      homeDocument({
        news: [{ headline: "Untraceable story", url: null, source: "Wire", published_at: null }],
      }),
    );
    render(<NewsTable state={READY} news={report.news} />);

    const row = screen.getByTestId("news-row");
    expect(row.querySelector("a")).toBeNull();
    expect(row.textContent).toContain("Untraceable story");
    expect(row.textContent).toContain("—");
  });

  it("says the cycle was empty rather than drawing an empty table", () => {
    render(<NewsTable state={READY} news={[]} />);
    expect(screen.getByTestId("home-news").textContent).toContain("No headlines in this cycle.");
    expect(screen.queryByTestId("news-table")).toBeNull();
  });

  it("says why the desk is missing, in its own card", () => {
    render(
      <NewsTable
        state={{ kind: "missing", reason: "Headlines unavailable — the feeds did not answer." }}
        news={null}
      />,
    );
    expect(screen.getByTestId("news-reason").textContent).toContain("did not answer");
  });

  it("reserves a box while loading and draws no rows", () => {
    render(<NewsTable state={LOADING} news={null} />);
    expect(screen.getByTestId("home-news")).toBeTruthy();
    expect(screen.queryAllByTestId("news-row")).toHaveLength(0);
    expect(screen.queryByTestId("news-table")).toBeNull();
  });
});
