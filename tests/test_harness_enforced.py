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

from harness_attestation import create_attestation  # noqa: E402
from harness_enforced import audit, install  # noqa: E402
from harness_runtime import default_result, policy_for  # noqa: E402
from provider_lifecycle import validate_receipt  # noqa: E402


class HarnessEnforcedTest(unittest.TestCase):
    def setup_repositories(self, root: Path) -> tuple[Path, Path, str]:
        source = root / "source"
        remote = root / "remote.git"
        source.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=source, check=True)
        subprocess.run(["git", "config", "user.email", "harness@example.invalid"], cwd=source, check=True)
        subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=source, check=True)
        (source / "docs").mkdir()
        (source / "docs/a.md").write_text("base\n")
        subprocess.run(["git", "add", "."], cwd=source, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=source, check=True)
        subprocess.run(["git", "branch", "-M", "main"], cwd=source, check=True)
        subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
        subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=source, check=True)
        subprocess.run(["git", "push", "-q", "origin", "main"], cwd=source, check=True)
        base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
        return source, remote, base

    def keys(self, root: Path) -> tuple[Path, Path]:
        key = root / "authority"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
        allowed = root / "allowed_signers"
        allowed.write_text(f"harness {key.with_suffix('.pub').read_text()}", encoding="utf-8")
        return key, allowed

    def attest(self, repo: Path, commit: str) -> None:
        result = default_result(
            "enforced-task", initial_tier="lite",
            work_item={"id": "WI-42", "provider": "jira"},
        )
        result.update({"state": "validated", "decision": "pass", "policy_digest": policy_for(ROOT, repo)})
        result["subject"] = {"kind": "commit", "digest": commit}
        result["task"] = {"scope": ["docs"], "tier_floor": "lite"}
        result["invariants"] = {name: "pass" for name in result["invariants"]}
        create_attestation(repo, result, commit=commit)

    def test_controlled_bare_remote_blocks_and_accepts_then_signs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, remote, base = self.setup_repositories(root)
            key, allowed = self.keys(root)
            receipts = root / "receipts"
            installed = install(
                remote, ROOT, protected_refs=("refs/heads/main",), signing_key=key,
                allowed_signers=allowed, receipt_dir=receipts,
            )
            self.assertEqual(installed["decision"], "pass", installed)
            self.assertEqual(audit(remote)["assurance"]["level"], "enforced")

            (source / "docs/a.md").write_text("unattested\n")
            subprocess.run(["git", "commit", "-qam", "unattested"], cwd=source, check=True)
            rejected = subprocess.run(
                ["git", "push", "origin", "main"], cwd=source, text=True, capture_output=True,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertEqual(subprocess.check_output(
                ["git", "rev-parse", "refs/heads/main"], cwd=remote, text=True
            ).strip(), base)

            commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
            self.attest(source, commit)
            subprocess.run([
                "git", "push", "--atomic", "-q", "origin",
                f"refs/harness/attestations/{commit}:refs/harness/attestations/{commit}",
                f"refs/harness/results/{commit}:refs/harness/results/{commit}",
            ], cwd=source, check=True)
            accepted = subprocess.run(
                ["git", "push", "origin", "main"], cwd=source, text=True, capture_output=True,
            )
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            receipt = json.loads((receipts / f"{commit}.json").read_text(encoding="utf-8"))
            self.assertEqual(validate_receipt(
                receipt, work_item_id="WI-42", repo=remote, allowed_signers=allowed,
            ), (True, "ACCEPTANCE_RECEIPT_VALID"))

    def test_audit_fails_after_hook_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, remote, _ = self.setup_repositories(root)
            key, allowed = self.keys(root)
            install(remote, ROOT, protected_refs=("refs/heads/main",), signing_key=key,
                    allowed_signers=allowed, receipt_dir=root / "receipts")
            (remote / "hooks/pre-receive").write_text("#!/bin/sh\nexit 0\n")
            self.assertEqual(audit(remote)["decision"], "block")
            self.assertNotEqual(audit(remote)["assurance"]["level"], "enforced")

    def test_audit_rejects_exposed_signing_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, remote, _ = self.setup_repositories(root)
            key, allowed = self.keys(root)
            install(remote, ROOT, protected_refs=("refs/heads/main",), signing_key=key,
                    allowed_signers=allowed, receipt_dir=root / "receipts")
            key.chmod(0o644)
            outcome = audit(remote)
            self.assertEqual(outcome["decision"], "block")
            self.assertFalse(outcome["assurance"]["signing_key_permissions_valid"])


if __name__ == "__main__":
    unittest.main()
