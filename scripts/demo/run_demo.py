#!/usr/bin/env python3
"""Run ONE real video through the REAL automation pipeline.

Drives the same worker HTTP endpoints the desktop app uses (spawns the worker
sidecar exactly like the Rust WorkerManager does: random loopback port +
per-session bearer token over stdin, `READY <token>` handshake, `SHUTDOWN` on
stdin to stop). No mocks, no fake progress, no fake success: every stage either
completes and writes a real artifact or the run fails honestly.

    python scripts/demo/run_demo.py --input VIDEO.mp4 --target-language zh

Output layout (default `output/`):

    output/
      original/      copy of the input video
      transcript/    transcript.json      (STT)
      translation/   translation.json     (translation)
      subtitles/     subtitle.srt/.ass    (subtitle stage)
      audio/         audio.wav            (extraction)
      final/         rendered.mp4         (burned subtitles + watermark)
      demo_result.json                    (machine-readable summary)

The pipeline that exists today produces a translated + subtitled video.
TTS/dubbing and audio mixing are NOT implemented - demo_result.json records
them as "not_implemented" rather than pretending.

Options:
    --provider mock|gemini|local   translation provider (default mock = offline)
    --model large-v3|turbo|...     faster-whisper model (default large-v3)
    --device auto|cuda|cpu         STT device (default auto)
    --python PATH                  interpreter for the worker (default py/python)
    --output DIR                   output directory (default ./output)
    --worker-dir DIR               worker package dir (default repo/worker)
    --timeout SECONDS              per-stage timeout (default 3600)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORKER_DIR = REPO_ROOT / "worker"
DEFAULT_OUTPUT = REPO_ROOT / "output"

HEADERS_JSON = {"Content-Type": "application/json"}


def pick_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def find_python() -> str:
    for name in ("py", "python", "python3"):
        if shutil.which(name):
            return name
    return ""


class WorkerSidecar:
    """Spawn the worker exactly like the Rust core does (token over stdin)."""

    def __init__(self, python: str, worker_dir: Path, port: int, timeout: int):
        self.port = port
        self.token = os.urandom(32).hex()
        self.process = subprocess.Popen(
            [python, "-m", "src.main", "--port", str(port)],
            cwd=str(worker_dir),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.timeout = timeout
        self.log_lines: list[str] = []

    def wait_ready(self, timeout: float = 60.0) -> bool:
        assert self.process.stdout is not None
        # Sidecar protocol: the parent must send the session token over stdin
        # BEFORE the worker binds; the worker blocks reading it, then echoes
        # `READY <token>` once the HTTP server is up (see worker/src/main.py
        # `_read_stdin_token`). Without this write the worker never starts.
        if self.process.stdin is not None:
            try:
                self.process.stdin.write(self.token + "\n")
                self.process.stdin.flush()
            except OSError:
                pass
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = self.process.stdout.readline()
            if not line:
                break
            self.log_lines.append(line.rstrip())
            if line.startswith("READY "):
                return line.split()[1] == self.token
        return False

    def post(self, path: str, payload: dict) -> dict:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=body,
            headers={**HEADERS_JSON, "Authorization": f"Bearer {self.token}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {"error": {"code": f"HTTP_{exc.code}", "message": raw[:300], "recoverable": False}}

    def stop(self) -> None:
        if self.process.poll() is None and self.process.stdin is not None:
            try:
                self.process.stdin.write("SHUTDOWN\n")
                self.process.stdin.flush()
            except OSError:
                pass
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait()
        remaining = self.process.stdout.read() if self.process.stdout else ""
        self.log_lines.extend(remaining.splitlines())


def stage_failed(result: dict) -> str | None:
    """Return the human error message when the worker envelope reports failure."""
    if "error" in result:
        error = result["error"]
        return f"{error.get('code', 'E_UNKNOWN')}: {error.get('message', 'no message')}"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one real video through the real automation pipeline.")
    parser.add_argument("--input", required=True, type=Path, help="Source video file")
    parser.add_argument("--target-language", default="zh", help="Target language (ISO-2, default zh)")
    parser.add_argument("--provider", default="mock", choices=["mock", "gemini", "local"],
                        help="Translation provider (default mock = offline pseudo-translation)")
    parser.add_argument("--model", default="large-v3",
                        help="faster-whisper model tier (large-v3|turbo|small|base|tiny)")
    parser.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    parser.add_argument("--python", default=None, help="Python interpreter for the worker")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output directory")
    parser.add_argument("--worker-dir", type=Path, default=DEFAULT_WORKER_DIR, help="Worker package dir")
    parser.add_argument("--timeout", type=int, default=3600, help="Per-stage timeout in seconds")
    args = parser.parse_args()

    # Resolve to absolute paths: the worker process runs with CWD=worker/, so
    # a relative path (e.g. `output/...`) would be resolved against the worker
    # directory and fail with "Video file does not exist.".
    input_video = args.input.resolve()
    if not input_video.is_file():
        print(f"[error] input video not found: {input_video}")
        return 2

    output = args.output.resolve()
    dirs = {
        "original": output / "original",
        "transcript": output / "transcript",
        "translation": output / "translation",
        "subtitles": output / "subtitles",
        "audio": output / "audio",
        "final": output / "final",
    }
    for directory in dirs.values():
        directory.mkdir(parents=True, exist_ok=True)

    python = args.python or find_python()
    if not python:
        print("[error] no Python interpreter found (py/python/python3)")
        return 2

    errors: list[str] = []
    summary = {
        "status": "failed",
        "input": str(input_video),
        "input_duration": None,
        "target_language": args.target_language,
        "provider": args.provider,
        "stt": None,
        "translation": None,
        "tts": "not_implemented",
        "subtitle": None,
        "audio_mix": "not_implemented",
        "final_video": None,
        "elapsed_seconds": 0,
        "errors": errors,
    }
    started = time.monotonic()

    def finish(code: int) -> int:
        summary["elapsed_seconds"] = round(time.monotonic() - started, 2)
        (output / "demo_result.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return code

    # Copy the input into output/original so the demo dir is self-contained.
    original_copy = dirs["original"] / input_video.name
    shutil.copy2(input_video, original_copy)
    print(f"[info] input copied to {original_copy}")

    # ---- spawn the worker (real sidecar protocol) -------------------------
    worker = WorkerSidecar(python, args.worker_dir, pick_port(), args.timeout)
    print(f"[info] starting worker on 127.0.0.1:{worker.port} ...")
    if not worker.wait_ready(timeout=120):
        errors.append("worker failed to start (READY handshake)")
        print("[error] worker did not become ready (see log below)")
        print("\n".join(worker.log_lines[-20:]))
        worker.stop()
        return finish(1)

    def run_stage(name: str, path: str, payload: dict, save_to: Path | None) -> dict | None:
        stage_started = time.monotonic()
        print(f"[stage] {name} ...", flush=True)
        result = worker.post(path, payload)
        failure = stage_failed(result)
        elapsed = time.monotonic() - stage_started
        if failure:
            print(f"[error] {name}: {failure}")
            errors.append(f"{name}: {failure}")
            return None
        if save_to is not None:
            save_to.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[ok] {name} ({elapsed:.1f}s)")
        return result

    try:
        # 1. Audio extract
        audio_path = dirs["audio"] / "audio.wav"
        extract = run_stage(
            "extract audio", "/v1/audio/extract",
            {"video_path": str(original_copy), "output_path": str(audio_path)},
            save_to=None,
        )
        if extract is None:
            worker.stop()
            return finish(1)
        summary["input_duration"] = extract.get("duration_seconds")
        summary["stt"] = str(dirs["transcript"] / "transcript.json")

        # 2. STT
        transcript = run_stage(
            "transcribe (STT)", "/v1/stt/transcribe",
            {"audio_path": str(audio_path), "project_id": "demo",
             "model": args.model, "device": args.device,
             "language": None, "total_duration_seconds": summary["input_duration"]},
            save_to=dirs["transcript"] / "transcript.json",
        )
        if transcript is None:
            worker.stop()
            return finish(1)
        summary["translation"] = str(dirs["translation"] / "translation.json")

        # 3. Translate
        api_key = os.environ.get("GEMINI_API_KEY") if args.provider == "gemini" else None
        translation = run_stage(
            "translate", "/v1/translate",
            {"transcript": transcript, "project_id": "demo",
             "provider": args.provider, "target_language": args.target_language,
             "model": args.model, "glossary_ver": "0",
             "api_key": api_key},
            save_to=dirs["translation"] / "translation.json",
        )
        if translation is None:
            worker.stop()
            return finish(1)
        summary["subtitle"] = str(dirs["subtitles"] / "subtitle.ass")

        # 4. Subtitle generation (worker writes .srt/.ass into subtitles/)
        subtitle = run_stage(
            "generate subtitles", "/v1/subtitle",
            {"transcript": transcript, "translation": translation,
             "project_id": "demo", "output_dir": str(dirs["subtitles"]),
             "language": args.target_language},
            save_to=None,
        )
        if subtitle is None:
            worker.stop()
            return finish(1)
        ass_path = subtitle.get("ass_path") or ""
        srt_path = subtitle.get("srt_path") or ""

        # 5. Render (burn subtitles; real watermark support exists but unused here)
        final_video = dirs["final"] / "rendered.mp4"
        render = run_stage(
            "render (burn-in)", "/v1/render",
            {"video_path": str(original_copy), "subtitle_path": ass_path,
             "output_path": str(final_video), "encoder": None,
             "preset": "medium", "crf": 18},
            save_to=None,
        )
        if render is None:
            worker.stop()
            return finish(1)
        summary["final_video"] = str(final_video)

        summary["status"] = "success"
        print()
        print(f"[done] SUCCESS in {time.monotonic() - started:.1f}s")
        print(f"  input duration : {summary['input_duration']}s")
        print(f"  transcript     : {summary['stt']}")
        print(f"  translation    : {summary['translation']}")
        print(f"  subtitles      : {srt_path} / {ass_path}")
        print(f"  final video    : {summary['final_video']}")
        print(f"  tts (dubbing)  : not implemented")
        print(f"  audio mix      : not implemented")
    finally:
        worker.stop()

    return finish(0)


if __name__ == "__main__":
    sys.exit(main())
