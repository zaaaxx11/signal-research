/**
 * The brand mark, drawn from `/logos/{SYMBOL}`.
 *
 * Four phases, one box:
 *
 *   loading   a neutral skeleton; the image is requested behind it
 *   loaded    the endpoint's image replaces the skeleton
 *   retrying  the first request failed; the same URL is asked once more with a
 *             cache-busting query, the skeleton still on screen
 *   failed    the letter monogram is drawn **locally** and the `<img>` leaves
 *             the DOM — never a broken-image glyph, never a row that jumps
 *
 * A placeholder is not an answer. Painting the letter while the request was in
 * flight made a slow logo look identical to a missing one, which is half of the
 * bug this component closes; the letter is now reserved for a failure that
 * survived the retry. The retry is the other half: an `<img>` cache can hold a
 * bad answer under the plain URL, so one cache-busted request asks again
 * without making the reader hard-refresh.
 *
 * The box is reserved at `LOGO_SIZE` × `LOGO_SIZE` on the wrapper, so the lane's
 * geometry does not depend on the network: a 404, a 500, a slow route and a
 * cached PNG all occupy the same 20 px.
 *
 * `alt=""` is not an omission: the mark repeats the symbol that is printed
 * beside it, and a screen reader that heard it twice would be reading noise.
 */

import * as React from "react";

import { cn } from "@/lib/utils";

import { LOGO_SIZE, cachedLogo, forgetLogo, logoPath, rememberLogo } from "./logo";

export type LogoState = "loading" | "loaded" | "failed";

export interface LogoMarkProps {
  symbol: string;
  className?: string;
  /**
   * The box, in px. Defaults to the grid lane's 20; Home's signal cards draw a
   * 16 px mark because their rows are 20 px tall. The box is still reserved at
   * the same size in all three states, so the lane does not depend on the
   * network.
   */
  size?: number;
}

export function LogoMark({ symbol, className, size }: LogoMarkProps) {
  const box = size ?? LOGO_SIZE;
  const [state, setState] = React.useState<LogoState>(() =>
    cachedLogo(symbol) === undefined ? "loading" : "loaded",
  );
  // The token doubles as "the first request failed": `null` means the plain URL
  // is in flight, a string means the one cache-busted retry is. Held in state
  // rather than computed in render so a re-render cannot change the URL and
  // restart the request.
  const [retry, setRetry] = React.useState<string | null>(null);

  // A row may be re-used for another symbol (a page turn keeps the DOM nodes);
  // the fallback must come back with the new symbol rather than inherit the old
  // one's answer.
  React.useEffect(() => {
    setState("loading");
    setRetry(null);
  }, [symbol]);

  const src = retry === null ? logoPath(symbol) : logoPath(symbol, retry);

  const handleError = () => {
    forgetLogo(symbol);
    if (retry === null) {
      // Exactly one retry. The endpoint ignores the query; its only job is to
      // name a URL the browser has not already answered from its cache.
      setRetry(Date.now().toString(36));
      return;
    }
    setState("failed");
  };

  return (
    <span
      data-logo={symbol}
      data-logo-state={state}
      style={{ width: box, height: box }}
      className={cn(
        "relative flex shrink-0 items-center justify-center overflow-hidden rounded-[2px] border border-line bg-surface-raised",
        className,
      )}
    >
      {state === "loading" ? (
        <span
          aria-hidden="true"
          data-testid={`logo-skeleton-${symbol}`}
          className="absolute inset-0 animate-pulse bg-line/50 motion-reduce:animate-none"
        />
      ) : null}

      {state === "failed" ? (
        <span
          aria-hidden="true"
          data-testid={`monogram-${symbol}`}
          style={{ fontSize: box <= 16 ? 8 : 9 }}
          className="font-mono leading-none text-fg-muted"
        >
          {symbol.slice(0, 1).toUpperCase()}
        </span>
      ) : null}

      {state === "failed" ? null : (
        <img
          src={src}
          alt=""
          width={box}
          height={box}
          loading="eager"
          decoding="async"
          data-testid={`logo-img-${symbol}`}
          onLoad={() => {
            rememberLogo(symbol, src);
            setState("loaded");
          }}
          onError={handleError}
          className={cn(
            "size-full object-contain",
            // Hidden, not absent: the request has to keep running while the
            // skeleton is what the reader sees.
            state === "loading" && "absolute inset-0 opacity-0",
          )}
        />
      )}
    </span>
  );
}
