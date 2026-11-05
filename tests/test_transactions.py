"""Interrupted transactions, undo/redo, and real PowerShell bridge checks."""
from argparse import Namespace
import contextlib
import io
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
sys.path.insert(0,str(PROJECT/'core'))
import executor
from safety import SafetyError


class TransactionCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='.organizer-test-',dir=PROJECT)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base/'root'
        self.root.mkdir()
        self.env = patch.dict(os.environ,{'LOCALAPPDATA':str(self.base/'local'),
                                        'PYTHONIOENCODING':'utf-8'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.journal = executor.Journal(self.root)

    def put(self, name, content='synthetic'):
        p = self.root/name
        p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(content,encoding='utf-8')
        return p

    def move(self, source, target, run='test-run'):
        with self.journal.locked():
            return executor.transaction(self.journal,run,'rename','move',str(source),str(target),'test')

    def undo(self,run):
        with contextlib.redirect_stdout(io.StringIO()):
            return executor.undo(Namespace(journal=str(self.journal.path),run_id=run,
                                            seq=0,last=False,list=False,dry_run=False))

    def recover(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return executor.recover(Namespace(root=str(self.root)))

    def test_prepare_failure_never_moves_user_file(self):
        source=self.put('before.txt')
        target=self.root/'after.txt'
        with patch.object(executor.Journal,'append',side_effect=OSError('journal unavailable')):
            with self.assertRaises(OSError):
                self.move(source,target)
        self.assertTrue(source.exists())
        self.assertFalse(target.exists())
        self.assertTrue(self.journal.unresolved())
        self.recover()
        self.assertFalse(self.journal.unresolved())

    def test_interrupt_after_move_finishes_references_and_can_undo(self):
        source=self.put('A/file.txt').parent
        target=self.root/'B'
        probe=self.put('_organize/probe/node.json',json.dumps({'path':str(source),'action':'keep'}))
        rename=executor.rename_noreplace
        def crash(src,dst):
            rename(src,dst)
            raise OSError('simulated process interruption')
        with patch.object(executor,'rename_noreplace',side_effect=crash):
            with self.assertRaises(OSError):
                self.move(source,target)
        self.assertTrue(target.exists())
        self.assertTrue(self.journal.unresolved())
        self.recover()
        self.assertEqual(json.loads(probe.read_text())['path'],str(target))
        self.assertFalse(self.journal.unresolved())
        self.assertEqual(self.undo('test-run'),0)
        self.assertTrue(source.exists())
        self.assertEqual(json.loads(probe.read_text())['path'],str(source))

    def test_partial_reference_update_is_resumed_idempotently(self):
        source=self.put('A/file.txt').parent
        target=self.root/'B'
        probes=[self.put(f'_organize/probe/node{i}.json',json.dumps({'path':str(source),'action':'keep'}))
                for i in range(2)]
        finish=executor.finish_references
        def crash(edits):
            finish(edits[:1])
            raise OSError('simulated interruption')
        with patch.object(executor,'finish_references',side_effect=crash):
            with self.assertRaises(OSError):
                self.move(source,target)
        self.recover()
        for probe in probes:
            self.assertEqual(json.loads(probe.read_text())['path'],str(target))
        self.assertEqual(self.recover(),0)

    def test_mirror_completion_restores_missing_main_suffix(self):
        source=self.put('before.txt')
        target=self.root/'after.txt'
        append=executor.Journal.append
        def crash(journal,*args,**kwargs):
            before=journal.path.read_bytes()
            result=append(journal,*args,**kwargs)
            status=kwargs.get('status',args[5] if len(args)>5 else 'done')
            if status=='done':
                journal.path.write_bytes(before)
                raise OSError('main log lost completion')
            return result
        with patch.object(executor.Journal,'append',new=crash):
            with self.assertRaises(OSError):
                self.move(source,target)
        self.assertTrue(target.exists())
        self.recover()
        self.assertEqual(self.journal.path.read_bytes(),self.journal.mirror.read_bytes())
        self.assertEqual(self.undo('test-run'),0)
        self.assertTrue(source.exists())

    def test_newer_mirrored_mkdir_identity_wins_over_stale_primary(self):
        target=self.root/'created'
        write=executor.atomic_write
        count=0
        def crash(path,data):
            nonlocal count
            if Path(path).parent==self.journal.tx_dir:
                count+=1
                if count==2:
                    raise OSError('primary manifest unavailable')
            write(path,data)
        with self.journal.locked():
            with patch.object(executor,'atomic_write',side_effect=crash):
                with self.assertRaises(OSError):
                    executor.transaction(self.journal,'mkdir-run','mkdir','mkdir','-',str(target),'test')
        self.assertFalse(target.exists())
        self.assertIsNotNone(self.journal.unresolved()[0]['identity'])
        self.recover()
        self.assertFalse(self.journal.unresolved())
        self.assertEqual(self.undo('mkdir-run'),0)
        self.assertFalse(target.exists())

    def test_mkdir_crash_before_identity_never_publishes_unknown_directory(self):
        target=self.root/'created'
        get_identity=executor.identity
        def crash(path):
            if 'staging' in Path(path).parts:
                raise OSError('interrupted immediately after staging mkdir')
            return get_identity(path)
        with self.journal.locked():
            with patch.object(executor,'identity',side_effect=crash):
                with self.assertRaises(OSError):
                    executor.transaction(self.journal,'mkdir-run','mkdir','mkdir','-',str(target),'test')
        self.assertFalse(target.exists())
        self.assertTrue(self.journal.unresolved())
        self.recover()
        self.assertFalse(self.journal.unresolved())

    def test_racing_destination_is_preserved_and_prepare_can_abort(self):
        source=self.put('before.txt','original')
        target=self.root/'after.txt'
        rename=executor.rename_noreplace
        def race(src,dst):
            Path(dst).write_text('racing destination')
            rename(src,dst)
        with patch.object(executor,'rename_noreplace',side_effect=race):
            with self.assertRaises(OSError):
                self.move(source,target)
        self.assertEqual(source.read_text(),'original')
        self.assertEqual(target.read_text(),'racing destination')
        self.recover()
        self.assertFalse(self.journal.unresolved())

    def test_new_marker_blocks_undo_without_leaking_path(self):
        source=self.put('before.txt')
        target=self.root/'after.txt'
        self.move(source,target)
        self.put('_organize/markers.tsv',f'path\tkind\tby\tnote\tts\n{target}\tSECRET\tuser\tx\tx\n')
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            result=executor.undo(Namespace(journal=str(self.journal.path),run_id='test-run',
                                          seq=0,last=False,list=False,dry_run=False))
        self.assertEqual(result,1)
        self.assertTrue(target.exists())
        self.assertNotIn(str(target),output.getvalue())

    def test_replaced_target_is_never_undone(self):
        source=self.put('before.txt','original')
        target=self.root/'after.txt'
        self.move(source,target)
        target.unlink()
        target.write_text('replacement with a different size')
        self.assertEqual(self.undo('test-run'),1)
        self.assertEqual(target.read_text(),'replacement with a different size')

    def test_undo_redo_new_directories_and_move(self):
        source=self.put('source.txt')
        target=self.root/'one/two/source.txt'
        with self.journal.locked():
            executor.ensure_parents(self.journal,'batch',target.parent,'test')
            executor.transaction(self.journal,'batch','move','move',str(source),str(target),'test')
        self.assertEqual(self.undo('batch'),0)
        self.assertTrue(source.exists())
        self.assertFalse((self.root/'one').exists())
        undo_run=executor.read_journal(self.journal.path)[-1]['run_id']
        self.assertEqual(self.undo(undo_run),0)
        self.assertTrue(target.exists())
        self.assertEqual(self.undo('batch'),0)
        self.assertTrue(source.exists())
        self.assertFalse((self.root/'one').exists())

    def test_protected_destination_and_self_move_are_rejected(self):
        source=self.put('source/file.txt').parent
        for target in (self.root/'Windows',source/'nested'):
            prepared,blocked=executor.preflight(self.root,[dict(op='move',src=str(source),dst=str(target),reason='x')])
            self.assertFalse(prepared)
            self.assertTrue(blocked)
        self.assertTrue((source/'file.txt').exists())

    def test_malformed_probe_fails_before_file_move(self):
        source=self.put('before.txt')
        self.put('_organize/probe/bad.json','{')
        prepared,blocked=executor.preflight(self.root,[dict(op='rename',src=str(source),dst='after.txt')])
        self.assertFalse(prepared)
        self.assertTrue(blocked)
        self.assertTrue(source.exists())

    def test_journal_mirrors_are_separated_by_root(self):
        other=self.base/'other'
        other.mkdir()
        self.assertNotEqual(self.journal.mirror,executor.Journal(other).mirror)

    def test_protected_root_is_rejected_before_creating_state(self):
        root=self.base/'Windows'
        root.mkdir()
        with self.assertRaises(SafetyError):
            executor.Journal(root)
        self.assertFalse((root/'_organize').exists())

    def test_missing_input_or_recovery_root_does_not_create_state(self):
        args=Namespace(root=str(self.root),actions=str(self.base/'missing.tsv'),
                       rename=None,plan=None,reason='-')
        with self.assertRaises(SafetyError):
            executor.read_actions(args)
        missing=self.base/'missing-root'
        with self.assertRaises(SafetyError):
            executor.recover(Namespace(root=str(missing)))
        self.assertFalse(missing.exists())
        self.assertFalse((self.root/'_organize').exists())

    @unittest.skipUnless(os.name=='nt','Windows exclusive file sharing')
    def test_real_locked_journal_stops_before_moving_file(self):
        shell=shutil.which('pwsh') or shutil.which('powershell')
        if not shell:
            self.skipTest('PowerShell unavailable')
        source=self.put('input.txt')
        destination=self.root/'dest'
        destination.mkdir()
        with self.journal.locked():
            pass
        actions=self.put('_organize/actions.tsv',
            f'op\tsrc\tdst\treason\nmove\t{source}\t{destination}\ttest\n')
        quote=lambda p: "'"+str(p).replace("'","''")+"'"
        script=self.base/'lock.ps1'
        script.write_text(
            f'$lock=[IO.File]::Open({quote(self.journal.path)},[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)\n'+
            f'try {{ & {quote(PROJECT/"scripts/apply.ps1")} -Root {quote(self.root)} -Actions {quote(actions)} }} finally {{ $lock.Dispose() }}\n',
            encoding='utf-8-sig')
        result=subprocess.run([shell,'-NoProfile','-NonInteractive','-File',str(script)],
                              capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=30)
        self.assertTrue(source.exists(),result.stdout+result.stderr)
        self.assertFalse((destination/source.name).exists())
        self.assertFalse(executor.read_journal(self.journal.path))

    def test_repeated_sequence_undo_after_redo_is_idempotent(self):
        source=self.put('A.txt')
        target=self.root/'B.txt'
        self.move(source,target)
        self.assertEqual(self.undo('test-run'),0)
        undo_run=executor.read_journal(self.journal.path)[-1]['run_id']
        self.assertEqual(self.undo(undo_run),0)
        boundary=executor.read_journal(self.journal.path)[-1]['seq']
        for unused in range(3):
            with contextlib.redirect_stdout(io.StringIO()):
                code=executor.undo(Namespace(journal=str(self.journal.path),run_id=None,
                                             seq=boundary,last=False,list=False,dry_run=False))
            self.assertEqual(code,0)
            self.assertTrue(source.exists())
            self.assertFalse(target.exists())

    def test_interrupted_secret_marker_update_blocks_apply_until_recovered(self):
        source=self.put('private.txt')
        write=executor.atomic_write
        def fail_primary(path,data):
            if Path(path)==self.root/'_organize/markers.tsv':
                raise OSError('primary marker write failed')
            return write(path,data)
        args=Namespace(root=str(self.root),action='add',path=str(source),
                       kind='SECRET',by='user',note='test')
        with patch.object(executor,'atomic_write',side_effect=fail_primary):
            with self.assertRaises(OSError):
                executor.marker_command(args)
        self.assertTrue(self.journal.pending_marker_update())
        with self.assertRaises(SafetyError):
            with self.journal.locked():
                pass
        self.recover()
        self.assertFalse(self.journal.pending_marker_update())
        prepared,blocked=executor.preflight(self.root,[dict(op='rename',src=str(source),dst='changed.txt')])
        self.assertFalse(prepared)
        self.assertTrue(blocked)
        self.assertTrue(source.exists())

    def test_note_does_not_reveal_secret_in_marker_listing(self):
        source=self.put('secret.txt')
        self.put('_organize/markers.tsv', 'path\tkind\tby\tnote\tts\n'+
                 f'{source}\tSECRET\tuser\ttest\tx\n{source}\tNOTE\tuser\tcomment\tx\n')
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            executor.marker_command(Namespace(root=str(self.root),action='list'))
        self.assertNotIn(str(source),output.getvalue())

    def test_reference_migration_updates_marker_mirror(self):
        source=self.put('A/file.txt').parent
        target=self.root/'B'
        self.put('_organize/markers.tsv',f'path\tkind\tby\tnote\tts\n{source}\tNOTE\tuser\ttest\tx\n')
        self.move(source,target)
        self.assertEqual((self.root/'_organize/markers.tsv').read_bytes(),
                         (self.journal.mirror_dir/'markers.tsv').read_bytes())
        self.assertEqual(self.undo('test-run'),0)
        self.assertEqual((self.root/'_organize/markers.tsv').read_bytes(),
                         (self.journal.mirror_dir/'markers.tsv').read_bytes())

    def test_real_powershell_bridges(self):
        shells=[]
        for name in ('pwsh','powershell'):
            path=shutil.which(name)
            if path and path not in shells:
                shells.append(path)
        if not shells:
            self.skipTest('PowerShell unavailable')
        for index,shell in enumerate(shells):
            with self.subTest(shell=shell):
                root=self.root/str(index)
                root.mkdir()
                source=root/'输入[1].txt'
                source.write_text('hello')
                actions=self.base/f'actions{index}.tsv'
                actions.write_text(f'op\tsrc\tdst\treason\nrename\t{source}\t输出[1].txt\ttest\n',encoding='utf-8')
                def invoke(script,*args):
                    return subprocess.run([shell,'-NoProfile','-NonInteractive','-File',
                        str(PROJECT/'scripts'/script),*map(str,args)],capture_output=True,text=True,
                        encoding='utf-8',errors='replace',timeout=30)
                r=invoke('apply.ps1','-Root',root,'-Actions',actions)
                self.assertEqual(r.returncode,0,r.stdout+r.stderr)
                self.assertTrue((root/'输出[1].txt').exists())
                rid=executor.read_journal(root/'_organize/journal.tsv')[0]['run_id']
                r=invoke('undo.ps1','-Journal',root/'_organize/journal.tsv','-RunId',rid)
                self.assertEqual(r.returncode,0,r.stdout+r.stderr)
                self.assertTrue(source.exists())
                r=invoke('markers.ps1','-Root',root,'-Action','add','-Path',source,'-Kind','SECRET')
                self.assertEqual(r.returncode,0,r.stdout+r.stderr)
                self.assertNotIn(str(source),r.stdout)
                r=invoke('markers.ps1','-Root',root,'-Action','check','-Path',source)
                self.assertEqual(r.returncode,0,r.stdout+r.stderr)
                self.assertIn('SECRET',r.stdout)
                r=invoke('recover.ps1','-Root',root)
                self.assertEqual(r.returncode,0,r.stdout+r.stderr)
                helper=self.base/f'journal-helper{index}.ps1'
                quote=lambda p: "'"+str(p).replace("'","''")+"'"
                helper.write_text(
                    f'. {quote(PROJECT/"scripts/journal.ps1")}\n'+
                    f'$path={quote(root/"_organize/journal.tsv")}\n'+
                    '$null=Add-Journal -Path $path -RunId helper -Op note -Reason "helper test"\n'+
                    '$rows=@(Read-Journal -Path $path)\n'+
                    'if (@($rows | Where-Object { $_.op -eq "note" }).Count -ne 1) { throw "read mismatch" }\n'+
                    '$runs=@(Get-JournalRuns -Path $path)\n'+
                    'if (-not ($runs | Where-Object { $_.run_id -eq "helper" })) { throw "run missing" }\n',
                    encoding='utf-8-sig')
                r=subprocess.run([shell,'-NoProfile','-NonInteractive','-File',str(helper)],
                                  capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=30)
                self.assertEqual(r.returncode,0,r.stdout+r.stderr)


if __name__=='__main__':
    unittest.main(verbosity=2)
