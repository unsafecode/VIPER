import os
import shutil
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Set, Type, Union
from urllib.parse import urlparse
from ast import literal_eval
from fractions import Fraction
from dotenv import load_dotenv
from .cobra_utils import get_file_info

from .video_preprocessor import VideoPreProcessor
from .video_analyzer import VideoAnalyzer
from .models.video import VideoManifest, SourceVideoMetadata
from .models.environment import CobraEnvironment, DEFAULT_ENV_PATH
from .analysis import AnalysisConfig
from .cobra_utils import (
    validate_video_manifest,
    write_video_manifest,
)
from .azure_integration import AzureStorageManager, AzureSearchUploader


def _coerce_fractional_number(value: Union[str, int, float, None]) -> Optional[float]:
    """Best-effort conversion of FFprobe style numeric fields to floats."""

    if value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value)

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None

        try:
            evaluated = literal_eval(text)
        except (ValueError, SyntaxError):
            evaluated = text

        if isinstance(evaluated, (int, float)):
            return float(evaluated)

        if isinstance(evaluated, str) and evaluated != text:
            return _coerce_fractional_number(evaluated)

        if isinstance(evaluated, str):
            try:
                return float(evaluated)
            except ValueError:
                try:
                    return float(Fraction(evaluated))
                except (ValueError, ZeroDivisionError):
                    return None

    return None


def _coerce_integer(value: Union[str, int, float, None]) -> Optional[int]:
    """Parse integers that may be encoded as strings or fractional values."""

    if value is None:
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        return None

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None

        try:
            evaluated = literal_eval(text)
        except (ValueError, SyntaxError):
            evaluated = text

        if isinstance(evaluated, int):
            return evaluated

        if isinstance(evaluated, float) and evaluated.is_integer():
            return int(evaluated)

        if isinstance(evaluated, str):
            try:
                return int(evaluated)
            except ValueError:
                try:
                    fraction_value = Fraction(evaluated)
                except (ValueError, ZeroDivisionError):
                    return None
                if fraction_value.denominator == 1:
                    return int(fraction_value.numerator)

    return None


class VideoClient:
    def __init__(
        self,
        video_path: Union[str, None] = None,
        manifest: Union[str, VideoManifest, None] = None,
        env_file_path: str = None,
        upload_to_azure: bool = False,
        # connection_config_list: List[Dict[str, str]] = None, # Not Implemented Yet
    ):
        # Video path is required if manifest is not provided
        if video_path is None and manifest is None:
            raise ValueError(
                "You must either provide a video_path to an input video or the manifest parameter. The manifest parameter can be a string path to a manifest json file or a VideoManifest object."
            )

        # If the environment file path is set, attempt to load the environment variables from the file
        self.env_file_path = None

        if env_file_path is not None:
            candidate_path = Path(env_file_path)
            if not candidate_path.is_file():
                candidate_path = DEFAULT_ENV_PATH.parent / env_file_path

            if candidate_path.is_file():
                load_dotenv(dotenv_path=candidate_path, override=True)
                self.env_file_path = str(candidate_path)
            else:
                raise FileNotFoundError(
                    f"Environment file not found at '{env_file_path}'."
                )

        # Load the environment variables in the pydantic model
        self.env = CobraEnvironment()

        self.upload_to_azure = upload_to_azure
        needs_storage_for_input = self._is_remote_path(video_path) or (
            isinstance(manifest, str) and self._is_remote_path(manifest)
        )
        self.storage_manager: Optional[AzureStorageManager] = None
        if self.env.storage.is_configured() and (
            self.upload_to_azure or needs_storage_for_input
        ):
            try:
                self.storage_manager = AzureStorageManager(self.env)
            except ValueError as exc:
                print(f"Azure storage configuration is incomplete: {exc}")

        self.search_uploader: Optional[AzureSearchUploader] = None
        if self.upload_to_azure and self.env.search.is_configured():
            try:
                self.search_uploader = AzureSearchUploader(self.env)
            except ValueError as exc:
                print(f"Azure search configuration is incomplete: {exc}")

        self._temporary_files: Set[str] = set()
        self._temporary_dirs: Set[str] = set()
        self._local_source_path: Optional[str] = None

        resolved_manifest = manifest
        if isinstance(resolved_manifest, str) and self._is_remote_path(resolved_manifest):
            if self.storage_manager is None:
                raise ValueError(
                    "Azure Storage configuration is required to download manifest files."
                )
            resolved_manifest = self.storage_manager.download_blob_to_tempfile(
                resolved_manifest, suffix=".json"
            )
            self._mark_temporary_file(resolved_manifest)

        resolved_video_path: Optional[str] = None
        if video_path is not None:
            resolved_video_path = self._ensure_local_video(video_path)

        if resolved_manifest is None:
            if resolved_video_path is None:
                raise ValueError(
                    "Unable to initialize VideoClient without a video source."
                )
            manifest_obj = self._prepare_video_manifest(resolved_video_path)
        else:
            manifest_obj = validate_video_manifest(resolved_manifest)
            if resolved_video_path is not None:
                manifest_obj.source_video.path = resolved_video_path
            else:
                source_path = manifest_obj.source_video.path
                if self._is_remote_path(source_path):
                    if self.storage_manager is None:
                        raise ValueError(
                            "Azure Storage configuration is required to download source video assets."
                        )
                    local_path = self.storage_manager.download_blob_to_tempfile(source_path)
                    self._mark_temporary_file(local_path)
                    manifest_obj.source_video.path = local_path
                elif not os.path.isfile(source_path):
                    raise FileNotFoundError(
                        f"File not found: {manifest_obj.source_video.path}"
                    )

        self.manifest = manifest_obj
        if os.path.isfile(self.manifest.source_video.path):
            resolved = os.path.abspath(self.manifest.source_video.path)
            self._local_source_path = resolved
            if self._is_probably_temp_file(resolved):
                self._mark_temporary_file(resolved)

        # Initialize the preprocessor and analyzer
        self.preprocessor = VideoPreProcessor(
            video_manifest=self.manifest, env=self.env
        )
        self.analyzer = VideoAnalyzer(
            video_manifest=self.manifest, env=self.env)
        self.storage_artifacts: Dict[str, Union[str, Dict[str, str]]] = {}
        self.latest_search_uploads: List[Dict[str, Union[str, None]]] = []

    def preprocess_video(
        self,
        output_directory: str = None,
        segment_length: int = 10,
        fps: float = 1,
        generate_transcripts_flag: bool = True,
        max_workers: int = None,
        trim_to_nearest_second=False,
        allow_partial_segments=True,
        overwrite_output=True,
    ):
        video_manifest_path = self.preprocessor.preprocess_video(
            output_directory=output_directory,
            segment_length=segment_length,
            fps=fps,
            generate_transcripts_flag=generate_transcripts_flag,
            max_workers=max_workers,
            trim_to_nearest_second=trim_to_nearest_second,
            allow_partial_segments=allow_partial_segments,
            overwrite_output=overwrite_output,
        )
        write_video_manifest(self.manifest)

        if self.upload_to_azure and self.storage_manager is not None:
            try:
                video_url = self.storage_manager.upload_source_video(self.manifest)
                if video_url:
                    self.storage_artifacts["video"] = video_url
            except Exception as exc:
                print(f"Failed to upload source video to Azure Storage: {exc}")

            try:
                local_manifest_path = self.manifest.video_manifest_path
                manifest_url = self.storage_manager.upload_manifest(self.manifest)
                if manifest_url:
                    self.storage_artifacts["manifest"] = manifest_url
                if local_manifest_path and os.path.isfile(local_manifest_path):
                    self._mark_temporary_file(local_manifest_path)
            except Exception as exc:
                print(f"Failed to upload manifest to Azure Storage: {exc}")

            try:
                transcript_url = self.storage_manager.upload_transcription(self.manifest)
                if transcript_url:
                    self.storage_artifacts["transcript"] = transcript_url
            except Exception as exc:
                print(f"Failed to upload transcript to Azure Storage: {exc}")

        output_dir = self.manifest.processing_params.output_directory
        if output_dir:
            self._mark_temporary_directory(output_dir)

        return video_manifest_path

    def analyze_video(
        self,
        analysis_config: Type[AnalysisConfig],
        run_async=False,
        max_concurrent_tasks=None,
        reprocess_segments=False,
        metadata: Optional[Dict[str, str]] = None,
    ):

        analysis_result = self.analyzer.analyze_video(
            analysis_config=analysis_config,
            run_async=run_async,
            max_concurrent_tasks=max_concurrent_tasks,
            reprocess_segments=reprocess_segments,
        )

        if self.upload_to_azure and self.storage_manager is not None:
            try:
                uploaded = self.storage_manager.upload_analysis_result(
                    manifest=self.manifest,
                    analysis_name=analysis_config.name,
                    analysis_result=analysis_result,
                    output_path=self.analyzer.latest_output_path,
                )
                if uploaded:
                    analyses = self.storage_artifacts.setdefault("analysis", {})
                    analyses[analysis_config.name] = uploaded
                    if "json" in uploaded:
                        self.analyzer.latest_output_path = uploaded["json"]
            except Exception as exc:
                print(f"Failed to upload analysis outputs to Azure Storage: {exc}")

            try:
                local_manifest_path = self.manifest.video_manifest_path
                manifest_url = self.storage_manager.upload_manifest(self.manifest)
                if manifest_url:
                    self.storage_artifacts["manifest"] = manifest_url
                if local_manifest_path and os.path.isfile(local_manifest_path):
                    self._mark_temporary_file(local_manifest_path)
            except Exception as exc:
                print(f"Failed to upload manifest to Azure Storage: {exc}")

        analysis_name = getattr(analysis_config, "name", "")
        self.latest_search_uploads = []
        if (
            metadata
            and self.search_uploader is not None
            and analysis_name.lower() == "actionsummary"
        ):
            action_items = []
            if isinstance(analysis_result, dict) and "results" in analysis_result:
                action_items = analysis_result.get("results", []) or []
            elif isinstance(analysis_result, list):
                action_items = analysis_result

            try:
                self.latest_search_uploads = self.search_uploader.upload_action_summary_documents(
                    manifest=self.manifest,
                    action_summary=action_items,
                    metadata=metadata,
                )
            except Exception as exc:
                print(f"Failed to upload action summary to Azure AI Search: {exc}")

        self._cleanup_local_resources()
        return analysis_result

    @staticmethod
    def _is_remote_path(path: Optional[str]) -> bool:
        if not path:
            return False
        parsed = urlparse(str(path))
        return parsed.scheme in {"http", "https"}

    @staticmethod
    def _is_probably_temp_file(path: str) -> bool:
        try:
            temp_root = Path(tempfile.gettempdir()).resolve()
            resolved_path = Path(path).resolve()
        except Exception:
            return False

        return temp_root == resolved_path.parent or temp_root in resolved_path.parents

    def _mark_temporary_file(self, path: Optional[str]) -> None:
        if not path:
            return
        self._temporary_files.add(os.path.abspath(path))

    def _mark_temporary_directory(self, path: Optional[str]) -> None:
        if not path:
            return
        self._temporary_dirs.add(os.path.abspath(path))

    def _ensure_local_video(self, video_path: str) -> str:
        if os.path.isfile(video_path):
            resolved = os.path.abspath(video_path)
            if self._is_probably_temp_file(resolved):
                self._mark_temporary_file(resolved)
            self._local_source_path = resolved
            return resolved

        if self._is_remote_path(video_path):
            if self.storage_manager is None:
                raise ValueError(
                    "Azure Storage configuration is required to download source videos."
                )
            local_path = self.storage_manager.download_blob_to_tempfile(video_path)
            self._mark_temporary_file(local_path)
            self._local_source_path = os.path.abspath(local_path)
            return self._local_source_path

        raise FileNotFoundError(f"File not found: {video_path}")

    def _cleanup_local_resources(self) -> None:
        for path in list(self._temporary_files):
            if path and os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
            self._temporary_files.discard(path)

        if not (self.upload_to_azure and self.storage_manager is not None):
            return

        output_directory = self.manifest.processing_params.output_directory
        if output_directory and os.path.isdir(output_directory):
            try:
                shutil.rmtree(output_directory)
            except OSError:
                pass
        self.manifest.processing_params.output_directory = None

        manifest_path = self.manifest.video_manifest_path
        if manifest_path and os.path.isfile(manifest_path):
            try:
                os.remove(manifest_path)
            except OSError:
                pass

        for directory in list(self._temporary_dirs):
            if directory and os.path.isdir(directory):
                try:
                    shutil.rmtree(directory)
                except OSError:
                    pass
            self._temporary_dirs.discard(directory)

    def _prepare_video_manifest(self, video_path: str, **kwargs) -> VideoManifest:

        manifest = VideoManifest()

        # Check that the video file exists
        if not os.path.isfile(video_path):
            raise FileNotFoundError(f"File not found: {video_path}")
        else:
            manifest.name = os.path.basename(video_path)
            manifest.source_video.path = os.path.abspath(video_path)

        # Get video metadata
        file_metadata = get_file_info(video_path)
        if file_metadata is not None:
            manifest_source: SourceVideoMetadata = {
                "path": video_path,
                "video_found": False,
                "size": [],
                "rotation": 0,
                "fps": 0,
                "duration": 0,
                "nframes": 0,
                "audio_found": False,
                "audio_duration": 0,
                "audio_fps": 0,
            }

            video_info = file_metadata.get("video_info") or {}
            if isinstance(video_info, dict) and video_info:
                manifest_source["video_found"] = True

                width = video_info.get("width")
                height = video_info.get("height")
                if isinstance(width, (int, float)) and isinstance(height, (int, float)):
                    manifest_source["size"] = [int(width), int(height)]

                fps_value = _coerce_fractional_number(video_info.get("fps"))
                if fps_value is not None:
                    manifest_source["fps"] = fps_value

                duration_value = _coerce_fractional_number(video_info.get("duration"))
                if duration_value is not None:
                    manifest_source["duration"] = duration_value

                frame_count = _coerce_integer(video_info.get("nb_frames"))
                if frame_count is not None:
                    manifest_source["nframes"] = frame_count

                side_data = video_info.get("side_data_list")
                if isinstance(side_data, dict) and "rotation" in side_data:
                    rotation_value = _coerce_integer(side_data.get("rotation"))
                    if rotation_value is not None:
                        manifest_source["rotation"] = rotation_value

            audio_info = file_metadata.get("audio_info") or {}
            if isinstance(audio_info, dict):
                codec_type = audio_info.get("codec_type")
                channels = _coerce_integer(audio_info.get("channels"))
                sample_rate = _coerce_integer(audio_info.get("sample_rate"))

                has_audio_stream = (
                    codec_type == "audio"
                    or (channels is not None and channels > 0)
                    or (sample_rate is not None and sample_rate > 0)
                )

                if has_audio_stream:
                    manifest_source["audio_found"] = True

                    audio_duration = _coerce_fractional_number(
                        audio_info.get("duration")
                    )
                    if audio_duration is not None:
                        manifest_source["audio_duration"] = audio_duration

                    audio_fps = _coerce_fractional_number(
                        audio_info.get("avg_frame_rate")
                    )
                    if audio_fps is not None:
                        manifest_source["audio_fps"] = audio_fps

            manifest.source_video = manifest.source_video.model_copy(
                update=manifest_source
            )

        return manifest
