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
            (task / "planning_gate_pass.json").write_text('{"decision":"pass"}')
            report = audit_workspace(product)
            self.assertEqual(report["legacy"], ["done-task"])
            self.assertEqual(report["needs_migration"], [])

    def test_plain_status_done_is_completed_legacy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            task = product / "harness-workspace/planning/tasks/done-task"
            task.mkdir(parents=True)
            (task / "00-任务卡.md").write_text("- 状态：done\n")
            (task / "planning_gate_pass.json").write_text('{"decision":"pass"}')
            report = audit_workspace(product)
            self.assertEqual(report["legacy"], ["done-task"])
            self.assertEqual(report["needs_migration"], [])

    def test_terminal_prefix_with_pending_acceptance_remains_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            tasks = product / "harness-workspace/planning/tasks"
            for task_id, status in (
                ("pending-acceptance", "implementation-complete / pending deployment acceptance"),
                ("pending-sync", "已完成，待同步外部状态"),
            ):
                task = tasks / task_id
                task.mkdir(parents=True)
                (task / "00-任务卡.md").write_text(f"- 状态：{status}\n")
                (task / "planning_gate_pass.json").write_text('{"decision":"pass"}')
            report = audit_workspace(product)
            self.assertEqual(report["needs_migration"], ["pending-acceptance", "pending-sync"])
            self.assertEqual(report["legacy"], [])

    def test_active_credentialed_task_requires_migration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            task = product / "harness-workspace/planning/tasks/active-task"
            task.mkdir(parents=True)
            (task / "00-任务卡.md").write_text("- 当前状态：进行中\n")
            (task / "planning_gate_pass.json").write_text('{"decision":"pass"}')
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
            (done / "planning_gate_pass.json").write_text('{"decision":"pass"}')
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

    def test_unknown_status_is_not_assumed_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            task = product / "harness-workspace/planning/tasks/ambiguous-task"
            task.mkdir(parents=True)
            (task / "00-任务卡.md").write_text("# 任务卡\n\n- 风险：L2\n")
            (task / "planning_gate_pass.json").write_text('{"decision":"pass"}')
            report = audit_workspace(product)
            self.assertEqual(report["unknown_status"], ["ambiguous-task"])
            self.assertEqual(report["needs_migration"], [])
            self.assertEqual(
                migration_eligibility(product, "ambiguous-task"),
                (False, "MIGRATION_STATUS_UNKNOWN"),
            )

    def test_malformed_or_failed_credentials_are_not_migration_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            tasks = product / "harness-workspace/planning/tasks"
            for task_id, payload in (("malformed", "{"), ("failed", '{"decision":"block"}')):
                task = tasks / task_id
                task.mkdir(parents=True)
                (task / "00-任务卡.md").write_text("- 当前状态：进行中\n")
                (task / "planning_gate_pass.json").write_text(payload)
                self.assertEqual(
                    migration_eligibility(product, task_id)[1],
                    "MIGRATION_CREDENTIAL_MISSING",
                )

    def test_migrate_command_rejects_ineligible_task_without_workspace_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            task = product / "harness-workspace/planning/tasks/done-task"
            task.mkdir(parents=True)
            (task / "00-任务卡.md").write_text("- 当前状态：已完成\n")
            (task / "planning_gate_pass.json").write_text('{"decision":"pass"}')
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
