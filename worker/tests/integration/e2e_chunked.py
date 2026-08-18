"""E2E for the CHUNKED Automation pipeline (acceptance driver).

Drives layer-by-layer exactly like the production path (worker over loopback
HTTP, parallel chunk processing -> ordered assembly -> timeline validation ->
FFmpeg final render -> finalize):

    extract audio -> POST /v1/automation/chunked (multi-chunk parallel STT +
    translation + optional TTS/voice-track assembly) -> render (burn subtitle
    + voice-track mix) -> finalize.

Evidence collected on every run (all numbers measured, never fabricated):

1. **Segment identity / timeline (FIX #1)** — transcript.id and the
   translation items' segment_id must be the same list, same order, 1:1.
2. **Chunk health** — total/completed/failed chunks + retry counts.
3. **Final MP4** — ffprobe: h264(+aac), resolution/fps/duration preserved,
   PLUS deep QC: video-not-black (signalstats YAVG) and audio-has-sound
   (volumedetect max_volume). A valid run must also *open* and not be a
   silent/black file, not merely exist.
4. Optional `--metrics`: CPU/RAM/GPU/VRAM peaks sampled while pipeline runs
   (driver-side MeasurementSampler via psutil + nvidia-smi).

Run (from repo root):

    py worker/tests/integration/e2e_chunked.py --duration 60 --dub --metrics
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import e2e_pipeline  # noqa: E402  (sibling runner: fixture + worker helpers)


def load_json(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def check_segment_identity(artifacts: dict) -> dict:
    transcript = load_json(artifacts["transcript"])
    translation = load_json(artifacts["translation"])

    t_ids = [seg["id"] for seg in transcript["segments"]]
    tr_ids = [
        item["segment_id"]
        for block in translation["blocks"]
        for item in block["translations"]
    ]
    checks = {
        "transcript_segments": len(t_ids),
        "translation_items": len(tr_ids),
        "same_count": len(t_ids) == len(tr_ids),
        "same_order": t_ids == tr_ids,
        "sequential_seg0": t_ids == [f"seg_{i}" for i in range(len(t_ids))],
        "no_duplicate_ids": len(set(tr_ids)) == len(tr_ids),
    }
    checks["pass"] = all(
        checks[k]
        for k in ("same_count", "same_order", "sequential_seg0", "no_duplicate_ids")
    )
    mismatches = [(a, b) for a, b in zip(t_ids, tr_ids) if a != b]
    if mismatches:
        checks["first_mismatch"] = mismatches[:5]
    return checks


def chunk_stats(manifest: dict) -> dict:
    chunks = manifest.get("chunks", [])
    retries = [c.get("retries", 0) for c in chunks]
    return {
        "total_chunks": manifest.get("total_chunks"),
        "completed_chunks": manifest.get("completed_chunks"),
        "failed_chunks": manifest.get("failed_chunks"),
        "any_retries": bool(any(r > 0 for r in retries)),
        "retry_count_sum": sum(retries),
        "max_retries_any_chunk": max(retries, default=0),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, default=300.0, help="target fixture seconds")
    parser.add_argument("--stt-model", default="small")
    parser.add_argument("--port", type=int, default=8801)
    parser.add_argument("--dub", action="store_true", help="enable TTS + voice-track assembly")
    parser.add_argument("--voice", default="vi-VN-HoaiMyNeural")
    parser.add_argument("--tts-engine", default="edge")
    parser.add_argument("--metrics", action="store_true", help="sample CPU/RAM/GPU/VRAM peaks")
    parser.add_argument(
        "--provider",
        default="mock",
        help="translation provider for the chunked automation path (the whole "
        "chunked pipeline runs STT + translation + TTS through this provider). "
        "Default mock (offline). Use 'local'/'free' to exercise the real "
        "OpenAI-compatible path — a stub server is started automatically.",
    )
    args = parser.parse_args()

    ffprobe = e2e_pipeline.find_ffprobe()
    workdir = Path(tempfile.mkdtemp(prefix="tc_e2e_chunk_"))
    out_dir = workdir / "out"
    out_dir.mkdir(exist_ok=True)

    provider_config: dict | None = None
    stub = None
    if args.provider in ("local", "free"):
        from e2e_providers import (  # noqa: PLC0415 - sibling stub server
            OpenAICompatStub,
        )
        from http.server import ThreadingHTTPServer
        import threading as _threading

        stub = ThreadingHTTPServer(("127.0.0.1", 0), OpenAICompatStub)
        provider_config = {"server_url": f"http://127.0.0.1:{stub.server_port}"}
        _threading.Thread(target=stub.serve_forever, daemon=True).start()

    report: dict = {
        "fixture_duration_s": args.duration,
        "stt_model": args.stt_model,
        "dub": args.dub,
        "stages": {},
        "identity": None,
        "output": None,
    }
    t_start = time.monotonic()

    # 1) Speech + fixture
    t0 = time.monotonic()
    speech = workdir / "speech.wav"
    engine, _ = e2e_pipeline.synthesize_speech(speech)
    report["speech_engine"] = engine
    video = e2e_pipeline.build_fixture(workdir, speech, args.duration)
    report["stages"]["fixture_build"] = {"seconds": round(time.monotonic() - t0, 2)}

    # 2) Worker
    t0 = time.monotonic()
    worker = e2e_pipeline.start_worker(args.port, workdir / "worker.log")
    report["stages"]["worker_start"] = {"seconds": round(time.monotonic() - t0, 2)}
    sampler = (
        e2e_pipeline.MetricSampler(worker.pid, interval=2.0).start()
        if args.metrics
        else None
    )
    try:
        # 3) Extract audio (the chunked pipeline consumes the wav, like Rust)
        audio = out_dir / "audio.wav"
        t0 = time.monotonic()
        extract = e2e_pipeline.http(args.port, "POST", "/v1/audio/extract", {
            "video_path": str(video),
            "output_path": str(audio),
            "job_id": "e2e-chunk-extract",
        })
        report["stages"]["extract_audio"] = {"seconds": round(time.monotonic() - t0, 2)}

        # 4) The chunked pipeline itself (STT + translation + optional TTS)
        project_dir = workdir / "project"
        project_dir.mkdir(exist_ok=True)
        t0 = time.monotonic()
        body = {
            "job_id": "e2e-chunked",
            "project_id": "e2e-chunked",
            "project_dir": str(project_dir),
            "source_video": str(video),
            "source_audio": str(audio),
            "target_language": "vi",
            "source_language": "en",
            "provider": args.provider,
            "model": args.provider,
            "provider_config": provider_config,
            "glossary_ver": "0",
            "stt_model": args.stt_model,
            "stt_device": "cpu",
            "chunk_duration": 30.0,
            "overlap": 2.0,
            "max_concurrency": 4,
            "max_retries": 2,
            "duration_tolerance": 0.5,
        }
        if args.dub:
            body.update({"dub": True, "voice": args.voice, "tts_engine": args.tts_engine})
        manifest = e2e_pipeline.http(args.port, "POST", "/v1/automation/chunked", body)
        report["stages"]["chunked"] = {
            "seconds": round(time.monotonic() - t0, 2),
            **chunk_stats(manifest),
        }

        # 5) FIX #1 identity invariant: transcript ids == translation ids
        identity = check_segment_identity(manifest["artifacts"])
        report["identity"] = identity
        if not identity["pass"]:
            print(json.dumps(report, indent=2, ensure_ascii=False))
            print("\nIDENTITY CHECK: FAIL", identity.get("first_mismatch"))
            return 2

        # 6) Render the final MP4 from the merged subtitle artifact (burn-in
        #    + optional voice-track replacement = audio assembly)
        output = out_dir / "rendered.mp4"
        t0 = time.monotonic()
        render_req = {
            "video_path": str(video),
            "subtitle_path": manifest["artifacts"]["subtitle_ass"],
            "output_path": str(output),
            "preset": "veryfast",
            "job_id": "e2e-chunk-render",
        }
        voice_track = manifest["artifacts"].get("voice_track")
        if voice_track:
            render_req["voice_track_path"] = voice_track
        render = e2e_pipeline.http(args.port, "POST", "/v1/render", render_req)
        report["stages"]["render"] = {
            "seconds": round(time.monotonic() - t0, 2),
            "encoder_used": render.get("encoder_used"),
            "voice_track_used": bool(voice_track),
        }

        # 7) ffprobe validation of the final MP4
        t0 = time.monotonic()
        validation = e2e_pipeline.validate_output(ffprobe, output, args.duration)
        validation["probe_seconds"] = round(time.monotonic() - t0, 2)
        report["output"] = validation

        # 8) Deep QC on the final file (not just log lines)
        luma = e2e_pipeline.average_luma(e2e_pipeline.find_ffmpeg(), output)
        loud = e2e_pipeline.audio_loudness(e2e_pipeline.find_ffmpeg(), output)
        report["qc"] = {"video_not_black": luma, "audio_has_sound": loud}

        if sampler is not None:
            report["metrics_peaks"] = sampler.close()

        # 9) Finalize (final validation + cleanup of the temp tree)
        finalize = e2e_pipeline.http(args.port, "POST", "/v1/automation/finalize", {
            "job_id": "e2e-chunked",
            "project_dir": str(project_dir),
            "output_path": str(output),
            "source_duration": args.duration,
            "duration_tolerance": 0.5,
        })
        report["finalize"] = finalize

        report["total_seconds"] = round(time.monotonic() - t_start, 2)
        report["workdir"] = str(workdir)
        report["final_mp4"] = str(output)

        print(json.dumps(report, indent=2, ensure_ascii=False))
        print(f"\nEVIDENCE_DIR={workdir}")
        print(f"FINAL_MP4={output}")
        problems = []
        if not identity["pass"]:
            problems.append("identity")
        for name, qc in (("video_not_black", luma), ("audio_has_sound", loud)):
            if not qc.get("ok"):
                problems.append(f"{name}: {qc.get('reason') or qc}")
        if validation.get("issues"):
            problems.append(validation["issues"])
        if problems:
            print("VALIDATION ISSUES:", "; ".join(map(str, problems)))
            return 3
        cs = report["stages"]["chunked"]
        print(
            f"VALIDATION: PASS\tchunks={cs['completed_chunks']}/{cs['total_chunks']}"
            f"\tsegments={identity['transcript_segments']}"
            f"\tretries={cs['retry_count_sum']}"
            f"\tluma={luma.get('mean_luma')}\tmax_db={loud.get('max_volume_db')}"
        )
        return 0
    finally:
        worker.terminate()
        try:
            worker.wait(timeout=10)
        except Exception:
            worker.kill()
        if stub is not None:
            stub.shutdown()


if __name__ == "__main__":
    sys.exit(main())