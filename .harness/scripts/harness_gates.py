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
from workspace_paths import active_planning_gate_path, load_layout


TIER_GATES = {
    "lite": ("harness", "structure", "quality_lint", "quality_test"),
    "standard": (
        "planning", "harness", "structure", "plan_sync", "dag_sync",
        "qa_evidence", "knowledge", "growth_review", "growth_freshness",
        "quality_lint", "quality_test",
    ),
    "strict": (
        "planning", "harness", "structure", "plan_sync", "dag_sync",
        "qa_evidence", "knowledge", "growth_review", "growth_freshness",
        "quality_lint", "quality_test", "strict_evidence",
    ),
}

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


def _strict_evidence(subject_digest: str) -> dict[str, Any]:
    path = Path(os.environ.get("HARNESS_STRICT_EVIDENCE", ""))
    try:
        receipt = json.loads(path.read_text(encoding="utf-8")) if str(path) != "." else {}
    except (OSError, json.JSONDecodeError):
        receipt = {}
    required = ("browser_qa", "deployment", "rollback")
    valid = (
        receipt.get("subject_digest") == subject_digest
        and all((receipt.get(name) or {}).get("decision") == "pass" for name in required)
    )
    return {
        "decision": "pass" if valid else "block",
        "reason": "STRICT_EVIDENCE_OK" if valid else "STRICT_EVIDENCE_REQUIRED",
    }


def prepare_ci_task(harness: Path, product: Path, task_id: str) -> bool:
    layout = load_layout(harness, product)
    task_dir = layout.tasks / task_id
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


def _task_work_item(task_dir: Path) -> dict[str, str] | None:
    for name in ("task.json", "planning_gate_pass.json", "phase0_pass.json"):
        try:
            data = json.loads((task_dir / name).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        value = data.get("work_item")
        if isinstance(value, dict) and value.get("id"):
            return {"id": str(value["id"]), "provider": str(value.get("provider") or "")}
        if isinstance(value, str) and value:
            return {"id": value, "provider": ""}
    return None


def committed_work_item(harness: Path, product: Path, task_id: str) -> dict[str, str] | None:
    tasks = load_layout(harness, product).tasks
    direct = _task_work_item(tasks / task_id)
    if direct:
        return direct
    if not tasks.is_dir():
        return None
    matches = [value for task_dir in tasks.iterdir() if task_dir.is_dir()
               and (value := _task_work_item(task_dir)) and value["id"] == task_id]
    return matches[0] if len(matches) == 1 else None


def run_gate_plan(harness: Path, product: Path, *, tier: str,
                  subject_digest: str, policy_digest: str,
                  ci_task_id: str = "", changed_files: list[str] | None = None,
                  read_only: bool = False) -> dict[str, Any]:
    if ci_task_id and tier != "lite":
        prepare_ci_task(harness, product, ci_task_id)
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
    checks: dict[str, dict[str, Any]] = {}
    for name in required_gates:
        started = time.monotonic()
        if name == "planning":
            gate = active_planning_gate_path(load_layout(harness, product))
            try:
                credential = json.loads(gate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                credential = {}
            payload = {
                "decision": "pass" if credential.get("decision") == "pass" else "block",
                "reason": "PLANNING_CREDENTIAL_OK" if credential.get("decision") == "pass"
                else "NO_PLANNING_GATE",
            }
            returncode = 0
            command = ["read", str(gate)]
        elif name == "strict_evidence":
            payload = _strict_evidence(subject_digest)
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
                completed = subprocess.run(
                    command, cwd=harness, env=env, text=True,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
                )
                payload = _payload(completed.stdout, completed.returncode)
                returncode = completed.returncode
        else:
            completed = subprocess.run(
                commands[name], cwd=harness, env=env, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
            )
            payload = _payload(completed.stdout, completed.returncode)
            returncode = completed.returncode
            command = commands[name]
            if autosync and payload["decision"] == "block" and name == "knowledge":
                subprocess.run(
                    ["bash", str(harness / ".harness/scripts/harness_knowledge.sh"), "sync-planning"],
                    cwd=harness, env=env, text=True, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, check=False,
                )
                completed = subprocess.run(
                    command, cwd=harness, env=env, text=True,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
                )
                payload = _payload(completed.stdout, completed.returncode)
            if (autosync and payload["decision"] == "block" and name == "growth_freshness"
                    and payload.get("reason") == "GROWTH_REPORT_MISSING"):
                subprocess.run(
                    ["bash", str(harness / ".harness/scripts/harness_growth.sh"), "scan"],
                    cwd=harness, env=env, text=True, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, check=False,
                )
                completed = subprocess.run(
                    command, cwd=harness, env=env, text=True,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
                )
                payload = _payload(completed.stdout, completed.returncode)
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
