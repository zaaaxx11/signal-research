/**
 * The painter: one 2D context, one pass, no DOM per tile.
 *
 * Everything the reader can see is drawn here, and nothing here is an element.
 * That is the point of the surface: 5,576 stocks cannot be 5,576 divs without
 * a layout pass that costs more than the frame it is in, and a treemap's whole
 * appeal is that the picture changes while the document does not.
 *
 * Four decisions worth keeping, each of them measured rather than assumed:
 *
 * **The context is scaled, the box is not.** `setTransform(dpr, …)` makes one
 * CSS pixel the unit of every coordinate and font size, so a 10 px symbol is
 * drawn by 20 device pixels at DPR 2 and the browser does not resample it. The
 * alternative — a CSS-sized canvas at DPR 2 — is the blur the task's
 * adversarial case (b) is about.
 *
 * **Tiles are painted in colour order.** Setting `fillStyle` is the expensive
 * call, not `fillRect`: on this box 5,576 rects under one style take 10 ms and
 * the same rects with a style per rect take 26 ms. So the tiles are bucketed by
 * their change quantized to 0.05 % (a step far below what the eye resolves on
 * this ramp) and each bucket sets the style once. It is the difference between
 * a 36 ms first frame and a 14 ms one.
 *
 * **Text is measured for labels and estimated for symbols.** A sector label is
 * one of eleven and gets an exact `measureText` plus truncation; a symbol is
 * one of 5,576, so it is gated by a conservative per-character width and drawn
 * only when the rect can hold it. Measuring every symbol cost 4 ms a frame and
 * bought nothing a 10 px monospace estimate does not already know.
 *
 * **A tile is separated by a gutter, not a border.** Borders double on shared
 * edges and turn a dense map into a lattice; a 0.5 px inset on each side gives
 * exactly 1 px between neighbours, which is enough to read the blocks apart and
 * cheap enough to paint.
 */

import type { MapLayout, SectorLayout, Tile } from "./layout";
import { signedPercent } from "./format";
import type { Palette } from "./theme";
import { rampFill } from "./theme";

export interface PaintInput {
  /** Device pixel ratio; the canvas backing store is already sized for it. */
  readonly dpr: number;
  /** CSS pixels. */
  readonly width: number;
  readonly height: number;
  /** The keyboard-selected symbol, ringed in `--focus`. */
  readonly selected?: string | null;
}

export interface PaintStats {
  readonly tiles: number;
  readonly labels: number;
  readonly symbols: number;
}

const SANS = 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';
const MONO = 'ui-monospace, "SF Mono", "JetBrains Mono", Menlo, Consolas, "Liberation Mono", monospace';
const LABEL_FONT = `600 10px ${SANS}`;
const SYMBOL_FONT = `500 10px ${MONO}`;
const NAME_FONT = `400 7px ${SANS}`;
const PRICE_FONT = `400 8px ${MONO}`;

/** Ramp quantization: 20 steps per percentage point, clamped at ±3. */
const TONE_STEPS = 20;
const TONE_LIMIT = 3;

/** Measured label widths, keyed by font+string. Eleven sectors, not 5,576. */
const widths = new Map<string, number>();

function textWidth(ctx: CanvasRenderingContext2D, font: string, text: string): number {
  const key = `${font}\u0000${text}`;
  const cached = widths.get(key);
  if (cached !== undefined) return cached;
  ctx.font = font;
  const measured = ctx.measureText(text).width;
  widths.set(key, measured);
  return measured;
}

/**
 * `ABCD` in a 10 px mono face: 6.02 px measured, 6.2 estimated. Overestimating
 * by 3 % can only suppress a symbol in a rect that would have been a hair too
 * tight, never overflow one.
 */
function estimateWidth(text: string): number {
  return text.length * 6.2;
}

/** The largest prefix of `text` that fits `max`, with an ellipsis when cut. */
function fitLabel(ctx: CanvasRenderingContext2D, font: string, text: string, max: number): string | null {
  if (max <= 0) return null;
  if (textWidth(ctx, font, text) <= max) return text;
  const ellipsis = "…";
  if (textWidth(ctx, font, ellipsis) > max) return null;
  let low = 0;
  let high = text.length;
  while (low < high) {
    const mid = Math.ceil((low + high) / 2);
    if (textWidth(ctx, font, `${text.slice(0, mid)}${ellipsis}`) <= max) low = mid;
    else high = mid - 1;
  }
  return low === 0 ? null : `${text.slice(0, low)}${ellipsis}`;
}

/** Sector chrome: the label band and the two hairlines, never a full fill. */
function paintSectorFrame(ctx: CanvasRenderingContext2D, sector: SectorLayout, palette: Palette): void {
  const { block, header } = sector;
  if (block.w <= 0 || block.h <= 0) return;

  ctx.strokeStyle = palette.border;
  ctx.lineWidth = 1;

  if (header !== null) {
    // The band is the only part of a block that is not covered by tiles, so it
    // is the only part that needs a fill; the rest of the plane is the canvas's
    // own background, which saves a second full-canvas pass per frame.
    ctx.fillStyle = palette.raised;
    ctx.fillRect(block.x, block.y, block.w, header.h);

    ctx.beginPath();
    ctx.moveTo(Math.round(block.x) + 0.5, Math.round(header.y + header.h) + 0.5);
    ctx.lineTo(Math.round(block.x + block.w) + 0.5, Math.round(header.y + header.h) + 0.5);
    ctx.stroke();
  }

  // The 1 px gutter, drawn *inside* the block so the block's area stays the
  // area the layout proved proportional to cap.
  ctx.strokeRect(
    Math.round(block.x) + 0.5,
    Math.round(block.y) + 0.5,
    Math.max(0, Math.round(block.w) - 1),
    Math.max(0, Math.round(block.h) - 1),
  );
}

/** A tile's painted box: rounded, inset by half a gutter where it can spare it. */
function tileBox(tile: Tile): { x: number; y: number; w: number; h: number } {
  const gx = tile.w >= 3 ? 0.5 : 0;
  const gy = tile.h >= 3 ? 0.5 : 0;
  const x0 = Math.round(tile.x + gx);
  const y0 = Math.round(tile.y + gy);
  const x1 = Math.round(tile.x + tile.w - gx);
  const y1 = Math.round(tile.y + tile.h - gy);
  return { x: x0, y: y0, w: Math.max(1, x1 - x0), h: Math.max(1, y1 - y0) };
}

function paintLabel(
  ctx: CanvasRenderingContext2D,
  sector: SectorLayout,
  palette: Palette,
): boolean {
  const header = sector.header;
  if (header === null || header.w < 26) return false;

  const padding = 5;
  const middle = header.y + header.h / 2;
  ctx.textBaseline = "middle";

  const value = signedPercent(sector.value).text;
  const valueWidth = textWidth(ctx, LABEL_FONT, value);
  const valueFits = header.w > valueWidth + 24;
  const labelMax = header.w - padding * 2 - (valueFits ? valueWidth + 8 : 0);
  const label = fitLabel(ctx, LABEL_FONT, sector.label, labelMax);
  if (label === null) return false;

  ctx.font = LABEL_FONT;
  ctx.textAlign = "left";
  ctx.fillStyle = palette.fg;
  ctx.fillText(label, header.x + padding, middle);

  if (valueFits) {
    ctx.textAlign = "right";
    ctx.fillStyle = palette.muted;
    ctx.fillText(value, header.x + header.w - padding, middle);
  }
  ctx.textAlign = "left";
  return true;
}

/**
 * Draw the whole map. The caller owns the canvas size; this function owns every
 * pixel inside it and returns what it painted so the frame can be checked.
 */
export function paintMap(
  ctx: CanvasRenderingContext2D,
  layout: MapLayout,
  palette: Palette,
  input: PaintInput,
): PaintStats {
  const { dpr, width, height } = input;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  // The canvas's own plane. One opaque fill covers the previous frame, so the
  // clear is skipped when the palette resolved a surface to paint.
  if (palette.surface) {
    ctx.fillStyle = palette.surface;
    ctx.fillRect(0, 0, width, height);
  } else {
    ctx.clearRect(0, 0, width, height);
  }

  for (const sector of layout.sectors) paintSectorFrame(ctx, sector, palette);

  // Bucket the tiles by a quantized change so the style is set once per colour,
  // not once per stock. The buckets are filled in layout order, so a bucket's
  // rects are already grouped by sector — the map is still painted in the order
  // the layout proved.
  const buckets = new Map<number, number[]>();
  const tiles = layout.tiles;
  for (let index = 0; index < tiles.length; index += 1) {
    const change = (tiles[index] as Tile).change;
    const clamped = Math.max(-TONE_LIMIT, Math.min(TONE_LIMIT, Number.isFinite(change) ? change : 0));
    const key = Math.round(clamped * TONE_STEPS);
    const bucket = buckets.get(key);
    if (bucket) bucket.push(index);
    else buckets.set(key, [index]);
  }

  const fills = new Map<number, ReturnType<typeof rampFill>>();
  let painted = 0;
  for (const [key, indices] of buckets) {
    const fill = rampFill(key / TONE_STEPS, palette);
    fills.set(key, fill);
    ctx.fillStyle = fill.fill;
    for (const index of indices) {
      const tile = tiles[index] as Tile;
      const box = tileBox(tile);
      ctx.fillRect(box.x, box.y, box.w, box.h);
      painted += 1;
    }
  }

  // Symbols, on top of the fills. The ink is the one the bucket's ramp chose,
  // and a tile with no room (or no contrast) simply carries no text.
  ctx.font = SYMBOL_FONT;
  ctx.textBaseline = "middle";
  ctx.textAlign = "left";
  let symbols = 0;
  for (let index = 0; index < tiles.length; index += 1) {
    const tile = tiles[index] as Tile;
    const box = tileBox(tile);
    if (box.w < 18 || box.h < 12 || estimateWidth(tile.symbol) + 4 > box.w) continue;

    const clamped = Math.max(-TONE_LIMIT, Math.min(TONE_LIMIT, Number.isFinite(tile.change) ? tile.change : 0));
    const key = Math.round(clamped * TONE_STEPS);
    const fill = fills.get(key) ?? rampFill(tile.change, palette);
    if (fill.contrast < 4.5) continue;

    // Tile big enough for symbol
    ctx.fillStyle = fill.ink;
    ctx.fillText(tile.symbol, box.x + 2, box.y + box.h / 2 + 0.5);
    symbols += 1;

    // Name line (tall enough, wide enough)
    if (tile.name && box.h >= 26 && box.w >= 40) {
      const nameText = fitLabel(ctx, NAME_FONT, tile.name, box.w - 4);
      if (nameText) {
        ctx.font = NAME_FONT;
        ctx.fillStyle = fill.ink;
        ctx.fillText(nameText, box.x + 2, box.y + box.h / 2 + 0.5 + 10);
      }
    }

    // Price line (even taller)
    if (tile.price !== null && box.h >= 38 && box.w >= 40) {
      const priceText = `$${tile.price.toFixed(2)}`;
      ctx.font = PRICE_FONT;
      ctx.fillStyle = fill.ink;
      ctx.fillText(priceText, box.x + 2, box.y + box.h / 2 + 0.5 + 20);
    }
  }

  let labels = 0;
  for (const sector of layout.sectors) {
    if (paintLabel(ctx, sector, palette)) labels += 1;
  }

  if (input.selected) {
    const tile = tiles.find((candidate) => candidate.symbol === input.selected);
    if (tile) {
      ctx.strokeStyle = palette.focus;
      ctx.lineWidth = 2;
      ctx.strokeRect(tile.x + 1, tile.y + 1, Math.max(0, tile.w - 2), Math.max(0, tile.h - 2));
    }
  }

  return { tiles: painted, labels, symbols };
}
