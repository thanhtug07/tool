#!/usr/bin/env python3
"""Phase 8 — Real Integration Test (fixed subprocess management).

The worker reads its auth token from stdin when stdin is a pipe.
We must write the token BEFORE waiting for READY on stdout.
"""
import json, os, subprocess, sys, time, urllib.request, urllib.error
from pathlib import Path
from datetime import datetime, timezone

PORT = 18771
TOKEN = "phase8test"
BASE = f"http://127.0.0.1:{PORT}"
WORKER_DIR = str(Path(__file__).resolve().parent.parent / "worker")
CLIP = str(Path(__file__).resolve().parent / "short_clip_2min.mp4")
OUT = Path(__file__).resolve().parent / "phase8_output"
OUT.mkdir(exist_ok=True)
JOB = f"phase8_{int(time.time())}"

STAGES = []

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
        bufsize=1,
    )
    log("Worker starting...")

    # CRITICAL: write the token to stdin so worker reads it (sidecar protocol)
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
            log(f"Worker READY: {text}")
            time.sleep(0.5)
            return proc
    raise RuntimeError("Worker did not become ready in 60s")

def stop_worker(proc):
    if proc and proc.poll() is None:
        try:
            proc.stdin.write(b"SHUTDOWN\n")
            proc.stdin.flush()
        except:
            pass
        try:
            proc.wait(timeout=8)
        except:
            proc.kill()
            proc.wait(timeout=3)
    log("Worker stopped.")

def run_stage(name, method, path, body=None, timeout=600):
    log(f">>> {name} START")
    t0 = time.time()
    try:
        result = api(method, path, body, timeout=timeout)
        elapsed = time.time() - t0
        STAGES.append({"name": name, "elapsed": round(elapsed, 2), "status": "OK"})
        log(f"<<< {name} DONE in {elapsed:.1f}s")
        return result
    except Exception as e:
        elapsed = time.time() - t0
        STAGES.append({"name": name, "elapsed": round(elapsed, 2), "status": "FAIL", "error": str(e)[:300]})
        log(f"!!! {name} FAILED in {elapsed:.1f}s: {e}")
        return None

def main():
    log(f"Phase 8 — Real Integration Test")
    log(f"  Clip: {CLIP} ({Path(CLIP).stat().st_size // 1024} KB)")
    overall = time.time()
    wp = start_worker()
    try:
        h = run_stage("health", "GET", "/health")
        if not h:
            return 1

        # 1. Extract
        audio_dir = str(OUT / "audio")
        os.makedirs(audio_dir, exist_ok=True)
        ext = run_stage("extract", "POST", "/v1/audio/extract", {
            "video_path": CLIP,
            "output_path": os.path.join(audio_dir, "audio.wav"),
            "job_id": JOB,
        })
        if not ext: return 1
        audio_path = ext["output_path"]
        duration = ext["duration_seconds"]
        log(f"  Audio: {audio_path} ({duration:.1f}s)")

        # 2. STT (this is slow on CPU)
        stt = run_stage("stt", "POST", "/v1/stt/transcribe", {
            "audio_path": audio_path,
            "project_id": "proj_phase8",
            "model": "large-v3",
            "device": "auto",
            "total_duration_seconds": duration,
            "job_id": JOB,
        }, timeout=600)
        if not stt: return 1
        transcript = stt
        nseg = len(transcript.get("segments", []))
        log(f"  Transcript: {nseg} segments")

        # 3. Translate (mock)
        tr = run_stage("translate", "POST", "/v1/translate", {
            "transcript": transcript,
            "target_language": "zh",
            "source_language": "en",
            "provider": "mock",
            "model": "mock",
            "project_id": "proj_phase8",
            "job_id": JOB,
        })
        if not tr: return 1
        translation = tr
        log(f"  Translation: {len(translation.get('blocks', []))} blocks")

        # 4. Subtitle
        sub_dir = str(OUT / "subs")
        os.makedirs(sub_dir, exist_ok=True)
        sub = run_stage("subtitle", "POST", "/v1/subtitle", {
            "transcript": transcript,
            "translation": translation,
            "language": "zh",
            "output_dir": sub_dir,
            "project_id": "proj_phase8",
            "job_id": JOB,
        })
        if not sub: return 1
        cues = sub.get("cues", [])
        ass_path = sub.get("ass_path", "")
        log(f"  Cues: {len(cues)}, ASS: {ass_path}")

        # 5. TTS (3 cues only)
        tts_out = {}
        if cues:
            tts_dir = str(OUT / "tts")
            os.makedirs(tts_dir, exist_ok=True)
            tts_cues = [{"start": c["start"], "end": c["end"], "text": c.get("text", "")} for c in cues[:3]]
            tts = run_stage("tts", "POST", "/v1/tts/synthesize", {
                "cues": tts_cues,
                "engine": "edge",
                "language": "zh",
                "duration_seconds": duration,
                "output_dir": tts_dir,
                "job_id": JOB,
            }, timeout=120)
            if tts:
                tts_out = tts
                log(f"  Voice: {tts.get('voice_used')}")

        # 6. Render
        if ass_path:
            render_dir = str(OUT / "render")
            os.makedirs(render_dir, exist_ok=True)
            body = {
                "video_path": CLIP,
                "subtitle_path": ass_path,
                "output_path": os.path.join(render_dir, "output.mp4"),
                "job_id": JOB,
            }
            if tts_out.get("voice_track_path"):
                body["voice_track_path"] = tts_out["voice_track_path"]
            rend = run_stage("render", "POST", "/v1/render", body, timeout=300)
            if rend:
                output = Path(rend.get("output_path", ""))
                if output.exists():
                    log(f"  Output: {output} ({output.stat().st_size // 1024} KB)")

    finally:
        stop_worker(wp)

    total = time.time() - overall
    log("")
    log("=" * 60)
    log(f"PHASE 8 RESULTS — {total:.1f}s total")
    log("=" * 60)
    for s in STAGES:
        log(f"  {s['name']:12s}  {s['elapsed']:6.1f}s  {s['status']}")

    output_path = OUT / "render" / "output.mp4"
    result = "PASS" if (output_path.exists() and output_path.stat().st_size > 0) else "FAIL"
    log(f"  PHASE 8: {result}")

    with open(OUT / "phase8_timestamps.json", "w") as f:
        json.dump({"stages": STAGES, "total_seconds": round(total, 2), "result": result}, f, indent=2)

    return 0 if result == "PASS" else 1

if __name__ == "__main__":
    sys.exit(main())
