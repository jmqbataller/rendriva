"""Advanced normalization, batch-control, export, and reporting for Rendriva."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

try:
    from PIL import Image, ImageColor
except ImportError:  # pragma: no cover
    Image = ImageColor = None


REFERENCE_ROLES = {
    "product",
    "logo",
    "style",
    "layout",
    "lighting",
    "background",
    "typography",
    "palette",
    "identity",
    "general",
}
REFERENCE_VIEWS = {"front", "back", "left-side", "right-side", "side", "detail", "three-quarter", "top", "bottom", "unspecified"}
DIVERSITY_AXES = ("composition", "camera", "background", "negative-space", "lighting")
DIVERSITY_DIRECTIONS = (
    "centered hero composition; eye-level camera; architectural negative space",
    "asymmetric rule-of-thirds composition; three-quarter camera; copy space on the left",
    "low-angle premium hero; shallow layered depth; copy space above",
    "top-down graphic composition; disciplined modular grid; balanced corner accents",
    "close-detail crop without losing product identity; tactile light; quiet background",
    "wide editorial composition; lateral light; generous copy space on the right",
    "minimal pedestal composition; soft frontal light; restrained tonal backdrop",
    "dynamic diagonal composition; crisp rim light; deep but uncluttered scene",
    "catalogue-straight composition; even commercial light; neutral seamless background",
    "campaign finale composition; elevated camera; layered brand-color environment",
)
DIVERSITY_COMPONENTS = (
    {"composition": "centered hero", "camera": "eye-level", "background": "architectural tonal set", "negative-space": "copy space above", "lighting": "soft frontal key"},
    {"composition": "asymmetric rule of thirds", "camera": "three-quarter", "background": "layered brand-color planes", "negative-space": "copy space on the left", "lighting": "lateral studio key"},
    {"composition": "low hero framing", "camera": "low angle", "background": "deep minimal set", "negative-space": "open upper third", "lighting": "crisp rim light"},
    {"composition": "modular top-down grid", "camera": "top view", "background": "clean graphic field", "negative-space": "balanced corner space", "lighting": "even commercial light"},
    {"composition": "tactile detail composition", "camera": "macro detail", "background": "quiet tonal backdrop", "negative-space": "compact caption zone", "lighting": "grazing texture light"},
    {"composition": "wide editorial framing", "camera": "wide eye-level", "background": "soft environmental depth", "negative-space": "copy space on the right", "lighting": "window-like side light"},
    {"composition": "minimal pedestal hero", "camera": "slightly elevated", "background": "neutral seamless", "negative-space": "generous side margins", "lighting": "softbox key and fill"},
    {"composition": "controlled diagonal movement", "camera": "three-quarter close", "background": "restrained gradient set", "negative-space": "open lower corner", "lighting": "directional key and rim"},
    {"composition": "catalogue-straight framing", "camera": "orthographic profile", "background": "light neutral seamless", "negative-space": "uniform breathing room", "lighting": "balanced catalogue light"},
    {"composition": "campaign finale hero", "camera": "elevated three-quarter", "background": "layered brand environment", "negative-space": "clean headline band", "lighting": "dramatic controlled key"},
)
PLATFORM_PRESETS = {
    "shopee-square": (1080, 1080),
    "instagram-post": (1080, 1350),
    "instagram-story": (1080, 1920),
    "facebook-post": (1200, 1500),
    "website-hero": (1920, 1080),
    "tiktok-cover": (1080, 1920),
}
MARKETPLACE_GOALS = {
    "product-hero": "Make the product the immediate focal point with clean commercial hierarchy.",
    "price-promotion": "Prioritize an exact supplied price and offer without inventing values.",
    "payday-sale": "Use energetic but disciplined sale hierarchy and only supplied claims.",
    "new-arrival": "Signal freshness through layout and art direction without unsupported claims.",
    "bundle": "Show the supplied bundle count and contents clearly; never invent included items.",
    "trust-builder": "Use only supplied proof points, guarantees, ratings, or credentials.",
}


def _sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _resolved_file(value: str, base_dir: Path, label: str) -> str:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    if not path.is_file():
        raise ValueError(f"{label} not found: {value}")
    return str(path)


def infer_reference_role(path: str) -> str:
    name = Path(path).stem.lower().replace("_", "-")
    rules = (
        ("logo", ("logo", "wordmark", "brandmark", "emblem")),
        ("typography", ("font", "type", "typography")),
        ("palette", ("palette", "colour", "color-swatch", "swatch")),
        ("layout", ("layout", "composition", "template", "wireframe")),
        ("lighting", ("lighting", "light-reference", "shadow")),
        ("background", ("background", "backdrop", "scene")),
        ("style", ("style", "mood", "inspiration", "inspo", "editorial")),
        ("identity", ("identity", "character", "face", "person")),
        ("product", ("product", "shirt", "garment", "dress", "shoe", "bottle", "bag", "packaging", "item", "front", "back", "side", "detail")),
    )
    for role, tokens in rules:
        if any(token in name for token in tokens):
            return role
    return "general"


def infer_reference_view(path: str) -> str:
    name = Path(path).stem.lower().replace("_", "-")
    if "three-quarter" in name or "3-4" in name or "threequarter" in name:
        return "three-quarter"
    for view in ("front", "back", "left-side", "right-side", "side", "detail", "top", "bottom"):
        if re.search(rf"(^|[^a-z]){re.escape(view)}([^a-z]|$)", name):
            return view
    return "unspecified"


def infer_identity_id(path: str) -> str:
    name = Path(path).stem.lower().replace("_", "-")
    name = re.sub(r"(^|-)(front|back|left-side|right-side|side|detail|three-quarter|threequarter|top|bottom|view|reference|ref|image|photo)(-|$)", "-", name)
    name = re.sub(r"-+", "-", name).strip("-")
    return name or "default-product"


def normalize_reference_assets(
    reference_images: Any,
    reference_assets: Any,
    base_dir: Path,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    if reference_images is not None:
        if not isinstance(reference_images, list) or any(not isinstance(value, str) or not value.strip() for value in reference_images):
            raise ValueError("reference_images must be a list of non-empty strings.")
        entries.extend({"path": value} for value in reference_images)
    if reference_assets is not None:
        if not isinstance(reference_assets, list) or any(not isinstance(value, dict) for value in reference_assets):
            raise ValueError("reference_assets must be a list of objects.")
        entries.extend(copy.deepcopy(reference_assets))

    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for index, entry in enumerate(entries, start=1):
        value = entry.get("path")
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"reference_assets[{index}].path must be a non-empty string.")
        path = _resolved_file(value, base_dir, "Reference image")
        explicit_role = entry.get("role")
        role = str(explicit_role or infer_reference_role(path))
        if role not in REFERENCE_ROLES:
            raise ValueError(f"reference_assets[{index}].role must be one of: {', '.join(sorted(REFERENCE_ROLES))}.")
        view = str(entry.get("view") or infer_reference_view(path))
        if view not in REFERENCE_VIEWS:
            raise ValueError(f"reference_assets[{index}].view must be one of: {', '.join(sorted(REFERENCE_VIEWS))}.")
        identity_id = str(entry.get("identity_id") or (infer_identity_id(path) if role in {"product", "identity"} else ""))
        preserve = entry.get("preserve", [])
        if not isinstance(preserve, list) or any(not isinstance(item, str) or not item.strip() for item in preserve):
            raise ValueError(f"reference_assets[{index}].preserve must be a list of non-empty strings.")
        key = (path, role, view)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(
            {
                "path": path,
                "role": role,
                "role_source": "explicit" if explicit_role else "filename-inference",
                "view": view,
                "identity_id": identity_id,
                "use_for_palette": bool(entry.get("use_for_palette", role not in {"layout", "lighting", "typography"})),
                "priority": int(entry.get("priority", 100 if role == "logo" else 50)),
                "preserve": [item.strip() for item in preserve],
                "sha256": _sha256(path),
            }
        )
    return normalized


def build_identity_packs(reference_assets: list[dict[str, Any]], required_views: list[str] | None = None) -> list[dict[str, Any]]:
    invalid = sorted(set(required_views or []) - REFERENCE_VIEWS)
    if invalid:
        raise ValueError(f"Unknown required product views: {', '.join(invalid)}.")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for asset in reference_assets:
        if asset["role"] in {"product", "identity"}:
            grouped.setdefault(asset["identity_id"] or "default-product", []).append(asset)
    packs: list[dict[str, Any]] = []
    for identity_id, assets in grouped.items():
        views = sorted({asset["view"] for asset in assets})
        missing = sorted(set(required_views or []) - set(views))
        packs.append(
            {
                "identity_id": identity_id,
                "views": views,
                "missing_required_views": missing,
                "assets": [{"path": asset["path"], "view": asset["view"], "sha256": asset["sha256"]} for asset in assets],
                "identity_signature": hashlib.sha256("|".join(sorted(asset["sha256"] for asset in assets)).encode()).hexdigest()[:16],
            }
        )
    return packs


def load_brand_profile(value: Any, base_dir: Path) -> tuple[dict[str, Any], dict[str, str] | None]:
    if value is None:
        return {}, None
    if isinstance(value, dict):
        return copy.deepcopy(value), {"source": "inline", "sha256": hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()}
    if not isinstance(value, str) or not value.strip():
        raise ValueError("brand_profile must be a JSON object or a path to a JSON file.")
    path = _resolved_file(value, base_dir, "Brand profile")
    try:
        profile = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not load brand profile {value}: {exc}") from exc
    if not isinstance(profile, dict):
        raise ValueError("brand_profile JSON must contain an object.")
    return profile, {"source": path, "sha256": _sha256(path)}


def normalize_diversity(value: Any, count: int) -> dict[str, Any]:
    if value is None:
        value = {}
    if isinstance(value, bool):
        value = {"enabled": value}
    if not isinstance(value, dict):
        raise ValueError("diversity must be a boolean or object.")
    threshold = float(value.get("max_similarity", 0.992))
    if not 0.5 <= threshold <= 1:
        raise ValueError("diversity.max_similarity must be from 0.5 through 1.")
    axes = value.get("axes", list(DIVERSITY_AXES))
    if not isinstance(axes, list) or any(axis not in DIVERSITY_AXES for axis in axes):
        raise ValueError(f"diversity.axes must use: {', '.join(DIVERSITY_AXES)}.")
    return {
        "enabled": bool(value.get("enabled", count > 1)),
        "max_similarity": threshold,
        "axes": axes,
        "allow_repeats": bool(value.get("allow_repeats", False)),
    }


def variation_direction(index: int, axes: list[str] | None = None) -> str:
    selected_axes = axes or list(DIVERSITY_AXES)
    components = DIVERSITY_COMPONENTS[(index - 1) % len(DIVERSITY_COMPONENTS)]
    return "; ".join(f"{axis}={components[axis]}" for axis in selected_axes)


def normalize_campaign(value: Any, brand: dict[str, Any], count: int) -> dict[str, Any]:
    if value is None:
        value = {}
    if isinstance(value, str):
        value = {"id": value}
    if not isinstance(value, dict):
        raise ValueError("campaign must be a string or object.")
    consistency = str(value.get("consistency", "strict" if count > 1 else "balanced"))
    if consistency not in {"strict", "balanced", "loose"}:
        raise ValueError("campaign.consistency must be strict, balanced, or loose.")
    tokens = {
        "palette": brand.get("palette", []),
        "fonts": brand.get("fonts", []),
        "tone": brand.get("tone", "professional, restrained, commercial"),
        "logo_position": value.get("logo_position", "consistent safe-zone placement"),
        "grid": value.get("grid", "8-point spacing rhythm"),
        "spacing": value.get("spacing", "consistent campaign spacing"),
    }
    tokens.update(value.get("tokens", {}) if isinstance(value.get("tokens", {}), dict) else {})
    signature = hashlib.sha256(json.dumps(tokens, sort_keys=True).encode()).hexdigest()[:16]
    return {
        "enabled": bool(value.get("enabled", count > 1 or bool(value))),
        "id": str(value.get("id", "default-campaign")),
        "name": str(value.get("name", value.get("id", "Rendriva campaign"))),
        "consistency": consistency,
        "tokens": tokens,
        "signature": signature,
    }


def normalize_platform_exports(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("platform_exports must be a list.")
    exports: list[dict[str, Any]] = []
    for index, raw in enumerate(value, start=1):
        item = {"preset": raw} if isinstance(raw, str) else copy.deepcopy(raw)
        if not isinstance(item, dict):
            raise ValueError(f"platform_exports[{index}] must be a preset name or object.")
        preset = str(item.get("preset", "custom"))
        if preset in PLATFORM_PRESETS:
            width, height = PLATFORM_PRESETS[preset]
        else:
            width, height = item.get("width"), item.get("height")
            if isinstance(width, bool) or isinstance(height, bool) or not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
                raise ValueError(f"platform_exports[{index}] needs a known preset or positive width and height.")
        strategy = str(item.get("strategy", "contain"))
        if strategy != "contain":
            raise ValueError("platform_exports currently uses strategy='contain' to prevent product cropping.")
        exports.append({"preset": preset, "width": width, "height": height, "strategy": strategy, "background": str(item.get("background", "brand-neutral"))})
    return exports


def normalize_marketplace(value: Any) -> dict[str, Any]:
    if value is None:
        return {"enabled": False, "goal": "product-hero", "platform": "general-marketplace", "exact_copy": {}, "claims": [], "auto_text_layers": False}
    if isinstance(value, str):
        value = {"goal": value}
    if not isinstance(value, dict):
        raise ValueError("marketplace must be a goal string or object.")
    goal = str(value.get("goal", "product-hero"))
    if goal not in MARKETPLACE_GOALS:
        raise ValueError(f"marketplace.goal must be one of: {', '.join(sorted(MARKETPLACE_GOALS))}.")
    exact_copy = {}
    for field in ("price", "original_price", "discount", "cta", "badge", "bundle_count"):
        if value.get(field) not in (None, ""):
            exact_copy[field] = str(value[field])
    claims = value.get("claims", [])
    if not isinstance(claims, list) or any(not isinstance(claim, str) or not claim.strip() for claim in claims):
        raise ValueError("marketplace.claims must be a list of non-empty strings.")
    return {
        "enabled": bool(value.get("enabled", True)),
        "goal": goal,
        "platform": str(value.get("platform", "general-marketplace")),
        "direction": MARKETPLACE_GOALS[goal],
        "exact_copy": exact_copy,
        "claims": [claim.strip() for claim in claims],
        "auto_text_layers": bool(value.get("auto_text_layers", True)),
    }


def image_similarity(left: Path, right: Path) -> float:
    if Image is None:
        return 0.0
    with Image.open(left) as source:
        first = source.convert("RGB").resize((64, 64), Image.Resampling.BILINEAR)
    with Image.open(right) as source:
        second = source.convert("RGB").resize((64, 64), Image.Resampling.BILINEAR)
    first_data, second_data = first.tobytes(), second.tobytes()
    difference = sum(abs(a - b) for a, b in zip(first_data, second_data)) / (len(first_data) * 255)
    return round(1 - difference, 6)


def create_platform_exports(job_dir: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    exports = manifest["spec"].get("platform_exports", [])
    if not exports:
        return []
    if Image is None:
        raise RuntimeError("Pillow is required for platform exports.")
    brand_colors = manifest["spec"].get("brand", {}).get("palette", [])
    background = brand_colors[-1] if brand_colors else "#F4F1EA"
    try:
        bg_rgb = ImageColor.getrgb(background)
    except ValueError:
        bg_rgb = (244, 241, 234)
    export_root = job_dir / "platform-exports"
    export_root.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for output in manifest["outputs"]:
        if output["status"] != "PASS":
            continue
        source_path = job_dir / output["file"]
        output_exports = []
        with Image.open(source_path) as opened:
            source = opened.convert("RGBA")
        for export_index, config in enumerate(exports, start=1):
            target = (config["width"], config["height"])
            scale = min(target[0] / source.width, target[1] / source.height)
            size = (max(1, round(source.width * scale)), max(1, round(source.height * scale)))
            resized = source.resize(size, Image.Resampling.LANCZOS)
            configured_background = config.get("background", "brand-neutral")
            if configured_background == "brand-neutral":
                export_rgb = bg_rgb
            else:
                try:
                    export_rgb = ImageColor.getrgb(configured_background)
                except ValueError as exc:
                    raise RuntimeError(f"Invalid platform export background: {configured_background}") from exc
            canvas = Image.new("RGBA", target, (*export_rgb, 255))
            position = ((target[0] - size[0]) // 2, (target[1] - size[1]) // 2)
            canvas.alpha_composite(resized, dest=position)
            name = f"{Path(output['file']).stem}-{export_index:02d}-{config['preset']}-{target[0]}x{target[1]}.png"
            path = export_root / name
            canvas.save(path, format="PNG")
            record = {"preset": config["preset"], "file": str(path.relative_to(job_dir)), "width": target[0], "height": target[1], "strategy": "contain", "source_uncropped": True}
            output_exports.append(record)
            records.append({"output_index": output["index"], **record})
        output["platform_exports"] = output_exports
    return records


def reference_fidelity_report(manifest: dict[str, Any]) -> dict[str, Any]:
    spec = manifest["spec"]
    outputs = []
    for item in manifest["outputs"]:
        composite = item.get("locked_layer_composite") or {}
        outputs.append(
            {
                "index": item["index"],
                "file": item["file"],
                "status": item["status"],
                "reference_preservation": ((item.get("quality") or {}).get("vision") or {}).get("reference_preservation"),
                "literal_source_layers": composite.get("layers", []),
                "evidence_note": (item.get("quality") or {}).get("evidence_note"),
            }
        )
    return {
        "job_id": manifest["job_id"],
        "fidelity_mode": spec.get("fidelity_mode"),
        "reference_assets": spec.get("reference_assets", []),
        "identity_packs": spec.get("identity_packs", []),
        "outputs": outputs,
    }


def diversity_report(job_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    passing = [item for item in manifest["outputs"] if item["status"] == "PASS" and (job_dir / item["file"]).is_file()]
    pairs = []
    for left_index, left in enumerate(passing):
        for right in passing[left_index + 1 :]:
            similarity = image_similarity(job_dir / left["file"], job_dir / right["file"])
            pairs.append({"left": left["index"], "right": right["index"], "similarity": similarity, "passes": similarity <= manifest["spec"]["diversity"]["max_similarity"]})
    output_reviews = [
        {"index": item["index"], "status": item["status"], "review": (item.get("quality") or {}).get("diversity")}
        for item in manifest["outputs"]
    ]
    rejected_duplicates = [
        value for value in output_reviews
        if value["review"] and value["review"].get("nearest")
        and value["review"]["nearest"]["similarity"] > value["review"]["threshold"]
    ]
    return {
        "job_id": manifest["job_id"],
        "policy": manifest["spec"].get("diversity", {}),
        "pairs": pairs,
        "outputs": output_reviews,
        "rejected_duplicate_outputs": [value["index"] for value in rejected_duplicates],
        "passed": all(pair["passes"] for pair in pairs) and not rejected_duplicates,
    }
