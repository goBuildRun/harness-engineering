# Harness 精简强制执行设计

> 状态：shadow 实现中；共享结果、三入口、GC 判定、迁移审计和 commit 判定器已落地，平台 enforcement 尚未验证
> 日期：2026-08-11
> 适用范围：所有通过 harness-engineering 接入的产品迭代  
> 当前命令实态仍以 [USAGE.md](../USAGE.md) 为准。

## 1. 目标

Harness 同时满足两个要求：

1. **严格执行**：接入产品的正式迭代不能绕过 Harness 成为有效交付。
2. **保持精简**：治理强度与风险匹配，不用重复命令、重复证据和重复上下文换取安全感。

最高原则：

> 没有与当前变更匹配的 Harness 合规结果，任务不能进入有效完成态；Harness 对合规结果零妥协，对执行成本持续最小化。

严格执行不等于所有任务运行同一条最长流程。严格的含义是：每个任务都完整执行其风险层级要求，缺少信息时不能自行降级。

### 1.1 能力保留契约

精简升级是执行面的收敛，不是推倒重来。参考项目中已经证明有效的能力继续保留，但由 `start/status/finish` 按 execution tier 自动编排：

| 来源 | 必须保留的能力 | 目标承载方式 |
|------|----------------|--------------|
| OpenAI Harness Engineering | 短 Agent 地图、仓库记录系统、渐进披露、机械反馈、doc gardening | `start` 加载最小上下文，`finish` 统一机械判定；内部继续复用规则和检查器 |
| BMAD Method | Analysis / Planning / Solutioning、可测试验收、实现就绪 | `lite` 使用 Quick Flow，`standard/strict` 按风险加载完整规划能力；规划产物仍归产品 workspace |
| planning level | L1/L2/L3 规划分级、任务模板、Entry Gate | 保留为 planning level 和 legacy 兼容；不再要求 Agent 手工选择整条执行命令链 |
| Flow-X | CONTEXT / LESSONS / PROGRESS / SUMMARY / TEST / REVIEW / GROWTH 的知识语义 | 保留目录和语义；PROGRESS、SUMMARY、TEST、REVIEW、GROWTH 按恢复、tier 或长期候选触发 |
| Superpowers | 规格先行、设计确认、TDD、调试纪律、完成前验证 | 作为 tier 相关内部 gate 和执行纪律，不扩展公共命令面 |
| GStack | 真实环境调查、浏览器 QA、审查视角 | 对前端、交互或高风险任务按需启用，不成为所有任务固定步骤 |
| Ralph / Agent Review | Agent 审 Agent、失败后修复重试 | 作为 `standard/strict` 可选或强制检查，由 `finish` 编排并计入预算 |
| Brownfield Intake | 已有项目事实扫描、上游参考系统建档、人工 review 后入知识 | 首次接入或事实漂移时运行，不在每个任务重复扫描 |
| Work Item providers | 负责人、状态、讨论和跨角色协同 | 保留 adapter；任务身份与 provider ID 分离，状态由统一生命周期同步 |

兼容的对象是能力、产物语义和审计链，不是旧脚本名称。任何能力迁入统一门面时，都必须说明原入口如何转为内部调用、按需触发或被删除。

## 2. 最小操作面

目标状态只向人类和 Agent 暴露三个动作：

```bash
harness start [<task-id>] [--work-item <provider-id>]
harness status [<task-id>]
harness finish [<task-id>]
```

- `start`：无 ID 时生成本地任务 ID，有 ID 时恢复任务；识别产品、绑定可选外部 Work Item、记录基线并给出初始 execution tier。
- `status`：默认读取当前任务，显示范围、当前有效 execution tier、成本、已通过检查和阻塞项。
- `finish`：重新判定最终 execution tier，执行或复用必要检查，生成本地合规结果；`pass` 只表示可进入提交/合并验证，不直接关闭外部 Work Item。

任务身份与外部协同 ID 分离：Harness 始终拥有稳定 `task-id`，`standard` / `strict` 另绑定产品 Work Item。历史任务可继续让两者取相同值。任务推断存在歧义时必须 `block` 并列出候选，不能静默选择“最近任务”。

CI 可见的任务绑定不能只存在于 gitignored 的 `runs/`：

- 新 `lite` 任务由 `start` 自动生成可提交的最小 `planning/tasks/<task-id>/task.json`，只含任务 ID、声明范围、execution tier 下限和可选 Work Item；它替代 L1 完整任务包，不新增 TEST/REVIEW/SUMMARY。Harness 不自动创建 Git commit。
- `standard` / `strict` 复用现有任务包中的 `Harness Task ID`、兼容 `任务编号`（Work Item ID）、provider、planning level、execution tier 下限和显式升级原因，不再复制一份绑定文件。现有脚本在过渡期继续解析 `任务编号`。
- CI 必须从变更中解析出唯一绑定并校验其 scope；零个或多个候选都 `block`。

现有 `planning_gate.sh`、`agent_start.sh`、`qa_sign_off.sh`、`subagent-pr-gate.sh`、`qa_evidence_check.sh`、`harness_growth.sh`、`check.sh` 和 provider 命令在实现期继续可用，但应逐步收敛为上述入口的内部能力，不再要求 Agent 手工编排完整命令链。

## 3. 单一权威结果

每个任务只保留一个机器可读的本地状态快照：

```text
harness-workspace/runs/tasks/<task-id>/result.json
```

最小字段：

```json
{
  "schema_version": 1,
  "task_id": "...",
  "work_item": null,
  "state": "active|blocked|validated",
  "enforcement": "shadow|enforced",
  "tier": {"initial": "standard", "effective": "strict"},
  "binding_digest": "...",
  "baseline": {"digest": "...", "source": "start|legacy|migration"},
  "subject": {"kind": "worktree|commit", "digest": "..."},
  "policy_digest": "...",
  "checks": {},
  "cost": {
    "implementation": {
      "input_tokens": "unknown",
      "output_tokens": "unknown",
      "context_chars": "unknown",
      "agent_calls": "unknown"
    },
    "harness": {
      "input_tokens": "unknown",
      "output_tokens": "unknown",
      "context_chars": 0,
      "agent_calls": 0,
      "gate_duration_ms": 0,
      "reruns": 0
    },
    "telemetry_complete": false
  },
  "decision": "pass|block"
}
```

每个 `checks.<name>` 最少记录 `decision`、`fingerprint`、`subject_digest`、`source: executed|cache`、完成时间和必要 evidence 引用；人工 Gate 另记录可信 reviewer identity。`cost` 至少区分产品实现成本和 Harness 固定开销，记录输入/输出 Token、上下文字符数、Agent 调用数、gate 耗时、重跑次数与完整性，不保存 prompt 正文、secret 或完整命令输出。字段拿不到精确值时写 `unknown`；只有确认没有发生该项活动时才能写 `0`，禁止估算后伪装成精确遥测。

`status` 默认展示两类成本及可计算的合计；只有相关字段都为数值时才输出合计值，否则显示 `unknown` 并把 `telemetry_complete` 置为 `false`。不得再为成本跟踪新增一套 Markdown 报告或独立状态协议。

`result.json` 使用任务级单写锁和“临时文件 + 原子替换”更新；中断后可恢复，多个 Agent 不得并发覆盖。它是当前任务的物化状态，不是可由开发者提交后让 CI 盲信的证明。

CI 必须对目标 commit 独立运行同一结果 schema，并把 `commit SHA + policy_digest + decision` 发布为 required check 和 CI artifact。合并/发布以该 commit 绑定的 CI check 为接受真相；本地 `result.json` 只能证明当前工作区已验证。二者共用一个 schema 和判定器，不新增第二套报告协议。

代码、计划、测试输入、相关配置、工具版本或 Harness policy 改变后，依赖旧 fingerprint 的结果自动失效。Markdown 只承担必要的人类摘要，不再默认按每个 T 复制 TEST、REVIEW、SUMMARY 和 QA 结论。

## 4. 风险分层

| Execution tier | 典型变更 | 最小验证深度 |
|---------|----------|--------------|
| `lite` | 文档、小修复、低风险配置 | 可追踪任务身份、范围检查、增量验证、成本记录、最终结果 |
| `standard` | 普通产品功能，默认层级 | `lite` + 独立审查、测试证据、知识变化判断 |
| `strict` | 生产、权限、安全、数据迁移、外部副作用 | `standard` + 完整 QA、人工 Gate、部署/回滚或生产证据 |

分层规则：

- `start` 只能根据计划、声明范围和已有路径给出初始 execution tier；`status` 与 `finish` 必须根据实际 diff 重新判定。
- 有效 execution tier 取 Harness core 下限、产品 policy、任务显式升级和实际变更风险四者中的最高值，并在任务生命周期内只升不降。
- Agent 或人类可以升级 execution tier，不能在缺少证据时自行降级。
- 无法可靠分类时使用 `standard`；命中高风险因素时强制 `strict`。
- BMAD 的 L1/L2/L3 规划复杂度与 execution tier 相关但不等同，最终 tier 由实际风险决定。
- policy 继续写入现有 Harness 配置与产品 `project.yaml`，不为每个 tier 新增一套配置文件；结果必须记录 `policy_digest` 以便复现判定。
- 人工或 Agent 显式升级必须写入可提交任务绑定的 `Execution tier 下限` / `Tier 显式升级` 字段，CI 不信任只存在于本地 `result.json` 的升级声明。

## 5. 四个不可省略的不变式

任何 execution tier 都必须证明：

1. 正式迭代绑定一个可追踪任务身份；`standard` / `strict` 使用产品 Work Item，`lite` 可使用 Harness 生成的本地任务 ID，不得无 ID 执行。
2. 实际改动没有越过声明的产品和任务范围。
3. 已运行与实际风险匹配的验证。
4. 本地 `finish` 返回 `pass` 后才能发起合并；目标 commit 的 CI required check 返回 `pass` 后才能合并或发布，外部 Work Item 只能由合并/发布自动化关闭。

人工 Gate 不能由 Agent 自行签署。未通过 `finish` 的直接编辑属于未受管变更，可以保留在工作区，但不能成为有效完成态。

## 6. 接入保障等级与强制执行边界

任务执行深度与部署环境的防绕过能力是两个正交维度：

- execution tier `lite|standard|strict` 回答“这个任务必须验证多深”；每个 tier 都必须完整执行自身要求。
- assurance level `local|guarded|enforced` 回答“谁能阻止无效结果成为正式交付”；它不降低或替代 execution tier。

| Assurance level | 最小环境 | 接受权威 | 保证与边界 |
|-----------------|----------|----------|------------|
| `local` | Git + Python + `start/status/finish` | 当前工作区/本地 Git | 结果真实、可复现、可审计；防漏主要依赖正常入口，不能承诺不可绕过 |
| `guarded` | `local` + 版本化 Git guards 和/或 CI commit check | 正常 Git 工作流 + CI | 对常规 commit/push/PR 机械阻断并暴露显式绕过；hook 可被 `--no-verify` 或本机管理员绕过，因此不等于 enforced |
| `enforced` | `guarded` + 受保护的权威接受点和发布/provider 门禁 | protected branch、受控 bare repository、发布/制品 gate 等 | 缺少绑定目标 commit 的有效 Harness 结果不能成为正式交付；管理员应急 bypass 必须独立审计 |

轻量推广路径是“先完整执行，再逐级增强接受保障”：五分钟进入 `local`，启用低成本 guards 后进入 `guarded`，只有确有不可绕过合规需求的团队才接入 `enforced`。默认采用不要求自建 Git 服务、数据库或常驻编排器；GitHub、GitLab、Gitea 都只是可选的 enforced 承载平台，不是 Harness core 依赖。

目标 `result.json` 在现有字段之外增加结构化保障快照：

```json
{
  "assurance": {
    "level": "local|guarded|enforced",
    "task_execution": "complete|incomplete",
    "acceptance_authority": "worktree|git-guards+ci|protected-authority",
    "bypassable": true,
    "verified_at": "...",
    "blockers": []
  }
}
```

该字段尚未落地前继续保留 `enforcement: shadow|enforced` 兼容契约：`local` 与 `guarded` 都映射为 `shadow`，只有 live audit 证明权威接受链不可绕过时才映射为 `enforced`。`shadow` 是旧协议的兼容值，不再作为面向采用者的成熟度名称。

仅靠提示词、`AGENTS.md` 或操作手册不能保证遵循。目标实现使用四层控制：

1. **导航层**：告诉 Agent 正确入口和最小规则。
2. **CLI 层**：`start/status/finish` 提供唯一正常路径。
3. **状态层**：本地状态只允许 `active → blocked|validated`；任何输入变化都把 `validated` 失效回待验证状态。
4. **合并层**：CI/MR 对目标 commit 重新判定并发布 required check，阻止未受管变更合并或发布；provider 在本地 `finish` 后最多进入 ready/review，合并或发布成功后才能进入 `done`。

保障重点是“绕过后不能被接受”，而不是假设所有工具都能阻止用户直接编辑文件。

“已安装”不等于 `enforced`。产品只有同时满足以下条件才可标记 `assurance.level: enforced`：权威接受点使用共享判定器、目标分支或等价发布入口要求 Harness check、发布依赖该 check、外部 Work Item 的 `done` 只由合并/发布自动化写入，并且 live probe 能证明成功运行绑定当前目标 commit。GitHub 实现通过目标 commit 的 workflow、branch protection 和 Actions run 验证这些条件；其他平台必须提供等价的受保护接受证据。未完成或无法验证任一条件时只能是 `local` 或 `guarded`，兼容字段继续写 `enforcement: shadow`，不得宣称流程不可绕过。

仓库管理员仍可能使用平台级紧急 bypass；该动作位于 Harness 本身权限边界之外，必须由平台审计记录并被视为显式例外，不能生成有效 Harness `pass`。

## 7. 成本控制不变式

成本控制属于正确性要求，不是附加报表：

- 每个任务记录输入/输出 Token、注入上下文字符数、Agent 调用数、gate 耗时和重跑原因；工具无法提供精确值时记录 `unknown`，禁止用 `0` 伪装完整数据。
- 上下文按任务路径、标签和知识条目检索，不再固定注入长期知识文件头部。
- gate 结果按输入 fingerprint 缓存；fingerprint 至少包含 schema、policy、任务契约、baseline/subject、相关 diff、配置以及会影响结果的工具版本。
- 只有确定性机械 gate 可以跨进程缓存。人工签署、生产副作用和 strict 的部署/回滚证据必须绑定当前 subject，不得跨 commit 复用。GC Agent 结果不作为机械缓存；receipt 必须声明独立 `gc-sweeper` 角色并绑定当前 task、subject 与 policy。
- Growth 仅在发现新失败模式、架构边界、默认行为或明确技术债候选时触发。
- 外部 Work Item 使用批量差异同步，不逐项重复 close/pull。
- 超出 execution tier 预算时先停止自动扩张上下文或 Agent 调用，并返回 `BUDGET_APPROVAL_REQUIRED`；人工批准只能增加预算或升级 tier，不能跳过必要 gate。
- CI required check 对目标 commit 重算 GC 信号；需要独立 GC 时通过 subject-bound HTTPS reviewer 获取 receipt，并把精确调用次数、上下文字符数与耗时写入统一成本字段。缺 reviewer 配置或无效 receipt 不能降级为机械 pass。

成本优化不能取消四个不变式，也不能降低高风险任务的证据质量。

## 8. 证据精简规则

- `result.json` 是本地任务状态真相源；commit 的合并/发布判定以同 schema 的 CI required check 为准。
- `04-实施记录.md` 只记录关键决策、异常和最终结果，不复制完整命令输出。
- `TEST.md`、`REVIEW.md`、`SUMMARY.md` 只在 execution tier 或人工阅读需求要求时生成。
- 多个 T 可以共享同一验证批次；只有风险和所有权独立时才拆证据。
- 历史 evidence 建索引并归档，默认不进入 Agent 上下文。
- 长期知识只收录跨任务仍有效的内容，不能把每轮执行记录复制进 CONTEXT/LESSONS。

## 9. 实施约束

该设计的落地不得先增加另一套并行框架。实施顺序遵循“影子验证后替换”：

1. 先为四个不变式补可执行验收测试，并记录当前任务耗时、Token、上下文和产物数量基线。
2. 实现共享 result schema、原子状态写入和成本遥测，以 shadow mode 包装现有 `check.sh`；此阶段结果不改变当前准入判定。
3. 让 execution tier 分类器在 shadow mode 同时读取计划与实际 diff，验证升级、未知分类和 policy digest，不先开放降本路径。
4. 提供 `start/status/finish` 门面，初期默认 `standard` 并调用现有 gate；CI 使用同一判定器对 commit 独立生成 required check。
5. CI 权威链和 provider 状态流转稳定后，再启用 `lite` 精简、`strict` 加强与确定性 gate 缓存。
6. 最后迁移活动任务，隐藏被门面替代的 Agent 可见命令链，并删除冗余证据要求。

每增加一个新入口或产物，必须同时说明它替代什么；不能证明替代关系的新增内容不进入 Harness core。

上线使用单一 feature flag 支持按产品灰度和回退；回退只恢复旧入口，不删除新格式或历史数据。

首版只使用现有 Python/shell、JSON/YAML、文件锁和 CI 能力，不引入数据库、常驻服务、事件总线或新的工作流引擎。

## 10. 历史数据与兼容升级

升级 Harness runtime 不应要求产品全量重写 `harness-workspace/`。默认策略是：

> 历史数据原位可读，新任务写新格式；只迁移继续执行所必需的最小状态。

| 现有内容 | 默认处理 | 原因 |
|----------|----------|------|
| `planning/product-specs/` | 原位保留 | 产品规格真相源不因 runtime 升级改变 |
| `planning/exec-plans/` | 原位保留 | 架构和执行决策继续有效 |
| `planning/tasks/` 已完成任务 | 不迁移，只读保留 | 不为历史任务补造新流程证据 |
| `planning/tasks/` 进行中任务 | 轻量迁移 | 只补 execution tier、baseline 和初始 `result.json` |
| `knowledge/CONTEXT.md` | 原位保留 | 后续改变检索方式，不重写知识正文 |
| `knowledge/LESSONS.md` | 原位保留 | 可渐进补稳定 ID 和标签，不要求一次转换 |
| `knowledge/REFERENCE_SYSTEMS.md` | 原位保留 | 上游边界知识继续作为长期事实 |
| `evidence/` 历史 TEST/REVIEW/SUMMARY/GROWTH | 不迁移 | 作为历史证据保留，默认不再注入上下文 |
| `runs/tasks/<id>/qa_approved_*.json` | 兼容读取 | `finish` 可将旧 QA 凭证映射为检查结果 |
| `planning_gate_pass.json` / `phase0_pass.json` | 兼容读取 | 作为已有 Planning Gate 证据，不重新生成 |
| 外部 Work Item ID | 原样保留 | 禁止重建 ID 或破坏现有协同状态关联 |

目标兼容读取顺序：

1. 存在与当前输入匹配的 `result.json`：使用新模型。
2. 不存在 `result.json`，但存在旧 Planning/QA 凭证：通过兼容层读取。
3. 两者都不存在：视为未受管任务并 `block`。

目标迁移命令：

```bash
harness workspace audit
harness migrate-task <task-id>
```

- `workspace audit` 只报告新格式任务、legacy 已完成任务、需要迁移的进行中任务和缺失关键凭证的任务，不修改数据。
- `migrate-task` 只面向进行中或明确重开的任务。存在可靠旧 baseline 时沿用；不存在时以迁移时 merge-base/工作区建立新 baseline，记录 `baseline_source: migration`，废弃旧缓存并至少按 `standard` 完整验证，禁止声称恢复了不存在的历史 baseline。
- 已完成历史任务只有在审计或重开时才按需生成 `decision: historical` 的只读结果。

迁移过程禁止移动、删除或批量改写原有 planning、knowledge 和 evidence 文件。旧格式读取能力至少跨一个明确兼容版本保留；移除前必须先由 `workspace audit` 证明没有仍依赖旧格式的活动任务。

## 11. 成功判定

- 未经过 Harness 的变更无法关闭 Work Item、通过 required check 或发布。
- 本地伪造、复制或提交 `result.json` 不能让其他 commit 通过 required check。
- `lite` 任务不再承担 `strict` 的证据数量和命令链。
- 相同输入的 gate 不重复运行。
- 单任务可以区分实现成本与 Harness 固定成本。
- 接入产品只需学习 `start/status/finish` 和一个结果文件。
- 旧脚本数量可以逐步减少，而不是继续增长。
- 已接入产品无需全量迁移，活动任务可最小升级，历史审计链保持可读。

首批产品灰度退出条件：绕过接受测试和 stale-cache 接受测试必须 100% 阻断；`lite` 相比当前 L2 路径的中位 Harness 上下文与证据数量至少降低 40%，中位门禁耗时至少降低 30%；execution tier 误降级为 0，非风险性误阻断率不高于 5%。未达到这些指标时不扩大 rollout。
