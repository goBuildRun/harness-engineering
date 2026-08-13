# Team Product R&D Harness 成熟度评估

> 初始评估日期：2026-06-21  
> 最近复核：2026-08-13
> 评估对象：harness-engineering `harness-engineering/` 当前实现 + product-owned `harness-workspace/`  
> 评估口径：以资深 Harness Engineering 工程师视角，区分「框架骨架成熟度」与「生产闭环成熟度」。

## 1. 总体结论

Team Product R&D Harness 已经从 embedded 项目脚手架升级为 **harness-engineering + product-owned workspace**。它当前的核心价值不在于单个脚本，而在于把已有项目接入、产品前导、Work Item 协同、Agent 执行、机械门禁、QA 签章、知识沉淀和自我成长串成了一个可运行闭环。

但它还没有完全进入「生产级全自动强约束 Harness」状态。关键差距集中在 Firecracker/远程隔离执行器、更多业务栈质量矩阵、复杂浏览器交互脚本库、业务服务大规模实战、第二协同 provider 验证。

2026-08-09 对多个试点产品的迭代证据复核新增一个更优先的判断：当前流程存在明显固定税，简单任务也承担完整命令链和多份重复证据，成熟产品的历史 evidence 又持续推高上下文成本。下一阶段首先落实 [精简强制执行设计](./design-docs/lean-enforcement.md)，目标是增强不可绕过性，同时减少 Agent 可见入口、重复 gate、重复证据和无关上下文。

## 2. 双评分

| 评分口径 | 当前评分 | 解释 |
|----------|----------|------|
| Harness 框架骨架成熟度 | **4.80 / 5** | harness-engineering / product workspace 分层、设计、脚本、文档、门禁、任务契约、知识成长已经形成完整骨架 |
| 生产闭环成熟度 | **4.56 / 5** | QA evidence、产品侧 quality commands、DAG product workspace 默认路径、Docker 后端、浏览器 setup/CI/audit 已进入可执行链；更强隔离、更多业务栈验证仍需补强 |
| 组织推广成熟度 | **4.55 / 5** | harness-engineering 产品台账 + product workspace 模型清楚；Teambition 已通，飞书/Jira 已具备基础 provider，仍需真实租户深度联调 |

综合判断：**可用于受控产品研发试运行；适合在首个业务服务 MR 中继续压实；距离广泛开源推广还差一轮生产化加固。**

## 3. 分项评估

| 维度 | 评分 | 当前状态 | 主要缺口 |
|------|------|----------|----------|
| 架构定位 | 4.9 | harness-engineering + product workspace、BMAD + Work Item + Flow-X 知识层已统一 | 需要更多独立产品 profile 示例 |
| 已有项目接入 | 4.6 | `harness_intake.sh` 已能扫描既有代码、关键入口、文档、测试与技术栈，生成产品侧 INTAKE 候选报告，并沉淀 `REFERENCE_SYSTEMS.md` 证据入口 | 语义级摘要仍需要 Agent/架构负责人深读关键入口后写入人工维护区 |
| BMAD Planning 前导 | 4.75 | `bmad_method_gate`、task card、phase0 pass 已落地；Entry Gate 通过后自动把 BMAD planning 同步到 `CONTEXT.md` | BMAD 技能执行真实性仍依赖留痕与人工审查 |
| 任务契约 | 4.8 | 7 字段契约 + `task_contract_check` | 契约质量仍需更细粒度语义校验 |
| DAG 同步 | 4.7 | `dag_sync_check` 已进入 `check.sh` | 跨任务并发状态机尚未完全自动化 |
| 结构守门 | 4.5 | allowlist + `structure_guard` + `plan_sync` | 缺 import-linter / 架构依赖扫描 |
| QA 分离 | 4.6 | `qa_sign_off` + `subagent-pr-gate` + `qa_evidence_check` | 仍需更丰富的报告语义校验 |
| 知识沉淀 | 4.7 | knowledge/evidence 已拆分，CONTEXT/LESSONS/GROWTH 已落地；全新项目 BMAD Planning 规划、已有项目 Intake、任务 Growth 三条知识入口已区分 | 需要团队例行 review，把候选转成规则 |
| Work Item 协同 | 4.72 | Teambition provider 已联调；飞书已补离线契约测试、token 诊断、只读任务校验、显式 smoke 创建入口和配置任务清单后的 list-mine；Jira 基础 adapter 已接入；`draft-spec → 确认 → sync-spec --assignee` 与 `bmad-work-item-v1` 同步契约已落地 | 状态回写、Webhook 与更复杂租户搜索仍需增强 |
| CI/MR 闭环 | 4.58 | validate/doc/check/MR gate 已有；2026-08-13 GitHub live audit 确认三个 workflow 已被识别且 Actions 已启用 | 尚无真实 workflow run，`main` 未配置保护规则，仍为 shadow |
| 运行时安全 | 4.1 | 默认 controlled argv 后端已禁止 shell 控制符；Docker acceptance 已实机验证产品根挂载、默认断网和 argv 边界 | Firecracker/远程隔离执行器仍未接入 |
| 前端/浏览器 QA | 4.25 | `browser_qa` 不再模拟成功，默认写入产品侧 runs，支持 setup/check、CI Playwright 镜像、trace、截图和 selector/script click-test | 仍需更多交互脚本库和 report 语义规范 |
| 业务实战覆盖 | 3.8 | 框架已准备 | 业务服务首包还未大规模压测 |

## 4. 已经很强的部分

1. **方法论完整**：不是单纯 Coding Harness，而是覆盖产品、协同、开发、验收、知识沉淀的研发系统。
2. **机械门禁扎实**：`validate_harness`、`doc-gardening`、`structure_guard`、`plan_sync`、`task_contract`、`dag_sync`、`check.sh` 已经形成可执行链。
3. **角色边界清楚**：Lead 不写业务代码，QA 不写业务逻辑，执行 Agent 不自签。
4. **Flow-X 精华已吸收**：CONTEXT、LESSONS、PROGRESS、SUMMARY、TEST、REVIEW、GROWTH 已有目录和脚本支撑。
5. **全新项目知识入口已经打通**：BMAD Planning 的产品目标、产品蓝图、架构/方案、验收和任务边界会通过 `harness_knowledge.sh sync-planning` 进入 `CONTEXT.md` 受管区块。
6. **已有项目接入有了正式入口**：`harness_intake.sh` 能把已有代码、关键入口、文档、测试和技术栈整理成可 review 候选，不会直接污染长期知识。
7. **组织协作基础好**：Work Item 抽象、Teambition/飞书/Jira provider、任务草稿确认、多任务 workspace、MR 推断已经具备扩展基础。
8. **harness-engineering / product workspace 边界清楚**：harness-engineering 可放任意位置，产品产物沉淀在产品自己的 `harness-workspace/`。
9. **参考项目能力已形成组合优势**：BMAD 负责产品规划，OpenAI Harness 提供渐进披露与机械反馈，Flow-X 提供知识语义，Superpowers 提供 TDD 纪律，GStack 提供真实环境验证，planning level 提供规划分级；精简升级应保留这些能力而不是保留其固定步骤。

## 5. 当前最需要优化的地方

### P0：马上做，决定生产可信度

| 项 | 为什么重要 | 建议落点 |
|----|------------|----------|
| 结果状态与成本遥测 | 没有原子任务状态、Token、上下文和 gate 成本就无法验证优化 | `runs/tasks/<id>/result.json` 作为本地物化快照；不新增独立成本报告 |
| CI 接受权威 | 本地文件可被修改，不能直接证明某个 commit 合规 | CI 用同一判定器生成绑定 commit SHA 与 policy digest 的 required check |
| 三级接入保障 | 任务执行严格度不应迫使轻量用户先建设平台基础设施 | `local` 默认可用、`guarded` 作为轻量推广目标、`enforced` 用于不可绕过准入；三者不降低 `lite/standard/strict` 要求 |
| Enforced 接入验收 | 安装 workspace 不代表受保护接受点、发布依赖和 provider 完成态已防绕过 | 未完成端到端接线的产品保持 local/guarded；通过平台或等价权威接受点 live probe 后才进入 enforced |
| 能力迁移验收 | 统一入口可能在降本时误删已有优秀机制 | 为参考能力矩阵建立验收测试，证明每项能力被内部承载、按需触发或有明确替代 |
| 精简执行门面 | 防漏不能依赖 Agent 记忆，复杂命令链本身又增加遗漏和 Token | `harness start/status/finish` 封装现有能力，由 local 验证逐步进入 guarded 并替代手工链 |
| Execution tier 贯穿执行 | `start` 时还没有最终 diff，无法一次判定风险 | 初始 tier + `finish` 按实际 diff 重算；有效 tier 只升不降 |
| Fingerprint 去重 | QA 和最终 check 会重复执行部分 gate，错误复用又会接受陈旧结果 | 完整 fingerprint；只缓存确定性机械 gate，人工/生产证据绑定 commit |
| Legacy workspace 兼容 | 已接入产品不能全量迁移，也不能伪造不存在的旧 baseline | 原位兼容；缺 baseline 的活动任务建立 migration baseline 并至少完整跑 `standard` |
| 扩展业务质量覆盖 | 当前已有产品侧 quality commands，但覆盖仍偏基础 | 在首个 Python 服务接入 Ruff、pytest、import-linter |
| 首个业务服务非 SKIP 全链压测 | 当前很多门禁在 Harness 自维护时可能 SKIP | 用真实业务 MR 跑通 structure/plan/task/dag/QA 全链 |
| Docker 后端试运行 | 已有 Docker backend，但还未被真实业务测试矩阵长期压测 | 在首个 Python/Node 服务中分别用 Docker 镜像跑 lint/test |

### P1：短期做，提升泛化和自动化

| 项 | 为什么重要 | 建议落点 |
|----|------------|----------|
| Work Item 状态回写串联 | 本地验证通过不等于已经合并或发布 | 本地 `finish` 最多同步 ready/review；CI 合并或发布成功后再 close |
| Intake review 例行化 | 已有项目接入报告生成了，不 review 就不会变成知识 | 首次接入后固定 review `intake-reports/`，迁入 CONTEXT/LESSONS/REFERENCE_SYSTEMS/架构文档 |
| Growth review 例行化 | 有候选但不 review/apply 就不会真正成长 | 仅在发现长期候选时生成 Growth，周期性清理候选积压，不按每任务空跑 |
| 飞书/Jira 深度验证 | 证明 Team Product Harness 不是 Teambition 专用 | 基础 provider 已接入；下一步用真实租户验证创建、查询、状态流转、权限错误 |
| Work Item 状态语义映射 | 让 `bmad-work-item-v1` 不只创建任务，也能推动看板状态 | 按 provider 补齐 Planning Gate / QA / Done 的真实状态流转 |

### P2：中期做，进入更强生产级

| 项 | 为什么重要 | 建议落点 |
|----|------------|----------|
| Firecracker/远程隔离执行器 | subject-bound HTTPS remote backend 协议已接入同一入口；强多租户隔离仍取决于 executor 实机 | 部署 Firecracker/remote executor，并验证无网络、workspace/subject 隔离与超时回收 |
| Playwright 交互库深化 | allowlist scenario、逐步 decision、截图与 trace 已落地 | 在真实前端 MR 中持续扩充领域场景，但不得退回任意脚本作为默认入口 |
| 开源发布预检 | harness-engineering 会管理多个产品，发布时不能带本机台账、绝对路径和密钥 | `release_preflight.sh` 已有；发布前必须执行并清理本机状态文件 |
| Agent Review 深化 | diff-bound 四视角 checklist 与独立 reviewer receipt 已落地 | 在产品 CI 配置 reviewer runner 并开启 `HARNESS_AGENT_REVIEW_REQUIRED=true` |
| Scope-change / hotfix workflow | 已复用 `start --kind ... --reason ...`，且 kind floor 不能降级 | 在真实任务验证 scope-change standard 与 hotfix strict 的误阻断率 |

## 6. 建议下一步执行顺序

1. **先建立基线与不变式验收测试**：量化当前 Token、上下文、耗时和产物数量，并覆盖绕过、陈旧缓存和 execution tier 误降级。
2. **再落地 assurance schema**：在现有兼容字段旁增加 `local|guarded|enforced`，不改变 execution tier 或伪造平台保障。
3. **实现轻量 guarded 接入**：由初始化安装版本化 guards/CI，日常仍只用 `start/status/finish`，显式显示可绕过边界。
4. **用真实业务变更灰度**：在多个试点产品中选择不同风险任务，验证误阻断率、迁移和 provider 状态流转。
5. **按需接入 enforced**：只有组织需要不可绕过准入时才配置 protected authority、发布依赖和 provider 终态；随后再深化强隔离和 Playwright。

## 7. 风险判断

| 风险 | 当前等级 | 缓解方式 |
|------|----------|----------|
| 只写文档，不被门禁执行 | 中 | 所有新增规范尽量接入 `check.sh` / CI |
| Agent 绕过 TEST/REVIEW 报告 | 低-中 | `qa_evidence_check` 已接入；继续增强报告语义校验 |
| 受控命令入口误认为完整安全边界 | 中 | 文档明确默认 controlled 只防误用；Docker 可用但强多租户隔离仍需 Firecracker/远程执行器 |
| 多 provider 泛化不足 | 低-中 | 第二 provider 验证前，不宣称完全平台无关 |
| Intake/Growth 变成噪音 | 中 | 接入报告和成长报告只生成候选，必须人工或 Agent review 后迁入知识或规则 |

## 8. 最终判断

Team Product R&D Harness 当前已经达到 **可试运行、可迭代、可审计** 的成熟度。它比普通 AI Coding 流程强很多，因为它已经把「做什么、谁做、改哪里、怎么验、谁签、如何沉淀」变成了可执行系统。

接下来最值得投入的不是再增加更多文档，而是把 **更完整业务测试、依赖边界、Playwright 工程化、真沙箱、provider 状态回写** 接到机械门禁里。做到这些后，它才会从「优秀的产品研发 Harness 骨架」进入「可开源推广的生产级 Harness」。
