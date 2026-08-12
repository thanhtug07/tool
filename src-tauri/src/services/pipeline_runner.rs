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
use std::time::Duration;

use serde_json::Value;

use crate::db::{DbError, Job, JobType};
use crate::security::secret_store::SecretStore;
use crate::services::dictionary_service::DictionaryService;
use crate::services::job_service::{JobRunContext, JobRunError, JobRunner};
use crate::services::project_service::ProjectService;
use crate::services::provider_service::ProviderService;
use crate::services::settings_service::SettingsService;
use crate::services::subtitle_service::{CueInput, SubtitleService};
use crate::services::worker_client::{
    ExtractAudioRequest, HttpError, RenderRequest, SubtitleRequest, TranscribeRequest, Transcript,
    TranslateRequest, Translation, TtsCue, TtsRequest, WorkerClient,
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

pub const DEFAULT_RENDER_NAME: &str = "rendered";

/// How long to wait for the worker to abort a stage after cancellation is
/// requested (the worker kills process trees before answering).
const CANCEL_WORKER_ABORT_TIMEOUT: Duration = Duration::from_secs(10);
/// Live-progress poll interval while a stage call is in flight.
const PROGRESS_POLL_INTERVAL: Duration = Duration::from_millis(500);
/// Minimum progress delta before a live update is persisted/emitted.
const PROGRESS_MIN_DELTA: f64 = 0.01;

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
                    }
                }
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
        let extract = self.run_stage(&client, job, ctx, (0.02, 0.15), {
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
        })?;
        if (ctx.is_cancelled)() {
            return Err(JobRunError::Cancelled);
        }

        // The extraction measured the source duration — feed it to STT so its
        // per-segment progress maps to 0..1 instead of stalling at the anchor.
        let total_duration = total_duration.or(extract.duration_seconds);

        (ctx.log)("info", "Transcribing audio…");
        (ctx.progress)(0.15, "transcribe");
        let transcript = self.run_stage(&client, job, ctx, (0.15, 1.0), {
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
        })?;
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
        let transcript: Transcript =
            self.read_json(&job.project_id, ARTIFACT_TRANSCRIPT, "transcribe")?;
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
        let model = param_str(p, "model")
            .filter(|v| !v.trim().is_empty())
            .or(resolved.model.clone())
            .unwrap_or_else(|| "gemini-2.5-flash-lite".to_string());

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
                transcript.segments.len()
            ),
        );
        (ctx.progress)(0.1, "translate");
        let translation: Translation = self.run_stage(&client, job, ctx, (0.1, 1.0), move |c| {
            c.translate(TranslateRequest {
                transcript,
                project_id: project_id.clone(),
                provider: provider.clone(),
                target_language: target_language.clone(),
                model: model.clone(),
                glossary_ver: Some(glossary_ver.clone()),
                glossary,
                characters,
                rules,
                api_key,
                provider_config,
                job_id: Some(job_id.clone()),
            })
        })?;
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
        let response = self.run_stage(&client, job, ctx, (0.1, 1.0), move |c| {
            c.generate_subtitles(SubtitleRequest {
                transcript,
                translation,
                project_id: project_id.clone(),
                output_dir: output_dir.clone(),
                language: language.clone(),
                job_id: Some(job_id.clone()),
            })
        })?;
        if (ctx.is_cancelled)() {
            return Err(JobRunError::Cancelled);
        }

        // Sync the generated cues into the editor's project-scoped cue table so
        // the subtitle editor can edit them (TASK-025).
        let cues: Vec<CueInput> = response
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
        self.subtitles
            .replace_project(&job.project_id, cues)
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
        // before tts). Only well-formed, non-empty cues are spoken.
        let stored = self.subtitles.list(&job.project_id).map_err(map_db)?;
        let cues: Vec<TtsCue> = stored
            .iter()
            .filter(|c| c.end > c.start && !c.text.trim().is_empty())
            .map(|c| TtsCue {
                start: c.start,
                end: c.end,
                text: c.text.clone(),
            })
            .collect();
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
        self.run_stage(&client, job, ctx, (0.1, 1.0), move |c| {
            c.tts_synthesize(TtsRequest {
                cues,
                voice: voice.clone(),
                engine: Some(engine.clone()),
                language: Some(target_language.clone()),
                duration_seconds,
                output_dir: output_dir.clone(),
                job_id: Some(job_id.clone()),
            })
        })?;
        if (ctx.is_cancelled)() {
            return Err(JobRunError::Cancelled);
        }
        (ctx.log)(
            "success",
            &format!("Voice track ready — {cue_count} cues, {engine_label}"),
        );
        (ctx.progress)(1.0, "done");
        Ok(())
    }

    fn run_render(&self, job: &Job, ctx: &JobRunContext<'_>) -> Result<(), JobRunError> {
        let p = &job.params;
        let project_dir = self.project_dir(&job.project_id)?;
        let video_path = self.source_video(job)?;
        let paths = artifact_paths(&project_dir);
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

        // Burn-in validation window: the longest displayed cue (the most
        // reliable subtitle-presence sample). Skipped when subtitles are not
        // burned or no cues were generated yet.
        let check_window = if burn_subtitles {
            let cues = self.subtitles.list(&job.project_id).map_err(map_db)?;
            cues.iter()
                .filter(|c| c.end > c.start && !c.text.trim().is_empty())
                .max_by(|a, b| (a.end - a.start).total_cmp(&(b.end - b.start)))
                .map(|c| (c.start, c.end))
        } else {
            None
        };

        let client = self.client()?;
        let job_id = job.id.clone();
        (ctx.log)("info", "Rendering the final video…");
        (ctx.progress)(0.1, "render");
        let output_path_str = output_path.display().to_string();
        let encoder = param_str(p, "encoder");
        let preset = param_str(p, "preset");
        let crf = param_u32(p, "crf");
        let watermark = p.get("watermark").cloned().filter(|v| v.is_object());
        let _response = self.run_stage(&client, job, ctx, (0.1, 1.0), move |c| {
            c.render(RenderRequest {
                video_path,
                subtitle_path,
                output_path: output_path_str.clone(),
                encoder: encoder.clone(),
                preset: preset.clone(),
                crf,
                watermark: watermark.clone(),
                voice_track_path: voice_track_path.clone(),
                check_window,
                job_id: Some(job_id.clone()),
            })
        })?;
        if (ctx.is_cancelled)() {
            return Err(JobRunError::Cancelled);
        }
        (ctx.log)(
            "success",
            &format!("Render complete — {} ready", output_path.display()),
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
        }
    }
}

// ---- small helpers ---------------------------------------------------------

fn permanent(code: &str, message: impl Into<String>) -> JobRunError {
    JobRunError::Permanent {
        code: code.to_string(),
        message: message.into(),
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

    // ---- test scaffolding -------------------------------------------------

    /// Canned HTTP server: accepts `n` requests, answering each from `routes`
    /// (path → (status, body)). Reads the full request (headers + body) first
    /// so Windows loopback stays clean. ``delay_ms`` stalls every response so
    /// tests can hold a stage in flight (cancel / live-progress behaviour).
    #[derive(Default)]
    struct CannedServer {
        routes: &'static [(&'static str, u16, &'static str)],
        delay_ms: u64,
        seen: Arc<std::sync::Mutex<Vec<String>>>,
    }

    impl CannedServer {
        fn spawn(&self) -> u16 {
            let listener =
                TcpListener::bind((Ipv4Addr::from(HOST_LOOPBACK), 0)).expect("bind test listener");
            let port = listener.local_addr().expect("local addr").port();
            let routes = self.routes;
            let delay_ms = self.delay_ms;
            let seen = self.seen.clone();
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
                            let (status, body) = matched
                                .map(|(_, s, b)| (*s, *b))
                                .unwrap_or((404, r#"{"error":{"code":"E_NOT_FOUND","message":"no route","recoverable":false}}"#));
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
    const TRANSLATION_BODY: &str = r#"{"schema_version":1,"target_language":"zh","model":"gemini-2.5-flash-lite","blocks":[{"block_idx":0,"translations":[{"idx":0,"segment_id":"seg_0","source_text":"Xin chào","translated_text":"你好","confidence":0.99}]}]}"#;
    const SUBTITLE_BODY: &str = r#"{"cues":[{"cue_number":1,"start":0.0,"end":1.2,"text":"你好"}],"ass_path":"C:\\sub\\subtitle.ass","srt_path":"C:\\sub\\subtitle.srt","warnings":[]}"#;
    const RENDER_BODY: &str = r#"{"output_path":"C:\\out\\out.mp4","encoder_used":"libx264","duration_seconds":1.2,"width":1280,"height":720,"fps":[25,1],"audio_streams":1}"#;

    // ---- tests -----------------------------------------------------------

    #[test]
    fn transcribe_runs_extract_then_stt_and_persists_artifact() {
        let h = harness();
        let pid = seed_project(&h, "transcribe");
        let server = CannedServer {
            routes: &[
                (
                    "/v1/audio/extract",
                    200,
                    r#"{"output_path":"a.wav","duration_seconds":1.2,"file_size_bytes":100}"#,
                ),
                ("/v1/stt/transcribe", 200, TRANSCRIPT_BODY),
            ],
            delay_ms: 0,
            seen: Arc::new(std::sync::Mutex::new(Vec::new())),
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
            routes: &[("/v1/translate", 200, TRANSLATION_BODY)],
            delay_ms: 0,
            seen: Arc::new(std::sync::Mutex::new(Vec::new())),
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
            routes: &[("/v1/translate", 200, TRANSLATION_BODY)],
            delay_ms: 0,
            seen: Arc::new(std::sync::Mutex::new(Vec::new())),
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
            routes: &[("/v1/subtitle", 200, SUBTITLE_BODY)],
            delay_ms: 0,
            seen: Arc::new(std::sync::Mutex::new(Vec::new())),
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

        let server = CannedServer {
            routes: &[("/v1/render", 200, RENDER_BODY)],
            delay_ms: 0,
            seen: Arc::new(std::sync::Mutex::new(Vec::new())),
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
            routes: &[(
                "/v1/tts/synthesize",
                200,
                r#"{"voice_track_path":"C:\\x\\voice_track.wav","meta_path":"C:\\x\\tts_meta.json","cue_count":1,"engine_used":"edge","voice_used":"vi-VN-HoaiMyNeural"}"#,
            )],
            delay_ms: 0,
            seen: Arc::new(std::sync::Mutex::new(Vec::new())),
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

        let server = CannedServer {
            routes: &[("/v1/render", 200, RENDER_BODY)],
            delay_ms: 0,
            seen: Arc::new(std::sync::Mutex::new(Vec::new())),
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
            routes: &[(
                "/v1/audio/extract",
                422,
                r#"{"error":{"code":"E_FFMPEG_NOT_FOUND","message":"ffmpeg missing","recoverable":true}}"#,
            )],
            delay_ms: 0,
            seen: Arc::new(std::sync::Mutex::new(Vec::new())),
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
            routes: &[(
                "/v1/audio/extract",
                200,
                r#"{"output_path":"a.wav","duration_seconds":1.2,"file_size_bytes":100}"#,
            )],
            delay_ms: 900,
            seen: Arc::new(std::sync::Mutex::new(Vec::new())),
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
        // A delayed stage plus a progress route: the runner polls the worker's
        // progress registry and maps it into the extract window (2%..15%).
        let server = CannedServer {
            routes: &[
                (
                    "/v1/audio/extract",
                    200,
                    r#"{"output_path":"a.wav","duration_seconds":1.2,"file_size_bytes":100}"#,
                ),
                (
                    "/v1/progress/job_0001",
                    200,
                    r#"{"job_id":"job_0001","progress":0.5,"stage":"extract-audio"}"#,
                ),
                ("/v1/stt/transcribe", 200, TRANSCRIPT_BODY),
            ],
            delay_ms: 900,
            seen: Arc::new(std::sync::Mutex::new(Vec::new())),
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
}
