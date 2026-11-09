"""The standalone review page must keep embedded data separate from code."""
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'core'))
from executor import Protection
from ui import build_board, group_key, render_html


class ScriptCounter(HTMLParser):
    def __init__(self):
        super().__init__()
        self.count = 0

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.count += 1


class ReviewPageCase(unittest.TestCase):
    def test_probe_text_cannot_close_the_script(self):
        payload = "</ScRiPt><script>document.title = 'injected'</script>"
        page = render_html('/root/<example>', {'text': payload})
        parser = ScriptCounter()
        parser.feed(page)
        self.assertEqual(parser.count, 1)
        self.assertIn(r'\u003c/ScRiPt>', page)
        self.assertIn('/root/&lt;example&gt;', page)

    def test_placeholder_like_data_never_changes_where_payload_is_inserted(self):
        page = render_html('/root/__PACKTIDY_DATA__', {'text':'__ROOT__'})
        self.assertIn('<small title="/root/__PACKTIDY_DATA__">', page)
        self.assertIn('const DATA = {"text": "__ROOT__"};', page)

    def test_group_identity_tracks_members_independent_of_order(self):
        group = {'name': 'Series', 'units': ['part1', 'part2']}
        reordered = {**group, 'units': ['part2', 'part1']}
        updated = {**group, 'units': ['part1', 'part3']}
        self.assertEqual(group_key(group), group_key(reordered))
        self.assertNotEqual(group_key(group), group_key(updated))

    def test_s4_answer_and_only_inventoried_paths_reach_production_page(self):
        with tempfile.TemporaryDirectory(prefix='.organizer-test-', dir=PROJECT) as tmp:
            root = Path(tmp)
            source = root/'incoming'
            source.mkdir()
            actual = source/'source.txt'
            actual.write_text('sample', encoding='utf-8')
            (source/'other.txt').write_text('also present', encoding='utf-8')
            probe = root/'_organize/probe'
            probe.mkdir(parents=True)
            group = {'name':'Example', 'units':['source.txt', 'other.txt']}
            fp = group_key(group)
            (probe/'group_demo.json').write_text(json.dumps({
                'path':str(source), 'groups':[group, {'name':'Count only', 'units':17}]}), encoding='utf-8')
            (probe/'units_demo.json').write_text(json.dumps({
                'path':str(source), 'loose_files_as_units':[str(actual)]}), encoding='utf-8')
            stored = {'root':str(root), 'nodes':[{'path':str(source), 'groups':[{
                'id':f'{fp}:0', 'fingerprint':fp,
                'suggest':{'mode':'keep', 'tier':'full', 'aiJudgement':{'hypothesis':'Verified note'}},
            }]}]}
            board = build_board(root, probe, stored, Protection([], set()))
            first, count_only = board['nodes'][0]['groups']
            self.assertEqual(board['matchedAnswers'], 1)
            self.assertEqual(first['tier'], 'full')
            self.assertEqual([u['source'] for u in first['units']], [str(actual), None])
            self.assertEqual(count_only['units'], [])
            html = render_html(root, board)
            self.assertIn('id="wires"', html)
            self.assertIn('Verified note', html)
            stored['nodes'][0]['groups'][0]['fingerprint'] = 'stale'
            self.assertEqual(build_board(root, probe, stored, Protection([], set()))['matchedAnswers'], 0)
