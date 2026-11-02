import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LogoMark } from "./LogoMark";
import { forgetLogo } from "./logo";
import { LOGO_SIZE } from "./logo";

/**
 * The mark's states, one box.
 *
 * `loading` is a neutral skeleton — a placeholder is not an answer, and painting
 * the letter monogram while the request is in flight is what made a slow logo
 * look like a missing one. `loaded` is the endpoint's image. `failed` is the
 * letter drawn **locally**: the endpoint already answers a monogram with a 200,
 * so a failure here is a transport failure, and asking it again after the one
 * retry would only freeze the same placeholder in place.
 *
 * A retry is one extra request with a cache-busting query, because the browser
 * may hold the old answer under the plain URL.
 */

function image(symbol = "AAPL"): HTMLImageElement {
  return screen.getByTestId(`logo-img-${symbol}`) as HTMLImageElement;
}

function box(symbol = "AAPL"): HTMLElement {
  return image(symbol).parentElement as HTMLElement;
}

describe("LogoMark", () => {
  it("shows a neutral skeleton before any bytes arrive, never the monogram", () => {
    forgetLogo("AAPL");
    render(<LogoMark symbol="AAPL" />);

    expect(box().getAttribute("data-logo-state")).toBe("loading");
    expect(screen.getByTestId("logo-skeleton-AAPL")).toBeTruthy();
    expect(screen.queryByTestId("monogram-AAPL")).toBeNull();

    // The request is already in flight behind the skeleton.
    expect(image().getAttribute("src")).toBe("/logos/AAPL");
    expect(image().getAttribute("loading")).toBe("eager");
    expect(image().getAttribute("decoding")).toBe("async");
  });

  it("reserves the same box in every state, so nothing shifts", () => {
    const { container } = render(<LogoMark symbol="AAPL" />);
    const wrapper = () => container.querySelector("[data-logo-state]") as HTMLElement;

    expect(wrapper().style.width).toBe(`${LOGO_SIZE}px`);
    expect(wrapper().style.height).toBe(`${LOGO_SIZE}px`);

    fireEvent.error(image());
    expect(wrapper().style.width).toBe(`${LOGO_SIZE}px`);
    expect(wrapper().style.height).toBe(`${LOGO_SIZE}px`);

    fireEvent.error(image());
    expect(wrapper().style.width).toBe(`${LOGO_SIZE}px`);
    expect(wrapper().style.height).toBe(`${LOGO_SIZE}px`);

    const other = render(<LogoMark symbol="MSFT" size={16} />);
    const msft = other.container.querySelector("[data-logo-state]") as HTMLElement;
    expect(msft.style.width).toBe("16px");
    expect(msft.style.height).toBe("16px");
  });

  it("swaps the skeleton for the endpoint image when it loads", () => {
    render(<LogoMark symbol="AAPL" />);
    expect(image().className).toContain("opacity-0");

    fireEvent.load(image());

    expect(screen.queryByTestId("logo-skeleton-AAPL")).toBeNull();
    expect(screen.queryByTestId("monogram-AAPL")).toBeNull();
    expect(box().getAttribute("data-logo-state")).toBe("loaded");
    expect(image().getAttribute("src")).toBe("/logos/AAPL");
    expect(image().className).not.toContain("opacity-0");
  });

  it("retries exactly once with a cache-busting query, then draws the monogram locally", () => {
    render(<LogoMark symbol="AAPL" />);

    fireEvent.error(image());

    // One retry: same symbol, different URL, skeleton still on screen.
    const retry = image();
    expect(retry.getAttribute("src")).toMatch(/^\/logos\/AAPL\?v=[a-z0-9]+$/);
    expect(retry.getAttribute("src")).not.toBe("/logos/AAPL");
    expect(screen.getByTestId("logo-skeleton-AAPL")).toBeTruthy();
    expect(screen.queryByTestId("monogram-AAPL")).toBeNull();

    fireEvent.error(retry);

    // The retry failed too: no <img> (never a broken glyph) and the letter is
    // drawn from the symbol itself, not fetched from the endpoint again.
    expect(screen.queryByTestId("logo-img-AAPL")).toBeNull();
    expect(screen.queryByTestId("logo-skeleton-AAPL")).toBeNull();
    expect(screen.getByTestId("monogram-AAPL").textContent).toBe("A");
    expect(
      (screen.getByTestId("monogram-AAPL").closest("[data-logo-state]") as HTMLElement).getAttribute(
        "data-logo-state",
      ),
    ).toBe("failed");
  });

  it("starts over when a row is reused for another symbol", () => {
    const { rerender } = render(<LogoMark symbol="AAPL" />);

    fireEvent.error(image());
    fireEvent.error(image());
    expect(screen.getByTestId("monogram-AAPL")).toBeTruthy();

    rerender(<LogoMark symbol="MSFT" />);

    expect(screen.queryByTestId("monogram-AAPL")).toBeNull();
    expect(screen.queryByTestId("monogram-MSFT")).toBeNull();
    expect(screen.getByTestId("logo-skeleton-MSFT")).toBeTruthy();
    expect(image("MSFT").getAttribute("src")).toBe("/logos/MSFT");
  });
});
