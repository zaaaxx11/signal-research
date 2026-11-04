import { useEffect, useRef, useState } from "react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";

/**
 * An awaitable confirm: `confirmAction(spec)` opens a modal and resolves to the
 * user's choice. Every sensitive action — clearing a watchlist, removing a
 * holding, resetting filters — goes through this, because `window.confirm`
 * blocks the renderer and cannot be themed.
 */
export interface ConfirmSpec {
  title: string;
  body: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** A destructive action paints the confirm button the destructive token. */
  danger?: boolean;
}

export type ConfirmResolver = (value: boolean) => void;

/** The Canvas registers itself here on mount; callers reach it through that. */
let opener: ((spec: ConfirmSpec, resolve: ConfirmResolver) => void) | null = null;

export function setConfirmOpener(next: typeof opener): void {
  opener = next;
}

export function confirmAction(spec: ConfirmSpec): Promise<boolean> {
  return new Promise<boolean>((resolve) => {
    if (opener === null) {
      resolve(false);
      return;
    }
    opener(spec, resolve);
  });
}

export function ConfirmDialogCanvas({ testId = "confirm-dialog" }: { testId?: string }) {
  const [spec, setSpec] = useState<ConfirmSpec | null>(null);
  const live = useRef<ConfirmResolver | null>(null);

  useEffect(() => {
    setConfirmOpener((next, resolve) => {
      if (live.current !== null) {
        // A new confirm while one is pending: the older promise loses.
        live.current(false);
      }
      live.current = resolve;
      setSpec(next);
    });
    return () => {
      setConfirmOpener(null);
      live.current = null;
    };
  }, []);

  const settle = (value: boolean) => {
    const pending = live.current;
    live.current = null;
    setSpec(null);
    if (pending) pending(value);
  };

  return (
    <Dialog
      open={spec !== null}
      onOpenChange={(open) => {
        if (!open && spec !== null) settle(false);
      }}
    >
      {spec === null ? null : (
        <DialogContent data-testid={testId} aria-describedby={undefined}>
          <DialogHeader>
            <DialogTitle>{spec.title}</DialogTitle>
            <DialogDescription>{spec.body}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => settle(false)} data-testid={`${testId}-cancel`}>
              {spec.cancelLabel ?? "Cancel"}
            </Button>
            <Button
              variant={spec.danger ? "destructive" : "default"}
              onClick={() => settle(true)}
              data-testid={`${testId}-confirm`}
            >
              {spec.confirmLabel ?? "Confirm"}
            </Button>
          </DialogFooter>
        </DialogContent>
      )}
    </Dialog>
  );
}
