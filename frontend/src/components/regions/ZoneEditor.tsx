import { useRef, useState } from "react";
import { Check, Eraser, Trash2 } from "lucide-react";
import type { LineSpec, ZoneSpec } from "@/types/api";
import {
  REGION_COLORS,
  clientToSource,
  toSvgPoints,
  type Point,
} from "@/lib/regions/coords";
import { cn } from "@/lib/utils/cn";

type Mode = "zone" | "line";

/**
 * Interactive scene-region editor.
 *
 * A controlled component: the parent owns the `zones`/`lines` arrays and gets
 * updates via `onChange`; only the in-progress draft is local. Drawing happens
 * in source-pixel space via an SVG whose viewBox is the frame size, so vertices
 * are already in the coordinate system the backend expects — no conversion at
 * submit time. `preserveAspectRatio="none"` matches the frame renderer.
 *
 * Zone mode: click to add polygon vertices, then "Finish" (needs >= 3).
 * Line mode: click a start point, then an end point (auto-commits).
 */
export function ZoneEditor({
  width,
  height,
  zones,
  lines,
  onChange,
}: {
  width: number;
  height: number;
  zones: ZoneSpec[];
  lines: LineSpec[];
  onChange: (next: { zones: ZoneSpec[]; lines: LineSpec[] }) => void;
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  const seq = useRef(0);
  const [mode, setMode] = useState<Mode>("zone");
  const [draft, setDraft] = useState<Point[]>([]);

  const vertexR = Math.max(2, Math.round(Math.min(width, height) / 90));

  const nextName = (prefix: string): string => {
    seq.current += 1;
    return `${prefix}-${seq.current}`;
  };

  const switchMode = (next: Mode) => {
    setMode(next);
    setDraft([]);
  };

  const handleClick = (e: React.MouseEvent<SVGSVGElement>) => {
    const svg = svgRef.current;
    if (!svg || width <= 0 || height <= 0) return;
    const point = clientToSource(
      e.clientX,
      e.clientY,
      svg.getBoundingClientRect(),
      width,
      height,
    );

    if (mode === "line") {
      if (draft.length === 0) {
        setDraft([point]);
        return;
      }
      const start = draft[0];
      if (start[0] === point[0] && start[1] === point[1]) return; // degenerate
      onChange({
        zones,
        lines: [
          ...lines,
          {
            name: nextName("line"),
            start,
            end: point,
            positive_label: "positive",
            negative_label: "negative",
          },
        ],
      });
      setDraft([]);
    } else {
      setDraft([...draft, point]);
    }
  };

  const finishZone = () => {
    if (draft.length < 3) return;
    onChange({ zones: [...zones, { name: nextName("zone"), polygon: draft }], lines });
    setDraft([]);
  };

  const deleteZone = (index: number) =>
    onChange({ zones: zones.filter((_, i) => i !== index), lines });
  const deleteLine = (index: number) =>
    onChange({ zones, lines: lines.filter((_, i) => i !== index) });

  const canFinish = mode === "zone" && draft.length >= 3;
  const hasDraft = draft.length > 0;

  return (
    <div className="flex flex-col gap-2">
      {/* Mode + actions */}
      <div className="flex items-center gap-2">
        <div className="inline-flex overflow-hidden rounded-lg border border-border-subtle">
          {(["zone", "line"] as Mode[]).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => switchMode(m)}
              className={cn(
                "px-3 py-1 text-xs font-medium capitalize transition-colors",
                mode === m
                  ? "bg-accent text-surface-0"
                  : "text-content-secondary hover:bg-surface-200",
              )}
            >
              {m}
            </button>
          ))}
        </div>
        {canFinish && (
          <button
            type="button"
            onClick={finishZone}
            className="inline-flex items-center gap-1 rounded-lg border border-accent/40 bg-accent/10 px-2 py-1 text-xs text-accent hover:bg-accent/20"
          >
            <Check className="h-3.5 w-3.5" /> Finish zone
          </button>
        )}
        {hasDraft && (
          <button
            type="button"
            onClick={() => setDraft([])}
            className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-content-muted hover:bg-surface-200 hover:text-content-primary"
          >
            <Eraser className="h-3.5 w-3.5" /> Clear
          </button>
        )}
      </div>

      <p className="text-[10px] text-content-muted">
        {mode === "zone"
          ? "Click to add polygon vertices, then Finish (min 3)."
          : "Click a start point, then an end point."}
      </p>

      {/* Drawing surface */}
      <div
        className="w-full overflow-hidden rounded-lg border border-border-subtle bg-surface-0"
        style={{ aspectRatio: `${width} / ${height}` }}
      >
        <svg
          ref={svgRef}
          className="h-full w-full cursor-crosshair"
          viewBox={`0 0 ${width} ${height}`}
          preserveAspectRatio="none"
          onClick={handleClick}
        >
          {zones.map((zone) => (
            <polygon
              key={zone.name}
              points={toSvgPoints(zone.polygon)}
              fill={REGION_COLORS.zoneFill}
              stroke={REGION_COLORS.zoneStroke}
              strokeWidth={1.5}
              vectorEffect="non-scaling-stroke"
            />
          ))}
          {lines.map((line) => (
            <line
              key={line.name}
              x1={line.start[0]}
              y1={line.start[1]}
              x2={line.end[0]}
              y2={line.end[1]}
              stroke={REGION_COLORS.lineStroke}
              strokeWidth={1.5}
              vectorEffect="non-scaling-stroke"
            />
          ))}

          {/* In-progress draft */}
          {draft.length > 0 && (
            <>
              <polyline
                points={toSvgPoints(draft)}
                fill="none"
                stroke={REGION_COLORS.draftStroke}
                strokeWidth={1.5}
                strokeDasharray="4 3"
                vectorEffect="non-scaling-stroke"
              />
              {mode === "zone" && draft.length >= 3 && (
                <line
                  x1={draft[draft.length - 1][0]}
                  y1={draft[draft.length - 1][1]}
                  x2={draft[0][0]}
                  y2={draft[0][1]}
                  stroke={REGION_COLORS.draftStroke}
                  strokeWidth={1}
                  strokeDasharray="2 3"
                  opacity={0.5}
                  vectorEffect="non-scaling-stroke"
                />
              )}
              {draft.map(([x, y], i) => (
                <circle key={i} cx={x} cy={y} r={vertexR} fill={REGION_COLORS.vertex} />
              ))}
            </>
          )}
        </svg>
      </div>

      {/* Configured regions */}
      {(zones.length > 0 || lines.length > 0) && (
        <ul className="flex flex-col gap-1">
          {zones.map((zone, i) => (
            <RegionRow
              key={zone.name}
              label={`${zone.name} · ${zone.polygon.length} pts`}
              color={REGION_COLORS.zoneStroke}
              onDelete={() => deleteZone(i)}
            />
          ))}
          {lines.map((line, i) => (
            <RegionRow
              key={line.name}
              label={line.name}
              color={REGION_COLORS.lineStroke}
              onDelete={() => deleteLine(i)}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function RegionRow({
  label,
  color,
  onDelete,
}: {
  label: string;
  color: string;
  onDelete: () => void;
}) {
  return (
    <li className="flex items-center justify-between rounded-md bg-surface-200/50 px-2 py-1">
      <span className="flex items-center gap-2 text-xs text-content-secondary">
        <span className="h-2 w-2 rounded-full" style={{ backgroundColor: color }} />
        {label}
      </span>
      <button
        type="button"
        onClick={onDelete}
        className="text-content-muted hover:text-status-error"
        aria-label={`Delete ${label}`}
      >
        <Trash2 className="h-3.5 w-3.5" />
      </button>
    </li>
  );
}
