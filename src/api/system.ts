import { safeInvoke } from "@/api/invoke";

/** GPU vendor inferred from the device name (mirrors Rust `GpuVendor`). */
export type GpuVendor = "nvidia" | "amd" | "intel";

/** Detected machine capabilities (probed once, cached in the Rust core). */
export type HardwareProfile = {
  gpu_vendor: GpuVendor | null;
  gpu_name: string | null;
  vram_mb: number | null;
  ram_mb: number;
  /** Hardware encoder names present in `ffmpeg -encoders` (nvenc/qsv/amf). */
  ffmpeg_encoders: string[];
};

/**
 * Cached machine hardware snapshot (GPU, RAM, FFmpeg encoders). The probe runs
 * once in the Rust core; the payload is static hardware info — never live
 * usage %. There is no backend endpoint for live CPU/GPU/RAM usage.
 */
export function getHardware(): Promise<HardwareProfile> {
  return safeInvoke("system.hardware");
}
