# AEL 工作流

> **背景与历史全景**：[architecture/overview.md](./overview.md)
> **使用说明**：[getting-started/cli.md](../getting-started/cli.md) · **多人协作**：[execution/collaboration.md](../execution/collaboration.md)
> **Work Item 配置**：[.ael/work-items/README.md](../../.ael/work-items/README.md)

> **当前推荐路径**：[design/lean-plan-flow.md](../design/lean-plan-flow.md)。公开操作面是 `ael plan → start → status → finish`，其中 `status` 只观察；低风险 `lite` 可省略显式 `plan`。旧脚本链保留为兼容诊断，不作为每个 Story 的人工步骤。

## 建设目的

AEL 工程设计旨在建立 AI 时代「**已有项目接入（Brownfield Intake）— 产品设计（BMAD Method）— 协作（Work Item）— AEL（AI Coding）— 知识沉淀（Self-Growth）**」一体化研发新体系，通过事实建档、流程门禁、架构约束、独立验收与成长报告，让 AI 写码可控可追溯、产品与研发始终对齐，并为软件产品团队沉淀可复制的组织级交付方法。

**产品环路前提**：实现前必须由 **BMAD Method** 形成与 planning level 和实际风险匹配的 BMAD Planning 结果；L1 可走 Quick Flow，L2/L3 使用相应完整度。不可跳过规划语义，但不要求低风险任务运行完整 Analysis / Planning / Solutioning 仪式。

## 架构定位（请先读）

BuildRun Agent Engineering Lifecycle **不是** OpenAI 原文的简单复刻，而是通用产品研发闭环：双闭环 + 三体产品闭环 + 项目级知识沉淀。产品差异通过显式 profile 隔离。

| 闭环 | 方法论来源 | 回答的问题 |
|------|------------|------------|
| **Intake：已有项目接入** | BuildRun Agent Engineering Lifecycle Brownfield Intake | 先看清已有事实：代码、文档、测试、技术栈、模块边界 |
| **BMAD Planning：BMAD Method 前导小闭环** | [BMAD Method](https://github.com/bmad-code-org/BMAD-METHOD) Analysis/Planning/Solutioning + Planning Gate | 做对的事：需求、边界、分级、`ael-workspace/planning/tasks/` 建档 |
| **Work Item 协同层** | 产品级可替换 Provider（`noop` / `teambition` / `feishu` / `jira`） | 产品真相源：排期、状态、跨角色协同 |
| **Lifecycle Execution：OpenAI-inspired 主执行闭环** | OpenAI Harness Engineering + Superpowers TDD + GStack | 把事做对：DAG、受控命令 TDD、QA 分离、GC、MR |
| **知识沉淀层** | Flow-X 上下文工程 | 让 AI 不依赖聊天记忆：CONTEXT、LESSONS、PROGRESS、SUMMARY、TEST、REVIEW、GROWTH |

> OpenAI 文章**没有** BMAD Method 式前导流程，也**没有**产品协同层。BuildRun Agent Engineering Lifecycle 将 **BMAD Method 前三阶段**显式接入为 BMAD Planning，经产品侧 Work Item provider 与看板/任务系统对齐，再交接 Lifecycle Execution。详见 **[planning/bmad-planning.md §0](../planning/bmad-planning.md#0-与-bmad-method-的关系)**。

### 双闭环（Git 内）

```
人类意图
   │
   ▼
┌──────────────────────────────────────┐
│ Intake  已有项目接入                   │
│ 既有代码/关键入口/文档/测试/CI → INTAKE 报告 │
│ ael_intake.sh scan → 人工 review     │
└──────────────────┬───────────────────┘
                   │ reviewed facts
                   ▼
┌──────────────────────────────────────┐
│ BMAD Planning  BMAD Method 前导小闭环     │
│ 规格 → 架构对齐 → L1/L2/L3 → ael-workspace/planning/tasks/      │
│ planning_gate.sh → bmad_method_gate.py     │
└──────────────────┬───────────────────┘
                   │ pass
                   ▼
┌──────────────────────────────────────┐
│ Lifecycle Execution  OpenAI-inspired 主闭环 │
│ agent_start → DAG → TDD → QA → GC    │
└──────────────────────────────────────┘
```

### 三体产品闭环（BMAD → Work Item → AEL）

```
┌──────────────┐   draft/confirm/sync ┌──────────────┐   pull/agent_start   ┌──────────────┐
│ BMAD Method  │ ───────────────────► │ Work Item    │ ───────────────────► │ AEL Exec │
│ Planning     │ ◄─────────────────── │ TB/飞书/Jira │ ◄────────────────── │ AEL 写码  │
└──────────────┘   close/状态回写       └──────────────┘   verify/gate        └──────────────┘
```

| 系统 | 职责 | 真相源 |
|------|------|--------|
| **BMAD Planning** | Analysis/Planning/Solutioning；`bmad_method_gate.py` 留痕 | `ael-workspace/planning/product-specs`、`ael-workspace/planning/exec-plans`、`ael-workspace/planning/tasks/00–03` |
| **Work Item** | 排期、状态、跨角色协同 | 当前产品 provider 的任务 ID |
| **Lifecycle Execution** | DAG、TDD、QA、MR | 代码、`tasks-dag`、QA 凭证 |

全新项目的长期上下文入口是 BMAD Planning：产品目标、产品蓝图、架构/执行计划、验收和任务边界进入 `ael-workspace/planning/` 后，由 `ael_knowledge.sh sync-planning` 写入 `knowledge/CONTEXT.md` 的受管区块。

已有项目接入报告位于 `ael-workspace/evidence/intake-reports/`。它只提供候选证据，必须人工或 Agent review 后才可迁入 `knowledge/CONTEXT.md`、`knowledge/LESSONS.md`、`knowledge/REFERENCE_SYSTEMS.md`、产品架构文档或技术债 Work Item。完整规则见 [getting-started/brownfield-intake.md](../getting-started/brownfield-intake.md)。

AEL **不绑定**某个协同系统。长期选择写在产品侧 `ael-workspace/project.yaml`；全局默认和 adapter 配置见 `.ael/work-items/config.yaml`。同一套 buildrun-agent-engineering-lifecycle 可同时服务 Teambition、飞书任务、Jira 或本地 `noop` 产品。

BMAD → Work Item 使用 `bmad-work-item-v1` 契约，但新任务由 `ael plan` 批量生成共享 readiness digest、planning bundle 和 per-child binding receipt。默认 offline 只写确定性的本地任务绑定，不猜测外部容器；明确授权后才使用 configured provider，并在同一 receipt 记录 create/readback、Tasklist/project 和父级 binding。Work Item 只保存状态、负责人、讨论、摘要和 AEL Links，完整 BMAD 产物继续以产品仓库 `ael-workspace/planning/` 为真相源。旧 `draft-spec` / `sync-spec` 仅供兼容诊断。详见 [planning/work-item-contract.md](../planning/work-item-contract.md)。

---

## Work Item provider

AEL 通过产品级 provider 连接 `noop`、Teambition、飞书或 Jira。产品侧 `ael-workspace/project.yaml` 保存 provider 选择和非密钥参数；密钥与个人身份只进入 `.env`、环境变量或 Secret Manager。

当前 L1 可豁免外部 Work Item，L2/L3 必须绑定 provider ID。精简执行目标进一步要求所有正式迭代都有任务身份：`lite` 可使用 AEL 本地 ID，`standard` / `strict` 使用产品 Work Item。

provider 配置、ID 规则和命令只在以下文档维护：

- [getting-started/cli.md](../getting-started/cli.md#20-初始化与路径)：当前使用说明中的初始化与绑定。
- [planning/work-item-contract.md](../planning/work-item-contract.md)：规划与协同契约。
- [.ael/work-items/README.md](../../.ael/work-items/README.md)：adapter 配置参考。

---

## 方法论映射

| 来源 | 已吸收能力 | 精简目标中的位置 |
|------|------------|------------------|
| **BMAD Method** | Analysis / Planning / Solutioning、产品规格和实现就绪 | `start` 按 planning level / execution tier 选择规划深度，产物继续写入产品 `planning/` |
| **planning level** | `tasks/_templates`、L1/L2/L3、Entry Gate | 保留 planning level 与 legacy 兼容，由统一入口内部调用 |
| **Work Item provider** | 多 provider 负责人、状态与讨论 | adapter 保留；任务 ID 分离，生命周期由 `start/finish` 同步 |
| **OpenAI AEL** | AGENTS 地图、记录系统、机械反馈、QA 分离、GC | 三项主动作后的核心运行模型 |
| **Superpowers** | 规格先行、TDD、调试与完成前验证 | execution tier 相关内部 gate |
| **GStack** | 真实环境调查、浏览器 QA、审查视角 | 前端、交互或高风险任务按需触发 |
| **Flow-X** | CONTEXT / LESSONS / PROGRESS / SUMMARY / TEST / REVIEW / GROWTH | 保留知识与证据语义，产物按恢复、tier 或长期候选生成 |
| **Ralph / Agent Review** | Agent 审 Agent、失败修复重试 | `standard/strict` 按需编排并计入成本 |
| **Brownfield Intake** | 既有事实扫描和人工 review 后沉淀 | 首次接入或事实漂移时触发，不按任务重复 |

完整能力保留契约见 [精简强制执行设计 §1.1](../design/lean-enforcement.md#11-能力保留契约)。

---

## Intake：已有项目接入（BMAD Planning 前）

已有产品首次接入时，先运行：

```bash
bash .ael/scripts/ael_knowledge.sh ensure
bash .ael/scripts/ael_intake.sh status
bash .ael/scripts/ael_intake.sh scan
```

Review 顺序：

1. 先看已有文档清单，确认 README、architecture、services README 是否覆盖产品事实。
2. 再看模块与技术栈信号，确认运行/测试命令、服务边界和禁止随意移动目录是否应进入长期知识。
3. 深读关键代码入口，尤其是上游/重度依赖的 agent、factory、middleware、service、retriever、router，把核心机制摘要写入 `knowledge/REFERENCE_SYSTEMS.md`。
4. 最后看 LESSONS / 技术债候选，只确认长期有效项；完成后运行 `ael_intake.sh apply-review` 自动写入 `knowledge/CONTEXT.md`、`knowledge/LESSONS.md` 与 `knowledge/REFERENCE_SYSTEMS.md` 的受管区块。

Intake 完成后再进入 BMAD Planning。不要用未 review 的扫描报告直接驱动 Agent 实现。

---

## 当前兼容：BMAD Planning 摘要

> 机械校验：`planning_gate.sh` 内置 `bmad_method_gate.py`。模板：`.ael/templates/product-spec.md`。

| 步 | 动作 | 产出 / 命令 |
|----|------|-------------|
| P1 | 产品规格 | `ael-workspace/planning/product-specs/<功能>.md` |
| P1b | 批量协同绑定（L2/L3） | `ael plan` 生成每个 child 的 Work Item binding receipt；旧 `draft-spec/sync-spec` 仅兼容 |
| P2 | 架构/影响对齐 | `ael-workspace/planning/exec-plans/active/`、`02-影响分析.md`（L3） |
| P3 | 分级 + 建档 | `ael-workspace/planning/tasks/YYYY-MM-DD-<work-item-id>-简称/`，模板 `tasks/_templates/` |
| P4 | 前导门禁 + 知识沉淀 | `planning_gate.sh` → `planning_gate_pass.json` + `knowledge/CONTEXT.md` 的 `BMAD Planning 规划沉淀` 区块 |

**规则**：`.ael/rules/bmad-entry-gate.md`
**工序**：`.ael/workflows/README.md`（`L1-trivial.md` / `L2-standard.md` / `L3-cross-service.md`）

当前 L2/L3 的 `00-任务卡.md` 须填：**Gate 1 已确认**、**Work Item ID**、兼容 `任务编号` 镜像和产品规格链接。目标统一入口另维护稳定 AEL Task ID；`lite` 使用本地任务 ID 和最小 `task.json`，不补造完整任务包。

---

## 当前兼容：Lifecycle Execution（8 步能力图）

下表描述当前已实现流程。演进时必须保持相同合规结果，但按 execution tier 减少不必要步骤，并由统一 `finish` 门面编排和复用结果。不能把表中的八步永久解释为所有任务都必须人工逐条执行。

| 步 | 角色 | 动作 | 命令/产物 |
|----|------|------|-----------|
| 1 | 人类/Lead | 进入主闭环 | `ael plan` → `ael start <work-item-id>`（一次生成共享 readiness/batch receipt；task-scoped 激活；持久化 lean policy；不重复 verify/pull/close） |
| 2 | lead-agent | 拆 DAG | `tasks-dag.md`（对齐 `03-实施方案.md`） |
| 3 | lead-agent | 计划门禁 + 任务契约 | `feedback_planner.sh` + `task_contract_check.sh` |
| 4 | backend/frontend | 受控命令 TDD | `run_in_sandbox.sh` + `structure_guard` |
| 5 | 执行代理 | 反馈自愈 | JSON `block` → 修复 |
| 6 | qa-evaluator | 风险要求时生成 TEST/REVIEW 证据并签章 | `ael-workspace/evidence/test-reports/`、`review-reports/`、`qa_sign_off.sh` → `subagent-pr-gate.sh` |
| 7 | gc-sweeper | 风险要求时执行独立熵减 | `memory-sweep.sh` |
| 8 | lead-agent | 准备合并 | 有长期候选时执行 `ael_growth.sh` review/apply；随后 `check.sh` → `mr_ready.sh`；目标由合并/发布自动化关闭 Work Item |

当前 L3 **必须**独立 QA + GC，L2 按风险、L1 可简化（见 workflows）；目标状态由 `standard/strict` 启用独立 QA、GC 或生产证据，并由 `finish` 自动编排。

**任务工作区**：`task_workspace` 按 Work Item ID 隔离 `ael-workspace/runs/tasks/<id>/`；多人协作见 [execution/collaboration.md](../execution/collaboration.md)。

---

## 使用方式

公开使用模型、当前兼容命令和排障只在 [getting-started/cli.md](../getting-started/cli.md) 维护；多人并发与角色交接见 [execution/collaboration.md](../execution/collaboration.md)。本文只解释流程结构，避免形成第二份操作手册。

---

## 当前兼容机械门禁摘要

```
planning_gate → planning_gate_pass + CONTEXT sync → task_workspace activate
        ↓
agent_start（校验 Work Item ID，pull 当前 provider）
        ↓
feedback_planner → task_contract_check → structure_guard → plan_sync_check → dag_sync_check → run_in_sandbox
        ↓
qa_sign_off → subagent-pr-gate（task_workspace qa-path）→ [有长期候选时 Growth review/apply] → quality_commands → check.sh → MR
```

CI（MR）：`task_workspace infer-mr` 从 diff 推断 `ael-workspace/planning/tasks/<id>/`；一 MR 一任务。详见全景手册 §13。

---

## 与 OpenAI 原文对齐点（Lifecycle Execution 范围）

| 原则 | 本仓库落地 |
|------|------------|
| AGENTS.md ≈ 100 行地图 | `AGENTS.md` → `getting-started/cli.md` → `execution/collaboration.md` |
| docs/ 为记录系统 | `docs/`（AEL 内部）+ `planning/`（BMAD Planning 产出） |
| 执行计划一等公民 | `exec-plans` + `03-实施方案` |
| 反馈 JSON 自愈 | `.ael/scripts/emit_json.py` |
| 验收与实现分离 | `qa_sign_off` + `subagent-pr-gate` |
| 黄金原则 + GC | `gc-golden-principles` + `memory-sweep` |
| doc-gardening | `doc-gardening.sh` + CI |

**BuildRun Agent Engineering Lifecycle 额外补齐**（OpenAI 原文无）：BMAD Planning + `bmad_method_gate.py`、BMAD planning 自动沉淀到 `CONTEXT.md`、产品级 Work Item provider、结构守门、多人协作 `task_workspace`。

---

## Ralph 循环（Agent 审 Agent）

MR 前：`check.sh` → 可选 `gstack_investigate.sh` → `mr_ready.sh` → CI `agent-review`。

---

## 入口

| 读者 | 文档 |
|------|------|
| 双闭环总览 | 本文 |
| 全景设计·评估·演进 | [architecture/overview.md](./overview.md) |
| BMAD Planning | [planning/bmad-planning.md](../planning/bmad-planning.md) |
| 多人协作 | [execution/collaboration.md](../execution/collaboration.md) |
| 完整命令 | [getting-started/cli.md](../getting-started/cli.md) |
| Work Item provider | [.ael/work-items/README.md](../../.ael/work-items/README.md) |
| Agent 地图 | [AGENTS.md](../../AGENTS.md) |
| Lead | [CLAUDE.md](../../CLAUDE.md) |

**演示任务**：`ael-workspace/planning/tasks/2026-06-17-TB-demo-kb`（Teambition taskId `6a335eb127a6242c96a84cb5`；目录名为历史 demo，规范命名应为 `YYYY-MM-DD-<work-item-id>-简称`）
