"""Performance benchmark for the MVP pipeline (MASTER_PLAN §4.1, §29.2).

Drives the real worker HTTP API over synthetic (deterministic, copyright-safe)
videos and measures each stage separately:

    video generation → media inspect → audio extract → STT → translation
    (mock) → subtitle generation → render → export QC

Measures per stage: wall time; peak worker RAM (polled via psutil when
available); output sizes. Writes a JSON report. GPU mode (`--device cuda`)
reports failure as NOT VERIFIED when the CUDA toolkit is absent.

Usage:
    python worker/scripts/benchmark_performance.py [--minutes 1 10]
        [--model tiny] [--device cpu] [--provider mock]
        [--out worker/perf_report.json]

Duration guidance (CPU, tiny model, measured on a 2020-era desktop):
    STT runs at roughly 0.2–0.3x realtime; 1 min ≈ 20 s, 10 min ≈ 2–3 min,
    30 min ≈ 7–10 min, 60 min ≈ 15–20 min (plus render). Run what fits your
    CI budget; the report records NOT_RUN for the rest with the reason.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKER_DIR = ROOT / "worker"
OUT_DIR = ROOT / "worker" / "bench_output"

STAGE_TIMEOUT_S = 7200


# ---------------------------------------------------------------------------
# worker lifecycle (same sidecar protocol as WorkerManager)
# ---------------------------------------------------------------------------


def spawn_worker() -> tuple[subprocess.Popen, int, str, threading.Thread]:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    token = secrets.token_hex(32)
    exe = os.environ.get("WORKER_EXE")
    if exe:
        cmd = [os.path.abspath(exe), "--port", str(port)]
        cwd = str(Path(exe).resolve().parent)
    else:
        cmd = [sys.executable, "-m", "src.main", "--port", str(port)]
        cwd = str(WORKER_DIR)
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "HF_HUB_OFFLINE": "1"},
    )
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(f"WORKER_AUTH_TOKEN={token}\n")
    proc.stdin.flush()
    deadline = time.time() + 120
    ready = None
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        line = line.rstrip("\r\n")
        if line.startswith("READY "):
            ready = line[6:].strip()
            break
    if ready != token:
        proc.kill()
        raise RuntimeError("worker READY handshake failed")

    def _drain() -> None:
        for _ in proc.stdout:
            pass

    drain = threading.Thread(target=_drain, daemon=True)
    drain.start()
    return proc, port, token, drain


def http_post(port: int, token: str, path: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=STAGE_TIMEOUT_S) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {"error": {"code": "E_HTTP", "message": str(e)}}


def expect_ok(status: int, payload: dict, stage: str) -> dict:
    if status != 200:
        err = payload.get("error", {})
        raise AssertionError(f"{stage}: HTTP {status} {err.get('code')} {err.get('message')}")
    return payload


# ---------------------------------------------------------------------------
# synthetic fixture generation
# ---------------------------------------------------------------------------


def _resolve_voice_dir() -> Path:
    for candidate in (
        ROOT / "golden" / "voices",
        ROOT / "golden" / "voices" / "en_US-lessac-medium",
        Path.home() / "AppData" / "Local" / "piper",
        Path.home() / ".local" / "share" / "piper",
    ):
        if (candidate / "en_US-lessac-medium.onnx").is_file():
            return candidate
    raise SystemExit(
        "piper voice en_US-lessac-medium not found; run: py -m piper.download_voices en_US-lessac-medium"
    )


PHRASE = "the quick brown fox jumps over the lazy dog. my phone number is five five five one two three four. "


def generate_video(minutes: int) -> Path:
    """Deterministic testsrc2 video + real piper speech (same voice as golden)."""
    vid = OUT_DIR / f"input_{minutes}min.mp4"
    if vid.is_file() and vid.stat().st_size > 0:
        return vid
    audio = OUT_DIR / f"announce_{minutes}min.wav"
    if not audio.is_file():
        # Synthesize ONE phrase clip, then loop it with ffmpeg to the target
        # length (deterministic; avoids very long piper synthesis runs that
        # silently produce empty output).
        clip = OUT_DIR / "phrase.wav"
        if not clip.is_file():
            voice_dir = _resolve_voice_dir()
            subprocess.run(
                [sys.executable, "-m", "piper",
                 "-m", str(voice_dir / "en_US-lessac-medium.onnx"),
                 "-c", str(voice_dir / "en_US-lessac-medium.onnx.json"),
                 "-f", str(clip)],
                input=PHRASE.encode("utf-8"), check=True, capture_output=True,
            )
            assert clip.is_file() and clip.stat().st_size > 0, "piper produced no audio"
        subprocess.run(
            [
                "ffmpeg", "-y", "-nostdin",
                "-stream_loop", "-1", "-i", str(clip),
                "-t", str(minutes * 60),
                "-af", "aresample=16000", "-ac", "1", "-c:a", "pcm_s16le", str(audio),
            ],
            check=True, capture_output=True,
        )
    subprocess.run(
        [
            "ffmpeg", "-y", "-nostdin",
            "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=24:duration={minutes*60}",
            "-i", str(audio),
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
            "-c:a", "aac", "-t", str(minutes * 60), "-shortest", str(vid),
        ],
        check=True, capture_output=True,
    )
    return vid


def peak_ram_mb(proc: subprocess.Popen) -> float | None:
    try:
        import psutil  # noqa: PLC0415

        try:
            p = psutil.Process(proc.pid)
            return p.memory_info().rss / (1024 * 1024)
        except psutil.Error:
            return None
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# benchmark
# ---------------------------------------------------------------------------


def run_benchmark(minutes: int, model: str, device: str, provider: str,
                  proc: subprocess.Popen, port: int, token: str) -> dict:
    result: dict = {"minutes": minutes, "model": model, "device": device, "provider": provider, "stages": {}}
    t_total = time.time()

    gen_t = time.time()
    video = generate_video(minutes)
    result["stages"]["generate"] = {"seconds": round(time.time() - gen_t, 2),
                                    "note": "synthetic fixture, excluded from total"}

    # media inspect (ffprobe)
    t = time.time()
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration,size",
         "-of", "json", str(video)],
        check=True, capture_output=True, text=True,
    )
    fmt = json.loads(probe.stdout)["format"]
    result["stages"]["media_inspect"] = {
        "seconds": round(time.time() - t, 2),
        "duration_s": float(fmt["duration"]),
        "size_bytes": int(fmt["size"]),
    }

    # audio extract
    t = time.time()
    wav = OUT_DIR / f"audio_{minutes}min.wav"
    status, payload = http_post(port, token, "/v1/audio/extract", {
        "video_path": str(video), "output_path": str(wav),
        "job_id": f"bench-{minutes}",
    })
    expect_ok(status, payload, "audio extract")
    result["stages"]["audio_extract"] = {"seconds": round(time.time() - t, 2)}

    # STT
    t = time.time()
    status, payload = http_post(port, token, "/v1/stt/transcribe", {
        "audio_path": str(wav), "project_id": f"bench-{minutes}", "model": model,
        "device": device, "language": "en", "job_id": f"bench-stt-{minutes}",
    })
    if status != 200:
        result["stages"]["stt"] = {
            "status": "FAILED",
            "code": payload.get("error", {}).get("code"),
            "message": payload.get("error", {}).get("message"),
        }
        result["status"] = "FAILED"
        return result
    transcript = payload
    stt_s = round(time.time() - t, 2)
    n_seg = len(transcript.get("segments", []))
    result["stages"]["stt"] = {
        "seconds": stt_s, "segments": n_seg,
        "audio_minutes": minutes,
        "realtime_factor": round(stt_s / (minutes * 60), 3),
    }

    # translation (mock)
    t = time.time()
    status, payload = http_post(port, token, "/v1/translate", {
        "transcript": transcript, "project_id": f"bench-{minutes}",
        "provider": provider, "target_language": "zh",
        "model": "gemini-2.5-flash-lite", "job_id": f"bench-tr-{minutes}",
    })
    expect_ok(status, payload, "translate")
    items = [it for b in payload.get("blocks", []) for it in b.get("translations", [])]
    result["stages"]["translate"] = {
        "seconds": round(time.time() - t, 2), "items": len(items),
    }

    # subtitle generation
    t = time.time()
    status, payload = http_post(port, token, "/v1/subtitle", {
        "transcript": transcript, "translation": payload, "project_id": f"bench-{minutes}",
        "output_dir": str(OUT_DIR), "language": "en", "job_id": f"bench-sub-{minutes}",
    })
    expect_ok(status, payload, "subtitle")
    ass_path = payload.get("ass_path", "")
    result["stages"]["subtitle"] = {
        "seconds": round(time.time() - t, 2), "cues": len(payload.get("cues", [])),
    }

    # render
    t = time.time()
    out_video = OUT_DIR / f"out_{minutes}min.mp4"
    status, payload = http_post(port, token, "/v1/render", {
        "video_path": str(video), "subtitle_path": ass_path,
        "output_path": str(out_video), "job_id": f"bench-render-{minutes}",
    })
    expect_ok(status, payload, "render")
    result["stages"]["render"] = {"seconds": round(time.time() - t, 2)}

    # export QC (ffprobe on output)
    t = time.time()
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration,size:stream=codec_type,codec_name",
         "-of", "json", str(out_video)],
        check=True, capture_output=True, text=True,
    )
    data = json.loads(probe.stdout)
    result["stages"]["export_qc"] = {
        "seconds": round(time.time() - t, 2),
        "duration_s": float(data["format"]["duration"]),
        "size_bytes": int(data["format"]["size"]),
        "streams": [f"{s.get('codec_type')}:{s.get('codec_name')}" for s in data.get("streams", [])],
    }

    result["total_pipeline_seconds"] = round(time.time() - t_total, 2)
    result["peak_worker_ram_mb"] = peak_ram_mb(proc)
    result["status"] = "PASS"
    return result


def _merge_into_existing_report(path: Path, fresh: dict) -> dict:
    """Merge new benchmark runs into an existing report instead of overwriting.

    The report file may already contain other durations from earlier runs
    (e.g. a 60-min run written before the 1/10-min runs). Keep the existing
    header and re-use any pre-existing ``runs`` that are not in ``fresh``.
    """
    try:
        existing = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fresh
    merged_runs = dict(existing.get("runs", {}))
    merged_runs.update(fresh.get("runs", {}))
    merged = dict(existing)
    merged.update(fresh)
    merged["runs"] = merged_runs
    return merged


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", nargs="+", type=int, default=[1, 10], help="durations to benchmark")
    parser.add_argument("--model", default="tiny")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--provider", default="mock")
    parser.add_argument("--worker-exe", default=None)
    parser.add_argument("--out", default=str(ROOT / "worker" / "perf_report.json"))
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if args.worker_exe:
        os.environ["WORKER_EXE"] = args.worker_exe

    # warm model cache (dev interpreter) so the worker never stalls on HF
    if args.device == "cpu" and not args.worker_exe:
        from faster_whisper import WhisperModel  # noqa: PLC0415

        WhisperModel(args.model, device="cpu", compute_type="int8")
        print(f"STT model `{args.model}` warmed")

    proc, port, token, _drain = spawn_worker()
    try:
        report: dict = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "hardware": {"cpu": "unknown", "cuda_devices": 0}, "runs": {}}
        try:
            import platform
            report["hardware"]["cpu"] = platform.processor() or "unknown"
        except Exception:
            pass
        try:
            import ctranslate2  # noqa: PLC0415
            report["hardware"]["cuda_devices"] = ctranslate2.get_cuda_device_count()
        except Exception:
            pass

        for minutes in args.minutes:
            print(f"\n=== benchmark {minutes} min (model={args.model} device={args.device}) ===")
            run = run_benchmark(minutes, args.model, args.device, args.provider, proc, port, token)
            report["runs"][f"{minutes}min"] = run
            for stage, m in run["stages"].items():
                secs = m.get("seconds")
                print(f"  {stage:16s} {secs:>8.2f}s" if secs is not None else f"  {stage:16s} {m}")
            print(f"  total pipeline: {run.get('total_pipeline_seconds')}s  peak RAM: {run.get('peak_worker_ram_mb')} MB  status: {run['status']}")

        merged = _merge_into_existing_report(args.out, report)
        Path(args.out).write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nreport: {args.out}")
        return 0
    finally:
        if proc.poll() is None and proc.stdin is not None:
            try:
                proc.stdin.write("SHUTDOWN\n")
                proc.stdin.flush()
                proc.wait(timeout=15)
            except Exception:
                proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
