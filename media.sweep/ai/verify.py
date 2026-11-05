"""客观核验：用文件名里的事实否决 TMDB 候选。

设计原则全部来自 mt-console-AI识别流程.md 的 verify() 判据表：

1. **发现矛盾就否决** —— 只做减法，不做加法。verify() 通过不代表"认对了"，
   只代表"没找到反证"。正向证据由调用方（AI 检索词 + TMDB 候选）负责。
2. **拿不到事实就放行** —— 文件名没有年份、TMDB 没有季列表时一律不否决，
   绝不制造假否决。宁可漏挡，不可错杀。
3. **只读文件名与 TMDB 返回，不调 AI、不联网** —— 纯函数，可单测。

为什么不能用 AI 自报的年份做判据：AI 对年份的记忆最不可靠（文档三条铁律之一），
而文件名里的年份是发布方写死的客观事实。所以本模块只解析文件名。
"""
from __future__ import annotations

import re
from typing import Any

# 文件名里的年份：必须带括号或明确分隔，避免把 1080p / 264 / 5.1 里的数字当年份。
# 允许 (2026) [2026] .2026. -2026-  _2026_ 和行尾的 2026
RE_NAME_YEAR = re.compile(
    r"(?:^|[^\d])"
    r"(?P<year>(?:19|20)\d{2})"
    r"(?=$|[^\d])"
)

# 季集标记：S01E02 / s1e2 / S01.E02 / 第1季第2集 / 第一季第二集
RE_SEASON_EPISODE = re.compile(
    r"(?:^|[ ._\-\[(])S(?P<season>\d{1,3})[ ._\-]*E(?:P)?(?P<episode>\d{1,4})"
    r"(?=$|[ ._\-\])]|E\d)",  # 允许紧跟 E02（多集 S01E01E02）
    re.I,
)
RE_SEASON_ONLY = re.compile(
    r"(?:^|[ ._\-\[(])S(?P<season>\d{1,3})(?=$|[ ._\-\])])",
    re.I,
)
# 只有集号没有季号：EP02 / E02 / 第02集
RE_EPISODE_ONLY = re.compile(
    r"(?:^|[ ._\-\[(])(?:E(?:P)?(?P<episode1>\d{1,4})|第(?P<episode2>\d{1,4})集)(?=$|[ ._\-\])])",
    re.I,
)

# 多集文件：S01E01E02 / S01E01-E02 / S01E01~02，判定时取最大集号。
RE_EPISODE_RANGE = re.compile(
    r"E(?P<first>\d{1,4})[ ._\-]*(?:[-~]|E)(?P<last>\d{1,4})", re.I
)

# 中文数字季号
CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
RE_CN_SEASON = re.compile(r"第(?P<cn>[一二三四五六七八九十]{1,3})季")


def _cn_number(text: str) -> int | None:
    """把「十二」「二十」这类中文数字转成整数，只处理 1-99。"""
    if not text:
        return None
    if text == "十":
        return 10
    if "十" in text:
        head, _, tail = text.partition("十")
        tens = CN_DIGITS.get(head, 1) if head else 1
        ones = CN_DIGITS.get(tail, 0) if tail else 0
        return tens * 10 + ones
    if len(text) == 1:
        return CN_DIGITS.get(text)
    return None


def parse_name_facts(name: str) -> dict[str, Any]:
    """从文件名提取客观事实。返回的字典里只有真正找到的键。

    - year:    文件名里的年份（发布年，可能与首映年差 1 年）
    - season:  季号（找不到为 None）
    - episode: 集号，多集文件取最大集号
    """
    if not isinstance(name, str):
        return {}
    # 去掉扩展名，避免 mkv/mp4 里的数字干扰
    stem = name.rsplit(".", 1)[0] if "." in name else name
    facts: dict[str, Any] = {}

    year = _pick_year(stem)
    if year is not None:
        facts["year"] = year

    season, episode = _pick_season_episode(stem)
    if season is not None:
        facts["season"] = season
    if episode is not None:
        facts["episode"] = episode
    return facts


def _pick_year(stem: str) -> int | None:
    """取文件名里最像"发布年"的那个年份。

    规则：优先取括号里的年份；否则取最后一个 1900-2099 之间的数字串。
    取"最后一个"是因为发布年通常写在标题之后、技术标签之前或之后，
    而标题里的数字（海上钢琴师1900）往往出现在更靠前的位置。
    """
    bracketed = re.findall(r"[\(\[]((?:19|20)\d{2})[\)\]]", stem)
    if bracketed:
        # 括号里有多个时取最后一个（发布年写法更靠后）
        return int(bracketed[-1])
    matches = [m.group("year") for m in RE_NAME_YEAR.finditer(stem)]
    if not matches:
        return None
    return int(matches[-1])


def _pick_season_episode(stem: str) -> tuple[int | None, int | None]:
    season: int | None = None
    episode: int | None = None

    match = RE_SEASON_EPISODE.search(stem)
    if match:
        season = int(match["season"])
        episode = int(match["episode"])
        # 多集文件：S01E01E02 / S01E01-E02，取最大集号
        tail = stem[match.start():]
        spans = RE_EPISODE_RANGE.finditer(tail)
        for span in spans:
            try:
                episode = max(episode, int(span["last"]))
            except (TypeError, ValueError):
                pass
        return season, episode

    if not season:
        cn = RE_CN_SEASON.search(stem)
        if cn:
            season = _cn_number(cn["cn"])
    if not season:
        only = RE_SEASON_ONLY.search(stem)
        if only:
            season = int(only["season"])

    ep_match = RE_EPISODE_ONLY.search(stem)
    if ep_match:
        raw = ep_match["episode1"] or ep_match["episode2"]
        try:
            episode = int(raw)
        except (TypeError, ValueError):
            episode = None
    return season, episode


def _media_seasons(media: dict) -> list[dict]:
    """从 tmdb.detail 的返回里尽量挖出季列表。

    不同版本的 MT 返回结构不同，这里只做宽松提取：
    支持 media["seasons"] = [{season_number, episode_count, air_date}]。
    """
    seasons = media.get("seasons")
    if isinstance(seasons, list):
        return [s for s in seasons if isinstance(s, dict)]
    return []


def _season_entries(media: dict) -> dict[int, dict]:
    """把季列表整理成 {季号: 该季信息}。"""
    out: dict[int, dict] = {}
    for season in _media_seasons(media):
        number = season.get("season_number", season.get("seasonNumber"))
        if isinstance(number, int):
            out[number] = season
    return out


def _season_year(season: dict) -> int | None:
    for key in ("air_date", "airDate"):
        value = season.get(key)
        if isinstance(value, str) and len(value) >= 4 and value[:4].isdigit():
            return int(value[:4])
    return None


def verify(identity: dict, media: dict, name: str) -> tuple[bool, list[str]]:
    """用文件名事实否决 TMDB 候选。

    返回 (是否通过, 否决理由列表)。理由列表为空即通过。
    任何一条判据命中矛盾都会否决 —— 这是有意的保守：整理错作品的代价
    远高于放弃一个文件。放弃的文件留在原地等人工看，不会丢。
    """
    facts = parse_name_facts(name)
    reasons: list[str] = []

    # ── 判据 1：年份 ────────────────────────────────────────────────
    reason = _check_year(facts, identity, media)
    if reason:
        reasons.append(reason)

    # ── 判据 2 & 3：季存在性 + 集号越界 ───────────────────────────
    # 用 TMDB 返回的类型判断，而不是 AI 的 identity —— identity 可能为空
    # （比如只做核对不提交的场景），判断依据应当来自客观数据源。
    media_type = media.get("mediaType") or identity.get("mediaType")
    if media_type == "tv":
        reasons.extend(_check_season_episode(facts, media))
    elif media_type == "movie":
        # 电影不该带季集号。AI 偶尔把电影填成 S01E01，
        # 提交给 MT 会走错整理分支（电影方案没有季集槽位）。
        if identity.get("season") is not None or identity.get("episode") is not None:
            reasons.append(
                f"TMDB 判定为电影，但 AI 给了季集号 "
                f"S{identity.get('season')}E{identity.get('episode')}"
            )

    return (not reasons), reasons


def _check_year(facts: dict, identity: dict, media: dict) -> str:
    """年份核对。只在两边都有年份时比较，容差 1 年。

    为什么容差 1 年：文件名里的年份通常是发布/压制年，而 TMDB 记的是
    首映/首播年，跨年上映的片子常差 1 年。差 2 年以上才是真矛盾。
    """
    name_year = facts.get("year")
    media_year = media.get("year")
    if not isinstance(media_year, int):
        try:
            media_year = int(str(media_year).strip())
        except (TypeError, ValueError):
            return ""  # TMDB 没年份，无从核验
    if name_year is None:
        return ""       # 文件名没年份，无从核验
    if abs(name_year - media_year) <= 1:
        return ""

    # 中文续作：剧名Ⅱ (2026) 里的 2026 是第 2 季播出年，
    # 而 TMDB 的 year 字段记的是第 1 季首播年。若该季自己 air_date 年份吻合就放行。
    season_number = facts.get("season")
    if season_number is not None:
        season = _season_entries(media).get(season_number)
        if season is not None:
            season_year = _season_year(season)
            if season_year is not None and abs(name_year - season_year) <= 1:
                return ""

    return (f"文件名年份 {name_year}，TMDB 首映/首播年份 {media_year}，"
            f"相差 {abs(name_year - media_year)} 年（容差 1 年）")


def _check_season_episode(facts: dict, media: dict) -> list[str]:
    """季存在性 + 集号越界。只在能拿到季列表时才判。"""
    reasons: list[str] = []
    seasons = _season_entries(media)
    if not seasons:
        return reasons  # TMDB 没给季列表，无从核验

    season_number = facts.get("season")
    episode = facts.get("episode")

    # 文件名带 Sxx 但 TMDB 没有这一季 → 硬否决。
    # 例外：Season 0 是 TMDB 的"特别篇"约定，很多剧有 S00 但 detail 里不列，
    # 这时不否决（宁可漏挡）。
    if season_number is not None and season_number != 0:
        if season_number not in seasons:
            available = "、".join(str(n) for n in sorted(seasons)) or "无"
            reasons.append(f"文件名是第 {season_number} 季，但 TMDB 只有第 {available} 季")
            return reasons  # 季都没有，再比集号没意义

    if season_number is not None and episode is not None:
        season = seasons.get(season_number)
        if season is not None:
            count = season.get("episode_count", season.get("episodeCount"))
            if isinstance(count, int) and count > 0 and episode > count:
                reasons.append(
                    f"文件名是 S{season_number:02d}E{episode:02d}，"
                    f"但 TMDB 第 {season_number} 季只有 {count} 集"
                )
    return reasons
