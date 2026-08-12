#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time
import tempfile
import urllib.parse
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parents[1] / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from work_item_providers import TeambitionProvider  # noqa: E402


ID_PATTERN = r"^[0-9a-f]{24}$"
TASK_ID = "6a38f8959690bfdafc8e80f2"


class RecordingTeambitionProvider(TeambitionProvider):
    def __init__(self, cfg: dict) -> None:
        super().__init__({"api_host": "https://api.dingtalk.com", "project_id": "project_1", **cfg}, ID_PATTERN)
        self.calls: list[tuple[str, str, dict | None]] = []
        self.open_calls: list[tuple[str, str, dict | None, dict | None]] = []
        self.task_payloads: list[dict] = []
        self.tasks_by_id: dict[str, dict] = {}

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        self.calls.append((method, path, body))
        if method == "POST" and path.endswith("/tasks"):
            return {"result": {"taskId": TASK_ID, "content": body.get("content") if body else ""}}
        if method == "GET" and "/tasks?" in path:
            query = path.partition("?")[2]
            params = dict(urllib.parse.parse_qsl(query))
            task_query = params.get("query") or ""
            if task_query.startswith("_id = ") and self.tasks_by_id:
                task_id = task_query.removeprefix("_id = ").strip()
                task = self.tasks_by_id.get(task_id)
                return {"result": [task] if task else []}
            return {"result": self.task_payloads}
        return {}

    def _validate_operator_user_id(self) -> tuple[bool, str]:
        return True, "DINGTALK_USER_OK: test-user"

    def _open_request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        query: dict | None = None,
    ) -> dict:
        if not self.tenant_id:
            raise RuntimeError("TEAMBITION_TENANT_ID_MISSING")
        self._open_api_authorization_header()
        self.open_calls.append((method, path, body, query))
        if path.endswith("/tfs"):
            return {
                "code": 200,
                "result": [
                    {"id": "tfs_todo", "name": "待处理", "kind": "todo"},
                    {"id": "tfs_done", "name": "已完成", "kind": "done"},
                ],
            }
        if path.endswith("/taskflowstatus"):
            return {
                "code": 200,
                "result": {
                    "taskflowstatusId": (body or {}).get("taskflowstatusId", "tfs_done"),
                    "updated": "2026-06-23T00:00:00Z",
                },
            }
        return {
            "code": 0,
            "result": {
                "data": [
                    {"_id": "scenario_task", "name": "任务"},
                    {"_id": "scenario_requirement", "name": "需求"},
                ]
            },
        }


def env(**extra: str) -> dict[str, str]:
    return {
        "DINGTALK_APP_KEY": "app-key",
        "DINGTALK_APP_SECRET": "app-secret",
        "DINGTALK_OPERATOR_USER_ID": "ding-user-1",
        **extra,
    }


class FakeResponse:
    def __init__(self, body: dict) -> None:
        self.body = json.dumps(body).encode()

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class TeambitionProviderTest(unittest.TestCase):
    def test_missing_env_blocks_without_network(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            provider = RecordingTeambitionProvider(
                {"status_update_mode": "stage", "stage_id_map": {"done": "stage_done"}}
            )
            ok, reason = provider.update_status(TASK_ID, "done")

        self.assertFalse(ok)
        self.assertIn("TEAMBITION_MISSING_ENV", reason)
        self.assertEqual(provider.calls, [])

    def test_default_skip_when_stage_map_not_configured(self) -> None:
        with patch.dict(os.environ, env(), clear=True):
            provider = RecordingTeambitionProvider({})
            ok, reason = provider.update_status(TASK_ID, "done")

        self.assertTrue(ok)
        self.assertIn("TEAMBITION_STATUS_SKIP", reason)
        self.assertEqual(provider.calls, [])

    def test_stage_mode_moves_task_to_configured_stage(self) -> None:
        with patch.dict(os.environ, env(), clear=True):
            provider = RecordingTeambitionProvider(
                {"status_update_mode": "stage", "stage_id_map": {"done": "stage_done"}}
            )
            ok, reason = provider.update_status(TASK_ID, "done")

        self.assertTrue(ok)
        self.assertIn("TEAMBITION_STAGE_UPDATED", reason)
        self.assertEqual(
            provider.calls,
            [
                (
                    "PUT",
                    f"/v1.0/project/users/ding-user-1/tasks/{TASK_ID}/stages",
                    {"stageId": "stage_done"},
                )
            ],
        )

    def test_stage_mode_is_inferred_when_stage_map_exists(self) -> None:
        with patch.dict(os.environ, env(), clear=True):
            provider = RecordingTeambitionProvider({"stage_id_map": {"已完成": "stage_done"}})
            ok, reason = provider.update_status(TASK_ID, "mr_merged")

        self.assertTrue(ok)
        self.assertIn("stage=done", reason)
        self.assertEqual(provider.calls[0][2], {"stageId": "stage_done"})

    def test_harness_execution_status_maps_to_in_progress_stage(self) -> None:
        with patch.dict(os.environ, env(), clear=True):
            provider = RecordingTeambitionProvider(
                {"status_update_mode": "stage", "stage_id_map": {"开发中": "stage_dev"}}
            )
            ok, reason = provider.update_status(TASK_ID, "harness_execution_started")

        self.assertTrue(ok)
        self.assertIn("stage=in_progress", reason)
        self.assertEqual(provider.calls[0][2], {"stageId": "stage_dev"})

    def test_missing_stage_mapping_blocks_in_stage_mode(self) -> None:
        with patch.dict(os.environ, env(), clear=True):
            provider = RecordingTeambitionProvider(
                {"status_update_mode": "stage", "stage_id_map": {"done": "stage_done"}}
            )
            ok, reason = provider.update_status(TASK_ID, "in_progress")

        self.assertFalse(ok)
        self.assertIn("TEAMBITION_STAGE_MISSING", reason)
        self.assertIn("stage=in_progress", reason)
        self.assertEqual(provider.calls, [])

    def test_stage_env_overrides_product_config(self) -> None:
        with patch.dict(os.environ, env(TEAMBITION_STAGE_ID_DONE="stage_done_env"), clear=True):
            provider = RecordingTeambitionProvider(
                {"status_update_mode": "stage", "stage_id_map": {"done": "stage_done_cfg"}}
            )
            ok, _reason = provider.update_status(TASK_ID, "done")

        self.assertTrue(ok)
        self.assertEqual(provider.calls[0][2], {"stageId": "stage_done_env"})

    def test_taskflowstatus_mode_discovers_done_status_and_updates_open_api(self) -> None:
        with patch.dict(
            os.environ,
            env(
                TEAMBITION_OPEN_API_AUTHORIZATION="tb-auth",
                TEAMBITION_TENANT_ID="org_1",
                TEAMBITION_OPEN_OPERATOR_ID="tb-user-1",
            ),
            clear=True,
        ):
            provider = RecordingTeambitionProvider({"status_update_mode": "taskflowstatus"})
            ok, reason = provider.update_status(TASK_ID, "done", "Harness complete")

        self.assertTrue(ok)
        self.assertIn("TEAMBITION_TASKFLOWSTATUS_UPDATED", reason)
        self.assertIn("taskflowstatusId=tfs_done", reason)
        self.assertEqual(
            provider.open_calls,
            [
                ("GET", f"/api/v3/task/{TASK_ID}/tfs", None, None),
                (
                    "PUT",
                    f"/api/v3/task/{TASK_ID}/taskflowstatus",
                    {"taskflowstatusId": "tfs_done", "tfsUpdateNote": "Harness complete"},
                    None,
                ),
            ],
        )

    def test_taskflowstatus_mode_uses_configured_status_id_without_listing(self) -> None:
        with patch.dict(
            os.environ,
            env(
                TEAMBITION_OPEN_API_AUTHORIZATION="tb-auth",
                TEAMBITION_TENANT_ID="org_1",
                TEAMBITION_OPEN_OPERATOR_ID="tb-user-1",
            ),
            clear=True,
        ):
            provider = RecordingTeambitionProvider(
                {"status_update_mode": "taskflowstatus", "taskflowstatus_id_map": {"done": "tfs_done_cfg"}}
            )
            ok, reason = provider.update_status(TASK_ID, "mr_merged")

        self.assertTrue(ok)
        self.assertIn("stage=done", reason)
        self.assertEqual(
            provider.open_calls,
            [
                (
                    "PUT",
                    f"/api/v3/task/{TASK_ID}/taskflowstatus",
                    {"taskflowstatusId": "tfs_done_cfg"},
                    None,
                )
            ],
        )

    def test_taskflowstatus_mode_is_inferred_when_status_id_map_exists(self) -> None:
        with patch.dict(
            os.environ,
            env(
                TEAMBITION_OPEN_API_AUTHORIZATION="tb-auth",
                TEAMBITION_TENANT_ID="org_1",
                TEAMBITION_OPEN_OPERATOR_ID="tb-user-1",
            ),
            clear=True,
        ):
            provider = RecordingTeambitionProvider({"taskflowstatus_id_map": {"已完成": "tfs_done_cfg"}})
            ok, reason = provider.update_status(TASK_ID, "done")

        self.assertTrue(ok)
        self.assertEqual(provider.status_update_mode, "taskflowstatus")
        self.assertIn("taskflowstatusId=tfs_done_cfg", reason)

    def test_taskflowstatus_mode_requires_open_operator_id(self) -> None:
        with patch.dict(
            os.environ,
            env(TEAMBITION_OPEN_API_AUTHORIZATION="tb-auth", TEAMBITION_TENANT_ID="org_1"),
            clear=True,
        ):
            provider = RecordingTeambitionProvider({"status_update_mode": "taskflowstatus"})
            ok, reason = provider.update_status(TASK_ID, "done")

        self.assertFalse(ok)
        self.assertIn("TEAMBITION_OPEN_OPERATOR_ID_MISSING", reason)
        self.assertEqual(provider.open_calls, [])

    def test_create_uses_configured_scenario_field_config_id(self) -> None:
        with patch.dict(os.environ, env(), clear=True):
            provider = RecordingTeambitionProvider(
                {"scenariofieldconfig_id": "scenario_task_type", "assignee_id": "executor_1"}
            )
            item = provider.create("创建任务类型 Work Item", "- AC1")

        self.assertEqual(item.id, TASK_ID)
        method, path, body = provider.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(path, "/v1.0/project/users/ding-user-1/tasks")
        self.assertEqual(body["scenariofieldconfigId"], "scenario_task_type")
        self.assertEqual(body["executorId"], "executor_1")

    def test_create_keeps_legacy_payload_when_scenario_field_config_is_absent(self) -> None:
        with patch.dict(os.environ, env(), clear=True):
            provider = RecordingTeambitionProvider({})
            provider.create("默认创建 Work Item")

        body = provider.calls[0][2]
        self.assertNotIn("scenariofieldconfigId", body)

    def test_verify_requires_configured_executor_to_match_task_executor(self) -> None:
        with patch.dict(os.environ, env(), clear=True):
            provider = RecordingTeambitionProvider({"assignee_id": "owner_1"})
            provider.task_payloads = [
                {"taskId": TASK_ID, "content": "Owned task", "executorId": "owner_1", "note": ""}
            ]
            ok, reason = provider.verify(TASK_ID)

        self.assertTrue(ok)
        self.assertIn("TEAMBITION_EXECUTOR_OK: executorId=owner_1", reason)

    def test_verify_blocks_when_task_executor_is_not_configured_owner(self) -> None:
        with patch.dict(os.environ, env(), clear=True):
            provider = RecordingTeambitionProvider({"assignee_id": "owner_1"})
            provider.task_payloads = [
                {"taskId": TASK_ID, "content": "Other task", "executorId": "owner_2", "note": ""}
            ]
            ok, reason = provider.verify(TASK_ID)

        self.assertFalse(ok)
        self.assertIn("TEAMBITION_EXECUTOR_MISMATCH", reason)
        self.assertIn("executorId=owner_2 expected=owner_1", reason)

    def test_verify_allows_executor_check_when_owner_is_not_configured(self) -> None:
        with patch.dict(os.environ, env(), clear=True):
            provider = RecordingTeambitionProvider({})
            provider.task_payloads = [
                {"taskId": TASK_ID, "content": "Unenforced task", "executorId": "owner_2", "note": ""}
            ]
            ok, reason = provider.verify(TASK_ID)

        self.assertTrue(ok)
        self.assertIn("TEAMBITION_EXECUTOR_SKIP", reason)

    def test_list_assigned_filters_by_executor_not_involve_members(self) -> None:
        with patch.dict(os.environ, env(), clear=True):
            provider = RecordingTeambitionProvider({"assignee_id": "owner_1"})
            provider.task_payloads = [
                {"taskId": TASK_ID, "content": "Owned task", "executorId": "owner_1", "note": ""},
                {
                    "taskId": "6a38f8959690bfdafc8e80f3",
                    "content": "Participant only",
                    "executorId": "owner_2",
                    "involveMembers": ["owner_1"],
                    "note": "",
                },
            ]
            items = provider.list_assigned()

        self.assertEqual([item.id for item in items], [TASK_ID])

    def test_list_assigned_falls_back_to_local_open_product_specs(self) -> None:
        checked_id = "6a38f8959690bfdafc8e80f3"
        done_id = "6a38f8959690bfdafc8e80f4"
        other_owner_id = "6a38f8959690bfdafc8e80f5"
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, env(), clear=True):
            root = Path(tmp)
            spec_dir = root / "harness-workspace/planning/product-specs"
            spec_dir.mkdir(parents=True)
            (spec_dir / "sample.md").write_text(
                "\n".join(
                    [
                        f"- [ ] Open task #{TASK_ID}",
                        f"- [x] Locally complete task #{checked_id}",
                        f"- [ ] Remotely done task #{done_id}",
                        f"- [ ] Other owner task #{other_owner_id}",
                    ]
                ),
                encoding="utf-8",
            )
            provider = RecordingTeambitionProvider({"assignee_id": "owner_1", "product_root": str(root)})
            provider.task_payloads = []
            provider.tasks_by_id = {
                TASK_ID: {"taskId": TASK_ID, "content": "Open task", "executorId": "owner_1", "isDone": False, "note": ""},
                done_id: {"taskId": done_id, "content": "Done task", "executorId": "owner_1", "isDone": True, "note": ""},
                other_owner_id: {
                    "taskId": other_owner_id,
                    "content": "Other owner task",
                    "executorId": "owner_2",
                    "isDone": False,
                    "note": "",
                },
            }
            items = provider.list_assigned()

        self.assertEqual([item.id for item in items], [TASK_ID])

    def test_scenario_field_configs_searches_open_api_with_documented_query(self) -> None:
        with patch.dict(
            os.environ,
            env(TEAMBITION_OPEN_API_AUTHORIZATION="tb-auth", TEAMBITION_TENANT_ID="org_1"),
            clear=True,
        ):
            provider = RecordingTeambitionProvider({})
            configs = provider.scenario_field_configs(keyword="任务", sfc_ids="scenario_task")

        self.assertEqual(configs[0]["id"], "scenario_task")
        self.assertEqual(configs[0]["name"], "任务")
        self.assertEqual(
            provider.open_calls,
            [
                (
                    "GET",
                    "/api/v3/scenariofieldconfig/search",
                    None,
                    {"q": "任务", "sfcIds": "scenario_task", "pageSize": 50},
                )
            ],
        )

    def test_scenario_field_configs_requires_open_api_authorization(self) -> None:
        with patch.dict(os.environ, env(TEAMBITION_TENANT_ID="org_1"), clear=True):
            provider = RecordingTeambitionProvider({})
            with self.assertRaisesRegex(RuntimeError, "TEAMBITION_OPEN_API_AUTHORIZATION_MISSING"):
                provider.scenario_field_configs()

    def test_scenario_field_configs_requires_tenant_id(self) -> None:
        with patch.dict(os.environ, env(TEAMBITION_OPEN_API_AUTHORIZATION="tb-auth"), clear=True):
            provider = RecordingTeambitionProvider({})
            with self.assertRaisesRegex(RuntimeError, "TEAMBITION_TENANT_ID_MISSING"):
                provider.scenario_field_configs()

    def test_open_request_uses_documented_headers_and_bearer_prefix(self) -> None:
        captured = {}

        def fake_urlopen(request, timeout: int = 30):  # type: ignore[no-untyped-def]
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            return FakeResponse({"code": 0, "result": []})

        with patch.dict(
            os.environ,
            env(TEAMBITION_OPEN_API_AUTHORIZATION="app-token", TEAMBITION_TENANT_ID="org_1"),
            clear=True,
        ):
            provider = TeambitionProvider({"project_id": "project_1"}, ID_PATTERN)
            with patch("urllib.request.urlopen", fake_urlopen):
                data = provider._open_request(
                    "GET",
                    "/api/v3/scenariofieldconfig/search",
                    query={"q": "任务", "pageSize": 50},
                )

        self.assertEqual(data, {"code": 0, "result": []})
        self.assertIn("q=%E4%BB%BB%E5%8A%A1", captured["url"])
        self.assertIn("pageSize=50", captured["url"])
        self.assertEqual(captured["headers"]["Authorization"], "Bearer app-token")
        self.assertEqual(captured["headers"]["X-tenant-id"], "org_1")
        self.assertEqual(captured["headers"]["X-tenant-type"], "organization")

    def test_open_request_can_generate_app_access_token_from_open_app_credentials(self) -> None:
        captured = {}

        def fake_urlopen(request, timeout: int = 30):  # type: ignore[no-untyped-def]
            captured["headers"] = dict(request.header_items())
            return FakeResponse({"code": 0, "result": []})

        with patch.dict(
            os.environ,
            env(
                TEAMBITION_OPEN_APP_ID="tb_open_app",
                TEAMBITION_OPEN_APP_SECRET="tb_secret",
                TEAMBITION_TENANT_ID="org_1",
            ),
            clear=True,
        ):
            provider = TeambitionProvider({"project_id": "project_1"}, ID_PATTERN)
            with patch("urllib.request.urlopen", fake_urlopen), patch.object(time, "time", return_value=1000):
                provider._open_request("GET", "/api/v3/scenariofieldconfig/search")

        auth = captured["headers"]["Authorization"]
        self.assertTrue(auth.startswith("Bearer "))
        token = auth.removeprefix("Bearer ")
        header_b64, payload_b64, _signature_b64 = token.split(".")

        def decode(segment: str) -> dict:
            import base64

            padding = "=" * (-len(segment) % 4)
            return json.loads(base64.urlsafe_b64decode((segment + padding).encode()))

        self.assertEqual(decode(header_b64), {"typ": "JWT", "alg": "HS256"})
        self.assertEqual(decode(payload_b64), {"_appId": "tb_open_app", "iat": 1000, "exp": 4600})


if __name__ == "__main__":
    unittest.main()
