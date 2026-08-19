#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / ".harness" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from harness_growth import (  # noqa: E402
    apply_review,
    capture_evidence,
    collect,
    freshness_status,
    render,
    reviewed_items,
    review_status,
)
from harness_knowledge import ensure  # noqa: E402
from workspace_paths import load_layout  # noqa: E402


def write_project_config(product: Path) -> None:
    (product / "harness-workspace").mkdir(parents=True)
    (product / "harness-workspace" / "project.yaml").write_text(
        """
product:
  id: demo
  name: Demo
  profile: generic
workspace:
  root: harness-workspace
  planning: planning
  runs: runs
  knowledge: knowledge
  evidence: evidence
work_item:
  provider: noop
""".lstrip(),
        encoding="utf-8",
    )


class HarnessGrowthTest(unittest.TestCase):
    def test_growth_rejects_invalid_identity_before_creating_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp) / "product"
            product.mkdir()
            env = {**os.environ, "HARNESS_PRODUCT_ROOT": str(product)}
            completed = subprocess.run(
                [
                    "bash", str(SCRIPT_DIR / "harness_growth.sh"), "capture",
                    "--work-item", "../../escape", "--summary", "must stay contained",
                ],
                cwd=ROOT, env=env, text=True, capture_output=True, check=True,
            )

            self.assertEqual(json.loads(completed.stdout)["reason"], "TASK_ID_INVALID")
            self.assertFalse((product / "harness-workspace").exists())

    def test_capture_helper_rejects_invalid_identity_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            write_project_config(product)
            layout = load_layout(ROOT, product)
            with self.assertRaisesRegex(ValueError, "TASK_ID_INVALID"):
                capture_evidence(
                    layout, title="bounded", summary="must stay contained",
                    work_item_id="../../escape",
                )
            self.assertFalse(layout.progress_dir.exists())

    def test_incremental_scan_preserves_only_unchanged_reviewed_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            write_project_config(product)
            layout = load_layout(ROOT, product)
            ensure(layout)
            first = layout.progress_dir / "first.md"
            second = layout.progress_dir / "second.md"
            first.write_text("first", encoding="utf-8")
            second.write_text("second", encoding="utf-8")
            original = [(first, "失败方案：旧候选保持不变")]
            report = layout.growth_reports_dir / "2026-06-22-GROWTH.md"
            report.write_text(
                render(layout, original)
                .replace("- **人工决定**：待定", "- **人工决定**：lesson")
                .replace("- **处理结果**：待处理", "- **处理结果**：已验证并沉淀"),
                encoding="utf-8",
            )

            refreshed = render(
                layout,
                [*original, (second, "失败原因：新增候选仍需审核")],
                previous_reviews=reviewed_items(report),
            )

        self.assertIn("- **人工决定**：lesson", refreshed)
        self.assertIn("- **处理结果**：已验证并沉淀", refreshed)
        self.assertEqual(refreshed.count("- **人工决定**：待定"), 1)
        self.assertEqual(refreshed.count("- **处理结果**：待处理"), 1)

    def test_cross_day_scan_preserves_unchanged_reviewed_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            write_project_config(product)
            layout = load_layout(ROOT, product)
            ensure(layout)
            evidence = layout.summaries_dir / "WI-42-T1-SUMMARY.md"
            evidence.write_text("- 经验沉淀：跨日扫描必须保留已审核决定。\n", encoding="utf-8")
            old_report = layout.growth_reports_dir / "2026-06-21-WI-42-GROWTH.md"
            old_report.write_text(
                render(layout, collect(layout, work_item_id="WI-42"), work_item_id="WI-42")
                .replace("- **人工决定**：待定", "- **人工决定**：lesson")
                .replace("- **处理结果**：待处理", "- **处理结果**：已验证并沉淀"),
                encoding="utf-8",
            )
            new_report = layout.growth_reports_dir / "2026-06-22-WI-42-GROWTH.md"
            env = {**os.environ, "HARNESS_PRODUCT_ROOT": str(product)}
            completed = subprocess.run(
                [
                    "bash", str(SCRIPT_DIR / "harness_growth.sh"), "scan",
                    "--work-item", "WI-42", "--output", str(new_report),
                ],
                cwd=ROOT, env=env, text=True, capture_output=True, check=True,
            )
            refreshed = new_report.read_text(encoding="utf-8")

        self.assertEqual(json.loads(completed.stdout)["decision"], "pass")
        self.assertIn("- **人工决定**：lesson", refreshed)
        self.assertIn("- **处理结果**：已验证并沉淀", refreshed)
        self.assertNotIn("- **人工决定**：待定", refreshed)

    def test_read_only_status_does_not_create_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            env = {**os.environ, "HARNESS_PRODUCT_ROOT": str(product)}
            subprocess.run(
                ["bash", str(SCRIPT_DIR / "harness_growth.sh"), "status"],
                cwd=ROOT, env=env, check=True, stdout=subprocess.DEVNULL,
            )
            self.assertFalse((product / "harness-workspace").exists())

    def test_capture_evidence_writes_progress_candidate_scannable_by_growth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            write_project_config(product)
            layout = load_layout(ROOT, product)
            ensure(layout)

            capture = capture_evidence(
                layout,
                title="Feishu tasklist auth",
                summary="经验沉淀：飞书任务清单 1470403 不是普通 API scope 问题，必须把应用作为 app/editor 清单成员初始化。",
                category="lesson",
                trigger="配置 providers.feishu.tasklist_guid 后 diagnose 失败",
                failed="只开通飞书 API scope 或把机器人群组加入清单",
                cause="tenant_access_token 仍按应用身份做资源级鉴权",
                next_action="用清单 owner/editor 的 user_access_token 跑 feishu-tasklist-member",
                source="agent-observed",
                command="bash .harness/scripts/work_item.sh feishu-tasklist-member",
            )
            candidates = collect(layout)
            capture_name = capture.name
            capture_text = capture.read_text(encoding="utf-8")

        self.assertTrue(capture_name.endswith("-GROWTH-CAPTURE.md"))
        self.assertIn("FEISHU", capture_text.upper())
        self.assertGreaterEqual(len(candidates), 1)
        self.assertTrue(any("1470403" in text for _path, text in candidates))
        self.assertTrue(any("feishu-tasklist-member" in text for _path, text in candidates))

    def test_capture_evidence_redacts_common_tokens(self) -> None:
        fake_token = "u-syntheticTokenForRedactionOnly1234567890"
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            write_project_config(product)
            layout = load_layout(ROOT, product)
            ensure(layout)

            capture = capture_evidence(
                layout,
                title="Feishu bearer token",
                summary=f"经验沉淀：调用失败时不要把 Authorization: Bearer {fake_token} 写进证据。",
                source=f"tenant_access_token={fake_token}",
                command=f"curl -H 'Authorization: Bearer {fake_token}'",
            )
            text = capture.read_text(encoding="utf-8")

        self.assertNotIn(fake_token, text)
        self.assertIn("<REDACTED", text)

    def test_apply_review_writes_context_and_lessons_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            write_project_config(product)
            layout = load_layout(ROOT, product)
            ensure(layout)

            report = layout.growth_reports_dir / "2026-06-22-GROWTH.md"
            report.write_text(
                """
# GROWTH — Harness 自我成长报告

## 1. 候选沉淀项

### G-001

- **来源**：`harness-workspace/evidence/review-reports/demo-T1-REVIEW.md`
- **原文摘要**：后续任务必须默认复用 ProductClient，避免重复实现。
- **建议分类**：lesson / context / architecture / tech-debt / ignore
- **人工决定**：context
- **处理结果**：已沉淀到 CONTEXT 默认行为

### G-002

- **来源**：`harness-workspace/evidence/test-reports/demo-T1-TEST.md`
- **原文摘要**：缺少失败路径测试会造成返工。
- **建议分类**：lesson / context / architecture / tech-debt / ignore
- **人工决定**：lesson / tech-debt
- **处理结果**：下次先补负向测试

### G-003

- **来源**：`harness-workspace/evidence/progress/demo-T1-PROGRESS.md`
- **原文摘要**：一次性调试日志，无长期价值。
- **建议分类**：lesson / context / architecture / tech-debt / ignore
- **人工决定**：ignore
- **处理结果**：已忽略
""".lstrip(),
                encoding="utf-8",
            )

            status = review_status(layout)
            result = apply_review(layout, report, allow_pending=False)
            context = layout.context_file.read_text(encoding="utf-8")
            lessons = layout.lessons_file.read_text(encoding="utf-8")

        self.assertEqual(status["pending"], [])
        self.assertTrue(result["ok"])
        self.assertEqual(result["applied_items"], 2)
        self.assertIn("BEGIN harness-engineering:growth:context", context)
        self.assertIn("ProductClient", context)
        self.assertIn("BEGIN harness-engineering:growth:lessons", lessons)
        self.assertIn("负向测试", lessons)
        self.assertNotIn("一次性调试日志", context)
        self.assertNotIn("一次性调试日志", lessons)

    def test_apply_review_blocks_pending_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            write_project_config(product)
            layout = load_layout(ROOT, product)
            ensure(layout)

            report = layout.growth_reports_dir / "2026-06-22-GROWTH.md"
            report.write_text(
                """
# GROWTH — Harness 自我成长报告

### G-001

- **来源**：`harness-workspace/evidence/review-reports/demo-T1-REVIEW.md`
- **原文摘要**：需要沉淀候选。
- **建议分类**：lesson / context / architecture / tech-debt / ignore
- **人工决定**：待定
- **处理结果**：待处理
""".lstrip(),
                encoding="utf-8",
            )

            status = review_status(layout)
            result = apply_review(layout, report, allow_pending=False)

        self.assertEqual(status["pending"], ["harness-workspace/evidence/growth-reports/2026-06-22-GROWTH.md:2"])
        self.assertFalse(result["ok"])
        self.assertIn("GROWTH_REVIEW_PENDING", result["reason"])

    def test_review_status_preserves_historical_pending_without_blocking_current_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            write_project_config(product)
            layout = load_layout(ROOT, product)
            ensure(layout)
            old = layout.growth_reports_dir / "2026-06-21-GROWTH.md"
            old.write_text("- **人工决定**：待定\n- **处理结果**：待处理\n", encoding="utf-8")
            current = layout.growth_reports_dir / "2026-06-22-GROWTH.md"
            current.write_text("- **人工决定**：lesson\n- **处理结果**：已沉淀\n", encoding="utf-8")

            status = review_status(layout)

        self.assertEqual(status["pending"], [])
        self.assertEqual(status["historical_pending"], ["harness-workspace/evidence/growth-reports/2026-06-21-GROWTH.md:2"])
        self.assertEqual(status["current_report"], "harness-workspace/evidence/growth-reports/2026-06-22-GROWTH.md")

    def test_freshness_blocks_when_candidates_have_no_growth_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            write_project_config(product)
            layout = load_layout(ROOT, product)
            ensure(layout)
            report = layout.summaries_dir / "demo-T1-SUMMARY.md"
            report.write_text("- 经验沉淀：后续任务需要进入 CONTEXT。\n", encoding="utf-8")

            status = freshness_status(layout)

        self.assertFalse(status["ok"])
        self.assertEqual("GROWTH_REPORT_MISSING", status["reason"])
        self.assertEqual(1, status["candidates"])

    def test_freshness_blocks_when_growth_report_is_not_bound_to_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            write_project_config(product)
            layout = load_layout(ROOT, product)
            ensure(layout)
            evidence = layout.summaries_dir / "demo-T1-SUMMARY.md"
            evidence.write_text("- 经验沉淀：后续任务需要进入 CONTEXT。\n", encoding="utf-8")
            report = layout.growth_reports_dir / "2026-06-22-GROWTH.md"
            report.write_text("# GROWTH\n", encoding="utf-8")
            status = freshness_status(layout)

        self.assertFalse(status["ok"])
        self.assertEqual("GROWTH_REPORT_UNBOUND", status["reason"])

    def test_freshness_uses_evidence_digest_not_checkout_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            write_project_config(product)
            layout = load_layout(ROOT, product)
            ensure(layout)
            evidence = layout.summaries_dir / "demo-T1-SUMMARY.md"
            evidence.write_text("- 经验沉淀：后续任务需要进入 CONTEXT。\n", encoding="utf-8")
            candidates = collect(layout)
            report = layout.growth_reports_dir / "2026-06-22-GROWTH.md"
            report.write_text(
                render(layout, candidates)
                .replace("- **人工决定**：待定", "- **人工决定**：context")
                .replace("- **处理结果**：待处理", "- **处理结果**：已沉淀"),
                encoding="utf-8",
            )
            newer = report.stat().st_mtime + 10
            os.utime(evidence, (newer, newer))

            status = freshness_status(layout)

        self.assertTrue(status["ok"])
        self.assertEqual("GROWTH_FRESHNESS_OK", status["reason"])
        self.assertEqual(status["evidence_digest"], status["report_evidence_digest"])

    def test_freshness_blocks_when_bound_evidence_content_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            write_project_config(product)
            layout = load_layout(ROOT, product)
            ensure(layout)
            evidence = layout.summaries_dir / "demo-T1-SUMMARY.md"
            evidence.write_text("- 经验沉淀：第一版。\n", encoding="utf-8")
            report = layout.growth_reports_dir / "2026-06-22-GROWTH.md"
            report.write_text(render(layout, collect(layout)), encoding="utf-8")
            evidence.write_text("- 经验沉淀：第二版。\n", encoding="utf-8")
            older = report.stat().st_mtime - 10
            os.utime(evidence, (older, older))

            status = freshness_status(layout)

        self.assertFalse(status["ok"])
        self.assertEqual("GROWTH_REPORT_STALE", status["reason"])

    def test_freshness_passes_when_latest_growth_report_is_reviewed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            write_project_config(product)
            layout = load_layout(ROOT, product)
            ensure(layout)
            evidence = layout.summaries_dir / "demo-T1-SUMMARY.md"
            evidence.write_text("- 经验沉淀：后续任务需要进入 CONTEXT。\n", encoding="utf-8")
            report = layout.growth_reports_dir / "2026-06-22-GROWTH.md"
            report.write_text(
                render(layout, collect(layout))
                .replace("- **人工决定**：待定", "- **人工决定**：context")
                .replace("- **处理结果**：待处理", "- **处理结果**：已沉淀"),
                encoding="utf-8",
            )

            status = freshness_status(layout)

        self.assertTrue(status["ok"])
        self.assertEqual("GROWTH_FRESHNESS_OK", status["reason"])

    def test_work_item_growth_ignores_unrelated_historical_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp)
            write_project_config(product)
            layout = load_layout(ROOT, product)
            ensure(layout)
            unrelated = layout.summaries_dir / "WI-OLD-T1-SUMMARY.md"
            unrelated.write_text("- 经验沉淀：历史候选。\n", encoding="utf-8")
            current = layout.summaries_dir / "WI-42-T1-SUMMARY.md"
            current.write_text("- 经验沉淀：当前候选。\n", encoding="utf-8")
            candidates = collect(layout, work_item_id="WI-42")
            report = layout.growth_reports_dir / "2026-06-22-WI-42-GROWTH.md"
            report.write_text(
                render(layout, candidates, work_item_id="WI-42")
                .replace("- **人工决定**：待定", "- **人工决定**：lesson")
                .replace("- **处理结果**：待处理", "- **处理结果**：已沉淀"),
                encoding="utf-8",
            )

            status = freshness_status(layout, work_item_id="WI-42")

        self.assertEqual(len(candidates), 1)
        self.assertTrue(status["ok"])
        self.assertEqual(status["work_item_id"], "WI-42")


if __name__ == "__main__":
    unittest.main()
