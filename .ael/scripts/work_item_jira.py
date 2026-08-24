#!/usr/bin/env python3
"""Jira Work Item provider adapter."""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from work_item_providers import DEFAULT_AEL_NAME, WorkItem, WorkItemProvider, valid_id

class JiraProvider(WorkItemProvider):
    """Jira issue provider for product teams that use Jira as Work Item source."""

    name = "jira"

    def __init__(self, cfg: dict[str, Any], id_pattern: str) -> None:
        self.id_pattern = id_pattern
        self.base_url = (os.environ.get("JIRA_BASE_URL") or str(cfg.get("base_url") or "")).rstrip("/")
        self.email = os.environ.get("JIRA_EMAIL", "")
        self.api_token = os.environ.get("JIRA_API_TOKEN", "")
        self.project_key = os.environ.get("JIRA_PROJECT_KEY", "") or str(cfg.get("project_key") or "")
        self.assignee_id = os.environ.get("JIRA_ASSIGNEE_ID", "") or str(cfg.get("assignee_id") or "")
        self.issue_type = os.environ.get("JIRA_ISSUE_TYPE", "") or str(cfg.get("issue_type") or "Task")
        self.api_version = str(cfg.get("api_version") or "3").strip() or "3"
        self.status_update_mode = str(cfg.get("status_update_mode") or "skip")
        self.done_transition_id = os.environ.get("JIRA_DONE_TRANSITION_ID", "") or str(cfg.get("done_transition_id") or "")

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.email and self.api_token)

    def _headers(self) -> dict[str, str]:
        token = base64.b64encode(f"{self.email}:{self.api_token}".encode()).decode()
        return {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(self, method: str, path: str, body: dict | None = None, query: dict | None = None) -> dict:
        if not self.base_url:
            raise RuntimeError("JIRA_BASE_URL_MISSING")
        url = f"{self.base_url}{path}"
        if query:
            url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v not in ("", None)})
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            err_body = e.read().decode(errors="replace")
            raise RuntimeError(f"JIRA_HTTP_{e.code}: {err_body[:500]}") from e

    @staticmethod
    def _adf(text: str) -> dict[str, Any]:
        return {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": text or f"Created by {DEFAULT_AEL_NAME}"}],
                }
            ],
        }

    def verify(self, work_item_id: str) -> tuple[bool, str]:
        if not valid_id(work_item_id, self.id_pattern):
            return False, f"JIRA_INVALID_ID: 须匹配 {self.id_pattern}"
        if not self.configured:
            return False, "JIRA_MISSING_ENV: 请配置 JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN；离线请显式使用 noop"
        try:
            item = self.pull(work_item_id)
            return True, f"JIRA_OK: {item.title or work_item_id}"
        except Exception as e:
            return False, f"JIRA_VERIFY_FAIL: {e}"

    def pull(self, work_item_id: str) -> WorkItem:
        if not self.configured:
            raise RuntimeError("JIRA_MISSING_ENV")
        data = self._request("GET", f"/rest/api/{self.api_version}/issue/{urllib.parse.quote(work_item_id, safe='')}")
        fields = data.get("fields") or {}
        description = fields.get("description")
        note = description if isinstance(description, str) else json.dumps(description or {}, ensure_ascii=False)
        status = fields.get("status") or {}
        return WorkItem(
            id=str(data.get("key") or work_item_id),
            title=str(fields.get("summary") or ""),
            status=str(status.get("name") or "unknown"),
            note=note,
            acceptance_criteria=[ln.strip("- ").strip() for ln in note.splitlines() if ln.strip().startswith("-")],
            url=f"{self.base_url}/browse/{work_item_id}" if self.base_url else None,
            provider=self.name,
            raw=data,
        )

    def create(self, title: str, note: str = "", project_id: str | None = None) -> WorkItem:
        if not self.configured:
            raise RuntimeError("JIRA_MISSING_ENV")
        if not (project_id or self.project_key):
            raise RuntimeError("JIRA_PROJECT_KEY_MISSING: sync-spec 创建 Jira issue 需要 JIRA_PROJECT_KEY")
        project_key = project_id or self.project_key
        description: Any = self._adf(note) if self.api_version == "3" else note
        body = {
            "fields": {
                "project": {"key": project_key},
                "summary": title,
                "issuetype": {"name": self.issue_type},
                "description": description,
            }
        }
        if self.assignee_id:
            body["fields"]["assignee"] = {"accountId": self.assignee_id}
        data = self._request("POST", f"/rest/api/{self.api_version}/issue", body)
        issue_key = str(data.get("key") or "")
        if not issue_key:
            raise RuntimeError(f"JIRA_CREATE_FAIL: {data}")
        return WorkItem(
            id=issue_key,
            title=title,
            note=note,
            status="created",
            url=f"{self.base_url}/browse/{issue_key}" if self.base_url else None,
            provider=self.name,
            raw=data,
        )

    def update_status(self, work_item_id: str, status: str, note: str = "") -> tuple[bool, str]:
        ok, reason = self.verify(work_item_id)
        if not ok:
            return False, reason
        if self.status_update_mode == "skip":
            return True, f"JIRA_STATUS_SKIP: status={status}; Jira 状态流转未启用"
        transition_id = self.done_transition_id if status in {"done", "closed", "qa_passed"} else ""
        if not transition_id:
            return True, f"JIRA_STATUS_SKIP: status={status}; 未配置 transition id"
        try:
            self._request(
                "POST",
                f"/rest/api/{self.api_version}/issue/{urllib.parse.quote(work_item_id, safe='')}/transitions",
                {"transition": {"id": transition_id}},
            )
            return True, f"JIRA_UPDATED: {status}"
        except Exception as e:
            return False, f"JIRA_UPDATE_FAIL: {e}"

    def list_assigned(self, assignee_id: str | None = None) -> list[WorkItem]:
        if not self.configured:
            return []
        assignee_clause = f'assignee = "{assignee_id}"' if assignee_id else "assignee = currentUser()"
        jql = os.environ.get("JIRA_LIST_MINE_JQL") or str(
            f"{assignee_clause} AND resolution = Unresolved ORDER BY updated DESC"
        )
        data = self._request(
            "GET",
            f"/rest/api/{self.api_version}/search",
            query={"jql": jql, "maxResults": 50, "fields": "summary,status,description"},
        )
        items: list[WorkItem] = []
        for issue in data.get("issues") or []:
            key = str(issue.get("key") or "")
            fields = issue.get("fields") or {}
            status = fields.get("status") or {}
            if key:
                items.append(
                    WorkItem(
                        id=key,
                        title=str(fields.get("summary") or ""),
                        status=str(status.get("name") or "unknown"),
                        note="",
                        url=f"{self.base_url}/browse/{key}" if self.base_url else None,
                        provider=self.name,
                        raw=issue,
                    )
                )
        return items
