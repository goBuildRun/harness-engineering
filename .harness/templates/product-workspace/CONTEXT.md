# CONTEXT — 项目共享上下文

> 维护者：planning-agent / lead-agent / 人类负责人  
> 作用：给所有后续需求、设计、开发、测试提供稳定上下文，禁止依赖聊天记忆。

## 1. 项目定位

| 项 | 内容 |
|----|------|
| 产品名 | {{product_name}} |
| 产品 ID | {{product_id}} |
| Profile | {{profile}} |
| 当前阶段 | 待补充 |

## 2. 技术栈与运行方式

| 维度 | 当前选择 | 证据路径 | 备注 |
|------|----------|----------|------|
| 前端 | 待补充 |  |  |
| 后端 | 待补充 |  |  |
| 数据库 | 待补充 |  |  |
| 测试 | 待补充 |  |  |
| CI/CD | 待补充 |  |  |

## 3. 领域语言

| 术语 | 定义 | 首次来源 |
|------|------|----------|
|      |      |          |

## 4. 已锁决策

| 决策 | 取值 | 原因 | 来源 |
|------|------|------|------|
| Harness workspace | `{{workspace}}/` | 产品产物归产品仓库所有 | harness-engineering init |
| BMAD 规划沉淀 | `sync-planning` 管理受管区块 | 全新项目的产品目标、产品蓝图、架构/方案和任务边界必须成为长期上下文 | harness-engineering `harness_knowledge.py` |
| 任务契约 | `harness-task-v1` 7 字段任务契约 | 明确 read/write/action/verify/done，减少任务漂移 | harness-engineering `tasks/_templates/03-实施方案.md` |
| 自我成长 | 先生成成长候选，再人工迁入规则 | 防止 AI 自动改全局规则导致规则漂移 | harness-engineering `harness_growth.py` |

## 5. 既有抽象索引

| 能力 | 路径 | 何时复用 | 禁止重复实现 |
|------|------|----------|--------------|
| Workspace 路径解析 | harness-engineering `workspace_paths.py` | 任何脚本需要定位 planning/runs/knowledge/evidence | 禁止硬编码 workspace 子目录 |
| 知识初始化与规划同步 | harness-engineering `harness_knowledge.py` | 新团队接入、目录缺失、模板恢复、BMAD planning 进入 CONTEXT | 禁止手写一套知识目录创建逻辑 |
| 成长扫描 | harness-engineering `harness_growth.py` | 迭代结束、QA/GC 后 | 禁止让 Agent 直接改 CONTEXT/LESSONS |

## 6. 禁动清单

| 路径/能力 | 原因 | 解禁条件 |
|-----------|------|----------|
| harness-engineering runtime | 产品经验不得直接污染全局规则 | 形成跨产品共识并人工 review |

## 7. 技术债与清理窗口

| 项 | 严重度 | 建议处理时机 | 来源 |
|----|--------|--------------|------|
|    |        |              |      |
