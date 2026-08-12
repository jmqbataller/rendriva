# Professional design rubric

## Contents

1. Prompt direction
2. Non-negotiable gates
3. Scored dimensions
4. AI-looking warning signs
5. Repair policy

## Prompt direction

Demand intentional art direction instead of vague quality adjectives. Specify purpose, audience, focal point, hierarchy, grid, spacing, negative space, palette, material behavior, lighting logic, camera perspective, brand constraints, exact copy handling, and output use.

Avoid imitating a living designer or copying protected work. Translate references into high-level visual properties.

## Non-negotiable gates

Fail an output when any applicable condition is true:

- It is a collage, grid, contact sheet, storyboard, or multi-panel output when separate images were requested.
- It misses a required subject, scene, product, logo, exact copy, or reference lock.
- A supplied product changes silhouette, proportions, construction, material, fabric weave, fibers, texture, finish, stitching, seams, folds, print, color, label, or product-specific detail.
- A supplied logo changes symbol geometry, lettering, spelling, spacing, color, aspect ratio, placement, or edge shape; is cropped or distorted; or is replaced with a generated approximation.
- Critical text is misspelled, unreadable, cropped, or distorted.
- The main product or subject is unintentionally cropped.
- A face, hand, logo, product, pattern, or material has a severe artifact.
- The requested dimensions, transparency, or file format are wrong.
- It contains a prohibited object or invented brand/product detail.
- It ignores an automatically extracted reference palette and introduces unrelated dominant colors without a purposeful contrast or accessibility reason.

## Scored dimensions

Score each dimension from `0` to `5`:

| Dimension | A score of 4 or 5 means |
|---|---|
| Visual hierarchy | Focal point and reading order are immediately clear |
| Composition and spacing | Grid, alignment, balance, and negative space feel intentional |
| Brand consistency | Palette, tone, typography intent, and references agree |
| Realism and artifact control | Lighting, perspective, anatomy, texture, and shadows are credible |
| Commercial usability | The asset is suitable for its intended ad, listing, print, or web placement |
| Originality and restraint | The design avoids generic template and default AI aesthetics |

Require every applicable gate to pass and an average score of at least `4.0` by default.

## AI-looking warning signs

- Generic neon blue-purple gradients unrelated to the brand
- Random glow, sparks, floating particles, or decorative objects
- Excessive glass panels, fake HUD graphics, or template-like tech styling
- Plastic skin, waxy materials, oversharpening, or artificial HDR
- Impossible hands, reflections, shadows, perspective, or object joins
- Meaningless glyphs, pseudo-text, duplicate logos, or invented labels
- Cluttered composition without hierarchy
- Perfectly centered sterile layouts used without purpose
- Excessive cinematic lighting that weakens product accuracy
- Repeated micro-details and incoherent background objects

Treat these as evidence to investigate, not an automatic aesthetic ban. A requested visual style may intentionally include some of them.

## Repair policy

Write a repair prompt that names the observed defect, restates the locked details, and describes the corrected professional outcome. Do not broadly redesign an image when a localized edit can fix it. Retry only the affected output and keep at most the best passing result.

Do not use generative repair inside a strict source-derived product or logo layer. Repair the background or layout, then composite the original protected source again. If strict generative reference fidelity cannot be compared with the source, do not pass the output.
