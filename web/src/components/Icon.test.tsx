import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Icon, type IconName } from "./Icon";

/**
 * The one icon contract: a glyph beside a word is silent, a glyph that is the
 * whole control is named, and every glyph paints in the ink around it.
 */

const NAMES: IconName[] = [
  "search",
  "funnel",
  "columns",
  "sort",
  "pulse",
  "star",
  "split",
  "bell",
  "download",
  "chevron-left",
  "chevron-right",
  "chevron-down",
  "pencil",
  "x",
  "list",
  "refresh",
  "clock",
  "bookmark",
  "plus",
];

describe("Icon", () => {
  it("is decorative by default: aria-hidden, no role, no name", () => {
    render(<Icon name="star" />);
    const svg = document.querySelector('[data-icon="star"]') as SVGSVGElement;
    expect(svg.tagName.toLowerCase()).toBe("svg");
    expect(svg.getAttribute("aria-hidden")).toBe("true");
    expect(svg.hasAttribute("role")).toBe(false);
    expect(svg.hasAttribute("aria-label")).toBe(false);
    expect(svg.getAttribute("focusable")).toBe("false");
    // It paints in the text colour, so dark and day themes need no second token.
    expect(svg.getAttribute("stroke")).toBe("currentColor");
    expect(svg.getAttribute("width")).toBe("14");
    expect(svg.getAttribute("height")).toBe("14");
  });

  it("is named when it carries the meaning alone", () => {
    render(<Icon name="bell" label="Alerts" size={16} />);
    const svg = screen.getByRole("img", { name: "Alerts" });
    expect(svg.getAttribute("aria-hidden")).toBeNull();
    expect(svg.getAttribute("width")).toBe("16");
  });

  it("draws something for every name", () => {
    for (const name of NAMES) {
      const { container, unmount } = render(<Icon name={name} />);
      const svg = container.querySelector("svg");
      expect(svg, `${name} draws no svg`).toBeTruthy();
      expect((svg?.children.length ?? 0) > 0, `${name} draws no path`).toBe(true);
      unmount();
    }
  });
});
