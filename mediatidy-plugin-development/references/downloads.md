# 下载与使用开发 Skill

[下载 Python 插件开发 Skill · SDK 2.0.0](https://murongyun8.github.io/MediaTidy-Wiki/downloads/mediatidy-plugin-development-2.0.0.zip)

也可在 MT「插件中心」点击「下载开发 Skill 完整包」。该入口使用登录后的 `GET /api/plugins/development/skill.zip`，返回随主程序打包的资源；旧主程序内置的包可能早于 Wiki，请同时核对宿主支持的能力。

## 包中包含什么

```text
mediatidy-plugin-development/
├── SKILL.md
├── agents/openai.yaml
├── references/
│   ├── getting-started.md、manifest.md、business.md 等文档
│   └── sdk/
│       ├── pyproject.toml
│       └── mt_plugin/     Python 客户端、CLI、契约、组件注册表
└── assets/
    ├── plugin-sdk/starter/
    ├── plugin-sdk/examples/pillow/
    ├── plugin-sdk/examples/react/
    └── plugin-frontend-sdk/   React 公开类型与宿主接口
```

SDK、模板和文档来自同一份仓库实现。包内不含解释器、第三方依赖、虚拟环境、真实凭据或已安装插件。AI 识别示例完整源码位于主仓库 `mediatidy-plugin-template/`，行为见 [AI 工作流](ai-organize.md)；开发包不复制该独立插件。

## 交给开发助手

解压并保留整个 `mediatidy-plugin-development/` 目录，按所用工具支持的方式导入 Skill，或让助手先读取 `SKILL.md`。不要只复制入口文件而丢失 references 和 assets。

```text
使用 mediatidy-plugin-development Skill 开发一个 MediaTidy Python 插件。
基于随包 starter 扫描授权目录，等待任务完成后保存清单并分页展示。
按同包 methods.json 选择能力，完成本地检查、打包和宿主包校验。
列出需要由我在 MT 授予的目录权限，以及尚未完成的实际环境验证。
```

作者环境安装与打包见[快速开始](getting-started.md)。Skill 不会安装、启用 MT 插件或授予目录、网络与业务权限。插件安装入口只接收作者打出的插件 ZIP。

## 契约下载

- [宿主能力与输入输出 schema](sdk/mt_plugin/methods.json)
- [Python 同步与异步客户端](sdk/mt_plugin/clients.py)
- [MT UI v2 组件注册表](sdk/mt_plugin/ui-registry.json)

单个源码文件用于查阅。执行 CLI 时使用完整 SDK 项目，避免遗漏契约和注册表。维护者同步方法见[开发工具](tooling.md)。
