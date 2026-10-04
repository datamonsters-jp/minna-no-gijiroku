import json
from pathlib import Path
import unittest
import unicodedata

ROOT = Path(__file__).resolve().parents[1]

class SiteRegressionTests(unittest.TestCase):
    def test_verified_report_results_and_preserved_unknowns(self):
        reports = []
        for day, numbers in [(1, range(1,5)), (3, range(5,7))]:
            data = json.loads((ROOT/f'data/summaries/令和8年_第2回定例会_{day}日目.json').read_text())
            expected = {f'報告第{n}号' for n in numbers}
            rows = [g for g in data['議案結果'] if unicodedata.normalize('NFKC', g['議案番号']) in expected]
            self.assertEqual(len(rows),len(expected))
            self.assertTrue(all(g['結果'] == '報告のみ' for g in rows))
            reports.extend(rows)
            if day == 1:
                self.assertTrue(any(g['結果'] == '不明' for g in data['議案結果']))
        self.assertEqual(len(reports),6)

    def test_rendered_notices_and_date(self):
        for p in (ROOT/'docs').rglob('*.html'):
            h=p.read_text(); self.assertIn('class="ai-notice"',h,p)
            self.assertIn('非公式サイト',h,p)
        home=(ROOT/'docs/index.html').read_text()
        self.assertIn('最新の会議日',home); self.assertNotIn('最終更新',home)
        meeting=(ROOT/'docs/kaigi/y2026-teirei-2.html').read_text()
        self.assertEqual(meeting.count('>報告のみ</td>'),6)
        self.assertIn('>不明</td>',meeting)
        self.assertIn('https://www.town.suo-oshima.lg.jp/uploaded/attachment/23964.pdf',meeting)

if __name__ == '__main__': unittest.main()
