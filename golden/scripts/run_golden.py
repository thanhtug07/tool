"""Golden E2E pipeline runner (RELEASE-P0-006).

Proves the real worker can traverse the whole MVP vertical slice on a real
(but synthetic, copyright-safe) video:

    golden.mp4 → audio extract → real STT (faster-whisper) → translate
    → subtitle generation → render (FFmpeg/libass) → export (QC)

The runner spawns the actual worker (the same sidecar protocol the Rust
WorkerManager uses), drives its HTTP API, and validates every stage against
`golden/expected/expected.json` with explicit tolerances. Translation uses
the deterministic `mock` provider by default (no credentials, no network);
`--provider gemini` runs the real provider and requires an API key stored in
Windows Credential Manager under `gemini`.

Exit code 0 only when every stage passes. Results are printed as a table and
optionally written to `golden/results/latest.json`.

Usage:
    py golden/scripts/run_golden.py [--provider mock|gemini] [--model tiny]
        [--device cpu] [--language en] [--target-zh zh] [--save-results]
"""

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
GOLDEN = ROOT / "golden"
WORKER_DIR = ROOT / "worker"

STAGE_TIMEOUT_S = 1800


# ---------------------------------------------------------------------------
# Model cache
# ---------------------------------------------------------------------------


def warm_stt_model(model: str, device: str) -> None:
    """Load the STT model once so the worker never stalls on a Hugging Face
    revision check mid-pipeline (observed: HF API hung ~3 min inside the
    transcribe route). After warming, the worker runs with HF_HUB_OFFLINE=1.
    """
    print(f"warming STT model `{model}` on `{device}` (first run downloads it)…")
    t0 = time.time()
    import faster_whisper  # noqa: PLC0415

    faster_whisper.WhisperModel(model, device=device, compute_type="int8")
    print(f"STT model ready in {time.time() - t0:.1f}s")


# ---------------------------------------------------------------------------
# Worker lifecycle (mirrors src-tauri WorkerManager sidecar protocol)
# ---------------------------------------------------------------------------

def spawn_worker() -> tuple[subprocess.Popen, int, str, threading.Thread]:
    """Spawn the real worker and complete the READY handshake.

    After the handshake, a background thread continuously drains the worker's
    stdout: the worker logs progress on every stage, and a full pipe buffer
    would deadlock it mid-transcribe (the same reason WorkerManager attaches
    forwarder threads).
    """
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    token = secrets.token_hex(32)
    proc = subprocess.Popen(
        [sys.executable, "-m", "src.main", "--port", str(port)],
        cwd=WORKER_DIR,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env={
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            # The model is warmed up front (see warm_stt_model); never let the
            # worker stall on a Hugging Face network round-trip mid-stage.
            "HF_HUB_OFFLINE": "1",
        },
    )
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(f"WORKER_AUTH_TOKEN={token}\n")
    proc.stdin.flush()

    deadline = time.time() + 90
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
        raise RuntimeError("worker READY handshake failed (token mismatch or timeout)")

    def _drain() -> None:
        for _ in proc.stdout:  # discard worker logs (they never contain secrets)
            pass

    drain = threading.Thread(target=_drain, daemon=True)
    drain.start()
    return proc, port, token, drain


def shutdown_worker(proc: subprocess.Popen) -> None:
    if proc.poll() is None and proc.stdin is not None:
        try:
            proc.stdin.write("SHUTDOWN\n")
            proc.stdin.flush()
        except OSError:
            pass
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


# ---------------------------------------------------------------------------
# HTTP helper (stdlib only — no extra deps in the runner)
# ---------------------------------------------------------------------------

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
# Validation helpers
# ---------------------------------------------------------------------------

def ffprobe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True,
    )
    return float(out.stdout.strip())


def ffprobe_streams(path: Path) -> list[str]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name",
         "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    )
    data = json.loads(out.stdout)
    return [f"{s.get('codec_type')}:{s.get('codec_name')}" for s in data.get("streams", [])]


_RESULTS: list[dict] = []


def check(ok: bool, label: str, detail: str) -> None:
    _RESULTS.append({"check": label, "ok": bool(ok), "detail": detail})
    print(f"  [{'PASS' if ok else 'FAIL'}] {label} — {detail}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Golden E2E pipeline runner")
    parser.add_argument("--provider", default="mock", choices=["mock", "gemini", "local"])
    parser.add_argument("--model", default="tiny")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--language", default="en")
    parser.add_argument("--target-language", default="zh")
    parser.add_argument("--save-results", action="store_true")
    args = parser.parse_args()

    expected = json.loads((GOLDEN / "expected" / "expected.json").read_text(encoding="utf-8"))
    video = GOLDEN / "video" / "golden.mp4"
    assert video.is_file(), f"golden video missing — run generate_golden.py first: {video}"
    warm_stt_model(args.model, args.device)

    work = GOLDEN / "results"
    work.mkdir(parents=True, exist_ok=True)
    _RESULTS.clear()
    started = time.time()

    print(f"golden E2E start: video={video} provider={args.provider} model={args.model}")
    proc, port, token, _drain = spawn_worker()
    try:
        # -- stage 1: audio extract -----------------------------------------
        print("stage 1/6: audio extract")
        wav = work / "audio.wav"
        payload = expect_ok(*http_post(port, token, "/v1/audio/extract", {
            "video_path": str(video),
            "output_path": str(wav),
            "job_id": "golden-extract",
        }), "extract")
        dur = ffprobe_duration(wav)
        expected_dur = expected["video"]["duration_seconds"]
        check(wav.is_file() and wav.stat().st_size > 0, "extract produced audio", str(wav))
        check(abs(dur - expected_dur) < 1.5, "extract duration ~ video", f"{dur:.2f}s vs {expected_dur:.2f}s")

        # -- stage 2: real STT ----------------------------------------------
        print("stage 2/6: STT (faster-whisper)")
        stt_start = time.time()
        transcript = expect_ok(*http_post(port, token, "/v1/stt/transcribe", {
            "audio_path": str(wav),
            "project_id": "golden-e2e",
            "model": args.model,
            "device": args.device,
            "language": args.language,
            "job_id": "golden-stt",
        }), "stt")
        stt_s = time.time() - stt_start
        text = " ".join(seg["text"] for seg in transcript["segments"]).lower()
        (work / "transcript.json").write_text(json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8")
        check(len(transcript["segments"]) >= 1, "stt produced segments", f"{len(transcript['segments'])} segs in {stt_s:.1f}s")
        for phrase in expected["transcript_contains"]:
            if phrase in ("five five five",):
                check("555" in text or "five five five" in text, f"stt mentions phone number", f"got …{text[-60:]}")
            else:
                check(phrase in text, f"stt contains `{phrase}`", f"text: {text[:120]}…")

        # -- stage 3: translate ---------------------------------------------
        print("stage 3/6: translate")
        translation = expect_ok(*http_post(port, token, "/v1/translate", {
            "transcript": transcript,
            "project_id": "golden-e2e",
            "provider": args.provider,
            "target_language": args.target_language,
            "model": "gemini-2.5-flash-lite",
            "job_id": "golden-translate",
        }), "translate")
        (work / "translation.json").write_text(json.dumps(translation, ensure_ascii=False, indent=2), encoding="utf-8")
        items = [t for b in translation["blocks"] for t in b["translations"]]
        check(len(items) == len(transcript["segments"]), "translate covered every segment", f"{len(items)} items")
        if args.provider == "mock":
            check(all(it["translated_text"].startswith(f"[{args.target_language}] ") for it in items),
                  "mock translate deterministic prefix", "every item prefixed")
        else:
            check(all(it["translated_text"].strip() for it in items), "real translate non-empty", "all items non-empty")

        # -- stage 4: subtitle generation -----------------------------------
        print("stage 4/6: subtitle generation")
        subtitle = expect_ok(*http_post(port, token, "/v1/subtitle", {
            "transcript": transcript,
            "translation": translation,
            "project_id": "golden-e2e",
            "output_dir": str(work),
            "language": args.language,
            "job_id": "golden-subtitle",
        }), "subtitle")
        cues = subtitle["cues"]
        check(len(cues) >= expected["min_cues"], "at least min_cues generated", f"{len(cues)} cues")
        check(all(c["start"] < c["end"] and c["text"].strip() for c in cues), "cue timing/text valid", "all cues well-formed")
        check(Path(subtitle["srt_path"]).is_file() and Path(subtitle["ass_path"]).is_file(), "srt+ass written",
              f"{subtitle['srt_path']} / {subtitle['ass_path']}")

        # -- stage 5: render -------------------------------------------------
        print("stage 5/6: render (FFmpeg/libass)")
        render_start = time.time()
        out = work / "rendered.mp4"
        rendered = expect_ok(*http_post(port, token, "/v1/render", {
            "video_path": str(video),
            "subtitle_path": subtitle["ass_path"],
            "output_path": str(out),
            "job_id": "golden-render",
        }), "render")
        render_s = time.time() - render_start
        check(out.is_file() and out.stat().st_size > 0, "render produced output", f"{rendered['encoder_used']} in {render_s:.1f}s")
        streams = ffprobe_streams(out)
        check(any(s.startswith("video:") for s in streams), "render output has video stream", ", ".join(streams))
        out_dur = ffprobe_duration(out)
        check(abs(out_dur - expected_dur) < 1.5, "render duration ~ source", f"{out_dur:.2f}s vs {expected_dur:.2f}s")

        # -- stage 6: export with QC ----------------------------------------
        print("stage 6/6: export (QC)")
        exported = expect_ok(*http_post(port, token, "/v1/export/video", {
            "source_video": str(out),
            "target_dir": str(work),
            "name": "golden-final",
            "run_qc": True,
        }), "export")
        qc = exported["qc"]
        check(Path(exported["path"]).is_file(), "export produced file", exported["path"])
        check(qc["passed"], "export QC passed", f"{len(qc['issues'])} issues")

    finally:
        shutdown_worker(proc)

    total_s = time.time() - started
    results = list(_RESULTS)
    passed = all(r["ok"] for r in results)
    print(f"\ngolden E2E {'PASS' if passed else 'FAIL'} — {sum(1 for r in results if r['ok'])}/{len(results)} checks in {total_s:.1f}s")

    if args.save_results:
        report = {
            "schema_version": 1,
            "generated_by": "golden/scripts/run_golden.py",
            "provider": args.provider,
            "model": args.model,
            "device": args.device,
            "total_seconds": round(total_s, 1),
            "passed": passed,
            "checks": results,
        }
        (work / "latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"results: {work / 'latest.json'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
