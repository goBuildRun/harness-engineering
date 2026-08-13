#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from harness_migration import audit_workspace, migration_eligibility  # noqa: E402
import harness_migration_commands  # noqa: E402


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
            self.assertEqual(
                migration_eligibility(product, "active-task"),
                (True, "TASK_MIGRATION_ELIGIBLE"),
            )

    def test_only_active_credentialed_tasks_are_migration_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            tasks = product / "harness-workspace/planning/tasks"
            done = tasks / "done-task"
            done.mkdir(parents=True)
            (done / "00-任务卡.md").write_text("- 当前状态：已完成\n")
            (done / "planning_gate_pass.json").write_text("{}")
            missing = tasks / "missing-credential"
            missing.mkdir()
            (missing / "00-任务卡.md").write_text("- 当前状态：进行中\n")
            self.assertEqual(
                migration_eligibility(product, "done-task")[1],
                "COMPLETED_LEGACY_MIGRATION_FORBIDDEN",
            )
            self.assertEqual(
                migration_eligibility(product, "missing-credential")[1],
                "MIGRATION_CREDENTIAL_MISSING",
            )
            self.assertEqual(
                migration_eligibility(product, "absent")[1],
                "MIGRATION_TASK_NOT_FOUND",
            )

    def test_migrate_command_rejects_ineligible_task_without_workspace_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            task = product / "harness-workspace/planning/tasks/done-task"
            task.mkdir(parents=True)
            (task / "00-任务卡.md").write_text("- 当前状态：已完成\n")
            (task / "planning_gate_pass.json").write_text("{}")
            captured = []
            with mock.patch.object(harness_migration_commands, "dump_json", side_effect=captured.append):
                harness_migration_commands.cmd_migrate(SimpleNamespace(
                    product_root=str(product), harness_root=str(Path(__file__).resolve().parents[1]),
                    task_id="done-task", reason="should not migrate",
                ))
            self.assertEqual(captured[-1]["decision"], "block")
            self.assertFalse((product / "harness-workspace/runs/tasks/done-task").exists())


if __name__ == "__main__":
    unittest.main()
