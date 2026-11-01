/**
 * A small modal shell for the toolbar's dialogs.
 *
 * One implementation of the four things a dialog owes a keyboard: it is a
 * `dialog` named by its title, focus moves inside when it opens and back to the
 * opener when it closes, Tab wraps instead of escaping into the page behind,
 * and Escape closes it. The compare, alert and export panels are content; this
 * is the frame.
 */

import { XIcon } from "lucide-react";
import * as React from "react";

import { cn } from "@/lib/utils";

export interface DialogShellProps {
  open: boolean;
  onClose: () => void;
  title: string;
  /** A sentence under the title, when the panel needs one. */
  description?: string;
  children: React.ReactNode;
  /** Width class for the panel; the default is a comfortable form. */
  size?: "sm" | "md" | "lg";
  labelledBy?: string;
}

let dialogSequence = 0;
export function nextDialogTitleId(): string {
  dialogSequence += 1;
  return `toolbar-dialog-${dialogSequence}`;
}

export function DialogShell({
  open,
  onClose,
  title,
  description,
  children,
  size = "md",
  labelledBy,
}: DialogShellProps) {
  const panelRef = React.useRef<HTMLDivElement>(null);
  const openerRef = React.useRef<HTMLElement | null>(null);
  const [titleId] = React.useState(() => labelledBy ?? nextDialogTitleId());

  React.useEffect(() => {
    if (!open) return;
    openerRef.current = document.activeElement as HTMLElement | null;
    const id = window.setTimeout(() => {
      const focusable = panelRef.current?.querySelector<HTMLElement>(
        "input:not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled])",
      );
      (focusable ?? panelRef.current)?.focus();
    }, 0);
    return () => {
      window.clearTimeout(id);
      const opener = openerRef.current;
      if (opener && opener.isConnected) opener.focus();
    };
  }, [open]);

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key !== "Tab" || !panelRef.current) return;
    const focusable = [
      ...panelRef.current.querySelectorAll<HTMLElement>(
        "button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href]",
      ),
    ].filter((element) => element.offsetParent !== null || element === document.activeElement);
    if (focusable.length === 0) return;
    const first = focusable[0]!;
    const last = focusable[focusable.length - 1]!;
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 px-3 py-10">
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        onKeyDown={onKeyDown}
        className={cn(
          "w-full overflow-hidden rounded-md border border-line-strong bg-surface shadow-2xl",
          size === "sm" ? "max-w-[380px]" : size === "lg" ? "max-w-[860px]" : "max-w-[560px]",
        )}
      >
        <div className="flex items-start justify-between gap-3 border-b border-line px-3 py-2">
          <div className="min-w-0">
            <h2 id={titleId} className="text-ui font-semibold text-fg">
              {title}
            </h2>
            {description ? <p className="mt-0.5 text-data text-fg-subtle">{description}</p> : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={`Close ${title}`}
            className="shrink-0 rounded-sm p-1 text-fg-muted transition-colors hover:text-fg"
          >
            <XIcon aria-hidden="true" className="size-3.5" />
          </button>
        </div>
        <div className="px-3 py-2.5">{children}</div>
      </div>
    </div>
  );
}
