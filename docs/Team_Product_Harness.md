# Team Product R&D Harness

> 定位：一套可迁移到任意软件产品研发团队的 AI 协作 Harness。具体产品约束通过 profile 承载；核心能力应服务所有需要产品、研发、测试、交付协作的团队。
>
> 已有项目接入专章：[Brownfield_Intake.md](./Brownfield_Intake.md)。
> 当前命令以 [USAGE.md](./USAGE.md) 为准；精简执行目标以 [design-docs/lean-enforcement.md](./design-docs/lean-enforcement.md) 为准。本文只维护通用模型，不维护第二套操作真相。
> BMAD、OpenAI Harness、Flow-X、Superpowers、GStack、planning level 和 Ralph 的有效能力继续保留；保留对象是能力与语义，不是旧步骤数量。完整矩阵见 [精简执行设计 §1.1](./design-docs/lean-enforcement.md#11-能力保留契约)。

## 1. 一句话定义

Team Product R&D Harness = 已有项目接入 + 产品前导 + 协同真相源 + AI 执行闭环 + 机械质量门禁 + 项目级知识沉淀 + 可审阅自我成长。

它不是单个 prompt，也不是某个业务项目的脚手架，而是一套把「想清楚、排清楚、做清楚、验清楚、沉淀清楚」连成闭环的研发操作系统。

## 2. 架构分层

| 层 | 通用职责 | 通用实现 |
|----|----------|----------------|
| 产品前导 | 需求、范围、验收、方案就绪 | BMAD Method → `planning/` |
| 已有项目接入 | 既有代码、文档、测试、技术栈事实扫描 | `harness_intake.sh` → `evidence/intake-reports/` |
| 协同真相源 | Work Item、负责人、状态、排期 | 产品级 provider：Teambition / 飞书 / Jira / noop |
| 执行闭环 | DAG、TDD、QA、GC、MR；按 execution tier 编排 | `.harness/scripts/`，目标由 `plan/start/finish` 封装，`status` 只观察 |
| 结构守门 | 代码落点、边界、diff 同步 | profile `package-allowlist.yaml` + `structure_guard` |
| 质量证据 | 测试、审查、签章、合并前总检 | `qa_sign_off` + `subagent-pr-gate` + `check.sh` |
| 知识沉淀 | 上下文、失败教训、任务摘要、成长报告 | `harness-workspace/knowledge/` + `harness-workspace/evidence/` |

## 3. 与 Flow-X 的公正对比

| 维度 | Flow-X 优势 | Harness 原优势 | 升级后的吸收结果 |
|------|-------------|-----------------------|------------------|
| 上下文工程 | `CONTEXT` / `LESSONS` / `PROGRESS` / `SUMMARY` 清晰 | 机械门禁强，但记忆沉淀较弱 | 新增 `harness-workspace/knowledge/` 全套知识目录 |
| 任务边界 | 任务契约清楚，读/写/验收边界显式 | `03-实施方案` 有路径表，但可读性不足 | 引入 7 字段任务契约与 `task_contract_check.sh` |
| 阶段产物 | staged artifacts 完整 | BMAD Planning/Harness Execution 分明 | 保留双闭环，同时把证据目录标准化 |
| 测试与审查 | 多层测试与审查理念强 | QA 签章与 PR gate 更机械 | 保留 TEST/REVIEW 语义与机械签章，按 execution tier 选择深度，不保留固定轮数 |
| 自成长 | `flow-evolve` 思路明确 | 以前主要靠人更新文档 | 新增 `harness_growth.sh scan` 生成候选成长报告 |
| 组织协同 | 更偏个人/小队工作流 | Work Item、多人协作、CI 门禁更强 | 升级为任意产品研发团队可用的协作 Harness |

结论：Flow-X 更擅长上下文与方法论组织，Team Product R&D Harness 更擅长机械约束、协同系统和交付门禁。升级方向不是二选一，而是用 Harness 承载组织级执行，用 Flow-X 的知识工程补足长期成长能力。

## 4. 从 Flow-X 吸收的机制

| Flow-X 机制 | Harness 落点 |
|-------------|--------------|
| `CONTEXT.md` 项目共享上下文 | `harness-workspace/knowledge/CONTEXT.md` |
| `LESSONS.md` 失败教训库 | `harness-workspace/knowledge/LESSONS.md` |
| `PROGRESS.md` 中断恢复 | `harness-workspace/evidence/progress/` |
| `SUMMARY.md` 任务摘要 | `harness-workspace/evidence/summaries/` |
| 任务 7 字段契约 | `03-实施方案.md` + `task_contract_check.sh` |
| DAG 与任务同步 | `dag_sync_check.sh` |
| 分层测试证据 | `harness-workspace/evidence/test-reports/`，按 execution tier 生成 |
| 分层审查证据 | `harness-workspace/evidence/review-reports/`，按 execution tier 生成 |
| 可审阅自我成长 | `harness-workspace/evidence/growth-reports/` + `harness_growth.sh scan/review-status/apply-review` |
| 产品质量命令 | 产品侧 `quality.commands.lint/test` + `quality_commands.sh` |
| 已有项目接入报告 | `harness-workspace/evidence/intake-reports/` + `harness_intake.sh` |

## 5. 自我成长闭环

Harness 的成长不能靠聊天记忆，也不能让 AI 自动修改全局规则。只有证据中出现跨任务失败模式、架构边界、新默认行为或明确技术债时，才生成可审阅候选，再由人把长期有效的内容提升为团队规则。

以下命令只在存在上述候选时执行，不属于每个任务的固定收尾：

```bash
bash .harness/scripts/harness_knowledge.sh ensure
bash .harness/scripts/harness_growth.sh status
bash .harness/scripts/harness_growth.sh scan
bash .harness/scripts/harness_growth.sh review-status
bash .harness/scripts/harness_growth.sh apply-review
```

`scan` 会读取：

- `harness-workspace/evidence/summaries/`
- `harness-workspace/evidence/progress/`
- `harness-workspace/evidence/test-reports/`
- `harness-workspace/evidence/review-reports/`

并生成：

- `harness-workspace/evidence/growth-reports/<date>-GROWTH.md`

人工 review 后，`harness_growth.sh apply-review` 会把 `context/architecture` 类确认项写入 `CONTEXT.md` 受管区块，把 `lesson/tech-debt` 类确认项写入 `LESSONS.md` 受管区块；架构文档和技术债 Work Item 仍由负责人按 review 结论单独补齐。

| 迁入位置 | 适合内容 |
|----------|----------|
| `CONTEXT.md` | 后续实现默认行为、产品目标、产品蓝图、架构/方案、既有抽象、禁动清单、团队约定；全新项目由 `harness_knowledge.sh sync-planning` 写入 BMAD 规划区块，已有项目由 `harness_intake.sh apply-review` 写入接入区块，成长项由 `harness_growth.sh apply-review` 写入 growth 区块 |
| `LESSONS.md` | 跨任务会复现的失败、误判、返工原因；由 `harness_intake.sh apply-review` 和 `harness_growth.sh apply-review` 写入受管区块 |
| 架构文档 | 模块边界、跨模块契约、容量边界、ADR |
| 技术债追踪 | 需要排期偿还的机制缺口 |

已有项目接入是另一条入口：`harness_intake.sh scan` 扫描产品既有代码、关键入口、文档、测试与技术栈，生成 `harness-workspace/evidence/intake-reports/<date>-INTAKE.md`；人工或 Agent review 后运行 `harness_intake.sh apply-review`，由 Harness 把受管证据区块写入产品知识系统。详见 [Brownfield_Intake.md](./Brownfield_Intake.md)。

全新项目的入口不同：先通过 BMAD Planning 形成 `planning/product-specs/`、`planning/exec-plans/`、`planning/tasks/`，再由 `harness_knowledge.sh sync-planning` 把产品目标、产品蓝图、架构/方案和任务边界同步到 `CONTEXT.md`。`planning_gate.sh` 通过时会自动执行这一步。

Work Item 系统承载协同真相源，不承载 BMAD 产物本体。推荐使用 `harness plan --level ... --task-dir ...` 一次生成 BMAD/Architecture/Readiness digest、batch planning receipt、子任务 create/readback/binding receipt 和共享上下文 digest；默认 offline，不猜测外部容器，明确授权后才调用 configured provider。`start` 将 lean policy 持久化，`finish` 对任务包中的 QA 单元 bounded fan-out 并稳定聚合。旧 `draft-spec` / `sync-spec` 仍可用于历史任务诊断。完整规格和计划仍以产品仓库 `harness-workspace/planning/` 为准。

## 6. 通用接入步骤

1. 把 harness-engineering `harness-engineering/` 放在团队可访问的位置，不复制到产品仓库。
2. 用启动脚本登记产品，并在产品根创建 `harness-workspace/`：

```bash
cd /path/to/harness-engineering
bash .harness/scripts/harness_init.sh init \
  --product-root /path/to/product \
  --product-id product-id \
  --product-name "Product Name" \
  --profile generic
```

3. 在 harness-engineering 中为该产品选择或新增 profile：
   - `.harness/profiles/<profile>/package-allowlist.yaml`
   - `docs/references/<profile>/`
   - 必要的 Agent 领域提示
4. 校验绑定与目录。单产品场景可设置默认 fallback；多产品并行时优先固定当前终端的产品上下文：

```bash
bash .harness/scripts/harness_init.sh list
bash .harness/scripts/harness_init.sh use --product-id product-id
eval "$(bash .harness/scripts/harness_product.sh env --product-id product-id)"
bash .harness/scripts/harness_knowledge.sh ensure
bash .harness/scripts/validate_harness.sh
bash .harness/scripts/doc-gardening.sh
```

5. 只有已有项目首次接入、上游显著升级或事实漂移时运行 `harness_intake.sh scan`，review 后再 `apply-review`；全新项目在规划完成后运行 `sync-planning`。二者都不是每个任务的固定步骤。
6. 新任务由 `plan` 生成 batch receipt，随后用 `<product-root>/harness-workspace/planning/tasks/<id>/03-实施方案.md` 的任务契约驱动开发；`start/finish` 按实际 diff/risk 选择最小 execution tier。L3 不再无条件映射 strict，高风险路径仍自动升级。

## 7. 任务契约

当前 L2/L3 以及目标 `standard/strict` 的实现任务必须包含：

| 字段 | 说明 |
|------|------|
| `id` | 任务 ID，如 `T1` |
| `read_files` | 必须阅读或复用的边界 |
| `write_files` | 允许修改的边界 |
| `action` | 做什么，不写模糊口号 |
| `verify` | 可执行验证命令 |
| `done` | 完成判定，对应 AC |

机械检查：

```bash
PRODUCT_ROOT=/path/to/product
bash .harness/scripts/task_contract_check.sh --task-dir "$PRODUCT_ROOT/harness-workspace/planning/tasks/<id>"
bash .harness/scripts/dag_sync_check.sh --task-dir "$PRODUCT_ROOT/harness-workspace/planning/tasks/<id>"
```

## 8. Product Profile

这里的 `profile` 专指 `generic` 或产品自定义的产品结构与领域配置，不表示执行风险；`lite/standard/strict` 的名称是 execution tier。

通用 Harness 不应写死任何产品域规则。迁移时只替换：

- harness-engineering 产品台账中的 `product.id` / `product.name` / `profile`
- product `harness-workspace/project.yaml`
- `.harness/profiles/<profile>/package-allowlist.yaml`（只有 `generic` 使用 `.harness/rules/package-allowlist.yaml` 兼容副本；显式未知 profile 直接阻塞）
- `docs/references/<profile>/`
- agent prompt 中的领域约束

公开仓库只内置 `generic` profile。产品专用 profile 由使用方在自己的受控环境维护，不进入通用默认值或公开参考资料。

## 9. 成熟度边界

已落地：

- JSON gate
- BMAD Planning/Harness Execution 交接
- Work Item provider 抽象：Teambition、飞书、Jira、noop 可按产品选择
- 路径白名单与 `plan_sync`
- `harness-task-v1` 7 字段任务契约与产品知识目录
- `dag_sync` 机械同步
- TEST/REVIEW/SUMMARY/PROGRESS 证据目录
- QA evidence + TEST/REVIEW 报告门禁
- 产品侧 lint/test 命令门禁
- controlled / Docker 双执行后端
- Playwright 浏览器 QA setup 与可选 CI job
- 自我成长报告生成

仍需按团队落地：

- Firecracker/远程隔离执行器等强沙箱
- 语言级 lint/test 接入
- 更丰富的 Playwright/browser QA 交互脚本库
- 更强的 TEST/REVIEW 语义质量校验
- 飞书与 Jira provider 的租户级深度联调、Webhook 与状态流转增强
