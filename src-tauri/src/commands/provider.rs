//! Provider Management IPC commands (`providers.*`).
//!
//! Thin orchestration over `ProviderService` (registry rows + capability
//! defaults) + `SecretStore` (OS credential vault) + the worker's
//! `/v1/providers/test` endpoint. The frontend never hard-codes providers:
//! it lists them from here, Automation resolves them through
//! `PipelineRunner` (which consults the same registry).
//!
//! Security model: full API keys cross UI → Rust → OS vault (and, when a test
//! or a translation runs, Rust → loopback worker) and are never returned to
//! the frontend — only `api_key_configured: bool` and masked forms. Keys are
//! never logged; command arguments that carry keys are never printed.
//! `Save & Test` stores the key ONLY if the live test succeeds.

use std::sync::Arc;

use tauri::State;

use crate::db::DbError;
use crate::security::secret_store::{SecretStore, SecretStoreError};
use crate::services::provider_service::{
    ProviderInput, ProviderRecord, ProviderService, ResolvedTranslationProvider,
};
use crate::services::worker_client::ProviderTestResult;
use crate::services::worker_manager::WorkerManager;

/// Wire view of a provider: the stored record + derived key status. The full
/// key never crosses the IPC boundary.
#[derive(Debug, Clone, serde::Serialize)]
pub struct ProviderView {
    #[serde(flatten)]
    pub record: ProviderRecord,
    /// Whether this provider's worker kind needs an API key.
    pub needs_key: bool,
    /// Whether a key is stored in the OS credential vault (only meaningful
    /// when `needs_key`).
    pub api_key_configured: bool,
}

/// `providers.list() → { providers, defaults }`
#[derive(Debug, Clone, serde::Serialize)]
pub struct ProvidersListResponse {
    pub providers: Vec<ProviderView>,
    /// capability → provider id (every known capability, healed to FREE).
    pub defaults: std::collections::BTreeMap<String, String>,
}

fn view(record: ProviderRecord, secrets: &SecretStore) -> Result<ProviderView, String> {
    let needs_key = record.needs_key();
    let api_key_configured = if needs_key {
        secrets.has_api_key(&record.id).unwrap_or(false)
    } else {
        false
    };
    Ok(ProviderView {
        record,
        needs_key,
        api_key_configured,
    })
}

/// `providers.list() → ProvidersListResponse`
#[tauri::command(rename = "providers.list")]
pub fn list_providers(
    providers: State<'_, Arc<ProviderService>>,
    secrets: State<'_, Arc<SecretStore>>,
) -> Result<ProvidersListResponse, String> {
    let records = providers.list().map_err(db_err)?;
    let mut views = Vec::with_capacity(records.len());
    for record in records {
        views.push(view(record, &secrets)?);
    }
    Ok(ProvidersListResponse {
        providers: views,
        defaults: providers.defaults().map_err(db_err)?,
    })
}

/// `providers.get(id) → ProviderView`
#[tauri::command(rename = "providers.get")]
pub fn get_provider(
    providers: State<'_, Arc<ProviderService>>,
    secrets: State<'_, Arc<SecretStore>>,
    id: String,
) -> Result<ProviderView, String> {
    view(providers.get(&id).map_err(db_err)?, &secrets)
}

/// `providers.create(input, test?) → ProviderView`
///
/// `test=true` runs a live connectivity test as part of the save; the API key
/// is stored ONLY when the test succeeds (Save & Test semantics).
#[tauri::command(rename = "providers.create")]
pub fn create_provider(
    providers: State<'_, Arc<ProviderService>>,
    secrets: State<'_, Arc<SecretStore>>,
    manager: State<'_, WorkerManager>,
    input: ProviderInput,
    test: Option<bool>,
) -> Result<ProviderView, String> {
    let record = providers.create(&input).map_err(db_err)?;
    let want_test = test.unwrap_or(false);
    let new_key = input
        .api_key
        .as_deref()
        .map(str::trim)
        .filter(|k| !k.is_empty());
    if want_test {
        run_test(&providers, &secrets, &manager, &record, new_key, true).inspect_err(|_| {
            let _ = providers.record_test(&record.id, "failure");
        })?;
    } else if let Some(key) = new_key {
        store_key(&secrets, &record.id, key)?;
    }
    view(providers.get(&record.id).map_err(db_err)?, &secrets)
}

/// `providers.update(id, input, test?) → ProviderView`
#[tauri::command(rename = "providers.update")]
pub fn update_provider(
    providers: State<'_, Arc<ProviderService>>,
    secrets: State<'_, Arc<SecretStore>>,
    manager: State<'_, WorkerManager>,
    id: String,
    input: ProviderInput,
    test: Option<bool>,
) -> Result<ProviderView, String> {
    if input.clear_key == Some(true) {
        let _ = secrets.delete_api_key(&id);
    }
    let record = providers.update(&id, &input).map_err(db_err)?;
    let want_test = test.unwrap_or(false);
    let new_key = input
        .api_key
        .as_deref()
        .map(str::trim)
        .filter(|k| !k.is_empty());
    if want_test {
        run_test(&providers, &secrets, &manager, &record, new_key, true).inspect_err(|_| {
            let _ = providers.record_test(&record.id, "failure");
        })?;
    } else if let Some(key) = new_key {
        store_key(&secrets, &record.id, key)?;
    }
    view(providers.get(&record.id).map_err(db_err)?, &secrets)
}

/// `providers.delete(id) → ()` — FREE is never deletable; deleting the default
/// falls back to FREE (enforced in ProviderService).
#[tauri::command(rename = "providers.delete")]
pub fn delete_provider(
    providers: State<'_, Arc<ProviderService>>,
    secrets: State<'_, Arc<SecretStore>>,
    id: String,
) -> Result<(), String> {
    providers.delete(&id).map_err(db_err)?;
    // Best-effort: remove the vault credential so no orphan secret lingers.
    let _ = secrets.delete_api_key(&id);
    Ok(())
}

/// `providers.set_default(id, capability) → ()`
#[tauri::command(rename = "providers.set_default")]
pub fn set_provider_default(
    providers: State<'_, Arc<ProviderService>>,
    id: String,
    capability: String,
) -> Result<(), String> {
    providers.set_default(&id, &capability).map_err(db_err)
}

/// `providers.set_enabled(id, enabled) → ProviderView`
#[tauri::command(rename = "providers.set_enabled")]
pub fn set_provider_enabled(
    providers: State<'_, Arc<ProviderService>>,
    secrets: State<'_, Arc<SecretStore>>,
    id: String,
    enabled: bool,
) -> Result<ProviderView, String> {
    let record = providers.set_enabled(&id, enabled).map_err(db_err)?;
    view(record, &secrets)
}

/// `providers.test(id, apiKey?) → ProviderTestResult`
///
/// Tests the provider with the stored key, or with `apiKey` as a one-shot
/// override (never stored). Records the outcome on the provider row.
#[tauri::command(rename = "providers.test")]
pub fn test_provider(
    providers: State<'_, Arc<ProviderService>>,
    secrets: State<'_, Arc<SecretStore>>,
    manager: State<'_, WorkerManager>,
    id: String,
    api_key: Option<String>,
) -> Result<ProviderTestResult, String> {
    let record = providers.get(&id).map_err(db_err)?;
    let override_key = api_key.as_deref().map(str::trim).filter(|k| !k.is_empty());
    run_test(&providers, &secrets, &manager, &record, override_key, false).inspect_err(|_| {
        let _ = providers.record_test(&record.id, "failure");
    })
}

// ---- shared helpers --------------------------------------------------------

/// Run a live provider test through the worker. When `store_key_on_success` is
/// set and an override key was supplied, the key is stored in the OS vault
/// only after the test succeeds (Save & Test semantics).
fn run_test(
    providers: &ProviderService,
    secrets: &SecretStore,
    manager: &WorkerManager,
    record: &ProviderRecord,
    api_key_override: Option<&str>,
    store_key_on_success: bool,
) -> Result<ProviderTestResult, String> {
    // Resolve the non-secret config the worker needs for this kind.
    let resolved = ResolvedTranslationProvider {
        id: record.id.clone(),
        kind: record.provider_kind.clone(),
        model: record.model.clone(),
        base_url: record.base_url.clone(),
        config: record.config.clone(),
        needs_key: record.needs_key(),
    };
    let config = providers.translation_config(&resolved);

    // Key resolution: override wins; otherwise the stored credential.
    let key = if record.needs_key() {
        match api_key_override {
            Some(k) => Some(k.to_string()),
            None => secrets.get_api_key(&record.id).map_err(secret_err)?,
        }
    } else {
        None
    };

    let client = manager.worker_client().ok_or_else(|| {
        "The worker is not running — start it before testing a provider.".to_string()
    })?;
    let result = client
        .test_provider(&record.provider_kind, &config, key.as_deref())
        .map_err(|e| e.to_string())?;

    if store_key_on_success {
        if let Some(k) = api_key_override {
            store_key(secrets, &record.id, k)?;
        }
    }
    providers
        .record_test(&record.id, "success")
        .map_err(db_err)?;
    Ok(result)
}

fn store_key(secrets: &SecretStore, id: &str, key: &str) -> Result<(), String> {
    secrets.set_api_key(id, key).map_err(secret_err)
}

fn db_err(e: DbError) -> String {
    e.to_string()
}

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
