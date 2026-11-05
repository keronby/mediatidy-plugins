# 当前实现与验证范围

本页用于定位文档对应代码，核对日期为 **2026-10-26**。它不沿用旧版 Goja 或早期 Python 重构的测试通过记录作为当前验收结果。

## 实现对应关系

| 文档主题 | 主仓库入口 |
|---|---|
| 清单与 schema | `backend/internal/plugins/types.go`、`schema.go`、`plugin-sdk/mt_plugin/schema.py` |
| 能力与 Python 客户端 | `backend/internal/plugins/contract/methods.json`、`scripts/generate-plugin-sdk.py` |
| 常驻运行、取消与生命周期 | `worker.go`、`lifecycle.go`、`channels.go`、`limits.go` |
| ZIP/Git、依赖与更新 | `install.go`、`install_jobs.go`、`python_environment.go` |
| 页面与资源 | `plugin-ui-schema/`、`backend/internal/plugins/page.go`、`resources.go`、`plugin-frontend-sdk/` |
| 任务、触发器与数据 | `tasks.go`、`flows.go`、`schedules.go`、`events.go`、`storage.go`、`data.go` |
| 统一日志 | `backend/internal/plugins/logs.go`、`frontend/src/components/plugins/PluginLogs.tsx` |
| AI 单次请求及原生搜索 | `backend/internal/ai/plugin_chat.go`、`plugin_native_web.go` |
| AI 示例策略 | `mediatidy-plugin-template/plugin.py` 及其 `tests/` |
| 分发 | `scripts/sync-plugin-sdk.py`、`package-plugin-development.py`、`sync-plugin-wiki.cjs` |

表中未带根路径的 Go 文件位于 `backend/internal/plugins/`。

## 仍需按环境验证

开发机检查、模拟测试、宿主包校验、实际安装、浏览器交互和发布容器运行是不同层次。[验收与排错](validation.md)列出相应步骤，执行记录应明确命令、环境、结果和跳过原因。

当前只接受公共 PyPI 哈希锁定 wheel；不支持运行时源码编译、系统包安装、替换宿主 SDK。二进制传输当前限定已实现的本地/FUSE 路由，其他路由返回不支持。旧 JS 入口、UI v1 和独立预览工具不属于当前作者流程。

本次文档及 Skill 更新不代表已调用真实 AI/云盘、完成生产插件安装、执行多架构发行容器验收或发布站点。要证明这些结果必须在对应环境单独执行。
