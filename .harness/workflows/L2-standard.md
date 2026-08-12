# L2 工序（标准）

> 定位：当前兼容工序。L2 是 planning level，不等于目标 `standard` execution tier。当前 Work Item **必填**产品 provider 的任务 ID；目标仍由实际 diff 决定最终 tier。

## BMAD Planning

1. `$PRODUCT_ROOT/harness-workspace/planning/product-specs/<功能>.md`（自 `.harness/templates/product-spec.md` 复制）
2. `bash .harness/scripts/work_item.sh draft-spec "$PRODUCT_ROOT/harness-workspace/planning/product-specs/<功能>.md" --assignee <provider-user-id>` 生成待确认草稿
3. 人确认标题、范围和负责人后，执行 `bash .harness/scripts/work_item.sh sync-spec "$PRODUCT_ROOT/harness-workspace/planning/product-specs/<功能>.md" --assignee <provider-user-id>`（回写 `#<work-item-id>`）
4. 建 `$PRODUCT_ROOT/harness-workspace/planning/tasks/YYYY-MM-DD-<work-item-id>-<简称>/`，最少 `00` + `03` + `04`（模板自 `tasks/_templates/`）
5. `00-任务卡.md`：Gate 1 → **已确认**；任务编号 = sync-spec 回写的 Work Item ID
6. `03-实施方案.md`：登记**目标路径表**
7. `bash .harness/scripts/planning_gate.sh L2 "$TASK_DIR"`（`TASK_DIR` 是上述产品任务目录；通过后自动 `task_workspace activate`）

## Harness Execution

1. `bash .harness/scripts/agent_start.sh <work-item-id>`（自动从 planning/tasks/ 恢复 Planning Gate；静默 `close in_progress`）
2. `03-实施方案.md` 明细任务 → 同步 `tasks-dag.md`
3. `feedback_planner.sh`（须含 TDD + `[ ]` + **业务路径**）→ 子代理沙箱 TDD
4. 写前 `structure_guard.sh --path`；写后 `--diff` + `plan_sync_check.sh`
5. L2 QA **按需**；触发时先确保任务包有 `05-QA验收.md`，再执行 `qa-evaluator` + `qa_sign_off` + `subagent-pr-gate`
6. `memory-sweep.sh` → `check.sh` → `mr_ready.sh`；目标生命周期中本地 `finish` 最多进入 ready/review，只有目标 commit 的 required check 通过且合并或发布成功后才能关闭 Work Item

## 参考

- [docs/USAGE.md](../../docs/USAGE.md) · [docs/COLLABORATION.md](../../docs/COLLABORATION.md)
