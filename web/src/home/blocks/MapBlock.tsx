/**
 * The 1-day performance map, at dashboard size.
 *
 * `MiniMap` is the map engine with its chrome removed — the same `layoutMap`,
 * `paintMap` and token ramp the Maps page uses, so there is one layout engine in
 * this product and not two that slowly disagree. This file adds only the card
 * around it: a title, a reserved box, and the sentence that says how much of the
 * market the picture shows.
 *
 * The trim is stated, not hidden. Home receives the 600 largest tiles of a
 * 5,573-tile market, and the card prints `600 of 5,573 tiles` under the canvas.
 * A reader who takes the map for the whole market would be wrong, and the
 * caption is the correction.
 *
 * An empty sector list is the case that must not draw an empty canvas: a blank
 * rectangle reads as "the market was flat" when the truth is "no sector carried
 * a value". The card says that in words instead.
 */

import { formatNumber } from "@/grid/format";
import { MiniMap } from "@/map/MiniMap";
import { navigate } from "@/shell/routes";

import type { BlockState, HomeMap } from "../home";
import { BLOCK_HEIGHT, BlockMessage, Card, SkeletonBar } from "./HomeBlock";

/** `600 of 5,573 tiles`, or the honest version when one number is missing. */
export function tilesLabel(map: HomeMap): string {
  if (map.tiles === null) return "Tile count unavailable";
  if (map.tilesTotal === null) return `${formatNumber(map.tiles, 0)} tiles`;
  return `${formatNumber(map.tiles, 0)} of ${formatNumber(map.tilesTotal, 0)} tiles`;
}

export interface MapBlockProps {
  readonly state: BlockState;
  readonly map: HomeMap | null;
  readonly className?: string;
}

function MapSkeleton() {
  return (
    <div aria-hidden="true" data-testid="map-skeleton" className="flex flex-col gap-1 p-2">
      <div className="flex gap-1">
        <span className="h-20 flex-[3] rounded-[1px] bg-surface-raised" />
        <span className="h-20 flex-[2] rounded-[1px] bg-surface-raised" />
      </div>
      <div className="flex gap-1">
        <span className="h-16 flex-[2] rounded-[1px] bg-surface-raised" />
        <span className="h-16 flex-[3] rounded-[1px] bg-surface-raised" />
      </div>
      <SkeletonBar width="38%" height={7} />
    </div>
  );
}

export function MapBlock({ state, map, className }: MapBlockProps) {
  return (
    <Card id="map" title="1-day performance" className={className} height={BLOCK_HEIGHT.map}>
      {state.kind === "loading" ? (
        <MapSkeleton />
      ) : state.kind !== "ready" ? (
        <BlockMessage id="map" reason={state.reason ?? "Unavailable."} failed={state.kind === "error"} />
      ) : map === null ? (
        <BlockMessage id="map" reason="Map unavailable." />
      ) : map.sectors.length === 0 ? (
        <div className="flex min-h-0 flex-1 flex-col">
          <p data-testid="map-empty" className="px-2 py-2 text-[11px] leading-snug text-fg-subtle">
            No sector carried a value for this session, so there is nothing to draw.
          </p>
        </div>
      ) : (
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          <div className="min-h-0 min-w-0 flex-1">
            <MiniMap
              // The canvas owns the card's whole body: 30 px is the title row.
              height={BLOCK_HEIGHT.map - 30}
              sectors={map.sectors}
              onOpenSymbol={(symbol) => navigate(`/stock/${encodeURIComponent(symbol)}`)}
              ariaLabel="1-day performance by sector"
            />
          </div>
        </div>
      )}
    </Card>
  );
}
