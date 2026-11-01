/**
 * The card every block is made of.
 *
 * A block is a `<section>` with a level-2 heading, a hairline under it, and a
 * body that either draws its content, a skeleton of the same shape, or one
 * sentence naming why it cannot. The three states share one box:
 *
 *   * the skeleton repeats the content's own structure (eight tiles, six tiles,
 *     rows) rather than a generic grey rectangle, so the answer cannot move the
 *     page when it lands;
 *   * `height` pins the box for the two blocks whose height must not depend on
 *     the payload (the movers list scrolls inside 40 rows, the map is a canvas);
 *   * `minHeight` reserves the floor for the rest, where the payload's row count
 *     is fixed by the design.
 *
 * The heading is not decoration: the page's `h1` is visually hidden, so `h2` is
 * the first thing a screen reader's heading list shows and every block needs
 * one to be findable by name.
 */

import * as React from "react";

import { cn } from "@/lib/utils";

import type { BlockState } from "../home";

/** The reserved floor, per block, in px. One source so the page and the tests
 * agree about what "no layout shift" means. */
export const BLOCK_HEIGHT = {
  headline: 34,
  strip: 84,
  breadth: 76,
  /** The movers body scrolls; the card is the same 420 px with 0 or 40 rows. */
  movers: 420,
  map: 420,
  /** Signals, headlines and majors sit in one row and share a height. */
  column: 188,
  insider: 92,
} as const;

export interface CardProps {
  readonly id: string;
  readonly title: string;
  readonly className?: string;
  readonly height?: number;
  readonly minHeight?: number;
  readonly actions?: React.ReactNode;
  readonly children: React.ReactNode;
}

export function Card({ id, title, className, height, minHeight, actions, children }: CardProps) {
  return (
    <section
      data-testid={`home-${id}`}
      data-block={id}
      style={height !== undefined ? { height } : minHeight !== undefined ? { minHeight } : undefined}
      className={cn(
        "flex min-w-0 flex-col overflow-hidden rounded-md border border-line bg-surface",
        className,
      )}
    >
      <header className="flex h-[26px] shrink-0 items-center justify-between gap-2 border-b border-line-strong px-2">
        <h2 className="truncate text-[12px] font-medium text-fg">{title}</h2>
        {actions}
      </header>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">{children}</div>
    </section>
  );
}

/** One sentence, in the body's place. Never a zero, never an empty table. */
export function BlockMessage({
  id,
  reason,
  failed = false,
}: {
  readonly id: string;
  readonly reason: string;
  readonly failed?: boolean;
}) {
  return (
    <p
      data-testid={`${id}-reason`}
      title={reason}
      className={cn(
        "min-w-0 px-2 py-2 text-[11px] leading-snug",
        failed ? "text-alert" : "text-fg-subtle",
      )}
    >
      {reason}
    </p>
  );
}

export interface HomeBlockProps {
  readonly id: string;
  readonly title: string;
  readonly state: BlockState;
  readonly skeleton: React.ReactNode;
  readonly className?: string;
  readonly height?: number;
  readonly minHeight?: number;
  readonly actions?: React.ReactNode;
  readonly children: React.ReactNode;
}

/**
 * The card plus its three states.
 *
 * A `ready` block with `null` data cannot happen through `blockState`, but the
 * body is written so a block that receives nothing still says why instead of
 * drawing zeros — the failure mode this page exists to avoid.
 */
export function HomeBlock({
  id,
  title,
  state,
  skeleton,
  className,
  height,
  minHeight,
  actions,
  children,
}: HomeBlockProps) {
  return (
    <Card
      id={id}
      title={title}
      className={className}
      height={height}
      minHeight={minHeight}
      actions={actions}
    >
      {state.kind === "loading" ? (
        skeleton
      ) : state.kind === "ready" ? (
        children
      ) : (
        <BlockMessage
          id={id}
          reason={state.reason ?? "Unavailable."}
          failed={state.kind === "error"}
        />
      )}
    </Card>
  );
}

/** A titled sub-section inside a card: the strip's two halves. */
export function SubHeading({ children, id }: { children: React.ReactNode; id?: string }) {
  return (
    <h3 id={id} className="px-2 pt-1.5 pb-1 text-[10px] uppercase tracking-[0.05em] text-fg-subtle">
      {children}
    </h3>
  );
}

/* ---------------------------------------------------------------------------
 * Skeletons. Shaped like the content they stand in for, never a grey box.
 * ------------------------------------------------------------------------ */

export function SkeletonBar({ width, height = 9 }: { width: string; height?: number }) {
  return (
    <span
      aria-hidden="true"
      style={{ width, height }}
      className="block rounded-[1px] bg-surface-raised"
    />
  );
}

/** `count` placeholder rows, each a bar of a different length. */
export function SkeletonRows({ count, testId }: { count: number; testId: string }) {
  return (
    <div aria-hidden="true" data-testid={testId} className="flex flex-col">
      {Array.from({ length: count }, (_, index) => (
        <div key={index} className="flex h-5 items-center gap-2 px-2">
          <SkeletonBar width={index % 3 === 0 ? "58%" : index % 3 === 1 ? "72%" : "46%"} />
        </div>
      ))}
    </div>
  );
}

/** A grid of placeholder tiles, for the strip and the breadth row. */
export function SkeletonTiles({
  count,
  columns,
  height,
  testId,
}: {
  count: number;
  columns: string;
  height: number;
  testId: string;
}) {
  return (
    <div aria-hidden="true" data-testid={testId} className={cn("grid gap-2 p-2", columns)}>
      {Array.from({ length: count }, (_, index) => (
        <div
          key={index}
          style={{ height }}
          className="flex flex-col justify-between rounded-sm border border-line bg-surface-sunken p-1.5"
        >
          <SkeletonBar width="62%" height={7} />
          <SkeletonBar width="44%" height={12} />
        </div>
      ))}
    </div>
  );
}
