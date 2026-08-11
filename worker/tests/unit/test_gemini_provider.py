"""Unit tests for the Gemini provider (TASK-019).

No network: an injected fake client drives ``models.generate_content``. Covers
default/override model, prompt + responseSchema wiring, JSON repair, error
mapping (E_API_AUTH / E_API_RATE_LIMIT / E_API_ERROR), retry/backoff, cost, and
sdk-missing behaviour. One real integration call is marked ``ai`` and skipped
without GEMINI_API_KEY.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest

from src.services.providers.base import (
    E_PROVIDER_UNAVAILABLE,
    BlockInput,
    CostEstimate,
    ProviderError,
    SourceSegment,
)
from src.services.providers.translation import gemini_provider as gem

REPO_ROOT = Path(__file__).resolve().parents[3]
TRANSLATION_SCHEMA = json.loads(
    (REPO_ROOT / "schemas" / "translation.schema.json").read_text(encoding="utf-8")
)

VALID_BLOCK = {
    "block_idx": 3,
    "translations": [
        {
            "idx": 0,
            "segment_id": "seg_0",
            "source_text": "Hello world",
            "translated_text": "Xin chào thế giới",
            "confidence": 0.99,
        },
        {
            "idx": 1,
            "segment_id": "seg_1",
            "source_text": "Goodbye",
            "translated_text": "Tạm biệt",
            "confidence": 0.95,
        },
    ],
}


class _ApiError(Exception):
    def __init__(self, rest_code: int) -> None:
        super().__init__(f"http {rest_code}")
        self.rest_code = rest_code


class FakeGeminiClient:
    """Duck-typed google-genai client: pops configured events per call."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._events: list = []  # dict(text=...) responses or Exception instances
        self.models = self

    def queue(self, *events) -> None:
        self._events.extend(events)

    def generate_content(self, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if not self._events:
            raise AssertionError("no response queued")
        event = self._events.pop(0)
        if isinstance(event, BaseException):
            raise event
        if isinstance(event, dict) and "text" not in event:
            event = {"text": json.dumps(event)}
        return SimpleNamespace(text=event["text"])


def _block(context=None) -> BlockInput:
    return BlockInput(
        block_idx=3,
        segments=(
            SourceSegment(idx=0, segment_id="seg_0", text="Hello world"),
            SourceSegment(idx=1, segment_id="seg_1", text="Goodbye", speaker="A"),
        ),
        target_language="vi",
        context=context or {"glossary": {"API": "Giao diện lập trình"}, "rules": ["Giữ tên riêng"]},
    )


def _block_schema() -> dict:
    return {
        **TRANSLATION_SCHEMA,
        "type": "object",
        "properties": TRANSLATION_SCHEMA["$defs"]["TranslationBlock"]["properties"],
        "required": TRANSLATION_SCHEMA["$defs"]["TranslationBlock"]["required"],
    }


class TestDefaultsAndWiring:
    def test_default_model_is_flash_lite(self) -> None:
        assert gem.GEMINI_DEFAULT_MODEL == "gemini-2.5-flash-lite"
        assert GeminiProvider_smoke().model_name == "gemini-2.5-flash-lite"

    def test_high_model_constant(self) -> None:
        assert gem.GEMINI_HIGH_MODEL == "gemini-2.5-flash"

    def test_model_override(self) -> None:
        assert GeminiProvider_smoke(model="gemini-2.5-flash").model_name == "gemini-2.5-flash"

    def test_sends_model_prompt_and_structured_config(self) -> None:
        client = FakeGeminiClient()
        client.queue(VALID_BLOCK)
        provider = gem.GeminiProvider(api_key="test-key", client=client)
        provider.translate_block(_block())
        call = client.calls[0]
        assert call["model"] == gem.GEMINI_DEFAULT_MODEL
        assert call["config"]["response_mime_type"] == "application/json"
        assert call["config"]["response_schema"] == gem._BLOCK_SCHEMA
        assert "vi" in call["contents"]
        assert "Hello world" in call["contents"]
        assert "Giao diện lập trình" in call["contents"]


class TestTranslateBlock:
    def test_translates_block(self) -> None:
        client = FakeGeminiClient()
        client.queue(VALID_BLOCK)
        provider = gem.GeminiProvider(api_key="test-key", client=client)
        result = provider.translate_block(_block())
        assert result.block_idx == 3
        assert [t.segment_id for t in result.translations] == ["seg_0", "seg_1"]
        assert result.translations[0].translated_text == "Xin chào thế giới"

    def test_output_validates_against_schema(self) -> None:
        client = FakeGeminiClient()
        client.queue(VALID_BLOCK)
        provider = gem.GeminiProvider(api_key="test-key", client=client)
        result = provider.translate_block(_block())
        jsonschema.validate(result.model_dump(), _block_schema())

    def test_repairs_markdown_fenced_output(self) -> None:
        client = FakeGeminiClient()
        client.queue({"text": "```json\n" + json.dumps(VALID_BLOCK) + "\n```"})
        provider = gem.GeminiProvider(client=client)
        result = provider.translate_block(_block())
        assert result.block_idx == 3

    def test_rejects_missing_segment(self) -> None:
        broken = {**VALID_BLOCK, "translations": VALID_BLOCK["translations"][:1]}
        client = FakeGeminiClient()
        client.queue({"text": json.dumps(broken)})
        client.queue({"text": json.dumps(VALID_BLOCK)})
        provider = gem.GeminiProvider(client=client)
        result = provider.translate_block(_block())
        assert len(result.translations) == 2  # retried after invalid output


class TestErrorsAndRetries:
    def test_auth_error_surfaces_immediately(self, monkeypatch) -> None:
        monkeypatch.setattr(gem, "_BACKOFF_SECONDS", (0.001, 0.001, 0.001))
        client = FakeGeminiClient()
        client.queue(_ApiError(401))
        provider = gem.GeminiProvider(client=client)
        with pytest.raises(ProviderError) as excinfo:
            provider.translate_block(_block())
        assert excinfo.value.code == gem.E_API_AUTH
        assert len(client.calls) == 1

    def test_rate_limit_retries_then_succeeds(self, monkeypatch) -> None:
        monkeypatch.setattr(gem, "_BACKOFF_SECONDS", (0.001, 0.001, 0.001))
        client = FakeGeminiClient()
        client.queue(_ApiError(429), _ApiError(429), VALID_BLOCK)
        provider = gem.GeminiProvider(client=client)
        result = provider.translate_block(_block())
        assert len(client.calls) == 3
        assert result.block_idx == 3

    def test_rate_limit_exhausted_raises(self, monkeypatch) -> None:
        monkeypatch.setattr(gem, "_BACKOFF_SECONDS", (0.001, 0.001, 0.001))
        client = FakeGeminiClient()
        for _ in range(gem.GEMINI_MAX_RETRIES + 1):
            client.queue(_ApiError(429))
        provider = gem.GeminiProvider(client=client)
        with pytest.raises(ProviderError) as excinfo:
            provider.translate_block(_block())
        assert excinfo.value.code == gem.E_API_RATE_LIMIT
        assert len(client.calls) == gem.GEMINI_MAX_RETRIES + 1

    def test_invalid_output_raises_after_retries(self, monkeypatch) -> None:
        monkeypatch.setattr(gem, "_BACKOFF_SECONDS", (0.001, 0.001, 0.001))
        client = FakeGeminiClient()
        for _ in range(gem.GEMINI_MAX_RETRIES + 1):
            client.queue({"text": "not json at all"})
        provider = gem.GeminiProvider(client=client)
        with pytest.raises(ProviderError) as excinfo:
            provider.translate_block(_block())
        assert excinfo.value.code == gem.E_API_ERROR
        assert len(client.calls) == gem.GEMINI_MAX_RETRIES + 1

    def test_sdk_missing_raises_unavailable(self, monkeypatch) -> None:
        google_mod = types.ModuleType("google")
        google_mod.__path__ = []
        monkeypatch.setitem(sys.modules, "google", google_mod)
        monkeypatch.delitem(sys.modules, "google.genai", raising=False)
        provider = gem.GeminiProvider(api_key="test-key")
        with pytest.raises(ProviderError) as excinfo:
            provider.translate_block(_block())
        assert excinfo.value.code == E_PROVIDER_UNAVAILABLE

    def test_server_error_is_retryable(self, monkeypatch) -> None:
        monkeypatch.setattr(gem, "_BACKOFF_SECONDS", (0.001, 0.001, 0.001))
        client = FakeGeminiClient()
        client.queue(_ApiError(503), VALID_BLOCK)
        provider = gem.GeminiProvider(client=client)
        assert provider.translate_block(_block()).block_idx == 3


class TestCostAndHealth:
    def test_estimate_cost(self) -> None:
        provider = gem.GeminiProvider()
        cost = provider.estimate_cost(_block())
        assert isinstance(cost, CostEstimate)
        assert cost.amount > 0
        assert cost.currency == "USD"

    def test_health_requires_credentials(self) -> None:
        assert gem.GeminiProvider(api_key=None, client=None).health() is False
        assert gem.GeminiProvider(api_key="x", client=None).health() is True
        assert gem.GeminiProvider(api_key=None, client=object()).health() is True


@pytest.mark.ai
@pytest.mark.skipif(not __import__("os").environ.get("GEMINI_API_KEY"), reason="requires GEMINI_API_KEY")
def test_real_gemini_translates_block() -> None:
    import os

    provider = gem.GeminiProvider(api_key=os.environ["GEMINI_API_KEY"])
    result = provider.translate_block(_block())
    assert len(result.translations) == 2
    assert all(t.confidence and 0 <= t.confidence <= 1 for t in result.translations)


def GeminiProvider_smoke(api_key=None, model=None):
    return gem.GeminiProvider(api_key=api_key, model=model, client=FakeGeminiClient())