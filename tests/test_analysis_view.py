import json
from pathlib import Path
import tempfile
import unittest

from ai_collusion.analysis_view import load_analysis


class AnalysisTests(unittest.TestCase):
    def load(self, review, digest='reviewed'):
        record = {'_sha256': digest, '_source': 'example.json', 'episode': {'turns': [{'turn': 1}, {'turn': 2}]}}
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'review.json'
            path.write_text(json.dumps({'records': {'reviewed': review}}))
            return load_analysis(path, [record])

    def test_matching_source_and_full_coverage(self):
        _, reviews = self.load({'turns': {'1': {}, '2': {}}})
        self.assertIn('reviewed', reviews)

    def test_changed_source_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.load({'turns': {'1': {}, '2': {}}}, digest='changed')

    def test_missing_turn_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'every recorded turn'):
            self.load({'turns': {'1': {}}})

    def test_invented_evidence_reference_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'missing turn'):
            self.load({'turns': {'1': {}, '2': {}}, 'findings': [{'turns': [3]}]})

    def test_invented_key_turn_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'missing key turn'):
            self.load({'turns': {'1': {}, '2': {}}, 'key_turns': [3]})


if __name__ == '__main__':
    unittest.main()
