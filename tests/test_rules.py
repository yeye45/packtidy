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
    base = {
        "version": 1,
        "minPacktidy": "0.1.0",
        "protected": ["windows"],
        "crossRules": [{"keys": ["番剧"], "to": ["影视"]}],
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
        ok, problems = rules.validate(minimal(version="1"))
        self.assertFalse(ok)

    def test_protected_must_be_string_list(self):
        ok, _ = rules.validate(minimal(protected=[1, 2]))
        self.assertFalse(ok)

    def test_cross_rules_shape(self):
        ok, _ = rules.validate(minimal(crossRules=[{"keys": ["a"]}]))
        self.assertFalse(ok)
        ok2, _ = rules.validate(minimal(crossRules=[{"keys": ["a"], "to": ["b"]}]))
        self.assertTrue(ok2)

    def test_new_categories_needs_keys_and_parent(self):
        ok, _ = rules.validate(minimal(newCategories={"x": {"keys": ["k"]}}))
        self.assertFalse(ok)
        ok2, _ = rules.validate(
            minimal(newCategories={"x": {"keys": ["k"], "parent": None}}))
        self.assertTrue(ok2)

    def test_underscore_fields_ignored(self):
        data = minimal(_comment="说明", _schema={})
        ok, problems = rules.validate(data)
        self.assertTrue(ok, problems)

    def test_unknown_field_reported_but_passes(self):
        ok, problems = rules.validate(minimal(unknownField=1))
        self.assertTrue(ok)   # 未知字段只警告不阻断
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
            self.assertEqual(data["version"], 1)

    def test_load_invalid_raises(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "r.json"
            p.write_text("{bad json", encoding="utf-8")
            with self.assertRaises(rules.RulesError):
                rules.load(p)

    def test_load_bad_schema_raises(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "r.json"
            p.write_text(json.dumps({"version": 1}), encoding="utf-8")
            with self.assertRaises(rules.RulesError):
                rules.load(p)


class FakeMod:
    pass


class TestApplyTo(unittest.TestCase):
    def test_applies_safety(self):
        safety = FakeMod()
        safety.PROTECTED_NAMES = set()
        safety.NESTED_ALLOW = set()
        safety.NESTED_DENY = set()
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

    def test_applies_decisions(self):
        dec = FakeMod()
        dec.CROSS_RULES = []
        dec.NEW_CATEGORIES = {}
        dec.CATEGORY_NODE_NAMES = set()
        data = minimal(
            crossRules=[{"keys": ["番剧"], "to": ["影视"]}],
            newCategories={"电子书": {"parent": "文档", "keys": ["epub"]}},
            categoryNodes=["影视"],
        )
        rules.apply_to(data, decisions_mod=dec)
        self.assertEqual(len(dec.CROSS_RULES), 1)
        self.assertEqual(dec.NEW_CATEGORIES["电子书"]["parent"], "文档")
        self.assertEqual(dec.CATEGORY_NODE_NAMES, {"影视"})

    def test_lowercases_names(self):
        safety = FakeMod()
        safety.PROTECTED_NAMES = set()
        safety.NESTED_ALLOW = set()
        safety.NESTED_DENY = set()
        rules.apply_to(minimal(protected=["WiNdOwS"]), safety_mod=safety)
        self.assertIn("windows", safety.PROTECTED_NAMES)


class TestDefaultPack(unittest.TestCase):
    """默认规则包本身要合法。"""

    def test_default_pack_valid(self):
        p = HERE.parent / "rules" / "default.json"
        if not p.is_file():
            self.skipTest("没有默认规则包")
        data = json.loads(p.read_text(encoding="utf-8-sig"))
        ok, problems = rules.validate(data)
        self.assertTrue(ok, problems)

    def test_default_pack_has_core_fields(self):
        p = HERE.parent / "rules" / "default.json"
        if not p.is_file():
            self.skipTest("没有默认规则包")
        data = json.loads(p.read_text(encoding="utf-8-sig"))
        for field in ("protected", "nestedAllow", "nestedDeny", "crossRules"):
            self.assertIn(field, data)
            self.assertTrue(len(data[field]) > 0, f"{field} 不能为空")


if __name__ == "__main__":
    unittest.main(verbosity=2)
