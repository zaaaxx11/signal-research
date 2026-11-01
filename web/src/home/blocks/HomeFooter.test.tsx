import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { HomeFooter } from "./HomeFooter";

/** The footer is the word, a landmark, and 32 px of nothing else. */
describe("HomeFooter", () => {
  it("is a footer landmark that says ztockz and nothing else", () => {
    const { container } = render(<HomeFooter />);

    const footer = screen.getByTestId("home-footer");
    expect(footer.tagName).toBe("FOOTER");
    expect(footer.textContent).toBe("ztockz");
    expect(container.querySelectorAll("a")).toHaveLength(0);
    // No version, no build stamp, no as-of: internal chrome is not product copy.
    expect(footer.textContent).not.toMatch(/v\d|as of|build|20\d\d/i);
  });
});
