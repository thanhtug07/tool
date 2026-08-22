import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(),
}));

import { invoke } from "@tauri-apps/api/core";
import {
  deleteApiKey,
  getApiKeyMasked,
  getSettings,
  setApiKey,
  setSetting,
  type SettingsSnapshot,
} from "./settings";

const mockedInvoke = vi.mocked(invoke);

const SNAPSHOT: SettingsSnapshot = {
  "ai.model": "large-v3",
  "ai.device": "auto",
  "ai.preset": "balanced",
  "gpu.override": "auto",
  "api.gemini.base_url": "",
  "api.gemini.model": "gemini-flash-lite-latest",
  "api.local.base_url": "http://127.0.0.1:8080",
  "cache.quota_bytes": 10737418240,
  "privacy.mode": "local",
  "privacy.telemetry": false,
  "tts.engine": "edge",
  "tts.voice": "vi-VN-HoaiMyNeural",
  "automation.orchestrator_v2": false,
};

describe("settings bridge (unit — mocked invoke)", () => {
  beforeEach(() => {
    mockedInvoke.mockReset();
  });

  it("stores an API key in the credential vault", async () => {
    mockedInvoke.mockResolvedValue(null);
    await setApiKey("gemini", "AIzaSy-secret-key-1234"); // gitleaks:allow - test fixture, not a real key
    expect(mockedInvoke).toHaveBeenCalledWith("secrets.set_api_key", {
      provider: "gemini",
      key: "AIzaSy-secret-key-1234", // gitleaks:allow - test fixture, not a real key
    });
  });

  it("reads a masked key (full secret never crosses IPC)", async () => {
    mockedInvoke.mockResolvedValue("AIz****wxyz");
    const masked = await getApiKeyMasked("gemini");
    expect(mockedInvoke).toHaveBeenCalledWith("secrets.get_api_key_masked", {
      provider: "gemini",
    });
    expect(masked).toBe("AIz****wxyz");
  });

  it("reports no stored key as null", async () => {
    mockedInvoke.mockResolvedValue(null);
    expect(await getApiKeyMasked("local")).toBeNull();
  });

  it("deletes a stored key", async () => {
    mockedInvoke.mockResolvedValue(null);
    await deleteApiKey("gemini");
    expect(mockedInvoke).toHaveBeenCalledWith("secrets.delete_api_key", {
      provider: "gemini",
    });
  });

  it("reads the settings snapshot", async () => {
    mockedInvoke.mockResolvedValue(SNAPSHOT);
    const settings = await getSettings();
    expect(mockedInvoke).toHaveBeenCalledWith("settings.get_all");
    expect(settings["ai.device"]).toBe("auto");
    expect(settings["cache.quota_bytes"]).toBe(10737418240);
  });

  it("persists a validated setting and returns the snapshot", async () => {
    mockedInvoke.mockResolvedValue({ ...SNAPSHOT, "ai.device": "cuda" });
    const updated = await setSetting("ai.device", "cuda");
    expect(mockedInvoke).toHaveBeenCalledWith("settings.set", {
      key: "ai.device",
      value: "cuda",
    });
    expect(updated["ai.device"]).toBe("cuda");
  });
});
