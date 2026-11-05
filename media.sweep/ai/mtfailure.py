"""读取 MT 的整理失败记录，给 AI 提供它自己查不到的上下文。

## 为什么要这个

MT 每放弃一个文件都会在 `organize_failure_records` 里留下 `reason_code` 和
`reason_message`。更关键的是：**我们要识别的文件，很多正是因为 MT 整理失败
才留在未识别目录**。它在 MT 那里已经试过一次，失败原因就在库里。

MT 的失败信息里常常**已经包含候选作品名、TMDB ID、季列表、年份** ——
这些是我们自己重新搜一遍要花配额、还不一定搜得到的东西。

## 实测数据（2026-11 当前库，共 1,929 条）

reason_code 分布：
    title_number_conflict 654   AI 搜索标题遗漏或改变了原始标题数字
    ambiguous_candidates  418   有多个候选无法确定
    year_conflict         243   年份冲突
    episode_missing       225   缺集
    season_mismatch       183   季不匹配
    no_candidates          71   完全没有候选
    ai_response_invalid    54   AI 回答格式错误
    ...

`reason_message` 最后一段的高频形态（归一化后）：

    558  常规识别阶段：搜索已执行，但没有返回作品候选
    409  多个同名候选仍符合原始条件，缺少区分依据
    232  文件年份 X 与 TMDB N 整剧首播年份 Y、第 N 季首播年份 Z 均不符
    208  常规识别阶段：文件名和直属目录均未能对应候选的权威标题或别名
     98  原始标题数字与候选身份冲突
     60  TMDB 已列出的季：SN
     54  匹配到剧集 佩内洛普 (tmdbid=N) 但无法识别集数

## 最有价值的三类（都在本模块的解析里）

**A. 已认出作品、只差集数**（225 条）
    匹配到剧集 埃罗芒阿老师 (tmdbid=70878) 但无法识别集数
  → 提取 `tmdb_id=70878` + `title=埃罗芒阿老师`，AI 只要回答「第几集」。
    这等于把 80% 的答案直接给了 AI。

**B. 候选带季列表**（83 条）
    候选《百花缭乱》（剧集，TMDB 46440）：TMDB 季列表未列出第 3 季…；
    TMDB 已列出的季：S00、S01、S02
  → 提取 `tmdb_id=46440` + `title=百花缭乱` + `seasons=[0,1,2]`。
    这类文件我们的 verify() 也能挡（季不存在），但提前告诉 AI 能让它
    别浪费配额去猜一个不存在的季。

**C. 候选带年份**（232 条）
    文件年份 2026 与 TMDB 235643 整剧首播年份 2023、第 1 季首播年份 2023 均不符
  → 提取 `tmdb_id=235643` + `first_year=2023` + `season_year=2023`。

## 重要修正：不要去找「权威标题列表」

早期版本的文档说能抽到形如
`与候选 tv:336241 的权威标题 ["失控的排灯节" ...] 未建立对应关系` 的列表。
**当前 MT 0.1.13 不产生这种格式。** 实测 208 条含「权威标题」字样的记录里，
那句话实际是：

    常规识别阶段：文件名和直属目录均未能对应候选的权威标题或别名

「权威标题」只是失败说明里的一个词，后面没有列表。所以本模块不再做引号抽取
（`RE_QUOTED` 保留仅用于兜底，实战中几乎不命中）。

## 设计约束

1. **只读、不加锁** —— `mode=ro&immutable=0` 打开，任何错误都静默返回 None。
   绝不让查失败影响识别主流程（MT 可能正在写这个库）。
2. **按 `current_path` 精确匹配** —— 唯一可靠的关联键。
   同名不同目录的文件很多，模糊匹配会张冠李戴。
3. **只取最近一条** —— 同一文件可能失败多次（重试），最新的才有意义。
4. **不把 reason_message 全文塞进 prompt** —— 有时几百字，会挤掉正经内容。
   只把抽取出的结构化事实交给 AI，见 `failure_hint()`。
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any

# ── 结构化抽取的正则 ───────────────────────────────────────────────

# A. 匹配到剧集 埃罗芒阿老师 (tmdbid=70878) 但无法识别集数
RE_MATCHED_SERIES = re.compile(
    r"匹配到剧集\s*(?P<title>[^()（]{1,80}?)\s*[（(]\s*tmdbid\s*=\s*(?P<id>\d+)\s*[)）]",
    re.I,
)

# B. 候选《百花缭乱》（剧集，TMDB 46440）
RE_CANDIDATE = re.compile(
    r"候选\s*《(?P<title>[^》]{1,80})》\s*[（(]\s*(?P<kind>剧集|电影)\s*[，,]\s*TMDB\s*(?P<id>\d+)\s*[)）]",
    re.I,
)

# B2. TMDB 已列出的季：S00、S01、S02
RE_SEASON_LIST = re.compile(r"TMDB\s*已列出的季\s*[:：]\s*(?P<list>[S\d、,，\s]+)", re.I)
RE_SEASON_ITEM = re.compile(r"S(?P<n>\d{1,3})", re.I)

# C. 文件年份 2026 与 TMDB 235643 整剧首播年份 2023、第 1 季首播年份 2023 均不符
RE_YEAR_CONFLICT = re.compile(
    r"TMDB\s*(?P<id>\d+)\s*整剧首播年份\s*(?P<first>\d{4})"
    r"(?:[、,，]\s*第\s*(?P<season>\d{1,3})\s*季首播年份\s*(?P<season_year>\d{4}))?",
    re.I,
)

# 兜底：形如 tv:336241 的裸 ID（本版 MT 少用，留作兼容）
RE_CANDIDATE_ID = re.compile(r"\b(?:tv|movie)[:\s]+(\d{1,8})", re.I)
RE_QUOTED = re.compile(r"[\"“]([^\"”]{1,120})[\"”]")

# 难度信号
RE_QUERY_COUNT = re.compile(r"常规\s*TMDB\s*查询\s*(\d+)\s*次", re.I)
RE_AI_QUERY_COUNT = re.compile(r"AI\s*新增查询\s*(\d+)\s*次", re.I)

# ── 语义分组 ───────────────────────────────────────────────────────

# MT 连候选都没找到 —— 这类我们再问 AI 意义不大，要让它更保守。
HOPELESS_CODES = frozenset({"no_candidates", "no_new_search_clues"})

REASON_LABELS = {
    "title_conflict": "标题冲突",
    "title_number_conflict": "标题数字冲突",
    "ambiguous_candidates": "同名候选无法裁决",
    "year_conflict": "年份冲突",
    "season_mismatch": "季不存在",
    "episode_missing": "缺集",
    "no_candidates": "搜索无候选",
    "no_new_search_clues": "没有新检索线索",
    "ai_response_invalid": "AI 回答格式错误",
    "candidate_details_incomplete": "候选信息不全",
    "candidate_recall_incomplete": "候选召回不全",
    "tmdb_api_error": "TMDB 接口错误",
    "input_evidence_unresolved": "输入依据无法解析",
}


def lookup(db_path: str, file_path: str) -> dict[str, Any] | None:
    """按文件当前路径查 MT 的失败记录。查不到返回 None。

    任何异常（库不存在、被锁、表不存在）都静默返回 None ——
    这是辅助信息，不能因为查不到就让整批识别失败。
    """
    if not db_path or not file_path:
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=0", uri=True, timeout=2.0)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute(
            "SELECT reason_code, reason_label, reason_message, created_at "
            "FROM organize_failure_records WHERE current_path = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (file_path,),
        ).fetchone()
    except sqlite3.Error:
        # 表不存在（老版本 MT）或缺列，都不影响主流程
        return None
    finally:
        conn.close()
    if not row:
        return None
    code, label, message, created = row
    return {
        "reason_code": code or "",
        "reason_label": label or REASON_LABELS.get(code or "", ""),
        "reason_message": message or "",
        "created_at": created or "",
    }


def parse_facts(message: str) -> dict[str, Any]:
    """从 reason_message 里抽出所有结构化的客观事实。

    返回的字典只包含真正抽到的键：
      tmdb_id      候选 TMDB ID
      title        候选作品名
      media_type   "tv" / "movie"（抽不到为 None）
      seasons      季号列表（如 [0, 1, 2]）
      first_year   整剧首播年
      season_year  该季首播年
      query_count / ai_query_count  难度信号
    """
    facts: dict[str, Any] = {}
    if not isinstance(message, str) or not message:
        return facts

    # A：已认出作品、只差集数
    match = RE_MATCHED_SERIES.search(message)
    if match:
        facts["tmdb_id"] = int(match["id"])
        facts["title"] = match["title"].strip()
        facts["media_type"] = "tv"

    # B：候选《名》（剧集，TMDB 123）
    if "tmdb_id" not in facts:
        match = RE_CANDIDATE.search(message)
        if match:
            facts["tmdb_id"] = int(match["id"])
            facts["title"] = match["title"].strip()
            facts["media_type"] = "tv" if match["kind"] == "剧集" else "movie"

    # C：年份冲突里带的 ID
    if "tmdb_id" not in facts:
        match = RE_YEAR_CONFLICT.search(message)
        if match:
            facts["tmdb_id"] = int(match["id"])
            facts["first_year"] = int(match["first"])
            if match["season_year"]:
                facts["season_year"] = int(match["season_year"])

    # 兜底：裸 ID
    if "tmdb_id" not in facts:
        match = RE_CANDIDATE_ID.search(message)
        if match:
            facts["tmdb_id"] = int(match.group(1))

    # 季列表
    match = RE_SEASON_LIST.search(message)
    if match:
        seasons = sorted({int(m["n"]) for m in RE_SEASON_ITEM.finditer(match["list"])})
        if seasons:
            facts["seasons"] = seasons

    # 难度信号
    normal = RE_QUERY_COUNT.search(message)
    if normal:
        facts["query_count"] = int(normal.group(1))
    ai_new = RE_AI_QUERY_COUNT.search(message)
    if ai_new:
        facts["ai_query_count"] = int(ai_new.group(1))

    return facts


def direct_reason(message: str) -> str:
    """抽出最能说明问题的那一句（分号分段的最后一段）。"""
    if not isinstance(message, str) or not message:
        return ""
    parts = [p.strip() for p in message.replace("；", ";").split(";") if p.strip()]
    return parts[-1][:200] if parts else ""


def failure_hint(record: dict[str, Any] | None) -> str:
    """把失败记录转成给 AI 的上下文文本。

    返回空串表示没有可用信息（调用方据此决定是否注入）。
    只输出**抽到的客观事实**，绝不编造「候选标题列表」这类本版 MT 不产生的东西。
    """
    if not record:
        return ""

    code = record.get("reason_code") or ""
    label = record.get("reason_label") or REASON_LABELS.get(code, code)
    message = record.get("reason_message") or ""
    facts = parse_facts(message)

    lines: list[str] = [f"这个文件 MT 之前整理失败过，原因是「{label}」。"]

    # ── 最有价值：MT 已经认出作品，只差集数 ──────────────────────
    if facts.get("tmdb_id") and facts.get("title"):
        title = facts["title"]
        if facts.get("media_type") == "movie":
            lines.append(f"MT 当时已经匹配到电影《{title}》（TMDB {facts['tmdb_id']}）。")
        else:
            lines.append(f"MT 当时已经匹配到剧集《{title}》（TMDB {facts['tmdb_id']}）。")
            if facts.get("seasons"):
                shown = "、".join(f"S{n:02d}" for n in facts["seasons"])
                lines.append(f"该剧在 TMDB 上只有这些季：{shown}。")
            if "episode_missing" in code or "无法识别集数" in message:
                lines.append("它只是无法确定是第几季第几集。请重点判断季集号，"
                             "如果文件名里看不出来，请如实标注低置信度。")
    elif facts.get("tmdb_id"):
        lines.append(f"MT 当时提到过 TMDB {facts['tmdb_id']} 这个候选。")
    elif facts.get("seasons"):
        shown = "、".join(f"S{n:02d}" for n in facts["seasons"])
        lines.append(f"MT 当时查到该剧在 TMDB 上只有这些季：{shown}。")

    # ── 年份信息 ────────────────────────────────────────────────
    if facts.get("first_year"):
        text = f"TMDB 记录的整剧首播年是 {facts['first_year']}"
        if facts.get("season_year"):
            text += f"，该季首播年是 {facts['season_year']}"
        lines.append(text + "。")

    # ── 难度信号 ────────────────────────────────────────────────
    if facts.get("query_count") is not None:
        detail = f"MT 当时做过 {facts['query_count']} 次 TMDB 查询"
        if facts.get("ai_query_count") is not None:
            detail += f"，其中 AI 补搜 {facts['ai_query_count']} 次"
        lines.append(detail + "。")

    # ── 直接原因（抽不出结构化事实时才展示，避免重复）──────────
    if not facts:
        reason = direct_reason(message)
        if reason:
            lines.append(f"MT 的记录写道：{reason}")

    # ── 明确告诉 AI 怎么处理 ────────────────────────────────────
    if code in HOPELESS_CODES:
        lines.append("MT 连候选都没找到。如果你也只能靠猜，请如实标注低置信度，不要硬认。")
    elif facts.get("tmdb_id"):
        lines.append("请基于上面的候选来判断，不要另找别的作品；确认不了就标注低置信度。")
    else:
        lines.append("请以文件名和目录里的实际信息为准重新判断；确认不了就标注低置信度。")

    return "\n".join(lines)


def lookup_hint(db_path: str, file_path: str) -> str:
    """一步到位：查记录并转成提示文本。查不到返回空串。"""
    return failure_hint(lookup(db_path, file_path))


def lookup_facts(db_path: str, file_path: str) -> dict[str, Any]:
    """只取结构化事实（给需要程序化利用的场景，比如直接采纳 MT 的 TMDB ID）。"""
    record = lookup(db_path, file_path)
    if not record:
        return {}
    return parse_facts(record.get("reason_message") or "")
