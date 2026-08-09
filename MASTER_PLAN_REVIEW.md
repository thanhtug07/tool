# MASTER_PLAN_REVIEW.md — Audit & Đánh giá MASTER_PLAN

**Version:** 1.0.0
**Ngày:** 2026-08-09
**Đối tượng audit:** `MASTER_PLAN.md` (2,109 dòng, 45 sections)
**Phương pháp:** Đọc toàn bộ tài liệu → audit tính nhất quán nội bộ → đối chiếu thông tin nhạy cảm thời gian với nghiên cứu Internet (2026) → phân loại Issue → đề xuất sửa.
**Nguyên tắc:** Không code. Chỉ review/audit/validate. Mọi thông tin model/API/license/pricing đều verify trước khi chốt.

---

## 1. KẾT LUẬN TỔNG THỂ (Executive Summary)

| Hạng mục | Đánh giá | Chi tiết |
|---|---|---|
| **Architecture** | ✅ **APPROVED** | Tauri 2 + Python sidecar + SQLite + FFmpeg là đúng đắn. 2 mitigation bổ sung cho whisper.cpp Vulkan. |
| **MVP Scope** | ✅ **APPROVED** | Transcribe → Translate → Subtitle → Render → Export. Không dính scope creep (MUST NOT sạch). |
| **Provider Strategy** | ⚠️ **NEEDS CHANGES** | Gemini 2.0 Flash **đã shutdown (1/6/2026)** — phải thay bằng Gemini 2.5 Flash-Lite/2.5 Flash. Cập nhật default model trong schema. |
| **Security** | ✅ **APPROVED** | Credential Manager + token loopback + argument array đúng. Bổ sung 2 khuyến nghị. |
| **Licensing** | ✅ **APPROVED** (có lưu ý) | ProPainter/XTTS v2/F5-TTS loại bỏ đúng. **Cờ đỏ mới:** fine-tune Kokoro tiếng Việt (community) có license mơ hồ (provenance CC-BY-NC-SA) — không dùng thương mại khi chưa verify. |

**Kết luận:** MASTER_PLAN vững chắc, nhất quán về ý tưởng. Cần **sửa 2 lỗi schema đã deprecated** (Gemini 2.0 Flash), **thống nhất 2 danh sách API** (POST vs GET /health, naming), **bổ sung 3 mitigation** cho whisper.cpp Vulkan và **làm rõ chiến lược TTS tiếng Việt local**. Chi tiết ở bảng dưới.

---

## 2. DANH SÁCH ISSUE (ưu tiên cao → thấp)

| # | Severity | Location | Vấn đề | Lý do | Đề xuất sửa |
|---|---|---|---|---|---|
| I1 | 🔴 **High** | 24.2 (L1385), 24.5 (L1434) | Schema dùng model `gemini-2.0-flash` làm mặc định | **Gemini 2.0 Flash đã deprecated và shut down ngày 1/6/2026** (theo docs Google). Dùng sẽ fail runtime. | Đổi default thành `gemini-2.5-flash-lite` (rẻ: $0.10/$0.40) cho preset Fast/Balanced; `gemini-2.5-flash` ($0.30/$2.50) cho High/Maximum. |
| I2 | 🔴 **High** | 16.2 (L898) vs 25.3 (L1489) | `POST /health` (16.2) trái với `GET /health` (25.3); naming API lệch (`/v1/analyze` vs `/v1/media/probe`, `/v1/extract_audio` vs `/v1/audio/extract`) | Hai danh sách endpoint không đồng nhất → implement sẽ mơ hồ. `/health` phải là GET (chuẩn REST, không gây side-effect). | Thống nhất về **một** danh sách (giữ style 25.3, GET /health). Xóa bảng 16.2 hoặc chuyển thành tham chiếu "xem 25.3". |
| I3 | 🟠 **Medium** | 7.7 (L512), 13.1 (L815), 21 (L1167) | "Kokoro hiện chưa có giọng tiếng Việt" — thông tin cũ | Đã có fine-tune community `iamdinhthuan/Kokoro-Vietnamese` (12+ giọng, G2P vig2p) và `contextboxai/Kokoro-Vietnamese` (ONNX). NHƯNG license fine-tune không rõ ràng (voicepack xuất phát từ LarVoice; dataset viVoice là CC-BY-NC-SA) → rủi ro thương mại. | Cập nhật wording: "có fine-tune community nhưng **license mơ hồ → KHÔNG dùng thương mại** khi chưa có license rõ. Local TTS tiếng Việt ưu tiên Piper (kiểm tra license từng giọng) hoặc dùng cloud." |
| I4 | 🟠 **Medium** | 14.2 (L842-843) | Chiến lược whisper.cpp Vulkan cho AMD/Intel thiếu mitigation | Nghiên cứu 2026 (whisper.cpp issues #3723, #3806, #3750, #3638): Vulkan Windows có crash driver-specific: AMD Radeon 780M + beam-size 8 + VAD segfault; AMD RDNA4 (RX 9070 XT) + flash-attn crash; MSVC static build backend không register (race static-init); init nhiều thread race. | Bổ sung 3 mitigation (xem Mục 4 bên dưới). |
| I5 | 🟡 **Low** | 8.2 (L552) | "không load toàn bộ vào RAM **model**" | Sai chính tả — ý là audio. Model vẫn phải load vào RAM. | Sửa thành "không load toàn bộ **audio** vào RAM". |
| I6 | 🟡 **Low** | 30.1 (L1669) | "RAM peak \| tracert" | `tracert` là lệnh trace network, không đo RAM. | Sửa thành `tracemalloc` (Python) hoặc Process Explorer / PSAPI. |
| I7 | 🟡 **Low** | 34 (L1772) | "pin version ổn định" không ghi phiên bản cụ thể | Khó hành động khi không có số version. | Ghi rõ: Tauri 2.5.1 (crash-free baseline đã ghi nhận 2025), tránh bug signing password-from-env (tauri#13485); theo dõi 2.12.x. |
| I8 | 🟢 **Info** | 15.2 (L875) | Cơ chế auth Rust ↔ worker chưa chi tiết | Design ok, cần quyết định cụ thể. | Chốt: sinh token 256-bit ngẫu nhiên mỗi session, truyền qua **stdin** (không phải argv/env public), worker so sánh constant-time, header `Authorization: Bearer`. |
| I9 | 🟢 **Info** | 32.2 (L1732-1735) | Python 3.11 vs phiên bản mới; size installer | 3.11 đã tương thích torch; 3.12/3.13 đã hỗ trợ. | Không đổi bây giờ (3.11 an toàn), note rằng re-verify 3.12/3.13 ở Phase 13. Size ước tính OK: CPU ~300-600MB, CUDA ~2-4GB. |
| I10 | 🟢 **Info** | 35 (L1783-1790) | Updater artifact Windows chưa ghi đúng | Nghiên cứu 2026: Tauri v2 NSIS updater artifact = `setup.exe` + `setup.exe.sig` (**không phải** `.nsis.zip` — xác nhận từ repo thực tế + docs Tauri). | Ghi rõ vào 35.1 và 32.1: `x64-setup.exe` + `.exe.sig`; `createUpdaterArtifacts: true`; env `TAURI_SIGNING_PRIVATE_KEY` (file `.env` **không** hoạt động). |

---

## 3. AUDIT MVP SCOPE

### 3.1 Kết quả

| Danh mục | Kết quả | Ghi chú |
|---|---|---|
| 38.1 MUST HAVE | ✅ Khớp | 25 mục, tất cả nằm trong phạm vi 45.2 (Transcribe→Translate→Subtitle→Render→Export). |
| 38.2 SHOULD HAVE | ✅ Hợp lý | Diarization/word-timestamps/local-LLM đều optional đúng. |
| 38.3 MUST NOT HAVE | ✅ Sạch | Không có TTS/dubbing/separation/OCR removal/billing trong MVP tasks (45.5). |
| 45.5 Task list (30 tasks) | ✅ Nhất quán | Dependency order đúng: 012 audio → 013 STT → 017-023 translation → 024 subtitle → 027 render → 029 export. |

### 3.2 Nhận xét scope
- **Đúng định hướng** với quyết định đã chốt: "Transcribe → Translate → Burn subtitles", Hybrid local-first, cloud opt-in.
- Một điểm lưu ý nhỏ: task 030 (Settings UI + secret store) đặt cuối, nhưng **secret storage nên được xây từ task 004-008** (nền tảng bảo mật) để API key không "đổ xô" vào cuối. → Chuyển một sub-task "secret_store.rs + credential manager" lên Phase 2.

---

## 4. NGHIÊN CỨU KIỂM CHỨNG 2026 (đã verify trên Internet)

### 4.1 whisper.cpp Vulkan trên Windows — kết quả kiểm chứng

| Vấn đề | Bằng chứng | Mitigation bắt buộc |
|---|---|---|
| Crash AMD Radeon 780M: beam-size 8 + VAD | issue #3723 (2026-03) | Giới hạn `beam_size ≤ 5-6`; VAD conservative (min-speech ≥ 500ms) |
| Crash AMD RDNA4 (RX 9070 XT) khi bật flash-attn | issue #3806 | Mặc định `flash_attn = False` trên AMD; chỉ bật trên NVIDIA |
| MSVC static build: Vulkan backend không register | issue #3750 | Nếu build static lib → gọi `ggml_backend_vk_reg()` thủ công sau khi init instance |
| Race khi init nhiều thread đồng thời | PR #3638 | **Chỉ 1 model init tại 1 thời điểm** (semaphore trong STT service) |
| Không có official Windows Vulkan binary | issue #3673 | Ta build sidecar riêng (đã nằm trong kế hoạch) |

**Kết luận:** Chiến lược hiện tại của MASTER_PLAN (faster-whisper CUDA cho NVIDIA, whisper.cpp Vulkan cho AMD/Intel, CPU fallback) **vẫn đúng**, nhưng phải thêm các mitigation trên vào `stt_service.py` design (Phase 5). Không cần đổi kiến trúc.

### 4.2 LLM Translation pricing (kiểm chứng 5/2026 - 8/2026)

| Model | Input $/MTok | Output $/MTok | Ghi chú |
|---|---|---|---|
| **Gemini 2.5 Flash-Lite** | $0.10 | $0.40 | Rẻ nhất GA; 1M context — **default mới** |
| Gemini 2.5 Flash | $0.30 | $2.50 | Thay thế 2.0 Flash |
| GPT-4o-mini | $0.15 | $0.60 | Ổn định, all-rounder |
| DeepSeek V4-Flash | $0.14 | $0.28 | Rẻ nhất tuyệt đối; cache-hit $0.0028/M |
| Claude Haiku 3.5 | $0.80 | $4.00 | Đắt hơn trước; dùng cho preset High nếu muốn chất lượng |
| **Gemini 2.0 Flash** | — | — | ❌ **SHUT DOWN 1/6/2026** |

> Áp dụng: bảng 7.6 + 12.3 preset routing cập nhật default model; vẫn giữ abstraction (không hard-code).

### 4.3 TTS tiếng Việt local (Post-MVP)

| Mô hình | License | Giọng Việt | Kết luận thương mại |
|---|---|---|---|
| Kokoro-82M (gốc) | Apache-2.0 | ✗ (chỉ en/ja/zh/fr/…, không có vi) | Dùng được cho **tiếng Trung** (`zf_xiaobei`) — dubbing VN→CN |
| Kokoro-Vietnamese (community fine-tune) | **Không rõ** (provenance LarVoice/viVoice CC-BY-NC-SA) | ✓ 12+ giọng | ⚠️ **KHÔNG dùng thương mại khi chưa verify license** |
| Chatterbox Multilingual v3 | MIT | ✓ (chất lượng KÉM — CER ~75%, khuyến cáo không dùng production cho vi) | ⚠️ Tránh cho tiếng Việt ở v3; theo dõi bản sau |
| Viterbox (VN fine-tune Chatterbox) | CC-BY-NC-4.0 | ✓ | ❌ Non-commercial |
| Piper ONNX | MIT (cũ)/GPL-3.0 (fork OHF-Voice) | ✓ 3 giọng (vi-vais1000, vi-25hours, vi-vivos) | ⚠️ Kiểm tra license từng giọng trước khi embed |

**Kết luận:** Chiến lược "cloud TTS mặc định cho tiếng Việt + local fallback" trong MASTER_PLAN **vẫn đúng và nên giữ nguyên**. Bổ sung: local fallback tiếng Việt ưu tiên Piper (verify license); Kokoro gốc dùng cho chiều Trung→... (reverse).

### 4.4 Tauri updater & code signing (kiểm chứng docs Tauri 2026)

| Chủ đề | Kết quả |
|---|---|
| Updater artifact Windows | NSIS: `*_x64-setup.exe` + `*.exe.sig` (KHÔNG phải `.nsis.zip`) |
| Config | `bundle.createUpdaterArtifacts: true`; `plugins.updater.pubkey`, `endpoints`, `windows.installMode: "passive"` |
| Signing | `npm run tauri signer generate -w ~/.tauri/app.key`; env `TAURI_SIGNING_PRIVATE_KEY` (+`_PASSWORD`). **`.env` file KHÔNG hoạt động** |
| Code signing Windows | OV cert: `bundle.windows.certificateThumbprint` + `digestAlgorithm: sha256` + `timestampUrl`; hoặc Azure Key Vault / Azure Artifact Signing / `signCommand` |
| Bug đã biết | tauri#13485: password-from-env có thể fail; workaround: tránh ký tự đặc biệt trong password, test trước release |
| Binary patching | Flag `--no-binary-patching` (tauri-cli 2.11+) dùng khi pre-sign binary với nhiều installer type |

---

## 5. AUDIT LICENSING (đối chiếu bảng 21)

| Dependency | MASTER_PLAN | Verify 2026 | Kết luận |
|---|---|---|---|
| ProPainter (NTU) | Loại bỏ | ✓ NTU S-Lab non-commercial | ✅ Giữ nguyên |
| XTTS v2 (CPML) | Loại bỏ | ✓ Coqui shut down 1/2024, không có đường mua license | ✅ Giữ nguyên |
| F5-TTS (CC-BY-NC) | Loại bỏ | ✓ | ✅ Giữ nguyên |
| Demucs htdemucs_ft (MIT) | Dùng | ✓ MIT, SDR 9.0-9.2 dB | ✅ OK |
| faster-whisper (MIT) | Dùng | ✓ | ✅ OK |
| pyannote community-1 | Optional + HF token | ✓ CC-BY-4.0 gated | ✅ OK |
| FFmpeg LGPL | Verify build | ✓ Nhắc lại: dùng build static thương mại có bảng license | ✅ OK |
| **Kokoro-Vietnamese** | — (mới) | ⚠️ License mơ hồ | ➕ **Thêm vào bảng**: không dùng thương mại khi chưa verify |
| Chatterbox v3 | Dùng (MIT) | ✓ MIT nhưng vi kém | ✅ OK (dùng cho ngôn ngữ khác, tránh vi) |

---

## 6. TRẠNG THÁI TOÀN BỘ TODO — VERIFY (12 mục)

| # | Location | Nội dung | Trạng thái 2026-08-09 |
|---|---|---|---|
| 1 | 7.4 (L476) | whisper.cpp Vulkan Windows stability | ✅ **VERIFIED** — hoạt động nhưng có crash driver-specific (xem 4.1); đã có mitigation |
| 2 | 7.6 (L500) | LLM giá/model hiện tại | ✅ **VERIFIED** — xem 4.2; **sửa I1** |
| 3 | 7.7 (L512) | Kokoro giọng Việt | ✅ **VERIFIED** — fine-tune tồn tại nhưng license mơ hồ (I3) |
| 4 | 10.2 (L703) | VRAM htdemucs_ft | ⏳ **RE-CHECK at Phase 13** — ước tính 6-8GB hợp lý; chưa test phần cứng |
| 5 | 13.1 (L815) | Thư viện/giọng Việt local | ✅ **VERIFIED** — xem 4.3 |
| 6 | 15.2 (L875) | Cơ chế auth Rust↔worker | ✅ **DECIDED** — token stdin + Bearer (I8) |
| 7 | 21 (L1167) | Kokoro license | ✅ **VERIFIED** — thêm cảnh báo (I3) |
| 8 | 21 (L1173) | Re-verify LICENSE trước release | 🔁 **ONGOING** — checklist bắt buộc ở TASKS |
| 9 | 32.2 (L1732) | Python version compatibility | ✅ **VERIFIED** — 3.11 an toàn; 3.12/3.13 có thể xét sau (I9) |
| 10 | 32.2 (L1735) | Size installer CPU/GPU | ✅ **VERIFIED** — CPU ~300-600MB, CUDA ~2-4GB |
| 11 | 34 (L1774) | Quy trình signing Tauri 2.x | ✅ **VERIFIED** — xem 4.4 (I7) |
| 12 | 36.2 (L1814) | Chiến lược license server | ⏳ **POSTPONE** — quyết định ở V1, không block MVP |

---

## 7. PHÊ DUYỆT CHÍNH THỨC

```text
MASTER_PLAN.md → TÌNH TRẠNG: APPROVED-WITH-REVISIONS

Các sửa đổi bắt buộc trước khi bắt đầu Phase 1:
  [S1] Đổi default model Gemini: gemini-2.0-flash → gemini-2.5-flash-lite (preset Fast/Balanced),
       gemini-2.5-flash (preset High/Maximum). Áp dụng cho schema 24.2, 24.5, bảng 7.6, 12.3.
  [S2] Thống nhất danh sách Worker API: giữ style 25.3 (GET /health), xóa trùng lặp ở 16.2.
  [S3] Bổ sung 3 mitigation whisper.cpp Vulkan vào STT design (beam_size ≤ 6, flash_attn off trên AMD,
       single-threaded model init + optional vk_reg manual).
  [S4] Cập nhật wording Kokoro tiếng Việt (license mơ hồ) tại 7.7, 13.1, 21.
  [S5] Thêm dòng "không load toàn bộ audio vào RAM" + sửa typo "tracert".
  [S6] Ghi rõ updater artifact Windows = setup.exe + .exe.sig; env signing (không .env file).

Sau khi áp dụng S1-S6 → MASTER_PLAN đủ điều kiện làm "Source of truth" cho TASKS.md.
```

---

*Hết MASTER_PLAN_REVIEW.md — Đầu vào cho ARCHITECTURE_DECISION.md.*
