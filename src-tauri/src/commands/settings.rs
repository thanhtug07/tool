//! Settings + secrets IPC commands (TASK-030, `MASTER_PLAN.md` §25.2).
//!
//! - `secrets.*` — API keys in the OS credential vault (Windows Credential
//!   Manager, FIX #8). Only masked keys ever return to the frontend; the full
//!   secret travels UI → Rust → vault and nowhere else, and is never logged.
//! - `settings.*` — validated, whitelisted app settings persisted in SQLite
//!   (non-secret only). `cache.quota_bytes` is applied to the cache service
//!   immediately.
//!
//! Security model: no raw error propagation that could embed a secret (keyring
//! errors are mapped to a fixed user-facing message), and command arguments
//! that carry keys are never logged.

use std::sync::Arc;

use tauri::{Manager, State};

use crate::db::DbError;
use crate::security::secret_store::{SecretStore, SecretStoreError};
use crate::services::cache_service::CacheService;
use crate::services::settings_service::SettingsService;
use crate::services::worker_client::{TtsPreviewRequest, TtsPreviewResponse, TtsVoicesResponse};
use crate::services::worker_manager::WorkerManager;

/// `secrets.set_api_key(provider, key) → ()`
#[tauri::command(rename = "secrets.set_api_key")]
pub fn set_api_key(
    store: State<'_, Arc<SecretStore>>,
    provider: String,
    key: String,
) -> Result<(), String> {
    store.set_api_key(&provider, &key).map_err(secret_err)
}

/// `secrets.get_api_key_masked(provider) → string | null`
#[tauri::command(rename = "secrets.get_api_key_masked")]
pub fn get_api_key_masked(
    store: State<'_, Arc<SecretStore>>,
    provider: String,
) -> Result<Option<String>, String> {
    store.get_api_key_masked(&provider).map_err(secret_err)
}

/// `secrets.delete_api_key(provider) → ()`
#[tauri::command(rename = "secrets.delete_api_key")]
pub fn delete_api_key(store: State<'_, Arc<SecretStore>>, provider: String) -> Result<(), String> {
    store.delete_api_key(&provider).map_err(secret_err)
}

/// `settings.get_all() → object` (every key, typed values)
#[tauri::command(rename = "settings.get_all")]
pub fn get_all(service: State<'_, Arc<SettingsService>>) -> Result<serde_json::Value, String> {
    service.get_all().map_err(db_err)
}

/// `settings.voices() → TtsVoicesResponse` — available TTS voices per engine,
/// served by the worker (never hard-coded in the UI).
#[tauri::command(rename = "settings.voices")]
pub fn voices(manager: State<'_, WorkerManager>) -> Result<TtsVoicesResponse, String> {
    let client = manager
        .worker_client()
        .ok_or_else(|| "The worker is not running — start it before listing voices.".to_string())?;
    client.tts_voices().map_err(|e| e.to_string())
}

/// `settings.ttsPreview(engine, voice, text) → TtsPreviewResponse` — real
/// single-clip TTS synthesis for the Voice Library preview (worker-cached).
/// The generated wav lands in the app-data `voice-previews` dir, which is
/// added to the asset scope so the webview can play it via `asset://`.
#[tauri::command(rename = "settings.ttsPreview")]
pub fn tts_preview(
    app: tauri::AppHandle,
    manager: State<'_, WorkerManager>,
    engine: String,
    voice: String,
    text: String,
) -> Result<TtsPreviewResponse, String> {
    let data_dir = app.path().app_data_dir().map_err(|e| e.to_string())?;
    let preview_dir = data_dir.join("voice-previews");
    std::fs::create_dir_all(&preview_dir).map_err(|e| e.to_string())?;
    // Asset-scope the preview dir so the returned wav is playable.
    let _ = app.asset_protocol_scope().allow_directory(&preview_dir, true);
    let client = manager
        .worker_client()
        .ok_or_else(|| "The worker is not running — start it before previewing a voice.".to_string())?;
    client
        .tts_preview(&TtsPreviewRequest {
            engine,
            voice,
            text,
            output_dir: Some(preview_dir.to_string_lossy().into_owned()),
        })
        .map_err(|e| e.to_string())
}

/// `settings.set(key, value) → updated object`
#[tauri::command(rename = "settings.set")]
pub fn set(
    service: State<'_, Arc<SettingsService>>,
    cache: State<'_, Arc<CacheService>>,
    key: String,
    value: String,
) -> Result<serde_json::Value, String> {
    // Validate + persist first, so an invalid value never mutates runtime
    // state; the cache quota then takes effect immediately (LRU re-runs).
    let updated = service.set(&key, &value).map_err(db_err)?;
    if key == "cache.quota_bytes" {
        if let Ok(bytes) = value.trim().parse::<u64>() {
            if let Err(e) = cache.set_max_bytes(bytes) {
                return Err(db_err(e));
            }
        }
    }
    Ok(updated)
}

fn db_err(e: DbError) -> String {
    e.to_string()
}

/// Map a credential-vault failure to a user-facing string.
///
/// The message deliberately never includes the key or a stack trace. FIX #8:
/// an unavailable vault surfaces as a clear "not saved" message — there is no
/// fallback to an encrypted file or custom crypto.
fn secret_err(e: SecretStoreError) -> String {
    match e {
        SecretStoreError::Unavailable(_) => {
            "Could not save the API key: the OS credential service (Windows \
             Credential Manager) is not available on this machine."
                .to_string()
        }
        SecretStoreError::InvalidInput(m) => format!("invalid input: {m}"),
        SecretStoreError::NotFound => "No API key stored for this provider.".to_string(),
    }
}
