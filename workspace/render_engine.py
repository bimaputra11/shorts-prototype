from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import cv2
import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageFont
import PIL

import render_utils
from face_clearance import FaceClearance
from quality_validation import validate_hook, validate_profile_content

WIDTH = render_utils.WIDTH
HEIGHT = render_utils.HEIGHT
FPS = render_utils.FPS
HOOK_FRAMES = render_utils.HOOK_FRAMES
MAX_INPUT_BYTES = 2_000_000_000

# Visual chunks are independently encoded and reused on later renders.
CACHE_SCHEMA_VERSION = 1
CACHE_CHUNK_FRAMES = 60  # Two seconds at 30 fps.
MAX_SEGMENT_CACHE_BYTES = 2_000_000_000

VIDEO_CODEC = "libx264"
ENCODER_PRESET = "veryfast"
ENCODER_CRF = 18
PIXEL_FORMAT = "yuv420p"
VIDEO_TRACK_TIMESCALE = 90000


def _workspace_path(workspace: Path, value: str, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty workspace-relative path.")
    raw = Path(value)
    if raw.is_absolute() or ".." in raw.parts:
        raise ValueError(f"{label} must stay inside the workspace.")
    resolved = (workspace / raw).resolve()
    if resolved != workspace and workspace not in resolved.parents:
        raise ValueError(f"{label} resolves outside the workspace.")
    return resolved


def _load_profile(profile_path: Path) -> dict[str, Any]:
    if not profile_path.is_file():
        raise FileNotFoundError(f"Project configuration not found: {profile_path}")
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Project configuration is not valid JSON: {exc}") from exc
    if not isinstance(profile, dict):
        raise ValueError("Project configuration root must be a JSON object.")
    if profile.get("schema_version") not in {1, 2}:
        raise ValueError("Unsupported project configuration schema_version. Supported versions are 1 and 2.")
    return profile


def _validate_hook(hook_text: str) -> str:
    return validate_hook(hook_text)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _stable_fingerprint(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value).strip())
    slug = re.sub(r"-{2,}", "-", slug).strip("-._")
    return (slug[:80] or "render").lower()


def _ffmpeg_identity(ffmpeg_exe: str) -> dict[str, Any]:
    executable = Path(ffmpeg_exe).resolve()
    stat = executable.stat()
    result = subprocess.run(
        [str(executable), "-version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Could not identify the FFmpeg executable used for rendering: "
            + result.stderr[-1000:]
        )
    version = (result.stdout or "").splitlines()
    return {
        "version_line": version[0].strip() if version else "unknown",
        "file_size_bytes": stat.st_size,
        "modified_ns": stat.st_mtime_ns,
    }


def _render_fingerprint_context(ffmpeg_exe: str) -> dict[str, Any]:
    font_path = Path(render_utils.FONT_PATH).resolve()
    if not font_path.is_file():
        raise FileNotFoundError(f"Caption font not found: {font_path}")

    return {
        "cache_schema_version": CACHE_SCHEMA_VERSION,
        "canvas": [WIDTH, HEIGHT],
        "fps": FPS,
        "hook_frames": HOOK_FRAMES,
        "chunk_frames": CACHE_CHUNK_FRAMES,
        "encoder": {
            "codec": VIDEO_CODEC,
            "preset": ENCODER_PRESET,
            "crf": ENCODER_CRF,
            "pixel_format": PIXEL_FORMAT,
            "video_track_timescale": VIDEO_TRACK_TIMESCALE,
        },
        "font_sha256": _sha256_file(font_path),
        # Conservative invalidation: any renderer or shared drawing-code change
        # forces fresh chunks rather than risking reuse of outdated pixels.
        "render_engine_sha256": _sha256_file(Path(__file__).resolve()),
        "render_utils_sha256": _sha256_file(Path(render_utils.__file__).resolve()),
        "face_clearance_sha256": _sha256_file(Path(__file__).resolve().parent / "face_clearance.py"),
        "quality_validation_sha256": _sha256_file(Path(__file__).resolve().parent / "quality_validation.py"),
        "opencv_version": cv2.__version__,
        "pillow_version": PIL.__version__,
        "numpy_version": np.__version__,
        "ffmpeg": _ffmpeg_identity(ffmpeg_exe),
    }


def _load_camera_keyframes(path: Path) -> list[tuple[float, np.ndarray]]:
    if not path.is_file():
        raise FileNotFoundError(f"Camera keyframes file not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = data.get("keyframes") if isinstance(data, dict) else None
    if not isinstance(raw, list) or len(raw) < 2:
        raise ValueError("Camera keyframes must contain at least two keyframes.")
    result: list[tuple[float, np.ndarray]] = []
    last_time = -math.inf
    for item in raw:
        timestamp = float(item["source_seconds"])
        matrix = np.asarray(item["homography"], dtype=np.float64)
        if not math.isfinite(timestamp) or timestamp <= last_time:
            raise ValueError("Camera keyframe timestamps must be finite and strictly increasing.")
        if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
            raise ValueError(f"Invalid camera homography at {timestamp:.3f}s.")
        if abs(float(matrix[2, 2])) < 1e-10:
            raise ValueError(f"Camera homography at {timestamp:.3f}s cannot be normalized.")
        matrix = matrix / matrix[2, 2]
        if abs(float(np.linalg.det(matrix))) < 1e-12:
            raise ValueError(f"Camera homography at {timestamp:.3f}s is singular.")
        result.append((timestamp, matrix))
        last_time = timestamp
    return result


def _camera_matrix(source_time: float, keyframes: list[tuple[float, np.ndarray]]) -> np.ndarray:
    if source_time <= keyframes[0][0]:
        return keyframes[0][1]
    if source_time >= keyframes[-1][0]:
        return keyframes[-1][1]
    for (t0, m0), (t1, m1) in zip(keyframes, keyframes[1:]):
        if t0 <= source_time <= t1:
            progress = (source_time - t0) / max(1e-9, t1 - t0)
            eased = progress * progress * (3.0 - 2.0 * progress)
            matrix = (1.0 - eased) * m0 + eased * m1
            return matrix / matrix[2, 2]
    return keyframes[-1][1]


def _warp_clean_artwork(
    artwork_bgr: np.ndarray,
    homography: np.ndarray,
) -> np.ndarray:
    # Linear interpolation is about 2.1x faster than cubic on the target laptop;
    # a 120-frame comparison found about 1/255 mean pixel difference.
    return cv2.warpPerspective(
        artwork_bgr, homography, (WIDTH, HEIGHT),
        flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT_101,
    )


def _validate_timed_captions(raw: Any, start: float, end: float) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ValueError("A motion segment's captions must be an array.")
    captions: list[dict[str, Any]] = []
    previous_start = -math.inf
    for item in raw:
        caption_start = float(item["start_seconds"])
        caption_end = float(item["end_seconds"])
        text = str(item["text"]).strip()
        y = float(item.get("y", 0.65))
        safe_bottom = item.get("safe_bottom_fraction")
        if safe_bottom is not None:
            safe_bottom = float(safe_bottom)
            if not 0.65 <= safe_bottom <= 0.98:
                raise ValueError("Timed caption safe_bottom_fraction must be between 0.65 and 0.98.")
        if not text or not (start <= caption_start < caption_end <= end + 1e-6):
            raise ValueError("Timed captions must have text and fit inside their motion segment.")
        if caption_start < previous_start:
            raise ValueError("Timed captions must be sorted by start_seconds.")
        if not 0.0 <= y <= 1.0:
            raise ValueError("Caption y must be between 0 and 1.")
        font = ImageFont.truetype(str(render_utils.FONT_PATH), render_utils.CAPTION_FONT_SIZE_PX)
        render_utils.wrap_caption(text, font)
        captions.append({
            "start_seconds": caption_start,
            "end_seconds": caption_end,
            "text": text,
            "y": y,
            "safe_bottom_fraction": safe_bottom,
        })
        previous_start = caption_start
    return captions


def _static_face_frames(segment: dict[str, Any]) -> Iterator[tuple[np.ndarray, tuple[int, int, int, int]]]:
    if segment.get("frame") is None:
        with Image.open(segment["asset"]) as source:
            composed = render_utils.compose_full_frame(source)
            segment["frame"] = np.asarray(composed, dtype=np.uint8)
    source_width, source_height = segment["dimensions"]
    scale = min(WIDTH / source_width, HEIGHT / source_height)
    foreground_width = max(1, round(source_width * scale))
    foreground_height = max(1, round(source_height * scale))
    foreground_x = (WIDTH - foreground_width) // 2
    foreground_y = (HEIGHT - foreground_height) // 2
    frame_bgr = cv2.cvtColor(segment["frame"], cv2.COLOR_RGB2BGR)
    yield frame_bgr, (
        foreground_x,
        foreground_y,
        foreground_width,
        foreground_height,
    )


def _keyframed_face_frames(
    segment: dict[str, Any],
    caption: dict[str, Any],
) -> Iterator[tuple[np.ndarray, None]]:
    if segment.get("artwork_bgr") is None:
        artwork = cv2.imread(segment["asset"], cv2.IMREAD_COLOR)
        if artwork is None:
            raise RuntimeError(f"Could not decode clean artwork: {segment['asset_name']}")
        segment["artwork_bgr"] = artwork
    start = float(caption["start_seconds"])
    end = float(caption["end_seconds"])
    final_sample = max(start, end - 1.0 / FPS)
    sample_count = min(16, max(3, int(math.ceil((final_sample - start) / 0.5)) + 1))
    sample_times = [
        start + (final_sample - start) * index / (sample_count - 1)
        for index in range(sample_count)
    ]
    for source_time in sample_times:
        matrix = _camera_matrix(source_time, segment["keyframes"])
        yield _warp_clean_artwork(segment["artwork_bgr"], matrix), None


def _reaction_face_frames(
    reaction_path: Path,
) -> Iterator[tuple[np.ndarray, tuple[int, int, int, int]]]:
    capture = cv2.VideoCapture(str(reaction_path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open reaction clip for face checking: {reaction_path.name}")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if fps <= 0 or count <= 0:
        capture.release()
        raise RuntimeError("Reaction clip has invalid duration or frame rate.")
    source_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    source_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    scale = min(WIDTH / source_width, HEIGHT / source_height)
    foreground_width = max(1, round(source_width * scale))
    foreground_height = max(1, round(source_height * scale))
    foreground_x = (WIDTH - foreground_width) // 2
    foreground_y = (HEIGHT - foreground_height) // 2
    try:
        for seconds in (0.25, 0.8, 1.4, 2.1, 2.8, 3.35):
            frame_index = min(count - 1, max(0, round(seconds * fps)))
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ok, frame = capture.read()
            if not ok or frame is None:
                continue
            fitted = render_utils._fit_video_frame(frame)
            yield fitted, (
                foreground_x,
                foreground_y,
                foreground_width,
                foreground_height,
            )
    finally:
        capture.release()


def _face_cache_key(
    *,
    kind: str,
    asset_sha256: str,
    caption: str,
    preferred_y: float,
    line_colors: list[str] | None,
    safe_bottom_fraction: float | None,
    font_sha256: str,
    face_manager: FaceClearance,
    extra: dict[str, Any] | None = None,
) -> str:
    return _stable_fingerprint({
        "face_clearance_schema": 2,
        "kind": kind,
        "asset_sha256": asset_sha256,
        "caption": caption,
        "preferred_y": preferred_y,
        "line_colors": line_colors,
        "safe_bottom_fraction": safe_bottom_fraction,
        "font_sha256": font_sha256,
        "face_model_sha256": face_manager.model_sha256,
        "extra": extra or {},
    })


def _prepare_segments(
    workspace: Path,
    profile: dict[str, Any],
    face_manager: FaceClearance,
) -> tuple[list[dict[str, Any]], int, list[dict[str, Any]]]:
    raw_segments = profile.get("segments")
    if not isinstance(raw_segments, list) or not raw_segments:
        raise ValueError("Project configuration must contain a non-empty segments array.")

    prepared: list[dict[str, Any]] = []
    face_report: list[dict[str, Any]] = []
    total_frames = 0
    font_sha256 = _sha256_file(Path(render_utils.FONT_PATH).resolve())

    for index, raw in enumerate(raw_segments, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"Segment {index} must be a JSON object.")
        kind = raw.get("type")
        frames = int(raw.get("frames", 0))
        if frames <= 0 or frames > 60 * 60 * FPS:
            raise ValueError(f"Segment {index} has an invalid frame count.")

        asset_path = _workspace_path(workspace, raw.get("asset"), f"Segment {index} asset")
        if not asset_path.is_file():
            raise FileNotFoundError(f"Segment {index} clean visual asset is missing: {asset_path}")
        if asset_path.stat().st_size > MAX_INPUT_BYTES:
            raise ValueError(f"Segment {index} asset exceeds the 2 GB input limit.")
        asset_sha256 = _sha256_file(asset_path)

        if kind == "still":
            caption_text = str(raw.get("caption", "")).strip()
            caption_y = float(raw.get("caption_y", 0.65))
            caption_line_colors = raw.get("caption_line_colors")
            safe_bottom = raw.get("caption_safe_bottom_fraction")
            if not 0.0 <= caption_y <= 1.0:
                raise ValueError(f"Still segment {index} caption_y must be between 0 and 1.")
            if safe_bottom is not None:
                safe_bottom = float(safe_bottom)
                if not 0.65 <= safe_bottom <= 0.98:
                    raise ValueError(
                        f"Still segment {index} caption_safe_bottom_fraction must be between 0.65 and 0.98."
                    )
            with Image.open(asset_path) as source:
                dimensions = [source.width, source.height]
                source.verify()

            segment = {
                "type": "still",
                "frames": frames,
                "asset": str(asset_path),
                "asset_sha256": asset_sha256,
                "asset_name": asset_path.name,
                "dimensions": dimensions,
                "frame": None,
                "render_frame": None,
                "caption": caption_text,
                "caption_y": caption_y,
                "caption_line_colors": caption_line_colors,
                "caption_safe_bottom_fraction": safe_bottom,
            }

            if caption_text:
                face_key = _face_cache_key(
                    kind="still",
                    asset_sha256=asset_sha256,
                    caption=caption_text,
                    preferred_y=caption_y,
                    line_colors=caption_line_colors,
                    safe_bottom_fraction=safe_bottom,
                    font_sha256=font_sha256,
                    face_manager=face_manager,
                )
                placement = face_manager.resolve_caption_y(
                    face_key,
                    caption_text,
                    caption_y,
                    lambda segment=segment: _static_face_frames(segment),
                    line_colors=caption_line_colors,
                    safe_bottom_fraction=safe_bottom,
                )
                segment["caption_y"] = float(placement["caption_y"])
                face_report.append({
                    "segment": index,
                    "caption": caption_text,
                    "preferred_y": placement["preferred_y"],
                    "final_y": placement["caption_y"],
                    "repositioned": placement["repositioned"],
                    "status": placement["status"],
                    "faces_detected": placement["faces_detected"],
                    "samples_checked": placement["samples_checked"],
                })
            prepared.append(segment)

        elif kind == "keyframed_image":
            source_start = float(raw["source_start_seconds"])
            if not math.isfinite(source_start) or source_start < 0:
                raise ValueError(f"Segment {index} source_start_seconds is invalid.")
            keyframes_path = _workspace_path(
                workspace, raw.get("camera_keyframes"), f"Segment {index} camera_keyframes"
            )
            keyframes = _load_camera_keyframes(keyframes_path)
            with Image.open(asset_path) as source:
                dimensions = [source.width, source.height]
                source.verify()
            source_end = source_start + frames / FPS
            if keyframes[0][0] > source_start + 1e-3 or keyframes[-1][0] < source_end - 1.0 / FPS:
                raise ValueError(
                    f"Segment {index} camera keyframes do not cover its complete time range."
                )
            captions = _validate_timed_captions(raw.get("captions", []), source_start, source_end)
            segment = {
                "type": "keyframed_image",
                "frames": frames,
                "asset": str(asset_path),
                "asset_sha256": asset_sha256,
                "asset_name": asset_path.name,
                "dimensions": dimensions,
                "artwork_bgr": None,
                "keyframes": keyframes,
                "source_start_seconds": source_start,
                "captions": captions,
            }
            keyframes_sha256 = _sha256_file(keyframes_path)
            for caption_index, caption in enumerate(captions, start=1):
                face_key = _face_cache_key(
                    kind="keyframed_image",
                    asset_sha256=asset_sha256,
                    caption=caption["text"],
                    preferred_y=caption["y"],
                    line_colors=None,
                    safe_bottom_fraction=None,
                    font_sha256=font_sha256,
                    face_manager=face_manager,
                    extra={
                        "keyframes_sha256": keyframes_sha256,
                        "start_seconds": caption["start_seconds"],
                        "end_seconds": caption["end_seconds"],
                        "source_start_seconds": source_start,
                        "sampling_interval_seconds": 0.5,
                        "maximum_samples": 16,
                    },
                )
                try:
                    placement = face_manager.resolve_caption_y(
                        face_key,
                        caption["text"],
                        caption["y"],
                        lambda segment=segment, item=caption: _keyframed_face_frames(segment, item),
                        safe_bottom_fraction=caption.get("safe_bottom_fraction"),
                    )
                except ValueError as exc:
                    if "No face-clear caption position" not in str(exc) or caption.get("safe_bottom_fraction") is not None:
                        raise
                    # Only when the normal safe area has no clear position, try
                    # the extended lower caption zone used by portrait profiles.
                    caption["safe_bottom_fraction"] = 0.94
                    face_key = _face_cache_key(
                        kind="keyframed_image",
                        asset_sha256=asset_sha256,
                        caption=caption["text"],
                        preferred_y=caption["y"],
                        line_colors=None,
                        safe_bottom_fraction=0.94,
                        font_sha256=font_sha256,
                        face_manager=face_manager,
                        extra={
                            "keyframes_sha256": keyframes_sha256,
                            "start_seconds": caption["start_seconds"],
                            "end_seconds": caption["end_seconds"],
                            "source_start_seconds": source_start,
                            "sampling_interval_seconds": 0.5,
                            "maximum_samples": 16,
                        },
                    )
                    placement = face_manager.resolve_caption_y(
                        face_key,
                        caption["text"],
                        caption["y"],
                        lambda segment=segment, item=caption: _keyframed_face_frames(segment, item),
                        safe_bottom_fraction=0.94,
                    )
                caption["y"] = float(placement["caption_y"])
                caption["overlay"] = render_utils.caption_overlay(
                    caption["text"],
                    caption["y"],
                    caption.get("safe_bottom_fraction"),
                )
                face_report.append({
                    "segment": index,
                    "caption_index": caption_index,
                    "caption": caption["text"],
                    "preferred_y": placement["preferred_y"],
                    "final_y": placement["caption_y"],
                    "repositioned": placement["repositioned"],
                    "status": placement["status"],
                    "faces_detected": placement["faces_detected"],
                    "samples_checked": placement["samples_checked"],
                })
            prepared.append(segment)
        else:
            raise ValueError(
                f"Unsupported segment type {kind!r}. Supported types: 'still', 'keyframed_image'."
            )

        total_frames += frames

    return prepared, total_frames, face_report


def _segment_filename(output_dir: Path, configured_name: str) -> Path:
    if not configured_name or Path(configured_name).name != configured_name:
        raise ValueError("output_filename must be a plain filename without directory components.")
    if not configured_name.lower().endswith(".mp4"):
        raise ValueError("output_filename must end in .mp4.")
    return output_dir / configured_name


def _keyframes_for_range(
    keyframes: list[tuple[float, np.ndarray]],
    first_time: float,
    last_time: float,
) -> list[dict[str, Any]]:
    """Return the in-range keyframes plus the interpolation neighbours."""
    first_index = 0
    for index, (timestamp, _) in enumerate(keyframes):
        if timestamp <= first_time:
            first_index = index
        else:
            break

    last_index = len(keyframes) - 1
    for index, (timestamp, _) in enumerate(keyframes):
        if timestamp >= last_time:
            last_index = index
            break

    return [
        {"source_seconds": timestamp, "homography": matrix.tolist()}
        for timestamp, matrix in keyframes[first_index:last_index + 1]
    ]


def _captions_for_range(
    captions: list[dict[str, Any]],
    first_time: float,
    last_time: float,
) -> list[dict[str, Any]]:
    return [
        {
            "start_seconds": item["start_seconds"],
            "end_seconds": item["end_seconds"],
            "text": item["text"],
            "y": item["y"],
            "safe_bottom_fraction": item.get("safe_bottom_fraction"),
        }
        for item in captions
        if item["start_seconds"] <= last_time + 1e-9
        and item["end_seconds"] > first_time + 1e-9
    ]


def _make_cache_job(
    cache_dir: Path,
    context: dict[str, Any],
    kind: str,
    frame_count: int,
    label: str,
    inputs: dict[str, Any],
    **render_data: Any,
) -> dict[str, Any]:
    fingerprint = _stable_fingerprint({
        "render_context": context,
        "segment_kind": kind,
        "frame_count": frame_count,
        "inputs": inputs,
    })
    return {
        "kind": kind,
        "frame_count": frame_count,
        "label": label,
        "fingerprint": fingerprint,
        "cache_path": cache_dir / f"{fingerprint}.mp4",
        **render_data,
    }


def _build_render_jobs(
    cache_dir: Path,
    context: dict[str, Any],
    reaction_path: Path,
    hook: str,
    hook_y: float,
    segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    jobs = [
        _make_cache_job(
            cache_dir=cache_dir,
            context=context,
            kind="reaction",
            frame_count=HOOK_FRAMES,
            label="reaction-opening",
            inputs={
                "reaction_video_sha256": _sha256_file(reaction_path),
                "hook_text": hook,
                "hook_y": hook_y,
            },
            reaction_path=reaction_path,
            hook_text=hook,
            hook_y=hook_y,
        )
    ]

    for segment_index, segment in enumerate(segments, start=1):
        for chunk_index, start_frame in enumerate(
            range(0, segment["frames"], CACHE_CHUNK_FRAMES), start=1
        ):
            chunk_frames = min(CACHE_CHUNK_FRAMES, segment["frames"] - start_frame)
            label = f"visual-{segment_index:02d}-chunk-{chunk_index:03d}"

            if segment["type"] == "still":
                inputs = {
                    "asset_sha256": segment["asset_sha256"],
                    "caption": segment["caption"],
                    "caption_y": segment["caption_y"],
                    "caption_line_colors": segment["caption_line_colors"],
                    "caption_safe_bottom_fraction": segment["caption_safe_bottom_fraction"],
                }
                jobs.append(_make_cache_job(
                    cache_dir=cache_dir,
                    context=context,
                    kind="still",
                    frame_count=chunk_frames,
                    label=label,
                    inputs=inputs,
                    segment=segment,
                    start_frame=start_frame,
                ))
                continue

            source_start = segment["source_start_seconds"]
            first_time = source_start + start_frame / FPS
            last_time = source_start + (start_frame + chunk_frames - 1) / FPS
            relevant_keyframes = _keyframes_for_range(
                segment["keyframes"], first_time, last_time
            )
            relevant_captions = _captions_for_range(
                segment["captions"], first_time, last_time
            )
            inputs = {
                "asset_sha256": segment["asset_sha256"],
                "segment_source_start_seconds": source_start,
                "chunk_start_frame": start_frame,
                "chunk_first_source_time": first_time,
                "keyframes": relevant_keyframes,
                "captions": relevant_captions,
            }
            jobs.append(_make_cache_job(
                cache_dir=cache_dir,
                context=context,
                kind="keyframed_image",
                frame_count=chunk_frames,
                label=label,
                inputs=inputs,
                segment=segment,
                start_frame=start_frame,
            ))

    return jobs


def _iter_job_frames(job: dict[str, Any]) -> Iterator[np.ndarray]:
    kind = job["kind"]
    if kind == "reaction":
        yield from render_utils.reaction_frames(
            job["reaction_path"],
            job["hook_text"],
            job["hook_y"],
        )
        return

    segment = job["segment"]
    start_frame = int(job["start_frame"])
    frame_count = int(job["frame_count"])

    if kind == "still":
        if segment.get("render_frame") is None:
            if segment.get("frame") is None:
                with Image.open(segment["asset"]) as source:
                    clean = render_utils.compose_full_frame(source)
                    segment["frame"] = np.asarray(clean, dtype=np.uint8)
            frame = segment["frame"]
            if segment["caption"]:
                frame = render_utils.caption_frame(
                    frame,
                    segment["caption"],
                    segment["caption_y"],
                    segment.get("caption_line_colors"),
                    segment.get("caption_safe_bottom_fraction"),
                )
            segment["render_frame"] = frame
        frame = segment["render_frame"]
        for _ in range(frame_count):
            yield frame
        return

    if kind == "keyframed_image":
        if segment.get("artwork_bgr") is None:
            artwork = cv2.imread(segment["asset"], cv2.IMREAD_COLOR)
            if artwork is None:
                raise RuntimeError(f"Could not decode clean artwork: {segment['asset_name']}")
            segment["artwork_bgr"] = artwork
        captions = segment["captions"]
        for offset in range(frame_count):
            source_time = (
                segment["source_start_seconds"] + (start_frame + offset) / FPS
            )
            matrix = _camera_matrix(source_time, segment["keyframes"])
            bgr = _warp_clean_artwork(segment["artwork_bgr"], matrix)
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            caption_data = next(
                (
                    item for item in captions
                    if item["start_seconds"] <= source_time < item["end_seconds"]
                ),
                None,
            )
            if caption_data is not None:
                rgb = render_utils.apply_caption_overlay(rgb, caption_data["overlay"])
            yield np.ascontiguousarray(rgb)
        return

    raise ValueError(f"Unknown render job type: {kind}")


def _validate_encoded_video(
    path: Path,
    expected_frames: int,
    decode_all: bool = False,
) -> dict[str, Any]:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"Encoded segment could not be opened: {path.name}")
    try:
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if (
            (width, height) != (WIDTH, HEIGHT)
            or abs(fps - FPS) > 0.1
            or frame_count != expected_frames
        ):
            raise RuntimeError(
                f"Encoded video failed validation: {width}x{height}, "
                f"{fps:.3f} fps, {frame_count} frames; expected "
                f"{WIDTH}x{HEIGHT}, {FPS} fps, {expected_frames} frames."
            )

        if decode_all:
            decoded = 0
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                if frame is None:
                    raise RuntimeError(f"Decoded an empty frame in {path.name}.")
                decoded += 1
            if decoded != expected_frames:
                raise RuntimeError(
                    f"Decoded {decoded} frames from {path.name}; expected {expected_frames}."
                )
        else:
            ok, frame = capture.read()
            if not ok or frame is None:
                raise RuntimeError(f"Could not decode the first frame of {path.name}.")
            if expected_frames > 1:
                capture.set(cv2.CAP_PROP_POS_FRAMES, expected_frames - 1)
                ok, frame = capture.read()
                if not ok or frame is None:
                    raise RuntimeError(f"Could not decode the last frame of {path.name}.")
        return {
            "width": width,
            "height": height,
            "fps": round(fps, 6),
            "frame_count": frame_count,
        }
    finally:
        capture.release()


def _cache_sidecar_path(cache_path: Path) -> Path:
    return cache_path.with_suffix(".json")


def _cache_entry_valid(job: dict[str, Any]) -> bool:
    cache_path = job["cache_path"]
    sidecar_path = _cache_sidecar_path(cache_path)
    if not cache_path.is_file() or not sidecar_path.is_file():
        if cache_path.exists() or sidecar_path.exists():
            cache_path.unlink(missing_ok=True)
            sidecar_path.unlink(missing_ok=True)
        return False

    try:
        metadata = json.loads(sidecar_path.read_text(encoding="utf-8"))
        if (
            metadata.get("cache_schema_version") != CACHE_SCHEMA_VERSION
            or metadata.get("fingerprint") != job["fingerprint"]
            or metadata.get("frame_count") != job["frame_count"]
            or metadata.get("size_bytes") != cache_path.stat().st_size
        ):
            raise ValueError("Cache metadata does not match this render job.")
        if metadata.get("sha256") != _sha256_file(cache_path):
            raise ValueError("Cached segment checksum does not match.")
        _validate_encoded_video(cache_path, job["frame_count"])
        os.utime(cache_path, None)
        return True
    except Exception:
        # A cache entry is disposable only when it is the exact content-addressed
        # file requested by this job. Source assets and project outputs are untouched.
        cache_path.unlink(missing_ok=True)
        sidecar_path.unlink(missing_ok=True)
        return False


def _encode_job_to_cache(
    job: dict[str, Any],
    ffmpeg_exe: str,
) -> int:
    cache_path: Path = job["cache_path"]
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = cache_path.with_name(
        f"{cache_path.stem}.{uuid.uuid4().hex}.partial.mp4"
    )
    process: subprocess.Popen | None = None

    command = [
        ffmpeg_exe,
        "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s:v", f"{WIDTH}x{HEIGHT}", "-r", str(FPS), "-i", "pipe:0",
        "-frames:v", str(job["frame_count"]), "-an",
        "-c:v", VIDEO_CODEC, "-preset", ENCODER_PRESET, "-crf", str(ENCODER_CRF),
        "-pix_fmt", PIXEL_FORMAT,
        "-video_track_timescale", str(VIDEO_TRACK_TIMESCALE),
        "-movflags", "+faststart",
        str(temporary_path),
    ]

    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        assert process.stdin is not None
        written = 0
        for frame in _iter_job_frames(job):
            if frame.shape != (HEIGHT, WIDTH, 3) or frame.dtype != np.uint8:
                raise RuntimeError(
                    f"{job['label']} produced an invalid frame: "
                    f"shape={frame.shape}, dtype={frame.dtype}."
                )
            process.stdin.write(memoryview(np.ascontiguousarray(frame)).cast("B"))
            written += 1
        if written != job["frame_count"]:
            raise RuntimeError(
                f"{job['label']} produced {written} frames; expected {job['frame_count']}."
            )

        process.stdin.close()
        stderr = process.stderr.read() if process.stderr is not None else b""
        return_code = process.wait()
        if return_code != 0:
            raise RuntimeError(
                f"FFmpeg failed while encoding {job['label']}: "
                + stderr.decode("utf-8", errors="replace")[-3000:]
            )

        _validate_encoded_video(temporary_path, job["frame_count"], decode_all=True)
        size_bytes = temporary_path.stat().st_size
        checksum = _sha256_file(temporary_path)
        os.replace(temporary_path, cache_path)

        metadata = {
            "cache_schema_version": CACHE_SCHEMA_VERSION,
            "fingerprint": job["fingerprint"],
            "kind": job["kind"],
            "label": job["label"],
            "frame_count": job["frame_count"],
            "size_bytes": size_bytes,
            "sha256": checksum,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        sidecar_path = _cache_sidecar_path(cache_path)
        metadata_temporary = sidecar_path.with_name(
            f"{sidecar_path.stem}.{uuid.uuid4().hex}.partial.json"
        )
        try:
            metadata_temporary.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(metadata_temporary, sidecar_path)
        finally:
            metadata_temporary.unlink(missing_ok=True)

        return size_bytes
    except Exception:
        if process is not None:
            try:
                if process.stdin and not process.stdin.closed:
                    process.stdin.close()
            except Exception:
                pass
            if process.poll() is None:
                process.kill()
            process.wait()
        raise
    finally:
        temporary_path.unlink(missing_ok=True)


def _assemble_cached_segments(
    ffmpeg_exe: str,
    jobs: list[dict[str, Any]],
    manifest_path: Path,
    output_path: Path,
) -> None:
    manifest_lines = ["ffconcat version 1.0"]
    for job in jobs:
        path = job["cache_path"].resolve().as_posix()
        if "'" in path or "\n" in path or "\r" in path:
            raise ValueError("Cache path contains a character unsupported by the concat manifest.")
        manifest_lines.append(f"file '{path}'")
    manifest_path.write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")

    command = [
        ffmpeg_exe,
        "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(manifest_path),
        "-map", "0:v:0",
        "-c:v", "copy", "-an",
        "-video_track_timescale", str(VIDEO_TRACK_TIMESCALE),
        "-movflags", "+faststart",
        str(output_path),
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "FFmpeg could not assemble the cached segments: "
            + result.stderr[-4000:]
        )


def _validate_final_output(
    ffmpeg_exe: str,
    path: Path,
    expected_frames: int,
) -> dict[str, Any]:
    """Check stream metadata/boundaries with OpenCV and decode the full file with FFmpeg."""
    metadata = _validate_encoded_video(path, expected_frames, decode_all=False)
    result = subprocess.run(
        [
            ffmpeg_exe,
            "-hide_banner", "-v", "error", "-xerror",
            "-i", str(path),
            "-map", "0:v:0",
            "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "The assembled MP4 failed full-stream decoding: " + result.stderr[-4000:]
        )
    return metadata


def _cleanup_stale_cache_artifacts(cache_dir: Path) -> None:
    """Remove abandoned temporary files older than a day; leave active renders alone."""
    cutoff = time.time() - 24 * 60 * 60
    patterns = ("*.partial.mp4", "*.partial.json", ".assembly-*.ffconcat")
    for pattern in patterns:
        for path in cache_dir.glob(pattern):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
            except OSError:
                continue


def _prune_segment_cache(
    cache_dir: Path,
    protected_paths: set[Path],
) -> dict[str, int]:
    files = [
        path for path in cache_dir.glob("*.mp4")
        if re.fullmatch(r"[a-f0-9]{64}\.mp4", path.name)
    ]
    total_bytes = sum(path.stat().st_size for path in files if path.exists())
    evicted_files = 0
    evicted_bytes = 0

    for path in sorted(files, key=lambda item: item.stat().st_mtime):
        if total_bytes <= MAX_SEGMENT_CACHE_BYTES:
            break
        if path.resolve() in protected_paths:
            continue
        try:
            size = path.stat().st_size
            path.unlink(missing_ok=True)
            _cache_sidecar_path(path).unlink(missing_ok=True)
            total_bytes -= size
            evicted_files += 1
            evicted_bytes += size
        except OSError:
            continue

    return {
        "evicted_files": evicted_files,
        "evicted_bytes": evicted_bytes,
        "cache_bytes": max(0, total_bytes),
    }


def build_configured_short(
    workspace: str | Path,
    reaction_video: str,
    project_config: str,
    hook_text: str,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Render a configured Short, reusing unchanged independently encoded segments."""
    overall_started = time.perf_counter()
    workspace_path = Path(workspace).resolve()
    reaction_path = _workspace_path(workspace_path, reaction_video, "reaction_video")
    config_path = _workspace_path(workspace_path, project_config, "project_config")
    destination_dir = Path(output_dir).resolve()

    # Mandatory on purpose: there is no project or renderer default hook.
    hook = _validate_hook(hook_text)
    if not reaction_path.is_file():
        raise FileNotFoundError(f"Reaction clip not found: {reaction_video}")
    if reaction_path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("Reaction clip exceeds the 2 GB input limit.")
    if not render_utils.FONT_PATH.is_file():
        raise FileNotFoundError(f"Caslon font not found: {render_utils.FONT_PATH}")

    profile = _load_profile(config_path)
    content_validation = validate_profile_content(profile, workspace_path)
    timing_reference = profile.get("timing_reference")
    reference_path = _workspace_path(workspace_path, timing_reference, "timing_reference")
    if not reference_path.is_file():
        raise FileNotFoundError(f"Timing reference video not found: {reference_path}")
    if reference_path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("Timing reference exceeds the 2 GB input limit.")

    reference = cv2.VideoCapture(str(reference_path))
    if not reference.isOpened():
        raise RuntimeError(f"Could not inspect timing reference: {reference_path.name}")
    ref_fps = float(reference.get(cv2.CAP_PROP_FPS) or 0.0)
    ref_frames = int(reference.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    ref_duration = ref_frames / ref_fps if ref_fps > 0 and ref_frames else 0.0
    reference.release()
    if ref_duration <= 0:
        raise ValueError("Timing reference has invalid duration or frame rate.")
    expected_duration = float(profile.get("reference_duration_seconds", 0.0))
    tolerance = float(profile.get("reference_duration_tolerance_seconds", 0.15))
    if expected_duration > 0 and abs(ref_duration - expected_duration) > tolerance:
        raise ValueError(
            f"Project profile expects a {expected_duration:.3f}s reference (±{tolerance:.3f}s), "
            f"but {reference_path.name} is {ref_duration:.3f}s."
        )

    preparation_started = time.perf_counter()
    face_manager = FaceClearance(workspace_path / "cache" / "face_clearance")
    hook_face_key = _face_cache_key(
        kind="reaction_hook",
        asset_sha256=_sha256_file(reaction_path),
        caption=hook,
        preferred_y=0.25,
        line_colors=None,
        safe_bottom_fraction=None,
        font_sha256=_sha256_file(Path(render_utils.FONT_PATH).resolve()),
        face_manager=face_manager,
        extra={
            "font_size_px": render_utils.HOOK_FONT_SIZE_PX,
            "max_width_px": render_utils.HOOK_TEXT_WIDTH_PX,
            "stroke_width_px": render_utils.CAPTION_STROKE_WIDTH_PX,
        },
    )
    hook_placement = face_manager.resolve_caption_y(
        hook_face_key,
        hook,
        0.25,
        lambda: _reaction_face_frames(reaction_path),
        font_size_px=render_utils.HOOK_FONT_SIZE_PX,
        max_width_px=render_utils.HOOK_TEXT_WIDTH_PX,
    )
    hook_y = float(hook_placement["caption_y"])
    segments, visual_frames, face_report = _prepare_segments(
        workspace_path,
        profile,
        face_manager,
    )
    face_report.insert(0, {
        "segment": "reaction_hook",
        "caption": hook,
        "preferred_y": hook_placement["preferred_y"],
        "final_y": hook_placement["caption_y"],
        "repositioned": hook_placement["repositioned"],
        "status": hook_placement["status"],
        "faces_detected": hook_placement["faces_detected"],
        "samples_checked": hook_placement["samples_checked"],
    })
    preparation_seconds = time.perf_counter() - preparation_started

    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    project_slug = _safe_slug(str(profile.get("project_name", config_path.stem)))
    cache_dir = workspace_path / "cache" / "render_segments" / project_slug
    cache_dir.mkdir(parents=True, exist_ok=True)
    _cleanup_stale_cache_artifacts(cache_dir)
    fingerprint_context = _render_fingerprint_context(ffmpeg_exe)
    jobs = _build_render_jobs(
        cache_dir=cache_dir,
        context=fingerprint_context,
        reaction_path=reaction_path,
        hook=hook,
        hook_y=hook_y,
        segments=segments,
    )

    destination_dir.mkdir(parents=True, exist_ok=True)
    output_path = _segment_filename(destination_dir, str(profile.get("output_filename", "")))
    partial_path = output_path.with_name(output_path.stem + ".partial.mp4")
    partial_path.unlink(missing_ok=True)
    manifest_path = cache_dir / f".assembly-{uuid.uuid4().hex}.ffconcat"
    total_frames = HOOK_FRAMES + visual_frames

    cache_started = time.perf_counter()
    cache_hits = 0
    cache_misses = 0
    reaction_generation_seconds = 0.0
    visual_generation_seconds = 0.0
    try:
        for job in jobs:
            if _cache_entry_valid(job):
                cache_hits += 1
                continue

            cache_misses += 1
            job_started = time.perf_counter()
            _encode_job_to_cache(job, ffmpeg_exe)
            elapsed = time.perf_counter() - job_started
            if job["kind"] == "reaction":
                reaction_generation_seconds += elapsed
            else:
                visual_generation_seconds += elapsed

        cache_processing_seconds = time.perf_counter() - cache_started
        assembly_started = time.perf_counter()
        _assemble_cached_segments(
            ffmpeg_exe=ffmpeg_exe,
            jobs=jobs,
            manifest_path=manifest_path,
            output_path=partial_path,
        )
        assembly_seconds = time.perf_counter() - assembly_started

        # Decode the complete result, not just its first frame, so a broken
        # stream-copy join or missing boundary frame cannot pass validation.
        validation = _validate_final_output(ffmpeg_exe, partial_path, total_frames)
        partial_path.replace(output_path)

        protected = {job["cache_path"].resolve() for job in jobs}
        pruning = _prune_segment_cache(cache_dir, protected)
        total_seconds = time.perf_counter() - overall_started
        cache_hit_rate = cache_hits / len(jobs) if jobs else 0.0

        return {
            "success": True,
            "project": profile.get("project_name", config_path.stem),
            "output_file": str(output_path),
            "duration_seconds": round(total_frames / validation["fps"], 3),
            "width": validation["width"],
            "height": validation["height"],
            "fps": validation["fps"],
            "frame_count": validation["frame_count"],
            "audio": "none",
            "hook_count": 1,
            "hook_text": hook,
            "reaction_duration_seconds": round(HOOK_FRAMES / FPS, 3),
            "visual_segment_count": len(segments),
            "visual_sources": [
                {
                    "file": segment["asset_name"],
                    "type": segment["type"],
                    "source_dimensions": segment["dimensions"],
                }
                for segment in segments
            ],
            "caption_style": "F37 Caslon Bold Display Condensed, per-line colors with black stroke",
            "caption_font_size_px": render_utils.CAPTION_FONT_SIZE_PX,
            "caption_background_panels": False,
            "profile_validation": content_validation,
            "source_frame_policy": {
                "reference_frame_asset_paths": "passed; no assets point into scene_frames",
                "source_provenance": content_validation["source_provenance"],
                "manual_source_page_review_required": True,
                "verification_limit": "File paths and source-page records are checked; pixel-level source identity is not machine-provable.",
            },
            "face_clearance": {
                "detector": "OpenCV YuNet face_detection_yunet_2023mar",
                "captions_checked": len(face_report),
                "repositioned_captions": sum(1 for item in face_report if item["repositioned"]),
                "captions_with_detected_faces": sum(1 for item in face_report if item["status"] == "clear"),
                "no_faces_detected": sum(1 for item in face_report if item["status"] == "no_faces_detected"),
                "results": face_report,
            },
            "frame_validation": "passed; metadata, boundary frames, and full FFmpeg decode",
            "segment_cache": {
                "enabled": True,
                "directory": str(cache_dir.relative_to(workspace_path)),
                "chunk_frames": CACHE_CHUNK_FRAMES,
                "chunk_seconds": round(CACHE_CHUNK_FRAMES / FPS, 3),
                "chunks_total": len(jobs),
                "chunks_reused": cache_hits,
                "chunks_rendered": cache_misses,
                "hit_rate": round(cache_hit_rate, 3),
                "cache_bytes": pruning["cache_bytes"],
                "cache_limit_bytes": MAX_SEGMENT_CACHE_BYTES,
                "evicted_files": pruning["evicted_files"],
                "evicted_bytes": pruning["evicted_bytes"],
            },
            "render_timing_seconds": {
                "profile_preparation": round(preparation_seconds, 3),
                "reaction_generation_and_pipe": round(reaction_generation_seconds, 3),
                "visual_generation_and_pipe": round(visual_generation_seconds, 3),
                "cache_lookup_and_generation": round(cache_processing_seconds, 3),
                "ffmpeg_finalize": round(assembly_seconds, 3),
                "total": round(total_seconds, 3),
            },
            "render_settings": {
                "warp_interpolation": "linear",
                "encoder": VIDEO_CODEC,
                "encoder_preset": ENCODER_PRESET,
                "crf": ENCODER_CRF,
                "pixel_format": PIXEL_FORMAT,
                "assembly": "FFmpeg concat demuxer with stream copy",
                "worker_count": 1,
            },
        }
    except Exception:
        partial_path.unlink(missing_ok=True)
        raise
    finally:
        manifest_path.unlink(missing_ok=True)
