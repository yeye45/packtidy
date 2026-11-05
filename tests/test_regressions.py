"""Safety regressions use synthetic files only; no third-party dependencies."""
import csv
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'core'))


class FilesystemCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='.organizer-test-',dir=PROJECT)
        self.base = Path(self.temp.name)
        self.root = self.base / 'root'
        self.root.mkdir()
        self.env = dict(os.environ, LOCALAPPDATA=str(self.base / 'local'),
                        PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
        self.addCleanup(self.temp.cleanup)

    def put(self, name, text='synthetic'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def markers(self, entries):
        self.put('_organize/markers.tsv', 'path\tkind\tby\tnote\tts\n' + ''.join(
            f'{p}\t{k}\tuser\ttest\t2026-11-03\n' for p, k in entries))

    def command(self, verb, args):
        engine = PROJECT / 'core/executor.py'
        if engine.exists():
            cmd = [sys.executable, '-B', str(engine), verb, *map(str, args)]
        else:
            pwsh = shutil.which('pwsh')
            if not pwsh:
                self.skipTest('PowerShell required for baseline')
            translated = {'--root':'-Root', '--actions':'-Actions', '--journal':'-Journal',
                          '--run-id':'-RunId', '--dry-run':'-DryRun'}
            cmd = [pwsh, '-NoProfile', '-NonInteractive', '-File',
                   str(PROJECT / f'scripts/{verb}.ps1')]
            cmd.extend(translated.get(str(a), str(a)) for a in args)
        return subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                              errors='replace', env=self.env, timeout=30)

    def apply(self, actions, dry=False):
        path = self.put('_organize/actions.tsv', 'op\tsrc\tdst\treason\n' + ''.join(
            f'{op}\t{src}\t{dst}\ttest\n' for op, src, dst in actions))
        return self.command('apply', ['--root', self.root, '--actions', path] +
                            (['--dry-run'] if dry else []))

    def rows(self):
        with (self.root / '_organize/journal.tsv').open(encoding='utf-8-sig', newline='') as f:
            return list(csv.DictReader(f, delimiter='\t'))

    def undo(self, run):
        return self.command('undo', ['--journal', self.root / '_organize/journal.tsv',
                                     '--run-id', run])

    def coverage(self):
        return subprocess.run([sys.executable, '-B', str(PROJECT/'core/coverage.py'),
                               str(self.root), '--json'], capture_output=True, text=True,
                              encoding='utf-8', env=self.env, timeout=15)

    def test_secret_descendant_blocks_parent_move(self):
        file = self.put('box/private.key')
        self.markers([(file, 'SECRET')])
        r = self.apply([('move', file.parent, self.root/'dest')])
        self.assertTrue(file.exists(), r.stdout + r.stderr)
        self.assertNotIn('private.key', r.stdout + r.stderr)

    def test_forward_slash_marker_protection(self):
        file = self.put('private/key.txt')
        self.markers([(file.parent, 'NO_TOUCH')])
        self.apply([('move', file.as_posix(), self.root/'dest')])
        self.assertTrue(file.exists())

    def test_note_does_not_override_protection(self):
        file = self.put('private/key.txt')
        self.markers([(file.parent, 'NO_TOUCH'), (file, 'NOTE')])
        self.apply([('move', file, self.root/'dest')])
        self.assertTrue(file.exists())

    @unittest.skipUnless(os.name=='nt','Windows path aliases')
    def test_short_path_and_trailing_dot_cannot_bypass_secret(self):
        import ctypes
        file=self.put('private credentials/secret.txt')
        self.markers([(file.parent,'SECRET')])
        function=ctypes.windll.kernel32.GetShortPathNameW
        function.argtypes=(ctypes.c_wchar_p,ctypes.c_wchar_p,ctypes.c_uint32)
        buffer=ctypes.create_unicode_buffer(32768)
        self.assertTrue(function(str(file.parent),buffer,len(buffer)))
        aliases=[str(Path(buffer.value)/file.name),str(file.parent)+'.\\'+file.name]
        for alias in aliases:
            r=self.apply([('rename',alias,'renamed.txt')])
            self.assertTrue(file.exists(),r.stdout+r.stderr)
            self.assertNotIn('secret.txt',r.stdout+r.stderr)

    def test_prefix_sibling_marker_is_unchanged(self):
        (self.root/'A').mkdir()
        protected = self.put('AB/file.txt')
        self.markers([(protected.parent, 'NO_TOUCH')])
        r = self.apply([('rename', self.root/'A', 'X')])
        self.assertEqual(r.returncode, 0, r.stdout+r.stderr)
        data = (self.root/'_organize/markers.tsv').read_text(encoding='utf-8-sig')
        self.assertIn(str(protected.parent), data)

    def test_repeat_undo_preserves_replacement_file(self):
        original = self.put('old.txt', 'original')
        r = self.apply([('rename', original, 'new.txt')])
        self.assertEqual(r.returncode, 0, r.stdout+r.stderr)
        run = self.rows()[0]['run_id']
        self.assertEqual(self.undo(run).returncode, 0)
        replacement = self.put('new.txt', 'replacement')
        self.undo(run)
        self.assertTrue(replacement.exists())
        self.assertEqual(replacement.read_text(), 'replacement')
        self.assertEqual(original.read_text(), 'original')

    def test_undo_of_undo_reapplies_rename(self):
        file = self.put('old.txt')
        self.apply([('rename', file, 'new.txt')])
        self.undo(self.rows()[0]['run_id'])
        undo_run = self.rows()[-1]['run_id']
        r = self.undo(undo_run)
        self.assertEqual(r.returncode, 0, r.stdout+r.stderr)
        self.assertTrue((self.root/'new.txt').exists())
        self.assertFalse(file.exists())

    def test_move_collision_preserves_both_contents_and_undo_restores_them(self):
        first=self.put('one/same.txt','first')
        second=self.put('two/same.txt','second')
        r=self.apply([('move',first,self.root/'dest'),('move',second,self.root/'dest')])
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual((self.root/'dest/same.txt').read_text(),'first')
        self.assertEqual((self.root/'dest/same_1.txt').read_text(),'second')
        r=self.undo(self.rows()[0]['run_id'])
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual(first.read_text(),'first')
        self.assertEqual(second.read_text(),'second')

    def test_rename_undo_conflict_uses_unoccupied_suffix(self):
        original=self.put('old.txt','original')
        self.apply([('rename',original,'new.txt')])
        self.put('old.txt','replacement')
        self.put('old_undone.txt','older conflict')
        r=self.undo(self.rows()[0]['run_id'])
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual((self.root/'old_undone_1.txt').read_text(),'original')
        self.assertEqual(original.read_text(),'replacement')
        self.assertEqual((self.root/'old_undone.txt').read_text(),'older conflict')

    def test_move_and_undo_update_nested_references(self):
        file = self.put('A/file.txt')
        probe = self.put('_organize/probe/node.json', json.dumps({
            'path':str(file.parent), 'action':'keep',
            'confirmed_units':[{'path':str(file)}], 'notes':f'leave {file} alone'}))
        self.markers([(file.parent, 'NOTE')])
        self.apply([('move', file.parent, self.root/'dest')])
        data = json.loads(probe.read_text(encoding='utf-8-sig'))
        self.assertEqual(data['path'], str(self.root/'dest/A'))
        self.assertEqual(data['confirmed_units'][0]['path'], str(self.root/'dest/A/file.txt'))
        self.assertEqual(data['notes'], f'leave {file} alone')
        run = self.rows()[0]['run_id']
        self.undo(run)
        data = json.loads(probe.read_text(encoding='utf-8-sig'))
        self.assertEqual(data['path'], str(file.parent))
        self.assertEqual(data['confirmed_units'][0]['path'], str(file))

    def test_coverage_detects_nested_missing_stages(self):
        child = self.root/'parent/child'
        child.mkdir(parents=True)
        for name, obj in [
            ('parent', {'path':str(child.parent), 'action':'enter', 'enter_children':[str(child)]}),
            ('units_parent', {'path':str(child.parent),'total_units':0,
                              'sub_containers':[{'path':str(child)}]}),
            ('group_parent', {'path':str(child.parent),'groups':[]}),
            ('child', {'path':str(child),'action':'split'})]:
            self.put(f'_organize/probe/{name}.json', json.dumps(obj))
        r = self.coverage()
        self.assertEqual(r.returncode, 1, r.stdout+r.stderr)
        rows = json.loads(r.stdout)
        self.assertTrue(any('child' in row['node'] and 'S2清点' in row['missing'] for row in rows))

    def test_coverage_accepts_bom_and_normalizes_paths(self):
        node = self.root/'node'
        node.mkdir()
        probe = self.put('_organize/probe/node.json', json.dumps({
            'path':node.as_posix(), 'action':'keep'}))
        probe.write_text(probe.read_text(), encoding='utf-8-sig')
        r = self.coverage()
        self.assertEqual(r.returncode, 0, r.stdout+r.stderr)

    def test_junction_does_not_escape_or_count_target(self):
        external = self.base/'outside'
        external.mkdir()
        file = external/'outside.txt'
        file.write_text('outside')
        link = self.root/'link'
        if os.name == 'nt':
            ps = shutil.which('pwsh') or shutil.which('powershell')
            if not ps:
                self.skipTest('PowerShell needed to create a junction')
            script = self.base/'link.ps1'
            script.write_text("New-Item -ItemType Junction -Path '"+str(link).replace("'","''")+
                              "' -Target '"+str(external).replace("'","''")+"' | Out-Null", encoding='utf-8-sig')
            subprocess.run([ps,'-NoProfile','-File',str(script)],check=True,capture_output=True)
            self.addCleanup(lambda: os.rmdir(link) if os.path.lexists(link) else None)
        else:
            link.symlink_to(external, target_is_directory=True)
        self.apply([('move',link/'outside.txt', self.root/'dest')])
        self.assertTrue(file.exists())
        import signals
        data = signals.analyze(str(self.root))
        # Count other known test files, but never outside.txt through the link.
        expected = sum(1 for p in (self.root/'_organize').rglob('*') if p.is_file())
        self.assertEqual(data['file-count-total'], expected)

    def test_dry_run_does_not_create_state_directory(self):
        file = self.put('input.txt')
        actions = self.base/'actions.tsv'
        actions.write_text(f'op\tsrc\tdst\treason\nmove\t{file}\t{self.root / "dest"}\ttest\n')
        r = self.command('apply',['--root',self.root,'--actions',actions,'--dry-run'])
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertFalse((self.root/'_organize').exists())
        self.assertTrue(file.exists())


class SequenceRegression(unittest.TestCase):
    def test_unreadable_directory_is_not_reported_empty(self):
        import signals
        with patch.object(signals.os,'scandir',side_effect=PermissionError('denied')):
            data=signals.analyze(str(PROJECT))
        self.assertIsNone(data['empty'])
        self.assertFalse(data['complete'])

    def test_two_seasons_are_not_merged(self):
        import signals
        entries=[{'name':f'Show S{season:02d}E{episode:02d} [1080p].mkv','dir':False}
                 for season in (1,2) for episode in range(1,4)]
        seq=signals.sig_sequence(entries)['sequence']
        self.assertEqual(seq['count'],3)
        self.assertEqual(seq['coverage'],1)

    def test_resolution_is_not_episode(self):
        import signals
        entries = [{'name':f'[Group] Show - {i:02d} [1080p].mkv','dir':False}
                   for i in range(1,13)]
        seq = signals.sig_sequence(entries)['sequence']
        self.assertEqual(seq['range'],'1-12')
        self.assertEqual(seq['coverage'],1.0)

    def test_missing_episode_survives_resolution_tag(self):
        import signals
        entries = [{'name':f'Show S01E{i:02d} [1080p].mkv','dir':False}
                   for i in (1,2,3,5,6)]
        seq = signals.sig_sequence(entries)['sequence']
        self.assertIn(4,seq['missing'])
        self.assertLessEqual(seq['coverage'],1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
