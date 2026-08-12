---
name: rendriva-image-agent
description: Generate or edit one to ten high-quality images as separate outputs, never a collage unless explicitly requested. Use for professional product photography, apparel flat-lays, fashion images, social ads, posters, logos, transparent DTF artwork, website hero graphics, mockups, reference-preserving edits, background changes, and any request for multiple individually downloadable images. Apply professional art direction, reference locks, per-image quality judging, and targeted repair so outputs look intentionally designed rather than generically AI-generated.
---

# Rendriva Image Agent

Produce commercially usable images through a strict plan, generate, inspect, and repair workflow. Treat every requested image as an independent deliverable.

## Enforce the output contract

- Accept `1` through `10` outputs per run. Clamp nothing silently; ask the user to split requests above ten.
- Return exactly one separate file or native image result per planned output.
- Never place multiple requested outputs inside one canvas, grid, sheet, contact sheet, storyboard, diptych, or collage unless the user explicitly requests that composition.
- Name file-backed outputs `image-01.<format>` through `image-10.<format>`.
- Preserve completed outputs when another output fails. Retry only the failed output.
- Treat `partial success` as valid and report completed, repaired, failed, and blocked items separately.

## Choose an execution path

1. Prefer the host's native image-generation or image-editing tool when it is available and the user wants images in conversation.
2. For a batch, invoke the native image tool once per planned output or use a native multi-output parameter only when it returns separate results. Never ask the model to draw ten images on one canvas.
3. Use `scripts/rendriva.py` when an executable workspace and `OPENAI_API_KEY` are available and the user needs deterministic filenames, resumable jobs, manifests, quality reports, exact text overlays, or a ZIP archive.
4. If no image tool and no configured API are available, produce the validated job plan and mark generation `BLOCKED`; never claim images were generated.

For the executable adapter contract and commands, read [references/adapter-usage.md](references/adapter-usage.md). For supported job fields, read [references/job-spec.md](references/job-spec.md).

## Plan every run

Resolve these values from the request without asking when they are clear:

- `mode`: `variations` for one concept with independent alternatives, or `scenes` for a distinct brief per image.
- `count`: explicit count, otherwise one.
- `operation`: `generate`, `edit`, or `variation`.
- `preset`: choose the closest production preset.
- `size`, `quality`, `format`, and background behavior.
- reference images and non-negotiable preservation locks.
- brand kit, exact copy, and prohibited elements.

Ask at most one compact question only when a missing choice would materially change the deliverable. Otherwise use professional defaults: high quality, PNG, opaque background, professional-designer mode enabled, and no collage.

## Build a professional creative brief

Translate the user's instruction into a per-image brief containing:

1. Purpose and audience
2. Subject and required content
3. Visual hierarchy and focal point
4. Grid, composition, alignment, spacing, and negative space
5. Camera, lighting, material, and realism direction when photographic
6. Controlled palette, typography intent, and brand direction
7. Reference locks and exact details to preserve
8. Disallowed elements and common AI-looking failure modes
9. Output specifications

Keep user facts and constraints authoritative. Do not invent logos, copy, product features, achievements, accessories, or brand details.

Use [references/professional-design-rubric.md](references/professional-design-rubric.md) when compiling prompts or judging outputs.

## Preserve references

- Reuse every required reference in each dependent generation or edit.
- Lock identity, face, product silhouette, proportions, color, fabric, texture, print, logo, readable label, count, placement, and background when the user requires them.
- Put the most important reference first when the provider gives the first reference extra fidelity.
- Prefer editing over fresh generation for localized changes.
- Use `locked_layers` instead of generative reference editing when the source artwork or product must remain source-derived and can be supplied as a transparent image. Generate only the background, then composite the locked layer before typography.
- State that high-fidelity generation reduces drift but cannot guarantee pixel-identical reproduction.

## Handle text professionally

- For a short label that is intentionally part of a scene, allow the image model to render it and judge spelling and legibility.
- For posters, ads, banners, price cards, or other exact-copy work, generate a text-free visual with intentional text-safe space, then apply the exact copy programmatically.
- Use the adapter's `text_layers` feature for file-backed jobs. Do not rely on generated pseudo-text for critical copy.
- Reject critical text that is misspelled, distorted, cropped, or unreadable.

## Generate, judge, and repair

For every output:

1. Generate or edit the image.
2. Inspect instruction following, reference preservation, composition, hierarchy, typography, realism, artifacts, commercial usability, and collage violations.
3. Assign `PASS`, `NEEDS_REPAIR`, `FAILED`, or `BLOCKED`.
4. Repair only the failed item, with a defect-specific repair prompt.
5. Perform at most one automatic repair attempt unless the user explicitly authorizes more.
6. Keep the better result and record the decision.

Require all non-negotiable gates to pass and an average professional-design score of at least `4/5`. Do not label an output `PASS` only because it is visually attractive.

## Deliver results

- Present each image separately with its index and status.
- For native generated images, let the host display and retain them; do not reconstruct them merely to make a ZIP.
- For adapter jobs, provide individual files plus `manifest.json`, `quality-report.json`, and `rendriva-output.zip`.
- Report exact failures and blocked capabilities. Never call a run complete beyond the available evidence.

## Production presets

Use the built-in adapter presets or mirror their intent when working through native tools:

- `product-photography`
- `apparel-flatlay`
- `fashion-model`
- `social-ad`
- `logo-icon`
- `transparent-dtf`
- `poster-flyer`
- `website-hero`
- `realistic-mockup`
- `general-creative`

## Safety and cost controls

- Confirm or warn before starting ten high-quality images when the execution surface can incur API charges and no prior user confirmation covers that cost.
- Avoid duplicate submissions by using a stable job ID and resume an incomplete job rather than restarting it.
- Keep API keys server-side or in environment variables. Never place secrets in prompts, manifests, files, commits, or client code.
- Respect provider content-policy decisions and mark rejected items `BLOCKED`.
