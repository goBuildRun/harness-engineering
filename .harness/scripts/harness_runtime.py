#!/usr/bin/env python3
"""Lean Harness state, tier, fingerprint, code-health, and CLI orchestration."""
from __future__ import annotations

import ast
import fcntl
import hashlib
import io
import json
import os
import re
import subprocess
import tempfile
import time
import tokenize
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from harness_schema import assert_result
from harness_assurance import snapshot as assurance_snapshot
from harness_tier import TIERS, classify_tier, task_kind_tier
from harness_gc_context import build_gc_context

SCHEMA_VERSION = 1
UNKNOWN = "unknown"
CODE_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".java", ".rs"}
DEBUG_RE = re.compile(r"console\.log\s*\(|println!\s*\(|^\s*print\s*\(", re.MULTILINE)
PROCESS_RE = re.compile(r"TODO:\s*AI|FIXME:\s*agent|AI fixed", re.IGNORECASE)
DEAD_RE = re.compile(r"\b(?:def|function)\s+(?:unused|dead|obsolete)[A-Za-z0-9_]*\b", re.IGNORECASE)
IMPORT_RE = re.compile(r"^(?:from\s+([\w.]+)\s+import|import\s+([\w.]+)|.*from\s+['\"]([^'\"]+)['\"])", re.MULTILINE)


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def fingerprint(gate: str, subject_digest: str, policy_digest: str, *inputs: Any) -> str:
    return canonical_digest(
        {"schema": SCHEMA_VERSION, "gate": gate, "subject": subject_digest,
         "policy": policy_digest, "inputs": inputs}
    )


def default_result(task_id: str, *, initial_tier: str = "standard", work_item: Any = None) -> dict[str, Any]:
    if initial_tier not in TIERS:
        initial_tier = "standard"
    return {
        "schema_version": SCHEMA_VERSION,
        "task_id": task_id,
        "work_item": work_item,
        "state": "active",
        "enforcement": "shadow",
        "assurance": assurance_snapshot("local"),
        "tier": {"initial": initial_tier, "effective": initial_tier},
        "binding_digest": "",
        "baseline": {"digest": "", "source": "start"},
        "subject": {"kind": "worktree", "digest": ""},
        "policy_digest": "",
        "invariants": {
            "task_identity": "pending", "scope": "pending",
            "risk_validation": "pending", "final_result": "pending",
        },
        "checks": {},
        "cost": {
            "implementation": {
                "input_tokens": UNKNOWN, "output_tokens": UNKNOWN,
                "context_chars": UNKNOWN, "agent_calls": UNKNOWN,
            },
            "harness": {
                "input_tokens": UNKNOWN, "output_tokens": UNKNOWN,
                "context_chars": 0, "agent_calls": 0,
                "gate_duration_ms": 0, "reruns": 0,
                "cache_hits": 0,
            },
            "telemetry_complete": False,
        },
        "blockers": [],
        "decision": "block",
        "updated_at": now(),
    }


def atomic_write_result(path: Path, result: dict[str, Any]) -> None:
    assert_result(result)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        result["updated_at"] = now()
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(result, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def load_result(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "assurance" not in data:
        data["assurance"] = assurance_snapshot(
            "enforced" if data.get("enforcement") == "enforced" else "local"
        )
    assert_result(data)
    return data


def _module_roots(paths: list[str]) -> set[str]:
    return {Path(path).parts[0] for path in paths if len(Path(path).parts) > 1}


def has_process_comment(text: str, suffix: str) -> bool:
    if suffix != ".py":
        return bool(PROCESS_RE.search(text))
    comments = (token.string for token in tokenize.generate_tokens(io.StringIO(text).readline)
                if token.type == tokenize.COMMENT)
    return any(PROCESS_RE.search(comment) for comment in comments)


def mechanical_code_health(repo: Path, changed_files: list[str], *, tier: str) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    triggers: set[str] = set()
    code_files = [path for path in changed_files if Path(path).suffix in CODE_SUFFIXES]
    if len(_module_roots(code_files)) > 1:
        triggers.add("cross_module")
    normalized = {path.replace("\\", "/") for path in changed_files}
    for rel in code_files:
        path = repo / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if has_process_comment(text, path.suffix):
            triggers.add("process_comment")
            findings.append({"severity": "block", "kind": "process_comment", "path": rel})
        if "/test" not in f"/{rel.lower()}" and DEBUG_RE.search(text):
            triggers.add("debug_output")
            findings.append({"severity": "block", "kind": "debug_output", "path": rel})
        if DEAD_RE.search(text):
            triggers.add("dead_code")
            findings.append({"severity": "review", "kind": "dead_code", "path": rel})
        if path.suffix == ".py":
            try:
                tree = ast.parse(text)
                imported: dict[str, int] = {}
                used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imported.update({(alias.asname or alias.name.split(".")[0]): node.lineno for alias in node.names})
                    elif isinstance(node, ast.ImportFrom) and node.module != "__future__":
                        imported.update({(alias.asname or alias.name): node.lineno for alias in node.names if alias.name != "*"})
                for name, line in imported.items():
                    if name not in used:
                        triggers.add("unused_import")
                        findings.append({"severity": "review", "kind": "unused_import", "path": f"{rel}:{line}"})
            except SyntaxError:
                pass
        if len(text.splitlines()) > 400:
            triggers.add("large_file")
            findings.append({"severity": "review", "kind": "large_file", "path": rel})
        for match in IMPORT_RE.finditer(text):
            imported = next((value for value in match.groups() if value), "")
            local = imported.replace(".", "/").lstrip("./")
            if local and any(other.startswith(local + ".") for other in normalized):
                triggers.add("direct_dependency_changed")
    agent_required = tier == "strict" or (tier == "standard" and bool(triggers))
    decision = "block" if any(item["severity"] == "block" for item in findings) else "pass"
    subject_digest = subject_for(repo, changed_files)
    return {
        "decision": decision,
        "mode": "mechanical",
        "triggers": sorted(triggers),
        "findings": len(findings),
        "finding_details": findings,
        "remediated": 0,
        "deferred_work_items": [],
        "subject_digest": subject_digest,
        "fingerprint": "",
        "source": "executed",
        "agent_required": agent_required,
        "completed_at": now(),
    }


def subject_for(repo: Path, changed_files: list[str]) -> str:
    entries = []
    for rel in sorted(set(changed_files)):
        path = repo / rel
        entries.append((rel, hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "absent"))
    return canonical_digest(entries)


def cache_matches(check: dict[str, Any], expected_fingerprint: str,
                  subject_digest: str, policy_digest: str) -> bool:
    return (
        check.get("source") in {None, "executed", "cache"}
        and check.get("fingerprint") == expected_fingerprint
        and check.get("subject_digest") == subject_digest
        and check.get("policy_digest", policy_digest) == policy_digest
    )


def valid_gc_result(gc_result: dict[str, Any] | None, result: dict[str, Any],
                    subject_digest: str, policy_digest: str) -> bool:
    return bool(
        gc_result and gc_result.get("decision") == "pass"
        and gc_result.get("role") == "gc-sweeper" and gc_result.get("independent") is True
        and gc_result.get("task_id") == result.get("task_id")
        and gc_result.get("subject_digest") == subject_digest
        and gc_result.get("policy_digest") == policy_digest
    )


def apply_code_health(result: dict[str, Any], mechanical: dict[str, Any], *,
                      gc_result: dict[str, Any] | None,
                      subject_digest: str = "", policy_digest: str = "") -> None:
    subject_digest = subject_digest or mechanical.get("subject_digest", "")
    required = bool(mechanical.get("agent_required"))
    combined = {key: value for key, value in mechanical.items() if key not in {"agent_required", "finding_details"}}
    combined["mode"] = "mechanical+agent" if required else "mechanical"
    combined["policy_digest"] = policy_digest
    if required:
        valid = valid_gc_result(gc_result, result, subject_digest, policy_digest)
        if not valid:
            combined["decision"] = "block"
            result["blockers"] = sorted(set(result.get("blockers", [])) | {"GC_REQUIRED"})
        else:
            deferred = gc_result.get("deferred_work_items", [])
            if gc_result.get("deferred_findings", 0) and not deferred:
                combined["decision"] = "block"
                result["blockers"] = sorted(
                    set(result.get("blockers", [])) | {"DEFERRED_WORK_ITEM_REQUIRED"}
                )
            combined.update({
                "findings": gc_result.get("findings", combined.get("findings", 0)),
                "remediated": gc_result.get("remediated", 0),
                "deferred_work_items": deferred,
                "agent_result_digest": canonical_digest(gc_result),
            })
            combined["fingerprint"] = fingerprint(
                "code_health", subject_digest, policy_digest,
                mechanical.get("fingerprint", ""), combined["agent_result_digest"],
            )
            if "DEFERRED_WORK_ITEM_REQUIRED" not in result.get("blockers", []):
                combined["decision"] = "pass" if mechanical.get("decision") == "pass" else "block"
    result.setdefault("checks", {})["code_health"] = combined


def telemetry_add_gc(result: dict[str, Any], *, context_chars: int, duration_ms: int) -> None:
    cost = result["cost"]["harness"]
    cost["agent_calls"] = int(cost.get("agent_calls") or 0) + 1
    cost["context_chars"] = int(cost.get("context_chars") or 0) + context_chars
    cost["gate_duration_ms"] = int(cost.get("gate_duration_ms") or 0) + duration_ms


def finish_decision(result: dict[str, Any]) -> str:
    invariant_pass = all(value == "pass" for value in result.get("invariants", {}).values())
    checks_pass = all(check.get("decision") == "pass" for check in result.get("checks", {}).values())
    decision = "pass" if invariant_pass and checks_pass and not result.get("blockers") else "block"
    result["decision"] = decision
    result["state"] = "validated" if decision == "pass" else "blocked"
    return decision


def git_changed(repo: Path, commit: str = "") -> list[str]:
    commands = []
    if commit:
        try:
            parent = subprocess.check_output(
                ["git", "rev-parse", f"{commit}^1"], cwd=repo, text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
            commands.append(["git", "diff", "--name-only", "-z", parent, commit])
        except (FileNotFoundError, subprocess.CalledProcessError):
            commands.append([
                "git", "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", "-z", commit,
            ])
    else:
        commands.extend((
            ["git", "diff", "--name-only", "-z", "HEAD"],
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        ))
    found: list[str] = []
    for command in commands:
        try:
            output = subprocess.check_output(command, cwd=repo, text=True, stderr=subprocess.DEVNULL)
            found.extend(path for path in output.split("\0") if path)
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass
    return list(dict.fromkeys(found))


def workspace_root(product: Path) -> Path:
    return product / "harness-workspace"


def result_path(product: Path, task_id: str) -> Path:
    return workspace_root(product) / "runs" / "tasks" / task_id / "result.json"


def active_task_path(product: Path) -> Path:
    return workspace_root(product) / "runs" / "active_task.json"


def resolve_task_id(product: Path, requested: str | None) -> tuple[str, list[str]]:
    if requested:
        return requested, []
    try:
        active = json.loads(active_task_path(product).read_text(encoding="utf-8"))
        task_id = str(active.get("task_id") or active.get("work_item_id") or "")
        if task_id and result_path(product, task_id).is_file():
            return task_id, []
    except (OSError, json.JSONDecodeError):
        pass
    tasks_root = workspace_root(product) / "runs" / "tasks"
    candidates = sorted(
        path.parent.name for path in tasks_root.glob("*/result.json")
        if load_result(path).get("state") in {"active", "blocked"}
    ) if tasks_root.is_dir() else []
    return (candidates[0], []) if len(candidates) == 1 else ("", candidates)


def policy_for(harness: Path, product: Path) -> str:
    paths = [harness / ".harness" / "config.yaml", product / "harness-workspace" / "project.yaml",
             harness / ".harness" / "rules" / "gc-golden-principles.md"]
    return canonical_digest([(str(path.relative_to(harness) if path.is_relative_to(harness) else path.name),
                              hashlib.sha256(path.read_bytes()).hexdigest()) for path in paths if path.is_file()])


def read_gc_result(task_root: Path) -> dict[str, Any] | None:
    path = task_root / "gc_result.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def invoke_gc_once(result: dict[str, Any], task_root: Path, mechanical: dict[str, Any],
                   changed: list[str], repo: Path) -> dict[str, Any] | None:
    existing = read_gc_result(task_root)
    if valid_gc_result(existing, result, mechanical.get("subject_digest", ""),
                       result.get("policy_digest", "")):
        return existing
    argv_json = os.environ.get("HARNESS_GC_AGENT_ARGV", "").strip()
    if not argv_json:
        return None
    if result["cost"]["harness"].get("agent_calls", 0) >= 1:
        result["blockers"] = sorted(set(result.get("blockers", [])) | {"BUDGET_APPROVAL_REQUIRED"})
        return None
    try:
        argv = json.loads(argv_json)
        if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
            raise ValueError
    except (json.JSONDecodeError, ValueError):
        result["blockers"] = sorted(set(result.get("blockers", [])) | {"GC_RUNNER_INVALID"})
        return None
    try:
        context, context_chars = build_gc_context(result, mechanical, changed, repo)
    except ValueError:
        result["blockers"] = sorted(
            set(result.get("blockers", [])) | {"BUDGET_APPROVAL_REQUIRED"})
        return None
    context_path = task_root / "gc_context.json"
    context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    started = time.monotonic()
    completed = subprocess.run([*argv, str(context_path), str(task_root / "gc_result.json")], check=False)
    telemetry_add_gc(result, context_chars=context_chars,
                     duration_ms=int((time.monotonic() - started) * 1000))
    if completed.returncode != 0:
        result["blockers"] = sorted(set(result.get("blockers", [])) | {"GC_REQUIRED"})
        return None
    return read_gc_result(task_root)


def main() -> int:
    from harness_cli import main as commands_main

    return commands_main()


if __name__ == "__main__":
    raise SystemExit(main())
