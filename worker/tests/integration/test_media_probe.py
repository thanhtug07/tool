"""Integration tests for MediaProbeService against real media fixtures (TASK-009).

Fixtures live under ``worker/tests/fixtures/media/`` and are committed, so these
tests never invoke ffmpeg themselves — they only need the ffprobe binary. They
are skipped when no ffprobe is available.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from src.api.schemas import MediaMetadata
from src.services.media_service import (
    E_VIDEO_CORRUPTED,
    E_VIDEO_INVALID,
    MediaProbeError,
    probe,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "media"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "media.schema.json"

FFPROBE = shutil.which("ffprobe")

pytestmark = pytest.mark.skipif(
    FFPROBE is None,
    reason="ffprobe not available on PATH",
)


def _golden_path(format_name: str):
    return FIXTURES / "golden" / f"{format_name}.json"


@pytest.fixture(scope="module")
def golden() -> dict[str, dict]:
    goldens: dict[str, dict] = {}
    for format_name in ("mp4", "mkv", "mov"):
        goldens[format_name] = json.loads(_golden_path(format_name).read_text("utf-8"))
    return goldens


@pytest.mark.parametrize(
    "format_name",
    ["mp4", "mkv", "mov"],
)
class TestGoldenFiles:
    def test_probe_matches_golden_metadata(self, format_name: str, golden: dict[str, dict]) -> None:
        metadata = probe(str(FIXTURES / f"tiny_h264.{format_name}"))
        assert isinstance(metadata, MediaMetadata)
        actual = json.loads(metadata.model_dump_json())
        expected = golden[format_name]

        assert actual["schema_version"] == expected["schema_version"] == 1
        assert actual["width"] == expected["width"] == 320
        assert actual["height"] == expected["height"] == 240
        assert actual["fps"] == expected["fps"] == {"numerator": 25, "denominator": 1}
        assert actual["codec"] == expected["codec"] == "h264"
        assert actual["rotation"] == expected["rotation"] == 0
        assert actual["aspect_ratio"] == expected["aspect_ratio"] == "4:3"
        assert abs(actual["duration"] - expected["duration"]) < 0.05
        assert actual["format"] == expected["format"]
        assert len(actual["video_streams"]) == 1
        assert len(actual["audio_streams"]) == 1
        assert actual["subtitle_streams"] == []

    def test_validates_against_canonical_schema(
        self, format_name: str, golden: dict[str, dict]
    ) -> None:
        import jsonschema

        schema = json.loads(SCHEMA_PATH.read_text("utf-8"))
        metadata = probe(str(FIXTURES / f"tiny_h264.{format_name}"))
        jsonschema.validate(json.loads(metadata.model_dump_json()), schema)


class TestFfmpegBinary:
    def test_resolves_ffprobe_from_environment(self) -> None:
        from src.services.media_service import resolve_ffprobe

        binary = resolve_ffprobe()
        assert binary in ("ffprobe", "ffprobe.exe") or binary.endswith(("ffprobe", "ffprobe.exe"))


class TestRotation:
    def test_rotation_tag_is_detected(self) -> None:
        metadata = probe(str(FIXTURES / "rotated90.mp4"))
        assert metadata.rotation.root == 90
        assert metadata.width == 320
        assert metadata.height == 240


class TestSubtitleAndAudioTracks:
    def test_subtitle_stream_is_detected(self) -> None:
        metadata = probe(str(FIXTURES / "with_subtitles.mkv"))
        assert len(metadata.subtitle_streams) == 1
        subtitle = metadata.subtitle_streams[0]
        assert subtitle.language == "vie"
        assert subtitle.codec == "subrip"

    def test_multiple_audio_streams(self) -> None:
        metadata = probe(str(FIXTURES / "multi_audio.mkv"))
        languages = [audio.language for audio in metadata.audio_streams]
        assert languages == ["vie", "eng"]


class TestUnusualCodec:
    def test_vp9_webm(self) -> None:
        metadata = probe(str(FIXTURES / "unusual_vp9.webm"))
        assert metadata.codec == "vp9"
        assert (metadata.fps.numerator, metadata.fps.denominator) == (25, 1)
        assert metadata.audio_streams[0].sample_rate == 48000


class TestUnicodePath:
    def test_unicode_filename_is_probed(self) -> None:
        import shutil

        unicode_name = FIXTURES / "phụ đề mẫu 中文.mp4"
        shutil.copy(FIXTURES / "tiny_h264.mp4", unicode_name)
        try:
            metadata = probe(str(unicode_name))
            assert metadata.codec == "h264"
            assert (metadata.fps.numerator, metadata.fps.denominator) == (25, 1)
        finally:
            unicode_name.unlink(missing_ok=True)

    def test_path_with_shell_metacharacters_is_probed_safely(self) -> None:
        import shutil

        nasty_name = FIXTURES / "clip; $(rm -rf) & 'touch' `evil` [x].mp4"
        shutil.copy(FIXTURES / "tiny_h264.mp4", nasty_name)
        try:
            metadata = probe(str(nasty_name))
            assert metadata.codec == "h264"
        finally:
            nasty_name.unlink(missing_ok=True)


class TestErrorMapping:
    def test_malformed_file_is_invalid(self) -> None:
        with pytest.raises(MediaProbeError) as exc_info:
            probe(str(FIXTURES / "malformed.bin"))
        assert exc_info.value.code == E_VIDEO_INVALID

    def test_corrupted_mp4_is_corrupted(self) -> None:
        with pytest.raises(MediaProbeError) as exc_info:
            probe(str(FIXTURES / "corrupted.mp4"))
        assert exc_info.value.code == E_VIDEO_CORRUPTED

    def test_audio_only_file_is_invalid(self, tmp_path) -> None:
        audio_only = tmp_path / "audio_only.mkv"
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            pytest.skip("ffmpeg not available to build audio-only fixture")
        subprocess.run(
            [
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                "-c:a", "aac",
                str(audio_only),
            ],
            check=True,
        )
        with pytest.raises(MediaProbeError) as exc_info:
            probe(str(audio_only))
        assert exc_info.value.code == E_VIDEO_INVALID

    def test_error_message_never_embeds_video_path(self) -> None:
        try:
            probe(str(FIXTURES / "malformed.bin"))
        except MediaProbeError as exc:
            assert str(FIXTURES) not in exc.message
