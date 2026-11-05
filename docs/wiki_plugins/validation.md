# 验收与排错

先区分作者环境、宿主包校验、依赖安装、运行和页面这几个阶段，使用与改动相关的检查。测试使用隔离数据库、本地文件及模拟服务，不将模拟结果写成真实云盘或模型验收。

## 插件作者

在 Python 3.12、uv 0.6.7 和 SDK dev 依赖就绪的插件根目录执行：

```powershell
uv run --no-sync python -m mt_plugin check
uv run --no-sync python -m mt_plugin pack
```

React 项目先在 frontend 安装锁定依赖；check/pack 会构建 UI。只有完整 MT 仓库时，从 **backend 模块根**执行宿主静态校验：

```powershell
go run ./cmd/plugin-devtool check-package C:/path/to/plugin.zip
```

宿主包校验只证明安装结构可接受，不启动 Python、不验证目标平台依赖，也不证明业务正确。随后在测试 MT 实例检查安装日志、配置授权、启用和实际动作。

## 运行与页面检查

- 生命周期：对象和事件循环复用、查询独立通道、配置纯校验、初始化失败可诊断、取消/停止清理、中断不自动重放。
- 业务：文件授权与版本、部分失败、task ref、等待续接、重复触发 BUSY、稳定幂等键、未知提交保护。
- 存储：跨调用/重启状态、分页、容量、数据恢复阻止条件与密钥不进入导出。
- UI：桌面及手机、明暗主题、显式保存配置、入口缺失/渲染失败时宿主管理可用；React 检查单份 React、资源续期、卸载和构建切换。
- 更新：ZIP/Git、同版本新构建、候选失败保留旧安装、配置保留和新增权限审阅。

Pillow 示例用真实第三方库完成缩放，但文件路由可用本地测试目录。AI 示例测试原生搜索完成标记、缺失引用仍做身份核对、同剧冲突、500 文件分页、手动重试和未知提交不重放。

## 主仓库与分发检查

以下从 MediaTidy 根目录执行；`python` 必须是已装 mypy、Pillow 的 Python 3.12：

```powershell
$env:PYTHONPATH = "$PWD/plugin-sdk;$PWD/mediatidy-plugin-template"
python -m unittest discover -s plugin-sdk/tests -p 'test_*.py'
python -m unittest discover -s mediatidy-plugin-template/tests -p 'test_*.py'
python -m mypy --config-file plugin-sdk/pyproject.toml plugin-sdk/mt_plugin
python scripts/sync-plugin-sdk.py --check
node scripts/check-plugin-docs.cjs
node --test plugin-ui-schema/schema.test.cjs plugin-ui-schema/data-schema.test.cjs
```

后端测试在 backend 运行。开发包变更至少运行 `go test ./internal/plugins ./internal/handler -run 'TestDevelopmentSkill|TestPluginSkillDownload' -count=1`；业务或运行时修改按影响范围扩大测试。需要 Python worker 的 Go 测试先令 `MT_PLUGIN_PYTHON` 指向 Python 3.12。

前端代码变化时在 frontend 运行 `pnpm run build`、`pnpm run test:failure-state` 及相关插件测试。Wiki 同步后在 Wiki 根运行 `npm run build`，并从构建结果验证所有插件章节与下载文件。Skill 还应从 ZIP 解压到临时目录，确认 SDK 能导入、模板可检查/打包，React 本地依赖路径可解析。

## 常见失败

| 现象 | 排查入口 |
|---|---|
| 不支持入口、UI 或 SDK | 核对 Python 入口、SDK/UI 字符串版本及目标宿主，而不只看包名 2.0.0 |
| Manifest/handler mismatch | 清单中的动作、查询、continuation 与装饰器逐项对照 |
| 找不到 mt_plugin/mypy | 作者虚拟环境安装完整 SDK dev extra，使用 --no-sync |
| 依赖准备失败 | 查看锁文件来源、Python 版本、wheel 哈希及平台，查看安装日志 |
| ASYNC_CLIENT_REQUIRED | 异步处理函数改用 ctx.amt 和异步文件辅助方法 |
| 权限拒绝或资源变化 | 检查声明、实例授权、目录、文件/方案修订，不只检查前端按钮 |
| BUSY / FLOW_WAITING | 等待已有流程；tasks.wait 后立即返回，避免继续调用 SDK |
| 页面加载失败 | 保留原 UI 模式，走宿主设置和日志；React 检查入口、共享版本和资源 |
| AI 单次请求失败 | 查看公开错误码与日志阶段，不自动重发、换模型或降级搜索 |
| outcome_unknown | 核对原任务与逐文件状态，禁止无条件重试 |

报告应写出实际执行的命令和环境，单独列出尚未进行的真实服务、浏览器或容器验证。[实现对应关系](implementation-status.md)用于定位源码，不是历史通过记录的替代品。

## 结构化位置与管理接口诊断

宿主校验错误保留 `code/message/retryable`，新增可选 `path`，例如 `config.groups[1].name`、`permissions[0]` 或 `content[0].props.bind`。前端显示错误码与字段位置，Python `SDKError.path` 也保留此信息。诊断不返回被拒绝的配置值、密钥或原始数据库异常；插件 `validate_config` 返回的字段错误只公开声明的字段位置及宿主提示，请将字段填写要求写在插件说明中。

`GET /api/plugins` 的 `data` 仍是正常安装列表；存在坏记录时，响应顶层增加 `diagnostics: [{recordId, kind, code, message}]`。坏记录不会伪装成可运行插件，当前错误码为 `INSTALLATION_CORRUPTED`。异步安装失败保留原 `error` 文本，并可通过安装任务的 `failure` 读取结构化错误。
