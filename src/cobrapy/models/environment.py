from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

from pydantic import (
    SecretStr,
    field_validator,
    model_validator,
    Field,
    ValidationError,
    PrivateAttr,
)



def _find_project_root(marker: str = "pyproject.toml") -> Path:
    current_path = Path(__file__).resolve()
    for parent in current_path.parents:
        if (parent / marker).exists():
            return parent
    return current_path.parent


PROJECT_ROOT = _find_project_root()
PACKAGE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"
PACKAGE_ENV_PATH = PACKAGE_ROOT / ".env"


def _load_env_files() -> list[Path]:
    """Load default environment files and return the ones that were found."""

    loaded: list[Path] = []
    for candidate in (DEFAULT_ENV_PATH, PACKAGE_ENV_PATH):
        if candidate.is_file() and candidate not in loaded:
            load_dotenv(candidate, override=False)
            loaded.append(candidate)
    return loaded


LOADED_ENV_PATHS = _load_env_files()
ENV_FILES_FOR_SETTINGS = tuple(str(path) for path in LOADED_ENV_PATHS)


def _format_missing_env_error(*, exc: ValidationError, env_prefix: str) -> str:
    missing_fields: list[str] = []

    for error in exc.errors():
        if error.get("type") != "missing":
            continue

        loc = error.get("loc") or ()
        if not loc:
            continue

        field = str(loc[0])
        missing_fields.append(field)

    if not missing_fields:
        return str(exc)

    formatted = [f"{env_prefix}{field.upper()}" for field in missing_fields]
    return (
        "Missing environment variables: "
        + ", ".join(formatted)
        + ". Please set these environment variables before proceeding."
    )


class GPTVision(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AZURE_OPENAI_GPT_VISION_",
        extra="ignore",
    )

    endpoint: str
    api_key: Optional[SecretStr] = Field(
        default=None,
        description=(
            "Optional Azure OpenAI API key. When omitted, Entra ID authentication "
            "is used through the shared Azure credential chain."
        ),
    )
    api_version: str
    deployment: str
    managed_identity_client_id: Optional[str] = Field(
        default=None,
        description="Optional client id when multiple managed identities are available.",
    )

    @field_validator("endpoint", "api_version", "deployment", mode="before")
    @classmethod
    def validate_required_string(cls, value, info):
        if isinstance(value, str) and not value.strip():
            raise ValueError(
                f"AZURE_OPENAI_GPT_VISION_{info.field_name.upper()} must not be blank."
            )
        return value

    @field_validator("api_key", "managed_identity_client_id", mode="before")
    @classmethod
    def normalize_blank_optional_strings(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value


class AzureSpeech(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AZURE_SPEECH_",
        extra="ignore",
    )

    region: str
    endpoint: Optional[str] = None
    resource_id: Optional[str] = Field(
        default=None,
        description=(
            "Azure resource ID for Entra ID Speech SDK authentication. Required "
            "by the Speech SDK when using managed identity authorization tokens."
        ),
    )
    use_managed_identity: bool = Field(
        default=True,
        description="Whether managed identity should be used instead of an API key.",
    )
    api_key: Optional[SecretStr] = Field(
        default=None,
        description="Optional API key if managed identity is not available.",
    )
    managed_identity_client_id: Optional[str] = Field(
        default=None,
        description="Optional client id when multiple managed identities are available.",
    )
    language: str = Field(
        default="en-US", description="Language to use for speech recognition."
    )

    @field_validator(
        "endpoint",
        "resource_id",
        "api_key",
        "managed_identity_client_id",
        mode="before",
    )
    @classmethod
    def normalize_blank_optional_strings(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def validate_configuration(self):
        if not self.use_managed_identity and not self.api_key:
            raise ValueError(
                "AZURE_SPEECH_API_KEY must be provided when managed identity is disabled."
            )
        return self


class AzureStorage(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AZURE_STORAGE_",
        extra="ignore",
    )

    account_url: Optional[str] = None
    account_name: Optional[str] = None
    account_key: Optional[SecretStr] = None
    connection_string: Optional[SecretStr] = None
    video_container: Optional[str] = None
    output_container: Optional[str] = None
    managed_identity_client_id: Optional[str] = None

    def is_configured(self) -> bool:
        return bool(self.connection_string or self.account_url)


class AzureAISearch(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AZURE_SEARCH_",
        extra="ignore",
    )

    endpoint: Optional[str] = None
    index_name: Optional[str] = None
    api_key: Optional[SecretStr] = None
    managed_identity_client_id: Optional[str] = None

    def is_configured(self) -> bool:
        return bool(self.endpoint and self.index_name)
class CobraEnvironment(BaseSettings):
    """Environment configuration for the Cobra backend."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILES_FOR_SETTINGS if ENV_FILES_FOR_SETTINGS else None,
        extra="ignore",
    )

    vision: Optional[GPTVision] = Field(
        default=None,
        description=(
            "Azure OpenAI vision configuration. ``None`` indicates that the required "
            "environment variables were not provided."
        ),
    )
    speech: Optional[AzureSpeech] = Field(
        default=None,
        description=(
            "Azure Speech configuration. ``None`` indicates that the required "
            "environment variables were not provided."
        ),
    )
    storage: AzureStorage = Field(default_factory=AzureStorage)
    search: AzureAISearch = Field(default_factory=AzureAISearch)

    _vision_error: Optional[str] = PrivateAttr(default=None)
    _speech_error: Optional[str] = PrivateAttr(default=None)

    @model_validator(mode="after")
    def _load_optional_settings(self) -> "CobraEnvironment":
        """Populate optional settings when their environment variables exist."""

        if self.vision is None:
            self.vision = self._refresh_vision_settings()

        if self.speech is None:
            self.speech = self._refresh_speech_settings()

        return self

    def _refresh_vision_settings(self) -> Optional[GPTVision]:
        """Attempt to create a GPTVision instance and cache any validation errors."""

        try:
            vision = GPTVision()
        except ValidationError as exc:
            config = getattr(GPTVision, "model_config", {}) or {}
            prefix = config.get("env_prefix", "") if hasattr(config, "get") else ""
            self._vision_error = _format_missing_env_error(
                exc=exc,
                env_prefix=prefix,
            )
            return None
        except ValueError as exc:  # ValueError raised inside validators
            self._vision_error = str(exc)
            return None

        self._vision_error = None
        return vision

    def _refresh_speech_settings(self) -> Optional[AzureSpeech]:
        """Attempt to create Azure Speech settings and cache validation errors."""

        try:
            speech = AzureSpeech()
        except ValidationError as exc:
            config = getattr(AzureSpeech, "model_config", {}) or {}
            prefix = config.get("env_prefix", "") if hasattr(config, "get") else ""
            self._speech_error = _format_missing_env_error(
                exc=exc,
                env_prefix=prefix,
            )
            return None
        except ValueError as exc:
            self._speech_error = str(exc)
            return None

        self._speech_error = None
        return speech

    def require_vision(self) -> GPTVision:
        """Return the configured vision settings or raise a helpful error."""

        vision = self.vision or self._refresh_vision_settings()
        if vision is None:
            message = (
                "Azure OpenAI vision environment variables are missing. Set "
                "AZURE_OPENAI_GPT_VISION_ENDPOINT, "
                "AZURE_OPENAI_GPT_VISION_API_VERSION, and "
                "AZURE_OPENAI_GPT_VISION_DEPLOYMENT before invoking video analysis "
                "endpoints. AZURE_OPENAI_GPT_VISION_API_KEY is optional; when it is "
                "not set, Entra ID authentication is used."
            )

            if self._vision_error:
                message = f"{message}\nValidation details: {self._vision_error}"
            raise RuntimeError(message)

        self.vision = vision
        return vision

    def require_speech(self) -> AzureSpeech:
        """Return configured Speech settings or raise a helpful error."""

        speech = self.speech or self._refresh_speech_settings()
        if speech is None:
            message = (
                "Azure Speech environment variables are missing. Set "
                "AZURE_SPEECH_REGION before generating transcripts. "
                "When AZURE_SPEECH_USE_MANAGED_IDENTITY is true, also set "
                "AZURE_SPEECH_RESOURCE_ID."
            )

            if self._speech_error:
                message = f"{message}\nValidation details: {self._speech_error}"
            raise RuntimeError(message)

        if speech.use_managed_identity and not speech.resource_id:
            raise RuntimeError(
                "AZURE_SPEECH_RESOURCE_ID must be set when using Entra ID "
                "authentication for Azure Speech."
            )

        self.speech = speech
        return speech

