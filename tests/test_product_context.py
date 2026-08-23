#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from product_context import ProductContextError, resolve_product_context  # noqa: E402


def write_project(root: Path, product_id: str, provider: str = "noop") -> None:
    workspace = root / "harness-workspace"
    workspace.mkdir(parents=True)
    (workspace / "project.yaml").write_text(
        f"""
product:
  id: {product_id}
  name: {product_id}
  profile: generic
workspace:
  root: harness-workspace
  planning: planning
  runs: runs
  knowledge: knowledge
  evidence: evidence
work_item:
  provider: {provider}
""".lstrip(),
        encoding="utf-8",
    )


@unittest.skipIf(yaml is None, "PyYAML is required")
class ProductContextTest(unittest.TestCase):
    def test_planning_gate_exports_resolved_product_before_changing_cwd(self) -> None:
        script = (SCRIPT_DIR / "bmad_entry_gate.sh").read_text(encoding="utf-8")

        resolve_at = script.index('PRODUCT_ROOT="$(bash "$SCRIPT_DIR/product_root.sh")"')
        export_at = script.index('export HARNESS_PRODUCT_ROOT="$PRODUCT_ROOT"')
        chdir_at = script.index('cd "$HARNESS_ROOT"')

        self.assertLess(resolve_at, export_at)
        self.assertLess(export_at, chdir_at)

    def test_planning_gate_passes_resolved_task_dir_to_contract_check(self) -> None:
        script = (SCRIPT_DIR / "bmad_entry_gate.sh").read_text(encoding="utf-8")

        self.assertIn(
            'task_contract_check.sh" --task-dir "$TASK_DIR_ABS"',
            script,
        )
        self.assertNotIn(
            'task_contract_check.sh" --task-dir "$TASK_DIR"',
            script,
        )

    def test_bmad_gate_retry_skips_an_already_passed_planning_stage(self) -> None:
        script = (SCRIPT_DIR / "bmad_entry_gate.sh").read_text(encoding="utf-8")

        self.assertIn("PLANNING_ALREADY_PASSED", script)
        self.assertIn('"$PLANNING_ALREADY_PASSED" != "true"', script)

    def test_env_product_id_overrides_active_product(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp) / "harness"
            products_dir = harness / ".harness" / "products"
            product_a = Path(tmp) / "product-a"
            product_b = Path(tmp) / "product-b"
            products_dir.mkdir(parents=True)
            write_project(product_a, "product-a")
            write_project(product_b, "product-b")
            (products_dir / "registry.yaml").write_text(
                yaml.safe_dump(
                    {
                        "products": [
                            {"id": "product-a", "name": "A", "root": str(product_a), "workspace": "harness-workspace"},
                            {"id": "product-b", "name": "B", "root": str(product_b), "workspace": "harness-workspace"},
                        ]
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            (products_dir / "active-product.json").write_text(
                json.dumps({"product_id": "product-a", "root": str(product_a)}),
                encoding="utf-8",
            )

            context = resolve_product_context(harness, environ={"HARNESS_PRODUCT_ID": "product-b"})

        self.assertEqual(product_b.resolve(), context.root)
        self.assertEqual("product-b", context.product_id)
        self.assertEqual("environment", context.source)

    def test_explicit_unknown_product_id_does_not_fallback_to_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp) / "harness"
            products_dir = harness / ".harness" / "products"
            product_a = Path(tmp) / "product-a"
            products_dir.mkdir(parents=True)
            write_project(product_a, "product-a")
            (products_dir / "registry.yaml").write_text(
                yaml.safe_dump({"products": [{"id": "product-a", "root": str(product_a)}]}, sort_keys=False),
                encoding="utf-8",
            )
            (products_dir / "active-product.json").write_text(
                json.dumps({"product_id": "product-a", "root": str(product_a)}),
                encoding="utf-8",
            )

            with self.assertRaises(ProductContextError):
                resolve_product_context(harness, product_id="missing-product")

    def test_cwd_discovery_wins_over_active_product(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp) / "harness"
            products_dir = harness / ".harness" / "products"
            product_a = Path(tmp) / "product-a"
            product_b = Path(tmp) / "product-b"
            nested_b = product_b / "src" / "feature"
            products_dir.mkdir(parents=True)
            nested_b.mkdir(parents=True)
            write_project(product_a, "product-a")
            write_project(product_b, "product-b")
            (products_dir / "registry.yaml").write_text(
                yaml.safe_dump(
                    {
                        "products": [
                            {"id": "product-a", "root": str(product_a), "workspace": "harness-workspace"},
                            {"id": "product-b", "root": str(product_b), "workspace": "harness-workspace"},
                        ]
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            (products_dir / "active-product.json").write_text(
                json.dumps({"product_id": "product-a", "root": str(product_a)}),
                encoding="utf-8",
            )

            context = resolve_product_context(harness, cwd=nested_b)

        self.assertEqual(product_b.resolve(), context.root)
        self.assertEqual("cwd:registry", context.source)

    def test_harness_product_env_outputs_session_exports_without_mutating_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp) / "harness"
            scripts = harness / ".harness" / "scripts"
            products_dir = harness / ".harness" / "products"
            product_a = Path(tmp) / "product-a"
            product_b = Path(tmp) / "product-b"
            products_dir.mkdir(parents=True)
            scripts.mkdir(parents=True)
            write_project(product_a, "product-a")
            write_project(product_b, "product-b")
            active_payload = json.dumps({"product_id": "product-a", "root": str(product_a)})
            (products_dir / "active-product.json").write_text(active_payload, encoding="utf-8")
            (products_dir / "registry.yaml").write_text(
                yaml.safe_dump(
                    {
                        "products": [
                            {"id": "product-a", "root": str(product_a), "workspace": "harness-workspace"},
                            {"id": "product-b", "root": str(product_b), "workspace": "harness-workspace"},
                        ]
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )

            out = subprocess.check_output(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "harness_product.py"),
                    "--harness-root",
                    str(harness),
                    "env",
                    "--product-id",
                    "product-b",
                ],
                cwd=ROOT,
                env={**os.environ, "HARNESS_PRETTY": "0"},
                text=True,
            )
            active_after = (products_dir / "active-product.json").read_text(encoding="utf-8")

        self.assertIn(f"export HARNESS_PRODUCT_ROOT={str(product_b.resolve())}", out)
        self.assertIn("export HARNESS_PRODUCT_ID=product-b", out)
        self.assertEqual(active_payload, active_after)

    def test_work_item_provider_uses_harness_product_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp) / "harness"
            products_dir = harness / ".harness" / "products"
            product_a = Path(tmp) / "product-a"
            product_b = Path(tmp) / "product-b"
            products_dir.mkdir(parents=True)
            write_project(product_a, "product-a", provider="noop")
            write_project(product_b, "product-b", provider="jira")
            (products_dir / "registry.yaml").write_text(
                yaml.safe_dump(
                    {
                        "products": [
                            {"id": "product-a", "root": str(product_a), "workspace": "harness-workspace"},
                            {"id": "product-b", "root": str(product_b), "workspace": "harness-workspace"},
                        ]
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            (products_dir / "active-product.json").write_text(
                json.dumps({"product_id": "product-a", "root": str(product_a)}),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"HARNESS_PRODUCT_ID": "product-b"}, clear=False):
                from work_item_providers import load_config

                cfg = load_config(harness)

        self.assertEqual("jira", cfg["provider"])

    def test_work_item_cli_blocks_on_unknown_harness_product_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp) / "harness"
            products_dir = harness / ".harness" / "products"
            product_a = Path(tmp) / "product-a"
            products_dir.mkdir(parents=True)
            write_project(product_a, "product-a")
            (products_dir / "registry.yaml").write_text(
                yaml.safe_dump({"products": [{"id": "product-a", "root": str(product_a)}]}, sort_keys=False),
                encoding="utf-8",
            )

            out = subprocess.check_output(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "work_item.py"),
                    "--harness-root",
                    str(harness),
                    "capabilities",
                ],
                cwd=ROOT,
                env={**os.environ, "HARNESS_PRODUCT_ID": "missing-product", "HARNESS_PRETTY": "0"},
                text=True,
            )
            data = json.loads(out)

        self.assertEqual("block", data["decision"])
        self.assertIn("PRODUCT_NOT_REGISTERED", data["reason"])


if __name__ == "__main__":
    unittest.main()
