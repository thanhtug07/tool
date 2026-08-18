# CHUNKED PIPELINE REPORT — Chunked Parallel Automation Pipeline

> Task: `TASK_AUTOMATION_PINELINE.md` — Phases 0–27. This report is the
> deliverable of Phases 20 (performance benchmark) and 27 (documentation).

## 1. Tóm tắt

Automation pipeline giờ có chế độ **chunked processing (mặc định TẮT — opt-in)**
chia video dài thành các chunk 30s (configurable 20/30/45/60s), xử lý song song
có giới hạn (`max_concurrency`, default 4), validation + retry theo chunk,
assembly theo thứ tự timeline, final validation, output verification và cleanup
an toàn. Pipeline classic hiện tại giữ nguyên — không regression.

## 2. Files changed

### Worker (`worker/`)
| File | Vai trò |
|---|---|
| `src/services/chunk_service.py` (**mới**) | Lõi: ChunkManager, per-chunk processing qua stt/translation/tts service hiện có, validation, retry, manifest, ordered assembly, timeline validation, final validation, verify_output, CleanupManager. Production path = `StreamingChunkPipeline` (stage-decoupled bounded pools STT→translate→TTS + ordered streaming assembly + streaming voice-track PCM → `cache/voice_track.wav`); `ChunkScheduler` (ThreadPoolExecutor) giữ lại làm historic path cho test retry semantics. |
| `src/api/pipeline.py` | Endpoints `/v1/automation/chunked` (run) + `/v1/automation/finalize` (final validation + verification) |
| `tests/unit/test_chunk_service.py` (**mới**) | 43 unit tests: chunk calc, short chunk, overlap, clamp, order, missing/duplicate, retry, manifest, cleanup safety, timeline, silent chunk, streaming pipeline (ordered commit, in-place stage retry, voice streaming) |

### Rust (`src-tauri/`)
| File | Vai trò |
|---|---|
| `src/db/repo/job.rs` | `JobType::Chunk` mới |
| `src/services/worker_client.rs` | `ChunkedRequest/Response`, `FinalizeRequest/Response`, `run_chunked`/`finalize` |
| `src/services/pipeline_runner.rs` | `run_chunk` (gọi worker chunked, forward progress/events/log) + helpers `profile_stage` |
| `src/services/settings_service.rs` | Settings `automation.chunk_duration`, `automation.chunk_overlap`, `automation.chunk_concurrency`, `automation.chunk_retries` |

### Frontend (`src/`)
| File | Vai trò |
|---|---|
| `src/pages/Automation/automation.ts` | Stage key `chunk` + resolve trong dependency chain |
| `src/workspace/types.ts`, `src/workspace/session.ts` | `chunked` option + persistence |
| `src/workspace/StudioWorkspace.tsx` | Wire `chunked` vào session/actions |
| `src/workspace/LeftPanel.tsx` | Toggle "Chunked processing (30s parallel)" trong More Options |
| `src/pages/Automation/logHelpers.ts`, `src/workspace/customTools.ts` | Labels/events cho chunk pipeline |

## 3. Architecture implemented

```
Input video (dài)
   ↓  audio extraction (1 lần)
30s chunk list (overlap 2s)          ← ChunkManager.build_chunks()
   ↓  STAGE-DECOUPLED bounded pools (stt/translate/tts, max_concurrency=4 configurable 1..8)
producer → STT pool → translation pool → TTS pool → completed q → assembly
     (mỗi stage tự do pool riêng; chunk xong STT nhường ngay slot cho chunk kế)
   ↓  retry theo chunk: retry ở ĐÚNG stage lỗi (không chạy lại STT), max_retries=2 → FAILED_PERMANENTLY
validated chunk results (index-ordered commit qua Ordered Assembly Buffer)
   ↓  order validation (missing/duplicate/out-of-order/gap)   ← validate_chunk_order()
ordered assembly: merge segments/cues; voice track stream PCM theo thứ tự commit   ← merge_segments/assemble_translations
   ↓  timeline validation (duration vs source, tolerance 0.5s)   ← validate_timeline()
final single encode (existing render → output/rendered.mp4)
   ↓  FINAL VALIDATION (ffprobe: streams, duration, size)   ← final_validation()
   ↓  OUTPUT VERIFIED (exists, readable, size stable, ffprobe ok)   ← verify_output()
   ↓  CLEANUP (chỉ khi validation PASS AND verification PASS)   ← CleanupManager
manifest (cache/chunk_manifest_{job_id}.json) survives cleanup; voice_track.wav được
move sang cache/ trước cleanup (durable, không nằm trong temp tree)
```

Progress/events: worker set progress/stage/message → Rust `ctx.log` → live log
(`CHUNK_CREATED`, `CHUNK_STARTED`, `STT_*`, `TRANSLATION_*`, `TTS_*`,
`CHUNK_VALIDATING/VALID/FAILED/RETRYING`, `ASSEMBLY_*`,
`FINAL_VALIDATION_*`, `OUTPUT_VERIFIED`, `CLEANUP_*`).

## 4. Tests executed

| Suite | Count | Result |
|---|---|---|
| Frontend (`npm test`) | 200 | ✅ PASS |
| Worker (`pytest`) | 114 | ✅ PASS |
| Rust (`cargo test`) | 199 | ✅ PASS |
| Chunk core unit tests (worker) | 43 | ✅ PASS (included above) |
| Typecheck + lint | — | ✅ clean |

## 5. Benchmark ladder

### Rung 1 — 30–60s (Phase 22) — `fixture_v2`, 40s video

| Metric | Value |
|---|---|
| Chunks | 2 (30s + 10s) |
| Concurrency | 4 |
| Wall time | ~49s (job_0110: 12:10:27 → 12:11:16) |
| Speed ratio | ~0.8x realtime |
| Result | ✅ succeeded — `output/rendered.mp4` 40.0s, 4.7 MB |
| Validation | PASS · Verification PASS · Cleanup (temp/job_0110 removed) |

### Rung 2 — 5 min (Phase 23) — `tc_5min`, 300s (first 5 min of bilibili source)

| Metric | Value |
|---|---|
| Chunks | 10 (30s + overlap 2s) |
| Concurrency | 4 |
| STT segments | 156 (timeline 9.1s → 300.0s, khớp source) |
| TTS | 10/10 voice tracks (edge, vi-VN-HoaiMyNeural) |
| Audio extraction | 0.4s |
| Chunked pipeline (STT+translate+TTS) | 1440s |
| Final encoding | 28s |
| Total wall | 1470s = 24.5 min |
| Speed ratio | 300/1470 = **0.20x realtime** (TTS-dominated, dense narration) |
| Result | ✅ succeeded — `output/rendered.mp4` 300.0s, 24.4 MB |
| Validation | PASS · Verification PASS · Cleanup (temp/job_0111 removed) |

> Ghi chú: video thử nghiệm là lời dẫn dày đặc (dense narration) — TTS edge-tts
> chiếm phần lớn thời gian. Video có ít lời hội thoại sẽ nhanh hơn đáng kể.

### Streaming pipeline benchmark (2026-08-18) — production path hiện tại

Toàn bộ ladder chạy lại bằng **`StreamingChunkPipeline`** (stage-decoupled pools)
qua driver `worker/tests/integration/e2e_chunked.py` — STT thật (faster-whisper
`small`, CPU), translation mock, TTS edge (dub), render + finalize thật.

| Rung | Source | Chunks | Chunked wall | Realtime | STT util | Identity | Output | Finalize |
|---|---|---|---|---:|---:|---:|---|---|
| 60s (no dub) | 60s | 2/2 | 19.67s | 3.05x | — | 16 seg PASS | 60.0s h264+aac | PASS, cleanup done |
| 60s (dub) | 60s | 2/2 | 27.12s | 2.21x | — | 16 seg PASS | 60.0s, voice track | PASS, cleanup done |
| 5 min (dub) | 300s | 10/10 | **75.62s** | **3.97x** | stt 3.16/4 (79%) | 86 seg PASS | 300.0s (38.8 MB) | PASS, cleanup done |
| 10 min (dub) | 600s | 20/20 | 238.11s | 2.52x | stt 2.34/4 | 157 seg PASS | 600.0s (77.9 MB) | PASS, cleanup done |
| **40 min (dub)** | 2400s | **80/80** | **660.03s** | **3.64x** | **stt 3.64/4 (91%)** | **615 seg PASS** | **2400.0s (310.9 MB)** | **PASS, cleanup done** |

So sánh với baseline **coupled pipeline** (cùng fixture 300s, cùng máy):
wall **97.56s → 75.49s** (−22.6%), STT utilization **2.42/4 (60%) → 3.16/4 (79%)**
— chunk xong STT nhường ngay slot STT cho chunk kế, hết tình trạng starvation
mà `PERFORMANCE_TRACE.md` ghi nhận. Ở 40 min, pool STT đạt **91%** full 4/4.

> Lưu ý: fixture là lời dẫn dày đặc (dense narration) — TTS edge-tts chiếm phần
> lớn thời gian wall; video ít lời sẽ nhanh hơn đáng kể. Realtime biến thiên
> theo tỷ lệ TTS/STT trên tổng thời lượng.

## 6. 40-minute benchmark (Phase 24)

**✅ PASS (2026-08-18, streaming pipeline)** — fixture 2400s (testsrc2 640×360@25
+ speech edge-tts), STT `small` CPU, dub edge `vi-VN-HoaiMyNeural`:

| Metric | Value |
|---|---|
| Chunks | 80/80 completed, 0 failed, 0 retries |
| STT segments | **615** (identity `seg_0..seg_614` 1:1 với translation, không trùng) |
| Timeline | first cue ≈ 9.1s, last 2400.0s — output duration chính xác **2400.0s** |
| Chunked wall | 660.03s (render thêm 67s; tổng E2E 802s ≈ 13.4 min) |
| Speed ratio | 2400 / 660 = **3.64x realtime** |
| STT pool utilization | avg **3.64/4 (91%)** |
| Output | `rendered.mp4` 310.9 MB, h264/aac 640×360@25, 0 ffprobe issues |
| QC | video-not-black (luma 123.75), audio-has-sound (max −2.3 dB) |
| Finalize | validation PASS · verification PASS · **cleanup done** (temp removed, `voice_track.wav` còn trong `cache/`) |
| EVIDENCE_DIR | `%TEMP%\tc_e2e_chunk_ay7aj3wx` (worker log, artifacts, MP4, trace) |

## 7. Failures & retries observed

- edge-tts `NoAudioReceived` transient (network) → chunk retry tự động, run vẫn
  hoàn thành (không fail cả pipeline).
- Silent chunk (không có speech) → xử lý hợp lệ, không đóng góp segment
  (không fail pipeline).
- Các bug phát hiện & sửa trong E2E: stream count type trong final_validation,
  duration source không ổn định (dùng probe thay vì ffmpeg out_time_us),
  finalize trả đủ field khi FAIL, manifest được giữ sau cleanup.
- **Streaming pipeline (2026-08-18):** 3 bug thật phát hiện qua unit test +
  E2E — (1) `_worker` gọi stage thiếu `ctx` → mọi stage thread crash, pipeline
  treo; (2) `_run_stt_stage` import `slice_audio` từ `media_service` (hàm nằm
  trong `chunk_service`) → `ImportError` ở run thật; (3) voice track streaming
  ghi vào temp tree → bị `finalize` xóa → đã move sang `cache/voice_track.wav`
  (durable). Cả 3 đều đã fix + retest (6 unit test mới + E2E ladder 60s→40min).

## 8. Cleanup verification

| Trường hợp | Kết quả |
|---|---|
| Run thành công (validation PASS + verified) | temp/job_XXXX bị xóa ✅ |
| Run failed (chunk fail / validation fail) | temp artifacts được GIỮ lại ✅ |
| Manifest | ghi vào `cache/chunk_manifest_{job_id}.json`, sống sót sau cleanup ✅ |
| Original video / output / cache | không bao giờ bị xóa ✅ |

## 9. Remaining blockers

- TTS (edge-tts) là bottleneck chính cho video lời dẫn dày đặc — ngoài phạm vi
  chunk pipeline (provider-side).
- Benchmark 40-min đã chạy PASS với streaming pipeline (§6). Với pipeline
  coupled cũ, rung 40-min (2918s bilibili) đã PASS ở FIX_REPORT 2026-08-18.
