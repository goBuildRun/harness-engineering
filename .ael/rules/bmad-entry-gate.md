# BMAD Planning Gate

> BMAD Planning 产出目录由产品侧 **`ael-workspace/project.yaml`** 配置（默认 `ael-workspace/planning/`），**不在** `buildrun-agent-engineering-lifecycle/` 内。解析：`.ael/scripts/workspace_paths.py`。

## 触发时机

- 新需求、缺陷或跨模块改造进入当前产品
- 尚未执行 `planning_gate.sh` 或上次为 `block`

## 1. 产品规格检查（BMAD Method 产出 · 机械校验）

| 级别 | 机械校验项 |
|------|------------|
| **L1** | `planning/product-specs/` 或 `runs/context.md` 含 front matter |
| **L2/L3** | product-spec + exec-plan + 00-任务卡工作流表；Planning 须含 `bmad-create-prd` 或 `bmad-correct-course`，并含 `bmad-validate-prd` |

模板：`.ael/templates/product-spec.md`、`.ael/templates/exec-plan.md`

必须存在以下之一（路径相对 **`ael-workspace/planning/` 根**，见产品 `ael-workspace/project.yaml`）：

- `product-specs/<功能>.md`
- L1：`runs/context.md`

## 2. Planning level（规划分级）

| 级别 | 判定 |
|------|------|
| **L1** | 单文件、无联动、无契约变化 |
| **L2** | 单服务多文件、核心逻辑、需留方案 |
| **L3** | 跨服务、对外 API、共享模型/错误码、DB |

## 3. 任务目录（L2/L3 硬约束）

路径：`tasks/YYYY-MM-DD-<work-item-id>-<简称>/`（完整路径为 `ael-workspace/planning/tasks/...`）

| 级别 | 必备文件 | Work Item |
|------|----------|-----------|
| L1 | product-spec 或 context | **豁免** |
| L2 | `00`、`03`、`04` | **必填**，按当前产品 provider ID 规则校验 |
| L3 | `00`～`06` | **必填**，按当前产品 provider ID 规则校验 |

任务卡模板：`buildrun-agent-engineering-lifecycle/tasks/_templates/`（仅模板留在 AEL）。

## 4. Gate 1：范围确认

在 `00-任务卡.md` 填写：

- Planning level L1/L2/L3
- 产品规格链接：`product-specs/<功能>.md`
- 执行计划链接：`exec-plans/active/<功能>.md`（L2/L3）
- **BMAD Method 工作流记录**表（L2/L3）

## 5. 机械门禁

```bash
cd buildrun-agent-engineering-lifecycle
PRODUCT_ROOT=/path/to/product
TASK_DIR="$PRODUCT_ROOT/ael-workspace/planning/tasks/YYYY-MM-DD-<id>-简称"
python3 .ael/scripts/bmad_method_gate.py --level L2 --ael-root . \
  --task-dir "$TASK_DIR"
bash .ael/scripts/planning_gate.sh L2 "$TASK_DIR"
```

## 6. 输出模板（Lead / planning-agent 汇报）

```text
- 任务目标：...
- 产品规格：planning/product-specs/xxx.md
- Planning level：L1 / L2 / L3
- task_dir：planning/tasks/... 或 不适用(L1)
- Gate 1：已确认
- workspace 配置：产品 `ael-workspace/project.yaml` → `workspace.planning`
- 下一步：planning_gate.sh → agent_start.sh <ID>
```

## 硬约束

- L2/L3 无 `planning/tasks/` 目录 → 阻塞
- 无 `bmad_method_gate.py` pass 不得写入 `runs/planning_gate_pass.json`
- 不要把 BMAD 讨论留在聊天；须入库 `planning/` 且 front matter 可审计
