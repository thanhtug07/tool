"""Test gates, security scan, and git helpers for the autonomous runner.

These functions run the project's real test commands (from ``AGENTS.md``) and
return structured results. They are used both by the runner (post-task
verification) and directly by coding agents.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent


class CommandError(RuntimeError):
    pass


def run_command(
    argv: Sequence[str],
    cwd: Path = REPO_ROOT,
    timeout: int = 600,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Run a command as an argument array (never a shell string)."""
    try:
        proc = subprocess.run(
            list(argv),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise CommandError(f"command not found: {argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CommandError(f"command timed out after {timeout}s: {argv[0]}") from exc
    if check and proc.returncode != 0:
        raise CommandError(
            f"command failed ({proc.returncode}): {' '.join(argv)}\n"
            f"--- stdout ---\n{proc.stdout[-2000:]}\n--- stderr ---\n{proc.stderr[-2000:]}"
        )
    return proc


# ---------------------------------------------------------------------------
# Layer gates (command sets from AGENTS.md)
# ---------------------------------------------------------------------------

FRONTEND_GATES = [
    ("typecheck", ["npm", "run", "typecheck"], 600),
    ("lint", ["npm", "run", "lint"], 600),
    ("format", ["npm", "run", "format:check"], 600),
    ("test", ["npm", "run", "test"], 600),
    ("build", ["npm", "run", "build"], 600),
]

RUST_GATES = [
    ("fmt", ["cargo", "fmt", "--check"], 600),
    ("check", ["cargo", "check"], 1200),
    ("clippy", ["cargo", "clippy", "--all-targets", "--all-features", "--", "-D", "warnings"], 1200),
    ("test", ["cargo", "test"], 1200),
]

WORKER_GATES = [
    ("pytest", ["python", "-m", "pytest", "worker/tests", "-m", "not ai"], 900),
]


def run_frontend_gates() -> dict[str, str]:
    return {name: _run_gate(name, argv, timeout) for name, argv, timeout in FRONTEND_GATES}


def run_rust_gates() -> dict[str, str]:
    return {name: _run_gate(name, argv, timeout) for name, argv, timeout in RUST_GATES}


def run_worker_gates() -> dict[str, str]:
    return {name: _run_gate(name, argv, timeout) for name, argv, timeout in WORKER_GATES}


def _run_gate(name: str, argv: Sequence[str], timeout: int) -> str:
    try:
        run_command(argv, timeout=timeout)
        return "PASS"
    except (CommandError, subprocess.SubprocessError) as exc:
        return f"FAIL: {exc}"


def run_gates_for_layers(layers: set[str]) -> dict[str, dict[str, str]]:
    """Run the gate set for each requested layer (frontend/rust/worker)."""
    results: dict[str, dict[str, str]] = {}
    if "frontend" in layers:
        results["frontend"] = run_frontend_gates()
    if "rust" in layers:
        results["rust"] = run_rust_gates()
    if "worker" in layers:
        results["worker"] = run_worker_gates()
    return results


def run_all_gates() -> dict[str, dict[str, str]]:
    return {
        "frontend": run_frontend_gates(),
        "rust": run_rust_gates(),
        "worker": run_worker_gates(),
    }


def gates_all_pass(results: dict[str, dict[str, str]]) -> bool:
    return all(status == "PASS" for layer in results.values() for status in layer.values())


# ---------------------------------------------------------------------------
# Security scan
# ---------------------------------------------------------------------------


def run_security_scan() -> dict[str, str]:
    """Run ``gitleaks detect`` if available; else report SKIP (no findings claim)."""
    if shutil.which("gitleaks") is None:
        return {"gitleaks": "SKIP (gitleaks not installed)"}
    try:
        run_command(["gitleaks", "detect", "--no-banner", "--no-color"], timeout=300)
        return {"gitleaks": "PASS"}
    except (CommandError, subprocess.SubprocessError) as exc:
        return {"gitleaks": f"FAIL: {exc}"}


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def git_status_lines() -> list[str]:
    return run_command(["git", "status", "--porcelain"]).stdout.splitlines()


def is_git_clean() -> bool:
    try:
        return not git_status_lines()
    except CommandError:
        return False


def last_commit_short() -> str:
    try:
        return run_command(["git", "rev-parse", "--short", "HEAD"]).stdout.strip()
    except CommandError:
        return ""


def git_log_tasks() -> list[str]:
    """Return task IDs already committed, from ``git log`` messages."""
    try:
        proc = run_command(["git", "log", "--oneline", "--no-decorate"])
    except CommandError:
        return []
    ids: list[str] = []
    for line in proc.stdout.splitlines():
        for token in line.split():
            clean = token.strip(":,")
            if clean.startswith("TASK-") and clean != "TASK-SAFE":
                ids.append(clean)
    return ids
