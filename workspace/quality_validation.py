from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from typing import Any


def _validate_on_screen_text(value: Any, label: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text.")
    if "." in value:
        raise ValueError(
            f"{label} contains a period. Period characters are not allowed in any on-screen line."
        )
    if any(not line.strip() for line in value.replace("\\n", "\n").splitlines()):
        raise ValueError(f"{label} contains an empty on-screen line.")


def validate_hook(hook_text: str) -> str:
    if not isinstance(hook_text, str):
        raise ValueError("Choose a hook and provide it as hook_text before rendering.")
    hook = hook_text.strip().replace("\\n", "\n")
    hook = "\n".join(line.strip() for line in hook.splitlines())
    if not hook:
        raise ValueError("A hook must be explicitly selected before rendering. No default hook is used.")
    if len(hook) > 120:
        raise ValueError("hook_text must contain no more than 120 characters.")
    if len(hook.splitlines()) > 3:
        raise ValueError("hook_text must use no more than three lines.")
    _validate_on_screen_text(hook, "hook_text")
    return hook


def validate_profile_content(profile: dict[str, Any], workspace: Path) -> dict[str, Any]:
    """Enforce display-text rules and check provenance metadata before rendering."""
    version = profile.get("schema_version")
    if version not in {1, 2}:
        raise ValueError("Unsupported profile schema_version. Supported versions are 1 and 2.")

    segments = profile.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ValueError("Project configuration must contain a non-empty segments array.")

    asset_count = 0
    source_count = 0
    legacy_missing: list[str] = []
    checked_text_lines = 0
    for index, segment in enumerate(segments, start=1):
        if not isinstance(segment, dict):
            raise ValueError(f"Segment {index} must be a JSON object.")

        asset = segment.get("asset")
        if not isinstance(asset, str) or not asset.strip():
            raise ValueError(f"Segment {index} is missing its visual asset path.")
        asset_path = Path(asset)
        if asset_path.is_absolute() or ".." in asset_path.parts:
            raise ValueError(f"Segment {index} asset path must remain relative to the workspace.")
        if "scene_frames" in {part.casefold() for part in asset_path.parts}:
            raise ValueError(
                f"Segment {index} points into scene_frames. Reference-video frames cannot be rendered as artwork."
            )
        resolved_asset = (workspace.resolve() / asset_path).resolve()
        if resolved_asset != workspace.resolve() and workspace.resolve() not in resolved_asset.parents:
            raise ValueError(f"Segment {index} asset path resolves outside the workspace.")
        if not resolved_asset.is_file():
            raise FileNotFoundError(f"Segment {index} asset does not exist: {asset}")
        if version == 2 and "assets" not in {part.casefold() for part in asset_path.parts}:
            raise ValueError(
                f"Segment {index} asset must be stored under a project-specific assets folder."
            )
        asset_count += 1

        if version == 2:
            source = segment.get("source")
            if not isinstance(source, dict):
                raise ValueError(
                    f"Segment {index} is missing its source record. Version 2 profiles require one verified original-source record per asset."
                )
            source_url = source.get("url")
            parsed = urlsplit(str(source_url or ""))
            if parsed.scheme != "https" or not parsed.netloc:
                raise ValueError(f"Segment {index} source.url must be a valid HTTPS page URL.")
            for field in ("page_title", "publisher", "verification_note"):
                value = source.get(field)
                if not isinstance(value, str) or len(value.strip()) < 3:
                    raise ValueError(f"Segment {index} source.{field} is required.")
            if source.get("verified_original") is not True:
                raise ValueError(
                    f"Segment {index} source must be marked verified_original=true after checking the source page."
                )
            expected_hash = str(source.get("asset_sha256") or "").lower()
            if len(expected_hash) != 64 or any(char not in "0123456789abcdef" for char in expected_hash):
                raise ValueError(f"Segment {index} source.asset_sha256 must be a 64-character SHA-256 hex digest.")
            digest = hashlib.sha256()
            with resolved_asset.open("rb") as image_file:
                for block in iter(lambda: image_file.read(1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest() != expected_hash:
                raise ValueError(
                    f"Segment {index} asset hash does not match its source record. Reconfirm the source or replace the stale record."
                )
            retrieved_at = source.get("retrieved_at_utc")
            try:
                parsed_time = datetime.fromisoformat(str(retrieved_at).replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(f"Segment {index} source.retrieved_at_utc must be an ISO 8601 timestamp.") from exc
            if parsed_time.tzinfo is None:
                raise ValueError(f"Segment {index} source.retrieved_at_utc must include a timezone.")
            source_count += 1
        else:
            legacy_missing.append(asset)

        kind = segment.get("type")
        captions: list[str] = []
        if kind == "still":
            caption = str(segment.get("caption", ""))
            if caption.strip():
                captions.append(caption)
        elif kind == "keyframed_image":
            raw_captions = segment.get("captions", [])
            if not isinstance(raw_captions, list):
                raise ValueError(f"Segment {index} captions must be an array.")
            for caption_index, item in enumerate(raw_captions, start=1):
                if not isinstance(item, dict):
                    raise ValueError(
                        f"Segment {index} caption {caption_index} must be an object."
                    )
                captions.append(str(item.get("text", "")))
        else:
            raise ValueError(
                f"Segment {index} has unsupported type {kind!r}; use 'still' or 'keyframed_image'."
            )

        for caption_index, caption in enumerate(captions, start=1):
            _validate_on_screen_text(caption, f"segment {index} caption {caption_index}")
            checked_text_lines += len(caption.replace("\\n", "\n").splitlines())

    if version == 2 and source_count != asset_count:
        raise ValueError("Every visual asset must have exactly one verified source record.")

    return {
        "passed": True,
        "schema_version": version,
        "asset_count": asset_count,
        "source_records": source_count,
        "source_provenance": (
            "complete_source_records_present" if version == 2 else "legacy_not_recorded"
        ),
        "legacy_assets_without_source_records": len(legacy_missing),
        "checked_caption_lines": checked_text_lines,
        "period_rule": "passed; no period characters in any caption line",
        "reference_frame_rule": "passed; no asset path points into scene_frames",
        "source_review_note": (
            "Source-page metadata is present; original-image identity still requires source-page and visual review"
            if version == 2
            else "Legacy profile: source URLs were not recorded, so provenance is not programmatically verified"
        ),
    }
