# VIDEO_PIPELINE.md

The video stages of the pipeline: **media inspection** (ffprobe), **subtitle burn-in render**, and **export + QC**. One of the three pipeline docs required by `MASTER_PLAN.md §22/§44` (see also `AI_PIPELINE.md`, `AUDIO_PIPELINE.md`).

## Stage order in the MVP vertical slice

```text
Import video → [AUDIO_PIPELINE] → [AI_PIPELINE] → Subtitle (ASS/SRT)
→ Render (burn-in) → Export (video + subtitles) + QC
```

## 1. Media inspection (`media_service.py`)

- `probe(path)` runs ffprobe and returns a validated `MediaMetadata` (resolution, FPS, duration, video/audio/subtitle streams, rotation, codec, format).
- Used by the project import flow and by render/export validation. Corrupt or invalid files raise `E_VIDEO_INVALID` / `E_VIDEO_CORRUPTED`.
- Tested against real ffprobe on synthetic MP4/MKV/MOV/WebM fixtures.

## 2. Subtitle burn-in render (`render_service.py`)

- libass burn-in via the `ass`/`subtitles` ffmpeg filter; optional text watermark (`drawtext`) and image watermark (`overlay`) with 9 named positions + custom x/y (`TASK-028`).
- Encoder auto-pick: NVENC → QSV → AMF → libx264, with graceful fallback to `libx264` on hardware-encode failure. Resolution/FPS/SAR/colour metadata are preserved (no re-timeline, no scaling).
- Progress + ETA streamed from ffmpeg `-progress`; cancellation kills the process tree and removes temp files.
- **Validation before ship** (`render_validation_issues`): resolution == input, FPS within 1%, duration within ±1 s, known output codec, audio streams (channels/sample-rate) preserved, and — when a subtitle window is supplied — a sampled frame must show a burned-in text region.
- Security: ffmpeg always runs from argument arrays (never a shell); subtitle/watermark files are copied into a temp workdir under generated names; watermark text is escaped for the filter-graph grammar (`'` is routed through a `textfile=` payload).

### Tests

- Unit: `worker/tests/unit/test_render_service.py` (encoder picking, arg building, escaping, watermark fingerprint, validation issues, SRT↔VTT conversion, export path resolution).
- Integration: `worker/tests/integration/test_render_ffmpeg.py` (real ffmpeg: burn-in, HW/CPU encoders, fallback, cancel, watermark region checks, export+QC).

## 3. Export + QC (`render_service.py`)

- `export_video(source, target_dir, ...)`: copies a rendered video to the user's chosen directory (atomic temp + `os.replace`, automatic ` (1)` suffix on collision), then runs QC. On hard QC failure the file is removed and `E_EXPORT_QC` is raised.
- `export_subtitles(source, target_dir, ...)`: copies a subtitle file, optionally converting SRT↔VTT (ASS is copied as-is; ASS→other is out of MVP scope).
- Error mapping (`MASTER_PLAN §28.1`): unwritable target → `E_PERMISSION_DENIED`; not enough disk → `E_DISK_FULL`.
- QC report (`build_qc_report`): video stream present, resolution/FPS/duration ±1 s, codec known, audio preserved, muxed subtitle streams kept (warn-only).
- Exposed over the worker HTTP API (`/v1/export/video`, `/v1/export/subtitles`) and proxied to the UI through Rust `export.video` / `export.subtitles` commands and the `ExportView`.

### Tests

- Unit: `TestExportVideo`, `TestExportSubtitles`, `TestBuildQcReport` in `worker/tests/unit/test_render_service.py`.
- Integration: `test_export_video_produces_valid_copy_with_passing_qc`, duplicate-suffix, corrupt-source rejection, subtitle passthrough/conversion in `test_render_ffmpeg.py`.
