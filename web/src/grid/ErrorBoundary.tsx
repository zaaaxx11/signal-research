/**
 * The grid's last line of defence.
 *
 * A throw inside `render` — the solver's invariant, a bad cell, a future
 * component — used to travel to the React root and unmount the whole shell:
 * `#root` with zero children, no message, no way back but a reload. A panel is
 * one band of the page; a fault in it must cost the panel, not the page.
 *
 * It catches **render** faults only. A failed request is not an exception here:
 * `useScreens` catches it and the table renders the API's own sentence inside
 * the grid, so a 422 still reads as a 422. This boundary must never be the
 * thing that hides an error from the reader.
 *
 * It also catches the one fault that is neither: a **lazy chunk that never
 * arrives**. `React.lazy` rejects when its `import()` rejects (a deploy rotated
 * the hash while the tab was open), React throws that rejection during render,
 * and the nearest boundary is where it lands. `app.tsx` wraps the detail panel
 * in one for exactly that reason, with a bounded retry inside the import.
 */

import * as React from "react";

import { cn } from "@/lib/utils";

export interface ErrorBoundaryProps {
  children: React.ReactNode;
  /** Changing this clears a previous failure: a new view is a new chance. */
  resetKey?: string | number;
  /** What failed, in the reader's words: "Today panel", "the grid". */
  label?: string;
  /**
   * The sentence under the label. The default speaks about rendering, which is
   * wrong for a chunk that never downloaded; a caller that knows better says so.
   */
  hint?: React.ReactNode;
  /**
   * Called *before* the boundary clears itself, so the owner can re-attempt
   * whatever failed — a fresh `React.lazy` type re-runs the dynamic import.
   */
  onRetry?: () => void;
  /** Geometry of the slot this boundary stands in (e.g. the 420 px dock). */
  className?: string;
}

interface ErrorBoundaryState {
  error: Error | null;
}

export class ErrorBoundary extends React.Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: unknown): ErrorBoundaryState {
    return { error: error instanceof Error ? error : new Error(String(error)) };
  }

  componentDidCatch(error: unknown, info: React.ErrorInfo): void {
    // Logged, never swallowed: the fallback names the fault, and the console
    // keeps the stack for whoever has to fix it.
    console.error("[grid] render failed", error, info.componentStack);
  }

  componentDidUpdate(previous: ErrorBoundaryProps): void {
    if (previous.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null });
    }
  }

  private retry = () => {
    // The owner first: it can hand back a new lazy type, and the cleared error
    // then lands on a component that has not already rejected.
    this.props.onRetry?.();
    this.setState({ error: null });
  };

  render(): React.ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <section
        role="alert"
        data-testid="grid-crash"
        className={cn(
          "flex min-h-0 flex-1 flex-col justify-center rounded-md border border-line border-l-2 border-l-alert bg-surface p-6",
          this.props.className,
        )}
      >
        <p className="text-ui font-medium text-fg">
          {this.props.label ? `${this.props.label} could not be drawn` : "This panel could not be drawn"}
        </p>
        <p className="mt-1 max-w-[70ch] break-words text-data text-fg-muted">
          {this.props.hint ?? (
            <>The rest of the page is still here. The failure was in rendering, not in the data:</>
          )}{" "}
          <span className="font-mono text-label text-fg-subtle">{error.message}</span>
        </p>
        <div className="mt-3">
          <button
            type="button"
            onClick={this.retry}
            className="rounded-sm border border-line-strong px-2 py-1 text-ui text-fg-muted transition-colors hover:text-fg"
          >
            Try again
          </button>
        </div>
      </section>
    );
  }
}
