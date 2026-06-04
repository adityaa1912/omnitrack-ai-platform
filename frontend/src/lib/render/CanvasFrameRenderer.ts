/**
 * Framework-agnostic canvas renderer for a single stream.
 *
 * Ownership model:
 *  - Owns ONE requestAnimationFrame loop.
 *  - Holds a pending/current ImageBitmap pair; never buffers history.
 *  - A monotonic sequence token guarantees only the newest decode wins;
 *    out-of-order/late decodes are closed on arrival.
 *  - Draws only when dirty, so idle streams cost ~nothing and busy streams
 *    are capped at display refresh (decoupled from WS arrival rate).
 *  - DPR-aware sizing via ResizeObserver (no per-frame layout reads).
 *
 * Detections are drawn with a uniform frame->canvas transform so overlays
 * align with the scaled image at any element size.
 */

import type { WsDetection } from "@/types/api";

interface PendingFrame {
  bitmap: ImageBitmap;
  detections: WsDetection[];
  seq: number;
}

const BOX_COLOR = "rgba(61,169,252,0.9)";
const BOX_FILL = "rgba(61,169,252,0.08)";
const LABEL_BG = "rgba(6,8,12,0.82)";
const LABEL_FG = "#e6edf6";
const TRACK_FG = "#5fc1ff";

export class CanvasFrameRenderer {
  private readonly canvas: HTMLCanvasElement;
  private readonly ctx: CanvasRenderingContext2D;
  private readonly resizeObserver: ResizeObserver;

  private rafId: number | null = null;
  private disposed = false;

  private currentBitmap: ImageBitmap | null = null;
  private currentDetections: WsDetection[] = [];
  private pending: PendingFrame | null = null;
  private dirty = false;

  /** CSS pixel size of the canvas box; updated by ResizeObserver only. */
  private cssWidth = 0;
  private cssHeight = 0;
  private dpr = Math.min(window.devicePixelRatio || 1, 2);

  constructor(canvas: HTMLCanvasElement) {
    const ctx = canvas.getContext("2d", { alpha: false });
    if (!ctx) throw new Error("2D canvas context unavailable");
    this.canvas = canvas;
    this.ctx = ctx;

    this.resizeObserver = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      if (!rect) return;
      this.cssWidth = rect.width;
      this.cssHeight = rect.height;
      this.applyBackingSize();
      this.dirty = true; // redraw at new size
    });
    this.resizeObserver.observe(canvas);

    this.loop = this.loop.bind(this);
    this.rafId = requestAnimationFrame(this.loop);
  }

  /**
   * Submit the newest decoded bitmap. Replaces any not-yet-drawn pending
   * bitmap (closing it) so only the latest is ever shown.
   */
  submit(bitmap: ImageBitmap, detections: WsDetection[], seq: number): void {
    if (this.disposed) {
      bitmap.close();
      return;
    }
    if (this.pending && this.pending.seq < seq) {
      this.pending.bitmap.close();
    } else if (this.pending && this.pending.seq >= seq) {
      // A newer frame is already pending; drop this stale one.
      bitmap.close();
      return;
    }
    this.pending = { bitmap, detections, seq };
    this.dirty = true;
  }

  dispose(): void {
    this.disposed = true;
    if (this.rafId !== null) cancelAnimationFrame(this.rafId);
    this.rafId = null;
    this.resizeObserver.disconnect();
    if (this.pending) {
      this.pending.bitmap.close();
      this.pending = null;
    }
    if (this.currentBitmap) {
      this.currentBitmap.close();
      this.currentBitmap = null;
    }
  }

  private applyBackingSize(): void {
    const w = Math.max(1, Math.round(this.cssWidth * this.dpr));
    const h = Math.max(1, Math.round(this.cssHeight * this.dpr));
    if (this.canvas.width !== w) this.canvas.width = w;
    if (this.canvas.height !== h) this.canvas.height = h;
  }

  private loop(): void {
    if (this.disposed) return;
    this.rafId = requestAnimationFrame(this.loop);

    if (!this.dirty) return;
    this.dirty = false;

    // Promote pending -> current, disposing the outgoing current bitmap.
    if (this.pending) {
      if (this.currentBitmap) this.currentBitmap.close();
      this.currentBitmap = this.pending.bitmap;
      this.currentDetections = this.pending.detections;
      this.pending = null;
    }

    this.draw();
  }

  private draw(): void {
    const { ctx, canvas } = this;
    const cw = canvas.width;
    const ch = canvas.height;
    if (cw === 0 || ch === 0) return;

    ctx.fillStyle = "#06080c";
    ctx.fillRect(0, 0, cw, ch);

    const bmp = this.currentBitmap;
    if (!bmp) return;

    // Scale frame to fill the backing store; uniform per-axis factors are
    // reused to place overlays.
    const sx = cw / bmp.width;
    const sy = ch / bmp.height;
    ctx.drawImage(bmp, 0, 0, cw, ch);

    this.drawDetections(this.currentDetections, sx, sy);
  }

  private drawDetections(detections: WsDetection[], sx: number, sy: number): void {
    const { ctx } = this;
    const fontPx = Math.max(11, Math.round(11 * this.dpr));
    ctx.lineWidth = Math.max(1, Math.round(1.5 * this.dpr));
    ctx.font = `${fontPx}px "JetBrains Mono", ui-monospace, monospace`;
    ctx.textBaseline = "top";

    for (const det of detections) {
      const x = det.x1 * sx;
      const y = det.y1 * sy;
      const w = (det.x2 - det.x1) * sx;
      const h = (det.y2 - det.y1) * sy;

      ctx.fillStyle = BOX_FILL;
      ctx.fillRect(x, y, w, h);
      ctx.strokeStyle = BOX_COLOR;
      ctx.strokeRect(x, y, w, h);

      // Label: "class conf" with optional "#track".
      const trackPart =
        det.track_id !== null && det.track_id !== undefined
          ? `#${det.track_id} `
          : "";
      const label = `${trackPart}${det.class_name} ${det.confidence}`;
      const padX = 5 * this.dpr;
      const padY = 3 * this.dpr;
      const textW = ctx.measureText(label).width;
      const labelH = fontPx + padY * 2;
      const labelY = y - labelH >= 0 ? y - labelH : y;

      ctx.fillStyle = LABEL_BG;
      ctx.fillRect(x, labelY, textW + padX * 2, labelH);

      // Track id tinted, rest neutral: draw in two segments.
      let cursor = x + padX;
      if (trackPart) {
        ctx.fillStyle = TRACK_FG;
        ctx.fillText(trackPart, cursor, labelY + padY);
        cursor += ctx.measureText(trackPart).width;
      }
      ctx.fillStyle = LABEL_FG;
      ctx.fillText(`${det.class_name} ${det.confidence}`, cursor, labelY + padY);
    }
  }
}
