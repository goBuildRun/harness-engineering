#!/usr/bin/env python3
"""Feishu Work Item provider adapter."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from work_item_feishu_binding import FeishuBindingMixin
from work_item_feishu_payload import FeishuPayloadMixin
from work_item_providers import WorkItem, WorkItemProvider, valid_id
class FeishuProvider(FeishuBindingMixin, FeishuPayloadMixin, WorkItemProvider):
    """Feishu/Lark Tasks provider.

    The default endpoints follow Feishu open platform task v2 conventions and
    remain configurable because task workspace deployments can differ by tenant.
    """

    name = "feishu"
    requires_l3_hierarchy_contract = True
    STATUS_ALIASES = {
        "done": "done",
        "closed": "done",
        "complete": "done",
        "completed": "done",
        "mr_merged": "done",
        "已完成": "done",
        "pending": "open",
        "todo": "open",
        "not_started": "open",
        "planning_gate_ready": "open",
        "in_progress": "open",
        "progress": "open",
        "harness_execution_started": "open",
        "qa": "open",
        "qa_passed": "open",
        "testing": "open",
        "ready_to_release": "open",
        "release_ready": "open",
        "awaiting_release": "open",
        "待处理": "open",
        "开发中": "open",
        "测试中": "open",
        "待发布": "open",
    }
    COMPLETED_MODE_ALIASES = {"completed", "complete", "completed_at", "completion"}
    DESCRIPTION_MODE_ALIASES = {"description", "patch", "note"}
    CREATE_READBACK_ATTEMPTS = 3

    def __init__(self, cfg: dict[str, Any], id_pattern: str) -> None:
        self.id_pattern = id_pattern
        self.api_host = (cfg.get("api_host") or "https://open.feishu.cn").rstrip("/")
        self.token_path = cfg.get("tenant_token_path") or "/open-apis/auth/v3/tenant_access_token/internal"
        self.tasks_path = cfg.get("tasks_path") or "/open-apis/task/v2/tasks"
        self.list_tasks_path = cfg.get("list_tasks_path") or self.tasks_path
        self.app_id = os.environ.get("FEISHU_APP_ID", "")
        self.app_secret = os.environ.get("FEISHU_APP_SECRET", "")
        configured_tasklist = str(cfg.get("tasklist_guid") or "").strip()
        explicit_override = os.environ.get("FEISHU_TASKLIST_GUID_OVERRIDE", "").strip()
        legacy_default = os.environ.get("FEISHU_TASKLIST_GUID", "").strip()
        self.tasklist_guid = explicit_override or configured_tasklist or legacy_default
        self.tasklist_source = (
            "override" if explicit_override else "product" if configured_tasklist else "legacy_env" if legacy_default else "unset"
        )
        self.assignee_id = os.environ.get("FEISHU_ASSIGNEE_ID", "") or str(cfg.get("assignee_id") or "")
        self.list_query = cfg.get("list_query") if isinstance(cfg.get("list_query"), dict) else {}
        self.status_update_mode = str(cfg.get("status_update_mode") or "skip").strip().lower()
        raw_product_root = str(cfg.get("_product_root") or cfg.get("product_root") or "").strip()
        self.product_root = Path(raw_product_root).expanduser().resolve() if raw_product_root else None
        self._token: str | None = None
        self._token_expires = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.app_id and self.app_secret)

    def _tenant_access_token(self) -> str:
        if self._token and time.time() < self._token_expires - 60:
            return self._token
        url = f"{self.api_host}{self.token_path}"
        body = {"app_id": self.app_id, "app_secret": self.app_secret}
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        if data.get("code", 0) != 0:
            raise RuntimeError(f"FEISHU_TOKEN_FAIL: {data.get('msg') or data}")
        token = data.get("tenant_access_token")
        if not token:
            raise RuntimeError(f"FEISHU_TOKEN_EMPTY: {data}")
        self._token = token
        self._token_expires = time.time() + int(data.get("expire") or data.get("expires_in") or 7200)
        return token

    @staticmethod
    def _bearer_token(token: str) -> str:
        token = token.strip()
        return token[7:].strip() if token.lower().startswith("bearer ") else token

    def _request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        query: dict | None = None,
        access_token: str | None = None,
    ) -> dict:
        token = self._bearer_token(access_token) if access_token else self._tenant_access_token()
        url = f"{self.api_host}{path}"
        if query:
            url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v not in ("", None)})
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            err_body = e.read().decode(errors="replace")
            raise RuntimeError(f"FEISHU_HTTP_{e.code}: {err_body[:500]}") from e

    def _tasklist_tasks_path(self, tasklist_guid: str) -> str:
        return f"/open-apis/task/v2/tasklists/{urllib.parse.quote(tasklist_guid, safe='')}/tasks"

    @classmethod
    def _canonical_status(cls, status: str) -> str:
        key = status.strip().lower()
        return cls.STATUS_ALIASES.get(key, key)

    @staticmethod
    def _is_completed(task: dict[str, Any]) -> bool:
        completed_at = str(task.get("completed_at") or "0")
        return completed_at not in {"", "0", "None", "none", "null"}

    @classmethod
    def _task_status(cls, task: dict[str, Any]) -> str:
        if cls._is_completed(task):
            return "done"
        if "completed_at" in task and not task.get("status"):
            return "open"
        return str(task.get("status") or "unknown")

    def verify(self, work_item_id: str) -> tuple[bool, str]:
        if not valid_id(work_item_id, self.id_pattern):
            return False, f"FEISHU_INVALID_ID: 须匹配 {self.id_pattern}"
        if not self.configured:
            return False, "FEISHU_MISSING_ENV: 请配置 FEISHU_APP_ID / FEISHU_APP_SECRET；离线请显式使用 noop"
        try:
            item = self.pull(work_item_id)
            return True, f"FEISHU_OK: {item.title or work_item_id}"
        except Exception as e:
            return False, f"FEISHU_VERIFY_FAIL: {e}"

    def pull(self, work_item_id: str) -> WorkItem:
        if not self.configured:
            raise RuntimeError("FEISHU_MISSING_ENV")
        data = self._request("GET", f"{self.tasks_path}/{urllib.parse.quote(work_item_id, safe='')}")
        task = self._task_payload(data)
        if not task:
            raise RuntimeError(f"FEISHU_NOT_FOUND: task={work_item_id}")
        actual_id = self._task_id(task)
        if actual_id != work_item_id:
            raise RuntimeError(f"FEISHU_RESPONSE_ID_MISMATCH: expected={work_item_id}; actual={actual_id or '<missing>'}")
        note = str(task.get("description") or task.get("notes") or "")
        return WorkItem(
            id=work_item_id,
            title=str(task.get("summary") or task.get("title") or task.get("name") or ""),
            status=self._task_status(task),
            note=note,
            acceptance_criteria=[ln.strip("- ").strip() for ln in note.splitlines() if ln.strip().startswith("-")],
            url=str(task.get("url") or ""),
            provider=self.name,
            raw=task,
        )

    def create(self, title: str, note: str = "", project_id: str | None = None) -> WorkItem:
        if not self.configured:
            raise RuntimeError("FEISHU_MISSING_ENV")
        tasklist_guid = str(project_id or self.tasklist_guid or "").strip()
        if not tasklist_guid:
            raise RuntimeError("FEISHU_TASKLIST_GUID_MISSING: refusing to create an unbound task")
        body: dict[str, Any] = {"summary": title}
        if note:
            body["description"] = note
        body["tasklists"] = [{"tasklist_guid": tasklist_guid}]
        if self.assignee_id:
            body["members"] = [{"id": self.assignee_id, "role": "assignee"}]
        data = self._request("POST", self.tasks_path, body)
        task = self._task_payload(data)
        task_id = self._task_id(task)
        if not task_id:
            raise RuntimeError(f"FEISHU_CREATE_FAIL: {data}")
        return self._pull_created_with_retry(task_id, "CREATE", tasklist_guid, "")

    def update_status(self, work_item_id: str, status: str, note: str = "") -> tuple[bool, str]:
        ok, reason = self.verify(work_item_id)
        if not ok:
            return False, reason
        if self.status_update_mode == "skip":
            return True, f"FEISHU_STATUS_SKIP: status={status}; 飞书状态回写未启用"
        try:
            if self.status_update_mode in self.COMPLETED_MODE_ALIASES:
                stage = self._canonical_status(status)
                if stage == "done":
                    completed_at = str(int(time.time() * 1000))
                elif stage == "open":
                    completed_at = "0"
                else:
                    return False, f"FEISHU_STATUS_UNSUPPORTED: {status}; completed mode only supports done/open statuses"
                body = {"task": {"completed_at": completed_at}, "update_fields": ["completed_at"]}
                self._request("PATCH", f"{self.tasks_path}/{urllib.parse.quote(work_item_id, safe='')}", body)
                readback = self.pull(work_item_id)
                provider_status = str(readback.status or "unknown")
                canonical_status = self._canonical_status(provider_status)
                readback_completed_at = str((readback.raw or {}).get("completed_at") or "0")
                detail = f"requested_status={status}; provider_status={provider_status}; canonical_status={canonical_status}; completed_at={readback_completed_at}"
                if canonical_status != stage:
                    return False, f"FEISHU_UPDATE_VERIFY_FAIL: expected={stage}; {detail}"
                return True, f"FEISHU_UPDATED: {detail}; readback=verified"
            if self.status_update_mode not in self.DESCRIPTION_MODE_ALIASES:
                return False, (
                    f"FEISHU_STATUS_MODE_UNSUPPORTED: {self.status_update_mode}; "
                    "use status_update_mode: completed, description, or skip"
                )
            body = {"description": f"[Harness] status={status}\n{note}"}
            self._request("PATCH", f"{self.tasks_path}/{urllib.parse.quote(work_item_id, safe='')}", body)
            return True, f"FEISHU_UPDATED: {status}"
        except Exception as e:
            return False, f"FEISHU_UPDATE_FAIL: {e}"

    def update_description(self, work_item_id: str, description: str) -> tuple[bool, str]:
        if not description.strip():
            return False, "FEISHU_DESCRIPTION_EMPTY"
        ok, reason = self.verify(work_item_id)
        if not ok:
            return False, reason
        try:
            body = {
                "task": {"description": description},
                "update_fields": ["description"],
            }
            self._request("PATCH", f"{self.tasks_path}/{urllib.parse.quote(work_item_id, safe='')}", body)
            return True, f"FEISHU_DESCRIPTION_UPDATED: chars={len(description)}"
        except Exception as e:
            return False, f"FEISHU_DESCRIPTION_UPDATE_FAIL: {e}"

    def update_title(self, work_item_id: str, title: str) -> tuple[bool, str]:
        title = title.strip()
        if not title:
            return False, "FEISHU_TITLE_EMPTY"
        ok, reason = self.verify(work_item_id)
        if not ok:
            return False, reason
        try:
            body = {"task": {"summary": title}, "update_fields": ["summary"]}
            self._request("PATCH", f"{self.tasks_path}/{urllib.parse.quote(work_item_id, safe='')}", body)
            return True, "FEISHU_TITLE_UPDATED"
        except Exception as e:
            return False, f"FEISHU_TITLE_UPDATE_FAIL: {e}"

    def create_subtask(self, parent_work_item_id: str, title: str, note: str = "") -> WorkItem:
        if not valid_id(parent_work_item_id, self.id_pattern):
            raise ValueError(f"FEISHU_INVALID_PARENT_ID: 须匹配 {self.id_pattern}")
        title = title.strip()
        if not title:
            raise ValueError("FEISHU_TITLE_EMPTY")
        if not self.tasklist_guid:
            raise RuntimeError("FEISHU_TASKLIST_GUID_MISSING: refusing to create an unbound subtask")
        parent = self.pull(parent_work_item_id)
        if self.tasklist_guid and self.tasklist_guid not in self._tasklist_guids(parent.raw):
            raise RuntimeError(
                "FEISHU_PARENT_TASKLIST_MISMATCH: "
                f"parent={parent_work_item_id}; expected={self.tasklist_guid}; "
                f"actual={sorted(self._tasklist_guids(parent.raw))}"
            )
        body: dict[str, Any] = {"summary": title}
        if note:
            body["description"] = note
        if self.assignee_id:
            body["members"] = [{"id": self.assignee_id, "role": "assignee"}]
        path = f"{self.tasks_path}/{urllib.parse.quote(parent_work_item_id, safe='')}/subtasks"
        data = self._request("POST", path, body)
        task = self._task_payload(data)
        task_id = self._task_id(task)
        if not task_id:
            raise RuntimeError(f"FEISHU_SUBTASK_CREATE_FAIL: {data}")
        return self._pull_created_with_retry(
            task_id,
            "SUBTASK_CREATE",
            self.tasklist_guid,
            parent_work_item_id,
            parent_item=parent,
        )

    def list_assigned(self, assignee_id: str | None = None) -> list[WorkItem]:
        if not self.configured:
            return []
        if not (self.tasklist_guid or self.list_query):
            return self._list_local_bound_items(assignee_id)
        assignee = assignee_id or self.assignee_id
        query = dict(self.list_query)
        list_path = self.list_tasks_path
        if self.tasklist_guid:
            default_list_path = self.list_tasks_path.rstrip("/") == self.tasks_path.rstrip("/")
            if default_list_path:
                list_path = self._tasklist_tasks_path(self.tasklist_guid)
            elif "{tasklist_guid}" in list_path:
                list_path = list_path.replace("{tasklist_guid}", urllib.parse.quote(self.tasklist_guid, safe=""))
            elif "tasklist_guid" not in query:
                query["tasklist_guid"] = self.tasklist_guid
        data = self._request("GET", list_path, query=query)
        tasks = self._task_list_payload(data)
        items: list[WorkItem] = []
        for task in tasks:
            if assignee and assignee not in self._task_assignee_ids(task):
                continue
            task_id = self._task_id(task)
            if not task_id:
                continue
            if self._is_completed(task):
                continue
            note = str(task.get("description") or task.get("notes") or "")
            items.append(
                WorkItem(
                    id=task_id,
                    title=str(task.get("summary") or task.get("title") or task.get("name") or ""),
                    status=self._task_status(task),
                    note=note[:200],
                    acceptance_criteria=[ln.strip("- ").strip() for ln in note.splitlines() if ln.strip().startswith("-")],
                    url=str(task.get("url") or ""),
                    provider=self.name,
                    raw=task,
                )
            )
        return items

    def _list_local_bound_items(self, assignee_id: str | None = None) -> list[WorkItem]:
        assignee = assignee_id or self.assignee_id
        items: list[WorkItem] = []
        for task_id in self._local_open_work_item_ids():
            try:
                item = self.pull(task_id)
            except Exception:
                continue
            raw = item.raw or {}
            status = str(item.status or "").lower()
            if status in {"done", "completed", "complete"} or str(raw.get("completed_at") or "0") != "0":
                continue
            if assignee and assignee not in self._task_assignee_ids(raw):
                continue
            items.append(item)
        return items

    def _local_open_work_item_ids(self) -> list[str]:
        if not self.product_root or not self.product_root.is_dir():
            return []
        spec_dir = self.product_root / "harness-workspace/planning/product-specs"
        if not spec_dir.is_dir():
            return []
        ids: list[str] = []
        seen: set[str] = set()
        for spec in sorted(spec_dir.glob("*.md")):
            try:
                lines = spec.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            for line in lines:
                if not line.startswith("- [ ]"):
                    continue
                for match in re.finditer(r"#([A-Za-z0-9][A-Za-z0-9._:-]{1,127})\b", line):
                    task_id = match.group(1)
                    if task_id in seen or not valid_id(task_id, self.id_pattern):
                        continue
                    ids.append(task_id)
                    seen.add(task_id)
        return ids
