from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from doc_gardening_check import check_links, github_anchor  # noqa: E402
from harness_timing import FINISH_RETRY_LIMIT, STAGE_BUDGETS_MS, STAGE_RETRY_LIMIT  # noqa: E402


class DocGardeningAnchorTests(unittest.TestCase):
    def test_github_anchor_preserves_double_space_after_punctuation_removal(self) -> None:
        self.assertEqual(
            github_anchor("3. 架构总览：双闭环 + 三体产品闭环"),
            "3-架构总览双闭环--三体产品闭环",
        )

    def test_same_file_broken_anchor_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc = root / "README.md"
            doc.write_text("# Existing Heading\n\n[bad](#missing-heading)\n", encoding="utf-8")

            issues = check_links(root, root, [doc])

        self.assertEqual(issues, ["BROKEN_ANCHOR:README.md->#missing-heading"])

    def test_relative_document_anchor_is_validated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docs = root / "docs"
            docs.mkdir()
            source = root / "README.md"
            target = docs / "guide.md"
            source.write_text("[guide](./docs/guide.md#目标章节)\n", encoding="utf-8")
            target.write_text("# 目标章节\n", encoding="utf-8")

            issues = check_links(root, root, [source, target])

        self.assertEqual(issues, [])

    def test_public_docs_use_the_canonical_command_contract(self) -> None:
        root = Path(__file__).resolve().parents[1]
        docs = (
            root / "README.md",
            root / "ARCHITECTURE.md",
            root / "docs/USAGE.md",
            root / "docs/design-docs/lean-enforcement.md",
            root / "docs/design-docs/lean-plan-flow.md",
            root / "docs/HARNESS_DOC_CONSISTENCY.md",
        )
        for path in docs:
            text = path.read_text(encoding="utf-8")
            self.assertIn("plan", text, path)
            self.assertIn("status", text, path)
            self.assertIn("finish", text, path)
            self.assertNotIn("`start/status/finish`", text, path)
            self.assertNotIn("harness start/status/finish", text, path)

    def test_documented_story_budget_matches_runtime_contract(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "docs/design-docs/lean-plan-flow.md").read_text(encoding="utf-8")
        for stage, budget_ms in STAGE_BUDGETS_MS.items():
            self.assertIn(stage, text)
            self.assertIn(f"{budget_ms // 60_000} 分钟", text)
        self.assertEqual(sum(STAGE_BUDGETS_MS.values()), 30 * 60_000)
        self.assertEqual(FINISH_RETRY_LIMIT, 2)
        self.assertEqual(STAGE_RETRY_LIMIT, 2)


if __name__ == "__main__":
    unittest.main()
