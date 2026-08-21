#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from harness_knowledge import check_planning, sync_planning  # noqa: E402
from workspace_paths import load_layout  # noqa: E402


def write_project_config(product: Path) -> None:
    (product / "harness-workspace").mkdir(parents=True)
    (product / "harness-workspace" / "project.yaml").write_text(
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


def write_minimal_spec(product: Path, title: str = "Demo 产品蓝图") -> None:
    spec = product / "harness-workspace" / "planning" / "product-specs" / "demo.md"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(
        f"""
---
bmad_method: true
bmad_skills:
  - bmad-create-prd
spec_level: L2
---

# {title}

## 目标

让团队用 BMAD 规划沉淀产品目标。
""".lstrip(),
        encoding="utf-8",
    )


class HarnessKnowledgeTest(unittest.TestCase):
    def test_check_planning_does_not_create_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            layout = load_layout(ROOT, product)
            result = check_planning(layout)
            self.assertFalse(result["ok"])
            self.assertFalse((product / "harness-workspace").exists())

    def test_sync_planning_writes_bmad_context_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            write_project_config(product)

            spec = product / "harness-workspace" / "planning" / "product-specs" / "demo.md"
            spec.parent.mkdir(parents=True)
            spec.write_text(
                """
---
bmad_method: true
bmad_skills:
  - bmad-create-prd
spec_level: L2
---

# Demo 产品蓝图

## 目标

让团队用 BMAD 规划沉淀产品目标。

## 验收标准

- [ ] CONTEXT 自动包含产品蓝图

## 非目标

- 不直接写业务代码
""".lstrip(),
                encoding="utf-8",
            )

            plan = product / "harness-workspace" / "planning" / "exec-plans" / "active" / "demo-plan.md"
            plan.parent.mkdir(parents=True)
            plan.write_text(
                """
---
bmad_method: true
bmad_skills:
  - bmad-check-implementation-readiness
linked_spec: product-specs/demo.md
---

# 执行计划：Demo

## 目标

打通规划到知识库的闭环。

## 就绪检查结论

方案可执行。
""".lstrip(),
                encoding="utf-8",
            )

            card = product / "harness-workspace" / "planning" / "tasks" / "2026-06-21-task-demo" / "00-任务卡.md"
            card.parent.mkdir(parents=True)
            card.write_text(
                """
# 任务卡

## 基本信息
- 任务标题：Demo 同步
- 任务编号：task_demo_001
- 当前状态：待开发
- 风险等级：L2

## 规划闭环
- 产品规格链接：`product-specs/demo.md`
- 执行计划链接：`exec-plans/active/demo-plan.md`

## 当前结论摘要
规划产物需要沉淀为长期上下文。
""".lstrip(),
                encoding="utf-8",
            )

            artifact = product / "harness-workspace" / "bmad-output" / "planning-artifacts" / "architecture.md"
            artifact.parent.mkdir(parents=True)
            artifact.write_text(
                """
# 架构蓝图

## 架构

采用 BMAD 输出的产品架构作为首批长期上下文。
""".lstrip(),
                encoding="utf-8",
            )
            design = product / "harness-workspace" / "bmad-output" / "design-artifacts" / "ux-flow.md"
            design.parent.mkdir(parents=True)
            design.write_text(
                """
# 交互设计

## 方案

把核心路径和异常状态作为 BMAD Planning 设计上下文。
""".lstrip(),
                encoding="utf-8",
            )

            layout = load_layout(ROOT, product)
            result = sync_planning(layout)
            context = layout.context_file.read_text(encoding="utf-8")

        self.assertEqual(result["product_specs"], 1)
        self.assertEqual(result["exec_plans"], 1)
        self.assertEqual(result["task_cards"], 1)
        self.assertEqual(result["bmad_artifacts"], 2)
        self.assertIn("BEGIN harness-engineering:planning-context", context)
        self.assertIn("Demo 产品蓝图", context)
        self.assertIn("执行计划：Demo", context)
        self.assertIn("Demo 同步", context)
        self.assertIn("架构蓝图", context)
        self.assertIn("交互设计", context)
        self.assertIn("采用 BMAD 输出的产品架构作为首批长期上下文", context)
        self.assertIn("核心路径和异常状态", context)

    def test_check_planning_blocks_when_context_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            write_project_config(product)
            write_minimal_spec(product)

            layout = load_layout(ROOT, product)
            stale = check_planning(layout)
            sync_planning(layout)
            fresh = check_planning(layout)

            write_minimal_spec(product, title="Demo 产品蓝图 v2")
            stale_after_change = check_planning(layout)

        self.assertFalse(stale["ok"])
        self.assertEqual("PLANNING_CONTEXT_STALE", stale["reason"])
        self.assertTrue(fresh["ok"])
        self.assertEqual("PLANNING_CONTEXT_OK", fresh["reason"])
        self.assertFalse(stale_after_change["ok"])
        self.assertEqual(1, stale_after_change["product_specs"])

    def test_sync_planning_indexes_collected_artifacts_beyond_twenty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            write_project_config(product)
            artifacts = product / "harness-workspace" / "bmad-output" / "planning-artifacts"
            artifacts.mkdir(parents=True)
            for index in range(21):
                (artifacts / f"artifact-{index:02d}.md").write_text(
                    f"# Planning Artifact {index:02d}\n",
                    encoding="utf-8",
                )

            layout = load_layout(ROOT, product)
            result = sync_planning(layout)
            context = layout.context_file.read_text(encoding="utf-8")

        self.assertEqual(21, result["bmad_artifacts"])
        self.assertIn("Planning Artifact 20", context)


if __name__ == "__main__":
    unittest.main()
