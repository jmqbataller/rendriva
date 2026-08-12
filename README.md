# Rendriva

**One request. Up to ten separate, professionally art-directed images. Never a collage by default.**

Rendriva is an installable ChatGPT Agent Skill plus a deterministic OpenAI Image API batch adapter. It turns an image request into a production workflow: plan, generate, inspect, repair, and deliver each image as an independent file.

## Download for ChatGPT Skills

[Download Rendriva ChatGPT Skill v1.6.0](https://github.com/jmqbataller/rendriva/releases/latest/download/rendriva-chatgpt-skill-v1.6.0.zip)

This upload-ready ZIP has `SKILL.md` at the archive root.

1. Download the ZIP.
2. Open the ChatGPT Skills upload flow.
3. Select `rendriva-chatgpt-skill-v1.6.0.zip`.
4. Review the skill details, then confirm the upload.

Integrity check: [SHA-256 checksum](dist/rendriva-chatgpt-skill-v1.6.0.zip.sha256)

## What Rendriva does

- Generates or edits **1–10 separate images per run**
- Supports **Variation Batch** and **Scene List** modes
- Prevents accidental collages, grids, contact sheets, and multi-panel canvases
- Applies professional art direction instead of generic AI-template styling
- Interprets reference images as product, logo, identity, palette, style, layout, lighting, background, or typography sources
- Combines front, back, side, and detail references into a multi-view product identity lock
- Automatically derives the shop palette from supplied reference images and prioritizes the logo
- Protects product fabric, weave, texture, stitching, construction, print, color, labels, and exact logo geometry
- Keeps one authoritative model face across every requested fashion variant, using the uploaded face when supplied
- Maps every output to one exact SKU source so variants cannot be swapped, blended, duplicated, or recolored
- Locks garment construction, protects important product details from occlusion, and separates product color from scene lighting
- Checks reference readability and resolution before generation and can block low-quality sources before API spending
- Directs complete hero/front/back/detail/lifestyle commerce shot lists instead of random variants
- Uses localized rollback-safe repair and remembers rejected campaign defects for later outputs
- Supports anchor approval checkpoints and video-ready continuity keyframe handoff
- Keeps campaign palette, typography, grid, spacing, and logo zones consistent across a batch
- Compares the complete batch through Campaign Vision Lock and repairs only visual outliers
- Protects named silhouette, material, fabric, texture, construction, print, logo, label, color, and stitching truth regions
- Generates low-cost draft candidates, selects distinct high-scoring concepts, and promotes only selected drafts to final quality
- Gives each batch output a distinct camera, composition, lighting, background, and negative-space direction
- Rejects near-duplicate outputs and repairs only the failed image
- Supports source-derived product cutout, natural shadow, and locked logo/product compositing
- Applies exact typography with automatic sizing, wrapping, and black/white contrast
- Adds only supplied marketplace prices, discounts, bundles, CTAs, and claims
- Creates separate contain-scaled Shopee, Instagram, Facebook, website, and TikTok exports
- Produces reusable brand, fidelity, diversity, campaign, quality, and manifest reports
- Preserves partial success and resumes interrupted jobs without repeating completed images

## Production presets

- Product Photography
- Apparel Flat-Lay
- Fashion Model
- Social Advertisement
- Logo and Icon
- Transparent DTF
- Poster and Flyer
- Website Hero
- Realistic Mockup
- General Creative

## Repository structure

```text
rendriva/
├── README.md
├── dist/
└── rendriva-image-agent/
    ├── SKILL.md
    ├── agents/openai.yaml
    ├── assets/icon.svg
    ├── references/
    └── scripts/
```

The `rendriva-image-agent/` directory is the portable Agent Skill package.

## Run the adapter

Requirements:

- Python 3.10+
- Pillow 10+
- An OpenAI API key for real API runs

Install the dependency:

```bash
python -m pip install -r rendriva-image-agent/scripts/requirements.txt
```

Keep the API key in your environment:

```bash
export OPENAI_API_KEY="your-key"
```

Validate the included example without making an API request:

```bash
python rendriva-image-agent/scripts/rendriva.py \
  rendriva-image-agent/scripts/example-job.json \
  --dry-run
```

Run or resume a job:

```bash
python rendriva-image-agent/scripts/rendriva.py \
  rendriva-image-agent/scripts/example-job.json \
  --output ./rendriva-runs

python rendriva-image-agent/scripts/rendriva.py \
  rendriva-image-agent/scripts/example-job.json \
  --output ./rendriva-runs \
  --resume
```

## Output contract

A request for ten images produces separate `image-01.png` through `image-10.png` files. A production package can also contain:

```text
manifest.json
quality-report.json
brand-profile.json
reference-fidelity-report.json
model-identity-report.json
commerce-production-report.json
video-continuity-pack.json
diversity-report.json
campaign-report.json
draft-selection-report.json
drafts/draft-01.png
platform-exports/*.png
rendriva-output.zip
```

Rendriva never combines the requested outputs into one collage unless the user explicitly requests that composition.

## Professional Designer Mode

Professional Designer Mode is enabled by default. It converts a simple prompt into an intentional creative brief covering purpose, audience, focal point, grid, hierarchy, spacing, palette, typography, lighting, material behavior, reference roles, product identity locks, prohibited elements, and output specifications.

The quality judge requires all non-negotiable gates to pass and a professional-design average of at least 4/5 by default.

## Reference accuracy

Reference jobs default to strict fidelity. Generative editing remains high fidelity rather than pixel-identical. For literal source preservation, use `locked_layers`; Rendriva generates only the surrounding design, derives alpha from a simple flat background when requested, and composites the source-derived product or logo afterward. When a requested angle exposes unseen construction, supply the matching reference view rather than asking the system to invent it.

## Security

- Never commit `OPENAI_API_KEY`.
- Keep provider credentials in environment variables or server-side secret storage.
- Job manifests never include API keys.
- Provider moderation and account limits still apply.

## Validation

The 61-test deterministic suite covers separate ten-image output, provider cardinality, no-collage prompts, partial failures, source-image localized repair, uploaded-face anchoring, face-drift repair, SKU mapping and assignment rejection, garment/visibility/color gates, reference preflight, approval checkpoints, defect memory, shot direction, video continuity packaging, cross-image Campaign Vision Lock, product-region truth contracts, drafts, reference roles, automatic palettes, strict fidelity, typography, exports, reporting, resume behavior, and ZIP packaging.

```bash
python -m unittest discover -s rendriva-image-agent/scripts/tests -v
```

## Status

Rendriva v1.6 is ready for controlled testing with real image-generation credentials. Live face consistency, SKU accuracy, photorealism, and product preservation still depend on the selected model, source quality, prompt constraints, and human approval for high-stakes commerce work.
