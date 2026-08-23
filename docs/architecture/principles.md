# Harness 设计原则

> 精简强制执行的权威设计见 [lean-enforcement.md](../design/lean-enforcement.md)。

0. **AI 时代研发新体系** — 「已有项目接入 — 产品设计（**BMAD Method**）— 协作 — Harness（AI Coding）」联动；**BMAD Planning 必须经 BMAD Method 完成**，否则产品环路不闭合。
1. **双闭环加事实前导** — Brownfield Intake 建立已有项目事实，BMAD Planning **BMAD Method**（`bmad_method_gate.py`）回答「做对的事」，Lifecycle Execution 的 OpenAI-inspired 主执行回答「把事做对」。
2. **人类掌舵，Agent 执行** — 工程师设计环境、意图与反馈回路。
3. **地图优于手册** — `AGENTS.md` 保持简短；细节沉入 `docs/` 与 `.harness/rules/`。
4. **验收与实现分离** — 要求独立 QA 或人工 Gate 时，业务 Agent 不能自签；其余任务仍须由机械结果完成验证。
5. **约束即倍增器** — DAG、TDD、分层架构、GC 黄金原则均编码为可执行钩子。
6. **熵要持续偿还** — 按风险触发 GC；未当场处理的真实技术债记入 `docs/operations/tech-debt.md`。
7. **仓库即情境边界** — 未入库的知识对 Agent 不存在；决策必须可版本化。
8. **候选不是知识** — INTAKE / GROWTH 报告必须经人工 review 后才能迁入 CONTEXT、LESSONS 或架构文档。
9. **结果强制，过程最小** — 接入产品没有有效 Harness 合规结果就不能完成、合并或发布；执行深度按风险分层，禁止用重复命令、重复证据和重复上下文代替治理。
10. **新增必须替代** — 新入口、新产物或新规则必须说明替代对象；不能减少复杂度或覆盖新风险的机制不进入 Lifecycle core。
11. **保留能力，不保留仪式** — 对 BMAD、OpenAI Harness、Flow-X、Superpowers、GStack、legacy planning 等参考项目，保留已验证的方法、数据语义和安全边界；原项目的命令数量、报告数量与固定步骤不构成兼容契约。
