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

from harness_assurance import apply_enforcement, audit_guards, install_guards  # noqa: E402
from harness_runtime import default_result, load_result  # noqa: E402
from harness_schema import validate_result  # noqa: E402


class HarnessAssuranceTest(unittest.TestCase):
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
            for name in ("pre-commit", "pre-push"):
                hook = product / ".githooks" / name
                self.assertTrue(hook.is_file())
                self.assertTrue(hook.stat().st_mode & 0o111)
                self.assertIn("HARNESS_GUARD_RUNTIME_MISSING", hook.read_text())
            self.assertEqual(audit_guards(product)["level"], "guarded")

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

    def test_guarded_never_maps_to_enforced_without_live_probe(self) -> None:
        result = default_result("guarded")
        result.update({"state": "validated", "decision": "pass"})
        apply_enforcement(
            result,
            {"enforcement": "shadow", "blockers": ["BRANCH_REQUIRED_CHECK_MISSING"]},
            guarded=True,
        )
        self.assertEqual(result["assurance"]["level"], "guarded")
        self.assertTrue(result["assurance"]["bypassable"])
        self.assertEqual(result["enforcement"], "shadow")

    def test_only_successful_live_enforcement_maps_to_enforced(self) -> None:
        result = default_result("enforced")
        apply_enforcement(
            result,
            {"enforcement": "enforced", "blockers": [], "probed_at": "2026-08-12T00:00:00Z"},
            guarded=True,
        )
        self.assertEqual(result["assurance"]["level"], "enforced")
        self.assertFalse(result["assurance"]["bypassable"])
        self.assertEqual(result["enforcement"], "enforced")

    def test_schema_rejects_both_directions_of_enforcement_mismatch(self) -> None:
        result = default_result("mismatch")
        result["enforcement"] = "enforced"
        self.assertIn("enforcement.assurance_mismatch", validate_result(result))
        result["enforcement"] = "shadow"
        result["assurance"] = {**result["assurance"], "level": "enforced"}
        self.assertIn("assurance.enforcement_mismatch", validate_result(result))


if __name__ == "__main__":
    unittest.main()
