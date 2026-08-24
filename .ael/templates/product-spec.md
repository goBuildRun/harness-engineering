---
bmad_method: true
bmad_flow: standard
bmad_phases:
  - planning
bmad_skills:
  - bmad-create-prd
  - bmad-validate-prd
bmad_completed_at: "YYYY-MM-DD"
spec_level: L2
# Uncomment when production acceptance requires a real Provider call:
# production_evidence:
#   provider_mode: real_required
---

# 功能名

> 本文件须由 BMAD Method **Planning** 工作流产出并映射入库；`bmad_skills` 填写 Cursor 中**实际执行**的技能名。  
> 落盘路径由产品侧 `ael-workspace/project.yaml` 决定（默认 `ael-workspace/planning/product-specs/`）。

## 目标

一句话说明该需求要解决的用户/业务问题。`work_item.sh draft-spec` 会把这里变成待确认任务草稿，`sync-spec` 确认后同步到外部 Work Item。

## 验收标准

- [ ] 可观测、可测试的条目

## 非目标

- 明确不做的事
