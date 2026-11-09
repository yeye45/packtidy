#!/usr/bin/env python3
"""
setup.py —— 整理前的一次性确认

在派第一个探针之前跑一次。目的：把"这个盘该怎么分"问清楚，
避免做到一半发现分类体系不对，返工三次。

实测教训：
  第一轮  影视 = 电影 + 短剧 + 科普片
  你说    科普片该进学习资料
  第二轮  建了学习资料
  你说    影视和娱乐视频要分开
  第三轮  又拆

根因不是判断错，是**没有在最开始确认分类体系**。

用法：
    python core/setup.py ask /path             生成要问的问题
    python core/setup.py apply /path 答案.json  写进 policy.json
    python core/setup.py show /path            看当前配置
"""
import argparse
import json
import os
import sys
from pathlib import Path

from executor import atomic_write
from rules import load_root_policy
from safety import SafetyError, assert_no_links, normal, within

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROBE = Path(__file__).resolve().parent


# ═══════════════════════════════════════════════════════════
# 要问的问题
#
# 只问真正影响整理的，不问能自己看出来的。
# ═══════════════════════════════════════════════════════════
QUESTIONS = [
    {
        "id": "adult_separation",
        "q": "成人内容和普通内容要不要分开？",
        "why": "影响最大。混在一起的话，找普通内容会不断撞见成人内容；"
               "分开则要多一个顶层目录。",
        "options": [
            "分开，成人内容独立成区",
            "混在一起，不介意",
            "分开，但只在特定类型里（如音声/游戏分开，图片不分开）",
        ],
        "affects": ["adult_audio", "adult_game", "adult_image"],
    },
    {
        "id": "video_split",
        "q": "视频要不要按用途分？",
        "why": "影视（电影/剧集）、学习资料（课程/科普）、娱乐视频（B站/录屏）"
               "三者都是视频，但找的时候目的完全不同。",
        "options": [
            "分开：影视 / 学习资料 / 娱乐视频",
            "只分影视和其他",
            "全放一起",
        ],
        "affects": ["video_series", "video_movie", "video_course", "video_clip"],
    },
    {
        "id": "game_split",
        "q": "游戏要不要按类型分？",
        "why": "按引擎分（RPG Maker / Unity / 其他）方便找同类；"
               "按题材分（ACT/GAL/SLG）方便按喜好找；不分则全在一个目录。",
        "options": [
            "按题材分（ACT / GAL / SLG 等）",
            "按引擎分（RPG Maker / Unity / 其他）",
            "不分，全在一个目录",
        ],
        "affects": ["game"],
    },
    {
        "id": "tool_vs_product",
        "q": "「工具」和「成品」要不要分开？",
        "why": "音源库（VOCALOID 声库）是工具，BGM 是成品；"
               "安装包是工具，装好的软件是成品。混在一起找起来会乱。",
        "options": [
            "分开",
            "不分开",
        ],
        "affects": ["voice_synth", "music", "software_installer",
                    "system_image", "install_tool"],
    },
    {
        "id": "depth_preference",
        "q": "目录层级偏好？",
        "why": "影响新建分类时放在哪一层。浅层好找，深层好归类。",
        "options": [
            "最多两层（顶层 / 分类）",
            "最多三层（顶层 / 大类 / 小类）",
            "不限制，按内容复杂度定",
        ],
        "affects": [],
    },
    {
        "id": "secret_handling",
        "q": "凭据类文件（密码、私钥、备份码）怎么处理？",
        "why": "这类文件不该被移动到别处，也不该进日志。",
        "options": [
            "标记后完全不动",
            "集中到一个加密目录",
            "我自己管，工具别碰",
        ],
        "affects": [],
    },
    {
        "id": "no_go_zones",
        "q": "有没有明确不想让工具碰的地方？",
        "why": "比如正在做的项目、其他人的资料、工作区。"
               "这些会标成 NO_TOUCH / FOREIGN / WORKSPACE。",
        "options": "自由填写路径，或留空",
        "affects": [],
    },
]


def find_policy(root):
    return Path(root) / "_organize" / "policy.json"


def cmd_ask(args):
    root = args.root
    existing = {}
    p = find_policy(root)
    if p.is_file():
        try:
            existing = json.loads(p.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            pass

    done = existing.get("setup") or {}

    out = {"root": root, "questions": [], "alreadyAnswered": list(done.keys())}
    for q in QUESTIONS:
        if q["id"] in done:
            continue
        out["questions"].append(q)

    if not out["questions"]:
        print("所有问题都已回答。要重新问，先删掉 policy.json 里的 setup 段。")
        return 0

    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_apply(args):
    try:
        answers = json.loads(Path(args.answers).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        print(f"答案文件无法读取: {exc}", file=sys.stderr)
        return 1

    if not isinstance(answers, dict) or not isinstance(answers.get("setup", answers), dict):
        print("答案文件需要 JSON 对象", file=sys.stderr)
        return 1
    answers = answers.get("setup", answers)
    try:
        root = Path(normal(args.root))
        assert_no_links(root)
        if not root.is_dir():
            raise SafetyError("整理根目录不存在")
        policy = load_root_policy(root)
        if not isinstance(policy.get("setup", {}), dict):
            raise SafetyError("现有 policy 的 setup 必须是对象")
        if "no_go_zones" in answers:
            zones = answers["no_go_zones"]
            if isinstance(zones, str):
                zones = [zones] if zones.strip() else []
            if not isinstance(zones, list) or any(not isinstance(p, str) or not p.strip() for p in zones):
                raise SafetyError("禁区必须是路径数组")
            if any(not within(root/path, root) for path in zones):
                raise SafetyError("禁区路径必须位于整理根目录内")
            answers["no_go_zones"] = zones
    except (OSError, ValueError) as exc:
        print(f"无法保存整理设置: {exc}", file=sys.stderr)
        return 1

    setup = policy.get("setup") or {}
    setup.update(answers)
    policy["setup"] = setup

    # 把关键回答转成实际配置
    if setup.get("adult_separation", "").startswith("分开"):
        mapping = policy.setdefault("mapping", {})
        for cat in ("adult_audio", "adult_game", "adult_image"):
            if not mapping.get(cat):
                mapping[cat] = "成人内容"
        policy["_adultSeparated"] = True

    if setup.get("video_split", "").startswith("分开：影视"):
        mapping = policy.setdefault("mapping", {})
        mapping.setdefault("video_movie", "影视")
        mapping.setdefault("video_series", "动画")
        mapping.setdefault("video_course", "学习资料")
        mapping.setdefault("video_clip", "娱乐视频")

    if setup.get("tool_vs_product", "") == "分开":
        mapping = policy.setdefault("mapping", {})
        mapping.setdefault("voice_synth", "歌声合成")
        mapping.setdefault("music", "音乐")
        mapping.setdefault("software_installer", "软件安装包")
        mapping.setdefault("install_tool", "装机工具")

    no_go = setup.get("no_go_zones")
    if isinstance(no_go, list):
        policy["no_go_zones"] = no_go

    p = find_policy(root)
    atomic_write(p, json.dumps(policy, ensure_ascii=False, indent=2).encode("utf-8"))

    print(f"已写入 {p}")
    print(f"  回答了 {len(setup)} 个问题")
    if policy.get("mapping"):
        print(f"  映射 {len(policy['mapping'])} 个类别")
    return 0


def cmd_show(args):
    p = find_policy(args.root)
    if not p.is_file():
        print("还没有 policy.json。先跑 setup.py ask")
        return 0
    data = json.loads(p.read_text(encoding="utf-8-sig"))
    setup = data.get("setup") or {}
    if not setup:
        print("policy.json 里没有 setup 段（分类体系未确认）")
        return 0

    print(f"配置: {p}")
    print()
    for q in QUESTIONS:
        v = setup.get(q["id"])
        if v is None:
            print(f"  {q['q']}　未回答")
        else:
            print(f"  {q['q']}")
            print(f"    → {v}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("ask", help="生成要问的问题")
    s.add_argument("root")
    s.set_defaults(func=cmd_ask)

    s = sub.add_parser("apply", help="把答案写进 policy.json")
    s.add_argument("root")
    s.add_argument("answers")
    s.set_defaults(func=cmd_apply)

    s = sub.add_parser("show", help="看当前配置")
    s.add_argument("root")
    s.set_defaults(func=cmd_show)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
