"""BATCHED STT prototype - ISOLATED research harness (worker/tests/integration).

Investigates whether ``BatchedInferencePipeline`` (faster-whisper 1.2.1) can
match the current per-chunk ``transcribe`` (sentence-level segments) after an
**offline, word-timestamp-based reconstruction** - i.e. without re-running any
inference. Production STT code is imported read-only; nothing here changes
``src/``.

Pipeline studied (production today):

    chunk wav -> faster-whisper transcribe(vad_filter, beam=5) -> sentence segments

Batched prototype studied here:

    chunk wav -> BatchedInferencePipeline(beam=5, word_timestamps=True, batch=N)
              -> RAW segments (1 per ~30s window, coarse start/end)
              -> reconstruction FROM WORD TIMESTAMPS (no re-inference)
              -> normalized segments (same schema as production build_transcript)

Reconstruction rules (deliberately reuse *existing* subtitle/timing concepts,
nothing invented blind):

- sentence boundary  : word whose stripped text ends with a sentence ender
                       (``. ! ? 。 ！ ？ `` ...) - punctuation boundaries
- silence boundary   : inter-word gap > ``gap_threshold_seconds`` -> split
- max duration       : segment span capped at ``max_segment_seconds``
                       (readability ceiling, mirrors SubtitleService CPS logic)
- max chars          : segment text capped at ``max_chars`` (derived from the
                       language's SubtitleStyle ``max_chars_per_line`` if found,
                       else a conservative default)

Modes (all measured, never fabricated):

    compare   regular vs batched+reconstructed over identical audio
    chunk     60s-chunk simulation: offsets + clamp + merge + identity checks
    compat    feed reconstructed transcript through TranslationService (mock)
              + SubtitleService + (TTS cue format check) without interface change
    bench     batch=1/2/4: STT wall, RTF, VRAM, GPU%, reconstructed segment count
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parents[1]))  # worker/ for `src.*` imports

import e2e_pipeline  # noqa: E402  (fixture + MetricSampler helpers)
from bench_stt import repeat_wav_wave  # noqa: E402  (shared helper)

# -- reconstruction constants (configurable; NOT "conclusions") ---------------
SENTENCE_ENDERS = frozenset(".!?。！？…")
DEFAULT_MAX_SEGMENT_SECONDS = 8.0
DEFAULT_GAP_THRESHOLD_SECONDS = 1.0  # only a *real* pause (not TTS microsilence)
SENTENCE_MIN_WORDS = 3
SENTENCE_MIN_CHARS = 8
DEFAULT_MAX_CHARS = 96  # conservative fallback (48/chars-per-line * 2, en style)


def _char_cap(language: str) -> int:
    from src.services.subtitle_service import default_style  # noqa: PLC0415

    try:
        return default_style(language).max_chars_per_line * 2
    except Exception:  # noqa: BLE001 - fallback cap
        return DEFAULT_MAX_CHARS


# -- model ---------------------------------------------------------------------


def load_model(model_name: str, device: str, compute_type: str | None = None):
    from src.core.cuda_libs import ensure_cuda_libraries  # noqa: PLC0415
    from src.services.stt_service import (
        _WHISPER_MODEL_CACHE,
        _WHISPER_MODEL_LOCK,
        pick_compute_type,
        resolve_device,
    )  # noqa: PLC0415

    resolved = resolve_device(device)
    compute = compute_type or pick_compute_type(resolved)
    key = (model_name, resolved, compute, None)
    cached = _WHISPER_MODEL_CACHE.get(key)
    if cached is not None:
        return cached, resolved, compute
    ensure_cuda_libraries()
    from faster_whisper import WhisperModel  # noqa: PLC0415

    with _WHISPER_MODEL_LOCK:
        cached = _WHISPER_MODEL_CACHE.get(key)
        if cached is None:
            try:
                cached = WhisperModel(model_name, device=resolved, compute_type=compute, local_files_only=True)
            except Exception:  # noqa: BLE001 - not cached -> allow download
                cached = WhisperModel(model_name, device=resolved, compute_type=compute)
            _WHISPER_MODEL_CACHE[key] = cached
    return cached, resolved, compute


def run_regular(model, audio_path: str, language: str, *, word_timestamps: bool = False):
    """Mirror production `_run_and_build`: vad_filter=True, beam_size=5."""
    result = model.transcribe(
        audio_path,
        language=language,
        vad_filter=True,
        beam_size=5,
        word_timestamps=word_timestamps,
    )
    segments, info = result if isinstance(result, tuple) else (result, None)
    return list(segments), info


def _segment_words(seg) -> list:
    """Extract word objects from a faster-whisper segment (word_timestamps=True)."""
    return list(getattr(seg, "words", None) or [])


def _norm_segments(segments) -> list[dict]:
    """Normalize a list of faster-whisper segments to the canonical dict shape
    used by ``build_transcript`` (without materializing ids -> caller merges)."""
    out = []
    for seg in segments:
        text = (getattr(seg, "text", None) or "").strip()
        if not text:
            continue
        out.append(
            {
                "start": max(0.0, float(getattr(seg, "start", 0.0) or 0.0)),
                "end": max(0.0, float(getattr(seg, "end", 0.0) or 0.0)),
                "text": text,
                "language": getattr(seg, "language", None),
                "confidence": getattr(seg, "avg_logprob", None),
            }
        )
    return out


def _words_to_segments(words: list[dict], *, language: str) -> list[dict]:
    """Reconstruct sentence/phrase segments from word timestamps.

    ``words``: list of ``{"start": s, "end": e, "text": w, "probability": p}``.
    Pure offline rule-based pass (no inference):
    1. split at a word ending with sentence punctuation (sentence boundary),
    2. split when the gap to the previous word exceeds ``gap_threshold``
       (silence boundary),
    3. hard caps: span <= ``max_segment_seconds`` and text <= ``max_chars``
       (readability/CPS ceiling, mirroring SubtitleService rules).
    """
    cap = _char_cap(language)
    max_seg = DEFAULT_MAX_SEGMENT_SECONDS
    gap_thresh = DEFAULT_GAP_THRESHOLD_SECONDS

    rebuilt: list[dict] = []
    cur_text = ""
    cur_start = None
    cur_end = None
    prev_end = None
    cur_conf = 0.0
    cur_n = 0
    cur_words = 0

    def flush() -> None:
        nonlocal cur_text, cur_start, cur_end, cur_conf, cur_n, cur_words
        if not cur_text:
            return
        rebuilt.append(
            {
                "start": round(float(cur_start), 3),
                "end": round(float(cur_end), 3),
                "text": cur_text,
                "language": language,
                "confidence": round(cur_conf / cur_n, 4) if cur_n else None,
            }
        )
        cur_text = ""
        cur_start = None
        cur_end = None
        cur_conf = 0.0
        cur_n = 0
        cur_words = 0

    for w in words:
        start = float(w["start"])
        end = float(w["end"])
        stripped = (w["text"] or "").strip()
        if not stripped:
            continue
        if cur_text:
            cur_text += " "
        cur_text += stripped
        if cur_start is None:
            cur_start = start
        cur_end = end
        cur_words += 1
        conf = float(w.get("probability") if w.get("probability") is not None else 0.5)
        cur_conf += conf
        cur_n += 1

        gap = (start - prev_end) if prev_end is not None else 0.0
        prev_end = end
        span = cur_end - cur_start
        is_sentence_end = stripped.endswith(tuple(SENTENCE_ENDERS))
        too_long = span > max_seg or len(cur_text) > cap
        gappy = gap > gap_thresh

        # Sentence boundary: punctuation at >= SENTENCE_MIN_CHARS (never a
        # 2-char fragment). Silence boundary: a real gap with enough context.
        if is_sentence_end and len(cur_text) >= SENTENCE_MIN_CHARS:
            flush()
        elif too_long:
            flush()
        elif gappy and cur_words >= SENTENCE_MIN_WORDS and len(cur_text) >= SENTENCE_MIN_CHARS:
            flush()

    flush()
    return rebuilt


def _flatten_words(segments) -> list[dict]:
    """Flatten all word objects across raw batched segments into dicts."""
    out: list[dict] = []
    for seg in segments:
        for w in _segment_words(seg):
            out.append(
                {
                    "start": float(getattr(w, "start", 0.0) or 0.0),
                    "end": float(getattr(w, "end", 0.0) or 0.0),
                    "text": getattr(w, "word", "") or "",
                    "probability": getattr(w, "probability", None),
                }
            )
    out.sort(key=lambda w: (w["start"], w["end"]))
    return out


def _text_tokens(text: str) -> list[str]:
    import re  # noqa: PLC0415

    return [t for t in re.split(r"[\s,.:;!?。，、；：！？…-]+", text.lower()) if t]


def run_batched(model, audio_path: str, language: str, *, batch_size: int, word_timestamps: bool = True):
    """Raw batched transcription. Returns (raw_segment_list, info)."""
    from faster_whisper.transcribe import BatchedInferencePipeline  # noqa: PLC0415

    bp = BatchedInferencePipeline(model)
    result = bp.transcribe(
        audio_path,
        language=language,
        beam_size=5,
        batch_size=batch_size,
        word_timestamps=word_timestamps,
        vad_filter=True,
    )
    segments, info = result if isinstance(result, tuple) else (result, None)
    return list(segments), info


# -- quality metrics -----------------------------------------------------------

def _timeline_metrics(segments: list[dict], total: float) -> dict:
    """coverage / gap / overlap over the source timeline, per segment set."""
    if not segments:
        return {"coverage_s": 0.0, "coverage_ratio": 0.0, "gap_max_s": 0.0, "overlap_max_s": 0.0}
    sorted_segs = sorted(segments, key=lambda s: s["start"])
    coverage = 0.0
    prev_end = 0.0
    gap_max = 0.0
    overlap_max = 0.0
    for seg in sorted_segs:
        start = max(0.0, seg["start"])
        end = min(total, max(start, seg["end"]))
        if start < prev_end:
            overlap_max = max(overlap_max, prev_end - start)
        elif start > prev_end:
            gap_max = max(gap_max, start - prev_end)
        if end > start:
            coverage += end - start
        prev_end = max(prev_end, end)
    return {
        "n": len(segments),
        "coverage_s": round(coverage, 3),
        "coverage_ratio": round(coverage / total, 3) if total else 0.0,
        "gap_max_s": round(gap_max, 3),
        "overlap_max_s": round(overlap_max, 3),
        "dur_min_s": round(min((s["end"] - s["start"]) for s in segments), 3),
        "dur_max_s": round(max((s["end"] - s["start"]) for s in segments), 3),
        "dur_avg_s": round(sum((s["end"] - s["start"]) for s in segments) / len(segments), 3) if segments else 0.0,
    }


def _word_delta(ref_tokens: list[str], cand_tokens: list[str]) -> dict:
    """missing = in ref but not cand; extra = in cand but not ref (token multiset)."""
    from collections import Counter  # noqa: PLC0415

    ref, cand = Counter(ref_tokens), Counter(cand_tokens)
    missing = sum((ref - cand).values())
    extra = sum((cand - ref).values())
    total = sum(ref.values())
    return {
        "ref_tokens": total,
        "cand_tokens": sum(cand.values()),
        "missing_words": missing,
        "extra_words": extra,
        "coverage_ratio": round((total - missing) / total, 4) if total else 1.0,
    }


def _drift_metrics(ref_segments: list[dict], cand_segments: list[dict]) -> dict:
    """Per-cue timestamp drift of candidate vs reference, matched 1:1 by order."""
    n = min(len(ref_segments), len(cand_segments))
    if n == 0:
        return {"n_matched": 0, "start_drift_avg_s": None, "end_drift_avg_s": None}
    start_drift = [cand_segments[i]["start"] - ref_segments[i]["start"] for i in range(n)]
    end_drift = [cand_segments[i]["end"] - ref_segments[i]["end"] for i in range(n)]
    return {
        "n_matched": n,
        "start_drift_avg_s": round(sum(start_drift) / n, 3),
        "start_drift_max_s": round(max(map(abs, start_drift)), 3),
        "end_drift_avg_s": round(sum(end_drift) / n, 3),
        "end_drift_max_s": round(max(map(abs, end_drift)), 3),
    }


# -- mode: compare ---------------------------------------------------------------

def run_compare(args: argparse.Namespace) -> int:
    workdir = Path(tempfile.mkdtemp(prefix="tc_bproto_cmp_"))
    audio = Path(args.audio) if args.audio else None
    if audio is None:
        speech = workdir / "speech.wav"
        engine, _ = e2e_pipeline.synthesize_speech(speech)
        audio = workdir / "audio.wav"
        real_dur = repeat_wav_wave(speech, audio, args.duration)
        print(f"fixture: engine={engine} audio={audio} duration={real_dur:.2f}s", flush=True)
    else:
        import wave  # noqa: PLC0415

        with wave.open(str(audio), "rb") as w:
            real_dur = w.getnframes() / float(w.getframerate())

    model, device, compute = load_model(args.model, args.device)
    print(f"device={device} compute={compute} model={args.model}", flush=True)

    t0 = time.perf_counter()
    reg_segs, reg_info = run_regular(model, str(audio), args.language)
    reg_wall = time.perf_counter() - t0
    reg_norm = _norm_segments(reg_segs)

    t0 = time.perf_counter()
    bat_segs, bat_info = run_batched(model, str(audio), args.language, batch_size=args.batch_size)
    bat_wall = time.perf_counter() - t0
    words = _flatten_words(bat_segs)
    bat_norm = _words_to_segments(words, language=args.language)

    report = {
        "mode": "compare",
        "audio_duration_s": round(real_dur, 2),
        "model": args.model,
        "device": device,
        "regular": {
            "wall_s": round(reg_wall, 3),
            "rtf": round(reg_wall / real_dur, 4),
            **{"segments": len(reg_norm)},
            **_timeline_metrics(reg_norm, real_dur),
            "sample": reg_norm[:6],
        },
        "batched_raw": {
            "wall_s": round(bat_wall, 3),
            "rtf": round(bat_wall / real_dur, 4),
            "raw_segments": len(bat_segs),
            "total_words": len(words),
        },
        "batched_reconstructed": {
            **{"segments": len(bat_norm)},
            **_timeline_metrics(bat_norm, real_dur),
            "sample": bat_norm[:6],
        },
        "quality": {
            "segment_count_delta": len(bat_norm) - len(reg_norm),
            **_word_delta(
                [t for s in reg_norm for t in _text_tokens(s["text"])],
                [t for s in bat_norm for t in _text_tokens(s["text"])],
            ),
            **_drift_metrics(reg_norm, bat_norm),
        },
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"EVIDENCE_DIR={workdir}", flush=True)
    return 0


# -- mode: chunk (identity) -------------------------------------------------------

def run_chunk(args: argparse.Namespace) -> int:
    from src.services.chunk_service import (  # noqa: PLC0415
        ChunkManager,
        clamp_to_logical,
        merge_segments,
    )

    workdir = Path(tempfile.mkdtemp(prefix="tc_bproto_chunk_"))
    speech = workdir / "speech.wav"
    engine, _ = e2e_pipeline.synthesize_speech(speech)
    audio = workdir / "audio.wav"
    real_dur = repeat_wav_wave(speech, audio, args.duration)
    print(f"fixture: engine={engine} audio={audio} duration={real_dur:.2f}s", flush=True)

    model, device, compute = load_model(args.model, args.device)
    chunk_duration = args.chunk_duration
    overlap = args.overlap
    manager = ChunkManager(real_dur, chunk_duration=chunk_duration, overlap=overlap)
    chunk_dir = workdir / "chunks"
    chunk_dir.mkdir(exist_ok=True)

    per_chunk_segments: list[dict] = []
    chunk_ok: list[dict] = []
    for chunk in manager.chunks:
        wav = chunk_dir / f"{chunk.chunk_id}.wav"
        from src.services.chunk_service import slice_audio  # noqa: PLC0415

        slice_audio(str(audio), str(wav), chunk.start, chunk.duration, ffmpeg_bin=str(e2e_pipeline.find_ffmpeg()))
        bat_segs, _ = run_batched(model, str(wav), args.language, batch_size=args.batch_size)
        words = _flatten_words(bat_segs)
        local = _words_to_segments(words, language=args.language)
        for seg in local:
            g_start = chunk.start + float(seg["start"])
            g_end = chunk.start + float(seg["end"])
            clamped = clamp_to_logical(g_start, g_end, chunk.logical_start, chunk.logical_end)
            if clamped is None:
                continue
            lo, hi = clamped
            per_chunk_segments.append(
                {
                    "chunk_id": chunk.chunk_id,
                    "src_idx": len(per_chunk_segments),
                    "start": round(lo, 3),
                    "end": round(hi, 3),
                    "text": seg["text"],
                    "language": args.language,
                }
            )
        chunk_ok.append({"chunk_id": chunk.chunk_id, "segments": len(local)})

    merged = merge_segments(
        [SimpleNamespace(segments=per_chunk_segments)]
    )
    order_issues = []
    prev_end = 0.0
    for seg in merged:
        if seg["start"] < prev_end - 1e-3:
            order_issues.append(f"overlap {seg['start']} < {prev_end}")
        if seg["start"] < 0:
            order_issues.append("negative start")
        prev_end = max(prev_end, seg["end"])
    report = {
        "mode": "chunk",
        "audio_duration_s": round(real_dur, 2),
        "chunks_total": len(manager.chunks),
        "chunks_segments": chunk_ok,
        "merged_segments": len(merged),
        "order_issues": order_issues,
        "identity_pass": not order_issues,
        "merged_sample": merged[:8],
        "timeline": _timeline_metrics(merged, real_dur),
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"EVIDENCE_DIR={workdir}", flush=True)
    return 0 if report["identity_pass"] else 2


# -- mode: compat (pipeline interfaces, no code change) ----------------------------

def run_compat(args: argparse.Namespace) -> int:
    from src.api.schemas import TranslationBlock, TranslationItem  # noqa: PLC0415
    from src.services.providers.base import SourceSegment  # noqa: PLC0415
    from src.services.providers.translation.mock_provider import MockProvider  # noqa: PLC0415
    from src.services.subtitle_service import CueSource, SubtitleService  # noqa: PLC0415
    from src.services.translation_service import TranslationService  # noqa: PLC0415

    workdir = Path(tempfile.mkdtemp(prefix="tc_bproto_compat_"))
    speech = workdir / "speech.wav"
    engine, _ = e2e_pipeline.synthesize_speech(speech)
    audio = workdir / "audio.wav"
    real_dur = repeat_wav_wave(speech, audio, args.duration)
    print(f"fixture: engine={engine} audio={audio} duration={real_dur:.2f}s", flush=True)

    model, device, compute = load_model(args.model, args.device)
    bat_segs, _ = run_batched(model, str(audio), args.language, batch_size=args.batch_size)
    words = _flatten_words(bat_segs)
    segs = _words_to_segments(words, language=args.language)

    # 1) TranslationService (production interface, MockProvider offline).
    sources = [
        SourceSegment(idx=i, segment_id=f"seg_{i}", text=s["text"], speaker=None)
        for i, s in enumerate(segs)
    ]
    service = TranslationService()
    blocks = service.translate_segments(
        sources,
        target_language=args.target_language,
        provider=MockProvider(nop_translate=args.nop_translate),
        model="mock",
        glossary_ver="0",
    )
    flat = [t for b in blocks for t in b.translations]
    by_id = {t.segment_id: t.translated_text for t in flat}
    missing = [s.segment_id for s in sources if s.segment_id not in by_id]
    assert not missing, f"translation missing {missing}"

    # 2) SubtitleService (production interface).
    cues = [
        CueSource(idx=i, segment_id=sources[i].segment_id, start=segs[i]["start"], end=segs[i]["end"], text=by_id[sources[i].segment_id])
        for i in range(len(segs))
    ]
    doc = SubtitleService().generate(
        cues,
        style=None,
        language=args.target_language,
        project_id="proto",
    )
    warnings = doc.warnings
    report = {
        "mode": "compat",
        "audio_duration_s": round(real_dur, 2),
        "segments": len(segs),
        "translated": len(flat),
        "translated_missing": len(missing),
        "subtitle_cues": len(doc.document.cues),
        "subtitle_warnings": warnings,
        "subtitle_ass_bytes": len(doc.ass_content),
        "subtitle_srt_bytes": len(doc.srt_content),
        "cues_sample": [
            {"n": c.cue_number, "start": c.start, "end": c.end, "text": c.text}
            for c in doc.document.cues[:6]
        ],
        "compat_pass": (len(flat) == len(segs) and len(doc.document.cues) >= 1 and not missing),
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"EVIDENCE_DIR={workdir}", flush=True)
    return 0 if report["compat_pass"] else 3


# -- mode: bench (batch size 1/2/4) ----------------------------------------------

def run_bench(args: argparse.Namespace) -> int:
    workdir = Path(tempfile.mkdtemp(prefix="tc_bproto_bench_"))
    speech = workdir / "speech.wav"
    engine, _ = e2e_pipeline.synthesize_speech(speech)
    audio = workdir / "audio.wav"
    real_dur = repeat_wav_wave(speech, audio, args.duration)
    print(f"fixture: engine={engine} audio={audio} duration={real_dur:.2f}s", flush=True)

    model, device, compute = load_model(args.model, args.device)
    results = []
    for batch in args.batches:
        t0 = time.perf_counter()
        sampler = e2e_pipeline.MetricSampler(os.getpid(), interval=1.0).start()
        try:
            bat_segs, _ = run_batched(model, str(audio), args.language, batch_size=batch)
        finally:
            metrics = sampler.close()
        wall = time.perf_counter() - t0
        words = _flatten_words(bat_segs)
        segs = _words_to_segments(words, language=args.language)
        results.append(
            {
                "batch_size": batch,
                "wall_s": round(wall, 3),
                "rtf": round(wall / real_dur, 4),
                "raw_segments": len(bat_segs),
                "reconstructed_segments": len(segs),
                "total_words": len(words),
                **{k: metrics.get(k) for k in ("gpu_peak_percent", "vram_peak_mb", "cpu_peak_percent", "ram_peak_mb")},
            }
        )
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)

    report = {"mode": "bench", "audio_duration_s": round(real_dur, 2), "model": args.model, "device": device, "results": results}
    out = workdir / "batched_bench.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nREPORT={out}")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--duration", type=float, default=60.0)
    common.add_argument("--audio", default=None)
    common.add_argument("--model", default="small")
    common.add_argument("--device", default="cuda", choices=("cpu", "cuda", "auto"))
    common.add_argument("--language", default="en")
    common.add_argument("--batch-size", type=int, default=8)

    p_cmp = sub.add_parser("compare", parents=[common])
    p_cmp.set_defaults(fn=run_compare)

    p_chunk = sub.add_parser("chunk", parents=[common])
    p_chunk.add_argument("--chunk-duration", type=float, default=60.0)
    p_chunk.add_argument("--overlap", type=float, default=2.0)
    p_chunk.set_defaults(fn=run_chunk)

    p_compat = sub.add_parser("compat", parents=[common])
    p_compat.add_argument("--target-language", default="vi")
    p_compat.add_argument("--nop-translate", action="store_true")
    p_compat.set_defaults(fn=run_compat)

    p_bench = sub.add_parser("bench", parents=[common])
    p_bench.add_argument("--batches", default="1,2,4", help="comma list of batch sizes")
    p_bench.set_defaults(fn=run_bench)

    args = parser.parse_args()
    if args.mode == "bench":
        args.batches = [int(x) for x in args.batches.split(",") if x]
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())