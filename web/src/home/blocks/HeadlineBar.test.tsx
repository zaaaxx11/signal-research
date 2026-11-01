import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { HeadlineBar, stampOf } from "./HeadlineBar";
import { LOADING, READY, readHome } from "../home";
import { homeDocument } from "../__fixtures__/home";

/**
 * The bar's promises:
 *
 * * the newest story is a real link, in a new tab, with a sane `rel`;
 * * the stamp is the feed's, formatted once;
 * * a headline is text — `<script>` in a feed title is eight characters, not a
 *   node;
 * * a missing or empty feed says so in the bar instead of drawing nothing.
 */

describe("HeadlineBar", () => {
  it("draws the newest headline as a new-tab link with its stamp and source", () => {
    const report = readHome(homeDocument());
    render(<HeadlineBar state={READY} news={report.news} />);

    const link = screen.getByTestId("headline-link");
    expect(link.getAttribute("href")).toBe("https://example.com/news/chips");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    expect(link.textContent).toContain("Futures slide");

    const bar = screen.getByTestId("home-headline");
    expect(bar.textContent).toContain("19:42 UTC · 31 Oct 2026");
    expect(bar.textContent).toContain("Reuters");
  });

  it("renders a script tag in a headline as text, never as markup", () => {
    const report = readHome(
      homeDocument({
        news: [
          {
            headline: "Markets <script>alert('x')</script> steady",
            url: "https://example.com/x",
            source: "AP",
            published_at: "2026-10-31T12:00:00Z",
          },
        ],
      }),
    );

    render(<HeadlineBar state={READY} news={report.news} />);

    const bar = screen.getByTestId("home-headline");
    expect(bar.querySelector("script")).toBeNull();
    expect(bar.textContent).toContain("Markets <script>alert('x')</script> steady");
  });

  it("prints a story with no followable link as text, not as a dead anchor", () => {
    const report = readHome(
      homeDocument({
        news: [{ headline: "No link here", url: "javascript:alert(1)", source: "", published_at: null }],
      }),
    );

    render(<HeadlineBar state={READY} news={report.news} />);

    expect(screen.queryByTestId("headline-link")).toBeNull();
    expect(screen.getByTestId("headline-text").textContent).toBe("No link here");
  });

  it("says the headlines are unavailable and why", () => {
    const report = readHome(homeDocument({ news: null, unavailable: ["news"] }));
    render(
      <HeadlineBar
        state={{ kind: "missing", reason: "Headlines unavailable — the source did not answer this request." }}
        news={report.news}
      />,
    );

    expect(screen.getByTestId("headline-reason").textContent).toContain(
      "the source did not answer this request",
    );
  });

  it("says the cycle was empty when the feed answered with nothing", () => {
    const report = readHome(homeDocument({ news: [] }));
    render(<HeadlineBar state={READY} news={report.news} />);

    expect(screen.getByTestId("home-headline").textContent).toContain("No headlines in this cycle.");
    expect(screen.queryByTestId("headline-link")).toBeNull();
  });

  it("draws a placeholder line while loading", () => {
    render(<HeadlineBar state={LOADING} news={null} />);

    expect(screen.getByTestId("headline-skeleton")).toBeTruthy();
    expect(screen.queryByTestId("headline-link")).toBeNull();
  });

  it("formats a stamp in UTC and refuses an unparseable one", () => {
    expect(stampOf("2026-10-31T19:42:00Z")).toBe("19:42 UTC · 31 Oct 2026");
    expect(stampOf("not a date")).toBe("");
    expect(stampOf(null)).toBe("");
  });
});
