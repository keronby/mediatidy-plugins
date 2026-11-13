"""Pure rules ported from mt-console media dedup; no host I/O.

Source: mt-console-server/core/dedup_state.py + dedup_parse.py.
Keep functions verbatim; tests compare against source AST on shared fixtures.
Explicit style is supplied by plugin adapters; no console config imports.
"""
import re
import logging
from typing import Any, Dict, Optional, Tuple
logger = logging.getLogger(__name__)

TMDB_TAG_RE = re.compile(r"(?<![A-Za-z0-9])tmdb(?:id|_id)?\s*[-=_:\s]\s*(\d+)", re.I)

NAMING_STYLES = {
    "brace": {
        "id": "brace",
        "label": "花括号标准 (推荐默认)",
        "template": "{title} ({year}) {{tmdb-{tmdb_id}}}",
        "example": "茶之味 (2004) {tmdb-2002}",
    },
    "bracket_id": {
        "id": "bracket_id",
        "label": "方括号 tmdbid",
        "template": "{title} ({year}) [tmdbid={tmdb_id}]",
        "example": "茶之味 (2004) [tmdbid=2002]",
    },
    "bracket_dash": {
        "id": "bracket_dash",
        "label": "方括号 tmdb",
        "template": "{title} ({year}) [tmdb-{tmdb_id}]",
        "example": "茶之味 (2004) [tmdb-2002]",
    },
    "dot_suffix": {
        "id": "dot_suffix",
        "label": "点号后缀",
        "template": "{title} ({year}).tmdb-{tmdb_id}",
        "example": "茶之味 (2004).tmdb-2002",
    },
    "brace_noyear": {
        "id": "brace_noyear",
        "label": "无年份花括号",
        "template": "{title} {{tmdb-{tmdb_id}}}",
        "example": "茶之味 {tmdb-2002}",
    },
}

DEFAULT_NAMING_STYLE = "brace"

def build_target_dir_name(title, year, tmdb_id, style_id=None):
    """按所选风格预测目标目录名。无年份风格省略年份；其他风格缺年份返回 None（走保护组）。"""
    title = (title or "").strip()
    if not title or tmdb_id is None:
        return None
    sid = style_id if style_id in NAMING_STYLES else DEFAULT_NAMING_STYLE
    if sid == "brace_noyear":
        return f"{title} {{tmdb-{int(tmdb_id)}}}"
    if year is None:
        return None
    try:
        y = int(year)
    except (TypeError, ValueError):
        return None
    if sid == "brace":
        return f"{title} ({y}) {{tmdb-{int(tmdb_id)}}}"
    if sid == "bracket_id":
        return f"{title} ({y}) [tmdbid={int(tmdb_id)}]"
    if sid == "bracket_dash":
        return f"{title} ({y}) [tmdb-{int(tmdb_id)}]"
    if sid == "dot_suffix":
        return f"{title} ({y}).tmdb-{int(tmdb_id)}"
    return f"{title} ({y}) {{tmdb-{int(tmdb_id)}}}"

_BRACE_RE = re.compile(r"\{tmdb-(\d+)\}\s*$", re.I)

_BRACKET_ID_RE = re.compile(r"\[\s*tmdbid\s*=\s*(\d+)\s*\]\s*$", re.I)

_BRACKET_DASH_RE = re.compile(r"\[\s*tmdb-(\d+)\s*\]\s*$", re.I)

_DOT_RE = re.compile(r"\.tmdb-(\d+)\s*$", re.I)

_YEAR_PAREN_RE = re.compile(r"\(\d{4}\)\s*$")

def detect_dir_style(dir_name):
    n = (dir_name or "").strip()
    if _BRACKET_ID_RE.search(n):
        return "bracket_id"
    if _BRACKET_DASH_RE.search(n):
        return "bracket_dash"
    if _DOT_RE.search(n):
        return "dot_suffix"
    if _BRACE_RE.search(n):
        # 花括号 + 紧邻年份括号 = brace；无年份括号 = brace_noyear
        pre = _BRACE_RE.sub("", n).rstrip()
        return "brace" if _YEAR_PAREN_RE.search(pre) else "brace_noyear"
    return None

YEAR_RE = re.compile(r"(?:[\[(（](\d{4})[\])）]|\b((?:19|20)\d{2})\b)")


def naming_style_of(cfg=None):
    return (cfg or {}).get("naming_style") or DEFAULT_NAMING_STYLE

def parse_media_dir(d_name: str) -> Optional[Dict[str, Any]]:
    """解析影视目录名称，提取纯标题、年份、TMDB ID。

    兼容格式包括但不限于：
      - 方括号标准：忠烈图 (1975) [tmdbid=76293] / 忠烈图 [tmdbid=76293]
      - 花括号标准：忠烈图 (1974) {tmdb-76293} / 忠烈图 {tmdb-76293}
      - 点号分隔：Avatar.The.Way.of.Water.2022.[tmdbid=76600]
      - 各种分隔符变体：[tmdb-xxx], [tmdb_id=xxx], [tmdb:xxx], {tmdbid=xxx}, （tmdbid=xxx）
    """
    m = TMDB_TAG_RE.search(d_name)
    if not m:
        return None
    tmdb_id = int(m.group(1))

    # ── 片名抽取：A/B 两段取，取到第一个非空片段为止 ────────────────────
    #
    #   A 段：tmdb 标记**之前**的文本（最常见）。要削掉两样东西：
    #     1) 紧贴标记的孤立左括号 —— 正则不强求左括号，所以
    #        `忠烈图 [tmdb_id=76293]` 的前缀会是 `忠烈图 [`；
    #     2) 其他工具的标签块 —— `[douban=123][tmdbid=76293] 忠烈图` 的前缀
    #        是 `[douban=123][`，剥掉孤立括号后剩 `[douban=123]`，若不继续剥，
    #        标题会变成 `[douban=123]`，与 TMDB 标题比对必然误判成不一致。
    #
    #   B 段：tmdb 标记**之后**的文本。有些库把标记写在片名前
    #        （`[tmdbid=76293] 忠烈图`、`{tmdb-76293}忠烈图`）。
    #
    # 全部在**年份解析之前**完成 —— 若放到年份解析之后，剥空会撞上
    # 「标题为空则回退前缀」的兜底，把标签原样装回去（这个坑踩过两次）。
    def _strip_leading_tags(text: str) -> str:
        """循环剥掉前导的整块 [xxx]/{xxx} 标签，剥完还剩文本才接受。"""
        cur = text.strip(" .-_")
        while True:
            if cur[:1] in ("[", "{") and ("]" in cur or "}" in cur):
                ends = [x for x in (cur.find("]"), cur.find("}")) if x != -1]
                rest = cur[min(ends) + 1:].strip(" .-_")
                if rest:
                    cur = rest
                    continue
            return cur

    # A 段：标记之前。先逐个削掉紧贴标记的孤立左括号（可能是 `[（` 叠写）
    before = d_name[:m.start()]
    while True:
        trimmed = before.rstrip(" .-_")
        if trimmed and trimmed[-1] in "[{(（":
            before = trimmed[:-1]
            continue
        before = trimmed
        break
    before = _strip_leading_tags(before)

    # B 段：标记之后（剔掉紧跟的右括号与分隔符）
    after = d_name[m.end():]
    after = re.sub(r"^[\s\]})）\-_:=]*", "", after)
    after = _strip_leading_tags(after)

    # A 段剥完只剩「一坨别的工具标签」（如 `[douban=123]`）时，它不是片名，
    # 必须让位给 B 段 —— 否则 `[douban=123][tmdbid=76293] 忠烈图` 的标题
    # 会变成 `[douban=123]`。判据：整段被一个方括号块占满。
    if before and re.fullmatch(r"[\[{][^\[\]{}]*[\]}]", before):
        before = ""

    year = None
    prefix = before or after or d_name
    title = prefix

    # 1. 尝试匹配紧邻末尾的 (1974) 或 [1974]
    tail_year_m = re.search(r"[\s._-]*[\[(（](\d{4})[\])）]\s*$", prefix)
    if tail_year_m:
        year = int(tail_year_m.group(1))
        title = prefix[:tail_year_m.start()].strip(" .-_")
    else:
        # 2. 尝试匹配末尾的点分/连字符/空格年份，如 .1974 或 - 1974
        tail_bare_year = re.search(r"[\s._-]+((?:19|20)\d{2})\s*$", prefix)
        if tail_bare_year:
            year = int(tail_bare_year.group(1))
            title = prefix[:tail_bare_year.start()].strip(" .-_")
        else:
            # 3. 扫描 prefix 中的任意年份（如果有多个取最后一个）
            all_years = list(YEAR_RE.finditer(prefix))
            if all_years:
                last_y = all_years[-1]
                year = int(last_y.group(1) or last_y.group(2))
                if last_y.start() > 1:
                    cand_title = prefix[:last_y.start()].strip(" .-_")
                    if cand_title:
                        title = cand_title

    # 极端兜底：整条目录名只有 tmdb 标签、没有任何片名
    if not title:
        title = prefix or d_name

    # 清洗零宽不可见字符
    title = clean_title_invisible_chars(title)

    style = detect_dir_style(d_name)
    return {
        "title": title,
        "year": year,
        "tmdb_id": tmdb_id,
        "style": style,
    }

def clean_title_invisible_chars(s: str) -> str:
    """清洗零宽不可见字符（如 \\u200b~\\u200f, \\ufeff 等脏字符）。"""
    if not s:
        return ""
    # 过滤零宽空格、零宽连字符、不可见格式控制符
    cleaned = re.sub(r"[\u200b-\u200f\ufeff\u2060\u202a-\u202e]", "", s)
    return cleaned.strip()

def evaluate_dir_match(item: Dict[str, Any], latest_title: str, latest_year: Any, target_style: str = None, expected_category: str = None) -> Tuple[bool, bool, str]:
    """比对目录的片名与年份是否与 TMDB 官方最新数据规范一致。
    返回: (is_matched: bool, need_organize: bool, diff_reason: str)
    """
    clean_local = clean_title_invisible_chars(item.get("local_title") or "")
    clean_latest = clean_title_invisible_chars(latest_title or "")

    # ── 命名风格比对：目标名 = 最新标题+最新年份+tmdb_id 按所选风格拼 ──
    # 2.4.2 拆分显示（用户 m06381）：旧逻辑用「整串目录名 != 目标名」判风格，
    # 标题差一字也会连报「命名风格不符」，把标题错重复计数成风格错。
    # 现行：风格只看模板（detect_dir_style vs target_style），整名差异只在
    # 标题/年份已一致时才视为风格/格式问题；标题已不一致时不再叠加风格条。
    style_issue = ""
    target_name = None
    if target_style is None:
        try:
            target_style = naming_style_of()
        except Exception:
            target_style = "brace"
    tmdb_id = item.get("tmdb_id")
    if tmdb_id is not None and (latest_title or clean_local):
        target_name = build_target_dir_name(
            latest_title or clean_local, latest_year, tmdb_id, target_style)
    # 无年份风格允许缺年份；其他风格缺年份时 target_name=None → 走保护组（扫描阶段已拦截）
    item["target_name"] = target_name
    item["target_style"] = target_style

    title_matched = True
    if clean_latest:
        title_matched = (clean_local == clean_latest)

    # 风格模板比对（仅模板本身错才报；标题错已在 title_matched 计数，不重复报）
    _cur_style = None
    try:
        _cur_style = detect_dir_style(item.get("dir_name") or "")
    except Exception:
        _cur_style = None
    item["detected_style"] = _cur_style
    if _cur_style is not None and _cur_style != target_style:
        style_issue = f"命名风格不符 (当前模板: {_cur_style} ➜ 目标模板: {target_style})"
    elif target_name and item.get("dir_name") and title_matched:
        # 标题已一致、模板也一致，但整名仍不等（多余空格/全半角等格式差异）
        _norm = lambda s: re.sub(r"\s+", " ", (s or "").strip())
        if _norm(item["dir_name"]) != _norm(target_name):
            style_issue = f"目录名格式不符 (当前: {item['dir_name']} ➜ 目标: {target_name})"

    year_matched = True
    year_issue = ""
    item_year = item.get("year")

    if latest_year is not None:
        try:
            ly = int(latest_year)
            if not item_year:
                year_matched = False
                year_issue = f"年份缺失 (TMDB应为 {ly})"
            elif int(item_year) != ly:
                year_matched = False
                year_issue = f"年份不符 (当前为 {item_year}，TMDB应为 {ly})"
        except (ValueError, TypeError):
            pass

    # ── 分类错误评判（用户 m06462：拿来做是否提交MT的重要依据）──
    # expected_category 来自 MT parse-test(full) 按分类规则算出的最终路径，
    # 且已校验返回的 tmdb.id 与本条目 tmdb_id 一致才采信（防标题歧义错分类）。
    # 只有标题/年份/风格都一致时，分类不同才单独算需整理；标题已错时分类信息
    # 只附在理由后面，不重复计数（与 F1 一致）。
    cat_issue = ""
    # m06722: expected 缺失时不直接无标识 —— item.category 即组内实际分类，
    # 用于前端「分类正确/未知」标识；分类正确 = 当前 == 期望。
    _cur_cat = (item.get("category") or "").strip()
    _exp_cat = (expected_category or item.get("expected_category") or "").strip()
    if item.get("expected_category_source") != "mt_parse_test":
        _exp_cat = ""
    if _exp_cat and _cur_cat:
        if _cur_cat != _exp_cat:
            cat_issue = f"分类错误 (当前: {_cur_cat} ➜ 应为: {_exp_cat})"
    item["expected_category"] = _exp_cat or item.get("expected_category") or ""
    # 分类正确标识：期望已知且当前与之相符（混乱组也必须有，否则前端永远看不到 ✓）
    item["has_cat_ok"] = bool(_exp_cat and _cur_cat and _cur_cat == _exp_cat)
    # 混乱保护组分类透传：组级 expected 需传给每个目录，前端才有分类正确/错误徽标
    if _exp_cat:
        item["cat_status"] = "error" if cat_issue else ("ok" if item["has_cat_ok"] else "unknown")
    else:
        item["cat_status"] = "unknown"

    item["has_cat_error"] = bool(cat_issue)
    item["cat_only"] = bool(cat_issue) and title_matched and year_matched and not style_issue
    if not title_matched or not year_matched or style_issue or cat_issue:
        reasons = []
        if not title_matched:
            reasons.append(f"片名不一致 (当前: {item.get('local_title')} ➜ 最新: {latest_title})")
        if not year_matched and year_issue:
            reasons.append(year_issue)
        if style_issue:
            reasons.append(style_issue)
        if cat_issue:
            reasons.append(cat_issue)
        return False, True, " · ".join(reasons)
    else:
        return True, False, ""

def reevaluate_group_dirs(group: Dict[str, Any], latest_title: str, latest_year: Any, target_style: str = None) -> Tuple[bool, int]:
    """对一组重复目录执行片名、年份及分类比对，并返回 (group_has_diff: bool, dirs_to_organize_count: int)。

    组内分类不同只作展示；仅 MT 路径核验能标记分类错误。
    """
    dir_list = group.get("dirs", [])
    group_has_diff = False
    group_link_types = set()
    total_dirs_to_organize = 0

    # 提取组内所有不为空的分类并查重
    distinct_cats = sorted(list(set(item.get("category") for item in dir_list if item.get("category"))))
    has_cat_diff = (len(distinct_cats) > 1)
    group["has_category_difference"] = has_cat_diff
    group["distinct_categories"] = distinct_cats

    for item in dir_list:
        lt = item.get("link_type", "none")
        if lt == "mixed":
            group_link_types.add("http")
            group_link_types.add("cloudnas")
        elif lt in ("http", "cloudnas"):
            group_link_types.add(lt)

        # 标题与年份是否一致判定 (严格核验片名与TMDB规范年份)
        _ts = target_style or group.get("target_style")
        is_matched, need_organize, diff_reason = evaluate_dir_match(item, latest_title, latest_year, _ts, item.get("expected_category"))

        # 组内分类不同仅是现象；仅 MT 路径核验结果能决定谁分类错误。
        item["has_cat_diff"] = has_cat_diff
        item["cat_only_reason"] = item.get("cat_only", False)

        item["is_matched"] = is_matched
        item["need_organize"] = need_organize
        item["diff_reason"] = diff_reason
        item["latest_title"] = latest_title
        item["latest_year"] = latest_year

    # 2.3.11 混合链接新逻辑（用户 m05242）：不再整组禁用。
    #   混合组按 MT 评分规则比对（忽略标题差别，TV 按 S01E01，电影按 tmdbid，
    #   槽位不混淆）；删除后仍 http+cloudnas 并存 -> 混乱 strm 保护组（见 scan 阶段判定）。
    #   这里只标记 mixed_candidate，最终胜败/删除在 mixed_link 阶段执行。
    is_mixed_group = ("http" in group_link_types and "cloudnas" in group_link_types)
    group["is_mixed_candidate"] = bool(is_mixed_group or any(i.get("link_type") == "mixed" for i in dir_list))
    for item in dir_list:
        lt = item.get("link_type", "none")
        if lt == "mixed":
            # 目录内混合：仍需逐文件比对，不在目录级禁用
            item["disabled"] = False
            item["disabled_reason"] = ""
            item["mixed_candidate"] = True
        else:
            item["disabled"] = False
            item["disabled_reason"] = ""
            item["mixed_candidate"] = bool(is_mixed_group)
        # scan 阶段已标 delete_candidate 时：默认 need_organize 重算走下面，
        # 这里保证旧字段不残留（scan 会覆盖写）。
        item.setdefault("delete_candidate", False)

        if item.get("need_organize"):
            group_has_diff = True
            if not item.get("disabled"):
                total_dirs_to_organize += 1

    if "http" in group_link_types and "cloudnas" in group_link_types:
        group["group_link_type"] = "mixed"
        group["group_link_desc"] = "混合链接组(同时含分享与本地)"
    elif "http" in group_link_types:
        group["group_link_type"] = "all_http"
        group["group_link_desc"] = "纯分享链接组(全为http)"
    elif "cloudnas" in group_link_types:
        group["group_link_type"] = "all_cloudnas"
        group["group_link_desc"] = "纯本地链接组(全为cloudnas)"
    else:
        group["group_link_type"] = "other"
        group["group_link_desc"] = "其他/无strm链接"

    group["has_difference"] = group_has_diff
    return group_has_diff, total_dirs_to_organize
