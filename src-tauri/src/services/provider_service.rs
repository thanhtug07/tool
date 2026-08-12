//! ProviderService (Provider Management): dynamic provider registry.
//!
//! Providers are *configuration rows*, not code. Automation resolves which
//! provider to use through this service (defaults per capability), never from
//! a hard-coded list in the UI. API keys are NOT stored here — they live in
//! the OS credential vault (`SecretStore`) keyed by provider id.
//!
//! Rules enforced here:
//! - `free` is the built-in safety net: it is always seeded, cannot be
//!   deleted and cannot be disabled, so the system can never end up with no
//!   default provider.
//! - Capability-level defaults live in `provider_defaults`; deleting the
//!   provider that is default for a capability resets that default to `free`.
//! - A provider must be enabled and declare a capability before it can be
//!   resolved for that capability.
//! - Custom providers (this build) support the worker registry kinds
//!   `gemini` / `local` / `mock`; `free` is reserved for the builtin.

use std::collections::BTreeSet;

use rusqlite::params;

use crate::db::{utc_iso8601_now, Database, DbError};

/// Provider type — the capability area a provider serves. Only `translation`
/// is live in this build; STT/TTS are reserved for later pipelines.
pub const PROVIDER_TYPE_TRANSLATION: &str = "translation";

/// Capability identifiers (schema-ready for STT/TTS in later builds).
pub const CAP_TRANSLATION: &str = "translation";
pub const CAP_STT: &str = "stt";
pub const CAP_TTS: &str = "tts";
pub const CAPABILITIES: &[&str] = &[CAP_TRANSLATION, CAP_STT, CAP_TTS];

/// Worker registry kinds the worker's factory understands.
pub const KIND_FREE: &str = "free";
pub const KIND_GEMINI: &str = "gemini";
pub const KIND_LOCAL: &str = "local";
pub const KIND_MOCK: &str = "mock";
/// Kinds a custom provider row may take in this build.
pub const CUSTOM_KINDS: &[&str] = &[KIND_GEMINI, KIND_LOCAL, KIND_MOCK];

/// Whether a worker kind requires a credential (API key) to run.
pub fn kind_needs_key(kind: &str) -> bool {
    kind == KIND_GEMINI
}

/// One provider row as stored (no secrets — those live in SecretStore).
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct ProviderRecord {
    pub id: String,
    pub name: String,
    pub provider_type: String,
    pub provider_kind: String,
    pub enabled: bool,
    pub base_url: Option<String>,
    pub model: Option<String>,
    pub config: serde_json::Value,
    pub capabilities: Vec<String>,
    pub last_test_status: Option<String>,
    pub last_test_at: Option<String>,
    pub created_at: String,
    pub updated_at: String,
}

impl ProviderRecord {
    /// Whether this provider can serve `capability`.
    pub fn supports(&self, capability: &str) -> bool {
        self.capabilities.iter().any(|c| c == capability)
    }

    /// Whether this provider's worker kind needs an API key.
    pub fn needs_key(&self) -> bool {
        kind_needs_key(&self.provider_kind)
    }
}

/// A translation provider resolved for the pipeline: the worker kind to call,
/// the non-secret config to send, and whether a key must be supplied.
#[derive(Debug, Clone)]
pub struct ResolvedTranslationProvider {
    pub id: String,
    pub kind: String,
    pub model: Option<String>,
    pub base_url: Option<String>,
    pub config: serde_json::Value,
    pub needs_key: bool,
}

/// Payload accepted by `create`/`update` (the wire model).
#[derive(Debug, Clone, serde::Deserialize)]
pub struct ProviderInput {
    pub name: String,
    pub provider_type: String,
    pub provider_kind: String,
    pub capabilities: Vec<String>,
    pub base_url: Option<String>,
    pub model: Option<String>,
    pub config: Option<serde_json::Value>,
    /// New API key to store in the credential vault (create: optional;
    /// update: `None`/empty = keep the existing key).
    pub api_key: Option<String>,
    /// Update only: remove the stored key.
    pub clear_key: Option<bool>,
    /// Enabled state (create default true; update optional).
    pub enabled: Option<bool>,
}

pub struct ProviderService {
    db: Database,
}

impl ProviderService {
    /// Open (creating if needed) the provider registry in `data_dir/app.db`.
    /// Migrations seed the built-in registry (FREE/gemini/local/mock).
    pub fn open(data_dir: std::path::PathBuf) -> Self {
        Self {
            db: Database::open(&data_dir.join("app.db")).unwrap_or_else(|e| {
                log::error!("provider database init failed: {e}");
                // Fail closed with an in-memory registry so commands report
                // errors instead of panicking; real data is on disk next launch.
                Database::open(&std::path::PathBuf::from(":memory:")).expect("in-memory db")
            }),
        }
    }

    /// Test-oriented constructor over an existing connection.
    #[cfg(test)]
    pub fn from_connection(conn: rusqlite::Connection) -> Self {
        Self {
            db: Database::from_connection(conn).expect("apply migrations"),
        }
    }

    /// All providers, ordered: builtin FREE first, then enabled, then name.
    pub fn list(&self) -> Result<Vec<ProviderRecord>, DbError> {
        let conn = self.db.conn();
        let mut stmt = conn
            .prepare(
                "SELECT id, name, provider_type, provider_kind, enabled, base_url, model,
                        config_json, capabilities_json, last_test_status, last_test_at,
                        created_at, updated_at
                 FROM providers
                 ORDER BY (id = 'free') DESC, enabled DESC, name ASC",
            )
            .map_err(DbError::from)?;
        let rows = stmt.query_map([], row_to_record).map_err(DbError::from)?;
        rows.collect::<Result<Vec<_>, _>>().map_err(DbError::from)
    }

    /// One provider by id.
    pub fn get(&self, id: &str) -> Result<ProviderRecord, DbError> {
        let conn = self.db.conn();
        conn.query_row(
            "SELECT id, name, provider_type, provider_kind, enabled, base_url, model,
                    config_json, capabilities_json, last_test_status, last_test_at,
                    created_at, updated_at
             FROM providers WHERE id = ?1",
            [id],
            row_to_record,
        )
        .map_err(|e| match e {
            rusqlite::Error::QueryReturnedNoRows => {
                DbError::NotFound(format!("provider `{id}` not found"))
            }
            other => DbError::from(other),
        })
    }

    /// Current default provider id for `capability`, healing to `free` when
    /// the stored default no longer exists (delete/fallback rule).
    pub fn default_for(&self, capability: &str) -> Result<String, DbError> {
        validate_capability(capability)?;
        let current: Option<String> = {
            let conn = self.db.conn();
            conn.query_row(
                "SELECT provider_id FROM provider_defaults WHERE capability = ?1",
                [capability],
                |r| r.get(0),
            )
            .ok()
        };
        if let Some(id) = current {
            if self.get(&id).is_ok() {
                return Ok(id);
            }
        }
        // Heal: no default (or deleted default) → FREE is the safety net.
        self.set_default(KIND_FREE, capability)?;
        Ok(KIND_FREE.to_string())
    }

    /// Every (capability → provider_id) default pair, for the UI. Always
    /// exposes every known capability (missing entries heal to FREE).
    pub fn defaults(&self) -> Result<std::collections::BTreeMap<String, String>, DbError> {
        let mut map = std::collections::BTreeMap::new();
        {
            let conn = self.db.conn();
            let mut stmt = conn
                .prepare("SELECT capability, provider_id FROM provider_defaults")
                .map_err(DbError::from)?;
            let rows = stmt
                .query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))
                .map_err(DbError::from)?;
            for row in rows {
                let (cap, id) = row.map_err(DbError::from)?;
                map.insert(cap, id);
            }
        }
        // The seed guarantees all three rows; fill anything missing with FREE.
        for cap in CAPABILITIES {
            map.entry((*cap).to_string())
                .or_insert_with(|| KIND_FREE.to_string());
        }
        Ok(map)
    }

    /// Create a provider row. `free` ids are reserved; custom ids are
    /// generated. Returns the created record (secrets handled by the caller).
    pub fn create(&self, input: &ProviderInput) -> Result<ProviderRecord, DbError> {
        validate_input(input, /*is_update=*/ false)?;
        let id = crate::db::new_uuid_v4()?;
        let now = utc_iso8601_now();
        self.db.transaction(|conn| {
            conn.execute(
                "INSERT INTO providers
                    (id, name, provider_type, provider_kind, enabled, base_url, model,
                     config_json, capabilities_json, last_test_status, last_test_at,
                     created_at, updated_at)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, NULL, NULL, ?10, ?10)",
                params![
                    id,
                    input.name.trim(),
                    input.provider_type,
                    input.provider_kind,
                    input.enabled.unwrap_or(true) as i64,
                    input.base_url.as_deref(),
                    input.model.as_deref(),
                    json_str(input.config.as_ref()),
                    serde_json::to_string(&input.capabilities).expect("capabilities json"),
                    now,
                ],
            )?;
            Ok(())
        })?;
        self.get(&id)
    }

    /// Update a provider row (secrets handled by the caller).
    pub fn update(&self, id: &str, input: &ProviderInput) -> Result<ProviderRecord, DbError> {
        validate_input(input, /*is_update=*/ true)?;
        // FREE may be edited in name/URL/model but never disabled or kind-changed.
        if id == KIND_FREE {
            if input.provider_kind != KIND_FREE {
                return Err(DbError::InvalidInput(
                    "FREE's provider kind cannot be changed".into(),
                ));
            }
            if input.enabled == Some(false) {
                return Err(DbError::InvalidInput(
                    "FREE is the built-in safety net and cannot be disabled".into(),
                ));
            }
        }
        let now = utc_iso8601_now();
        self.db.transaction(|conn| {
            let changed = conn.execute(
                "UPDATE providers SET
                    name = ?2, provider_type = ?3, provider_kind = ?4,
                    base_url = ?5, model = ?6, config_json = ?7,
                    capabilities_json = ?8,
                    enabled = COALESCE(?9, enabled),
                    updated_at = ?10
                 WHERE id = ?1",
                params![
                    id,
                    input.name.trim(),
                    input.provider_type,
                    input.provider_kind,
                    input.base_url.as_deref(),
                    input.model.as_deref(),
                    json_str(input.config.as_ref()),
                    serde_json::to_string(&input.capabilities).expect("capabilities json"),
                    input.enabled.map(|e| e as i64),
                    now,
                ],
            )?;
            if changed == 0 {
                return Err(DbError::NotFound(format!("provider `{id}` not found")));
            }
            Ok(())
        })?;
        self.get(id)
    }

    /// Delete a provider. FREE is never deletable. If the deleted provider was
    /// the default for any capability, that default resets to FREE.
    pub fn delete(&self, id: &str) -> Result<(), DbError> {
        if id == KIND_FREE {
            return Err(DbError::InvalidInput(
                "FREE is the built-in provider and cannot be deleted".into(),
            ));
        }
        self.db.transaction(|conn| {
            let row = conn
                .query_row(
                    "SELECT capabilities_json FROM providers WHERE id = ?1",
                    [id],
                    |r| r.get::<_, String>(0),
                )
                .map_err(|e| match e {
                    rusqlite::Error::QueryReturnedNoRows => {
                        DbError::NotFound(format!("provider `{id}` not found"))
                    }
                    other => DbError::from(other),
                })?;
            let caps: Vec<String> = serde_json::from_str(&row).unwrap_or_default();
            conn.execute("DELETE FROM providers WHERE id = ?1", [id])?;
            // ON DELETE CASCADE already removed defaults pointing at `id`;
            // restore FREE as the default for the capabilities it covered.
            for cap in caps {
                conn.execute(
                    "INSERT OR REPLACE INTO provider_defaults (capability, provider_id)
                     VALUES (?1, 'free')",
                    [cap],
                )?;
            }
            Ok(())
        })
    }

    /// Set the default provider for `capability`. The provider must exist, be
    /// enabled and declare the capability.
    pub fn set_default(&self, provider_id: &str, capability: &str) -> Result<(), DbError> {
        validate_capability(capability)?;
        let row = self.get(provider_id)?;
        if !row.enabled {
            return Err(DbError::InvalidInput(format!(
                "provider `{provider_id}` is disabled — enable it before making it the default"
            )));
        }
        if !row.supports(capability) {
            return Err(DbError::InvalidInput(format!(
                "provider `{provider_id}` does not support capability `{capability}`"
            )));
        }
        self.db.transaction(|conn| {
            conn.execute(
                "INSERT OR REPLACE INTO provider_defaults (capability, provider_id)
                 VALUES (?1, ?2)",
                params![capability, provider_id],
            )?;
            Ok(())
        })
    }

    /// Enable/disable a provider (FREE is never disabled).
    pub fn set_enabled(&self, id: &str, enabled: bool) -> Result<ProviderRecord, DbError> {
        if id == KIND_FREE && !enabled {
            return Err(DbError::InvalidInput(
                "FREE is the built-in safety net and cannot be disabled".into(),
            ));
        }
        let now = utc_iso8601_now();
        self.db.transaction(|conn| {
            let changed = conn.execute(
                "UPDATE providers SET enabled = ?2, updated_at = ?3 WHERE id = ?1",
                params![id, enabled as i64, now],
            )?;
            if changed == 0 {
                return Err(DbError::NotFound(format!("provider `{id}` not found")));
            }
            Ok(())
        })?;
        self.get(id)
    }

    /// Record the outcome of a connectivity test.
    pub fn record_test(&self, id: &str, status: &str) -> Result<ProviderRecord, DbError> {
        let now = utc_iso8601_now();
        self.db.transaction(|conn| {
            let changed = conn.execute(
                "UPDATE providers SET last_test_status = ?2, last_test_at = ?3, updated_at = ?3
                 WHERE id = ?1",
                params![id, status, now],
            )?;
            if changed == 0 {
                return Err(DbError::NotFound(format!("provider `{id}` not found")));
            }
            Ok(())
        })?;
        self.get(id)
    }

    /// Resolve the translation provider for the pipeline.
    ///
    /// `explicit` is the provider id the job asked for (e.g. from the UI); when
    /// absent the capability default is used (seeded to FREE, healed on
    /// delete). The provider must exist, be enabled and support translation.
    pub fn resolve_translation(
        &self,
        explicit: Option<&str>,
    ) -> Result<ResolvedTranslationProvider, DbError> {
        let row = match explicit {
            Some(id) if !id.trim().is_empty() => self.get(id)?,
            _ => {
                let default_id = self.default_for(CAP_TRANSLATION)?;
                self.get(&default_id)?
            }
        };
        if !row.enabled {
            return Err(DbError::InvalidInput(format!(
                "provider `{}` is disabled — enable it in Settings → Providers",
                row.id
            )));
        }
        if !row.supports(CAP_TRANSLATION) {
            return Err(DbError::InvalidInput(format!(
                "provider `{}` does not support translation",
                row.id
            )));
        }
        Ok(ResolvedTranslationProvider {
            id: row.id.clone(),
            kind: row.provider_kind.clone(),
            model: row.model.clone(),
            base_url: row.base_url.clone(),
            config: row.config.clone(),
            needs_key: row.needs_key(),
        })
    }

    /// The non-secret config map sent to the worker for a translation call:
    /// `model`/`base_url` for cloud kinds, `server_url`/`model_path` for the
    /// local/free kinds (mirrors the worker factory's expectations).
    pub fn translation_config(
        &self,
        resolved: &ResolvedTranslationProvider,
    ) -> serde_json::Map<String, serde_json::Value> {
        let mut map = serde_json::Map::new();
        if let Some(model) = &resolved.model {
            if !model.trim().is_empty() {
                map.insert("model".into(), serde_json::Value::String(model.clone()));
            }
        }
        match resolved.kind.as_str() {
            KIND_GEMINI => {
                if let Some(url) = &resolved.base_url {
                    if !url.trim().is_empty() {
                        map.insert("base_url".into(), serde_json::Value::String(url.clone()));
                    }
                }
            }
            KIND_LOCAL | KIND_FREE => {
                if let Some(url) = &resolved.base_url {
                    if !url.trim().is_empty() {
                        map.insert("server_url".into(), serde_json::Value::String(url.clone()));
                    }
                }
                if let Some(cfg) = resolved.config.get("model_path") {
                    if let Some(path) = cfg.as_str() {
                        if !path.is_empty() {
                            map.insert(
                                "model_path".into(),
                                serde_json::Value::String(path.to_string()),
                            );
                        }
                    }
                }
            }
            _ => {}
        }
        map
    }
}

fn row_to_record(row: &rusqlite::Row<'_>) -> rusqlite::Result<ProviderRecord> {
    let config_json: String = row.get(7)?;
    let capabilities_json: String = row.get(8)?;
    Ok(ProviderRecord {
        id: row.get(0)?,
        name: row.get(1)?,
        provider_type: row.get(2)?,
        provider_kind: row.get(3)?,
        enabled: row.get::<_, i64>(4)? != 0,
        base_url: row.get(5)?,
        model: row.get(6)?,
        config: serde_json::from_str(&config_json).unwrap_or_else(|_| serde_json::json!({})),
        capabilities: serde_json::from_str(&capabilities_json).unwrap_or_default(),
        last_test_status: row.get(9)?,
        last_test_at: row.get(10)?,
        created_at: row.get(11)?,
        updated_at: row.get(12)?,
    })
}

fn json_str(value: Option<&serde_json::Value>) -> String {
    value
        .map(|v| serde_json::to_string(v).unwrap_or_else(|_| "{}".to_string()))
        .unwrap_or_else(|| "{}".to_string())
}

fn validate_capability(capability: &str) -> Result<(), DbError> {
    if CAPABILITIES.contains(&capability) {
        Ok(())
    } else {
        Err(DbError::InvalidInput(format!(
            "unknown capability `{capability}` (known: {})",
            CAPABILITIES.join(", ")
        )))
    }
}

fn validate_input(input: &ProviderInput, is_update: bool) -> Result<(), DbError> {
    if input.name.trim().is_empty() {
        return Err(DbError::InvalidInput(
            "provider name must not be empty".into(),
        ));
    }
    if input.provider_type != PROVIDER_TYPE_TRANSLATION {
        return Err(DbError::InvalidInput(format!(
            "unsupported provider type `{}` (this build supports `translation`)",
            input.provider_type
        )));
    }
    if !(CUSTOM_KINDS.contains(&input.provider_kind.as_str())
        || is_update && input.provider_kind == KIND_FREE)
    {
        return Err(DbError::InvalidInput(format!(
            "unsupported provider kind `{}`",
            input.provider_kind
        )));
    }
    if input.capabilities.is_empty() {
        return Err(DbError::InvalidInput(
            "at least one capability is required".into(),
        ));
    }
    let seen: BTreeSet<&str> = input.capabilities.iter().map(String::as_str).collect();
    if seen.len() != input.capabilities.len() {
        return Err(DbError::InvalidInput("duplicate capabilities".into()));
    }
    for cap in &seen {
        validate_capability(cap)?;
    }
    if let Some(cfg) = &input.config {
        if !cfg.is_object() {
            return Err(DbError::InvalidInput(
                "configuration must be a JSON object".into(),
            ));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn service() -> ProviderService {
        ProviderService::from_connection(rusqlite::Connection::open_in_memory().expect("mem"))
    }

    fn input(name: &str, kind: &str) -> ProviderInput {
        ProviderInput {
            name: name.into(),
            provider_type: PROVIDER_TYPE_TRANSLATION.into(),
            provider_kind: kind.into(),
            capabilities: vec![CAP_TRANSLATION.into()],
            base_url: None,
            model: None,
            config: None,
            api_key: None,
            clear_key: None,
            enabled: Some(true),
        }
    }

    #[test]
    fn fresh_registry_seeds_free_as_default() {
        let svc = service();
        let list = svc.list().expect("list");
        let ids: Vec<&str> = list.iter().map(|p| p.id.as_str()).collect();
        assert!(ids.contains(&"free"));
        assert!(ids.contains(&"gemini"));
        assert!(ids.contains(&"local"));
        assert!(ids.contains(&"mock"));
        assert_eq!(svc.default_for(CAP_TRANSLATION).expect("default"), "free");
        let defaults = svc.defaults().expect("defaults");
        assert_eq!(
            defaults.get("translation").map(String::as_str),
            Some("free")
        );
        assert_eq!(defaults.get("stt").map(String::as_str), Some("free"));
        assert_eq!(defaults.get("tts").map(String::as_str), Some("free"));
    }

    #[test]
    fn free_is_immutable() {
        let svc = service();
        assert!(svc.delete("free").is_err(), "FREE cannot be deleted");
        assert!(
            svc.set_enabled("free", false).is_err(),
            "FREE cannot be disabled"
        );
    }

    #[test]
    fn create_edit_delete_roundtrip() {
        let svc = service();
        let created = svc.create(&input("My Gemini", "gemini")).expect("create");
        assert!(!created.id.is_empty());
        assert!(created.enabled);

        let edited = svc
            .update(
                &created.id,
                &ProviderInput {
                    model: Some("gemini-2.5-flash".into()),
                    ..input("My Gemini v2", "gemini")
                },
            )
            .expect("update");
        assert_eq!(edited.name, "My Gemini v2");
        assert_eq!(edited.model.as_deref(), Some("gemini-2.5-flash"));

        svc.delete(&created.id).expect("delete");
        assert!(svc.get(&created.id).is_err());
    }

    #[test]
    fn set_default_validates_capability_and_enabled() {
        let svc = service();
        let created = svc.create(&input("TTS only", "local")).expect("create");
        // Not a translation provider → cannot become translation default.
        svc.update(
            &created.id,
            &ProviderInput {
                capabilities: vec![CAP_TTS.into()],
                ..input("TTS only", "local")
            },
        )
        .expect("update caps");
        assert!(svc.set_default(&created.id, CAP_TRANSLATION).is_err());

        // Disabled provider cannot become default.
        svc.set_enabled(&created.id, false).expect("disable");
        assert!(svc.set_default(&created.id, CAP_TTS).is_err());

        // Enabled + capable → becomes default.
        svc.set_enabled(&created.id, true).expect("enable");
        svc.set_default(&created.id, CAP_TTS).expect("set default");
        assert_eq!(svc.default_for(CAP_TTS).expect("default"), created.id);
    }

    #[test]
    fn deleting_default_falls_back_to_free() {
        let svc = service();
        let created = svc.create(&input("Gemini 2", "gemini")).expect("create");
        svc.set_default(&created.id, CAP_TRANSLATION)
            .expect("set default");
        assert_eq!(
            svc.default_for(CAP_TRANSLATION).expect("default"),
            created.id
        );
        svc.delete(&created.id).expect("delete");
        assert_eq!(
            svc.default_for(CAP_TRANSLATION).expect("default"),
            "free",
            "deleted default heals to FREE"
        );
    }

    #[test]
    fn resolve_requires_enabled_and_capability() {
        let svc = service();
        let resolved = svc.resolve_translation(None).expect("resolve default");
        assert_eq!(resolved.id, "free");
        assert!(!resolved.needs_key);

        let created = svc.create(&input("Gemini 3", "gemini")).expect("create");
        let resolved = svc
            .resolve_translation(Some(&created.id))
            .expect("resolve explicit");
        assert_eq!(resolved.kind, "gemini");
        assert!(resolved.needs_key, "gemini requires a key");

        svc.set_enabled(&created.id, false).expect("disable");
        assert!(svc.resolve_translation(Some(&created.id)).is_err());

        assert!(
            svc.resolve_translation(Some("does-not-exist")).is_err(),
            "unknown provider is a hard error"
        );
    }

    #[test]
    fn translation_config_maps_kinds() {
        let svc = service();
        let local = svc.resolve_translation(None).expect("free default");
        let cfg = svc.translation_config(&local);
        assert_eq!(
            cfg.get("server_url").and_then(|v| v.as_str()),
            Some("http://127.0.0.1:8080"),
            "FREE maps to server_url for the local worker kind"
        );
    }
}
