import argparse
import json
import shutil
import sys
from pathlib import Path

workspace = Path(__file__).resolve().parent
project_root = workspace.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(workspace))
from video_pipeline import analyze_video

parser = argparse.ArgumentParser(description="Verify that analysis results are reused from cache.")
parser.add_argument("source", type=Path, help="Path to a local source video")
args = parser.parse_args()
source = args.source.resolve()
if not source.is_file():
    parser.error(f"Source video does not exist: {source}")
out = workspace / "working" / "_analysis-smoke-test"
try:
    first = analyze_video(source, out)
    second = analyze_video(source, out)
    assert first["analysis_cache_hit"] is False, first
    assert second["analysis_cache_hit"] is True, second
    assert first["scene_count"] == second["scene_count"]
    assert first["caption_segment_count"] == second["caption_segment_count"]
    print(json.dumps({
        "first_cache_hit": first["analysis_cache_hit"],
        "second_cache_hit": second["analysis_cache_hit"],
        "scenes": first["scene_count"],
        "frames": first["scene_frames_extracted"],
        "caption_segments": first["caption_segment_count"],
        "timings": first["analysis_timings_seconds"],
        "sampling_mode": "single sequential decode for scene stills and OCR",
    }))
finally:
    shutil.rmtree(out, ignore_errors=True)
