# LICENSING.md

Licensing record for **AI Video Localization Studio**. Required doc per `MASTER_PLAN.md §22/§44`; maps to the frozen licensing table in `MASTER_PLAN §21` and the LICENSING CHECKLIST in `TASKS.md §6`.

> **RULE (`MASTER_PLAN §21`):** do not assert a license that has not been verified against the actual upstream `LICENSE` file at the time of the release build.

## Project license

**TODO — UNDECIDED.** The app's own license has not been chosen (`README.md` "License" section marks it undecided). `src-tauri/Cargo.toml` sets `publish = false` and excludes the app crate from the cargo-deny audit for this reason. **A `LICENSE` file is added once the owner decides.** Do not distribute until decided.

## MVP runtime dependencies

Verified research status (2026) per `MASTER_PLAN §21` — re-verify each `LICENSE` file at release-build time.

| Dependency                 | License                    | Role                | Commercial | Notes                                                                                                                          |
| -------------------------- | -------------------------- | ------------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------ |
| Tauri 2                    | MIT / Apache-2.0 (dual)    | app shell           | ✅         | Bundle WebView2 (Microsoft, free)                                                                                              |
| React / ReactDOM           | MIT                        | UI                  | ✅         |                                                                                                                                |
| Tailwind / shadcn/ui       | MIT                        | UI                  | ✅         |                                                                                                                                |
| Rust crates (`Cargo.lock`) | MIT / Apache-2.0           | core                | ✅         | cargo-deny CI + whitelist                                                                                                      |
| rusqlite (bundled SQLite)  | MIT / Apache-2.0           | DB                  | ✅         | compiled in, self-contained                                                                                                    |
| keyring                    | MIT/Apache-2.0 per crate   | secrets             | ✅         | Windows Credential Manager                                                                                                     |
| Python                     | PSF                        | worker runtime      | ✅         | bundled via PyInstaller/standalone                                                                                             |
| fastapi / uvicorn          | MIT / BSD-3-Clause         | worker API          | ✅         |                                                                                                                                |
| faster-whisper             | MIT                        | STT                 | ✅         | model weights license is separate                                                                                              |
| Whisper models (OpenAI)    | MIT                        | STT weights         | ✅         | attribution encouraged (add to About)                                                                                          |
| whisper.cpp                | MIT                        | STT fallback        | ✅         | use verified build                                                                                                             |
| Silero VAD                 | MIT                        | VAD                 | ✅         |                                                                                                                                |
| jsonschema                 | MIT                        | manifest validation | ✅         |                                                                                                                                |
| FFmpeg / FFprobe           | LGPL/GPL (build-dependent) | media               | ✅         | use a verified LGPL-safe static build; LGPL requires dynamic-linking or source/object offer; must ship an FFmpeg license table |
| libass                     | ISC                        | subtitle burn-in    | ✅         | inside the FFmpeg build                                                                                                        |
| x264 / x265 / SVT-AV1      | GPL / MPL / BSD            | encoders            | ⚠️         | GPL encoders require care in LGPL-safe build; verify the vendored build's feature set                                          |

## Explicitly EXCLUDED from the MVP (non-commercial / post-MVP)

| Dependency                                  | License                  | Reason                          |
| ------------------------------------------- | ------------------------ | ------------------------------- |
| ProPainter                                  | NTU S-Lab non-commercial | NOT used — removed              |
| XTTS v2                                     | CPML non-commercial      | NOT used — removed              |
| F5-TTS                                      | CC-BY-NC-4.0 weights     | NOT used — removed              |
| Viterbox                                    | CC-BY-NC-4.0             | NOT used — removed              |
| Kokoro-Vietnamese (community)               | unclear provenance       | pending verification — excluded |
| Demucs / RapidOCR / PaddleOCR / TTS dubbing | see §21                  | post-MVP — not in release       |

## Checklist status (`TASKS.md §6`)

- [ ] Project license decided + `LICENSE` file added (owner decision).
- [ ] Whisper model MIT attribution recorded in About/LICENSING.md.
- [ ] FFmpeg LGPL compliance table included with the shipped build.
- [ ] `cargo-deny license audit` run and recorded (whitelist in `deny.toml`; CI job exists).
- [ ] `pip-licenses` audit of the bundled Python runtime recorded.

**Status:** table above reflects the frozen `MASTER_PLAN §21` research; the runtime audits (`cargo-deny`, `pip-licenses`) have **not** been executed on this machine (tools unavailable) and are recorded as NOT RUN in `RELEASE_READINESS_AUDIT.md`. They must run before release.
