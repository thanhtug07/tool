"""Generate the golden video fixture (RELEASE-P0-006).

Produces a deterministic, short test video with known speech:

  golden/audio/transcript.wav   — piper-tts synthesis of GOLDEN_TEXT (16 kHz mono)
  golden/video/golden.mp4       — 640x360 25fps test-pattern video + the speech track
  golden/expected/expected.json — the known transcript + expected translation

Determinism:
  - piper-tts is deterministic given the same voice + text (no random noise in
    the ONNX pipeline; default noise scales are fixed).
  - ffmpeg lavfi test-pattern video is deterministic.
  - STT/translation output is *validated with tolerance* (see run_golden.py).

Usage:
  py golden/scripts/generate_golden.py [--voice en_US-lessac-medium]
"""

golden_text = "The quick brown fox jumps over the lazy dog. My phone number is five five five, one two three four."

expected = {
  "schema_version": 1,
  "source": "golden/scripts/generate_golden.py",
  "voice": None,
  "golden_text": golden_text,
  "transcript_contains": ["quick brown fox", "lazy dog", "five five five"],
  "translation": "金色的狐狸跳过懒狗。我的电话号码是五五五，一二三四。",
  "video": {"width": 640, "height": 360, "fps": 25, "duration_seconds": 12.0},
  "min_cues": 2,
}

import argparse, json, os, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
GOLDEN = ROOT / "golden"

def resolve_voice_dir() -> Path:
    candidates = [
        GOLDEN / "voices",
        Path.home() / "AppData" / "Local" / "piper",
        Path.home() / ".local" / "share" / "piper",
        Path.home() / "Library" / "Application Support" / "piper",
        Path(os.environ.get("PIPER_VOICE_DIR", "")),
    ]
    for d in candidates:
        p = d / "en_US-lessac-medium"
        if p.is_dir() and (p / "en_US-lessac-medium.onnx").is_file():
            return p
    raise SystemExit("piper voice en_US-lessac-medium not found; run: py -m piper.download_voices en_US-lessac-medium")

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--voice", default="en_US-lessac-medium")
    parser.add_argument("--text", default=golden_text)
    args = parser.parse_args()

    voice_dir = resolve_voice_dir()
    wav = GOLDEN / "audio" / "transcript.wav"
    GOLDEN.joinpath("audio").mkdir(parents=True, exist_ok=True)

    print(f"synthesizing speech with {args.voice}…")
    subprocess.run(
        [sys.executable, "-m", "piper",
         "-m", str(voice_dir / f"{args.voice}.onnx"),
         "-c", str(voice_dir / f"{args.voice}.onnx.json"),
         "-f", str(wav)],
        input=args.text.encode("utf-8"), check=True, capture_output=True,
    )
    assert wav.is_file() and wav.stat().st_size > 0, "piper produced no audio"

    print("muxing 640x360 25fps test-pattern video…")
    mp4 = GOLDEN / "video" / "golden.mp4"
    GOLDEN.joinpath("video").mkdir(parents=True, exist_ok=True)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(wav)],
        check=True, capture_output=True, text=True,
    )
    duration = float(probe.stdout.strip())
    # Pad the video to the speech length plus a 0.5s tail; pad the audio with
    # silence so both tracks end together (deterministic).
    video_len = duration + 0.5
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"testsrc=size=640x360:rate=25:duration={video_len}",
        "-i", str(wav),
        "-af", f"apad=pad_dur={video_len - duration}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
        "-t", str(video_len), str(mp4),
    ], check=True)

    expected_json = GOLDEN / "expected" / "expected.json"
    expected = {
        "schema_version": 1,
        "source": "golden/scripts/generate_golden.py",
        "voice": args.voice,
        "golden_text": args.text,
        "transcript_contains": ["quick brown fox", "lazy dog", "five five five"],
        "translation": "金色的狐狸跳过懒狗。我的电话号码是五五五，一二三四。",
        "video": {"width": 640, "height": 360, "fps": 25, "duration_seconds": video_len},
        "min_cues": 2,
    }
    expected_json.parent.mkdir(parents=True, exist_ok=True)
    expected_json.write_text(json.dumps(expected, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"golden fixture written:")
    print(f"  audio   {wav}")
    print(f"  video   {mp4}")
    print(f"  expected {expected_json}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
