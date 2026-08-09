# MASTER_PLAN.md — AI Video Localization Studio

**Version:** 1.0.0
**Status:** FROZEN — ARCHITECTURE FREEZE V3 (S1–S6 + FIX #1–#10 applied; xem PHASE_0_CHANGELOG.md và PHASE_0_FINAL_CHANGELOG.md)
**Ngày tạo:** 2026-08-09
**Source of truth cho:** toàn bộ quá trình phát triển sản phẩm AI Video Localization Studio.
**Ngôn ngữ:** Tiếng Việt (thuật ngữ kỹ thuật giữ nguyên tiếng Anh).

> **QUY TẮC QUAN TRỌNG NHẤT:** File này là *technical master plan* giao trực tiếp cho team developer / AI coding agent. **KHÔNG CODE** ở bước này. Mọi quyết định kỹ thuật phải có: `Problem → Possible Solutions → Comparison → Recommended Solution → Reason → Trade-offs → Fallback`. Mọi thông tin về model/API/library/license/pricing phải được kiểm chứng trên Internet trước khi đưa vào. Nếu chưa chắc chắn → đánh dấu `TODO — VERIFY`.

---

## MỤC LỤC

1. [Product Vision](#1-product-vision)
2. [Requirements](#2-requirements)
3. [Functional Requirements](#3-functional-requirements)
4. [Non-Functional Requirements](#4-non-functional-requirements)
5. [Complete Architecture](#5-complete-architecture)
6. [Technology Selection](#6-technology-selection)
7. [Technology Comparison](#7-technology-comparison)
8. [AI Architecture](#8-ai-architecture)
9. [Video Architecture](#9-video-architecture)
10. [Audio Architecture](#10-audio-architecture)
11. [Subtitle Architecture](#11-subtitle-architecture)
12. [Translation Architecture](#12-translation-architecture)
13. [TTS Architecture](#13-tts-architecture)
14. [GPU Architecture](#14-gpu-architecture)
15. [Desktop Architecture](#15-desktop-architecture)
16. [Backend Architecture](#16-backend-architecture)
17. [Database Architecture](#17-database-architecture)
18. [Job System](#18-job-system)
19. [Cache System](#19-cache-system)
20. [Security](#20-security)
21. [Licensing](#21-licensing)
22. [Folder Structure](#22-folder-structure)
23. [Module Responsibilities](#23-module-responsibilities)
24. [Data Schemas](#24-data-schemas)
25. [API Design](#25-api-design)
26. [UI/UX Architecture](#26-uiux-architecture)
27. [Processing Pipelines](#27-processing-pipelines)
28. [Error Handling](#28-error-handling)
29. [Testing Strategy](#29-testing-strategy)
30. [Benchmark Strategy](#30-benchmark-strategy)
31. [Performance Optimization](#31-performance-optimization)
32. [Packaging](#32-packaging)
33. [Installer](#33-installer)
34. [Code Signing](#34-code-signing)
35. [Auto Update](#35-auto-update)
36. [Commercial Architecture](#36-commercial-architecture)
37. [Development Roadmap](#37-development-roadmap)
38. [MVP Definition](#38-mvp-definition)
39. [V1 Definition](#39-v1-definition)
40. [Future Roadmap](#40-future-roadmap)
41. [Risks](#41-risks)
42. [Technical Debt](#42-technical-debt)
43. [Acceptance Criteria](#43-acceptance-criteria)
44. [Definition of Done](#44-definition-of-done)
45. [FINAL RECOMMENDATION](#45-final-recommendation)

---

# 1. Product Vision

## 1.1 Tầm nhìn

**AI Video Localization Studio** là một ứng dụng desktop Windows (`.exe`) cho phép người dùng nhập một video và tự động thực hiện toàn bộ quy trình bản địa hóa (localization):

```text
Input Video
    ↓
Video Analysis
    ↓
Audio Extraction
    ↓
Speech-to-Text (Local)
    ↓
Speaker Detection / Diarization
    ↓
Context Understanding
    ↓
AI Contextual Translation
    ↓
Subtitle Generation
    ↓
Subtitle Rendering (burn-in)
    ↓
[Post-MVP] OCR / Burned-in Subtitle Removal
    ↓
[Post-MVP] Original Voice Separation
    ↓
[Post-MVP] Voice Generation / AI Dubbing
    ↓
[Post-MVP] Music / SFX Preservation
    ↓
[Post-MVP] Audio Mixing
    ↓
Watermark
    ↓
Video Rendering
    ↓
Quality Check
    ↓
Final Video
```

Sản phẩm hướng tới **chất lượng thương mại** (không phải script demo), chạy ổn định trên máy Windows của hàng nghìn người dùng.

## 1.2 MVP (Minimum Viable Product)

MVP chia làm 2 tầng: **MVP CORE** (vertical slice — chứng minh giá trị trước tiên) và **MVP POLISH** (hoàn thiện UX/settings sau khi CORE chạy).

**MVP CORE (vertical slice — làm trước):**

```text
Import Video
 → Analyze Video (ffprobe)
 → Extract Audio
 → Local STT
 → Contextual Translation
 → Subtitle Generation
 → Subtitle Burn-in
 → Export
```

**MVP POLISH (sau CORE — vẫn nằm trong MVP, thứ tự thấp hơn):** subtitle editing UI, subtitle preview, job UI hoàn chỉnh, cache, project save/load/resume, watermark, GPU detect + override, privacy mode, translation memory + glossary UI, settings, auto-update.

MVP **KHÔNG gồm** (đưa sang Post-MVP): AI dubbing, audio separation, voice cloning, advanced OCR removal, complex timeline, cloud backend, billing/subscription/enterprise.

## 1.3 Mô hình AI

**Hybrid — Local-first, Cloud opt-in:**

```text
STT           → Local (faster-whisper / whisper.cpp)
OCR           → Local (PaddleOCR/RapidOCR)
Audio         → Local (FFmpeg)
Separation    → Local (Demucs) [Post-MVP]
Translation   → Cloud mặc định + Local fallback
TTS           → Cloud mặc định + Local fallback [Post-MVP]
```

**Lý do:** người dùng nhập video là dữ liệu nhạy cảm; STT/OCR/video chạy local để bảo mật và tiết kiệm chi phí. Translation/TTS dùng cloud để đạt chất lượng cao, nhưng phải có fallback local để đảm bảo offline capability và tránh lock-in.

## 1.4 Nguyên tắc thiết kế (thứ tự ưu tiên)

```text
Reliability > Output Quality > User Experience > Performance > Cost > Development Speed
```

Phải giải thích trade-off khi ưu tiên. MVP không over-engineering nhưng thiết kế theo hướng `MVP → Scalable Architecture → Commercial Product`.

---

# 2. Requirements

## 2.1 Phạm vi sản phẩm

| Loại | Nội dung |
|---|---|
| Nền tảng | Windows 10 (64-bit), Windows 11 |
| Định dạng đầu vào | MP4, MKV, MOV, AVI, WebM |
| Ngôn ngữ nguồn | Tiếng Trung (Quan Thoại), tiếng Anh, tiếng Nhật, tiếng Hàn (ưu tiên Trung ↔ Việt) |
| Ngôn ngữ đích | Tiếng Việt (ưu tiên #1), tiếng Anh |
| Đầu ra | Video đã render (subtitle burn-in), SRT/VTT/ASS subtitles |
| Độ dài video | 1 phút → 2 giờ+ (không load toàn bộ vào RAM) |

## 2.2 Người dùng mục tiêu

- Creator / editor dịch nội dung video Trung Quốc sang tiếng Việt (phim, TikTok, douyin, phim ngắn).
- Agency / studio bản địa hóa nội dung cho nền tảng video ngắn.
- Người dùng cá nhân muốn xem nội dung ngoại ngữ với phụ đề Việt.

## 2.3 Constraints (Ràng buộc)

- End-user **không phải cài** Python, Node.js, Rust, FFmpeg, CUDA toolkit (bundle vào installer).
- App phải chạy được khi **không có GPU** (CPU fallback), nhưng khuyến khích GPU.
- App phải hoạt động **offline** cho pipeline local.
- Không hard-code secret/API key trong frontend hoặc `.exe`.

---

# 3. Functional Requirements

## 3.1 Video Import & Analysis

- Import MP4/MKV/MOV/AVI/WebM.
- Phân tích tự động: resolution, FPS, duration, codec, bitrate, audio tracks, subtitle tracks, language, aspect ratio, rotation, color format.
- Hiển thị thông tin media trước khi xử lý.

## 3.2 Speech-to-Text (Local)

- Detect ngôn ngữ tự động + cho phép override.
- Timestamp cấp segment (bắt buộc), word-level timestamp (ưu tiên, dùng cho timing/preview).
- Sentence segmentation.
- Confidence score.
- Speaker diarization (tùy chọn, cần HF token) — đánh dấu optional ở MVP.

## 3.3 Contextual AI Translation

- Không dịch từng câu độc lập. **MVP (Context-aware):** context gồm segment trước/segment hiện tại/segment sau, speaker metadata (nếu có), glossary, character dictionary, translation rules, genre.
- **KHÔNG ghi nhận ở MVP** các khả năng cần scene detection/diarization sản xuất: hiểu scene, mối quan hệ nhân vật, tone/emotion của diễn viên. Các khả năng này thuộc **V1+ (Scene-aware / Speaker-aware / Character-aware)** — xem §12.1 và mục 39.
- Output: JSON schema có kiểm soát (structured output).
- Translation memory + glossary + character dictionary.
- Retry + validation + hallucination detection + quality scoring.

## 3.4 Subtitle Generation

- Tạo SRT / VTT / ASS; embedded subtitle khi render.
- Line breaking, max chars per line, reading speed, timing, safe area, position, font, font size, stroke, shadow, background box.

## 3.5 Subtitle Editing / Preview

- Bảng edit transcript/translation (source ↔ translated song song).
- Preview video với subtitle overlay trong app (HTML5 canvas / video element).
- Chỉnh sửa thủ công timing, text, style.

## 3.6 Subtitle Burn-in & Render

- Render subtitle vào video bằng libass (ASS) hoặc drawtext (SRT đơn giản).
- Giữ nguyên resolution/FPS/aspect ratio.
- Hardware acceleration: NVENC / QuickSync / AMF; fallback CPU (libx264/libx265).
- Progress, ETA, cancel, error recovery, temporary files cleanup.

## 3.7 Watermark

- Text watermark (text, font, size, color, opacity, position, rotation, margin).
- Image watermark (PNG/JPG/WebP, scale, opacity, position).
- Vị trí: 9 vị trí + custom.

## 3.8 Export

- Export video (render), export subtitle files (SRT/VTT/ASS).
- Chọn thư mục output, preset chất lượng.

## 3.9 Project System

- Mỗi video = một project; save/load/resume.
- Metadata + cache trong SQLite.

## 3.10 Job Pipeline

- Pipeline: IMPORT → ANALYZE → EXTRACT_AUDIO → TRANSCRIBE → TRANSLATE → GENERATE_SUBTITLE → RENDER → EXPORT.
- Trạng thái: QUEUED / RUNNING / COMPLETED / FAILED / CANCELLED.
- Progress, retry, logs, error code, cancellation, dependency, cache.

## 3.11 Caching

- Cache theo từng stage (STT / Translation / TTS / Render). Đổi subtitle style → chỉ render lại, không chạy lại AI.

## 3.12 Settings

- AI settings (chọn model, device, provider, API key).
- GPU settings (tự detect + cho phép override device).
- API settings (provider, key, base URL).
- Chất lượng preset: Fast / Balanced / High Quality / Maximum Quality.
- Privacy Mode.

---

# 4. Non-Functional Requirements

## 4.1 Performance targets (thiết kế cho)

| Video | Target | Ràng buộc |
|---|---|---|
| 1 phút | Hoàn tất pipeline nhanh, khởi động nhanh | Test thường xuyên |
| 10 phút | Ổn định, không OOM | Streaming/chunk |
| 30 phút | Ổn định, progress rõ ràng | Chunk, cache |
| 1 giờ | Chạy qua đêm an toàn | Resume, checkpoint |
| 2 giờ+ | Không sập, không ngốn RAM | Streaming, never load full video |

- RAM: không load toàn bộ video vào RAM. FFmpeg dùng pipe/stream. STT xử lý audio đã extract (chunk theo VAD).
- VRAM: lớn nhất là separation (Post-MVP). STT large-v3 int8 ~2.5–2.9 GB; separation ~8 GB.
- Disk: temp files phải được dọn dẹp; cho phép cấu hình location + quota.
- Parallelism: các job độc lập (VD: extract audio xong có thể transcribe trong khi user xem trước metadata) nhưng **thận trọng** — không chạy song song 2 job nặng AI cùng lúc để tránh OOM.

## 4.2 Độ tin cậy

- Crash recovery: resume từ stage cuối cùng hoàn thành (dựa cache + job status).
- Error handling đầy đủ (xem [28. Error Handling](#28-error-handling)).
- Không mất project khi crash (SQLite WAL + auto-save).

## 4.3 Bảo mật

- API keys lưu trong Windows Credential Manager (không trong source, không trong DB plaintext). Nếu Credential Manager không khả dụng → **fail-safe**: chặn lưu key + thông báo rõ ràng cho user; KHÔNG custom crypto, KHÔNG hardcoded key, KHÔNG fallback file encrypted tự chế (xem §15.1).
- Không log secret.
- Chống command injection khi gọi FFmpeg (không shell string concatenation; dùng argument array).
- Chống path traversal.
- Privacy Mode (mặc định): STT/FFmpeg/subtitle/render local — không upload video; translation cloud chỉ khi explicit consent; xóa temp khi hoàn tất; không gửi telemetry nếu user không đồng ý.

## 4.4 Khả năng mở rộng

- Provider abstraction: STT/Translation/TTS/OCR/Separation đều là interface, có thể thay provider.
- Không lock-in.

## 4.5 Maintainability

- Module boundaries rõ ràng.
- Log structured.
- Tài liệu: README, ARCHITECTURE, DEVELOPMENT, AI_PIPELINE, VIDEO_PIPELINE, AUDIO_PIPELINE, DATABASE, API, SECURITY, LICENSING, TESTING, RELEASE.

## 4.6 Offline capability

- Local pipeline (STT/OCR/video/render) chạy offline hoàn toàn.
- Translation/TTS cloud cần mạng; khi mất mạng → local fallback (nếu có) hoặc cảnh báo rõ ràng.

---

# 5. Complete Architecture

## 5.1 Tổng quan kiến trúc

```text
┌────────────────────────────────────────────────────┐
│                  Tauri 2 Desktop App              │
│  ┌─────────────────────────────────────────────┐  │
│  │         Frontend: React + TypeScript        │  │
│  │         UI: Tailwind + component library    │  │
│  │         State: Zustand + TanStack Query     │  │
│  └────────────────────┬────────────────────────┘  │
│                       │ IPC (Tauri commands / events) │
│  ┌────────────────────┴────────────────────────┐  │
│  │         Rust Core (Tauri backend)           │  │
│  │  ProjectService / JobService / CacheService │  │
│  │  SettingsService / MediaProbe (ffprobe)     │  │
│  │  SQLite (rusqlite) / Secret storage         │  │
│  └────────────────────┬────────────────────────┘  │
└───────────────────────┼───────────────────────────┘
                        │ spawn / manage lifecycle
┌───────────────────────▼───────────────────────────┐
│           Python AI Worker (sidecar process)     │
│  FastAPI / JSON-RPC over localhost:port           │
│  ┌──────────┬──────────┬──────────┬────────────┐  │
│  │ STT      │Translate │ Subtitle │ FFmpeg/    │  │
│  │ (faster-  │ (LLM)    │ Engine   │ Render     │  │
│  │ whisper) │          │          │            │  │
│  └──────────┴──────────┴──────────┴────────────┘  │
└───────────────────────┬───────────────────────────┘
                        │
┌───────────────────────▼───────────────────────────┐
│   FFmpeg/FFprobe (bundled)  +  GPU (CUDA/QSV/AMF) │
└───────────────────────────────────────────────────┘
```

## 5.2 Nguyên tắc phân tầng

- **Tầng 1 (Tauri Rust):** quản lý lifecycle sidecar, SQLite, secrets, IPC an toàn, job orchestration (nhẹ).
- **Tầng 2 (Python AI Worker):** tất cả công việc AI/video nặng. Chạy như process riêng → crash của AI không làm chết app; có thể giết/restart.
- **Tầng 3 (Frontend):** UI thuần, gọi qua typed bridge, không biết gì về file system trực tiếp.

**Lý do tách Python worker thành process riêng (không dùng PyO3 inline):**
- PyTorch/CUDA heavy; lỗi/segfault không làm chết toàn app.
- Có thể tắt/bật, restart, set resource limits, chạy ở thread riêng.
- Giao tiếp qua HTTP/WebSocket trên localhost → dễ test, dễ debug, dễ thay bằng service khác.
- Tauri giữ Rust core mỏng (giảm compile time, giảm rủi ro Rust phức tạp).

**Fallback:** nếu quá khó bundle Python, thay bằng `tauri-plugin-python` (PyO3) nhưng cần bundle libpython — đánh dấu là phương án dự phòng, không phải mặc định.

## 5.3 Luồng dữ liệu MVP

```text
Video file
 → (Rust) MediaProbe via ffprobe → MediaMetadata
 → (Python) FFmpeg extract audio → audio.wav (16k mono)
 → (Python) faster-whisper → transcript.json (segments + words + speakers)
 → (Python) LLM translate (cloud/local) → translation.json
 → (Python) SubtitleEngine → subtitle.ass / subtitle.srt
 → (Python) FFmpeg render (libass burn-in) → output.mp4
 → (Python) Quality check via ffprobe → OK/FAIL
```

---

# 6. Technology Selection

## 6.1 Quyết định tổng thể (FINAL — xem chi tiết ở [7. Technology Comparison](#7-technology-comparison))

| Layer | Lựa chọn | Lý do tóm tắt |
|---|---|---|
| Desktop shell | **Tauri 2** | RAM thấp, bundle nhỏ, security model tốt; Python chạy sidecar |
| Frontend | **React 18 + TypeScript + Vite** | Hệ sinh thái lớn, dễ tuyển, typed |
| UI | **Tailwind CSS + shadcn/ui (Radix)** | Nhất quán, accessible, nhanh |
| State | **Zustand + TanStack Query** | Nhẹ, quản lý async/progress tốt |
| Rust core | **Tauri 2 + rusqlite + tauri-plugin-* (dialog, fs, store, shell, updater)** | Chính thống |
| AI runtime | **Python 3.11 (CPython) — sidecar worker** | PyTorch/whisper/demucs/LLM SDK đều là Python-first |
| Backend (trong worker) | **FastAPI (Uvicorn) trên localhost random port** | Async, WebSocket cho progress, OpenAPI để test |
| Database | **SQLite (WAL)** — file trong project dir + app-data | Local, không cần server, đủ cho desktop |
| Video/audio | **FFmpeg 7.x + FFprobe (bundled, statically built)** | Chuẩn ngành |
| STT | **faster-whisper (CTranslate2) — GPU/CPU; whisper.cpp — CPU/fallback** | Xem 8.2 |
| Diarization | **pyannote speaker-diarization-community-1 (CC-BY-4.0)** | Optional MVP; cần HF token |
| Translation | **Cloud: Gemini 2.5 Flash-Lite·Flash (MVP default) + Local fallback: llama.cpp + Qwen GGUF** (OpenAI/Anthropic/DeepSeek = V1+ configurable) | Xem 8.4 / 12 |
| OCR | **RapidOCR (ONNX) / PaddleOCR** | Post-MVP removal; dùng cho detect hiển thị |
| Separation | **Demucs v4 htdemucs_ft (MIT)** | Post-MVP |
| TTS | **Cloud: ElevenLabs / Azure / OpenAI TTS + Local: Kokoro (Apache-2.0) / Chatterbox (MIT)** | Post-MVP |
| Render | **FFmpeg + libass (ASS) + NVENC/QSV/AMF encoder** | Xem 9 |
| Packaging | **Tauri bundler → NSIS installer + WebView2** | Xem 32–35 |
| Testing | **Rust: cargo test; Python: pytest; Frontend: Vitest + Playwright** | Xem 29 |

---

# 7. Technology Comparison

## 7.1 Desktop Framework

**Problem:** cần app desktop Windows chạy nặng AI/FFmpeg, RAM hạn chế, cài cho người dùng phổ thông, bundle Python worker.

| Tiêu chí | Tauri 2 | Electron | .NET WPF/WinUI |
|---|---|---|---|
| Performance | Cao (native shell) | Trung bình (Chromium) | Cao |
| RAM idle | ~30–80 MB | ~200–400 MB | ~50–100 MB |
| Bundle size | 5–15 MB (app) + sidecar | 80–250 MB | 30–80 MB |
| Rendering | WebView2 (Windows) — nhất quán trên Win10/11 | Chromium bundled — pixel-identical | Native |
| Dev speed | Trung bình (Rust + TS) | Nhanh | Trung bình |
| Security model | Capability-based (allowlist) | Opt-in hardening | OS-based |
| Ecosystem | Tốt (đang lớn) | Rất lớn | Tốt (Windows) |
| Python integration | Sidecar (đã chứng minh) | child_process | subprocess / IronPython |
| FFmpeg integration | Shell sidecar | shell | Process |
| AI integration | Python sidecar | Python sidecar | Python subprocess |
| Installer | NSIS/MSI built-in | electron-builder | MSIX/InstallShield |
| Auto update | Built-in updater plugin | electron-updater | Windows Update / tự làm |
| Mobile | iOS/Android | Không | Không (WinUI: Windows) |

**Kết luận:** **Tauri 2**.
- *Reason:* sản phẩm chạy cùng lúc với AI worker ngốn RAM/VRAM → tiết kiệm RAM của shell là rất quan trọng. WebView2 có sẵn trên Windows 10/11 hiện đại → không cần bundle Chromium. Security model deny-by-default phù hợp app xử lý dữ liệu nhạy cảm.
- *Trade-off:* cần viết Rust; hệ sinh thái nhỏ hơn Electron; bắt buộc kiểm soát CSP. Nhưng Rust core chỉ cần mỏng (orchestration), phần nặng nằm ở Python.
- *Fallback:* nếu team không có kinh nghiệm Rust → **Electron** là lựa chọn dự phòng (mọi thứ khác giữ nguyên; chỉ đổi shell + IPC).

## 7.2 AI Runtime / Backend trong worker

**Problem:** cần chạy PyTorch/whisper/LLM trong desktop app.

| Tiêu chí | Python (sidecar) | Rust (native) | Node.js |
|---|---|---|---|
| Hệ sinh thái AI | ★★★★★ (PyTorch, HF, whisper, demucs) | ★★ (burn, whisper.cpp) | ★★★ (runtime server) |
| STT quality | ★★★★★ | ★★★★ (whisper.cpp) | ★★★ |
| TTS/Demucs | ★★★★★ | ★★ | ★★ |
| LLM SDK | ★★★★★ | ★★★★ | ★★★★ |
| Dev speed | ★★★★ | ★★ | ★★★ |
| Bundle | Trung bình (python-build-standalone / PyInstaller) | Nhỏ | Trung bình |

**Kết luận:** **Python sidecar** cho AI; **Rust** chỉ giữ orchestration mỏng.
- *Trade-off:* bundle Python cồng kềnh (~300–600 MB với torch CUDA), cần CI build per-platform. Chấp nhận vì không có lựa chọn nào tốt hơn cho chất lượng AI.
- *Fallback:* nếu installer quá lớn → tách CUDA wheels thành "AI add-on download optional" (mặc định CPU, hỏi user có tải GPU runtime).

## 7.3 Database

| Tiêu chí | SQLite | PostgreSQL |
|---|---|---|
| Triển khai | File, zero-config | Cần server |
| Phù hợp desktop | ★★★★★ | ★★ |
| Concurrency | WAL tốt cho 1 process | Tốt |
| Backup/migration | Đơn giản | Phức tạp |
| Post-MVP cloud | Khó đồng bộ (cần bridge) | Tốt |

**Kết luận:** **SQLite (WAL mode)**, file DB đặt trong thư mục app-data. Dùng `migrations` có version (như SQLite fork `diesel` hoặc thư viện migration đơn giản).
- *Trade-off:* không support multi-process write đồng thời tốt → chỉ Rust core (hoặc worker) ghi DB, các bên khác đọc qua API.

## 7.4 STT — so sánh chi tiết (đã kiểm chứng 2026)

| Tiêu chí | faster-whisper | whisper.cpp | OpenAI Whisper |
|---|---|---|---|
| Backend | CTranslate2 (CUDA/CPU, int8) | ggml (CUDA/Vulkan/Metal/CPU) | PyTorch |
| NVIDIA speed (large-v3 int8) | ~12× RT (RTX 4070), ~2.5–2.9 GB VRAM | ~8× RT (CUDA) | chậm, ~4.5–10 GB |
| CPU | Tốt (int8) | Tốt (AVX/NEON) | Trung bình |
| Windows GPU | ★★★★★ (CUDA) | ★★★★ (CUDA/Vulkan) | ★★★ |
| AMD GPU | ✗ (chỉ CUDA) | ★★★★ (Vulkan) | ✗ |
| Word timestamps | ✗ native (cần WhisperX align) | ✗ (interpolate) | ✗ |
| Diarization | ✗ (cần WhisperX/pyannote) | ✗ | ✗ |
| Language detect | ✓ | ✓ | ✓ |
| VAD built-in | ✓ (Silero VAD) | cơ bản | ✗ |
| License | MIT | MIT | MIT |
| Python API | Native | C wrapper (pywhispercpp) | Native |

**Kết luận STT (MVP):**
- **NVIDIA + Python → faster-whisper** (compute_type `int8_float16`), model **large-v3** (chất lượng) / **turbo** (nhanh). Đây là lựa chọn chính.
- **CPU-only / AMD / máy không CUDA → whisper.cpp** (Vulkan backend cho AMD/Intel, hoặc CPU AVX2). *Fallback tự động* dựa GPU detect.
- Word-level timestamps: dùng **WhisperX alignment** (wav2vec2 forced alignment) để <100 ms — phục vụ karaoke-style preview và timing chính xác. Đánh dấu optional/Post-MVP nếu tăng complexity.

> **VERIFIED 2026-08-09:** whisper.cpp Vulkan trên Windows hoạt động nhưng có crash driver-specific đã ghi nhận (AMD Radeon 780M + beam-size 8 + VAD → segfault #3723; AMD RDNA4 + flash-attn → crash #3806; MSVC static lib → Vulkan backend không register #3750; init đa thread → race #3638). → Bắt buộc **3 mitigation** ở [8.2](#82-stt-design-local) và [14.2](#142-strategy-matrix). Vẫn phải benchmark lại với phần cứng mục tiêu ở Phase 5.

## 7.5 Diarization

| Tiêu chí | pyannote community-1 | pyannote 3.1 | Simple diarization (energy) |
|---|---|---|---|
| Chất lượng | Tốt nhất OSS | Cũ hơn | Kém |
| License | CC-BY-4.0 (gated HF) | MIT code + gated model | — |
| Cần HF token | ✓ (accept agreement) | ✓ | ✗ |
| GPU VRAM | ~2–4 GB | ~2–4 GB | ✗ |
| Cài đặt | pyannote-audio 3.x/4.x | pyannote-audio 3.0 | — |

**Kết luận:** MVP **optional**. Mặc định OFF; bật khi user có HF token + accept license. Dùng `pyannote/speaker-diarization-community-1` (CC-BY-4.0). Nếu không có token → không diarization (speaker = `speaker_00` tất cả), app vẫn chạy.

## 7.6 Translation provider (đã kiểm chứng giá cả 2026 — mang tính tham khảo)

| Provider | Model (đề xuất) | Context | Giá 1M tokens (≈) | Chất lượng Trung↔Việt | Streaming | Structured output | Batch |
|---|---|---|---|---|---|---|---|
| OpenAI | gpt-4o-mini | 128K | $0.15 in / $0.60 out | Tốt | ✓ | JSON mode ✓ | ✓ |
| Google | Gemini 2.5 Flash-Lite (default) / 2.5 Flash | 1M | $0.10/$0.40 (Lite) · $0.30/$2.50 (Flash) | Rất tốt | ✓ | JSON ✓ | ✓ |
| Anthropic | Claude Haiku/Sonnet | 200K | rẻ→trung bình | Tốt | ✓ | JSON ✓ | ✓ |
| DeepSeek | deepseek-chat/V3 | 64K | rất rẻ | Tốt (Trung gốc) | ✓ | JSON ✓ | ✓ |
| Local | Qwen2.5/Qwen3 GGUF (7–14B) qua llama.cpp | 32K+ | $0 | Trung bình–Tốt | ✓ | ✓ | ✓ |

> **VERIFIED 2026-08-09:** Gemini 2.0 Flash đã **shutdown 1/6/2026** → default = Gemini 2.5 Flash-Lite ($0.10/$0.40), Gemini 2.5 Flash ($0.30/$2.50); GPT-4o-mini $0.15/$0.60; DeepSeek V4-Flash $0.14/$0.28; Claude Haiku 3.5 ~$0.80/$4.00. **Re-verify giá/model chính xác tại thời điểm implement** (giá/model đổi liên tục).

**Kết luận:** **Abstraction `TranslationProvider`**. **MVP chỉ implement:** `GeminiProvider` (cloud default) + `LocalLLMProvider` (llama.cpp server + Qwen GGUF) + `MockProvider` (test). OpenAI/Anthropic/DeepSeek/Custom OpenAI-compatible **chỉ là V1+** — interface đã sẵn, user config được sau. Không lock-in.

## 7.7 TTS (Post-MVP — thiết kế trước)

| Nhu cầu | Best Quality | Best Cost | Best Privacy | Best Local | Best Commercial License |
|---|---|---|---|---|---|
| Cloud | ElevenLabs | Azure/Google/OpenAI TTS (đắt vừa) | — | — | ✓ |
| Local | Chatterbox (MIT) | Kokoro (Apache-2.0) | ✓ | ✓ | ✓ |
| Local (chất lượng cao) | XTTS v2 (CPML — **non-commercial**) | F5-TTS (CC-BY-NC — **non-commercial**) | ✓ | ✓ | ✗ |

**Kết luận TTS:** Post-MVP. `TTSProvider` abstraction. Mặc định **cloud** (ElevenLabs/Azure) cho dubbing chất lượng cao — đặc biệt cho **tiếng Việt** (local VN chưa có lựa chọn thương mại an toàn, xem dưới). **Local:** **Kokoro gốc** (Apache-2.0, chạy CPU — hỗ trợ tiếng Trung `zf_xiaobei`, **KHÔNG có tiếng Việt**) và **Chatterbox v3** (MIT — tránh dùng tiếng Việt vì CER ~75% không production, dùng cho ngôn ngữ khác). **KHÔNG dùng XTTS v2 / F5-TTS / Viterbox trong sản phẩm thương mại** (license non-commercial). **Kokoro-Vietnamese (community fine-tune): tồn tại nhưng license KHÔNG rõ ràng (provenance viVoice CC-BY-NC-SA / LarVoice) → `TODO — VERIFY BEFORE COMMERCIAL RELEASE`; không dùng thương mại khi chưa có bằng chứng license.** Tiếng Việt local fallback (Post-MVP): Piper (kiểm tra license từng giọng, cảnh báo fork GPL-3.0). **VERIFIED 2026-08-09** — chi tiết ở [21. Licensing](#21-licensing).

## 7.8 OCR (Post-MVP removal; dùng cho detect)

| Engine | License | GPU | Chất lượng tiếng Trung | Cài đặt |
|---|---|---|---|---|
| RapidOCR (ONNX/Paddle) | Apache-2.0 | ONNX Runtime (CPU/GPU) | Tốt | Dễ |
| PaddleOCR | Apache-2.0 | CUDA/CPU | Rất tốt | Trung bình |
| EasyOCR | Apache-2.0 | PyTorch | Tốt | Dễ |
| Tesseract | Apache-2.0 | CPU | Trung bình | Dễ |

**Kết luận:** **RapidOCR** mặc định (ONNX Runtime, nhẹ, không cần PaddlePaddle full). PaddleOCR là option nâng cao. Inpainting: **STTN + LaMa** (MIT) cho removal; **ProPainter KHÔNG dùng** (NTU S-Lab **non-commercial** license) — chỉ tham khảo kiến trúc.

---

# 8. AI Architecture

## 8.1 AI Engine tổng thể (Hybrid)

```text
                    AI ENGINE (Python worker)
                           │
              ┌────────────┴────────────┐
              │                         │
           LOCAL                      CLOUD
              │                         │
        Whisper (faster-whisper)    Translation (LLM)
        OCR (RapidOCR)              TTS (ElevenLabs/Azure)
        Separation (Demucs)         LLM (optional)
        FFmpeg / whisper.cpp
```

User chọn trong Settings: **Cloud / Local / Hybrid** (mặc định Hybrid).

## 8.2 STT Design (Local)

- Input: audio 16 kHz mono WAV.
- Pipeline: Silero VAD → segment → faster-whisper (batch, int8_float16) → (optional) WhisperX align → (optional) pyannote diarization → JSON. **Device strategy:** NVIDIA/CPU → faster-whisper; AMD/Intel → whisper.cpp (Vulkan) kèm **3 mitigation bắt buộc** (xem [14.2](#142-strategy-matrix)); CPU → int8.
- Output schema (xem 24).
- Model download: từ HuggingFace Hub lần đầu (có progress), cache trong `models/` thư mục user-data. Offline: hỗ trợ import model file.
- Long audio: không load toàn bộ **audio** vào RAM; dùng streaming batch theo VAD segments.

## 8.3 Context Understanding

Context Engine thu thập và nén context trước khi gọi LLM:

```text
Scene Context (từ scene detection qua ffprobe/scenedetect — Post-MVP, KHÔNG cam kết MVP)
Conversation Context (N trước + N sau)
Speaker Context (bản đồ speaker → vai trò/tên — user có thể đặt tên)
Terminology (glossary)
Translation Rules (style/formality/register)
```

## 8.4 Translation Engine (Contextual)

```text
Segments (có speaker + time + prev/next)
      +
Glossary + Character dict
      +
Translation rules
      ↓
Chunking (block ~5-10 subtitles, có overlap context 2)
      ↓
LLM (cloud hoặc local) → structured JSON output
      ↓
Validation (schema, count, timing giữ nguyên)
      ↓
Quality check (missing text? quá dài? hallucination?)
      ↓
Retry / Repair (tối đa N lần, backoff)
      ↓
Final translation.json
```

### 8.4.1 Chunking & Context window (đã kiểm chứng 2026)

- Dịch theo **block 5–10 subtitle** / 1 request (giảm số request ~5–10×, chất lượng tốt hơn dịch từng dòng).
- **Overlap:** gửi kèm 2 block trước làm read-only context để giữ continuity (speaker, terminology, pronoun).
- Chunk theo semantic boundary (hội thoại, scene) nếu có.
- Token budget: giữ prompt < 70% context window (tránh "lost in the middle").

### 8.4.2 Translation Memory (TM)

- Lưu cặp `(source_hash, target_text, glossary_version, model)` trong DB.
- Nếu source trùng → dùng lại translation (tiết kiệm $).
- Fuzzy match (optional, Post-MVP): levenshtein threshold.

### 8.4.3 Glossary & Character Dictionary

- User có thể nhập/import glossary: `source_term → preferred_translation`.
- Character dictionary: `speaker_id → character name`, `character → tone/register`.
- Glossary được inject vào prompt mỗi block.

### 8.4.4 Structured Output

- Dùng provider-native structured output khi có (OpenAI JSON mode, Gemini responseSchema, Claude tool use); fallback JSON parsing + repair.
- Schema versioned (xem 24).

### 8.4.5 Hallucination & Quality

- Checks: số block trả về = số gửi đi; không bỏ trống; không tự thêm nội dung; dịch ra đúng target language (ngôn ngữ detect).
- Quality score: LLM-as-judge (optional, tốn thêm token — bật ở preset High/Maximum).
- Quá dài (vượt chars-per-second) → rewrite ngắn lại.

## 8.5 Giảm chi phí API

- Block batching (5–10 sub/call).
- Translation memory (skip trùng).
- **Prompt caching** (đầu prompt tĩnh: glossary, rules, system) — theo cơ chế từng provider (Anthropic `cache_control`, OpenAI auto prefix, Gemini context caching).
- Preset "Fast" dùng model rẻ; "High/Maximum" dùng model chất lượng.
- Local fallback miễn phí khi đủ khả năng phần cứng.
- Retry backoff tránh tốn token vô ích.

## 8.6 Provider Abstraction

```python
# Giao diện (kiến trúc, không phải code triển khai)
class TranslationProvider(Protocol):
    name: str
    def translate_block(block, context) -> TranslatedBlock: ...

class STTProvider(Protocol):
    def transcribe(audio_path, options) -> Transcript: ...

class TTSProvider(Protocol): ...
class OCRProvider(Protocol): ...
class SeparationProvider(Protocol): ...
```

- Cloud: **Gemini (MVP default)**; "OpenAI-compatible custom" (để dùng bất kỳ server nào). OpenAI/Anthropic/DeepSeek = V1+ (FIX #7).
- Local: llama.cpp (OpenAI-compatible `/v1/chat/completions`).
- MockProvider: cho test không cần mạng.

---

# 9. Video Architecture

## 9.1 Video Engine (FFmpeg)

- **Probe:** `ffprobe -print_format json` để lấy metadata (streams, codec, duration, fps, rotation, bitrate, language).
- **Extract audio:** `ffmpeg -i in -vn -ac 1 -ar 16000 -c:a pcm_s16le out.wav` (hoặc pipe).
- **Render (burn-in):** dùng **libass** để render ASS với styling phức tạp (font, stroke, shadow, position, karaoke). Cách gọi an toàn bằng argument array (không shell).
- **Encoder selection:** tự detect qua GPU abstraction → NVENC (h264_nvenc/hevc_nvenc/av1_nvenc) → QSV (h264_qsv) → AMF (h264_amf) → CPU (libx264/libx265/libsvtav1). CRF/bitrate theo preset.

## 9.2 Render Engine

- **Preserve:** resolution, FPS, aspect ratio (anamorphic → SAR), color metadata nếu có.
- **Passes:**
  1. Nếu cần watermark → filter_complex `drawtext`/`overlay`.
  2. Burn subtitle: `ass` filter (hoặc `subtitles` filter với SRT).
  3. Encode theo preset + hardware.
- **Temporary files:** encode thẳng ra file tạm trong temp dir project, rename atomic khi xong.
- **Progress/ETA:** parse `-progress pipe:1` (out_time, speed) → tính %. Cancel: kill FFmpeg process gracefully, dọn temp.
- **Pause/Resume:** FFmpeg không support pause tự nhiên → MVP: cho phép cancel + resume lại từ cache stage (không phải từ giữa file encode). Đánh dấu Post-MVP cho segment-based resume thật.
- **Error recovery:** lỗi codec/hw → tự động fallback sang encoder khác (NVENC fail → libx264).

## 9.3 Xử lý video dài (không ngốn RAM)

- Không decode toàn bộ. FFmpeg stream; mỗi filter xử lý theo khung hình.
- Render: encode 1 pass trực tiếp từ input → output (không intermediate full-frame trong RAM).
- STT/OCR: xử lý theo chunk VAD / scene, decode từng đoạn, free memory.
- Disk: temp files cấu hình thư mục; dọn sau job.

---

# 10. Audio Architecture

## 10.1 Audio Pipeline MVP

```text
Video
 → extract audio (16k mono WAV, temp)
 → STT
```

## 10.2 Post-MVP: Original Audio Separation (thiết kế)

```text
Original Audio
   ↓
Demucs (htdemucs_ft) 4-stem: vocals/music/sfx/ambient
   ↓
Remove/reduce original voice (giữ music + sfx)
   ↓
Mix với TTS dubbing mới
```

- **Lựa chọn:** Demucs v4 `htdemucs_ft` (MIT, SDR 9.0–9.2 dB). UVR (MDX-Net) như model bổ sung qua `audio-separator` (MIT) — không dùng GUI UVR.
- GPU: ~6–8 GB VRAM (htdemucs). CPU fallback: chạy chậm (~5–10× real-time) nhưng OK cho video ngắn. Cơ chế fallback tự động nếu không GPU.
- **TODO — VERIFY:** VRAM chính xác của htdemucs_ft khi implement.

## 10.3 Audio Timing / Alignment (quan trọng cho dubbing — Post-MVP, thiết kế trước)

Nếu câu gốc `10.0s → 13.2s` nhưng TTS tạo `10.0s → 15.1s`:

1. **Compute target duration** = subtitle duration (source hoặc user-adjust).
2. **TTS parameter tuning:** `rate` (độ chậm/nhanh) — điều chỉnh trước (ít méo).
3. **Time-stretching:** nếu lệch nhỏ (<30%), dùng **Rubber Band** (`rubberband` filter, chất lượng WSOLA) hoặc FFmpeg `atempo` (giữ pitch). Giới hạn ±25% để tránh méo.
4. **Pause adjustment:** chèn/tăng gap tại dấu câu thay vì kéo dài phát âm.
5. **Sentence splitting:** nếu quá dài so với slot → tách thành 2 sub + điều chỉnh timing cả 2.
6. **Subtitle timing alignment:** dịch start/end theo audio thực tế sau khi stretch (audio-locked subtitles).
7. **Overlap prevention:** không để 2 đoạn dubbing chồng nhau; chèn silence gap tối thiểu.

**Ràng buộc:** không để giọng méo/quá nhanh → giới hạn rate 0.8–1.2×, stretch ±25%; nếu vượt → tách sub.

---

# 11. Subtitle Architecture

## 11.1 Subtitle Engine

**Input:** `translation.json` (đã có timing source + text đích).
**Xử lý:**
- Group segments → cues (tự động merge các đoạn liền nhau cùng speaker nếu cần).
- **Line breaking:** chia theo `max_chars_per_line` — giá trị **configurable theo ngôn ngữ/font/display width** (VD tiếng Việt ~42, nhưng KHÔNG phải rule universal hard-coded; phụ thuộc font, kích thước, safe area, độ dài từ), ưu tiên ngắt tại từ/dấu câu; không ngắt giữa cụm từ. Policy = hàm theo (ngôn ngữ, font metrics, vị trí, style) — xem TASKS.md TASK-024.
- **Reading speed:** kiểm tra chars-per-second (CPS) ≤ ngưỡng (mặc định ~15–20 CPS cho tiếng Việt); nếu vượt → tăng duration (kéo dài thời gian hiển thị) hoặc cảnh báo.
- **Timing:** dùng source timing (đã align); padding start/end tối thiểu 50–80 ms.
- **Style:** safe area, position (default bottom-center), font, size, stroke, shadow, background box → sinh **ASS** style.
- **Output:** ASS (mặc định cho render), SRT/VTT (export). Embedded subtitle track trong MKV/MP4 (optional).

## 11.2 Subtitle Detection / OCR (Post-MVP removal; thiết kế trước)

### Case A — Subtitle là track riêng:
- FFprobe phát hiện subtitle streams → nếu là bản gốc cần thay → **không mux** track đó vào output (hoặc mux track mới). Không cần OCR.

### Case B — Subtitle burn-in vào hình ảnh:
```text
Video Frame
 → Subtitle Detection (RapidOCR detect, tìm vùng text bền vững theo thời gian)
 → OCR (RapidOCR)
 → Bounding Box (khu vực subtitle ổn định)
 → Mask
 → Inpainting (STTN temporal / LaMa single-frame)
 → Clean Frame
```
- **Temporal consistency:** phát hiện subtitle vì chúng xuất hiện/xuất hiện theo chu kỳ; mask ổn định theo track.
- **Long video:** chia subvideo chunks (VD: 40–80 frames, có overlap), xử lý từng chunk, free VRAM; `fp16`; resize nếu cần.
- **GPU:** inpainting nặng → ưu tiên GPU; CPU fallback = STTN (ONNX) với quality thấp hơn.
- **Fallback:** nếu OCR fail/sai → để user khoanh vùng subtitle band thủ công.

---

# 12. Translation Architecture

## 12.1 Context Engine

```text
MVP context:
Conversation Context (speaker + text của block trước/sau)
Speaker Context      (speaker_id → name/register — chỉ khi có diarization metadata)
Terminology          (glossary entries khớp với block)
Translation Rules    (style: formal/informal, genre, note)
      ↓
Compiled Context (đổ vào prompt system + few-shot)

V1+ (KHÔNG nằm trong MVP):
Scene Context        (scene index, loại cảnh: hội thoại/hành động — cần scene detection)
Character/Affective  (mối quan hệ nhân vật, tone/emotion — nghiên cứu, chưa cam kết MVP)
```

## 12.2 Prompt Architecture (template, sẽ refine ở Phase 5)

```
[SYSTEM]
Bạn là chuyên gia dịch phụ đề {source} → {target} cho {genre}.
RULES: {translation_rules}
GLOSSARY: {glossary}
CHARACTERS: {character_dict}

[USER]
Đây là một block phụ đề. Mỗi dòng có format: [idx] speaker | start-end | text
Hãy dịch TẤT CẢ dòng sang {target}. Giữ nguyên số dòng, giữ nguyên [idx].
Output JSON: {"translations":[{"idx":N,"text":"..."}]}

Context trước: {prev_block}
Dòng cần dịch:
[idx=1] ...
...
```

## 12.3 Cost/Quality routing (preset)

| Preset | Model (default — MVP) | Chunk | Overlap | QC | Retry |
|---|---|---|---|---|---|
| Fast | gemini-2.5-flash-lite | 10 | 2 | Basic | 1 |
| Balanced | gemini-2.5-flash-lite | 8 | 2 | Basic | 2 |
| High Quality | gemini-2.5-flash | 6 | 3 | Full + judge | 2 |
| Maximum | gemini-2.5-flash | 5 | 3 | Full + judge | 3 |

> **MVP provider strategy (FROZEN):** MVP chỉ implement `GeminiProvider` + `LocalLLMProvider` (+ MockProvider cho test). OpenAI / Anthropic / DeepSeek là **V1+** — provider abstraction giữ nguyên interface, chỉ thêm sau (xem [7.6 Translation provider](#76-translation-provider) và `ARCHITECTURE_DECISION.md` §3.1).

---

# 13. TTS Architecture

## 13.1 TTS Provider (Post-MVP — thiết kế trước, không implement MVP)

```text
TTSProvider
 ├── CloudProvider (ElevenLabs / Azure / OpenAI TTS / Google)
 ├── LocalProvider (Kokoro / Chatterbox / Piper)
 └── MockProvider (test)
```

- **Speaker mapping:** `speaker_01 → Vietnamese Female Voice A`, `speaker_02 → Vietnamese Male Voice B`.
- Voice preview, rate/pitch/volume control, pause, timing.
- Voice cloning chỉ khi hợp pháp + có consent; UI phải có checkbox consent + disclaimer.
- **VERIFIED 2026-08-09:** local TTS tiếng Việt chưa có lựa chọn thương mại an toàn (Kokoro gốc không hỗ trợ vi; Kokoro-VN community license mơ hồ; Chatterbox v3 tiếng Việt CER ~75%; Viterbox CC-BY-NC). → Chiến lược: **cloud TTS mặc định cho tiếng Việt** + Piper (verify license) làm fallback. Re-verify tại thời điểm implement.

---

# 14. GPU Architecture

## 14.1 GPU Abstraction Layer

Detect và chọn strategy:

```text
HardwareProbe (Rust, chạy 1 lần khi khởi động + có thể re-run)
 ├── GPU vendor (NVIDIA/AMD/Intel) + model + VRAM
 │      ├── NVIDIA: nvidia-smi / NVML (VRAM, CUDA version, driver)
 │      └── AMD/Intel: WMI / dxdiag (model, VRAM), driver version
 ├── CUDA availability (PyTorch detect)
 ├── FFmpeg capabilities (chạy `ffmpeg -encoders` grep nvenc/qsv/amf)
 └── RAM total
      ↓
Strategy: {device: cuda/cpu, compute_type, encoder, chunk_size, batch}
```

## 14.2 Strategy Matrix

| Hardware | STT | Inpaint (Post-MVP) | Separation (Post-MVP) | Encoder |
|---|---|---|---|---|
| NVIDIA CUDA | faster-whisper int8 | STTN CUDA | Demucs CUDA | h264_nvenc |
| Intel iGPU (QSV) | whisper.cpp Vulkan/CPU | CPU | CPU | h264_qsv |
| AMD GPU | whisper.cpp Vulkan | CPU/DirectML(optional) | CPU | h264_amf |
| CPU-only | whisper.cpp CPU | STTN CPU | Demucs CPU (chậm) | libx264 |

- **whisper.cpp Vulkan — 3 mitigation bắt buộc (VERIFIED 2026-08-09):**
  1. `beam_size ≤ 6` (tránh crash AMD Radeon 780M + VAD — issue #3723).
  2. `flash_attn = False` khi device = AMD/Intel Vulkan (tránh crash AMD RDNA4 — issue #3806); chỉ bật trên NVIDIA CUDA.
  3. **Model init single-threaded** (semaphore trong STT service — tránh race #3638); nếu build static lib → gọi `ggml_backend_vk_reg()` thủ công sau khi init instance (#3750).
- **Vulkan = compatibility enhancement, KHÔNG phải blocker:** nếu Vulkan init thất bại (driver/binary cũ) → **tự fallback CPU** với log + user-visible warning + khuyên dùng driver mới; app không được crash vì lý do này. Luôn có safe defaults (CPU int8) và `Device override` trong Settings.
- **VRAM guard:** ước tính VRAM cần cho từng model; nếu < yêu cầu → tự hạ model/batch/segment size hoặc xuống CPU. Nếu OOM xảy ra → bắt exception → fallback + thông báo.
- **Driver:** log driver version; cảnh báo nếu thiếu driver mới (lỗi CUDA common).
- User có thể override device trong Settings (Auto / CUDA / CPU).

---

# 15. Desktop Architecture

## 15.1 Tauri 2 + React + TypeScript

- **Rust core:** commands IPC, sidecar lifecycle (spawn/health-check/restart/kill), SQLite (rusqlite), secret storage (**Windows Credential Manager** — không dùng tauri-plugin-stronghold, không custom crypto), settings store (tauri-plugin-store / JSON), dialog, fs (scoped), updater.
- **Frontend:** React + TS + Vite + Tailwind + shadcn/ui. State: Zustand (UI) + TanStack Query (server state / job polling) + WebSocket events (progress realtime).
- **IPC:** `invoke` cho commands; events `job:progress`, `job:log`, `job:status` từ Rust→Frontend.
- **Security:** CSP strict; chỉ dùng whitelist capabilities; không enable `dangerous*` không cần.

## 15.2 Lifecycle Python Worker

```text
App start
 → Rust kiểm tra worker binary (đã bundle)
 → spawn worker (python-wrapped exe) với args: --port random
 → worker mở HTTP + WebSocket trên localhost:port (bind 127.0.0.1)
 → Rust poll /health (retry 10x) → ready
 → nếu crash → Rust restart (max 3 lần) + log
App exit
 → gửi graceful shutdown → kill nếu timeout
```

- Port: random trong range, không hard-code (tránh conflict + bảo mật).
- Auth giữa Rust ↔ worker: token 256-bit ngẫu nhiên sinh mỗi session, truyền qua **stdin** (worker in `READY <token>` sau khi bind, Rust đọc); header `Authorization: Bearer <token>`; so sánh constant-time. Không qua argv/env public (dễ log).

## 15.3 So sánh các Option đã yêu cầu

| Option | Đánh giá | Kết luận |
|---|---|---|
| **A. Tauri + React + Python** | RAM thấp, bundle nhỏ, security tốt, sidecar proven | ✅ **CHỌN** |
| B. Electron + React + Python | Dev nhanh, ecosystem lớn, nhưng RAM/bundle cao | Fallback |
| C. Native Windows + C#/.NET | Hiệu năng cao, nhưng AI ecosystem Python phải qua subprocess, UI native tốn công | Không chọn |
| D. Khác (pywebview, Wails, Neutralino) | pywebview: WebView2 + Python-only (đơn giản nhưng ít kiểm soát native/updater); Wails: Go không lợi thế AI; Neutralino: non-mature | Không chọn |

---

# 16. Backend Architecture

## 16.1 MVP — Không có cloud backend

- Toàn bộ local. "Backend" = Rust core + Python worker (localhost).
- Không cần POST/GET API công khai ở MVP.

## 16.2 Worker API (localhost) — thiết kế REST/WebSocket

```
(Danh sách endpoint chuẩn — **single source of truth** → xem [25.3 Worker HTTP](#253-worker-http-rust-python).)

- `/health` dùng **GET** (không POST).
- Naming endpoint theo 25.3: `media/probe`, `audio/extract`, `stt/transcribe`, `translation/translate`, `subtitle/generate`, `render`, `jobs/{id}/cancel`, WS `/v1/events`.
```

- Auth: header `Authorization: Bearer <session_token>` (token do Rust sinh).
- Error format chuẩn (xem 28).
- **Fallback (nếu complexity):** MVP có thể dùng request-response đơn giản + polling thay vì WebSocket (lớp Rust dùng `invoke` chờ kết quả, progress qua polling 500 ms). Quyết định ở Phase 2 — ưu tiên polling cho MVP để giảm complexity.

---

# 17. Database Architecture

## 17.1 SQLite (WAL), schema versioned (migrations)

### Bảng core

```sql
-- projects
CREATE TABLE projects (
  id TEXT PRIMARY KEY,           -- uuid
  name TEXT NOT NULL,
  source_video_path TEXT NOT NULL,
  status TEXT NOT NULL,          -- draft/analyzed/transcribed/translated/rendered
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  settings_json TEXT             -- project-level overrides
);

-- media_metadata (1:1 project)
CREATE TABLE media_metadata (
  project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
  duration REAL, width INT, height INT, fps REAL,
  codec TEXT, bitrate INT, rotation INT,
  audio_streams_json TEXT, subtitle_streams_json TEXT,
  format TEXT, aspect_ratio TEXT
);

-- transcript_segments (STT output, per project)
CREATE TABLE transcript_segments (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  idx INT NOT NULL,                 -- thứ tự
  speaker TEXT,                     -- speaker_00...
  start REAL NOT NULL,
  end REAL NOT NULL,
  source_text TEXT NOT NULL,
  source_lang TEXT,
  confidence REAL,
  word_timings_json TEXT,           -- optional
  UNIQUE(project_id, idx)
);

-- translations (1:1 với segment, versioned)
CREATE TABLE translations (
  id TEXT PRIMARY KEY,
  segment_id TEXT NOT NULL REFERENCES transcript_segments(id) ON DELETE CASCADE,
  target_text TEXT NOT NULL,
  target_lang TEXT NOT NULL,
  glossary_version INT,
  model TEXT,
  status TEXT DEFAULT 'draft',     -- draft/translated/edited/approved
  qc_passed INT, qc_notes TEXT,
  created_at TEXT, updated_at TEXT
);

-- subtitle_cues (kết quả SubtitleEngine)
CREATE TABLE subtitle_cues (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  start REAL, end REAL,
  text TEXT,                        -- text đích
  style_json TEXT,
  cue_number INT,
  UNIQUE(project_id, cue_number)
);

-- jobs
CREATE TABLE jobs (
  id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
  type TEXT NOT NULL,               -- analyze/transcribe/translate/generate_subtitle/render/export
  status TEXT NOT NULL,             -- queued/running/completed/failed/cancelled
  progress REAL DEFAULT 0,
  stage TEXT,                       -- sub-stage label
  error_code TEXT, error_message TEXT, error_log TEXT,
  params_json TEXT,
  created_at TEXT, updated_at TEXT, started_at TEXT, finished_at TEXT
);

-- glossary
CREATE TABLE glossary_terms (
  id TEXT PRIMARY KEY,
  source_term TEXT NOT NULL, target_term TEXT NOT NULL,
  lang TEXT, project_id TEXT,        -- NULL = global
  note TEXT,
  UNIQUE(lang, source_term, project_id)
);

-- character_dict
CREATE TABLE characters (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  speaker TEXT NOT NULL,             -- speaker_01
  name TEXT, gender TEXT, role TEXT, register TEXT,
  UNIQUE(project_id, speaker)
);

-- settings (app-level) — bảng key-value, JSON
CREATE TABLE app_settings (
  key TEXT PRIMARY KEY, value_json TEXT
);

-- provider_config (secrets KHÔNG lưu ở đây — chỉ non-secret)
CREATE TABLE provider_config (
  provider TEXT PRIMARY KEY,        -- translation_openai...
  active INT,
  config_json TEXT                  -- model, base_url, params (KHÔNG key)
);
```

**Lưu ý bảo mật:** API keys KHÔNG lưu trong SQLite. Lưu trong Windows Credential Manager (xem 20).

## 17.2 Cache bảng (xem 19)

---

# 18. Job System

## 18.1 Khái niệm

Job pipeline trong Rust core; mỗi job là một đơn vị công việc với dependency.

```text
IMPORT → ANALYZE → EXTRACT_AUDIO → TRANSCRIBE → TRANSLATE → GENERATE_SUBTITLE → RENDER → EXPORT
```

## 18.2 Trạng thái & máy trạng thái

```text
queued → running → completed
                  → failed
                  → cancelled
```

- **Retry:** tự động (transient lỗi như API timeout) với backoff (1s, 5s, 30s, tối đa 3 lần); manual retry cho lỗi vĩnh viễn.
- **Progress:** 0–1; stage phụ (VD: translate 30/120 blocks).
- **Logs:** gắn job_id, structured JSON, lưu DB + file log.
- **Cancellation:** job đang chạy → gửi cancel flag qua API; FFmpeg kill; dọn temp.
- **Dependency:** job chỉ chạy khi dependency hoàn thành (queue check).
- **Cache:** trước khi chạy, kiểm tra cache (xem 19) → nếu hit, jump straight to completed.
- **Persistence:** job status lưu DB → app restart vẫn biết trạng thái, có thể resume.

## 18.3 Concurrency

- **MVP:** 1 job nặng tại một thời điểm (queue FIFO), để tránh OOM và tránh tranh chấp GPU. UI có thể hiển thị queue.
- Post-MVP: cho phép chạy song song các job nhẹ (analyze, subtitle gen) trong khi job nặng (render) chạy, với resource guard.

---

# 19. Cache System

## 19.1 Nguyên tắc

> Đổi font subtitle → KHÔNG chạy lại STT. Sửa translation → KHÔNG chạy lại STT. Đổi subtitle style → CHỈ render lại.

## 19.2 Cache keys (content-addressed)

| Cache | Key | Store |
|---|---|---|
| STT | `stt:{sha256(audio_file)}:{model}:{compute_type}:{lang}:{vad}` | thư mục cache |
| Translation | `tr:{sha256(source_block_json)}:{target_lang}:{model}:{glossary_ver}:{rules_ver}` | DB + file JSON |
| TTS (Post-MVP) | `tts:{sha256(text)}:{voice}:{provider}:{params}` | thư mục cache |
| Render | `render:{sha256(video+subtitle_style+wm+encoder+preset)}` | thư mục cache |
| Audio extract | `audio:{sha256(video)}:{spec}` | thư mục cache |

- **Invalidation:** tăng version counter khi thay đổi tham số thuộc key; xóa cache project khi user chỉnh source/translation tay (chỉ invalidate downstream).
- **Storage:** trong thư mục `cache/` của project + `user-data/cache/` cho cache dùng chung (models không tính là cache — tách riêng `models/`).
- **Dọn dẹp:** LRU theo dung lượng (setting mặc định VD 10 GB), dọn khi tạo project mới hoặc job xong.
- Render cache: chỉ cache nếu đủ chỗ; nếu video lớn → không cache render (chỉ cache watermark/subtitle "composite layer" nếu khả thi — Post-MVP).

## 19.3 Ví dụ flow

```text
User đổi watermark:
 → check render cache: MISS (watermark trong key)
 → KHÔNG chạy STT/translation (hit)
 → chạy lại render từ video sạch + subtitle cached
```

---

# 20. Security

## 20.1 Secrets

- **API keys:** lưu trong **Windows Credential Manager** qua Rust crate `windows-credential-manager` hoặc `keyring`. Frontend chỉ nhận masked (VD `sk-****last4`); chỉ Rust đọc key thật và inject vào worker session (qua env/stdin, không qua UI).
- Không hard-code secret trong source / `.exe` / config commit.
- Env var override cho CI/local (không commit).
- **Fail-safe khi Credential Manager không khả dụng** (máy không có DPAPI/credential service): **KHÔNG** fallback bằng encrypted-file/custom crypto — chặn lưu key + thông báo rõ cho user cách khắc phục (bật Credential Manager / chạy với tài khoản hợp lệ). Luôn ưu tiên fail-an-toàn hơn lưu key thiếu an toàn.

## 20.2 Command injection (FFmpeg/shell)

- **KHÔNG BAO GIỜ** build câu lệnh shell string. Dùng argument array: `Command::new(ffmpeg).args([...])`.
- Validate input path (không chứa `;`, `|`, `&`, newline, NUL).
- Mọi path user cấp → normalize + kiểm tra nằm trong scoped dir khi cần.
- Worker API chỉ nhận đường dẫn từ Rust (đã validate), không nhận tùy ý từ renderer.

## 20.3 Path traversal

- Scoped fs (tauri-plugin-fs capabilities) — frontend không đọc/ghi file tùy ý.
- File cho user chỉ ở project dir / export dir user chọn.

## 20.4 Temp files & logs

- Temp files: ACL hạn chế (chỉ user hiện tại), xóa sau job; cấu hình location.
- **KHÔNG log:** API key, password, token, nội dung transcript nhạy cảm (log độ dài/hash thay vì text nếu cần debug).
- Log rotation + dung lượng giới hạn.

## 20.5 Network

- Worker chỉ bind `127.0.0.1` (không LAN/WAN) + session token.
- TLS không bắt buộc trên loopback; nhưng token + no-bind-external là bắt buộc.
- Gọi API cloud qua HTTPS; timeout + retry.

## 20.6 Process isolation

- Worker chạy process riêng (isolation crash).
- Code signing cho mọi binary phân phối (xem 34).

## 20.7 Privacy Mode

- Khi bật (mặc định): STT/FFmpeg/subtitle/render **hoàn toàn local** — không upload video; translation **cloud chỉ khi user explicit consent** (bật riêng), nếu có local model thì dùng local; xóa temp sau job; **không telemetry** trừ khi user đồng ý.
- Ở MVP **KHÔNG** bao gồm OCR/separation trong Privacy Mode (các thành phần đó là Post-MVP — không có sẵn ở MVP).

---

# 21. Licensing

> **QUY TẮC:** Không tự khẳng định license nếu chưa kiểm chứng. Bảng dưới là trạng thái đã kiểm chứng nghiên cứu 2026 — **phải re-verify tại thời điểm implement** với file LICENSE thật của từng repo/model.

| Dependency | License | Build config | Commercial | Redistribution | Notice | Source disclosure | Risk | Action |
|---|---|---|---|---|---|---|---|---|---|
| Tauri 2 | MIT/Apache-2.0 (dual) | Cargo dep | ✅ | ✅ | Không bắt buộc (apache nếu dùng) | Không yêu cầu cho MIT | WebView2 runtime (Microsoft, miễn phí) | Bundle WebView2 embedBootstrapper |
| React / ReactDOM | MIT | npm dep | ✅ | ✅ | Không bắt buộc | Không yêu cầu | — | — |
| Tailwind / shadcn/ui | MIT | npm dep | ✅ | ✅ | Không bắt buộc | Không yêu cầu | — | — |
| Rust crates | MIT/Apache-2.0 | Cargo.lock | ✅ | ✅ | Theo từng crate | Không yêu cầu (MIT) | License phức tạp nhiều crate | cargo-deny CI + whitelist |
| Python | PSF | PyInstaller bundle | ✅ | ✅ | — | Không yêu cầu | — | bundle python-build-standalone |
| faster-whisper | MIT | pip dep | ✅ | ✅ | — | Không yêu cầu | model weights license riêng | ghi attribution |
| Whisper models (OpenAI) | MIT | tải từ HF | ✅ | ✅ | Attribution khuyến khích | Không yêu cầu | — | ghi attribution |
| whisper.cpp | MIT | CMake build (Vulkan/CUDA) | ✅ | ✅ | — | Không yêu cầu | Vulkan crashes (3 mitigations) | dùng build riêng đã verify |
| WhisperX | BSD-2-Clause | pip dep | ✅ | ✅ | — | Không yêu cầu | — | — |
| wav2vec2 align models (fairseq) | Apache-2.0 | HF weights | ✅ | ✅ | — | Không yêu cầu | — | — |
| pyannote-audio + community-1 | MIT (code) / CC-BY-4.0 (model, gated) | pip dep + HF token | ✅ (code) / ⚠️ model | ✅ | Bắt buộc attribution model | Không yêu cầu | model gated + license riêng | user accept agreement + attribution |
| Silero VAD | MIT | HF weights | ✅ | ✅ | — | Không yêu cầu | — | — |
| llama.cpp | MIT | CMake build | ✅ | ✅ | — | Không yêu cầu | — | — |
| Qwen GGUF (Alibaba) | Apache-2.0 | tải từ HF | ✅ | ✅ | — | Không yêu cầu | model card | verify model card |
| FFmpeg | LGPL/GPL (tùy build) | Build static LGPL-safe | ✅ | ✅ | Bắt buộc theo LGPL | LGPL yêu cầu (cho phép dynamic + cung cấp object) | build phải cẩn thận libass/x264 | dùng build đã verify + bảng license |
| libass | ISC | ffmpeg build dep | ✅ | ✅ | — | Không yêu cầu | — | — |
| Demucs + htdemucs_ft | MIT (code + weights) | pip dep (Post-MVP) | ✅ | ✅ | Attribution | Không yêu cầu | weights license | verify weights |
| RapidOCR / PaddleOCR | Apache-2.0 | pip dep (Post-MVP) | ✅ | ✅ | — | Không yêu cầu | PP-OCR weights Apache | — |
| Kokoro (gốc) | Apache-2.0 | pip dep (Post-MVP) | ✅ | ✅ | — | Không yêu cầu | không có giọng tiếng Việt | — |
| **Kokoro-Vietnamese (community fine-tune)** | **Không rõ** (provenance viVoice CC-BY-NC-SA / LarVoice) | HF weights | ❌ (chưa chứng minh) | ⚠️ | ⚠️ | rủi ro thương mại cao | **`TODO — VERIFY BEFORE COMMERCIAL RELEASE`** | loại khỏi MVP |
| **Viterbox** (VN Chatterbox fine-tune) | **CC-BY-NC-4.0** | HF weights | ❌ | ❌ | ❌ | non-commercial | loại bỏ |
| Chatterbox v3 | MIT | pip dep (Post-MVP) | ✅ | ✅ | — | Không yêu cầu | CER ~75% tiếng Việt | dùng cho ngôn ngữ khác |
| Piper | MIT (cũ) / GPL-3.0 (fork OHF-Voice) | — (Post-MVP) | ✅ (GPL copyleft) | ✅ | Bắt buộc nếu GPL | **GPL yêu cầu disclosure nếu embed** | GPL khi embed closed-source | chỉ dùng nếu cần, kiểm tra từng giọng |
| **ProPainter** | **NTU S-Lab (non-commercial)** | — | ❌ | ❌ | — | — | **KHÔNG dùng trong sản phẩm** | loại bỏ |
| **XTTS v2** | **CPML (non-commercial)** | — | ❌ | ❌ | — | — | **KHÔNG dùng thương mại** | loại bỏ |
| **F5-TTS** | **CC-BY-NC-4.0 (weights)** | — | ❌ | ❌ | — | — | **KHÔNG dùng thương mại** | loại bỏ |
| ElevenLabs / Azure / OpenAI API | Commercial API (ToS) | SDK | ✅ (theo ToS) | N/A | Theo ToS | N/A | ToS, giá, voice consent | đọc kỹ ToS |
| GPU runtime (NVIDIA CUDA / AMD ROCm / Intel driver) | EULA driver | bundle rời / driver hệ thống | ✅ | ✅ | — | Không yêu cầu | driver tải từ hãng; không bundle CUDA toolkit | tải từ nguồn chính thức |
| GPU SDK runtime (Vulkan Loader) | Apache-2.0 (Khronos) | build dep | ✅ | ✅ | — | Không yêu cầu | — | — |

> **TODO — VERIFY BEFORE RELEASE:** re-check LICENSE file của mỗi dependency + model card tại thời điểm build release. FFmpeg build phải có bảng kê license (LGPL compliance).

---

# 22. Folder Structure

```text
ai-video-localization/
├── README.md
├── ARCHITECTURE.md
├── MASTER_PLAN.md
├── DEVELOPMENT.md
├── AI_PIPELINE.md
├── VIDEO_PIPELINE.md
├── AUDIO_PIPELINE.md
├── DATABASE.md
├── API.md
├── SECURITY.md
├── LICENSING.md
├── TESTING.md
├── RELEASE.md
├── .github/workflows/           # CI: test, lint, build, sign, release
│
├── src-tauri/                   # Tauri Rust core
│   ├── src/
│   │   ├── main.rs
│   │   ├── lib.rs
│   │   ├── commands/            # IPC commands (project, job, settings, media)
│   │   │   ├── mod.rs
│   │   │   ├── project.rs
│   │   │   ├── job.rs
│   │   │   ├── settings.rs
│   │   │   ├── media.rs
│   │   │   └── system.rs
│   │   ├── services/            # business logic (mỏng)
│   │   │   ├── mod.rs
│   │   │   ├── project_service.rs
│   │   │   ├── job_service.rs
│   │   │   ├── cache_service.rs
│   │   │   ├── worker_client.rs     # gọi Python worker (HTTP/WS)
│   │   │   ├── worker_manager.rs    # lifecycle sidecar
│   │   │   ├── settings_service.rs
│   │   │   └── hardware_probe.rs
│   │   ├── db/
│   │   │   ├── mod.rs
│   │   │   ├── migrations.rs
│   │   │   └── repo/*.rs
│   │   ├── security/
│   │   │   ├── mod.rs
│   │   │   └── secret_store.rs      # Windows Credential Manager
│   │   └── error.rs
│   ├── capabilities/default.json
│   ├── tauri.conf.json
│   ├── Cargo.toml
│   └── icons/
│
├── worker/                       # Python AI worker
│   ├── pyproject.toml
│   ├── requirements.txt          # chia: base / cpu / gpu / dev
│   ├── src/
│   │   ├── __init__.py
│   │   ├── main.py               # FastAPI app + lifecycle
│   │   ├── api/
│   │   │   ├── routes.py
│   │   │   ├── schemas.py        # pydantic models (chia sẻ với Rust schema)
│   │   │   └── ws.py
│   │   ├── services/
│   │   │   ├── media_service.py      # ffprobe
│   │   │   ├── audio_service.py      # ffmpeg extract
│   │   │   ├── stt_service.py        # faster-whisper / whisper.cpp
│   │   │   ├── diarization_service.py
│   │   │   ├── translation_service.py
│   │   │   ├── providers/
│   │   │   │   ├── base.py
│   │   │   │   ├── translation/
│   │   │   │   │   ├── openai_provider.py
│   │   │   │   │   ├── gemini_provider.py
│   │   │   │   │   ├── anthropic_provider.py
│   │   │   │   │   ├── local_llm_provider.py   # llama.cpp OpenAI-compatible
│   │   │   │   │   └── mock_provider.py
│   │   │   │   ├── tts/            # Post-MVP
│   │   │   │   └── ocr/            # Post-MVP
│   │   │   ├── subtitle_service.py
│   │   │   ├── render_service.py
│   │   │   ├── context_service.py
│   │   │   ├── quality_service.py
│   │   │   ├── cache.py
│   │   │   └── hardware.py         # torch device detect
│   │   ├── core/
│   │   │   ├── job.py              # worker-side job executor
│   │   │   ├── ffmpeg.py           # safe arg builder
│   │   │   └── logging.py
│   │   └── utils/
│   ├── tests/
│   │   ├── unit/
│   │   ├── integration/
│   │   ├── fixtures/
│   │   └── bench/
│   └── scripts/
│       ├── build_win.ps1           # PyInstaller onedir
│       └── download_models.py
│
├── src/                          # Frontend (React)
│   ├── main.tsx
│   ├── App.tsx
│   ├── api/
│   │   ├── bridge.ts             # typed invoke wrapper
│   │   └── events.ts
│   ├── stores/
│   │   ├── project.ts
│   │   ├── job.ts
│   │   └── settings.ts
│   ├── pages/
│   │   ├── Dashboard/
│   │   ├── Project/
│   │   │   ├── ImportView.tsx
│   │   │   ├── AnalyzeView.tsx
│   │   │   ├── TranscriptView.tsx
│   │   │   ├── TranslateView.tsx
│   │   │   ├── SubtitleEditorView.tsx
│   │   │   ├── PreviewView.tsx
│   │   │   └── ExportView.tsx
│   │   ├── Settings/
│   │   │   ├── AISettings.tsx
│   │   │   ├── GPUSettings.tsx
│   │   │   └── APISettings.tsx
│   │   └── LogsView.tsx
│   ├── components/               # shared UI
│   │   ├── ui/                   # shadcn
│   │   ├── JobProgress.tsx
│   │   ├── VideoPreview.tsx
│   │   ├── Timeline.tsx
│   │   └── ErrorBoundary.tsx
│   ├── types/                    # chia sẻ schema với Rust/Python
│   │   └── api.ts
│   ├── styles/
│   └── tests/
│
├── schemas/                      # JSON schema versioned (single source of truth)
│   ├── transcript.schema.json
│   ├── translation.schema.json
│   ├── subtitle.schema.json
│   ├── job.schema.json
│   └── api.schema.json
│
├── scripts/
│   ├── build_sidecar.ps1
│   ├── fetch_ffmpeg.ps1
│   └── verify_release.ps1
│
└── vendor/                       # binaries downloaded (không commit)
    ├── ffmpeg/
    └── models/  (gitignored)
```

---

# 23. Module Responsibilities

| Module | Purpose | Input | Output | Dependencies | Public Interface | Failure Modes | Test Strategy |
|---|---|---|---|---|---|---|---|
| **ProjectService** | CRUD project, mở/đóng project | video path, project id | Project, dirs | DB | `create/load/save/delete` | path không hợp lệ, dir bận | unit + integration |
| **MediaProbeService** | ffprobe → metadata | video path | MediaMetadata | ffprobe | `probe(path)` | file hỏng, codec lạ | unit (fixtures) |
| **AudioService** | extract audio | video path, spec | wav path | ffmpeg | `extract()` | audio không có | unit + integration |
| **STTService** | transcribe | wav, options | Transcript JSON | faster-whisper/whisper.cpp | `transcribe()` | OOM, model lỗi | integration + fixtures |
| **DiarizationService** | assign speaker | wav, transcript | segments có speaker | pyannote | `diarize()` | no HF token, model gated | integration (optional) |
| **ContextService** | build context | segments, glossary, chars | context | DB | `build(block)` | — | unit |
| **TranslationService** | dịch block | segments, context, target | translations | providers | `translate()` | API fail, invalid JSON | unit (mock) + integration |
| **SubtitleService** | sinh cues + style | translations, config | SubtitleDoc (ASS/SRT) | — | `generate()` | timing lỗi, quá dài | unit |
| **RenderService** | burn-in + encode | video, subtitle, wm, preset | output video | ffmpeg | `render()` | hw encode fail, disk full | integration + golden |
| **JobService** | queue, state, retry, cancel | job spec | job status | DB, services | `submit/status/cancel/retry` | — | unit + integration |
| **CacheService** | cache get/set/invalidate | key, payload | cache hit/miss | filesystem, DB | `get/set/invalidate` | disk full | unit |
| **SettingsService** | đọc/ghi settings | — | settings | DB, secret store | `get/set` | — | unit |
| **WorkerManager** | lifecycle sidecar | config | running worker | — | `start/stop/health/restart` | crash loop, port conflict | integration |
| **HardwareProbe** | detect GPU/CPU/ffmpeg caps | — | HardwareProfile | nvidia-smi/WMI/ffmpeg | `probe()` | driver thiếu | integration |

---

# 24. Data Schemas

## 24.1 Transcript segment (output STT)

```json
{
  "schema_version": 1,
  "project_id": "proj_001",
  "language": "zh",
  "model": "faster-whisper:large-v3:int8",
  "segments": [
    {
      "id": "seg_0001",
      "idx": 0,
      "speaker": "speaker_01",
      "start": 10.25,
      "end": 13.72,
      "text": "你最近怎么样？",
      "language": "zh",
      "confidence": 0.96,
      "words": [
        {"word": "你", "start": 10.26, "end": 10.35, "speaker": "speaker_01"}
      ]
    }
  ]
}
```

## 24.2 Translation (output LLM — per block & merged)

```json
{
  "schema_version": 1,
  "target_language": "vi",
  "model": "gemini-2.5-flash-lite",
  "blocks": [
    {
      "block_idx": 0,
      "translations": [
        {"idx": 0, "segment_id": "seg_0001", "source_text": "你最近怎么样？",
         "translated_text": "Dạo này cậu thế nào?", "confidence": 0.94}
      ]
    }
  ]
}
```

## 24.3 Subtitle document (ASS/SRT output)

```json
{
  "schema_version": 1,
  "project_id": "proj_001",
  "style": {"font": "Arial", "font_size": 44, "stroke": 2,
            "shadow": 1, "position": "bottom_center", "bg_box": false,
            "max_chars_per_line": 42, "max_cps": 18},
  "cues": [
    {"cue_number": 1, "start": 10.25, "end": 13.72,
     "text": "Dạo này cậu thế nào?"}
  ],
  "output": {"ass_path": "...", "srt_path": "..."}
}
```

## 24.4 Job

```json
{
  "id": "job_0123", "project_id": "proj_001", "type": "transcribe",
  "status": "running", "progress": 0.42, "stage": "transcribing",
  "error_code": null, "error_message": null,
  "params": {"model": "large-v3", "device": "cuda"},
  "created_at": "...", "started_at": "...", "finished_at": null
}
```

## 24.5 Settings

```json
{
  "language": {"source": "auto", "target": "vi"},
  "quality_preset": "balanced",
  "stt": {"model": "large-v3", "device": "auto"},
  "translation": {"provider": "gemini", "model": "gemini-2.5-flash-lite",
                  "chunk_size": 8, "context_overlap": 2},
  "render": {"encoder": "auto", "crf": 20, "preset": "p6",
             "hw_accel": "auto"},
  "privacy_mode": false,
  "cache": {"max_gb": 10}
}
```

## 24.6 Versioning

- Mỗi schema có `schema_version`; migrations nâng version; parser validate + chuyển đổi.

---

# 25. API Design

## 25.1 IPC (Frontend ↔ Rust) — Tauri commands

```
project.create(name, video_path) → Project
project.open(id) → Project + metadata
project.save(id) → void
project.delete(id) → void
media.probe(project_id) → MediaMetadata
job.submit(project_id, type, params) → Job
job.get(id) → Job
job.list(project_id) → Job[]
job.cancel(id) → void
job.retry(id) → void
subtitle.get_cues(project_id) → Cue[]
subtitle.update_cue(id, patch) → Cue
translation.get(project_id) → Translation[]
translation.update(segment_id, text) → void
translation.save_glossary(project_id, terms) → void
settings.get() → Settings
settings.update(patch) → void
settings.set_api_key(provider, key) → void   // Rust lưu credential manager
hardware.probe() → HardwareProfile
export.video(project_id, options) → Job
export.subtitles(project_id, format, path) → Path
```

## 25.2 Events (Rust → Frontend)

```
job:status   { jobId, status, progress, stage, error }
job:log      { jobId, level, message }
project:changed { projectId }
worker:health { ok, gpu }
```

## 25.3 Worker HTTP (Rust ↔ Python)

```
GET  /health
POST /v1/media/probe
POST /v1/audio/extract
POST /v1/stt/transcribe        (async job)
POST /v1/translation/translate (async job)
POST /v1/subtitle/generate
POST /v1/render
POST /v1/jobs/{id}/cancel
WS   /v1/events
```

- Auth header: `Authorization: Bearer <session_token>`.
- Response errors: `{"error": {"code": "E_FFMPEG_RENDER", "message": "...", "recoverable": true}}`.

---

# 26. UI/UX Architecture

## 26.1 Layout tổng thể

```text
┌─────────────────────────────────────────┐
│ AI VIDEO LOCALIZATION STUDIO      [menu]│
├─────────────┬───────────────────────────┤
│ Sidebar     │       Main Workspace      │
│  Project    │   (thay đổi theo view)    │
│  Transcript │                           │
│  Translate  │   Video Preview + Editor  │
│  Subtitle   │                           │
│  Export     │                           │
│  Settings   │                           │
├─────────────┴───────────────────────────┤
│ Job Progress Bar / Status / Logs (docked)│
└─────────────────────────────────────────┘
```

## 26.2 Các màn hình MVP

1. **Dashboard:** danh sách project, tạo/mở, recent.
2. **Project creation:** chọn video, name, target language.
3. **Import/Analyze:** hiển thị metadata + nút "Start pipeline".
4. **Transcript view:** bảng segments (time, speaker, source text, confidence), có thể sửa text nguồn.
5. **Translate view:** song song source ↔ translation, glossary panel, quality badge, nút retranslate segment/block.
6. **Subtitle editor:** bảng cues, chỉnh timing/text, style preview realtime.
7. **Preview:** video element + subtitle overlay (ASS preview qua libass? MVP: dùng `<video>` + caption overlay HTML gần đúng; render xem trước frame).
8. **Export:** preset, watermark, encoder, thư mục; progress + cancel.
9. **Settings:** AI, GPU, API keys (masked), cache, privacy.
10. **Logs:** bảng log filter theo level/job.

## 26.3 Timeline (tối giản MVP)

- Track: video, subtitle. Không cần complex timeline ở MVP — chỉ cần scrub bar + danh sách cues.

## 26.4 Error UI

- Toast + inline error, job failed banner kèm error code + "Retry" / "View logs".
- ErrorBoundary toàn app.

---

# 27. Processing Pipelines

## 27.1 Pipeline MVP đầy đủ

```text
1. User tạo project (chọn video)
2. [AUTO] analyze → metadata
3. [AUTO] extract_audio → wav (cache)
4. [AUTO] transcribe → transcript.json (cache) [STT LOCAL]
5. [AUTO/MANUAL] translate → translation.json (cache) [CLOUD hoặc LOCAL]
   - bắt buộc có context, glossary
6. [MANUAL REVIEW] user duyệt/sửa translation
7. [AUTO] generate_subtitle → cues + ASS/SRT (cache)
8. [MANUAL] user chỉnh style / watermark / preset
9. [AUTO] render → output video
10. [AUTO] quality check (ffprobe: duration, resolution, stream ok)
11. [MANUAL] export (di chuyển ra thư mục người dùng)
```

Mỗi bước: check cache → chạy → lưu DB + cache → cập nhật job status → notify.

## 27.2 Post-MVP pipeline đầy đủ

```text
... sau translate ...
 → OCR/burned-in removal (nếu cần)
 → audio separation (Demucs)
 → TTS dubbing theo speaker mapping
 → timing alignment
 → audio mixing (dub + music/sfx)
 → subtitle rendering
 → watermark
 → render
 → QC
```

---

# 28. Error Handling

## 28.1 Error taxonomy (mỗi lỗi có: Error Code, Cause, User Message, Technical Log, Recovery, Retry)

| Error Code | Cause | User Message (VI) | Recovery | Retry |
|---|---|---|---|---|
| `E_VIDEO_INVALID` | File không phải video / hỏng | "File video không hợp lệ hoặc bị hỏng." | Chọn file khác | — |
| `E_VIDEO_CORRUPTED` | Container lỗi, decode fail | "Video bị hỏng, không thể đọc." | Thử ffmpeg repair (`-err_detect ignore_err`) | 1 |
| `E_FFMPEG_NOT_FOUND` | Thiếu binary | "Không tìm thấy FFmpeg." | Reinstall | — |
| `E_FFMPEG_FAILED` | FFmpeg lỗi (đọc log) | "Lỗi khi xử lý video." + detail | Log, retry khác encoder | 1 |
| `E_GPU_UNAVAILABLE` | Không có GPU | "Không tìm thấy GPU tương thích." | Dùng CPU | — |
| `E_CUDA_ERROR` | CUDA/driver lỗi | "Lỗi CUDA. Kiểm tra driver." | Detect driver, fallback CPU | 1 |
| `E_OUT_OF_VRAM` | Thiếu VRAM | "Không đủ VRAM." | Hạ model/batch/segment, fallback CPU | tự |
| `E_OUT_OF_RAM` | Thiếu RAM | "Không đủ bộ nhớ." | Giảm parallelism, đóng app khác | — |
| `E_API_TIMEOUT` | API cloud timeout | "API quá chậm." | Tăng timeout, retry | 2 |
| `E_API_RATE_LIMIT` | Rate limit | "Vượt giới hạn API." | Backoff exponential, queue | 3 |
| `E_API_AUTH` | Key sai/hết hạn | "API key không hợp lệ." | Mở settings sửa key | — |
| `E_TTS_FAILED` | TTS lỗi (Post-MVP) | "Không tạo được giọng nói." | Đổi voice/provider | 2 |
| `E_TRANSLATION_FAILED` | LLM lỗi/parse fail | "Dịch thất bại cho block X." | Retry block, giữ bản cũ | 2 |
| `E_OCR_FAILED` | OCR lỗi (Post-MVP) | "Không nhận diện được phụ đề." | Thử engine khác | 1 |
| `E_DISK_FULL` | Hết dung lượng | "Đĩa không đủ dung lượng." | Dọn temp, chọn ổ khác | — |
| `E_PERMISSION_DENIED` | Không có quyền ghi | "Không có quyền ghi vào thư mục." | Chọn thư mục khác | — |
| `E_NETWORK_OFFLINE` | Mất mạng | "Mất kết nối Internet." | Chờ / local fallback | — |
| `E_MODEL_DOWNLOAD_FAILED` | Tải model lỗi | "Không tải được model." | Retry / import model tay | 2 |
| `E_JOB_CANCELLED` | User cancel | "Đã hủy." | — | — |

## 28.2 Nguyên tắc

- Mọi lỗi log kèm job_id + stack trace (technical), nhưng UI chỉ hiện message thân thiện.
- Lỗi transient vs permanent: retry policy khác nhau.
- Nếu job fail → project giữ nguyên trạng thái cache tốt nhất → user sửa lỗi và "resume".

---

# 29. Testing Strategy

## 29.1 Tầng test

| Loại | Công cụ | Phạm vi |
|---|---|---|
| Unit tests (Rust) | cargo test | services, cache, schema validate |
| Unit tests (Python) | pytest | context, subtitle engine, providers (mock), chunking |
| Unit tests (Frontend) | Vitest | stores, bridge, components |
| Integration (worker) | pytest + httpx | STT trên fixture nhỏ, translation mock, render short clip |
| AI pipeline tests | pytest (marker `ai`) | whisper nhỏ, translate với mock+1 real call (tagged) |
| Video tests | pytest | render MP4/MKV/MOV, verify ffprobe output, golden hash |
| Audio tests | pytest | extract 16k wav, cache key |
| UI tests | Playwright (Tauri driver) | flow chính: import→transcribe(mock)→translate(mock)→render |
| Performance tests | pytest bench + scripts | 1/10/30/60 min benchmark |
| GPU tests | CI job có GPU | nvenc, cuda STT, VRAM guard |
| Packaging tests | script verify | installer cài/uninstall, sidecar chạy, không cần runtime ngoài |
| Installation tests | máy ảo / GitHub Actions Windows | Win10/Win11 |
| Update tests | — | upgrade từ version cũ, rollback |
| Regression | toàn bộ | chạy trên merge |

## 29.2 Test matrix phần cứng (CI + manual)

```text
OS:        Windows 10, Windows 11
GPU:       NVIDIA, AMD, Intel iGPU, CPU-only
RAM:       8GB, 16GB, 32GB
VRAM:      4GB, 8GB, 12GB+
```

> CI chỉ chạy một phần (CPU + 1 GPU runner). Phần AMD/Intel/8GB cần test manual hoặc máy ảo riêng.

## 29.3 Fixtures

- Video mẫu 10 giây (nhiều codec), audio mẫu tiếng Trung/Việt, subtitle mẫu burn-in.
- Mock LLM server (OpenAI-compatible) cho CI không cần API key.

---

# 30. Benchmark Strategy

## 30.1 Benchmark script (`scripts/bench`)

Với video 1 / 10 / 30 / 60 phút (fixtures tổng hợp), đo:

| Metric | Ghi chú |
|---|---|
| Processing time từng stage | per stage |
| RAM peak | tracemalloc (Python) / Process Explorer |
| VRAM peak | nvidia-smi |
| CPU usage | — |
| GPU usage | — |
| Output size / bitrate | ffprobe |
| Translation latency | per block |
| TTS latency (Post-MVP) | — |
| Rendering latency | per min video |

## 30.2 Baseline target (ước tính, sẽ đo chính xác)

- STT: ~10–50× real-time tùy GPU/model (faster-whisper large-v3 int8 GPU ~12×).
- Render: nhanh hơn real-time nhiều với NVENC; CPU chậm hơn.
- Translation: phụ thuộc provider + block size.
- Toàn pipeline 10 phút video: mục tiêu < 10–15 phút với GPU tầm trung.

---

# 31. Performance Optimization

## 31.1 Không load toàn bộ video vào RAM

- FFmpeg stream; audio extract → file; STT theo VAD chunks; render stream.

## 31.2 Chunking & parallelism

- STT: VAD pre-segment + batch.
- Translation: parallel blocks với concurrency giới hạn (theo provider rate limit).
- Render: FFmpeg multithread theo hardware.
- Chỉ 1 job nặng cùng lúc (MVP).

## 31.3 Memory management (Python worker)

- Gọi `gc.collect()` + `torch.cuda.empty_cache()` giữa các job lớn.
- Load model lazy (chỉ load khi cần); unload sau job (có warm option).
- Worker restart khi leak nặng (health monitor).

## 31.4 Temp storage

- Temp trong `%LOCALAPPDATA%` của app hoặc thư mục project cấu hình; dọn sau job.
- Cache LRU giới hạn dung lượng.

---

# 32. Packaging

## 32.1 Pipeline build

```text
Source Code
 → Frontend build (Vite)
 → Rust build (tauri build)
 → Python worker build (PyInstaller onedir → sidecar exe)  [CI per OS/arch]
 → Bundle FFmpeg/FFprobe (vendor)
 → Bundle models? KHÔNG — models download lần đầu (tránh installer ~2-5GB)
 → Tauri bundler: NSIS installer
 → Code sign (main exe + sidecar + installer)
 → Auto-update artifacts
```

## 32.2 Python worker bundle

- **PyInstaller onedir** (không onefile — khởi động nhanh, dễ debug, less AV flag).
- Python **3.11** (đã test với torch ecosystem; `TODO — VERIFY` mới nhất tương thích).
- GPU: bundle cả torch CUDA wheels → installer lớn (~2–4 GB). **Giải pháp:** chia 2 bản:
  - **CPU installer** (mặc định): torch CPU only (~300–600 MB total).
  - **GPU runtime add-on:** option download thêm CUDA wheels khi user có GPU (bật trong Settings). Đánh dấu `TODO — VERIFY` size chính xác.
- Tauri `externalBin` sidecar — lưu ý bug NSIS không thay sidecar khi reinstall (cần installer hooks kill process + delete binary, `SetOverwrite on`).

## 32.3 FFmpeg bundle

- Build static FFmpeg (LGPL-safe config) hoặc dùng build đã verify có license tài liệu rõ. Bundle `ffmpeg.exe`, `ffprobe.exe`.

## 32.4 Models (không bundle)

- Model weights tải lần đầu từ HF Hub (có progress + resume). Thư mục `models/` trong user-data. Offline: cho phép import thủ công.
- Quản lý model tách 4 thành phần riêng biệt (đồng bộ TASKS.md TASK-016A–D):
  - **ModelRegistry** — manifest khai báo model (id, name, version, source/repo, download URL, expected size, checksum, license, required VRAM, supported backend).
  - **ModelDownloader** — tải có progress + resume (huggingface_hub snapshot_download / HTTP range).
  - **ModelVerifier** — verify checksum + license + kích thước sau tải; **checksum không khớp → KHÔNG đánh dấu model available** (đánh dấu corrupt, cho re-download).
  - **ModelCache** — lưu trong `user-data/models/`, key theo `(id, version)`; không re-download nếu đã có hợp lệ; hỗ trợ import thủ công offline.

## 32.5 WebView2

- Bundle `embedBootstrapper` (thêm ~1.8 MB) để đảm bảo máy không có WebView2 vẫn cài được.

---

# 33. Installer

## 33.1 NSIS installer (Tauri bundler)

`AI-Video-Localization-Setup.exe`

- Install / Uninstall (Apps & Features).
- Desktop shortcut, Start Menu.
- File association (optional): `.avlp` project.
- Version, upgrade (replace), repair (re-run installer).
- Installer hooks: kill sidecar process trước khi thay binary (fix bug reinstall).
- Per-user install (không cần admin) → mặc định; Enterprise có thể yêu cầu admin.
- WebView2 bootstrapper.

---

# 34. Code Signing

- Chứng chỉ **OV** (đủ giảm SmartScreen) hoặc **EV** (tốt nhất cho app mới). Trong `tauri.conf.json` `bundle.windows`: `certificateThumbprint`, `digestAlgorithm: sha256`, `timestampUrl`; hoặc dùng Azure Key Vault / Azure Artifact Signing / `signCommand` tùy biến.
- Sign: main exe, sidecar exe, installer (`signtool sign` + timestamp).
- **VERIFIED 2026-08-09:** pin Tauri CLI ổn định (vd 2.5.1 trở lên); **bug tauri#13485** — signing key có password đọc từ env có thể fail → test qua CI, tránh ký tự đặc biệt trong password. Mọi update bundle phải đặt env `TAURI_SIGNING_PRIVATE_KEY` (file `.env` **KHÔNG hoạt động** — phải dùng env thật).
- CI lưu cert trong GitHub secrets; key bảo mật tuyệt đối (lưu ý: mất private key = không thể publish update cho user đã cài bản cũ).

---

# 35. Auto Update

## 35.1 Cơ chế (Tauri updater plugin)

```text
App
 → Check version (endpoint static JSON / update server)
 → New version?
 → Download (installer artifact + .sig)
 → Verify signature (public key embedded)
 → Install (passive mode)
 → Restart
```

- **VERIFIED 2026-08-09 (Windows):** updater artifact = `*_x64-setup.exe` + `*.exe.sig` (Tauri v2 KHÔNG tạo `.nsis.zip` nữa). Config: `bundle.createUpdaterArtifacts: true`; `plugins.updater.pubkey` + `endpoints` (HTTPS, không bật `dangerousInsecureTransportProtocol`); `windows.installMode: "passive"`. Build phải đặt env `TAURI_SIGNING_PRIVATE_KEY`; nếu thiếu `.sig` → updater từ chối update.
- **Rollback:** giữ bản cài trước (installer ghi đè nhưng NSIS + updater cho phép chạy lại bản cũ); test rollback trong testing. Sidecar version phải bump theo app version (bug NSIS không thay sidecar khi cùng version).
- Update server: static JSON trên GitHub Releases (MVP) → tự host CDN (V1).
- Không force-update khi đang render (chờ job xong hoặc hỏi user).

---

# 36. Commercial Architecture

## 36.1 Mô hình (V1+, không phải MVP)

```text
Desktop App
 → Authentication (license server)
 → Subscription / Credits
Free / Pro / Studio / Enterprise
```

## 36.2 Nguyên tắc

- **Core local processing phải hoạt động khi server downtime** (đã mua license offline-check với grace period).
- MVP không có billing — không lock feature sau paywall ở MVP.
- Credits dùng cho cloud AI (translation/TTS) — có thể giới hạn local AI ở bản Free.
- License server: `TODO — VERIFY` chiến lược (Keygen / self-host / buy once).
- Telemetry optional (user opt-in).

---

# 37. Development Roadmap

## 37.1 Phases (chi tiết ở phần FINAL RECOMMENDATION + TASKS)

```text
Phase 0  Architecture                [đã xong — MASTER_PLAN.md này]
Phase 1  Project Foundation          (repo, CI, schemas, dev env)
Phase 2  Desktop Shell + Worker      (Tauri + sidecar + IPC + health)
Phase 3  Video Engine                (probe, extract, ffmpeg safe)
Phase 4  Job System + Cache          (pipeline, state, cache)
Phase 5  STT                         (faster-whisper/whisper.cpp)
Phase 6  Translation                 (context engine, providers, glossary)
Phase 7  Subtitle Engine             (cues, ASS/SRT, style)
Phase 8  Subtitle Editor + Preview   (UI)
Phase 9  Render + Watermark + Export (UI + QC)
Phase 10 Settings + GPU + Errors     (settings UI, hardware probe)
Phase 11 Optimization + Performance  (memory, chunking, bench)
Phase 12 Testing                     (matrix, golden, E2E)
Phase 13 Packaging + Installer + Signing
Phase 14 Beta + Auto Update
Phase 15 Release
```

Mỗi phase có: Goal, Tasks, Files, Dependencies, Implementation order, Acceptance criteria, Potential problems, Testing, Definition of Done (chi tiết ở TASKS.md).

---

# 38. MVP Definition

## 38.1a MVP CORE (vertical slice — bắt buộc, làm trước)

> Chứng minh pipeline Import → Probe → Extract → STT → Translation → Subtitle Gen → Burn → Export chạy end-to-end. Mục tiêu: một video mẫu 10 phút tiếng Trung → video phụ đề tiếng Việt hoàn chỉnh.

```text
✓ Windows desktop app (Tauri) — installable, launchable
✓ Import video (MP4/MKV/MOV/AVI/WebM)
✓ Video metadata detection (ffprobe)
✓ Audio extraction (16k mono)
✓ Local STT (faster-whisper / whisper.cpp) — segments + timestamps + confidence
✓ Language detect + override
✓ Contextual translation (cloud default, local fallback nếu có local model)
✓ Translation quality check + retry
✓ Subtitle generation (ASS/SRT/VTT)
✓ Subtitle style (font, size, stroke, shadow, position)
✓ Burn-in render (libass + hardware encode + CPU fallback)
✓ Export (video + subtitle files)
✓ Progress + ETA + Cancel
✓ Error handling + retry (bảng lỗi core)
✓ Job pipeline + logs
```

**Quality gates bắt buộc của MVP CORE:** chạy qua `GOLDEN_VIDEO_TEST.md` (PASS cho các checkpoint bắt buộc) và translation đạt ngưỡng của `QUALITY_BENCHMARK.md` (mục 5).

## 38.1b MVP POLISH (sau CORE — bắt buộc trước release MVP, thứ tự thấp hơn)

> Mở rộng UX/settings sau khi CORE chạy. Vẫn thuộc MVP, nhưng nếu phải cắt giảm scope thì cắt ở đây trước (đánh dấu rõ trong báo cáo release).

```text
✓ Subtitle editing UI (text, timing, speaker)
✓ Subtitle preview (video + overlay) — position/font/stroke/shadow khớp ASS defaults
✓ Glossary + translation memory (cơ bản)
✓ Watermark (text + image, basic)
✓ Cache (STT / translation / render)
✓ Project save/load/resume
✓ Settings (AI, GPU, API key masked, cache, privacy mode)
✓ GPU detect + device override
✓ Privacy Mode (local-first)
```

## 38.2 MVP SHOULD HAVE

```text
○ Speaker diarization (cần HF token — optional ON)
○ Word-level timestamps (WhisperX align)
○ Translation local fallback (Qwen GGUF qua llama.cpp)
○ LLM-as-judge quality score (preset High/Maximum)
○ File association .avlp
○ Auto-update
```

## 38.3 MVP MUST NOT HAVE

```text
✗ AI dubbing / TTS
✗ Voice cloning
✗ Audio source separation
✗ Burned-in subtitle removal / inpainting (OCR)
✗ Complex timeline (NLE)
✗ Cloud backend / accounts / billing / subscription / enterprise
✗ Team/collaboration
✗ Mobile support
```

---

# 39. V1 Definition

Thêm vào sau MVP:

```text
○ Speaker diarization (default ON khi có token)
○ Word-level timestamps đầy đủ
○ Local translation fallback production-ready
○ Scene-aware / Speaker-aware / Character-aware translation (scene detection + character relationships, tone/emotion) — hiện KHÔNG cam kết ở MVP (§3.3/§12.1)
○ Burned-in subtitle removal (RapidOCR + STTN/LaMa)
○ Audio separation (Demucs)
○ AI dubbing (TTS cloud + local) với timing alignment
○ Audio mixing
○ Timeline nâng cao
○ Auto-update production
○ Licensing/commercial (offline grace, credits)
○ Benchmark + báo cáo performance
○ Enterprise features (license, admin)
```

---

# 40. Future Roadmap

```text
V2:
○ Video editing cơ bản (cắt ghép, clip)
○ Multi-language tracks trong 1 project
○ Batch processing nhiều project
○ GPU resource scheduler (nhiều GPU)
○ Voice cloning có consent
○ Plugin system (provider plugins)

V3:
○ Mobile (Tauri mobile)
○ Cloud rendering (optional)
○ Web/API service cho enterprise
○ Collaboration
```

---

# 41. Risks

| # | Risk | Probability | Impact | Mitigation |
|---|---|---|---|---|
| R1 | Installer quá lớn (torch CUDA) | High | Medium | Tách CPU/GPU installer + runtime add-on |
| R2 | Sidecar NSIS reinstall không thay binary | Medium | High | Installer hooks kill+delete, bump version |
| R3 | OOM/VRAM trên máy yếu | High | High | VRAM guard, CPU fallback, 1 job/lần |
| R4 | API provider giá/model đổi | Medium | High | Provider abstraction, không hard-code |
| R5 | Hallucination trong translation | High | Medium | Structured output + validation + QC |
| R6 | Burn-in removal khó cho video có motion (Post-MVP) | High | Medium | Nhiều engine + manual mask fallback |
| R7 | Whisper diarization chưa chính xác 100% | Medium | Low | Optional, user chỉnh speaker |
| R8 | Pyannote license gated | Medium | Low | Optional + hướng dẫn HF token |
| R9 | GPL license (Piper fork) rủi ro | Low | Medium | Chọn Kokoro/Chatterbox thay thế |
| R10 | WebView2 thiếu trên Win10 cũ | Medium | Low | Embed bootstrapper |
| R11 | Crash giữa render mất công | Medium | Medium | Cache + resume từ stage |
| R12 | TTS tiếng Việt local chưa tốt (Post-MVP) | High | Medium | Cloud TTS mặc định cho Việt |
| R13 | FFmpeg LGPL compliance sai | Low | High | Dùng build đã verify + tài liệu license |
| R14 | Tauri/updater bug phiên bản | Medium | Medium | Pin version, test update |
| R15 | GPU detect sai (WMI thiếu) | Medium | Medium | Nhiều nguồn detect + manual override |
| R16 | Translation local (Qwen GGUF) chậm/chất lượng | Medium | Medium | Cloud default, local fallback |
| R17 | Ví dụ API cost vượt budget user | Medium | Medium | Preset Fast, TM, cache, hiển thị chi phí ước tính |
| R18 | Bảo mật API key lộ | Low | High | Credential Manager + masked + không log |
| R19 | Rất khó test GPU matrix | High | Medium | CI GPU + test matrix manual |
| R20 | Scope creep AI dubbing vào MVP | Medium | High | Giữ MVP strict — MUST NOT list |

---

# 42. Technical Debt

| Item | Why | Khi nào trả |
|---|---|---|
| Polling thay WebSocket | Giảm complexity MVP | Phase V1 khi có timeline realtime |
| Preview overlay HTML thay libass live | Nhanh, nhưng khác biệt font | Post-MVP: render preview frame |
| Không segment-based render resume | FFmpeg không pause tự nhiên | V1: keyframe-based resume |
| Cache render không đầy đủ | Tránh disk tốn | V1: composite layer cache |
| Worker HTTP không TLS | Loopback + token đủ cho desktop | Chỉ khi remote |
| Chỉ 1 job song song | Tránh OOM | V1: scheduler |
| PyInstaller onedir (chậm load hơn native) | — | V1: kiểm tra alternatives |

---

# 43. Acceptance Criteria (cấp MVP — tóm tắt)

- App cài được trên Win10/11 mới, không cần cài Python/Node/Rust/FFmpeg/CUDA.
- Import MP4/MKV/MOV → metadata đúng.
- STT local ra transcript tiếng Trung/Việt với timestamp đúng.
- Translation có context, glossary áp dụng, đúng target language, không miss line.
- Subtitle sinh ASS/SRT hợp lệ; editor sửa được; preview hiển thị đúng.
- Render giữ nguyên resolution/FPS, subtitle rõ ràng, watermark đúng vị trí. **Render validation bắt buộc:** verify output resolution == input (hoặc theo config), FPS, audio track còn nguyên, codec/container hợp lệ, burn-in có subtitle, duration ≈ source (±1s); encoder fail → fallback NVENC→QSV→AMF→CPU + cảnh báo, KHÔNG xuất file hỏng im lặng (xem §9, TASKS TASK-027/029).
- Progress/ETA/cancel/retry hoạt động; crash không mất project.
- Cache: đổi style không chạy lại AI.
- Privacy Mode: không upload video; cloud translation chỉ khi explicit consent; không telemetry nếu không đồng ý.
- Bảng lỗi core xử lý đúng message.
- MVP checklist ở 38.1 pass.

---

# 44. Definition of Done (cho MVP)

> Checklist xác định MVP hoàn thành — chi tiết hơn ở TASKS.md. Chia 3 tầng: **Technical / Product / Quality**.

**Tầng 1 — Technical (hệ thống chạy đúng, không sập, không lùi bảo mật):**

- [ ] Build + install được trên Win10/11 mới (không cần Python/Node/Rust/FFmpeg/CUDA); uninstall sạch.
- [ ] Test suite xanh: unit + integration + E2E + benchmark 1/10/30/60 phút.
- [ ] Không crash (critical) khi cancel/OOM/API fail; bảng lỗi core đúng code + message.
- [ ] Không regression bảo mật: API keys chỉ ở Credential Manager, không log secret, không fallback encrypted-file tự chế.
- [ ] Installer SmartScreen pass (signed); auto-update + rollback test (Phase 14).

**Tầng 2 — Product (luồng nghiệp vụ chạy end-to-end):**

- [ ] Pipeline Import→Probe→Extract→STT→Translate→Subtitle→Render→Export chạy end-to-end trên video mẫu 10 phút (tiếng Trung→Việt).
- [ ] Chạy được CPU-only và NVIDIA GPU (cùng 1 installer hoặc runtime add-on).
- [ ] Cache đúng (đổi style = chỉ render; sửa translation = không chạy lại STT).
- [ ] Cancel giữa render dọn temp; resume không chạy lại AI.
- [ ] Progress/ETA/cancel/retry hoạt động; crash không mất project.

**Tầng 3 — Quality (chất lượng đầu ra đạt ngưỡng):**

- [ ] STT: transcript trên GOLDEN_VIDEO_TEST.md đạt checkpoint bắt buộc (timing ±200ms, không miss segment).
- [ ] Translation: đạt ngưỡng score của QUALITY_BENCHMARK.md (mục 5) trên Golden Translation Dataset.
- [ ] Subtitle: readability — line break theo policy, CPS ≤ ngưỡng config, timing padding đúng, preview position khớp final render.
- [ ] Video integrity: output resolution/FPS/audio/codec/container/duration đạt render validation (§43); watermark đúng vị trí.
- [ ] Tài liệu đủ (README, ARCHITECTURE, DEVELOPMENT, AI/VIDEO/AUDIO_PIPELINE, DATABASE, API, SECURITY, LICENSING, TESTING, RELEASE).
- [ ] Không có dependency non-commercial trong release (ProPainter/XTTS/F5 loại bỏ) — bảng LICENSING verified.

---

# 45. FINAL RECOMMENDATION

## 45.1 Recommended Stack

```text
Desktop:     Tauri 2 (Rust)
Frontend:    React 18 + TypeScript + Vite
UI:          Tailwind CSS + shadcn/ui (Radix) + Zustand + TanStack Query
AI Runtime:  Python 3.11 sidecar (FastAPI/Uvicorn trên 127.0.0.1)
STT:         faster-whisper (CTranslate2, int8) — NVIDIA/CPU
             whisper.cpp (ggml, Vulkan/CPU) — AMD/Intel/CPU fallback
Alignment:   WhisperX wav2vec2 forced alignment (optional)
Diarization: pyannote speaker-diarization-community-1 (optional, HF token)
Translation: Cloud: Gemini 2.5 Flash-Lite (default) / 2.5 Flash [MVP; OpenAI/Claude/DeepSeek = V1+ configurable]
             Local fallback: llama.cpp + Qwen GGUF (OpenAI-compatible)
OCR:         RapidOCR (ONNX Runtime) — [Post-MVP removal]
Inpainting:  STTN + LaMa (MIT) — [Post-MVP]; KHÔNG dùng ProPainter
TTS:         Cloud: ElevenLabs / Azure / OpenAI TTS  |  Local: Kokoro / Chatterbox — [Post-MVP]
Separation:  Demucs v4 htdemucs_ft (MIT) — [Post-MVP]
Audio timing: Rubber Band / FFmpeg atempo + rate/pause/split — [Post-MVP]
Video:       FFmpeg 7.x + FFprobe (bundled) + libass
Encode:      NVENC / QuickSync / AMF / libx264 / libx265 (auto-detect + fallback)
Database:    SQLite (WAL) + versioned migrations
Backend:     Không cloud backend ở MVP — Rust core + Python worker (localhost)
Packaging:   PyInstaller onedir (worker) + Tauri bundler
Installer:   NSIS (WebView2 embedBootstrapper, installer hooks)
Update:      Tauri updater (static JSON, signed, rollback)
Cloud (V1+): License server + credits (offline grace)
```

## 45.2 MVP Scope (làm đầu tiên — đúng thứ tự)

**MVP CORE (vertical slice — làm trước):**

1. Import + Probe (ffprobe) + Extract audio
2. Local STT
3. Contextual Translation (cloud default + local fallback option)
4. Subtitle generation
5. Subtitle burn-in render + export

**MVP POLISH (sau CORE, trước release):** subtitle editing UI + preview, watermark, cache, project save/load, GPU detect + override, privacy mode, settings UI, glossary + translation memory UI.

**KHÔNG** làm ở MVP: dubbing, separation, OCR removal, billing, timeline phức tạp.

## 45.3 V1 Scope (sau MVP)

Diarization mặc định, word timestamps, local translation production, OCR removal, separation, dubbing, audio mix, auto-update, licensing.

## 45.4 Top 20 Technical Risks

(Xem bảng [41. Risks](#41-risks) — R1→R20. Top 5: R3 OOM/VRAM, R1 installer size, R2 sidecar reinstall, R5 hallucination, R4 provider drift.)

## 45.5 First 30 Development Tasks (đúng thứ tự triển khai)

> Chi tiết format đầy đủ (Acceptance Criteria, Test cases, DoD) sẽ nằm ở `TASKS.md`. Dưới đây là thứ tự + mô tả ngắn.

| # | Task | Purpose | Files/Modules | Dependencies | Expected Result |
|---|---|---|---|---|---|
| 001 | Khởi tạo repo + README + git + .gitignore + AGENTS.md | Foundation | toàn repo | — | Repo clean, có hướng dẫn |
| 002 | Cấu hình CI cơ bản (lint, build test) | Foundation | `.github/workflows/*` | 001 | CI xanh |
| 003 | Tạo Tauri 2 shell + React/Vite + Tailwind | Desktop shell | `src-tauri/`, `src/` | 001 | App mở cửa sổ |
| 004 | Bridge IPC typed đầu tiên (`invoke` ping) | IPC | `bridge.ts`, `commands/system.rs` | 003 | Ping hoạt động |
| 005 | Python worker skeleton (FastAPI + /health) | Worker | `worker/` | 001 | /health OK |
| 006 | Sidecar lifecycle (spawn, health, restart) | Worker mgmt | `worker_manager.rs` | 005 | Rust spawn/stop worker |
| 007 | Schema JSON versioned + Pydantic + TS types | Schema | `schemas/` | 005 | Validate chung |
| 008 | SQLite setup + migrations + ProjectService | DB | `db/`, `project_service.rs` | 007 | CRUD project |
| 009 | ffprobe media probe (MediaProbeService) | Video | `media_service.py` | 005 | Metadata đúng |
| 010 | Job system (state, queue, progress, persist) | Jobs | `job_service.rs`, DB | 008 | Job lifecycle |
| 011 | CacheService (keys, get/set/invalidate) | Cache | `cache_service.rs`, `cache.py` | 008 | Cache hit/miss |
| 012 | Audio extract (FFmpeg safe args) | Audio | `audio_service.py`, `ffmpeg.py` | 009 | WAV 16k mono |
| 013 | STT: faster-whisper integrate + progress | STT | `stt_service.py` | 012 | Transcript JSON |
| 014 | STT: device/GPU detect + int8 + VRAM guard | STT | `hardware.py` | 013 | Chọn device đúng |
| 015 | STT: whisper.cpp fallback (CPU/AMD) | STT | `stt_service.py` | 014 | CPU chạy được |
| 016 | Model management (TASK-016A Registry / B Downloader / C Verifier / D Cache) | Models | `model_registry.py`, `download_models.py`, `model_verifier.py`, `model_cache.py` | 013 | Download/resume/verify/import |
| 017 | TranslationProvider interface + MockProvider | Translation | `providers/translation/*` | 007 | Mock trả kết quả |
| 018 | OpenAI provider (JSON mode) — **[Post-MVP, implement sau TASK-020]** | Translation | `openai_provider.py` | 017 | Dịch block (V1+) |
| 019 | Gemini provider | Translation | `gemini_provider.py` | 017 | Dịch block |
| 020 | Local LLM provider (llama.cpp OpenAI-compat) | Translation | `local_llm_provider.py` | 017 | Dịch local |
| 021 | Context Engine + chunking + overlap | Translation | `context_service.py` | 019, 020 | Context đúng |
| 022 | Translation validation + retry + QC cơ bản | Translation | `quality_service.py` | 021 | Không miss line |
| 023 | Glossary + character dict + translation memory | Translation | DB, `translation_service.py` | 022 | Glossary áp dụng |
| 024 | SubtitleEngine (cues, line break, CPS, ASS/SRT) | Subtitle | `subtitle_service.py` | 023 | ASS/SRT hợp lệ |
| 025 | Subtitle editor UI (bảng cues, chỉnh timing/text) | UI | `SubtitleEditorView.tsx` | 024 | Sửa được |
| 026 | Preview (video + overlay) | UI | `PreviewView.tsx` | 025 | Preview đúng |
| 027 | RenderService (libass burn-in + encoder auto + progress) | Render | `render_service.py` | 024 | Output video |
| 028 | Watermark (text + image) | Render | `render_service.py`, UI | 027 | Watermark đúng |
| 029 | Export (video + subtitle files) + QC (ffprobe) | Export | `render_service.py`, UI | 027 | Export OK |
| 030 | Settings UI (AI/GPU/API masked/cache/privacy) + error UI | Settings | `SettingsViews.tsx`, `secret_store.rs` | 010, 011 | Settings hoạt động |

## 45.6 Definition of Done (MVP)

(Xem đầy đủ [44. Definition of Done](#44-definition-of-done).) Tóm tắt: pipeline end-to-end 10 phút video Trung→Việt; chạy CPU + NVIDIA; cache đúng; cancel/resume; secrets an toàn; test + benchmark xanh; installer + update + signing; licensing verified; docs đủ.

---

*End of MASTER_PLAN.md — Source of truth. Các quyết định cần `TODO — VERIFY` phải được xác nhận trước Phase 5 (STT), Phase 6 (Translation) và Phase 13 (Packaging).*
