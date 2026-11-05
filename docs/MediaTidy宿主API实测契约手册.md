# MediaTidy 宿主 HTTP API 实测契约手册

> **文档定位**：这是一份**面向 `mt-console` 开发者的实现手册**，只回答一个问题——「调用 MT 宿主 API 时，怎么保证不出错、出错时怎么快速定位」。
>
> 与既有 [MediaTidy核心体系破译与mt-console优化技术白皮书.md](MediaTidy核心体系破译与mt-console优化技术白皮书.md) 的分工：白皮书讲**业务算法与架构方向**，本文讲**接口层的确定行为**。
>
> **所有结论均在 `http://127.0.0.1:2019` 实机验证**（MT 2026-10 版本，`sdkVersion: 2`）。凡标注「实测」的条目都有可复现的请求；凡标注「未验证」的条目明确告知不确定性，请勿据此写死代码。

---

## 目录

- [0. 三分钟速查](#0-三分钟速查)
- [1. 请求与响应基础契约](#1-请求与响应基础契约)
- [2. 插件安装流水线](#2-插件安装流水线)
- [3. 插件状态与授权（grants）](#3-插件状态与授权grants)
- [4. 运行（runs）与查询（queries）](#4-运行runs与查询queries)
- [5. 日志与定时器](#5-日志与定时器)
- [6. manifest 校验规则全表](#6-manifest-校验规则全表)
- [7. UI AST v2 规范](#7-ui-ast-v2-规范)
- [8. MT 业务原生 API](#8-mt-业务原生-api)
- [9. 平台容量与资源限制](#9-平台容量与资源限制)
- [10. 对既有白皮书的勘误](#10-对既有白皮书的勘误)
- [11. mt-console 实现建议](#11-mt-console-实现建议)
- [附录 A：完整错误码表](#附录-a完整错误码表)
- [附录 B：最小可用插件包](#附录-b最小可用插件包)
- [附录 C：验证脚本](#附录-c验证脚本)

---

## 0. 三分钟速查

```
基址      http://<host>:2019/api
认证      POST /auth/login {"username","password"} → data.token
          Authorization: Bearer <token>
成功      {"code":0,"message":"success","data":<payload>}
失败      {"code":<http>,"data":{"error":{"code":"<枚举>","message":"...","retryable":bool}},"message":"<同上>"}
```

**最容易踩死的 6 条：**

| # | 事实 | 违反后果 |
|---|---|---|
| 1 | `queries` 的 body 必须是 `{}` | 传 `{"input":{}}` → `400 OPERATION_FAILED` |
| 2 | `runs` 的 body 用键 `action` | 用 `actionId` → `400 OPERATION_FAILED`（不报"字段错"，极难定位） |
| 3 | `PUT /plugins/<id>` 不传 `config` 会清空配置 | 插件静默丢失全部配置 |
| 4 | `configSchema` 未声明的 config key 会让**安装静默失败** | HTTP 返回 200，插件列表里查不到，真因只在 `plugin_records.installJob.body.error` |
| 5 | `actions[].id` / `queries[].id` / `triggers[].key` 只允许 `[a-z0-9.-]` | `a_b`、`go1`、`K1` 全部 `INVALID_MANIFEST` |
| 6 | 插件身份是 `id@版本hash`，同版本重装不覆盖旧包 | 反复重装会堆到 100 个包上限 `PLUGIN_PACKAGE_LIMIT` |

---

## 1. 请求与响应基础契约

### 1.1 认证

```bash
curl -s -X POST http://127.0.0.1:2019/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"******"}'
# → {"code":0,"message":"success","data":{"token":"eyJhbGciOi..."}}
```

- token 是 JWT，实测长度 **188** 字符。
- 未带 token 访问业务端点 → `401 {"code":-1,"message":"missing authorization"}`。
- **这个 401 的 `code` 是 `-1`**，没有 `data.error` 结构 —— 解析器必须容忍两种错误形状。

### 1.2 两种响应包装（必须分别处理）

```jsonc
// 成功
{"code": 0, "message": "success", "data": { ... }}

// 失败（业务校验类，有 error 枚举）
{"code": 400,
 "data": {"error": {"code": "INVALID_MANIFEST", "message": "plugin.json: ...", "retryable": false}},
 "message": "plugin.json: ..."}

// 失败（基础设施类，只有 code=-1 + 文案）
{"code": -1, "message": "插件请求参数无效或过大"}
```

**解析规则：**
1. `code == 0` → 成功，取 `data`。
2. 否则取 `data.error.code` 作为机器可读错误码；**若不存在**（`code == -1`）则退化为按 HTTP 状态 + `message` 文案判断。
3. 错误 `message` 与顶层 `message` 实测一致，取任意一个。

### 1.3 `retryable` 字段不可信

实测多个真正可重试的错误（`PLUGIN_PACKAGE_LIMIT`、`DEPENDENCY_LOCK_REQUIRED`）都标了 `retryable: false`。**不要用它驱动重试策略**，写死自己的重试白名单。

---

## 2. 插件安装流水线

MT 的安装是**两阶段 + 一次性 ticket**，console 必须完整走完。

```
[1] POST /plugins/inspect/upload   (multipart, 字段名 file)
        ↓ data.ticket（TTL 30 分钟）
[2] POST /plugins/install          {"ticket": ..., "grants": {...}}
        ↓ 返回 install job，status 需轮询
[3] GET  /plugins/<id>             直到 200 且 status=ready
[4] PUT  /plugins/<id>             enabled + config + grants 一起提交
```

### 2.1 阶段一：上传预检

```bash
curl -s -X POST http://127.0.0.1:2019/api/plugins/inspect/upload \
  -H "Authorization: Bearer $T" \
  -F "file=@media.dup-scan-1.0.2.zip;type=application/zip"
```

**multipart 字段名必须是 `file`**（不是 `package`、`zip`）。

成功响应（实测）：

```jsonc
{"code":0,"message":"success","data":{
  "ticket":"f61df06a-...",            // 一次性安装凭证
  "expires":"2026-10-29T01:20:51.246189433Z",  // 实测 TTL = 29:59.99，即 30 分钟
  "release":{ "commit":"...", "hash":"...", "manifest":{...}, "ref":"...", "sourceUrl":"..." },
  "currentRevision":"...",
  "currentInstanceId":"..."
}}
```

- **TTL 精确为 30 分钟**，带纳秒精度。
- `release.manifest` 是宿主解析后的清单，可用来在安装前校验。
- 此阶段会做完整静态分析（含 Python 语法、依赖锁、AST），**所有 manifest 错误都在这里暴露**。
- **未传 `file` 字段** → `400 {"code":-1,"message":"请上传有效的 ZIP 文件，大小不超过 40 MiB"}`（注意走的是 `code=-1` 通道）。

另有一个 GitHub/Gitee 直连模式 `POST /plugins/inspect`，body `{"url","ref"}`，实测存在但仅接受**公开仓库 HTTPS 地址**（否则 `INVALID_REPOSITORY`）。

### 2.2 阶段二：安装

```jsonc
POST /plugins/install
{"ticket": "<上一步的 ticket>", "grants": {"storage": true, "files.read": true}}
```

**`grants` 只能是「能力名 → 布尔」的扁平 map。** 传嵌套结构（如 `{"roots": [...]}`）会被静默忽略，导致安装看似成功、插件却永不出现。

响应返回一个 install job（`data.pluginId`、`data.candidate.manifest` 等），**HTTP 200 不代表安装成功**。

### 2.3 ★ 安装失败的静默陷阱（最关键的一条）

实测：`POST /plugins/install` 返回 **200**，`GET /plugins/<id>` 却一直 **404**，`GET /plugins` 里也没有它。

真因藏在 SQLite 里：

```sql
-- 容器内 /app/data/mediatidy.db
SELECT body FROM plugin_records WHERE kind='installJob' ORDER BY rowid DESC LIMIT 3;
```

`body` 是 JSON，里面有 `status: "failed"` 和 `error` 字段，例如：

```
status=failed  error=插件配置校验失败，请检查配置与插件代码
status=failed  error=config.cd2Token: Schema 未声明此字段
status=failed  error=配置校验失败: ... SyntaxError: positional argument follows keyword argument
```

**console 必须实现这个探针**，否则用户只会看到「装不上」，而没有任何线索。

```python
def wait_install(plugin_id: str, timeout: int = 240) -> str:
    """返回 'ready' / 'failed:<原因>' / 'timeout'
    注意：uv 首次拉 wheel 实测要 2~3 分钟，超时别设太短。"""
```

### 2.4 ★ 插件身份是「id + 版本内容哈希」

实测容器内 `packages/` 目录：

```
37 × media.series-dedup 1.0.0     ← 同 id 同版本，不同内容哈希
18 × media.dup-scan     1.0.0
15 × media.sha1-dedup   1.0.0
 3 × media.dup-scan     1.0.1
 2 × media.dup-scan     1.0.2
```

**同一 `id@version` 只要内容不同，就是一个新包**。后果：

1. 反复重装（改一点装一次）会线性堆积。
2. 堆到 **100 个包**触发 `400 PLUGIN_PACKAGE_LIMIT`（实测：81 个包 / 100 个暂存目录时触发）。
3. 触发后是**全局阻断**——任何插件的 `inspect/upload` 都失败，不只是当前这个。

清理方法（保留每 `id@version` 最新一份）：

```bash
# 容器内
for d in /app/data/plugins/packages/*/; do
  python3 -c "import json;m=json.load(open('$d/plugin.json'));print(m['id'],m['version'],'$d')"
done | sort -k1,1 -k2,2 -k3,3 | awk '{last[$1"@"$2]=$3} END{for(k in last) print last[k]}' > /tmp/keep.txt

for d in /app/data/plugins/packages/*/; do
  grep -qxF "$d" /tmp/keep.txt || rm -rf "$d"
done
rm -rf /app/data/plugins/packages-inspections/*   # 暂存目录可整体清空
```

> `packages-inspections/` 是 inspect 产物，install 完成后即可删除，实证清空后插件全部正常运行。

---

## 3. 插件状态与授权（grants）

### 3.1 `GET /plugins/<id>` 返回结构

```jsonc
{
  "status": "ready",              // ready / disabled / error / installing
  "enabled": true,
  "config": { ... },              // 用户配置
  "grants": {
    "capabilities": ["storage","files.read","rules.read"],
    "roots": [],                  // 授权目录，见 3.3
    "http": [], "channelIds": [], "profileIds": [], "strmProfileIds": [],
    "autoProfileIds": [], "eventProfileIds": [], "eventStrmProfileIds": [],
    "profileRevisions": {}, "strmRevisions": {}
  },
  "manifest": { ... },
  "instanceId": "...", "revision": "...", "hash": "...", "commit": "...",
  "ref": "...", "sourceUrl": "...", "installedAt": "...", "previous": {...}
}
```

### 3.2 `PUT /plugins/<id>` 的字段语义（实测澄清）

| 提交体 | 实测结果 |
|---|---|
| `{"enabled": true}` | `200`，但 **`config` 被清空** |
| `{"enabled": true, "config": {...}}` | `200`，`config` 正确写入，`grants` 保持原样 |
| `{"enabled": true, "config": {...}, "grants": {...}}` | `200`，三者同时写入 |
| `grants.roots = ["/strm"]`（字符串数组） | `400 {"code":-1,"message":"插件请求参数无效或过大"}` |
| `grants.roots = []` | `200`，**清空该插件全部目录授权** |

**结论：console 更新配置时必须「读-改-写」，把现有 `config` 与 `grants` 原样带上。**

```python
def safe_update(plugin_id: str, *, config=None, enabled=None):
    cur = get(f"/plugins/{plugin_id}")["data"]
    body = {
        "enabled": cur["enabled"] if enabled is None else enabled,
        "config":  cur.get("config") or {} if config is None else config,
        "grants":  cur.get("grants") or {},          # ← 必须回传
    }
    return put(f"/plugins/{plugin_id}", body)
```

### 3.3 `grants.roots` 必须是对象数组

```jsonc
"roots": [{
  "id": "root-b4626435-631a-4982-a6ba-8083d745a821",
  "name": "strm",
  "path": "/strm",
  "read": true, "write": false, "delete": false,
  "storageType": "local"
}]
```

- `id` 是宿主生成的 `root-<uuid>`，console **不要自己编**。
- 正确流程：让用户在 MT 界面上完成目录授权 → console 读 `grants.roots` 学习 → 之后回传同一份。
- **纯 CD2 / 外部 API 型插件无需任何 root**（`roots: []` 是正常的，不是配置缺失）。

### 3.4 能力（capabilities）必须与清单一致

`grants.capabilities` ⊆ `manifest.permissions`。清单里没声明的能力不能授权；授权里多出的能力会被拒。

实测合法权限全集（取自容器内 `/app/data/plugins/sdk/2/mt_plugin/methods.json`，共 31 项）：

```
ai.chat  ai.complete  ai.web.search  events.subscribe  ffprobe.cache.exists
files.delete  files.read  files.write  http.request  media.parse  media.recognize
notification.send  organize.manual.run  organize.prepare  organize.read  organize.run
quality.compare  quality.evaluate  quality.prepare  records.read  rules.read
secrets.use  storage  strm.cleanup  strm.read  strm.run  system.read
tasks.cancel  tasks.read  tasks.wait  tmdb.read  triggers.manage
```

**注意 `log` 不是权限**（写入日志不需要声明）。写错会得到 `permissions[0]: 未知或重复的能力`。

---

## 4. 运行（runs）与查询（queries）

### 4.1 `POST /plugins/<id>/runs` —— 触发动作

```jsonc
{"action": "dup-scan", "input": {"scan_dir": "/strm/云影"}}
```

- **键必须是 `action`**。用 `actionId` 同样返回 `400 OPERATION_FAILED`，无法从错误区分。
- 成功返回 `{"id": "...", "status": "queued", ...}`。
- **错误码高度同质化**：动作不存在、输入类型不匹配、参数缺失，全部返回同一个：

  ```json
  {"code":400,"data":{"error":{"code":"OPERATION_FAILED",
   "message":"操作未完成，请查看 MT 中对应业务的状态","retryable":false}}}
  ```

  实测 `{"action":"no-such-action"}`、`{"action":"summary"}`（query 名当 action 用）、`{}` **四种错误输入返回完全相同的响应**。

  → **console 必须在客户端先校验**：`action` ∈ `manifest.actions[*].id`，字段 ∈ `actions[*].fields[*].key`，否则用户只会看到一句无信息的「操作未完成」。

- 已知业务错误：同一 action 有在途流程 → `400 BUSY 该动作已有在途流程`。

### 4.2 `GET /plugins/<id>/runs` —— 运行历史

- **不支持任何分页参数**（实测 `?limit=2`、`?page=1&pageSize=2`、`?offset=0&limit=2`、`?status=done` 全部原样返回全量 72 条）。
- 数据量随使用增长，console 必须**在本地做切片**，不要指望服务端。
- 元素键：`id, flowId, action, status, result, error, drafts, files, createdAt, updatedAt, pluginId, pluginVersion`。
- 实测 `status` 取值：`queued / running / done / failed / cancelled / interrupted`。
  **`interrupted` 未在官方文档中出现**（实测 72 条里有 2 条），它表示插件进程被中断（如宿主重启），console 应单独展示而非归入 failed。

### 4.3 `POST /plugins/<id>/queries/<name>` —— 数据查询

```jsonc
// body 必须是空对象
POST /plugins/dup-scan/queries/rules   {}
```

- 传 `{"input": {}}` → `400 OPERATION_FAILED`。**这是最容易踩的坑**，因为同一个 SDK 的 action 通道确实用 `input`。
- query 名不存在 → `404 {"code":404,"data":{"error":{"code":"NOT_FOUND",...}}}`（与 action 名不存在返回的 400 不同，可用来区分）。
- 通道限时 **10 秒**，比 action 的 30 分钟严格得多，别在 query 里做重活。

---

## 5. 日志与定时器

### 5.1 `GET /plugins/<id>/logs`

| 参数 | 实测行为 |
|---|---|
| （无） | 返回 100 条 |
| `?limit=N` | 返回 N 条（**生效**） |
| `?level=error` | 只返回 error 级（**生效**，实测 28 条） |
| `?limit=3&level=error` | 两者叠加生效 |
| `?page=1&pageSize=N` | **无效**，仍返回 100 条 |

元素键：`id, at, level, source, message, instanceId, pluginVersion, configRevision, packageHash`。

`level` 实测取值：`debug / info / error`（**无 `warning`**）。

### 5.2 ★ 排查顺序（这是定位问题的最短路径）

```
HTTP 状态码  →  /plugins/<id>/logs  →  plugin_records.installJob.body.error
   (路由层)         (运行期)                    (安装期)
```

- **`/app/data/logs/app-*.log` 里只有 HTTP 状态码**，没有业务原因，别在那儿浪费时间。
- 安装失败（2.3 的静默陷阱）**只**在 `plugin_records` 里有。
- **插件代码有语法错误时**，`plugin_logs` 会直接记录

  ```
  File ".../plugin.py", line 257
      )
  SyntaxError: positional argument follows keyword argument
  ```

  而 `GET /ui` 仍可能返回**旧版本的 200**。所以「`/ui` 是 200」绝不能作为健康判定依据。

### 5.3 `GET /plugins/<id>/schedules`

返回数组：

```jsonc
[{"declarationKey":"sha1-dedup-job","id":"ee058faf-...","key":"plugin:<uuid>:ee058faf-...",
  "name":"sha1 比对删除（定时）","kind":"cron","cron":"0 4 * * *","timezone":"Local",
  "enabled":false,"revision":"...","request":{"action":"run-job","selections":null,"input":{}}}]
```

- 定时器由 `init` 阶段的 `ctx.mt.triggers.schedules.upsert(declaration_key=..., cron=..., kind=...)` 创建，需要 `triggers.manage` 权限。
- **新建默认 `enabled: false`**，必须由用户在 MT 界面启用（console 可读不可代劳，除非确认有对应写接口）。
- `timezone: "Local"` 指宿主服务端时区。
- `request` 是触发时透传给插件的 `{"action","input"}`。

---

## 6. manifest 校验规则全表

以下全部为 `inspect/upload` 阶段的实测报错，可作为 console 的**客户端预校验清单**（在本地就拦下来，比让用户等一次 200 OK 再查 DB 强得多）。

### 6.1 标识符

| 字段 | 规则 | 反例 |
|---|---|---|
| `id` | 小写 + 数字 + `-` + `.`，**不能有大写** | `media.Probe` → `id: 插件标识格式无效` |
| `actions[].id` | 同上 | `Go`、`a_b`、`goX` → `动作或查询标识无效或重复` |
| `queries[].id` | 同上 | 同上 |
| `triggers[].key` | 同上 | `k1`、`k_1`、`K1` → `标识无效或重复` |
| `config[].key` | 同上 | `config[0].key: 标识无效或重复` |

实测通过：`media.probe`、`probe`、`media.probe-1`、`go-1`、`sha1-dedup-job`、`job1`、`a.b`。

### 6.2 actions / queries

| 规则 | 报错 |
|---|---|
| `actions` 必须 **1–50 项** | `actions: 必须包含 1–50 项` |

### 6.3 config

| 规则 | 报错 |
|---|---|
| `config[].type` 仅允许 `text / textarea / number / boolean / files / profile / stringList` | `config[0].type: 不支持此类型，允许值：...` |
| 不支持 `secret` 字段 | `config[0].secret: 不支持此字段` |

> **密钥方案**：MT 的 `secrets` 声明 + `secrets.use` 权限在实测中**无公开写入 API**（`/plugins/<id>/secrets`、`/secrets`、`/settings`、`/plugin-secrets` 全部 404），只能让用户在宿主外壳手工填。第三方插件把令牌放普通 `config`（`type: "text"`）更实用，代价是明文。

### 6.4 configSchema

| 写法 | 结果 |
|---|---|
| `{"type":"object","properties":{}}` | ✅ |
| `{"type":"object"}` | ✅ |
| `{"type":"object","additionalProperties":true}` | ✅ |
| `{"type":"object","properties":{"a":{"type":"string"}}}` | ❌ `configSchema: Schema 属性无效` |
| `properties` 值带 `label` / `title` | ❌ `不支持此字段` |
| `properties` 值为字符串 `"text"` | ❌ JSON Schema 无效 |

**结论：`configSchema.properties` 里写任何东西都会被拒。** 保持 `{"type":"object","properties":{}}`（可加 `additionalProperties`），实际字段全部在 `config[]` 里声明。

> ⚠️ **关键推论**：`config[]` 声明的字段与 `configSchema` 是两套东西。宿主安装时会校验传入的 `config` 是否「Schema 已声明」，而 `properties` 又不能写内容 → 唯一的解法是 `additionalProperties: true`，或者**安装时 `config` 传空对象、装完再 `PUT` 写入**。

### 6.5 triggers

| 规则 | 报错 |
|---|---|
| `type` 仅允许 `schedule` / `event` | `triggers[0].type: 允许值：schedule, ...` |
| 不支持 `name` | `triggers[0].name: 不支持此字段` |
| 不支持 `cron` | `triggers[0].cron: 不支持此字段` |
| 不支持 `description` | `triggers[0].description: 不支持此字段` |

清单里只声明 `key` + `type` + `action`，**cron 表达式在代码里 `upsert`**。

### 6.6 依赖锁（常被忽略的硬门槛）

清单全部合法后，还会遇到：

```
400 DEPENDENCY_LOCK_REQUIRED  插件必须包含 pyproject.toml 和 uv.lock
```

包根必须有 `plugin.json` / `plugin.py` / `pyproject.toml` / `uv.lock` 四个文件。

`uv.lock` 不能手写——用容器内的 uv 生成：

```bash
docker exec mediatidy sh -c "cd /tmp/build && /usr/local/bin/uv lock --python 3.12"
```

---

## 7. UI AST v2 规范

### 7.1 组件全集

取自容器内 `/app/data/plugins/sdk/2/mt_plugin/ui-registry.json`（另含 `maxNodes: 500`、`maxDepth: 12`）：

```
Page  Section  Stack  Grid  Card  Text  Markdown  Icon  Image  Badge  Alert
Divider  Details  Input  Textarea  Select  Checkbox  FilePicker  ProfilePicker
Progress  Steps  Metric  Button  Repeat  Table  Tabs  Tab  Dialog  Drawer
EmptyState  DraftReview  RunHistory  ConfigForm  ScheduleForm
EventSubscriptions  RunLog  TaskList  DataManagement
```

### 7.2 高频属性错误（全部实测）

| 组件 | 错误写法 | 正确写法 | 报错 |
|---|---|---|---|
| `Tabs` | 直接放 `Tab` 当根 | 根必须是 `Tabs` 包裹 | — |
| `Tabs` | 无 `bind` | `bind="state.activeTab"` | `content[0].props.bind: 缺少可写绑定` |
| `Tab` | 无 `id` | `id="tab-main"` | — |
| 任意容器 | `id="..."` | 去掉 | `不支持此属性，允许属性：description, disabled, title, visible` |
| `Table` | `rows=` | `source=` | `允许属性：columns, disabled, pageSize, rowKey, searchable, selectionBind, sortable, source, visible` |
| `Button` | `text=` | `label=` | `允许属性：disabled, label, onClick, size, variant, visible` |
| `Alert` | `label=` | `text=` | `允许属性：disabled, text, tone, visible` |
| `Button` | `onClick={"invoke": "..."}` | `onClick=[{"type":"invoke","actionId":"..."}]` | — |

**Python 侧的额外坑**：`ui.Tab(id="x", ui.Section(...))` 会 `SyntaxError: positional argument follows keyword argument`。关键字参数必须放在位置参数之后：

```python
ui.Tab(
    ui.Section(...),          # 位置参数在前
    id="tab-main",            # 关键字在后
    label="标题",
)
```

### 7.3 数据引用

- 成功响应是 `{"code":0,"data":{...}}`，**AST 里的 `ui.ref` 必须带 `.data` 层**：
  - ❌ `ui.ref("query.overview.total")`
  - ✅ `ui.ref("query.overview.data.total")`
- 条件显示用 `$op` 操作符（全集：`eq ne gt gte lt lte and or not if coalesce concat length includes`）：

```python
ui.Alert(
    text="⏳ 正在后台处理…",
    tone="warning",
    visible={"$op": "includes", "args": [["queued","running","waiting"], {"$ref":"run.status"}]},
)
```

### 7.4 ★「`/ui` 返回 200 不等于页面能用」

这是本次开发中最费时的一个误判。实测：

| 情况 | `GET /ui` | 用户实际看到 |
|---|---|---|
| 根节点是 `Tab` 而非 `Tabs` | **200** | 页面不渲染 |
| 插件代码有 `SyntaxError` | **200（旧包缓存）** | 页面不渲染 |
| 装了但 `enabled: false` | 200 | 页面不出现 |

**健康判定必须三元组**：`status == "ready"` + `enabled == true` + `plugin_logs` 无 `error`。

---

## 8. MT 业务原生 API

以下端点均实测可用（`Authorization: Bearer`），供 console 直连而不经插件。

### 8.1 端点矩阵

| 端点 | 方法 | 关键参数 | 实测行为 |
|---|---|---|---|
| `/health` | GET | — | 200 `{"status":"ok"}`，**无需认证**，可用于探活 |
| `/auth/login` | POST | `username`,`password` | 返回 JWT |
| `/organize/profiles` | GET | — | 整理方案列表（`id/name/sortOrder/enabled/storageType`） |
| `/organize/manual` | POST | `sourceDir` \| `sourceFile` \| `sourceFiles`（三选一，必填且只能一个）+ `dest` + `profileId` + `transfer` | 实测空 body 报 `sourceDir、sourceFile、sourceFiles 必须且只能填写一个` |
| `/organize/tasks` | GET | **不支持分页** | 固定返回 20 条 |
| `/organize/records` | GET | **`profileId` 必填** + `page` | 缺 `profileId` → `400 profileId 必填`；返回 `data.groups[]` |
| `/tmdb/{movie\|tv}/{id}` | GET | — | 实时拉取并刷入本地缓存 |
| `/tmdb-cache` | GET | `page`,`pageSize`,`search` | 返回 `{items,page,total}`，`total` 实测 55176 |
| `/tmdb-cache/{id}/refresh` | POST | — | 条目不存在 → `400 cache entry not found` |
| `/strm/profiles` | GET | — | STRM 方案列表 |

### 8.2 分页行为对照（★ 每个端点都不一样）

| 端点 | `limit` | `page/pageSize` | 结论 |
|---|---|---|---|
| `/tmdb-cache` | ❌ 无效 | ✅ 生效（`page=2&pageSize=2` 正确返回第 2 页） | 标准分页 |
| `/organize/tasks` | ❌ 无效 | ❌ 无效（固定 20 条） | 无分页 |
| `/plugins/<id>/runs` | ❌ 无效 | ❌ 无效（返回全量） | 无分页，本地切片 |
| `/plugins/<id>/logs` | ✅ 生效 | ❌ 无效 | 半支持 |

### 8.3 `/organize/tasks` 元素结构

```jsonc
{"id":93236,"profileId":1148,"status":"done",
 "sourceDir":"/小小玻珠/预处理/待整理","destDir":"/小小玻珠/云盘影视",
 "totalFiles":0,"doneFiles":0,"skippedFiles":0,"upgradedFiles":0,"errorFiles":0,
 "movieFiles":..,"tvFiles":..,"warningCount":1,"warningLog":"...","errorLog":null,
 "triggerKind":"...","createdAt":"...","startedAt":"...","finishedAt":"...",
 "achievementRecordedAt":"...","dailySummaryRecordedAt":"..."}
```

---

## 9. 平台容量与资源限制

| 项 | 限制 | 超限表现 |
|---|---|---|
| 插件包 ZIP 大小 | 40 MiB | `请上传有效的 ZIP 文件，大小不超过 40 MiB` |
| **插件包总数** | **100 个** | `400 PLUGIN_PACKAGE_LIMIT 插件包达到 100 个，请清理未使用版本后重试`（**全局阻断**） |
| ticket 有效期 | 30 分钟 | `400 INSPECTION_EXPIRED` |
| action 通道 | 30 分钟 / 次 | 超时中断 |
| query / page 通道 | **10 秒** | 超时 |
| init / stop | 10 秒 | 超时 |
| worker 内存 | 512 MiB | OOM |
| storage KV | 32 MiB / 10000 键 / 单值 256 KiB / 单批 1 MiB | `storage.set` 抛错 |
| `files.scan` 单次 | 10000 个文件 | 截断 |
| `ffprobe.cache_exists` | 每运行 500 次 | 抛错 |
| `http.request` | 每运行 50 次 | 抛错 |

> **`PLUGIN_PACKAGE_LIMIT` 是最容易自伤的一条**：它按 `id@内容哈希` 计数，开发期反复重装会迅速耗尽，而且不影响只是当前插件，是全平台装不了新插件。

---

## 10. 对既有白皮书的勘误

[MediaTidy核心体系破译与mt-console优化技术白皮书.md](MediaTidy核心体系破译与mt-console优化技术白皮书.md) 中以下 4 点与本次实测不符，建议修订（其余内容仍然成立）：

| # | 白皮书原文 | 实测结论 |
|---|---|---|
| 1 | 「`grants.roots` 必须是结构体对象列表，误传 `["/strm"]` 会导致插件状态变 `error`」 | 传字符串数组返回 `400 {"code":-1,"message":"插件请求参数无效或过大"}`，**状态不会变 error**。另外 `roots: []` 是合法且正常的（非文件类插件就是空） |
| 2 | 「`PUT` 会覆盖，`storage` 单 Key 256 KiB」 | 正确，但需补充：**不传 `config` 会清空配置**（不是「保持原值」） |
| 3 | 「`/organize/records` 查询整理历史记录」 | **`profileId` 是必填参数**，缺省直接 400；返回结构是 `data.groups[]` 而非平铺记录 |
| 4 | 「`/api/plugins/{id}/queries/{name}` 执行查询」 | 需补充：**body 必须是 `{}`**，传 `{"input":{}}` 会 400 |

另外白皮书未记录、但 console 必须处理的三件事：

- 安装 200 但实际 `failed` 的静默陷阱（见 §2.3）
- 插件包 100 个上限（见 §2.4、§9）
- `runs` 的 `interrupted` 状态（见 §4.2）

---

## 11. mt-console 实现建议

### 11.1 客户端预校验（能省掉 80% 的无效往返）

在打包上传**之前**本地校验，把错误直接呈现给用户：

```python
IDENT = re.compile(r"^[a-z0-9.-]+$")

def validate_manifest(m: dict) -> list[str]:
    errs = []
    if not IDENT.match(m.get("id", "")):            errs.append("id 只能含小写字母/数字/-/.")
    if not m.get("actions"):                          errs.append("actions 必须 1–50 项")
    for a in m.get("actions") or []:
        if not IDENT.match(a.get("id", "")):          errs.append(f"action id 非法: {a.get('id')}")
        if "label" not in a:                          errs.append(f"action {a.get('id')} 缺 label")
    for q in m.get("queries") or []:
        if not IDENT.match(q.get("id", "")):          errs.append(f"query id 非法: {q.get('id')}")
    for t in m.get("triggers") or []:
        if not IDENT.match(t.get("key", "")):         errs.append(f"trigger key 非法: {t.get('key')}")
        if t.get("type") not in ("schedule", "event"): errs.append("trigger type 只能是 schedule/event")
        for bad in ("name", "cron", "description"):
            if bad in t:                              errs.append(f"triggers[].{bad} 不支持，请在代码里 upsert")
    for c in m.get("config") or []:
        if c.get("type") not in ("text","textarea","number","boolean","files","profile","stringList"):
            errs.append(f"config {c.get('key')} type 非法")
        if "secret" in c:                             errs.append("config[].secret 不支持")
    cs = m.get("configSchema") or {}
    if cs.get("properties"):                          errs.append("configSchema.properties 必须为空，字段写在 config[]")
    return errs
```

### 11.2 一个健壮的 API 客户端

```python
class MtError(Exception):
    def __init__(self, http: int, code: str, message: str):
        self.http, self.code, self.message = http, code, message
        super().__init__(f"[{http}/{code}] {message}")

def call(method: str, path: str, *, json_body=None, files=None, timeout=60):
    r = SESSION.request(method, BASE + path, json=json_body, files=files, timeout=timeout)
    try:
        d = r.json()
    except ValueError:
        raise MtError(r.status_code, "NON_JSON", r.text[:200])
    if d.get("code") == 0:
        return d.get("data")
    err = (d.get("data") or {}).get("error") or {}
    raise MtError(r.status_code, err.get("code") or str(d.get("code")), err.get("message") or d.get("message") or "")
```

要点：

- 成功码看**顶层** `code == 0`，错误码看 **`data.error.code`**。
- `code == -1` 通道没有 `error` 对象，用 `str(d["code"])` 兜底。
- 401 单独处理（触发重新登录，不要当成业务错误弹给用户）。

### 11.3 安装流程的参考实现

```python
def install(zip_path: str, wait: int = 240) -> str:
    data = call("POST", "/plugins/inspect/upload",
                files={"file": (os.path.basename(zip_path), open(zip_path, "rb"), "application/zip")},
                timeout=180)
    ticket = data["ticket"]
    manifest = data["release"]["manifest"]
    pid = manifest["id"]

    # grants 只能是扁平能力 map
    call("POST", "/plugins/install",
         {"ticket": ticket, "grants": {p: True for p in manifest.get("permissions", [])}})

    # 轮询：uv 首次装依赖实测 2~3 分钟
    deadline = time.time() + wait
    while time.time() < deadline:
        try:
            st = call("GET", f"/plugins/{pid}")["status"]
            if st == "ready":
                return "ready"
        except MtError as e:
            if e.http == 404:
                # 可能已静默失败，查 DB
                reason = peek_install_error(pid)
                if reason:
                    raise MtError(400, "INSTALL_FAILED", reason)
        time.sleep(3)
    raise MtError(408, "TIMEOUT", "安装超时，请检查 plugin_records.installJob")
```

`peek_install_error` 需要宿主文件访问（容器内或挂载 DB）：

```python
def peek_install_error(plugin_id: str, db="/app/data/mediatidy.db"):
    import sqlite3, json
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    for (body,) in c.execute(
        "SELECT body FROM plugin_records WHERE kind='installJob' ORDER BY rowid DESC LIMIT 20"):
        d = json.loads(body)
        if d.get("pluginId") == plugin_id and d.get("status") == "failed":
            return d.get("error")
    return None
```

### 11.4 建议给 console 加的功能

| 功能 | 理由 |
|---|---|
| **包体量仪表** | 显示 `packages/` 目录数量占 100 的比例，接近上限时告警。这是最容易自伤且最难自查的限制 |
| **一键清理旧包** | 保留每 `id@version` 最新一份（§2.4 的脚本） |
| **安装失败原因直读** | 安装超时后自动查 `plugin_records`，把 `error` 呈现给用户，而不是「装不上」 |
| **插件健康三元组** | `status` + `enabled` + `plugin_logs` 最近 error，替代「`/ui` 200 就算好」 |
| **runs 本地分页 + 状态归类** | 服务端不分页；`interrupted` 需单独展示 |
| **manifest 预校验** | §11.1，把 15 类清单错误在本地拦下 |
| **grants 读-改-写封装** | 避免「改配置把授权改没了」这类事故 |

---

## 附录 A：完整错误码表

| `data.error.code` | HTTP | 触发条件 | 处置 |
|---|---|---|---|
| `INVALID_MANIFEST` | 400 | 清单字段非法（见 §6） | 修正清单，**用 message 定位具体字段** |
| `INVALID_PACKAGE` | 400 | 非 ZIP / 文件数超限 | 检查包结构 |
| `DEPENDENCY_LOCK_REQUIRED` | 400 | 缺 `pyproject.toml` 或 `uv.lock` | 用容器内 uv 生成锁文件 |
| `DEPENDENCY_INSTALL_FAILED` | 400 | 依赖装不上 | 检查 wheel 平台/版本 |
| `PLUGIN_PACKAGE_LIMIT` | 400 | 包总数达 100 | 清理旧包（§2.4），**全局阻断** |
| `INSPECTION_EXPIRED` | 400 | ticket 过期/不存在/参数缺失 | 重新 `inspect/upload`；**该码无法区分三者** |
| `PACKAGE_INTEGRITY_FAILED` | 400 | 包内容与 commit 不符 | 重新打包，勿手改容器内文件 |
| `INVALID_REPOSITORY` | 400 | `/plugins/inspect` 的 url 非公开仓库 | 改用 zip 上传 |
| `INVALID_UI_PAGE` | 400 | UI AST 不合法 | 见 §7.2，message 会指明组件路径和允许属性 |
| `NOT_FOUND` | 404 | 资源不存在 | 插件未装 / query 名不存在 |
| `OPERATION_FAILED` | 400 | **万能兜底**，见 §4.1 | 客户端预校验，勿依赖此码定位 |
| `BUSY` | 400 | 同一 action 有在途流程 | 等待完成或 cancel |
| —（`code: -1`） | 400 | 参数结构错误（如 roots 非对象数组） | 检查 body 结构 |

## 附录 B：最小可用插件包

能通过 `inspect/upload` 的最小结构（实测 200 OK）：

```
probe.zip
├── plugin.json
├── plugin.py
├── pyproject.toml
└── uv.lock
```

```jsonc
// plugin.json
{
  "id": "media.probe",
  "name": "probe",
  "version": "0.0.1",
  "sdkVersion": "2",
  "entry": "plugin.py:Plugin",
  "description": "p",
  "permissions": ["storage"],
  "actions": [{"id": "go-1", "label": "Go", "fields": []}],
  "queries": [],
  "config": [],
  "configSchema": {"type": "object", "properties": {}},
  "ui": {"mode": "python", "version": "2", "entry": "", "icon": ""}
}
```

```toml
# pyproject.toml
[project]
name = "media.probe"
version = "0.0.1"
requires-python = "==3.12.*"
dependencies = []
```

```toml
# uv.lock
version = 1
requires-python = "==3.12.*"

[[package]]
name = "media-probe"
version = "0.0.1"
source = { virtual = "." }
```

```python
# plugin.py
from mt_plugin import PluginBase

class Plugin(PluginBase):
    pass
```

## 附录 C：验证脚本

复现本文全部结论的最小脚本（需 `requests`）：

```python
import requests, json
BASE = "http://127.0.0.1:2019/api"

s = requests.Session()
tok = s.post(f"{BASE}/auth/login",
             json={"username": "admin", "password": "PASSWORD"}).json()["data"]["token"]
s.headers["Authorization"] = f"Bearer {tok}"

def show(label, r):
    print(f"{label:<44} {r.status_code} {r.text[:100]}")

# 1. 分页行为矩阵
show("tmdb-cache page2",        s.get(f"{BASE}/tmdb-cache?page=2&pageSize=2"))
show("tmdb-cache limit=2",      s.get(f"{BASE}/tmdb-cache?limit=2"))
show("runs limit=2",            s.get(f"{BASE}/plugins/media.dup-scan/runs?limit=2"))
show("logs limit=3",            s.get(f"{BASE}/plugins/media.dup-scan/logs?limit=3"))
show("logs level=error",        s.get(f"{BASE}/plugins/media.dup-scan/logs?level=error"))

# 2. query 通道 body 契约
show("query bare {}",           s.post(f"{BASE}/plugins/media.dup-scan/queries/rules", json={}))
show("query {input:{}}",        s.post(f"{BASE}/plugins/media.dup-scan/queries/rules", json={"input": {}}))

# 3. runs 通道 action 键
show("runs {action:nosuch}",    s.post(f"{BASE}/plugins/media.dup-scan/runs",
                                      json={"action": "no-such-action", "input": {}}))
show("runs {actionId:nosuch}",  s.post(f"{BASE}/plugins/media.dup-scan/runs",
                                      json={"actionId": "no-such-action", "input": {}}))

# 4. grants 语义（务必先备份并恢复）
cur = s.get(f"{BASE}/plugins/media.dup-scan").json()["data"]
orig = {"enabled": cur["enabled"], "config": cur["config"], "grants": cur["grants"]}
show("PUT roots=['/strm']",     s.put(f"{BASE}/plugins/media.dup-scan",
                                    json={**orig, "grants": {**orig["grants"], "roots": ["/strm"]}}))
s.put(f"{BASE}/plugins/media.dup-scan", json=orig)   # 恢复
```

---

**最后更新**：2026-10-29 · 基于 MT `sdkVersion 2` 实机验证。
凡本文未覆盖的行为，建议先用「附录 C」的探针脚本验证，再写进 console，不要照搬推测。
