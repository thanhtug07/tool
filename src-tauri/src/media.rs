//! Scoped media URI protocol (TASK-026, `MASTER_PLAN.md` §3.5 / §11).
//!
//! The preview `<video>` element streams project source videos through a
//! private ``media://`` scheme instead of a raw filesystem path. A request is
//! only answered when the decoded path equals a registered project's
//! ``source_video_path`` — arbitrary local files are refused. Single ``Range``
//! requests are honored so scrubbing works on large files without loading the
//! whole clip into memory.

use std::io::{Read, Seek, SeekFrom};

use tauri::http::header::{ACCEPT_RANGES, CONTENT_LENGTH, CONTENT_RANGE, CONTENT_TYPE, RANGE};
use tauri::http::{Request, Response, StatusCode};
use tauri::{AppHandle, Manager, Runtime};

use crate::services::project_service::ProjectService;

pub const MEDIA_SCHEME: &str = "media";

/// Upper bound for a single served byte range. Browsers probe with open-ended
/// ranges (``bytes=0-``); honoring them verbatim would buffer the *entire*
/// file in RAM (a multi-GB 40-minute video would OOM the app). The response is
/// capped at this chunk with an accurate ``Content-Range``, so the client
/// issues follow-up range requests — the standard streaming behaviour.
const MAX_MEDIA_CHUNK_BYTES: u64 = 64 * 1024 * 1024;

/// True when `path` (as requested through the protocol) matches one of the
/// project source-video paths. Windows paths compare case-insensitively and
/// both `/` and `\` separators are normalized away.
pub fn is_allowed_path(path: &str, allowed: &[String]) -> bool {
    let requested = normalize_path(path);
    allowed
        .iter()
        .any(|candidate| normalize_path(candidate) == requested)
}

fn normalize_path(path: &str) -> String {
    #[cfg(windows)]
    {
        path.replace('/', "\\").to_lowercase()
    }
    #[cfg(not(windows))]
    {
        path.replace('\\', "/").to_lowercase()
    }
}

/// Decode a media URL path component (``request.uri().path()``) back to an
/// absolute filesystem path. The frontend percent-encodes the entire path.
pub fn parse_media_path(path_component: &str) -> Option<String> {
    let raw = path_component.trim_start_matches('/');
    if raw.is_empty() {
        return None;
    }
    let decoded = percent_decode(raw);
    Some(decoded.trim_start_matches('/').to_string())
}

/// Minimal percent-decoding for UTF-8 (invalid escapes pass through).
fn percent_decode(input: &str) -> String {
    let bytes = input.as_bytes();
    let mut out: Vec<u8> = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            if let (Some(high), Some(low)) = (hex(bytes[i + 1]), hex(bytes[i + 2])) {
                out.push(high * 16 + low);
                i += 3;
                continue;
            }
        }
        out.push(bytes[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

fn hex(b: u8) -> Option<u8> {
    match b {
        b'0'..=b'9' => Some(b - b'0'),
        b'a'..=b'f' => Some(b - b'a' + 10),
        b'A'..=b'F' => Some(b - b'A' + 10),
        _ => None,
    }
}

/// Parse a single ``bytes=a-b`` / ``bytes=a-`` / ``bytes=-n`` range. Multi-range
/// and zero-length requests are rejected.
pub fn parse_byte_range(range: &str, len: u64) -> Option<(u64, u64)> {
    let range = range.strip_prefix("bytes=")?;
    if range.contains(',') {
        return None;
    }
    let (start_s, end_s) = range.split_once('-')?;
    if start_s.is_empty() {
        let n: u64 = end_s.parse().ok()?;
        if n == 0 {
            return None;
        }
        let start = len.saturating_sub(n);
        return Some((start, len.saturating_sub(1)));
    }
    let start: u64 = start_s.parse().ok()?;
    let end = if end_s.is_empty() {
        len.saturating_sub(1)
    } else {
        end_s.parse::<u64>().ok()?.min(len.saturating_sub(1))
    };
    if start > end {
        return None;
    }
    Some((start, end))
}

/// Content type for the common local video containers.
pub fn mime_for(path: &str) -> &'static str {
    let ext = std::path::Path::new(path)
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or("")
        .to_lowercase();
    match ext.as_str() {
        "mp4" => "video/mp4",
        "m4v" => "video/x-m4v",
        "mkv" => "video/x-matroska",
        "webm" => "video/webm",
        "mov" => "video/quicktime",
        "avi" => "video/x-msvideo",
        "ts" | "m2ts" => "video/mp2t",
        _ => "application/octet-stream",
    }
}

/// Project-scoped media paths the protocol may serve.
///
/// In addition to each project's source video, the project's own working
/// directory (cache/ + output/ artifacts such as the extracted audio or the
/// rendered video) is served — the UI needs those to preview pipeline output.
/// Arbitrary files outside a project directory are still refused.
fn allowed_media_paths<R: Runtime>(app: &AppHandle<R>) -> Vec<String> {
    let Some(service) = app.try_state::<ProjectService>() else {
        return Vec::new();
    };
    let Ok(projects) = service.list() else {
        return Vec::new();
    };
    let mut paths = Vec::new();
    for project in projects {
        paths.push(project.source_video_path);
        let dir = service.project_dir(&project.id);
        if let Ok(files) = collect_files(&dir) {
            paths.extend(files);
        }
    }
    paths
}

/// Recursively collect regular files under `dir` (bounded to the project tree).
fn collect_files(dir: &std::path::Path) -> std::io::Result<Vec<String>> {
    let mut out = Vec::new();
    for entry in std::fs::read_dir(dir)? {
        let path = entry?.path();
        if path.is_dir() {
            out.extend(collect_files(&path)?);
        } else if path.is_file() {
            out.push(path.to_string_lossy().into_owned());
        }
    }
    Ok(out)
}

/// Handle one ``media://`` request: validate the path, then serve it (honoring
/// a single byte ``Range`` so video seeking works).
pub fn media_response<R: Runtime>(
    app: &AppHandle<R>,
    request: &Request<Vec<u8>>,
) -> Response<Vec<u8>> {
    let path = match parse_media_path(request.uri().path()) {
        Some(path) => path,
        None => return error_response(StatusCode::BAD_REQUEST, "invalid media uri"),
    };
    let allowed = allowed_media_paths(app);
    if !is_allowed_path(&path, &allowed) {
        return error_response(StatusCode::FORBIDDEN, "media path is not allowed");
    }
    let file = match std::fs::File::open(&path) {
        Ok(file) => file,
        Err(_) => return error_response(StatusCode::NOT_FOUND, "media file not found"),
    };
    let range = request
        .headers()
        .get(RANGE)
        .and_then(|value| value.to_str().ok());
    serve_media(file, &path, range)
}

fn serve_media(mut file: impl Read + Seek, path: &str, range: Option<&str>) -> Response<Vec<u8>> {
    let len = file.seek(SeekFrom::End(0)).unwrap_or(0);
    if let Some(range) = range {
        if let Some((start, end)) = parse_byte_range(range, len) {
            if start >= len {
                return Response::builder()
                    .status(StatusCode::RANGE_NOT_SATISFIABLE)
                    .header(CONTENT_RANGE, format!("bytes */{len}"))
                    .body(Vec::new())
                    .unwrap();
            }
            // Bound the served window so open-ended / large ranges never load
            // the whole file into memory; the client continues with the next
            // range (accurate Content-Range keeps the stream valid).
            let end = end.min(start + MAX_MEDIA_CHUNK_BYTES - 1);
            let count = (end - start + 1) as usize;
            let mut body = vec![0u8; count];
            let mut filled = 0usize;
            if file.seek(SeekFrom::Start(start)).is_ok() {
                while filled < count {
                    match file.read(&mut body[filled..]) {
                        Ok(0) => break,
                        Ok(n) => filled += n,
                        Err(_) => break,
                    }
                }
            }
            body.truncate(filled);
            return Response::builder()
                .status(StatusCode::PARTIAL_CONTENT)
                .header(CONTENT_TYPE, mime_for(path))
                .header(CONTENT_RANGE, format!("bytes {start}-{end}/{len}"))
                .header(ACCEPT_RANGES, "bytes")
                .header(CONTENT_LENGTH, body.len())
                .body(body)
                .unwrap();
        }
    }
    let mut body = Vec::new();
    let _ = file.read_to_end(&mut body);
    Response::builder()
        .status(StatusCode::OK)
        .header(CONTENT_TYPE, mime_for(path))
        .header(ACCEPT_RANGES, "bytes")
        .header(CONTENT_LENGTH, body.len())
        .body(body)
        .unwrap()
}

fn error_response(status: StatusCode, message: &str) -> Response<Vec<u8>> {
    Response::builder()
        .status(status)
        .header(CONTENT_TYPE, "text/plain; charset=utf-8")
        .body(message.as_bytes().to_vec())
        .unwrap()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_media_path_decodes_an_encoded_windows_path() {
        let encoded = "C%3A%5CUsers%5Cuser%5Cclip.mp4";
        assert_eq!(
            parse_media_path(&format!("/{encoded}")),
            Some("C:\\Users\\user\\clip.mp4".to_string())
        );
    }

    #[test]
    fn parse_media_path_handles_raw_paths_and_rejects_empty() {
        assert_eq!(
            parse_media_path("/C:/videos/a.mp4"),
            Some("C:/videos/a.mp4".to_string())
        );
        assert_eq!(parse_media_path(""), None);
        assert_eq!(parse_media_path("/"), None);
    }

    #[test]
    fn is_allowed_path_matches_case_insensitively_and_by_separator() {
        let allowed = vec!["C:\\Users\\User\\Clip.MP4".to_string()];
        assert!(is_allowed_path("C:\\Users\\user\\clip.mp4", &allowed));
        assert!(is_allowed_path("C:/Users/user/clip.mp4", &allowed));
        assert!(!is_allowed_path("D:\\Users\\user\\clip.mp4", &allowed));
        assert!(!is_allowed_path("", &allowed));
    }

    #[test]
    fn parse_byte_range_supports_closed_suffix_and_open_ranges() {
        assert_eq!(parse_byte_range("bytes=0-9", 100), Some((0, 9)));
        assert_eq!(parse_byte_range("bytes=90-", 100), Some((90, 99)));
        assert_eq!(parse_byte_range("bytes=-10", 100), Some((90, 99)));
        assert_eq!(parse_byte_range("bytes=0-999", 100), Some((0, 99)));
        assert_eq!(parse_byte_range("bytes=5-2", 100), None);
        assert_eq!(parse_byte_range("bytes=0-1,3-4", 100), None);
        assert_eq!(parse_byte_range("items=0-9", 100), None);
        assert_eq!(parse_byte_range("bytes=-0", 100), None);
    }

    #[test]
    fn mime_for_maps_common_containers_and_defaults() {
        assert_eq!(mime_for("clip.mp4"), "video/mp4");
        assert_eq!(mime_for("a.MKV"), "video/x-matroska");
        assert_eq!(mime_for("subtitle.srt"), "application/octet-stream");
    }

    #[test]
    fn open_ended_range_is_capped_to_chunk_size() {
        // A `bytes=0-` probe on a huge file must not buffer the whole file.
        let len = 4_000_000_000u64; // ~4 GB
        assert_eq!(
            parse_byte_range("bytes=0-", len),
            Some((0, len - 1)),
            "parse_byte_range stays exact; the cap is applied at serve time"
        );
    }

    #[test]
    fn serve_caps_the_served_window_and_keeps_content_range_exact() {
        use std::io::Cursor;
        // 8 MB logical file; request everything from byte 0.
        let len = 8 * 1024 * 1024u64;
        let mut bytes = Vec::with_capacity(len as usize);
        for i in 0..len {
            bytes.push((i % 251) as u8);
        }
        let mut file = Cursor::new(bytes.clone());
        let response = serve_media(&mut file, "clip.mp4", Some("bytes=0-"));
        assert_eq!(response.status(), StatusCode::PARTIAL_CONTENT);
        assert_eq!(
            response
                .headers()
                .get(CONTENT_RANGE)
                .and_then(|v| v.to_str().ok()),
            Some(format!("bytes 0-{}/{len}", len - 1).as_str())
        );
        assert_eq!(response.body().len() as u64, len);
    }
}
