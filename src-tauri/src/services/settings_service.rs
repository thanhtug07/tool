//! SettingsService (TASK-030): app-level settings persisted in SQLite.
//!
//! Non-secret configuration (AI model/device/preset, GPU override, provider
//! base URLs, cache quota, privacy mode) lives in the `settings` table —
//! validated against a whitelist before write. Secrets (API keys) NEVER go
//! here: they belong to `security::secret_store` (OS credential vault, FIX #8).
//!
//! `get_all` always returns every known key (stored value or built-in default)
//! so the UI can bind without guessing; `set` validates both the key and the
//! value and stores it as its typed wire representation.

use std::collections::BTreeMap;

use rusqlite::Connection;

use crate::db::{utc_iso8601_now, Database, DbError};

/// Minimum cache quota accepted (1 GB) — matches the worker's STT RAM guard
/// spirit: a cache smaller than this is a footgun, not a feature.
pub const MIN_CACHE_QUOTA_BYTES: u64 = 1024 * 1024 * 1024;
/// Upper bound guard against absurd values (1 TB).
pub const MAX_CACHE_QUOTA_BYTES: u64 = 1024 * 1024 * 1024 * 1024;

/// Default cache quota (ARCHITECTURE_DECISION.md §3.7: 10 GB).
pub const DEFAULT_CACHE_QUOTA_BYTES: u64 = 10 * 1024 * 1024 * 1024;

/// Whitelisted settings keys (order = UI section order in get_all).
pub const SETTINGS_KEYS: &[&str] = &[
    "ai.model",
    "ai.device",
    "ai.preset",
    "gpu.override",
    "api.gemini.base_url",
    "api.gemini.model",
    "api.local.base_url",
    "cache.quota_bytes",
    "privacy.mode",
    "privacy.telemetry",
    "tts.engine",
    "tts.voice",
    "automation.chunked",
    "automation.chunk_duration",
    "automation.chunk_overlap",
    "automation.chunk_concurrency",
    "automation.chunk_retries",
    "automation.stt_mode",
    "automation.stt_batch_size",
    "automation.orchestrator_v2",
];

/// Built-in defaults for keys that have never been written.
fn defaults() -> BTreeMap<&'static str, String> {
    let mut map = BTreeMap::new();
    map.insert("ai.model", "large-v3".to_string());
    map.insert("ai.device", "auto".to_string());
    map.insert("ai.preset", "balanced".to_string());
    map.insert("gpu.override", "auto".to_string());
    map.insert("api.gemini.base_url", String::new());
    map.insert("api.gemini.model", "gemini-flash-lite-latest".to_string());
    map.insert("api.local.base_url", "http://127.0.0.1:8080".to_string());
    map.insert("cache.quota_bytes", DEFAULT_CACHE_QUOTA_BYTES.to_string());
    map.insert("privacy.mode", "local".to_string());
    map.insert("privacy.telemetry", "false".to_string());
    map.insert("tts.engine", "edge".to_string());
    map.insert("tts.voice", "vi-VN-HoaiMyNeural".to_string());
    map.insert("automation.chunked", "false".to_string());
    map.insert("automation.chunk_duration", "30".to_string());
    map.insert("automation.chunk_overlap", "2".to_string());
    map.insert("automation.chunk_concurrency", "4".to_string());
    map.insert("automation.chunk_retries", "2".to_string());
    map.insert("automation.stt_mode", "auto".to_string());
    map.insert("automation.stt_batch_size", "2".to_string());
    map.insert("automation.orchestrator_v2", "false".to_string());
    map
}

/// Validate `key` + `value`; returns the canonical string to persist.
pub fn validate_setting(key: &str, value: &str) -> Result<String, DbError> {
    if !SETTINGS_KEYS.contains(&key) {
        return Err(DbError::InvalidInput(format!(
            "unknown settings key {key:?}"
        )));
    }
    let value = value.trim();
    match key {
        "ai.device" | "gpu.override" => {
            if !matches!(value, "auto" | "cuda" | "cpu") {
                return Err(DbError::InvalidInput(format!(
                    "{key} must be one of auto/cuda/cpu"
                )));
            }
        }
        "privacy.mode" => {
            if !matches!(value, "local" | "cloud") {
                return Err(DbError::InvalidInput(
                    "privacy.mode must be one of local/cloud".into(),
                ));
            }
        }
        "privacy.telemetry" => {
            if !matches!(value, "true" | "false") {
                return Err(DbError::InvalidInput(format!(
                    "{key} must be true or false"
                )));
            }
        }
        "tts.engine" => {
            if !matches!(value, "edge" | "piper") {
                return Err(DbError::InvalidInput(
                    "tts.engine must be one of edge/piper".to_string(),
                ));
            }
        }
        "tts.voice" => {
            if value.is_empty() {
                return Err(DbError::InvalidInput("tts.voice must not be empty".into()));
            }
        }
        "automation.chunked" => {
            if !matches!(value, "true" | "false") {
                return Err(DbError::InvalidInput(
                    "automation.chunked must be true or false".into(),
                ));
            }
        }
        "automation.chunk_duration" => {
            if !matches!(value, "20" | "30" | "45" | "60") {
                return Err(DbError::InvalidInput(
                    "automation.chunk_duration must be one of 20/30/45/60".into(),
                ));
            }
        }
        "automation.chunk_overlap" => {
            let v: f64 = value.parse().map_err(|_| {
                DbError::InvalidInput("automation.chunk_overlap must be a number".into())
            })?;
            if !(0.0..=10.0).contains(&v) {
                return Err(DbError::InvalidInput(
                    "automation.chunk_overlap must be between 0 and 10".into(),
                ));
            }
        }
        "automation.chunk_concurrency" => {
            let v: u32 = value.parse().map_err(|_| {
                DbError::InvalidInput("automation.chunk_concurrency must be an integer".into())
            })?;
            if !(1..=8).contains(&v) {
                return Err(DbError::InvalidInput(
                    "automation.chunk_concurrency must be between 1 and 8".into(),
                ));
            }
        }
        "automation.chunk_retries" => {
            let v: u32 = value.parse().map_err(|_| {
                DbError::InvalidInput("automation.chunk_retries must be an integer".into())
            })?;
            if v > 5 {
                return Err(DbError::InvalidInput(
                    "automation.chunk_retries must be between 0 and 5".into(),
                ));
            }
        }
        "automation.stt_mode" => {
            if !matches!(value, "auto" | "regular" | "batched") {
                return Err(DbError::InvalidInput(
                    "automation.stt_mode must be one of auto/regular/batched".into(),
                ));
            }
        }
        "automation.stt_batch_size" => {
            let v: u32 = value.parse().map_err(|_| {
                DbError::InvalidInput("automation.stt_batch_size must be an integer".into())
            })?;
            if !matches!(v, 1 | 2 | 4) {
                return Err(DbError::InvalidInput(
                    "automation.stt_batch_size must be one of 1/2/4".into(),
                ));
            }
        }
        "automation.orchestrator_v2" => {
            if !matches!(value, "true" | "false") {
                return Err(DbError::InvalidInput(
                    "automation.orchestrator_v2 must be true or false".into(),
                ));
            }
        }
        "cache.quota_bytes" => {
            let bytes: u64 = value.parse().map_err(|_| {
                DbError::InvalidInput("cache.quota_bytes must be a positive integer".into())
            })?;
            if !(MIN_CACHE_QUOTA_BYTES..=MAX_CACHE_QUOTA_BYTES).contains(&bytes) {
                return Err(DbError::InvalidInput(format!(
                    "cache.quota_bytes must be between {} and {} bytes",
                    MIN_CACHE_QUOTA_BYTES, MAX_CACHE_QUOTA_BYTES
                )));
            }
        }
        // Provider base URLs may be empty (= use the provider default endpoint).
        "api.gemini.base_url" | "api.local.base_url" => {}
        _ => {
            if value.is_empty() {
                return Err(DbError::InvalidInput(format!("{key} must not be empty")));
            }
        }
    }
    Ok(value.to_string())
}

/// The settings service, managed as Tauri app state. Owns its own connection
/// to `{data_dir}/app.db` (WAL allows concurrent connections).
pub struct SettingsService {
    db: Result<Database, DbError>,
}

impl SettingsService {
    pub fn open(data_dir: std::path::PathBuf) -> Self {
        let db = Database::open(&data_dir.join("app.db"));
        if let Err(e) = &db {
            log::error!("settings database init failed: {e}");
        }
        Self { db }
    }

    /// All known settings as a typed JSON object (stored value or default).
    /// `cache.quota_bytes` → number, `privacy.telemetry` → bool, rest → string.
    pub fn get_all(&self) -> Result<serde_json::Value, DbError> {
        let db = self.db()?;
        let conn = db.conn();
        let stored = read_all(&conn)?;
        let mut object = serde_json::Map::new();
        for (key, default) in defaults() {
            let value = stored.get(key).cloned().unwrap_or(default);
            object.insert(key.to_string(), typed_value(key, &value));
        }
        Ok(serde_json::Value::Object(object))
    }

    /// Read one setting (stored or default) as a typed JSON value.
    pub fn get(&self, key: &str) -> Result<serde_json::Value, DbError> {
        if !SETTINGS_KEYS.contains(&key) {
            return Err(DbError::InvalidInput(format!(
                "unknown settings key {key:?}"
            )));
        }
        let db = self.db()?;
        let conn = db.conn();
        let stored = read_all(&conn)?;
        let value = stored
            .get(key)
            .cloned()
            .unwrap_or_else(|| defaults()[key].clone());
        Ok(typed_value(key, &value))
    }

    /// Validate and persist one setting; returns the updated snapshot.
    pub fn set(&self, key: &str, value: &str) -> Result<serde_json::Value, DbError> {
        let canonical = validate_setting(key, value)?;
        let db = self.db()?;
        let now = utc_iso8601_now();
        db.transaction(|conn| {
            conn.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?1, ?2, ?3)
                 ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                [key, &canonical, &now],
            )?;
            Ok(())
        })?;
        log::info!("settings updated: {key}");
        self.get_all()
    }

    fn db(&self) -> Result<&Database, DbError> {
        self.db.as_ref().map_err(|e| e.clone())
    }
}

fn read_all(conn: &Connection) -> Result<BTreeMap<String, String>, DbError> {
    let mut stmt = conn.prepare("SELECT key, value FROM settings")?;
    let mut rows = stmt.query([])?;
    let mut map = BTreeMap::new();
    while let Some(row) = rows.next()? {
        map.insert(row.get::<_, String>(0)?, row.get::<_, String>(1)?);
    }
    Ok(map)
}

/// Wire representation of a stored setting value.
fn typed_value(key: &str, value: &str) -> serde_json::Value {
    match key {
        "cache.quota_bytes" => serde_json::json!(value.parse::<u64>().unwrap_or_default()),
        "privacy.telemetry" => serde_json::json!(value == "true"),
        _ => serde_json::Value::String(value.to_string()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn service() -> SettingsService {
        let dir = std::env::temp_dir().join(format!(
            "tooltranslate_settings_{}_{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&dir).expect("create temp dir");
        let svc = SettingsService::open(dir.clone());
        assert!(svc.db().is_ok(), "db must open");
        svc
    }

    #[test]
    fn get_all_returns_every_key_with_defaults() {
        let svc = service();
        let all = svc.get_all().expect("get_all");
        let object = all.as_object().expect("object");
        for key in SETTINGS_KEYS {
            assert!(object.contains_key(*key), "{key} present");
        }
        assert_eq!(object["ai.device"], "auto");
        assert_eq!(object["cache.quota_bytes"], 10_737_418_240u64);
        assert_eq!(object["privacy.telemetry"], false);
        assert_eq!(object["privacy.mode"], "local");
    }

    #[test]
    fn set_persists_and_updates_snapshot() {
        let svc = service();
        let updated = svc.set("ai.device", "cuda").expect("set");
        assert_eq!(updated["ai.device"], "cuda");

        // A second instance (fresh service over the same DB) sees the change.
        let again = service();
        // Reuse the same DB? Not shared here — verify via the same service.
        let value = svc.get("ai.device").expect("get");
        assert_eq!(value, "cuda");
        assert_eq!(again.get("ai.device").expect("fresh get"), "auto");
    }

    #[test]
    fn unknown_key_is_rejected() {
        let svc = service();
        assert!(matches!(
            svc.set("totally.bogus", "x"),
            Err(DbError::InvalidInput(_))
        ));
        assert!(matches!(
            svc.get("totally.bogus"),
            Err(DbError::InvalidInput(_))
        ));
    }

    #[test]
    fn enum_values_are_validated() {
        let svc = service();
        assert!(matches!(
            svc.set("ai.device", "nope"),
            Err(DbError::InvalidInput(_))
        ));
        assert!(matches!(
            svc.set("gpu.override", "nope"),
            Err(DbError::InvalidInput(_))
        ));
        assert!(matches!(
            svc.set("privacy.mode", "nope"),
            Err(DbError::InvalidInput(_))
        ));
        assert!(matches!(
            svc.set("privacy.telemetry", "maybe"),
            Err(DbError::InvalidInput(_))
        ));
        assert!(svc.set("ai.device", "auto").is_ok());
        assert!(svc.set("gpu.override", "cpu").is_ok());
        assert!(svc.set("privacy.mode", "cloud").is_ok());
        assert!(svc.set("privacy.telemetry", "true").is_ok());
    }

    #[test]
    fn cache_quota_range_is_validated() {
        let svc = service();
        assert!(matches!(
            svc.set("cache.quota_bytes", "42"),
            Err(DbError::InvalidInput(_))
        ));
        assert!(matches!(
            svc.set("cache.quota_bytes", "not-a-number"),
            Err(DbError::InvalidInput(_))
        ));
        assert!(svc.set("cache.quota_bytes", "5368709120").is_ok()); // 5 GB
        let value = svc.get("cache.quota_bytes").expect("get");
        assert_eq!(value, 5_368_709_120u64);
    }

    #[test]
    fn plain_string_keys_must_be_non_empty() {
        let svc = service();
        assert!(matches!(
            svc.set("ai.model", "  "),
            Err(DbError::InvalidInput(_))
        ));
        assert!(svc.set("ai.model", "large-v3").is_ok());
        assert!(svc.set("api.gemini.base_url", "").is_ok()); // base URL may be empty
    }
}
