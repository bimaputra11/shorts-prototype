from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Callable, Iterable

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

import render_utils


FACE_CLEARANCE_SCHEMA_VERSION = 1
FACE_MODEL_FILENAME = "face_detection_yunet_2023mar.onnx"
FACE_MODEL_PATH = Path(__file__).resolve().parent / "models" / FACE_MODEL_FILENAME
FACE_SCORE_THRESHOLD = 0.72
FACE_NMS_THRESHOLD = 0.30
MAX_DETECTION_DIMENSION = 1280


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _caption_bounds(
    caption: str,
    center_y_fraction: float,
    line_colors: list[str] | None = None,
    safe_bottom_fraction: float | None = None,
    font_size_px: int | None = None,
    max_width_px: int | None = None,
) -> tuple[int, int, int, int]:
    if not 0.0 <= float(center_y_fraction) <= 1.0:
        raise ValueError("Caption y position must be between 0 and 1.")
    selected_font_size = (
        render_utils.CAPTION_FONT_SIZE_PX if font_size_px is None else int(font_size_px)
    )
    font = ImageFont.truetype(str(render_utils.FONT_PATH), selected_font_size)
    draw = ImageDraw.Draw(Image.new("RGB", (render_utils.WIDTH, render_utils.HEIGHT)))
    lines = render_utils.caption_line_layout(
        caption, font, line_colors, max_width_px=max_width_px
    )
    stroke = render_utils.CAPTION_STROKE_WIDTH_PX if render_utils.CAPTION_STROKE_ENABLED else 0
    line_bounds = [
        draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
        for text, _ in lines
    ]
    text_width = max(bounds[2] - bounds[0] for bounds in line_bounds)
    ascent, descent = font.getmetrics()
    line_height = ascent + descent
    spacing = 10
    text_height = line_height * len(lines) + spacing * (len(lines) - 1)

    safe_bottom = (
        render_utils.SAFE_BOTTOM
        if safe_bottom_fraction is None
        else round(render_utils.HEIGHT * float(safe_bottom_fraction))
    )
    min_center = render_utils.SAFE_TOP + text_height // 2 + 36
    max_center = safe_bottom - text_height // 2 - 36
    if max_center < min_center:
        raise ValueError("Caption safe area is too small for the requested text.")
    requested_center = round(render_utils.HEIGHT * float(center_y_fraction))
    center = min(max(requested_center, min_center), max_center)
    pad = max(6, stroke + 2)
    return (
        render_utils.WIDTH // 2 - text_width // 2 - pad,
        center - text_height // 2 - pad,
        render_utils.WIDTH // 2 + (text_width + 1) // 2 + pad,
        center + (text_height + 1) // 2 + pad,
    )


def _detect_faces(
    frame_bgr: np.ndarray,
    detector,
    roi: tuple[int, int, int, int] | None = None,
) -> list[dict[str, float]]:
    if frame_bgr is None or frame_bgr.ndim != 3 or frame_bgr.shape[2] != 3:
        raise ValueError("Face detection received an invalid BGR frame.")

    frame_height, frame_width = frame_bgr.shape[:2]
    offset_x = 0
    offset_y = 0
    if roi is not None:
        offset_x, offset_y, roi_width, roi_height = [int(value) for value in roi]
        offset_x = max(0, min(offset_x, frame_width - 1))
        offset_y = max(0, min(offset_y, frame_height - 1))
        roi_width = max(1, min(roi_width, frame_width - offset_x))
        roi_height = max(1, min(roi_height, frame_height - offset_y))
        image = frame_bgr[offset_y:offset_y + roi_height, offset_x:offset_x + roi_width]
    else:
        image = frame_bgr

    original_height, original_width = image.shape[:2]
    scale = min(1.0, MAX_DETECTION_DIMENSION / max(original_width, original_height))
    if scale < 1.0:
        detection_image = cv2.resize(
            image,
            (max(1, round(original_width * scale)), max(1, round(original_height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    else:
        detection_image = image

    detector.setInputSize((detection_image.shape[1], detection_image.shape[0]))
    _, faces = detector.detect(detection_image)
    if faces is None or len(faces) == 0:
        return []

    inverse_scale = 1.0 / scale
    results: list[dict[str, float]] = []
    for face in faces:
        x, y, width, height = [float(value) * inverse_scale for value in face[:4]]
        confidence = float(face[14])
        if confidence < FACE_SCORE_THRESHOLD:
            continue

        # Expand the detector's face rectangle to include hairline and nearby
        # facial area, not just the tight eye/nose/mouth detection rectangle.
        pad_x = max(12.0, width * 0.18)
        pad_top = max(16.0, height * 0.24)
        pad_bottom = max(12.0, height * 0.16)
        left = max(0.0, x - pad_x) + offset_x
        top = max(0.0, y - pad_top) + offset_y
        right = min(float(original_width), x + width + pad_x) + offset_x
        bottom = min(float(original_height), y + height + pad_bottom) + offset_y
        results.append({
            "x1": left,
            "y1": top,
            "x2": right,
            "y2": bottom,
            "confidence": confidence,
        })
    return results


def _intersects(
    caption_box: tuple[int, int, int, int],
    face: dict[str, float],
) -> bool:
    left, top, right, bottom = caption_box
    # Add a small clearance margin around the detected face region.
    face_left = face["x1"] - 10
    face_top = face["y1"] - 10
    face_right = face["x2"] + 10
    face_bottom = face["y2"] + 10
    return not (
        right <= face_left
        or left >= face_right
        or bottom <= face_top
        or top >= face_bottom
    )


class FaceClearance:
    """Cached YuNet face detection and per-caption safe-position selection."""

    def __init__(self, cache_dir: Path, model_path: Path = FACE_MODEL_PATH):
        self.cache_dir = Path(cache_dir)
        self.model_path = Path(model_path)
        if not self.model_path.is_file():
            raise FileNotFoundError(
                f"YuNet face-detection model is missing: {self.model_path}"
            )
        self.model_sha256 = sha256_file(self.model_path)
        self.algorithm_sha256 = sha256_file(Path(__file__).resolve())
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._detector = None

    def _get_detector(self):
        if self._detector is None:
            self._detector = cv2.FaceDetectorYN_create(
                str(self.model_path),
                "",
                (640, 640),
                FACE_SCORE_THRESHOLD,
                FACE_NMS_THRESHOLD,
                5000,
            )
        return self._detector

    def resolve_caption_y(
        self,
        cache_key: str,
        caption: str,
        preferred_y: float,
        frame_provider: Callable[[], Iterable[tuple[np.ndarray, tuple[int, int, int, int] | None]]],
        line_colors: list[str] | None = None,
        safe_bottom_fraction: float | None = None,
        font_size_px: int | None = None,
        max_width_px: int | None = None,
    ) -> dict:
        cache_path = self.cache_dir / f"{cache_key}.json"
        if cache_path.is_file():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if (
                    cached.get("schema_version") == FACE_CLEARANCE_SCHEMA_VERSION
                    and cached.get("model_sha256") == self.model_sha256
                    and cached.get("algorithm_sha256") == self.algorithm_sha256
                    and isinstance(cached.get("caption_y"), (int, float))
                    and cached.get("status") in {"clear", "no_faces_detected"}
                ):
                    return cached
            except (OSError, json.JSONDecodeError):
                pass

        all_faces: list[dict[str, float]] = []
        samples_checked = 0
        detector = self._get_detector()
        for frame, roi in frame_provider():
            samples_checked += 1
            all_faces.extend(_detect_faces(frame, detector, roi))

        if not all_faces:
            selected_y = float(preferred_y)
            status = "no_faces_detected"
        else:
            candidates = [float(preferred_y)]
            candidates.extend(
                value / 100.0
                for value in range(18, 94, 3)
                if abs(value / 100.0 - float(preferred_y)) > 0.004
            )
            candidates = sorted(
                dict.fromkeys(candidates),
                key=lambda value: (abs(value - float(preferred_y)), value),
            )
            selected_y = None
            for candidate in candidates:
                box = _caption_bounds(
                    caption,
                    candidate,
                    line_colors=line_colors,
                    safe_bottom_fraction=safe_bottom_fraction,
                    font_size_px=font_size_px,
                    max_width_px=max_width_px,
                )
                if not any(_intersects(box, face) for face in all_faces):
                    selected_y = candidate
                    break
            if selected_y is None:
                raise ValueError(
                    "No face-clear caption position is available for "
                    f"{caption!r}. Shorten the caption or adjust the scene composition."
                )
            status = "clear"

        result = {
            "schema_version": FACE_CLEARANCE_SCHEMA_VERSION,
            "model": self.model_path.name,
            "model_sha256": self.model_sha256,
            "algorithm_sha256": self.algorithm_sha256,
            "caption_y": round(float(selected_y), 4),
            "font_size_px": (
                render_utils.CAPTION_FONT_SIZE_PX if font_size_px is None else int(font_size_px)
            ),
            "max_width_px": max_width_px,
            "preferred_y": round(float(preferred_y), 4),
            "repositioned": abs(float(selected_y) - float(preferred_y)) > 0.004,
            "status": status,
            "faces_detected": len(all_faces),
            "samples_checked": samples_checked,
        }
        temporary = cache_path.with_name(f"{cache_path.stem}.{uuid.uuid4().hex}.partial")
        try:
            temporary.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, cache_path)
        finally:
            temporary.unlink(missing_ok=True)
        return result
