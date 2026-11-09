#!/usr/bin/env python3
"""规则包加载与校验的测试。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORE = HERE.parent / "core"
sys.path.insert(0, str(CORE))

import rules  # noqa: E402


def minimal(**over):
    """v2 最小合法结构。"""
    base = {
        "version": 2,
        "minPacktidy": "0.1.0",
        "protected": ["windows"],
        "categories": {
            "video": {"label": "视频", "keys": ["电影", "影片"],
                      "suggest": ["影视"]},
        },
    }
    base.update(over)
    return base


class TestValidate(unittest.TestCase):
    def test_minimal_ok(self):
        ok, problems = rules.validate(minimal())
        self.assertTrue(ok, problems)

    def test_missing_required(self):
        data = minimal()
        del data["protected"]
        ok, problems = rules.validate(data)
        self.assertFalse(ok)
        self.assertTrue(any("protected" in p for p in problems))

    def test_version_must_be_int(self):
        ok, _ = rules.validate(minimal(version="2"))
        self.assertFalse(ok)
        self.assertFalse(rules.validate(minimal(version=True))[0])

    def test_protected_must_be_string_list(self):
        ok, _ = rules.validate(minimal(protected=[1, 2]))
        self.assertFalse(ok)

    def test_categories_shape(self):
        ok, _ = rules.validate(minimal(categories={"a": {"label": "x"}}))
        self.assertFalse(ok)   # 缺 keys
        ok2, _ = rules.validate(
            minimal(categories={"a": {"keys": ["k"]}}))
        self.assertTrue(ok2)   # label/suggest 可选

    def test_suggest_must_be_list(self):
        ok, _ = rules.validate(
            minimal(categories={"a": {"keys": ["k"], "suggest": "x"}}))
        self.assertFalse(ok)

    def test_category_keys_and_suggest_elements_must_be_strings(self):
        self.assertFalse(rules.validate(minimal(categories={"a": {"keys": [1]}}))[0])
        self.assertFalse(rules.validate(minimal(categories={"a": {"keys": ["x"], "suggest": [None]}}))[0])

    def test_mapping_target_null_allowed(self):
        ok, _ = rules.validate(
            minimal(mapping={"video": None}))
        self.assertTrue(ok)

    def test_mapping_target_string_allowed(self):
        ok, _ = rules.validate(
            minimal(mapping={"video": "影视"}))
        self.assertTrue(ok)

    def test_mapping_unknown_category_warns(self):
        ok, problems = rules.validate(
            minimal(mapping={"nonexistent": "某目录"}))
        self.assertTrue(ok)   # 只警告，不阻断
        self.assertTrue(any("类别不存在" in p for p in problems))

    def test_underscore_fields_ignored(self):
        data = minimal(_comment="说明", _schema={})
        ok, problems = rules.validate(data)
        self.assertTrue(ok, problems)

    def test_unknown_field_reported_but_passes(self):
        ok, problems = rules.validate(minimal(unknownField=1))
        self.assertTrue(ok)   # 未知字段只警告不阻断（前向兼容）
        self.assertTrue(any("未知字段" in p for p in problems))


class TestLoad(unittest.TestCase):
    def test_load_missing_returns_marker(self):
        data = rules.load("/nonexistent/rules.json")
        self.assertTrue(data.get("_missing"))

    def test_load_valid(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "r.json"
            p.write_text(json.dumps(minimal()), encoding="utf-8")
            data = rules.load(p)
            self.assertEqual(data["version"], 2)

    def test_load_invalid_json_raises(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "r.json"
            p.write_text("{bad json", encoding="utf-8")
            with self.assertRaises(rules.RulesError):
                rules.load(p)

    def test_load_bad_schema_raises(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "r.json"
            p.write_text(json.dumps({"version": 2}), encoding="utf-8")
            with self.assertRaises(rules.RulesError):
                rules.load(p)

    def test_load_uses_dynamic_cache(self):
        with tempfile.TemporaryDirectory() as d:
            old = os.environ.get("LOCALAPPDATA")
            try:
                os.environ["LOCALAPPDATA"] = d
                p = Path(d) / "packtidy" / "rules" / "remote.json"
                p.parent.mkdir(parents=True)
                p.write_text(json.dumps(minimal(version=7)), encoding="utf-8")
                self.assertEqual(rules.load()["version"], 7)
            finally:
                if old is None:
                    os.environ.pop("LOCALAPPDATA", None)
                else:
                    os.environ["LOCALAPPDATA"] = old

    def test_root_policy_relative_rules_path(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            org = root / "_organize"
            org.mkdir()
            (org / "custom.json").write_text(json.dumps(minimal(version=8)), encoding="utf-8")
            (org / "policy.json").write_text(json.dumps({"rules": "custom.json"}), encoding="utf-8")
            self.assertEqual(rules.load_for_root(root)["version"], 8)

    def test_root_policy_missing_target_is_error(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            org = root / "_organize"
            org.mkdir()
            (org / "policy.json").write_text(json.dumps({"rules": "missing.json"}), encoding="utf-8")
            with self.assertRaises(rules.RulesError):
                rules.load_for_root(root)

    def test_corrupt_root_policy_is_error(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            org = root / "_organize"
            org.mkdir()
            (org / "policy.json").write_text("{bad", encoding="utf-8")
            with self.assertRaises(rules.RulesError):
                rules.load_root_policy(root)


class FakeMod:
    pass


def new_safety():
    m = FakeMod()
    m.PROTECTED_NAMES = set()
    m.NESTED_ALLOW = set()
    m.NESTED_DENY = set()
    return m


def new_decisions():
    m = FakeMod()
    m.CROSS_RULES = []
    m.NEW_CATEGORIES = {}
    m.CATEGORY_NODE_NAMES = set()
    return m


class TestApplyTo(unittest.TestCase):
    def test_applies_safety(self):
        safety = new_safety()
        data = minimal(
            protected=["Windows", "Program Files"],
            nestedAllow=["Downloads"],
            nestedDeny=["node_modules"],
        )
        changed = rules.apply_to(data, safety_mod=safety)
        self.assertEqual(safety.PROTECTED_NAMES, {"windows", "program files"})
        self.assertEqual(safety.NESTED_ALLOW, {"downloads"})
        self.assertEqual(safety.NESTED_DENY, {"node_modules"})
        self.assertEqual(len(changed), 3)

    def test_v2_categories_to_cross_rules(self):
        dec = new_decisions()
        data = minimal(
            categories={
                "video": {"keys": ["电影", "影片"], "suggest": ["影视"]},
                "game": {"keys": ["游戏"], "suggest": ["游戏"]},
            },
            mapping={"video": "我的影视区", "game": None},
        )
        rules.apply_to(data, decisions_mod=dec)
        self.assertEqual(len(dec.CROSS_RULES), 2)
        # 有映射的用映射名
        video = [r for r in dec.CROSS_RULES if "电影" in r["keys"]][0]
        self.assertEqual(video["to"], ["我的影视区"])
        # 没映射的退回 suggest
        game = [r for r in dec.CROSS_RULES if "游戏" in r["keys"]][0]
        self.assertEqual(game["to"], ["游戏"])

    def test_v2_mapping_feeds_category_nodes(self):
        dec = new_decisions()
        data = minimal(mapping={"video": "影视"})
        rules.apply_to(data, decisions_mod=dec)
        self.assertIn("影视", dec.CATEGORY_NODE_NAMES)

    def test_v1_backward_compatible(self):
        dec = new_decisions()
        old = {
            "version": 1,
            "minPacktidy": "0.1.0",
            "protected": [],
            "crossRules": [{"keys": ["番剧"], "to": ["影视"]}],
            "newCategories": {"电子书": {"parent": "文档", "keys": ["epub"]}},
            "categoryNodes": ["影视"],
        }
        rules.apply_to(old, decisions_mod=dec)
        self.assertEqual(len(dec.CROSS_RULES), 1)
        self.assertEqual(dec.NEW_CATEGORIES["电子书"]["parent"], "文档")
        self.assertEqual(dec.CATEGORY_NODE_NAMES, {"影视"})

    def test_lowercases_names(self):
        safety = new_safety()
        rules.apply_to(minimal(protected=["WiNdOwS"]), safety_mod=safety)
        self.assertIn("windows", safety.PROTECTED_NAMES)


class TestDefaultPack(unittest.TestCase):
    """默认规则包本身要合法，且不含任何具体分类名。"""

    @classmethod
    def setUpClass(cls):
        p = HERE.parent / "rules" / "default.json"
        if not p.is_file():
            raise unittest.SkipTest("没有默认规则包")
        cls.data = json.loads(p.read_text(encoding="utf-8-sig"))

    def test_valid(self):
        ok, problems = rules.validate(self.data)
        self.assertTrue(ok, problems)

    def test_has_core_sections(self):
        for field in ("protected", "nestedAllow", "nestedDeny", "categories"):
            self.assertIn(field, self.data)
            self.assertTrue(len(self.data[field]) > 0, f"{field} 不能为空")

    def test_mapping_all_unset(self):
        """默认规则包不预设任何本地目录名。"""
        mapping = self.data.get("mapping", {})
        for k, v in mapping.items():
            if k.startswith("_"):
                continue
            self.assertIsNone(
                v, f"mapping.{k} 应为 null——默认包不该指定具体目录名")

    def test_every_category_has_keys(self):
        for name, info in self.data["categories"].items():
            if name.startswith("_"):
                continue
            self.assertIn("keys", info, f"{name} 缺 keys")
            self.assertTrue(len(info["keys"]) > 0, f"{name} 的 keys 为空")


if __name__ == "__main__":
    unittest.main(verbosity=2)
