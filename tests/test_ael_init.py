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
SCRIPT_DIR = ROOT / ".ael" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import ael_init  # noqa: E402
from ael_init import ensure_product_workspace, render_bmad_config_block  # noqa: E402
from ael_init_bmad import upsert_managed_block  # noqa: E402
from ael_assurance import install_guards  # noqa: E402


class AELInitTest(unittest.TestCase):
    def test_guarded_configuration_fails_before_workspace_write_without_git(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product_root = Path(tmp)
            result = install_guards(product_root)
            self.assertEqual(result["reason"], "GUARDED_GIT_REPOSITORY_REQUIRED")
            self.assertFalse((product_root / "ael-workspace").exists())
            self.assertFalse((product_root / ".githooks").exists())

    def test_guarded_init_installs_hooks_and_declares_guarded_assurance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product_root = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=product_root, check=True)
            product = {
                "id": "guarded-demo", "name": "Guarded Demo", "profile": "generic",
                "root": str(product_root), "workspace": "ael-workspace",
                "config": "ael-workspace/project.yaml", "work_item_provider": "noop",
                "work_item_id_pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$",
                "assurance": "guarded",
            }
            ensure_product_workspace(product)
            result = install_guards(product_root)
            self.assertEqual(result["decision"], "pass")
            config = yaml.safe_load((product_root / "ael-workspace/project.yaml").read_text())
            self.assertEqual(config["assurance"]["level"], "guarded")
            self.assertNotIn("enforcement", config)
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
                "workspace": "ael-workspace",
                "config": "ael-workspace/project.yaml",
                "work_item_provider": "noop",
                "work_item_id_pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$",
            }

            created = ensure_product_workspace(product)

            self.assertTrue((product_root / "ael-workspace" / "bmad-output" / "design-artifacts").is_dir())
            self.assertIn("ael-workspace/bmad-output/design-artifacts", created)
            self.assertFalse((product_root / ".github").exists())
            self.assertIn(
                "Local AEL execution state",
                (product_root / "ael-workspace" / "runs" / ".gitignore").read_text(encoding="utf-8"),
            )

    def test_init_rejects_noncanonical_workspace_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            product_root = root / "product"
            product_root.mkdir()
            original_products_dir = ael_init.products_dir
            try:
                ael_init.products_dir = lambda: root / ".ael-products"
                result = ael_init.cmd_init(
                    Namespace(
                        product_root=str(product_root),
                        product_id="demo",
                        product_name="Demo",
                        profile="generic",
                        workspace="harness-workspace",
                        work_item_provider="",
                        work_item_id_pattern="",
                        overwrite_config=False,
                        install_bmad=False,
                        assurance="local",
                    )
                )
            finally:
                ael_init.products_dir = original_products_dir

            self.assertEqual(result, 0)
            self.assertFalse((product_root / "harness-workspace").exists())
            self.assertFalse((product_root / "ael-workspace").exists())

    def test_init_blocks_explicit_provider_change_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            product_root = root / "product"
            product_root.mkdir()
            products_root = root / ".ael-products"
            original_products_dir = ael_init.products_dir
            try:
                ael_init.products_dir = lambda: products_root
                base_args = dict(
                    product_root=str(product_root),
                    product_id="demo",
                    product_name="Demo",
                    profile="generic",
                    workspace="ael-workspace",
                    work_item_id_pattern="",
                    overwrite_config=False,
                    install_bmad=False,
                    assurance="local",
                )
                ael_init.cmd_init(Namespace(**base_args, work_item_provider="noop"))
                ael_init.cmd_init(Namespace(**base_args, work_item_provider="jira"))
            finally:
                ael_init.products_dir = original_products_dir

            registry = yaml.safe_load((products_root / "registry.yaml").read_text(encoding="utf-8"))
            config = yaml.safe_load((product_root / "ael-workspace" / "project.yaml").read_text(encoding="utf-8"))
            self.assertEqual(registry["products"][0]["work_item_provider"], "noop")
            self.assertEqual(config["work_item"]["provider"], "noop")

    def test_generic_workspace_templates_do_not_leak_product_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product_root = Path(tmp)
            product = {
                "id": "demo",
                "name": "Demo",
                "profile": "generic",
                "root": str(product_root),
                "workspace": "ael-workspace",
                "config": "ael-workspace/project.yaml",
                "work_item_provider": "noop",
                "work_item_id_pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$",
            }

            ensure_product_workspace(product)

            generated_files = [
                product_root / "ael-workspace" / "README.md",
                product_root / "ael-workspace" / "project.yaml",
                product_root / "ael-workspace" / "knowledge" / "CONTEXT.md",
                product_root / "ael-workspace" / "planning" / "product-specs" / "index.md",
            ]
            forbidden = ["ExampleCorp", "Example Product", "internal-product"]
            leaked: list[str] = []
            for path in generated_files:
                text = path.read_text(encoding="utf-8")
                for term in forbidden:
                    if term in text:
                        leaked.append(f"{path.relative_to(product_root)}:{term}")

            self.assertEqual([], leaked)

            config = yaml.safe_load((product_root / "ael-workspace/project.yaml").read_text(encoding="utf-8"))
            self.assertEqual(config["bmad"]["output_root"], "ael-workspace/bmad-output")
            self.assertEqual(config["bmad"]["normalized_planning_root"], "ael-workspace/planning")

    def test_bmad_config_pins_design_artifacts(self) -> None:
        product = {
            "id": "demo",
            "name": "Demo",
            "workspace": "ael-workspace",
        }

        block = render_bmad_config_block(product)

        self.assertIn("design_artifacts", block)
        self.assertIn("[modules.bmm]", block)
        self.assertIn("[modules.cis]", block)
        self.assertIn("{project-root}/ael-workspace/bmad-output/design-artifacts", block)

    def test_bmad_config_replaces_legacy_managed_block_without_duplication(self) -> None:
        legacy = """owner = \"product\"

# BEGIN harness-engineering managed BMAD output
[core]
output_folder = \"{project-root}/harness-workspace/bmad-output\"
# END harness-engineering managed BMAD output

tail = \"preserved\"
"""
        block = render_bmad_config_block({"workspace": "ael-workspace"})

        updated = upsert_managed_block(legacy, block)

        self.assertEqual(updated.count("managed BMAD output"), 2)
        self.assertNotIn("harness-workspace/bmad-output", updated)
        self.assertNotIn("BEGIN harness-engineering", updated)
        self.assertIn("BEGIN buildrun-agent-engineering-lifecycle", updated)
        self.assertIn('owner = "product"', updated)
        self.assertIn('tail = "preserved"', updated)

    def test_remove_product_unregisters_and_clears_active_without_deleting_workspace(self) -> None:
        if yaml is None:
            self.skipTest("PyYAML is required")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ael_products = root / ".ael" / "products"
            product_root = root / "product"
            (product_root / "ael-workspace").mkdir(parents=True)
            (product_root / "_bmad").mkdir()
            ael_products.mkdir(parents=True)
            (ael_products / "registry.yaml").write_text(
                yaml.safe_dump(
                    {
                        "products": [
                            {
                                "id": "demo",
                                "name": "Demo",
                                "root": str(product_root),
                                "workspace": "ael-workspace",
                                "config": "ael-workspace/project.yaml",
                            }
                        ]
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            (ael_products / "active-product.json").write_text(
                '{"product_id":"demo","root":"' + str(product_root) + '","workspace":"ael-workspace"}',
                encoding="utf-8",
            )

            original_products_dir = ael_init.products_dir
            try:
                ael_init.products_dir = lambda: ael_products
                result = ael_init.remove_product(
                    Namespace(product_id="demo", product_root="", delete_generated=False, force=False, dry_run=False)
                )
            finally:
                ael_init.products_dir = original_products_dir

            self.assertEqual(result["reason"], "PRODUCT_REMOVED")
            registry = yaml.safe_load((ael_products / "registry.yaml").read_text(encoding="utf-8"))
            self.assertEqual(registry["products"], [])
            self.assertFalse((ael_products / "active-product.json").exists())
            self.assertTrue((product_root / "ael-workspace").is_dir())
            self.assertTrue((product_root / "_bmad").is_dir())

    def test_remove_product_requires_force_to_delete_generated_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ael_products = root / ".ael" / "products"
            product_root = root / "product"
            (product_root / "ael-workspace").mkdir(parents=True)
            ael_products.mkdir(parents=True)
            (ael_products / "registry.yaml").write_text(
                "products:\n- id: demo\n  name: Demo\n  root: " + str(product_root) + "\n  workspace: ael-workspace\n",
                encoding="utf-8",
            )

            original_products_dir = ael_init.products_dir
            try:
                ael_init.products_dir = lambda: ael_products
                result = ael_init.remove_product(
                    Namespace(product_id="demo", product_root="", delete_generated=True, force=False, dry_run=False)
                )
            finally:
                ael_init.products_dir = original_products_dir

            self.assertEqual(result["reason"], "DELETE_GENERATED_REQUIRES_FORCE")
            self.assertTrue((product_root / "ael-workspace").is_dir())

    def test_remove_product_deletes_generated_paths_with_force(self) -> None:
        if yaml is None:
            self.skipTest("PyYAML is required")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ael_products = root / ".ael" / "products"
            product_root = root / "product"
            (product_root / "ael-workspace").mkdir(parents=True)
            (product_root / "_bmad-output").mkdir()
            ael_products.mkdir(parents=True)
            (ael_products / "registry.yaml").write_text(
                yaml.safe_dump(
                    {
                        "products": [
                            {
                                "id": "demo",
                                "name": "Demo",
                                "root": str(product_root),
                                "workspace": "ael-workspace",
                            }
                        ]
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )

            original_products_dir = ael_init.products_dir
            try:
                ael_init.products_dir = lambda: ael_products
                result = ael_init.remove_product(
                    Namespace(product_id="demo", product_root="", delete_generated=True, force=True, dry_run=False)
                )
            finally:
                ael_init.products_dir = original_products_dir

            self.assertEqual(result["reason"], "PRODUCT_REMOVED")
            self.assertFalse((product_root / "ael-workspace").exists())
            self.assertFalse((product_root / "_bmad-output").exists())

    def test_remove_product_dry_run_does_not_modify_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ael_products = root / ".ael" / "products"
            product_root = root / "product"
            (product_root / "ael-workspace").mkdir(parents=True)
            ael_products.mkdir(parents=True)
            registry_text = "products:\n- id: demo\n  name: Demo\n  root: " + str(product_root) + "\n  workspace: ael-workspace\n"
            (ael_products / "registry.yaml").write_text(registry_text, encoding="utf-8")

            original_products_dir = ael_init.products_dir
            try:
                ael_init.products_dir = lambda: ael_products
                result = ael_init.remove_product(
                    Namespace(product_id="demo", product_root="", delete_generated=True, force=False, dry_run=True)
                )
            finally:
                ael_init.products_dir = original_products_dir

            self.assertEqual(result["reason"], "PRODUCT_REMOVE_DRY_RUN")
            self.assertEqual((ael_products / "registry.yaml").read_text(encoding="utf-8"), registry_text)
            self.assertTrue((product_root / "ael-workspace").is_dir())


if __name__ == "__main__":
    unittest.main()
