"""Golden translation benchmark (QUALITY_BENCHMARK.md §4).

Loads `golden/translation/manifest.json` + `cases/*.json`, translates every
case with the named provider (through the same TranslationService the worker
route uses), and scores the output against the curated expected translations.

- `--provider mock`  → deterministic, no network, no key (CI regression).
- `--provider gemini`/`local` → real provider; `--api-key-env` supplies the
  key via an environment variable (never a CLI argument in plain sight).

Output: `bench_report.json` in the dataset dir (per-case + totals).

Usage:
    py worker/scripts/benchmark_translation.py --provider mock
    py worker/scripts/benchmark_translation.py --provider gemini --api-key-env GEMINI_API_KEY
"""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "worker"))

from src.api.pipeline import build_translation_provider  # noqa: PLC0415
from src.services.providers.base import SourceSegment  # noqa: PLC0415
from src.services.translation_service import TranslationService  # noqa: PLC0415

DATASET = ROOT / "golden" / "translation"


def normalize(text: str) -> str:
    return " ".join(text.strip().lower().split())


def main() -> int:
    parser = argparse.ArgumentParser(description="Golden translation benchmark")
    parser.add_argument("--provider", default="mock")
    parser.add_argument("--model", default="gemini-2.5-flash-lite")
    parser.add_argument("--api-key-env", default=None, help="env var holding the API key")
    parser.add_argument("--out", default=None, help="report path (default: dataset/bench_report.json)")
    args = parser.parse_args()

    manifest = json.loads((DATASET / "manifest.json").read_text(encoding="utf-8"))
    cases = sorted((DATASET / "cases").glob("C0*.json"))

    api_key = None
    if args.api_key_env:
        api_key = os.environ.get(args.api_key_env)
        if not api_key:
            print(f"error: env var {args.api_key_env} is not set (or empty)")
            return 2
    provider = build_translation_provider(args.provider, None, api_key)

    report = {
        "schema_version": 1,
        "provider": args.provider,
        "model": args.model,
        "dataset_version": manifest["version"],
        "cases": [],
        "totals": {},
    }
    per_case = []
    total_segments = 0
    total_ok = 0

    for case_path in cases:
        case = json.loads(case_path.read_text(encoding="utf-8"))
        expected = json.loads(
            (DATASET / "expected" / f"{case['case_id']}.json").read_text(encoding="utf-8")
        )
        exp_by_idx = {e["idx"]: e["expected_text"] for e in expected["segments"]}

        segments = [
            SourceSegment(idx=s["idx"], segment_id=f"seg_{s['idx']}", text=s["text"], speaker=s["speaker"])
            for s in case["segments"]
        ]
        service = TranslationService()
        try:
            blocks = service.translate_segments(
                segments,
                target_language=case["target_lang"],
                provider=provider,
                model=args.model,
                glossary_ver="bench",
                glossary=case.get("glossary"),
            )
        except Exception as exc:  # noqa: BLE001 - report per-case failures
            report["cases"].append({"case_id": case["case_id"], "category": case["category"], "error": str(exc)[:200]})
            per_case.append(0.0)
            continue

        items = {t.segment_id: t.translated_text for b in blocks for t in b.translations}
        ok = 0
        total = len(segments)
        for seg in segments:
            got = items.get(f"seg_{seg.idx}")
            want = exp_by_idx[seg.idx]
            total_segments += 1
            if args.provider == "mock":
                # Deterministic pseudo-translation: `[<lang>] <source>` — the
                # assertion is structural, not semantic.
                match = got is not None and got.startswith(f"[{case['target_lang']}] ")
            else:
                match = got is not None and normalize(got) == normalize(want)
            if match:
                ok += 1
                total_ok += 1
        ratio = ok / total if total else 1.0
        per_case.append(ratio)
        report["cases"].append(
            {
                "case_id": case["case_id"],
                "category": case["category"],
                "segments_total": total,
                "segments_ok": ok,
                "segment_accuracy": round(ratio, 3),
            }
        )

    seg_acc = total_ok / total_segments if total_segments else 0.0
    case_pass = sum(1 for r in per_case if r >= manifest["default_thresholds"]["segment_accuracy"])
    case_ratio = case_pass / len(per_case) if per_case else 0.0
    report["totals"] = {
        "cases_total": len(per_case),
        "cases_pass": case_pass,
        "segments_total": total_segments,
        "segments_ok": total_ok,
        "segment_accuracy": round(seg_acc, 3),
        "case_pass_ratio": round(case_ratio, 3),
        "thresholds": manifest["default_thresholds"],
        "passed": case_ratio >= manifest["default_thresholds"]["case_pass_ratio"],
    }

    out = Path(args.out) if args.out else DATASET / "bench_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["totals"], ensure_ascii=False, indent=2))
    print(f"report: {out}")
    return 0 if report["totals"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
