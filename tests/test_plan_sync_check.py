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
from plan_sync_check import (  # noqa: E402
    active_task_baseline,
    active_task_planning_dir,
    extract_planned_paths,
    task_changed,
)
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
            self.assertEqual(active_task_planning_dir(layout), task_dir.resolve())
            self.assertIn("services/gateway/new.py", task_changed(layout))

            result = self._run(product, task_dir)

            self.assertEqual(result["decision"], "pass", result)
            self.assertIn("1 个变更路径", result["reason"])
            self.assertEqual(
                find_business_paths("`deer-flow` and `deer-flowing`", ("deer-flow/",)),
                ["deer-flow"],
            )

    def test_lean_task_id_selects_active_plan_over_stale_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product, task_dir, _baseline = self._product(Path(tmp))
            runs = product / "harness-workspace/runs"
            (runs / "active_task.json").write_text(json.dumps({"task_id": "wi-demo"}), encoding="utf-8")
            stale = product / "harness-workspace/planning/tasks/2026-07-25-wi-stale"
            stale.mkdir()
            (stale / "03-实施方案.md").write_text(
                "| ID | write_files |\n|---|---|\n| T1 | `services/stale.py` |\n",
                encoding="utf-8",
            )
            (runs / "planning_gate_pass.json").write_text(
                json.dumps({"task_dir": str(stale)}), encoding="utf-8"
            )
            (product / "services/gateway/new.py").write_text("task change\n", encoding="utf-8")

            layout = load_layout(ROOT, product)
            self.assertEqual(active_task_planning_dir(layout), task_dir.resolve())
            out = subprocess.check_output(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "plan_sync_check.py"),
                    "--harness-root",
                    str(ROOT),
                    "--product-root",
                    str(product),
                ],
                text=True,
            )

            self.assertEqual(json.loads(out)["decision"], "pass", out)

    def test_active_plan_does_not_inherit_scope_from_a_newer_unrelated_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product, task_dir, baseline = self._product(Path(tmp))
            newer = product / "harness-workspace/planning/tasks/2026-08-15-wi-unrelated"
            newer.mkdir()
            (newer / "03-实施方案.md").write_text(
                "| ID | write_files |\n|---|---|\n| T1 | `services/unrelated/new.py` |\n",
                encoding="utf-8",
            )
            capture_baseline(product, baseline, work_item_id="wi-demo")
            unrelated = product / "services/unrelated/new.py"
            unrelated.parent.mkdir(parents=True)
            unrelated.write_text("unplanned task change\n", encoding="utf-8")

            layout = load_layout(ROOT, product)
            planned = extract_planned_paths(layout, str(task_dir))
            self.assertNotIn("services/unrelated/new.py", planned)
            out = subprocess.check_output(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "plan_sync_check.py"),
                    "--harness-root",
                    str(ROOT),
                    "--product-root",
                    str(product),
                ],
                text=True,
            )

            result = json.loads(out)
            self.assertEqual(result["decision"], "block", result)
            self.assertIn("services/unrelated/new.py", result["reason"])

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

    def test_plan_sync_accepts_declared_directory_but_rejects_near_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product, task_dir, baseline = self._product(Path(tmp))
            (task_dir / "03-实施方案.md").write_text(
                "| ID | write_files |\n|---|---|\n| T1 | `services/gateway/` |\n",
                encoding="utf-8",
            )
            capture_baseline(product, baseline, work_item_id="wi-demo")
            (product / "services" / "gateway" / "new.py").write_text(
                "task change\n",
                encoding="utf-8",
            )

            allowed = self._run(product, task_dir)

            self.assertEqual(allowed["decision"], "pass", allowed)

            near_prefix = product / "services" / "gateway-archive" / "new.py"
            near_prefix.parent.mkdir(parents=True)
            near_prefix.write_text("unplanned\n", encoding="utf-8")

            blocked = self._run(product, task_dir)

            self.assertEqual(blocked["decision"], "block", blocked)
            self.assertIn("services/gateway-archive/new.py", blocked["reason"])

    def test_extract_planned_paths_preserves_spaces_inside_backticks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product, task_dir, _baseline = self._product(Path(tmp))
            expected = "services/Product Blueprint (Current).md"
            (task_dir / "03-实施方案.md").write_text(
                f"| ID | write_files |\n|---|---|\n| T1 | `{expected}` |\n",
                encoding="utf-8",
            )
            layout = load_layout(ROOT, product)

            planned = extract_planned_paths(layout, str(task_dir))

            self.assertIn(expected, planned)


if __name__ == "__main__":
    unittest.main()
