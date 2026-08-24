#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".ael" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from work_item_providers import (  # noqa: E402
    FeishuProvider,
    JiraProvider,
    NoopProvider,
    TeambitionProvider,
    WorkItemProvider,
)


class WorkItemModuleBoundariesTest(unittest.TestCase):
    def test_public_provider_exports_remain_compatible(self) -> None:
        for provider in (NoopProvider, TeambitionProvider, FeishuProvider, JiraProvider):
            self.assertTrue(issubclass(provider, WorkItemProvider))

    def test_work_item_modules_stay_within_code_health_limit(self) -> None:
        names = (
            "work_item.py",
            "work_item_contract.py",
            "work_item_diagnostics.py",
            "work_item_feishu.py",
            "work_item_feishu_payload.py",
            "work_item_jira.py",
            "work_item_local_binding.py",
            "work_item_provider_config.py",
            "work_item_providers.py",
            "work_item_teambition.py",
            "work_item_teambition_support.py",
        )
        for name in names:
            self.assertLessEqual(len((SCRIPTS / name).read_text().splitlines()), 400, name)


if __name__ == "__main__":
    unittest.main()
