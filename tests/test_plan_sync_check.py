#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from worktree_baseline import capture_baseline  # noqa: E402
from plan_sync_check import active_task_baseline, extract_planned_paths, task_changed  # noqa: E402
from business_paths import find_business_paths, load_business_roots  # noqa: E402
from workspace_paths import load_layout  # noqa: E402


class PlanSyncBaselineTest(unittest.TestCase):
    def _product(self, root: Path) -> tuple[Path, Path, Path]:
        product = root / "product"
        task_dir = product / "harness-workspace" / "planning" / "tasks" / "2026-07-26-wi-demo"
        task_dir.mkdir(parents=True)
        (product / "services" / "gateway").mkdir(parents=True)
        (product / "services" / "gateway" / "legacy.py").write_text("base\n", encoding="utf-8")
        (product / "harness-workspace" / "project.yaml").write_text(
            """
product:
  id: demo
  name: Demo
  profile: generic
workspace:
  root: harness-workspace
  planning: planning
  runs: runs
  knowledge: knowledge
  evidence: evidence
""".lstrip(),
            encoding="utf-8",
        )
        (task_dir / "03-实施方案.md").write_text(
            "| ID | name | owner | depends_on | read_files | write_files | action | verify | done |\n"
            "|---|---|---|---|---|---|---|---|---|\n"
            "| T1 | demo | backend-agent | 无 | `README.md` | `services/gateway/new.py` | write | test | done |\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "init", "-q"], cwd=product, check=True)
        subprocess.run(["git", "config", "user.email", "harness@example.invalid"], cwd=product, check=True)
        subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=product, check=True)
        subprocess.run(["git", "add", "."], cwd=product, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=product, check=True)

        workspace = product / "harness-workspace" / "runs" / "tasks" / "wi-demo"
        workspace.mkdir(parents=True)
        active = product / "harness-workspace" / "runs" / "active_task.json"
        active.write_text(json.dumps({"work_item_id": "wi-demo"}), encoding="utf-8")
        return product, task_dir, workspace / "worktree_baseline.json"

    def _run(self, product: Path, task_dir: Path, *extra: str) -> dict:
        env = {**os.environ, "HARNESS_PRODUCT_ROOT": str(product)}
        out = subprocess.check_output(
            [
                sys.executable,
                str(SCRIPT_DIR / "plan_sync_check.py"),
                "--harness-root",
                str(ROOT),
                "--product-root",
                str(product),
                "--task-dir",
                str(task_dir),
                *extra,
            ],
            cwd=ROOT,
            env=env,
            text=True,
        )
        return json.loads(out)

    def test_plan_sync_uses_task_baseline_and_includes_new_untracked_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product, task_dir, baseline = self._product(Path(tmp))
            (product / "services" / "gateway" / "legacy.py").write_text("preexisting dirty\n", encoding="utf-8")
            capture_baseline(product, baseline, work_item_id="wi-demo")
            (product / "services" / "gateway" / "new.py").write_text("task change\n", encoding="utf-8")

            layout = load_layout(ROOT, product)
            active_id, active_baseline = active_task_baseline(layout)
            self.assertEqual(active_id, "wi-demo")
            self.assertEqual(active_baseline, baseline.resolve())
            self.assertIn("services/gateway/new.py", task_changed(layout))

            result = self._run(product, task_dir)

            self.assertEqual(result["decision"], "pass", result)
            self.assertIn("1 个变更路径", result["reason"])
            self.assertEqual(
                find_business_paths("`deer-flow` and `deer-flowing`", ("deer-flow/",)),
                ["deer-flow"],
            )

    def test_recovery_baseline_requires_reason_and_preserves_planned_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product, task_dir, baseline = self._product(Path(tmp))
            (product / "services" / "gateway" / "legacy.py").write_text("preexisting dirty\n", encoding="utf-8")
            (product / "services" / "gateway" / "new.py").write_text("task change\n", encoding="utf-8")

            recovered = self._run(
                product,
                task_dir,
                "--recover-baseline",
                "independent review confirmed legacy diff",
            )
            checked = self._run(product, task_dir)

            self.assertEqual(recovered["decision"], "pass")
            self.assertIn("PLAN_SYNC_BASELINE_RECOVERED", recovered["reason"])
            self.assertTrue(baseline.is_file())
            self.assertEqual(checked["decision"], "pass")

    def test_plan_sync_accepts_explicit_business_root_for_submodule_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product, task_dir, baseline = self._product(Path(tmp))
            (product / "harness-workspace" / "project.yaml").write_text(
                """
product:
  id: demo
platform_product:
  source_roots:
    runtime: deer-flow
workspace:
  root: harness-workspace
  planning: planning
  runs: runs
  knowledge: knowledge
  evidence: evidence
""".lstrip(),
                encoding="utf-8",
            )
            (task_dir / "03-实施方案.md").write_text(
                "| ID | write_files |\n|---|---|\n| T1 | `deer-flow` |\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "."], cwd=product, check=True)
            subprocess.run(["git", "commit", "-qm", "configure submodule root"], cwd=product, check=True)
            capture_baseline(product, baseline, work_item_id="wi-demo")
            subprocess.run(["git", "add", "."], cwd=product, check=True)
            subprocess.run(["git", "commit", "-qm", "capture task baseline"], cwd=product, check=True)
            subprocess.run(["git", "update-index", "--add", "--cacheinfo", "160000,1111111111111111111111111111111111111111,deer-flow"], cwd=product, check=True)

            layout = load_layout(ROOT, product)
            roots = load_business_roots(ROOT, product_root=product)
            self.assertEqual(roots, ("deer-flow/", "harness-workspace/"))
            self.assertIn("deer-flow", extract_planned_paths(layout, str(task_dir)))
            self.assertIn("deer-flow", task_changed(layout))

            result = self._run(product, task_dir)

            self.assertEqual(result["decision"], "pass", result)
            self.assertIn("1 个变更路径", result["reason"])


if __name__ == "__main__":
    unittest.main()
