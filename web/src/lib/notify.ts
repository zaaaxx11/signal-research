/**
 * The global notification store: `notify.*` from anywhere, one `<ToastCenter/>`.
 *
 * A module-level pub/sub (no react context) so any of the three markets where a
 * toast is raised — a fetch hook's catch, a confirm's rejection, a save's
 * success — can reach the renderer without prop fluids. Text comes in and goes
 * out without being trusted: rendered as text nodes, never as markup.
 */
export type ToastKind = "info" | "success" | "warn" | "error";

export interface Toast {
  readonly id: number;
  readonly kind: ToastKind;
  readonly title: string;
  readonly body?: string;
  /** Own dismisser: the default (4000) is the only number the store applies. */
  readonly ttl_ms: number;
}

type Listener = (toasts: readonly Toast[]) => void;

const AUTO_DISMISS_MS = 4000;
const MAX_VISIBLE = 4;

let next_id = 1;
let toasts: Toast[] = [];
const listeners = new Set<Listener>();
const timers = new Map<number, ReturnType<typeof setTimeout>>();

function emit(): void {
  const frozen = [...toasts];
  for (const listener of listeners) listener(frozen);
}

export function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  listener([...toasts]);
  return () => listeners.delete(listener);
}

/** Push one toast. Returns its id, so a caller can dismiss early. */
export function push(kind: ToastKind, title: string, body?: string): number {
  const id = next_id++;
  const toast: Toast = { id, kind, title, body, ttl_ms: AUTO_DISMISS_MS };
  toasts = [...toasts, toast].slice(-MAX_VISIBLE);
  emit();
  timers.set(
    id,
    setTimeout(() => dismiss(id), AUTO_DISMISS_MS),
  );
  return id;
}

export function dismiss(id: number): void {
  const timed = timers.get(id);
  if (timed !== undefined) {
    clearTimeout(timed);
    timers.delete(id);
  }
  const kept = toasts.filter((toast) => toast.id !== id);
  if (kept.length !== toasts.length) {
    toasts = kept;
    emit();
  }
}

/** Clear everything — a route change's hygiene. */
export function clearToasts(): void {
  for (const [id, timed] of timers) {
    clearTimeout(timed);
    timers.delete(id);
  }
  toasts = [];
  emit();
}

/** The four verb helpers a caller means. */
export const notify = {
  info: (title: string, body?: string) => push("info", title, body),
  success: (title: string, body?: string) => push("success", title, body),
  warn: (title: string, body?: string) => push("warn", title, body),
  error: (title: string, body?: string) => push("error", title, body),
};
