import { Button } from "@/components/ui/button";

/**
 * The fetch-failure display every page's catch renders: what broke, why (the
 * error's own sentence, as text — never re-thrown and never swallowed), and one
 * Retry that re-runs the read the page was doing.
 */
export function ErrorState({
  title = "Something went wrong",
  error,
  onRetry,
  testId = "error-state",
}: {
  title?: string;
  error: unknown;
  onRetry?: () => void;
  testId?: string;
}) {
  const detail =
    error instanceof Error ? error.message : error != null ? String(error) : "Unknown problem";
  return (
    <div
      role="alert"
      data-testid={testId}
      className="flex flex-col items-start gap-2 rounded-[6px] border border-[var(--border)] bg-[var(--surface)] px-4 py-5"
    >
      <div className="text-[13px] font-semibold text-[var(--down)]">{title}</div>
      <p className="max-w-md text-[12px] leading-snug text-[var(--fg-muted)]">{detail}</p>
      {onRetry ? (
        <Button size="sm" variant="outline" onClick={onRetry} data-testid={`${testId}-retry`}>
          Retry
        </Button>
      ) : null}
    </div>
  );
}
