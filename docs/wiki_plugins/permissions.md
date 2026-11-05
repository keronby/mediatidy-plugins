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
