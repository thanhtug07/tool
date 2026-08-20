# PERFORMANCE_AUDIT.md — Đánh giá & đo hiệu năng Automation (chunked pipeline)

Ngày: 2026-08-19 · Môi trường: máy dev (local-development-only). Mọi con số trong tài liệu này đều được **đo thật** bằng `worker/tests/integration/e2e_chunked.py` (fixture thật: speech + video qua ffmpeg, chạy toàn bộ extract → chunked → render → QC → finalize) — không có số liệu bịa hoặc ước lượng suông.

## 1. Hardware & phần mềm

| Hạng mục | Giá trị đo |
|---|---|
| CPU | Intel Core i7-10850H @ 2.70 GHz — 6 physical / 12 logical cores |
| RAM | 31.7 GB |
| GPU | NVIDIA Quadro T1000 — 4 GB VRAM, driver 582.16 (CUDA) |
| Python | 3.13 (worker; mục tiêu deploy chốt 3.11 — không đổi) |
| STT | faster-whisper `small` |

## 2. Phương pháp

- Fixture: speech (edge-tts, fallback SAPI) + test-pattern video, đúng duration mục tiêu.
- Chunk: `chunk_duration=30s`, `overlap=2s`; provider `mock` (không có LLM → cô lập phần STT/TTS; dịch thật đã được phủ bởi unit test của shared-provider fix).
- Mỗi run chạy full chain + render (libx264 veryfast) + QC (video không đen, audio có âm thanh) + finalize. Identity check (transcript ids == translation ids, 1:1 đúng thứ tự) đều PASS.
- Số liệu theo stage (peak/avg_active workers, total_ms) lấy từ `build_performance_trace` — số thực đo trong pipeline, không suy diễn.
- `stt_workers`/`translate_workers`/`tts_workers` đặt bằng `--workers`; STT CPU budget = `cores // stt_workers` (đã có sẵn).

## 3. Ma trận kết quả (bước **chunked** — STT+dịch+TTS, chưa tính render)

| # | duration | device | workers | chunked (s) | RTF (thời gian / duration) | ghi chú |
|---|---|---|---|---|---|---|
| run1 | 60 s | CPU | 1 | 17.97 | 0.299 | baseline serial |
| run2 | 60 s | CPU | 4 | 15.85 | 0.264 | 2 chunks |
| run3 | 300 s | CPU | 4 | 74.27 | 0.248 | 10 chunks |
| run4 | 300 s | CPU | 1 | 88.35 | 0.294 | serial 10 chunks |
| run5 | 300 s | GPU (auto) | 4 | 49.26 | 0.164 | CUDA |
| run6 | 60 s | CPU | 4 | 20.93 | 0.349 | `--dub` piper (voice track thật) |
| run7 | 60 s | GPU (auto) | 4 | 9.00 | 0.150 | + metric sampler |

`RTF < 1` nghĩa là pipeline nhanh hơn thời gian thực của video (60 s video xử lý xong trong ~9–18 s tùy thiết bị).

### Metric peaks (run7, GPU, 60 s)

| Metric | Giá trị |
|---|---|
| CPU peak | 157 % (≈2 core) |
| RAM peak | 663 MB (worker process) |
| GPU util peak | 55 % |
| **VRAM peak** | **2.53 / 4 GB** |

→ 4 chunk STT đồng thời (small, float16) dùng ~2.5 GB VRAM: còn dư địa trên card 4 GB, không đổi tham số concurrency cho GPU.

## 4. Phân tích bottleneck theo stage (perf trace thực)

Từ run5 (300 s, GPU): `stt total_ms ≈ 178 s` dàn trên `peak_active 4 / avg_active 3.62`; `slice` và `translate` chiếm <1 % wall. Kết luận:

- **STT là bottleneck tuyệt đối** ở cả CPU lẫn GPU (mọi model chạy qua faster-whisper). Các stage còn lại (slice/translate/TTS) đều không nghẽn.
- **Parallel CPU 4 worker chỉ lời ~1.19×** (88.35 → 74.27 s) dù pool đầy (avg_active 3.57/4). Nguyên nhân: budget `12//4 = 3 threads/call` + overhead, nên 4 tiến trình STT cạnh tranh đủ 12 luồng — tăng workers trên CPU không tuyến tính. `stt_thread_budget` giữ hệ thống không oversubscribe (đúng thiết kế).
- **GPU là hướng chính**: 300 s: 49.26 s so với 88.35 s CPU serial (**1.79×**), RTF 0.164. Khi có CUDA, `stt_device=auto` nên được dùng.
- **Backpressure/assembly không bị nghẽn**: các queue giới hạn (`maxsize == pool size`) và ordered assembly buffer hoạt động — chunk_level avg_active 3.58/4 khi 4 workers, không có trường hợp pool "đói" bất thường.

## 5. Các fix hiệu năng đã triển khai (kèm vị trí code)

1. **Dùng chung translation provider cho cả run** — `chunk_service.py:_ensure_translation_provider` / `_stop_translation_provider`:
   - Trước: `build_translation_provider()` gọi **mỗi chunk** (`_run_translate_stage`). Với local/free provider, mỗi chunk sẽ spawn/kill một `llama-server` — phí khổng lồ khi có model.
   - Sau: build **1 lần**, lazy + lock (`_PROVIDER_LOCK`), dùng chung cho mọi translate worker; dừng đúng 1 lần sau khi pool cạn (finally, idempotent).
   - Thread-safety: `LocalLLMProvider.ensure_started()` giờ có `_start_lock` (không cold-start kép khi pool translate đổ vào cùng lúc).
2. **Cache PiperVoice theo ONNX path** — `tts_service.py:_PIPER_VOICE_CACHE` (LRU tối đa 8):
   - Trước: `PiperVoice.load()` mỗi **cue** → parse model ONNX (hàng trăm MB) từng dòng.
   - Sau: load 1 lần/voice/process, thread-safe (onnxruntime session), có eviction.
3. **Live Log không hiện duplicate liên tiếp** — `logHelpers.ts:isConsecutiveDuplicate` + dùng trong `LiveLog.tsx`:
   - Root cause #19: Rust dedupe message theo từng stage call; nhưng pool stage-decoupled có thể đẩy lại đúng một detail line không liền nhau — UI vẫn hiện trùng.
   - Fix: bỏ bớt dòng trùng **chính xác liền kề** (cùng level + message) ở lớp hiển thị; các lần lặp khác nhau vẫn giữ.

## 6. Khuyến nghị settings cho máy này

- `stt_device`: `auto` (có CUDA) — nhanh hơn ~1.8× so với CPU serial.
- `chunk_concurrency` / workers mặc định `4`: đủ lấp GPU và CPU; trên GPU VRAM peak 2.5 GB/4 GB — không cần giảm.
- Với CPU-only: giữ `stt_workers` ≤ `cores // 2` (budget đã tự điều chỉnh) — tăng nữa không lời.
- Provider `free`/`local`: fix (5.1) đảm bảo đúng **một** llama-server cho cả video, không phải mỗi chunk.
- Piper dubbing: đã có voice cache → nhiều cue không còn phí reload model.

## 7. Phạm vi & ngoài phạm vi

- Không thay đổi kiến trúc, scope MVP, hay tham số subtitle; không commit binary/model (vendor gitignored); không log secret.
- Chưa đo: hiệu năng translation thật trên GPU/llama-server (không có model LLM trên máy tại thời điểm đo) — đã phủ bằng unit test; TTS edge (cloud) khi có network.
