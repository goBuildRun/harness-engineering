#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".ael" / "scripts"))

from diff_integrity_check import (  # noqa: E402
    changed_for_layout, check_commit_integrity, check_diff_integrity,
)


class DiffIntegrityCheckTest(unittest.TestCase):
    def _repo(self, root: Path) -> Path:
        repo = root / "product"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "harness@example.invalid"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=repo, check=True)
        (repo / "tracked.txt").write_text("baseline\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
        return repo

    def test_untracked_new_blank_line_at_eof_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp))
            (repo / "new.md").write_text("content\n\n", encoding="utf-8")

            result = check_diff_integrity(repo, ["new.md"])

            self.assertEqual(result["decision"], "block")
            self.assertIn("new blank line at EOF", result["reason"])
            self.assertEqual(result["checked_untracked"], 1)

    def test_tracked_trailing_whitespace_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp))
            (repo / "tracked.txt").write_text("changed  \n", encoding="utf-8")

            result = check_diff_integrity(repo, ["tracked.txt"])

            self.assertEqual(result["decision"], "block")
            self.assertIn("trailing whitespace", result["reason"])

    def test_only_task_scoped_untracked_files_are_checked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp))
            (repo / "legacy.md").write_text("preexisting\n\n", encoding="utf-8")
            (repo / "story.md").write_text("clean\n", encoding="utf-8")

            result = check_diff_integrity(repo, ["story.md"])

            self.assertEqual(result["decision"], "pass")
            self.assertEqual(result["checked_untracked"], 1)

    def test_commit_check_catches_new_file_whitespace_without_a_worktree_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp))
            (repo / "new.md").write_text("content\n\n", encoding="utf-8")
            subprocess.run(["git", "add", "new.md"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "add bad whitespace"], cwd=repo, check=True)

            result = check_commit_integrity(repo, "HEAD")

            self.assertEqual(result["decision"], "block")
            self.assertIn("new blank line at EOF", result["reason"])

    def test_commit_check_accepts_clean_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp))
            (repo / "new.md").write_text("content\n", encoding="utf-8")
            subprocess.run(["git", "add", "new.md"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "add clean file"], cwd=repo, check=True)

            result = check_commit_integrity(repo, "HEAD")

            self.assertEqual(result["decision"], "pass")
            self.assertEqual(result["checked_paths"], 1)

    def test_root_commit_check_does_not_require_a_materialized_empty_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "root-only"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "harness@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=repo, check=True)
            (repo / "root.md").write_text("content\n\n", encoding="utf-8")
            subprocess.run(["git", "add", "root.md"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "root"], cwd=repo, check=True)

            result = check_commit_integrity(repo, "HEAD")

            self.assertEqual(result["decision"], "block")
            self.assertIn("new blank line at EOF", result["reason"])

    def test_without_active_baseline_fallback_includes_untracked_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp))
            (repo / "new.md").write_text("content\n", encoding="utf-8")
            layout = SimpleNamespace(
                product_root=repo,
                agent_workspace=repo / "ael-workspace/runs",
            )

            self.assertEqual(changed_for_layout(layout), ["new.md"])

    def test_unborn_repository_checks_staged_whitespace_without_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "unborn"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            (repo / "staged.md").write_text("content  \n", encoding="utf-8")
            subprocess.run(["git", "add", "staged.md"], cwd=repo, check=True)

            result = check_diff_integrity(repo, ["staged.md"])

            self.assertEqual(result["decision"], "block")
            self.assertIn("trailing whitespace", result["reason"])


if __name__ == "__main__":
    unittest.main()
