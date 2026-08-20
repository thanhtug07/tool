# PIPELINE PERFORMANCE PROFILE — Đo từng stage độc lập

Ngày: 2026-08-19 · Máy dev: i7-10850H 6C/12T, 32 GB RAM, Quadro T1000 4 GB (driver 582.16).
Mọi con số = đo thật bằng `worker/tests/integration/e2e_chunked.py` (fixture ffmpeg thật + speech, full chain extract → chunked → render → finalize, identity check PASS). **STT không bị tối ưu thêm trong pha này.**

## 1. Cách đo

- Bên trong worker: `build_performance_trace` ghi từng stage của chunk (slice/stt/translate/tts: **total_ms**, **concurrency peak/avg** theo wall-overlap, và mới bổ sung **queue wait** — thời gian chunk nằm chờ trong queue giới hạn của từng stage, cho biết mức backpressure). Các phase hậu-STT (**validate, merge/assembly, subtitle, voice, ghi artifact, manifest**) được bấm giờ `time.monotonic()` quanh từng block trong `run_chunked_pipeline` và lưu vào `trace["phases"]` + `manifest.perf.phases`.
- Ngoài worker: harness bấm giờ từng HTTP call `extract_audio` / chunked / `render` / `finalize`, kèm `MetricSampler` (CPU/RAM/GPU/VRAM peaks) khi `--metrics`.
- Queue wait = chênh lệch `ready` (upstream đưa vào queue) → lúc worker nhặt. `slice` không phải queue riêng (chạy trong worker STT) nên wait = 0.

## 2. Profile chuẩn (300 s video, 10 chunk × 30 s, động từng stage — CPU)

| Stage | Input | Wall (s) | RTF | Concurrency | Queue wait (avg/chunk) | Resource đo được |
|---|---|---|---|---|---|---|
| Extract audio | 300 s video | 0.24 | 0.001 | serial | — | ~0 CPU |
| **STT (chunked)** | 10×30 s wav | **74.06** | 0.247 | peak 5 / avg 3.58 | **20.1 s** | CPU 586 %* |
| Translation (mock → #4) | 85 segments | ~0.0 | ~0 | peak 1 | 0.1 ms | — |
| Subtitle gen | 85 segments | 0.014 | ~0 | serial | — | — |
| Audio/voice assembly | — (no dub) | — | — | — | — | — |
| File I/O (artifacts) | ~2 MB | 0.003 | ~0 | serial | — | — |
| Validation (10 chunk + timeline) | 10 artifacts | 0.001 | ~0 | serial | — | — |
| Render (ffmpeg libx264) | 300 s + subs | 7.27 | 0.024 | 1 | — | GPU n/a |
| Finalize / QC | 300 s mp4 | 1.09 | 0.004 | serial | — | — |
| **Assembly (merge+translate-pair)** | 10 chunks | ~0.0 | ~0 | serial | — | — |

*CPU 586 % từ run dub-piper 60 s (2 chunk song song); run này nối tiếp vì 4 người STT tranh 12 luồng.

**TOTAL (CPU): 0.24 + 74.06 + 0.014 + 0.003 + 0.001 + 7.27 + 1.09 ≈ 82.7 s** cho 300 s video → **RTF tổng 0.276**.

## 3. So sánh thiết bị (cùng 300 s, 10 chunk, workers=4)

| | CPU | GPU (auto) |
|---|---|---|
| STT chunked (s) | 74.06 | **46.19** |
| queue wait STT avg/chunk | 20.1 s | **10.8 s** |
| Render (s) | 7.27 | 9.87 |
| GPU util peak | 0 % | 99 % |
| VRAM peak | 0 | 2.63 / 4 GB |
| CPU util peak | ~150–580 % | 148 % ✓ (thả CPU) |
| **TOTAL ≈** | 82.7 s (RTF 0.276) | **57.4 s (RTF 0.191)** |

## 4. Đo riêng từng nhánh nặng (theo yêu cầu)

**Translation (11) — real path, provider `local` (OpenAI-compat stub qua loopback):**
- 300 s: `translate total_ms 198 ms` cho 10 chunk (HTTP thật, 1 provider dùng chung — fix pha 1), `peak_active 1`, queue wait ~0.1 ms.
- Network latency: loopback ≈ 0 (không phải bottleneck ở stub).
- **Chưa đo được**: latency LLM thật (gemini/llama-server) — trên máy không có model/key tại thời điểm đo. Với mock provider (default) translate ≈ 0 vì không có network/LLM.

**TTS (12) — run dub piper 60 s, 2 chunk song song:**
- `tts total_ms 7355 ms` (7.4 s) cho 16 cue/2 chunk, peak_active 2, avg 0.32, queue wait ~0.1 ms.
- Voice assembly (move track): 2 ms. Render có voice track: 2.47 s.
- PiperVoice cache (fix pha 1) giữ load = 1/voice; RTF TTS ≈ 0.12 (7.4 s/60 s).

**Audio assembly / Subtitle / File I/O / Validation:** toàn bộ cộng lại **< 30 ms** trên 300 s run (trace `phases_wall_s 0.012–0.024 s`) — **không phải bottleneck**, không tối ưu thêm.

**FFmpeg (render):** 300 s → 7.27 s encode libx264 veryfast (P1) / 9.87 s (P2, GPU đang bận STT). Extend audio slice: `slice total_ms ~0.9 s/10 chunk`. Đây là toàn bộ chi phí FFmpeg trong pipeline; chiếm < 10 % wall.

**RAM:** worker peak 663–854 MB (STT + STT+dub). VRAM: only GPU runs (2.5–2.6 GB).

## 5. Kết luận

1. **STT chiếm > 85 % tổng wall** (74/82.7 s CPU; 46/57.4 s GPU) — bottleneck tuyệt đối, đã được pha 1 xử lý (thread budget, shared model, GPU), pha này **giữ nguyên**.
2. Mọi stage còn lại (translation, tts, assembly, subtitle, file I/O, validation) cộng < 1 s trên 300 s video — **không có việc gì để tối ưu** theo số liệu thực; không "tối ưu theo cảm tính".
3. Queue wait trung bình 10–20 s/chunk ở STT = pool 4 worker **đầy liên tục** (avg_active 3.58/4), backpressure hoạt động đúng, không có chunk "đói" — tăng thêm workers chỉ tốn CPU (đo: 4 worker ≈ 5.9 core).
4. Khuyến nghị: chạy chunked với `stt_device=auto` khi có GPU; giữ workers=4; nếu CPU-only và video dài, 4 workers vẫn tốt hơn 1 (~+19 % nhưng RAM ổn).