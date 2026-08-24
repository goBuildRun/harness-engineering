# 可靠性与可观测性

> 精简目标保留真实环境取证、日志/指标/追踪和浏览器 QA 能力，但只在 execution tier 与验收标准要求时触发。任务级 Token、上下文字符数、Agent 调用数、gate 耗时和重跑原因统一进入 `result.json`，不新增成本报告。

## Agent 可读目标

- 应用支持按 git worktree 启动独立实例（业务项目配置后启用）
- 日志/指标/追踪应对 Agent 可查询（LogQL / PromQL 或等价本地栈）
- 受控执行：`.ael/scripts/run_in_sandbox.sh` 默认 controlled argv；需要容器隔离时设置 `AEL_SANDBOX_BACKEND=docker`，首次启用或配置变化后运行 `python3 .ael/scripts/sandbox_acceptance.py --cwd "$PWD"` 做可选实机验收
- 前端验证：`.ael/scripts/browser_qa.py`（真实 Playwright 入口；未安装或页面异常会 block）
- 浏览器 QA 环境：`.ael/scripts/browser_qa_setup.sh check|install`；CI 设置 `AEL_BROWSER_QA_URL` 后启用 Playwright 镜像 job
- 已有项目接入报告应能重复生成；报告差异用于发现服务目录、测试入口、CI 或技术栈漂移
- 相同 fingerprint 的确定性检查应复用结果；人工、生产副作用和部署/回滚证据必须绑定当前 commit，不能缓存复用

## SLO 提示示例（写入 exec-plan 或 product-spec）

- 服务冷启动 < 800ms
- 关键用户旅程 P95 < 2s

Agent 通过受控命令入口运行应用后，用上述工具取证，勿仅凭“我觉得没问题”通过验收。前端任务如果没有可访问 URL，则在 TEST/REVIEW 报告中明确说明浏览器 QA 未执行的原因。
