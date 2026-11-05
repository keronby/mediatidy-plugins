---
name: mediatidy-plugin-development
description: 开发、迁移和排查 MediaTidy Python SDK v2 插件，包括清单、常驻生命周期、任务续接、Python 组件或 React 页面、依赖与 ZIP 打包。用于 MediaTidy 插件作者任务，不用于修改无关主应用功能。
---

# MediaTidy Python 插件开发

以随包契约和目标宿主实现为准。入口为 `plugin.py:Plugin`，继承 `mt_plugin.Plugin`；`sdkVersion` 与 `ui.version` 都是字符串 `"2"`。旧 Goja/JS 插件即使声明 SDK v2，也需要迁移入口、页面和依赖。

## 开始工作

确认用户要创建、修改还是排查插件，先读现有清单、处理函数和相关测试。保留用户选择的 UI 模式和既有业务；只在能力或执行范围确实不明时澄清。主仓库可用时对照当前实现；只有本包时用随包契约，不能假定旧宿主已支持所有新字段。

- 环境、模板与打包：[快速开始](references/getting-started.md)。
- 清单、schema、Python/JSON 命名：[清单与参数](references/manifest.md)。
- 方法参数、权限、限额和允许通道：[methods.json](references/sdk/mt_plugin/methods.json)；实现入口是 [clients.py](references/sdk/mt_plugin/clients.py)。
  两者为本包旧版快照，未含 `ffprobe.cache_exists`、`artifacts.*`、`emby.*`；这些新能力见 [业务接口](references/business.md) 与 [能力清单](references/capabilities.md)，运行时先用 `runtime.capabilities()` 探测。
- Python 组件属性：[ui-registry.json](references/sdk/mt_plugin/ui-registry.json)。

## 选择随包资源

默认从 [目录扫描模板](assets/plugin-sdk/starter/README.md) 复制到用户工作目录，SDK 位于 `references/sdk/`。安装作者依赖：在插件虚拟环境中安装 `<本 Skill>/references/sdk[dev]`，之后用 `uv run --no-sync python -m mt_plugin pack`。Python 固定 3.12，uv 固定 0.6.7；不要将宿主 SDK 加入插件运行依赖或锁文件。

- 图片处理使用 [Pillow 示例](assets/plugin-sdk/examples/pillow/README.md)。
- React 使用 [React 示例](assets/plugin-sdk/examples/react/README.md) 与 [公开前端 SDK](assets/plugin-frontend-sdk/package.json)，并读 [UI](references/ui.md)。
- Emby 与图片：[Emby 配置与通用 API](references/emby.md)、[临时图片与预览](references/artifacts.md)。保留 assets 下目录层次时本地依赖可直接解析；搬走模板后重设依赖并更新锁文件。React 作者另需 Node 22.12+，默认 Python 作者不需要。
- 本包不含 AI 插件源码。处理其业务时阅读 [AI 工作流](references/ai-organize.md)，在可用的主仓库中检查 `mediatidy-plugin-template/`，不要凭旧示例重建已变更的行为。

## 实现时遵守的契约

- 同步函数用 `ctx.mt`，异步函数用 `await ctx.amt`。Python 方法和顶层关键字使用 snake_case；嵌套字典仍用契约 camelCase。不要混用字典与普通关键字以为会自动合并。
- 每个公开动作、查询、内部续接均与清单一一对应。`validate_config` 纯校验；查询和页面只读。业务与读取通道可能并行访问常驻对象，跨重启状态放 KV 或 data_dir。
- 耗时操作保存宿主 task ref。调用 `tasks.wait` 后立即返回；完成函数用 `@continuation` 且清单标记 `kind: continuation`。排队不等于成功，续接检查最终任务状态。见[生命周期](references/automation.md)。
- 触发器使用清单稳定 key；schedule upsert 使用 `declarationKey`，event upsert 使用 `key`。新增默认暂停，重建保留用户暂停状态。
- 文件使用有效宿主引用、授权根及相对路径，不能把不同挂载路由或绝对路径混用。二进制通过工作文件传输，目录、方案、版本与权限最终由宿主校验。见[业务接口](references/business.md)、[权限](references/permissions.md)。
- 直接整理选 `organize.manual.run`；人工审阅选 `organize.manual.prepare/prepare_batch` 和宿主 DraftReview，保留 `organize.prepare/organize.submit` 授权。未知提交先核对原任务，不重放；稳定幂等键不复用于不同请求。
- 批量流程先检查 `runtime.info().limits.maxWorkflowFiles`；缺失时按 100 处理。当前上限 500，列表页仍最多 100，AI 示例每批 8。分项持久化和分页显示，避免把全部明细塞进运行摘要。见[容量](references/workflow-capacity.md)。
- AI 原生搜索必须同时启用 `single_request=True` 和 `native_web_search=True`，并有 `ai.chat/ai.web.search` 授权。该模式失败不自动重发或降级；搜索来源缺失与 TMDB 身份核对失败不同。不要把 AI 示例特有的一次识别规则强加给未选择该模式的插件。
- 配置草稿显式保存，日志记录阶段和可定位错误，避免凭据、私有链接与敏感请求。见[存储](references/storage.md)、[日志](references/logs.md)。

插件与依赖是受信任原生代码；虚拟环境和 SDK 授权不是安全沙箱，React 与主站共享 JavaScript。宿主上传检查只读数据；作者 check/pack 会导入源码并可能构建 React，不能对不可信上传包执行作者 CLI。

## 验证与交付

按[验收](references/validation.md)选择与改动相关的检查：清单/装饰器一致、schema、Python 类型、依赖锁和 ZIP；有主仓库时补宿主包检查。业务用隔离数据及模拟服务测试，覆盖取消、部分失败、续接、幂等和未知提交。UI 在实际宿主检查页面及失败后的管理入口。

交付源码、插件 ZIP、必要授权说明及已验证/未验证范围。区分本地模拟与真实服务结果；开发请求不包含推送、正式发布、启用生产插件、真实云端操作或外部通知授权。排错先定位依赖、生命周期、页面、SDK 或业务任务阶段，避免把失败包装成成功。
