#!/usr/bin/env python3
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".harness" / "scripts"


class RuntimeModuleBoundariesTest(unittest.TestCase):
    def test_runtime_python_modules_do_not_exceed_code_health_limit(self) -> None:
        oversized = {
            path.name: len(path.read_text(encoding="utf-8").splitlines())
            for path in SCRIPTS.glob("*.py")
            if len(path.read_text(encoding="utf-8").splitlines()) > 400
        }
        self.assertEqual(oversized, {})


if __name__ == "__main__":
    unittest.main()
