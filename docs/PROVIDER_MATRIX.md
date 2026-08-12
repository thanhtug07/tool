# PROVIDER MATRIX

Status of every provider in the repository, verified against source. A provider is **REAL** only when it has a working implementation; **PARTIAL** when code exists but is unproven/incomplete; **MOCK** when it exists for tests/dev only; **NOT IMPLEMENTED** when nothing exists.

## Translation

| Provider | Function | Implemented | Local | API Key | Default | Tested | Fallback | Verdict |
|---|---|---|---|---|---|---|---|---|
| **MockProvider** | Translation | Yes — `worker/src/services/providers/translation/mock_provider.py` | Yes | No | Yes (app default) | Yes — unit + golden E2E | — | **REAL** (dev/offline; pseudo-translation output `[<lang>] text`) |
| **Gemini** | Translation | Yes — `gemini_provider.py` (google-genai SDK, structured output, retry/backoff, segment-count validation) | No (cloud) | Yes — Windows Credential Manager | Configurable (S1 default) | **NO — never run against a live key** (unit tests use an injected fake client) | `E_API_AUTH` / `E_API_RATE_LIMIT` / `E_API_ERROR` surface cleanly | **PARTIAL** (real code, unproven live) |
| **Local LLM** (llama.cpp / OpenAI-compatible) | Translation | Yes — `local_llm_provider.py` | Yes (server process) | No | Configurable | **NO — never run against a real server/GGUF** (mock-server unit tests only) | None | **PARTIAL** (real code, unproven live) |

## STT

| Provider | Function | Implemented | Local | API Key | Default | Tested | Fallback | Verdict |
|---|---|---|---|---|---|---|---|---|
| **faster-whisper** (CTranslate2) | STT | Yes — `stt_service.py` (VRAM guard, VAD, progress, cancel) | Yes | No | Yes (`large-v3`, guarded) | **Yes — real inference on CPU and CUDA** (release audit: real NVIDIA run, golden E2E 16/16) | VRAM downgrade tiers | **REAL** |
| **whisper.cpp** (ggml) | STT fallback | Yes — `_transcribe_whisper_cpp` | Yes | No | No | **No — no binary/model in repo** (unit-tested via injected runner) | AMD/Intel/CPU | **PARTIAL** (needs a compiled `whisper-cli` + ggml model, neither bundled) |

## TTS / Dubbing

| Provider | Function | Implemented | Local | API Key | Default | Tested | Fallback | Verdict |
|---|---|---|---|---|---|---|---|---|
| Kokoro-82M | TTS (zh) | **NO** — `providers/tts/` is empty | Local | No | — | — | — | **NOT IMPLEMENTED** |
| Piper | TTS (vi fallback) | **NO** | Local | No | — | — | — | **NOT IMPLEMENTED** |
| ElevenLabs / Azure / OpenAI TTS | TTS (cloud) | **NO** | Cloud | — | — | — | — | **NOT IMPLEMENTED** (designed only, post-MVP) |
| Voice cloning | Voice cloning | **NO** | — | — | — | — | — | **NOT IMPLEMENTED** (post-MVP, out of scope) |

## Audio

| Provider | Function | Implemented | Local | API Key | Default | Tested | Fallback | Verdict |
|---|---|---|---|---|---|---|---|---|
| FFmpeg (audio extract) | 16k mono WAV extraction | Yes — `audio_service.py` | Yes | No | Yes | **Yes — real ffmpeg, incl. cancel/no-audio** | — | **REAL** |
| Demucs `htdemucs_ft` | Speech/background separation, music preservation | **NO** — post-MVP (T036) | Local | No | — | — | — | **NOT IMPLEMENTED** |

## OCR / Logo removal

| Provider | Function | Implemented | Local | API Key | Default | Tested | Fallback | Verdict |
|---|---|---|---|---|---|---|---|---|
| RapidOCR / PaddleOCR | OCR | **NO** — `providers/ocr/` is empty | Local | — | — | — | — | **NOT IMPLEMENTED** (post-MVP) |
| Inpainting (laMa etc.) | Logo/watermark removal | **NO** | Local | — | — | — | — | **NOT IMPLEMENTED** (post-MVP; ProPainter rejected on licensing) |

## Context / LLM (translation-side)

| Provider | Function | Implemented | Local | API Key | Default | Tested | Verdict |
|---|---|---|---|---|---|---|---|
| Context Engine (`context_service.py`) | Chunking + glossary/character/rules context for translation | Yes | Yes | No | Yes | Yes — unit | **REAL** |
| Translation Memory (`translation_service.py`) | Exact-source translation cache | Yes | Yes | No | Yes | Yes — unit | **REAL** |

## Summary

- **REAL and proven end-to-end:** FFmpeg extract, faster-whisper STT, mock translation, subtitle generation, burn-in render, watermark, export.
- **REAL code, unproven live:** Gemini translation, local-LLM translation, whisper.cpp fallback.
- **NOT IMPLEMENTED (block the "dubbed video" goal):** TTS, voice cloning, audio separation/music preservation, OCR, logo removal.
