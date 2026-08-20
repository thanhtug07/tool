"""STT-stage benchmark driver (chunked pipeline, renderless).

Measures the REAL STT stage in isolation over the real chunked pipeline by
driving /v1/automation/chunked with the offline ``mock`` provider (translation
≈0, no network, no LLM), then reading the pipeline's own perf trace. The
chunked wall time ≈ STT wall time on this provider, so RTF here is an honest
STT-throughput measurement across model × device × workers × batch × chunk.

Per config it also records: VRAM/GPU/CPU peaks (MetricSampler) and transcript
quality metrics (measure.transcript), so the long-chunk benchmark can answer
"is it faster *and* still correct?" — not just "is it faster?".

Run from repo root:

    py worker/tests/integration/bench_stt.py [--duration 300] [--model small]
        [--chunk-durations 30,60,120] [--configs "auto@batched@w2@b2"]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import analyze_trace  # noqa: E402
import e2e_pipeline  # noqa: E402
import measure_quality  # noqa: E402

WORKER_DIR = Path(__file__).resolve().parents[2]

# Vietnamese reference lines (same content as SPEECH_LINES, localized).
VI_LINES = (
    "Xin chào và chào mừng đến với kênh của chúng tôi.",
    "Hôm nay chúng tôi thử nghiệm quy trình bản địa hóa video.",
    "Nhân viên trích xuất âm thanh, phiên dịch và kết xuất phụ đề.",
    "Câu này xác minh phụ đề được ghi vào video cuối cùng.",
    "Cảm ơn bạn đã xem và hẹn gặp lại trong video tiếp theo.",
)


def synthesize_lang(out_wav: Path, language: str) -> tuple[str, float]:
    text = " ".join(
        VI_LINES if language == "vi" else e2e_pipeline.SPEECH_LINES
    )
    voice = "vi-VN-HoaiMyNeural" if language == "vi" else "en-US-AriaNeural"
    try:
        import edge_tts  # type: ignore

        mp3 = out_wav.with_suffix(".mp3")

        async def _synth() -> None:
            await edge_tts.Communicate(text, voice).save(str(mp3))

        asyncio.run(_synth())
        ffmpeg = e2e_pipeline.find_ffmpeg()
        proc = e2e_pipeline.run(
            [
                str(ffmpeg), "-y", "-nostdin", "-i", str(mp3),
                "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out_wav),
            ]
        )
        mp3.unlink(missing_ok=True)
        if proc.returncode == 0 and out_wav.is_file() and out_wav.stat().st_size > 0:
            return "edge-" + language, e2e_pipeline._wav_duration(out_wav)
    except Exception:  # noqa: BLE001 - optional network
        pass
    return e2e_pipeline.synthesize_speech(out_wav)


def run_config(
    port: int,
    video: Path,
    audio: Path,
    chunk_duration: float,
    *,
    model: str,
    device: str,
    workers: int,
    batch: int,
    mode: str,
    project_dir: Path,
    total_duration: float,
) -> dict:
    body = {
        "job_id": "bench-stt",
        "project_id": "bench-stt",
        "project_dir": str(project_dir),
        "source_video": str(video),
        "source_audio": str(audio),
        "target_language": "vi",
        "source_language": "en",
        "provider": "mock",
        "model": "mock",
        "provider_config": None,
        "glossary_ver": "0",
        "stt_model": model,
        "stt_device": device,
        "stt_mode": mode,
        "stt_batch_size": batch,
        "chunk_duration": chunk_duration,
        "overlap": 2.0,
        "max_concurrency": max(workers, 1),
        "stt_workers": workers,
        "translate_workers": workers,
        "tts_workers": workers,
        "max_retries": 0,
        "duration_tolerance": 0.5,
    }
    t0 = time.monotonic()
    manifest = e2e_pipeline.http(port, "POST", "/v1/automation/chunked", body)
    wall = time.monotonic() - t0
    perf = manifest.get("perf") or {}
    stt = (perf.get("stages") or {}).get("stt", {})
    trace_path = manifest.get("trace_path")
    quality = {}
    transcript_path = None
    if trace_path:
        cache_dir = Path(trace_path).parent
        transcript_path = cache_dir / "transcript.json"
        if transcript_path.is_file():
            quality = measure_quality.transcript_metrics(
                str(transcript_path), chunk_duration, overlap=2.0, total_duration=total_duration
            )
    return {
        "wall_s": round(wall, 2),
        "rtf_wall": round(wall / total_duration, 3),
        "stt_total_ms": stt.get("total_ms"),
        "stt_peak_active": stt.get("peak_active"),
        "stt_avg_active": stt.get("avg_active"),
        "completed_chunks": manifest.get("completed_chunks"),
        "total_chunks": manifest.get("total_chunks"),
        "quality": quality,
        "transcript_path": str(transcript_path) if transcript_path else None,
        "perf_path": manifest.get("trace_path"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, default=300.0)
    parser.add_argument("--model", default="small")
    parser.add_argument("--chunk-durations", default="30,60,120,180",
                        help="comma list; one bench pass per chunk duration")
    parser.add_argument("--chunk-duration", type=float, default=None,
                        help="single chunk duration override (kept for back-compat)")
    parser.add_argument("--lang", default="en", choices=("en", "vi"),
                        help="fixture language (en = existing English lines, vi = Vietnamese)")
    parser.add_argument("--port", type=int, default=8802)
    parser.add_argument(
        "--configs",
        default=("auto@batched@w2@b2;auto@batched@w2@b4"),
        help="semicolon list of device@mode@workers@batch (no spaces; device: cpu|auto)",
    )
    args = parser.parse_args()

    if args.chunk_duration is not None:
        chunk_durations = [args.chunk_duration]
    else:
        chunk_durations = [float(c.strip()) for c in args.chunk_durations.split(",") if c.strip()]

    workdir = Path(tempfile.mkdtemp(prefix="tc_bench_stt_"))
    out_dir = workdir / "out"
    out_dir.mkdir(exist_ok=True)

    print(f"[1/3] build speech ({args.lang}) + {args.duration}s fixture", flush=True)
    speech = workdir / "speech.wav"
    engine, _ = synthesize_lang(speech, args.lang)
    video = e2e_pipeline.build_fixture(workdir, speech, args.duration)
    audio = out_dir / "audio.wav"
    print(f"[2/3] start worker", flush=True)
    worker = e2e_pipeline.start_worker(args.port, workdir / "worker.log")
    query = {
        "video_path": str(video),
        "output_path": str(audio),
        "job_id": "bench-stt-extract",
    }
    e2e_pipeline.http(args.port, "POST", "/v1/audio/extract", query)
    audio_path = audio
    results = []
    try:
        for cdu in chunk_durations:
            for spec in [c for c in args.configs.split(";") if c]:
                dev, mode, w_s, b_s = spec.split("@")
                w, b = int(w_s.lstrip("w")), int(b_s.lstrip("b"))
                label = f"{dev} {mode} w{w} b{b} c{cdu:g}"
                print(f"[3/3] config {label} ...", flush=True)
                proj_tag = f"{dev}_{mode}_w{w}_b{b}_c{cdu:g}"
                project_dir = workdir / f"proj_{proj_tag}"
                project_dir.mkdir(exist_ok=True)
                sampler = e2e_pipeline.MetricSampler(worker.pid, interval=2.0).start()
                try:
                    row = run_config(
                        args.port,
                        video,
                        audio_path,
                        cdu,
                        model=args.model,
                        device=dev,
                        workers=w,
                        batch=b,
                        mode=mode,
                        project_dir=project_dir,
                        total_duration=args.duration,
                    )
                finally:
                    row["sampler"] = sampler.close()
                row["label"] = label
                row["config"] = spec
                row["chunk_duration"] = cdu
                results.append(row)
                print(
                    f"    wall={row['wall_s']}s rtf={row['rtf_wall']} "
                    f"chunks={row['completed_chunks']}/{row['total_chunks']} "
                    f"segs={row['quality'].get('segment_count')} "
                    f"coverage={row['quality'].get('timeline_coverage')} "
                    f"vram={row['sampler'].get('vram_peak_mb')} "
                    f"gpu={row['sampler'].get('gpu_peak_percent')}",
                    flush=True,
                )
    finally:
        worker.terminate()
        try:
            worker.wait(timeout=10)
        except Exception:
            worker.kill()

    print("\n===== STT BENCH SUMMARY =====")
    print(json.dumps(results, indent=2, ensure_ascii=False))
    report_path = workdir / "bench_report.json"
    report_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nBENCH_REPORT={report_path}")
    print(f"EVIDENCE_DIR={workdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())