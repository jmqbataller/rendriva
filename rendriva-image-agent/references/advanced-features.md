# Rendriva advanced features

## Contents

- Reference Intelligence and multi-view identity
- Batch diversity and campaign consistency
- Single Model Face Lock
- Commerce Production Suite
- Product Region Truth Map
- Draft-to-Final Workflow
- Automatic cutout and natural shadow
- Reusable brand profiles
- Platform Export Pack
- Reference Fidelity Report
- Professional Typography Engine
- Marketplace Conversion Mode
- Output package

Rendriva 1.5 adds Single Model Face Lock while retaining verified cross-image campaign review, product-region truth contracts, draft-to-final promotion, and the original one-to-ten separate-output contract.

## Reference Intelligence and multi-view identity

Use `reference_assets` when each source has a distinct purpose. A filename-based detector supplies the role and view when omitted; explicit values always win.

```json
{
  "operation": "edit",
  "reference_assets": [
    {"path": "./shop-logo.png", "role": "logo"},
    {"path": "./shirt-front.png", "role": "product", "view": "front", "identity_id": "shirt-01"},
    {"path": "./shirt-back.png", "role": "product", "view": "back", "identity_id": "shirt-01"},
    {"path": "./mood.jpg", "role": "style", "use_for_palette": false}
  ],
  "product_identity": {"required_views": ["front", "back"]}
}
```

Supported roles are `product`, `model`, `logo`, `style`, `layout`, `lighting`, `background`, `typography`, `palette`, `identity`, and `general`. Product and identity references with the same `identity_id` form one product identity pack. A `model` reference controls face identity only and does not contribute to the automatic brand palette by default. Required missing product views fail validation instead of inviting invented construction.

## Batch diversity and campaign consistency

Campaign tokens remain stable while each output receives a different camera/composition/background direction.

```json
{
  "count": 5,
  "campaign": {
    "id": "payday-august",
    "consistency": "strict",
    "logo_position": "top-left safe zone",
    "grid": "8-point modular grid"
  },
  "diversity": {
    "enabled": true,
    "max_similarity": 0.992,
    "axes": ["composition", "camera", "background", "negative-space", "lighting"]
  }
}
```

The deterministic diversity gate compares completed outputs and repairs or fails near-duplicates. Campaign and diversity reports document both controls.

### Campaign Vision Lock

For campaigns with at least two passing outputs, cross-image vision review is enabled by default. It compares the complete batch for shared palette logic, typography, logo treatment, product-scale rhythm, lighting, grid/spacing, and meaningful diversity. Only identified outliers are repaired.

```json
{
  "campaign": {
    "id": "payday-august",
    "vision_lock": {
      "enabled": true,
      "min_score": 4.0,
      "max_repair_attempts": 1
    }
  }
}
```

`campaign-report.json` claims cross-image verification only when the batch judge actually ran. Disabling the vision judge leaves the report explicitly unverified.

## Single Model Face Lock

Multi-image `fashion-model` jobs enable this lock automatically. Request language such as “same model,” “one face,” “consistent model,” or “model for each variant” also enables it. Set `model_identity_lock` to `false` only when different people are intentional.

```json
{
  "prompt": "Generate one model for each clothing variant using the same face",
  "count": 4,
  "preset": "fashion-model",
  "model_identity_lock": {
    "enabled": true,
    "min_confidence": 0.8,
    "allow_pose_variation": true,
    "allow_expression_variation": true
  }
}
```

Without a supplied face, the first output must pass QA before it becomes the anchor for later variants. When the request explicitly says to match one uploaded face, a single generic upload is promoted to the authoritative `model` role automatically and is excluded from palette extraction. With multiple uploaded references, mark exactly one as `model` or set `model_identity_lock.source_path`:

```json
{
  "operation": "edit",
  "reference_assets": [
    {"path": "./shop-model-face.png", "role": "model", "use_for_palette": false},
    {"path": "./shirt-front.png", "role": "product", "view": "front"}
  ],
  "model_identity_lock": {"enabled": true, "min_confidence": 0.85}
}
```

Each later generation receives the same anchor. Per-image QA compares facial identity while allowing requested changes to outfit, pose, expression, camera, lighting, and background. A different person, lookalike, blended identity, or materially changed facial structure fails the gate and triggers repair only for that output. `model-identity-report.json` records the anchor fingerprint, comparison confidence, observations, and verification status. If vision comparison is disabled, later outputs cannot claim verified face consistency.

Runs made with the explicit mock provider exercise the workflow but keep `verified: false` and label their evidence synthetic; placeholder mock outputs do not establish real face fidelity.

## Commerce Production Suite

Rendriva 1.6 adds ten coordinated controls for fashion and product campaigns:

```json
{
  "sku_variant_matrix": {
    "variants": [
      {"id": "SKU-BLACK", "source_path": "./black.png", "expected_color": "black"},
      {"id": "SKU-BEIGE", "source_path": "./beige.png", "expected_color": "beige"}
    ]
  },
  "garment_construction_lock": true,
  "product_visibility_guard": {"min_visible_ratio": 0.9},
  "reference_preflight": {"min_edge": 512, "blocking": true},
  "localized_repair": {"rollback_on_lock_drift": true},
  "sku_color_guard": {"delta_e_tolerance": 6},
  "shot_director": {"shots": ["hero", "full-body-front"]},
  "approval_checkpoint": {"approved": true},
  "defect_memory": {"entries": ["no padded chest contour"]},
  "video_continuity_pack": {"aspect_ratio": "9:16"}
}
```

- **SKU Variant Matrix:** assign every output to exactly one authoritative product source. One variant per output is mapped automatically when variant count equals output count; otherwise provide `output_indices` or enable `auto_cycle` intentionally.
- **Garment Construction Lock:** verify neckline, sleeves, hem, seams, stitching, pockets, buttons, straps, silhouette, length, fit, print, and labels. Flat-lay jobs can prohibit padded, molded, mannequin, cup, or body contours.
- **Product Visibility Guard:** require a configurable visible-product ratio and protect important details from hands, hair, props, accessories, and cropping.
- **Reference Preflight:** inspect decoding and dimensions before generation. The default reports warnings; set `blocking: true` to stop before API spending when a source fails.
- **Localized Repair with Rollback:** target the smallest defective area and reject a repair candidate if a previously passing identity, product, logo, construction, color, or typography region changes.
- **SKU Color Guard:** distinguish scene lighting from the product's identity color and record the configured visual color tolerance.
- **Shot Director:** assign deliberate commerce coverage roles instead of random variations. Supported roles are `hero`, `full-body-front`, `three-quarter`, `back-view`, `product-detail`, `lifestyle`, `fabric-detail`, `side-view`, `catalogue-front`, and `campaign-finale`.
- **Approval Checkpoint:** when enabled but unapproved, generate only the selected anchor output and mark the remaining batch as awaiting approval. Start the approved production job with the accepted anchor as the face/model source.
- **Campaign Defect Memory:** add observed QA defects to later prompts within the same run and cap the retained list with `max_entries`.
- **Video Continuity Pack:** create `video-continuity-pack.json` containing approved keyframes, shot roles, SKU fingerprints, and first-to-last-frame preservation prompts.

The batch judge also compares SKU assignment, garment construction, product visibility, and source color across passing outputs. `commerce-production-report.json` records the evidence per image.

## Product Region Truth Map

Protected product and logo references automatically receive whole-asset silhouette/material/logo truth contracts. For precise print, label, stitching, fabric, or color regions, supply normalized bounds and an optional same-size mask.

```json
{
  "product_truth_map": {
    "regions": [
      {
        "name": "front-chest-print",
        "role": "print",
        "source_path": "./shirt-front.png",
        "mask_path": "./front-print-mask.png",
        "bbox": [0.22, 0.18, 0.56, 0.46],
        "preserve": ["exact artwork geometry", "exact ink colors"],
        "required": true
      }
    ]
  }
}
```

Supported roles are `silhouette`, `fabric`, `texture`, `construction`, `print`, `logo`, `label`, `color`, `stitching`, `material`, and `identity`. Every required region receives a named QA result. A missing or failed required region blocks `PASS`.

## Draft-to-Final Workflow

Generate low-quality concepts, score them, select distinct candidates, then promote only the selected drafts to final quality. Exact typography is withheld from promotion sources and applied only after final rendering.

```json
{
  "count": 3,
  "quality": "high",
  "draft_to_final": {
    "enabled": true,
    "candidate_count": 6,
    "draft_quality": "low",
    "selection_mode": "auto-score",
    "include_drafts": true
  }
}
```

For manual selection, set `selection_mode` to `manual` and provide exactly one unique candidate number per final output, for example `"selection": [4, 1, 6]`. The adapter records the full decision in `draft-selection-report.json` and preserves selected drafts as promotion evidence when requested.

## Automatic cutout and natural shadow

Use this literal source-composite path for a product on a flat background:

```json
{
  "locked_layers": [
    {
      "path": "./product-on-white.jpg",
      "role": "product",
      "require_alpha": false,
      "auto_cutout": true,
      "cutout_tolerance": 28,
      "edge_softness": 4,
      "shadow": {
        "enabled": true,
        "opacity": 75,
        "blur": 24,
        "offset_x": 12,
        "offset_y": 20
      }
    }
  ]
}
```

Auto cutout is intended for simple corner-matched backgrounds. Difficult hair, glass, reflective products, or complex scenery should use a prepared transparent source. The foreground RGB pixels remain source-derived; only the alpha mask, scale, position, and optional shadow are produced.

## Reusable brand profiles

Set `brand_profile` to an inline object or a JSON file. A job-level `brand` object overrides matching profile fields.

```json
{
  "brand_profile": "./my-shop-brand.json",
  "brand": {"tone": "premium, warm, direct"}
}
```

Every adapter run writes `brand-profile.json`, including an automatically extracted palette when references define the shop colors.

## Platform Export Pack

```json
{
  "platform_exports": [
    "shopee-square",
    "instagram-post",
    "instagram-story",
    "facebook-post",
    "website-hero",
    "tiktok-cover"
  ]
}
```

Exports use contain scaling and brand-neutral padding, so the product is never cropped to force a new aspect ratio. Each source output remains a separate image, and every export is a separate PNG under `platform-exports/`.

## Reference Fidelity Report

`reference-fidelity-report.json` records reference roles, views, identity signatures, SHA-256 source fingerprints, per-output comparison results, and literal locked-layer evidence. It does not claim verified preservation when comparison evidence is unavailable.

## Professional Typography Engine

Text layers support automatic sizing, word wrapping, height limits, contrast selection, and semantic styles.

```json
{
  "text_layers": [
    {
      "text": "PAYDAY SAVINGS START HERE",
      "style": "headline",
      "font_size": "auto",
      "color": "auto",
      "x": 0.08,
      "y": 0.08,
      "max_width": 0.48,
      "max_height": 0.24
    }
  ]
}
```

Styles are `headline`, `subheadline`, `body`, `price`, `badge`, and `caption`. Use `font_path` for an exact licensed brand font.

## Marketplace Conversion Mode

```json
{
  "marketplace": {
    "platform": "Shopee",
    "goal": "payday-sale",
    "price": "₱299",
    "original_price": "₱399",
    "discount": "25% OFF",
    "cta": "Shop now",
    "claims": ["Free shipping on eligible orders"]
  }
}
```

Goals are `product-hero`, `price-promotion`, `payday-sale`, `new-arrival`, `bundle`, and `trust-builder`. Supplied conversion copy becomes exact typography by default. Rendriva must never invent a price, discount, bundle item, rating, guarantee, credential, urgency claim, badge, or CTA.

## Output package

The ZIP can contain:

- each passing `image-XX` file;
- `manifest.json`;
- `quality-report.json`;
- `brand-profile.json`;
- `reference-fidelity-report.json`;
- `model-identity-report.json`;
- `diversity-report.json`;
- `campaign-report.json`;
- `draft-selection-report.json`;
- optional individual `drafts/draft-XX` candidates;
- individual `platform-exports/*.png` files.
