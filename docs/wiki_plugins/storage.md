# 数据、工作目录与密钥

## 数据放在哪里

| 数据 | 存放位置与生命周期 |
|---|---|
| 配置 | 宿主保存，`ctx.config` 是当前调用快照；页面 config 是未保存草稿 |
| 业务 JSON | `ctx.mt.storage`，主数据库按安装实例隔离，更新保留 |
| 持久文件 | `ctx.data_dir`，跨 worker 重启保留 |
| 调用临时文件 | `ctx.work_dir`，回调结束后清理 |
| 第三方库临时文件 | worker 生命周期内的临时目录 |
| 日志、任务、运行结果 | 宿主分别管理，不用 KV 复制一套任务系统 |
| 授权媒体目录内的输出 | 用户文件，清空插件 KV 或卸载不删除它们 |

等待续接可能发生在另一调用中，不把跨调用状态放在 work_dir 或仅存内存。上传入队前宿主复制接管工作文件，拒绝入队、取消排队或任务结束时清理传输暂存。

## KV 与并发

`storage.get` 用 `found` 区分不存在和 null；写入可用 `expectedRevision` 防止覆盖并发修改。省略表示无条件，空字符串要求键尚不存在，非空值要求匹配当前修订。有效期使用 `expiresAt`，`storage.batch` 原子提交。

默认每实例 32 MiB、10,000 个键，单值 256 KiB，单次 batch 1 MiB。实际用量及限额查 `storage.usage`，不要假设管理员未调整策略。大量逐文件结果分键保存、查询分页，不能把全部结果写成一个大对象。

限制按实际传输的 JSON UTF-8 字节计算，包含引号、转义及字段名，不按字符数或作品数量计算。超过 256 KiB 的写入明确报错，消息包含操作位置、实际字节数和上限；失败不会覆盖旧值。成功写入的单值超过 200 KiB 时，宿主在插件日志提示接近上限，同一次插件调用最多提醒一次，不记录数据内容。

### 扫描结果分键与游标分页

完整的[分键分页示例](/downloads/plugins-sdk-v2/examples/storage-table/plugin.py)使用合成查重结果，不访问云盘或修改媒体：

- `currentScan` 只保存正在展示的扫描 ID。
- `scans/<runId>/summary` 保存状态和计数；状态为 running、done 或 failed。
- `scans/<runId>/groups/<序号>` 每个键保存一组结果，避免一份 JSON 随扫描持续增长。
- 查询用 `storage.list(prefix=..., cursor=..., limit=20, values=True)`，返回条数可能受 1 MiB 返回容量限制，必须使用返回的 `nextCursor` 继续，不自行计算偏移。

```python
page = ctx.mt.storage.list(prefix=f"scans/{scan_id}/groups/",
                           cursor=cursor, limit=20, values=True)
return {"groups": [entry["value"] for entry in page["items"]],
        "nextCursor": page.get("nextCursor", "")}
```

先写 running 摘要，再逐组写入，全部成功才标记 done。失败要保留已经写入的数据、尽力记录 failed 并抛出原异常；取消或容量耗尽也可能阻止失败标记，因此 running 不等于成功或零结果，页面应提示检查宿主运行状态。运行结果只返回计数和扫描 ID，不复制全部组数据。

对同一结果的修改先 `get` 再使用返回的 `revision` 作为 `expectedRevision`，避免覆盖并发编辑。不同扫描使用不同前缀；旧扫描游标不能继续读取新扫描。

分键不会绕过实例总容量、键数、调用次数或单值限制。单组仍过大时，应继续按版本拆分。示例保留旧数据便于排错，反复运行前可在宿主数据管理中清理；真实插件应仅清理已结束且不再展示的扫描，逐页列出旧前缀的键，再按最多 100 项的 batch 删除，最后删除摘要。

## 更新、备份与恢复

同来源更新保留安装实例和业务数据；不同来源的同名插件不能继承权限及空间。业务数据自行记录格式版本，回退代码不等于回退数据；遇到不支持的格式停止写入并提示恢复备份或迁移。

`on_init` 会在启用、更新或重启等场景调用。初始化只补缺失值，不能每次覆盖用户设置。以下代码需要清单声明且用户授予 `storage`；安装或初始化不会绕过授权。

```python
def on_init(self, ctx):
    saved = ctx.mt.storage.get(key="settings")
    if not saved["found"]:
        ctx.mt.storage.set(key="settings", value={"schemaVersion": 1, "threshold": 10})
```

`found=True` 的值即使为 `null`、空字符串或 `False` 也不是未初始化。不要通过更换插件 ID 或卸载重装实施升级；重新安装与更新的实例归属规则不同。

宿主数据管理可导出 KV 和非密钥配置，恢复前预检来源、格式、容量、配置字段，正式恢复再次检查。恢复覆盖业务键及配置并停用插件；活动或结果未知任务会阻止清空与恢复。备份不含授权、密钥、确认信息及内部额度。

`data_dir` 文件不在 KV 导出中，需单独备份。卸载默认保留业务数据，重新安装时核对来源并显式关联；关联后仍需检查配置与授权。不要把卸载保留等同于系统文件的完整备份。

## 配置与密钥

页面 config 仅为草稿，调用宿主保存后才应用。更新默认保留已有配置，选择性默认值升级见[清单](manifest.md)。等待中的流程不会偷偷替换成新配置。

密钥在宿主外壳设置，清单声明后授予 `secrets.use`，再通过 `ctx.mt.secrets.get` 读取。宿主加密存储并脱敏已知密钥，作者仍不得将密钥放入 KV 导出、配置默认值、页面、日志和异常文本。日志使用见[统一日志](logs.md)。
