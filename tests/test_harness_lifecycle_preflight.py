#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from harness_lifecycle_preflight import discover, preflight_resumed  # noqa: E402


class HarnessLifecyclePreflightTest(unittest.TestCase):
    def product(self, root: Path, config: str) -> Path:
        product = root / "product"
        workspace = product / "harness-workspace"
        workspace.mkdir(parents=True)
        (workspace / "project.yaml").write_text(config, encoding="utf-8")
        return product

    def test_noop_is_local_and_offline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = self.product(Path(tmp), "work_item:\n  provider: noop\n")
            result = discover(product)
        self.assertEqual(result["decision"], "pass")
        self.assertEqual(result["network_calls"], 0)

    def test_feishu_completed_mode_discovers_ready_mapping_before_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = self.product(Path(tmp), """
work_item:
  provider: feishu
  providers:
    feishu:
      status_update_mode: completed
""".lstrip())
            result = discover(product)
        self.assertEqual(result["decision"], "pass")
        self.assertEqual(result["status_mapping"]["ready_to_release"], "open")
        self.assertEqual(result["readback"], "verified-open")

    def test_disabled_or_missing_ready_mapping_blocks_early(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            feishu = self.product(root, "work_item:\n  provider: feishu\n")
            self.assertEqual(discover(feishu)["reason"], "LIFECYCLE_STATUS_UPDATE_DISABLED")

    def test_digest_is_stable_and_resumed_strict_task_blocks_offline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = self.product(Path(tmp), "work_item:\n  provider: feishu\n")
            first = discover(product)
            second = discover(product)
            outcome = preflight_resumed({
                "decision": "pass", "reason": "TASK_RESUMED",
                "result": {
                    "tier": {"initial": "strict", "effective": "strict"},
                    "work_item": {"id": "WI-1", "provider": "feishu"},
                },
                "changed": False,
            }, product, None)
        self.assertEqual(first["capability_digest"], second["capability_digest"])
        self.assertEqual(outcome["decision"], "block")
        self.assertEqual(outcome["reason"], "LIFECYCLE_STATUS_UPDATE_DISABLED")
        self.assertEqual(outcome["lifecycle"]["network_calls"], 0)


if __name__ == "__main__":
    unittest.main()
