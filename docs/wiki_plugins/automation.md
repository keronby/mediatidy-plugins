# 生命周期、调度与续接

每个启用的实例拥有一个长期存活的 Python worker 和插件对象。连接、第三方库、缓存与 asyncio 事件循环可复用；内存可丢失，需要跨重启保留的数据写入自身 KV 或 `ctx.data_dir`。

## 生命周期与调用通道

入口继承 `mt_plugin.Plugin`，支持 `validate_config(config, ctx)`、`on_init(ctx)`、`on_stop(ctx)`、`get_page(ctx)`，以及 `@action`、`@query`、`@continuation`。处理函数可同步或异步；同步使用 `ctx.mt`，异步使用 `await ctx.amt...`。在异步处理函数中直接使用同步客户端会报错，避免阻塞事件循环。

配置保存、启用和更新切换前，宿主在独立进程调用 `validate_config`，不运行初始化。返回 None 或 `{ "fieldErrors": {"字段": "原因"} }`；失败保留当前安装、配置和 worker。独立校验不保证任意 Python 代码没有原生副作用，作者必须遵守纯校验契约。

`on_init` 覆盖启用、启动恢复、配置变化与更新；成功后才投递业务。`on_stop` 覆盖停用、重配置、更新、卸载和正常关闭；停止失败仍清理 worker。初始化失败或进程退出进入 error，停止投递，用户可查日志并重试；中断业务不自动重放。

业务通道串行执行；查询与页面有独立的串行读取通道。生命周期切换独占实例。每个公开动作只允许一个 queued/running/waiting 流程，重复手动请求返回 BUSY，自动触发记录跳过。

`ctx` 提供 plugin_id、instance_id、run_id、flow_id、source、package_hash、config_revision、config、files、event、reason、data_dir、work_dir。授权由宿主绑定到实际 RPC 调用，不能通过修改这些元数据扩大权限。

耗时宿主操作返回任务引用。调用 `ctx.mt.tasks.wait(refs=[...], completion={"action":"collect", "input":{...}})` 后立即返回。完成动作需在清单标记 `kind: "continuation"`，并用 `@continuation("collect")` 注册；它不能被页面、公开 API、定时或普通订阅调用。等待会释放业务通道，原公开动作名额仍被占用；续接保持原构建、配置、根流程与幂等信息。

日志、进度或 Storage 写入应在 `tasks.wait` 之前完成。登记等待后（包括 `finally` 中）继续调用 SDK 会收到 `FLOW_WAITING`；异步接口遵循同样规则。

```python
ctx.mt.log(level="info", message="任务已提交，等待完成后继续")
ctx.mt.tasks.wait(refs=[task["ref"]], completion={"action": "collect", "input": {}})
return {"waiting": True}
```

## 定时与方案事件

触发器使用清单声明的稳定 key；在初始化中用 `ctx.mt.triggers.schedules.upsert(...)` 或 `ctx.mt.triggers.events.upsert(...)` 登记。**schedule 参数是 `declarationKey`（Python `declaration_key`），event 参数是 `key`**。重复初始化复用登记并保留用户暂停状态，新增登记默认暂停。删除清单声明会清理对应触发器。

定时定义由 MT 统一调度器保存，不在 KV 或 Python 进程里另建 Cron。五段 Cron 顺序为分钟、小时、日、月、星期；`Local` 指服务端时区。一次性任务使用 `kind: "once"`、带时区的 `at` 和空 `cron`。普通更新需要当前 revision；启用及恢复用户暂停由宿主界面操作。

方案完成订阅还需 `events.subscribe` 与具体方案事件授权，接收 `kind/profileId/status/total/completed/failed`，不返回路径、凭据或原始探测数据。插件发起的业务不会再次产生方案订阅事件，自己的任务完成用 `tasks.wait` 续接，避免形成循环。

## 取消和运行预算

取消先通知调用并撤销其后续 SDK 请求。协作取消的异步处理可保留 worker；同步处理拒绝退出或超过宽限时终止 worker 与子进程，其他受影响调用标记中断。暂停触发器、取消插件流程、取消宿主任务分别操作。

宿主默认预算：导入 30 秒，配置校验 2 秒，初始化/停止各 10 秒，查询/页面各 10 秒，动作 30 分钟，内存 512 MiB，取消宽限 1 秒。管理员可设置 MT_PLUGIN_STARTUP_TIMEOUT、MT_PLUGIN_VALIDATION_TIMEOUT、MT_PLUGIN_LIFECYCLE_TIMEOUT、MT_PLUGIN_READ_TIMEOUT、MT_PLUGIN_ACTION_TIMEOUT、MT_PLUGIN_CANCEL_GRACE（如 10s、30m）和 MT_PLUGIN_MEMORY_MIB；插件清单不能取消这些限制。
