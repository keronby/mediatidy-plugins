# SDK v2 能力清单（生成文件）

同步客户端为 `ctx.mt`，异步客户端为 `ctx.amt`。参数可传 JSON 字典，或使用 snake_case 关键字；宿主契约中的 JSON 字段使用 camelCase。输入输出的完整 JSON Schema 见 [语言无关契约](/downloads/plugins-sdk-v2/methods.json)。

每次调用重新验证实例、构建、配置修订、资源、授权与配额。`organize.submit` 仍是宿主确认草稿所需的权限，不是可调用的 SDK 方法。

| Python 方法 | 权限 | 每运行上限 | 允许通道 |
|---|---|---:|---|
| `emby.servers.list` | emby.api | 500 | action, query, page |
| `emby.request` | emby.api | 500 | action |
| `ffprobe.cache_exists` | ffprobe.cache.exists | 500 | action, query, page |
| `ai.capabilities` | — | 500 | action |
| `ai.chat` | ai.chat | 78 | action |
| `ai.complete` | ai.complete | 32 | action |
| `ai.web.search` | ai.web.search | 32 | action |
| `config.get` | — | 500 | action, query, init, stop, page |
| `files.batch` | files.write | 500 | action |
| `files.copy` | files.write | 500 | action |
| `files.delete` | files.delete | 500 | action |
| `files.download` | files.read | 100 | action |
| `files.list` | files.read | 500 | action, query, page |
| `files.mkdir` | files.write | 500 | action |
| `files.move` | files.write | 500 | action |
| `files.read_text` | files.read | 500 | action, query, page |
| `files.rename` | files.write | 500 | action |
| `files.roots` | files.read | 500 | action, query, page |
| `files.scan` | files.read | 500 | action |
| `files.search` | files.read | 500 | action |
| `files.stat` | files.read | 500 | action, query, page |
| `files.upload` | files.write | 100 | action |
| `files.write_text` | files.write | 500 | action |
| `http.request` | http.request | 50 | action |
| `log` | — | 20000 | action, query, init, stop, page |
| `media.parse` | media.parse | 500 | action |
| `media.recognize` | media.recognize | 100 | action |
| `notification.send` | notification.send | 10 | action |
| `organize.manual.prepare` | organize.prepare | 500 | action |
| `organize.manual.prepare_batch` | organize.prepare | 100 | action |
| `organize.manual.run` | organize.manual.run | 500 | action |
| `organize.profile` | organize.read | 500 | action, query, page |
| `organize.profiles` | organize.read | 500 | action, query, page |
| `organize.run` | organize.run | 500 | action |
| `quality.compare` | quality.compare | 500 | action |
| `quality.evaluate` | quality.evaluate | 500 | action |
| `quality.evaluate_batch` | quality.evaluate | 500 | action |
| `quality.prepare` | quality.prepare | 500 | action |
| `quality.upgrade` | quality.compare | 500 | action |
| `records.list` | records.read | 500 | action, query, page |
| `rules.effective` | rules.read | 500 | action, query, page |
| `rules.get` | rules.read | 500 | action, query, page |
| `rules.list` | rules.read | 500 | action, query, page |
| `runtime.capabilities` | — | 500 | action, query, init, stop, page |
| `runtime.checkpoint` | — | 5000 | action, query, init, stop, page |
| `runtime.info` | — | 500 | action, query, init, stop, page |
| `runtime.progress` | — | 5000 | action |
| `runtime.resources` | — | 500 | action, query, init, stop, page |
| `runtime.result` | — | 5000 | action |
| `secrets.get` | secrets.use | 30 | action, init, stop |
| `storage.batch` | storage | 500 | action, init, stop |
| `storage.delete` | storage | 500 | action, init, stop |
| `storage.get` | storage | 5000 | action, query, init, stop, page |
| `storage.list` | storage | 500 | action, query, init, stop, page |
| `storage.set` | storage | 5000 | action, init, stop |
| `storage.usage` | storage | 500 | action, query, init, stop, page |
| `strm.cleanup` | strm.cleanup | 10 | action |
| `strm.preview` | strm.read | 500 | action |
| `strm.profile` | strm.read | 500 | action, query, page |
| `strm.profiles` | strm.read | 500 | action, query, page |
| `strm.run` | strm.run | 10 | action |
| `strm.verify` | strm.run | 10 | action |
| `system.config` | system.read | 500 | action, query, page |
| `system.info` | system.read | 500 | action, query, page |
| `tasks.cancel` | tasks.cancel | 500 | action |
| `tasks.get` | tasks.read | 500 | action, query, page |
| `tasks.items` | tasks.read | 500 | action, query, page |
| `tasks.list` | tasks.read | 500 | action, query, page |
| `tasks.wait` | tasks.wait | 500 | action |
| `tmdb.detail` | tmdb.read | 500 | action |
| `tmdb.search` | tmdb.read | 500 | action |
| `tmdb.season` | tmdb.read | 500 | action |
| `triggers.events.create` | events.subscribe | 100 | action, init, stop |
| `triggers.events.delete` | events.subscribe | 100 | action, init, stop |
| `triggers.events.list` | events.subscribe | 100 | action, init, stop |
| `triggers.events.pause` | events.subscribe | 100 | action, init, stop |
| `triggers.events.update` | events.subscribe | 100 | action, init, stop |
| `triggers.events.upsert` | events.subscribe | 100 | action, init, stop |
| `triggers.schedules.create` | triggers.manage | 500 | action, init, stop |
| `triggers.schedules.delete` | triggers.manage | 500 | action, init, stop |
| `triggers.schedules.list` | triggers.manage | 500 | action, init, stop |
| `triggers.schedules.pause` | triggers.manage | 500 | action, init, stop |
| `triggers.schedules.update` | triggers.manage | 500 | action, init, stop |
| `triggers.schedules.upsert` | triggers.manage | 500 | action, init, stop |
