#!/usr/bin/env python3
"""
lookup.py —— 拿不准时主动查，而不是猜

原则：**不确定就查证，不凭字面推断。**

实测教训：把 VOCALOID 归进"音乐"，因为名字里有"音源"。
查证后发现它是歌声合成引擎 + 声库，是工具不是成品。

三类查询：
  ① 软件/产品识别    这是什么东西
  ② 游戏/影视识别    什么作品、什么类型、有没有成人内容
  ③ 格式/文件识别    这个扩展名/文件头是什么

设计约束：
  · 只查公开信息，不上传任何文件内容
  · 查询结果带来源，可复核
  · 查不到就说查不到，不用猜测填充
  · 缓存结果，避免重复查询

用法：
    python core/lookup.py search "VOCALOID"
    python core/lookup.py game "Seek Girl V"
    python core/lookup.py batch questions.json --out answers.json
"""
import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
CACHE_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "packtidy" / "lookup"

USER_AGENT = "packtidy/0.1 (local file organizer; metadata lookup only)"
TIMEOUT = 15


# ═══════════════════════════════════════════════════════════
# 已知数据源
#
# 每个源声明：能查什么、返回什么字段、可靠度
# 不内置"猜测"逻辑——查不到就是查不到
# ═══════════════════════════════════════════════════════════
SOURCES = {
    "vndb": {
        "url": "https://api.vndb.org/kana/vn",
        "kind": "post",
        "about": "视觉小说数据库。能查 galgame 的标题、标签、成人分级",
        "reliability": "high",
    },
    "dlsite": {
        "url": "https://www.dlsite.com/maniax/api/",
        "kind": "web",
        "about": "DLsite 公开元数据。能查 RJ 号作品的分类与年龄分级",
        "reliability": "high",
    },
}


def cache_key(kind, query):
    import hashlib
    raw = f"{kind}::{query}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def cache_get(kind, query, max_age_days=30):
    p = CACHE_DIR / f"{cache_key(kind, query)}.json"
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        age = time.time() - data.get("_ts", 0)
        if age > max_age_days * 86400:
            return None
        return data.get("result")
    except (OSError, ValueError):
        return None


def cache_put(kind, query, result):
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        p = CACHE_DIR / f"{cache_key(kind, query)}.json"
        p.write_text(json.dumps(
            {"_ts": time.time(), "kind": kind, "query": query,
             "result": result}, ensure_ascii=False, indent=2),
            encoding="utf-8")
    except OSError:
        pass


# ═══════════════════════════════════════════════════════════
# 查询：VNDB（视觉小说）
# ═══════════════════════════════════════════════════════════

def query_vndb(title, limit=3):
    """查 VNDB。返回匹配的视觉小说及其标签。"""
    cached = cache_get("vndb", title)
    if cached is not None:
        return cached

    payload = {
        "filters": ["search", "=", title],
        "fields": "id,title,alttitle,released,tags{name,rating,category},"
                  "image{sexual,violence}",
        "results": limit,
    }
    req = urllib.request.Request(
        SOURCES["vndb"]["url"],
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                "source": "vndb", "query": title}

    results = []
    for vn in (data.get("results") or []):
        # 标签按 rating 筛，只留 2 分以上的（VNDB 的 rating 是 0-3）
        tags = [t["name"] for t in (vn.get("tags") or [])
                if t.get("rating", 0) >= 2]
        img = vn.get("image") or {}
        # 成人判断：VNDB 的 image.sexual 大于 1.5 基本是成人向
        sexual = img.get("sexual") or 0
        results.append({
            "id": vn.get("id"),
            "title": vn.get("title"),
            "altTitle": vn.get("alttitle"),
            "released": vn.get("released"),
            "tags": tags[:12],
            "sexualRating": round(sexual, 2),
            "isAdult": sexual >= 1.5,
        })

    result = {"ok": True, "source": "vndb", "query": title,
              "results": results,
              "found": len(results) > 0}
    cache_put("vndb", title, result)
    return result


# ═══════════════════════════════════════════════════════════
# 查询：通用搜索（兜底）
# ═══════════════════════════════════════════════════════════

def query_search(term):
    """通用查询。返回可检索的线索，不做结论。

    刻意不内置搜索引擎——信任谁由使用者决定。
    这里只给出"该去哪里查"和"查什么词"。
    """
    cached = cache_get("search", term)
    if cached is not None:
        return cached

    result = {
        "ok": True,
        "source": "suggestion",
        "query": term,
        "note": "未配置搜索引擎。以下是建议的查证途径，不是结论。",
        "where": [
            f"VNDB（视觉小说）：python core/lookup.py vndb \"{term}\"",
            f"DLsite（RJ 号作品）：搜 RJ 号或作品名",
            f"Wikipedia / 官网：搜 \"{term}\" + 开发商名",
            f"Steam 商店页：搜 \"{term}\"，看 Mature Content 描述",
        ],
        "warn": "名字有歧义时不要按字面推断。"
                "例：VOCALOID 叫「音源」但是工具不是成品音乐。",
    }
    cache_put("search", term, result)
    return result


# ═══════════════════════════════════════════════════════════
# 批量：从 AI 问题清单里挑出可查的
# ═══════════════════════════════════════════════════════════

def batch_resolve(questions, out_path=None):
    """给一批问题补充查询结果。

    只查"能查的"——组名像作品名的才查。
    查不到的保持原样，不填猜测内容。
    """
    import re

    resolved = []
    for q in questions:
        group = q.get("group") or ""
        parent = q.get("parent") or ""
        shape = q.get("shape") or {}
        entry = dict(q)

        # 有 RJ 号 → 建议查 DLsite
        if re.search(r"RJ\d{6,8}", group, re.I):
            entry["lookupHint"] = "dlsite"
            entry["lookupKey"] = re.search(r"RJ\d{6,8}",
                                           group, re.I).group(0).upper()
        # 内容是媒体，名字像作品 → 查 VNDB（视觉小说）
        elif shape.get("kind") in ("media", "image") and len(group) >= 2:
            r = query_vndb(group)
            if r.get("found"):
                entry["lookup"] = r
                entry["lookupHint"] = "vndb"
        # 内容是安装包 → 大概率是软件，不查作品库
        elif shape.get("kind") == "installer":
            entry["lookupHint"] = "software"
            entry["note"] = "内容是安装包，可能是软件/工具，不是作品"
        else:
            entry["lookupHint"] = "generic"

        resolved.append(entry)

    if out_path:
        Path(out_path).write_text(
            json.dumps({"resolved": resolved}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"已写入 {out_path}")
    return resolved


def cmd_vndb(args):
    r = query_vndb(args.title, args.limit)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r.get("ok") else 1


def cmd_search(args):
    r = query_search(args.term)
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
    batch_resolve(qs, args.out)
    return 0


def cmd_sources(args):
    print("可用数据源:")
    for name, info in SOURCES.items():
        print(f"  {name:10} {info['about']}")
        print(f"             可靠度: {info['reliability']}")
    print()
    print("设计约束:")
    print("  · 只查公开信息，不上传文件内容")
    print("  · 结果带来源，可复核")
    print("  · 查不到就说查不到")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("vndb", help="查视觉小说数据库")
    s.add_argument("title")
    s.add_argument("--limit", type=int, default=3)
    s.set_defaults(func=cmd_vndb)

    s = sub.add_parser("search", help="给出查证途径（不内置搜索引擎）")
    s.add_argument("term")
    s.set_defaults(func=cmd_search)

    s = sub.add_parser("batch", help="给问题清单补查询结果")
    s.add_argument("questions")
    s.add_argument("--out")
    s.set_defaults(func=cmd_batch)

    s = sub.add_parser("sources", help="列出数据源")
    s.set_defaults(func=cmd_sources)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
