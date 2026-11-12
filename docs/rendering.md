# Rendering, Profiles and Quality Checks

## Configurable renderer
The former video-specific renderers were consolidated into workspace/render_engine.py, exposed through build_configured_short. Shared helpers live in workspace/render_utils.py.

The tool accepts a JSON project profile, reaction clip and explicit hook_text. Hook selection belongs to the user; blank or missing hook text must not be replaced with a default.

## Profiles
Profiles live in workspace/profiles/. template.json is the starting point for a new adaptation. Profiles describe timing reference, output filename, visual assets, scene durations, rewritten captions and optional keyframed-image motion.

Supported segment types include still and keyframed_image. Still captions can specify per-line colors (white/yellow); omitted colors default to white and an empty caption suppresses text. A still segment may define caption_safe_bottom_fraction; timed captions can also use safe_bottom_fraction.

New profiles default to schema version 1. Schema version 2 supports detailed per-image source metadata when specifically useful. Do not add extensive provenance records to routine projects by default.

## Output conventions and typography
- 1080 × 1920 (9:16), 30 fps, H.264 MP4
- 3.5-second reaction opening (105 frames)
- One explicitly selected hook
- No audio unless implementation/profile is deliberately changed
- F37 Caslon Bold Display Condensed caption font
- Main captions: 70 px with 6 px black stroke
- Hook: 71 px with 860 px text width
- Centered horizontal alignment with authored line breaks preserved

The source image is contained rather than destructively center-cropped; a softened/darkened enlargement of the same source can fill unused canvas space.

## Caption layout
Author line breaks in the profile. Prefer two balanced lines when they fit; use three only when necessary. Avoid one-word orphan lines, dangling short lines and accidental renderer wrapping. Center each line on the same axis. Inspect actual rendered frames rather than trusting character counts alone.

No period characters are allowed in hook or caption text. The validator rejects them. Blank authored lines and obvious paths into scene_frames/ are also rejected.

## Face clearance
workspace/face_clearance.py uses OpenCV YuNet and the pinned model workspace/models/face_detection_yunet_2023mar.onnx. Detection/placement results are cached separately from encoded render chunks.

The renderer checks static artwork, sampled keyframed artwork and six samples from the opening reaction. It moves captions when a clear position is available. A no_faces_detected result is not proof that a face is absent; visually inspect missed or low-confidence cases.

## Caching and output
The renderer caches the reaction opening and two-second visual chunks using content-addressed inputs, reuses unchanged H.264 segments and assembles the final MP4 with FFmpeg stream copy. The configured output is replaced atomically after validation.

The render-segment cache is disposable and can be regenerated, at the cost of re-encoding affected chunks. Face-clearance cache is separate. See render-performance-research.md.
