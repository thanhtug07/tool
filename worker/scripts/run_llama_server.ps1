# llama-server launch helper (TASK-020).
#
# Starts an OpenAI-compatible llama-server on the given host/port for the
# LocalLLMProvider fallback backend. Requires a GGUF model (Qwen 7-14B quant
# Q4_K_M recommended) and a llama-server binary (see vendor/ or LLAMA_SERVER_BIN).
#
# Usage:
#   .\scripts\run_llama_server.ps1 -Model "C:\models\qwen-14b-q4_k_m.gguf" -Port 8342
#   .\scripts\run_llama_server.ps1 -Model "C:\models\qwen-7b-q4_k_m.gguf" -GpuLayers 0

param(
    [Parameter(Mandatory = $true)]
    [string]$Model,

    [int]$Port = 8342,

    [int]$GpuLayers = 0,

    [string]$HostName = "127.0.0.1"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $Model)) {
    Write-Error "Model file not found: $Model"
    exit 1
}

$server = $null
foreach ($candidate in @($env:LLAMA_SERVER_BIN, "llama-server", "llama-server.exe")) {
    if (-not $candidate) { continue }
    $resolved = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($resolved) { $server = $resolved.Source; break }
    if (Test-Path -LiteralPath $candidate) { $server = $candidate; break }
}
if (-not $server) {
    Write-Error "llama-server not found. Install it and/or set LLAMA_SERVER_BIN."
    exit 1
}

Write-Host "Launching llama-server: $server"
Write-Host "  model=$Model host=$HostName port=$Port gpu_layers=$GpuLayers"

& $server -m $Model --host $HostName --port $Port --n-gpu-layers $GpuLayers
if ($LASTEXITCODE -ne 0) {
    Write-Error "llama-server exited with code $LASTEXITCODE"
    exit $LASTEXITCODE
}