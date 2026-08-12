# Rendriva

**One request. Up to ten separate, professionally art-directed images. Never a collage by default.**

Rendriva is an installable ChatGPT Agent Skill plus a deterministic OpenAI Image API batch adapter. It turns an image request into a production workflow: plan, generate, inspect, repair, and deliver each image as an independent file.

## Download for ChatGPT Skills

[Download Rendriva ChatGPT Skill v1.2.0](https://github.com/jmqbataller/rendriva/raw/refs/heads/main/dist/rendriva-chatgpt-skill-v1.2.0.zip)

This upload-ready ZIP has `SKILL.md` at the archive root.

1. Download the ZIP.
2. Open the ChatGPT Skills upload flow.
3. Select `rendriva-chatgpt-skill-v1.2.0.zip`.
4. Review the skill details, then confirm the upload.

Integrity check: [SHA-256 checksum](dist/rendriva-chatgpt-skill-v1.2.0.zip.sha256)

## What Rendriva does

- Generates or edits **1–10 separate images per run**
- Supports **Variation Batch** and **Scene List** modes
- Prevents accidental collages, grids, contact sheets, and multi-panel canvases
- Applies professional art direction instead of generic AI-template styling
- Judges every output independently for instructions, composition, hierarchy, brand consistency, realism, artifacts, and commercial usability
- Repairs only the failed image while preserving passing outputs
- Compares generated outputs with supplied reference images
- Automatically extracts the default shop/brand palette from any supplied reference image
- Combines distinct colors from multiple references and prioritizes an identified logo
- Applies reference-derived colors to backgrounds, accents, typography, and design elements without recoloring protected assets
- Defaults supplied product and logo references to strict fidelity
- Protects fabric weave, texture, stitching, construction, print, color, proportions, labels, and exact logo geometry
- Supports source-derived `locked_layers` for transparent products, artwork, and logos that must not be redrawn
- Records locked-asset roles and SHA-256 source fingerprints in the manifest
- Adds critical poster and advertisement copy programmatically for exact spelling
- Produces individual files, `manifest.json`, `quality-report.json`, and `rendriva-output.zip`
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

Run the job:

```bash
python rendriva-image-agent/scripts/rendriva.py \
  rendriva-image-agent/scripts/example-job.json \
  --output ./rendriva-runs
```

Resume an interrupted job:

```bash
python rendriva-image-agent/scripts/rendriva.py \
  rendriva-image-agent/scripts/example-job.json \
  --output ./rendriva-runs \
  --resume
```

## Output contract

A request for ten images produces:

```text
image-01.png
image-02.png
...
image-10.png
manifest.json
quality-report.json
rendriva-output.zip
```

Rendriva never combines those ten requested outputs into one collage unless the user explicitly requests a collage.

## Professional Designer Mode

Professional Designer Mode is enabled by default. It converts a simple prompt into an intentional creative brief covering purpose, audience, focal point, grid, hierarchy, spacing, palette, typography, lighting, material behavior, reference locks, prohibited elements, and output specifications.

The quality judge requires all non-negotiable gates to pass and a professional-design average of at least 4/5 by default.

## Reference accuracy

Reference images now automatically define the default brand palette unless an explicit palette is supplied. With multiple references, Rendriva combines distinct dominant colors and prioritizes an identified logo; the palette applies only to unprotected design elements. Reference jobs also default to strict fidelity. Any visible drift in product material, fabric texture, stitching, construction, print, color, label, proportions, or logo geometry fails the quality gate. Generative editing remains high fidelity rather than pixel-identical; for literal preservation, supply transparent product and logo assets through `locked_layers` so Rendriva generates only the surrounding design and composites the source-derived assets afterward.

## Security

- Never commit `OPENAI_API_KEY`.
- Keep provider credentials in environment variables or server-side secret storage.
- Job manifests never include API keys.
- Provider moderation and account limits still apply.

## Validation

The included test suite covers:

- Ten independent outputs
- No-collage prompt enforcement
- Scene and variation planning
- Partial failures
- Targeted repair
- Automatic multi-reference palette extraction, logo priority, and manual override
- Reference-palette-aware design QA and manifest fingerprints
- Strict product, fabric, texture, and logo fidelity gates
- Reference-aware judging that cannot pass unverified strict edits
- Source-fingerprint-aware locked-layer compositing
- Exact text expectations
- Resume and duplicate-job protection
- ZIP packaging

Run it with:

```bash
python -m unittest discover -s rendriva-image-agent/scripts/tests -v
```

## Status

Rendriva v1.2 is ready for controlled testing with real image-generation credentials. Live output quality still depends on the selected model, references, prompt constraints, and human review for high-stakes brand or product work.
