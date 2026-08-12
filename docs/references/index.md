# 外部参考索引

本目录保存外部方法、上游系统和产品 profile 的可追溯参考。参考项目提供能力来源，不提供需要逐套执行的并行工作流。

| 来源 | 保留能力 | 内部权威说明 |
|------|----------|--------------|
| OpenAI Harness Engineering | 短 Agent 地图、仓库记录系统、渐进披露、机械反馈、doc gardening | [精简强制执行设计 §1.1](../design-docs/lean-enforcement.md#11-能力保留契约)、[AGENTS.md](../../AGENTS.md) |
| [BMAD Method](https://github.com/bmad-code-org/BMAD-METHOD) | 产品分析、规划、方案设计、可测试验收与实现就绪 | [BMAD_Prelude.md](../BMAD_Prelude.md) |
| planning level | L1/L2/L3 planning level、任务模板与 Entry Gate | [workflows README](../../.harness/workflows/README.md) |
| [Flow-X](https://github.com/aitanjp/flow-x) | CONTEXT / LESSONS / PROGRESS / SUMMARY / TEST / REVIEW / GROWTH 的知识与证据语义 | [Team_Product_Harness.md](../Team_Product_Harness.md) |
| [Superpowers](https://github.com/obra/superpowers) | 规格先行、TDD、调试纪律和完成前验证 | [QUALITY.md](../QUALITY.md) |
| [GStack](https://github.com/garrytan/gstack) | 真实环境调查、浏览器 QA 和审查视角 | [RELIABILITY.md](../RELIABILITY.md) |
| Ralph / Agent Review | Agent 审 Agent、失败修复和重试 | [Harness_Workflow.md](../Harness_Workflow.md) |
| Brownfield Intake | 已有项目事实扫描、人工 review 后入长期知识 | [Brownfield_Intake.md](../Brownfield_Intake.md) |
| Work Item providers | 负责人、状态、讨论和跨角色协同 | [BMAD_Work_Item_Contract.md](../BMAD_Work_Item_Contract.md)、[adapter README](../../.harness/work-items/README.md) |

引用方式：只在任务计划或 exec-plan 中链接与当前变更直接相关的条目，优先写相对路径和精确章节，不把整份外部文档或本目录全量注入 prompt。能力的保留、按需触发或替代关系以 [精简强制执行设计](../design-docs/lean-enforcement.md) 为准。
