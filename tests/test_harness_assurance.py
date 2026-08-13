#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from harness_assurance import audit_guards, install_guards, pre_push_script  # noqa: E402
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
            for name in ("pre-commit", "post-commit", "pre-push"):
                hook = product / ".githooks" / name
                self.assertTrue(hook.is_file())
                self.assertTrue(hook.stat().st_mode & 0o111)
                self.assertIn("HARNESS_GUARD_RUNTIME_MISSING", hook.read_text())
            subprocess.run(["git", "add", ".githooks"], cwd=product, check=True)
            self.assertEqual(audit_guards(product)["level"], "guarded")

    def test_guard_audit_rejects_untracked_or_modified_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=product, check=True)
            install_guards(product)
            untracked = audit_guards(product)
            self.assertEqual(untracked["level"], "local")
            self.assertIn("GUARDED_GIT_GUARDS_UNTRACKED", untracked["blockers"])
            subprocess.run(["git", "add", ".githooks"], cwd=product, check=True)
            (product / ".githooks/pre-push").write_text("#!/bin/sh\nexit 0\n")
            modified = audit_guards(product)
            self.assertEqual(modified["level"], "local")
            self.assertIn("GUARDED_GIT_GUARDS_MODIFIED", modified["blockers"])

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

    def test_guarded_finish_commit_push_lifecycle_does_not_stale_deadlock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            remote = product.parent / f"{product.name}-remote.git"
            (product / "docs").mkdir()
            (product / "docs/note.md").write_text("base\n")
            (product / ".gitignore").write_text("harness-workspace/runs/\n")
            subprocess.run(["git", "init", "-q"], cwd=product, check=True)
            subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
            subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=product, check=True)
            subprocess.run(["git", "config", "user.email", "harness@example.invalid"], cwd=product, check=True)
            subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=product, check=True)
            subprocess.run(["git", "add", "."], cwd=product, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=product, check=True)
            install_guards(product)
            subprocess.run(["git", "add", ".githooks"], cwd=product, check=True)
            subprocess.run(["git", "commit", "-qm", "guarded onboarding", "--no-verify"], cwd=product, check=True)
            command = [str(SCRIPTS / "harness"), "--product-root", str(product)]
            subprocess.check_output([*command, "start", "guarded-task", "--tier", "lite", "--scope", "docs"])
            binding_result = json.loads(subprocess.check_output(
                [*command, "finish", "--skip-legacy-gates"], text=True,
                env={**dict(os.environ), "HARNESS_PRODUCT_ROOT": str(product)},
            ))
            self.assertEqual(binding_result["decision"], "pass")
            subprocess.run(["git", "add", "."], cwd=product, check=True)
            subprocess.run(["git", "commit", "-qm", "binding"], cwd=product, check=True)
            (product / "docs/note.md").write_text("changed\n")
            finished = json.loads(subprocess.check_output(
                [*command, "finish", "--skip-legacy-gates"], text=True,
                env={**dict(os.environ), "HARNESS_PRODUCT_ROOT": str(product)},
            ))
            self.assertEqual(finished["decision"], "pass")
            subprocess.run([str(product / ".githooks/pre-commit")], cwd=product, check=True)
            subprocess.run(["git", "add", "docs/note.md"], cwd=product, check=True)
            subprocess.run(["git", "commit", "-qm", "change"], cwd=product, check=True)
            head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=product, text=True).strip()
            pushed = subprocess.run(
                [str(product / ".githooks/pre-push"), "origin", str(remote)], cwd=product,
                text=True, input=f"refs/heads/main {head} refs/heads/main {'0' * 40}\n",
                capture_output=True,
            )
            self.assertEqual(pushed.returncode, 0, pushed.stderr)
            self.assertIn("HARNESS_PUSH_GUARD_PASS", pushed.stderr)
            status = json.loads(subprocess.check_output([*command, "status"], text=True))
            self.assertEqual(status["result"]["state"], "validated")

    def test_pre_push_checks_every_new_commit_and_pushes_attestation_refs(self) -> None:
        script = pre_push_script()
        self.assertIn("rev-list", script)
        self.assertIn("refs/harness/attestations", script)
        self.assertIn("git push", script)

    def test_schema_rejects_both_directions_of_enforcement_mismatch(self) -> None:
        result = default_result("mismatch")
        result["enforcement"] = "enforced"
        self.assertIn("enforcement.assurance_mismatch", validate_result(result))
        result["enforcement"] = "shadow"
        result["assurance"] = {**result["assurance"], "level": "enforced"}
        self.assertIn("assurance.enforcement_mismatch", validate_result(result))


if __name__ == "__main__":
    unittest.main()
