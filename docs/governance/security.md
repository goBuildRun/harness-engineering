# 安全边界

> 精简只减少公开步骤，不削弱安全边界。目标 `finish` 按 execution tier 选择 controlled、Docker、浏览器或人工安全 Gate；安全失败始终 fail closed，不能因成本预算或缓存被跳过。

## 受控执行

- `run_in_sandbox.sh` 是统一测试/构建入口，默认 `controlled` 后端：不经 shell eval，禁止 `sudo`、`rm -rf /`、shell 控制符和 `bash -c` 绕行。
- 需要更强隔离时设置 `AEL_SANDBOX_BACKEND=docker`；Docker 后端把产品根挂载到 `/workspace`，默认 `AEL_SANDBOX_DOCKER_NETWORK=none`。
- `AEL_SANDBOX_BACKEND=remote` 使用 HTTPS JSON 协议提交 argv、workspace ref、timeout 和 subject digest；token 只进入 Authorization header，返回 subject 必须精确匹配。协议接入不等于远程执行器已经具备 Firecracker 级隔离。
- Docker 后端是当前可用的容器级隔离能力，但不是 Firecracker/微虚拟机级强隔离，也不是运行不可信恶意代码的完整安全边界。
- `AEL_SANDBOX_IMAGE` 应按产品技术栈显式选择；不要在镜像里烘入密钥。

## 产品质量命令

- 产品侧 `quality.commands` 只能声明 lint/test argv；可执行文件 allowlist、shell 绕行拦截和危险环境变量拦截由 buildrun-agent-engineering-lifecycle 控制，不能由产品配置放宽。
- 严格 CI 下缺少 lint/test 配置会 `block`，避免业务质量门禁空跑。

## 密钥与发布

- 密钥不得入库；使用环境变量、`.env` 或密钥管理服务，`.env` 必须 gitignore。
- Work Item provider 的 app key、secret、token、个人 user id 不写入产品 `project.yaml`；`project.yaml` 只保存 provider 选择和非密钥产品参数。
- 开源发布 buildrun-agent-engineering-lifecycle 前必须运行 `release_preflight.sh`，清理 `.ael/products/registry.yaml`、`active-product.json`、`.env`、本机绝对路径和疑似密钥。

## 接入扫描与 QA

- 已有项目接入扫描默认跳过 `.env`、私钥/证书、依赖缓存、`ael-workspace*`、`_bmad/`、`bmad-output/` 与本地 Agent 缓存。
- INTAKE 报告会对疑似密钥值脱敏，但它不是密钥审计工具；报告进入知识库前必须人工 review。
- `qa-evaluator` 须覆盖：越权访问、注入、敏感数据泄露三类基础探测。
- 合并前安全相关失败为**硬门禁**，不可“先合再修”。
- 人工安全 Gate、生产副作用和 strict 部署/回滚证据必须绑定当前 commit，不得跨 commit 缓存。
