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
TRUTH_REGION_ROLES = {
    "silhouette", "fabric", "texture", "construction", "print", "logo",
    "label", "color", "stitching", "material", "identity",
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
    vision_value = value.get("vision_lock", count > 1)
    if isinstance(vision_value, bool):
        vision_value = {"enabled": vision_value}
    if not isinstance(vision_value, dict):
        raise ValueError("campaign.vision_lock must be a boolean or object.")
    vision_min_score = float(vision_value.get("min_score", 4.0))
    vision_repairs = vision_value.get("max_repair_attempts", 1)
    if not 0 <= vision_min_score <= 5:
        raise ValueError("campaign.vision_lock.min_score must be from 0 through 5.")
    if isinstance(vision_repairs, bool) or not isinstance(vision_repairs, int) or not 0 <= vision_repairs <= 2:
        raise ValueError("campaign.vision_lock.max_repair_attempts must be an integer from 0 through 2.")
    return {
        "enabled": bool(value.get("enabled", count > 1 or bool(value))),
        "id": str(value.get("id", "default-campaign")),
        "name": str(value.get("name", value.get("id", "Rendriva campaign"))),
        "consistency": consistency,
        "tokens": tokens,
        "signature": signature,
        "vision_lock": {
            "enabled": bool(vision_value.get("enabled", count > 1)),
            "min_score": vision_min_score,
            "max_repair_attempts": vision_repairs,
        },
    }


def normalize_product_truth_map(
    value: Any,
    reference_assets: list[dict[str, Any]],
    locked_layers: list[dict[str, Any]],
    base_dir: Path,
) -> dict[str, Any]:
    if value is False:
        return {"enabled": False, "mode": "disabled", "regions": []}
    if value is None:
        regions: list[dict[str, Any]] = []
        sources = [
            {"path": asset["path"], "source_role": asset["role"], "identity_id": asset.get("identity_id", ""), "strategy": "vision-comparison"}
            for asset in reference_assets if asset["role"] in {"product", "identity", "logo"}
        ] + [
            {"path": layer["path"], "source_role": layer["role"], "identity_id": "", "strategy": "literal-source-composite"}
            for layer in locked_layers if layer["role"] in {"product", "identity", "logo", "artwork", "protected-asset"}
        ]
        for source_index, source in enumerate(sources, start=1):
            stem = source["identity_id"] or Path(source["path"]).stem
            if source["source_role"] == "logo":
                roles = ["logo"]
            elif source["source_role"] in {"artwork"}:
                roles = ["print", "color"]
            else:
                roles = ["silhouette", "material"]
            for role in roles:
                regions.append(
                    {
                        "name": f"{stem}-{role}-{source_index}",
                        "role": role,
                        "source_path": source["path"],
                        "source_sha256": _sha256(source["path"]),
                        "mask_path": None,
                        "mask_sha256": None,
                        "bbox": [0.0, 0.0, 1.0, 1.0],
                        "required": True,
                        "preserve": [f"exact {role}"],
                        "comparison_strategy": source["strategy"],
                    }
                )
        return {"enabled": bool(regions), "mode": "automatic-whole-asset", "regions": regions}
    if not isinstance(value, dict):
        raise ValueError("product_truth_map must be false or an object.")
    raw_regions = value.get("regions", [])
    if not isinstance(raw_regions, list) or any(not isinstance(region, dict) for region in raw_regions):
        raise ValueError("product_truth_map.regions must be a list of objects.")
    default_sources = [asset["path"] for asset in reference_assets if asset["role"] in {"product", "identity", "logo"}]
    default_sources += [layer["path"] for layer in locked_layers]
    normalized: list[dict[str, Any]] = []
    names: set[str] = set()
    for index, raw in enumerate(raw_regions, start=1):
        name = str(raw.get("name", f"region-{index}")).strip()
        if not name or name in names:
            raise ValueError(f"product_truth_map.regions[{index}].name must be unique and non-empty.")
        names.add(name)
        role = str(raw.get("role", "identity"))
        if role not in TRUTH_REGION_ROLES:
            raise ValueError(f"product_truth_map.regions[{index}].role must be one of: {', '.join(sorted(TRUTH_REGION_ROLES))}.")
        source_value = raw.get("source_path", raw.get("source"))
        if source_value is None:
            if not default_sources:
                raise ValueError(f"product_truth_map.regions[{index}] needs a source_path because no protected source is available.")
            source_path = default_sources[0]
        elif not isinstance(source_value, str) or not source_value.strip():
            raise ValueError(f"product_truth_map.regions[{index}].source_path must be a non-empty string.")
        else:
            source_path = _resolved_file(source_value, base_dir, "Truth-map source")
        mask_path = None
        if raw.get("mask_path") is not None:
            if not isinstance(raw["mask_path"], str) or not raw["mask_path"].strip():
                raise ValueError(f"product_truth_map.regions[{index}].mask_path must be a non-empty string.")
            mask_path = _resolved_file(raw["mask_path"], base_dir, "Truth-map mask")
            if Image is not None:
                try:
                    with Image.open(source_path) as source_image, Image.open(mask_path) as mask_image:
                        if source_image.size != mask_image.size:
                            raise ValueError(f"product_truth_map.regions[{index}] mask dimensions must match its source image.")
                except ValueError:
                    raise
                except Exception as exc:
                    raise ValueError(f"product_truth_map.regions[{index}] mask/source images could not be inspected: {exc}") from exc
        bbox = raw.get("bbox", [0.0, 0.0, 1.0, 1.0])
        if not isinstance(bbox, list) or len(bbox) != 4 or any(isinstance(number, bool) or not isinstance(number, (int, float)) for number in bbox):
            raise ValueError(f"product_truth_map.regions[{index}].bbox must be [x, y, width, height].")
        bbox = [float(number) for number in bbox]
        if any(number < 0 or number > 1 for number in bbox) or bbox[2] <= 0 or bbox[3] <= 0 or bbox[0] + bbox[2] > 1 or bbox[1] + bbox[3] > 1:
            raise ValueError(f"product_truth_map.regions[{index}].bbox must fit inside normalized source bounds.")
        preserve = raw.get("preserve", [f"exact {role}"])
        if not isinstance(preserve, list) or any(not isinstance(item, str) or not item.strip() for item in preserve):
            raise ValueError(f"product_truth_map.regions[{index}].preserve must be a list of non-empty strings.")
        literal = source_path in {layer["path"] for layer in locked_layers}
        normalized.append(
            {
                "name": name,
                "role": role,
                "source_path": source_path,
                "source_sha256": _sha256(source_path),
                "mask_path": mask_path,
                "mask_sha256": _sha256(mask_path) if mask_path else None,
                "bbox": bbox,
                "required": bool(raw.get("required", True)),
                "preserve": [item.strip() for item in preserve],
                "comparison_strategy": "literal-source-composite" if literal else "masked-vision-comparison" if mask_path else "bounded-vision-comparison",
            }
        )
    return {"enabled": bool(value.get("enabled", True)) and bool(normalized), "mode": "explicit-regions", "regions": normalized}


def normalize_draft_to_final(value: Any, final_count: int) -> dict[str, Any]:
    if value is None:
        value = False
    if isinstance(value, bool):
        value = {"enabled": value}
    if not isinstance(value, dict):
        raise ValueError("draft_to_final must be a boolean or object.")
    enabled = bool(value.get("enabled", True))
    candidate_count = value.get("candidate_count", min(10, max(final_count * 2, final_count)))
    if isinstance(candidate_count, bool) or not isinstance(candidate_count, int) or not final_count <= candidate_count <= 10:
        raise ValueError("draft_to_final.candidate_count must be an integer from final count through 10.")
    draft_quality = str(value.get("draft_quality", "low"))
    if draft_quality not in {"low", "medium", "auto"}:
        raise ValueError("draft_to_final.draft_quality must be low, medium, or auto.")
    selection_mode = str(value.get("selection_mode", "auto-score"))
    if selection_mode not in {"auto-score", "manual"}:
        raise ValueError("draft_to_final.selection_mode must be auto-score or manual.")
    selection = value.get("selection", [])
    if not isinstance(selection, list) or any(isinstance(item, bool) or not isinstance(item, int) for item in selection):
        raise ValueError("draft_to_final.selection must be a list of candidate numbers.")
    if selection_mode == "manual" and enabled and len(selection) != final_count:
        raise ValueError("Manual draft selection must contain exactly one candidate number per final output.")
    if len(set(selection)) != len(selection) or any(item < 1 or item > candidate_count for item in selection):
        raise ValueError("draft_to_final.selection contains a duplicate or out-of-range candidate number.")
    return {
        "enabled": enabled,
        "candidate_count": candidate_count,
        "draft_quality": draft_quality,
        "selection_mode": selection_mode,
        "selection": selection,
        "include_drafts": bool(value.get("include_drafts", True)),
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
                "region_fidelity": ((item.get("quality") or {}).get("vision") or {}).get("region_fidelity", []),
                "evidence_note": (item.get("quality") or {}).get("evidence_note"),
            }
        )
    return {
        "job_id": manifest["job_id"],
        "fidelity_mode": spec.get("fidelity_mode"),
        "reference_assets": spec.get("reference_assets", []),
        "identity_packs": spec.get("identity_packs", []),
        "product_truth_map": spec.get("product_truth_map", {"enabled": False, "regions": []}),
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
