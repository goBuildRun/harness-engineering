#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from harness_attestation import create_attestation  # noqa: E402
from harness_runtime import default_result  # noqa: E402
from acceptance_authority import (  # noqa: E402
    TerminalAuthorization,
    authorize_provider_transition,
    terminal_authorization_matches,
)
from acceptance_trust import TrustPolicy  # noqa: E402
from provider_lifecycle import build_receipt, sign_policy, validate_receipt  # noqa: E402
import work_item  # noqa: E402


class ProviderLifecycleTest(unittest.TestCase):
    def keys(self, root: Path) -> tuple[Path, Path]:
        key = root / "authority"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
        allowed = root / "allowed_signers"
        public = key.with_suffix(".pub").read_text()
        allowed.write_text(f"harness {public}harness-policy {public}", encoding="utf-8")
        return key, allowed

    def policy(self, root: Path, key: Path, *, task_id: str = "task-1",
               work_items: list[dict[str, str]] | None = None) -> Path:
        target = root / "acceptance-policy.json"
        policy = {
            "schema": "harness-acceptance-policy-v1",
            "policy_id": "test-policy",
            "repo_id": "test-repo",
            "attested_task_id": task_id,
            "accepted_ref": "refs/heads/main",
            "accepted_commit_mode": "exact-ref-tip",
            "allowed_authorities": ["git-receive", "release-gate"],
            "terminal_work_items": work_items or [{
                "id": "WI-42", "provider": "jira", "attested_work_item_id": "WI-42",
            }],
            "closure_order": [item["id"] for item in (work_items or [{"id": "WI-42"}])],
            "expires_at": "2099-01-01T00:00:00Z",
        }
        sign_policy(policy, key)
        target.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")
        return target

    def trust(
        self, policy: Path, key: Path, *, completed_work_items: tuple[str, ...] = (),
    ) -> TrustPolicy:
        payload = json.loads(policy.read_text(encoding="utf-8"))
        canonical = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ) + "\n"
        fingerprint = subprocess.check_output(
            ["ssh-keygen", "-lf", str(key.with_suffix(".pub")), "-E", "sha256"],
            text=True,
        ).split()[1]
        return TrustPolicy(
            acceptance_policy_id=payload["policy_id"],
            acceptance_policy_digest=__import__("hashlib").sha256(
                canonical.encode("utf-8")
            ).hexdigest(),
            receipt_signer_fingerprint=fingerprint,
            policy_signer_fingerprint=fingerprint,
            completed_work_items=completed_work_items,
        )

    def test_receipt_binds_git_acceptance_without_platform_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "harness@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=repo, check=True)
            (repo / "a").write_text("a")
            subprocess.run(["git", "add", "a"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "a"], cwd=repo, check=True)
            commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
            result = default_result("task-1", work_item={"id": "WI-42", "provider": "jira"})
            result.update({"decision": "pass", "state": "validated", "policy_digest": "policy"})
            result["subject"] = {"kind": "commit", "digest": commit}
            result["invariants"] = {name: "pass" for name in result["invariants"]}
            create_attestation(repo, result, commit=commit)
            key, allowed = self.keys(repo)
            policy = self.policy(repo, key)
            receipt = build_receipt(
                repo, commit=commit, work_item_id="WI-42", provider="jira",
                authority="git-receive", accepted_ref="refs/heads/main", signing_key=key,
                acceptance_policy=policy, policy_allowed_signers=allowed, repo_id="test-repo",
            )
            self.assertNotIn("run_id", receipt)
            self.assertEqual(receipt["commit_sha"], commit)
            self.assertEqual(validate_receipt(
                receipt, work_item_id="WI-42", expected_ref="refs/heads/main",
                expected_commit=commit, configured_provider="jira", repo=repo,
                allowed_signers=allowed, acceptance_policy=policy,
                policy_allowed_signers=allowed, repo_id="test-repo",
                trust_policy=self.trust(policy, key))[0], True)
            receipt["accepted_ref"] = "refs/heads/other"
            self.assertEqual(validate_receipt(
                receipt, work_item_id="WI-42", expected_ref="refs/heads/main",
                expected_commit=commit, configured_provider="jira", repo=repo,
                allowed_signers=allowed, acceptance_policy=policy,
                policy_allowed_signers=allowed, repo_id="test-repo",
                trust_policy=self.trust(policy, key))[1],
                "ACCEPTANCE_REF_MISMATCH",
            )

            with self.assertRaisesRegex(ValueError, "ACCEPTANCE_REF_MISMATCH"):
                build_receipt(
                    repo, commit=commit, work_item_id="WI-42", provider="jira",
                    authority="git-receive", accepted_ref="refs/heads/other", signing_key=key,
                    acceptance_policy=policy, policy_allowed_signers=allowed,
                    repo_id="test-repo",
                )

            valid_receipt = build_receipt(
                repo, commit=commit, work_item_id="WI-42", provider="jira",
                authority="git-receive", accepted_ref="refs/heads/main", signing_key=key,
                acceptance_policy=policy, policy_allowed_signers=allowed, repo_id="test-repo",
            )
            (repo / "b").write_text("b")
            subprocess.run(["git", "add", "b"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "b"], cwd=repo, check=True)
            self.assertEqual(validate_receipt(
                valid_receipt, work_item_id="WI-42", expected_ref="refs/heads/main",
                expected_commit=commit, configured_provider="jira", repo=repo,
                allowed_signers=allowed, acceptance_policy=policy,
                policy_allowed_signers=allowed, repo_id="test-repo",
                trust_policy=self.trust(policy, key))[1],
                "ACCEPTANCE_COMMIT_NOT_REF_TIP",
            )
            subprocess.run(["git", "update-ref", "-d", "refs/heads/main"], cwd=repo, check=True)
            self.assertEqual(validate_receipt(
                valid_receipt, work_item_id="WI-42", expected_ref="refs/heads/main",
                expected_commit=commit, configured_provider="jira", repo=repo,
                allowed_signers=allowed, acceptance_policy=policy,
                policy_allowed_signers=allowed, repo_id="test-repo",
                trust_policy=self.trust(policy, key))[1],
                "ACCEPTANCE_REF_MISMATCH",
            )

    def test_external_policy_and_provider_bindings_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "harness@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=repo, check=True)
            (repo / "a").write_text("a")
            subprocess.run(["git", "add", "a"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "a"], cwd=repo, check=True)
            commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
            result = default_result(
                "task-1", work_item={"id": "WI-42", "provider": "jira"},
            )
            result.update({"decision": "pass", "state": "validated", "policy_digest": "policy"})
            result["subject"] = {"kind": "commit", "digest": commit}
            result["invariants"] = {name: "pass" for name in result["invariants"]}
            create_attestation(repo, result, commit=commit)
            key, allowed = self.keys(repo)
            policy = self.policy(repo, key)
            receipt = build_receipt(
                repo, commit=commit, work_item_id="WI-42", provider="jira",
                authority="release-gate", accepted_ref="refs/heads/main", signing_key=key,
                acceptance_policy=policy, policy_allowed_signers=allowed, repo_id="test-repo",
            )

            self.assertEqual(validate_receipt(
                receipt, work_item_id="WI-42", expected_ref="refs/heads/main",
                expected_commit=commit, configured_provider="github", repo=repo,
                allowed_signers=allowed, acceptance_policy=policy,
                policy_allowed_signers=allowed, repo_id="test-repo",
                trust_policy=self.trust(policy, key),
            )[1], "ACCEPTANCE_PROVIDER_MISMATCH")
            self.assertEqual(validate_receipt(
                receipt, work_item_id="WI-42", expected_ref="refs/heads/main",
                expected_commit="0" * 40, configured_provider="jira", repo=repo,
                allowed_signers=allowed, acceptance_policy=policy,
                policy_allowed_signers=allowed, repo_id="test-repo",
                trust_policy=self.trust(policy, key),
            )[1], "ACCEPTANCE_COMMIT_MISMATCH")
            self.assertEqual(validate_receipt(
                receipt, work_item_id="WI-42", expected_ref="refs/heads/main",
                expected_commit=commit, configured_provider="jira", repo=repo,
                allowed_signers=allowed, acceptance_policy=policy,
                policy_allowed_signers=allowed, repo_id="other-repo",
                trust_policy=self.trust(policy, key),
            )[1], "ACCEPTANCE_POLICY_REPO_MISMATCH")

            tampered = json.loads(policy.read_text(encoding="utf-8"))
            tampered["allowed_authorities"] = ["git-receive"]
            policy.write_text(json.dumps(tampered, indent=2) + "\n", encoding="utf-8")
            self.assertEqual(validate_receipt(
                receipt, work_item_id="WI-42", expected_ref="refs/heads/main",
                expected_commit=commit, configured_provider="jira", repo=repo,
                allowed_signers=allowed, acceptance_policy=policy,
                policy_allowed_signers=allowed, repo_id="test-repo",
                trust_policy=self.trust(policy, key),
            )[1], "ACCEPTANCE_POLICY_SIGNATURE_INVALID")

            other_items = [{
                "id": "WI-43", "provider": "jira", "attested_work_item_id": "WI-42",
            }]
            policy = self.policy(repo, key, work_items=other_items)
            with self.assertRaisesRegex(ValueError, "ACCEPTANCE_POLICY_WORK_ITEM_MISMATCH"):
                build_receipt(
                    repo, commit=commit, work_item_id="WI-42", provider="jira",
                    authority="git-receive", accepted_ref="refs/heads/main", signing_key=key,
                    acceptance_policy=policy, policy_allowed_signers=allowed,
                    repo_id="test-repo",
                )

            multi_items = [
                {"id": "WI-42", "provider": "jira", "attested_work_item_id": "WI-42"},
                {"id": "WI-docs", "provider": "jira", "attested_work_item_id": "WI-42"},
            ]
            policy = self.policy(repo, key, work_items=multi_items)
            predecessor_receipt = build_receipt(
                repo, commit=commit, work_item_id="WI-docs", provider="jira",
                authority="release-gate", accepted_ref="refs/heads/main", signing_key=key,
                acceptance_policy=policy, policy_allowed_signers=allowed, repo_id="test-repo",
            )
            self.assertEqual(validate_receipt(
                predecessor_receipt, work_item_id="WI-docs", expected_ref="refs/heads/main",
                expected_commit=commit, configured_provider="jira", repo=repo,
                allowed_signers=allowed, acceptance_policy=policy,
                policy_allowed_signers=allowed, repo_id="test-repo",
                trust_policy=self.trust(policy, key),
            )[1], "ACCEPTANCE_CLOSURE_ORDER_BLOCKED")
            self.assertEqual(validate_receipt(
                predecessor_receipt, work_item_id="WI-docs", expected_ref="refs/heads/main",
                expected_commit=commit, configured_provider="jira", repo=repo,
                allowed_signers=allowed, acceptance_policy=policy,
                policy_allowed_signers=allowed, repo_id="test-repo",
                trust_policy=self.trust(
                    policy, key, completed_work_items=("WI-42",),
                ),
            ), (True, "ACCEPTANCE_RECEIPT_VALID"))

    def test_terminal_receipt_rejects_expired_signature(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "harness@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=repo, check=True)
            (repo / "a").write_text("a")
            subprocess.run(["git", "add", "a"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "a"], cwd=repo, check=True)
            commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
            result = default_result("task-1", work_item={"id": "WI-42", "provider": "jira"})
            result.update({"decision": "pass", "state": "validated", "policy_digest": "policy"})
            result["subject"] = {"kind": "commit", "digest": commit}
            result["invariants"] = {name: "pass" for name in result["invariants"]}
            create_attestation(repo, result, commit=commit)
            key, allowed = self.keys(repo)
            policy = self.policy(repo, key)
            receipt = build_receipt(
                repo, commit=commit, work_item_id="WI-42", provider="jira",
                authority="git-receive", accepted_ref="refs/heads/main", signing_key=key,
                acceptance_policy=policy, policy_allowed_signers=allowed, repo_id="test-repo",
            )
            receipt["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            self.assertEqual(validate_receipt(
                receipt, work_item_id="WI-42", expected_ref="refs/heads/main",
                expected_commit=commit, configured_provider="jira", repo=repo,
                allowed_signers=allowed, acceptance_policy=policy,
                policy_allowed_signers=allowed, repo_id="test-repo",
                trust_policy=self.trust(policy, key),
            )[1], "ACCEPTANCE_RECEIPT_EXPIRED")

    def test_caller_generated_trust_roots_cannot_authorize_terminal_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
            subprocess.run(
                ["git", "config", "user.email", "harness@example.invalid"],
                cwd=repo,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Harness Test"], cwd=repo, check=True
            )
            (repo / "a").write_text("a")
            subprocess.run(["git", "add", "a"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "a"], cwd=repo, check=True)
            commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repo, text=True
            ).strip()
            result = default_result(
                "task-1", work_item={"id": "WI-42", "provider": "jira"}
            )
            result.update({"decision": "pass", "state": "validated", "policy_digest": "policy"})
            result["subject"] = {"kind": "commit", "digest": commit}
            result["invariants"] = {name: "pass" for name in result["invariants"]}
            create_attestation(repo, result, commit=commit)

            authority = repo / "authority-root"
            authority.mkdir()
            authority_key, authority_allowed = self.keys(authority)
            authority_policy = self.policy(authority, authority_key)
            pinned = self.trust(authority_policy, authority_key)

            attacker = repo / "attacker-root"
            attacker.mkdir()
            attacker_key, attacker_allowed = self.keys(attacker)
            attacker_policy = self.policy(attacker, attacker_key)
            forged_policy_receipt = build_receipt(
                repo,
                commit=commit,
                work_item_id="WI-42",
                provider="jira",
                authority="release-gate",
                accepted_ref="refs/heads/main",
                signing_key=attacker_key,
                acceptance_policy=attacker_policy,
                policy_allowed_signers=attacker_allowed,
                repo_id="test-repo",
            )
            self.assertEqual(
                validate_receipt(
                    forged_policy_receipt,
                    work_item_id="WI-42",
                    expected_ref="refs/heads/main",
                    expected_commit=commit,
                    configured_provider="jira",
                    repo=repo,
                    allowed_signers=attacker_allowed,
                    acceptance_policy=attacker_policy,
                    policy_allowed_signers=attacker_allowed,
                    repo_id="test-repo",
                    trust_policy=pinned,
                )[1],
                "ACCEPTANCE_POLICY_SIGNER_MISMATCH",
            )

            forged_receipt = build_receipt(
                repo,
                commit=commit,
                work_item_id="WI-42",
                provider="jira",
                authority="release-gate",
                accepted_ref="refs/heads/main",
                signing_key=attacker_key,
                acceptance_policy=authority_policy,
                policy_allowed_signers=authority_allowed,
                repo_id="test-repo",
            )
            self.assertEqual(
                validate_receipt(
                    forged_receipt,
                    work_item_id="WI-42",
                    expected_ref="refs/heads/main",
                    expected_commit=commit,
                    configured_provider="jira",
                    repo=repo,
                    allowed_signers=attacker_allowed,
                    acceptance_policy=authority_policy,
                    policy_allowed_signers=authority_allowed,
                    repo_id="test-repo",
                    trust_policy=pinned,
                )[1],
                "ACCEPTANCE_SIGNER_MISMATCH",
            )

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
                receipt, work_item_id="WI-42", expected_ref="refs/heads/main",
                expected_commit="0" * 40, configured_provider="jira", repo=repo,
                allowed_signers=allowed, acceptance_policy=repo / "missing-policy",
                policy_allowed_signers=allowed, repo_id="test-repo",
                trust_policy=TrustPolicy(
                    acceptance_policy_id="test-policy",
                    acceptance_policy_digest="0" * 64,
                    receipt_signer_fingerprint="SHA256:" + "A" * 43,
                    policy_signer_fingerprint="SHA256:" + "A" * 43,
                ))[1],
                "ACCEPTANCE_AUTHORITY_INVALID",
            )
            receipt["authority"] = "git-receive"
            receipt["signature"] = "fake"
            self.assertEqual(validate_receipt(
                receipt, work_item_id="WI-43", expected_ref="refs/heads/main",
                expected_commit="0" * 40, configured_provider="jira", repo=repo,
                allowed_signers=allowed, acceptance_policy=repo / "missing-policy",
                policy_allowed_signers=allowed, repo_id="test-repo",
                trust_policy=TrustPolicy(
                    acceptance_policy_id="test-policy",
                    acceptance_policy_digest="0" * 64,
                    receipt_signer_fingerprint="SHA256:" + "A" * 43,
                    policy_signer_fingerprint="SHA256:" + "A" * 43,
                ))[1],
                "ACCEPTANCE_WORK_ITEM_MISMATCH",
            )

    def test_local_terminal_close_always_requires_authority_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receipt = root / "receipt.json"
            receipt.write_text("{}\n", encoding="utf-8")
            provider = mock.Mock(name="provider")
            provider.name = "feishu"
            for status in (
                "done",
                "closed",
                "completed",
                "implemented",
                "released",
                "mr_merged",
                "已完成",
                "已实现",
                "已发布",
            ):
                args = SimpleNamespace(
                    harness_root=str(root), id="WI-42", status=status, note="",
                    lifecycle_receipt=str(receipt), accepted_ref="refs/heads/main",
                    accepted_commit="0" * 40,
                )
                with self.subTest(status=status), mock.patch(
                    "work_item.get_provider", return_value=provider
                ), redirect_stdout(io.StringIO()):
                    self.assertEqual(work_item.cmd_close(args), 0)
            provider.update_status.assert_not_called()

    def test_provider_authorization_rejects_attribute_lookalike(self) -> None:
        forged = SimpleNamespace(
            work_item_id="WI-42",
            provider="feishu",
            status="done",
            receipt_digest="0" * 64,
        )
        self.assertFalse(
            terminal_authorization_matches(
                forged,
                work_item_id="WI-42",
                provider="feishu",
                status="done",
            )
        )

        class ForgedAuthorization(TerminalAuthorization):
            def __init__(self) -> None:
                self.work_item_id = "WI-42"
                self.provider = "feishu"
                self.status = "done"
                self.receipt_digest = "0" * 64

        self.assertFalse(
            terminal_authorization_matches(
                ForgedAuthorization(),
                work_item_id="WI-42",
                provider="feishu",
                status="done",
            )
        )
        with self.assertRaisesRegex(ValueError, "TERMINAL_AUTHORIZATION_ISSUER_INVALID"):
            TerminalAuthorization()

        unregistered = object.__new__(TerminalAuthorization)
        unregistered.work_item_id = "WI-42"
        unregistered.provider = "feishu"
        unregistered.status = "done"
        unregistered.receipt_digest = "0" * 64
        self.assertFalse(
            terminal_authorization_matches(
                unregistered,
                work_item_id="WI-42",
                provider="feishu",
                status="done",
            )
        )

    def test_acceptance_authority_issues_only_after_validation_for_feishu(self) -> None:
        receipt = {"schema": "harness-acceptance-receipt-v1", "signature": "signed"}
        trust = TrustPolicy(
            acceptance_policy_id="policy",
            acceptance_policy_digest="0" * 64,
            receipt_signer_fingerprint="SHA256:" + "A" * 43,
            policy_signer_fingerprint="SHA256:" + "B" * 43,
        )
        kwargs = {
            "work_item_id": "WI-42",
            "provider": "feishu",
            "status": "done",
            "expected_ref": "refs/heads/main",
            "expected_commit": "0" * 40,
            "repo": Path("."),
            "allowed_signers": Path("receipt-signers"),
            "acceptance_policy": Path("policy.json"),
            "policy_allowed_signers": Path("policy-signers"),
            "repo_id": "repo",
            "trust_policy": trust,
        }
        with mock.patch(
            "acceptance_authority.validate_receipt",
            return_value=(True, "ACCEPTANCE_RECEIPT_VALID"),
        ) as validate:
            authorization = authorize_provider_transition(receipt, **kwargs)
        validate.assert_called_once()
        self.assertTrue(
            terminal_authorization_matches(
                authorization,
                work_item_id="WI-42",
                provider="feishu",
                status="done",
            )
        )

        with mock.patch("acceptance_authority.validate_receipt") as validate:
            with self.assertRaisesRegex(
                ValueError, "ACCEPTANCE_PROVIDER_TERMINAL_UNSUPPORTED"
            ):
                authorize_provider_transition(receipt, **{**kwargs, "provider": "jira"})
        validate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
