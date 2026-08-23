# 技术债追踪

当前执行顺序和验收标准以 [Git-native Harness Upgrade](../plans/active/git-native-harness-upgrade.md) 为准；本表只保留债务状态，不作为第二套 backlog。

| ID | 描述 | 严重度 | 状态 |
|----|------|--------|------|
| TD-001 | 沙箱接 Firecracker/远程隔离执行器 | 中 | partially done：Docker backend（默认无网络）、可选实机 acceptance 和 subject-bound HTTPS remote executor 协议已接入；Firecracker/remote executor 实机部署和隔离验证 open |
| TD-002 | browser_qa 复杂交互脚本库与 report 语义规范 | — | **已偿还** 2026-08-11 · setup/CI、trace、截图、console/network audit 与最多 50 步的 allowlist scenario JSON 已落地；逐步 decision 写入统一 browser report，任意 JS 仅保留兼容入口 |
| TD-003 | import-linter / Ruff 分层 import CI | — | **已偿还** 2026-08-11 · `quality.commands` 支持安全 Ruff argv 与 AST `python-import-boundaries` builtin，边界由产品 profile 配置 |
| TD-004 | Ralph agent-review 脚本化（非 echo） | — | **已偿还** 2026-06-21 · CI `ai_code_review` 调用 `mr_ready.sh` |
| TD-005 | Teambition API 联调（钉钉 Teambition provider） | — | **已偿还** 2026-06-18 |
| TD-006 | `status_map` 接线 + Teambition 看板状态真回写 | — | **已偿还** 2026-08-11 · stage/taskflowstatus 双模式、状态别名映射及离线 API 契约测试已覆盖 |
| TD-007 | `noop` sync-spec 生成合规本地 ID 或显式拒绝 L2/L3 | — | **已偿还** 2026-06-20 · noop 生成语义化本地 ID |
| TD-008 | BMAD Method 产出机械校验（front matter + 工作流表） | — | **已偿还** 2026-06-18 · `bmad_method_gate.py` |
| TD-009 | product-spec 验收 checkbox 机械校验 | — | **已偿还** 2026-06-18 · 合入 TD-008 |
| TD-010 | L3 Solutioning 强制 `bmad-create-architecture` | — | **已偿还** 2026-06-18 · 合入 TD-008 |
| TD-011 | CI 硬拦 TEST/REVIEW 报告与 QA 凭证 | 中 | done：`qa_evidence_check.sh` 已接入 `check.sh` |
| TD-012 | 飞书/Jira provider 真实租户深度验证、Webhook 与状态流转 | 中 | partially done：2026-08-12 飞书真实租户已验证 app/editor 清单授权、token、tasklist、smoke 创建、pull、负责人过滤与开放态读取；Jira、Webhook 和 Git-native 完成态流转仍 open |
| TD-013 | Flow-X 式知识沉淀与成长候选报告 | — | **已偿还** 2026-06-20 · `harness_knowledge.py` + `harness_growth.py` |
| TD-014 | `tasks-dag.md` 与 `03-实施方案.md` 任务 ID 同步 | — | **已偿还** 2026-06-20 · `dag_sync_check.py` |
| TD-015 | 7 字段任务契约机械校验 | — | **已偿还** 2026-06-20 · `task_contract_check.py` |
| TD-016 | 产品侧 `quality.commands` 接入 CI/check | — | **已偿还** 2026-06-21 · `quality_commands.py` + Harness allowlist/危险 env 拦截 |
| TD-017 | 开源发布前防泄漏预检 | — | **已偿还** 2026-06-21 · `release_preflight.sh` |
| TD-018 | 实现统一 `plan/start/status/finish` 门面、原子 `result.json` 与实现/Harness 分项成本遥测；内部复用现有 gate，不新增并行流程 | 中 | partially done：统一入口、结构化 runner、绑定 usage receipt、GC 模型身份和 rollout 汇总已落地；低风险 `lite` 可由 `start` 生成最小绑定；仍需第三方 provider 真实 usage response 与至少 5 个 lite 样本 |
| TD-019 | 以 Git object/ref attestation 作为唯一 commit 接受真相；`finish` 只进入 ready/review，受控接受或发布后才能关闭 Work Item | — | **已偿还** 2026-08-13 · canonical result/attestation/GC refs、receive gate 重跑、SSH acceptance receipt、provider 消费与 bare Git push 验收已闭合 |
| TD-020 | 建立能力迁移验收：逐项证明 BMAD、TDD、QA、安全、知识、浏览器、协同能力已被统一门面承载或按需触发 | — | **已偿还** 2026-08-13 · [能力迁移映射](../design/capability-migration.md) 与 `capability_contract.py` 覆盖 BMAD/TDD/QA/知识/浏览器/GC/provider/Git authority carrier |
| TD-021 | 建立 Git-native `local → guarded → enforced` 验收与绕过测试 | 高 | in progress：guarded 已覆盖全部新增 commit、同步 attestation refs 并保持 commit 后状态；enforced 仍须证明受控 `pre-receive` 或发布入口重跑 gates 并签名 |
| TD-022 | 拆分超过 400 行的 `work_item.py`，保持 provider CLI、诊断和同步契约不变 | — | **已偿还** 2026-08-11 · provider 网络诊断与能力矩阵迁入 `work_item_diagnostics.py`；核心 CLI 降至 400 行内 |
| TD-023 | 拆分超过 1000 行的 brownfield intake，隔离扫描策略、信号采集、报告渲染与知识沉淀 | — | **已偿还** 2026-08-11 · `harness_intake_{policy,scan,review}.py` 按职责拆分，入口和各模块均不超过 400 行并保留 CLI/import 契约 |
| TD-024 | 拆分超过 1700 行的 Work Item provider 单体，隔离 facade、BMAD contract、provider 与 transport/payload helper | — | **已偿还** 2026-08-11 · Teambition、Feishu、Jira adapter 独立，旧 `work_item_providers` export 保持兼容，所有 Work Item 模块不超过 400 行 |
| TD-025 | 清理其余 runtime 超大 Python 模块并机械防止重新增长 | — | **已偿还** 2026-08-11 · growth review、knowledge parse、doc policy、workspace config、product registry、BMAD init 已按职责拆分；全局测试强制 `.harness/scripts/*.py` 单文件不超过 400 行 |
| TD-026 | Scope-change / hotfix 使用显式风险升级且不能绕过共同不变式 | — | **已偿还** 2026-08-11 · 复用 `harness start --kind ... --reason ...`；scope-change 最低 standard、hotfix 强制 strict，kind floor 有 CLI 与分类器测试 |
| TD-027 | Agent Review 生成结构化 checklist 并与 MR diff 绑定 | — | **已偿还** 2026-08-11 · `mr_ready` 输出四视角 checklist；required 模式校验 runs 内 subject-bound 独立 reviewer receipt，旧 diff 或实现角色自签均阻断 |
| TD-028 | 将部署保障从二态兼容字段演进为 `local|guarded|enforced`，并实现轻量 guarded guards | 中 | partially done：结构化 assurance、历史兼容、repo-local hooks 与审计已落地；临时仓库已跑通 finish → pre-commit → commit → pre-push commit check，并覆盖含空格安装路径；待真实团队试运行、显式绕过审计和更多平台验证 |
| TD-029 | 实现 Git-native attestation 对象、refs、verifier 与 pre-receive 接受端 | 高 | in progress：对象/ref/verifier 已实现；接受端必须自行重跑 commit gates，不能信任客户端自签 attestation |

## 已偿还（Harness 强化）

| 项 | 说明 |
|----|------|
| BMAD Planning 凭证链 | `planning_gate_pass.json` + agent_start/check 强制 |
| PRODUCT_ROOT | `product_root.sh` + session/env/cwd 产品上下文；active product pointer 与 `.product-root` 仅作兜底 |
| plan_sync | `plan_sync_check.sh` |
| harness-manifest | 单一 validate 清单 |
| CI 硬门禁 | structure + MR gates |
| Work Item 抽象层 + 多 provider 基础能力 | `work_item.sh` + Teambition 实测 + 飞书/Jira 基础 adapter |
| 文档对齐审计 | docs/ 与 Harness 实态、跨文档一致性（2026-06-18） |
| Cursor rules | `.cursor/rules/harness-engineering.mdc` |
| 双分层对照 | ARCHITECTURE ↔ PACKAGE |
| Flow-X 知识层 | CONTEXT/LESSONS/PROGRESS/SUMMARY/TEST/REVIEW/GROWTH |
| 任务契约与 DAG 同步 | `task_contract_check.sh` + `dag_sync_check.sh` |
| 开源发布预检 | `release_preflight.sh` 阻断本机台账、绝对路径和疑似密钥 |
| MR 前产品化预检 | `mr_ready.sh` 在产品根检查 git 变更、runs 防漏与 `check.sh` 全链 |
| Docker 沙箱后端 | `run_in_sandbox.sh` 通过 `HARNESS_SANDBOX_BACKEND=docker` 切换 |
| Browser QA setup/CI | `browser_qa_setup.sh` + 可选 `HARNESS_BROWSER_QA_URL` CI job |
| 飞书 provider 契约测试 | `tests/test_feishu_provider.py` 覆盖 token/create/pull/update skip |
