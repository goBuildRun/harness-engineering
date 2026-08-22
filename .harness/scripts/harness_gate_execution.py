#!/usr/bin/env python3
"""Dependency-scoped cache keys and bounded parallel execution for read-only gates."""
from __future__ import annotations

import ast
import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from harness_cache import CACHEABLE_GATES, reuse_check
from harness_runtime import canonical_digest, now
from harness_timing import append_event


PARALLEL_READ_ONLY_GATES = {
    "harness", "structure", "diff_integrity", "plan_sync", "dag_sync",
    "qa_evidence", "knowledge", "growth_review", "growth_freshness", "growth_release",
}
NEVER_CACHE_GATES = {
    "qa_evidence", "growth_review", "growth_freshness", "growth_release", "quality_lint",
    "quality_test", "browser_qa", "strict_evidence", "provider", "deployment",
    "rollback", "gc_agent",
}
DEFAULT_TIMEOUTS = {
    "quality_lint": 180,
    "quality_test": 600,
    "browser_qa": 300,
}
SHELL_TOOL_REF = re.compile(r"[A-Za-z0-9_.-]+\.(?:py|sh)")


@dataclass(frozen=True)
class GateSpec:
    name: str
    command: tuple[str, ...]
    input_digest: str
    fingerprint: str
    timeout_seconds: float
    budget_ms: int
    parallel_safe: bool
    cacheable: bool


def _sha(path: Path) -> str:
    try:
        content = path.read_bytes()
        if not isinstance(content, bytes):
            return "absent"
        return hashlib.sha256(content).hexdigest()
    except (OSError, TypeError):
        return "absent"


def _tree_digest(root: Path, *, suffixes: set[str] | None = None) -> str:
    if not root.is_dir():
        return "absent"
    entries = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if suffixes and path.suffix not in suffixes:
            continue
        entries.append((str(path.relative_to(root)), _sha(path)))
    return canonical_digest(entries)


def _manifest_inputs(harness: Path) -> str:
    manifest = harness / ".harness/harness-manifest.yaml"
    try:
        import yaml
        payload = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    except (ImportError, OSError, ValueError):
        return _sha(manifest)
    required = [
        (str(rel), _sha(harness / str(rel)))
        for rel in payload.get("required_files") or []
    ]
    return canonical_digest({
        "manifest": _sha(manifest), "required": required,
        "agents": _tree_digest(harness / ".harness/agents", suffixes={".yaml", ".md"}),
    })


def _tool_dependency_entries(harness: Path, command: list[str]) -> list[tuple[str, str]]:
    scripts = (harness / ".harness/scripts").resolve()
    pending: list[Path] = []
    missing: list[tuple[str, str]] = []
    for item in command:
        if not item.endswith((".py", ".sh")):
            continue
        path = Path(item)
        path = path if path.is_absolute() else harness / path
        try:
            resolved = path.resolve()
            resolved.relative_to(scripts)
        except (OSError, ValueError):
            missing.append((str(path), _sha(path)))
            continue
        pending.append(resolved)
    seen: set[Path] = set()
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        candidates: set[Path] = set()
        if path.suffix == ".py":
            try:
                tree = ast.parse(text)
            except SyntaxError:
                tree = None
            for node in ast.walk(tree) if tree is not None else []:
                modules: list[str] = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules = [node.module]
                for module in modules:
                    candidates.add(scripts / f"{module.replace('.', '/')}".replace("//", "/"))
        else:
            candidates.update(scripts / name for name in SHELL_TOOL_REF.findall(text))
        for candidate in candidates:
            python_file = candidate.with_suffix(".py") if not candidate.suffix else candidate
            package_file = candidate / "__init__.py"
            for dependency in (python_file, package_file):
                if dependency.is_file() and dependency not in seen:
                    pending.append(dependency.resolve())
    entries = [(str(path.relative_to(harness.resolve())), _sha(path)) for path in sorted(seen)]
    return sorted(entries + missing)


def _safe_task_dir(product: Path, planning_credential: dict[str, Any]) -> Path | None:
    raw = str(planning_credential.get("task_dir") or "").strip()
    if not raw:
        return None
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = product / candidate
    try:
        candidate = candidate.resolve()
        candidate.relative_to(product.resolve())
    except (OSError, ValueError):
        return None
    return candidate


def gate_input_digest(
    name: str,
    *,
    harness: Path,
    product: Path,
    changed_files: list[str],
    planning_gate: Path,
    planning_credential: dict[str, Any],
    command: list[str],
) -> str:
    task_dir = _safe_task_dir(product, planning_credential)
    changed_paths = sorted(set(changed_files))
    changed_content = [
        (rel, _sha(product / rel)) for rel in changed_paths
        if not rel.startswith("harness-workspace/runs/")
    ]
    project = product / "harness-workspace/project.yaml"
    common = {"gate": name, "tools": _tool_dependency_entries(harness, command)}
    if name == "harness":
        value: Any = {
            **common,
            "manifest_inputs": _manifest_inputs(harness),
            "config": _sha(harness / ".harness/config.yaml"),
        }
    elif name == "structure":
        value = {
            **common, "paths": changed_paths, "project": _sha(project),
            "rules": _tree_digest(harness / ".harness/rules", suffixes={".md", ".yaml"}),
        }
    elif name == "diff_integrity":
        value = {**common, "changed": changed_content}
    elif name in {"plan_sync", "dag_sync"}:
        value = {
            **common, "paths": changed_paths,
            "plan": _sha(task_dir / "03-实施方案.md") if task_dir else "absent",
            "dag": _sha(task_dir / "tasks-dag.md") if task_dir else "absent",
            "project": _sha(project),
        }
    elif name == "qa_evidence":
        value = {
            **common, "changed": changed_content,
            "plan": _sha(task_dir / "03-实施方案.md") if task_dir else "absent",
            "evidence": _tree_digest(product / "harness-workspace/evidence", suffixes={".md", ".json"}),
            "receipts": _tree_digest(product / "harness-workspace/runs", suffixes={".json"}),
        }
    elif name == "knowledge":
        value = {
            **common,
            "planning": _tree_digest(product / "harness-workspace/planning", suffixes={".md", ".json"}),
            "context": _sha(product / "harness-workspace/knowledge/CONTEXT.md"),
            "project": _sha(project),
        }
    elif name.startswith("growth_"):
        value = {
            **common,
            "progress": _tree_digest(product / "harness-workspace/evidence/progress", suffixes={".md"}),
            "growth": _tree_digest(product / "harness-workspace/evidence/growth-reports", suffixes={".md"}),
        }
    elif name.startswith("quality_"):
        value = {**common, "changed": changed_content, "project": _sha(project)}
    else:
        value = {
            **common, "changed": changed_content, "planning_gate": _sha(planning_gate),
        }
    return canonical_digest(value)


def build_spec(
    name: str,
    command: list[str],
    *,
    input_digest: str,
    tier: str,
    configured_timeout: int,
    remaining_budget_ms: int | None,
) -> GateSpec:
    gate_timeout = float(DEFAULT_TIMEOUTS.get(name, configured_timeout))
    if configured_timeout != 120:
        gate_timeout = configured_timeout
    if remaining_budget_ms is not None:
        gate_timeout = min(gate_timeout, max(0.001, remaining_budget_ms / 1000))
    fingerprint = canonical_digest({
        "schema": 2, "gate": name, "input_digest": input_digest,
        "tier": tier, "command_kind": Path(command[0]).name if command else "read",
    })
    return GateSpec(
        name=name,
        command=tuple(command),
        input_digest=input_digest,
        fingerprint=fingerprint,
        timeout_seconds=gate_timeout,
        budget_ms=max(1, int(gate_timeout * 1000)),
        parallel_safe=name in PARALLEL_READ_ONLY_GATES,
        cacheable=name in CACHEABLE_GATES and name not in NEVER_CACHE_GATES,
    )


def _executed_check(
    spec: GateSpec, payload: dict[str, Any], *, returncode: int, duration_ms: int,
    subject_digest: str, policy_digest: str, started_at: str,
) -> dict[str, Any]:
    reason = str(payload.get("reason") or "")
    timeout_kind = "gate" if returncode == 124 else "none"
    remediation = ""
    if spec.name == "knowledge" and payload.get("decision") == "block":
        remediation = "harness_knowledge.sh sync-planning"
    elif spec.name == "growth_freshness" and reason.startswith("GROWTH_"):
        remediation = "harness_growth.sh scan"
    return {
        "decision": payload["decision"],
        "source": "executed",
        "subject_digest": subject_digest,
        "policy_digest": policy_digest,
        "input_digest": spec.input_digest,
        "fingerprint": spec.fingerprint,
        "started_at": started_at,
        "completed_at": now(),
        "duration_ms": duration_ms,
        "tool_wait_ms": duration_ms,
        "attempt": 1,
        "retry_limit": 1,
        "budget_ms": spec.budget_ms,
        "timeout_kind": timeout_kind,
        "reason": reason,
        **({"next_action": remediation} if remediation else {}),
    }


def execute_specs(
    specs: list[GateSpec],
    *,
    runner: Callable[[list[str], str, float], tuple[dict[str, Any], int]],
    previous_checks: dict[str, dict[str, Any]],
    subject_digest: str,
    policy_digest: str,
    ledger: Path | None = None,
    task_id: str = "",
    work_item_id: str = "",
    remaining_budget_ms: int | None = None,
) -> tuple[dict[str, dict[str, Any]], int]:
    checks: dict[str, dict[str, Any]] = {}
    pending: list[GateSpec] = []
    cache_hits = 0
    for spec in specs:
        cached = reuse_check(
            previous_checks.get(spec.name), gate=spec.name, fingerprint=spec.fingerprint,
            subject_digest=subject_digest, policy_digest=policy_digest,
            input_digest=spec.input_digest,
        ) if spec.cacheable else None
        if cached:
            cached.update({"duration_ms": 0, "tool_wait_ms": 0, "cache_hit": True})
            checks[spec.name] = cached
            cache_hits += 1
        else:
            pending.append(spec)

    deadline = (
        time.monotonic() + max(0, remaining_budget_ms) / 1000
        if remaining_budget_ms is not None else None
    )

    def execute(spec: GateSpec) -> tuple[str, dict[str, Any]]:
        started_at = now()
        started_epoch_ms = int(time.time() * 1000)
        span_id = f"gate-{spec.name}-{started_epoch_ms}"
        remaining = spec.timeout_seconds
        if deadline is not None:
            remaining = min(remaining, max(0.0, deadline - time.monotonic()))
        if remaining <= 0:
            return spec.name, {
                "decision": "block", "source": "not_executed",
                "subject_digest": subject_digest, "policy_digest": policy_digest,
                "input_digest": spec.input_digest, "fingerprint": spec.fingerprint,
                "started_at": started_at, "completed_at": now(),
                "duration_ms": 0, "tool_wait_ms": 0, "attempt": 0,
                "retry_limit": 1, "budget_ms": 0, "timeout_kind": "stage",
                "reason": "STAGE_BUDGET_EXCEEDED",
            }
        if ledger is not None:
            append_event(ledger, {
                "task_id": task_id, "work_item_id": work_item_id,
                "stage": f"gate:{spec.name}", "event": "start", "span_id": span_id,
                "parent_span_id": "finalize", "attempt": 1, "epoch_ms": started_epoch_ms,
                "input_digest": spec.input_digest, "budget_ms": spec.budget_ms,
                "reason": "GATE_STARTED",
            })
        started = time.monotonic()
        payload, returncode = runner(list(spec.command), spec.name, remaining)
        duration_ms = max(0, int((time.monotonic() - started) * 1000))
        check = _executed_check(
            spec, payload, returncode=returncode, duration_ms=duration_ms,
            subject_digest=subject_digest, policy_digest=policy_digest,
            started_at=started_at,
        )
        if deadline is not None and time.monotonic() > deadline:
            check.update({
                "decision": "block", "reason": "STAGE_BUDGET_EXCEEDED",
                "timeout_kind": "stage",
            })
        if ledger is not None:
            append_event(ledger, {
                "task_id": task_id, "work_item_id": work_item_id,
                "stage": f"gate:{spec.name}", "event": "end", "span_id": span_id,
                "attempt": 1, "epoch_ms": int(time.time() * 1000),
                "wall_ms": duration_ms, "tool_wait_ms": duration_ms,
                "decision": check["decision"],
                "reason": (
                    "STAGE_BUDGET_EXCEEDED" if check["reason"] == "STAGE_BUDGET_EXCEEDED"
                    else "GATE_TIMEOUT" if returncode == 124 else "GATE_FINISHED"
                ),
                "input_digest": spec.input_digest, "cache_hit": False,
            })
        return spec.name, check

    parallel = [spec for spec in pending if spec.parallel_safe]
    serial = [spec for spec in pending if not spec.parallel_safe]
    if parallel:
        with ThreadPoolExecutor(max_workers=min(8, len(parallel))) as pool:
            futures = [pool.submit(execute, spec) for spec in parallel]
            for future in as_completed(futures):
                name, check = future.result()
                checks[name] = check
    for spec in serial:
        name, check = execute(spec)
        checks[name] = check
    return checks, cache_hits
