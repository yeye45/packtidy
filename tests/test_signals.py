#!/usr/bin/env python3
"""signals.py 的单元测试。用合成布局，不依赖真实盘。"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "core"))

import signals  # noqa: E402


def mk(base, name, content=b"x"):
    p = os.path.join(base, name)
    os.makedirs(os.path.dirname(p), exist_ok=True) if os.path.dirname(p) else None
    with open(p, "wb") as f:
        f.write(content)
    return p


class TestUnity(unittest.TestCase):
    def test_unity_layout(self):
        with tempfile.TemporaryDirectory() as d:
            mk(d, "game.exe", b"MZ")
            os.makedirs(os.path.join(d, "game_Data"))
            r = signals.analyze(d)
            self.assertTrue(r["signals"]["unity-layout"])
            self.assertTrue(len(r["signals"]["unity-evidence"]) > 0)

    def test_exe_without_data(self):
        with tempfile.TemporaryDirectory() as d:
            mk(d, "setup.exe", b"MZ")
            r = signals.analyze(d)
            self.assertFalse(r["signals"]["unity-layout"])


class TestVM(unittest.TestCase):
    def test_vm_disks(self):
        with tempfile.TemporaryDirectory() as d:
            mk(d, "disk-s001.vmdk", b"KDMV")
            mk(d, "disk-s002.vmdk", b"KDMV")
            mk(d, "vm.vmx", b"config")
            mk(d, "vm.vmsd", b"snapshot")
            r = signals.analyze(d)
            self.assertTrue(r["signals"]["vm-layout"])
            self.assertEqual(r["signals"]["vm-disk-count"], 2)


class TestSequence(unittest.TestCase):
    def test_continuous(self):
        with tempfile.TemporaryDirectory() as d:
            for i in range(1, 13):
                mk(d, f"[Group] Show - {i:02d} [1080p].mkv")
            r = signals.analyze(d)
            self.assertTrue(r["signals"]["sequence-detected"])
            seq = r["signals"]["sequence"]
            self.assertEqual(seq["count"], 12)
            self.assertEqual(seq["missing"], [])
            self.assertGreaterEqual(seq["coverage"], 0.7)
            self.assertEqual(seq["range"], '1-12')
            self.assertLessEqual(seq["coverage"], 1)

    def test_gap(self):
        with tempfile.TemporaryDirectory() as d:
            for i in [1, 2, 3, 7, 8, 9, 10]:
                mk(d, f"ep{i:02d}.mp4")
            r = signals.analyze(d)
            self.assertTrue(r["signals"]["sequence-detected"])
            self.assertIn(4, r["signals"]["sequence"]["missing"])

    def test_too_few(self):
        with tempfile.TemporaryDirectory() as d:
            mk(d, "ep01.mp4")
            mk(d, "ep02.mp4")
            r = signals.analyze(d)
            self.assertFalse(r["signals"]["sequence-detected"])


class TestVolumeParts(unittest.TestCase):
    def test_complete(self):
        with tempfile.TemporaryDirectory() as d:
            for i in range(1, 4):
                mk(d, f"data.part{i}.rar", b"Rar!")
            r = signals.analyze(d)
            self.assertTrue(r["signals"]["volume-parts"])
            self.assertEqual(r["signals"]["volume-groups"][0]["missing"], [])

    def test_missing_part(self):
        with tempfile.TemporaryDirectory() as d:
            mk(d, "data.part1.rar")
            mk(d, "data.part3.rar")
            r = signals.analyze(d)
            self.assertTrue(r["signals"]["volume-parts"])
            self.assertIn(2, r["signals"]["volume-groups"][0]["missing"])


class TestShell(unittest.TestCase):
    def test_nested_same_name(self):
        with tempfile.TemporaryDirectory() as d:
            inner = os.path.join(d, "Game", "Game")
            os.makedirs(inner)
            mk(inner, "x.exe")
            r = signals.analyze(os.path.join(d, "Game"))
            self.assertTrue(r["signals"]["is-shell"])


class TestBatchArrival(unittest.TestCase):
    def test_same_second(self):
        with tempfile.TemporaryDirectory() as d:
            for i in range(5):
                mk(d, f"f{i}.txt")
            # 同秒创建在真实文件系统上通常成立
            r = signals.analyze(d)
            self.assertIn("batch-ratio", r["signals"])

    def test_few_items(self):
        with tempfile.TemporaryDirectory() as d:
            mk(d, "a.txt")
            mk(d, "b.txt")
            r = signals.analyze(d)
            self.assertFalse(r["signals"]["batch-arrival"])


class TestHidden(unittest.TestCase):
    def test_hidden_visible_to_scandir(self):
        """Hidden 属性不该让项消失。"""
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "hidden_dir")
            os.makedirs(p)
            try:
                os.system(f'attrib +h "{p}"')
            except Exception:
                pass
            r = signals.analyze(d)
            self.assertEqual(r["signals"]["dir-count"], 1)


class TestComposition(unittest.TestCase):
    def test_single_file(self):
        with tempfile.TemporaryDirectory() as d:
            mk(d, "a.txt")
            r = signals.analyze(d)
            self.assertEqual(r["signals"]["file-count"], 1)

    def test_mixed(self):
        with tempfile.TemporaryDirectory() as d:
            mk(d, "a.txt")
            mk(d, "b.jpg")
            mk(d, "c.mp4")
            mk(d, "d.zip")
            r = signals.analyze(d)
            self.assertGreaterEqual(r["signals"]["kind-count"], 4)


class TestEmpty(unittest.TestCase):
    def test_empty(self):
        with tempfile.TemporaryDirectory() as d:
            r = signals.analyze(d)
            self.assertTrue(r["empty"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
