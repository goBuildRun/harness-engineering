#!/usr/bin/env python3
"""Pure Feishu task payload normalization helpers."""
from __future__ import annotations

from typing import Any


class FeishuPayloadMixin:
    @staticmethod
    def _task_payload(data: dict[str, Any]) -> dict[str, Any]:
        payload = data.get("data") or data.get("result") or data
        if isinstance(payload, dict) and isinstance(payload.get("task"), dict):
            return payload["task"]
        if isinstance(payload, dict) and isinstance(payload.get("subtask"), dict):
            return payload["subtask"]
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _task_id(task: dict[str, Any]) -> str:
        return str(task.get("guid") or task.get("task_guid") or task.get("id") or "")

    @staticmethod
    def _task_list_payload(data: dict[str, Any]) -> list[dict[str, Any]]:
        payload = data.get("data") or data.get("result") or data
        if isinstance(payload, list):
            return [t for t in payload if isinstance(t, dict)]
        if not isinstance(payload, dict):
            return []
        for key in ("tasks", "items", "list", "task_list"):
            value = payload.get(key)
            if isinstance(value, list):
                return [t for t in value if isinstance(t, dict)]
        if isinstance(payload.get("task"), dict):
            return [payload["task"]]
        return []

    @staticmethod
    def _task_assignee_ids(task: dict[str, Any]) -> set[str]:
        ids: set[str] = set()
        for key in ("members", "assignees", "assignee_members"):
            value = task.get(key) or []
            if isinstance(value, list):
                for member in value:
                    if not isinstance(member, dict):
                        continue
                    role = str(member.get("role") or member.get("member_role") or "").lower()
                    if role and "assignee" not in role:
                        continue
                    for id_key in ("id", "open_id", "union_id", "user_id", "member_id"):
                        if member.get(id_key):
                            ids.add(str(member[id_key]))
        assignee = task.get("assignee") or task.get("owner")
        if isinstance(assignee, dict):
            for id_key in ("id", "open_id", "union_id", "user_id", "member_id"):
                if assignee.get(id_key):
                    ids.add(str(assignee[id_key]))
        for key in ("assignee_id", "owner_id"):
            if task.get(key):
                ids.add(str(task[key]))
        return ids
