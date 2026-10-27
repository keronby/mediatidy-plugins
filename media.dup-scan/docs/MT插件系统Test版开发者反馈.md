# MediaTidy 插件系统 Test 版开发者深度体验与改进建议

> 来源：首批真实业务插件（`media.dup-scan` STRM 查重洗版插件）端到端研发与实战调试总结  
> 日期：2026-10-27  

---

## 摘要与致谢

MediaTidy 新版插件架构（SDK v2 + MT UI v2 声明式前端 + 细粒度权限模型）在设计理念、安全性及性能表现上非常出色：
- **声明式 UI 设计极简**：纯 Python 即可驱动丰富的管理与业务面板，免去了搭建复杂 Node/React 前端的负担；
- **权限与资源隔离严谨**：针对文件目录、整理方案（profiles）、规则（rules）的细粒度授权机制非常完备；
- **运行时性能优异**：本地扫描与 RPC 调度效率极高，万级文件扫描响应敏捷。

在基于当前刚上线的 Test 版进行全流程落地开发过程中，我们遇到并攻克了若干开发者体验、报错透明度及前端健壮性方面的痛点。以下整理为 **5 条高价值反馈与建议**，供 MT 团队迭代参考：

---

## 1. ZIP 上传静态校验失败缺乏错误根因（排错黑盒）

### 现状与痛点
当调用 `POST /api/plugins/inspect/upload` 上传插件 ZIP 时，如果包内存在任何细微的不符规范之处（例如：`plugin.json` 中的表单字段类型误填了非白名单值、`pyproject.toml` 配置段落层级差异、或者 schema 格式不兼容），接口会在 **1.5ms** 内直接统一返回：

```json
{
  "code": 400,
  "data": {
    "error": {
      "code": "OPERATION_FAILED",
      "message": "操作未完成，请查看 MT 中对应业务的状态",
      "retryable": false
    }
  },
  "message": "操作未完成，请查看 MT 中对应业务的状态"
}
```

同时宿主后台日志 `app-YYYY-MM-DD.log` 也仅记录：
```json
{"level":"WARN","status":400,"method":"POST","path":"/api/plugins/inspect/upload","latency":"1.615ms"}
```
由于没有输出任何具体的校验失败原因，开发者完全无法得知究竟是哪个文件的哪一行触发了校验拒绝，只能盲目二分猜测排查，试错成本极高。

### 建议
建议在 Go 后端执行静态数据校验失败时，将具体的校验失败原因放入响应体 `data.error.message` 或 `data.error.detail` 中返回给调用方，并在宿主日志中输出详细 debug 信息。例如：
- `Manifest validation error: actions[1].fields[0].type 'string' is invalid, supported: [text, number, boolean, select, files, ...]`
- `pyproject.toml error: [tool.uv].package must be false`
- `Dependency error: uv.lock missing or virtual project name mismatch`

---

## 2. MT UI v2 组件属性全等白名单过于严苛，且缺少出错组件定位

### 现状与痛点
前端 UI v2 解析器在客户端对每个组件属性进行递归校验。其底层实现为全等白名单判定（属性不在组件白名单即阻断）：
```javascript
// 当组件包含不在白名单中的属性时直接抛错
i = (c, f) => { (!Le(c) || Object.keys(c).some(h => Ze.has(h) || !f.includes(h))) && r() }
```
这导致如果开发者习惯性传入了现代 UI 框架常见但未被显式收录的辅助属性（例如为 `Button` 传入了 `size="sm"`），校验器会立即判定全页非法，直接抛出：
```text
CATCH: 无效或不受支持的 MT UI v2 页面
```
这会导致整个插件详情页完全白屏崩溃，且浏览器控制台没有任何提示指向具体是哪个组件、哪一列属性写错。

### 建议
1. **适度容错**：对不影响结构与安全性的非致命展示属性（如 `size`、样式提示等）做忽略或降级处理，避免单点非致命属性直接破坏全页可用性；
2. **错误定位**：校验报错时带上组件路径，例如：`Invalid prop 'size' for component 'Button' at Page > Section[0] > Table.columns[4]`，让开发者能够瞬间完成定位。

---

## 3. 前端 PluginDetail 在 `profileIds` 为空时的空指针异常（Bug）

### 现状与复现
在插件初次安装、管理员尚未分配授权的整理方案时，接口返回的 `plugin.grants.profileIds` 字段为 `null`。

在官方前端 `PluginDetail.tsx` 中，存在如下直接解构逻辑：
```javascript
// 未做空安全防护
n.grants.profileIds.length
```
当 `profileIds` 为 `null` 时，直接触发运行时未捕获异常：
```text
TypeError: Cannot read properties of null (reading "length")
```
导致该插件的整个详情页挂掉，管理员甚至无法在界面上点击“修改授权”来为插件授权，陷入死锁。

### 修复方案
建议前端加上可选链与空值保底：
```typescript
n.grants?.profileIds?.length ?? 0
```

---

## 4. 单 Key 256 KiB 限制在批量媒体扫描场景较易触顶，建议提供分片抽象或写入容量预警

### 现状与痛点
在大型媒体库（如包含数万至数十万文件的电影、动漫库）查重场景中，即使只保留重复结果摘要，100~200 组重复作品（包含作品名、各版本路径、分差、版本属性等）序列化后极易接近或突破单 Key **256 KiB** 的上限。

一旦数据超限，`ctx.mt.storage.set` 写入直接报错失败。若插件未做极端裁剪防护，容易导致扫描正常执行完毕但前端查询结果为空的现象。

### 建议
1. **容量预警**：当单次 `storage.set` 写入体积超过 200 KiB 时，在系统日志中打印 warn 级别预警；
2. **官方分片集合**：建议在官方 Python SDK 中封装类似 `storage.Collection` 或 `storage.set_chunks` 的高级分片/分页抽象，方便开发者开箱即用存储列表数据，无需每个人重复造分片轮子。

---

## 5. 建议在开发文档中补充 `Table` 行内操作列（Action Cells）的官方范式

### 现状
许多业务插件都需要在数据表格（Table）的每一行放置行内交互操作（例如：选择保留该版本、忽略、重试、删除等）。

当前官方文档与示例中主要展示了单行信息展示，未明确说明如何在 `Table.columns` 声明内配置嵌入式操作单元格（`cells: [ui.Button(...)]`），以及如何通过 `ui.ref("row.id")` 正确捕获行上下文传递给 action 的 input。开发者需要自行逆向前端 AST 才能确认此功能受系统原生支持。

### 建议
在 `ui.md` 文档的 Table 章节中补充行内操作按钮的标准书写示例，例如：
```python
ui.Table(
    source=ui.ref("query.summary.groups"),
    columns=[
        {"title": "作品名称", "dataIndex": "name"},
        {"title": "操作", "key": "action", "cells": [
            ui.Button(
                label="保留此版本",
                variant="outline",
                onClick=[
                    {"type": "invoke", "actionId": "decide", "input": {"id": ui.ref("row.id")}},
                    {"type": "query", "queryId": "summary"}
                ]
            )
        ]}
    ]
)
```

---

## 结语

MT 插件系统的架构底座非常扎实，以上反馈均属于新架构在首发 Test 阶段常见的易用性与边界打磨问题。感谢 MT 团队带来如此强大的扩展能力，期待在后续版本中持续优化，共同构建繁荣的 MT 插件生态！
