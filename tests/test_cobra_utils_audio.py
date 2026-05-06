import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from cobrapy import cobra_utils  # noqa: E402
from cobrapy.models.environment import CobraEnvironment  # noqa: E402


def test_extract_base_audio_writes_speech_sdk_compatible_wav(monkeypatch):
    calls = []

    def fake_run(cmd, check):
        calls.append((cmd, check))

    monkeypatch.setattr(cobra_utils.subprocess, "run", fake_run)

    cobra_utils.extract_base_audio("input.mp4", "output.wav")

    assert calls == [
        (
            [
                "ffmpeg",
                "-i",
                "input.mp4",
                "-vn",
                "-acodec",
                "pcm_s16le",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-map",
                "0:a:0",
                "-f",
                "wav",
                "output.wav",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
            ],
            True,
        )
    ]


def test_extract_audio_chunk_writes_speech_sdk_compatible_wav(monkeypatch):
    calls = []

    def fake_run(cmd, check):
        calls.append((cmd, check))

    monkeypatch.setattr(cobra_utils.subprocess, "run", fake_run)

    result = cobra_utils.extract_audio_chunk(("input.mp4", 3.5, 8.0, "chunk.wav"))

    assert result == ("chunk.wav", 3.5)
    assert calls == [
        (
            [
                "ffmpeg",
                "-i",
                "input.mp4",
                "-ss",
                "3.5",
                "-to",
                "8.0",
                "-vn",
                "-acodec",
                "pcm_s16le",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-map",
                "0:a:0",
                "-f",
                "wav",
                "chunk.wav",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
            ],
            True,
        )
    ]


def test_managed_identity_speech_token_includes_resource_id(monkeypatch):
    monkeypatch.setenv("AZURE_SPEECH_REGION", "swedencentral")
    monkeypatch.setenv("AZURE_SPEECH_USE_MANAGED_IDENTITY", "true")
    monkeypatch.setenv(
        "AZURE_SPEECH_RESOURCE_ID",
        "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/speech",
    )

    env = CobraEnvironment()

    assert (
        cobra_utils._format_speech_authorization_token(env, "token")
        == "aad#/subscriptions/sub/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/speech#token"
    )


def test_managed_identity_speech_token_requires_resource_id(
    monkeypatch,
):
    monkeypatch.setenv("AZURE_SPEECH_REGION", "unit-test-region")
    monkeypatch.setenv("AZURE_SPEECH_USE_MANAGED_IDENTITY", "true")
    monkeypatch.delenv("AZURE_SPEECH_RESOURCE_ID", raising=False)

    env = CobraEnvironment()

    with pytest.raises(RuntimeError) as exc_info:
        cobra_utils._format_speech_authorization_token(env, "token")

    assert "AZURE_SPEECH_RESOURCE_ID" in str(exc_info.value)
