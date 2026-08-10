<#
.SYNOPSIS
    Builds whisper-cli.exe (whisper.cpp) with Vulkan support for the TASK-015
    sidecar. The binary is never committed to the repo — it lands in vendor/
    (gitignored).

.DESCRIPTION
    1. Clones whisper.cpp at a pinned tag (default: master; set -Tag for a
       release).
    2. Configures with GGML_VULKAN=ON (Vulkan support for AMD/Intel, per
       MASTER_PLAN §14.2). Pass -EnableCuda to also enable CUDA for NVIDIA.
    3. Builds the whisper-cli target in Release.
    4. Copies whisper-cli.exe (and its DLLs, if any) into the output directory
       (default: repo/vendor/whisper-cpp).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File build_whisper_cpp.ps1 -Tag v1.7.4
#>
param(
    [string]$Repo = "https://github.com/ggml-org/whisper.cpp.git",
    [string]$Tag = "",
    [string]$BuildDir = "",
    [string]$OutputDir = "",
    [switch]$EnableCuda
)

$ErrorActionPreference = "Stop"

function Resolve-RepoRoot {
    $dir = $PSScriptRoot
    while ($dir -and -not (Test-Path (Join-Path $dir "MASTER_PLAN.md"))) {
        $dir = Split-Path $dir -Parent
    }
    if (-not $dir) { throw "Could not locate the repo root from $PSScriptRoot" }
    return $dir
}

$root = Resolve-RepoRoot
if (-not $BuildDir) { $BuildDir = Join-Path $env:TEMP "whisper-cpp-build" }
if (-not $OutputDir) { $OutputDir = Join-Path $root "vendor\whisper-cpp" }

Write-Host "Building whisper.cpp $Tag into $BuildDir -> $OutputDir"

$srcDir = Join-Path $BuildDir "whisper.cpp"
if (-not (Test-Path (Join-Path $srcDir "CMakeLists.txt"))) {
    Write-Host "Cloning whisper.cpp (default branch)$(if ($Tag) { " at $Tag" })..."
    $cloneArgs = @("clone", "--depth", "1")
    if ($Tag) { $cloneArgs += @("--branch", $Tag) }
    $cloneArgs += @($Repo, $srcDir)
    & git @cloneArgs
    if ($LASTEXITCODE -ne 0) { throw "git clone failed (check -Tag / -Repo)." }
} else {
    Write-Host "Source already present at $srcDir; skipping clone."
}

$build = Join-Path $BuildDir "build"
New-Item -ItemType Directory -Force -Path $build | Out-Null

$cmakeArgs = @(
    "-S", $srcDir,
    "-B", $build,
    "-DGGML_VULKAN=ON"
)
if ($EnableCuda) { $cmakeArgs += @("-DGGML_CUDA=ON") }
$cmakeArgs += @("-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_RUNTIME_OUTPUT_DIRECTORY=$build")

Write-Host "Configuring with: $($cmakeArgs -join ' ')"
cmake @cmakeArgs
if ($LASTEXITCODE -ne 0) { throw "CMake configure failed." }

Write-Host "Building whisper-cli (Release)..."
cmake --build $build --config Release --target whisper-cli --parallel
if ($LASTEXITCODE -ne 0) { throw "CMake build failed." }

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$binary = Join-Path $build "whisper-cli.exe"
if (-not (Test-Path $binary)) { throw "Build finished but $binary was not produced." }
Copy-Item $binary -Destination $OutputDir -Force

Get-ChildItem -Path $build -Filter "*.dll" -Recurse | ForEach-Object {
    Copy-Item $_.FullName -Destination $OutputDir -Force
}

Write-Host ""
Write-Host "SUCCESS: whisper-cli.exe built to $OutputDir"
Write-Host "Verify manually: & '$OutputDir\whisper-cli.exe' --help"
Write-Host "Note: Vulkan init failure at runtime falls back to CPU (TASK-015),"
Write-Host "      so this is a compatibility enhancement, not a blocker."
