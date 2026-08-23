# 质量门禁与品味不变式

> **命令级使用方式**：[getting-started/cli.md](../getting-started/cli.md) 第 4–8 节；目标流程是三项主动作 `plan/start/finish`，`status` 只读，见第 9 节。
> **精简执行目标**：[design/lean-enforcement.md](../design/lean-enforcement.md)。质量门禁必须严格，但门禁组合、证据数量和执行成本按实际风险分层。

## 质量执行原则

- 本地 `finish` 结果只允许进入提交和 review；没有绑定目标 commit/tree/task/policy/result 的有效 Git attestation，任务不能被正式接收、发布或关闭外部 Work Item。
- `lite`、`standard`、`strict` 都执行共同不变式，但不共享同一条最长命令链。
- 已由相同 fingerprint 验证的确定性机械 gate 应复用结果；人工签署、生产副作用和 strict 部署/回滚证据不得跨 commit 复用。
- `result.json` 是本地任务执行快照；commit 准入以 Git object/ref 中的 canonical attestation 为准。Markdown 证据按风险和人工阅读需要生成。
- 精简只能删除重复成本，不能取消范围检查、风险匹配验证或必要人工 Gate。
- execution tier 在 `start` 时只是初值，`finish` 必须按实际 diff 重算，且任务生命周期内只能升级。
- commit verifier 必须解析出唯一任务绑定：新 `lite` 使用最小 `task.json`，`standard` / `strict` 使用现有任务包；不得按“最近任务”猜测。
- BMAD 验收语义、Superpowers TDD、GStack 真实环境验证、QA 分离和 GC 继续作为可选或强制 gate 保留；execution tier 决定何时运行，不要求使用者手工串联。

## 机械门禁（本地 + 接受端）

下列是当前 runtime 的门禁能力索引，不是每个任务固定执行的总清单。目标 `finish` 按 execution tier、实际 diff 和缓存 fingerprint 选择必要检查；共同不变式始终强制。

- `bash .harness/scripts/validate_harness.sh` — Harness 结构完整
- `bash .harness/scripts/harness_intake.sh status` — 已有项目接入信号检查
- `bash .harness/scripts/structure_guard.sh --diff` — **路径白名单 / 错放目录**
- `python3 .harness/scripts/diff_integrity_check.py` — 按当前 Work Item baseline 检查 tracked、staged 与新增未跟踪文件的 Git 空白错误
- `bash .harness/scripts/structure_guard.sh --path <拟建路径>` — 写文件前
- `bash .harness/scripts/task_contract_check.sh --task-dir <harness-workspace/planning/tasks/...>` — 任务 7 字段契约
- `bash .harness/scripts/dag_sync_check.sh --task-dir <harness-workspace/planning/tasks/...>` — DAG 与实施方案同步；默认读取任务目录内 `tasks-dag.md`
- `bash .harness/scripts/qa_evidence_check.sh` — QA 签章 + TEST/REVIEW 报告完整性
- `bash .harness/scripts/harness_knowledge.sh check-planning` — `CONTEXT.md` 的 BMAD Planning 受管区块新鲜度
- `bash .harness/scripts/harness_growth.sh capture --summary "..."` — 排障完成时记录自我成长候选证据，不直接改长期知识
- `bash .harness/scripts/harness_growth.sh review-status` — GROWTH 报告人工 review 状态
- `bash .harness/scripts/harness_growth.sh freshness` — GROWTH 报告的 Evidence digest 是否覆盖当前 Work Item evidence 候选；不依赖 checkout 文件时间，也不扫描其他任务历史
- `bash .harness/scripts/quality_commands.sh lint|test` — 产品侧业务 lint/test 命令
- `bash .harness/scripts/run_in_sandbox.sh '<cmd>'` — 受控 TDD 执行入口；默认 controlled argv，可切 Docker backend
- `bash .harness/scripts/browser_qa_setup.sh check` — 前端任务前检查 Playwright Chromium
- `python3 .harness/scripts/browser_qa.py <url> --action audit` — 真实浏览器 QA 证据
- `python3 .harness/scripts/browser_qa.py <url> --action scenario --scenario <path.json>` — 受限交互步骤、逐步结果、截图和 trace
- `mr_ready.sh` 的 diff-bound review checklist + 独立 receipt — correctness/security/tests/scope 四视角，禁止实现角色自签
- `bash .harness/scripts/release_preflight.sh` — 开源发布前防泄漏检查，不属于日常产品 MR 门禁
- `bash .harness/scripts/check.sh` — Planning Gate + validate + structure + diff_integrity + plan_sync + dag_sync + qa_evidence + knowledge_freshness + growth_freshness + quality；本地可自动刷新 planning CONTEXT、生成缺失 GROWTH 报告，CI 与未绑定/内容过期的 GROWTH 报告会阻断
- `bash .harness/scripts/subagent-pr-gate.sh <TASK>` — QA 凭证

通用结构规则由 active profile 和 `structure_guard` 决定；产品专用规则只能放入对应 profile，不得进入 generic 默认值。

## 品味不变式（GC 与 Review 依据）

1. 禁止无意义 AI 过程注释（`TODO: AI`、`FIXME: agent` 等）
2. 禁止遗留 `console.log` / `print` 调试输出（测试文件除外）
3. 单文件建议 ≤ 400 行；超出须拆分或记入技术债
4. 边界处校验外部数据形状；禁止 YOLO 式猜测结构
5. 优先仓库内共享工具包，避免复制粘贴辅助函数
6. 任务中断且需要跨会话恢复时才写 `harness-workspace/evidence/progress/`；完成信息进入权威运行结果，只有 execution tier 或人工阅读需要时再生成 SUMMARY
7. 已有项目首次接入必须 review `harness-workspace/evidence/intake-reports/`，不能把扫描报告直接当长期知识
8. 排障或实现中出现跨任务会复现的失败、外部系统权限坑、架构边界或新默认行为时，先运行 `harness_growth.sh capture --summary "..."` 写入 `evidence/progress/` 候选证据；不要直接改长期知识。
9. 只有发现新的跨任务失败模式、架构边界、默认行为或明确技术债候选时才触发 Growth；候选必须 review 后才能进入 CONTEXT/LESSONS。`check.sh` 已是 tier-aware 结构化 runner 的兼容包装，本地仍保留 knowledge/growth 自动修复，CI 只读并阻断陈旧证据。

完整 GC 规则：`.harness/rules/gc-golden-principles.md`
