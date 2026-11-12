from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

import render_utils


def review_caption(
    text: str,
    *,
    label: str,
    line_colors: list[str] | None = None,
    font_size_px: int = render_utils.CAPTION_FONT_SIZE_PX,
    max_width_px: int | None = None,
) -> dict[str, Any]:
    font = ImageFont.truetype(str(render_utils.FONT_PATH), font_size_px)
    lines = render_utils.caption_line_layout(
        text,
        font,
        line_colors,
        max_width_px=max_width_px,
    )
    draw = ImageDraw.Draw(Image.new("RGB", (render_utils.WIDTH, render_utils.HEIGHT)))
    stroke = (
        render_utils.CAPTION_STROKE_WIDTH_PX
        if render_utils.CAPTION_STROKE_ENABLED
        else 0
    )
    widths = []
    for line, _ in lines:
        box = draw.textbbox((0, 0), line, font=font, stroke_width=stroke)
        widths.append(box[2] - box[0])

    authored_lines = [
        line
        for line in text.replace("\\n", "\n").splitlines()
        if line.strip()
    ]
    warnings: list[str] = []
    if len(lines) > len(authored_lines):
        warnings.append("authored line wrapped again; revise the line breaks")
    if len(widths) > 1 and max(widths) > 0:
        ratio = min(widths) / max(widths)
        if ratio < 0.55:
            warnings.append("caption lines are visibly uneven; rebalance the block")
    else:
        ratio = 1.0

    return {
        "label": label,
        "lines": [line for line, _ in lines],
        "line_widths_px": widths,
        "shortest_to_longest_ratio": round(ratio, 3),
        "status": "review_required" if warnings else "passed",
        "warnings": warnings,
    }


def review_profile(profile_path: Path, hook_text: str | None = None) -> dict[str, Any]:
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    results: list[dict[str, Any]] = []
    for segment_index, segment in enumerate(profile.get("segments", []), 1):
        if segment.get("type") == "still":
            caption = str(segment.get("caption", ""))
            if caption.strip():
                results.append(
                    review_caption(
                        caption,
                        label=f"segment {segment_index}",
                        line_colors=segment.get("caption_line_colors"),
                    )
                )
        elif segment.get("type") == "keyframed_image":
            for caption_index, caption in enumerate(segment.get("captions", []), 1):
                value = str(caption.get("text", ""))
                if value.strip():
                    results.append(
                        review_caption(
                            value,
                            label=f"segment {segment_index} caption {caption_index}",
                        )
                    )

    if hook_text:
        results.insert(
            0,
            review_caption(
                hook_text,
                label="opening hook",
                font_size_px=render_utils.HOOK_FONT_SIZE_PX,
                max_width_px=render_utils.HOOK_TEXT_WIDTH_PX,
            ),
        )

    warnings = [item for item in results if item["warnings"]]
    return {
        "profile": str(profile_path),
        "status": "review_required" if warnings else "passed",
        "captions_checked": len(results),
        "warnings": warnings,
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check caption line wrapping and visual balance before rendering"
    )
    parser.add_argument("profile", type=Path, help="Profile JSON path")
    parser.add_argument("--hook", default=None, help="Selected hook text, when available")
    args = parser.parse_args()
    print(json.dumps(review_profile(args.profile, args.hook), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
