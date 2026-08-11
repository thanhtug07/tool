//! CacheService (TASK-011).
//!
//! Content-addressed cache for the pipeline stages â€” audio / STT / translation /
//! subtitle / render â€” per ARCHITECTURE_DECISION.md Â§3.7 (FROZEN) and
//! MASTER_PLAN.md Â§19.
//!
//! Semantics
//! ---------
//! - **Keys are content-addressed**: changing any input or parameter changes the
//!   key, so "change subtitle style â†’ render cache MISS but STT/translation
//!   still HIT" falls out of the key builder itself (Â§3.7). A param *version*
//!   (glossary version, rules version, style) is part of the key â€” bumping it is
//!   the invalidation primitive.
//! - **Downstream cascade**: `invalidate_downstream` removes every stage at or
//!   after a given stage for a project (editing translation clears subtitle and
//!   render but never touches audio/STT).
//! - **Quota LRU**: eviction is by total size over a configurable quota (default
//!   10 GB), oldest last-accessed first. The index lives in SQLite
//!   (`cache_entries`), the payloads as files in `{data_dir}/cache/`.
//! - **Crash safety**: files are written to a temp name and atomically renamed;
//!   a `get` that finds a stale DB row without a file cleans the row up; orphan
//!   `*.tmp-*` files are swept on open and during eviction.
//!
//! Storage layout
//! --------------
//! ```text
//! {data_dir}/cache/{stage}_{sha256(key)}
//! ```
//! The key itself may contain characters illegal in Windows file names (`:`),
//! so the file name is the SHA-256 of the key â€” never the raw key.
//!
//! SHA-256 is implemented in this module (streaming, no external crate) so the
//! cache adds no dependency to the MVP core.

use std::fs::{self, File};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use crate::db::{is_valid_uuid_v4, utc_iso8601_now, Database, DbError};

/// Pipeline stage order; downstream invalidation removes everything at and
/// after the given stage. Must match `worker/src/services/cache.py`.
pub const STAGE_ORDER: [&str; 5] = ["audio", "stt", "tr", "subtitle", "render"];

/// Default cache quota (ARCHITECTURE_DECISION.md Â§3.7).
pub const DEFAULT_CACHE_QUOTA_BYTES: u64 = 10 * 1024 * 1024 * 1024; // 10 GB

const MAX_KEY_CHARS: usize = 512;
const READ_CHUNK: usize = 64 * 1024;

/// Tunable knobs for the cache service.
#[derive(Debug, Clone)]
pub struct CacheServiceConfig {
    /// LRU eviction threshold over the whole cache root.
    pub max_bytes: u64,
}

impl Default for CacheServiceConfig {
    fn default() -> Self {
        Self {
            max_bytes: DEFAULT_CACHE_QUOTA_BYTES,
        }
    }
}

/// Snapshot of cache usage (diagnostics / IPC surface).
#[derive(Debug, Clone)]
pub struct CacheStats {
    pub entry_count: u64,
    pub total_bytes: u64,
}

/// The cache service, managed as Tauri app state. Owns its own connection to
/// `{data_dir}/app.db` (WAL allows concurrent connections, same as JobService).
///
/// The LRU quota is an atomic so it can be changed at runtime (TASK-030
/// settings) from a shared reference.
pub struct CacheService {
    data_dir: PathBuf,
    db: Result<Database, DbError>,
    max_bytes: AtomicU64,
}

impl CacheService {
    /// Initialize the cache rooted at `data_dir` (DB at `{data_dir}/app.db`,
    /// payloads under `{data_dir}/cache/`). Init failures are captured so the
    /// app keeps running and callers surface a clean error.
    pub fn open(data_dir: PathBuf, config: CacheServiceConfig) -> Self {
        let db = Database::open(&data_dir.join("app.db"));
        if let Err(e) = &db {
            log::error!("cache database init failed: {e}");
        }
        if let Err(e) = fs::create_dir_all(data_dir.join("cache")) {
            log::error!("failed to create cache root: {e}");
        }
        let svc = Self {
            data_dir,
            db,
            max_bytes: AtomicU64::new(config.max_bytes),
        };
        svc.cleanup_temp_files();
        svc
    }

    /// Root directory of cache payloads: `{data_dir}/cache`.
    pub fn cache_root(&self) -> PathBuf {
        self.data_dir.join("cache")
    }

    // ------------------------------------------------------------------
    // Key builders (ARCHITECTURE_DECISION.md Â§3.7 â€” FROZEN formats)
    // ------------------------------------------------------------------

    /// `audio:{sha256(video)}:{spec}`
    pub fn audio_key(video_sha256: &str, spec: &str) -> String {
        format!("audio:{video_sha256}:{spec}")
    }

    /// `stt:{sha256(audio)}:{model}:{compute}:{lang}:{vad}`
    pub fn stt_key(
        audio_sha256: &str,
        model: &str,
        compute: &str,
        lang: &str,
        vad: &str,
    ) -> String {
        format!("stt:{audio_sha256}:{model}:{compute}:{lang}:{vad}")
    }

    /// `tr:{sha256(source_block)}:{target}:{model}:{glossary_ver}:{rules_ver}`
    pub fn tr_key(
        source_sha256: &str,
        target: &str,
        model: &str,
        glossary_ver: &str,
        rules_ver: &str,
    ) -> String {
        format!("tr:{source_sha256}:{target}:{model}:{glossary_ver}:{rules_ver}")
    }

    /// `render:{sha256(video+style+wm+encoder+preset)}` â€” the whole input set
    /// (including the video's own content hash) is folded into one digest.
    pub fn render_key(
        video_sha256: &str,
        style: &str,
        watermark: &str,
        encoder: &str,
        preset: &str,
    ) -> String {
        let folded = format!("{video_sha256}|{style}|{watermark}|{encoder}|{preset}");
        format!("render:{}", sha256_hex(folded.as_bytes()))
    }

    // ------------------------------------------------------------------
    // Read / write / invalidate
    // ------------------------------------------------------------------

    /// Look up a cached artifact. Returns the payload path on a HIT, `None` on
    /// a MISS. A stale row whose file disappeared (crash) is dropped and the
    /// MISS is reported â€” cache corruption never surfaces as an error.
    pub fn get(&self, project_id: &str, key: &str) -> Result<Option<PathBuf>, DbError> {
        let project_id = validate_id(project_id)?;
        validate_key(key)?;
        let db = self.db()?;
        let conn = db.conn();
        let row = CacheRepo::new(&conn).get(&project_id, key)?;
        let Some((file_name, _stage)) = row else {
            return Ok(None);
        };
        let path = self.cache_root().join(file_name);
        if !path.is_file() {
            log::warn!("cache entry {} is stale (file missing), dropping row", key);
            let _ = CacheRepo::new(&conn).delete(&project_id, key)?;
            return Ok(None);
        }
        let now = utc_iso8601_now();
        CacheRepo::new(&conn).touch(&project_id, key, &now)?;
        Ok(Some(path))
    }

    /// Store a small artifact (bytes) and return its cache path. Enforces the
    /// quota afterwards.
    pub fn set(
        &self,
        project_id: &str,
        key: &str,
        stage: &str,
        data: &[u8],
    ) -> Result<PathBuf, DbError> {
        let project_id = validate_id(project_id)?;
        validate_key(key)?;
        validate_stage(stage)?;
        let db = self.db()?;
        let file_name = self.file_name_for(key, stage);
        let dest = self.cache_root().join(&file_name);
        write_atomic(&dest, data)?;
        let size = data.len() as u64;
        self.record_entry(db, &project_id, key, stage, &file_name, size)?;
        self.evict_to_quota()?;
        Ok(dest)
    }

    /// Store an artifact already on disk (e.g. a render output) by copying it
    /// into the cache atomically. Enforces the quota afterwards.
    pub fn set_from_path(
        &self,
        project_id: &str,
        key: &str,
        stage: &str,
        src: &Path,
    ) -> Result<PathBuf, DbError> {
        let project_id = validate_id(project_id)?;
        validate_key(key)?;
        validate_stage(stage)?;
        let db = self.db()?;
        let file_name = self.file_name_for(key, stage);
        let dest = self.cache_root().join(&file_name);
        copy_atomic(src, &dest)?;
        let size = fs::metadata(&dest)?.len();
        self.record_entry(db, &project_id, key, stage, &file_name, size)?;
        self.evict_to_quota()?;
        Ok(dest)
    }

    /// Remove one artifact (file + index row). Returns whether it existed.
    pub fn invalidate(&self, project_id: &str, key: &str) -> Result<bool, DbError> {
        let project_id = validate_id(project_id)?;
        validate_key(key)?;
        let db = self.db()?;
        let conn = db.conn();
        let repo = CacheRepo::new(&conn);
        let Some((file_name, _)) = repo.get(&project_id, key)? else {
            return Ok(false);
        };
        let _ = fs::remove_file(self.cache_root().join(file_name));
        let _ = repo.delete(&project_id, key)?;
        Ok(true)
    }

    /// Cascade invalidation: drop every stage at or after `from_stage` for the
    /// project (MASTER_PLAN.md Â§19.2). E.g. editing translation invalidates
    /// `tr`, `subtitle` and `render` but leaves `audio`/`stt` untouched.
    pub fn invalidate_downstream(
        &self,
        project_id: &str,
        from_stage: &str,
    ) -> Result<u64, DbError> {
        let project_id = validate_id(project_id)?;
        let idx = stage_index(from_stage)?;
        let db = self.db()?;
        let conn = db.conn();
        let repo = CacheRepo::new(&conn);
        let victims = repo.list_stages(&project_id, &STAGE_ORDER[idx..])?;
        for (key, file_name) in &victims {
            let _ = fs::remove_file(self.cache_root().join(file_name));
            let _ = repo.delete(&project_id, key)?;
        }
        Ok(victims.len() as u64)
    }

    /// Enforce the quota: evict oldest last-accessed entries until the total
    /// size is under `max_bytes`. Returns how many entries were evicted.
    pub fn evict_to_quota(&self) -> Result<u64, DbError> {
        let db = self.db()?;
        let conn = db.conn();
        let repo = CacheRepo::new(&conn);
        let quota = self.max_bytes.load(Ordering::Relaxed);
        let mut total = repo.total_bytes()?;
        if total <= quota {
            self.cleanup_temp_files();
            return Ok(0);
        }
        let mut evicted = 0u64;
        for (key, file_name, size) in repo.oldest_first()? {
            if total <= quota {
                break;
            }
            let project_id = key.0.clone();
            let _ = fs::remove_file(self.cache_root().join(&file_name));
            let _ = repo.delete(&project_id, &key.1)?;
            total = total.saturating_sub(size);
            evicted += 1;
        }
        self.cleanup_temp_files();
        Ok(evicted)
    }

    /// Current cache usage (all projects).
    pub fn stats(&self) -> Result<CacheStats, DbError> {
        let db = self.db()?;
        let conn = db.conn();
        let repo = CacheRepo::new(&conn);
        Ok(CacheStats {
            entry_count: repo.count()?,
            total_bytes: repo.total_bytes()?,
        })
    }

    /// Update the LRU quota at runtime (TASK-030 settings). The new quota is
    /// enforced immediately — eviction runs right away if already over.
    pub fn set_max_bytes(&self, max_bytes: u64) -> Result<u64, DbError> {
        self.max_bytes.store(max_bytes, Ordering::Relaxed);
        self.evict_to_quota()
    }

    /// SHA-256 hex digest of `data` (public so other services and the worker
    /// parity tests share one implementation).
    pub fn sha256_hex(data: &[u8]) -> String {
        sha256_hex(data)
    }

    /// SHA-256 hex digest of a file, streamed (never loads the file into RAM â€”
    /// MASTER_PLAN.md Â§5).
    pub fn sha256_file(path: &Path) -> Result<String, DbError> {
        let mut file = File::open(path)?;
        let mut hasher = Sha256::new();
        let mut buf = [0u8; READ_CHUNK];
        loop {
            let n = file.read(&mut buf)?;
            if n == 0 {
                break;
            }
            hasher.update(&buf[..n]);
        }
        Ok(hex(&hasher.finish()))
    }

    fn db(&self) -> Result<&Database, DbError> {
        self.db.as_ref().map_err(|e| e.clone())
    }

    /// Payload file name: `{stage}_{sha256(key)}` â€” safe on every filesystem.
    fn file_name_for(&self, key: &str, stage: &str) -> String {
        format!("{stage}_{}", sha256_hex(key.as_bytes()))
    }

    fn record_entry(
        &self,
        db: &Database,
        project_id: &str,
        key: &str,
        stage: &str,
        file_name: &str,
        size: u64,
    ) -> Result<(), DbError> {
        let now = utc_iso8601_now();
        let conn = db.conn();
        CacheRepo::new(&conn).upsert(project_id, key, stage, file_name, size, &now)
    }

    /// Remove leftover `*.tmp-*` files from interrupted writes (crash safety).
    fn cleanup_temp_files(&self) {
        let root = self.cache_root();
        let Ok(entries) = fs::read_dir(&root) else {
            return;
        };
        for entry in entries.flatten() {
            let name = entry.file_name();
            let name = name.to_string_lossy();
            if name.contains(".tmp-") && entry.path().is_file() {
                if let Err(e) = fs::remove_file(entry.path()) {
                    log::warn!(
                        "failed to remove stale temp file {}: {e}",
                        entry.path().display()
                    );
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// SQLite repo for `cache_entries`
// ---------------------------------------------------------------------------

/// One LRU candidate: `((project_id, key), file_name, size_bytes)`.
type LruEntry = ((String, String), String, u64);

/// Access to the `cache_entries` table. Lives in this module (TASK-011 file
/// scope) rather than `db/repo/` because the cache is service-owned.
struct CacheRepo<'a> {
    conn: &'a rusqlite::Connection,
}

impl<'a> CacheRepo<'a> {
    fn new(conn: &'a rusqlite::Connection) -> Self {
        Self { conn }
    }

    fn get(&self, project_id: &str, key: &str) -> Result<Option<(String, String)>, DbError> {
        let mut stmt = self.conn.prepare(
            "SELECT file_name, stage FROM cache_entries WHERE project_id = ?1 AND key = ?2",
        )?;
        let mut rows = stmt.query([project_id, key])?;
        if let Some(row) = rows.next()? {
            Ok(Some((row.get(0)?, row.get(1)?)))
        } else {
            Ok(None)
        }
    }

    fn touch(&self, project_id: &str, key: &str, now: &str) -> Result<(), DbError> {
        self.conn.execute(
            "UPDATE cache_entries SET last_accessed_at = ?1 WHERE project_id = ?2 AND key = ?3",
            [now, project_id, key],
        )?;
        Ok(())
    }

    fn upsert(
        &self,
        project_id: &str,
        key: &str,
        stage: &str,
        file_name: &str,
        size: u64,
        now: &str,
    ) -> Result<(), DbError> {
        self.conn.execute(
            "INSERT OR REPLACE INTO cache_entries
                (project_id, key, stage, file_name, size_bytes, created_at, last_accessed_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?6)",
            rusqlite::params![project_id, key, stage, file_name, size as i64, now],
        )?;
        Ok(())
    }

    fn delete(&self, project_id: &str, key: &str) -> Result<bool, DbError> {
        let n = self.conn.execute(
            "DELETE FROM cache_entries WHERE project_id = ?1 AND key = ?2",
            [project_id, key],
        )?;
        Ok(n > 0)
    }

    fn list_stages(
        &self,
        project_id: &str,
        stages: &[&str],
    ) -> Result<Vec<(String, String)>, DbError> {
        if stages.is_empty() {
            return Ok(Vec::new());
        }
        let placeholders = stages.iter().map(|_| "?").collect::<Vec<_>>().join(",");
        let sql = format!(
            "SELECT key, file_name FROM cache_entries \
             WHERE project_id = ?1 AND stage IN ({placeholders})"
        );
        let mut stmt = self.conn.prepare(&sql)?;
        let mut params: Vec<&dyn rusqlite::ToSql> = vec![&project_id];
        params.extend(stages.iter().map(|s| s as &dyn rusqlite::ToSql));
        let mut rows = stmt.query(params.as_slice())?;
        let mut out = Vec::new();
        while let Some(row) = rows.next()? {
            out.push((row.get(0)?, row.get(1)?));
        }
        Ok(out)
    }

    /// All entries oldest last-accessed first: `((project_id, key), file, size)`.
    fn oldest_first(&self) -> Result<Vec<LruEntry>, DbError> {
        let mut stmt = self.conn.prepare(
            "SELECT project_id, key, file_name, size_bytes FROM cache_entries \
             ORDER BY last_accessed_at ASC, created_at ASC",
        )?;
        let mut rows = stmt.query([])?;
        let mut out = Vec::new();
        while let Some(row) = rows.next()? {
            out.push((
                (row.get(0)?, row.get(1)?),
                row.get(2)?,
                row.get::<_, i64>(3)? as u64,
            ));
        }
        Ok(out)
    }

    fn total_bytes(&self) -> Result<u64, DbError> {
        let total = self.conn.query_row(
            "SELECT COALESCE(SUM(size_bytes), 0) FROM cache_entries",
            [],
            |r| r.get::<_, i64>(0),
        )?;
        Ok(total as u64)
    }

    fn count(&self) -> Result<u64, DbError> {
        let count = self
            .conn
            .query_row("SELECT COUNT(*) FROM cache_entries", [], |r| {
                r.get::<_, i64>(0)
            })?;
        Ok(count as u64)
    }
}

// ---------------------------------------------------------------------------
// Validation helpers
// ---------------------------------------------------------------------------

fn validate_id(id: &str) -> Result<String, DbError> {
    if !is_valid_uuid_v4(id) {
        return Err(DbError::InvalidInput(format!("invalid project id: {id:?}")));
    }
    Ok(id.to_string())
}

fn validate_key(key: &str) -> Result<(), DbError> {
    if key.is_empty() {
        return Err(DbError::InvalidInput("cache key must not be empty".into()));
    }
    if key.len() > MAX_KEY_CHARS {
        return Err(DbError::InvalidInput(format!(
            "cache key exceeds {MAX_KEY_CHARS} characters"
        )));
    }
    if key.contains('\0') {
        return Err(DbError::InvalidInput(
            "cache key contains a NUL byte".into(),
        ));
    }
    Ok(())
}

fn validate_stage(stage: &str) -> Result<(), DbError> {
    if !STAGE_ORDER.contains(&stage) {
        return Err(DbError::InvalidInput(format!(
            "unknown cache stage: {stage:?}"
        )));
    }
    Ok(())
}

fn stage_index(stage: &str) -> Result<usize, DbError> {
    STAGE_ORDER
        .iter()
        .position(|s| *s == stage)
        .ok_or_else(|| DbError::InvalidInput(format!("unknown cache stage: {stage:?}")))
}

// ---------------------------------------------------------------------------
// Atomic file helpers
// ---------------------------------------------------------------------------

fn temp_sibling(dest: &Path) -> PathBuf {
    let name = dest
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("cache")
        .to_string();
    dest.with_file_name(format!("{name}.tmp-{}", std::process::id()))
}

fn write_atomic(dest: &Path, data: &[u8]) -> Result<(), DbError> {
    let tmp = temp_sibling(dest);
    let mut file = File::create(&tmp)?;
    file.write_all(data)?;
    file.sync_all()?;
    fs::rename(&tmp, dest)?;
    Ok(())
}

fn copy_atomic(src: &Path, dest: &Path) -> Result<(), DbError> {
    let tmp = temp_sibling(dest);
    fs::copy(src, &tmp)?;
    fs::rename(&tmp, dest)?;
    Ok(())
}

// ---------------------------------------------------------------------------
// Streaming SHA-256 (no external dependency)
// ---------------------------------------------------------------------------

const K: [u32; 64] = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
];

/// Incremental SHA-256 hasher.
struct Sha256 {
    state: [u32; 8],
    buffer: [u8; 64],
    buffer_len: usize,
    total_len: u64,
}

impl Sha256 {
    fn new() -> Self {
        Self {
            state: [
                0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab,
                0x5be0cd19,
            ],
            buffer: [0u8; 64],
            buffer_len: 0,
            total_len: 0,
        }
    }

    fn update(&mut self, mut data: &[u8]) {
        self.total_len = self.total_len.wrapping_add(data.len() as u64);
        if self.buffer_len > 0 {
            let need = 64 - self.buffer_len;
            let take = need.min(data.len());
            self.buffer[self.buffer_len..self.buffer_len + take].copy_from_slice(&data[..take]);
            self.buffer_len += take;
            data = &data[take..];
            if self.buffer_len == 64 {
                let block = self.buffer;
                self.compress(&block);
                self.buffer_len = 0;
            }
        }
        while data.len() >= 64 {
            let mut block = [0u8; 64];
            block.copy_from_slice(&data[..64]);
            self.compress(&block);
            data = &data[64..];
        }
        if !data.is_empty() {
            self.buffer[..data.len()].copy_from_slice(data);
            self.buffer_len = data.len();
        }
    }

    fn finish(mut self) -> [u8; 32] {
        let bit_len = self.total_len.wrapping_mul(8);
        self.buffer[self.buffer_len] = 0x80;
        if self.buffer_len + 1 > 56 {
            self.buffer[self.buffer_len + 1..].fill(0);
            let block = self.buffer;
            self.compress(&block);
            self.buffer.fill(0);
        } else {
            self.buffer[self.buffer_len + 1..].fill(0);
        }
        self.buffer[56..64].copy_from_slice(&bit_len.to_be_bytes());
        let block = self.buffer;
        self.compress(&block);
        let mut out = [0u8; 32];
        for (i, word) in self.state.iter().enumerate() {
            out[i * 4..i * 4 + 4].copy_from_slice(&word.to_be_bytes());
        }
        out
    }

    fn compress(&mut self, block: &[u8; 64]) {
        let mut w = [0u32; 64];
        for (i, word) in block.chunks_exact(4).enumerate() {
            w[i] = u32::from_be_bytes([word[0], word[1], word[2], word[3]]);
        }
        for i in 16..64 {
            let s0 = w[i - 15].rotate_right(7) ^ w[i - 15].rotate_right(18) ^ (w[i - 15] >> 3);
            let s1 = w[i - 2].rotate_right(17) ^ w[i - 2].rotate_right(19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16]
                .wrapping_add(s0)
                .wrapping_add(w[i - 7])
                .wrapping_add(s1);
        }
        let (mut a, mut b, mut c, mut d, mut e, mut f, mut g, mut h) = (
            self.state[0],
            self.state[1],
            self.state[2],
            self.state[3],
            self.state[4],
            self.state[5],
            self.state[6],
            self.state[7],
        );
        for i in 0..64 {
            let s1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
            let ch = (e & f) ^ (!e & g);
            let t1 = h
                .wrapping_add(s1)
                .wrapping_add(ch)
                .wrapping_add(K[i])
                .wrapping_add(w[i]);
            let s0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
            let maj = (a & b) ^ (a & c) ^ (b & c);
            let t2 = s0.wrapping_add(maj);
            h = g;
            g = f;
            f = e;
            e = d.wrapping_add(t1);
            d = c;
            c = b;
            b = a;
            a = t1.wrapping_add(t2);
        }
        self.state[0] = self.state[0].wrapping_add(a);
        self.state[1] = self.state[1].wrapping_add(b);
        self.state[2] = self.state[2].wrapping_add(c);
        self.state[3] = self.state[3].wrapping_add(d);
        self.state[4] = self.state[4].wrapping_add(e);
        self.state[5] = self.state[5].wrapping_add(f);
        self.state[6] = self.state[6].wrapping_add(g);
        self.state[7] = self.state[7].wrapping_add(h);
    }
}

fn sha256_hex(data: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(data);
    hex(&hasher.finish())
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db::repo::project::ProjectRepo;
    use crate::db::{new_uuid_v4, Project, ProjectStatus};

    fn test_data_dir(label: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "tooltranslate_cache_{label}_{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).expect("create temp dir");
        dir
    }

    fn service(label: &str, max_bytes: u64) -> CacheService {
        let svc = CacheService::open(test_data_dir(label), CacheServiceConfig { max_bytes });
        assert!(svc.db().is_ok(), "db must open cleanly for tests");
        svc
    }

    fn seed_project(svc: &CacheService) -> String {
        let db = svc.db().expect("db");
        let project = Project {
            id: new_uuid_v4().expect("uuid"),
            name: "cache test".into(),
            source_video_path: "v.mp4".into(),
            status: ProjectStatus::Draft,
            created_at: utc_iso8601_now(),
            updated_at: utc_iso8601_now(),
            settings_json: None,
        };
        let conn = db.conn();
        ProjectRepo::new(&conn)
            .insert(&project)
            .expect("seed project");
        project.id
    }

    // ------------------------------------------------------------------
    // SHA-256 correctness (verified against hashlib.sha256)
    // ------------------------------------------------------------------

    #[test]
    fn sha256_matches_known_vectors() {
        assert_eq!(
            sha256_hex(b""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
        assert_eq!(
            sha256_hex(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
        assert_eq!(
            sha256_hex(b"hello world"),
            "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
        );
        // Block boundaries: 63 / 64 / 65 / 1000 bytes
        assert_eq!(
            sha256_hex(&[b'x'; 63]),
            "75220b47218278e656f2013bb8f0c455a25eaf01e86c64924e9d48d89776d6f2"
        );
        assert_eq!(
            sha256_hex(&[b'x'; 64]),
            "7ce100971f64e7001e8fe5a51973ecdfe1ced42befe7ee8d5fd6219506b5393c"
        );
        assert_eq!(
            sha256_hex(&[b'x'; 65]),
            "9537c5fdf120482f7d58d25e9ed583f52c02b4e304ea814db1633ad565aed7e9"
        );
        assert_eq!(
            sha256_hex(&[b'x'; 1000]),
            "44f8354494a5ba03ba1792a8d3e9c534c47a9181980fde7a3f44b06ef2ae7c7f"
        );
    }

    #[test]
    fn sha256_is_incremental_across_chunk_boundaries() {
        // Feeding bytes in small slices must equal one big update.
        let mut a = Sha256::new();
        for _ in 0..10 {
            a.update(&[b'a'; 37]);
        }
        let whole = sha256_hex(&[b'a'; 370]);
        assert_eq!(hex(&a.finish()), whole);
    }

    #[test]
    fn sha256_file_streams_content_and_rejects_missing_file() {
        let svc = service("sha256file", DEFAULT_CACHE_QUOTA_BYTES);
        let path = svc.cache_root().join("input.bin");
        fs::write(&path, [b'z'; 4097]).expect("write");
        assert_eq!(
            CacheService::sha256_file(&path).expect("hash"),
            sha256_hex(&[b'z'; 4097])
        );
        assert!(CacheService::sha256_file(&path.join("nope")).is_err());
        let _ = fs::remove_dir_all(svc.data_dir.clone());
    }

    // ------------------------------------------------------------------
    // Key builders (Â§3.7 formats + stability)
    // ------------------------------------------------------------------

    #[test]
    fn key_builders_match_frozen_formats() {
        assert_eq!(
            CacheService::audio_key("abc123", "wav:16000:mono"),
            "audio:abc123:wav:16000:mono"
        );
        assert_eq!(
            CacheService::stt_key("deadbeef", "large-v3", "int8", "zh", "silero"),
            "stt:deadbeef:large-v3:int8:zh:silero"
        );
        assert_eq!(
            CacheService::tr_key("feed", "vi", "gemini-2.5-flash-lite", "g3", "r2"),
            "tr:feed:vi:gemini-2.5-flash-lite:g3:r2"
        );
        let r = CacheService::render_key("vid", "styleA", "wm1", "libx264", "fast");
        assert!(r.starts_with("render:"));
        assert_eq!(r.len(), "render:".len() + 64);
        // Fixed digest â€” must match worker/src/services/cache.py parity test.
        assert_eq!(
            r,
            "render:f8fee54b8677570e6e2347080670aa9c9397051dde998f8951c30bfb1bad29f8"
        );
    }

    #[test]
    fn key_builders_are_stable_and_sensitive() {
        assert_eq!(
            CacheService::stt_key("h", "m", "c", "l", "v"),
            CacheService::stt_key("h", "m", "c", "l", "v")
        );
        assert_ne!(
            CacheService::stt_key("h", "m", "c", "l", "v"),
            CacheService::stt_key("h", "m", "c", "l", "v2"),
            "VAD change must change the key"
        );
        assert_ne!(
            CacheService::tr_key("s", "vi", "m", "g1", "r"),
            CacheService::tr_key("s", "vi", "m", "g2", "r"),
            "glossary version change must change the key"
        );
        assert_ne!(
            CacheService::render_key("v", "styleA", "wm", "enc", "p"),
            CacheService::render_key("v", "styleB", "wm", "enc", "p"),
            "style change must invalidate render"
        );
        assert_eq!(
            CacheService::render_key("v", "styleA", "wm", "enc", "p"),
            CacheService::render_key("v", "styleA", "wm", "enc", "p")
        );
    }

    // ------------------------------------------------------------------
    // get / set / invalidate
    // ------------------------------------------------------------------

    #[test]
    fn set_then_get_roundtrips_and_touches() {
        let svc = service("roundtrip", DEFAULT_CACHE_QUOTA_BYTES);
        let pid = seed_project(&svc);
        let key = CacheService::audio_key("sha", "wav:16000:mono");
        let path = svc.set(&pid, &key, "audio", b"wavdata").expect("set");
        assert!(path.is_file());
        assert_eq!(fs::read(&path).unwrap(), b"wavdata");

        let hit = svc.get(&pid, &key).expect("get").expect("hit");
        assert_eq!(hit, path);

        // last_accessed_at advanced on get.
        let db = svc.db().expect("db");
        let conn = db.conn();
        let lu: String = conn
            .query_row(
                "SELECT last_accessed_at FROM cache_entries WHERE project_id = ?1 AND key = ?2",
                [&pid, &key],
                |r| r.get(0),
            )
            .expect("row");
        assert!(!lu.is_empty());
        let _ = fs::remove_dir_all(svc.data_dir.clone());
    }

    #[test]
    fn get_miss_returns_none() {
        let svc = service("miss", DEFAULT_CACHE_QUOTA_BYTES);
        let pid = seed_project(&svc);
        let key = CacheService::stt_key("h", "m", "c", "l", "v");
        assert!(svc.get(&pid, &key).expect("get").is_none());
        let _ = fs::remove_dir_all(svc.data_dir.clone());
    }

    #[test]
    fn set_from_path_copies_payload() {
        let svc = service("frompath", DEFAULT_CACHE_QUOTA_BYTES);
        let pid = seed_project(&svc);
        let src = svc.cache_root().join("src.mp4");
        fs::write(&src, b"render-bytes").expect("write");
        let key = CacheService::render_key("v", "s", "wm", "enc", "p");
        let dest = svc.set_from_path(&pid, &key, "render", &src).expect("set");
        assert_eq!(fs::read(&dest).unwrap(), b"render-bytes");
        let _ = fs::remove_dir_all(svc.data_dir.clone());
    }

    #[test]
    fn overwrite_updates_payload() {
        let svc = service("overwrite", DEFAULT_CACHE_QUOTA_BYTES);
        let pid = seed_project(&svc);
        let key = CacheService::tr_key("s", "vi", "m", "g", "r");
        svc.set(&pid, &key, "tr", b"v1").expect("set v1");
        svc.set(&pid, &key, "tr", b"v2-longer").expect("set v2");
        let path = svc.get(&pid, &key).expect("get").expect("hit");
        assert_eq!(fs::read(path).unwrap(), b"v2-longer");
        assert_eq!(svc.stats().expect("stats").entry_count, 1);
        let _ = fs::remove_dir_all(svc.data_dir.clone());
    }

    #[test]
    fn invalidate_removes_file_and_row() {
        let svc = service("invalidate", DEFAULT_CACHE_QUOTA_BYTES);
        let pid = seed_project(&svc);
        let key = CacheService::audio_key("sha", "spec");
        svc.set(&pid, &key, "audio", b"x").expect("set");
        assert!(svc.invalidate(&pid, &key).expect("invalidate"));
        assert!(svc.get(&pid, &key).expect("get").is_none());
        assert!(!svc.invalidate(&pid, &key).expect("invalidate again"));
        let _ = fs::remove_dir_all(svc.data_dir.clone());
    }

    #[test]
    fn stale_row_where_file_is_missing_is_cleaned_on_get() {
        let svc = service("stale", DEFAULT_CACHE_QUOTA_BYTES);
        let pid = seed_project(&svc);
        let key = CacheService::audio_key("sha", "spec");
        let path = svc.set(&pid, &key, "audio", b"x").expect("set");
        fs::remove_file(&path).expect("simulate crash losing the file");
        assert!(svc.get(&pid, &key).expect("get").is_none());
        assert_eq!(
            svc.stats().expect("stats").entry_count,
            0,
            "stale row dropped"
        );
        let _ = fs::remove_dir_all(svc.data_dir.clone());
    }

    // ------------------------------------------------------------------
    // Downstream cascade invalidation
    // ------------------------------------------------------------------

    fn seed_full_pipeline(svc: &CacheService, pid: &str) {
        let entries = [
            ("audio", CacheService::audio_key("vsha", "wav")),
            ("stt", CacheService::stt_key("asha", "m", "c", "zh", "v")),
            ("tr", CacheService::tr_key("s", "vi", "m", "g", "r")),
            (
                "subtitle",
                CacheService::render_key("v", "style", "wm", "e", "p"),
            ),
            (
                "render",
                CacheService::render_key("v", "style2", "wm", "e", "p"),
            ),
        ];
        for (stage, key) in entries {
            svc.set(pid, &key, stage, b"payload").expect("set");
        }
        assert_eq!(svc.stats().expect("stats").entry_count, 5);
    }

    #[test]
    fn editing_translation_invalidates_downstream_not_stt() {
        let svc = service("cascade_tr", DEFAULT_CACHE_QUOTA_BYTES);
        let pid = seed_project(&svc);
        seed_full_pipeline(&svc, &pid);
        let removed = svc.invalidate_downstream(&pid, "tr").expect("cascade");
        assert_eq!(removed, 3, "tr + subtitle + render");
        assert_eq!(
            svc.stats().expect("stats").entry_count,
            2,
            "audio + stt remain"
        );
        let _ = fs::remove_dir_all(svc.data_dir.clone());
    }

    #[test]
    fn changing_style_invalidates_only_render() {
        let svc = service("cascade_style", DEFAULT_CACHE_QUOTA_BYTES);
        let pid = seed_project(&svc);
        seed_full_pipeline(&svc, &pid);
        let removed = svc.invalidate_downstream(&pid, "render").expect("cascade");
        assert_eq!(removed, 1);
        assert_eq!(svc.stats().expect("stats").entry_count, 4);
        let _ = fs::remove_dir_all(svc.data_dir.clone());
    }

    #[test]
    fn cascade_is_scoped_per_project() {
        let svc = service("cascade_scope", DEFAULT_CACHE_QUOTA_BYTES);
        let pid_a = seed_project(&svc);
        let pid_b = seed_project(&svc);
        seed_full_pipeline(&svc, &pid_a);
        // Project B caches only STT under the same key.
        let key = CacheService::stt_key("asha", "m", "c", "zh", "v");
        svc.set(&pid_b, &key, "stt", b"payload").expect("set");
        let removed = svc.invalidate_downstream(&pid_a, "tr").expect("cascade");
        assert_eq!(removed, 3);
        // B's stt row untouched.
        assert!(svc.get(&pid_b, &key).expect("get").is_some());
        let _ = fs::remove_dir_all(svc.data_dir.clone());
    }

    // ------------------------------------------------------------------
    // LRU eviction
    // ------------------------------------------------------------------

    #[test]
    fn evicts_oldest_first_when_over_quota() {
        let svc = service("evict", 200);
        let pid = seed_project(&svc);
        for k in ["a", "b", "c", "d", "e"] {
            svc.set(&pid, &format!("tr:{k}"), "tr", &[b'y'; 100])
                .expect("set");
        }
        assert_eq!(
            svc.stats().expect("stats").entry_count,
            2,
            "quota keeps 200/100 bytes"
        );
        assert!(
            svc.stats().expect("stats").total_bytes <= 200,
            "quota enforced"
        );

        // Oldest inserted (a, b) evicted; newest (d, e) survive.
        for k in &["a", "b"] {
            assert!(
                svc.get(&pid, &format!("tr:{k}")).expect("get").is_none(),
                "{k} evicted"
            );
        }
        for k in &["d", "e"] {
            assert!(
                svc.get(&pid, &format!("tr:{k}")).expect("get").is_some(),
                "{k} kept"
            );
        }
        let _ = fs::remove_dir_all(svc.data_dir.clone());
    }

    #[test]
    fn recent_get_protects_from_eviction() {
        let svc = service("evict_recent", 320);
        let pid = seed_project(&svc);
        for k in ["a", "b", "c"] {
            svc.set(&pid, &format!("tr:{k}"), "tr", &[b'y'; 100])
                .expect("set");
        }
        // Deterministic LRU order: a most recent, b oldest, c in between.
        {
            let db = svc.db().expect("db");
            let conn = db.conn();
            for (k, t) in [
                ("a", "2026-01-01T00:00:03.000Z"),
                ("b", "2026-01-01T00:00:01.000Z"),
                ("c", "2026-01-01T00:00:02.000Z"),
            ] {
                conn.execute(
                    "UPDATE cache_entries SET last_accessed_at = ?1 WHERE project_id = ?2 AND key = ?3",
                    [t, &pid, &format!("tr:{k}")],
                )
                .expect("set access time");
            }
        }
        svc.set(&pid, "tr:d", "tr", &[b'y'; 100]).expect("set d");
        assert!(
            svc.get(&pid, "tr:a").expect("get").is_some(),
            "a recently touched, kept"
        );
        assert!(
            svc.get(&pid, "tr:b").expect("get").is_none(),
            "b oldest, evicted"
        );
        assert!(svc.get(&pid, "tr:c").expect("get").is_some(), "c kept");
        assert!(svc.get(&pid, "tr:d").expect("get").is_some(), "d kept");
        let _ = fs::remove_dir_all(svc.data_dir.clone());
    }

    // ------------------------------------------------------------------
    // Validation
    // ------------------------------------------------------------------

    #[test]
    fn invalid_project_id_is_rejected() {
        let svc = service("invalid", DEFAULT_CACHE_QUOTA_BYTES);
        assert!(matches!(
            svc.get("../../escape", "tr:x"),
            Err(DbError::InvalidInput(_))
        ));
        assert!(matches!(
            svc.set("not-a-uuid", "tr:x", "tr", b"x"),
            Err(DbError::InvalidInput(_))
        ));
        let _ = fs::remove_dir_all(svc.data_dir.clone());
    }

    #[test]
    fn empty_or_oversized_key_is_rejected() {
        let svc = service("keyvalid", DEFAULT_CACHE_QUOTA_BYTES);
        let pid = seed_project(&svc);
        assert!(matches!(
            svc.set(&pid, "", "tr", b"x"),
            Err(DbError::InvalidInput(_))
        ));
        let long = "x".repeat(MAX_KEY_CHARS + 1);
        assert!(matches!(
            svc.set(&pid, &long, "tr", b"x"),
            Err(DbError::InvalidInput(_))
        ));
        assert!(matches!(
            svc.set(&pid, "k", "bogus-stage", b"x"),
            Err(DbError::InvalidInput(_))
        ));
        assert!(matches!(
            svc.invalidate_downstream(&pid, "bogus-stage"),
            Err(DbError::InvalidInput(_))
        ));
        let _ = fs::remove_dir_all(svc.data_dir.clone());
    }
}
