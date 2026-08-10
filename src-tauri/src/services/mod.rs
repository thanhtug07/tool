//! Core services of the Rust layer.
//!
//! TASK-006 adds the Python sidecar lifecycle: `worker_manager` (process
//! lifecycle: spawn / READY / restart / shutdown) and `worker_client`
//! (authenticated loopback HTTP). TASK-008 adds `project_service`
//! (SQLite-backed project CRUD + working directories).

pub mod project_service;
pub mod worker_client;
pub mod worker_manager;

#[cfg(test)]
mod contract_tests;
