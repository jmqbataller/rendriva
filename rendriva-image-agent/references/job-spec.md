# Rendriva job specification

## Contents

1. Minimal variation job
2. Scene-list job
3. Reference and brand locks
4. Automatic reference palette
5. Strict product and logo fidelity
6. Literal source compositing
7. Exact text layers
8. Supported fields
9. Advanced production controls

## Minimal variation job

```json
{
  "prompt": "Premium studio product photograph of a ceramic coffee mug",
  "count": 4,
  "mode": "variations",
  "preset": "product-photography",
  "quality": "high",
  "format": "png",
  "size": "1024x1024"
}
```

## Scene-list job

```json
{
  "prompt": "Create a professional campaign for the same running shoe",
  "mode": "scenes",
  "scenes": [
    "Three-quarter studio hero view",
    "Side profile on a neutral seamless background",
    "Macro close-up of the fabric and stitching"
  ],
  "preset": "product-photography",
  "professional_designer_mode": true
}
```

Reference jobs default to `"fidelity_mode": "strict"`. Use `"guided"` only when the source is inspiration and controlled variation is allowed.

## Strict product and logo fidelity

```json
{
  "prompt": "Place this exact shirt in a premium marketplace banner",
  "operation": "edit",
  "reference_images": ["./shirt-front.png"],
  "fidelity_mode": "strict",
  "preserve": [
    "exact shirt length and construction",
    "exact fabric weave, texture, stitching, folds, print, and color"
  ]
}
```

Strict mode prohibits redraw, recolor, reshape, retexture, logo approximation, and invented product detail. The comparison judge must fail visible drift. A generative reference edit is high fidelity, not literal preservation. Use source-derived `locked_layers` whenever the original pixels must remain protected, and always use them for an exact supplied logo when a transparent source is available.

## Automatic reference palette

Every supplied reference image becomes a palette source by default. If several references exist, Rendriva combines distinct dominant colors and prioritizes a locked logo layer. Explicit `brand.palette` values override automatic extraction.

```json
{
  "prompt": "Create a premium shop banner using the visual identity of these references",
  "operation": "edit",
  "reference_images": ["./shop-reference.png", "./product-reference.png"],
  "brand": {
    "auto_palette_from_references": true,
    "palette_max_colors": 5
  }
}
```

Use `brand.palette_source_images` to select specific palette sources. The adapter records the extracted colors, resolved source paths, and SHA-256 fingerprints in `manifest.json`. Palette colors control unprotected backgrounds, accents, typography, and graphic elements; they never authorize recoloring a protected source asset.

When `scenes` is present, `count` defaults to its length and must match it when explicitly provided.

## Reference and brand locks

```json
{
  "prompt": "Place this exact shirt in a premium flat-lay campaign",
  "count": 3,
  "mode": "variations",
  "operation": "edit",
  "reference_images": ["./shirt-front.png"],
  "preserve": [
    "exact color",
    "fabric texture",
    "print design and placement",
    "shirt length and proportions"
  ],
  "brand": {
    "palette": ["#F5EFE5", "#171717", "#E97824"],
    "tone": "premium, restrained, commercial",
    "avoid": ["generic neon glow", "excessive glassmorphism"]
  }
}
```

## Literal source compositing

Use `locked_layers` when generative edits are not accurate enough and the product/artwork is available as a transparent image. Rendriva generates the background without drawing the product, then composites the source-derived layer before exact typography and QA.

```json
{
  "prompt": "Premium social-sale background with a clean product zone on the right",
  "operation": "generate",
  "locked_layers": [
    {
      "path": "./shirt-transparent.png",
      "role": "product",
      "x": 0.76,
      "y": 0.55,
      "max_width": 0.42,
      "max_height": 0.78,
      "anchor": "center",
      "require_alpha": true
    },
    {
      "path": "./brand-logo.png",
      "role": "logo",
      "x": 0.08,
      "y": 0.08,
      "max_width": 0.22,
      "max_height": 0.16,
      "anchor": "top-left",
      "require_alpha": true
    }
  ]
}
```

Do not combine `locked_layers` and `reference_images` in the current adapter. A locked layer must use `operation: "generate"`. Scaling is proportional and source-derived, but resampling means it is not a claim of byte-for-byte pixel identity.

## Exact text layers

```json
{
  "prompt": "Minimal product-sale background with generous clean space at top",
  "preset": "social-ad",
  "text_safe_mode": true,
  "text_layers": [
    {
      "text": "PAYDAY SALE",
      "x": 0.08,
      "y": 0.08,
      "max_width": 0.84,
      "font_size": 72,
      "color": "#111111",
      "align": "left"
    }
  ]
}
```

`x`, `y`, and `max_width` accept normalized values from zero to one. Set `font_path` when an exact brand font is required. Without it, the adapter uses an available default font and reports the fallback in the manifest.

## Advanced production controls

Rendriva 1.3 supports automatic reference roles, multi-view identity packs, batch diversity, campaign locks, cutout/shadow compositing, reusable brand profiles, platform export packs, fidelity reporting, responsive exact typography, and marketplace conversion goals. See [advanced-features.md](advanced-features.md) for complete examples and constraints.

## Supported fields

| Field | Values / behavior |
|---|---|
| `prompt` | Required non-empty string |
| `count` | Integer `1..10`; defaults to `1` or scene count |
| `mode` | `variations` or `scenes` |
| `scenes` | Distinct scene instructions, maximum ten |
| `operation` | `generate`, `edit`, or `variation` |
| `preset` | One of the ten built-in production presets |
| `size` | Provider-supported `WIDTHxHEIGHT` or `auto` |
| `quality` | `low`, `medium`, `high`, or `auto` |
| `format` | `png`, `jpeg`, or `webp` |
| `background` | `opaque`, `transparent`, or `auto` |
| `reference_images` | Local image paths used for edit/reference jobs |
| `reference_assets` | Role-aware local sources with `path`, `role`, `view`, `identity_id`, and palette controls |
| `product_identity.required_views` | Views that must exist in the identity pack before generation |
| `locked_layers` | Transparent source images composited after background generation |
| `locked_layers[].role` | `product`, `logo`, `artwork`, `identity`, or `protected-asset` |
| `locked_layers[].auto_cutout` | Derive alpha from a simple corner-matched background when the source has no transparency |
| `locked_layers[].shadow` | Natural shadow controls: opacity, blur, offsets, and color |
| `fidelity_mode` | `strict`, `guided`, or `none`; defaults to `strict` when a source asset exists |
| `preserve` | Non-negotiable details to retain |
| `avoid` | Additional forbidden visual elements |
| `brand` | Palette, tone, fonts, references, and avoid list |
| `brand_profile` | Reusable brand JSON object or local JSON file; job `brand` values override it |
| `brand.auto_palette_from_references` | Defaults to `true` when any reference or locked layer exists |
| `brand.palette_source_images` | Optional reference-image paths used specifically for automatic palette extraction |
| `brand.palette_max_colors` | Extract `1..8` colors; defaults to `5` |
| `professional_designer_mode` | Defaults to `true` |
| `text_safe_mode` | Generate without critical text and reserve layout space |
| `text_layers` | Exact copy applied after generation |
| `campaign` | Cross-output campaign ID, consistency level, grid, logo position, and shared tokens |
| `diversity` | Near-duplicate threshold and composition/camera/background variation axes |
| `platform_exports` | Separate contain-scaled Shopee, Instagram, Facebook, website, or TikTok PNG exports |
| `marketplace` | Conversion goal plus exact supplied price, discount, CTA, bundle count, and claims |
| `max_repair_attempts` | Defaults to `1`; capped at `3` |
| `min_professional_score` | Defaults to `4.0` out of `5` |
| `judge_model` | Defaults to `RENDRIVA_JUDGE_MODEL` or `gpt-5.5` |
| `concurrency` | Independent scene jobs in flight; defaults to `2`, maximum `4` |
