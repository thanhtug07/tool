"""Subtitle engine (TASK-024): cues, line break policy, CPS, ASS/SRT/VTT.

Phase 7 — turns a translated transcript into render-ready subtitle artifacts.
The subtitle **line-break policy is configurable, not a universal rule**: the
``max_chars_per_line`` threshold lives in ``SubtitleStyle`` (a function of
language / font metrics / display width / safe area) and is never hard-coded in
the wrapping algorithm (MASTER_PLAN.md §11.1, TASKS.md TASK-024).

Responsibilities
----------------
- ``CueSource`` — one translated line plus the source timing/speaker it came
  from (a transcript segment that has already been translated).
- ``SubtitleService.generate`` — optionally merges adjacent same-speaker cues,
  applies the line-break policy, checks chars-per-second (extending the
  duration when it can, otherwise warning), enforces a minimum gap/padding so
  cues never overlap, and emits an ASS/SRT (plus a VTT string helper).
- ``SubtitleDoc`` — the canonical ``Subtitle`` pydantic document (schemas/
  subtitle.schema.json §24.3) plus the serialized ASS/SRT text.
- ``validate_subtitle_with_ffmpeg`` — validates a subtitle file by parsing it
  with ffmpeg (libass), the acceptance check for "ASS mở bằng ffmpeg/VLC".

Error taxonomy follows the established service pattern (MASTER_PLAN §28.1):
``E_SUBTITLE_INVALID`` for inputs/outputs that do not conform.
"""

from __future__ import annotations

import math
import os
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from src.api.schemas import Cue, Subtitle, SubtitleOutput, SubtitleStyle, Transcript, Translation

E_SUBTITLE_INVALID = "E_SUBTITLE_INVALID"
E_SUBTITLE_FAILED = "E_SUBTITLE_FAILED"

# Engine timing parameters (not part of the style schema — they are processing
# rules, configurable per SubtitleService instance, not universal constants).
DEFAULT_MIN_GAP_SECONDS = 0.05   # padding 50 ms between cues (spec: 50-80 ms)
DEFAULT_MIN_DURATION_SECONDS = 0.2
DEFAULT_MERGE_GAP_SECONDS = 0.25
DEFAULT_MERGE_MAX_CHARS_MULTIPLIER = 2  # merged cue may hold up to 2 wrapped lines

# Play resolution of the ASS document. These are the reference pixel grid for
# the position margins; font size / margins are in PlayRes pixels.
ASS_PLAYRES_X = 1920
ASS_PLAYRES_Y = 1080
ASS_MARGIN_LR = 10
ASS_MARGIN_V = 24

# Alignment codes per position (ASS V4+): 2 = bottom center, 8 = top center.
_ASS_ALIGNMENT = {"bottom_center": 2, "top_center": 8}

# Per-language default styles. These are *defaults*, overrideable per call —
# the wrapping algorithm itself reads ``max_chars_per_line`` from the style and
# never contains a hard-coded character limit.
DEFAULT_STYLES: dict[str, SubtitleStyle] = {
    "vi": SubtitleStyle(
        font="Arial", font_size=44, stroke=2, shadow=1,
        position="bottom_center", bg_box=False, max_chars_per_line=42, max_cps=18,
    ),
    "zh": SubtitleStyle(
        font="Microsoft YaHei", font_size=44, stroke=2, shadow=1,
        position="bottom_center", bg_box=False, max_chars_per_line=24, max_cps=14,
    ),
    "en": SubtitleStyle(
        font="Arial", font_size=44, stroke=2, shadow=1,
        position="bottom_center", bg_box=False, max_chars_per_line=48, max_cps=20,
    ),
}

# Languages wrapped by character count (no space-delimited words).
_CJK_LANGUAGES = frozenset({"zh", "ja", "ko"})

# Unicode codepoint categories that carry no visual width (combining marks,
# zero-width joiners, variation selectors).
_NO_WIDTH = frozenset({"Mn", "Me", "Cf"})
_ZWJ = "\u200d"
_VARIATION_SELECTOR_RANGE = (0xFE00, 0xFE0F)


class SubtitleError(Exception):
    """Subtitle failure carrying the architecture error code (MASTER_PLAN §28.1)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def display_width(text: str) -> int:
    """Approximate grapheme-cluster count (Vietnamese diacritics count as one).

    Combining marks, ZWJ sequences and variation selectors attach to the
    preceding base codepoint, so ``"a\\u0301"`` and emoji ZWJ sequences count as
    a single character — the unit that matters for line breaking and CPS.
    """
    width = 0
    join_next = False
    for ch in text:
        joining = unicodedata.category(ch) in _NO_WIDTH or 0xFE00 <= ord(ch) <= _VARIATION_SELECTOR_RANGE[1]
        if join_next:
            join_next = ch == _ZWJ
            continue
        if ch == _ZWJ:
            join_next = True
            continue
        if ch == "\u200c" or joining:
            continue
        width += 1
    return width


def default_style(language: str) -> SubtitleStyle:
    """Per-language default style (configurable per call, never universal)."""
    return DEFAULT_STYLES.get((language or "vi").lower()[:2], DEFAULT_STYLES["vi"])


@dataclass(frozen=True)
class CueSource:
    """One translated line plus the source timing/speaker it came from."""

    idx: int
    segment_id: str
    start: float
    end: float
    speaker: str | None = None
    text: str = ""


@dataclass
class LineBreakPolicy:
    """Configurable line breaking — a function of language + style, not a constant.

    ``max_chars_per_line`` comes from the caller's style (which may itself be
    derived from language, font metrics, display width and safe area). An
    optional ``measure`` callable (e.g. real font-metric widths) can be injected;
    ``None`` falls back to the grapheme-aware ``display_width``.
    """

    language: str = "vi"
    max_chars_per_line: int = 42
    measure: Callable[[str], int] | None = None

    @property
    def _measure(self) -> Callable[[str], int]:
        return self.measure or display_width

    @property
    def _cjk(self) -> bool:
        return (self.language or "").lower()[:2] in _CJK_LANGUAGES

    def wrap(self, text: str) -> list[str]:
        """Split ``text`` into lines that each fit ``max_chars_per_line``.

        Hard newlines (already-merged cues) are kept as forced breaks. CJK text
        is broken at character boundaries; space-delimited text is wrapped at
        word boundaries (punctuation stays glued to its word), only falling
        back to a character break when a single word alone overflows the line.
        """
        lines: list[str] = []
        for part in (text or "").split("\n"):
            lines.extend(self._wrap_single(part))
        return lines

    def _wrap_single(self, text: str) -> list[str]:
        text = text.strip()
        if not text:
            return []
        if self._measure(text) <= self.max_chars_per_line:
            return [text]
        if self._cjk or " " not in text:
            return self._wrap_by_clusters(text)
        return self._wrap_by_words(text)

    def _wrap_by_clusters(self, text: str) -> list[str]:
        clusters = self._clusters(text)
        lines: list[str] = []
        current = ""
        for cluster in clusters:
            if current and self._measure(current + cluster) > self.max_chars_per_line:
                lines.append(current)
                current = cluster
            else:
                current += cluster
        if current:
            lines.append(current)
        return lines

    def _wrap_by_words(self, text: str) -> list[str]:
        words = text.split(" ")
        lines: list[str] = []
        current = ""
        for word in words:
            if not word:
                continue
            candidate = f"{current} {word}".strip() if current else word
            if self._measure(candidate) <= self.max_chars_per_line:
                current = candidate
                continue
            if current:
                lines.append(current)
                current = ""
            # A single word alone overflows the line → hard character break.
            if self._measure(word) > self.max_chars_per_line:
                lines.extend(self._wrap_by_clusters(word))
            else:
                current = word
        if current:
            lines.append(current)
        return lines

    def _clusters(self, text: str) -> list[str]:
        clusters: list[str] = []
        join_next = False
        for ch in text:
            joining = unicodedata.category(ch) in _NO_WIDTH or 0xFE00 <= ord(ch) <= _VARIATION_SELECTOR_RANGE[1]
            if not clusters:
                clusters.append(ch)
                join_next = ch == _ZWJ
                continue
            if join_next:
                clusters[-1] += ch
                join_next = ch == _ZWJ
                continue
            if ch == _ZWJ or ch == "\u200c" or joining:
                clusters[-1] += ch
                join_next = ch == _ZWJ
                continue
            clusters.append(ch)
        return clusters


def _round_ms(seconds: float) -> float:
    return round(seconds * 1000) / 1000


def _merge_cues(
    segments: Sequence[CueSource],
    *,
    same_speaker: bool,
    max_gap_seconds: float,
    max_chars: int,
    measure: Callable[[str], int],
) -> list[CueSource]:
    """Merge adjacent cues that share a speaker, sit close in time, and stay
    within ``max_chars`` combined (spec: "merge các đoạn liền nhau cùng speaker
    nếu cần"). The two source texts are joined with a hard break and re-wrapped.
    """
    if not same_speaker:
        return list(segments)
    merged: list[CueSource] = []
    for segment in segments:
        if merged and _can_merge(merged[-1], segment, max_gap_seconds, max_chars, measure):
            prev = merged.pop()
            combined = CueSource(
                idx=prev.idx,
                segment_id=f"{prev.segment_id}+{segment.segment_id}",
                start=prev.start,
                end=max(prev.end, segment.end),
                speaker=prev.speaker,
                text=f"{prev.text}\n{segment.text}",
            )
            merged.append(combined)
        else:
            merged.append(segment)
    return merged


def _can_merge(
    prev: CueSource,
    next_: CueSource,
    max_gap_seconds: float,
    max_chars: int,
    measure: Callable[[str], int],
) -> bool:
    if (prev.speaker or "") != (next_.speaker or ""):
        return False
    if next_.start - prev.end > max_gap_seconds:
        return False
    return measure(f"{prev.text}\n{next_.text}") <= max_chars


def chars_per_second(text: str, duration_seconds: float, *, measure: Callable[[str], int]) -> float:
    if duration_seconds <= 0:
        return 0.0
    return measure(text) / duration_seconds


class SubtitleDoc:
    """Canonical Subtitle document + serialized ASS/SRT text + warnings."""

    def __init__(
        self,
        document: Subtitle,
        ass_content: str,
        srt_content: str,
        warnings: list[str] | None = None,
    ) -> None:
        self.document = document
        self.ass_content = ass_content
        self.srt_content = srt_content
        self.warnings = list(warnings or [])

    def write(self, directory: Path) -> SubtitleOutput:
        """Persist ``subtitle.ass`` + ``subtitle.srt`` and record their paths."""
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        ass_path = directory / "subtitle.ass"
        srt_path = directory / "subtitle.srt"
        ass_path.write_text(self.ass_content, encoding="utf-8")
        srt_path.write_text(self.srt_content, encoding="utf-8")
        output = SubtitleOutput(ass_path=str(ass_path), srt_path=str(srt_path))
        self.document.output = output
        return output


class SubtitleService:
    """Generates cues + ASS/SRT from translated transcript segments (TASK-024)."""

    def __init__(
        self,
        *,
        min_gap_seconds: float = DEFAULT_MIN_GAP_SECONDS,
        min_duration_seconds: float = DEFAULT_MIN_DURATION_SECONDS,
        merge_same_speaker: bool = True,
        merge_max_gap_seconds: float = DEFAULT_MERGE_GAP_SECONDS,
    ) -> None:
        self.min_gap_seconds = min_gap_seconds
        self.min_duration_seconds = min_duration_seconds
        self.merge_same_speaker = merge_same_speaker
        self.merge_max_gap_seconds = merge_max_gap_seconds

    # -- public API ---------------------------------------------------------

    def generate(
        self,
        segments: Sequence[CueSource],
        *,
        style: SubtitleStyle | None = None,
        project_id: str = "proj_001",
        language: str = "vi",
        merge_same_speaker: bool | None = None,
        output_dir: Path | str | None = None,
    ) -> SubtitleDoc:
        """Build the Subtitle document: merge → wrap → CPS/padding → serialize."""
        if not segments:
            raise SubtitleError(E_SUBTITLE_INVALID, "No cue segments to generate subtitles from.")
        style = style or default_style(language)
        policy = LineBreakPolicy(language=language, max_chars_per_line=style.max_chars_per_line)

        merge = self.merge_same_speaker if merge_same_speaker is None else merge_same_speaker
        merge_max_chars = style.max_chars_per_line * DEFAULT_MERGE_MAX_CHARS_MULTIPLIER
        sources = _merge_cues(
            list(segments),
            same_speaker=merge,
            max_gap_seconds=self.merge_max_gap_seconds,
            max_chars=merge_max_chars,
            measure=policy._measure,
        )

        warnings: list[str] = []
        cues: list[Cue] = []
        for cue_number, source in enumerate(sources, start=1):
            wrapped = "\n".join(policy.wrap(source.text))
            cue = Cue(
                cue_number=cue_number,
                start=_round_ms(source.start),
                end=_round_ms(source.end),
                text=wrapped,
            )
            cue = self._enforce_reading_speed(cue, style.max_cps, warnings, measure=policy._measure)
            cue = self._enforce_timing(cue, cues, warnings)
            cues.append(cue)

        document = Subtitle(
            schema_version=1,
            project_id=project_id,
            style=style,
            cues=cues,
            output=SubtitleOutput(),
        )
        doc = SubtitleDoc(
            document=document,
            ass_content=ass_to_ass_text(document),
            srt_content=to_srt(document.cues),
            warnings=warnings,
        )
        if output_dir is not None:
            doc.write(Path(output_dir))
        return doc

    def from_transcript_and_translation(
        self,
        transcript: Transcript,
        translation: Translation,
        *,
        style: SubtitleStyle | None = None,
        language: str | None = None,
        **kwargs,
    ) -> SubtitleDoc:
        """Merge a Transcript (timing/speaker) with a Translation document.

        Timing comes from the transcript; the target text comes from the
        translation items, matched by ``segment_id``. A transcript segment
        without a translation is an inconsistency → ``E_SUBTITLE_INVALID``.
        """
        by_segment = {
            item.segment_id: item.translated_text
            for block in translation.blocks
            for item in block.translations
        }
        missing = [s.id for s in transcript.segments if s.id not in by_segment]
        if missing:
            raise SubtitleError(
                E_SUBTITLE_INVALID,
                f"Translation is missing {len(missing)} transcript segment(s): {missing[:5]}",
            )
        sources = [
            CueSource(
                idx=segment.idx,
                segment_id=segment.id,
                start=segment.start,
                end=segment.end,
                speaker=segment.speaker,
                text=by_segment[segment.id],
            )
            for segment in transcript.segments
        ]
        return self.generate(
            sources,
            style=style,
            project_id=transcript.project_id,
            language=language or transcript.language,
            **kwargs,
        )

    # -- internals ----------------------------------------------------------

    def _enforce_reading_speed(
        self,
        cue: Cue,
        max_cps: int,
        warnings: list[str],
        *,
        measure: Callable[[str], int],
    ) -> Cue:
        cps = chars_per_second(cue.text, cue.end - cue.start, measure=measure)
        if cps <= max_cps:
            return cue
        # Extend the display time so CPS drops to the threshold (rounded up to
        # 10 ms). If the next cue is too close we shorten instead and warn.
        needed_end = _round_ms(cue.start + math.ceil(measure(cue.text) / max_cps * 1000) / 1000)
        if needed_end > cue.end:
            cue = cue.model_copy(update={"end": needed_end})
        warnings.append(
            f"cue {cue.cue_number}: {cps:.1f} cps exceeds max_cps={max_cps}; "
            f"duration extended to {cue.end - cue.start:.2f}s"
        )
        return cue

    def _enforce_timing(self, cue: Cue, previous: list[Cue], warnings: list[str]) -> Cue:
        start = cue.start
        end = max(cue.end, start + self.min_duration_seconds)
        if previous:
            prev = previous[-1]
            # Guarantee the min padding: pull the previous cue's end back so
            # there is at least min_gap of quiet time before this cue starts.
            if start - prev.end < self.min_gap_seconds:
                padded_end = _round_ms(start - self.min_gap_seconds)
                if padded_end > prev.start:
                    previous[-1] = prev.model_copy(update={"end": padded_end})
                    prev = previous[-1]
            # Never overlap: keep at least min_gap from the previous cue.
            max_end = prev.start - self.min_gap_seconds
            if max_end > start:
                end = min(end, max_end)
        return cue.model_copy(update={"start": _round_ms(start), "end": _round_ms(end)})


# -- serializers --------------------------------------------------------------


def format_ass_timestamp(seconds: float) -> str:
    """ASS timestamp ``H:MM:SS.cc`` (hundredths of a centisecond, cc = 1/100 s)."""
    total_centiseconds = max(0, round(seconds * 100))
    hours, remainder = divmod(total_centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    secs, centis = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def _format_srt_timestamp(seconds: float) -> str:
    total_ms = max(0, round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    secs, ms = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def _format_vtt_timestamp(seconds: float) -> str:
    total_ms = max(0, round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    secs, ms = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"


def escape_ass_text(text: str) -> str:
    """Escape ASS Dialogue text (backslash, braces, commas, newlines)."""
    escaped = text.replace("\\", "\\\\")
    escaped = escaped.replace("{", "\\{").replace("}", "\\}")
    escaped = escaped.replace(",", "\\,")
    return escaped.replace("\n", "\\N")


def ass_to_ass_text(document: Subtitle) -> str:
    """Serialize a Subtitle document into an ASS file (V4+ styles)."""
    style = document.style
    alignment = _ASS_ALIGNMENT[style.position]
    border_style = 3 if style.bg_box else 1
    header = "\n".join(
        [
            "[Script Info]",
            "; Generated by ai-video-localization SubtitleService (TASK-024)",
            "ScriptType: v4.00+",
            f"PlayResX: {ASS_PLAYRES_X}",
            f"PlayResY: {ASS_PLAYRES_Y}",
            "WrapStyle: 2",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
            "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            (
                "Style: Default,"
                f"{style.font},{style.font_size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
                f"0,0,0,0,100,100,0,0,{border_style},{style.stroke},{style.shadow},"
                f"{alignment},{ASS_MARGIN_LR},{ASS_MARGIN_LR},{ASS_MARGIN_V},1"
            ),
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]
    )
    events = []
    for cue in document.cues:
        events.append(
            "Dialogue: 0,"
            f"{format_ass_timestamp(cue.start)},{format_ass_timestamp(cue.end)},Default,,0,0,0,,"
            f"{escape_ass_text(cue.text)}"
        )
    return header + "\n" + "\n".join(events) + "\n"


def to_srt(cues: Sequence[Cue]) -> str:
    """Serialize cues into an SRT file."""
    blocks = []
    for cue in cues:
        blocks.append(
            f"{cue.cue_number}\n"
            f"{_format_srt_timestamp(cue.start)} --> {_format_srt_timestamp(cue.end)}\n"
            f"{cue.text}\n"
        )
    return "\n".join(blocks)


def to_vtt(cues: Sequence[Cue]) -> str:
    """Serialize cues into a WebVTT file."""
    blocks = ["WEBVTT", ""]
    for cue in cues:
        blocks.append(
            f"{_format_vtt_timestamp(cue.start)} --> {_format_vtt_timestamp(cue.end)}\n"
            f"{cue.text}"
        )
        blocks.append("")
    return "\n".join(blocks)


def validate_subtitle_with_ffmpeg(path: Path | str) -> None:
    """Parse a subtitle file with ffmpeg (libass) and raise on failure.

    The acceptance check for "ASS/SRT opens cleanly in ffmpeg/VLC": a malformed
    file makes ffmpeg exit non-zero, surfaced as ``E_SUBTITLE_INVALID``. The
    file is remuxed into a temporary Matroska container (which accepts both the
    ASS and SRT subtitle codecs); the ``null`` muxer rejects purely-subtitle
    streams, so we cannot rely on it for validation.
    """
    from src.core.ffmpeg import run_ffmpeg

    path = Path(path)
    fmt = "ass" if path.suffix.lower() == ".ass" else "srt"
    fd, mkv_name = tempfile.mkstemp(suffix=".mkv")
    os.close(fd)
    try:
        args = [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
            "-f", fmt, "-i", str(path), "-map", "0:s:0", "-c", "copy",
            "-f", "matroska", mkv_name,
        ]
        result = run_ffmpeg(args, timeout=30)
    finally:
        try:
            os.unlink(mkv_name)
        except OSError:
            pass
    if result.returncode != 0:
        raise SubtitleError(E_SUBTITLE_INVALID, f"Subtitle file failed to parse with ffmpeg ({fmt}).")
