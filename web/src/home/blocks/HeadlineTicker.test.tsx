import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { HeadlineTicker } from "./HeadlineTicker";
import { READY, readHome } from "../home";
import { homeDocument } from "../__fixtures__/home";

/**
 * The ticker's promises: the stories are real links, the second copy is
 * decoration (hidden from assistive tech), the loop duration follows the list,
 * and there is no sliding bar when there is nothing to slide.
 */

describe("HeadlineTicker", () => {
  it("renders each story twice and hides the copy", () => {
    const report = readHome(homeDocument());
    render(<HeadlineTicker state={READY} news={report.news} />);

    const lists = screen.getByTestId("home-ticker").querySelectorAll("ul");
    expect(lists).toHaveLength(2);
    expect(lists[0]?.querySelectorAll("li")).toHaveLength(8);
    expect(lists[1]?.getAttribute("aria-hidden")).toBe("true");
    expect(lists[1]?.querySelectorAll("li")).toHaveLength(8);
  });

  it("links every story out, safely", () => {
    const report = readHome(homeDocument());
    render(<HeadlineTicker state={READY} news={report.news} />);

    const anchors = [...screen.getByTestId("ticker-track").querySelectorAll("a")];
    expect(anchors.length).toBeGreaterThan(0);
    for (const anchor of anchors) {
      expect(anchor.getAttribute("href")).toMatch(/^https:\/\//);
      expect(anchor.getAttribute("target")).toBe("_blank");
      expect(anchor.getAttribute("rel")).toBe("noopener noreferrer");
    }
  });

  it("sets a duration from the number of stories, not a constant", () => {
    const many = readHome(homeDocument());
    const { unmount } = render(<HeadlineTicker state={READY} news={many.news} />);
    const eight = screen.getByTestId("ticker-track").style.getPropertyValue("--zt-ticker-duration");
    unmount();

    render(<HeadlineTicker state={READY} news={many.news?.slice(0, 2) ?? null} />);
    const two = screen.getByTestId("ticker-track").style.getPropertyValue("--zt-ticker-duration");
    expect(eight).toBe("40s"); // 8 stories × 5 s
    expect(two).toBe("28s"); // floor keeps a short list readable
  });

  it("draws no bar when the cycle is empty", () => {
    render(<HeadlineTicker state={READY} news={[]} />);
    expect(screen.queryByTestId("home-ticker")).toBeNull();
  });

  it("names the failure instead of sliding an empty line", () => {
    render(
      <HeadlineTicker
        state={{ kind: "missing", reason: "Headlines unavailable — the feeds did not answer." }}
        news={null}
      />,
    );
    expect(screen.getByTestId("ticker-unavailable").textContent).toContain("did not answer");
  });
});
