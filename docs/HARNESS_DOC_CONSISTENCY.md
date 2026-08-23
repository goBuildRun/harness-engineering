# Team Product R&D Harness 文档一致性审查报告

> 初始审查日期：2026-06-20  
> 最近复核：2026-08-23
> 基准实态：Team Product R&D Harness 已从 embedded harness 调整为 **harness-engineering `harness-engineering/` + product-owned `harness-workspace/`**。

## 1. 审查结论

文档权威边界：

1. [AGENTS.md](../AGENTS.md) 只维护 Agent 最小导航和不可绕过边界。
2. [README.md](../README.md) 只维护开源入口与最小快速开始。
3. [ARCHITECTURE.md](../ARCHITECTURE.md)、[Team_Product_Harness.md](./Team_Product_Harness.md) 和 [Harness_Product_Workspace.md](./Harness_Product_Workspace.md) 分别维护系统架构、通用模型和 workspace 分层。
4. [USAGE.md](./USAGE.md) 维护三项主动作（`plan/start/finish`）和只读 `status`，并明确标注当前可执行的过渡期命令。
5. [design-docs/lean-enforcement.md](./design-docs/lean-enforcement.md) 维护精简强制执行目标。
6. [Harness_Workflow.md](./Harness_Workflow.md) 只解释流程结构。
7. [Harness_成熟度评估.md](./Harness_成熟度评估.md) 维护当前评分、缺口和优化优先级。
8. [Harness_全景手册.md](./Harness_全景手册.md) 只保留设计背景与历史全景。

同一命令、provider 配置、迁移规则或成熟度结论不得在上述文档中并行维护全文。

## 1.1 当前实现基准

以下值直接对应 runtime；变更代码时必须同步更新本节和 [USAGE.md](./USAGE.md)，否则文档审查失败：

| 能力 | 当前基准 |
|------|----------|
| 主动作 | `plan`、`start`、`finish`；`status` 只读；低风险 `lite` 可由 `start` 生成最小 `task.json` |
| Story 阶段 | `takeover` 2m → `planning` 3m → `implementation_test` 10m → `independent_qa` 7m → `deploy_provider` 5m → `finalize` 3m |
| 重试与超时 | Story 总上限 30m；阶段重试上限 2；同输入 `finish` 上限 2；gate 默认 120s，质量命令按自身上限且受阶段剩余预算约束 |
| 并行与缓存 | 只读 gate 可 bounded parallel；仅确定性 gate 按真实输入 fingerprint 缓存；QA、GC Agent、浏览器、部署、Provider、回滚不缓存 |
| 保障语义 | `lite/standard/strict` 是执行深度；`local/guarded/enforced` 是接受保障；`guarded` 始终 `bypassable: true`，未知遥测保持 `unknown/pending` |

| 维度 | 结果 | 说明 |
|------|------|------|
| harness-engineering / product workspace 分离 | ✅ 已对齐 | harness-engineering 保存运行时、脚本、模板、示例与本机台账；产品仓库保存产物、知识、证据 |
| 位置无关启动 | ✅ 已对齐 | `harness_init.sh init/use/list` 指定产品根，不要求同级目录 |
| 产品台账 | ✅ 已对齐 | `.harness/products/registry.yaml` + session context + `active-product.json` fallback 为本机状态，开源源码只提交 example |
| 产品配置 | ✅ 已对齐 | `<product-root>/harness-workspace/project.yaml` 是产品 workspace 真相源 |
| BMAD Planning 命名 | ✅ 已对齐 | 历史 `Phase 0` 已重构为 BMAD Planning；产品目录使用 `planning/` |
| 运行状态 | ✅ 已对齐 | `harness-workspace/runs/` |
| 长期知识 | ✅ 已对齐 | `harness-workspace/knowledge/CONTEXT.md`、`LESSONS.md`、`REFERENCE_SYSTEMS.md`；全新项目由 BMAD planning 同步，已有项目由 intake review 沉淀 |
| 证据与成长 | ✅ 已对齐 | `harness-workspace/evidence/{intake-reports,summaries,progress,test-reports,review-reports,growth-reports}` |
| 精简强制执行目标 | ✅ 已对齐 | `design-docs/lean-enforcement.md` 是唯一目标设计；当前可运行命令仍以 `USAGE.md` 为准 |
| 三级接入保障 | ✅ 设计已对齐 | `local|guarded|enforced` 描述部署防绕过强度，与 `lite|standard|strict` 任务深度正交；旧 `shadow|enforced` 字段暂作兼容 |
| 已有项目接入 | ✅ 已对齐 | `docs/Brownfield_Intake.md` + `harness_intake.sh` + 产品侧 `intake-reports/` |
| Flow-X 吸收 | ✅ 已对齐 | 7 字段任务契约、DAG 同步、知识沉淀、自我成长候选报告与 apply-review |
| 兼容边界 | ✅ 已记录 | `workspace_paths.py` / `planning_gate_pass.json` 是新语义入口；`phase0_paths.py` / `phase0_pass.json` 只作为历史兼容名 |
| 精简升级数据策略 | ✅ 已对齐 | 历史 workspace 原位可读；新任务写新格式；仅活动或重开任务补最小 `result.json`，外部 Work Item ID 不重建 |
| 参考能力保留 | ✅ 已对齐 | 保留 BMAD、OpenAI Harness、Flow-X、Superpowers、GStack、legacy planning、Ralph 的有效能力和产物语义；不保留固定命令数与仪式 |
| 参考能力路由 | ✅ 已对齐 | `docs/references/index.md` 将来源、保留能力和内部权威说明一一映射，避免静默删除或重复维护外部流程 |
| 成本可观测性 | ✅ 目标已对齐 | `result.json.cost` 区分 implementation 与 harness，记录 Token、上下文、Agent 调用、gate 耗时、重跑和完整性；未知值不得记为 0 |
| Agent 分层规则 | ✅ 已对齐 | Agent 规则只强制共同不变式；独立 QA、浏览器 QA、GC、TEST/REVIEW 和人工 Gate 按当前兼容工序或 execution tier 触发 |
| Profile 隔离 | ✅ 已对齐 | generic 默认不含产品目录或领域约束；未知显式 profile fail closed；产品规则只从对应 profile 加载 |
| 公开仓库边界 | ✅ 已机械化 | `test_public_repository.py` 检查路径、内容、静态字符串拼接、本机状态和绝对路径；禁止通过拆字符串隐藏产品标识 |
| 任务身份与层级 | ✅ 已对齐 | 任务模板区分 Harness Task ID、Work Item ID、planning level 与 execution tier；`任务编号`、`风险等级` 仅作当前脚本兼容镜像 |
| 入口文档精简 | ✅ 已对齐 | README 与全景手册已收敛为入口和系统解释；命令、配置、成熟度和技术债继续由各自权威文档维护 |
| 文档链接与锚点 | ✅ 已机械化 | `doc_gardening_check.py` 同时校验本地 Markdown 路径和章节锚点；产品外部引用必须显式标注，不伪装为 harness 内部相对链接 |

## 2. 单一真相源

| 真相源 | 责任 |
|--------|------|
| `.harness/products/registry.yaml` | harness-engineering 在本机管理哪些产品、产品根在哪里；gitignored |
| `HARNESS_PRODUCT_ID` / `HARNESS_PRODUCT_ROOT` | 多产品并行时当前 session 作用于哪个产品 |
| `.harness/products/active-product.json` | 未显式指定产品时的默认兜底；gitignored |
| `.harness/products/*.example.*` | 开源源码中的产品台账格式示例 |
| `<product-root>/harness-workspace/project.yaml` | 产品 workspace 结构、BMAD 输出路径、知识与证据目录 |
| `.harness/config.yaml` | Harness 默认值与历史兼容 |
| `.harness/harness-manifest.yaml` | Harness 自身必备文件清单 |

## 3. 权威路径

| 类型 | 路径 |
|------|------|
| 产品规格 | `harness-workspace/planning/product-specs/` |
| 执行计划 | `harness-workspace/planning/exec-plans/active/`、`completed/` |
| 任务包 | `harness-workspace/planning/tasks/` |
| 运行状态 | `harness-workspace/runs/` |
| 长期知识 | `harness-workspace/knowledge/` |
| 接入/测试/审查/成长证据 | `harness-workspace/evidence/` |
| Harness 模板 | `harness-engineering/.harness/templates/`、`harness-engineering/tasks/_templates/` |

旧路径 `harness-workspace/phase0/`、裸 `phase0/`、`docs/product-specs/` 只允许在历史兼容说明中出现。

## 4. 核心入口

命令契约只有一套：`plan` 生成规划与 task-scoped 凭证，`start` 接管并记录基线，`status` 只读观察，`finish` 按 tier 执行/复用门禁并生成结果。`standard/strict` 必须有规划凭证；低风险 `lite` 可由 `start` 生成最小 `task.json`。`confirm`、`stage`、workspace/migration 和旧脚本属于兼容或管理能力，不形成第二个完成态。

| 文档 | 职责 |
|------|------|
| [README.md](../README.md) | GitHub 中文开源入口 |
| [Harness_Product_Workspace.md](./Harness_Product_Workspace.md) | harness-engineering + product workspace 内部设计解读 |
| [Team_Product_Harness.md](./Team_Product_Harness.md) | 通用产品研发 Harness 模型 |
| [Brownfield_Intake.md](./Brownfield_Intake.md) | 已有项目接入的定位、操作、报告和 review 规则 |
| [BMAD_Work_Item_Contract.md](./BMAD_Work_Item_Contract.md) | BMAD Planning 到 Work Item 的协同契约 |
| [USAGE.md](./USAGE.md) | canonical 使用模型与过渡期兼容命令参考 |
| [references/index.md](./references/index.md) | 外部参考能力的路由与内部权威说明 |
| [BMAD_Prelude.md](./BMAD_Prelude.md) | BMAD Planning / BMAD Method 专章 |
| [Harness_成熟度评估.md](./Harness_成熟度评估.md) | 当前成熟度与优化优先级 |
| [design-docs/lean-enforcement.md](./design-docs/lean-enforcement.md) | 精简强制执行目标与演进约束 |
| [AGENTS.md](../AGENTS.md) | Agent 导航地图 |

## 5. 机械一致性不变式

| 不变式 | 机械检查 |
|--------|----------|
| Harness 必备文件不会静默丢失 | `.harness/harness-manifest.yaml` + `validate_harness.sh` |
| Agent 入口保持短小且链接到权威文档 | `agents_max_lines` + `agents_must_link` |
| generic 与产品 profile 不串味 | `test_business_paths.py` + `structure_check.py` |
| 文档相对链接和章节锚点有效 | `doc-gardening.sh` + `test_doc_gardening.py` |
| 旧目录名只出现在兼容说明中 | `doc_gardening_check.py` 的陈旧引用规则 |
| Harness 自检不修改 active product 或产品 workspace | 状态型 Growth smoke 在临时产品根执行；`validate_harness.sh` 不调用 `harness_init use` |

## 6. 当前缺口与状态

| ID | 项 | 说明 |
|----|----|------|
| HDC-003 | provider 深度验证 | 飞书/Jira 基础 adapter 已接入；需要真实租户验证状态流转、Webhook、权限错误 |
| HDC-004 | 强沙箱 | `run_in_sandbox.sh` 已支持 controlled 与 Docker backend；Firecracker/远程隔离执行器仍是后续项 |
| HDC-005 | 精简执行门面与结果 schema | `plan/start/status/finish`、原子 `result.json`、tier-aware gate 与 Git-native attestation 已实现；低风险 `lite` 可由 `start` 生成最小绑定；`shadow|enforced` 只作为历史兼容字段 |
| HDC-006 | 成本与缓存 | implementation/harness 分项 schema、绑定 usage receipt、GC provider/model/calls/context/duration、fingerprint 和只读 rollout 汇总已落地；真实第三方 usage response 联调仍需 endpoint/model/key，未知 baseline 或样本不足返回 insufficient_data |
| HDC-007 | Legacy workspace 兼容 | **已完成**：`workspace audit` 只读分类且有无副作用测试；`migrate-task` 机械拒绝已完成 legacy、缺凭证和不存在任务，仅活动凭证任务可逐项迁移，已有结果只做身份修复 |
| HDC-010 | 三级保障接入验收 | **已完成**：local/guarded、bare Git receive 阻断、acceptance receipt、provider 消费、authority audit 与独立 SSH GC receipt 已覆盖；产品实例仍须对自己的 authority 单独 install/audit |
| HDC-011 | 能力迁移验收 | `capability_contract.py` 已将 BMAD/TDD/QA/知识/浏览器/GC/协同/Git 接受能力接入 `validate_harness`；真实 provider 生命周期仍由 HDC-003/HDC-010 跟踪 |

## 7. 已解决或保留兼容

| ID | 处理结果 |
|----|----------|
| HDC-001 | `workspace_paths.py` 已成为语义入口；`phase0_paths.py` 和旧凭证名只保留兼容读取。 |
| HDC-002 | `qa_evidence_check.sh` 已接入 `check.sh`；更深报告语义校验作为质量增强，不再视为流程缺口。 |
| HDC-008 | 仓库 `.DS_Store` 已从工作树移出并保存在 `/tmp/harness-engineering.DS_Store.backup`，文档园艺恢复可执行。 |
| HDC-009 | 历史产品文档中的架构引用已改为显式 `$PRODUCT_ROOT/...` 外部引用，不再参与 harness 内部相对链接判定。 |
