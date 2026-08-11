//! SecretStore (TASK-030): API keys in the OS credential vault.
//!
//! Architecture (frozen — `ARCHITECTURE_DECISION.md` §3.5 / `MASTER_PLAN.md`
//! §20.7): keys are stored ONLY in the OS credential service (Windows
//! Credential Manager) via `keyring`, never in the SQLite DB and never in any
//! file. FIX #8 fail-safe: if the credential service is unavailable (no
//! DPAPI / vault service / headless CI), `set_api_key` FAILS with a clear
//! user-facing error and the app shows it — there is intentionally NO
//! encrypted-file or custom-crypto fallback.
//!
//! Security rules enforced here:
//! - The full key never crosses back to the frontend: the IPC surface only
//!   returns a masked form (`get_api_key_masked`).
//! - Keys are never logged (no `Debug` impl carries the secret; the command
//!   layer must not print request args).
//! - Providers are validated against a fixed allow-list so the service/user
//!   names fed to `keyring` cannot be attacker-controlled.

/// Provider allow-list (MVP: Gemini + local LLM; OpenAI is post-MVP but kept
/// valid so the UI can store a key without a code change).
pub const PROVIDERS: &[&str] = &["gemini", "local", "openai"];

/// Credential service/username naming. The service is the app identifier; the
/// user component is the provider name (validated, short, ASCII).
const CREDENTIAL_SERVICE: &str = "com.tooltranslatechina.studio";
const MAX_KEY_CHARS: usize = 4096;

/// Failure surfacing an unusable OS credential service (FIX #8).
#[derive(Debug, Clone, PartialEq)]
pub enum SecretStoreError {
    /// The OS credential service is unavailable or rejected the operation.
    /// The key was NOT stored. No fallback is attempted (FIX #8).
    Unavailable(String),
    /// Invalid input (unknown provider, empty/oversized key).
    InvalidInput(String),
    /// No stored credential for this provider.
    NotFound,
}

impl std::fmt::Display for SecretStoreError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SecretStoreError::Unavailable(m) => write!(
                f,
                "{m} The OS credential service is not available, so the key was not saved."
            ),
            SecretStoreError::InvalidInput(m) => write!(f, "invalid input: {m}"),
            SecretStoreError::NotFound => write!(f, "no API key stored for this provider"),
        }
    }
}

impl std::error::Error for SecretStoreError {}

/// Low-level credential vault abstraction.
///
/// The real implementation talks to `keyring` (Windows Credential Manager);
/// tests use an in-memory mock so the round-trip contract is verified without
/// requiring a desktop credential service.
pub trait CredentialVault: Send + Sync {
    fn set(&self, service: &str, user: &str, secret: &str) -> Result<(), SecretStoreError>;
    fn get(&self, service: &str, user: &str) -> Result<Option<String>, SecretStoreError>;
    fn delete(&self, service: &str, user: &str) -> Result<(), SecretStoreError>;
}

/// `keyring`-backed vault (Windows Credential Manager).
struct KeyringVault;

impl CredentialVault for KeyringVault {
    fn set(&self, service: &str, user: &str, secret: &str) -> Result<(), SecretStoreError> {
        let entry = keyring::Entry::new(service, user)
            .map_err(|e| SecretStoreError::Unavailable(e.to_string()))?;
        entry
            .set_password(secret)
            .map_err(|e| SecretStoreError::Unavailable(e.to_string()))
    }

    fn get(&self, service: &str, user: &str) -> Result<Option<String>, SecretStoreError> {
        let entry = keyring::Entry::new(service, user)
            .map_err(|e| SecretStoreError::Unavailable(e.to_string()))?;
        match entry.get_password() {
            Ok(secret) => Ok(Some(secret)),
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(e) => Err(SecretStoreError::Unavailable(e.to_string())),
        }
    }

    fn delete(&self, service: &str, user: &str) -> Result<(), SecretStoreError> {
        let entry = keyring::Entry::new(service, user)
            .map_err(|e| SecretStoreError::Unavailable(e.to_string()))?;
        match entry.delete_credential() {
            Ok(()) => Ok(()),
            Err(keyring::Error::NoEntry) => Err(SecretStoreError::NotFound),
            Err(e) => Err(SecretStoreError::Unavailable(e.to_string())),
        }
    }
}

/// Provider-scoped secret storage, managed as Tauri app state.
pub struct SecretStore {
    vault: Box<dyn CredentialVault>,
}

impl SecretStore {
    /// Credential-vault-backed store.
    pub fn new() -> Self {
        Self {
            vault: Box::new(KeyringVault),
        }
    }

    /// Store an API key for `provider` in the OS credential vault.
    ///
    /// FIX #8: on credential-service failure the error is returned — nothing is
    /// written anywhere else and no fallback exists.
    pub fn set_api_key(&self, provider: &str, key: &str) -> Result<(), SecretStoreError> {
        let provider = validate_provider(provider)?;
        if key.is_empty() {
            return Err(SecretStoreError::InvalidInput(
                "API key must not be empty".into(),
            ));
        }
        if key.len() > MAX_KEY_CHARS {
            return Err(SecretStoreError::InvalidInput(format!(
                "API key exceeds {MAX_KEY_CHARS} characters"
            )));
        }
        self.vault.set(CREDENTIAL_SERVICE, &provider, key)
    }

    /// Whether a key exists for `provider` (no secret is returned).
    pub fn has_api_key(&self, provider: &str) -> Result<bool, SecretStoreError> {
        let provider = validate_provider(provider)?;
        match self.vault.get(CREDENTIAL_SERVICE, &provider) {
            Ok(Some(_)) => Ok(true),
            Ok(None) => Ok(false),
            Err(e) => Err(e),
        }
    }

    /// Masked form of the stored key for display, e.g. `AIz****abcd`.
    ///
    /// The full secret NEVER crosses the IPC boundary — the frontend only ever
    /// receives this masked string (or `None` when no key is stored).
    pub fn get_api_key_masked(&self, provider: &str) -> Result<Option<String>, SecretStoreError> {
        let provider = validate_provider(provider)?;
        match self.vault.get(CREDENTIAL_SERVICE, &provider)? {
            Some(secret) => Ok(Some(mask_key(&secret))),
            None => Ok(None),
        }
    }

    /// Remove the stored key for `provider`.
    pub fn delete_api_key(&self, provider: &str) -> Result<(), SecretStoreError> {
        let provider = validate_provider(provider)?;
        self.vault.delete(CREDENTIAL_SERVICE, &provider)
    }
}

impl Default for SecretStore {
    fn default() -> Self {
        Self::new()
    }
}

fn validate_provider(provider: &str) -> Result<String, SecretStoreError> {
    if PROVIDERS.contains(&provider) {
        Ok(provider.to_string())
    } else {
        Err(SecretStoreError::InvalidInput(format!(
            "unknown provider {provider:?}"
        )))
    }
}

/// Mask a secret for display: first 3 chars + `****` + last 4 chars.
/// Short secrets degrade to a fixed `****` so no prefix leaks.
pub fn mask_key(key: &str) -> String {
    if key.len() <= 8 {
        return "****".to_string();
    }
    let prefix: String = key.chars().take(3).collect();
    let suffix: String = key
        .chars()
        .rev()
        .take(4)
        .collect::<Vec<_>>()
        .into_iter()
        .rev()
        .collect();
    format!("{prefix}****{suffix}")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;
    use std::sync::Mutex;

    /// In-memory vault: round-trips the exact contract without touching the OS.
    #[derive(Default)]
    struct MemoryVault(Mutex<HashMap<(String, String), String>>);

    impl CredentialVault for MemoryVault {
        fn set(&self, service: &str, user: &str, secret: &str) -> Result<(), SecretStoreError> {
            self.0
                .lock()
                .unwrap()
                .insert((service.to_string(), user.to_string()), secret.to_string());
            Ok(())
        }

        fn get(&self, service: &str, user: &str) -> Result<Option<String>, SecretStoreError> {
            Ok(self
                .0
                .lock()
                .unwrap()
                .get(&(service.to_string(), user.to_string()))
                .cloned())
        }

        fn delete(&self, service: &str, user: &str) -> Result<(), SecretStoreError> {
            let mut map = self.0.lock().unwrap();
            if map
                .remove(&(service.to_string(), user.to_string()))
                .is_some()
            {
                Ok(())
            } else {
                Err(SecretStoreError::NotFound)
            }
        }
    }

    fn store() -> SecretStore {
        SecretStore {
            vault: Box::new(MemoryVault::default()),
        }
    }

    #[test]
    fn roundtrip_set_has_get_masked_delete() {
        let store = store();
        store
            .set_api_key("gemini", "AIzaSy-0123456789abcdefghijklmnopqrstuvwxyz")
            .expect("set");
        assert!(store.has_api_key("gemini").expect("has"));
        let masked = store.get_api_key_masked("gemini").expect("masked");
        assert_eq!(masked.as_deref(), Some("AIz****wxyz"));
        // The masked form must never contain the middle of the secret.
        assert!(!masked.unwrap().contains("abcdefghijklmn"));
        store.delete_api_key("gemini").expect("delete");
        assert!(!store.has_api_key("gemini").expect("has after delete"));
    }

    #[test]
    fn missing_key_reports_none_not_error() {
        let store = store();
        assert!(!store.has_api_key("gemini").expect("has"));
        assert_eq!(store.get_api_key_masked("gemini").expect("masked"), None);
    }

    #[test]
    fn unknown_provider_is_rejected() {
        let store = store();
        assert!(matches!(
            store.set_api_key("../../etc", "secret"),
            Err(SecretStoreError::InvalidInput(_))
        ));
        assert!(matches!(
            store.has_api_key("bogus"),
            Err(SecretStoreError::InvalidInput(_))
        ));
    }

    #[test]
    fn empty_or_oversized_key_is_rejected() {
        let store = store();
        assert!(matches!(
            store.set_api_key("gemini", ""),
            Err(SecretStoreError::InvalidInput(_))
        ));
        let long = "x".repeat(MAX_KEY_CHARS + 1);
        assert!(matches!(
            store.set_api_key("gemini", &long),
            Err(SecretStoreError::InvalidInput(_))
        ));
    }

    #[test]
    fn mask_key_never_exposes_the_middle() {
        assert_eq!(mask_key("AIzaSy-0123456789wxyz"), "AIz****wxyz");
        assert_eq!(mask_key("short"), "****");
        assert_eq!(mask_key(""), "****");
        assert_eq!(mask_key("abcdefgh"), "****");
    }

    #[test]
    fn providers_are_allowlisted() {
        assert_eq!(PROVIDERS, &["gemini", "local", "openai"]);
    }
}
