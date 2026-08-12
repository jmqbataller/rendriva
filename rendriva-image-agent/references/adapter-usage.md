# Rendriva adapter usage

## Contents

1. Requirements
2. Commands
3. Execution behavior
4. Native ChatGPT behavior
5. Evidence states

## Requirements

- Python 3.10 or newer
- Pillow 10 or newer for exact text overlays
- `OPENAI_API_KEY` for real API runs
- Network access to `api.openai.com`

Install the optional image-composition dependency:

```bash
python -m pip install -r scripts/requirements.txt
```

Keep the API key in the environment. Never store it in a job file.

## Commands

Validate and preview a job without making an API request:

```bash
python scripts/rendriva.py job.json --dry-run
```

Run a job:

```bash
OPENAI_API_KEY=... python scripts/rendriva.py job.json --output ./rendriva-runs
```

Resume an interrupted job:

```bash
OPENAI_API_KEY=... python scripts/rendriva.py job.json --output ./rendriva-runs --resume
```

Disable the external vision judge while retaining deterministic structural checks:

```bash
python scripts/rendriva.py job.json --no-vision-judge
```

## Execution behavior

- Compute a stable job ID from the normalized specification.
- Refuse counts outside `1..10`.
- Create one output record and one image file per planned item.
- Use a single multi-output generation request for variation mode when no per-scene input differs; save every returned item separately.
- Use independent generation requests for scene mode.
- Apply exact text layers after generation.
- Composite optional transparent `locked_layers` before typography for source-derived product accuracy.
- Default reference jobs to strict fidelity, protecting product construction, material, fabric texture, print, labels, color, proportions, and exact logo geometry.
- Automatically extract a usable brand palette from any supplied reference images. Prioritize a locked logo layer, then combine distinct colors from the remaining references.
- Detect or accept explicit reference roles and views, then build multi-view product identity packs.
- For fashion-model batches, approve one face anchor and reuse it sequentially for every later variant; compare each face and repair only identity drift.
- Apply the derived palette only to unprotected design elements and record its colors plus source fingerprints in the manifest.
- Load and emit reusable shop brand profiles; lock shared campaign tokens while enforcing distinct batch compositions.
- Compare the complete campaign batch visually and repair only identified outliers when Campaign Vision Lock is enabled.
- Build whole-asset or masked product truth regions and block `PASS` when a required region is missing or visibly changed.
- Optionally generate low-cost draft candidates, select distinct high-scoring concepts, and promote only those candidates to final quality.
- Record each locked layer's role and SHA-256 source fingerprint in the manifest; never generatively repair inside a source-derived layer.
- Optionally cut a product out from a simple flat background, add a natural shadow, wrap exact typography with automatic contrast, and add supplied marketplace copy without invented claims.
- Run structural checks and, by default, a vision-based professional-design judge.
- Reject deterministic near-duplicates and create separate contain-scaled platform exports when requested.
- Regenerate only an item marked `NEEDS_REPAIR`, once by default.
- Persist progress after every material step so `--resume` skips completed items.
- Package successful outputs, brand/fidelity/model-identity/diversity/campaign/draft-selection reports, and optional draft/platform exports into `rendriva-output.zip`.

## Native ChatGPT behavior

When the host exposes a native image-generation tool, call it separately for each planned output. A batch of ten means ten separate native results, not one prompt asking for a ten-panel sheet. For same-model batches, approve the first face, then attach that same anchor to every later call instead of generating all faces independently. Generated images may be retained by the host automatically; do not download or reconstruct them solely to create an archive.

## Evidence states

- `PASS`: all required gates pass and the professional score meets threshold.
- `NEEDS_REPAIR`: a repairable defect remains before the allowed retry.
- `FAILED`: generation or judging failed after allowed retries.
- `BLOCKED`: policy, missing tool, missing key, or unavailable capability prevented generation.

Strict generative reference jobs cannot receive `PASS` when the comparison judge is disabled. Source-derived `locked_layers` provide the literal-preservation path; ordinary reference edits remain high-fidelity generation and must not be described as pixel-identical.

The explicit `--mock` provider validates orchestration only. Its reports use `evidence_mode: synthetic-mock`; campaign and face comparisons may record synthetic pass logic but must keep `verified: false`. Placeholder mock pixels are not evidence of real face consistency, product fidelity, or commercial image quality.
