/**
 * The product's one icon set: inline SVG, `currentColor`, 14 px by default.
 *
 * Why hand-drawn paths instead of an icon package: the toolbar carries a dozen
 * glyphs, and a tree-shaken import of a library still ships its runtime. These
 * are the twelve shapes the chrome actually uses, written once, drawn at the
 * label's own colour and optical size, and impossible to drift into a second
 * visual language.
 *
 * Two rules the component enforces:
 *
 * * **Decorative by default.** An icon beside a word is `aria-hidden` — the
 *   word is the name. An icon that *is* the button must be named by its button,
 *   not by the SVG; `label` exists for the few places a glyph stands alone.
 * * **It inherits ink.** `stroke="currentColor"` and no fill: the button's
 *   `text-*` class is the icon's colour, in both themes, with no second token.
 */

import * as React from "react";

import { cn } from "@/lib/utils";

export type IconName =
  | "search"
  | "funnel"
  | "columns"
  | "sort"
  | "pulse"
  | "star"
  | "split"
  | "bell"
  | "download"
  | "chevron-left"
  | "chevron-right"
  | "chevron-down"
  | "pencil"
  | "x"
  | "list"
  | "refresh"
  | "clock"
  | "bookmark"
  | "plus";

/**
 * One path (or a few) per name, drawn on a 24-unit grid.
 *
 * Kept as data rather than a switch so the test can assert every name draws
 * something, and so adding a glyph is one entry, not a new component.
 */
const PATHS: Record<IconName, React.ReactNode> = {
  search: (
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.6-3.6" />
    </>
  ),
  funnel: <path d="M3 5h18l-7 8v6l-4 2v-8L3 5Z" />,
  columns: (
    <>
      <rect x="3" y="4" width="18" height="16" rx="1.5" />
      <path d="M10 4v16M17 4v16" />
    </>
  ),
  sort: <path d="M8 4v16m0 0-3-3m3 3 3-3M16 20V4m0 0-3 3m3-3 3 3" />,
  pulse: <path d="M2 12h4l2.5-6 4 12 2.5-6h7" />,
  star: <path d="m12 3 2.7 5.7 6.3.8-4.6 4.3 1.2 6.2L12 17l-5.6 3 1.2-6.2L3 9.5l6.3-.8L12 3Z" />,
  split: (
    <>
      <rect x="3" y="4" width="18" height="16" rx="1.5" />
      <path d="M12 4v16" />
    </>
  ),
  bell: (
    <>
      <path d="M6 16V10a6 6 0 1 1 12 0v6l1.5 2.5h-15L6 16Z" />
      <path d="M10 21h4" />
    </>
  ),
  download: <path d="M12 3v11m0 0 4-4m-4 4-4-4M4 19h16" />,
  "chevron-left": <path d="m14 6-6 6 6 6" />,
  "chevron-right": <path d="m10 6 6 6-6 6" />,
  "chevron-down": <path d="m6 10 6 6 6-6" />,
  pencil: (
    <>
      <path d="M4 20h4L20 8l-4-4L4 16v4Z" />
      <path d="m14 6 4 4" />
    </>
  ),
  x: <path d="m6 6 12 12M18 6 6 18" />,
  list: <path d="M4 6h2m4 0h10M4 12h2m4 0h10M4 18h2m4 0h10" />,
  refresh: (
    <>
      <path d="M20 12a8 8 0 1 1-2.3-5.6" />
      <path d="M20 4v5h-5" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="8" />
      <path d="M12 8v4.5l3 1.5" />
    </>
  ),
  bookmark: <path d="M6 4h12v17l-6-4-6 4V4Z" />,
  plus: <path d="M12 5v14M5 12h14" />,
};

export interface IconProps extends Omit<React.SVGProps<SVGSVGElement>, "name"> {
  name: IconName;
  /** The optical size in px. 14 beside a 12 px label, 16 for a lone button. */
  size?: number;
  className?: string;
  /**
   * The accessible name, only for an icon that carries the meaning alone.
   * Omitted, the SVG is decorative: the button around it must be named.
   */
  label?: string;
}

export function Icon({ name, size = 14, className, label, ...rest }: IconProps) {
  return (
    <svg
      {...rest}
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      // A glyph that is part of a labelled control must not repeat that label.
      aria-hidden={label ? undefined : true}
      aria-label={label}
      role={label ? "img" : undefined}
      focusable="false"
      data-icon={name}
      // Optical alignment: the svg sits on the text baseline rather than
      // floating above it, so an icon + word row reads as one line of type.
      className={cn("inline-block shrink-0 align-[-0.125em]", className)}
    >
      {PATHS[name]}
    </svg>
  );
}
