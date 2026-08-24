#!/usr/bin/env python3
"""Session-scoped product context helpers."""
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path

from ael_output import dump_json
from product_context import ProductContext, ProductContextError, resolve_product_context


def ael_root_from_script() -> Path:
    return Path(__file__).resolve().parents[2]


def add_selector_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--product-root", default="", help="Product repository root for this command/session.")
    parser.add_argument("--product-id", default="", help="Registered product id for this command/session.")
    parser.add_argument("--cwd", default="", help="Optional cwd used for product discovery.")


def resolve_context(args: argparse.Namespace) -> ProductContext:
    ael_root = Path(args.ael_root).expanduser().resolve() if args.ael_root else ael_root_from_script()
    cwd = Path(args.cwd).expanduser().resolve() if getattr(args, "cwd", "") else Path.cwd()
    return resolve_product_context(
        ael_root,
        product_root=getattr(args, "product_root", ""),
        product_id=getattr(args, "product_id", ""),
        cwd=cwd,
    )


def cmd_resolve(args: argparse.Namespace) -> int:
    try:
        context = resolve_context(args)
    except ProductContextError as exc:
        dump_json({"decision": "block", "reason": f"PRODUCT_CONTEXT_ERROR: {exc}"})
        return 2
    dump_json({"decision": "pass", "reason": "PRODUCT_CONTEXT_RESOLVED", "product_context": context.to_dict()})
    return 0


def cmd_env(args: argparse.Namespace) -> int:
    try:
        context = resolve_context(args)
    except ProductContextError as exc:
        print(f"PRODUCT_CONTEXT_ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"export AEL_PRODUCT_ROOT={shlex.quote(str(context.root))}")
    if context.product_id:
        print(f"export AEL_PRODUCT_ID={shlex.quote(context.product_id)}")
    else:
        print("unset AEL_PRODUCT_ID")
    return 0


def cmd_exec(args: argparse.Namespace) -> int:
    try:
        context = resolve_context(args)
    except ProductContextError as exc:
        print(f"PRODUCT_CONTEXT_ERROR: {exc}", file=sys.stderr)
        return 2
    command = list(args.command or [])
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        dump_json({"decision": "block", "reason": "PRODUCT_EXEC_COMMAND_REQUIRED"})
        return 2
    env = dict(os.environ)
    env["AEL_PRODUCT_ROOT"] = str(context.root)
    if context.product_id:
        env["AEL_PRODUCT_ID"] = context.product_id
    else:
        env.pop("AEL_PRODUCT_ID", None)
    proc = subprocess.run(command, env=env, check=False)
    return proc.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve or pin an AEL product context without mutating active-product.json")
    parser.add_argument("--ael-root", default="")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_resolve = sub.add_parser("resolve")
    add_selector_args(p_resolve)
    p_resolve.set_defaults(func=cmd_resolve)

    p_env = sub.add_parser("env")
    add_selector_args(p_env)
    p_env.set_defaults(func=cmd_env)

    p_exec = sub.add_parser("exec")
    add_selector_args(p_exec)
    p_exec.add_argument("command", nargs=argparse.REMAINDER)
    p_exec.set_defaults(func=cmd_exec)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
