#!/usr/bin/env python3
import json
from pathlib import Path
import unittest
from unittest.mock import patch, MagicMock
import urllib.request

import scripts.updater_service as updater


class UpdaterServiceUnitTests(unittest.TestCase):
    def test_version_tuple(self):
        self.assertEqual(updater._version_tuple('4.4.4'), (4, 4, 4))
        self.assertEqual(updater._version_tuple('4.5.1'), (4, 5, 1))
        self.assertTrue(updater._version_tuple('4.4.5') > updater._version_tuple('4.4.4'))

    def test_parse_changelog_markdown(self):
        md = """
## [4.4.6] - 2026-10-15 - Заголовок 4.4.6
- Пункт 1
- Пункт 2

## [4.4.5] - 2026-10-13 - Заголовок 4.4.5
* Пункт 3
        """
        parsed = updater.parse_changelog_markdown(md)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]['version'], '4.4.6')
        self.assertEqual(parsed[0]['title'], 'Заголовок 4.4.6')
        self.assertEqual(len(parsed[0]['changes']), 2)

    def test_filter_releases_between(self):
        rels = [
            {'version': '4.5.0', 'title': 'v4.5.0', 'changes': []},
            {'version': '4.4.6', 'title': 'v4.4.6', 'changes': []},
            {'version': '4.4.5', 'title': 'v4.4.5', 'changes': []},
            {'version': '4.4.4', 'title': 'v4.4.4', 'changes': []},
        ]
        res = updater.filter_releases_between(rels, '4.4.4', '4.4.6')
        self.assertEqual([r['version'] for r in res], ['4.4.6', '4.4.5'])

    def test_check_github_updates_with_releases_between(self):
        def fake_urlopen(req, timeout=None):
            url = req.full_url if hasattr(req, 'full_url') else str(req)
            if url.endswith('VERSION'):
                resp = MagicMock()
                resp.read.return_value = b'4.4.6'
                resp.__enter__.return_value = resp
                return resp
            if url.endswith('RELEASES.json'):
                resp = MagicMock()
                resp.read.return_value = json.dumps([
                    {'version': '4.4.6', 'title': 'Rel 4.4.6', 'changes': ['C1']},
                    {'version': '4.4.5', 'title': 'Rel 4.4.5', 'changes': ['C2']},
                    {'version': '4.4.4', 'title': 'Rel 4.4.4', 'changes': []},
                ]).encode('utf-8')
                resp.__enter__.return_value = resp
                return resp
            raise ValueError(f"Unknown {url}")

        with patch.object(updater, 'get_current_version', return_value='4.4.4'), \
             patch('urllib.request.urlopen', side_effect=fake_urlopen):
            res = updater.check_github_updates()
            self.assertTrue(res['ok'])
            self.assertTrue(res['has_update'])
            self.assertEqual(res['latest_version'], '4.4.6')
            self.assertEqual(len(res['releases_between']), 2)


if __name__ == '__main__':
    unittest.main()
