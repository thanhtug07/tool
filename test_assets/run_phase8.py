#!/usr/bin/env python3
"""Phase 8 — Real Integration Test with proper subprocess management."""

import json, os, signal, subprocess, sys, time, urllib.request, urllib.error
from pathlib import Path
from datetime import datetime, timezone

PORT = 18770
TOKEN = "phase8tok"
BASE = f"http://127.0.0.1:{PORT}"
WORKER_DIR = str(Path(__file__).resolve().parent.parent / "worker")
CLIP = str(Path(__file__).resolve().parent / "short_clip_3min.mp4")
OUT = Path(__file__).resolve().parent / "phase8_output"
OUT.mkdir(exist_ok=True)
JOB = f"phase8_{int(time.time())}"

def ts(): return datetime.now(timezone.utc).isoformat()
def log(s, **kw): print(f"  [{ts()}] {s}", **kw)

def api(method, path, body=None):
    data = json.dumps(body).encode() if body else None
    r = urllib.request.Request(f"{BASE}{path}", data=data, method=method,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(r, timeout=600) as resp:
        return json.loads(resp.read())

def start_worker():
    env = {**os.environ, "WORKER_AUTH_TOKEN": TOKEN}
    p = subprocess.Popen([sys.executable, "-m", "src.main", "--port", str(PORT)],
        cwd=WORKER_DIR, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # Read READY from stdout
    deadline = time.time() + 30
    while time.time() < deadline:
        line = p.stdout.readline().decode("utf-8", errors="replace").strip()
        if line.startswith("READY"):
            log(f"Worker ready: {line}")
            time.sleep(0.5)
            return p
        if p.poll() is not None:
            stderr = p.stderr.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Worker died: {stderr[-500:]}")
    raise RuntimeError("Worker timeout")

def stop_worker(p):
    if p and p.poll() is None:
        p.stdin.write(b"SHUTDOWN\n")
        p.stdin.flush()
        try: p.wait(timeout=10)
        except: p.kill(); p.wait(timeout=5)
    log("Worker stopped")

STAGES = []

def run():
    log(f"Phase 8 — Real Integration Test")
    log(f"  Clip: {CLIP}")
    t0 = time.time()
    wp = start_worker()
    try:
        h = api("GET", "/health"); log(f"Health: {h}")

        # Stage 1: Extract
        log("EXTRACT start"); t1 = time.time()
        audio_dir = str(OUT / "audio"); os.makedirs(audio_dir, exist_ok=True)
        r = api("POST", "/v1/audio/extract", {"video_path": CLIP, "output_path": os.path.join(audio_dir, "audio.wav"), "job_id": JOB})
        STAGES.append({"stage": "extract", "elapsed": time.time()-t1, "result": r})
        log(f"EXTRACT done in {time.time()-t1:.1f}s — duration={r.get('duration_seconds')}s")
        audio_path = r["output_path"]

        # Stage 2: STT
        log("STT start"); t2 = time.time()
        r = api("POST", "/v1/stt/transcribe", {"audio_path": audio_path, "project_id": "proj8", "model": "large-v3", "device": "auto", "job_id": JOB})
        STAGES.append({"stage": "stt", "elapsed": time.time()-t2, "segments": len(r.get("segments",[]))})
        log(f"STT done in {time.time()-t1:.1f}s — {len(r.get('segments',[]))} segments")
        transcript = r

        # Stage 3: Translate (mock)
        log("TRANSLATE start"); t3 = time.time()
        r = api("POST", "/v1/translate", {"transcript": transcript, "target_language": "zh", "source_language": "en", "provider": "mock", "model": "mock", "job_id": JOB})
        STAGES.append({"stage": "translate", "elapsed": time.time()-t3, "blocks": len(r.get("blocks",[]))})
        log(f"TRANSLATE done in {time.time()-t3:.1f}s — {len(r.get('blocks',[]))} blocks")
        translation = r

        # Stage 4: Subtitle
        log("SUBTITLE start"); t4 = time.time()
        sub_dir = str(OUT / "subs"); os.makedirs(sub_dir, exist_ok=True)
        r = api("POST", "/v1/subtitle", {"transcript": transcript, "translation": translation, "language": "zh", "output_dir": sub_dir, "project_id": "proj8", "job_id": JOB})
        STAGES.append({"stage": "subtitle", "elapsed": time.time()-t4, "cues": len(r.get("cues",[]))})
        log(f"SUBTITLE done in {time.time()-t4:.1f}s — {len(r.get('cues',[]))} cues")
        ass_path = r.get("ass_path", "")
        cues = r.get("cues", [])

        # Stage 5: TTS (limit to first 5 cues for speed)
        tts_result = {}
        if cues:
            log("TTS start"); t5 = time.time()
            tts_dir = str(OUT / "tts"); os.makedirs(tts_dir, exist_ok=True)
            tts_cues = [{"start": c["start"], "end": c["end"], "text": c.get("text","")} for c in cues[:5]]
            r = api("POST", "/v1/tts/synthesize", {"cues": tts_cues, "engine": "edge", "language": "zh", "duration_seconds": transcript["segments"][-1]["end"] if transcript.get("segments") else 180, "output_dir": tts_dir, "job_id": JOB})
            STAGES.append({"stage": "tts", "elapsed": time.time()-t5, "voice": r.get("voice_used")})
            log(f"TTS done in {time.time()-t5:.1f}s — voice={r.get('voice_used')}")
            tts_result = r
        else:
            log("TTS skipped — no cues")

        # Stage 6: Render
        if ass_path:
            log("RENDER start"); t6 = time.time()
            render_dir = str(OUT / "render"); os.makedirs(render_dir, exist_ok=True)
            body = {"video_path": CLIP, "subtitle_path": ass_path, "output_path": os.path.join(render_dir, "output.mp4"), "job_id": JOB}
            vt = tts_result.get("voice_track_path")
            if vt: body["voice_track_path"] = vt
            r = api("POST", "/v1/render", body)
            STAGES.append({"stage": "render", "elapsed": time.time()-t6, "output": r.get("output_path","")})
            log(f"RENDER done in {time.time()-t6:.1f}s")
        else:
            log("RENDER skipped — no subtitle")

    finally:
        stop_worker(wp)

    total = time.time() - t0
    log(f"\n{'='*60}")
    log(f"PHASE 8 RESULTS — {total:.1f}s total")
    log(f"{'='*60}")
    for s in STAGES:
        log(f"  {s['stage']:12s}  {s['elapsed']:.1f}s  {json.dumps({k:v for k,v in s.items() if k not in ('stage','elapsed')})}")

    # Check output
    render_out = OUT / "render" / "output.mp4"
    if render_out.exists():
        log(f"\n  OUTPUT: {render_out} ({render_out.stat().st_size/1024:.0f} KB)")
        log("  RESULT: PASS")
    else:
        log("  RESULT: FAIL — no output video")
        sys.exit(1)

    with open(OUT / "phase8_timestamps.json", "w") as f:
        json.dump(STAGES, f, indent=2)
    return 0

if __name__ == "__main__":
    sys.exit(run())
