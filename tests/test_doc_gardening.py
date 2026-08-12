from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from doc_gardening_check import check_links, github_anchor  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
