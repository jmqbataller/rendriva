"""Commerce-production controls for Rendriva v1.6."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None


SHOT_TYPES = (
    "hero",
    "full-body-front",
    "three-quarter",
    "back-view",
    "product-detail",
    "lifestyle",
    "fabric-detail",
    "side-view",
    "catalogue-front",
    "campaign-finale",
)


def _sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _resolve(value: str, base_dir: Path, label: str) -> str:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    if not path.is_file():
        raise ValueError(f"{label} not found: {value}")
    return str(path)


def _string_list(value: Any, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{label} must be a list of non-empty strings.")
    return [item.strip() for item in value]


def normalize_sku_variant_matrix(
    value: Any,
    reference_assets: list[dict[str, Any]],
    count: int,
    base_dir: Path,
) -> dict[str, Any]:
    if value is None or value is False:
        return {"enabled": False, "variants": [], "assignments": [], "require_every_variant": False}
    if not isinstance(value, dict):
        raise ValueError("sku_variant_matrix must be false or an object.")
    raw_variants = value.get("variants", [])
    if not isinstance(raw_variants, list) or not raw_variants or any(not isinstance(item, dict) for item in raw_variants):
        raise ValueError("sku_variant_matrix.variants must be a non-empty list of objects.")
    asset_by_identity = {
        asset.get("identity_id"): asset
        for asset in reference_assets
        if asset.get("identity_id") and asset.get("role") in {"product", "identity"}
    }
    variants: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, raw in enumerate(raw_variants, start=1):
        variant_id = str(raw.get("id", raw.get("sku", ""))).strip()
        if not variant_id or variant_id in ids:
            raise ValueError(f"sku_variant_matrix.variants[{index}].id must be unique and non-empty.")
        ids.add(variant_id)
        identity_id = str(raw.get("identity_id", "")).strip()
        source_value = raw.get("source_path", raw.get("source"))
        asset = asset_by_identity.get(identity_id)
        if source_value is not None:
            if not isinstance(source_value, str) or not source_value.strip():
                raise ValueError(f"sku_variant_matrix.variants[{index}].source_path must be a non-empty string.")
            source_path = _resolve(source_value, base_dir, "SKU source")
        elif asset:
            source_path = asset["path"]
        else:
            raise ValueError(f"SKU variant '{variant_id}' needs source_path or a matching product identity_id.")
        output_indices = raw.get("output_indices", [])
        if output_indices and (
            not isinstance(output_indices, list)
            or any(isinstance(item, bool) or not isinstance(item, int) or not 1 <= item <= count for item in output_indices)
        ):
            raise ValueError(f"sku_variant_matrix.variants[{index}].output_indices must use integers from 1 through {count}.")
        variants.append(
            {
                "id": variant_id,
                "label": str(raw.get("label", variant_id)),
                "identity_id": identity_id,
                "source_path": source_path,
                "source_sha256": _sha256(source_path),
                "expected_color": str(raw.get("expected_color", raw.get("color", "unspecified"))),
                "preserve": _string_list(raw.get("preserve"), f"sku_variant_matrix.variants[{index}].preserve"),
                "output_indices": list(output_indices),
            }
        )
    assignments: dict[int, str] = {}
    for variant in variants:
        for output_index in variant["output_indices"]:
            if output_index in assignments:
                raise ValueError(f"Output {output_index} is assigned to more than one SKU variant.")
            assignments[output_index] = variant["id"]
    unassigned = [index for index in range(1, count + 1) if index not in assignments]
    if unassigned:
        if len(variants) == count and not any(variant["output_indices"] for variant in variants):
            assignments = {index: variants[index - 1]["id"] for index in range(1, count + 1)}
        elif bool(value.get("auto_cycle", False)):
            for index in unassigned:
                assignments[index] = variants[(index - 1) % len(variants)]["id"]
        else:
            raise ValueError(
                "Every output must map to exactly one SKU variant. Supply output_indices, use one variant per output, or set auto_cycle=true."
            )
    require_every = bool(value.get("require_every_variant", True))
    if require_every:
        missing = sorted(ids - set(assignments.values()))
        if missing:
            raise ValueError(f"SKU variants have no assigned output: {', '.join(missing)}.")
    by_id = {variant["id"]: variant for variant in variants}
    return {
        "enabled": True,
        "variants": variants,
        "assignments": [
            {
                "output_index": index,
                "variant_id": assignments[index],
                "source_path": by_id[assignments[index]]["source_path"],
                "source_sha256": by_id[assignments[index]]["source_sha256"],
                "expected_color": by_id[assignments[index]]["expected_color"],
            }
            for index in range(1, count + 1)
        ],
        "require_every_variant": require_every,
    }


def normalize_garment_construction_lock(value: Any, enabled_default: bool) -> dict[str, Any]:
    if value is None:
        value = {"enabled": enabled_default}
    if isinstance(value, bool):
        value = {"enabled": value}
    if not isinstance(value, dict):
        raise ValueError("garment_construction_lock must be a boolean or object.")
    fields = _string_list(value.get("fields"), "garment_construction_lock.fields") or [
        "neckline", "sleeves", "hem", "seams", "stitching", "pockets", "buttons",
        "straps", "silhouette", "length", "fit", "print placement", "label placement",
    ]
    return {
        "enabled": bool(value.get("enabled", enabled_default)),
        "fields": fields,
        "forbid_unseen_construction": bool(value.get("forbid_unseen_construction", True)),
        "flat_chest_when_flatlay": bool(value.get("flat_chest_when_flatlay", True)),
    }


def normalize_visibility_guard(value: Any, enabled_default: bool) -> dict[str, Any]:
    if value is None:
        value = {"enabled": enabled_default}
    if isinstance(value, bool):
        value = {"enabled": value}
    if not isinstance(value, dict):
        raise ValueError("product_visibility_guard must be a boolean or object.")
    ratio = float(value.get("min_visible_ratio", 0.85))
    if not 0 <= ratio <= 1:
        raise ValueError("product_visibility_guard.min_visible_ratio must be from 0 through 1.")
    return {
        "enabled": bool(value.get("enabled", enabled_default)),
        "min_visible_ratio": ratio,
        "protected_details": _string_list(value.get("protected_details"), "product_visibility_guard.protected_details")
        or ["logo", "print", "neckline", "hem", "silhouette", "label"],
        "blockers": _string_list(value.get("blockers"), "product_visibility_guard.blockers")
        or ["hands", "hair", "arms", "props", "accessories", "cropping"],
    }


def build_reference_preflight(value: Any, reference_assets: list[dict[str, Any]]) -> dict[str, Any]:
    if value is False:
        return {"enabled": False, "passed": True, "blocking": False, "checks": [], "issues": []}
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("reference_preflight must be false or an object.")
    min_edge = int(value.get("min_edge", 512))
    if min_edge < 64:
        raise ValueError("reference_preflight.min_edge must be at least 64.")
    checks: list[dict[str, Any]] = []
    issues: list[str] = []
    for asset in reference_assets:
        record = {"path": asset["path"], "role": asset["role"], "readable": True, "width": None, "height": None, "issues": []}
        if Image is None:
            record["issues"].append("Pillow unavailable; image dimensions were not inspected.")
        else:
            try:
                with Image.open(asset["path"]) as image:
                    record["width"], record["height"] = image.size
                    image.verify()
                if min(record["width"], record["height"]) < min_edge:
                    record["issues"].append(f"Shortest edge is below {min_edge}px.")
            except Exception as exc:
                record["readable"] = False
                record["issues"].append(f"Image cannot be decoded: {exc}")
        if record["issues"]:
            issues.extend(f"{Path(asset['path']).name}: {issue}" for issue in record["issues"])
        checks.append(record)
    blocking = bool(value.get("blocking", False))
    passed = not issues
    return {
        "enabled": True,
        "passed": passed,
        "blocking": blocking,
        "min_edge": min_edge,
        "checks": checks,
        "issues": issues,
        "recommendations": ["Upload a sharp, unobscured source with the required product angle and readable logo/label."] if issues else [],
    }


def normalize_localized_repair(value: Any) -> dict[str, Any]:
    if value is None:
        value = {}
    if isinstance(value, bool):
        value = {"enabled": value}
    if not isinstance(value, dict):
        raise ValueError("localized_repair must be a boolean or object.")
    return {
        "enabled": bool(value.get("enabled", True)),
        "zones": _string_list(value.get("zones"), "localized_repair.zones") or ["hands", "face", "hair", "body anatomy", "background", "shadow", "text-safe space"],
        "rollback_on_lock_drift": bool(value.get("rollback_on_lock_drift", True)),
        "preserve_passing_regions": bool(value.get("preserve_passing_regions", True)),
    }


def normalize_color_guard(value: Any, enabled_default: bool) -> dict[str, Any]:
    if value is None:
        value = {"enabled": enabled_default}
    if isinstance(value, bool):
        value = {"enabled": value}
    if not isinstance(value, dict):
        raise ValueError("sku_color_guard must be a boolean or object.")
    tolerance = float(value.get("delta_e_tolerance", 6.0))
    if not 0 <= tolerance <= 100:
        raise ValueError("sku_color_guard.delta_e_tolerance must be from 0 through 100.")
    return {
        "enabled": bool(value.get("enabled", enabled_default)),
        "delta_e_tolerance": tolerance,
        "separate_lighting_from_product_color": bool(value.get("separate_lighting_from_product_color", True)),
        "neutral_reference_required": bool(value.get("neutral_reference_required", False)),
    }


def normalize_shot_director(value: Any, count: int) -> dict[str, Any]:
    if value is None or value is False:
        return {"enabled": False, "shots": []}
    if value is True:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("shot_director must be a boolean or object.")
    requested = value.get("shots", list(SHOT_TYPES[:count]))
    if not isinstance(requested, list) or any(str(item) not in SHOT_TYPES for item in requested):
        raise ValueError(f"shot_director.shots must use: {', '.join(SHOT_TYPES)}.")
    if not requested:
        raise ValueError("shot_director.shots cannot be empty when enabled.")
    shots = [str(requested[(index - 1) % len(requested)]) for index in range(1, count + 1)]
    return {"enabled": True, "shots": [{"output_index": index, "shot": shot} for index, shot in enumerate(shots, start=1)]}


def normalize_approval_checkpoint(value: Any, count: int) -> dict[str, Any]:
    if value is None or value is False:
        return {"enabled": False, "approved": True, "anchor_output_index": 1, "pending_outputs": []}
    if value is True:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("approval_checkpoint must be a boolean or object.")
    anchor = int(value.get("anchor_output_index", 1))
    if not 1 <= anchor <= count:
        raise ValueError(f"approval_checkpoint.anchor_output_index must be from 1 through {count}.")
    approved = bool(value.get("approved", False))
    return {
        "enabled": True,
        "approved": approved,
        "anchor_output_index": anchor,
        "pending_outputs": [] if approved else [index for index in range(1, count + 1) if index != anchor],
        "approval_note": str(value.get("approval_note", "Approve the anchor model, product accuracy, styling, and framing before finalizing the batch.")),
    }


def normalize_defect_memory(value: Any) -> dict[str, Any]:
    if value is None:
        value = {}
    if isinstance(value, bool):
        value = {"enabled": value}
    if not isinstance(value, dict):
        raise ValueError("defect_memory must be a boolean or object.")
    return {
        "enabled": bool(value.get("enabled", True)),
        "entries": _string_list(value.get("entries"), "defect_memory.entries"),
        "max_entries": int(value.get("max_entries", 30)),
    }


def normalize_video_continuity(value: Any, enabled_default: bool) -> dict[str, Any]:
    if value is None:
        value = {"enabled": enabled_default}
    if isinstance(value, bool):
        value = {"enabled": value}
    if not isinstance(value, dict):
        raise ValueError("video_continuity_pack must be a boolean or object.")
    return {
        "enabled": bool(value.get("enabled", enabled_default)),
        "aspect_ratio": str(value.get("aspect_ratio", "9:16")),
        "motion_style": str(value.get("motion_style", "smooth premium commercial movement")),
        "preserve_first_to_last_frame": bool(value.get("preserve_first_to_last_frame", True)),
        "no_overlay_text": bool(value.get("no_overlay_text", True)),
    }


def normalize_commerce_suite(raw: dict[str, Any], reference_assets: list[dict[str, Any]], count: int, preset: str, base_dir: Path) -> dict[str, Any]:
    sku = normalize_sku_variant_matrix(raw.get("sku_variant_matrix"), reference_assets, count, base_dir)
    garment_default = sku["enabled"] or (preset in {"fashion-model", "apparel-flatlay"} and bool(reference_assets))
    visibility_default = sku["enabled"] or (preset in {"fashion-model", "apparel-flatlay", "product-photography"} and bool(reference_assets))
    preflight = build_reference_preflight(raw.get("reference_preflight"), reference_assets)
    if preflight["blocking"] and not preflight["passed"]:
        raise ValueError("Reference preflight failed: " + "; ".join(preflight["issues"]))
    return {
        "sku_variant_matrix": sku,
        "garment_construction_lock": normalize_garment_construction_lock(raw.get("garment_construction_lock"), garment_default),
        "product_visibility_guard": normalize_visibility_guard(raw.get("product_visibility_guard"), visibility_default),
        "reference_preflight": preflight,
        "localized_repair": normalize_localized_repair(raw.get("localized_repair")),
        "sku_color_guard": normalize_color_guard(raw.get("sku_color_guard"), sku["enabled"]),
        "shot_director": normalize_shot_director(raw.get("shot_director"), count),
        "approval_checkpoint": normalize_approval_checkpoint(raw.get("approval_checkpoint"), count),
        "defect_memory": normalize_defect_memory(raw.get("defect_memory")),
        "video_continuity_pack": normalize_video_continuity(raw.get("video_continuity_pack"), False),
    }


def commerce_assignment(spec: dict[str, Any], output_index: int) -> dict[str, Any] | None:
    return next((item for item in spec["sku_variant_matrix"]["assignments"] if item["output_index"] == output_index), None)


def shot_assignment(spec: dict[str, Any], output_index: int) -> str | None:
    record = next((item for item in spec["shot_director"]["shots"] if item["output_index"] == output_index), None)
    return record["shot"] if record else None


def build_commerce_report(manifest: dict[str, Any]) -> dict[str, Any]:
    spec = manifest["spec"]
    outputs = []
    for item in manifest["outputs"]:
        vision = (item.get("quality") or {}).get("vision") or {}
        outputs.append(
            {
                "index": item["index"],
                "file": item["file"],
                "status": item["status"],
                "sku_assignment": item.get("sku_assignment"),
                "shot": item.get("shot"),
                "sku_variant_match": vision.get("sku_variant_match"),
                "garment_construction_match": vision.get("garment_construction_match"),
                "product_visibility_ratio": vision.get("product_visibility_ratio"),
                "product_visibility_pass": vision.get("product_visibility_pass"),
                "sku_color_match": vision.get("sku_color_match"),
                "anatomy_quality": vision.get("anatomy_quality"),
                "repair_rollback_protected": bool(item.get("repair_rollback_protected", True)),
            }
        )
    return {
        "job_id": manifest["job_id"],
        "sku_variant_matrix": spec["sku_variant_matrix"],
        "garment_construction_lock": spec["garment_construction_lock"],
        "product_visibility_guard": spec["product_visibility_guard"],
        "reference_preflight": spec["reference_preflight"],
        "sku_color_guard": spec["sku_color_guard"],
        "approval_checkpoint": manifest.get("approval_checkpoint", spec["approval_checkpoint"]),
        "defect_memory": manifest.get("defect_memory", spec["defect_memory"]),
        "outputs": outputs,
    }


def build_video_continuity_report(manifest: dict[str, Any]) -> dict[str, Any]:
    policy = manifest["spec"]["video_continuity_pack"]
    keyframes = []
    for item in manifest["outputs"]:
        if item["status"] != "PASS":
            continue
        assignment = item.get("sku_assignment") or {}
        keyframes.append(
            {
                "index": item["index"],
                "file": item["file"],
                "shot": item.get("shot"),
                "variant_id": assignment.get("variant_id"),
                "source_sha256": assignment.get("source_sha256"),
                "motion_prompt": (
                    f"Animate {item.get('shot') or 'the approved composition'} with {policy['motion_style']}; preserve the same model face, exact SKU, fabric, texture, construction, print, logo, color, and background continuity from first to last frame."
                ),
            }
        )
    return {"enabled": policy["enabled"], "policy": policy, "keyframes": keyframes}
