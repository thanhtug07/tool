//! Minimal console logger for the Rust core (TASK-006).
//!
//! Lifecycle events are logged as single `[LEVEL] target: message` lines.
//! Secrets (the worker bearer token, API keys) are never logged by construction
//! — redaction happens at the call sites in `worker_manager`.

use std::io::Write;
use std::sync::atomic::{AtomicBool, Ordering};

use log::{LevelFilter, Log, Metadata, Record};

struct ConsoleLogger;

static CONSOLE_LOGGER: ConsoleLogger = ConsoleLogger;
static INITIALIZED: AtomicBool = AtomicBool::new(false);

impl Log for ConsoleLogger {
    fn enabled(&self, _metadata: &Metadata<'_>) -> bool {
        true
    }

    fn log(&self, record: &Record<'_>) {
        let level = record.level();
        let line = format!("[{}] {}: {}", level, record.target(), record.args());
        let _ = writeln!(std::io::stdout(), "{line}");
    }

    fn flush(&self) {
        let _ = std::io::stdout().flush();
    }
}

/// Install the console logger once. Safe to call multiple times.
pub fn init() {
    if INITIALIZED.swap(true, Ordering::SeqCst) {
        return;
    }
    let _ = log::set_logger(&CONSOLE_LOGGER);
    log::set_max_level(LevelFilter::Info);
}
