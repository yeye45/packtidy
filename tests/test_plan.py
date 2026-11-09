#!/usr/bin/env python3
"""plan 子命令测试：从探针结果生成待批准动作。"""
import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORE = HERE.parent / "core"
sys.path.insert(0, str(CORE))

import executor  # noqa: E402


@contextmanager
def project_tempdir():
    """在项目内建立测试根，避免把系统 TEMP/AppData 当整理根。"""
    with tempfile.TemporaryDirectory(dir=HERE.parent) as tmp:
        old_localappdata = os.environ.get("LOCALAPPDATA")
        localappdata = Path(tmp) / "localappdata"
        localappdata.mkdir()
        os.environ["LOCALAPPDATA"] = str(localappdata)
        try:
            yield tmp
        finally:
            if old_localappdata is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = old_localappdata


def setup_root(tmp):
    """造一个最小可用的盘上结构。"""
    root = Path(tmp) / "root"
    probe = root / "_organize" / "probe"
    probe.mkdir(parents=True)
    (root / "_organize" / "markers.tsv").write_text(
        "path\tkind\tby\tnote\tts\n", encoding="utf-8")
    return root, probe


def write_probe(probe, name, data):
    p = probe / name
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


class TestPlan(unittest.TestCase):
    def test_rename_generated(self):
        with project_tempdir() as tmp:
            root, probe = setup_root(tmp)
            src = root / "oldname"
            src.mkdir()
            write_probe(probe, "a.json", {
                "path": str(src), "action": "rename",
                "rename_to": "newname", "unit_why": "test"})
            args = type("A", (), {
                "root": str(root), "probe": None, "policy": None,
                "out": None, "dry_run": True, "quiet": True})()
            self.assertEqual(executor.plan(args), 0)

    def test_split_not_generated(self):
        """split 不该生成动作——必须先归组。"""
        with project_tempdir() as tmp:
            root, probe = setup_root(tmp)
            src = root / "container"
            src.mkdir()
            (src / "a.txt").write_text("x")
            write_probe(probe, "b.json", {
                "path": str(src), "action": "split", "unit_why": "test"})
            args = type("A", (), {
                "root": str(root), "probe": None, "policy": None,
                "out": str(root / "_organize" / "plan.tsv"),
                "dry_run": False, "quiet": True})()
            executor.plan(args)
            # 没有 rename/merge → 不产生文件
            self.assertFalse((root / "_organize" / "plan.tsv").exists())

    def test_merge_outside_zone_skipped(self):
        with project_tempdir() as tmp:
            root, probe = setup_root(tmp)
            src = root / "item"
            src.mkdir()
            target_dir = root / "untrusted_zone"
            target_dir.mkdir()
            write_probe(probe, "c.json", {
                "path": str(src), "action": "merge",
                "merge_target": str(target_dir), "unit_why": "test"})
            args = type("A", (), {
                "root": str(root), "probe": None, "policy": None,
                "out": str(root / "_organize" / "plan.tsv"),
                "dry_run": False, "quiet": True})()
            executor.plan(args)
            self.assertFalse((root / "_organize" / "plan.tsv").exists())

    def test_merge_inside_zone_generated(self):
        with project_tempdir() as tmp:
            root, probe = setup_root(tmp)
            src = root / "item"
            src.mkdir()
            zone = root / "trusted"
            zone.mkdir()
            policy = root / "policy.json"
            policy.write_text(json.dumps({
                "auto_approve": {"zones": ["trusted"]}}), encoding="utf-8")
            write_probe(probe, "d.json", {
                "path": str(src), "action": "merge",
                "merge_target": str(zone), "unit_why": "test"})
            out = root / "_organize" / "plan.tsv"
            args = type("A", (), {
                "root": str(root), "probe": None, "policy": str(policy),
                "out": str(out), "dry_run": False, "quiet": True})()
            executor.plan(args)
            self.assertTrue(out.exists())
            self.assertIn("move", out.read_text(encoding="utf-8"))

    def test_keep_and_ask_ignored(self):
        with project_tempdir() as tmp:
            root, probe = setup_root(tmp)
            src = root / "keepme"
            src.mkdir()
            write_probe(probe, "e.json", {
                "path": str(src), "action": "keep", "unit_why": "test"})
            write_probe(probe, "f.json", {
                "path": str(src), "action": "ask", "unit_why": "test"})
            out = root / "_organize" / "plan.tsv"
            args = type("A", (), {
                "root": str(root), "probe": None, "policy": None,
                "out": str(out), "dry_run": False, "quiet": True})()
            executor.plan(args)
            self.assertFalse(out.exists())

    def test_missing_source_skipped(self):
        """探针结果指向已不存在的路径，不该生成动作。"""
        with project_tempdir() as tmp:
            root, probe = setup_root(tmp)
            write_probe(probe, "g.json", {
                "path": str(root / "gone"), "action": "rename",
                "rename_to": "newname", "unit_why": "test"})
            out = root / "_organize" / "plan.tsv"
            args = type("A", (), {
                "root": str(root), "probe": None, "policy": None,
                "out": str(out), "dry_run": False, "quiet": True})()
            executor.plan(args)
            self.assertFalse(out.exists())

    def test_units_and_group_files_ignored(self):
        """units_*/group_*/dup_* 不是探针判定，不该生成动作。"""
        with project_tempdir() as tmp:
            root, probe = setup_root(tmp)
            src = root / "x"
            src.mkdir()
            for prefix in ("units_", "group_", "dup_"):
                write_probe(probe, f"{prefix}test.json", {
                    "path": str(src), "action": "rename",
                    "rename_to": "n", "unit_why": "test"})
            out = root / "_organize" / "plan.tsv"
            args = type("A", (), {
                "root": str(root), "probe": None, "policy": None,
                "out": str(out), "dry_run": False, "quiet": True})()
            executor.plan(args)
            self.assertFalse(out.exists())

    def test_empty_recalculation_replaces_old_tsv(self):
        with project_tempdir() as tmp:
            root, probe = setup_root(tmp)
            src = root / "keepme"
            src.mkdir()
            write_probe(probe, "keep.json", {
                "path": str(src), "action": "keep", "unit_why": "test"})
            out = root / "_organize" / "plan.tsv"
            out.write_text("stale contents\nold action\n", encoding="utf-8")
            args = type("A", (), {
                "root": str(root), "probe": None, "policy": None,
                "out": str(out), "dry_run": False, "quiet": True})()

            executor.plan(args)

            self.assertEqual(out.read_text(encoding="utf-8"),
                             "op\tsrc\tdst\treason\n")

    def test_reads_root_policy_with_legacy_top_level_zones(self):
        with project_tempdir() as tmp:
            root, probe = setup_root(tmp)
            src = root / "item"
            src.mkdir()
            zone = root / "trusted"
            zone.mkdir()
            policy_path = root / "_organize" / "policy.json"
            policy_path.write_text(json.dumps({"zones": ["trusted"]}),
                                   encoding="utf-8")
            write_probe(probe, "merge.json", {
                "path": str(src), "action": "merge",
                "merge_target": str(zone), "unit_why": "test"})
            out = root / "_organize" / "plan.tsv"
            args = type("A", (), {
                "root": str(root), "probe": None, "policy": None,
                "out": str(out), "dry_run": False, "quiet": True})()

            executor.plan(args)

            self.assertIn("move\t", out.read_text(encoding="utf-8"))

    def test_auto_approval_switch_and_size_limit(self):
        with project_tempdir() as tmp:
            root, probe = setup_root(tmp)
            disabled = root / "rename-disabled"
            oversized = root / "over-limit"
            disabled.mkdir()
            oversized.mkdir()
            (disabled / "payload.bin").write_bytes(b"x")
            (oversized / "payload.bin").write_bytes(b"x")
            policy_path = root / "_organize" / "policy.json"
            policy_path.write_text(json.dumps({"auto_approve": {
                "rename": False, "merge_to_known_zone": False,
                "zones": ["trusted"], "max_gb": 0}}), encoding="utf-8")
            zone = root / "trusted"
            zone.mkdir()
            write_probe(probe, "disabled.json", {
                "path": str(disabled), "action": "rename",
                "rename_to": "renamed", "unit_why": "test"})
            write_probe(probe, "large.json", {
                "path": str(oversized), "action": "rename",
                "rename_to": "renamed", "unit_why": "test"})
            write_probe(probe, "merge.json", {
                "path": str(disabled), "action": "merge",
                "merge_target": str(zone), "unit_why": "test"})
            out = root / "_organize" / "plan.tsv"
            args = type("A", (), {
                "root": str(root), "probe": None, "policy": None,
                "out": str(out), "dry_run": False, "quiet": True})()

            executor.plan(args)

            self.assertFalse(out.exists())

            # 开启改名后，max_gb=0 仍应阻止含有非空文件的对象。
            policy_path.write_text(json.dumps({"auto_approve": {
                "rename": True, "max_gb": 0}}), encoding="utf-8")
            write_probe(probe, "disabled.json", {
                "path": str(oversized), "action": "rename",
                "rename_to": "renamed", "unit_why": "test"})
            executor.plan(args)
            self.assertFalse(out.exists())

    def test_secret_path_never_enters_plan_or_output(self):
        with project_tempdir() as tmp:
            root, probe = setup_root(tmp)
            secret = root / "vault"
            secret.mkdir()
            src = secret / "credentials"
            src.mkdir()
            (root / "_organize" / "markers.tsv").write_text(
                "path\tkind\tby\tnote\tts\n"
                f"{secret}\tSECRET\ttest\tprivate\tnow\n",
                encoding="utf-8")
            write_probe(probe, "secret.json", {
                "path": str(src), "action": "rename",
                "rename_to": "renamed", "unit_why": "test"})
            out = root / "_organize" / "plan.tsv"
            args = type("A", (), {
                "root": str(root), "probe": None, "policy": None,
                "out": str(out), "dry_run": False, "quiet": False})()
            from io import StringIO
            from contextlib import redirect_stdout
            captured = StringIO()

            with redirect_stdout(captured):
                executor.plan(args)

            self.assertFalse(out.exists())
            self.assertNotIn(str(secret), captured.getvalue())

    def test_explicit_policy_adds_exclusions_and_probe_notes_stay_private(self):
        with project_tempdir() as tmp:
            root, probe = setup_root(tmp)
            secret, local, extra, public = (root/name for name in ('vault', 'local', 'extra', 'public'))
            for directory in (secret, local, extra, public):
                directory.mkdir()
            (root/'_organize/markers.tsv').write_text(
                f'path\tkind\tby\tnote\tts\n{secret}\tSECRET\ttest\tprivate\tnow\n', encoding='utf-8')
            (root/'_organize/policy.json').write_text('{"no_go_zones":["local"]}', encoding='utf-8')
            external = Path(tmp)/'strict.json'
            external.write_text('{"no_go_zones":["extra"]}', encoding='utf-8')
            for source in (local, extra, public):
                write_probe(probe, source.name + '.json', {
                    'path': str(source), 'action': 'rename', 'rename_to': 'renamed',
                    'unit_why': f'Compared against {secret}'})
            out = root/'_organize/plan.tsv'
            args = type('A', (), {'root': str(root), 'probe': None, 'policy': str(external),
                                  'out': str(out), 'dry_run': False, 'quiet': True})()
            executor.plan(args)
            rows = executor.read_tsv(out, ('op','src','dst','reason'))
            self.assertEqual([executor.key(row['src']) for row in rows], [executor.key(public)])
            self.assertNotIn(str(secret), rows[0]['reason'])


if __name__ == "__main__":
    unittest.main(verbosity=2)
