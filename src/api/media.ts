export const MEDIA_SCHEME = "media";

/**
 * Build the scoped `media://` URL for an absolute local file path. The Rust
 * core answers this scheme only for registered project source videos
 * (`src-tauri/src/media.rs`); raw filesystem paths never reach the DOM.
 */
export function toMediaUrl(path: string): string {
  return `${MEDIA_SCHEME}://localhost/${encodeURIComponent(path)}`;
}
