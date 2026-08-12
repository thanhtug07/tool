# Model Checker

Checks whether every model the app needs is present on this machine. It **never downloads anything** — you install models manually (see `models/MODEL_DOWNLOAD_COMMANDS.md`).

## Usage

```bash
# Standard check (core pipeline models only)
python scripts/models/check_models.py

# Also show future-stage models (TTS / separation / OCR — stages not implemented)
python scripts/models/check_models.py --all

# Show resolved paths for OK entries too
python scripts/models/check_models.py --verbose
```

On this machine, use `py` instead of `python` (the Windows launcher):

```bash
py scripts/models/check_models.py
```

## Output

```
[OK] faster-whisper-large-v3 (STT)
[OK] faster-whisper-turbo (STT)
[OK] faster-whisper-tiny (STT)
[MISSING] faster-whisper-small (STT)
...
```

- `[OK]` — model file found and looks valid (a `.bin` exists in the HF cache dir).
- `[MISSING]` — required for the **current** pipeline and not found. Exit code becomes **1**.
- `[PENDING]` — stage not implemented yet (TTS / audio separation / OCR). Not required today; hidden unless `--all`.
- `[N/A]` — cloud/API entry (Gemini, local LLM server) — no file to check.

## What counts as "required"

Only **category A** (core automation): a faster-whisper model (any tier — `large-v3`, `turbo`, `small`, `base`, or `tiny` satisfy STT) plus a translation path. Mock translation works offline with zero keys, so the only hard requirement is an STT model.

The environment variables honored: `HF_HUB_CACHE`, `HF_HOME`, `TORCH_HOME`, `USERPROFILE`/`HOME`, and `REPO_ROOT`-relative paths under `models/`.

## Notes

- faster-whisper downloads its model into the **Hugging Face cache** automatically on first use — you do not need to place anything manually if you let the first run download it (it will, ~3.1 GB for `large-v3`). The checker just reports whether that already happened.
- The repo's `models/manifest.json` is a separate, unused model-registry layer (not wired into the pipeline yet). This checker uses `scripts/models/model_manifest.json` instead.
