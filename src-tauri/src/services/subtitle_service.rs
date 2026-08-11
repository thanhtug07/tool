//! SubtitleService (TASK-025): cue persistence for the subtitle editor.
//!
//! Backs ``subtitle.*`` IPC commands. Cues are project-scoped and ordered by
//! ``cue_number``. The pipeline imports a complete cue set atomically
//! (``replace_project`` — delete + insert inside one transaction), and editor
//! edits land through ``update_cue`` with value validation and an
//! ``E_NOT_FOUND``-style error (DbError::NotFound) for stale ids.

use std::path::PathBuf;

use crate::db::repo::subtitle::{SubtitleCue, SubtitleRepo};
use crate::db::{is_valid_uuid_v4, new_uuid_v4, utc_iso8601_now, Database, DbError};

const MAX_TEXT_LEN: usize = 2000;
const MAX_SPEAKER_LEN: usize = 100;

/// Valid subtitle cue statuses (matching the editor's status column).
pub const CUE_STATUSES: &[&str] = &["draft", "translated", "edited", "approved"];

/// Editor patch: any subset of the user-editable fields.
#[derive(Debug, Clone, Default, serde::Deserialize)]
pub struct CuePatch {
    pub start: Option<f64>,
    pub end: Option<f64>,
    pub text: Option<String>,
    #[serde(default)]
    pub speaker: Option<String>,
    #[serde(default)]
    pub status: Option<String>,
}

/// A cue as supplied by the pipeline import (no backend-assigned fields yet).
#[derive(Debug, Clone, serde::Deserialize)]
pub struct CueInput {
    pub cue_number: i64,
    pub start: f64,
    pub end: f64,
    pub text: String,
    #[serde(default)]
    pub speaker: Option<String>,
    #[serde(default)]
    pub source_text: Option<String>,
}

/// Subtitle cue persistence, managed as Tauri app state.
pub struct SubtitleService {
    db: Result<Database, DbError>,
}

impl SubtitleService {
    /// Open the persistence layer rooted at `data_dir` (same `app.db`).
    pub fn open(data_dir: PathBuf) -> Self {
        let db = Database::open(&data_dir.join("app.db"));
        if let Err(e) = &db {
            log::error!("subtitle database init failed: {e}");
        }
        Self { db }
    }

    fn db(&self) -> Result<&Database, DbError> {
        self.db.as_ref().map_err(|e| e.clone())
    }

    /// All cues of a project, ordered by cue number.
    pub fn list(&self, project_id: &str) -> Result<Vec<SubtitleCue>, DbError> {
        let project_id = validate_id(project_id)?;
        let conn = self.db()?.conn();
        SubtitleRepo::new(&conn).list(&project_id)
    }

    /// Atomically replace a project's cues (import from SubtitleEngine output).
    pub fn replace_project(&self, project_id: &str, cues: Vec<CueInput>) -> Result<usize, DbError> {
        let project_id = validate_id(project_id)?;
        if cues.is_empty() {
            return Err(DbError::InvalidInput(
                "subtitle queue must not be empty".into(),
            ));
        }
        let mut rows: Vec<SubtitleCue> = Vec::with_capacity(cues.len());
        let now = utc_iso8601_now();
        for (offset, input) in cues.into_iter().enumerate() {
            let input = validate_input(input, offset)?;
            rows.push(SubtitleCue {
                id: new_uuid_v4()?,
                project_id: project_id.clone(),
                cue_number: input.cue_number,
                start: input.start,
                end: input.end,
                text: input.text,
                speaker: input.speaker,
                source_text: input.source_text,
                status: "draft".into(),
                style_json: None,
                updated_at: now.clone(),
            });
        }
        self.db()?.transaction(|conn| {
            SubtitleRepo::new(conn).delete_project(&project_id)?;
            SubtitleRepo::new(conn).insert_many(&rows)?;
            Ok(())
        })?;
        Ok(rows.len())
    }

    /// Apply an editor patch to one cue. `DbError::NotFound` when the id is gone.
    pub fn update_cue(&self, id: &str, patch: CuePatch) -> Result<SubtitleCue, DbError> {
        if !is_valid_uuid_v4(id) {
            return Err(DbError::InvalidInput(format!("invalid cue id: {id:?}")));
        }
        if let Some(text) = &patch.text {
            validate_text(text)?;
        }
        if let Some(status) = &patch.status {
            if !CUE_STATUSES.contains(&status.as_str()) {
                return Err(DbError::InvalidInput(format!(
                    "invalid cue status: {status:?}"
                )));
            }
        }
        if let Some(speaker) = &patch.speaker {
            if speaker.chars().count() > MAX_SPEAKER_LEN {
                return Err(DbError::InvalidInput(format!(
                    "speaker exceeds {MAX_SPEAKER_LEN} characters"
                )));
            }
        }
        if let Some(start) = patch.start {
            if start < 0.0 {
                return Err(DbError::InvalidInput("cue start must be >= 0".into()));
            }
        }
        if let Some(end) = patch.end {
            if end < 0.0 {
                return Err(DbError::InvalidInput("cue end must be >= 0".into()));
            }
        }
        if let (Some(s), Some(e)) = (patch.start, patch.end) {
            if e < s {
                return Err(DbError::InvalidInput("cue end must be >= cue start".into()));
            }
        }

        let conn = self.db()?.conn();
        let repo = SubtitleRepo::new(&conn);
        let existing = repo
            .get(id)?
            .ok_or_else(|| DbError::NotFound(format!("cue {id:?} does not exist")))?;
        let new_start = patch.start.unwrap_or(existing.start);
        let new_end = patch.end.unwrap_or(existing.end);
        if new_end < new_start {
            return Err(DbError::InvalidInput("cue end must be >= cue start".into()));
        }
        let new_text = patch.text.map_or(existing.text, |t| t);
        let new_speaker = if patch.speaker.is_some() {
            patch.speaker
        } else {
            existing.speaker
        };
        let new_status = patch.status.unwrap_or(existing.status);
        repo.update(
            id,
            new_start,
            new_end,
            &new_text,
            new_speaker.as_deref(),
            &new_status,
            &utc_iso8601_now(),
        )?
        .ok_or_else(|| DbError::NotFound(format!("cue {id:?} does not exist")))
    }
}

fn validate_id(id: &str) -> Result<String, DbError> {
    if !is_valid_uuid_v4(id) {
        return Err(DbError::InvalidInput(format!("invalid project id: {id:?}")));
    }
    Ok(id.to_string())
}

fn validate_text(text: &str) -> Result<(), DbError> {
    if text.trim().is_empty() {
        return Err(DbError::InvalidInput("cue text must not be empty".into()));
    }
    if text.chars().count() > MAX_TEXT_LEN {
        return Err(DbError::InvalidInput(format!(
            "cue text exceeds {MAX_TEXT_LEN} characters"
        )));
    }
    Ok(())
}

fn validate_input(input: CueInput, offset: usize) -> Result<CueInput, DbError> {
    if input.cue_number < 1 {
        return Err(DbError::InvalidInput(format!(
            "cue_number must be >= 1 (position {offset})"
        )));
    }
    if input.start < 0.0 || input.end < 0.0 {
        return Err(DbError::InvalidInput(format!(
            "cue times must be >= 0 (position {offset})"
        )));
    }
    if input.end < input.start {
        return Err(DbError::InvalidInput(format!(
            "cue end must be >= start (position {offset})"
        )));
    }
    validate_text(&input.text)?;
    if let Some(speaker) = &input.speaker {
        if speaker.chars().count() > MAX_SPEAKER_LEN {
            return Err(DbError::InvalidInput(format!(
                "speaker exceeds {MAX_SPEAKER_LEN} characters"
            )));
        }
    }
    Ok(input)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db::new_uuid_v4;

    fn service(label: &str) -> (SubtitleService, std::path::PathBuf) {
        let dir = std::env::temp_dir().join(format!(
            "tooltranslate_sub_svc_{label}_{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        let svc = SubtitleService::open(dir.clone());
        assert!(svc.db().is_ok(), "db must open cleanly for tests");
        (svc, dir)
    }

    fn seed_project(svc: &SubtitleService) -> String {
        let pid = new_uuid_v4().unwrap();
        let conn = svc.db().expect("db").conn();
        conn.execute(
            "INSERT INTO projects (id, name, source_video_path, status, created_at, updated_at)
             VALUES (?1, 'seed', 'v.mp4', 'draft', 't0', 't0')",
            rusqlite::params![pid],
        )
        .expect("seed project");
        pid
    }

    fn cue_input(number: i64, text: &str) -> CueInput {
        CueInput {
            cue_number: number,
            start: number as f64,
            end: number as f64 + 1.0,
            text: text.into(),
            speaker: Some("A".into()),
            source_text: Some("source".into()),
        }
    }

    #[test]
    fn replace_and_list_roundtrip() {
        let (svc, dir) = service("replace");
        let pid = seed_project(&svc);
        let n = svc
            .replace_project(&pid, vec![cue_input(1, "một"), cue_input(2, "hai")])
            .expect("replace");
        assert_eq!(n, 2);
        let cues = svc.list(&pid).expect("list");
        assert_eq!(cues.len(), 2);
        assert_eq!(cues[0].cue_number, 1);
        assert_eq!(cues[1].text, "hai");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn replace_is_atomic_overwrite() {
        let (svc, dir) = service("overwrite");
        let pid = seed_project(&svc);
        svc.replace_project(&pid, vec![cue_input(1, "cũ")])
            .expect("first");
        svc.replace_project(&pid, vec![cue_input(1, "mới")])
            .expect("second");
        let cues = svc.list(&pid).expect("list");
        assert_eq!(cues.len(), 1);
        assert_eq!(cues[0].text, "mới");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn empty_replace_is_rejected() {
        let (svc, dir) = service("empty");
        let pid = seed_project(&svc);
        assert!(matches!(
            svc.replace_project(&pid, vec![]),
            Err(DbError::InvalidInput(_))
        ));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn update_cue_patches_text_and_persists() {
        let (svc, dir) = service("edit");
        let pid = seed_project(&svc);
        svc.replace_project(&pid, vec![cue_input(1, "gốc")])
            .expect("replace");
        let cue = svc.list(&pid).expect("list").remove(0);

        let updated = svc
            .update_cue(
                &cue.id,
                CuePatch {
                    text: Some("sửa".into()),
                    ..Default::default()
                },
            )
            .expect("update");
        assert_eq!(updated.text, "sửa");
        assert_eq!(updated.status, "draft");
        // Refresh from DB: the edit survived.
        let refreshed = svc.list(&pid).expect("list");
        assert_eq!(refreshed[0].text, "sửa");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn update_cue_patches_timing_and_status() {
        let (svc, dir) = service("timing");
        let pid = seed_project(&svc);
        svc.replace_project(&pid, vec![cue_input(1, "x")])
            .expect("replace");
        let cue = svc.list(&pid).expect("list").remove(0);
        let updated = svc
            .update_cue(
                &cue.id,
                CuePatch {
                    start: Some(5.0),
                    end: Some(7.5),
                    status: Some("approved".into()),
                    ..Default::default()
                },
            )
            .expect("update");
        assert_eq!(updated.start, 5.0);
        assert_eq!(updated.end, 7.5);
        assert_eq!(updated.status, "approved");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn update_cue_rejects_invalid_values() {
        let (svc, dir) = service("invalid");
        let pid = seed_project(&svc);
        svc.replace_project(&pid, vec![cue_input(1, "x")])
            .expect("replace");
        let cue = svc.list(&pid).expect("list").remove(0);
        assert!(matches!(
            svc.update_cue(
                &cue.id,
                CuePatch {
                    start: Some(9.0),
                    end: Some(5.0),
                    ..Default::default()
                }
            ),
            Err(DbError::InvalidInput(_))
        ));
        assert!(matches!(
            svc.update_cue(
                &cue.id,
                CuePatch {
                    text: Some("  ".into()),
                    ..Default::default()
                }
            ),
            Err(DbError::InvalidInput(_))
        ));
        assert!(matches!(
            svc.update_cue(
                &cue.id,
                CuePatch {
                    status: Some("nope".into()),
                    ..Default::default()
                }
            ),
            Err(DbError::InvalidInput(_))
        ));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn update_cue_missing_id_is_not_found() {
        let (svc, dir) = service("missing");
        assert!(matches!(
            svc.update_cue("00000000-0000-4000-8000-000000000000", CuePatch::default()),
            Err(DbError::NotFound(_))
        ));
        let _ = std::fs::remove_dir_all(&dir);
    }
}
