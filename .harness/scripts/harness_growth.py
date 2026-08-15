#!/usr/bin/env python3
"""Generate Harness self-growth reports from task evidence."""
from __future__ import annotations

import argparse
import re
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from harness_growth_capture import capture_evidence
from harness_knowledge import ensure
from harness_growth_review import apply_review, resolve_report
from harness_output import dump_json
from workspace_paths import Phase0Layout, load_active_planning_gate, load_layout

SIGNAL = re.compile(
    r"(经验沉淀|沉淀候选|LESSONS|CONTEXT|已排除|失败|Critical|Major|风险|技术债|重复|越界|架构沉淀|安全|性能)",
    re.IGNORECASE,
)
PENDING_RE = re.compile(r"人工决定\*\*[：:]\s*待定|处理结果\*\*[：:]\s*待处理")
EVIDENCE_DIGEST_RE = re.compile(r"^- Evidence digest: `([0-9a-f]{64})`$", re.MULTILINE)
def emit(decision: str, reason: str, **extra) -> None:
    dump_json({"decision": decision, "reason": reason, **extra})


def _bound_to_work_item(path: Path, work_item_id: str) -> bool:
    if not work_item_id:
        return True
    if work_item_id in path.name:
        return True
    try:
        head = path.read_text(encoding="utf-8", errors="ignore")[:2000]
    except OSError:
        return False
    return bool(re.search(rf"Work Item(?: ID)?\*\*[：:]\s*`?{re.escape(work_item_id)}`?", head))


def evidence_files(layout: Phase0Layout, work_item_id: str = "") -> list[Path]:
    roots = (
        layout.summaries_dir,
        layout.progress_dir,
        layout.test_reports_dir,
        layout.review_reports_dir,
    )
    files: list[Path] = []
    for root in roots:
        if root.is_dir():
            files.extend(sorted(
                p for p in root.rglob("*.md")
                if p.is_file() and _bound_to_work_item(p, work_item_id)
            ))
    return files


def clean_line(line: str) -> str:
    return re.sub(r"\s+", " ", line.strip()).strip("| ")


def collect(layout: Phase0Layout, work_item_id: str = "") -> list[tuple[Path, str]]:
    candidates: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for path in evidence_files(layout, work_item_id):
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line in lines:
            cleaned = clean_line(line)
            if not cleaned or len(cleaned) < 8:
                continue
            if SIGNAL.search(cleaned):
                key = f"{path}:{cleaned[:180]}"
                if key not in seen:
                    seen.add(key)
                    candidates.append((path, cleaned[:260]))
    return candidates


def render(layout: Phase0Layout, candidates: list[tuple[Path, str]], work_item_id: str = "") -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        "# GROWTH — Harness 自我成长报告",
        "",
        f"- 生成时间：{ts}",
        f"- 产品：{layout.product_name}",
        f"- Profile：{layout.harness_profile}",
        f"- 候选数量：{len(candidates)}",
        *([f"- Work Item: `{work_item_id}`"] if work_item_id else []),
        f"- Evidence digest: `{evidence_digest(layout, candidates)}`",
        "",
        "## 1. 候选沉淀项",
        "",
        "> 人工 review 后，运行 `harness_growth.sh apply-review` 把长期有效项写入 `CONTEXT.md` / `LESSONS.md`；架构文档和技术债 Work Item 由负责人单独确认。",
        "",
    ]
    if not candidates:
        lines.extend(["暂无候选。", ""])
    for idx, (path, text) in enumerate(candidates, start=1):
        lines.extend(
            [
                f"### G-{idx:03d}",
                "",
                f"- **来源**：`{layout.rel(path)}`",
                f"- **原文摘要**：{text}",
                "- **建议分类**：lesson / context / architecture / tech-debt / ignore",
                "- **人工决定**：待定",
                "- **处理结果**：待处理",
                "",
            ]
        )

    lines.extend(
        [
            "## 2. Review 指引",
            "",
            "- 进入 `LESSONS.md`：跨任务会重复踩坑，且未来 6 个月有复现概率。",
            "- 进入 `CONTEXT.md`：会影响后续实现默认行为、既有抽象复用、禁动清单。",
            "- 进入架构文档：涉及模块边界、ADR、跨模块契约、容量边界。",
            "- 忽略：只对本次任务有效、证据不足、或已经被现有条目覆盖。",
            "- `人工决定` 建议填写：`context`、`lesson`、`architecture`、`tech-debt`、`ignore`，可组合如 `lesson / tech-debt`。",
            "",
        ]
    )
    return "\n".join(lines)


def _report_bound_to_work_item(report: Path, work_item_id: str) -> bool:
    if not work_item_id:
        return True
    if work_item_id in report.name:
        return True
    try:
        head = report.read_text(encoding="utf-8", errors="ignore")[:1200]
    except OSError:
        return False
    return f"Work Item: `{work_item_id}`" in head or f"Work Item：`{work_item_id}`" in head


def review_status(layout: Phase0Layout, work_item_id: str = "") -> dict[str, object]:
    reports = sorted(layout.growth_reports_dir.glob("*-GROWTH.md")) if layout.growth_reports_dir.is_dir() else []
    reports = [report for report in reports if _report_bound_to_work_item(report, work_item_id)]
    pending: list[str] = []
    reviewed: list[str] = []
    historical_pending: list[str] = []
    current = reports[-1] if reports else None
    for report in reports:
        text = report.read_text(encoding="utf-8", errors="ignore")
        pending_count = len(PENDING_RE.findall(text))
        if pending_count:
            target = pending if report == current else historical_pending
            target.append(f"{layout.rel(report)}:{pending_count}")
        else:
            reviewed.append(layout.rel(report))
    return {
        "reports": len(reports),
        "current_report": layout.rel(current) if current else "",
        "pending": pending,
        "historical_pending": historical_pending,
        "reviewed": reviewed,
        "work_item_id": work_item_id,
    }


def latest_report(layout: Phase0Layout, work_item_id: str = "") -> Path | None:
    reports = sorted(layout.growth_reports_dir.glob("*-GROWTH.md")) if layout.growth_reports_dir.is_dir() else []
    reports = [report for report in reports if _report_bound_to_work_item(report, work_item_id)]
    return reports[-1] if reports else None


def evidence_digest(layout: Phase0Layout, candidates: list[tuple[Path, str]]) -> str:
    digest = hashlib.sha256()
    for path, text in candidates:
        digest.update(layout.rel(path).encode("utf-8"))
        digest.update(b"\0")
        digest.update(text.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def report_evidence_digest(report: Path) -> str:
    try:
        text = report.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    match = EVIDENCE_DIGEST_RE.search(text)
    return match.group(1) if match else ""


def freshness_status(layout: Phase0Layout, work_item_id: str = "") -> dict[str, object]:
    candidates = collect(layout, work_item_id)
    evidence = evidence_files(layout, work_item_id)
    report = latest_report(layout, work_item_id)
    if not candidates:
        return {
            "ok": True,
            "reason": "GROWTH_FRESHNESS_OK_NO_CANDIDATES",
            "candidates": 0,
            "evidence_files": len(evidence),
            "latest_report": "",
            "work_item_id": work_item_id,
        }
    if report is None:
        return {
            "ok": False,
            "reason": "GROWTH_REPORT_MISSING",
            "candidates": len(candidates),
            "evidence_files": len(evidence),
            "latest_report": "",
            "work_item_id": work_item_id,
        }
    current_digest = evidence_digest(layout, candidates)
    bound_digest = report_evidence_digest(report)
    if not bound_digest:
        return {
            "ok": False,
            "reason": "GROWTH_REPORT_UNBOUND",
            "candidates": len(candidates),
            "evidence_files": len(evidence),
            "latest_report": layout.rel(report),
            "evidence_digest": current_digest,
            "report_evidence_digest": "",
            "work_item_id": work_item_id,
        }
    if bound_digest != current_digest:
        return {
            "ok": False,
            "reason": "GROWTH_REPORT_STALE",
            "candidates": len(candidates),
            "evidence_files": len(evidence),
            "latest_report": layout.rel(report),
            "evidence_digest": current_digest,
            "report_evidence_digest": bound_digest,
            "work_item_id": work_item_id,
        }
    status = review_status(layout, work_item_id)
    if status["pending"]:
        return {
            "ok": False,
            "reason": "GROWTH_REVIEW_PENDING",
            "candidates": len(candidates),
            "evidence_files": len(evidence),
            "latest_report": layout.rel(report),
            "evidence_digest": current_digest,
            "report_evidence_digest": bound_digest,
            **status,
        }
    return {
        "ok": True,
        "reason": "GROWTH_FRESHNESS_OK",
        "candidates": len(candidates),
        "evidence_files": len(evidence),
        "latest_report": layout.rel(report),
        "evidence_digest": current_digest,
        "report_evidence_digest": bound_digest,
        **status,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--harness-root", default=".")
    parser.add_argument("--product-root", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--report", default="")
    parser.add_argument("--allow-pending", action="store_true")
    parser.add_argument("--title", default="")
    parser.add_argument("--summary", default="")
    parser.add_argument("--category", default="lesson")
    parser.add_argument("--trigger", default="")
    parser.add_argument("--failed", default="")
    parser.add_argument("--cause", default="")
    parser.add_argument("--next-action", default="")
    parser.add_argument("--source", default="")
    parser.add_argument("--command", default="")
    parser.add_argument("--work-item", default="")
    parser.add_argument("cmd", choices=("scan", "status", "review-status", "freshness", "apply-review", "capture"))
    args = parser.parse_args()

    layout = load_layout(
        Path(args.harness_root).resolve(),
        Path(args.product_root).resolve() if args.product_root else None,
    )
    if args.cmd in {"scan", "apply-review", "capture"}:
        ensure(layout)

    gate = load_active_planning_gate(layout)
    work_item_id = args.work_item.strip() or str(((gate or {}).get("work_item") or {}).get("id") or "").strip()
    candidates = collect(layout, work_item_id)
    if args.cmd == "capture":
        summary = args.summary.strip()
        if not summary:
            emit("block", "GROWTH_CAPTURE_SUMMARY_REQUIRED")
            return 0
        out = capture_evidence(
            layout,
            title=args.title.strip(),
            summary=summary,
            category=args.category.strip(),
            trigger=args.trigger.strip(),
            failed=args.failed.strip(),
            cause=args.cause.strip(),
            next_action=args.next_action.strip(),
            source=args.source.strip(),
            command=args.command.strip(),
            work_item_id=work_item_id,
        )
        emit("pass", f"GROWTH_CAPTURE_READY: {layout.rel(out)}", evidence=layout.rel(out))
        return 0

    if args.cmd == "status":
        emit("pass", "GROWTH_STATUS", candidates=len(candidates), evidence_files=len(evidence_files(layout, work_item_id)), work_item_id=work_item_id)
        return 0

    if args.cmd == "review-status":
        status = review_status(layout, work_item_id)
        decision = "block" if status["pending"] else "pass"
        reason = "GROWTH_REVIEW_PENDING" if status["pending"] else "GROWTH_REVIEW_OK"
        emit(decision, reason, **status)
        return 0

    if args.cmd == "freshness":
        result = freshness_status(layout, work_item_id)
        emit("pass" if result.pop("ok") else "block", str(result.pop("reason")), **result)
        return 0

    if args.cmd == "apply-review":
        result = apply_review(layout, resolve_report(layout, args.report), args.allow_pending)
        emit("pass" if result.pop("ok") else "block", str(result.pop("reason")), **result)
        return 0

    out = Path(args.output).resolve() if args.output else (
        layout.growth_reports_dir / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}{'-' + work_item_id if work_item_id else ''}-GROWTH.md"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(layout, candidates, work_item_id), encoding="utf-8")
    emit("pass", f"GROWTH_REPORT_READY: {layout.rel(out)}", candidates=len(candidates), report=layout.rel(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
