#!/usr/bin/env python3
from __future__ import annotations

import sys
import subprocess
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import harness_init  # noqa: E402
from harness_init import ensure_product_workspace, render_bmad_config_block  # noqa: E402
from harness_assurance import install_guards  # noqa: E402


class HarnessInitTest(unittest.TestCase):
    def test_guarded_configuration_fails_before_workspace_write_without_git(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product_root = Path(tmp)
            result = install_guards(product_root)
            self.assertEqual(result["reason"], "GUARDED_GIT_REPOSITORY_REQUIRED")
            self.assertFalse((product_root / "harness-workspace").exists())
            self.assertFalse((product_root / ".githooks").exists())

    def test_guarded_init_installs_hooks_and_declares_guarded_assurance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product_root = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=product_root, check=True)
            product = {
                "id": "guarded-demo", "name": "Guarded Demo", "profile": "generic",
                "root": str(product_root), "workspace": "harness-workspace",
                "config": "harness-workspace/project.yaml", "work_item_provider": "noop",
                "work_item_id_pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$",
                "assurance": "guarded",
            }
            ensure_product_workspace(product)
            result = install_guards(product_root)
            self.assertEqual(result["decision"], "pass")
            config = yaml.safe_load((product_root / "harness-workspace/project.yaml").read_text())
            self.assertEqual(config["assurance"]["level"], "guarded")
            self.assertEqual(
                subprocess.check_output(
                    ["git", "config", "--local", "--get", "core.hooksPath"],
                    cwd=product_root, text=True,
                ).strip(),
                ".githooks",
            )

    def test_workspace_contains_design_artifacts_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product_root = Path(tmp)
            product = {
                "id": "demo",
                "name": "Demo",
                "profile": "generic",
                "root": str(product_root),
                "workspace": "harness-workspace",
                "config": "harness-workspace/project.yaml",
                "work_item_provider": "noop",
                "work_item_id_pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$",
            }

            created = ensure_product_workspace(product)

            self.assertTrue((product_root / "harness-workspace" / "bmad-output" / "design-artifacts").is_dir())
            self.assertIn("harness-workspace/bmad-output/design-artifacts", created)
            self.assertTrue((product_root / ".github/workflows/release.yml").is_file())
            self.assertTrue((product_root / ".github/workflows/harness-provider-complete.yml").is_file())
            self.assertTrue((product_root / ".github/workflows/harness-required.yml").is_file())

    def test_generic_workspace_templates_do_not_leak_product_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product_root = Path(tmp)
            product = {
                "id": "demo",
                "name": "Demo",
                "profile": "generic",
                "root": str(product_root),
                "workspace": "harness-workspace",
                "config": "harness-workspace/project.yaml",
                "work_item_provider": "noop",
                "work_item_id_pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$",
            }

            ensure_product_workspace(product)

            generated_files = [
                product_root / "harness-workspace" / "README.md",
                product_root / "harness-workspace" / "project.yaml",
                product_root / "harness-workspace" / "knowledge" / "CONTEXT.md",
                product_root / "harness-workspace" / "planning" / "product-specs" / "index.md",
            ]
            forbidden = ["ExampleCorp", "Example Product", "internal-product"]
            leaked: list[str] = []
            for path in generated_files:
                text = path.read_text(encoding="utf-8")
                for term in forbidden:
                    if term in text:
                        leaked.append(f"{path.relative_to(product_root)}:{term}")

            self.assertEqual([], leaked)

            config = yaml.safe_load((product_root / "harness-workspace/project.yaml").read_text(encoding="utf-8"))
            self.assertEqual(config["bmad"]["output_root"], "harness-workspace/bmad-output")
            self.assertEqual(config["bmad"]["normalized_planning_root"], "harness-workspace/planning")

    def test_bmad_config_pins_design_artifacts(self) -> None:
        product = {
            "id": "demo",
            "name": "Demo",
            "workspace": "harness-workspace",
        }

        block = render_bmad_config_block(product)

        self.assertIn("design_artifacts", block)
        self.assertIn("[modules.bmm]", block)
        self.assertIn("[modules.cis]", block)
        self.assertIn("{project-root}/harness-workspace/bmad-output/design-artifacts", block)

    def test_remove_product_unregisters_and_clears_active_without_deleting_workspace(self) -> None:
        if yaml is None:
            self.skipTest("PyYAML is required")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness_products = root / ".harness" / "products"
            product_root = root / "product"
            (product_root / "harness-workspace").mkdir(parents=True)
            (product_root / "_bmad").mkdir()
            harness_products.mkdir(parents=True)
            (harness_products / "registry.yaml").write_text(
                yaml.safe_dump(
                    {
                        "products": [
                            {
                                "id": "demo",
                                "name": "Demo",
                                "root": str(product_root),
                                "workspace": "harness-workspace",
                                "config": "harness-workspace/project.yaml",
                            }
                        ]
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            (harness_products / "active-product.json").write_text(
                '{"product_id":"demo","root":"' + str(product_root) + '","workspace":"harness-workspace"}',
                encoding="utf-8",
            )

            original_products_dir = harness_init.products_dir
            try:
                harness_init.products_dir = lambda: harness_products
                result = harness_init.remove_product(
                    Namespace(product_id="demo", product_root="", delete_generated=False, force=False, dry_run=False)
                )
            finally:
                harness_init.products_dir = original_products_dir

            self.assertEqual(result["reason"], "PRODUCT_REMOVED")
            registry = yaml.safe_load((harness_products / "registry.yaml").read_text(encoding="utf-8"))
            self.assertEqual(registry["products"], [])
            self.assertFalse((harness_products / "active-product.json").exists())
            self.assertTrue((product_root / "harness-workspace").is_dir())
            self.assertTrue((product_root / "_bmad").is_dir())

    def test_remove_product_requires_force_to_delete_generated_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness_products = root / ".harness" / "products"
            product_root = root / "product"
            (product_root / "harness-workspace").mkdir(parents=True)
            harness_products.mkdir(parents=True)
            (harness_products / "registry.yaml").write_text(
                "products:\n- id: demo\n  name: Demo\n  root: " + str(product_root) + "\n  workspace: harness-workspace\n",
                encoding="utf-8",
            )

            original_products_dir = harness_init.products_dir
            try:
                harness_init.products_dir = lambda: harness_products
                result = harness_init.remove_product(
                    Namespace(product_id="demo", product_root="", delete_generated=True, force=False, dry_run=False)
                )
            finally:
                harness_init.products_dir = original_products_dir

            self.assertEqual(result["reason"], "DELETE_GENERATED_REQUIRES_FORCE")
            self.assertTrue((product_root / "harness-workspace").is_dir())

    def test_remove_product_deletes_generated_paths_with_force(self) -> None:
        if yaml is None:
            self.skipTest("PyYAML is required")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness_products = root / ".harness" / "products"
            product_root = root / "product"
            (product_root / "harness-workspace").mkdir(parents=True)
            (product_root / "_bmad-output").mkdir()
            harness_products.mkdir(parents=True)
            (harness_products / "registry.yaml").write_text(
                yaml.safe_dump(
                    {
                        "products": [
                            {
                                "id": "demo",
                                "name": "Demo",
                                "root": str(product_root),
                                "workspace": "harness-workspace",
                            }
                        ]
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )

            original_products_dir = harness_init.products_dir
            try:
                harness_init.products_dir = lambda: harness_products
                result = harness_init.remove_product(
                    Namespace(product_id="demo", product_root="", delete_generated=True, force=True, dry_run=False)
                )
            finally:
                harness_init.products_dir = original_products_dir

            self.assertEqual(result["reason"], "PRODUCT_REMOVED")
            self.assertFalse((product_root / "harness-workspace").exists())
            self.assertFalse((product_root / "_bmad-output").exists())

    def test_remove_product_dry_run_does_not_modify_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness_products = root / ".harness" / "products"
            product_root = root / "product"
            (product_root / "harness-workspace").mkdir(parents=True)
            harness_products.mkdir(parents=True)
            registry_text = "products:\n- id: demo\n  name: Demo\n  root: " + str(product_root) + "\n  workspace: harness-workspace\n"
            (harness_products / "registry.yaml").write_text(registry_text, encoding="utf-8")

            original_products_dir = harness_init.products_dir
            try:
                harness_init.products_dir = lambda: harness_products
                result = harness_init.remove_product(
                    Namespace(product_id="demo", product_root="", delete_generated=True, force=False, dry_run=True)
                )
            finally:
                harness_init.products_dir = original_products_dir

            self.assertEqual(result["reason"], "PRODUCT_REMOVE_DRY_RUN")
            self.assertEqual((harness_products / "registry.yaml").read_text(encoding="utf-8"), registry_text)
            self.assertTrue((product_root / "harness-workspace").is_dir())


if __name__ == "__main__":
    unittest.main()
