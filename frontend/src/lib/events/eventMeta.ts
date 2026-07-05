/**
 * Presentation metadata for derived events.
 *
 * Pure, framework-agnostic mapping from the backend's wire values
 * (`severity`, `event_type`, source-frame `timestamp`) to display strings and
 * Tailwind classes. Kept separate from the components so the same mapping is
 * reused by the badge, the row, and any future timeline without duplication.
 *
 * NOTE on Tailwind: class strings here are LITERAL on purpose. Tailwind purges
 * classes it cannot find as literals in source, so dynamic construction like
 * `bg-status-${x}` would render unstyled. The palette is restrained (surface /
 * accent / status live·idle·warn·error), so the five severities are mapped onto
 * those real tokens with intensity (opacity) distinguishing high vs critical.
 */

export const SEVERITY_LEVELS = [
  "info",
  "low",
  "medium",
  "high",
  "critical",
] as const;

export type SeverityLevel = (typeof SEVERITY_LEVELS)[number];

export interface SeverityStyle {
  level: SeverityLevel;
  /** Short display label. */
  label: string;
  /** Monotonic rank (info=0 … critical=4), mirrors backend `severity_rank`. */
  rank: number;
  /** Background class for a small indicator dot. */
  dotClass: string;
  /** Border+background+text classes for a pill badge. */
  badgeClass: string;
  /** Left-border color class for a feed row (pair with `border-l-2`). */
  rowAccentClass: string;
}

const SEVERITY_STYLES: Record<SeverityLevel, SeverityStyle> = {
  info: {
    level: "info",
    label: "info",
    rank: 0,
    dotClass: "bg-status-idle",
    badgeClass: "border-border bg-surface-200/60 text-content-secondary",
    rowAccentClass: "border-l-border-strong",
  },
  low: {
    level: "low",
    label: "low",
    rank: 1,
    dotClass: "bg-accent",
    badgeClass: "border-accent/30 bg-accent/10 text-accent",
    rowAccentClass: "border-l-accent/50",
  },
  medium: {
    level: "medium",
    label: "medium",
    rank: 2,
    dotClass: "bg-status-warn",
    badgeClass: "border-status-warn/30 bg-status-warn/10 text-status-warn",
    rowAccentClass: "border-l-status-warn/60",
  },
  high: {
    level: "high",
    label: "high",
    rank: 3,
    dotClass: "bg-status-error",
    badgeClass: "border-status-error/30 bg-status-error/10 text-status-error",
    rowAccentClass: "border-l-status-error/60",
  },
  critical: {
    level: "critical",
    label: "critical",
    rank: 4,
    dotClass: "bg-status-error",
    badgeClass: "border-status-error/60 bg-status-error/20 text-status-error",
    rowAccentClass: "border-l-status-error",
  },
};

const FALLBACK_SEVERITY = SEVERITY_STYLES.info;

/** Resolve severity styling; unknown values fall back to `info` (never throws). */
export function severityStyle(severity: string): SeverityStyle {
  return (
    (SEVERITY_STYLES as Record<string, SeverityStyle | undefined>)[severity] ??
    FALLBACK_SEVERITY
  );
}

/** Human labels for the 8 EventType wire values. */
const EVENT_TYPE_LABELS: Record<string, string> = {
  object_appeared: "Object appeared",
  object_disappeared: "Object disappeared",
  object_entered: "Entered zone",
  object_exited: "Exited zone",
  crossing_direction: "Line crossing",
  dwell_time: "Dwell time",
  stationary_object: "Stationary object",
  near_collision: "Near collision",
};

/** Readable label for an event type; falls back to a de-slugged form. */
export function eventTypeLabel(eventType: string): string {
  const known = EVENT_TYPE_LABELS[eventType];
  if (known) return known;
  return eventType.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

/**
 * Format a source-frame timestamp (seconds, relative to the stream's frame
 * clock — NOT wall-clock) for compact display, e.g. `1:23.4` or `12.3s`.
 */
export function formatEventTimestamp(timestampSeconds: number): string {
  if (!Number.isFinite(timestampSeconds) || timestampSeconds < 0) return "—";
  if (timestampSeconds < 60) return `${timestampSeconds.toFixed(1)}s`;
  const minutes = Math.floor(timestampSeconds / 60);
  const seconds = timestampSeconds - minutes * 60;
  return `${minutes}:${seconds.toFixed(1).padStart(4, "0")}`;
}
