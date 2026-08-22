//! PipelineRunner (RELEASE-P0-003): the concrete `JobRunner` that executes the
//! MVP vertical slice against the Python worker over the loopback HTTP API.
//!
//! Job types map to worker stages (MASTER_PLAN.md §17.1; worker routes
//! `RELEASE-P0-001`):
//!
//! - `transcribe` → extract audio (`/v1/audio/extract`) + STT
//!   (`/v1/stt/transcribe`), persisting `cache/transcript.json`.
//! - `translate` → `/v1/translate` with the selected provider (credential from
//!   the OS vault — never hardcoded, never logged), persisting
//!   `cache/translation.json`.
//! - `subtitle` → `/v1/subtitle`, persisting `cache/subtitle.{srt,ass}` and
//!   syncing the cues into `SubtitleService` so the editor can edit them.
//! - `render` → `/v1/render`, burning `cache/subtitle.ass` into the source
//!   video and writing `output/{name}.mp4`.
//!
//! Artifacts are per-project, under the validated project directory:
//!
//! ```text
//! {data}/projects/{project_id}/cache/{audio.wav, transcript.json,
//!      translation.json, subtitle.srt, subtitle.ass}
//! {data}/projects/{project_id}/output/{name}.mp4
//! ```
//!
//! The runner fetches a fresh authenticated `WorkerClient` per job run
//! (the loopback port + session token rotate when the worker restarts), polls
//! `ctx.is_cancelled()` between stages, and reports progress through
//! `ctx.progress`. Errors from the worker's canonical envelope map to
//! `JobRunError::Transient`/`Permanent` by the envelope's `recoverable` flag.

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::mpsc::RecvTimeoutError;
use std::sync::Arc;
use std::time::{Duration, Instant};

use serde_json::Value;

use crate::db::repo::task::{Task, TaskStatus, TaskType};
use crate::db::{utc_iso8601_now, DbError, Job, JobType, SubtitleCue};
use crate::security::secret_store::SecretStore;
use crate::services::dictionary_service::DictionaryService;
use crate::services::job_service::{JobRunContext, JobRunError, JobRunner};
use crate::services::project_service::ProjectService;
use crate::services::provider_service::ProviderService;
use crate::services::settings_service::SettingsService;
use crate::services::subtitle_service::{CueInput, SubtitleService};
use crate::services::task_runner;
use crate::services::worker_client::{
    AudioProcessRequest, ChunkedAutomationRequest, ChunkedFinalizeRequest, ExtractAudioRequest,
    HttpError, LogoRegion, LogoRemoveRequest, RenderRequest, RenderSubtitleCue,
    RenderSubtitleStyle, SubtitleRequest, TranscribeRequest, Transcript, TranslateRequest,
    Translation, TtsCue, TtsRequest, WorkerClient,
};
use crate::services::worker_manager::WorkerManager;

// Canonical artifact names under the project directory (shared with the
// frontend via `pipeline.artifact_paths`).
pub const ARTIFACT_AUDIO: &str = "cache/audio.wav";
pub const ARTIFACT_TRANSCRIPT: &str = "cache/transcript.json";
pub const ARTIFACT_TRANSLATION: &str = "cache/translation.json";
pub const ARTIFACT_SUBTITLE_ASS: &str = "cache/subtitle.ass";
pub const ARTIFACT_SUBTITLE_SRT: &str = "cache/subtitle.srt";
pub const ARTIFACT_VOICE_TRACK: &str = "cache/voice_track.wav";
pub const ARTIFACT_PERFORMANCE_REPORT: &str = "cache/performance_report.json";
/// Custom-workflow artifacts (logo removal + audio processing).
pub const ARTIFACT_LOGO_REMOVED: &str = "cache/logo_removed.mp4";
pub const ARTIFACT_AUDIO_MIX: &str = "cache/audio_mix.wav";

pub const DEFAULT_RENDER_NAME: &str = "rendered";

/// How long to wait for the worker to abort a stage after cancellation is
/// requested (the worker kills process trees before answering).
const CANCEL_WORKER_ABORT_TIMEOUT: Duration = Duration::from_secs(10);
/// Live-progress poll interval while a stage call is in flight.
const PROGRESS_POLL_INTERVAL: Duration = Duration::from_millis(500);
/// Minimum progress delta before a live update is persisted/emitted.
const PROGRESS_MIN_DELTA: f64 = 0.01;

/// Stage-level retry policy for worker calls that can fail with transient
/// network errors (edge-tts cloud synthesis, provider HTTP translation).
///
/// This layer is distinct from — and complements — the worker's own in-call
/// retries (edge-tts 3×1.5s, Gemini 429/5xx backoff, QualityGate 3×backoff)
/// and the JobService whole-job retry (1s/5s/30s): it reruns the *full stage
/// call* with exponential backoff when the worker has already classified the
/// failure as non-recoverable, so a cloud blip cannot fail the whole job.
const STAGE_MAX_ATTEMPTS: u32 = 3;
/// Base backoff for stage-level retries, doubled per attempt (2s → 4s).
const STAGE_RETRY_BASE_DELAY: Duration = Duration::from_secs(2);
/// How often cancellation is polled while backing off between attempts.
const STAGE_RETRY_POLL_INTERVAL: Duration = Duration::from_millis(100);

/// Canonical per-project artifact locations, derived from the project dir.
///
/// The runner writes/reads these paths and the frontend uses them to preview,
/// edit, and export — one source of truth for the artifact scheme.
#[derive(Debug, Clone)]
pub struct ArtifactPaths {
    pub project_dir: PathBuf,
    pub audio: PathBuf,
    pub transcript: PathBuf,
    pub translation: PathBuf,
    pub subtitle_srt: PathBuf,
    pub subtitle_ass: PathBuf,
    pub voice_track: PathBuf,
    pub performance_report: PathBuf,
    pub logo_removed: PathBuf,
    pub audio_mix: PathBuf,
    pub rendered_video: PathBuf,
}

pub fn artifact_paths(project_dir: &Path) -> ArtifactPaths {
    ArtifactPaths {
        project_dir: project_dir.to_path_buf(),
        audio: project_dir.join(ARTIFACT_AUDIO),
        transcript: project_dir.join(ARTIFACT_TRANSCRIPT),
        translation: project_dir.join(ARTIFACT_TRANSLATION),
        subtitle_srt: project_dir.join(ARTIFACT_SUBTITLE_SRT),
        subtitle_ass: project_dir.join(ARTIFACT_SUBTITLE_ASS),
        voice_track: project_dir.join(ARTIFACT_VOICE_TRACK),
        performance_report: project_dir.join(ARTIFACT_PERFORMANCE_REPORT),
        logo_removed: project_dir.join(ARTIFACT_LOGO_REMOVED),
        audio_mix: project_dir.join(ARTIFACT_AUDIO_MIX),
        rendered_video: project_dir
            .join("output")
            .join(format!("{DEFAULT_RENDER_NAME}.mp4")),
    }
}
/// Where the runner gets its authenticated worker client for a job run.
///
/// `WorkerManager` is the production source; tests inject a stub pointing at a
/// canned HTTP server.
pub trait WorkerClientSource: Send + Sync {
    fn worker_client(&self) -> Option<WorkerClient>;
}

impl WorkerClientSource for WorkerManager {
    fn worker_client(&self) -> Option<WorkerClient> {
        WorkerManager::worker_client(self)
    }
}

/// The concrete pipeline executor wired into `JobService`.
pub struct PipelineRunner {
    workers: Arc<dyn WorkerClientSource>,
    projects: Arc<ProjectService>,
    settings: Arc<SettingsService>,
    secrets: Arc<SecretStore>,
    subtitles: Arc<SubtitleService>,
    dictionary: Arc<DictionaryService>,
    providers: Arc<ProviderService>,
}

impl PipelineRunner {
    pub fn new(
        workers: Arc<dyn WorkerClientSource>,
        projects: Arc<ProjectService>,
        settings: Arc<SettingsService>,
        secrets: Arc<SecretStore>,
        subtitles: Arc<SubtitleService>,
        dictionary: Arc<DictionaryService>,
        providers: Arc<ProviderService>,
    ) -> Self {
        Self {
            workers,
            projects,
            settings,
            secrets,
            subtitles,
            dictionary,
            providers,
        }
    }

    // ---- shared plumbing ------------------------------------------------

    fn client(&self) -> Result<WorkerClient, JobRunError> {
        self.workers
            .worker_client()
            .ok_or_else(|| JobRunError::Transient {
                code: "E_WORKER_NOT_READY".into(),
                message: "the AI worker is not ready — it may still be starting or has crashed"
                    .into(),
            })
    }

    fn project_dir(&self, project_id: &str) -> Result<PathBuf, JobRunError> {
        Ok(self.projects.project_dir(project_id))
    }

    /// Load the source video path for a job: explicit `params.video_path` wins,
    /// otherwise the project's stored source video.
    fn source_video(&self, job: &Job) -> Result<String, JobRunError> {
        if let Some(v) = param_str(&job.params, "video_path") {
            if !v.trim().is_empty() {
                return Ok(v);
            }
        }
        let project = self.projects.load(&job.project_id).map_err(map_db)?;
        if project.source_video_path.trim().is_empty() {
            return Err(permanent(
                "E_PARAMS_INVALID",
                "no video to process: pass `video_path` in the job params or set the project's source video",
            ));
        }
        Ok(project.source_video_path)
    }

    /// Read a JSON artifact produced by an earlier stage into a typed document.
    fn read_json<T: serde::de::DeserializeOwned>(
        &self,
        project_id: &str,
        rel: &str,
        stage: &str,
    ) -> Result<T, JobRunError> {
        let path = self.project_dir(project_id)?.join(rel);
        let raw = fs::read(&path).map_err(|_| {
            permanent(
                "E_ARTIFACT_MISSING",
                format!("missing `{rel}` — run the {stage} stage before this one"),
            )
        })?;
        serde_json::from_slice(&raw).map_err(|e| {
            permanent(
                "E_ARTIFACT_INVALID",
                format!("`{rel}` is not valid JSON ({e})"),
            )
        })
    }

    fn write_artifact<T: serde::Serialize>(
        &self,
        project_id: &str,
        rel: &str,
        value: &T,
    ) -> Result<(), JobRunError> {
        let path = self.project_dir(project_id)?.join(rel);
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).map_err(map_io)?;
        }
        let bytes = serde_json::to_vec_pretty(value).map_err(|e| {
            permanent(
                "E_ARTIFACT_WRITE",
                format!("artifact serialization failed: {e}"),
            )
        })?;
        fs::write(&path, bytes).map_err(|e| {
            permanent(
                "E_ARTIFACT_WRITE",
                format!("cannot write {}: {e}", path.display()),
            )
        })
    }

    /// Run one worker stage call while polling cancellation and live progress.
    ///
    /// The HTTP call runs on a worker thread (it can take many minutes on real
    /// media); the calling thread polls ``ctx.is_cancelled()`` and the worker's
    /// ``/v1/progress/{job_id}`` registry so the job shows moving progress and
    /// cancellation reaches the worker promptly (the worker then kills any
    /// FFmpeg process tree / aborts STT / stops between translate blocks).
    /// ``window`` maps the worker's 0..1 stage progress into the job's own
    /// progress span for this stage (e.g. extract 2%..15%, transcribe 15%..100%).
    fn run_stage<T, F>(
        &self,
        client: &WorkerClient,
        job: &Job,
        ctx: &JobRunContext<'_>,
        window: (f64, f64),
        call: F,
    ) -> Result<T, JobRunError>
    where
        T: Send + 'static,
        F: FnOnce(&WorkerClient) -> Result<T, HttpError> + Send + 'static,
    {
        let job_id = job.id.clone();
        let (tx, rx) = std::sync::mpsc::channel();
        let call_client = client.clone();
        std::thread::spawn(move || {
            let _ = tx.send(call(&call_client));
        });

        let (win_start, win_end) = window;
        let mut last_reported: Option<f64> = None;
        let mut last_message: Option<String> = None;
        loop {
            if (ctx.is_cancelled)() {
                // Ask the worker to abort the in-flight stage. A bounded wait
                // avoids blocking the job forever on a stuck worker — the
                // stage thread finishes on its own when the worker answers.
                let _ = client.cancel_job(&job_id);
                match rx.recv_timeout(CANCEL_WORKER_ABORT_TIMEOUT) {
                    Ok(_) | Err(RecvTimeoutError::Disconnected) => {}
                    Err(RecvTimeoutError::Timeout) => {
                        log::warn!(
                            "worker stage for job {job_id} did not stop promptly after cancel"
                        );
                    }
                }
                return Err(JobRunError::Cancelled);
            }
            match rx.recv_timeout(PROGRESS_POLL_INTERVAL) {
                Ok(result) => return result.map_err(Self::map_http),
                Err(RecvTimeoutError::Disconnected) => {
                    return Err(JobRunError::Permanent {
                        code: "E_WORKER_CALL_FAILED".into(),
                        message: "the worker stage thread exited without a result".into(),
                    });
                }
                Err(RecvTimeoutError::Timeout) => {
                    // Live progress from the worker's stage registry (best-effort;
                    // failures mean "no progress available" — keep the anchors).
                    if let Ok(progress) = client.get_progress(&job_id) {
                        if let Some(p) = progress.progress {
                            let mapped = win_start + p.clamp(0.0, 1.0) * (win_end - win_start);
                            let should_report = last_reported
                                .is_none_or(|last| (mapped - last).abs() >= PROGRESS_MIN_DELTA);
                            if should_report {
                                last_reported = Some(mapped);
                                (ctx.progress)(mapped, progress.stage.as_deref().unwrap_or(""));
                            }
                        }
                        // Forward the worker's real detail line to the live log
                        // exactly once per change (e.g. ``segment 81/127``).
                        if let Some(message) = progress.message {
                            if last_message.as_deref() != Some(message.as_str()) {
                                last_message = Some(message.clone());
                                (ctx.log)("info", &message);
                            }
                        }
                        // Drain the worker's event queue — parallel chunk
                        // pipelines enqueue multiple events per poll interval;
                        // each must reach the frontend live log exactly once.
                        for evt in &progress.events {
                            (ctx.log)(&evt.level, &evt.message);
                        }
                    }
                }
            }
        }
    }

    /// Run one worker stage call with stage-level retry.
    ///
    /// ``run_stage`` answers the single attempt; this wrapper retries transient
    /// failures — and the allowlisted network-flavoured *permanent* codes —
    /// with exponential backoff (2s, 4s) before propagating, logging each
    /// retry through the live job log. Cancellation is honored between
    /// attempts and while backing off, so a user cancel is never delayed by a
    /// retry. ``call`` must be reusable across attempts (a ``Fn`` that clones
    /// its request payload per call — the worker request types are all
    /// ``Clone``).
    fn run_stage_retryable<T, F, C>(
        &self,
        client: &WorkerClient,
        job: &Job,
        ctx: &JobRunContext<'_>,
        window: (f64, f64),
        stage_label: &str,
        call_factory: F,
    ) -> Result<T, JobRunError>
    where
        T: Send + 'static,
        F: Fn() -> C + 'static,
        C: FnOnce(&WorkerClient) -> Result<T, HttpError> + Send + 'static,
    {
        let mut attempt: u32 = 0;
        loop {
            attempt += 1;
            match self.run_stage(client, job, ctx, window, call_factory()) {
                Ok(value) => return Ok(value),
                Err(err) if attempt < STAGE_MAX_ATTEMPTS && stage_error_is_retryable(&err) => {
                    let delay = STAGE_RETRY_BASE_DELAY * attempt;
                    let (code, message) = job_error_code_message(&err);
                    (ctx.log)(
                        "warn",
                        &format!(
                            "{stage_label} failed ({code}: {message}) — retrying in {}s (attempt {attempt}/{STAGE_MAX_ATTEMPTS})",
                            delay.as_secs()
                        ),
                    );
                    let deadline = Instant::now() + delay;
                    while Instant::now() < deadline {
                        if (ctx.is_cancelled)() {
                            return Err(JobRunError::Cancelled);
                        }
                        std::thread::sleep(STAGE_RETRY_POLL_INTERVAL);
                    }
                }
                Err(err) => return Err(err),
            }
        }
    }

    /// Map a worker HTTP failure onto the job-run error taxonomy.
    fn map_http(err: HttpError) -> JobRunError {
        match err {
            HttpError::Worker(envelope) => {
                if envelope.recoverable {
                    JobRunError::Transient {
                        code: envelope.code,
                        message: envelope.message,
                    }
                } else {
                    JobRunError::Permanent {
                        code: envelope.code,
                        message: envelope.message,
                    }
                }
            }
            HttpError::ConnectFailed(_) => JobRunError::Transient {
                code: "E_WORKER_UNREACHABLE".into(),
                message: format!("the AI worker is unreachable: {err}"),
            },
            HttpError::ReadFailed(_) | HttpError::WriteFailed(_) | HttpError::ConnectionClosed => {
                // Transport-level interruption: the worker may have crashed
                // mid-stage and will be restarted — the retry policy reruns the
                // stage on the fresh instance.
                JobRunError::Transient {
                    code: "E_WORKER_CALL_FAILED".into(),
                    message: format!("worker connection interrupted: {err}"),
                }
            }
            other => JobRunError::Permanent {
                code: "E_WORKER_CALL_FAILED".into(),
                message: format!("worker call failed: {other}"),
            },
        }
    }

    /// Resolve a stage-scoped setting: `params.{key}` → settings `{settings_key}`
    /// → `fallback`.
    /// Read a numeric settings value (stored as a string) with a fallback.
    fn setting_f64(&self, key: &str, fallback: f64) -> f64 {
        match self.settings.get(key) {
            Ok(Value::String(s)) => s.trim().parse().unwrap_or(fallback),
            Ok(Value::Number(n)) => n.as_f64().unwrap_or(fallback),
            _ => fallback,
        }
    }

    /// Read an integer settings value (stored as a string) with a fallback.
    fn setting_u32(&self, key: &str, fallback: u32) -> u32 {
        match self.settings.get(key) {
            Ok(Value::String(s)) => s.trim().parse().unwrap_or(fallback),
            Ok(Value::Number(n)) => n.as_u64().map(|v| v as u32).unwrap_or(fallback),
            _ => fallback,
        }
    }

    /// Read a string settings value with a fallback.
    fn setting_str(&self, key: &str, fallback: &str) -> String {
        match self.settings.get(key) {
            Ok(Value::String(s)) => {
                let t = s.trim();
                if t.is_empty() {
                    fallback.to_string()
                } else {
                    t.to_string()
                }
            }
            Ok(Value::Number(n)) => n.to_string(),
            _ => fallback.to_string(),
        }
    }

    fn setting_or(
        &self,
        params: &Value,
        key: &str,
        settings_key: &str,
        fallback: &str,
    ) -> Result<String, JobRunError> {
        if let Some(v) = param_str(params, key) {
            if !v.trim().is_empty() {
                return Ok(v);
            }
        }
        match self.settings.get(settings_key) {
            Ok(Value::String(s)) if !s.trim().is_empty() => Ok(s),
            Ok(_) => Ok(fallback.to_string()),
            Err(_) => Ok(fallback.to_string()),
        }
    }

    // ---- stages ----------------------------------------------------------

    fn run_transcribe(&self, job: &Job, ctx: &JobRunContext<'_>) -> Result<(), JobRunError> {
        let p = &job.params;
        let project_dir = self.project_dir(&job.project_id)?;
        let video_path = self.source_video(job)?;
        let model = self.setting_or(p, "model", "ai.model", "large-v3")?;
        let device = self.setting_or(p, "device", "ai.device", "auto")?;
        let language = param_str(p, "language");
        let total_duration = param_f64(p, "total_duration_seconds");

        let client = self.client()?;
        let audio_path = artifact_paths(&project_dir).audio.display().to_string();
        let job_id = job.id.clone();
        let project_id = job.project_id.clone();

        (ctx.log)("info", "Extracting audio from the source video…");
        (ctx.progress)(0.02, "extract-audio");
        let extract = profile_stage(
            &project_dir,
            "audio_extraction",
            None,
            serde_json::json!({
                "model": model,
                "device": device,
                "source_language": language,
            }),
            || {
                self.run_stage(&client, job, ctx, (0.02, 0.15), {
                    let video_path = video_path.clone();
                    let audio_path = audio_path.clone();
                    let job_id = job_id.clone();
                    move |c| {
                        c.extract_audio(ExtractAudioRequest {
                            video_path,
                            output_path: audio_path,
                            job_id: Some(job_id),
                        })
                    }
                })
            },
        )?;
        if (ctx.is_cancelled)() {
            return Err(JobRunError::Cancelled);
        }
        // No fabricated success: the extracted audio file must exist on disk before
        // we feed it to STT — a 200 without bytes cannot be transcribed.
        let audio_artifact = artifact_paths(&project_dir).audio;
        if !audio_artifact.is_file() || audio_artifact.metadata().map(|m| m.len()).unwrap_or(0) == 0
        {
            return Err(permanent(
                "E_ARTIFACT_MISSING",
                format!(
                    "audio extraction reported success but produced no audio file: {}",
                    audio_artifact.display()
                ),
            ));
        }

        // The extraction measured the source duration — feed it to STT so its
        // per-segment progress maps to 0..1 instead of stalling at the anchor.
        let total_duration = total_duration.or(extract.duration_seconds);

        (ctx.log)("info", "Transcribing audio…");
        (ctx.progress)(0.15, "transcribe");
        let transcript = profile_stage(
            &project_dir,
            "stt",
            None,
            serde_json::json!({
                "model": model,
                "device": device,
                "source_language": language,
                "audio_duration_seconds": total_duration,
            }),
            || {
                self.run_stage(&client, job, ctx, (0.15, 1.0), {
                    let audio_path = audio_path.clone();
                    let job_id = job_id.clone();
                    let project_id = project_id.clone();
                    let model = model.clone();
                    let device = device.clone();
                    let language = language.clone();
                    move |c| {
                        c.transcribe(TranscribeRequest {
                            audio_path,
                            project_id,
                            model,
                            device,
                            language,
                            total_duration_seconds: total_duration,
                            job_id: Some(job_id),
                        })
                    }
                })
            },
        )?;
        if (ctx.is_cancelled)() {
            return Err(JobRunError::Cancelled);
        }

        self.write_artifact(&job.project_id, ARTIFACT_TRANSCRIPT, &transcript)?;
        (ctx.log)(
            "success",
            &format!(
                "Transcription complete — {} segments",
                transcript.segments.len()
            ),
        );
        (ctx.progress)(1.0, "done");
        Ok(())
    }

    fn run_translate(&self, job: &Job, ctx: &JobRunContext<'_>) -> Result<(), JobRunError> {
        let p = &job.params;
        let project_dir = self.project_dir(&job.project_id)?;
        let transcript: Transcript =
            self.read_json(&job.project_id, ARTIFACT_TRANSCRIPT, "transcribe")?;
        let segment_count = transcript.segments.len();
        // Provider Management: the provider is resolved from the registry —
        // an explicit id from the job params, or the capability default
        // (seeded to FREE) when absent. No hard-coded defaults here.
        let provider_param = param_str(p, "provider").filter(|v| !v.trim().is_empty());
        let resolved = self
            .providers
            .resolve_translation(provider_param.as_deref())
            .map_err(map_db)?;
        let target_language = param_str(p, "target_language").ok_or_else(|| {
            permanent(
                "E_PARAMS_INVALID",
                "translate job is missing `target_language`",
            )
        })?;
        // The model label comes from the job params or the provider row — never
        // a hard-coded model name (AGENTS). When the provider row carries none
        // (e.g. FREE/LOCAL, which select their model via config/path), fall back
        // to the provider kind as an honest cache-key label.
        let model = param_str(p, "model")
            .filter(|v| !v.trim().is_empty())
            .or_else(|| resolved.model.clone().filter(|m| !m.trim().is_empty()))
            .unwrap_or_else(|| resolved.kind.clone());

        // Secrets come exclusively from the OS credential vault — never from
        // params, files, or logs. Only providers whose worker kind needs a
        // credential (e.g. gemini) require one.
        let api_key = if resolved.needs_key {
            match self.secrets.get_api_key(&resolved.id) {
                Ok(Some(key)) => Some(key),
                Ok(None) => {
                    return Err(permanent(
                        "E_API_KEY_MISSING",
                        format!(
                            "no API key stored for provider `{}` — add one in Settings → Providers",
                            resolved.id
                        ),
                    ))
                }
                Err(e) => {
                    return Err(permanent(
                        "E_API_KEY_MISSING",
                        format!("cannot read the stored API key for `{}`: {e}", resolved.id),
                    ))
                }
            }
        } else {
            None
        };

        // Provider-specific non-secret config from the provider row (model /
        // base URL / local server), mapped to the worker's expectations.
        let provider_config = self.providers.translation_config(&resolved);
        let provider = resolved.kind.clone();

        // Project glossary → translation memory (term → translation).
        let mut glossary = serde_json::Map::new();
        let entries = self
            .dictionary
            .glossary_list(&job.project_id)
            .map_err(map_db)?;
        for entry in entries {
            glossary.insert(entry.term, Value::String(entry.translation));
        }
        let glossary = if glossary.is_empty() {
            None
        } else {
            Some(glossary)
        };
        let glossary_ver = self
            .dictionary
            .glossary_fingerprint(&job.project_id)
            .ok()
            .or_else(|| param_str(p, "glossary_ver"))
            .unwrap_or_else(|| "0".to_string());

        let client = self.client()?;
        let job_id = job.id.clone();
        let project_id = job.project_id.clone();
        let characters = param_object(p, "characters");
        let rules = param_string_array(p, "rules");
        let provider_config = if provider_config.is_empty() {
            None
        } else {
            Some(provider_config)
        };
        (ctx.log)(
            "info",
            &format!(
                "Translating {} segments → {target_language}…",
                segment_count
            ),
        );
        (ctx.progress)(0.1, "translate");
        // Translation hits a cloud/provider HTTP API — transient network blips
        // retry with backoff at the stage level (see `run_stage_retryable`).
        // The request is built once and cloned per attempt (all request types
        // are `Clone`), so every retry sends an identical payload.
        let request = TranslateRequest {
            transcript,
            project_id,
            provider: provider.clone(),
            target_language: target_language.clone(),
            model: model.clone(),
            glossary_ver: Some(glossary_ver.clone()),
            glossary,
            characters,
            rules,
            api_key,
            provider_config,
            job_id: Some(job_id),
        };
        let translation: Translation = profile_stage(
            &project_dir,
            "translation",
            Some(segment_count),
            serde_json::json!({
                "provider": provider,
                "target_language": target_language,
                "model": model,
                "glossary_ver": glossary_ver,
            }),
            || {
                self.run_stage_retryable(&client, job, ctx, (0.1, 1.0), "translation", move || {
                    let request = request.clone();
                    move |c| c.translate(request)
                })
            },
        )?;
        if (ctx.is_cancelled)() {
            return Err(JobRunError::Cancelled);
        }

        self.write_artifact(&job.project_id, ARTIFACT_TRANSLATION, &translation)?;
        let translated_items = translation
            .blocks
            .iter()
            .map(|b| b.translations.len())
            .sum::<usize>();
        (ctx.log)(
            "success",
            &format!("Translation complete — {translated_items} segments"),
        );
        (ctx.progress)(1.0, "done");
        Ok(())
    }

    fn run_subtitle(&self, job: &Job, ctx: &JobRunContext<'_>) -> Result<(), JobRunError> {
        let project_dir = self.project_dir(&job.project_id)?;
        let transcript: Transcript =
            self.read_json(&job.project_id, ARTIFACT_TRANSCRIPT, "transcribe")?;
        let translation: Translation =
            self.read_json(&job.project_id, ARTIFACT_TRANSLATION, "translate")?;
        let segment_count = transcript.segments.len();
        let translation_blocks = translation.blocks.len();
        let output_dir = artifact_paths(&project_dir)
            .subtitle_srt
            .parent()
            .expect("cache dir")
            .display()
            .to_string();
        let language = param_str(&job.params, "language")
            .filter(|v| !v.trim().is_empty())
            .or(Some(transcript.language.clone()));

        let client = self.client()?;
        let job_id = job.id.clone();
        let project_id = job.project_id.clone();
        (ctx.log)("info", "Generating subtitles…");
        (ctx.progress)(0.1, "subtitle");
        let response = profile_stage(
            &project_dir,
            "subtitle_generation",
            Some(segment_count),
            serde_json::json!({
                "translation_blocks": translation_blocks,
                "language": language,
            }),
            || {
                self.run_stage(&client, job, ctx, (0.1, 1.0), move |c| {
                    c.generate_subtitles(SubtitleRequest {
                        transcript,
                        translation,
                        project_id: project_id.clone(),
                        output_dir: output_dir.clone(),
                        language: language.clone(),
                        job_id: Some(job_id.clone()),
                    })
                })
            },
        )?;
        if (ctx.is_cancelled)() {
            return Err(JobRunError::Cancelled);
        }

        // Sync the generated cues into the editor's project-scoped cue table so
        // the subtitle editor can edit them (TASK-025). User edits are preserved
        // across re-runs: cues matching an existing row by timing keep the row's
        // text/timing/speaker when the row is user-owned (status `edited`/
        // `approved`) or the text is unchanged; a fresh translation (different
        // text on a non-user-owned row) wins so re-running with another target
        // language actually updates the subtitles.
        let fresh: Vec<CueInput> = response
            .cues
            .iter()
            .map(|c| CueInput {
                cue_number: c.cue_number as i64,
                start: c.start,
                end: c.end,
                text: c.text.clone(),
                speaker: None,
                source_text: None,
            })
            .collect();
        let existing = self.subtitles.list(&job.project_id).map_err(map_db)?;
        let merged = merge_subtitle_cues(&existing, &fresh);
        self.subtitles
            .replace_project(&job.project_id, merged)
            .map_err(map_db)?;
        (ctx.log)(
            "success",
            &format!("Subtitles generated — {} cues", response.cues.len()),
        );
        (ctx.progress)(1.0, "done");
        Ok(())
    }

    fn run_tts(&self, job: &Job, ctx: &JobRunContext<'_>) -> Result<(), JobRunError> {
        let p = &job.params;
        let project_dir = self.project_dir(&job.project_id)?;
        // Cues come from the persisted subtitle cue table (subtitle stage ran
        // before tts). Only well-formed, non-empty cues with valid timing are
        // spoken. [H-01] Validate timestamps to prevent TTS from generating
        // audio at wrong positions (negative times, end-before-start, or
        // severely out-of-order cues).
        let stored = self.subtitles.list(&job.project_id).map_err(map_db)?;
        let mut prev_end: f64 = 0.0;
        let mut cue_warnings: Vec<String> = Vec::new();
        let cues: Vec<TtsCue> = stored
            .iter()
            .filter(|c| {
                // Basic shape: end must exceed start and text must be non-empty.
                if c.end <= c.start || c.text.trim().is_empty() {
                    return false;
                }
                // [H-01] Reject cues with negative timestamps — these indicate
                // corrupted subtitle data and would generate audio at invalid
                // positions.
                if c.start < 0.0 || c.end < 0.0 {
                    cue_warnings.push(format!(
                        "dropped cue {}: negative timestamp ({:.2}s–{:.2}s)",
                        c.cue_number, c.start, c.end
                    ));
                    return false;
                }
                // [H-01] Warn on out-of-order cues (end < previous end) — not
                // dropped because TTS can still synthesize them, but the
                // timeline may produce overlapping speech.
                if c.end < prev_end {
                    cue_warnings.push(format!(
                        "cue {}: end ({:.2}s) precedes previous end ({:.2}s) — possible overlap",
                        c.cue_number, c.end, prev_end
                    ));
                }
                prev_end = prev_end.max(c.end);
                true
            })
            .map(|c| TtsCue {
                start: c.start,
                end: c.end,
                text: c.text.clone(),
            })
            .collect();
        for w in &cue_warnings {
            (ctx.log)("warn", w);
        }
        if cues.is_empty() {
            return Err(permanent(
                "E_ARTIFACT_MISSING",
                "no subtitle cues — run the subtitle stage before voice generation",
            ));
        }
        // The voice track must span the speech: use the last cue's end.
        let duration_seconds = cues.iter().map(|c| c.end).fold(0.0_f64, f64::max).max(1.0);
        let target_language = param_str(p, "target_language").unwrap_or_else(|| "vi".to_string());
        let voice = param_str(p, "voice").filter(|v| !v.trim().is_empty());
        let engine = param_str(p, "engine")
            .filter(|v| !v.trim().is_empty())
            .unwrap_or_else(|| "edge".to_string());
        let output_dir = artifact_paths(&project_dir)
            .audio
            .parent()
            .expect("cache dir")
            .display()
            .to_string();

        let client = self.client()?;
        let job_id = job.id.clone();
        let cue_count = cues.len();
        let engine_label = engine.clone();
        (ctx.log)("info", "Generating dubbed audio…");
        (ctx.progress)(0.1, "tts");
        // Voice dubbing hits a cloud TTS service (edge-tts) or spawns local
        // synthesis — transient blips retry with backoff at the stage level
        // (see `run_stage_retryable`), complementing the worker's own 3×1.5s
        // edge-tts retry. The request is built once and cloned per attempt.
        let request = TtsRequest {
            cues,
            voice,
            engine: Some(engine),
            language: Some(target_language.clone()),
            duration_seconds,
            output_dir,
            job_id: Some(job_id),
        };
        let response = profile_stage(
            &project_dir,
            "tts",
            Some(cue_count),
            serde_json::json!({
                "engine": engine_label,
                "target_language": target_language,
                "duration_seconds": duration_seconds,
            }),
            || {
                self.run_stage_retryable(
                    &client,
                    job,
                    ctx,
                    (0.1, 1.0),
                    "voice dubbing",
                    move || {
                        let request = request.clone();
                        move |c| c.tts_synthesize(request)
                    },
                )
            },
        )?;
        if (ctx.is_cancelled)() {
            return Err(JobRunError::Cancelled);
        }
        // No fabricated success: the worker validated its own result before
        // answering, but a voice track that is missing or empty on disk still
        // means the dubbing stage did not actually produce usable audio.
        let voice_track_path = PathBuf::from(&response.voice_track_path);
        if !voice_track_path.is_file()
            || voice_track_path.metadata().map(|m| m.len()).unwrap_or(0) == 0
        {
            return Err(permanent(
                "E_ARTIFACT_MISSING",
                format!(
                    "voice dubbing reported success but produced no audio file: {}",
                    voice_track_path.display()
                ),
            ));
        }
        (ctx.log)(
            "success",
            &format!(
                "Voice track ready — {cue_count} cues, {engine_label} ({})",
                voice_track_path.display()
            ),
        );
        (ctx.progress)(1.0, "done");
        Ok(())
    }

    fn run_logo(&self, job: &Job, ctx: &JobRunContext<'_>) -> Result<(), JobRunError> {
        let p = &job.params;
        let project_dir = self.project_dir(&job.project_id)?;
        let video_path = self.source_video(job)?;
        let output_path = artifact_paths(&project_dir).logo_removed;
        if let Some(parent) = output_path.parent() {
            fs::create_dir_all(parent).map_err(map_io)?;
        }
        let region = LogoRegion {
            x: param_u32(p, "logo_x").unwrap_or(0) as i32,
            y: param_u32(p, "logo_y").unwrap_or(0) as i32,
            width: param_u32(p, "logo_width").unwrap_or(64).max(1) as i32,
            height: param_u32(p, "logo_height").unwrap_or(64).max(1) as i32,
            time_start: param_f64(p, "logo_time_start"),
            time_end: param_f64(p, "logo_time_end"),
        };
        let client = self.client()?;
        let job_id = job.id.clone();
        let output_str = output_path.display().to_string();
        (ctx.log)("info", "Removing the marked logo…");
        (ctx.progress)(0.1, "logo");
        let _response = profile_stage(
            &project_dir,
            "logo_removal",
            None,
            serde_json::json!({
                "region": {
                    "x": region.x,
                    "y": region.y,
                    "width": region.width,
                    "height": region.height,
                    "time_start": region.time_start,
                    "time_end": region.time_end,
                },
            }),
            || {
                self.run_stage(&client, job, ctx, (0.1, 1.0), move |c| {
                    c.remove_logo(LogoRemoveRequest {
                        video_path: video_path.clone(),
                        output_path: output_str.clone(),
                        region: region.clone(),
                        job_id: Some(job_id.clone()),
                    })
                })
            },
        )?;
        if (ctx.is_cancelled)() {
            return Err(JobRunError::Cancelled);
        }
        if !output_path.is_file() || output_path.metadata().map(|m| m.len()).unwrap_or(0) == 0 {
            return Err(permanent(
                "E_ARTIFACT_MISSING",
                format!(
                    "logo removal reported success but produced no output file: {}",
                    output_path.display()
                ),
            ));
        }
        (ctx.log)(
            "success",
            &format!("Logo removed — {}", output_path.display()),
        );
        (ctx.progress)(1.0, "done");
        Ok(())
    }

    fn run_audio(&self, job: &Job, ctx: &JobRunContext<'_>) -> Result<(), JobRunError> {
        let p = &job.params;
        let project_dir = self.project_dir(&job.project_id)?;
        let video_path = self.source_video(job)?;
        let output_path = artifact_paths(&project_dir).audio_mix;
        if let Some(parent) = output_path.parent() {
            fs::create_dir_all(parent).map_err(map_io)?;
        }
        let mode = param_str(p, "audio_mode").unwrap_or_else(|| "vocal_removal".to_string());
        let mode_label = mode.clone();
        let client = self.client()?;
        let job_id = job.id.clone();
        let output_str = output_path.display().to_string();
        (ctx.log)("info", "Processing audio…");
        (ctx.progress)(0.1, "audio");
        let _response = profile_stage(
            &project_dir,
            "audio_processing",
            None,
            serde_json::json!({
                "mode": mode_label,
            }),
            || {
                self.run_stage(&client, job, ctx, (0.1, 1.0), move |c| {
                    c.process_audio(AudioProcessRequest {
                        video_path: video_path.clone(),
                        output_path: output_str.clone(),
                        mode: mode_label.clone(),
                        job_id: Some(job_id.clone()),
                    })
                })
            },
        )?;
        if (ctx.is_cancelled)() {
            return Err(JobRunError::Cancelled);
        }
        if !output_path.is_file() || output_path.metadata().map(|m| m.len()).unwrap_or(0) == 0 {
            return Err(permanent(
                "E_ARTIFACT_MISSING",
                format!(
                    "audio processing reported success but produced no output file: {}",
                    output_path.display()
                ),
            ));
        }
        (ctx.log)(
            "success",
            &format!("Audio processed ({mode}) — {}", output_path.display()),
        );
        (ctx.progress)(1.0, "done");
        Ok(())
    }

    fn run_render(&self, job: &Job, ctx: &JobRunContext<'_>) -> Result<(), JobRunError> {
        let p = &job.params;
        let project_dir = self.project_dir(&job.project_id)?;
        let paths = artifact_paths(&project_dir);
        // The custom-workflow logo step writes ``cache/logo_removed.mp4``;
        // when it ran, the render burns subtitles onto the logo-free video
        // instead of the untouched source.
        let video_path = if param_str(p, "logo_removed")
            .map(|v| v == "true")
            .unwrap_or(false)
            && paths.logo_removed.is_file()
        {
            paths.logo_removed.clone()
        } else {
            PathBuf::from(self.source_video(job)?)
        };
        // The custom-workflow audio step writes ``cache/audio_mix.wav``; when
        // it ran, the render maps that track instead of the original audio
        // (and the dub voice mixes over it).
        let audio_track_path = if param_str(p, "audio_mix")
            .map(|v| v == "true")
            .unwrap_or(false)
            && paths.audio_mix.is_file()
        {
            Some(paths.audio_mix.display().to_string())
        } else {
            None
        };
        // Burn subtitles unless the caller explicitly opted out
        // (`params.burn_subtitles == "false"`); rendering without subtitles
        // is a legitimate output and must not require the ASS artifact.
        let burn_subtitles = param_str(p, "burn_subtitles")
            .map(|v| v == "true")
            .unwrap_or(true);
        let subtitle_path = if burn_subtitles {
            if !paths.subtitle_ass.is_file() {
                return Err(permanent(
                    "E_ARTIFACT_MISSING",
                    "missing `cache/subtitle.ass` — run the subtitle stage before rendering",
                ));
            }
            Some(paths.subtitle_ass.display().to_string())
        } else {
            None
        };
        // Dubbed render: when the caller enabled voice, the TTS voice track
        // (cache/voice_track.wav) is mixed over the original audio by the
        // worker's render stage.
        let voice_track_path = if param_str(p, "voice_track")
            .map(|v| v == "true")
            .unwrap_or(false)
        {
            if !paths.voice_track.is_file() {
                return Err(permanent(
                    "E_ARTIFACT_MISSING",
                    "missing `cache/voice_track.wav` — run the tts stage before rendering with voice",
                ));
            }
            Some(paths.voice_track.display().to_string())
        } else {
            None
        };
        let name = param_str(p, "output_name")
            .filter(|v| !v.trim().is_empty())
            .unwrap_or_else(|| DEFAULT_RENDER_NAME.to_string());
        let output_path = if name == DEFAULT_RENDER_NAME {
            paths.rendered_video
        } else {
            project_dir.join("output").join(format!("{name}.mp4"))
        };
        if let Some(parent) = output_path.parent() {
            fs::create_dir_all(parent).map_err(map_io)?;
        }

        // Burn-in validation window: the longest displayed cue whose baseline
        // (0.5 s before its start, where the worker measures re-encode noise)
        // is guaranteed subtitle-free. Choosing the plain longest cue can put
        // the baseline inside the previous back-to-back cue, so the worker's
        // (active - baseline) delta collapses and a correctly burned render is
        // wrongly rejected. Skipped when subtitles are not burned or no usable
        // cue exists (the format checks still run).
        let check_window = if burn_subtitles {
            let cues = self.subtitles.list(&job.project_id).map_err(map_db)?;
            let windows: Vec<(f64, f64)> = cues
                .iter()
                .filter(|c| c.end > c.start && !c.text.trim().is_empty())
                .map(|c| (c.start, c.end))
                .collect();
            burn_in_check_window(&windows)
        } else {
            None
        };
        // Edited cues + position override. When the cue table was edited (text
        // changed / cue deleted) or the caption was dragged to a custom spot,
        // the render-time ASS is rebuilt from the cue table + style so the
        // output reflects the user's edits. Otherwise the plain
        // cache/subtitle.ass from the subtitle stage is burned as-is.
        let subtitle_cues = if burn_subtitles {
            let cues = self.subtitles.list(&job.project_id).map_err(map_db)?;
            let edited: Vec<RenderSubtitleCue> = cues
                .iter()
                .filter(|c| c.end > c.start && !c.text.trim().is_empty())
                .map(|c| RenderSubtitleCue {
                    cue_number: c.cue_number,
                    start: c.start,
                    end: c.end,
                    text: c.text.clone(),
                })
                .collect();
            (!edited.is_empty()).then_some(edited)
        } else {
            None
        };
        let subtitle_cue_count = subtitle_cues.as_ref().map(|c| c.len());
        let subtitle_style = param_object(p, "subtitle_style").map(|o| RenderSubtitleStyle {
            position: o
                .get("position")
                .and_then(|v| v.as_str())
                .unwrap_or("bottom_center")
                .to_string(),
            custom_x: o.get("custom_x").and_then(|v| v.as_f64()),
            custom_y: o.get("custom_y").and_then(|v| v.as_f64()),
            language: o
                .get("language")
                .and_then(|v| v.as_str())
                .map(str::to_string),
        });

        let client = self.client()?;
        let job_id = job.id.clone();
        (ctx.log)("info", "Rendering the final video…");
        (ctx.progress)(0.1, "render");
        let output_path_str = output_path.display().to_string();
        let encoder = param_str(p, "encoder");
        let preset = param_str(p, "preset");
        let crf = param_u32(p, "crf");
        let watermark = p.get("watermark").cloned().filter(|v| v.is_object());
        let _response = profile_stage(
            &project_dir,
            "final_encoding",
            subtitle_cue_count,
            serde_json::json!({
                "burn_subtitles": burn_subtitles,
                "voice_track": voice_track_path.is_some(),
                "audio_track": audio_track_path.is_some(),
                "subtitle_cues": subtitle_cue_count,
                "encoder": encoder,
                "preset": preset,
                "crf": crf,
                "watermark": watermark.is_some(),
                "check_window": check_window,
            }),
            || {
                self.run_stage(&client, job, ctx, (0.1, 1.0), move |c| {
                    c.render(RenderRequest {
                        video_path: video_path.display().to_string(),
                        subtitle_path,
                        output_path: output_path_str.clone(),
                        encoder: encoder.clone(),
                        preset: preset.clone(),
                        crf,
                        watermark: watermark.clone(),
                        voice_track_path: voice_track_path.clone(),
                        audio_track_path: audio_track_path.clone(),
                        subtitle_cues: subtitle_cues.clone(),
                        subtitle_style: subtitle_style.clone(),
                        check_window,
                        job_id: Some(job_id.clone()),
                    })
                })
            },
        )?;
        if (ctx.is_cancelled)() {
            return Err(JobRunError::Cancelled);
        }
        // No fabricated success: only report "complete" when the output actually
        // exists on disk and is non-empty. The worker also validates its render
        // result before answering, but a 200 without a usable file is a real
        // failure and must surface as such.
        if !output_path.is_file() || output_path.metadata().map(|m| m.len()).unwrap_or(0) == 0 {
            return Err(permanent(
                "E_ARTIFACT_MISSING",
                format!(
                    "render reported success but produced no output file: {}",
                    output_path.display()
                ),
            ));
        }
        (ctx.log)(
            "success",
            &format!("Render complete — {} ready", output_path.display()),
        );
        (ctx.progress)(1.0, "done");
        Ok(())
    }

    /// Chunked parallel pipeline (TASK_AUTOMATION_PINELINE).
    ///
    /// One job runs the whole chain: extract audio → fixed-length chunks under
    /// bounded concurrency (STT → translation → TTS per chunk, overlap context
    /// clamped out of the final timeline) → merged cache artifacts → a single
    /// final encode → final validation + output verification → cleanup. Every
    /// step is real worker processing; the worker never deletes intermediates
    /// until validation and verification both pass.
    fn run_chunk(&self, job: &Job, ctx: &JobRunContext<'_>) -> Result<(), JobRunError> {
        let p = &job.params;
        let project_dir = self.project_dir(&job.project_id)?;
        let paths = artifact_paths(&project_dir);
        let video_path = self.source_video(job)?;
        let client = self.client()?;
        let job_id = job.id.clone();
        let project_id = job.project_id.clone();

        // 1) extract the full audio track (same step the transcribe stage runs)
        (ctx.log)("info", "Chunked pipeline: extracting audio…");
        (ctx.progress)(0.01, "extract-audio");
        let extract = profile_stage(
            &project_dir,
            "audio_extraction",
            None,
            serde_json::json!({}),
            || {
                self.run_stage(&client, job, ctx, (0.01, 0.05), {
                    let video_path = video_path.clone();
                    let audio_path = paths.audio.display().to_string();
                    let job_id = job_id.clone();
                    move |c| {
                        c.extract_audio(ExtractAudioRequest {
                            video_path,
                            output_path: audio_path,
                            job_id: Some(job_id),
                        })
                    }
                })
            },
        )?;
        if (ctx.is_cancelled)() {
            return Err(JobRunError::Cancelled);
        }
        if !paths.audio.is_file() || paths.audio.metadata().map(|m| m.len()).unwrap_or(0) == 0 {
            return Err(permanent(
                "E_ARTIFACT_MISSING",
                "chunked pipeline: audio extraction produced no audio file",
            ));
        }
        let extract_duration = extract.duration_seconds.unwrap_or(0.0);

        // 2) resolve the translation provider from the registry (never
        //    hard-coded) — same resolution the translate stage uses.
        let provider_param = param_str(p, "provider").filter(|v| !v.trim().is_empty());
        let resolved = self
            .providers
            .resolve_translation(provider_param.as_deref())
            .map_err(map_db)?;
        let target_language = param_str(p, "target_language")
            .or_else(|| param_str(p, "targetLanguage"))
            .ok_or_else(|| {
                permanent("E_PARAMS_INVALID", "chunk job is missing `target_language`")
            })?;
        let model = param_str(p, "model")
            .filter(|v| !v.trim().is_empty())
            .or_else(|| resolved.model.clone().filter(|m| !m.trim().is_empty()))
            .unwrap_or_else(|| resolved.kind.clone());
        let api_key = if resolved.needs_key {
            match self.secrets.get_api_key(&resolved.id) {
                Ok(Some(key)) => Some(key),
                Ok(None) => {
                    return Err(permanent(
                        "E_API_KEY_MISSING",
                        format!(
                            "no API key stored for provider `{}` — add one in Settings → Providers",
                            resolved.id
                        ),
                    ))
                }
                Err(e) => {
                    return Err(permanent(
                        "E_API_KEY_MISSING",
                        format!("cannot read the stored API key for `{}`: {e}", resolved.id),
                    ))
                }
            }
        } else {
            None
        };
        let provider_config = self.providers.translation_config(&resolved);
        let provider = resolved.kind.clone();
        let mut glossary = serde_json::Map::new();
        let entries = self
            .dictionary
            .glossary_list(&job.project_id)
            .map_err(map_db)?;
        for entry in entries {
            glossary.insert(entry.term, Value::String(entry.translation));
        }
        let glossary = if glossary.is_empty() {
            None
        } else {
            Some(glossary)
        };
        let glossary_ver = self
            .dictionary
            .glossary_fingerprint(&job.project_id)
            .ok()
            .or_else(|| param_str(p, "glossary_ver"))
            .unwrap_or_else(|| "0".to_string());
        let provider_config = if provider_config.is_empty() {
            None
        } else {
            Some(provider_config)
        };

        // 3) chunk tuning from settings (configurable, never hard-coded)
        let chunk_duration = self.setting_f64("automation.chunk_duration", 30.0);
        let overlap = self.setting_f64("automation.chunk_overlap", 2.0);
        let max_concurrency = self.setting_u32("automation.chunk_concurrency", 4);
        let max_retries = self.setting_u32("automation.chunk_retries", 2);

        // 4) per-request options from the run (dub / voice / burn / watermark)
        // Read params supporting both snake_case (v1) and camelCase (v2 pipeline.submit).
        let dub = param_str(p, "dub")
            .or_else(|| param_str(p, "dubAudio"))
            .map(|v| v == "true" || v == "True")
            .or_else(|| p.get("dubAudio").and_then(|v| v.as_bool()))
            .unwrap_or(false);
        let voice = param_str(p, "voice").filter(|v| !v.trim().is_empty());
        let engine = param_str(p, "engine")
            .or_else(|| param_str(p, "ttsEngine"))
            .filter(|v| !v.trim().is_empty())
            .unwrap_or_else(|| "edge".to_string());
        let source_language = param_str(p, "source_language")
            .or_else(|| param_str(p, "sourceLanguage"))
            .filter(|v| !v.trim().is_empty());
        let stt_model = self.setting_or(p, "model", "ai.model", "large-v3")?;
        let stt_device = self.setting_or(p, "device", "ai.device", "auto")?;
        let stt_mode = self.setting_str("automation.stt_mode", "auto");
        let stt_batch_size = self.setting_u32("automation.stt_batch_size", 2);
        let characters = param_object(p, "characters");
        let rules = param_string_array(p, "rules");

        (ctx.log)(
            "info",
            &format!(
                "Chunked pipeline: {chunk_duration}s chunks, overlap {overlap}s, concurrency {max_concurrency}…"
            ),
        );
        (ctx.progress)(0.05, "chunk");
        let request = ChunkedAutomationRequest {
            job_id: job_id.clone(),
            project_id: project_id.clone(),
            project_dir: project_dir.display().to_string(),
            source_video: video_path.clone(),
            source_audio: paths.audio.display().to_string(),
            target_language: target_language.clone(),
            source_language: source_language.clone(),
            provider: provider.clone(),
            provider_config: provider_config.clone(),
            api_key: api_key.clone(),
            model: model.clone(),
            glossary_ver,
            glossary: glossary.clone(),
            characters: characters.clone(),
            rules: rules.clone(),
            dub,
            voice: voice.clone(),
            tts_engine: engine.clone(),
            stt_model,
            stt_device,
            stt_mode,
            stt_batch_size,
            chunk_duration,
            overlap,
            max_concurrency,
            max_retries,
            duration_tolerance: 0.5,
        };
        let response = profile_stage(
            &project_dir,
            "chunked_pipeline",
            None,
            serde_json::json!({
                "provider": provider,
                "target_language": target_language,
                "dub": dub,
                "chunk_duration": chunk_duration,
                "overlap": overlap,
                "max_concurrency": max_concurrency,
                "max_retries": max_retries,
            }),
            || {
                self.run_stage_retryable(
                    &client,
                    job,
                    ctx,
                    (0.05, 0.9),
                    "chunked pipeline",
                    move || {
                        let request = request.clone();
                        move |c| c.automation_chunked(request)
                    },
                )
            },
        )?;
        if (ctx.is_cancelled)() {
            return Err(JobRunError::Cancelled);
        }
        if !response.failed_chunks.is_empty() {
            return Err(permanent(
                "E_CHUNK_FAILED",
                format!(
                    "{} chunk(s) failed permanently (of {})",
                    response.failed_chunks.len(),
                    response.total_chunks
                ),
            ));
        }
        (ctx.log)(
            "success",
            &format!(
                "Chunked processing complete — {}/{} chunks, artifacts merged",
                response.completed_chunks, response.total_chunks
            ),
        );

        // 5) single final encode (the existing render call, subtitles burned
        //    from the merged ASS + voice track when dubbing)
        let burn_subtitles = param_str(p, "burn_subtitles")
            .map(|v| v == "true")
            .unwrap_or(true);
        let subtitle_path = if burn_subtitles {
            if !paths.subtitle_ass.is_file() {
                return Err(permanent(
                    "E_ARTIFACT_MISSING",
                    "chunked pipeline: missing `cache/subtitle.ass` from the merged assembly",
                ));
            }
            Some(paths.subtitle_ass.display().to_string())
        } else {
            None
        };
        let voice_track_path = if dub {
            // [H-02] Validate both existence AND non-empty: a 0-byte file from
            // a failed PCM assembly would pass the is_file() check but produce
            // silent output when mixed by the renderer.
            if !paths.voice_track.is_file() {
                return Err(permanent(
                    "E_ARTIFACT_MISSING",
                    "chunked pipeline: missing `cache/voice_track.wav` from the merged assembly",
                ));
            }
            if paths.voice_track.metadata().map(|m| m.len()).unwrap_or(0) == 0 {
                return Err(permanent(
                    "E_ARTIFACT_MISSING",
                    "chunked pipeline: voice track file is empty (0 bytes) — TTS assembly may have failed",
                ));
            }
            Some(paths.voice_track.display().to_string())
        } else {
            None
        };
        let watermark = p.get("watermark").cloned().filter(|v| v.is_object());
        let subtitle_style = param_object(p, "subtitle_style").map(|o| RenderSubtitleStyle {
            position: o
                .get("position")
                .and_then(|v| v.as_str())
                .unwrap_or("bottom_center")
                .to_string(),
            custom_x: o.get("custom_x").and_then(|v| v.as_f64()),
            custom_y: o.get("custom_y").and_then(|v| v.as_f64()),
            language: o
                .get("language")
                .and_then(|v| v.as_str())
                .map(str::to_string),
        });
        let output_path = paths.rendered_video.clone();
        if let Some(parent) = output_path.parent() {
            fs::create_dir_all(parent).map_err(map_io)?;
        }
        let output_path_str = output_path.display().to_string();
        (ctx.log)("info", "Chunked pipeline: rendering the final video…");
        (ctx.progress)(0.9, "render");
        let _render = profile_stage(
            &project_dir,
            "final_encoding",
            None,
            serde_json::json!({
                "burn_subtitles": burn_subtitles,
                "voice_track": voice_track_path.is_some(),
                "watermark": watermark.is_some(),
            }),
            || {
                self.run_stage(&client, job, ctx, (0.9, 1.0), {
                    let output_path_str = output_path_str.clone();
                    let job_id = job_id.clone();
                    move |c| {
                        c.render(RenderRequest {
                            video_path: video_path.clone(),
                            subtitle_path: subtitle_path.clone(),
                            output_path: output_path_str,
                            encoder: None,
                            preset: None,
                            crf: None,
                            watermark: watermark.clone(),
                            voice_track_path: voice_track_path.clone(),
                            audio_track_path: None,
                            subtitle_cues: None,
                            subtitle_style: subtitle_style.clone(),
                            check_window: None,
                            job_id: Some(job_id.clone()),
                        })
                    }
                })
            },
        )?;
        if (ctx.is_cancelled)() {
            return Err(JobRunError::Cancelled);
        }
        if !output_path.is_file() || output_path.metadata().map(|m| m.len()).unwrap_or(0) == 0 {
            return Err(permanent(
                "E_ARTIFACT_MISSING",
                "chunked pipeline: render produced no output file",
            ));
        }

        // 6) final validation + output verification + cleanup (worker-side;
        //    temp files are kept unless validation AND verification pass)
        (ctx.progress)(0.99, "validate");
        let finalize = self.run_stage(&client, job, ctx, (0.99, 1.0), {
            let job_id = job_id.clone();
            let project_dir_str = project_dir.display().to_string();
            let output_path = output_path_str.clone();
            move |c| {
                c.automation_finalize(ChunkedFinalizeRequest {
                    job_id,
                    project_dir: project_dir_str,
                    output_path,
                    // The worker probed the source video itself (reliable
                    // ffprobe duration); fall back to the ffmpeg extract
                    // estimate only when it is unavailable.
                    source_duration: if response.total_duration > 0.0 {
                        response.total_duration
                    } else {
                        extract_duration.max(1.0)
                    },
                    duration_tolerance: 0.5,
                })
            }
        })?;
        if finalize.validation != "PASS" || finalize.verification != "PASS" {
            return Err(permanent(
                "E_FINAL_VALIDATION",
                format!(
                    "final validation {}/verification {} — {:?}",
                    finalize.validation, finalize.verification, finalize.issues
                ),
            ));
        }
        (ctx.log)(
            "success",
            &format!(
                "Chunked pipeline complete — final validation PASS, output verified, cleanup {}",
                finalize.cleanup
            ),
        );
        (ctx.progress)(1.0, "done");
        Ok(())
    }
}

impl JobRunner for PipelineRunner {
    fn run(&self, job: &Job, ctx: &JobRunContext<'_>) -> Result<(), JobRunError> {
        if (ctx.is_cancelled)() {
            return Err(JobRunError::Cancelled);
        }
        match job.job_type {
            JobType::Transcribe => self.run_transcribe(job, ctx),
            JobType::Translate => self.run_translate(job, ctx),
            JobType::Subtitle => self.run_subtitle(job, ctx),
            JobType::Tts => self.run_tts(job, ctx),
            JobType::Render => self.run_render(job, ctx),
            JobType::Logo => self.run_logo(job, ctx),
            JobType::Audio => self.run_audio(job, ctx),
            JobType::Chunk => self.run_chunk(job, ctx),
        }
    }
}

/// Wraps a `JobRunner` with real project context so tasks execute with correct
/// `project_id` and merged params (base job params + task overrides).
pub struct PipelineTaskExecutor {
    runner: Arc<dyn JobRunner>,
    project_id: String,
    base_params: Value,
}

impl PipelineTaskExecutor {
    pub fn new(runner: Arc<dyn JobRunner>, project_id: String, base_params: Value) -> Self {
        Self {
            runner,
            project_id,
            base_params,
        }
    }
}

impl task_runner::TaskExecutor for PipelineTaskExecutor {
    fn execute_task(
        &self,
        task: &Task,
        cancel_check: &dyn Fn() -> bool,
        progress_fn: &dyn Fn(f64, &str),
        log_fn: &dyn Fn(&str, &str),
    ) -> Result<task_runner::TaskResult, task_runner::TaskRunnerError> {
        let ctx = JobRunContext {
            progress: progress_fn,
            log: log_fn,
            is_cancelled: cancel_check,
        };

        let job_type = match task.task_type {
            TaskType::Transcribe => JobType::Transcribe,
            TaskType::Translate => JobType::Translate,
            TaskType::Subtitle => JobType::Subtitle,
            TaskType::Tts => JobType::Tts,
            TaskType::Render => JobType::Render,
            TaskType::Logo => JobType::Logo,
            TaskType::Chunk => JobType::Chunk,
            TaskType::Audio => JobType::Audio,
        };

        let mut params = self.base_params.clone();
        if let Some(s) = &task.params_json {
            if let Ok(v) = serde_json::from_str::<Value>(s) {
                if let (Some(base), Some(over)) = (params.as_object_mut(), v.as_object()) {
                    for (k, val) in over {
                        base.insert(k.clone(), val.clone());
                    }
                } else if v.is_object() {
                    params = v;
                }
            }
        }

        let job = Job {
            id: task.id.clone(),
            project_id: self.project_id.clone(),
            job_type,
            status: crate::db::JobStatus::Running,
            progress: 0.0,
            stage: task.stage.clone(),
            params,
            error_code: None,
            error_message: None,
            retry_count: 0,
            cancel_requested: false,
            created_at: task.created_at.clone(),
            updated_at: task.updated_at.clone(),
            started_at: task.started_at.clone(),
            finished_at: None,
            error_log: None,
        };

        match self.runner.run(&job, &ctx) {
            Ok(()) => Ok(task_runner::TaskResult {
                task_id: task.id.clone(),
                status: TaskStatus::Succeeded,
                error_code: None,
                error_message: None,
                result_json: None,
            }),
            Err(JobRunError::Transient { code, message }) => Ok(task_runner::TaskResult {
                task_id: task.id.clone(),
                status: TaskStatus::Failed,
                error_code: Some(code),
                error_message: Some(message),
                result_json: None,
            }),
            Err(JobRunError::Permanent { code, message }) => Ok(task_runner::TaskResult {
                task_id: task.id.clone(),
                status: TaskStatus::Failed,
                error_code: Some(code),
                error_message: Some(message),
                result_json: None,
            }),
            Err(JobRunError::Cancelled) => Ok(task_runner::TaskResult {
                task_id: task.id.clone(),
                status: TaskStatus::Cancelled,
                error_code: Some("CANCELLED".to_string()),
                error_message: Some("task cancelled".to_string()),
                result_json: None,
            }),
        }
    }
}

/// Match regenerated cues against the editor's existing rows so a pipeline
/// re-run does not clobber user edits.
///
/// Cues are matched by timing (±0.5 s, each existing row used once). A matched
/// row is kept when it is user-owned (status `edited`/`approved`) or its text
/// is unchanged — a fresh translation (different text on a non-user-owned row)
/// wins, so re-running with another target language updates the subtitles.
/// Unmatched fresh cues are appended; existing rows with no regenerated
/// counterpart drop away.
fn merge_subtitle_cues(existing: &[SubtitleCue], fresh: &[CueInput]) -> Vec<CueInput> {
    const MATCH_TOLERANCE_S: f64 = 0.5;
    const USER_OWNED: [&str; 2] = ["edited", "approved"];
    let mut used = vec![false; existing.len()];
    let mut out: Vec<CueInput> = Vec::with_capacity(fresh.len());
    for cue in fresh {
        let matched = existing.iter().enumerate().find(|(i, e)| {
            !used[*i]
                && (e.start - cue.start).abs() <= MATCH_TOLERANCE_S
                && (e.end - cue.end).abs() <= MATCH_TOLERANCE_S
        });
        match matched {
            Some((i, existing_cue)) => {
                used[i] = true;
                let user_owned = USER_OWNED.contains(&existing_cue.status.as_str());
                if user_owned || existing_cue.text == cue.text {
                    out.push(CueInput {
                        cue_number: cue.cue_number,
                        start: existing_cue.start,
                        end: existing_cue.end,
                        text: existing_cue.text.clone(),
                        speaker: existing_cue.speaker.clone(),
                        source_text: existing_cue.source_text.clone(),
                    });
                } else {
                    out.push(cue.clone());
                }
            }
            None => out.push(cue.clone()),
        }
    }
    out
}

/// Pick the burn-in validation window: the longest cue whose 0.5 s baseline
/// (where the worker measures re-encode noise) is free of any *other* cue.
///
/// Choosing the plain longest cue can place the baseline inside a back-to-back
/// previous cue; a subtitle visible in both sampled frames collapses the
/// (active - baseline) luminance delta and a correctly burned render is
/// wrongly rejected. `cues` must already be filtered to valid, non-empty cues.
fn burn_in_check_window(cues: &[(f64, f64)]) -> Option<(f64, f64)> {
    const BASELINE_S: f64 = 0.5;
    cues.iter()
        .copied()
        .filter(|(start, _)| *start >= BASELINE_S)
        .filter(|(start, _)| {
            let baseline_lo = start - BASELINE_S;
            !cues
                .iter()
                .any(|(o_start, o_end)| o_end > &baseline_lo && o_start < start)
        })
        .max_by(|(a0, a1), (b0, b1)| (a1 - a0).total_cmp(&(b1 - b0)))
}

// ---- small helpers ---------------------------------------------------------

/// Time a stage closure and record the elapsed time + outcome in the
/// project's performance report (best-effort — a profile write never fails
/// the stage).
fn profile_stage<T, F>(
    project_dir: &Path,
    stage: &str,
    metric: Option<usize>,
    extra: serde_json::Value,
    call: F,
) -> Result<T, JobRunError>
where
    F: FnOnce() -> Result<T, JobRunError>,
{
    let started = Instant::now();
    let result = call();
    let elapsed_ms = started.elapsed().as_millis() as u64;
    let outcome = if result.is_ok() { "ok" } else { "error" };
    let entry = serde_json::json!({
        "stage": stage,
        "elapsed_ms": elapsed_ms,
        "metric": metric,
        "outcome": outcome,
        "extra": extra,
        "at": utc_iso8601_now(),
    });
    let report_path = project_dir.join(ARTIFACT_PERFORMANCE_REPORT);
    let mut report = std::fs::read_to_string(&report_path)
        .ok()
        .and_then(|s| serde_json::from_str::<serde_json::Value>(&s).ok())
        .unwrap_or_else(|| serde_json::json!({ "stages": [] }));
    if report.get("stages").and_then(|v| v.as_array()).is_none() {
        report["stages"] = serde_json::json!([]);
    }
    let stages = report["stages"].as_array_mut().expect("fresh array");
    stages.push(entry);
    if let Some(parent) = report_path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let _ = std::fs::write(
        &report_path,
        serde_json::to_string_pretty(&report).unwrap_or_default(),
    );
    log::info!("stage {stage} completed in {elapsed_ms}ms ({outcome})");
    result
}

fn permanent(code: &str, message: impl Into<String>) -> JobRunError {
    JobRunError::Permanent {
        code: code.to_string(),
        message: message.into(),
    }
}

/// Whether a stage error deserves a stage-level retry. Transient errors always
/// do. A few worker codes are *permanent* by the envelope but are in practice
/// transient network blips — the worker classifies them non-recoverable
/// because it cannot tell a blip from a real failure after its own in-call
/// retries have been exhausted (e.g. edge-tts answered "no audio received"
/// three times in a row, or the provider HTTP call 5xx'd / rate-limited).
fn stage_error_is_retryable(err: &JobRunError) -> bool {
    match err {
        JobRunError::Transient { .. } => true,
        JobRunError::Permanent { code, .. } => matches!(
            code.as_str(),
            "E_TTS_FAILED" | "E_API_ERROR" | "E_API_RATE_LIMIT"
        ),
        JobRunError::Cancelled => false,
    }
}

fn job_error_code_message(err: &JobRunError) -> (String, String) {
    match err {
        JobRunError::Transient { code, message } | JobRunError::Permanent { code, message } => {
            (code.clone(), message.clone())
        }
        JobRunError::Cancelled => ("E_CANCELLED".into(), "cancelled".into()),
    }
}

fn param_str(p: &Value, key: &str) -> Option<String> {
    p.get(key).and_then(|v| v.as_str()).map(str::to_string)
}

fn param_f64(p: &Value, key: &str) -> Option<f64> {
    p.get(key).and_then(|v| v.as_f64())
}

fn param_u32(p: &Value, key: &str) -> Option<u32> {
    p.get(key).and_then(|v| v.as_u64()).map(|n| n as u32)
}

fn param_object(p: &Value, key: &str) -> Option<serde_json::Map<String, Value>> {
    p.get(key)
        .and_then(|v| v.as_object())
        .cloned()
        .filter(|o| !o.is_empty())
}

fn param_string_array(p: &Value, key: &str) -> Option<Vec<String>> {
    p.get(key)
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|v| v.as_str().map(str::to_string))
                .collect()
        })
        .filter(|v: &Vec<String>| !v.is_empty())
}

fn map_db(e: DbError) -> JobRunError {
    JobRunError::Permanent {
        code: "E_DB".into(),
        message: e.to_string(),
    }
}

fn map_io(e: std::io::Error) -> JobRunError {
    JobRunError::Permanent {
        code: "E_IO".into(),
        message: e.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db::{utc_iso8601_now, Job, JobStatus};
    use crate::services::worker_client::HOST_LOOPBACK;
    use std::io::{BufReader, Read, Write};
    use std::net::{Ipv4Addr, TcpListener};
    use std::thread;

    // ---- burn_in_check_window ----------------------------------------------

    #[test]
    fn burn_window_prefers_longest_with_clean_baseline() {
        let cues = [(4.5, 10.0), (0.91, 3.36), (11.0, 12.0)];
        assert_eq!(burn_in_check_window(&cues), Some((4.5, 10.0)));
    }

    #[test]
    fn burn_window_skips_cue_whose_baseline_lands_in_previous_cue() {
        // The longest cue (3.85, 9.40) has its baseline at 3.35s — inside the
        // previous back-to-back cue (0.91, 3.36). It must be rejected in favour
        // of the longest cue with a clean 0.5 s pre-window.
        let cues = [(0.91, 3.36), (3.85, 9.40)];
        assert_eq!(burn_in_check_window(&cues), Some((0.91, 3.36)));
    }

    #[test]
    fn burn_window_rejects_any_cue_overlapping_the_baseline() {
        // Cue B's baseline (3.7 s) is covered by cue A -> B rejected.
        let cues = [(0.6, 4.0), (4.2, 8.0)];
        assert_eq!(burn_in_check_window(&cues), Some((0.6, 4.0)));
        // Cue C's baseline (7.6 s) is covered by cue B -> A still wins.
        let cues = [(0.6, 4.0), (4.2, 8.0), (8.1, 9.0)];
        assert_eq!(burn_in_check_window(&cues), Some((0.6, 4.0)));
    }

    #[test]
    fn burn_window_requires_nonnegative_baseline() {
        assert_eq!(burn_in_check_window(&[(0.2, 5.0)]), None);
        assert_eq!(burn_in_check_window(&[]), None);
    }

    // ---- merge_subtitle_cues -----------------------------------------------

    fn cue(number: i64, start: f64, end: f64, text: &str, status: &str) -> SubtitleCue {
        SubtitleCue {
            id: format!("id-{number}"),
            project_id: "p1".into(),
            cue_number: number,
            start,
            end,
            text: text.into(),
            speaker: None,
            source_text: None,
            status: status.into(),
            style_json: None,
            updated_at: utc_iso8601_now(),
        }
    }

    fn fresh(number: i64, start: f64, end: f64, text: &str) -> CueInput {
        CueInput {
            cue_number: number,
            start,
            end,
            text: text.into(),
            speaker: None,
            source_text: None,
        }
    }

    #[test]
    fn merge_keeps_edited_rows_across_a_re_run() {
        let existing = vec![cue(1, 0.91, 3.56, "Xin chào (đã sửa)", "edited")];
        let fresh = vec![fresh(1, 0.90, 3.55, "Xin chào")];
        let merged = merge_subtitle_cues(&existing, &fresh);
        assert_eq!(merged.len(), 1);
        assert_eq!(merged[0].text, "Xin chào (đã sửa)");
        assert_eq!(merged[0].start, 0.91);
    }

    #[test]
    fn merge_lets_a_new_translation_win_on_unedited_rows() {
        let existing = vec![cue(1, 0.91, 3.56, "Xin chào", "draft")];
        let fresh = vec![fresh(1, 0.90, 3.55, "Hello")];
        let merged = merge_subtitle_cues(&existing, &fresh);
        assert_eq!(merged.len(), 1);
        assert_eq!(merged[0].text, "Hello");
        assert_eq!(merged[0].start, 0.90);
    }

    #[test]
    fn merge_appends_new_cues_and_drops_stale_rows() {
        let existing = vec![
            cue(1, 0.91, 3.56, "A", "draft"),
            cue(2, 4.0, 5.0, "B", "draft"),
        ];
        let fresh = vec![fresh(1, 0.90, 3.55, "A"), fresh(3, 6.0, 7.0, "C")];
        let merged = merge_subtitle_cues(&existing, &fresh);
        assert_eq!(merged.len(), 2);
        assert_eq!(merged[0].text, "A");
        assert_eq!(merged[1].text, "C");
    }

    #[test]
    fn merge_preserves_edited_timing_and_speaker() {
        let mut e = cue(1, 1.0, 2.0, "Text", "approved");
        e.speaker = Some("Narrator".into());
        let merged = merge_subtitle_cues(&[e], &[fresh(1, 1.05, 2.05, "Text")]);
        assert_eq!(merged[0].speaker.as_deref(), Some("Narrator"));
        assert_eq!(merged[0].start, 1.0);
    }

    // ---- test scaffolding -------------------------------------------------

    /// Canned HTTP server: accepts `n` requests, answering each from `routes`
    /// (path → (status, body)). Reads the full request (headers + body) first
    /// so Windows loopback stays clean. ``delay_ms`` stalls every response so
    /// tests can hold a stage in flight (cancel / live-progress behaviour).
    ///
    /// ``fail_first`` answers that many *matched-route* requests with the
    /// canonical error envelope in ``fail_body`` (non-2xx) instead of the
    /// route body — used to exercise stage-level retries. Live-progress polls
    /// (unmatched paths) are never consumed by the budget.
    #[derive(Default)]
    struct CannedServer {
        routes: Vec<(String, u16, String)>,
        delay_ms: u64,
        seen: Arc<std::sync::Mutex<Vec<String>>>,
        fail_first: u32,
        fail_body: String,
    }

    impl CannedServer {
        fn spawn(&self) -> u16 {
            let listener =
                TcpListener::bind((Ipv4Addr::from(HOST_LOOPBACK), 0)).expect("bind test listener");
            let port = listener.local_addr().expect("local addr").port();
            let routes = self.routes.clone();
            let delay_ms = self.delay_ms;
            let seen = self.seen.clone();
            let mut failures_left = self.fail_first;
            let fail_body = self.fail_body.clone();
            thread::spawn(move || {
                // Accept forever: the runner may poll live progress or send a
                // cancel request after the canned routes are consumed, and a
                // client blocked on a dead listener would stall the test. The
                // thread dies with the test process.
                loop {
                    match listener.accept() {
                        Ok((mut stream, _)) => {
                            let mut reader = BufReader::new(stream.try_clone().expect("clone"));
                            let mut drained = Vec::new();
                            let mut chunk = [0u8; 4096];
                            loop {
                                if drained.windows(4).any(|w| w == b"\r\n\r\n") {
                                    break;
                                }
                                match reader.read(&mut chunk) {
                                    Ok(0) => break,
                                    Ok(n) => drained.extend_from_slice(&chunk[..n]),
                                    Err(_) => break,
                                }
                            }
                            let raw = String::from_utf8_lossy(&drained);
                            let request_line = raw.lines().next().unwrap_or_default();
                            let path = request_line
                                .split_whitespace()
                                .nth(1)
                                .unwrap_or_default()
                                .to_string();
                            seen.lock().unwrap().push(request_line.to_string());
                            let header_end = drained
                                .windows(4)
                                .position(|w| w == b"\r\n\r\n")
                                .map(|p| p + 4)
                                .unwrap_or(drained.len());
                            let header_block = String::from_utf8_lossy(&drained[..header_end]);
                            let content_length = header_block.lines().find_map(|line| {
                                let mut parts = line.splitn(2, ':');
                                let name = parts.next()?.trim();
                                if name.eq_ignore_ascii_case("content-length") {
                                    parts.next()?.trim().parse::<usize>().ok()
                                } else {
                                    None
                                }
                            });
                            if let Some(len) = content_length {
                                let missing =
                                    header_end + len - drained.len().min(header_end + len);
                                let mut rest = vec![0u8; missing];
                                if missing > 0 && reader.read_exact(&mut rest).is_err() {
                                    eprintln!("test server: failed to drain request body");
                                }
                            }
                            // Progress polls must answer instantly (they have a
                            // short client read timeout); only stage calls are
                            // delayed so tests can hold a stage in flight.
                            if delay_ms > 0 && !path.starts_with("/v1/progress/") {
                                std::thread::sleep(Duration::from_millis(delay_ms));
                            }
                            let matched = routes.iter().find(|(p, _, _)| p == &path);
                            let (status, body) = match matched {
                            Some(_) if failures_left > 0 => {
                                failures_left -= 1;
                                (
                                    422,
                                    if fail_body.is_empty() {
                                        r#"{"error":{"code":"E_TTS_FAILED","message":"edge-tts: no audio received","recoverable":false}}"#.to_string()
                                    } else {
                                        fail_body.clone()
                                    },
                                )
                            }
                            Some((_, s, b)) => (*s, b.clone()),
                            None => (
                                404,
                                r#"{"error":{"code":"E_NOT_FOUND","message":"no route","recoverable":false}}"#.to_string(),
                            ),
                        };
                            let reason = if status == 200 { "OK" } else { "Error" };
                            let response = format!(
                                "HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                                body.len()
                            );
                            let _ = stream.write_all(response.as_bytes());
                            // Unknown paths (e.g. live-progress polls) answer 404;
                            // matched routes respond with their canned body.
                        }
                        Err(e) => {
                            eprintln!("test server accept failed: {e}");
                            break;
                        }
                    }
                }
            });
            port
        }
    }

    /// Stub `WorkerClientSource` pointing at a canned server.
    struct StubSource {
        port: u16,
    }

    impl WorkerClientSource for StubSource {
        fn worker_client(&self) -> Option<WorkerClient> {
            Some(WorkerClient::new(self.port, "test-token".into()))
        }
    }

    struct Harness {
        projects: Arc<ProjectService>,
        settings: Arc<SettingsService>,
        secrets: Arc<SecretStore>,
        subtitles: Arc<SubtitleService>,
        dictionary: Arc<DictionaryService>,
        providers: Arc<ProviderService>,
        dir: PathBuf,
    }

    fn harness() -> Harness {
        static COUNTER: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
        let n = COUNTER.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        let dir = std::env::temp_dir().join(format!(
            "tooltranslate_pipeline_runner_{}_{}",
            std::process::id(),
            n
        ));
        Harness {
            projects: Arc::new(ProjectService::open(dir.clone())),
            settings: Arc::new(SettingsService::open(dir.clone())),
            secrets: Arc::new(SecretStore::new()),
            subtitles: Arc::new(SubtitleService::open(dir.clone())),
            dictionary: Arc::new(DictionaryService::open(dir.clone())),
            providers: Arc::new(ProviderService::open(dir.clone())),
            dir,
        }
    }

    fn seed_project(h: &Harness, name: &str) -> String {
        h.projects
            .create(name.into(), "C:\\videos\\source.mp4".into())
            .expect("create project")
            .id
    }

    fn job(project_id: &str, job_type: JobType, params: Value) -> Job {
        let now = utc_iso8601_now();
        Job {
            id: "job_0001".into(),
            project_id: project_id.into(),
            job_type,
            status: JobStatus::Queued,
            progress: 0.0,
            stage: "queued".into(),
            error_code: None,
            error_message: None,
            error_log: None,
            params,
            created_at: now.clone(),
            updated_at: now,
            started_at: None,
            finished_at: None,
            retry_count: 0,
            cancel_requested: false,
        }
    }

    fn runner(h: &Harness, port: u16) -> PipelineRunner {
        PipelineRunner::new(
            Arc::new(StubSource { port }),
            h.projects.clone(),
            h.settings.clone(),
            h.secrets.clone(),
            h.subtitles.clone(),
            h.dictionary.clone(),
            h.providers.clone(),
        )
    }

    fn run(job: &Job, runner: &PipelineRunner) -> Result<(), JobRunError> {
        let progress = |_: f64, _: &str| {};
        let log = |_: &str, _: &str| {};
        let cancelled = || false;
        runner.run(
            job,
            &JobRunContext {
                progress: &progress,
                log: &log,
                is_cancelled: &cancelled,
            },
        )
    }

    const TRANSCRIPT_BODY: &str = r#"{"schema_version":1,"project_id":"p","language":"vi","model":"large-v3","segments":[{"id":"seg_0","idx":0,"speaker":null,"start":0.0,"end":1.2,"text":"Xin chào","language":"vi","confidence":0.98,"words":null}]}"#;
    const TRANSLATION_BODY: &str = r#"{"schema_version":1,"target_language":"zh","model":"gemini-flash-lite-latest","blocks":[{"block_idx":0,"translations":[{"idx":0,"segment_id":"seg_0","source_text":"Xin chào","translated_text":"你好","confidence":0.99}]}]}"#;
    const SUBTITLE_BODY: &str = r#"{"cues":[{"cue_number":1,"start":0.0,"end":1.2,"text":"你好"}],"ass_path":"C:\\sub\\subtitle.ass","srt_path":"C:\\sub\\subtitle.srt","warnings":[]}"#;
    const RENDER_BODY: &str = r#"{"output_path":"C:\\out\\out.mp4","encoder_used":"libx264","duration_seconds":1.2,"width":1280,"height":720,"fps":[25,1],"audio_streams":1}"#;

    // ---- tests -----------------------------------------------------------

    #[test]
    fn transcribe_runs_extract_then_stt_and_persists_artifact() {
        let h = harness();
        let pid = seed_project(&h, "transcribe");
        // The worker writes the extracted audio before answering; the runner
        // must only feed a real file to STT.
        let audio = h.dir.join("projects").join(&pid).join(ARTIFACT_AUDIO);
        fs::create_dir_all(audio.parent().expect("cache")).expect("cache dir");
        fs::write(&audio, b"wav-marker").expect("seed audio");
        let server = CannedServer {
            routes: vec![
                (
                    "/v1/audio/extract".to_string(),
                    200,
                    r#"{"output_path":"a.wav","duration_seconds":1.2,"file_size_bytes":100}"#
                        .to_string(),
                ),
                (
                    "/v1/stt/transcribe".to_string(),
                    200,
                    TRANSCRIPT_BODY.to_string(),
                ),
            ],
            delay_ms: 0,
            seen: Arc::new(std::sync::Mutex::new(Vec::new())),
            fail_first: 0,
            fail_body: String::new(),
        };
        let port = server.spawn();
        let runner = runner(&h, port);
        let j = job(
            &pid,
            JobType::Transcribe,
            serde_json::json!({"model": "turbo", "language": "vi"}),
        );
        run(&j, &runner).expect("transcribe succeeds");

        // The transcript artifact was persisted and parses back to the doc.
        let path = h.dir.join("projects").join(&pid).join(ARTIFACT_TRANSCRIPT);
        let transcript: Transcript =
            serde_json::from_slice(&fs::read(path).expect("artifact")).expect("parses");
        assert_eq!(transcript.segments.len(), 1);
        assert_eq!(transcript.segments[0].text, "Xin chào");

        // Both stage calls were made, in order, with the job id.
        let seen = server.seen.lock().unwrap().clone();
        assert_eq!(seen.len(), 2);
        assert!(seen[0].contains("/v1/audio/extract"));
        assert!(seen[1].contains("/v1/stt/transcribe"));
        let _ = std::fs::remove_dir_all(&h.dir);
    }

    #[test]
    fn translate_persists_translation_and_sends_provider() {
        let h = harness();
        let pid = seed_project(&h, "translate");
        // Seed the transcript artifact exactly where the stage expects it.
        let project = h.dir.join("projects").join(&pid);
        fs::create_dir_all(project.join("cache")).expect("cache dir");
        fs::write(project.join(ARTIFACT_TRANSCRIPT), TRANSCRIPT_BODY).expect("seed transcript");

        let server = CannedServer {
            routes: vec![(
                "/v1/translate".to_string(),
                200,
                TRANSLATION_BODY.to_string(),
            )],
            delay_ms: 0,
            seen: Arc::new(std::sync::Mutex::new(Vec::new())),
            fail_first: 0,
            fail_body: String::new(),
        };
        let port = server.spawn();
        let runner = runner(&h, port);
        let j = job(
            &pid,
            JobType::Translate,
            serde_json::json!({"provider": "mock", "target_language": "zh"}),
        );
        run(&j, &runner).expect("translate succeeds");

        let path = h.dir.join("projects").join(&pid).join(ARTIFACT_TRANSLATION);
        let translation: Translation =
            serde_json::from_slice(&fs::read(path).expect("artifact")).expect("parses");
        assert_eq!(
            translation.blocks[0].translations[0].translated_text,
            "你好"
        );
        let _ = std::fs::remove_dir_all(&h.dir);
    }

    #[test]
    fn translate_without_provider_param_resolves_registry_default() {
        // Provider Management: no `provider` param → the capability default
        // (seeded FREE) is resolved from the registry — no hard-coded fallback.
        let h = harness();
        let pid = seed_project(&h, "translate-default");
        let project = h.dir.join("projects").join(&pid);
        fs::create_dir_all(project.join("cache")).expect("cache dir");
        fs::write(project.join(ARTIFACT_TRANSCRIPT), TRANSCRIPT_BODY).expect("seed transcript");

        let server = CannedServer {
            routes: vec![(
                "/v1/translate".to_string(),
                200,
                TRANSLATION_BODY.to_string(),
            )],
            delay_ms: 0,
            seen: Arc::new(std::sync::Mutex::new(Vec::new())),
            fail_first: 0,
            fail_body: String::new(),
        };
        let port = server.spawn();
        let runner = runner(&h, port);
        let j = job(
            &pid,
            JobType::Translate,
            serde_json::json!({"target_language": "zh"}),
        );
        run(&j, &runner).expect("default provider resolves and translates");
        let _ = std::fs::remove_dir_all(&h.dir);
    }

    #[test]
    fn translate_with_disabled_provider_fails_hard() {
        let h = harness();
        let pid = seed_project(&h, "translate-disabled");
        let project = h.dir.join("projects").join(&pid);
        fs::create_dir_all(project.join("cache")).expect("cache dir");
        fs::write(project.join(ARTIFACT_TRANSCRIPT), TRANSCRIPT_BODY).expect("seed transcript");

        // Disable the seeded gemini row, then ask for it explicitly: the
        // runner must refuse instead of silently using another provider.
        h.providers
            .set_enabled("gemini", false)
            .expect("disable gemini");
        let runner = runner(&h, 1);
        let j = job(
            &pid,
            JobType::Translate,
            serde_json::json!({"provider": "gemini", "target_language": "zh"}),
        );
        let err = run(&j, &runner).expect_err("disabled provider must fail");
        match err {
            JobRunError::Permanent { code, message } => {
                assert_eq!(code, "E_DB");
                assert!(
                    message.contains("disabled"),
                    "message should explain the provider is disabled: {message}"
                );
            }
            other => panic!("expected permanent error, got {other:?}"),
        }
        let _ = std::fs::remove_dir_all(&h.dir);
    }

    #[test]
    fn translate_without_prior_transcript_fails_cleanly() {
        let h = harness();
        let pid = seed_project(&h, "missing-artifact");
        let runner = runner(&h, 1);
        let j = job(
            &pid,
            JobType::Translate,
            serde_json::json!({"provider": "mock", "target_language": "zh"}),
        );
        let err = run(&j, &runner).expect_err("missing artifact must fail");
        match err {
            JobRunError::Permanent { code, .. } => assert_eq!(code, "E_ARTIFACT_MISSING"),
            other => panic!("expected E_ARTIFACT_MISSING, got {other:?}"),
        }
        let _ = std::fs::remove_dir_all(&h.dir);
    }

    #[test]
    fn subtitle_syncs_cues_into_editor_table() {
        let h = harness();
        let pid = seed_project(&h, "subtitle");
        let project = h.dir.join("projects").join(&pid);
        fs::create_dir_all(project.join("cache")).expect("cache dir");
        fs::write(project.join(ARTIFACT_TRANSCRIPT), TRANSCRIPT_BODY).expect("seed transcript");
        fs::write(project.join(ARTIFACT_TRANSLATION), TRANSLATION_BODY).expect("seed translation");

        let server = CannedServer {
            routes: vec![("/v1/subtitle".to_string(), 200, SUBTITLE_BODY.to_string())],
            delay_ms: 0,
            seen: Arc::new(std::sync::Mutex::new(Vec::new())),
            fail_first: 0,
            fail_body: String::new(),
        };
        let port = server.spawn();
        let runner = runner(&h, port);
        let j = job(&pid, JobType::Subtitle, serde_json::json!({}));
        run(&j, &runner).expect("subtitle succeeds");

        // Cues landed in the editor's project-scoped table.
        let cues = h.subtitles.list(&pid).expect("cues");
        assert_eq!(cues.len(), 1);
        assert_eq!(cues[0].text, "你好");
        let _ = std::fs::remove_dir_all(&h.dir);
    }

    #[test]
    fn render_requires_subtitle_artifact() {
        let h = harness();
        let pid = seed_project(&h, "render-missing");
        let runner = runner(&h, 1);
        let j = job(&pid, JobType::Render, serde_json::json!({}));
        let err = run(&j, &runner).expect_err("missing subtitle must fail");
        match err {
            JobRunError::Permanent { code, .. } => assert_eq!(code, "E_ARTIFACT_MISSING"),
            other => panic!("expected E_ARTIFACT_MISSING, got {other:?}"),
        }
        let _ = std::fs::remove_dir_all(&h.dir);
    }

    #[test]
    fn render_writes_output_video() {
        let h = harness();
        let pid = seed_project(&h, "render");
        let project = h.dir.join("projects").join(&pid);
        fs::create_dir_all(project.join("cache")).expect("cache dir");
        fs::write(project.join(ARTIFACT_SUBTITLE_ASS), "[Script Info]\n").expect("seed ass");
        // The worker writes the rendered video before answering; the runner
        // must report success only when that file actually lands on disk.
        let output = project.join("output").join("final.mp4");
        fs::create_dir_all(output.parent().expect("output dir")).expect("output dir");
        fs::write(&output, b"mp4-marker").expect("seed output");

        let server = CannedServer {
            routes: vec![("/v1/render".to_string(), 200, RENDER_BODY.to_string())],
            delay_ms: 0,
            seen: Arc::new(std::sync::Mutex::new(Vec::new())),
            fail_first: 0,
            fail_body: String::new(),
        };
        let port = server.spawn();
        let runner = runner(&h, port);
        let j = job(
            &pid,
            JobType::Render,
            serde_json::json!({"output_name": "final"}),
        );
        run(&j, &runner).expect("render succeeds");

        // The render route itself wrote the output on the worker side; the
        // runner computed the same canonical output path and called through.
        let seen = server.seen.lock().unwrap().clone();
        assert!(seen[0].contains("/v1/render"));
        // The subtitle artifact is untouched by the stage.
        let body = fs::read_to_string(project.join(ARTIFACT_SUBTITLE_ASS)).expect("ass kept");
        assert_eq!(body, "[Script Info]\n");
        assert!(output.is_file(), "output video must exist on success");
        let _ = std::fs::remove_dir_all(&h.dir);
    }

    #[test]
    fn render_without_output_file_fails_honestly() {
        // Defense-in-depth on top of the worker's own validation: a 200 that
        // never produced the output file must not be reported as success.
        let h = harness();
        let pid = seed_project(&h, "render-no-file");
        let project = h.dir.join("projects").join(&pid);
        fs::create_dir_all(project.join("cache")).expect("cache dir");
        fs::write(project.join(ARTIFACT_SUBTITLE_ASS), "[Script Info]\n").expect("seed ass");

        let server = CannedServer {
            routes: vec![("/v1/render".to_string(), 200, RENDER_BODY.to_string())],
            delay_ms: 0,
            seen: Arc::new(std::sync::Mutex::new(Vec::new())),
            fail_first: 0,
            fail_body: String::new(),
        };
        let port = server.spawn();
        let runner = runner(&h, port);
        let j = job(
            &pid,
            JobType::Render,
            serde_json::json!({"output_name": "final"}),
        );
        let err = run(&j, &runner).expect_err("missing output file must fail");
        match err {
            JobRunError::Permanent { code, message } => {
                assert_eq!(code, "E_ARTIFACT_MISSING");
                assert!(
                    message.contains("no output file"),
                    "message should explain the missing file: {message}"
                );
            }
            other => panic!("expected E_ARTIFACT_MISSING, got {other:?}"),
        }
        let _ = std::fs::remove_dir_all(&h.dir);
    }

    #[test]
    fn tts_without_cues_fails_cleanly() {
        let h = harness();
        let pid = seed_project(&h, "tts-no-cues");
        let runner = runner(&h, 1);
        let j = job(&pid, JobType::Tts, serde_json::json!({}));
        let err = run(&j, &runner).expect_err("tts without cues must fail");
        match err {
            JobRunError::Permanent { code, .. } => assert_eq!(code, "E_ARTIFACT_MISSING"),
            other => panic!("expected E_ARTIFACT_MISSING, got {other:?}"),
        }
        let _ = std::fs::remove_dir_all(&h.dir);
    }

    #[test]
    fn tts_calls_worker_with_cues() {
        let h = harness();
        let pid = seed_project(&h, "tts");
        h.subtitles
            .replace_project(
                &pid,
                vec![CueInput {
                    cue_number: 1,
                    start: 0.0,
                    end: 2.0,
                    text: "xin chào".into(),
                    speaker: None,
                    source_text: None,
                }],
            )
            .expect("seed cue");

        let server = CannedServer {
            routes: {
                // The worker writes the voice track (its result path, escaped
                // for the JSON body) before answering; the runner must find it.
                let track = h.dir.join("projects").join(&pid).join(ARTIFACT_VOICE_TRACK);
                fs::create_dir_all(track.parent().expect("cache")).expect("cache dir");
                fs::write(&track, b"wave-marker").expect("seed voice track");
                vec![(
                    "/v1/tts/synthesize".to_string(),
                    200,
                    format!(
                        r#"{{"voice_track_path":"{}","meta_path":"C:\\x\\tts_meta.json","cue_count":1,"engine_used":"edge","voice_used":"vi-VN-HoaiMyNeural"}}"#,
                        track.display().to_string().replace('\\', "\\\\")
                    ),
                )]
            },
            delay_ms: 0,
            seen: Arc::new(std::sync::Mutex::new(Vec::new())),
            fail_first: 0,
            fail_body: String::new(),
        };
        let port = server.spawn();
        let runner = runner(&h, port);
        let j = job(
            &pid,
            JobType::Tts,
            serde_json::json!({"target_language": "vi", "engine": "edge"}),
        );
        run(&j, &runner).expect("tts stage succeeds");
        let seen = server.seen.lock().unwrap().clone();
        assert!(seen[0].contains("/v1/tts/synthesize"));
        let _ = std::fs::remove_dir_all(&h.dir);
    }

    #[test]
    fn render_with_voice_requires_voice_track_artifact() {
        let h = harness();
        let pid = seed_project(&h, "render-voice-missing");
        let project = h.dir.join("projects").join(&pid);
        fs::create_dir_all(project.join("cache")).expect("cache dir");
        fs::write(project.join(ARTIFACT_SUBTITLE_ASS), "[Script Info]\n").expect("seed ass");
        let runner = runner(&h, 1);
        let j = job(
            &pid,
            JobType::Render,
            serde_json::json!({"voice_track": "true"}),
        );
        let err = run(&j, &runner).expect_err("missing voice track must fail");
        match err {
            JobRunError::Permanent { code, .. } => assert_eq!(code, "E_ARTIFACT_MISSING"),
            other => panic!("expected E_ARTIFACT_MISSING, got {other:?}"),
        }
        let _ = std::fs::remove_dir_all(&h.dir);
    }

    #[test]
    fn render_with_voice_track_calls_render_with_voice() {
        let h = harness();
        let pid = seed_project(&h, "render-voice");
        let project = h.dir.join("projects").join(&pid);
        fs::create_dir_all(project.join("cache")).expect("cache dir");
        fs::write(project.join(ARTIFACT_SUBTITLE_ASS), "[Script Info]\n").expect("seed ass");
        fs::write(project.join(ARTIFACT_VOICE_TRACK), b"not-a-real-wav").expect("seed voice track");
        let output = project.join("output").join("final.mp4");
        fs::create_dir_all(output.parent().expect("output dir")).expect("output dir");
        fs::write(&output, b"mp4-marker").expect("seed output");

        let server = CannedServer {
            routes: vec![("/v1/render".to_string(), 200, RENDER_BODY.to_string())],
            delay_ms: 0,
            seen: Arc::new(std::sync::Mutex::new(Vec::new())),
            fail_first: 0,
            fail_body: String::new(),
        };
        let port = server.spawn();
        let runner = runner(&h, port);
        let j = job(
            &pid,
            JobType::Render,
            serde_json::json!({"voice_track": "true", "output_name": "final"}),
        );
        run(&j, &runner).expect("dubbed render succeeds");
        let seen = server.seen.lock().unwrap().clone();
        assert!(seen[0].contains("/v1/render"));
        let _ = std::fs::remove_dir_all(&h.dir);
    }

    #[test]
    fn cancelled_before_run_stops_immediately() {
        let h = harness();
        let pid = seed_project(&h, "cancelled");
        let runner = runner(&h, 1);
        let progress = |_: f64, _: &str| {};
        let log = |_: &str, _: &str| {};
        let cancelled = || true;
        let j = job(&pid, JobType::Transcribe, serde_json::json!({}));
        let outcome = runner.run(
            &j,
            &JobRunContext {
                progress: &progress,
                log: &log,
                is_cancelled: &cancelled,
            },
        );
        assert!(matches!(outcome, Err(JobRunError::Cancelled)));
        let _ = std::fs::remove_dir_all(&h.dir);
    }

    #[test]
    fn worker_error_envelope_maps_to_job_error() {
        let h = harness();
        let pid = seed_project(&h, "worker-error");
        let server = CannedServer {
            routes: vec![(
                "/v1/audio/extract".to_string(),
                422,
                r#"{"error":{"code":"E_FFMPEG_NOT_FOUND","message":"ffmpeg missing","recoverable":true}}"#.to_string(),
            )],
            delay_ms: 0,
            seen: Arc::new(std::sync::Mutex::new(Vec::new())),
            fail_first: 0,
            fail_body: String::new(),
        };
        let port = server.spawn();
        let runner = runner(&h, port);
        let j = job(&pid, JobType::Transcribe, serde_json::json!({}));
        let err = run(&j, &runner).expect_err("worker error must fail");
        match err {
            JobRunError::Transient { code, message } => {
                assert_eq!(code, "E_FFMPEG_NOT_FOUND");
                assert!(message.contains("ffmpeg"));
            }
            other => panic!("expected Transient E_FFMPEG_NOT_FOUND, got {other:?}"),
        }
        let _ = std::fs::remove_dir_all(&h.dir);
    }

    #[test]
    fn no_worker_client_is_transient() {
        let h = harness();
        let pid = seed_project(&h, "no-worker");
        struct NoneSource;
        impl WorkerClientSource for NoneSource {
            fn worker_client(&self) -> Option<WorkerClient> {
                None
            }
        }
        let runner = PipelineRunner::new(
            Arc::new(NoneSource),
            h.projects.clone(),
            h.settings.clone(),
            h.secrets.clone(),
            h.subtitles.clone(),
            h.dictionary.clone(),
            h.providers.clone(),
        );
        let j = job(&pid, JobType::Transcribe, serde_json::json!({}));
        let err = run(&j, &runner).expect_err("no client must fail");
        match err {
            JobRunError::Transient { code, .. } => assert_eq!(code, "E_WORKER_NOT_READY"),
            other => panic!("expected E_WORKER_NOT_READY, got {other:?}"),
        }
        let _ = std::fs::remove_dir_all(&h.dir);
    }

    #[test]
    fn cancel_mid_stage_propagates_to_worker_and_returns_cancelled() {
        let h = harness();
        let pid = seed_project(&h, "cancel-mid-stage");
        // The stage holds the connection open well past the poll interval so
        // the runner observes the cancel flag mid-flight.
        let server = CannedServer {
            routes: vec![(
                "/v1/audio/extract".to_string(),
                200,
                r#"{"output_path":"a.wav","duration_seconds":1.2,"file_size_bytes":100}"#
                    .to_string(),
            )],
            delay_ms: 900,
            seen: Arc::new(std::sync::Mutex::new(Vec::new())),
            fail_first: 0,
            fail_body: String::new(),
        };
        let port = server.spawn();
        let runner = runner(&h, port);
        let j = job(&pid, JobType::Transcribe, serde_json::json!({}));

        let cancelled = Arc::new(std::sync::atomic::AtomicBool::new(false));
        let flag = {
            let cancelled = cancelled.clone();
            std::thread::spawn(move || {
                std::thread::sleep(Duration::from_millis(150));
                cancelled.store(true, std::sync::atomic::Ordering::SeqCst);
            })
        };
        let progress = |_: f64, _: &str| {};
        let log = |_: &str, _: &str| {};
        let cancelled_flag = cancelled.clone();
        let outcome = runner.run(
            &j,
            &JobRunContext {
                progress: &progress,
                log: &log,
                is_cancelled: &|| cancelled_flag.load(std::sync::atomic::Ordering::SeqCst),
            },
        );
        flag.join().expect("flag thread");

        assert!(matches!(outcome, Err(JobRunError::Cancelled)));
        // Cancellation reached the worker's cancel endpoint (the poll loop
        // called it while the stage was in flight).
        let seen = server.seen.lock().unwrap().clone();
        assert!(
            seen.iter().any(|l| l.contains("/v1/jobs/job_0001/cancel")),
            "worker cancel endpoint was not called: {seen:?}"
        );
        // The next stage never ran.
        assert!(!h
            .dir
            .join("projects")
            .join(&pid)
            .join(ARTIFACT_TRANSCRIPT)
            .exists());
        let _ = std::fs::remove_dir_all(&h.dir);
    }

    #[test]
    fn live_stage_progress_maps_into_job_window() {
        let h = harness();
        let pid = seed_project(&h, "live-progress");
        let audio = h.dir.join("projects").join(&pid).join(ARTIFACT_AUDIO);
        fs::create_dir_all(audio.parent().expect("cache")).expect("cache dir");
        fs::write(&audio, b"wav-marker").expect("seed audio");
        // A delayed stage plus a progress route: the runner polls the worker's
        // progress registry and maps it into the extract window (2%..15%).
        let server = CannedServer {
            routes: vec![
                (
                    "/v1/audio/extract".to_string(),
                    200,
                    r#"{"output_path":"a.wav","duration_seconds":1.2,"file_size_bytes":100}"#
                        .to_string(),
                ),
                (
                    "/v1/progress/job_0001".to_string(),
                    200,
                    r#"{"job_id":"job_0001","progress":0.5,"stage":"extract-audio"}"#.to_string(),
                ),
                (
                    "/v1/stt/transcribe".to_string(),
                    200,
                    TRANSCRIPT_BODY.to_string(),
                ),
            ],
            delay_ms: 900,
            seen: Arc::new(std::sync::Mutex::new(Vec::new())),
            fail_first: 0,
            fail_body: String::new(),
        };
        let port = server.spawn();
        let runner = runner(&h, port);
        let j = job(&pid, JobType::Transcribe, serde_json::json!({}));

        let reported: Arc<std::sync::Mutex<Vec<(f64, String)>>> = Arc::default();
        let progress = {
            let reported = reported.clone();
            move |p: f64, stage: &str| reported.lock().unwrap().push((p, stage.to_string()))
        };
        let logs: Arc<std::sync::Mutex<Vec<(String, String)>>> = Arc::default();
        let log = {
            let logs = logs.clone();
            move |level: &str, message: &str| {
                logs.lock()
                    .unwrap()
                    .push((level.to_string(), message.to_string()))
            }
        };
        let outcome = runner.run(
            &j,
            &JobRunContext {
                progress: &progress,
                log: &log,
                is_cancelled: &|| false,
            },
        );
        assert!(outcome.is_ok(), "{outcome:?}");
        // Live-log lines: the stage-start line is always emitted, and any
        // detail message the worker's progress registry reports is forwarded
        // exactly once per change.
        let logs = logs.lock().unwrap();
        assert!(
            logs.iter().any(|(_, m)| m.contains("Extracting audio")),
            "stage-start log line missing: {logs:?}"
        );

        // The worker's 0.5 progress landed inside the extract window (0.02..0.15),
        // i.e. ~0.085 — proving live progress reached the job.
        let values = reported.lock().unwrap().clone();
        let live = values
            .iter()
            .any(|(p, stage)| *p > 0.02 && *p < 0.15 && stage == "extract-audio");
        assert!(live, "live extract progress not mapped: {values:?}");
        let _ = std::fs::remove_dir_all(&h.dir);
    }

    // ---- stage-level retry (transient network blips in TTS / translation) ----

    /// Seed the transcript artifact so the translate stage can run.
    fn seed_transcript(h: &Harness, pid: &str) {
        let project = h.dir.join("projects").join(pid);
        fs::create_dir_all(project.join("cache")).expect("cache dir");
        fs::write(project.join(ARTIFACT_TRANSCRIPT), TRANSCRIPT_BODY).expect("seed transcript");
    }

    #[test]
    fn translate_retries_network_blip_then_succeeds() {
        // A provider HTTP failure (E_API_ERROR, permanent by the envelope but
        // a network blip in practice) must be retried at the stage level — the
        // first attempt answers the error, the second succeeds.
        let h = harness();
        let pid = seed_project(&h, "translate-retry");
        seed_transcript(&h, &pid);

        let server = CannedServer {
            routes: vec![("/v1/translate".to_string(), 200, TRANSLATION_BODY.to_string())],
            delay_ms: 0,
            seen: Arc::new(std::sync::Mutex::new(Vec::new())),
            fail_first: 1,
            fail_body: r#"{"error":{"code":"E_API_ERROR","message":"Gemini request failed (HTTP 500).","recoverable":false}}"#.to_string(),
        };
        let port = server.spawn();
        let runner = runner(&h, port);
        let j = job(
            &pid,
            JobType::Translate,
            serde_json::json!({"provider": "mock", "target_language": "zh"}),
        );
        run(&j, &runner).expect("translate succeeds after one retry");

        // The stage was attempted twice; the artifact is from the successful one.
        let seen = server.seen.lock().unwrap().clone();
        assert_eq!(
            seen.iter().filter(|l| l.contains("/v1/translate")).count(),
            2,
            "expected two translate attempts: {seen:?}"
        );
        let path = h.dir.join("projects").join(&pid).join(ARTIFACT_TRANSLATION);
        let translation: Translation =
            serde_json::from_slice(&fs::read(path).expect("artifact")).expect("parses");
        assert_eq!(
            translation.blocks[0].translations[0].translated_text,
            "你好"
        );
        let _ = std::fs::remove_dir_all(&h.dir);
    }

    #[test]
    fn tts_retries_edge_blip_then_succeeds() {
        // edge-tts intermittently answers "no audio received" (E_TTS_FAILED,
        // permanent by the envelope). The stage-level retry must rerun the
        // whole synthesis call after the backoff instead of failing the job.
        let h = harness();
        let pid = seed_project(&h, "tts-retry");
        h.subtitles
            .replace_project(
                &pid,
                vec![CueInput {
                    cue_number: 1,
                    start: 0.0,
                    end: 2.0,
                    text: "xin chào".into(),
                    speaker: None,
                    source_text: None,
                }],
            )
            .expect("seed cue");

        let server = CannedServer {
            routes: {
                let track = h.dir.join("projects").join(&pid).join(ARTIFACT_VOICE_TRACK);
                fs::create_dir_all(track.parent().expect("cache")).expect("cache dir");
                fs::write(&track, b"wave-marker").expect("seed voice track");
                vec![(
                    "/v1/tts/synthesize".to_string(),
                    200,
                    format!(
                        r#"{{"voice_track_path":"{}","meta_path":"C:\\x\\tts_meta.json","cue_count":1,"engine_used":"edge","voice_used":"vi-VN-HoaiMyNeural"}}"#,
                        track.display().to_string().replace('\\', "\\\\")
                    ),
                )]
            },
            delay_ms: 0,
            seen: Arc::new(std::sync::Mutex::new(Vec::new())),
            fail_first: 1,
            fail_body: r#"{"error":{"code":"E_TTS_FAILED","message":"edge-tts synthesis failed: no audio was received","recoverable":false}}"#.to_string(),
        };
        let port = server.spawn();
        let runner = runner(&h, port);
        let j = job(
            &pid,
            JobType::Tts,
            serde_json::json!({"target_language": "vi", "engine": "edge"}),
        );
        run(&j, &runner).expect("tts succeeds after one retry");

        let seen = server.seen.lock().unwrap().clone();
        assert_eq!(
            seen.iter()
                .filter(|l| l.contains("/v1/tts/synthesize"))
                .count(),
            2,
            "expected two tts attempts: {seen:?}"
        );
        let _ = std::fs::remove_dir_all(&h.dir);
    }

    #[test]
    fn translate_does_not_retry_auth_failure() {
        // E_API_AUTH is a real (permanent) failure — a wrong API key will not
        // become valid by retrying. The stage must fail after a single attempt
        // and the job service reports the permanent error immediately.
        let h = harness();
        let pid = seed_project(&h, "translate-auth");
        seed_transcript(&h, &pid);

        let server = CannedServer {
            routes: vec![(
                "/v1/translate".to_string(),
                401,
                r#"{"error":{"code":"E_API_AUTH","message":"Gemini authentication failed (invalid API key).","recoverable":false}}"#.to_string(),
            )],
            delay_ms: 0,
            seen: Arc::new(std::sync::Mutex::new(Vec::new())),
            fail_first: 0,
            fail_body: String::new(),
        };
        let port = server.spawn();
        let runner = runner(&h, port);
        let j = job(
            &pid,
            JobType::Translate,
            serde_json::json!({"provider": "mock", "target_language": "zh"}),
        );
        let err = run(&j, &runner).expect_err("auth failure must fail");
        match err {
            JobRunError::Permanent { code, .. } => assert_eq!(code, "E_API_AUTH"),
            other => panic!("expected E_API_AUTH, got {other:?}"),
        }
        let seen = server.seen.lock().unwrap().clone();
        assert_eq!(
            seen.iter().filter(|l| l.contains("/v1/translate")).count(),
            1,
            "auth failure must not be retried: {seen:?}"
        );
        let _ = std::fs::remove_dir_all(&h.dir);
    }
}
