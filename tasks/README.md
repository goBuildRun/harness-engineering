# tasks/_templates/ — BMAD Planning 任务卡模板（Harness 内）

L1/L2/L3 planning level 使用目录化任务模板，统一入口按风险选择所需深度。

**BMAD Planning 任务产出**写入产品侧 **`harness-workspace/planning/tasks/`**（配置见产品 `harness-workspace/project.yaml`）。本目录**仅保留模板**，不存放任务实例。

## 命名（harness-workspace/planning/tasks/ 下）

```text
harness-workspace/planning/tasks/YYYY-MM-DD-<work-item-id>-<简称>/
```

Teambition 示例：`harness-workspace/planning/tasks/2026-06-17-6a335eb127a6242c96a84cb5-kb-health/`

> 历史 demo 目录 `harness-workspace/planning/tasks/2026-06-17-TB-demo-kb` 未含完整 Work Item ID，仅为联调示例；新建任务请遵守上式。

## 与双闭环关系

| 阶段 | 写入位置 |
|------|----------|
| BMAD Planning | `harness-workspace/planning/tasks/<id>/` 内 `00`～`02`；规格在 `harness-workspace/planning/product-specs/`；计划在 `harness-workspace/planning/exec-plans/`；`planning_gate_pass.json` |
| Lifecycle Execution | 同任务目录内 `03`～`06`、`tasks-dag.md`、业务代码 |

QA 凭证在 `harness-workspace/runs/tasks/<work-item-id>/`（本地 gitignore），不在 `harness-workspace/planning/tasks/` 目录。

## 模板

自 `harness-engineering/` 复制到 BMAD Planning 任务目录：

```bash
PRODUCT_ROOT=/path/to/product
TASK_DIR="$PRODUCT_ROOT/harness-workspace/planning/tasks/$(date +%Y-%m-%d)-<work-item-id>-简称"
mkdir -p "$TASK_DIR"
cp tasks/_templates/00-任务卡.md tasks/_templates/03-实施方案.md tasks/_templates/04-实施记录.md "$TASK_DIR/"
```

详见 [docs/planning/bmad-planning.md](../docs/planning/bmad-planning.md) 与 [docs/getting-started/cli.md](../docs/getting-started/cli.md) §2.3。
