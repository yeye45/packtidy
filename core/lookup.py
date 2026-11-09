#!/usr/bin/env python3
"""
lookup.py —— 查证线索，不是查询实现

**职责边界**：
  代码  告诉 AI「该去哪查、搜什么词」，不代替它去查
  AI    用自己的搜索/浏览工具实际查证，然后确认判断

为什么这样分：
  · AI 本来就有搜索能力，代码发 HTTP 请求是重复劳动
  · 内置数据源必然片面——VNDB 只覆盖视觉小说，
    DLsite 只覆盖 RJ 号作品，其它内容一概查不到
  · 硬编码"只信这两个站"是把自己的判断强加给使用者

所以这个文件只做三件事：
  ① 给出可查的数据源清单（不预设只信谁）
  ② 从组名/内容里提取可检索的关键词
  ③ 生成具体的查询建议——去哪查、搜什么

实际查询由 AI 完成，结果通过 --answers 回填。

用法：
    python core/lookup.py sources                 列出可查的数据源
    python core/lookup.py suggest "某组名"         给出查询建议
    python core/lookup.py batch questions.json    批量生成线索
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


# ═══════════════════════════════════════════════════════════
# 可查的数据源清单
#
# 不预设"只信这些"——这是给 AI 的参考，不是白名单。
# 每项说明：能查什么、怎么查、局限在哪
# ═══════════════════════════════════════════════════════════
SOURCE_GUIDE = {
    "通用搜索": {
        "how": "搜「<名称> + 开发商/发行商」或「<名称> + 是什么」",
        "good_for": "任何拿不准的东西",
        "limit": "结果质量取决于关键词；同名不同物很常见",
    },
    "Wikipedia / 官网": {
        "how": "搜「<名称> site:wikipedia.org」或直接看开发商官网",
        "good_for": "软件、引擎、产品类",
        "limit": "小众作品可能没有条目",
    },
    "VNDB": {
        "how": "vndb.org 搜标题，看 tags 与 image 分级",
        "good_for": "视觉小说 / galgame",
        "limit": "只收视觉小说，ACT / RPG Maker / 同人小游戏查不到",
    },
    "DLsite": {
        "how": "按 RJ 号直接访问，或搜作品名",
        "good_for": "RJ 号作品（同人游戏、音声、CG 集）",
        "limit": "需要 RJ 号或准确的作品名",
    },
    "Steam 商店页": {
        "how": "store.steampowered.com 搜名称，看 Mature Content 描述",
        "good_for": "上架 Steam 的游戏",
        "limit": "下架或地区限制的看不到",
    },
    "Bangumi / 番组计划": {
        "how": "bgm.tv 搜作品名",
        "good_for": "动画、漫画、游戏的中文条目",
        "limit": "偏二次元，通用软件没有",
    },
}


def extract_search_terms(group_name, sample_files=None):
    """从组名和样例文件里提取可检索的关键词。

    返回按可靠度排序的候选搜索词。
    """
    terms = []

    # ① RJ 号最精确
    rj = re.search(r"RJ\d{6,8}", group_name, re.I)
    if rj:
        terms.append({"term": rj.group(0).upper(), "precision": "exact",
                      "note": "RJ 号可直接定位作品"})

    # ② 括号里的原文名往往是官方标题
    for m in re.finditer(r"[（(]([^）)]{2,40})[）)]", group_name):
        inner = m.group(1).strip()
        if inner and not re.fullmatch(r"[\d\s\.\-]+", inner):
            terms.append({"term": inner, "precision": "high",
                          "note": "括号内的名称通常是原名或副标题"})

    # ③ 去掉版本号/汉化标记后的主体
    cleaned = re.sub(r"[\[【][^\]】]*[\]】]", "", group_name)
    cleaned = re.sub(r"(汉化|中文|简体|繁体|无修|破解|绿色|整合|合集|版)",
                     "", cleaned)
    # 整段删除编号（RJ 号、纯数字），不要留下 "RJ" 这种残渣
    cleaned = re.sub(r"\bRJ\s*\d{6,8}\b", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\bv?\d+[\d\.]*\b", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" -_·,，")
    if len(cleaned) >= 2 and cleaned != group_name:
        terms.append({"term": cleaned, "precision": "medium",
                      "note": "去掉版本/汉化标记后的主体"})

    # ④ 原样
    if group_name and len(group_name) >= 2:
        terms.append({"term": group_name, "precision": "medium",
                      "note": "原名"})

    # 去重，保留最高精度
    seen = {}
    for t in terms:
        k = t["term"].lower()
        if k not in seen:
            seen[k] = t
    return sorted(seen.values(),
                  key=lambda x: {"exact": 0, "high": 1,
                                 "medium": 2}.get(x["precision"], 9))


def build_suggestion(group_name, shape=None, sample_files=None):
    """给一组生成查证建议。

    返回：搜什么、去哪查、为什么值得查。
    不做结论。
    """
    terms = extract_search_terms(group_name, sample_files)
    kind = (shape or {}).get("kind")

    # 按内容形态推荐优先查哪里
    if re.search(r"RJ\d{6,8}", group_name, re.I):
        priority = ["DLsite", "通用搜索"]
        why = "有 RJ 号，是 DLsite 作品"
    elif kind in ("media", "image"):
        priority = ["VNDB", "Steam 商店页", "Bangumi / 番组计划", "通用搜索"]
        why = "内容是媒体/图像，可能是作品"
    elif kind == "installer":
        priority = ["Wikipedia / 官网", "通用搜索"]
        why = "内容是安装包，可能是软件或工具而不是作品"
    elif kind == "code":
        priority = ["Wikipedia / 官网", "通用搜索"]
        why = "内容是源码，可能是开源项目"
    else:
        priority = ["通用搜索", "Wikipedia / 官网", "Bangumi / 番组计划"]
        why = "内容形态不明，从通用搜索开始"

    return {
        "group": group_name,
        "why": why,
        "searchTerms": terms[:4],
        "tryFirst": priority,
        "guides": {name: SOURCE_GUIDE[name]
                   for name in priority if name in SOURCE_GUIDE},
        "reminder": "查到的是参考，不是结论。结合文件内容和目录上下文判断。",
    }


def cmd_sources(args):
    print("可查的数据源（参考，不是白名单）:\n")
    for name, info in SOURCE_GUIDE.items():
        print(f"  {name}")
        print(f"      怎么查：{info['how']}")
        print(f"      适合：  {info['good_for']}")
        print(f"      局限：  {info['limit']}")
        print()
    print("实际查询由 AI 用自己的搜索/浏览工具完成。")
    print("本工具只给出「去哪查、搜什么」，不代替查询。")
    return 0


def cmd_suggest(args):
    shape = {"kind": args.shape} if args.shape else None
    r = build_suggestion(args.group, shape)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0


def cmd_batch(args):
    try:
        data = json.loads(Path(args.questions).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        print(f"无法读取: {exc}", file=sys.stderr)
        return 1

    qs = data.get("questions") if isinstance(data, dict) else data
    if not isinstance(qs, list):
        print("需要 questions 数组", file=sys.stderr)
        return 1

    out = []
    for q in qs:
        entry = dict(q)
        entry["lookupGuide"] = build_suggestion(
            q.get("group", ""), q.get("shape"),
            q.get("sampleFiles"))
        out.append(entry)

    payload = {"questions": out}
    if args.out:
        Path(args.out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"已写入 {args.out}  ({len(out)} 组)")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("sources", help="列出可查的数据源")
    s.set_defaults(func=cmd_sources)

    s = sub.add_parser("suggest", help="给出某组的查证建议")
    s.add_argument("group")
    s.add_argument("--shape", help="内容形态：installer/media/image/code")
    s.set_defaults(func=cmd_suggest)

    s = sub.add_parser("batch", help="给问题清单批量生成查证建议")
    s.add_argument("questions")
    s.add_argument("--out")
    s.set_defaults(func=cmd_batch)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
