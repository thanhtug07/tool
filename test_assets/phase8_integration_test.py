#!/usr/bin/env python3
"""Phase 8 — Real Integration Test (non-chunked, orchestrator_v2=true).

Starts the worker, then calls each stage endpoint sequentially, exactly as
the Rust PipelineRunner + TaskRunner would.  Records timestamps for
concurrency analysis.

Environment assumptions:
  - FFmpeg on PATH
  - Worker Python deps installed (worker/)
  - Short clip at test_assets/short_clip_3min.mp4
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
WORKER_PORT = 18765  # use a high port to avoid collision
WORKER_DIR = Path(__file__).resolve().parent.parent / "worker"
CLIP_PATH = Path(__file__).resolve().parent / "short_clip_3min.mp4"
PROJECT_DIR = Path(__file__).resolve().parent / "phase8_project"
TOKEN = "phase8-test-token"
BASE = f"http://127.0.0.1:{WORKER_PORT}"
JOB_ID = f"phase8_{int(time.time())}"
PROJECT_ID = "proj_phase8"
TARGET_LANG = "zh"
SOURCE_LANG = "en"

# Stage timestamps
STAGE_LOG: list[dict] = []

def ts():
    return datetime.now(timezone.utc).isoformat()

def log_stage(stage: str, event: str, detail: str = ""):
    now = ts()
    entry = {"stage": stage, "event": event, "timestamp": now, "detail": detail}
    STAGE_LOG.append(entry)
    print(f"  [{now}] {stage}: {event} {detail}".strip())

def api_call(method: str, path: str, body: dict | None = None, expect: int = 200) -> dict:
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {TOKEN}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} from {method} {path}: {body_text}") from e

# ---------------------------------------------------------------------------
# Worker lifecycle
# ---------------------------------------------------------------------------
_worker_proc: subprocess.Popen | None = None

def start_worker():
    global _worker_proc
    print("Starting worker ...")
    env = os.environ.copy()
    env["WORKER_AUTH_TOKEN"] = TOKEN
    _worker_proc = subprocess.Popen(
        [sys.executable, "-m", "src.main", "--port", str(WORKER_PORT)],
        cwd=str(WORKER_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Wait for READY line on stdout
    deadline = time.time() + 30
    while time.time() < deadline:
        line = _worker_proc.stdout.readline().decode("utf-8", errors="replace").strip()
        if line.startswith("READY"):
            print(f"Worker ready: {line}")
            # Give uvicorn a moment to fully bind
            time.sleep(0.5)
            return
        if _worker_proc.poll() is not None:
            stderr = _worker_proc.stderr.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Worker exited during startup: {stderr[-500:]}")
    raise RuntimeError("Worker did not become ready within 30s")

def stop_worker():
    global _worker_proc
    if _worker_proc and _worker_proc.poll() is None:
        _worker_proc.terminate()
        try:
            _worker_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _worker_proc.kill()
    print("Worker stopped.")

# ---------------------------------------------------------------------------
# Stage execution
# ---------------------------------------------------------------------------
def stage_extract() -> dict:
    """Stage 1: Audio extraction."""
    log_stage("extract", "STARTED")
    out_dir = str(PROJECT_DIR / "artifacts")
    os.makedirs(out_dir, exist_ok=True)
    result = api_call("POST", "/v1/audio/extract", {
        "video_path": str(CLIP_PATH),
        "output_path": os.path.join(out_dir, "audio.wav"),
        "job_id": JOB_ID,
    })
    log_stage("extract", "COMPLETED", f"duration={result.get('duration_seconds')}s")
    return result

def stage_stt(audio_path: str) -> dict:
    """Stage 2: Speech-to-text."""
    log_stage("stt", "STARTED")
    result = api_call("POST", "/v1/stt/transcribe", {
        "audio_path": audio_path,
        "job_id": JOB_ID,
        "project_id": PROJECT_ID,
    })
    seg_count = len(result.get("segments", []))
    log_stage("stt", "COMPLETED", f"segments={seg_count}")
    return result

def stage_translate(transcript: dict) -> dict:
    """Stage 3: Translation (mock provider)."""
    log_stage("translate", "STARTED")
    result = api_call("POST", "/v1/translate", {
        "transcript": transcript,
        "target_language": TARGET_LANG,
        "source_language": SOURCE_LANG,
        "provider": "mock",
        "model": "mock",
        "job_id": JOB_ID,
    })
    block_count = len(result.get("blocks", []))
    log_stage("translate", "COMPLETED", f"blocks={block_count}")
    return result

def stage_subtitle(transcript: dict, translation: dict) -> dict:
    """Stage 4: Subtitle generation."""
    log_stage("subtitle", "STARTED")
    out_dir = str(PROJECT_DIR / "artifacts")
    os.makedirs(out_dir, exist_ok=True)
    result = api_call("POST", "/v1/subtitle", {
        "transcript": transcript,
        "translation": translation,
        "language": TARGET_LANG,
        "output_dir": out_dir,
        "job_id": JOB_ID,
    })
    cue_count = len(result.get("cues", []))
    log_stage("subtitle", "COMPLETED", f"cues={cue_count}")
    return result

def stage_tts(cues: list[dict], duration: float) -> dict:
    """Stage 5: TTS (voice track)."""
    if not cues:
        log_stage("tts", "SKIPPED", "no cues")
        return {}
    log_stage("tts", "STARTED")
    out_dir = str(PROJECT_DIR / "artifacts")
    os.makedirs(out_dir, exist_ok=True)
    tts_cues = [{"start": c["start"], "end": c["end"], "text": c.get("text", "")} for c in cues]
    result = api_call("POST", "/v1/tts/synthesize", {
        "cues": tts_cues[:5],  # limit to 5 cues for speed
        "engine": "edge",
        "language": TARGET_LANG,
        "duration_seconds": duration,
        "output_dir": out_dir,
        "job_id": JOB_ID,
    })
    log_stage("tts", "COMPLETED", f"voice={result.get('voice_used')}")
    return result

def stage_render(ass_path: str, voice_track: str | None = None) -> dict:
    """Stage 6: Video render (burn subtitles)."""
    log_stage("render", "STARTED")
    out_dir = str(PROJECT_DIR / "artifacts")
    output_path = os.path.join(out_dir, "output.mp4")
    body = {
        "video_path": str(CLIP_PATH),
        "subtitle_path": ass_path,
        "output_path": output_path,
        "job_id": JOB_ID,
    }
    if voice_track:
        body["voice_track_path"] = voice_track
    result = api_call("POST", "/v1/render", body)
    log_stage("render", "COMPLETED", f"output={output_path}")
    return result

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print(f"Phase 8 — Real Integration Test")
    print(f"  Clip: {CLIP_PATH} ({CLIP_PATH.stat().st_size / 1024:.0f} KB)")
    print(f"  Project dir: {PROJECT_DIR}")
    print(f"  Job ID: {JOB_ID}")
    print()

    os.makedirs(PROJECT_DIR, exist_ok=True)
    overall_start = time.time()

    try:
        start_worker()

        # Verify health
        health = api_call("GET", "/health")
        print(f"  Health: {health}")
        print()

        # Run stages
        extract_result = stage_extract()
        audio_path = extract_result["output_path"]
        duration = extract_result["duration_seconds"]

        stt_result = stage_stt(audio_path)
        transcript = stt_result  # contains "segments" key

        translate_result = stage_translate(transcript)

        subtitle_result = stage_subtitle(transcript, translate_result)
        ass_path = subtitle_result.get("ass_path", "")

        tts_result = stage_tts(subtitle_result.get("cues", []), duration)
        voice_track = tts_result.get("voice_track_path")

        render_result = stage_render(ass_path, voice_track)

    finally:
        stop_worker()

    overall_elapsed = time.time() - overall_start

    # Summary
    print()
    print("=" * 60)
    print("PHASE 8 — REAL INTEGRATION RESULTS")
    print("=" * 60)

    for entry in STAGE_LOG:
        print(f"  {entry['timestamp']}  {entry['stage']:12s}  {entry['event']:10s}  {entry['detail']}")

    print()
    print(f"  Total wall time: {overall_elapsed:.2f}s")
    print()

    # Check output file
    output_path = PROJECT_DIR / "artifacts" / "output.mp4"
    if output_path.exists():
        print(f"  Output video: {output_path} ({output_path.stat().st_size / 1024:.0f} KB)")
        print("  RESULT: PASS — All stages completed, output video exists.")
    else:
        print("  RESULT: FAIL — Output video not found.")
        sys.exit(1)

    # Write timestamp log for analysis
    log_path = PROJECT_DIR / "stage_timestamps.json"
    with open(log_path, "w") as f:
        json.dump(STAGE_LOG, f, indent=2)
    print(f"  Timestamps: {log_path}")
    print()

    return 0

if __name__ == "__main__":
    sys.exit(main())
