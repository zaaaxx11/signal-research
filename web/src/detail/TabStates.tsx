/**
 * The four states every tab owes the reader, in one vocabulary.
 *
 * A skeleton is per tab, so a slow fundamentals read never animates the News
 * tab; an error names only the resource that failed and offers its own retry;
 * an empty answer is a sentence, not a red box. Nothing here logs: an empty
 * insider list is normal and must stay silent in the console.
 */

import * as React from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

import type { MetricCell } from "./metrics";

export interface TabSkeletonProps {
  label: string;
  testId: string;
  rows?: number;
}

export function TabSkeleton({ label, testId, rows = 8 }: TabSkeletonProps): React.ReactElement {
  return (
    <div
      role="status"
      aria-busy="true"
      aria-live="polite"
      data-testid={testId}
      className="flex flex-col gap-2"
    >
      <span className="sr-only">Loading {label}…</span>
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className="flex items-center justify-between gap-3">
          <Skeleton className={cn("h-3", index % 3 === 0 ? "w-2/3" : "w-1/2")} />
          <Skeleton className="h-3 w-12" />
        </div>
      ))}
    </div>
  );
}

export interface TabErrorProps {
  label: string;
  testId: string;
  message: string | null;
  onRetry: () => void;
}

export function TabError({ label, testId, message, onRetry }: TabErrorProps): React.ReactElement {
  return (
    <div
      role="alert"
      data-testid={testId}
      className="rounded-sm border border-line border-l-2 border-l-alert bg-surface-sunken p-2"
    >
      <p className="text-data font-medium text-fg">Could not load {label}.</p>
      {message ? (
        <p className="mt-1 break-words text-data text-fg-muted">{message}</p>
      ) : null}
      <button
        type="button"
        onClick={onRetry}
        className="mt-2 rounded-sm border border-line-control px-2 py-1 text-data text-fg-muted hover:text-fg"
      >
        Retry
      </button>
    </div>
  );
}

export interface TabEmptyProps {
  testId: string;
  children: React.ReactNode;
}

export function TabEmpty({ testId, children }: TabEmptyProps): React.ReactElement {
  return (
    <div
      data-testid={testId}
      className="rounded-sm border border-line bg-surface-sunken px-3 py-4"
    >
      <p className="break-words text-data text-fg-muted">{children}</p>
    </div>
  );
}

export interface MetricRowViewProps {
  label: string;
  cell: MetricCell;
  testId: string;
}

/**
 * One label/value/unit row. The label column is `minmax(0,1fr)` and wraps, so a
 * metric key longer than the panel cannot push the value out of a 360 px dock.
 */
export function MetricRowView({
  label,
  cell,
  testId,
}: MetricRowViewProps): React.ReactElement {
  return (
    <div
      data-testid={testId}
      className="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-2 py-1"
    >
      <dt className="min-w-0 break-words text-data text-fg-muted" title={label}>
        {label}
      </dt>
      <dd className="flex items-baseline justify-end gap-1.5 whitespace-nowrap text-data text-fg">
        <span className={cn("nums", cell.absent && "text-fg-subtle")}>{cell.text}</span>
        {cell.unit.length > 0 ? (
          <span className="w-7 shrink-0 text-right text-label uppercase tracking-[0.06em] text-fg-subtle">
            {cell.unit}
          </span>
        ) : (
          <span aria-hidden="true" className="w-7 shrink-0" />
        )}
      </dd>
    </div>
  );
}
