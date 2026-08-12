# BMAD Planning 前导小闭环（Team Product R&D Harness）

> **定位**：Team Product R&D Harness 的**产品设计与任务前导闭环**，在 OpenAI 式「主执行闭环」之前运行。  
> **方法论溯源**：[BMAD Method](https://github.com/bmad-code-org/BMAD-METHOD)（BMM）的 **Analysis → Planning → Solutioning**；Team Product R&D Harness 将其落地为 **BMAD Planning**，产出写入 **产品侧 `harness-workspace/planning/`**，经 `planning_gate.sh` 交接到 Harness Execution。  
> **Harness 与产出分离**：`harness-engineering/` 仅含规程、脚本、模板；**不入库** PRD / exec-plan / 任务包到 Harness 目录内。
> **已有项目**：先执行 [Brownfield_Intake.md](./Brownfield_Intake.md)，把既有代码和文档转成可 review 的项目事实，再进入 BMAD Planning。
> **精简升级**：BMAD 的需求澄清、规划和 Solutioning 继续保留；目标 `harness start` 按 planning level 与 execution tier 选择 Quick Flow 或完整能力，不要求使用者手工编排 BMAD 与 Harness 命令链。

---

## 0. 与 BMAD Method 的关系

**设计立场**：实现前的 BMAD Planning **必须**使用 [BMAD Method](https://github.com/bmad-code-org/BMAD-METHOD) 与任务风险匹配的能力，否则产品环路无法闭合。L1 的 Quick Flow 已满足低风险规划语义；只有 L2/L3 或更高风险任务才要求相应完整度的 Analysis / Planning / Solutioning，不能把“使用 BMAD”解释为所有任务固定运行完整方法链。

对于已有产品，BMAD Planning 之前先做 Brownfield Intake。Intake 不是 BMAD 的替代品，它负责把历史代码、架构文档、测试和技术栈扫描成候选事实；BMAD Planning 负责基于这些经过 review 的事实创建新任务的规格、方案和交接凭证。

```
                    三体产品闭环（缺一环即断链）
┌─────────────────────────────────────────────────────────────────┐
│  ① BMAD Method     ② Work Item        ③ Harness Execution        │
│  Analysis/Planning/  provider/ID 对齐    按规格写码、QA、MR        │
│  Solutioning         排期与状态          Implementation           │
│  ↓ 产品真相          ↓ 协同真相          ↓ 工程真相                 │
└─────────────────────────────────────────────────────────────────┘
```

| 维度 | BMAD Method（BMM） | Team Product R&D Harness BMAD Planning（落地层） |
|------|-------------------|----------------------------------|
| **必经能力** | Analysis → Planning → Solutioning | 按 planning level 保留；L1 用 Quick Flow，L2/L3 使用相应完整度 |
| **Implementation** | Dev/Story/QA 循环 | **归入 Harness Execution** |
| **典型产出** | PRD、架构、就绪检查 | 映射到 **`harness-workspace/planning/product-specs`**、**`harness-workspace/planning/exec-plans`**、**`harness-workspace/planning/tasks`** |
| **交接** | 模块内 workflow 链 | **`planning_gate.sh`** → `harness-workspace/runs/planning_gate_pass.json` |

### 0.1 BMAD Planning 目录配置（产品 `harness-workspace/project.yaml`）

所有 BMAD Planning 产出路径**相对产品根**，由 harness-engineering `workspace_paths.py` 解析。产品侧真相源是 `harness-workspace/project.yaml`：

```yaml
workspace:
  root: harness-workspace
  planning: planning
  runs: runs
  knowledge: knowledge
  evidence: evidence

planning:
  product_specs: product-specs
  exec_plans_active: exec-plans/active
  exec_plans_completed: exec-plans/completed
  tasks: tasks

bmad:
  install_root: .                 # BMAD `_bmad/` 安装于产品根
  output_root: harness-workspace/bmad-output
  normalized_planning_root: harness-workspace/planning
```

`npx bmad-method install` 只接受产品根；harness-engineering 的 `harness_init.sh init --install-bmad` 会以非交互方式指定产品根、默认模块 `bmm,tea`、默认工具 `codex,cursor`，并在安装后写入 `_bmad/custom/config.toml`，把 BMAD 原生输出校准到 `harness-workspace/bmad-output/`。Harness 标准化四类原生输出：`planning-artifacts/`、`design-artifacts/`、`implementation-artifacts/`、`test-artifacts/`；其中 `design-artifacts/` 同时暴露给 `modules.bmm.design_artifacts` 与 `modules.cis.design_artifacts`，方便 BMAD 版本或团队流程启用设计模块。安装结束后若 `_bmad/` 未生成，init 必须返回 `block`；正式 PRD / 计划 / 任务包再由团队映射到 `harness-workspace/planning/`。

安装参数可用环境变量覆盖：`HARNESS_BMAD_MODULES`、`HARNESS_BMAD_TOOLS`、`HARNESS_BMAD_COMMUNICATION_LANGUAGE`、`HARNESS_BMAD_DOCUMENT_LANGUAGE`、`HARNESS_BMAD_USER_NAME`。

查看解析结果：

```bash
cd harness-engineering
python3 .harness/scripts/workspace_paths.py json
python3 .harness/scripts/workspace_paths.py ensure-dirs
```

说明见产品侧 `$PRODUCT_ROOT/harness-workspace/planning/README.md`。

**任务卡内链接约定**（相对 `planning/` 根，非 Harness 目录）：

- 产品规格：`product-specs/<功能>.md`
- 执行计划：`exec-plans/active/<功能>.md`

（门禁仍接受旧写法 `docs/product-specs/...` 并自动映射，新文档请用上表。）

### 0.2 BMAD Method 在产品根执行（必遵）

BMAD 工具链与 Harness **分离**：在 **产品根** 安装与调用 BMAD；产出**映射入库**到 `harness-workspace/planning/`（或产品 `project.yaml` 中配置的目录）。

**首次初始化**（推荐从 harness-engineering 执行，仅需一次）：

```bash
cd /path/to/harness-engineering
bash .harness/scripts/harness_init.sh init \
  --product-root /path/to/product \
  --product-id my-product \
  --product-name "My Product" \
  --profile generic \
  --install-bmad
```

如未带 `--install-bmad`，init 只创建产品 workspace 并提示 `BMAD_NOT_INSTALLED`；此时可以在产品根手动执行 `npx bmad-method install` 后，再回到 harness-engineering 重新执行 init，或用 `harness_product.sh env/exec` 固定产品上下文完成输出路径校准。

多产品并行时，不要在不同终端反复执行 `harness_init.sh use`。每个终端固定自己的产品上下文：

```bash
eval "$(bash .harness/scripts/harness_product.sh env --product-id my-product)"
```

**在 Cursor 中按级别选轨**：

| 级别 | BMAD 技能顺序（Chat 中触发） |
|------|------------------------------|
| **L2/L3** | Analysis（按需）→ `bmad-create-prd` → `bmad-validate-prd` → `bmad-create-architecture`（L3）→ `bmad-check-implementation-readiness` |
| **L1** | BMAD Quick Flow（`bmad-quick-dev` / `bmad-agent-quick-flow-solo-dev` 等） |

**映射落地 + 过门禁**（在 `harness-engineering/` 执行 Harness 脚本）：

```bash
cd /path/to/harness-engineering
PRODUCT_ROOT=/path/to/product

# 1. 新建规格（文件落在 harness-workspace/planning/product-specs/）
cp .harness/templates/product-spec.md "$PRODUCT_ROOT/harness-workspace/planning/product-specs/某功能.md"
# 编辑 front matter 与验收标准

# 2. L2/L3：exec-plan + 任务目录（落在 planning/）
cp .harness/templates/exec-plan.md "$PRODUCT_ROOT/harness-workspace/planning/exec-plans/active/某功能.md"
TASK_DIR="$PRODUCT_ROOT/harness-workspace/planning/tasks/$(date +%Y-%m-%d)-<work-item-id>-简称"
mkdir -p "$TASK_DIR"
cp tasks/_templates/00-任务卡.md tasks/_templates/03-实施方案.md tasks/_templates/04-实施记录.md "$TASK_DIR/"

# 3. Work Item 草稿确认与同步（可选，L2/L3 生产须真实 Work Item ID）
bash .harness/scripts/work_item.sh draft-spec "$PRODUCT_ROOT/harness-workspace/planning/product-specs/某功能.md" --assignee <provider-user-id>
bash .harness/scripts/work_item.sh sync-spec "$PRODUCT_ROOT/harness-workspace/planning/product-specs/某功能.md" --assignee <provider-user-id>

# 4. Planning Gate
bash .harness/scripts/planning_gate.sh L2 "$TASK_DIR"
```

`draft-spec` 使用同一契约生成待确认任务草稿，不写外部系统；`sync-spec` 使用 `bmad-work-item-v1`，只把目标摘要、验收项、负责人、Gate 状态和 Harness Links 同步到 Teambition/飞书/Jira；完整 BMAD 产物仍以产品仓库 `harness-workspace/planning/` 为真相源。详见 [BMAD_Work_Item_Contract.md](./BMAD_Work_Item_Contract.md)。

Planning Gate 通过后会自动运行 `harness_knowledge.sh sync-planning`，把 BMAD Planning 形成的产品规格、产品蓝图、架构/执行计划和任务边界写入产品侧 `harness-workspace/knowledge/CONTEXT.md` 的受管区块。全新项目的首批长期上下文应来自这里，而不是依赖聊天记忆。

**机械校验**（单独诊断）：

```bash
python3 .harness/scripts/bmad_method_gate.py \
  --level L2 \
  --harness-root . \
  --task-dir "$PRODUCT_ROOT/harness-workspace/planning/tasks/YYYY-MM-DD-<id>-简称"
```

模板：`.harness/templates/product-spec.md` · 规则：`.harness/rules/bmad-entry-gate.md`

> **准入凭证**：Harness Execution 优先读取 `harness-workspace/runs/planning_gate_pass.json`（及 `harness-workspace/planning/tasks/.../planning_gate_pass.json` 副本）；`phase0_pass.json` 仅作为历史兼容副本。

---

## 1. 前导闭环 vs 主闭环

```
┌─────────────────────────────────────────────────────────────┐
│  BMAD Planning：BMAD Method → planning/ 入库 → Planning Gate │
└──────────────────────────┬──────────────────────────────────┘
                           │ planning_gate.sh pass
┌──────────────────────────▼──────────────────────────────────┐
│  Harness Execution：agent_start → DAG → TDD → QA → MR        │
└─────────────────────────────────────────────────────────────┘
```

| 维度 | BMAD Planning | Harness Execution |
|------|---------|---------|
| 主要产出 | `harness-workspace/planning/product-specs/`、`harness-workspace/planning/exec-plans/`、`harness-workspace/planning/tasks/` | 业务代码、`tasks-dag.md`、QA 凭证 |
| 运行环境 | BMAD @ 产品根 + Harness 门禁脚本 | `harness-engineering/.harness/scripts/` |

---

## 2. 四步规程（P1–P4）

### P1：产品规格（Planning）

- 产出：`harness-workspace/planning/product-specs/<功能>.md`
- front matter 须含 `bmad_method: true`、`bmad_skills`、`bmad_completed_at`

### P2：执行计划（Solutioning）

- 产出：`harness-workspace/planning/exec-plans/active/<slug>.md`
- `linked_spec` 指向 `product-specs/<功能>.md`

### P3：任务目录（L2/L3）

- 产出：`harness-workspace/planning/tasks/YYYY-MM-DD-<work-item-id>-<简称>/`
- 模板来源：`harness-engineering/tasks/_templates/`
- `00-任务卡.md` 中 **Gate 1 → 已确认**

### P4：Planning Gate

```bash
cd /path/to/harness-engineering
bash .harness/scripts/planning_gate.sh L2 "$PRODUCT_ROOT/harness-workspace/planning/tasks/YYYY-MM-DD-<id>-简称"
bash .harness/scripts/agent_start.sh <work-item-id>
```

---

## 3. 检查清单

以下是当前实现期检查清单；统一入口落地后由 `start/finish` 自动完成或提示缺口，不继续暴露为人工总清单。

- [ ] 产品根已通过 `harness_init.sh init --install-bmad` 或手动 `npx bmad-method install` 生成 `_bmad/`
- [ ] `harness-workspace/planning/product-specs/` 含可测试验收标准
- [ ] L2/L3 已建 `harness-workspace/planning/tasks/<date>-<id>-<name>/`
- [ ] `planning_gate.sh` 返回 `pass`
- [ ] `harness-workspace/knowledge/CONTEXT.md` 已出现 `BMAD Planning 规划沉淀` 区块
- [ ] 然后才 `agent_start.sh`

---

## 4. 相关文档

- `$PRODUCT_ROOT/harness-workspace/planning/README.md` — 产出目录与配置项
- [USAGE.md](./USAGE.md) — 完整命令
- [Harness_Workflow.md](./Harness_Workflow.md) — 双闭环总览
- [Harness_Product_Workspace.md](./Harness_Product_Workspace.md) — harness-engineering 产品台账与产品 `project.yaml` 分工
- [.harness/rules/bmad-entry-gate.md](../.harness/rules/bmad-entry-gate.md)
