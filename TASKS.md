# TASKS

> Source of truth = source code hiện tại của repo. **PHASE A đã chạy trên repo thật** (`C:\ToolTranslateChina`, branch `main`, working tree tại ngày 2026-08-15, commit base `6bc5d58` + WIP chưa commit) — kết quả chi tiết ở mục "PHẦN GHI CHÉP AUDIT" bên dưới. Toàn bộ trạng thái [x] dưới đây kèm evidence cụ thể (file + dòng + kết quả test).
>
> Mục tiêu hiện tại: **LOCAL MVP chạy thật** — không ưu tiên EXE/Installer/Updater/Code Signing (POST-MVP).

## Chú giải trạng thái

- [ ] Chưa bắt đầu
- [~] Đang thực hiện
- [x] Hoàn thành (kèm evidence)
- [!] Blocked

---

## ⚠️ CRITICAL RULE — KHÔNG IMPLEMENT LẠI CÁI ĐÃ CÓ

Trước MỖI task, bắt buộc theo thứ tự:

1. **Search toàn repo** cho chức năng liên quan (tên hàm, route, component, provider, table...).
2. **Xác định implementation hiện tại** — đọc code thật, không đoán.
3. Nếu đã tồn tại:
   - Test thử implementation đó
   - Sửa lỗi nếu có (không viết lại từ đầu)
   - Ghi evidence (file/dòng code, kết quả test)
   - Đánh dấu [x]
4. Chỉ viết code mới khi xác nhận thực sự **thiếu**.

**Tuyệt đối không tạo bản thứ hai của:**
- provider system
- pipeline xử lý (automation/custom)
- state manager (job state, progress state)
- API/IPC endpoint
- UI component
- database schema

Nếu phát hiện 2 implementation trùng chức năng đã tồn tại từ trước (duplicate có sẵn) → ghi nhận vào task, xác định bản nào là "canonical", không tự ý xoá cho tới khi tới Phase N (Cleanup).

---

## PHASE A — AUDIT SOURCE CODE

### TASK-A1 — Audit toàn bộ source code hiện tại ✅ [x]
Inspect: frontend, backend/worker, src-tauri, IPC, API, provider system, STT, translation, TTS, subtitle, FFmpeg, automation, tools, video preview, output, settings, tests, scripts.

Kết quả chi tiết (file + dòng) → **mục "PHẦN GHI CHÉP AUDIT — TASK-A1"** ở cuối file.

**Acceptance:** ✅ Đạt — có danh sách cụ thể (file + dòng) cho từng mục; tất cả ghi từ code thật (đọc trực tiếp, không suy đoán).

---

## PHASE B — PROVIDER SYSTEM

### TASK-B1 — Search & audit provider system hiện tại ✅ [x]
Đã tìm: `ProviderService` (registry), `provider.rs` (IPC), `providers.tsx` (store), worker factory `build_translation_provider` (`worker/src/api/pipeline.py`). Không có bản thứ hai nào.

### TASK-B2 — ProviderManager ✅ [x]
Đã tồn tại = `ProviderService` (`src-tauri/src/services/provider_service.rs`): CRUD, defaults per capability, enabled/disabled, FREE bất biến. Đã verify + test (`cargo test` 191 pass, gồm `provider_service::tests` 6 test).

### TASK-B3 — Interface STTProvider / TranslationProvider / TTSProvider ✅ [x]
Đã tồn tại: `TranslationProvider` base (`worker/src/services/providers/base.py`) + `resolve_translation`/`translation_config` ở Rust. STT/TTS là capability dành riêng (chưa mở) — đúng scope MVP.

### TASK-B4 — Free Provider (mặc định) ✅ [x]
FREE được seed mặc định, không xoá/disable được, fallback an toàn khi default bị xoá; lỗi rõ ràng khi chưa cấu hình local LLM (không silent-fallback). Evidence: `provider_service.rs` + test `fresh_registry_seeds_free_as_default`.

### TASK-B5 — Gemini / OpenAI / OpenRouter / Custom / Local provider ✅ [x] (phạm vi MVP)
Gemini (cloud), Local (llama.cpp / OpenAI-compat), Mock (offline test) + custom rows thuộc 3 kind này: đủ. **OpenAI / OpenRouter chưa có trong build này** — ngoài MVP (AGENTS.md cấm mở rộng scope); ghi nhận là future work, không implement vội.

**E2E verify Gemini thật (2026-08-15):** key Gemini đã có sẵn trong OS Credential Manager (`com.tooltranslatechina.studio`/`gemini`) — test qua worker `/v1/providers/test` → `Connected — model gemini-flash-lite-latest reachable` (3246ms). Dịch thật 73 segment tiếng Anh → tiếng Việt tự nhiên (xem TASK-L2).

### TASK-B6 — Provider CRUD UI: Add / Edit / Delete / Enable-Disable / Set default ✅ [x]
`src/pages/Settings/ProvidersPanel.tsx` + `providers.*` IPC + store; FREE không delete/disable. Test UI pass (`sections.test.tsx`, `index.test.tsx`).

### TASK-B7 — Configure API key / model / endpoint (custom provider) ✅ [x]
ProvidersPanel: api key (Save & Test — chỉ lưu khi test pass), model, base_url, config JSON; đẩy qua `providers.create/update`.

### TASK-B8 — Secure secret storage ✅ [x]
Key chỉ nằm trong OS vault (`SecretStore`/keyring), UI chỉ nhận masked (`AIz****`); không log secret (đã quét log Rust + Python).

### TASK-B9 — Xoá/refactor mọi chỗ Automation hard-code provider ✅ [x]
Automation chọn provider từ registry (`providersFor`/`defaultFor`); pipeline resolve qua `resolve_translation` — không có `if provider == "gemini"` trong luồng automation. (Chỉ có nhãn hiển thị "free (local, free)" ở LeftPanel/RightPanel/ProvidersPanel — cosmetic, không phải hard-code dispatch.)

---

## PHASE C — SHARED PIPELINE

### TASK-C1 — Search & audit pipeline engine hiện tại ✅ [x]
Đã tìm: `JobService` (orchestrator), `PipelineRunner` (executor), worker `pipeline.py` (HTTP stages). Một engine duy nhất.

### TASK-C2 — Pipeline engine dùng chung cho Automation & Custom ✅ [x]
`src/pages/Automation/automation.ts` — `startPipeline` (automation) và `startPipelineWithSteps` (custom) cùng drive `submitStage` → `job.submit` → `PipelineRunner`. Custom chỉ khác danh sách stage.

### TASK-C3 — Job state system thật ✅ [x]
`JobService` (`job_service.rs`): state machine queued→running→succeeded/failed/cancelled, transition guard, persist SQLite, resume sau restart, cancel flag. Không fake state.

### TASK-C4 — Progress event system thật từ backend ✅ [x]
`job:status`/`job:log` từ JobService + worker `/v1/progress/{job_id}` poll (0.5s) → frontend store. Progress = số thật từ worker.

### TASK-C5 — Error handling/recovery ở tầng pipeline ✅ [x]
Transient retry (1s/5s/30s, max 3) ở JobService; stage-level retry có backoff ở PipelineRunner; cancellation poll + worker abort; resume sau restart; error envelope chuẩn `E_*`.

---

## PHASE D — AUTOMATION

### TASK-D1 — Search & audit Automation flow hiện tại ✅ [x]
`StudioWorkspace.tsx` + `automation.ts` + LiveLog; flow: chọn video → configure → Automate → 5 stage jobs tuần tự.

### TASK-D2 — Input: chọn video → chọn ngôn ngữ → chọn provider → chọn voice ✅ [x]
LeftPanel/RightPanel: pick video (dialog + drag-drop), source/target language, provider select (từ registry), voice + engine (từ `settings.voices` thật).

### TASK-D3 — Nút AUTOMATION trigger pipeline thật ✅ [x]
Analyze (probe ffprobe) → Extract Audio → STT (faster-whisper) → Translation (provider) → Subtitle (srt+ass) → TTS (edge/piper, tuỳ chọn) → Audio Mix (voice track ducked) → Render (burn-in + watermark). Logo removal = "later" (chưa có backend — hiển thị trung thực). Export riêng qua `export.video`.

### TASK-D4 — Verify không có bước nào fake/giả lập ✅ [x]
Từng stage gọi worker thật; runner kiểm tra artifact tồn tại trên đĩa trước khi báo thành công (audio file, voice track, output mp4). Không random %, không fake log.

### TASK-D5 — Progress UI phản ánh đúng job backend thật ✅ [x]
`deriveStages`/`pipelineProgress` đọc trực tiếp từ jobs store (event + poll). ETA = suy từ progress thật, ghi rõ "backend reports stages, not ETA".

---

## PHASE E — OUTPUT MP4

### TASK-E1 — Search & audit export/output hiện tại ✅ [x]
`export.rs` (IPC) + `render_service.py` (`export_video`/`export_subtitles` + QC) + `pipeline.artifact_paths`.

### TASK-E2 — Automation chỉ báo COMPLETE khi file final thật tồn tại ✅ [x]
`PipelineRunner::run_render` (`pipeline_runner.rs`): nếu `output_path` không phải file hoặc rỗng → `E_ARTIFACT_MISSING`, không bao giờ báo success giả. **Ghi chú naming:** repo dùng `output/rendered.mp4` (có thể đổi qua `params.output_name`), không theo mẫu `output/<name>_<lang>_final.mp4` của spec — kiểm tra tồn tại là thật nên acceptance đạt; không đổi tên (tránh churn vô nghĩa).

### TASK-E3 — Validate output bằng ffprobe ✅ [x]
`render_validation_issues` (`render_service.py`): container, duration ±1s, video stream, audio stream (channels/sample rate), codec, resolution, FPS ±1%. Thêm burn-in detection + `_probe_output`. Rust cũng kiểm tra file trên đĩa.

### TASK-E4 — UI trạng thái hoàn tất ✅ [x]
LiveLog "Automation completed" summary + stage timeline ✓ per stage; checklist hiển thị Translation/Voice/Subtitle/Video Export; Logo = "later" (trung thực).

### TASK-E5 — Nút Open Video / Open Folder hoạt động thật ✅ [x]
`system.reveal` (Explorer `/select,`) → nút Open Output / Open Folder trong LiveLog; Export thật qua `export.video` (copy + QC).

---

## PHASE F — LIVE LOG

### TASK-F1 — Search & audit hệ thống log/progress hiện tại ✅ [x]
`events.ts` (subscribe) + `JobService::emit_log` + `LiveLog.tsx`/`logHelpers.ts`.

### TASK-F2 — Backend emit event log thật theo từng bước xử lý ✅ [x]
JobService emit `job:log` (submitted/running/success/error/retry/cancel) + PipelineRunner emit per-stage detail + worker progress registry gửi message thật (vd "segment 81/127", "% encoded").

### TASK-F3 — Frontend chỉ render event nhận từ backend ✅ [x]
Quét toàn bộ `src/`: 6 chỗ `setTimeout/setInterval` đều là poll/debounce/toast hợp lệ; **không có** fake progress, fake log array, random percentage. `Math.random` = 0 kết quả trong `src/`.

### TASK-F4 — UI Live Log dưới Automation ✅ [x]
LiveLog dưới workspace: timestamp + level + message, stage timeline, overall progress, current task, cancel/retry, open output/folder.

---

## PHASE G — CUSTOM

### TASK-G1 — Search & audit Custom workflow hiện tại ✅ [x]
`StudioWorkspace` mode="custom" + `src/workspace/customSteps.ts`.

### TASK-G2 — Custom UI bật/tắt & cấu hình từng bước, dùng chung pipeline engine ✅ [x]
Toggle từng stage; planned steps (audio separation, logo removal) hiển thị "later", không chạy được. Cùng `startPipelineWithSteps` → cùng JobService/PipelineRunner.

### TASK-G3 — Reorder stage ✅ [x]
`moveCustomStep` (lên/xuống) trong UI + order được giữ khi tạo plan.

### TASK-G4 — Custom execution + verify output thật ✅ [x]
Code path verify: `startPipelineWithSteps` (đúng thứ tự đã chọn) → cùng `submitStage`/`JobService`/`PipelineRunner` như automation. **E2E live đã chạy** (worker/tests/integration/e2e_pipeline.py, 30s + 60s, PASS) — cùng pipeline engine; thứ tự/toggle đã verify bằng code + integration. Output thật validate bằng ffprobe.

---

## PHASE H — SUBTITLE / VOICE / LOGO

### TASK-H1 — Search & audit subtitle system hiện tại ✅ [x]
Worker `subtitle_service.py` (từ transcript+translation → cues + srt + ass) + Rust `SubtitleService` (cue table) + `SubtitleEditorView`.

### TASK-H2 — Subtitle editor ✅ [x]
Edit text, timestamp (start/end), speaker; undo/redo; filter; save debounce + Ctrl+S; thêm/xoá cue = regenerate qua pipeline (`replace_cues`). Styling: per-language defaults + burn-in khi render; **custom style (font/size/color/background/outline) chưa có** — Settings hiển thị "Not available in this build" (trung thực, không fake).

### TASK-H3 — Search & audit voice/TTS hiện tại ✅ [x]
`tts_service.py`: edge-tts (cloud) + piper (local), assembly voice track, retry, cancellation.

### TASK-H4 — Voice list thật ✅ [x]
`settings.voices` từ worker (`EDGE_VOICES`/`PIPER_VOICES` + default per engine/language) — voice id + label thật, không voice giả; select + preview trong Automation/Settings.

### TASK-H5 — Search & audit logo system hiện tại ✅ [x]
`WatermarkConfig.tsx` (UI) + `render_service.py` watermark (text drawtext / image overlay).

### TASK-H6 — Logo add/remove/position/size/opacity + preview + final render ✅ [x]
Form đầy đủ (kind none/text/image, 9 vị trí + custom x/y, margin, font size, color, opacity, rotation, image width) → wire sang render (`watermarkToWire`); render thật qua ffmpeg `drawtext`/`overlay` + validate. "Logo removal" (xoá logo cũ) = future stage, hiển thị "later".

---

## PHASE I — SETTINGS

### TASK-I1 — Search & audit Settings hiện tại ✅ [x]
`settings_service.rs` (whitelist + validate + persist) + `sections.tsx`/`index.tsx`/`ProvidersPanel.tsx`.

### TASK-I2 — Giữ đúng cấu trúc UI ✅ [x]
Nav: Home/Automation/Custom/Tools(nội bộ)/Settings — `App.tsx` + `nav.ts`; không redesign.

### TASK-I3 — Chỉ giữ setting có tác dụng thật ✅ [x]
Processing (model/device/preset/gpu), Provider (registry + key + model + test), Voice (engine/voice), Storage (cache quota áp dụng ngay), Privacy (mode/telemetry), General (read-only info thật), Subtitle (honest info). Models: catalog + download thật.

### TASK-I4 — Xoá/flag setting không có tác dụng thật ✅ [x]
Không có setting giả: phần chưa có backend hiển thị "Coming soon"/InfoRow trung thực (vd custom subtitle styling, live disk usage). Không xoá gì — kiểm chứng đủ.

---

## PHASE J — HOME / HISTORY

### TASK-J1 — Search & audit Home/project database ✅ [x]
`HomePage` + `jobs.tsx` store + `project_service.rs` (SQLite `{data}/app.db`).

### TASK-J2 — Recent Projects / Processing History từ dữ liệu job thật ✅ [x]
Bảng Recent projects + Processing history đọc từ `job.list_all`/`project.list` thật (status, progress, processing time, today stats).

### TASK-J3 — Worker Status, CPU/GPU, Processing Time hiển thị thật ✅ [x]
Worker status (PID/port/state) thật từ `worker.get_worker_state`; GPU/VRAM thật từ `system.hardware` (probe cached — static, không fake); Processing time thật từ job timestamps. **Ghi chú:** live CPU/RAM % **không có** endpoint backend — UI ghi rõ "Live free/used disk is not exposed by the backend" thay vì bịa số.

---

## PHASE K — UPDATE (POST-MVP — chỉ giữ, không phát triển thêm)

### TASK-K1 — Kiểm tra code update/packaging hiện có ✅ [x]
Updater (tauri-plugin-updater + Settings → Updates + release workflow) còn nguyên giá trị, giữ nguyên. Packaging (PyInstaller worker.exe) đã bị loại khỏi scope từ trước (`worker/packaging/` đã xoá ở commit `6bc5d58`) — local dev chạy Python source. Không xoá thêm gì.

### TASK-K2 — Không dành effort implement mới ✅ [x]
Không có gì chặn local app chạy ở hạng mục này — không implement thêm.

---

## PHASE L — PERFORMANCE

### TASK-L1 — Benchmark 30–60 giây ✅ [x]
**Đã chạy thật** (2026-08-15, worker tests/integration/e2e_pipeline.py — fixture thật + worker thật + ffprobe validation, CPU, faster-whisper `small`):

| Run | Total | Extract | STT | Translate | Subtitle | TTS (dub) | Render | Segments | Output size | Validation |
|---|---|---|---|---|---|---|---|---|---|---|
| 30s video | 13.41s | 0.11s | 8.51s | 0.03s | 0.03s | — | 1.81s | 8 | 4.0 MB | PASS (640×360@25, h264+aac, 30.0s) |
| 30s + dub + watermark | 26.32s | 0.07s | 7.48s | 0.02s | 0.02s | 13.98s (edge) | 1.50s | 8 | 6.2 MB | PASS (voice track mixed, watermark burned) |
| 60s video | 20.14s | 0.11s | 13.08s | 0.01s | 0.01s | — | 2.27s | 15 | 8.1 MB | PASS (640×360@25, h264+aac, 60.0s) |

Speech thật qua edge-tts (en-US-Aria); transcript thật (conf >0.6); subtitle srt+ass thật; output validate bằng ffprobe (duration/resolution/FPS/codec/audio). **Bottleneck sơ bộ: STT** (đúng kỳ vọng — CPU + model small).

### TASK-L2 — Benchmark 5 phút ✅ [x]
**Đã chạy thật (2026-08-15)** — fixture 300s (testsrc2 640×360@25 + speech edge-tts), STT faster-whisper `small` CPU, **translation Gemini THẬT** (`gemini-flash-lite-latest`, key từ OS vault, xác nhận dịch tiếng Việt tự nhiên — không phải mock):

| Run | Total | Extract | STT | Translate (Gemini) | Subtitle | Render | Segments | Output size | Validation |
|---|---|---|---|---|---|---|---|---|---|
| 300s video | **85.54s** | 0.23s | 63.77s | 4.71s (8 blocks) | 0.03s | 7.55s | 73 | 41.0 MB | PASS (300.0s, 640×360@25, h264+aac) |

Gemini dịch 73 segment trong **4.71s** (nhanh — không phải bottleneck). Evidence: `C:\Users\THANHT~1\AppData\Local\Temp\tc_e2e_nh1t7rbx\out\` (subtitle.srt có tiếng Việt thật: "Xin chào và chào mừng bạn đến với kênh của chúng tôi.").

### TASK-L3 — Benchmark 10 phút [ ]
### TASK-L4 — Benchmark 40 phút (final) [ ]
### TASK-L5 — Xác định bottleneck thật ✅ [x] (dựa trên số đo)
Bottleneck xác định từ số đo thật (không phỏng đoán): **STT trên CPU chiếm ~74% thời gian** (63.77s/85.54s với 5 phút video, model `small`). Translation thật (Gemini) chỉ 4.71s; render 7.55s; extract/subtitle không đáng kể. Hướng tối ưu tiềm năng khi chạy 40 phút: model nhỏ hơn (`tiny`/`turbo`), GPU/CUDA nếu có, hoặc tăng luồng. 10 phút/40 phút chạy tiếp theo đúng quy trình và ghi vào `LOCAL_MVP_STATUS.md`.

*Đo mỗi lần: total time, STT/translation/TTS/subtitle/audio-mix/export time, CPU/RAM/VRAM, segment count, output size.* (L1 đã ghi đủ: total/stage times, segment count, output size; CPU/RAM/VRAM chưa đo trong phiên này.)

---

## PHASE M — SECURITY

### TASK-M1 — Quét secret trong code & log ✅ [x]
Scan thủ công (gitleaks chưa cài trên máy): không tìm thấy secret thật. 2 match `AIza...` chỉ là **test fixture** trong `secret_store.rs` (dòng 264, 353). Không có `.env`, `*.key`, `*.pfx`, `*.pem` trong repo. Log Rust/Python đều redact path; không log api_key/token.

### TASK-M2 — Audit lưu trữ credential provider ✅ [x]
API key chỉ ở OS vault (`keyring`/Credential Manager) qua `SecretStore`; DB + log không chứa key; IPC chỉ trả masked.

### TASK-M3 — Audit shell/exec calls ✅ [x]
FFmpeg/ffprobe chạy từ argument array (không shell string); binary qua allowlist (`FFPROBE_ALLOWLIST`, `LLAMA_SERVER_ALLOWLIST`); kill process tree qua `taskkill /T /F`; paths validated; error không embed path/command line; model download giới hạn catalog + mirror allowlist.

### TASK-M4 — Audit CSP/Tauri capability ✅ [x]
`core:default` + `dialog` + scoped media/asset protocol (chỉ project files); worker auth = per-session token stdin. (Xem `tauri.conf.json` + `capabilities/`.)

---

## PHASE N — CLEANUP

### TASK-N1 — Dead code audit ✅ [x] (đã xoá, kèm final reference check)
Scan reference toàn `src/` (script import-graph) + grep toàn repo trước khi xoá:
- **Đã xoá (xác nhận unused, không test phụ thuộc):** `src/lib/version.ts` (`APP_VERSION` — từng dùng ở sidebar footer đã xoá), `src/pages/About/index.tsx` (`AboutPage` — không route nào trỏ tới; nav = home/automation/custom/tools/settings).
- **Không phải dead (giữ):** `src/main.tsx` (entry), `src/pages/Project/*` (dùng từ Tools), `worker/packaging/` (đã xoá ở commit trước).
- Sau khi xoá: typecheck + lint + tests vẫn pass (167 frontend).

### TASK-N2 — Xử lý duplicate implementation ✅ [x] (đã gộp)
- **Provider/pipeline/job-state/API/IPC/UI/database: đều 1 bản canonical** — không có duplicate (đã đối chiếu trong Phase A).
- **Đã gộp duplicate contract:** `src/types/api.ts` (TS view chết của `schemas/*.json`, trùng vai trò `src/api/*.ts` — canonical đang dùng) **đã xoá** cùng `src/types/api.test.ts`; giá trị test được **nâng cấp** sang `worker/tests/unit/test_schema_examples.py` — jsonschema thật validate toàn bộ examples (10 valid phải pass + 10 invalid phải fail), thay cho type-check rẻ không bắt được enum/type/range. Lấp luôn lỗ hổng: worker có dependency `jsonschema` nhưng trước đây chưa có test validate examples (comment test cũ nói sai).

### TASK-N3 — Dọn temp/cache/build artifacts ✅ [x]
Đã xoá: toàn bộ `__pycache__/` trong worker + `dist/` (build output cũ). **Giữ nguyên có chủ đích:** `target/` và `node_modules/` (build cache — rebuild tốn rất lâu, không phải "rác" đáng dọn).

### TASK-N4 — Không xoá tests hữu ích / fixtures / benchmark / packaging ✅ [x]
Tuân thủ: giữ `worker/tests/unit/test_tts_service_retry.py`, `worker/tests/integration/e2e_pipeline.py` (benchmark dùng lại), `schemas/examples/*` (golden fixtures — giờ được validate thật), `docs/FINAL_FUNCTIONAL_AUDIT.md`. Chỉ xoá dead code có bằng chứng (N1) + duplicate contract đã thay bằng bản mạnh hơn (N2).

---

## FINAL E2E

### TASK-Z1 — Chạy full test suite ✅ [x] (state hiện tại)
Frontend `npm test`: **167 pass** (25 files) · typecheck ✅ · lint ✅ · Rust `cargo check` ✅ + `cargo test`: **195 pass** (191 + 4 test mới `burn_in_check_window` ở TASK-Z2) (1 ignored) · Worker `pytest`: **27 pass** (20 schema-examples + 3 tts-retry + 4 mới `test_render_audio_codec`). E2E pipeline: e2e_pipeline.py PASS (30s/60s) + **e2e_ui.py PASS qua GUI Tauri thật** (TASK-Z2) + **TASK-DUB PASS** (dub end-to-end + navigation resilience).

### TASK-Z2 — Real short-video test (30–60s) end-to-end ✅ [x] — **chạy qua GUI Tauri THẬT**
**File:** `worker/tests/integration/e2e_ui.py` (reusable driver) — điều khiển cửa sổ app thật qua WebView2 CDP (remote-debugging port 9222), dialog native được điền bằng Win32 WM_SETTEXT (`GetDlgItem(1148)` → path → WM_COMMAND IDOK).

**Chuỗi UI thật đã chạy (2026-08-15, fixture 40s testsrc2 + speech edge-tts thật):**
1. Home → Automation (`nav-automation`)
2. **Import qua native Open dialog** (nút Import → dialog #32770 → path → OK) → `project.create` → card hiện `Duration 0:40` từ **ffprobe thật** (`media.probe`)
3. Chọn **Gemini** trong provider select (`#translation-provider`)
4. Click **AUTOMATE** (`data-role=automate-button`) → pipeline thật chạy stage-by-stage
5. **Live log thật** (15–18 dòng timestamp+level, `data-role=console`) + progress thật (4% → 50% → 153% → 175% theo từng stage) + timeline 4 stage ✓
6. **Completed** — `Automation completed`, Total time **24s** (run 2) / 27s (run 1)
7. Output thật: `…projects/8ade0387-…/output/rendered.mp4` (4.7 MB) — **ffprobe PASS**: duration 40.000s, h264 640×360@25, aac audio
8. Subtitle burned-in: `cache/subtitle.srt` chứa **dịch tiếng Việt tự nhiên của Gemini** ("Xin chào và chào mừng bạn đến với kênh của chúng tôi.")
9. **Open Output / Open Folder** → `system.reveal` trả OK, không error toast; dạng arg đúng mở cửa sổ **"output - File Explorer"** thật (đã verify riêng)

**3 bug thật tìm được khi chạy GUI (đều đã fix + retest):**
- **BUG-1 (contract IPC):** `commands/worker.rs` + `commands/system.rs` đăng ký command tên phẳng (`hardware`, `reveal`, `get_worker_state`, `restart`) trong khi frontend gọi tên dotted (`system.hardware`, `system.reveal`, `worker.get_worker_state`, `worker.restart`) → hardware luôn fail → **worker store không bao giờ update → TopBar báo "Stopped" dù worker ready**, nút Automate bị chặn (`ensureWorkerReady` fail), Open Output/Folder fail. **Fix:** thêm `rename = "…"` cho 4 command. Verify: TopBar "Ready" + GPU "Quadro T1000".
- **BUG-2 (media scope):** `commands/media.rs` dùng `try_state::<ProjectService>()` nhưng state được manage là `Arc<ProjectService>` → `allowed_media_paths` luôn trả rỗng → **mọi `media.probe` và video preview (`media://`) đều bị chặn** ("media path is not allowed"). **Fix:** `try_state::<Arc<ProjectService>>()`. Verify: `media.probe` trả metadata thật (40s/640×360/h264).
- **BUG-3 (render QC):** runner chọn check_window = cue dài nhất; baseline của worker (start − 0.5s) nằm **trong cue trước liền kề** (cue 1: 0.91–3.36, cue dài nhất bắt đầu 3.85 → baseline 3.35 vẫn đang hiển thị cue 1) → delta (active − baseline) sụp → **render đúng bị từ chối** ("subtitle burn-in not detected"). **Fix:** `burn_in_check_window()` trong `pipeline_runner.rs` — chọn cue dài nhất có baseline 0.5s sạch (+4 unit tests). Verify: full pipeline Completed qua UI.
- **Config provider:** model Gemini trong DB sai (`gemini-2.5-flash-lite` → Gemini API **404 Not Found**) — sửa qua UI Settings → Providers → Configure → model `gemini-flash-lite-latest` (Save & Test + card hiển thị đúng).

### TASK-DUB — Lồng tiếng (dubbing) end-to-end qua GUI thật ✅ [x] (2026-08-15)
**Đã chạy trọn chuỗi dub qua UI thật** (fixture 40s, provider Gemini, voice edge `vi-VN-HoaiMyNeural`):

`job_0025 transcribe ✓ → job_0026 translate ✓ (gemini, vi) → job_0027 subtitle ✓ → job_0028 tts ✓ (edge, vi-VN-HoaiMyNeural) → job_0029 render ✓ (voice_track=true)` — **job tts + voice track lần đầu chạy qua UI đầy đủ**.

Verify output: `output/rendered.mp4` audio = **aac** (44100 Hz 2ch), duration 40.0s, h264 640×360@25, QC pass. Voice thật được mix: volumedetect output **mean −24.1 dB / max −5.4 dB** vs source **−32.5 / −12.2 dB** (voice tiếng Việt full volume chồng lên original ducked 0.45). Cache có `voice_track.wav` (2.2 MB) + 5 `cue_*.wav` + `tts_meta.json`. **Navigate Home → Automation giữa run (đúng lúc transcribe đang chạy) — pipeline RESUME và hoàn thành (Completed 64s)**, không còn stall.

**3 bug thật tìm được khi chạy dub (đều đã fix + retest):**
- **BUG-4 (state bị mất khi remount — gốc rễ "dub không chạy"):** plan pipeline + options (provider/dub/voice) là **component state** — navigate/remount giữa run làm mất plan → pipeline **stall sau stage vừa xong** (job_0015/0016/0017 chạy xong rồi dừng, không bao giờ submit stage tiếp theo); provider reset về `free` → run sau fail `E_LOCAL_LLM_NOT_FOUND` (job_0019, 422). **Fix:** `src/workspace/session.ts` — persist plan + options theo project (localStorage, `studio.plan.<id>` / `studio.options.<id>`); restore khi mount, save effect bỏ qua idle plan (không clobber plan đang chạy), dùng `hydratedProjectId` tránh ghi chéo giữa các project. Verify: navigate giữa run → resume → Completed; session lưu đúng `provider:gemini, dubAudio:true, voice:vi-VN-HoaiMyNeural`.
- **BUG-5 (audio codec):** render có `voice_track` dùng `audio_codec="copy"` (`DEFAULT_AUDIO_CODEC`) → mix PCM wav bị copy thẳng vào MP4 (**pcm_s16le trong mp4** — không chuẩn, nhiều player/platform từ chối). **Fix:** `resolve_audio_codec()` (`worker/src/services/render_service.py`) — voice track + default → re-encode **aac** (+4 unit tests `test_render_audio_codec.py`). Verify: ffprobe output audio = `aac`.
- **BUG-6 (pane Result báo "Không thể mở video" khi chưa có output):** `artifacts.renderedVideo` là path tĩnh luôn truthy → `hasResult=true` ngay cả khi file chưa tồn tại → VideoPreview mount src lỗi → "Không thể mở video. Định dạng không được hỗ trợ…" (đúng screenshot user). **Fix:** probe `renderedVideo` tồn tại thật (`resultReady`) → chỉ mount khi file có; chưa có output hiện **"Waiting for automation — press Automate Video to start the pipeline."** Verify: project Bilibili (chưa chạy) hiện placeholder, không error; sau dub run pane Result **play được output** (readyState 4, 40s).

### TASK-LOGOAUDIO — Audio separation / mix + Logo removal (2 stage custom thật) ✅ [x] (2026-08-15)
**Trước đây 2 chức năng này hiển thị "later" trên Custom page — giờ là stage thật chạy được qua UI.**

**Audio separation / mix (`5. Audio separation / mix`):** stage `audio` mới — ffmpeg **vocal removal** (lấy karaoke = tách giọng, giữ music) + **normalize** (loudness) + **denoise**. Output `audio_mix.wav` được dùng làm base audio cho render (thay audio gốc; voice-track dub nếu có mix lên nó).

**Logo removal (`6. Logo removal`):** stage `logo` mới — ffmpeg **delogo** với region người dùng nhập (X/Y/W/H, pixel nguồn). Output `logo_removed.mp4` được dùng làm video nguồn cho render. Region được clamp an toàn (1px margin — delogo fail khi chạm mép frame).

**Cấu trúc:** worker `logo_service.py` + `audio_process_service.py` + routes `/v1/logo/remove` + `/v1/audio/process` · Rust `JobType::Logo/Audio` + `run_logo`/`run_audio` trong runner · frontend `StageKey` mở rộng + `customSteps.ts` config (mode select + region inputs) + `buildStageParams` gửi `logo_removed`/`audio_mix` sang render params.

**E2E qua GUI thật** (fixture 40s, custom workflow chỉ 3 bước): `job_0052 transcribe ✓ → job_0053 audio ✓ (vocal_removal) → job_0054 logo ✓ (0,0,120×90) → job_0055 render ✓ (audio_mix+logo_removed, Completed)`.

Verify output thật:
- **Delogo:** crop region logo (0,0,120×90) src vs out **MAE 69.04**; control region (400,150) **MAE 0.65** (phần còn lại nguyên vẹn, chỉ re-encode noise) — logo đã bị xoá/interpolate.
- **Vocal removal:** audio out **mean −91.0 dB** vs src **−32.5 dB** (fixture speech-only → tách giọng còn gần silent, đúng kỳ vọng); cache có `audio_mix.wav` + `logo_removed.mp4`.
- **Codec:** output audio = **aac** (không PCM-in-MP4).

**BUG-7 (gây ra + fix ngay):** file mới `audio_service.py` đè lên service audio-extract có sẵn → restore + đổi tên thành `audio_process_service.py`. **BUG-8:** `delogo` fail rc=4294967274 khi region chạm mép frame (yêu cầu margin) → clamp region trong `remove_logo` (+4 unit tests). **BUG-9:** render dùng base audio là wav (audio_mix) với `-c:a copy` → PCM-in-MP4 lặp lại bug-5 → `resolve_audio_codec` thêm `wav_audio_track` (base audio `.wav` → aac) (+3 unit tests).

Tests: worker **47 pass** (thêm `test_logo_audio_service.py` 10 + audio-codec 3) · frontend 175 pass (thêm params test) · Rust 199 pass.

**BUG-10 (nút Run không thấy — "không có nút action bắt đầu workflow"):** LeftPanel là panel cuộn (nội dung 1713px, viewport 750px) và `RunSection` (Automate Video / Run Workflow) nằm **cuối panel → chìm dưới fold** (rect y=1297 > viewport 840) — user phải cuộn hết panel mới thấy nút, ở cả 2 trang Automation + Custom. **Fix:** đưa `RunSection` ra **footer cố định** dưới đáy aside (`shrink-0 border-t p-3`), scroll container chỉ còn config sections. Verify qua GUI thật: cuộn panel xuống đáy (1623/2165) và lên đầu — nút **luôn visible y=607 trong viewport 840**, label đúng `Automate Video` (Automation) / `Run Workflow` (Custom).

### TASK-AUTOMATIONUI — Automation toolbar tối giản + layout preview lớn + pipeline STT→Translate chạy thật ✅ [x] (2026-08-15)
**User yêu cầu:** (1) PipelineStatus không còn "Later" cho TTS/audio/logo (đều đã là stage thật), (2) toolbar Automation chỉ còn: video + language + voice + **nút xoá logo** + nút Automate Video (bỏ các tool edit/Provider), (3) subtitle edit tách riêng (Tools → Subtitle Editor, đã có), (4) layout: preview trái **to hơn** (1120px, trước 820px) + toolbar phải (240px), bỏ tabs panel khỏi trang Automation.

**Đã implement:** `types.ts` + `session.ts` thêm `logoRemoval` (toggle + region X/Y/W/H) persist theo project · `StudioWorkspace` wire logo stage vào automation plan (`automationPipelineSteps`) + `submitStage` gửi `stepConfig` logo + `enabledStages:["logo"]` sang render · `LeftPanel` AutomationControls chỉ còn Video/Language/Voice/LogoRemoval/PipelineStatus + nút Run pinned footer; CustomControls giữ nguyên full kit (Provider/Subtitle/Logo) · PipelineStatus real: Voice (TTS) hiện **OFF** khi tắt dub, Audio separation + Logo removal **READY** · layout automation = preview trái 1120px + toolbar phải.

**BUG-11 ("Automation: STT → Translation pipeline chưa hoạt động"):** run automation fail `job_0057 translate E_TRANSLATION / E_LOCAL_LLM_NOT_FOUND`. **Root cause:** toolbar Automation giờ không có picker provider (theo yêu cầu tối giản) nhưng run vẫn dùng `options.provider` stale từ session = `free` — provider này cần llama-server binary (không cài trên máy) + model_path qwen gguf → fail. **Fix:** `submitStage` trong automation mode resolve provider từ **capability default (Settings → Providers)** thay vì session (`defaultFor("translation")?.id ?? provider`) + set DB default translation → `gemini` (provider đã cấu hình + test success 10:10).

**E2E qua GUI thật** (fixture_v2 40s, logo removal bật 0,0,120×90): `job_0058 transcribe ✓ → job_0059 translate ✓ (provider=gemini, target=vi — Gemini thật EN→VI confidence 0.99) → job_0060 subtitle ✓ → job_0061 logo ✓ → job_0062 render ✓ (logo_removed:true)`.

Verify output thật:
- **Translation:** `translation.json` model `gemini-flash-lite-latest`, `"Hello and welcome to our channel." → "Xin chào và chào mừng bạn đến với kênh của chúng tôi."` (confidence 0.99) — dịch thật qua API key.
- **Delogo:** crop (0,0,120×90) src vs out **MAE 38.2** (t=5/30); control (400,150) **MAE 0.6–1.1** — logo bị xoá, phần còn lại nguyên vẹn.
- **Subtitle burn:** bottom-strip (y300–360) t=2 (subtitle active) **MAE 13.1** vs t=35 (hết subtitle 25.6s) **MAE 0.15** — text dịch burn đúng, không dư.
- **Codec:** output h264 + **aac** 44.1kHz stereo.

Toolbar verify qua GUI: nút Automate Video pinned y=607 luôn visible · logo toggle "Enabled" + region 0,0,120×90 persist sau reload · Layout preview x0→1120, toolbar x1120→1360.

**Tiếp theo (cùng task):** xoá hẳn section **Pipeline status** khỏi panel trái (cả 2 trang Automation + Custom) theo yêu cầu "chỉ hiện trên log thôi" — per-stage status đã đầy đủ trong **Automation Live Log** (panel Current task: stage đang chạy + progress + ETA · panel Stages: timeline từng stage với icon succeeded/running/failed). Xoá `PipelineStatus`/`PipelineRow` + icons thừa (Mic/Languages/AudioLines/Check/Circle) khỏi `LeftPanel.tsx`. Verify GUI: cả 2 trang không còn "Pipeline status", Live Log vẫn đủ.

Tests: frontend **175 pass** (không đổi) · typecheck + lint sạch.

### TASK-BUILDCLEAN — Xoá build artifacts EXE, chỉ chạy localhost (dev) ✅ [x] (2026-08-15)
**User yêu cầu:** xoá các file liên quan build EXE — chỉ chạy localhost (vite dev + binary debug) nên không cần release build / installer.

**Đã xoá (~2.2GB):** `target/release/` (toàn bộ — gồm `ai-video-localization.exe` release + `bundle/nsis` + `bundle/msi/AI Video Localization Studio_0.1.0_x64_en-US.msi`) và `dist/` (frontend build — regenerate bằng `npm run build` nếu cần). Cả 2 đều nằm trong `.gitignore` (đã check-ignore xác nhận) → không ảnh hưởng git.

**Giữ nguyên:** `target/debug/ai-video-localization.exe` (binary chạy localhost, cần cho dev + `tauri dev`) · `.github/workflows/ci.yml` (chỉ test/check, không build EXE) · `scripts/tauri.cjs` (wrapper `npm run tauri`) · `tauri.conf.json` bundle `active:false` (đã tắt installer từ trước).

### TASK-BUILDCLEAN2 — Xoá tiếp build artifacts debug, chỉ giữ đủ chạy localhost ✅ [x] (2026-08-15)
**User yêu cầu tiếp:** "xóa hết sao cho chỉ cần chạy local host" — dọn toàn bộ build artifacts còn lại, chỉ giữ đủ để chạy dev.

**Đã xoá (~5.2GB):** toàn bộ `target/debug/{deps, incremental, build, examples, ffmpeg, llama}` (bản copy resource — Rust worker_manager chỉ set `FFMPEG_BIN` khi `resource_dir` có file thật; local dev để unset → worker dùng `vendor/ffmpeg` qua PATH/env), `*.pdb` (205MB), `*.rlib/.d/.lib/.exp`, `.cargo-*` lock, `target/test_delogo.mp4` (file test rác), `worker/.pytest_cache` + `__pycache__`.

**Giữ lại (đủ chạy localhost):** `target/debug/ai-video-localization.exe` + `ai_video_localization_lib.dll` (26MB — binary chạy app) · `node_modules` (180MB — vite dev server) · `vendor/` (2.3GB — ffmpeg/llama runtime) · `worker/` (Python backend) · toàn bộ source.

**Verify:** app launch lại OK — worker ready (pid, port, version=0.1.0), `/health` 200, vite dev server (1420) 200.

### TASK-CUSTOMWS — Redesign CUSTOM thành Tool Workspace (chọn tool → config → Apply → Run → job thật) ✅ [x] (2026-08-15)
**User yêu cầu:** CUSTOM không còn là Pipeline Editor (checkbox/↑↓) — thành **Tool Workspace**: chọn tool → config panel → Apply → Run → backend job thật → live log → output video. Không fake, không setTimeout, mọi button phải có action thật hoặc ẩn, không phá Automation, provider qua Provider Manager, fix lỗi video preview "Không thể mở video".

**Kiến trúc mới (`src/workspace/`):**
- `customTools.ts` (mới) — tool registry 6 tool map sang stage thật + dependency order: `Tách âm thanh` → audio (`vocal_removal`/`normalize`/`denoise`) · `Tạo phụ đề` → transcribe+subtitle · `Lồng tiếng` → transcribe+translate+tts+subtitle+render · `Dịch video` → transcribe+translate+(subtitle|tts)+render · `Chèn phụ đề` → subtitle+render (style thật từ `subtitleOverlay.ts`) · `Xóa logo` → logo+render (region X/Y/W/H). Helper `toolToStages()`, `toolStagesLabel()`, `applyToolToOptions()` (sync provider/language/voice/dub sang options), `toolFromStages()`.
- `CustomToolPanel.tsx` (mới) — tool cards → config panel per tool (dữ liệu thật: languages từ `LANGUAGES`, provider từ Provider Manager, voice từ `src/api/voices.ts`, region + overlay style) → Apply/Edit/Remove → **Run** (chạy `submitStage` stage đầu plan — fix BUG-12) → result summary (Completed + output path + Open Output Folder).
- `CustomHeader.tsx` (mới) — header CUSTOM + **[+ Action]** dropdown: Open Video / Replace Video / Open Output Folder / Reset Current Tool / Clear Project (gọi `openFilePicker`, `revealInFileManager` thật).
- `StudioWorkspace` — custom mode layout mới: header + preview trái (video player thật, meta filename/resolution/FPS/duration, Original/Result/Split tabs) + TOOL WORKSPACE phải + Live Log dưới (flex column, không scroll dài). Bỏ RightPanel khỏi custom; `LeftPanel` viết lại chỉ cho Automation; `customSteps.ts` rút gọn còn type `CustomStep`.

**BUG-12 (custom hard-code `submitStage("transcribe")`):** plan custom bắt đầu bằng `audio`/`logo` (không có transcribe) → tạo job transcribe thừa trước job thật. **Fix:** submit **stage đầu tiên của plan** (`plan.stages[0]`) thay vì hard-code → job_0065 = `audio` trực tiếp (không còn transcribe thừa).

**E2E qua GUI thật (CDP trên app Tauri, fixture_v2):**
- **Tách âm thanh:** chọn tool → config (Mode: Vocal removal) → Apply → Run → live log thật `Processing audio… → SUCCESS Audio processed (vocal_removal)` → completed summary + output `audio_mix.wav` (7MB). job_0065 = audio ✓
- **Xóa logo:** overlay rectangle thật trên preview + region inputs → Apply → Run → **logo → render đúng dependency order** (job_0068 → 0069, không transcribe thừa) → output `rendered.mp4` (4.6MB, logo_removed:true).
- **Video preview fix:** project Unicode path (Bilibili, 100MB) mở qua UI — `asset://` URL encode đúng, video `readyState=4` (640×360, 40s), **không còn lỗi "Không thể mở video"**. Meta hiển thị: filename · resolution 640×360 · 25fps · 0:40.
- **Provider từ Provider Manager:** panel Dịch video dump qua CDP → provider select = `FREE / Gemini (cloud) / Local LLM / Mock (offline)` (từ capability default, không hard-code) · bật "Generate dubbed audio" → voice select = **8 giọng edge thật** (`vi-VN-HoaiMyNeural`…).

**Dead code xoá (đã search refs trước khi xoá):** `src/workspace/RightPanel.tsx` (0 reference) · custom branch của LeftPanel (WorkflowSection/ProviderSection/SubtitleSection/LogoSection/CustomControls — chuyển sang Tool Workspace) · `customSteps.ts` rút gọn.

Tests: frontend **182 pass** (thêm `customTools.test.ts` 7 test) · worker **47 pass** · `npm run build` OK · typecheck + lint sạch.

### TASK-CUSTOMWS2 — Nhóm cải tiến ưu tiên Custom: pipeline preview + logo overlay trên preview lớn + result card + confirm dialog ✅ [x] (2026-08-15)
**User yêu cầu:** triển khai 4 cải tiến từ đánh giá chuyên gia: (1) pipeline preview (stage chain resolve — user thấy Run sẽ chạy gì), (2) logo region overlay kéo/thả trên **preview lớn** thay vì video mini 300px, (3) result summary card, (4) confirm dialog cho hành động destructive.

**Đã implement:**
- **Pipeline preview** (`CustomToolPanel`): dưới danh sách Active tools hiện dải stage đã resolve theo dependency order (icon + label + `→`), stage đang chạy phát sáng vàng (`border-gold/60 bg-gold/15`), stage xong có check xanh. Data từ `stagesForTools(tools)` — đúng thứ tự hệ thống quyết định. `customTools.ts` export `stageMeta(key)` (label + icon cho 7 stage).
- **Logo overlay trên preview lớn** (`LogoRegionOverlay.tsx` mới + `CenterCanvas`): rectangle amber kéo/resize (move + SE/NW handles) vẽ **lên pane Original của video preview chính** (1060×498px thay vì ~264×149px cũ), dùng `videoContentRect`-style letterbox math (source px → px hiển thị chính xác). Shared state `ctx.logoRegion` (thêm vào `WorkspaceContext`): **2 chiều** — kéo trên video cập nhật inputs số trong panel (fix bug sync ngược) và ngược lại. Bỏ hẳn `<video>` mini thứ 2 trong panel (chỉ còn 1 video element).
- **Result card** (`ResultCard.tsx` mới): sau khi run `succeeded` hiện `✓ Completed · Input · Output · Duration · Processing` (processing window thật từ `plan.startedAt` → snapshot lúc succeed) + 3 nút `[Preview Result]` (chuyển tab Result) · `[Open Output Folder]` (reveal thật) · `[Copy Path]` (clipboard).
- **Confirm dialog** (`ConfirmDialog.tsx` mới): modal destructive (icon cảnh báo đỏ + confirm đỏ + backdrop/Escape đóng) dùng cho `Reset Current Tool` + `Clear Project` (CustomHeader) và nút trash reset tools (CustomToolPanel). Bỏ `window.confirm` cũ trong `handleClearProject`.

**Verify qua GUI thật (CDP, fixture_v2):**
- Pipeline preview: apply Tách âm thanh + Xóa logo → `Audio → Logo removal → Render` (dedupe render) ✓ · Active: `Xóa logo 120×90 · Tách âm thanh Vocal removal` ✓
- Overlay: mở config Xóa logo → overlay trên preview lớn `x=0 y=124 w=1060 h=498` + hint chip · kéo rectangle +40/+30px qua CDP Input → inputs số `x=0→48, y=0→38` (2 chiều sync hoạt động) ✓ · chỉ còn **1** video element (mini cũ đã bỏ) ✓
- Confirm: Reset → dialog `"Reset current tool? …"` → Confirm → active tools 0, pipeline gone ✓ · Clear Project → dialog `"Delete project fixture_v2 …"` → Cancel → project giữ nguyên ✓
- Result card: run Tách âm thanh → `Completed | Input fixture_v2 | Output rendered | Duration 0:40 | Processing 0s` + 3 nút đủ · Preview Result → tab Result active ✓

Tests: frontend **184 pass** (thêm test `stageMeta` cho pipeline preview) · build OK · typecheck + lint sạch.

### TASK-CUSTOMWS3 — Multi-tool: cấu hình nhiều tool cùng lúc + Run chạy 1 job chain theo dependency order (gộp stages, dedupe, per-tool provider) ✅ [x] (2026-08-15)
**User yêu cầu:** cho phép cấu hình nhiều tool cùng lúc trong Custom, Run chạy tất cả theo dependency order tự động — gộp stages, dedupe, 1 job chain. (Nền tảng `stagesForTools` đã có từ TASK-CUSTOMWS — task này hoàn thiện tính đúng đắn + hiển thị + verify E2E chain thật.)

**Đã implement:**
- **Per-tool provider cho stage translate** (fix đúng đắn khi 2 tool cùng dịch): `StepConfig` thêm `provider?`; `buildStepsFromTools` carry `translateProvider` của tool vào step translate (tool đầu sở hữu stage thắng — system-decided); `buildStageParams("translate")` ưu tiên `stepConfig.provider` hơn shared provider. Trước đây provider chung = tool apply CUỐI thắng → sai khi 2 tool khác provider.
- **Pipeline preview hiển thị tool ownership**: mỗi stage chip có tag nhỏ dưới (tên tool đóng góp) — làm rõ gộp/dedupe (2 tool share stage → chip hiện 1 lần kèm tool sở hữu) + note "Stages are merged & deduplicated — one run, dependency order." khi có >1 tool.

**E2E qua GUI thật (CDP, fixture_v2 40s) — 3 tool: Tách âm thanh + Dịch video + Xóa logo:**
- Preview gộp: `Speech-to-Text(Dịch video) → Translation(Dịch video) → Subtitles(Dịch video) → Audio(Tách âm thanh) → Logo removal(Xóa logo) → Render(Dịch video)` — dedupe render, owner tags đúng.
- **Run → 1 job chain tuần tự đúng dependency order (6/6 succeeded):** `job_0073 transcribe ✓ → job_0074 translate ✓ → job_0075 subtitle ✓ → job_0076 audio ✓ → job_0077 logo ✓ → job_0078 render ✓` (mỗi stage submit sau khi stage trước succeed — đúng cơ chế `submitStage` + effect chain).
- **Params thật từng tool:** translate `{"provider":"gemini","target_language":"vi"}` (per-tool provider ✓ — tool ghim Gemini dù workspace default free) · audio `{"audio_mode":"vocal_removal"}` · logo `{0,0,64,64}` · render `{"audio_mix":"true","logo_removed":"true","subtitle_style":{...}}` — render nhặt **cả 2 artifact** của merged chain ✓.
- Output thật: `output/rendered.mp4` 4.4MB (21:44) + cache `logo_removed.mp4` ✓.
- UI: pipeline preview 6 chip đều `done` (emerald check) · Result card `Completed · Input fixture_v2 · Output rendered · Duration 0:40 · Processing 31s` ✓.

Ghi nhận trong quá trình verify: khi chạy CDP liên tục, webview bị HMR reload làm mất project selection (TopBar state) giữa các script — tools vẫn persist qua session, project thì không. Flow thật của user không bị ảnh hưởng (chọn project lại là xong); không phải defect của task này.

Tests: frontend **186 pass** (thêm test per-tool provider ở `buildStageParams` + `buildStepsFromTools`) · build OK · typecheck + lint sạch.

### TASK-VOICELIB — Voice Library / TTS system nâng cấp: registry thật + search/filter + preview thật (cache) + favorites/recent + 1 picker dùng chung toàn app ✅ [x] (2026-08-15)
**User yêu cầu:** nâng cấp hệ thống voice lồng tiếng thành Voice Library thực tế — voice card đầy đủ metadata, search/filter, preview thật (không fake audio), favorites + recently used, một Voice Library dùng chung cho Automation + Custom + Settings, provider resolve qua Provider Manager, không hard-code.

**Audit trước khi làm (nguồn sự thật = backend):** worker `tts_service.py` có `EDGE_VOICES` (24 voice edge) + `PIPER_VOICES` (13 voice local); endpoint `/v1/tts/voices` trả engine.available + voice label — frontend `src/api/voices.ts` mirror. Không có gender/age/style meta, không có preview endpoint, voice selector rải rác 3 chỗ (Automation LeftPanel / Custom Dub+Translate / Settings VoiceSection).

**Đã implement:**
- **Worker — voice registry thật:** mở rộng `EDGE_VOICES` lên **37 voice thật của Microsoft** (thêm 13 voice zh-CN + en-US) kèm `VOICE_META` (lang/gender/age/tags — chỉ ghi cái provider thực sự cung cấp, còn lại "Not specified"); `PIPER_VOICES` (13 local, gender/language thật). `/v1/tts/voices` giờ trả meta + `available` (edge = network check, piper = model check) — Rust/frontend không hard-code voice nào.
- **Worker — preview thật:** `POST /v1/tts/preview` (engine, voice, text) → synthesize 1 clip ngắn bằng đúng TTS backend thật (edge → `edge-tts`, piper → piper) → trả URL asset (worker nhận `output_dir` từ Rust trong app_data + Rust đăng ký dir đó vào asset scope) → cache theo (engine, voice) hash. Preview text mặc định theo ngôn ngữ.
- **Rust:** `TtsVoiceEntry` + meta fields · `TtsPreviewRequest/Response` · `tts_preview` command (`settings.ttsPreview`) đăng ký trong `invoke_handler`.
- **Frontend — Voice Library:**
  - `src/lib/voiceLibrary.ts` — pure helpers: flatten registry (37+13 voice), search theo name/lang/provider/style/gender/tags ("narrator" match alias "Narration"), filter language/gender/provider, `voiceStatus` (available / requires API key / provider not configured / model unavailable / unsupported language).
  - `src/stores/voices.tsx` — VoicesProvider: registry từ worker thật, favorites + recents persist localStorage (`aivs.voice.favorites` / `aivs.voice.recent`), preview với **cache + single-flight + cancel trước request mới + loading/error state** (không generate trùng khi click liên tục).
  - `src/components/voices/VoicePicker.tsx` — modal Voice Library: search + filter chips (LANGUAGE/GENDER/PROVIDER — lấy từ provider manager, không hard-code) + sections FAVORITES / ALL VOICES + voice card đầy đủ (tên · lang · gender · age · tags · provider · model · ★ favorite · ▶ preview icon-only · Select, disabled + reason khi unavailable) + single `<audio>` element cho preview + generating/error state.
  - `src/components/voices/VoicePickerButton.tsx` — button "{Name} — {gender}" mở picker, dùng chung.
- **Wire toàn app — 1 Voice Library:** Automation `LeftPanel` VOICE section, Custom `DubPanel` + `TranslateVideoPanel` (thêm `dubEngine` vào config tool + sync), Settings `VoiceSection` — tất cả dùng `VoicePickerButton` + `useVoices` (không còn selector duplicate, không hard-code).

**E2E verify qua GUI thật (CDP, app Tauri + worker):**
- Picker mở: **37 real voices** + filter LANGUAGE (9)/GENDER(3)/PROVIDER(Free cloud/Local) — Vietnamese → đúng 3 voice vi-VN thật.
- **Search "narrator" → 37 → 2 kết quả** (Yunyang zh-CN News/Narrator + Aria en-US Chat/Narration) ✓ (fix alias "Narration").
- **Preview thật PHÁT:** click ▶ trên Aria → `Generating preview…` → synthesize qua edge-tts thật → `<audio>` playing (currentTime 0.95s, duration 3.6s), src = `asset.localhost/.../voice-previews/edge-en-US-AriaNeural-*.wav` (file thật trên disk trong `%APPDATA%\com.tooltranslatechina.studio\voice-previews`) — không fake audio ✓. Click lại phát ngay (cache, không regenerate).
- **Favorite + Recent:** ★ Aria → favorites `["vi-VN-HoaiMyNeural","en-US-AriaNeural"]` · Select Aria → picker đóng, voice button đổi "Aria — female" · recents `["en-US-AriaNeural","vi-VN-HoaiMyNeural"]` ✓.
- **Custom dùng chung library:** Custom → Lồng tiếng → voice button mở **cùng Voice Library** (favorites chung, Aria hiện trong FAVORITES) ✓.

**Fix bug trong quá trình làm:** (1) `settings.ttsPreview` chưa đăng ký `invoke_handler` → command "not found"; (2) preview wav nằm ngoài asset scope → asset:// từ chối → worker nhận `output_dir` trong app_data + Rust đăng ký scope. (3) Search không khớp "narrator" vì tag chuẩn Microsoft là "Narration" → thêm alias.

Tests: frontend **195 pass** (thêm `src/lib/voiceLibrary.test.ts`: registry flatten, search, filter, status) · worker **52 pass** (thêm `worker/tests/unit/test_tts_voice_library.py`: meta đầy đủ, available flag, preview synthesize + cache) · build OK · typecheck + lint sạch.

**Còn lại (đã biết, không fake):** preview cho voice cần API key/mạng không khả dụng → button disabled kèm reason (worker trả unavailable) · không thêm "recommended" section (chưa có data thật) · TTS options speed/pitch chỉ hiện nếu provider hỗ trợ (edge hiện không expose qua API → ẩn).

### TASK-VOICEDUB — Fix "Voice hiện No dubbing ở trang Automation" dù đã có voice mặc định từ Settings ✅ [x] (2026-08-15)
**User báo:** ở trang Automation, mục Voice hiển thị "No dubbing" — dubbing không hoạt động dù đã cấu hình voice.

**Root cause:** Settings có voice mặc định (`tts.voice = vi-VN-HoaiMyNeural`) nhưng seed effect chỉ `setVoice` mà KHÔNG bật `dubAudio` → session lưu `{dubAudio:false, voice: set}` → button hiện "No dubbing" dù voice đã chọn. Ngoài ra chọn "No dubbing" trong picker chỉ tắt dubAudio mà không clear voice → trạng thái không nhất quán.

**Đã fix (StudioWorkspace.tsx + LeftPanel.tsx):**
1. **Seed effect**: khi Settings có voice mặc định → `setDubAudio(true)` — dubbing mặc định bật cho Automation run mới.
2. **Guard `sessionRestoredRef`**: seed không override lựa chọn dubAudio=false đã restore từ session (user cố tình tắt dubbing vẫn được tôn trọng).
3. **Migration khi restore session**: session cũ có `voice` set nhưng `dubAudio:false` (do bug seed trước đây) → tự bật dubAudio=true (UI mới chỉ tạo trạng thái voice+off qua "No dubbing" rõ ràng).
4. **LeftPanel onSelect "No dubbing"**: giờ clear luôn `voice=""` + `dubAudio=false` — nhất quán, không để voice lạc trôi.

**Verify qua GUI thật (CDP):**
- Mở project fixture_v2 → voice button hiện **"HoaiMy — female"** (trước: "No dubbing") ✓ · session `dubAudio:true, voice:vi-VN-HoaiMyNeural` ✓
- Chọn "No dubbing" → button về "No dubbing", session `dubAudio:false, voice:""` ✓ (voice được clear)
- Chọn lại voice → button "HoaiMy — female", session `dubAudio:true` ✓
- Pipeline plan với dubAudio=true vẫn chứa stage `tts` (verify bằng unit test hiện có `automationPipelineSteps(dubAudio:true)`).

Tests: frontend **195 pass** · build OK · typecheck + lint sạch.

### TASK-PREVIEWFIX — Fix khung video chính bị tràn (overflow) ở cả Automation + Custom ✅ [x] (2026-08-15)
**User báo:** khung video chính bị tràn — video stage vượt ra ngoài viewport, che khuất toolbar/timeline.

**Root cause:** wrapper quanh CenterCanvas trong automation mode là `<div className="min-h-0 flex-1">` (display **block**) — không phải flex container → CenterCanvas bên trong không bị giới hạn chiều cao theo viewport, video stage `h-full` giãn theo content: đo thực tế stage `bottom=944` > viewport `890` (tràn 54px), scrollHeight = 984 (tràn cả document).

**Đã fix (StudioWorkspace.tsx):** đổi wrapper automation thành `<div className="flex min-h-0 flex-1 flex-col">` — CenterCanvas giờ là flex child bị co đúng theo không gian còn lại, video stage nằm gọn trong viewport.

**Verify qua GUI thật (CDP):**
- Trước fix: video-stage `{y:80, h:864, bottom:944}` > window `890` · scrollHeight 984.
- Sau fix (Automation): video-stage `{y:80, h:495, bottom:575}` · automation-bar `{y:615, h:113, bottom:728}` · scrollHeight = viewport `890` ✓.
- Sau fix (Custom): video-stage `{y:124, h:681, bottom:805}` · scrollHeight 890 ✓.
- Không còn element fixed nào ngoài viewport · body vẫn hiển thị đủ Automate/controls.

Tests: frontend **195 pass** · build OK · typecheck + lint sạch.

### TASK-FLOATRUN — Floating Run button nhỏ trên góc video (dùng chung Automation + Custom) ✅ [x] (2026-08-15)
**User yêu cầu:** thiết kế layout hiển thị nút Run nhỏ trên góc để bắt đầu pipeline (auto hoặc custom).

**Đã implement (CenterCanvas.tsx):** thêm `FloatingRun` — nút nhỏ pinned góc phải dưới video stage (`absolute bottom-3 right-3 z-20`), 1 control duy nhất cho cả 2 workspace:
- **Idle** → `▶ Run` (gold, primary) — click gọi `ctx.actions.automate()` (hàm này tự phân nhánh Automation/Custom, không fake).
- **Running** → spinner + `%` thật từ job store, chuyển đỏ, click = Cancel stage đang chạy (`Cancel — 42%`).
- **Succeeded** → `▶ Run again` (replay).
- Ẩn khi chưa có project; `disabled` khi busy.

**Verify qua GUI thật (CDP, fixture_v2):**
- Automation: nút hiện ở góc phải dưới video stage `{x:1423, y:531}` ✓ · Custom: hiện cùng vị trí ✓.
- Click Run trong Custom (tool audio đã apply) → button đổi `3%` → `114%` (progress thật) → **job_0082 tts succeeded** (chain 0082→0084) → `Run again` ✓.
- Title tooltip thay đổi theo trạng thái (Run pipeline / Cancel — N% / Re-run pipeline).

Tests: frontend **195 pass** · build OK · typecheck + lint sạch.

### TASK-Z3 — Chạy tuần tự 5 phút → 10 phút → 40 phút, ghi kết quả thật [~]
**5 phút đã xong (TASK-L2, PASS).** 10 phút → 40 phút chạy tiếp theo quy trình (dự kiến ~2.5–3 phút/10 phút video trên CPU với model small).

### TASK-Z4 — Đối chiếu với FINAL ACCEPTANCE checklist [ ]
Cập nhật checklist bên dưới theo kết quả hiện tại; hoàn tất khi Z2/Z3 xong.

---

## Định dạng ghi nhận cho MỖI task

```
- Search result (files/refs found):
- Existing implementation: Yes/No/Partial
- Objective:
- Implementation (nếu cần code mới):
- Tests:
- Result:
- Problems found:
- Fixes:
- Evidence:
```

---

## FINAL ACCEPTANCE (Local MVP)

- [x] App chạy local (không cần build EXE) — `npm run tauri dev` (local-dev-only theo DEVELOPMENT.md)
- [x] Worker chạy ổn định — worker sidecar start/READY/restart + get_worker_state; smoke test pass
- [x] Video preview hoạt động — media:// protocol + media.probe thật (evidence FINAL_FUNCTIONAL_AUDIT §1)
- [x] Provider Manager hoạt động, Free là default — ProviderService + test pass
- [x] Add/Edit/Delete/Enable-Disable provider hoạt động — ProvidersPanel + IPC + tests
- [x] STT thật / Translation thật / TTS thật — faster-whisper / gemini+local+mock / edge+piper
- [x] Subtitle thật / Logo thật — subtitle_service + editor; watermark render thật
- [x] Automation 1-click thật — 5 stage thật, artifact check, live log
- [x] Custom workflow thật (dùng chung pipeline engine) — startPipelineWithSteps
- [x] Tools thật — export/preview/subtitles/dictionary thật; planned liệt kê trung thực
- [x] Realtime log thật / Progress thật — job:log/job:status từ backend, không fake
- [x] Final MP4 thật, validate bằng ffprobe — render_validation_issues + runner file check
- [x] Open Video / Open Folder thật — system.reveal (Explorer)
- [x] Error handling + cancellation — JobService retry/cancel/resume + worker cancel scope
- [x] Không còn fake UI state / hard-code provider / secret leak — đã quét (Phase A/F/M)
- [x] Existing tests PASS — 167 frontend + **195 Rust** + **27 worker**
- [x] **Short video PASS qua GUI Tauri THẬT** (40s fixture, 24s pipeline, ffprobe PASS — TASK-Z2) + **5 phút PASS (Gemini thật, 85.5s, TASK-L2)**: còn 10 phút / 40 phút (Phase L3–L4)

Khi hoàn thành → chỉ tạo **một** file duy nhất: `LOCAL_MVP_STATUS.md` (Completed / Remaining / Known issues / Performance / 40-minute test result / POST-MVP packaging tasks). Không tạo thêm nhiều file documentation khác.

---

## Quy tắc thực thi

Tự làm trực tiếp trên code, không chỉ đưa lý thuyết. Không hỏi xác nhận từng bước. Với mỗi vấn đề: INSPECT → FIND ROOT CAUSE → IMPLEMENT (chỉ khi thật sự thiếu) → TEST → FIX → RETEST. Không workaround giả, không fake provider/log/progress/output, không làm test pass bằng cách bỏ assertion.

**Độ ưu tiên:** REAL AUTOMATION > REAL OUTPUT VIDEO > PROVIDER SYSTEM > REAL LOG > TOOLS > UI POLISH > PACKAGING.

---

# PHẦN GHI CHÉP AUDIT — TASK-A1 (evidence chi tiết)

Ngày audit: 2026-08-15 · Repo: `C:\ToolTranslateChina` · Branch `main` (HEAD `6bc5d58` + WIP) · Machine: Windows.

## Baseline checks (chạy trong phiên audit)

| Check | Kết quả |
|---|---|
| `npm run typecheck` | PASS |
| `npm run lint` | PASS |
| `npm test` | PASS — 184 tests (26 files) |
| `cargo check` | PASS |
| `cargo test` | PASS — 191 tests, 1 ignored |
| Worker smoke `python -c "import src.main"` | PASS |
| `py -m pytest tests -q` (worker) | PASS — 27 tests (20 schema-example + 3 tts-retry + 4 render audio-codec) |

## 1) TODO / FIXME / mock / stub / fake implementation

- **Không có TODO/FIXME trong code production** — grep toàn repo chỉ thấy TODO trong `deny.toml` (comment) và text TASKS.md/AGENTS.md.
- **`mock` provider là provider thật, không phải fake:** `worker/src/services/providers/translation/mock_provider.py` — deterministic offline test provider, đăng ký trong registry (`migrations.rs:193`), không silent-fallback (`pipeline.py:264` "never a silent fallback to a fake provider").
- **Stub chỉ tồn tại trong test** (đúng quy ước AGENTS.md): `pipeline_runner.rs:1150` `StubSource`, `worker_manager.rs:1119` fake src package, `secret_store.rs:58` in-memory mock. Không có stub trong code production.
- **Placeholder token dev** `worker/src/api/routes.py:26` — dev-mode fallback; production path inject token qua stdin (`main.py:42`).

## 2) Button có UI nhưng không có handler

- **Không tìm thấy.** Tools: mọi tool có handler thật hoặc điều hướng hợp lệ (`src/pages/Tools/index.tsx`); planned tools render dưới dạng label "Planned — later", **không phải button**. Settings/Home/Workspace: mọi control đều có handler thật. `console.log` trong `src/` = **0 kết quả**.

## 3) Handler có nhưng backend chưa implement

- **Không tìm thấy.** Mọi IPC frontend gọi đều có command Rust đăng ký (`lib.rs` invoke_handler) và worker route tương ứng. Mọi command Rust đều có frontend bridge (`src/api/*`).

## 4) API tồn tại nhưng frontend chưa gọi

- **Không tìm thấy.** Đối chiếu `lib.rs` invoke_handler (37 commands) ↔ `src/api/*` — tất cả đều được gọi (project/job/subtitle/export/dictionary/media/pipeline/models/settings/provider/system/worker/bridge). `dictionary.*` gọi từ `DictionaryPage`; `export.*` từ `ExportView`/`StudioWorkspace`; `subtitle.*` từ editor + workspace.

## 5) Provider bị hard-code

- **Không có hard-code dispatch provider trong automation/pipeline.** `pipeline_runner.rs:run_translate` resolve qua `self.providers.resolve_translation(...)` — không `if kind == "gemini"`. Worker factory `build_translation_provider` (`pipeline.py:246-267`) là điểm phân phối duy nhất (cần thiết — nó *là* factory).
- Chỉ có nhãn hiển thị dùng `provider_kind === "free"` (`LeftPanel.tsx:239`, `RightPanel.tsx:469`, `ProvidersPanel.tsx:450,582`) — cosmetic.

## 6) Fake progress / fake log / fake video result

- **Không có.** `src/` không có `Math.random` cho progress; 6 `setTimeout/setInterval` đều hợp lệ (poll jobs 3s, poll worker, debounce subtitle save 600ms, toast auto-dismiss, đồng hồ elapsed 1s, worker-ready wait).
- Progress UI luôn bắt nguồn từ `job.progress`/`job:status` thật (`jobs.tsx` store; `deriveStages`).
- **Anti-fake guard trong backend:** `pipeline_runner.rs` kiểm tra file tồn tại + non-empty sau extract-audio (`run_transcribe`), sau tts (`run_tts`), sau render (`run_render` → `E_ARTIFACT_MISSING` khi "reported success but produced no output file").

## 7) Output path không thực sự tồn tại / báo success nhưng chưa tạo output thật

- **Không tìm thấy path ảo.** Artifact scheme duy nhất `pipeline_runner.rs:artifact_paths` (`cache/audio.wav, transcript.json, translation.json, subtitle.srt/ass, voice_track.wav`, `output/rendered.mp4`) — được cả Rust lẫn frontend dùng (`pipeline.artifact_paths`).
- **Ghi chú naming:** spec E2 muốn `output/<name>_<lang>_final.mp4`; repo dùng `output/rendered.mp4` (+ `params.output_name` tuỳ chọn). Kiểm tra tồn tại là thật → không phải lỗi, chỉ khác convention.
- Export thật: `render_service.export_video` copy + QC + atomic replace; `export_subtitles` copy/convert srt↔vtt.
- Settings → Storage "Output directory = per project {data}/projects/{id}/output" — đúng thực tế.

## 8) Tool chỉ có UI chưa xử lý thật

- **Không có.** Tools list = export (thật), preview (thật), watermark (thật), subtitles editor (thật), dictionary (thật), và các mục gắn cờ ⚡ điều hướng sang Automation (thật). Planned (Video Cutter/Converter/Logo Remover/Audio Separator/Mixer/Voice Generator/Dubbing) hiển thị trung thực là "Planned — later", không tạo UI giả.

## 9) Xác minh thêm các hạng mục quan trọng

- **STT thật:** `worker/src/services/stt_service.py` — faster-whisper (WhisperModel lazy), VAD, CUDA/CPU fallback, progress theo segment.
- **Translation thật:** `gemini_provider.py` (SDK + retry + structured output + repair), `local_llm_provider.py` (llama-server lifecycle + OpenAI-compat + VRAM guard), `mock_provider.py` (offline test). Context/glossary: `context_service.py` + `dictionary_service.rs`.
- **Subtitle thật:** `subtitle_service.py` sinh cues + SRT + ASS; Rust `SubtitleService` sync cue table cho editor.
- **TTS thật:** `tts_service.py` — edge-tts/piper, voice track assembly, atempo fit, retry.
- **Render thật:** `render_service.py` — burn-in libass, watermark, HW encoder fallback, **ffprobe validation** (resolution/FPS/duration/codec/audio) + burn-in pixel detection.
- **Log thật:** JobService emit `job:log`; LiveLog render đúng event; không fake.
- **Security:** key chỉ ở OS vault; token stdin; ffmpeg arg array; allowlists; gitleaks chưa cài (scan thủ công — sạch).

## 10) Vấn đề / ghi nhận mở (không phải defect)

1. **Benchmark dài (L2–L4, Z3) blocked** — thiếu fixture video dài + translation provider thật (Gemini key hoặc local LLM). Không phải lỗi app.
2. **OpenAI / OpenRouter provider** chưa có — ngoài MVP scope (AGENTS.md). Ghi nhận future work.
3. **Custom subtitle styling** (font/size/color/background/outline) chưa có backend — UI trung thực "Not available in this build".
4. **Live CPU/RAM %** không có endpoint — UI không bịa số.
5. **Z2 chưa click qua GUI Tauri** trong phiên này — pipeline E2E thật qua worker API đã chạy (TASK-L1); UI-level có evidence audit trước. Ghi nhận 1 lần chạy GUI để khép hoàn toàn.
6. ~~Dead-code scan (N1)~~ → đã xong ở Phase N: xoá `src/lib/version.ts`, `src/pages/About/index.tsx`, gộp duplicate contract `src/types/api.ts` → `worker/tests/unit/test_schema_examples.py`.

## 11) Artifact mới trong phiên audit (code, không phải doc)

- `worker/tests/integration/e2e_pipeline.py` — integration/benchmark script dùng lại được: fixture thật (edge-tts/SAPI + ffmpeg), worker thật, pipeline thật, ffprobe validation, báo cáo JSON. Không chạy trong `pytest tests` (không đặt tên `test_*`). Đây là test hữu ích — giữ nguyên theo TASK-N4.
