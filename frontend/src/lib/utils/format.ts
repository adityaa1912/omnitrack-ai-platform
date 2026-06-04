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

/** Human uptime from a start timestamp (ms) to now. */
export function formatUptime(startedAtMs: number, nowMs = Date.now()): string {
  const sec = Math.max(0, Math.floor((nowMs - startedAtMs) / 1000));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}
