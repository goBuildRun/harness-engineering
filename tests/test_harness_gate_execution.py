#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from harness_gate_execution import (  # noqa: E402
    CandidateManifest, GateInputError, GateSpec, build_spec, execute_specs,
    gate_input_digest,
)


def spec(name: str, *, parallel: bool = True, cacheable: bool = False) -> GateSpec:
    return GateSpec(
        name=name, command=(name,), input_digest=f"input-{name}",
        fingerprint=f"fp-{name}", timeout_seconds=2, budget_ms=2000,
        parallel_safe=parallel, cacheable=cacheable,
    )


class HarnessGateExecutionTest(unittest.TestCase):
    def test_cache_unsafe_gate_builds_non_cacheable_spec(self) -> None:
        built = build_spec(
            "structure", ["true"], input_digest="input", tier="strict",
            configured_timeout=120, remaining_budget_ms=None, cache_safe=False,
        )

        self.assertFalse(built.cacheable)

    def test_read_only_gates_run_in_parallel_but_results_can_be_stably_ordered(self) -> None:
        barrier = threading.Barrier(2)

        def runner(_command: list[str], name: str, _timeout: int):
            barrier.wait(timeout=1)
            time.sleep(0.03 if name == "a" else 0)
            return {"decision": "pass", "reason": f"{name.upper()}_OK"}, 0

        started = time.monotonic()
        checks, hits = execute_specs(
            [spec("a"), spec("b")], runner=runner, previous_checks={},
            subject_digest="subject", policy_digest="policy",
        )
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.20)
        self.assertEqual(list(checks), ["a", "b"])
        self.assertEqual(hits, 0)

    def test_opaque_gate_remains_serial(self) -> None:
        active = 0
        maximum = 0
        lock = threading.Lock()

        def runner(_command: list[str], _name: str, _timeout: int):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.01)
            with lock:
                active -= 1
            return {"decision": "pass", "reason": "OK"}, 0

        execute_specs(
            [spec("quality_lint", parallel=False), spec("quality_test", parallel=False)],
            runner=runner, previous_checks={}, subject_digest="subject", policy_digest="policy",
        )
        self.assertEqual(maximum, 1)

    def test_precise_cache_rebinds_current_subject(self) -> None:
        prior = {
            "structure": {
                "decision": "pass", "source": "executed", "fingerprint": "fp-structure",
                "input_digest": "input-structure", "subject_digest": "old-subject",
                "policy_digest": "policy", "completed_at": "earlier",
            }
        }

        def must_not_run(*_args):
            raise AssertionError("cache hit must not execute")

        checks, hits = execute_specs(
            [spec("structure", cacheable=True)], runner=must_not_run,
            previous_checks=prior, subject_digest="new-subject", policy_digest="policy",
        )
        self.assertEqual(hits, 1)
        self.assertEqual(checks["structure"]["source"], "cache")
        self.assertEqual(checks["structure"]["subject_digest"], "new-subject")
        self.assertEqual(checks["structure"]["original_subject_digest"], "old-subject")

    def test_precise_cache_never_rebinds_across_policy(self) -> None:
        prior = {
            "structure": {
                "decision": "pass", "source": "executed", "fingerprint": "fp-structure",
                "input_digest": "input-structure", "subject_digest": "old-subject",
                "policy_digest": "old-policy", "completed_at": "earlier",
            }
        }
        calls = []

        def runner(_command, name, _timeout):
            calls.append(name)
            return {"decision": "pass", "reason": "OK"}, 0

        checks, hits = execute_specs(
            [spec("structure", cacheable=True)], runner=runner,
            previous_checks=prior, subject_digest="new-subject", policy_digest="new-policy",
        )
        self.assertEqual(hits, 0)
        self.assertEqual(calls, ["structure"])
        self.assertEqual(checks["structure"]["source"], "executed")

    def test_manifest_drift_blocks_cache_reuse_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dependency = root / "dependency.py"
            dependency.write_text("VALUE = 1\n", encoding="utf-8")
            manifest = CandidateManifest(root, root)
            manifest.digest(dependency, root=root)
            dependency.write_text("VALUE = 2\n", encoding="utf-8")
            prior = {"structure": {
                "decision": "pass", "source": "executed",
                "fingerprint": "fp-structure", "input_digest": "input-structure",
                "subject_digest": "old-subject", "policy_digest": "policy",
                "completed_at": "earlier",
            }}

            checks, hits = execute_specs(
                [spec("structure", cacheable=True)],
                runner=lambda *_args: self.fail("drifted input must not execute"),
                previous_checks=prior, subject_digest="subject", policy_digest="policy",
                candidates_manifest=manifest,
            )

        self.assertEqual(hits, 0)
        self.assertEqual(checks["structure"]["reason"], "GATE_INPUT_CHANGED")

    def test_manifest_drift_during_gate_blocks_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dependency = root / "dependency.py"
            dependency.write_text("VALUE = 1\n", encoding="utf-8")
            manifest = CandidateManifest(root, root)
            manifest.digest(dependency, root=root)

            def runner(*_args):
                dependency.write_text("VALUE = 2\n", encoding="utf-8")
                return {"decision": "pass", "reason": "OK"}, 0

            checks, hits = execute_specs(
                [spec("structure")], runner=runner, previous_checks={},
                subject_digest="subject", policy_digest="policy",
                candidates_manifest=manifest,
            )

        self.assertEqual(hits, 0)
        self.assertEqual(checks["structure"]["decision"], "block")
        self.assertEqual(checks["structure"]["reason"], "GATE_INPUT_CHANGED")

    def test_imported_tool_change_invalidates_gate_input_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp)
            scripts = harness / ".harness/scripts"
            scripts.mkdir(parents=True)
            wrapper = scripts / "wrapper.py"
            helper = scripts / "helper.py"
            wrapper.write_text("from helper import value\n", encoding="utf-8")
            helper.write_text("value = 1\n", encoding="utf-8")
            kwargs = {
                "harness": harness, "product": harness, "changed_files": [],
                "planning_gate": harness / "missing.json", "planning_credential": {},
                "command": ["python3", str(wrapper)],
            }
            before = gate_input_digest("harness", **kwargs)
            helper.write_text("value = 2\n", encoding="utf-8")
            after = gate_input_digest("harness", **kwargs)
        self.assertNotEqual(before, after)

    def test_import_from_package_submodule_invalidates_gate_input_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp)
            scripts = harness / ".harness/scripts"
            package = scripts / "local_package"
            package.mkdir(parents=True)
            wrapper = scripts / "wrapper.py"
            helper = package / "helper.py"
            wrapper.write_text("from local_package import helper\n", encoding="utf-8")
            (package / "__init__.py").write_text("", encoding="utf-8")
            helper.write_text("value = 1\n", encoding="utf-8")
            kwargs = {
                "harness": harness, "product": harness, "changed_files": [],
                "planning_gate": harness / "missing.json", "planning_credential": {},
                "command": ["python3", str(wrapper)],
            }
            before = gate_input_digest("harness", **kwargs)
            helper.write_text("value = 2\n", encoding="utf-8")
            after = gate_input_digest("harness", **kwargs)
        self.assertNotEqual(before, after)

    def test_shared_candidate_manifest_reads_changed_source_once_across_gates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            source = product / "src/feature.py"
            source.parent.mkdir()
            source.write_text("value = 1\n", encoding="utf-8")

            class CountingManifest(CandidateManifest):
                source_reads = 0

                def _read(self, path: Path, *, root: Path) -> bytes | None:
                    key = self._key(path)
                    if key == self._key(source) and key not in self._digests:
                        self.source_reads += 1
                    return super()._read(path, root=root)

            candidates = CountingManifest(product, product)
            common = {
                "harness": product, "product": product,
                "changed_files": ["src/feature.py"],
                "planning_gate": product / "missing.json", "planning_credential": {},
                "command": [], "candidates_manifest": candidates,
            }
            gate_input_digest("diff_integrity", **common)
            gate_input_digest("quality_lint", **common)

        self.assertEqual(candidates.source_reads, 1)

    def test_current_task_digests_ignore_bounded_unrelated_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = root / "harness"
            product = root / "product"
            scripts = harness / ".harness/scripts"
            scripts.mkdir(parents=True)
            entry = scripts / "entry.sh"
            entry.write_text("#!/bin/sh\n", encoding="utf-8")
            workspace = product / "harness-workspace"
            workspace.mkdir(parents=True)
            (workspace / "project.yaml").write_text(
                "product:\n  id: demo\nworkspace:\n  root: harness-workspace\n",
                encoding="utf-8",
            )
            task_dir = workspace / "planning/tasks/current-story"
            task_dir.mkdir(parents=True)
            plan = task_dir / "03-实施方案.md"
            plan.write_text("# Current plan\n", encoding="utf-8")
            evidence = workspace / "evidence"
            current_test = evidence / "test-reports/WI-42-T1-TEST.md"
            current_test.parent.mkdir(parents=True)
            current_test.write_text("current test\n", encoding="utf-8")
            current_capture = evidence / "progress/2026-08-22-WI-42-item-GROWTH-CAPTURE.md"
            current_capture.parent.mkdir(parents=True)
            current_capture.write_text("current growth\n", encoding="utf-8")
            current_growth = evidence / "growth-reports/2026-08-22-WI-42-GROWTH.md"
            current_growth.parent.mkdir(parents=True)
            current_growth.write_text("current review\n", encoding="utf-8")
            credential = {
                "task_dir": str(task_dir.relative_to(product)),
                "work_item": {"id": "WI-42"},
            }
            common = {
                "harness": harness, "product": product, "changed_files": [],
                "planning_gate": workspace / "runs/planning_gate_pass.json",
                "planning_credential": credential, "command": ["bash", str(entry)],
                "task_id": "T1", "work_item_id": "WI-42",
            }

            before = {
                name: gate_input_digest(name, **common)
                for name in ("qa_evidence", "growth_release", "knowledge")
            }
            old_task = workspace / "planning/tasks/old-story"
            old_task.mkdir(parents=True)
            (old_task / "notes.md").write_text("historical knowledge\n", encoding="utf-8")
            for index in range(40):
                old_test = evidence / "test-reports" / f"WI-OLD-{index}-TEST.md"
                old_test.write_text(f"historical test {index}\n", encoding="utf-8")
                old_growth = evidence / "growth-reports" / f"2025-01-{index:02d}-WI-OLD-GROWTH.md"
                old_growth.write_text(f"historical growth {index}\n", encoding="utf-8")
            (evidence / "growth-reports/2025-02-01-WI-420-GROWTH.md").write_text(
                "colliding historical growth\n", encoding="utf-8",
            )

            after_history = {
                name: gate_input_digest(name, **common)
                for name in ("qa_evidence", "growth_release", "knowledge")
            }
            current_test.write_text("current test changed\n", encoding="utf-8")
            qa_changed = gate_input_digest("qa_evidence", **common)
            current_capture.write_text("current growth changed\n", encoding="utf-8")
            growth_changed = gate_input_digest("growth_release", **common)
            plan.write_text("# Current plan changed\n", encoding="utf-8")
            knowledge_changed = gate_input_digest("knowledge", **common)

        self.assertEqual(before, after_history)
        self.assertNotEqual(after_history["qa_evidence"], qa_changed)
        self.assertNotEqual(after_history["growth_release"], growth_changed)
        self.assertNotEqual(after_history["knowledge"], knowledge_changed)

    def test_candidate_manifest_fails_closed_on_limits_deadline_and_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            product = root / "product"
            tree = product / "tree"
            tree.mkdir(parents=True)
            (tree / "one.md").write_text("one\n", encoding="utf-8")
            (tree / "two.md").write_text("two\n", encoding="utf-8")
            limited = CandidateManifest(root, product, max_scan_entries=1)
            with self.assertRaises(GateInputError) as limit_error:
                limited.tree_digest(tree, allowed_root=product)
            self.assertEqual(limit_error.exception.reason, "GATE_INPUT_LIMIT_EXCEEDED")

            expired = CandidateManifest(root, product, deadline=1.0, clock=lambda: 1.0)
            with self.assertRaises(GateInputError) as deadline_error:
                expired.digest(product / "missing", root=product)
            self.assertEqual(deadline_error.exception.reason, "STAGE_BUDGET_EXCEEDED")

            outside = root / "outside.md"
            outside.write_text("outside\n", encoding="utf-8")
            escaped = product / "escaped.md"
            try:
                escaped.symlink_to(outside)
            except OSError as exc:  # pragma: no cover - platform permission fallback
                self.skipTest(f"symlinks unavailable: {exc}")
            candidates = CandidateManifest(root, product)
            with self.assertRaises(GateInputError) as symlink_error:
                candidates.digest(escaped, root=product)
            self.assertEqual(symlink_error.exception.reason, "GATE_INPUT_SYMLINK_ESCAPE")

            with self.assertRaises(GateInputError) as task_dir_error:
                gate_input_digest(
                    "knowledge", harness=root, product=product, changed_files=[],
                    planning_gate=product / "missing.json",
                    planning_credential={"task_dir": str(escaped)}, command=[],
                )
            self.assertEqual(task_dir_error.exception.reason, "GATE_INPUT_SYMLINK_ESCAPE")

    def test_expired_absolute_deadline_prevents_cache_reuse_and_execution(self) -> None:
        prior = {
            "structure": {
                "decision": "pass", "source": "executed", "fingerprint": "fp-structure",
                "input_digest": "input-structure", "subject_digest": "old-subject",
                "policy_digest": "policy", "completed_at": "earlier",
            },
        }
        calls = []

        def runner(*_args):
            calls.append("executed")
            return {"decision": "pass", "reason": "OK"}, 0

        checks, hits = execute_specs(
            [spec("structure", cacheable=True)], runner=runner,
            previous_checks=prior, subject_digest="new-subject", policy_digest="policy",
            deadline=1.0, clock=lambda: 1.0,
        )

        self.assertEqual(hits, 0)
        self.assertEqual(calls, [])
        self.assertEqual(checks["structure"]["source"], "not_executed")
        self.assertEqual(checks["structure"]["reason"], "STAGE_BUDGET_EXCEEDED")

    def test_required_manifest_file_and_project_config_invalidate_relevant_gates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp)
            scripts = harness / ".harness/scripts"
            scripts.mkdir(parents=True)
            entry = scripts / "entry.sh"
            entry.write_text("#!/bin/sh\n", encoding="utf-8")
            (harness / "required.md").write_text("present\n", encoding="utf-8")
            (harness / ".harness/harness-manifest.yaml").write_text(
                "required_files:\n  - required.md\n", encoding="utf-8",
            )
            product = harness / "product"
            (product / "harness-workspace").mkdir(parents=True)
            project = product / "harness-workspace/project.yaml"
            project.write_text("product: one\n", encoding="utf-8")
            common = {
                "harness": harness, "product": product, "changed_files": [],
                "planning_gate": product / "missing.json", "planning_credential": {},
                "command": ["bash", str(entry)],
            }
            harness_before = gate_input_digest("harness", **common)
            knowledge_before = gate_input_digest("knowledge", **common)
            (harness / "required.md").unlink()
            project.write_text("product: two\n", encoding="utf-8")
            harness_after = gate_input_digest("harness", **common)
            knowledge_after = gate_input_digest("knowledge", **common)
        self.assertNotEqual(harness_before, harness_after)
        self.assertNotEqual(knowledge_before, knowledge_after)

    def test_serial_gates_share_one_stage_budget(self) -> None:
        calls = []

        def runner(_command: list[str], name: str, timeout: float):
            calls.append((name, timeout))
            time.sleep(min(0.04, timeout))
            return {"decision": "pass", "reason": "OK"}, 0

        started = time.monotonic()
        checks, _hits = execute_specs(
            [spec("a", parallel=False), spec("b", parallel=False)], runner=runner,
            previous_checks={}, subject_digest="subject", policy_digest="policy",
            remaining_budget_ms=50,
        )
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.075)
        self.assertEqual(checks["b"]["decision"], "block")
        self.assertEqual(checks["b"]["reason"], "STAGE_BUDGET_EXCEEDED")
        if len(calls) == 2:
            self.assertLess(calls[1][1], calls[0][1])
        else:
            self.assertEqual(checks["b"]["source"], "not_executed")
            self.assertEqual(checks["b"]["attempt"], 0)


if __name__ == "__main__":
    unittest.main()
