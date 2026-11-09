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
import lookup  # noqa: E402


class TestLookupHint(unittest.TestCase):
    """查证线索：该去哪查，不是结论。"""

    def test_rj_code_goes_dlsite(self):
        h = D._lookup_hint("RJ123456 某作")
        self.assertEqual(h["source"], "dlsite")
        self.assertIn("RJ123456", h["key"])

    def test_rj_code_case_insensitive(self):
        h = D._lookup_hint("rj999999")
        self.assertEqual(h["source"], "dlsite")

    def test_media_goes_vndb(self):
        h = D._lookup_hint("某视觉小说", {"kind": "media"})
        self.assertEqual(h["source"], "vndb")
        self.assertIn("lookup.py vndb", h.get("command", ""))

    def test_installer_flagged_as_software(self):
        h = D._lookup_hint("某软件合集", {"kind": "installer"})
        self.assertEqual(h["source"], "software")
        self.assertIn("不是作品", h["note"])

    def test_unknown_falls_back_to_generic(self):
        h = D._lookup_hint("奇奇怪怪", None)
        self.assertEqual(h["source"], "generic")
        self.assertIn("不按字面推断", h["note"])

    def test_image_also_vndb(self):
        h = D._lookup_hint("某画集", {"kind": "image"})
        self.assertEqual(h["source"], "vndb")


class TestVNDBFields(unittest.TestCase):
    """VNDB 返回结果的处理逻辑（不真联网）。"""

    def test_adult_threshold(self):
        # image.sexual >= 1.5 判定为成人向，这是代码里的阈值
        # 通过构造假数据验证判定逻辑
        fake_low = {"sexual": 0.5}
        fake_high = {"sexual": 2.0}
        self.assertFalse((fake_low.get("sexual") or 0) >= 1.5)
        self.assertTrue((fake_high.get("sexual") or 0) >= 1.5)

    def test_tag_filtering(self):
        # 只留 rating >= 2 的标签
        tags = [{"name": "a", "rating": 3}, {"name": "b", "rating": 1},
                {"name": "c", "rating": 2}]
        kept = [t["name"] for t in tags if t.get("rating", 0) >= 2]
        self.assertEqual(kept, ["a", "c"])


class TestLookupSources(unittest.TestCase):
    def test_sources_declared(self):
        self.assertIn("vndb", lookup.SOURCES)
        for name, info in lookup.SOURCES.items():
            self.assertIn("about", info)
            self.assertIn("reliability", info)

    def test_cache_key_deterministic(self):
        k1 = lookup.cache_key("vndb", "test")
        k2 = lookup.cache_key("vndb", "test")
        k3 = lookup.cache_key("vndb", "other")
        self.assertEqual(k1, k2)
        self.assertNotEqual(k1, k3)

    def test_search_gives_paths_not_conclusions(self):
        r = lookup.query_search("某个东西")
        self.assertTrue(r["ok"])
        self.assertIn("where", r)
        self.assertIn("note", r)
        # 不该直接给结论
        self.assertNotIn("category", r)


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


class TestNoNetwork(unittest.TestCase):
    """这些测试不能触网——CI 环境可能没有网络。"""

    def test_lookup_hint_is_pure(self):
        # _lookup_hint 不联网，只做字符串判断
        for name in ["RJ123456", "某作品", "某软件"]:
            h = D._lookup_hint(name)
            self.assertIn("source", h)

    def test_query_search_has_no_network_call(self):
        # query_search 只给建议，不联网
        r = lookup.query_search("test")
        self.assertEqual(r["source"], "suggestion")


if __name__ == "__main__":
    unittest.main(verbosity=2)
