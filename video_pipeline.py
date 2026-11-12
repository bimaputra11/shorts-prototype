from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont, ImageOps
from rapidocr import RapidOCR
from scenedetect import ContentDetector, detect


SUPPORTED_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
DEFAULT_SCENE_THRESHOLD = 27.0
DEFAULT_OCR_INTERVAL = 0.35
MAX_OCR_SAMPLES = 360
OCR_MIN_CONFIDENCE = 0.35


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _slug(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    value = re.sub(r"-{2,}", "-", value).strip("-._")
    return (value[:80] or "inspiration-video").lower()


def _seconds_to_srt(seconds: float) -> str:
    milliseconds = max(0, int(round(seconds * 1000)))
    hours, rem = divmod(milliseconds, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def _json_safe_box(box: Any) -> list[list[float]] | None:
    if box is None:
        return None
    try:
        return [[round(float(point[0]), 1), round(float(point[1]), 1)] for point in box]
    except (TypeError, ValueError, IndexError):
        return None


def probe_video(video_path: Path) -> dict[str, Any]:
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"OpenCV could not open video: {video_path.name}")

    try:
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if fps <= 0 or width <= 0 or height <= 0:
            raise RuntimeError("Video metadata is incomplete or unsupported.")
        duration = frame_count / fps if frame_count > 0 else 0.0
        if duration <= 0:
            raise RuntimeError("Could not determine a valid video duration.")

        return {
            "filename": video_path.name,
            "duration_seconds": round(duration, 3),
            "width": width,
            "height": height,
            "fps": round(fps, 3),
            "frame_count": frame_count,
            "aspect_ratio": round(width / height, 5),
            "orientation": "portrait" if height > width else (
                "landscape" if width > height else "square"
            ),
            "file_size_bytes": video_path.stat().st_size,
        }
    finally:
        capture.release()


def detect_scenes(
    video_path: Path,
    fps: float,
    duration: float,
    threshold: float = DEFAULT_SCENE_THRESHOLD,
) -> list[dict[str, Any]]:
    min_scene_len = max(1, int(round(fps * 0.25)))
    scene_list = detect(
        str(video_path),
        ContentDetector(threshold=threshold, min_scene_len=min_scene_len),
        show_progress=False,
    )

    scenes: list[dict[str, Any]] = []
    for index, (start, end) in enumerate(scene_list, start=1):
        start_seconds = max(0.0, float(start.seconds))
        end_seconds = min(duration, float(end.seconds))
        if end_seconds <= start_seconds:
            continue
        scenes.append({
            "scene": index,
            "start_seconds": round(start_seconds, 3),
            "end_seconds": round(end_seconds, 3),
            "duration_seconds": round(end_seconds - start_seconds, 3),
            "representative_seconds": round((start_seconds + end_seconds) / 2, 3),
        })

    if not scenes:
        scenes = [{
            "scene": 1,
            "start_seconds": 0.0,
            "end_seconds": round(duration, 3),
            "duration_seconds": round(duration, 3),
            "representative_seconds": round(duration / 2, 3),
        }]

    # Keep scene timing contiguous and deterministic despite detector rounding.
    for index, scene in enumerate(scenes, start=1):
        scene["scene"] = index
    return scenes


def extract_scene_frames(
    video_path: Path,
    scenes: list[dict[str, Any]],
    frames_dir: Path,
) -> list[dict[str, Any]]:
    frames_dir.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError("Could not reopen video to extract scene frames.")

    extracted: list[dict[str, Any]] = []
    try:
        for scene in scenes:
            timestamp = float(scene["representative_seconds"])
            capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000.0)
            ok, frame = capture.read()
            if not ok or frame is None:
                # Seek to the scene's first frame if its midpoint is not readable.
                capture.set(cv2.CAP_PROP_POS_MSEC, float(scene["start_seconds"]) * 1000.0)
                ok, frame = capture.read()
            if not ok or frame is None:
                scene["frame_extracted"] = False
                continue

            filename = f"scene_{int(scene['scene']):03d}_{timestamp:07.2f}s.jpg"
            frame_path = frames_dir / filename
            if not cv2.imwrite(
                str(frame_path),
                frame,
                [int(cv2.IMWRITE_JPEG_QUALITY), 92],
            ):
                scene["frame_extracted"] = False
                continue

            scene["frame_extracted"] = True
            scene["frame_file"] = str(frame_path)
            extracted.append({
                "scene": int(scene["scene"]),
                "timestamp_seconds": timestamp,
                "path": str(frame_path),
            })
    finally:
        capture.release()

    return extracted


def make_contact_sheet(
    extracted_frames: list[dict[str, Any]],
    output_path: Path,
    columns: int = 3,
) -> None:
    if not extracted_frames:
        return

    cell_width = 360
    image_height = 500
    label_height = 42
    cell_height = image_height + label_height
    rows = math.ceil(len(extracted_frames) / columns)

    sheet = Image.new("RGB", (columns * cell_width, rows * cell_height), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)

    try:
        font = ImageFont.truetype("arial.ttf", 18)
    except OSError:
        font = ImageFont.load_default()

    for index, item in enumerate(extracted_frames):
        x = (index % columns) * cell_width
        y = (index // columns) * cell_height
        with Image.open(item["path"]) as source:
            image = ImageOps.contain(source.convert("RGB"), (cell_width - 16, image_height - 12))
        px = x + (cell_width - image.width) // 2
        py = y + (image_height - image.height) // 2
        sheet.paste(image, (px, py))
        label = (
            f"Scene {item['scene']:03d}  |  "
            f"{item['timestamp_seconds']:.2f}s"
        )
        draw.text((x + 10, y + image_height + 8), label, fill=(245, 245, 245), font=font)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, quality=90)


def _read_ocr_snapshot(
    engine: RapidOCR,
    frame,
    timestamp: float,
) -> dict[str, Any]:
    height, width = frame.shape[:2]
    max_dimension = max(height, width)
    if max_dimension > 1600:
        scale = 1600.0 / max_dimension
        frame = cv2.resize(
            frame,
            (max(1, int(width * scale)), max(1, int(height * scale))),
            interpolation=cv2.INTER_AREA,
        )

    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    result = engine(rgb)

    detections: list[dict[str, Any]] = []
    if result is not None:
        texts = result.txts or []
        scores = result.scores or []
        boxes = result.boxes
        for index, raw_text in enumerate(texts):
            text = " ".join(str(raw_text).split()).strip()
            if not text:
                continue
            try:
                score = float(scores[index])
            except (IndexError, TypeError, ValueError):
                score = 0.0
            if score < OCR_MIN_CONFIDENCE:
                continue

            box = None
            if boxes is not None:
                try:
                    box = _json_safe_box(boxes[index])
                except (IndexError, TypeError):
                    box = None

            detections.append({
                "text": text,
                "confidence": round(score, 4),
                "box": box,
            })

    detections.sort(
        key=lambda item: (
            min((point[1] for point in item["box"]), default=0.0)
            if item["box"] else 0.0,
            min((point[0] for point in item["box"]), default=0.0)
            if item["box"] else 0.0,
        )
    )
    return {
        "timestamp_seconds": round(timestamp, 3),
        "text": "\n".join(item["text"] for item in detections),
        "detections": detections,
    }


def _merge_ocr_snapshots(
    snapshots: list[dict[str, Any]],
    duration: float,
    interval: float,
) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    last_nonempty_time: float | None = None

    for snapshot in snapshots:
        text = snapshot["text"].strip()
        if not text:
            continue

        signature = re.sub(r"\\s+", " ", text.casefold()).strip()
        timestamp = float(snapshot["timestamp_seconds"])
        confidence_values = [
            float(item["confidence"]) for item in snapshot["detections"]
        ]
        average_confidence = (
            sum(confidence_values) / len(confidence_values)
            if confidence_values else 0.0
        )

        if (
            current is not None
            and current["_signature"] == signature
            and last_nonempty_time is not None
            and timestamp - last_nonempty_time <= interval * 1.75
        ):
            current["end_seconds"] = round(min(duration, timestamp + interval), 3)
            current["samples"] += 1
            current["average_confidence"] = round(
                (
                    current["average_confidence"] * (current["samples"] - 1)
                    + average_confidence
                ) / current["samples"],
                4,
            )
            last_nonempty_time = timestamp
            continue

        if current is not None:
            current.pop("_signature", None)
            segments.append(current)

        current = {
            "_signature": signature,
            "start_seconds": round(timestamp, 3),
            "end_seconds": round(min(duration, timestamp + interval), 3),
            "text": text,
            "average_confidence": round(average_confidence, 4),
            "samples": 1,
            "detections": snapshot["detections"],
        }
        last_nonempty_time = timestamp

    if current is not None:
        current.pop("_signature", None)
        segments.append(current)
    return segments


def extract_scene_frames_and_caption_segments(
    video_path: Path,
    scenes: list[dict[str, Any]],
    frames_dir: Path,
    duration: float,
    fps: float,
    interval_seconds: float = DEFAULT_OCR_INTERVAL,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Read the source once sequentially for both scene stills and OCR samples."""
    interval = max(0.15, float(interval_seconds))
    interval = max(interval, duration / MAX_OCR_SAMPLES)
    sample_count = min(
        MAX_OCR_SAMPLES,
        max(1, int(math.ceil(duration / interval))),
    )

    scene_targets: dict[int, list[dict[str, Any]]] = {}
    for scene in scenes:
        target_index = max(0, round(float(scene["representative_seconds"]) * fps))
        scene_targets.setdefault(target_index, []).append(scene)

    ocr_targets: dict[int, float] = {}
    for index in range(sample_count):
        target_seconds = min(index * interval, max(0.0, duration - 0.02))
        target_index = max(0, round(target_seconds * fps))
        ocr_targets.setdefault(target_index, target_seconds)

    target_indices = sorted(set(scene_targets) | set(ocr_targets))
    if not target_indices:
        raise RuntimeError("No analysis sample positions were generated.")

    frames_dir.mkdir(parents=True, exist_ok=True)
    for stale_frame in frames_dir.glob("scene_*.jpg"):
        stale_frame.unlink(missing_ok=True)

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError("Could not open source video for sequential analysis.")

    engine = RapidOCR()
    extracted: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    frame_index = 0
    target_pointer = 0
    maximum_target = target_indices[-1]

    try:
        while frame_index <= maximum_target:
            ok, frame = capture.read()
            if not ok or frame is None:
                break

            while (
                target_pointer < len(target_indices)
                and target_indices[target_pointer] == frame_index
            ):
                sample_index = target_indices[target_pointer]
                timestamp = frame_index / fps

                for scene in scene_targets.get(sample_index, []):
                    filename = (
                        f"scene_{int(scene['scene']):03d}_"
                        f"{float(scene['representative_seconds']):07.2f}s.jpg"
                    )
                    frame_path = frames_dir / filename
                    if cv2.imwrite(
                        str(frame_path),
                        frame,
                        [int(cv2.IMWRITE_JPEG_QUALITY), 92],
                    ):
                        scene["frame_extracted"] = True
                        scene["frame_file"] = str(frame_path)
                        extracted.append({
                            "scene": int(scene["scene"]),
                            "timestamp_seconds": round(timestamp, 3),
                            "path": str(frame_path),
                        })
                    else:
                        scene["frame_extracted"] = False

                if sample_index in ocr_targets:
                    snapshots.append(
                        _read_ocr_snapshot(engine, frame, timestamp)
                    )
                target_pointer += 1
            frame_index += 1
    finally:
        capture.release()

    caption_segments = _merge_ocr_snapshots(snapshots, duration, interval)
    metadata = {
        "sample_interval_seconds": round(interval, 3),
        "frames_sampled": len(snapshots),
        "frames_with_text": sum(1 for item in snapshots if item["text"]),
        "minimum_detection_confidence": OCR_MIN_CONFIDENCE,
        "frame_decode_passes": 1,
        "sampling_mode": "single sequential pass; representative frames and OCR share decoded frames",
    }
    return extracted, caption_segments, metadata


def extract_caption_segments(
    video_path: Path,
    duration: float,
    interval_seconds: float = DEFAULT_OCR_INTERVAL,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Compatibility helper; the main analysis path uses sequential sampling."""
    interval = max(0.15, float(interval_seconds))
    interval = max(interval, duration / MAX_OCR_SAMPLES)
    sample_count = min(
        MAX_OCR_SAMPLES,
        max(1, int(math.ceil(duration / interval))),
    )
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError("Could not reopen video for caption extraction.")
    engine = RapidOCR()
    snapshots: list[dict[str, Any]] = []
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    if fps <= 0:
        capture.release()
        raise RuntimeError("Could not determine source frame rate.")
    try:
        next_index = 0
        frame_index = 0
        while frame_index < int(math.ceil(duration * fps)):
            ok, frame = capture.read()
            if not ok or frame is None:
                break
            if next_index < sample_count:
                target_index = round(
                    min(next_index * interval, max(0.0, duration - 0.02)) * fps
                )
                if frame_index >= target_index:
                    snapshots.append(
                        _read_ocr_snapshot(engine, frame, frame_index / fps)
                    )
                    next_index += 1
            frame_index += 1
    finally:
        capture.release()
    segments = _merge_ocr_snapshots(snapshots, duration, interval)
    metadata = {
        "sample_interval_seconds": round(interval, 3),
        "frames_sampled": len(snapshots),
        "frames_with_text": sum(1 for item in snapshots if item["text"]),
        "minimum_detection_confidence": OCR_MIN_CONFIDENCE,
    }
    return segments, metadata


def write_srt(segments: list[dict[str, Any]], output_path: Path) -> None:
    blocks: list[str] = []
    for index, segment in enumerate(segments, start=1):
        text = str(segment["text"]).replace("\r", "").strip()
        blocks.append(
            f"{index}\n"
            f"{_seconds_to_srt(float(segment['start_seconds']))} --> "
            f"{_seconds_to_srt(float(segment['end_seconds']))}\n"
            f"{text}\n"
        )
    output_path.write_text("\n".join(blocks), encoding="utf-8-sig")


def analyze_video(
    video_path: str | Path,
    output_root: str | Path,
    scene_threshold: float = DEFAULT_SCENE_THRESHOLD,
    ocr_interval_seconds: float = DEFAULT_OCR_INTERVAL,
) -> dict[str, Any]:
    total_started = time.perf_counter()
    source = Path(video_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Video not found: {source}")
    if source.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported video format {source.suffix}. "
            f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )

    output_base = Path(output_root).expanduser().resolve()
    output_dir = output_base / _slug(source.stem)
    frames_dir = output_dir / "scene_frames"
    output_dir.mkdir(parents=True, exist_ok=True)
    analysis_path = output_dir / "analysis.json"
    captions_path = output_dir / "captions.srt"
    contact_sheet = output_dir / "contact_sheet.jpg"

    source_sha256 = _sha256_file(source)
    settings = {
        "scene_threshold": float(scene_threshold),
        "ocr_interval_seconds": float(ocr_interval_seconds),
        "pipeline_sha256": _sha256_file(Path(__file__).resolve()),
        "opencv_version": cv2.__version__,
    }

    # Reuse analysis only when the source bytes and all analysis settings match.
    if analysis_path.is_file() and captions_path.is_file() and contact_sheet.is_file():
        try:
            cached = json.loads(analysis_path.read_text(encoding="utf-8"))
            cache_info = cached.get("analysis_cache", {})
            required_frames = [
                Path(scene["frame_file"])
                for scene in cached.get("scenes", [])
                if scene.get("frame_extracted") and scene.get("frame_file")
            ]
            if (
                cache_info.get("source_sha256") == source_sha256
                and cache_info.get("settings") == settings
                and required_frames
                and all(frame.is_file() for frame in required_frames)
            ):
                scenes = cached.get("scenes", [])
                captions = cached.get("caption_segments", [])
                return {
                    "success": True,
                    "video": cached.get("video"),
                    "scene_count": len(scenes),
                    "scene_frames_extracted": sum(
                        1 for scene in scenes if scene.get("frame_extracted")
                    ),
                    "caption_segment_count": len(captions),
                    "captions": [
                        {
                            "start_seconds": item["start_seconds"],
                            "end_seconds": item["end_seconds"],
                            "text": item["text"],
                            "confidence": item["average_confidence"],
                        }
                        for item in captions
                    ],
                    "contact_sheet": str(contact_sheet),
                    "analysis_file": str(analysis_path),
                    "captions_file": str(captions_path),
                    "analysis_cache_hit": True,
                    "source_sha256": source_sha256,
                    "analysis_timings_seconds": cached.get("timings_seconds", {}),
                }
        except (OSError, ValueError, KeyError, TypeError):
            pass

    timings: dict[str, float] = {}
    started = time.perf_counter()
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    probe = probe_video(source)
    timings["probe"] = round(time.perf_counter() - started, 3)

    started = time.perf_counter()
    scenes = detect_scenes(
        source,
        fps=float(probe["fps"]),
        duration=float(probe["duration_seconds"]),
        threshold=float(scene_threshold),
    )
    timings["scene_detection"] = round(time.perf_counter() - started, 3)

    started = time.perf_counter()
    extracted_frames, caption_segments, ocr_metadata = (
        extract_scene_frames_and_caption_segments(
            source,
            scenes,
            frames_dir,
            duration=float(probe["duration_seconds"]),
            fps=float(probe["fps"]),
            interval_seconds=ocr_interval_seconds,
        )
    )
    timings["sequential_frame_and_ocr_pass"] = round(time.perf_counter() - started, 3)

    started = time.perf_counter()
    make_contact_sheet(extracted_frames, contact_sheet)
    timings["contact_sheet"] = round(time.perf_counter() - started, 3)
    timings["total"] = round(time.perf_counter() - total_started, 3)

    analysis = {
        "schema_version": 1,
        "analyzed_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_video": str(source),
        "analysis_cache": {
            "source_sha256": source_sha256,
            "settings": settings,
        },
        "video": probe,
        "processing": {
            "ffmpeg_executable": ffmpeg_exe,
            "scene_detector": "PySceneDetect ContentDetector",
            "scene_threshold": float(scene_threshold),
            "ocr_engine": "RapidOCR with ONNX Runtime",
            "ocr": ocr_metadata,
        },
        "timings_seconds": timings,
        "scenes": scenes,
        "caption_segments": caption_segments,
        "outputs": {
            "directory": str(output_dir),
            "contact_sheet": str(contact_sheet) if contact_sheet.exists() else None,
            "scene_frames_directory": str(frames_dir),
        },
    }

    analysis_path.parent.mkdir(parents=True, exist_ok=True)
    analysis_path.write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_srt(caption_segments, captions_path)

    return {
        "success": True,
        "video": probe,
        "scene_count": len(scenes),
        "scene_frames_extracted": len(extracted_frames),
        "caption_segment_count": len(caption_segments),
        "captions": [
            {
                "start_seconds": item["start_seconds"],
                "end_seconds": item["end_seconds"],
                "text": item["text"],
                "confidence": item["average_confidence"],
            }
            for item in caption_segments
        ],
        "contact_sheet": str(contact_sheet),
        "analysis_file": str(analysis_path),
        "captions_file": str(captions_path),
        "analysis_cache_hit": False,
        "source_sha256": source_sha256,
        "analysis_timings_seconds": timings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect an inspiration Short: scenes, representative frames, and on-screen captions."
    )
    parser.add_argument("video", help="Path to the input video file.")
    parser.add_argument(
        "--output",
        default="workspace/working",
        help="Output directory for internal analysis artifacts.",
    )
    parser.add_argument(
        "--scene-threshold",
        type=float,
        default=DEFAULT_SCENE_THRESHOLD,
    )
    parser.add_argument(
        "--ocr-interval",
        type=float,
        default=DEFAULT_OCR_INTERVAL,
    )
    args = parser.parse_args()

    try:
        result = analyze_video(
            args.video,
            args.output,
            scene_threshold=args.scene_threshold,
            ocr_interval_seconds=args.ocr_interval,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
