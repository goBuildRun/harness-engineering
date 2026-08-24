"""Pure planning helpers shared by the batch planning facade."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from bmad_method_gate import run_gate as run_bmad_gate
from ael_runtime import canonical_digest, now
from ael_task_resolution import valid_task_id
from work_item_contract import (
    extract_from_task_dir,
    resolve_product_spec_path,
    work_item_contract_from_spec,
)
from workspace_paths import Phase0Layout


def _digest_file(path: Path) -> dict[str, str]:
    try:
        data = path.read_bytes()
    except OSError:
        return {"path": str(path), "sha256": "missing"}
    return {"path": str(path), "sha256": hashlib.sha256(data).hexdigest()}


def _shared_input_files(layout: Phase0Layout) -> list[dict[str, str]]:
    roots = [
        layout.product_specs, layout.exec_plans_active, layout.exec_plans_completed,
        layout.bmad_output_root,
    ]
    files: list[Path] = []
    for root in roots:
        if root.is_dir():
            files.extend(sorted(p for p in root.rglob("*") if p.is_file()))
    return [_digest_file(path) for path in sorted({p.resolve() for p in files})]


def _task_input_files(task_dir: Path) -> list[dict[str, str]]:
    generated = {"task.json", "planning_gate_pass.json", "phase0_pass.json"}
    return [_digest_file(path) for path in sorted(p for p in task_dir.rglob("*")
                                                  if p.is_file() and p.name not in generated)]


def _task_binding_digest(task_dir: Path) -> str:
    """Return only the current binding identity, excluding generated gate files."""
    binding = _read_json(task_dir / "task.json")
    stored_task_dir = str(binding.get("task_dir") or "")
    work_item = binding.get("work_item")
    if isinstance(work_item, dict) and stored_task_dir:
        expected = canonical_digest({
            "task_dir": stored_task_dir,
            "work_item": {
                "id": str(work_item.get("id") or ""),
                "provider": str(work_item.get("provider") or "noop"),
            },
            "parent": binding.get("work_item_parent_id"),
        })
        if binding.get("binding_digest") != expected:
            return ""
        return expected
    existing = _existing_work_item(task_dir)
    if existing.get("reason"):
        return canonical_digest(existing)
    return canonical_digest({
        "id": existing.get("id") or "", "provider": existing.get("provider") or "",
    }) if existing.get("id") else ""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _task_title(task_dir: Path) -> str:
    card = task_dir / "00-任务卡.md"
    try:
        text = card.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        text = ""
    match = re.search(r"任务标题[：:]\s*`?([^`\n]+)", text)
    if match and match.group(1).strip():
        return match.group(1).strip()
    heading = re.search(r"^#\s+(.+?)\s*$", text, re.MULTILINE)
    return heading.group(1).strip() if heading else task_dir.name


def _task_contract(layout: Phase0Layout, task_dir: Path, level: str) -> dict[str, Any]:
    _card_id, spec = extract_from_task_dir(task_dir)
    contract: dict[str, Any] = {
        "spec_path": "", "expected_parent_id": None, "production_evidence": None,
    }
    if not spec:
        return contract
    try:
        spec_path, _ = resolve_product_spec_path(
            layout.ael_root, layout.product_root, spec,
        )
        parsed = work_item_contract_from_spec(spec_path, require_l3_type=level == "L3")
    except (OSError, ValueError) as exc:
        contract["error"] = str(exc)
        return contract
    contract.update({
        "spec_path": str(spec_path),
        "expected_parent_id": parsed.get("expected_parent_id"),
        "production_evidence": parsed.get("production_evidence"),
        "item_type": parsed.get("item_type") or "",
    })
    return contract


def _existing_work_item(task_dir: Path) -> dict[str, str]:
    candidates: list[dict[str, str]] = []
    card_id, _ = extract_from_task_dir(task_dir)
    if card_id:
        candidates.append({"id": card_id, "provider": ""})
    for name in ("task.json", "planning_gate_pass.json", "phase0_pass.json"):
        value = _read_json(task_dir / name).get("work_item")
        if isinstance(value, dict) and value.get("id"):
            candidates.append({"id": str(value["id"]), "provider": str(value.get("provider") or "")})
    if not candidates:
        return {"id": "", "provider": ""}
    ids = {item["id"] for item in candidates}
    providers = {item["provider"] for item in candidates if item["provider"]}
    if len(ids) != 1 or len(providers) > 1:
        return {"id": "", "provider": "", "reason": "WORK_ITEM_BINDING_AMBIGUOUS"}
    return {"id": next(iter(ids), ""), "provider": next(iter(providers), "")}


def _local_work_item_id(task_dir: Path, inputs: list[dict[str, str]]) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", task_dir.name.lower()).strip("-")[:54] or "task"
    return f"local-{slug}-{canonical_digest(inputs)[:12]}"


def _write_task_binding(task_dir: Path, work_item: dict[str, Any], *, source: str,
                        contract: dict[str, Any], product_root: Path) -> Path:
    path = task_dir / "task.json"
    existing = _read_json(path)
    try:
        stored_task_dir = str(task_dir.resolve().relative_to(product_root.resolve()))
    except ValueError:
        raise ValueError("TASK_BINDING_OUTSIDE_PRODUCT") from None
    payload = {
        **existing,
        "schema": "harness-task-binding-v2",
        "task_dir": stored_task_dir,
        "work_item": {
            "id": str(work_item.get("id") or ""),
            "provider": str(work_item.get("provider") or "noop"),
        },
        "work_item_parent_id": contract.get("expected_parent_id"),
        "provider_binding_source": source,
        "binding_digest": canonical_digest({
            "task_dir": stored_task_dir, "work_item": work_item,
            "parent": contract.get("expected_parent_id"),
        }),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".task-binding-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path


def _public_work_item(item: Any) -> dict[str, Any]:
    raw = getattr(item, "raw", {}) or {}
    public = {
        "id": str(getattr(item, "id", "") or ""),
        "title": str(getattr(item, "title", "") or ""),
        "status": str(getattr(item, "status", "") or ""),
        "provider": str(getattr(item, "provider", "") or ""),
        "url": getattr(item, "url", None),
        "parent_work_item_id": str(raw.get("parent_task_guid") or raw.get("parent_work_item_id") or ""),
    }
    for key in ("project_id", "projectId", "project_key", "tasklist_guid", "tasklists"):
        if key in raw:
            value = raw[key]
            if key == "tasklists" and isinstance(value, list):
                value = sorted({
                    str(item.get("tasklist_guid") or "") for item in value
                    if isinstance(item, dict) and item.get("tasklist_guid")
                })
            public[key] = value
    return public


def _provider_context_digest(provider: Any, mode: str, project_id: str | None) -> str:
    """Digest adapter semantics without persisting raw credentials or tokens."""
    if provider is None:
        return canonical_digest({"mode": mode, "provider": "noop", "project": project_id})
    attrs: dict[str, Any] = {}
    for key, value in sorted(vars(provider).items()):
        lowered = key.lower()
        if key.startswith("_") or lowered in {
            "token", "api_token", "app_secret", "password", "created", "calls",
            "call_count", "request_count", "requests",
        }:
            continue
        if any(marker in lowered for marker in ("secret", "password", "authorization")):
            attrs[key] = {"present": bool(value), "digest": canonical_digest(str(value))}
            continue
        if "token" in lowered:
            attrs[key] = {"present": bool(value), "digest": canonical_digest(str(value))}
            continue
        try:
            json.dumps(value)
            attrs[key] = value
        except TypeError:
            attrs[key] = str(value)
    return canonical_digest({
        "mode": mode, "provider": str(getattr(provider, "name", "")),
        "class": f"{provider.__class__.__module__}.{provider.__class__.__qualname__}",
        "project": project_id, "attrs": attrs,
    })


def _shared_readiness(layout: Phase0Layout, level: str, task_dirs: list[Path],
                      shared_inputs: list[dict[str, str]]) -> dict[str, Any]:
    cache_file = layout.agent_workspace / "planning" / "shared-readiness.json"
    shared_digest = canonical_digest({"level": level, "inputs": shared_inputs})
    task_inputs = [
        {"task_dir": str(task_dir), "inputs": _task_input_files(task_dir)}
        for task_dir in task_dirs
    ]
    readiness_digest = canonical_digest({
        "level": level, "shared_digest": shared_digest, "task_inputs": task_inputs,
    })
    cached = _read_json(cache_file)
    cached_by_task = {
        str(item.get("task_dir") or ""): item
        for item in (cached.get("bmad", {}).get("results", []) or [])
        if isinstance(item, dict)
    }
    bmad_results = []
    for task_dir in task_dirs:
        task_key = str(task_dir)
        current_task = next(item for item in task_inputs if item["task_dir"] == task_key)
        previous = cached_by_task.get(task_key)
        if previous and previous.get("task_digest") == canonical_digest(current_task["inputs"]):
            bmad_results.append({**previous, "cache_hit": True})
            continue
        try:
            outcome = run_bmad_gate(level, layout, task_key)
        except (OSError, ValueError, RuntimeError) as exc:
            outcome = {"decision": "block", "reason": f"BMAD_GATE_EXCEPTION: {exc}"}
        bmad_results.append({
            "task_dir": task_key, "task_digest": canonical_digest(current_task["inputs"]),
            "decision": outcome.get("decision"),
            "reason": str(outcome.get("reason") or ""), "cache_hit": False,
        })
    bmad_decision = "pass" if all(item["decision"] == "pass" for item in bmad_results) else "pending"
    architecture_files = [item for item in shared_inputs if any(
        marker in item["path"].lower() for marker in ("architecture", "exec-plans", "design-artifacts")
    )]
    readiness_inputs = [
        item for task in task_inputs for item in task["inputs"]
        if Path(item["path"]).name in {"03-实施方案.md", "04-实施记录.md"}
    ]
    readiness = {
        "schema": "harness-shared-readiness-v1",
        "decision": "pass" if bmad_decision == "pass" else "guarded",
        "reason": "SHARED_BMAD_ARCH_READINESS_READY" if bmad_decision == "pass" else "SHARED_READINESS_DEFERRED_TO_EXECUTION_GATE",
        "level": level,
        "shared_digest": shared_digest,
        "readiness_digest": readiness_digest,
        "task_inputs": task_inputs,
        "bmad": {"decision": bmad_decision, "results": bmad_results,
                 "digest": canonical_digest(bmad_results)},
        "architecture": {"digest": canonical_digest(architecture_files), "inputs": architecture_files},
        "implementation_readiness": {
            "digest": canonical_digest({"task_dirs": [str(path) for path in task_dirs],
                                         "inputs": readiness_inputs,
                                         "required": ["03-实施方案.md", "04-实施记录.md"]}),
            "task_count": len(task_dirs), "inputs": readiness_inputs,
        },
        "cache_hit": bool(bmad_results) and all(item.get("cache_hit") for item in bmad_results),
        "created_at": now(),
    }
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(readiness, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return readiness


def _task_id(task_dir: Path, level: str, index: int, inputs: list[dict[str, str]],
             provider_mode: str) -> str:
    existing = _existing_work_item(task_dir)
    if existing.get("reason"):
        return ""
    work_item = existing.get("id") or ""
    if work_item and valid_task_id(work_item):
        return work_item
    return _local_work_item_id(task_dir, inputs)


def _gate(task_dir: Path, product: Path, level: str, work_item_id: str,
          provider_mode: str, provider_name: str) -> dict[str, Any]:
    return {
        "schema": "harness-planning-credential-v1", "decision": "pass",
        "gate": "planning", "level": level, "task_dir": str(task_dir),
        "product_root": str(product),
        "work_item": {"id": work_item_id, "provider": provider_name},
        "created_at": now(), "source": "harness-plan-batch",
    }
