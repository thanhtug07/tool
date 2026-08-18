//! Model management IPC (Settings → Providers → local LLM).
//!
//! The translation GGUF catalog + download run through the *worker* (it bundles
//! the fetch layer and reports cancellable progress via its job registry); this
//! module proxies them and polls ``/v1/progress`` so the frontend sees moving
//! progress. Downloaded models land in ``{app_data}/models/`` and translate
//! providers can point their ``model_path`` at them.

use std::path::PathBuf;
use std::time::Duration;

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager, State};

use crate::services::worker_client::{
    HttpError, ModelCatalogResponse, ModelDownloadRequest, ModelDownloadResponse,
};
use crate::services::worker_manager::WorkerManager;

/// How often the download command polls live progress from the worker.
const PROGRESS_POLL_INTERVAL: Duration = Duration::from_millis(400);
/// Minimum progress delta before a new `models:download-progress` event fires.
const PROGRESS_MIN_DELTA: f64 = 0.01;

/// Serialized local model (an installed GGUF in ``{app_data}/models``).
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LocalModelInfo {
    pub file_name: String,
    pub path: String,
    pub size_bytes: u64,
}

/// Wire shape of the `models:download-progress` event.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ModelDownloadProgressPayload {
    job_id: String,
    progress: f64,
    stage: String,
    message: Option<String>,
}

/// ``models.catalog() → ModelCatalogResponse`` (from the worker).
#[tauri::command(rename = "models.catalog")]
pub fn catalog(manager: State<'_, WorkerManager>) -> Result<ModelCatalogResponse, String> {
    let client = manager.worker_client().ok_or_else(model_client_err)?;
    client.model_catalog().map_err(|e| e.to_string())
}

/// ``models.list_local() → LocalModelInfo[]`` — installed GGUFs in app data.
#[tauri::command(rename = "models.list_local")]
pub fn list_local(app: AppHandle) -> Result<Vec<LocalModelInfo>, String> {
    let dir = models_dir(&app)?;
    let mut models = Vec::new();
    if let Ok(entries) = std::fs::read_dir(&dir) {
        for entry in entries.flatten() {
            let path = entry.path();
            if path.extension().and_then(|e| e.to_str()) != Some("gguf") {
                continue;
            }
            let size_bytes = std::fs::metadata(&path).map(|m| m.len()).unwrap_or(0);
            models.push(LocalModelInfo {
                file_name: path
                    .file_name()
                    .map(|n| n.to_string_lossy().into_owned())
                    .unwrap_or_default(),
                path: path.to_string_lossy().into_owned(),
                size_bytes,
            });
        }
    }
    models.sort_by(|a, b| a.file_name.cmp(&b.file_name));
    Ok(models)
}

/// ``models.download(repo_id, filename, mirror?) → ModelDownloadResponse``
///
/// Downloads the GGUF into ``{app_data}/models``, emitting
/// ``models:download-progress`` events (``{jobId, progress, stage, message}``)
/// while the worker streams it. Returns once the file is on disk.
#[tauri::command(rename = "models.download")]
pub async fn download(
    app: AppHandle,
    manager: State<'_, WorkerManager>,
    repo_id: String,
    filename: String,
    mirror: Option<String>,
) -> Result<ModelDownloadResponse, String> {
    let client = manager.worker_client().ok_or_else(model_client_err)?;
    let job_id = format!("model-download-{}", uuid_v4());
    let local_dir = models_dir(&app)?.to_string_lossy().into_owned();

    let request = ModelDownloadRequest {
        repo_id,
        filename,
        local_dir,
        mirror,
        job_id: Some(job_id.clone()),
    };

    // The worker call is blocking HTTP for minutes on ~2 GB — run it on a
    // background thread while the calling thread polls progress + emits.
    let (tx, rx) = std::sync::mpsc::channel();
    let call_client = client.clone();
    std::thread::spawn(move || {
        let _ = tx.send(call_client.download_model(request));
    });

    // Run the poll loop off the async executor (recv_timeout blocks); the
    // worker call runs on its own thread, so this bounds to the download.
    let poll_app = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        poll_download_progress(&poll_app, client, rx, job_id)
    })
    .await
    .map_err(|e| format!("download task failed: {e}"))?
}

/// Poll the worker progress registry while a download is in flight, emitting
/// ``models:download-progress`` events; returns the worker's final response.
fn poll_download_progress(
    app: &AppHandle,
    client: crate::services::worker_client::WorkerClient,
    rx: std::sync::mpsc::Receiver<Result<ModelDownloadResponse, HttpError>>,
    job_id: String,
) -> Result<ModelDownloadResponse, String> {
    let mut last_reported: Option<f64> = None;
    let mut last_message: Option<String> = None;
    loop {
        match rx.recv_timeout(PROGRESS_POLL_INTERVAL) {
            Ok(result) => return result.map_err(|e| e.to_string()),
            Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => {
                return Err("the worker download thread exited without a result".to_string());
            }
            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {
                if let Ok(progress) = client.get_progress(&job_id) {
                    let mut emitted = false;
                    if let Some(p) = progress.progress {
                        let should_report =
                            last_reported.is_none_or(|last| (p - last).abs() >= PROGRESS_MIN_DELTA);
                        if should_report {
                            last_reported = Some(p);
                            let _ = app.emit(
                                "models:download-progress",
                                ModelDownloadProgressPayload {
                                    job_id: job_id.clone(),
                                    progress: p,
                                    stage: progress
                                        .stage
                                        .clone()
                                        .unwrap_or_else(|| "model-download".to_string()),
                                    message: progress.message.clone(),
                                },
                            );
                            emitted = true;
                        }
                    }
                    if let Some(message) = progress.message {
                        if last_message.as_deref() != Some(message.as_str()) {
                            last_message = Some(message.clone());
                            if !emitted {
                                let _ = app.emit(
                                    "models:download-progress",
                                    ModelDownloadProgressPayload {
                                        job_id: job_id.clone(),
                                        progress: last_reported.unwrap_or(0.0),
                                        stage: progress
                                            .stage
                                            .unwrap_or_else(|| "model-download".to_string()),
                                        message: Some(message.clone()),
                                    },
                                );
                            }
                        }
                    }
                }
            }
        }
    }
}

/// ``{app_data}/models`` — where downloaded translation models live.
fn models_dir(app: &AppHandle) -> Result<PathBuf, String> {
    let dir = app
        .path()
        .app_data_dir()
        .map_err(|e| e.to_string())?
        .join("models");
    std::fs::create_dir_all(&dir).map_err(io_err)?;
    Ok(dir)
}

fn model_client_err() -> String {
    "The worker is not running — start it before downloading a model.".to_string()
}

fn io_err(e: std::io::Error) -> String {
    format!("filesystem error: {e}")
}

fn uuid_v4() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    format!("{nanos:x}-{:x}", std::process::id())
}
