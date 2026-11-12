# Production Workflow

Check the actual project checkpoint before resuming an existing adaptation.

## 1. Intake
Submit a YouTube Shorts/watch URL, authorized direct video URL or local video file. The intake tool validates and downloads supported sources into workspace/inbox/. Playlists are disabled.

## 2. Analyze
Run analyze_inspiration_video using the inbox-relative path. Analysis probes duration, dimensions and frame rate; detects scene changes; extracts representative frames; and reads timed on-screen captions with OCR. A sequential decode pass is used for frame extraction and OCR sampling.

Analysis results and intermediate files live under workspace/working/. Completed analysis can be reused when source SHA-256, settings, pipeline code hash and OpenCV version match.

## 3. Match visuals
Identify distinct images/footage in the inspiration. Search for close, good-quality matches with adequate resolution. Prefer matching composition and subject. Do not use screenshots or extracted reference frames as final artwork. Keep routine sourcing efficient.

## 4. Prepare adaptation
Create/update the project asset folder and JSON profile. Keep the inspiration sequence and narrative. Rephrase original captions to improve engagement, but do not replace the story, change its order or omit substantial information.

Author line breaks deliberately. Select a reaction clip appropriate to the hook. Prepare hook options and ask the user to choose.

## 5. Render
Call build_configured_short with the profile, selected reaction and exact user-approved hook_text. Hook text is mandatory; do not render before the user chooses.

## 6. Validate and review
Run profile/content validation and inspect rendered frames. Check caption wording and line breaks, symmetrical alignment, unexpected wrapping, face clearance, hook/reaction composition, output duration/resolution/frame rate/codec and full FFmpeg decoding.

Update the project checkpoint only with verified outcomes.

## 7. Deliver
Write the configured output filename in workspace/output/. The renderer replaces the configured output atomically after validation. Do not create version-suffixed duplicates unless a separate variant is requested.
