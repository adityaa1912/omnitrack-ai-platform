/**
 * API contract types.
 *
 * These mirror the backend Pydantic schemas in backend/main.py EXACTLY and
 * are the single source of truth for the client/server contract. If the
 * backend contract changes, update this file first and let TypeScript
 * surface every affected call site.
 */

/** Mirrors HealthResponse. */
export interface HealthResponse {
  status: string;
  timestamp: string; // ISO datetime
  active_streams: number;
  version: string;
}

/** Mirrors MetricsResponse. */
export interface StreamMetrics {
  stream_id: string;
  fps: number;
  total_frames: number;
  total_detections: number;
  inference_time_avg_ms: number;
  is_active: boolean;
  error_message: string | null;
}

/** Mirrors StreamResponse (GET /streams). */
export interface StreamSummary {
  stream_id: string;
  source: string;
  is_running: boolean;
  metrics: StreamMetrics;
}

/** Mirrors DetectionResponse (GET /stream/{id}/detections). */
export interface Detection {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  class_id: number;
  class_name: string;
  confidence: number;
  track_id: number | null;
}

/**
 * Mirrors StartStreamRequest (POST /stream/start).
 * `source` is 0 for webcam, a file path, or an RTSP URL.
 */
export interface StartStreamRequest {
  stream_id: string;
  source: number | string;
  width?: number;
  height?: number;
  fps?: number;
  confidence_threshold?: number;
  tracking_enabled?: boolean;
  track_distance?: number;
  max_age?: number;
  // Optional scene geometry for the event engine's zone/line detectors.
  zones?: ZoneSpec[];
  lines?: LineSpec[];
  dwell_seconds?: number;
}

/**
 * Scene-region geometry. Mirrors the backend ZoneSpec/LineSpec exactly.
 * Coordinates are in source-frame pixels (top-left origin), the same space as
 * detections.
 */
export interface ZoneSpec {
  name: string;
  polygon: [number, number][]; // >= 3 vertices
}

export interface LineSpec {
  name: string;
  start: [number, number];
  end: [number, number];
  positive_label?: string;
  negative_label?: string;
}

/** Mirrors RegionsResponse (GET /stream/{id}/regions). */
export interface RegionsResponse {
  stream_id: string;
  zones: ZoneSpec[];
  lines: LineSpec[];
  dwell_seconds: number;
  width: number;
  height: number;
}

/**
 * WebSocket message contract (WS /stream/{id}/ws).
 * Note: `confidence` is a preformatted string and `frame` is base64 JPEG.
 * Consumed in a later commit; defined here to keep the contract complete.
 */
export interface WsDetection {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  class_id: number;
  class_name: string;
  confidence: string;
  track_id: number | null;
}

export interface WsFrameMessage {
  type: "frame";
  stream_id: string;
  timestamp: number;
  frame: string; // base64-encoded JPEG
  detections: WsDetection[];
}

export interface WsKeepAliveMessage {
  type: "keep_alive";
}

export type WsMessage = WsFrameMessage | WsKeepAliveMessage;
