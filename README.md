# MediaTidy Plugins

[MediaTidy](https://github.com/murongyun8/MediaTidy) 第三方插件集（Python SDK v2），共 3 个插件。

## 插件

| 插件 ID | 名称 | 版本 | 说明 |
| :--- | :--- | :--- | :--- |
| `media.dup-scan` | STRM 重复扫描 | 1.3.2 | 在作品目录内扫描重复 `.strm`，按 MediaTidy 质量评分规则（`总分 = Σ(weight × 位置分)`，与 MT 同口径）与多版本槽位（ISO / 普通、DV / HDR 等）识别应保留 / 应删除版本。本地根走 `os.walk` 快路径（50w+ 库秒级），云盘根走宿主 `files.scan` 异步任务。删除按 STRM 内容分组：`http(s)` 分享删 `.strm` + 同名 `.nfo` + `-mediainfo.json`；本地链接直接删 STRM 内容指向的媒体文件路径（CD2 挂载，同步删云端），同时删 `.strm` 本体与附带文件。 |
| `media.series-dedup` | 剧集查重（影视重整分流） | 1.0.0 | 按 TMDB ID 扫描电影 / 电视剧目录，比对 TMDB 最新标题 / 年份，按 STRM 链接属性（分享链 / 云盘链 / 混合链）自动分流重整任务。扫描根、整理方案 ID、目标目录均在插件配置中填写，无硬编码预设。 |
| `media.sweep` | 整理清道夫 | 1.4.1 | MT 整理后的清扫环节：用本地 sha1 归约库比对云盘未识别目录，视频全部命中的作品视为已整理并整包删除，未命中的原地保留；其余文件交 AI 识别（原生搜索 → 公开资料搜索 → 纯文件名三级降级）后交 MT 整理。零第三方依赖。 |

## 安装（插件中心 / 集合包）

本仓库根目录 `plugins.json` 为 MediaTidy 插件集合索引（`schemaVersion: 1`），可直接在 MT 插件中心添加本仓库地址安装 / 更新全部插件；也可先用各插件 `tools/` 下的打包脚本（`pack_local.py` / `build.sh`）打出单插件 `.zip`，再在 MT「插件管理 → 安装插件」上传。

## 部署脚本的环境变量（脱敏）

`tools/deploy.py`、`tools/logs.sh`、`tools/reinstall.py` 等本地部署脚本不再硬编码任何账号密码、内网地址或站点专属根 ID，运行时通过环境变量传入：

- `MT_BASE`（默认 `http://127.0.0.1:2019/api`）
- `MT_USER`（默认 `admin`）
- `MT_PASSWORD`（必填，不设默认值）
- `MT_DB_PATH`、`MT_CLOUD_ROOT_ID`、`MT_LOCAL_ROOT_ID`、`MT_DEPLOY_USER`（按部署环境覆盖）

## 目录结构

```
plugins.json          # 插件集合索引（3 个插件）
media.dup-scan/       # STRM 重复扫描
media.series-dedup/   # 剧集查重
media.sweep/          # 整理清道夫
```

## License

MIT
