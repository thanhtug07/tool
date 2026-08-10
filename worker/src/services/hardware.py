"""Hardware probe + strategy matrix (TASK-014, MASTER_PLAN §14.1-14.2).

Detects the machine's GPU / RAM / FFmpeg encoding capabilities and resolves the
frozen strategy: which device, compute type, STT backend and video encoder the
pipeline should use.

Design
------
- **Lazy heavy imports**: ``torch`` is imported only inside ``detect_gpu`` so
  the module imports cleanly on machines without the AI stack.
- **Frozen matrix (§14.2)**: NVIDIA CUDA -> faster-whisper (int8); Intel iGPU /
  AMD GPU -> whisper.cpp Vulkan (TASK-015); CPU-only -> faster-whisper int8.
  Video encoders: ``h264_nvenc`` / ``h264_qsv`` / ``h264_amf`` / ``libx264``,
  with graceful fallback to ``libx264`` when the hardware encoder is missing.
- **User override (Auto / CUDA / CPU)**: ``device_override`` on the profile; an
  unsupported override (e.g. CUDA requested but torch.cuda unavailable) degrades
  to CPU with a logged warning, never an error.
- **VRAM guard (§14.2)**: the STT model is downgraded to the largest tier that
  fits free VRAM before loading (reuses the TASK-013 guard from
  ``stt_service.guard_model_tier``).

The Rust sidecar owns the authoritative first-boot probe
(``hardware_probe.rs``); this module is the Python view used by the worker
pipeline.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass

from src.services.stt_service import guard_model_tier, pick_compute_type

logger = logging.getLogger(__name__)

GPU_NVIDIA = "nvidia"
GPU_AMD = "amd"
GPU_INTEL = "intel"

#: §14.2 STT backend per GPU vendor (faster-whisper is primary for NVIDIA/CPU).
STT_BACKEND_NVIDIA = "faster-whisper"
STT_BACKEND_WHISPER_CPP = "whisper-cpp"

#: §14.2 video encoder per GPU vendor.
_ENCODER_BY_VENDOR = {
    GPU_NVIDIA: "h264_nvenc",
    GPU_INTEL: "h264_qsv",
    GPU_AMD: "h264_amf",
    None: "libx264",
}

#: Hardware encoders worth advertising from ``ffmpeg -encoders``.
_HW_ENCODER_SUFFIXES = ("nvenc", "qsv", "amf")

#: Vendor keywords matched against GPU names (NVIDIA first, order matters).
_VENDOR_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (GPU_NVIDIA, ("nvidia", "geforce", "quadro", "tesla", "rtx", "gtx", "cuda")),
    (GPU_INTEL, ("intel", "arc")),
    (GPU_AMD, ("amd", "radeon", "rx ")),
)

_VALID_OVERRIDES = ("auto", "cuda", "cpu")


@dataclass(frozen=True)
class HardwareProfile:
    """Detected machine capabilities plus the user's device override."""

    gpu_vendor: str | None = None
    gpu_name: str | None = None
    vram_mb: float | None = None
    ram_mb: float | None = None
    torch_cuda: bool = False
    ffmpeg_encoders: tuple[str, ...] = ()
    device_override: str = "auto"

    @property
    def has_gpu(self) -> bool:
        return self.gpu_vendor is not None


@dataclass(frozen=True)
class Strategy:
    """Resolved pipeline strategy (MASTER_PLAN §14.2)."""

    device: str
    compute_type: str
    stt_backend: str
    whisper_encoder: str
    vulkan: bool


def _infer_vendor(gpu_name: str | None) -> str | None:
    if not gpu_name:
        return None
    lowered = gpu_name.lower()
    for vendor, keywords in _VENDOR_KEYWORDS:
        if any(kw in lowered for kw in keywords):
            return vendor
    return None


def detect_gpu() -> tuple[str | None, str | None, float | None]:
    """Return ``(vendor, name, total_vram_mb)`` via lazy torch (CUDA) or None.

    Falls back to ``nvidia-smi`` when torch is unavailable but an NVIDIA GPU is
    present, so a profile still reports the vendor even without the AI stack.
    """
    try:
        import torch  # noqa: PLC0415 - lazy, heavy

        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            vendor = _infer_vendor(name)
            try:
                _free, total = torch.cuda.mem_get_info(0)
                vram = total / (1024 * 1024)
            except (RuntimeError, TypeError):
                vram = None
            return vendor, name, vram
    except ImportError:
        pass

    smi_vram = _nvidia_smi_vram_mb()
    if smi_vram is not None:
        return GPU_NVIDIA, "NVIDIA GPU (nvidia-smi)", smi_vram
    return None, None, None


def _nvidia_smi_vram_mb() -> float | None:
    """Total VRAM in MB from ``nvidia-smi`` (best-effort, no torch needed)."""
    binary = shutil.which("nvidia-smi")
    if not binary:
        return None
    try:
        completed = subprocess.run(
            [binary, "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip().splitlines()[0].strip() if completed.stdout.strip() else ""
    try:
        return float(value) if value else None
    except ValueError:
        return None


def detect_ram_mb() -> float | None:
    """Total physical RAM in MB (Windows via ctypes, POSIX via sysconf)."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes  # noqa: PLC0415

            class MEMORYSTATUSEX(ctypes.Structure):  # noqa: N801
                _fields_ = [
                    ("dwLength", wintypes.DWORD),
                    ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):  # type: ignore[attr-defined]
                return stat.ullTotalPhys / (1024 * 1024)
        except (AttributeError, OSError):
            return None
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return (pages * page_size) / (1024 * 1024)
    except (AttributeError, OSError, ValueError):
        return None


def detect_ffmpeg_encoders() -> tuple[str, ...]:
    """Hardware encoder names advertised by ``ffmpeg -encoders``.

    Returns the ones ending in ``nvenc``/``qsv``/``amf`` (best-effort; empty
    tuple when ffmpeg is unavailable or the listing cannot be parsed).
    """
    binary = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if not binary:
        return ()
    try:
        completed = subprocess.run(
            [binary, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    encoders: list[str] = []
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and any(parts[1].endswith(sfx) for sfx in _HW_ENCODER_SUFFIXES):
            encoders.append(parts[1])
    return tuple(dict.fromkeys(encoders))


def probe(device_override: str = "auto") -> HardwareProfile:
    """Build a :class:`HardwareProfile` from the live machine (best-effort)."""
    if device_override not in _VALID_OVERRIDES:
        raise ValueError(f"Unsupported device override: {device_override!r}.")
    gpu_vendor, gpu_name, vram = detect_gpu()
    try:
        import torch  # noqa: PLC0415 - lazy, heavy

        torch_cuda = bool(torch.cuda.is_available())
    except ImportError:
        torch_cuda = False
    return HardwareProfile(
        gpu_vendor=gpu_vendor,
        gpu_name=gpu_name,
        vram_mb=vram,
        ram_mb=detect_ram_mb(),
        torch_cuda=torch_cuda,
        ffmpeg_encoders=detect_ffmpeg_encoders(),
        device_override=device_override,
    )


def resolve_strategy(profile: HardwareProfile) -> Strategy:
    """Apply the frozen §14.2 matrix + user override to pick the strategy.

    - NVIDIA + torch.cuda -> faster-whisper on CUDA (int8_float16).
    - Intel iGPU / AMD GPU  -> whisper.cpp Vulkan (TASK-015); device=CPU in
      faster-whisper terms.
    - CPU-only              -> faster-whisper int8 on CPU.
    - ``cuda`` override without torch.cuda -> degrade to CPU (logged warning).
    """
    override = profile.device_override
    vendor = profile.gpu_vendor
    torch_cuda = profile.torch_cuda

    encoder = _ENCODER_BY_VENDOR.get(vendor, "libx264")
    if encoder != "libx264" and encoder not in profile.ffmpeg_encoders:
        logger.warning("Hardware encoder %s unavailable; falling back to libx264", encoder)
        encoder = "libx264"

    if override == "cpu":
        device, backend, vulkan = "cpu", STT_BACKEND_NVIDIA, False
    elif override == "cuda":
        if torch_cuda:
            device, backend, vulkan = "cuda", STT_BACKEND_NVIDIA, False
        else:
            logger.warning("CUDA override requested but torch.cuda is unavailable; using CPU.")
            device, backend, vulkan = "cpu", STT_BACKEND_NVIDIA, False
    elif torch_cuda and vendor == GPU_NVIDIA:
        device, backend, vulkan = "cuda", STT_BACKEND_NVIDIA, False
    elif vendor in (GPU_INTEL, GPU_AMD):
        device, backend, vulkan = "cpu", STT_BACKEND_WHISPER_CPP, True
    else:
        device, backend, vulkan = "cpu", STT_BACKEND_NVIDIA, False

    return Strategy(
        device=device,
        compute_type=pick_compute_type(device),
        stt_backend=backend,
        whisper_encoder=encoder,
        vulkan=vulkan,
    )


def pick_stt_model(strategy: Strategy, requested_model: str, vram_mb: float | None = None) -> str:
    """§14.2 VRAM guard: downgrade ``requested_model`` to fit free VRAM.

    Only applies on CUDA (CPU inference is not VRAM-bound). Reuses the TASK-013
    tier guard so the model choice stays consistent with ``stt_service``.
    """
    if strategy.device != "cuda":
        return requested_model
    return guard_model_tier(requested_model, vram_mb)
