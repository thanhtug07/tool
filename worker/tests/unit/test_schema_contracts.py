"""Schema contract tests (TASK-007).

Validates the three validation layers for the canonical contracts:
1. JSON Schema (source of truth) rejects/passes the example fixtures.
2. Generated Pydantic models (`worker/src/api/schemas.py`) match the schemas.
3. The generator is idempotent (regeneration is byte-identical).

Also guards against secrets leaking into the shared schemas.
"""

import json
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from src.api.schemas import (
    ErrorResponse,
    HealthResponse,
    Job,
    MediaMetadata,
    Model,
    Project,
    Subtitle,
    Transcript,
    Translation,
    WorkerStateInfo,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCHEMAS_DIR = REPO_ROOT / "schemas"
EXAMPLES_VALID = SCHEMAS_DIR / "examples" / "valid"
EXAMPLES_INVALID = SCHEMAS_DIR / "examples" / "invalid"
SCHEMAS_PY = REPO_ROOT / "worker" / "src" / "api" / "schemas.py"

# example name -> (schema file, JSON-pointer to the contract)
EXAMPLE_SCHEMA = {
    "health": ("api.schema.json", "#/$defs/HealthResponse"),
    "worker_state": ("api.schema.json", "#/$defs/WorkerStateInfo"),
    "error": ("api.schema.json", "#/$defs/ErrorResponse"),
    "transcript": ("transcript.schema.json", "#"),
    "translation": ("translation.schema.json", "#"),
    "subtitle": ("subtitle.schema.json", "#"),
    "job": ("job.schema.json", "#"),
    "media": ("media.schema.json", "#"),
    "project": ("project.schema.json", "#"),
    "model": ("model.schema.json", "#"),
}

# example name -> generated Pydantic model
EXAMPLE_MODEL = {
    "health": HealthResponse,
    "worker_state": WorkerStateInfo,
    "error": ErrorResponse,
    "transcript": Transcript,
    "translation": Translation,
    "subtitle": Subtitle,
    "job": Job,
    "media": MediaMetadata,
    "project": Project,
    "model": Model,
}

SCHEMA_VERSIONED = (
    "transcript.schema.json",
    "translation.schema.json",
    "subtitle.schema.json",
    "media.schema.json",
)

FORBIDDEN_SCHEMA_FIELDS = ("api_key", "password", "private_key", "worker_auth_token", "secret")


def load_schema(filename: str) -> dict:
    return json.loads((SCHEMAS_DIR / filename).read_text(encoding="utf-8"))


def resolve_pointer(doc: dict, pointer: str) -> dict:
    """Resolve a simple JSON pointer like `#/$defs/HealthResponse`.

    The returned schema is rebuilt as a standalone document that still carries
    the file's `$defs`, so internal `$ref: "#/$defs/..."` pointers keep resolving.
    """
    assert pointer.startswith("#/")
    parts = pointer[2:].split("/")
    node = doc
    for part in parts:
        node = node[part]
    return {"$schema": doc.get("$schema"), "title": node.get("title", parts[-1]), "$defs": doc.get("$defs", {}), **node}


def contract_schema(example_name: str) -> dict:
    filename, pointer = EXAMPLE_SCHEMA[example_name]
    doc = load_schema(filename)
    if pointer == "#":
        return doc
    return resolve_pointer(doc, pointer)


@pytest.mark.parametrize("name", sorted(EXAMPLE_SCHEMA))
def test_valid_examples_pass_json_schema(name: str):
    schema = contract_schema(name)
    payload = json.loads((EXAMPLES_VALID / f"{name}.json").read_text(encoding="utf-8"))
    jsonschema.validate(instance=payload, schema=schema)


@pytest.mark.parametrize("name", sorted(EXAMPLE_SCHEMA))
def test_invalid_examples_rejected_by_json_schema(name: str):
    schema = contract_schema(name)
    payload = json.loads((EXAMPLES_INVALID / f"{name}.json").read_text(encoding="utf-8"))
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=payload, schema=schema)


@pytest.mark.parametrize("name", sorted(EXAMPLE_MODEL))
def test_valid_examples_parse_into_pydantic_models(name: str):
    model = EXAMPLE_MODEL[name]
    payload = json.loads((EXAMPLES_VALID / f"{name}.json").read_text(encoding="utf-8"))
    parsed = model.model_validate(payload)
    assert parsed.model_dump() == payload


@pytest.mark.parametrize("name", sorted(EXAMPLE_MODEL))
def test_invalid_examples_rejected_by_pydantic_models(name: str):
    model = EXAMPLE_MODEL[name]
    payload = json.loads((EXAMPLES_INVALID / f"{name}.json").read_text(encoding="utf-8"))
    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.parametrize("name", sorted(EXAMPLE_MODEL))
def test_strict_schemas_reject_unexpected_fields(name: str):
    schema = contract_schema(name)
    payload = json.loads((EXAMPLES_VALID / f"{name}.json").read_text(encoding="utf-8"))
    assert jsonschema.validate(instance=payload, schema=schema) is None
    tampered = dict(payload)
    tampered["unexpected_field"] = "should not pass"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=tampered, schema=schema)


@pytest.mark.parametrize("filename", SCHEMA_VERSIONED)
def test_versioned_schemas_require_schema_version(filename: str):
    doc = load_schema(filename)
    assert "schema_version" in doc.get("required", [])
    assert doc["properties"]["schema_version"] == {"type": "integer", "const": 1}


def test_health_contract_matches_existing_runtime():
    """TASK-005 /health payload must be valid against the canonical schema."""
    schema = contract_schema("health")
    from src import __version__

    payload = {"status": "ok", "version": __version__, "gpu": None}
    jsonschema.validate(instance=payload, schema=schema)
    parsed = HealthResponse.model_validate(payload)
    assert parsed.model_dump() == payload


def test_schemas_contain_no_secrets():
    for path in SCHEMAS_DIR.glob("*.schema.json"):
        doc = json.loads(path.read_text(encoding="utf-8"))
        text = json.dumps(doc)
        for forbidden in FORBIDDEN_SCHEMA_FIELDS:
            assert forbidden not in text, f"{path.name} must not reference {forbidden!r}"


def test_generated_pydantic_models_are_up_to_date_and_idempotent():
    """Regenerating from the canonical schemas reproduces the checked-in file."""
    import sys

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import generate_schemas

    docs = generate_schemas.load_schemas()
    merged = generate_schemas.merge_schemas(docs)
    generated = generate_schemas.run_generator(merged)
    generated = generate_schemas.strip_generator_header(generated)
    generated = generate_schemas.strip_container_root(generated, "WorkerSchemas")
    expected = SCHEMAS_PY.read_text(encoding="utf-8")
    assert generate_schemas.BANNER + generated == expected
    assert SCHEMAS_PY.read_text(encoding="utf-8").startswith("# DO NOT EDIT - generated")
