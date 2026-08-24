#!/usr/bin/env python3
"""Profile package structure guard — path allowlist + diff scan."""
from __future__ import annotations

import argparse
import fnmatch
import subprocess
import sys
from pathlib import Path

from ael_output import dump_json

try:
    import yaml
except ImportError:
    yaml = None

from business_paths import DEFAULT_BUSINESS_ROOTS, allowlist_path, load_business_roots

BUSINESS_PREFIXES = DEFAULT_BUSINESS_ROOTS


def load_allowlist(rules_path: Path) -> dict:
    if yaml is None or not rules_path.is_file():
        return {
            "allowed_roots": list(BUSINESS_PREFIXES) + ["buildrun-agent-engineering-lifecycle/"],
            "forbidden_globs": ["web/app/*"],
            "protected_prefixes": ["web/app/", "deerflow/"],
            "protected_exceptions": ["services/web/app/"],
            "forbid_root_py": True,
            "api_file_globs": ["**/api/*.py", "**/api/**/*.py"],
            "max_api_file_lines": 120,
        }
    with open(rules_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def emit(decision: str, reason: str) -> None:
    dump_json({"decision": decision, "reason": reason})


def normalize(path: str) -> str:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.lstrip("/")


def under_allowed(path: str, allowed_roots: list[str]) -> bool:
    p = normalize(path)
    for root in allowed_roots:
        r = normalize(root)
        if p == r.rstrip("/") or p.startswith(r):
            return True
    return False


def under_exception(path: str, exceptions: list[str]) -> bool:
    p = normalize(path)
    for ex in exceptions:
        if p.startswith(normalize(ex)):
            return True
    return False


def is_protected_new(path: str, cfg: dict) -> bool:
    p = normalize(path)
    if under_exception(p, cfg.get("protected_exceptions") or []):
        return False
    for pref in cfg.get("protected_prefixes") or []:
        if p.startswith(normalize(pref)):
            return True
    return False


def is_business_path(path: str, roots: tuple[str, ...] = BUSINESS_PREFIXES) -> bool:
    p = normalize(path)
    return any(p.startswith(prefix) for prefix in roots)


def check_path(path: str, cfg: dict) -> list[str]:
    issues = []
    p = normalize(path)
    if not p or p.endswith("/"):
        return issues

    allowed = cfg.get("allowed_roots") or []
    allow_all = bool(cfg.get("allow_all"))
    if not allow_all and not under_allowed(p, allowed):
        issues.append(f"OUT_OF_ALLOWLIST: {p} 允许根: {', '.join(allowed)}")

    if cfg.get("forbid_root_py", True) and "/" not in p and p.endswith(".py"):
        issues.append(f"ROOT_PY_FORBIDDEN: {p}")

    for glob_pat in cfg.get("forbidden_globs") or []:
        if fnmatch.fnmatch(p, glob_pat):
            if glob_pat.startswith("web/app/") and under_exception(p, cfg.get("protected_exceptions") or []):
                continue
            issues.append(f"FORBIDDEN_GLOB: {p} matches {glob_pat}")

    if is_protected_new(p, cfg) and not allow_all and not under_allowed(p, allowed):
        issues.append(f"PROTECTED_PREFIX: {p}")

    return issues


def git_changed_files(repo_root: Path) -> list[str]:
    files: list[str] = []
    for cmd in (["git", "diff", "--name-only"], ["git", "diff", "--cached", "--name-only"]):
        try:
            out = subprocess.check_output(cmd, cwd=repo_root, stderr=subprocess.DEVNULL, text=True)
            files.extend(line.strip() for line in out.splitlines() if line.strip())
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
    return list(dict.fromkeys(files))


def check_api_thickness(path: Path, cfg: dict) -> list[str]:
    issues = []
    p = normalize(str(path))
    api_globs = cfg.get("api_file_globs") or []
    if not any(fnmatch.fnmatch(p, pattern) for pattern in api_globs):
        return issues
    if not path.is_file() or path.suffix != ".py":
        return issues
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return issues
    max_lines = int(cfg.get("max_api_file_lines") or 120)
    if len(lines) > max_lines:
        issues.append(
            f"API_TOO_THICK: {p} has {len(lines)} lines (max {max_lines}); move logic to core/"
        )
    return issues


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", action="append", default=[])
    parser.add_argument("--diff", action="store_true")
    parser.add_argument("--ael-root", default=".")
    parser.add_argument("--product-root", default="")
    parser.add_argument("--repo", default="", help="deprecated alias for --product-root")
    parser.add_argument("--profile", default="")
    args = parser.parse_args()

    ael_root = Path(args.ael_root).resolve()
    product_root = Path(args.product_root or args.repo or ".").resolve()
    rules_path = allowlist_path(ael_root, args.profile, product_root)
    if not rules_path.is_file():
        emit("block", f"UNKNOWN_PROFILE: 找不到 profile allowlist: {rules_path}")
        return 0
    cfg = load_allowlist(rules_path)
    business_roots = load_business_roots(ael_root, args.profile, product_root)

    paths: list[str] = list(args.path)

    if args.diff:
        paths.extend(git_changed_files(product_root))

    if not paths:
        emit("pass", "STRUCTURE_SKIP: 无 git 变更；MR 提交业务代码时须有 diff")
        return 0

    # --diff 模式：仅严格检查业务路径变更
    if args.diff and not args.path:
        business_paths = [p for p in paths if is_business_path(p, business_roots)]
        harness_only = [p for p in paths if p.startswith("buildrun-agent-engineering-lifecycle/")]
        if not business_paths:
            if harness_only:
                emit("pass", f"STRUCTURE_SKIP_AEL_ONLY: 仅 harness 变更 {len(harness_only)} 个文件")
            else:
                emit("pass", "STRUCTURE_SKIP: 无业务路径变更")
            return 0
        paths = business_paths

    all_issues: list[str] = []
    for rel in paths:
        rel = normalize(rel)
        if rel.startswith("buildrun-agent-engineering-lifecycle/"):
            rel = rel[len("buildrun-agent-engineering-lifecycle/") :]
        all_issues.extend(check_path(rel, cfg))
        full = product_root / rel
        if full.is_file():
            all_issues.extend(check_api_thickness(full, cfg))

    if all_issues:
        hint = f"检查 active profile allowlist: {rules_path}"
        emit("block", f"STRUCTURE_VIOLATION: {'; '.join(all_issues)}. {hint}")
        return 0

    emit("pass", f"STRUCTURE_OK: {len(paths)} 个路径通过检查")
    return 0


if __name__ == "__main__":
    main()
