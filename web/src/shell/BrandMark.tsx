/**
 * The brand mark: a line that climbs.
 *
 * Four strokes, nothing else — a baseline, a jagged rise, the arrow head that
 * ends it, and a short inbound arrow at the top left. It is drawn with
 * `currentColor` so the header decides the ink; on the dark theme that is the
 * accent, on the light theme a darker magenta that does not wash out.
 *
 * 18 px is the header size, and at 18 px the strokes still read as a chart
 * rather than a scribble: 2.4 units on a 32-unit box is ~1.35 px there.
 */
export interface BrandMarkProps {
  /** Rendered box in px; the viewBox scales, the strokes stay proportional. */
  readonly size?: number;
  readonly className?: string;
  /** A standalone mark (favicon, empty state) needs a name; a decorative one does not. */
  readonly title?: string;
}

export function BrandMark({ size = 18, className, title }: BrandMarkProps) {
  return (
    <svg
      viewBox="0 0 32 32"
      width={size}
      height={size}
      role={title === undefined ? undefined : "img"}
      aria-hidden={title === undefined ? true : undefined}
      aria-label={title}
      focusable="false"
      className={className}
    >
      {title === undefined ? null : <title>{title}</title>}
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="2.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        {/* the floor */}
        <path d="M4 27h24" />
        {/* the climb */}
        <path d="M4 22.5 10 16l5 3.5L25 8" />
        {/* the arrow head */}
        <path d="M19 7.5h6.5V14" />
        {/* the inbound arrow, top left */}
        <path d="M4.5 6.5h6" />
        <path d="M8 4.5 10.5 6.5 8 8.5" />
      </g>
    </svg>
  );
}
