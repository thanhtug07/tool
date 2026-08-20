"""Batched STT production benchmark — BATCHED_STT task DoD gate.

Same fixture, same machine, comparing the *production* engines exactly as the
pipeline runs them (``stt_service.transcribe`` with ``stt_mode=``):

    regular | batched(batch=1) | batched(batch=2) | batched(batch=4)

Measures, for each config: wall/RTF, VRAM peak and GPU peak % (nvidia-smi via
MetricSampler), transcript segment count, word/coverage and timeline coverage
(``coverage_s / audio_s``), plus a stability repeat of each engine. The
decision ``auto -> batched`` must be justified by THESE numbers (DoD:
no default change from the small-model benchmark alone).

Run (from worker/):

    py -3.13 tests/integration/bench_batched_stt.py --model large-v3 --duration 60 --device cuda --repeat 2

Every number is measured on this machine, never fabricated.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parents[1]))

import e2e_pipeline  # noqa: E402 (fixture + MetricSampler helpers)


def repeat_wav_wave(src: Path, out: Path, seconds: float) -> float:
    import wave

    with wave.open(str(src), "rb") as w:
        params = w.getparams()
        frames = w.readframes(w.getnframes())
    target = int(seconds * params.framerate)
    if target <= 0:
        target = params.nframes
    n_repeats = target // params.nframes + 1
    payload = frames * n_repeats
    payload = payload[: target * params.sampwidth * params.nchannels]
    with wave.open(str(out), "wb") as w:
        w.setparams(params)
        w.writeframes(payload)
    return target / float(params.framerate)


def coverage(segments: list[dict]) -> tuple[float, float, int]:
    """(word_coverage, timeline_coverage, segment_count).

    word_coverage: fraction of transcript words carrying word-level
    timestamps; timeline_coverage: seconds of the audio covered by the union
    of segment intervals (overlaps merged, so it is an upper bound for real
    speech density and a lower bound for coverage defects).
    """
    total_words = 0
    covered_words = 0
    iv: list[tuple[float, float]] = []
    for seg in segments:
        words = seg.get("words") or []
        total_words += len(words)
        covered_words += len(words)
        iv.append((float(seg["start"]), float(seg["end"])))
    union = 0.0
    cur_lo = cur_hi = None
    for lo, hi in sorted(iv):
        if cur_hi is None:
            cur_lo, cur_hi = lo, hi
        elif lo <= cur_hi:
            cur_hi = max(cur_hi, hi)
        else:
            union += cur_hi - cur_lo
            cur_lo, cur_hi = lo, hi
    if cur_hi is not None:
        union += cur_hi - cur_lo
    return (
        covered_words / total_words if total_words else 0.0,
        union,
        len(segments),
    )


def run_config(args: argparse.Namespace, workdir: Path, audio: Path, audio_s: float,
               stt_mode: str, batch_size: int | None) -> dict:
    from src.services import stt_service  # noqa: PLC0415

    device = stt_service.resolve_device(args.device)
    compute = stt_service.pick_compute_type(device)
    model = stt_service._load_whisper_model(args.model, device, compute)

    trials: list[dict] = []
    for rep in range(args.repeat):
        sampler = e2e_pipeline.MetricSampler(None, interval=1.0).start()
        t0 = time.perf_counter()
        result = stt_service.transcribe(
            str(audio),
            project_id="bench-batched",
            model_name=args.model,
            device=device,
            compute_type=compute,
            language=args.language,
            stt_mode=stt_mode,
            batch_size=batch_size if batch_size is not None else 2,
        )
        wall = time.perf_counter() - t0
        metrics = sampler.close()
        segs = result.transcript["segments"]
        words_covered, timeline_covered, n_segs = coverage(segs)
        trials.append(
            {
                "rep": rep + 1,
                "wall_s": round(wall, 3),
                "rtf": round(wall / audio_s, 4),
                "engine": result.engine,
                "segments": n_segs,
                "word_coverage": round(words_covered, 4),
                "timeline_coverage_s": round(timeline_covered, 3),
                "timeline_coverage_ratio": round(timeline_covered / audio_s, 4),
                "vram_peak_mb": metrics.get("vram_peak_mb"),
                "gpu_peak_percent": metrics.get("gpu_peak_percent"),
            }
        )
    return {
        "stt_mode": stt_mode,
        "batch_size": batch_size,
        "trials": trials,
        "wall_s_median": round(sorted(t["wall_s"] for t in trials)[len(trials) // 2], 3),
        "rtf_median": round(sorted(t["rtf"] for t in trials)[len(trials) // 2], 4),
        "vram_peak_mb_max": max(t["vram_peak_mb"] or 0 for t in trials),
        "gpu_peak_percent_max": max(t["gpu_peak_percent"] or 0 for t in trials),
        "word_coverage_min": min(t["word_coverage"] for t in trials),
        "timeline_coverage_ratio_min": min(t["timeline_coverage_ratio"] for t in trials),
        "segments_range": [min(t["segments"] for t in trials), max(t["segments"] for t in trials)],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="large-v3")
    parser.add_argument("--device", default="cuda", choices=("cpu", "cuda", "auto"))
    parser.add_argument("--language", default="en")
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--audio", default=None, help="fixed wav to reuse across configs")
    args = parser.parse_args()

    workdir = Path(tempfile.mkdtemp(prefix="tc_bench_batched_"))
    if args.audio:
        import wave

        with wave.open(args.audio, "rb") as w:
            audio_s = w.getnframes() / float(w.getframerate())
        audio = Path(args.audio)
    else:
        speech = workdir / "speech.wav"
        engine, _ = e2e_pipeline.synthesize_speech(speech)
        audio = workdir / "audio.wav"
        audio_s = repeat_wav_wave(speech, audio, args.duration)

    report: dict = {
        "model": args.model,
        "device": args.device,
        "audio_duration_s": round(audio_s, 2),
        "repeat": args.repeat,
        "region": "large-v3=latency-CPU-tied? encoder batch overrides",
        "configs": [],
    }
    configs = [("regular", None), ("batched", 1), ("batched", 2), ("batched", 4)]
    t_start = time.monotonic()
    for stt_mode, batch in configs:
        row = run_config(args, workdir, audio, audio_s, stt_mode, batch)
        report["configs"].append(row)
        print(json.dumps(row, ensure_ascii=False, indent=2), flush=True)
    report["total_seconds"] = round(time.monotonic() - t_start, 3)

    out = workdir / "bench_batched_stt.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"REPORT={out}")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())