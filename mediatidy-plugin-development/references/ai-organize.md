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
