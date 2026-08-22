#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parents[1] / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from work_item_providers import (  # noqa: E402
    NoopProvider,
    WorkItem,
    WorkItemProvider,
    gate_check,
    sync_spec_markdown,
    work_item_drafts_from_spec,
)
from work_item_contract import work_item_contract_from_spec  # noqa: E402


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

    def create_subtask(self, parent_work_item_id: str, title: str, note: str = "") -> WorkItem:
        item_id = f"wi_{len(self.created) + 1:08d}"
        self.created.append({"id": item_id, "title": title, "note": note, "parent_id": parent_work_item_id})
        return WorkItem(id=item_id, title=title, note=note, provider=self.name)

    def update_status(self, work_item_id: str, status: str, note: str = "") -> tuple[bool, str]:
        return True, "ok"


class StrictHierarchyCaptureProvider(CaptureProvider):
    requires_l3_hierarchy_contract = True


class PlacementAwareCaptureProvider(CaptureProvider):
    def __init__(self, project_id: str = "") -> None:
        super().__init__()
        self.project_id = project_id
        self.binding_checks: list[dict[str, str | None]] = []

    def verify_binding(
        self,
        work_item_id: str,
        expected_project_id: str | None = None,
        expected_parent_id: str | None = None,
    ) -> tuple[bool, str]:
        self.binding_checks.append(
            {
                "work_item_id": work_item_id,
                "expected_project_id": expected_project_id,
                "expected_parent_id": expected_parent_id,
            }
        )
        return True, "CAPTURE_BINDING_OK"


class RejectBindingCaptureProvider(PlacementAwareCaptureProvider):
    def verify_binding(
        self,
        work_item_id: str,
        expected_project_id: str | None = None,
        expected_parent_id: str | None = None,
    ) -> tuple[bool, str]:
        super().verify_binding(work_item_id, expected_project_id, expected_parent_id)
        return False, "CAPTURE_BINDING_MISMATCH"


class FailSecondCreateProvider(CaptureProvider):
    def create(self, title: str, note: str = "", project_id: str | None = None) -> WorkItem:
        if self.created:
            raise RuntimeError("injected second create failure")
        return super().create(title, note, project_id)


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

    def test_diagnose_parent_without_id_blocks_before_provider_checks(self) -> None:
        output = subprocess.check_output(
            [
                "python3", str(SCRIPT_DIR / "work_item.py"),
                "--harness-root", str(SCRIPT_DIR.parents[1]),
                "diagnose", "--parent-id", "epic_parent_123",
            ],
            text=True,
        )
        data = json.loads(output)

        self.assertEqual("block", data["decision"])
        self.assertIn("WORK_ITEM_DIAGNOSE_PARENT_REQUIRES_ID", data["reason"])

    def test_feishu_draft_rejects_untyped_l3_spec_before_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            workspace = product / "harness-workspace"
            workspace.mkdir()
            (workspace / "project.yaml").write_text(
                "product:\n  id: demo\nworkspace:\n  root: harness-workspace\n"
                "work_item:\n  provider: feishu\n",
                encoding="utf-8",
            )
            spec = workspace / "planning" / "product-specs" / "untyped.md"
            spec.parent.mkdir(parents=True)
            spec.write_text(
                "---\nspec_level: L3\n---\n\n# Untyped\n\n- [ ] AC\n",
                encoding="utf-8",
            )
            output = subprocess.check_output(
                [
                    "python3", str(SCRIPT_DIR / "work_item.py"),
                    "--harness-root", str(SCRIPT_DIR.parents[1]),
                    "draft-spec", str(spec),
                ],
                cwd=product,
                env={**os.environ, "HARNESS_PRODUCT_ROOT": str(product), "WORK_ITEM_PROVIDER": "feishu"},
                text=True,
            )
        data = json.loads(output)

        self.assertEqual("block", data["decision"])
        self.assertIn("WORK_ITEM_TYPE_REQUIRED", data["reason"])

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

    def test_sync_spec_uses_front_matter_parent_for_subtasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            spec = product / "harness-workspace" / "planning" / "product-specs" / "story.md"
            spec.parent.mkdir(parents=True)
            spec.write_text(
                """
---
spec_level: L3
work_item_type: story
work_item_parent_id: epic_parent_123
---

# Story

## 验收标准

- [ ] 作为 Epic 子任务创建
""".lstrip(),
                encoding="utf-8",
            )
            provider = PlacementAwareCaptureProvider()

            count, message = sync_spec_markdown(provider, spec)

        self.assertEqual(1, count)
        self.assertEqual("epic_parent_123", provider.created[0]["parent_id"])
        self.assertEqual("epic_parent_123", provider.binding_checks[0]["expected_parent_id"])
        self.assertIn("parent=epic_parent_123", message)

    def test_sync_spec_rejects_conflicting_cli_and_spec_parents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp) / "story.md"
            spec.write_text(
                "---\nwork_item_parent_id: epic_parent_123\n---\n\n# Story\n\n- [ ] AC\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "WORK_ITEM_PARENT_CONFLICT"):
                sync_spec_markdown(CaptureProvider(), spec, parent_work_item_id="other_parent_456")

    def test_story_accepts_cli_parent_when_spec_parent_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp) / "story.md"
            spec.write_text(
                "---\nspec_level: L3\nwork_item_type: story\n---\n\n# Story\n\n- [ ] AC\n",
                encoding="utf-8",
            )
            provider = PlacementAwareCaptureProvider()

            sync_spec_markdown(provider, spec, parent_work_item_id="epic_parent_123")

        self.assertEqual("epic_parent_123", provider.created[0]["parent_id"])

    def test_epic_rejects_cli_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp) / "epic.md"
            spec.write_text(
                "---\nspec_level: L3\nwork_item_type: epic\n---\n\n# Epic\n\n- [ ] AC\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "WORK_ITEM_PARENT_FORBIDDEN"):
                sync_spec_markdown(
                    StrictHierarchyCaptureProvider(),
                    spec,
                    parent_work_item_id="epic_parent_123",
                )

    def test_strict_hierarchy_provider_requires_l3_work_item_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp) / "untyped.md"
            spec.write_text(
                "---\nspec_level: L3\n---\n\n# Untyped\n\n- [ ] AC\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "WORK_ITEM_TYPE_REQUIRED"):
                sync_spec_markdown(StrictHierarchyCaptureProvider(), spec)

    def test_noop_l3_without_hierarchy_contract_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp) / "local.md"
            spec.write_text(
                "---\nspec_level: L3\n---\n\n# Local\n\n- [ ] AC\n",
                encoding="utf-8",
            )
            provider = NoopProvider(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$")

            with self.assertRaisesRegex(ValueError, "WORK_ITEM_TYPE_REQUIRED"):
                sync_spec_markdown(provider, spec)

    def test_l3_task_without_parent_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp) / "task.md"
            spec.write_text(
                "---\nspec_level: L3\nwork_item_type: task\n---\n\n# Task\n\n- [ ] AC\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "WORK_ITEM_PARENT_REQUIRED"):
                sync_spec_markdown(NoopProvider(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$"), spec)

    def test_parent_sync_blocks_provider_without_placement_verification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp) / "story.md"
            original = (
                "---\nspec_level: L3\nwork_item_type: story\n"
                "work_item_parent_id: epic_parent_123\n---\n\n# Story\n\n- [ ] AC\n"
            )
            spec.write_text(original, encoding="utf-8")
            provider = CaptureProvider()

            with self.assertRaisesRegex(ValueError, "CAPTURE_BINDING_VERIFY_UNSUPPORTED"):
                sync_spec_markdown(provider, spec)

            self.assertEqual([], provider.created)
            self.assertEqual(original, spec.read_text(encoding="utf-8"))

    def test_project_sync_blocks_provider_without_placement_verification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp) / "feature.md"
            original = "---\nspec_level: L2\n---\n\n# Feature\n\n- [ ] AC\n"
            spec.write_text(original, encoding="utf-8")
            provider = CaptureProvider()
            provider.project_id = "project_123"

            with self.assertRaisesRegex(ValueError, "CAPTURE_BINDING_VERIFY_UNSUPPORTED"):
                sync_spec_markdown(provider, spec)

            self.assertEqual([], provider.created)
            self.assertEqual(original, spec.read_text(encoding="utf-8"))

    def test_sync_does_not_write_id_when_post_create_binding_check_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp) / "feature.md"
            original = "---\nspec_level: L2\n---\n\n# Feature\n\n- [ ] AC\n"
            spec.write_text(original, encoding="utf-8")
            provider = RejectBindingCaptureProvider(project_id="project_123")

            with self.assertRaisesRegex(RuntimeError, "CAPTURE_BINDING_MISMATCH"):
                sync_spec_markdown(provider, spec)

            self.assertEqual(1, len(provider.created))
            self.assertEqual(original, spec.read_text(encoding="utf-8"))

    def test_noop_story_parent_uses_explicit_guarded_local_semantics(self) -> None:
        provider = NoopProvider(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$")
        item = provider.create_subtask("epic_parent_123", "Local Story")

        ok, reason = provider.verify_binding(item.id, expected_parent_id="epic_parent_123")

        self.assertTrue(ok)
        self.assertEqual("epic_parent_123", item.raw["parent_work_item_id"])
        self.assertIn("assurance=guarded", reason)
        self.assertNotIn("enforced", reason)

    def test_malformed_front_matter_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp) / "malformed.md"
            spec.write_text("---\nwork_item: [\n---\n\n# Broken\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "WORK_ITEM_FRONT_MATTER_INVALID"):
                work_item_contract_from_spec(spec, require_l3_type=True)

    def test_conflicting_parent_aliases_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp) / "conflicting-parent.md"
            spec.write_text(
                "---\nwork_item_parent_id: epic_one\n"
                "work_item:\n  parent_id: epic_two\n---\n\n# Conflict\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "WORK_ITEM_PARENT_ALIAS_CONFLICT"):
                work_item_contract_from_spec(spec)

    def test_conflicting_type_aliases_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp) / "conflicting-type.md"
            spec.write_text(
                "---\nwork_item_type: story\n"
                "work_item:\n  type: epic\n---\n\n# Conflict\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "WORK_ITEM_TYPE_ALIAS_CONFLICT"):
                work_item_contract_from_spec(spec)

    def test_non_mapping_work_item_metadata_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp) / "invalid-work-item.md"
            spec.write_text(
                "---\nwork_item: unexpected\n---\n\n# Invalid\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "WORK_ITEM_METADATA_INVALID"):
                work_item_contract_from_spec(spec)

    def test_non_scalar_parent_alias_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp) / "invalid-parent.md"
            spec.write_text(
                "---\nwork_item_parent_id:\n  - epic_one\n---\n\n# Invalid\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "WORK_ITEM_PARENT_INVALID"):
                work_item_contract_from_spec(spec)

    def test_real_provider_requirement_is_carried_into_verified_work_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            product = root / "product"
            workspace = product / "harness-workspace"
            task_dir = workspace / "planning" / "tasks" / "task"
            spec = workspace / "planning" / "product-specs" / "story.md"
            task_dir.mkdir(parents=True)
            spec.parent.mkdir(parents=True)
            spec.write_text(
                "---\nspec_level: L3\nwork_item_type: story\n"
                "work_item_parent_id: epic_parent_123\n"
                "production_evidence:\n  provider_mode: real_required\n"
                "---\n\n# Story\n",
                encoding="utf-8",
            )
            (task_dir / "00-任务卡.md").write_text(
                "Work Item ID: task_123456\n"
                "产品规格链接: `harness-workspace/planning/product-specs/story.md`\n",
                encoding="utf-8",
            )
            provider = PlacementAwareCaptureProvider()
            with (
                patch("work_item_contract.load_config", return_value={"requirements": {"L3": True}}),
                patch("work_item_contract.get_provider", return_value=provider),
                patch("work_item_contract.active_product_root", return_value=product),
            ):
                result = gate_check(root, "L3", str(task_dir))

        self.assertTrue(result["ok"])
        self.assertEqual(
            result["work_item"]["production_evidence"],
            {"provider_mode": "real_required"},
        )

    def test_gate_accepts_planning_relative_product_spec_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            product = root / "product"
            workspace = product / "harness-workspace"
            task_dir = workspace / "planning" / "tasks" / "task"
            spec = workspace / "planning" / "product-specs" / "story.md"
            task_dir.mkdir(parents=True)
            spec.parent.mkdir(parents=True)
            spec.write_text(
                "---\nspec_level: L3\nwork_item_type: story\n"
                "work_item_parent_id: epic_parent_123\n---\n\n# Story\n",
                encoding="utf-8",
            )
            (task_dir / "00-任务卡.md").write_text(
                "Work Item ID: task_123456\n"
                "产品规格链接: `product-specs/story.md`\n",
                encoding="utf-8",
            )
            provider = PlacementAwareCaptureProvider()
            with (
                patch("work_item_contract.load_config", return_value={"requirements": {"L3": True}}),
                patch("work_item_contract.get_provider", return_value=provider),
                patch("work_item_contract.active_product_root", return_value=product),
            ):
                result = gate_check(root, "L3", str(task_dir))

        self.assertTrue(result["ok"])
        self.assertEqual(
            result["work_item"]["product_spec"],
            "harness-workspace/planning/product-specs/story.md",
        )

    def test_gate_rejects_product_spec_links_outside_supported_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            product = root / "product"
            workspace = product / "harness-workspace"
            task_dir = workspace / "planning" / "tasks" / "task"
            spec = workspace / "planning" / "product-specs" / "story.md"
            task_dir.mkdir(parents=True)
            spec.parent.mkdir(parents=True)
            spec.write_text(
                "---\nspec_level: L3\nwork_item_type: story\n"
                "work_item_parent_id: epic_parent_123\n---\n\n# Story\n",
                encoding="utf-8",
            )
            provider = PlacementAwareCaptureProvider()
            invalid_links = (
                str(spec),
                "../product-specs/story.md",
                "docs/product-specs/story.md",
                "product-specs/../product-specs/story.md",
                "product-specs-similar/story.md",
                "harness-workspace/planning/product-specs/../../outside.md",
            )
            for link in invalid_links:
                with self.subTest(link=link):
                    (task_dir / "00-任务卡.md").write_text(
                        "Work Item ID: task_123456\n"
                        f"产品规格链接: `{link}`\n",
                        encoding="utf-8",
                    )
                    with (
                        patch("work_item_contract.load_config", return_value={"requirements": {"L3": True}}),
                        patch("work_item_contract.get_provider", return_value=provider),
                        patch("work_item_contract.active_product_root", return_value=product),
                    ):
                        result = gate_check(root, "L3", str(task_dir))

                    self.assertFalse(result["ok"])
                    self.assertTrue(
                        any("WORK_ITEM_SPEC_PATH_INVALID" in failure for failure in result["failures"]),
                        result,
                    )

    def test_gate_rejects_product_spec_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            product = root / "product"
            workspace = product / "harness-workspace"
            task_dir = workspace / "planning" / "tasks" / "task"
            specs = workspace / "planning" / "product-specs"
            outside = product / "outside.md"
            task_dir.mkdir(parents=True)
            specs.mkdir(parents=True)
            outside.write_text(
                "---\nspec_level: L3\nwork_item_type: story\n"
                "work_item_parent_id: epic_parent_123\n---\n\n# Outside\n",
                encoding="utf-8",
            )
            (specs / "escape.md").symlink_to(outside)
            (task_dir / "00-任务卡.md").write_text(
                "Work Item ID: task_123456\n"
                "产品规格链接: `product-specs/escape.md`\n",
                encoding="utf-8",
            )
            provider = PlacementAwareCaptureProvider()
            with (
                patch("work_item_contract.load_config", return_value={"requirements": {"L3": True}}),
                patch("work_item_contract.get_provider", return_value=provider),
                patch("work_item_contract.active_product_root", return_value=product),
            ):
                result = gate_check(root, "L3", str(task_dir))

        self.assertFalse(result["ok"])
        self.assertTrue(
            any("WORK_ITEM_SPEC_OUTSIDE_PRODUCT_SPECS" in failure for failure in result["failures"]),
            result,
        )

    def test_unknown_production_provider_mode_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp) / "invalid-production-evidence.md"
            spec.write_text(
                "---\nproduction_evidence:\n  provider_mode: inferred\n---\n\n# Invalid\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "PRODUCTION_EVIDENCE_PROVIDER_MODE_INVALID"):
                work_item_contract_from_spec(spec)

    def test_partial_multi_item_sync_persists_each_created_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp) / "partial.md"
            spec.write_text("# Partial\n\n- [ ] First\n- [ ] Second\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "injected second create failure"):
                sync_spec_markdown(FailSecondCreateProvider(), spec)
            updated = spec.read_text(encoding="utf-8")

        self.assertIn("- [ ] First #wi_00000001", updated)
        self.assertIn("- [ ] Second", updated)

    def test_gate_fails_when_bound_product_spec_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            product = root / "product"
            task_dir = product / "harness-workspace" / "planning" / "tasks" / "task"
            task_dir.mkdir(parents=True)
            (task_dir / "00-任务卡.md").write_text(
                "Work Item ID: task_123456\n"
                "产品规格链接: `harness-workspace/planning/product-specs/missing.md`\n",
                encoding="utf-8",
            )
            provider = NoopProvider(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$")
            with (
                patch("work_item_contract.load_config", return_value={"requirements": {"L3": True}}),
                patch("work_item_contract.get_provider", return_value=provider),
                patch("work_item_contract.active_product_root", return_value=product),
            ):
                result = gate_check(root, "L3", str(task_dir))

        self.assertFalse(result["ok"])
        self.assertTrue(any("WORK_ITEM_SPEC_MISSING" in failure for failure in result["failures"]))

    def test_l3_gate_rejects_l2_product_spec(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            product = root / "product"
            workspace = product / "harness-workspace"
            task_dir = workspace / "planning" / "tasks" / "task"
            spec = workspace / "planning" / "product-specs" / "wrong-level.md"
            task_dir.mkdir(parents=True)
            spec.parent.mkdir(parents=True)
            spec.write_text("---\nspec_level: L2\n---\n\n# Wrong level\n", encoding="utf-8")
            (task_dir / "00-任务卡.md").write_text(
                "Work Item ID: task_123456\n"
                "产品规格链接: `harness-workspace/planning/product-specs/wrong-level.md`\n",
                encoding="utf-8",
            )
            provider = NoopProvider(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$")
            with (
                patch("work_item_contract.load_config", return_value={"requirements": {"L3": True}}),
                patch("work_item_contract.get_provider", return_value=provider),
                patch("work_item_contract.active_product_root", return_value=product),
            ):
                result = gate_check(root, "L3", str(task_dir))

        self.assertFalse(result["ok"])
        self.assertTrue(any("WORK_ITEM_SPEC_LEVEL_MISMATCH" in failure for failure in result["failures"]))

    def test_l3_gate_rejects_untyped_spec_with_no_parent_under_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            product = root / "product"
            workspace = product / "harness-workspace"
            task_dir = workspace / "planning" / "tasks" / "task"
            spec = workspace / "planning" / "product-specs" / "untyped.md"
            task_dir.mkdir(parents=True)
            spec.parent.mkdir(parents=True)
            spec.write_text("---\nspec_level: L3\n---\n\n# Untyped\n", encoding="utf-8")
            (task_dir / "00-任务卡.md").write_text(
                "Work Item ID: task_123456\n"
                "产品规格链接: `harness-workspace/planning/product-specs/untyped.md`\n",
                encoding="utf-8",
            )
            provider = NoopProvider(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$")
            with (
                patch("work_item_contract.load_config", return_value={"requirements": {"L3": True}}),
                patch("work_item_contract.get_provider", return_value=provider),
                patch("work_item_contract.active_product_root", return_value=product),
            ):
                result = gate_check(root, "L3", str(task_dir))

        self.assertFalse(result["ok"])
        self.assertTrue(any("WORK_ITEM_TYPE_REQUIRED" in failure for failure in result["failures"]))

    def test_gate_blocks_project_binding_without_placement_verification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            product = root / "product"
            workspace = product / "harness-workspace"
            task_dir = workspace / "planning" / "tasks" / "task"
            spec = workspace / "planning" / "product-specs" / "feature.md"
            task_dir.mkdir(parents=True)
            spec.parent.mkdir(parents=True)
            spec.write_text("---\nspec_level: L2\n---\n\n# Feature\n", encoding="utf-8")
            (task_dir / "00-任务卡.md").write_text(
                "Work Item ID: task_123456\n"
                "产品规格链接: `harness-workspace/planning/product-specs/feature.md`\n",
                encoding="utf-8",
            )
            provider = CaptureProvider()
            provider.project_id = "project_123"
            with (
                patch("work_item_contract.load_config", return_value={"requirements": {"L2": True}}),
                patch("work_item_contract.get_provider", return_value=provider),
                patch("work_item_contract.active_product_root", return_value=product),
            ):
                result = gate_check(root, "L2", str(task_dir))

        self.assertFalse(result["ok"])
        self.assertIn("CAPTURE_BINDING_VERIFY_UNSUPPORTED", result["failures"])

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
