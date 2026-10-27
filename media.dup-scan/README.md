# STRM 重复扫描（media.dup-scan）

MediaTidy 插件。在一个 tmdb 作品目录内部找出重复的 strm，按 **MT 自己的评分规则**
判断哪个版本该保留，**只报告，不删除任何文件**。

- 换对话接手请先读 [docs/交接说明.md](docs/交接说明.md)
- 官方开发文档见 [../../MediaTidy插件开发官方文档.md](../../MediaTidy插件开发官方文档.md)

## 快速上手

```bash
python3 tools/test_core.py     # 离线回归
bash tools/build.sh            # 打包
python3 tools/deploy.py        # 装进 MT 并启用
bash tools/logs.sh 20          # 看日志
```

## 设计约束

1. 评分规则**运行时从 MT 取**（`rules.effective` → `selection.qualityRuleId` → `rules.get`），
   插件内不写死任何 ID。拿不到规则就中止，绝不用内置默认值硬算。
2. 只在**同一作品目录内**比较；跨目录重复、连集包与单集重叠都不处理。
3. 只读。manifest 没有 `files.delete` / `files.write` / `strm.run`。
