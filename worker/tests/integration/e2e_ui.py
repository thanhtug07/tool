"""E2E through the REAL desktop UI (TASK-Z2).

Drives the actual Tauri app window over WebView2 remote-debugging (CDP):
click through Home -> Automation -> Import (native file dialog, automated via
PowerShell SendKeys) -> provider selection -> AUTOMATE -> live log ->
Completed -> Open Output (real `system.reveal`), then validates the final MP4
with ffprobe. Every number printed is measured from the live UI / real files.

Setup (one time, from the repo root):

    npm run build
    cd src-tauri && cargo build --features tauri/custom-protocol

Launch the app with WebView2 remote debugging + worker env, e.g. from Git Bash:

    export WORKER_PYTHON="C:/Users/<user>/AppData/Local/Programs/Python/Python313/python.exe"
    export FFMPEG_BIN="C:/ToolTranslateChina/vendor/ffmpeg/ffmpeg.exe"
    export FFPROBE_BIN="C:/ToolTranslateChina/vendor/ffmpeg/ffprobe.exe"
    export WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS="--remote-debugging-port=9222"
    ./target/debug/ai-video-localization.exe &

Then:

    py worker/tests/integration/e2e_ui.py --fixture <path-to-video>

Exit code 0 = the whole UI flow produced a validated MP4.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets

CDP = "http://127.0.0.1:9222"
REPO = Path(__file__).resolve().parents[2]

# Script's own (test) video — override with --fixture.
DEFAULT_FIXTURE = Path(tempfile.gettempdir()) / "tc_ui_e2e" / "fixture_speech.mp4"


def find_ffprobe() -> Path:
    for cand in [REPO / "vendor" / "ffmpeg" / "ffprobe.exe", Path("C:/ToolTranslateChina/vendor/ffmpeg/ffprobe.exe")]:
        if cand.is_file():
            return cand
    raise SystemExit("ffprobe not found")


# ---------------------------------------------------------------------------
# CDP plumbing
# ---------------------------------------------------------------------------

async def get_page():
    targets = json.load(urllib.request.urlopen(f"{CDP}/json"))
    page = next((t for t in targets if t["type"] == "page"), None)
    if page is None:
        raise SystemExit("no CDP page target — is the app running with remote debugging on 9222?")
    return page


async def cdp_eval(ws, expression: str, await_promise: bool = True):
    await ws.send(
        json.dumps(
            {
                "id": 1,
                "method": "Runtime.evaluate",
                "params": {
                    "expression": expression,
                    "awaitPromise": await_promise,
                    "returnByValue": True,
                },
            }
        )
    )
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == 1:
            if "exceptionDetails" in msg.get("result", {}):
                raise RuntimeError(f"page eval failed: {msg['result']['exceptionDetails']}")
            return msg["result"]["result"].get("value")


JS = {
    "click": "el => { const e = document.querySelector(%s); if (!e) return 'MISSING'; e.click(); return 'OK'; }",
    "clickByText": "text => { const els = [...document.querySelectorAll('button, [role=button]')]; const el = els.find(b => (b.innerText||'').trim() === %s); if (!el) return 'MISSING'; el.click(); return 'OK'; }",
}


async def wait_for(ws, expr: str, timeout: float, interval: float = 1.0, desc: str = ""):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = await cdp_eval(ws, expr)
        if last:
            return last
        await asyncio.sleep(interval)
    raise TimeoutError(f"timed out waiting for {desc or expr}; last={last!r}")


def set_select_js(selector: str, value: str) -> str:
    return f"""(() => {{
      const sel = document.querySelector('{selector}');
      if (!sel) return 'MISSING';
      const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set;
      setter.call(sel, '{value}');
      sel.dispatchEvent(new Event('change', {{ bubbles: true }}));
      return 'OK';
    }})()"""


# ---------------------------------------------------------------------------
# Native file dialog automation (Windows)
# ---------------------------------------------------------------------------

PS_FOREGROUND = r"""
Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class Win {
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
}
"@
$h = [Win]::GetForegroundWindow()
$sb = New-Object System.Text.StringBuilder 256
[Win]::GetWindowText($h, $sb, 256) | Out-Null
$sb.ToString()
"""

PS_FILL_DIALOG = r"""
Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
using System.Collections.Generic;
public class Dlg {
  [DllImport("user32.dll")] public static extern IntPtr GetDlgItem(IntPtr h, int id);
  [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr h, uint m, IntPtr w, string l);
  [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr lp);
  [DllImport("user32.dll")] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  public delegate bool EnumProc(IntPtr h, IntPtr lp);
}
"@
$found = [IntPtr]::Zero
$cb = [Dlg+EnumProc]{
  param($h, $lp)
  $cls = New-Object System.Text.StringBuilder 64
  [void][Dlg]::GetClassName($h, $cls, 64)
  if ($cls.ToString() -eq "#32770") {
    $t = New-Object System.Text.StringBuilder 128
    [void][Dlg]::GetWindowText($h, $t, 128)
    if ($t.ToString() -eq "Open") {
      $script:found = $h
      return $false
    }
  }
  return $true
}
[void][Dlg]::EnumWindows($cb, [IntPtr]::Zero)
$dlg = $script:found
if ($dlg -eq [IntPtr]::Zero) { Write-Output "NO_DIALOG"; exit 2 }
$edit = [Dlg]::GetDlgItem($dlg, 1148)
if ($edit -eq [IntPtr]::Zero) { Write-Output "NO_EDIT"; exit 3 }
[void][Dlg]::SendMessage($edit, 0x000C, [IntPtr]::Zero, "{path}")  # WM_SETTEXT into the File name edit
Start-Sleep -Milliseconds 300
[void][Dlg]::SendMessage($dlg, 0x0111, [IntPtr]1, [IntPtr]::Zero)  # WM_COMMAND IDOK -> Open
Write-Output "SENT"
"""


def foreground_title() -> str:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", PS_FOREGROUND],
        capture_output=True, text=True, timeout=30,
    )
    return (out.stdout or "").strip()


def fill_open_dialog(path: str) -> None:
    ps = PS_FILL_DIALOG.replace("{path}", path)
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        capture_output=True, text=True, timeout=60,
    )
    text = (out.stdout or "").strip()
    if text != "SENT":
        raise RuntimeError(f"dialog automation failed: {text or out.stderr}")


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------

async def run(fixture: Path) -> None:
    if not fixture.is_file():
        raise SystemExit(f"fixture not found: {fixture}")
    page = await get_page()
    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=2**24) as ws:
        # 1) Navigate to Automation from Home.
        r = await cdp_eval(ws, "document.querySelector('[data-role=nav-automation]') ? 'present' : 'absent'")
        if r != "present":
            raise SystemExit("nav-automation missing — wrong page?")
        await cdp_eval(ws, "document.querySelector('[data-role=nav-automation]').click()")
        await wait_for(
            ws,
            "document.querySelector('[data-role=studio-workspace]') ? 'yes' : ''",
            10, desc="studio workspace",
        )
        print("[1] Automation page opened")

        # 2) Import the video through the real native file dialog.
        await cdp_eval(ws, "document.querySelector('[data-role=automate-button]') ? 'ok' : ''")
        r = await cdp_eval(ws, "(() => { const b=[...document.querySelectorAll('button')].find(b=>(b.innerText||'').trim()==='Import'); if(!b) return 'MISSING'; b.click(); return 'OK'; })()")
        if r != "OK":
            # project already loaded (e.g. rerun) — replace path instead
            r = await cdp_eval(ws, "(() => { const b=[...document.querySelectorAll('button')].find(b=>(b.innerText||'').trim()==='Replace'); if(!b) return 'MISSING'; b.click(); return 'OK'; })()")
            if r != "OK":
                raise SystemExit("no Import/Replace button found")
        time.sleep(2.0)
        fill_open_dialog(str(fixture))
        # 3) Wait for the project card + real ffprobe metadata.
        await wait_for(
            ws,
            "document.querySelector('[data-role=left-empty]') ? '' : 'loaded'",
            15, desc="project loaded (drop zone replaced)",
        )
        meta = await wait_for(
            ws,
            "(() => { const d=document.querySelector('[data-role=studio-workspace]'); const t=d?d.innerText:''; const m=t.match(/(\\d+):(\\d{2})/); return m ? m[0] : ''; })()",
            20, desc="ffprobe duration shown",
        )
        print(f"[3] project imported — duration {meta} (real ffprobe)")

        # 4) Set the translation provider to Gemini (real UI select).
        opts = await cdp_eval(
            ws,
            "JSON.stringify([...document.querySelectorAll('#translation-provider option')].map(o=>({v:o.value,t:o.text})))",
        )
        print(f"[4] provider options: {opts}")
        gemini = next((o["v"] for o in json.loads(opts) if "emini" in o["t"]), None)
        if gemini is None:
            raise SystemExit("no Gemini provider option found")
        await cdp_eval(ws, set_select_js("#translation-provider", gemini))
        time.sleep(1.5)
        key_state = await wait_for(
            ws,
            "(() => { const t=document.querySelector('[data-role=studio-workspace]').innerText; return t.includes('API key configured') ? 'configured' : ''; })()",
            8, desc="API key configured readout",
        )
        print(f"[4] Gemini selected — {key_state} (key from OS vault)")

        # 5) AUTOMATE.
        await cdp_eval(ws, "document.querySelector('[data-role=automate-button]').click()")
        print("[5] AUTOMATE clicked")
        started = time.monotonic()

        # 6) Watch live progress.
        samples = []
        status = ""
        while time.monotonic() - started < 600:
            status = await cdp_eval(
                ws,
                "(() => { const s=document.querySelector('[data-role=completed-summary]'); const f=document.querySelector('[data-role=failed-summary]'); "
                "const p=document.querySelector('[data-role=overall-pct]'); "
                "if (s) return 'COMPLETED'; if (f) return 'FAILED:' + (document.querySelector('[data-role=studio-workspace]')||{}).innerText; "
                "return 'RUNNING pct=' + (p ? p.innerText : '?'); })()",
            )
            if status.startswith("COMPLETED"):
                print("[6] pipeline COMPLETED in UI")
                break
            if status.startswith("FAILED"):
                raise SystemExit(f"pipeline failed: {status}")
            if status not in samples or (samples and abs(time.monotonic() - samples[-1][0]) > 9):
                samples.append((time.monotonic() - started, status))
            await asyncio.sleep(1.5)
        else:
            raise SystemExit("pipeline did not finish in 600s")

        for elapsed, s in samples:
            print(f"      t={elapsed:6.1f}s  {s}")

        # 7) Evidence: log lines, stage timings, output path, total time.
        log = await cdp_eval(
            ws,
            "document.querySelector('[data-role=console]') ? document.querySelector('[data-role=console]').innerText : ''",
        )
        output = await cdp_eval(ws, "document.querySelector('[data-role=output-path]')?.innerText || ''")
        summary = await cdp_eval(ws, "document.querySelector('[data-role=completed-summary]')?.innerText || ''")
        timeline = await cdp_eval(
            ws,
            "JSON.stringify([...document.querySelectorAll('[data-role=timeline] li')].map(li => li.innerText.replace(/\\s+/g,' ').trim()))",
        )
        print(f"[7] output path: {output}")
        print(f"[7] completed summary: {summary!r}")
        print("[7] timeline:")
        for t in json.loads(timeline):
            print(f"      - {t}")
        print("[7] live log lines:", log.count("\n") + 1)

        # 8) Validate the output with ffprobe.
        out_path = Path(output)
        if not out_path.is_file():
            raise SystemExit(f"output file does not exist: {output}")
        ffprobe = find_ffprobe()
        probe = subprocess.run(
            [
                str(ffprobe), "-v", "error", "-show_entries",
                "format=duration,size:stream=codec_type,codec_name,width,height,r_frame_rate",
                "-of", "json", str(out_path),
            ],
            capture_output=True, text=True, timeout=120,
        )
        data = json.loads(probe.stdout)
        fmt = data.get("format", {})
        streams = data.get("streams", [])
        print(
            f"[8] ffprobe: duration={fmt.get('duration')}s size={int(fmt.get('size', 0)) / 1e6:.1f}MB "
            f"streams={json.dumps(streams)}"
        )
        ok = (
            float(fmt.get("duration", 0)) > 20
            and any(s.get("codec_type") == "video" and s.get("codec_name") == "h264" for s in streams)
            and any(s.get("codec_type") == "audio" for s in streams)
        )
        if not ok:
            raise SystemExit(f"output validation failed: {probe.stdout}")

        # 9) Open Output (real system.reveal -> Explorer /select, opens the
        #    output folder with the file selected). Assert the command resolves
        #    without an error toast.
        await cdp_eval(ws, "document.querySelector('[data-role=open-output]').click()")
        time.sleep(2.5)
        toasts = await cdp_eval(
            ws,
            "JSON.stringify([...document.querySelectorAll('[data-role=toast]')].map(t => t.innerText))",
        )
        reveal = await cdp_eval(
            ws,
            f"""(async () => {{
              const inv = window.__TAURI_INTERNALS__.invoke;
              try {{ await inv('system.reveal', {{ path: {json.dumps(str(out_path))} }}); return 'OK'; }}
              catch (e) {{ return 'ERR ' + e; }}
            }})()""",
        )
        print(f"[9] Open Output clicked — toasts={toasts} direct system.reveal={reveal}")
        if toasts not in ("[]", ""):
            print(f"[!] unexpected toasts: {toasts}")

        print("\n=== UI E2E PASSED ===")
        print(f"output={out_path}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    args = ap.parse_args()
    asyncio.run(run(args.fixture))
    return 0


if __name__ == "__main__":
    sys.exit(main())
