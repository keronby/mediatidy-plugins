#!/usr/bin/env python3
"""核心层离线回归（不连 MT、不连宿主，纯本地）。

覆盖：文件名解析 nameinfo / MT 规则桥接 rules / 打分 scoring / 作品目录判胜负 workscan。
用真实 MT 的 quality-strm 规则数据做断言，保证分数与 MT 一致。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core import nameinfo, rules as R, scoring, workscan

FAIL = []
def ck(cond, msg):
    if not cond:
        FAIL.append(msg)
        print("  ❌", msg)

# --- 真实 MT 规则（GET /api/organize/editor-options → qualityRules[quality-strm]）---
MT_RULE = {
    "id": "quality-strm", "name": "本地strm",
    "scoring": {
        "resolution": {"enabled": True, "weight": 20, "unknownPolicy": "zero",
                       "priority": ["2160p", "1080p", "1080i", "720p"]},
        "videoCodec": {"enabled": True, "weight": 10, "unknownPolicy": "zero",
                       "priority": ["H.265", "H.264", "AV1", "VP9"]},
        "hdr": {"enabled": True, "weight": 40, "unknownPolicy": "zero",
                "priority": ["Dolby Vision P7", "Dolby Vision P5", "Dolby Vision P8",
                             "Dolby Vision", "HDR10+", "HDR10", "HLG", "HDR", "SDR"]},
        "bitrate": {"enabled": True, "weight": 25, "unknownPolicy": "zero", "priority": None},
        "audioCodec": {"enabled": False, "weight": 0},
    },
    "exclude": {"resolutions": [], "codecs": ["Xvid", "DivX"], "hdrTypes": []},
    "version": {"enabled": True, "slots": [{"name": "ISO", "match": {"file_extension": ["iso"]}}],
                "unmatched": "best"},
}

print("== nameinfo ==")
p = nameinfo.parse("孤胆特工.2010.2160p.Remux.Dolby Vision.10-bit.24fps.66.0Mbps.H265.TrueHD 7.1.strm")
ck(p["resolution"] == "2160p", "分辨率应 2160p，得 %s" % p["resolution"])
ck(p["hdr"] == "Dolby Vision", "HDR 应 Dolby Vision，得 %s" % p["hdr"])
ck(p["videoCodec"] == "H.265", "编码应 H.265，得 %s" % p["videoCodec"])
# bitrate 单位是 Mbps（66.0），不是 kbps
ck(abs((p["bitrate"] or 0) - 66.0) < 0.5, "码率应 66.0Mbps，得 %s" % p["bitrate"])
ck(p["audioChannels"] == "7.1", "声道应 7.1，得 %s" % p["audioChannels"])

print("== rules 桥接 ==")
rules, unsup, disabled = R.to_scorer_rules(MT_RULE)
ck(rules["min_score_diff"] == 0.0, "无 minScoreDiff 时门槛应遵从 MT 规则 0.0，得 %s" % rules["min_score_diff"])
ck("video_codec" in rules["scoring"], "videoCodec 应被桥接成 video_codec")
ck(rules["scoring"]["video_codec"]["weight"] == 10, "video_codec 权重应 10")
ck("audio_codec" not in rules["scoring"], "enabled=false 的维度不应进计分")
ck("audio_codec" in disabled, "enabled=false 的维度应进 disabled 名单")
ck(rules["slots"] and rules["slots"][0]["name"] == "ISO", "ISO 槽位应保留")

print("== scoring (10倍放大模式) ==")
total_weight = sum(float((v or {}).get("weight") or 0) for v in rules["scoring"].values())
ck(total_weight == 95.0, "权重合计应 95（20+10+40+25），得 %s" % total_weight)
max_s = scoring.max_score(rules)
ck(max_s == 950.0, "理论满分（10倍放大）应为 950，得 %s" % max_s)

# Wiki 官方测试样例验证：priority: [2160p, 1080p, 720p], weight: 20
wiki_test_rule = {
    "scoring": {
        "resolution": {"weight": 20.0, "priority": ["2160p", "1080p", "720p"], "unknown_policy": "zero"}
    }
}
s_2160, _ = scoring.score({"resolution": "2160p"}, wiki_test_rule)
s_1080, _ = scoring.score({"resolution": "1080p"}, wiki_test_rule)
s_720, _ = scoring.score({"resolution": "720p"}, wiki_test_rule)
ck(abs(s_2160 - 200.0) < 0.01, "Wiki示例：2160p 得分应 200，得 %.2f" % s_2160)
ck(abs(s_1080 - 133.33) < 0.05, "Wiki示例：1080p 得分应 133.33，得 %.2f" % s_1080)
ck(abs(s_720 - 66.67) < 0.05, "Wiki示例：720p 得分应 66.67，得 %.2f" % s_720)

print("== workscan ==")
g = workscan.scan_work("/strm/x/孤胆特工 (2010) {tmdb-20811}", [
    {"rel": "a", "name": "孤胆特工.2010.2160p.Remux.Dolby Vision.10-bit.24fps.66.0Mbps.H265.TrueHD 7.1.strm"},
    {"rel": "b", "name": "孤胆特工.2010.2160p.HDR10.10-bit.24fps.65.2Mbps.H265.DTSHD-MA 5.1.strm"},
], rules)
ck(len(g) == 1, "应得 1 组，得 %d" % len(g))
if g:
    ck(g[0]["verdict"] == "keep", "DV 应判 keep，得 %s" % g[0]["verdict"])
    ck("Dolby Vision" in (g[0]["keep"] or {}).get("name", ""), "应保留 DV 版本")

# 同规格不同容器 → 分差不足
g2 = workscan.scan_work("/strm/x/测试 (2020) {tmdb-1}", [
    {"rel": "a", "name": "测试.2020.2160p.WEB-DL.HDR10.H265.DDP 5.1.strm"},
    {"rel": "b", "name": "测试.2020.2160p.WEB-DL.HDR10.H265.DDP 5.1.mkv.strm"},
], rules)
ck(bool(g2) and g2[0]["verdict"] in ("tie", "same"), "同规格应判 tie/same，得 %s" % (g2[0]["verdict"] if g2 else "无组"))

print()
print("✅ 全部通过" if not FAIL else "❌ 失败 %d 项" % len(FAIL))
sys.exit(1 if FAIL else 0)
