"""Gemini provider (TASK-019): default translation backend (FROZEN ADR §3.3).

Uses the ``google-genai`` SDK with ``responseSchema`` structured output. The
model is configurable via settings with the S1 default ``gemini-2.5-flash-lite``
(Fast/Balanced) and ``gemini-2.5-flash`` for High/Maximum presets — never
hard-coded at call sites.

Design
------
- **Lazy SDK import**: ``google.genai`` is imported only inside
  ``_client()``, so the module (and the whole worker) imports cleanly where the
  SDK or an API key is absent; ``translate_block`` then raises
  ``E_PROVIDER_UNAVAILABLE``.
- **Structured output**: ``response_mime_type=application/json`` +
  ``responseSchema`` aligned with ``schemas/translation.schema.json``.
- **Repair**: Gemini occasionally wraps JSON in markdown fences or adds
  preamble; ``_parse_block`` strips fences and extracts the JSON object before
  validating against the pydantic ``TranslationBlock``.
- **Retry/backoff**: transient HTTP failures (429 rate-limit, 5xx) retry up to
  ``GEMINI_MAX_RETRIES`` with exponential backoff; auth failures (401/403)
  surface immediately as ``E_API_AUTH``.
- **Error mapping** (§28.1): ``E_API_AUTH``, ``E_API_RATE_LIMIT``,
  ``E_API_ERROR``, ``E_PROVIDER_UNAVAILABLE``.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from src.api.schemas import TranslationBlock, TranslationItem
from src.services.providers.base import (
    E_PROVIDER_UNAVAILABLE,
    BlockInput,
    CostEstimate,
    ProviderError,
    TranslationProvider,
)

logger = logging.getLogger(__name__)

E_API_AUTH = "E_API_AUTH"
E_API_RATE_LIMIT = "E_API_RATE_LIMIT"
E_API_ERROR = "E_API_ERROR"

#: S1 default model (ADR §3.3 / MASTER_PLAN §12): Fast/Balanced preset.
GEMINI_DEFAULT_MODEL = "gemini-2.5-flash-lite"
#: High/Maximum preset model.
GEMINI_HIGH_MODEL = "gemini-2.5-flash"

#: Retries for transient failures, then backoff in seconds.
GEMINI_MAX_RETRIES = 3
_BACKOFF_SECONDS = (1.0, 2.0, 4.0)

_AUTH_CODES = {401, 403}
_RETRYABLE_CODES = {409, 429, 500, 502, 503, 504}

#: responseSchema mirroring TranslationBlock (§24.2).
_BLOCK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "block_idx": {"type": "integer"},
        "translations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "idx": {"type": "integer"},
                    "segment_id": {"type": "string"},
                    "source_text": {"type": "string"},
                    "translated_text": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": [
                    "idx",
                    "segment_id",
                    "source_text",
                    "translated_text",
                    "confidence",
                ],
            },
        },
    },
    "required": ["block_idx", "translations"],
}


def _rest_code(exc: BaseException) -> int | None:
    """Best-effort HTTP/status code from a google-genai error."""
    for attr in ("rest_code", "status_code", "code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def build_prompt(block: BlockInput) -> str:
    """Prompt for one block: target language, context, numbered segments."""
    context = block.context or {}
    glossary = context.get("glossary") or {}
    rules = context.get("rules") or []

    lines = [
        "Translate the following media subtitles into {target}.".format(target=block.target_language),
        "Return ONLY valid JSON matching the requested schema: a TranslationBlock "
        "with block_idx and one translations[] item per segment, using the exact "
        "segment_id and idx given. Never skip, merge, or invent segments.",
    ]
    if glossary:
        lines.append("Glossary (use these fixed terms):")
        for term, translation in glossary.items():
            lines.append(f"- {term} = {translation}")
    if rules:
        lines.append("Rules:")
        lines.extend(f"- {rule}" for rule in rules)

    lines.append("Segments:")
    for segment in block.segments:
        speaker = f" (speaker={segment.speaker})" if segment.speaker else ""
        lines.append(f"[{segment.idx}|{segment.segment_id}]{speaker} {segment.text}")

    return "\n".join(lines)


def _extract_json(text: str) -> str:
    """Strip markdown fences / preamble and return the JSON object substring."""
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        lines = [line for line in lines if not line.strip().startswith("```")]
        stripped = "\n".join(lines).strip()
    first = stripped.find("{")
    last = stripped.rfind("}")
    if first != -1 and last != -1 and last > first:
        return stripped[first : last + 1]
    return stripped


def _parse_block(text: str) -> TranslationBlock:
    """Parse + repair Gemini's JSON output into a validated TranslationBlock."""
    import json

    from pydantic import ValidationError

    for candidate in (text, _extract_json(text)):
        if not candidate:
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        try:
            return TranslationBlock.model_validate(payload)
        except ValidationError:
            # Wrong shape or missing items: try the next candidate, then fail.
            continue
    raise ProviderError(E_API_ERROR, "Gemini returned an invalid/irreparable TranslationBlock.")


def _check_block_matches(block: BlockInput, parsed: TranslationBlock) -> None:
    """Gemini sometimes drops/renames items: require 1:1 with the input block."""
    expected = {segment.segment_id for segment in block.segments}
    actual = {item.segment_id for item in parsed.translations}
    if actual != expected or len(parsed.translations) != len(block.segments):
        raise ProviderError(E_API_ERROR, "Gemini mismatched segment count/ids.")


def _parse_and_validate(block: BlockInput, text: str) -> TranslationBlock:
    parsed = _parse_block(text)
    _check_block_matches(block, parsed)
    return parsed


class GeminiProvider:
    """Frozen provider: Google Gemini via ``responseSchema`` structured output."""

    name = "gemini"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.api_key = api_key
        self.model_name = model or GEMINI_DEFAULT_MODEL
        # Test seam: injected client must expose `models.generate_content(...)`.
        self._client = client

    def _resolve_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from google import genai  # noqa: PLC0415 - lazy, heavy

            return genai.Client(api_key=self.api_key)
        except ImportError as exc:
            raise ProviderError(
                E_PROVIDER_UNAVAILABLE,
                "google-genai SDK is not installed; cannot run the Gemini provider.",
            ) from exc

    def translate_block(self, block: BlockInput) -> TranslationBlock:
        client = self._resolve_client()
        prompt = build_prompt(block)
        config = {
            "response_mime_type": "application/json",
            "response_schema": _BLOCK_SCHEMA,
        }

        attempt = 0
        while True:
            try:
                response = client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=config,
                )
            except ProviderError:
                raise
            except Exception as exc:  # noqa: BLE001 - classify every API failure
                code = _rest_code(exc)
                if code in _AUTH_CODES:
                    raise ProviderError(E_API_AUTH, "Gemini authentication failed (invalid API key).") from exc
                if code in _RETRYABLE_CODES and attempt < GEMINI_MAX_RETRIES:
                    attempt += 1
                    delay = _BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS)) - 1]
                    logger.warning("Gemini transient error (HTTP %s); retrying in %.1fs", code, delay)
                    time.sleep(delay)
                    continue
                if code == 429:
                    raise ProviderError(E_API_RATE_LIMIT, "Gemini rate limit hit.") from exc
                raise ProviderError(E_API_ERROR, f"Gemini request failed (HTTP {code}).") from exc

            text = getattr(response, "text", None)
            try:
                return _parse_and_validate(block, str(text) if text is not None else "")
            except ProviderError:
                if attempt < GEMINI_MAX_RETRIES:
                    attempt += 1
                    delay = _BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS)) - 1]
                    logger.warning("Gemini returned invalid output; retrying in %.1fs", delay)
                    time.sleep(delay)
                    continue
                raise

    def estimate_cost(self, block: BlockInput) -> CostEstimate:
        chars = sum(len(segment.text) for segment in block.segments)
        return CostEstimate(
            amount=round(chars / 1000 * 0.0001, 6),
            currency="USD",
            unit="block",
        )

    def health(self) -> bool:
        return bool(self.api_key or self._client is not None)