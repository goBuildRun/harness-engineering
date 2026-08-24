# workflows/ — 任务类型工序（规划分级）

Lead / planning-agent 在 **BMAD Planning**（含 `bmad_method_gate.py` 留痕）完成分级后，按级别选用工序。  
官方分级说明：[planning/bmad-planning.md](../../docs/planning/bmad-planning.md)

L1/L2/L3 是继承自 planning level 思路的 **planning level**，继续用于规格和任务包复杂度；它不等于目标 `lite/standard/strict` execution tier。统一入口落地后，这些工序由 `start/finish` 内部选择，不再要求 Agent 手工串联。

本目录记录当前兼容工序和能力来源，不是新的公开操作手册。任务是否启用独立 QA、完整证据、浏览器验证或生产 Gate，以最终 execution tier 为准，不能仅凭 L1/L2/L3 固定推导。

## 选型

```
新任务
  ├─ 先做 BMAD Planning：docs/planning/bmad-planning.md
  ├─ planning_gate.sh pass
  └─ Lifecycle Execution：docs/getting-started/cli.md（OpenAI 主闭环）
       ├─ L1 → L1-trivial.md
       ├─ L2 → L2-standard.md
       └─ L3 → L3-cross-service.md
```

## 文件

| 文件 | 说明 |
|------|------|
| [L1-trivial.md](./L1-trivial.md) | 单文件小修，简化 QA |
| [L2-standard.md](./L2-standard.md) | 单服务多文件，标准门禁 |
| [L3-cross-service.md](./L3-cross-service.md) | 跨服务，强制独立 QA |

这些工序以本目录中的版本为权威实现，不依赖仓库外的参考路径。
