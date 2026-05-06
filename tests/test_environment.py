import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
COBRAPY_ROOT = SRC_ROOT / "cobrapy"
if str(COBRAPY_ROOT) not in sys.path:
    sys.path.insert(0, str(COBRAPY_ROOT))

if "cobra_utils" not in sys.modules:
    import types

    cobra_utils_stub = types.ModuleType("cobra_utils")

    def _noop_get_file_info(path: str) -> dict:
        return {}

    cobra_utils_stub.get_file_info = _noop_get_file_info
    sys.modules["cobra_utils"] = cobra_utils_stub

os.environ.setdefault("AZURE_SPEECH_REGION", "unit-test-region")

from cobrapy.models.environment import CobraEnvironment


VISION_ENV_VARS = {
    "AZURE_OPENAI_GPT_VISION_ENDPOINT": "https://example.openai.azure.com",
    "AZURE_OPENAI_GPT_VISION_API_KEY": "test-key",
    "AZURE_OPENAI_GPT_VISION_API_VERSION": "2024-02-01",
    "AZURE_OPENAI_GPT_VISION_DEPLOYMENT": "gpt-vision",
    "AZURE_OPENAI_GPT_VISION_MANAGED_IDENTITY_CLIENT_ID": "client-id",
}


@pytest.fixture(autouse=True)
def _prepare_env(monkeypatch):
    monkeypatch.setenv("AZURE_SPEECH_REGION", "unit-test-region")
    # Start each test without the vision configuration to avoid leakage between cases
    for env_var in VISION_ENV_VARS:
        monkeypatch.delenv(env_var, raising=False)


def test_cobra_environment_initializes_without_vision():
    env = CobraEnvironment()

    assert env.vision is None

    with pytest.raises(RuntimeError) as exc_info:
        env.require_vision()

    message = str(exc_info.value)
    assert "AZURE_OPENAI_GPT_VISION_ENDPOINT" in message
    assert "Missing environment variables" in message


def test_cobra_environment_loads_vision_when_available(monkeypatch):
    for key, value in VISION_ENV_VARS.items():
        monkeypatch.setenv(key, value)

    env = CobraEnvironment()

    vision = env.require_vision()

    assert vision.endpoint == VISION_ENV_VARS["AZURE_OPENAI_GPT_VISION_ENDPOINT"]
    assert vision.api_version == VISION_ENV_VARS["AZURE_OPENAI_GPT_VISION_API_VERSION"]
    assert vision.api_key is not None
    assert vision.managed_identity_client_id == "client-id"
    # ensure repeated calls reuse the cached configuration instead of rebuilding it
    assert env.require_vision() is vision


def test_cobra_environment_loads_vision_without_api_key_for_entra_auth(
    monkeypatch,
):
    for key, value in VISION_ENV_VARS.items():
        if key != "AZURE_OPENAI_GPT_VISION_API_KEY":
            monkeypatch.setenv(key, value)

    env = CobraEnvironment()

    vision = env.require_vision()

    assert vision.endpoint == VISION_ENV_VARS["AZURE_OPENAI_GPT_VISION_ENDPOINT"]
    assert vision.api_key is None


def test_cobra_environment_rejects_blank_required_vision_values(monkeypatch):
    for key, value in VISION_ENV_VARS.items():
        if key != "AZURE_OPENAI_GPT_VISION_API_KEY":
            monkeypatch.setenv(key, value)
    monkeypatch.setenv("AZURE_OPENAI_GPT_VISION_ENDPOINT", "")

    env = CobraEnvironment()

    with pytest.raises(RuntimeError) as exc_info:
        env.require_vision()

    assert "AZURE_OPENAI_GPT_VISION_ENDPOINT must not be blank" in str(exc_info.value)
