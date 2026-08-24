#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ael_output import dump_json
from browser_scenarios import load_scenario, run_scenario
from workspace_paths import load_layout


def emit(decision: str, reason: str, **extra: Any) -> None:
    dump_json({"decision": decision, "reason": reason, **extra})


def default_output(runs_root: Path, url: str, action: str) -> Path:
    safe = url.replace("://", "_").replace("/", "_").replace(":", "_")
    suffix = "png" if action == "screenshot" else "json"
    return runs_root / "browser-qa" / f"{safe}.{suffix}"


def is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def resolve_output(product_root: Path, runs_root: Path, raw: str, url: str, action: str, allow_external: bool) -> tuple[Path | None, str]:
    if not raw:
        output = default_output(runs_root, url, action)
    else:
        output = Path(raw)
        if not output.is_absolute():
            output = product_root / output
        output = output.resolve()
    if not allow_external and not is_under(output, runs_root):
        return None, f"BROWSER_QA_OUTPUT_OUTSIDE_RUNS: 输出必须位于 {runs_root}"
    return output, ""


def load_click_script(product_root: Path, raw: str, allow_external: bool) -> tuple[str, str]:
    if not raw:
        return "", ""
    path = Path(raw)
    if not path.is_absolute():
        path = product_root / path
    path = path.resolve()
    if not allow_external and not is_under(path, product_root):
        return "", f"BROWSER_QA_SCRIPT_OUTSIDE_PRODUCT: 脚本必须位于产品根 {product_root}"
    return path.read_text(encoding="utf-8"), ""


def run_playwright(
    url: str,
    action: str,
    output: Path,
    selector: str = "",
    script_text: str = "",
    scenario_steps: list[dict[str, str]] | None = None,
    trace: bool = True,
) -> tuple[bool, str, dict[str, Any]]:
    if action == "click-test" and not selector and not script_text:
        return False, "BROWSER_QA_CLICK_TARGET_REQUIRED: click-test 必须提供 --selector 或 --script", {}

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False, "BROWSER_QA_UNAVAILABLE: Playwright 未安装，不能生成浏览器 QA 证据", {}

    output.parent.mkdir(parents=True, exist_ok=True)
    status = 0
    payload: dict[str, Any] = {"url": url}
    trace_path = output.with_suffix(".trace.zip")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        tracing_started = False
        if trace:
            context.tracing.start(screenshots=True, snapshots=True, sources=True)
            tracing_started = True
        page = context.new_page()
        console_errors: list[str] = []
        failed_requests: list[str] = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("requestfailed", lambda req: failed_requests.append(req.url))
        try:
            try:
                response = page.goto(url, wait_until="networkidle", timeout=30_000)
                status = response.status if response else 0
            except Exception as exc:
                return False, f"BROWSER_QA_NAVIGATION_FAILED: {exc}", {"url": url}
            if action == "click-test":
                if selector:
                    page.locator(selector).first.click(timeout=10_000)
                if script_text:
                    page.evaluate(script_text)
                try:
                    page.wait_for_load_state("networkidle", timeout=10_000)
                except Exception:
                    pass
            if action == "scenario":
                try:
                    payload["steps"] = run_scenario(page, scenario_steps or [])
                except Exception as exc:
                    return False, f"BROWSER_QA_SCENARIO_FAILED: {type(exc).__name__}: {exc}", payload
            if action in {"screenshot", "audit", "click-test", "scenario"}:
                screenshot = output if output.suffix == ".png" else output.with_suffix(".png")
                page.screenshot(path=str(screenshot), full_page=True)
            payload = {
                "url": url,
                "status": status,
                "console_errors": console_errors,
                "failed_requests": failed_requests,
                "screenshot": str((output if output.suffix == ".png" else output.with_suffix(".png")).resolve()),
            }
            if action == "click-test":
                payload["selector"] = selector
                payload["script"] = "provided" if script_text else ""
            report = output if output.suffix == ".json" else output.with_suffix(".json")
            if tracing_started:
                context.tracing.stop(path=str(trace_path))
                tracing_started = False
                payload["trace"] = str(trace_path.resolve())
            report.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        finally:
            if tracing_started:
                context.tracing.stop(path=str(trace_path))
                payload["trace"] = str(trace_path.resolve())
            context.close()
            browser.close()

    if status >= 400 or status == 0:
        return False, f"BROWSER_QA_HTTP_ERROR: status={status}", payload
    if console_errors or failed_requests:
        return False, "BROWSER_QA_ISSUES: console/network failures detected", payload
    return True, "BROWSER_QA_OK: 真实浏览器 QA 证据已生成", payload


def main():
    parser = argparse.ArgumentParser(description="GStack/Playwright 浏览器 QA 入口")
    parser.add_argument("url", help="需要测试的本地或远程 URL (例如 http://localhost:3000)")
    parser.add_argument("--action", choices=["screenshot", "click-test", "audit", "scenario"], default="audit", help="要执行的验证动作")
    parser.add_argument("--output", default="", help="报告或截图输出路径")
    parser.add_argument("--selector", default="", help="click-test 要点击的 CSS selector")
    parser.add_argument("--script", default="", help="click-test 可选 JS 脚本路径；相对产品根解析")
    parser.add_argument("--scenario", default="", help="scenario 动作的受限 JSON 步骤文件；相对产品根解析")
    parser.add_argument("--ael-root", default=".", help="buildrun-agent-engineering-lifecycle 根")
    parser.add_argument("--product-root", default="", help="产品根")
    parser.add_argument("--no-trace", action="store_true", help="不生成 Playwright trace")
    parser.add_argument("--allow-external-output", action="store_true", help="允许输出到产品 runs 之外")
    parser.add_argument("--allow-external-script", action="store_true", help="允许读取产品根之外的 click-test 脚本")
    args = parser.parse_args()

    product_root_arg = args.product_root
    layout = load_layout(
        Path(args.ael_root).resolve(),
        Path(product_root_arg).resolve() if product_root_arg else None,
    )
    output, output_error = resolve_output(
        layout.product_root,
        layout.runs_root,
        args.output,
        args.url,
        args.action,
        args.allow_external_output,
    )
    if output_error or output is None:
        emit("block", output_error, output=args.output)
        return 0
    try:
        script_text, script_error = load_click_script(layout.product_root, args.script, args.allow_external_script)
        if script_error:
            emit("block", script_error, script=args.script)
            return 0
    except OSError as exc:
        emit("block", f"BROWSER_QA_SCRIPT_NOT_FOUND: {exc}", script=args.script)
        return 0
    scenario_steps: list[dict[str, str]] = []
    if args.action == "scenario":
        scenario_steps, scenario_error = load_scenario(
            layout.product_root, args.scenario, args.allow_external_script,
        )
        if scenario_error:
            emit("block", scenario_error, scenario=args.scenario)
            return 0
    ok, reason, evidence = run_playwright(
        args.url,
        args.action,
        output,
        selector=args.selector,
        script_text=script_text,
        scenario_steps=scenario_steps,
        trace=not args.no_trace,
    )
    emit("pass" if ok else "block", reason, action=args.action, evidence=evidence)
    return 0

if __name__ == "__main__":
    sys.exit(main())
