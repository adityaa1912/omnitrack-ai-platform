import { memo, type ReactNode } from "react";
import { Panel } from "@/components/ui/Panel";
import { cn } from "@/lib/utils/cn";

type Tone = "default" | "accent" | "warn" | "error";

const VALUE_TONE: Record<Tone, string> = {
  default: "text-content-primary",
  accent: "text-accent-glow",
  warn: "text-status-warn",
  error: "text-status-error",
};

export interface MetricCardProps {
  label: string;
  value: string;
  unit?: string;
  tone?: Tone;
  chart?: ReactNode;
}

/** Reusable telemetry metric primitive: label, value(+unit), optional chart. */
function MetricCardImpl({ label, value, unit, tone = "default", chart }: MetricCardProps) {
  return (
    <Panel className="flex flex-col gap-2 p-4">
      <span className="text-[10px] uppercase tracking-wide text-content-muted">
        {label}
      </span>
      <div className="flex items-baseline gap-1">
        <span className={cn("font-mono text-2xl font-semibold tabular-nums", VALUE_TONE[tone])}>
          {value}
        </span>
        {unit ? <span className="text-xs text-content-muted">{unit}</span> : null}
      </div>
      {chart ? <div className="mt-1">{chart}</div> : null}
    </Panel>
  );
}

export const MetricCard = memo(MetricCardImpl);
