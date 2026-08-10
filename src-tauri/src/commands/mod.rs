//! IPC command modules exposed to the frontend via `invoke`.
//! TASK-004: first typed bridge (`system::ping`). TASK-006: worker state.
//! TASK-008: project CRUD.

pub mod project;
pub mod system;
pub mod worker;
