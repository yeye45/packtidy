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
CROSS_RULES = [
    {'keys': ['番剧', '动画', '动漫'],       'to': ['影视', 'Video', '番']},
    {'keys': ['电影', '影片', '剧集', '短剧', 'MV', '录屏'],
                                            'to': ['影视', 'Video']},
    {'keys': ['ASMR', '音声'],              'to': ['ASMR']},
    {'keys': ['galgame', '成人', 'R18'],     'to': ['H_Library']},
    {'keys': ['安装包', '破解', '插件', '绿色版'],
                                            'to': ['软件安装包']},
    {'keys': ['课程', '教程', '教材'],        'to': ['文档', '个人文档']},
    {'keys': ['镜像', '刷机', '装机', '驱动'], 'to': ['系统镜像']},
    {'keys': ['模型', '工作流', '词库', 'LoRA', 'embedding',
              'ComfyUI', '训练'],           'to': ['AI工具']},
    {'keys': ['游戏', 'Minecraft'],          'to': ['游戏']},
    {'keys': ['备份', '还原点'],             'to': ['backup']},
    {'keys': ['VOCALOID', '音源'],           'to': ['VOCALOID']},
]

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
    hits = ['文档', '个人文档', '软件安装包', 'ASMR', 'Picture', 'Doujin',
            'Media', 'Video', '游戏', '影视', 'AI', '模型', '工作流',
            '课程', '电子书', '书籍', '壁纸', '字体', '音声', 'CG',
            'Comic', 'Novel', 'Software', 'Documents']
    return any(h.lower() in name.lower() for h in hits)


def suggest_group(group_name, node_name, zones, node_path=None):
    """给一组算出去向建议。

    返回 {mode, target, newName, newParent, reason, needsCheck}
    """
    # ① 组名包含节点名 → 本目录内部的东西，原地
    if node_name and node_name in group_name:
        return {'mode': 'keep', 'reason': '组名含目录名，属本目录内部',
                'needsCheck': False}

    # ② 节点本身已经是分类目录 → 组在它内部，无需移动
    #   （AI工具 / H_Library / 番 / VOCALOID 这类已经分好类的顶层区）
    if node_path and is_category_node(node_name or ''):
        return {'mode': 'keep',
                'reason': '所在目录已是分类目录，组在内部无需移动',
                'needsCheck': False}

    # ② 命中跨类关键词 → 找已有区
    for rule in CROSS_RULES:
        if any(k in group_name for k in rule['keys']):
            for t in rule['to']:
                # 优先匹配浅层、文件多的目录
                cands = [z for z in zones
                         if t.lower() in z['label'].lower()]
                if cands:
                    cands.sort(key=lambda z: (z['depth'], -z['files']))
                    return {'mode': 'move', 'target': cands[0]['path'],
                            'targetLabel': cands[0]['label'],
                            'reason': '命中跨类关键词',
                            'needsCheck': False}
            # 有关键词但无对应区 → 看白名单
            for cat, info in NEW_CATEGORIES.items():
                if any(k in group_name for k in info['keys']):
                    return {'mode': 'new', 'newName': cat,
                            'newParent': info['parent'],
                            'reason': '盘上没有对应分类，且属常见类型',
                            'needsCheck': True}
            # 都不匹配 → 原地，别折腾
            return {'mode': 'keep',
                    'reason': '有关键词但无匹配分类，保持原位',
                    'needsCheck': False}

    # ③ 默认原地
    return {'mode': 'keep', 'reason': '无明显跨类特征', 'needsCheck': False}


CATEGORY_NODE_NAMES = {
    'AI工具', 'H_Library', '番', 'VOCALOID', '影视', '游戏',
    '系统镜像', '软件安装包', '文档', 'Adobe', 'PPT模板',
    'backup', 'Media', 'Doujin', 'Documents', 'Software',
}


def is_category_node(name):
    """这个顶层区本身就是一个分类容器吗？"""
    if name in CATEGORY_NODE_NAMES:
        return True
    low = name.lower()
    return any(c.lower() == low for c in CATEGORY_NODE_NAMES)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root', required=True)
    ap.add_argument('--probe')
    ap.add_argument('--out')
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

    for n in nodes:
        for g in n["groups"]:
            s = suggest_group(g["name"], n["name"], zones, n["path"])
            g["suggest"] = s
            total += 1
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
    }

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
