import { cn } from "@/lib/utils/cn";

type Status = "live" | "idle" | "warn" | "error";

const STATUS_STYLES: Record<Status, string> = {
  live: "bg-status-live shadow-[0_0_8px_2px_rgba(34,211,154,0.5)]",
  idle: "bg-status-idle",
  warn: "bg-status-warn",
  error: "bg-status-error",
};

/** Small status indicator dot; pulses when live. */
export function StatusDot({
  status,
  className,
}: {
  status: Status;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-block h-2 w-2 rounded-full",
        STATUS_STYLES[status],
        status === "live" && "animate-pulse-live",
        className,
      )}
    />
  );
}
