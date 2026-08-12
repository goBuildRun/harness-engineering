# Team Product R&D Harness Agent Map

本文件是 Agent 的最小导航图。它只回答“我该读哪里、遵守什么边界、用哪个角色”，不承载完整操作手册。

## System Intent

Team Product R&D Harness 是一套独立的产品研发 Harness runtime。它服务 1..N 个产品仓库；产品自己的规格、任务、运行状态、知识和证据沉淀在产品侧 `harness-workspace/`。

设计基准：

- 代码仓库和 workspace 产物是系统记录，不依赖聊天记忆。
- 入口文档做地图，细节按需跳转。
- 规则优先机械化，不能只靠自然语言提醒。
- 产品经验先进入产品 workspace，跨产品规则必须人工 review 后再上升到 harness-engineering。
- 参考项目的有效能力必须保留，但由统一入口按 execution tier 编排；Agent 不手工串联多套项目流程。

## Read First

| 你要做什么 | 入口 |
|------------|------|
| 理解整体架构 | [ARCHITECTURE.md](ARCHITECTURE.md) |
| 开始使用 Harness | [docs/USAGE.md](docs/USAGE.md) |
| 理解 harness-engineering / product workspace 分层 | [docs/Harness_Product_Workspace.md](docs/Harness_Product_Workspace.md) |
| 理解通用产品研发模型 | [docs/Team_Product_Harness.md](docs/Team_Product_Harness.md) |
| 多人协作与 Work Item | [docs/COLLABORATION.md](docs/COLLABORATION.md) |
| 质量门禁 | [docs/QUALITY.md](docs/QUALITY.md) |
| BMAD 前导 | [docs/BMAD_Prelude.md](docs/BMAD_Prelude.md) |
| 当前成熟度与技术债 | [docs/Harness_成熟度评估.md](docs/Harness_成熟度评估.md) |
| 理解精简目标与能力保留 | [docs/design-docs/lean-enforcement.md](docs/design-docs/lean-enforcement.md) |

## Role Map

| 角色 | 主要职责 | 禁止事项 |
|------|----------|----------|
| `planning-agent` | 产品前导、规格和任务建档 | 写业务代码 |
| `lead-agent` | 拆 DAG、调度、维护执行闭环 | 亲自实现业务代码 |
| `backend-agent` | 服务端 TDD 实现 | 自签 QA |
| `frontend-agent` | 前端 TDD 实现与浏览器证据 | 自签 QA |
| `qa-evaluator` | 独立测试、审查、签章 | 写业务逻辑 |
| `gc-sweeper` | 熵减、清理调试残留、整理证据 | 新增功能 |

详细角色配置在 runtime `.harness/agents/` 目录。Claude 专用适配见 [CLAUDE.md](CLAUDE.md)。其它工具适配应保持同样原则：只写适配差异，不复制完整手册。

## Product Workspace Map

| 产品侧目录 | 责任 |
|------------|------|
| `<product-root>/harness-workspace/project.yaml` | 产品 workspace 配置真相源 |
| `<product-root>/harness-workspace/planning/` | 产品规格、执行计划、任务包 |
| `<product-root>/harness-workspace/runs/` | 本地运行状态、active task、QA 凭证 |
| `<product-root>/harness-workspace/knowledge/` | 产品级 CONTEXT / LESSONS / REFERENCE_SYSTEMS；`agent_start` 会注入摘要 |
| `<product-root>/harness-workspace/evidence/` | INTAKE / SUMMARY / PROGRESS / TEST / REVIEW / GROWTH |

harness-engineering 不保存产品 PRD、任务包、测试报告或成长报告。

## Hard Boundaries

- 先产品前导，再实现；没有通过前导门禁，不进入执行闭环。
- Lead 不写业务代码；执行 Agent 不自签；QA 不写业务逻辑。
- 任务完成必须有与 execution tier 匹配的可执行验证和有效 Harness 结果；独立 QA、TEST/REVIEW 证据按 `standard` / `strict` 要求执行，不能一刀切下放给 `lite`。
- 变更路径必须符合产品 profile 的结构白名单，并与实施方案同步。
- 精简只能隐藏或按需触发内部能力，不能删除 BMAD 规划、TDD、风险匹配验证、安全边界或知识审阅语义。
- 接入报告和成长报告都只是候选；长期记忆必须人工 review 后通过 `apply-review` 迁入产品知识。
- 排障或实现中发现跨任务可复现教训、外部系统权限坑、架构边界或新默认行为时，Agent 必须先运行 `harness_growth.sh capture` 写入 `evidence/progress/` 候选证据；不要等用户提醒，也不要直接写长期知识。

## Runtime Internals

harness-engineering runtime 包含 agents、rules、scripts、templates、workflows 和 work-items。只有在需要执行命令、改规则或调试门禁时才深入阅读：

- 脚本索引：[.harness/README.md](.harness/README.md)
- 运行时清单：[.harness/harness-manifest.yaml](.harness/harness-manifest.yaml)
- 文档边界：[.harness/rules/doc-boundary.md](.harness/rules/doc-boundary.md)
