# PHASE_0_FINAL_CHANGELOG.md — Bản ghi FIX #1–#10

**Ngày:** 2026-08-09
**Phạm vi:** Mọi thay đổi áp dụng sau PHASE_0_CHANGELOG.md (S1–S6) trong quá trình **FINAL PRE-IMPLEMENTATION AUDIT**.
**Kết quả:** MASTER_PLAN nâng lên **ARCHITECTURE FREEZE V3**.

---

## FIX #1 — Tách MVP CORE vs MVP POLISH

| Mục | Cũ | Mới | Lý do |
|---|---|---|---|
| MASTER_PLAN §1.2 | 1 pipeline duy nhất (gồm editor/preview/watermark) | `MVP CORE` (vertical slice Import→Probe→Extract→STT→Translation→Subtitle Gen→Burn→Export) + `MVP POLISH` (editor, preview, job UI, cache, project persistence, watermark, GPU detect, privacy, TM/glossary, settings) | CORE chứng minh giá trị sớm; POLISH giữ nguyên trong MVP nhưng ưu tiên thấp hơn |
| MASTER_PLAN §38.1 | 1 list MUST HAVE 24 items trộn cả hai | Tách `38.1a MVP CORE` + `38.1b MVP POLISH`; CORE gắn quality gates (GOLDEN_VIDEO_TEST + QUALITY_BENCHMARK) | Scope rõ; cắt giảm ưu tiên ở POLISH trước |
| MASTER_PLAN §45.2 | 4 bước trộn editor/preview/watermark | CORE 5 bước → POLISH 9 mục | Đồng bộ với §1.2/§38 |
| ARCHITECTURE_DECISION §8 | MVP gồm editor/preview/watermark | Split CORE/POLISH | Đồng bộ freeze |
| IMPLEMENTATION_ROADMAP Phase gates | — | Thêm milestone CORE-slice trước POLISH | Đường tới pipeline sớm nhất |

## FIX #2 — QUALITY_BENCHMARK.md (Golden Translation Dataset)

| Mục | Cũ | Mới |
|---|---|---|
| Tạo mới | Chưa có | Tài liệu định nghĩa dataset (11 category), format case, runner, 5 tiêu chí chấm, ngưỡng theo preset, regression process. Data chưa có → `TODO — CREATE GOLDEN TRANSLATION DATASET` |

## FIX #3 — GOLDEN_VIDEO_TEST.md

| Mục | Cũ | Mới |
|---|---|---|
| Tạo mới | Chưa có | Spec video mẫu (10 phút, multi-speaker, music, tốc độ đa dạng...), 12 checkpoint, ngưỡng STT, PASS/FAIL/WARNING, không pixel-perfect. Fixture chưa có → `TODO — CREATE GOLDEN VIDEO FIXTURE` |

## FIX #4 — Context-aware wording (bỏ overclaim scene/emotion)

| Mục | Cũ | Mới |
|---|---|---|
| MASTER_PLAN §3.3 | "Hệ thống phải hiểu: scene, mối quan hệ nhân vật, tone, emotion..." | MVP = Context-aware (prev/current/next + glossary + character dict + speaker metadata + rules); scene/emotion = V1+ (Scene-aware/Speaker-aware/Character-aware) |
| MASTER_PLAN §12.1 | Scene Context nằm trong context engine | Scene chuyển sang V1+; MVP context liệt kê rõ |
| MASTER_PLAN §39 | — | Thêm dòng Scene-aware/Speaker-aware/Character-aware vào V1 |
| ARCHITECTURE_DECISION §3.3 | Context Engine gồm scene | scene = Post-MVP |
| ROADMAP Phase 6 / TASKS TASK-021 | context có scene | Bỏ scene; scene detection = Post-MVP |

## FIX #5 — Privacy Mode consistency

| Mục | Cũ | Mới |
|---|---|---|
| MASTER_PLAN §20.7 | "local STT/OCR/separation" (OCR/separation là Post-MVP) | MVP: local STT/FFmpeg/subtitle; cloud translation chỉ khi explicit consent; local translation khi có; không telemetry mặc định; OCR/separation không nằm ở MVP |
| MASTER_PLAN §4.3, §43 | — | Đồng bộ wording + fail-safe secrets |
| ARCHITECTURE_DECISION §4, §6 | Privacy "không upload video" | Làm rõ explicit consent + local default |

## FIX #6 — ModelRegistry/Downloader/Verifier/Cache

| Mục | Cũ | Mới |
|---|---|---|
| MASTER_PLAN §32.4 | 2 dòng (tải lần đầu + import thủ công) | Mô tả 4 thành phần + metadata (id, name, version, source, URL, size, checksum, license, VRAM, backend) + checksum fail → không available |
| TASKS TASK-016 | 1 task monolithic | TASK-016A Registry / B Downloader / C Verifier / D Cache — mỗi phần có gate riêng |
| ARCHITECTURE_DECISION §3.4 | — | Thêm ModelManagerService |

## FIX #7 — Translation provider strategy (MVP = Gemini + Local LLM)

| Mục | Cũ | Mới |
|---|---|---|
| ARCHITECTURE_DECISION §3.1 | providers `{openai,gemini,anthropic,deepseek,local_llm,mock}` | `{gemini,local_llm,mock}`; openai/anthropic/deepseek = V1+ |
| ARCHITECTURE_DECISION §2.5/§3.3 | GPT-4o-mini/Claude Haiku trong preset | MVP chỉ Gemini |
| MASTER_PLAN §12.3 | table có gpt-4o-mini/DeepSeek/Claude | MVP chỉ Gemini 2.5 Flash-Lite/Flash; còn lại V1+ |
| MASTER_PLAN §7.6 | kết luận "provider user cấu hình (OpenAI/Gemini/Claude/DeepSeek...)" | MVP chỉ Gemini + Local LLM + Mock; phần còn lại V1+ |
| ROADMAP Phase 6 | OpenAI + Gemini + Anthropic + DeepSeek + Local | Gemini + Local (MVP); OpenAI/Anthropic/DeepSeek Post-MVP |
| TASKS TASK-018 | OpenAI provider = MVP | `[Post-MVP, implement sau TASK-020]`; path MVP bỏ 018 |
| TASKS TASK-021 deps | 018, 019, 020 | 019, 020 |

## FIX #8 — Security/credential fail-safe (bỏ custom crypto)

| Mục | Cũ | Mới |
|---|---|---|
| MASTER_PLAN §15.1 | "tauri-plugin-stronghold **hoặc** Windows Credential Manager" | Chỉ Windows Credential Manager; bỏ stronghold |
| MASTER_PLAN §20.1 | — | Thêm fail-safe: CM không khả dụng → chặn lưu key + thông báo; KHÔNG custom crypto/encrypted-file |
| ARCHITECTURE_DECISION §6 | CM + không log | Thêm fail-safe rule |
| TASKS TASK-030 | "fallback mạnh mật khẩu encrypted file (ghi chú rủi ro)" | Bỏ — fail-safe: chặn lưu key + thông báo |

## FIX #9 — Licensing audit (format đầy đủ)

| Mục | Cũ | Mới |
|---|---|---|
| MASTER_PLAN §21 | 7 cột, thiếu Build config/Notice/Source disclosure | 9 cột đúng format yêu cầu + thêm GPU runtime (CUDA/ROCm/Vulkan Loader) + llama.cpp server row |
| ARCHITECTURE_DECISION §7 | thiếu GPU deps | Thêm GPU runtime + Vulkan Loader |

## FIX #10 — Translation ↔ STT dependency (dev fixtures vs runtime)

| Mục | Cũ | Mới |
|---|---|---|
| ROADMAP §2 + §4 | Path `013→017→018→021`; không nói fixtures | Path `017→019→020→021`; thêm mock transcript fixtures (`fixtures/transcripts/*.json`) để translation dev song song STT; runtime bắt buộc `STT → Transcript → Context Engine → Translation` |
| TASKS §2 | — | Thêm note FIX #10 (dev fixture = dev dependency, runtime bắt buộc) |

## CÁC BỔ SUNG KHÁC (trong cùng audit)

| Chủ đề | Thay đổi |
|---|---|
| Subtitle policy | MASTER_PLAN §11.1, ROADMAP Phase 7, TASKS TASK-024: bỏ "42 ký tự" universal → `max_chars_per_line` configurable theo ngôn ngữ/font/display width |
| Preview ≈ final render | ARCHITECTURE_DECISION §4, ROADMAP Phase 8, TASKS TASK-026: overlay khớp ASS defaults (position/font/stroke/shadow) |
| Render validation | MASTER_PLAN §43, ROADMAP Phase 9, TASKS TASK-027/029: verify resolution/FPS/audio/codec/container/duration/burn-in; không xuất file hỏng im lặng |
| STT Vulkan non-blocker | MASTER_PLAN §14.2, ROADMAP Phase 5, TASKS TASK-015: Vulkan = compatibility enhancement; init fail → CPU fallback + warning |
| DoD 3 tầng | MASTER_PLAN §44, TASKS §7: Technical / Product / Quality |
| AI coding agent policy | TASKS §8 (mới): **ONE TASK = ONE GATE** — gate fail → STOP + báo blocker; không đổi architecture/scope khi chưa approval |
| Header | MASTER_PLAN: FREEZE V2 → **FREEZE V3** |
| Task table | MASTER_PLAN §45.5: TASK-016 (4 modules), TASK-018 [Post-MVP] |

---

*Hết PHASE_0_FINAL_CHANGELOG.md — đối chiếu với PHASE_0_CHANGELOG.md (S1–S6) và MASTER_PLAN_REVIEW.md.*
