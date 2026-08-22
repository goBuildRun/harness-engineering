#!/usr/bin/env python3
"""work_item.py — Work Item 统一 CLI（stdout JSON，exit 0）。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from harness_knowledge import sync_planning
from product_context import ProductContextError, resolve_product_root
from provider_lifecycle import TERMINAL_STATUSES, validate_terminal_receipt
from harness_output import dump_json
from work_item_diagnostics import cmd_capabilities, cmd_diagnose
from workspace_paths import load_layout
from work_item_providers import (
    DEFAULT_HARNESS_NAME,
    FeishuProvider,
    TeambitionProvider,
    apply_assignee,
    extract_from_task_dir,
    gate_check,
    get_provider,
    load_config,
    provider_expected_project_id,
    sync_spec_markdown,
    work_item_drafts_from_spec,
)


def emit(decision: str, reason: str, **extra) -> None:
    dump_json({"decision": decision, "reason": reason, **extra})


def harness_root_from_script() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def cmd_sync_spec(args: argparse.Namespace) -> int:
    root = Path(args.harness_root or harness_root_from_script())
    provider = get_provider(root)
    assignee = (args.assignee or "").strip()
    apply_assignee(provider, assignee)
    spec = Path(args.spec)
    if not spec.is_file():
        emit("block", f"SYNC_SPEC_MISSING: {spec}")
        return 0
    try:
        count, msg = sync_spec_markdown(
            provider,
            spec,
            assignee=assignee,
            parent_work_item_id=(args.parent_id or "").strip(),
        )
    except Exception as e:
        emit("block", f"WORK_ITEM_SYNC_FAILED: {e}", provider=provider.name)
        return 0
    emit("pass", f"WORK_ITEM_SYNC: {msg}", provider=provider.name, count=count, assignee=assignee)
    return 0


def cmd_draft_spec(args: argparse.Namespace) -> int:
    root = Path(args.harness_root or harness_root_from_script())
    provider = get_provider(root)
    spec = Path(args.spec)
    if not spec.is_file():
        emit("block", f"DRAFT_SPEC_MISSING: {spec}")
        return 0
    assignee = (args.assignee or "").strip()
    try:
        drafts = work_item_drafts_from_spec(
            spec,
            assignee=assignee,
            parent_work_item_id=(args.parent_id or "").strip(),
            require_l3_type=True,
        )
    except Exception as e:
        emit("block", f"WORK_ITEM_DRAFT_FAILED: {e}", provider=provider.name)
        return 0
    emit(
        "pass",
        f"WORK_ITEM_DRAFT: {len(drafts)} items generated for confirmation",
        contract="bmad-work-item-v1",
        provider=provider.name,
        count=len(drafts),
        assignee=assignee,
        drafts=drafts,
    )
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    root = Path(args.harness_root or harness_root_from_script())
    cfg = load_config(root)
    provider = get_provider(root)
    from work_item_providers import level_requires_work_item

    if args.level and not level_requires_work_item(cfg, args.level):
        emit("pass", f"WORK_ITEM_SKIP: {args.level} 豁免 Work Item 绑定")
        return 0
    ok, reason = provider.verify_binding(
        args.id,
        expected_project_id=provider_expected_project_id(provider),
        expected_parent_id=(args.parent_id or "").strip() or None,
    )
    emit("pass" if ok else "block", reason, provider=provider.name, work_item_id=args.id)
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    root = Path(args.harness_root or harness_root_from_script())
    provider = get_provider(root)
    try:
        item = provider.pull(args.id)
        emit("pass", f"WORK_ITEM_PULL: {item.title}", work_item=item.to_dict())
    except Exception as e:
        emit("block", str(e))
    return 0


def cmd_close(args: argparse.Namespace) -> int:
    root = Path(args.harness_root or harness_root_from_script())
    provider = get_provider(root)
    normalized_status = str(args.status or "").strip().lower()
    if normalized_status in TERMINAL_STATUSES:
        receipt = None
        if args.lifecycle_receipt:
            try:
                receipt = json.loads(Path(args.lifecycle_receipt).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        try:
            product_root = resolve_product_root(root)
        except ProductContextError as exc:
            emit("block", f"ACCEPTANCE_PRODUCT_ROOT_INVALID: {exc}", work_item_id=args.id)
            return 0
        ok, reason = validate_terminal_receipt(
            receipt, work_item_id=args.id, expected_ref=(args.accepted_ref or "").strip(),
            expected_commit=(args.accepted_commit or "").strip(),
            configured_provider=provider.name, repo=product_root,
        )
        if not ok:
            emit("block", reason, work_item_id=args.id)
            return 0
    ok, reason = provider.update_status(args.id, args.status, args.note or "")
    extra: dict[str, object] = {}
    if ok:
        try:
            layout = load_layout(root)
            extra["knowledge_sync"] = sync_planning(layout)
        except Exception as e:
            emit("block", f"{reason}; KNOWLEDGE_SYNC_FAILED: {e}", provider=provider.name)
            return 0
    emit("pass" if ok else "block", reason, provider=provider.name, **extra)
    return 0


def cmd_update_description(args: argparse.Namespace) -> int:
    root = Path(args.harness_root or harness_root_from_script())
    description_file = Path(args.file)
    if not description_file.is_file():
        emit("block", f"WORK_ITEM_DESCRIPTION_FILE_MISSING: {description_file}")
        return 0
    description = description_file.read_text(encoding="utf-8")
    if not description.strip():
        emit("block", f"WORK_ITEM_DESCRIPTION_FILE_EMPTY: {description_file}")
        return 0
    provider = get_provider(root)
    ok, reason = provider.update_description(args.id, description)
    emit(
        "pass" if ok else "block",
        reason,
        provider=provider.name,
        work_item_id=args.id,
        source_file=str(description_file),
        chars=len(description),
    )
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    root = Path(args.harness_root or harness_root_from_script())
    result = gate_check(root, args.level, args.task_dir)
    if result["ok"]:
        emit("pass", "WORK_ITEM_GATE_OK", work_item=result.get("work_item"), provider=result.get("provider"))
    else:
        emit("block", "; ".join(result["failures"]), work_item=result.get("work_item"))
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    td = Path(args.task_dir)
    wi_id, spec = extract_from_task_dir(td)
    emit("pass", "WORK_ITEM_EXTRACT", work_item_id=wi_id, product_spec=spec)
    return 0


def cmd_scenario_configs(args: argparse.Namespace) -> int:
    root = Path(args.harness_root or harness_root_from_script())
    provider = get_provider(root)
    if not isinstance(provider, TeambitionProvider):
        emit("block", f"WORK_ITEM_SCENARIO_CONFIGS_UNSUPPORTED: provider={provider.name}")
        return 0
    try:
        configs = provider.scenario_field_configs(
            keyword=args.keyword or "",
            sfc_ids=args.sfc_ids or "",
            page_token=args.page_token or "",
            page_size=args.page_size,
        )
    except Exception as e:
        emit("block", f"TEAMBITION_SCENARIO_CONFIGS_FAILED: {e}", provider=provider.name)
        return 0
    emit(
        "pass",
        f"TEAMBITION_SCENARIO_CONFIGS: {len(configs)} 条",
        provider=provider.name,
        tenant_id=provider.tenant_id,
        tenant_type=provider.tenant_type,
        configs=configs,
    )
    return 0


def cmd_list_mine(args: argparse.Namespace) -> int:
    root = Path(args.harness_root or harness_root_from_script())
    provider = get_provider(root)
    assignee_env_map = {
        "teambition": "DINGTALK_OPERATOR_USER_ID",
        "feishu": "FEISHU_ASSIGNEE_ID",
        "jira": "JIRA_ASSIGNEE_ID",
    }
    assignee_env = assignee_env_map.get(provider.name, "")
    assignee = args.assignee or os.environ.get(assignee_env, "")
    try:
        items = provider.list_assigned(assignee or None)
    except Exception as e:
        emit("block", f"WORK_ITEM_LIST_FAILED: {e}", provider=provider.name, assignee=assignee)
        return 0
    emit("pass", f"WORK_ITEM_LIST: {len(items)} 条", items=[i.to_dict() for i in items], assignee=assignee)
    return 0


def cmd_feishu_tasklist_member(args: argparse.Namespace) -> int:
    root = Path(args.harness_root or harness_root_from_script())
    provider = get_provider(root)
    if not isinstance(provider, FeishuProvider):
        emit("block", f"FEISHU_TASKLIST_MEMBER_UNSUPPORTED: provider={provider.name}")
        return 0
    try:
        data = provider.ensure_tasklist_member(
            tasklist_guid=args.tasklist_guid or None,
            member_id=args.member_id or None,
            member_type=args.member_type,
            role=args.role,
            access_token=args.user_access_token or os.environ.get("FEISHU_USER_ACCESS_TOKEN", "") or None,
        )
    except Exception as e:
        emit("block", f"FEISHU_TASKLIST_MEMBER_FAILED: {e}", provider=provider.name)
        return 0
    emit(
        "pass",
        "FEISHU_TASKLIST_MEMBER_OK",
        provider=provider.name,
        tasklist_guid=args.tasklist_guid or provider.tasklist_guid,
        member_id=args.member_id or provider.app_id,
        member_type=args.member_type,
        role=args.role,
        raw=data,
    )
    return 0


def cmd_update_title(args: argparse.Namespace) -> int:
    root = Path(args.harness_root or harness_root_from_script())
    provider = get_provider(root)
    ok, reason = provider.update_title(args.id, args.title)
    emit("pass" if ok else "block", reason, provider=provider.name, work_item_id=args.id)
    return 0


def cmd_create_subtask(args: argparse.Namespace) -> int:
    root = Path(args.harness_root or harness_root_from_script())
    provider = get_provider(root)
    try:
        item = provider.create_subtask(args.parent_id, args.title, args.note)
    except Exception as e:
        emit("block", f"WORK_ITEM_SUBTASK_CREATE_FAILED: {e}", provider=provider.name)
        return 0
    emit("pass", f"WORK_ITEM_SUBTASK_CREATED: {item.id}", provider=provider.name, work_item=item.to_dict())
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=f"{DEFAULT_HARNESS_NAME} Work Item CLI")
    parser.add_argument("--harness-root", default="")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_draft = sub.add_parser("draft-spec")
    p_draft.add_argument("spec")
    p_draft.add_argument("--assignee", default="", help="可选：确认后要分配的 provider 用户 ID")
    p_draft.add_argument("--parent-id", default="", help="可选：父 Work Item ID；须与 spec front matter 一致")
    p_draft.set_defaults(func=cmd_draft_spec)

    p_sync = sub.add_parser("sync-spec")
    p_sync.add_argument("spec")
    p_sync.add_argument("--assignee", default="", help="可选：覆盖本次创建任务的负责人 ID")
    p_sync.add_argument("--parent-id", default="", help="可选：父 Work Item ID；须与 spec front matter 一致")
    p_sync.set_defaults(func=cmd_sync_spec)

    p_v = sub.add_parser("verify")
    p_v.add_argument("--id", required=True)
    p_v.add_argument("--level", default="")
    p_v.add_argument("--parent-id", default="", help="可选：同时精确校验父 Work Item ID")
    p_v.set_defaults(func=cmd_verify)

    p_pull = sub.add_parser("pull")
    p_pull.add_argument("--id", required=True)
    p_pull.set_defaults(func=cmd_pull)

    p_close = sub.add_parser("close")
    p_close.add_argument("--id", required=True)
    p_close.add_argument("--status", default="ready_to_release")
    p_close.add_argument("--note", default="")
    p_close.add_argument("--lifecycle-receipt", default="")
    p_close.add_argument("--accepted-ref", default="")
    p_close.add_argument("--accepted-commit", default="")
    p_close.set_defaults(func=cmd_close)

    p_update_description = sub.add_parser("update-description")
    p_update_description.add_argument("--id", required=True)
    p_update_description.add_argument("--file", required=True, help="包含完整 Work Item 描述的 UTF-8 Markdown 文件")
    p_update_description.set_defaults(func=cmd_update_description)

    p_update_title = sub.add_parser("update-title")
    p_update_title.add_argument("--id", required=True)
    p_update_title.add_argument("--title", required=True)
    p_update_title.set_defaults(func=cmd_update_title)

    p_create_subtask = sub.add_parser("create-subtask")
    p_create_subtask.add_argument("--parent-id", required=True)
    p_create_subtask.add_argument("--title", required=True)
    p_create_subtask.add_argument("--note", default="")
    p_create_subtask.set_defaults(func=cmd_create_subtask)

    p_gate = sub.add_parser("gate")
    p_gate.add_argument("--level", required=True)
    p_gate.add_argument("--task-dir", default="")
    p_gate.set_defaults(func=cmd_gate)

    p_ext = sub.add_parser("extract")
    p_ext.add_argument("--task-dir", required=True)
    p_ext.set_defaults(func=cmd_extract)

    p_diag = sub.add_parser("diagnose")
    p_diag.add_argument("--id", default="", help="可选：只读校验一个已有 Work Item ID")
    p_diag.add_argument("--parent-id", default="", help="可选：同时精确校验父 Work Item ID")
    p_diag.add_argument("--create-smoke-title", default="", help="可选：显式创建一个 smoke task 用于真实写入联调")
    p_diag.set_defaults(func=cmd_diagnose)

    p_caps = sub.add_parser("capabilities")
    p_caps.set_defaults(func=cmd_capabilities)

    p_scenario = sub.add_parser("scenario-configs")
    p_scenario.add_argument("--keyword", default="", help="可选：按名称关键词筛选 Teambition 类型配置")
    p_scenario.add_argument("--sfc-ids", default="", help="可选：逗号分隔的任务类型 ID 列表")
    p_scenario.add_argument("--page-token", default="", help="可选：分页标")
    p_scenario.add_argument("--page-size", type=int, default=50, help="可选：每页数量，默认 50")
    p_scenario.set_defaults(func=cmd_scenario_configs)

    p_list = sub.add_parser("list-mine")
    p_list.add_argument("--assignee", default="")
    p_list.set_defaults(func=cmd_list_mine)

    p_feishu_member = sub.add_parser("feishu-tasklist-member")
    p_feishu_member.add_argument("--tasklist-guid", default="")
    p_feishu_member.add_argument("--member-id", default="", help="默认使用当前 FEISHU_APP_ID")
    p_feishu_member.add_argument("--member-type", default="app", choices=["app", "user", "chat"])
    p_feishu_member.add_argument("--role", default="editor", choices=["editor", "viewer"])
    p_feishu_member.add_argument("--user-access-token", default="", help="可选：用有清单编辑权限的用户 token 添加 app 成员")
    p_feishu_member.set_defaults(func=cmd_feishu_tasklist_member)

    args = parser.parse_args()
    try:
        return args.func(args)
    except Exception as exc:
        if exc.__class__.__name__ == "ProductContextError":
            emit("block", f"PRODUCT_CONTEXT_ERROR: {exc}")
            return 0
        raise


if __name__ == "__main__":
    sys.exit(main())
