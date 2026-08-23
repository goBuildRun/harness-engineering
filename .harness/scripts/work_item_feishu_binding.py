#!/usr/bin/env python3
"""Exact Feishu tasklist and parent binding validation."""
from __future__ import annotations

import time
import urllib.parse
from typing import TYPE_CHECKING, Any

from work_item_providers import valid_id

if TYPE_CHECKING:
    from work_item_providers import WorkItem


class FeishuBindingMixin:
    CREATE_READBACK_ATTEMPTS: int
    app_id: str
    id_pattern: str
    tasklist_guid: str

    @property
    def configured(self) -> bool:
        raise NotImplementedError

    def pull(self, work_item_id: str) -> WorkItem:
        raise NotImplementedError

    def _request(self, method: str, path: str, body=None, query=None, access_token=None) -> dict:
        raise NotImplementedError

    def ensure_tasklist_member(
        self,
        tasklist_guid: str | None = None,
        member_id: str | None = None,
        member_type: str = "app",
        role: str = "editor",
        access_token: str | None = None,
    ) -> dict[str, Any]:
        guid = tasklist_guid or self.tasklist_guid
        if not guid:
            raise RuntimeError("FEISHU_TASKLIST_GUID_MISSING")
        target_id = member_id or self.app_id
        if not target_id:
            raise RuntimeError("FEISHU_TASKLIST_MEMBER_ID_MISSING")
        return self._request(
            "POST",
            f"/open-apis/task/v2/tasklists/{urllib.parse.quote(guid, safe='')}/add_members",
            {"members": [{"id": target_id, "type": member_type, "role": role}]},
            access_token=access_token,
        )

    def _pull_created_with_retry(
        self,
        task_id: str,
        operation: str,
        expected_tasklist: str,
        expected_parent: str,
        parent_item: WorkItem | None = None,
    ) -> WorkItem:
        last_error: Exception | None = None
        for attempt in range(1, self.CREATE_READBACK_ATTEMPTS + 1):
            try:
                item = self.pull(task_id)
                ok, reason = self._verify_pulled_binding(
                    item,
                    expected_tasklist,
                    expected_parent,
                    parent_item=parent_item,
                )
                if ok:
                    item.raw["_harness_binding_verified"] = {
                        "tasklist": expected_tasklist,
                        "parent": None if expected_parent is None else expected_parent,
                    }
                    return item
                last_error = RuntimeError(reason)
            except Exception as exc:
                last_error = exc
            if attempt < self.CREATE_READBACK_ATTEMPTS:
                time.sleep(0.2 * attempt)
        raise RuntimeError(
            f"FEISHU_{operation}_READBACK_FAIL: created_id={task_id}; "
            f"attempts={self.CREATE_READBACK_ATTEMPTS}; last={last_error}"
        )

    @staticmethod
    def _tasklist_guids(task: dict[str, Any]) -> set[str]:
        tasklists = task.get("tasklists") or []
        if not isinstance(tasklists, list):
            return set()
        return {
            str(item.get("tasklist_guid") or "").strip()
            for item in tasklists
            if isinstance(item, dict) and str(item.get("tasklist_guid") or "").strip()
        }

    def verify_binding(
        self,
        work_item_id: str,
        expected_project_id: str | None = None,
        expected_parent_id: str | None = None,
    ) -> tuple[bool, str]:
        if not valid_id(work_item_id, self.id_pattern):
            return False, f"FEISHU_INVALID_ID: 须匹配 {self.id_pattern}"
        if not self.configured:
            return False, "FEISHU_MISSING_ENV: 请配置 FEISHU_APP_ID / FEISHU_APP_SECRET；离线请显式使用 noop"
        expected_tasklist = str(expected_project_id or self.tasklist_guid or "").strip()
        if not expected_tasklist:
            return False, "FEISHU_TASKLIST_UNCONFIGURED: 精确 binding 校验需要产品 tasklist_guid"
        expected_parent = None if expected_parent_id is None else str(expected_parent_id).strip()
        try:
            item = self.pull(work_item_id)
            return self._verify_pulled_binding(item, expected_tasklist, expected_parent)
        except Exception as exc:
            return False, f"FEISHU_BINDING_VERIFY_FAIL: {exc}"

    def verify_item_binding(
        self,
        item: WorkItem,
        expected_project_id: str | None = None,
        expected_parent_id: str | None = None,
    ) -> tuple[bool, str]:
        marker = (item.raw or {}).get("_harness_binding_verified")
        expected = {
            "tasklist": str(expected_project_id or self.tasklist_guid or "").strip(),
            "parent": None if expected_parent_id is None else str(expected_parent_id).strip(),
        }
        if isinstance(marker, dict) and marker.get("tasklist") == expected["tasklist"] \
                and marker.get("parent") == expected["parent"]:
            return True, f"FEISHU_BINDING_OK_READBACK: task={item.id}"
        # A caller may provide a provider-created item without the marker; keep
        # the old fail-closed verification in that case.
        return self.verify_binding(
            item.id, expected_project_id=expected_project_id,
            expected_parent_id=expected_parent_id,
        )

    def _verify_pulled_binding(
        self,
        item: WorkItem,
        expected_tasklist: str,
        expected_parent: str | None,
        parent_item: WorkItem | None = None,
    ) -> tuple[bool, str]:
        actual_parent = str(item.raw.get("parent_task_guid") or "").strip()
        if expected_parent is not None and actual_parent != expected_parent:
            return False, (
                "FEISHU_PARENT_MISMATCH: "
                f"task={item.id}; expected={expected_parent or '<top-level>'}; actual={actual_parent or '<top-level>'}"
            )

        membership = "unconfigured"
        if expected_tasklist:
            direct_tasklists = self._tasklist_guids(item.raw)
            if expected_parent:
                parent = parent_item if parent_item and parent_item.id == actual_parent else self.pull(actual_parent)
                parent_tasklists = self._tasklist_guids(parent.raw)
                if expected_tasklist not in parent_tasklists:
                    return False, (
                        "FEISHU_TASKLIST_MISMATCH: "
                        f"task={item.id}; expected={expected_tasklist}; "
                        f"direct={sorted(direct_tasklists)}; parent={actual_parent}; parent_tasklists={sorted(parent_tasklists)}"
                    )
                membership = (
                    f"direct+parent:{actual_parent}"
                    if expected_tasklist in direct_tasklists
                    else f"inherited:{actual_parent}"
                )
            elif expected_tasklist in direct_tasklists:
                membership = "direct"
            elif actual_parent:
                parent = parent_item if parent_item and parent_item.id == actual_parent else self.pull(actual_parent)
                parent_tasklists = self._tasklist_guids(parent.raw)
                if expected_tasklist not in parent_tasklists:
                    return False, (
                        "FEISHU_TASKLIST_MISMATCH: "
                        f"task={item.id}; expected={expected_tasklist}; "
                        f"direct={sorted(direct_tasklists)}; parent={actual_parent}; parent_tasklists={sorted(parent_tasklists)}"
                    )
                membership = f"inherited:{actual_parent}"
            else:
                return False, (
                    "FEISHU_TASKLIST_MISMATCH: "
                    f"task={item.id}; expected={expected_tasklist}; direct={sorted(direct_tasklists)}; parent=<top-level>"
                )
        return True, (
            "FEISHU_BINDING_OK: "
            f"task={item.id}; tasklist={expected_tasklist or '<unconfigured>'}; "
            f"membership={membership}; parent={actual_parent or '<top-level>'}"
        )
