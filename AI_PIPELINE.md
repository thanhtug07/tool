# AI_PIPELINE.md

The AI stages of the pipeline: **speech-to-text** (local faster-whisper) and **contextual translation** (cloud or local). One of the three pipeline docs required by `MASTER_PLAN.md §22/§44` (see also `VIDEO_PIPELINE.md`, `AUDIO_PIPELINE.md`).

## Stage order in the MVP vertical slice

```text
Import video → [AUDIO_PIPELINE: ffprobe + extract] → STT → Translate → Subtitle
→ [VIDEO_PIPELINE: render] → Export + QC
```

The end-to-end flow is driven by `JobService` → `PipelineRunner` → `WorkerClient` (Rust) over the authenticated loopback HTTP API (`worker/src/api/pipeline.py`).

## 1. Speech-to-text (`stt_service.py`)

- Local `faster-whisper` (CTranslate2) with `whisper.cpp` fallback; models resolved via the model registry/cache (`model_registry.py`, `model_cache.py`, `model_verifier.py`, `model_downloader.py`).
- Input: 16 kHz mono WAV (from `AUDIO_PIPELINE`). Output: segments with `idx`, `segment_id`, `start`, `end`, `text`, `confidence`.
- Language detect + override: the STT `language` parameter pins the source language; otherwise auto-detect.
- Hardware: `hardware.py` detects GPU/VRAM and picks a device strategy; `pick_stt_model` guards VRAM and falls back to CPU.
- Caching: STT cache keyed by `(audio_sha256, model, compute, lang, vad)` (`cache.py`). A cached transcript is reused without re-running inference.

### Tests

- Unit: `worker/tests/unit/test_stt_service.py`, `test_stt_whisper_cpp.py` (mock/double based).
- Real inference (`@pytest.mark.ai`) is excluded from the default suite — it needs a downloaded model. One real faster-whisper run was executed during RELEASE-P0-006 (golden E2E).

## 2. Contextual translation (`translation_service.py`, `context_service.py`, `quality_service.py`)

- Chunking: `ContextEngine` greedily groups cues (max 10 per block, `BLOCK_MAX_CUES`) with a token budget that re-splits when a prompt would exceed it.
- Context pack per chunk: speaker map, glossary matches, referenced characters, rules, previous/next blocks as read-only context.
- Providers (`src/services/providers/`): `GeminiProvider` (cloud default), `LocalLLMProvider` (llama.cpp), `MockProvider` (deterministic, offline, for tests/dev).
- Translation memory: exact-source cache keyed by `(text_hash, target, glossary_ver, model)` so repeating lines are not re-translated. Cache-hit blocks are re-assembled with the **current** segment ids so the subtitle stage always sees a 1:1 match (regression fixed in RELEASE-P1-001).
- Quality gate (`quality_service.py`): per-block validation — line-count parity, `idx` monotonic, `segment_id` in source, non-empty translations, CPS limits — with retry (up to `MAX_RETRIES`) on transient provider errors and hallucination/coverage checks.

### Tests

- Unit: `test_translation_service.py` (TM hit/miss, glossary invalidation, dedup, segment-id regression), `test_quality_service.py`, `test_context_service.py`, `test_gemini_provider.py` (real Gemini call is `@pytest.mark.ai`).
- Integration: `test_subtitle_ffmpeg.py` validates generated ASS/SRT against real ffmpeg.

## Privacy model

Local-first: STT and subtitle/render run fully on-device. Cloud translation is used only when the user enables it (privacy setting); local `llama.cpp` is the offline fallback. See `SECURITY.md`.
