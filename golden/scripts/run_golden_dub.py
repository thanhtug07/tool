"""Golden E2E — dubbing (TTS voice track + render mix) proof.

Extends the vanilla golden runner with the TTS stage on the same short
synthetic golden video:

    golden.mp4 → extract → STT → translate → TTS synthesize (edge-tts)
    → render with voice_track → export/QC

Checks:
 1. voice_track.wav exists, is non-silent, and covers the full video duration
    (proves TTS produced audible speech aligned to the translated cues)
 2. render output keeps video + audio streams and source duration
 3. output audio differs from source audio (PSNR filter → finite dB), i.e.
    the dubbed voice is actually mixed into the final file — not just a
    subtitle/burn-in run
 4. export QC passes

Exit code 0 only when every check passes.
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_golden import (  # noqa: E402  (reuse worker lifecycle + HTTP helpers)
    GOLDEN,
    check,
    expect_ok,
    ffprobe_duration,
    ffprobe_streams,
    http_post,
    shutdown_worker,
    spawn_worker,
    warm_stt_model,
)

def ffprobe_volume(path: Path) -> float:
    """Return mean volume (dB) of the first audio stream."""
    vol = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        check=True, capture_output=True, text=True,
    )
    for line in vol.stderr.splitlines():
        if "mean_volume" in line:
            return float(line.split("mean_volume:")[1].strip().split(" ")[0])
    raise AssertionError(f"volumedetect produced no mean_volume for {path}")


def audio_diff_ratio(a: Path, b: Path) -> float:
    """Relative RMS difference between two audio files (0 = identical).

    Both are resampled to 16 kHz mono PCM and compared sample-by-sample;
    ``diff_rms / src_rms`` is returned. A mixed-in voice track yields a large
    ratio, an untouched passthrough ~0.
    """
    import array

    pcm_a = a.with_suffix(".pcm")
    pcm_b = b.with_suffix(".b.pcm")
    try:
        for src, dst in ((a, pcm_a), (b, pcm_b)):
            subprocess.run(
                ["ffmpeg", "-v", "error", "-i", str(src), "-ar", "16000", "-ac", "1",
                 "-f", "s16le", "-y", str(dst)],
                check=True, capture_output=True, text=True,
            )
        sa = array.array("h")
        sb = array.array("h")
        sa.frombytes(pcm_a.read_bytes())
        sb.frombytes(pcm_b.read_bytes())
        n = min(len(sa), len(sb))
        if n == 0:
            raise AssertionError("no PCM samples to compare")
        src_rms = (sum(x * x for x in sa[:n]) / n) ** 0.5
        diff_rms = (sum((sa[i] - sb[i]) ** 2 for i in range(n)) / n) ** 0.5
        if src_rms == 0:
            return 0.0
        return diff_rms / src_rms
    finally:
        pcm_a.unlink(missing_ok=True)
        pcm_b.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Golden E2E — dubbing proof")
    parser.add_argument("--model", default="tiny")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--voice", default="zh-CN-YunxiNeural")
    parser.add_argument("--engine", default="edge")
    parser.add_argument("--language", default="en")
    parser.add_argument("--target-language", default="zh")
    args = parser.parse_args()

    video = GOLDEN / "video" / "golden.mp4"
    assert video.is_file(), f"golden video missing — run generate_golden.py first: {video}"
    expected = json.loads((GOLDEN / "expected" / "expected.json").read_text(encoding="utf-8"))
    expected_dur = expected["video"]["duration_seconds"]
    work = GOLDEN / "results" / "dub"
    work.mkdir(parents=True, exist_ok=True)

    warm_stt_model(args.model, args.device)
    started = time.time()
    print(f"golden dub E2E start: engine={args.engine} voice={args.voice} video={video}")
    proc, port, token, _drain = spawn_worker(None)
    try:
        # -- stage 1: audio extract -----------------------------------------
        print("stage 1/7: audio extract")
        wav = work / "audio.wav"
        expect_ok(*http_post(port, token, "/v1/audio/extract", {
            "video_path": str(video), "output_path": str(wav), "job_id": "dub-extract",
        }), "extract")
        check(wav.is_file() and wav.stat().st_size > 0, "extract produced audio", str(wav))

        # -- stage 2: real STT ----------------------------------------------
        print("stage 2/7: STT (faster-whisper)")
        transcript = expect_ok(*http_post(port, token, "/v1/stt/transcribe", {
            "audio_path": str(wav), "project_id": "dub-e2e", "model": args.model,
            "device": args.device, "language": args.language, "job_id": "dub-stt",
        }), "stt")
        check(len(transcript["segments"]) >= 1, "stt produced segments",
              f"{len(transcript['segments'])} segs")

        # -- stage 3: translate ---------------------------------------------
        print("stage 3/7: translate (mock, offline)")
        translation = expect_ok(*http_post(port, token, "/v1/translate", {
            "transcript": transcript, "project_id": "dub-e2e", "provider": "mock",
            "target_language": args.target_language, "model": "gemini-2.5-flash-lite",
            "job_id": "dub-translate",
        }), "translate")
        items = [t for b in translation["blocks"] for t in b["translations"]]
        check(len(items) == len(transcript["segments"]), "translate covered every segment",
              f"{len(items)} items")

        # -- stage 4: TTS synthesize ----------------------------------------
        print(f"stage 4/7: TTS synthesize (engine={args.engine})")
        # Translation items carry no timing; the Rust runner zips them with
        # transcript segments by index — mirror that here.
        cues = [
            {"start": seg["start"], "end": seg["end"], "text": t["translated_text"]}
            for seg, t in zip(transcript["segments"], items)
        ]
        tts_start = time.time()
        tts = expect_ok(*http_post(port, token, "/v1/tts/synthesize", {
            "cues": cues, "voice": args.voice, "engine": args.engine,
            "language": args.target_language, "duration_seconds": expected_dur,
            "output_dir": str(work), "job_id": "dub-tts",
        }), "tts")
        tts_s = time.time() - tts_start
        track = Path(tts["voice_track_path"])
        check(track.is_file() and track.stat().st_size > 0, "tts produced voice track",
              f"{tts['engine_used']} / {tts['voice_used']} in {tts_s:.1f}s, {track.stat().st_size} bytes")
        track_dur = ffprobe_duration(track)
        check(abs(track_dur - expected_dur) < 1.5, "voice track covers full duration",
              f"{track_dur:.2f}s vs {expected_dur:.2f}s")
        track_vol = ffprobe_volume(track)
        check(track_vol > -45.0, "voice track is audible (has speech)",
              f"mean volume {track_vol:.1f} dB")

        # -- stage 5: render with voice track --------------------------------
        print("stage 5/7: render (subtitles + voice mix)")
        out = work / "rendered_dub.mp4"
        expect_ok(*http_post(port, token, "/v1/render", {
            "video_path": str(video), "output_path": str(out), "job_id": "dub-render",
            "voice_track_path": str(track),
        }), "render")
        check(out.is_file() and out.stat().st_size > 0, "render produced output", str(out))
        streams = ffprobe_streams(out)
        check(any(s.startswith("video:") for s in streams) and any(s.startswith("audio:") for s in streams),
              "render output has video + audio streams", ", ".join(streams))
        out_dur = ffprobe_duration(out)
        check(abs(out_dur - expected_dur) < 1.5, "render duration ~ source",
              f"{out_dur:.2f}s vs {expected_dur:.2f}s")

        # -- stage 6: dubbing actually mixed in ------------------------------
        print("stage 6/7: verify voice is mixed into the output audio")
        ratio = audio_diff_ratio(out, video)
        check(ratio > 0.15, "output audio differs from source (voice mixed in)",
              f"relative RMS difference {ratio:.2f}")
        # Extract the mixed audio once more — it must also contain speech.
        mixed_wav = work / "mixed.wav"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(out), "-vn", "-y", str(mixed_wav)],
            check=True, capture_output=True, text=True,
        )
        mixed_vol = ffprobe_volume(mixed_wav)
        check(mixed_vol > -45.0, "mixed output audio is audible",
              f"mean volume {mixed_vol:.1f} dB")

        # -- stage 7: export with QC ----------------------------------------
        print("stage 7/7: export (QC)")
        exported = expect_ok(*http_post(port, token, "/v1/export/video", {
            "source_video": str(out), "target_dir": str(work), "name": "golden-dub-final",
            "run_qc": True,
        }), "export")
        qc = exported["qc"]
        check(Path(exported["path"]).is_file(), "export produced file", exported["path"])
        check(qc["passed"], "export QC passed", f"{len(qc['issues'])} issues")

    finally:
        shutdown_worker(proc)

    import run_golden as rg

    print(f"\ndub golden E2E finished in {time.time() - started:.1f}s")
    failed = [r for r in rg._RESULTS if not r["ok"]]
    for r in rg._RESULTS:
        print(f"  [{'PASS' if r['ok'] else 'FAIL'}] {r['check']} — {r['detail']}")
    print(f"\nRESULT: {len(rg._RESULTS) - len(failed)}/{len(rg._RESULTS)} PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
