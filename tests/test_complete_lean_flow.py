from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".harness" / "scripts"
import sys
sys.path.insert(0, str(SCRIPTS))

from harness_planning import plan_batch  # noqa: E402
from story_cycle_benchmark import benchmark  # noqa: E402
from workspace_paths import load_layout  # noqa: E402


class FakeProvider:
    name = "fake"
    project_id = "project-1"

    def __init__(self) -> None:
        self.created = 0

    def create(self, title: str, note: str = "", project_id: str | None = None):
        self.created += 1
        from work_item_providers import WorkItem
        return WorkItem(
            id=f"WI-created-{self.created}", title=title, note=note,
            status="created", provider=self.name,
            raw={"parent_work_item_id": "EPIC-1"},
        )

    def create_subtask(self, parent_work_item_id: str, title: str, note: str = ""):
        return self.create(title, note, self.project_id)

    def verify_binding(self, work_item_id: str, expected_project_id=None, expected_parent_id=None):
        return True, "FAKE_BINDING_OK"

    def verify_item_binding(self, item, expected_project_id=None, expected_parent_id=None):
        return True, "FAKE_READBACK_OK"


class CompleteLeanFlowTest(unittest.TestCase):
    def _product(self, root: Path, *, configured: bool = False):
        product = root / "product"
        workspace = product / "harness-workspace"
        task = workspace / "planning" / "tasks" / "story"
        task.mkdir(parents=True)
        (workspace / "project.yaml").write_text(
            "product:\n  id: demo\n  name: Demo\n  profile: generic\n"
            "workspace:\n  root: harness-workspace\n  planning: planning\n"
            "  runs: runs\n  knowledge: knowledge\n  evidence: evidence\n"
            "planning:\n  product_specs: product-specs\n  exec_plans_active: exec-plans/active\n"
            "  exec_plans_completed: exec-plans/completed\n  tasks: tasks\n"
            f"work_item:\n  provider: {'fake' if configured else 'noop'}\n",
            encoding="utf-8",
        )
        (workspace / "planning" / "product-specs").mkdir(parents=True)
        spec = workspace / "planning" / "product-specs" / "demo.md"
        front = "---\nspec_level: L3\n"
        if configured:
            front += "work_item_type: story\nwork_item_parent_id: EPIC-1\n"
        spec.write_text(front + "---\n\n# Demo\n", encoding="utf-8")
        (task / "00-任务卡.md").write_text(
            "任务标题：Demo Story\n产品规格链接：`product-specs/demo.md`\n",
            encoding="utf-8",
        )
        for name in ("01-需求与背景.md", "02-影响分析.md", "03-实施方案.md",
                     "04-实施记录.md", "05-QA验收.md", "06-交付结论.md"):
            (task / name).write_text(f"# {name}\n", encoding="utf-8")
        return product, task

    def test_offline_batch_is_stable_and_reuses_child(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product, task = self._product(Path(tmp))
            layout = load_layout(ROOT, product)
            first = plan_batch(layout, level="L3", task_dirs=[str(task)])
            second = plan_batch(layout, level="L3", task_dirs=[str(task)])
            self.assertEqual(first["decision"], "pass")
            self.assertTrue(second["cache_hit"])
            self.assertTrue(second["children"][0]["cache_hit"])
            binding = json.loads((task / "task.json").read_text())
            self.assertEqual(binding["work_item"]["provider"], "noop")

    def test_configured_provider_create_and_readback_are_bound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            product, task = self._product(Path(tmp), configured=True)
            provider = FakeProvider()
            with patch("harness_planning.get_provider", return_value=provider):
                result = plan_batch(
                    load_layout(ROOT, product), level="L3", task_dirs=[str(task)],
                    provider_mode="configured",
                )
            self.assertEqual(result["decision"], "pass")
            self.assertEqual(provider.created, 1)
            child = json.loads((product / "harness-workspace/runs/planning" /
                                result["batch_id"] / "children/WI-created-1.json").read_text())
            self.assertEqual(child["provider_receipt"]["action"], "create")
            self.assertEqual(child["provider_receipt"]["readback"]["decision"], "pass")

    def test_full_benchmark_keeps_quality_and_unknowns(self) -> None:
        fixture = json.loads((ROOT / "tests/fixtures/story-43-5-efficiency.json").read_text())
        result = benchmark(fixture)
        self.assertTrue(result["full_cycle_budget_pass"])
        self.assertEqual(result["offline_qa_units"], 5)
        self.assertEqual(result["quality_guards"]["provider_calls"], 0)
        self.assertEqual(result["production_p95"], "unknown")


if __name__ == "__main__":
    unittest.main()
