# AEL 文档地图

这份地图按读者任务组织文档。每个主题只保留一个操作或设计真相源；历史执行包保留事实，不参与日常操作路径。

## 快速开始

| 需要解决的问题 | 权威文档 |
|---|---|
| 安装、绑定产品、运行 `plan/start/status/finish` | [getting-started/cli.md](./getting-started/cli.md) |
| 已有项目首次接入 | [getting-started/brownfield-intake.md](./getting-started/brownfield-intake.md) |

## 架构与模型

| 主题 | 权威文档 |
|---|---|
| 系统边界、闭环、能力来源 | [architecture/overview.md](./architecture/overview.md) |
| 通用产品研发模型与角色 | [architecture/team-model.md](./architecture/team-model.md) |
| 从 Intake 到交付的阶段流 | [architecture/workflow.md](./architecture/workflow.md) |
| AEL 与 product workspace 分层 | [architecture/workspace.md](./architecture/workspace.md) |
| 核心原则与取舍 | [architecture/principles.md](./architecture/principles.md) |

## 规划与执行

| 主题 | 权威文档 |
|---|---|
| BMAD Planning、Planning Gate 和规格路径 | [planning/bmad-planning.md](./planning/bmad-planning.md) |
| BMAD 到 Work Item 的同步契约 | [planning/work-item-contract.md](./planning/work-item-contract.md) |
| 多人协作、Work Item 领取和角色交接 | [execution/collaboration.md](./execution/collaboration.md) |

## 设计与治理

| 主题 | 权威文档 |
|---|---|
| 精简强制执行、风险分层和成本边界 | [design/lean-enforcement.md](./design/lean-enforcement.md) |
| 推荐的短流程和 30 分钟止损预算 | [design/lean-plan-flow.md](./design/lean-plan-flow.md) |
| 参考能力的承载、按需触发和验收 | [design/capability-migration.md](./design/capability-migration.md) |
| 质量门禁与品味不变式 | [governance/quality.md](./governance/quality.md) |
| 安全边界 | [governance/security.md](./governance/security.md) |
| 可靠性与可观测性 | [governance/reliability.md](./governance/reliability.md) |
| 文档所有权、真相源和一致性检查 | [governance/documentation.md](./governance/documentation.md) |

## 运行状态与计划

| 主题 | 权威文档 |
|---|---|
| 当前成熟度与优先级 | [operations/maturity.md](./operations/maturity.md) |
| 技术债状态 | [operations/tech-debt.md](./operations/tech-debt.md) |
| 当前执行计划 | [plans/active/](./plans/active/) |
| 外部能力来源路由 | [references.md](./references.md) |

## 历史与审计

`plans/active/` 中的三个 E4/升级文档是版本化执行包和审计证据，不是新的操作入口。它们保留历史时间线、离线基准、未决风险和迁移上下文；新设计应更新设计文档，不复制历史报告。

上级入口：[AGENTS.md](../AGENTS.md) · [ARCHITECTURE.md](../ARCHITECTURE.md) · [README.md](../README.md)

新增文档必须先说明它替代或合并了哪个主题，并在此登记；如果内容能归入现有权威文档，不得新增文件。
