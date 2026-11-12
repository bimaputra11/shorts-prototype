from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _slug(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    value = re.sub(r"-{2,}", "-", value).strip("-._")
    return (value[:100] or "inspiration-video").lower()


def record_stage(
    workspace: str | Path,
    source_path: str | Path,
    stage: str,
    details: dict[str, Any] | None = None,
    status: str = "complete",
) -> Path:
    """Persist a compact resumable record for one inspiration video."""
    workspace_path = Path(workspace).resolve()
    source = Path(source_path)
    if not source.is_absolute():
        source = (workspace_path / source).resolve()

    state_dir = workspace_path / "working" / "_pipeline_state"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / f"{_slug(source.stem)}.json"

    try:
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        state = {}

    state.setdefault("schema_version", 1)
    state["source"] = {
        "filename": source.name,
        "workspace_path": str(source.relative_to(workspace_path)) if workspace_path in source.parents else str(source),
    }
    state.setdefault("stages", {})
    state["stages"][stage] = {
        "status": status,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "details": details or {},
    }
    state["latest_stage"] = stage
    state["updated_at_utc"] = datetime.now(timezone.utc).isoformat()

    temporary = state_path.with_name(state_path.name + ".partial")
    try:
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(state_path)
    finally:
        temporary.unlink(missing_ok=True)
    return state_path
