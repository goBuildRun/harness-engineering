#!/usr/bin/env python3
"""Tier-aware structured adapters for existing Harness gates."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from harness_runtime import canonical_digest, now
from harness_output import dump_json
from process_control import run_process_group
from harness_task_resolution import valid_task_id
from workspace_paths import active_planning_gate_path, load_layout


TIER_GATES = {
    "lite": ("harness", "structure", "diff_integrity", "quality_lint", "quality_test"),
    "standard": (
        "planning", "harness", "structure", "diff_integrity", "plan_sync", "dag_sync",
        "qa_evidence", "knowledge", "growth_review", "growth_freshness",
        "quality_lint", "quality_test",
    ),
    "strict": (
        "planning", "harness", "structure", "diff_integrity", "plan_sync", "dag_sync",
        "qa_evidence", "knowledge", "growth_review", "growth_freshness",
        "quality_lint", "quality_test", "strict_evidence",
    ),
}
DEFAULT_GATE_TIMEOUT_SECONDS = 3600


def checks_for_tier(checks: dict[str, dict[str, Any]], tier: str) -> dict[str, dict[str, Any]]:
    required = {"tier", "scope", "code_health", *TIER_GATES[tier]}
    return {name: check for name, check in checks.items() if name in required}

FRONTEND_PARTS = {"frontend", "web", "ui", "app", "pages", "components"}
FRONTEND_SUFFIXES = {".tsx", ".jsx", ".vue", ".svelte", ".html", ".css", ".scss"}


def browser_required(changed_files: list[str]) -> bool:
    return any(
        Path(path).suffix.lower() in FRONTEND_SUFFIXES
        or bool(set(Path(path).parts) & FRONTEND_PARTS)
        for path in changed_files
    )


def _commands(harness: Path) -> dict[str, list[str]]:
    scripts = harness / ".harness/scripts"
    return {
        "harness": ["bash", str(scripts / "validate_harness.sh")],
        "structure": ["bash", str(scripts / "structure_guard.sh"), "--diff"],
        "diff_integrity": [
            "python3", str(scripts / "diff_integrity_check.py"),
            "--harness-root", str(harness),
        ],
        "plan_sync": ["bash", str(scripts / "plan_sync_check.sh")],
        "dag_sync": ["bash", str(scripts / "dag_sync_check.sh")],
        "qa_evidence": ["bash", str(scripts / "qa_evidence_check.sh")],
        "knowledge": ["bash", str(scripts / "harness_knowledge.sh"), "check-planning"],
        "growth_review": ["bash", str(scripts / "harness_growth.sh"), "review-status"],
        "growth_freshness": ["bash", str(scripts / "harness_growth.sh"), "freshness"],
        "quality_lint": ["bash", str(scripts / "quality_commands.sh"), "lint"],
        "quality_test": ["bash", str(scripts / "quality_commands.sh"), "test"],
    }


def _payload(output: str, returncode: int) -> dict[str, Any]:
    try:
        value = json.loads(output)
    except json.JSONDecodeError:
        return {"decision": "block", "reason": "GATE_OUTPUT_INVALID"}
    if not isinstance(value, dict) or value.get("decision") not in {"pass", "block"}:
        return {"decision": "block", "reason": "GATE_OUTPUT_INVALID"}
    if returncode != 0:
        value["decision"] = "block"
        value["reason"] = f"GATE_EXIT_{returncode}: {value.get('reason', '')}"
    return value


def _gate_timeout_seconds() -> int:
    raw = os.environ.get("HARNESS_GATE_TIMEOUT_SECONDS", "")
    try:
        return max(1, int(raw or DEFAULT_GATE_TIMEOUT_SECONDS))
    except ValueError:
        return DEFAULT_GATE_TIMEOUT_SECONDS


def _run_gate_command(command: list[str], *, gate: str, cwd: Path,
                      env: dict[str, str], timeout: int) -> tuple[dict[str, Any], int]:
    try:
        completed = run_process_group(command, cwd=cwd, env=env, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        log = " ".join(str(output).splitlines()[-25:])[:2000]
        reason = f"GATE_TIMEOUT: gate={gate}; timeout={timeout}s"
        if log:
            reason += f"; log={log}"
        return {"decision": "block", "reason": reason}, 124
    return _payload(completed.stdout, completed.returncode), completed.returncode


def _strict_evidence(subject_digest: str, production_policy: Any = None) -> dict[str, Any]:
    path = Path(os.environ.get("HARNESS_STRICT_EVIDENCE", ""))
    try:
        receipt = json.loads(path.read_text(encoding="utf-8")) if str(path) != "." else {}
    except (OSError, json.JSONDecodeError):
        receipt = {}
    required = ("browser_qa", "deployment", "rollback")
    base_valid = (
        receipt.get("subject_digest") == subject_digest
        and all((receipt.get(name) or {}).get("decision") == "pass" for name in required)
    )
    if production_policy is not None and not isinstance(production_policy, dict):
        return {"decision": "block", "reason": "STRICT_PRODUCTION_POLICY_INVALID"}
    provider_mode = str((production_policy or {}).get("provider_mode") or "").strip()
    if provider_mode not in {"", "synthetic_allowed", "real_required"}:
        return {"decision": "block", "reason": "STRICT_PRODUCTION_POLICY_INVALID"}
    if base_valid and provider_mode == "real_required":
        provider_acceptance = receipt.get("provider_acceptance") or {}
        provider_valid = (
            isinstance(provider_acceptance, dict)
            and provider_acceptance.get("decision") == "pass"
            and provider_acceptance.get("provider_mode") == "real"
            and provider_acceptance.get("synthetic_only") is False
            and isinstance(provider_acceptance.get("evidence_ref"), str)
            and bool(provider_acceptance["evidence_ref"].strip())
        )
        if not provider_valid:
            return {
                "decision": "block",
                "reason": "STRICT_REAL_PROVIDER_EVIDENCE_REQUIRED",
            }
    return {
        "decision": "pass" if base_valid else "block",
        "reason": "STRICT_EVIDENCE_OK" if base_valid else "STRICT_EVIDENCE_REQUIRED",
    }


def prepare_ci_task(harness: Path, product: Path, task_id: str) -> bool:
    if not valid_task_id(task_id):
        return False
    layout = load_layout(harness, product)
    task_dir = layout.tasks / task_id
    if not task_dir.is_dir() and layout.tasks.is_dir():
        matches = [
            candidate for candidate in layout.tasks.iterdir()
            if candidate.is_dir()
            and (_task_work_item(candidate) or {}).get("id") == task_id
        ]
        if len(matches) == 1:
            task_dir = matches[0]
    source = next(
        (path for path in (task_dir / "planning_gate_pass.json", task_dir / "phase0_pass.json")
         if path.is_file()),
        None,
    )
    if source is None:
        return False
    try:
        credential = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if credential.get("decision") != "pass":
        return False
    credential["task_dir"] = str(task_dir.resolve())
    target = layout.runs_root / "planning_gate_pass.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(credential, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    work_item = str((credential.get("work_item") or {}).get("id") or task_id)
    (layout.runs_root / "active_task.json").write_text(
        json.dumps({"work_item_id": work_item, "task_id": task_id}, indent=2) + "\n",
        encoding="utf-8",
    )
    return True


def _task_work_item_resolution(task_dir: Path) -> dict[str, Any]:
    candidates = []
    for name in ("task.json", "planning_gate_pass.json", "phase0_pass.json"):
        try:
            data = json.loads((task_dir / name).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        value = data.get("work_item")
        if isinstance(value, dict) and value.get("id"):
            candidates.append({"source": f"{task_dir.name}/{name}", "id": str(value["id"]),
                               "provider": str(value.get("provider") or "")})
        elif isinstance(value, str) and value:
            candidates.append({"source": f"{task_dir.name}/{name}", "id": value, "provider": ""})
    candidates.sort(key=lambda item: item["source"])
    ids = {item["id"] for item in candidates}
    providers = {item["provider"] for item in candidates if item["provider"]}
    if len(ids) == 1 and len(providers) <= 1:
        return {"status": "unique", "work_item": {"id": next(iter(ids)),
                "provider": next(iter(providers), "")}, "candidates": candidates}
    status = "ambiguous" if candidates else "missing"
    return {"status": status, "work_item": None, "candidates": candidates}


def _task_work_item(task_dir: Path) -> dict[str, str] | None:
    return _task_work_item_resolution(task_dir)["work_item"]


def committed_work_item_resolution(harness: Path, product: Path, task_id: str) -> dict[str, Any]:
    if not valid_task_id(task_id):
        return {"status": "missing", "work_item": None, "candidates": []}
    tasks = load_layout(harness, product).tasks
    if not tasks.is_dir():
        return {"status": "missing", "work_item": None, "candidates": []}
    records = [(task_dir, resolution) for task_dir in sorted(tasks.iterdir()) if task_dir.is_dir()
               and (resolution := _task_work_item_resolution(task_dir))["candidates"]]
    direct = next((value for task_dir, value in records if task_dir.name == task_id), None)
    target_ids = {task_id, *(item["id"] for item in (direct or {}).get("candidates", []))}
    matches = [(task_dir, value) for task_dir, value in records if task_dir.name == task_id
               or any(item["id"] in target_ids for item in value["candidates"])]
    candidates = sorted((item for _task_dir, value in matches for item in value["candidates"]),
                        key=lambda item: item["source"])
    if len(matches) == 1 and matches[0][1]["status"] == "unique":
        return {"status": "unique", "work_item": matches[0][1]["work_item"],
                "candidates": candidates}
    status = "ambiguous" if matches else "missing"
    return {"status": status, "work_item": None, "candidates": candidates}


def committed_work_item(harness: Path, product: Path, task_id: str) -> dict[str, str] | None:
    return committed_work_item_resolution(harness, product, task_id)["work_item"]


def run_gate_plan(harness: Path, product: Path, *, tier: str,
                  subject_digest: str, policy_digest: str,
                  ci_task_id: str = "", changed_files: list[str] | None = None,
                  read_only: bool = False) -> dict[str, Any]:
    if ci_task_id and tier != "lite":
        prepare_ci_task(harness, product, ci_task_id)
    layout = load_layout(harness, product)
    planning_gate = active_planning_gate_path(layout)
    try:
        planning_credential = json.loads(planning_gate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        planning_credential = {}
    commands = _commands(harness)
    required_gates = list(TIER_GATES[tier])
    if tier == "standard" and browser_required(changed_files or []):
        required_gates.append("browser_qa")
    env = {**os.environ, "HARNESS_PRODUCT_ROOT": str(product)}
    if read_only:
        env.update({"CI": "true", "HARNESS_GATE_READ_ONLY": "1"})
    autosync = not read_only and not any(env.get(name, "").lower() == "true" for name in (
        "CI", "GITHUB_ACTIONS", "GITLAB_CI",
    ))
    timeout = _gate_timeout_seconds()
    checks: dict[str, dict[str, Any]] = {}
    for name in required_gates:
        started = time.monotonic()
        if name == "planning":
            payload = {
                "decision": "pass" if planning_credential.get("decision") == "pass" else "block",
                "reason": "PLANNING_CREDENTIAL_OK" if planning_credential.get("decision") == "pass"
                else "NO_PLANNING_GATE",
            }
            returncode = 0
            command = ["read", str(planning_gate)]
        elif name == "strict_evidence":
            work_item = planning_credential.get("work_item") or {}
            production_policy = (
                work_item.get("production_evidence") if isinstance(work_item, dict) else None
            )
            payload = _strict_evidence(subject_digest, production_policy)
            returncode = 0
            command = ["read", "HARNESS_STRICT_EVIDENCE"]
        elif name == "browser_qa":
            url = os.environ.get("HARNESS_BROWSER_QA_URL", "")
            if not url:
                payload = {"decision": "block", "reason": "BROWSER_QA_CONFIG_REQUIRED"}
                returncode = 0
                command = ["browser_qa", "missing-url"]
            else:
                command = [
                    "python3", str(harness / ".harness/scripts/browser_qa.py"), url,
                    "--action", "audit", "--harness-root", str(harness),
                    "--product-root", str(product),
                ]
                payload, returncode = _run_gate_command(
                    command, gate=name, cwd=harness, env=env, timeout=timeout,
                )
        else:
            if name == "diff_integrity" and read_only:
                commands[name] = [*commands[name], "--commit", subject_digest]
            payload, returncode = _run_gate_command(
                commands[name], gate=name, cwd=harness, env=env, timeout=timeout,
            )
            command = commands[name]
            if (autosync and payload["decision"] == "block" and name == "knowledge"
                    and not str(payload.get("reason") or "").startswith("GATE_TIMEOUT:")):
                sync_payload, _ = _run_gate_command(
                    ["bash", str(harness / ".harness/scripts/harness_knowledge.sh"), "sync-planning"],
                    gate="knowledge_autosync", cwd=harness, env=env, timeout=timeout,
                )
                if sync_payload["decision"] == "pass":
                    payload, returncode = _run_gate_command(
                        command, gate=name, cwd=harness, env=env, timeout=timeout,
                    )
                else:
                    payload = sync_payload
            if (autosync and payload["decision"] == "block" and name == "growth_freshness"
                    and payload.get("reason") == "GROWTH_REPORT_MISSING"):
                sync_payload, _ = _run_gate_command(
                    ["bash", str(harness / ".harness/scripts/harness_growth.sh"), "scan"],
                    gate="growth_autosync", cwd=harness, env=env, timeout=timeout,
                )
                if sync_payload["decision"] == "pass":
                    payload, returncode = _run_gate_command(
                        command, gate=name, cwd=harness, env=env, timeout=timeout,
                    )
                else:
                    payload = sync_payload
        duration = int((time.monotonic() - started) * 1000)
        checks[name] = {
            "decision": payload["decision"], "source": "executed",
            "subject_digest": subject_digest, "policy_digest": policy_digest,
            "fingerprint": canonical_digest({
                "gate": name, "subject": subject_digest, "policy": policy_digest,
                "tier": tier, "command": command,
            }),
            "completed_at": now(), "duration_ms": duration,
            "reason": str(payload.get("reason") or ""),
        }
    required = set(required_gates)
    missing = sorted(required - set(checks))
    decision = "pass" if not missing and all(
        check["decision"] == "pass" for check in checks.values()
    ) else "block"
    return {"decision": decision, "checks": checks, "missing": missing}


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--harness-root", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--product-root", default=os.environ.get("HARNESS_PRODUCT_ROOT", os.getcwd()))
    parser.add_argument("--tier", choices=tuple(TIER_GATES), default="standard")
    parser.add_argument("--subject", default="compat-check")
    parser.add_argument("--policy", default="compat-check")
    args = parser.parse_args()
    result = run_gate_plan(
        Path(args.harness_root).resolve(), Path(args.product_root).resolve(),
        tier=args.tier, subject_digest=args.subject, policy_digest=args.policy,
    )
    dump_json(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
