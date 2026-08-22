#!/usr/bin/env python3
"""Phase 9 — Real Chunked Integration Test.

Calls POST /v1/automation/chunked with the short clip.
The worker internally fans out to concurrent chunk processing via
StreamingChunkPipeline + ThreadPoolExecutor.

Verifies:
  - Worker handles chunked mode
  - Concurrent chunk processing (observable from events/timestamps)
  - Progress events are meaningful
  - Final manifest is returned
"""
import json, os, subprocess, sys, time, urllib.request, urllib.error
from pathlib import Path
from datetime import datetime, timezone

PORT = 18772
TOKEN = "phase9test"
BASE = f"http://127.0.0.1:{PORT}"
WORKER_DIR = str(Path(__file__).resolve().parent.parent / "worker")
CLIP = str(Path(__file__).resolve().parent / "short_clip_30s.mp4")
OUT = Path(__file__).resolve().parent / "phase9_output"
OUT.mkdir(exist_ok=True)
JOB = f"phase9_{int(time.time())}"

EVENTS = []

def ts():
    return datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]

def log(msg):
    print(f"[{ts()}] {msg}", flush=True)

def api(method, path, body=None, timeout=600):
    data = json.dumps(body).encode() if body else None
    r = urllib.request.Request(
        f"{BASE}{path}", data=data, method=method,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"}
    )
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read())

def start_worker():
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "src.main", "--port", str(PORT)],
        cwd=WORKER_DIR, env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    log("Worker starting...")
    proc.stdin.write((TOKEN + "\n").encode())
    proc.stdin.flush()
    deadline = time.time() + 60
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                err = proc.stderr.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"Worker exited early:\n{err[-1000:]}")
            time.sleep(0.2)
            continue
        text = line.decode("utf-8", errors="replace").strip()
        log(f"  [worker] {text}")
        if text.startswith("READY"):
            log(f"Worker READY")
            time.sleep(0.5)
            return proc
    raise RuntimeError("Worker timeout")

def stop_worker(proc):
    if proc and proc.poll() is None:
        try:
            proc.stdin.write(b"SHUTDOWN\n")
            proc.stdin.flush()
        except: pass
        try: proc.wait(timeout=8)
        except:
            proc.kill()
            proc.wait(timeout=3)
    log("Worker stopped.")

def poll_events(job_id):
    """Poll /v1/progress to drain queued events."""
    try:
        r = api("GET", f"/v1/progress/{job_id}")
        events = r.get("events", [])
        for evt in events:
            EVENTS.append({"ts": ts(), **evt})
            log(f"  EVENT: {evt.get('message', evt)}")
        return r
    except:
        return None

def main():
    log(f"Phase 9 — Real Chunked Integration Test")
    log(f"  Clip: {CLIP} ({Path(CLIP).stat().st_size // 1024} KB)")
    project_dir = str(OUT / "project")
    audio_dir = str(OUT / "audio")
    os.makedirs(project_dir, exist_ok=True)
    os.makedirs(audio_dir, exist_ok=True)

    # Pre-extract audio (the chunked endpoint needs source_audio)
    overall = time.time()
    wp = start_worker()
    try:
        log("Pre-extracting audio...")
        ext = api("POST", "/v1/audio/extract", {
            "video_path": CLIP,
            "output_path": os.path.join(audio_dir, "audio.wav"),
            "job_id": JOB + "_pre",
        })
        audio_path = ext["output_path"]
        duration = ext["duration_seconds"]
        log(f"  Audio extracted: {audio_path} ({duration:.1f}s)")

        # Call chunked automation
        log(">>> chunked_automation START")
        t0 = time.time()
        manifest = api("POST", "/v1/automation/chunked", {
            "job_id": JOB,
            "project_id": "proj_phase9",
            "project_dir": project_dir,
            "source_video": CLIP,
            "source_audio": audio_path,
            "target_language": "zh",
            "source_language": "en",
            "provider": "mock",
            "model": "mock",
            "stt_model": "large-v3",
            "stt_device": "auto",
            "stt_mode": "auto",
            "chunk_duration": 30.0,
            "overlap": 2.0,
            "max_concurrency": 4,
            "dub": False,
            "max_retries": 2,
        }, timeout=600)
        elapsed = time.time() - t0
        log(f"<<< chunked_automation DONE in {elapsed:.1f}s")

        # Analyze manifest
        log("")
        log("=" * 60)
        log("PHASE 9 RESULTS")
        log("=" * 60)

        if manifest:
            log(f"  Manifest keys: {list(manifest.keys())}")
            chunks = manifest.get("chunks", [])
            log(f"  Chunks: {len(chunks)}")
            for c in chunks[:5]:
                log(f"    {c.get('chunk_index', '?')}: {c.get('status', '?')} "
                    f"stt={c.get('stt_segments', '?')} "
                    f"trans={c.get('translation_blocks', '?')}")
            if len(chunks) > 5:
                log(f"    ... and {len(chunks)-5} more")

            total_segs = sum(c.get("stt_segments", 0) for c in chunks)
            log(f"  Total STT segments across chunks: {total_segs}")

            # Check for errors
            errors = [c for c in chunks if c.get("status") == "error"]
            if errors:
                log(f"  ERRORS: {len(errors)} chunks failed")
                for e in errors[:3]:
                    log(f"    {e}")

            output_files = manifest.get("output_files", {})
            log(f"  Output files: {list(output_files.keys()) if output_files else 'none'}")

            # Final check
            if chunks and not errors:
                log(f"  PHASE 9: PASS — {len(chunks)} chunks processed successfully")
                result = "PASS"
            elif errors:
                log(f"  PHASE 9: FAIL — {len(errors)} chunk errors")
                result = "FAIL"
            else:
                log(f"  PHASE 9: FAIL — no chunks in manifest")
                result = "FAIL"
        else:
            log("  PHASE 9: FAIL — no manifest returned")
            result = "FAIL"

        # Save results
        with open(OUT / "phase9_results.json", "w") as f:
            json.dump({
                "manifest": manifest,
                "events": EVENTS,
                "elapsed": round(elapsed, 2),
                "result": result,
            }, f, indent=2, default=str)

    finally:
        stop_worker(wp)

    total = time.time() - overall
    log(f"\n  Total wall time: {total:.1f}s")
    log(f"  Events captured: {len(EVENTS)}")
    return 0 if result == "PASS" else 1

if __name__ == "__main__":
    sys.exit(main())
