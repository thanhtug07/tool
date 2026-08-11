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
    }
  ]
}
