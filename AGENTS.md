# AGENTS.md — Hướng dẫn cho AI coding agent

## Quy tắc cứng (vi phạm → dừng task)

1. **KHÔNG tự ý đổi architecture/scope/provider/model** khi chưa có approval. Thay đổi vượt phạm vi task → dừng, trình proposal, chờ chấp thuận.
2. **KHÔNG mở rộng MVP scope** (không dubbing/separation/OCR removal/voice cloning/timeline/billing/cloud backend).
3. **Chất lượng bắt buộc** trước khi báo "xong":
   - Frontend: typecheck + lint + format + test đều pass.
   - Rust: `cargo check` / `cargo test` pass.
   - Worker: import smoke (`python -c "import src.main"` từ `worker/`) không lỗi.
   - Không regression bảo mật (secret/log/credential).
   - Không hard-code: model name, API key, kết luận license, tham số subtitle.
4. **Dữ liệu chưa có** (model, transcript, API key, dataset) → dùng fixture/mock theo quy ước. Không bịa số liệu.

## Bảo mật

- **KHÔNG BAO GIỜ commit:** `.env`, `*.key`, `*.pfx`, `*.pem`, API key, credential. Đã chặn trong `.gitignore`.
- Không log secret/API key/path nhạy cảm.
- Trước khi commit, chạy `git status` và rà lại file sẽ add.

## Lệnh kiểm tra

| Layer | Command |
|---|---|
| Frontend typecheck | `npm run typecheck` (tsc --noEmit) |
| Frontend lint | `npm run lint` (eslint) |
| Frontend format | `npm run format:check` (prettier) |
| Frontend test | `npm run test` (vitest) |
| Frontend build | `npm run build` (vite) |
| Rust | `cargo check` / `cargo test` (CI thêm: `cargo fmt --check`, `cargo clippy -- -D warnings`) |
| Worker smoke | `python -c "import src.main"` (từ `worker/`) |
| Secret scan | `gitleaks detect` |

Thứ tự trước khi báo xong: **typecheck + lint + format + test layer liên quan** → tất cả pass.

## Cấu trúc & conventions

- Frontend: React + TS + Vite (`src/`); Rust: `src-tauri/`; Worker: Python (`worker/`).
- Schema dùng chung: `schemas/*.json` (single source of truth); Pydantic models trong `worker/src/api/schemas.py` được generate từ đó.
- Binaries/model KHÔNG commit: `vendor/` gitignored.
- Build exe: KHÔNG còn trong scope — repo chạy local-development-only (`npm run tauri dev`); không đóng gói EXE/installer/updater.
- Ngôn ngữ doc mặc định: tiếng Việt.

## Environment (đã detect trên máy dev)

Node 24 / npm 11 / Rust 1.96 / Python 3.13.7 (worker chốt = Python 3.11). Git 2.52.0. Không dùng `"latest"` cho dependency critical.
