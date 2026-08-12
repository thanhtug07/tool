@echo off
rem ---------------------------------------------------------------------------
rem dev.cmd - launcher for `npm run tauri dev`.
rem
rem The Rust toolchain (rustup) installs to %USERPROFILE%\.cargo\bin, which is
rem often missing from PATH in a fresh cmd window. Without it, tauri fails
rem immediately with:
rem   failed to run 'cargo metadata' ... program not found
rem
rem This script prepends the cargo bin directory to PATH, moves to the project
rem root, and then runs the standard dev command (it blocks until you close
rem the app window).
rem ---------------------------------------------------------------------------

set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"

cd /d "%~dp0.."

where cargo >nul 2>nul
if errorlevel 1 (
    echo [dev.cmd] ERROR: cargo not found at %%USERPROFILE%%\.cargo\bin
    echo [dev.cmd] Install the Rust toolchain via https://rustup.rs and retry.
    exit /b 1
)

echo [dev.cmd] cargo: %PATH:~0,80%...
echo [dev.cmd] Starting Tauri dev build (first run compiles Rust - be patient)...
npm run tauri dev
