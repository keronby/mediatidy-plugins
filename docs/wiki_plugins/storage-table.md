# 分键分页与行内操作

使用 Python 3.12 与同版本 SDK，执行 `python -m mt_plugin pack` 后上传 ZIP。
启用时仅需授予 `storage`。需要支持 `Button.size` 的宿主反馈修复版本。

点击“生成 120 组演示结果”，完成后逐页查看并标记保留。示例使用合成数据，不访问媒体目录，不删除文件。

每次扫描使用运行 ID 隔离结果，一组一个键，另存状态摘要。查询通过 `storage.list` 游标读取 20 条，超过返回容量限制时以实际返回条数和 `nextCursor` 为准。
写入失败会保留部分结果并抛出错误；状态标记也失败时仍显示“尚未完成”，需查看宿主运行状态。标记操作用修订号防止覆盖并发修改。

`invoke` 只等待动作入队；宿主在当前运行结束后刷新查询。按钮事件后直接追加 `query` 可能先读到旧值。
查询结果位于 `query.summary.data`，当前表格行位于 `row`。

示例保留旧扫描数据供排错；反复运行仍受实例配额约束，可在宿主“数据与存储”清理。
真实插件应提供保留策略：对已结束且不再展示的扫描，按旧扫描前缀调用 `storage.list(values=False)`，逐页将键交给 `storage.batch` 删除，每批最多 100 项，最后删除摘要；禁止清理仍在运行或正在展示的扫描。

## 示例源码

- [plugin.py](/downloads/plugins-sdk-v2/examples/storage-table/plugin.py)
- [plugin.json](/downloads/plugins-sdk-v2/examples/storage-table/plugin.json)
- [pyproject.toml](/downloads/plugins-sdk-v2/examples/storage-table/pyproject.toml)
- [uv.lock](/downloads/plugins-sdk-v2/examples/storage-table/uv.lock)
