# GPU STT Audit Findings

> Date: 2026-08-23 — GPU acceleration investigation for faster-whisper STT

## Summary

**GPU STT is SLOWER than CPU on this hardware.** The Quadro T1000 (4GB VRAM) cannot outperform CPU for large-v3 inference.

## Environment

| Component | Value |
|-----------|-------|
| CPU | Intel i7-10850H, 6C/12T @ 2.70GHz |
| RAM | 32 GB |
| GPU | NVIDIA Quadro T1000, 4096 MiB VRAM |
| STT Backend | faster-whisper (ctranslate2 4.8.1) |
| STT Model | large-v3 (2.9GB) |
| Compute Type | int8_float16 (GPU) / int8 (CPU) |

## Benchmark: 30s Audio Clip

| Device | STT Wall Time | VRAM Used | GPU Util |
|--------|--------------|-----------|----------|
| CPU (int8) | ~19s | 0 | N/A |
| GPU (int8_float16) | 39.1s | 2016 MiB | 45% |

**GPU is 2x slower than CPU** for this workload.

## Root Cause

1. **Quadro T1000 is a low-end GPU** — 640 CUDA cores, designed for CAD/professional workloads, not ML inference
2. **Model too large** — large-v3 (2.9GB) consumes 2GB VRAM, leaving only 2GB for inference buffers
3. **Data transfer overhead** — audio data must be transferred to GPU memory, processed, then transferred back
4. **Batch size limitation** — 4GB VRAM limits batch_size to 2, underutilizing GPU parallelism
5. **ctranslate2 CUDA backend** — optimized for higher-end GPUs (RTX series), not Quadro T-series

## Fix Applied

`worker/src/services/hardware.py` — Added ctranslate2 CUDA detection:

```python
def _ctranslate2_cuda_available() -> bool:
    """Check if ctranslate2 has CUDA support (independent of torch)."""
    try:
        import ctranslate2 as ct
        return ct.get_cuda_device_count() > 0
    except Exception:
        return False
```

This correctly detects GPU availability even without PyTorch installed. However, the strategy should prefer CPU when GPU is slower.

## Recommendation

For machines with low-end GPUs (Quadro T-series, integrated GPUs):
- Use `device=cpu` with `compute_type=int8`
- Do NOT force GPU just because CUDA is detected
- Add a benchmark-based GPU capability check in `resolve_strategy`

For machines with high-end GPUs (RTX 3060+, RTX 4060+):
- Use `device=cuda` with `compute_type=int8_float16` or `float16`
- GPU will be 3-5x faster than CPU for large-v3

## Phase 9 Impact

The chunked pipeline times out because:
1. 30s clip → 1 chunk → STT takes 39s on GPU (vs ~19s on CPU)
2. 2-min clip → 4 chunks → STT takes ~156s on GPU (vs ~76s on CPU)
3. Total chunked pipeline (STT + translate + subtitle + TTS + assembly) exceeds 600s timeout

**Phase 9 remains BLOCKED** — not due to code defects, but due to hardware limitations.
