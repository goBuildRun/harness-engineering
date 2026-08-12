# Team Product R&D Harness

面向软件产品研发团队的独立式 AI engineering harness。它把产品意图、任务身份、Agent 执行、风险匹配验证和知识沉淀连接成可审计闭环，并由一套 harness-engineering 服务多个产品仓库。

> 当前可执行命令以 [docs/USAGE.md](./docs/USAGE.md) 为准。`harness start/status/finish`、单一 `result.json` 和 `lite/standard/strict` 已进入可执行阶段；旧脚本只保留作兼容和诊断入口，不与统一门面形成第二套完成态。

## 核心原则

> 没有与当前变更匹配的 Harness 合规结果，任务不能进入有效完成态；Harness 对合规结果零妥协，对执行成本持续最小化。

- 所有正式迭代都有可追踪任务身份。
- 实现前必须形成与复杂度匹配的 BMAD Planning 结果。
- 实际变更不得越过产品、profile 和任务声明范围。
- 验证深度由实际风险决定；严格执行不等于所有任务执行最长流程。
- 本地验证只允许进入 review；合并、发布和关闭外部 Work Item 由 commit 绑定的 CI 结果决定。
- 规则优先机械化，不能只依赖 Agent 记忆或提示词。
- 产品经验先进入产品 workspace；跨产品规则必须经人工 review 后进入 harness-engineering。

## 系统边界

```text
harness-engineering/
├── AGENTS.md                 Agent 最小导航
├── ARCHITECTURE.md           系统边界与真相源
├── docs/                     设计、使用、质量与协作
├── .harness/                 scripts / rules / agents / templates
└── tasks/_templates/         当前 L2/L3 任务包模板

product-repo/
├── product code / tests / architecture
└── harness-workspace/
    ├── project.yaml          产品接入配置真相源
    ├── planning/             规格、计划、任务绑定
    ├── runs/                 本地任务状态与凭证
    ├── knowledge/            CONTEXT / LESSONS / REFERENCE_SYSTEMS
    └── evidence/             按需生成的 INTAKE / TEST / REVIEW / GROWTH
```

harness-engineering 不保存产品 PRD、任务实例、测试报告或产品知识。产品差异由 `harness-workspace/project.yaml` 和 active profile 承载；通用 Agent 不写死任何产品路径。

## 能力来源

精简升级保留经过验证的能力，不保留外部项目的命令数量和固定仪式：

| 来源 | 保留能力 | Harness 中的承载方式 |
|------|----------|----------------------|
| OpenAI Harness Engineering | 短地图、repo-local 记录、渐进披露、机械反馈、doc gardening | `AGENTS.md`、JSON gate、统一结果 |
| [BMAD Method](https://github.com/bmad-code-org/BMAD-METHOD) | Analysis、Planning、Solutioning、可测试验收 | planning level 决定 Quick Flow 或完整规划 |
| [Flow-X](https://github.com/aitanjp/flow-x) | CONTEXT、LESSONS、PROGRESS、SUMMARY、TEST、REVIEW、GROWTH 语义 | 产品 workspace 中按恢复、风险或长期候选触发 |
| [Superpowers](https://github.com/obra/superpowers) | 规格先行、TDD、调试纪律、完成前验证 | execution tier 相关内部 gate |
| [GStack](https://github.com/garrytan/gstack) | 真实环境调查、浏览器 QA、审查视角 | 前端、交互和高风险任务按需启用 |
| planning level | L1/L2/L3、任务模板、Entry Gate | 保留为 planning level，不作为执行风险等级 |
| Ralph / Agent Review | Agent 审 Agent、失败修复重试 | `standard/strict` 按风险启用 |
| Brownfield Intake | 已有项目事实扫描和人工审阅 | 首次接入或事实漂移时运行 |
| Work Item providers | 负责人、状态、讨论与协同 | provider adapter；任务 ID 与外部 ID 分离 |

完整路由见 [外部参考索引](./docs/references/index.md) 和 [能力保留契约](./docs/design-docs/lean-enforcement.md#11-能力保留契约)。

## 当前状态

当前 runtime 已具备：

- 多产品注册、session 产品上下文和 product workspace 路径解析。
- BMAD Planning、Planning Gate 与任务工作区恢复。
- noop、Teambition、飞书和 Jira provider 基础能力。
- Intake、产品知识、任务契约、DAG、结构与计划同步。
- 受控命令、Docker 后端、QA 签章、浏览器 QA 和 CI gate。
- 历史 workspace、Planning Gate、QA 与 evidence 的兼容读取基础。
- `harness start/status/finish` 统一门面、原子 `result.json` 和 execution tier 分类。
- 成本遥测、确定性 gate fingerprint 缓存、GC 判定与活动任务迁移审计。
- commit 绑定共享判定器、GitHub required/release eligibility/provider completion workflow 模板和 enforced live probe。

尚未完成：

- 结构化 `assurance.level: local|guarded|enforced` 结果字段及版本化 guarded Git guards。
- GitHub required check、受保护分支、release eligibility 和 provider completion 的真实环境端到端验收。
- 非 GitHub 平台的等价受保护接受点 live probe，以及更多 provider usage/lifecycle 实测。

任务风险使用 `lite|standard|strict`，部署保障使用 `local|guarded|enforced`，两者互不替代。当前兼容 schema 仍将 `local`/`guarded` 映射为 `shadow`；仅安装 workspace 不代表流程不可绕过。成熟度与实施顺序见 [Harness_成熟度评估.md](./docs/Harness_成熟度评估.md)。

## 最小接入

以下命令在 `harness-engineering/` 根执行：

```bash
BIN=.harness/scripts

bash "$BIN/harness_init.sh" init \
  --product-root /path/to/product \
  --product-id my-product \
  --product-name "My Product" \
  --profile generic \
  --work-item-provider noop \
  --install-bmad
```

初始化只建立产品 workspace、台账和可选 BMAD 环境。provider、质量命令、沙箱、浏览器 QA 与多人协作配置统一在 [USAGE.md](./docs/USAGE.md) 维护。

已有项目首次接入时：

```bash
bash "$BIN/harness_intake.sh" status
bash "$BIN/harness_intake.sh" scan
```

INTAKE 报告只是候选证据。完成报告中的 review 工作台后才能执行：

```bash
bash "$BIN/harness_intake.sh" apply-review
```

若报告仍有未勾选项，`apply-review` 会返回 `INTAKE_REVIEW_PENDING`；`--allow-pending` 只用于生成草稿，不代表正式沉淀完成。完整说明见 [Brownfield_Intake.md](./docs/Brownfield_Intake.md)。

## 使用模型

正常路径只有：

```bash
harness start [<task-id>] [--work-item <provider-id>]
harness status [<task-id>]
harness finish [<task-id>]
```

入口脚本为 `.harness/scripts/harness`。旧命令链仅用于兼容和诊断，不应被照抄成每个任务的固定全链。所有任务都满足共同不变式，独立 QA、TEST/REVIEW、浏览器验证、GC 和人工 Gate 只在当前工序或实际风险要求时启用。

## 自检

修改 harness-engineering 后至少运行：

```bash
bash .harness/scripts/validate_harness.sh
bash .harness/scripts/doc-gardening.sh
```

产品任务还必须执行与实际变更匹配的质量命令和最终 gate。所有 Harness 脚本用 JSON `decision: pass|block` 表达判定；收到 `block` 时按 `reason` 修复，不通过忽略退出码或改 allowlist 绕过。

## 文档入口

| 文档 | 唯一职责 |
|------|----------|
| [AGENTS.md](./AGENTS.md) | Agent 最小地图与不可绕过边界 |
| [ARCHITECTURE.md](./ARCHITECTURE.md) | 系统边界、状态与真相源 |
| [docs/USAGE.md](./docs/USAGE.md) | canonical 使用模型和当前兼容命令 |
| [docs/design-docs/lean-enforcement.md](./docs/design-docs/lean-enforcement.md) | 精简强制执行目标 |
| [docs/BMAD_Prelude.md](./docs/BMAD_Prelude.md) | BMAD Planning 专章 |
| [docs/COLLABORATION.md](./docs/COLLABORATION.md) | 多人和 Work Item 协作 |
| [docs/Brownfield_Intake.md](./docs/Brownfield_Intake.md) | 已有项目接入与 review |
| [docs/QUALITY.md](./docs/QUALITY.md) | 质量门禁与风险分层 |
| [docs/references/index.md](./docs/references/index.md) | 参考能力与内部权威路由 |
| [.harness/README.md](./.harness/README.md) | runtime 脚本索引 |

历史数据默认原位可读；新任务写新格式；只迁移继续执行所需的最小状态。升级不会批量移动、删除或重写产品 `planning/`、`knowledge/` 和 `evidence/`。
