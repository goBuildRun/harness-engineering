# 独立验收与深度怀疑 (Verification & Skepticism)

> **QA 签章与门禁命令**：[getting-started/cli.md](../../docs/getting-started/cli.md) 第 5 节。

基于 OpenAI 关于 Harness Engineering 的“执行与验收剥离”哲学，为了避免大模型“为了通过测试而写针对性代码”（即既当运动员又当裁判）。

## 1. QA Evaluator 介入机制

独立 QA 由当前兼容工序或 execution tier 触发：目标 `standard` / `strict`、当前 L3，以及命中共享契约、权限、安全、数据、生产副作用等高风险因素的任务必须启用；`lite` 或当前低风险 L1/L2 可由最终机械验证闭环。

需要独立 QA 的业务 Subagent（如 `backend-agent` 或 `frontend-agent`）在提交代码成果前，必须：
1. 自己在本地跑通 TDD 循环。
2. 将结果提交给 **Lead Agent**。

Lead Agent 接到完成汇报后，必须启动一个名为 `qa-evaluator` 的全新隔离会话。没有触发独立 QA 时，不得伪造签章或生成空 TEST/REVIEW 报告。

## 2. QA Evaluator 的使命
`qa-evaluator` 的系统提示自带强烈的“怀疑论”倾向，它不负责写业务逻辑，它的唯一工作是**挑刺**：
- 阅读 `backend-agent` 变更文件与 **git 路径清单**
- 核对：变更路径 ⊆ `03-实施方案.md` 登记路径
- 运行或要求提供：`bash .harness/scripts/structure_guard.sh --diff` 的 `pass` 输出
- 质问：“这个测试用例是否覆盖了边界条件？”“是否存在安全越权？”
- 它被授权编写并运行“破坏性测试脚本”去攻击 `backend-agent` 的代码。

对于要求独立 QA 的任务，只有当 `qa-evaluator` 给出书面 JSON 通过协议，Lead 才能将 DAG 中的任务设为 `[x]`。

### QA 签章命令（物理凭证）

```bash
# 通过
bash .harness/scripts/qa_sign_off.sh T1 pass '边界与安全测试已通过'

# 驳回
bash .harness/scripts/qa_sign_off.sh T1 fail '缺少越权用例'

# 业务 Agent 完结前
bash .harness/scripts/subagent-pr-gate.sh T1
```

主凭证：`runs/tasks/<work-item-id>/qa_approved_<TASK_ID>.json`；`runs/qa_approved_<TASK_ID>.json` 是当前兼容副本。模板见 `.harness/templates/qa-evidence.json`。

`qa_sign_off.sh` 只写独立 receipt，不重复追加 `05-QA验收.md`；当前 L2 因风险触发 QA 但尚无 `05` 时，应按模板补建该文件，不把缺少可选模板误报为实现失败。
