import { useEffect, useState } from "react";

import { dismiss, subscribe, type Toast, type ToastKind } from "@/lib/notify";

/**
 * The one renderer for every notification. Mounted once at the shell; the
 * store decides *what* shows, this decides *how*: colour per kind (tokens only:
 * --up/--down/--alert/--accent), a slide-and-fade entrance handled by CSS
 * transitions on transform/opacity (compositor-only, so no jank), and an
 * aria-live="polite" region so screen readers hear it without a focus steal.
 */

const KIND_STYLE: Record<ToastKind, { bg: string; ink: string; icon: string }> = {
  info: { bg: "var(--accent-strong)", ink: "var(--accent-fg)", icon: "i" },
  success: { bg: "var(--up)", ink: "#123", icon: "✓" },
  warn: { bg: "var(--alert)", ink: "#221a05", icon: "!" },
  error: { bg: "var(--down)", ink: "#200a08", icon: "✕" },
};

function ToastRow({ toast }: { toast: Toast }) {
  const [enters, setEnters] = useState(false);
  useEffect(() => {
    const frame = requestAnimationFrame(() => setEnters(true));
    return () => cancelAnimationFrame(frame);
  }, []);
  const kind = KIND_STYLE[toast.kind];
  return (
    <div
      role="status"
      data-toast-kind={toast.kind}
      className="pointer-events-auto flex max-w-xs items-start gap-2 overflow-hidden rounded-[6px] px-3 py-2 text-[12px] shadow-lg transition-all duration-300 ease-out"
      style={{
        background: kind.bg,
        color: kind.ink,
        transform: enters ? "translateY(0)" : "translateY(-8px)",
        opacity: enters ? 1 : 0,
      }}
    >
      <span aria-hidden className="mt-[1px] font-bold">{kind.icon}</span>
      <div className="min-w-0">
        <div className="font-semibold leading-tight">{toast.title}</div>
        {toast.body ? <div className="opacity-80 leading-snug">{toast.body}</div> : null}
      </div>
      <button
        type="button"
        aria-label="Dismiss notification"
        className="ml-auto shrink-0 rounded p-0.5 opacity-70 hover:opacity-100"
        onClick={() => dismiss(toast.id)}
      >
        ✕
      </button>
    </div>
  );
}

export function ToastCenter({ testId = "toast-center" }: { testId?: string }) {
  const [toasts, setToasts] = useState<readonly Toast[]>([]);
  useEffect(() => subscribe(setToasts), []);
  return (
    <div
      aria-live="polite"
      aria-atomic="false"
      className="pointer-events-none fixed inset-x-0 top-2 z-[60] flex flex-col items-center gap-2"
      data-testid={testId}
    >
      {toasts.map((toast) => (
        <ToastRow key={toast.id} toast={toast} />
      ))}
    </div>
  );
}
