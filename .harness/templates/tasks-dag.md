### 任务流拓扑图

- [ ] T1: (模块名) 与 `03-实施方案.md` 同 ID 的实现任务 / 负责人: backend-agent / 前置依赖: 无

按 execution tier 或明确风险添加 QA、GC、浏览器验证和人工 Gate 节点；不要为低风险任务预建空节点。

### 计划门禁

通过：`bash .harness/scripts/feedback_planner.sh '[ ] T1 <active-profile-允许的路径> 写失败测试 [ ] 实现 [ ] 沙箱验证'`

计划须同时含：**测试/TDD**、**`[ ]` 语法**、**白名单业务路径**（或 `structure_guard`）。

### 同步门禁

```bash
bash .harness/scripts/task_contract_check.sh --task-dir <planning/tasks/...>
bash .harness/scripts/dag_sync_check.sh --task-dir <planning/tasks/...>
```

Harness 规则见 `harness-engineering/docs/USAGE.md`；复制到产品任务目录后不要保留指向模板原位置的相对链接。
