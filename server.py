from __future__ import annotations

import importlib
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from mcp.server import MCPServer

# Do not pass tunnel credentials to tools or child processes.
for name in ("CONTROL_PLANE_API_KEY", "OPENAI_API_KEY", "OPENAI_ADMIN_KEY"):
    os.environ.pop(name, None)

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s %(levelname)s %(message)s",
)

PROJECT_ROOT = Path(__file__).resolve().parent
WORKSPACE = (PROJECT_ROOT / "workspace").resolve()
WORKSPACE.mkdir(parents=True, exist_ok=True)
for folder_name in ("inbox", "reactions", "working", "output"):
    (WORKSPACE / folder_name).mkdir(parents=True, exist_ok=True)

mcp = MCPServer("shorts-prototype")

def resolve_path(path: str = ".") -> Path:
    """Resolve a path for operations that are restricted to workspace."""
    raw = (path or ".").strip()

    if "\x00" in raw:
        raise ValueError("Invalid path.")

    relative = Path(raw)

    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Use a relative path inside workspace.")

    target = (WORKSPACE / relative).resolve()

    if target != WORKSPACE and WORKSPACE not in target.parents:
        raise ValueError("Path is outside the workspace.")

    return target


def resolve_read_path(path: str = ".") -> Path:
    """Resolve a read-only path anywhere inside the project root."""
    raw = (path or ".").strip()

    if "\x00" in raw:
        raise ValueError("Invalid path.")

    relative = Path(raw)

    if relative.is_absolute():
        raise ValueError("Use a relative path from workspace.")

    target = (WORKSPACE / relative).resolve()

    if target != PROJECT_ROOT and PROJECT_ROOT not in target.parents:
        raise ValueError("Read path resolves outside the project root.")

    return target


def display_path(target: Path) -> str:
    """Return paths relative to workspace, including .. for project-root files."""
    return str(Path(os.path.relpath(target, WORKSPACE)))


def output(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


@mcp.tool()
def import_inspiration_source(
    source_url: str,
    filename: str | None = None,
) -> str:
    """Import a reference source into the inspiration workflow.

    Download YouTube Shorts/watch links with yt-dlp or direct HTTP(S) video-file
    URLs. Downloads are validated before being kept in workspace/inbox.
    """
    if filename is not None and len(filename) > 240:
        raise ValueError("filename must be 240 characters or fewer.")

    if str(WORKSPACE) not in sys.path:
        sys.path.insert(0, str(WORKSPACE))
    import media_intake
    media_intake = importlib.reload(media_intake)
    result = media_intake.import_inspiration_source(
        source_url=source_url,
        inbox_dir=WORKSPACE / "inbox",
        filename=filename,
    )
    try:
        from pipeline_state import record_stage
        relative_source = result.get("workspace_relative_path")
        if relative_source:
            state_path = record_stage(
                WORKSPACE,
                relative_source,
                "import",
                {
                    "status": result.get("status"),
                    "source_type": result.get("source_type"),
                    "file": relative_source,
                    "size_bytes": result.get("size_bytes"),
                },
            )
            result["pipeline_state_file"] = str(state_path.relative_to(WORKSPACE))
    except Exception:
        logging.exception("Could not save the import checkpoint.")
    return output(result)

@mcp.tool()
def analyze_inspiration_video(
    video_path: str,
    scene_threshold: float = 27.0,
    ocr_interval_seconds: float = 0.35,
) -> str:
    """Analyze an inspiration Short from workspace/inbox.

    Detect scene changes, extract representative frames, build a contact sheet,
    and read timed on-screen captions. The input path must be relative to workspace.
    """
    if not 1.0 <= float(scene_threshold) <= 100.0:
        raise ValueError("scene_threshold must be between 1 and 100.")
    if not 0.15 <= float(ocr_interval_seconds) <= 5.0:
        raise ValueError("ocr_interval_seconds must be between 0.15 and 5 seconds.")
    source = resolve_path(video_path)
    if not source.is_file():
        raise FileNotFoundError(f"Video not found: {video_path}")
    if source.suffix.lower() not in {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}:
        raise ValueError("Unsupported video format. Use MP4, MOV, M4V, MKV, WEBM, or AVI.")
    if source.stat().st_size > 2_000_000_000:
        raise ValueError("Video exceeds the 2 GB prototype input limit.")

    import video_pipeline
    video_pipeline = importlib.reload(video_pipeline)

    result = video_pipeline.analyze_video(
        source,
        WORKSPACE / "working",
        scene_threshold=float(scene_threshold),
        ocr_interval_seconds=float(ocr_interval_seconds),
    )

    for field in ("contact_sheet", "analysis_file", "captions_file"):
        if result.get(field):
            result[field] = str(
                Path(result[field]).resolve().relative_to(WORKSPACE)
            )

    result["output_directory"] = str(
        Path(result["analysis_file"]).parent
    )
    try:
        from pipeline_state import record_stage
        state_path = record_stage(
            WORKSPACE,
            source,
            "analysis",
            {
                "scene_count": result.get("scene_count"),
                "scene_frames_extracted": result.get("scene_frames_extracted"),
                "caption_segment_count": result.get("caption_segment_count"),
                "source_sha256": result.get("source_sha256"),
                "analysis_cache_hit": result.get("analysis_cache_hit", False),
                "timings_seconds": result.get("analysis_timings_seconds", {}),
            },
        )
        result["pipeline_state_file"] = str(state_path.relative_to(WORKSPACE))
    except Exception:
        logging.exception("Could not save the analysis checkpoint.")
    return output(result)


@mcp.tool()
def build_configured_short(
    reaction_video: str,
    project_config: str,
    hook_text: str,
) -> str:
    """Render any prepared Short using one reusable, project-configured renderer.

    The project config supplies clean visual assets, scene durations, captions,
    and optional image camera-keyframes. hook_text is intentionally required:
    the user must choose the hook before this tool is called. There is no default.
    """
    reaction = resolve_path(reaction_video)
    config = resolve_path(project_config)
    reactions_root = (WORKSPACE / "reactions").resolve()
    profiles_root = (WORKSPACE / "profiles").resolve()
    if reactions_root not in reaction.parents:
        raise ValueError("reaction_video must point to a file inside workspace/reactions.")
    if profiles_root not in config.parents:
        raise ValueError("project_config must point to a JSON profile inside workspace/profiles.")
    if not reaction.is_file():
        raise FileNotFoundError(f"Reaction clip not found: {reaction_video}")
    if not config.is_file():
        raise FileNotFoundError(f"Project configuration not found: {project_config}")
    if reaction.stat().st_size > 2_000_000_000:
        raise ValueError("Reaction clip exceeds the 2 GB prototype input limit.")
    if config.stat().st_size > 2_000_000:
        raise ValueError("Project configuration exceeds the 2 MB input limit.")
    if not isinstance(hook_text, str) or not hook_text.strip():
        raise ValueError("Select one hook before rendering. No default hook is available.")

    if str(WORKSPACE) not in sys.path:
        sys.path.insert(0, str(WORKSPACE))
    import render_utils
    import face_clearance
    import quality_validation
    importlib.reload(render_utils)
    importlib.reload(face_clearance)
    importlib.reload(quality_validation)
    import render_engine
    render_engine = importlib.reload(render_engine)

    profile_data = json.loads(config.read_text(encoding="utf-8"))
    timing_reference = profile_data.get("timing_reference")
    if timing_reference:
        try:
            from pipeline_state import record_stage
            provenance_status = (
                "source_records_present_review_required"
                if profile_data.get("schema_version") == 2
                else "legacy_not_recorded"
            )
            record_stage(
                WORKSPACE,
                timing_reference,
                "profile_prepared",
                {
                    "profile": str(config.relative_to(WORKSPACE)),
                    "schema_version": profile_data.get("schema_version"),
                    "visual_assets": len(profile_data.get("segments", [])),
                },
            )
            record_stage(
                WORKSPACE,
                timing_reference,
                "asset_provenance",
                {"status": provenance_status},
                status=provenance_status,
            )
            record_stage(
                WORKSPACE,
                timing_reference,
                "hook_selected",
                {"hook_text": hook_text},
            )
            record_stage(
                WORKSPACE,
                timing_reference,
                "render",
                {
                    "profile": str(config.relative_to(WORKSPACE)),
                    "hook_text": hook_text,
                    "output_filename": profile_data.get("output_filename"),
                },
                status="in_progress",
            )
        except Exception:
            logging.exception("Could not save the pre-render checkpoint.")

    result = render_engine.build_configured_short(
        workspace=WORKSPACE,
        reaction_video=str(reaction.relative_to(WORKSPACE)),
        project_config=str(config.relative_to(WORKSPACE)),
        hook_text=hook_text,
        output_dir=WORKSPACE / "output",
    )
    result["output_file"] = str(Path(result["output_file"]).resolve().relative_to(WORKSPACE))
    if timing_reference:
        try:
            from pipeline_state import record_stage
            record_stage(
                WORKSPACE,
                timing_reference,
                "rendered",
                {
                    "output_file": result["output_file"],
                    "duration_seconds": result.get("duration_seconds"),
                    "segment_cache": result.get("segment_cache"),
                    "face_clearance": result.get("face_clearance"),
                },
            )
            state_path = record_stage(
                WORKSPACE,
                timing_reference,
                "validated",
                {
                    "frame_validation": result.get("frame_validation"),
                    "profile_validation": result.get("profile_validation"),
                },
            )
            result["pipeline_state_file"] = str(state_path.relative_to(WORKSPACE))
        except Exception:
            logging.exception("Could not save the completed render checkpoint.")
    return output(result)


@mcp.tool()
def server_info() -> str:
    """Show server identity, Python version, and workspace location."""
    return output({
        "server": "shorts-prototype",
        "python": sys.version.split()[0],
        "project_root": str(PROJECT_ROOT),
        "workspace": str(WORKSPACE),
        "status": "running",
    })


@mcp.tool()
def list_files(path: str = ".", limit: int = 200) -> str:
    """List files and folders anywhere inside the project root (read-only)."""
    target = resolve_read_path(path)

    if not target.is_dir():
        raise ValueError("Path is not a directory.")

    limit = max(1, min(int(limit), 500))
    entries = []

    for item in sorted(target.iterdir(), key=lambda p: p.name.lower())[:limit]:
        info = {
            "name": item.name,
            "type": "symlink" if item.is_symlink() else (
                "directory" if item.is_dir() else "file"
            ),
        }

        if not item.is_symlink() and item.is_file():
            info["size_bytes"] = item.stat().st_size

        entries.append(info)

    return output({
        "path": display_path(target),
        "entries": entries,
        "returned": len(entries),
    })


@mcp.tool()
def read_text_file(path: str, max_chars: int = 50000) -> str:
    """Read a UTF-8 text file anywhere inside the project root (read-only)."""
    target = resolve_read_path(path)

    if not target.is_file():
        raise ValueError("File does not exist or is not a regular file.")

    max_chars = max(1, min(int(max_chars), 100000))

    if target.stat().st_size > 2_000_000:
        raise ValueError("File exceeds the 2 MB text-reading limit.")

    text = target.read_text(encoding="utf-8-sig")
    truncated = len(text) > max_chars

    return output({
        "path": display_path(target),
        "content": text[:max_chars],
        "truncated": truncated,
    })


@mcp.tool()
def write_text_file(path: str, content: str, overwrite: bool = False) -> str:
    """Create or update a UTF-8 text file inside the workspace."""
    target = resolve_path(path)

    if target == WORKSPACE:
        raise ValueError("A file path is required.")

    if len(content.encode("utf-8")) > 2_000_000:
        raise ValueError("Content exceeds the 2 MB limit.")

    if target.exists() and not overwrite:
        raise FileExistsError("File exists. Set overwrite=true explicitly.")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")

    return output({
        "success": True,
        "path": str(target.relative_to(WORKSPACE)),
        "bytes_written": target.stat().st_size,
    })


@mcp.tool()
def create_directory(path: str) -> str:
    """Create a directory inside the workspace."""
    target = resolve_path(path)
    target.mkdir(parents=True, exist_ok=True)

    return output({
        "success": True,
        "path": str(target.relative_to(WORKSPACE)),
    })


@mcp.tool()
def get_file_info(path: str) -> str:
    """Inspect metadata anywhere inside the project root (read-only)."""
    target = resolve_read_path(path)

    if not target.exists():
        raise FileNotFoundError(path)

    stat = target.stat()

    return output({
        "path": display_path(target),
        "type": "directory" if target.is_dir() else "file",
        "size_bytes": stat.st_size,
        "modified": datetime.fromtimestamp(
            stat.st_mtime, timezone.utc
        ).isoformat(),
    })


@mcp.tool()
def run_powershell(command: str, timeout_seconds: int = 60) -> str:
    """Run a PowerShell command from the workspace.

    IMPORTANT: This executes with the Windows user's permissions.
    It is not restricted to the workspace filesystem boundary.
    """
    if not command.strip() or len(command) > 10000:
        raise ValueError("Command must contain 1–10000 characters.")

    timeout_seconds = max(1, min(int(timeout_seconds), 300))

    env = os.environ.copy()

    for name in ("CONTROL_PLANE_API_KEY", "OPENAI_API_KEY", "OPENAI_ADMIN_KEY"):
        env.pop(name, None)

    args = [
        "powershell.exe",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        command,
    ]

    try:
        result = subprocess.run(
            args,
            cwd=str(WORKSPACE),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        return output({
            "exit_code": result.returncode,
            "stdout": result.stdout[-30000:],
            "stderr": result.stderr[-30000:],
            "timed_out": False,
            "working_directory": str(WORKSPACE),
        })

    except subprocess.TimeoutExpired as exc:
        def decode(value):
            if isinstance(value, bytes):
                return value.decode("utf-8", errors="replace")
            return value or ""

        return output({
            "exit_code": None,
            "stdout": decode(exc.stdout)[-30000:],
            "stderr": decode(exc.stderr)[-30000:],
            "timed_out": True,
            "timeout_seconds": timeout_seconds,
        })


if __name__ == "__main__":
    mcp.run()
