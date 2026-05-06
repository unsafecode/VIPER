from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from cobrapy.analysis import ActionSummary, ChapterAnalysis  # noqa: E402
from cobrapy.video_client import VideoClient  # noqa: E402


ANALYSIS_CONFIGS = {
    "action-summary": ActionSummary,
    "chapter-analysis": ChapterAnalysis,
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run local COBRA video preprocessing and analysis against real Azure "
            "services. This command never stubs model responses."
        )
    )
    parser.add_argument("video", help="Path to the local MP4 file to analyze.")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory for manifest, frames, prompts, transcript, and analysis JSON.",
    )
    parser.add_argument(
        "--analysis",
        choices=sorted(ANALYSIS_CONFIGS),
        default="action-summary",
        help="Analysis to run after preprocessing.",
    )
    parser.add_argument(
        "--segment-length",
        type=int,
        default=10,
        help="Segment length in seconds.",
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=0.5,
        help="Frame extraction rate for analysis.",
    )
    parser.add_argument(
        "--no-transcripts",
        action="store_true",
        help="Disable Azure Speech transcription even when the video has audio.",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=1,
        help="Maximum preprocessing workers.",
    )
    parser.add_argument(
        "--env-file",
        default=None,
        help="Optional .env path to load before creating the VideoClient.",
    )
    parser.add_argument(
        "--no-overwrite",
        action="store_true",
        help="Do not overwrite an existing output directory.",
    )
    return parser.parse_args()


def _default_output_dir(video_path: Path) -> Path:
    return PROJECT_ROOT / "outputs" / video_path.stem


def _verify_ffmpeg() -> None:
    missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing:
        raise RuntimeError(
            "FFmpeg is required for local video analysis. Missing executable(s): "
            + ", ".join(missing)
            + ". Install FFmpeg and ensure ffmpeg and ffprobe are on PATH."
        )


def _summarize_run(client: VideoClient, result: Any, result_path: str | None) -> dict:
    manifest = client.manifest
    transcript = manifest.audio_transcription
    result_count = len(result) if isinstance(result, list) else None

    return {
        "video": str(Path(manifest.source_video.path).resolve()),
        "manifest": manifest.video_manifest_path,
        "output_directory": manifest.processing_params.output_directory,
        "source_audio": manifest.source_audio.path,
        "audio_found": manifest.source_video.audio_found,
        "transcript_segments": len(transcript.segments) if transcript else 0,
        "transcript_preview": (transcript.text[:500] if transcript else ""),
        "segments": len(manifest.segments),
        "analysis_result": result_path,
        "analysis_entries": result_count,
    }


def main() -> int:
    args = _parse_args()
    _verify_ffmpeg()

    video_path = Path(args.video).expanduser().resolve()
    if not video_path.is_file():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    output_dir = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else _default_output_dir(video_path)
    )

    client = VideoClient(
        video_path=str(video_path),
        env_file_path=args.env_file,
        upload_to_azure=False,
    )
    manifest_path = client.preprocess_video(
        output_directory=str(output_dir),
        segment_length=args.segment_length,
        fps=args.fps,
        generate_transcripts_flag=not args.no_transcripts,
        max_workers=args.max_workers,
        overwrite_output=not args.no_overwrite,
    )

    analysis_config = ANALYSIS_CONFIGS[args.analysis]()
    result = client.analyzer.analyze_video(
        analysis_config=analysis_config,
        run_async=False,
        reprocess_segments=False,
    )
    result_path = client.analyzer.latest_output_path

    summary = _summarize_run(client, result, result_path)
    summary["manifest"] = manifest_path
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
