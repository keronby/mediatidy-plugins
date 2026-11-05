# MediaTidy 插件开发官方指南汇总（Python SDK v2 最新版）

> **文档说明**：本指南完整同步自 MediaTidy 官方最新 Wiki 仓库（`MuRongYun8/MediaTidy-Wiki/docs/plugins`）。
> **对应版本**：Python SDK `2.0.0`，`sdkVersion: "2"`，`ui.version: "2"`。
> **同步日期**：2026-10-28（全量 19 篇核心指南完整无损聚合）。

## 目录导航

- [01. 概述与核心架构](#01-概述与核心架构)
- [02. 插件中心使用指南](#02-插件中心使用指南)
- [03. 快速开始：第一个 Python 插件](#03-快速开始：第一个-python-插件)
- [04. 清单与数据契约 (manifest / plugin.json)](#04-清单与数据契约-(manifest--pluginjson))
- [05. 双模式 UI（Python AST 与 React 模块联邦）](#05-双模式-uipython-ast-与-react-模块联邦)
- [06. 表格行内操作与分键分页 (storage-table)](#06-表格行内操作与分键分页-(storage-table))
- [07. 业务接口总览](#07-业务接口总览)
- [08. 能力清单与方法参考 (capabilities)](#08-能力清单与方法参考-(capabilities))
- [09. 权限体系与配额模型 (permissions)](#09-权限体系与配额模型-(permissions))
- [10. Emby 配置与通用 API](#10-emby-配置与通用-api)
- [11. 生命周期与后台续接 (automation & tasks)](#11-生命周期与后台续接-(automation--tasks))
- [12. 存储与状态规范 (storage / KV)](#12-存储与状态规范-(storage--kv))
- [13. 日志规范 (logs)](#13-日志规范-(logs))
- [14. 工作流容量设计](#14-工作流容量设计)
- [15. AI 识别整理官方工作流参考](#15-ai-识别整理官方工作流参考)
- [16. 下载与使用 Skill 开发包](#16-下载与使用-skill-开发包)
- [17. 官方开发工具链 (tooling / check / pack)](#17-官方开发工具链-(tooling--check--pack))
- [18. 验收与排错全书（常见问题与错误代码速查）](#18-验收与排错全书常见问题与错误代码速查)
- [19. 宿主方法实现状态与对照表](#19-宿主方法实现状态与对照表)

---

# 01. 概述与核心架构

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


---

# 02. 插件中心使用指南

# 插件中心使用指南

插件中心提供上传 ZIP、从 Git 安装、开发文档与 Skill 下载。初始发现目录为空，不会自动安装 AI 或其他示例。只安装你信任的插件及依赖：Python 有原生访问能力，React 页面与主站共享 JavaScript 环境。

## 安装与启用

1. 使用作者提供的插件 ZIP，或填写 GitHub/Gitee **公开仓库根地址**。分支或标签留空时读取默认分支，也支持完整 commit；宿主固定来源提交。
2. 读取插件信息，审阅名称、版本、来源、构建哈希、能力及资源需求。开发 Skill ZIP 和 MT 主仓库本身不是可安装插件。
3. 确认安装后，宿主异步准备 Python 依赖。界面展示阶段与日志，可取消，失败后可重试。
4. 首次安装完成默认停用。在「设置与授权」配置能力、目录、方案及密钥，再启用。
5. 启用时初始化常驻 worker。失败时查看日志、修正原因后重试；先运行小范围操作，再核对最终任务结果。

SDK 能力与目录读、写、删除权限分别控制。`organize.manual.run` 可以按插件传入的作品和季集直接整理，可能按已授权方案移动、覆盖或清理文件。草稿路线先产生草稿，再由宿主审阅确认。自动整理需要额外的自动执行方案授权，方案变化后重新审阅。

## 页面、任务与日志

Python 与 React 页面均嵌入宿主外壳。加载失败时，设置、授权、密钥、日志、历史和任务仍可使用。页面配置编辑是草稿，必须显式保存。

`queued` 表示已排队，`waiting` 表示插件在等待业务任务，都不等于完成。以逐文件结果和宿主任务最终状态判断成功。结果未知时先核对原任务，避免再次移动或写入同一文件。

暂停触发器停止未来触发；取消插件流程停止当前调用及续接；取消业务任务针对已入队的整理、STRM 或文件任务。已完成的文件操作不会回滚，停用插件不等于撤回已交给宿主的任务。

## 定时、更新与数据

新建定时和订阅默认暂停，由用户检查时间、参数、方案和资源后启用。重新初始化使用稳定 key 复用登记并保留用户暂停状态。

更新支持同版本号的新构建。依赖准备或配置校验失败保留旧安装；在途流程需等待完成或主动取消后重试。更新保留配置、数据及仍有效授权，新能力需重新授权。新构建初始化失败进入错误状态，不自动重放中断业务。

「数据与存储」管理业务 KV、运行历史、导出与恢复。清空/恢复会检查活动或结果未知任务；备份不含密钥与授权，也不包含插件 `data_dir` 文件。卸载默认保留业务数据，不删除插件此前写入授权媒体目录的用户文件。详见[存储](storage.md)。

旧 JS/Goja 入口和 UI v1 不符合当前 Python 清单。保留旧数据不代表自动迁移代码、授权或执行，应由作者提供当前运行时可安装的版本。开发请使用[完整 Skill](downloads.md)。

## 配置修复与授权审阅

安装检查后会展开配置与授权，并持续显示已选能力数量、未授权能力和安装后状态。首次安装保持停用；不会默认全选能力，也不会自动授权目录或 Emby 服务器。初始化因缺少权限失败时，可在宿主设置补齐所需授权后重试。

只有 `configSchema` 的插件可以在安装、升级及「设置与授权」编辑完整 JSON 配置；同时有旧式字段表单时可切换到 JSON 编辑。JSON 或字段校验失败会保留草稿并阻止保存，服务端仍校验最终配置。插件工作台失败不妨碍进入宿主设置、日志或修复安装。包完整性错误需要重新检查原包并使用修复安装，不能靠保存配置绕过。

升级自动移除从旧版本继承、而新版已不再声明的权限和失去用途的资源授权。新能力仍需审阅选择；未知权限及非法资源不会被自动接受。

单条安装记录损坏时，插件中心显示诊断，其余正常插件仍可使用。原始记录与业务数据保留，不会自动删除或恢复出厂。数据库整体不可读仍会报告列表加载失败。


---

# 03. 快速开始：第一个 Python 插件

# 快速开始：第一个 Python 插件

准备 Python **3.12** 和 uv **0.6.7**。默认 Python 页面不需要 Node；React 页面另需 Node **22.12+**。先从[开发资源](downloads.md)取得完整 Skill，或使用主仓库的 `plugin-sdk/`。

## 选择模板

| 示例 | 主仓库位置 | Skill 包中的位置 |
|---|---|---|
| 目录扫描与任务续接 | `plugin-sdk/starter/` | `assets/plugin-sdk/starter/` |
| Pillow 图片处理 | `plugin-sdk/examples/pillow/` | `assets/plugin-sdk/examples/pillow/` |
| React 交互页面 | `plugin-sdk/examples/react/` | `assets/plugin-sdk/examples/react/` |
| Table 行内操作与分键分页 | `plugin-sdk/examples/storage-table/` | `assets/plugin-sdk/examples/storage-table/` |

把所需模板复制到自己的工作目录，修改 `plugin.json` 的 `id/name/author/description`。保留 `entry: "plugin.py:Plugin"`、`sdkVersion: "2"` 和 UI v2 声明。React 模板的本地前端依赖需要一起复制或重新指向公开前端 SDK，见 [UI](ui.md)。

目录模板是小规模教学示例，将报告放在单个 KV 值中。扩展到大量文件时应改成分键存储与分页查询，遵守[工作流容量](workflow-capacity.md)，不要把模板当成无上限目录索引器。

## 准备作者环境

在复制后的插件目录执行（PowerShell）：

```powershell
uv sync --python 3.12
# 替换为本机主仓库的 plugin-sdk，或解压 Skill 的 references/sdk
$sdkPath = 'C:/path/to/mediatidy-plugin-development/references/sdk'
uv pip install --python .venv/Scripts/python.exe "$sdkPath[dev]"
uv run --no-sync python -m mt_plugin check
uv run --no-sync python -m mt_plugin pack
```

Linux/macOS 对应命令：

```bash
uv sync --python 3.12
sdk_path=/path/to/mediatidy-plugin-development/references/sdk
uv pip install --python .venv/bin/python "${sdk_path}[dev]"
uv run --no-sync python -m mt_plugin pack
```

SDK 只安装在作者环境，**不要**用 `uv add mt-plugin` 把它写入插件运行依赖或锁文件。`--no-sync` 防止 uv 移除额外安装的作者工具。`check` 和 `pack` 都会更新锁文件、导入作者入口并运行 mypy，只用于你信任的源码。

## 编写入口

以下展示处理函数形状；`inspect`、`summary` 还需要在清单的 `actions`、`queries` 中声明，配套清单见[清单与参数](manifest.md)。

```python
from mt_plugin import Plugin as BasePlugin, Context, action, query, ui

class Plugin(BasePlugin):
    def on_init(self, ctx: Context):
        self.count = 0
        ctx.mt.log(level="info", message="插件已初始化")

    @action("inspect")
    def inspect(self, input: dict, ctx: Context):
        self.count += 1
        return {"count": self.count}

    @query("summary")
    def summary(self, input: dict, ctx: Context):
        return {"count": self.count}

    def get_page(self, ctx: Context):
        return ui.page(ui.Button(
            label="检查", onClick=[{"type": "invoke", "actionId": "inspect"}]
        ))
```

对象会复用，进程重启会丢失内存状态。需要持久化时声明并使用 `storage`。业务和读取通道可能同时访问对象，涉及共享状态时由作者处理一致性。文件选择由宿主放入 `ctx.files`，不是任意绝对路径参数。

## 打包与验证

发布包根目录至少包含 `plugin.json`、`plugin.py`、`pyproject.toml`、`uv.lock`。`pack` 输出 `dist/<插件ID>-<版本>.zip`。其他 Python 模块、提示词和资源应加入 `[tool.mt-plugin].include`；目录被递归收集。

使用 MT「上传 ZIP」读取清单，检查授权后安装。依赖准备完成后仍需配置、启用，再执行小规模测试并查看最终任务状态。也可提交完整可安装内容到公开 GitHub/Gitee 仓库；React 必须携带预构建 `ui/`。开发 Skill ZIP 不是插件安装包。

开发机检查不能代替宿主检查、目标平台依赖安装或实际页面验证，完整步骤见[验收](validation.md)。


---

# 04. 清单与数据契约 (manifest / plugin.json)

# 清单、配置与调用参数

`plugin.json` 声明安装身份、入口、动作、查询、权限和 UI 模式，与 `plugin.py` 中的装饰器必须一致。宿主在执行前再次验证输入和输出。

## 最小清单

下面清单可与快速开始的 `inspect` / `summary` 示例配套：

```json
{
  "id": "example.counter",
  "name": "计数示例",
  "version": "0.1.0",
  "sdkVersion": "2",
  "entry": "plugin.py:Plugin",
  "permissions": [],
  "config": [],
  "actions": [{
    "id": "inspect", "label": "检查",
    "inputSchema": {"type": "object", "properties": {}},
    "outputSchema": {
      "type": "object", "properties": {"count": {"type": "integer"}},
      "required": ["count"]
    }
  }],
  "queries": [{
    "id": "summary", "label": "计数",
    "inputSchema": {"type": "object", "properties": {}},
    "outputSchema": {
      "type": "object", "properties": {"count": {"type": "integer"}},
      "required": ["count"]
    }
  }],
  "triggers": [],
  "ui": {"mode": "python", "version": "2"}
}
```

插件 ID、动作 ID 和触发器 key 使用小写字母开头的 3–80 位标识，只含小写字母、数字、点和连字符。内容变化会产生新构建哈希，即使作者没有改变 `version`。动作 ID 与查询 ID 不能重复。

## 字段与入口对应

| 清单字段 | 用途 |
|---|---|
| `actions` | 公开 `@action("id")`；`kind` 缺省或为 `command` |
| `actions[].kind: "continuation"` | 内部 `@continuation("id")`，只由宿主完成事件续接 |
| `queries` | `@query("id")`，不能调用写入能力 |
| `config` / `configSchema` | 配置表单与有界 JSON Schema |
| `actions[].fields` | 表单支持 `text/textarea/number/boolean/files/profile/stringList` |
| `inputSchema` / `outputSchema` | 动作或查询的数据校验 |
| `secrets` | 单独声明密钥 `key/label`，不放普通配置默认值 |
| `triggers` | 稳定 `key`、`type: schedule或event` 和公开 `action` |
| `ui` | Python：`mode/version`；React：额外 `entry: "ui/remoteEntry.js"` |

`ui.icon` 和 `ui.assets` 使用包内 `ui/` 下的相对路径，资源须随包携带，不重复声明同一路径。动作最多 50 个，配置和单个动作表单字段最多 30 个，密钥最多 30 个。

Schema 支持 `object/array/string/number/integer/boolean/null`、`properties/required/items/enum` 以及数值、长度、项数边界；对象默认拒绝未知字段。以 [schema.py](/downloads/plugins-sdk-v2/schema.py) 的子集为准，不套用任意完整 JSON Schema 语法。

文件描述在 `ctx.files`，不要将伪造的引用或宿主绝对路径塞进普通 `input`。静态 schema 通过后，宿主还会独立调用 `validate_config(config, ctx)`；此函数只做校验，不初始化业务或调用 SDK。

## Python 名称与 JSON 名称

```python
# 顶层关键字转换为 rootId、videoOnly、idempotencyKey。
task = ctx.mt.files.scan(
    root_id="selected-root", path="videos", video_only=True,
    idempotency_key="stable-business-key"
)
# 嵌套字典保持宿主 JSON 字段名，不做递归转换。
ctx.mt.tasks.wait(refs=[task["ref"]], completion={
    "action": "collect-report", "input": {}
})
return {"taskRef": task["ref"]}
```

也可将完整 camelCase 字典作为第一个位置参数。不要混用字典和普通关键字拼接参数：通用客户端提供字典时使用该字典。异步函数使用 `await ctx.amt...`。异常为 `SDKError`，用 `code` 与 `retryable` 判断，不解析中文消息。

## 升级时的配置

默认保留已有配置。字段声明 `upgradeDefault: true` 时，只有已保存值等于旧默认值、且新清单仍有默认值，才采用新默认值；用户自定义值保留，安装请求显式配置优先。当前 AI 示例用这一机制更新默认提示词。


---

# 05. 双模式 UI（Python AST 与 React 模块联邦）

# 双模式 UI

清单声明 `ui.mode = python | react`。模式不能被损坏页面的隐式回退替换。设置、启停、授权、密钥、日志、任务、历史及整理草稿审阅由宿主外壳提供。

## Python 页面

`get_page(ctx)` 使用 `mt_plugin.ui` 返回 MT UI v2 JSON。页面生成是只读调用，每次返回都由宿主校验组件、属性、绑定、事件和规模。普通 Python 函数可组合可复用组件：

```python
from mt_plugin import ui

def results():
    return ui.Card(ui.Text(text=ui.ref("run.progress.message")),
                   ui.Button(label="刷新数据", onClick=[{"type":"query", "queryId":"history"}]))

def get_page(ctx):
    return ui.page(results(), queries=[{"id":"history", "name":"history"}])
```

布局包括 Page、Section、Stack、Grid、Card、Divider、Tabs/Tab、Details、Dialog、Drawer；内容包括 Text、Markdown、Image、Badge、Alert、Metric、Progress、Steps、EmptyState；输入包括 Input、Textarea、Select、Checkbox、FilePicker、ProfilePicker；数据包括 Repeat 和 Table（筛选、排序、分页、跨页选择、移动端卡片）。宿主插槽包括 ConfigForm、DraftReview、RunHistory、RunLog、TaskList、ScheduleForm、EventSubscriptions、DataManagement。

`input`、`config`、`state` 是独立可写空间；config 只是草稿，必须显式保存。`plugin`、`run`、`flow`、`stats`、`query.<id>` 和 `row` 只读。`ui.ref(path)` 绑定值，`ui.expr(op, *args)` 使用有界受控表达式；visible/disabled 可条件绑定，Repeat 使用数据循环。

### 默认值、目录与执行反馈

表单默认值放在 `ui.page(..., state={...})` 中；Input 继续使用 `bind`，不需要额外的 `value` 属性。读取 Storage 时先判断 `found`，不要把用户保存的 `0`、`False` 或空字符串误当作缺失值。

```python
from mt_plugin import ui

def get_page(ctx):
    return ui.page(
        ui.Input(label="分差阈值", type="number", bind="state.threshold"),
        ui.DirectoryPicker(label="扫描目录", bind="state.source"),
        ui.DirectoryPicker(label="转移目标", bind="state.target", access="write"),
        ui.Button(label="开始扫描", onClick=[{
            "type": "invoke", "actionId": "scan",
            "input": {"source": ui.ref("state.source"), "threshold": ui.ref("state.threshold")},
        }], loading=ui.expr("or", ui.expr("eq", ui.ref("run.status"), "queued"),
                           ui.expr("eq", ui.ref("run.status"), "running"))),
        state={"threshold": 10, "source": None, "target": None},
    )
```

`scan` 需在清单声明，并使用 inputSchema 接收这些字段。目录选择结果为 `{rootId, path}` 或 `null`，`path` 相对授权根，根目录本身为 `.`。组件只浏览已授权且可读取的现存目录；`access="write"` 还要求 `files.write` 能力与该根的写入授权。确认时再次检查目录存在性与当前权限。取消不修改绑定值；清除写入 `null`；不创建目录或返回宿主绝对路径。

目录应通过 `ctx.mt.files.roots()` 动态发现，由用户选择。不要硬编码 NAS 绝对路径，也不要假定第一个根就是 STRM 输出目录。没有授权根时，引导用户到宿主「设置与授权」配置。

Button 点击请求期间自动加载并禁用，提交锁覆盖整组事件。`loading` 接受布尔值或表达式，可绑定长期任务状态；加载结束不代表后台业务完成。失败后恢复交互，业务并发限制仍由宿主执行。`DirectoryPicker` 与 `Button.loading` 需要包含本次交互优化的宿主，早期 SDK v2 宿主会拒绝未知组件或属性。

单选 `Select` 的选项可包含 `description`；提供说明时渲染为可点击的选项卡片，值仍使用原始 `value`（包括布尔值），未提供说明时保留下拉选择。

onClick 支持 set、invoke、query 和 host 事件。host 支持 saveConfig、refreshRun、refreshPage、cancelRun、newRun、openLogs、reviewDrafts。普通输入只更新前端状态；数据通过查询与运行结果更新，页面结构由“刷新页面”显式重新读取 Python。

`stats.selections` 是文件选择器当前勾选的文件或目录数量；`stats.files`、`stats.drafts` 是当前运行的文件和草稿数量，可用于按钮可用状态与空状态提示。

单页最多 256 KiB、500 个组件、深度 12，表格最多 12 列。图片可使用清单声明的 UI 资源路径，或 `file:<宿主文件引用>`；后者只预览授权的 PNG、JPEG、WebP，最多 8 MiB。禁止任意网页地址。组件属性见 [注册表](/downloads/plugins-sdk-v2/ui-registry.json)。

### 校验诊断与按钮尺寸

未知组件、属性、事件和绑定仍会被拒绝。错误会包含具体路径，例如 `content[0].props.columns[1].cells[0].props.size`，以及允许的属性或枚举值；修正后点击宿主“刷新页面”重试。插件页面失败时，宿主设置、授权、日志和任务入口仍可使用。

`Button.size` 支持 `default`、`sm`、`lg`、`icon`，默认 `default`；表格行内按钮可用 `sm`。该属性需要包含插件开发者反馈修复的宿主版本，早期 SDK v2 宿主会拒绝它。`icon` 使用紧凑方形按钮，仍应提供可读的 `label`。

### Table 行内操作

列定义使用 `label`、可选的 `field` 和必填的 `cells`。普通文本列写 `cells: []`；操作列把组件放入 `cells`。不使用其他框架的 `title`、`dataIndex` 或 `key`。

```python
from mt_plugin import ui

def get_page(ctx):
    return ui.page(
        ui.Table(source=ui.ref("query.summary.data.groups"), rowKey="id", columns=[
            {"label": "作品名称", "field": "name", "cells": []},
            {"label": "操作", "cells": [ui.Button(
                label="标记保留", size="sm", variant="outline",
                onClick=[{"type": "invoke", "actionId": "decide",
                          "input": {"id": ui.ref("row.id")}}],
            )]},
        ]),
        queries=[{"id": "summary", "name": "summary", "input": {}}],
    )
```

查询响应由宿主包装为 `{data, loading, error}`，因此数据从 `query.summary.data` 读取。按钮所在行通过只读 `row` 传入事件，`row.id` 不需要复制到全局 state。

清单必须声明公开动作 `decide`，其 `inputSchema` 包含必填字符串 `id`，并声明查询 `summary`；Python 中分别注册 `@action("decide")` 和 `@query("summary")`。包含清单、输入模型、分页查询及修订检查的[完整示例](/downloads/plugins-sdk-v2/examples/storage-table/plugin.py)可直接打包运行，安装说明见[示例 README](/plugins/storage-table)。

`invoke` 等待动作入队，不等待业务执行结束；紧接着触发 `query` 可能读到旧结果。宿主会在当前运行结束后刷新查询，也可以提供手动刷新按钮。删除等实际文件操作仍须通过对应授权能力实现，此示例的“保留”只修改演示结果标记。

## React 页面

参考 `plugin-sdk/examples/react`；Skill 中对应 `assets/plugin-sdk/examples/react`。只有这类作者需要 Node 22.12+。通过 `@module-federation/vite` 构建并导出 `./Page`，发布包包括 `ui/remoteEntry.js`、模块、CSS 和资源。宿主使用配套 federation runtime 按实例与构建哈希加载。

完整复制 Skill 的 assets 目录后，示例 frontend 的 `file:../../../../plugin-frontend-sdk` 可以直接解析。如果只搬走 React 项目，应在 `frontend/package.json` 重设 `@mediatidy/plugin-ui` 本地路径并更新 package-lock，再运行 `npm ci`。前端依赖安装完成后回到插件根目录运行 `python -m mt_plugin pack`；它会调用前端 `npm run build`。Git 安装必须提交构建好的 `ui/`，宿主不会运行 npm。

共享 React、React DOM、JSX runtime 以及公开的 `@mediatidy/plugin-ui`，必须配置 singleton，示例使用 import:false 从宿主获取实现。公开 SDK 在 `plugin-frontend-sdk`，模板不引用主站内部源码。CSS Modules 继承主站主题变量；第三方组件随作者构建携带，MT 容器不安装 Node。

页面获得 `mt`：实例状态、主题、语言、尺寸；runAction、query、saveConfig、selectFiles、selectProfile、previewUrl、openLogs、openTasks、openHistory、reviewDrafts、refreshPage。runAction 返回排队的运行引用，最终结果在宿主任务和运行记录中核对。

资源会话由登录用户申请，按用户、实例与构建绑定，使用限定路径的 HttpOnly Cookie，5 分钟到期并由页面续期。资源 URL 不含主站 JWT，响应 private/no-store，PWA 排除私有资源。构建切换使用新模块标识并卸载旧页面；组件应清理自身订阅。加载和渲染错误由宿主错误边界显示，管理入口保留。

React 插件与主站共享 JavaScript 环境，是受信任代码；Python 及其库同样有原生访问能力。


---

# 06. 表格行内操作与分键分页 (storage-table)

# 分键分页与行内操作

使用 Python 3.12 与同版本 SDK，执行 `python -m mt_plugin pack` 后上传 ZIP。
启用时仅需授予 `storage`。需要支持 `Button.size` 的宿主反馈修复版本。

点击“生成 120 组演示结果”，完成后逐页查看并标记保留。示例使用合成数据，不访问媒体目录，不删除文件。

每次扫描使用运行 ID 隔离结果，一组一个键，另存状态摘要。查询通过 `storage.list` 游标读取 20 条，超过返回容量限制时以实际返回条数和 `nextCursor` 为准。
写入失败会保留部分结果并抛出错误；状态标记也失败时仍显示“尚未完成”，需查看宿主运行状态。标记操作用修订号防止覆盖并发修改。

`invoke` 只等待动作入队；宿主在当前运行结束后刷新查询。按钮事件后直接追加 `query` 可能先读到旧值。
查询结果位于 `query.summary.data`，当前表格行位于 `row`。

示例保留旧扫描数据供排错；反复运行仍受实例配额约束，可在宿主“数据与存储”清理。
真实插件应提供保留策略：对已结束且不再展示的扫描，按旧扫描前缀调用 `storage.list(values=False)`，逐页将键交给 `storage.batch` 删除，每批最多 100 项，最后删除摘要；禁止清理仍在运行或正在展示的扫描。

## 示例源码

- [plugin.py](/downloads/plugins-sdk-v2/examples/storage-table/plugin.py)
- [plugin.json](/downloads/plugins-sdk-v2/examples/storage-table/plugin.json)
- [pyproject.toml](/downloads/plugins-sdk-v2/examples/storage-table/pyproject.toml)
- [uv.lock](/downloads/plugins-sdk-v2/examples/storage-table/uv.lock)


---

# 07. 业务接口总览

# 业务、文件与任务能力

同步调用使用 `ctx.mt`，异步使用 `await ctx.amt`。方法、权限、限额及允许通道见[能力清单](capabilities.md)，输入输出以 [methods.json](/downloads/plugins-sdk-v2/methods.json) 为准。Python 顶层关键字转 camelCase，嵌套字典保持 JSON 字段名。

## 如何选择能力

| 业务 | 能力与边界 |
|---|---|
| 运行状态 | `runtime.info/resources/capabilities`；`checkpoint` 检查取消；`progress/result` 发布进度和部分结果 |
| 文件 | `roots/list/stat/read_text`；后台 `scan/search`；按授权执行复制、移动、删除和写入 |
| 系统、规则与历史 | `system.info/config` 返回公开配置；`rules`、`records` 读取允许的规则和业务记录 |
| 媒体与 TMDB | `media.parse/recognize`；`tmdb` 的搜索、详情及季集查询，互相不是必经步骤 |
| 整理 | `organize.run` 自动方案；`organize.manual.run` 指定作品直接执行；`manual.prepare/prepare_batch` 草稿 |
| STRM | 方案读取、预览、生成、核验和清理；核验与清理不可当成只读查询 |
| 质量评分 | `quality.prepare/evaluate/evaluate_batch/compare/upgrade`；仅业务结论，不导出 FF 探测明细 |
| 本地 FF 缓存 | `ffprobe.cache_exists`；按完整 SHA1 仅查询本地缓存记录是否存在 |
| AI | `ai.capabilities/complete/chat/web.search`；使用用户配置的服务，模型输出不产生权限 |
| 外部通信 | `http.request` 按目标/路径/方法授权，`notification.send` 按通知通道授权 |
| 持久数据与自动化 | `storage`、`tasks`、`triggers`，见各专题 |

`config.get` 是当前插件配置，`system.config` 是公开业务配置，授权方案通过 `organize.profile/strm.profile` 读取，不提供任意数据库配置入口。

## 文件选择与分页

选择器返回文件或目录描述到 `ctx.files`，选择目录不会隐式展开全部文件。逐页浏览用 `files.list`；递归扫描用 `files.scan`，等待扫描任务后分页读取 `tasks.items`。列表和任务明细单页最多 100，完整工作流最多 500，底层扫描另有容量限制，见[大批量流程](workflow-capacity.md)。

文件变更默认不覆盖；跨目录操作检查源与目标授权及文件版本。根目录不可删除，递归删除须显式指定。宿主遵循路径路由、限流和取消语义；不支持的云盘操作返回 `UNSUPPORTED`，不要通过直接拼接路径绕开检查。

## 查询本地 FF 缓存是否存在

清单声明 `"permissions": ["ffprobe.cache.exists"]`，用户单独授予该能力后，可在 action、query、page 通道调用；每次调用一个 SHA1，每运行最多 500 次。此方法需要包含该能力的宿主版本，旧宿主不能调用。

```python
exists = ctx.mt.ffprobe.cache_exists(sha1="0123456789abcdef0123456789abcdef01234567")
# 异步调用：exists = await ctx.amt.ffprobe.cache_exists(sha1=sha1)
```

返回 Python `bool`（JSON `true/false`），不返回 FF 数据、文件名、路径或其他缓存字段。SHA1 必须为完整的 40 位十六进制字符串，大小写均可；不支持前缀、批量、列表或导出。该授权用于查询整个本地整理 FF 缓存的存在性，不依赖文件或整理方案授权。

这里只判断本地数据库是否有记录；关闭整理缓存使用后已有记录仍可查到。`True` 不保证探测数据完整、版本兼容或对应文件仍存在。查询不会访问远程 FF 缓存、运行 ffprobe、回填数据或更新缓存时间。查询失败抛出 SDK 错误，不要将错误捕获后当成 `False`。

## 三种整理路线

| 路线 | 调用 | 后续 |
|---|---|---|
| 让已有方案识别整理 | `organize.run` | 授权自动方案按自身识别、命名、覆盖和清理策略执行 |
| 插件已确定作品 | `organize.manual.run` | 逐文件直接入队，不再要求草稿确认 |
| 需要用户审阅 | `organize.manual.prepare/prepare_batch` | 宿主 DraftReview 编辑、核验并确认，准备本身不等于入队 |

直接整理的示例片段（还需清单声明相应能力、动作与内部续接）：

```python
task = ctx.mt.organize.manual.run(
    file_ref=file["ref"], profile_id=profile_id,
    tmdb_id=tmdb_id, media_type="tv", season=1, episode=3,
    idempotency_key=f"{ctx.flow_id}:{file['ref']}"
)
ctx.mt.tasks.wait(refs=[task["ref"]], completion={
    "action": "collect-result", "input": {}
})
return {"taskRef": task["ref"], "status": "waiting"}
```

直接路线检查文件引用、方案授权、修订及参数，不替作者重新识别作品；实际整理仍读取命名与元数据所需的 TMDB 资料，并执行季集和封存保护等业务校验。混合作品逐文件传作品及季集，不能把整个目录绑定为同一作品。

草稿路线需要 `organize.prepare` 及确认所需的 `organize.submit` 授权。**没有**供 worker 调用的 `organize.submit/confirm` 方法，确认在宿主完成。

## 结果、幂等与续接

`queued` 只代表入队，最终任务状态才代表结果。`runtime.result(value=...)` 可以先发布不超过 256 KiB 的部分结果，后续取消或失败仍可查看。任务引用必须使用宿主返回的 ref，不能保存内部队列整数 ID 代替它。

在提交业务前保存稳定幂等键与状态；同一键不得用于不同请求。处于 `submitting/outcome_unknown` 或未确认是否成功提交时，先核对原任务，不能自动重试。取消保留已经完成的操作，不能承诺回滚。等待和续接见[生命周期](automation.md)。

## 二进制与图片

| Python 辅助方法 | 用途 |
|---|---|
| `ctx.download(ref)` | 流式下载授权文件到当前工作目录，返回 Path |
| `ctx.read_bytes(ref)` | 读取最多 8 MiB 内存数据 |
| `ctx.upload(path, destination, idempotency_key=...)` | 把工作文件交给宿主，返回任务引用 |
| `ctx.write_bytes(data, destination, idempotency_key=...)` | 最多 8 MiB 的内存数据写入工作文件并上传 |

异步对应 `adownload/aread_bytes/aupload/awrite_bytes`。`destination` 是 `{"rootId": "...", "path": "relative.png"}`。流式传输上限 256 MiB，不把二进制塞入 JSON RPC；覆盖必须明确 `overwrite` 和目标 `version`。

当前二进制读取、写入和文件图片预览支持本地/FUSE 授权目录；其他未支持路由返回 `UNSUPPORTED`。宿主接管已提交上传的工作文件，随后清理工作目录不会使排队任务丢失数据。Pillow 示例演示读取、缩放、写回、等待任务与文件引用预览。

## AI 调用

`ai.capabilities` 查询可用性，调用不会自动启用模型或联网。普通 `ai.chat` 与单次原生搜索是不同模式；选择 `single_request=True, native_web_search=True` 时还需要联网能力授权和宿主 AI 联网设置。具体协议、错误及示例身份核对见 [AI 工作流](ai-organize.md)。文件名、上下文及查询内容可能发送到用户配置的外部服务。


---

# 08. 能力清单与方法参考 (capabilities)

# SDK v2 能力清单（生成文件）

同步客户端为 `ctx.mt`，异步客户端为 `ctx.amt`。参数可传 JSON 字典，或使用 snake_case 关键字；宿主契约中的 JSON 字段使用 camelCase。输入输出的完整 JSON Schema 见 [语言无关契约](/downloads/plugins-sdk-v2/methods.json)。

每次调用重新验证实例、构建、配置修订、资源、授权与配额。`organize.submit` 仍是宿主确认草稿所需的权限，不是可调用的 SDK 方法。

| Python 方法 | 权限 | 每运行上限 | 允许通道 |
|---|---|---:|---|
| `emby.servers.list` | emby.api | 500 | action, query, page |
| `emby.request` | emby.api | 500 | action |
| `ffprobe.cache_exists` | ffprobe.cache.exists | 500 | action, query, page |
| `ai.capabilities` | — | 500 | action |
| `ai.chat` | ai.chat | 78 | action |
| `ai.complete` | ai.complete | 32 | action |
| `ai.web.search` | ai.web.search | 32 | action |
| `config.get` | — | 500 | action, query, init, stop, page |
| `files.batch` | files.write | 500 | action |
| `files.copy` | files.write | 500 | action |
| `files.delete` | files.delete | 500 | action |
| `files.download` | files.read | 100 | action |
| `files.list` | files.read | 500 | action, query, page |
| `files.mkdir` | files.write | 500 | action |
| `files.move` | files.write | 500 | action |
| `files.read_text` | files.read | 500 | action, query, page |
| `files.rename` | files.write | 500 | action |
| `files.roots` | files.read | 500 | action, query, page |
| `files.scan` | files.read | 500 | action |
| `files.search` | files.read | 500 | action |
| `files.stat` | files.read | 500 | action, query, page |
| `files.upload` | files.write | 100 | action |
| `files.write_text` | files.write | 500 | action |
| `http.request` | http.request | 50 | action |
| `log` | — | 20000 | action, query, init, stop, page |
| `media.parse` | media.parse | 500 | action |
| `media.recognize` | media.recognize | 100 | action |
| `notification.send` | notification.send | 10 | action |
| `organize.manual.prepare` | organize.prepare | 500 | action |
| `organize.manual.prepare_batch` | organize.prepare | 100 | action |
| `organize.manual.run` | organize.manual.run | 500 | action |
| `organize.profile` | organize.read | 500 | action, query, page |
| `organize.profiles` | organize.read | 500 | action, query, page |
| `organize.run` | organize.run | 500 | action |
| `quality.compare` | quality.compare | 500 | action |
| `quality.evaluate` | quality.evaluate | 500 | action |
| `quality.evaluate_batch` | quality.evaluate | 500 | action |
| `quality.prepare` | quality.prepare | 500 | action |
| `quality.upgrade` | quality.compare | 500 | action |
| `records.list` | records.read | 500 | action, query, page |
| `rules.effective` | rules.read | 500 | action, query, page |
| `rules.get` | rules.read | 500 | action, query, page |
| `rules.list` | rules.read | 500 | action, query, page |
| `runtime.capabilities` | — | 500 | action, query, init, stop, page |
| `runtime.checkpoint` | — | 5000 | action, query, init, stop, page |
| `runtime.info` | — | 500 | action, query, init, stop, page |
| `runtime.progress` | — | 5000 | action |
| `runtime.resources` | — | 500 | action, query, init, stop, page |
| `runtime.result` | — | 5000 | action |
| `secrets.get` | secrets.use | 30 | action, init, stop |
| `storage.batch` | storage | 500 | action, init, stop |
| `storage.delete` | storage | 500 | action, init, stop |
| `storage.get` | storage | 5000 | action, query, init, stop, page |
| `storage.list` | storage | 500 | action, query, init, stop, page |
| `storage.set` | storage | 5000 | action, init, stop |
| `storage.usage` | storage | 500 | action, query, init, stop, page |
| `strm.cleanup` | strm.cleanup | 10 | action |
| `strm.preview` | strm.read | 500 | action |
| `strm.profile` | strm.read | 500 | action, query, page |
| `strm.profiles` | strm.read | 500 | action, query, page |
| `strm.run` | strm.run | 10 | action |
| `strm.verify` | strm.run | 10 | action |
| `system.config` | system.read | 500 | action, query, page |
| `system.info` | system.read | 500 | action, query, page |
| `tasks.cancel` | tasks.cancel | 500 | action |
| `tasks.get` | tasks.read | 500 | action, query, page |
| `tasks.items` | tasks.read | 500 | action, query, page |
| `tasks.list` | tasks.read | 500 | action, query, page |
| `tasks.wait` | tasks.wait | 500 | action |
| `tmdb.detail` | tmdb.read | 500 | action |
| `tmdb.search` | tmdb.read | 500 | action |
| `tmdb.season` | tmdb.read | 500 | action |
| `triggers.events.create` | events.subscribe | 100 | action, init, stop |
| `triggers.events.delete` | events.subscribe | 100 | action, init, stop |
| `triggers.events.list` | events.subscribe | 100 | action, init, stop |
| `triggers.events.pause` | events.subscribe | 100 | action, init, stop |
| `triggers.events.update` | events.subscribe | 100 | action, init, stop |
| `triggers.events.upsert` | events.subscribe | 100 | action, init, stop |
| `triggers.schedules.create` | triggers.manage | 500 | action, init, stop |
| `triggers.schedules.delete` | triggers.manage | 500 | action, init, stop |
| `triggers.schedules.list` | triggers.manage | 500 | action, init, stop |
| `triggers.schedules.pause` | triggers.manage | 500 | action, init, stop |
| `triggers.schedules.update` | triggers.manage | 500 | action, init, stop |
| `triggers.schedules.upsert` | triggers.manage | 500 | action, init, stop |


---

# 09. 权限体系与配额模型 (permissions)

# 权限与资源

清单声明宿主能力，用户授予实例能力和具体资源。每次 SDK 调用校验许可证、实例、构建、配置修订、状态、资源、授权和配额。RPC 权限来自宿主闭包，插件提交的身份字段不构成授权。

安装即信任 Python、依赖及 React 代码。进程隔离、虚拟环境与 SDK 白名单不限制任意 Python 原生行为，也不隔离 React 和主站 JavaScript。只安装可信作者及依赖。

## 能力和资源分别授权

| 操作 | 需要核对 |
|---|---|
| 读取、复制、写入、删除 | 对应 SDK 能力与目录 read/write/delete；移动检查源和目标 |
| 直接手动整理 | `organize.manual.run`、有效单文件引用和整理方案 |
| 草稿确认 | `organize.prepare/organize.submit`、文件、方案和宿主确认 |
| 自动整理 | `organize.run` 及自动执行方案授权 |
| STRM | 读取、执行、清理分别授权，并限定方案 |
| HTTP 与通知 | 授权目标/路径/方法，或通知通道 |
| Emby 配置与 API | 统一 `emby.api` 能力及具体服务器授权；可查询、修改、删除，MT 自动鉴权，不返回 API Key |
| 原生 AI 搜索 | `ai.chat` 与 `ai.web.search`，以及宿主 AI 联网设置 |
| 方案事件 | `events.subscribe` 及方案事件资源 |
| 本地 FF 缓存存在性 | 单独授权 `ffprobe.cache.exists`；按完整 SHA1 仅返回布尔值，不返回内容 |

SDK 目录授权区分 read/write/delete；文件引用绑定实例与版本，路径必须位于授权根内。宿主数据库、配置、密钥、缓存和日志不属于可授权媒体文件。媒体、AI、HTTP、通知、整理和 STRM 分别授权，前端隐藏按钮不能代替后端校验。

查询和页面的 SDK 通道只允许只读能力；生命周期只允许日志、自身 KV、授权信息、声明密钥及触发器管理；配置校验不允许 SDK 业务调用。这是宿主接口边界，作者也必须遵守只读与纯校验契约。

具体方法能否用于 action/query/init/stop/page，以[能力表](capabilities.md)为准；例如 AI 可用性查询也不能假设可放进页面通道。

Emby 只提供 `emby.servers.list` 与 `emby.request` 两个方法。服务器授权为 `embyServerIds`，旧数据默认空数组；新增能力需明确授权。列表可用于页面和查询，通用请求只用于动作。配置变化实时生效，停用、删除或撤销授权后拒绝请求。详见 [Emby 配置与通用 API](emby.md)。

授权目录中的 STRM、NFO、字幕等按普通文件处理，读取 STRM 可能得到播放地址。授予文件读取和外部服务能力后，不能宣称插件无法传播已读取内容。

## FF 业务评分边界

`quality.prepare/evaluate/evaluate_batch/compare/upgrade` 只接受授权文件与方案，返回评分、资格、完整状态、固定原因和比较结论，不导出远程 FF 数据、任意 SHA1 查询、原始探测字段或宿主凭据。评分计量按实例累计，不能靠清空 KV 重置。

独立的 `ffprobe.cache_exists` 允许查询任意完整 SHA1 在本地整理 FF 缓存中是否有记录，必须单独声明并获得 `ffprobe.cache.exists` 授权。其返回值只有布尔值，不读取或导出探测详情，也不访问远程 FF 服务；旧插件更新时不会自动获得此权限。用法与存在性的含义见[业务接口](business.md#查询本地-ff-缓存是否存在)。

停用、授权撤销或更新使旧 worker 后续 SDK 调用失效；新增权限需重新授权。取消插件流程与取消已提交的宿主任务分别操作。

整理直接路线使用 organize.manual.run；草稿路线使用 organize.manual.prepare/prepareBatch，保留 organize.prepare 与 organize.submit 权限，最终确认在宿主界面完成。没有供 worker 调用的 organize.submit/confirm。


---

# 10. Emby 配置与通用 API

# Emby 配置与通用 API

Emby SDK 只有两个方法：`ctx.mt.emby.servers.list()` 和 `ctx.mt.emby.request(...)`。异步处理函数使用 `await ctx.amt.emby...`。查询媒体库、条目搜索、刷新以及 Emby 扩展插件接口统一通过 `request` 调用。

## 授权与服务器配置

在清单 `permissions` 中声明 `"emby.api"`，再由用户在安装或设置页面勾选“调用 Emby API”和具体服务器。所有操作共用这一个能力，授权范围包括查询、修改、删除媒体及其他服务器管理操作。没有默认授权服务器，旧插件更新也不会自动获得能力或服务器授权。

```python
servers = ctx.mt.emby.servers.list()
if not servers:
    raise ValueError("请先在插件设置中授权 Emby 服务器")
server = servers[0]
```

每个服务器包含以下字段：

| 字段 | 含义 |
|---|---|
| `id` | MT 中的服务器标识，传给 `server_id` |
| `name`、`embyUrl` | 名称与地址；URL 不含凭据、查询参数或片段 |
| `enabled` | MT 中是否启用 |
| `embyServerId` | Emby 自身的服务器标识，与 MT 的 `id` 不同 |
| `pathMappings` | `localPrefix` / `embyPrefix` 路径映射列表 |
| `hasApiKey` | 是否配置 API Key，只有布尔值 |

列表来自 MT 当前配置，不访问远端，只返回本插件已授权的服务器。已停用的服务器仍显示状态，但不能执行请求；已删除的服务器不再返回。每次请求重新读取配置，修改地址或密钥后无需重新填写插件配置。

`servers.list` 可用于 `action/query/page`；`request` 只用于 `action`，包括动作续接。页面需要远端数据时，通过声明的动作加载结果，再显示动作结果或存储数据。`init/stop/validateConfig` 不能调用这两个方法。

## 通用请求契约

```python
result = ctx.mt.emby.request(
    server_id=server["id"],
    method="GET",
    path="/Items",
    query={"ParentId": "library-id", "StartIndex": 0, "Limit": 100},
    timeout_ms=30000,
)
if result["status"] != 200:
    raise ValueError(f"Emby 返回 HTTP {result['status']}")
items = result["data"]["Items"]
```

| 参数 | 规则 |
|---|---|
| `server_id` / JSON `serverId` | 必填，MT 服务器 ID，须已授权、启用并配置 API Key |
| `method` | 默认 GET；支持 GET、HEAD、POST、PUT、PATCH、DELETE，大小写不敏感 |
| `path` | 必填，以 `/` 开头的 API 路径；可带 `/emby` 前缀，MT 避免重复拼接；最多 2048 字节 |
| `query` | 参数字典；值支持字符串、数值、布尔值或这些值的数组；数组变成重复参数。最多 100 个参数，编码后最多 16 KiB |
| `json` | 可选 JSON 请求体，最多 256 KiB；GET/HEAD 不接受请求体 |
| `timeout_ms` / JSON `timeoutMs` | 默认 30000，显式值为 500–60000 毫秒 |

顶层 Python 关键字使用 snake_case；`query` 和 `json` 内部键名按 Emby 接口原样传递，例如 `ParentId`、`Recursive`，不会自动改名。Emby 接口要求逗号分隔时传字符串，例如 `"IncludeItemTypes": "Movie,Series"`。

返回 `{"status": HTTP状态码, "data": 响应内容}`。合法 JSON 解析为 Python 对象，UTF-8 文本保留字符串，空响应为 `None`；响应最多 512 KiB，需通过分页控制结果大小。401、403、404、429、500 等仍作为 HTTP 结果返回，插件必须检查 `status`，不会被伪装成空列表。

每次运行最多调用 `request` 500 次；列表查询另有每运行 500 次上限。支持 HTTP、HTTPS、反代前缀和历史带 `/emby` 的配置，不要求 MT 管理页面使用 HTTPS。

## 常见操作示例

以下代码应放在已声明的动作中，`server_id` 来自已授权列表。

```python
# 查询媒体库
libraries = ctx.mt.emby.request(
    server_id=server_id, path="/Library/SelectableMediaFolders",
)

# 分页搜索电影/剧集；继续查询时增加 StartIndex
page = ctx.mt.emby.request(
    server_id=server_id, path="/Items",
    query={"ParentId": library_id, "SearchTerm": "三体",
           "IncludeItemTypes": "Movie,Series", "Recursive": True,
           "StartIndex": 0, "Limit": 100, "Fields": "ProviderIds,Path"},
)

# 按条目 ID 查询，或按 TMDB ID 筛选
item = ctx.mt.emby.request(server_id=server_id, path="/Items", query={"Ids": item_id})
matched = ctx.mt.emby.request(
    server_id=server_id, path="/Items",
    query={"AnyProviderIdEquals": "tmdb.123", "Limit": 100},
)

# 刷新条目或媒体库；空的成功响应仅表示 Emby 接受请求
from urllib.parse import quote
refresh = ctx.mt.emby.request(
    server_id=server_id, method="POST",
    path=f"/Items/{quote(item_id, safe='')}/Refresh",
    query={"Recursive": True},
)
scan = ctx.mt.emby.request(server_id=server_id, method="POST", path="/Library/Refresh")

# 调用 Emby 上已安装扩展插件的接口时，仍传该接口的 path/query/json；
# 路径和参数须以对应扩展文档为准，MT 不另行封装其业务协议。
```

异步函数中使用相同参数，例如 `await ctx.amt.emby.request(server_id=server_id, path="/Items", query={"Limit": 100})`。

Emby 上游文档：[查询条目](https://dev.emby.media/reference/RestAPI/ItemsService/getItems.html)、[条目刷新](https://dev.emby.media/reference/RestAPI/ItemRefreshService/postItemsByIdRefresh.html)、[整库扫描](https://dev.emby.media/reference/RestAPI/LibraryService/postLibraryRefresh.html)。

## 错误与边界

MT 自动注入 `X-Emby-Token`，不提供自定义鉴权头或 API Key 读取。路径不能是完整 URL，也不能包含查询串、片段、越界或嵌套编码；查询串放入 `query`。不跟随任何重定向，3xx 原样返回状态码，因此密钥不会被转发到跳转目标。

凭据管理和登录接口不开放，包括 `Auth`、`Connect`、`QuickConnect` 路径、`Authenticate*` 及包含 `Password` 的路径段。鉴权 query 参数禁止覆盖。响应中的敏感字段和当前宿主密钥会被脱敏；这些限制是 SDK 接口约束，插件的整体信任边界仍见[权限](permissions.md)。

宿主校验或网络失败抛出 `SDKError`，可读取 `.code`：`PERMISSION_DENIED`、`CONTEXT_DENIED`、`INVALID_ARGUMENT`、`EMBY_SERVER_DISABLED`、`EMBY_NOT_CONFIGURED`、`NOT_FOUND`、`EMBY_TIMEOUT`、`EMBY_UNAVAILABLE`、`EMBY_RESPONSE_ERROR`、`CANCELLED` 等。不得将错误当作零媒体或刷新成功。

宿主不自动重试请求。写操作遇到超时、取消或响应读取失败时，远端可能已经执行；先核对 Emby 状态，避免重复删除或修改。首期仅支持 JSON/文本，不支持流式播放、二进制上传下载或 WebSocket。

同为 SDK v2 的旧宿主可能没有 `emby.api`，安装时会报告未知能力，需升级到包含本功能的 MT。完整契约见 [methods.json](/downloads/plugins-sdk-v2/methods.json)。


---

# 11. 生命周期与后台续接 (automation & tasks)

# 生命周期、调度与续接

每个启用的实例拥有一个长期存活的 Python worker 和插件对象。连接、第三方库、缓存与 asyncio 事件循环可复用；内存可丢失，需要跨重启保留的数据写入自身 KV 或 `ctx.data_dir`。

## 生命周期与调用通道

入口继承 `mt_plugin.Plugin`，支持 `validate_config(config, ctx)`、`on_init(ctx)`、`on_stop(ctx)`、`get_page(ctx)`，以及 `@action`、`@query`、`@continuation`。处理函数可同步或异步；同步使用 `ctx.mt`，异步使用 `await ctx.amt...`。在异步处理函数中直接使用同步客户端会报错，避免阻塞事件循环。

配置保存、启用和更新切换前，宿主在独立进程调用 `validate_config`，不运行初始化。返回 None 或 `{ "fieldErrors": {"字段": "原因"} }`；失败保留当前安装、配置和 worker。独立校验不保证任意 Python 代码没有原生副作用，作者必须遵守纯校验契约。

`on_init` 覆盖启用、启动恢复、配置变化与更新；成功后才投递业务。`on_stop` 覆盖停用、重配置、更新、卸载和正常关闭；停止失败仍清理 worker。初始化失败或进程退出进入 error，停止投递，用户可查日志并重试；中断业务不自动重放。

业务通道串行执行；查询与页面有独立的串行读取通道。生命周期切换独占实例。每个公开动作只允许一个 queued/running/waiting 流程，重复手动请求返回 BUSY，自动触发记录跳过。

`ctx` 提供 plugin_id、instance_id、run_id、flow_id、source、package_hash、config_revision、config、files、event、reason、data_dir、work_dir。授权由宿主绑定到实际 RPC 调用，不能通过修改这些元数据扩大权限。

耗时宿主操作返回任务引用。调用 `ctx.mt.tasks.wait(refs=[...], completion={"action":"collect", "input":{...}})` 后立即返回。完成动作需在清单标记 `kind: "continuation"`，并用 `@continuation("collect")` 注册；它不能被页面、公开 API、定时或普通订阅调用。等待会释放业务通道，原公开动作名额仍被占用；续接保持原构建、配置、根流程与幂等信息。

日志、进度或 Storage 写入应在 `tasks.wait` 之前完成。登记等待后（包括 `finally` 中）继续调用 SDK 会收到 `FLOW_WAITING`；异步接口遵循同样规则。

```python
ctx.mt.log(level="info", message="任务已提交，等待完成后继续")
ctx.mt.tasks.wait(refs=[task["ref"]], completion={"action": "collect", "input": {}})
return {"waiting": True}
```

## 定时与方案事件

触发器使用清单声明的稳定 key；在初始化中用 `ctx.mt.triggers.schedules.upsert(...)` 或 `ctx.mt.triggers.events.upsert(...)` 登记。**schedule 参数是 `declarationKey`（Python `declaration_key`），event 参数是 `key`**。重复初始化复用登记并保留用户暂停状态，新增登记默认暂停。删除清单声明会清理对应触发器。

定时定义由 MT 统一调度器保存，不在 KV 或 Python 进程里另建 Cron。五段 Cron 顺序为分钟、小时、日、月、星期；`Local` 指服务端时区。一次性任务使用 `kind: "once"`、带时区的 `at` 和空 `cron`。普通更新需要当前 revision；启用及恢复用户暂停由宿主界面操作。

方案完成订阅还需 `events.subscribe` 与具体方案事件授权，接收 `kind/profileId/status/total/completed/failed`，不返回路径、凭据或原始探测数据。插件发起的业务不会再次产生方案订阅事件，自己的任务完成用 `tasks.wait` 续接，避免形成循环。

## 取消和运行预算

取消先通知调用并撤销其后续 SDK 请求。协作取消的异步处理可保留 worker；同步处理拒绝退出或超过宽限时终止 worker 与子进程，其他受影响调用标记中断。暂停触发器、取消插件流程、取消宿主任务分别操作。

宿主默认预算：导入 30 秒，配置校验 2 秒，初始化/停止各 10 秒，查询/页面各 10 秒，动作 30 分钟，内存 512 MiB，取消宽限 1 秒。管理员可设置 MT_PLUGIN_STARTUP_TIMEOUT、MT_PLUGIN_VALIDATION_TIMEOUT、MT_PLUGIN_LIFECYCLE_TIMEOUT、MT_PLUGIN_READ_TIMEOUT、MT_PLUGIN_ACTION_TIMEOUT、MT_PLUGIN_CANCEL_GRACE（如 10s、30m）和 MT_PLUGIN_MEMORY_MIB；插件清单不能取消这些限制。


---

# 12. 存储与状态规范 (storage / KV)

# 数据、工作目录与密钥

## 数据放在哪里

| 数据 | 存放位置与生命周期 |
|---|---|
| 配置 | 宿主保存，`ctx.config` 是当前调用快照；页面 config 是未保存草稿 |
| 业务 JSON | `ctx.mt.storage`，主数据库按安装实例隔离，更新保留 |
| 持久文件 | `ctx.data_dir`，跨 worker 重启保留 |
| 调用临时文件 | `ctx.work_dir`，回调结束后清理 |
| 第三方库临时文件 | worker 生命周期内的临时目录 |
| 日志、任务、运行结果 | 宿主分别管理，不用 KV 复制一套任务系统 |
| 授权媒体目录内的输出 | 用户文件，清空插件 KV 或卸载不删除它们 |

等待续接可能发生在另一调用中，不把跨调用状态放在 work_dir 或仅存内存。上传入队前宿主复制接管工作文件，拒绝入队、取消排队或任务结束时清理传输暂存。

## KV 与并发

`storage.get` 用 `found` 区分不存在和 null；写入可用 `expectedRevision` 防止覆盖并发修改。省略表示无条件，空字符串要求键尚不存在，非空值要求匹配当前修订。有效期使用 `expiresAt`，`storage.batch` 原子提交。

默认每实例 32 MiB、10,000 个键，单值 256 KiB，单次 batch 1 MiB。实际用量及限额查 `storage.usage`，不要假设管理员未调整策略。大量逐文件结果分键保存、查询分页，不能把全部结果写成一个大对象。

限制按实际传输的 JSON UTF-8 字节计算，包含引号、转义及字段名，不按字符数或作品数量计算。超过 256 KiB 的写入明确报错，消息包含操作位置、实际字节数和上限；失败不会覆盖旧值。成功写入的单值超过 200 KiB 时，宿主在插件日志提示接近上限，同一次插件调用最多提醒一次，不记录数据内容。

### 扫描结果分键与游标分页

完整的[分键分页示例](/downloads/plugins-sdk-v2/examples/storage-table/plugin.py)使用合成查重结果，不访问云盘或修改媒体：

- `currentScan` 只保存正在展示的扫描 ID。
- `scans/<runId>/summary` 保存状态和计数；状态为 running、done 或 failed。
- `scans/<runId>/groups/<序号>` 每个键保存一组结果，避免一份 JSON 随扫描持续增长。
- 查询用 `storage.list(prefix=..., cursor=..., limit=20, values=True)`，返回条数可能受 1 MiB 返回容量限制，必须使用返回的 `nextCursor` 继续，不自行计算偏移。

```python
page = ctx.mt.storage.list(prefix=f"scans/{scan_id}/groups/",
                           cursor=cursor, limit=20, values=True)
return {"groups": [entry["value"] for entry in page["items"]],
        "nextCursor": page.get("nextCursor", "")}
```

先写 running 摘要，再逐组写入，全部成功才标记 done。失败要保留已经写入的数据、尽力记录 failed 并抛出原异常；取消或容量耗尽也可能阻止失败标记，因此 running 不等于成功或零结果，页面应提示检查宿主运行状态。运行结果只返回计数和扫描 ID，不复制全部组数据。

对同一结果的修改先 `get` 再使用返回的 `revision` 作为 `expectedRevision`，避免覆盖并发编辑。不同扫描使用不同前缀；旧扫描游标不能继续读取新扫描。

分键不会绕过实例总容量、键数、调用次数或单值限制。单组仍过大时，应继续按版本拆分。示例保留旧数据便于排错，反复运行前可在宿主数据管理中清理；真实插件应仅清理已结束且不再展示的扫描，逐页列出旧前缀的键，再按最多 100 项的 batch 删除，最后删除摘要。

## 更新、备份与恢复

同来源更新保留安装实例和业务数据；不同来源的同名插件不能继承权限及空间。业务数据自行记录格式版本，回退代码不等于回退数据；遇到不支持的格式停止写入并提示恢复备份或迁移。

`on_init` 会在启用、更新或重启等场景调用。初始化只补缺失值，不能每次覆盖用户设置。以下代码需要清单声明且用户授予 `storage`；安装或初始化不会绕过授权。

```python
def on_init(self, ctx):
    saved = ctx.mt.storage.get(key="settings")
    if not saved["found"]:
        ctx.mt.storage.set(key="settings", value={"schemaVersion": 1, "threshold": 10})
```

`found=True` 的值即使为 `null`、空字符串或 `False` 也不是未初始化。不要通过更换插件 ID 或卸载重装实施升级；重新安装与更新的实例归属规则不同。

宿主数据管理可导出 KV 和非密钥配置，恢复前预检来源、格式、容量、配置字段，正式恢复再次检查。恢复覆盖业务键及配置并停用插件；活动或结果未知任务会阻止清空与恢复。备份不含授权、密钥、确认信息及内部额度。

`data_dir` 文件不在 KV 导出中，需单独备份。卸载默认保留业务数据，重新安装时核对来源并显式关联；关联后仍需检查配置与授权。不要把卸载保留等同于系统文件的完整备份。

## 配置与密钥

页面 config 仅为草稿，调用宿主保存后才应用。更新默认保留已有配置，选择性默认值升级见[清单](manifest.md)。等待中的流程不会偷偷替换成新配置。

密钥在宿主外壳设置，清单声明后授予 `secrets.use`，再通过 `ctx.mt.secrets.get` 读取。宿主加密存储并脱敏已知密钥，作者仍不得将密钥放入 KV 导出、配置默认值、页面、日志和异常文本。日志使用见[统一日志](logs.md)。


---

# 13. 日志规范 (logs)

# 统一日志

## 作者日志与宿主采集

```python
ctx.mt.log(level="info", message="已扫描 12 个文件，跳过 2 个非视频")
```

支持 debug、info、warn、error，并接入 Python 标准 logging；第三方库 stdout/stderr 单独收集，不与 RPC 共用协议通道。运行内的标准 logging 关联当前调用；无法确认归属的后台输出记录为实例日志。

宿主自动记录安装依赖、生命周期、动作起止、SDK 失败、异常堆栈、超时、进程退出和续接，附加实例、插件版本、构建哈希和配置修订，有调用上下文时附加运行、根流程和动作。

## 查询与保留

登录后的 `GET /api/plugins/:id/logs` 支持 level、runId、flowId、search、before、after、limit（最多 200）。ID 是稳定递增游标；before 倒序读历史，after 正序读新增，不能同时提供。统一 API 响应 data 内包含 items、hasMore、retentionDays 和 maxEntries。

宿主日志页每 3 秒刷新，支持暂停、筛选和加载较早日志，按 ID 去重。页面损坏、停用、初始化失败和首次安装依赖失败时仍可访问。默认保留 7 天、每实例 5,000 条，单条 2 KiB；超限滚动淘汰并在界面提示。

宿主脱敏已存储的插件密钥，作者仍应避免记录凭据、私有链接、完整敏感请求及未经处理的外部错误。

## SDK 与 AI 错误定位

`SDKError` 的公开错误码与消息会保留到运行错误和日志；普通 Python 异常仍在日志中保留诊断信息。`ai.chat` 区分认证失败、访问拒绝、限流、超时、服务不可用和响应格式错误，提供 `retryable`，不向插件返回上游原始错误体。未启用 `singleRequest` 时，短暂 AI 服务故障最多重试一次；取消、权限和参数错误不自动重试。插件应在抛出错误前持久化已完成结果与未识别文件的失败状态，并继续跟踪此前已提交的业务任务。

`AI_TIMEOUT` 的消息区分域名解析、连接、HTTPS 握手、发送请求、等待响应和读取回复。宿主 HTTP 日志同时记录 `phase`、`elapsedMs` 与 `requestWritten`，不记录请求正文、认证头或服务商响应体。`requestWritten` 表示这次 HTTP 调用中曾成功写出请求，不代表服务商已处理或计费；连接内部重试不会清掉该标记。HTTPS 握手默认最多等待 10 秒，独立于模型生成回复的超时；已有显式握手超时与代理配置保持不变。后续连接握手超时不能说明此前的对话、查询或内部重试尚未发送，增加模型超时也不能解决这类连接问题。

HTTP 协议响应无效时返回 `AI_PROTOCOL_ERROR`，宿主日志标记 `reason=invalid_http_response`，不会把响应字节直接写入日志。连接超时配置保留原客户端的 HTTP/2 协商能力，也保留显式 HTTP/1.1 限制与自定义代理设置。

未启用 `singleRequest` 时，若 HTTP/2 请求已发出但等待响应超时，`ai.chat` 在原有的一次重试中尝试 HTTP/1.1，并计入兼容性重试统计；不增加请求次数、不改变代理、不覆盖显式协议设置或自定义传输。取消、握手超时以及已经开始读取回复的超时不触发此协议切换。

启用 `ai.chat(singleRequest=true)` 时不自动重发，包括空回复、参数兼容及 HTTP 协议降级；失败计数和耗时仍保留。模型响应与整理准备/提交耗时分别统计，后台整理耗时见逐文件结果。


---

# 14. 工作流容量设计

# 大批量工作流与分页

整轮容量与单次分页大小独立，先用 `ctx.mt.runtime.info()` 读取实际限制。

| 限制 | 当前值 | 影响范围 |
|---|---:|---|
| `maxWorkflowFiles` | 500 | 文件选择、汇总、累计整理草稿、确认提交及 tasks.wait 引用数量 |
| `maxFileBatch` | 100 | 文件列表和任务明细单页 |
| `maxScanFiles` | 10,000 | 底层扫描上限，不表示可将这些文件全交给一轮整理 |
| AI 示例每批文件 | 8 | 示例自身策略，不是所有插件的统一批大小 |
| 单个 KV 值 / 运行摘要 | 256 KiB | 完整明细应分键、分页 |
| 单次协议消息 | 4 MiB | 不传大二进制或无限增长的汇总 |

宿主没有 `maxWorkflowFiles` 时按 100 处理并提示更新，不先请求 AI 再在整理阶段撞上旧上限。扫描需要主动限制和检查结果，选目录并不免除工作流容量检查。

当前 `organize.manual.run` 每运行最多调用 500 次，`storage.get/set`、`runtime.checkpoint/progress/result` 为 5,000 次；AI 合计请求上限 78，独立方法仍有各自限额，详见[能力表](capabilities.md)。这些不是无限重试预算。

## AI 示例如何处理 500 个文件

100 个文件分 13 批，106 个分 14 批，500 个分 63 批。逐文件结果分键存储；每批最终回答只保存最多 8 KiB 的诊断预览并标记截断。页面分页查询，运行摘要仅发布有限预览。

同剧组跨批等待一致性检查后再提交，独立文件可按批推进。超过 500 个视频会在调用 AI 和整理前停止，提示减少目录或分批选择；单目录扫描达到限制也不能把截断结果当作完整扫描。

工作流仍受执行超时、内存预算、取消、授权、文件版本、幂等及任务归属约束。容量不是模型耗时或识别成功率承诺。详见 [AI 工作流](ai-organize.md)。


---

# 15. AI 识别整理官方工作流参考

# AI 识别整理示例与单次搜索契约

主仓库 `mediatidy-plugin-template/` 是独立 Python 插件，不自动安装，宿主不按插件 ID 特判。下文区分通用 SDK 行为与该示例的业务策略，核对日期为 2026-10-26。

## 当前工作流

选择授权文件或目录及方案 → 后台扫描并去重 → 分批识别 → 同剧一致性检查 → TMDB 身份核对 → 直接整理或草稿审阅 → 等待任务 → 分页结果、原始回答预览、日志与历史。

每轮最多 500 个文件，每批最多 8 个。开始前读取宿主 `maxWorkflowFiles`，旧宿主缺少时按 100 处理；超量在调用 AI 和提交整理前停止。100、106、500 个文件分别需要 13、14、63 批。详情见[工作流容量](workflow-capacity.md)。

初始化不自动整理。默认提示词来源为 `prompts/default.txt`，与清单默认值同步；用户可以完整编辑并显式保存。提示词字段通过 `upgradeDefault` 只更新未自定义的旧默认值。

## 一批只发送一次模型请求

```python
response = ctx.mt.ai.chat(
    messages=[{"role": "user", "content": "待识别的文件线索"}],
    file_refs=[file["ref"]],
    single_request=True,
    native_web_search=True,
)
```

这两个参数分别对应 JSON `singleRequest/nativeWebSearch`，默认均为 false。原生搜索必须同时启用 singleRequest，只接受文本消息，不能混用函数工具或工具消息。实例必须声明并获准 `ai.chat`、`ai.web.search`，宿主 AI 联网设置也必须启用。

宿主使用配置服务商的 Responses `web_search`，要求完成搜索及完整最终回答；不额外探测、不跟随可能重放 POST 的重定向、不兼容重试，也不降级普通聊天或 Tavily。失败仍可能带 `retryable`，这不表示此次调用会自动重发。未选择 singleRequest 的普通调用维持其原有兼容与重试行为。

成功返回 `message`、`usage`、`metrics` 和 `nativeWebSearch: {performed: true, sources: [{title, url}]}`。来源仅来自服务商完成的搜索记录与 URL 引用，不代表宿主已确认作品身份。

| 错误 | 含义与处理 |
|---|---|
| `AI_NATIVE_SEARCH_DISABLED` | 宿主未开启 AI 联网 |
| `AI_NATIVE_SEARCH_NOT_PERFORMED` | 没有服务商已完成的搜索记录 |
| `AI_INCOMPLETE_RESPONSE` | 响应或最终文本未完整完成 |
| `AI_REQUEST_REJECTED` | 服务商拒绝参数或不支持对应协议，检查服务配置 |
| `AI_TIMEOUT/AI_PROTOCOL_ERROR` | 查看宿主日志中的连接阶段，不能靠自动重复识别掩盖失败 |

## 解析、引用与身份核对

示例按原样 `fileRef` 匹配结果，要求作品名、原文名、首映/首播年份、有效 movie/tv 类型和正整数 TMDB ID；剧集需有效季集，特别篇允许第 0 季。电影不能含季集；多集合集不会按单集提交。

接受唯一完整的 JSON 对象或数组，也可解析被说明文字/代码围栏包围的完整 JSON。不会从截断 JSON 抢救局部结果，不在多个答案间猜选。重复、缺失、陌生引用逐项标明；不按数组位置配对文件。

**缺少 TMDB 搜索引用本身不会阻止身份核对。** 服务商完成搜索是请求级要求；逐项引用可为空。匹配实际服务商来源的链接可显示为搜索依据；伪造、缺失引用不能跳过也不会单独阻止后续 TMDB 核对。

AI 结论先保存展示，再调用已有 `tmdb.detail`。相同类型/ID 在一轮内去重；返回 ID、类型和年份必须一致，title 或 originalTitle 经 Unicode NFKC、大小写与标点规范化后须与 TMDB 名称有交集。失败保留 AI 原身份和差异，不自动搜索、更换 ID 或生成可提交草稿。

缺失或无效结果不阻止独立文件继续；同剧分组还需满足下面的一致性要求。通过身份核对也不等于整理成功，宿主仍执行方案、路径、权限、版本与季集业务检查。

## 同剧跨批一致性

只将同授权根、同目录、同规范化剧名前缀、同文件季号且有明确分集标记的多个文件归组，不将整个目录或不同作品混为一组。每批传入 `seriesGroup` 和已有 `seriesContext` 候选。

组内文件先等待所有批次完成，再核对 mediaType、TMDB ID 和季号。冲突标记 `identity_conflict`，整组不整理也不产生草稿；识别不完整则整组暂缓。其他独立文件可继续。不会为了凑齐一致性覆盖模型结论，也不会额外调用模型自动修复。

## 手动重试与未知提交

用户可通过示例页面发起新一轮识别重试，保留原方案、补充说明及审阅模式，重新检查文件版本与授权。仅选取可重试的 failed、unrecognized 或 identity_conflict 项，且必须从未提交、没有任务/草稿引用；已进入提交、结果未知、已有草稿或成功项不重放。原记录保存新流程关联，重复点击不能重复创建同一轮重试。

历史数据只展示，不自动重提业务。AI 服务失败时保存已经完成的识别结果，并继续关联此前提交的任务；取消和未知提交仍遵循[业务幂等规则](business.md)。

## 保存与计时

逐文件结果分键存储、分页查询。每批服务商最终可见文本保存**最多 8 KiB 的预览**及解析诊断，超出时标记截断；不记录模型内部推理或凭据。旧记录没有原始回答时显示缺失，不生成替代文本。

`modelDurationMs` 是模型请求累计耗时，`modelQueueMs` 是限流等待；原生搜索耗时包含服务商搜索与生成。`nativeWebRequests` 是 `modelRequests` 的子集，不能相加，也不表示服务商内部搜索次数。`metadataCheckMs` 记录 TMDB 核对，`organizeSubmissionMs` 记录准备/提交 SDK 耗时，逐文件 `organizeDurationMs` 跟踪提交到最终任务状态，含排队。

示例测试使用本地模拟服务。通过模拟测试不代表实际模型、联网和云盘已验证。


---

# 16. 下载与使用 Skill 开发包

# 下载与使用开发 Skill

[下载 Python 插件开发 Skill · SDK 2.0.0](/downloads/mediatidy-plugin-development-2.0.0.zip)

也可在 MT「插件中心」点击「下载开发 Skill 完整包」。该入口使用登录后的 `GET /api/plugins/development/skill.zip`，返回随主程序打包的资源；旧主程序内置的包可能早于 Wiki，请同时核对宿主支持的能力。

当前开发包保持 SDK 2.0.0，已同步以下能力和示例：

- [Emby 配置与通用 API](emby.md)：`emby.servers.list()`、`emby.request()`，包含同步、异步接口与服务器授权说明。
- [目录选择与按钮反馈](ui.md)：`ui.DirectoryPicker`、读写目录授权、`Button.loading` 绑定及页面 `state` 默认值。
- [配置修复与错误诊断](user-guide.md)：Schema 配置 JSON 编辑、升级授权变化、异常安装记录提示及带字段位置的错误。
- [存储初始化](storage.md)只补缺失值，以及 [`tasks.wait` 登记后立即返回](automation.md)的用法。

此前的本地 FF 缓存存在性查询 `ffprobe.cache_exists`、`Button.size`、Table 行内操作和分键分页示例继续包含在包内。新增接口和组件需要目标宿主包含对应实现；下载新版 Skill 不会升级正在运行的 MT。旧宿主尚未支持时，请按其能力选择接口和组件。

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
    ├── plugin-sdk/examples/storage-table/
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

- [宿主能力与输入输出 schema](/downloads/plugins-sdk-v2/methods.json)
- [Python 同步与异步客户端](/downloads/plugins-sdk-v2/clients.py)
- [MT UI v2 组件注册表](/downloads/plugins-sdk-v2/ui-registry.json)

单个源码文件用于查阅。执行 CLI 时使用完整 SDK 项目，避免遗漏契约和注册表。维护者同步方法见[开发工具](tooling.md)。


---

# 17. 官方开发工具链 (tooling / check / pack)

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


---

# 18. 验收与排错全书（常见问题与错误代码速查）

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


---

# 19. 宿主方法实现状态与对照表

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


---
