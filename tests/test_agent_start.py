#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / ".harness" / "scripts"


class AgentStartTest(unittest.TestCase):
    def test_agent_start_injects_product_knowledge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            workspace = product / "harness-workspace"
            task_dir = workspace / "planning" / "tasks" / "2026-06-22-local1234-demo"
            knowledge = workspace / "knowledge"
            task_dir.mkdir(parents=True)
            knowledge.mkdir(parents=True)

            (workspace / "project.yaml").write_text(
                """
product:
  id: demo
  name: Demo
  profile: generic
workspace:
  root: harness-workspace
  planning: planning
  runs: runs
  knowledge: knowledge
  evidence: evidence
work_item:
  provider: noop
""".lstrip(),
                encoding="utf-8",
            )
            (knowledge / "CONTEXT.md").write_text("# CONTEXT\n\nDemo stable context.\n", encoding="utf-8")
            (knowledge / "LESSONS.md").write_text("# LESSONS\n\nDemo stable lesson.\n", encoding="utf-8")
            (knowledge / "REFERENCE_SYSTEMS.md").write_text(
                "# REFERENCE_SYSTEMS\n\nDemo upstream boundary.\n",
                encoding="utf-8",
            )
            (task_dir / "planning_gate_pass.json").write_text(
                json.dumps(
                    {
                        "decision": "pass",
                        "gate": "planning",
                        "level": "L1",
                        "task_dir": str(task_dir),
                        "work_item": {"id": "local1234", "provider": "noop"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            env = {
                **os.environ,
                "HARNESS_PRODUCT_ROOT": str(product),
                "WORK_ITEM_PROVIDER": "noop",
            }
            out = subprocess.check_output(
                ["bash", str(SCRIPT_DIR / "agent_start.sh"), "local1234"],
                cwd=ROOT,
                env=env,
                text=True,
                stderr=subprocess.DEVNULL,
            )
            data = json.loads(out)
            context = (workspace / "runs" / "tasks" / "local1234" / "context.md").read_text(encoding="utf-8")
            legacy_context = (workspace / "runs" / "context.md").read_text(encoding="utf-8")
            baseline = json.loads(
                (workspace / "runs" / "tasks" / "local1234" / "worktree_baseline.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(data["decision"], "pass")
        self.assertIn("产品知识注入", context)
        self.assertIn("Demo stable context", context)
        self.assertIn("Demo stable lesson", context)
        self.assertIn("Demo upstream boundary", context)
        self.assertEqual(context, legacy_context)
        self.assertEqual(baseline["work_item_id"], "local1234")
        self.assertEqual(baseline["mode"], "task_start")


if __name__ == "__main__":
    unittest.main()
