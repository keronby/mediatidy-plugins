# 依赖、检查、打包与同步

## 作者工具与宿主安装

| 阶段 | 实际行为 |
|---|---|
| 作者 `python -m mt_plugin check` | 校验清单/装饰器/schema，执行 uv lock，独立导入入口，不调用 on_init，运行 mypy；React 还执行前端构建 |
| 作者 `python -m mt_plugin pack` | 完成同样检查，按 include 收集文件并输出 ZIP；支持 `--root` 和 `--output` |
| 宿主读取 ZIP/Git | 只解析数据和资源，不导入作者代码 |
| 宿主准备依赖 | 按锁文件、哈希和平台 wheel 准备候选环境，不安装项目自身或开发依赖 |
| 启用插件 | 配置校验、启动常驻实例并调用 on_init |

作者 Python 固定 3.12，uv 固定 0.6.7；SDK dev extra 固定 mypy。React 作者另需 Node 22.12+ 和已安装的前端依赖。作者 check/pack 可执行源码导入与前端构建，不应拿来检查不可信上传包。

## 运行依赖

`pyproject.toml` 与 `uv.lock` 锁定直接和传递依赖及 wheel 哈希。当前只接受公共 PyPI，拒绝 Git/path/editable 依赖、自定义 uv 来源或安装选项、宿主 SDK 替换。原生库必须有匹配平台的预编译 wheel 及所需系统库，不在宿主编译扩展或安装系统软件。

宿主先解析锁文件，再用 `uv export --locked --offline` 导出运行依赖，`uv pip sync --require-hashes --only-binary :all:` 安装。锁文件与项目不一致、哈希不符、缺少 wheel 或系统库会在依赖准备或初始化时失败。每实例、每构建隔离环境，沿用现有持久化存储和只读镜像约束。

SDK 只在作者虚拟环境安装，运行时由 MT 提供。不能用加进锁文件的方式修复“找不到 mt_plugin”，应按[快速开始](getting-started.md)准备作者环境。

## 发布包与更新

包至少包含 `plugin.py/plugin.json/pyproject.toml/uv.lock`；辅助 Python 模块、提示词、UI 和资源加入 `[tool.mt-plugin].include`。pack 排除环境、缓存和 node_modules，拒绝符号链接和路径逃逸。ZIP 最多 40 MiB，解压最多 200 MiB、1,000 个文件。

`include` 接受相对文件路径和目录，目录递归收集；不支持 `core/**`、`ui/*` 等通配符。显式声明的路径不存在、必需文件未纳入、或 `ui.icon/ui.assets/ui.entry` 声明的资源漏打包时，`check` 和 `pack` 都会报告字段位置与原因。例如：

```toml
[tool.mt-plugin]
include = ["plugin.py", "plugin.json", "pyproject.toml", "uv.lock", "core", "ui"]
```

只列出实际存在的目录。未声明 `include` 时，使用四个必需文件及已存在的 `ui/prompts` 目录。开发环境目录、缓存及 `.pyc/.pyo` 不进入发布包。配置 UI 字段必须对应 `configSchema` 中的属性（除非允许额外属性），已声明的默认值也须满足对应 schema；无需为尚待用户填写的必填项伪造默认值。

Git 支持公开 GitHub/Gitee 仓库，分支或标签固定到 commit，仓库须包含完整可安装内容；React 提交预构建 `ui/`。构建哈希覆盖源码、依赖锁、UI 与资源，同版本号也能更新。

更新先准备候选环境并校验保留配置，失败保留旧安装。切换前核对在途流程；BUSY 时等待结束或明确取消后重试。保留数据、配置和有效授权，新权限重新审阅；新构建初始化失败进入 error，中断业务不自动重放。

安装预览绑定当前实例及配置版本；预览后配置、授权或安装发生变化时，须重新读取并审阅，避免旧预览恢复已经撤销的授权。更新插件代码保留已有整理/STRM 方案的授权版本；方案本身变化需要在设置中重新审阅保存。通过 ZIP 更新保留已有仓库地址和分支，内容不同的 ZIP 不沿用旧仓库提交号。

准备候选阶段可以取消；进入停止实例及切换文件阶段后，取消入口会锁定并等待最终结果。安装记录与数据归属以事务保存；切换或保存失败会恢复原文件、Python 环境及安装记录。恢复本身失败会明确返回 `INSTALL_ROLLBACK_FAILED`，不会报告“原构建保留”。候选环境不会在在途流程检查前覆盖当前环境。

插件代码损坏或配置校验失败时，配置内容不变的纯撤权和停用仍可保存并中断在途流程。撤销同时新增授权、修改配置或启用插件的请求继续执行完整校验；不能借撤权绕过在途流程检查。已生效的撤权不会因后续初始化失败而撤销。

### 修复已安装文件

不要直接修改已安装目录中的 `plugin.json`、源码或 `release.json`。修改插件应重新打包并更新，同版本号的新构建也受支持。

安装文件损坏或缺失时，在更新入口重新读取可信原 ZIP 或原仓库提交；同一构建会显示“修复安装”。预检保存独立候选，不修改正在使用的文件。确认后先校验候选包、依赖和已保存配置；没有在途流程时停止实例，再恢复经过哈希校验的文件。修复保留实例、数据、配置、授权、启停状态、来源和回退记录，不自动信任现场改动、不重算受损文件的哈希。不同构建使用普通更新流程。

`PACKAGE_INTEGRITY_FAILED` 表示安装文件完整性失败；`MANIFEST_MISMATCH` 在安装阶段列出未注册/未声明的 action、query 或 continuation。上传预检始终不运行插件代码。ZIP 拒绝信息包含条目路径或不安全条目的索引；配置错误包含 `config.groups[1].name` 这样的路径，不公开配置值和原始系统异常。详细 Python 异常仍在插件安装日志中。

## 维护文档与开发 Skill

在 MediaTidy 根目录使用 Python 3.12 执行：

```powershell
# 生成契约/客户端，复制正文、公开 SDK、模板并生成宿主内嵌 Skill ZIP
python scripts/sync-plugin-sdk.py
python scripts/sync-plugin-sdk.py --check
node scripts/check-plugin-docs.cjs

# 同步到指定 Wiki；--check 只验证，不写文件
node scripts/sync-plugin-wiki.cjs C:/path/to/MediaTidy-Wiki
node scripts/sync-plugin-wiki.cjs C:/path/to/MediaTidy-Wiki --check
```

只检查契约可执行 `python scripts/generate-plugin-sdk.py --check`；只检查 ZIP 可执行 `python scripts/package-plugin-development.py --check`。能力表、客户端、运行时快照和 Skill references 均从源文件生成，不分别手改。

Wiki 同步更新插件正文、下载资源及同一份 ZIP，清理已淘汰的 JS SDK 类型文件；新增章节的导航由 Wiki 的 `docs/.vitepress/config.ts` 维护。主源码、SDK 文档和 Skill 文档使用不同相对路径，交给同步器转换。

同步后在 Wiki 根运行 `npm run build`，确认导航、下载路径及 VitePress base 正确。同步和构建不会提交、推送或部署站点。当前没有旧 Goja 运行器、TSX 声明式作者 CLI、本机目录热同步或独立页面预览流程。
