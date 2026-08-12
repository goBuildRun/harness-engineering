#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from work_item_providers import NoopProvider, WorkItem, WorkItemProvider, sync_spec_markdown, work_item_drafts_from_spec  # noqa: E402


class CaptureProvider(WorkItemProvider):
    name = "capture"

    def __init__(self) -> None:
        self.created: list[dict[str, str]] = []

    def verify(self, work_item_id: str) -> tuple[bool, str]:
        return True, "ok"

    def pull(self, work_item_id: str) -> WorkItem:
        return WorkItem(id=work_item_id, title="captured", provider=self.name)

    def create(self, title: str, note: str = "", project_id: str | None = None) -> WorkItem:
        item_id = f"wi_{len(self.created) + 1:08d}"
        self.created.append({"id": item_id, "title": title, "note": note})
        return WorkItem(id=item_id, title=title, note=note, provider=self.name)

    def update_status(self, work_item_id: str, status: str, note: str = "") -> tuple[bool, str]:
        return True, "ok"


class WorkItemSyncContractTest(unittest.TestCase):
    def test_capabilities_command_remains_routed_after_diagnostics_split(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            workspace = product / "harness-workspace"
            workspace.mkdir()
            (workspace / "project.yaml").write_text(
                "product:\n  id: demo\nworkspace:\n  root: harness-workspace\n"
                "work_item:\n  provider: noop\n",
                encoding="utf-8",
            )
            output = subprocess.check_output(
                [
                    "python3", str(SCRIPT_DIR / "work_item.py"),
                    "--harness-root", str(SCRIPT_DIR.parents[1]), "capabilities",
                ],
                cwd=product,
                env={**os.environ, "HARNESS_PRODUCT_ROOT": str(product), "WORK_ITEM_PROVIDER": "noop"},
                text=True,
            )
        data = json.loads(output)
        self.assertEqual(data["decision"], "pass")
        self.assertEqual(data["capabilities"]["verify"], "local")

    def test_noop_provider_accepts_and_generates_semantic_local_ids(self) -> None:
        provider = NoopProvider(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$")

        ok, _ = provider.verify("policy-guardrails-provider-poc")
        item = provider.create("Policy Guardrails Provider PoC")

        self.assertTrue(ok)
        self.assertRegex(item.id, r"^policy-guardrails-provider-poc-[0-9a-f]{8}$")
        self.assertTrue(provider.verify(item.id)[0])

    def test_update_description_cli_blocks_when_source_file_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            workspace = product / "harness-workspace"
            workspace.mkdir(parents=True)
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
  id_pattern: "^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$"
""".lstrip(),
                encoding="utf-8",
            )
            out = subprocess.check_output(
                [
                    "python3",
                    str(SCRIPT_DIR / "work_item.py"),
                    "--harness-root",
                    str(SCRIPT_DIR.parents[1]),
                    "update-description",
                    "--id",
                    "demo-task",
                    "--file",
                    str(product / "missing.md"),
                ],
                cwd=product,
                env={**os.environ, "HARNESS_PRODUCT_ROOT": str(product)},
                text=True,
            )

        data = json.loads(out)
        self.assertEqual("block", data["decision"])
        self.assertIn("WORK_ITEM_DESCRIPTION_FILE_MISSING", data["reason"])

    def test_sync_spec_creates_structured_bmad_work_item_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            spec = product / "harness-workspace" / "planning" / "product-specs" / "demo.md"
            spec.parent.mkdir(parents=True)
            spec.write_text(
                """
---
bmad_method: true
bmad_skills:
  - bmad-create-prd
  - bmad-validate-prd
spec_level: L2
---

# Demo 登录

## 目标

让用户可以用手机号登录。

## 验收标准

- [ ] 手机号登录成功后进入首页

## 非目标

- 不做第三方登录
""".lstrip(),
                encoding="utf-8",
            )

            provider = CaptureProvider()
            count, message = sync_spec_markdown(provider, spec, assignee="ou_demo")
            updated = spec.read_text(encoding="utf-8")

        self.assertEqual(count, 1)
        self.assertIn("bmad-work-item-v1", message)
        self.assertIn("#wi_00000001", updated)
        self.assertEqual(provider.created[0]["title"], "手机号登录成功后进入首页")
        note = provider.created[0]["note"]
        self.assertIn("## Harness Links", note)
        self.assertIn("Product Spec: `harness-workspace/planning/product-specs/demo.md`", note)
        self.assertIn("BMAD Planning: pending", note)
        self.assertIn("Planning Gate: pending", note)
        self.assertIn("Harness Execution: not_started", note)
        self.assertIn("Assignee: `ou_demo`", note)
        self.assertIn("完整 BMAD 产物以产品仓库为真相源", note)
        self.assertIn("不做第三方登录", note)

    def test_draft_spec_generates_confirmation_payload_without_modifying_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            spec = product / "harness-workspace" / "planning" / "product-specs" / "demo.md"
            spec.parent.mkdir(parents=True)
            original = """
# Demo 支付

## 目标

让用户完成订单支付。

## 验收标准

- [ ] 创建待支付订单
- [ ] 支付成功后回写订单状态 #existing_12345678
- [ ] 支付失败时保留重试入口
""".lstrip()
            spec.write_text(original, encoding="utf-8")

            drafts = work_item_drafts_from_spec(spec, assignee="user_001")
            after = spec.read_text(encoding="utf-8")

        self.assertEqual(after, original)
        self.assertEqual([d["draft_id"] for d in drafts], ["D001", "D002"])
        self.assertEqual(drafts[0]["title"], "创建待支付订单")
        self.assertEqual(drafts[1]["title"], "支付失败时保留重试入口")
        self.assertEqual(drafts[0]["assignee"], "user_001")
        self.assertEqual(drafts[0]["contract"], "bmad-work-item-v1")
        self.assertIn("Assignee: `user_001`", drafts[0]["note"])

    def test_close_syncs_planning_context_after_status_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            workspace = product / "harness-workspace"
            workspace.mkdir(parents=True)
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
  id_pattern: "^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$"
""".lstrip(),
                encoding="utf-8",
            )
            spec = workspace / "planning" / "product-specs" / "demo.md"
            spec.parent.mkdir(parents=True)
            spec.write_text("# Demo 产品蓝图\n\n## 目标\n\n沉淀 close 后的任务边界。\n", encoding="utf-8")

            out = subprocess.check_output(
                [
                    "python3",
                    str(SCRIPT_DIR / "work_item.py"),
                    "--harness-root",
                    str(SCRIPT_DIR.parents[1]),
                    "close",
                    "--id",
                    "demo-close-task",
                    "--status",
                    "ready_to_release",
                ],
                cwd=product,
                env={**os.environ, "HARNESS_PRODUCT_ROOT": str(product)},
                text=True,
            )
            data = json.loads(out)
            context = (workspace / "knowledge" / "CONTEXT.md").read_text(encoding="utf-8")

        self.assertEqual("pass", data["decision"])
        self.assertIn("knowledge_sync", data)
        self.assertEqual(1, data["knowledge_sync"]["product_specs"])
        self.assertIn("Demo 产品蓝图", context)


if __name__ == "__main__":
    unittest.main()
