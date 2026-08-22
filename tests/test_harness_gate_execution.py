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

from harness_gate_execution import GateSpec, execute_specs, gate_input_digest  # noqa: E402


def spec(name: str, *, parallel: bool = True, cacheable: bool = False) -> GateSpec:
    return GateSpec(
        name=name, command=(name,), input_digest=f"input-{name}",
        fingerprint=f"fp-{name}", timeout_seconds=2, budget_ms=2000,
        parallel_safe=parallel, cacheable=cacheable,
    )


class HarnessGateExecutionTest(unittest.TestCase):
    def test_read_only_gates_run_in_parallel_but_results_can_be_stably_ordered(self) -> None:
        barrier = threading.Barrier(2)

        def runner(_command: list[str], name: str, _timeout: int):
            barrier.wait(timeout=1)
            time.sleep(0.02)
            return {"decision": "pass", "reason": f"{name.upper()}_OK"}, 0

        started = time.monotonic()
        checks, hits = execute_specs(
            [spec("a"), spec("b")], runner=runner, previous_checks={},
            subject_digest="subject", policy_digest="policy",
        )
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.20)
        self.assertEqual(set(checks), {"a", "b"})
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
