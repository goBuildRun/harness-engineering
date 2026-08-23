# QA 阶段注入（Lead → qa-evaluator）

## 审查清单

1. 变更路径 ⊆ `03-实施方案.md` 登记路径
2. `bash .harness/scripts/structure_guard.sh --diff` → pass
3. `bash .harness/scripts/plan_sync_check.sh` → pass
4. 测试覆盖边界、安全、scope
5. 无错放目录（PACKAGE_STRUCTURE）

## 签章命令（仅 QA 执行）

```bash
bash .harness/scripts/qa_sign_off.sh <TASK_ID> pass '<说明>'
bash .harness/scripts/subagent-pr-gate.sh <TASK_ID>
bash .harness/scripts/task_workspace.sh qa-path <TASK_ID>   # 排障：凭证路径
```

签章写入 `runs/tasks/<taskId>/qa_approved_<Tn>.json`（有激活任务时）。

## 任务包记录

- `qa_sign_off.sh` 只写独立 receipt；TEST、REVIEW 和 `05-QA验收.md` 由各自工序维护，不重复追加签章段。
- 当前 L2 因风险触发独立 QA 但没有 `05` 时，先从 `tasks/_templates/05-QA验收.md` 补建；未触发独立 QA 时不要创建空文件。

## 禁止

- 修改业务逻辑以「帮开发通过」
