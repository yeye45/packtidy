"""Run with PACKTIDY_TEST_CROSS_VOLUME_DIR on a second local volume for a real transfer."""
from argparse import Namespace
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT/'core'))
import cross_volume
import executor
from safety import SafetyError, encode_tsv, within


@unittest.skipUnless(os.name == 'nt', 'cross-volume mutation is available on Windows NTFS only')
class CrossVolumeCase(unittest.TestCase):
    def setUp(self):
        # 不能放系统临时目录——路径里有 AppData，命中保护名单
        # （Journal 会因此拒绝初始化，这是对的）。
        # 也不能直接共用一个项目内目录——CI 并发跑多个 job 会互相踩。
        # 折中：建在项目目录下，但每次用独立子目录。
        import uuid
        slot = PROJECT / '.packtidy-cross' / uuid.uuid4().hex[:12]
        slot.mkdir(parents=True)
        source = tempfile.TemporaryDirectory(prefix='src-', dir=slot)
        self.addCleanup(source.cleanup)
        external = os.environ.get('PACKTIDY_TEST_CROSS_VOLUME_DIR')
        target = tempfile.TemporaryDirectory(prefix='dst-',
                                             dir=external or slot)
        self.addCleanup(target.cleanup)
        self.root, self.target = Path(source.name), Path(target.name)
        if external:
            self.assertTrue(cross_volume.different_volume(self.root, self.target))
        else:
            volumes = patch.object(cross_volume, 'different_volume', side_effect=lambda a, b:
                                   within(a, self.target) != within(b, self.target))
            volumes.start()
            self.addCleanup(volumes.stop)
        environment = patch.dict(os.environ, {'LOCALAPPDATA': str(self.root/'local')})
        environment.start()
        self.addCleanup(environment.stop)
        self.journal = executor.Journal(self.root)
        self.output = io.StringIO()
        redirect = contextlib.redirect_stdout(self.output)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)

    def sample(self):
        directory = self.root/'source'
        (directory/'nested'/'empty').mkdir(parents=True)
        (directory/'nested'/'a.txt').write_bytes(b'first')
        (directory/'b.bin').write_bytes(b'\0\x02')
        return directory, self.target/directory.name

    def apply(self, source, *, destination=None, opt_in=True, roots=None, dry_run=False, receipt=None):
        actions = self.root/'actions.tsv'
        actions.write_bytes(encode_tsv([dict(op='move', src=str(source),
                                            dst=str(destination or self.target), reason='test')],
                                       ('op', 'src', 'dst', 'reason')))
        return executor.apply(Namespace(root=str(self.root), actions=str(actions), plan=None, rename=None,
                                        reason='-', batch_size=50, allow_cross_drive=opt_in,
                                        destination_root=[str(self.target)] if roots is None else roots,
                                        dry_run=dry_run, receipt=receipt))

    def recover(self):
        return executor.recover(Namespace(root=str(self.root)))

    def undo(self, run):
        return executor.undo(Namespace(journal=str(self.journal.path), run_id=run, seq=None,
                                       last=False, list=False, dry_run=False))

    def last_run(self):
        return executor.read_journal(self.journal.path)[-1]['run_id']

    def marker(self, path, kind='SECRET'):
        markers = self.target/'_organize'/'markers.tsv'
        markers.parent.mkdir(exist_ok=True)
        markers.write_text(f'path\tkind\tby\tnote\tts\n{path}\t{kind}\tuser\tx\tx\n', encoding='utf-8')

    def test_explicit_authorization_and_existing_destination(self):
        src, dst = self.sample()
        with self.assertRaises(SafetyError):
            self.apply(src, roots=[])
        with self.assertRaises(SafetyError):
            self.apply(src, opt_in=False)
        self.assertEqual(self.apply(src, opt_in=False, roots=[], dry_run=True), 1)
        self.assertEqual(self.apply(src, destination=self.target/'missing', dry_run=True), 1)
        self.assertEqual(self.apply(src, dry_run=True), 0)
        self.assertFalse(self.journal.path.exists())
        self.assertFalse(dst.exists())
        self.assertTrue(src.exists())

    def test_move_undo_redo_and_reference_update(self):
        src, dst = self.sample()
        probe = self.root/'_organize'/'probe'/'unit.json'
        probe.parent.mkdir(parents=True)
        probe.write_text(json.dumps({'path': str(src), 'action': 'keep'}), encoding='utf-8')
        self.assertEqual(self.apply(src), 0)
        self.assertFalse(src.exists())
        self.assertEqual((dst/'nested'/'a.txt').read_bytes(), b'first')
        self.assertTrue((dst/'nested'/'empty').is_dir())
        self.assertEqual(json.loads(probe.read_text(encoding='utf-8'))['path'], str(dst))
        import coverage
        rows = coverage.check(str(self.root), probe.parent)
        self.assertIn({'node': src.name, 'reason': '已跨卷移出', 'status': 'stale'}, rows)
        self.assertFalse(list((self.root/'_organize'/'cross-volume').glob('*.original')))
        run = self.last_run()
        self.assertEqual(self.undo(run), 0)
        self.assertFalse(dst.exists())
        self.assertEqual((src/'b.bin').read_bytes(), b'\0\x02')
        self.assertEqual(self.undo(self.last_run()), 0)
        self.assertFalse(src.exists())
        self.assertEqual(self.undo(run), 0)
        self.assertTrue(src.exists())

    def test_verified_cross_volume_move_requires_a_fresh_source_tree(self):
        src, dst = self.sample()
        receipt = self.root/'review.json'
        self.assertEqual(self.apply(src, dry_run=True, receipt=receipt), 0)
        self.assertFalse(dst.exists())
        (src/'nested'/'a.txt').write_bytes(b'changed after preview')
        with self.assertRaisesRegex(SafetyError, '预演清单不符'):
            self.apply(src, receipt=receipt)
        self.assertTrue(src.is_dir())
        self.assertFalse(dst.exists())
        fresh = self.root/'fresh-review.json'
        self.assertEqual(self.apply(src, dry_run=True, receipt=fresh), 0)
        self.assertEqual(self.apply(src, receipt=fresh), 0)
        self.assertEqual((dst/'nested'/'a.txt').read_bytes(), b'changed after preview')
        self.assertEqual(self.undo(self.last_run()), 0)
        self.assertEqual((src/'nested'/'a.txt').read_bytes(), b'changed after preview')
        self.assertFalse(dst.exists())

    def test_destination_descendant_marker_and_collision_are_preserved(self):
        src, dst = self.sample()
        secret = dst/'nested'/'a.txt'
        self.marker(secret)
        self.assertEqual(self.apply(src), 1)
        self.assertNotIn(str(secret), self.output.getvalue())
        self.marker(self.target/'unrelated')
        dst.write_text('unrelated', encoding='utf-8')
        self.assertEqual(self.apply(src), 1)
        self.assertEqual(dst.read_text(encoding='utf-8'), 'unrelated')
        self.assertTrue(src.exists())

    def test_links_and_windows_streams_block_before_copy(self):
        src, dst = self.sample()
        os.link(src/'b.bin', self.root/'alias.bin')
        self.assertEqual(self.apply(src), 1)
        (self.root/'alias.bin').unlink()
        if os.name == 'nt':
            with open(str(src/'b.bin') + ':extra', 'wb') as stream:
                stream.write(b'protected metadata')
            self.assertEqual(self.apply(src), 1)
        self.assertFalse(dst.exists())
        self.assertFalse(self.journal.unresolved())

    def test_interruption_during_copy_can_abort_without_retiring_original(self):
        src, dst = self.sample()
        def partial(source, stage, *_):
            stage.mkdir()
            (stage/'partial').write_text('incomplete', encoding='utf-8')
            raise OSError('simulated disk full')
        with patch.object(cross_volume, 'copy_inventory', side_effect=partial):
            with self.assertRaises(OSError):
                self.apply(src)
        self.assertTrue(src.exists())
        self.assertFalse(dst.exists())
        self.assertTrue(self.journal.unresolved())
        self.assertEqual(self.recover(), 0)
        self.assertFalse(self.journal.unresolved())

    def test_copy_verification_and_racing_destination_preserve_original(self):
        src, dst = self.sample()
        copy = cross_volume.copy_inventory
        def corrupt(source, stage, *args):
            copy(source, stage, *args)
            (stage/'b.bin').write_bytes(b'corrupt copy')
        with patch.object(cross_volume, 'copy_inventory', side_effect=corrupt):
            with self.assertRaises(SafetyError):
                self.apply(src)
        self.assertTrue(src.exists())
        self.assertFalse(dst.exists())
        self.assertEqual(self.recover(), 0)

        publish = cross_volume.publish
        def race(before, after, rename):
            Path(after).write_bytes(b'unrelated file')
            publish(before, after, rename)
        with patch.object(cross_volume, 'publish', side_effect=race):
            with self.assertRaises(OSError):
                self.apply(src)
        self.assertTrue(src.exists())
        self.assertEqual(dst.read_bytes(), b'unrelated file')
        self.assertEqual(self.recover(), 0)

    def test_target_executor_lock_is_respected(self):
        src, dst = self.sample()
        with executor.Journal(self.target).locked():
            with self.assertRaises(SafetyError):
                self.apply(src)
        self.assertTrue(src.exists())
        self.assertFalse(dst.exists())
        self.assertFalse(self.journal.unresolved())

    def test_interruption_after_publication_or_retirement_resumes(self):
        publish = cross_volume.publish
        for stop_after in ('publish', 'retire'):
            with self.subTest(stop_after=stop_after):
                src, dst = self.sample()
                def interrupt(before, after, rename):
                    publish(before, after, rename)
                    action = 'retire' if str(after).endswith('.original') else 'publish'
                    if action == stop_after:
                        raise OSError('simulated interruption')
                with patch.object(cross_volume, 'publish', side_effect=interrupt):
                    with self.assertRaises(OSError):
                        self.apply(src)
                self.assertTrue(dst.exists())
                self.assertEqual(self.recover(), 0)
                self.assertFalse(src.exists())
                self.assertFalse(self.journal.unresolved())
                self.assertEqual(self.undo(self.last_run()), 0)
                src.rename(src.with_name('checked-' + stop_after))

    def test_interrupted_purge_resumes_only_known_entries(self):
        src, dst = self.sample()
        def interrupt(original, *_):
            (original/'b.bin').unlink()
            raise OSError('simulated interruption')
        with patch.object(cross_volume, 'remove_inventory', side_effect=interrupt):
            with self.assertRaises(OSError):
                self.apply(src)
        tx, = self.journal.unresolved()
        original, _ = executor.transfer_paths(self.journal, tx)
        (original/'unexpected').write_text('keep this', encoding='utf-8')
        with self.assertRaises(SafetyError):
            self.recover()
        self.assertTrue((original/'nested'/'a.txt').exists())
        self.assertEqual((original/'unexpected').read_text(encoding='utf-8'), 'keep this')
        (original/'unexpected').unlink()
        self.assertEqual(self.recover(), 0)
        self.assertFalse(original.exists())
        self.assertTrue((dst/'b.bin').exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows handle-based deletion')
    def test_windows_never_removes_a_racing_quarantine_replacement(self):
        src, dst = self.sample()
        open_reader = cross_volume.open_reader
        def replace(path, *, delete=False):
            if delete and path.name == 'a.txt':
                path.rename(path.with_name('original.txt'))
                path.write_bytes(b'replacement data')
            return open_reader(path, delete=delete)
        with patch.object(cross_volume, 'open_reader', side_effect=replace):
            with self.assertRaises(SafetyError):
                self.apply(src)
        tx, = self.journal.unresolved()
        original, _ = executor.transfer_paths(self.journal, tx)
        self.assertEqual((original/'nested'/'a.txt').read_bytes(), b'replacement data')
        self.assertEqual((original/'nested'/'original.txt').read_bytes(), b'first')
        self.assertEqual((dst/'nested'/'a.txt').read_bytes(), b'first')

    def test_target_change_after_publication_keeps_source(self):
        src, dst = self.sample()
        publish = cross_volume.publish
        def edit(before, after, rename):
            publish(before, after, rename)
            (Path(after)/'b.bin').write_bytes(b'changed')
        with patch.object(cross_volume, 'publish', side_effect=edit):
            with self.assertRaises(SafetyError):
                self.apply(src)
        self.assertTrue(src.exists())
        self.assertEqual((dst/'b.bin').read_bytes(), b'changed')
        with self.assertRaises(SafetyError):
            self.recover()

    def test_undo_preserves_post_move_changes_and_new_destination_marker(self):
        src, dst = self.sample()
        self.assertEqual(self.apply(src), 0)
        run = self.last_run()
        self.marker(dst/'nested'/'a.txt')
        self.assertEqual(self.undo(run), 1)
        self.marker(self.target/'unrelated')
        (dst/'nested'/'a.txt').write_bytes(b'changed')
        self.assertEqual(self.undo(run), 1)
        self.assertFalse(src.exists())
        self.assertEqual((dst/'nested'/'a.txt').read_bytes(), b'changed')
