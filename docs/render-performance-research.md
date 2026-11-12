# Render performance research and benchmarks — 2026-11-10

## Summary

The renderer has three performance improvements: reaction footage is decoded sequentially and reuses one pre-rendered caption overlay; keyframed artwork uses linear rather than cubic interpolation; and unchanged timeline chunks are reused from a content-addressed cache. The final output is assembled from independently encoded chunks with FFmpeg stream copy.

The cache reduces repeat-render work after hook, caption, keyframe, or asset changes. It preserves the existing 1080×1920, 30 fps, H.264 output settings and validates the assembled file by decoding the complete stream.

## Segment cache design

The renderer stores reusable segments under `workspace/cache/render_segments/<project-slug>/`. The reaction opening is one 105-frame (3.5-second) chunk. Each profile visual segment is divided into chunks of up to 60 frames (2 seconds); chunks never cross a profile segment boundary.

Each chunk has a SHA-256 fingerprint and a matching JSON sidecar. The fingerprint includes the relevant source-file hashes, hook text or caption content/timing/position, the camera keyframes that affect that chunk, font hash, renderer and shared-renderer source hashes, library versions, FFmpeg build identity, canvas/FPS, and encoder settings. A hook edit therefore invalidates the reaction chunk, while a timed caption or camera-keyframe edit invalidates only the visual chunks that depend on it.

Cache chunks are encoded as H.264 MP4 with matching dimensions, frame rate, pixel format, encoder settings, and video time base. FFmpeg's concat demuxer joins them with `-c:v copy`; unchanged chunks are not encoded again. Each chunk is checked before being stored, cache hits are checked against their sidecar checksum and video metadata, and the final assembled file is fully decoded before it is accepted.

Completed cache entries are kept under a 2 GB target per project slug. When the total exceeds the target, least-recently-used entries are evicted; chunks used by the current render are protected, so a project whose active chunks alone exceed 2 GB can temporarily exceed the target. Abandoned partial files and assembly manifests older than 24 hours are removed on the next render. Rendering remains single-worker. Profile preparation, cache validation, and final assembly still run on each render, so a cache hit does not make the whole operation instantaneous.

### Segment-cache benchmark

A full Sargent profile test produced 493 frames (16.433 seconds) and eight chunks. On the project laptop, using the temporary validation hook:

| Scenario | Wall time | Chunk work |
|---|---:|---|
| First uncached render | 29.01 s | 8 rendered, 0 reused |
| Unchanged rerender | 6.10 s | 0 rendered, 8 reused |

The unchanged rerender was 4.76× faster, or about 79% less wall time. Both outputs passed metadata checks and a full FFmpeg decode.

Selective invalidation was also tested. Changing only the hook reused all seven visual chunks and regenerated only the reaction chunk. In a separate four-chunk test containing both still and keyframed artwork, changing one timed caption regenerated one chunk; changing the hook regenerated only the reaction chunk. All test outputs had the expected frame count and passed full-stream decoding.

These are local measurements, not guaranteed speedups for every profile. Shorter or still-heavy videos may have less work to save, and profile preparation plus output validation remain necessary.

## Local machine and constraints

- CPU: Intel Core i5-2520M, 2 physical cores / 4 logical processors.
- GPU: Intel HD Graphics 3000.
- Encoder tested: `h264_qsv` is listed by this FFmpeg build but failed to initialize a Media SDK session (`MFX session: -9`).
- No NVIDIA GPU / NVENC device is present. Current reliable encoder is `libx264`, preset `veryfast`, CRF 18.
- Consequently, the near-term work should target fewer Python pixel operations, reduced repeat work, and bounded CPU concurrency; do not assume hardware encoding exists.

## Measured improvements

All wall times were measured locally on the machine above. They are environment-specific, not universal speed guarantees.

| Test | Before | After | Result |
|---|---:|---:|---:|
| Reaction segment generation (105 frames, 3.5 s) | 44.4 s | 3.33 s | 13.4× faster |
| Image warp, 120 frames | Cubic: 46.02 ms/frame | Linear: 21.85 ms/frame | 2.1× faster |
| Full Sargent profile render (493 frames, 16.433 s) | 76.17 s | 37.55 s | 2.03× faster; about 50.7% less wall time |

The full Sargent run used the same 1080×1920 / 30 fps / 493-frame output settings. The resulting MP4 passed structural validation and OCR checks. A 120-frame cubic-vs-linear comparison found approximately 0.9–1.0 mean pixel levels of difference out of 255, with a 95th percentile absolute difference of 3 levels in the sampled frames.

The reaction optimization is specific but generalizable: the existing reaction clip is already 1080×1920, yet the old path built a blurred PIL background and relaid out the same caption on every frame. The new path detects native-size frames, skips image composition, decodes sequentially rather than seeking for every frame, and alpha-blends one precomputed hook overlay. The first-frame comparison against the former method had a mean absolute difference of approximately 0.001 levels.

Other test renders using the optimized engine:

- Mental Pain Sculptures: 399 frames, 13.300 s; one run completed in 14.84 s.
- Maria Luisa: 823 frames, 27.433 s; one run completed in 30.71 s.

These are individual test timings and will vary with CPU load and caching.

## What did not improve the current workload

I tested a native FFmpeg filtergraph that loops five pre-rendered still images and concatenates them. For a 294-frame / 9.8-second still-image portion, the existing Python raw-frame pipe encoded in 4.72 s; the looped-image FFmpeg graph took 8.50 s. Both produced 294 frames at 1080×1920, and seven sampled decoded frames were identical. Therefore, replacing the existing still-image path with this basic looped-input graph would be a regression on this laptop. Keep it for now; reconsider a parallel, cached segment pipeline rather than blindly moving all work into FFmpeg.

## GitHub projects reviewed

### givemotion — https://github.com/givemotion/givemotion

MIT-licensed, agent-oriented video engine. The useful idea is not its browser/Chrome renderer; it is the 1–2-second render chunks, input fingerprints, worker control, and reuse of unchanged chunks. Its README describes only regenerating chunks whose fingerprints change. This closely matches our edit loop: a hook change should invalidate only the reaction segment; a caption change should invalidate only the chunk(s) containing that caption. Borrow the cache model, not the whole runtime.

Source: https://github.com/givemotion/givemotion

### MovieGo — https://github.com/mowshon/moviego

A Go editing toolkit with a parallel render pipeline and an FFmpeg-only fast path when the edit can be expressed as a filtergraph. Useful design principle: send transforms directly to FFmpeg when it can reproduce them; reserve Python frame processing for operations that genuinely need custom pixel access. The project requires Go 1.25+, so porting our renderer to Go would add a new runtime and is not recommended now.

Source: https://github.com/mowshon/moviego

### Editly — https://github.com/mifi/editly

Declarative Node.js + FFmpeg editing with JSON/JSON5 timelines, streaming media, image/video clips, and titles. Our JSON profiles already follow the declarative-edit idea. Editly is useful as a reference for expressing a timeline, but a full migration would add Node dependencies and duplicate the renderer we have just consolidated.

Source: https://github.com/mifi/editly

### FFmpeg Slideshow.py — https://github.com/Twinklebear/ffmpeg-slideshow-py

Pre-renders image segments and processes them asynchronously before combining them in an FFmpeg graph. This is a useful example of isolating scene-sized work and parallel preparation. Its specific looped-image method was slower in our local microbenchmark, so only the segment-isolation/caching idea should be considered.

Source: https://github.com/Twinklebear/ffmpeg-slideshow-py

### ffmpeg-render-pro — https://github.com/beeswaxpat/ffmpeg-render-pro

Splits frame ranges across workers, encodes MP4 chunks separately, then joins compatible segments using stream copy. This is a possible model for a future segment cache. With only two physical CPU cores, we should benchmark one versus two workers and avoid oversubscribing the machine.

Source: https://github.com/beeswaxpat/ffmpeg-render-pro

### AI Shorts Generator — https://github.com/aminedrif/ai-shorts-generator

A more complete Shorts pipeline with transcription, face-aware cropping, animated subtitles, and optional NVIDIA CUDA/NVENC. Its broader automation is interesting, but its GPU acceleration is not currently available on this Intel HD 3000 machine and its virality/transcription stack is not a direct replacement for our clean-artwork workflow.

Source: https://github.com/aminedrif/ai-shorts-generator

## Recommended next improvements

1. **Bounded concurrency.** Benchmark one versus two chunk workers. The local CPU has only two physical cores; do not assume parallel encoding will be faster or enable it without a measured gain.
2. **FFmpeg-native motion path.** Test whether the current homography-derived zoom/pan can be represented closely enough with FFmpeg `zoompan` or `perspective`. Compare representative frames against the current clean-artwork output before replacing OpenCV.
3. **Keep quality presets explicit.** Do not change CRF or x264 preset globally just to gain speed without comparing visual quality and file size. Hardware encoders remain disabled until a working device is detected.

## Render telemetry

`build_configured_short` now returns `render_timing_seconds` with:

- `profile_preparation`
- `reaction_generation_and_pipe` — time spent encoding reaction chunks that missed the cache
- `visual_generation_and_pipe` — time spent encoding visual chunks that missed the cache
- `cache_lookup_and_generation` — total cache verification plus all newly encoded chunks
- `ffmpeg_finalize` — final stream-copy assembly
- `total`

The reaction and visual generation timings are components of `cache_lookup_and_generation` and must not be added to it again. On a full cache hit, no reaction or visual chunk is encoded, but cache checksum/metadata checks, final assembly, and full-output decoding still run.


## November 11, 2026 quality and workflow improvements

### Rule validation and original-source records

quality_validation.py now runs before render preparation. It rejects period characters anywhere in hook/caption lines, blank authored caption lines, and asset paths under scene_frames/. New profile schema version 2 requires an HTTPS source page, page title, publisher, explicit verified_original flag, timezone-aware retrieval timestamp, verification note, and SHA-256 of the exact local image. The hash proves the local asset has not drifted from its record; it cannot establish that the historical attribution is correct or that the source page is authoritative. Source-page and visual confirmation remain manual. Schema version 1 is accepted for backward compatibility and reported as legacy_not_recorded.

### Face-aware caption clearance

face_clearance.py uses OpenCV's built-in YuNet API and the pinned face_detection_yunet_2023mar.onnx model. The model was retrieved from https://huggingface.co/opencv/opencv_zoo/resolve/d4938dfc9d4ec5d098bfa33e98b3f3345a236586/models/face_detection_yunet/face_detection_yunet_2023mar.onnx. It is 232,589 bytes and its SHA-256 is 8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4. Static artwork is checked once per caption/asset combination; keyframed artwork is sampled at up to 0.5-second intervals (capped at 16 samples per caption); the reaction opening is sampled at six timestamps. Caption placement decisions are content/model/algorithm-version cached. The algorithm selects the closest candidate y-position that does not intersect an expanded detected-face rectangle. It uses an extended lower safe area only as a fallback when the normal caption zone has no clear position.

This is a guardrail, not a guarantee: YuNet may miss faces in paintings, profiles, unusual lighting, sculptures, or small/occluded subjects. Results report no_faces_detected separately and still require visual review when uncertain. Detection can reposition text; it does not judge whether a caption obscures important non-face artwork details.

### Analysis pass and analysis cache

video_pipeline.py still performs one scene-detection pass, then combines representative-frame extraction and OCR sampling into one sequential decode pass. This removes repeated timestamp seeks during extraction/OCR. A completed analysis can be reused only when the source SHA-256, scene threshold, OCR interval, pipeline-code hash, OpenCV version, and required generated files all match. A local 13.3-second test clip produced five scene stills and eleven OCR caption groups; cold analysis took 46.645 seconds and an unchanged repeat was an analysis-cache hit. A second, 3.5-second trim produced two scene stills and five OCR caption groups in 7.992 seconds, followed by an analysis-cache hit. OCR inference dominated the cold runs, so sampling density was not reduced.

### Lazy asset materialization and output replacement

A cache hit no longer composes a static artwork frame or decodes a keyframed image merely to build cache jobs. Source dimensions, file hashes, face-placement metadata, and profile data are still checked; pixel materialization happens only when a missing render chunk is actually encoded. The configured MP4 filename is now stable. The renderer writes to a partial file, validates the full stream, then atomically replaces the configured output rather than creating -v2, -v3, and later duplicates.

### Internal stage checkpoints

pipeline_state.py stores compact JSON records under workspace/working/_pipeline_state/ for import, analysis, profile preparation, source-record status, hook selection, render, and validation. It is deliberately local and internal; it does not create a user-facing tracking report. The checkpoint is evidence of what the project recorded, not a substitute for verifying that a stage's output still exists.

### Integration benchmark

A temporary 6.5-second validation render (105-frame reaction opening plus a 90-frame portrait scene) generated three independently encoded chunks:

| Scenario | Wall time | Chunks |
|---|---:|---|
| First render | 11.922 s | 3 rendered, 0 reused |
| Unchanged rerender | 3.012 s | 0 rendered, 3 reused |

The unchanged rerender reused all chunks and completed about 4.0× faster (about 74.7% less wall time) in this small integration test. Both outputs passed metadata, boundary-frame, and full FFmpeg decode validation. The temporary profile, MP4, and render cache were removed. All six existing deliverable MP4s were compared by SHA-256 before and after and remained unchanged.

### Additional GitHub references

- Abekyo Editor — https://github.com/abekyo/abekyo-editor : JSON Schema validation before render, progress/cancellation, and a declarative edit plan. We borrowed the validation-first principle, not the editor runtime.
- Shorts Studio — https://github.com/EdryStarz/shorts-studio : resumable processing stages and sampled face-detection ideas. It is treated as an architectural reference, not a dependency.
