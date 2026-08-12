# AI Video Localization Studio

Desktop app (Windows 10/11) dành cho localization video: tự động **transcribe (STT)**, **dịch (translation)**, **tạo subtitle**, và **render** video có subtitle đã dịch sang ngôn ngữ khác.

> **Trạng thái: Sprint 0 — Foundation.** Scaffold (TASK-001), typed IPC (TASK-004), Python sidecar + `/health` + lifecycle (TASK-005/006) và shared schemas/contracts (TASK-007) đã xong. Chưa có tính năng UI/worker AI; đừng dùng cho production.

## Mục tiêu (MVP scope)

- Import video → STT (faster-whisper/whisper.cpp, chạy local, không upload).
- Chỉnh sửa transcript & dịch tự động (nhiều provider: OpenAI/Gemini/Anthropic/local LLM).
- Subtitle editor + render video có subtitle đã dịch (FFmpeg), watermark, preset chất lượng.
- Hoàn toàn local-first: model, dữ liệu, project nằm trên máy user.

Ngoài MVP (KHÔNG làm trong giai đoạn đầu): dubbing, audio separation, OCR, voice cloning, timeline editor, billing, cloud backend.

## Kiến trúc tổng quan

- **Tauri 2** shell (Rust core) — `src-tauri/`.
- **Frontend:** React + TypeScript + Vite + Tailwind (shadcn/ui) — `src/`.
- **AI worker:** Python 3.11 sidecar (FastAPI trên localhost, PyTorch/faster-whisper) — `worker/`.
- **Database:** SQLite (WAL) trong app-data.
- **Binary:** FFmpeg + models tải về trong `vendor/` (không commit).

Chi tiết: [`ARCHITECTURE_DECISION.md`](ARCHITECTURE_DECISION.md) (frozen), [`MASTER_PLAN.md`](MASTER_PLAN.md).

## Cấu trúc repository

```
├── MASTER_PLAN.md            # Source of truth (FROZEN)
├── ARCHITECTURE_DECISION.md  # Quyết định kiến trúc + ADR
├── IMPLEMENTATION_ROADMAP.md # Lộ trình
├── TASKS.md                  # Danh sách task chi tiết + AI agent policy
├── QUALITY_BENCHMARK.md      # Ngưỡng chất lượng translation
├── GOLDEN_VIDEO_TEST.md      # Video test chuẩn cho STT
├── .github/workflows/        # CI (bắt đầu từ TASK-002)
├── src-tauri/                # Rust core (Tauri)
├── src/                      # Frontend React
├── worker/                   # Python AI worker (sidecar)
├── schemas/                  # JSON schema (single source of truth)
├── scripts/                  # build_sidecar, fetch_ffmpeg, verify_release
├── docs/                     # Tài liệu bổ sung
└── vendor/                   # Binaries/models (gitignored)
```

## Prerequisites (đã pin)

| Tool    | Version              | Ghi chú                                                               |
| ------- | -------------------- | --------------------------------------------------------------------- |
| Node.js | 24.x (dev: v24.16.0) | npm đi kèm (11.x)                                                     |
| Rust    | 1.96.x (dev: 1.96.0) | Cargo toolchain                                                       |
| Python  | 3.11 (dev: 3.13.7)   | Worker sidecar — xem ADR. 3.13 là dev machine; architecture chốt 3.11 |
| FFmpeg  | —                    | Tải qua `scripts/fetch_ffmpeg.ps1`                                    |
| Git     | 2.x                  |                                                                       |

> Dev machine hiện có Python 3.13.7, nhưng architecture chốt **Python 3.11** cho worker (xem MASTER_PLAN_REVIEW I9 — re-verify 3.12/3.13 ở Phase 13). `.python-version` / pyproject sẽ ghi rõ.

## Quickstart

```powershell
# 1. Clone & cài frontend deps
git clone <repo-url> && cd ai-video-localization
npm install

# 2. Worker (Python)
cd worker
python -m pip install -e ".[dev]"   # hoặc uv sync

# 3. Rust check
cargo check

# 4. Chạy dev
npm run tauri dev
```

## Worker (Python) — dev guide

- **Python version:** 3.11 (canonical; dev machine 3.13.7, range `>=3.11,<3.14`).
- **Cài đặt:** `cd worker && python -m pip install -e ".[dev]"`.
- **Test:** `cd worker && python -m pytest` (hoặc từ root: `python -m pytest worker/tests` — bỏ qua `ai` marker mặc định).
- **Chạy worker (dev):** `cd worker && python -m src.main --port 8765` — bind **chỉ `127.0.0.1`** (không LAN). Port mặc định `8765` (đổi qua `--port` hoặc biến môi trường `WORKER_PORT`).
- **Health:** `GET /health` trả `{"status": "ok", "version": "0.1.0", "gpu": null}` — deterministic, không rò secret/path/env. Yêu cầu header `Authorization: Bearer <token>` (mặc định placeholder `dev-placeholder-token`, override qua `WORKER_AUTH_TOKEN` hoặc `configure_auth_token` khi chạy sidecar).
  ```powershell
  Invoke-RestMethod -Uri "http://127.0.0.1:8765/health" -Headers @{ Authorization = "Bearer dev-placeholder-token" }
  ```
- **Sidecar mode (TASK-006):** Rust (`WorkerManager`) tự spawn worker khi app khởi động — port ephemeral random trên `127.0.0.1`, token 256-bit random mỗi session truyền qua **stdin** (không qua argv/env/log/UI). Worker echo `READY <token>` trên stdout sau khi bind; Rust poll `/health` có auth → `READY`; nhận `SHUTDOWN` trên stdin → graceful exit. Override interpreter dev bằng biến môi trường `WORKER_PYTHON` (mặc định dùng `python` trên PATH).
- Chưa có STT / translation / GPU detect (các task sau).

## Shared schemas / contracts (TASK-007)

- **Source of truth:** `schemas/*.schema.json` — JSON Schema (draft 2020-12) cho toàn bộ contract dùng chung giữa TS frontend, Rust core và Python worker. KHÔNG có Zod/Pydantic/Serde-only hay interface trùng lặp thủ công làm source of truth.
- **Bộ contract hiện tại:** worker health, worker state, error envelope (`{"error": {code, message, recoverable}}`), job state, và data document transcript / translation / subtitle (MASTER_PLAN §24). `schemas/examples/valid/` + `schemas/examples/invalid/` chứa fixture hợp lệ/bị reject dùng chung cho cả 3 tầng.
- **Python:** `worker/src/api/schemas.py` **được generate** từ `schemas/` bằng `datamodel-code-generator` — không sửa tay. Re-gen: `python scripts/generate_schemas.py` (idempotent, có test).
- **TypeScript:** `src/types/api.ts` mirror schema; `src/types/api.test.ts` kiểm tra drift (so canonical fixtures với type + schema).
- **Rust:** struct mirror trong `src-tauri/src/services/` (`HealthResponse`, `WorkerStateInfo`, `ErrorResponse`) + test cross-language parse fixtures (`services/contract_tests.rs`).
- **Quy tắc:** schema không bao giờ chứa secret (token runtime-only — MASTER_PLAN §24). Thay đổi contract → sửa JSON Schema → re-gen Python → cập nhật TS/Rust nếu cần → chạy test 3 tầng.

## Lệnh thường dùng

| Action                | Command                              |
| --------------------- | ------------------------------------ |
| Frontend dev          | `npm run dev`                        |
| Frontend typecheck    | `npm run typecheck`                  |
| Frontend lint         | `npm run lint`                       |
| Frontend format       | `npm run format`                     |
| Frontend test         | `npm run test`                       |
| Rust check            | `cargo check`                        |
| Rust test             | `cargo test`                         |
| Worker test           | `pytest worker/tests`                |
| Re-gen worker schemas | `python scripts/generate_schemas.py` |

## CI (GitHub Actions)

Workflow: `.github/workflows/ci.yml` — chạy trên mọi **Pull Request** và **push lên `main`**.

| Job        | Runner         | Checks                                                                             |
| ---------- | -------------- | ---------------------------------------------------------------------------------- |
| `frontend` | windows-latest | `npm ci` + typecheck + lint + format:check + test (vitest) + build                 |
| `rust`     | windows-latest | `cargo fmt --check` + `cargo check` + `cargo clippy -- -D warnings` + `cargo test` |
| `worker`   | ubuntu-latest  | Python 3.11 + `pip install -e .[dev]` + `pytest` (bỏ `ai` marker)                  |
| `licenses` | ubuntu-latest  | cargo-deny license audit (whitelist trong `deny.toml`)                             |
| `security` | ubuntu-latest  | gitleaks secret scan (fail nếu có secret/credential)                               |

- CI dùng lockfiles: `package-lock.json` (npm) + `Cargo.lock`.
- Không có workflow release/deploy ở TASK-002 — CI sign/release sẽ được thêm ở phase sau.
- Branch protection cho `main` (PR bắt buộc + required status checks) cấu hình trên GitHub sau khi repo được push.
- **CI badge:** thêm sau khi repo có remote/owner trên GitHub (`/action/workflows/ci.yml/badge.svg`).

## Tài liệu liên quan (hiện có)

- [ARCHITECTURE_DECISION.md](ARCHITECTURE_DECISION.md) — kiến trúc + ADR (frozen)
- [IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md) — lộ trình
- [TASKS.md](TASKS.md) — task chi tiết + AI agent policy
- [QUALITY_BENCHMARK.md](QUALITY_BENCHMARK.md), [GOLDEN_VIDEO_TEST.md](GOLDEN_VIDEO_TEST.md)
- [DEVELOPMENT.md](DEVELOPMENT.md) — hướng dẫn phát triển (setup, test, build)
- [API.md](API.md) — Tauri IPC + worker HTTP API
- [DATABASE.md](DATABASE.md) — schema SQLite + migrations
- [SECURITY.md](SECURITY.md) — mô hình bảo mật
- [AI_PIPELINE.md](AI_PIPELINE.md), [VIDEO_PIPELINE.md](VIDEO_PIPELINE.md), [AUDIO_PIPELINE.md](AUDIO_PIPELINE.md) — pipeline AI / video / audio
- [TESTING.md](TESTING.md) — chiến lược test + lệnh chạy
- [RELEASE.md](RELEASE.md) — build/packaging/release gates
- [LICENSING.md](LICENSING.md) — bảng license + checklist
- [docs/USER_GUIDE.md](docs/USER_GUIDE.md) — hướng dẫn cài đặt & sử dụng cho người dùng cuối

## Trạng thái / Roadmap

Xem [IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md) và [TASKS.md](TASKS.md). Sprint 0 (Foundation) đang chạy từ TASK-001.

## License

**TODO — CHƯA QUYẾT ĐỊNH.** Sẽ xác nhận trước release; xem [LICENSING.md](LICENSING.md) khi có.
