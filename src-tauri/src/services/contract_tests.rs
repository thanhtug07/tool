//! Cross-language contract tests (TASK-007).
//!
//! Parses the canonical example fixtures in `schemas/examples/valid/` into the
//! Rust structs that mirror the JSON Schemas. The same payloads are validated
//! by the Python suite (jsonschema + generated Pydantic models) and type-checked
//! against the TypeScript types, so a passing run here means the wire
//! representation is consistent across all three layers.

use serde_json::json;

use crate::db::{Project, ProjectStatus};

use super::worker_client::{ErrorResponse, HealthResponse};
use super::worker_manager::WorkerStateInfo;

const EXAMPLES: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../schemas/examples/valid");

fn example(name: &str) -> serde_json::Value {
    let raw = std::fs::read_to_string(format!("{EXAMPLES}/{name}.json"))
        .unwrap_or_else(|e| panic!("cannot read {name}.json: {e}"));
    serde_json::from_str(&raw).unwrap_or_else(|e| panic!("cannot parse {name}.json: {e}"))
}

#[test]
fn health_example_parses_into_health_response() {
    let value = example("health");
    let health: HealthResponse = serde_json::from_value(value).expect("health parses");
    assert_eq!(health.status, "ok");
    assert_eq!(health.version, "0.1.0");
    assert_eq!(health.gpu, None);
}

#[test]
fn worker_state_example_parses_and_round_trips() {
    let value = example("worker_state");
    let info: WorkerStateInfo = serde_json::from_value(value).expect("worker state parses");
    use super::worker_manager::WorkerState;
    assert_eq!(info.state, WorkerState::Ready);
    assert_eq!(info.pid, Some(1234));
    assert_eq!(info.port, Some(8765));
    assert_eq!(info.restarts, 0);
    assert_eq!(info.last_error, None);

    // Serialized back out, the canonical field names and lowercase enum survive,
    // and no token-shaped field appears.
    let serialized = serde_json::to_value(&info).expect("worker state serializes");
    assert_eq!(serialized["state"], json!("ready"));
    assert_eq!(serialized["port"], json!(8765));
    assert!(serialized.get("token").is_none());
    assert!(serialized.get("last_error").is_some_and(|v| v.is_null()));
}

#[test]
fn error_example_parses_into_error_response() {
    let value = example("error");
    let response: ErrorResponse = serde_json::from_value(value).expect("error parses");
    assert_eq!(response.error.code, "E_FFMPEG_RENDER");
    assert!(!response.error.message.is_empty());
    assert!(response.error.recoverable);
}

#[test]
fn project_example_parses_into_project_and_round_trips() {
    let value = example("project");
    let project: Project = serde_json::from_value(value).expect("project parses");
    assert_eq!(project.status, ProjectStatus::Draft);
    assert_eq!(project.created_at, "2026-08-10T09:15:00.000Z");
    assert_eq!(project.settings_json, None);

    // Serialized back out, the canonical field names and lowercase enum survive.
    let serialized = serde_json::to_value(&project).expect("project serializes");
    assert_eq!(serialized["status"], json!("draft"));
    assert_eq!(serialized["name"], json!("Bộ phim mẫu"));
    assert_eq!(serialized["created_at"], json!("2026-08-10T09:15:00.000Z"));
}
