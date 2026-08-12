"""CUDA DLL discovery for Windows (GPU STT on pip-provided CUDA libraries).

faster-whisper (ctranslate2) needs ``cublas64_12.dll`` / ``cudnn64_9.dll`` /
``cudart64_12.dll`` at inference time. The pip packages (``nvidia-cublas-cu12``,
``nvidia-cudnn-cu12``, ``nvidia-cuda-runtime-cu12``, ``nvidia-cuda-nvrtc-cu12``)
ship them under ``site-packages/nvidia/*/bin``, which the Windows DLL loader
never searches by default. Without registration every CUDA encode dies with::

    RuntimeError: Library cublas64_12.dll is not found or cannot be loaded

This module adds those directories (plus the ``ctranslate2`` package dir, which
bundles cuDNN) to the process DLL search path. No-op on non-Windows and when
the packages are absent — STT then runs on CPU via the service-level fallback.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_registered = False


def _candidate_dirs() -> list[Path]:
    """Directories that may contain CUDA runtime DLLs (best effort)."""
    dirs: list[Path] = []
    try:
        import site

        site_packages = [Path(p) for p in site.getsitepackages()]
    except Exception:  # noqa: BLE001 - never block startup on discovery
        site_packages = []
    try:
        import ctranslate2  # noqa: PLC0415 - the package dir bundles cuDNN

        dirs.append(Path(ctranslate2.__file__).resolve().parent)
    except Exception:  # noqa: BLE001 - faster-whisper may be absent
        pass
    for base in site_packages:
        nvidia = base / "nvidia"
        if not nvidia.is_dir():
            continue
        for child in sorted(nvidia.iterdir()):
            if (child / "bin").is_dir():
                dirs.append(child / "bin")
    return dirs


def ensure_cuda_libraries() -> None:
    """Register pip-provided CUDA DLL directories once per process."""
    global _registered
    if _registered:
        return
    _registered = True
    if sys.platform != "win32":
        return
    dirs = [d for d in _candidate_dirs() if d.is_dir()]
    for directory in dirs:
        try:
            os.add_dll_directory(str(directory))
        except OSError:  # noqa: S110 - best effort; CPU fallback still works
            pass
    # ctranslate2's delay-loaded CUDA imports resolve through the standard
    # loader search, which reaches PATH but NOT add_dll_directory-only dirs
    # (verified: add_dll_directory alone still yields "cublas64_12.dll is not
    # found"). Prepend the dirs to PATH so cuBLAS/cuDNN/cudart are found.
    if dirs:
        os.environ["PATH"] = os.pathsep.join(str(d) for d in dirs) + os.pathsep + os.environ.get("PATH", "")
