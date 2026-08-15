#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from task_contract_check import task_file  # noqa: E402
from workspace_paths import load_layout, resolve_task_dir  # noqa: E402


class TaskPathResolutionTest(unittest.TestCase):
    def test_task_dir_accepts_absolute_product_planning_and_basename_forms(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            workspace = product / "harness-workspace"
            task_dir = workspace / "planning/tasks/2026-08-15-story"
            task_dir.mkdir(parents=True)
            plan = task_dir / "03-实施方案.md"
            plan.write_text("# plan\n", encoding="utf-8")
            (workspace / "project.yaml").write_text(
                "product:\n  id: demo\n  name: Demo\n  profile: generic\n"
                "workspace:\n  root: harness-workspace\n  planning: planning\n  runs: runs\n"
                "  knowledge: knowledge\n  evidence: evidence\nwork_item:\n  provider: noop\n",
                encoding="utf-8",
            )
            layout = load_layout(ROOT, product)

            values = (
                str(task_dir),
                "harness-workspace/planning/tasks/2026-08-15-story",
                "tasks/2026-08-15-story",
                "2026-08-15-story",
            )
            for value in values:
                with self.subTest(value=value):
                    self.assertEqual(resolve_task_dir(layout, value, cwd=ROOT), task_dir.resolve())
                    args = SimpleNamespace(plan="", task_dir=value)
                    self.assertEqual(task_file(args, layout), plan.resolve())


if __name__ == "__main__":
    unittest.main()
