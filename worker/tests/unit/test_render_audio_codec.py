"""Render audio-codec decisions (dub/processed outputs must not be PCM-in-MP4).

``resolve_audio_codec`` is the pure decision behind the final ``-c:a`` flag:
- plain renders keep ``copy`` (source audio is already container-compatible),
- a dubbed render mixes in a PCM wav, so ``copy`` would mux raw PCM into the
  MP4 — the renderer must re-encode to AAC instead;
- an audio/process output (``audio_mix.wav``) is also PCM, so it needs AAC too.
"""

from src.services.render_service import DEFAULT_AUDIO_CODEC, resolve_audio_codec


def test_non_dub_keeps_copy_default():
    assert resolve_audio_codec(DEFAULT_AUDIO_CODEC, voice_track=False) == "copy"


def test_non_dub_keeps_explicit_codec():
    assert resolve_audio_codec("aac", voice_track=False) == "aac"
    assert resolve_audio_codec("libmp3lame", voice_track=False) == "libmp3lame"


def test_dub_with_default_switches_to_aac():
    assert resolve_audio_codec(DEFAULT_AUDIO_CODEC, voice_track=True) == "aac"


def test_dub_keeps_explicit_codec():
    # An explicit codec is respected even with a voice track.
    assert resolve_audio_codec("libmp3lame", voice_track=True) == "libmp3lame"
    assert resolve_audio_codec("aac", voice_track=True) == "aac"


def test_wav_audio_track_with_default_switches_to_aac():
    # audio/process output is a PCM wav used as the render's base audio;
    # copying it would mux raw PCM into the MP4.
    assert resolve_audio_codec(DEFAULT_AUDIO_CODEC, voice_track=False, wav_audio_track=True) == "aac"


def test_wav_audio_track_keeps_explicit_codec():
    assert resolve_audio_codec("libmp3lame", voice_track=False, wav_audio_track=True) == "libmp3lame"
    assert resolve_audio_codec("aac", voice_track=False, wav_audio_track=True) == "aac"


def test_dub_over_wav_audio_track_with_default_switches_to_aac():
    assert resolve_audio_codec(DEFAULT_AUDIO_CODEC, voice_track=True, wav_audio_track=True) == "aac"