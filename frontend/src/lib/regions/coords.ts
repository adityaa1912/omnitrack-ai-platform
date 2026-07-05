/**
 * Pure geometry helpers for the scene-region editor and overlay.
 *
 * Coordinates are in source-frame pixel space (top-left origin) — the same
 * space as detections and the backend `Zone`/`CrossingLine`. Kept free of React
 * and the DOM so the mapping is unit-testable in isolation.
 */

export type Point = [number, number];

/** Shared region colours (drawn in SVG; mirror the Tailwind design tokens). */
export const REGION_COLORS = {
  zoneStroke: "#3da9fc",
  zoneFill: "rgba(61,169,252,0.15)",
  lineStroke: "#f5b14c",
  draftStroke: "#5fc1ff",
  vertex: "#5fc1ff",
  label: "#e6edf6",
} as const;

function clamp(value: number, lo: number, hi: number): number {
  return value < lo ? lo : value > hi ? hi : value;
}

/**
 * Map a client (screen) coordinate to source-pixel space.
 *
 * `rect` is the rendered SVG element's bounding box. The result is clamped to
 * the frame bounds and rounded to whole pixels. A zero-sized rect maps to the
 * origin (defensive; avoids division by zero before layout).
 */
export function clientToSource(
  clientX: number,
  clientY: number,
  rect: { left: number; top: number; width: number; height: number },
  width: number,
  height: number,
): Point {
  const nx = rect.width > 0 ? (clientX - rect.left) / rect.width : 0;
  const ny = rect.height > 0 ? (clientY - rect.top) / rect.height : 0;
  return [
    Math.round(clamp(nx, 0, 1) * width),
    Math.round(clamp(ny, 0, 1) * height),
  ];
}

/** Format points as an SVG `points` attribute (`"x,y x,y …"`). */
export function toSvgPoints(points: Point[]): string {
  return points.map(([x, y]) => `${x},${y}`).join(" ");
}
