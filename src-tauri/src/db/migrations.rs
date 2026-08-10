//! Versioned schema migrations (TASK-008).
//!
//! sqlx-style: each migration is a versioned SQL batch applied once, in order,
//! gated by `PRAGMA user_version`. Each migration runs inside an IMMEDIATE
//! transaction, so two app instances starting concurrently serialize instead of
//! double-applying. Only tables owned by this task live here — TASK-010/011
//! tables are added by their own later migrations.
//!
//! Rules: the list is append-only and applied migrations are never edited.

use rusqlite::{Connection, TransactionBehavior};

use crate::db::DbError;

/// One ordered schema migration.
pub struct Migration {
    /// Version number (equals `PRAGMA user_version` after the migration runs).
    pub version: i64,
    /// Short human-readable name, used in error messages.
    pub name: &'static str,
    /// SQL batch (multiple statements allowed; must not manage its own tx).
    pub sql: &'static str,
}

/// The ordered migration list. Append-only.
pub const MIGRATIONS: &[Migration] = &[
    Migration {
        version: 1,
        name: "create projects table",
        sql: r#"
            CREATE TABLE projects (
                id TEXT PRIMARY KEY,           -- uuid v4
                name TEXT NOT NULL,
                source_video_path TEXT NOT NULL,
                status TEXT NOT NULL,          -- draft/analyzed/transcribed/translated/rendered
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                settings_json TEXT             -- project-level overrides
            );
        "#,
    },
    Migration {
        version: 2,
        name: "index projects.updated_at",
        sql: r#"
            CREATE INDEX idx_projects_updated_at ON projects(updated_at);
        "#,
    },
];

/// Apply every pending migration (versions greater than `user_version`) in order.
pub fn run_migrations(conn: &mut Connection) -> Result<(), DbError> {
    let current = current_version(conn)?;
    for migration in MIGRATIONS.iter().filter(|m| m.version > current) {
        apply_migration(conn, migration)?;
    }
    Ok(())
}

/// Apply one migration atomically and bump `user_version`.
///
/// `pub` so tests can simulate a partial upgrade (v1 applied, v2 pending).
pub fn apply_migration(conn: &mut Connection, migration: &Migration) -> Result<(), DbError> {
    let tx = conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
    tx.execute_batch(migration.sql).map_err(|e| {
        DbError::Migration(format!(
            "migration v{} ({}) failed: {e}",
            migration.version, migration.name
        ))
    })?;
    tx.pragma_update(None, "user_version", migration.version)?;
    tx.commit().map_err(|e| {
        DbError::Migration(format!(
            "commit migration v{} ({}) failed: {e}",
            migration.version, migration.name
        ))
    })?;
    Ok(())
}

/// Current schema version (`PRAGMA user_version`).
pub fn current_version(conn: &Connection) -> Result<i64, DbError> {
    Ok(conn.pragma_query_value(None, "user_version", |r| r.get::<_, i64>(0))?)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn mem_conn() -> Connection {
        Connection::open_in_memory().expect("in-memory sqlite")
    }

    #[test]
    fn fresh_db_reaches_latest_version() {
        let mut conn = mem_conn();
        run_migrations(&mut conn).expect("migrate");
        let v: i64 = conn
            .pragma_query_value(None, "user_version", |r| r.get(0))
            .unwrap();
        assert_eq!(v, MIGRATIONS.len() as i64);
    }

    #[test]
    fn migrations_are_idempotent() {
        let mut conn = mem_conn();
        run_migrations(&mut conn).expect("run");
        run_migrations(&mut conn).expect("re-run");
        let v: i64 = conn
            .pragma_query_value(None, "user_version", |r| r.get(0))
            .unwrap();
        assert_eq!(v, MIGRATIONS.len() as i64);
    }

    #[test]
    fn v1_to_v2_preserves_existing_rows() {
        let mut conn = mem_conn();
        // Simulate a deployment that stopped at v1 with live data...
        apply_migration(&mut conn, &MIGRATIONS[0]).expect("apply v1");
        conn.execute(
            "INSERT INTO projects (id, name, source_video_path, status, created_at, updated_at)
             VALUES ('00000000-0000-4000-8000-000000000000', 'kept row', 'video.mp4', 'draft', 't0', 't0')",
            [],
        )
        .expect("seed row");
        // ...then upgrade to the latest version.
        run_migrations(&mut conn).expect("migrate to latest");
        let v: i64 = conn
            .pragma_query_value(None, "user_version", |r| r.get(0))
            .unwrap();
        assert_eq!(v, MIGRATIONS.len() as i64);

        let kept: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM projects WHERE id = '00000000-0000-4000-8000-000000000000'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(kept, 1, "v1→v2 must not drop data");
    }

    #[test]
    fn user_version_gates_reapplication() {
        let mut conn = mem_conn();
        run_migrations(&mut conn).expect("migrate");
        // A second run (e.g. a second app instance) sees an up-to-date
        // user_version and applies nothing further.
        let applied = run_migrations(&mut conn);
        assert!(applied.is_ok());
        let v: i64 = conn
            .pragma_query_value(None, "user_version", |r| r.get(0))
            .unwrap();
        assert_eq!(v, MIGRATIONS.len() as i64);
    }
}
