import { memo, useMemo } from "react";
import { Area, AreaChart, ResponsiveContainer, YAxis } from "recharts";
import type { TelemetrySample } from "@/store/useTelemetryStore";

type Field = "fps" | "inferenceMs" | "detectionRate";

export interface SparklineProps {
  data: readonly TelemetrySample[];
  field: Field;
  /** Stroke/fill color (CSS). Operationally meaningful or accent. */
  color: string;
  height?: number;
}

/**
 * Compact area sparkline. Memoized; dataset derived via useMemo keyed on the
 * buffer reference, so it only recomputes when a new sample lands. Animation
 * disabled to avoid per-update churn; axis-less for density.
 */
function SparklineImpl({ data, field, color, height = 40 }: SparklineProps) {
  const series = useMemo(() => {
    if (field !== "detectionRate") {
      return data.map((s) => ({ v: s[field] }));
    }
    // Derived rate: delta of cumulative totalDetections between samples.
    const out: { v: number }[] = [];
    for (let i = 0; i < data.length; i += 1) {
      if (i === 0) {
        out.push({ v: 0 });
        continue;
      }
      const dDet = data[i].totalDetections - data[i - 1].totalDetections;
      const dt = Math.max(1, (data[i].t - data[i - 1].t) / 1000);
      out.push({ v: Math.max(0, dDet / dt) });
    }
    return out;
  }, [data, field]);

  const gradientId = useMemo(
    () => `spark-${field}-${Math.random().toString(36).slice(2, 8)}`,
    [field],
  );

  if (series.length < 2) {
    return (
      <div
        style={{ height }}
        className="flex items-center justify-center text-[10px] text-content-muted"
      >
        collecting…
      </div>
    );
  }

  return (
    <div style={{ height }} className="w-full">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={series} margin={{ top: 2, right: 0, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.35} />
              <stop offset="100%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <YAxis hide domain={["dataMin", "dataMax"]} />
          <Area
            type="monotone"
            dataKey="v"
            stroke={color}
            strokeWidth={1.5}
            fill={`url(#${gradientId})`}
            isAnimationActive={false}
            dot={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

export const Sparkline = memo(SparklineImpl);
