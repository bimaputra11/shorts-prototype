# Shorts Prototype

A local-first system for adapting YouTube Shorts. It analyzes an inspiration video, finds close visual matches, rephrases on-screen captions without changing the story, adds a creator reaction and a user-selected hook, then exports a vertical MP4.

## Documentation map
- [Agent and contributor instructions](AGENTS.md)
- [Architecture and data flow](docs/architecture.md)
- [Production workflow](docs/workflow.md)
- [Profiles, rendering and validation](docs/rendering.md)
- [Editorial and visual rules](docs/project-rules.md)
- [Development and dependencies](docs/development.md)
- [Local operations, tunnel and security](docs/operations.md)
- [Render performance research](docs/render-performance-research.md)
- [Secure MCP connection setup](docs/secure-mcp-connection-setup.md)

## Product outcome
Input: an inspiration Short URL or local video file, plus the reaction-clip library.
Output: a finished 1080 × 1920 H.264 MP4 with an opening reaction, a hook explicitly selected by the user, visually matched assets and engaging captions faithful to the original story.

The intended experience is to submit an inspiration video, review hook choices, approve one, and receive the finished adaptation. Internal asset inventories and processing logs are implementation details, not product deliverables.

## Current implementation
Implemented: local MCP connectivity, source-video intake, scene detection and OCR analysis, project profiles, a configurable renderer, cached render chunks, face-aware caption placement and output validation.

Still incomplete: general automatic visual-asset matching, automatic reaction selection, automatic caption rewriting/timing and one-step end-to-end orchestration. Important editorial and asset-selection steps currently involve the assistant and user.

The project uses Windows and Python 3.12. Its processing stack includes FFmpeg, yt-dlp, PySceneDetect, RapidOCR with ONNX Runtime, Pillow, OpenCV headless and NumPy. See docs/development.md.

## Repository map
- AGENTS.md: agent/contributor instructions
- server.py: local MCP server and tool registrations
- video_pipeline.py: scene detection and OCR analysis
- scripts/: tunnel controller
- docs/: focused documentation
- workspace/media_intake.py: source URL validation and downloads
- workspace/render_engine.py: configurable renderer
- workspace/render_utils.py: shared rendering helpers
- workspace/face_clearance.py: face detection and caption placement
- workspace/quality_validation.py: pre-render checks
- workspace/pipeline_state.py: resumable stage checkpoints
- workspace/profiles/: JSON timelines and template
- workspace/reactions/: reaction-video library
- workspace/working/: per-project analysis and matched assets
- workspace/cache/: reusable render and analysis caches
- workspace/models/ and workspace/fonts/: runtime assets
- workspace/inbox/: inspiration source videos
- workspace/output/: finished exports

Read AGENTS.md first, then the guide relevant to the task. Inspect live code and current project files before relying on historical notes. The tunnel must be running for ChatGPT to access the local MCP server; see docs/operations.md.
