# BÁO CÁO KẾT QUẢ FIX (2026-08-18)

Báo cáo kết quả 5 bug fix theo scope đã duyệt + E2E thật + kiểm tra final MP4.
Mọi số liệu đều đo được từ các lần chạy thật (không ước lượng).

## 1. Tóm tắt

| #    | Bug                                                         | Trạng thái                         | Điểm chứng minh                                   |
| ---- | ----------------------------------------------------------- | ---------------------------------- | ------------------------------------------------- |
| P0-1 | Segment identity/timeline (chunked)                         | ✅ Fix                             | `e2e_chunked.py` 5 cấp thời lượng, identity 1:1   |
| P0-2 | FFmpeg audio `-map` + watermark                             | ✅ Fix (gồm bug đồng hành `[1:v]`) | Unit + E2E dub + image watermark (pixel-check đỏ) |
| P0-3 | Output directory → `E_RENDER_INVALID`                       | ✅ Fix                             | Unit test mkdir fail                              |
| P0-4 | TTS default voice sai ngôn ngữ (piper)                      | ✅ Fix                             | Unit test mới                                     |
| P1-5 | TranslationMemory thiếu `rules_ver`                         | ✅ Fix                             | Unit test mới                                     |
| P1-6 | Extract `duration_seconds` ×1000 (ffmpeg 9.0 `out_time_ms`) | ✅ Fix                             | Đã verify thật: 30s → 30.0 (trước 30000.0)        |

- Worker `pytest tests/unit`: **102 passed** (trước fix 83) — chạy lại sau mọi thay đổi.
- Worker import smoke: **OK**.
- Không sửa frontend/Rust → không có gate nào của 2 layer này bị ảnh hưởng.

## 2. Chi tiết từng fix

### P0-1 — Segment identity (worker/src/services/chunk_service.py)

**Nguyên nhân:** assembly cũ build transcript qua `merge_segments` (dedup theo `(start,end,source_text)` + renumber) NHƯNG build translation/subtitle qua `merge_cues` (dedup theo `(start,end,translated_text)` + renumber **độc lập**). Cue bị skip (translation rỗng → drop tại `process_one_chunk`) làm `seg_N` lệch toàn bộ từ dòng đó trở đi; `subtitle_service.from_transcript_and_translation` match theo `segment_id` strict → lỗi/thiếu.

**Fix:** transcript merged là nguồn duy nhất của identity. Mỗi segment gán `(chunk_id, src_idx)` từ `process_one_chunk`; `assemble_translations(segments, per_chunk)` ghép translation theo cặp identity này, translation thiếu → fallback source text. Translation blocks + subtitle cues build **từ chính list segments** (cùng id, 1:1). ID vẫn giữ format `seg_N` (schema `^seg_[0-9]+$`). Thêm regression test `test_identity_alignment_when_a_cue_is_dropped` (4 seg nhập, 1 cue drop → 4 translation, fallback đúng).

### P0-2 — Audio `-map` + watermark (worker/src/services/render_service.py)

**Nguyên nhân gốc:** `-map` audio tính bằng `args.count('-i') - 1` → khi image watermark input thêm SAU audio, index ra `2:a` (map nhầm streams của ảnh, mất audio).

**Fix phần 1:** `build_render_args` track `audio_input_index = 1` tường minh → `-map 1:a` ổn định.

**Bug đồng hành phát hiện trong E2E (fix luôn):** `build_filter_graph` gắn cứng `[1:v]` cho watermark image. Khi có replacement audio (voice track / audio process) ở input 1, ảnh nằm ở **input 2** → graph đọc streams của WAV → ffmpeg lỗi `matches no streams` (đã chứng minh: variant A có `[1:v]`+không audio = RC 0; B có `[1:v]`+audio = RC -22; C `[2:v]`+audio = RC 0). Fix: thêm `image_index` cho `build_filter_graph`; `render()` truyền `2 if audio_path else 1`.

**E2E xác nhận:** `e2e_pipeline.py --duration 30 --dub --watermark-image <png>` → PASS, MP4 có h264 + aac, watermark đỏ ở top-left **(avg RGB 200,50,0 — pixel-check)**.

### P0-3 — Output directory (render_service.py)

`tempfile.mkdtemp(dir=output_parent)` ngoài try → `FileNotFoundError` thô khi thiếu thư mục. Fix: `output_parent.mkdir(parents=True, exist_ok=True)` + except `OSError` → `RenderError(E_RENDER_INVALID, "Output directory is missing or not writable.")`. Test: parent tồn tại dưới dạng FILE → mkdir fail → code đúng.

### P0-4 — TTS locale (worker/src/services/tts_service.py)

`_DEFAULT_VOICE` trước đây set piper en/ja/ko → `vi_VN-vais1000-medium` và `_DEFAULT_VOICE_FALLBACK` đổ về giọng Việt — mọi ngôn ngữ không có default đều bị dub bằng tiếng Việt. Fix: `_DEFAULT_VOICE` chỉ list giọng native (vi/zh có edge+piper; en/ja/ko/fr/de/es chỉ edge); `_DEFAULT_VOICE_FALLBACK = {}` → ngôn ngữ không hỗ trợ cho engine → `E_TTS_UNAVAILABLE` rõ ràng. Cập nhật 2 test cũ đang assert hành vi bug; thêm test raise.

### P1-5 — TranslationMemory rules_ver (worker/src/services/translation_service.py)

TM key thiếu `rules_ver` (trong khi `cache.tr_key` đã có). Fix: `rules_version()` (sha256 sorted rules, empty→"none"); `_key/get/put/save/load` đều có `rules_ver`; `load` backward-compat `e.get("rules_ver","none")`; `translate_segments`/`_store` thread `rules_v`. Test: đổi rules → miss; giữ rules → hit; roundtrip; load dữ liệu cũ không field vẫn nạp.

## 3. E2E — bằng chứng đo được

### Chunked (mới: worker/tests/integration/e2e_chunked.py — driver /v1/automation/chunked)

| Duration | Chunks | Segments | Identity `seg_N` (count/order/dupe) | MP4 (ffprobe)                        | Finalize           |
| -------- | ------ | -------- | ----------------------------------- | ------------------------------------ | ------------------ |
| 30s      | 1/1    | 8        | PASS                                | h264/aac 640×360@25, 30.0s, 0 issues | PASS, cleanup done |
| 60s      | 2/2    | 16       | PASS                                | 60.0s, 0 issues                      | PASS               |
| 5 min    | 10/10  | 83       | PASS                                | 300.0s, 0 issues                     | PASS               |
| 10 min   | 20/20  | 158      | PASS                                | 600.0s, 0 issues                     | PASS               |
| 40 min   | 80/80  | 615      | PASS                                | 2400.0s, 0 issues (325 MB)           | PASS               |

Identity = transcript `id` == translation `segment_id`, đúng thứ tự, không trùng, tuần tự `seg_0..seg_n`. EVIDENCE_DIR (worker log, artifact, MP4): `%TEMP%\tc_e2e_chunk_oincip6o` (40 phút), `tc_e2e_chunk_cq2z17r7` (10 phút), `tc_e2e_chunk_o2a0tjj6` (5 phút), `tc_e2e_chunk_ig87i3to` (60s), `tc_e2e_chunk_5d_mrpox` (30s).

### Single-shot (worker/tests/integration/e2e_pipeline.py — đã có sẵn, thêm flag `--watermark-image`)

- 30s: PASS, 15.07s.
- 60s: PASS, 21.62s.
- Dub + image watermark 30s: PASS, 36.37s — **final MP4**: h264/aac 640×360@25, 30.0s, 0 issues, watermark đỏ xác nhận bằng pixel (avg RGB 200,50,0).

### P1-6 — Extract `duration_seconds` ×1000 (worker/src/core/ffmpeg.py) — fix theo xác nhận "tiếp tục"

Bản ffmpeg 9.0 đang dùng ghi `out_time_ms=30000000` cho file 30s (giá trị là microsecond, không chuyển về ms theo quy ước). `run_ffmpeg` đọc lần lượt từng dòng `out_time_us` rồi `out_time_ms`; dòng `out_time_ms` về sau thắng → `out_time_seconds` trả 30000s → extract báo `duration_seconds: 30000.0`.

**Fix:** `out_time_seconds` chỉ tin `out_time_us` (key chuẩn hiện diện trong mọi build ffmpeg), bỏ hẳn fallback `out_time_ms` — vì chính key này của bản 9.0 bị sai bậc nên không thể dùng `/1000`. Thêm `worker/tests/unit/test_ffmpeg_progress.py` (8 test: us đúng, ms bẩn bị bỏ qua, không us → None, v.v.).

**Verify thật:** chạy lại `e2e_pipeline.py --duration 30` → `extract_audio.duration_seconds = 30.0`, `reported_duration = 30.0`, `VALIDATION: PASS` (trước fix: 30000.0). Toàn bộ `pytest tests/unit` = 102 passed, smoke OK. Không ảnh hưởng chunk math (chunked vốn dùng ffprobe duration, không dùng số này).

## 4. Ghi nhận còn lại (chưa có phạm vi)

Tất cả bug tìm được đã đóng. Các mục còn lại chỉ là note chưa nằm trong scope fix:

- P1-7 (LiveLog dup IDs), P2-8..11 (AutomationOptions, STAGE_LABELS, EMPTY_PROJECT, ConfirmDialog Escape): theo thỏa thuận vẫn HOÃN.

## 5. Files thay đổi

Sửa code:

- `worker/src/services/chunk_service.py` (P0-1)
- `worker/src/services/render_service.py` (P0-2 + P0-2b + P0-3)
- `worker/src/services/tts_service.py` (P0-4)
- `worker/src/services/translation_service.py` (P1-5)
- `worker/src/core/ffmpeg.py` (P1-6 — bỏ fallback `out_time_ms`)

Tests:

- `worker/tests/unit/test_render_fixes.py` (mới — P0-2/P0-3, xác nhận `[1:v]`/`[2:v]`, `-map 1:a`)
- `worker/tests/unit/test_translation_memory.py` (mới — P1-5)
- `worker/tests/unit/test_chunk_service.py` (thêm identity-drop test)
- `worker/tests/unit/test_tts_voice_library.py` (sửa assert cũ theo hành vi đúng)
- `worker/tests/unit/test_ffmpeg_progress.py` (mới — P1-6)

Integration:

- `worker/tests/integration/e2e_chunked.py` (mới — driver chunked + identity check)
- `worker/tests/integration/e2e_pipeline.py` (thêm `--watermark-image`)

## 6. Ghi chú phạm vi

- `npm run format:check` vẫn đỏ cho **20 file có sẵn** (WIP) — không thuộc scope fix; không đụng.
- P1-6 (pin Python dev=CI 3.11), P1-7 (LiveLog dup IDs), P2-8..11 (AutomationOptions, STAGE_LABELS, EMPTY_PROJECT, ConfirmDialog Escape): theo thỏa thuận vẫn HOÃN.
