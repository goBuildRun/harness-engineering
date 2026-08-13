#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from harness_attestation import create_attestation  # noqa: E402
from harness_runtime import default_result  # noqa: E402
from provider_lifecycle import build_receipt, validate_receipt  # noqa: E402


class ProviderLifecycleTest(unittest.TestCase):
    def keys(self, root: Path) -> tuple[Path, Path]:
        key = root / "authority"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
        allowed = root / "allowed_signers"
        allowed.write_text(f"harness {key.with_suffix('.pub').read_text()}", encoding="utf-8")
        return key, allowed

    def test_receipt_binds_git_acceptance_without_platform_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "harness@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=repo, check=True)
            (repo / "a").write_text("a")
            subprocess.run(["git", "add", "a"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "a"], cwd=repo, check=True)
            commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
            result = default_result("task-1")
            result.update({"decision": "pass", "state": "validated", "policy_digest": "policy"})
            result["subject"] = {"kind": "commit", "digest": commit}
            result["invariants"] = {name: "pass" for name in result["invariants"]}
            create_attestation(repo, result, commit=commit)
            key, allowed = self.keys(repo)
            receipt = build_receipt(
                repo, commit=commit, work_item_id="WI-42", provider="jira",
                authority="git-receive", accepted_ref="refs/heads/main", signing_key=key,
            )
            self.assertNotIn("run_id", receipt)
            self.assertEqual(receipt["commit_sha"], commit)
            self.assertEqual(validate_receipt(
                receipt, work_item_id="WI-42", repo=repo, allowed_signers=allowed)[0], True)
            receipt["accepted_ref"] = "refs/heads/other"
            self.assertEqual(validate_receipt(
                receipt, work_item_id="WI-42", repo=repo, allowed_signers=allowed)[1],
                "ACCEPTANCE_SIGNATURE_INVALID",
            )

    def test_terminal_receipt_rejects_expired_signature(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "harness@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=repo, check=True)
            (repo / "a").write_text("a")
            subprocess.run(["git", "add", "a"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "a"], cwd=repo, check=True)
            commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
            result = default_result("task-1")
            result.update({"decision": "pass", "state": "validated", "policy_digest": "policy"})
            result["subject"] = {"kind": "commit", "digest": commit}
            result["invariants"] = {name: "pass" for name in result["invariants"]}
            create_attestation(repo, result, commit=commit)
            key, allowed = self.keys(repo)
            receipt = build_receipt(
                repo, commit=commit, work_item_id="WI-42", provider="jira",
                authority="git-receive", accepted_ref="refs/heads/main", signing_key=key,
            )
            receipt["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            self.assertEqual(validate_receipt(
                receipt, work_item_id="WI-42", repo=repo, allowed_signers=allowed
            )[1], "ACCEPTANCE_RECEIPT_EXPIRED")

    def test_terminal_receipt_rejects_wrong_item_or_local_authority(self) -> None:
        receipt = {
            "schema": "harness-acceptance-receipt-v1", "authority": "local",
            "accepted_ref": "refs/heads/main", "commit_sha": "a", "attestation_object": "b",
            "task_id": "t", "policy_digest": "p", "result_digest": "r",
            "work_item_id": "WI-42", "provider": "jira", "accepted_at": "now",
            "expires_at": "2099-01-01T00:00:00Z",
        }
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            allowed = repo / "missing"
            self.assertEqual(validate_receipt(
                receipt, work_item_id="WI-42", repo=repo, allowed_signers=allowed)[1],
                "ACCEPTANCE_AUTHORITY_INVALID",
            )
            receipt["authority"] = "git-receive"
            receipt["signature"] = "fake"
            self.assertEqual(validate_receipt(
                receipt, work_item_id="WI-43", repo=repo, allowed_signers=allowed)[1],
                "ACCEPTANCE_WORK_ITEM_MISMATCH",
            )


if __name__ == "__main__":
    unittest.main()
