# GOLDEN_VIDEO_TEST.md — Golden Video Regression Test

**Version:** 1.0.0
**Ngày:** 2026-08-09
**Base:** `MASTER_PLAN.md` (FROZEN V3) + `QUALITY_BENCHMARK.md`.
**Mục đích:** Định nghĩa **video mẫu chuẩn** + danh sách **checkpoint** để chạy regression toàn pipeline (Import → Probe → Extract → STT → Translation → Subtitle → Render → Export) và xác nhận chất lượng đầu ra.

> ⚠️ **TRẠNG THÁI:** Video mẫu chưa có → đánh dấu `TODO — CREATE GOLDEN VIDEO FIXTURE`. Tài liệu này định nghĩa **spec video cần có** + **checklist kiểm tra** để khi có fixture chỉ cần tạo file, không đổi quy trình.

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
