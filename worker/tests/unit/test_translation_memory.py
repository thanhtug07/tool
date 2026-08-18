"""TranslationMemory ``rules_ver`` regression (FIX #5, review 2026-08-18).

The memory key is ``(source_hash, target_language, glossary_ver, rules_ver,
model)``. Before the fix, ``rules_ver`` was absent from the key while
``cache.tr_key`` already included it — so switching the translation rules
(e.g. natural vs literal) still hit entries produced under the old rules.
"""

from __future__ import annotations

import json

from src.api.schemas import TranslationItem
from src.services.translation_service import TranslationMemory, rules_version, source_hash


def item(text: str = "Hello", translated: str = "Xin chào") -> TranslationItem:
    return TranslationItem(
        idx=0,
        segment_id="seg_0",
        source_text=text,
        translated_text=translated,
        confidence=1.0,
    )


def test_rules_version_is_stable_and_distinguishes_rule_sets():
    assert rules_version(None) == "none"
    assert rules_version([]) == "none"
    assert rules_version(["natural", "formal"]) == rules_version(["formal", "natural"])
    assert rules_version(["natural"]) != rules_version(["literal"])
    assert rules_version(["natural", "formal"]) != rules_version(["natural"])


def test_memory_rotates_cache_when_rules_change():
    tm = TranslationMemory()
    tm.put(
        item(),
        target_language="vi",
        glossary_ver="g1",
        rules_ver=rules_version(["natural"]),
        model="m1",
    )
    # Same source/target/glossary/model, different rules → cache miss.
    assert (
        tm.get(
            "Hello",
            target_language="vi",
            glossary_ver="g1",
            rules_ver=rules_version(["literal"]),
            model="m1",
        )
        is None
    )
    # Same rules → hit.
    hit = tm.get(
        "Hello",
        target_language="vi",
        glossary_ver="g1",
        rules_ver=rules_version(["natural"]),
        model="m1",
    )
    assert hit is not None and hit.translated_text == "Xin chào"
    assert hit.rules_ver == rules_version(["natural"])


def test_save_load_roundtrip_preserves_rules_ver(tmp_path):
    tm = TranslationMemory()
    tm.put(
        item(translated="Dịch A"),
        target_language="vi",
        glossary_ver="g1",
        rules_ver="rulesA",
        model="m1",
    )
    path = tmp_path / "tm.json"
    tm.save(path)
    loaded = TranslationMemory.load(path)
    assert len(loaded) == 1
    hit = loaded.get(
        "Hello",
        target_language="vi",
        glossary_ver="g1",
        rules_ver="rulesA",
        model="m1",
    )
    assert hit is not None and hit.translated_text == "Dịch A" and hit.rules_ver == "rulesA"


def test_load_old_entries_without_rules_ver_default_to_none(tmp_path):
    # Backward compatibility: entries written before the fix have no
    # ``rules_ver`` field → they load as the "none" sentinel and still hit for
    # a caller that also passes no rules.
    path = tmp_path / "tm.json"
    payload = [
        {
            "hash": source_hash("Hello"),
            "target_language": "vi",
            "glossary_ver": "g1",
            "model": "m1",
            "idx": 0,
            "segment_id": "seg_0",
            "source_text": "Hello",
            "translated_text": "Xin chào",
            "confidence": 1.0,
        }
    ]
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = TranslationMemory.load(path)
    assert len(loaded) == 1
    hit = loaded.get(
        "Hello",
        target_language="vi",
        glossary_ver="g1",
        rules_ver="none",
        model="m1",
    )
    assert hit is not None and hit.translated_text == "Xin chào"