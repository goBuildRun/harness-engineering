# 产品规格索引

BMAD Planning **须**经 [BMAD Method](https://github.com/bmad-code-org/BMAD-METHOD) 工作流产出 PRD/验收标准，并映射到本目录（`planning/product-specs/`）。**机械门禁**校验 YAML front matter 与可勾选验收标准。

BMAD 在 **产品根**安装（推荐由 `harness_init.sh init --install-bmad` 非交互生成 `_bmad/`）；BMAD 原生输出由 init 校准到 `{{workspace}}/bmad-output/`，正式规格映射到 `{{workspace}}/planning/product-specs/`。

## 新建规格

```bash
# 从 harness-engineering 目录执行；PRODUCT_ROOT 指向产品根
PRODUCT_ROOT=/path/to/{{product_name}}
cp .harness/templates/product-spec.md "$PRODUCT_ROOT/{{workspace}}/planning/product-specs/<功能>.md"

# 1. 在 Cursor 于产品根执行 BMAD Method（如 bmad-create-prd → bmad-validate-prd）
# 2. 将产出映射到 planning/product-specs/<功能>.md，填写 front matter 中 bmad_skills / bmad_completed_at
# 3. L2/L3 另建 {{workspace}}/planning/exec-plans/active/ 与 {{workspace}}/planning/tasks/，见 harness-engineering docs/BMAD_Prelude.md
```

## Front matter 必填字段

| 字段 | 说明 |
|------|------|
| `bmad_method: true` | 声明经 BMAD Method |
| `bmad_flow` | L1 用 `quick`；L2/L3 用 `standard` |
| `bmad_skills` | **实际执行**的技能名列表 |
| `bmad_completed_at` | 工作流完成日期 |
| `spec_level` | L1 / L2 / L3，与 `planning_gate.sh` 参数一致 |

校验脚本：harness-engineering 的 `bmad_method_gate.py`（由 `planning_gate.sh` 调用）。

Work Item 同步：在 harness-engineering 目录先执行 `work_item.sh draft-spec <markdown> --assignee <provider-user-id>` 生成待确认任务草稿，确认后执行 `work_item.sh sync-spec <markdown> --assignee <provider-user-id>`，按 `bmad-work-item-v1` 创建协同任务，并回写 `#<work-item-id>` 到勾选行尾。外部任务只保存摘要、负责人、状态和 Harness Links，完整规格以本目录为真相源。

## 路径约定（相对 `planning/`）

- 产品规格：`product-specs/<功能>.md`
- 执行计划：`exec-plans/active/<功能>.md`
