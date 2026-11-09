#!/usr/bin/env python3
"""
decisions.py —— 生成决策界面的数据，含新建分类的归属规划

核心逻辑：
  1. 读 probe/*.json
  2. 为每组算建议（原地 / 移动 / 新建）
  3. 新建的目录要有归属父级，不能都堆在根目录
  4. 输出 JSON 给 ui.html 用
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SYSTEM_DIRS = {
    '$recycle.bin', 'system volume information', 'recovery',
    'config.msi', 'windows', 'program files', 'program files (x86)',
    '$windows.~bt', '$windows.~ws', 'perflogs', 'intel', 'amd',
}

# ═══════════════════════════════════════════════════════════
# 归组关系 → 用户能看懂的话
# ═══════════════════════════════════════════════════════════
RELATION_TEXT = {
    'theme':              '同一类东西',
    'multi-version':      '同一作品的不同版本',
    'episode-complement': '同一作品的分集',
    'cross-layer-dup':    '重复的两份',
    'form-cluster':       '来源相同的一批',
}

# ═══════════════════════════════════════════════════════════
# 跨类规则：组名命中关键词 → 该去哪个已有类
# ═══════════════════════════════════════════════════════════
# 跨类映射由规则包提供（rules/default.json 的 categories + mapping）。
# 这里留空——写死的映射必然会带出某个人的分类体系。
CROSS_RULES = []

# 本身就是分类容器的顶层区名字。空集表示"没有任何一个算"——
# 具体名单由规则包的 categoryNodes 提供，不写死在代码里。
CATEGORY_NODE_NAMES = set()


# ═══════════════════════════════════════════════════════════
# 新建分类白名单：只有这些才值得新建，而且指定归属父级
# key = 目录名，value = 父级分类（None 表示放根目录）
# ═══════════════════════════════════════════════════════════
NEW_CATEGORIES = {
    # 文档类
    '电子书':   {'parent': '文档', 'keys': ['电子书', '小说', 'epub', 'txt全集']},
    '课程':     {'parent': '文档', 'keys': ['课程', '网课', '培训']},
    '教材':     {'parent': '文档', 'keys': ['教材', '教科书', '课本']},
    '字体':     {'parent': '文档', 'keys': ['字体', '字库', 'font']},

    # 系统类
    '刷机':     {'parent': '系统', 'keys': ['刷机', 'ROM', 'TWRP', 'OTA', '救砖']},
    '驱动':     {'parent': '系统', 'keys': ['驱动', 'driver']},
    '系统镜像': {'parent': '系统', 'keys': ['iso', 'gho', 'wim', '系统镜像']},

    # 设计类
    '素材':     {'parent': '设计', 'keys': ['素材', '笔刷', '预设', '模板素材']},
    '字体设计': {'parent': '设计', 'keys': ['字体设计']},

    # 其它
    '壁纸':     {'parent': None, 'keys': ['壁纸', 'wallpaper']},
    '音效':     {'parent': None, 'keys': ['音效', 'sound']},
}


def load_probes(probe_dir):
    probes, units, groups = {}, {}, {}
    if not probe_dir.is_dir():
        return probes, units, groups
    for name in os.listdir(probe_dir):
        if not name.endswith(".json"):
            continue
        try:
            data = json.loads((probe_dir / name).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or not data.get("path"):
            continue
        path = data["path"]
        if name.startswith("units_"):
            units[path] = data
        elif name.startswith("group_"):
            groups[path] = data
        elif not name.startswith("dup_"):
            probes[path] = data
    return probes, units, groups


def unit_num(item):
    v = item.get("units")
    if isinstance(v, list):
        return len(v)
    if isinstance(v, int):
        return v
    if isinstance(item.get("unit_count"), int):
        return item["unit_count"]
    return 0


def build_nodes(root, probes, units, groups):
    nodes = []
    for path in sorted(set(probes) | set(units) | set(groups)):
        if not os.path.isdir(path):
            continue
        p = probes.get(path) or {}
        u = units.get(path) or {}
        g = groups.get(path) or {}
        nodes.append({
            "path": path,
            "name": os.path.basename(path.rstrip(os.sep)),
            "action": p.get("action"),
            "confidence": p.get("confidence"),
            "what": (p.get("actual") or {}).get("what", ""),
            "unit_why": p.get("unit_why", ""),
            "rename_to": p.get("rename_to"),
            "total_units": u.get("total_units"),
            "groups": [{
                "name": x.get("name", ""),
                "why": x.get("why", ""),
                "relation": x.get("relation", ""),
                "relationText": RELATION_TEXT.get(
                    x.get("relation", ""), x.get("relation", "")),
                "confidence": x.get("confidence", ""),
                "n": unit_num(x),
            } for x in (g.get("groups") or [])],
            "solo_n": len(g.get("solo_units") or []),
            "uncertain_n": len(g.get("uncertain") or []),
        })
    return nodes


def count_files(path, limit=20000):
    """粗数文件数，用于判断目录是否有内容。"""
    n = 0
    try:
        for dp, dns, fns in os.walk(path):
            n += len(fns)
            if n >= limit:
                return n
    except OSError:
        pass
    return n


def collect_zones(root, max_depth=3, min_files=1):
    """收集候选去向，最多 3 层。

    每项：{path, label, depth, files}
      label  相对路径，用 / 分隔（"文件/软件安装包"）
      depth  1/2/3
      files  文件数（粗估）

    过滤规则：
      - 跳过系统目录、下划线/点开头
      - 跳过空目录（文件数 < min_files）
      - 深度超过 max_depth 不再下探
    """
    zones = []

    def walk(path, label, depth):
        if depth > max_depth:
            return
        try:
            entries = sorted(os.scandir(path), key=lambda e: e.name)
        except OSError:
            return
        for e in entries:
            if not e.is_dir(follow_symlinks=False):
                continue
            name = e.name
            low = name.casefold()
            if name.startswith(('_', '.', '$')):
                continue
            if depth == 1 and low in SYSTEM_DIRS:
                continue
            try:
                attrs = getattr(e.stat(follow_symlinks=False),
                                'st_file_attributes', 0)
                if attrs & 0x2 or attrs & 0x4:
                    continue
            except OSError:
                pass

            sub_label = f"{label}/{name}" if label else name
            files = count_files(e.path)
            if files >= min_files:
                zones.append({
                    "path": e.path,
                    "label": sub_label,
                    "depth": depth,
                    "files": files,
                })
            # 继续下探（有内容才下探，省时间）
            if files > 0 and depth < max_depth:
                walk(e.path, sub_label, depth + 1)

    walk(root, "", 1)
    return zones


def looks_like_category(name):
    """判断子目录名是否像一个分类桶。"""
    # 通用分类词。具体名字由调用方的 zones 决定，这里只判断"像不像分类"
    generic = ['文档', '软件', '安装包', '视频', '音频', '图片', '图像',
               '游戏', '影视', '模型', '课程', '书', '音乐', '素材',
               'document', 'software', 'video', 'audio', 'image', 'picture',
               'game', 'model', 'course', 'book', 'music', 'asset']
    low = name.lower()
    return any(g in low for g in generic)


# ═══════════════════════════════════════════════════════════
# 内容形态检测：先看"里面是什么"，再看"叫什么名字"
#
# 只看名字必然出错——"音源"可能是采样库也可能是成品曲，
# "镜像"可能是系统盘也可能是刷机包。名字是线索，内容才是证据。
# ═══════════════════════════════════════════════════════════

# 安装包特征：这些扩展名说明是"可分发的软件"
INSTALLER_EXT = {".exe", ".msi", ".dmg", ".pkg", ".appx", ".msix",
                 ".deb", ".rpm", ".apk"}
# 归档特征
ARCHIVE_EXT = {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz",
               ".iso", ".wim", ".gho", ".esd"}
# 媒体成品特征
MEDIA_EXT = {".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".webm",
             ".mp3", ".flac", ".wav", ".aac", ".m4a", ".ogg"}
# 图像特征
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp",
             ".psd", ".tif", ".tiff", ".svg"}
# 代码特征
CODE_EXT = {".py", ".js", ".ts", ".java", ".c", ".cpp", ".cs", ".go",
            ".rs", ".php", ".rb", ".swift", ".kt", ".lua"}
# 文档特征
DOC_EXT = {".doc", ".docx", ".pdf", ".txt", ".md", ".xls", ".xlsx",
           ".ppt", ".pptx", ".odt", ".csv", ".epub", ".mobi"}


def content_shape(path, max_files=2000):
    """看一个目录里装的是什么形态的文件。

    只统计扩展名分布，不读内容。成本低，但足以区分
    "安装包集合"和"成品媒体"这种关键差别。
    """
    import os as _os
    from collections import Counter

    ext_count = Counter()
    total = 0
    try:
        for dp, dn, fn in _os.walk(path):
            for f in fn:
                e = _os.path.splitext(f)[1].lower()
                ext_count[e] += 1
                total += 1
                if total >= max_files:
                    break
            if total >= max_files:
                break
    except OSError:
        return None

    if not total:
        return {"total": 0, "dominant": None, "kind": "empty"}

    top_ext, top_n = ext_count.most_common(1)[0]
    ratio = top_n / total

    def fam(exts):
        return sum(ext_count[e] for e in exts) / total

    shares = {
        "installer": fam(INSTALLER_EXT),
        "archive": fam(ARCHIVE_EXT),
        "media": fam(MEDIA_EXT),
        "image": fam(IMAGE_EXT),
        "code": fam(CODE_EXT),
        "doc": fam(DOC_EXT),
    }
    best = max(shares, key=shares.get)

    kind = "mixed"
    if shares["installer"] >= 0.5:
        kind = "installer"      # 主要是可执行安装体
    elif shares["media"] >= 0.6:
        kind = "media"          # 主要是音视频成品
    elif shares["image"] >= 0.6:
        kind = "image"          # 主要是图像
    elif shares["code"] >= 0.5:
        kind = "code"           # 主要是源码
    elif shares["archive"] >= 0.5:
        kind = "archive"        # 主要是压缩包
    elif shares["doc"] >= 0.5:
        kind = "document"       # 主要是文档

    return {
        "total": total,
        "dominant": top_ext,
        "ratio": round(ratio, 2),
        "shares": {k: round(v, 2) for k, v in shares.items() if v > 0},
        "kind": kind,
    }


# ═══════════════════════════════════════════════════════════
# 新类别发现：规则包不可能穷举所有类型
#
# 遇到匹配不上的组时，不该直接放弃（fallback 到"保持原位"）。
# 而是看这批东西有没有共同特征，能不能提出候选类别。
#
# 判据（与最小单元规则一致）：
#   ≥2 个组共享同一特征  → 值得建类
#   只 1 个组           → 不建，标 uncertain
# ═══════════════════════════════════════════════════════════

# 从组名里提取"类别词根"的常见后缀（去掉这些后剩下的就是名词）
_GENERIC_SUFFIX = [
    "合集", "集合", "打包", "整包", "系列", "文件", "目录", "资源",
    "资料", "内容", "相关", "备份", "存档",
]


def name_stem(group_name):
    """从组名提取可能的类别词根。

    规则：去掉括号、去掉通用后缀、去掉版本号，
    但**保留"X 与 Y"里的主词**，不按空格硬切。

    "VOCALOID 编辑器与声库" → "VOCALOID"
    "编程课程合集"          → "编程课程"
    "4K 壁纸打包"           → "4K 壁纸"
    """
    import re as _re

    s = group_name.strip()
    # 去括号内容
    s = _re.sub(r"[（(【\[].*?[)）】\]]", "", s)

    # 按"与 / 和 / 及"切成主次，取主词
    for sep in ("与", "和", "及", "/", "、"):
        if sep in s:
            head = s.split(sep)[0].strip()
            if len(head) >= 2:
                s = head
                break

    # 空格分隔时，取前半段——但要求前半段不是纯数字/纯符号
    # （"4K 壁纸" 的前半段是 "4K"，有字母，保留整段；
    #   "VOCALOID 编辑器" 的前半段是 "VOCALOID"，取前半段）
    # 空格分隔：只有当前半段"本身是词根"时才切
    # "VOCALOID 编辑器"  前半段 VOCALOID 是完整词 → 切
    # "4K 壁纸"         前半段 4K 是规格不是词根 → 不切，保留整段
    if " " in s:
        head = s.split(" ")[0].strip()
        is_word = (any("\u4e00" <= ch <= "\u9fff" for ch in head)
                   or (head.isascii() and head.isalpha() and len(head) >= 3))
        if is_word:
            s = head

    # 去通用后缀（可能连续出现）
    changed = True
    while changed:
        changed = False
        for suf in _GENERIC_SUFFIX:
            if s.endswith(suf) and len(s) > len(suf):
                s = s[: -len(suf)].strip()
                changed = True

    # 去尾部的版本/日期/编号
    s = _re.sub(r"[\s\-_]*v?[\d\.]+[a-zA-Z]?$", "", s, flags=_re.I).strip()
    # 去尾部标点
    s = s.strip(" \t-_·,，")

    # 词根至少要有一个汉字或 2 个字母
    has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in s)
    if not has_cjk and len(s) < 2:
        return ""
    return s


def propose_new_categories(unmatched, min_groups=2):
    """从匹配不上的组里找新类别候选。

    两种聚类依据，任一成立就提议：
      ① 词根相同     "VOCALOID 声库" / "VOCALOID 编辑器" → 词根都是 VOCALOID
      ② 形态相同     "声库A" / "声库B" 都装 exe，形态一致且数量够

    只 1 组的不提议——单个孤件不足以证明"这是一类东西"。

    unmatched: [(组名, 内容形态)] 的列表
    返回: {候选名: {count, kinds, members, basis}}
    """
    from collections import defaultdict

    # ① 按词根聚
    by_stem = defaultdict(lambda: {"count": 0, "kinds": set(), "members": []})
    for name, kind in unmatched:
        stem = name_stem(name)
        if not stem or len(stem) < 2:
            continue
        if stem.isdigit():
            continue
        b = by_stem[stem]
        b["count"] += 1
        if kind:
            b["kinds"].add(kind)
        b["members"].append(name)

    out = {}
    for stem, info in by_stem.items():
        if info["count"] >= min_groups:
            out[stem] = {
                "count": info["count"],
                "kinds": sorted(info["kinds"]),
                "members": info["members"][:10],
                "basis": "词根相同",
            }

    # ② 若词根聚类没结果，退而按"形态 + 同类词"聚
    if not out:
        by_kind = defaultdict(list)
        for name, kind in unmatched:
            if kind:
                by_kind[kind].append(name)
        for kind, names in by_kind.items():
            if len(names) >= min_groups:
                # 用最长公共前缀当候选名
                common = _common_prefix(names)
                label = common if len(common) >= 2 else f"{kind}类"
                out[label] = {
                    "count": len(names),
                    "kinds": [kind],
                    "members": names[:10],
                    "basis": f"形态一致（{kind}）",
                }

    return out


def _common_prefix(names):
    """取一组名字的最长公共前缀（去标点后）。"""
    import re as _re
    if not names:
        return ""
    cleaned = [_re.sub(r"[\s\-_·,，]", "", n) for n in names]
    if len(cleaned) < 2:
        return cleaned[0] if cleaned else ""
    prefix = cleaned[0]
    for n in cleaned[1:]:
        i = 0
        while i < len(prefix) and i < len(n) and prefix[i] == n[i]:
            i += 1
        prefix = prefix[:i]
    # 单字前缀也保留——"A" 是有效线索，调用方可自行决定是否采用
    return prefix


# ═══════════════════════════════════════════════════════════
# AI 判断环节：代码做不了的语义决策
#
# 代码能算扩展名分布、能提词根、能匹配规则包，
# 但"这个组到底是什么东西""该叫什么名字"必须由 AI 判断。
# 这里只负责把需要 AI 判断的挑出来，并把事实准备齐。
# ═══════════════════════════════════════════════════════════

# 什么情况必须交给 AI
AI_TRIGGERS = {
    "no_rule_match":   "规则包匹配不上",
    "shape_conflict":  "内容形态与目标类别冲突",
    "need_naming":     "候选类别需要命名",
    "cross_bucket":    "同系列跨桶",
}


def needs_ai(suggest, shape=None):
    """这一组的建议，需不需要 AI 来判断？

    返回 None 表示代码已能定论；否则返回触发原因。
    """
    if suggest.get("needsCheck"):
        # needsCheck 为真就是代码对结论没把握，必须交人/AI
        # 有 evidence 说明是形态与名字的矛盾
        if suggest.get("evidence"):
            return "shape_conflict"
        return "need_naming"

    if suggest["mode"] == "keep":
        if shape and shape.get("kind") not in (None, "mixed", "empty"):
            # 形态明确但没定去向 → 需要判断这是什么
            return "no_rule_match"
        return None

    if suggest["mode"] == "new" and suggest.get("needsCheck"):
        return "need_naming"

    return None


def build_ai_question(group_name, node_name, node_path, suggest,
                      shape=None, sample_files=None, trigger=None):
    """构造一个 AI 判断请求。只放事实，不放判断。"""
    return {
        "group": group_name,
        "parent": node_name,
        "path": node_path,
        "shape": shape or {},
        "sampleFiles": (sample_files or [])[:8],
        "suggestion": {
            "mode": suggest.get("mode"),
            "target": suggest.get("targetLabel") or suggest.get("newName"),
            "reason": suggest.get("reason"),
            "evidence": suggest.get("evidence") or [],
        },
        "question": _ai_question_text(suggest, shape, trigger),
        "requiredOutput": {
            "flow": "猜想 → 验证 → 确认，三步都要走",
            "fields": {
                "hypothesis": "最初认为这是什么（写完再去验）",
                "verification": {
                    "method": "怎么查的：查了哪个站、看了什么文件",
                    "findings": "查到什么，支持还是推翻猜测",
                    "disproved": "推翻过什么假设（空数组也要说明没推翻）",
                },
                "confirmation": {
                    "holds": "true/false——你的判断站得住吗",
                    "confidence": "high/medium/low",
                    "why": "为什么确认（或为什么不确认）",
                    "residualRisk": "还剩什么不确定",
                },
            },
            "note": "代码不检查你验得够不够。它会原样记下这条链，"
                    "人看到推理过程自己判断。",
        },
    }


def _ai_question_text(suggest, shape=None, trigger=None):
    if trigger == "need_naming":
        target = (suggest.get("target") or suggest.get("newName")
                  or suggest.get("targetLabel") or "这个类别")
        return (f"建议新建「{target}」，但这个名字合适吗？"
                f"或者该并进已有的某一类？")
    if trigger == "shape_conflict":
        return ("名字指向的类别和文件内容对不上。"
                "该信内容还是名字？内容是对的，该归到哪一类？")
    if trigger == "no_rule_match":
        kind = (shape or {}).get("kind", "未知")
        return (f"规则包中没有匹配项，但内容是 {kind}。"
                f"这些是什么？该归到哪一类？")
    if suggest.get("mode") == "keep":
        return "这些文件是什么？该归到哪一类？还是保持原位？"
    if suggest.get("needsCheck"):
        return "这个归类对吗？不确定的话该归到哪？"
    return "这个归类对吗？不确定的话该归到哪？"


def sample_file_names(path, limit=8):
    """取几个文件名作为样例——只要名字，不读内容。"""
    import os as _os
    out = []
    try:
        for dp, dn, fn in _os.walk(path):
            for f in sorted(fn):
                out.append(f)
                if len(out) >= limit:
                    return out
    except OSError:
        pass
    return out


# 内容形态 → 该去哪个语义类别（优先级高于名字匹配）
SHAPE_TO_CATEGORY = {
    "media":     "media_pending",      # 需要按名字细分（视频/音乐）
    "image":     "image_pending",
    "code":      "code",
    "document":  "book",
    "installer": "software_installer",
    "archive":   None,                 # 压缩包看不出内容，不判断
    "mixed":     None,
    "empty":     None,
}


def suggest_group(group_name, node_name, zones, node_path=None,
                  group_path=None):
    """给一组算出去向建议。

    判断顺序（重要）：
      ① 结构信号   —— 组内实际装的是什么形态的文件
      ② 归属信号   —— 组在哪个目录下、那个目录是不是分类容器
      ③ 名字信号   —— 关键词匹配（最弱，仅作兜底）

    只看名字会出错：VOCALOID 叫"音源"但装的是安装包+声库，
    归到"音乐"就错了。先看内容形态能避免这类错误。

    返回 {mode, target, newName, newParent, reason, needsCheck, evidence}
    """
    evidence = []

    # ① 内容形态：这是最硬的证据
    shape = None
    if group_path and os.path.isdir(group_path):
        shape = content_shape(group_path)
        if shape and shape["kind"] not in (None, "mixed", "empty"):
            evidence.append(f"内容以 {shape['kind']} 为主"
                            f"（{shape['dominant']} 占 {shape.get('ratio', 0):.0%}）")

    # ② 归属：组名含目录名，或所在目录本身是分类容器 → 原地
    if node_name and node_name in group_name:
        return {"mode": "keep", "reason": "组名含目录名，属本目录内部",
                "needsCheck": False, "evidence": evidence}

    if node_path and is_category_node(node_name or ""):
        return {"mode": "keep",
                "reason": "所在目录已是分类目录，组在内部无需移动",
                "needsCheck": False, "evidence": evidence}

    # ③ 名字匹配
    for rule in CROSS_RULES:
        if not any(k in group_name for k in rule["keys"]):
            continue

        # 名字命中了，但如果内容形态与目标类别冲突，必须标出待确认
        #
        # 冲突的定义：目标是"成品"类，但内容是"工具"类（或反之）。
        # 判断靠目标类别的性质，不靠具体词表——
        # 词表会漏，"歌声合成"这种既含工具又含产物的名字必然踩坑。
        conflict = False
        if shape:
            kind = shape["kind"]
            targets_text = " ".join(str(t) for t in rule["to"]).lower()

            def has_any(words):
                return any(w.lower() in targets_text for w in words)

            # 成品类目标的特征词
            PRODUCT_WORDS = (
                "音乐", "歌曲", "影视", "视频", "电影", "图像", "图片",
                "漫画", "书", "文档", "曲", "music", "video", "movie",
                "image", "comic", "book", "document", "photo")
            # 工具类目标的特征词
            TOOL_WORDS = (
                "安装包", "工具", "软件", "程序", "代码", "驱动", "镜像",
                "模板", "字体", "模型", "installer", "tool", "software",
                "code", "driver", "template", "model")

            if kind == "installer" and has_any(PRODUCT_WORDS):
                conflict = True
                evidence.append(
                    "内容以安装包为主，但目标类别是成品类——可能是工具/素材")
            elif kind == "media" and has_any(TOOL_WORDS):
                conflict = True
                evidence.append(
                    "内容是成品媒体，但目标类别是工具类——可能名字有歧义")
            elif kind == "code" and has_any(PRODUCT_WORDS):
                conflict = True
                evidence.append("内容是源码，但目标类别是成品类")

        for t in rule["to"]:
            cands = [z for z in zones if t.lower() in z["label"].lower()]
            if cands:
                cands.sort(key=lambda z: (z["depth"], -z["files"]))
                return {"mode": "move", "target": cands[0]["path"],
                        "targetLabel": cands[0]["label"],
                        "reason": "命中跨类关键词",
                        "needsCheck": conflict,
                        "evidence": evidence}

        for cat, info in NEW_CATEGORIES.items():
            if any(k in group_name for k in info["keys"]):
                return {"mode": "new", "newName": cat,
                        "newParent": info["parent"],
                        "reason": "盘上没有对应分类",
                        "needsCheck": True,
                        "evidence": evidence}

    # ④ 没有名字线索，但内容形态明确 → 按形态给建议
    if shape and shape["kind"] not in (None, "mixed", "empty"):
        hint = SHAPE_TO_CATEGORY.get(shape["kind"])
        if hint and hint in NEW_CATEGORIES:
            info = NEW_CATEGORIES[hint]
            return {"mode": "new", "newName": hint,
                    "newParent": info["parent"],
                    "reason": f"内容形态是 {shape['kind']}，但名字无线索",
                    "needsCheck": True,
                    "evidence": evidence}

    return {"mode": "keep",
            "reason": "无明显跨类特征" + ("（内容混杂）" if shape else ""),
            "needsCheck": False, "evidence": evidence}



def _boot_rules():
    """启动时加载规则包，覆盖模块级常量。

    规则包可选——没有就沿用代码默认值。加载失败不阻断。
    """
    try:
        import rules as rules_mod
        import safety as safety_mod
    except ImportError:
        return
    try:
        data = rules_mod.load()
        if data.get("_missing"):
            return
        changed = rules_mod.apply_to(data, safety_mod=safety_mod,
                                     decisions_mod=sys.modules[__name__])
        if changed:
            print(f"规则包 v{data.get('version')} 已生效: "
                  f"{len(changed)} 组常量", file=sys.stderr)
    except Exception as exc:
        print(f"规则包加载失败，使用内置默认: {type(exc).__name__}",
              file=sys.stderr)


def is_category_node(name):
    """这个顶层区本身就是一个分类容器吗？

    容器内部的组不需要再移动——它们已经在正确的位置了。
    名单由规则包的 categoryNodes 提供；这里留空表示"全都不算"，
    宁可多问一次，也不凭猜测把容器内的组搬走。
    """
    if not name:
        return False
    low = name.casefold()
    return any(low == c.casefold() for c in CATEGORY_NODE_NAMES)


def apply_answers(nodes, answers_path):
    """把 AI 的判断回填。代码只记录，不做确认。

    AI 自己走完三步，代码如实记下：
      猜想 hypothesis    最初认为这是什么
      验证 verification  怎么查的、查到什么、推翻过什么
      确认 confirmation  是否站得住、置信度、残余风险

    答案格式：
      {"answers": [{
          "group": "...", "parent": "...",
          "category": "...", "label": "...",
          "hypothesis": "最初猜它是什么",
          "verification": {
            "method": "查了 DLsite 作品页 / 看了 exe 签名",
            "findings": "确认为编辑器 + 声库",
            "disproved": ["最初按名字以为是成品音乐"]
          },
          "confirmation": {
            "holds": true,
            "confidence": "high|medium|low",
            "why": "为什么确认站得住",
            "residualRisk": "还剩什么不确定"
          }
        }]}

    **代码不判断验证够不够。** 那是 AI 的活——
    它会写下理由和残余风险，人看到整条推理链自己决定。

    代码只做一件事实性的事：
      记录 confirmation 是否给出、holds 是否为真。
      没给出的，在报告里列出，不替它降级。
    """
    import json as _json
    from pathlib import Path as _P

    try:
        raw = _P(answers_path).read_text(encoding="utf-8-sig")
        payload = _json.loads(raw)
    except (OSError, ValueError) as exc:
        print(f"答案文件无法读取: {type(exc).__name__}", file=sys.stderr)
        return 0, 0

    items = payload.get("answers") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        print("答案格式错误：需要 answers 数组", file=sys.stderr)
        return 0, 0

    index = {}
    for n in nodes:
        for g in n["groups"]:
            index[(n["name"], g["name"])] = g

    applied = missed = 0
    incomplete = []

    for a in items:
        if not isinstance(a, dict):
            continue
        key = (a.get("parent"), a.get("group"))
        g = index.get(key)
        if not g:
            missed += 1
            continue

        sg = g.setdefault("suggest", {})
        cat = a.get("category")
        label = a.get("label") or cat
        if cat:
            sg["mode"] = "new"
            sg["newName"] = label
            sg["newParent"] = None

        ver = a.get("verification") or {}
        conf = a.get("confirmation") or {}

        # 原样记录 AI 交来的全部内容
        sg["aiJudgement"] = {
            "category": cat,
            "label": label,
            "hypothesis": a.get("hypothesis") or "",
            "verification": {
                "method": ver.get("method") or "",
                "findings": ver.get("findings") or "",
                "disproved": ver.get("disproved") or [],
            },
            "confirmation": {
                "holds": conf.get("holds"),
                "confidence": conf.get("confidence") or "",
                "why": conf.get("why") or "",
                "residualRisk": conf.get("residualRisk") or "",
            },
        }

        # 事实性记录：AI 有没有做完确认
        has_confirmation = isinstance(conf, dict) and conf.get("holds") is not None
        has_verification = bool((ver.get("method") or "").strip())

        if not (has_confirmation and has_verification):
            incomplete.append((a.get("parent"), a.get("group"),
                               not has_verification, not has_confirmation))
            # 代码不降级，只标出"这一步没做完"，仍进待确认
            sg["needsCheck"] = True
            missing = []
            if not has_verification:
                missing.append("验证过程")
            if not has_confirmation:
                missing.append("确认结论")
            sg.setdefault("evidence", []).append(
                "AI 未给出" + "、".join(missing))
        else:
            # AI 自己确认过 → 按它说的记录，不做二次判断
            sg["needsCheck"] = not bool(conf.get("holds"))

        applied += 1

    if incomplete:
        print(f"  {len(incomplete)} 条判断的验证/确认不完整：", file=sys.stderr)
        for parent, group, no_ver, no_conf in incomplete[:10]:
            bits = []
            if no_ver:
                bits.append("无验证")
            if no_conf:
                bits.append("无确认")
            print(f"    {parent} / {group}　{'、'.join(bits)}", file=sys.stderr)

    return applied, missed

def main():
    _boot_rules()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root', required=True)
    ap.add_argument('--probe')
    ap.add_argument('--out')
    ap.add_argument('--need-ai', action='store_true',
                    help='只输出需要 AI 判断的，附完整事实')
    ap.add_argument('--answers', help='AI 判断结果 JSON，回填到建议里')
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    probe_dir = Path(args.probe) if args.probe else Path(root) / '_organize' / 'probe'

    probes, units, groups = load_probes(probe_dir)
    nodes = build_nodes(root, probes, units, groups)
    zones = collect_zones(root)

    # 为每组加建议
    total = keep = move = new = 0
    need_check = 0
    new_dirs = {}

    # ── 第一遍：逐组算建议，同时收集"匹配不上"的 ──
    unmatched = []       # [(组名, 内容形态)]

    for n in nodes:
        for g in n["groups"]:
            s = suggest_group(g["name"], n["name"], zones, n["path"])
            g["suggest"] = s
            total += 1

            # 记下既没移动也没新建、且内容形态明确的组
            if s["mode"] == "keep" and s.get("evidence"):
                shape = None
                if s.get("evidence"):
                    ev = s["evidence"][0]
                    for k in ("installer", "media", "image", "code",
                              "document", "archive"):
                        if k in ev:
                            shape = k
                            break
                if shape:
                    unmatched.append((g["name"], shape))

            if s["mode"] == "keep":
                keep += 1
            elif s["mode"] == "move":
                move += 1
            else:
                new += 1
                cat = s["newName"]
                if cat not in new_dirs:
                    new_dirs[cat] = {"name": cat, "parent": s["newParent"],
                                     "count": 0}
                new_dirs[cat]["count"] += 1
            if s["needsCheck"]:
                need_check += 1

    # ── AI 判断环节：挑出代码定不了的 ──
    ai_questions = []
    for n in nodes:
        for g in n["groups"]:
            sg = g["suggest"]
            # 重新算一次形态（suggest 里已经算过，这里从 evidence 反推）
            kind = None
            for ev in (sg.get("evidence") or []):
                for k in ("installer", "media", "image", "code",
                          "document", "archive"):
                    if k in ev:
                        kind = k
                        break
                if kind:
                    break
            shape = {"kind": kind} if kind else None

            why = needs_ai(sg, shape)
            if not why:
                continue
            # 组路径：组名不一定是目录名，按单元列表逐个试
            import os as _os
            gp = None
            cands = []
            if n.get("path"):
                cands.append(_os.path.join(n["path"], g["name"]))
            # 组的 units 字段里可能是真实路径
            for u in (g.get("units") or [])[:5]:
                if isinstance(u, str):
                    cands.append(u if _os.path.isabs(u)
                                 else _os.path.join(n.get("path") or "", u))
            for c in cands:
                if c and _os.path.isdir(c):
                    gp = c
                    break
            q = build_ai_question(g["name"], n["name"], n.get("path"),
                                  sg, shape,
                                  sample_file_names(gp) if gp else [],
                                  trigger=why)
            q["trigger"] = why
            q["triggerText"] = AI_TRIGGERS.get(why, why)
            ai_questions.append(q)

    # ── 第二遍：从未匹配上的组里发现新类别候选 ──
    proposals = propose_new_categories(unmatched)
    if proposals:
        print("\n发现的候选类别（≥2 组共享同一词根）:", file=sys.stderr)
        for stem, info in sorted(proposals.items(),
                                 key=lambda x: -x[1]["count"]):
            kinds = "/".join(sorted(info["kinds"])) or "?"
            print(f"  {stem:16} {info['count']} 组  形态={kinds}",
                  file=sys.stderr)
            print(f"      {'、'.join(info['members'][:3])}",
                  file=sys.stderr)

    # 把候选类别写进输出，供人确认后加入规则包
    proposal_out = {
        stem: {"count": info["count"],
               "kinds": sorted(info["kinds"]),
               "members": info["members"][:10]}
        for stem, info in proposals.items()
    }

    data = {
        "root": root,
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "nodes": nodes,
        "zones": zones,
        "summary": {
            "total": total, "keep": keep, "move": move, "new": new,
            "needCheck": need_check,
        },
        "newDirs": sorted(new_dirs.values(), key=lambda x: -x["count"]),
        "proposedCategories": proposal_out,
        "aiQuestions": ai_questions,
    }

    # --answers：回填 AI 判断
    if args.answers:
        applied, missed = apply_answers(nodes, args.answers)
        print(f"回填 {applied} 组判断", file=sys.stderr)
        if missed:
            print(f"  {missed} 条没找到对应组（已忽略）", file=sys.stderr)

    # --need-ai：只输出问题清单，方便喂给 AI
    if args.need_ai:
        print(json.dumps({"questions": data["aiQuestions"]},
                         ensure_ascii=False, indent=2))
        return 1 if data["aiQuestions"] else 0

    out = Path(args.out) if args.out else Path(root) / '_organize' / 'decisions.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                   encoding='utf-8')

    print(f'已生成: {out}')
    print(f'  {len(nodes)} 节点 / {total} 组')
    print(f'  保持原位 {keep} · 移到已有区 {move} · 建议新建 {new}')
    print(f'  需确认 {need_check} 组')
    if new_dirs:
        print('  建议新建的目录:')
        for d in data["newDirs"]:
            parent = d["parent"] or "根目录"
            print(f'    {parent} / {d["name"]}　({d["count"]} 组)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
