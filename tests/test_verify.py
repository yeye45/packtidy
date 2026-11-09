#!/usr/bin/env python3
"""验证机制与查询能力的测试。不依赖网络。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORE = HERE.parent / "core"
sys.path.insert(0, str(CORE))

import decisions as D  # noqa: E402


class TestVerifyCases(unittest.TestCase):
    """verify.py 的用例执行逻辑。"""

    def setUp(self):
        import verify
        self.V = verify

    def test_stem_case(self):
        case = {"kind": "stem", "name": "编程课程合集", "expect": "编程课程"}
        ok, msg = self.V.run_case(case, D)
        self.assertTrue(ok, msg)

    def test_shape_case(self):
        with tempfile.TemporaryDirectory() as d:
            for i in range(8):
                (Path(d) / f"x{i}.exe").write_text("x", encoding="utf-8")
            case = {"kind": "shape", "path": d, "expect": "installer"}
            ok, msg = self.V.run_case(case, D)
            self.assertTrue(ok, msg)

    def test_shape_case_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            for i in range(8):
                (Path(d) / f"x{i}.mp4").write_text("x", encoding="utf-8")
            case = {"kind": "shape", "path": d, "expect": "installer"}
            ok, msg = self.V.run_case(case, D)
            self.assertFalse(ok)

    def test_missing_path_skipped(self):
        case = {"kind": "shape", "path": "/nonexistent/xyz", "expect": "x"}
        ok, msg = self.V.run_case(case, D)
        self.assertIsNone(ok)

    def test_suggest_case_mode(self):
        case = {"kind": "suggest", "group": "随便", "parent": "父",
                "expectMode": "keep"}
        ok, msg = self.V.run_case(case, D)
        self.assertTrue(ok, msg)

    def test_suggest_target_assertion_catches_wrong_destination(self):
        case = {"kind":"suggest", "group":"ComfyUI 便携运行环境", "parent":"下载",
                "zones":[{"path":"/ai", "label":"AI工具", "depth":1, "files":1}],
                "expectMode":"move", "expectTarget":"/wrong"}
        self.assertFalse(self.V.run_case(case, D)[0])
        case["expectTarget"] = "/ai"
        self.assertTrue(self.V.run_case(case, D)[0])

    def test_suggest_without_assertions_fails(self):
        self.assertFalse(self.V.run_case({"kind":"suggest", "group":"随便"}, D)[0])

    def test_unknown_kind_skipped(self):
        case = {"kind": "nonsense"}
        ok, msg = self.V.run_case(case, D)
        self.assertIsNone(ok)

    def test_load_cases_from_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.json"
            p.write_text(json.dumps({"cases": [
                {"kind": "stem", "name": "a合集", "expect": "a"}]}),
                encoding="utf-8")
            cases = self.V.load_cases(p)
            self.assertEqual(len(cases), 1)
            self.assertIn("_source", cases[0])

    def test_load_cases_skips_bad_json(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "good.json").write_text(
                json.dumps({"cases": [{"kind": "stem", "name": "x"}]}),
                encoding="utf-8")
            (Path(d) / "bad.json").write_text("{bad", encoding="utf-8")
            cases = self.V.load_cases(d)
            self.assertEqual(len(cases), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
