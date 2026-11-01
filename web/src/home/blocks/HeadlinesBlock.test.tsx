import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { HeadlinesBlock, shortStamp } from "./HeadlinesBlock";
import { LOADING, READY, readHome } from "../home";
import { homeDocument } from "../__fixtures__/home";

/**
 * Five stories, each a real new-tab link, each rendered as text — and a list
 * that says "none" rather than drawing an empty box.
 */

function newsRow(index: number, headline: string) {
  return {
    headline,
    url: `https://example.com/${index}`,
    source: "AP",
    published_at: `2026-10-31T${String(10 + index).padStart(2, "0")}:00:00Z`,
  };
}

describe("HeadlinesBlock", () => {
  it("draws up to five stories as new-tab links", () => {
    const report = readHome(homeDocument());
    render(<HeadlinesBlock state={READY} news={report.news} />);

    const links = screen.getByTestId("home-headlines").querySelectorAll("[data-news-link]");
    expect(links).toHaveLength(3);
    expect(links[0]?.getAttribute("target")).toBe("_blank");
    expect(links[0]?.getAttribute("rel")).toBe("noopener noreferrer");
    expect(links[0]?.textContent).toContain("Futures slide");
  });

  it("never draws more than five rows", () => {
    const report = readHome(
      homeDocument({ news: Array.from({ length: 8 }, (_, index) => newsRow(index, `Story ${index}`)) }),
    );
    render(<HeadlinesBlock state={READY} news={report.news} />);

    expect(screen.getByTestId("home-headlines").querySelectorAll("li")).toHaveLength(5);
  });

  it("renders markup in a headline as text", () => {
    const report = readHome(
      homeDocument({ news: [newsRow(3, "Chip <script>alert(1)</script> selloff")] }),
    );
    render(<HeadlinesBlock state={READY} news={report.news} />);

    const card = screen.getByTestId("home-headlines");
    expect(card.querySelector("script")).toBeNull();
    expect(card.textContent).toContain("Chip <script>alert(1)</script> selloff");
  });

  it("says the cycle was empty, and offers the news page", () => {
    const report = readHome(homeDocument({ news: [] }));
    render(<HeadlinesBlock state={READY} news={report.news} />);

    expect(screen.getByTestId("home-headlines").textContent).toContain("No headlines in this cycle.");
    expect(screen.getByTestId("home-headlines").querySelector('a[href="/app/news"]')).not.toBeNull();
  });

  it("prints the reason when the feed is missing", () => {
    render(<HeadlinesBlock state={{ kind: "missing", reason: "Headlines unavailable." }} news={null} />);

    expect(screen.getByTestId("headlines-reason").textContent).toBe("Headlines unavailable.");
  });

  it("reserves five rows while loading", () => {
    render(<HeadlinesBlock state={LOADING} news={null} />);

    expect(screen.getByTestId("headlines-skeleton").children).toHaveLength(5);
  });

  it("formats a short stamp without the year", () => {
    expect(shortStamp("2026-10-31T19:42:00Z")).toBe("19:42 · 31 Oct");
    expect(shortStamp("nope")).toBe("");
  });
});
