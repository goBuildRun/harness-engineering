#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from harness_runtime import (  # noqa: E402
    SCHEMA_VERSION,
    apply_code_health,
    atomic_write_result,
    cache_matches,
    classify_tier,
    task_kind_tier,
    default_result,
    finish_decision,
    fingerprint,
    git_changed,
    mechanical_code_health,
    load_result,
    resolve_task_id,
    result_path,
    telemetry_add_gc,
)
from harness_cycle_commands import begin_stage, finish_stage  # noqa: E402
from harness_scope import paths_within_scope  # noqa: E402
from harness_state import invalidate_if_stale  # noqa: E402
from harness_task_resolution import bind_active_task  # noqa: E402
import harness_commands  # noqa: E402
import harness_ci  # noqa: E402
import harness_cli  # noqa: E402
import harness_migration_commands  # noqa: E402


def complete_story_stages(product: Path, task_id: str) -> None:
    path = result_path(product, task_id)
    result = load_result(path)
    for stage in (
        "takeover", "planning", "implementation_test", "independent_qa",
        "deploy_provider", "finalize",
    ):
        if stage != "takeover":
            assert begin_stage(result, path, task_id, stage)["decision"] == "pass"
        assert finish_stage(
            result, path, task_id, stage, decision="pass", reason="STAGE_COMPLETED",
        )["decision"] == "pass"
    atomic_write_result(path, result)


class HarnessRuntimeTest(unittest.TestCase):
    def test_start_resume_monotonically_strengthens_tier_and_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=product, check=True)
            common = {
                "product_root": str(product),
                "harness_root": str(ROOT),
                "task_id": "resume-strict",
                "work_item": "WI-42",
                "kind": "implementation",
                "reason": "",
            }
            captured = []
            with mock.patch.object(harness_commands, "dump_json", side_effect=captured.append):
                harness_commands.cmd_start(SimpleNamespace(
                    **common, tier="standard", scope=["harness-workspace/planning/tasks/story"],
                ))
            baseline_path = product / "harness-workspace/runs/tasks/resume-strict/worktree_baseline.json"
            baseline_before = baseline_path.read_bytes()

            captured = []
            with mock.patch.object(harness_commands, "dump_json", side_effect=captured.append):
                harness_commands.cmd_start(SimpleNamespace(
                    **common, tier="strict", scope=["deer-flow", "harness-workspace/evidence"],
                ))

            result = captured[-1]["result"]
            self.assertEqual(captured[-1]["reason"], "TASK_RESUMED_WITH_STRONGER_BINDING")
            self.assertEqual(result["tier"], {"initial": "strict", "effective": "strict"})
            self.assertEqual(result["task"]["tier_floor"], "strict")
            self.assertEqual(result["task"]["scope"], [
                "deer-flow",
                "harness-workspace/evidence",
                "harness-workspace/planning/tasks/story",
            ])
            self.assertEqual(result["binding_revisions"][-1]["previous_tier"], "standard")
            self.assertEqual(baseline_path.read_bytes(), baseline_before)

    def test_start_resume_rejects_work_item_rebinding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=product, check=True)
            common = {
                "product_root": str(product), "harness_root": str(ROOT),
                "task_id": "resume-identity", "tier": "standard", "scope": ["src"],
                "kind": "implementation", "reason": "",
            }
            with mock.patch.object(harness_commands, "dump_json"):
                harness_commands.cmd_start(SimpleNamespace(**common, work_item="WI-1"))
            captured = []
            with mock.patch.object(harness_commands, "dump_json", side_effect=captured.append):
                harness_commands.cmd_start(SimpleNamespace(**common, work_item="WI-2"))
            self.assertEqual(captured[-1]["decision"], "block")
            self.assertEqual(captured[-1]["reason"], "TASK_RESUME_WORK_ITEM_MISMATCH")

    def test_start_resume_repairs_null_work_item_from_unique_planning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=product, check=True)
            task_id = "resume-planning-binding"
            common = {
                "product_root": str(product), "harness_root": str(ROOT), "task_id": task_id,
                "work_item": "", "kind": "implementation", "reason": "",
                "tier": "strict", "scope": ["src"],
            }
            with mock.patch.object(harness_commands, "dump_json"):
                harness_commands.cmd_start(SimpleNamespace(**common))
            path = product / "harness-workspace/runs/tasks" / task_id / "result.json"
            result = json.loads(path.read_text())
            result["checks"]["planning"] = {
                "decision": "pass", "stale": False, "fingerprint": "old",
                "subject_digest": "old", "source": "executed",
            }
            atomic_write_result(path, result)
            baseline = path.parent / "worktree_baseline.json"
            baseline_before = baseline.read_bytes()
            (product / "harness-workspace/project.yaml").write_text(
                "work_item:\n  provider: feishu\n  providers:\n"
                "    feishu:\n      status_update_mode: completed\n",
                encoding="utf-8",
            )
            task_dir = product / "harness-workspace/planning/tasks/2026-resume-planning-binding"
            task_dir.mkdir(parents=True)
            (task_dir / "planning_gate_pass.json").write_text(json.dumps({
                "decision": "pass",
                "work_item": {"id": task_id, "provider": "feishu"},
            }))

            captured = []
            with mock.patch.object(harness_commands, "dump_json", side_effect=captured.append):
                harness_commands.cmd_start(SimpleNamespace(**common))

            repaired = captured[-1]["result"]
            self.assertEqual(captured[-1]["reason"], "TASK_RESUMED_WITH_STRONGER_BINDING")
            self.assertEqual(repaired["work_item"], {"id": task_id, "provider": "feishu"})
            self.assertEqual(repaired["tier"], {"initial": "strict", "effective": "strict"})
            self.assertEqual(repaired["task"]["scope"], ["src"])
            self.assertEqual(repaired["task"]["work_item"], task_id)
            self.assertEqual(repaired["binding_revisions"][-1]["reason"],
                             "monotonic resume strengthening")
            self.assertTrue(repaired["checks"]["planning"]["stale"])
            self.assertIn("TASK_BINDING_CHANGED", repaired["blockers"])
            self.assertEqual(baseline.read_bytes(), baseline_before)

    def test_start_infers_unique_committed_work_item_and_preserves_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=product, check=True)
            task_id = "WI-43.3"
            task_dir = product / "harness-workspace/planning/tasks/2026-08-19-story-43-3"
            task_dir.mkdir(parents=True)
            (product / "harness-workspace/project.yaml").write_text(
                "work_item:\n  provider: feishu\n  providers:\n    feishu:\n      status_update_mode: completed\n",
                encoding="utf-8",
            )
            (task_dir / "planning_gate_pass.json").write_text(json.dumps({
                "decision": "pass",
                "work_item": {"id": task_id, "provider": "feishu"},
            }))
            captured = []
            with mock.patch.object(harness_commands, "dump_json", side_effect=captured.append):
                harness_commands.cmd_start(SimpleNamespace(
                    product_root=str(product), harness_root=str(ROOT), task_id=task_id,
                    work_item="", kind="implementation", reason="", tier="strict",
                    scope=["src"],
                ))

            result = captured[-1]["result"]
            self.assertEqual(result["work_item"], {"id": task_id, "provider": "feishu"})
            self.assertEqual(result["task"]["work_item"], task_id)

    def test_start_rejects_explicit_work_item_conflicting_with_committed_planning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=product, check=True)
            task_id = "execution-43-3"
            task_dir = product / "harness-workspace/planning/tasks" / task_id
            task_dir.mkdir(parents=True)
            (task_dir / "task.json").write_text(json.dumps({
                "task_id": task_id,
                "work_item": {"id": "WI-43.3", "provider": "feishu"},
            }))
            captured = []
            with mock.patch.object(harness_commands, "dump_json", side_effect=captured.append):
                harness_commands.cmd_start(SimpleNamespace(
                    product_root=str(product), harness_root=str(ROOT), task_id=task_id,
                    work_item="WI-other", kind="implementation", reason="", tier="strict",
                    scope=["src"],
                ))

            self.assertEqual(captured[-1]["decision"], "block")
            self.assertEqual(captured[-1]["reason"], "TASK_START_WORK_ITEM_MISMATCH")
            self.assertFalse((
                product / "harness-workspace/runs/tasks" / task_id / "result.json"
            ).exists())

    def test_start_blocks_ambiguous_cross_task_work_item_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=product, check=True)
            task_id = "WI-duplicate"
            tasks = product / "harness-workspace/planning/tasks"
            for name in ("story-a", "story-b"):
                task_dir = tasks / name
                task_dir.mkdir(parents=True)
                (task_dir / "phase0_pass.json").write_text(json.dumps({
                    "decision": "pass",
                    "work_item": {"id": task_id, "provider": "feishu"},
                }))
            captured = []
            with mock.patch.object(harness_commands, "dump_json", side_effect=captured.append):
                harness_commands.cmd_start(SimpleNamespace(
                    product_root=str(product), harness_root=str(ROOT), task_id=task_id,
                    work_item="", kind="implementation", reason="", tier="standard",
                    scope=["src"],
                ))

            self.assertEqual(captured[-1], {
                "decision": "block",
                "reason": "TASK_START_WORK_ITEM_AMBIGUOUS",
                "candidates": [
                    {"source": "story-a/phase0_pass.json", "id": task_id,
                     "provider": "feishu"},
                    {"source": "story-b/phase0_pass.json", "id": task_id,
                     "provider": "feishu"},
                ],
            })
            self.assertFalse((product / "harness-workspace/runs").exists())

    def test_start_blocks_conflicting_work_items_within_one_task_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=product, check=True)
            task_id = "intra-dir-conflict"
            task_dir = product / "harness-workspace/planning/tasks" / task_id
            task_dir.mkdir(parents=True)
            (task_dir / "task.json").write_text(json.dumps({
                "work_item": {"id": "WI-task", "provider": "feishu"},
            }))
            (task_dir / "planning_gate_pass.json").write_text(json.dumps({
                "decision": "pass", "work_item": {"id": "WI-gate", "provider": "feishu"},
            }))
            captured = []
            with mock.patch.object(harness_commands, "dump_json", side_effect=captured.append):
                harness_commands.cmd_start(SimpleNamespace(
                    product_root=str(product), harness_root=str(ROOT), task_id=task_id,
                    work_item="", kind="implementation", reason="", tier="strict",
                    scope=["src"],
                ))

            self.assertEqual(captured[-1]["reason"], "TASK_START_WORK_ITEM_AMBIGUOUS")
            self.assertEqual(captured[-1]["candidates"], [
                {"source": f"{task_id}/planning_gate_pass.json", "id": "WI-gate",
                 "provider": "feishu"},
                {"source": f"{task_id}/task.json", "id": "WI-task", "provider": "feishu"},
            ])
            self.assertFalse((product / "harness-workspace/runs").exists())

    def test_git_changed_preserves_unicode_commit_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=repo, check=True)
            target = repo / "03-实施方案.md"
            target.write_text("first\n", encoding="utf-8")
            subprocess.run(["git", "add", str(target.name)], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "unicode path"], cwd=repo, check=True)

            self.assertEqual(git_changed(repo, "HEAD"), ["03-实施方案.md"])

    def test_cost_baseline_never_substitutes_zero_for_unknown(self) -> None:
        baseline = json.loads(
            (ROOT / "tests" / "fixtures" / "lean-cost-baseline.json").read_text()
        )
        self.assertEqual(baseline["implementation"]["input_tokens"], "unknown")
        self.assertEqual(baseline["harness"]["gate_duration_ms"], "unknown")

    def test_result_has_four_invariants_and_unknown_implementation_cost(self) -> None:
        result = default_result("task-1", initial_tier="standard")
        self.assertEqual(result["schema_version"], SCHEMA_VERSION)
        self.assertEqual(
            set(result["invariants"]),
            {"task_identity", "scope", "risk_validation", "final_result"},
        )
        self.assertEqual(result["cost"]["implementation"]["input_tokens"], "unknown")
        self.assertFalse(result["cost"]["telemetry_complete"])

    def test_atomic_result_write_leaves_no_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "task" / "result.json"
            atomic_write_result(target, default_result("task-atomic"))
            self.assertEqual(json.loads(target.read_text())["task_id"], "task-atomic")
            self.assertFalse(target.with_suffix(".json.tmp").exists())

    def test_subject_or_policy_change_invalidates_validated_result(self) -> None:
        result = default_result("stale-task")
        result.update({
            "state": "validated", "decision": "pass",
            "subject": {"kind": "worktree", "digest": "subject-a"},
            "policy_digest": "policy-a",
        })
        result["invariants"] = {key: "pass" for key in result["invariants"]}
        result["checks"]["tests"] = {"decision": "pass", "fingerprint": "old"}
        self.assertTrue(invalidate_if_stale(result, "subject-b", "policy-a"))
        self.assertEqual(result["state"], "active")
        self.assertEqual(result["decision"], "block")
        self.assertIn("INPUT_CHANGED", result["blockers"])
        self.assertTrue(result["checks"]["tests"]["stale"])
        self.assertEqual(result["cost"]["harness"]["reruns"], 1)

    def test_unchanged_subject_does_not_invalidate(self) -> None:
        result = default_result("stable-task")
        result["subject"]["digest"] = "subject-a"
        result["policy_digest"] = "policy-a"
        self.assertFalse(invalidate_if_stale(result, "subject-a", "policy-a"))

    def test_task_inference_blocks_ambiguity_without_active_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            for task_id in ("task-a", "task-b"):
                target = product / "harness-workspace" / "runs" / "tasks" / task_id / "result.json"
                atomic_write_result(target, default_result(task_id))
            task_id, candidates = resolve_task_id(product, None)
            self.assertEqual(task_id, "")
            self.assertEqual(candidates, ["task-a", "task-b"])

    def test_task_inference_resolves_active_work_item_to_slugged_execution_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            runs = product / "harness-workspace" / "runs"
            runs.mkdir(parents=True)
            (runs / "active_task.json").write_text(
                json.dumps({"work_item_id": "WI-43.2"}), encoding="utf-8",
            )
            target = runs / "tasks" / "WI-43.2-binding-correction" / "result.json"
            result = default_result("WI-43.2-binding-correction", work_item={"id": "WI-43.2"})
            result["state"] = "validated"
            atomic_write_result(target, result)
            for task_id in ("old-active", "old-blocked"):
                old = runs / "tasks" / task_id / "result.json"
                result = default_result(task_id)
                result["state"] = "active" if task_id == "old-active" else "blocked"
                atomic_write_result(old, result)

            task_id, candidates = resolve_task_id(product, None)
            self.assertEqual(task_id, "WI-43.2-binding-correction")
            self.assertEqual(candidates, [])

    def test_task_inference_keeps_duplicate_active_work_item_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            runs = product / "harness-workspace" / "runs"
            (runs / "tasks").mkdir(parents=True)
            (runs / "active_task.json").write_text(
                json.dumps({"work_item_id": "WI-43.2"}), encoding="utf-8",
            )
            for task_id in ("WI-43.2-first", "WI-43.2-second"):
                result = default_result(task_id, work_item={"id": "WI-43.2"})
                result["state"] = "validated"
                atomic_write_result(runs / "tasks" / task_id / "result.json", result)

            task_id, candidates = resolve_task_id(product, None)
            self.assertEqual(task_id, "")
            self.assertEqual(candidates, ["WI-43.2-first", "WI-43.2-second"])

    def test_active_task_binding_preserves_workspace_fields_and_rejects_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            active = product / "harness-workspace/runs/active_task.json"
            active.parent.mkdir(parents=True)
            active.write_text(json.dumps({
                "work_item_id": "WI-43.2", "task_dir": "/planning/story-43.2",
            }))

            self.assertTrue(bind_active_task(product, "story-43.2-fix", "WI-43.2"))
            strengthened = json.loads(active.read_text())
            self.assertEqual(strengthened["task_id"], "story-43.2-fix")
            self.assertEqual(strengthened["task_dir"], "/planning/story-43.2")
            self.assertFalse(bind_active_task(product, "other-task", "WI-43.2"))
            self.assertFalse(bind_active_task(product, "story-43.2-fix", ""))
            self.assertEqual(json.loads(active.read_text()), strengthened)

    def test_task_resolution_rejects_conflicting_dual_identity_and_unknown_active_work_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            runs = product / "harness-workspace" / "runs"
            (runs / "tasks" / "execution-a").mkdir(parents=True)
            (runs / "active_task.json").write_text(json.dumps({
                "task_id": "execution-a", "work_item_id": "WI-current",
            }))
            conflicting = default_result("execution-a", work_item={"id": "WI-other"})
            conflicting["state"] = "validated"
            atomic_write_result(runs / "tasks" / "execution-a" / "result.json", conflicting)
            matching = default_result("execution-b", work_item={"id": "WI-current"})
            matching["state"] = "validated"
            atomic_write_result(runs / "tasks" / "execution-b" / "result.json", matching)
            old = default_result("old-active")
            atomic_write_result(runs / "tasks" / "old-active" / "result.json", old)

            task_id, candidates = resolve_task_id(product, None)
            self.assertEqual(task_id, "")
            self.assertEqual(candidates, [])

            (runs / "active_task.json").write_text(json.dumps({"work_item_id": "WI-missing"}))
            task_id, candidates = resolve_task_id(product, None)
            self.assertEqual(task_id, "")
            self.assertEqual(candidates, [])

    def test_runtime_task_ids_reject_path_traversal_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp) / "product"
            product.mkdir()
            captured = []
            with mock.patch.object(harness_commands, "dump_json", side_effect=captured.append):
                harness_commands.cmd_start(SimpleNamespace(
                    product_root=str(product), harness_root=str(ROOT), task_id="../../escape",
                    work_item="", kind="implementation", reason="", tier="lite", scope=["docs"],
                ))
            self.assertEqual(captured[-1]["reason"], "TASK_ID_INVALID")
            self.assertFalse((Path(tmp) / "escape").exists())
            self.assertEqual(resolve_task_id(product, "../escape"), ("", ["TASK_ID_INVALID"]))

            active = product / "harness-workspace/runs/active_task.json"
            active.parent.mkdir(parents=True)
            active.write_text(json.dumps({"task_id": "../escape"}))
            self.assertEqual(resolve_task_id(product, None), ("", ["TASK_ID_INVALID"]))

    def test_migration_preserves_committed_work_item_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            task_id = "legacy-task"
            task_dir = product / "harness-workspace" / "planning" / "tasks" / task_id
            task_dir.mkdir(parents=True)
            (task_dir / "00-任务卡.md").write_text("- 当前状态：进行中\n")
            (task_dir / "planning_gate_pass.json").write_text(json.dumps({
                "decision": "pass",
                "work_item": {"id": "WI-42", "provider": "jira"},
            }))
            captured = []
            with mock.patch.object(harness_migration_commands, "dump_json", side_effect=captured.append):
                harness_migration_commands.cmd_migrate(SimpleNamespace(
                    product_root=str(product), harness_root=str(ROOT),
                    task_id=task_id, reason="active legacy task",
                ))
            result = captured[-1]["result"]
            self.assertEqual(result["work_item"], {"id": "WI-42", "provider": "jira"})
            self.assertEqual(result["invariants"]["task_identity"], "pass")
            self.assertTrue(result["binding_digest"])
            self.assertEqual(result["blockers"], ["STANDARD_REVALIDATION_REQUIRED"])

            result["work_item"] = None
            result["invariants"]["task_identity"] = "pending"
            result["binding_digest"] = ""
            atomic_write_result(
                product / "harness-workspace" / "runs" / "tasks" / task_id / "result.json",
                result,
            )
            harness_migration_commands.cmd_migrate(SimpleNamespace(
                product_root=str(product), harness_root=str(ROOT),
                task_id=task_id, reason="active legacy task",
            ))
            repaired = json.loads((
                product / "harness-workspace" / "runs" / "tasks" / task_id / "result.json"
            ).read_text())
            self.assertEqual(repaired["work_item"], {"id": "WI-42", "provider": "jira"})
            self.assertEqual(repaired["invariants"]["task_identity"], "pass")

    def test_amend_migrated_task_adds_audited_scope_and_invalidates_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=product, check=True)
            task_id = "migrated-task"
            path = product / "harness-workspace/runs/tasks" / task_id / "result.json"
            result = default_result(task_id, initial_tier="standard", work_item={"id": "WI-42"})
            result["baseline"]["source"] = "migration"
            result["tier"]["effective"] = "strict"
            result["binding_digest"] = "old-binding"
            result["subject"]["digest"] = "old-subject"
            result["policy_digest"] = "old-policy"
            result["cost"]["receipt"] = {
                "provider": "test", "model": "test", "task_id": task_id,
                "subject_digest": result["subject"]["digest"],
                "policy_digest": result["policy_digest"],
            }
            result["checks"]["planning"] = {
                "decision": "pass", "fingerprint": "old", "subject_digest": "old",
                "source": "executed",
            }
            atomic_write_result(path, result)
            captured = []
            with (mock.patch.object(harness_commands, "dump_json", side_effect=captured.append),
                  mock.patch.object(harness_commands, "policy_for", return_value="new-policy")):
                harness_commands.cmd_amend(SimpleNamespace(
                    product_root=str(product), harness_root=str(ROOT), task_id=task_id,
                    scope=["harness-workspace/", ".githooks", ".githooks"],
                    reason="recover committed adoption scope",
                ))
            amended = captured[-1]["result"]
            self.assertEqual(captured[-1]["reason"], "TASK_BINDING_AMENDED")
            self.assertEqual(amended["task"]["scope"], [".githooks", "harness-workspace"])
            self.assertEqual(amended["binding_revisions"][0]["previous_binding_digest"], "old-binding")
            self.assertEqual(amended["binding_revisions"][0]["reason"], "recover committed adoption scope")
            self.assertTrue(amended["checks"]["planning"]["stale"])
            self.assertIn("TASK_BINDING_CHANGED", amended["blockers"])
            self.assertNotEqual(amended["binding_digest"], "old-binding")
            self.assertEqual(amended["tier"]["effective"], "standard")
            self.assertNotIn("receipt", amended["cost"])
            self.assertEqual(amended["policy_digest"], "new-policy")

    def test_amend_task_rejects_unsafe_scope_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            task_id = "task-unsafe"
            path = product / "harness-workspace/runs/tasks" / task_id / "result.json"
            atomic_write_result(path, default_result(task_id))
            before = path.read_text()
            captured = []
            with mock.patch.object(harness_commands, "dump_json", side_effect=captured.append):
                harness_commands.cmd_amend(SimpleNamespace(
                    product_root=str(product), harness_root=str(ROOT), task_id=task_id,
                    scope=["../outside"], reason="bad scope",
                ))
            self.assertEqual(captured[-1]["reason"], "TASK_AMEND_SCOPE_INVALID")
            self.assertEqual(path.read_text(), before)

    def test_amend_task_cli_help_states_that_scope_is_replaced(self) -> None:
        parser = harness_cli.build_parser()
        subparsers = next(action for action in parser._actions if action.dest == "command")
        amend_parser = subparsers.choices["amend-task"]
        scope_action = next(action for action in amend_parser._actions if action.dest == "scope")

        self.assertIn("complete replacement scope", scope_action.help)
        self.assertIn("every path that must remain bound", scope_action.help)

    def test_cli_lite_start_status_finish_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            (product / "docs").mkdir()
            (product / "docs" / "note.md").write_text("base\n")
            (product / ".gitignore").write_text("harness-workspace/runs/\n")
            subprocess.run(["git", "init", "-q"], cwd=product, check=True)
            subprocess.run(["git", "config", "user.email", "harness@example.invalid"], cwd=product, check=True)
            subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=product, check=True)
            subprocess.run(["git", "add", "."], cwd=product, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=product, check=True)
            command = [
                "python3", str(SCRIPTS / "harness_runtime.py"),
                "--harness-root", str(ROOT), "--product-root", str(product),
            ]
            started = json.loads(subprocess.check_output(
                [*command, "start", "lite-task", "--tier", "lite", "--scope", "docs"], text=True
            ))
            self.assertEqual(started["decision"], "pass")
            subprocess.run(["git", "add", "."], cwd=product, check=True)
            subprocess.run(["git", "commit", "-qm", "binding"], cwd=product, check=True)
            (product / "docs" / "note.md").write_text("changed\n")
            status = json.loads(subprocess.check_output([*command, "status"], text=True))
            self.assertEqual(status["result"]["enforcement_notice"], "LOCAL_ONLY")
            premature = subprocess.run(
                [*command, "finish", "--skip-legacy-gates"], text=True,
                capture_output=True, check=False,
                env={**os.environ, "HARNESS_PRODUCT_ROOT": str(product)},
            )
            self.assertEqual(premature.returncode, 1)
            self.assertEqual(json.loads(premature.stdout)["reason"], "STAGE_STILL_ACTIVE")
            complete_story_stages(product, "lite-task")
            finished = json.loads(subprocess.check_output(
                [*command, "finish", "--skip-legacy-gates"], text=True,
                env={**os.environ, "HARNESS_PRODUCT_ROOT": str(product)},
            ))
            self.assertEqual(finished["decision"], "pass")
            self.assertEqual(finished["result"]["assurance"]["task_execution"], "complete")
            self.assertEqual(finished["result"]["checks"]["code_health"]["mode"], "mechanical")
            cached = json.loads(subprocess.check_output(
                [*command, "finish", "--skip-legacy-gates"], text=True,
                env={**os.environ, "HARNESS_PRODUCT_ROOT": str(product)},
            ))
            self.assertEqual(cached["result"]["checks"]["tier"]["source"], "cache")
            self.assertEqual(cached["result"]["checks"]["scope"]["source"], "cache")
            self.assertEqual(cached["result"]["checks"]["code_health"]["source"], "executed")
            self.assertGreaterEqual(cached["result"]["cost"]["harness"]["cache_hits"], 2)
            (product / "docs" / "note.md").write_text("changed again\n")
            rerun = json.loads(subprocess.check_output(
                [*command, "finish", "--skip-legacy-gates"], text=True,
                env={**os.environ, "HARNESS_PRODUCT_ROOT": str(product)},
            ))
            self.assertEqual(rerun["decision"], "pass")
            self.assertGreaterEqual(rerun["result"]["cost"]["harness"]["reruns"], 1)

    def test_status_projects_input_change_without_mutating_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            (product / "docs").mkdir()
            (product / "docs/note.md").write_text("base\n")
            (product / ".gitignore").write_text("harness-workspace/runs/\n")
            subprocess.run(["git", "init", "-q"], cwd=product, check=True)
            subprocess.run(["git", "config", "user.email", "harness@example.invalid"], cwd=product, check=True)
            subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=product, check=True)
            subprocess.run(["git", "add", "."], cwd=product, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=product, check=True)
            command = [
                "python3", str(SCRIPTS / "harness_runtime.py"),
                "--harness-root", str(ROOT), "--product-root", str(product),
            ]
            subprocess.check_output(
                [*command, "start", "read-only-status", "--tier", "lite", "--scope", "docs"],
                text=True,
            )
            (product / "docs/note.md").write_text("validated\n")
            complete_story_stages(product, "read-only-status")
            finished = json.loads(subprocess.check_output(
                [*command, "finish", "--skip-legacy-gates"], text=True,
                env={**os.environ, "HARNESS_PRODUCT_ROOT": str(product)},
            ))
            self.assertEqual(finished["decision"], "pass", finished)
            result_path = product / "harness-workspace/runs/tasks/read-only-status/result.json"
            before = result_path.read_bytes()
            (product / "docs/note.md").write_text("changed after validation\n")

            status = json.loads(subprocess.check_output([*command, "status"], text=True))

            self.assertEqual(status["result"]["state"], "active")
            self.assertEqual(status["result"]["decision"], "block")
            self.assertIn("INPUT_CHANGED", status["result"]["blockers"])
            self.assertEqual(result_path.read_bytes(), before)

    def test_finish_fails_closed_when_a_gate_changes_the_subject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            source = product / "docs" / "note.md"
            source.parent.mkdir(parents=True)
            source.write_text("base\n")
            (product / ".gitignore").write_text("harness-workspace/runs/\n")
            subprocess.run(["git", "init", "-q"], cwd=product, check=True)
            subprocess.run(["git", "config", "user.email", "harness@example.invalid"], cwd=product, check=True)
            subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=product, check=True)
            subprocess.run(["git", "add", "."], cwd=product, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=product, check=True)
            common = {"product_root": str(product), "harness_root": str(ROOT)}
            with mock.patch.object(harness_commands, "dump_json"):
                harness_commands.cmd_start(SimpleNamespace(
                    **common, task_id="gate-mutation", tier="lite", scope=["docs"],
                    work_item="", kind="implementation", reason="",
                ))
            source.write_text("before gate\n")
            complete_story_stages(product, "gate-mutation")
            captured = []

            def mutate_subject(*_args, **kwargs):
                source.write_text("after gate\n")
                checks = {
                    name: harness_commands.executed_check(
                        decision="pass", fingerprint=f"{name}-fingerprint",
                        subject_digest=kwargs["subject_digest"],
                        policy_digest=kwargs["policy_digest"],
                        completed_at=harness_commands.now(),
                    )
                    for name in ("harness", "structure", "quality_lint", "quality_test")
                }
                return {"decision": "pass", "checks": checks, "missing": []}

            with mock.patch.object(harness_commands, "run_gate_plan", side_effect=mutate_subject), \
                    mock.patch.object(harness_commands, "dump_json", side_effect=captured.append):
                harness_commands.cmd_finish(SimpleNamespace(
                    **common, task_id="gate-mutation", skip_legacy_gates=False,
                ))

            outcome = captured[-1]
            result = outcome["result"]
            changed = harness_commands.changed_since_baseline(
                product,
                harness_commands.result_path(product, "gate-mutation").parent / "worktree_baseline.json",
            )
            self.assertEqual(outcome["decision"], "block")
            self.assertEqual(outcome["reason"], "INPUT_CHANGED")
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(result["subject"]["digest"], harness_commands.subject_for(product, changed))
            self.assertEqual(result["invariants"]["scope"], "pending")
            self.assertEqual(result["invariants"]["risk_validation"], "pending")
            self.assertEqual(result["invariants"]["final_result"], "pending")
            self.assertTrue(all(check.get("stale") is True for check in result["checks"].values()))

    def test_ci_checks_are_always_executed(self) -> None:
        completed = subprocess.run([
            "python3", str(SCRIPTS / "harness_runtime.py"),
            "--harness-root", str(ROOT), "--product-root", str(ROOT),
            "ci-check", "--task-id", "cache-ci", "--commit", "HEAD",
            "--tier", "lite", "--scope", ".",
        ], text=True, capture_output=True, check=False)
        result = json.loads(completed.stdout)["result"]
        self.assertEqual(result["checks"]["tier"]["source"], "executed")
        self.assertEqual(result["checks"]["scope"]["source"], "executed")
        self.assertEqual(result["checks"]["code_health"]["source"], "executed")
        self.assertEqual(result["task"], {"task_id": "cache-ci", "scope": ["."], "tier_floor": "lite"})
        self.assertFalse((ROOT / "harness-workspace").exists())

    def test_scope_change_and_hotfix_require_reason_and_raise_tier_floor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=product, check=True)
            command = [
                "python3", str(SCRIPTS / "harness_runtime.py"),
                "--harness-root", str(ROOT), "--product-root", str(product),
                "start", "urgent-fix", "--kind", "hotfix", "--tier", "lite", "--scope", ".",
            ]
            blocked_process = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertEqual(blocked_process.returncode, 1)
            blocked = json.loads(blocked_process.stdout)
            self.assertEqual(blocked["reason"], "TASK_KIND_REASON_REQUIRED")
            started = json.loads(subprocess.check_output(
                [*command, "--reason", "production regression"], text=True,
            ))
            self.assertEqual(started["result"]["tier"]["initial"], "strict")
            self.assertEqual(started["result"]["task"]["change_reason"], "production regression")

    def test_tier_only_escalates_and_unknown_defaults_standard(self) -> None:
        self.assertEqual(classify_tier(["docs/readme.md"], floor="lite"), "lite")
        self.assertEqual(classify_tier(["src/a.py"], floor="lite"), "standard")
        self.assertEqual(classify_tier(["db/migrations/1.sql"], floor="lite"), "strict")
        self.assertEqual(classify_tier([], floor="strict"), "strict")
        self.assertEqual(task_kind_tier("scope-change", "lite"), "standard")
        self.assertEqual(task_kind_tier("hotfix", "lite"), "strict")
        self.assertEqual(task_kind_tier("implementation", "strict"), "strict")
        self.assertEqual(task_kind_tier("harness-maintenance", "lite"), "lite")
        self.assertEqual(classify_tier([".githooks/pre-commit", "harness-workspace/project.yaml"],
                                       floor="lite", kind="harness-maintenance"), "lite")
        self.assertEqual(classify_tier(["src/a.py"], floor="lite", kind="harness-maintenance"), "standard")

    def test_lite_revalidation_discards_stale_standard_only_checks(self) -> None:
        checks = {
            "tier": {"decision": "pass"}, "scope": {"decision": "pass"},
            "code_health": {"decision": "pass"}, "harness": {"decision": "pass"},
            "quality_lint": {"decision": "pass"}, "quality_test": {"decision": "pass"},
            "structure": {"decision": "pass"},
            "qa_evidence": {"decision": "block", "stale": True},
        }
        required = {
            "tier", "scope", "code_health", "harness", "structure",
            "quality_lint", "quality_test",
        }
        retained = harness_commands.checks_for_tier(checks, "lite")
        self.assertNotIn("qa_evidence", retained)
        self.assertEqual(set(retained), required)

    def test_repository_root_scope_covers_all_paths(self) -> None:
        self.assertTrue(paths_within_scope(["src/a.py", "docs/a.md"], ["."]))
        self.assertFalse(paths_within_scope(["src/a.py", "docs/a.md"], ["src"]))

    def test_lite_always_scans_but_does_not_require_agent_without_signal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "docs").mkdir()
            (repo / "docs" / "readme.md").write_text("clean\n")
            check = mechanical_code_health(repo, ["docs/readme.md"], tier="lite")
            self.assertEqual(check["mode"], "mechanical")
            self.assertFalse(check["agent_required"])
            self.assertEqual(check["decision"], "pass")

    def test_standard_dead_code_or_cross_module_requires_gc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            for rel in ("src/a.py", "packages/b.py"):
                path = repo / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("def unused_helper():\n    pass\n")
            check = mechanical_code_health(
                repo, ["src/a.py", "packages/b.py"], tier="standard"
            )
            self.assertTrue(check["agent_required"])
            self.assertIn("cross_module", check["triggers"])
            self.assertIn("dead_code", check["triggers"])

    def test_unused_python_import_is_a_gc_signal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            path = repo / "src" / "module.py"
            path.parent.mkdir(parents=True)
            path.write_text("import os\n\ndef live():\n    return 1\n")
            check = mechanical_code_health(repo, ["src/module.py"], tier="standard")
            self.assertIn("unused_import", check["triggers"])
            self.assertTrue(check["agent_required"])

    def test_strict_without_independent_gc_result_blocks(self) -> None:
        result = default_result("strict-task", initial_tier="strict")
        check = {"decision": "pass", "triggers": [], "agent_required": True}
        apply_code_health(result, check, gc_result=None)
        self.assertEqual(result["checks"]["code_health"]["decision"], "block")
        self.assertEqual(result["blockers"], ["GC_REQUIRED"])

    def test_gc_change_invalidates_old_test_fingerprint(self) -> None:
        old = fingerprint("tests", "subject-a", "policy-a")
        new = fingerprint("tests", "subject-b", "policy-a")
        cached = {"fingerprint": old, "subject_digest": "subject-a"}
        self.assertFalse(cache_matches(cached, new, "subject-b", "policy-a"))

    def test_gc_change_reexecutes_tier_and_scope_for_new_subject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            source = product / "src" / "module.py"
            source.parent.mkdir(parents=True)
            source.write_text("def live():\n    return 1\n")
            (product / ".gitignore").write_text("harness-workspace/runs/\n")
            subprocess.run(["git", "init", "-q"], cwd=product, check=True)
            subprocess.run(["git", "config", "user.email", "harness@example.invalid"], cwd=product, check=True)
            subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=product, check=True)
            subprocess.run(["git", "add", "."], cwd=product, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=product, check=True)
            common = {"product_root": str(product), "harness_root": str(ROOT)}
            with mock.patch.object(harness_commands, "dump_json"):
                harness_commands.cmd_start(SimpleNamespace(
                    **common, task_id="gc-cache", tier="standard", scope=["src"],
                    work_item="", kind="implementation",
                ))
            source.write_text("def unused_helper():\n    pass\n")
            complete_story_stages(product, "gc-cache")
            captured = []

            def run_gc(*_args, **_kwargs):
                source.write_text("def live():\n    return 2\n")
                changed = harness_commands.changed_since_baseline(
                    product, harness_commands.result_path(product, "gc-cache").parent / "worktree_baseline.json"
                )
                return {
                    "decision": "pass",
                    "role": "gc-sweeper", "independent": True, "task_id": "gc-cache",
                    "subject_digest": harness_commands.subject_for(product, changed),
                    "policy_digest": harness_commands.policy_for(ROOT, product),
                    "findings": 1, "remediated": 1, "deferred_work_items": [],
                }

            with mock.patch.object(harness_commands, "invoke_gc_once", side_effect=run_gc), \
                    mock.patch.object(harness_commands, "dump_json", side_effect=captured.append):
                harness_commands.cmd_finish(SimpleNamespace(
                    **common, task_id="gc-cache", skip_legacy_gates=True,
                ))
            result = captured[-1]["result"]
            final_subject = result["subject"]["digest"]
            self.assertEqual(result["checks"]["tier"]["source"], "executed")
            self.assertEqual(result["checks"]["scope"]["source"], "executed")
            self.assertEqual(result["checks"]["tier"]["subject_digest"], final_subject)
            self.assertEqual(result["checks"]["scope"]["subject_digest"], final_subject)
            self.assertEqual(result["checks"]["code_health"]["subject_digest"], final_subject)

    def test_blocking_gc_finding_cannot_finish(self) -> None:
        result = default_result("blocked-task")
        result["invariants"] = {key: "pass" for key in result["invariants"]}
        result["checks"]["code_health"] = {"decision": "block"}
        self.assertEqual(finish_decision(result), "block")

    def test_out_of_scope_debt_is_deferred_not_remediated(self) -> None:
        result = default_result("debt-task")
        check = {"decision": "pass", "triggers": ["large_file"], "agent_required": True}
        gc = {
            "decision": "pass",
            "role": "gc-sweeper", "independent": True, "task_id": "debt-task",
            "subject_digest": "subject-a",
            "policy_digest": "",
            "findings": 1,
            "remediated": 0,
            "deferred_work_items": ["WI-42"],
        }
        apply_code_health(result, check, gc_result=gc, subject_digest="subject-a")
        code_health = result["checks"]["code_health"]
        self.assertEqual(code_health["remediated"], 0)
        self.assertEqual(code_health["deferred_work_items"], ["WI-42"])

    def test_deferred_debt_without_work_item_blocks(self) -> None:
        result = default_result("debt-task")
        check = {"decision": "pass", "triggers": ["large_file"], "agent_required": True}
        gc = {
            "decision": "pass", "role": "gc-sweeper", "independent": True,
            "task_id": "debt-task", "subject_digest": "subject-a", "policy_digest": "",
            "findings": 1,
            "remediated": 0, "deferred_findings": 1, "deferred_work_items": [],
        }
        apply_code_health(result, check, gc_result=gc, subject_digest="subject-a")
        self.assertIn("DEFERRED_WORK_ITEM_REQUIRED", result["blockers"])

    def test_gc_result_requires_independent_role_task_and_policy_binding(self) -> None:
        result = default_result("strict-task", initial_tier="strict")
        check = {"decision": "pass", "triggers": [], "agent_required": True}
        forged = {
            "decision": "pass", "task_id": "strict-task",
            "subject_digest": "subject-a", "policy_digest": "policy-a",
        }
        apply_code_health(
            result, check, gc_result=forged,
            subject_digest="subject-a", policy_digest="policy-a",
        )
        self.assertEqual(result["checks"]["code_health"]["decision"], "block")
        self.assertIn("GC_REQUIRED", result["blockers"])

    def test_valid_independent_gc_adjudicates_mechanical_block(self) -> None:
        result = default_result("strict-task", initial_tier="strict")
        check = {
            "decision": "block",
            "triggers": ["debug_output"],
            "findings": 1,
            "agent_required": True,
        }
        gc = {
            "decision": "pass",
            "role": "gc-sweeper",
            "independent": True,
            "task_id": "strict-task",
            "subject_digest": "subject-a",
            "policy_digest": "policy-a",
            "findings": 1,
            "remediated": 0,
            "deferred_work_items": [],
            "mechanical_adjudication": [{
                "trigger": "debug_output",
                "decision": "retain",
                "reason": "structured command output",
            }],
        }

        apply_code_health(
            result, check, gc_result=gc,
            subject_digest="subject-a", policy_digest="policy-a",
        )

        code_health = result["checks"]["code_health"]
        self.assertEqual(code_health["decision"], "pass")
        self.assertEqual(code_health["mechanical_decision"], "block")
        self.assertEqual(result["blockers"], [])

    def test_gc_result_requires_complete_unique_mechanical_adjudication(self) -> None:
        mechanical = {
            "decision": "block",
            "triggers": ["debug_output", "large_file"],
            "findings": 2,
            "agent_required": True,
        }
        base_gc = {
            "decision": "pass", "role": "gc-sweeper", "independent": True,
            "task_id": "strict-task", "subject_digest": "subject-a",
            "policy_digest": "policy-a", "findings": 2, "remediated": 0,
            "deferred_work_items": [],
        }
        invalid_adjudications = (
            [],
            [{"trigger": "debug_output", "decision": "retain", "reason": "required output"}],
            [
                {"trigger": "debug_output", "decision": "retain", "reason": "required output"},
                {"trigger": "debug_output", "decision": "retain", "reason": "duplicate"},
            ],
            [
                {"trigger": "debug_output", "decision": "retain", "reason": "required output"},
                {"trigger": "unknown", "decision": "retain", "reason": "not scanned"},
            ],
            [
                {"trigger": "debug_output", "decision": "pass", "reason": "unknown verdict"},
                {"trigger": "large_file", "decision": "retain", "reason": "cohesive"},
            ],
        )
        for adjudications in invalid_adjudications:
            with self.subTest(adjudications=adjudications):
                result = default_result("strict-task", initial_tier="strict")
                apply_code_health(
                    result, mechanical,
                    gc_result={**base_gc, "mechanical_adjudication": adjudications},
                    subject_digest="subject-a", policy_digest="policy-a",
                )
                self.assertEqual(result["checks"]["code_health"]["decision"], "block")
                self.assertIn("GC_REQUIRED", result["blockers"])

    def test_cache_cannot_cross_subject_or_policy_digest(self) -> None:
        fp = fingerprint("gate", "subject-a", "policy-a")
        cached = {
            "fingerprint": fp,
            "subject_digest": "subject-a",
            "policy_digest": "policy-a",
        }
        self.assertTrue(cache_matches(cached, fp, "subject-a", "policy-a"))
        self.assertFalse(cache_matches(cached, fp, "subject-b", "policy-a"))
        self.assertFalse(cache_matches(cached, fp, "subject-a", "policy-b"))

    def test_commit_changes_use_first_parent_for_real_merge_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "harness@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=repo, check=True)
            (repo / "base.md").write_text("base\n")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
            subprocess.run(["git", "checkout", "-qb", "feature"], cwd=repo, check=True)
            (repo / "feature.py").write_text("def unused_feature():\n    pass\n")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "feature"], cwd=repo, check=True)
            subprocess.run(["git", "checkout", "-q", "master"], cwd=repo, check=True)
            (repo / "main-only.md").write_text("main\n")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "main"], cwd=repo, check=True)
            subprocess.run(["git", "merge", "--no-ff", "-m", "merge feature", "feature"], cwd=repo, check=True)
            merge_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
            self.assertEqual(git_changed(repo, merge_sha), ["feature.py"])
            check = mechanical_code_health(repo, git_changed(repo, merge_sha), tier="standard")
            self.assertTrue(check["agent_required"])
            self.assertIn("dead_code", check["triggers"])

    def test_gc_telemetry_records_calls_context_and_duration(self) -> None:
        result = default_result("cost-task")
        telemetry_add_gc(result, context_chars=1234, duration_ms=55)
        harness = result["cost"]["harness"]
        self.assertEqual(harness["agent_calls"], 1)
        self.assertEqual(harness["context_chars"], 1234)
        self.assertEqual(harness["gate_duration_ms"], 55)

    def test_ci_check_consumes_subject_bound_gc_receipt_and_telemetry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            source = product / "module.py"
            source.write_text("def live():\n    return 1\n")
            subprocess.run(["git", "init", "-q"], cwd=product, check=True)
            subprocess.run(["git", "config", "user.email", "harness@example.invalid"], cwd=product, check=True)
            subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=product, check=True)
            subprocess.run(["git", "add", "."], cwd=product, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=product, check=True)
            source.write_text("def unused_helper():\n    pass\n")
            subprocess.run(["git", "add", "."], cwd=product, check=True)
            subprocess.run(["git", "commit", "-qm", "change"], cwd=product, check=True)
            sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=product, text=True).strip()
            receipt = product / "gc-result.json"
            receipt.write_text(json.dumps({
                "decision": "pass", "role": "gc-sweeper", "independent": True,
                "task_id": "gc-ci", "subject_digest": sha,
                "policy_digest": harness_commands.policy_for(ROOT, product),
                "findings": 0, "remediated": 0, "deferred_work_items": [],
                "telemetry": {"agent_calls": 1, "context_chars": 321, "duration_ms": 54,
                              "provider": "compatible", "model": "gc-model"},
            }))
            captured = []
            with mock.patch.object(harness_ci, "run_gate_plan", return_value={
                "decision": "pass", "checks": {}, "missing": [],
            }), mock.patch.object(harness_ci, "dump_json", side_effect=captured.append):
                harness_commands.cmd_ci_check(SimpleNamespace(
                    product_root=str(product), harness_root=str(ROOT), task_id="gc-ci",
                    commit=sha, tier="standard", scope=["."], gc_result=str(receipt), output="",
                ))
            result = captured[-1]["result"]
            self.assertEqual(result["decision"], "pass")
            self.assertEqual(result["cost"]["harness"]["agent_calls"], 1)
            self.assertEqual(result["cost"]["harness"]["context_chars"], 321)
            self.assertGreaterEqual(result["cost"]["harness"]["gate_duration_ms"], 54)

    def test_validate_harness_has_no_product_workspace_side_effect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            env = {"HARNESS_PRODUCT_ROOT": str(product)}
            subprocess.run(
                ["bash", str(SCRIPTS / "validate_harness.sh")],
                cwd=ROOT,
                env={**__import__("os").environ, **env},
                check=True,
                stdout=subprocess.DEVNULL,
            )
            self.assertFalse((product / "harness-workspace").exists())


if __name__ == "__main__":
    unittest.main()
