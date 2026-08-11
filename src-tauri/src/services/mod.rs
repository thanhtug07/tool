//! Core services of the Rust layer.
//!
//! TASK-006 adds the Python sidecar lifecycle: `worker_manager` (process
//! lifecycle: spawn / READY / restart / shutdown) and `worker_client`
//! (authenticated loopback HTTP). TASK-008 adds `project_service`
//! (SQLite-backed project CRUD + working directories). TASK-010 adds
//! `job_service` (pipeline job orchestrator). TASK-011 adds `cache_service`
//! (content-addressed cache, quota LRU, downstream invalidation). TASK-014
//! adds `hardware_probe` (GPU/VRAM/RAM/FFmpeg detect + strategy input for
//! MASTER_PLAN §14.2).

pub mod cache_service;
pub mod dictionary_service;
pub mod hardware_probe;
pub mod job_service;
pub mod project_service;
pub mod worker_client;
pub mod worker_manager;

#[cfg(test)]
mod contract_tests;
