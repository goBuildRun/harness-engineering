#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from harness_attestation import create_attestation  # noqa: E402
from harness_gc_context import build_gc_context  # noqa: E402
from harness_gc_receipt import build_receipt, store_receipt  # noqa: E402
from harness_receive import _export_submodules, accept_updates, commits_for_update, verify_updates  # noqa: E402
from harness_runtime import default_result, mechanical_code_health, policy_for  # noqa: E402
from provider_lifecycle import sign_policy, validate_receipt  # noqa: E402


class HarnessReceiveTest(unittest.TestCase):
    def repository(self, root: Path) -> tuple[str, str]:
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "harness@example.invalid"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=root, check=True)
        (root / "docs").mkdir()
        (root / "docs/a.md").write_text("base\n")
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
        old = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        (root / "docs/a.md").write_text("changed\n")
        subprocess.run(["git", "commit", "-qam", "change"], cwd=root, check=True)
        new = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        return old, new

    def attest(self, repo: Path, commit: str, *, work_item: dict[str, str] | None = None) -> None:
        result = default_result("receive-task", initial_tier="lite", work_item=work_item)
        result.update({"state": "validated", "decision": "pass", "policy_digest": policy_for(ROOT, repo)})
        result["subject"] = {"kind": "commit", "digest": commit}
        result["task"] = {"scope": ["docs"], "tier_floor": "lite"}
        result["invariants"] = {name: "pass" for name in result["invariants"]}
        create_attestation(repo, result, commit=commit)

    def keys(self, root: Path) -> tuple[Path, Path]:
        key = root / "authority"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
        allowed = root / "allowed_signers"
        public = key.with_suffix(".pub").read_text()
        allowed.write_text(f"harness {public}harness-policy {public}", encoding="utf-8")
        return key, allowed

    def policy(self, root: Path, key: Path) -> Path:
        target = root / "acceptance-policy.json"
        policy = {
            "schema": "harness-acceptance-policy-v1",
            "policy_id": "receive-policy",
            "repo_id": "test-repo",
            "attested_task_id": "receive-task",
            "accepted_ref": "refs/heads/main",
            "accepted_commit_mode": "exact-ref-tip",
            "allowed_authorities": ["git-receive"],
            "terminal_work_items": [{
                "id": "WI-42", "provider": "jira", "attested_work_item_id": "WI-42",
            }],
            "closure_order": ["WI-42"],
            "expires_at": "2099-01-01T00:00:00Z",
        }
        sign_policy(policy, key)
        target.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")
        return target

    def test_commit_range_excludes_old_and_handles_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            old, new = self.repository(repo)
            self.assertEqual(commits_for_update(repo, old, new), [new])
            self.assertEqual(commits_for_update(repo, new, "0" * 40), [])

    def test_invalid_protected_update_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            old, _ = self.repository(repo)
            outcome = verify_updates(
                repo, ROOT, [(old, "f" * len(old), "refs/heads/main")]
            )
            self.assertEqual(outcome["decision"], "block")
            self.assertEqual(outcome["reason"], "RECEIVE_RANGE_INVALID")

    def test_protected_ref_rejects_non_fast_forward(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            old, new = self.repository(repo)
            outcome = verify_updates(repo, ROOT, [(new, old, "refs/heads/main")])
            self.assertEqual(outcome["reason"], "RECEIVE_NON_FAST_FORWARD_BLOCK")

    def test_receive_recomputes_commit_result_without_network_or_workspace_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            old, new = self.repository(repo)
            self.attest(repo, new)
            before = subprocess.check_output(["git", "show-ref"], cwd=repo, text=True)
            outcome = verify_updates(repo, ROOT, [(old, new, "refs/heads/main")])
            after = subprocess.check_output(["git", "show-ref"], cwd=repo, text=True)
            self.assertEqual(outcome["decision"], "pass", outcome)
            self.assertEqual(after, before)
            self.assertFalse((repo / "harness-workspace").exists())

    def test_receive_works_from_bare_repository(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            source.mkdir()
            old, new = self.repository(source)
            self.attest(source, new)
            bare = Path(tmp) / "remote.git"
            subprocess.run(["git", "clone", "--bare", "-q", str(source), str(bare)], check=True)
            blob = subprocess.check_output(
                ["git", "rev-parse", f"refs/harness/attestations/{new}"], cwd=source, text=True
            ).strip()
            result_object = json.loads(subprocess.check_output(
                ["git", "cat-file", "blob", blob], cwd=source, text=True
            ))["result_object"]
            for obj in (blob, result_object):
                data = subprocess.check_output(["git", "cat-file", "blob", obj], cwd=source)
                subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=bare, input=data, check=True,
                               stdout=subprocess.DEVNULL)
            subprocess.run(["git", "update-ref", f"refs/harness/attestations/{new}", blob], cwd=bare, check=True)
            subprocess.run(["git", "update-ref", f"refs/harness/results/{new}", result_object], cwd=bare, check=True)
            outcome = verify_updates(bare, ROOT, [(old, new, "refs/heads/main")])
            self.assertEqual(outcome["decision"], "pass", outcome)

    def test_receive_materializes_submodule_from_explicit_local_object_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            component = root / "component"
            component.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=component, check=True)
            subprocess.run(["git", "config", "user.email", "harness@example.invalid"], cwd=component, check=True)
            subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=component, check=True)
            (component / "value.txt").write_text("trusted component\n")
            subprocess.run(["git", "add", "."], cwd=component, check=True)
            subprocess.run(["git", "commit", "-qm", "component"], cwd=component, check=True)

            repo = root / "product"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "harness@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=repo, check=True)
            (repo / "docs").mkdir()
            (repo / "docs/a.md").write_text("base\n")
            subprocess.run([
                "git", "-c", "protocol.file.allow=always", "submodule", "add", "-q",
                str(component), "vendor/component",
            ], cwd=repo, check=True)
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "base with component"], cwd=repo, check=True)
            old = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
            (repo / "docs/a.md").write_text("changed\n")
            subprocess.run(["git", "commit", "-qam", "change"], cwd=repo, check=True)
            new = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
            self.attest(repo, new)

            missing = verify_updates(repo, ROOT, [(old, new, "refs/heads/main")])
            self.assertEqual(missing["reason"], "RECEIVE_SUBMODULE_SOURCE_MISSING:vendor/component")

            def gates(_harness: Path, checkout: Path, **_kwargs: object) -> dict[str, object]:
                self.assertEqual(
                    (checkout / "vendor/component/value.txt").read_text(), "trusted component\n",
                )
                self.assertEqual(subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=checkout, text=True,
                ).strip(), new)
                return {"decision": "pass", "checks": {}, "missing": []}

            with mock.patch("harness_receive.run_gate_plan", side_effect=gates):
                accepted = verify_updates(
                    repo, ROOT, [(old, new, "refs/heads/main")],
                    submodule_repositories={"vendor/component": component},
                )
            self.assertEqual(accepted["decision"], "pass", accepted)

            destination = root / "isolated"
            destination.mkdir()
            component_sha = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=component, text=True,
            ).strip()
            polluted = {
                "GIT_OBJECT_DIRECTORY": str(root / "parent-quarantine"),
                "GIT_QUARANTINE_PATH": str(root / "parent-quarantine"),
            }
            with mock.patch.dict(os.environ, polluted), mock.patch(
                "harness_receive._gitlinks",
                return_value={"vendor/component": component_sha},
            ):
                _export_submodules(
                    repo, new, destination, {"vendor/component": component},
                )
            self.assertEqual(
                (destination / "vendor/component/value.txt").read_text(), "trusted component\n",
            )

    def test_receive_rejects_missing_attestation_and_unverified_middle_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            old, middle = self.repository(repo)
            (repo / "docs/a.md").write_text("again\n")
            subprocess.run(["git", "commit", "-qam", "again"], cwd=repo, check=True)
            tip = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
            self.attest(repo, tip)
            outcome = verify_updates(repo, ROOT, [(old, tip, "refs/heads/main")])
            self.assertEqual(outcome["reason"], "RECEIVE_ATTESTATION_INVALID")
            self.assertEqual(outcome["commit"], middle)

    def test_non_protected_ref_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            old, new = self.repository(repo)
            outcome = verify_updates(repo, ROOT, [(old, new, "refs/heads/scratch")],
                                     protected_refs=("refs/heads/main",))
            self.assertEqual(outcome["decision"], "pass")
            self.assertEqual(outcome["verified_commits"], [])

    def test_receive_recomputes_code_health_from_commit_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "harness@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=repo, check=True)
            (repo / "src").mkdir()
            (repo / "src/a.py").write_text("x = 1\n")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
            old = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
            (repo / "src/a.py").write_text("print('debug')\n")
            subprocess.run(["git", "commit", "-qam", "debug"], cwd=repo, check=True)
            new = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
            result = default_result("receive-task")
            result.update({"state": "validated", "decision": "pass", "policy_digest": policy_for(ROOT, repo)})
            result["subject"] = {"kind": "commit", "digest": new}
            result["task"] = {"scope": ["src"], "tier_floor": "standard"}
            result["invariants"] = {name: "pass" for name in result["invariants"]}
            create_attestation(repo, result, commit=new)
            outcome = verify_updates(repo, ROOT, [(old, new, "refs/heads/main")])
            self.assertEqual(outcome["reason"], "RECEIVE_CODE_HEALTH_BLOCK")

    def test_receive_does_not_trust_client_gc_agent_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "harness@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=repo, check=True)
            (repo / "src").mkdir()
            (repo / "src/a.py").write_text("def live():\n    return 1\n")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
            old = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
            (repo / "src/a.py").write_text("def unused_helper():\n    pass\n")
            subprocess.run(["git", "commit", "-qam", "dead code"], cwd=repo, check=True)
            new = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
            result = default_result("receive-task", initial_tier="standard")
            result.update({"state": "validated", "decision": "pass", "policy_digest": policy_for(ROOT, repo)})
            result["subject"] = {"kind": "commit", "digest": new}
            result["task"] = {"scope": ["src"], "tier_floor": "standard"}
            result["invariants"] = {name: "pass" for name in result["invariants"]}
            result["checks"]["code_health"] = {
                "decision": "pass", "mode": "mechanical+agent", "agent_result_digest": "forged",
            }
            create_attestation(repo, result, commit=new)
            outcome = verify_updates(repo, ROOT, [(old, new, "refs/heads/main")])
            self.assertEqual(outcome["reason"], "RECEIVE_GC_AUTHORITY_REQUIRED")

            key, allowed = self.keys(repo)
            mechanical = mechanical_code_health(repo, ["src/a.py"], tier="standard")
            context_result = result | {"task": {"scope": ["src"], "tier_floor": "standard"}}
            context, context_chars = build_gc_context(
                context_result, mechanical, ["src/a.py"], repo, base_ref=f"{new}^",
            )
            gc_result = {
                "decision": "pass", "role": "gc-sweeper", "independent": True,
                "task_id": "receive-task", "subject_digest": new,
                "policy_digest": result["policy_digest"], "findings": 1, "remediated": 1,
                "deferred_work_items": [],
                "mechanical_adjudication": [
                    {
                        "trigger": trigger, "decision": "remediated",
                        "reason": "independent authority reviewed the trigger",
                    }
                    for trigger in mechanical["triggers"]
                ],
                "telemetry": {"agent_calls": 1, "context_chars": context_chars, "duration_ms": 10,
                              "provider": "compatible", "model": "gc-model"},
            }
            receipt = build_receipt(
                commit=new, task_id="receive-task", policy_digest=result["policy_digest"],
                context=context, triggers=mechanical["triggers"], gc_result=gc_result,
                signing_key=key,
            )
            store_receipt(repo, receipt)
            with mock.patch("harness_receive.run_gate_plan", return_value={
                "decision": "pass", "checks": {}, "missing": [],
            }):
                accepted = verify_updates(
                    repo, ROOT, [(old, new, "refs/heads/main")], gc_allowed_signers=allowed,
                )
            self.assertEqual(accepted["decision"], "pass", accepted)

    def test_receive_reruns_gates_instead_of_trusting_client_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            old, new = self.repository(repo)
            self.attest(repo, new)
            with mock.patch("harness_receive.run_gate_plan", return_value={
                "decision": "block",
                "checks": {"quality_test": {
                    "decision": "block", "reason": "QUALITY_TEST_FAILED",
                }},
                "missing": [],
            }):
                outcome = verify_updates(repo, ROOT, [(old, new, "refs/heads/main")])
            self.assertEqual(outcome["reason"], "RECEIVE_GATE_BLOCK")
            self.assertEqual(outcome["blocked_gates"], ["quality_test"])
            self.assertEqual(outcome["gate_reasons"], {
                "quality_test": "QUALITY_TEST_FAILED",
            })

    def test_acceptance_receipt_is_derived_from_verified_commit_binding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            old, new = self.repository(repo)
            self.attest(repo, new, work_item={"id": "WI-42", "provider": "jira"})
            key, allowed = self.keys(repo)
            policy = self.policy(repo, key)
            outcome = accept_updates(
                repo, ROOT, [(old, new, "refs/heads/main")], signing_key=key,
                acceptance_policy=policy, policy_allowed_signers=allowed, repo_id="test-repo",
            )
            self.assertEqual(outcome["decision"], "pass", outcome)
            receipt = outcome["receipts"][0]
            self.assertEqual(receipt["accepted_ref"], "refs/heads/main")
            self.assertEqual(receipt["work_item_id"], "WI-42")
            self.assertEqual(receipt["provider"], "jira")
            self.assertEqual(validate_receipt(
                receipt, work_item_id="WI-42", expected_ref="refs/heads/main",
                expected_commit=new, configured_provider="jira", repo=repo,
                allowed_signers=allowed, acceptance_policy=policy,
                policy_allowed_signers=allowed, repo_id="test-repo",
            ), (True, "ACCEPTANCE_RECEIPT_VALID"))

    def test_acceptance_does_not_sign_unbound_or_unverified_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            old, new = self.repository(repo)
            key, allowed = self.keys(repo)
            policy = self.policy(repo, key)
            self.attest(repo, new)
            outcome = accept_updates(
                repo, ROOT, [(old, new, "refs/heads/main")], signing_key=key,
                acceptance_policy=policy, policy_allowed_signers=allowed, repo_id="test-repo",
            )
            self.assertEqual(outcome["reason"], "ACCEPTANCE_WORK_ITEM_BINDING_MISSING")
            self.assertNotIn("receipts", outcome)


if __name__ == "__main__":
    unittest.main()
