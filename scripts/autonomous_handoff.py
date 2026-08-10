"""Session handoff generation for the autonomous runner.

When a coding-agent session must end (context near limit, crash, machine
restart), a fresh session must be able to resume with NO conversation history.
``docs/AUTONOMOUS_HANDOFF.md`` is written to capture everything the next
session needs.
"""

from __future__ import annotations

from pathlib import Path

from autonomous_state import REPO_ROOT, _now

HANDOFF_FILE = REPO_ROOT / "docs" / "AUTONOMOUS_HANDOFF.md"


def write_handoff(
    state: dict,
    task_section: str = "",
    files_changed: list[str] | None = None,
    tests_executed: list[str] | None = None,
    current_failures: list[str] | None = None,
    next_action: str = "",
    known_blockers: list[str] | None = None,
    context_near_limit: bool = False,
) -> str:
    """Write the handoff document. Returns the rendered markdown text."""
    files_changed = files_changed or []
    tests_executed = tests_executed or []
    current_failures = current_failures or []
    known_blockers = known_blockers or []

    sections = [
        "# AUTONOMOUS HANDOFF — Resume point",
        "",
        f"> Generated: {_now()}",
        "",
        "## Current task",
        "",
        f"- Task: `{state.get('current_task')}`",
        f"- Status: `{state.get('current_status')}`",
        f"- Retry count: `{state.get('retry_count')}`",
        f"- Context near limit: `{'true' if context_near_limit else 'false'}`",
        "",
        "## Completed tasks",
        "",
        _bullets(state.get("completed_tasks", [])),
        "",
        "## Next task",
        "",
        f"- `{state.get('next_task')}`",
        "",
        "## Task brief (from TASKS.md)",
        "",
        "```",
        task_section.strip(),
        "```",
        "",
        "## Current implementation",
        "",
        "See the working tree; the task brief above plus `TASKS.md` remain the source of truth.",
        "",
        "## Files changed in this session",
        "",
        _bullets(files_changed),
        "",
        "## Files that must not be modified",
        "",
        _bullets(
            [
                "`MASTER_PLAN.md`",
                "`MASTER_PLAN_REVIEW.md`",
                "`ARCHITECTURE_DECISION.md`",
                "`IMPLEMENTATION_ROADMAP.md`",
                "`TASKS.md`",
            ]
        ),
        "",
        "## Tests already executed",
        "",
        _bullets(tests_executed),
        "",
        "## Tests still required",
        "",
        "Run the relevant gates from `AGENTS.md` (frontend/rust/worker) before reporting PASS.",
        "",
        "## Current failures",
        "",
        _bullets(current_failures),
        "",
        "## Last commit",
        "",
        f"- `{state.get('last_commit')}`",
        "",
        "## Git status",
        "",
        "```",
        "Run `git status` on resume to inspect the working tree.",
        "```",
        "",
        "## Architecture decisions relevant to current task",
        "",
        "Read `ARCHITECTURE_DECISION.md` and `MASTER_PLAN.md` for the frozen decisions.",
        "",
        "## Next exact action",
        "",
        f"{next_action or 'Continue implementing the current task until its gate PASSes, then commit.'}",
        "",
        "## Known blockers",
        "",
        _bullets(known_blockers),
        "",
        "## Instructions for the next agent",
        "",
        "1. Read `AGENTS.md`, `docs/AUTONOMOUS_PROGRESS.md`, and this handoff.",
        "2. Inspect `git status` and `git log --oneline -20`.",
        "3. Resume the current task exactly; do NOT restart from TASK-001.",
        "4. Do not modify the protected source-of-truth documents.",
        "5. Run the task's acceptance tests and the relevant regression gates.",
        "6. Commit ONE task per commit, then update `docs/AUTONOMOUS_PROGRESS.md`.",
        "",
    ]
    text = "\n".join(sections)
    HANDOFF_FILE.write_text(text, encoding="utf-8")
    return text


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "- (none)"


if __name__ == "__main__":
    from autonomous_state import read_state

    result = read_state()
    print(write_handoff(result.state))
