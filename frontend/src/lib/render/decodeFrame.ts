/**
 * Decode a base64-encoded JPEG (as delivered over the stream WebSocket) into
 * an ImageBitmap off the main thread via createImageBitmap.
 *
 * `isDisposed` lets a caller that has torn down cancel the effect of a decode
 * that resolves late: if disposed, the freshly decoded bitmap is closed
 * immediately and null is returned, preventing leaks and stale draws.
 */

function base64ToBytes(base64: string): Uint8Array {
  const binary = atob(base64);
  const len = binary.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

export async function decodeJpegBase64(
  base64: string,
  isDisposed: () => boolean,
): Promise<ImageBitmap | null> {
  if (isDisposed()) return null;
  const bytes = base64ToBytes(base64);
  // `bytes as BlobPart`: newer lib.dom types Uint8Array as generic over
  // ArrayBufferLike, which no longer structurally matches BlobPart. This is a
  // type-only assertion (no runtime effect); the bytes are a valid Blob part.
  const blob = new Blob([bytes as BlobPart], { type: "image/jpeg" });
  const bitmap = await createImageBitmap(blob);
  if (isDisposed()) {
    bitmap.close();
    return null;
  }
  return bitmap;
}
