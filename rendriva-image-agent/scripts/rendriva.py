#!/usr/bin/env python3
"""Rendriva: separate-image batch generation with professional QA and repair."""

from __future__ import annotations

import argparse
import base64
import colorsys
import copy
import hashlib
import io
import json
import mimetypes
import os
import random
import re
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

try:
    from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont, ImageStat
except ImportError:  # pragma: no cover - handled with a focused runtime error
    Image = ImageColor = ImageDraw = ImageFilter = ImageFont = ImageStat = None

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from rendriva_advanced import (  # noqa: E402
    MARKETPLACE_GOALS,
    build_identity_packs,
    create_platform_exports,
    diversity_report,
    image_similarity,
    load_brand_profile,
    normalize_campaign,
    normalize_draft_to_final,
    normalize_diversity,
    normalize_marketplace,
    normalize_model_identity_lock,
    normalize_platform_exports,
    normalize_product_truth_map,
    normalize_reference_assets,
    reference_fidelity_report,
    variation_direction,
)
from rendriva_commerce import (  # noqa: E402
    build_commerce_report,
    build_video_continuity_report,
    commerce_assignment,
    normalize_commerce_suite,
    shot_assignment,
)


VERSION = "1.6.1"
DEFAULT_IMAGE_MODEL = "gpt-image-2"
DEFAULT_JUDGE_MODEL = "gpt-5.5"
VALID_FORMATS = {"png", "jpeg", "webp"}
VALID_QUALITIES = {"low", "medium", "high", "auto"}
VALID_BACKGROUNDS = {"opaque", "transparent", "auto"}
VALID_MODES = {"variations", "scenes"}
VALID_OPERATIONS = {"generate", "edit", "variation"}
VALID_FIDELITY_MODES = {"none", "guided", "strict"}
VALID_STATUSES = {"QUEUED", "GENERATING", "JUDGING", "NEEDS_REPAIR", "REPAIRING", "PASS", "FAILED", "BLOCKED"}

STRICT_ASSET_LOCKS = [
    "source product silhouette, proportions, construction, dimensions, and edge geometry",
    "source material, fabric weave, texture, finish, stitching, folds, print, and color",
    "source logo geometry, lettering, spacing, colors, placement, and aspect ratio",
    "source labels, marks, and identifiers without redraw, recolor, retexture, reshaping, or invented detail",
]

MODEL_IDENTITY_LOCKS = [
    "same individual face identity across every model output",
    "facial proportions, bone structure, eye shape and spacing, eyebrows, nose, lips, jawline, ears, skin tone, and distinguishing features",
    "natural age appearance without face substitution, identity blending, or a lookalike replacement",
]

PRESETS: dict[str, dict[str, Any]] = {
    "product-photography": {
        "direction": "Commercial product photography with accurate materials, controlled studio lighting, credible shadows, clean hierarchy, and marketplace-ready framing.",
        "avoid": ["invented product features", "floating props", "overly cinematic haze", "plastic-looking materials"],
    },
    "apparel-flatlay": {
        "direction": "Premium top-view apparel flat-lay with accurate garment proportions, smooth fabric presentation, coherent folds, and uncluttered styling.",
        "avoid": ["mannequin contours unless requested", "padded-looking chest shapes", "cropped garment edges", "invented straps or pockets"],
    },
    "fashion-model": {
        "direction": "Editorial fashion photography with natural skin texture, believable anatomy, accurate garment construction, restrained retouching, and campaign-ready composition.",
        "avoid": ["plastic skin", "distorted hands", "changed garment details", "unnatural body proportions"],
    },
    "social-ad": {
        "direction": "Conversion-focused social advertisement with a decisive focal point, clear reading order, disciplined spacing, and intentional copy-safe negative space.",
        "avoid": ["generic template layout", "random badges", "decorative clutter", "pseudo-text"],
    },
    "logo-icon": {
        "direction": "Distinct, scalable identity mark with a strong silhouette, restrained geometry, balanced negative space, and professional brand-system logic.",
        "avoid": ["mockup presentation unless requested", "tiny illegible detail", "stock-symbol clichés", "extra wording"],
    },
    "transparent-dtf": {
        "direction": "Print-focused isolated artwork with clean edges, strong silhouette, controlled detail, and transparent background suitable for apparel production.",
        "avoid": ["mockup garment", "background rectangle", "unwanted white halo", "thin fragile detail"],
        "background": "transparent",
        "format": "png",
    },
    "poster-flyer": {
        "direction": "Professional poster art direction with a deliberate grid, strong headline zone, supporting hierarchy, and print-aware spacing.",
        "avoid": ["pseudo-text", "crowded edges", "inconsistent alignment", "generic AI poster ornament"],
    },
    "website-hero": {
        "direction": "Responsive website hero visual with a clear focal subject, usable negative space for interface copy, controlled contrast, and restrained brand-consistent detail.",
        "avoid": ["fake UI controls", "busy text area", "random glow", "generic SaaS illustration"],
    },
    "realistic-mockup": {
        "direction": "Production-realistic mockup with correct perspective, scale, surface interaction, lighting continuity, and believable material behavior.",
        "avoid": ["warped logo", "impossible reflections", "floating product", "inconsistent perspective"],
    },
    "general-creative": {
        "direction": "Professionally art-directed visual with intentional hierarchy, composition, palette, spacing, material logic, and commercial usability.",
        "avoid": ["generic neon gradient", "random floating objects", "excessive glassmorphism", "template-like AI styling"],
    },
}

SCORED_DIMENSIONS = [
    "visual_hierarchy",
    "composition_spacing",
    "brand_consistency",
    "realism_artifact_control",
    "commercial_usability",
    "originality_restraint",
]


class RendrivaError(RuntimeError):
    pass


class ValidationError(RendrivaError):
    pass


class ProviderError(RendrivaError):
    pass


def json_dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def json_load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValidationError(f"Job file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValidationError("The job specification must be a JSON object.")
    return data


def as_string_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValidationError(f"{field} must be a list of non-empty strings.")
    return [item.strip() for item in value]


def validate_size(value: str) -> str:
    if value == "auto":
        return value
    parts = value.lower().split("x")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ValidationError("size must be 'auto' or WIDTHxHEIGHT.")
    width, height = map(int, parts)
    if width <= 0 or height <= 0 or width % 16 or height % 16:
        raise ValidationError("Custom width and height must be positive multiples of 16.")
    ratio = max(width, height) / min(width, height)
    if ratio > 3:
        raise ValidationError("The long-to-short edge ratio must not exceed 3:1.")
    return f"{width}x{height}"


def resolve_paths(items: list[str], base_dir: Path) -> list[str]:
    resolved: list[str] = []
    for item in items:
        candidate = Path(item).expanduser()
        if not candidate.is_absolute():
            candidate = (base_dir / candidate).resolve()
        if not candidate.is_file():
            raise ValidationError(f"Reference image not found: {item}")
        resolved.append(str(candidate))
    return resolved


def _rgb_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def _color_distance(left: tuple[int, int, int], right: tuple[int, int, int]) -> float:
    return sum((a - b) ** 2 for a, b in zip(left, right)) ** 0.5


def _color_saturation(rgb: tuple[int, int, int]) -> float:
    return colorsys.rgb_to_hsv(*(channel / 255 for channel in rgb))[1]


def _svg_color_candidates(path: Path) -> list[tuple[float, tuple[int, int, int]]]:
    source = path.read_text(encoding="utf-8", errors="replace")
    colors: list[tuple[int, int, int]] = []
    for value in re.findall(r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?\b", source):
        digits = value[1:]
        if len(digits) == 3:
            digits = "".join(character * 2 for character in digits)
        colors.append(tuple(int(digits[index : index + 2], 16) for index in (0, 2, 4)))
    for groups in re.findall(r"rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)", source):
        colors.append(tuple(min(255, int(value)) for value in groups))
    unique = list(dict.fromkeys(colors))
    if not unique:
        raise ValidationError(f"No usable colors were found in SVG reference: {path}")
    weight = 1.0 / len(unique)
    return [(weight, color) for color in unique]


def _raster_color_candidates(path: Path) -> list[tuple[float, tuple[int, int, int]]]:
    if Image is None:
        raise ValidationError("Pillow is required to extract a palette from raster reference images.")
    try:
        with Image.open(path) as source:
            image = source.convert("RGBA")
            image.thumbnail((160, 160), Image.Resampling.LANCZOS)
            data = image.tobytes()
            pixels = [
                (data[index], data[index + 1], data[index + 2])
                for index in range(0, len(data), 4)
                if data[index + 3] >= 48
            ]
    except Exception as exc:
        raise ValidationError(f"Could not extract a color palette from reference image {path}: {exc}") from exc
    if not pixels:
        raise ValidationError(f"Reference image has no visible pixels for palette extraction: {path}")
    sample = Image.new("RGB", (len(pixels), 1))
    sample.putdata(pixels)
    quantized = sample.quantize(colors=min(12, len(set(pixels))), method=Image.Quantize.MEDIANCUT)
    palette = quantized.getpalette() or []
    counts = sorted(quantized.getcolors(maxcolors=256) or [], reverse=True)
    total = sum(count for count, _index in counts) or 1
    candidates: list[tuple[float, tuple[int, int, int]]] = []
    for count, index in counts:
        offset = index * 3
        rgb = tuple(palette[offset : offset + 3])
        if len(rgb) == 3:
            candidates.append((count / total, rgb))
    return candidates


def extract_reference_palette(paths: list[str], max_colors: int = 5) -> list[str]:
    candidates: list[tuple[float, tuple[int, int, int]]] = []
    for source_index, value in enumerate(paths):
        path = Path(value)
        source_candidates = (
            _svg_color_candidates(path) if path.suffix.lower() == ".svg" else _raster_color_candidates(path)
        )
        source_weight = 1.25 if source_index == 0 else 1.0
        candidates.extend((weight * source_weight, rgb) for weight, rgb in source_candidates)
    ranked = sorted(
        candidates,
        key=lambda item: (item[0] * (1 + 0.5 * _color_saturation(item[1]))),
        reverse=True,
    )
    chromatic = [item for item in ranked if _color_saturation(item[1]) >= 0.12]
    neutral = [item for item in ranked if _color_saturation(item[1]) < 0.12]
    selected: list[tuple[int, int, int]] = []
    for _weight, rgb in chromatic + neutral:
        if all(_color_distance(rgb, existing) >= 28 for existing in selected):
            selected.append(rgb)
        if len(selected) == max_colors:
            break
    if not selected and ranked:
        selected.append(ranked[0][1])
    return [_rgb_hex(rgb) for rgb in selected]


def normalize_brand(
    value: Any,
    base_dir: Path,
    reference_assets: list[dict[str, Any]],
    locked_layers: list[dict[str, Any]],
) -> dict[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValidationError("brand must be an object.")
    brand = copy.deepcopy(value)
    explicit_palette = brand.get("palette")
    if explicit_palette is not None:
        if not isinstance(explicit_palette, list) or any(
            not isinstance(item, str) or not item.strip() for item in explicit_palette
        ):
            raise ValidationError("brand.palette must be a list of non-empty color strings.")
        brand["palette"] = [item.strip() for item in explicit_palette]
        brand["palette_source"] = "explicit"
        brand["palette_sources"] = []
        return brand

    configured_sources = as_string_list(brand.get("palette_source_images"), "brand.palette_source_images")
    if configured_sources:
        palette_sources = resolve_paths(configured_sources, base_dir)
    else:
        logo_layers = [layer["path"] for layer in locked_layers if layer["role"] == "logo"]
        other_layers = [layer["path"] for layer in locked_layers if layer["role"] != "logo"]
        eligible_assets = [asset for asset in reference_assets if asset["use_for_palette"]]
        eligible_assets.sort(key=lambda asset: (asset["role"] != "logo", -asset["priority"]))
        palette_sources = list(dict.fromkeys(logo_layers + [asset["path"] for asset in eligible_assets] + other_layers))

    auto_value = brand.get("auto_palette_from_references", bool(palette_sources))
    if not isinstance(auto_value, bool):
        raise ValidationError("brand.auto_palette_from_references must be true or false.")
    max_colors = brand.get("palette_max_colors", 5)
    if isinstance(max_colors, bool) or not isinstance(max_colors, int) or not 1 <= max_colors <= 8:
        raise ValidationError("brand.palette_max_colors must be an integer from 1 through 8.")
    brand["auto_palette_from_references"] = auto_value
    brand["palette_max_colors"] = max_colors
    if auto_value and palette_sources:
        palette = extract_reference_palette(palette_sources, max_colors=max_colors)
        if not palette:
            raise ValidationError("Automatic palette extraction did not find any usable colors.")
        brand["palette"] = palette
        brand["palette_source"] = "auto-reference"
        brand["palette_sources"] = [
            {"path": path, "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}
            for path in palette_sources
        ]
        brand["palette_source_images"] = palette_sources
    else:
        brand["palette_source"] = "none"
        brand["palette_sources"] = []
    return brand


def normalize_locked_layers(value: Any, base_dir: Path) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValidationError("locked_layers must be a list of objects.")
    normalized: list[dict[str, Any]] = []
    for index, raw_layer in enumerate(value, start=1):
        path_value = raw_layer.get("path")
        if not isinstance(path_value, str) or not path_value.strip():
            raise ValidationError(f"locked_layers[{index}].path must be a non-empty string.")
        resolved_path = resolve_paths([path_value], base_dir)[0]
        shadow_value = raw_layer.get("shadow", False)
        if isinstance(shadow_value, bool):
            shadow = {"enabled": shadow_value}
        elif isinstance(shadow_value, dict):
            shadow = copy.deepcopy(shadow_value)
            shadow["enabled"] = bool(shadow.get("enabled", True))
        else:
            raise ValidationError(f"locked_layers[{index}].shadow must be a boolean or object.")
        shadow.update(
            {
                "opacity": int(shadow.get("opacity", 75)),
                "blur": int(shadow.get("blur", 24)),
                "offset_x": int(shadow.get("offset_x", 12)),
                "offset_y": int(shadow.get("offset_y", 20)),
                "color": str(shadow.get("color", "#000000")),
            }
        )
        if not 0 <= shadow["opacity"] <= 255 or not 0 <= shadow["blur"] <= 100:
            raise ValidationError(f"locked_layers[{index}].shadow opacity must be 0..255 and blur must be 0..100.")
        layer = {
            "path": resolved_path,
            "role": str(raw_layer.get("role", "product")),
            "x": float(raw_layer.get("x", 0.5)),
            "y": float(raw_layer.get("y", 0.5)),
            "max_width": float(raw_layer.get("max_width", 0.45)),
            "max_height": float(raw_layer.get("max_height", 0.75)),
            "anchor": raw_layer.get("anchor", "center"),
            "require_alpha": bool(raw_layer.get("require_alpha", True)),
            "auto_cutout": bool(raw_layer.get("auto_cutout", False)),
            "cutout_tolerance": int(raw_layer.get("cutout_tolerance", 28)),
            "edge_softness": int(raw_layer.get("edge_softness", 4)),
            "shadow": shadow,
        }
        if layer["role"] not in {"product", "logo", "artwork", "identity", "protected-asset"}:
            raise ValidationError(
                f"locked_layers[{index}].role must be product, logo, artwork, identity, or protected-asset."
            )
        for key in ("x", "y", "max_width", "max_height"):
            if not 0 <= layer[key] <= 1:
                raise ValidationError(f"locked_layers[{index}].{key} must be from 0 through 1.")
        if layer["max_width"] == 0 or layer["max_height"] == 0:
            raise ValidationError(f"locked_layers[{index}] maximum dimensions must be greater than zero.")
        if layer["anchor"] not in {"top-left", "top-center", "center", "bottom-center", "bottom-right"}:
            raise ValidationError(
                f"locked_layers[{index}].anchor must be top-left, top-center, center, bottom-center, or bottom-right."
            )
        if not 1 <= layer["cutout_tolerance"] <= 120:
            raise ValidationError(f"locked_layers[{index}].cutout_tolerance must be from 1 through 120.")
        if not 0 <= layer["edge_softness"] <= 20:
            raise ValidationError(f"locked_layers[{index}].edge_softness must be from 0 through 20.")
        if layer["require_alpha"] and layer["auto_cutout"]:
            raise ValidationError(f"locked_layers[{index}] cannot require alpha and request auto_cutout at the same time.")
        normalized.append(layer)
    return normalized


def normalize_job(raw: dict[str, Any], base_dir: Path | None = None) -> dict[str, Any]:
    base_dir = (base_dir or Path.cwd()).resolve()
    prompt = raw.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValidationError("prompt is required and must be a non-empty string.")

    scenes = as_string_list(raw.get("scenes"), "scenes")
    mode = raw.get("mode", "scenes" if scenes else "variations")
    if mode not in VALID_MODES:
        raise ValidationError(f"mode must be one of: {', '.join(sorted(VALID_MODES))}.")
    if mode == "scenes" and not scenes:
        raise ValidationError("scenes mode requires at least one scene.")
    if mode == "variations" and scenes:
        raise ValidationError("scenes can only be used with mode='scenes'.")
    count = raw.get("count", len(scenes) if scenes else 1)
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 10:
        raise ValidationError("count must be an integer from 1 through 10.")
    if scenes and len(scenes) != count:
        raise ValidationError("count must equal the number of scenes.")

    preset = raw.get("preset", "general-creative")
    if preset not in PRESETS:
        raise ValidationError(f"Unknown preset '{preset}'. Choose one of: {', '.join(PRESETS)}.")
    preset_defaults = PRESETS[preset]
    has_references = bool(raw.get("reference_images") or raw.get("reference_assets"))
    operation = raw.get("operation", "edit" if has_references else "generate")
    if operation not in VALID_OPERATIONS:
        raise ValidationError(f"operation must be one of: {', '.join(sorted(VALID_OPERATIONS))}.")

    output_format = raw.get("format", preset_defaults.get("format", "png"))
    if output_format == "jpg":
        output_format = "jpeg"
    if output_format not in VALID_FORMATS:
        raise ValidationError(f"format must be one of: {', '.join(sorted(VALID_FORMATS))}.")
    quality = raw.get("quality", "high")
    if quality not in VALID_QUALITIES:
        raise ValidationError(f"quality must be one of: {', '.join(sorted(VALID_QUALITIES))}.")
    background = raw.get("background", preset_defaults.get("background", "opaque"))
    if background not in VALID_BACKGROUNDS:
        raise ValidationError(f"background must be one of: {', '.join(sorted(VALID_BACKGROUNDS))}.")
    if background == "transparent" and output_format not in {"png", "webp"}:
        raise ValidationError("Transparent output requires PNG or WebP.")

    try:
        reference_assets = normalize_reference_assets(raw.get("reference_images"), raw.get("reference_assets"), base_dir)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    references = [asset["path"] for asset in reference_assets]
    locked_layers = normalize_locked_layers(raw.get("locked_layers"), base_dir)
    if references and locked_layers:
        raise ValidationError("Use either reference assets for generative fidelity or locked_layers for literal compositing, not both.")
    if locked_layers and operation != "generate":
        raise ValidationError("locked_layers require operation='generate' because source pixels are composited after generation.")
    if operation in {"edit", "variation"} and not references:
        raise ValidationError(f"operation='{operation}' requires at least one reference image.")

    try:
        model_identity_lock = normalize_model_identity_lock(raw.get("model_identity_lock"), prompt.strip(), preset, count, reference_assets, base_dir)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    if model_identity_lock["enabled"] and model_identity_lock.get("source_path"):
        for asset in reference_assets:
            if asset["path"] == model_identity_lock["source_path"] and asset["role"] == "general":
                asset["role"] = "model"
                asset["role_source"] = "request-inference"
                asset["use_for_palette"] = False
                asset["preserve"] = list(dict.fromkeys(asset["preserve"] + MODEL_IDENTITY_LOCKS))

    identity_config = raw.get("product_identity", {})
    if identity_config is None:
        identity_config = {}
    if not isinstance(identity_config, dict):
        raise ValidationError("product_identity must be an object.")
    required_views = as_string_list(identity_config.get("required_views"), "product_identity.required_views")
    try:
        identity_packs = build_identity_packs(reference_assets, required_views)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    if required_views and not identity_packs:
        raise ValidationError("product_identity.required_views requires at least one product or identity reference asset.")
    missing_views = sorted({view for pack in identity_packs for view in pack["missing_required_views"]})
    if missing_views:
        raise ValidationError(f"The product identity pack is missing required source views: {', '.join(missing_views)}.")

    protected_product_reference = any(asset["role"] in {"product", "logo", "identity", "general"} for asset in reference_assets)
    protected_model_reference = any(asset["role"] == "model" for asset in reference_assets)
    protected_reference = protected_product_reference or protected_model_reference
    default_fidelity = "strict" if protected_reference or locked_layers else "guided" if references else "none"
    fidelity_mode = str(raw.get("fidelity_mode", default_fidelity))
    if fidelity_mode not in VALID_FIDELITY_MODES:
        raise ValidationError(f"fidelity_mode must be one of: {', '.join(sorted(VALID_FIDELITY_MODES))}.")
    if fidelity_mode != "none" and not references and not locked_layers:
        raise ValidationError("fidelity_mode requires reference assets or locked_layers.")
    preserve = as_string_list(raw.get("preserve"), "preserve")
    preserve += [lock for asset in reference_assets for lock in asset["preserve"]]
    if fidelity_mode == "strict" and (protected_product_reference or locked_layers):
        preserve = list(dict.fromkeys(preserve + STRICT_ASSET_LOCKS))
    if fidelity_mode == "strict" and protected_model_reference:
        preserve = list(dict.fromkeys(preserve + MODEL_IDENTITY_LOCKS))

    try:
        profile, profile_evidence = load_brand_profile(raw.get("brand_profile"), base_dir)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    raw_brand = raw.get("brand", {})
    if raw_brand is None:
        raw_brand = {}
    if not isinstance(raw_brand, dict):
        raise ValidationError("brand must be an object.")
    merged_brand = {**profile, **copy.deepcopy(raw_brand)}
    brand = normalize_brand(merged_brand, base_dir, reference_assets, locked_layers)
    if profile_evidence:
        brand["profile"] = profile_evidence

    try:
        marketplace = normalize_marketplace(raw.get("marketplace", raw.get("marketplace_goal")))
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    text_layers = copy.deepcopy(raw.get("text_layers", []))
    if not isinstance(text_layers, list) or any(not isinstance(item, dict) for item in text_layers):
        raise ValidationError("text_layers must be a list of objects.")
    if marketplace["enabled"] and marketplace["auto_text_layers"] and marketplace["exact_copy"]:
        positions = {"badge": (0.08, 0.08, 34), "discount": (0.08, 0.15, 44), "price": (0.08, 0.24, 70), "original_price": (0.08, 0.34, 32), "cta": (0.08, 0.42, 34), "bundle_count": (0.08, 0.50, 34)}
        existing = {layer.get("text") for layer in text_layers}
        for field, text_value in marketplace["exact_copy"].items():
            if text_value not in existing:
                x, y, size = positions.get(field, (0.08, 0.58, 32))
                text_layers.append({"text": text_value, "x": x, "y": y, "max_width": 0.52, "max_height": 0.12, "font_size": size, "color": "auto", "style": "price" if field == "price" else "badge" if field in {"badge", "discount"} else "body"})
    for index, layer in enumerate(text_layers, start=1):
        if layer.get("font_path"):
            font_path = Path(str(layer["font_path"])).expanduser()
            if not font_path.is_absolute():
                font_path = (base_dir / font_path).resolve()
            if not font_path.is_file():
                raise ValidationError(f"text_layers[{index}].font_path not found: {layer['font_path']}")
            layer["font_path"] = str(font_path)

    try:
        diversity = normalize_diversity(raw.get("diversity"), count)
        campaign = normalize_campaign(raw.get("campaign"), brand, count)
        platform_exports = normalize_platform_exports(raw.get("platform_exports"))
        product_truth_map = normalize_product_truth_map(raw.get("product_truth_map"), reference_assets, locked_layers, base_dir)
        draft_to_final = normalize_draft_to_final(raw.get("draft_to_final"), count)
        commerce = normalize_commerce_suite(raw, reference_assets, count, preset, base_dir)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc

    max_repairs = raw.get("max_repair_attempts", 1)
    if isinstance(max_repairs, bool) or not isinstance(max_repairs, int) or not 0 <= max_repairs <= 3:
        raise ValidationError("max_repair_attempts must be an integer from 0 through 3.")
    threshold = raw.get("min_professional_score", 4.0)
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0 <= float(threshold) <= 5:
        raise ValidationError("min_professional_score must be from 0 through 5.")
    concurrency = raw.get("concurrency", 2)
    if isinstance(concurrency, bool) or not isinstance(concurrency, int) or not 1 <= concurrency <= 4:
        raise ValidationError("concurrency must be an integer from 1 through 4.")

    image_model = str(raw.get("image_model", os.environ.get("RENDRIVA_IMAGE_MODEL", DEFAULT_IMAGE_MODEL)))
    normalized_size = validate_size(str(raw.get("size", "1024x1024")))
    if image_model.startswith("gpt-image-2") and normalized_size != "auto":
        width, height = map(int, normalized_size.split("x"))
        pixels = width * height
        if max(width, height) > 3840 or not 655_360 <= pixels <= 8_294_400:
            raise ValidationError("gpt-image-2 custom sizes require a maximum edge of 3840px and 655,360 through 8,294,400 total pixels.")

    normalized = {
        "prompt": prompt.strip(), "count": count, "mode": mode, "scenes": scenes,
        "operation": operation, "preset": preset, "size": normalized_size, "quality": quality,
        "format": output_format, "background": background, "reference_images": references,
        "reference_assets": reference_assets, "identity_packs": identity_packs,
        "locked_layers": locked_layers, "protected_reference": protected_reference,
        "protected_product_reference": protected_product_reference,
        "protected_model_reference": protected_model_reference,
        "fidelity_mode": fidelity_mode, "preserve": preserve,
        "avoid": as_string_list(raw.get("avoid"), "avoid"), "brand": brand,
        "campaign": campaign, "diversity": diversity, "platform_exports": platform_exports,
        "marketplace": marketplace, "product_truth_map": product_truth_map,
        "draft_to_final": draft_to_final, "model_identity_lock": model_identity_lock,
        **commerce,
        "professional_designer_mode": bool(raw.get("professional_designer_mode", True)),
        "text_safe_mode": bool(raw.get("text_safe_mode", bool(text_layers))), "text_layers": text_layers,
        "max_repair_attempts": max_repairs, "min_professional_score": float(threshold),
        "image_model": image_model,
        "judge_model": str(raw.get("judge_model", os.environ.get("RENDRIVA_JUDGE_MODEL", DEFAULT_JUDGE_MODEL))),
        "concurrency": concurrency,
    }
    validate_text_layers(normalized["text_layers"])
    return normalized


def validate_text_layers(layers: list[dict[str, Any]]) -> None:
    for index, layer in enumerate(layers, start=1):
        text = layer.get("text")
        if not isinstance(text, str) or not text:
            raise ValidationError(f"text_layers[{index}].text must be a non-empty string.")
        for key in ("x", "y", "max_width", "max_height"):
            if key in layer:
                value = layer[key]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= float(value) <= 1:
                    raise ValidationError(f"text_layers[{index}].{key} must be from 0 through 1.")
        if layer.get("align", "left") not in {"left", "center", "right"}:
            raise ValidationError(f"text_layers[{index}].align must be left, center, or right.")
        font_size = layer.get("font_size", 64)
        if font_size != "auto" and (isinstance(font_size, bool) or not isinstance(font_size, int) or font_size <= 0):
            raise ValidationError(f"text_layers[{index}].font_size must be 'auto' or a positive integer.")
        if layer.get("style", "body") not in {"headline", "subheadline", "body", "price", "badge", "caption"}:
            raise ValidationError(f"text_layers[{index}].style is not supported.")
        color = str(layer.get("color", "#111111"))
        if color != "auto":
            try:
                ImageColor.getcolor(color, "RGBA")
            except (ValueError, TypeError) as exc:
                raise ValidationError(f"text_layers[{index}].color must be 'auto' or a valid color.") from exc
        x, y = float(layer.get("x", 0.08)), float(layer.get("y", 0.08))
        max_width, max_height = float(layer.get("max_width", 0.84)), float(layer.get("max_height", 0.30))
        if x + max_width > 1 or y + max_height > 1:
            raise ValidationError(f"text_layers[{index}] text zone must fit fully inside the canvas.")


def stable_job_id(spec: dict[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    return digest[:16]


def extension_for(output_format: str) -> str:
    return "jpg" if output_format == "jpeg" else output_format


def build_plan(spec: dict[str, Any]) -> list[dict[str, Any]]:
    extension = extension_for(spec["format"])
    plan: list[dict[str, Any]] = []
    for zero_index in range(spec["count"]):
        scene = spec["scenes"][zero_index] if spec["mode"] == "scenes" else None
        sku_assignment = commerce_assignment(spec, zero_index + 1)
        shot = shot_assignment(spec, zero_index + 1)
        plan.append(
            {
                "index": zero_index + 1,
                "scene": scene,
                "file": f"image-{zero_index + 1:02d}.{extension}",
                "status": "QUEUED",
                "attempts": 0,
                "repair_attempts": 0,
                "quality": None,
                "error": None,
                "variation_direction": variation_direction(zero_index + 1, spec["diversity"]["axes"]),
                "campaign_signature": spec["campaign"]["signature"],
                "sku_assignment": sku_assignment,
                "shot": shot,
            }
        )
    return plan


def _join(values: list[str]) -> str:
    return "; ".join(values) if values else "none supplied"


def compile_prompt(spec: dict[str, Any], item: dict[str, Any], repair: dict[str, Any] | None = None) -> str:
    preset = PRESETS[spec["preset"]]
    brand = spec["brand"]
    brand_lines = []
    if brand.get("palette"):
        brand_lines.append(f"palette: {_join([str(value) for value in brand['palette']])}")
    if brand.get("palette_source") == "auto-reference":
        brand_lines.append("palette source: automatically extracted from the supplied authoritative reference images")
    if brand.get("tone"):
        brand_lines.append(f"tone: {brand['tone']}")
    if brand.get("fonts"):
        brand_lines.append(f"font direction: {_join([str(value) for value in brand['fonts']])}")
    avoid = list(preset.get("avoid", [])) + spec["avoid"] + as_string_list(brand.get("avoid"), "brand.avoid")
    if spec["professional_designer_mode"]:
        avoid += [
            "generic AI-template appearance",
            "meaningless pseudo-text",
            "unmotivated neon glow or floating particles",
            "plastic materials or artificial HDR",
            "clutter without hierarchy",
        ]
    text_rule = (
        "Do not render any letters, words, prices, labels, or pseudo-text. Reserve clean intentional negative space for exact typography that will be applied later."
        if spec["text_safe_mode"]
        else "Render text only when the brief explicitly requires it; keep all required text exact, legible, and undistorted."
    )
    locked_layer_rule = "No source-composited layer is configured."
    if spec["locked_layers"]:
        placements = [
            f"{layer['role']} layer {index}: reserve a clean unobstructed zone around ({layer['x']:.2f}, {layer['y']:.2f}) with maximum width {layer['max_width']:.2f} and height {layer['max_height']:.2f}"
            for index, layer in enumerate(spec["locked_layers"], start=1)
        ]
        locked_layer_rule = (
            "Do not draw, imitate, duplicate, silhouette, or partially render the locked source product. "
            "Generate only the surrounding background, staging, lighting environment, and layout. "
            "The unchanged source-derived layer will be composited afterward. "
            + "; ".join(placements)
            + ". Keep every reserved zone free of conflicting objects, fake shadows, or duplicate products."
        )
    fidelity_rule = "No source asset is supplied."
    if spec["fidelity_mode"] == "guided":
        fidelity_rule = (
            "Use the source as a visual reference, but treat fidelity as guided rather than literal. "
            "Do not claim pixel-identical preservation."
        )
    elif spec["fidelity_mode"] == "strict":
        if spec["protected_product_reference"] or spec["locked_layers"]:
            strategy = "literal source compositing" if spec["locked_layers"] else "strict reference editing with mandatory comparison QA"
            fidelity_rule = (
                f"Use {strategy}. The supplied product, garment, artwork, or logo is authoritative and protected. "
                "Do not redraw, reinterpret, retouch, recolor, reshape, restyle, retexture, smooth, sharpen, replace, or invent any protected detail. "
                "Keep fabric weave, fibers, stitching, seams, folds, finish, print, color, silhouette, proportions, labels, and product construction unchanged. "
                "Keep every logo's exact symbol, lettering, geometry, spacing, color, aspect ratio, and placement unchanged; never approximate it with generated text. "
                "If the requested transformation conflicts with a protected asset, preserve the asset and modify only the background, staging, lighting environment, layout, or unprotected region."
            )
        elif spec["protected_model_reference"]:
            fidelity_rule = (
                "Use strict face-identity reference editing. Preserve the same individual person's facial proportions, bone structure, eyes, eyebrows, nose, lips, jawline, ears, skin tone, distinguishing features, and natural age appearance. "
                "Do not copy the reference clothing or background unless requested; the model source controls facial identity only."
            )
        else:
            fidelity_rule = "Apply strict role scoping: each non-product reference controls only its declared style, layout, lighting, background, typography, or palette dimension. No product or logo lock is implied."
    reference_intelligence = "No reference assets supplied."
    if spec["reference_assets"]:
        role_lines = [
            f"{Path(asset['path']).name}: role={asset['role']}, view={asset['view']}, identity={asset['identity_id'] or 'not-applicable'}, role-source={asset['role_source']}"
            for asset in spec["reference_assets"]
        ]
        reference_intelligence = (
            "Use each reference only for its assigned role. Product/identity references control protected product identity; model references control face identity only; logo references control exact brand marks; "
            "style/layout/lighting/background/typography references guide only that named dimension. Never let a style reference overwrite product construction or logo geometry.\n"
            + "\n".join(role_lines)
        )
    identity_intelligence = "No multi-view identity pack supplied."
    if spec["identity_packs"]:
        identity_intelligence = "\n".join(
            f"Identity {pack['identity_id']} ({pack['identity_signature']}): reconcile views {', '.join(pack['views'])} as one unchanged product; never blend it with another identity."
            for pack in spec["identity_packs"]
        )
    campaign = spec["campaign"]
    campaign_rule = (
        f"Campaign {campaign['id']} uses {campaign['consistency']} consistency and token signature {campaign['signature']}. "
        f"Keep these fixed across outputs: {json.dumps(campaign['tokens'], ensure_ascii=False, sort_keys=True)}."
        if campaign["enabled"] else "No cross-output campaign lock is required."
    )
    marketplace = spec["marketplace"]
    marketplace_rule = "Marketplace conversion mode is disabled."
    if marketplace["enabled"]:
        marketplace_rule = (
            f"Platform: {marketplace['platform']}; goal: {marketplace['goal']}. {marketplace['direction']} "
            f"Exact supplied copy only: {json.dumps(marketplace['exact_copy'], ensure_ascii=False, sort_keys=True)}. "
            f"Allowed supplied claims only: {_join(marketplace['claims'])}. Never invent a price, discount, bundle item, rating, guarantee, urgency claim, badge, credential, or CTA."
        )
    truth_map = spec["product_truth_map"]
    truth_rule = "No product truth regions are configured."
    if truth_map["enabled"]:
        truth_rule = "\n".join(
            f"{region['name']}: role={region['role']}; required={region['required']}; source={Path(region['source_path']).name}; bbox={region['bbox']}; preserve={_join(region['preserve'])}; strategy={region['comparison_strategy']}"
            for region in truth_map["regions"]
        )
        truth_rule = (
            "Treat every named region as an independent preservation contract. Never compensate for a failed logo, print, fabric, texture, stitching, label, color, construction, silhouette, material, or identity region with a visually attractive redesign.\n"
            + truth_rule
        )
    model_identity = spec["model_identity_lock"]
    model_identity_rule = "Single Model Face Lock is disabled."
    if model_identity["enabled"]:
        if model_identity.get("source_path"):
            model_identity_rule = (
                f"The attached model identity anchor {Path(model_identity['source_path']).name} is authoritative. Use the same individual person and face in this output. "
                f"Preserve: {_join(model_identity['preserve'])}. Do not substitute a lookalike, blend identities, change ethnicity or apparent age, or generate a different model. "
                "Pose, expression, outfit, camera, lighting, and scene may vary only when the person's recognizable facial identity remains unchanged."
            )
        else:
            model_identity_rule = (
                "Establish exactly one clear, natural, professionally photographed model face for this first approved output. "
                "This person will become the authoritative identity anchor for every later variant, so avoid an obscured, profile-only, cropped, duplicated, or ambiguous face."
            )
    assignment = item.get("sku_assignment") or commerce_assignment(spec, item["index"])
    sku_rule = "SKU Variant Matrix is disabled."
    if assignment:
        variant = next(entry for entry in spec["sku_variant_matrix"]["variants"] if entry["id"] == assignment["variant_id"])
        sku_rule = (
            f"Use only SKU {variant['id']} ({variant['label']}) from {Path(variant['source_path']).name}; source fingerprint {variant['source_sha256']}; expected color {variant['expected_color']}. "
            f"Preserve {_join(variant['preserve'])}. Do not swap, blend, duplicate, recolor, or borrow construction from another variant."
        )
    garment = spec["garment_construction_lock"]
    garment_rule = (
        f"Lock {_join(garment['fields'])}; do not invent unseen construction. "
        + ("For flat-lays keep the chest completely flat with no padded, molded, mannequin, cup, or body contour." if garment["flat_chest_when_flatlay"] else "")
        if garment["enabled"] else "Garment Construction Lock is disabled."
    )
    visibility = spec["product_visibility_guard"]
    visibility_rule = (
        f"Keep at least {visibility['min_visible_ratio']:.0%} of the product visibly judgeable. Protect {_join(visibility['protected_details'])} from {_join(visibility['blockers'])}."
        if visibility["enabled"] else "Product Visibility Guard is disabled."
    )
    color_guard = spec["sku_color_guard"]
    color_rule = (
        f"Preserve exact SKU color independently from scene lighting; target ΔE within {color_guard['delta_e_tolerance']:.1f}. Never bake lighting color into product identity."
        if color_guard["enabled"] else "SKU Color Guard is disabled."
    )
    shot_rule = f"Required shot role: {item.get('shot')}. Complete this coverage role without violating identity or product locks." if item.get("shot") else "No formal shot-list role is assigned."
    defect_entries = spec["defect_memory"].get("entries", [])
    defect_rule = f"Do not repeat previously rejected campaign defects: {_join(defect_entries)}." if defect_entries else "No prior campaign defects have been recorded."
    if item.get("batch_variations"):
        scene = f"Create {spec['count']} independent professional variations across the provider response. Every returned item must remain one standalone image."
        if spec["diversity"]["enabled"]:
            directions = [f"output {index}: {variation_direction(index, spec['diversity']['axes'])}" for index in range(1, spec["count"] + 1)]
            scene += " Use these ordered diversity directions: " + "; ".join(directions)
    else:
        scene = item.get("scene") or f"Create an independent professional variation {item['index']} of {spec['count']}."
        if spec["diversity"]["enabled"]:
            scene += f" Mandatory diversity direction: {item['variation_direction']}."
    if item.get("selected_draft_file"):
        scene += (
            f" Promote the supplied selected draft {item['selected_draft_file']} into final production quality. Preserve its approved composition, camera, hierarchy, and negative-space plan, "
            "but re-render materials, edges, lighting, and finishing at final quality while all source and truth-region locks remain authoritative."
        )
    if item.get("draft_candidate"):
        scene += " This is a low-cost draft candidate for composition selection, not the final production render. Prioritize clear hierarchy and a distinct usable concept."
    repair_block = ""
    if repair:
        defects = repair.get("defects") or [repair.get("reason", "The previous output failed quality review.")]
        repair_block = (
            "\nREPAIR DIRECTIVE:\n"
            f"Correct these observed defects: {_join([str(value) for value in defects])}.\n"
            f"Use this targeted correction: {repair.get('repair_prompt', 'Correct the defects without changing locked details.')}\n"
        )
        if spec["localized_repair"]["enabled"]:
            repair_block += (
                f"Repair only the smallest affected zone among {_join(spec['localized_repair']['zones'])}. Preserve every passing region. "
                "Reject and roll back the repair if face, SKU, garment construction, product color, fabric, texture, print, logo, or typography locks drift.\n"
            )
    return f"""Create ONE standalone image file for this single batch item.

STRICT OUTPUT RULE:
This request is for exactly one standalone image. Do not create a collage, grid, contact sheet, storyboard, diptych, split screen, before-and-after panel, multi-frame layout, or multiple alternatives inside the canvas. Do not print the batch index.

CORE REQUEST:
{spec['prompt']}

THIS IMAGE'S SCENE:
{scene}

PROFESSIONAL ART DIRECTION:
{preset['direction']}
Build an intentional focal point and reading order. Use a disciplined grid, credible alignment, balanced spacing, useful negative space, controlled palette, coherent lighting and perspective, believable materials, and commercial-ready finishing. Make the result feel deliberately art-directed by an experienced professional designer rather than like a generic AI default.

BRAND DIRECTION:
{'; '.join(brand_lines) if brand_lines else 'No separate brand kit supplied; use a restrained, purpose-led visual system.'}
Apply the brand palette to unprotected backgrounds, accents, typography, graphic shapes, and layout styling. Use dominant colors intentionally with suitable neutral support and readable contrast. Never recolor, tint, or alter a protected product, garment, artwork, or logo to force it into the palette.

REFERENCE LOCKS:
Preserve these details exactly where applicable: {_join(spec['preserve'])}.
Do not invent product features, logos, wording, accessories, achievements, or brand details.

REFERENCE FIDELITY POLICY ({spec['fidelity_mode'].upper()}):
{fidelity_rule}

REFERENCE INTELLIGENCE:
{reference_intelligence}

MULTI-VIEW PRODUCT IDENTITY:
{identity_intelligence}

SINGLE MODEL FACE LOCK:
{model_identity_rule}

SKU VARIANT MATRIX:
{sku_rule}

GARMENT CONSTRUCTION LOCK:
{garment_rule}

PRODUCT VISIBILITY GUARD:
{visibility_rule}

SKU COLOR GUARD:
{color_rule}

SHOT DIRECTOR:
{shot_rule}

CAMPAIGN DEFECT MEMORY:
{defect_rule}

PRODUCT REGION TRUTH MAP:
{truth_rule}

CAMPAIGN CONSISTENCY:
{campaign_rule}

MARKETPLACE CONVERSION MODE:
{marketplace_rule}

SOURCE COMPOSITE POLICY:
{locked_layer_rule}

TEXT POLICY:
{text_rule}

PROHIBITED ELEMENTS:
{_join(avoid)}.

OUTPUT:
Size {spec['size']}; quality {spec['quality']}; format {spec['format']}; background {spec['background']}.
{repair_block}""".strip()


def api_request(
    url: str,
    api_key: str,
    *,
    json_body: dict[str, Any] | None = None,
    raw_body: bytes | None = None,
    content_type: str = "application/json",
    attempts: int = 4,
    timeout: int = 240,
) -> dict[str, Any]:
    if (json_body is None) == (raw_body is None):
        raise ValueError("Provide exactly one request body.")
    body = json.dumps(json_body).encode() if json_body is not None else raw_body
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": content_type,
        "User-Agent": f"rendriva/{VERSION}",
    }
    last_error: Exception | None = None
    for attempt in range(attempts):
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            payload = exc.read().decode("utf-8", errors="replace")
            last_error = ProviderError(f"OpenAI API returned HTTP {exc.code}: {payload[:1200]}")
            if exc.code not in {408, 409, 429, 500, 502, 503, 504} or attempt == attempts - 1:
                raise last_error from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = ProviderError(f"OpenAI API request failed: {exc}")
            if attempt == attempts - 1:
                raise last_error from exc
        time.sleep(min(2**attempt + random.random(), 12))
    raise ProviderError(str(last_error))


def multipart_body(fields: dict[str, str], files: list[tuple[str, Path]]) -> tuple[bytes, str]:
    boundary = f"----rendriva-{uuid.uuid4().hex}"
    body = io.BytesIO()
    for name, value in fields.items():
        body.write(f"--{boundary}\r\n".encode())
        body.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.write(str(value).encode())
        body.write(b"\r\n")
    for field_name, path in files:
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body.write(f"--{boundary}\r\n".encode())
        body.write(f'Content-Disposition: form-data; name="{field_name}"; filename="{path.name}"\r\n'.encode())
        body.write(f"Content-Type: {content_type}\r\n\r\n".encode())
        body.write(path.read_bytes())
        body.write(b"\r\n")
    body.write(f"--{boundary}--\r\n".encode())
    return body.getvalue(), f"multipart/form-data; boundary={boundary}"


def extract_images(payload: dict[str, Any]) -> list[bytes]:
    images: list[bytes] = []
    for item in payload.get("data", []):
        encoded = item.get("b64_json")
        if encoded:
            images.append(base64.b64decode(encoded))
    if not images:
        raise ProviderError("The image API response did not contain image data.")
    return images


class OpenAIProvider:
    def __init__(self, api_key: str):
        if not api_key:
            raise ProviderError("OPENAI_API_KEY is required for a real generation run.")
        self.api_key = api_key

    def generate(self, spec: dict[str, Any], prompt: str, n: int = 1) -> list[bytes]:
        fields: dict[str, Any] = {
            "model": spec["image_model"],
            "prompt": prompt,
            "n": n,
            "quality": spec["quality"],
            "size": spec["size"],
            "output_format": spec["format"],
            "background": spec["background"],
        }
        payload = api_request("https://api.openai.com/v1/images/generations", self.api_key, json_body=fields)
        images = extract_images(payload)
        if len(images) != n:
            raise ProviderError(f"Requested {n} images but the API returned {len(images)}.")
        return images

    def edit(self, spec: dict[str, Any], prompt: str, n: int = 1) -> list[bytes]:
        fields = {
            "model": spec["image_model"],
            "prompt": prompt,
            "n": str(n),
            "quality": spec["quality"],
            "size": spec["size"],
            "output_format": spec["format"],
            "background": spec["background"],
        }
        files = [("image[]", Path(path)) for path in spec["reference_images"]]
        body, content_type = multipart_body(fields, files)
        payload = api_request(
            "https://api.openai.com/v1/images/edits",
            self.api_key,
            raw_body=body,
            content_type=content_type,
        )
        images = extract_images(payload)
        if len(images) != n:
            raise ProviderError(f"Requested {n} images but the API returned {len(images)}.")
        return images

    def create(self, spec: dict[str, Any], prompt: str, n: int = 1) -> list[bytes]:
        if spec["operation"] in {"edit", "variation"} or spec["reference_images"]:
            return self.edit(spec, prompt, n=n)
        return self.generate(spec, prompt, n=n)

    def promote(self, spec: dict[str, Any], prompt: str, draft_path: Path, n: int = 1) -> list[bytes]:
        fields = {
            "model": spec["image_model"],
            "prompt": prompt,
            "n": str(n),
            "quality": spec["quality"],
            "size": spec["size"],
            "output_format": spec["format"],
            "background": spec["background"],
        }
        files = [("image[]", Path(path)) for path in spec["reference_images"]]
        files.append(("image[]", draft_path))
        body, content_type = multipart_body(fields, files)
        payload = api_request("https://api.openai.com/v1/images/edits", self.api_key, raw_body=body, content_type=content_type)
        images = extract_images(payload)
        if len(images) != n:
            raise ProviderError(f"Requested {n} promoted images but the API returned {len(images)}.")
        return images

    def create_with_identity(
        self,
        spec: dict[str, Any],
        prompt: str,
        identity_path: Path,
        *,
        draft_path: Path | None = None,
        n: int = 1,
    ) -> list[bytes]:
        fields = {
            "model": spec["image_model"],
            "prompt": prompt,
            "n": str(n),
            "quality": spec["quality"],
            "size": spec["size"],
            "output_format": spec["format"],
            "background": spec["background"],
        }
        sources = [identity_path]
        sources.extend(Path(path) for path in spec["reference_images"])
        if draft_path:
            sources.append(draft_path)
        deduplicated = list(dict.fromkeys(str(path.resolve()) for path in sources))
        body, content_type = multipart_body(fields, [("image[]", Path(path)) for path in deduplicated])
        payload = api_request("https://api.openai.com/v1/images/edits", self.api_key, raw_body=body, content_type=content_type)
        images = extract_images(payload)
        if len(images) != n:
            raise ProviderError(f"Requested {n} identity-locked images but the API returned {len(images)}.")
        return images

    def repair(self, spec: dict[str, Any], prompt: str, failed_path: Path, n: int = 1) -> list[bytes]:
        fields = {
            "model": spec["image_model"],
            "prompt": prompt,
            "n": str(n),
            "quality": spec["quality"],
            "size": spec["size"],
            "output_format": spec["format"],
            "background": spec["background"],
        }
        sources = [failed_path]
        identity = spec["model_identity_lock"].get("source_path")
        if identity:
            sources.append(Path(identity))
        sources.extend(Path(path) for path in spec["reference_images"])
        deduplicated = list(dict.fromkeys(str(path.resolve()) for path in sources))
        body, content_type = multipart_body(fields, [("image[]", Path(path)) for path in deduplicated])
        payload = api_request("https://api.openai.com/v1/images/edits", self.api_key, raw_body=body, content_type=content_type)
        images = extract_images(payload)
        if len(images) != n:
            raise ProviderError(f"Requested {n} repaired images but the API returned {len(images)}.")
        return images

    def judge(self, spec: dict[str, Any], image_path: Path, prompt: str) -> dict[str, Any]:
        mime = mimetypes.guess_type(image_path.name)[0] or "image/png"
        image_url = f"data:{mime};base64,{base64.b64encode(image_path.read_bytes()).decode()}"
        schema = quality_schema()
        content: list[dict[str, Any]] = [{"type": "input_text", "text": quality_prompt(spec, prompt)}]
        truth_sources = [region["source_path"] for region in spec["product_truth_map"]["regions"]]
        model_source = spec["model_identity_lock"].get("source_path")
        sku_sources = [assignment["source_path"] for assignment in spec["sku_variant_matrix"]["assignments"]]
        judge_references = list(dict.fromkeys(list(spec["reference_images"]) + [layer["path"] for layer in spec["locked_layers"]] + truth_sources + sku_sources + ([model_source] if model_source else [])))
        if judge_references:
            content.append(
                {
                    "type": "input_text",
                    "text": "The next image or images are role-scoped source references. Compare only the dimension declared before each image. Product/logo/identity sources control protected product identity and materials; a model source controls only the person's face identity; style/layout/lighting/background/typography/palette sources must not be treated as product or face identity.",
                }
            )
            role_lookup = {asset["path"]: asset for asset in spec.get("reference_assets", [])}
            truth_lookup: dict[str, list[dict[str, Any]]] = {}
            for region in spec["product_truth_map"]["regions"]:
                truth_lookup.setdefault(region["source_path"], []).append(region)
            for reference in judge_references:
                reference_path = Path(reference)
                asset = role_lookup.get(reference)
                if asset:
                    content.append({"type": "input_text", "text": f"Reference role={asset['role']}; view={asset['view']}; identity={asset['identity_id'] or 'not-applicable'}."})
                if model_source and str(reference_path) == str(Path(model_source)):
                    content.append({"type": "input_text", "text": "Authoritative Single Model Face Lock source. Compare facial identity only; pose, expression, clothing, camera, and background may differ."})
                if truth_lookup.get(reference):
                    content.append({"type": "input_text", "text": "Truth-map source for regions: " + ", ".join(f"{region['name']} ({region['role']}, bbox={region['bbox']})" for region in truth_lookup[reference]) + "."})
                reference_mime = mimetypes.guess_type(reference_path.name)[0] or "image/png"
                reference_url = f"data:{reference_mime};base64,{base64.b64encode(reference_path.read_bytes()).decode()}"
                content.append({"type": "input_image", "image_url": reference_url})
            for region in spec["product_truth_map"]["regions"]:
                if region.get("mask_path"):
                    mask_path = Path(region["mask_path"])
                    mask_mime = mimetypes.guess_type(mask_path.name)[0] or "image/png"
                    mask_url = f"data:{mask_mime};base64,{base64.b64encode(mask_path.read_bytes()).decode()}"
                    content.append({"type": "input_text", "text": f"Binary or grayscale mask for truth region '{region['name']}' ({region['role']}). White/visible pixels identify the source area to compare."})
                    content.append({"type": "input_image", "image_url": mask_url})
        content.append({"type": "input_text", "text": "The next image is the generated final output to evaluate."})
        content.append({"type": "input_image", "image_url": image_url})
        request_body = {
            "model": spec["judge_model"],
            "input": [
                {
                    "role": "user",
                    "content": content,
                }
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "rendriva_quality_review",
                    "strict": True,
                    "schema": schema,
                }
            },
        }
        payload = api_request("https://api.openai.com/v1/responses", self.api_key, json_body=request_body, timeout=180)
        text = extract_response_text(payload)
        try:
            result = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"The quality judge returned invalid JSON: {text[:500]}") from exc
        return result

    def judge_campaign(self, spec: dict[str, Any], images: list[tuple[int, Path]]) -> dict[str, Any]:
        content: list[dict[str, Any]] = [{"type": "input_text", "text": campaign_vision_prompt(spec, [index for index, _path in images])}]
        for index, image_path in images:
            mime = mimetypes.guess_type(image_path.name)[0] or "image/png"
            image_url = f"data:{mime};base64,{base64.b64encode(image_path.read_bytes()).decode()}"
            content.append({"type": "input_text", "text": f"Campaign output index {index}."})
            content.append({"type": "input_image", "image_url": image_url})
        request_body = {
            "model": spec["judge_model"],
            "input": [{"role": "user", "content": content}],
            "text": {"format": {"type": "json_schema", "name": "rendriva_campaign_vision_review", "strict": True, "schema": campaign_vision_schema()}},
        }
        payload = api_request("https://api.openai.com/v1/responses", self.api_key, json_body=request_body, timeout=240)
        text = extract_response_text(payload)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"The campaign vision judge returned invalid JSON: {text[:500]}") from exc


def quality_schema() -> dict[str, Any]:
    score_properties = {name: {"type": "number", "minimum": 0, "maximum": 5} for name in SCORED_DIMENSIONS}
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "gates_pass": {"type": "boolean"},
            "collage_violation": {"type": "boolean"},
            "instruction_following": {"type": "boolean"},
            "reference_preservation": {"type": "boolean"},
            "text_correctness": {"type": "boolean"},
            "model_identity_match": {"type": "boolean"},
            "model_identity_confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "model_identity_observations": {"type": "string"},
            "sku_variant_match": {"type": "boolean"},
            "garment_construction_match": {"type": "boolean"},
            "product_visibility_ratio": {"type": "number", "minimum": 0, "maximum": 1},
            "product_visibility_pass": {"type": "boolean"},
            "sku_color_match": {"type": "boolean"},
            "anatomy_quality": {"type": "boolean"},
            "localized_repair_scope_preserved": {"type": "boolean"},
            "region_fidelity": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "name": {"type": "string"},
                        "passed": {"type": "boolean"},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "observations": {"type": "string"},
                    },
                    "required": ["name", "passed", "confidence", "observations"],
                },
            },
            "scores": {
                "type": "object",
                "additionalProperties": False,
                "properties": score_properties,
                "required": SCORED_DIMENSIONS,
            },
            "defects": {"type": "array", "items": {"type": "string"}},
            "repair_prompt": {"type": "string"},
            "summary": {"type": "string"},
        },
        "required": [
            "gates_pass",
            "collage_violation",
            "instruction_following",
            "reference_preservation",
            "text_correctness",
            "model_identity_match",
            "model_identity_confidence",
            "model_identity_observations",
            "sku_variant_match",
            "garment_construction_match",
            "product_visibility_ratio",
            "product_visibility_pass",
            "sku_color_match",
            "anatomy_quality",
            "localized_repair_scope_preserved",
            "region_fidelity",
            "scores",
            "defects",
            "repair_prompt",
            "summary",
        ],
    }


CAMPAIGN_VISION_DIMENSIONS = [
    "palette_consistency",
    "typography_consistency",
    "logo_consistency",
    "product_scale_rhythm",
    "lighting_coherence",
    "spacing_grid_consistency",
    "diversity_preserved",
    "model_face_consistency",
    "sku_assignment_consistency",
    "garment_construction_consistency",
    "product_visibility_consistency",
    "sku_color_consistency",
]


def campaign_vision_schema() -> dict[str, Any]:
    score_properties = {name: {"type": "number", "minimum": 0, "maximum": 5} for name in CAMPAIGN_VISION_DIMENSIONS}
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "passed": {"type": "boolean"},
            "scores": {"type": "object", "additionalProperties": False, "properties": score_properties, "required": CAMPAIGN_VISION_DIMENSIONS},
            "outlier_indices": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 10}},
            "defects_by_index": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"index": {"type": "integer", "minimum": 1, "maximum": 10}, "defects": {"type": "array", "items": {"type": "string"}}},
                    "required": ["index", "defects"],
                },
            },
            "repair_prompts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"index": {"type": "integer", "minimum": 1, "maximum": 10}, "prompt": {"type": "string"}},
                    "required": ["index", "prompt"],
                },
            },
            "summary": {"type": "string"},
        },
        "required": ["passed", "scores", "outlier_indices", "defects_by_index", "repair_prompts", "summary"],
    }


def campaign_vision_prompt(spec: dict[str, Any], indices: list[int]) -> str:
    campaign = spec["campaign"]
    model_rule = (
        "Single Model Face Lock is enabled: verify that every visible model is recognizably the same individual face while allowing requested pose, expression, outfit, camera, and scene variation. Any different face is a material outlier."
        if spec["model_identity_lock"]["enabled"]
        else "Single Model Face Lock is disabled; score model_face_consistency as 5 unless the brief independently requires one recurring person."
    )
    return f"""Act as the senior campaign art director for a true cross-image review. Compare all attached outputs together, not independently.

CAMPAIGN ID: {campaign['id']}
CONSISTENCY: {campaign['consistency']}
TOKEN SIGNATURE: {campaign['signature']}
TOKENS: {json.dumps(campaign['tokens'], ensure_ascii=False, sort_keys=True)}
OUTPUT INDICES: {indices}

Verify shared palette logic, typography system, logo treatment and safe-zone behavior, product-scale rhythm, lighting family, spacing/grid logic, and professional finish. Also verify meaningful composition/camera/background diversity; consistency must not become duplication. Product identity and truth-region locks remain non-negotiable. Verify every output against its recorded SKU assignment, garment construction contract, protected-detail visibility target, and source product color. A consistent-looking batch still fails when variants are swapped, blended, obscured, recolored, or structurally invented.

{model_rule}

Score every dimension from 0 to 5. The required average is {campaign['vision_lock']['min_score']:.1f}/5. Mark passed only when the average reaches the threshold and there are no material campaign outliers. Identify only genuine outlier indices. Provide defect-specific repair instructions for each outlier without changing protected products, logos, exact text, or passing outputs."""


def quality_prompt(spec: dict[str, Any], generation_prompt: str) -> str:
    expected_text = [layer["text"] for layer in spec["text_layers"]]
    fidelity_gate = "No reference-fidelity gate applies."
    if spec["fidelity_mode"] == "guided":
        fidelity_gate = "Assess reference similarity as guidance and report material drift, but do not require literal source identity."
    elif spec["fidelity_mode"] == "strict" and (spec["protected_product_reference"] or spec["locked_layers"]):
        fidelity_gate = (
            "STRICT SOURCE-ASSET GATE: mark reference_preservation false and fail the gates for any change to product silhouette, proportions, construction, fabric weave, fibers, texture, finish, stitching, seams, folds, print, color, label, or logo. "
            "A logo fails for altered symbol geometry, lettering, spelling, spacing, color, aspect ratio, placement, cropping, distortion, redraw, or generated approximation. "
            "Do not excuse drift because the result is visually attractive."
        )
    elif spec["fidelity_mode"] == "strict" and spec["protected_model_reference"]:
        fidelity_gate = (
            "STRICT MODEL IDENTITY GATE: compare only the recurring person's facial identity against the model reference. Require the same individual while allowing requested changes to pose, expression, clothing, camera, lighting, and background."
        )
    palette_gate = ""
    if spec["brand"].get("palette_source") == "auto-reference":
        palette_gate = (
            f"REFERENCE-DERIVED BRAND PALETTE: {_join(spec['brand']['palette'])}. "
            "Judge whether unprotected backgrounds, accents, typography, and design elements use this palette coherently. "
            "Penalize unrelated dominant colors, but do not require or permit recoloring any protected source asset."
        )
    campaign_gate = ""
    if spec["campaign"]["enabled"]:
        campaign_gate = (
            f"CAMPAIGN GATE: preserve campaign token signature {spec['campaign']['signature']} and its palette, typography direction, logo safe-zone behavior, grid, and spacing system while still making this output compositionally distinct."
        )
    marketplace_gate = ""
    if spec["marketplace"]["enabled"]:
        marketplace_gate = (
            f"MARKETPLACE TRUTH GATE: exact allowed copy is {json.dumps(spec['marketplace']['exact_copy'], ensure_ascii=False, sort_keys=True)} and allowed claims are {_join(spec['marketplace']['claims'])}. "
            "Fail instruction following or text correctness for any invented price, discount, rating, guarantee, bundle content, badge, credential, or urgency claim."
        )
    truth_gate = "No product-region truth map applies. Return an empty region_fidelity array."
    if spec["product_truth_map"]["enabled"]:
        region_contracts = [
            {
                "name": region["name"],
                "role": region["role"],
                "required": region["required"],
                "bbox": region["bbox"],
                "preserve": region["preserve"],
                "strategy": region["comparison_strategy"],
            }
            for region in spec["product_truth_map"]["regions"]
        ]
        truth_gate = (
            f"PRODUCT REGION TRUTH GATE: evaluate every contract by exact name and return one region_fidelity result per contract: {json.dumps(region_contracts, ensure_ascii=False, sort_keys=True)}. "
            "Any failed required region must make reference_preservation and gates_pass false. Literal-source-composite regions should pass when the recorded source-derived layer is visibly intact."
        )
    model_identity_gate = "Single Model Face Lock is disabled. Return model_identity_match=true, confidence=1, and a not-applicable observation."
    if spec["model_identity_lock"]["enabled"]:
        if spec["model_identity_lock"].get("source_path"):
            model_identity_gate = (
                f"SINGLE MODEL FACE GATE: compare the generated model with the authoritative face anchor. Require the same recognizable individual, preserving {_join(spec['model_identity_lock']['preserve'])}. "
                f"Fail gates_pass and model_identity_match for a different person, lookalike replacement, blended identity, materially changed facial structure, ethnicity, skin tone, or apparent age. "
                f"The minimum acceptable comparison confidence is {spec['model_identity_lock']['min_confidence']:.2f}. Do not fail merely because pose, expression, outfit, camera, lighting, or background differs."
            )
        else:
            model_identity_gate = (
                "SINGLE MODEL FACE ANCHOR GATE: this output establishes the recurring person. Require exactly one clear, natural, unobscured, non-duplicated face suitable as the identity anchor; return model_identity_match=true when usable."
            )
    commerce_gate = (
        "COMMERCE QA: compare only the SKU assigned in the brief with its named authoritative source. Set sku_variant_match=false for a swapped, blended, duplicated, or wrong variant. "
        f"When Garment Construction Lock is enabled ({spec['garment_construction_lock']['enabled']}), verify neckline, sleeves, hem, seams, stitching, pockets, buttons, straps, silhouette, length, fit, print, and labels; never accept invented unseen construction. "
        f"When Product Visibility Guard is enabled ({spec['product_visibility_guard']['enabled']}), estimate the visible product ratio and require at least {spec['product_visibility_guard']['min_visible_ratio']:.2f}; fail when a protected logo, print, neckline, hem, silhouette, or label is obscured. "
        f"When SKU Color Guard is enabled ({spec['sku_color_guard']['enabled']}), distinguish lighting from product identity color and reject material color drift beyond the configured tolerance. "
        "Set anatomy_quality=false for malformed hands, fingers, limbs, face artifacts, or implausible body geometry. For a repair, set localized_repair_scope_preserved=false if previously passing face, product, logo, color, construction, or typography regions changed. Disabled gates return true and visibility ratio 1."
    )
    return f"""Act as a strict senior design director and production QA reviewer. Evaluate the final attached image against the generation brief and any authoritative source references supplied after this instruction.

BRIEF:
{generation_prompt}

Fail the gates if the image is a collage/grid/multi-panel output, misses required content, changes locked reference details, contains severe visual artifacts, crops the main subject unintentionally, invents prohibited brand/product details, or renders critical text incorrectly. The exact required text strings are: {_join(expected_text)}. If no reference or critical text applies, mark that gate true.

{fidelity_gate}

{palette_gate}

{campaign_gate}

{marketplace_gate}

{truth_gate}

{model_identity_gate}

{commerce_gate}

Score visual hierarchy, composition and spacing, brand consistency, realism and artifact control, commercial usability, and originality/restraint from 0 to 5. Generic AI aesthetics, random glow, pseudo-text, plastic texture, clutter, incoherent shadows, and template-like styling must reduce the relevant scores unless explicitly requested.

The required professional average is {spec['min_professional_score']:.1f}/5. Return concise observed defects and a targeted repair prompt that fixes them without changing locked details."""


def extract_response_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    for output in payload.get("output", []):
        for content in output.get("content", []):
            if content.get("type") in {"output_text", "text"} and isinstance(content.get("text"), str):
                return content["text"]
    raise ProviderError("The Responses API result did not contain output text.")


def find_default_font() -> str | None:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ]
    return next((path for path in candidates if Path(path).is_file()), None)


def _load_font(font_path: str | None, size: int) -> tuple[Any, bool]:
    try:
        return (ImageFont.truetype(font_path, size), False) if font_path else (ImageFont.truetype(find_default_font(), size), True)
    except (OSError, TypeError):
        try:
            return ImageFont.load_default(size=size), True
        except TypeError:  # older Pillow
            return ImageFont.load_default(), True


def _wrap_text(draw: Any, text: str, font: Any, max_width: int, spacing: int) -> str:
    paragraphs = text.splitlines() or [text]
    wrapped: list[str] = []
    for paragraph in paragraphs:
        words = paragraph.split()
        if not words:
            wrapped.append("")
            continue
        line = words[0]
        for word in words[1:]:
            candidate = f"{line} {word}"
            bbox = draw.textbbox((0, 0), candidate, font=font)
            if bbox[2] - bbox[0] <= max_width:
                line = candidate
            else:
                wrapped.append(line)
                line = word
        wrapped.append(line)
    return "\n".join(wrapped)


def fit_text(
    draw: Any,
    text: str,
    font_path: str | None,
    requested_size: int | str,
    max_width: int,
    max_height: int | None = None,
) -> tuple[Any, int, bool, str]:
    size = 96 if requested_size == "auto" else int(requested_size)
    while size >= 10:
        font, fallback = _load_font(font_path, size)
        spacing = max(4, size // 5)
        rendered = _wrap_text(draw, text, font, max_width, spacing)
        bbox = draw.multiline_textbbox((0, 0), rendered, font=font, spacing=spacing)
        fits_height = max_height is None or bbox[3] - bbox[1] <= max_height
        if bbox[2] - bbox[0] <= max_width and fits_height:
            return font, size, fallback, rendered
        size -= 2
    raise RendrivaError(f"Text layer cannot fit within {max_width}px and {max_height or 'unlimited'}px height: {text[:80]}")


def auto_cutout_background(image: Any, tolerance: int = 28, edge_softness: int = 4) -> Any:
    """Remove a corner-matched flat background while preserving foreground RGB pixels."""
    rgba = image.convert("RGBA")
    corners = [rgba.getpixel((0, 0))[:3], rgba.getpixel((rgba.width - 1, 0))[:3], rgba.getpixel((0, rgba.height - 1))[:3], rgba.getpixel((rgba.width - 1, rgba.height - 1))[:3]]
    background = tuple(round(sum(pixel[channel] for pixel in corners) / len(corners)) for channel in range(3))
    pixels = []
    ramp = max(1, edge_softness * 8)
    source_pixels = rgba.get_flattened_data() if hasattr(rgba, "get_flattened_data") else rgba.getdata()
    for red, green, blue, alpha in source_pixels:
        distance = ((red - background[0]) ** 2 + (green - background[1]) ** 2 + (blue - background[2]) ** 2) ** 0.5
        cutout_alpha = max(0, min(255, round((distance - tolerance) * 255 / ramp)))
        pixels.append((red, green, blue, min(alpha, cutout_alpha)))
    rgba.putdata(pixels)
    if edge_softness:
        alpha = rgba.getchannel("A").filter(ImageFilter.GaussianBlur(edge_softness / 2))
        rgba.putalpha(alpha)
    return rgba


def apply_locked_layers(image_path: Path, layers: list[dict[str, Any]]) -> dict[str, Any]:
    if not layers:
        return {"applied": False, "layers": []}
    if Image is None:
        raise RendrivaError("Pillow is required for locked_layers. Install scripts/requirements.txt.")
    with Image.open(image_path) as source:
        canvas = source.convert("RGBA")
    applied: list[dict[str, Any]] = []
    for layer in layers:
        with Image.open(layer["path"]) as layer_source:
            has_alpha = "A" in layer_source.getbands()
            if layer["require_alpha"] and not has_alpha:
                raise RendrivaError(
                    f"Locked layer requires a transparent source image but has no alpha channel: {layer['path']}"
                )
            foreground = layer_source.convert("RGBA")
            cutout_applied = bool(layer["auto_cutout"] and not has_alpha)
            if cutout_applied:
                foreground = auto_cutout_background(foreground, layer["cutout_tolerance"], layer["edge_softness"])
        max_width = max(1, int(layer["max_width"] * canvas.width))
        max_height = max(1, int(layer["max_height"] * canvas.height))
        scale = min(max_width / foreground.width, max_height / foreground.height)
        target = (max(1, round(foreground.width * scale)), max(1, round(foreground.height * scale)))
        foreground = foreground.resize(target, Image.Resampling.LANCZOS)
        anchor_x = int(layer["x"] * canvas.width)
        anchor_y = int(layer["y"] * canvas.height)
        offsets = {
            "top-left": (0, 0),
            "top-center": (-target[0] // 2, 0),
            "center": (-target[0] // 2, -target[1] // 2),
            "bottom-center": (-target[0] // 2, -target[1]),
            "bottom-right": (-target[0], -target[1]),
        }
        offset_x, offset_y = offsets[layer["anchor"]]
        x, y = anchor_x + offset_x, anchor_y + offset_y
        if x < 0 or y < 0 or x + target[0] > canvas.width or y + target[1] > canvas.height:
            raise RendrivaError(f"Locked layer placement falls outside the canvas: {layer['path']}")
        shadow = layer["shadow"]
        if shadow["enabled"]:
            shadow_color = ImageColor.getcolor(shadow["color"], "RGBA")
            alpha = foreground.getchannel("A").point(lambda value: round(value * shadow["opacity"] / 255))
            if shadow["blur"]:
                alpha = alpha.filter(ImageFilter.GaussianBlur(shadow["blur"]))
            shadow_image = Image.new("RGBA", foreground.size, (*shadow_color[:3], 0))
            shadow_image.putalpha(alpha)
            shadow_canvas = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            shadow_canvas.paste(shadow_image, (x + shadow["offset_x"], y + shadow["offset_y"]), shadow_image)
            canvas = Image.alpha_composite(canvas, shadow_canvas)
        canvas.alpha_composite(foreground, dest=(x, y))
        applied.append(
            {
                "path": layer["path"],
                "role": layer["role"],
                "source_sha256": hashlib.sha256(Path(layer["path"]).read_bytes()).hexdigest(),
                "position": [x, y],
                "size": [target[0], target[1]],
                "source_alpha": has_alpha,
                "auto_cutout_applied": cutout_applied,
                "shadow_applied": bool(shadow["enabled"]),
                "source_derived": True,
                "generatively_redrawn": False,
            }
        )
    output_format = image_path.suffix.lower().lstrip(".")
    if output_format in {"jpg", "jpeg"}:
        canvas.convert("RGB").save(image_path, format="JPEG", quality=95)
    else:
        canvas.save(image_path, format=output_format.upper())
    return {
        "applied": True,
        "layers": applied,
        "strategy": "literal-source-composite",
        "protected_source_count": len(applied),
    }


def apply_text_layers(image_path: Path, layers: list[dict[str, Any]]) -> dict[str, Any]:
    if not layers:
        return {"applied": False, "font_fallback": False}
    if Image is None:
        raise RendrivaError("Pillow is required for text_layers. Install scripts/requirements.txt.")
    with Image.open(image_path) as source:
        canvas = source.convert("RGBA")
    draw = ImageDraw.Draw(canvas)
    used_fallback = False
    applied_layers: list[dict[str, Any]] = []
    for layer in layers:
        x = int(float(layer.get("x", 0.08)) * canvas.width)
        y = int(float(layer.get("y", 0.08)) * canvas.height)
        max_width = int(float(layer.get("max_width", 0.84)) * canvas.width)
        max_height = int(float(layer.get("max_height", 0.30)) * canvas.height)
        font_path = layer.get("font_path")
        if font_path:
            font_path = str(Path(font_path).expanduser().resolve())
            if not Path(font_path).is_file():
                raise RendrivaError(f"Font file not found: {font_path}")
        font, actual_size, fallback, rendered_text = fit_text(
            draw,
            layer["text"],
            font_path,
            layer.get("font_size", 64),
            max_width,
            max_height,
        )
        used_fallback = used_fallback or fallback
        requested_color = str(layer.get("color", "#111111"))
        if requested_color == "auto":
            sample = canvas.crop((x, y, min(canvas.width, x + max_width), min(canvas.height, y + max_height))).convert("RGB")
            mean = ImageStat.Stat(sample).mean if sample.width and sample.height else [255, 255, 255]
            luminance = 0.2126 * mean[0] + 0.7152 * mean[1] + 0.0722 * mean[2]
            requested_color = "#FFFFFF" if luminance < 145 else "#111111"
        color = ImageColor.getcolor(requested_color, "RGBA")
        align = layer.get("align", "left")
        anchor = {"left": "la", "center": "ma", "right": "ra"}[align]
        if align == "center":
            x += max_width // 2
        elif align == "right":
            x += max_width
        draw.multiline_text(
            (x, y),
            rendered_text,
            font=font,
            fill=color,
            anchor=anchor,
            align=align,
            spacing=max(4, actual_size // 5),
            stroke_width=int(layer.get("stroke_width", 0)),
            stroke_fill=ImageColor.getcolor(str(layer.get("stroke_color", "#000000")), "RGBA"),
        )
        applied_layers.append(
            {
                "text": layer["text"],
                "rendered_text": rendered_text,
                "wrapped": rendered_text != layer["text"],
                "style": layer.get("style", "body"),
                "font_size": actual_size,
                "color": requested_color,
                "font_fallback": fallback,
                "position": [x, y],
                "max_size": [max_width, max_height],
            }
        )
    output_format = image_path.suffix.lower().lstrip(".")
    if output_format in {"jpg", "jpeg"}:
        canvas.convert("RGB").save(image_path, format="JPEG", quality=95)
    else:
        canvas.save(image_path, format=output_format.upper())
    return {"applied": True, "font_fallback": used_fallback, "layers": applied_layers, "engine": "rendriva-typography-v1"}


def structural_review(spec: dict[str, Any], image_path: Path) -> dict[str, Any]:
    defects: list[str] = []
    if not image_path.is_file() or image_path.stat().st_size == 0:
        return {"passed": False, "defects": ["Output file is missing or empty."], "metadata": {}}
    if Image is None:
        return {"passed": True, "defects": [], "metadata": {"inspection": "Pillow unavailable"}}
    try:
        with Image.open(image_path) as image:
            image.verify()
        with Image.open(image_path) as image:
            width, height = image.size
            detected = (image.format or "").lower()
            has_alpha = "A" in image.getbands()
    except Exception as exc:
        return {"passed": False, "defects": [f"Image cannot be decoded: {exc}"], "metadata": {}}
    if spec["size"] != "auto":
        expected = tuple(map(int, spec["size"].split("x")))
        if (width, height) != expected:
            defects.append(f"Dimensions are {width}x{height}; expected {expected[0]}x{expected[1]}.")
    expected_format = {"jpeg": "jpeg", "png": "png", "webp": "webp"}[spec["format"]]
    if detected != expected_format:
        defects.append(f"Detected format is {detected or 'unknown'}; expected {expected_format}.")
    if spec["background"] == "transparent" and not has_alpha:
        defects.append("Transparent output was requested but the file has no alpha channel.")
    return {
        "passed": not defects,
        "defects": defects,
        "metadata": {"width": width, "height": height, "format": detected, "has_alpha": has_alpha},
    }


def finalize_review(spec: dict[str, Any], structural: dict[str, Any], vision: dict[str, Any] | None) -> dict[str, Any]:
    defects = list(structural.get("defects", []))
    if not structural.get("passed"):
        return {
            "passed": False,
            "average_score": 0.0,
            "defects": defects,
            "repair_prompt": "Correct the file dimensions, format, transparency, or decoding problem while preserving the full brief.",
            "structural": structural,
            "vision": vision,
        }
    if vision is None:
        strict_reference_unverified = (
            spec["fidelity_mode"] == "strict" and spec["protected_reference"] and bool(spec["reference_images"]) and not spec["locked_layers"]
        )
        model_identity_unverified = spec["model_identity_lock"]["enabled"] and bool(spec["model_identity_lock"].get("source_path"))
        if strict_reference_unverified or model_identity_unverified:
            reason = "Single Model Face Lock was not verified because the vision judge is disabled." if model_identity_unverified else "Strict generative reference fidelity was not verified because the vision judge is disabled."
            return {
                "passed": False,
                "average_score": None,
                "defects": [reason],
                "repair_prompt": "Enable reference-aware vision judging so the protected source can be compared.",
                "structural": structural,
                "vision": None,
                "evidence_note": "Strict fidelity cannot pass without comparison evidence.",
            }
        return {
            "passed": True,
            "average_score": None,
            "defects": [],
            "repair_prompt": "",
            "structural": structural,
            "vision": None,
            "evidence_note": "Vision judge disabled; only deterministic structural checks ran.",
        }
    scores = vision.get("scores", {})
    values = [float(scores.get(name, 0)) for name in SCORED_DIMENSIONS]
    average = sum(values) / len(values)
    gates = bool(vision.get("gates_pass")) and not bool(vision.get("collage_violation"))
    if spec["fidelity_mode"] == "strict" and (spec["protected_reference"] or spec["locked_layers"]):
        gates = gates and bool(vision.get("reference_preservation"))
    if spec["model_identity_lock"]["enabled"]:
        identity_match = bool(vision.get("model_identity_match"))
        identity_confidence = float(vision.get("model_identity_confidence", 0))
        if not identity_match or identity_confidence < spec["model_identity_lock"]["min_confidence"]:
            gates = False
            defects.append(
                f"Single Model Face Lock failed (match={identity_match}, confidence={identity_confidence:.3f}, required={spec['model_identity_lock']['min_confidence']:.3f})."
            )
    if spec["sku_variant_matrix"]["enabled"] and not bool(vision.get("sku_variant_match")):
        gates = False
        defects.append("The generated product does not match the SKU assigned to this output.")
    if spec["garment_construction_lock"]["enabled"] and not bool(vision.get("garment_construction_match")):
        gates = False
        defects.append("Garment Construction Lock failed.")
    if spec["product_visibility_guard"]["enabled"]:
        visible_ratio = float(vision.get("product_visibility_ratio", 0))
        if not bool(vision.get("product_visibility_pass")) or visible_ratio < spec["product_visibility_guard"]["min_visible_ratio"]:
            gates = False
            defects.append(
                f"Product visibility {visible_ratio:.3f} is below the required {spec['product_visibility_guard']['min_visible_ratio']:.3f} or a protected detail is obscured."
            )
    if spec["sku_color_guard"]["enabled"] and not bool(vision.get("sku_color_match")):
        gates = False
        defects.append("SKU Color Guard failed; product identity color drifted from its source.")
    if not bool(vision.get("anatomy_quality", True)):
        gates = False
        defects.append("Anatomy QA failed.")
    if spec["localized_repair"]["enabled"] and not bool(vision.get("localized_repair_scope_preserved", True)):
        gates = False
        defects.append("Localized repair changed a previously passing locked region; rollback is required.")
    required_regions = {region["name"] for region in spec["product_truth_map"]["regions"] if region["required"]}
    region_results = {str(result.get("name")): result for result in vision.get("region_fidelity", [])}
    missing_regions = sorted(required_regions - set(region_results))
    failed_regions = sorted(name for name in required_regions if name in region_results and not bool(region_results[name].get("passed")))
    if missing_regions or failed_regions:
        gates = False
        if missing_regions:
            defects.append(f"Required truth regions were not evaluated: {', '.join(missing_regions)}.")
        if failed_regions:
            defects.append(f"Required truth regions failed preservation: {', '.join(failed_regions)}.")
    passed = gates and average >= spec["min_professional_score"]
    defects.extend(str(value) for value in vision.get("defects", []))
    if average < spec["min_professional_score"]:
        defects.append(f"Professional-design average {average:.2f}/5 is below {spec['min_professional_score']:.2f}/5.")
    return {
        "passed": passed,
        "average_score": round(average, 3),
        "defects": defects,
        "repair_prompt": str(vision.get("repair_prompt", "Correct the listed defects while retaining all locked details.")),
        "structural": structural,
        "vision": vision,
    }


@dataclass
class RunContext:
    spec: dict[str, Any]
    job_dir: Path
    manifest: dict[str, Any]
    provider: Any
    use_vision_judge: bool
    lock: threading.Lock
    cancelled: threading.Event
    progress: Callable[[str], None]

    def persist(self) -> None:
        with self.lock:
            self.manifest["updated_at"] = int(time.time())
            json_dump(self.job_dir / "manifest.json", self.manifest)


def save_image(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_bytes(payload)
    temporary.replace(path)


def model_identity_anchor_path(context: RunContext) -> Path | None:
    policy = context.spec["model_identity_lock"]
    if not policy["enabled"]:
        return None
    if policy.get("source_path"):
        return Path(policy["source_path"])
    state = context.manifest.get("model_identity_lock") or {}
    anchor_file = state.get("anchor_file")
    if not anchor_file:
        return None
    path = Path(anchor_file)
    return path if path.is_absolute() else context.job_dir / path


def runtime_spec_for_item(context: RunContext, item: dict[str, Any]) -> dict[str, Any]:
    anchor_path = model_identity_anchor_path(context)
    runtime_spec = copy.deepcopy(context.spec)
    assignment = item.get("sku_assignment")
    if assignment:
        assigned_source = assignment["source_path"]
        product_sources = {
            asset["path"] for asset in runtime_spec["reference_assets"] if asset["role"] in {"product", "identity"}
        }
        runtime_spec["reference_images"] = list(
            dict.fromkeys([path for path in runtime_spec["reference_images"] if path not in product_sources or path == assigned_source] + [assigned_source])
        )
        runtime_spec["reference_assets"] = [
            asset for asset in runtime_spec["reference_assets"] if asset["role"] not in {"product", "identity"} or asset["path"] == assigned_source
        ]
        runtime_spec["product_truth_map"]["regions"] = [
            region for region in runtime_spec["product_truth_map"]["regions"] if region["source_path"] not in product_sources or region["source_path"] == assigned_source
        ]
        runtime_spec["product_truth_map"]["enabled"] = bool(runtime_spec["product_truth_map"]["regions"])
        runtime_spec["operation"] = "edit"
    if runtime_spec["defect_memory"]["enabled"]:
        runtime_spec["defect_memory"]["entries"] = list((context.manifest.get("defect_memory") or {}).get("entries", runtime_spec["defect_memory"]["entries"]))
    if not anchor_path:
        return runtime_spec
    if not anchor_path.is_file():
        raise RendrivaError(f"Model identity anchor is missing: {anchor_path}")
    runtime_spec["model_identity_lock"]["source_path"] = str(anchor_path.resolve())
    runtime_spec["model_identity_lock"]["source_sha256"] = hashlib.sha256(anchor_path.read_bytes()).hexdigest()
    runtime_spec["model_identity_lock"]["anchor_strategy"] = (context.manifest.get("model_identity_lock") or {}).get("anchor_strategy", runtime_spec["model_identity_lock"]["anchor_strategy"])
    item["model_identity_anchor_file"] = str(anchor_path.relative_to(context.job_dir)) if anchor_path.is_relative_to(context.job_dir) else str(anchor_path)
    item["model_identity_anchor_sha256"] = runtime_spec["model_identity_lock"]["source_sha256"]
    return runtime_spec


def remember_defects(context: RunContext, review: dict[str, Any]) -> None:
    policy = context.spec["defect_memory"]
    if not policy["enabled"]:
        return
    observed = [str(value).strip() for value in review.get("defects", []) if str(value).strip()]
    if not observed:
        return
    with context.lock:
        state = context.manifest.setdefault("defect_memory", copy.deepcopy(policy))
        state["entries"] = list(dict.fromkeys(state.get("entries", []) + observed))[-policy["max_entries"] :]


def review_image(context: RunContext, item: dict[str, Any], prompt: str, image_path: Path) -> dict[str, Any]:
    review_spec = runtime_spec_for_item(context, item)
    structural = structural_review(review_spec, image_path)
    vision = None
    if structural["passed"] and context.use_vision_judge:
        vision = context.provider.judge(review_spec, image_path, prompt)
    return finalize_review(review_spec, structural, vision)


def apply_diversity_gate(context: RunContext, item: dict[str, Any], image_path: Path, review: dict[str, Any]) -> dict[str, Any]:
    policy = context.spec["diversity"]
    if not review.get("passed") or not policy["enabled"] or policy["allow_repeats"]:
        return review
    with context.lock:
        candidates = [
            (other["index"], context.job_dir / other["file"])
            for other in context.manifest["outputs"]
            if other["index"] != item["index"] and other["status"] == "PASS" and (context.job_dir / other["file"]).is_file()
        ]
    comparisons = [{"index": index, "similarity": image_similarity(image_path, path)} for index, path in candidates]
    nearest = max(comparisons, key=lambda value: value["similarity"], default=None)
    review["diversity"] = {"threshold": policy["max_similarity"], "comparisons": comparisons, "nearest": nearest}
    if nearest and nearest["similarity"] > policy["max_similarity"]:
        review["passed"] = False
        review.setdefault("defects", []).append(
            f"Output is too similar to image {nearest['index']:02d} ({nearest['similarity']:.4f} > {policy['max_similarity']:.4f})."
        )
        review["repair_prompt"] = (
            f"Create a materially different standalone composition using this variation direction: {item['variation_direction']}. "
            "Change camera, composition, negative-space placement, lighting, and background while preserving all identity and campaign locks."
        )
    return review


def create_item_payload(context: RunContext, item: dict[str, Any], prompt: str, generation_spec: dict[str, Any] | None = None) -> bytes:
    generation_spec = generation_spec or runtime_spec_for_item(context, item)
    selected_draft = item.get("selected_draft_file")
    identity_path = Path(generation_spec["model_identity_lock"]["source_path"]) if generation_spec["model_identity_lock"]["enabled"] and generation_spec["model_identity_lock"].get("source_path") else None
    if identity_path:
        if not hasattr(context.provider, "create_with_identity"):
            raise ProviderError("The configured provider does not support Single Model Face Lock generation.")
        draft_path = context.job_dir / selected_draft if selected_draft else None
        if draft_path and not draft_path.is_file():
            raise RendrivaError(f"Selected draft file is missing: {selected_draft}")
        return context.provider.create_with_identity(generation_spec, prompt, identity_path, draft_path=draft_path, n=1)[0]
    if selected_draft:
        draft_path = context.job_dir / selected_draft
        if not draft_path.is_file():
            raise RendrivaError(f"Selected draft file is missing: {selected_draft}")
        if not hasattr(context.provider, "promote"):
            raise ProviderError("The configured provider does not support draft-to-final promotion.")
        return context.provider.promote(generation_spec, prompt, draft_path, n=1)[0]
    return context.provider.create(generation_spec, prompt, n=1)[0]


def create_repair_payload(context: RunContext, spec: dict[str, Any], prompt: str, failed_path: Path) -> bytes:
    if spec["localized_repair"]["enabled"]:
        if not hasattr(context.provider, "repair"):
            raise ProviderError("The configured provider does not support localized source-image repair.")
        return context.provider.repair(spec, prompt, failed_path, n=1)[0]
    return context.provider.create(spec, prompt, n=1)[0]


def process_item(
    context: RunContext,
    item: dict[str, Any],
    initial_bytes: bytes | None = None,
    actual_prompt: str | None = None,
) -> None:
    if context.cancelled.is_set():
        return
    spec = runtime_spec_for_item(context, item)
    image_path = context.job_dir / item["file"]
    prompt = actual_prompt or compile_prompt(spec, item)
    item["compiled_prompt"] = prompt
    try:
        item["status"] = "GENERATING"
        item["attempts"] += 1
        context.persist()
        context.progress(f"Image {item['index']}/{spec['count']} — generating")
        payload = initial_bytes if initial_bytes is not None else create_item_payload(context, item, prompt, spec)
        save_image(image_path, payload)
        item["locked_layer_composite"] = apply_locked_layers(image_path, spec["locked_layers"])
        item["text_overlay"] = apply_text_layers(image_path, spec["text_layers"])
        item["status"] = "JUDGING"
        context.persist()
        context.progress(f"Image {item['index']}/{spec['count']} — judging")
        review = apply_diversity_gate(context, item, image_path, review_image(context, item, prompt, image_path))
        remember_defects(context, review)
        item["quality"] = review
        if review["passed"]:
            item["status"] = "PASS"
            context.progress(f"Image {item['index']}/{spec['count']} — complete")
            context.persist()
            return

        item["status"] = "NEEDS_REPAIR"
        context.persist()
        while item["repair_attempts"] < spec["max_repair_attempts"] and not context.cancelled.is_set():
            item["status"] = "REPAIRING"
            item["repair_attempts"] += 1
            context.persist()
            context.progress(f"Image {item['index']}/{spec['count']} — repairing")
            repair_prompt = compile_prompt(spec, item, repair=review)
            repaired = create_repair_payload(context, spec, repair_prompt, image_path)
            repair_path = image_path.with_name(f"{image_path.stem}-repair-{item['repair_attempts']}{image_path.suffix}")
            save_image(repair_path, repaired)
            locked_composite = apply_locked_layers(repair_path, spec["locked_layers"])
            overlay = apply_text_layers(repair_path, spec["text_layers"])
            repaired_review = apply_diversity_gate(context, item, repair_path, review_image(context, item, repair_prompt, repair_path))
            remember_defects(context, repaired_review)
            item.setdefault("repair_history", []).append(
                {
                    "attempt": item["repair_attempts"],
                    "file": repair_path.name,
                    "prompt": repair_prompt,
                    "quality": repaired_review,
                    "locked_layer_composite": locked_composite,
                    "text_overlay": overlay,
                }
            )
            review = repaired_review
            if repaired_review["passed"]:
                repair_path.replace(image_path)
                item["quality"] = repaired_review
                item["selected_repair"] = item["repair_attempts"]
                item["status"] = "PASS"
                context.progress(f"Image {item['index']}/{spec['count']} — repaired and complete")
                context.persist()
                return
            if spec["localized_repair"]["rollback_on_lock_drift"] and not bool((repaired_review.get("vision") or {}).get("localized_repair_scope_preserved", True)):
                item["repair_rollback_protected"] = True
                repair_path.unlink(missing_ok=True)
        item["quality"] = review
        item["status"] = "FAILED"
        item["error"] = "Quality requirements were not met after allowed repair attempts."
    except Exception as exc:
        item["status"] = "BLOCKED" if isinstance(exc, ProviderError) and "policy" in str(exc).lower() else "FAILED"
        item["error"] = str(exc)
        context.progress(f"Image {item['index']}/{spec['count']} — {item['status'].lower()}: {exc}")
    finally:
        context.persist()


def run_draft_to_final(context: RunContext) -> None:
    policy = context.spec["draft_to_final"]
    if not policy["enabled"]:
        return
    existing = context.manifest.get("draft_selection") or {}
    existing_selection = existing.get("selected_candidates", [])
    existing_files = [context.job_dir / f"drafts/draft-{index:02d}.{extension_for(context.spec['format'])}" for index in existing_selection]
    if existing.get("status") == "SELECTED" and len(existing_selection) == context.spec["count"] and all(path.is_file() for path in existing_files):
        for item, candidate_index in zip(context.manifest["outputs"], existing_selection):
            item["selected_draft_index"] = candidate_index
            item["selected_draft_file"] = f"drafts/draft-{candidate_index:02d}.{extension_for(context.spec['format'])}"
        context.persist()
        return

    draft_spec = copy.deepcopy(context.spec)
    draft_spec["count"] = policy["candidate_count"]
    draft_spec["mode"] = "variations"
    draft_spec["scenes"] = []
    draft_spec["quality"] = policy["draft_quality"]
    draft_spec["draft_to_final"] = {**policy, "enabled": False}
    draft_spec["text_layers"] = []
    draft_spec["text_safe_mode"] = True
    draft_plan = build_plan(draft_spec)
    common_item = dict(draft_plan[0])
    common_item["batch_variations"] = True
    common_item["draft_candidate"] = True
    prompt = compile_prompt(draft_spec, common_item)
    context.progress(f"Generating {policy['candidate_count']} low-cost draft candidates for selection")
    draft_root = context.job_dir / "drafts"
    draft_root.mkdir(parents=True, exist_ok=True)
    draft_identity_anchor = model_identity_anchor_path(context) if context.spec["model_identity_lock"]["enabled"] else None
    if context.spec["model_identity_lock"]["enabled"] and not hasattr(context.provider, "create_with_identity"):
        raise ProviderError("The configured provider does not support Single Model Face Lock draft generation.")
    if draft_identity_anchor:
        identity_draft_spec = copy.deepcopy(draft_spec)
        identity_draft_spec["model_identity_lock"]["source_path"] = str(draft_identity_anchor.resolve())
        identity_prompt = compile_prompt(identity_draft_spec, common_item)
        payloads = context.provider.create_with_identity(identity_draft_spec, identity_prompt, draft_identity_anchor, n=policy["candidate_count"])
    elif context.spec["model_identity_lock"]["enabled"]:
        first_payload = context.provider.create(draft_spec, compile_prompt(draft_spec, {**draft_plan[0], "draft_candidate": True}), n=1)[0]
        draft_identity_anchor = draft_root / f"draft-01.{extension_for(context.spec['format'])}"
        save_image(draft_identity_anchor, first_payload)
        identity_draft_spec = copy.deepcopy(draft_spec)
        identity_draft_spec["model_identity_lock"]["source_path"] = str(draft_identity_anchor.resolve())
        identity_prompt = compile_prompt(identity_draft_spec, common_item)
        remaining = context.provider.create_with_identity(identity_draft_spec, identity_prompt, draft_identity_anchor, n=policy["candidate_count"] - 1)
        payloads = [first_payload, *remaining]
    else:
        payloads = context.provider.create(draft_spec, prompt, n=policy["candidate_count"])
    if len(payloads) != policy["candidate_count"]:
        raise ProviderError(f"Draft provider returned {len(payloads)} candidates for {policy['candidate_count']} planned drafts.")

    candidates: list[dict[str, Any]] = []
    for candidate_index, payload in enumerate(payloads, start=1):
        relative = f"drafts/draft-{candidate_index:02d}.{extension_for(context.spec['format'])}"
        path = context.job_dir / relative
        save_image(path, payload)
        locked = apply_locked_layers(path, draft_spec["locked_layers"])
        overlay = {
            "applied": False,
            "font_fallback": False,
            "reason": "Exact typography is withheld from draft promotion sources and applied only to final outputs.",
        }
        candidate_item = draft_plan[candidate_index - 1]
        candidate_item["draft_candidate"] = True
        candidate_spec = draft_spec
        if draft_identity_anchor and (context.spec["model_identity_lock"].get("source_path") or candidate_index > 1):
            candidate_spec = copy.deepcopy(draft_spec)
            candidate_spec["model_identity_lock"]["source_path"] = str(draft_identity_anchor.resolve())
            candidate_spec["model_identity_lock"]["source_sha256"] = hashlib.sha256(draft_identity_anchor.read_bytes()).hexdigest()
        candidate_prompt = compile_prompt(candidate_spec, candidate_item)
        structural = structural_review(candidate_spec, path)
        vision = context.provider.judge(candidate_spec, path, candidate_prompt) if structural["passed"] and context.use_vision_judge else None
        review = finalize_review(candidate_spec, structural, vision)
        candidates.append(
            {
                "index": candidate_index,
                "file": relative,
                "status": "PASS" if review["passed"] else "FAILED",
                "quality": review,
                "score": review.get("average_score"),
                "locked_layer_composite": locked,
                "text_overlay": overlay,
                "selected": False,
            }
        )

    if context.spec["model_identity_lock"]["enabled"] and not context.spec["model_identity_lock"].get("source_path"):
        if not candidates or candidates[0]["status"] != "PASS":
            raise RendrivaError("The first draft could not establish an approved model face identity anchor.")
        record_model_identity_anchor(context, draft_identity_anchor, "first-approved-draft")
    elif context.spec["model_identity_lock"]["enabled"] and draft_identity_anchor:
        record_model_identity_anchor(context, draft_identity_anchor, "supplied-reference")

    if policy["selection_mode"] == "manual":
        selected = list(policy["selection"])
        failed_selected = [index for index in selected if candidates[index - 1]["status"] != "PASS"]
        if failed_selected:
            raise RendrivaError(f"Manual draft selection contains candidates that did not pass draft QA: {failed_selected}.")
    else:
        passing = [candidate for candidate in candidates if candidate["status"] == "PASS"]
        passing.sort(key=lambda candidate: (-(candidate["score"] if candidate["score"] is not None else 0), candidate["index"]))
        selected = []
        for candidate in passing:
            if not context.spec["diversity"]["enabled"] or all(
                image_similarity(context.job_dir / candidate["file"], context.job_dir / candidates[chosen - 1]["file"])
                <= context.spec["diversity"]["max_similarity"]
                for chosen in selected
            ):
                selected.append(candidate["index"])
            if len(selected) == context.spec["count"]:
                break
    if len(selected) != context.spec["count"]:
        raise RendrivaError(f"Draft selection produced only {len(selected)} usable candidates for {context.spec['count']} final outputs.")
    selected_set = set(selected)
    for candidate in candidates:
        candidate["selected"] = candidate["index"] in selected_set
    for item, candidate_index in zip(context.manifest["outputs"], selected):
        item["selected_draft_index"] = candidate_index
        item["selected_draft_file"] = f"drafts/draft-{candidate_index:02d}.{extension_for(context.spec['format'])}"
    context.manifest["draft_selection"] = {
        "status": "SELECTED",
        "selection_mode": policy["selection_mode"],
        "draft_quality": policy["draft_quality"],
        "selected_candidates": selected,
        "candidates": candidates,
    }
    context.persist()


def record_model_identity_anchor(context: RunContext, path: Path, strategy: str, output_index: int | None = None) -> None:
    if not path.is_file():
        raise RendrivaError(f"Cannot establish model identity anchor from missing file: {path}")
    anchor_file = str(path.relative_to(context.job_dir)) if path.is_relative_to(context.job_dir) else str(path.resolve())
    context.manifest["model_identity_lock"] = {
        **context.spec["model_identity_lock"],
        "status": "ACTIVE",
        "anchor_strategy": strategy,
        "anchor_file": anchor_file,
        "anchor_output_index": output_index,
        "anchor_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    context.persist()


def run_model_identity_generation(context: RunContext, items: list[dict[str, Any]]) -> None:
    policy = context.spec["model_identity_lock"]
    anchor_path = model_identity_anchor_path(context)
    if policy.get("source_path") and not (context.manifest.get("model_identity_lock") or {}).get("anchor_file"):
        anchor_path = Path(policy["source_path"])
        record_model_identity_anchor(context, anchor_path, "supplied-reference")
    if anchor_path is None:
        existing_anchor = next(
            (
                item for item in sorted(context.manifest["outputs"], key=lambda value: value["index"])
                if item["status"] == "PASS" and (context.job_dir / item["file"]).is_file()
            ),
            None,
        )
        if existing_anchor:
            anchor_path = context.job_dir / existing_anchor["file"]
            record_model_identity_anchor(context, anchor_path, "first-approved-output", existing_anchor["index"])
    if anchor_path is None:
        anchor_item = min(items, key=lambda value: value["index"])
        context.progress(f"Image {anchor_item['index']}/{context.spec['count']} — establishing the single model face anchor")
        process_item(context, anchor_item)
        if anchor_item["status"] != "PASS":
            error = "The first model could not establish an approved face identity anchor."
            for item in items:
                if item is not anchor_item and item["status"] != "PASS":
                    item["status"] = "FAILED"
                    item["error"] = error
            context.manifest["model_identity_lock"] = {**policy, "status": "FAILED", "reason": error}
            context.persist()
            return
        anchor_path = context.job_dir / anchor_item["file"]
        record_model_identity_anchor(context, anchor_path, "first-approved-output", anchor_item["index"])
    context.progress(f"Single Model Face Lock active — using {anchor_path.name} for all remaining variants")
    for item in sorted(items, key=lambda value: value["index"]):
        if item["status"] == "PASS":
            continue
        process_item(context, item)


def run_generation(context: RunContext) -> None:
    items = [item for item in context.manifest["outputs"] if item["status"] != "PASS"]
    if not items:
        context.progress("All requested outputs already pass; nothing to resume.")
        return

    spec = context.spec
    checkpoint = spec["approval_checkpoint"]
    if checkpoint["enabled"] and not checkpoint["approved"]:
        anchor = next(item for item in context.manifest["outputs"] if item["index"] == checkpoint["anchor_output_index"])
        if anchor["status"] != "PASS":
            context.progress(f"Approval checkpoint — generating anchor image {anchor['index']} only")
            process_item(context, anchor)
        if anchor["status"] == "PASS" and spec["model_identity_lock"]["enabled"] and not spec["model_identity_lock"].get("source_path"):
            record_model_identity_anchor(context, context.job_dir / anchor["file"], "approval-checkpoint-anchor", anchor["index"])
        for item in context.manifest["outputs"]:
            if item["index"] != anchor["index"] and item["status"] != "PASS":
                item["status"] = "BLOCKED"
                item["error"] = "Awaiting user approval of the anchor image before paid batch generation."
        context.manifest["approval_checkpoint"] = {
            **checkpoint,
            "status": "AWAITING_APPROVAL" if anchor["status"] == "PASS" else "ANCHOR_FAILED",
            "anchor_file": anchor["file"] if anchor["status"] == "PASS" else None,
        }
        context.persist()
        return
    if spec["model_identity_lock"]["enabled"]:
        run_model_identity_generation(context, items)
        return
    can_batch = (
        spec["mode"] == "variations"
        and spec["operation"] == "generate"
        and not spec["reference_images"]
        and not spec["draft_to_final"]["enabled"]
        and not spec["sku_variant_matrix"]["enabled"]
        and not spec["shot_director"]["enabled"]
    )
    if can_batch and all(item["attempts"] == 0 for item in items):
        common_item = dict(items[0])
        common_item["batch_variations"] = True
        common_prompt = compile_prompt(spec, common_item)
        context.progress(f"Generating {len(items)} separate images in one provider request")
        try:
            payloads = context.provider.create(spec, common_prompt, n=len(items))
        except Exception as exc:
            for item in items:
                item["status"] = "FAILED"
                item["error"] = str(exc)
            context.persist()
            return
        if len(payloads) > len(items):
            error = f"Provider returned {len(payloads)} images for {len(items)} planned outputs."
            for item in items:
                item["status"] = "FAILED"
                item["error"] = error
            context.persist()
            return
        for item, payload in zip(items, payloads):
            process_item(context, item, initial_bytes=payload, actual_prompt=common_prompt)
        if len(payloads) < len(items):
            error = f"Provider returned only {len(payloads)} images for {len(items)} planned outputs."
            for item in items[len(payloads) :]:
                item["status"] = "FAILED"
                item["error"] = error
            context.persist()
        return

    with ThreadPoolExecutor(max_workers=spec["concurrency"], thread_name_prefix="rendriva") as executor:
        futures = [executor.submit(process_item, context, item) for item in items]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as exc:  # process_item normally contains errors; this is a last guard
                context.progress(f"Unexpected worker failure: {exc}")


def finalize_campaign_vision_review(spec: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    scores = review.get("scores", {})
    values = [float(scores.get(name, 0)) for name in CAMPAIGN_VISION_DIMENSIONS]
    average = sum(values) / len(values)
    valid_indices = set(range(1, spec["count"] + 1))
    outliers = sorted({int(index) for index in review.get("outlier_indices", []) if int(index) in valid_indices})
    passed = bool(review.get("passed")) and average >= spec["campaign"]["vision_lock"]["min_score"] and not outliers
    return {**review, "verified": True, "average_score": round(average, 3), "outlier_indices": outliers, "passed": passed}


def repair_campaign_outlier(context: RunContext, item: dict[str, Any], campaign_review: dict[str, Any], attempt: int) -> bool:
    defects_lookup = {int(entry["index"]): entry.get("defects", []) for entry in campaign_review.get("defects_by_index", [])}
    prompt_lookup = {int(entry["index"]): entry.get("prompt", "") for entry in campaign_review.get("repair_prompts", [])}
    repair = {
        "defects": defects_lookup.get(item["index"], ["This image is a visual outlier in the campaign batch."]),
        "repair_prompt": prompt_lookup.get(item["index"], "Match the shared campaign system while keeping this composition distinct."),
    }
    runtime_spec = runtime_spec_for_item(context, item)
    prompt = compile_prompt(runtime_spec, item, repair=repair)
    temporary_path = (context.job_dir / item["file"]).with_name(f"{Path(item['file']).stem}-campaign-repair-{attempt}{Path(item['file']).suffix}")
    payload = create_repair_payload(context, runtime_spec, prompt, context.job_dir / item["file"])
    save_image(temporary_path, payload)
    locked = apply_locked_layers(temporary_path, runtime_spec["locked_layers"])
    overlay = apply_text_layers(temporary_path, runtime_spec["text_layers"])
    review = apply_diversity_gate(context, item, temporary_path, review_image(context, item, prompt, temporary_path))
    item.setdefault("campaign_repair_history", []).append(
        {"attempt": attempt, "file": temporary_path.name, "prompt": prompt, "quality": review, "locked_layer_composite": locked, "text_overlay": overlay}
    )
    if review["passed"]:
        temporary_path.replace(context.job_dir / item["file"])
        item["quality"] = review
        item["selected_campaign_repair"] = attempt
        return True
    temporary_path.unlink(missing_ok=True)
    return False


def run_campaign_vision_lock(context: RunContext) -> None:
    policy = context.spec["campaign"]["vision_lock"]
    existing = context.manifest.get("campaign_visual_review") or {}
    if (existing.get("verified") or existing.get("comparison_executed")) and existing.get("passed"):
        return
    passing = [item for item in context.manifest["outputs"] if item["status"] == "PASS"]
    if not policy["enabled"] or len(passing) < 2:
        context.manifest["campaign_visual_review"] = {
            "verified": False,
            "passed": False,
            "reason": "Campaign Vision Lock requires at least two passing outputs." if policy["enabled"] else "Campaign Vision Lock is disabled.",
            "attempts": [],
        }
        context.persist()
        return
    if not context.use_vision_judge or not hasattr(context.provider, "judge_campaign"):
        context.manifest["campaign_visual_review"] = {
            "verified": False,
            "passed": False,
            "reason": "Cross-image campaign vision judging is unavailable or disabled.",
            "attempts": [],
        }
        context.persist()
        return

    attempts: list[dict[str, Any]] = []
    for attempt in range(policy["max_repair_attempts"] + 1):
        passing = [item for item in context.manifest["outputs"] if item["status"] == "PASS"]
        images = [(item["index"], context.job_dir / item["file"]) for item in passing]
        review = finalize_campaign_vision_review(context.spec, context.provider.judge_campaign(context.spec, images))
        review["comparison_executed"] = True
        review["synthetic_evidence"] = bool(getattr(context.provider, "synthetic_evidence", False))
        if review["synthetic_evidence"]:
            review["verified"] = False
        attempts.append({"attempt": attempt, "review": review})
        if review["passed"]:
            context.manifest["campaign_visual_review"] = {**review, "attempts": attempts}
            context.persist()
            return
        if attempt >= policy["max_repair_attempts"] or not review["outlier_indices"]:
            break
        context.progress(f"Campaign Vision Lock found outliers {review['outlier_indices']} — repairing only those outputs")
        for index in review["outlier_indices"]:
            item = next((candidate for candidate in passing if candidate["index"] == index), None)
            if item and not repair_campaign_outlier(context, item, review, attempt + 1):
                item["status"] = "FAILED"
                item["error"] = "Campaign outlier repair did not pass per-image quality review."
        context.persist()

    final_review = attempts[-1]["review"] if attempts else {"verified": False, "passed": False, "reason": "No campaign review ran."}
    context.manifest["campaign_visual_review"] = {**final_review, "attempts": attempts}
    for index in final_review.get("outlier_indices", []):
        item = next((candidate for candidate in context.manifest["outputs"] if candidate["index"] == index), None)
        if item and item["status"] == "PASS":
            item["status"] = "FAILED"
            item["error"] = "Campaign Vision Lock still identified this output as an outlier after allowed repairs."
    context.persist()


def quality_report(manifest: dict[str, Any]) -> dict[str, Any]:
    synthetic = manifest.get("evidence_mode") == "synthetic-mock"
    counts = {status: 0 for status in ("PASS", "FAILED", "BLOCKED", "NEEDS_REPAIR")}
    results = []
    for item in manifest["outputs"]:
        if item["status"] in counts:
            counts[item["status"]] += 1
        results.append(
            {
                "index": item["index"],
                "file": item["file"],
                "status": item["status"],
                "repair_attempts": item["repair_attempts"],
                "average_score": (item.get("quality") or {}).get("average_score"),
                "defects": (item.get("quality") or {}).get("defects", []),
                "error": item.get("error"),
                "visual_verification_status": "SYNTHETIC_TEST_ONLY" if synthetic else "VERIFIED" if item["status"] == "PASS" else "NOT_VERIFIED",
            }
        )
    return {
        "job_id": manifest["job_id"],
        "evidence_mode": manifest.get("evidence_mode", "vision-provider"),
        "delivery_ready": not synthetic and counts["FAILED"] == 0 and counts["BLOCKED"] == 0,
        "verification_note": "Mock outputs validate orchestration only and are not delivery-ready visual evidence." if synthetic else "Passing outputs were evaluated by the configured provider workflow.",
        "counts": counts,
        "campaign_visual_review": manifest.get("campaign_visual_review"),
        "draft_selection": manifest.get("draft_selection"),
        "model_identity_report": manifest.get("model_identity_report"),
        "outputs": results,
    }


def build_model_identity_report(manifest: dict[str, Any], job_dir: Path) -> dict[str, Any]:
    policy = manifest["spec"]["model_identity_lock"]
    state = manifest.get("model_identity_lock") or {}
    anchor_output_index = state.get("anchor_output_index")
    outputs = []
    comparisons = []
    for item in manifest["outputs"]:
        vision = (item.get("quality") or {}).get("vision") or {}
        record = {
            "index": item["index"],
            "file": item["file"],
            "status": item["status"],
            "is_generated_anchor": item["index"] == anchor_output_index,
            "anchor_sha256": item.get("model_identity_anchor_sha256"),
            "match": vision.get("model_identity_match"),
            "confidence": vision.get("model_identity_confidence"),
            "observations": vision.get("model_identity_observations"),
        }
        outputs.append(record)
        if policy["enabled"] and item["index"] != anchor_output_index:
            comparisons.append(record)
    comparison_passed = bool(policy["enabled"] and comparisons) and all(
        record["status"] == "PASS"
        and record["match"] is True
        and isinstance(record["confidence"], (int, float))
        and float(record["confidence"]) >= policy["min_confidence"]
        for record in comparisons
    )
    synthetic_evidence = manifest.get("evidence_mode") == "synthetic-mock"
    verified = comparison_passed and not synthetic_evidence
    anchor_file = state.get("anchor_file", policy.get("source_path"))
    anchor_path = Path(anchor_file) if anchor_file else None
    if anchor_path and not anchor_path.is_absolute():
        anchor_path = job_dir / anchor_path
    current_anchor_sha256 = hashlib.sha256(anchor_path.read_bytes()).hexdigest() if anchor_path and anchor_path.is_file() else state.get("anchor_sha256", policy.get("source_sha256"))
    return {
        "enabled": policy["enabled"],
        "mode": policy["mode"],
        "status": "DISABLED" if not policy["enabled"] else "SYNTHETIC_TEST_ONLY" if comparison_passed and synthetic_evidence else "PASS" if verified else "FAILED_OR_UNVERIFIED",
        "verified": verified,
        "comparison_passed": comparison_passed,
        "synthetic_evidence": synthetic_evidence,
        "anchor_strategy": state.get("anchor_strategy", policy["anchor_strategy"]),
        "anchor_file": anchor_file,
        "anchor_sha256": current_anchor_sha256,
        "anchor_output_index": anchor_output_index,
        "min_confidence": policy["min_confidence"],
        "outputs": outputs,
    }


def package_outputs(job_dir: Path, manifest: dict[str, Any]) -> Path:
    manifest["model_identity_report"] = build_model_identity_report(manifest, job_dir)
    report_path = job_dir / "quality-report.json"
    json_dump(report_path, quality_report(manifest))
    export_records = create_platform_exports(job_dir, manifest)
    brand_profile_path = job_dir / "brand-profile.json"
    json_dump(brand_profile_path, manifest["spec"]["brand"])
    fidelity_path = job_dir / "reference-fidelity-report.json"
    json_dump(fidelity_path, reference_fidelity_report(manifest))
    diversity_path = job_dir / "diversity-report.json"
    json_dump(diversity_path, diversity_report(job_dir, manifest))
    draft_path = job_dir / "draft-selection-report.json"
    json_dump(
        draft_path,
        manifest.get("draft_selection", {"status": "DISABLED", "reason": "Draft-to-final workflow was not enabled."}),
    )
    model_identity_path = job_dir / "model-identity-report.json"
    json_dump(model_identity_path, manifest["model_identity_report"])
    commerce_path = job_dir / "commerce-production-report.json"
    json_dump(commerce_path, build_commerce_report(manifest))
    video_path = job_dir / "video-continuity-pack.json"
    json_dump(video_path, build_video_continuity_report(manifest))
    campaign_path = job_dir / "campaign-report.json"
    campaign_review = manifest.get("campaign_visual_review") or {}
    json_dump(
        campaign_path,
        {
            "job_id": manifest["job_id"],
            "campaign": manifest["spec"]["campaign"],
            "evidence_scope": "cross-image-vision-comparison" if campaign_review.get("verified") else "synthetic-mock-comparison" if campaign_review.get("synthetic_evidence") else "policy-and-per-output-qa",
            "batch_visual_consistency_verified": bool(campaign_review.get("verified")),
            "batch_visual_consistency_passed": bool(campaign_review.get("passed")) and not bool(campaign_review.get("synthetic_evidence")),
            "synthetic_comparison_passed": bool(campaign_review.get("passed")) if campaign_review.get("synthetic_evidence") else None,
            "vision_review": campaign_review,
            "verification_note": (
                "All passing outputs were compared together by Campaign Vision Lock."
                if campaign_review.get("verified")
                else "The mock provider exercised the comparison workflow, but its placeholder outputs do not verify real visual consistency."
                if campaign_review.get("synthetic_evidence")
                else "Shared tokens and per-output QA are recorded, but cross-image vision comparison was unavailable or unnecessary."
            ),
            "outputs": [
                {"index": item["index"], "file": item["file"], "status": item["status"], "campaign_signature": item["campaign_signature"]}
                for item in manifest["outputs"]
            ],
        },
    )
    manifest["platform_export_pack"] = export_records
    json_dump(job_dir / "manifest.json", manifest)
    archive_path = job_dir / "rendriva-output.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in manifest["outputs"]:
            if item["status"] == "PASS":
                image_path = job_dir / item["file"]
                if image_path.is_file():
                    archive.write(image_path, arcname=image_path.name)
        for path in (job_dir / "manifest.json", report_path, brand_profile_path, fidelity_path, diversity_path, campaign_path, draft_path, model_identity_path, commerce_path, video_path):
            archive.write(path, arcname=path.name)
        export_root = job_dir / "platform-exports"
        if export_root.is_dir():
            for path in sorted(export_root.glob("*.png")):
                archive.write(path, arcname=str(path.relative_to(job_dir)))
        if manifest["spec"]["draft_to_final"]["enabled"] and manifest["spec"]["draft_to_final"]["include_drafts"]:
            draft_root = job_dir / "drafts"
            if draft_root.is_dir():
                for path in sorted(draft_root.glob("draft-*")):
                    archive.write(path, arcname=str(path.relative_to(job_dir)))
    return archive_path


def create_manifest(spec: dict[str, Any], job_id: str) -> dict[str, Any]:
    now = int(time.time())
    return {
        "rendriva_version": VERSION,
        "job_id": job_id,
        "created_at": now,
        "updated_at": now,
        "fidelity": {
            "mode": spec["fidelity_mode"],
            "strategy": (
                "literal-source-composite"
                if spec["locked_layers"]
                else "strict-generative-reference"
                if spec["fidelity_mode"] == "strict" and spec["reference_images"]
                else "guided-reference"
                if spec["fidelity_mode"] == "guided"
                else "none"
            ),
            "literal_source_preservation": bool(spec["locked_layers"]),
        },
        "brand_palette": {
            "colors": spec["brand"].get("palette", []),
            "source": spec["brand"].get("palette_source", "none"),
            "sources": spec["brand"].get("palette_sources", []),
        },
        "reference_intelligence": {
            "assets": spec["reference_assets"],
            "identity_packs": spec["identity_packs"],
        },
        "campaign": spec["campaign"],
        "marketplace": spec["marketplace"],
        "product_truth_map": spec["product_truth_map"],
        "draft_to_final": spec["draft_to_final"],
        "sku_variant_matrix": spec["sku_variant_matrix"],
        "garment_construction_lock": spec["garment_construction_lock"],
        "product_visibility_guard": spec["product_visibility_guard"],
        "reference_preflight": spec["reference_preflight"],
        "localized_repair": spec["localized_repair"],
        "sku_color_guard": spec["sku_color_guard"],
        "shot_director": spec["shot_director"],
        "approval_checkpoint": spec["approval_checkpoint"],
        "defect_memory": copy.deepcopy(spec["defect_memory"]),
        "video_continuity_pack": spec["video_continuity_pack"],
        "model_identity_lock": {
            **spec["model_identity_lock"],
            "status": "ACTIVE" if spec["model_identity_lock"].get("source_path") else "PENDING" if spec["model_identity_lock"]["enabled"] else "DISABLED",
            "anchor_file": spec["model_identity_lock"].get("source_path"),
            "anchor_sha256": spec["model_identity_lock"].get("source_sha256"),
            "anchor_output_index": None,
        },
        "spec": spec,
        "outputs": build_plan(spec),
    }


def execute(
    spec: dict[str, Any],
    output_root: Path,
    provider: Any,
    *,
    resume: bool = False,
    use_vision_judge: bool = True,
    progress: Callable[[str], None] = print,
) -> tuple[Path, dict[str, Any]]:
    job_id = stable_job_id(spec)
    job_dir = output_root.resolve() / f"rendriva-{job_id}"
    manifest_path = job_dir / "manifest.json"
    if manifest_path.exists():
        if not resume:
            raise RendrivaError(f"This job already exists at {job_dir}. Use --resume to avoid duplicate generation.")
        manifest = json_load(manifest_path)
        if manifest.get("job_id") != job_id:
            raise RendrivaError("Existing manifest does not match the normalized job specification.")
    else:
        job_dir.mkdir(parents=True, exist_ok=True)
        manifest = create_manifest(spec, job_id)
        json_dump(manifest_path, manifest)

    manifest["evidence_mode"] = "synthetic-mock" if bool(getattr(provider, "synthetic_evidence", False)) else "vision-provider"

    cancelled = threading.Event()

    def cancel_handler(_signum: int, _frame: Any) -> None:
        cancelled.set()
        progress("Cancellation requested; preserving completed outputs and manifest.")

    previous_handlers = {}
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, cancel_handler)

    context = RunContext(spec, job_dir, manifest, provider, use_vision_judge, threading.Lock(), cancelled, progress)
    try:
        run_draft_to_final(context)
        run_generation(context)
        run_campaign_vision_lock(context)
        context.persist()
        package_outputs(job_dir, manifest)
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    return job_dir, manifest


class MockProvider:
    """Deterministic provider used only by tests and the explicit --mock option."""

    synthetic_evidence = True

    def create(self, spec: dict[str, Any], prompt: str, n: int = 1) -> list[bytes]:
        if Image is None:
            raise RendrivaError("Pillow is required for mock generation.")
        size = (1024, 1024) if spec["size"] == "auto" else tuple(map(int, spec["size"].split("x")))
        images = []
        for index in range(n):
            mode = "RGBA" if spec["background"] == "transparent" else "RGB"
            prompt_seed = int(hashlib.sha256(f"{prompt}|{index}".encode()).hexdigest()[:8], 16)
            shift_x = ((index % 5) - 2) * int(size[0] * 0.055)
            shift_y = ((index // 5) - 1) * int(size[1] * 0.055)
            neutral = 225 + (prompt_seed % 20)
            color = (neutral, max(0, neutral - 3), max(0, neutral - 11), 0) if mode == "RGBA" else (neutral, max(0, neutral - 3), max(0, neutral - 11))
            image = Image.new(mode, size, color)
            draw = ImageDraw.Draw(image)
            left = int(size[0] * 0.16) + shift_x
            top = int(size[1] * (0.18 + (index % 3) * 0.025)) + shift_y
            right = int(size[0] * (0.72 + (index % 4) * 0.045)) + shift_x
            bottom = int(size[1] * 0.80) + shift_y
            accent = (30 + prompt_seed % 55, 35 + (prompt_seed >> 5) % 45, 45 + (prompt_seed >> 10) % 55)
            draw.rounded_rectangle((left, top, right, bottom), radius=max(8, size[0] // 40), fill=(*accent, 255) if mode == "RGBA" else accent)
            stream = io.BytesIO()
            target_format = {"png": "PNG", "jpeg": "JPEG", "webp": "WEBP"}[spec["format"]]
            if target_format == "JPEG" and mode == "RGBA":
                image = image.convert("RGB")
            image.save(stream, format=target_format)
            images.append(stream.getvalue())
        return images

    def judge(self, spec: dict[str, Any], image_path: Path, prompt: str) -> dict[str, Any]:
        return {
            "gates_pass": True,
            "collage_violation": False,
            "instruction_following": True,
            "reference_preservation": True,
            "text_correctness": True,
            "model_identity_match": True,
            "model_identity_confidence": 1.0,
            "model_identity_observations": "Mock face-identity comparison passed.",
            "sku_variant_match": True,
            "garment_construction_match": True,
            "product_visibility_ratio": 1.0,
            "product_visibility_pass": True,
            "sku_color_match": True,
            "anatomy_quality": True,
            "localized_repair_scope_preserved": True,
            "region_fidelity": [
                {"name": region["name"], "passed": True, "confidence": 1.0, "observations": "Mock truth-region comparison passed."}
                for region in spec["product_truth_map"]["regions"]
            ],
            "scores": {name: 4.5 for name in SCORED_DIMENSIONS},
            "defects": [],
            "repair_prompt": "",
            "summary": "Mock professional review passed.",
        }

    def promote(self, spec: dict[str, Any], prompt: str, draft_path: Path, n: int = 1) -> list[bytes]:
        return self.create(spec, f"{prompt}|promoted-from={draft_path.name}", n=n)

    def create_with_identity(
        self,
        spec: dict[str, Any],
        prompt: str,
        identity_path: Path,
        *,
        draft_path: Path | None = None,
        n: int = 1,
    ) -> list[bytes]:
        draft_label = draft_path.name if draft_path else "none"
        return self.create(spec, f"{prompt}|identity={identity_path.name}|draft={draft_label}", n=n)

    def repair(self, spec: dict[str, Any], prompt: str, failed_path: Path, n: int = 1) -> list[bytes]:
        return self.create(spec, f"{prompt}|localized-repair-source={failed_path.name}", n=n)

    def judge_campaign(self, spec: dict[str, Any], images: list[tuple[int, Path]]) -> dict[str, Any]:
        return {
            "passed": True,
            "scores": {name: 4.5 for name in CAMPAIGN_VISION_DIMENSIONS},
            "outlier_indices": [],
            "defects_by_index": [],
            "repair_prompts": [],
            "summary": "Mock cross-image campaign review passed.",
        }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate 1-10 separate, professionally judged images.")
    parser.add_argument("job", type=Path, help="Path to a Rendriva JSON job specification")
    parser.add_argument("--output", type=Path, default=Path("./rendriva-runs"), help="Output root directory")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print the normalized plan without API calls")
    parser.add_argument("--resume", action="store_true", help="Resume an existing job and skip passing outputs")
    parser.add_argument("--no-vision-judge", action="store_true", help="Run deterministic structural checks only")
    parser.add_argument("--mock", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--version", action="version", version=f"Rendriva {VERSION}")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        raw = json_load(args.job)
        spec = normalize_job(raw, args.job.parent)
        plan = build_plan(spec)
        if args.dry_run:
            print(json.dumps({"job_id": stable_job_id(spec), "spec": spec, "plan": plan}, indent=2, ensure_ascii=False))
            return 0
        if spec["count"] == 10 and spec["quality"] == "high":
            print("NOTICE: This run requests 10 high-quality images and may incur substantial API usage.", file=sys.stderr)
        provider = MockProvider() if args.mock else OpenAIProvider(os.environ.get("OPENAI_API_KEY", ""))
        job_dir, manifest = execute(
            spec,
            args.output,
            provider,
            resume=args.resume,
            use_vision_judge=not args.no_vision_judge,
        )
        report = quality_report(manifest)
        print(json.dumps({"job_dir": str(job_dir), **report}, indent=2, ensure_ascii=False))
        campaign_required = spec["campaign"]["vision_lock"]["enabled"] and spec["count"] > 1
        campaign_ok = bool((report.get("campaign_visual_review") or {}).get("passed"))
        return 0 if report["counts"]["FAILED"] == 0 and report["counts"]["BLOCKED"] == 0 and (not campaign_required or campaign_ok) else 2
    except (RendrivaError, OSError) as exc:
        print(f"Rendriva error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
