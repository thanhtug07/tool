"""Unit tests for MediaProbeService parsing and failure mapping (TASK-009).

Parser logic is exercised against synthetic ffprobe JSON so the tests never
require a real ffprobe binary.
"""

from __future__ import annotations

import json

import pytest

from src.api.schemas import MediaMetadata
from src.services.media_service import (
    E_FFMPEG_NOT_FOUND,
    E_VIDEO_CORRUPTED,
    E_VIDEO_INVALID,
    MediaProbeError,
    _ProbeResult,
    _classify_failure,
    parse_ffprobe_output,
    probe,
    resolve_ffprobe,
)


def _json(duration: str = "2.000000", width: int = 320, height: int = 240) -> bytes:
    """A minimal but well-formed ffprobe JSON payload."""
    payload = {
        "streams": [
            {
                "index": 0,
                "codec_name": "h264",
                "profile": "Constrained Baseline",
                "codec_type": "video",
                "width": width,
                "height": height,
                "pix_fmt": "yuv420p",
                "avg_frame_rate": "25/1",
                "r_frame_rate": "25/1",
                "display_aspect_ratio": "4:3",
                "bit_rate": "100000",
                "duration": duration,
            },
            {
                "index": 1,
                "codec_name": "aac",
                "codec_type": "audio",
                "channels": 2,
                "sample_rate": "44100",
                "bit_rate": "64000",
                "duration": duration,
                "tags": {"language": "vie"},
            },
        ],
        "format": {
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            "duration": duration,
            "bit_rate": "174472",
        },
    }
    return json.dumps(payload).encode("utf-8")


class TestParseFfprobeOutput:
    def test_parses_canonical_media_metadata(self) -> None:
        metadata = parse_ffprobe_output(_json())
        assert isinstance(metadata, MediaMetadata)
        assert metadata.schema_version == 1
        assert metadata.duration == 2.0
        assert metadata.width == 320
        assert metadata.height == 240
        assert metadata.fps.numerator == 25
        assert metadata.fps.denominator == 1
        assert metadata.codec == "h264"
        assert metadata.bitrate == 174472
        assert metadata.rotation.root == 0
        assert metadata.format == "mov,mp4,m4a,3gp,3g2,mj2"
        assert metadata.aspect_ratio == "4:3"
        assert len(metadata.video_streams) == 1
        assert metadata.video_streams[0].pixel_format == "yuv420p"
        assert len(metadata.audio_streams) == 1
        assert metadata.audio_streams[0].language == "vie"
        assert metadata.subtitle_streams == []

    def test_prefers_avg_frame_rate(self) -> None:
        payload = _json()
        data = json.loads(payload)
        data["streams"][0]["avg_frame_rate"] = "30000/1001"
        data["streams"][0]["r_frame_rate"] = "30/1"
        metadata = parse_ffprobe_output(json.dumps(data).encode("utf-8"))
        assert (metadata.fps.numerator, metadata.fps.denominator) == (30000, 1001)

    def test_variable_frame_rate_falls_back_to_r_frame_rate(self) -> None:
        payload = _json()
        data = json.loads(payload)
        data["streams"][0]["avg_frame_rate"] = "0/0"
        data["streams"][0]["r_frame_rate"] = "24000/1001"
        metadata = parse_ffprobe_output(json.dumps(data).encode("utf-8"))
        assert (metadata.fps.numerator, metadata.fps.denominator) == (24000, 1001)

    def test_unusual_fps_rational_is_preserved_exactly(self) -> None:
        payload = _json()
        data = json.loads(payload)
        data["streams"][0]["avg_frame_rate"] = "160/17"
        metadata = parse_ffprobe_output(json.dumps(data).encode("utf-8"))
        assert (metadata.fps.numerator, metadata.fps.denominator) == (160, 17)

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("0", 0),
            ("90", 270),  # tags.rotate is clockwise; CCW display angle = 270
            ("-90", 90),
            ("180", 180),
            ("270", 90),
            ("-270", 270),
            ("450", 270),
            ("0.0", 0),
            ("12", 0),  # not an exact multiple -> snap to nearest
        ],
    )
    def test_rotation_tag_normalization(self, raw: str, expected: int) -> None:
        payload = _json()
        data = json.loads(payload)
        data["streams"][0]["tags"] = {"rotate": raw}
        metadata = parse_ffprobe_output(json.dumps(data).encode("utf-8"))
        assert metadata.rotation.root == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("90", 90),  # side_data rotation is counter-clockwise
            ("-90", 270),
            ("180", 180),
            ("-180", 180),
            ("0", 0),
        ],
    )
    def test_rotation_from_display_matrix_side_data(self, raw: str, expected: int) -> None:
        payload = _json()
        data = json.loads(payload)
        data["streams"][0]["side_data_list"] = [
            {"side_data_type": "Display Matrix", "rotation": raw}
        ]
        metadata = parse_ffprobe_output(json.dumps(data).encode("utf-8"))
        assert metadata.rotation.root == expected

    def test_no_rotation_means_zero(self) -> None:
        assert parse_ffprobe_output(_json()).rotation.root == 0

    def test_aspect_ratio_falls_back_to_dimension_gcd(self) -> None:
        payload = _json()
        data = json.loads(payload)
        data["streams"][0].pop("display_aspect_ratio")
        metadata = parse_ffprobe_output(json.dumps(data).encode("utf-8"))
        assert metadata.aspect_ratio == "4:3"

    def test_container_duration_fallback_uses_video_stream(self) -> None:
        payload = _json()
        data = json.loads(payload)
        data["format"].pop("duration")
        metadata = parse_ffprobe_output(json.dumps(data).encode("utf-8"))
        assert metadata.duration == 2.0

    def test_subtitle_streams_are_mapped(self) -> None:
        payload = _json()
        data = json.loads(payload)
        data["streams"].append(
            {
                "index": 2,
                "codec_name": "subrip",
                "codec_type": "subtitle",
                "tags": {"language": "vie", "title": "Phu de"},
            }
        )
        metadata = parse_ffprobe_output(json.dumps(data).encode("utf-8"))
        assert len(metadata.subtitle_streams) == 1
        subtitle = metadata.subtitle_streams[0]
        assert subtitle.codec == "subrip"
        assert subtitle.language == "vie"
        assert subtitle.title == "Phu de"

    def test_multiple_video_audio_streams(self) -> None:
        payload = _json()
        data = json.loads(payload)
        data["streams"][0]["index"] = 1
        data["streams"].insert(
            0,
            {
                "index": 0,
                "codec_name": "h264",
                "codec_type": "video",
                "width": 640,
                "height": 480,
                "avg_frame_rate": "25/1",
                "r_frame_rate": "25/1",
            },
        )
        data["streams"].append(
            {
                "index": 2,
                "codec_name": "aac",
                "codec_type": "audio",
                "channels": 1,
                "sample_rate": "48000",
                "tags": {"language": "eng"},
            }
        )
        metadata = parse_ffprobe_output(json.dumps(data).encode("utf-8"))
        assert len(metadata.video_streams) == 2
        assert len(metadata.audio_streams) == 2

    def test_missing_video_stream_is_invalid(self) -> None:
        payload = _json()
        data = json.loads(payload)
        data["streams"] = [s for s in data["streams"] if s.get("codec_type") != "video"]
        with pytest.raises(MediaProbeError) as exc_info:
            parse_ffprobe_output(json.dumps(data).encode("utf-8"))
        assert exc_info.value.code == E_VIDEO_INVALID

    def test_bad_json_is_invalid(self) -> None:
        with pytest.raises(MediaProbeError) as exc_info:
            parse_ffprobe_output(b"{not valid json")
        assert exc_info.value.code == E_VIDEO_INVALID

    def test_non_dict_payload_is_invalid(self) -> None:
        with pytest.raises(MediaProbeError) as exc_info:
            parse_ffprobe_output(b"[1, 2, 3]")
        assert exc_info.value.code == E_VIDEO_INVALID

    def test_unusual_codec_is_accepted(self) -> None:
        payload = _json()
        data = json.loads(payload)
        data["streams"][0]["codec_name"] = "vp9"
        metadata = parse_ffprobe_output(json.dumps(data).encode("utf-8"))
        assert metadata.codec == "vp9"

    def test_null_fields_are_tolerated(self) -> None:
        payload = _json()
        data = json.loads(payload)
        data["streams"][0].update(
            {"codec_name": None, "width": None, "height": None, "display_aspect_ratio": None}
        )
        metadata = parse_ffprobe_output(json.dumps(data).encode("utf-8"))
        assert metadata.codec is None
        assert metadata.width is None
        assert metadata.height is None
        assert metadata.aspect_ratio is None


class TestClassifyFailure:
    def _result(self, stderr: str) -> _ProbeResult:
        return _ProbeResult(returncode=1, stdout=b"", stderr=stderr.encode("utf-8"))

    def test_invalid_data_marker_is_invalid(self, tmp_path) -> None:
        path = str(tmp_path / "blob.bin")
        with open(path, "wb") as handle:
            handle.write(b"random bytes")
        error = _classify_failure(self._result("blob.bin: Invalid data found when processing input"), path)
        assert error.code == E_VIDEO_INVALID

    def test_not_a_like_marker_is_invalid(self, tmp_path) -> None:
        path = str(tmp_path / "blob.bin")
        with open(path, "wb") as handle:
            handle.write(b"random bytes")
        error = _classify_failure(self._result("blob.bin: does not look like a video"), path)
        assert error.code == E_VIDEO_INVALID

    def test_mp4_with_ftyp_header_failure_is_corrupted(self, tmp_path) -> None:
        path = str(tmp_path / "clip.mp4")
        with open(path, "wb") as handle:
            handle.write(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2")
        error = _classify_failure(self._result("clip.mp4: moov atom not found"), path)
        assert error.code == E_VIDEO_CORRUPTED

    def test_unknown_failure_is_corrupted(self, tmp_path) -> None:
        path = str(tmp_path / "clip.mkv")
        with open(path, "wb") as handle:
            handle.write(b"not a real mkv")
        error = _classify_failure(self._result("clip.mkv: something went wrong"), path)
        assert error.code == E_VIDEO_CORRUPTED


class TestProbeErrorPaths:
    def test_empty_path_is_invalid(self) -> None:
        with pytest.raises(MediaProbeError) as exc_info:
            probe("")
        assert exc_info.value.code == E_VIDEO_INVALID

    def test_nul_byte_path_is_invalid(self) -> None:
        with pytest.raises(MediaProbeError) as exc_info:
            probe("a\x00b.mp4")
        assert exc_info.value.code == E_VIDEO_INVALID

    def test_missing_file_is_invalid(self) -> None:
        with pytest.raises(MediaProbeError) as exc_info:
            probe("does_not_exist_12345.mp4")
        assert exc_info.value.code == E_VIDEO_INVALID

    def test_ffprobe_not_found_is_mapped(self, monkeypatch, tmp_path) -> None:
        from src.services import media_service

        path = str(tmp_path / "clip.mp4")
        with open(path, "wb") as handle:
            handle.write(b"\x00\x00\x00\x18ftypisom")

        def fake_run(ffprobe: str, path: str, extra: list[str]) -> _ProbeResult:
            raise MediaProbeError(E_FFMPEG_NOT_FOUND, "ffprobe executable not found.")

        monkeypatch.setattr(media_service, "_run_ffprobe", fake_run)
        with pytest.raises(MediaProbeError) as exc_info:
            probe(path)
        assert exc_info.value.code == E_FFMPEG_NOT_FOUND

    def test_lenient_retry_recovers_corrupted_but_parseable_file(
        self, monkeypatch, tmp_path
    ) -> None:
        from src.services import media_service

        path = str(tmp_path / "clip.mkv")
        with open(path, "wb") as handle:
            handle.write(b"broken")
        good_output = _json()

        def fake_run(ffprobe: str, path: str, extra: list[str]) -> _ProbeResult:
            if "-err_detect" in extra:
                return _ProbeResult(returncode=0, stdout=good_output, stderr=b"")
            return _ProbeResult(returncode=1, stdout=b"", stderr=b"broken.mkv: error")

        monkeypatch.setattr(media_service, "_run_ffprobe", fake_run)
        metadata = probe(path)
        assert metadata.width == 320

    def test_double_failure_raises_corrupted(self, monkeypatch, tmp_path) -> None:
        from src.services import media_service

        path = str(tmp_path / "clip.mkv")
        with open(path, "wb") as handle:
            handle.write(b"broken")

        def fake_run(ffprobe: str, path: str, extra: list[str]) -> _ProbeResult:
            return _ProbeResult(returncode=1, stdout=b"", stderr=b"clip.mkv: broken")

        monkeypatch.setattr(media_service, "_run_ffprobe", fake_run)
        with pytest.raises(MediaProbeError) as exc_info:
            probe(path)
        assert exc_info.value.code == E_VIDEO_CORRUPTED

    def test_error_messages_never_embed_path(self, monkeypatch, tmp_path) -> None:
        from src.services import media_service

        path = str(tmp_path / "clip.mp4")
        with open(path, "wb") as handle:
            handle.write(b"\x00\x00\x00\x18ftypisom")

        def fake_run(ffprobe: str, path: str, extra: list[str]) -> _ProbeResult:
            return _ProbeResult(
                returncode=1,
                stdout=b"",
                stderr=f"{path}: Invalid data found when processing input".encode(),
            )

        monkeypatch.setattr(media_service, "_run_ffprobe", fake_run)
        try:
            probe(path)
        except MediaProbeError as exc:
            assert path not in exc.message


class TestResolveFfprobe:
    def test_default_is_bare_ffprobe(self, monkeypatch) -> None:
        monkeypatch.delenv("FFPROBE_BIN", raising=False)
        assert resolve_ffprobe() == "ffprobe"

    def test_allowlisted_bare_name(self, monkeypatch) -> None:
        monkeypatch.setenv("FFPROBE_BIN", "ffprobe")
        assert resolve_ffprobe() == "ffprobe"

    def test_allowlisted_bare_name_windows(self, monkeypatch) -> None:
        monkeypatch.setenv("FFPROBE_BIN", "ffprobe.exe")
        assert resolve_ffprobe() == "ffprobe.exe"

    def test_rejects_arbitrary_command_name(self, monkeypatch) -> None:
        monkeypatch.setenv("FFPROBE_BIN", "notepad.exe")
        with pytest.raises(MediaProbeError) as exc_info:
            resolve_ffprobe()
        assert exc_info.value.code == E_FFMPEG_NOT_FOUND

    def test_rejects_arbitrary_path(self, monkeypatch) -> None:
        monkeypatch.setenv("FFPROBE_BIN", "C:\\Windows\\System32\\cmd.exe")
        with pytest.raises(MediaProbeError) as exc_info:
            resolve_ffprobe()
        assert exc_info.value.code == E_FFMPEG_NOT_FOUND

    def test_rejects_path_with_wrong_basename(self, monkeypatch, tmp_path) -> None:
        wrong = tmp_path / "wrongname.exe"
        wrong.write_bytes(b"")
        monkeypatch.setenv("FFPROBE_BIN", str(wrong))
        with pytest.raises(MediaProbeError) as exc_info:
            resolve_ffprobe()
        assert exc_info.value.code == E_FFMPEG_NOT_FOUND

    def test_accepts_existing_path_with_allowlisted_basename(self, monkeypatch, tmp_path) -> None:
        ffprobe = tmp_path / "ffprobe.exe"
        ffprobe.write_bytes(b"")
        monkeypatch.setenv("FFPROBE_BIN", str(ffprobe))
        assert resolve_ffprobe() == str(ffprobe)

    def test_rejects_nul_in_candidate(self, monkeypatch) -> None:
        from src.services import media_service

        fake_environ = {"FFPROBE_BIN": "ffprobe\x00be"}
        monkeypatch.setattr(media_service.os, "environ", fake_environ)
        with pytest.raises(MediaProbeError) as exc_info:
            resolve_ffprobe()
        assert exc_info.value.code == E_FFMPEG_NOT_FOUND
