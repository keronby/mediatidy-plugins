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

Git 支持公开 GitHub/Gitee 仓库，分支或标签固定到 commit，仓库须包含完整可安装内容；React 提交预构建 `ui/`。构建哈希覆盖源码、依赖锁、UI 与资源，同版本号也能更新。

更新先准备候选环境并校验保留配置，失败保留旧安装。切换前核对在途流程；BUSY 时等待结束或明确取消后重试。保留数据、配置和有效授权，新权限重新审阅；新构建初始化失败进入 error，中断业务不自动重放。

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
