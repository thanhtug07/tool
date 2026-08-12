# AUDIO_PIPELINE.md

The audio stages of the pipeline: **audio extraction** from the source video and the audio carried into render. One of the three pipeline docs required by `MASTER_PLAN.md §22/§44` (see also `AI_PIPELINE.md`, `VIDEO_PIPELINE.md`).

## Stage order in the MVP vertical slice

```text
Import video → ffprobe (media_service) → extract audio (16 kHz mono WAV) → [AI_PIPELINE: STT]
```

## 1. Audio extraction (`audio_service.py`)

- Extracts the source video's audio track into a 16 kHz mono PCM WAV for STT.
- Runs ffmpeg from an argument array (no shell), supports cancellation, and cleans up temp files.
- Failure handling: missing/corrupt input, no audio track, and cancellation are mapped to `MASTER_PLAN §28.1` codes.
- Exposed over the worker HTTP API (`/v1/audio/extract`) and driven from the pipeline via `WorkerClient`.

### Tests

- Unit: audio-service argument building and failure classification.
- Integration: `worker/tests/integration/test_audio_service.py` — real ffmpeg extraction on synthetic fixtures, including no-audio input, cancellation, and injection cases.

## 2. Audio in render

- Render preserves the source audio: the default audio codec is `copy` (`DEFAULT_AUDIO_CODEC`), and post-render validation asserts audio streams (channels + sample rate) are unchanged (see `VIDEO_PIPELINE.md`).

## Related notes

- The golden E2E runner (`golden/scripts/run_golden.py`) synthesizes deterministic speech (piper) so the full pipeline can be exercised without copyrighted media.
- Performance benchmark fixtures reuse the same synthesis path (`worker/scripts/benchmark_performance.py`).
