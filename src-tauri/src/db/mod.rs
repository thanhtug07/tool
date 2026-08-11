//! SQLite database layer (TASK-008).
//!
//! Architecture (frozen in `ARCHITECTURE_DECISION.md` §2.3 / `MASTER_PLAN.md`
//! §17.1):
//! - SQLite in **WAL** journal mode, located in the OS user-data directory.
//! - Schema **versioned** via `PRAGMA user_version`; migrations are applied in
//!   order and gated so concurrent app instances never double-apply them.
//! - `foreign_keys` enabled on every connection.
//! - Only the Rust core writes the DB (the Python worker talks over HTTP), which
//!   keeps lock contention low; WAL + a `busy_timeout` still let two app
//!   instances open and write the same file without silently overwriting each
//!   other's rows.

pub mod migrations;
pub mod repo;

use std::fs;
use std::path::Path;
use std::sync::Mutex;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use rusqlite::{Connection, TransactionBehavior};

pub use migrations::{run_migrations, MIGRATIONS};
pub use repo::characters::{CharacterEntry, CharacterRepo};
pub use repo::glossary::{GlossaryEntry, GlossaryRepo};
pub use repo::job::{Job, JobStatus, JobType};
pub use repo::project::{Project, ProjectStatus};
pub use repo::subtitle::{SubtitleCue, SubtitleRepo};

/// How long a connection waits for a write lock before failing (WAL readers do
/// not block, so this only matters for concurrent writers).
const BUSY_TIMEOUT: Duration = Duration::from_secs(5);

/// Error type for the whole persistence stack (open, migrations, repos).
#[derive(Debug, Clone)]
pub enum DbError {
    Io(String),
    Sqlite(String),
    Migration(String),
    InvalidInput(String),
    NotFound(String),
    Conflict(String),
}

impl std::fmt::Display for DbError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            DbError::Io(m) => write!(f, "io error: {m}"),
            DbError::Sqlite(m) => write!(f, "sqlite error: {m}"),
            DbError::Migration(m) => write!(f, "migration error: {m}"),
            DbError::InvalidInput(m) => write!(f, "invalid input: {m}"),
            DbError::NotFound(m) => write!(f, "not found: {m}"),
            DbError::Conflict(m) => write!(f, "conflict: {m}"),
        }
    }
}

impl std::error::Error for DbError {}

impl From<rusqlite::Error> for DbError {
    fn from(e: rusqlite::Error) -> Self {
        DbError::Sqlite(e.to_string())
    }
}

impl From<std::io::Error> for DbError {
    fn from(e: std::io::Error) -> Self {
        DbError::Io(e.to_string())
    }
}

/// A single SQLite connection configured per the frozen architecture.
///
/// The connection is wrapped in a `Mutex` because rusqlite `Connection` is
/// `Send` but not `Sync`, while Tauri commands may run on any thread.
pub struct Database {
    conn: Mutex<Connection>,
}

impl Database {
    /// Open (creating if needed) the database at `db_path` and bring it to the
    /// latest schema version. The parent directory is created on demand.
    pub fn open(db_path: &Path) -> Result<Self, DbError> {
        if let Some(parent) = db_path.parent() {
            fs::create_dir_all(parent)?;
        }
        let conn = Connection::open(db_path)?;
        Self::from_connection(conn)
    }

    /// Wrap an existing connection (file or in-memory) and apply the schema.
    pub fn from_connection(mut conn: Connection) -> Result<Self, DbError> {
        // `journal_mode` must be set outside any transaction.
        conn.pragma_update(None, "journal_mode", "WAL")?;
        conn.pragma_update(None, "foreign_keys", true)?;
        conn.busy_timeout(BUSY_TIMEOUT)?;
        run_migrations(&mut conn)?;
        Ok(Self {
            conn: Mutex::new(conn),
        })
    }

    /// Borrow the underlying connection (serialized across threads).
    pub fn conn(&self) -> std::sync::MutexGuard<'_, Connection> {
        self.conn.lock().unwrap()
    }

    /// Journal mode of the underlying file (e.g. `"wal"`). Diagnostics/tests.
    pub fn journal_mode(&self) -> Result<String, DbError> {
        let conn = self.conn();
        Ok(conn.pragma_query_value(None, "journal_mode", |r| r.get::<_, String>(0))?)
    }

    /// Whether `PRAGMA foreign_keys` is enabled. Diagnostics/tests.
    pub fn foreign_keys_on(&self) -> Result<bool, DbError> {
        let conn = self.conn();
        Ok(conn.pragma_query_value(None, "foreign_keys", |r| r.get::<_, bool>(0))?)
    }

    /// Latest applied schema version (`PRAGMA user_version`). Diagnostics/tests.
    pub fn schema_version(&self) -> Result<i64, DbError> {
        let conn = self.conn();
        Ok(conn.pragma_query_value(None, "user_version", |r| r.get::<_, i64>(0))?)
    }

    /// Run `f` inside an IMMEDIATE transaction (the write lock is taken up
    /// front so concurrent app instances serialize cleanly) and commit.
    pub fn transaction<T>(
        &self,
        f: impl FnOnce(&Connection) -> Result<T, DbError>,
    ) -> Result<T, DbError> {
        let mut conn = self.conn();
        let tx = conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let result = f(&tx)?;
        tx.commit()?;
        Ok(result)
    }
}

/// Generate a random UUID v4 using OS randomness (`getrandom`).
pub fn new_uuid_v4() -> Result<String, DbError> {
    let mut bytes = [0u8; 16];
    getrandom::getrandom(&mut bytes).map_err(|e| DbError::Io(e.to_string()))?;
    bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
    bytes[8] = (bytes[8] & 0x3f) | 0x80; // RFC 4122 variant
    let h = bytes.iter().map(|b| format!("{b:02x}")).collect::<String>();
    Ok(format!(
        "{}-{}-{}-{}-{}",
        &h[0..8],
        &h[8..12],
        &h[12..16],
        &h[16..20],
        &h[20..32]
    ))
}

/// Validate a UUID v4 (lowercase hex, version nibble `4`, RFC 4122 variant).
///
/// Used to validate every project id crossing the IPC boundary. Because project
/// ids become directory names, the strict format also rules out path traversal.
pub fn is_valid_uuid_v4(id: &str) -> bool {
    let b = id.as_bytes();
    if b.len() != 36 {
        return false;
    }
    for (i, ch) in b.iter().enumerate() {
        match i {
            8 | 13 | 18 | 23 => {
                if *ch != b'-' {
                    return false;
                }
            }
            _ => {
                if !ch.is_ascii_hexdigit() || ch.is_ascii_uppercase() {
                    return false;
                }
            }
        }
    }
    b[14] == b'4' && matches!(b[19], b'8' | b'9' | b'a' | b'b')
}

/// Current UTC time as an ISO-8601 string with millisecond precision
/// (`YYYY-MM-DDTHH:MM:SS.mmmZ`), matching the canonical `Project` schema.
pub fn utc_iso8601_now() -> String {
    let millis = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as i128)
        .unwrap_or(0);
    format_millis(millis)
}

/// Format epoch milliseconds as `YYYY-MM-DDTHH:MM:SS.mmmZ` (no external deps).
fn format_millis(millis: i128) -> String {
    let secs = millis.div_euclid(1000);
    let ms = millis.rem_euclid(1000);
    let days = secs.div_euclid(86_400);
    let secs_of_day = secs.rem_euclid(86_400);
    let (year, month, day) = civil_from_days(days as i64);
    let (hh, mm, ss) = (
        secs_of_day / 3600,
        (secs_of_day % 3600) / 60,
        secs_of_day % 60,
    );
    format!("{year:04}-{month:02}-{day:02}T{hh:02}:{mm:02}:{ss:02}.{ms:03}Z")
}

/// Days-since-epoch → (year, month, day) in the proleptic Gregorian calendar
/// (Howard Hinnant's `civil_from_days` algorithm).
fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    ((if m <= 2 { y + 1 } else { y }), m as u32, d as u32)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db::repo::project::ProjectRepo;

    fn temp_dir(label: &str) -> std::path::PathBuf {
        let dir =
            std::env::temp_dir().join(format!("tooltranslate_db_{label}_{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).expect("create temp dir");
        dir
    }

    #[test]
    fn opens_with_wal_and_foreign_keys() {
        let dir = temp_dir("pragmas");
        let db = Database::open(&dir.join("app.db")).expect("open");
        assert_eq!(db.journal_mode().unwrap(), "wal");
        assert!(db.foreign_keys_on().unwrap());
        assert_eq!(db.schema_version().unwrap(), MIGRATIONS.len() as i64);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn reopen_is_idempotent() {
        let dir = temp_dir("reopen");
        let path = dir.join("app.db");
        Database::open(&path).expect("open first");
        let db = Database::open(&path).expect("open second");
        assert_eq!(db.schema_version().unwrap(), MIGRATIONS.len() as i64);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn uuid_v4_is_valid_unique_and_rfc4122() {
        let a = new_uuid_v4().expect("uuid a");
        let b = new_uuid_v4().expect("uuid b");
        assert_ne!(a, b);
        assert!(is_valid_uuid_v4(&a));
        assert!(is_valid_uuid_v4(&b));
        assert!(!is_valid_uuid_v4("not-a-uuid"));
        assert!(!is_valid_uuid_v4(""));
        assert!(!is_valid_uuid_v4(&a.to_uppercase()));
        assert!(!is_valid_uuid_v4(&a.replace('-', "")));
        // wrong version / variant nibbles
        assert!(!is_valid_uuid_v4("00000000-0000-3000-8000-000000000000"));
        assert!(!is_valid_uuid_v4("00000000-0000-4000-7000-000000000000"));
    }

    #[test]
    fn iso8601_formatting_known_values() {
        assert_eq!(format_millis(0), "1970-01-01T00:00:00.000Z");
        assert_eq!(format_millis(1_786_353_300_000), "2026-08-10T09:15:00.000Z");
        // millisecond precision survives sub-second time
        assert_eq!(format_millis(1_000_123), "1970-01-01T00:16:40.123Z");
    }

    #[test]
    fn transaction_rolls_back_atomically() {
        let dir = temp_dir("rollback");
        let db = Database::open(&dir.join("app.db")).expect("open");
        let err = db.transaction(|conn| {
            let repo = ProjectRepo::new(conn);
            let project = Project {
                id: new_uuid_v4().expect("uuid"),
                name: "first".into(),
                source_video_path: "a.mp4".into(),
                status: ProjectStatus::Draft,
                created_at: utc_iso8601_now(),
                updated_at: utc_iso8601_now(),
                settings_json: None,
            };
            repo.insert(&project)?;
            // Re-inserting the same id violates the primary key, so the whole
            // transaction must abort with no partial row visible.
            repo.insert(&project)
        });
        assert!(err.is_err());
        let conn = db.conn();
        let count: i64 = conn
            .query_row("SELECT COUNT(*) FROM projects", [], |r| r.get(0))
            .expect("count");
        assert_eq!(count, 0);
        let _ = fs::remove_dir_all(&dir);
    }
}
