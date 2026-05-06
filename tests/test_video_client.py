import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from cobrapy import video_client as video_client_module  # noqa: E402
from cobrapy.video_client import VideoClient  # noqa: E402


def _build_file_metadata(audio_info: dict) -> dict:
    return {
        "video_info": {
            "width": 1920,
            "height": 1080,
            "fps": "30/1",
            "duration": "10.0",
            "nb_frames": "300",
        },
        "audio_info": audio_info,
    }


@pytest.fixture(autouse=True)
def _prepare_env(monkeypatch):
    monkeypatch.setenv("AZURE_SPEECH_REGION", "unit-test-region")
    for env_var in (
        "AZURE_STORAGE_ACCOUNT_URL",
        "AZURE_STORAGE_ACCOUNT_NAME",
        "AZURE_STORAGE_ACCOUNT_KEY",
        "AZURE_STORAGE_CONNECTION_STRING",
        "AZURE_STORAGE_VIDEO_CONTAINER",
        "AZURE_STORAGE_OUTPUT_CONTAINER",
        "AZURE_STORAGE_MANAGED_IDENTITY_CLIENT_ID",
        "AZURE_SEARCH_ENDPOINT",
        "AZURE_SEARCH_INDEX_NAME",
        "AZURE_SEARCH_API_KEY",
        "AZURE_SEARCH_MANAGED_IDENTITY_CLIENT_ID",
    ):
        monkeypatch.delenv(env_var, raising=False)


def test_prepare_manifest_detects_audio_without_bits_per_sample(monkeypatch, tmp_path):
    video_path = tmp_path / "demo.mp4"
    video_path.write_bytes(b"0")

    metadata = _build_file_metadata(
        {
            "codec_type": "audio",
            "duration": "5.25",
            "avg_frame_rate": "48000/1",
            "channels": "2",
        }
    )

    monkeypatch.setattr(video_client_module, "get_file_info", lambda path: metadata)

    client = VideoClient(video_path=str(video_path))

    source = client.manifest.source_video
    assert source.audio_found is True
    assert source.audio_duration == pytest.approx(5.25)
    assert source.audio_fps == pytest.approx(48000.0)


def test_prepare_manifest_detects_audio_from_channel_count(monkeypatch, tmp_path):
    video_path = tmp_path / "demo_no_codec.mp4"
    video_path.write_bytes(b"0")

    metadata = _build_file_metadata(
        {
            "channels": "1",
            "sample_rate": "16000",
            "duration": "2.0",
            "avg_frame_rate": "16000/1",
        }
    )

    monkeypatch.setattr(video_client_module, "get_file_info", lambda path: metadata)

    client = VideoClient(video_path=str(video_path))

    source = client.manifest.source_video
    assert source.audio_found is True
    assert source.audio_duration == pytest.approx(2.0)
    assert source.audio_fps == pytest.approx(16000.0)


def test_upload_disabled_does_not_initialize_cloud_clients(monkeypatch, tmp_path):
    video_path = tmp_path / "local_only.mp4"
    video_path.write_bytes(b"0")

    metadata = _build_file_metadata({})
    monkeypatch.setattr(video_client_module, "get_file_info", lambda path: metadata)

    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_URL", "https://example.blob.core.windows.net")
    monkeypatch.setenv("AZURE_STORAGE_VIDEO_CONTAINER", "videos")
    monkeypatch.setenv("AZURE_SEARCH_ENDPOINT", "https://example.search.windows.net")
    monkeypatch.setenv("AZURE_SEARCH_INDEX_NAME", "index")

    def fail_storage_init(env):
        raise AssertionError("Storage should not initialize for local-only runs.")

    def fail_search_init(env):
        raise AssertionError("Search should not initialize for local-only runs.")

    monkeypatch.setattr(video_client_module, "AzureStorageManager", fail_storage_init)
    monkeypatch.setattr(video_client_module, "AzureSearchUploader", fail_search_init)

    client = VideoClient(video_path=str(video_path), upload_to_azure=False)

    assert client.storage_manager is None
    assert client.search_uploader is None
