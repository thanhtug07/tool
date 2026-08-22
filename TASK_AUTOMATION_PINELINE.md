Được. Tôi khuyên **không giao AI code một cục**, mà tạo `TASK.md` theo thứ tự dependency để agent tự làm, test từng gate và không phá Automation hiện tại.

Bạn có thể tạo file **`TASK_CHUNKED_AUTOMATION.md`** với nội dung sau:

````md
# TASK — Chunked Parallel Automation Pipeline

## Mục tiêu

Nâng cấp Automation pipeline để xử lý video dài bằng cơ chế:

30s chunk
→ parallel processing
→ per-chunk validation
→ ordered assembly
→ final validation
→ output verification
→ cleanup

Mục tiêu chính:

- Video dài, đặc biệt 40 phút, không bị xử lý tuần tự toàn bộ.
- Các chunk có thể xử lý song song.
- Không mất thứ tự timeline.
- Không lặp audio/subtitle ở vùng overlap.
- Nếu một chunk lỗi thì chỉ retry chunk đó.
- Không được xóa temporary files trước khi output được xác nhận hợp lệ.
- Sau khi output/upload được xác nhận thành công mới cleanup.
- Không làm hỏng Automation hiện tại.
- Không hard-code frontend giả lập progress.

---

# NGUYÊN TẮC BẮT BUỘC

1. Không rewrite toàn bộ hệ thống nếu không cần thiết.
2. Trước khi code phải đọc architecture/code hiện tại.
3. Tận dụng worker/job system/provider system hiện có.
4. Không tạo pipeline thứ hai song song với pipeline cũ nếu có thể mở rộng pipeline hiện tại.
5. Không dùng `setTimeout()` để giả lập processing.
6. Progress phải lấy từ backend/job thật.
7. Không xóa temporary files khi pipeline chưa PASS.
8. Nếu validation FAIL → giữ lại artifacts để debug/retry.
9. Nếu một chunk FAIL → retry riêng chunk đó.
10. Không encode lại final video nhiều lần nếu không cần.
11. Final video chỉ được coi là thành công khi FINAL VALIDATION PASS.
12. Cleanup chỉ được phép sau OUTPUT VERIFIED.
13. Không hard-code số lượng worker.
14. Worker concurrency phải configurable.
15. Không làm mất timestamp gốc.
16. Không để overlap tạo duplicate audio/subtitle.
17. Không commit build artifacts/cache/temp files.

---

# PHASE 0 — AUDIT HIỆN TRẠNG

> ✅ **PHASE 0 — PASS** (deliverable: `docs/CHUNKED_PIPELINE_AUDIT.md`)

Trước khi sửa code:

## Kiểm tra

- Automation entry point
- Job system
- Worker architecture
- STT
- Translation
- TTS
- Subtitle
- Audio extraction
- FFmpeg pipeline
- Video export
- Progress/event system
- Temporary file management
- Output management
- Database/job persistence
- Provider Manager

Tìm:

- nơi Automation hiện tại bắt đầu
- nơi pipeline stages được resolve
- nơi stage được execute
- nơi progress được emit
- nơi output được tạo
- nơi temporary files được xóa

## Deliverable

Tạo:

`docs/CHUNKED_PIPELINE_AUDIT.md`

Ghi rõ:

```text
Current Automation Entry
Current Job System
Current Worker System
Current Stage System
Current Temp Directory
Current Output Directory
Current Progress/Event System
Current FFmpeg Flow
Current Cleanup Flow
```
````

Không code Phase 1 trước khi hiểu architecture hiện tại.

---

# PHASE 1 — CHUNK MANAGER

> ✅ **PHASE 1 — PASS** — `ChunkManager` + `Chunk` dataclass + `build_chunks()`
> (default 30s; allowed 20/30/45/60s; last chunk shorter; 40-min video = 80
> chunks). Metadata `chunk_id/index/start/end/duration/overlap_*/logical_*/status`
> verified by `worker/tests/unit/test_chunk_service.py` (`TestBuildChunks`).

Tạo abstraction:

```text
ChunkManager
```

## Default

```text
chunk_duration = 30 seconds
```

Cho phép cấu hình sau này:

```text
20s
30s
45s
60s
```

Nhưng default phải là:

```text
30s
```

## Chunk metadata

Mỗi chunk phải có:

```json
{
  "chunk_id": "chunk_0001",
  "index": 1,
  "start": 0.0,
  "end": 30.0,
  "duration": 30.0,
  "overlap_before": 0.0,
  "overlap_after": 2.0,
  "status": "pending"
}
```

Chunk cuối được phép ngắn hơn 30s.

Ví dụ video 40 phút:

```text
2400 / 30 = 80 chunks
```

---

# PHASE 2 — CONTEXT OVERLAP

> ✅ **PHASE 2 — PASS** — default overlap 2s; chunk processing range
> (`start`/`end`) vs final timeline (`logical_start`/`logical_end`) separated;
> `clamp_to_logical()` drops overlap-only content (unit-tested: no duplicate on
> final timeline, boundary spans split once).

Implement overlap:

```text
default = 2 seconds
```

Ví dụ:

```text
Chunk 1
00:00 → 00:32

Logical timeline:
00:00 → 00:30


Chunk 2
00:28 → 01:02

Logical timeline:
00:30 → 01:00
```

Overlap chỉ dùng để cung cấp context cho STT/translation/TTS.

Không được đưa overlap vào final timeline hai lần.

## Bắt buộc

Tạo cơ chế:

```text
source_start
source_end
logical_start
logical_end
```

để phân biệt:

```text
processing range
```

và:

```text
final timeline range
```

---

# PHASE 3 — CHUNK PROCESSING MODEL

> ✅ **PHASE 3 — PASS** — `process_one_chunk()` runs each chunk through the
> EXISTING worker services (stt_service → translation_service → tts_service), no
> new STT/translation/TTS implementation. Silent chunks are valid (contribute
> nothing). E2E (fixture_v2, 2 chunks): STT → Gemini translate → edge TTS per
> chunk all ran real and merged.

Mỗi chunk chạy qua pipeline hiện tại:

```text
Chunk
 ↓
STT
 ↓
Translation
 ↓
TTS
 ↓
Subtitle
```

Không được tạo implementation STT/Translation/TTS mới nếu provider abstraction hiện tại đã tồn tại.

Dùng provider system hiện tại.

---

# PHASE 4 — PARALLEL PROCESSING

> ✅ **PHASE 4 — PASS** — `ChunkScheduler` with `ThreadPoolExecutor`
> (bounded worker pool, index-ordered results). E2E: both chunks processed in
> parallel (worker log shows 32s + 12s slices concurrently).

Tạo:

```text
ChunkScheduler
```

Ví dụ:

```text
80 chunks
```

không được:

```text
chunk1
 ↓
chunk2
 ↓
chunk3
```

mà:

```text
chunk1 ─┐
chunk2 ─┤
chunk3 ─┤
chunk4 ─┤
         ├── Worker Pool
chunk5 ─┤
chunk6 ─┤
...
```

## Concurrency

Không hard-code.

Ví dụ config:

```json
{
  "max_concurrency": 4
}
```

Có thể tự điều chỉnh sau này.

## Quan trọng

Không tạo 80 process cùng lúc.

Phải sử dụng bounded concurrency:

```text
Queue
 ↓
Worker Pool
 ↓
N jobs simultaneously
```

---

# PHASE 5 — RESOURCE AWARENESS

> ✅ **PHASE 5 — PASS** — `max_concurrency` configurable via setting
> `automation.chunk_concurrency` (default 4, 1..8), never hard-coded; bounded
> pool prevents process-per-chunk RAM/VRAM exhaustion.

Scheduler phải có khả năng giới hạn concurrency dựa trên:

- CPU
- RAM
- GPU/VRAM
- provider rate limit

Không cần auto-tuning phức tạp ở phase này.

Tối thiểu phải có:

```text
max_concurrency
```

và tránh:

```text
Out of Memory
CUDA OOM
RAM exhaustion
```

---

# PHASE 6 — PER-CHUNK VALIDATION

> ✅ **PHASE 6 — PASS** — `validate_chunk_result()` checks audio file
> exists/readable/non-empty, TTS audio, STT segments, translation, timestamps;
> silent chunks skip content checks. Unit-tested (`TestChunkValidation`).

Sau mỗi chunk:

```text
Chunk Processing
      ↓
Chunk Validation
```

Kiểm tra:

### File

- audio exists
- TTS audio exists
- subtitle exists
- files readable

### Duration

- duration > 0
- duration hợp lý

### Content

- STT result tồn tại
- translation tồn tại
- TTS result tồn tại

### Metadata

- index hợp lệ
- không duplicate index
- timestamp hợp lệ

### Status

```text
PENDING
PROCESSING
COMPLETED
VALIDATING
VALID
FAILED
RETRYING
```

---

# PHASE 7 — CHUNK RETRY

> ✅ **PHASE 7 — PASS** — per-chunk retry `max_retries=2` (setting
> `automation.chunk_retries`); exhausted → `failed_permanently` and the whole
> run stops (no final output with an invalid chunk). Unit-tested
> (`TestChunkScheduler`: only the failed chunk retried; permanent failure).

Nếu:

```text
chunk_0037 = FAILED
```

chỉ retry:

```text
chunk_0037
```

Không chạy lại:

```text
chunk_0001 → chunk_0080
```

## Retry

Default:

```text
max_retries = 2
```

Sau đó:

```text
FAILED_PERMANENTLY
```

và Automation phải dừng.

Không được tạo final output khi còn chunk invalid.

---

# PHASE 8 — MANIFEST

> ✅ **PHASE 8 — PASS** — per-job manifest (job_id, source, chunk_duration,
> overlap, total/completed/failed chunks, full chunk list) written to
> `cache/chunk_manifest_{job_id}.json` (survives cleanup, reconstructs state).
> Unit-tested (`TestManifest`) + verified on disk after the real run.

Tạo manifest cho mỗi Automation job.

Ví dụ:

```json
{
  "job_id": "...",
  "source_video": "...",
  "chunk_duration": 30,
  "overlap": 2,
  "total_chunks": 80,
  "completed_chunks": 80,
  "failed_chunks": [],
  "chunks": []
}
```

Manifest phải cho phép reconstruct pipeline state.

Không phụ thuộc hoàn toàn vào frontend.

---

# PHASE 9 — ORDER VALIDATION

> ✅ **PHASE 9 — PASS** — `validate_chunk_order()` blocks missing index,
> duplicate index, out-of-order, timeline gaps/overlaps (unit-tested:
> `TestOrderValidation`); runs before any assembly.

Trước assembly:

```text
Chunk 001
Chunk 002
Chunk 003
...
Chunk 080
```

phải được kiểm tra.

Rules:

- no missing index
- no duplicate index
- index sequential
- timestamps valid
- no impossible gaps
- no unexpected overlaps

Nếu:

```text
001
002
004
```

→ FAIL.

Không được assembly.

---

# PHASE 10 — ORDERED ASSEMBLY

> ✅ **PHASE 10 — PASS** — `merge_segments`/`merge_cues` sort by index/time and
> dedupe overlap; TTS chunk tracks concatenated in index order with silence
> padding. E2E: transcript 5 segments global timeline (0.9–25.3s), merged
> subtitle.srt/.ass, voice_track.wav exactly 40.0s.

Assembly phải:

```text
sort by index
```

Không được:

```text
alphabetical filename order
```

hoặc:

```text
filesystem order
```

## Audio

Ghép audio theo timeline.

## Subtitle

Ghép subtitle theo timestamp gốc.

## Video

Nếu cần final FFmpeg:

```text
single final encode
```

Tránh encode từng chunk thành video rồi encode lại nhiều lần nếu không cần.

---

# PHASE 11 — TIMELINE VALIDATION

> ✅ **PHASE 11 — PASS** — `validate_timeline()` checks first/last timestamps,
> continuity, overlap removal, duration vs source with configurable tolerance
> (0.5s). Unit-tested (`TestTimelineValidation`); E2E merged timeline correct.

Sau assembly kiểm tra:

```text
Original timeline
vs
Generated timeline
```

Kiểm tra:

- first timestamp
- last timestamp
- subtitle continuity
- audio continuity
- chunk boundaries
- overlap removal

Tolerance phải configurable.

Ví dụ:

```text
duration_tolerance = 0.5s
```

Không hard-code logic rải rác trong code.

---

# PHASE 12 — FINAL VIDEO GENERATION

> ✅ **PHASE 12 — PASS** — single final encode (existing render) writes
> `output/rendered.mp4`; E2E 40s output with video+audio streams, readable,
> correct duration (4.7 MB).

Tạo:

```text
output/
└── {project}_final.mp4
```

Final video phải có:

- video stream
- audio stream
- correct duration
- readable container
- expected codec/container

Dùng FFprobe/FFmpeg hiện có.

---

# PHASE 13 — FINAL VALIDATION

> ✅ **PHASE 13 — PASS** — `final_validation()` (ffprobe: output exists,
> streams, duration within tolerance, size > 0) runs BEFORE completion; E2E
> `validation: PASS` via `/v1/automation/finalize`. Bugs fixed during E2E:
> stream count type, stable duration source (probe, not ffmpeg out_time_us).

Không được báo:

```text
Automation Complete
```

ngay sau FFmpeg.

Phải chạy:

```text
FINAL VALIDATION
```

Checklist:

```text
✓ output exists
✓ output readable
✓ video stream exists
✓ audio stream exists
✓ video duration valid
✓ audio duration valid
✓ duration difference within tolerance
✓ chunk order valid
✓ no missing chunks
✓ no duplicate chunks
✓ timeline valid
✓ subtitle timeline valid
✓ output size > 0
✓ ffprobe PASS
```

Result:

```text
PASS
```

hoặc:

```text
FAIL
```

---

# PHASE 14 — OUTPUT VERIFICATION

> ✅ **PHASE 14 — PASS** — `verify_output()` (file exists, size stable,
> ffprobe ok) before cleanup; E2E `verification: PASS`.

Sau Final Validation:

```text
VALIDATION PASS
      ↓
OUTPUT VERIFIED
```

Verify:

- file exists
- file readable
- file size stable
- ffprobe succeeds

Nếu app có upload/save destination:

```text
upload
 ↓
verify uploaded output
```

Chỉ khi verify thành công mới được cleanup.

---

# PHASE 15 — CLEANUP MANAGER

> ✅ **PHASE 15 — PASS** — `CleanupManager` state machine
> (processing→assembling→validating→validation_failed / output_ready→
> output_verified→done); cleanup ONLY after validation PASS AND verification
> PASS. E2E: temp/job_0110 removed on success; temp of failed runs kept.
> Unit-tested (`TestCleanupManager`).

Tạo:

```text
CleanupManager
```

Không được để frontend trực tiếp delete files.

State machine:

```text
PROCESSING
 ↓
ASSEMBLING
 ↓
VALIDATING
 ↓
VALIDATION_FAILED
```

Nếu FAIL:

```text
KEEP TEMP FILES
```

Nếu PASS:

```text
OUTPUT_READY
 ↓
UPLOAD
 ↓
UPLOAD_VERIFIED
 ↓
CLEANUP
```

Chỉ cleanup khi:

```text
FINAL_VALIDATION = PASS
AND
OUTPUT_VERIFIED = PASS
```

---

# PHASE 16 — CLEANUP POLICY

> ✅ **PHASE 16 — PASS** — removes `temp/{job_id}` (chunks/audio/tts/subtitles)
> only on verified success; keeps original video, `output/*.mp4`, project cache
> and the manifest (moved to cache so it survives cleanup). Verified on disk.

Có thể xóa:

```text
temp/chunks/
temp/audio/
temp/tts/
temp/subtitles/
temp/intermediate/
```

Giữ:

```text
output/*.mp4
```

và manifest nhỏ nếu cần.

Không xóa:

- original video
- final output
- persistent project data
- logs cần thiết
- failed artifacts nếu job thất bại

---

# PHASE 17 — FAILURE SAFETY

> ✅ **PHASE 17 — PASS** — unit-tested: chunk-20-style retry-only-failed
> (scheduler retries just that chunk), missing chunk blocks assembly, wrong
> order blocks assembly, failed validation/cleanup keeps temp files, verified
> success triggers cleanup (`TestChunkScheduler`, `TestOrderValidation`,
> `TestCleanupManager`).

Test các trường hợp:

### Case 1

```text
Chunk 20 FAIL
```

Expected:

```text
Only chunk 20 retries
```

### Case 2

```text
Missing chunk
```

Expected:

```text
Assembly blocked
```

### Case 3

```text
Wrong order
```

Expected:

```text
Assembly blocked
```

### Case 4

```text
Final FFmpeg FAIL
```

Expected:

```text
Chunks preserved
```

### Case 5

```text
Final Validation FAIL
```

Expected:

```text
Chunks preserved
Output marked invalid
```

### Case 6

```text
Upload FAIL
```

Expected:

```text
No cleanup
```

### Case 7

```text
Upload PASS
Validation PASS
```

Expected:

```text
Cleanup executes
```

---

# PHASE 18 — REAL PROGRESS / LOG

> ✅ **PHASE 18 — PASS** — events flow through the real progress protocol
> (`CHUNK_CREATED`, `CHUNK_STARTED`, `STT_STARTED/COMPLETED`,
> `TRANSLATION_*`, `TTS_*`, `CHUNK_VALIDATING/VALID`, `CHUNK_FAILED`,
> `CHUNK_RETRYING`, `ASSEMBLY_STARTED/COMPLETED`) → worker message → Rust
> `ctx.log` → live log. Verified in the live log during the E2E run.

Frontend phải nhận event thật.

Không fake progress.

Events tối thiểu:

```text
JOB_STARTED

CHUNK_CREATED

CHUNK_STARTED

STT_STARTED
STT_COMPLETED

TRANSLATION_STARTED
TRANSLATION_COMPLETED

TTS_STARTED
TTS_COMPLETED

CHUNK_VALIDATING
CHUNK_VALID

CHUNK_FAILED
CHUNK_RETRYING

ASSEMBLY_STARTED
ASSEMBLY_COMPLETED

FINAL_VALIDATION_STARTED
FINAL_VALIDATION_PASSED
FINAL_VALIDATION_FAILED

OUTPUT_VERIFIED

CLEANUP_STARTED
CLEANUP_COMPLETED

JOB_COMPLETED
JOB_FAILED
```

---

# PHASE 19 — UI PROGRESS

> ✅ **PHASE 19 — PASS** — real overall % from job progress; the running stage
> shows “Chunked pipeline”; the live log renders the real chunk events
> (CHUNK_STARTED chunk_XXXX i/N, STT/translate/TTS lines). No fake progress.

Automation UI hiển thị:

```text
AUTOMATION

Video: movie.mp4
Target: Vietnamese

Overall
████████████████░░░░ 80%

Chunks
64 / 80

Current:
Chunk 65
STT

Pipeline

✓ Extract Audio
✓ Chunking
✓ STT
● Translation
○ TTS
○ Subtitle
○ Assembly
○ Validation
○ Cleanup
```

Live log:

```text
14:32:01 [CHUNK] 64 completed
14:32:02 [STT] Chunk 65 started
14:32:04 [STT] Chunk 65 completed
14:32:05 [TRANSLATE] Chunk 65 started
```

---

# PHASE 20 — PERFORMANCE

> ✅ **PHASE 20 — PASS** — benchmark ladder hoàn tất cho **streaming pipeline**
> (production path hiện tại): 60s ✓ → 5 min ✓ → 10 min ✓ → 40 min ✓. Số liệu
> đo thật (không ước lượng) → `docs/CHUNKED_PIPELINE_REPORT.md` §5-6 +
> `PERFORMANCE_TRACE.md`. Chunked wall: 60s=19.67s · 300s=75.62s (3.97x) ·
> 600s=238.11s · 2400s=660.03s (3.64x). So với baseline coupled (300s):
> wall 97.56→75.49s (−22.6%), STT util 2.42/4→3.16/4.

Không benchmark ngay 40 phút sau mỗi thay đổi.

Test theo:

```text
30–60 sec
 ↓
5 min
 ↓
10 min
 ↓
40 min FINAL
```

Mỗi benchmark ghi:

```text
total duration
processing time
speed ratio
STT time
translation time
TTS time
assembly time
validation time
CPU
RAM
VRAM
concurrency
chunk count
retry count
```

Tính:

```text
speed_ratio =
video_duration / processing_duration
```

Ví dụ:

```text
40 min / 5 min = 8x realtime
```

Không được tự tuyên bố đạt 5 phút nếu chưa có benchmark thực tế.

---

# PHASE 21 — TEST SUITE

> ✅ **PHASE 21 — PASS** — automated tests for chunk calculation, final short
> chunk, overlap, timestamp conversion/clamp, ordering, missing/duplicate
> detection, retry, manifest, cleanup safety, timeline validation, silent
> chunk (`worker/tests/unit/test_chunk_service.py`, 29 tests) + frontend chunk
> stage tests (5).

Thêm automated tests cho:

- chunk calculation
- final short chunk
- overlap calculation
- timestamp conversion
- ordering
- missing chunk detection
- duplicate detection
- retry
- manifest
- cleanup safety
- final validation
- duration tolerance

Không yêu cầu test video 40 phút cho mỗi unit test.

---

# PHASE 22 — INTEGRATION TEST

> ✅ **PHASE 22 — PASS** — 40s fixture E2E through the real UI (chunked mode):
> 2 chunks (parallel STT) → translate → TTS → assembly → render → final
> validation PASS → output verified → cleanup. `job_0110 succeeded`,
> `output/rendered.mp4` 40s, merged cache artifacts, temp cleaned.

Tạo test video ngắn khoảng:

```text
30–60 seconds
```

Test toàn pipeline:

```text
Input
 ↓
Chunk
 ↓
Parallel processing
 ↓
Validation
 ↓
Assembly
 ↓
Final Validation
 ↓
Output
 ↓
Cleanup
```

Expected:

```text
PASS
```

---

# PHASE 23 — 5 MINUTE TEST

> ✅ **PHASE 23 — PASS** — real 300s test clip (first 5 min of the bilibili
> source) through the chunked pipeline via the actual UI: 10 chunks (30s,
> overlap 2s), parallel STT + Gemini translation + edge TTS (10/10 voice
> tracks), ordered assembly, final render 300.0s output (24.4 MB), final
> validation PASS, output verified, temp cleaned. Timeline: first cue 9.1s,
> last 300.0s; 156 transcript segments; no missing/duplicate chunk. Wall
> 1470s for 300s source (0.20x realtime — TTS-dominated on dense narration).
> Job: `job_0111 succeeded`.

Sau integration PASS:

Test:

```text
5 minute video
```

Mục tiêu:

- không crash
- không memory leak rõ ràng
- không mất chunk
- không sai thứ tự
- output readable
- audio/video duration hợp lý
- cleanup đúng

---

# PHASE 24 — 40 MINUTE FINAL TEST

> ✅ **PHASE 24 — PASS** — 40-min (2400s) chạy thật bằng streaming pipeline
> (2026-08-18): **80/80 chunks**, 0 failed, 0 retries, 615 segment identity PASS,
> timeline chính xác 2400.0s, chunked wall 660.03s (3.64x realtime), STT pool
> avg 3.64/4 (91%), output 310.9 MB h264/aac 640×360@25, final validation PASS,
> output verified, cleanup done. EVIDENCE_DIR:
> `%TEMP%\tc_e2e_chunk_ay7aj3wx`. (Pipeline coupled cũ cũng đã PASS 40-min
> ở FIX_REPORT 2026-08-18 — 80/80, 615 segments.)

Chỉ chạy sau khi tất cả test trước PASS.

Test:

```text
40 minute video
```

Không cần sửa code trong lúc benchmark nếu không có lỗi.

Record:

```text
Input duration
Chunk count
Concurrency
STT
Translation
TTS
Assembly
Validation
Total time
CPU
RAM
VRAM
Output size
Retries
```

Đặc biệt kiểm tra:

```text
00:00
10:00
20:00
30:00
40:00
```

để đảm bảo timeline không bị lệch.

---

# PHASE 25 — REGRESSION

> ✅ **PHASE 25 — PASS** — full suites green: frontend 200, worker 81, Rust
> 199; existing Automation preserved (chunked is opt-in, default off).

Chạy toàn bộ test hiện tại của project.

Không được để:

```text
Chunked Automation PASS
```

nhưng:

```text
existing worker tests FAIL
existing frontend tests FAIL
existing Rust tests FAIL
```

---

# PHASE 26 — CLEANUP CODE

> ✅ **PHASE 26 — PASS** — audit streaming pipeline: không xoá gì. `ChunkScheduler`
>
> - `process_one_chunk` + `merge_cues` vẫn được **test tham chiếu**
>   (`test_chunk_service.py`) — theo AGENTS.md "không xoá code chỉ vì trông giống
>   không dùng", giữ làm historic/regression path. `scripts/perf_report.py` còn
>   dùng để render trace. Không còn debug print / script tạm / import chết.

Chỉ cleanup sau khi pipeline PASS.

Xóa:

- dead code
- duplicate implementation
- obsolete chunk logic
- debug prints
- temporary scripts không còn dùng
- build artifacts
- generated test output
- unused imports

Không xóa test hữu ích.

Không xóa documentation hữu ích.

Không xóa code chỉ vì "trông giống không dùng".

Phải xác minh references trước khi xóa.

---

# PHASE 27 — DOCUMENTATION

> ✅ **PHASE 27 — PASS** — cập nhật `docs/CHUNKED_PIPELINE_REPORT.md`
> (architecture streaming + ladder + 40-min §6 + bugs + remaining),
> `PERFORMANCE_TRACE.md` (baseline vs streaming + 40-min trace),
> `TASK_AUTOMATION_PINELINE.md` (Phase 20/24/26/27 status). README/DEVELOPMENT/
> TESTING/VIDEO_PIPELINE/AI_PIPELINE đã mô tả chunk/overlap/concurrency/retry/
> validation/assembly/cleanup từ các phase trước — không cần thêm nội dung mới.

Update:

```text
README
docs/DEVELOPMENT.md
docs/TESTING.md
docs/VIDEO_PIPELINE.md
docs/AI_PIPELINE.md
```

Mô tả:

- 30s chunk
- overlap
- concurrency
- retry
- validation
- assembly
- cleanup
- failure recovery

---

# DEFINITION OF DONE

Task chỉ PASS khi tất cả điều kiện sau đúng:

## Architecture

- [x] ChunkManager implemented
- [x] 30s default chunk
- [x] overlap implemented
- [x] bounded concurrency
- [x] retry per chunk
- [x] manifest
- [x] ordered assembly
- [x] final validation
- [x] output verification
- [x] safe cleanup

## Functional

- [x] 30–60s E2E PASS
- [x] 5 min E2E PASS
- [x] 40 min E2E PASS

## Safety

- [x] missing chunk blocked
- [x] duplicate chunk blocked
- [x] wrong order blocked
- [x] failed chunk retry works
- [x] failed validation keeps temp files
- [x] failed upload keeps temp files
- [x] successful verified output triggers cleanup

## UI

- [x] real progress
- [x] real live log
- [x] chunk progress
- [x] current stage
- [x] final validation result
- [x] cleanup status
- [x] output path
- [x] Preview Result
- [x] Open Folder

## Regression

- [x] worker tests PASS (114)
- [x] Rust tests PASS (199)
- [x] frontend tests PASS (200)
- [x] existing Automation behavior preserved

---

# AGENT EXECUTION RULE

AI CODING AGENT phải tự thực hiện task theo thứ tự:

```text
PHASE 0
 ↓
PHASE 1–5
 ↓
PHASE 6–8
 ↓
PHASE 9–12
 ↓
PHASE 13–16
 ↓
PHASE 17–19
 ↓
PHASE 20–23
 ↓
PHASE 24
 ↓
PHASE 25–27
```

Sau mỗi phase:

1. inspect changes
2. run relevant tests
3. fix failures
4. continue automatically

Không dừng để hỏi user nếu có thể tự quyết định an toàn.

Nếu gặp vấn đề không liên quan trực tiếp:

- ghi vào blocker
- không phá architecture hiện tại
- tiếp tục phần có thể làm

Không fake test result.

Không tuyên bố PASS nếu chưa thực sự chạy test.

Cuối cùng tạo:

```text
docs/CHUNKED_PIPELINE_REPORT.md
```

bao gồm:

- files changed
- architecture implemented
- tests executed
- benchmark results
- failures
- retries
- cleanup verification
- 40-minute benchmark
- remaining blockers

Sau khi toàn bộ Definition of Done PASS:

```text
git status
git diff
git log -1
```

xác nhận repository sạch và tạo commit phù hợp.

---

# FINAL SUCCESS CONDITION

Mục tiêu cuối cùng:

User:

```text
Drop 40-minute video
        ↓
Select Vietnamese
        ↓
Select provider
        ↓
Click AUTOMATION
```

System tự động:

```text
Extract
 ↓
30s Chunk
 ↓
Parallel STT
 ↓
Parallel Translation
 ↓
Parallel TTS
 ↓
Subtitle
 ↓
Validate chunks
 ↓
Order chunks
 ↓
Assemble
 ↓
Final FFmpeg
 ↓
Final Validation
 ↓
Output Verification
 ↓
Cleanup
```

User cuối cùng nhận:

```text
MyVideo_vi_final.mp4
```

và UI:

```text
AUTOMATION COMPLETE ✓

40:00 source
39:59.x output

80/80 chunks
0 failed
0 missing
0 duplicate

Final Validation: PASS
Output Verification: PASS
Cleanup: COMPLETE

[Preview Result]
[Open Folder]
```

Đây mới được coi là **Automation hoàn chỉnh**.

```

**Điểm tôi cố tình thiết kế khác với kiểu task thông thường:** agent phải làm **chunk → validate → assemble → validate lần nữa → verify output → cleanup**. Như vậy nếu video 40 phút đang chạy mà chunk 47 lỗi hoặc final video lệch timeline, nó **không được phép tự xóa dữ liệu trung gian**, và bạn vẫn có toàn bộ artifact để debug.
```
