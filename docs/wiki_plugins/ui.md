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
