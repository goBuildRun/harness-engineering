#!/usr/bin/env python3
"""Provider diagnostics and capability reporting for the Work Item CLI."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

from harness_output import dump_json
from work_item_providers import (
    FeishuProvider, JiraProvider, TeambitionProvider, get_provider,
    load_config, load_harness_name,
)


def emit(decision: str, reason: str, **extra) -> None:
    dump_json({"decision": decision, "reason": reason, **extra})


def cmd_diagnose(args) -> int:
    root = Path(args.harness_root)
    if (args.parent_id or "").strip() and not (args.id or "").strip():
        emit("block", "WORK_ITEM_DIAGNOSE_PARENT_REQUIRES_ID: --parent-id requires --id")
        return 0
    cfg = load_config(root)
    provider_name = cfg.get("provider", "noop")
    lines: list[str] = [f"provider={provider_name}"]
    try:
        provider = get_provider(root)
    except Exception as exc:
        emit("block", f"WORK_ITEM_PROVIDER_LOAD_FAILED: {exc}", provider=provider_name)
        return 0

    if provider_name == "teambition":
        if not isinstance(provider, TeambitionProvider):
            emit("block", "; ".join(lines + ["PROVIDER_TYPE_MISMATCH"]))
            return 0
        app_key, app_secret = provider.app_key, provider.app_secret
        uid, pid = provider.operator_user_id, provider.project_id
        if not all([app_key, app_secret, uid, pid]):
            lines.append("MISSING_CONFIG: 检查 DingTalk 凭据和产品 project.yaml 的 teambition.project_id")
            emit("block", "; ".join(lines))
            return 0
        try:
            import urllib.parse

            query = urllib.parse.urlencode({"appkey": app_key, "appsecret": app_secret})
            with urllib.request.urlopen(f"https://oapi.dingtalk.com/gettoken?{query}", timeout=30) as response:
                token_data = json.loads(response.read())
            if token_data.get("errcode") != 0:
                emit("block", "; ".join(lines + [f"TOKEN_FAIL: {token_data.get('errmsg')}"]))
                return 0
            lines.append("TOKEN_OK")
        except Exception as exc:
            emit("block", "; ".join(lines + [f"TOKEN_FAIL: {exc}"]))
            return 0
        ok_user, user_message = provider._validate_operator_user_id()
        lines.append(user_message)
        if not ok_user:
            emit("block", "; ".join(lines))
            return 0
        token = provider._get_access_token()
        url = f"{provider.api_host}/v1.0/project/users/{uid}/projectIds/{pid}/tasks?maxResults=1"
        request = urllib.request.Request(url, headers={
            "x-acs-dingtalk-access-token": token, "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(request, timeout=30):
                lines.append(f"PROJECT_TASKS_OK: 可访问项目 {pid}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:400]
            emit("block", "; ".join(lines + [f"PROJECT_TASKS_FAIL HTTP{exc.code}: {detail}"]))
            return 0

    if provider_name == "feishu":
        if not isinstance(provider, FeishuProvider) or not provider.configured:
            emit("block", "; ".join(lines + ["MISSING_ENV: 检查 FEISHU_APP_ID / FEISHU_APP_SECRET"]))
            return 0
        try:
            provider._tenant_access_token()
            lines.append("FEISHU_TOKEN_OK")
        except Exception as exc:
            emit("block", "; ".join(lines + [f"FEISHU_TOKEN_FAIL: {exc}"]))
            return 0
        if provider.tasklist_guid:
            try:
                provider.list_assigned("")
                lines.append(f"FEISHU_TASKLIST_OK: {provider.tasklist_guid}")
            except Exception as exc:
                emit("block", "; ".join(lines + [f"FEISHU_TASKLIST_FAIL: {exc}"]), provider=provider.name)
                return 0
        if args.id:
            ok, reason = provider.verify_binding(
                args.id,
                expected_project_id=provider.tasklist_guid or None,
                expected_parent_id=(args.parent_id or "").strip() or None,
            )
            lines.append(reason)
            if not ok:
                emit("block", "; ".join(lines), provider=provider.name, work_item_id=args.id)
                return 0
        if args.create_smoke_title:
            try:
                item = provider.create(
                    title=args.create_smoke_title,
                    note=f"{load_harness_name(root)} Feishu provider smoke test",
                )
                lines.append(f"FEISHU_CREATE_SMOKE_OK: {item.id}")
            except Exception as exc:
                emit("block", "; ".join(lines + [f"FEISHU_CREATE_SMOKE_FAIL: {exc}"]), provider=provider.name)
                return 0

    if provider_name == "jira":
        if not isinstance(provider, JiraProvider) or not provider.configured:
            emit("block", "; ".join(lines + ["MISSING_ENV: 检查 JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN"]))
            return 0
        try:
            items = provider.list_assigned()
            lines.append(f"JIRA_OK: list-mine 可访问，当前返回 {len(items)} 条")
        except Exception as exc:
            emit("block", "; ".join(lines + [f"JIRA_FAIL: {exc}"]))
            return 0

    emit("pass", "; ".join(lines))
    return 0


def cmd_capabilities(args) -> int:
    provider = get_provider(Path(args.harness_root))
    capability_map = {
        "noop": {"verify": "local", "pull": "local", "create": "local", "list_mine": "empty", "status_update": "local"},
        "teambition": {
            "verify": "verified", "pull": "verified", "create": "verified",
            "list_mine": "verified", "scenario_configs": "open-api-authorization-required",
            "status_update": "stage-map-config-required",
        },
        "feishu": {
            "verify": "basic", "pull": "basic", "create": "basic",
            "list_mine": "configured-tasklist", "status_update": "completed-mode-configurable",
        },
        "jira": {
            "verify": "basic", "pull": "basic", "create": "basic",
            "list_mine": "basic", "status_update": "transition-config-required",
        },
    }
    emit(
        "pass", f"WORK_ITEM_CAPABILITIES: {provider.name}", provider=provider.name,
        capabilities=capability_map.get(provider.name, {}),
    )
    return 0
