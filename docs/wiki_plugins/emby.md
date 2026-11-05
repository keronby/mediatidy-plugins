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
