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
use std::path::PathBuf;
use std::sync::Arc;

use serde_json::Value;

use crate::db::{DbError, Job, JobType};
use crate::security::secret_store::SecretStore;
use crate::services::dictionary_service::DictionaryService;
use crate::services::job_service::{JobRunContext, JobRunError, JobRunner};
use crate::services::project_service::ProjectService;
use crate::services::settings_service::SettingsService;
use crate::services::subtitle_service::{CueInput, SubtitleService};
use crate::services::worker_client::{
    ExtractAudioRequest, HttpError, RenderRequest, SubtitleRequest, TranscribeRequest, Transcript,
    TranslateRequest, Translation, WorkerClient,
};
use crate::services::worker_manager::WorkerManager;

// Canonical artifact names under the project directory.
const ARTIFACT_AUDIO: &str = "cache/audio.wav";
const ARTIFACT_TRANSCRIPT: &str = "cache/transcript.json";
const ARTIFACT_TRANSLATION: &str = "cache/translation.json";
const ARTIFACT_SUBTITLE_ASS: &str = "cache/subtitle.ass";

const DEFAULT_RENDER_NAME: &str = "rendered";
/// Providers that never need a credential (worker registry: mock/local).
const KEYLESS_PROVIDERS: &[&str] = &["mock", "local"];

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
}

impl PipelineRunner {
    pub fn new(
        workers: Arc<dyn WorkerClientSource>,
        projects: Arc<ProjectService>,
        settings: Arc<SettingsService>,
        secrets: Arc<SecretStore>,
        subtitles: Arc<SubtitleService>,
        dictionary: Arc<DictionaryService>,
    ) -> Self {
        Self {
            workers,
            projects,
            settings,
            secrets,
            subtitles,
            dictionary,
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
        let audio_path = project_dir.join(ARTIFACT_AUDIO);

        (ctx.progress)(0.02, "extract-audio");
        let extract = client.extract_audio(ExtractAudioRequest {
            video_path: video_path.clone(),
            output_path: audio_path.display().to_string(),
            job_id: Some(job.id.clone()),
        });
        if (ctx.is_cancelled)() {
            return Err(JobRunError::Cancelled);
        }
        extract.map_err(Self::map_http)?;

        (ctx.progress)(0.15, "transcribe");
        let transcript = client.transcribe(TranscribeRequest {
            audio_path: audio_path.display().to_string(),
            project_id: job.project_id.clone(),
            model,
            device,
            language,
            total_duration_seconds: total_duration,
            job_id: Some(job.id.clone()),
        });
        if (ctx.is_cancelled)() {
            return Err(JobRunError::Cancelled);
        }
        let transcript = transcript.map_err(Self::map_http)?;

        self.write_artifact(&job.project_id, ARTIFACT_TRANSCRIPT, &transcript)?;
        (ctx.progress)(1.0, "done");
        Ok(())
    }

    fn run_translate(&self, job: &Job, ctx: &JobRunContext<'_>) -> Result<(), JobRunError> {
        let p = &job.params;
        let transcript: Transcript =
            self.read_json(&job.project_id, ARTIFACT_TRANSCRIPT, "transcribe")?;
        let provider = param_str(p, "provider")
            .filter(|v| !v.trim().is_empty())
            .unwrap_or_else(|| "mock".to_string());
        let target_language = param_str(p, "target_language").ok_or_else(|| {
            permanent(
                "E_PARAMS_INVALID",
                "translate job is missing `target_language`",
            )
        })?;
        let model = if provider == "gemini" {
            self.setting_or(p, "model", "api.gemini.model", "gemini-2.5-flash-lite")?
        } else {
            param_str(p, "model")
                .filter(|v| !v.trim().is_empty())
                .unwrap_or_else(|| "gemini-2.5-flash-lite".to_string())
        };

        // Secrets come exclusively from the OS credential vault — never from
        // params, files, or logs.
        let api_key = if KEYLESS_PROVIDERS.contains(&provider.as_str()) {
            None
        } else {
            match self.secrets.get_api_key(&provider) {
                Ok(Some(key)) => Some(key),
                Ok(None) => {
                    return Err(permanent(
                        "E_API_KEY_MISSING",
                        format!(
                            "no API key stored for provider `{provider}` — add one in Settings → API keys"
                        ),
                    ))
                }
                Err(e) => {
                    return Err(permanent(
                        "E_API_KEY_MISSING",
                        format!("cannot read the stored API key for `{provider}`: {e}"),
                    ))
                }
            }
        };

        // Provider-specific non-secret config from Settings.
        let mut provider_config = serde_json::Map::new();
        let base_url_key = if provider == "local" {
            "api.local.base_url"
        } else {
            "api.gemini.base_url"
        };
        if let Ok(Value::String(base)) = self.settings.get(base_url_key) {
            if !base.trim().is_empty() {
                provider_config.insert("base_url".into(), Value::String(base));
            }
        }

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
        (ctx.progress)(0.1, "translate");
        let translation = client.translate(TranslateRequest {
            transcript,
            project_id: job.project_id.clone(),
            provider,
            target_language,
            model,
            glossary_ver: Some(glossary_ver),
            glossary,
            characters: param_object(p, "characters"),
            rules: param_string_array(p, "rules"),
            api_key,
            provider_config: if provider_config.is_empty() {
                None
            } else {
                Some(provider_config)
            },
            job_id: Some(job.id.clone()),
        });
        if (ctx.is_cancelled)() {
            return Err(JobRunError::Cancelled);
        }
        let translation: Translation = translation.map_err(Self::map_http)?;

        self.write_artifact(&job.project_id, ARTIFACT_TRANSLATION, &translation)?;
        (ctx.progress)(1.0, "done");
        Ok(())
    }

    fn run_subtitle(&self, job: &Job, ctx: &JobRunContext<'_>) -> Result<(), JobRunError> {
        let transcript: Transcript =
            self.read_json(&job.project_id, ARTIFACT_TRANSCRIPT, "transcribe")?;
        let translation: Translation =
            self.read_json(&job.project_id, ARTIFACT_TRANSLATION, "translate")?;
        let output_dir = self.project_dir(&job.project_id)?.join("cache");
        let language = param_str(&job.params, "language")
            .filter(|v| !v.trim().is_empty())
            .or(Some(transcript.language.clone()));

        let client = self.client()?;
        (ctx.progress)(0.1, "subtitle");
        let response = client.generate_subtitles(SubtitleRequest {
            transcript,
            translation,
            project_id: job.project_id.clone(),
            output_dir: output_dir.display().to_string(),
            language,
            job_id: Some(job.id.clone()),
        });
        if (ctx.is_cancelled)() {
            return Err(JobRunError::Cancelled);
        }
        let response = response.map_err(Self::map_http)?;

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

        (ctx.progress)(1.0, "done");
        Ok(())
    }

    fn run_render(&self, job: &Job, ctx: &JobRunContext<'_>) -> Result<(), JobRunError> {
        let p = &job.params;
        let project_dir = self.project_dir(&job.project_id)?;
        let video_path = self.source_video(job)?;
        let subtitle_path = project_dir.join(ARTIFACT_SUBTITLE_ASS);
        if !subtitle_path.is_file() {
            return Err(permanent(
                "E_ARTIFACT_MISSING",
                "missing `cache/subtitle.ass` — run the subtitle stage before rendering",
            ));
        }
        let name = param_str(p, "output_name")
            .filter(|v| !v.trim().is_empty())
            .unwrap_or_else(|| DEFAULT_RENDER_NAME.to_string());
        let output_path = project_dir.join("output").join(format!("{name}.mp4"));
        if let Some(parent) = output_path.parent() {
            fs::create_dir_all(parent).map_err(map_io)?;
        }

        let client = self.client()?;
        (ctx.progress)(0.1, "render");
        let response = client.render(RenderRequest {
            video_path,
            subtitle_path: Some(subtitle_path.display().to_string()),
            output_path: output_path.display().to_string(),
            encoder: param_str(p, "encoder"),
            preset: param_str(p, "preset"),
            crf: param_u32(p, "crf"),
            watermark: p.get("watermark").cloned().filter(|v| v.is_object()),
            check_window: None,
            job_id: Some(job.id.clone()),
        });
        if (ctx.is_cancelled)() {
            return Err(JobRunError::Cancelled);
        }
        let _response = response.map_err(Self::map_http)?;
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
    /// so Windows loopback stays clean.
    struct CannedServer {
        routes: &'static [(&'static str, u16, &'static str)],
        seen: Arc<std::sync::Mutex<Vec<String>>>,
    }

    impl CannedServer {
        fn spawn(&self) -> u16 {
            let listener =
                TcpListener::bind((Ipv4Addr::from(HOST_LOOPBACK), 0)).expect("bind test listener");
            let port = listener.local_addr().expect("local addr").port();
            let routes = self.routes;
            let seen = self.seen.clone();
            thread::spawn(move || {
                let mut remaining = routes.len();
                while remaining > 0 {
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
                            let (status, body) = routes
                                .iter()
                                .find(|(p, _, _)| p == &path)
                                .map(|(_, s, b)| (*s, *b))
                                .unwrap_or((404, r#"{"error":{"code":"E_NOT_FOUND","message":"no route","recoverable":false}}"#));
                            let reason = if status == 200 { "OK" } else { "Error" };
                            let response = format!(
                                "HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                                body.len()
                            );
                            let _ = stream.write_all(response.as_bytes());
                            remaining -= 1;
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
        )
    }

    fn run(job: &Job, runner: &PipelineRunner) -> Result<(), JobRunError> {
        let progress = |_: f64, _: &str| {};
        let cancelled = || false;
        runner.run(
            job,
            &JobRunContext {
                progress: &progress,
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
    fn cancelled_before_run_stops_immediately() {
        let h = harness();
        let pid = seed_project(&h, "cancelled");
        let runner = runner(&h, 1);
        let progress = |_: f64, _: &str| {};
        let cancelled = || true;
        let j = job(&pid, JobType::Transcribe, serde_json::json!({}));
        let outcome = runner.run(
            &j,
            &JobRunContext {
                progress: &progress,
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
        );
        let j = job(&pid, JobType::Transcribe, serde_json::json!({}));
        let err = run(&j, &runner).expect_err("no client must fail");
        match err {
            JobRunError::Transient { code, .. } => assert_eq!(code, "E_WORKER_NOT_READY"),
            other => panic!("expected E_WORKER_NOT_READY, got {other:?}"),
        }
        let _ = std::fs::remove_dir_all(&h.dir);
    }
}
