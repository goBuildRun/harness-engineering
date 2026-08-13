#!/usr/bin/env python3
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class GitNativeContractTest(unittest.TestCase):
    def test_runtime_and_templates_have_no_hosted_lifecycle_contract(self) -> None:
        forbidden = (
            "platform: github", "required_check:", "harness-commit-acceptance",
            "GITHUB_TOKEN", "workflow_run", "required_run_id",
        )
        roots = (ROOT / ".harness" / "scripts", ROOT / ".harness" / "templates")
        findings = []
        for base in roots:
            for path in base.rglob("*"):
                if not path.is_file() or "__pycache__" in path.parts:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                findings.extend(
                    f"{path.relative_to(ROOT)}:{term}" for term in forbidden if term in text
                )
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
