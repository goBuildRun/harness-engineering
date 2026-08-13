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
    resolve_task_id,
    telemetry_add_gc,
)
from harness_scope import paths_within_scope  # noqa: E402
from harness_state import invalidate_if_stale  # noqa: E402
import harness_commands  # noqa: E402
import harness_migration_commands  # noqa: E402


class HarnessRuntimeTest(unittest.TestCase):
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

    def test_migration_preserves_committed_work_item_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            task_id = "legacy-task"
            task_dir = product / "harness-workspace" / "planning" / "tasks" / task_id
            task_dir.mkdir(parents=True)
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
            self.assertEqual(status["result"]["enforcement_notice"], "NOT_ENFORCED")
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

    def test_ci_checks_are_always_executed(self) -> None:
        output = subprocess.check_output([
            "python3", str(SCRIPTS / "harness_runtime.py"),
            "--harness-root", str(ROOT), "--product-root", str(ROOT),
            "ci-check", "--task-id", "cache-ci", "--commit", "HEAD",
            "--tier", "lite", "--scope", ".",
        ], text=True)
        result = json.loads(output)["result"]
        self.assertEqual(result["checks"]["tier"]["source"], "executed")
        self.assertEqual(result["checks"]["scope"]["source"], "executed")
        self.assertEqual(result["checks"]["code_health"]["source"], "executed")
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
            blocked = json.loads(subprocess.check_output(command, text=True))
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
                "telemetry": {"agent_calls": 1, "context_chars": 321, "duration_ms": 54},
            }))
            captured = []
            with mock.patch.object(harness_commands, "run_gate_plan", return_value={
                "decision": "pass", "checks": {}, "missing": [],
            }), mock.patch.object(harness_commands, "dump_json", side_effect=captured.append):
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
