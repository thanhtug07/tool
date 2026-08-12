{
  "blockers": [
    {
      "id": "BLOCKER-SIGNING",
      "gate": "code signing (OV cert + signtool + timestamp)",
      "why": "release gate requires a signed installer (SmartScreen pass, MASTER_PLAN Phase 13)",
      "attempted": "tauri build produced unsigned exe/MSI/NSIS artifacts; no signing config exists in the repo",
      "human_decision_required": "provide an OV code-signing certificate + password, or decide to ship unsigned",
      "safe_resume_point": "run `npx tauri build` (artifacts in target/release/bundle/), then sign with signtool"
    },
    {
      "id": "BLOCKER-UPDATER",
      "gate": "updater (plugin + HTTPS endpoint + pubkey + createUpdaterArtifacts)",
      "why": "updater needs tauri-plugin-updater + HTTPS manifest server + pubkey (Phase 14 / T038, post-MVP)",
      "attempted": "none - no plugin/config exists in the repo",
      "human_decision_required": "decide whether to implement the updater (T038) and provide hosting",
      "safe_resume_point": "implement T038, then re-run the release pipeline"
    },
    {
      "id": "BLOCKER-CLEAN-VM",
      "gate": "clean-machine installer smoke test (install/launch/pipeline/uninstall on Win10/11 without Python/Node/Rust/FFmpeg/CUDA)",
      "why": "DoD Tier 1 requires install + clean uninstall on a fresh OS; dev-machine verification from the real installer is done but is not a substitute",
      "attempted": "silent install/launch/installed-worker E2E/uninstall all PASS on the dev machine; portable-run test PASS from %TEMP%",
      "human_decision_required": "provision a clean Windows VM or dedicated test machine for the owner to run the smoke test",
      "safe_resume_point": "install target/release/bundle/nsis/*.exe on the clean machine, run golden E2E, uninstall"
    },
    {
      "id": "BLOCKER-LICENSE",
      "gate": "project license decision + LICENSE file",
      "why": "DoD Tier 3 + MASTER_PLAN 21 require a license decision and file before distribution; AGENTS forbids asserting an unverified license",
      "attempted": "LICENSING.md table + cargo-deny + pip-licenses audits PASS; LICENSE file deliberately NOT created",
      "human_decision_required": "owner chooses the project license; LICENSE file then added and cargo-deny whitelist updated",
      "safe_resume_point": "add LICENSE after the owner decision; no code changes required"
    },
    {
      "id": "BLOCKER-NVENC",
      "gate": "NVIDIA NVENC encode session on a working desktop GPU",
      "why": "product DoD requires NVIDIA path; real CUDA STT validated here, but NVENC returns 'Function not implemented' on this embedded Quadro (driver/GPU-session limitation)",
      "attempted": "verified the mandated NVENC->libx264 fallback works (render 0.7-1.2s, valid output); real STT on CUDA PASS",
      "human_decision_required": "run one render with a working NVENC session on a desktop NVIDIA GPU and record in worker/perf_report.json",
      "safe_resume_point": "worker runner already detects NVENC and falls back automatically; owner runs hardware test"
    },
    {
      "id": "BLOCKER-GEMINI-REAL",
      "gate": "real Gemini translation call (`GEMINI_API_KEY`)",
      "why": "translation service/provider validated via MockProvider + golden benchmark; a real provider call is unproven",
      "attempted": "@pytest.mark.ai live test exists but is skipped without a key",
      "human_decision_required": "provide a GEMINI_API_KEY or accept this verification later at UAT",
      "safe_resume_point": "export GEMINI_API_KEY and run `py -3.13 -m pytest worker/tests/unit/test_gemini_provider.py -m ai`"
    }
  ]
}