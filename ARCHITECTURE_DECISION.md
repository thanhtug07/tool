# ARCHITECTURE_DECISION.md — Quyết định kiến trúc (FROZEN)

**Version:** 1.0.0
**Ngày freeze:** 2026-08-09
**Phạm vi:** Toàn bộ quyết định kiến trúc cho MVP của AI Video Localization Studio (Windows desktop).
**Thẩm quyền:** Sau khi áp dụng các sửa đổi S1-S6 từ `MASTER_PLAN_REVIEW.md`, các quyết định dưới đây được **ĐÓNG BĂNG (FREEZE)**. Mọi thay đổi sau freeze phải qua ADR (Architecture Decision Record) mới — không sửa tự do.

---

## 1. QUY TẮC FREEZE

```text
1. Mọi lựa chọn trong file này là FROZEN cho MVP, trừ khi có lý do kỹ thuật chứng minh được.
2. Thay đổi = viết ADR (TITLE / CONTEXT / DECISION / CONSEQUENCES) → nộp review → chấp thuận.
3. Không thêm dependency mới vào MVP nếu chưa có ADR.
4. KHÔNG CODE trước Phase 1 (Project Foundation) hoàn tất.
5. Không đổi provider abstraction; chỉ thêm provider mới qua interface.
```

---

## 2. DECISION MATRIX (chấm điểm 1-5)

### 2.1 Desktop shell

| Tiêu chí (trọng số) | Tauri 2 | Electron | .NET WPF/WinUI |
|---|---|---|---|
| RAM idle (0.25) | 5 | 2 | 4 |
| Bundle size (0.15) | 5 | 2 | 3 |
| Security model (0.2) | 5 | 3 | 4 |
| Python/FFmpeg integration (0.15) | 5 | 5 | 3 |
| Auto-update/installer (0.15) | 5 | 5 | 3 |
| Dev speed (0.1) | 3 | 5 | 3 |
| **Tổng (weighted)** | **4.80** | **3.50** | **3.45** |

**→ CHỌN: Tauri 2.**
- *Reason:* shell phải tiết kiệm RAM/VRAM vì AI worker chạy cùng lúc; security deny-by-default phù hợp xử lý dữ liệu nhạy cảm.
- *Trade-off:* phải viết Rust (orchestration mỏng), CSP strict, ecosystem nhỏ hơn Electron.
- *Fallback (nếu team không có Rust):* Electron — giữ nguyên mọi tầng khác, chỉ đổi shell.

### 2.2 AI runtime

| Tiêu chí | Python sidecar | Rust native | Node.js |
|---|---|---|---|
| AI ecosystem (0.4) | 5 | 2 | 3 |
| STT/TTS/Demucs quality (0.3) | 5 | 4 | 3 |
| Bundle complexity (0.15) | 3 | 5 | 4 |
| Dev speed (0.15) | 4 | 2 | 3 |
| **Tổng** | **4.55** | **3.10** | **3.15** |

**→ CHỌN: Python 3.11 sidecar (process riêng, FastAPI localhost).**
- *Reason:* toàn bộ model AI (faster-whisper, llama.cpp client, Demucs, TTS) là Python-first. Process riêng → crash AI không chết app, restart được, set resource limits được.
- *Trade-off:* installer nặng; phải quản lý lifecycle + auth token.
- *Fallback:* `tauri-plugin-python` (PyO3) nếu bundle Python quá khó — không phải mặc định.

### 2.3 Database

| Tiêu chí | SQLite (WAL) | PostgreSQL |
|---|---|---|
| Desktop fit (0.4) | 5 | 2 |
| Concurrency (0.3) | 4 | 5 |
| Migration/backup (0.3) | 4 | 3 |
| **Tổng** | **4.4** | **3.2** |

**→ CHỌN: SQLite WAL + versioned migrations.** Chỉ Rust core ghi DB; worker đọc/ghi qua API để tránh lock.

### 2.4 STT

| Tiêu chí | faster-whisper | whisper.cpp |
|---|---|---|
| NVIDIA speed/quality (0.4) | 5 | 4 |
| AMD/Intel GPU (0.25) | 2 | 4 |
| CPU (0.2) | 4 | 5 |
| Windows stability (0.15) | 5 | 3 (mitigations cần thiết) |
| **Tổng** | **4.15** | **4.00** |

**→ CHỌN: CẢ HAI (chiến lược kép):** faster-whisper (CUDA/CPU, int8) làm chính; whisper.cpp (Vulkan/CPU) cho AMD/Intel. Fallback tự động theo GPU detect. Kèm 3 mitigation (Mục 3.2).

### 2.5 Translation

| Tiêu chí | Cloud (LLM) | Local (llama.cpp) | Rule-based (không dùng) |
|---|---|---|---|
| Chất lượng Trung↔Việt (0.4) | 5 | 3 | 2 |
| Chi phí/block (0.3) | 4 | 5 | 5 |
| Offline (0.2) | 2 | 5 | 5 |
| Setup complexity (0.1) | 5 | 3 | 5 |
| **Tổng** | **4.3** | **4.0** | **3.4** |

**→ CHỌN: Cloud mặc định + Local fallback.** Abstraction `TranslationProvider`. **MVP chỉ implement:** `GeminiProvider` (mặc định Gemini 2.5 Flash-Lite Fast/Balanced, 2.5 Flash High/Maximum) + `LocalLLMProvider` (llama.cpp server OpenAI-compatible + Qwen GGUF) + `MockProvider`. OpenAI / Anthropic / DeepSeek → **V1+** (interface đã sẵn, không hard-code ở MVP).

### 2.6 TTS (Post-MVP, quyết định để dành)

- Cloud mặc định (ElevenLabs/Azure/OpenAI TTS) — chất lượng cao nhất, xử lý tốt tiếng Việt.
- Local: Kokoro gốc (tiếng Trung OK), Chatterbox (tránh tiếng Việt v3 — CER kém), Piper (verify license). **Không** XTTS v2 / F5-TTS / Viterbox / Kokoro-VN community (non-commercial).

### 2.7 Video render

**→ CHỌN: FFmpeg 7.x + libass + encoder auto-detect** (NVENC → QSV → AMF → libx264/x265). Argument array (không shell). Progress qua `-progress pipe:1`.

---

## 3. MVP TECHNICAL ARCHITECTURE (FROZEN)

### 3.1 Sơ đồ phân tầng

```text
┌──────────────────────────────────────────────────────┐
│  TẦNG 1 — Tauri 2 Rust Core                          │
│  ProjectService | JobService | CacheService          │
│  SettingsService | HardwareProbe | MediaProbe        │
│  WorkerManager (lifecycle) | WorkerClient (HTTP/WS)  │
│  SQLite (rusqlite, WAL) | SecretStore (CredManager)  │
└───────────────┬──────────────────────────────────────┘
                │ IPC: commands + events (typed)
┌───────────────┴──────────────────────────────────────┐
│  TẦNG 3 — Frontend React + TS + Vite                 │
│  Zustand (UI state) + TanStack Query (jobs)          │
│  Views: Dashboard/Import/Analyze/Transcript/Translate│
│         SubtitleEditor/Preview/Export/Settings/Logs  │
└──────────────────────────────────────────────────────┘
                │ spawn / auth token (stdin) / HTTP:WS
┌───────────────▼──────────────────────────────────────┐
│  TẦNG 2 — Python Worker (sidecar, 127.0.0.1)         │
│  FastAPI/Uvicorn  |  services: media/audio/stt/      │
│  diarization/context/translation/subtitle/render/    │
│  quality/cache/hardware/model(registry+downloader+   │
│  verifier+cache)                                     │
│  providers: translation{gemini,local_llm,mock}       │
│             (openai/anthropic/deepseek = V1+)        │
└──────────────────────────────────────────────────────┘
                │ subprocess (arg array)
┌───────────────▼──────────────────────────────────────┐
│  FFmpeg/FFprobe (bundled) + GPU (CUDA/QSV/AMF)       │
└──────────────────────────────────────────────────────┘
```

### 3.2 STT Design (FROZEN + mitigations)

```text
wav 16k mono
 → Silero VAD (segment)
 → faster-whisper (int8_float16, large-v3/turbo)   [NVIDIA/CPU]
   └─ nếu GPU = AMD/Intel → whisper.cpp (Vulkan)    [fallback]
 → (optional) WhisperX wav2vec2 align → word timestamps
 → (optional) pyannote community-1 → speaker
 → transcript.json
```

**3 mitigation bắt buộc (whisper.cpp Vulkan Windows — từ nghiên cứu 2026):**
1. `beam_size ≤ 6` (tránh crash AMD Radeon 780M + VAD, issue #3723).
2. `flash_attn = False` khi device = AMD/Intel Vulkan (tránh crash RDNA4, issue #3806); chỉ bật trên NVIDIA CUDA.
3. **Model init single-threaded** (semaphore) — tránh race init (PR #3638); nếu build static lib → gọi `ggml_backend_vk_reg()` thủ công sau init instance (issue #3750).

**Model & VRAM guard:** large-v3 int8 ~2.5–2.9 GB VRAM; nếu < yêu cầu → hạ xuống turbo/small hoặc CPU. OOM → catch → fallback + thông báo.

### 3.3 Translation Design (FROZEN)

```text
segments (speaker + time)
 + Glossary + Character dict + Rules
 → Context Engine (MVP: prev/current/next block, speaker map khi có,
                  glossary, rules)   [scene = Post-MVP]
 → Chunking: block 5-10 cues, overlap 2, semantic boundary
 → LLM (cloud default / local fallback) → structured JSON
 → Validation (schema, count = input, target lang detect)
 → Quality (CPS, hallucination) + Retry (max N, backoff)
 → translation.json
```

**Preset routing (cập nhật theo S1 + FIX #7 — MVP chỉ Gemini; GPT-4o-mini/Claude = V1+):**

| Preset | Model default (MVP) | Chunk | Overlap | QC | Retry |
|---|---|---|---|---|---|
| Fast | `gemini-2.5-flash-lite` | 10 | 2 | Basic | 1 |
| Balanced | `gemini-2.5-flash-lite` | 8 | 2 | Basic | 2 |
| High Quality | `gemini-2.5-flash` | 6 | 3 | Full + judge | 2 |
| Maximum | `gemini-2.5-flash` | 5 | 3 | Full + judge | 3 |

**Cost control:** block batching (giảm ~5-10× request), Translation Memory (skip trùng), prompt caching (đầu prompt tĩnh), hiển thị chi phí ước tính.

### 3.4 Module boundaries (trách nhiệm rõ ràng)

| Module | Tầng | Public interface | Trách nhiệm |
|---|---|---|---|
| ProjectService | Rust | `create/load/save/delete` | CRUD project, dirs |
| MediaProbeService | Python | `probe(path)` | ffprobe → metadata |
| AudioService | Python | `extract(path, spec)` | WAV 16k mono |
| STTService | Python | `transcribe(wav, opts)` | STT + VAD + align + speaker |
| ContextService | Python | `build(block)` | context pack |
| ModelManagerService | Python | `registry()/download()/verify()/cache()` | ModelRegistry/Downloader/Verifier/Cache (FIX #6) |
| TranslationService | Python | `translate(block, ctx)` | gọi provider, validate, retry |
| SubtitleService | Python | `generate(translations, cfg)` | cues + ASS/SRT + line break + CPS |
| RenderService | Python | `render(video, sub, wm, preset)` | libass burn-in + encode + progress |
| QualityService | Python | `check(translation)` | hallucination, CPS, lang detect |
| JobService | Rust | `submit/status/cancel/retry` | queue, state machine, persist |
| CacheService | Rust + Python | `get/set/invalidate` | cache keys content-addressed |
| WorkerManager | Rust | `start/stop/health/restart` | lifecycle sidecar + token |
| HardwareProbe | Rust + Python | `probe()` | GPU/CPU/ffmpeg caps → strategy |
| SecretStore | Rust | `set/get/delete` | Windows Credential Manager |

### 3.5 Data flow chính (MVP)

```text
Video → [Rust] MediaProbe(ffprobe) → metadata → DB
      → [Python] AudioService.extract → audio.wav 16k mono (cache)
      → [Python] STTService.transcribe → transcript.json (cache)
      → [Python] ContextService + TranslationService → translation.json (cache)
      → [Frontend] SubtitleEditor (user review) → edits → DB
      → [Python] SubtitleService.generate → subtitle.ass/srt (cache)
      → [Python] RenderService.render → output.mp4 (cache composite)
      → [Python] QualityService.check → QC report
      → Export → thư mục user
```

### 3.6 Job System (FROZEN)

```text
Pipeline: IMPORT → ANALYZE → EXTRACT_AUDIO → TRANSCRIBE → TRANSLATE
          → GENERATE_SUBTITLE → RENDER → EXPORT
State: queued → running → completed / failed / cancelled
Retry: auto (transient, backoff 1s/5s/30s, max 3) + manual (permanent)
Concurrency (MVP): 1 job nặng tại 1 thời điểm (queue FIFO)
Persistence: job status trong DB → resume sau crash
Cache check trước khi chạy mỗi stage → hit thì skip
```

### 3.7 Cache System (FROZEN)

| Cache | Key | Store |
|---|---|---|
| Audio extract | `audio:{sha256(video)}:{spec}` | project/cache |
| STT | `stt:{sha256(audio)}:{model}:{compute}:{lang}:{vad}` | project/cache |
| Translation | `tr:{sha256(source_block)}:{target}:{model}:{glossary_ver}:{rules_ver}` | DB + JSON |
| TTS (Post-MVP) | `tts:{sha256(text)}:{voice}:{provider}:{params}` | cache |
| Render | `render:{sha256(video+style+wm+encoder+preset)}` | cache (nếu đủ dung lượng) |

**Nguyên tắc:** đổi style → chỉ render; sửa translation → không chạy lại STT. LRU theo dung lượng (mặc định 10 GB).

---

## 4. LOCAL-FIRST STRATEGY (FROZEN)

```text
STT / Audio / Video / Render → 100% LOCAL (offline được)
Translation → Cloud mặc định (chất lượng) + Local fallback (llama.cpp + Qwen GGUF)
TTS (Post-MVP) → Cloud mặc định + Local fallback
Privacy Mode → mặc định: local STT/FFmpeg/subtitle; KHÔNG upload video;
               cloud translation chỉ khi explicit consent; local translation khi có;
               xóa temp; không telemetry (OCR/separation = Post-MVP, không nằm ở MVP)
```

- **Lý do:** dữ liệu video nhạy cảm; STT/OCR local để bảo mật + tiết kiệm chi phí (không phải gửi media lên cloud).
- **Không lock-in:** mọi provider là interface; user tự chọn cloud key hoặc local.
- **Preview ≈ Final render consistency:** subtitle preview (HTML overlay) phải khớp vị trí/font/size/stroke/shadow với ASS defaults (bottom-center, safe-area) — xem TASKS TASK-026. Độ lệch chấp nhận được ghi rõ; không để preview nhìn khác hẳn output cuối.
- **Offline:** toàn bộ local pipeline chạy offline; translation/tải model cần mạng → cảnh báo rõ.

---

## 5. PERFORMANCE CONSTRAINTS (FROZEN)

| Constraint | Giá trị |
|---|---|
| Không load toàn bộ video vào RAM | FFmpeg stream; STT theo VAD chunk |
| STT large-v3 int8 VRAM | ~2.5–2.9 GB |
| Separation VRAM (Post-MVP) | ~6–8 GB (Demucs htdemucs_ft) |
| 1 job nặng / 1 thời điểm (MVP) | queue FIFO, tránh OOM |
| Translation chunk | 5–10 cues/block, overlap 2 |
| Model load | lazy; unload sau job; `gc.collect()` + `torch.cuda.empty_cache()` |
| RAM target | hoạt động tốt ở 8 GB (CPU-only), 16 GB+ thoải mái |
| Pipeline 10 phút video (GPU tầm trung) | mục tiêu < 10–15 phút |

---

## 6. SECURITY REVIEW (FROZEN)

| Hạng mục | Quyết định |
|---|---|
| API keys | Windows Credential Manager (Rust `keyring`/`windows-credential-manager`); UI chỉ masked; không log. **Fail-safe:** CM không khả dụng → chặn lưu key + thông báo; KHÔNG custom crypto / encrypted-file fallback |
| Rust ↔ worker auth | Token 256-bit random mỗi session, truyền qua **stdin**, header `Authorization: Bearer`, so sánh constant-time |
| Worker bind | Chỉ `127.0.0.1`, port random |
| FFmpeg/command | Argument array (không shell string); validate path (chặn `; | &` newline NUL) |
| Path traversal | tauri-plugin-fs scoped; file chỉ trong project dir + export dir user chọn |
| CSP | Strict; capabilities deny-by-default; không enable `dangerous*` |
| Logs | Không log secret/transcript; log rotation + giới hạn |
| Temp files | ACL user hiện tại; xóa sau job |
| Privacy Mode | Mặc định local: không upload video; cloud translation chỉ khi explicit consent; không telemetry nếu không đồng ý |

---

## 7. LICENSING REVIEW (FROZEN — kèm điều kiện)

| Dependency | License | Dùng | Điều kiện |
|---|---|---|---|
| ProPainter | NTU non-commercial | ❌ Loại | — |
| XTTS v2 | CPML non-commercial | ❌ Loại | — |
| F5-TTS | CC-BY-NC-4.0 | ❌ Loại | — |
| Viterbox | CC-BY-NC-4.0 | ❌ Loại | — |
| Kokoro-Vietnamese (community) | Không rõ | ❌ Loại MVP | chỉ dùng khi có license thương mại rõ |
| faster-whisper | MIT | ✅ | — |
| whisper.cpp | MIT | ✅ | — |
| WhisperX | BSD-2-Clause | ✅ | — |
| pyannote community-1 | CC-BY-4.0 (gated) | ✅ optional | user accept agreement + HF token |
| Demucs htdemucs_ft | MIT | ✅ (Post-MVP) | — |
| RapidOCR | Apache-2.0 | ✅ (Post-MVP) | — |
| STTN + LaMa | MIT | ✅ (Post-MVP) | — |
| Kokoro (gốc) | Apache-2.0 | ✅ (Post-MVP) | tiếng Trung OK; Việt qua cloud |
| Chatterbox v3 | MIT | ✅ (Post-MVP) | tránh tiếng Việt (CER kém) |
| Piper | MIT/GPL-3.0 (fork) | ⚠️ (Post-MVP) | verify license từng giọng trước khi embed |
| FFmpeg | LGPL/GPL build | ✅ | dùng build static thương mại có bảng license |
| Qwen GGUF | Apache-2.0 | ✅ | verify model card |
| GPU runtime (NVIDIA CUDA / AMD ROCm / Intel driver) | EULA driver | ✅ | tải từ hãng, không bundle CUDA toolkit |
| Vulkan Loader (Khronos) | Apache-2.0 | ✅ | build dep |

**Re-verify:** mọi LICENSE file + model card tại thời điểm build release (Phase 13) — checklist trong TASKS.md.

---

## 8. MVP DEFINITION (FROZEN — từ MASTER_PLAN 38 + 45.2)

```text
MVP CORE (vertical slice — làm trước):
Import → Analyze → Extract Audio → Local STT → Contextual Translation
     → Subtitle Generation → Burn-in Render → Export

MVP POLISH (sau CORE, trước release):
Subtitle Editor/Preview → Watermark → Cache → Project save/load/resume
     → GPU detect + override → Privacy Mode → Settings UI → Glossary + TM UI

KHÔNG có: dubbing, separation, OCR removal, billing, timeline phức tạp, cloud backend.
```

---

## 9. PHASE BREAKDOWN (FROZEN)

```text
Phase 1  Project Foundation     (repo, CI, schemas, dev env, secret_store sớm)
Phase 2  Desktop Shell + Worker (Tauri + sidecar + IPC + health + auth token)
Phase 3  Video Engine           (probe, extract audio, ffmpeg safe)
Phase 4  Job System + Cache     (pipeline, state machine, cache)
Phase 5  STT                    (faster-whisper / whisper.cpp + mitigations)
Phase 6  Translation            (context engine, providers, glossary, TM)
Phase 7  Subtitle Engine        (cues, ASS/SRT, style, CPS)
Phase 8  Subtitle Editor + Preview (UI)
Phase 9  Render + Watermark + Export + QC
Phase 10 Settings + GPU + Error UI
Phase 11 Optimization + Performance + Benchmark
Phase 12 Testing (matrix, golden, E2E)
Phase 13 Packaging + Installer + Signing + Licensing verify
Phase 14 Beta + Auto Update
Phase 15 Release
```

Thứ tự coding phải theo Phase. Chi tiết task, dependency graph, và sprint đầu tiên nằm trong `TASKS.md`.

---

## 10. CÁC QUYẾT ĐỊNH BỊ TỪ CHỐI (đã cân nhắc, không chọn)

| Phương án | Lý do từ chối |
|---|---|
| Electron | RAM/bundle cao, security opt-in — chỉ fallback |
| .NET WPF/WinUI | AI ecosystem Python phải qua subprocess, UI native tốn công |
| Rust native cho AI | Ecosystem STT/TTS/Demucs kém hơn hẳn Python |
| PostgreSQL | Cần server, không phù hợp desktop |
| ProPainter (inpaint) | Non-commercial license |
| XTTS v2 / F5-TTS (TTS local) | Non-commercial license |
| WebSocket ở MVP | Polling 500ms đủ đơn giản; WS để dành V1 |
| Segment-based render resume | FFmpeg không pause tự nhiên — cancel + resume từ cache stage |

---

*Hết ARCHITECTURE_DECISION.md — Đầu vào cho IMPLEMENTATION_ROADMAP.md và TASKS.md.*
