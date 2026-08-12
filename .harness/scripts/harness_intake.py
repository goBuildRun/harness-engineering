#!/usr/bin/env python3
"""Brownfield product intake scan for the harness-engineering."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness_knowledge import ensure
from harness_output import dump_json
from harness_intake_review import apply_review, review_status, table_rows
from harness_intake_scan import collect
from workspace_paths import Phase0Layout, load_layout

def md_escape(text: str) -> str:
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("\n", " ")
    )


def bullet_list(items: list[str], empty: str = "暂无发现。") -> list[str]:
    if not items:
        return [empty]
    return [f"- `{item}`" for item in items]


def render_report(layout: Phase0Layout, data: dict[str, Any]) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        "# INTAKE — 已有项目接入扫描报告",
        "",
        f"- 生成时间：{ts}",
        f"- 产品：{layout.product_name}",
        f"- Product ID：`{layout.product_id}`",
        f"- Profile：`{layout.harness_profile}`",
        f"- 产品根：`{layout.product_root}`",
        f"- Workspace：`{layout.rel(layout.workspace_root)}`",
        f"- 扫描文件数：{data['file_count']}",
        f"- 文档候选数：{data['doc_count']}",
        f"- 代码/配置候选数：{data['code_count']}",
        "",
        "> 本报告是 brownfield 接入候选证据。它不会绕过人工判断直接修改长期知识；人工 review 后运行 `harness_intake.sh apply-review`，由 Harness 自动写入 `knowledge/CONTEXT.md` 与 `knowledge/LESSONS.md` 的受管区块。",
        "",
    ]
    if data["truncated"]:
        lines.extend(["> 扫描达到文件上限，报告只覆盖前一批排序后的产品文件。必要时用 `--max-files` 提高上限。", ""])

    lines.extend(
        [
            "## 0. Review 工作台（先处理）",
            "",
            "> 把本节作为 intake 完成度看板。每一项处理完后改成 `[x]`；`harness_intake.sh review-status` 会以这里未勾选项作为阻断信号。",
            "",
            "- [ ] 确认第 1 节 intake 归属策略是否符合产品真实边界。",
            "- [ ] 对 `upstream-reference` scope 只提炼核心逻辑、关键特性、可复用约束，不追踪其上游技术债。",
            "- [ ] 对 `product-owned` scope 提炼长期产品事实，并 review 技术债 / 测试 / 架构边界。",
            "- [ ] 确认是否有自研目录被错误归到 `upstream-reference` 或默认 scope。",
            "- [ ] Review 第 2 节现有文档，按归属策略挑选需要进入长期产品上下文的事实。",
            "- [ ] Review 第 3 节关键代码入口、模块和技术栈信号，确认自研服务边界、构建命令和运行入口。",
            "- [ ] Review 第 4 节测试与 CI 信号，确认自研范围可复用的 lint/test/verify 命令。",
            "- [ ] Review 第 5 节 CONTEXT 候选；确认后由 `apply-review` 写入 `harness-workspace/knowledge/CONTEXT.md`。",
            "- [ ] Review 第 5 节 LESSONS / 技术债候选；确认后由 `apply-review` 写入 `LESSONS.md`，需要排期的另建 Work Item。",
            "- [ ] Review 上游参考系统；把 DeerFlow、OpenViking 等重度依赖的核心逻辑、扩展点和禁改边界沉淀到 `REFERENCE_SYSTEMS.md`。",
            "- [ ] Review 第 5 节架构候选，更新产品架构文档或架构索引。",
            "- [ ] 记录被忽略候选的原因，尤其是被明确标记为上游参考的目录。",
            "",
            "### Review 结论记录",
            "",
            "| 类别 | 处理结果 | 证据位置 / Work Item |",
            "|------|----------|----------------------|",
            "| CONTEXT | 待处理 | - |",
            "| LESSONS | 待处理 | - |",
            "| 架构文档 | 待处理 | - |",
            "| 技术债 | 待处理 | - |",
            "| 忽略项 | 待处理 | - |",
            "",
            "填写说明：",
            "",
            "- `处理结果` 建议写：`已沉淀`、`已创建 Work Item`、`已忽略`、`不适用` 或 `待补充`。",
            "- `证据位置 / Work Item` 写实际落点，例如 `harness-workspace/knowledge/CONTEXT.md#产品边界`、`docs/architecture.md#系统边界`、飞书任务 GUID，或 `忽略：上游参考代码，不追踪技术债`。",
            "- 上游参考 scope（如 `upstream-reference`）只沉淀核心逻辑和可复用约束；不要因为它们的 TODO/FIXME 创建产品技术债。",
            "- 自研 scope（如 `product-owned`）才需要沉淀产品事实、测试命令、架构边界，并为真实技术债创建 Work Item。",
            "",
        ]
    )

    lines.extend(["## 1. 顶层结构与归属策略", ""])
    lines.extend(["### Intake 归属策略", ""])
    lines.extend(["| 路径 | 角色 | Review 策略 | 技术债策略 | 说明 |", "|------|------|-------------|------------|------|"])
    for scope in scope_rows(data["policy"]):
        path = scope.path if scope.path != "." else "(default)"
        lines.append(
            f"| `{md_escape(path)}` | {md_escape(scope.role)} | {md_escape(scope.review)} | {md_escape(scope.debt)} | {md_escape(scope.description)} |"
        )
    lines.append("")

    lines.extend(["### 目录", ""])
    lines.extend(bullet_list(data["top_level"]["dirs"]))
    lines.extend(["", "### 文件", ""])
    lines.extend(bullet_list(data["top_level"]["files"]))

    lines.extend(["", "## 2. 现有文档清单", ""])
    if not data["docs"]:
        lines.extend(["暂无文档候选。", ""])
    else:
        lines.extend(["| 路径 | 归属/处理 | 识别到的标题 |", "|------|-----------|--------------|"])
        for item in data["docs"][:60]:
            heads = " / ".join(item["headings"]) if item["headings"] else "未识别标题"
            lines.append(f"| `{md_escape(item['path'])}` | {md_escape(item['scope'])} | {md_escape(heads)} |")

    lines.extend(["", "## 3. 代码、模块与技术栈信号", ""])
    if data["modules"]:
        lines.extend(["### 模块目录", ""])
        for item in data["modules"]:
            children = ", ".join(f"`{child}`" for child in item["children"]) or "未发现直接子目录"
            lines.append(f"- `{item['root']}`：{children}")
        lines.append("")

    if data["entries"]:
        lines.extend(["### 关键代码入口候选", "", "| 路径 | 归属/处理 | 候选原因 |", "|------|-----------|----------|"])
        for item in data["entries"][:100]:
            lines.append(f"| `{md_escape(item['path'])}` | {md_escape(item['scope'])} | {md_escape(item['reason'])} |")
        lines.append("")
    else:
        lines.extend(["### 关键代码入口候选", "", "暂无关键代码入口候选。", ""])

    if data["stacks"]:
        lines.extend(["### 技术栈信号", "", "| 路径 | 归属/处理 | 信号 | 细节 |", "|------|-----------|------|------|"])
        for item in data["stacks"]:
            details: list[str] = []
            if item.get("frameworks"):
                details.append("frameworks=" + ",".join(item["frameworks"]))
            if item.get("scripts"):
                interesting = [s for s in item["scripts"] if any(key in s for key in ("test", "build", "lint", "dev", "start"))]
                if interesting:
                    details.append("scripts=" + ",".join(interesting[:12]))
            lines.append(f"| `{md_escape(item['path'])}` | {md_escape(item['scope'])} | {md_escape(item['signal'])} | {md_escape('; '.join(details) or '-')} |")
    else:
        lines.append("暂无技术栈信号。")

    if data["suffix_counts"]:
        lines.extend(["", "### 文件类型分布", ""])
        lines.append(", ".join(f"`{suffix}`={count}" for suffix, count in data["suffix_counts"][:15]))

    lines.extend(["", "## 4. 构建、测试与 CI 信号", ""])
    if not data["tests"]:
        lines.append("暂无发现。")
    else:
        for item in data["tests"]:
            lines.append(f"- `{item['path']}` — {item['scope']}")

    lines.extend(["", "## 5. 可沉淀候选", ""])
    lines.extend(
        [
            "### CONTEXT 候选",
            "",
            "- 从 `README.md`、`docs/`、`architecture/` 中提炼产品目标、核心用户、领域术语和非目标。",
            "- 从关键代码入口和技术栈信号中提炼运行方式、构建命令、测试命令和模块边界。",
            "- 从顶层结构与模块目录中提炼服务边界、共享库边界和禁止随意移动的目录。",
            "",
            "### 上游参考系统候选",
            "",
            "- 对 `upstream-reference` scope 深读 README、架构文档、关键代码入口、扩展点代码和测试入口。",
            "- 沉淀到 `harness-workspace/knowledge/REFERENCE_SYSTEMS.md`：核心能力、主要流程、可扩展点、禁改边界、升级风险。",
            "- 上游 TODO/FIXME 不创建产品技术债；只有产品自研补丁层和集成边界缺口才进入 Work Item。",
            "",
            "### LESSONS / 技术债候选",
            "",
            f"> 仅展示 `debt=track` scope 中的候选；`debt=ignore` scope 已忽略 {data['ignored_markers']} 条上游/参考目录候选。",
            "",
        ]
    )
    if not data["markers"]:
        lines.extend(["暂无 TODO/FIXME/HACK/skip/flaky 等候选。", ""])
    else:
        lines.extend(["| 来源 | 归属/处理 | 行 | 摘要 |", "|------|-----------|----|------|"])
        for item in data["markers"]:
            lines.append(f"| `{md_escape(item['path'])}` | {md_escape(item['scope'])} | {item['line']} | {md_escape(item['text'])} |")

    lines.extend(
        [
            "",
            "### 架构候选",
            "",
            "- 优先 review `architecture/`、`docs/`、`README.md` 中已存在的边界说明。",
            "- 若存在 ADR、设计决策或部署文档，迁入产品架构索引，避免只留在散落文件中。",
            "- 若代码目录已经体现多服务边界，补齐服务职责、接口契约、数据流与部署边界。",
            "",
            "## 6. 人工 Review 清单",
            "",
            "本报告的可勾选 review 项集中在 `## 0. Review 工作台（先处理）`。处理完成后，把对应 `- [ ]` 改成 `- [x]`，并在 Review 结论记录表中留下证据位置或 Work Item。",
            "",
            "## 7. 建议下一步",
            "",
            "1. 人工 review 本报告第 2～5 节，并完成第 0 节工作台。",
            "2. 运行 `harness_intake.sh apply-review`，由 Harness 自动更新 `knowledge/CONTEXT.md`、`knowledge/LESSONS.md` 与 `knowledge/REFERENCE_SYSTEMS.md` 的受管区块。",
            "3. 为缺失的架构索引或技术债创建产品侧任务。",
            "4. 再启动 BMAD Planning，为第一个接入后的改造任务建立规格和任务包。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--harness-root", default=".")
    parser.add_argument("--product-root", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--report", default="")
    parser.add_argument("--allow-pending", action="store_true")
    parser.add_argument("--max-files", type=int, default=6000)
    parser.add_argument("--max-markers", type=int, default=80)
    parser.add_argument("cmd", choices=("status", "scan", "review-status", "apply-review"))
    args = parser.parse_args()

    product_root = args.product_root
    layout = load_layout(
        Path(args.harness_root).resolve(),
        Path(product_root).resolve() if product_root else None,
    )
    ensure(layout)
    data = collect(layout, args.max_files, args.max_markers)

    if args.cmd == "status":
        emit(
            "pass",
            "INTAKE_STATUS",
            product=layout.product_name,
            files=data["file_count"],
            docs=data["doc_count"],
            code=data["code_count"],
            stack_signals=len(data["stacks"]),
            test_signals=len(data["tests"]),
            marker_candidates=len(data["markers"]),
            intake_reports_dir=layout.rel(layout.intake_reports_dir),
        )
        return 0

    if args.cmd == "review-status":
        status = review_status(layout)
        decision = "block" if status["pending"] else "pass"
        reason = "INTAKE_REVIEW_PENDING" if status["pending"] else "INTAKE_REVIEW_OK"
        emit(decision, reason, **status)
        return 0

    if args.cmd == "apply-review":
        report = Path(args.report).resolve() if args.report else None
        result = apply_review(layout, report, args.allow_pending)
        emit("pass" if result.pop("ok") else "block", result.pop("reason"), **result)
        return 0

    out = Path(args.output).resolve() if args.output else (
        layout.intake_reports_dir / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}-INTAKE.md"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_report(layout, data), encoding="utf-8")
    emit(
        "pass",
        f"INTAKE_REPORT_READY: {layout.rel(out)}",
        product=layout.product_name,
        report=layout.rel(out),
        files=data["file_count"],
        docs=data["doc_count"],
        code=data["code_count"],
        marker_candidates=len(data["markers"]),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
