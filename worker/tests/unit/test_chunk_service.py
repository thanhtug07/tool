"""Unit tests for the chunked parallel pipeline (TASK_AUTOMATION_PINELINE).

Covers the pure pipeline logic — chunk math (Phase 1/2), bounded scheduler +
per-chunk retry (Phase 4/7), order validation (Phase 9), ordered assembly
(Phase 10), timeline validation (Phase 11), final validation (Phase 13),
output verification (Phase 14) and the cleanup state machine (Phase 15/16).
No media is processed here; real media runs in the integration test.
"""

from __future__ import annotations

import io
import json
import os
import time
import wave

import pytest

from src.services.chunk_service import (
    CHUNK_FAILED_PERMANENTLY,
    CHUNK_VALID,
    CLEANUP_DONE,
    CLEANUP_OUTPUT_VERIFIED,
    CleanupManager,
    Chunk,
    ChunkArtifacts,
    ChunkFailedError,
    ChunkManager,
    ChunkScheduler,
    StreamingChunkPipeline,
    _append_chunk_voice,
    _pcm_to_wav,
    assemble_translations,
    build_chunks,
    build_performance_trace,
    clamp_to_logical,
    concat_voice_tracks,
    merge_cues,
    merge_segments,
    validate_chunk_order,
    validate_chunk_result,
    validate_timeline,
)

import src.services.chunk_service as chunk_service  # noqa: E402

from src.services.chunk_service import ChunkPipelineContext  # noqa: E402


# ---------------------------------------------------------------------------
# PHASE 1/2 — chunk math + overlap timeline
# ---------------------------------------------------------------------------


class TestBuildChunks:
    def test_default_30s_chunks_with_overlap(self):
        chunks = build_chunks(60.0, chunk_duration=30.0, overlap=2.0)
        assert len(chunks) == 2
        c1, c2 = chunks
        assert c1.chunk_id == "chunk_0001" and c1.index == 1
        assert (c1.start, c1.end) == (0.0, 32.0)
        assert (c1.logical_start, c1.logical_end) == (0.0, 30.0)
        assert (c1.overlap_before, c1.overlap_after) == (0.0, 2.0)
        assert (c2.start, c2.end) == (28.0, 60.0)
        assert (c2.logical_start, c2.logical_end) == (30.0, 60.0)
        assert (c2.overlap_before, c2.overlap_after) == (2.0, 0.0)

    def test_final_short_chunk(self):
        chunks = build_chunks(75.0, chunk_duration=30.0, overlap=2.0)
        assert [c.logical_start for c in chunks] == [0.0, 30.0, 60.0]
        assert chunks[-1].logical_end == 75.0
        assert chunks[-1].end == 75.0

    def test_40minute_video_chunk_count(self):
        chunks = build_chunks(40 * 60.0, chunk_duration=30.0, overlap=2.0)
        assert len(chunks) == 80
        assert chunks[0].chunk_id == "chunk_0001"
        assert chunks[-1].chunk_id == "chunk_0080"

    def test_allowed_durations(self):
        expected = {20.0: 6, 30.0: 4, 45.0: 3, 60.0: 2}
        for d, n in expected.items():
            chunks = build_chunks(120.0, chunk_duration=d)
            assert len(chunks) == n

    def test_short_video_single_chunk(self):
        chunks = build_chunks(10.0)
        assert len(chunks) == 1
        assert chunks[0].logical_end == 10.0

    def test_invalid_inputs(self):
        with pytest.raises(ValueError):
            build_chunks(0)
        with pytest.raises(ValueError):
            build_chunks(10, chunk_duration=0)
        with pytest.raises(ValueError):
            build_chunks(10, overlap=-1)


class TestClampToLogical:
    def test_clamps_overlap_margins(self):
        # Segment lives in both chunks' overlap region → clamped per chunk.
        assert clamp_to_logical(29.5, 30.5, 0.0, 30.0) == (29.5, 30.0)
        assert clamp_to_logical(29.5, 30.5, 30.0, 60.0) == (30.0, 30.5)

    def test_segment_entirely_in_overlap_is_dropped(self):
        # Fully inside the overlap_before context of chunk 2 (before its window).
        assert clamp_to_logical(28.2, 29.1, 30.0, 60.0) is None

    def test_no_duplicate_on_final_timeline(self):
        # A 1s segment at the boundary keeps one half in each chunk — neither
        # half is dropped, and both halves combined equal the original span.
        lo1, hi1 = clamp_to_logical(29.6, 30.4, 0.0, 30.0)
        lo2, hi2 = clamp_to_logical(29.6, 30.4, 30.0, 60.0)
        assert (lo1, hi1, lo2, hi2) == (29.6, 30.0, 30.0, 30.4)


# ---------------------------------------------------------------------------
# PHASE 9 — order validation
# ---------------------------------------------------------------------------


class TestOrderValidation:
    def test_sequential_pass(self):
        chunks = build_chunks(90.0)
        assert validate_chunk_order(chunks) == []

    def test_missing_index_fails(self):
        chunks = build_chunks(90.0)
        dropped = [c for c in chunks if c.index != 2]
        issues = validate_chunk_order(dropped)
        assert any("missing chunk" in i for i in issues)

    def test_duplicate_index_fails(self):
        chunks = build_chunks(60.0)
        chunks = [chunks[0], chunks[0], chunks[1]]
        issues = validate_chunk_order(chunks)
        assert any("duplicate" in i for i in issues)

    def test_timeline_gap_fails(self):
        chunks = build_chunks(90.0)
        shifted = [
            Chunk(
                chunk_id=c.chunk_id,
                index=c.index,
                start=c.start + 1.0,
                end=c.end + 1.0,
                duration=c.duration,
                overlap_before=c.overlap_before,
                overlap_after=c.overlap_after,
                logical_start=c.logical_start + (1.0 if c.index == 2 else 0.0),
                logical_end=c.logical_end + (1.0 if c.index == 2 else 0.0),
            )
            for c in chunks
        ]
        issues = validate_chunk_order(shifted)
        assert any("gap/overlap" in i for i in issues)


# ---------------------------------------------------------------------------
# PHASE 6 — per-chunk validation
# ---------------------------------------------------------------------------


class TestChunkValidation:
    def test_valid_artifacts(self, tmp_path):
        audio = tmp_path / "audio.wav"
        audio.write_bytes(b"\x00" * 100)
        art = ChunkArtifacts(
            index=1,
            chunk_id="chunk_0001",
            logical_start=0.0,
            logical_end=30.0,
            segments=[{"start": 0.5, "end": 1.2, "text": "hello"}],
            cues=[{"start": 0.5, "end": 1.2, "text": "xin chào"}],
            voice_track=None,
            audio_path=str(audio),
        )
        assert validate_chunk_result(art) == []

    def test_empty_audio_fails(self, tmp_path):
        audio = tmp_path / "audio.wav"
        audio.write_bytes(b"")
        art = ChunkArtifacts(
            index=1,
            chunk_id="chunk_0001",
            logical_start=0.0,
            logical_end=30.0,
            segments=[{"start": 0.5, "end": 1.2, "text": "hello"}],
            cues=[{"start": 0.5, "end": 1.2, "text": "xin chào"}],
            voice_track=str(tmp_path / "missing.wav"),
            audio_path=str(audio),
        )
        issues = validate_chunk_result(art)
        assert any("audio" in i for i in issues)
        assert any("tts audio missing" in i for i in issues)

    def test_empty_stt_fails(self, tmp_path):
        audio = tmp_path / "audio.wav"
        audio.write_bytes(b"\x00" * 10)
        art = ChunkArtifacts(
            index=1,
            chunk_id="chunk_0001",
            logical_start=0.0,
            logical_end=30.0,
            segments=[],
            cues=[{"start": 0.5, "end": 1.2, "text": "xin chào"}],
            voice_track=None,
            audio_path=str(audio),
        )
        assert any("stt result empty" in i for i in validate_chunk_result(art))

    def test_silent_chunk_is_valid(self, tmp_path):
        audio = tmp_path / "audio.wav"
        audio.write_bytes(b"\x00" * 10)
        art = ChunkArtifacts(
            index=2,
            chunk_id="chunk_0002",
            logical_start=30.0,
            logical_end=40.0,
            segments=[],
            cues=[],
            voice_track=None,
            audio_path=str(audio),
            silent=True,
        )
        assert validate_chunk_result(art) == []


# ---------------------------------------------------------------------------
# PHASE 4/7 — bounded scheduler + per-chunk retry
# ---------------------------------------------------------------------------


class TestChunkScheduler:
    def test_sequential_results_in_order(self):
        manager = ChunkManager(90.0)
        seen: list[int] = []

        def process(chunk: Chunk):
            seen.append(chunk.index)
            return chunk.index

        scheduler = ChunkScheduler(max_concurrency=2)
        results = scheduler.run(manager, process)
        assert results == [1, 2, 3]
        assert len(seen) == 3
        assert manager.count(CHUNK_VALID) == 3

    def test_retries_only_the_failed_chunk(self):
        manager = ChunkManager(60.0)
        attempts: dict[int, int] = {}

        def process(chunk: Chunk):
            attempts[chunk.index] = attempts.get(chunk.index, 0) + 1
            if chunk.index == 2 and attempts[chunk.index] <= 2:
                raise ChunkFailedError("transient")
            return chunk.index

        scheduler = ChunkScheduler(max_concurrency=2, max_retries=2)
        results = scheduler.run(manager, process)
        assert results == [1, 2]
        # Chunk 2 failed twice and succeeded on its 3rd attempt (max_retries=2
        # means 2 retries after the initial attempt); chunk 1 ran exactly once.
        assert attempts == {1: 1, 2: 3}
        assert manager.count(CHUNK_VALID) == 2

    def test_permanent_failure_after_retries(self):
        manager = ChunkManager(60.0)

        def process(chunk: Chunk):
            raise ChunkFailedError("always")

        scheduler = ChunkScheduler(max_concurrency=2, max_retries=2)
        with pytest.raises(ChunkFailedError) as exc:
            scheduler.run(manager, process)
        assert "failed permanently" in str(exc.value)
        assert manager.count(CHUNK_FAILED_PERMANENTLY) >= 1


# ---------------------------------------------------------------------------
# PHASE 8 — manifest
# ---------------------------------------------------------------------------


class TestManifest:
    def test_manifest_reconstructs_state(self):
        manager = ChunkManager(60.0)
        for i in (1, 2):
            manager.set_status(i, CHUNK_VALID)
        manifest = manager.manifest(job_id="job_1", source_video="movie.mp4")
        assert manifest["job_id"] == "job_1"
        assert manifest["chunk_duration"] == 30.0
        assert manifest["overlap"] == 2.0
        assert manifest["total_chunks"] == 2
        assert manifest["completed_chunks"] == 2
        assert manifest["failed_chunks"] == []
        assert len(manifest["chunks"]) == 2
        assert manifest["chunks"][0]["chunk_id"] == "chunk_0001"
        assert manifest["chunks"][1]["logical_start"] == 30.0
        # The manifest is JSON-serializable (survives a round trip).
        json.dumps(manifest)


# ---------------------------------------------------------------------------
# PHASE 10 — ordered assembly
# ---------------------------------------------------------------------------


class TestAssembly:
    def _artifacts(self, index, chunks):
        c = chunks[index - 1]
        return ChunkArtifacts(
            index=index,
            chunk_id=c.chunk_id,
            logical_start=c.logical_start,
            logical_end=c.logical_end,
            segments=[
                {
                    "chunk_id": c.chunk_id,
                    "src_idx": 0,
                    "start": c.logical_start + 0.5,
                    "end": c.logical_start + 5.0,
                    "text": f"seg-{index}",
                }
            ],
            cues=[
                {
                    "chunk_id": c.chunk_id,
                    "src_idx": 0,
                    "start": c.logical_start + 0.5,
                    "end": c.logical_start + 5.0,
                    "text": f"vi-{index}",
                    "source_text": f"seg-{index}",
                }
            ],
            voice_track=None,
            audio_path="audio.wav",
        )

    def test_merge_segments_sorted_by_time_and_renumbered(self):
        chunks = build_chunks(60.0)
        arts = [self._artifacts(2, chunks), self._artifacts(1, chunks)]
        merged = merge_segments(arts)
        assert [s["start"] for s in merged] == [0.5, 30.5]
        assert merged[0]["id"] == "seg_0"
        assert merged[1]["id"] == "seg_1"
        assert [s["idx"] for s in merged] == [0, 1]

    def test_merge_cues_deduplicates(self):
        chunks = build_chunks(60.0)
        arts = [self._artifacts(1, chunks), self._artifacts(1, chunks)]
        merged = merge_cues(arts)
        assert len(merged) == 1

    def test_identity_alignment_when_a_cue_is_dropped(self):
        # Chunk 2 keeps only ONE of its two segments translated (the other was
        # skipped mid-stream). The buggy assembly merged cues independently and
        # renumbered them, so every seg_N after the first skipped row drifted.
        # The fix pairs by the stable (chunk_id, src_idx) against the canonical
        # merged transcript and falls back to the source text — ids stay 1:1.
        chunks = build_chunks(60.0)
        chunk2 = chunks[1]
        arts = [
            ChunkArtifacts(
                index=1,
                chunk_id="chunk_0001",
                logical_start=0.0,
                logical_end=30.0,
                segments=[
                    {"chunk_id": "chunk_0001", "src_idx": 0, "start": 0.5, "end": 1.5, "text": "a"},
                    {"chunk_id": "chunk_0001", "src_idx": 1, "start": 2.0, "end": 3.0, "text": "b"},
                ],
                cues=[
                    {"chunk_id": "chunk_0001", "src_idx": 0, "start": 0.5, "end": 1.5, "text": "A", "source_text": "a"},
                    {"chunk_id": "chunk_0001", "src_idx": 1, "start": 2.0, "end": 3.0, "text": "B", "source_text": "b"},
                ],
                voice_track=None,
                audio_path="audio.wav",
            ),
            ChunkArtifacts(
                index=2,
                chunk_id=chunk2.chunk_id,
                logical_start=chunk2.logical_start,
                logical_end=chunk2.logical_end,
                segments=[
                    {"chunk_id": chunk2.chunk_id, "src_idx": 0, "start": 30.5, "end": 31.5, "text": "c"},
                    {"chunk_id": chunk2.chunk_id, "src_idx": 1, "start": 32.0, "end": 33.0, "text": "d"},
                ],
                cues=[
                    # seg d (src_idx 1) has NO cue — its translation was skipped.
                    {"chunk_id": chunk2.chunk_id, "src_idx": 0, "start": 30.5, "end": 31.5, "text": "C", "source_text": "c"},
                ],
                voice_track=None,
                audio_path="audio.wav",
            ),
        ]
        merged = merge_segments(arts)
        assert [s["id"] for s in merged] == ["seg_0", "seg_1", "seg_2", "seg_3"]
        translated = assemble_translations(merged, arts)
        # 4 transcript segments in → 4 translations out, ids aligned, and the
        # skipped cue falls back to its source text. merge_cues alone yields 3.
        assert len(translated) == len(merged)
        assert translated == ["A", "B", "C", "d"]
        assert len(merge_cues(arts)) == 3

    def test_concat_pads_missing_tracks_with_silence(self, tmp_path):
        from src.core.ffmpeg import resolve_ffmpeg  # noqa: PLC0415

        if not resolve_ffmpeg():
            pytest.skip("ffmpeg unavailable")
        src = tmp_path / "a.wav"
        (tmp_path / "b").mkdir(exist_ok=True)
        out = tmp_path / "out.wav"
        concat_voice_tracks([str(src), None], [2.0, 2.0], str(out))
        assert out.exists() and out.stat().st_size > 0


# ---------------------------------------------------------------------------
# PHASE 11 — timeline validation
# ---------------------------------------------------------------------------


class TestTimelineValidation:
    def test_pass(self):
        chunks = build_chunks(60.0)
        segments = [
            {"start": 0.5, "end": 5.0, "text": "a"},
            {"start": 30.2, "end": 35.0, "text": "b"},
        ]
        assert validate_timeline(chunks, segments, 60.0) == []

    def test_overlap_not_removed_fails(self):
        chunks = build_chunks(60.0)
        segments = [
            {"start": 0.5, "end": 5.0, "text": "a"},
            {"start": 4.0, "end": 9.0, "text": "b"},
        ]
        issues = validate_timeline(chunks, segments, 60.0)
        assert any("overlap not removed" in i for i in issues)

    def test_exceeds_source_duration_fails(self):
        chunks = build_chunks(60.0)
        segments = [{"start": 0.5, "end": 65.0, "text": "a"}]
        issues = validate_timeline(chunks, segments, 60.0)
        assert any("exceeds source duration" in i for i in issues)


# ---------------------------------------------------------------------------
# PHASE 15/16 — cleanup policy
# ---------------------------------------------------------------------------


class TestCleanupManager:
    def test_cleanup_only_after_verified(self, tmp_path):
        temp = tmp_path / "temp"
        temp.mkdir()
        (temp / "manifest.json").write_text("{}")
        manager = CleanupManager(str(temp))
        assert not manager.cleanup()  # still processing → refused
        manager.keep_temp()
        assert not manager.cleanup()  # failed → kept
        manager.transition(CLEANUP_OUTPUT_VERIFIED)
        assert manager.cleanup()
        assert manager.state == CLEANUP_DONE
        assert not temp.exists()

    def test_failed_validation_keeps_temp_files(self, tmp_path):
        temp = tmp_path / "temp"
        temp.mkdir()
        (temp / "chunk_0001").mkdir()
        manager = CleanupManager(str(temp))
        manager.keep_temp()
        assert temp.exists()  # artifacts survive for debugging/retry


class TestSharedWhisperModel:
    """Phase 24 fix: chunked pipeline must not reload the ~3 GB model per chunk.

    ``stt_service._load_whisper_model`` now caches one instance per
    (model, device, compute_type) — CTranslate2 supports concurrent inference
    on a shared model, and loading large-v3 per chunk caused OOM/network flakes
    on long videos.
    """

    def test_cache_returns_same_instance(self, monkeypatch):
        import src.services.stt_service as stt

        # Prime the cache directly (the real loader would download a ~3 GB
        # model); then a call with the same key must return that instance
        # without ever invoking faster-whisper.
        sentinel = object()
        key = ("large-v3", "cpu", "int8")
        stt._WHISPER_MODEL_CACHE[key] = sentinel

        monkeypatch.setattr(stt, "ensure_cuda_libraries", lambda: None)
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):  # noqa: ARG001
            if name == "faster_whisper":
                raise AssertionError("faster-whisper imported on cache hit")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        try:
            got = stt._load_whisper_model(*key)
            assert got is sentinel
        finally:
            stt._WHISPER_MODEL_CACHE.clear()


# ---------------------------------------------------------------------------
# Performance trace — pure arithmetic over measured per-chunk stage timings.
# ---------------------------------------------------------------------------


class TestPerformanceTrace:
    def _art(self, index: int, perf: dict) -> ChunkArtifacts:
        return ChunkArtifacts(
            index=index,
            chunk_id=f"chunk_{index:04d}",
            logical_start=(index - 1) * 30.0,
            logical_end=index * 30.0,
            segments=[],
            cues=[],
            voice_track=None,
            audio_path="audio.wav",
            perf=perf,
        )

    def test_serial_chunks_report_peak_1_per_stage(self):
        c1 = {
            "wall_start_s": 0.0, "wall_end_s": 10.0,
            "slice_start_s": 0.0, "slice_s": 1.0,
            "stt_start_s": 1.0, "stt_s": 3.0,
            "translate_start_s": 4.0, "translate_s": 2.0,
            "tts_start_s": 6.0, "tts_s": 4.0,
        }
        c2 = {
            "wall_start_s": 10.0, "wall_end_s": 20.0,
            "slice_start_s": 10.0, "slice_s": 1.0,
            "stt_start_s": 11.0, "stt_s": 3.0,
            "translate_start_s": 14.0, "translate_s": 2.0,
            "tts_start_s": 16.0, "tts_s": 4.0,
        }
        trace = build_performance_trace(
            [self._art(1, c1), self._art(2, c2)],
            job_id="j",
            total_duration=60.0,
            chunk_duration=30.0,
            overlap=2.0,
            max_concurrency=4,
            max_retries=2,
        )
        assert trace["wall_elapsed_s"] == 20.0
        assert trace["chunk_level"]["peak_active"] == 1
        for stage in ("slice", "stt", "translate", "tts"):
            assert trace["stages"][stage]["peak_active"] == 1
        assert trace["stages"]["stt"]["total_ms"] == 6000
        # 6 s of total stt across chunks over a 20 s wall → avg 0.3
        assert trace["stages"]["stt"]["avg_active"] == pytest.approx(0.3)
        assert trace["stages"]["tts"]["avg_active"] == pytest.approx(0.4)

    def test_overlapping_chunks_report_stage_concurrency(self):
        # Two chunks running stt at exactly the same wall slice.
        c1 = {
            "wall_start_s": 0.0, "wall_end_s": 5.0,
            "slice_start_s": 0.0, "slice_s": 1.0,
            "stt_start_s": 1.0, "stt_s": 4.0,
            "translate_start_s": 0.0, "translate_s": 0.0,
            "tts_start_s": 0.0, "tts_s": 0.0,
        }
        c2 = {
            "wall_start_s": 0.0, "wall_end_s": 5.0,
            "slice_start_s": 0.0, "slice_s": 1.0,
            "stt_start_s": 1.0, "stt_s": 4.0,
            "translate_start_s": 0.0, "translate_s": 0.0,
            "tts_start_s": 0.0, "tts_s": 0.0,
        }
        trace = build_performance_trace(
            [self._art(1, c1), self._art(2, c2)],
            job_id="j",
            total_duration=60.0,
            chunk_duration=30.0,
            overlap=2.0,
            max_concurrency=4,
            max_retries=2,
        )
        assert trace["chunk_level"]["peak_active"] == 2
        assert trace["stages"]["stt"]["peak_active"] == 2
        assert trace["stages"]["stt"]["avg_active"] == pytest.approx(8 / 5, abs=0.01)

    def test_rows_relative_to_earliest_start(self):
        c1 = {
            "wall_start_s": 2.0, "wall_end_s": 5.0,
            "slice_start_s": 2.0, "slice_s": 1.0,
            "stt_start_s": 3.0, "stt_s": 1.0,
            "translate_start_s": 0.0, "translate_s": 0.0,
            "tts_start_s": 0.0, "tts_s": 0.0,
        }
        c2 = {
            "wall_start_s": 0.0, "wall_end_s": 3.0,
            "slice_start_s": 0.0, "slice_s": 1.0,
            "stt_start_s": 1.0, "stt_s": 1.0,
            "translate_start_s": 0.0, "translate_s": 0.0,
            "tts_start_s": 0.0, "tts_s": 0.0,
        }
        trace = build_performance_trace(
            [self._art(2, c2), self._art(1, c1)],
            job_id="j",
            total_duration=60.0,
            chunk_duration=30.0,
            overlap=2.0,
            max_concurrency=4,
            max_retries=2,
        )
        rows = trace["chunks"]
        assert rows[0]["chunk_id"] == "chunk_0002" and rows[0]["start_ms"] == 0
        assert rows[1]["chunk_id"] == "chunk_0001" and rows[1]["start_ms"] == 2000
        assert trace["wall_elapsed_s"] == 5.0

    def test_no_perf_chunks_yield_empty_measured(self):
        art = ChunkArtifacts(
            index=1,
            chunk_id="chunk_0001",
            logical_start=0.0,
            logical_end=30.0,
            segments=[],
            cues=[],
            voice_track=None,
            audio_path="a.wav",
        )  # perf defaults to None (e.g. test-only artifacts)
        trace = build_performance_trace(
            [art],
            job_id="j",
            total_duration=60.0,
            chunk_duration=30.0,
            overlap=2.0,
            max_concurrency=4,
            max_retries=2,
        )
        assert trace["config"]["measured_chunks"] == 0
        assert trace["chunks"] == []


# ---------------------------------------------------------------------------
# Streaming pipeline — stage-decoupled pools + ordered assembly buffer.
# ---------------------------------------------------------------------------


class TestVoiceStreaming:
    RATE = 44100

    def _write_track(self, path, seconds):
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.RATE)
            w.writeframes(b"\x00" * int(seconds * self.RATE) * 2)

    def test_append_pads_silence_and_wraps_wav(self, tmp_path):
        track = tmp_path / "c1.wav"
        self._write_track(track, 1.0)
        buf = io.BytesIO()
        _append_chunk_voice(buf, str(track), 1.0, self.RATE)
        _append_chunk_voice(buf, None, 2.0, self.RATE)  # missing track -> silence
        _append_chunk_voice(buf, str(track), 1.0, self.RATE)
        data = buf.getvalue()
        assert len(data) == 4 * self.RATE * 2  # 4 s of mono 16-bit
        pcm = tmp_path / "v.pcm"
        pcm.write_bytes(data)
        wav = _pcm_to_wav(str(pcm), str(tmp_path / "v.wav"), self.RATE)
        with wave.open(wav, "rb") as w:
            assert w.getnframes() == 4 * self.RATE

    def test_append_truncates_oversized_track(self, tmp_path):
        track = tmp_path / "c1.wav"
        self._write_track(track, 3.0)
        buf = io.BytesIO()
        _append_chunk_voice(buf, str(track), 1.0, self.RATE)  # track longer than window
        assert len(buf.getvalue()) == 1 * self.RATE * 2


class TestStreamingPipeline:
    def _ctx(self, tmp_path, *, dub=False):
        return ChunkPipelineContext(
            project_id="p",
            project_dir=str(tmp_path),
            source_audio=str(tmp_path / "a.wav"),
            source_language="en",
            target_language="vi",
            provider="mock",
            provider_config=None,
            api_key=None,
            model="mock",
            glossary_ver="0",
            glossary=None,
            characters=None,
            rules=None,
            dub=dub,
            voice="vi-VN-HoaiMyNeural",
            tts_engine="edge",
            workdir=str(tmp_path / "temp"),
        )

    def _stubs(self, *, fail_translate_once=(), translate_delays=None, dub=False):
        """Stub the three real stage functions so tests exercise only the
        pipeline machinery (queues, ordering, retry, voice streaming)."""
        stt_calls: dict[int, int] = {}
        translate_calls: dict[int, int] = {}

        def stt(state, ctx):
            stt_calls[state.chunk.index] = stt_calls.get(state.chunk.index, 0) + 1
            state.perf["wall_start_s"] = time.monotonic()
            state.perf["slice_start_s"] = time.monotonic()
            state.audio_path = os.path.join(ctx.workdir, f"chunks/{state.chunk.chunk_id}/audio.wav")
            if state.chunk.index == 2:
                state.segments = []  # chunk 2 is genuinely silent
                state.silent = True
                return
            state.segments = [
                {
                    "chunk_id": state.chunk.chunk_id,
                    "src_idx": 0,
                    "start": state.chunk.logical_start + 0.5,
                    "end": state.chunk.logical_start + 5.0,
                    "text": f"seg-{state.chunk.index}",
                }
            ]

        def translate(state, ctx):
            if not state.segments:
                return
            translate_calls[state.chunk.index] = translate_calls.get(state.chunk.index, 0) + 1
            if state.chunk.index in fail_translate_once and translate_calls[state.chunk.index] == 1:
                raise ChunkFailedError(f"transient {state.chunk.index}")
            if translate_delays and state.chunk.index in translate_delays:
                time.sleep(translate_delays[state.chunk.index])
            state.cues = [
                {
                    "chunk_id": state.chunk.chunk_id,
                    "src_idx": 0,
                    "start": state.segments[0]["start"],
                    "end": state.segments[0]["end"],
                    "source_text": state.segments[0]["text"],
                    "text": f"vi-{state.chunk.index}",
                }
            ]

        def tts(state, ctx):
            if not state.segments:
                return
            state.perf["tts_start_s"] = time.monotonic()
            if dub:
                import wave as _wave  # noqa: PLC0415

                out_dir = os.path.dirname(state.audio_path)
                os.makedirs(out_dir, exist_ok=True)
                track = os.path.join(out_dir, "voice.wav")
                with _wave.open(track, "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(44100)
                    w.writeframes(b"\x00" * int(round((state.chunk.logical_end - state.chunk.logical_start) * 44100)) * 2)
                state.voice_track = track

        return stt, translate, tts, stt_calls, translate_calls

    def _monkeypatch_stages(self, monkeypatch, stubs):
        monkeypatch.setattr(chunk_service, "_run_stt_stage", stubs[0])
        monkeypatch.setattr(chunk_service, "_run_translate_stage", stubs[1])
        monkeypatch.setattr(chunk_service, "_run_tts_stage", stubs[2])

    def test_ordered_commit_despite_out_of_order_completion(self, tmp_path, monkeypatch):
        # Chunk 1's translation is slow; chunks 2,3 finish first. The assembly
        # buffer must still commit C1 -> C2 -> C3.
        manager = ChunkManager(6.0, chunk_duration=2.0, overlap=0.0)
        ctx = self._ctx(tmp_path)
        stubs = self._stubs(translate_delays={1: 0.4})
        self._monkeypatch_stages(monkeypatch, stubs)
        commits: list[str] = []

        pipeline = StreamingChunkPipeline(
            manager=manager,
            ctx=ctx,
            stt_workers=2,
            translate_workers=2,
            tts_workers=2,
            max_retries=0,
            on_event=lambda level, msg: commits.append(msg) if msg.startswith("CHUNK_ASSEMBLED") else None,
        )
        assembled = pipeline.run()
        assert [a.index for a in assembled] == [1, 2, 3]
        assert commits == ["CHUNK_ASSEMBLED chunk_0001", "CHUNK_ASSEMBLED chunk_0002", "CHUNK_ASSEMBLED chunk_0003"]
        assert manager.count(CHUNK_VALID) == 3

    def test_retry_at_stage_reuses_earlier_work(self, tmp_path, monkeypatch):
        # Translation fails once for chunk 1, then succeeds. STT must NOT re-run.
        manager = ChunkManager(6.0, chunk_duration=2.0, overlap=0.0)
        ctx = self._ctx(tmp_path)
        stubs = self._stubs(fail_translate_once=(1,))
        self._monkeypatch_stages(monkeypatch, stubs)
        stt_calls, translate_calls = stubs[3], stubs[4]

        pipeline = StreamingChunkPipeline(
            manager=manager,
            ctx=ctx,
            stt_workers=2,
            translate_workers=2,
            tts_workers=2,
            max_retries=1,
        )
        assembled = pipeline.run()
        assert [a.index for a in assembled] == [1, 2, 3]
        assert stt_calls[1] == 1          # STT never redone
        assert translate_calls[1] == 2    # translation retried once
        assert manager.get(1).retries == 1

    def test_permanent_failure_stops_job(self, tmp_path, monkeypatch):
        manager = ChunkManager(6.0, chunk_duration=2.0, overlap=0.0)
        ctx = self._ctx(tmp_path)
        stubs = self._stubs(fail_translate_once=(1,))
        self._monkeypatch_stages(monkeypatch, stubs)

        pipeline = StreamingChunkPipeline(
            manager=manager,
            ctx=ctx,
            stt_workers=2,
            translate_workers=2,
            tts_workers=2,
            max_retries=0,
        )
        with pytest.raises(ChunkFailedError) as exc:
            pipeline.run()
        assert "failed permanently" in str(exc.value)
        assert manager.count(CHUNK_FAILED_PERMANENTLY) == 1

    def test_silent_chunk_skips_translate_and_tts(self, tmp_path, monkeypatch):
        manager = ChunkManager(6.0, chunk_duration=2.0, overlap=0.0)
        ctx = self._ctx(tmp_path)
        stubs = self._stubs()
        self._monkeypatch_stages(monkeypatch, stubs)
        translate_calls = stubs[4]

        pipeline = StreamingChunkPipeline(
            manager=manager,
            ctx=ctx,
            stt_workers=2,
            translate_workers=2,
            tts_workers=2,
            max_retries=0,
        )
        assembled = pipeline.run()
        assert len(assembled) == 3
        assert assembled[1].silent is True
        assert assembled[1].segments == [] and assembled[1].cues == []
        assert 2 not in translate_calls

    def test_streaming_voice_track_dub(self, tmp_path, monkeypatch):
        # dub=True: chunk 2 is silent (no track), chunks 1+3 have a track.
        # The streaming assembly pads with silence so the final WAV covers the
        # full source duration even though it was written incrementally.
        manager = ChunkManager(6.0, chunk_duration=2.0, overlap=0.0)
        ctx = self._ctx(tmp_path, dub=True)
        stubs = self._stubs(dub=True)
        self._monkeypatch_stages(monkeypatch, stubs)

        pipeline = StreamingChunkPipeline(
            manager=manager,
            ctx=ctx,
            stt_workers=2,
            translate_workers=2,
            tts_workers=2,
            max_retries=0,
        )
        assembled = pipeline.run()
        assert [a.index for a in assembled] == [1, 2, 3]
        assert pipeline.voice_track_path and os.path.isfile(pipeline.voice_track_path)
        with wave.open(pipeline.voice_track_path, "rb") as w:
            assert w.getframerate() == 44100
            assert w.getnframes() == 6 * 44100  # full 6 s source duration

    def test_validate_workers_ge_one(self, tmp_path):
        manager = ChunkManager(6.0, chunk_duration=2.0)
        ctx = self._ctx(tmp_path)
        with pytest.raises(ValueError):
            StreamingChunkPipeline(manager=manager, ctx=ctx, stt_workers=0, translate_workers=1, tts_workers=1)
