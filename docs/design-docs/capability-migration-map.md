# Harness 能力迁移映射

> 状态：local implementation，2026-08-13。此表解释能力承载关系；`.harness/scripts/capability_contract.py` 与 `validate_harness.sh` 机械检查承载入口和 tier gate 未被静默删除。该审计不代表 guarded 或 enforced 接受保障已接线；旧 result schema 仍显示 `shadow`。

| 来源能力 | 统一入口承载 | 当前状态 | 验收证据 |
|----------|--------------|----------|----------|
| OpenAI Harness：短导航、机械反馈、doc gardening | `start` 最小绑定；`finish` 包装现有 gate | 已承载 | `validate_harness`、`doc-gardening`、runtime tests |
| BMAD / planning level：规划、L1/L2/L3、Entry Gate | 结构化 runner 按 tier 调用 planning 兼容能力 | 已按 tier 承载；lite 不重复完整规划，standard/strict 强制凭证 | BMAD、agent-start 与 gate coverage tests |
| Superpowers：TDD、调试、完成前验证 | quality lint/test 独立写入共享 result | 已承载，人工/测试结果不缓存 | `checks.quality_lint` / `checks.quality_test` |
| Flow-X：CONTEXT / LESSONS / evidence | workspace 原位兼容，不批量迁移 | 已承载；真实 workspace audit 会区分 completed legacy 与活动任务 | `workspace audit` / `migrate-task` / migration tests |
| GStack / 浏览器 QA | standard 前端 diff 自动触发；strict 使用 subject-bound receipt | 已承载，缺 URL/receipt 时阻断 | browser gate 与 gate coverage tests |
| Ralph / Agent Review、独立 QA | standard/strict 将 QA evidence 写入独立 check | 已承载且缺失时阻断 | `checks.qa_evidence` 与 gate coverage tests |
| GC sweeper | `finish` 机械扫描并按 tier/信号要求一次独立结果；CI 对目标 commit 按需调用 HTTPS reviewer | 已接入，receipt 绑定 role/task/subject/policy，缺 runner fail closed | `test_harness_runtime.py`、`test_harness_gc_context.py`、`test_ci_gc_review.py` |
| Brownfield Intake | 首次接入或事实漂移时独立调用 | 保留，不进入每任务固定链 | 既有 intake tests |
| Work Item provider | 本地最多 ready；终态要求 merge/release CI receipt | 已机械封堵本地终态；GitHub 模板只消费绑定 merge commit 的成功 required run，真实 provider 待产品接线 | provider lifecycle / enforcement tests |
| CI commit 接受 | 共享 schema 的 `ci-check` + artifact | GitHub PR 与 merge commit 双阶段模板已接入；其他平台复用同一判定器 | `harness-commit-acceptance` |

升级到 `guarded` 需要版本化 guards/CI 对正常 Git 工作流机械阻断并报告显式绕过边界；升级到 `enforced` 仍要求 live audit 证明受保护权威接受点、required check、发布依赖和 provider done 链全部通过。任何一项未验证、证据过期或只提供 snapshot 时，兼容状态必须显示 `NOT_ENFORCED`。
