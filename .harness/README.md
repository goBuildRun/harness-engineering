# Harness Runtime Internals

本目录是 harness-engineering 的运行时内部区，保存可复用的 Agent 配置、规则、脚本、模板、工作流和 Work Item provider。

面向使用者的入口不是本文件：

- Agent 地图：[../AGENTS.md](../AGENTS.md)
- 架构入口：[../ARCHITECTURE.md](../ARCHITECTURE.md)
- 使用与兼容参考：[../docs/USAGE.md](../docs/USAGE.md)
- harness-engineering / product workspace 分层：[../docs/Harness_Product_Workspace.md](../docs/Harness_Product_Workspace.md)

## Directory Map

| 目录/文件 | 责任 |
|-----------|------|
| `agents/` | planning、lead、backend、frontend、qa、gc 角色定义 |
| `rules/` | 结构、QA、BMAD、GC、文档边界等可引用规则 |
| `profiles/` | 产品 profile 级 allowlist 与领域规则 |
| `scripts/` | 机械门禁、workspace 解析、Work Item、QA、接入扫描、成长扫描 |
| `templates/` | 产品规格、执行计划、任务 DAG、证据报告模板 |
| `workflows/` | L1/L2/L3 工作流定义 |
| `work-items/` | Work Item provider 配置与说明 |
| `products/` | 本机产品台账与 active product pointer；源码只提交 example |
| `harness-manifest.yaml` | Harness 自身必备文件清单 |

## Stable Interfaces

外部文档应尽量引用稳定概念，不直接展开 runtime 内部路径。确需执行命令时，优先引用 [../docs/USAGE.md](../docs/USAGE.md)。

当前稳定接口：

> 下列接口是当前实现期稳定能力，不是未来公共命令清单。统一门面落地后，除初始化、诊断和 runtime 开发外，它们由 `start/status/finish` 内部调用；能力和 JSON 判定语义继续保留。

统一入口：`.harness/scripts/harness start|status|finish`。workspace 审计、活动任务迁移和 commit 判定作为内部管理能力存在，不扩展日常公共操作面。受控 Git 接收点或发布入口未验证前只能显示 `local` 或 `guarded`。

| 接口 | 用途 |
|------|------|
| `harness_init.sh` | 登记、切换和列出产品 |
| `pretty.sh` / `json_pretty.py` | 将 harness JSON 输出格式化给人阅读 |
| `harness_knowledge.sh` | 初始化产品侧知识与证据目录 |
| `harness_intake.sh` | 扫描已有项目代码/文档并生成接入候选 |
| `planning_gate.sh` | 产品前导交接门禁 |
| `agent_start.sh` | 启动执行闭环并首次保存任务 worktree baseline |
| `task_contract_check.sh` | 校验 7 字段任务契约 |
| `dag_sync_check.sh` | 校验 DAG 与实施方案一致 |
| `structure_guard.sh` | 校验写入路径和 diff |
| `plan_sync_check.sh` | 校验相对任务 baseline 的 tracked/untracked 变更与实施方案路径表一致 |
| `diff_integrity_check.py` | 对任务 baseline 后的 tracked/untracked 变更执行 Git 空白完整性检查 |
| `qa_sign_off.sh` | QA 独立签章 |
| `qa_evidence_check.sh` | QA 签章 + TEST/REVIEW 报告门禁 |
| `quality_commands.sh` | 执行产品侧 `quality.commands.lint/test` |
| `run_in_sandbox.sh` | 受控测试/构建入口；默认 controlled argv，可切 Docker backend |
| `browser_qa.py` | 真实 Playwright 浏览器 QA 入口；未安装时 block |
| `browser_qa_setup.sh` | 检查或安装 Playwright Chromium |
| `harness_growth.sh` | 捕捉、扫描、review 并应用可审阅成长候选 |
| `release_preflight.sh` | 开源发布前检查本机台账、绝对路径和疑似密钥泄漏 |
| `check.sh` | 发布前完整机械门禁 |

Work Item 同步契约由 [../docs/BMAD_Work_Item_Contract.md](../docs/BMAD_Work_Item_Contract.md) 维护，runtime 脚本只负责执行该契约。

## Ownership Rules

- runtime 内部规则和模板属于 harness-engineering。
- 产品规格、任务包、运行状态、QA 证据和产品知识属于产品仓库的 `harness-workspace/`。
- `workspace_paths.py` 和 `planning_gate_pass.json` 是新语义入口；`phase0` 字段与 `phase0_pass.json` 只作为历史兼容层保留。
- 新增脚本必须能输出 JSON `decision`，并优先接入 `check.sh` 或 CI。
