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

单选 `Select` 的选项可包含 `description`；提供说明时渲染为可点击的选项卡片，值仍使用原始 `value`（包括布尔值），未提供说明时保留下拉选择。

onClick 支持 set、invoke、query 和 host 事件。host 支持 saveConfig、refreshRun、refreshPage、cancelRun、newRun、openLogs、reviewDrafts。普通输入只更新前端状态；数据通过查询与运行结果更新，页面结构由“刷新页面”显式重新读取 Python。

`stats.selections` 是文件选择器当前勾选的文件或目录数量；`stats.files`、`stats.drafts` 是当前运行的文件和草稿数量，可用于按钮可用状态与空状态提示。

单页最多 256 KiB、500 个组件、深度 12，表格最多 12 列。图片可使用清单声明的 UI 资源路径，或 `file:<宿主文件引用>`；后者只预览授权的 PNG、JPEG、WebP，最多 8 MiB。禁止任意网页地址。组件属性见 [注册表](sdk/mt_plugin/ui-registry.json)。

## React 页面

参考 `plugin-sdk/examples/react`；Skill 中对应 `assets/plugin-sdk/examples/react`。只有这类作者需要 Node 22.12+。通过 `@module-federation/vite` 构建并导出 `./Page`，发布包包括 `ui/remoteEntry.js`、模块、CSS 和资源。宿主使用配套 federation runtime 按实例与构建哈希加载。

完整复制 Skill 的 assets 目录后，示例 frontend 的 `file:../../../../plugin-frontend-sdk` 可以直接解析。如果只搬走 React 项目，应在 `frontend/package.json` 重设 `@mediatidy/plugin-ui` 本地路径并更新 package-lock，再运行 `npm ci`。前端依赖安装完成后回到插件根目录运行 `python -m mt_plugin pack`；它会调用前端 `npm run build`。Git 安装必须提交构建好的 `ui/`，宿主不会运行 npm。

共享 React、React DOM、JSX runtime 以及公开的 `@mediatidy/plugin-ui`，必须配置 singleton，示例使用 import:false 从宿主获取实现。公开 SDK 在 `plugin-frontend-sdk`，模板不引用主站内部源码。CSS Modules 继承主站主题变量；第三方组件随作者构建携带，MT 容器不安装 Node。

页面获得 `mt`：实例状态、主题、语言、尺寸；runAction、query、saveConfig、selectFiles、selectProfile、previewUrl、openLogs、openTasks、openHistory、reviewDrafts、refreshPage。runAction 返回排队的运行引用，最终结果在宿主任务和运行记录中核对。

资源会话由登录用户申请，按用户、实例与构建绑定，使用限定路径的 HttpOnly Cookie，5 分钟到期并由页面续期。资源 URL 不含主站 JWT，响应 private/no-store，PWA 排除私有资源。构建切换使用新模块标识并卸载旧页面；组件应清理自身订阅。加载和渲染错误由宿主错误边界显示，管理入口保留。

React 插件与主站共享 JavaScript 环境，是受信任代码；Python 及其库同样有原生访问能力。
