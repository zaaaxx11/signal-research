import * as React from "react";

import { Badge } from "@/components/ui/badge";
import type { SymbolHit } from "@/lib/api";

import { SymbolSearch } from "./SymbolSearch";

export interface TopBarProps {
  onSelect: (hit: SymbolHit) => void;
  inputRef: React.RefObject<HTMLInputElement | null>;
}

/**
 * Identity and the one global control.
 *
 * Deliberately not a navbar: no menu, no avatar, no marketing links — a
 * screener's top bar has to earn 48 px by holding the search box. The
 * wordmark is set in the page's own mono/sans stack (no webfont request).
 */
export function TopBar({ onSelect, inputRef }: TopBarProps) {
  return (
    <header className="flex h-12 shrink-0 items-center gap-4 border-b border-line bg-surface px-3">
      <div className="flex shrink-0 items-center gap-2.5">
        <span
          aria-hidden="true"
          className="flex size-[22px] items-center justify-center rounded-[4px] border border-line-strong bg-surface-sunken font-mono text-[13px] font-bold leading-none text-up"
        >
          Z
        </span>
        <div className="leading-none">
          <div className="text-ui font-semibold uppercase tracking-[0.2em] text-fg">
            Ztockz
          </div>
          <div className="mt-1 text-label uppercase tracking-[0.24em] text-fg-subtle">
            Equity screener
          </div>
        </div>
      </div>

      <div className="flex min-w-0 flex-1 justify-center px-2">
        <SymbolSearch
          onSelect={onSelect}
          inputRef={inputRef}
          className="max-w-[560px]"
        />
      </div>

      <div className="flex shrink-0 items-center gap-2.5">
        <Badge
          variant="outline"
          className="hidden rounded-sm border-line px-1.5 py-0 font-mono text-label uppercase tracking-[0.14em] text-fg-subtle sm:inline-flex"
        >
          Preview
        </Badge>
        <span className="font-mono text-label tracking-[0.06em] text-fg-subtle">
          v{__APP_VERSION__}
        </span>
      </div>
    </header>
  );
}
