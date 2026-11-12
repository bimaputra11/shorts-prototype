# Architecture and Data Flow

## System shape
Shorts Prototype is a Windows-hosted, local-first Python application exposed to ChatGPT through a local MCP server and secure tunnel.

Inspiration URL/file -> server.py MCP tools -> media_intake.py -> workspace/inbox/
-> video_pipeline.py (probe, scenes, representative frames, OCR)
-> workspace/working/<project>/ (analysis, samples, matched assets)
-> workspace/profiles/<project>.json (timeline, visuals, captions, motion)
-> build_configured_short -> render_engine.py
-> render_utils.py / face_clearance.py -> quality_validation.py
-> workspace/output/<configured-name>.mp4

## Main components
| Component | Responsibility |
|---|---|
| server.py | MCP server lifecycle and tool registration |
| workspace/media_intake.py | URL validation, download and video validation |
| video_pipeline.py | Probing, scene detection, representative frames and OCR |
| workspace/pipeline_state.py | Per-project stage checkpoints |
| workspace/render_engine.py | Configurable rendering and cache orchestration |
| workspace/render_utils.py | Composition, captions, reaction and encoding helpers |
| workspace/face_clearance.py | YuNet face detection and caption-position selection |
| workspace/quality_validation.py | Profile and caption validation before encoding |

## Workspace boundaries
- inbox/: downloaded or locally supplied inspiration videos
- working/: project analysis, matched artwork, contact sheets and pipeline state
- profiles/: JSON render instructions and starter template
- reactions/: reusable creator reaction clips
- cache/: disposable/rebuildable processing caches
- models/ and fonts/: runtime assets required by processing/rendering
- output/: finished deliverables

A profile and prepared assets may remain usable after the original inbox video is removed, but re-analysis then requires obtaining the source video again.

## Runtime behavior
The MCP server exposes intake, analysis, file access and configured rendering tools. It reloads video_pipeline.py for each analysis call and reloads render_utils.py, face_clearance.py, quality_validation.py and render_engine.py for each render call. Changes to these modules generally do not require a server restart. Changes to server.py or MCP schemas require restarting the server/tunnel and refreshing connector discovery as appropriate.

See operations.md for connection details.
