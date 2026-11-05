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
