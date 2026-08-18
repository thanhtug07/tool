"""E2E pipeline runner (benchmark + acceptance evidence).

Builds a REAL short video fixture (ffmpeg test pattern + real synthesized
speech), starts the real Python worker over loopback HTTP, drives the real
pipeline stages (extract audio -> STT -> translate -> subtitle -> TTS ->
render) exactly as the Rust PipelineRunner does, and validates the final MP4
with ffprobe (resolution / FPS / duration / codec / audio preserved).

Run from anywhere:

    python worker/tests/integration/e2e_pipeline.py [--duration 30] [--stt-model small]

Environment requirements (all optional with clear skips):
- FFmpeg: `vendor/ffmpeg/ffmpeg.exe` (repo) or `FFMPEG_BIN`/PATH.
- STT model: cached in ~/.cache/huggingface (tiny/small/turbo already present).
- Speech: edge-tts (network) or Windows SAPI (offline PowerShell fallback).
- TTS dubbing: edge-tts (network) — skipped cleanly when unavailable.

Every number printed is measured, never fabricated. Exit code 0 = full
pipeline produced a validated MP4.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parents[2]
REPO = WORKER_DIR.parent

FFMPEG_CANDIDATES = [
    REPO / "vendor" / "ffmpeg" / "ffmpeg.exe",
    REPO / "vendor" / "ffmpeg" / "ffmpeg",
    Path(os.environ.get("FFMPEG_BIN", "")),
]
FFPROBE_CANDIDATES = [
    REPO / "vendor" / "ffmpeg" / "ffprobe.exe",
    REPO / "vendor" / "ffmpeg" / "ffprobe",
]

TOKEN = "e2e-test-token"
HOST = "127.0.0.1"

# Script spoken in the fixture (English so STT is deterministic enough).
SPEECH_LINES = [
    "Hello and welcome to our channel.",
    "Today we are testing the full video localization pipeline.",
    "The worker extracts audio, transcribes speech, translates and renders.",
    "This sentence verifies that subtitles are burned into the final video.",
    "Thank you for watching and see you in the next video.",
]


def find_ffmpeg() -> Path:
    for candidate in FFMPEG_CANDIDATES:
        if candidate.is_file():
            return candidate
    found = shutil.which("ffmpeg")
    if found:
        return Path(found)
    raise SystemExit("ffmpeg not found (vendor/ffmpeg or FFMPEG_BIN or PATH)")


def find_ffprobe() -> Path:
    for candidate in FFPROBE_CANDIDATES:
        if candidate.is_file():
            return candidate
    found = shutil.which("ffprobe")
    if found:
        return Path(found)
    raise SystemExit("ffprobe not found")


def run(args: list[str], *, cwd=None, env=None, timeout=600) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, cwd=cwd, env=env, timeout=timeout)


# ---- speech synthesis ------------------------------------------------------


def synthesize_edge(text: str, out_wav: Path) -> bool:
    """edge-tts (needs network). Returns False when it fails."""
    try:
        import edge_tts  # type: ignore

        mp3 = out_wav.with_suffix(".mp3")

        async def _synth() -> None:
            await edge_tts.Communicate(text, "en-US-AriaNeural").save(str(mp3))

        asyncio.run(_synth())
        ffmpeg = find_ffmpeg()
        proc = run(
            [
                str(ffmpeg), "-y", "-nostdin", "-i", str(mp3),
                "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out_wav),
            ]
        )
        mp3.unlink(missing_ok=True)
        return proc.returncode == 0 and out_wav.is_file() and out_wav.stat().st_size > 0
    except Exception:
        return False


def synthesize_sapi(text: str, out_wav: Path) -> bool:
    """Windows SAPI via PowerShell (fully offline)."""
    script = (
        "Add-Type -AssemblyName System.Speech; "
        f"$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.SetOutputToWaveFile('{out_wav}'); "
        f"$s.Speak('{text.replace(chr(39), chr(39) * 2)}'); "
        "$s.Dispose()"
    )
    try:
        proc = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], timeout=120)
        return proc.returncode == 0 and out_wav.is_file() and out_wav.stat().st_size > 0
    except Exception:
        return False


def synthesize_speech(out_wav: Path) -> tuple[str, float]:
    """One continuous speech WAV. Returns (engine_used, duration_s)."""
    text = " ".join(SPEECH_LINES)
    if synthesize_edge(text, out_wav):
        engine = "edge-tts"
    elif sys.platform == "win32" and synthesize_sapi(text, out_wav):
        engine = "windows-sapi"
    else:
        raise SystemExit("no speech synthesizer available (edge-tts or SAPI)")
    duration = _wav_duration(out_wav)
    return engine, duration


def _wav_duration(path: Path) -> float:
    import wave

    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


# ---- fixture ---------------------------------------------------------------


def build_fixture(workdir: Path, speech_wav: Path, target_duration: float) -> Path:
    """640x360@25fps H.264 + AAC video with the speech repeated to fit duration."""
    ffmpeg = find_ffmpeg()
    video = workdir / "fixture.mp4"
    speech = speech_wav

    # Repeat the speech until it covers the target duration, then pad/trim.
    speech_dur = _wav_duration(speech_wav)
    repeats = max(1, int(target_duration / max(0.1, speech_dur)) + 1)
    concat = workdir / "speech_concat.txt"
    with open(concat, "w", encoding="utf-8") as f:
        for _ in range(repeats):
            f.write(f"file '{speech}'\n")
    speech_full = workdir / "speech_full.wav"
    proc = run(
        [
            str(ffmpeg), "-y", "-nostdin", "-f", "concat", "-safe", "0", "-i", str(concat),
            "-t", f"{target_duration:.2f}", "-c:a", "pcm_s16le", str(speech_full),
        ]
    )
    if proc.returncode != 0:
        raise SystemExit(f"concat failed: {proc.stderr[-400:]}")

    # testsrc2 video + the speech audio (with a quiet sine bed so render QC's
    # burn-in comparison has motion to cancel).
    proc = run(
        [
            str(ffmpeg), "-y", "-nostdin",
            "-f", "lavfi", "-i", f"testsrc2=duration={target_duration:.2f}:size=640x360:rate=25",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={target_duration:.2f}:sample_rate=44100",
            "-i", str(speech_full),
            "-filter_complex",
            "[1:a]volume=0.15[bed];[2:a]adelay=200:all=1[sp];[bed][sp]amix=inputs=2:duration=first[aud]",
            "-map", "0:v", "-map", "[aud]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(video),
        ]
    )
    if proc.returncode != 0 or not video.is_file():
        raise SystemExit(f"fixture build failed: {proc.stderr[-400:]}")
    return video


# ---- worker ----------------------------------------------------------------


def start_worker(port: int, log_path: Path) -> subprocess.Popen:
    env = os.environ.copy()
    env["WORKER_AUTH_TOKEN"] = TOKEN
    env["FFMPEG_BIN"] = str(find_ffmpeg())
    vendor_bin = str(REPO / "vendor" / "ffmpeg")
    env["PATH"] = vendor_bin + os.pathsep + env.get("PATH", "")
    logf = open(log_path, "w+", encoding="utf-8")
    # stdin=DEVNULL: the worker's sidecar handshake reads the token from stdin,
    # and with a non-TTY (piped) stdin it would block forever waiting for a
    # line. In this E2E the token comes from the WORKER_AUTH_TOKEN env instead.
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "src.main", "--port", str(port)],
        cwd=str(WORKER_DIR),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=logf,
        stderr=subprocess.STDOUT,
    )
    deadline = time.monotonic() + 90
    last_err = None
    attempts = 0
    while time.monotonic() < deadline:
        attempts += 1
        if proc.poll() is not None:
            raise SystemExit(f"worker exited early (code {proc.returncode}): {Path(log_path).read_text(encoding='utf-8', errors='replace').splitlines()[-6:]}")
        try:
            http(port, "GET", "/health")
            return proc
        except Exception as exc:  # noqa: BLE001 - poll loop
            last_err = exc
            if attempts <= 3 or attempts % 20 == 0:
                print(f"  [worker-poll #{attempts}] {type(exc).__name__}: {exc}", flush=True)
            time.sleep(0.5)
    logf.flush()
    disk_log = Path(log_path).read_text(encoding="utf-8", errors="replace")
    raise SystemExit(f"worker did not become healthy in 90s ({last_err}); log:\n{'\n'.join(disk_log.splitlines()[-10:])}")


def http(port: int, method: str, path: str, body: dict | None = None) -> dict:
    url = f"http://{HOST}:{port}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=1800) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"raw": raw[:400]}
        raise RuntimeError(f"HTTP {exc.code} {path}: {payload}") from None


# ---- validation ------------------------------------------------------------


def probe(ffprobe: Path, path: Path) -> dict:
    proc = run(
        [
            str(ffprobe), "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ]
    )
    if proc.returncode != 0:
        raise SystemExit(f"ffprobe failed: {proc.stderr[:400]}")
    return json.loads(proc.stdout)


def validate_output(ffprobe: Path, output: Path, source_duration: float) -> dict:
    doc = probe(ffprobe, output)
    streams = doc.get("streams", [])
    fmt = doc.get("format", {})
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fps = None
    if video and video.get("r_frame_rate"):
        num, _, den = video["r_frame_rate"].partition("/")
        fps = float(num) / float(den) if den and float(den) > 0 else float(num)
    result = {
        "container": fmt.get("format_name"),
        "duration": float(fmt.get("duration", 0) or 0),
        "width": video.get("width") if video else None,
        "height": video.get("height") if video else None,
        "fps": round(fps, 3) if fps else None,
        "video_codec": video.get("codec_name") if video else None,
        "audio_codec": audio.get("codec_name") if audio else None,
        "audio_channels": audio.get("channels") if audio else None,
        "size_bytes": output.stat().st_size,
    }
    issues = []
    if video is None:
        issues.append("no video stream")
    if audio is None:
        issues.append("no audio stream")
    if result["width"] != 640 or result["height"] != 360:
        issues.append("resolution changed")
    if result["fps"] is not None and abs(result["fps"] - 25.0) > 0.25:
        issues.append(f"fps {result['fps']} != 25")
    if abs(result["duration"] - source_duration) > 1.5:
        issues.append(f"duration {result['duration']:.2f}s vs source {source_duration:.2f}s")
    if result["video_codec"] not in ("h264", "hevc", "av1"):
        issues.append(f"unexpected video codec {result['video_codec']}")
    result["issues"] = issues
    return result


# ---- deep QC (frame luminance, audio loudness) ------------------------------


def average_luma(ffmpeg: Path, path: Path, samples: int = 6) -> dict:
    """Sample ``samples`` evenly-spaced frames and return their mean luma (YAVG).

    A rendered video that opens but is all-black scores ~0; the testsrc2
    fixture + burned subtitles score well above 40. Returns raw data as
    measured, never fabricated.
    """
    doc = json.loads(
        run([str(find_ffprobe()), "-v", "error", "-print_format", "json",
             "-show_format", str(path)]).stdout
    )
    total = float(doc.get("format", {}).get("duration", 0) or 0)
    if total <= 0:
        return {"ok": False, "reason": "could not read duration"}
    step = max(0.1, total / (samples + 1))
    ys: list[float] = []
    for i in range(samples):
        t = round(step * (i + 1), 3)
        proc = run(
            [
                str(ffmpeg), "-nostdin", "-ss", f"{t}", "-i", str(path),
                "-frames:v", "1", "-vf",
                "signalstats,metadata=print:file=-", "-f", "null", "-",
            ]
        )
        for stream in (proc.stdout or "", proc.stderr or ""):
            for line in stream.splitlines():
                if "YAVG" in line:
                    try:
                        ys.append(float(line.split("=", 1)[1].strip()))
                    except ValueError:
                        pass
    if not ys:
        return {"ok": False, "reason": "no YAVG samples decoded"}
    mean = sum(ys) / len(ys)
    return {"ok": mean > 8.0, "mean_luma": round(mean, 2), "min_luma": round(min(ys), 2), "samples": len(ys)}


def audio_loudness(ffmpeg: Path, path: Path) -> dict:
    """volumedetect on the output's audio stream: is there actually sound?"""
    proc = run(
        [
            str(ffmpeg), "-nostdin", "-i", str(path), "-map", "0:a:0",
            "-af", "volumedetect", "-f", "null", "-",
        ]
    )
    out = (proc.stderr or "") + (proc.stdout or "")
    mean_vol = max_vol = None
    for line in out.splitlines():
        if "mean_volume" in line:
            mean_vol = line.split(":", 1)[1].strip().split(" ")[0]
        if "max_volume" in line:
            max_vol = line.split(":", 1)[1].strip().split(" ")[0]
    def _db(s: str | None) -> float | None:
        try:
            return float(s) if s is not None else None
        except ValueError:
            return None
    mm, mx = _db(mean_vol), _db(max_vol)
    return {
        "ok": mx is not None and mx > -40.0,
        "mean_volume_db": mm,
        "max_volume_db": mx,
    }


class MetricSampler:
    """Samples worker-process CPU/RSS (psutil) and GPU util/VRAM (nvidia-smi)
    every ``interval`` seconds; call ``close`` to collect peaks.

    Driver-side only — no worker code touched. Missing psutil / nvidia-smi
    degrade gracefully to ``None``.
    """

    def __init__(self, worker_pid: int | None, interval: float = 2.0):
        self.interval = interval
        self._proc = None
        try:
            import psutil  # type: ignore

            self._psutil = psutil
            if worker_pid:
                try:
                    self._proc = psutil.Process(worker_pid)
                except psutil.Error:
                    self._proc = None
        except Exception:  # noqa: BLE001 - optional dependency
            self._psutil = None
        self._samples: list[dict] = []
        self._running = False

    def start(self) -> "MetricSampler":
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def _loop(self) -> None:
        util_path = shutil.which("nvidia-smi")
        while self._running:
            sample: dict = {}
            if self._psutil is not None and self._proc is not None:
                try:
                    procs = [self._proc] + self._proc.children(recursive=True)
                    cpus = sum(p.cpu_percent(None) for p in procs)
                    rss = sum(p.memory_info().rss for p in procs)
                    sample["cpu_percent"] = round(cpus, 1)
                    sample["rss_mb"] = round(rss / (1024 * 1024), 1)
                except Exception:  # noqa: BLE001 - process may exit mid-run
                    pass
            if util_path:
                q = run(
                    [
                        util_path, "--query-gpu=utilization.gpu,memory.used",
                        "--format=csv,noheader,nounits",
                    ],
                    timeout=10,
                )
                vals = (q.stdout or "").strip().splitlines()
                if vals:
                    parts = [p.strip() for p in vals[0].split(",")]
                    sample["gpu_percent"] = float(parts[0])
                    sample["vram_mb"] = float(parts[1])
            if sample:
                self._samples.append(sample)
            time.sleep(self.interval)

    def close(self) -> dict:
        self._running = False
        try:
            self._thread.join(timeout=self.interval + 2)
        except Exception:  # noqa: BLE001
            pass
        if not self._samples:
            return {}
        return {
            "cpu_peak_percent": max(s.get("cpu_percent", 0) for s in self._samples),
            "ram_peak_mb": max(s.get("rss_mb", 0) for s in self._samples),
            "gpu_peak_percent": max((s.get("gpu_percent") for s in self._samples if "gpu_percent" in s), default=None),
            "vram_peak_mb": max((s.get("vram_mb") for s in self._samples if "vram_mb" in s), default=None),
            "samples": len(self._samples),
        }


# ---- main ------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, default=30.0, help="target fixture seconds")
    parser.add_argument("--stt-model", default="small", help="faster-whisper model (cached: tiny/small/turbo)")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--dub", action="store_true", help="also run the TTS dubbing stage")
    parser.add_argument("--watermark", action="store_true", help="burn a text watermark too")
    parser.add_argument("--watermark-image", default=None, help="burn an IMAGE watermark (extra ffmpeg input — exercises the audio-map fix)")
    parser.add_argument("--provider", default="mock", choices=["mock", "gemini"], help="translation provider (gemini reads GEMINI_API_KEY env, never logs it)")
    parser.add_argument("--port", type=int, default=8799)
    args = parser.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY") if args.provider == "gemini" else None
    if args.provider == "gemini" and not api_key:
        raise SystemExit("GEMINI_API_KEY env is required for --provider gemini")

    workdir = Path(tempfile.mkdtemp(prefix="tc_e2e_"))
    out_dir = workdir / "out"
    out_dir.mkdir(exist_ok=True)
    ffmpeg = find_ffmpeg()
    ffprobe = find_ffprobe()

    report: dict = {
        "fixture_duration_s": args.duration,
        "stt_model": args.stt_model,
        "translation_provider": args.provider,
        "stages": {},
        "output": None,
    }
    t_start = time.monotonic()

    # 1) Speech + fixture
    t0 = time.monotonic()
    speech = workdir / "speech.wav"
    engine, _ = synthesize_speech(speech)
    report["speech_engine"] = engine
    video = build_fixture(workdir, speech, args.duration)
    report["stages"]["fixture_build"] = {"seconds": round(time.monotonic() - t0, 2)}

    # 2) Worker
    t0 = time.monotonic()
    worker = start_worker(args.port, workdir / "worker.log")
    report["stages"]["worker_start"] = {"seconds": round(time.monotonic() - t0, 2)}
    try:
        # 3) Extract audio
        audio = out_dir / "audio.wav"
        t0 = time.monotonic()
        extract = http(args.port, "POST", "/v1/audio/extract", {
            "video_path": str(video), "output_path": str(audio), "job_id": "e2e-extract",
        })
        report["stages"]["extract_audio"] = {
            "seconds": round(time.monotonic() - t0, 2),
            "duration_seconds": extract.get("duration_seconds"),
            "file_size_bytes": extract.get("file_size_bytes"),
        }

        # 4) STT
        t0 = time.monotonic()
        transcript = http(args.port, "POST", "/v1/stt/transcribe", {
            "audio_path": str(audio), "project_id": "e2e", "model": args.stt_model,
            "device": args.device, "language": "en",
            "total_duration_seconds": extract.get("duration_seconds"),
            "job_id": "e2e-stt",
        })
        n_segments = len(transcript.get("segments", []))
        report["stages"]["stt"] = {
            "seconds": round(time.monotonic() - t0, 2),
            "segments": n_segments,
        }

        # 5) Translate (real provider — mock offline or gemini cloud with the
        #    key from GEMINI_API_KEY; the key is sent to the loopback worker
        #    exactly like the app does and is never logged/printed).
        t0 = time.monotonic()
        translation = http(args.port, "POST", "/v1/translate", {
            "transcript": transcript, "project_id": "e2e", "provider": args.provider,
            "target_language": "vi",
            "model": "gemini-flash-lite-latest" if args.provider == "gemini" else "mock",
            "api_key": api_key,
            "job_id": "e2e-translate",
        })
        report["stages"]["translate"] = {
            "seconds": round(time.monotonic() - t0, 2),
            "blocks": len(translation.get("blocks", [])),
        }

        # 6) Subtitle
        t0 = time.monotonic()
        sub = http(args.port, "POST", "/v1/subtitle", {
            "transcript": transcript, "translation": translation,
            "project_id": "e2e", "output_dir": str(out_dir), "language": "vi",
            "job_id": "e2e-subtitle",
        })
        n_cues = len(sub.get("cues", []))
        report["stages"]["subtitle"] = {
            "seconds": round(time.monotonic() - t0, 2),
            "cues": n_cues,
            "srt": Path(sub.get("srt_path", "")).is_file(),
            "ass": Path(sub.get("ass_path", "")).is_file(),
        }

        # 7) TTS dubbing (optional)
        voice_track = None
        if args.dub and n_cues > 0:
            t0 = time.monotonic()
            try:
                tts = http(args.port, "POST", "/v1/tts/synthesize", {
                    "cues": [
                        {"start": c["start"], "end": c["end"], "text": c["text"]}
                        for c in sub["cues"]
                    ],
                    "voice": "en-US-AriaNeural", "engine": "edge",
                    "language": "en", "duration_seconds": extract.get("duration_seconds"),
                    "output_dir": str(out_dir), "job_id": "e2e-tts",
                })
                voice_track = tts.get("voice_track_path")
                report["stages"]["tts"] = {
                    "seconds": round(time.monotonic() - t0, 2),
                    "voice_track_exists": bool(voice_track and Path(voice_track).is_file()),
                    "engine_used": tts.get("engine_used"),
                }
            except RuntimeError as exc:
                report["stages"]["tts"] = {"seconds": round(time.monotonic() - t0, 2), "skipped": str(exc)[:200]}

        # 8) Render (burn-in + optional watermark/voice mix)
        output = out_dir / "rendered.mp4"
        check_window = None
        if n_cues > 0 and sub["cues"]:
            longest = max(sub["cues"], key=lambda c: c["end"] - c["start"])
            check_window = [longest["start"], longest["end"]]
        t0 = time.monotonic()
        render_req = {
            "video_path": str(video),
            "subtitle_path": sub.get("ass_path"),
            "output_path": str(output),
            "preset": "veryfast",
            "check_window": check_window,
            "job_id": "e2e-render",
        }
        if args.watermark:
            render_req["watermark"] = {
                "text": {
                    "text": "E2E TEST", "position": "top-left", "margin": 12,
                    "font_size": 28, "color": "#FFFFFFFF", "opacity": 1.0,
                }
            }
        if args.watermark_image:
            # Image watermark = a SECOND extra input AFTER the optional voice
            # track; the renderer must still map the replacement audio (input
            # 1), never the image (input 2). FIX #2 regression evidence.
            render_req["watermark"] = {
                "image": {
                    "image_path": args.watermark_image, "position": "top-left",
                    "margin": 12, "width": 120, "opacity": 1.0,
                }
            }
        if voice_track:
            render_req["voice_track_path"] = voice_track
        render = http(args.port, "POST", "/v1/render", render_req)
        report["stages"]["render"] = {
            "seconds": round(time.monotonic() - t0, 2),
            "encoder_used": render.get("encoder_used"),
            "reported_duration": render.get("duration_seconds"),
        }

        # 9) ffprobe validation
        t0 = time.monotonic()
        validation = validate_output(ffprobe, output, args.duration)
        validation["probe_seconds"] = round(time.monotonic() - t0, 2)
        report["output"] = validation
        report["total_seconds"] = round(time.monotonic() - t_start, 2)
        report["workdir"] = str(workdir)
        report["final_mp4"] = str(output)

        print(json.dumps(report, indent=2, ensure_ascii=False))
        print(f"\nEVIDENCE_DIR={workdir}")
        print(f"FINAL_MP4={output}")
        if validation.get("issues"):
            print("VALIDATION ISSUES:", "; ".join(validation["issues"]))
            return 2
        print("VALIDATION: PASS")
        return 0
    finally:
        worker.terminate()
        try:
            worker.wait(timeout=10)
        except Exception:
            worker.kill()


if __name__ == "__main__":
    sys.exit(main())
