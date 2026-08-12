"use strict";

/**
 * tauri.cjs — cross-shell launcher for the local Tauri CLI.
 *
 * `npm run tauri dev` / `npm run tauri build` resolve the @tauri-apps/cli
 * binary, but the Rust toolchain (cargo/rustc) that it spawns lives in
 * `%USERPROFILE%\.cargo\bin`, which is often missing from PATH in a fresh
 * shell. Without it the CLI fails immediately with:
 *
 *   failed to run 'cargo metadata' ... program not found
 *
 * This launcher prepends the cargo bin directory to PATH and then runs the
 * local CLI via `node`, forwarding exit codes/signals. It works in cmd,
 * PowerShell and Git Bash alike.
 */

const { spawn } = require("node:child_process");
const path = require("node:path");
const os = require("node:os");

const root = path.join(__dirname, "..");

// rustup installs the toolchain here on Windows (and ~/.cargo/bin elsewhere).
const cargoBin = path.join(os.homedir(), ".cargo", "bin");

const env = {
  ...process.env,
  PATH: `${cargoBin}${path.delimiter}${process.env.PATH ?? ""}`,
};

// Local CLI entry (node_modules/@tauri-apps/cli/tauri.js) — spawned via `node`
// so we never depend on platform-specific .cmd shims.
const cliJs = path.join(root, "node_modules", "@tauri-apps", "cli", "tauri.js");
const args = process.argv.slice(2);

const child = spawn(process.execPath, [cliJs, ...args], {
  env,
  stdio: "inherit",
});

child.on("error", (error) => {
  console.error("[tauri] failed to launch the Tauri CLI:", error.message);
  process.exit(1);
});

child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code ?? 1);
});
