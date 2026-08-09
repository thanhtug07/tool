# AI Video Localization Studio

Desktop app (Windows 10/11) dành cho localization video: tự động **transcribe (STT)**, **dịch (translation)**, **tạo subtitle**, và **render** video có subtitle đã dịch sang ngôn ngữ khác.

> **Trạng thái: Sprint 0 — Foundation (TASK-001).** Đang xây nền tảng repo; chưa có tính năng UI/worker nào. Đừng dùng cho production.

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

## Lệnh thường dùng

| Action             | Command               |
| ------------------ | --------------------- |
| Frontend dev       | `npm run dev`         |
| Frontend typecheck | `npm run typecheck`   |
| Frontend lint      | `npm run lint`        |
| Frontend format    | `npm run format`      |
| Rust check         | `cargo check`         |
| Rust test          | `cargo test`          |
| Worker test        | `pytest worker/tests` |

## Tài liệu liên quan (hiện có)

- [ARCHITECTURE_DECISION.md](ARCHITECTURE_DECISION.md) — kiến trúc + ADR (frozen)
- [IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md) — lộ trình
- [TASKS.md](TASKS.md) — task chi tiết + AI agent policy
- [QUALITY_BENCHMARK.md](QUALITY_BENCHMARK.md), [GOLDEN_VIDEO_TEST.md](GOLDEN_VIDEO_TEST.md)

Các docs `DEVELOPMENT.md`, `API.md`, `SECURITY.md`, `TESTING.md`, `RELEASE.md`, `LICENSING.md` (theo `MASTER_PLAN.md §22`) sẽ được tạo ở các task sau trong Sprint 0/1.

## Trạng thái / Roadmap

Xem [IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md) và [TASKS.md](TASKS.md). Sprint 0 (Foundation) đang chạy từ TASK-001.

## License

**TODO — CHƯA QUYẾT ĐỊNH.** Sẽ xác nhận trước release; xem [LICENSING.md](docs/LICENSING.md) khi có.
