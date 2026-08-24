#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / ".ael" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from ael_intake import table_rows  # noqa: E402
from ael_intake_policy import skip_file  # noqa: E402


class HarnessIntakeTest(unittest.TestCase):
    def test_intake_modules_keep_single_responsibility_size(self) -> None:
        for name in (
            "ael_intake.py", "ael_intake_policy.py",
            "ael_intake_review.py", "ael_intake_scan.py",
        ):
            lines = (SCRIPT_DIR / name).read_text().splitlines()
            self.assertLessEqual(len(lines), 400, name)

    def test_scan_policy_excludes_local_secrets_and_generated_workspace(self) -> None:
        self.assertTrue(skip_file(Path(".env")))
        self.assertTrue(skip_file(Path("service-account.json")))
        self.assertTrue(skip_file(Path("certificate.pem")))
        self.assertFalse(skip_file(Path("src/config.py")))

    def test_table_rows_preserves_inline_code_inside_description(self) -> None:
        text = """
### 关键代码入口候选

| 路径 | 归属/处理 | 候选原因 |
|------|-----------|----------|
| `services/gateway/` | product-owned; review=full; debt=track | 上游网关扩展包，运行时 import 契约 `gateway.*` |

### 技术栈信号
""".lstrip()

        rows = table_rows(text, "### 关键代码入口候选", r"\n### 技术栈信号")

        self.assertEqual(rows[0][0], "services/gateway/")
        self.assertEqual(rows[0][2], "上游网关扩展包，运行时 import 契约 `gateway.*`")


if __name__ == "__main__":
    unittest.main()
