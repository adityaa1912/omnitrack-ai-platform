import type { RegionsResponse } from "@/types/api";
import { REGION_COLORS, toSvgPoints } from "@/lib/regions/coords";

/**
 * Read-only SVG overlay of a stream's configured zones/lines, drawn over the
 * viewer. The viewBox is the source frame size and `preserveAspectRatio="none"`
 * matches the frame renderer, which stretches the frame to fill the canvas — so
 * regions align exactly with the video and the detection boxes at any element
 * size. Non-interactive (pointer-events: none).
 */
export function RegionOverlay({ regions }: { regions: RegionsResponse | undefined }) {
  if (!regions || regions.width <= 0 || regions.height <= 0) return null;
  if (regions.zones.length === 0 && regions.lines.length === 0) return null;

  const labelSize = Math.max(10, Math.round(regions.height / 32));

  return (
    <svg
      className="pointer-events-none absolute inset-0 h-full w-full"
      viewBox={`0 0 ${regions.width} ${regions.height}`}
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      {regions.zones.map((zone) => {
        const [ox, oy] = zone.polygon[0] ?? [0, 0];
        return (
          <g key={`zone-${zone.name}`}>
            <polygon
              points={toSvgPoints(zone.polygon)}
              fill={REGION_COLORS.zoneFill}
              stroke={REGION_COLORS.zoneStroke}
              strokeWidth={1.5}
              vectorEffect="non-scaling-stroke"
            />
            <text
              x={ox + 4}
              y={oy + labelSize}
              fill={REGION_COLORS.zoneStroke}
              fontSize={labelSize}
              fontFamily="JetBrains Mono, ui-monospace, monospace"
            >
              {zone.name}
            </text>
          </g>
        );
      })}

      {regions.lines.map((line) => {
        const mx = (line.start[0] + line.end[0]) / 2;
        const my = (line.start[1] + line.end[1]) / 2;
        return (
          <g key={`line-${line.name}`}>
            <line
              x1={line.start[0]}
              y1={line.start[1]}
              x2={line.end[0]}
              y2={line.end[1]}
              stroke={REGION_COLORS.lineStroke}
              strokeWidth={1.5}
              vectorEffect="non-scaling-stroke"
            />
            <text
              x={mx + 4}
              y={my}
              fill={REGION_COLORS.lineStroke}
              fontSize={labelSize}
              fontFamily="JetBrains Mono, ui-monospace, monospace"
            >
              {line.name}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
