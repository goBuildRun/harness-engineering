#!/usr/bin/env python3
from __future__ import annotations

from copy import deepcopy
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".ael" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import ael_cycle_release  # noqa: E402
import ael_cycle_stage_commands  # noqa: E402
import provider_attempt  # noqa: E402
from ael_attestation import create_attestation  # noqa: E402
from ael_candidate import (  # noqa: E402
    _snapshot_digest,
    bound_candidate,
    build_snapshot,
    committed_candidate,
    refresh_evidence,
    release_evidence_paths,
)
from ael_gate_inputs import gate_input_digest  # noqa: E402
from ael_runtime import result_path, subject_for, task_operation_lock  # noqa: E402


def candidate_binding(snapshot: dict) -> dict:
    return {
        "schema": snapshot["schema"],
        "digest": snapshot["candidate_digest"],
        "paths": snapshot["candidate_paths"],
        "baseline_commit": snapshot["baseline_commit"],
        "snapshot_digest": snapshot["snapshot_digest"],
        "snapshot": "candidate-snapshot.json",
        "frozen_at": snapshot["captured_at"],
    }


class CandidateIntegrityTest(unittest.TestCase):
    def repo(self, root: Path) -> str:
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(
            ["git", "config", "user.email", "harness@example.invalid"],
            cwd=root, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Harness Test"], cwd=root, check=True,
        )
        (root / "base.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True,
        ).strip()

    def test_release_evidence_excludes_mutable_runtime_control_records(self) -> None:
        result_ref = "ael-workspace/runs/tasks/task-1/result.json"
        changed = [
            "ael-workspace/evidence/qa.json",
            "ael-workspace/runs/tasks/task-1/phase-handoff.json",
            "ael-workspace/runs/tasks/task-1/wall-clock-ledger.jsonl",
            result_ref,
        ]

        self.assertEqual(
            release_evidence_paths(changed, result_ref),
            changed[:2],
        )

    def test_candidate_uses_subject_for_and_bound_snapshot_stays_current(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            baseline = self.repo(repo)
            (repo / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
            snapshot = build_snapshot(
                repo, ["feature.py"], baseline_commit=baseline,
            )
            binding = candidate_binding(snapshot)

            self.assertEqual(
                snapshot["candidate_digest"], subject_for(repo, ["feature.py"]),
            )
            self.assertEqual(
                bound_candidate(repo, snapshot, binding, ["feature.py"])["decision"],
                "pass",
            )

            tampered = deepcopy(snapshot)
            tampered["baseline_commit"] = "f" * 40
            tampered["snapshot_digest"] = _snapshot_digest(tampered)
            self.assertEqual(
                bound_candidate(repo, tampered, binding, ["feature.py"])["reason"],
                "CANDIDATE_RESULT_BINDING_MISMATCH",
            )

    def test_candidate_digest_is_recomputed_from_frozen_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            baseline = self.repo(repo)
            (repo / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
            snapshot = build_snapshot(repo, ["feature.py"], baseline_commit=baseline)
            binding = candidate_binding(snapshot)
            snapshot["candidate_digest"] = "0" * 64
            snapshot["snapshot_digest"] = _snapshot_digest(snapshot)
            binding.update({
                "digest": snapshot["candidate_digest"],
                "snapshot_digest": snapshot["snapshot_digest"],
            })

            outcome = bound_candidate(repo, snapshot, binding, ["feature.py"])

        self.assertEqual(outcome["reason"], "CANDIDATE_SNAPSHOT_INVALID")

    def test_snapshot_capture_time_is_digest_and_result_bound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            baseline = self.repo(repo)
            (repo / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
            snapshot = build_snapshot(repo, ["feature.py"], baseline_commit=baseline)
            binding = candidate_binding(snapshot)

            tampered = deepcopy(snapshot)
            tampered["captured_at"] = "2099-01-01T00:00:00Z"
            self.assertEqual(
                bound_candidate(repo, tampered, binding, ["feature.py"])["reason"],
                "CANDIDATE_SNAPSHOT_DIGEST_MISMATCH",
            )

            tampered["snapshot_digest"] = _snapshot_digest(tampered)
            self.assertEqual(
                bound_candidate(repo, tampered, binding, ["feature.py"])["reason"],
                "CANDIDATE_RESULT_BINDING_MISMATCH",
            )

    def test_commit_requires_exact_paths_content_and_modes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            baseline = self.repo(repo)
            (repo / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
            snapshot = build_snapshot(repo, ["feature.py"], baseline_commit=baseline)
            binding = candidate_binding(snapshot)
            subprocess.run(["git", "add", "feature.py"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "candidate"], cwd=repo, check=True)

            outcome = committed_candidate(repo, snapshot, binding, "HEAD")
            self.assertEqual(outcome["decision"], "pass", outcome)

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            baseline = self.repo(repo)
            (repo / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
            snapshot = build_snapshot(repo, ["feature.py"], baseline_commit=baseline)
            binding = candidate_binding(snapshot)
            (repo / "extra.py").write_text("EXTRA = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "feature.py", "extra.py"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "extra"], cwd=repo, check=True)

            outcome = committed_candidate(repo, snapshot, binding, "HEAD")
            self.assertEqual(outcome["reason"], "CANDIDATE_COMMIT_PATHS_MISMATCH")

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            baseline = self.repo(repo)
            feature = repo / "feature.py"
            feature.write_text("VALUE = 1\n", encoding="utf-8")
            snapshot = build_snapshot(repo, ["feature.py"], baseline_commit=baseline)
            binding = candidate_binding(snapshot)
            feature.chmod(0o755)
            subprocess.run(["git", "add", "feature.py"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "mode"], cwd=repo, check=True)

            outcome = committed_candidate(repo, snapshot, binding, "HEAD")
            self.assertEqual(outcome["reason"], "CANDIDATE_COMMIT_MISMATCH")

    def test_commit_allows_only_snapshot_declared_evidence_paths_beside_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            baseline = self.repo(repo)
            feature = repo / "feature.py"
            evidence = repo / "ael-workspace/evidence/qa.json"
            feature.write_text("VALUE = 1\n", encoding="utf-8")
            evidence.parent.mkdir(parents=True)
            evidence.write_text('{"decision":"pass"}\n', encoding="utf-8")
            snapshot = build_snapshot(
                repo, ["feature.py", "ael-workspace/evidence/qa.json"],
                baseline_commit=baseline,
            )
            binding = candidate_binding(snapshot)
            subprocess.run(
                ["git", "add", "feature.py", "ael-workspace/evidence/qa.json"],
                cwd=repo, check=True,
            )
            subprocess.run(["git", "commit", "-qm", "candidate+evidence"], cwd=repo, check=True)

            outcome = committed_candidate(repo, snapshot, binding, "HEAD")

        self.assertEqual(outcome["decision"], "pass", outcome)

    def test_commit_rejects_undeclared_evidence_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            baseline = self.repo(repo)
            (repo / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
            snapshot = build_snapshot(repo, ["feature.py"], baseline_commit=baseline)
            binding = candidate_binding(snapshot)
            extra = repo / "ael-workspace/evidence/undeclared.json"
            extra.parent.mkdir(parents=True)
            extra.write_text('{"decision":"pass"}\n', encoding="utf-8")
            subprocess.run(
                ["git", "add", "feature.py", str(extra.relative_to(repo))],
                cwd=repo, check=True,
            )
            subprocess.run(["git", "commit", "-qm", "extra evidence"], cwd=repo, check=True)

            outcome = committed_candidate(repo, snapshot, binding, "HEAD")

        self.assertEqual(outcome["reason"], "CANDIDATE_COMMIT_PATHS_MISMATCH")

    def test_commit_evidence_bytes_must_match_attested_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            baseline = self.repo(repo)
            feature = repo / "feature.py"
            evidence = repo / "ael-workspace/evidence/qa.json"
            feature.write_text("VALUE = 1\n", encoding="utf-8")
            evidence.parent.mkdir(parents=True)
            evidence.write_text('{"decision":"pass","review":"A"}\n', encoding="utf-8")
            snapshot = build_snapshot(
                repo, ["feature.py", "ael-workspace/evidence/qa.json"],
                baseline_commit=baseline,
            )
            binding = candidate_binding(snapshot)
            attested_manifest = refresh_evidence(
                repo, ["ael-workspace/evidence/qa.json"],
            )
            evidence.write_text(
                '{"decision":"pass","review":"forged-B"}\n', encoding="utf-8",
            )
            subprocess.run(
                ["git", "add", "feature.py", "ael-workspace/evidence/qa.json"],
                cwd=repo, check=True,
            )
            subprocess.run(["git", "commit", "-qm", "forged evidence"], cwd=repo, check=True)

            outcome = committed_candidate(
                repo, snapshot, binding, "HEAD",
                evidence_manifest=attested_manifest,
            )

        self.assertEqual(outcome["reason"], "CANDIDATE_COMMIT_EVIDENCE_MISMATCH")

    def test_release_rechecks_commit_evidence_from_attested_result(self) -> None:
        product = Path("/tmp/e4-release-binding")
        snapshot = {"snapshot": True}
        manifest = {
            "evidence_paths": ["ael-workspace/evidence/qa.json"],
            "evidence_entries": [{"path": "ael-workspace/evidence/qa.json"}],
            "evidence_digest": "attested-evidence",
        }
        result = {
            "candidate": {"digest": "candidate"}, "policy_digest": "policy",
            "evidence_manifest": {**manifest, "evidence_digest": "live-caller"},
        }
        attested = {**result, "evidence_manifest": manifest}
        with (
            mock.patch.object(
                ael_cycle_release, "load_candidate_snapshot", return_value=snapshot,
            ),
            mock.patch.object(
                ael_cycle_release, "committed_candidate",
                side_effect=[
                    {"decision": "pass", "commit": "a" * 40},
                    {"decision": "block", "reason": "CANDIDATE_COMMIT_EVIDENCE_MISMATCH"},
                ],
            ) as committed,
            mock.patch.object(
                ael_cycle_release, "verify_attestation",
                return_value={"decision": "pass", "result": attested},
            ),
            mock.patch.object(
                ael_cycle_release, "_attestation_result_status",
                return_value={"decision": "pass", "reason": "ATTESTATION_RESULT_BOUND"},
            ),
            mock.patch.object(
                ael_cycle_release, "validate_current_release_evidence",
            ) as live_evidence,
        ):
            outcome = ael_cycle_release._guarded_release_integrity(
                product, product / "result.json", "task-1", result, "HEAD",
            )

        self.assertEqual(outcome["reason"], "CANDIDATE_COMMIT_EVIDENCE_MISMATCH")
        self.assertEqual(committed.call_count, 2)
        self.assertIsNone(committed.call_args_list[0].kwargs["evidence_manifest"])
        self.assertIs(committed.call_args_list[1].kwargs["evidence_manifest"], manifest)
        live_evidence.assert_not_called()

    def test_candidate_digest_is_compatible_with_unchanged_attestation_creator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            baseline = self.repo(repo)
            (repo / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
            snapshot = build_snapshot(repo, ["feature.py"], baseline_commit=baseline)
            subprocess.run(["git", "add", "feature.py"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "candidate"], cwd=repo, check=True)
            result = {
                "task_id": "task-1",
                "decision": "pass",
                "state": "validated",
                "policy_digest": "policy-1",
                "subject": {
                    "kind": "worktree",
                    "digest": snapshot["candidate_digest"],
                    "paths": snapshot["candidate_paths"],
                },
            }

            outcome = create_attestation(repo, result)

        self.assertEqual(outcome["decision"], "pass", outcome)

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            baseline = self.repo(repo)
            feature = repo / "feature.py"
            feature.write_text("VALUE = 1\n", encoding="utf-8")
            snapshot = build_snapshot(repo, ["feature.py"], baseline_commit=baseline)
            binding = candidate_binding(snapshot)
            feature.unlink()
            feature.symlink_to("base.txt")
            subprocess.run(["git", "add", "feature.py"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "type"], cwd=repo, check=True)

            outcome = committed_candidate(repo, snapshot, binding, "HEAD")
            self.assertEqual(outcome["reason"], "CANDIDATE_COMMIT_MISMATCH")


class StageEvidenceTest(unittest.TestCase):
    def args(self, product: Path, *, action: str, stage: str) -> SimpleNamespace:
        return SimpleNamespace(
            product_root=str(product), task_id="task-1", action=action, stage=stage,
            decision="pass", reason="STAGE_COMPLETED", tool_wait_ms="unknown",
        )

    def test_qa_pass_and_deploy_start_block_without_current_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            path = result_path(product, "task-1")
            path.parent.mkdir(parents=True)
            path.write_text("{}\n", encoding="utf-8")
            active = {"cycle": {"current_stage": "independent_qa"}}
            qa_block = {"decision": "block", "reason": "QA_EVIDENCE_INVALID"}
            with (
                mock.patch.object(ael_cycle_stage_commands, "load_result", return_value=active),
                mock.patch.object(ael_cycle_stage_commands, "atomic_write_result"),
                mock.patch.object(ael_cycle_stage_commands, "dump_json"),
                mock.patch.object(
                    ael_cycle_stage_commands, "validate_current_qa_evidence",
                    return_value=qa_block,
                ),
                mock.patch.object(ael_cycle_stage_commands, "finish_stage") as finish,
            ):
                ael_cycle_stage_commands.cmd_stage(
                    self.args(product, action="end", stage="independent_qa"),
                )
            finish.assert_not_called()
            self.assertEqual(active["cycle"]["current_stage"], "independent_qa")

            deploy_state = {"candidate": {"digest": "subject"}, "cycle": {}}
            with (
                mock.patch.object(
                    ael_cycle_stage_commands, "load_result", return_value=deploy_state,
                ),
                mock.patch.object(ael_cycle_stage_commands, "atomic_write_result"),
                mock.patch.object(ael_cycle_stage_commands, "dump_json"),
                mock.patch.object(
                    ael_cycle_stage_commands, "load_candidate_snapshot",
                    return_value={"snapshot": True},
                ),
                mock.patch.object(
                    ael_cycle_stage_commands, "changed_since_baseline", return_value=[],
                ),
                mock.patch.object(
                    ael_cycle_stage_commands, "bound_candidate",
                    return_value={"decision": "pass", "candidate_digest": "subject"},
                ),
                mock.patch.object(
                    ael_cycle_stage_commands, "validate_current_qa_evidence",
                    return_value=qa_block,
                ) as qa_check,
                mock.patch.object(ael_cycle_stage_commands, "begin_stage") as begin,
            ):
                ael_cycle_stage_commands.cmd_stage(
                    self.args(product, action="start", stage="deploy_provider"),
                )
            begin.assert_not_called()
            qa_check.assert_called_once_with(product.resolve(), "task-1", deploy_state)

    def test_governed_qa_receipts_bind_the_frozen_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            qa = product / "ael-workspace/runs/tasks/task-1/qa_approved_T1.json"
            qa.parent.mkdir(parents=True)
            (product / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
            snapshot = build_snapshot(product, ["feature.py"], baseline_commit="a" * 40)
            qa.write_text(json.dumps({
                "paths_reviewed": ["feature.py"],
                "subject_digest": "legacy-full-subject",
                "candidate_paths_reviewed": ["feature.py"],
                "candidate_subject_digest": "legacy-full-subject",
                "candidate_snapshot_digest": snapshot["snapshot_digest"],
            }), encoding="utf-8")
            result = {"task_id": "task-1", "candidate": candidate_binding(snapshot)}
            payload = {
                "decision": "pass",
                "checked": [{"task_id": "T1", "qa": str(qa.relative_to(product))}],
                "task_count": 1,
                "candidate": {
                    "subject_digest": snapshot["candidate_digest"],
                    "snapshot_digest": snapshot["snapshot_digest"],
                },
            }

            outcome = ael_cycle_stage_commands.validate_qa_candidate_binding(
                product, result, payload, snapshot=snapshot,
            )

        self.assertEqual(outcome["decision"], "block", outcome)
        self.assertEqual(outcome["reason"], "QA_CANDIDATE_SUBJECT_MISMATCH")

    def test_deploy_start_freezes_current_release_evidence_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            path = result_path(product, "task-1")
            path.parent.mkdir(parents=True)
            path.write_text("{}\n", encoding="utf-8")
            result = {
                "tier": {"effective": "strict"},
                "candidate": {"digest": "subject"},
                "cycle": {},
            }
            manifest = {"evidence_digest": "evidence", "evidence_paths": []}
            initial_paths = ["ael-workspace/evidence/qa.json"]
            handoff_path = "ael-workspace/runs/tasks/task-1/phase-handoff.json"
            result_ref = "ael-workspace/runs/tasks/task-1/result.json"
            refresh = mock.Mock(return_value=manifest)
            with (
                mock.patch.object(
                    ael_cycle_stage_commands, "load_result", return_value=result,
                ),
                mock.patch.object(ael_cycle_stage_commands, "atomic_write_result"),
                mock.patch.object(ael_cycle_stage_commands, "dump_json"),
                mock.patch.object(
                    ael_cycle_stage_commands, "load_candidate_snapshot",
                    return_value={"snapshot": True},
                ),
                mock.patch.object(
                    ael_cycle_stage_commands, "changed_since_baseline",
                    side_effect=[
                        [*initial_paths, result_ref],
                        [*initial_paths, handoff_path, result_ref],
                    ],
                ),
                mock.patch.object(
                    ael_cycle_stage_commands, "bound_candidate",
                    return_value={"decision": "pass", "candidate_digest": "subject"},
                ),
                mock.patch.object(
                    ael_cycle_stage_commands, "validate_current_qa_evidence",
                    return_value={"decision": "pass", "reason": "QA_EVIDENCE_CURRENT"},
                ),
                mock.patch.object(
                    ael_cycle_stage_commands, "refresh_evidence", refresh,
                ),
                mock.patch.object(
                    ael_cycle_stage_commands, "begin_stage",
                    return_value={"decision": "pass", "reason": "STAGE_STARTED"},
                ),
                mock.patch.object(
                    ael_cycle_stage_commands, "write_phase_handoff", return_value={},
                ),
            ):
                ael_cycle_stage_commands.cmd_stage(
                    self.args(product, action="start", stage="deploy_provider"),
                )

        self.assertEqual(result["evidence_manifest"], manifest)
        refresh.assert_called_once_with(product.resolve(), [*initial_paths, handoff_path])

    def test_mutable_result_record_does_not_stale_release_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            path = result_path(product, "task-1")
            path.parent.mkdir(parents=True)
            path.write_text("{}\n", encoding="utf-8")
            evidence_ref = "ael-workspace/evidence/qa.json"
            result_ref = "ael-workspace/runs/tasks/task-1/result.json"
            manifest = {"evidence_digest": "current", "evidence_paths": [evidence_ref]}
            result = {
                "task_id": "task-1",
                "tier": {"effective": "lite"},
                "candidate": {"digest": "subject"},
                "cycle": {},
                "evidence_manifest": manifest,
            }

            def refresh(_product: Path, paths: list[str]) -> dict:
                return manifest if paths == [evidence_ref] else {
                    "evidence_digest": "self-referential", "evidence_paths": paths,
                }

            with (
                mock.patch.object(
                    ael_cycle_stage_commands, "changed_since_baseline",
                    return_value=[evidence_ref, result_ref],
                ),
                mock.patch.object(
                    ael_cycle_stage_commands, "refresh_evidence", side_effect=refresh,
                ),
            ):
                outcome = ael_cycle_stage_commands.validate_current_release_evidence(
                    product, "task-1", result,
                )

        self.assertEqual(outcome["decision"], "pass", outcome)

    def test_qa_receipt_and_aggregate_require_snapshot_binding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            qa = product / "qa.json"
            (product / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
            snapshot = build_snapshot(product, ["feature.py"], baseline_commit="a" * 40)
            qa.write_text(json.dumps({
                "candidate_paths_reviewed": ["feature.py"],
                "candidate_subject_digest": snapshot["candidate_digest"],
            }), encoding="utf-8")
            result = {"task_id": "task-1", "candidate": candidate_binding(snapshot)}
            payload = {
                "decision": "pass", "checked": [{"task_id": "T1", "qa": "qa.json"}],
                "task_count": 1, "candidate": {
                    "subject_digest": snapshot["candidate_digest"],
                    "snapshot_digest": snapshot["snapshot_digest"],
                },
            }

            missing_receipt = ael_cycle_stage_commands.validate_qa_candidate_binding(
                product, result, payload, snapshot=snapshot,
            )
            qa.write_text(json.dumps({
                "candidate_paths_reviewed": ["feature.py"],
                "candidate_subject_digest": snapshot["candidate_digest"],
                "candidate_snapshot_digest": snapshot["snapshot_digest"],
            }), encoding="utf-8")
            missing_aggregate = ael_cycle_stage_commands.validate_qa_candidate_binding(
                product, result, {**payload, "candidate": {}}, snapshot=snapshot,
            )

        self.assertEqual(missing_receipt["reason"], "QA_CANDIDATE_SNAPSHOT_MISMATCH")
        self.assertEqual(missing_aggregate["reason"], "QA_CANDIDATE_SNAPSHOT_MISMATCH")

    def test_qa_binding_rechecks_paths_after_checker_returns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            qa = product / "qa.json"
            (product / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
            (product / "late.py").write_text("VALUE = 2\n", encoding="utf-8")
            snapshot = build_snapshot(product, ["feature.py"], baseline_commit="a" * 40)
            qa.write_text(json.dumps({
                "candidate_paths_reviewed": ["feature.py"],
                "candidate_subject_digest": snapshot["candidate_digest"],
                "candidate_snapshot_digest": snapshot["snapshot_digest"],
            }), encoding="utf-8")
            result = {"task_id": "task-1", "candidate": candidate_binding(snapshot)}
            payload = {
                "decision": "pass", "checked": [{"task_id": "T1", "qa": "qa.json"}],
                "task_count": 1, "candidate": {
                    "subject_digest": snapshot["candidate_digest"],
                    "snapshot_digest": snapshot["snapshot_digest"],
                },
            }

            outcome = ael_cycle_stage_commands.validate_qa_candidate_binding(
                product, result, payload, snapshot=snapshot,
                changed_paths=["feature.py", "late.py"],
            )

        self.assertEqual(outcome["reason"], "QA_CANDIDATE_STALE")

    def test_repeated_qa_start_does_not_rewrite_frozen_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            path = result_path(product, "task-1")
            path.parent.mkdir(parents=True)
            path.write_text("{}\n", encoding="utf-8")
            result = {
                "candidate": {"digest": "frozen"},
                "cycle": {
                    "current_stage": "independent_qa",
                    "stages": {
                        "independent_qa": {
                            "status": "active", "wall_ms": 0,
                            "attempts": [{"attempt": 1, "started_epoch_ms": 1}],
                        },
                    },
                },
            }
            with (
                mock.patch.object(ael_cycle_stage_commands, "load_result", return_value=result),
                mock.patch.object(ael_cycle_stage_commands, "atomic_write_result"),
                mock.patch.object(ael_cycle_stage_commands, "dump_json") as emitted,
                mock.patch.object(ael_cycle_stage_commands, "_freeze_candidate") as freeze,
            ):
                ael_cycle_stage_commands.cmd_stage(
                    self.args(product, action="start", stage="independent_qa"),
                )

        freeze.assert_not_called()
        self.assertEqual(result["candidate"]["digest"], "frozen")
        self.assertEqual(emitted.call_args.args[0]["reason"], "STAGE_ALREADY_ACTIVE")

    def test_candidate_freeze_failure_does_not_activate_stage_or_append_span(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            path = result_path(product, "task-1")
            path.parent.mkdir(parents=True)
            path.write_text("{}\n", encoding="utf-8")
            result = {
                "cycle": {
                    "current_stage": "",
                    "stages": {
                        "takeover": {"status": "pass"},
                        "planning": {"status": "pass"},
                        "implementation_test": {"status": "pass"},
                    },
                },
            }
            with (
                mock.patch.object(ael_cycle_stage_commands, "load_result", return_value=result),
                mock.patch.object(ael_cycle_stage_commands, "atomic_write_result"),
                mock.patch.object(ael_cycle_stage_commands, "dump_json") as emitted,
                mock.patch.object(
                    ael_cycle_stage_commands, "_freeze_candidate",
                    side_effect=ValueError("CANDIDATE_BASELINE_INVALID"),
                ),
                mock.patch.object(ael_cycle_stage_commands, "begin_stage") as begin,
            ):
                ael_cycle_stage_commands.cmd_stage(
                    self.args(product, action="start", stage="independent_qa"),
                )

        begin.assert_not_called()
        self.assertEqual(emitted.call_args.args[0]["reason"], "CANDIDATE_BASELINE_INVALID")


class ProviderLockTest(unittest.TestCase):
    def test_authority_required_attempt_rejects_compatibility_preflight_before_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            path = result_path(product, "task-1")
            path.parent.mkdir(parents=True)
            path.write_text("{}\n", encoding="utf-8")
            preflight = product / "preflight.json"
            preflight.write_text(json.dumps({
                "schema": "harness-provider-preflight-receipt-v2",
                "decision": "pass",
            }))
            authority = product / "sandbox-authority.json"
            authority.write_text("{}\n", encoding="utf-8")
            with (
                mock.patch.object(provider_attempt, "load_result", return_value={"cycle": {}}),
                mock.patch.object(provider_attempt, "changed_since_baseline", return_value=[]),
                mock.patch.object(
                    provider_attempt, "load_candidate_snapshot", return_value={"snapshot": True},
                ),
                mock.patch.object(
                    provider_attempt, "bound_candidate",
                    return_value={"decision": "pass", "candidate_digest": "subject"},
                ),
                mock.patch.object(provider_attempt, "run_once") as run_once,
            ):
                outcome = provider_attempt.attempt_for_task(
                    product, "task-1", preflight_path=preflight,
                    expected_subject="subject", provider="mock",
                    adapter_path=product / "adapter", evidence_ref="evidence.json",
                    command=["provider"], timeout_seconds=5,
                    sandbox_authority_path=authority,
                    sandbox_trust=mock.sentinel.sandbox_trust,
                    authority_required=True,
                )
        self.assertEqual(outcome["reason"], "PROVIDER_PREFLIGHT_RECEIPT_FIELDS_INVALID")
        run_once.assert_not_called()

    def test_provider_authority_blocks_before_runner_and_confines_binding_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            path = result_path(product, "task-1")
            path.parent.mkdir(parents=True)
            path.write_text("{}\n", encoding="utf-8")
            preflight = product / "preflight.json"
            preflight.write_text("{}\n", encoding="utf-8")
            authority = product / "sandbox-authority.json"
            authority.write_text("{}\n", encoding="utf-8")
            common = (
                mock.patch.object(provider_attempt, "load_result", return_value={"cycle": {}}),
                mock.patch.object(provider_attempt, "changed_since_baseline", return_value=[]),
                mock.patch.object(
                    provider_attempt, "load_candidate_snapshot", return_value={"snapshot": True},
                ),
                mock.patch.object(
                    provider_attempt, "bound_candidate",
                    return_value={"decision": "pass", "candidate_digest": "subject"},
                ),
                mock.patch.object(provider_attempt, "run_once"),
            )
            with common[0], common[1], common[2], common[3], common[4] as run_once:
                missing = provider_attempt.attempt_for_task(
                    product, "task-1", preflight_path=preflight,
                    expected_subject="subject", provider="mock",
                    adapter_path=product / "adapter", evidence_ref="evidence.json",
                    command=["provider"], timeout_seconds=5, authority_required=True,
                )
            self.assertEqual(missing["reason"], "PROVIDER_SANDBOX_AUTHORITY_REQUIRED")
            run_once.assert_not_called()

            common = (
                mock.patch.object(provider_attempt, "load_result", return_value={"cycle": {}}),
                mock.patch.object(provider_attempt, "changed_since_baseline", return_value=[]),
                mock.patch.object(
                    provider_attempt, "load_candidate_snapshot", return_value={"snapshot": True},
                ),
                mock.patch.object(
                    provider_attempt, "bound_candidate",
                    return_value={"decision": "pass", "candidate_digest": "subject"},
                ),
                mock.patch.object(provider_attempt, "run_once"),
            )
            with common[0], common[1], common[2], common[3], common[4] as run_once, \
                    mock.patch.object(provider_attempt, "validate_canonical_preflight", return_value={
                        "decision": "pass", "reason": "PROVIDER_PREFLIGHT_RECEIPT_OK",
                    }), \
                    mock.patch.object(provider_attempt, "validate_sandbox_authority", return_value={
                        "decision": "block", "reason": "EXECUTION_AUTHORITY_SIGNER_MISMATCH",
                    }):
                forged = provider_attempt.attempt_for_task(
                    product, "task-1", preflight_path=preflight,
                    expected_subject="subject", provider="mock",
                    adapter_path=product / "adapter", evidence_ref="evidence.json",
                    command=["provider"], timeout_seconds=5,
                    sandbox_authority_path=authority,
                    sandbox_trust=mock.sentinel.sandbox_trust,
                    authority_required=True,
                )
            self.assertEqual(forged["reason"], "EXECUTION_AUTHORITY_SIGNER_MISMATCH")
            run_once.assert_not_called()

            common = (
                mock.patch.object(provider_attempt, "load_result", return_value={"cycle": {}}),
                mock.patch.object(provider_attempt, "changed_since_baseline", return_value=[]),
                mock.patch.object(
                    provider_attempt, "load_candidate_snapshot", return_value={"snapshot": True},
                ),
                mock.patch.object(
                    provider_attempt, "bound_candidate",
                    return_value={"decision": "pass", "candidate_digest": "subject"},
                ),
                mock.patch.object(provider_attempt, "run_once"),
            )
            outside = product / "outside-binding.json"
            with common[0], common[1], common[2], common[3], common[4] as run_once:
                escaped = provider_attempt.attempt_for_task(
                    product, "task-1", preflight_path=preflight,
                    expected_subject="subject", provider="mock",
                    adapter_path=product / "adapter", evidence_ref="evidence.json",
                    command=["provider"], timeout_seconds=5,
                    sandbox_authority_path=authority,
                    sandbox_trust=mock.sentinel.sandbox_trust,
                    authority_binding_path=outside,
                    authority_required=True,
                )
            self.assertEqual(escaped["reason"], "PROVIDER_AUTHORITY_BINDING_PATH_INVALID")
            self.assertFalse(outside.exists())
            run_once.assert_not_called()

    def test_stale_qa_blocks_before_provider_attempt_is_claimed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            path = result_path(product, "task-1")
            path.parent.mkdir(parents=True)
            path.write_text("{}\n", encoding="utf-8")
            preflight = product / "preflight.json"
            preflight.write_text("{}\n", encoding="utf-8")
            with (
                mock.patch.object(provider_attempt, "load_result", return_value={"cycle": {}}),
                mock.patch.object(provider_attempt, "changed_since_baseline", return_value=[]),
                mock.patch.object(
                    provider_attempt, "load_candidate_snapshot", return_value={"snapshot": True},
                ),
                mock.patch.object(
                    provider_attempt, "bound_candidate",
                    return_value={"decision": "pass", "candidate_digest": "subject"},
                ),
                mock.patch.object(
                    provider_attempt, "_provider_readiness",
                    return_value=({"decision": "pass", "reason": "PROVIDER_READY"}, 1.0),
                ),
                mock.patch.object(
                    provider_attempt, "validate_current_release_evidence", create=True,
                    return_value={"decision": "block", "reason": "QA_EVIDENCE_STALE"},
                ),
                mock.patch.object(provider_attempt, "run_once") as run_once,
            ):
                outcome = provider_attempt.attempt_for_task(
                    product, "task-1", preflight_path=preflight,
                    expected_subject="subject", provider="mock",
                    adapter_path=product / "adapter", evidence_ref="evidence.json",
                    command=["provider"], timeout_seconds=5,
                )

        self.assertEqual(outcome["reason"], "QA_EVIDENCE_STALE")
        run_once.assert_not_called()

    def test_authorization_hashing_is_charged_before_provider_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            path = result_path(product, "task-1")
            path.parent.mkdir(parents=True)
            path.write_text("{}\n", encoding="utf-8")
            preflight = product / "preflight.json"
            preflight.write_text("{}\n", encoding="utf-8")
            clock = [0]

            def digest(_result):
                clock[0] = 2
                return "digest"

            def readiness(_result, _timeout):
                if clock[0] >= 2:
                    return {"decision": "block", "reason": "PROVIDER_DEADLINE_EXCEEDED"}, 0
                return {"decision": "pass", "reason": "PROVIDER_READY"}, 1

            with (
                mock.patch.object(provider_attempt, "load_result", return_value={"cycle": {}}),
                mock.patch.object(provider_attempt, "changed_since_baseline", return_value=[]),
                mock.patch.object(
                    provider_attempt, "load_candidate_snapshot", return_value={"snapshot": True},
                ),
                mock.patch.object(
                    provider_attempt, "bound_candidate",
                    return_value={"decision": "pass", "candidate_digest": "subject"},
                ),
                mock.patch.object(provider_attempt, "authorization_digest", side_effect=digest),
                mock.patch.object(provider_attempt, "_provider_readiness", side_effect=readiness),
                mock.patch.object(
                    provider_attempt, "validate_current_release_evidence", create=True,
                    return_value={"decision": "pass", "reason": "RELEASE_EVIDENCE_CURRENT"},
                ),
                mock.patch.object(provider_attempt, "run_once") as run_once,
            ):
                outcome = provider_attempt.attempt_for_task(
                    product, "task-1", preflight_path=preflight,
                    expected_subject="subject", provider="mock",
                    adapter_path=product / "adapter", evidence_ref="evidence.json",
                    command=["provider"], timeout_seconds=5,
                )

        self.assertEqual(outcome["reason"], "PROVIDER_DEADLINE_EXCEEDED")
        run_once.assert_not_called()

    def test_provider_rechecks_under_lock_and_blocks_concurrent_stage_close(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            path = result_path(product, "task-1")
            path.parent.mkdir(parents=True)
            path.write_text("{}\n", encoding="utf-8")
            preflight = product / "preflight.json"
            preflight.write_text("{}\n", encoding="utf-8")
            entered = threading.Event()
            release = threading.Event()
            acquired = threading.Event()
            result_holder: list[dict] = []

            def locked_run_once(*_args, **_kwargs):
                entered.set()
                release.wait(2)
                return {"decision": "pass", "reason": "PROVIDER_ATTEMPT_OK"}

            patches = (
                mock.patch.object(provider_attempt, "load_result", return_value={"cycle": {}}),
                mock.patch.object(provider_attempt, "changed_since_baseline", return_value=[]),
                mock.patch.object(
                    provider_attempt, "load_candidate_snapshot", return_value={"snapshot": True},
                ),
                mock.patch.object(
                    provider_attempt, "bound_candidate",
                    return_value={"decision": "pass", "candidate_digest": "subject"},
                ),
                mock.patch.object(
                    provider_attempt, "_provider_readiness",
                    return_value=({"decision": "pass", "reason": "PROVIDER_READY"}, 1.0),
                ),
                mock.patch.object(
                    provider_attempt, "validate_current_release_evidence",
                    return_value={"decision": "pass", "reason": "RELEASE_EVIDENCE_CURRENT"},
                ),
                mock.patch.object(provider_attempt, "run_once", side_effect=locked_run_once),
            )
            with (
                patches[0], patches[1], patches[2], patches[3], patches[4],
                patches[5], patches[6],
            ):
                attempt = threading.Thread(target=lambda: result_holder.append(
                    provider_attempt.attempt_for_task(
                        product, "task-1", preflight_path=preflight,
                        expected_subject="subject", provider="mock",
                        adapter_path=product / "adapter", evidence_ref="evidence.json",
                        command=["provider"], timeout_seconds=5,
                    )
                ))
                attempt.start()
                self.assertTrue(entered.wait(1))

                def close_stage() -> None:
                    with task_operation_lock(path):
                        acquired.set()

                closer = threading.Thread(target=close_stage)
                closer.start()
                time.sleep(0.05)
                self.assertFalse(acquired.is_set())
                release.set()
                attempt.join(2)
                closer.join(2)

            self.assertTrue(acquired.is_set())
            self.assertEqual(result_holder[0]["decision"], "pass")

    def test_provider_stage_or_deadline_failure_never_invokes_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            path = result_path(product, "task-1")
            path.parent.mkdir(parents=True)
            path.write_text("{}\n", encoding="utf-8")
            preflight = product / "preflight.json"
            preflight.write_text("{}\n", encoding="utf-8")
            with (
                mock.patch.object(provider_attempt, "load_result", return_value={"cycle": {}}),
                mock.patch.object(provider_attempt, "changed_since_baseline", return_value=[]),
                mock.patch.object(
                    provider_attempt, "load_candidate_snapshot", return_value={"snapshot": True},
                ),
                mock.patch.object(
                    provider_attempt, "bound_candidate",
                    return_value={"decision": "pass", "candidate_digest": "subject"},
                ),
                mock.patch.object(
                    provider_attempt, "_provider_readiness",
                    return_value=({"decision": "block", "reason": "PROVIDER_STAGE_NOT_ACTIVE"}, 0),
                ),
                mock.patch.object(provider_attempt, "run_once") as run_once,
            ):
                outcome = provider_attempt.attempt_for_task(
                    product, "task-1", preflight_path=preflight,
                    expected_subject="subject", provider="mock",
                    adapter_path=product / "adapter", evidence_ref="evidence.json",
                    command=["provider"], timeout_seconds=5,
                )
            self.assertEqual(outcome["reason"], "PROVIDER_STAGE_NOT_ACTIVE")
            run_once.assert_not_called()


class ReleaseAttestationTest(unittest.TestCase):
    def result(self) -> dict:
        return {
            "task_id": "task-1", "decision": "pass", "state": "validated",
            "policy_digest": "policy-1", "candidate": {"digest": "subject"},
            "work_item": {"id": "task-1", "provider": "noop"},
            "cycle": {
                "canonical_finish": {"decision": "pass", "input_digest": "finish"},
            },
        }

    def test_missing_or_mismatched_attestation_blocks_before_readback(self) -> None:
        for reason in ("ATTESTATION_MISSING", "ATTESTATION_POLICY_MISMATCH"):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as tmp:
                product = Path(tmp)
                path = result_path(product, "task-1")
                path.parent.mkdir(parents=True)
                path.write_text("{}\n", encoding="utf-8")
                result = self.result()
                with (
                    mock.patch.object(ael_cycle_release, "load_result", return_value=result),
                    mock.patch.object(ael_cycle_release, "atomic_write_result"),
                    mock.patch.object(ael_cycle_release, "dump_json"),
                    mock.patch.object(
                        ael_cycle_release, "budget_status",
                        return_value={"decision": "pass"},
                    ),
                    mock.patch.object(
                        ael_cycle_release, "stage_budget_status",
                        return_value={"decision": "pass"},
                    ),
                    mock.patch.object(
                        ael_cycle_release, "load_candidate_snapshot",
                        return_value={"snapshot": True},
                    ),
                    mock.patch.object(
                        ael_cycle_release, "committed_candidate",
                        return_value={"decision": "pass", "commit": "a" * 40},
                    ),
                    mock.patch.object(
                        ael_cycle_release, "verify_attestation",
                        return_value={"decision": "block", "reason": reason},
                    ) as verify,
                    mock.patch.object(ael_cycle_release, "validate_readback") as readback,
                ):
                    ael_cycle_release.cmd_release_ready(
                        SimpleNamespace(
                            product_root=str(product), task_id="task-1",
                            commit="commit-1", receipt="",
                        ),
                        refresh_assurance=mock.Mock(),
                    )
                verify.assert_called_once_with(
                    product.resolve(), commit="a" * 40, task_id="task-1",
                    policy_digest="policy-1",
                )
                readback.assert_not_called()

    def test_bound_attestation_allows_normal_readback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            path = result_path(product, "task-1")
            path.parent.mkdir(parents=True)
            path.write_text("{}\n", encoding="utf-8")
            result = self.result()
            refresh = mock.Mock()
            with (
                mock.patch.object(ael_cycle_release, "load_result", return_value=result),
                mock.patch.object(ael_cycle_release, "atomic_write_result"),
                mock.patch.object(ael_cycle_release, "dump_json"),
                mock.patch.object(
                    ael_cycle_release, "budget_status", return_value={"decision": "pass"},
                ),
                mock.patch.object(
                    ael_cycle_release, "stage_budget_status",
                    return_value={"decision": "pass"},
                ),
                mock.patch.object(
                    ael_cycle_release, "load_candidate_snapshot",
                    return_value={"snapshot": True},
                ),
                mock.patch.object(
                    ael_cycle_release, "committed_candidate",
                    return_value={"decision": "pass", "commit": "a" * 40},
                ),
                mock.patch.object(
                    ael_cycle_release, "verify_attestation",
                    return_value={
                        "decision": "pass", "reason": "ATTESTATION_VALID",
                        "result": deepcopy(result),
                    },
                ),
                mock.patch.object(
                    ael_cycle_release, "validate_current_release_evidence",
                    return_value={"decision": "pass", "reason": "RELEASE_EVIDENCE_CURRENT"},
                    create=True,
                ),
                mock.patch.object(
                    ael_cycle_release, "validate_readback",
                    return_value={"decision": "pass"},
                ),
                mock.patch.object(
                    ael_cycle_release, "finish_stage", return_value={"decision": "pass"},
                ),
                mock.patch.object(
                    ael_cycle_release, "close_story_cycle",
                    return_value={"decision": "pass", "reason": "STORY_READY_TO_RELEASE"},
                ),
            ):
                ael_cycle_release.cmd_release_ready(
                    SimpleNamespace(
                        product_root=str(product), task_id="task-1",
                        commit="commit-1", receipt="",
                    ),
                    refresh_assurance=refresh,
                )
            self.assertEqual(result["state"], "ready_to_release")
            refresh.assert_called_once()

    def test_attestation_result_mismatch_blocks_before_lifecycle_readback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            path = result_path(product, "task-1")
            path.parent.mkdir(parents=True)
            path.write_text("{}\n", encoding="utf-8")
            result = self.result()
            attested = deepcopy(result)
            attested["candidate"] = {"digest": "older-subject"}
            emitted = []
            with (
                mock.patch.object(ael_cycle_release, "load_result", return_value=result),
                mock.patch.object(ael_cycle_release, "atomic_write_result"),
                mock.patch.object(ael_cycle_release, "dump_json", side_effect=emitted.append),
                mock.patch.object(ael_cycle_release, "budget_status", return_value={"decision": "pass"}),
                mock.patch.object(ael_cycle_release, "stage_budget_status", return_value={"decision": "pass"}),
                mock.patch.object(ael_cycle_release, "load_candidate_snapshot", return_value={"snapshot": True}),
                mock.patch.object(ael_cycle_release, "committed_candidate", return_value={"decision": "pass", "commit": "a" * 40}),
                mock.patch.object(ael_cycle_release, "verify_attestation", return_value={"decision": "pass", "reason": "ATTESTATION_VALID", "result": attested}),
                mock.patch.object(ael_cycle_release, "validate_readback") as readback,
                mock.patch.object(
                    ael_cycle_release, "validate_current_release_evidence",
                    return_value={"decision": "pass", "reason": "RELEASE_EVIDENCE_CURRENT"},
                    create=True,
                ),
            ):
                ael_cycle_release.cmd_release_ready(
                    SimpleNamespace(product_root=str(product), task_id="task-1", commit="HEAD", receipt=""),
                    refresh_assurance=mock.Mock(),
                )

        self.assertEqual(emitted[-1]["reason"], "ATTESTATION_RESULT_BINDING_MISMATCH")
        readback.assert_not_called()


class ExecutableDependencyTest(unittest.TestCase):
    def test_wrapper_and_argv_file_changes_invalidate_gate_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp)
            scripts = harness / ".ael/scripts"
            scripts.mkdir(parents=True)
            wrapper = scripts / "wrapper"
            payload = scripts / "payload.data"
            wrapper.write_text("#!/usr/bin/env python3\nprint('one')\n", encoding="utf-8")
            payload.write_text("one\n", encoding="utf-8")
            common = {
                "harness": harness, "product": harness, "changed_files": [],
                "planning_gate": harness / "missing.json", "planning_credential": {},
                "command": [str(wrapper), str(payload)],
            }

            before = gate_input_digest("harness", **common)
            wrapper.write_text("#!/usr/bin/env python3\nprint('two')\n", encoding="utf-8")
            wrapper_changed = gate_input_digest("harness", **common)
            payload.write_text("two\n", encoding="utf-8")
            payload_changed = gate_input_digest("harness", **common)

            self.assertNotEqual(before, wrapper_changed)
            self.assertNotEqual(wrapper_changed, payload_changed)


if __name__ == "__main__":
    unittest.main()
