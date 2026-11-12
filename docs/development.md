# Development and Dependencies

## Environment
The local environment is Windows 10 Pro, 64-bit, with Python 3.12. The project uses .venv/; do not commit or copy it as source.

## Processing stack
- MCP Python SDK for the local MCP server
- OpenAI Secure MCP Tunnel client for connector access
- imageio-ffmpeg for portable FFmpeg
- yt-dlp plus yt-dlp-ejs for supported YouTube downloads
- Node.js as the JavaScript runtime for current YouTube player challenge solving
- PySceneDetect for scene-change detection
- RapidOCR with ONNX Runtime for on-screen text recognition
- Pillow, OpenCV headless and NumPy for image/frame processing

The last dependency/source/environment audit found no PyTorch (torch) dependency or usage. Recheck the live environment if this becomes relevant again.

Pinned dependency versions are in requirements.txt. Install with:
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt

## Code map
| File | Role |
|---|---|
| server.py | MCP server and tool definitions |
| video_pipeline.py | Video analysis pipeline |
| workspace/media_intake.py | Source intake/download validation |
| workspace/render_engine.py | Main configurable renderer |
| workspace/render_utils.py | Shared rendering utilities |
| workspace/face_clearance.py | Face-aware caption placement |
| workspace/quality_validation.py | Pre-render validation |
| workspace/pipeline_state.py | Stage checkpoint persistence |

## Change and test procedure
1. Inspect implementation and relevant profiles.
2. Make the smallest focused change.
3. Run Python compilation for changed modules.
4. Run relevant smoke tests and profile validation.
5. If rendering behavior changed, render a representative project, inspect frames and run full FFmpeg decode.
6. Update relevant documentation.

Do not claim tests or visual reviews that were not run.

## Reload behavior
The server reloads video_pipeline.py for each analysis call and reloads render_utils.py, face_clearance.py, quality_validation.py and render_engine.py for each render call. Code-only changes to these modules generally do not require a server restart.

Changes to server.py, tool registrations or MCP schemas require restarting the server/tunnel and refreshing connector discovery as appropriate.
