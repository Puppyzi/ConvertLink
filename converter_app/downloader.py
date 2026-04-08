from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


ProgressCallback = Optional[Callable[[str], None]]
ProgressValueCallback = Optional[Callable[[int], None]]
PhaseCallback = Optional[Callable[[str], None]]

DOWNLOAD_PROGRESS_PREFIX = "__DL_PROGRESS__:"
POSTPROCESS_PROGRESS_PREFIX = "__PP_PROGRESS__:"

YOUTUBE_HOST_MARKERS = ("youtube.com", "youtu.be", "youtube-nocookie.com")
TWITTER_HOST_MARKERS = ("twitter.com", "x.com")
INSTAGRAM_HOST_MARKERS = ("instagram.com", "instagr.am")
QUICKTIME_VIDEO_CODECS = {"h264", "hevc"}
QUICKTIME_AUDIO_CODECS = {"aac", "alac", "mp3", "ac3", "eac3"}
TWITTER_STATUS_PATTERN = re.compile(
    r"https?://(?:www\.)?(?:twitter\.com|x\.com)/(?:[^/?#]+/)?(?:i/web/|i/)?status/(\d+)",
    re.IGNORECASE,
)


class DependencyError(RuntimeError):
    pass


@dataclass
class DownloadResult:
    file_path: Path
    raw_output: str


@dataclass(frozen=True)
class VideoQualityOption:
    label: str
    selector: str
    width: Optional[int]
    height: Optional[int]
    fps: Optional[float]
    estimated_size_bytes: Optional[int]
    source_note: str


@dataclass
class MediaInspectionResult:
    source_url: str
    title: str
    duration_seconds: Optional[int]
    mp4_options: list[VideoQualityOption]


def _runtime_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def _bundled_tool(*parts: str) -> Optional[str]:
    candidate = _runtime_root().joinpath(*parts)
    return str(candidate) if candidate.exists() else None


def yt_dlp_location() -> Optional[str]:
    return _bundled_tool("tools", "yt-dlp") or shutil.which("yt-dlp")


def deno_location() -> Optional[str]:
    return _bundled_tool("tools", "deno") or shutil.which("deno")


def ffmpeg_location() -> Optional[str]:
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        return ffmpeg_path

    vendor_dir = _runtime_root() / "vendor"
    if vendor_dir.exists() and str(vendor_dir) not in sys.path:
        sys.path.insert(0, str(vendor_dir))

    try:
        from imageio_ffmpeg import get_ffmpeg_exe

        return get_ffmpeg_exe()
    except Exception:
        return None


def dependency_report() -> dict[str, bool]:
    return {
        "yt_dlp": yt_dlp_location() is not None,
        "deno": deno_location() is not None,
        "ffmpeg": ffmpeg_location() is not None,
    }


def detect_source_platform(url: str) -> str:
    lowered = url.lower()
    if any(marker in lowered for marker in YOUTUBE_HOST_MARKERS):
        return "youtube"
    if any(marker in lowered for marker in TWITTER_HOST_MARKERS):
        return "twitter"
    if any(marker in lowered for marker in INSTAGRAM_HOST_MARKERS):
        return "instagram"
    return "generic"


def normalize_media_url(url: str) -> str:
    stripped = url.strip()
    match = TWITTER_STATUS_PATTERN.search(stripped)
    if match:
        return f"https://x.com/i/status/{match.group(1)}"
    return stripped


def human_readable_size(size_bytes: Optional[int]) -> str:
    if size_bytes is None or size_bytes < 0:
        return "unknown"

    value = float(size_bytes)
    units = ["B", "KB", "MB", "GB", "TB"]
    unit_index = 0

    while value >= 1024 and unit_index < len(units) - 1:
        value /= 1024
        unit_index += 1

    if unit_index == 0:
        return f"{int(value)} {units[unit_index]}"

    return f"{value:.1f} {units[unit_index]}"


def _source_display_name(source_platform: str) -> str:
    if source_platform == "youtube":
        return "YouTube"
    if source_platform == "twitter":
        return "X/Twitter"
    if source_platform == "instagram":
        return "Instagram"
    return "this"


def _as_float(value) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value) -> Optional[int]:
    numeric = _as_float(value)
    if numeric is None:
        return None
    return int(numeric)


def _normalized_text(value) -> str:
    return str(value or "").strip().lower()


def _parse_resolution_text(value: str) -> tuple[Optional[int], Optional[int]]:
    match = re.search(r"(\d{2,5})\s*[xX]\s*(\d{2,5})", value)
    if match:
        return int(match.group(1)), int(match.group(2))

    match = re.search(r"(\d{3,4})p\b", value.lower())
    if match:
        return None, int(match.group(1))

    return None, None


def _expected_extension(output_format: str) -> str:
    if output_format == "mp3":
        return "mp3"
    if output_format == "mp4":
        return "mp4"
    raise ValueError(f"Unsupported format: {output_format}")


def _find_recent_output(
    output_dir: Path,
    extension: str,
    started_at: float,
    candidate: Optional[Path],
) -> Optional[Path]:
    if candidate and candidate.exists():
        return candidate

    recent_matches = sorted(
        (
            path
            for path in output_dir.glob(f"*.{extension}")
            if path.is_file() and path.stat().st_mtime >= started_at - 2
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if recent_matches:
        return recent_matches[0]

    if candidate:
        fallback = candidate.with_suffix(f".{extension}")
        if fallback.exists():
            return fallback

    return None


def _dedupe_destination_path(destination_dir: Path, file_name: str) -> Path:
    destination = destination_dir / file_name
    if not destination.exists():
        return destination

    stem = Path(file_name).stem
    suffix = Path(file_name).suffix
    counter = 2

    while True:
        candidate = destination_dir / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _move_to_destination(source_path: Path, destination_dir: Path) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination_path = _dedupe_destination_path(destination_dir, source_path.name)
    shutil.move(str(source_path), str(destination_path))
    return destination_path


def _estimated_size_bytes(format_info: dict, fallback_duration: Optional[float]) -> Optional[int]:
    for key in ("filesize", "filesize_approx"):
        value = _as_int(format_info.get(key))
        if value and value > 0:
            return value

    duration = _as_float(format_info.get("duration")) or fallback_duration
    bitrate_kbps = (
        _as_float(format_info.get("tbr"))
        or _as_float(format_info.get("vbr"))
        or _as_float(format_info.get("abr"))
    )
    if duration and bitrate_kbps:
        return int(duration * bitrate_kbps * 1000 / 8)

    return None


def _is_audio_only(format_info: dict) -> bool:
    return not _format_has_video(format_info) and _format_has_audio(format_info)


def _video_dimensions(format_info: dict) -> tuple[Optional[int], Optional[int]]:
    width = _as_int(format_info.get("width"))
    height = _as_int(format_info.get("height"))
    if width or height:
        return width, height

    for field in ("resolution", "format_note", "format"):
        value = str(format_info.get(field) or "")
        if not value:
            continue
        parsed_width, parsed_height = _parse_resolution_text(value)
        if parsed_width or parsed_height:
            return parsed_width, parsed_height

    return None, None


def _format_has_video(format_info: dict) -> bool:
    if any(_video_dimensions(format_info)):
        return True

    vcodec = _normalized_text(format_info.get("vcodec"))
    if vcodec not in ("", "none", "unknown"):
        return True

    video_ext = _normalized_text(format_info.get("video_ext"))
    if video_ext not in ("", "none"):
        return True

    resolution = _normalized_text(format_info.get("resolution"))
    return bool(resolution and resolution != "audio only")


def _format_has_audio(format_info: dict) -> bool:
    acodec = _normalized_text(format_info.get("acodec"))
    if acodec not in ("", "none", "unknown"):
        return True

    audio_ext = _normalized_text(format_info.get("audio_ext"))
    if audio_ext not in ("", "none"):
        return True

    resolution = _normalized_text(format_info.get("resolution"))
    if resolution == "audio only":
        return True

    format_note = _normalized_text(format_info.get("format_note"))
    if format_note.startswith("audio"):
        return True

    return False


def _format_audio_status(format_info: dict) -> str:
    if _format_has_audio(format_info):
        return "present"

    acodec = _normalized_text(format_info.get("acodec"))
    if acodec == "none":
        return "missing"
    if not acodec or acodec == "unknown":
        return "unknown"
    return "present"


def _is_mp4_like_delivery(format_info: dict) -> bool:
    ext = _normalized_text(format_info.get("ext"))
    container = _normalized_text(format_info.get("container"))
    protocol = _normalized_text(format_info.get("protocol"))
    url = _normalized_text(format_info.get("url"))
    manifest_url = _normalized_text(format_info.get("manifest_url"))

    return (
        ext == "mp4"
        or "mp4" in container
        or ".mp4" in url
        or ".mp4" in manifest_url
        or protocol.startswith("m3u8")
    )


def _direct_delivery_note(format_info: dict) -> str:
    protocol = _normalized_text(format_info.get("protocol"))
    if protocol.startswith("m3u8"):
        return "HLS stream saved as MP4"
    if _normalized_text(format_info.get("ext")) == "mp4":
        return "direct MP4 variant"
    return "single video stream"


def _preferred_audio_formats(formats: list[dict]) -> list[dict]:
    preferred_exts = {"m4a", "mp4", "aac"}
    preferred = [fmt for fmt in formats if fmt.get("ext") in preferred_exts]
    return preferred or formats


def _audio_sort_key(format_info: dict, duration_seconds: Optional[float]) -> tuple:
    ext_priority = 1 if format_info.get("ext") in {"m4a", "mp4", "aac"} else 0
    bitrate = _as_float(format_info.get("abr")) or _as_float(format_info.get("tbr")) or 0
    size = _estimated_size_bytes(format_info, duration_seconds) or 0
    return (ext_priority, bitrate, size)


def _select_best_audio_format(
    formats: list[dict], duration_seconds: Optional[float]
) -> Optional[dict]:
    audio_formats = [fmt for fmt in formats if _is_audio_only(fmt)]
    if not audio_formats:
        return None

    preferred = _preferred_audio_formats(audio_formats)
    return max(preferred, key=lambda fmt: _audio_sort_key(fmt, duration_seconds))


def _select_best_audio_source_format(
    formats: list[dict], duration_seconds: Optional[float]
) -> Optional[dict]:
    audio_only = _select_best_audio_format(formats, duration_seconds)
    if audio_only:
        return audio_only

    formats_with_audio = [fmt for fmt in formats if _format_has_audio(fmt)]
    if not formats_with_audio:
        return None

    preferred = _preferred_audio_formats(formats_with_audio)
    return max(preferred, key=lambda fmt: _audio_sort_key(fmt, duration_seconds))


def _quality_label(height: Optional[int], width: Optional[int], fps: Optional[float]) -> str:
    if height:
        label = f"{height}p"
    elif width:
        label = f"{width}px wide"
    else:
        label = "Best available"

    if width and height:
        label += f" ({width}x{height})"

    fps_value = _as_int(fps)
    if fps_value and fps_value >= 50:
        label += f" {fps_value}fps"

    return label


def _option_sort_key(option: VideoQualityOption) -> tuple:
    height = option.height or 0
    fps = _as_int(option.fps) or 0
    size = option.estimated_size_bytes or 0
    return (height, fps, size)


def _candidate_key(format_info: dict) -> tuple:
    _width, height = _video_dimensions(format_info)
    height = height or 0
    fps = _as_int(format_info.get("fps")) or 0
    fps_bucket = fps if fps >= 50 else 30 if fps else 0
    return (height, fps_bucket)


def _build_direct_option(
    format_info: dict, duration_seconds: Optional[float]
) -> VideoQualityOption:
    width, height = _video_dimensions(format_info)
    return VideoQualityOption(
        label=_quality_label(
            height,
            width,
            _as_float(format_info.get("fps")),
        ),
        selector=str(format_info.get("format_id")),
        width=width,
        height=height,
        fps=_as_float(format_info.get("fps")),
        estimated_size_bytes=_estimated_size_bytes(format_info, duration_seconds),
        source_note=_direct_delivery_note(format_info),
    )


def _build_merged_option(
    video_format: dict,
    audio_format: dict,
    duration_seconds: Optional[float],
) -> VideoQualityOption:
    width, height = _video_dimensions(video_format)
    video_size = _estimated_size_bytes(video_format, duration_seconds)
    audio_size = _estimated_size_bytes(audio_format, duration_seconds)
    combined_size = None
    if video_size is not None and audio_size is not None:
        combined_size = video_size + audio_size

    return VideoQualityOption(
        label=_quality_label(
            height,
            width,
            _as_float(video_format.get("fps")),
        ),
        selector=f"{video_format.get('format_id')}+{audio_format.get('format_id')}",
        width=width,
        height=height,
        fps=_as_float(video_format.get("fps")),
        estimated_size_bytes=combined_size,
        source_note="video + audio merged into MP4",
    )


def _base_yt_dlp_command() -> list[str]:
    yt_dlp_path = yt_dlp_location()
    if not yt_dlp_path:
        raise DependencyError("yt-dlp is missing from the app bundle.")

    command = [
        yt_dlp_path,
        "--no-update",
        "--no-warnings",
        "--no-playlist",
        "--newline",
        "--progress",
        "--progress-delta",
        "0.5",
        "--progress-template",
        f"download:{DOWNLOAD_PROGRESS_PREFIX}%(progress._percent_str)s",
        "--progress-template",
        f"postprocess:{POSTPROCESS_PROGRESS_PREFIX}%(progress.postprocessor)s:%(progress.status)s",
        "--ignore-config",
        "--cache-dir",
        str(_runtime_root() / ".yt-dlp-cache"),
    ]

    deno_path = deno_location()
    if deno_path:
        command.extend(["--js-runtimes", f"deno:{deno_path}"])

    return command


def _augment_error_message(message: str) -> str:
    lowered = message.lower()
    if "403" in lowered and "youtube" in lowered:
        return (
            message
            + "\n\nYouTube blocked the request. This app now uses the newer yt-dlp + Deno path, "
            "but some videos may still require browser cookies or YouTube may be rate-limiting the IP."
        )
    return message


def _extract_progress_value(line: str) -> Optional[int]:
    match = re.search(r"(\d+(?:\.\d+)?)%", line)
    if not match:
        return None

    try:
        raw_value = float(match.group(1))
    except ValueError:
        return None

    if raw_value >= 100:
        return 99
    if raw_value < 0:
        return 0
    return int(raw_value)


def _friendly_postprocess_message(postprocessor: str, status: str) -> tuple[str, str]:
    normalized_status = (status or "").strip().lower()

    if "extractaudio" in postprocessor.lower():
        label = "Converting audio..."
        log = f"Post-processing: converting audio ({normalized_status or 'working'})"
        return label, log

    if "merger" in postprocessor.lower():
        label = "Merging video..."
        log = f"Post-processing: merging video ({normalized_status or 'working'})"
        return label, log

    label = "Processing file..."
    log = f"Post-processing: {postprocessor} ({normalized_status or 'working'})"
    return label, log


def _probe_primary_stream_codecs(
    file_path: Path, ffmpeg_path: str
) -> tuple[Optional[str], Optional[str]]:
    result = subprocess.run(
        [ffmpeg_path, "-hide_banner", "-i", str(file_path)],
        capture_output=True,
        text=True,
    )
    details = "\n".join(part for part in (result.stderr, result.stdout) if part)

    video_match = re.search(r"Stream #.*?: Video: ([^,\s]+)", details)
    audio_match = re.search(r"Stream #.*?: Audio: ([^,\s]+)", details)
    video_codec = _normalized_text(video_match.group(1)) if video_match else None
    audio_codec = _normalized_text(audio_match.group(1)) if audio_match else None
    return video_codec, audio_codec


def _quicktime_incompatibility_reason(
    file_path: Path, ffmpeg_path: Optional[str]
) -> Optional[str]:
    if not ffmpeg_path:
        return None

    video_codec, audio_codec = _probe_primary_stream_codecs(file_path, ffmpeg_path)
    if not video_codec:
        return None

    if video_codec not in QUICKTIME_VIDEO_CODECS:
        return f"{video_codec.upper()} video"

    if audio_codec and audio_codec not in QUICKTIME_AUDIO_CODECS:
        return f"{audio_codec.upper()} audio"

    return None


def _transcode_mp4_for_quicktime(
    source_path: Path,
    ffmpeg_path: str,
) -> Path:
    transcoded_path = source_path.with_name(
        f"{source_path.stem}.quicktime{source_path.suffix}"
    )
    command = [
        ffmpeg_path,
        "-y",
        "-hide_banner",
        "-i",
        str(source_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-map_metadata",
        "0",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(transcoded_path),
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0 or not transcoded_path.exists():
        details = "\n".join(part for part in (result.stderr, result.stdout) if part).strip()
        raise RuntimeError(
            "The MP4 downloaded successfully, but converting it to a QuickTime-friendly "
            f"H.264/AAC file failed.\n\n{details or 'ffmpeg exited with an unknown error.'}"
        )

    source_path.unlink(missing_ok=True)
    transcoded_path.replace(source_path)
    return source_path


def _load_media_info(url: str, ffmpeg_path: Optional[str]) -> dict:
    command = _base_yt_dlp_command() + ["--dump-single-json", url]
    if ffmpeg_path:
        command.extend(["--ffmpeg-location", ffmpeg_path])

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        message = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
        raise RuntimeError(_augment_error_message(message.strip() or "Failed to inspect media."))

    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("yt-dlp returned unreadable format data.") from exc

    if isinstance(info, dict) and info.get("entries"):
        entries = info.get("entries") or []
        info = entries[0] if entries else info

    if not isinstance(info, dict):
        raise RuntimeError("Unable to inspect this link.")

    return info


def inspect_media(url: str, progress_callback: ProgressCallback = None) -> MediaInspectionResult:
    url = normalize_media_url(url)
    ffmpeg_path = ffmpeg_location()
    source_platform = detect_source_platform(url)

    if progress_callback:
        progress_callback("Inspecting available MP4 qualities...")
    info = _load_media_info(url, ffmpeg_path)

    formats = info.get("formats") or []
    duration_seconds = _as_float(info.get("duration"))
    audio_format = _select_best_audio_format(formats, duration_seconds) if ffmpeg_path else None

    options_by_key: dict[tuple, VideoQualityOption] = {}

    for format_info in formats:
        candidate: Optional[VideoQualityOption] = None
        width, height = _video_dimensions(format_info)
        if not (width or height):
            continue
        if not _format_has_video(format_info) or not _is_mp4_like_delivery(format_info):
            continue

        audio_status = _format_audio_status(format_info)
        allow_unknown_audio = source_platform in {"twitter", "instagram"} or _normalized_text(
            format_info.get("protocol")
        ).startswith("m3u8")
        if audio_status == "present" or (
            audio_status == "unknown" and allow_unknown_audio
        ):
            candidate = _build_direct_option(format_info, duration_seconds)
        elif audio_format:
            candidate = _build_merged_option(format_info, audio_format, duration_seconds)

        if not candidate:
            continue

        key = _candidate_key(format_info)
        existing = options_by_key.get(key)
        if not existing or _option_sort_key(candidate) > _option_sort_key(existing):
            options_by_key[key] = candidate

    options = sorted(options_by_key.values(), key=_option_sort_key, reverse=True)

    if not options:
        source_label = _source_display_name(source_platform)
        raise RuntimeError(
            f"No MP4 quality options were found for {source_label} link. "
            "Try another link or install ffmpeg for broader format support."
        )

    if progress_callback:
        progress_callback(f"Found {len(options)} MP4 quality options.")

    return MediaInspectionResult(
        source_url=url,
        title=str(info.get("title") or "Untitled video"),
        duration_seconds=_as_int(duration_seconds),
        mp4_options=options,
    )


def download_media(
    url: str,
    output_format: str,
    output_dir: Path,
    progress_callback: ProgressCallback = None,
    progress_value_callback: ProgressValueCallback = None,
    phase_callback: PhaseCallback = None,
    mp4_selector: Optional[str] = None,
    mp4_label: Optional[str] = None,
) -> DownloadResult:
    url = normalize_media_url(url)
    ffmpeg_path = ffmpeg_location()
    source_platform = detect_source_platform(url)

    if output_format == "mp3" and not ffmpeg_path:
        raise DependencyError(
            "MP3 conversion needs ffmpeg. Install it with "
            "'python3 -m pip install --target vendor -r requirements.txt'."
        )

    if output_format == "mp4" and mp4_selector and "+" in mp4_selector and not ffmpeg_path:
        raise DependencyError(
            "That MP4 quality needs ffmpeg to merge video and audio. Install it with "
            "'python3 -m pip install --target vendor -r requirements.txt'."
        )

    staging_dir = Path(tempfile.mkdtemp(prefix=".convertlink-", dir=str(output_dir)))
    started_at = time.time()
    command = _base_yt_dlp_command() + [
        "-P",
        str(staging_dir),
        "-o",
        "%(title).180B.%(ext)s",
        "--print",
        "after_move:__FINAL_PATH__:%(filepath)s",
    ]

    if ffmpeg_path:
        command.extend(["--ffmpeg-location", ffmpeg_path])

    if output_format == "mp3":
        audio_selector = None
        if source_platform == "twitter":
            if progress_callback:
                progress_callback("Checking X/Twitter audio track...")
            if phase_callback:
                phase_callback("Preparing audio...")

            info = _load_media_info(url, ffmpeg_path)
            formats = info.get("formats") or []
            duration_seconds = _as_float(info.get("duration"))
            audio_source = _select_best_audio_source_format(formats, duration_seconds)
            if not audio_source:
                raise RuntimeError(
                    "This X/Twitter post does not expose an audio track, so MP3 is not available for this link."
                )
            audio_selector = str(audio_source.get("format_id"))

        command.extend(
            [
                "--extract-audio",
                "--audio-format",
                "mp3",
                "--audio-quality",
                "0",
                "-f",
                audio_selector or "ba/b",
            ]
        )
    elif output_format == "mp4":
        if ffmpeg_path:
            command.extend(["--remux-video", "mp4"])
        if mp4_selector:
            command.extend(["-f", mp4_selector])
            if "+" in mp4_selector:
                command.extend(["--merge-output-format", "mp4"])
        elif ffmpeg_path:
            command.extend(
                [
                    "-f",
                    "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
                    "--merge-output-format",
                    "mp4",
                ]
            )
        else:
            command.extend(["-f", "b[ext=mp4]/best[ext=mp4]/best"])
    else:
        raise ValueError(f"Unsupported format: {output_format}")

    command.append(url)

    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        if process.stdout is None:
            raise RuntimeError("Failed to capture downloader output.")

        output_lines: list[str] = []
        final_path: Optional[Path] = None
        last_progress_value: Optional[int] = None
        last_phase_log: Optional[str] = None

        for raw_line in process.stdout:
            line = raw_line.rstrip()
            if not line:
                continue

            if line.startswith(DOWNLOAD_PROGRESS_PREFIX):
                progress_value = _extract_progress_value(line)
                if progress_value is not None and progress_value_callback:
                    if progress_value != last_progress_value:
                        progress_value_callback(progress_value)
                        last_progress_value = progress_value
                if phase_callback:
                    phase_callback("download")
                continue

            if line.startswith(POSTPROCESS_PROGRESS_PREFIX):
                payload = line.replace(POSTPROCESS_PROGRESS_PREFIX, "", 1)
                postprocessor, _, status = payload.partition(":")
                phase_label, phase_log = _friendly_postprocess_message(
                    postprocessor, status
                )
                if phase_callback:
                    phase_callback(phase_label)
                if progress_callback and phase_log != last_phase_log:
                    progress_callback(phase_log)
                if phase_log != last_phase_log:
                    output_lines.append(phase_log)
                    last_phase_log = phase_log
                continue

            output_lines.append(line)
            if progress_callback:
                progress_callback(line)

            if line.startswith("__FINAL_PATH__:"):
                raw_path = line.replace("__FINAL_PATH__:", "", 1).strip()
                candidate = Path(raw_path)
                final_path = candidate if candidate.is_absolute() else staging_dir / candidate

        return_code = process.wait()
        combined_output = "\n".join(output_lines)

        if return_code != 0:
            raise RuntimeError(
                _augment_error_message(
                    combined_output or "yt-dlp exited with an unknown error."
                )
            )

        if not final_path:
            final_path = _find_recent_output(
                output_dir=staging_dir,
                extension=_expected_extension(output_format),
                started_at=started_at,
                candidate=None,
            )

        if not final_path:
            raise RuntimeError(
                "The download finished, but the saved file could not be located."
            )

        if output_format == "mp4" and ffmpeg_path:
            incompatibility = _quicktime_incompatibility_reason(final_path, ffmpeg_path)
            if incompatibility:
                if phase_callback:
                    phase_callback("Optimizing MP4...")
                if progress_callback:
                    progress_callback(
                        "Downloaded MP4 uses "
                        f"{incompatibility}, so it is being converted to H.264/AAC for "
                        "better QuickTime compatibility..."
                    )
                final_path = _transcode_mp4_for_quicktime(final_path, ffmpeg_path)
                if progress_callback:
                    progress_callback("QuickTime-friendly MP4 conversion finished.")

        final_output_path = _move_to_destination(final_path, output_dir)

        if phase_callback:
            phase_callback("Complete")
        if progress_value_callback:
            progress_value_callback(100)

        return DownloadResult(file_path=final_output_path, raw_output=combined_output)
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)
