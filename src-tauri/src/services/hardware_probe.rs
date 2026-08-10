//! HardwareProbe (TASK-014): detect GPU / VRAM / RAM / FFmpeg capabilities.
//!
//! Runs once at startup (and can be re-run) to choose the pipeline strategy
//! (MASTER_PLAN §14.1). Detection is multi-source and best-effort — the app
//! must never crash because a probe tool is missing:
//!
//! - **NVIDIA**: `nvidia-smi --query-gpu=name,memory.total` (VRAM + name).
//! - **AMD / Intel**: PowerShell WMI (`Win32_VideoController` Name).
//! - **RAM**: PowerShell WMI (`Win32_ComputerSystem` TotalPhysicalMemory).
//! - **FFmpeg encoders**: `ffmpeg -encoders` filtered to `nvenc`/`qsv`/`amf`.
//!
//! Every subprocess call is bounded by a timeout; a missing binary yields
//! `None` and the probe degrades to whatever source is available
//! (MASTER_PLAN §14.1: "nhiều nguồn detect + manual override", R15).

use std::time::{Duration, Instant};

use serde::Serialize;

/// GPU vendor inferred from a device name (MASTER_PLAN §14.2 matrix).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum GpuVendor {
    Nvidia,
    Amd,
    Intel,
}

impl GpuVendor {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Nvidia => "nvidia",
            Self::Amd => "amd",
            Self::Intel => "intel",
        }
    }
}

/// Detected machine capabilities (MASTER_PLAN §14.1 `HardwareProfile`).
#[derive(Debug, Clone, PartialEq, Eq, Default, Serialize)]
pub struct HardwareProfile {
    pub gpu_vendor: Option<GpuVendor>,
    pub gpu_name: Option<String>,
    pub vram_mb: Option<u64>,
    pub ram_mb: u64,
    /// Hardware encoder names present in `ffmpeg -encoders` (nvenc/qsv/amf).
    pub ffmpeg_encoders: Vec<String>,
}

impl HardwareProfile {
    /// Fill any still-unknown fields from another (fallback) probe source.
    fn merge(&mut self, other: HardwareProfile) {
        if self.gpu_vendor.is_none() {
            self.gpu_vendor = other.gpu_vendor;
        }
        if self.gpu_name.is_none() {
            self.gpu_name = other.gpu_name;
        }
        if self.vram_mb.is_none() {
            self.vram_mb = other.vram_mb;
        }
        if self.ram_mb == 0 {
            self.ram_mb = other.ram_mb;
        }
        if self.ffmpeg_encoders.is_empty() {
            self.ffmpeg_encoders = other.ffmpeg_encoders;
        }
    }
}

/// Subprocess probe timeout — probe tools are quick; a hung one means missing.
const PROBE_TIMEOUT: Duration = Duration::from_secs(8);
/// Windows `CREATE_NO_WINDOW` so probe tools never flash a console.
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// Run `program args...`, capturing stdout, bounded by `timeout`.
///
/// Returns `None` on spawn error, timeout (child killed), or unreadable output.
/// Mirrors the bounded-run discipline of the worker sidecar without spawning a
/// supervisor thread (probe tools are short-lived and low-volume).
fn run_with_timeout(program: &str, args: &[&str], timeout: Duration) -> Option<String> {
    let mut command = std::process::Command::new(program);
    command
        .args(args)
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    let mut child = command.spawn().ok()?;
    let deadline = Instant::now() + timeout;
    loop {
        match child.try_wait().ok()? {
            Some(_) => break,
            None => {
                if Instant::now() >= deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    return None;
                }
                std::thread::sleep(Duration::from_millis(25));
            }
        }
    }
    let output = child.wait_with_output().ok()?;
    Some(String::from_utf8_lossy(&output.stdout).into_owned())
}

/// Infer the GPU vendor from a device name (best-effort, lowercase matching).
pub fn vendor_from_name(name: &str) -> Option<GpuVendor> {
    let lowered = name.to_ascii_lowercase();
    if ["nvidia", "geforce", "quadro", "tesla", "rtx", "gtx", "cuda"]
        .iter()
        .any(|kw| lowered.contains(kw))
    {
        return Some(GpuVendor::Nvidia);
    }
    if ["intel", "arc"].iter().any(|kw| lowered.contains(kw)) {
        return Some(GpuVendor::Intel);
    }
    if ["amd", "radeon"].iter().any(|kw| lowered.contains(kw)) {
        return Some(GpuVendor::Amd);
    }
    None
}

/// Parse one `nvidia-smi --query-gpu=name,memory.total` CSV line.
///
/// `"NVIDIA GeForce RTX 4070, 12288"` -> vendor/name/VRAM. Returns `None` when
/// the line cannot be parsed or names no known vendor.
pub fn parse_nvidia_smi(line: &str) -> Option<HardwareProfile> {
    let (name, memory) = line.split_once(',')?;
    let name = name.trim();
    let memory = memory.trim();
    if name.is_empty() || memory.is_empty() {
        return None;
    }
    let vram_mb: u64 = memory.parse().ok()?;
    Some(HardwareProfile {
        gpu_vendor: vendor_from_name(name),
        gpu_name: Some(name.to_string()),
        vram_mb: Some(vram_mb),
        ram_mb: 0,
        ffmpeg_encoders: Vec::new(),
    })
}

/// Parse `ffmpeg -encoders` output, keeping hardware encoder names.
///
/// Lines look like ` V.....D h264_nvenc    NVIDIA CUDA H.264 (NVENV)`. Returns
/// the encoder tokens ending in `nvenc`/`qsv`/`amf`, in first-seen order.
pub fn parse_ffmpeg_encoders(output: &str) -> Vec<String> {
    output
        .lines()
        .filter_map(|line| {
            let token = line.split_whitespace().nth(1)?;
            let is_hw =
                token.ends_with("nvenc") || token.ends_with("qsv") || token.ends_with("amf");
            is_hw.then(|| token.to_string())
        })
        .collect()
}

/// Parse PowerShell WMI output (`Win32_VideoController` Name lines).
pub fn parse_wmi_names(output: &str) -> Vec<String> {
    output
        .lines()
        .map(|line| line.trim().to_string())
        .filter(|line| !line.is_empty())
        .collect()
}

fn nvidia_smi_profile() -> Option<HardwareProfile> {
    let output = run_with_timeout(
        "nvidia-smi",
        &[
            "--query-gpu=name,memory.total",
            "--format=csv,noheader,nounits",
        ],
        PROBE_TIMEOUT,
    )?;
    let line = output.lines().next()?;
    parse_nvidia_smi(line)
}

fn wmi_gpu_profile() -> Option<HardwareProfile> {
    let script = "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name";
    let output = run_with_timeout(
        "powershell",
        &["-NoProfile", "-NonInteractive", "-Command", script],
        PROBE_TIMEOUT,
    )?;
    for name in parse_wmi_names(&output) {
        if let Some(vendor) = vendor_from_name(&name) {
            return Some(HardwareProfile {
                gpu_vendor: Some(vendor),
                gpu_name: Some(name),
                vram_mb: None,
                ram_mb: 0,
                ffmpeg_encoders: Vec::new(),
            });
        }
    }
    None
}

fn total_ram_mb() -> Option<u64> {
    let script = "[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1MB)";
    let output = run_with_timeout(
        "powershell",
        &["-NoProfile", "-NonInteractive", "-Command", script],
        PROBE_TIMEOUT,
    )?;
    output.trim().parse::<u64>().ok()
}

fn ffmpeg_encoders() -> Vec<String> {
    run_with_timeout("ffmpeg", &["-hide_banner", "-encoders"], PROBE_TIMEOUT)
        .map(|output| parse_ffmpeg_encoders(&output))
        .unwrap_or_default()
}

/// Probe the machine, layering sources (nvidia-smi -> WMI, then RAM + ffmpeg).
pub fn probe() -> HardwareProfile {
    let mut profile = HardwareProfile::default();
    if let Some(smi) = nvidia_smi_profile() {
        profile.merge(smi);
    }
    if profile.gpu_vendor.is_none() {
        if let Some(wmi) = wmi_gpu_profile() {
            profile.merge(wmi);
        }
    }
    profile.ram_mb = total_ram_mb().unwrap_or(0);
    profile.ffmpeg_encoders = ffmpeg_encoders();
    profile
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn vendor_from_nvidia_name() {
        assert_eq!(
            vendor_from_name("NVIDIA GeForce RTX 4070"),
            Some(GpuVendor::Nvidia)
        );
        assert_eq!(vendor_from_name("Quadro RTX 6000"), Some(GpuVendor::Nvidia));
    }

    #[test]
    fn vendor_from_intel_and_amd() {
        assert_eq!(vendor_from_name("Intel Arc A770"), Some(GpuVendor::Intel));
        assert_eq!(vendor_from_name("AMD Radeon 780M"), Some(GpuVendor::Amd));
    }

    #[test]
    fn vendor_unknown() {
        assert_eq!(vendor_from_name("Microsoft Basic Display Adapter"), None);
    }

    #[test]
    fn parse_nvidia_smi_line() {
        let profile = parse_nvidia_smi("NVIDIA GeForce RTX 4070, 12288").expect("parse");
        assert_eq!(profile.gpu_vendor, Some(GpuVendor::Nvidia));
        assert_eq!(profile.gpu_name.as_deref(), Some("NVIDIA GeForce RTX 4070"));
        assert_eq!(profile.vram_mb, Some(12_288));
    }

    #[test]
    fn parse_nvidia_smi_rejects_garbage() {
        assert!(parse_nvidia_smi("").is_none());
        assert!(parse_nvidia_smi("Nothing, here").is_none());
        // Unknown vendor still parses (VRAM known) but carries no vendor.
        let unknown = parse_nvidia_smi("Microsoft Basic, 8192").expect("parse");
        assert_eq!(unknown.gpu_vendor, None);
        assert_eq!(unknown.vram_mb, Some(8192));
    }

    #[test]
    fn parse_ffmpeg_encoder_listing() {
        let output = "\
Encoders:
 V.....D h264_nvenc    NVIDIA CUDA H.264 (NVENV)
 V.....D h264_qsv      Intel Quick Sync
 V....D h264_amf
 V....D libx264       libx264 H.264/MPEG-4 AVC
";
        let encoders = parse_ffmpeg_encoders(output);
        assert!(encoders.contains(&"h264_nvenc".to_string()));
        assert!(encoders.contains(&"h264_qsv".to_string()));
        assert!(!encoders.iter().any(|e| e == "libx264"));
    }

    #[test]
    fn parse_wmi_name_lines() {
        let names = parse_wmi_names("\r\nNVIDIA GeForce RTX 4070\r\nMicrosoft Basic Display\r\n");
        assert_eq!(
            names,
            vec!["NVIDIA GeForce RTX 4070", "Microsoft Basic Display"]
        );
    }

    #[test]
    fn merge_fills_unknown_fields() {
        let mut base = HardwareProfile::default();
        base.merge(HardwareProfile {
            gpu_vendor: Some(GpuVendor::Amd),
            gpu_name: Some("AMD Radeon 780M".to_string()),
            vram_mb: Some(4096),
            ram_mb: 0,
            ffmpeg_encoders: vec!["h264_amf".to_string()],
        });
        assert_eq!(base.gpu_vendor, Some(GpuVendor::Amd));
        assert_eq!(base.vram_mb, Some(4096));
        assert_eq!(base.ffmpeg_encoders, vec!["h264_amf".to_string()]);
    }
}
