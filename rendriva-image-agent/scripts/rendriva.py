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
    from PIL import Image, ImageColor, ImageDraw, ImageFont
except ImportError:  # pragma: no cover - handled with a focused runtime error
    Image = ImageColor = ImageDraw = ImageFont = None


VERSION = "1.2.0"
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
    references: list[str],
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
        palette_sources = list(dict.fromkeys(logo_layers + references + other_layers))

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
        layer = {
            "path": resolved_path,
            "role": str(raw_layer.get("role", "product")),
            "x": float(raw_layer.get("x", 0.5)),
            "y": float(raw_layer.get("y", 0.5)),
            "max_width": float(raw_layer.get("max_width", 0.45)),
            "max_height": float(raw_layer.get("max_height", 0.75)),
            "anchor": raw_layer.get("anchor", "center"),
            "require_alpha": bool(raw_layer.get("require_alpha", True)),
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

    operation = raw.get("operation", "edit" if raw.get("reference_images") else "generate")
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

    references = resolve_paths(as_string_list(raw.get("reference_images"), "reference_images"), base_dir)
    locked_layers = normalize_locked_layers(raw.get("locked_layers"), base_dir)
    if references and locked_layers:
        raise ValidationError("Use either reference_images for generative fidelity or locked_layers for literal compositing, not both.")
    if locked_layers and operation != "generate":
        raise ValidationError("locked_layers require operation='generate' because the source pixels are composited after background generation.")
    if operation in {"edit", "variation"} and not references:
        raise ValidationError(f"operation='{operation}' requires at least one reference image.")

    fidelity_mode = str(raw.get("fidelity_mode", "strict" if references or locked_layers else "none"))
    if fidelity_mode not in VALID_FIDELITY_MODES:
        raise ValidationError(f"fidelity_mode must be one of: {', '.join(sorted(VALID_FIDELITY_MODES))}.")
    if fidelity_mode != "none" and not references and not locked_layers:
        raise ValidationError("fidelity_mode requires reference_images or locked_layers.")
    preserve = as_string_list(raw.get("preserve"), "preserve")
    if fidelity_mode == "strict":
        preserve = list(dict.fromkeys(preserve + STRICT_ASSET_LOCKS))

    brand = normalize_brand(raw.get("brand"), base_dir, references, locked_layers)
    text_layers = raw.get("text_layers", [])
    if not isinstance(text_layers, list) or any(not isinstance(item, dict) for item in text_layers):
        raise ValidationError("text_layers must be a list of objects.")

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
            raise ValidationError(
                "gpt-image-2 custom sizes require a maximum edge of 3840px and 655,360 through 8,294,400 total pixels."
            )

    normalized = {
        "prompt": prompt.strip(),
        "count": count,
        "mode": mode,
        "scenes": scenes,
        "operation": operation,
        "preset": preset,
        "size": normalized_size,
        "quality": quality,
        "format": output_format,
        "background": background,
        "reference_images": references,
        "locked_layers": locked_layers,
        "fidelity_mode": fidelity_mode,
        "preserve": preserve,
        "avoid": as_string_list(raw.get("avoid"), "avoid"),
        "brand": brand,
        "professional_designer_mode": bool(raw.get("professional_designer_mode", True)),
        "text_safe_mode": bool(raw.get("text_safe_mode", bool(text_layers))),
        "text_layers": text_layers,
        "max_repair_attempts": max_repairs,
        "min_professional_score": float(threshold),
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
        for key in ("x", "y", "max_width"):
            if key in layer:
                value = layer[key]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= float(value) <= 1:
                    raise ValidationError(f"text_layers[{index}].{key} must be from 0 through 1.")
        if layer.get("align", "left") not in {"left", "center", "right"}:
            raise ValidationError(f"text_layers[{index}].align must be left, center, or right.")
        font_size = layer.get("font_size", 64)
        if isinstance(font_size, bool) or not isinstance(font_size, int) or font_size <= 0:
            raise ValidationError(f"text_layers[{index}].font_size must be a positive integer.")


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
        strategy = "literal source compositing" if spec["locked_layers"] else "strict reference editing with mandatory comparison QA"
        fidelity_rule = (
            f"Use {strategy}. The supplied product, garment, artwork, or logo is authoritative and protected. "
            "Do not redraw, reinterpret, retouch, recolor, reshape, restyle, retexture, smooth, sharpen, replace, or invent any protected detail. "
            "Keep fabric weave, fibers, stitching, seams, folds, finish, print, color, silhouette, proportions, labels, and product construction unchanged. "
            "Keep every logo's exact symbol, lettering, geometry, spacing, color, aspect ratio, and placement unchanged; never approximate it with generated text. "
            "If the requested transformation conflicts with a protected asset, preserve the asset and modify only the background, staging, lighting environment, layout, or unprotected region."
        )
    if item.get("batch_variations"):
        scene = (
            f"Create {spec['count']} independent professional variations across the provider response. "
            "Every returned item must remain one standalone image."
        )
    else:
        scene = item.get("scene") or f"Create an independent professional variation {item['index']} of {spec['count']}."
    repair_block = ""
    if repair:
        defects = repair.get("defects") or [repair.get("reason", "The previous output failed quality review.")]
        repair_block = (
            "\nREPAIR DIRECTIVE:\n"
            f"Correct these observed defects: {_join([str(value) for value in defects])}.\n"
            f"Use this targeted correction: {repair.get('repair_prompt', 'Correct the defects without changing locked details.')}\n"
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

    def judge(self, spec: dict[str, Any], image_path: Path, prompt: str) -> dict[str, Any]:
        mime = mimetypes.guess_type(image_path.name)[0] or "image/png"
        image_url = f"data:{mime};base64,{base64.b64encode(image_path.read_bytes()).decode()}"
        schema = quality_schema()
        content: list[dict[str, Any]] = [{"type": "input_text", "text": quality_prompt(spec, prompt)}]
        judge_references = list(spec["reference_images"]) + [layer["path"] for layer in spec["locked_layers"]]
        if judge_references:
            content.append(
                {
                    "type": "input_text",
                    "text": "The next image or images are authoritative source references. Compare identity, color, materials, proportions, artwork, logos, and every stated preservation lock against them.",
                }
            )
            for reference in judge_references:
                reference_path = Path(reference)
                reference_mime = mimetypes.guess_type(reference_path.name)[0] or "image/png"
                reference_url = f"data:{reference_mime};base64,{base64.b64encode(reference_path.read_bytes()).decode()}"
                content.append({"type": "input_image", "image_url": reference_url})
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
            "scores",
            "defects",
            "repair_prompt",
            "summary",
        ],
    }


def quality_prompt(spec: dict[str, Any], generation_prompt: str) -> str:
    expected_text = [layer["text"] for layer in spec["text_layers"]]
    fidelity_gate = "No reference-fidelity gate applies."
    if spec["fidelity_mode"] == "guided":
        fidelity_gate = "Assess reference similarity as guidance and report material drift, but do not require literal source identity."
    elif spec["fidelity_mode"] == "strict":
        fidelity_gate = (
            "STRICT SOURCE-ASSET GATE: mark reference_preservation false and fail the gates for any change to product silhouette, proportions, construction, fabric weave, fibers, texture, finish, stitching, seams, folds, print, color, label, or logo. "
            "A logo fails for altered symbol geometry, lettering, spelling, spacing, color, aspect ratio, placement, cropping, distortion, redraw, or generated approximation. "
            "Do not excuse drift because the result is visually attractive."
        )
    palette_gate = ""
    if spec["brand"].get("palette_source") == "auto-reference":
        palette_gate = (
            f"REFERENCE-DERIVED BRAND PALETTE: {_join(spec['brand']['palette'])}. "
            "Judge whether unprotected backgrounds, accents, typography, and design elements use this palette coherently. "
            "Penalize unrelated dominant colors, but do not require or permit recoloring any protected source asset."
        )
    return f"""Act as a strict senior design director and production QA reviewer. Evaluate the final attached image against the generation brief and any authoritative source references supplied after this instruction.

BRIEF:
{generation_prompt}

Fail the gates if the image is a collage/grid/multi-panel output, misses required content, changes locked reference details, contains severe visual artifacts, crops the main subject unintentionally, invents prohibited brand/product details, or renders critical text incorrectly. The exact required text strings are: {_join(expected_text)}. If no reference or critical text applies, mark that gate true.

{fidelity_gate}

{palette_gate}

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


def fit_text(draw: Any, text: str, font_path: str | None, requested_size: int, max_width: int) -> tuple[Any, int, bool]:
    size = requested_size
    fallback = False
    while size >= 10:
        try:
            font = ImageFont.truetype(font_path, size) if font_path else ImageFont.truetype(find_default_font(), size)
            fallback = not bool(font_path)
        except (OSError, TypeError):
            font = ImageFont.load_default(size=size)
            fallback = True
        bbox = draw.multiline_textbbox((0, 0), text, font=font, spacing=max(4, size // 5))
        if bbox[2] - bbox[0] <= max_width:
            return font, size, fallback
        size -= 2
    raise RendrivaError(f"Text layer cannot fit within {max_width}px: {text[:80]}")


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
        canvas.alpha_composite(foreground, dest=(x, y))
        applied.append(
            {
                "path": layer["path"],
                "role": layer["role"],
                "source_sha256": hashlib.sha256(Path(layer["path"]).read_bytes()).hexdigest(),
                "position": [x, y],
                "size": [target[0], target[1]],
                "source_alpha": has_alpha,
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
    for layer in layers:
        x = int(float(layer.get("x", 0.08)) * canvas.width)
        y = int(float(layer.get("y", 0.08)) * canvas.height)
        max_width = int(float(layer.get("max_width", 0.84)) * canvas.width)
        font_path = layer.get("font_path")
        if font_path:
            font_path = str(Path(font_path).expanduser().resolve())
            if not Path(font_path).is_file():
                raise RendrivaError(f"Font file not found: {font_path}")
        font, actual_size, fallback = fit_text(draw, layer["text"], font_path, int(layer.get("font_size", 64)), max_width)
        used_fallback = used_fallback or fallback
        color = ImageColor.getcolor(str(layer.get("color", "#111111")), "RGBA")
        align = layer.get("align", "left")
        anchor = {"left": "la", "center": "ma", "right": "ra"}[align]
        if align == "center":
            x += max_width // 2
        elif align == "right":
            x += max_width
        draw.multiline_text(
            (x, y),
            layer["text"],
            font=font,
            fill=color,
            anchor=anchor,
            align=align,
            spacing=max(4, actual_size // 5),
            stroke_width=int(layer.get("stroke_width", 0)),
            stroke_fill=ImageColor.getcolor(str(layer.get("stroke_color", "#000000")), "RGBA"),
        )
    output_format = image_path.suffix.lower().lstrip(".")
    if output_format in {"jpg", "jpeg"}:
        canvas.convert("RGB").save(image_path, format="JPEG", quality=95)
    else:
        canvas.save(image_path, format=output_format.upper())
    return {"applied": True, "font_fallback": used_fallback}


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
            spec["fidelity_mode"] == "strict" and bool(spec["reference_images"]) and not spec["locked_layers"]
        )
        if strict_reference_unverified:
            return {
                "passed": False,
                "average_score": None,
                "defects": ["Strict generative reference fidelity was not verified because the vision judge is disabled."],
                "repair_prompt": "Enable reference-aware vision judging or use locked_layers for source-derived compositing.",
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
    if spec["fidelity_mode"] == "strict" and (spec["reference_images"] or spec["locked_layers"]):
        gates = gates and bool(vision.get("reference_preservation"))
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


def review_image(context: RunContext, item: dict[str, Any], prompt: str, image_path: Path) -> dict[str, Any]:
    structural = structural_review(context.spec, image_path)
    vision = None
    if structural["passed"] and context.use_vision_judge:
        vision = context.provider.judge(context.spec, image_path, prompt)
    return finalize_review(context.spec, structural, vision)


def process_item(
    context: RunContext,
    item: dict[str, Any],
    initial_bytes: bytes | None = None,
    actual_prompt: str | None = None,
) -> None:
    if context.cancelled.is_set():
        return
    spec = context.spec
    image_path = context.job_dir / item["file"]
    prompt = actual_prompt or compile_prompt(spec, item)
    item["compiled_prompt"] = prompt
    try:
        item["status"] = "GENERATING"
        item["attempts"] += 1
        context.persist()
        context.progress(f"Image {item['index']}/{spec['count']} — generating")
        payload = initial_bytes if initial_bytes is not None else context.provider.create(spec, prompt, n=1)[0]
        save_image(image_path, payload)
        item["locked_layer_composite"] = apply_locked_layers(image_path, spec["locked_layers"])
        item["text_overlay"] = apply_text_layers(image_path, spec["text_layers"])
        item["status"] = "JUDGING"
        context.persist()
        context.progress(f"Image {item['index']}/{spec['count']} — judging")
        review = review_image(context, item, prompt, image_path)
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
            repaired = context.provider.create(spec, repair_prompt, n=1)[0]
            repair_path = image_path.with_name(f"{image_path.stem}-repair-{item['repair_attempts']}{image_path.suffix}")
            save_image(repair_path, repaired)
            locked_composite = apply_locked_layers(repair_path, spec["locked_layers"])
            overlay = apply_text_layers(repair_path, spec["text_layers"])
            repaired_review = review_image(context, item, repair_prompt, repair_path)
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
        item["quality"] = review
        item["status"] = "FAILED"
        item["error"] = "Quality requirements were not met after allowed repair attempts."
    except Exception as exc:
        item["status"] = "BLOCKED" if isinstance(exc, ProviderError) and "policy" in str(exc).lower() else "FAILED"
        item["error"] = str(exc)
        context.progress(f"Image {item['index']}/{spec['count']} — {item['status'].lower()}: {exc}")
    finally:
        context.persist()


def run_generation(context: RunContext) -> None:
    items = [item for item in context.manifest["outputs"] if item["status"] != "PASS"]
    if not items:
        context.progress("All requested outputs already pass; nothing to resume.")
        return

    spec = context.spec
    can_batch = spec["mode"] == "variations" and spec["operation"] == "generate" and not spec["reference_images"]
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
        for item, payload in zip(items, payloads):
            process_item(context, item, initial_bytes=payload, actual_prompt=common_prompt)
        return

    with ThreadPoolExecutor(max_workers=spec["concurrency"], thread_name_prefix="rendriva") as executor:
        futures = [executor.submit(process_item, context, item) for item in items]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as exc:  # process_item normally contains errors; this is a last guard
                context.progress(f"Unexpected worker failure: {exc}")


def quality_report(manifest: dict[str, Any]) -> dict[str, Any]:
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
            }
        )
    return {"job_id": manifest["job_id"], "counts": counts, "outputs": results}


def package_outputs(job_dir: Path, manifest: dict[str, Any]) -> Path:
    report_path = job_dir / "quality-report.json"
    json_dump(report_path, quality_report(manifest))
    archive_path = job_dir / "rendriva-output.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in manifest["outputs"]:
            if item["status"] == "PASS":
                image_path = job_dir / item["file"]
                if image_path.is_file():
                    archive.write(image_path, arcname=image_path.name)
        archive.write(job_dir / "manifest.json", arcname="manifest.json")
        archive.write(report_path, arcname="quality-report.json")
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
        run_generation(context)
        context.persist()
        package_outputs(job_dir, manifest)
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    return job_dir, manifest


class MockProvider:
    """Deterministic provider used only by tests and the explicit --mock option."""

    def create(self, spec: dict[str, Any], prompt: str, n: int = 1) -> list[bytes]:
        if Image is None:
            raise RendrivaError("Pillow is required for mock generation.")
        size = (1024, 1024) if spec["size"] == "auto" else tuple(map(int, spec["size"].split("x")))
        images = []
        for index in range(n):
            mode = "RGBA" if spec["background"] == "transparent" else "RGB"
            color = (238, 235, 227, 0) if mode == "RGBA" else (238, 235, 227)
            image = Image.new(mode, size, color)
            draw = ImageDraw.Draw(image)
            draw.rectangle((size[0] * 0.2, size[1] * 0.2, size[0] * 0.8, size[1] * 0.8), fill=(40, 40, 45, 255) if mode == "RGBA" else (40, 40, 45))
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
            "scores": {name: 4.5 for name in SCORED_DIMENSIONS},
            "defects": [],
            "repair_prompt": "",
            "summary": "Mock professional review passed.",
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
        return 0 if report["counts"]["FAILED"] == 0 and report["counts"]["BLOCKED"] == 0 else 2
    except (RendrivaError, OSError) as exc:
        print(f"Rendriva error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
