from __future__ import annotations

import importlib.util
import json
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


if __name__ == "__main__":
    unittest.main()
