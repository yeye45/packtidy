#!/usr/bin/env python3
"""plan 子命令测试：从探针结果生成待批准动作。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORE = HERE.parent / "core"
sys.path.insert(0, str(CORE))

import executor  # noqa: E402


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
        with tempfile.TemporaryDirectory() as tmp:
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
        with tempfile.TemporaryDirectory() as tmp:
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
        with tempfile.TemporaryDirectory() as tmp:
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
        with tempfile.TemporaryDirectory() as tmp:
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
        with tempfile.TemporaryDirectory() as tmp:
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
        with tempfile.TemporaryDirectory() as tmp:
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
        with tempfile.TemporaryDirectory() as tmp:
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
