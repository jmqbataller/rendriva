# Rendriva job specification

## Contents

1. Minimal variation job
2. Scene-list job
3. Reference and brand locks
4. Literal source compositing
5. Exact text layers
6. Supported fields

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
      "x": 0.76,
      "y": 0.55,
      "max_width": 0.42,
      "max_height": 0.78,
      "anchor": "center",
      "require_alpha": true
    }
  ]
}
```

Do not combine `locked_layers` and `reference_images` in v1. A locked layer must use `operation: "generate"`. Scaling is proportional and source-derived, but resampling means it is not a claim of byte-for-byte pixel identity.

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
| `locked_layers` | Transparent source images composited after background generation |
| `preserve` | Non-negotiable details to retain |
| `avoid` | Additional forbidden visual elements |
| `brand` | Palette, tone, fonts, references, and avoid list |
| `professional_designer_mode` | Defaults to `true` |
| `text_safe_mode` | Generate without critical text and reserve layout space |
| `text_layers` | Exact copy applied after generation |
| `max_repair_attempts` | Defaults to `1`; capped at `3` |
| `min_professional_score` | Defaults to `4.0` out of `5` |
| `judge_model` | Defaults to `RENDRIVA_JUDGE_MODEL` or `gpt-5.5` |
| `concurrency` | Independent scene jobs in flight; defaults to `2`, maximum `4` |
