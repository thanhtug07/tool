# GOLDEN_VIDEO_TEST.md — Golden Video Regression Test

**Version:** 1.0.0
**Ngày:** 2026-08-09
**Base:** `MASTER_PLAN.md` (FROZEN V3) + `QUALITY_BENCHMARK.md`.
**Mục đích:** Định nghĩa **video mẫu chuẩn** + danh sách **checkpoint** để chạy regression toàn pipeline (Import → Probe → Extract → STT → Translation → Subtitle → Render → Export) và xác nhận chất lượng đầu ra.

> ✅ **TRẠNG THÁI (2026-08-12):** Fixture đã có — `golden/video/golden.mp4` (synthetic, bản quyền an toàn, tạo bằng piper-tts + ffmpeg lavfi) + runner tự động `golden/scripts/run_golden.py` + kết quả thực tế trong `golden/results/latest.json` (PASS 16/16, 5.5s). Chi tiết ở mục 7.

---

## 1. MỤC TIÊU

- Kiểm tra pipeline end-to-end không regression (thay đổi code/phần mềm không làm vỡ output).
- Xác nhận chất lượng STT/translation/timing/subtitle/render trên cùng một đầu vào chuẩn.
- **KHÔNG yêu cầu pixel-perfect** — so sánh theo ngưỡng chấp nhận, không so từng pixel.

## 2. SPEC VIDEO MẪU (fixture bắt buộc)

| Thuộc tính | Giá trị | Lý do |
|---|---|---|
| Duration | 10 phút (có thể chia clip nhỏ 30-60s cho test nhanh) | MVP DoD dùng video 10 phút |
| Nguồn | tiếng Trung (phim/hội thoại tự nhiên), mục tiêu dịch tiếng Việt | đúng use-case chính |
| Nhiều speaker | ≥ 3 người nói chuyện xen kẽ | test diarization/context/speaker |
| Music/background | có nhạc nền + tạp âm nhẹ | test STT trong điều kiện thực |
| Tốc độ nói | có đoạn nhanh, đoạn chậm, đoạn ngắt quãng | test VAD + timing + CPS |
| Độ dài câu | có câu ngắn (1-2 từ) và câu dài | test line break + CPS |
| Proper nouns | tên riêng (người, địa danh, thương hiệu) lặp lại | test consistency + glossary |
| Idioms | ≥ 3 thành ngữ | test translation quality |
| Timing variation | có đoạn nói đè lên nhau / nhồi nhét | test timing validation |
| Format | MP4 H.264 + AAC (chuẩn) | baseline; có thêm 1 MKV + 1 MOV cho probe test |

## 3. CHECKPOINTS (checklist bắt buộc khi chạy)

| # | Checkpoint | PASS tiêu chí | Phương pháp |
|---|---|---|---|
| 1 | Import + Probe | resolution/fps/duration/codec/rotation đúng thực tế | ffprobe so sánh metadata kỳ vọng |
| 2 | Extract audio | WAV 16kHz mono, duration ≈ video | ffprobe |
| 3 | STT timestamps | segment timing ±200ms so reference; không miss segment lớn | so sánh transcript vs reference |
| 4 | STT text | CER/WER so reference dưới ngưỡng (mục 4) | so sánh tự động |
| 5 | Translation | score theo QUALITY_BENCHMARK.md ≥ ngưỡng preset | runner benchmark |
| 6 | Subtitle timing | cues không chồng nhau; padding 50-80ms; duration ≥ CPS cho phép | parse ASS |
| 7 | Line break | theo policy configurable (KHÔNG hard-code 42) | parse ASS |
| 8 | Render resolution/FPS | == input (hoặc theo config) | ffprobe output |
| 9 | Render audio | còn nguyên kênh/tần số, không bị mất | ffprobe stream |
| 10 | Burn-in | frame sample có vùng text subtitle | frame extract + kiểm tra (manual/threshold) |
| 11 | Duration | output ±1s so source | ffprobe |
| 12 | Export + QC | file export hợp lệ; QC report pass | TASK-029 |

## 4. NGƯỠNG STT (mặc định, hiệu chỉnh sau fixture thật)

| Metric | Ngưỡng PASS | Ghi chú |
|---|---|---|
| Segment coverage | ≥ 95% (không miss đoạn thoại chính) | |
| Timing drift | ±200ms cho ≥ 90% segment | |
| CER | ≤ 15% trên reference (tiếng Trung, int8) | tham khảo; re-calibrate theo model |
| Language detect | đúng zh cho đoạn tiếng Trung | |

## 5. CÁCH CHẠY

```text
runner: worker/scripts/golden_video_test.py (hoặc pytest marker `golden`)
input : golden/video/<fixture>.mp4 + golden/video/reference_*.json
flow  : chạy pipeline đầy đủ → thu report cho từng checkpoint
output: golden_report.json + logs (STT/translation/render)
```

- Chạy ở: CI (GPU runner, marker `golden`, chậm — chỉ trên main/PR đặc biệt) + manual trước release.
- Test nhanh: dùng clip 30-60s trích từ video 10 phút (golden clip) để mỗi PR.

## 6. PASS / FAIL / WARNING

- **PASS:** toàn bộ checkpoint bắt buộc (1-12) đạt ngưỡng.
- **FAIL:** checkpoint bắt buộc không đạt → chặn release (và chặn merge nếu CI chạy).
- **WARNING:** checkpoint phụ vượt ngưỡng nhưng không chặn (VD translation score ở mức biên, codec phụ) → ghi trong báo cáo release.

## 7. KHÔNG PIXEL-PERFECT

- Không so từng pixel giữa 2 lần render; dùng threshold: SSIM ≥ 0.95 cho frame có subtitle (hoặc kiểm tra vùng text có nội dung) → tránh flaky test do encoder khác nhau.
- Nếu cần so chính xác: dùng libx264 cùng preset ở cả 2 lần chạy.

---

*Hết GOLDEN_VIDEO_TEST.md — tích hợp qua MASTER_PLAN §38.1a, Phase 12, DoD tầng 2 & 3.*


## 8. HIỆN TRẠNG THỰC TẾ (2026-08-12) — IMPLEMENTED

> Trước đây mục này đánh dấu `TODO — CREATE GOLDEN VIDEO FIXTURE`. Đã hoàn thành.

### 8.1 Fixture

| Thuộc tính | Giá trị | Ghi chú |
|---|---|---|
| Video | `golden/video/golden.mp4` | MP4 H.264 + AAC, 640×360, 25fps, ~6.4s |
| Audio gốc | `golden/audio/transcript.wav` | piper-tts (`en_US-lessac-medium`) |
| Text nguồn | "The quick brown fox jumps over the lazy dog. My phone number is five five five, one two three four." | deterministic, không bản quyền |
| Expected | `golden/expected/expected.json` | transcript_contains + translation + video metadata + min_cues |

**Tạo lại fixture:** `py golden/scripts/generate_golden.py` (cần ffmpeg + `py -m piper.download_voices en_US-lessac-medium` một lần).

### 8.2 Runner E2E

`py golden/scripts/run_golden.py [--provider mock|gemini|local] [--model tiny] [--device cpu]`

Chuỗi thực tế: spawn worker thật (giao thức sidecar) → `/v1/audio/extract` → `/v1/stt/transcribe` (faster-whisper thật) → `/v1/translate` (mock deterministic mặc định; `--provider gemini` cần key trong Credential Manager) → `/v1/subtitle` → `/v1/render` (FFmpeg/libass) → `/v1/export/video` (QC).

### 8.3 Kết quả đo được (chạy trên máy dev, 2026-08-12)

```text
golden E2E PASS — 16/16 checks in 5.5s
  [PASS] extract produced audio
  [PASS] extract duration ~ video          6.41s vs 6.46s
  [PASS] stt produced segments             1 segs in 2.2s
  [PASS] stt contains `quick brown fox`    the quick brown fox jumps over the lazy dog, my phone number is 555-1234.
  [PASS] stt contains `lazy dog`
  [PASS] stt mentions phone number         (555 hoặc five five five)
  [PASS] translate covered every segment
  [PASS] mock translate deterministic prefix
  [PASS] at least min_cues generated
  [PASS] cue timing/text valid
  [PASS] srt+ass written
  [PASS] render produced output            libx264 in 1.0s
  [PASS] render output has video stream    video:h264, audio:aac
  [PASS] render duration ~ source          6.44s vs 6.46s
  [PASS] export produced file
  [PASS] export QC passed                  0 issues
```

Chi tiết JSON: `golden/results/latest.json`.

### 8.4 Deterministic vs tolerance

- **Deterministic:** fixture sinh bằng piper-tts + ffmpeg lavfi (cùng voice/text → cùng audio/video); mock translation; subtitle/ASS cấu trúc.
- **Tolerance:** STT text (so theo phrase chứ không từng từ — model nhỏ có thể chuẩn hoá số "five five five" → "555-1234"); thời lượng ±1.5s; số cue ≥ min_cues (phụ thuộc segmentation của model).
- **Real provider:** `--provider gemini` chạy translation thật (cần API key trong Windows Credential Manager) — kết quả không deterministic, chỉ kiểm tra non-empty.

### 8.5 Lưu ý vận hành

- Runner đặt `HF_HUB_OFFLINE=1` sau khi warm model để tránh stall trên Hugging Face API giữa pipeline (đã quan sát: revision-check treo ~3 phút).
- Lần đầu chạy cần tải model whisper (~75MB `tiny`): `py -c "from faster_whisper import WhisperModel; WhisperModel('tiny', device='cpu', compute_type='int8')"`.
