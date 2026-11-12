# Editorial and Visual Rules

## Narrative fidelity
- Preserve the inspiration's original story, sequence and substantial information.
- Rephrase captions to be more engaging, natural and concise.
- Do not invent a different narrative, silently alter facts or omit important beats.
- If a material factual problem is noticed, flag it separately rather than silently rewriting the story.
- Captions are the primary narration; voiceover is not required.

## Visual fidelity and asset matching
- Follow the inspiration's visual sequence and composition as closely as practical.
- Detect the inspiration images, then search for close visual matches with good quality and sufficient resolution.
- Do not render screenshots or extracted frames from the reference video as artwork.
- Do not cover, blur, retouch or otherwise manipulate baked-in captions from reference frames.
- Keep routine image sourcing efficient; do not create lengthy provenance paperwork.

## Caption composition
- Every line shares a common horizontal center axis.
- Prefer balanced two-line blocks; use three lines only when necessary.
- Avoid isolated words, dangling short lines and accidental wrapping.
- Preserve authored line breaks through rendering.
- Check actual rendered frames at phone-screen scale.

## Readability and safe placement
- Main captions use 70 px type with a 6 px black stroke.
- The hook uses 71 px type and an 860 px text width.
- Keep text within the canvas safe area and clear of detected faces.
- Visually inspect scenes where face detection reports no faces or appears uncertain.
- No periods in any hook or caption line.

## Reaction clips
- Use a reaction clip at the opening and select one that fits the hook's emotional tone.
- Reaction filenames describe the visible expression/gesture in lowercase kebab-case.
- Renaming must not change the underlying video bytes.

## Approval and delivery
- Always show hook options and wait for explicit user choice before rendering.
- Deliver the finished video, not internal paperwork.
- Use one configured output filename and replace it atomically after successful validation.
