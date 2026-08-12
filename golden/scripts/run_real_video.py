"""Real 48-minute video pipeline runner (FINAL ACCEPTANCE).

Drives the real worker through the full automation vertical slice on the
user's real ~48-minute video (Chinese narration, Bilibili export):

    video → audio extract → real STT (faster-whisper turbo, CUDA) → translate
    (mock, deterministic offline) → subtitle (ASS/SRT) → render (libass
    burn-in, NVENC → libx264 fallback) → export to D:\\Downloads\\New + QC.

Deliberately does NOT run TTS: the TTS stage is not implemented in this build
(voice selection UI is disabled). The original audio is preserved through the
render (-map 0:a?) and QC verifies no track is dropped.

Usage:
    py -3.13 golden/scripts/run_real_video.py
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Windows console/log files default to the ANSI codepage (cp1252), which
# cannot encode the Chinese/Vietnamese text this runner prints. Force UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:  # pragma: no cover - Python < 3.7
    pass

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from run_golden import (  # noqa: E402  (reuse the golden harness helpers)
    _RESULTS,
    check,
    expect_ok,
    ffprobe_duration,
    ffprobe_streams,
    http_post,
    shutdown_worker,
    spawn_worker,
    warm_stt_model,
)

VIDEO = Path(r"D:\Downloads\耗时2个月将聊斋尸变喷水焦螟三篇故事改编成悬疑动画一口气看完_哔哩哔哩_bilibili.mp4")
EXPORT_DIR = Path(r"D:\Downloads\New")
STAGE_TIMEOUT_S = 7200  # 48-min video: STT + render are the long poles.


def main() -> int:
    parser = argparse.ArgumentParser(description="Real 48-min video pipeline runner")
    parser.add_argument("--model", default="turbo")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--provider", default="mock", choices=["mock", "gemini", "local"])
    parser.add_argument("--language", default="zh")
    parser.add_argument("--target-language", default="vi")
    parser.add_argument("--save-results", action="store_true")
    args = parser.parse_args()

    assert VIDEO.is_file(), f"input video missing: {VIDEO}"
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    work = EXPORT_DIR / "_pipeline_work"
    work.mkdir(parents=True, exist_ok=True)

    # STT model: warm first (downloads if missing) so the worker never stalls
    # on a HF network round-trip mid-stage; then run the worker offline.
    try:
        warm_stt_model(args.model, args.device)
    except Exception as exc:  # noqa: BLE001 — CUDA may be unavailable; fall back to CPU
        print(f"WARNING: {args.device} warm failed ({exc}); falling back to cpu")
        args.device = "cpu"
        warm_stt_model(args.model, args.device)

    _RESULTS.clear()
    started = time.time()
    print(f"real-video E2E start: {VIDEO.name}\n  provider={args.provider} "
          f"model={args.model} device={args.device} "
          f"lang={args.language}->{args.target_language}\n  export_dir={EXPORT_DIR}")

    proc, port, token, _drain = spawn_worker(None)
    try:
        # -- stage 1: audio extract -------------------------------------------
        print("\n[1/6] audio extract")
        wav = work / "audio.wav"
        payload = expect_ok(*http_post(port, token, "/v1/audio/extract", {
            "video_path": str(VIDEO),
            "output_path": str(wav),
            "job_id": "real-extract",
        }), "extract")
        src_dur = ffprobe_duration(VIDEO)
        wav_dur = ffprobe_duration(wav)
        check(wav.is_file() and wav.stat().st_size > 0, "extract produced audio", f"{wav.stat().st_size/1e6:.1f} MB")
        check(abs(wav_dur - src_dur) < 2.0, "extract duration ~ video", f"{wav_dur:.1f}s vs {src_dur:.1f}s")

        # -- stage 2: real STT --------------------------------------------------
        print("[2/6] STT (faster-whisper, turbo)")
        stt_t0 = time.time()
        transcript = expect_ok(*http_post(port, token, "/v1/stt/transcribe", {
            "audio_path": str(wav),
            "project_id": "real-e2e",
            "model": args.model,
            "device": args.device,
            "language": args.language,
            "job_id": "real-stt",
        }), "stt")
        stt_s = time.time() - stt_t0
        segs = transcript["segments"]
        (work / "transcript.json").write_text(json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8")
        check(len(segs) > 50, "stt produced many segments", f"{len(segs)} segs in {stt_s:.1f}s")
        sample = " ".join(s["text"] for s in segs[:3])[:120]
        check(all(s.get("text") for s in segs), "stt segments non-empty", f"sample: {sample}")

        # -- stage 3: translate -------------------------------------------------
        print("[3/6] translate")
        translation = expect_ok(*http_post(port, token, "/v1/translate", {
            "transcript": transcript,
            "project_id": "real-e2e",
            "provider": args.provider,
            "target_language": args.target_language,
            "model": "gemini-2.5-flash-lite",
            "job_id": "real-translate",
        }), "translate")
        (work / "translation.json").write_text(json.dumps(translation, ensure_ascii=False, indent=2), encoding="utf-8")
        items = [t for b in translation["blocks"] for t in b["translations"]]
        check(len(items) == len(segs), "translate covered every segment", f"{len(items)} items")
        if args.provider == "mock":
            check(all(it["translated_text"].startswith(f"[{args.target_language}] ") for it in items),
                  "mock translate deterministic prefix", "every item prefixed")
        else:
            check(all(it["translated_text"].strip() for it in items), "real translate non-empty", "all non-empty")

        # -- stage 4: subtitle --------------------------------------------------
        print("[4/6] subtitle")
        subtitle = expect_ok(*http_post(port, token, "/v1/subtitle", {
            "transcript": transcript,
            "translation": translation,
            "project_id": "real-e2e",
            "output_dir": str(work),
            "language": args.language,
            "job_id": "real-subtitle",
        }), "subtitle")
        cues = subtitle["cues"]
        check(len(cues) >= 50, "subtitle produced cues", f"{len(cues)} cues")
        check(all(c["start"] < c["end"] and c["text"].strip() for c in cues), "cue timing/text valid", "all well-formed")
        srt, ass = Path(subtitle["srt_path"]), Path(subtitle["ass_path"])
        check(srt.is_file() and ass.is_file(), "srt+ass written", f"{srt.name} / {ass.name}")

        # -- stage 5: render -----------------------------------------------------
        print("[5/6] render (libass burn-in)")
        render_t0 = time.time()
        rendered = work / "rendered.mp4"
        result = expect_ok(*http_post(port, token, "/v1/render", {
            "video_path": str(VIDEO),
            "subtitle_path": subtitle["ass_path"],
            "output_path": str(rendered),
            "job_id": "real-render",
        }), "render")
        render_s = time.time() - render_t0
        check(rendered.is_file() and rendered.stat().st_size > 0, "render produced output",
              f"{result['encoder_used']} in {render_s:.1f}s, {rendered.stat().st_size/1e6:.1f} MB")
        streams = ffprobe_streams(rendered)
        check(any(s.startswith("video:") for s in streams), "output has video stream", ", ".join(streams))
        check(any(s.startswith("audio:") for s in streams), "output has audio stream (original preserved)", ", ".join(streams))
        out_dur = ffprobe_duration(rendered)
        check(abs(out_dur - src_dur) < 2.0, "render duration ~ source", f"{out_dur:.1f}s vs {src_dur:.1f}s")

        # -- stage 6: export + QC -------------------------------------------------
        print("[6/6] export + QC")
        final_name = "聊斋动画_越南语字幕.mp4"
        exported = expect_ok(*http_post(port, token, "/v1/export/video", {
            "source_video": str(rendered),
            "target_dir": str(EXPORT_DIR),
            "name": "聊斋动画_越南语字幕",
            "run_qc": True,
        }), "export")
        qc = exported["qc"]
        final = EXPORT_DIR / final_name
        check(final.is_file() and final.stat().st_size > 0, "final file in D:\\Downloads\\New",
              f"{final.stat().st_size/1e6:.1f} MB")
        check(qc["passed"], "export QC passed", f"{len(qc['issues'])} issues")
        if not qc["passed"]:
            for issue in qc["issues"]:
                print("   QC issue:", issue)
    finally:
        shutdown_worker(proc)

    total_s = time.time() - started
    results = list(_RESULTS)
    passed = all(r["ok"] for r in results)
    print(f"\nreal-video E2E {'PASS' if passed else 'FAIL'} — "
          f"{sum(1 for r in results if r['ok'])}/{len(results)} checks in {total_s/60:.1f} min")

    if args.save_results:
        report = {
            "schema_version": 1,
            "generated_by": "golden/scripts/run_real_video.py",
            "video": VIDEO.name,
            "provider": args.provider,
            "model": args.model,
            "device": args.device,
            "total_seconds": round(total_s, 1),
            "passed": passed,
            "checks": results,
        }
        (work / "latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
