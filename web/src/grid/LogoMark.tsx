/**
 * The brand mark, drawn from `/logos/{SYMBOL}`.
 *
 * Three states, one box:
 *
 *   loading   the monogram is painted; the image is requested behind it
 *   loaded    the image replaces the monogram
 *   failed    the monogram stays and the `<img>` leaves the DOM — never a
 *             broken-image glyph, never a row that jumps because a mark was
 *             missing
 *
 * The box is reserved at `LOGO_SIZE` × `LOGO_SIZE` on the wrapper, so the lane's
 * geometry does not depend on the network: a 404, a 500, a slow route and a
 * 30-day-cached PNG all occupy the same 20 px.
 *
 * `alt=""` is not an omission: the mark repeats the symbol that is printed
 * beside it, and a screen reader that heard it twice would be reading noise.
 */

import * as React from "react";

import { cn } from "@/lib/utils";

import { LOGO_SIZE, logoPath } from "./logo";

export type LogoState = "loading" | "loaded" | "failed";

export interface LogoMarkProps {
  symbol: string;
  className?: string;
}

export function LogoMark({ symbol, className }: LogoMarkProps) {
  const [state, setState] = React.useState<LogoState>("loading");

  // A row may be re-used for another symbol (a page turn keeps the DOM nodes);
  // the fallback must come back with the new symbol rather than inherit the old
  // one's answer.
  React.useEffect(() => {
    setState("loading");
  }, [symbol]);

  return (
    <span
      data-logo={symbol}
      data-logo-state={state}
      style={{ width: LOGO_SIZE, height: LOGO_SIZE }}
      className={cn(
        "relative flex shrink-0 items-center justify-center overflow-hidden rounded-[2px] border border-line bg-surface-raised",
        className,
      )}
    >
      {state === "loaded" ? null : (
        <span
          aria-hidden="true"
          data-testid={`monogram-${symbol}`}
          className="font-mono text-[9px] leading-none text-fg-muted"
        >
          {symbol.slice(0, 1).toUpperCase()}
        </span>
      )}

      {state === "failed" ? null : (
        <img
          src={logoPath(symbol)}
          alt=""
          width={LOGO_SIZE}
          height={LOGO_SIZE}
          loading="lazy"
          decoding="async"
          data-testid={`logo-img-${symbol}`}
          onLoad={() => setState("loaded")}
          onError={() => setState("failed")}
          className={cn(
            "size-full object-contain",
            // Hidden, not absent: the request has to keep running while the
            // monogram is what the reader sees.
            state === "loading" && "absolute inset-0 opacity-0",
          )}
        />
      )}
    </span>
  );
}
