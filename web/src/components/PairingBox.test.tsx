import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PairingBox } from "./PairingBox";

describe("PairingBox", () => {
  it("divides a container proportionally to the parts", () => {
    const { container } = render(
      <PairingBox direction="col" parts={[30, 10, 10]} testId="pb">
        <div data-testid="a" />
        <div data-testid="b" />
        <div data-testid="c" />
      </PairingBox>,
    );
    const a = container.querySelector('[data-testid="a"]') as HTMLElement;
    // The wrapper div carries the flex weight; the child is its content.
    expect(a.parentElement?.style.flex).toContain("30");
    const b = container.querySelector('[data-testid="b"]') as HTMLElement;
    expect(b.parentElement?.style.flex).toContain("10");
    const frame = container.querySelector('[data-testid="pb"]') as HTMLElement;
    expect(frame.style.flexDirection).toBe("column");
  });

  it("refuses a mismatch between parts and children", () => {
    expect(() =>
      render(
        <PairingBox parts={[1, 2, 3]}>
          <div />
          <div />
        </PairingBox>,
      ),
    ).toThrow(/3 parts but 2 children/);
  });

  it("supports row direction and a gap", () => {
    const { container } = render(
      <PairingBox direction="row" parts={[2, 1]} gap={4}>
        <div />
        <div />
      </PairingBox>,
    );
    const frame = container.firstElementChild as HTMLElement;
    expect(frame.style.flexDirection).toBe("row");
    expect(frame.style.gap).toBe("4px");
  });
});
