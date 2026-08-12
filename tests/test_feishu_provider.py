#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parents[1] / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from work_item_providers import FeishuProvider  # noqa: E402


ID_PATTERN = r"^[A-Za-z0-9_-]{8,128}$"


class FakeResponse:
    def __init__(self, body: dict) -> None:
        self.body = json.dumps(body).encode()

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class UrlopenRecorder:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.calls = []

    def __call__(self, request, timeout: int = 30):  # type: ignore[no-untyped-def]
        self.calls.append(request)
        if not self.responses:
            raise AssertionError("No fake Feishu response left")
        return FakeResponse(self.responses.pop(0))

    @staticmethod
    def body(request) -> dict:  # type: ignore[no-untyped-def]
        if request.data is None:
            return {}
        return json.loads(request.data.decode())


def provider(cfg: dict | None = None) -> FeishuProvider:
    return FeishuProvider(
        {
            "api_host": "https://open.feishu.cn",
            "tasklist_guid": "cfg_tasklist",
            **(cfg or {}),
        },
        ID_PATTERN,
    )


class FeishuProviderTest(unittest.TestCase):
    def test_missing_env_blocks_without_network(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            p = provider()
            ok, reason = p.verify("task_guid_123")
        self.assertFalse(ok)
        self.assertIn("FEISHU_MISSING_ENV", reason)

    def test_tenant_token_request_and_cache(self) -> None:
        fake = UrlopenRecorder([
            {"code": 0, "tenant_access_token": "token-1", "expire": 7200},
        ])
        with patch.dict(os.environ, {"FEISHU_APP_ID": "app-id", "FEISHU_APP_SECRET": "app-secret"}, clear=True):
            p = provider()
            with patch("urllib.request.urlopen", fake):
                self.assertEqual(p._tenant_access_token(), "token-1")
                self.assertEqual(p._tenant_access_token(), "token-1")

        self.assertEqual(len(fake.calls), 1)
        call = fake.calls[0]
        self.assertEqual(call.get_method(), "POST")
        self.assertTrue(call.full_url.endswith("/open-apis/auth/v3/tenant_access_token/internal"))
        self.assertEqual(fake.body(call), {"app_id": "app-id", "app_secret": "app-secret"})

    def test_create_uses_tasklist_and_assignee(self) -> None:
        fake = UrlopenRecorder(
            [
                {"code": 0, "tenant_access_token": "token-1", "expire": 7200},
                {"code": 0, "data": {"items": []}},
                {"code": 0, "data": {"task": {"guid": "task_guid_123", "status": "todo"}}},
            ]
        )
        env = {
            "FEISHU_APP_ID": "app-id",
            "FEISHU_APP_SECRET": "app-secret",
            "FEISHU_TASKLIST_GUID": "env_tasklist",
            "FEISHU_ASSIGNEE_ID": "ou_user_1",
        }
        with patch.dict(os.environ, env, clear=True):
            p = provider()
            with patch("urllib.request.urlopen", fake):
                item = p.create("新功能验收", "- AC1\n- AC2")

        self.assertEqual(item.id, "task_guid_123")
        self.assertEqual(item.provider, "feishu")
        self.assertEqual([call.get_method() for call in fake.calls], ["POST", "GET", "POST"])
        create_call = fake.calls[2]
        self.assertEqual(create_call.get_method(), "POST")
        self.assertTrue(create_call.full_url.endswith("/open-apis/task/v2/tasks"))
        body = fake.body(create_call)
        self.assertEqual(body["summary"], "新功能验收")
        self.assertEqual(body["description"], "- AC1\n- AC2")
        self.assertEqual(body["tasklists"], [{"tasklist_guid": "env_tasklist"}])
        self.assertEqual(body["members"], [{"id": "ou_user_1", "role": "assignee"}])

    def test_create_uses_product_default_assignee_when_env_absent(self) -> None:
        fake = UrlopenRecorder(
            [
                {"code": 0, "tenant_access_token": "token-1", "expire": 7200},
                {"code": 0, "data": {"items": []}},
                {"code": 0, "data": {"task": {"guid": "task_guid_123", "status": "todo"}}},
            ]
        )
        with patch.dict(os.environ, {"FEISHU_APP_ID": "app-id", "FEISHU_APP_SECRET": "app-secret"}, clear=True):
            p = provider({"assignee_id": "ou_default_owner"})
            with patch("urllib.request.urlopen", fake):
                p.create("默认负责人验证")

        body = fake.body(fake.calls[2])
        self.assertEqual(body["tasklists"], [{"tasklist_guid": "cfg_tasklist"}])
        self.assertEqual(body["members"], [{"id": "ou_default_owner", "role": "assignee"}])

    def test_ensure_tasklist_member_adds_current_app_as_editor(self) -> None:
        fake = UrlopenRecorder(
            [
                {"code": 0, "tenant_access_token": "token-1", "expire": 7200},
                {"code": 0, "data": {"tasklist": {"guid": "cfg_tasklist"}}},
            ]
        )
        env = {"FEISHU_APP_ID": "cli_app_1", "FEISHU_APP_SECRET": "app-secret"}
        with patch.dict(os.environ, env, clear=True):
            p = provider()
            with patch("urllib.request.urlopen", fake):
                p.ensure_tasklist_member()

        call = fake.calls[1]
        self.assertEqual(call.get_method(), "POST")
        self.assertTrue(call.full_url.endswith("/open-apis/task/v2/tasklists/cfg_tasklist/add_members"))
        self.assertEqual(fake.body(call), {"members": [{"id": "cli_app_1", "type": "app", "role": "editor"}]})

    def test_pull_extracts_title_note_status_and_acceptance_criteria(self) -> None:
        fake = UrlopenRecorder(
            [
                {"code": 0, "tenant_access_token": "token-1", "expire": 7200},
                {
                    "code": 0,
                    "data": {
                        "task": {
                            "guid": "task_guid_123",
                            "summary": "已有项目接入",
                            "status": "todo",
                            "description": "- 已完成接入扫描\n- 已确认飞书任务",
                            "url": "https://example.test/task_guid_123",
                        }
                    },
                },
            ]
        )
        with patch.dict(os.environ, {"FEISHU_APP_ID": "app-id", "FEISHU_APP_SECRET": "app-secret"}, clear=True):
            p = provider()
            with patch("urllib.request.urlopen", fake):
                item = p.pull("task_guid_123")

        self.assertEqual(item.id, "task_guid_123")
        self.assertEqual(item.title, "已有项目接入")
        self.assertEqual(item.status, "todo")
        self.assertEqual(item.acceptance_criteria, ["已完成接入扫描", "已确认飞书任务"])
        get_call = fake.calls[1]
        self.assertEqual(get_call.get_method(), "GET")
        self.assertTrue(get_call.full_url.endswith("/open-apis/task/v2/tasks/task_guid_123"))

    def test_pull_reports_done_when_completed_at_is_set(self) -> None:
        fake = UrlopenRecorder(
            [
                {"code": 0, "tenant_access_token": "token-1", "expire": 7200},
                {
                    "code": 0,
                    "data": {
                        "task": {
                            "guid": "task_guid_123",
                            "summary": "已完成任务",
                            "status": "todo",
                            "completed_at": "1782803000000",
                            "description": "- AC1",
                        }
                    },
                },
            ]
        )
        with patch.dict(os.environ, {"FEISHU_APP_ID": "app-id", "FEISHU_APP_SECRET": "app-secret"}, clear=True):
            p = provider()
            with patch("urllib.request.urlopen", fake):
                item = p.pull("task_guid_123")

        self.assertEqual(item.status, "done")

    def test_update_status_skip_verifies_but_does_not_patch(self) -> None:
        fake = UrlopenRecorder(
            [
                {"code": 0, "tenant_access_token": "token-1", "expire": 7200},
                {"code": 0, "data": {"task": {"guid": "task_guid_123", "summary": "任务", "status": "todo"}}},
            ]
        )
        with patch.dict(os.environ, {"FEISHU_APP_ID": "app-id", "FEISHU_APP_SECRET": "app-secret"}, clear=True):
            p = provider({"status_update_mode": "skip"})
            with patch("urllib.request.urlopen", fake):
                ok, reason = p.update_status("task_guid_123", "done")

        self.assertTrue(ok)
        self.assertIn("FEISHU_STATUS_SKIP", reason)
        self.assertEqual([call.get_method() for call in fake.calls], ["POST", "GET"])

    def test_update_status_completed_mode_marks_task_done(self) -> None:
        fake = UrlopenRecorder(
            [
                {"code": 0, "tenant_access_token": "token-1", "expire": 7200},
                {"code": 0, "data": {"task": {"guid": "task_guid_123", "summary": "任务", "status": "todo"}}},
                {"code": 0, "data": {"task": {"guid": "task_guid_123", "completed_at": "1782803000000"}}},
            ]
        )
        with patch.dict(os.environ, {"FEISHU_APP_ID": "app-id", "FEISHU_APP_SECRET": "app-secret"}, clear=True):
            p = provider({"status_update_mode": "completed"})
            with patch("urllib.request.urlopen", fake), patch("time.time", return_value=1782803000):
                ok, reason = p.update_status("task_guid_123", "done", "Harness complete")

        self.assertTrue(ok)
        self.assertIn("FEISHU_UPDATED", reason)
        self.assertIn("completed_at=1782803000000", reason)
        self.assertEqual([call.get_method() for call in fake.calls], ["POST", "GET", "PATCH"])
        patch_body = fake.body(fake.calls[2])
        self.assertEqual(patch_body, {"task": {"completed_at": "1782803000000"}, "update_fields": ["completed_at"]})

    def test_update_status_completed_mode_reopens_in_progress_task(self) -> None:
        fake = UrlopenRecorder(
            [
                {"code": 0, "tenant_access_token": "token-1", "expire": 7200},
                {
                    "code": 0,
                    "data": {
                        "task": {
                            "guid": "task_guid_123",
                            "summary": "任务",
                            "status": "todo",
                            "completed_at": "1782803000000",
                        }
                    },
                },
                {"code": 0, "data": {"task": {"guid": "task_guid_123", "completed_at": "0"}}},
            ]
        )
        with patch.dict(os.environ, {"FEISHU_APP_ID": "app-id", "FEISHU_APP_SECRET": "app-secret"}, clear=True):
            p = provider({"status_update_mode": "completed"})
            with patch("urllib.request.urlopen", fake):
                ok, reason = p.update_status("task_guid_123", "in_progress")

        self.assertTrue(ok)
        self.assertIn("completed_at=0", reason)
        self.assertEqual(fake.body(fake.calls[2]), {"task": {"completed_at": "0"}, "update_fields": ["completed_at"]})

    def test_update_status_completed_mode_rejects_unknown_status(self) -> None:
        fake = UrlopenRecorder(
            [
                {"code": 0, "tenant_access_token": "token-1", "expire": 7200},
                {"code": 0, "data": {"task": {"guid": "task_guid_123", "summary": "任务", "status": "todo"}}},
            ]
        )
        with patch.dict(os.environ, {"FEISHU_APP_ID": "app-id", "FEISHU_APP_SECRET": "app-secret"}, clear=True):
            p = provider({"status_update_mode": "completed"})
            with patch("urllib.request.urlopen", fake):
                ok, reason = p.update_status("task_guid_123", "ready_to_release")

        self.assertFalse(ok)
        self.assertIn("FEISHU_STATUS_UNSUPPORTED", reason)
        self.assertEqual([call.get_method() for call in fake.calls], ["POST", "GET"])

    def test_update_description_verifies_and_patches_only_description(self) -> None:
        fake = UrlopenRecorder(
            [
                {"code": 0, "tenant_access_token": "token-1", "expire": 7200},
                {"code": 0, "data": {"task": {"guid": "task_guid_123", "summary": "任务", "status": "todo"}}},
                {"code": 0, "data": {"task": {"guid": "task_guid_123"}}},
            ]
        )
        description = "## Harness Links\n- Product Spec: `spec.md`\n\n## Gate\n- Planning Gate: passed"
        with patch.dict(os.environ, {"FEISHU_APP_ID": "app-id", "FEISHU_APP_SECRET": "app-secret"}, clear=True):
            p = provider({"status_update_mode": "completed"})
            with patch("urllib.request.urlopen", fake):
                ok, reason = p.update_description("task_guid_123", description)

        self.assertTrue(ok)
        self.assertIn("FEISHU_DESCRIPTION_UPDATED", reason)
        self.assertEqual([call.get_method() for call in fake.calls], ["POST", "GET", "PATCH"])
        self.assertEqual(
            fake.body(fake.calls[2]),
            {"task": {"description": description}, "update_fields": ["description"]},
        )

    def test_update_description_rejects_empty_content_without_network(self) -> None:
        with patch.dict(os.environ, {"FEISHU_APP_ID": "app-id", "FEISHU_APP_SECRET": "app-secret"}, clear=True):
            p = provider()
            ok, reason = p.update_description("task_guid_123", "  \n")

        self.assertFalse(ok)
        self.assertEqual("FEISHU_DESCRIPTION_EMPTY", reason)

    def test_update_title_verifies_and_patches_only_summary(self) -> None:
        fake = UrlopenRecorder(
            [
                {"code": 0, "tenant_access_token": "token-1", "expire": 7200},
                {"code": 0, "data": {"task": {"guid": "task_guid_123", "summary": "旧标题"}}},
                {"code": 0, "data": {"task": {"guid": "task_guid_123", "summary": "Epic 40"}}},
            ]
        )
        with patch.dict(os.environ, {"FEISHU_APP_ID": "app-id", "FEISHU_APP_SECRET": "app-secret"}, clear=True):
            p = provider()
            with patch("urllib.request.urlopen", fake):
                ok, reason = p.update_title("task_guid_123", "Epic 40")

        self.assertTrue(ok)
        self.assertEqual("FEISHU_TITLE_UPDATED", reason)
        self.assertEqual(
            fake.body(fake.calls[2]),
            {"task": {"summary": "Epic 40"}, "update_fields": ["summary"]},
        )

    def test_create_subtask_posts_under_parent_and_verifies_parent_guid(self) -> None:
        fake = UrlopenRecorder(
            [
                {"code": 0, "tenant_access_token": "token-1", "expire": 7200},
                {"code": 0, "data": {"task": {"guid": "parent_guid_456", "summary": "Epic"}}},
                {"code": 0, "data": {"subtask": {"guid": "task_guid_123", "summary": "Story"}}},
                {
                    "code": 0,
                    "data": {
                        "task": {
                            "guid": "task_guid_123",
                            "summary": "Story",
                            "parent_task_guid": "parent_guid_456",
                        }
                    },
                },
            ]
        )
        with patch.dict(os.environ, {"FEISHU_APP_ID": "app-id", "FEISHU_APP_SECRET": "app-secret"}, clear=True):
            p = provider()
            with patch("urllib.request.urlopen", fake):
                item = p.create_subtask("parent_guid_456", "Story", "Harness child")

        self.assertEqual("task_guid_123", item.id)
        self.assertEqual(fake.calls[2].get_method(), "POST")
        self.assertTrue(fake.calls[2].full_url.endswith("/open-apis/task/v2/tasks/parent_guid_456/subtasks"))
        self.assertEqual(
            fake.body(fake.calls[2]),
            {"summary": "Story", "description": "Harness child"},
        )

    def test_create_subtask_rejects_invalid_parent_without_network(self) -> None:
        with patch.dict(os.environ, {"FEISHU_APP_ID": "app-id", "FEISHU_APP_SECRET": "app-secret"}, clear=True):
            p = provider()
            with self.assertRaisesRegex(ValueError, "FEISHU_INVALID_PARENT_ID"):
                p.create_subtask("bad", "Story")

    def test_list_assigned_filters_tasklist_by_assignee(self) -> None:
        fake = UrlopenRecorder(
            [
                {"code": 0, "tenant_access_token": "token-1", "expire": 7200},
                {
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "guid": "task_guid_123",
                                "summary": "分派给我",
                                "completed_at": "0",
                                "description": "- AC1",
                                "members": [{"id": "ou_user_1", "role": "assignee"}],
                            },
                            {
                                "guid": "task_guid_456",
                                "summary": "分派给别人",
                                "status": "todo",
                                "members": [{"id": "ou_user_2", "role": "assignee"}],
                            },
                            {
                                "guid": "task_guid_done",
                                "summary": "已经完成",
                                "status": "todo",
                                "completed_at": "1782803000000",
                                "members": [{"id": "ou_user_1", "role": "assignee"}],
                            },
                        ]
                    },
                },
            ]
        )
        env = {"FEISHU_APP_ID": "app-id", "FEISHU_APP_SECRET": "app-secret"}
        with patch.dict(os.environ, env, clear=True):
            p = provider({"assignee_id": "ou_user_1"})
            with patch("urllib.request.urlopen", fake):
                items = p.list_assigned()

        self.assertEqual([i.id for i in items], ["task_guid_123"])
        self.assertEqual(items[0].title, "分派给我")
        self.assertEqual(items[0].status, "open")
        list_call = fake.calls[1]
        self.assertEqual(list_call.get_method(), "GET")
        self.assertTrue(list_call.full_url.endswith("/open-apis/task/v2/tasklists/cfg_tasklist/tasks"))

    def test_list_assigned_without_tasklist_uses_bound_product_spec_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product_root = Path(tmp)
            spec = product_root / "harness-workspace" / "planning" / "product-specs" / "demo.md"
            spec.parent.mkdir(parents=True)
            spec.write_text(
                "# Demo\n\n## 验收标准\n\n- [ ] 接入飞书任务 #task_guid_123\n",
                encoding="utf-8",
            )
            fake = UrlopenRecorder(
                [
                    {"code": 0, "tenant_access_token": "token-1", "expire": 7200},
                    {
                        "code": 0,
                        "data": {
                            "task": {
                                "guid": "task_guid_123",
                                "summary": "接入飞书任务",
                                "status": "todo",
                                "completed_at": "0",
                                "members": [{"id": "ou_user_1", "role": "assignee"}],
                            }
                        },
                    },
                ]
            )
            env = {"FEISHU_APP_ID": "app-id", "FEISHU_APP_SECRET": "app-secret"}
            with patch.dict(os.environ, env, clear=True):
                p = provider({"tasklist_guid": "", "_product_root": str(product_root), "assignee_id": "ou_user_1"})
                with patch("urllib.request.urlopen", fake):
                    items = p.list_assigned()

        self.assertEqual([i.id for i in items], ["task_guid_123"])
        self.assertEqual(items[0].title, "接入飞书任务")
        self.assertTrue(fake.calls[1].full_url.endswith("/open-apis/task/v2/tasks/task_guid_123"))


if __name__ == "__main__":
    unittest.main()
