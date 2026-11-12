from __future__ import annotations

from pathlib import Path
from typing import Iterator

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageFilter, ImageEnhance

WIDTH = 1080
HEIGHT = 1920
FPS = 30
FONT_PATH = Path(__file__).resolve().parent / "fonts" / "F37-Caslon" / "F37Caslon-BoldDisplayCondensed.otf"
CAPTION_FONT_SIZE_PX = 70
HOOK_FONT_SIZE_PX = 71
HOOK_TEXT_WIDTH_PX = 860
CAPTION_STROKE_ENABLED = True
CAPTION_STROKE_COLOR = (0, 0, 0)
CAPTION_STROKE_WIDTH_PX = 6

# Conservative Shorts safe zone: symmetrical side clearance and vertical
# protection for the platform UI overlays.
SAFE_LEFT = round(WIDTH * 0.13)
SAFE_RIGHT = WIDTH - round(WIDTH * 0.13)
SAFE_TOP = 288 + round(HEIGHT * 0.03)
SAFE_BOTTOM = HEIGHT - 672 - round(HEIGHT * 0.03)
SAFE_CENTER_X = WIDTH // 2
SAFE_TEXT_WIDTH = 2 * min(SAFE_CENTER_X - SAFE_LEFT, SAFE_RIGHT - SAFE_CENTER_X)

# A reaction introduction is standardized across all project profiles.
HOOK_FRAMES = 105


def compose_full_frame(source: Image.Image) -> Image.Image:
    """Preserve the complete source over a softened fill; never crop the foreground."""
    source = source.convert("RGB")
    background = ImageOps.fit(
        source,
        (WIDTH, HEIGHT),
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )
    background = background.filter(ImageFilter.GaussianBlur(radius=42))
    background = ImageEnhance.Brightness(background).enhance(0.52)

    foreground = ImageOps.contain(
        source,
        (WIDTH, HEIGHT),
        method=Image.Resampling.LANCZOS,
    )
    x = (WIDTH - foreground.width) // 2
    y = (HEIGHT - foreground.height) // 2
    background.paste(foreground, (x, y))
    return background


def _fit_video_frame(frame: np.ndarray) -> np.ndarray:
    """Fit the entire reaction frame; bypass all work for native 9:16 inputs."""
    if frame.shape[1] == WIDTH and frame.shape[0] == HEIGHT:
        return frame
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    image = Image.fromarray(rgb, mode="RGB")
    composed = compose_full_frame(image)
    return cv2.cvtColor(np.asarray(composed), cv2.COLOR_RGB2BGR)


def wrap_caption(
    caption: str,
    font: ImageFont.FreeTypeFont,
    max_width_px: int | None = None,
) -> str:
    """Preserve deliberate line breaks and wrap only lines that exceed the safe width."""
    draw = ImageDraw.Draw(Image.new("RGB", (WIDTH, HEIGHT)))
    max_width = (SAFE_TEXT_WIDTH if max_width_px is None else int(max_width_px)) - 8
    normalized = caption.replace("\\n", "\n")
    paragraphs = normalized.splitlines() or [normalized]
    wrapped: list[str] = []

    for paragraph in paragraphs:
        words = paragraph.split()
        if not words:
            continue
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            bounds = draw.textbbox(
                (0, 0), candidate, font=font, stroke_width=CAPTION_STROKE_WIDTH_PX
            )
            if bounds[2] - bounds[0] <= max_width:
                current = candidate
            else:
                wrapped.append(current)
                current = word
        wrapped.append(current)

    for line in wrapped:
        bounds = draw.textbbox(
            (0, 0), line, font=font, stroke_width=CAPTION_STROKE_WIDTH_PX
        )
        if bounds[2] - bounds[0] > max_width:
            raise ValueError(
                f"Caption word cannot fit the safe zone at fixed {CAPTION_FONT_SIZE_PX}px: {line!r}"
            )
    if len(wrapped) > 4:
        raise ValueError("Caption requires more than four lines at the fixed font size.")
    return "\n".join(wrapped)


def caption_line_layout(
    caption: str,
    font: ImageFont.FreeTypeFont,
    line_colors: list[str] | None = None,
    max_width_px: int | None = None,
) -> list[tuple[str, tuple[int, int, int]]]:
    """Wrap authored caption lines and assign a supported color to each line."""
    normalized = caption.replace("\\n", "\n")
    authored_lines = normalized.splitlines()
    if not authored_lines:
        authored_lines = [normalized]

    if line_colors is None:
        normalized_lines = wrap_caption(normalized, font, max_width_px=max_width_px)
        return [(line, (255, 255, 255)) for line in normalized_lines.splitlines()]
    else:
        if not isinstance(line_colors, list) or len(line_colors) != len(authored_lines):
            raise ValueError(
                "caption_line_colors must contain one color name for each authored caption line."
            )
        requested_colors = line_colors

    palette = {
        "white": (255, 255, 255),
        "yellow": (255, 215, 0),
    }
    result: list[tuple[str, tuple[int, int, int]]] = []
    for authored_line, requested_color in zip(authored_lines, requested_colors):
        color_name = str(requested_color).strip().lower()
        if color_name not in palette:
            raise ValueError(
                f"Unsupported caption line color {requested_color!r}; use white or yellow."
            )
        if not authored_line.strip():
            continue
        wrapped = wrap_caption(authored_line, font, max_width_px=max_width_px).splitlines()
        result.extend((line, palette[color_name]) for line in wrapped)

    if not result:
        raise ValueError("A visible caption must contain at least one non-empty line.")
    if len(result) > 4:
        raise ValueError("Caption requires more than four lines at the fixed font size.")
    return result


def caption_frame(
    rgb: np.ndarray,
    caption: str,
    y_fraction: float,
    line_colors: list[str] | None = None,
    safe_bottom_fraction: float | None = None,
) -> np.ndarray:
    """Draw centered Caslon caption lines with optional per-line colors and black outline."""
    if not FONT_PATH.is_file():
        raise FileNotFoundError(f"Caslon font not found: {FONT_PATH}")
    if not 0.0 <= float(y_fraction) <= 1.0:
        raise ValueError("Caption y_fraction must be between 0 and 1.")
    if safe_bottom_fraction is not None and not 0.65 <= float(safe_bottom_fraction) <= 0.98:
        raise ValueError("Caption safe_bottom_fraction must be between 0.65 and 0.98.")

    image = Image.fromarray(rgb, mode="RGB")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(FONT_PATH), CAPTION_FONT_SIZE_PX)
    lines = caption_line_layout(caption, font, line_colors)
    stroke_width = CAPTION_STROKE_WIDTH_PX if CAPTION_STROKE_ENABLED else 0

    line_bounds = [
        draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
        for text, _ in lines
    ]
    text_width = max(bounds[2] - bounds[0] for bounds in line_bounds)
    if text_width > SAFE_TEXT_WIDTH - 8:
        raise ValueError(
            f"Caption is too wide for the centered safe zone at {CAPTION_FONT_SIZE_PX}px."
        )

    ascent, descent = font.getmetrics()
    line_height = ascent + descent
    line_spacing = 10
    caption_height = line_height * len(lines) + line_spacing * (len(lines) - 1)

    requested_y = int(HEIGHT * y_fraction)
    safe_min_y = SAFE_TOP + caption_height // 2 + 36
    safe_bottom = (
        SAFE_BOTTOM
        if safe_bottom_fraction is None
        else round(HEIGHT * float(safe_bottom_fraction))
    )
    safe_max_y = safe_bottom - caption_height // 2 - 36
    if safe_max_y < safe_min_y:
        raise ValueError("Caption safe area is too small for the requested text.")
    caption_y = min(max(requested_y, safe_min_y), safe_max_y)

    first_line_y = caption_y - (len(lines) - 1) * (line_height + line_spacing) / 2
    for index, (text, color) in enumerate(lines):
        line_y = round(first_line_y + index * (line_height + line_spacing))
        draw.text(
            (WIDTH // 2, line_y),
            text,
            font=font,
            fill=color,
            anchor="mm",
            stroke_width=stroke_width,
            stroke_fill=CAPTION_STROKE_COLOR,
        )
    return np.asarray(image, dtype=np.uint8)


def caption_overlay(
    caption: str,
    y_fraction: float,
    safe_bottom_fraction: float | None = None,
    font_size_px: int | None = None,
    max_width_px: int | None = None,
) -> tuple[int, int, np.ndarray, np.ndarray]:
    """Rasterize a stable caption once so motion frames can reuse a small overlay."""
    if not FONT_PATH.is_file():
        raise FileNotFoundError(f"Caslon font not found: {FONT_PATH}")
    if not 0.0 <= float(y_fraction) <= 1.0:
        raise ValueError("Caption y_fraction must be between 0 and 1.")
    selected_font_size = CAPTION_FONT_SIZE_PX if font_size_px is None else int(font_size_px)
    font = ImageFont.truetype(str(FONT_PATH), selected_font_size)
    normalized = wrap_caption(caption, font, max_width_px=max_width_px)
    canvas = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    bounds = draw.multiline_textbbox(
        (0, 0), normalized, font=font, spacing=10, align="center",
        stroke_width=CAPTION_STROKE_WIDTH_PX if CAPTION_STROKE_ENABLED else 0,
    )
    caption_height = bounds[3] - bounds[1]
    requested_y = int(HEIGHT * y_fraction)
    if safe_bottom_fraction is not None and not 0.65 <= float(safe_bottom_fraction) <= 0.98:
        raise ValueError("Caption safe_bottom_fraction must be between 0.65 and 0.98.")
    safe_bottom = SAFE_BOTTOM if safe_bottom_fraction is None else round(HEIGHT * float(safe_bottom_fraction))
    safe_min_y = SAFE_TOP + caption_height // 2 + 36
    safe_max_y = safe_bottom - caption_height // 2 - 36
    if safe_max_y < safe_min_y:
        raise ValueError("Caption safe area is too small for the requested text.")
    caption_y = min(max(requested_y, safe_min_y), safe_max_y)
    draw.multiline_text(
        (WIDTH // 2, caption_y), normalized, font=font, fill=(255, 255, 255, 255),
        anchor="mm", align="center", spacing=10,
        stroke_width=CAPTION_STROKE_WIDTH_PX if CAPTION_STROKE_ENABLED else 0,
        stroke_fill=(*CAPTION_STROKE_COLOR, 255),
    )
    alpha = canvas.getchannel("A")
    bbox = alpha.getbbox()
    if bbox is None:
        raise ValueError("Caption overlay contains no visible text.")
    cropped = canvas.crop(bbox)
    rgba = np.asarray(cropped, dtype=np.uint8)
    return bbox[0], bbox[1], rgba[:, :, :3], rgba[:, :, 3]


def apply_caption_overlay(rgb: np.ndarray, overlay: tuple[int, int, np.ndarray, np.ndarray]) -> np.ndarray:
    """Alpha-blend only the small text rectangle instead of redrawing on the full frame."""
    x, y, text_rgb, alpha_u8 = overlay
    height, width = alpha_u8.shape
    roi = rgb[y:y + height, x:x + width]
    alpha = alpha_u8.astype(np.float32)[:, :, None] / 255.0
    roi[:] = (
        text_rgb.astype(np.float32) * alpha +
        roi.astype(np.float32) * (1.0 - alpha)
    ).astype(np.uint8)
    return rgb


def reaction_frames(
    reaction_path: Path,
    hook_caption: str,
    hook_y_fraction: float = 0.25,
) -> Iterator[np.ndarray]:
    """Decode the reaction clip sequentially and reuse one pre-rendered hook overlay."""
    capture = cv2.VideoCapture(str(reaction_path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open reaction clip: {reaction_path.name}")

    source_fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    source_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if source_fps <= 0 or source_frames <= 0:
        capture.release()
        raise RuntimeError("Reaction clip has invalid duration or frame rate.")

    # The hook is identical on every reaction frame. Rasterize it once rather
    # than reloading the font, laying out text, and creating a full PIL image
    # 105 times per render.
    hook_overlay = caption_overlay(
        hook_caption,
        hook_y_fraction,
        font_size_px=HOOK_FONT_SIZE_PX,
        max_width_px=HOOK_TEXT_WIDTH_PX,
    )
    last_frame = None
    next_source_index = 0
    try:
        for output_index in range(HOOK_FRAMES):
            target_source_index = int(round(output_index * source_fps / FPS))
            while next_source_index <= target_source_index and next_source_index < source_frames:
                ok, frame = capture.read()
                if not ok or frame is None:
                    break
                last_frame = frame
                next_source_index += 1

            if last_frame is None:
                capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, frame = capture.read()
                if not ok or frame is None:
                    raise RuntimeError("Could not read the reaction clip's first frame.")
                last_frame = frame
                next_source_index = 1

            if last_frame.shape[1] == WIDTH and last_frame.shape[0] == HEIGHT:
                # Native 1080x1920 reaction clips need only a color conversion.
                rgb = cv2.cvtColor(last_frame, cv2.COLOR_BGR2RGB)
            else:
                rgb = cv2.cvtColor(_fit_video_frame(last_frame), cv2.COLOR_BGR2RGB)
            yield apply_caption_overlay(rgb, hook_overlay)
    finally:
        capture.release()
