# Agent Instructions

This is the operating guide for AI agents and contributors.

## Start here
1. Read README.md for the project map.
2. Read the relevant guide in docs/: architecture.md, workflow.md, rendering.md, project-rules.md, development.md, or operations.md.
3. Inspect live code, profiles and project files before relying on documentation.
4. Check the current project checkpoint before resuming an existing video.

## Non-negotiable rules
- Preserve the inspiration video's visual sequence, story, order and substantial information. Rephrase captions for engagement; do not invent a new narrative.
- Match inspiration visuals efficiently using good-quality, close visual matches. Do not render screenshots or extracted reference frames as artwork. Avoid lengthy sourcing paperwork for routine adaptations.
- The user must choose the opening hook before rendering. Never use an assumed or fallback hook.
- Preserve authored caption line breaks. Center lines on a common axis; prefer balanced two-line blocks and use three only when needed. Avoid isolated words, dangling short lines and unexpected wrapping.
- Main captions: 70 px with 6 px black stroke. Hook: 71 px with 860 px text width. Keep face-clearance checks and visual review.
- No period characters in hook or caption text. Run validation before export.
- Reaction filenames use descriptive lowercase kebab-case. Renaming must not alter clip bytes.
- Keep the configured output filename; do not create version-suffixed duplicates.
- Do not delete assets, source media, caches, outputs or dependencies unless explicitly asked. For cleanup, inspect scope/references and verify afterward.
- Never expose credentials. The runtime key is outside the repository under local app data.

## Change discipline
Inspect before editing. Make focused changes. Run relevant tests, compilation and smoke checks. Update docs when behavior changes. Never claim a test, render, visual inspection or deletion unless tool output confirms it. Distinguish completed work from assumptions and pending work.
