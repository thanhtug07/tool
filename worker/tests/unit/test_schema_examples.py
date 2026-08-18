"""Schema example conformance (single source of truth: ``schemas/*.json``).

Every canonical example under ``schemas/examples/valid/`` must fully validate
against its JSON Schema, and every ``invalid/`` example must fail. This is the
runtime value-level validation the frontend's old TS type-anchor test could
only approximate — ``jsonschema`` checks enums, types, ranges and required
fields for real.

Wire-up table (example stem -> (schema file, JSON pointer)):
- ``api.schema.json`` carries three $defs (HealthResponse / WorkerStateInfo /
  ErrorResponse) — the api examples target each sub-schema.
- every other schema validates a whole document, examples match by stem.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

SCHEMAS = Path(__file__).resolve().parents[3] / "schemas"
EXAMPLES = SCHEMAS / "examples"

#: example stem -> (schema file, JSON pointer into it)
WIRE: dict[str, tuple[str, str]] = {
    "health": ("api.schema.json", "#/$defs/HealthResponse"),
    "worker_state": ("api.schema.json", "#/$defs/WorkerStateInfo"),
    "error": ("api.schema.json", "#/$defs/ErrorResponse"),
    "job": ("job.schema.json", "#"),
    "media": ("media.schema.json", "#"),
    "model": ("model.schema.json", "#"),
    "project": ("project.schema.json", "#"),
    "subtitle": ("subtitle.schema.json", "#"),
    "transcript": ("transcript.schema.json", "#"),
    "translation": ("translation.schema.json", "#"),
}


def _load_stem(stem: str, kind: str) -> tuple[dict, dict]:
    """Return (example_payload, validation_schema) for ``examples/<kind>/<stem>.json``.

    ``api.schema.json`` targets a ``$defs`` sub-schema whose internal
    ``#/$defs/...`` references must resolve against the full document, so the
    sub-schema is validated with the document's ``$defs`` embedded.
    """
    schema_file, pointer = WIRE[stem]
    schema = json.loads((SCHEMAS / schema_file).read_text(encoding="utf-8"))
    example = json.loads((EXAMPLES / kind / f"{stem}.json").read_text(encoding="utf-8"))
    if pointer == "#":
        return example, schema
    node: dict = schema
    for part in pointer.lstrip("#/").split("/"):
        node = node[part]  # type: ignore[index]
    if "$defs" in schema and "$defs" not in node:
        node = {**node, "$defs": schema["$defs"]}
    return example, node


@pytest.mark.parametrize("stem", sorted(WIRE))
def test_valid_examples_conform(stem: str) -> None:
    payload, schema = _load_stem(stem, "valid")
    jsonschema.validate(payload, schema)  # raises ValidationError on drift


@pytest.mark.parametrize("stem", sorted(WIRE))
def test_invalid_examples_are_rejected(stem: str) -> None:
    payload, schema = _load_stem(stem, "invalid")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, schema)
