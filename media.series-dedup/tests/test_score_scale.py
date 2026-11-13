"""Numeric checks for MT's weight × ten-point position score."""
import unittest
from pathlib import Path
from series_core.quality.models import MediaFile, MediaMeta
from series_core.quality.scorer import QualityScorer


class ScoreScale(unittest.TestCase):
    def test_mt_ten_point_total_details_and_difference(self):
        scorer = QualityScorer({'scoring': {'resolution': {
            'weight': 20, 'priority': ['2160p', '1080p']}}}, force_dedup=True)
        files = [MediaFile(Path(f'{res}.strm'), True, 0, 0,
                           MediaMeta(resolution=res), link_kind='http')
                 for res in ('2160p', '1080p')]
        winner, losers, diff, _ = scorer.compare_and_decide(files)
        self.assertEqual(winner.score, 200)
        self.assertEqual(losers[0].score, 100)
        self.assertEqual(diff, 100)
        self.assertEqual(winner.score_details['resolution']['score'], 200)

    def test_bitrate_and_subtitle_use_same_scale(self):
        scorer = QualityScorer({'scoring': {
            'bitrate': {'weight': 25}, 'subtitle': {'enabled': True, 'weight': 5}}})
        file = MediaFile(Path('movie.strm'), True, 0, 0,
                         MediaMeta(bitrate_kbps=50000), companions=[Path('movie.srt')])
        self.assertEqual(scorer.calculate_score(file), 300)
        self.assertEqual(file.score_details['bitrate']['score'], 250)
        self.assertEqual(file.score_details['subtitle']['score'], 50)
