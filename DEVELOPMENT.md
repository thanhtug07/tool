# DEVELOPMENT.md — Local-development run book

Repo này là **local-development-only**. Không đóng gói EXE/installer/updater.
Tất cả runtime chạy từ source: frontend (Vite dev server), Rust core (Tauri
debug), worker (Python từ source cây).

## Yêu cầu môi trường (đã detect trên máy dev)

- Node 24 / npm 11
- Rust 1.96 (toolchain qua `rustup`)
- Python 3.11–3.13 (worker chốt Python 3.11; máy dev đang dùng 3.13)
- FFmpeg/FFprobe: đặt trong `vendor/ffmpeg/` (repo không commit, xem
  `.gitignore`). Nếu thiếu, worker/media probe báo lỗi rõ thay vì bịa số liệu.
- API keys: không nằm trong repo — cấu hình trong Settings → Providers và lưu
  vào OS credential vault (Windows Credential Manager).

## Cài đặt

```powershell
# Frontend
npm install

# Worker (từ thư mục worker/)
py -3 -m pip install -e ".[cuda]"   # bỏ [cuda] nếu máy không có GPU

# Rust: tauri dev tự build (cargo) — không cần bước riêng.
```

## Chạy app

```powershell
npm run tauri dev
```

- `tauri dev` chạy `beforeDevCommand` (`npm run dev` → Vite tại
  `http://localhost:1420`) rồi mở cửa sổ native Tauri 2.
- Worker được `WorkerManager` spawn từ source: `python -m src.main --port <ephemeral>`
  (Python từ `WORKER_PYTHON` env hoặc PATH — không có bundled `worker.exe`).
- Chỉ xem UI không cần Rust (mất IPC, hiển thị trạng thái empty):
  ```powershell
  npm run dev
  ```

## Kiểm tra chất lượng (bắt buộc trước khi báo "xong")

| Layer              | Command                                                                                    |
| ------------------ | ------------------------------------------------------------------------------------------ |
| Frontend typecheck | `npm run typecheck`                                                                        |
| Frontend lint      | `npm run lint`                                                                             |
| Frontend format    | `npm run format:check`                                                                     |
| Frontend test      | `npm run test`                                                                             |
| Rust               | `cargo check` / `cargo test` (CI thêm: `cargo fmt --check`, `cargo clippy -- -D warnings`) |
| Worker smoke       | `python -c "import src.main"` (từ `worker/`)                                               |
| Secret scan        | `gitleaks detect`                                                                          |

Worker health: vào Settings → Processing → Restart worker, hoặc để Rust test
`real_worker_starts_ready_and_shuts_down` (cargo test) chạy handshake thật.

## Chunked pipeline (chunked processing)

Automation có chế độ **chunked** (opt-in — toggle "Chunked processing (30s
parallel)" trong More Options):

- Video được chia chunk 30s (configurable 20/30/45/60s) với overlap 2s context.
- Chunk xử lý **song song có giới hạn** qua `ThreadPoolExecutor`
  (`automation.chunk_concurrency`, default 4, 1..8) — không process-per-chunk.
- Mỗi chunk chạy qua chính các service hiện có (STT → translation → TTS →
  subtitle); silent chunk hợp lệ.
- Per-chunk validation + retry riêng chunk đó (`automation.chunk_retries`,
  default 2); exhausted → FAILED_PERMANENTLY → cả run dừng.
- Ordered assembly: segments/cues sort theo index/time, dedupe overlap, TTS
  tracks concat theo thứ tự; timeline validation (tolerance 0.5s).
- Final validation (ffprobe: streams, duration, size) + output verification
  TRƯỚC khi báo thành công.
- Cleanup chỉ khi validation PASS AND verified; failed run giữ toàn bộ temp.
- Manifest `cache/chunk_manifest_{job_id}.json` sống sót sau cleanup.

Chi tiết: `docs/CHUNKED_PIPELINE_AUDIT.md` (audit) +
`docs/CHUNKED_PIPELINE_REPORT.md` (report + benchmark).

## Phạm vi không hỗ trợ (deferred — không còn trong scope)

Các task liên quan packaging/release bị **DEFERRED** và KHÔNG được đưa lại mà
không có approval:

- **Update System / updater**: `tauri-plugin-updater` (Rust + JS) đã gỡ;
  `Settings → Updates` đã gỡ; key `updates.auto_check` đã gỡ khỏi whitelist
  settings. API `checkForUpdates`/`installUpdate` không tồn tại.
- **Installer / code signing**: không NSIS/MSI, không Authenticode, không
  `TAURI_SIGNING_PRIVATE_KEY`, không `WINDOWS_CERTIFICATE`.
- **Release artifact / CI release**: `.github/workflows/release.yml` đã xóa.
- **Worker bundle (PyInstaller)**: `worker/packaging/build_worker.py` +
  `worker.spec` đã xóa; `worker-dist/` không còn được sinh ra. Worker luôn chạy
  từ source — `WorkerManagerConfig.worker_bin` chỉ là override tùy chọn.
- Tài liệu cũ `docs/FINAL_FUNCTIONAL_AUDIT.md` ghi nhận lịch sử các release
  gate — giữ nguyên như bản ghi, không phản ánh scope hiện tại.

## Cấu trúc liên quan

- `schemas/*.json` — single source of truth; Pydantic models trong
  `worker/src/api/schemas.py` generate từ đó.
- `src-tauri/tauri.conf.json` — `bundle.active: false`; không còn
  `plugins.updater`.
- `scripts/tauri.cjs` — dev launcher cho `npm run tauri`.
