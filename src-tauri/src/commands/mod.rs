//! IPC command modules exposed to the frontend via `invoke`.
//! TASK-004: first typed bridge (`system::ping`). TASK-006: worker state.
//! TASK-008: project CRUD. TASK-010: job lifecycle. TASK-029: export.
//! TASK-030: settings + secrets.

pub mod dictionary;
pub mod export;
pub mod job;
pub mod pipeline;
pub mod project;
pub mod provider;
pub mod settings;
pub mod subtitle;
pub mod system;
pub mod worker;
