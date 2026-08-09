#!/usr/bin/env python3
"""Generate `worker/src/api/schemas.py` from the canonical JSON Schemas.

Source of truth: `MASTER_PLAN.md` §24 and TASK-007. The JSON Schema files under
`schemas/` are canonical; the Pydantic models are generated from them via
`datamodel-code-generator` and must not be edited by hand.

Re-run after any change to `schemas/*.schema.json`:

    python scripts/generate_schemas.py

Idempotency (regenerating produces an identical file) is asserted by
`worker/tests/unit/test_schema_contracts.py`.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = ROOT / "schemas"
OUTPUT = ROOT / "worker" / "src" / "api" / "schemas.py"

BANNER = (
    "# DO NOT EDIT - generated from schemas/*.schema.json by scripts/generate_schemas.py\n"
    "# Source of truth: schemas/ (single source of truth - MASTER_PLAN.md 24 / TASK-007).\n"
    "# Re-run `python scripts/generate_schemas.py` after changing any schema file.\n"
    "\n"
)


def load_schemas() -> dict:
    """Load every `schemas/*.schema.json` file as a `{title: doc}` map."""
    docs: dict[str, dict] = {}
    for path in sorted(SCHEMAS_DIR.glob("*.schema.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        docs[path.name] = doc
    return docs


def merge_schemas(docs: dict[str, dict]) -> dict:
    """Merge the per-file schemas into one document for a single generation pass.

    Every file's `$defs` are hoisted to the merged top level (keys are unique
    across files). Each file's document root becomes a top-level `$def` named by
    its `title`, so the generated model keeps the canonical name. The `api`
    file is a pure container of `$defs` (no root contract), so its empty root is
    skipped.
    """
    merged: dict = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "WorkerSchemas",
        "type": "object",
        "$defs": {},
    }
    for filename, doc in docs.items():
        for name, schema in doc.get("$defs", {}).items():
            if name in merged["$defs"]:
                raise SystemExit(
                    f"schema $def name collision: {name!r} (in {filename})"
                )
            merged["$defs"][name] = schema
        title = doc.get("title")
        root = {k: v for k, v in doc.items() if k in ("type", "properties", "required", "additionalProperties")}
        if title is None or not root.get("properties"):
            continue  # container files (e.g. api.schema.json) have no root contract
        root["description"] = f"Document root from {filename}"
        if title in merged["$defs"]:
            raise SystemExit(f"schema title collision: {title!r} (in {filename})")
        merged["$defs"][title] = root
    return merged


def run_generator(merged: dict) -> str:
    """Run datamodel-code-generator on the merged document, return the module text."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tmp:
        json.dump(merged, tmp, ensure_ascii=False, indent=2)
        tmp_path = Path(tmp.name)

    output_path = ROOT / ".tmp_gen_schemas.py"
    cmd = [
        sys.executable,
        "-m",
        "datamodel_code_generator",
        "--input",
        str(tmp_path),
        "--input-file-type",
        "jsonschema",
        "--output",
        str(output_path),
        "--output-model-type",
        "pydantic_v2.BaseModel",
        "--enum-field-as-literal",
        "all",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise SystemExit(f"datamodel-code-generator failed:\n{proc.stderr}")
        return output_path.read_text(encoding="utf-8")
    finally:
        tmp_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)


def strip_generator_header(text: str) -> str:
    """Drop datamodel-code-generator's header (embeds a temp filename + timestamp)."""
    lines = text.splitlines(keepends=True)
    while lines and lines[0].lstrip().startswith("#"):
        lines.pop(0)
    return "".join(lines)


def strip_container_root(text: str, title: str) -> str:
    """Remove artificial empty container models generated from `$defs` containers.

    Both the merged container (`WorkerSchemas`) and file containers such as the
    `api` root have no contract of their own; datamodel-code-generator emits
    them as empty `class X(BaseModel): pass` blocks. Drop any such block.
    """
    pattern = re.compile(r"^class \w+\(BaseModel\):\n    pass\n\n", re.MULTILINE)
    return pattern.sub("", text)


def main() -> None:
    docs = load_schemas()
    merged = merge_schemas(docs)
    generated = run_generator(merged)
    generated = strip_generator_header(generated)
    generated = strip_container_root(generated, "WorkerSchemas")
    OUTPUT.write_text(BANNER + generated, encoding="utf-8")
    print(f"generated {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
