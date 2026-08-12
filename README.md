# Rendriva

**One request. Up to ten separate, professionally art-directed images. Never a collage by default.**

Rendriva is an installable ChatGPT Agent Skill plus a deterministic OpenAI Image API batch adapter. It turns an image request into a production workflow: plan, generate, inspect, repair, and deliver each image as an independent file.

## What Rendriva does

- Generates or edits **1–10 separate images per run**
- Supports **Variation Batch** and **Scene List** modes
- Prevents accidental collages, grids, contact sheets, and multi-panel canvases
- Applies professional art direction instead of generic AI-template styling
- Judges every output independently for instructions, composition, hierarchy, brand consistency, realism, artifacts, and commercial usability
- Repairs only the failed image while preserving passing outputs
- Compares generated outputs with supplied reference images
- Supports source-derived `locked_layers` for transparent products or artwork that should not be redrawn
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

Generative reference editing reduces drift but cannot promise pixel-identical reproduction. When literal product or artwork preservation matters, supply a transparent source asset through `locked_layers`; Rendriva generates the surrounding design and composites the source-derived layer afterward.

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
- Reference-aware judging
- Locked-layer compositing
- Exact text expectations
- Resume and duplicate-job protection
- ZIP packaging

Run it with:

```bash
python -m unittest discover -s rendriva-image-agent/scripts/tests -v
```

## Status

Rendriva v1 is ready for controlled testing with real image-generation credentials. Live output quality still depends on the selected model, references, prompt constraints, and human review for high-stakes brand or product work.
