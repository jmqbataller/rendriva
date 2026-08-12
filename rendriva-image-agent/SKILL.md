---
name: rendriva-image-agent
description: Generate or edit one to ten high-quality images as separate outputs, never a collage unless explicitly requested. Use for professional product photography, apparel flat-lays, fashion images, same-face model variants, social ads, posters, logos, transparent DTF artwork, website hero graphics, mockups, reference-preserving edits, background changes, campaign batches, draft selection, and any request for multiple individually downloadable images. Apply professional art direction, reference-derived brand palettes, Single Model Face Lock, strict product-region truth locks, draft-to-final promotion, cross-image Campaign Vision Lock, per-image quality judging, and targeted repair so outputs look intentionally designed rather than generically AI-generated.
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

For the executable adapter contract and commands, read [references/adapter-usage.md](references/adapter-usage.md). For supported job fields, read [references/job-spec.md](references/job-spec.md). For reference roles, identity packs, campaign controls, exports, reports, typography, and marketplace modes, read [references/advanced-features.md](references/advanced-features.md).

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

## Derive the brand palette

- When the user supplies any reference image, automatically extract its dominant usable colors and treat them as the default brand palette unless the user supplies an explicit palette.
- When several references exist, combine their distinct dominant colors. Prioritize an identified shop-logo reference, then the first authoritative reference.
- Apply the derived palette to backgrounds, accents, typography, graphic shapes, and overall design-system choices with readable contrast and restrained neutral support.
- Never recolor or tint a protected product, garment, artwork, or logo to match the derived palette. Reference fidelity remains authoritative over palette harmony.
- Keep palette selection deterministic for file-backed jobs and record colors plus source fingerprints in the manifest.
- Save the normalized identity as `brand-profile.json` so it can be reused in later shop campaigns. A job-level brand override always wins over the stored profile.

## Interpret references by role

- Classify every reference as product, model, logo, identity, palette, style, layout, lighting, background, typography, or general. Prefer explicit user roles; otherwise use deterministic filename inference and record that the role was inferred.
- Let each reference control only its assigned dimension. Never let a mood board or lighting reference alter a protected product or logo.
- Combine front, back, side, and detail references with the same identity ID into one multi-view product identity pack.
- When a required view is unavailable, do not invent unseen construction. Request the matching view or fail deterministic validation.

## Keep one model face across variants

- Enable Single Model Face Lock automatically for multi-image `fashion-model` requests and whenever the user asks for one model, one face, the same face, a consistent model, or a model for each variant.
- If the user supplies one model/face reference and asks to match the uploaded face, treat that upload as the only authoritative face anchor even when its filename is generic. Do not derive brand colors from a model reference unless explicitly requested.
- Without a supplied model reference, generate and approve the first model image before continuing. Reuse that approved image as the face anchor for every later variant.
- For native image tools, generate sequentially and attach the same face anchor to every later call. Do not generate variants independently when that would allow different identities.
- Preserve the same recognizable individual: facial proportions, bone structure, eyes, eyebrows, nose, lips, jawline, ears, skin tone, distinguishing features, and natural age appearance.
- Allow requested changes to pose, expression, outfit, product variant, camera, lighting, and background only when the person's face identity remains unchanged.
- Reject face substitution, identity blending, twins/lookalikes, materially changed ethnicity or apparent age, multiple competing faces, or an obscured anchor face.
- Compare every later output with the anchor. Repair only the output whose identity drifts; never regenerate the passing variants.
- If the available tool cannot reuse an anchor or perform face-comparison QA, report the consistency lock as unverified rather than claiming that all faces match.

## Preserve references

- Reuse every required reference in each dependent generation or edit.
- Default to strict fidelity whenever the user supplies a product, garment, artwork, label, or logo image. Treat the supplied asset as authoritative, not as loose inspiration.
- Lock identity, face, product silhouette, proportions, construction, color, fabric weave, fibers, texture, finish, stitching, seams, folds, print, logo, readable label, count, placement, and aspect ratio when the user requires them.
- Never redraw, reinterpret, retouch, recolor, reshape, restyle, smooth, sharpen, retexture, replace, or invent any protected product or logo detail. Never approximate a supplied logo with generated text or a visually similar mark.
- For background, ad-layout, poster, or mockup changes, modify only the environment and unprotected regions. Keep the protected asset out of the generative edit whenever possible.
- Put the most important reference first when the provider gives the first reference extra fidelity.
- Prefer editing over fresh generation for localized changes.
- Use `locked_layers` for products and always for logos when literal preservation is required and a transparent source is available. Generate only the background, then composite the unchanged source-derived layer before typography. Record the source fingerprint in the manifest.
- If the source is not suitable for literal compositing, use strict reference editing plus comparison QA. Do not mark the output `PASS` when texture, fabric, construction, print, label, or logo fidelity cannot be verified.
- If a requested new angle, pose, fold, or view necessarily exposes unseen product detail, explain that literal preservation is impossible; ask for a matching source view or label the result as generative rather than exact.
- Never promise pixel-identical reproduction from generative editing. Claim literal source preservation only for source-derived compositing.

## Handle text professionally

- For a short label that is intentionally part of a scene, allow the image model to render it and judge spelling and legibility.
- For posters, ads, banners, price cards, or other exact-copy work, generate a text-free visual with intentional text-safe space, then apply the exact copy programmatically.
- Use the adapter's `text_layers` feature for file-backed jobs. Do not rely on generated pseudo-text for critical copy.
- Reject critical text that is misspelled, distorted, cropped, or unreadable.
- Use automatic wrapping, fit-to-zone sizing, semantic type roles, and sampled black/white contrast when exact text layers request `auto` behavior.
- In marketplace conversion mode, use only the supplied price, discount, bundle contents, claims, badge, and CTA. Never manufacture conversion claims.

## Balance diversity and campaign consistency

- Give every output a distinct composition, camera, background, lighting, and negative-space direction while preserving the same product and brand identity.
- Apply stable campaign palette, typography, logo-safe-zone, grid, and spacing tokens to all outputs in a campaign.
- Compare passing batch outputs for near-duplicates. Repair or fail an item that exceeds the configured similarity threshold.
- Keep campaign consistency and batch diversity as separate checks: consistent does not mean duplicated.
- When Campaign Vision Lock is enabled, compare all passing outputs together for palette, typography, logo treatment, product-scale rhythm, lighting, grid, spacing, and meaningful diversity.
- Repair only indices identified as campaign outliers. Never regenerate a passing campaign member merely because another image drifted.
- Claim cross-image campaign verification only when the batch vision judge actually ran and passed.

## Enforce product truth regions

- Build automatic whole-asset silhouette, material, and logo truth contracts from protected sources.
- Use explicit normalized bounds and optional same-size masks when the user identifies a print, label, stitching, fabric, texture, color, construction, logo, material, silhouette, or identity region.
- Return one named `region_fidelity` QA result for every configured region. Any missing or failed required region blocks `PASS`.
- Keep literal-source-composite evidence distinct from masked or bounded vision comparison; never describe vision comparison as pixel identity.

## Promote drafts to final quality

- When `draft_to_final` is enabled, generate low-quality composition candidates before final rendering.
- Auto-selection must choose the highest-scoring candidates that also satisfy batch diversity; manual selection must contain one unique candidate per requested final.
- Promote only selected drafts, preserving their composition, camera, hierarchy, and negative-space plan while final reference and truth-region locks remain authoritative.
- Do not place exact text onto promotion-source drafts. Apply exact typography only after final rendering.
- Record every candidate, score, selection, and final mapping in `draft-selection-report.json`.

## Prepare production exports

- For literal product compositing, optionally derive a foreground alpha mask from a simple flat background and add a controlled natural shadow without modifying source RGB detail.
- Create platform exports as separate contain-scaled files; never crop the protected product merely to fill a new aspect ratio.
- Record source roles, views, fingerprints, identity packs, comparison results, cutout/shadow evidence, and verification limits in `reference-fidelity-report.json`.

## Generate, judge, and repair

For every output:

1. Generate or edit the image.
2. Inspect instruction following, reference preservation, composition, hierarchy, typography, realism, artifacts, commercial usability, and collage violations.
3. Under strict fidelity, fail the non-negotiable gate for any product material, fabric, construction, print, label, color, proportion, or logo drift—even if the result is attractive.
4. Assign `PASS`, `NEEDS_REPAIR`, `FAILED`, or `BLOCKED`.
5. Repair only the failed item, with a defect-specific repair prompt.
6. Perform at most one automatic repair attempt unless the user explicitly authorizes more.
7. Keep the better result and record the decision.

Require all non-negotiable gates to pass and an average professional-design score of at least `4/5`. Do not label an output `PASS` only because it is visually attractive.

## Deliver results

- Present each image separately with its index and status.
- For native generated images, let the host display and retain them; do not reconstruct them merely to make a ZIP.
- For adapter jobs, provide individual files plus `manifest.json`, `quality-report.json`, `reference-fidelity-report.json`, `model-identity-report.json`, `diversity-report.json`, `campaign-report.json`, `brand-profile.json`, `draft-selection-report.json`, optional drafts/platform exports, and `rendriva-output.zip`.
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
