/**
 * Card grid: a responsive grid of cards, Finviz-style.
 *
 * Each child is a card with a border, surface background, and hover state.
 * The grid auto-fills with a minimum column width of 180px.
 */

import * as React from "react";

import { cn } from "@/lib/utils";

export interface CardGridProps {
  readonly children: React.ReactNode;
  /** Override the minimum column width. Default 180px. */
  readonly minWidth?: number;
  /** Gap between cards. Default 3 (12px in Tailwind scale). */
  readonly gap?: number;
  /** Additional classes on the grid container. */
  readonly className?: string;
}

export function CardGrid({
  children,
  minWidth = 180,
  gap = 3,
  className,
}: CardGridProps) {
  return (
    <div
      data-testid="card-grid"
      className={cn("grid gap-3", className)}
      style={{
        gridTemplateColumns: `repeat(auto-fill, minmax(${minWidth}px, 1fr))`,
        gap: `${gap * 4}px`,
      }}
    >
      {children}
    </div>
  );
}

export interface CardGridCardProps {
  readonly children: React.ReactNode;
  readonly className?: string;
}

export function CardGridCard({ children, className }: CardGridCardProps) {
  return (
    <div
      className={cn(
        "rounded-md border border-line bg-surface p-3 transition-colors hover:border-line-strong",
        className,
      )}
    >
      {children}
    </div>
  );
}