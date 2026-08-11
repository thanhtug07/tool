//! Security primitives (TASK-030).
//!
//! `secret_store` keeps API keys in the OS credential vault (Windows
//! Credential Manager via the `keyring` crate). FIX #8 (frozen in
//! `ARCHITECTURE_DECISION.md` / `MASTER_PLAN.md` §20): when the credential
//! service is unavailable the app BLOCKS the key save with a clear message and
//! deliberately does NOT fall back to an encrypted file or custom crypto.

pub mod secret_store;
