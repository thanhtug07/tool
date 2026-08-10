"""Unit tests for the hardware probe + strategy matrix (TASK-014).

Covers the frozen MASTER_PLAN §14.2 matrix for three machine shapes (NVIDIA
CUDA, AMD/Intel iGPU, CPU-only), the device override, the ffmpeg encoder
listing, and the VRAM guard integration.
"""

from __future__ import annotations

from src.services import hardware
from src.services.hardware import (
    GPU_AMD,
    GPU_INTEL,
    GPU_NVIDIA,
    HardwareProfile,
    Strategy,
    _infer_vendor,
    detect_ffmpeg_encoders,
    pick_stt_model,
    probe,
    resolve_strategy,
)


def _profile(
    *,
    vendor=None,
    torch_cuda=False,
    encoders=(),
    override="auto",
    vram=None,
) -> HardwareProfile:
    return HardwareProfile(
        gpu_vendor=vendor,
        gpu_name=None,
        vram_mb=vram,
        torch_cuda=torch_cuda,
        ffmpeg_encoders=encoders,
        device_override=override,
    )


class TestInferVendor:
    def test_nvidia_names(self) -> None:
        assert _infer_vendor("NVIDIA GeForce RTX 4070") == GPU_NVIDIA
        assert _infer_vendor("Quadro RTX 6000") == GPU_NVIDIA

    def test_intel_and_amd(self) -> None:
        assert _infer_vendor("Intel Arc A770") == GPU_INTEL
        assert _infer_vendor("AMD Radeon 780M") == GPU_AMD

    def test_unknown_or_missing(self) -> None:
        assert _infer_vendor("Microsoft Basic Display Adapter") is None
        assert _infer_vendor(None) is None


class TestStrategyMatrix:
    def test_nvidia_cuda(self) -> None:
        strategy = resolve_strategy(_profile(vendor=GPU_NVIDIA, torch_cuda=True, encoders=("h264_nvenc",)))
        assert strategy.device == "cuda"
        assert strategy.compute_type == "int8_float16"
        assert strategy.stt_backend == "faster-whisper"
        assert strategy.whisper_encoder == "h264_nvenc"
        assert strategy.vulkan is False

    def test_amd_intel_use_whisper_cpp_vulkan(self) -> None:
        for vendor in (GPU_AMD, GPU_INTEL):
            strategy = resolve_strategy(_profile(vendor=vendor))
            assert strategy.stt_backend == "whisper-cpp"
            assert strategy.vulkan is True
            assert strategy.device == "cpu"

    def test_cpu_only(self) -> None:
        strategy = resolve_strategy(_profile())
        assert strategy.device == "cpu"
        assert strategy.compute_type == "int8"
        assert strategy.stt_backend == "faster-whisper"
        assert strategy.whisper_encoder == "libx264"

    def test_encoder_fallback_when_missing(self) -> None:
        strategy = resolve_strategy(_profile(vendor=GPU_NVIDIA, torch_cuda=True, encoders=()))
        assert strategy.whisper_encoder == "libx264"

    def test_encoder_amd_amf(self) -> None:
        strategy = resolve_strategy(_profile(vendor=GPU_AMD, encoders=("h264_amf",)))
        assert strategy.whisper_encoder == "h264_amf"


class TestDeviceOverride:
    def test_force_cpu_wins(self) -> None:
        strategy = resolve_strategy(
            _profile(vendor=GPU_NVIDIA, torch_cuda=True, override="cpu")
        )
        assert strategy.device == "cpu"
        assert strategy.stt_backend == "faster-whisper"

    def test_force_cuda_ok_when_available(self) -> None:
        strategy = resolve_strategy(
            _profile(vendor=GPU_NVIDIA, torch_cuda=True, override="cuda")
        )
        assert strategy.device == "cuda"

    def test_force_cuda_degrades_to_cpu(self) -> None:
        strategy = resolve_strategy(_profile(vendor=GPU_NVIDIA, torch_cuda=False, override="cuda"))
        assert strategy.device == "cpu"

    def test_invalid_override_rejected(self) -> None:
        try:
            probe(device_override="vulkan")
        except ValueError:
            return
        raise AssertionError("probe() must reject an unsupported override")


class TestVramGuard:
    def test_cuda_downgrades_when_short(self) -> None:
        strategy = Strategy(
            device="cuda",
            compute_type="int8_float16",
            stt_backend="faster-whisper",
            whisper_encoder="libx264",
            vulkan=False,
        )
        assert pick_stt_model(strategy, "large-v3", vram_mb=2000.0) == "small"

    def test_cpu_never_downgrades(self) -> None:
        strategy = Strategy(
            device="cpu",
            compute_type="int8",
            stt_backend="faster-whisper",
            whisper_encoder="libx264",
            vulkan=False,
        )
        assert pick_stt_model(strategy, "large-v3", vram_mb=2000.0) == "large-v3"

    def test_unknown_vram_keeps_model(self) -> None:
        strategy = Strategy(
            device="cuda",
            compute_type="int8_float16",
            stt_backend="faster-whisper",
            whisper_encoder="libx264",
            vulkan=False,
        )
        assert pick_stt_model(strategy, "large-v3", vram_mb=None) == "large-v3"


def test_detect_ffmpeg_encoders_returns_tuple(monkeypatch) -> None:
    class _Done:
        returncode = 0
        stdout = "Encoders:\n V....D h264_nvenc      NVIDIA CUDA H.264 (NVENV)\n V....D h264_qsv\n V....D h264_amf\n V....D libx264\n"
        stderr = ""

    monkeypatch.setattr(hardware.shutil, "which", lambda name: "ffmpeg")
    monkeypatch.setattr(hardware.subprocess, "run", lambda *a, **k: _Done())
    encoders = detect_ffmpeg_encoders()
    assert "h264_nvenc" in encoders
    assert "h264_qsv" in encoders
    assert "libx264" not in encoders


def test_detect_ffmpeg_encoders_empty_when_missing(monkeypatch) -> None:
    monkeypatch.setattr(hardware.shutil, "which", lambda name: None)
    assert detect_ffmpeg_encoders() == ()
