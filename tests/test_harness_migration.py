#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from harness_migration import audit_workspace  # noqa: E402


class HarnessMigrationTest(unittest.TestCase):
    def test_completed_legacy_task_is_not_selected_for_migration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            task = product / "harness-workspace/planning/tasks/done-task"
            task.mkdir(parents=True)
            (task / "00-任务卡.md").write_text("- 当前状态：已完成\n")
            (task / "planning_gate_pass.json").write_text("{}")
            report = audit_workspace(product)
            self.assertEqual(report["legacy"], ["done-task"])
            self.assertEqual(report["needs_migration"], [])

    def test_active_credentialed_task_requires_migration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            task = product / "harness-workspace/planning/tasks/active-task"
            task.mkdir(parents=True)
            (task / "00-任务卡.md").write_text("- 当前状态：进行中\n")
            (task / "planning_gate_pass.json").write_text("{}")
            self.assertEqual(audit_workspace(product)["needs_migration"], ["active-task"])


if __name__ == "__main__":
    unittest.main()
