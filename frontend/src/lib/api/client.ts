/**
 * Framework-agnostic API client.
 *
 * A thin, typed wrapper over fetch that centralizes base URL resolution,
 * JSON handling, query-string encoding, and error normalization. No React
 * here by design: this layer is independently testable and reusable.
 */

import { env } from "@/config/env";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type QueryValue = string | number | boolean | undefined | null;

interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  /** JSON body; serialized automatically. */
  body?: unknown;
  /** Query parameters; undefined/null entries are dropped. */
  query?: Record<string, QueryValue>;
  signal?: AbortSignal;
}

function buildUrl(path: string, query?: Record<string, QueryValue>): string {
  const base = env.apiBaseUrl.replace(/\/$/, "");
  const url = `${base}${path.startsWith("/") ? path : `/${path}`}`;
  if (!query) return url;

  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null) continue;
    params.append(key, String(value));
  }
  const qs = params.toString();
  return qs ? `${url}?${qs}` : url;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, query, signal } = options;

  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (env.apiKey) headers["X-API-Key"] = env.apiKey;

  const response = await fetch(buildUrl(path, query), {
    method,
    signal,
    headers: Object.keys(headers).length > 0 ? headers : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  if (!response.ok) {
    let detail: unknown;
    try {
      detail = await response.json();
    } catch {
      detail = await response.text().catch(() => undefined);
    }
    const normalizeDetail = (value: unknown): string | undefined => {
      if (value == null) return undefined;
      if (typeof value === "string") return value;
      if (Array.isArray(value)) {
        const parts = value
          .map((item) => {
            if (item && typeof item === "object" && "msg" in item) {
              const msg = String((item as { msg: unknown }).msg);
              const loc = (item as { loc?: unknown }).loc;
              return Array.isArray(loc) ? `${msg} (${loc.join(".")})` : msg;
            }
            return typeof item === "string" ? item : JSON.stringify(item);
          })
          .filter(Boolean);
        return parts.length > 0 ? parts.join("; ") : undefined;
      }
      if (typeof value === "object" && "msg" in value) {
        return String((value as { msg: unknown }).msg);
      }
      return JSON.stringify(value);
    };
    const innerDetail =
      detail && typeof detail === "object" && "detail" in detail
        ? (detail as { detail: unknown }).detail
        : detail;
    const message =
      normalizeDetail(innerDetail) ??
      `Request failed with status ${response.status}`;
    throw new ApiError(response.status, message, detail);
  }

  // 204 / empty body tolerance
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

export const apiClient = {
  get: <T>(path: string, opts?: Omit<RequestOptions, "method" | "body">) =>
    request<T>(path, { ...opts, method: "GET" }),
  post: <T>(path: string, opts?: Omit<RequestOptions, "method">) =>
    request<T>(path, { ...opts, method: "POST" }),
  put: <T>(path: string, opts?: Omit<RequestOptions, "method">) =>
    request<T>(path, { ...opts, method: "PUT" }),
};
