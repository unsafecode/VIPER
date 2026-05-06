from __future__ import annotations

import base64
import concurrent.futures
import json
import os
import subprocess
import threading
import time
from shutil import rmtree
from typing import Iterable, Optional, Sequence, Tuple, Union


_SPEECH_TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"

from .azure_credentials import build_azure_credential
from .models.environment import CobraEnvironment
from .models.transcription import SegmentTiming, TranscriptionResult, WordTiming
from .models.video import VideoManifest


def encode_image_base64(image_path: str) -> str:
    """Return a base64 encoded representation of an image file."""

    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")


def generate_safe_dir_name(name: str) -> str:
    """Generate a filesystem safe directory name from the provided string."""

    import re

    return re.sub(r'[<>:"/\\|?*.]', "_", name).replace(" ", "_")


def _acquire_managed_identity_token(env: CobraEnvironment) -> str:
    speech_settings = env.require_speech()
    credential = build_azure_credential(
        managed_identity_client_id=speech_settings.managed_identity_client_id
    )
    try:
        return credential.get_token(_SPEECH_TOKEN_SCOPE).token
    finally:
        credential.close()


def _format_speech_authorization_token(env: CobraEnvironment, access_token: str) -> str:
    speech_settings = env.require_speech()
    return f"aad#{speech_settings.resource_id}#{access_token}"


def _create_speech_config(
    env: CobraEnvironment, *, auth_token: Optional[str] = None
):
    import azure.cognitiveservices.speech as speechsdk

    speech_settings = env.require_speech()

    if speech_settings.use_managed_identity:
        if not auth_token:
            raise RuntimeError(
                "Managed identity requires an authorization token for Azure Speech."
            )
        speech_config = speechsdk.SpeechConfig(
            auth_token=_format_speech_authorization_token(env, auth_token),
            region=speech_settings.region,
        )
    else:
        speech_config = speechsdk.SpeechConfig(
            subscription=speech_settings.api_key.get_secret_value(),
            region=speech_settings.region,
        )

    if speech_settings.endpoint:
        speech_config.set_property(
            speechsdk.PropertyId.SpeechServiceConnection_Endpoint,
            speech_settings.endpoint,
        )

    speech_config.speech_recognition_language = speech_settings.language
    speech_config.request_word_level_timestamps()
    speech_config.output_format = speechsdk.OutputFormat.Detailed

    return speech_config


def _start_managed_identity_token_refresher(
    recognizer, env: CobraEnvironment, initial_token: Optional[str] = None
):
    """Start a background thread that refreshes the speech token when needed."""

    speech_settings = env.require_speech()
    credential = build_azure_credential(
        managed_identity_client_id=speech_settings.managed_identity_client_id
    )

    def acquire_token() -> str:
        access_token = credential.get_token(_SPEECH_TOKEN_SCOPE).token
        return _format_speech_authorization_token(env, access_token)

    # Set the initial token on the recognizer.
    recognizer.authorization_token = (
        _format_speech_authorization_token(env, initial_token)
        if initial_token
        else acquire_token()
    )

    stop_event = threading.Event()

    def refresh_loop():
        # Azure Speech tokens are valid for 10 minutes. Refresh slightly earlier.
        refresh_interval = 8 * 60
        while not stop_event.wait(refresh_interval):
            try:
                recognizer.authorization_token = acquire_token()
            except Exception as exc:  # pragma: no cover - best effort refresh
                print(f"Failed to refresh speech token: {exc}")

    thread = threading.Thread(target=refresh_loop, daemon=True)
    thread.start()

    def stop():
        stop_event.set()
        thread.join(timeout=1)
        try:
            credential.close()
        except AttributeError:  # pragma: no cover - defensive cleanup
            pass

    return stop


def generate_transcript(audio_file_path: str, env: CobraEnvironment) -> TranscriptionResult:
    """Transcribe an audio file using Azure Speech and return a structured result."""

    import azure.cognitiveservices.speech as speechsdk

    if not os.path.exists(audio_file_path):
        raise FileNotFoundError(f"Audio file not found: {audio_file_path}")

    managed_identity_token: Optional[str] = None
    speech_settings = env.require_speech()

    if speech_settings.use_managed_identity:
        managed_identity_token = _acquire_managed_identity_token(env)

    speech_config = _create_speech_config(env, auth_token=managed_identity_token)
    audio_config = speechsdk.audio.AudioConfig(filename=audio_file_path)
    recognizer = speechsdk.SpeechRecognizer(
        speech_config=speech_config,
        audio_config=audio_config,
    )

    stop_refresher: Optional[callable] = None
    if speech_settings.use_managed_identity:
        stop_refresher = _start_managed_identity_token_refresher(
            recognizer,
            env,
            initial_token=managed_identity_token,
        )

    results: list[dict] = []
    done = threading.Event()

    def handle_recognized(evt):
        if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
            payload = json.loads(evt.result.json)
            results.append(payload)

    def stop_handler(_):
        done.set()

    recognizer.recognized.connect(handle_recognized)
    recognizer.session_stopped.connect(stop_handler)
    recognizer.canceled.connect(stop_handler)

    recognizer.start_continuous_recognition()
    done.wait()
    recognizer.stop_continuous_recognition_async().get()

    if stop_refresher is not None:
        stop_refresher()

    words: list[WordTiming] = []
    segments: list[SegmentTiming] = []
    text_segments: list[str] = []
    audio_duration: Optional[float] = None

    for payload in results:
        offset = payload.get("Offset", 0) / 10_000_000
        duration = payload.get("Duration", 0) / 10_000_000
        audio_duration = max(audio_duration or 0.0, offset + duration)

        alternatives = payload.get("NBest", [])
        if not alternatives:
            continue

        top_alternative = alternatives[0]
        display_text = top_alternative.get("Display", "")
        if display_text:
            text_segments.append(display_text)

        segment_words: list[WordTiming] = []
        for word_info in top_alternative.get("Words", []):
            word_start = word_info.get("Offset", 0) / 10_000_000
            word_duration = word_info.get("Duration", 0) / 10_000_000
            word_end = word_start + word_duration
            word = WordTiming(
                word=word_info.get("Word", ""),
                start=word_start,
                end=word_end,
                confidence=word_info.get("Confidence"),
            )
            words.append(word)
            segment_words.append(word)

        if segment_words:
            segments.append(
                SegmentTiming(
                    text=display_text,
                    start=segment_words[0].start,
                    end=segment_words[-1].end,
                    words=segment_words,
                )
            )
        elif display_text:
            segments.append(
                SegmentTiming(
                    text=display_text,
                    start=offset,
                    end=offset + duration,
                    words=[],
                )
            )

    transcription_text = " ".join(text_segments).strip()

    return TranscriptionResult(
        text=transcription_text,
        duration=audio_duration,
        words=words,
        segments=segments,
    )


def parse_transcript(
    transcription_object: TranscriptionResult, start_time: float, end_time: float
) -> str:
    """Extract a slice of text between the provided timestamps."""

    if not isinstance(transcription_object, TranscriptionResult):
        raise TypeError("The object passed is not of the correct type.")

    if start_time > end_time:
        raise ValueError("The start time is greater than the end time.")

    if start_time < 0:
        raise ValueError("The start time is less than 0.")

    words_in_range = [
        word.word
        for word in transcription_object.words
        if word.start >= start_time and word.end <= end_time
    ]

    return " ".join(words_in_range)


def get_file_info(video_path: str) -> Optional[dict]:
    cmd = [
        "ffprobe",
        "-i",
        video_path,
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        "-hide_banner",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        print(f"Failed to get info for file {video_path}\n{exc.stderr}", end="")
        return None

    file_info: dict = {}
    info = json.loads(result.stdout)

    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video":
            file_info["video_info"] = stream
        if stream.get("codec_type") == "audio":
            file_info["audio_info"] = stream

    return file_info


def segment_and_extract(
    start_time: float,
    end_time: float,
    input_video_path: str,
    segment_path: str,
    frames_dir: str,
    fps: float,
) -> None:
    segment_file_name = "segment.mp4"
    segment_video_path = os.path.join(segment_path, segment_file_name)
    cmd_extract_segment = [
        "ffmpeg",
        "-ss",
        str(start_time),
        "-to",
        str(end_time),
        "-i",
        input_video_path,
        "-c",
        "copy",
        segment_video_path,
        "-hide_banner",
        "-loglevel",
        "error",
    ]
    subprocess.run(cmd_extract_segment, check=True)

    output_pattern = os.path.join(frames_dir, "frame_%05d.jpg")
    cmd_extract_frames = [
        "ffmpeg",
        "-i",
        segment_video_path,
        "-vf",
        f"fps={fps}",
        "-q:v",
        "2",
        output_pattern,
        "-hide_banner",
        "-loglevel",
        "error",
    ]
    subprocess.run(cmd_extract_frames, check=True)


def extract_base_audio(video_path: str, audio_path: str) -> None:
    cmd = [
        "ffmpeg",
        "-i",
        video_path,
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
        audio_path,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
    ]
    subprocess.run(cmd, check=True)


def extract_audio_chunk(args: Tuple[str, float, float, str]):
    video_path, start, end, audio_chunk_path = args
    cmd = [
        "ffmpeg",
        "-i",
        video_path,
        "-ss",
        str(start),
        "-to",
        str(end),
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
        audio_chunk_path,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
    ]
    subprocess.run(cmd, check=True)
    return audio_chunk_path, start


def parallelize_audio(
    extract_args_list: Sequence[Tuple[str, float, float, str]],
    max_workers: int,
):
    print(f"Extracting audio chunks in parallel using {max_workers} workers...")
    with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
        extracted_chunks = list(executor.map(extract_audio_chunk, extract_args_list))
        return extracted_chunks


def parallelize_transcription(process_args_list: Sequence[Tuple[str, float]]):
    print("Processing audio chunks in parallel using 2 workers...")
    with concurrent.futures.ProcessPoolExecutor(max_workers=2) as executor:
        transcripts = list(executor.map(process_chunk, process_args_list))

    combined_transcript = transcripts[0]
    for transcript in transcripts[1:]:
        combined_transcript.extend(transcript)

    return combined_transcript


def process_chunk(args: Tuple[str, float]):
    audio_chunk_path, start_time = args
    env = CobraEnvironment()
    transcript = generate_transcript(audio_file_path=audio_chunk_path, env=env)

    for word in transcript.words:
        word.start += start_time
        word.end += start_time

    for segment in transcript.segments:
        segment.start += start_time
        segment.end += start_time

    if transcript.duration is not None:
        transcript.duration = (transcript.duration or 0) + start_time

    return transcript


def validate_video_manifest(video_manifest: Union[str, VideoManifest]) -> VideoManifest:
    if isinstance(video_manifest, str):
        if os.path.isfile(video_manifest):
            with open(video_manifest, "r", encoding="utf-8") as file:
                video_manifest = VideoManifest.model_validate_json(json_data=file.read())
            return video_manifest
        raise FileNotFoundError(f"video_manifest file not found in {video_manifest}")
    if isinstance(video_manifest, VideoManifest):
        return video_manifest
    raise ValueError("video_manifest must be a string or a VideoManifest object")


def get_elapsed_time(start_time: float) -> str:
    elapsed = time.time() - start_time
    return f"{elapsed:.1f}s"


def write_video_manifest(manifest: VideoManifest) -> None:
    video_manifest_path = os.path.join(
        manifest.processing_params.output_directory, "_video_manifest.json"
    )
    with open(video_manifest_path, "w", encoding="utf-8") as file:
        file.write(manifest.model_dump_json(indent=4))

    print(f"Video manifest for {manifest.name} saved to {video_manifest_path}")

    manifest.video_manifest_path = video_manifest_path


def prepare_outputs_directory(
    file_name: str,
    segment_length: int,
    frames_per_second: float,
    output_directory: Optional[str] = None,
    overwrite_output: bool = False,
    output_directory_prefix: str = "",
) -> str:
    if output_directory is None:
        safe_dir_name = generate_safe_dir_name(file_name)
        asset_directory_name = (
            f"{output_directory_prefix}{safe_dir_name}_{frames_per_second:.2f}fps_"
            f"{segment_length}sSegs_cobra"
        )
        asset_directory_path = os.path.join(".", asset_directory_name)
    else:
        asset_directory_path = output_directory

    if not os.path.exists(asset_directory_path):
        os.makedirs(asset_directory_path)
    else:
        if overwrite_output:
            rmtree(asset_directory_path)
            os.makedirs(asset_directory_path)
        else:
            raise FileExistsError(
                f"Directory already exists: {asset_directory_path}. If you would like to overwrite it, set overwrite_output=True"
            )
    return asset_directory_path

