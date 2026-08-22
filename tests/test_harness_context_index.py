#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from harness_context_index import build_index, render_index  # noqa: E402
from workspace_paths import load_layout  # noqa: E402


class HarnessContextIndexTest(unittest.TestCase):
    def test_index_contains_digest_and_summary_but_not_full_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            workspace = product / "harness-workspace"
            knowledge = workspace / "knowledge"
            knowledge.mkdir(parents=True)
            (workspace / "project.yaml").write_text(
                "product:\n  id: demo\n  name: Demo\nworkspace:\n  root: harness-workspace\n  knowledge: knowledge\n",
                encoding="utf-8",
            )
            for name in ("CONTEXT.md", "LESSONS.md", "REFERENCE_SYSTEMS.md"):
                (knowledge / name).write_text(f"# {name}\n\nimportant summary\n" + "x" * 4000)
            payload = build_index(load_layout(ROOT, product))
            rendered = render_index(payload)
        self.assertFalse(payload["full_text_included"])
        self.assertTrue(all(len(item["summary"]) <= 800 for item in payload["artifacts"]))
        self.assertIn("SHA-256", rendered)
        self.assertLess(len(rendered), 4000)


if __name__ == "__main__":
    unittest.main()
