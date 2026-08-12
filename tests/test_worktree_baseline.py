#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from worktree_baseline import capture_baseline, changed_since_baseline  # noqa: E402


class WorktreeBaselineTest(unittest.TestCase):
    def _repo(self, root: Path) -> Path:
        repo = root / "product"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(
            ["git", "config", "user.email", "harness@example.invalid"],
            cwd=repo,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Harness Test"], cwd=repo, check=True
        )
        (repo / "legacy.txt").write_text("base\n", encoding="utf-8")
        (repo / "planned.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
        return repo

    def test_capture_ignores_unchanged_preexisting_dirty_and_untracked_paths(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._repo(root)
            baseline = root / "baseline.json"
            (repo / "legacy.txt").write_text("dirty before start\n", encoding="utf-8")
            (repo / "existing-untracked.txt").write_text("existing\n", encoding="utf-8")

            capture_baseline(repo, baseline, work_item_id="wi-1")
            self.assertEqual(changed_since_baseline(repo, baseline), [])

            (repo / "legacy.txt").write_text("changed by task\n", encoding="utf-8")
            (repo / "existing-untracked.txt").unlink()
            (repo / "new-untracked.txt").write_text("new\n", encoding="utf-8")

            self.assertEqual(
                changed_since_baseline(repo, baseline),
                ["existing-untracked.txt", "legacy.txt", "new-untracked.txt"],
            )

    def test_recovery_excludes_only_planned_paths_and_records_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._repo(root)
            baseline = root / "baseline.json"
            (repo / "legacy.txt").write_text("preexisting\n", encoding="utf-8")
            (repo / "planned.txt").write_text("task change\n", encoding="utf-8")
            (repo / "planned-untracked.txt").write_text("task new\n", encoding="utf-8")

            capture_baseline(
                repo,
                baseline,
                work_item_id="wi-legacy",
                mode="recovery",
                reason="independent review confirms unrelated pre-existing changes",
                exclude_paths={"planned.txt", "planned-untracked.txt"},
            )

            data = json.loads(baseline.read_text(encoding="utf-8"))
            self.assertEqual(data["mode"], "recovery")
            self.assertEqual(
                data["reason"],
                "independent review confirms unrelated pre-existing changes",
            )
            self.assertEqual(
                data["excluded_paths"], ["planned-untracked.txt", "planned.txt"]
            )
            self.assertEqual(
                changed_since_baseline(repo, baseline),
                ["planned-untracked.txt", "planned.txt"],
            )

    def test_recovery_requires_reason_and_capture_does_not_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._repo(root)
            baseline = root / "baseline.json"

            capture_baseline(repo, baseline, work_item_id="wi-1")
            original = baseline.read_text(encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "reason"):
                capture_baseline(
                    repo, root / "recovery.json", work_item_id="wi-1", mode="recovery"
                )
            capture_baseline(repo, baseline, work_item_id="wi-2")
            self.assertEqual(baseline.read_text(encoding="utf-8"), original)

    def test_generated_python_caches_never_enter_baseline_or_changed_paths(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._repo(root)
            baseline = root / "baseline.json"
            cache = repo / "services" / "__pycache__" / "adapter.cpython-313.pyc"
            cache.parent.mkdir(parents=True)
            cache.write_bytes(b"before")

            capture_baseline(repo, baseline, work_item_id="wi-cache")
            data = json.loads(baseline.read_text(encoding="utf-8"))
            self.assertNotIn(
                "services/__pycache__/adapter.cpython-313.pyc",
                data["entries"],
            )

            cache.write_bytes(b"after")
            (cache.parent / "new.cpython-313.pyo").write_bytes(b"new")
            self.assertEqual(changed_since_baseline(repo, baseline), [])


if __name__ == "__main__":
    unittest.main()
