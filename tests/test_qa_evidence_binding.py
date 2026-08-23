#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from harness_runtime import atomic_write_result, canonical_digest, default_result  # noqa: E402
from harness_execution_authority import (  # noqa: E402
    AuthorityTrust, build_receipt, sign_receipt,
)
from qa_evidence_binding import prepare_bundle, receipt_binding  # noqa: E402
from qa_evidence_check import validate_qa_json, validate_reviewer_identity  # noqa: E402
from workspace_paths import load_layout  # noqa: E402
from worktree_baseline import capture_baseline, changed_since_baseline  # noqa: E402


class QaEvidenceBindingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.qa_trust: dict[Path, AuthorityTrust] = {}

    def test_unkeyed_host_identity_digest_is_not_authority(self) -> None:
        identity = {
            "schema": "harness-host-reviewer-identity-v1",
            "issued_by": "codex-host",
            "reviewer_role": "qa-evaluator",
            "session_id": "self-selected-session",
            "work_item_id": "WI-42",
            "task_id": "T1",
            "issued_at": "2026-08-23T00:00:00Z",
        }
        identity["receipt_digest"] = canonical_digest(identity)

        _session, issue = validate_reviewer_identity(
            identity, work_item_id="WI-42", task_id="T1",
        )
        self.assertEqual(issue, "QA_REVIEWER_IDENTITY_RECEIPT_INVALID")

    def reviewer_identity(
        self, product: Path, session: str,
    ) -> tuple[Path, AuthorityTrust]:
        authority = product.parent / f"qa-authority-{session}"
        authority.mkdir(exist_ok=True)
        key = authority / "key"
        if not key.is_file():
            subprocess.run(
                ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
                check=True,
            )
        public = key.with_suffix(".pub").read_text(encoding="utf-8")
        allowed = authority / "allowed_signers"
        allowed.write_text(f"harness-qa-reviewer {public}", encoding="utf-8")
        fingerprint = subprocess.check_output(
            ["ssh-keygen", "-lf", str(key.with_suffix(".pub")), "-E", "sha256"],
            text=True,
        ).split()[1]
        trust = AuthorityTrust(allowed, fingerprint, "harness-qa-reviewer")
        claims = {
            "issued_by": "codex-host",
            "reviewer_role": "qa-evaluator",
            "session_id": session,
            "work_item_id": "WI-42",
            "task_id": "T1",
        }
        issued = datetime.now(timezone.utc)
        receipt = sign_receipt(build_receipt(
            authority="qa-reviewer-identity",
            action="qa-signoff",
            subject_digest=canonical_digest({"work_item_id": "WI-42", "task_id": "T1"}),
            provider="codex-host",
            input_digest=canonical_digest({
                "work_item_id": "WI-42", "task_id": "T1",
                "reviewer_role": "qa-evaluator",
            }),
            output_digest=canonical_digest({"session_id": session}),
            claims=claims,
            issued_at=issued,
            expires_at=issued + timedelta(minutes=5),
        ), key)
        path = authority / "reviewer-identity.json"
        path.write_text(json.dumps(receipt), encoding="utf-8")
        self.qa_trust[product.resolve()] = trust
        return path, trust

    def receipt_binding(self, product: Path, paths: list[str], session: str = "reviewer"):
        identity, trust = self.reviewer_identity(product, session)
        with mock.patch.dict(os.environ, {
            "HARNESS_QA_REVIEWER_IDENTITY_RECEIPT": str(identity),
        }), mock.patch("qa_evidence_check.trust_from_installation", return_value=trust):
            return receipt_binding(ROOT, product, "WI-42", "T1", paths)

    def validate_qa(self, path: Path, layout) -> list[str]:
        trust = self.qa_trust.get(layout.product_root.resolve())
        if trust is None:
            return validate_qa_json(path, "T1", "WI-42", layout)
        with mock.patch("qa_evidence_check.trust_from_installation", return_value=trust):
            return validate_qa_json(path, "T1", "WI-42", layout)

    def fixture(self, root: Path):
        product = root / "product"
        workspace = product / "harness-workspace"
        task = workspace / "planning/tasks/demo"
        runs = workspace / "runs"
        task.mkdir(parents=True)
        runs.mkdir(parents=True)
        (workspace / "project.yaml").write_text(
            "product:\n  id: demo\n  name: Demo\nworkspace:\n  root: harness-workspace\n  planning: planning\n  runs: runs\n  evidence: evidence\nwork_item:\n  provider: noop\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "init", "-q"], cwd=product, check=True)
        subprocess.run(["git", "config", "user.email", "harness@example.invalid"], cwd=product, check=True)
        subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=product, check=True)
        (product / "source.py").write_text("before\n")
        (product / ".gitignore").write_text("harness-workspace/runs/\n")
        subprocess.run(["git", "add", "."], cwd=product, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=product, check=True)
        (runs / "active_task.json").write_text(json.dumps({"task_id": "WI-42"}))
        (runs / "planning_gate_pass.json").write_text(json.dumps({
            "decision": "pass", "task_dir": str(task),
            "work_item": {"id": "WI-42", "provider": "noop"},
        }))
        task_root = runs / "tasks/WI-42"
        capture_baseline(product, task_root / "worktree_baseline.json", work_item_id="WI-42")
        result = default_result("WI-42", work_item={"id": "WI-42", "provider": "noop"})
        result["cost"]["story_usage_baseline"] = {"session_id": "implementer"}
        atomic_write_result(task_root / "result.json", result)
        (product / "source.py").write_text("after\n")
        layout = load_layout(ROOT, product)
        layout.test_reports_dir.mkdir(parents=True)
        layout.review_reports_dir.mkdir(parents=True)
        (layout.test_reports_dir / "WI-42-T1-TEST.md").write_text("结论：`pass`\n")
        (layout.review_reports_dir / "WI-42-T1-REVIEW.md").write_text("结论：`pass`\n")
        return product, layout, task_root, changed_since_baseline(
            product, task_root / "worktree_baseline.json",
        )

    def test_shared_bundle_is_reused_and_receipt_is_independent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product, layout, task_root, paths = self.fixture(Path(tmp))
            def runner(_harness, _product):  # type: ignore[no-untyped-def]
                return {"decision": "pass", "reason": "TEST_PASS"}

            first = prepare_bundle(ROOT, product, "WI-42", paths, runner)
            second = prepare_bundle(ROOT, product, "WI-42", paths, runner)
            binding = self.receipt_binding(product, paths)
            receipt = {
                "task_id": "T1", "work_item_id": "WI-42", "decision": "pass",
                "reviewer": "qa-evaluator", "structure_gate": "pass",
                "paths_reviewed": paths, **binding["binding"],
            }
            path = task_root / "qa_approved_T1.json"
            path.write_text(json.dumps(receipt))
            issues = self.validate_qa(path, layout)
        self.assertEqual(first["reason"], "QA_BUNDLE_PREPARED")
        self.assertEqual(second["reason"], "QA_BUNDLE_REUSED")
        self.assertEqual(issues, [])

    def test_same_session_self_sign_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product, _layout, _task_root, paths = self.fixture(Path(tmp))
            prepare_bundle(
                ROOT, product, "WI-42", paths,
                lambda _harness, _product: {"decision": "pass"},
            )
            result = self.receipt_binding(product, paths, "implementer")
        self.assertEqual(result["reason"], "QA_INDEPENDENCE_UNPROVEN")

    def test_missing_host_reviewer_identity_blocks_signoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product, _layout, _task_root, paths = self.fixture(Path(tmp))
            prepare_bundle(
                ROOT, product, "WI-42", paths,
                lambda _harness, _product: {"decision": "pass"},
            )
            with mock.patch.dict(os.environ, {}, clear=True):
                result = receipt_binding(ROOT, product, "WI-42", "T1", paths)
        self.assertEqual(result["reason"], "QA_HOST_REVIEWER_IDENTITY_MISSING")

    def test_source_change_invalidates_old_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product, layout, task_root, paths = self.fixture(Path(tmp))
            prepare_bundle(
                ROOT, product, "WI-42", paths,
                lambda _harness, _product: {"decision": "pass"},
            )
            binding = self.receipt_binding(product, paths)
            receipt = {
                "task_id": "T1", "work_item_id": "WI-42", "decision": "pass",
                "reviewer": "qa-evaluator", "structure_gate": "pass",
                "paths_reviewed": paths, **binding["binding"],
            }
            path = task_root / "qa_approved_T1.json"
            path.write_text(json.dumps(receipt))
            (product / "source.py").write_text("changed again\n")
            issues = self.validate_qa(path, layout)
        self.assertTrue(any(item.startswith("QA_SUBJECT_MISMATCH:") for item in issues))

    def test_failed_mechanical_gate_and_unknown_implementer_cannot_sign(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product, _layout, task_root, paths = self.fixture(Path(tmp))
            blocked = prepare_bundle(
                ROOT, product, "WI-42", paths,
                lambda _harness, _product: {"decision": "block", "reason": "TEST_BLOCK"},
            )
            result = json.loads((task_root / "result.json").read_text())
            result["cost"]["story_usage_baseline"]["session_id"] = "unknown"
            atomic_write_result(task_root / "result.json", result)
            prepare_bundle(
                ROOT, product, "WI-42", paths,
                lambda _harness, _product: {"decision": "pass"},
            )
            unknown = self.receipt_binding(product, paths)
        self.assertEqual(blocked["decision"], "block")
        self.assertEqual(unknown["reason"], "QA_INDEPENDENCE_UNPROVEN")

    def test_governed_standard_and_strict_tasks_reject_legacy_receipt(self) -> None:
        for tier in ("standard", "strict"):
            with self.subTest(tier=tier), tempfile.TemporaryDirectory() as tmp:
                product, layout, task_root, paths = self.fixture(Path(tmp))
                result = json.loads((task_root / "result.json").read_text())
                result["tier"] = {"initial": tier, "effective": tier}
                atomic_write_result(task_root / "result.json", result)
                receipt = {
                    "task_id": "T1", "work_item_id": "WI-42", "decision": "pass",
                    "reviewer": "qa-evaluator", "structure_gate": "pass",
                    "paths_reviewed": paths,
                }
                path = task_root / "qa_approved_T1.json"
                path.write_text(json.dumps(receipt))
                issues = self.validate_qa(path, layout)
            self.assertTrue(any(item.startswith("QA_V2_RECEIPT_REQUIRED:") for item in issues))

    def test_malformed_current_result_fails_closed_for_legacy_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product, layout, task_root, paths = self.fixture(Path(tmp))
            (task_root / "result.json").write_text("{", encoding="utf-8")
            receipt = {
                "task_id": "T1", "work_item_id": "WI-42", "decision": "pass",
                "reviewer": "qa-evaluator", "structure_gate": "pass",
                "paths_reviewed": paths,
            }
            path = task_root / "qa_approved_T1.json"
            path.write_text(json.dumps(receipt))
            issues = self.validate_qa(path, layout)
        self.assertTrue(any(item.startswith("QA_RESULT_INVALID:") for item in issues))
        self.assertTrue(any(item.startswith("QA_V2_RECEIPT_REQUIRED:") for item in issues))

    def test_invalid_implementer_session_shape_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product, layout, task_root, paths = self.fixture(Path(tmp))
            result = json.loads((task_root / "result.json").read_text())
            result["cost"]["story_usage_baseline"] = "not-an-object"
            atomic_write_result(task_root / "result.json", result)
            receipt = {
                "task_id": "T1", "work_item_id": "WI-42", "decision": "pass",
                "reviewer": "qa-evaluator", "structure_gate": "pass",
                "paths_reviewed": paths, "schema": "harness-qa-receipt-v2",
            }
            path = task_root / "qa_approved_T1.json"
            path.write_text(json.dumps(receipt))
            issues = self.validate_qa(path, layout)
        self.assertTrue(any(item.startswith("QA_IMPLEMENTER_IDENTITY_UNPROVEN:") for item in issues))

    def test_lite_task_keeps_legacy_receipt_compatibility(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product, layout, task_root, paths = self.fixture(Path(tmp))
            result = json.loads((task_root / "result.json").read_text())
            result["tier"] = {"initial": "lite", "effective": "lite"}
            atomic_write_result(task_root / "result.json", result)
            receipt = {
                "task_id": "T1", "work_item_id": "WI-42", "decision": "pass",
                "reviewer": "qa-evaluator", "structure_gate": "pass",
                "paths_reviewed": paths,
            }
            path = task_root / "qa_approved_T1.json"
            path.write_text(json.dumps(receipt))
            issues = self.validate_qa(path, layout)
        self.assertEqual(issues, [])

    def test_current_result_implementer_session_is_authoritative(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product, layout, task_root, paths = self.fixture(Path(tmp))
            prepare_bundle(
                ROOT, product, "WI-42", paths,
                lambda _harness, _product: {"decision": "pass"},
            )
            binding = self.receipt_binding(product, paths)
            receipt = {
                "task_id": "T1", "work_item_id": "WI-42", "decision": "pass",
                "reviewer": "qa-evaluator", "structure_gate": "pass",
                "paths_reviewed": paths, **binding["binding"],
            }
            path = task_root / "qa_approved_T1.json"
            path.write_text(json.dumps(receipt))
            result = json.loads((task_root / "result.json").read_text())
            result["cost"]["story_usage_baseline"]["session_id"] = "new-implementer"
            atomic_write_result(task_root / "result.json", result)
            issues = self.validate_qa(path, layout)
        self.assertTrue(any(item.startswith("QA_IMPLEMENTER_SESSION_MISMATCH:") for item in issues))

    def test_tampered_host_reviewer_identity_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product, layout, task_root, paths = self.fixture(Path(tmp))
            prepare_bundle(
                ROOT, product, "WI-42", paths,
                lambda _harness, _product: {"decision": "pass"},
            )
            binding = self.receipt_binding(product, paths)
            receipt = {
                "task_id": "T1", "work_item_id": "WI-42", "decision": "pass",
                "reviewer": "qa-evaluator", "structure_gate": "pass",
                "paths_reviewed": paths, **binding["binding"],
            }
            receipt["reviewer_identity_receipt"]["claims"]["session_id"] = "forged"
            path = task_root / "qa_approved_T1.json"
            path.write_text(json.dumps(receipt))
            issues = self.validate_qa(path, layout)
        self.assertTrue(any(item.startswith("QA_REVIEWER_IDENTITY_RECEIPT_INVALID:") for item in issues))

    def test_non_string_host_reviewer_identity_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product, layout, task_root, paths = self.fixture(Path(tmp))
            prepare_bundle(
                ROOT, product, "WI-42", paths,
                lambda _harness, _product: {"decision": "pass"},
            )
            binding = self.receipt_binding(product, paths)
            receipt = {
                "task_id": "T1", "work_item_id": "WI-42", "decision": "pass",
                "reviewer": "qa-evaluator", "structure_gate": "pass",
                "paths_reviewed": paths, **binding["binding"],
            }
            identity = receipt["reviewer_identity_receipt"]
            identity["claims"]["session_id"] = ["forged"]
            path = task_root / "qa_approved_T1.json"
            path.write_text(json.dumps(receipt))
            issues = self.validate_qa(path, layout)
        self.assertTrue(any(item.startswith("QA_REVIEWER_IDENTITY_RECEIPT_INVALID:") for item in issues))

    def test_bundle_prepare_is_singleflight_with_locked_recheck(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product, _layout, _task_root, paths = self.fixture(Path(tmp))
            calls = 0

            def runner(_harness, _product):
                nonlocal calls
                calls += 1
                time.sleep(0.05)
                return {"decision": "pass"}

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(
                    lambda _index: prepare_bundle(ROOT, product, "WI-42", paths, runner),
                    range(2),
                ))
        self.assertEqual(calls, 1)
        self.assertEqual(
            sorted(result["reason"] for result in results),
            ["QA_BUNDLE_PREPARED", "QA_BUNDLE_REUSED"],
        )


if __name__ == "__main__":
    unittest.main()
