# 代码放置法则（写文件前必读）

> 路径权威：当前产品 `ael-workspace/project.yaml` 中的 active profile + `.ael/profiles/<profile>/package-allowlist.yaml`；产品领域说明按需读取 `docs/references/<profile>/index.md`。
> 机械校验：`bash .ael/scripts/structure_guard.sh --path <路径>`

## 写前四问

1. **路径是否在 active profile 的 allowlist 内？**
2. **是否符合产品架构、模块所有权和依赖方向？**
3. **任务契约的 `write_files` 是否已登记该路径？**
4. **是否触碰产品 profile 标记的 protected、upstream、generated 或 secret 边界？**

## 禁止模式

- 在 allowlist 外新建或移动业务文件
- 为通过检查而放宽 allowlist，且没有产品架构依据和人工确认
- 修改任务契约未登记的路径
- 绕过 profile 的 protected、upstream、generated、migration 或 secret 约束
- 把一个产品的目录结构复制为所有产品的默认规则

## 写后必跑

```bash
bash .ael/scripts/structure_guard.sh --diff
```

`block` 时修正文件位置或任务计划；只有产品架构确实变化且经负责人确认时，才能更新对应 profile allowlist 和领域说明。
