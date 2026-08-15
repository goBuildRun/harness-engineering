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
    def test_l3_agent_start_requests_strict_tier_and_planned_write_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            workspace = product / "harness-workspace"
            task_dir = workspace / "planning" / "tasks" / "2026-06-22-local1234-demo"
            task_dir.mkdir(parents=True)
            (workspace / "project.yaml").write_text(
                "product:\n  id: demo\n  name: Demo\n  profile: generic\n"
                "workspace:\n  root: harness-workspace\n  planning: planning\n  runs: runs\n"
                "  knowledge: knowledge\n  evidence: evidence\nwork_item:\n  provider: noop\n",
                encoding="utf-8",
            )
            (task_dir / "00-任务卡.md").write_text("- Work Item：`local1234`\n", encoding="utf-8")
            (task_dir / "03-实施方案.md").write_text(
                "| ID | 读取边界 | 写入边界 | 动作 | 验证命令 | 完成标准 |\n"
                "| --- | --- | --- | --- | --- | --- |\n"
                "| T1 | `services/source.py` | `services/replay.py` | 实现反馈 | `python3 -m unittest` | 测试通过 |\n",
                encoding="utf-8",
            )
            (task_dir / "planning_gate_pass.json").write_text(json.dumps({
                "decision": "pass", "level": "L3", "task_dir": str(task_dir),
                "work_item": {"id": "local1234", "provider": "noop"},
            }), encoding="utf-8")
            env = {**os.environ, "HARNESS_PRODUCT_ROOT": str(product), "WORK_ITEM_PROVIDER": "noop"}

            out = subprocess.check_output(
                ["bash", str(SCRIPT_DIR / "agent_start.sh"), "local1234"],
                cwd=ROOT, env=env, text=True, stderr=subprocess.DEVNULL,
            )
            result = json.loads(
                (workspace / "runs/tasks/local1234/result.json").read_text(encoding="utf-8")
            )

        self.assertEqual(json.loads(out)["decision"], "pass")
        self.assertEqual(result["tier"]["initial"], "strict")
        self.assertIn("services/replay.py", result["task"]["scope"])
        self.assertIn(
            "harness-workspace/planning/tasks/2026-06-22-local1234-demo",
            result["task"]["scope"],
        )

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
            result = json.loads(
                (workspace / "runs" / "tasks" / "local1234" / "result.json").read_text(
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
        self.assertEqual(result["task_id"], "local1234")
        self.assertEqual(result["work_item"]["id"], "local1234")

    def test_agent_start_resume_preserves_runtime_and_worktree_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            workspace = product / "harness-workspace"
            task_dir = workspace / "planning" / "tasks" / "2026-06-22-local1234-demo"
            task_dir.mkdir(parents=True)
            (workspace / "project.yaml").write_text(
                "product:\n  id: demo\n  name: Demo\nworkspace:\n  root: harness-workspace\n  planning: planning\n  runs: runs\n  knowledge: knowledge\n  evidence: evidence\nwork_item:\n  provider: noop\n",
                encoding="utf-8",
            )
            (task_dir / "planning_gate_pass.json").write_text(json.dumps({
                "decision": "pass", "level": "L1", "task_dir": str(task_dir),
                "work_item": {"id": "local1234", "provider": "noop"},
            }), encoding="utf-8")
            env = {**os.environ, "HARNESS_PRODUCT_ROOT": str(product), "WORK_ITEM_PROVIDER": "noop"}
            argv = ["bash", str(SCRIPT_DIR / "agent_start.sh"), "local1234"]
            subprocess.check_output(argv, cwd=ROOT, env=env, text=True, stderr=subprocess.DEVNULL)
            baseline_path = workspace / "runs/tasks/local1234/worktree_baseline.json"
            result_path = workspace / "runs/tasks/local1234/result.json"
            baseline_before = baseline_path.read_bytes()
            result_before = json.loads(result_path.read_text(encoding="utf-8"))
            subprocess.check_output(argv, cwd=ROOT, env=env, text=True, stderr=subprocess.DEVNULL)

            baseline_after = baseline_path.read_bytes()
            result_after = json.loads(result_path.read_text(encoding="utf-8"))

        self.assertEqual(baseline_after, baseline_before)
        self.assertEqual(result_after["baseline"], result_before["baseline"])


if __name__ == "__main__":
    unittest.main()
