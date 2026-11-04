import { type CSSProperties, type ReactNode } from "react";

/**
 * The pairing box: one parent divided into children whose sizes are exactly
 * proportional to `parts`.
 *
 * The logic every page's boxes follow. A parent of 20x50 px with parts
 * [30, 10, 10] laid out `direction="col"` holds a 20x30 box then two 10x10
 * boxes (less the shared gap); the same parts in a row give 30/50 of the width
 * to the first child. The ratios are expressed as flex weights, so the exact
 * pixels belong to the container — what the contract fixes is the *proportion*,
 * which is what survives a responsive page.
 */
export function PairingBox({
  direction = "col",
  parts,
  gap = 4,
  className,
  style,
  testId,
  children,
}: {
  direction?: "row" | "col";
  /** One flex weight per child; the container divides by their sum. */
  parts: number[];
  gap?: number;
  className?: string;
  style?: CSSProperties;
  testId?: string;
  children: ReactNode[];
}) {
  if (parts.length !== children.length) {
    throw new Error(`PairingBox: ${parts.length} parts but ${children.length} children`);
  }
  const flexes = parts.map((weight) => `${weight} 1 0%`).join(", ");
  return (
    <div
      className={className}
      style={{
        display: "flex",
        flexDirection: direction === "row" ? "row" : "column",
        gap,
        minHeight: 0,
        ...style,
      }}
      data-testid={testId}
      data-parts={flexes}
    >
      {children.map((child, index) => (
        <div
          key={index}
          style={{ flex: parts[index], minHeight: 0, minWidth: 0 }}
          data-pairing-index={index}
        >
          {child}
        </div>
      ))}
    </div>
  );
}
