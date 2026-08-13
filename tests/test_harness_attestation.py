#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from harness_attestation import commit_subject, create_attestation, verify_attestation  # noqa: E402
from harness_runtime import canonical_digest, default_result  # noqa: E402


class HarnessAttestationTest(unittest.TestCase):
    def repo(self, root: Path) -> str:
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "harness@example.invalid"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=root, check=True)
        (root / "tracked.txt").write_text("one\n", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", "one"], cwd=root, check=True)
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()

    def result(self, repo: Path, commit: str) -> dict:
        result = default_result("task-1", initial_tier="lite")
        result.update({"state": "validated", "decision": "pass", "policy_digest": "policy-1"})
        result["task"] = {"scope": ["tracked.txt"]}
        result["subject"] = {
            "kind": "worktree", "digest": commit_subject(repo, commit, ["tracked.txt"]),
            "paths": ["tracked.txt"],
        }
        result["binding_digest"] = canonical_digest({"task_id": "task-1", "commit": commit})
        result["invariants"] = {name: "pass" for name in result["invariants"]}
        return result

    def test_attestation_binds_commit_tree_task_policy_and_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            commit = self.repo(repo)
            result = self.result(repo, commit)
            created = create_attestation(repo, result, commit=commit)
            self.assertEqual(created["decision"], "pass")
            verified = verify_attestation(repo, commit=commit, policy_digest="policy-1")
            self.assertEqual(verified["decision"], "pass")
            payload = verified["attestation"]
            self.assertEqual(payload["commit_sha"], commit)
            self.assertEqual(payload["task_id"], "task-1")
            self.assertEqual(payload["result_digest"], canonical_digest(result))

    def test_missing_copied_and_policy_changed_attestations_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            first = self.repo(repo)
            self.assertEqual(verify_attestation(repo, commit=first)["reason"], "ATTESTATION_MISSING")
            create_attestation(repo, self.result(repo, first), commit=first)
            (repo / "tracked.txt").write_text("two\n", encoding="utf-8")
            subprocess.run(["git", "commit", "-qam", "two"], cwd=repo, check=True)
            second = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
            blob = subprocess.check_output(
                ["git", "rev-parse", f"refs/harness/attestations/{first}"], cwd=repo, text=True
            ).strip()
            subprocess.run(["git", "update-ref", f"refs/harness/attestations/{second}", blob], cwd=repo, check=True)
            self.assertEqual(verify_attestation(repo, commit=second)["reason"], "ATTESTATION_COMMIT_MISMATCH")
            self.assertEqual(
                verify_attestation(repo, commit=first, policy_digest="policy-2")["reason"],
                "ATTESTATION_POLICY_MISMATCH",
            )

    def test_verifier_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            commit = self.repo(repo)
            create_attestation(repo, self.result(repo, commit), commit=commit)
            before = subprocess.check_output(["git", "show-ref"], cwd=repo, text=True)
            verify_attestation(repo, commit=commit)
            after = subprocess.check_output(["git", "show-ref"], cwd=repo, text=True)
            self.assertEqual(after, before)

    def test_result_cannot_attest_a_commit_with_different_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            first = self.repo(repo)
            result = self.result(repo, first)
            (repo / "tracked.txt").write_text("different\n", encoding="utf-8")
            subprocess.run(["git", "commit", "-qam", "different"], cwd=repo, check=True)
            outcome = create_attestation(repo, result, commit="HEAD")
            self.assertEqual(outcome["reason"], "HARNESS_RESULT_SUBJECT_MISMATCH")


if __name__ == "__main__":
    unittest.main()
