/** Compact, locale-aware formatters for telemetry display. */

export function formatCount(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return value.toLocaleString();
}

export function formatFps(value: number): string {
  return value.toFixed(1);
}

export function formatMs(value: number): string {
  return `${value.toFixed(0)}ms`;
}
