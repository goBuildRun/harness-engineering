#!/usr/bin/env python3
from __future__ import annotations

import json
import io
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from harness_assurance import (audit_guards, check_bootstrap, check_release_candidate,
                               create_bootstrap, create_release_candidate, install_guards,
                               pre_push_script)  # noqa: E402
from harness_runtime import default_result, load_result  # noqa: E402
from harness_commands import refresh_assurance  # noqa: E402
from harness_schema import validate_result  # noqa: E402
from harness_cli import main as harness_main  # noqa: E402


class HarnessAssuranceTest(unittest.TestCase):
    def release_candidate_result(self, paths: list[str]) -> dict:
        result = default_result("task-release", initial_tier="standard")
        result.update(state="blocked", decision="block", policy_digest="policy-1")
        result["binding_digest"] = "binding-1"
        result["subject"] = {"kind": "worktree", "digest": "subject-1", "paths": paths}
        result["invariants"] = {
            "task_identity": "pass", "scope": "pass", "risk_validation": "block",
            "final_result": "pass",
        }
        result["checks"] = {
            "quality_test": {"decision": "pass"},
            "qa_evidence": {
                "decision": "block",
                "reason": "QA_EVIDENCE_INVALID: QA_SIGNOFF_MISSING:T5",
            },
        }
        return result

    def test_release_candidate_is_index_bound_one_shot_and_not_a_final_attestation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=product, check=True)
            subprocess.run(["git", "config", "user.email", "harness@example.invalid"], cwd=product, check=True)
            subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=product, check=True)
            (product / "tracked.txt").write_text("base\n")
            subprocess.run(["git", "add", "tracked.txt"], cwd=product, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=product, check=True)
            install_guards(product)
            subprocess.run(["git", "add", ".githooks"], cwd=product, check=True)
            subprocess.run(["git", "commit", "-qm", "guards", "--no-verify"], cwd=product, check=True)
            candidate_path = "中文任务.md"
            (product / candidate_path).write_text("candidate\n")
            subprocess.run(["git", "add", candidate_path], cwd=product, check=True)
            created = create_release_candidate(product, self.release_candidate_result([candidate_path]))
            self.assertEqual(created["decision"], "pass", created)
            self.assertEqual(check_release_candidate(product)["decision"], "pass")
            subprocess.run(["git", "commit", "-qm", "candidate"], cwd=product, check=True)
            sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=product, text=True).strip()
            self.assertEqual(check_release_candidate(product)["reason"], "RELEASE_CANDIDATE_MISSING")
            self.assertEqual(
                subprocess.check_output(
                    ["git", "show", f"refs/harness/release-candidates/{sha}"], cwd=product, text=True,
                ) and True,
                True,
            )
            self.assertNotEqual(subprocess.run(
                ["git", "show-ref", "--verify", f"refs/harness/attestations/{sha}"], cwd=product,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            ).returncode, 0)

    def test_release_candidate_rejects_any_pending_gate_beyond_production_t5(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=product, check=True)
            install_guards(product)
            subprocess.run(["git", "add", ".githooks"], cwd=product, check=True)
            result = self.release_candidate_result([".githooks/post-commit", ".githooks/pre-commit", ".githooks/pre-push"])
            result["checks"]["qa_evidence"]["reason"] += "; QA_SIGNOFF_MISSING:T4"
            self.assertEqual(create_release_candidate(product, result)["reason"], "RELEASE_CANDIDATE_NOT_ELIGIBLE")

    def test_strict_release_candidate_allows_only_post_candidate_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=product, check=True)
            subprocess.run(["git", "config", "user.email", "harness@example.invalid"], cwd=product, check=True)
            subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=product, check=True)
            subprocess.run(["git", "commit", "--allow-empty", "-qm", "base"], cwd=product, check=True)
            install_guards(product)
            subprocess.run(["git", "add", ".githooks"], cwd=product, check=True)
            paths = [".githooks/post-commit", ".githooks/pre-commit", ".githooks/pre-push"]
            result = self.release_candidate_result(paths)
            result["checks"]["qa_evidence"]["reason"] += "; QA_SIGNOFF_MISSING:T-GC"
            result["checks"]["strict_evidence"] = {
                "decision": "block",
                "reason": "STRICT_EVIDENCE_REQUIRED",
            }

            created = create_release_candidate(product, result)

            self.assertEqual(created["decision"], "pass", created)
            self.assertEqual(created["receipt"]["pending_gates"], ["T5", "T-GC", "strict_evidence"])
            self.assertEqual(check_release_candidate(product)["decision"], "pass")

    def test_release_candidate_rejects_other_qa_or_strict_evidence_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=product, check=True)
            install_guards(product)
            subprocess.run(["git", "add", ".githooks"], cwd=product, check=True)
            paths = [".githooks/post-commit", ".githooks/pre-commit", ".githooks/pre-push"]

            invalid_qa = self.release_candidate_result(paths)
            invalid_qa["checks"]["qa_evidence"]["reason"] += "; QA_REPORT_INVALID:T4"
            self.assertEqual(
                create_release_candidate(product, invalid_qa)["reason"],
                "RELEASE_CANDIDATE_NOT_ELIGIBLE",
            )

            invalid_strict = self.release_candidate_result(paths)
            invalid_strict["checks"]["strict_evidence"] = {
                "decision": "block",
                "reason": "STRICT_EVIDENCE_BINDING_MISMATCH",
            }
            self.assertEqual(
                create_release_candidate(product, invalid_strict)["reason"],
                "RELEASE_CANDIDATE_NOT_ELIGIBLE",
            )

    def test_release_candidate_resolves_agent_start_work_item_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            task_id = "WI-agent-start"
            runs = product / "harness-workspace" / "runs"
            task_root = runs / "tasks" / task_id
            task_root.mkdir(parents=True)
            (runs / "active_task.json").write_text(
                json.dumps({"work_item_id": task_id}),
                encoding="utf-8",
            )
            (task_root / "result.json").write_text(
                json.dumps(default_result(task_id)),
                encoding="utf-8",
            )

            output = io.StringIO()
            with patch.object(
                sys,
                "argv",
                ["harness", "--product-root", str(product), "release-candidate"],
            ), redirect_stdout(output):
                self.assertEqual(harness_main(), 0)

            outcome = json.loads(output.getvalue())
            self.assertEqual(outcome["reason"], "RELEASE_CANDIDATE_GUARDED_REQUIRED")

    def test_local_lite_onboarding_public_path_completes_under_five_minutes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=product, check=True)
            subprocess.run(["git", "config", "user.email", "harness@example.invalid"], cwd=product, check=True)
            subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=product, check=True)
            (product / "docs").mkdir()
            (product / "docs/note.md").write_text("base\n")
            subprocess.run(["git", "add", "."], cwd=product, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=product, check=True)
            command = [str(SCRIPTS / "harness"), "--product-root", str(product)]
            started = time.monotonic()
            start = json.loads(subprocess.check_output(
                [*command, "start", "onboarding", "--tier", "lite", "--scope", "docs"], text=True,
            ))
            (product / "docs/note.md").write_text("implemented\n")
            finish = json.loads(subprocess.check_output(
                [*command, "finish", "--skip-legacy-gates"], text=True,
            ))
            status = json.loads(subprocess.check_output([*command, "status"], text=True))
            self.assertEqual(start["decision"], "pass")
            self.assertEqual(finish["decision"], "pass", finish)
            self.assertEqual(status["decision"], "pass")
            self.assertLess(time.monotonic() - started, 300)
            self.assertFalse((product / ".github").exists())
    def test_default_result_is_local_and_legacy_result_remains_readable(self) -> None:
        current = default_result("current")
        self.assertEqual(current["assurance"]["level"], "local")
        self.assertTrue(current["assurance"]["bypassable"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.json"
            legacy = default_result("legacy")
            legacy.pop("assurance")
            path.write_text(json.dumps(legacy))
            loaded = load_result(path)
        self.assertEqual(loaded["assurance"]["level"], "local")

    def test_finish_assurance_refresh_reports_guarded_pre_commit_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=product, check=True)
            install_guards(product)
            subprocess.run(["git", "add", ".githooks"], cwd=product, check=True)
            result = default_result("task")
            result.update(state="validated", decision="pass")
            refresh_assurance(result, product, "policy", phase="pre-commit-head")
            self.assertEqual(result["assurance"]["level"], "guarded")
            self.assertEqual(result["assurance"]["acceptance_authority"], "git-hooks")
            self.assertEqual(result["assurance"]["head_attestation"]["phase"], "pre-commit-head")
            self.assertTrue(result["assurance"]["bypassable"])

    def test_guard_install_is_repo_local_versioned_and_auditable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=product, check=True)
            installed = install_guards(product)
            self.assertEqual(installed["decision"], "pass")
            self.assertEqual(
                subprocess.check_output(
                    ["git", "config", "--local", "--get", "core.hooksPath"],
                    cwd=product, text=True,
                ).strip(),
                ".githooks",
            )
            configured_root = subprocess.check_output(
                ["git", "config", "--local", "--get", "harness.engineeringRoot"],
                cwd=product, text=True,
            ).strip()
            self.assertEqual(Path(configured_root), ROOT)
            for name in ("pre-commit", "post-commit", "pre-push"):
                hook = product / ".githooks" / name
                self.assertTrue(hook.is_file())
                self.assertTrue(hook.stat().st_mode & 0o111)
                self.assertIn("HARNESS_GUARD_RUNTIME_MISSING", hook.read_text())
            subprocess.run(["git", "add", ".githooks"], cwd=product, check=True)
            self.assertEqual(audit_guards(product)["level"], "guarded")

    def test_guarded_bootstrap_is_bound_to_index_and_consumed_by_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=product, check=True)
            subprocess.run(["git", "config", "user.email", "harness@example.invalid"], cwd=product, check=True)
            subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=product, check=True)
            (product / "base.txt").write_text("base\n")
            subprocess.run(["git", "add", "base.txt"], cwd=product, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=product, check=True)
            install_guards(product)
            subprocess.run(["git", "add", ".githooks"], cwd=product, check=True)
            created = create_bootstrap(product, "adoption-task", "install guarded hooks")
            self.assertEqual(created["decision"], "pass")
            self.assertEqual(check_bootstrap(product)["decision"], "pass")
            (product / "extra.txt").write_text("extra\n")
            subprocess.run(["git", "add", "extra.txt"], cwd=product, check=True)
            self.assertEqual(check_bootstrap(product)["reason"], "GUARDED_BOOTSTRAP_BINDING_MISMATCH")
            renewed = create_bootstrap(product, "adoption-task", "include reviewed extra path")
            self.assertEqual(renewed["decision"], "pass")
            subprocess.run(["git", "commit", "-qm", "guarded bootstrap"], cwd=product, check=True)
            self.assertEqual(check_bootstrap(product)["reason"], "GUARDED_BOOTSTRAP_MISSING")

    def test_guard_audit_rejects_untracked_or_modified_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=product, check=True)
            install_guards(product)
            untracked = audit_guards(product)
            self.assertEqual(untracked["level"], "local")
            self.assertIn("GUARDED_GIT_GUARDS_UNTRACKED", untracked["blockers"])
            subprocess.run(["git", "add", ".githooks"], cwd=product, check=True)
            (product / ".githooks/pre-push").write_text("#!/bin/sh\nexit 0\n")
            modified = audit_guards(product)
            self.assertEqual(modified["level"], "local")
            self.assertIn("GUARDED_GIT_GUARDS_MODIFIED", modified["blockers"])

    def test_guard_blocks_without_validated_task_and_finds_configured_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=product, check=True)
            install_guards(product)
            blocked = subprocess.run(
                [str(product / ".githooks" / "pre-commit")], cwd=product,
                text=True, capture_output=True,
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertNotIn("HARNESS_GUARD_RUNTIME_MISSING", blocked.stderr)

    def test_guarded_finish_commit_push_lifecycle_does_not_stale_deadlock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            remote = product.parent / f"{product.name}-remote.git"
            (product / "docs").mkdir()
            (product / "docs/note.md").write_text("base\n")
            (product / ".gitignore").write_text("harness-workspace/runs/\n")
            subprocess.run(["git", "init", "-q"], cwd=product, check=True)
            subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
            subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=product, check=True)
            subprocess.run(["git", "config", "user.email", "harness@example.invalid"], cwd=product, check=True)
            subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=product, check=True)
            subprocess.run(["git", "add", "."], cwd=product, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=product, check=True)
            install_guards(product)
            subprocess.run(["git", "add", ".githooks"], cwd=product, check=True)
            subprocess.run(["git", "commit", "-qm", "guarded onboarding", "--no-verify"], cwd=product, check=True)
            command = [str(SCRIPTS / "harness"), "--product-root", str(product)]
            subprocess.check_output([*command, "start", "guarded-task", "--tier", "lite", "--scope", "docs"])
            binding_result = json.loads(subprocess.check_output(
                [*command, "finish", "--skip-legacy-gates"], text=True,
                env={**dict(os.environ), "HARNESS_PRODUCT_ROOT": str(product)},
            ))
            self.assertEqual(binding_result["decision"], "pass")
            subprocess.run(["git", "add", "."], cwd=product, check=True)
            subprocess.run(["git", "commit", "-qm", "binding"], cwd=product, check=True)
            (product / "docs/note.md").write_text("changed\n")
            finished = json.loads(subprocess.check_output(
                [*command, "finish", "--skip-legacy-gates"], text=True,
                env={**dict(os.environ), "HARNESS_PRODUCT_ROOT": str(product)},
            ))
            self.assertEqual(finished["decision"], "pass")
            subprocess.run([str(product / ".githooks/pre-commit")], cwd=product, check=True)
            subprocess.run(["git", "add", "docs/note.md"], cwd=product, check=True)
            subprocess.run(["git", "commit", "-qm", "change"], cwd=product, check=True)
            head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=product, text=True).strip()
            pushed = subprocess.run(
                [str(product / ".githooks/pre-push"), "origin", str(remote)], cwd=product,
                text=True, input=f"refs/heads/main {head} refs/heads/main {'0' * 40}\n",
                capture_output=True,
            )
            self.assertEqual(pushed.returncode, 0, pushed.stderr)
            self.assertIn("HARNESS_PUSH_GUARD_PASS", pushed.stderr)
            status = json.loads(subprocess.check_output([*command, "status"], text=True))
            self.assertEqual(status["result"]["state"], "validated")

    def test_pre_push_checks_every_new_commit_and_pushes_attestation_refs(self) -> None:
        script = pre_push_script()
        self.assertIn("rev-list", script)
        self.assertIn("refs/harness/attestations", script)
        self.assertIn("git push", script)
        self.assertIn("--atomic", script)
        self.assertIn("refs/harness/results", script)

    def test_pre_push_ignores_deleted_refs_and_processes_each_input_ref(self) -> None:
        script = pre_push_script()
        self.assertIn('[[ "$local_sha" =~ ^0+$ ]] && continue', script)
        self.assertIn("while read -r local_ref local_sha remote_ref remote_sha", script)

    def test_schema_rejects_both_directions_of_enforcement_mismatch(self) -> None:
        result = default_result("mismatch")
        result["enforcement"] = "enforced"
        self.assertIn("enforcement.assurance_mismatch", validate_result(result))
        result["enforcement"] = "shadow"
        result["assurance"] = {**result["assurance"], "level": "enforced"}
        self.assertIn("assurance.enforcement_mismatch", validate_result(result))


if __name__ == "__main__":
    unittest.main()
