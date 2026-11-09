#!/usr/bin/env python3
"""内容形态检测与新类别发现的测试。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORE = HERE.parent / "core"
sys.path.insert(0, str(CORE))

import decisions as D  # noqa: E402


def make_dir(root, exts, count=1):
    """在 root 下造 count 个带指定扩展名的文件。"""
    for i, e in enumerate(exts):
        p = Path(root) / f"f{i}{e}"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")


class TestContentShape(unittest.TestCase):
    def test_installer(self):
        with tempfile.TemporaryDirectory() as d:
            make_dir(d, [".exe"] * 8 + [".txt"])
            r = D.content_shape(d)
            self.assertEqual(r["kind"], "installer")
            self.assertGreaterEqual(r["shares"]["installer"], 0.5)

    def test_media(self):
        with tempfile.TemporaryDirectory() as d:
            make_dir(d, [".mp4"] * 8 + [".jpg"])
            r = D.content_shape(d)
            self.assertEqual(r["kind"], "media")

    def test_audio_counts_as_media(self):
        with tempfile.TemporaryDirectory() as d:
            make_dir(d, [".mp3"] * 7 + [".flac"] * 3)
            r = D.content_shape(d)
            self.assertEqual(r["kind"], "media")

    def test_image(self):
        with tempfile.TemporaryDirectory() as d:
            make_dir(d, [".jpg"] * 10)
            r = D.content_shape(d)
            self.assertEqual(r["kind"], "image")

    def test_code(self):
        with tempfile.TemporaryDirectory() as d:
            make_dir(d, [".py"] * 6 + [".md"])
            r = D.content_shape(d)
            self.assertEqual(r["kind"], "code")

    def test_document(self):
        with tempfile.TemporaryDirectory() as d:
            make_dir(d, [".pdf"] * 6 + [".docx"] * 4)
            r = D.content_shape(d)
            self.assertEqual(r["kind"], "document")

    def test_archive(self):
        with tempfile.TemporaryDirectory() as d:
            make_dir(d, [".zip"] * 6 + [".rar"] * 4)
            r = D.content_shape(d)
            self.assertEqual(r["kind"], "archive")

    def test_mixed(self):
        with tempfile.TemporaryDirectory() as d:
            make_dir(d, [".exe", ".jpg", ".txt", ".mp4", ".py"])
            r = D.content_shape(d)
            self.assertEqual(r["kind"], "mixed")

    def test_empty(self):
        with tempfile.TemporaryDirectory() as d:
            r = D.content_shape(d)
            self.assertEqual(r["kind"], "empty")
            self.assertEqual(r["total"], 0)

    def test_missing_dir(self):
        r = D.content_shape("/nonexistent/path/xyz")
        # 不抛异常，返回 total=0 或 None
        self.assertTrue(r is None or r["total"] == 0)

    def test_case_insensitive(self):
        with tempfile.TemporaryDirectory() as d:
            make_dir(d, [".EXE"] * 6 + [".Exe"] * 4)
            r = D.content_shape(d)
            self.assertEqual(r["kind"], "installer")

    def test_nested_dirs_counted(self):
        with tempfile.TemporaryDirectory() as d:
            sub = Path(d) / "sub" / "deep"
            sub.mkdir(parents=True)
            for i in range(8):
                (sub / f"x{i}.exe").write_text("x", encoding="utf-8")
            r = D.content_shape(d)
            self.assertEqual(r["kind"], "installer")
            self.assertEqual(r["total"], 8)


class TestNameStem(unittest.TestCase):
    def test_strips_brackets(self):
        self.assertNotIn("（", D.name_stem("某物（说明）"))

    def test_strips_generic_suffix(self):
        self.assertEqual(D.name_stem("编程课程合集"), "编程课程")
        self.assertEqual(D.name_stem("声库打包"), "声库")

    def test_keeps_space_words(self):
        # "4K 壁纸" 不该被空格切断
        self.assertEqual(D.name_stem("4K 壁纸打包"), "4K 壁纸")

    def test_splits_on_conjunction(self):
        stem = D.name_stem("VOCALOID 编辑器与声库")
        self.assertTrue(stem.startswith("VOCALOID"))

    def test_strips_trailing_number(self):
        self.assertEqual(D.name_stem("某物2023"), "某物")

    def test_empty_input(self):
        self.assertEqual(D.name_stem(""), "")

    def test_only_punctuation(self):
        self.assertEqual(D.name_stem("---"), "")


class TestPropose(unittest.TestCase):
    def test_same_stem_proposes(self):
        u = [("VOCALOID 编辑器与声库", "installer"),
             ("VOCALOID 声库合集", "installer")]
        r = D.propose_new_categories(u)
        self.assertTrue(any("VOCALOID" in k for k in r), r)

    def test_same_shape_fallback(self):
        u = [("声库A合集", "installer"),
             ("声库B合集", "installer"),
             ("声库C合集", "installer")]
        r = D.propose_new_categories(u)
        self.assertTrue(r, "形态一致应能聚类")

    def test_single_group_not_proposed(self):
        u = [("孤零零的一个", "installer")]
        r = D.propose_new_categories(u)
        self.assertEqual(r, {}, "孤件不建类")

    def test_two_groups_can_propose(self):
        u = [("声库A", "installer"), ("声库B", "installer")]
        r = D.propose_new_categories(u)
        self.assertTrue(r)

    def test_custom_threshold(self):
        u = [("声库A", "installer"), ("声库B", "installer")]
        r = D.propose_new_categories(u, min_groups=3)
        self.assertEqual(r, {})

    def test_no_kind_still_works_by_stem(self):
        u = [("VOCALOID 编辑器", None), ("VOCALOID 声库", None)]
        r = D.propose_new_categories(u)
        self.assertTrue(any("VOCALOID" in k for k in r))

    def test_members_recorded(self):
        u = [("声库A合集", "installer"), ("声库B合集", "installer")]
        r = D.propose_new_categories(u)
        for info in r.values():
            self.assertTrue(info["members"])
            self.assertIn("count", info)
            self.assertIn("basis", info)


class TestCommonPrefix(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(D._common_prefix(["声库A", "声库B"]), "声库")

    def test_no_common(self):
        self.assertEqual(D._common_prefix(["abc", "xyz"]), "")

    def test_single(self):
        self.assertEqual(D._common_prefix(["abc"]), "abc")

    def test_strips_punctuation(self):
        self.assertEqual(D._common_prefix(["A-B", "A_C"]), "A")

    def test_empty(self):
        self.assertEqual(D._common_prefix([]), "")


class TestSuggestConflicts(unittest.TestCase):
    """内容形态与名字冲突时必须标 needsCheck。"""

    @classmethod
    def setUpClass(cls):
        """加载默认规则包——suggest_group 依赖 CROSS_RULES，
        测试环境默认是空的，不加载就测不到名字匹配路径。"""
        import json
        from pathlib import Path as _P
        pack = _P(__file__).resolve().parent.parent / "rules" / "default.json"
        if pack.is_file():
            import rules
            data = json.loads(pack.read_text(encoding="utf-8-sig"))
            rules.apply_to(data, decisions_mod=D)

    def test_installer_to_synth_tool_ok(self):
        """安装包归到工具类目标（歌声合成）——不冲突，是对的。"""
        with tempfile.TemporaryDirectory() as d:
            make_dir(d, [".exe"] * 8)
            zones = [{"path": "/x/歌声合成", "label": "歌声合成",
                      "depth": 1, "files": 10}]
            r = D.suggest_group("音源合集", "下载目录", zones,
                                node_path="/x", group_path=d)
            self.assertFalse(r["needsCheck"], r)
            self.assertEqual(r["mode"], "move")

    def test_installer_to_music_product_conflicts(self):
        """安装包归到「音乐成品」——冲突，必须标待确认。"""
        with tempfile.TemporaryDirectory() as d:
            make_dir(d, [".exe"] * 8)
            zones = [{"path": "/x/音乐", "label": "音乐",
                      "depth": 1, "files": 10}]
            r = D.suggest_group("歌曲合集", "下载目录", zones,
                                node_path="/x", group_path=d)
            self.assertTrue(r["needsCheck"], r)
            self.assertTrue(any("冲突" in e or "成品" in e
                                for e in r["evidence"]), r)

    def test_media_named_tool(self):
        """名字命中"软件/代码"，但内容是成品媒体 → 必须标待确认。"""
        with tempfile.TemporaryDirectory() as d:
            make_dir(d, [".mp4"] * 8)
            zones = [{"path": "/x/软件安装包", "label": "软件安装包",
                      "depth": 1, "files": 10}]
            r = D.suggest_group("软件工具代码", "下载目录", zones,
                                node_path="/x", group_path=d)
            self.assertTrue(r["needsCheck"], r)

    def test_evidence_mentions_shape(self):
        with tempfile.TemporaryDirectory() as d:
            make_dir(d, [".exe"] * 8)
            r = D.suggest_group("随便什么", "下载目录", [], group_path=d)
            self.assertTrue(r.get("evidence"))
            self.assertTrue(any("installer" in e for e in r["evidence"]), r)




class TestIntegration(unittest.TestCase):
    """端到端：造目录 → 算建议 → 发现候选类别。"""

    @classmethod
    def setUpClass(cls):
        import json
        from pathlib import Path as _P
        pack = _P(__file__).resolve().parent.parent / "rules" / "default.json"
        if pack.is_file():
            import rules
            data = json.loads(pack.read_text(encoding="utf-8-sig"))
            rules.apply_to(data, decisions_mod=D)

    def test_three_installer_dirs_become_candidate(self):
        with tempfile.TemporaryDirectory() as base:
            names = ["工具A合集", "工具B合集", "工具C合集"]
            for n in names:
                p = os.path.join(base, n)
                os.makedirs(p)
                for i in range(6):
                    open(os.path.join(p, f"x{i}.exe"), "w").close()

            unmatched = []
            for n in names:
                p = os.path.join(base, n)
                r = D.suggest_group(n, "下载目录", [], node_path="/x",
                                    group_path=p)
                self.assertEqual(r["mode"], "keep")
                self.assertTrue(r.get("evidence"))
                unmatched.append((n, "installer"))

            props = D.propose_new_categories(unmatched)
            self.assertTrue(props, "三个同形态目录应产生候选")
            # 至少一个候选项包含全部 3 组
            counts = [v["count"] for v in props.values()]
            self.assertIn(3, counts)

    def test_single_installer_dir_no_candidate(self):
        with tempfile.TemporaryDirectory() as base:
            p = os.path.join(base, "孤件")
            os.makedirs(p)
            for i in range(6):
                open(os.path.join(p, f"x{i}.exe"), "w").close()
            r = D.suggest_group("孤件", "下载目录", [], node_path="/x",
                                group_path=p)
            props = D.propose_new_categories([("孤件", "installer")])
            self.assertEqual(props, {}, "单个孤件不该产生候选")




class TestNeedsAI(unittest.TestCase):
    """什么情况该交给 AI。"""

    def test_keep_with_shape_needs_ai(self):
        sug = {"mode": "keep", "reason": "无匹配",
               "evidence": ["内容以 installer 为主（.exe 占 89%）"]}
        self.assertEqual(D.needs_ai(sug, {"kind": "installer"}),
                         "no_rule_match")

    def test_keep_without_shape_no_ai(self):
        sug = {"mode": "keep", "reason": "无特征", "evidence": []}
        self.assertIsNone(D.needs_ai(sug, None))

    def test_conflict_needs_ai(self):
        sug = {"mode": "move", "needsCheck": True,
               "evidence": ["内容以安装包为主，但目标类别是成品类"]}
        self.assertEqual(D.needs_ai(sug, {"kind": "installer"}),
                         "shape_conflict")

    def test_new_with_check_needs_naming(self):
        sug = {"mode": "new", "newName": "某类", "needsCheck": True,
               "evidence": []}
        self.assertEqual(D.needs_ai(sug, None), "need_naming")

    def test_clean_move_no_ai(self):
        sug = {"mode": "move", "target": "/x/影视", "needsCheck": False,
               "evidence": []}
        self.assertIsNone(D.needs_ai(sug, {"kind": "media"}))


class TestBuildQuestion(unittest.TestCase):
    def test_question_has_facts(self):
        sug = {"mode": "keep", "reason": "无匹配",
               "evidence": ["内容以 installer 为主"]}
        q = D.build_ai_question("某组", "父目录", "/x", sug,
                                {"kind": "installer"},
                                ["a.exe", "b.exe"], trigger="no_rule_match")
        self.assertEqual(q["group"], "某组")
        self.assertEqual(q["parent"], "父目录")
        self.assertEqual(q["shape"]["kind"], "installer")
        self.assertEqual(q["sampleFiles"], ["a.exe", "b.exe"])
        self.assertIn("installer", q["question"])

    def test_need_naming_question_mentions_target(self):
        sug = {"mode": "new", "newName": "电子书", "needsCheck": True}
        q = D.build_ai_question("x", "y", "/p", sug, None, [],
                                trigger="need_naming")
        self.assertIn("电子书", q["question"])


class TestApplyAnswers(unittest.TestCase):
    def _nodes(self):
        return [{
            "name": "父A",
            "groups": [
                {"name": "组一", "suggest": {"mode": "keep", "needsCheck": True}},
                {"name": "组二", "suggest": {"mode": "keep", "needsCheck": True}},
            ],
        }]

    def test_applies_high_confidence(self):
        import json, tempfile
        nodes = self._nodes()
        ans = {"answers": [{"parent": "父A", "group": "组一",
                            "category": "study", "label": "学习资料",
                            "confidence": "high", "evidence": "课程视频"}]}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as f:
            json.dump(ans, f, ensure_ascii=False)
            path = f.name
        applied, missed = D.apply_answers(nodes, path)
        self.assertEqual(applied, 1)
        self.assertEqual(missed, 0)
        g = nodes[0]["groups"][0]
        self.assertEqual(g["suggest"]["aiJudgement"]["label"], "学习资料")
        self.assertFalse(g["suggest"]["needsCheck"])   # high → 不再待确认
        os.unlink(path)

    def test_low_confidence_stays_pending(self):
        import json, tempfile
        nodes = self._nodes()
        ans = {"answers": [{"parent": "父A", "group": "组一",
                            "category": "x", "label": "X",
                            "confidence": "low"}]}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as f:
            json.dump(ans, f, ensure_ascii=False)
            path = f.name
        D.apply_answers(nodes, path)
        self.assertTrue(nodes[0]["groups"][0]["suggest"]["needsCheck"])
        os.unlink(path)

    def test_unmatched_reported(self):
        import json, tempfile
        nodes = self._nodes()
        ans = {"answers": [{"parent": "不存在的父", "group": "不存在的组",
                            "category": "x", "label": "X",
                            "confidence": "high"}]}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as f:
            json.dump(ans, f, ensure_ascii=False)
            path = f.name
        applied, missed = D.apply_answers(nodes, path)
        self.assertEqual(applied, 0)
        self.assertEqual(missed, 1)
        os.unlink(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)