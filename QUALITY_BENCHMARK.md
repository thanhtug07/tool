# QUALITY_BENCHMARK.md — Golden Translation Benchmark & Dataset

**Version:** 1.0.0
**Ngày:** 2026-08-09
**Base:** `MASTER_PLAN.md` (FROZEN V3) + `ARCHITECTURE_DECISION.md`.
**Mục đích:** Định nghĩa **Golden Translation Dataset** + cách chấm điểm (score) + ngưỡng chấp nhận để đảm bảo chất lượng dịch ổn định qua thời gian (regression) và so sánh provider/preset.

> ✅ **TRẠNG THÁI DỮ LIỆU (2026-08-12):** Dataset thật đã có tại `golden/translation/` — 11 case / 11 category (zh→vi), kèm `manifest.json` và expected dịch chuẩn. Chi tiết vận hành ở mục 7.

---

## 1. MỤC TIÊU

- Bảo vệ chất lượng translation khỏi regression (đổi model/preset/prompt/glossary phải không làm score giảm dưới ngưỡng).
- So sánh khách quan giữa: `GeminiProvider` vs `LocalLLMProvider`, giữa các preset (Fast/Balanced/High/Maximum).
- Kiểm soát chi phí ↔ chất lượng: High/Maximum đắt hơn phải cho score cao hơn để justify.

## 2. CẤU TRÚC GOLDEN TRANSLATION DATASET

```text
golden/translation/
├── README.md                 # quy trình cập nhật dataset + người approve
├── manifest.json             # version dataset, ngày, source, người review, ngưỡng mặc định
├── cases/
│   ├── C001_dialogue.json
│   ├── C002_idiom.json
│   ├── C003_slang_casual.json
│   ├── C004_proper_noun.json
│   ├── C005_character_name.json
│   ├── C006_repeated_terminology.json
│   ├── C007_context_dependent.json
│   ├── C008_short_ambiguous.json
│   ├── C009_long_dialogue.json
│   ├── C010_multi_speaker.json
│   └── C011_cultural_expression.json
└── expected/
    └── (bản dịch tham chiếu — review bởi ít nhất 2 người)
```

### 2.1 Format case (`cases/C*.json`)

```json
{
  "case_id": "C004_proper_noun",
  "category": "proper_noun",
  "source_lang": "zh",
  "target_lang": "vi",
  "context": { "prev_block": "...", "next_block": "...", "speaker_map": {} },
  "segments": [
    { "idx": 0, "speaker": "A", "text": "张三明天要去上海。" }
  ]
}
```

### 2.2 Danh sách category (bắt buộc phủ đủ)

| Category | Mô tả | Bắt buộc trong MVP dataset |
|---|---|---|
| dialogue | Hội thoại tự nhiên, ngắt quãng | ✅ |
| idiom | Thành ngữ / tục ngữ (dịch nghĩa không từng chữ) | ✅ |
| slang_casual | Khẩu ngữ, văn nói | ✅ |
| proper_noun | Tên địa danh / tổ chức / thương hiệu | ✅ |
| character_name | Tên nhân vật nhất quán xuyên suốt | ✅ |
| repeated_terminology | Thuật ngữ lặp lại — glossary phải giữ nhất quán | ✅ |
| context_dependent | Câu chỉ hiểu đúng khi có block trước/sau | ✅ |
| short_ambiguous | Câu ngắn, đa nghĩa khi đứng riêng | ✅ |
| long_dialogue | 1 block dài 5-10 cues — không miss line | ✅ |
| multi_speaker | Nhiều speaker — không lẫn lộn | ✅ |
| cultural_expression | Biểu đạt văn hóa (cần chuyển ngữ cảnh) | ✅ |

> Nếu MVP chưa có đủ 11 category → dataset coi là **chưa hoàn chỉnh**; benchmark vẫn chạy được trên subset nhưng báo `coverage: partial`.

## 3. QUY TRÌNH TAO DỰ LIỆU (khi thực hiện TODO)

1. Lấy segment thật từ video mẫu (Golden Video) + transcript đã align.
2. Dịch tham chiếu (reference) bởi **≥2 người** biết song ngữ Trung–Việt; chốt bằng discussion.
3. Mỗi case có `reason` ghi rõ tại sao case khó (chỗ nào dễ dịch sai).
4. Commit dataset riêng (không lẫn vào source); version theo manifest.
5. `TODO — CREATE GOLDEN TRANSLATION DATASET` → xóa sau khi có ít nhất 50 case / 11 category.

## 4. CÁCH CHẠY BENCHMARK (script)

```text
runner: worker/scripts/benchmark_translation.py
input : golden/translation/manifest.json + cases/*.json
flow  : với mỗi preset/model cấu hình →
          chunk cases theo context → gọi provider → thu translations
        → so sánh với expected → tính score
output: bench_report.json (per-case + tổng) + diff file
```

- Chạy tự động: CI (marker `ai`, chạy khi thay đổi prompt/model/preset) + manual trên máy dev.
- Dùng `MockProvider` để test infrastructure không cần API key.

## 5. ĐÁNH GIÁ & NGƯỠNG CHẤP NHẬN

### 5.1 Các tiêu chí chấm (weight)

| Tiêu chí | Weight | Cách tính |
|---|---|---|
| Line coverage (không miss line) | 0.25 | % segment có output (không empty, không bỏ sót) |
| Độ đúng nghĩa | 0.40 | Semantic similarity (embedding cosine) với reference, ngưỡng 0.85 = 1.0 điểm |
| Thuật ngữ/glossary | 0.15 | % thuật ngữ khớp glossary (case có `expected_terms`) |
| Ngữ pháp/tự nhiên | 0.10 | Human spot-check (subset) hoặc metric thay thế tạm thời |
| Format nhất quán | 0.10 | idx khớp, không ghép/thêm dòng, đúng số segment |

### 5.2 Công thức

```text
score_case = Σ (tiêu chí_weight × đạt được)
score_total = weighted mean over all cases
```

### 5.3 Ngưỡng (threshold) — mặc định, review lại khi có dataset

| Preset | Ngưỡng PASS (score) | Ghi chú |
|---|---|---|
| Fast | ≥ 0.80 | rẻ nhất; cho phép sai nhỏ ở idioms |
| Balanced | ≥ 0.85 | mặc định |
| High Quality | ≥ 0.90 | kèm Full QC + judge |
| Maximum | ≥ 0.93 | kèm judge |

> Ngưỡng chưa phải chốt cuối — **phải hiệu chỉnh sau khi có dataset thật** (nếu model tốt hơn ngưỡng → nâng; nếu không đạt khả thi → xem lại dataset/prompt trước khi hạ ngưỡng). Không hạ ngưỡng chỉ để "qua CI".

## 6. REGRESSION PROCESS

1. Mỗi thay đổi prompt/model/preset/glossary ảnh hưởng translation → chạy benchmark.
2. Score giảm > 0.03 so với baseline → **chặn merge**, phải giải thích.
3. Baseline lưu trong `bench_report_baseline.json` (đính kèm model + ngày + commit).
4. Đổi model mặc định (VD Gemini 2.5 Flash → model mới) → bắt buộc benchmark lại trước khi đổi.

## 7. TRÁCH NHIỆM

- Dataset update → có review + version bump trong manifest.
- Chỉ người duyệt chất lượng (hoặc review 2 người) được merge dataset.
- Benchmark report mới nhất đính kèm vào release checklist (MASTER_PLAN §44 tầng 3).

---

*Hết QUALITY_BENCHMARK.md — tích hợp qua MASTER_PLAN §38.1a, Phase 12, DoD tầng 3.*

## 8. HIỆN TRẠNG THỰC TẾ (2026-08-12) — IMPLEMENTED

> Trước đây đánh dấu `TODO — CREATE GOLDEN TRANSLATION DATASET`. Đã hoàn thành.

### 8.1 Dataset

| Thành phần | Đường dẫn | Ghi chú |
|---|---|---|
| Manifest | `golden/translation/manifest.json` | version 1.0.0, zh→vi, coverage full, 11 case |
| Cases | `golden/translation/cases/C0XX_*.json` | đúng format mục 2.1 (source_lang/target_lang/context/segments) |
| Expected | `golden/translation/expected/C0XX_*.json` | bản dịch tham chiếu hand-curated (idx → expected_text) |

11 category đều có case: dialogue, idiom, slang_casual, proper_noun, character_name,
repeated_terminology (kèm glossary), context_dependent, short_ambiguous, long_dialogue
(5 cue), multi_speaker, cultural_expression.

### 8.2 Chạy benchmark

```text
# Deterministic regression (không cần network / key) — mock provider
py golden/scripts/run_translation_benchmark.py --provider mock

# Real provider (cần API key trong Windows Credential Manager)
py golden/scripts/run_translation_benchmark.py --provider gemini [--api-key-env GEMINI_API_KEY]
```

Script chưa tồn tại → tạo theo mục 4 của tài liệu này; ngưỡng mặc định trong manifest
(`segment_accuracy ≥ 0.9`, `case_pass_ratio ≥ 0.9`). Với mock provider, mọi case phải PASS
100% vì translation deterministic `[<target_lang>] <text>` — benchmark thực chất dùng để đo
provider thật (gemini/local) trên bộ câu cố định này.

### 8.3 Ghi chú

- Dataset nhỏ (11 case) đủ để bắt đầu regression; mục 3.5 của tài liệu vẫn yêu cầu mở rộng
  lên ≥ 50 case khi có người review → ghi nhận là P2 trong release audit.
- Không nhúng model bản quyền; mọi text trong dataset là nội dung tự viết.
