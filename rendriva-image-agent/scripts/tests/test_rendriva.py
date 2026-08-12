from __future__ import annotations

import base64
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "rendriva.py"
SPEC = importlib.util.spec_from_file_location("rendriva", MODULE_PATH)
rendriva = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = rendriva
SPEC.loader.exec_module(rendriva)


def base_job(**overrides):
    job = {
        "prompt": "Premium commercial product image of a matte black bottle",
        "count": 1,
        "mode": "variations",
        "preset": "product-photography",
        "size": "1024x1024",
        "quality": "high",
        "format": "png",
    }
    job.update(overrides)
    return job


class RecordingProvider(rendriva.MockProvider):
    def __init__(self):
        self.create_calls = []
        self.failed_first_image = False

    def create(self, spec, prompt, n=1):
        self.create_calls.append(n)
        return super().create(spec, prompt, n=n)

    def judge(self, spec, image_path, prompt):
        if image_path.name == "image-01.png" and not self.failed_first_image:
            self.failed_first_image = True
            result = super().judge(spec, image_path, prompt)
            result["gates_pass"] = False
            result["scores"]["composition_spacing"] = 2
            result["defects"] = ["The focal point is weak and the spacing is accidental."]
            result["repair_prompt"] = "Strengthen the focal point and use a disciplined grid."
            return result
        return super().judge(spec, image_path, prompt)


class PartialFailureProvider(rendriva.MockProvider):
    def create(self, spec, prompt, n=1):
        if "Blocked scene" in prompt:
            raise rendriva.ProviderError("Synthetic provider failure")
        return super().create(spec, prompt, n=n)


class DuplicateProvider(rendriva.MockProvider):
    def create(self, spec, prompt, n=1):
        one = super().create(spec, "fixed-output", n=1)[0]
        return [one for _ in range(n)]


class ShortBatchProvider(rendriva.MockProvider):
    def create(self, spec, prompt, n=1):
        return super().create(spec, prompt, n=1)


class CampaignOutlierProvider(rendriva.MockProvider):
    synthetic_evidence = False

    def __init__(self):
        self.campaign_calls = 0

    def judge_campaign(self, spec, images):
        self.campaign_calls += 1
        result = super().judge_campaign(spec, images)
        if self.campaign_calls == 1:
            result["passed"] = False
            result["outlier_indices"] = [2]
            result["defects_by_index"] = [{"index": 2, "defects": ["Logo safe-zone and lighting family drift from the campaign."]}]
            result["repair_prompts"] = [{"index": 2, "prompt": "Restore the shared logo safe zone and lighting family while retaining a distinct composition."}]
        return result


class DraftRecordingProvider(rendriva.MockProvider):
    def __init__(self):
        self.create_calls = []
        self.create_prompts = []
        self.create_text_layers = []
        self.promotions = []

    def create(self, spec, prompt, n=1):
        self.create_calls.append({"quality": spec["quality"], "n": n})
        self.create_prompts.append(prompt)
        self.create_text_layers.append(spec["text_layers"])
        return super().create(spec, prompt, n=n)

    def promote(self, spec, prompt, draft_path, n=1):
        self.promotions.append(draft_path.name)
        return super().promote(spec, prompt, draft_path, n=n)


class IdentityRecordingProvider(rendriva.MockProvider):
    synthetic_evidence = False

    def __init__(self):
        self.creates = []
        self.identity_creates = []

    def create(self, spec, prompt, n=1):
        self.creates.append({"n": n, "quality": spec["quality"]})
        return super().create(spec, prompt, n=n)

    def create_with_identity(self, spec, prompt, identity_path, *, draft_path=None, n=1):
        self.identity_creates.append(
            {"identity": identity_path.name, "draft": draft_path.name if draft_path else None, "n": n, "prompt": prompt}
        )
        return super().create_with_identity(spec, prompt, identity_path, draft_path=draft_path, n=n)


class FaceMismatchProvider(IdentityRecordingProvider):
    def __init__(self):
        super().__init__()
        self.failed_face_once = False

    def judge(self, spec, image_path, prompt):
        result = super().judge(spec, image_path, prompt)
        if image_path.name == "image-02.png" and not self.failed_face_once:
            self.failed_face_once = True
            result["gates_pass"] = False
            result["model_identity_match"] = False
            result["model_identity_confidence"] = 0.22
            result["model_identity_observations"] = "The face is a different individual."
            result["defects"] = ["The model face does not match the identity anchor."]
            result["repair_prompt"] = "Restore the exact anchored face identity while retaining this variant's outfit and pose."
        return result


class RendrivaTests(unittest.TestCase):
    def test_rejects_more_than_ten_outputs(self):
        with self.assertRaises(rendriva.ValidationError):
            rendriva.normalize_job(base_job(count=11))

    def test_ten_outputs_are_separate_and_numbered(self):
        spec = rendriva.normalize_job(base_job(count=10))
        plan = rendriva.build_plan(spec)
        self.assertEqual(len(plan), 10)
        self.assertEqual(plan[0]["file"], "image-01.png")
        self.assertEqual(plan[-1]["file"], "image-10.png")
        self.assertEqual(len({item["file"] for item in plan}), 10)

    def test_scene_count_must_match(self):
        with self.assertRaises(rendriva.ValidationError):
            rendriva.normalize_job(base_job(mode="scenes", count=2, scenes=["Hero view"]))

    def test_compiled_prompt_forbids_collage(self):
        spec = rendriva.normalize_job(base_job(count=2))
        prompt = rendriva.compile_prompt(spec, rendriva.build_plan(spec)[0])
        self.assertIn("exactly one standalone image", prompt.lower())
        self.assertIn("do not create a collage", prompt.lower())
        self.assertIn("professional designer", prompt.lower())

    def test_reference_defaults_to_strict_product_and_logo_fidelity(self):
        with tempfile.TemporaryDirectory() as temporary:
            reference = Path(temporary) / "product-logo.png"
            rendriva.Image.new("RGB", (64, 64), (80, 60, 40)).save(reference)
            spec = rendriva.normalize_job(
                base_job(operation="edit", reference_images=[str(reference)])
            )
        self.assertEqual(spec["fidelity_mode"], "strict")
        self.assertTrue(any("fabric weave" in lock for lock in spec["preserve"]))
        self.assertTrue(any("logo geometry" in lock for lock in spec["preserve"]))
        prompt = rendriva.compile_prompt(spec, rendriva.build_plan(spec)[0])
        self.assertIn("do not redraw", prompt.lower())
        self.assertIn("fabric weave", prompt.lower())
        self.assertIn("logo's exact symbol", prompt.lower())

    def test_strict_fidelity_requires_a_source_asset(self):
        with self.assertRaises(rendriva.ValidationError):
            rendriva.normalize_job(base_job(fidelity_mode="strict"))

    def test_any_reference_image_automatically_defines_brand_palette(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "reference-one.png"
            second = root / "reference-two.png"
            image_one = rendriva.Image.new("RGB", (64, 64), "#C2410C")
            image_one.save(first)
            image_two = rendriva.Image.new("RGB", (64, 64), "#1D4ED8")
            image_two.save(second)
            spec = rendriva.normalize_job(
                base_job(operation="edit", reference_images=[str(first), str(second)])
            )
        self.assertEqual(spec["brand"]["palette_source"], "auto-reference")
        self.assertIn("#C2410C", spec["brand"]["palette"])
        self.assertIn("#1D4ED8", spec["brand"]["palette"])
        self.assertEqual(len(spec["brand"]["palette_sources"]), 2)
        prompt = rendriva.compile_prompt(spec, rendriva.build_plan(spec)[0])
        self.assertIn("automatically extracted", prompt.lower())
        self.assertIn("never recolor", prompt.lower())

    def test_logo_layer_has_palette_priority_and_manifest_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            product_path = root / "product.png"
            logo_path = root / "logo.png"
            rendriva.Image.new("RGBA", (80, 80), (20, 180, 90, 255)).save(product_path)
            rendriva.Image.new("RGBA", (80, 80), (110, 30, 180, 255)).save(logo_path)
            spec = rendriva.normalize_job(
                base_job(
                    locked_layers=[
                        {"path": str(product_path), "role": "product"},
                        {"path": str(logo_path), "role": "logo"},
                    ]
                )
            )
            manifest = rendriva.create_manifest(spec, "brand-test")
        self.assertEqual(spec["brand"]["palette_source_images"][0], str(logo_path))
        self.assertEqual(manifest["brand_palette"]["source"], "auto-reference")
        self.assertIn("#6E1EB4", manifest["brand_palette"]["colors"])
        self.assertEqual(len(manifest["brand_palette"]["sources"][0]["sha256"]), 64)

    def test_explicit_brand_palette_overrides_reference_extraction(self):
        with tempfile.TemporaryDirectory() as temporary:
            reference = Path(temporary) / "reference.png"
            rendriva.Image.new("RGB", (32, 32), "#00FF00").save(reference)
            spec = rendriva.normalize_job(
                base_job(
                    operation="edit",
                    reference_images=[str(reference)],
                    brand={"palette": ["#111111", "#F5EFE5"]},
                )
            )
        self.assertEqual(spec["brand"]["palette_source"], "explicit")
        self.assertEqual(spec["brand"]["palette"], ["#111111", "#F5EFE5"])

    def test_dry_plan_is_stable(self):
        spec = rendriva.normalize_job(base_job(count=3))
        self.assertEqual(rendriva.stable_job_id(spec), rendriva.stable_job_id(spec))

    def test_mock_end_to_end_creates_ten_files_and_zip(self):
        spec = rendriva.normalize_job(base_job(count=10, quality="medium"))
        with tempfile.TemporaryDirectory() as temporary:
            job_dir, manifest = rendriva.execute(spec, Path(temporary), rendriva.MockProvider())
            self.assertTrue(all(item["status"] == "PASS" for item in manifest["outputs"]))
            for index in range(1, 11):
                self.assertTrue((job_dir / f"image-{index:02d}.png").is_file())
            with zipfile.ZipFile(job_dir / "rendriva-output.zip") as archive:
                names = set(archive.namelist())
            self.assertIn("image-01.png", names)
            self.assertIn("image-10.png", names)
            self.assertIn("manifest.json", names)
            self.assertIn("quality-report.json", names)
            self.assertNotIn("collage.png", names)

    def test_repairs_only_the_failed_output(self):
        provider = RecordingProvider()
        spec = rendriva.normalize_job(base_job(count=2))
        with tempfile.TemporaryDirectory() as temporary:
            _, manifest = rendriva.execute(spec, Path(temporary), provider)
        self.assertEqual(provider.create_calls, [2, 1])
        self.assertEqual(manifest["outputs"][0]["repair_attempts"], 1)
        self.assertEqual(manifest["outputs"][1]["repair_attempts"], 0)
        self.assertTrue(all(item["status"] == "PASS" for item in manifest["outputs"]))

    def test_exact_text_layer_is_recorded(self):
        spec = rendriva.normalize_job(
            base_job(
                text_safe_mode=True,
                text_layers=[
                    {
                        "text": "PAYDAY SALE",
                        "x": 0.08,
                        "y": 0.08,
                        "max_width": 0.84,
                        "font_size": 52,
                        "color": "#111111",
                    }
                ],
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            _, manifest = rendriva.execute(spec, Path(temporary), rendriva.MockProvider())
        self.assertTrue(manifest["outputs"][0]["text_overlay"]["applied"])

    def test_duplicate_job_requires_resume(self):
        spec = rendriva.normalize_job(base_job())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rendriva.execute(spec, root, rendriva.MockProvider())
            with self.assertRaises(rendriva.RendrivaError):
                rendriva.execute(spec, root, rendriva.MockProvider())
            job_dir, manifest = rendriva.execute(spec, root, rendriva.MockProvider(), resume=True)
            self.assertTrue(job_dir.is_dir())
            self.assertEqual(manifest["outputs"][0]["status"], "PASS")

    def test_scene_failure_preserves_successful_outputs(self):
        spec = rendriva.normalize_job(
            base_job(
                mode="scenes",
                count=3,
                scenes=["Hero scene", "Blocked scene", "Detail scene"],
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            job_dir, manifest = rendriva.execute(spec, Path(temporary), PartialFailureProvider())
            statuses = [item["status"] for item in manifest["outputs"]]
            self.assertEqual(statuses.count("PASS"), 2)
            self.assertEqual(statuses.count("FAILED"), 1)
            self.assertTrue((job_dir / "image-01.png").is_file())
            self.assertTrue((job_dir / "image-03.png").is_file())
            self.assertFalse((job_dir / "image-02.png").exists())

    def test_locked_layer_is_composited_and_recorded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            layer_path = root / "product.png"
            layer = rendriva.Image.new("RGBA", (200, 300), (220, 30, 30, 0))
            draw = rendriva.ImageDraw.Draw(layer)
            draw.rectangle((20, 20, 180, 280), fill=(220, 30, 30, 255))
            layer.save(layer_path)
            logo_path = root / "logo.png"
            logo = rendriva.Image.new("RGBA", (120, 40), (0, 0, 0, 0))
            logo_draw = rendriva.ImageDraw.Draw(logo)
            logo_draw.rectangle((4, 4, 116, 36), fill=(20, 20, 20, 255))
            logo.save(logo_path)
            spec = rendriva.normalize_job(
                base_job(
                    locked_layers=[
                        {
                            "path": str(layer_path),
                            "x": 0.75,
                            "y": 0.5,
                            "max_width": 0.35,
                            "max_height": 0.7,
                            "anchor": "center",
                        },
                        {
                            "path": str(logo_path),
                            "role": "logo",
                            "x": 0.08,
                            "y": 0.08,
                            "max_width": 0.2,
                            "max_height": 0.12,
                            "anchor": "top-left",
                        }
                    ]
                )
            )
            _, manifest = rendriva.execute(spec, root / "runs", rendriva.MockProvider())
            composite = manifest["outputs"][0]["locked_layer_composite"]
            self.assertTrue(composite["applied"])
            self.assertEqual(len(composite["layers"]), 2)
            self.assertEqual(composite["strategy"], "literal-source-composite")
            self.assertEqual(composite["layers"][0]["role"], "product")
            self.assertEqual(composite["layers"][1]["role"], "logo")
            self.assertEqual(len(composite["layers"][0]["source_sha256"]), 64)
            self.assertTrue(composite["layers"][0]["source_derived"])
            self.assertFalse(composite["layers"][0]["generatively_redrawn"])
            self.assertTrue(manifest["fidelity"]["literal_source_preservation"])

    def test_reference_and_locked_layers_cannot_be_combined(self):
        with tempfile.TemporaryDirectory() as temporary:
            image_path = Path(temporary) / "product.png"
            rendriva.Image.new("RGBA", (32, 32), (0, 0, 0, 0)).save(image_path)
            with self.assertRaises(rendriva.ValidationError):
                rendriva.normalize_job(
                    base_job(
                        operation="edit",
                        reference_images=[str(image_path)],
                        locked_layers=[{"path": str(image_path)}],
                    )
                )

    def test_judge_receives_reference_and_final_image(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "reference.png"
            output = root / "output.png"
            rendriva.Image.new("RGB", (1024, 1024), (10, 10, 10)).save(reference)
            rendriva.Image.new("RGB", (1024, 1024), (20, 20, 20)).save(output)
            spec = rendriva.normalize_job(
                base_job(operation="edit", reference_images=[str(reference)])
            )
            vision_result = rendriva.MockProvider().judge(spec, output, "prompt")
            captured = {}

            def fake_request(url, api_key, **kwargs):
                captured.update(kwargs["json_body"])
                return {"output_text": json.dumps(vision_result)}

            with patch.object(rendriva, "api_request", side_effect=fake_request):
                result = rendriva.OpenAIProvider("test-key").judge(spec, output, "prompt")
            content = captured["input"][0]["content"]
            self.assertEqual(sum(item["type"] == "input_image" for item in content), 2)
            self.assertTrue(result["gates_pass"])

    def test_judge_receives_external_truth_source_and_mask(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "reference.png"
            truth_source = root / "artwork.png"
            truth_mask = root / "artwork-mask.png"
            output = root / "output.png"
            for path, color in ((reference, "#111111"), (truth_source, "#AA4422"), (output, "#222222")):
                rendriva.Image.new("RGB", (64, 64), color).save(path)
            rendriva.Image.new("L", (64, 64), 255).save(truth_mask)
            spec = rendriva.normalize_job(base_job(operation="edit", reference_images=[str(reference)], product_truth_map={"regions": [{"name": "front-artwork", "role": "print", "source_path": str(truth_source), "mask_path": str(truth_mask)}]}))
            vision_result = rendriva.MockProvider().judge(spec, output, "prompt")
            captured = {}

            def fake_request(url, api_key, **kwargs):
                captured.update(kwargs["json_body"])
                return {"output_text": json.dumps(vision_result)}

            with patch.object(rendriva, "api_request", side_effect=fake_request):
                rendriva.OpenAIProvider("test-key").judge(spec, output, "prompt")
            content = captured["input"][0]["content"]
            self.assertEqual(sum(item["type"] == "input_image" for item in content), 4)

    def test_quality_prompt_lists_exact_text(self):
        spec = rendriva.normalize_job(
            base_job(text_layers=[{"text": "PAYDAY SALE"}, {"text": "₱299"}])
        )
        prompt = rendriva.quality_prompt(spec, "generation prompt")
        self.assertIn("PAYDAY SALE", prompt)
        self.assertIn("₱299", prompt)

    def test_quality_prompt_includes_reference_palette_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            reference = Path(temporary) / "reference.png"
            rendriva.Image.new("RGB", (32, 32), "#E97824").save(reference)
            spec = rendriva.normalize_job(
                base_job(operation="edit", reference_images=[str(reference)])
            )
        prompt = rendriva.quality_prompt(spec, "generation prompt")
        self.assertIn("REFERENCE-DERIVED BRAND PALETTE", prompt)
        self.assertIn("#E97824", prompt)

    def test_strict_reference_cannot_pass_without_comparison_judge(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "product.png"
            output = root / "output.png"
            rendriva.Image.new("RGB", (1024, 1024), (10, 10, 10)).save(reference)
            rendriva.Image.new("RGB", (1024, 1024), (20, 20, 20)).save(output)
            spec = rendriva.normalize_job(
                base_job(operation="edit", reference_images=[str(reference)])
            )
            review = rendriva.finalize_review(spec, rendriva.structural_review(spec, output), None)
        self.assertFalse(review["passed"])
        self.assertIn("not verified", review["defects"][0].lower())

    def test_reference_preservation_false_fails_strict_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            reference = Path(temporary) / "logo.png"
            rendriva.Image.new("RGB", (64, 64), (10, 10, 10)).save(reference)
            spec = rendriva.normalize_job(base_job(operation="edit", reference_images=[str(reference)]))
        structural = {"passed": True, "defects": [], "metadata": {}}
        vision = rendriva.MockProvider().judge(spec, Path("unused.png"), "prompt")
        vision["reference_preservation"] = False
        review = rendriva.finalize_review(spec, structural, vision)
        self.assertFalse(review["passed"])

    def test_reference_intelligence_detects_roles_and_builds_multiview_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = []
            for name, color in (("shop-logo.png", "#E97824"), ("shirt-front.png", "#223344"), ("shirt-back.png", "#334455"), ("campaign-style.png", "#445566")):
                path = root / name
                rendriva.Image.new("RGB", (48, 48), color).save(path)
                paths.append(str(path))
            spec = rendriva.normalize_job(
                base_job(operation="edit", reference_images=paths, product_identity={"required_views": ["front", "back"]})
            )
        roles = [asset["role"] for asset in spec["reference_assets"]]
        self.assertEqual(roles, ["logo", "product", "product", "style"])
        self.assertEqual(spec["identity_packs"][0]["views"], ["back", "front"])
        prompt = rendriva.compile_prompt(spec, rendriva.build_plan(spec)[0])
        self.assertIn("REFERENCE INTELLIGENCE", prompt)
        self.assertIn("reconcile views back, front", prompt)

    def test_missing_required_identity_view_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            product = Path(temporary) / "shirt-front.png"
            rendriva.Image.new("RGB", (32, 32), "#222222").save(product)
            with self.assertRaises(rendriva.ValidationError):
                rendriva.normalize_job(base_job(operation="edit", reference_images=[str(product)], product_identity={"required_views": ["front", "back"]}))

    def test_brand_profile_campaign_and_diversity_tokens_are_reusable(self):
        with tempfile.TemporaryDirectory() as temporary:
            profile_path = Path(temporary) / "shop-brand.json"
            profile_path.write_text(json.dumps({"palette": ["#123456", "#F5F1EA"], "tone": "calm premium", "fonts": ["Inter", "Manrope"]}), encoding="utf-8")
            spec = rendriva.normalize_job(base_job(count=3, brand_profile=str(profile_path), campaign={"id": "launch-01", "consistency": "strict"}))
        plan = rendriva.build_plan(spec)
        self.assertEqual(spec["brand"]["tone"], "calm premium")
        self.assertEqual(len(spec["brand"]["profile"]["sha256"]), 64)
        self.assertEqual({item["campaign_signature"] for item in plan}, {spec["campaign"]["signature"]})
        self.assertEqual(len({item["variation_direction"] for item in plan}), 3)

    def test_duplicate_batch_output_fails_diversity_gate(self):
        spec = rendriva.normalize_job(base_job(count=2, max_repair_attempts=0, diversity={"max_similarity": 0.99}))
        with tempfile.TemporaryDirectory() as temporary:
            _, manifest = rendriva.execute(spec, Path(temporary), DuplicateProvider())
        self.assertEqual([item["status"] for item in manifest["outputs"]], ["PASS", "FAILED"])
        self.assertIn("too similar", manifest["outputs"][1]["quality"]["defects"][-1].lower())

    def test_auto_cutout_shadow_and_source_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            product_path = root / "product-white-bg.png"
            product = rendriva.Image.new("RGB", (120, 160), "white")
            rendriva.ImageDraw.Draw(product).rectangle((25, 20, 95, 140), fill="#B02030")
            product.save(product_path)
            spec = rendriva.normalize_job(base_job(locked_layers=[{"path": str(product_path), "require_alpha": False, "auto_cutout": True, "shadow": True}]))
            _, manifest = rendriva.execute(spec, root / "runs", rendriva.MockProvider())
        evidence = manifest["outputs"][0]["locked_layer_composite"]["layers"][0]
        self.assertTrue(evidence["auto_cutout_applied"])
        self.assertTrue(evidence["shadow_applied"])
        self.assertTrue(evidence["source_derived"])

    def test_typography_wraps_and_uses_automatic_contrast(self):
        with tempfile.TemporaryDirectory() as temporary:
            image_path = Path(temporary) / "dark.png"
            rendriva.Image.new("RGB", (600, 400), "#111111").save(image_path)
            result = rendriva.apply_text_layers(image_path, [{"text": "A professionally typeset long marketplace headline", "x": 0.08, "y": 0.08, "max_width": 0.38, "max_height": 0.45, "font_size": "auto", "color": "auto", "style": "headline"}])
        self.assertTrue(result["layers"][0]["wrapped"])
        self.assertEqual(result["layers"][0]["color"], "#FFFFFF")
        self.assertEqual(result["engine"], "rendriva-typography-v1")

    def test_marketplace_mode_uses_only_supplied_conversion_copy(self):
        spec = rendriva.normalize_job(base_job(marketplace={"goal": "payday-sale", "platform": "Shopee", "price": "₱299", "discount": "20% OFF", "claims": ["Free shipping on eligible orders"]}))
        prompt = rendriva.compile_prompt(spec, rendriva.build_plan(spec)[0])
        self.assertIn("₱299", prompt)
        self.assertIn("20% OFF", prompt)
        self.assertIn("Never invent a price", prompt)
        self.assertEqual([layer["text"] for layer in spec["text_layers"]], ["₱299", "20% OFF"])

    def test_platform_export_pack_and_machine_readable_reports(self):
        spec = rendriva.normalize_job(base_job(platform_exports=["shopee-square", "instagram-story"]))
        with tempfile.TemporaryDirectory() as temporary:
            job_dir, manifest = rendriva.execute(spec, Path(temporary), rendriva.MockProvider())
            for relative, expected in (("platform-exports/image-01-01-shopee-square-1080x1080.png", (1080, 1080)), ("platform-exports/image-01-02-instagram-story-1080x1920.png", (1080, 1920))):
                with rendriva.Image.open(job_dir / relative) as exported:
                    self.assertEqual(exported.size, expected)
            for report in ("brand-profile.json", "reference-fidelity-report.json", "diversity-report.json", "campaign-report.json"):
                self.assertTrue((job_dir / report).is_file())
            with zipfile.ZipFile(job_dir / "rendriva-output.zip") as archive:
                names = set(archive.namelist())
            self.assertIn("platform-exports/image-01-02-instagram-story-1080x1920.png", names)
            self.assertEqual(len(manifest["platform_export_pack"]), 2)

    def test_short_provider_batch_marks_missing_outputs_failed(self):
        spec = rendriva.normalize_job(base_job(count=2, diversity=False))
        with tempfile.TemporaryDirectory() as temporary:
            _, manifest = rendriva.execute(spec, Path(temporary), ShortBatchProvider())
        self.assertEqual([item["status"] for item in manifest["outputs"]], ["PASS", "FAILED"])
        self.assertIn("only 1 images", manifest["outputs"][1]["error"])

    def test_custom_exports_have_unique_names_and_honor_background(self):
        spec = rendriva.normalize_job(base_job(platform_exports=[{"preset": "custom", "width": 500, "height": 700, "background": "#FF0000"}, {"preset": "custom", "width": 600, "height": 800, "background": "#00FF00"}]))
        with tempfile.TemporaryDirectory() as temporary:
            job_dir, manifest = rendriva.execute(spec, Path(temporary), rendriva.MockProvider())
            paths = [job_dir / record["file"] for record in manifest["platform_export_pack"]]
            self.assertEqual(len({path.name for path in paths}), 2)
            with rendriva.Image.open(paths[0]) as first, rendriva.Image.open(paths[1]) as second:
                self.assertEqual(first.getpixel((0, 0))[:3], (255, 0, 0))
                self.assertEqual(second.getpixel((0, 0))[:3], (0, 255, 0))

    def test_relative_font_path_is_resolved_from_job_directory(self):
        source_font = rendriva.find_default_font()
        if not source_font:
            self.skipTest("No system font available")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copy(source_font, root / "brand.ttf")
            spec = rendriva.normalize_job(base_job(text_layers=[{"text": "Exact", "font_path": "brand.ttf"}]), root)
        self.assertTrue(Path(spec["text_layers"][0]["font_path"]).is_absolute())

    def test_text_zone_must_stay_inside_canvas(self):
        with self.assertRaises(rendriva.ValidationError):
            rendriva.normalize_job(base_job(text_layers=[{"text": "Off canvas", "x": 0.9, "max_width": 0.4}]))

    def test_required_views_need_product_references_and_valid_names(self):
        with self.assertRaises(rendriva.ValidationError):
            rendriva.normalize_job(base_job(product_identity={"required_views": ["front"]}))
        with tempfile.TemporaryDirectory() as temporary:
            product = Path(temporary) / "product-front.png"
            rendriva.Image.new("RGB", (32, 32), "#222222").save(product)
            with self.assertRaises(rendriva.ValidationError):
                rendriva.normalize_job(base_job(operation="edit", reference_images=[str(product)], product_identity={"required_views": ["inside"]}))

    def test_style_only_reference_is_role_scoped_not_product_locked(self):
        with tempfile.TemporaryDirectory() as temporary:
            style = Path(temporary) / "campaign-style.png"
            rendriva.Image.new("RGB", (32, 32), "#556677").save(style)
            spec = rendriva.normalize_job(base_job(operation="edit", reference_images=[str(style)]))
        self.assertEqual(spec["fidelity_mode"], "guided")
        self.assertFalse(any("fabric weave" in lock for lock in spec["preserve"]))
        self.assertFalse(spec["protected_reference"])

    def test_disabled_diversity_removes_batch_directions(self):
        spec = rendriva.normalize_job(base_job(count=2, diversity=False))
        item = dict(rendriva.build_plan(spec)[0])
        item["batch_variations"] = True
        prompt = rendriva.compile_prompt(spec, item)
        self.assertNotIn("ordered diversity directions", prompt)

    def test_campaign_report_states_verification_scope_honestly(self):
        spec = rendriva.normalize_job(base_job(count=2))
        with tempfile.TemporaryDirectory() as temporary:
            job_dir, _ = rendriva.execute(spec, Path(temporary), rendriva.MockProvider())
            report = json.loads((job_dir / "campaign-report.json").read_text(encoding="utf-8"))
            fidelity = json.loads((job_dir / "reference-fidelity-report.json").read_text(encoding="utf-8"))
            quality = json.loads((job_dir / "quality-report.json").read_text(encoding="utf-8"))
        self.assertFalse(report["batch_visual_consistency_verified"])
        self.assertFalse(report["batch_visual_consistency_passed"])
        self.assertTrue(report["synthetic_comparison_passed"])
        self.assertEqual(report["evidence_scope"], "synthetic-mock-comparison")
        self.assertIn("placeholder outputs", report["verification_note"])
        self.assertFalse(quality["delivery_ready"])
        self.assertEqual(quality["outputs"][0]["visual_verification_status"], "SYNTHETIC_TEST_ONLY")
        self.assertFalse(fidelity["verified"])
        self.assertIsNone(fidelity["outputs"][0]["reference_preservation"])
        self.assertTrue(fidelity["outputs"][0]["synthetic_reference_result"])

    def test_campaign_vision_lock_repairs_only_the_outlier(self):
        provider = CampaignOutlierProvider()
        spec = rendriva.normalize_job(base_job(count=3, campaign={"id": "launch", "vision_lock": {"max_repair_attempts": 1}}))
        with tempfile.TemporaryDirectory() as temporary:
            _, manifest = rendriva.execute(spec, Path(temporary), provider)
        self.assertEqual(provider.campaign_calls, 2)
        self.assertNotIn("campaign_repair_history", manifest["outputs"][0])
        self.assertEqual(len(manifest["outputs"][1]["campaign_repair_history"]), 1)
        self.assertNotIn("campaign_repair_history", manifest["outputs"][2])
        self.assertTrue(manifest["campaign_visual_review"]["verified"])
        self.assertTrue(manifest["campaign_visual_review"]["passed"])

    def test_campaign_vision_lock_is_unverified_without_vision_judge(self):
        spec = rendriva.normalize_job(base_job(count=2))
        with tempfile.TemporaryDirectory() as temporary:
            job_dir, manifest = rendriva.execute(spec, Path(temporary), rendriva.MockProvider(), use_vision_judge=False)
            report = json.loads((job_dir / "campaign-report.json").read_text(encoding="utf-8"))
        self.assertFalse(manifest["campaign_visual_review"]["verified"])
        self.assertFalse(report["batch_visual_consistency_verified"])
        self.assertEqual(report["evidence_scope"], "policy-and-per-output-qa")

    def test_automatic_and_explicit_product_truth_regions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            product = root / "shirt-front.png"
            mask = root / "print-mask.png"
            rendriva.Image.new("RGB", (64, 64), "#884422").save(product)
            rendriva.Image.new("L", (64, 64), 255).save(mask)
            automatic = rendriva.normalize_job(base_job(operation="edit", reference_images=[str(product)]), root)
            explicit = rendriva.normalize_job(
                base_job(
                    operation="edit",
                    reference_images=[str(product)],
                    product_truth_map={"regions": [{"name": "front-print", "role": "print", "source_path": "shirt-front.png", "mask_path": "print-mask.png", "bbox": [0.2, 0.2, 0.6, 0.6], "preserve": ["exact artwork geometry and ink colors"]}]},
                ),
                root,
            )
        self.assertEqual({region["role"] for region in automatic["product_truth_map"]["regions"]}, {"silhouette", "material"})
        region = explicit["product_truth_map"]["regions"][0]
        self.assertEqual(region["comparison_strategy"], "masked-vision-comparison")
        self.assertEqual(len(region["source_sha256"]), 64)
        self.assertEqual(len(region["mask_sha256"]), 64)
        prompt = rendriva.compile_prompt(explicit, rendriva.build_plan(explicit)[0])
        self.assertIn("PRODUCT REGION TRUTH MAP", prompt)
        self.assertIn("front-print", prompt)

    def test_required_truth_region_failure_blocks_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            product = Path(temporary) / "product-front.png"
            output = Path(temporary) / "output.png"
            rendriva.Image.new("RGB", (64, 64), "#222222").save(product)
            rendriva.Image.new("RGB", (1024, 1024), "#333333").save(output)
            spec = rendriva.normalize_job(base_job(operation="edit", reference_images=[str(product)]))
            vision = rendriva.MockProvider().judge(spec, output, "prompt")
            vision["region_fidelity"][0]["passed"] = False
            review = rendriva.finalize_review(spec, rendriva.structural_review(spec, output), vision)
        self.assertFalse(review["passed"])
        self.assertIn("truth regions failed", " ".join(review["defects"]).lower())

    def test_truth_region_mask_must_match_source_dimensions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            product = root / "product.png"
            mask = root / "mask.png"
            rendriva.Image.new("RGB", (64, 64), "#222222").save(product)
            rendriva.Image.new("L", (32, 32), 255).save(mask)
            with self.assertRaises(rendriva.ValidationError):
                rendriva.normalize_job(base_job(operation="edit", reference_images=[str(product)], product_truth_map={"regions": [{"name": "logo", "role": "logo", "source_path": str(product), "mask_path": str(mask)}]}))

    def test_draft_to_final_selects_candidates_and_promotes_only_selected(self):
        provider = DraftRecordingProvider()
        spec = rendriva.normalize_job(base_job(count=2, text_layers=[{"text": "PAYDAY SALE"}], draft_to_final={"candidate_count": 4, "draft_quality": "low", "include_drafts": True}))
        with tempfile.TemporaryDirectory() as temporary:
            job_dir, manifest = rendriva.execute(spec, Path(temporary), provider)
            with zipfile.ZipFile(job_dir / "rendriva-output.zip") as archive:
                names = set(archive.namelist())
        self.assertEqual(provider.create_calls[0], {"quality": "low", "n": 4})
        self.assertEqual(provider.create_text_layers[0], [])
        self.assertNotIn("PAYDAY SALE", provider.create_prompts[0])
        self.assertEqual(len(provider.promotions), 2)
        self.assertEqual(manifest["draft_selection"]["selected_candidates"], [1, 2])
        self.assertEqual([item["selected_draft_index"] for item in manifest["outputs"]], [1, 2])
        self.assertIn("draft-selection-report.json", names)
        self.assertIn("drafts/draft-01.png", names)
        self.assertTrue(manifest["campaign_visual_review"]["passed"])

    def test_manual_draft_selection_requires_one_unique_choice_per_final(self):
        with self.assertRaises(rendriva.ValidationError):
            rendriva.normalize_job(base_job(count=2, draft_to_final={"candidate_count": 4, "selection_mode": "manual", "selection": [1]}))
        spec = rendriva.normalize_job(base_job(count=2, draft_to_final={"candidate_count": 4, "selection_mode": "manual", "selection": [4, 2]}))
        self.assertEqual(spec["draft_to_final"]["selection"], [4, 2])

    def test_single_model_face_lock_auto_triggers_for_fashion_batches_and_request_language(self):
        fashion = rendriva.normalize_job(base_job(count=3, preset="fashion-model"))
        requested = rendriva.normalize_job(base_job(count=2, prompt="Generate a model for each variant using one face only"))
        disabled = rendriva.normalize_job(base_job(count=3, preset="fashion-model", model_identity_lock=False))
        self.assertTrue(fashion["model_identity_lock"]["enabled"])
        self.assertEqual(fashion["model_identity_lock"]["anchor_strategy"], "first-approved-output")
        self.assertTrue(requested["model_identity_lock"]["enabled"])
        self.assertFalse(disabled["model_identity_lock"]["enabled"])
        prompt = rendriva.compile_prompt(fashion, rendriva.build_plan(fashion)[0])
        self.assertIn("SINGLE MODEL FACE LOCK", prompt)
        self.assertIn("authoritative identity anchor", prompt)

    def test_first_approved_model_becomes_anchor_for_every_later_variant(self):
        provider = IdentityRecordingProvider()
        spec = rendriva.normalize_job(base_job(count=3, preset="fashion-model"))
        with tempfile.TemporaryDirectory() as temporary:
            job_dir, manifest = rendriva.execute(spec, Path(temporary), provider)
            report = json.loads((job_dir / "model-identity-report.json").read_text(encoding="utf-8"))
            with zipfile.ZipFile(job_dir / "rendriva-output.zip") as archive:
                names = set(archive.namelist())
        self.assertEqual(provider.creates[0]["n"], 1)
        self.assertEqual([call["identity"] for call in provider.identity_creates], ["image-01.png", "image-01.png"])
        self.assertEqual(manifest["model_identity_lock"]["anchor_output_index"], 1)
        self.assertEqual([item.get("model_identity_anchor_file") for item in manifest["outputs"][1:]], ["image-01.png", "image-01.png"])
        self.assertTrue(report["verified"])
        self.assertEqual(report["status"], "PASS")
        self.assertIn("model-identity-report.json", names)

    def test_supplied_model_reference_is_the_only_face_anchor(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model = root / "shop-model-face.png"
            rendriva.Image.new("RGB", (64, 64), "#B08070").save(model)
            spec = rendriva.normalize_job(
                base_job(count=2, preset="fashion-model", operation="edit", reference_assets=[{"path": str(model), "role": "model"}])
            )
            provider = IdentityRecordingProvider()
            _, manifest = rendriva.execute(spec, root / "runs", provider)
        self.assertEqual(spec["model_identity_lock"]["source_path"], str(model))
        self.assertFalse(spec["brand"]["palette_sources"])
        self.assertEqual([call["identity"] for call in provider.identity_creates], [model.name, model.name])
        self.assertEqual(manifest["model_identity_lock"]["anchor_strategy"], "supplied-reference")

    def test_uploaded_face_request_promotes_a_generic_upload_to_authoritative_model_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            uploaded = root / "upload-92831.png"
            rendriva.Image.new("RGB", (64, 64), "#A87868").save(uploaded)
            spec = rendriva.normalize_job(
                base_job(
                    count=3,
                    prompt="Pareho ang mukha sa uploaded image for every clothing variant",
                    reference_images=[str(uploaded)],
                )
            )
            provider = IdentityRecordingProvider()
            _, manifest = rendriva.execute(spec, root / "runs", provider)
        self.assertEqual(spec["reference_assets"][0]["role"], "model")
        self.assertEqual(spec["reference_assets"][0]["role_source"], "request-inference")
        self.assertFalse(spec["reference_assets"][0]["use_for_palette"])
        self.assertEqual(spec["model_identity_lock"]["source_path"], str(uploaded))
        self.assertEqual(spec["model_identity_lock"]["anchor_strategy"], "supplied-reference")
        self.assertEqual([call["identity"] for call in provider.identity_creates], [uploaded.name, uploaded.name, uploaded.name])
        self.assertTrue(manifest["model_identity_report"]["verified"])

    def test_uploaded_face_anchor_is_honored_even_for_one_model_image(self):
        with tempfile.TemporaryDirectory() as temporary:
            uploaded = Path(temporary) / "upload.png"
            rendriva.Image.new("RGB", (64, 64), "#A87868").save(uploaded)
            spec = rendriva.normalize_job(base_job(count=1, prompt="Same face as the uploaded image", reference_images=[str(uploaded)]))
        self.assertTrue(spec["model_identity_lock"]["enabled"])
        self.assertEqual(spec["model_identity_lock"]["source_path"], str(uploaded))
        self.assertEqual(spec["reference_assets"][0]["role"], "model")

    def test_face_identity_mismatch_repairs_only_the_failed_variant(self):
        provider = FaceMismatchProvider()
        spec = rendriva.normalize_job(base_job(count=3, preset="fashion-model", max_repair_attempts=1))
        with tempfile.TemporaryDirectory() as temporary:
            _, manifest = rendriva.execute(spec, Path(temporary), provider)
        self.assertEqual([item["status"] for item in manifest["outputs"]], ["PASS", "PASS", "PASS"])
        self.assertEqual(manifest["outputs"][0]["repair_attempts"], 0)
        self.assertEqual(manifest["outputs"][1]["repair_attempts"], 1)
        self.assertEqual(manifest["outputs"][2]["repair_attempts"], 0)
        self.assertIn("exact anchored face identity", manifest["outputs"][1]["repair_history"][0]["prompt"])

    def test_face_lock_with_drafts_uses_one_draft_anchor_for_candidates_and_finals(self):
        provider = IdentityRecordingProvider()
        spec = rendriva.normalize_job(base_job(count=2, preset="fashion-model", draft_to_final={"candidate_count": 4, "draft_quality": "low"}))
        with tempfile.TemporaryDirectory() as temporary:
            _, manifest = rendriva.execute(spec, Path(temporary), provider)
        self.assertEqual(provider.identity_creates[0]["identity"], "draft-01.png")
        self.assertEqual(provider.identity_creates[0]["n"], 3)
        self.assertEqual([call["identity"] for call in provider.identity_creates[1:]], ["draft-01.png", "draft-01.png"])
        self.assertEqual(manifest["model_identity_lock"]["anchor_strategy"], "first-approved-draft")
        self.assertTrue(manifest["model_identity_report"]["verified"])

    def test_face_lock_cannot_claim_verification_without_vision_judge(self):
        spec = rendriva.normalize_job(base_job(count=2, preset="fashion-model", max_repair_attempts=0))
        with tempfile.TemporaryDirectory() as temporary:
            _, manifest = rendriva.execute(spec, Path(temporary), rendriva.MockProvider(), use_vision_judge=False)
        self.assertEqual([item["status"] for item in manifest["outputs"]], ["PASS", "FAILED"])
        self.assertFalse(manifest["model_identity_report"]["verified"])

    def test_sku_variant_matrix_maps_every_output_to_one_authoritative_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources = []
            for name, color in (("black", "#111111"), ("beige", "#D8C4A0"), ("maroon", "#741D35")):
                path = root / f"{name}-product.png"
                rendriva.Image.new("RGB", (600, 600), color).save(path)
                sources.append(path)
            spec = rendriva.normalize_job(base_job(count=3, sku_variant_matrix={"variants": [
                {"id": "SKU-BLK", "source_path": str(sources[0]), "expected_color": "black"},
                {"id": "SKU-BGE", "source_path": str(sources[1]), "expected_color": "beige"},
                {"id": "SKU-MRN", "source_path": str(sources[2]), "expected_color": "maroon"},
            ]}))
            plan = rendriva.build_plan(spec)
        self.assertEqual([item["sku_assignment"]["variant_id"] for item in plan], ["SKU-BLK", "SKU-BGE", "SKU-MRN"])
        self.assertEqual(len({item["sku_assignment"]["source_sha256"] for item in plan}), 3)
        self.assertIn("Do not swap", rendriva.compile_prompt(spec, plan[0]))

    def test_sku_matrix_rejects_unassigned_outputs_and_duplicate_assignments(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "product.png"
            rendriva.Image.new("RGB", (600, 600), "#222222").save(source)
            with self.assertRaises(rendriva.ValidationError):
                rendriva.normalize_job(base_job(count=2, sku_variant_matrix={"variants": [{"id": "SKU-1", "source_path": str(source), "output_indices": [1]}]}))
            with self.assertRaises(rendriva.ValidationError):
                rendriva.normalize_job(base_job(count=2, sku_variant_matrix={"variants": [
                    {"id": "SKU-1", "source_path": str(source), "output_indices": [1, 2]},
                    {"id": "SKU-2", "source_path": str(source), "output_indices": [2]},
                ]}))

    def test_commerce_gates_are_non_negotiable(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "garment.png"
            output = Path(temporary) / "output.png"
            rendriva.Image.new("RGB", (1024, 1024), "#333333").save(source)
            rendriva.Image.new("RGB", (1024, 1024), "#444444").save(output)
            spec = rendriva.normalize_job(base_job(count=1, sku_variant_matrix={"variants": [{"id": "SKU-1", "source_path": str(source)}]}, product_visibility_guard={"min_visible_ratio": 0.9}))
            vision = rendriva.MockProvider().judge(spec, output, "prompt")
            vision["product_visibility_ratio"] = 0.6
            vision["product_visibility_pass"] = False
            review = rendriva.finalize_review(spec, rendriva.structural_review(spec, output), vision)
        self.assertFalse(review["passed"])
        self.assertTrue(any("visibility" in defect.lower() for defect in review["defects"]))

    def test_reference_preflight_reports_low_resolution_and_can_block(self):
        with tempfile.TemporaryDirectory() as temporary:
            reference = Path(temporary) / "tiny-product.png"
            rendriva.Image.new("RGB", (80, 80), "#555555").save(reference)
            warning_spec = rendriva.normalize_job(base_job(operation="edit", reference_images=[str(reference)]))
            self.assertFalse(warning_spec["reference_preflight"]["passed"])
            with self.assertRaises(rendriva.ValidationError):
                rendriva.normalize_job(base_job(operation="edit", reference_images=[str(reference)], reference_preflight={"min_edge": 512, "blocking": True}))

    def test_shot_director_defect_memory_and_video_handoff_are_packaged(self):
        spec = rendriva.normalize_job(base_job(count=3, shot_director=True, defect_memory={"entries": ["no padded chest contour"]}, video_continuity_pack=True))
        with tempfile.TemporaryDirectory() as temporary:
            job_dir, manifest = rendriva.execute(spec, Path(temporary), rendriva.MockProvider())
            self.assertEqual([item["shot"] for item in manifest["outputs"]], ["hero", "full-body-front", "three-quarter"])
            self.assertIn("no padded chest contour", manifest["outputs"][0]["compiled_prompt"])
            self.assertTrue((job_dir / "commerce-production-report.json").is_file())
            self.assertTrue((job_dir / "video-continuity-pack.json").is_file())
            video = rendriva.json_load(job_dir / "video-continuity-pack.json")
            self.assertEqual(len(video["keyframes"]), 3)
            with zipfile.ZipFile(job_dir / "rendriva-output.zip") as archive:
                self.assertIn("commerce-production-report.json", archive.namelist())
                self.assertIn("video-continuity-pack.json", archive.namelist())

    def test_unapproved_checkpoint_generates_only_anchor_and_blocks_paid_batch(self):
        spec = rendriva.normalize_job(base_job(count=3, approval_checkpoint=True))
        with tempfile.TemporaryDirectory() as temporary:
            job_dir, manifest = rendriva.execute(spec, Path(temporary), rendriva.MockProvider())
            self.assertEqual([item["status"] for item in manifest["outputs"]], ["PASS", "BLOCKED", "BLOCKED"])
            self.assertTrue((job_dir / "image-01.png").is_file())
            self.assertFalse((job_dir / "image-02.png").exists())
            self.assertEqual(manifest["approval_checkpoint"]["status"], "AWAITING_APPROVAL")

    def test_localized_repair_uses_failed_output_as_first_edit_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "product.png"
            failed = root / "failed-output.png"
            rendriva.Image.new("RGB", (1024, 1024), "#111111").save(reference)
            rendriva.Image.new("RGB", (1024, 1024), "#222222").save(failed)
            spec = rendriva.normalize_job(base_job(operation="edit", reference_images=[str(reference)]))
            payload = rendriva.MockProvider().create(spec, "repair", n=1)[0]
            captured = {}

            def fake_request(url, api_key, **kwargs):
                captured.update(kwargs)
                return {"data": [{"b64_json": base64.b64encode(payload).decode()}]}

            with patch.object(rendriva, "api_request", side_effect=fake_request):
                result = rendriva.OpenAIProvider("test-key").repair(spec, "repair only the hand", failed, n=1)
            body = captured["raw_body"]
        self.assertEqual(len(result), 1)
        self.assertLess(body.find(b'filename="failed-output.png"'), body.find(b'filename="product.png"'))


if __name__ == "__main__":
    unittest.main()
