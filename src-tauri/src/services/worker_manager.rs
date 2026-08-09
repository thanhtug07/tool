//! Python sidecar lifecycle manager (TASK-006).
//!
//! Owns the Python worker process for the lifetime of the app:
//!
//! ```text
//! Stopped ──► Starting ──► Ready ──► Stopping ──► Stopped
//!    │            │                       ▲
//!    │            ▼                       │ (graceful exit)
//!    └──────► Failed ◄───────── crash ────┘
//! ```
//!
//! Spawn contract (frozen in `MASTER_PLAN.md` §15.2 / `ARCHITECTURE_DECISION.md` §6):
//! - Port: OS-assigned ephemeral port on `127.0.0.1`, passed via `--port <n>`
//!   (never a secret, so argv is fine). The tiny `find → release → bind` window
//!   is mitigated by restarting on bind failure with a fresh port.
//! - Auth token: 256-bit random per session, passed over **stdin** (never argv
//!   or env, both of which are easy to leak via process listings). The worker
//!   echoes `READY <token>` on stdout once it has bound; the Rust core verifies
//!   it matches, then polls authenticated `/health`.
//! - Shutdown: `SHUTDOWN` over stdin (graceful) → bounded wait → `kill()`.
//!
//! Secrets are never logged and never cross the IPC boundary.

use std::io::{BufRead, BufReader, Read, Write};
use std::net::{Ipv4Addr, TcpListener};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc::{channel, sync_channel, Receiver, Sender, SyncSender};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};

use super::worker_client::{HttpError, WorkerClient, HOST_LOOPBACK};

pub const MAX_RESTARTS_DEFAULT: u32 = 3;
pub const HEALTH_POLL_ATTEMPTS_DEFAULT: usize = 10;
pub const HEALTH_POLL_INTERVAL_DEFAULT: Duration = Duration::from_secs(1);
pub const STARTUP_READY_TIMEOUT_DEFAULT: Duration = Duration::from_secs(5);
pub const GRACEFUL_SHUTDOWN_TIMEOUT_DEFAULT: Duration = Duration::from_secs(3);
pub const KILL_WAIT_TIMEOUT_DEFAULT: Duration = Duration::from_secs(2);

const POLL_STEP: Duration = Duration::from_millis(200);
const EXIT_POLL_INTERVAL: Duration = Duration::from_millis(200);

/// Lifecycle state of the Python sidecar.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum WorkerState {
    Stopped,
    Starting,
    Ready,
    Stopping,
    Failed,
}

/// Serialized snapshot exposed over IPC (never contains the token).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkerStateInfo {
    pub state: WorkerState,
    pub pid: Option<u32>,
    pub port: Option<u16>,
    pub restarts: u32,
    pub last_error: Option<String>,
}

/// Tunable lifecycle parameters (defaults match TASK-006 spec).
#[derive(Debug, Clone)]
pub struct WorkerManagerConfig {
    /// Python interpreter to use. `None` → `WORKER_PYTHON` env → `python` (PATH).
    pub python: Option<PathBuf>,
    /// Directory to run `python -m src.main` from. `None` → resolved (repo layout).
    pub worker_dir: Option<PathBuf>,
    /// Maximum automatic restarts after an unexpected exit before `Failed`.
    pub max_restarts: u32,
    pub health_poll_attempts: usize,
    pub health_poll_interval: Duration,
    pub startup_ready_timeout: Duration,
    pub graceful_shutdown_timeout: Duration,
    pub kill_wait_timeout: Duration,
}

impl Default for WorkerManagerConfig {
    fn default() -> Self {
        Self {
            python: None,
            worker_dir: None,
            max_restarts: MAX_RESTARTS_DEFAULT,
            health_poll_attempts: HEALTH_POLL_ATTEMPTS_DEFAULT,
            health_poll_interval: HEALTH_POLL_INTERVAL_DEFAULT,
            startup_ready_timeout: STARTUP_READY_TIMEOUT_DEFAULT,
            graceful_shutdown_timeout: GRACEFUL_SHUTDOWN_TIMEOUT_DEFAULT,
            kill_wait_timeout: KILL_WAIT_TIMEOUT_DEFAULT,
        }
    }
}

#[derive(Debug)]
struct WorkerProcess {
    child: Child,
    stdin: ChildStdin,
    port: u16,
    token: String,
    pid: u32,
}

#[derive(Debug)]
struct Inner {
    state: WorkerState,
    process: Option<WorkerProcess>,
    port: Option<u16>,
    last_error: Option<String>,
    restarts: u32,
}

enum Msg {
    Stop,
}

#[derive(Debug, Clone, Copy)]
enum Stream {
    Stdout,
    Stderr,
}

struct WorkerLine {
    gen: u32,
    stream: Stream,
    text: String,
}

enum ReadyPhase {
    Ready,
    Failed,
    StoppedRequested,
}

/// The managed sidecar. `start()`/`stop()` are the public API; a background
/// supervisor thread drives readiness, crash-restart and shutdown.
pub struct WorkerManager {
    inner: Arc<Mutex<Inner>>,
    config: WorkerManagerConfig,
    control: Mutex<Option<Control>>,
}

struct Control {
    tx: SyncSender<Msg>,
    join: std::thread::JoinHandle<()>,
}

impl WorkerManager {
    pub fn new(config: WorkerManagerConfig) -> Self {
        Self {
            inner: Arc::new(Mutex::new(Inner {
                state: WorkerState::Stopped,
                process: None,
                port: None,
                last_error: None,
                restarts: 0,
            })),
            config,
            control: Mutex::new(None),
        }
    }

    /// Spawn the worker and begin supervision. Idempotent-guarded: returns an
    /// error if a worker is already running or still being supervised.
    pub fn start(&self) -> Result<(), String> {
        {
            let mut control = self.control.lock().unwrap();
            if let Some(c) = control.as_ref() {
                if c.join.is_finished() {
                    *control = None; // stale handle from a finished supervisor
                } else {
                    return Err("worker already running".to_string());
                }
            }
        }

        let mut inner = self.inner.lock().unwrap();
        if !matches!(inner.state, WorkerState::Stopped | WorkerState::Failed) {
            return Err(format!("cannot start worker from state {:?}", inner.state));
        }

        let process = self.spawn().inspect_err(|e| {
            inner.state = WorkerState::Failed;
            inner.last_error = Some(e.clone());
            inner.port = None;
        })?;

        inner.state = WorkerState::Starting;
        inner.restarts = 0;
        inner.last_error = None;
        inner.port = Some(process.port);
        inner.process = Some(process);
        drop(inner);

        self.spawn_supervisor();
        Ok(())
    }

    /// Stop the worker (graceful, then forced). Safe to call repeatedly; blocks
    /// until the supervisor has finished cleanup (bounded by shutdown timeouts).
    /// A `Failed` worker (e.g. after exhausting restarts) is normalized to
    /// `Stopped`.
    pub fn stop(&self) {
        let Some(control) = self.control.lock().unwrap().take() else {
            self.normalize_to_stopped();
            return;
        };
        if control.join.is_finished() {
            // The supervisor already returned (e.g. Failed after restarts).
            self.normalize_to_stopped();
            return;
        }
        let _ = control.tx.send(Msg::Stop);
        let _ = control.join.join();
    }

    /// Idempotent final state after any shutdown/failure path.
    fn normalize_to_stopped(&self) {
        let mut inner = self.inner.lock().unwrap();
        inner.port = None;
        inner.state = WorkerState::Stopped;
        inner.last_error = None;
        inner.restarts = 0;
    }

    /// Immutable snapshot for IPC / UI. Never exposes the session token.
    pub fn state_info(&self) -> WorkerStateInfo {
        let inner = self.inner.lock().unwrap();
        WorkerStateInfo {
            state: inner.state,
            pid: inner.process.as_ref().map(|p| p.pid),
            port: inner.port,
            restarts: inner.restarts,
            last_error: inner.last_error.clone(),
        }
    }

    /// Authenticated HTTP client when the worker is `Ready` (Rust-side use only).
    pub fn worker_client(&self) -> Option<WorkerClient> {
        let inner = self.inner.lock().unwrap();
        if inner.state == WorkerState::Ready {
            inner
                .process
                .as_ref()
                .map(|p| WorkerClient::new(p.port, p.token.clone()))
        } else {
            None
        }
    }

    /// Spawn a fresh worker process: pick a port, generate a token, hand the
    /// token over stdin. Does not touch shared state (caller assigns it).
    fn spawn(&self) -> Result<WorkerProcess, String> {
        spawn_worker(&self.config)
    }

    fn spawn_supervisor(&self) {
        let inner = Arc::clone(&self.inner);
        let config = self.config.clone();
        let (tx, rx) = sync_channel::<Msg>(4);
        let join = std::thread::Builder::new()
            .name("worker-supervisor".into())
            .spawn(move || {
                Supervisor::new(inner, config, rx).run();
            })
            .expect("failed to spawn supervisor thread");
        *self.control.lock().unwrap() = Some(Control { tx, join });
    }

    #[cfg(test)]
    fn session_token(&self) -> Option<String> {
        self.inner
            .lock()
            .unwrap()
            .process
            .as_ref()
            .map(|p| p.token.clone())
    }

    /// Simulate an external crash of the current worker (tests only).
    #[cfg(test)]
    fn kill_current_for_test(&self) {
        let mut inner = self.inner.lock().unwrap();
        if let Some(p) = inner.process.as_mut() {
            let _ = p.child.kill();
        }
    }
}

struct Supervisor {
    inner: Arc<Mutex<Inner>>,
    config: WorkerManagerConfig,
    rx: Receiver<Msg>,
    gen: u32,
}

impl Supervisor {
    fn new(inner: Arc<Mutex<Inner>>, config: WorkerManagerConfig, rx: Receiver<Msg>) -> Self {
        Self {
            inner,
            config,
            rx,
            gen: 0,
        }
    }

    fn run(&mut self) {
        let (line_tx, line_rx) = channel::<WorkerLine>();
        let (port, pid) = {
            let inner = self.inner.lock().unwrap();
            let p = inner
                .process
                .as_ref()
                .expect("supervisor started without a process");
            (p.port, p.pid)
        };
        log::info!("worker starting (pid={pid}, port={port})");
        self.attach_forwarders(line_tx.clone());

        loop {
            if self.recv_stop() {
                self.shutdown();
                return;
            }
            match self.state() {
                WorkerState::Starting => match self.wait_ready(&line_rx) {
                    ReadyPhase::Ready => {}
                    ReadyPhase::Failed => {
                        if !self.try_restart(&line_rx, &line_tx) {
                            self.fail("worker failed to start");
                            return;
                        }
                    }
                    ReadyPhase::StoppedRequested => {
                        self.shutdown();
                        return;
                    }
                },
                WorkerState::Ready => {
                    std::thread::sleep(EXIT_POLL_INTERVAL);
                    for line in self.drain(&line_rx) {
                        self.forward(line);
                    }
                    if self.child_exited_now() {
                        log::warn!("worker process exited unexpectedly");
                        if !self.try_restart(&line_rx, &line_tx) {
                            self.fail("worker crashed too many times");
                            return;
                        }
                    }
                }
                _ => return,
            }
        }
    }

    /// Wait for the `READY <token>` handshake, then poll authenticated `/health`.
    fn wait_ready(&self, rx: &Receiver<WorkerLine>) -> ReadyPhase {
        let expected = self.token();
        let ready_deadline = Instant::now() + self.config.startup_ready_timeout;

        let mut ready_token: Option<String> = None;
        while ready_token.is_none() {
            if self.recv_stop() {
                return ReadyPhase::StoppedRequested;
            }
            if self.child_exited_now() {
                return ReadyPhase::Failed;
            }
            for line in self.drain(rx) {
                if line.gen == self.gen {
                    if let Some(tok) = parse_ready(&line.text) {
                        ready_token = Some(tok.to_string());
                    } else {
                        self.forward(line);
                    }
                } else {
                    self.forward(line);
                }
            }
            if ready_token.is_none() {
                if Instant::now() >= ready_deadline {
                    log::error!("worker startup timed out waiting for READY handshake");
                    return ReadyPhase::Failed;
                }
                std::thread::sleep(POLL_STEP);
            }
        }

        if !constant_time_eq(ready_token.as_deref().unwrap_or_default(), &expected) {
            log::error!("worker READY token mismatch — refusing to mark ready");
            return ReadyPhase::Failed;
        }

        let client = WorkerClient::new(self.port(), ready_token.unwrap());
        for attempt in 0..self.config.health_poll_attempts {
            if self.recv_stop() {
                return ReadyPhase::StoppedRequested;
            }
            if self.child_exited_now() {
                return ReadyPhase::Failed;
            }
            if attempt > 0 {
                std::thread::sleep(self.config.health_poll_interval);
            }
            for line in self.drain(rx) {
                self.forward(line);
            }
            match client.check_health() {
                Ok(health) if health.status == "ok" => {
                    log::info!(
                        "worker ready (pid={}, port={}, version={})",
                        self.pid(),
                        self.port(),
                        health.version
                    );
                    let mut inner = self.inner.lock().unwrap();
                    inner.state = WorkerState::Ready;
                    inner.last_error = None;
                    return ReadyPhase::Ready;
                }
                Ok(_) => log::warn!("worker health response had unexpected status"),
                Err(HttpError::Status(401)) => {
                    log::error!("worker rejected the health token (HTTP 401)")
                }
                Err(HttpError::ConnectFailed(_)) => {} // server not up yet — retry
                Err(e) => log::warn!("worker health poll failed: {e}"),
            }
        }
        log::error!(
            "worker did not become healthy within {} attempts",
            self.config.health_poll_attempts
        );
        ReadyPhase::Failed
    }

    /// Restart after an unexpected exit. Returns false when restarts are exhausted.
    fn try_restart(&mut self, rx: &Receiver<WorkerLine>, tx: &Sender<WorkerLine>) -> bool {
        // Discard stale lines queued from the dead process.
        while rx.try_recv().is_ok() {}

        let (restarts, max) = {
            let mut inner = self.inner.lock().unwrap();
            inner.restarts += 1;
            (inner.restarts, self.config.max_restarts)
        };
        if restarts > max {
            log::error!("worker exceeded max restarts ({max}) — giving up");
            return false;
        }
        log::warn!("restarting worker ({restarts}/{max})");

        self.kill_current();
        let process = match self.spawn() {
            Ok(p) => p,
            Err(e) => {
                log::error!("worker restart spawn failed: {e}");
                return false;
            }
        };

        self.gen += 1;
        let (port, pid) = (process.port, process.pid);
        {
            let mut inner = self.inner.lock().unwrap();
            inner.state = WorkerState::Starting;
            inner.last_error = None;
            inner.port = Some(port);
            inner.process = Some(process);
        }
        log::info!("worker restarting (pid={pid}, port={port})");
        self.attach_forwarders(tx.clone());
        true
    }

    fn fail(&self, message: &str) {
        self.kill_current();
        let mut inner = self.inner.lock().unwrap();
        inner.port = None;
        inner.state = WorkerState::Failed;
        inner.last_error = Some(message.to_string());
        log::error!("worker failed: {message}");
    }

    /// Graceful shutdown: `SHUTDOWN` on stdin, bounded wait, then forced kill.
    fn shutdown(&self) {
        {
            let mut inner = self.inner.lock().unwrap();
            if inner.process.is_none() {
                inner.state = WorkerState::Stopped;
                inner.port = None;
                return;
            }
            inner.state = WorkerState::Stopping;
        }
        log::info!("worker stopping");

        {
            let mut inner = self.inner.lock().unwrap();
            if let Some(p) = inner.process.as_mut() {
                let _ = writeln!(p.stdin, "SHUTDOWN");
                let _ = p.stdin.flush();
            }
        }

        let graceful_deadline = Instant::now() + self.config.graceful_shutdown_timeout;
        while Instant::now() < graceful_deadline {
            if self.child_exited_now() {
                break;
            }
            std::thread::sleep(POLL_STEP);
        }

        if !self.child_exited_now() {
            log::warn!("worker did not exit gracefully in time — terminating");
            {
                let mut inner = self.inner.lock().unwrap();
                if let Some(p) = inner.process.as_mut() {
                    let _ = p.child.kill();
                }
            }
            let kill_deadline = Instant::now() + self.config.kill_wait_timeout;
            while Instant::now() < kill_deadline {
                if self.child_exited_now() {
                    break;
                }
                std::thread::sleep(POLL_STEP);
            }
            if !self.child_exited_now() {
                // TerminateProcess cannot be ignored; block to reap the child.
                let mut inner = self.inner.lock().unwrap();
                if let Some(p) = inner.process.as_mut() {
                    let _ = p.child.wait();
                }
            }
        }

        self.finish_stop();
    }

    fn finish_stop(&self) {
        let mut inner = self.inner.lock().unwrap();
        if let Some(mut p) = inner.process.take() {
            let _ = p.child.wait();
            drop(p.stdin);
            log::info!("worker process stopped (pid={})", p.pid);
        }
        inner.port = None;
        inner.state = WorkerState::Stopped;
        inner.last_error = None;
        inner.restarts = 0;
        log::info!("worker stopped");
    }

    fn kill_current(&self) {
        let mut inner = self.inner.lock().unwrap();
        if let Some(mut p) = inner.process.take() {
            let _ = p.stdin.flush();
            let _ = p.child.kill();
            let _ = p.child.wait();
            log::info!("worker process terminated (pid={})", p.pid);
        }
    }

    /// Forward one worker line to the Rust logger, redacting any token.
    fn forward(&self, line: WorkerLine) {
        // The `READY <token>` handshake line must never reach the logs.
        if line.text.trim_start().starts_with("READY ") {
            return;
        }
        let token = self.token();
        let msg = redact(&line.text, &token);
        let msg = msg.trim_end_matches(['\n', '\r']);
        if msg.is_empty() {
            return;
        }
        match line.stream {
            Stream::Stdout => log::info!("[worker:stdout] {msg}"),
            Stream::Stderr => log::warn!("[worker:stderr] {msg}"),
        }
    }

    /// Attach stdout/stderr forwarder threads for the current process.
    fn attach_forwarders(&self, tx: Sender<WorkerLine>) {
        let (stdout, stderr) = {
            let mut inner = self.inner.lock().unwrap();
            match inner.process.as_mut() {
                Some(p) => (p.child.stdout.take(), p.child.stderr.take()),
                None => (None, None),
            }
        };
        let gen = self.gen;

        if let Some(stdout) = stdout {
            let tx = tx.clone();
            std::thread::spawn(move || read_into_channel(stdout, Stream::Stdout, gen, tx));
        }
        if let Some(stderr) = stderr {
            std::thread::spawn(move || read_into_channel(stderr, Stream::Stderr, gen, tx));
        }
    }

    fn drain(&self, rx: &Receiver<WorkerLine>) -> Vec<WorkerLine> {
        let mut lines = Vec::new();
        while let Ok(line) = rx.try_recv() {
            lines.push(line);
        }
        lines
    }

    fn recv_stop(&self) -> bool {
        matches!(self.rx.try_recv(), Ok(Msg::Stop))
    }

    fn state(&self) -> WorkerState {
        self.inner.lock().unwrap().state
    }

    fn pid(&self) -> u32 {
        self.inner
            .lock()
            .unwrap()
            .process
            .as_ref()
            .map(|p| p.pid)
            .unwrap_or(0)
    }

    fn port(&self) -> u16 {
        self.inner.lock().unwrap().port.unwrap_or(0)
    }

    fn token(&self) -> String {
        self.inner
            .lock()
            .unwrap()
            .process
            .as_ref()
            .map(|p| p.token.clone())
            .unwrap_or_default()
    }

    fn child_exited_now(&self) -> bool {
        let mut inner = self.inner.lock().unwrap();
        match inner.process.as_mut() {
            None => true,
            Some(p) => match p.child.try_wait() {
                Ok(Some(_)) => true,
                Ok(None) => false,
                Err(e) => {
                    log::warn!("failed to poll worker process: {e}");
                    true
                }
            },
        }
    }

    /// Shared spawn logic used by the manager and restarts.
    fn spawn(&self) -> Result<WorkerProcess, String> {
        spawn_worker(&self.config)
    }
}

/// Read lines from a worker pipe into the shared channel until EOF/error.
fn read_into_channel(
    stream: impl Read + Send + 'static,
    stream_kind: Stream,
    gen: u32,
    tx: Sender<WorkerLine>,
) {
    let mut reader = BufReader::new(stream);
    let mut line = String::new();
    loop {
        line.clear();
        match reader.read_line(&mut line) {
            Ok(0) | Err(_) => break,
            Ok(_) => {
                if tx
                    .send(WorkerLine {
                        gen,
                        stream: stream_kind,
                        text: line.clone(),
                    })
                    .is_err()
                {
                    break;
                }
            }
        }
    }
}

fn spawn_worker(config: &WorkerManagerConfig) -> Result<WorkerProcess, String> {
    let python = resolve_python(config)?;
    let worker_dir = resolve_worker_dir(config)?;
    let port =
        pick_ephemeral_port().map_err(|e| format!("failed to allocate ephemeral port: {e}"))?;
    let token = generate_token().map_err(|e| format!("failed to generate session token: {e}"))?;

    log::info!(
        "spawning worker: {} -m src.main --port {port} (dir={})",
        python.display(),
        worker_dir.display()
    );

    let mut cmd = Command::new(&python);
    cmd.arg("-m")
        .arg("src.main")
        .arg("--port")
        .arg(port.to_string());
    cmd.current_dir(&worker_dir);
    // The sidecar must not write `.pyc` caches into the source tree (it would
    // trip the `tauri dev` file watcher and slow the first import).
    cmd.env("PYTHONDONTWRITEBYTECODE", "1");
    cmd.stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    let mut child = cmd
        .spawn()
        .map_err(|e| format!("failed to spawn python worker `{}`: {e}", python.display()))?;
    let pid = child.id();
    let mut stdin = child
        .stdin
        .take()
        .ok_or_else(|| "worker stdin unavailable".to_string())?;

    let write_result = (|| -> std::io::Result<()> {
        writeln!(stdin, "WORKER_AUTH_TOKEN={token}")?;
        stdin.flush()
    })();
    if let Err(e) = write_result {
        let _ = child.kill();
        let _ = child.wait();
        return Err(format!("failed to write token to worker stdin: {e}"));
    }

    Ok(WorkerProcess {
        child,
        stdin,
        port,
        token,
        pid,
    })
}

/// OS-assigned ephemeral port on loopback (avoids fixed/guessable ports).
fn pick_ephemeral_port() -> std::io::Result<u16> {
    let listener = TcpListener::bind((Ipv4Addr::from(HOST_LOOPBACK), 0))?;
    let port = listener.local_addr()?.port();
    Ok(port)
}

/// 256-bit cryptographically secure random token, hex-encoded (64 chars).
fn generate_token() -> Result<String, String> {
    let mut bytes = [0u8; 32];
    getrandom::getrandom(&mut bytes).map_err(|e| e.to_string())?;
    Ok(bytes.iter().map(|b| format!("{b:02x}")).collect())
}

/// Resolve the Python interpreter: config → `WORKER_PYTHON` → PATH `python`.
///
/// Bundled (release) binary resolution is a later packaging phase.
fn resolve_python(config: &WorkerManagerConfig) -> Result<PathBuf, String> {
    if let Some(p) = &config.python {
        // A bare command name (no separator) is resolved via PATH; only explicit
        // paths need to exist up front.
        if is_bare_command(p) || p.exists() {
            return Ok(p.clone());
        }
        return Err(format!(
            "configured python `{}` does not exist",
            p.display()
        ));
    }
    if let Ok(env) = std::env::var("WORKER_PYTHON") {
        if !env.trim().is_empty() {
            return Ok(PathBuf::from(env.trim()));
        }
    }
    Ok(PathBuf::from("python"))
}

fn is_bare_command(path: &std::path::Path) -> bool {
    let s = path.as_os_str().to_string_lossy();
    !s.contains('\\') && !s.contains('/')
}

/// Resolve the worker package directory (where `src.main` lives).
fn resolve_worker_dir(config: &WorkerManagerConfig) -> Result<PathBuf, String> {
    if let Some(d) = &config.worker_dir {
        if d.is_dir() {
            return Ok(d.clone());
        }
        return Err(format!("worker dir `{}` does not exist", d.display()));
    }
    let from_manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("worker");
    if from_manifest.is_dir() {
        return Ok(from_manifest);
    }
    let cwd = std::env::current_dir().map_err(|e| format!("cannot resolve cwd: {e}"))?;
    let cwd_worker = cwd.join("worker");
    if cwd_worker.is_dir() {
        return Ok(cwd_worker);
    }
    Err("cannot locate the worker directory (set worker_dir or run from the repo)".into())
}

/// Parse the `READY <token>` protocol line.
fn parse_ready(line: &str) -> Option<&str> {
    let token = line.strip_prefix("READY ")?;
    let token = token.trim_end_matches(['\n', '\r']);
    if token.is_empty() {
        None
    } else {
        Some(token)
    }
}

/// Constant-time string comparison (avoid early-exit timing signal).
fn constant_time_eq(a: &str, b: &str) -> bool {
    if a.len() != b.len() {
        return false;
    }
    let mut diff = 0u8;
    for (x, y) in a.as_bytes().iter().zip(b.as_bytes().iter()) {
        diff |= x ^ y;
    }
    diff == 0
}

/// Replace any occurrence of the session token in a log line.
fn redact(line: &str, token: &str) -> String {
    if token.is_empty() || !line.contains(token) {
        line.to_string()
    } else {
        line.replace(token, "***")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // ---- pure helpers ----------------------------------------------------

    #[test]
    fn token_is_64_hex_chars_and_unique() {
        let a = generate_token().expect("token");
        let b = generate_token().expect("token");
        assert_eq!(a.len(), 64);
        assert_eq!(b.len(), 64);
        assert_ne!(a, b);
        assert!(a.chars().all(|c| c.is_ascii_hexdigit()));
    }

    #[test]
    fn ephemeral_port_is_loopback_and_rebindable() {
        let port = pick_ephemeral_port().expect("port");
        assert!(port > 0);
        // The manager releases the listener before the worker binds; the port
        // must therefore be rebindable on loopback.
        let listener = TcpListener::bind((Ipv4Addr::from(HOST_LOOPBACK), port)).expect("rebind");
        let addr = listener.local_addr().expect("addr");
        assert!(addr.ip().is_loopback());
        assert_eq!(addr.port(), port);
    }

    #[test]
    fn parse_ready_line() {
        assert_eq!(parse_ready("READY abc123\n"), Some("abc123"));
        assert_eq!(parse_ready("READY abc123\r\n"), Some("abc123"));
        assert_eq!(parse_ready("READY "), None);
        assert_eq!(parse_ready("ready abc"), None);
        assert_eq!(parse_ready("{}json\n"), None);
    }

    #[test]
    fn constant_time_eq_works() {
        assert!(constant_time_eq("abc", "abc"));
        assert!(!constant_time_eq("abc", "abd"));
        assert!(!constant_time_eq("abc", "ab"));
        assert!(!constant_time_eq("ab", "abc"));
    }

    #[test]
    fn redact_hides_token() {
        assert_eq!(redact("hello abc123 world", "abc123"), "hello *** world");
        assert_eq!(redact("no secret", "abc123"), "no secret");
        assert_eq!(redact("abc123abc123", "abc123"), "******");
        assert_eq!(redact("x", ""), "x");
    }

    #[test]
    fn state_machine_rejects_duplicate_start() {
        let config = WorkerManagerConfig {
            python: Some(PathBuf::from("definitely-not-a-real-python")),
            max_restarts: 0,
            health_poll_attempts: 1,
            health_poll_interval: Duration::from_millis(10),
            startup_ready_timeout: Duration::from_millis(100),
            ..Default::default()
        };
        let manager = WorkerManager::new(config);
        // start fails deterministically (no such interpreter) -> Failed state.
        let err = manager.start().unwrap_err();
        assert!(err.contains("python"), "got: {err}");
        assert_eq!(manager.state_info().state, WorkerState::Failed);
        // starting again from Failed is allowed; it fails again deterministically.
        assert!(manager.start().is_err());
        // stop is a safe no-op in every state.
        manager.stop();
        manager.stop();
    }

    // ---- real worker integration (skipped when python/worker not available) --

    fn test_python() -> Option<PathBuf> {
        std::env::var("WORKER_PYTHON")
            .ok()
            .filter(|v| !v.trim().is_empty())
            .map(PathBuf::from)
            .or_else(|| Some(PathBuf::from("python")))
    }

    fn probe_worker_available() -> bool {
        let Some(python) = test_python() else {
            return false;
        };
        match Command::new(&python)
            .args(["-c", "import fastapi, uvicorn; print('ok')"])
            .output()
        {
            Ok(out) => out.status.success() && String::from_utf8_lossy(&out.stdout).trim() == "ok",
            Err(_) => false,
        }
    }

    fn fast_config() -> WorkerManagerConfig {
        WorkerManagerConfig {
            python: test_python(),
            max_restarts: MAX_RESTARTS_DEFAULT,
            health_poll_attempts: 20,
            health_poll_interval: Duration::from_millis(250),
            startup_ready_timeout: Duration::from_secs(10),
            graceful_shutdown_timeout: Duration::from_secs(3),
            kill_wait_timeout: Duration::from_secs(2),
            ..Default::default()
        }
    }

    fn wait_until_ready(manager: &WorkerManager, timeout: Duration) -> Option<WorkerClient> {
        let deadline = Instant::now() + timeout;
        loop {
            if let Some(client) = manager.worker_client() {
                return Some(client);
            }
            let info = manager.state_info();
            if info.state == WorkerState::Failed {
                panic!("worker failed: {:?}", info.last_error);
            }
            if Instant::now() >= deadline {
                return None;
            }
            std::thread::sleep(POLL_STEP);
        }
    }

    #[test]
    fn real_worker_starts_ready_and_shuts_down() {
        if !probe_worker_available() {
            eprintln!("SKIP real_worker_starts_ready_and_shuts_down: python worker not available");
            return;
        }
        let manager = WorkerManager::new(fast_config());
        manager.start().expect("start");

        let client = wait_until_ready(&manager, Duration::from_secs(20)).expect("worker ready");
        assert_eq!(client.check_health().expect("health").status, "ok");

        let info = manager.state_info();
        assert_eq!(info.state, WorkerState::Ready);
        assert!(info.port.is_some());
        assert!(info.pid.is_some());

        // The token must never leak into the serialized IPC snapshot.
        let token = manager.session_token().expect("session token");
        assert!(!serde_json::to_string(&info).unwrap().contains(&token));

        // Graceful shutdown -> process gone, port released, token cleared.
        manager.stop();
        let info = manager.state_info();
        assert_eq!(info.state, WorkerState::Stopped);
        assert!(info.pid.is_none());
        assert!(info.port.is_none());
        assert_eq!(manager.session_token(), None);
    }

    #[test]
    fn real_worker_restarts_after_crash() {
        if !probe_worker_available() {
            eprintln!("SKIP real_worker_restarts_after_crash: python worker not available");
            return;
        }
        let manager = WorkerManager::new(fast_config());
        manager.start().expect("start");

        let _client = wait_until_ready(&manager, Duration::from_secs(20)).expect("worker ready");
        let first_pid = manager.state_info().pid.expect("pid");

        // Simulate an external crash by force-killing the worker process.
        manager.kill_current_for_test();

        let deadline = Instant::now() + Duration::from_secs(25);
        let restarted = loop {
            let info = manager.state_info();
            if info.state == WorkerState::Ready
                && info.pid.is_some()
                && info.pid != Some(first_pid)
                && info.restarts >= 1
            {
                break true;
            }
            if info.state == WorkerState::Failed {
                panic!("worker failed after crash: {:?}", info.last_error);
            }
            if Instant::now() >= deadline {
                break false;
            }
            std::thread::sleep(POLL_STEP);
        };
        assert!(restarted, "worker did not restart after crash");

        let client = manager.worker_client().expect("ready client");
        assert_eq!(client.check_health().expect("health").status, "ok");

        manager.stop();
        assert_eq!(manager.state_info().state, WorkerState::Stopped);
    }

    #[test]
    fn real_worker_startup_failure_cleans_up() {
        if !probe_worker_available() {
            eprintln!("SKIP real_worker_startup_failure_cleans_up: python worker not available");
            return;
        }
        // A fake `src` package that exits immediately: the process dies during
        // startup, which must trigger the bounded restart logic and cleanup.
        let bad_dir =
            std::env::temp_dir().join(format!("tooltranslate_bad_worker_{}", std::process::id()));
        let _ = std::fs::create_dir_all(bad_dir.join("src"));
        std::fs::write(bad_dir.join("src").join("__init__.py"), b"").expect("write __init__");
        std::fs::write(
            bad_dir.join("src").join("main.py"),
            b"import sys\nsys.stdin.readline()\nsys.exit(1)\n",
        )
        .expect("write main");

        let config = WorkerManagerConfig {
            python: test_python(),
            worker_dir: Some(bad_dir.clone()),
            max_restarts: 1,
            health_poll_attempts: 1,
            health_poll_interval: Duration::from_millis(10),
            startup_ready_timeout: Duration::from_secs(2),
            graceful_shutdown_timeout: Duration::from_secs(1),
            kill_wait_timeout: Duration::from_secs(1),
        };
        let manager = WorkerManager::new(config);
        manager.start().expect("spawn ok, worker will fail");

        let deadline = Instant::now() + Duration::from_secs(20);
        loop {
            let info = manager.state_info();
            if info.state == WorkerState::Failed {
                break;
            }
            if Instant::now() >= deadline {
                panic!(
                    "worker did not reach Failed in time: {:?}",
                    manager.state_info()
                );
            }
            std::thread::sleep(POLL_STEP);
        }

        // Cleanup must leave no process and no port behind.
        let info = manager.state_info();
        assert!(info.pid.is_none());
        assert!(info.port.is_none());
        assert_eq!(manager.session_token(), None);

        manager.stop();
        assert_eq!(manager.state_info().state, WorkerState::Stopped);

        let _ = std::fs::remove_dir_all(&bad_dir);
    }
}
