//! Media IPC commands (video preview metadata).
//!
//! `media.probe` runs the bundled ffprobe against a project-scoped media file
//! and returns real metadata (duration / resolution / FPS / audio tracks) so
//! the frontend can render the source video info honestly instead of guessing
//! from the `<video>` element alone (which cannot report FPS or audio count).

use std::path::PathBuf;
use std::time::Duration;

use serde::Serialize;
use serde_json::Value;
use tauri::{AppHandle, Manager, Runtime};

use crate::db::Project;
use crate::media::{allowed_media_paths, is_allowed_path};

/// Serialized ffprobe metadata (wire shape, camelCase).
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MediaProbeResult {
    pub duration: f64,
    pub width: u32,
    pub height: u32,
    pub fps: Option<f64>,
    pub audio_tracks: u32,
    pub video_codec: Option<String>,
    pub container: Option<String>,
}

/// `media.probe(path) → MediaProbeResult`
///
/// Only paths in the project media allowlist (a project's source video or a
/// file under a project working directory) are probed — arbitrary local files
/// are refused, mirroring the `media://` protocol's scope.
#[tauri::command(rename = "media.probe")]
pub fn media_probe_command<R: Runtime>(
    app: AppHandle<R>,
    path: String,
) -> Result<MediaProbeResult, String> {
    let trimmed = path.trim();
    if trimmed.is_empty() {
        return Err("media path is empty".into());
    }
    if !is_allowed_path(trimmed, &allowed_media_paths(&app)) {
        return Err(
            "media path is not allowed (register the video as a project source first)".into(),
        );
    }
    let probe = resolve_ffprobe(&app).ok_or_else(|| {
        "ffprobe is not available (check the bundled FFmpeg resources)".to_string()
    })?;
    run_probe(&probe, trimmed)
}

/// Resolve the ffprobe binary: bundled resource → repo `vendor/ffmpeg` (dev)
/// → PATH. Mirrors how the worker resolves FFmpeg in release/dev.
fn resolve_ffprobe<R: Runtime>(app: &AppHandle<R>) -> Option<PathBuf> {
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Ok(res) = app.path().resource_dir() {
        candidates.push(res.join("ffmpeg").join("ffprobe.exe"));
        #[cfg(not(windows))]
        candidates.push(res.join("ffmpeg").join("ffprobe"));
    }
    // Dev builds: the repo's vendored ffmpeg (never committed; downloaded by
    // the worker's vendoring script).
    #[cfg(debug_assertions)]
    {
        let from_manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("vendor")
            .join("ffmpeg");
        candidates.push(from_manifest.join("ffprobe.exe"));
        #[cfg(not(windows))]
        candidates.push(from_manifest.join("ffprobe"));
    }
    for candidate in candidates {
        if candidate.is_file() {
            return Some(candidate);
        }
    }
    Some(PathBuf::from("ffprobe"))
}

/// Run ffprobe with a bounded timeout and parse the JSON document.
fn run_probe(ffprobe: &std::path::Path, path: &str) -> Result<MediaProbeResult, String> {
    let (tx, rx) = std::sync::mpsc::channel();
    let ffprobe = ffprobe.to_path_buf();
    let path = path.to_string();
    std::thread::spawn(move || {
        let output = std::process::Command::new(&ffprobe)
            .arg("-v")
            .arg("error")
            .arg("-print_format")
            .arg("json")
            .arg("-show_format")
            .arg("-show_streams")
            .arg(&path)
            .output();
        let _ = tx.send(output);
    });
    let output = rx
        .recv_timeout(Duration::from_secs(30))
        .map_err(|_| "ffprobe timed out probing the media file".to_string())?
        .map_err(|e| format!("failed to run ffprobe: {e}"))?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
        return Err(if stderr.is_empty() {
            "ffprobe could not read the media file (invalid or unsupported format)".to_string()
        } else {
            format!("ffprobe error: {stderr}")
        });
    }
    parse_probe(&output.stdout)
}

fn parse_probe(stdout: &[u8]) -> Result<MediaProbeResult, String> {
    let doc: Value = serde_json::from_slice(stdout)
        .map_err(|e| format!("ffprobe returned invalid JSON: {e}"))?;
    let streams = doc.get("streams").and_then(Value::as_array);
    let format = doc.get("format");

    let mut width = 0u32;
    let mut height = 0u32;
    let mut fps: Option<f64> = None;
    let mut video_codec: Option<String> = None;
    let mut audio_tracks = 0u32;
    let mut stream_duration: Option<f64> = None;

    if let Some(streams) = streams {
        for stream in streams {
            let codec_type = stream.get("codec_type").and_then(Value::as_str);
            match codec_type {
                Some("video") => {
                    width = stream.get("width").and_then(Value::as_u64).unwrap_or(0) as u32;
                    height = stream.get("height").and_then(Value::as_u64).unwrap_or(0) as u32;
                    video_codec = stream
                        .get("codec_name")
                        .and_then(Value::as_str)
                        .map(str::to_string);
                    fps = parse_fps(stream.get("r_frame_rate").and_then(Value::as_str));
                    if stream_duration.is_none() {
                        stream_duration = stream
                            .get("duration")
                            .and_then(Value::as_str)
                            .and_then(parse_f64);
                    }
                }
                Some("audio") => {
                    audio_tracks += 1;
                }
                _ => {}
            }
        }
    }

    let duration = format
        .and_then(|f| f.get("duration"))
        .and_then(Value::as_str)
        .and_then(parse_f64)
        .or(stream_duration)
        .unwrap_or(0.0);
    let container = format
        .and_then(|f| f.get("format_name"))
        .and_then(Value::as_str)
        .map(str::to_string);

    if width == 0 || height == 0 {
        return Err("no video stream found — the file is not a playable video".to_string());
    }
    Ok(MediaProbeResult {
        duration,
        width,
        height,
        fps,
        audio_tracks,
        video_codec,
        container,
    })
}

/// Parse ffprobe `r_frame_rate` like `25/1` or `30000/1001`; null on 0/0.
fn parse_fps(rate: Option<&str>) -> Option<f64> {
    let rate = rate?.trim();
    if rate.is_empty() {
        return None;
    }
    if let Some((num, den)) = rate.split_once('/') {
        let n: f64 = num.trim().parse().ok()?;
        let d: f64 = den.trim().parse().ok()?;
        if d <= 0.0 {
            return None;
        }
        let value = n / d;
        return (value > 0.0 && value.is_finite()).then_some((value * 1000.0).round() / 1000.0);
    }
    rate.parse::<f64>().ok().filter(|v| *v > 0.0)
}

fn parse_f64(s: &str) -> Option<f64> {
    s.trim().parse::<f64>().ok()
}

/// Convenience used by setup/command wiring: add a project's source video and
/// working directory to the asset-protocol scope (best-effort).
pub fn allow_project_media<R: Runtime>(
    app: &AppHandle<R>,
    project: &Project,
    project_dir: &std::path::Path,
) {
    let scope = app.asset_protocol_scope();
    let _ = scope.allow_file(&project.source_video_path);
    let _ = scope.allow_directory(project_dir, true);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_probe_parses_ffprobe_output() {
        let json = br#"{
            "streams": [
                {"codec_type":"video","codec_name":"h264","width":1280,"height":720,"r_frame_rate":"25/1","duration":"12.000000"},
                {"codec_type":"audio","codec_name":"aac","duration":"12.000000"},
                {"codec_type":"audio","codec_name":"aac","duration":"12.000000"}
            ],
            "format": {"format_name":"mov,mp4,m4a,3gp,3g2,mj2","duration":"12.050000"}
        }"#;
        let result = parse_probe(json).expect("parses");
        assert_eq!(result.duration, 12.05);
        assert_eq!(result.width, 1280);
        assert_eq!(result.height, 720);
        assert_eq!(result.fps, Some(25.0));
        assert_eq!(result.audio_tracks, 2);
        assert_eq!(result.video_codec.as_deref(), Some("h264"));
        assert!(result.container.unwrap().contains("mp4"));
    }

    #[test]
    fn parse_probe_rejects_files_without_video_stream() {
        let json = br#"{"streams":[{"codec_type":"audio"}],"format":{}}"#;
        assert!(parse_probe(json).is_err());
    }

    #[test]
    fn parse_fps_handles_rational_and_variable() {
        assert_eq!(parse_fps(Some("25/1")), Some(25.0));
        assert_eq!(parse_fps(Some("30000/1001")), Some(29.97));
        assert_eq!(parse_fps(Some("0/0")), None);
        assert_eq!(parse_fps(Some("")), None);
        assert_eq!(parse_fps(None), None);
    }
}
