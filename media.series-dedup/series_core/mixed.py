"""Console mixed-link bucket decisions; planning only, never delete here.

The scorer/parser/models are copied unchanged from mt-console. The caller must
supply an explicitly selected MT rule, not a guessed/default scoring dictionary.
Local STRM target content is classification/extension evidence ONLY. It never
becomes a submit/delete path; map the work directory with configured prefixes.
"""
import re
from typing import Optional
from pathlib import Path
EP_RE = re.compile(r"S(\d{1,2})E(\d{1,3})", re.I)

def episode_key_from_name(name: str) -> Optional[str]:
    m = EP_RE.search(name or "")
    return f"S{int(m.group(1)):02d}E{int(m.group(2)):02d}" if m else None

def slot_label(slot):
    return {"unmatched": "公共槽", "unmatched-skip": "未命中槽位（跳过）"}.get(slot, slot)

def _target_from_line(line):
    ep=Path(line).suffix.lower().lstrip(".")
    if not ep and "." in line:
        ep=line.rsplit(".",1)[-1].lower().strip().split("/")[0].split("?")[0]
    return ep

def score_group(group, rule, entries):
    """对混合组做 MT 评分比对，返回 winners/losers/chaos/skipped 结构。

    口径：winners/losers 为**文件数**；group.winner_dir_count/loser_dir_count
    为**目录数**（dedup_scan 按 dir_path 去重计），两者并存不可混读。
    目录可既有胜又有败（is_winner_dir=bool(wrat)），败者项在同目录内删。
    - 按 (episode[S01E01], slot) 分桶；电影整组为一个桶 (episode=None)。
    - 槽内用 compare_and_decide（组内 disabled_dims + 平分 http 优先 + 无分差门槛）。
    - unmatched=skip 条目标 skipped，不参与胜败。
    - 只有桶内 >=2 个可比条目才产生 loser。
    """
    from .quality.scorer import QualityScorer
    media_type = group.get("media_type") or "movie"
    if not rule:
        raise ValueError("未加载有效的已选评分规则，不使用默认空规则判胜败")
    rules = {}
    scorer = QualityScorer(rule, force_dedup=True)
    # slot 分档
    for e in entries:
        try:
            tier=scorer.slot_tier(e["file"])
        except Exception as exc:
            raise ValueError("槽位判定失败，停止混合评分: %s" % exc) from exc
        e["slot"]=tier or "unmatched-skip"
        e["skipped"]=tier is None
    buckets={}
    for e in entries:
        if e.get("skipped"): continue
        ak=e.get("episode") if media_type=="tv" else None
        if media_type=="tv" and not ak:
            e["skipped"] = True
            e["skip_reason"] = "缺少明确季集编号，不能与其他未知集比较"
            continue
        buckets.setdefault((ak, e.get("slot")), []).append(e)
    winners=[]; losers=[]; buckets_out=[]
    for (ak,slot), lst in sorted(buckets.items(), key=lambda kv: str(kv[0])):
        if len(lst)<2 or len({str(Path(e["dir_path"]).resolve()) for e in lst}) < 2:
            # 单例桶无对手：仍需算分（前端 tooltip/展示读 score，未算则为 None 空分）
            for e in lst:
                e["winner"]=True
                try:
                    scorer.calculate_score(e["file"])
                except Exception as _ex:
                    raise ValueError("单例评分失败，停止混合评分: %s" % _ex) from _ex
                e["score"]=e["file"].score
                e["score_details"]={k:v for k,v in (e["file"].score_details or {}).items()}
            winners.extend(lst); continue
        files=[x["file"] for x in lst]
        try:
            winner, llist, diff, _ = scorer.compare_and_decide(files)
        except Exception as ex:
            raise ValueError("桶内评分失败，停止混合评分: %s" % ex) from ex
        for x in lst:
            x["score"]=x["file"].score
            x["score_details"]={k:v for k,v in (x["file"].score_details or {}).items()}
        winners.append(next(x for x in lst if x["file"] is winner))
        lset={id(f) for f in llist}
        for x in lst:
            if id(x["file"]) in lset:
                x["winner"]=False; x["score_diff_to_winner"]=round(winner.score-x["file"].score,2)
                losers.append(x)
            else:
                x["winner"]=True
        buckets_out.append({"episode":ak,"slot":slot,"winner_score":winner.score,
                            "loser_count":len(llist),"score_diff":diff})
    # 混乱保护：剩余可比条目仍 http+cloudnas 并存
    # Skipped files remain on disk: include them exactly as mt-console does.
    rem={e.get("link_kind") for e in entries if e.get("skipped") or e.get("winner")}
    chaos=("http" in rem and "cloudnas" in rem)
    return {"entries":entries,"winners":winners,"losers":losers,
            "chaos":chaos,"buckets":buckets_out,
            "skipped": [e for e in entries if e.get("skipped")],
            "remaining_link_types":sorted(rem),
            "rule_id":(rule.get("entry_id") or rules.get("selected_id") or ""),
            "rule_name":(rule.get("entry_name") or "")}


def collect_entries(dir_path, media_type_hint=""):
    """Collect local index files; source addresses remain untouched."""
    import os
    from .quality.parser import MediaParser
    from .quality.models import MediaFile
    parser = MediaParser()
    entries = []
    def fail(exc):
        raise exc
    for root, dirs, files in os.walk(dir_path, followlinks=False, onerror=fail):
        dirs[:] = sorted(d for d in dirs if not Path(root, d).is_symlink())
        for name in sorted(files):
            if not name.lower().endswith('.strm'):
                continue
            path = Path(root, name)
            if path.is_symlink():
                raise ValueError(f"拒绝读取符号链接：{path}")
            with path.open(encoding='utf-8', errors='ignore') as stream:
                line = stream.readline().strip()
            kind = 'http' if line.startswith(('http://', 'https://')) else 'cloudnas' if line else 'other'
            meta = parser.parse(path)
            episode = episode_key_from_name(path.stem)
            if not episode and meta.episode is not None:
                episode = f"S{int(meta.season if meta.season is not None else 1):02d}E{int(meta.episode):02d}"
            ext = _target_from_line(line)
            meta.target_ext = ext if ext and ext != 'strm' else 'strm'
            meta.target_format = 'iso' if meta.target_ext == 'iso' else 'standard'
            meta.media_type = media_type_hint or meta.media_type
            file = MediaFile(path=path, is_strm=True, size_bytes=0, mtime=0, meta=meta, link_kind=kind)
            entries.append(dict(strm_path=str(path), file=file, episode=episode, link_kind=kind,
                                name=name, dir_path=dir_path, dir_name=Path(dir_path).name))
    return entries


def distinct_work_dirs(group):
    import os
    return {os.path.normpath(os.path.abspath(d["dir_path"])) for d in group.get("dirs", [])}

def plan_group(group, rule):
    """Return a serializable review plan. Does not authorize any mutation."""
    if not rule:
        raise ValueError('必须显式选择有效 MT 评分规则')
    if len(distinct_work_dirs(group)) < 2:
        raise ValueError("媒体查重仅处理不同作品目录的相同TMDB ID；同目录请使用STRM查重")
    entries = []
    seen = set()
    for directory in group.get('dirs', []):
        import os
        identity = os.path.normpath(os.path.abspath(directory["dir_path"]))
        if identity in seen:
            continue
        seen.add(identity)
        entries.extend(collect_entries(directory['dir_path'], group.get('media_type', 'movie')))
    if any(entry['link_kind'] == 'other' for entry in entries):
        raise ValueError('存在空/未知 STRM，停止评分以免将未分类文件标为可删除')
    result = score_group(group, rule, entries)
    for entry in result['entries']:
        entry.pop('file', None)
    result['planning_only'] = True
    return result
