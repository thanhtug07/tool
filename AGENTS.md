# AGENTS.md — Hướng dẫn cho AI coding agent

> Phiên bản rút gọn của **TASKS.md §8 AI CODING AGENT EXECUTION POLICY** (bắt buộc). Mọi AI coding agent làm việc trên repo này phải tuân.

## Nguồn sự thật

- **Kiến trúc:** `MASTER_PLAN.md` (FROZEN — không tự sửa) + `ARCHITECTURE_DECISION.md` (ADR, thêm dep mới phải có ADR).
- **Việc cần làm:** `TASKS.md` — làm theo dependency graph, ONE TASK = ONE GATE.
- **Lộ trình:** `IMPLEMENTATION_ROADMAP.md`.

## Quy tắc cứng (vi phạm → dừng task)

1. **ONE TASK = ONE GATE:** Làm xong 1 task → chạy đúng test/acceptance của task đó. Gate FAIL → STOP, không chuyển task tiếp theo, báo blocker kèm log/error.
2. **KHÔNG tự ý đổi architecture/scope/provider/model** khi chưa có approval. Thay đổi vượt phạm vi task → dừng, trình proposal, chờ chấp thuận.
3. **KHÔNG mở rộng MVP scope** (không dubbing/separation/OCR removal/voice cloning/timeline/billing/cloud backend).
4. **Chất lượng bắt buộc** trước khi báo "xong":
   - Test chạy được + pass (gồm cả test mới cho phần vừa viết).
   - Không regression bảo mật (secret/log/credential).
   - Không hard-code: model name, API key, kết luận license, tham số subtitle.
5. **Dữ liệu chưa có** (model, transcript, API key, dataset) → dùng fixture/mock theo quy ước. Không bịa số liệu.
6. **Kết thúc task** → cập nhật checklist/trạng thái trong TASKS.md nếu task yêu cầu, rồi báo: TASK, gate result, test đã chạy, files changed.

## Bảo mật

- **KHÔNG BAO GIỜ commit:** `.env`, `*.key`, `*.pfx`, `*.pem`, API key, credential. Đã chặn trong `.gitignore`.
- Không log secret/API key/path nhạy cảm.
- Trước khi commit, chạy `git status` và rà lại file sẽ add.

## Test / format cần chạy

| Layer | Command |
|---|---|
| Frontend typecheck | `npm run typecheck` (tsc --noEmit) |
| Frontend lint | `npm run lint` (eslint) |
| Frontend format | `npm run format` (prettier) |
| Frontend test | `npm run test` (vitest) |
| Rust | `cargo check` / `cargo test` (CI thêm: `cargo fmt --check`, `cargo clippy -- -D warnings`) |
| Worker | `python -m pytest worker/tests` (subset nhanh, không chạy `ai` marker) |
| Secret scan | `gitleaks detect` (CI từ TASK-002) |

Thứ tự trước khi báo xong: **typecheck + lint + format + test layer liên quan** → tất cả pass.

## Cấu trúc & conventions

- Thư mục theo `MASTER_PLAN.md §22` — không tạo thư mục mới ngoài quy hoạch nếu chưa được chấp thuận.
- Schema dùng chung: `schemas/*.json` (single source of truth).
- Binaries/model KHÔNG commit: `vendor/` gitignored.
- Frontend: React + TS + Vite (`src/`); Rust: `src-tauri/`; Worker: Python (`worker/`).
- Ngôn ngữ doc mặc định: tiếng Việt (giữ nhất quán với docs hiện có).

## Environment (đã detect trên máy dev)

Node 24.16.0 / npm 11.13.0 / Rust 1.96.0 / Python 3.13.7 (architecture chốt worker = Python 3.11 — xem ADR). Git 2.52.0. Không dùng `"latest"` cho dependency critical.

## Autonomous runner (scripts/autonomous_runner.py)

Repo được điều khiển bởi autonomous orchestrator — chạy từ **current task → TASK-030** mà không cần copy prompt thủ công:

```powershell
python scripts/autonomous_runner.py --dry-run   # preview (không thay đổi gì)
python scripts/autonomous_runner.py --status    # trạng thái hiện tại
python scripts/autonomous_runner.py --resume    # tiếp tục từ state đã lưu
python scripts/autonomous_runner.py             # chạy vòng lặp
```

- **State:** `docs/AUTONOMOUS_PROGRESS.md` (machine + human readable) — nguồn continuity, không bao giờ fake PASS trước khi gate pass.
- **Handoff:** `docs/AUTONOMOUS_HANDOFF.md` — session mới resume từ đây (không cần conversation history).
- **Blockers:** `docs/AUTONOMOUS_BLOCKERS.md` — blocker thật (cần quyết định product/architecture) thì ghi vào đây và dừng.
- **Config:** `config/autonomous.json` — `agent_command`, retry, session policy, auto_commit, start_task. KHÔNG lưu secret trong config.
- **Agent prompt:** `scripts/autonomous_agent_prompt.md` — master instructions cho mỗi coding-agent session; runner tự nối task brief vào.
- **Thứ tự nguồn sự thật:** `TASKS.md` → `MASTER_PLAN.md` → `ARCHITECTURE_DECISION.md` → `IMPLEMENTATION_ROADMAP.md` → `AGENTS.md` → repo code/tests → state files.
- **Test infra:** `scripts/tests/` — test cho chính orchestrator (`python -m pytest scripts/tests -q`), không invoke coding agent thật.
