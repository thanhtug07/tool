//! Minimal, dependency-free HTTP client for the localhost worker API (TASK-006).
//!
//! The Python worker is a loopback-only sidecar; the Rust core only needs a
//! small number of authenticated `GET` calls (the `/health` readiness probe) on
//! plain `127.0.0.1` HTTP. `reqwest`/`ureq` would drag in a TLS stack we do not
//! need, so this module speaks HTTP/1.1 directly over `std::net::TcpStream`.
//!
//! Security contract: responses are size-capped, timeouts are enforced, and the
//! bearer token is only ever sent in the `Authorization` header — never logged.

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::time::Duration;

use serde::{Deserialize, Serialize};

/// Loopback address the worker must bind (`127.0.0.1`, never the LAN).
pub const HOST_LOOPBACK: [u8; 4] = [127, 0, 0, 1];

const CONNECT_TIMEOUT: Duration = Duration::from_secs(2);
const READ_TIMEOUT: Duration = Duration::from_secs(3);
const WRITE_TIMEOUT: Duration = Duration::from_secs(2);
/// Upper bound for response headers (plenty for a FastAPI health response).
const MAX_HEADER_BYTES: usize = 16 * 1024;
/// Upper bound for a control-plane response body (16 MiB — transcripts of
/// long videos plus their translations can exceed 1 MiB).
const MAX_BODY_BYTES: usize = 16 * 1024 * 1024;
/// I/O timeout for pipeline stage calls (extract/transcribe/translate/
/// subtitle/render) and export calls. These run for minutes on real media; the
/// short ``READ_TIMEOUT`` is only for the health probe and progress polling.
/// 2 hours comfortably bounds a 40-minute video even at RTF ~2.5 on a slow
/// CPU; a genuinely hung worker is caught by the supervisor instead.
const PIPELINE_IO_TIMEOUT: Duration = Duration::from_secs(2 * 60 * 60);
/// I/O timeout for the live-progress poll: the worker answers instantly, so a
/// stalled poll must fail fast and be treated as "no progress available".
const PROGRESS_READ_TIMEOUT: Duration = Duration::from_secs(1);

/// Response of the worker's `GET /health` endpoint (see worker schemas).
#[derive(Debug, Clone, Deserialize, PartialEq)]
pub struct HealthResponse {
    pub status: String,
    pub version: String,
    #[serde(default)]
    pub gpu: Option<serde_json::Value>,
}

/// Error envelope returned by worker HTTP endpoints (canonical `api.schema.json`).
///
/// Mirrors `{"error": {"code": ..., "message": ..., "recoverable": ...}}` from
/// MASTER_PLAN.md §25.3. Never contains stack traces, tokens, or filesystem paths.
#[derive(Debug, Clone, Deserialize, PartialEq)]
pub struct ErrorEnvelope {
    pub code: String,
    pub message: String,
    pub recoverable: bool,
}

/// Full worker error body: `{"error": { ... }}`.
#[derive(Debug, Clone, Deserialize, PartialEq)]
pub struct ErrorResponse {
    pub error: ErrorEnvelope,
}

/// Errors from talking to the worker over HTTP.
#[derive(Debug, Clone, PartialEq)]
pub enum HttpError {
    /// TCP connect failed (expected while the worker is still starting).
    ConnectFailed(String),
    WriteFailed(String),
    ReadFailed(String),
    /// Peer closed the connection before the response completed.
    ConnectionClosed,
    MalformedResponse(String),
    /// A non-2xx status code was received.
    Status(u16),
    /// The worker answered with a canonical error envelope (MASTER_PLAN §25.3).
    Worker(ErrorEnvelope),
}

impl std::fmt::Display for HttpError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            HttpError::ConnectFailed(e) => write!(f, "connection failed: {e}"),
            HttpError::WriteFailed(e) => write!(f, "write failed: {e}"),
            HttpError::ReadFailed(e) => write!(f, "read failed: {e}"),
            HttpError::ConnectionClosed => write!(f, "connection closed before response completed"),
            HttpError::MalformedResponse(e) => write!(f, "malformed response: {e}"),
            HttpError::Status(code) => write!(f, "HTTP status {code}"),
            HttpError::Worker(e) => write!(f, "{}: {}", e.code, e.message),
        }
    }
}

impl std::error::Error for HttpError {}

/// Authenticated client for a specific worker session (loopback port + token).
///
/// The token is owned by the Rust core and never serialized or logged; it is
/// used only in the `Authorization: Bearer` header.
#[derive(Debug, Clone)]
pub struct WorkerClient {
    port: u16,
    token: String,
}

/// Request body for `POST /v1/export/video` (TASK-029).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ExportVideoRequest {
    pub source_video: String,
    pub target_dir: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub run_qc: Option<bool>,
}

/// QC verdict returned with an exported video (TASK-029).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ExportQcReport {
    pub passed: bool,
    #[serde(default)]
    pub issues: Vec<String>,
    #[serde(default)]
    pub warnings: Vec<String>,
}

/// Response of `POST /v1/export/video`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ExportVideoResponse {
    pub path: String,
    pub qc: ExportQcReport,
}

/// Request body for `POST /v1/export/subtitles` (TASK-029).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ExportSubtitleRequest {
    pub source_subtitle: String,
    pub target_dir: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    /// `srt` / `vtt` / `ass`; `None` keeps the source extension.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub format: Option<String>,
}

/// Response of `POST /v1/export/subtitles`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ExportSubtitleResponse {
    pub path: String,
}

/// Request body for `POST /v1/providers/test` (Provider Management).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ProviderTestRequest {
    pub provider_kind: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub provider_config: Option<serde_json::Map<String, serde_json::Value>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub api_key: Option<String>,
}

/// Result of `POST /v1/providers/test`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ProviderTestResult {
    pub ok: bool,
    pub latency_ms: u64,
    pub detail: String,
}

// ---------------------------------------------------------------------------
// Pipeline stage contracts (RELEASE-P0). Mirror worker/src/api/pipeline.py +
// worker/src/api/schemas.py exactly; unknown fields are rejected by the worker
// (`extra="forbid"`), so these structs are the canonical artifacts.
// ---------------------------------------------------------------------------

/// Request body for `POST /v1/audio/extract`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ExtractAudioRequest {
    pub video_path: String,
    pub output_path: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub job_id: Option<String>,
}

/// Response of `POST /v1/audio/extract`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ExtractAudioResponse {
    pub output_path: String,
    pub duration_seconds: Option<f64>,
    pub file_size_bytes: u64,
}

/// Request body for `POST /v1/stt/transcribe`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct TranscribeRequest {
    pub audio_path: String,
    pub project_id: String,
    pub model: String,
    pub device: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub language: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub total_duration_seconds: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub job_id: Option<String>,
}

/// Canonical transcript segment (worker `TranscriptSegment`).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct TranscriptSegment {
    pub id: String,
    pub idx: u32,
    #[serde(default)]
    pub speaker: Option<String>,
    pub start: f64,
    pub end: f64,
    pub text: String,
    pub language: String,
    pub confidence: f64,
    #[serde(default)]
    pub words: Option<Vec<serde_json::Value>>,
}

/// Canonical transcript artifact (worker `Transcript`).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Transcript {
    pub schema_version: u32,
    pub project_id: String,
    pub language: String,
    pub model: String,
    pub segments: Vec<TranscriptSegment>,
}

/// One translated segment inside a block (worker `TranslationItem`).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct TranslationItem {
    pub idx: u32,
    pub segment_id: String,
    pub source_text: String,
    pub translated_text: String,
    pub confidence: f64,
}

/// A translation block (worker `TranslationBlock`).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct TranslationBlock {
    pub block_idx: u32,
    pub translations: Vec<TranslationItem>,
}

/// Canonical translation artifact (worker `Translation`).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Translation {
    pub schema_version: u32,
    pub target_language: String,
    pub model: String,
    pub blocks: Vec<TranslationBlock>,
}

/// Request body for `POST /v1/translate`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct TranslateRequest {
    pub transcript: Transcript,
    pub project_id: String,
    pub provider: String,
    pub target_language: String,
    pub model: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub glossary_ver: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub glossary: Option<serde_json::Map<String, serde_json::Value>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub characters: Option<serde_json::Map<String, serde_json::Value>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub rules: Option<Vec<String>>,
    /// Never logged; only sent over loopback in the request body.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub api_key: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub provider_config: Option<serde_json::Map<String, serde_json::Value>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub job_id: Option<String>,
}

/// One subtitle cue (worker `Cue`).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Cue {
    pub cue_number: u32,
    pub start: f64,
    pub end: f64,
    pub text: String,
}

/// Request body for `POST /v1/subtitle`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct SubtitleRequest {
    pub transcript: Transcript,
    pub translation: Translation,
    pub project_id: String,
    pub output_dir: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub language: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub job_id: Option<String>,
}

/// Response of `POST /v1/subtitle`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct SubtitleResponse {
    pub cues: Vec<Cue>,
    pub ass_path: String,
    pub srt_path: String,
    #[serde(default)]
    pub warnings: Vec<String>,
}

/// Text watermark for `POST /v1/render` (worker `WatermarkTextRequest`).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct WatermarkText {
    pub text: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub position: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub margin: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub x: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub y: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub font_size: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub color: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub opacity: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub rotation: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub font: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub font_file: Option<String>,
}

/// Image watermark for `POST /v1/render` (worker `WatermarkImageRequest`).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct WatermarkImage {
    pub image_path: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub position: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub margin: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub x: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub y: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub width: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub opacity: Option<f64>,
}

/// One cue to speak (worker `TTSCueRequest`).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct TtsCue {
    pub start: f64,
    pub end: f64,
    pub text: String,
}

/// Request body for `POST /v1/tts/synthesize` (dubbing voice track).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct TtsRequest {
    pub cues: Vec<TtsCue>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub voice: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub engine: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub language: Option<String>,
    pub duration_seconds: f64,
    pub output_dir: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub job_id: Option<String>,
}

/// Response of `POST /v1/tts/synthesize`.
#[derive(Debug, Clone, Deserialize, PartialEq)]
pub struct TtsResponse {
    pub voice_track_path: String,
    pub meta_path: String,
    pub cue_count: u32,
    pub engine_used: String,
    pub voice_used: String,
}

/// Request body for `POST /v1/render`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct RenderRequest {
    pub video_path: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub subtitle_path: Option<String>,
    pub output_path: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub encoder: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub preset: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub crf: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub watermark: Option<serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub voice_track_path: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub check_window: Option<(f64, f64)>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub job_id: Option<String>,
}

/// Response of `POST /v1/render`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct RenderResponse {
    pub output_path: String,
    pub encoder_used: String,
    pub duration_seconds: f64,
    pub width: u32,
    pub height: u32,
    /// `[numerator, denominator]` of the output frame rate.
    #[serde(default)]
    pub fps: Vec<u32>,
    pub audio_streams: u32,
}

/// Response of `POST /v1/jobs/{job_id}/cancel`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct CancelResponse {
    pub cancelled: bool,
}

/// Response of `GET /v1/progress/{job_id}` (live stage progress).
///
/// ``progress``/``stage`` are `null` when no stage for the job is currently
/// registered — callers keep their own stage anchors in that case.
#[derive(Debug, Clone, Deserialize, PartialEq)]
pub struct ProgressResponse {
    pub job_id: String,
    pub progress: Option<f64>,
    pub stage: Option<String>,
}

impl WorkerClient {
    pub fn new(port: u16, token: String) -> Self {
        Self { port, token }
    }

    pub fn port(&self) -> u16 {
        self.port
    }

    pub fn addr(&self) -> SocketAddr {
        SocketAddr::from((HOST_LOOPBACK, self.port))
    }

    /// Probe `GET /health` with the session token.
    pub fn check_health(&self) -> Result<HealthResponse, HttpError> {
        let (status, body) = http_get(
            self.addr(),
            "/health",
            &[("Authorization", format!("Bearer {}", self.token))],
            READ_TIMEOUT,
        )?;
        if status != 200 {
            return Err(HttpError::Status(status));
        }
        serde_json::from_slice(&body)
            .map_err(|e| HttpError::MalformedResponse(format!("invalid health body: {e}")))
    }

    /// Copy a rendered video to a user directory and QC it (TASK-029).
    ///
    /// Copying a large rendered video plus its probe/QC can take well beyond
    /// the control-plane read timeout, so this uses the pipeline timeout.
    pub fn export_video(
        &self,
        request: ExportVideoRequest,
    ) -> Result<ExportVideoResponse, HttpError> {
        let body = serde_json::to_vec(&request).map_err(|e| {
            HttpError::MalformedResponse(format!("request serialization failed: {e}"))
        })?;
        let (status, body) = http_post(
            self.addr(),
            "/v1/export/video",
            &[("Authorization", format!("Bearer {}", self.token))],
            &body,
            PIPELINE_IO_TIMEOUT,
        )?;
        parse_json_response(status, body)
    }

    /// Export a subtitle file, optionally converting SRT↔VTT (TASK-029).
    pub fn export_subtitles(
        &self,
        request: ExportSubtitleRequest,
    ) -> Result<ExportSubtitleResponse, HttpError> {
        let body = serde_json::to_vec(&request).map_err(|e| {
            HttpError::MalformedResponse(format!("request serialization failed: {e}"))
        })?;
        let (status, body) = http_post(
            self.addr(),
            "/v1/export/subtitles",
            &[("Authorization", format!("Bearer {}", self.token))],
            &body,
            PIPELINE_IO_TIMEOUT,
        )?;
        parse_json_response(status, body)
    }

    // -- Pipeline stages (RELEASE-P0) -------------------------------------

    /// Extract 16 kHz mono WAV audio from a video (RELEASE-P0-001 route).
    pub fn extract_audio(
        &self,
        request: ExtractAudioRequest,
    ) -> Result<ExtractAudioResponse, HttpError> {
        let (status, body) = self.post_json("/v1/audio/extract", &request)?;
        parse_json_response(status, body)
    }

    /// Transcribe an audio file into a canonical Transcript.
    pub fn transcribe(&self, request: TranscribeRequest) -> Result<Transcript, HttpError> {
        let (status, body) = self.post_json("/v1/stt/transcribe", &request)?;
        parse_json_response(status, body)
    }

    /// Translate a Transcript through the selected provider.
    pub fn translate(&self, request: TranslateRequest) -> Result<Translation, HttpError> {
        let (status, body) = self.post_json("/v1/translate", &request)?;
        parse_json_response(status, body)
    }

    /// Generate subtitle cues + ASS/SRT files from a Transcript + Translation.
    pub fn generate_subtitles(
        &self,
        request: SubtitleRequest,
    ) -> Result<SubtitleResponse, HttpError> {
        let (status, body) = self.post_json("/v1/subtitle", &request)?;
        parse_json_response(status, body)
    }

    /// Synthesize a dubbing voice track from translated cues (`POST /v1/tts/synthesize`).
    pub fn tts_synthesize(&self, request: TtsRequest) -> Result<TtsResponse, HttpError> {
        let (status, body) = self.post_json("/v1/tts/synthesize", &request)?;
        parse_json_response(status, body)
    }

    /// Burn subtitles into a video with FFmpeg/libass.
    pub fn render(&self, request: RenderRequest) -> Result<RenderResponse, HttpError> {
        let (status, body) = self.post_json("/v1/render", &request)?;
        parse_json_response(status, body)
    }

    /// Request cancellation of an in-flight stage for ``job_id`` (idempotent).
    pub fn cancel_job(&self, job_id: &str) -> Result<CancelResponse, HttpError> {
        let path = format!("/v1/jobs/{job_id}/cancel");
        let (status, body) = self.post_json(&path, &serde_json::json!({}))?;
        parse_json_response(status, body)
    }

    /// Poll live stage progress for ``job_id`` (best-effort, polled by the
    /// runner while a stage call is in flight).
    pub fn get_progress(&self, job_id: &str) -> Result<ProgressResponse, HttpError> {
        let path = format!("/v1/progress/{job_id}");
        let (status, body) = http_get(
            self.addr(),
            &path,
            &[("Authorization", format!("Bearer {}", self.token))],
            PROGRESS_READ_TIMEOUT,
        )?;
        parse_json_response(status, body)
    }

    /// Probe whether a provider kind is reachable/configured (Provider test).
    ///
    /// The worker validates credentials/endpoints against the *live* provider
    /// (or the local server for free/local kinds); the Rust core records the
    /// outcome on the provider row.
    pub fn test_provider(
        &self,
        provider_kind: &str,
        config: &serde_json::Map<String, serde_json::Value>,
        api_key: Option<&str>,
    ) -> Result<ProviderTestResult, HttpError> {
        let request = ProviderTestRequest {
            provider_kind: provider_kind.to_string(),
            provider_config: if config.is_empty() {
                None
            } else {
                Some(config.clone())
            },
            api_key: api_key.map(str::to_string),
        };
        let (status, body) = self.post_json("/v1/providers/test", &request)?;
        parse_json_response(status, body)
    }

    /// Serialize a request and POST it to ``path`` with the pipeline timeout.
    fn post_json<T: Serialize>(
        &self,
        path: &str,
        request: &T,
    ) -> Result<(u16, Vec<u8>), HttpError> {
        let body = serde_json::to_vec(request).map_err(|e| {
            HttpError::MalformedResponse(format!("request serialization failed: {e}"))
        })?;
        http_post(
            self.addr(),
            path,
            &[("Authorization", format!("Bearer {}", self.token))],
            &body,
            PIPELINE_IO_TIMEOUT,
        )
    }
}

/// Parse a JSON response, surfacing the worker's error envelope when present.
fn parse_json_response<T: serde::de::DeserializeOwned>(
    status: u16,
    body: Vec<u8>,
) -> Result<T, HttpError> {
    if status == 200 {
        return serde_json::from_slice(&body)
            .map_err(|e| HttpError::MalformedResponse(format!("invalid response body: {e}")));
    }
    if let Ok(response) = serde_json::from_slice::<ErrorResponse>(&body) {
        return Err(HttpError::Worker(response.error));
    }
    Err(HttpError::Status(status))
}

/// A tiny HTTP/1.1 GET used only for localhost control-plane calls.
fn http_get(
    addr: SocketAddr,
    path: &str,
    headers: &[(&str, String)],
    read_timeout: Duration,
) -> Result<(u16, Vec<u8>), HttpError> {
    let mut stream = TcpStream::connect_timeout(&addr, CONNECT_TIMEOUT)
        .map_err(|e| HttpError::ConnectFailed(e.to_string()))?;
    stream
        .set_read_timeout(Some(read_timeout))
        .map_err(|e| HttpError::ReadFailed(e.to_string()))?;
    stream
        .set_write_timeout(Some(WRITE_TIMEOUT))
        .map_err(|e| HttpError::WriteFailed(e.to_string()))?;

    let mut request = format!(
        "GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nConnection: close\r\n",
        addr.port()
    );
    for (name, value) in headers {
        request.push_str(&format!("{name}: {value}\r\n"));
    }
    request.push_str("\r\n");
    stream
        .write_all(request.as_bytes())
        .map_err(|e| HttpError::WriteFailed(e.to_string()))?;

    read_response(&mut stream)
}

/// A tiny HTTP/1.1 POST used only for localhost control-plane calls.
fn http_post(
    addr: SocketAddr,
    path: &str,
    headers: &[(&str, String)],
    body: &[u8],
    io_timeout: Duration,
) -> Result<(u16, Vec<u8>), HttpError> {
    let mut stream = TcpStream::connect_timeout(&addr, CONNECT_TIMEOUT)
        .map_err(|e| HttpError::ConnectFailed(e.to_string()))?;
    stream
        .set_read_timeout(Some(io_timeout))
        .map_err(|e| HttpError::ReadFailed(e.to_string()))?;
    stream
        .set_write_timeout(Some(WRITE_TIMEOUT))
        .map_err(|e| HttpError::WriteFailed(e.to_string()))?;

    let mut request = format!(
        "POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nConnection: close\r\nContent-Type: application/json\r\nContent-Length: {}\r\n",
        addr.port(),
        body.len()
    );
    for (name, value) in headers {
        request.push_str(&format!("{name}: {value}\r\n"));
    }
    request.push_str("\r\n");
    stream
        .write_all(request.as_bytes())
        .map_err(|e| HttpError::WriteFailed(e.to_string()))?;
    stream
        .write_all(body)
        .map_err(|e| HttpError::WriteFailed(e.to_string()))?;

    read_response(&mut stream)
}

/// Read and parse a complete HTTP/1.1 response body.
fn read_response(stream: &mut TcpStream) -> Result<(u16, Vec<u8>), HttpError> {
    let raw = read_through_headers(stream)?;
    let header_end = raw
        .windows(4)
        .position(|w| w == b"\r\n\r\n")
        .map(|p| p + 4)
        .unwrap_or(raw.len());
    let (header_block, body_prefix) = raw.split_at(header_end);
    let header_block = String::from_utf8_lossy(header_block);

    let status = parse_status_line(header_block.lines().next().unwrap_or_default())?;

    let content_length = header_block.lines().find_map(|line| {
        let mut parts = line.splitn(2, ':');
        let name = parts.next()?.trim();
        if name.eq_ignore_ascii_case("content-length") {
            parts.next()?.trim().parse::<usize>().ok()
        } else {
            None
        }
    });

    let mut body = body_prefix.to_vec();
    match content_length {
        Some(len) => {
            if len > MAX_BODY_BYTES {
                return Err(HttpError::MalformedResponse(
                    "response body too large".into(),
                ));
            }
            while body.len() < len {
                let mut chunk = [0u8; 4096];
                let n = stream
                    .read(&mut chunk)
                    .map_err(|e| HttpError::ReadFailed(e.to_string()))?;
                if n == 0 {
                    return Err(HttpError::ConnectionClosed);
                }
                body.extend_from_slice(&chunk[..n]);
            }
            if body.len() > len {
                body.truncate(len);
            }
        }
        None => {
            // No Content-Length: read until EOF (Connection: close).
            let mut chunk = [0u8; 4096];
            loop {
                match stream.read(&mut chunk) {
                    Ok(0) => break,
                    Ok(n) => {
                        body.extend_from_slice(&chunk[..n]);
                        if body.len() > MAX_BODY_BYTES {
                            return Err(HttpError::MalformedResponse(
                                "response body too large".into(),
                            ));
                        }
                    }
                    Err(e) if e.kind() == std::io::ErrorKind::TimedOut => break,
                    Err(e) => return Err(HttpError::ReadFailed(e.to_string())),
                }
            }
        }
    }
    Ok((status, body))
}

/// Read bytes up to and including the end of the header block (`\r\n\r\n`).
///
/// Extra bytes read beyond the header terminator are preserved (they may be the
/// start of the body) — the caller splits them apart.
fn read_through_headers(stream: &mut TcpStream) -> Result<Vec<u8>, HttpError> {
    let mut buf = Vec::with_capacity(512);
    let mut chunk = [0u8; 1024];
    loop {
        if buf.windows(4).any(|w| w == b"\r\n\r\n") {
            return Ok(buf);
        }
        if buf.len() > MAX_HEADER_BYTES {
            return Err(HttpError::MalformedResponse(
                "response headers too large".into(),
            ));
        }
        let n = stream
            .read(&mut chunk)
            .map_err(|e| HttpError::ReadFailed(e.to_string()))?;
        if n == 0 {
            return Err(HttpError::ConnectionClosed);
        }
        buf.extend_from_slice(&chunk[..n]);
    }
}

/// Parse the status code from a status line such as `HTTP/1.1 200 OK`.
fn parse_status_line(line: &str) -> Result<u16, HttpError> {
    let code = line
        .split_whitespace()
        .nth(1)
        .and_then(|c| c.parse::<u16>().ok())
        .ok_or_else(|| HttpError::MalformedResponse(format!("bad status line: {line:?}")))?;
    Ok(code)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{BufReader, Read, Write};
    use std::net::{Ipv4Addr, TcpListener};
    use std::thread;

    /// Spawn a one-shot TCP server that lets `handler` answer the request.
    ///
    /// Drains the full request (headers + any ``Content-Length`` body — the
    /// export calls are POSTs) first so the socket receive buffer is empty when
    /// the handler closes the connection — otherwise Windows loopback can
    /// surface RST/read errors instead of a clean EOF.
    fn serve(handler: impl FnOnce(&mut TcpStream) + Send + 'static) -> u16 {
        let listener =
            TcpListener::bind((Ipv4Addr::from(HOST_LOOPBACK), 0)).expect("bind test listener");
        let port = listener.local_addr().expect("local addr").port();
        thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("accept");
            let mut reader = BufReader::new(stream.try_clone().expect("clone"));
            let mut drained = Vec::new();
            let mut chunk = [0u8; 1024];
            loop {
                if drained.windows(4).any(|w| w == b"\r\n\r\n") {
                    break;
                }
                let n = reader.read(&mut chunk).expect("read request");
                if n == 0 {
                    break;
                }
                drained.extend_from_slice(&chunk[..n]);
            }
            let header_end = drained
                .windows(4)
                .position(|w| w == b"\r\n\r\n")
                .map(|p| p + 4)
                .unwrap_or(drained.len());
            let header_block = String::from_utf8_lossy(&drained);
            let content_length = header_block.lines().find_map(|line| {
                let mut parts = line.splitn(2, ':');
                let name = parts.next()?.trim();
                if name.eq_ignore_ascii_case("content-length") {
                    parts.next()?.trim().parse::<usize>().ok()
                } else {
                    None
                }
            });
            // Body bytes may already have been read together with the headers
            // (``drained`` holds them); only read the remainder from the socket.
            if let Some(len) = content_length {
                let missing = header_end + len - drained.len().min(header_end + len);
                let mut rest = vec![0u8; missing];
                if missing > 0 && reader.read_exact(&mut rest).is_err() {
                    eprintln!("test server: failed to drain request body");
                }
            }
            handler(&mut stream);
        });
        port
    }

    fn client_on(port: u16) -> WorkerClient {
        WorkerClient::new(port, "test-token".to_string())
    }

    #[test]
    fn http_get_returns_status_and_body() {
        let port = serve(|stream| {
            let response = "HTTP/1.1 200 OK\r\nContent-Length: 5\r\nConnection: close\r\n\r\nhello";
            let _ = stream.write_all(response.as_bytes());
        });
        let (status, body) = http_get(
            SocketAddr::from((HOST_LOOPBACK, port)),
            "/health",
            &[],
            READ_TIMEOUT,
        )
        .expect("request succeeds");
        assert_eq!(status, 200);
        assert_eq!(body, b"hello");
    }

    #[test]
    fn http_get_handles_body_split_across_header_read() {
        let port = serve(|stream| {
            let response = "HTTP/1.1 200 OK\r\nContent-Length: 11\r\n\r\nhello world";
            let _ = stream.write_all(response.as_bytes());
        });
        let (status, body) = http_get(
            SocketAddr::from((HOST_LOOPBACK, port)),
            "/health",
            &[],
            READ_TIMEOUT,
        )
        .expect("request succeeds");
        assert_eq!(status, 200);
        assert_eq!(body, b"hello world");
    }

    #[test]
    fn check_health_parses_worker_response() {
        let port = serve(|stream| {
            let body = r#"{"status":"ok","version":"0.1.0","gpu":null}"#;
            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}",
                body.len()
            );
            let _ = stream.write_all(response.as_bytes());
        });
        let health = client_on(port).check_health().expect("health succeeds");
        assert_eq!(health.status, "ok");
        assert_eq!(health.version, "0.1.0");
        assert_eq!(health.gpu, None);
    }

    #[test]
    fn check_health_surfaces_non_200() {
        let port = serve(|stream| {
            let response = "HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n\r\n";
            let _ = stream.write_all(response.as_bytes());
        });
        let err = client_on(port).check_health().expect_err("401 is an error");
        assert_eq!(err, HttpError::Status(401));
    }

    #[test]
    fn check_health_rejects_connection_refused() {
        // Nothing listening on this port.
        let listener = TcpListener::bind((Ipv4Addr::from(HOST_LOOPBACK), 0)).expect("bind");
        let port = listener.local_addr().expect("local addr").port();
        drop(listener);
        let err = client_on(port).check_health().expect_err("must fail");
        assert!(matches!(err, HttpError::ConnectFailed(_)));
    }

    #[test]
    fn export_video_parses_worker_response() {
        let port = serve(|stream| {
            let body =
                r#"{"path":"C:\\out\\final.mp4","qc":{"passed":true,"issues":[],"warnings":[]}}"#;
            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}",
                body.len()
            );
            let _ = stream.write_all(response.as_bytes());
        });
        let result = client_on(port)
            .export_video(ExportVideoRequest {
                source_video: "C:\\in.mp4".into(),
                target_dir: "C:\\out".into(),
                name: None,
                run_qc: None,
            })
            .expect("export succeeds");
        assert!(result.path.ends_with("final.mp4"));
        assert!(result.qc.passed);
        assert!(result.qc.issues.is_empty());
    }

    #[test]
    fn export_video_surfaces_worker_error_envelope() {
        let port = serve(|stream| {
            let body = r#"{"error":{"code":"E_PERMISSION_DENIED","message":"Không có quyền ghi vào thư mục.","recoverable":true}}"#;
            let response = format!(
                "HTTP/1.1 422 Unprocessable Entity\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}",
                body.len()
            );
            let _ = stream.write_all(response.as_bytes());
        });
        let err = client_on(port)
            .export_video(ExportVideoRequest {
                source_video: "C:\\in.mp4".into(),
                target_dir: "C:\\out".into(),
                name: None,
                run_qc: None,
            })
            .expect_err("422 is an error");
        match err {
            HttpError::Worker(envelope) => {
                assert_eq!(envelope.code, "E_PERMISSION_DENIED");
                assert!(envelope.recoverable);
            }
            other => panic!("expected Worker error, got {other:?}"),
        }
    }

    #[test]
    fn export_subtitles_parses_worker_response() {
        let port = serve(|stream| {
            let body = r#"{"path":"C:\\out\\subtitle.vtt"}"#;
            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}",
                body.len()
            );
            let _ = stream.write_all(response.as_bytes());
        });
        let result = client_on(port)
            .export_subtitles(ExportSubtitleRequest {
                source_subtitle: "C:\\in.srt".into(),
                target_dir: "C:\\out".into(),
                name: None,
                format: Some("vtt".into()),
            })
            .expect("export succeeds");
        assert!(result.path.ends_with("subtitle.vtt"));
    }

    #[test]
    fn check_health_rejects_premature_close() {
        let port = serve(|stream| {
            let _ = stream.write_all(b"HTTP/1.1 200 OK\r\nContent-Le");
        });
        let err = client_on(port).check_health().expect_err("must fail");
        assert!(matches!(err, HttpError::ConnectionClosed));
    }

    #[test]
    fn check_health_rejects_bad_status_line() {
        let port = serve(|stream| {
            let _ = stream.write_all(b"BOGUS\r\nContent-Length: 0\r\n\r\n");
        });
        let err = client_on(port).check_health().expect_err("must fail");
        assert!(matches!(err, HttpError::MalformedResponse(_)));
    }

    fn sample_transcript() -> Transcript {
        Transcript {
            schema_version: 1,
            project_id: "proj-1".into(),
            language: "vi".into(),
            model: "large-v3".into(),
            segments: vec![TranscriptSegment {
                id: "seg_0".into(),
                idx: 0,
                speaker: None,
                start: 0.0,
                end: 1.2,
                text: "Xin chào".into(),
                language: "vi".into(),
                confidence: 0.98,
                words: None,
            }],
        }
    }

    #[test]
    fn extract_audio_parses_worker_response() {
        let port = serve(|stream| {
            let body = r#"{"output_path":"C:\\out\\audio.wav","duration_seconds":12.5,"file_size_bytes":400000}"#;
            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}",
                body.len()
            );
            let _ = stream.write_all(response.as_bytes());
        });
        let result = client_on(port)
            .extract_audio(ExtractAudioRequest {
                video_path: "C:\\in.mp4".into(),
                output_path: "C:\\out\\audio.wav".into(),
                job_id: Some("job-1".into()),
            })
            .expect("extract succeeds");
        assert!(result.output_path.ends_with("audio.wav"));
        assert_eq!(result.duration_seconds, Some(12.5));
        assert_eq!(result.file_size_bytes, 400000);
    }

    #[test]
    fn transcribe_round_trips_canonical_transcript() {
        let port = serve(|stream| {
            let body = r#"{"schema_version":1,"project_id":"proj-1","language":"vi","model":"large-v3","segments":[{"id":"seg_0","idx":0,"speaker":null,"start":0.0,"end":1.2,"text":"Xin chào","language":"vi","confidence":0.98,"words":null}]}"#;
            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}",
                body.len()
            );
            let _ = stream.write_all(response.as_bytes());
        });
        let transcript = client_on(port)
            .transcribe(TranscribeRequest {
                audio_path: "C:\\audio.wav".into(),
                project_id: "proj-1".into(),
                model: "large-v3".into(),
                device: "auto".into(),
                language: Some("vi".into()),
                total_duration_seconds: None,
                job_id: Some("job-1".into()),
            })
            .expect("transcribe succeeds");
        assert_eq!(transcript, sample_transcript());
    }

    #[test]
    fn translate_sends_provider_and_parses_translation() {
        let port = serve(|stream| {
            let body = r#"{"schema_version":1,"target_language":"zh","model":"gemini-2.5-flash-lite","blocks":[{"block_idx":0,"translations":[{"idx":0,"segment_id":"seg_0","source_text":"Xin chào","translated_text":"你好","confidence":0.99}]}]}"#;
            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}",
                body.len()
            );
            let _ = stream.write_all(response.as_bytes());
        });
        let result = client_on(port)
            .translate(TranslateRequest {
                transcript: sample_transcript(),
                project_id: "proj-1".into(),
                provider: "gemini".into(),
                target_language: "zh".into(),
                model: "gemini-2.5-flash-lite".into(),
                glossary_ver: None,
                glossary: None,
                characters: None,
                rules: None,
                api_key: Some("secret".into()),
                provider_config: None,
                job_id: Some("job-1".into()),
            })
            .expect("translate succeeds");
        assert_eq!(result.blocks.len(), 1);
        assert_eq!(result.blocks[0].translations[0].translated_text, "你好");
    }

    #[test]
    fn generate_subtitles_parses_cues_and_paths() {
        let port = serve(|stream| {
            let body = r#"{"cues":[{"cue_number":1,"start":0.0,"end":1.2,"text":"你好"}],"ass_path":"C:\\sub\\subtitle.ass","srt_path":"C:\\sub\\subtitle.srt","warnings":[]}"#;
            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}",
                body.len()
            );
            let _ = stream.write_all(response.as_bytes());
        });
        let result = client_on(port)
            .generate_subtitles(SubtitleRequest {
                transcript: sample_transcript(),
                translation: Translation {
                    schema_version: 1,
                    target_language: "zh".into(),
                    model: "gemini-2.5-flash-lite".into(),
                    blocks: vec![],
                },
                project_id: "proj-1".into(),
                output_dir: "C:\\sub".into(),
                language: None,
                job_id: None,
            })
            .expect("subtitle succeeds");
        assert_eq!(result.cues.len(), 1);
        assert!(result.srt_path.ends_with("subtitle.srt"));
        assert_eq!(result.cues[0].text, "你好");
    }

    #[test]
    fn render_parses_worker_response() {
        let port = serve(|stream| {
            let body = r#"{"output_path":"C:\\out\\final.mp4","encoder_used":"libx264","duration_seconds":12.5,"width":1920,"height":1080,"fps":[25,1],"audio_streams":1}"#;
            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}",
                body.len()
            );
            let _ = stream.write_all(response.as_bytes());
        });
        let result = client_on(port)
            .render(RenderRequest {
                video_path: "C:\\in.mp4".into(),
                subtitle_path: Some("C:\\sub\\subtitle.ass".into()),
                output_path: "C:\\out\\final.mp4".into(),
                encoder: None,
                preset: None,
                crf: None,
                watermark: None,
                voice_track_path: None,
                check_window: None,
                job_id: Some("job-1".into()),
            })
            .expect("render succeeds");
        assert_eq!(result.encoder_used, "libx264");
        assert_eq!(result.fps, vec![25, 1]);
        assert_eq!(result.audio_streams, 1);
    }

    #[test]
    fn cancel_job_parses_idempotent_response() {
        let port = serve(|stream| {
            let body = r#"{"cancelled":true}"#;
            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}",
                body.len()
            );
            let _ = stream.write_all(response.as_bytes());
        });
        let result = client_on(port)
            .cancel_job("job-1")
            .expect("cancel succeeds");
        assert!(result.cancelled);
    }

    #[test]
    fn get_progress_parses_live_progress() {
        let port = serve(|stream| {
            let body = r#"{"job_id":"job-1","progress":0.42,"stage":"transcribe"}"#;
            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}",
                body.len()
            );
            let _ = stream.write_all(response.as_bytes());
        });
        let result = client_on(port)
            .get_progress("job-1")
            .expect("progress succeeds");
        assert_eq!(result.job_id, "job-1");
        assert_eq!(result.progress, Some(0.42));
        assert_eq!(result.stage.as_deref(), Some("transcribe"));
    }

    #[test]
    fn get_progress_parses_unknown_job_as_null() {
        let port = serve(|stream| {
            let body = r#"{"job_id":"job-x","progress":null,"stage":null}"#;
            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}",
                body.len()
            );
            let _ = stream.write_all(response.as_bytes());
        });
        let result = client_on(port)
            .get_progress("job-x")
            .expect("progress succeeds");
        assert_eq!(result.progress, None);
        assert_eq!(result.stage, None);
    }

    #[test]
    fn pipeline_error_envelope_is_surfaced() {
        let port = serve(|stream| {
            let body = r#"{"error":{"code":"E_CANCELLED","message":"Render was cancelled.","recoverable":false}}"#;
            let response = format!(
                "HTTP/1.1 409 Conflict\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}",
                body.len()
            );
            let _ = stream.write_all(response.as_bytes());
        });
        let err = client_on(port)
            .cancel_job("job-1")
            .expect_err("409 is an error");
        match err {
            HttpError::Worker(envelope) => {
                assert_eq!(envelope.code, "E_CANCELLED");
                assert!(!envelope.recoverable);
            }
            other => panic!("expected Worker error, got {other:?}"),
        }
    }
}
