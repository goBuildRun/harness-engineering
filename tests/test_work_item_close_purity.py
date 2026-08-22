#!/usr/bin/env python3
from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import work_item  # noqa: E402


class WorkItemClosePurityTest(unittest.TestCase):
    def test_nonterminal_status_update_does_not_sync_planning(self) -> None:
        provider = mock.Mock(name="noop")
        provider.name = "noop"
        provider.update_status.return_value = (True, "NOOP_UPDATED")
        args = SimpleNamespace(
            harness_root=str(ROOT), id="WI-42", status="in_progress", note="started",
        )
        output = io.StringIO()
        with mock.patch("work_item.get_provider", return_value=provider), redirect_stdout(output):
            self.assertEqual(work_item.cmd_close(args), 0)
        provider.update_status.assert_called_once_with("WI-42", "in_progress", "started")
        self.assertNotIn("knowledge_sync", output.getvalue())


if __name__ == "__main__":
    unittest.main()
