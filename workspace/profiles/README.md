# Render project profiles

Each JSON profile describes one video's visuals and timing. The same MCP renderer handles every profile; do not create a new render tool for a new video.

## Default profile format

Use `schema_version: 1` for routine projects. It requires no per-image source dossier or asset hash. Version 2 remains supported only when detailed source records are specifically useful; do not make that extra research the default workflow.

Required root properties: `schema_version`, `project_name`, `output_filename`, `timing_reference`, and `segments`. `reference_duration_seconds` with `reference_duration_tolerance_seconds` helps ensure the correct inspiration video is used. All file paths are relative to `workspace/`.

## Image matching

Inspect the visual scene in the inspiration Short, identify the subject and composition, then search online for a close visual match. Prioritize resemblance, image quality, and sufficient resolution. Keep this stage efficient; do not turn it into extensive source-page research or provenance paperwork.

Use a clean image found online, not a screenshot or extracted frame from the inspiration video. Never cover, blur, crop around, or otherwise alter baked-in captions or overlays on a reference frame. If the exact image is difficult to find, use the closest high-quality visual match and keep the scene's role in the sequence.

Keep downloaded assets together under a project-specific folder in `workspace/working/<project>/assets/`.

## Supported segment types

- `still`: `asset`, `frames`, and optional `caption`, `caption_y`, `caption_line_colors`, and `caption_safe_bottom_fraction`. The source image is fitted in full over a softened fill. An empty caption draws no text. `caption_line_colors` sets one color per authored caption line (white or yellow); omitted colors default to white.
- `keyframed_image`: `asset`, `frames`, `source_start_seconds`, `camera_keyframes`, and `captions`. Each caption has `start_seconds`, `end_seconds`, `text`, and optional `y` and `safe_bottom_fraction`. The camera-keyframe JSON contains ordered `source_seconds` and 3x3 `homography` matrices.

## Caption writing and layout

The inspiration video's captions are the script of record. Rephrase them to be more engaging and natural, but preserve the original narrative, order, emphasis, and information. Do not replace the story with a new factual explanation, add background the source did not include, or substantially change the details.

Author line breaks deliberately. All lines must be centered on the same horizontal axis, with consistent spacing and a visually balanced block. Prefer two balanced lines when they fit. Use three lines only when the text needs them. Avoid isolated words, dangling short lines, and accidental extra wrapping.

Examples of preferred line breaks:

```text
The dispute centered on
images she had donated
for public access
```

```text
Carol Highsmith sued Getty
and Alamy for $1 billion
```

The profile's authored line breaks are preserved. Review the actual rendered line layout before export and adjust the profile text when a line wraps unexpectedly.

No period characters are allowed anywhere in hook or caption text. Every authored line must contain visible text. The validator rejects periods and blank lines before encoding.

## Typography and face clearance

- Main captions: 70 px
- Main caption outline: 6 px, black
- Opening hook: 71 px, with a wider 860 px text area so the hook can remain cleanly wrapped
- Keep every caption visually centered and away from faces
- Face detection may reposition captions automatically; inspect scenes where no face was detected and review the finished video

Every project receives the standard 3.5-second reaction opening. The hook is not stored in the profile. Present hook options and wait for the user to select one, then supply the exact choice as the required `hook_text` argument to `build_configured_short`.

The renderer replaces the profile's configured output file atomically after successful frame validation; it does not create `-v2` duplicates. Temporary render tests must not be left in the final output directory.

`template.json` is the starter profile. Replace the placeholder project name, paths, durations, captions, and output filename.
