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
- Run structural checks and, by default, a vision-based professional-design judge.
- Regenerate only an item marked `NEEDS_REPAIR`, once by default.
- Persist progress after every material step so `--resume` skips completed items.
- Package successful outputs and reports into `rendriva-output.zip`.

## Native ChatGPT behavior

When the host exposes a native image-generation tool, call it separately for each planned output. A batch of ten means ten separate native results, not one prompt asking for a ten-panel sheet. Generated images may be retained by the host automatically; do not download or reconstruct them solely to create an archive.

## Evidence states

- `PASS`: all required gates pass and the professional score meets threshold.
- `NEEDS_REPAIR`: a repairable defect remains before the allowed retry.
- `FAILED`: generation or judging failed after allowed retries.
- `BLOCKED`: policy, missing tool, missing key, or unavailable capability prevented generation.
