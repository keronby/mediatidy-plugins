# MT AI 能力实测报告

> 实测环境：MediaTidy 0.1.13，2026-10-29
> 探针插件：`media.aiprobe`（media.sha1-dedup 系列开发用）
> 官方插件版本：`official.ai-organize` 2.0.8

## 一、MT AI 配置现状

`GET /api/system/config` 的 `ai` 段：

```json
{
  "enabled": true,
  "identityRecognitionEnabled": true,
  "episodeCompletionEnabled": false,
  "baseURL": "http://192.168.10.207:3065/v1",
  "apiKey": "sk-a••••605f",
  "model": "deepseek-v4.1-flash",
  "webSearchEnabled": true,
  "tavilyAPIKey": "sk_t••••CTRU",
  "batchEnabled": true,
  "batchSize": 8,
  "batchWaitMs": 1500,
  "minIntervalMs": 2000,
  "maxConcurrent": 3,
  "timeoutMs": 90000,
  "reasoningEffort": "low",
  "reasoningTimeoutMs": 120000
}
```

**已修复的问题**：`model` 原值为 `" deepseek-v4.1-flash"`（**前导一个空格**）。
MT 原样拼进上游请求，上游判定模型不存在返回 HTTP 404，
插件侧报 `AI 接口或模型不存在（HTTP 404），请检查服务地址和模型名称`。
去掉空格后 404 消失。

> ⚠️ `POST /api/system/config` 是**整体覆写**而非 merge。
> 只传单个字段会把未传的段落全部重置为空，且脱敏 key 会被写成真实值导致 401。
> 写入前必须 `GET` 完整配置、改目标字段、整体 POST。

## 二、AI 能力实测矩阵

`ai.capabilities()` 全部返回 `available`：

| 能力 | available | reason |
|---|---|---|
| chat | true | available |
| webSearch | true | available |
| tmdb | true | available |

但**“声明可用”不等于“实际能用”**，逐条实测结果：

| # | 调用方式 | 结果 | 错误 / 返回 |
|---|---|---|---|
| 1 | `ai.chat(nativeWebSearch=False)` | ✅ 通 | 返回 `"你好"`；`modelRequests:1, modelDurationMs:1465` |
| 2 | `ai.complete(system, input, schema)` | ✅ 通 | 结构化 JSON 输出正常；`outputMode: json_schema` |
| 3 | `ai.chat(nativeWebSearch=True, singleRequest=True)` | ❌ 断 | `服务商未返回已完成的原生搜索记录，不能按模型记忆整理` |
| 4 | `ai.web.search(query)` | ❌ 断 | `公开资料搜索失败、超时或被取消` |

### 补充实测：模型其实“知道答案”

问《一出好戏》TMDB ID：

- `nativeWebSearch=False` → 返回 `"517991"`（**正确**）
- `nativeWebSearch=True` → 失败

说明模型是**凭训练记忆答对**的，服务商并未执行搜索、也未返回
`nativeWebSearch.performed=true` 标记。MT 明确拒绝这种结果。

### 参数约束（重要）

- `nativeWebSearch=True` **必须**配 `singleRequest=True`，且不能混用 `tools`。
  违反报：`原生搜索需要 singleRequest=true，且不能混用函数工具`
- MT 日志显示 `providerProfile: openai_compatible`。
  该 profile 下服务商通常不返回原生搜索元数据。

## 三、官方 AI 插件的硬依赖

`official.ai-organize` 2.0.8 的 `plugin.py` 中：

```python
# 常量 OUTPUT_CONTRACT 硬编码了原生搜索要求
OUTPUT_CONTRACT = "本次必须使用服务商原生网页搜索，在同一次响应内完成识别。仅返回 JSON 对象 {items:[...]}..."

# _identify() 第 415 行
response = ctx.mt.ai.chat(
    messages=messages,
    file_refs=[file["ref"] for file in files],
    singleRequest=True,
    native_web_search=True,      # ← 硬依赖
)
search: dict[str, Any] = dict(response.get("nativeWebSearch") or {})
if search.get("performed") is not True:
    raise SDKError(
        "宿主或服务商未确认原生搜索已完成，请更新 MT 并检查模型联网支持；未提交整理",
        "AI_NATIVE_SEARCH_NOT_PERFORMED",
    )
```

**结论**：只要服务商不返回 `nativeWebSearch.performed=true`，
官方插件在 `_identify` 第一步就抛错，整个识别流程无法启动。
这**不是 bug 而是 MT 的安全设计**——禁止按模型记忆整理，保证识别结果可追溯。

## 四、可行改造路线

### 路线 A：`ai.web.search` + `ai.complete` 组合（推荐，待 Tavily 修复）

- 用 `ai.web.search(query)` 做真实搜索，拿 `items[{title, summary, url}]`
- 把搜索结果作为资料喂给 `ai.complete(system, input, schema)` 做结构化判定
- 仍然是**真搜索**，只是搜索时机由插件控制，不依赖服务商原生能力
- 前提：`ai.web.search` 必须修通（当前报 `公开资料搜索失败、超时或被取消`）

### 路线 B：`ai.chat(nativeWebSearch=False)` + 显式搜索证据

- 不用 `complete`，用 `chat` 多轮：先搜、再把结果塞进对话
- 兼容性最好，但需要自己实现 JSON 解析与重试

### 路线 C：换真支持原生搜索的服务商

- 需要服务商透传 Google Search grounding 等原生搜索元数据
- 反代 Gemini 通常不透传 → 难以指望
- 若换成官方 Gemini / OpenAI 带 web search 的 endpoint，则**官方插件可零改动运行**

## 五、其它实测发现（对 mt-console 有用）

- `GET /api/logs` **无需插件即可读 MT 主程序日志**，返回 `{data:{logs:[{time,level,caller,msg,extra}]}}`
  - 但**固定只返回 50 条**，`?limit=2000` / `?lines=2000` / `?tail=2000` 全部无效
- `GET /api/system/config` 是唯一可读系统配置的端点，密钥一律脱敏
- `ai.chat` 的返回结构：`{message:{role,content}, usage, metrics, toolsUnavailable?, nativeWebSearch?}`
  - `nativeWebSearch` 仅在 `nativeWebSearch=True` 且服务商支持时出现
- MT 内部还有 `license.v3_request_failed` 心跳/能力探测超时告警（每 ~40s 一次），与插件无关
