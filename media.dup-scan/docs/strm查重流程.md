# strm 查重 · 流程说明（给 AI 读）

本文件描述 mt-console「媒体库维护 → strm 查重」模块的完整工作流程。
读者按此文档即可复现、修改或重写这套逻辑，不需要先读源码。

**与「影视查重」的区别**：
- **strm 查重**（本文件）：在**同一个目录内**找出重复的 strm（同一集/同一部片的多版本），按 MT 评分规则打分保留最优、删除其余。**会真删文件。**
- **影视查重**：跨目录按 tmdb_id 找重复目录，只提交给 MT 整理，不删文件。

---

## 0. 一句话

> `os.walk` 遍历 strm 库 → 同目录内按「作品 + 槽位 + 版本」聚组 → 每组用 MT 评分规则打分，**分数最高者保留、其余全删**（连同其专属 nfo/mediainfo/图片/字幕）→ 支持 dry-run / 回收站 / 直接删除三种模式。

---

## 1. 术语与数据前提

| 术语 | 含义 |
|---|---|
| strm 文件 | 单行文本指针，内容形如 `http://192.168.10.207:2019/api/shareVideoPlayUrl?shareCode=..&fileId=..&sha1=..&fileName=001锂X.2020.1080p.SDR.10-bit.24fps.7.7Mbps.H265.DTSHD-MA 5.1.mkv` |
| 重复组 | 同一个分组键下出现 ≥2 个媒体文件 |
| 洗版 | 保留胜者、删除其余的过程 |
| 槽位（slot） | MT 规则里的资源类型档位，如 `ISO`。不同槽位互不竞争 |
| 伴随文件 | 与 strm 同名的 `.nfo` / `-mediainfo.json` / 海报 / 字幕，随 strm 一起删除 |
| dry-run | 只报告不执行 |

**默认扫描根**：`runtime.scan_paths`，线上实配为 `['/vol1/1000/strm/shareStrm']`（**不是 `云盘影视`**，两者是并存的库）。

---

## 2. 全流程五步

### 第 1 步 · 扫描（`StrmEngine.scan`）

1. **展开并行单元**（`_split_scan_paths`）：把每个 scan_path 展开成直接子目录列表（跳过 `.`、`@` 开头和 `ignored_dirs`），用于多进程并行；若 scan_path 本身直接含媒体文件则保留原路径。
2. **屏蔽过滤**：`ignore_paths` 前缀匹配 + `Path.resolve()` 二次比对。命中的目录**整棵子树剪掉**（`dirs[:] = []`），且不统计其内文件。
3. **并行策略**：`workers > 1` 且展开后 >1 个子目录 → `ProcessPoolExecutor`；否则单进程。
4. **遍历**（`MediaScanner.scan_directory`）：`os.walk` 逐目录处理，**不跨目录**。

### 第 2 步 · 目录内识别媒体文件

对每个目录下的文件：

```
parser.is_media_file(path) → (是否媒体, 是否strm)
  后缀 == .strm            → (True, True)
  后缀 ∈ VIDEO_EXTS        → (True, False)
```

```
VIDEO_EXTS = {.mkv .mp4 .avi .ts .m2ts .rmvb .iso .wmv .mov .flv .webm .rm .mpg .mpeg .vob .strm}
SUB_EXTS   = {.srt .ass .ssa .sub .sup .idx .vtt}
META_EXTS  = {.nfo .json}
IMAGE_EXTS = {.jpg .jpeg .png .webp .tbn}
```

解析文件名（`parser.parse`）得到 `MediaMeta`：`title / media_type(movie|tv) / season / episode / episode_end / edition / resolution / video_codec / hdr / bitrate_kbps / audio_codec / source / target_ext / target_format`。

**ISO 判定**（`iso_mode == "same_dir"` 才做）：读 strm 内容，优先取 `?fileName=` 参数（URL 解码，可能含空格如 `PCM 5.1.iso`），取后缀；没有则扫内容里出现的 `.iso/.mkv` 等。结果写入 `target_format ∈ {iso, standard}` 与 `target_ext`。

### 第 3 步 · 分组（关键规则）

分组键由**四段**拼成：

```python
# ① 主键
if media_type == "tv" and season is not None and episode is not None:
    if episode_end is not None and episode_end > episode:
        key = f"TV:{title}:S{season:02d}E{episode:02d}-E{episode_end:02d}"   # 连集包独立成键
    else:
        key = f"TV:{title}:S{season:02d}E{episode:02d}"
else:
    key = f"Movie:{curr_dir.name}"        # 电影按目录名聚合

# ② 版本变体：Extended / Director's Cut / 加长版 等视为不同条目
if edition:
    key = f"{key}#{edition}"

# ③ 槽位档
tier = scorer.slot_tier(mf)               # "iso" | 槽位名 | "unmatched" | None
if tier is None:
    continue                              # unmatched:skip 且未命中槽位 → 整个文件跳过
key = f"{key}#{tier}"
```

**三条铁律**
1. **连集包 `S01E01-E02` 与单集 `S01E01` 键不同，永不互相洗版**。这是刻意的：否则删掉合并文件会丢失部分集数。
2. **不同槽位档互不竞争**。ISO 单独一档（`#iso`）；未命中任何槽的按 `unmatched` 策略处理：`best` → 全部归入 `#unmatched` 一档；`skip` → 直接跳过不参与。
3. **电影按目录名聚合，不按标题**。同一目录下多个电影文件会被判为重复。

### 第 4 步 · 打分与判定（`QualityScorer`）

#### 4a. 维度得分公式

```
优先级型维度（resolution / video_codec / hdr / source / audio_codec …，规则里有 priority 列表）
  位置分 = 10 × (N - idx) / N          # idx 从 0 开始，N = len(priority)
  得分   = weight × 位置分
  未在 priority 里命中 → 0 分
  值未知             → 0 分（unknown_policy: zero）

码率型维度（bitrate）
  ratio = ln(mbps / 1.0) / ln(50.0 / 1.0)      # 对数压缩，lo=1Mbps hi=50Mbps
  ratio = clamp(ratio, 0, 1)
  得分 = weight × ratio × 10
  mbps <= 1 或未知 → 0 分
```

**实测意义**（`quality-strm`，weight = hdr 40 / bitrate 25 / resolution 20 / video_codec 10）：

| 情形 | 得分 |
|---|---|
| 2160p + HDR10 + H.265 + 20Mbps | 20×10 + 10×10 + 40×10 + 25×8.6 = **915** |
| 1080p + SDR + H.264 + 8Mbps | 20×6.67 + 10×6.67 + 40×3.33 + 25×6.3 = **466** |

HDR 权重最高（40），一条 `Dolby Vision P7` 与 `SDR` 的差距就是 40×10 vs 40×3.33 ≈ 267 分——**远比分辨率更能决定胜负**。

#### 4b. 排除规则

规则里 `exclude.codecs: [Xvid, DivX]`、`exclude.resolutions`、`exclude.hdr_types` 命中的文件：
`_score_file` 直接返回 **-999.0**，必然落败。

#### 4c. 组级 unknown 维度禁用

先算组内所有文件在某维度上是否「一方已知一方未知」；若规则该维度 `unknown_policy == "ignore"` 且组内存在未知，则该维度**对本组所有文件计 0 分**，避免「有值」白拿分。

#### 4d. 排序与胜负（`compare_and_decide`）

```python
sorted_files = sorted(files, key=lambda x: (x.score, 1 if not x.is_strm else 0, x.mtime), reverse=True)
winner = sorted_files[0]
losers = sorted_files[1:]
score_diff = round(winner.score - losers[0].score, 2)

is_wash_out = True          # ← 硬编码，恒为 True
```

**排序优先级**：分数最高 → 真实视频优于 strm（`is_strm` 为假更优）→ mtime 更新者优。

**分差门槛不生效**：代码里 `is_wash_out = True` 是**硬编码**的，注释写明「本地 strm 查重场景下，保留重复的 strm 本身没有意义，分差门槛（`min_score_diff`）也无意义——凡同组聚合出重复即判洗版」。`min_score_diff`（默认 8.0）只用于展示。

#### 4e. 跨集合并文件保护（第二遍）

```
survivor_cover = 所有组胜者覆盖的集号集合
for 每个淘汰者：
    if 它是连集包（episode_end > episode）:
        missing = 它覆盖的集号 - survivor_cover
        if missing 非空:        # 删了就会丢失这些集
            强制保留，从 losers 移除
```

**目的**：防止「`S01E01-E02` 输给 `S01E03` 之外的另一组」导致 E01/E02 全库消失。

### 第 5 步 · 执行清理（`ExecutionAction.execute_cleanup`）

只处理 `is_wash_out == True` 的组：

```
for group in groups:
    if not group.is_wash_out: continue
    for loser in group.losers:
        删除 loser 主文件
        删除 loser.companions 里每一个伴随文件
```

**伴随文件匹配规则**（`CompanionEngine.find_companions_fast`）：

| 类型 | 规则 |
|---|---|
| 元数据 | `stem.nfo`、`stem.json`（扩展名 ∈ META_EXTS 且 stem 完全相同） |
| mediainfo | 文件名以 `stem` 开头且含 `mediainfo`、扩展名 `.json` |
| 字幕 | 扩展名 ∈ SUB_EXTS 且以 `stem.` / `stem-` / `stem_` 开头 |
| 图片 | 扩展名 ∈ IMAGE_EXTS 且以 `stem-` / `stem_` 开头 |

> 注意：`build_companion_index` 因文件名含 `8-bit` 的 `-` 会导致前缀切分错位，所以查询时额外补查 `f"{stem}-mediainfo"` 这个完整变体键。

**三种执行模式**

| `mode` | 行为 |
|---|---|
| `dry-run` | 只统计，`action_status = "simulated"`，不动文件 |
| `trash` | `shutil.move` 到 `trash_dir`，保留**相对 base_dir 的目录结构**；`trash_dir` 缺省 `/tmp/strm_trash_<时间戳>` |
| `delete` | `file_path.unlink()`，**不可恢复** |

**孤儿文件**：`clean_washout` 只传 `orphan_files=[]`，**不删孤儿**（那是 `clean_orphans` 的职责）。

---

## 3. API 清单

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/scan` | 快速扫描（query 参数） |
| POST | `/api/scan` | 扫描，返回重复组，不修改磁盘 |
| GET | `/api/scan/last` | 最近一次扫描结果（跨端/刷新可见） |
| POST | `/api/scan/clear` | 删除扫描结果缓存（不动媒体文件） |
| POST | `/api/clean` | 扫描 + 执行清理 |
| POST | `/api/orphans/clean` | 只清理孤儿伴随文件 |

**`POST /api/scan` body**

```json
{
  "scan_paths": ["/vol1/1000/strm/shareStrm"],
  "ignored_dirs": null,
  "ignore_paths": null,
  "media_type": null,
  "iso_mode": "same_dir"
}
```

`iso_mode`：`same_dir`（读 strm 判 ISO，单独成档）| `other_dir` / `no_import`（不读内容，ISO 与普通格式共同竞争）。

**`POST /api/clean` body**

```json
{
  "scan_paths": ["/vol1/1000/strm/shareStrm"],
  "mode": "dry-run",
  "trash_dir": null,
  "ignored_dirs": null,
  "ignore_paths": null
}
```

**返回结构**

```json
{
  "mode": "dry-run",
  "washed_groups_count": 69,
  "retained_files_count": 69,
  "cleaned_media_files_count": 71,
  "cleaned_companion_files_count": 213,
  "cleaned_orphan_files_count": 0,
  "cleaned_total_bytes": 0,
  "records": [
    {
      "group_key": "TV:宋飞正传:1989:S04E23#unmatched",
      "winner": {"path": "...", "is_strm": true, "score": 915.0, "score_details": {...}},
      "score_diff": 449.0,
      "cleaned_items": [{"path": "...", "status": "simulated", "size": 0, "is_companion": false}]
    }
  ]
}
```

---

## 4. 前端链路

`frontend/src/views/Dedup.vue`（菜单「媒体库维护 → strm查重」）：

| 动作 | 前端调用 | 后端 |
|---|---|---|
| 开始扫描 | `apiTbScan(scanPaths, null, isoMode)` → `POST /api/scan` | `api_scan_post` |
| 执行删除 | `apiTbClean({scan_paths, mode:'delete', trash_dir:null})` → `POST /api/clean` | `api_clean` |
| 清空记录 | `apiScanClear()` → `POST /api/scan/clear` | `api_scan_clear` |

**点击守卫**：`if (!(result.wash_out_groups_count > 0)) { err = '当前扫描结果没有可洗版（删除）的项目'; return }` —— 扫描结果里没有可洗版组时按钮直接拦截。

页面过滤器：全部组 / 仅洗版 / 剧集 / 电影 / ISO 类。

---

## 5. 配置项（`/data/config.yaml`）

```yaml
runtime:
  scan_paths: ['/vol1/1000/strm/shareStrm']     # 默认扫描根
  ignored_dirs: ['.recycle', '@recycle', '.trash', '$recycle.bin', '.ds_store', '@eaDir', '.git']
  ignore_paths: []                               # 屏蔽路径，整棵子树跳过
strategy:
  prefer_real_media: false
  real_media_bonus: 0.0
  min_score_diff: 8.0                            # 仅展示，实际 is_wash_out 恒 True
  force_dedup: false
  iso_mode: same_dir
  workers: 4
```

**线上实况（实测）**：`scan_paths = ['/vol1/1000/strm/shareStrm']`、`ignore_paths = []`、`iso_mode = same_dir`、`force_dedup = False`。

**评分规则文件**：`RULES_DIR/mediatidy_评分规则_屏蔽ISO.yaml`
（容器内 `/data/rules/`，宿主 `/vol1/1000/docker/mt-console/data/rules/`）

```yaml
quality:
  - name: 本地strm
    id: quality-strm
    version:
      enabled: true
      unmatched: best
      slots:
        - name: ISO
          match: { file_extension: [iso] }
    min_score_diff: 8
    scoring:
      resolution: { weight: 20, unknown_policy: zero, priority: [2160p, 1080p, 1080i, 720p] }
      video_codec: { weight: 10, unknown_policy: zero, priority: [H.265, H.264, AV1, VP9] }
      hdr: { weight: 40, unknown_policy: zero, priority: [Dolby Vision P7, P5, P8, Dolby Vision, HDR10+, HDR10, HLG, HDR, SDR] }
      bitrate: { weight: 25, unknown_policy: zero }
    exclude: { resolutions: [], codecs: [Xvid, DivX], hdr_types: [] }
```

---

## 6. 实现要点 · 别踩的坑

1. **`unmatched` / `slots` 在 `version` 下，而 `QualityScorer` 读顶层** —— 必须过 `config._build_rules_from_entry()` 把 `version.slots` / `version.unmatched` 提到顶层。直接拿 `raw['quality'][0]` 构造 → `unmatched='skip'`、`slots=[]` → `slot_tier()` 全返回 `None` → 所有文件被跳过 → **重复组恒为 0**。这是最容易复现的「扫不出东西」故障。
2. **宿主机上 `RULES_DIR` 解析为 `/app/data/rules`（不存在）** —— `load_scoring_rules()` 在宿主返回空壳 `order: []`，只有在容器内 `/data/rules` 才有效。调试必须在容器内跑。
3. **`is_wash_out` 是硬编码 True** —— 不要以为 `min_score_diff` 在起作用。想看「有区别」需要改代码。
4. **连集包与单集永不互相比** —— 扫出来 0 组往往不是 bug。剧集库里视觉上的「重复」（`宋飞正传 S04E23-E24` vs `S04E23`）就是这种形态，**设计如此**。
5. **`_detect_target` 只在 `iso_mode == "same_dir"` 时读 strm 内容**，其它模式不读，`target_format` 恒为 `standard`、`target_ext` 对 strm 为空。
6. **伴随文件索引的 `-mediainfo` 特例** —— 文件名含 `8-bit` 的 `-` 会让前缀切分错位，必须额外用 `stem + "-mediainfo"` 完整键查一次。
7. **`trash` 模式保留相对 `base_dir` 的结构**，`base_dir` 取 `scan_paths[0]`；跨 base_dir 的文件会拍平到 `trash_dir/文件名`，**可能撞名**。
8. **`clean_washout` 不删孤儿** —— 孤儿走 `/api/orphans/clean`（`clean_orphans`）。
9. **并行进程池会吞异常**：`ProcessPoolExecutor` 分支里 `except Exception: continue`，单个子目录出错**静默跳过**，不会报错。结果会少但不提示。
10. **`_split_scan_paths` 展开粒度** = 顶层子目录数，`workers` 与 `min(workers, 子目录数)` 取小；子目录很少时并行没有收益。

---

## 7. 已知边界

| 情况 | 现状 |
|---|---|
| 跨目录重复（同 tmdb 不同目录） | **不处理**（那是「影视查重」的职责） |
| 连集包 vs 单集重叠 | 视为不同键，**不互相比、不报重复** |
| 不同 `edition`（导演剪辑版等） | 不同键，**不互相比** |
| 不同槽位（ISO vs 普通） | 不同键，**不互相比** |
| 分差小于 `min_score_diff` | **仍然会洗版**（`is_wash_out` 恒 True） |
| 孤儿伴随文件 | 本流程不删 |
| 真实视频文件 | 参与分组，但排序时 `is_strm=False` 有优待 |

---

## 8. 最小复现脚本（宿主机，只读）

```python
import sys, os
sys.path.insert(0, "/vol1/@appshare/fn-deepseek-harness/日常/media-console/server")
from pathlib import Path
from core import config as C
from core.scorer import QualityScorer
from core.scanner import MediaScanner

raw = C._load_yaml(Path("/tmp/rules.yaml"))          # 从容器取出或直接指容器路径
rules = C._build_rules_from_entry(raw["quality"][0]) # ← 关键：必须过这个函数
scorer = QualityScorer(rules)
scanner = MediaScanner(scorer, iso_mode="same_dir", ignore_paths=[])

d = Path("/vol1/1000/strm/云盘影视/电视剧/欧美剧/宋飞正传 (1989) {tmdb-1400}/Season 4")
groups, orphans, total = scanner.scan_directory(d)
print("媒体文件:", total, "重复组:", len(groups))
for g in groups:
    print(g.group_key, "胜者:", g.winner.path.name, "淘汰:", [l.path.name for l in g.losers])
```

预期输出（真实库实测）：`媒体文件: 24 重复组: 0`
—— 因为该季的「重复」全是连集包 vs 单集，键不同不相遇。

---

## 9. 环境速查

| 项 | 值 |
|---|---|
| 引擎入口 | `server/core/engine.py` → `StrmEngine` |
| 扫描器 | `server/core/scanner.py` → `MediaScanner.scan_directory` |
| 评分器 | `server/core/scorer.py` → `QualityScorer` |
| 伴随文件 | `server/core/companion.py` → `CompanionEngine` |
| 执行器 | `server/core/action.py` → `ExecutionAction.execute_cleanup` |
| API | `server/api/strm_api.py`（`/api/scan`、`/api/clean`） |
| 前端 | `frontend/src/views/Dedup.vue` |
| 规则 | 容器内 `/data/rules/mediatidy_评分规则_屏蔽ISO.yaml` |
| 扫描结果缓存 | 容器内 `/data/last_scan.json` |
| 容器 | `mt-console`，`http://127.0.0.1:28002` |
| 部署 | `docker cp` 单文件后 `docker restart mt-console` |
| 宿主机提权 | `echo 'fuck504' \| su hua -c '...' 2>&1 \| sed 's/^Password: *//'` |
| 容器内跑脚本 | `docker exec -w /app/server mt-console python3 -c "import sys; sys.path.insert(0,'/app/server'); ..."` |
