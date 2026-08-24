from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".ael" / "scripts"
import sys
sys.path.insert(0, str(SCRIPTS))

from ael_knowledge import sync_planning  # noqa: E402
from ael_planning import plan_batch  # noqa: E402
from task_workspace import activate_task  # noqa: E402
from workspace_paths import load_layout  # noqa: E402


class HarnessPlanningTest(unittest.TestCase):
    def _product(self, root: Path) -> tuple[Path, Path]:
        product = root / "product"
        workspace = product / "ael-workspace"
        task = workspace / "planning" / "tasks" / "story"
        task.mkdir(parents=True)
        (workspace / "project.yaml").write_text(
            "product:\n  id: demo\n  name: Demo\n  profile: generic\n"
            "workspace:\n  root: ael-workspace\n  planning: planning\n"
            "  runs: runs\n  knowledge: knowledge\n  evidence: evidence\n"
            "planning:\n  product_specs: product-specs\n  exec_plans_active: exec-plans/active\n"
            "  exec_plans_completed: exec-plans/completed\n  tasks: tasks\n"
            "work_item:\n  provider: noop\n",
            encoding="utf-8",
        )
        (workspace / "planning" / "product-specs").mkdir(parents=True)
        (workspace / "planning" / "product-specs" / "demo.md").write_text(
            "---\nspec_level: L3\n---\n\n# Demo\n", encoding="utf-8"
        )
        (task / "00-任务卡.md").write_text(
            "任务编号：`WI-demo`\n产品规格链接：`product-specs/demo.md`\n", encoding="utf-8"
        )
        for name in ("01-需求与背景.md", "02-影响分析.md", "03-实施方案.md",
                     "04-实施记录.md", "05-QA验收.md", "06-交付结论.md"):
            (task / name).write_text(f"# {name}\n", encoding="utf-8")
        return product, task

    def test_plan_batch_writes_children_without_shared_active_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product, task = self._product(Path(tmp))
            layout = load_layout(ROOT, product)
            result = plan_batch(layout, level="L3", task_dirs=[str(task)])
            self.assertEqual(result["decision"], "pass")
            self.assertIn("context_sync", result)
            child = product / "ael-workspace/runs/planning" / result["batch_id"] / "children/WI-demo.json"
            self.assertTrue(child.is_file())
            activated = activate_task(layout, "WI-demo", task_scoped=True)
            self.assertTrue(activated["ok"])
            self.assertTrue((product / "ael-workspace/runs/tasks/WI-demo/planning_gate_pass.json").is_file())
            self.assertFalse((product / "ael-workspace/runs/planning_gate_pass.json").exists())

    def test_knowledge_sync_uses_input_digest_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product, _ = self._product(Path(tmp))
            layout = load_layout(ROOT, product)
            first = sync_planning(layout)
            second = sync_planning(layout)
            self.assertFalse(first["cache_hit"])
            self.assertTrue(second["cache_hit"])
            cache = product / "ael-workspace/runs/planning/context-sync.json"
            self.assertEqual(first["input_digest"], json.loads(cache.read_text())["input_digest"])

    def test_global_preflight_short_circuits_incomplete_l3_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product, task = self._product(Path(tmp))
            (task / "06-交付结论.md").unlink()
            result = plan_batch(load_layout(ROOT, product), level="L3", task_dirs=[str(task)])
            self.assertEqual(result["decision"], "block")
            self.assertEqual(result["reason"], "GLOBAL_PREFLIGHT_TASK_PACKAGE_INCOMPLETE")
            self.assertFalse((product / "ael-workspace/runs/planning").exists())

    def test_cli_plan_uses_single_batch_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product, task = self._product(Path(tmp))
            completed = subprocess.run(
                ["bash", str(SCRIPTS / "ael"), "--product-root", str(product),
                 "plan", "--level", "L3", "--task-dir", str(task)],
                cwd=ROOT,
                env={**os.environ, "AEL_PRODUCT_ROOT": str(product), "AEL_PRETTY": "0"},
                text=True, capture_output=True, check=True,
            )
            result = json.loads(completed.stdout)
            self.assertEqual(result["reason"], "PLANNING_BATCH_READY")
            self.assertEqual(len(result["children"]), 1)
            item = result["children"][0]["work_item_id"]
            started = subprocess.run(
                ["bash", str(SCRIPTS / "agent_start.sh"), item], cwd=ROOT,
                env={**os.environ, "AEL_PRODUCT_ROOT": str(product),
                     "WORK_ITEM_PROVIDER": "noop", "AEL_PRETTY": "0"},
                text=True, capture_output=True, check=False, timeout=20,
            )
            self.assertEqual(json.loads(started.stdout)["decision"], "pass", started.stdout + started.stderr)
            self.assertFalse((product / "ael-workspace/runs/active_task.json").exists(),
                             (product / "ael-workspace/runs/active_task.json").read_text()
                             if (product / "ael-workspace/runs/active_task.json").exists() else "")
            finished = subprocess.run(
                ["bash", str(SCRIPTS / "ael"), "--product-root", str(product),
                 "finish", item, "--skip-legacy-gates"], cwd=ROOT,
                env={**os.environ, "AEL_PRODUCT_ROOT": str(product), "AEL_PRETTY": "0"},
                text=True, capture_output=True, check=False, timeout=20,
            )
            self.assertNotIn("ACTIVE_TASK_BINDING_CONFLICT", finished.stdout)


if __name__ == "__main__":
    unittest.main()
