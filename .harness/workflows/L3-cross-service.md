# L3 工序（跨服务）

> 定位：当前兼容工序。L3 是 planning level；跨服务、API、契约或 DB 变更通常也会把 execution tier 升到 `strict`，但最终由风险规则和实际 diff 判定。当前 Work Item **必填**产品 provider 的任务 ID。

## BMAD Planning

1. `$PRODUCT_ROOT/harness-workspace/planning/product-specs/` + `$PRODUCT_ROOT/harness-workspace/planning/exec-plans/active/`
2. `work_item.sh draft-spec --assignee <provider-user-id>` → 人确认父子任务边界和负责人
3. `work_item.sh sync-spec --assignee <provider-user-id>` → 回写 `#<work-item-id>`
4. 建 `$PRODUCT_ROOT/harness-workspace/planning/tasks/YYYY-MM-DD-<work-item-id>-<简称>/` 全套 `00`～`06`
5. `02-影响分析.md` 含契约变化清单
6. planning-agent 或 Lead **只分析不改代码**
7. `planning_gate.sh L3 "$TASK_DIR"`，其中 `TASK_DIR` 指向上述产品任务目录

## Harness Execution

1. `agent_start.sh <work-item-id>` → `tasks-dag.md` 含跨服务依赖顺序
2. 子代理按 provider → consumer 顺序；每步 `structure_guard` + `plan_sync_check`
3. **必须** `qa-evaluator` + `qa_sign_off.sh` + `subagent-pr-gate.sh`
4. **必须** `gc-sweeper` + `memory-sweep.sh`
5. `06-交付结论.md` 与 `planning/exec-plans/completed/` 归档
6. `mr_ready.sh`；目标生命周期中 Work Item 由合并或发布自动化关闭，本地验证不得直接写 `done`

## 参考

- [docs/BMAD_Prelude.md](../../docs/BMAD_Prelude.md)
