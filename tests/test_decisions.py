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
    """AI 自己走 猜想→验证→确认，代码只记录。"""

    def _nodes(self, n=5):
        return [{"name": "P", "groups": [
            {"name": f"G{i}", "suggest": {"mode": "keep", "needsCheck": True}}
            for i in range(n)]}]

    def _write(self, answers):
        import json, tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as f:
            json.dump({"answers": answers}, f, ensure_ascii=False)
            return f.name

    def test_full_flow_passes(self):
        nodes = self._nodes()
        path = self._write([{
            "parent": "P", "group": "G0", "category": "voice_synth",
            "label": "歌声合成", "hypothesis": "怀疑是成品音乐",
            "verification": {"method": "查 DLsite", "findings": "是声库",
                             "disproved": ["按名字归音乐是错的"]},
            "confirmation": {"holds": True, "confidence": "high",
                             "why": "验证推翻了初猜", "residualRisk": "无"}}])
        D.apply_answers(nodes, path)
        g = nodes[0]["groups"][0]["suggest"]
        self.assertFalse(g["needsCheck"])
        j = g["aiJudgement"]
        self.assertEqual(j["hypothesis"], "怀疑是成品音乐")
        self.assertIn("DLsite", j["verification"]["method"])
        self.assertTrue(j["confirmation"]["holds"])
        os.unlink(path)

    def test_missing_verification_pending(self):
        nodes = self._nodes()
        path = self._write([{
            "parent": "P", "group": "G1", "category": "x", "label": "X",
            "confirmation": {"holds": True, "confidence": "high",
                             "why": "我觉得"}}])
        D.apply_answers(nodes, path)
        g = nodes[0]["groups"][1]["suggest"]
        self.assertTrue(g["needsCheck"])
        self.assertTrue(any("验证过程" in e for e in g.get("evidence", [])))
        os.unlink(path)

    def test_missing_confirmation_pending(self):
        nodes = self._nodes()
        path = self._write([{
            "parent": "P", "group": "G2", "category": "x", "label": "X",
            "verification": {"method": "查了", "findings": "是"}}])
        D.apply_answers(nodes, path)
        g = nodes[0]["groups"][2]["suggest"]
        self.assertTrue(g["needsCheck"])
        self.assertTrue(any("确认结论" in e for e in g.get("evidence", [])))
        os.unlink(path)

    def test_ai_says_not_holds(self):
        """AI 自己确认'站不住'——代码尊重它的结论。"""
        nodes = self._nodes()
        path = self._write([{
            "parent": "P", "group": "G3", "category": "x", "label": "X",
            "hypothesis": "猜是A", "verification": {"method": "查了",
                                                    "findings": "是B不是A",
                                                    "disproved": ["A"]},
            "confirmation": {"holds": False, "confidence": "low",
                             "why": "验证推翻了", "residualRisk": "还需人看"}}])
        D.apply_answers(nodes, path)
        g = nodes[0]["groups"][3]["suggest"]
        self.assertTrue(g["needsCheck"], "AI 说站不住 → 保持待确认")
        self.assertFalse(g["aiJudgement"]["confirmation"]["holds"])
        os.unlink(path)

    def test_code_does_not_downgrade_confidence(self):
        """代码不替 AI 降级——记录原值。"""
        nodes = self._nodes()
        path = self._write([{
            "parent": "P", "group": "G4", "category": "x", "label": "X",
            "verification": {"method": "查了", "findings": "是"},
            "confirmation": {"holds": True, "confidence": "medium",
                             "why": "大概吧", "residualRisk": "细节不确定"}}])
        D.apply_answers(nodes, path)
        g = nodes[0]["groups"][4]["suggest"]
        # 代码如实记录 medium，不因为缺东西改成 low
        self.assertEqual(g["aiJudgement"]["confirmation"]["confidence"],
                         "medium")
        os.unlink(path)

    def test_unmatched_reported(self):
        nodes = self._nodes()
        path = self._write([{"parent": "不存在", "group": "也没有",
                             "category": "x", "label": "X"}])
        applied, missed = D.apply_answers(nodes, path)
        self.assertEqual(applied, 0)
        self.assertEqual(missed, 1)
        os.unlink(path)


class TestQuestionPackage(unittest.TestCase):
    def test_requires_three_steps(self):
        sug = {"mode": "keep", "reason": "x", "evidence": []}
        q = D.build_ai_question("g", "p", "/x", sug, None, [],
                                trigger="no_rule_match")
        ro = q["requiredOutput"]
        self.assertIn("hypothesis", ro["fields"])
        self.assertIn("verification", ro["fields"])
        self.assertIn("confirmation", ro["fields"])
        self.assertIn("holds", ro["fields"]["confirmation"])




class TestVerificationTier(unittest.TestCase):
    """分级：不是所有判断都要写完整三步。"""

    def test_structural_signal_is_direct(self):
        sug = {"mode": "keep", "evidence": ["unity-layout: true"]}
        self.assertEqual(D.verification_tier("x", sug), "direct")

    def test_vm_layout_is_direct(self):
        sug = {"mode": "keep", "evidence": ["vm-layout: true"]}
        self.assertEqual(D.verification_tier("x", sug), "direct")

    def test_split_requires_full(self):
        sug = {"mode": "split", "evidence": []}
        self.assertEqual(D.verification_tier("x", sug), "full")

    def test_merge_requires_full(self):
        sug = {"mode": "merge", "evidence": []}
        self.assertEqual(D.verification_tier("x", sug), "full")

    def test_conflict_requires_full(self):
        sug = {"mode": "keep", "needsCheck": True, "evidence": ["冲突"]}
        self.assertEqual(D.verification_tier("x", sug), "full")

    def test_new_requires_full(self):
        sug = {"mode": "new", "evidence": []}
        self.assertEqual(D.verification_tier("x", sug), "full")

    def test_shape_evidence_requires_verify(self):
        sug = {"mode": "keep", "evidence": ["内容以 installer 为主"]}
        self.assertEqual(D.verification_tier("x", sug), "verify")

    def test_empty_evidence_defaults_verify(self):
        sug = {"mode": "keep", "evidence": []}
        self.assertEqual(D.verification_tier("x", sug), "verify")

    def test_question_carries_tier(self):
        sug = {"mode": "split", "evidence": []}
        q = D.build_ai_question("g", "p", "/x", sug, None, [],
                                trigger="no_rule_match")
        self.assertEqual(q["requiredOutput"]["tier"], "full")
        self.assertIn("tierGuide", q["requiredOutput"])


class TestTierEnforcement(unittest.TestCase):
    """按级别检查完整性。"""

    def _nodes(self):
        return [{"name": "P", "groups": [
            {"name": "D", "suggest": {"mode": "keep",
                                      "evidence": ["unity-layout: true"]}},
            {"name": "V", "suggest": {"mode": "keep",
                                      "evidence": ["内容以 installer 为主"]}},
            {"name": "F", "suggest": {"mode": "split", "evidence": []}},
        ]}]

    def _write(self, answers):
        import json, tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as f:
            json.dump({"answers": answers}, f, ensure_ascii=False)
            return f.name

    def test_direct_needs_only_evidence(self):
        """直接定的只要 evidence，不要求三步。"""
        nodes = self._nodes()
        path = self._write([{
            "parent": "P", "group": "D", "category": "x", "label": "X",
            "evidence": "steamapps/ 下有 appmanifest_*.acf",
            "confirmation": {"holds": True, "confidence": "high"}}])
        D.apply_answers(nodes, path)
        g = nodes[0]["groups"][0]["suggest"]
        self.assertEqual(g["aiJudgement"]["verificationTier"], "direct")
        self.assertFalse(g["needsCheck"], "直接定不该因为缺 verification 被拦")
        os.unlink(path)

    def test_direct_without_evidence_blocked(self):
        nodes = self._nodes()
        path = self._write([{
            "parent": "P", "group": "D", "category": "x", "label": "X",
            "confirmation": {"holds": True}}])
        D.apply_answers(nodes, path)
        g = nodes[0]["groups"][0]["suggest"]
        self.assertTrue(g["needsCheck"])

    def test_full_requires_three_steps(self):
        nodes = self._nodes()
        path = self._write([{
            "parent": "P", "group": "F", "category": "x", "label": "X",
            "evidence": "有内容"}])
        D.apply_answers(nodes, path)
        g = nodes[0]["groups"][2]["suggest"]
        self.assertEqual(g["aiJudgement"]["verificationTier"], "full")
        self.assertTrue(g["needsCheck"], "必须三步的缺步骤应仍待确认")
        os.unlink(path)




class TestPolicyMapping(unittest.TestCase):
    """policy.json 的 mapping 要真的生效——S0 前置确认的产物。"""

    def _setup_policy(self, tmp, mapping):
        import json
        from pathlib import Path as _P
        org = _P(tmp) / "_organize"
        org.mkdir(parents=True, exist_ok=True)
        (org / "policy.json").write_text(
            json.dumps({"mapping": mapping}, ensure_ascii=False),
            encoding="utf-8")
        return tmp

    def test_load_returns_mapping(self):
        with tempfile.TemporaryDirectory() as d:
            self._setup_policy(d, {"video_movie": "我的影视"})
            mp, st = D.load_policy_mapping(d)
            self.assertEqual(mp.get("video_movie"), "我的影视")

    def test_load_skips_comment_keys(self):
        with tempfile.TemporaryDirectory() as d:
            self._setup_policy(d, {"_comment": "说明", "game": "游戏"})
            mp, _ = D.load_policy_mapping(d)
            self.assertNotIn("_comment", mp)
            self.assertIn("game", mp)

    def test_load_missing_file(self):
        with tempfile.TemporaryDirectory() as d:
            mp, st = D.load_policy_mapping(d)
            self.assertEqual(mp, {})

    def test_apply_changes_cross_rules(self):
        """mapping 应该覆盖 CROSS_RULES 的目标。"""
        saved = [dict(r) for r in D.CROSS_RULES]
        saved_nodes = set(D.CATEGORY_NODE_NAMES)
        try:
            D.CROSS_RULES = [
                {"keys": ["电影", "影片"], "to": ["影视"]},
                {"keys": ["游戏"], "to": ["游戏"]},
            ]
            D.CATEGORY_NODE_NAMES = set()
            n = D.apply_policy_mapping({"video_movie": "我的影视区"})
            self.assertGreater(n, 0)
            rule = [r for r in D.CROSS_RULES if "电影" in r["keys"]][0]
            self.assertEqual(rule["to"], ["我的影视区"])
        finally:
            D.CROSS_RULES = saved
            D.CATEGORY_NODE_NAMES = saved_nodes

    def test_apply_adds_to_category_nodes(self):
        saved = [dict(r) for r in D.CROSS_RULES]
        saved_nodes = set(D.CATEGORY_NODE_NAMES)
        try:
            D.CROSS_RULES = []
            D.CATEGORY_NODE_NAMES = set()
            D.apply_policy_mapping({"game": "我的游戏区"})
            self.assertIn("我的游戏区", D.CATEGORY_NODE_NAMES)
        finally:
            D.CROSS_RULES = saved
            D.CATEGORY_NODE_NAMES = saved_nodes

    def test_apply_empty_is_noop(self):
        n = D.apply_policy_mapping({})
        self.assertEqual(n, 0)

    def test_category_keys_covers_default_pack(self):
        """CATEGORY_KEYS 要覆盖规则包里所有类别。"""
        import json
        from pathlib import Path as _P
        pack = _P(__file__).resolve().parent.parent / "rules" / "default.json"
        if not pack.is_file():
            self.skipTest("没有规则包")
        data = json.loads(pack.read_text(encoding="utf-8-sig"))
        cats = {k for k in data.get("categories", {})
                if not k.startswith("_")}
        missing = cats - set(D.CATEGORY_KEYS)
        self.assertFalse(missing, f"CATEGORY_KEYS 缺: {missing}")




class TestUiUnitList(unittest.TestCase):
    """ui._unit_list —— units 可能是数组也可能只是计数。"""

    def test_list_passthrough(self):
        import ui
        r = ui._unit_list({"units": ["a", "b", "c"]})
        self.assertEqual(r, ["a", "b", "c"])

    def test_int_becomes_placeholders(self):
        import ui
        r = ui._unit_list({"units": 3})
        self.assertEqual(len(r), 3)
        self.assertTrue(all("第" in x for x in r))

    def test_missing_returns_empty(self):
        import ui
        self.assertEqual(ui._unit_list({}), [])
        self.assertEqual(ui._unit_list({"units": None}), [])

    def test_long_list_truncated(self):
        import ui
        r = ui._unit_list({"units": [f"u{i}" for i in range(200)]})
        self.assertLessEqual(len(r), 60)

    def test_very_long_name_truncated(self):
        import ui
        r = ui._unit_list({"units": ["x" * 500]})
        self.assertLessEqual(len(r[0]), 200)


class TestUiLoadsRules(unittest.TestCase):
    """ui.py 必须加载规则包，否则建议全线失效。"""

    def test_ui_imports_rules(self):
        import inspect
        import ui
        src = inspect.getsource(ui.main)
        self.assertIn("rules", src, "ui.main 应加载规则包")
        self.assertIn("apply_to", src, "ui.main 应应用规则包")

    def test_ui_applies_policy_mapping(self):
        import inspect
        import ui
        src = inspect.getsource(ui.main)
        self.assertIn("load_policy_mapping", src,
                      "ui.main 应应用 policy.json 的 mapping")


class TestSuggestNotAllKeep(unittest.TestCase):
    """有规则包时，suggest 不能全部返回 keep。"""

    def test_mixed_modes_with_rules(self):
        saved = [dict(r) for r in D.CROSS_RULES]
        saved_nodes = set(D.CATEGORY_NODE_NAMES)
        try:
            D.CROSS_RULES = [
                {"keys": ["课程", "教程"], "to": ["学习资料"]},
                {"keys": ["游戏"], "to": ["游戏"]},
            ]
            D.CATEGORY_NODE_NAMES = set()
            zones = [
                {"path": "/x/学习资料", "label": "学习资料", "depth": 1, "files": 10},
                {"path": "/x/游戏", "label": "游戏", "depth": 1, "files": 10},
            ]
            modes = set()
            for name in ["编程课程", "某个游戏", "随便什么"]:
                r = D.suggest_group(name, "下载", zones)
                modes.add(r["mode"])
            self.assertGreater(len(modes), 1,
                               f"应返回多种 mode，实际只有 {modes}")
        finally:
            D.CROSS_RULES = saved
            D.CATEGORY_NODE_NAMES = saved_nodes


if __name__ == "__main__":
    unittest.main(verbosity=2)