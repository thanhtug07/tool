# PHASE_0_CHANGELOG.md — Nhật ký thay đổi Phase 0 (S1–S6)

**Version:** 1.0.0
**Ngày:** 2026-08-09
**Phạm vi:** Mọi thay đổi documentation áp dụng vào `MASTER_PLAN.md` sau khi audit (`MASTER_PLAN_REVIEW.md`). Kết quả = **ARCHITECTURE FREEZE V2**.
**Nguồn:** `MASTER_PLAN_REVIEW.md` (S1–S6) + kiểm chứng Internet 2026 (Gemini pricing, whisper.cpp Vulkan, TTS VN, Tauri updater/signing).

---

## 1. S1 — Gemini model deprecated → cập nhật default

| Mục | Nội dung |
|---|---|
| **Issue** | Schema & preset routing dùng `gemini-2.0-flash` làm model mặc định |
| **Old Decision** | Gemini 2.0 Flash ($0.10/$0.40) là default translation model |
| **New Decision** | Default = **Gemini 2.5 Flash-Lite** ($0.10/$0.40) cho Fast/Balanced; **Gemini 2.5 Flash** ($0.30/$2.50) cho High/Maximum; GPT-4o-mini/Claude Haiku là lựa chọn thay thế |
| **Reason** | Gemini 2.0 Flash **shutdown 1/6/2026** (docs Google) — dùng sẽ fail runtime |
| **Affected Files** | `MASTER_PLAN.md` §6.1, §7.6, §12.3, §24.2, §24.5, §45.1 |
| **Impact on MVP** | Không đổi scope; chỉ đổi model default + config mẫu |

## 2. S2 — Thống nhất Worker API

| Mục | Nội dung |
|---|---|
| **Issue** | Hai danh sách endpoint trùng/khác nhau: §16.2 (`POST /health`, `/v1/analyze`, `/v1/extract_audio`) vs §25.3 (`GET /health`, `/v1/media/probe`, `/v1/audio/extract`) |
| **Old Decision** | Hai danh sách API tồn tại song song, `POST /health` |
| **New Decision** | **Single source of truth = §25.3**; §16.2 thành tham chiếu; `/health` = **GET**; naming theo 25.3 |
| **Reason** | Chuẩn REST; tránh mơ hồ khi implement |
| **Affected Files** | `MASTER_PLAN.md` §16.2, §25.3 (không đổi) |
| **Impact on MVP** | Không đổi scope; định nghĩa API rõ ràng cho worker |

## 3. S3 — whisper.cpp Vulkan: 3 mitigation bắt buộc

| Mục | Nội dung |
|---|---|
| **Issue** | Chiến lược whisper.cpp Vulkan cho AMD/Intel thiếu mitigation; driver crash đã ghi nhận |
| **Old Decision** | whisper.cpp Vulkan là fallback đơn thuần, không ràng buộc tham số |
| **New Decision** | Bắt buộc: (1) `beam_size ≤ 6`; (2) `flash_attn = False` trên AMD/Intel Vulkan; (3) **model init single-threaded** + `ggml_backend_vk_reg()` thủ công nếu static lib |
| **Reason** | VERIFIED 2026: AMD Radeon 780M + beam 8 + VAD segfault (#3723); AMD RDNA4 + flash-attn crash (#3806); MSVC static lib backend không register (#3750); init race (#3638) |
| **Affected Files** | `MASTER_PLAN.md` §7.4, §8.2, §14.2 |
| **Impact on MVP** | Không đổi scope; thêm ràng buộc kỹ thuật cho STT (đã phản ánh trong TASK-015) |

## 4. S4 — TTS tiếng Việt: licensing làm rõ

| Mục | Nội dung |
|---|---|
| **Issue** | "Kokoro chưa có giọng tiếng Việt" — thông tin cũ; license fine-tune mơ hồ |
| **Old Decision** | Kokoro = Apache-2.0, chờ giọng Việt; Piper là fallback tùy ý |
| **New Decision** | Kokoro gốc không có tiếng Việt (tiếng Trung OK); **Kokoro-Vietnamese community → `TODO — VERIFY BEFORE COMMERCIAL RELEASE`** (provenance viVoice CC-BY-NC-SA/LarVoice); Chatterbox v3 tránh tiếng Việt (CER ~75%); Viterbox non-commercial → loại; cloud TTS mặc định cho tiếng Việt |
| **Reason** | Không đoán license; bảo vệ commercial distribution |
| **Affected Files** | `MASTER_PLAN.md` §7.7, §13.1, §21 |
| **Impact on MVP** | Không (TTS Post-MVP); bảng licensing đầy đủ hơn cho release |

## 5. S5 — Lỗi chính tả / thuật ngữ

| Mục | Nội dung |
|---|---|
| **Issue** | §8.2 "không load toàn bộ vào RAM model"; §30.1 "RAM peak \| tracert" |
| **Old Decision** | — (lỗi typo) |
| **New Decision** | "không load toàn bộ **audio** vào RAM"; "tracemalloc (Python) / Process Explorer" |
| **Reason** | Chính xác thuật ngữ |
| **Affected Files** | `MASTER_PLAN.md` §8.2, §30.1 |
| **Impact on MVP** | Không |

## 6. S6 — Tauri updater & code signing (VERIFIED 2026)

| Mục | Nội dung |
|---|---|
| **Issue** | Quy trình signing/updater mô tả chung chung; artifact Windows sai |
| **Old Decision** | "signingConfig + pin version"; updater tải "installer artifact + .sig" |
| **New Decision** | OV/EV cert qua `bundle.windows.{certificateThumbprint,digestAlgorithm,timestampUrl}` hoặc Azure KV/Artifact Signing/`signCommand`; **Windows artifact = `*_x64-setup.exe` + `*.exe.sig`** (không `.nsis.zip`); `createUpdaterArtifacts: true`; `installMode: passive`; env `TAURI_SIGNING_PRIVATE_KEY` (.env KHÔNG hoạt động); bug tauri#13485 (password-env) |
| **Reason** | Docs Tauri chính thức + repo thực tế 2026; tránh update fail |
| **Affected Files** | `MASTER_PLAN.md` §34, §35.1 |
| **Impact on MVP** | Không đổi scope; Phase 13/14 định nghĩa chính xác |

## 7. Cập nhật bổ sung (từ review, không thuộc S1–S6)

| Mục | Nội dung |
|---|---|
| **Header status** | `DRAFT — PENDING ARCHITECTURE REVIEW` → `FROZEN — ARCHITECTURE FREEZE V2 (S1–S6 applied; xem PHASE_0_CHANGELOG.md)` |
| **Auth Rust↔worker** (§15.2) | `TODO — VERIFY` → quyết định: token 256-bit random, truyền qua **stdin** (`READY <token>`), `Authorization: Bearer`, so sánh constant-time |
| **Affected Files** | `MASTER_PLAN.md` header, §15.2 |
| **Impact on MVP** | Chốt design (đã phản ánh trong ARCHITECTURE_DECISION §6, TASK-006) |

---

## 8. KHÔNG THAY ĐỔI (giữ nguyên MVP scope)

```text
Transcribe → Translate → Subtitle → Burn → Export
Không thêm: Dubbing / Audio Separation / OCR Removal / Voice Cloning / Advanced Timeline / Billing / Enterprise
```

---

*Hết PHASE_0_CHANGELOG.md — Kết quả: MASTER_PLAN.md đã đồng bộ với ARCHITECTURE_DECISION.md, IMPLEMENTATION_ROADMAP.md, TASKS.md.*
