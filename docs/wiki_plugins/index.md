# MediaTidy 插件 · Python SDK v2

MediaTidy 插件通过 Python 实现业务，使用宿主的文件、媒体、整理、STRM、AI、任务和存储能力。每个启用实例拥有常驻 Python 对象；页面可以由 Python 返回 MT UI 组件，也可以采用 React 模块联邦。

本文对应当前仓库的 Python SDK `2.0.0`、`sdkVersion: "2"` 和 `ui.version: "2"`，核对日期为 2026-10-27。SDK 包版本不等于主应用版本；同为 v2 的旧 Goja 插件也不能直接运行在当前 Python 运行时。

## 从这里开始

| 你要做什么 | 阅读入口 |
|---|---|
| 安装、授权、更新和排错 | [插件中心使用指南](user-guide.md) |
| 给 AI 使用完整开发包 | [下载与使用 Skill](downloads.md) |
| 编写第一个 Python 插件 | [快速开始](getting-started.md)、[清单与数据契约](manifest.md) |
| 选择 Python 或 React 页面 | [双模式 UI](ui.md) |
| 使用文件、整理、AI 等宿主能力 | [业务接口](business.md)、[能力清单](capabilities.md)、[权限](permissions.md) |
| 读取 Emby 配置、查询或管理 Emby | [Emby 配置与通用 API](emby.md) |
| 后台任务、定时和完成回调 | [生命周期与续接](automation.md) |
| 保存结果、查看日志、处理大量文件 | [存储](storage.md)、[日志](logs.md)、[工作流容量](workflow-capacity.md) |
| 理解 AI 识别整理示例 | [AI 工作流](ai-organize.md) |
| 检查、打包、同步和验收 | [开发工具](tooling.md)、[验收与排错](validation.md)、[实现对应关系](implementation-status.md) |

## 运行与信任边界

作者流程是：编写 → 本地检查和打包 → 上传 ZIP 或读取 Git → 宿主准备依赖 → 配置授权 → 启用 → 核对任务和统一日志。首次安装完成默认停用，新建触发器默认暂停。

插件、第三方 Python 库和 React 页面都属于受信任代码。虚拟环境隔离依赖，宿主能力授权约束 SDK 调用，但二者都不是限制任意 Python 原生行为的安全沙箱；React 也与主站共享 JavaScript 环境。

宿主在检查上传包时只读取数据；安装阶段准备受限来源的依赖，启用时执行插件代码。页面出错时，宿主仍提供设置、授权、密钥、日志、历史和任务入口。

## 文档和契约来源

主仓库 `backend/internal/plugins/contract/methods.json` 定义宿主方法，`plugin-ui-schema/registry.json` 定义组件。生成器维护 Python 客户端、运行时快照和能力表；`docs/plugins-sdk-v2/` 是正文来源，再同步到 SDK、Skill 和官方 Wiki。准确参数以同包 [methods.json](/downloads/plugins-sdk-v2/methods.json) 为准。
