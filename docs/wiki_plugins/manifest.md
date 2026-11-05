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
