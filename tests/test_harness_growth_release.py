#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from harness_growth_release import release_status  # noqa: E402


class HarnessGrowthReleaseTest(unittest.TestCase):
    def test_followup_is_off_critical_path_but_stays_pending_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "x-GROWTH-CAPTURE.md").write_text(
                "- **Release impact**：followup\n", encoding="utf-8",
            )
            result = release_status(root)
        self.assertEqual(result["decision"], "pass")
        self.assertEqual(result["followups_pending_review"], ["x-GROWTH-CAPTURE.md"])

    def test_blocker_and_historical_unknown_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            capture = root / "x-GROWTH-CAPTURE.md"
            capture.write_text("- **Release impact**：blocker\n", encoding="utf-8")
            self.assertEqual(release_status(root)["reason"], "GROWTH_RELEASE_BLOCKER")
            capture.write_text("# legacy capture\n", encoding="utf-8")
            self.assertEqual(release_status(root)["reason"], "GROWTH_RELEASE_IMPACT_UNKNOWN")


if __name__ == "__main__":
    unittest.main()
