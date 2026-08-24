#!/usr/bin/env python3
"""Teambition Work Item provider adapter."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from work_item_teambition_support import TeambitionSupportMixin
from work_item_providers import WorkItem, WorkItemProvider, valid_id

class TeambitionProvider(TeambitionSupportMixin, WorkItemProvider):
    """钉钉 Teambition — 钉钉开放平台项目管理 API。"""

    name = "teambition"
    STATUS_STAGE_ALIASES = {
        "pending": "pending",
        "todo": "pending",
        "backlog": "pending",
        "not_started": "pending",
        "待处理": "pending",
        "design": "design",
        "designing": "design",
        "planning": "design",
        "bmad_planning": "design",
        "planning_gate_ready": "design",
        "设计中": "design",
        "in_progress": "in_progress",
        "progress": "in_progress",
        "dev": "in_progress",
        "development": "in_progress",
        "ael_execution_started": "in_progress",
        "开发中": "in_progress",
        "testing": "testing",
        "test": "testing",
        "qa": "testing",
        "qa_passed": "testing",
        "qa_review": "testing",
        "测试中": "testing",
        "ready_to_release": "ready_to_release",
        "release_ready": "ready_to_release",
        "awaiting_release": "ready_to_release",
        "release": "ready_to_release",
        "待发布": "ready_to_release",
        "done": "done",
        "closed": "done",
        "complete": "done",
        "completed": "done",
        "mr_merged": "done",
        "已完成": "done",
        "implemented": "implemented",
        "released": "implemented",
        "已实现": "implemented",
        "cancelled": "cancelled",
        "canceled": "cancelled",
        "cancel": "cancelled",
        "已取消": "cancelled",
    }
    STAGE_ENV_KEYS = (
        "pending",
        "design",
        "in_progress",
        "testing",
        "ready_to_release",
        "done",
        "implemented",
        "cancelled",
    )
    TASKFLOWSTATUS_MODE_ALIASES = {
        "taskflowstatus",
        "taskflow_status",
        "taskflow",
        "tfs",
        "open_taskflowstatus",
    }
    DEFAULT_TASKFLOWSTATUS_NAMES = {
        "pending": ("待处理", "待办", "未开始", "todo", "open"),
        "design": ("设计中",),
        "in_progress": ("开发中", "进行中", "处理中", "in progress"),
        "testing": ("测试中", "测试", "qa"),
        "ready_to_release": ("待发布",),
        "done": ("已完成", "完成", "done", "closed", "complete", "completed"),
        "implemented": ("已实现", "已发布", "released", "implemented"),
        "cancelled": ("已取消", "取消", "cancelled", "canceled"),
    }
    DEFAULT_TASKFLOWSTATUS_KINDS = {
        "pending": ("todo", "open", "pending"),
        "design": ("design",),
        "in_progress": ("doing", "in_progress", "progress", "processing"),
        "testing": ("testing", "qa"),
        "ready_to_release": ("ready_to_release", "release"),
        "done": ("done", "closed", "complete", "completed", "finish", "finished", "end"),
        "implemented": ("implemented", "released"),
        "cancelled": ("cancelled", "canceled"),
    }

    def __init__(self, cfg: dict[str, Any], id_pattern: str) -> None:
        self.id_pattern = id_pattern
        raw_product_root = str(cfg.get("_product_root") or cfg.get("product_root") or "").strip()
        self.product_root = Path(raw_product_root).expanduser().resolve() if raw_product_root else None
        self.api_host = (cfg.get("api_host") or "https://api.dingtalk.com").rstrip("/")
        self.app_key = os.environ.get("DINGTALK_APP_KEY", "")
        self.app_secret = os.environ.get("DINGTALK_APP_SECRET", "")
        self.operator_user_id = os.environ.get("DINGTALK_OPERATOR_USER_ID", "")
        self.project_id = os.environ.get("TEAMBITION_PROJECT_ID", "") or str(cfg.get("project_id") or "")
        self.assignee_id = os.environ.get("TEAMBITION_ASSIGNEE_ID", "") or str(cfg.get("assignee_id") or "")
        self.scenariofieldconfig_id = (
            os.environ.get("TEAMBITION_SCENARIOFIELDCONFIG_ID", "")
            or os.environ.get("TEAMBITION_SCENARIO_FIELD_CONFIG_ID", "")
            or str(cfg.get("scenariofieldconfig_id") or cfg.get("scenario_field_config_id") or "")
        )
        self.open_api_host = (cfg.get("open_api_host") or "https://open.teambition.com").rstrip("/")
        self.open_api_authorization = (
            os.environ.get("TEAMBITION_OPEN_API_AUTHORIZATION", "")
            or os.environ.get("TEAMBITION_OPEN_API_TOKEN", "")
        )
        self.open_app_id = os.environ.get("TEAMBITION_OPEN_APP_ID", "") or str(cfg.get("open_app_id") or "")
        self.open_app_secret = os.environ.get("TEAMBITION_OPEN_APP_SECRET", "")
        self.tenant_id = (
            os.environ.get("TEAMBITION_TENANT_ID", "")
            or os.environ.get("TEAMBITION_ORG_ID", "")
            or str(cfg.get("tenant_id") or cfg.get("organization_id") or "")
        )
        self.tenant_type = (
            os.environ.get("TEAMBITION_TENANT_TYPE", "")
            or str(cfg.get("tenant_type") or "organization")
        )
        self.open_operator_id = (
            os.environ.get("TEAMBITION_OPEN_OPERATOR_ID", "")
            or os.environ.get("TEAMBITION_OPERATOR_ID", "")
            or str(cfg.get("open_operator_id") or cfg.get("operator_id") or "")
        )
        self.stage_id_map = self._load_stage_id_map(cfg)
        self.taskflowstatus_id_map = self._load_taskflowstatus_id_map(cfg)
        self.taskflowstatus_name_map = self._load_taskflowstatus_name_map(cfg)
        self.status_update_mode = str(
            cfg.get("status_update_mode")
            or ("taskflowstatus" if (self.taskflowstatus_id_map or self.taskflowstatus_name_map) else "")
            or ("stage" if self.stage_id_map else "skip")
        ).strip().lower()
        self._token: str | None = None
        self._token_expires = 0.0

    def verify(self, work_item_id: str) -> tuple[bool, str]:
        if not valid_id(work_item_id, self.id_pattern):
            return False, f"TEAMBITION_INVALID_ID: 须为 24 位十六进制 taskId，匹配 {self.id_pattern}"
        if not self.configured:
            return False, "TEAMBITION_MISSING_ENV: 请配置 DINGTALK_APP_KEY / DINGTALK_APP_SECRET / DINGTALK_OPERATOR_USER_ID / TEAMBITION_PROJECT_ID；离线请显式使用 noop"
        ok_user, user_msg = self._validate_operator_user_id()
        if not ok_user:
            return False, user_msg
        try:
            item = self.pull(work_item_id)
            if item.id == work_item_id:
                ok_executor, executor_msg = self._validate_executor(item)
                if not ok_executor:
                    return False, executor_msg
                return True, f"TEAMBITION_OK: {item.title}; {executor_msg}"
        except Exception as e:
            return False, f"TEAMBITION_VERIFY_FAIL: {e}"
        return False, "TEAMBITION_NOT_FOUND"

    def _validate_executor(self, item: WorkItem) -> tuple[bool, str]:
        expected = str(self.assignee_id or "").strip()
        if not expected:
            return True, "TEAMBITION_EXECUTOR_SKIP: 未配置 assignee_id，未强制校验执行者"
        executor = str((item.raw or {}).get("executorId") or "").strip()
        if executor != expected:
            return (
                False,
                "TEAMBITION_EXECUTOR_MISMATCH: "
                f"executorId={executor or '<empty>'} expected={expected}; "
                "Work Item 必须分配给当前执行者后才能进入 AEL Execution",
            )
        return True, f"TEAMBITION_EXECUTOR_OK: executorId={executor}"

    def pull(self, work_item_id: str) -> WorkItem:
        if not self.configured:
            raise RuntimeError("TEAMBITION_MISSING_ENV")
        uid = self.operator_user_id
        pid = self.project_id
        # 查询项目任务列表，按 taskId 过滤
        q = urllib.parse.urlencode(
            {
                "maxResults": 100,
                "query": f"_id = {work_item_id}",
            }
        )
        path = f"/v1.0/project/users/{uid}/projectIds/{pid}/tasks?{q}"
        data = self._request("GET", path)
        tasks = data.get("result") or data.get("tasks") or []
        if isinstance(tasks, dict):
            tasks = tasks.get("tasks") or tasks.get("list") or []
        for t in tasks:
            tid = str(t.get("taskId") or t.get("id") or "")
            if tid == work_item_id:
                note = t.get("note") or ""
                ac = [ln.strip("- ").strip() for ln in note.splitlines() if ln.strip().startswith("-")]
                return WorkItem(
                    id=work_item_id,
                    title=t.get("content") or "",
                    status=str(t.get("stageId") or t.get("status") or "unknown"),
                    note=note,
                    acceptance_criteria=ac,
                    url=f"https://www.teambition.com/task/{work_item_id}",
                    provider=self.name,
                    raw=t,
                )
        raise RuntimeError(f"TEAMBITION_NOT_FOUND: taskId={work_item_id}")

    def create(self, title: str, note: str = "", project_id: str | None = None) -> WorkItem:
        if not self.configured:
            raise RuntimeError("TEAMBITION_MISSING_ENV")
        pid = project_id or self.project_id
        path = f"/v1.0/project/users/{self.operator_user_id}/tasks"
        body = {"projectId": pid, "content": title, "note": note}
        if self.scenariofieldconfig_id:
            body["scenariofieldconfigId"] = self.scenariofieldconfig_id
        if self.assignee_id:
            body["executorId"] = self.assignee_id
        data = self._request("POST", path, body)
        result = data.get("result") or data
        task_id = str(result.get("taskId") or result.get("id") or "")
        if not task_id:
            raise RuntimeError(f"TEAMBITION_CREATE_FAIL: {data}")
        return WorkItem(
            id=task_id,
            title=title,
            note=note,
            status="created",
            provider=self.name,
            raw=result,
        )

    def update_status(self, work_item_id: str, status: str, note: str = "") -> tuple[bool, str]:
        if not valid_id(work_item_id, self.id_pattern):
            return False, f"TEAMBITION_INVALID_ID: 须为 24 位十六进制 taskId，匹配 {self.id_pattern}"
        if self.status_update_mode in {"skip", "disabled", "off"}:
            return True, f"TEAMBITION_STATUS_SKIP: status={status}; Teambition 状态流转未启用"
        if self.status_update_mode in self.TASKFLOWSTATUS_MODE_ALIASES:
            return self._update_taskflowstatus(work_item_id, status, note)
        if not self.configured:
            return False, "TEAMBITION_MISSING_ENV"
        if self.status_update_mode != "stage":
            return False, (
                f"TEAMBITION_STATUS_MODE_UNSUPPORTED: {self.status_update_mode}; "
                "请使用 status_update_mode: stage 或 taskflowstatus"
            )
        stage_key = self._canonical_stage_key(status)
        stage_id = self.stage_id_map.get(stage_key)
        if not stage_id:
            return False, (
                f"TEAMBITION_STAGE_MISSING: status={status} stage={stage_key}; "
                f"请在 providers.teambition.stage_id_map.{stage_key} 配置 Teambition stageId"
            )
        try:
            path = f"/v1.0/project/users/{self.operator_user_id}/tasks/{work_item_id}/stages"
            self._request("PUT", path, {"stageId": stage_id})
            return True, f"TEAMBITION_STAGE_UPDATED: status={status} stage={stage_key} stageId={stage_id}"
        except Exception as e:
            return False, f"TEAMBITION_UPDATE_FAIL: {e}"

    def _update_taskflowstatus(self, work_item_id: str, status: str, note: str = "") -> tuple[bool, str]:
        if not self.open_operator_id:
            return False, "TEAMBITION_OPEN_OPERATOR_ID_MISSING: 请配置 TEAMBITION_OPEN_OPERATOR_ID 或 operator_id"
        stage_key = self._canonical_stage_key(status)
        body: dict[str, str] = {}
        status_id = self.taskflowstatus_id_map.get(stage_key)
        status_name = self.taskflowstatus_name_map.get(stage_key, "")
        matched_status: dict[str, Any] | None = None
        try:
            if not status_id:
                statuses = self.taskflow_statuses(work_item_id)
                matched_status = self._find_taskflowstatus(stage_key, statuses, status_name)
                if matched_status:
                    status_id = str(matched_status.get("id") or matched_status.get("_id") or "").strip()
                if not status_id and status_name:
                    body["tfsName"] = status_name
                if not status_id and not body:
                    available = [
                        f"{s.get('name') or ''}({s.get('kind') or ''},{s.get('id') or s.get('_id') or ''})"
                        for s in statuses
                    ]
                    return False, (
                        f"TEAMBITION_TASKFLOWSTATUS_MISSING: status={status} stage={stage_key}; "
                        f"请配置 taskflowstatus_id_map.{stage_key} 或 taskflowstatus_name_map.{stage_key}; "
                        f"available={available}"
                    )
            if status_id:
                body["taskflowstatusId"] = status_id
            if note:
                body["tfsUpdateNote"] = note
            path = f"/api/v3/task/{urllib.parse.quote(work_item_id, safe='')}/taskflowstatus"
            data = self._open_request("PUT", path, body)
            error = self._open_error_message(data)
            if error:
                return False, f"TEAMBITION_TASKFLOWSTATUS_UPDATE_FAIL: {error}"
            result = data.get("result") if isinstance(data.get("result"), dict) else {}
            returned_id = str((result or {}).get("taskflowstatusId") or status_id or "")
            returned_name = str((matched_status or {}).get("name") or status_name or "")
            return (
                True,
                "TEAMBITION_TASKFLOWSTATUS_UPDATED: "
                f"status={status} stage={stage_key} taskflowstatusId={returned_id} tfsName={returned_name}",
            )
        except Exception as e:
            return False, f"TEAMBITION_TASKFLOWSTATUS_UPDATE_FAIL: {e}"

    def list_assigned(self, assignee_id: str | None = None) -> list[WorkItem]:
        uid = assignee_id or self.assignee_id or self.operator_user_id
        if not self.configured:
            return []
        q = urllib.parse.urlencode({"maxResults": 50})
        path = f"/v1.0/project/users/{self.operator_user_id}/projectIds/{self.project_id}/tasks?{q}"
        data = self._request("GET", path)
        tasks = data.get("result") or []
        if isinstance(tasks, dict):
            tasks = tasks.get("tasks") or tasks.get("list") or []
        items: list[WorkItem] = []
        for t in tasks:
            executor = str(t.get("executorId") or "")
            if uid != executor:
                continue
            tid = str(t.get("taskId") or t.get("id") or "")
            if not tid:
                continue
            if t.get("isDone") is True:
                continue
            items.append(
                WorkItem(
                    id=tid,
                    title=t.get("content") or "",
                    status=str(t.get("stageId") or "unknown"),
                    note=(t.get("note") or "")[:200],
                    provider=self.name,
                    raw=t,
                )
            )
        seen = {item.id for item in items}
        for tid in self._local_open_work_item_ids():
            if tid in seen:
                continue
            try:
                item = self.pull(tid)
            except Exception:
                continue
            raw = item.raw or {}
            if raw.get("isDone") is True:
                continue
            executor = str(raw.get("executorId") or "")
            if uid and executor != uid:
                continue
            items.append(item)
            seen.add(tid)
        return items

    def _local_open_work_item_ids(self) -> list[str]:
        if not self.product_root or not self.product_root.is_dir():
            return []
        spec_dir = self.product_root / "ael-workspace/planning/product-specs"
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
                    tid = match.group(1)
                    if tid in seen or not valid_id(tid, self.id_pattern):
                        continue
                    ids.append(tid)
                    seen.add(tid)
        return ids
