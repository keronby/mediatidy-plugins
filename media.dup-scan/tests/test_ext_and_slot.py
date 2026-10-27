# -*- coding: utf-8 -*-
import unittest
from core import nameinfo, rules as rulelib, workscan


class TestExtAndSlot(unittest.TestCase):
    def test_extract_real_ext(self):
        # 1. 本地/云盘路径
        p1 = "/CloudNAS/CloudDrive/115open/电影/茶之味.2004.1080p.iso"
        self.assertEqual(nameinfo.extract_real_ext(p1), "iso")

        # 2. 带 query fileName 的 URL
        p2 = "http://192.168.10.207:2019/api/shareVideoPlayUrl?accountId=3&fileName=%E8%8C%B6%E4%B9%8B%E5%91%B3.2004.1080p.mkv&password=9535"
        self.assertEqual(nameinfo.extract_real_ext(p2), "mkv")

        # 3. 纯 URL
        p3 = "https://example.com/stream/avatar.2009.mp4?token=xyz"
        self.assertEqual(nameinfo.extract_real_ext(p3), "mp4")

        # 4. 无法识别的 fallback
        p4 = "invalid content without media ext"
        self.assertEqual(nameinfo.extract_real_ext(p4), "strm")

    def test_slot_isolation_between_iso_and_mkv(self):
        # 模拟一个电影目录下有两份 strm，一份源是 iso，一份源是 mkv
        work_dir = "/strm/电影/茶之味 (2004) {tmdb-15318}"
        entries = [
            {
                "rel": "茶之味 (2004) {tmdb-15318}/茶之味.2004.1080p.BluRay.SDR.8-bit.24fps.31.9Mbps.H264.DTSHD-MA 5.1.strm",
                "name": "茶之味.2004.1080p.BluRay.SDR.8-bit.24fps.31.9Mbps.H264.DTSHD-MA 5.1.strm",
                "realExt": "iso",
            },
            {
                "rel": "茶之味 (2004) {tmdb-15318}/茶之味.2004.1080p.10-bit.24fps.10.4Mbps.H265.DTS.strm",
                "name": "茶之味.2004.1080p.10-bit.24fps.10.4Mbps.H265.DTS.strm",
                "realExt": "mkv",
            },
        ]
        # 带有 ISO 槽位的规则
        rules = {
            "min_score_diff": 8.0,
            "slots": [{"name": "ISO", "match": {"file_extension": ["iso"]}}],
            "scoring": {
                "resolution": {"weight": 20.0, "priority": ["2160p", "1080p"]},
            },
        }
        groups = workscan.scan_work(work_dir, entries, rules)
        # 因为分属 ISO 槽位和 default 槽位，各自只有 1 个文件，因此不构成重复组！
        self.assertEqual(len(groups), 0)

    def test_same_slot_dup_decision(self):
        # 模拟同一槽位下两个版本比较
        work_dir = "/strm/电影/茶之味 (2004) {tmdb-15318}"
        entries = [
            {
                "rel": "茶之味 (2004) {tmdb-15318}/茶之味.2004.2160p.strm",
                "name": "茶之味.2004.2160p.strm",
                "realExt": "mkv",
            },
            {
                "rel": "茶之味 (2004) {tmdb-15318}/茶之味.2004.1080p.strm",
                "name": "茶之味.2004.1080p.strm",
                "realExt": "mkv",
            },
        ]
        rules = {
            "min_score_diff": 5.0,
            "scoring": {
                "resolution": {"weight": 20.0, "priority": ["2160p", "1080p"]},
            },
        }
        groups = workscan.scan_work(work_dir, entries, rules)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["verdict"], "keep")
        self.assertEqual(groups[0]["keep"]["resolution"], "2160p")
        self.assertEqual(groups[0]["drop"]["resolution"], "1080p")


if __name__ == "__main__":
    unittest.main()
