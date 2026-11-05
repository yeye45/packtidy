#!/usr/bin/env python3
"""
signals.py —— 第一步（划分最小单元）的信号采集

只做一件事：给一个目录，输出判断"它是 1 个单元还是 N 个"所需的**事实**。

不做判断，不下结论，不读文件内容。

设计边界（只服务第一步）：
  OK  判断"这些文件是否构成一个不可分割的整体"
  OK  判断"这些子项是否各自独立"
  NO  不判断"该不该动"
  NO  不判断"该不该改名"
  NO  不判断内容主题
  NO  不判断是否重复

用法:
    python signals.py <目录>
    python signals.py <目录> --json
    python signals.py <目录1> <目录2> ...
"""
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime
from safety import SafetyError, assert_no_links, is_reparse

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


BIN_EXT = {".exe", ".dll", ".so", ".dylib", ".sys", ".msi", ".ocx"}
VM_EXT = {".vmdk", ".vmem", ".vmsd", ".nvram", ".scoreboard", ".vmsn", ".vmx"}
ENGINE_EXT = {".pak", ".bundle", ".assets", ".uasset", ".ress", ".resource"}
IMG_EXT = {".jpg", ".jpeg", ".png", ".heic", ".gif", ".webp", ".bmp"}
VID_EXT = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".ts"}
AUD_EXT = {".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg", ".opus"}
ARC_EXT = {".zip", ".7z", ".rar", ".tar", ".gz", ".iso", ".001"}
DOC_EXT = {".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".pdf",
           ".txt", ".md", ".csv", ".epub"}

ENGINE_SUBDIRS = {"il2cpp_data", "streamingassets", "plugins", "managed",
                  "_data", "steamapps", "shaderpacks", "common", "gamedata",
                  "www", "scenario", "resources", "graphics"}
SAVE_DIRS = {"save", "saves", "savedata", "存档", "セーブ"}


def bucket(ext):
    e = ext.lower()
    if e in BIN_EXT: return "bin"
    if e in VM_EXT: return "vm"
    if e in ENGINE_EXT: return "engine"
    if e in IMG_EXT: return "img"
    if e in VID_EXT: return "vid"
    if e in AUD_EXT: return "aud"
    if e in ARC_EXT: return "arc"
    if e in DOC_EXT: return "doc"
    return "other"


def scan(path, max_items=None):
    assert_no_links(path)
    out = []
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    isdir = e.is_dir(follow_symlinks=False)
                    st = e.stat(follow_symlinks=False)
                    reparse = is_reparse(e.path)
                    out.append({
                        "name": e.name, "dir": isdir,
                        "size": 0 if isdir else st.st_size,
                        "mtime": st.st_mtime, "ctime": st.st_ctime,
                        "reparse": reparse,
                    })
                except OSError:
                    raise
                if max_items is not None and len(out) >= max_items:
                    raise SafetyError('扫描达到上限，结果不完整')
    except OSError:
        raise
    return out


def subtree_size(path, limit=None):
    assert_no_links(path)
    total = n = 0
    try:
        def fail(error):
            raise error
        for dp, dns, fns in os.walk(path,onerror=fail):
            dns[:] = [dn for dn in dns if not is_reparse(os.path.join(dp, dn))]
            for fn in fns:
                try:
                    if is_reparse(os.path.join(dp, fn)):
                        continue
                    total += os.path.getsize(os.path.join(dp, fn))
                    n += 1
                except OSError:
                    raise
                if limit is not None and n >= limit:
                    raise SafetyError('统计达到上限，结果不完整')
    except OSError:
        raise
    return total, n


# ═══ A. 结构绑定：命中说明内部不可拆 ═══

def sig_unity(entries):
    dirs = {e["name"].lower() for e in entries if e["dir"]}
    exes = [os.path.splitext(e["name"])[0].lower()
            for e in entries if not e["dir"] and e["name"].lower().endswith(".exe")]
    pairs = [f"{x}.exe + {x}_data/" for x in exes if f"{x}_data" in dirs]
    return {"unity-layout": bool(pairs), "unity-evidence": pairs}


def sig_vm(entries):
    vm = [e["name"] for e in entries
          if not e["dir"] and os.path.splitext(e["name"])[1].lower() in VM_EXT]
    disks = [n for n in vm if n.lower().endswith(".vmdk")]
    ctrl = [n for n in vm if any(n.lower().endswith(x) for x in
                                 (".vmx", ".vmsd", ".nvram", ".vmem"))]
    return {"vm-layout": len(disks) > 0,
            "vm-disk-count": len(disks),
            "vm-control-count": len(ctrl),
            "vm-evidence": f"{len(disks)} vmdk + {len(ctrl)} 控制文件"}


def sig_engine_payload(entries):
    n = sum(1 for e in entries
            if not e["dir"] and os.path.splitext(e["name"])[1].lower() in ENGINE_EXT)
    return {"engine-payload-count": n}


def sig_save_binding(entries):
    saves = [e["name"] for e in entries
             if e["dir"] and e["name"].lower() in SAVE_DIRS]
    return {"save-binding": bool(saves), "save-dirs": saves}


def sig_bin_plus_data(entries):
    has_bin = any(not e["dir"] and os.path.splitext(e["name"])[1].lower() in BIN_EXT
                  for e in entries)
    data_dirs = [e["name"] for e in entries
                 if e["dir"] and e["name"].lower() in ENGINE_SUBDIRS]
    return {"bin-plus-data": has_bin and len(data_dirs) >= 2,
            "data-dirs": data_dirs[:8]}


# ═══ B. 序列连续：命中说明属于一个序列 ═══

def sig_sequence(entries):
    files = [e for e in entries if not e["dir"]]
    groups = {}
    for e in files:
        ext = os.path.splitext(e["name"])[1].lower()
        if ext not in (IMG_EXT | VID_EXT | AUD_EXT | DOC_EXT):
            continue
        stem = os.path.splitext(e["name"])[0]
        # Strip technical tags, retaining season/episode and title numbers.
        stem = re.sub(r'[\[【(][^\]】)]*(?:\d{3,4}[pi]|[xh]26[45]|hevc|av1)[^\]】)]*[\]】)]', '', stem, flags=re.I)
        stem = re.sub(r'\s+(?:\d{3,4}[pi]|[xh]26[45]|hevc|av1)\b.*$', '', stem, flags=re.I).strip()
        match = re.search(r'(?i)(?:s\d{1,2}e|\bep(?:isode)?\s*)(\d{1,4})(?!\d)', stem)
        if match is None:
            match = re.search(r'(?:第\s*)?(\d{1,4})\s*(?:集|话|話|期)?[\])】]?\s*$', stem)
        if match is None:
            continue
        n = int(match.group(1))
        prefix = (stem[:match.start(1)]+'{n}'+stem[match.end(1):]).casefold()
        groups.setdefault((ext, prefix), []).append(n)

    best = None
    for (ext, prefix), nums in groups.items():
        unique = set(nums)
        if len(unique) < 3:
            continue
        lo, hi = min(nums), max(nums)
        span = hi - lo + 1
        if span > 5000:
            continue
        missing = sorted(set(range(lo, hi + 1)) - set(nums))
        cov = len(unique) / span
        cand = {"ext": ext, "prefix": prefix, "count": len(nums),
                "range": f"{lo}-{hi}", "missing": missing[:8],
                "unique-count": len(unique),
                "coverage": round(cov, 3)}
        if best is None or len(nums) > best["count"]:
            best = cand

    if best and best["coverage"] >= 0.7:
        return {"sequence-detected": True, "sequence": best}
    return {"sequence-detected": False}


def sig_volume_parts(entries):
    files = [e["name"] for e in entries if not e["dir"]]
    groups = {}
    for n in files:
        m = re.search(r"(?:part|\.part)(\d+)|\.(\d{3})$", n, re.I)
        if not m:
            continue
        idx = int(m.group(1) or m.group(2))
        base = re.sub(r"(?:\.part\d+|\d{3}$)", "", n, flags=re.I)[:50]
        groups.setdefault(base, []).append(idx)
    hits = []
    for base, idxs in groups.items():
        if len(idxs) >= 2:
            lo, hi = min(idxs), max(idxs)
            hits.append({"base": base, "parts": len(idxs),
                         "range": f"{lo}-{hi}",
                         "missing": sorted(set(range(lo, hi + 1)) - set(idxs))})
    return {"volume-parts": bool(hits), "volume-groups": hits[:5]}


# ═══ C. 到达聚集：命中说明可能是一组 ═══

def sig_batch_arrival(entries):
    if len(entries) < 3:
        return {"batch-arrival": False}
    secs = Counter()
    for e in entries:
        try:
            secs[datetime.fromtimestamp(e["ctime"]).strftime("%Y-%m-%d %H:%M:%S")] += 1
        except (ValueError, OSError):
            pass
    if not secs:
        return {"batch-arrival": False}
    at, n = secs.most_common(1)[0]
    ratio = n / len(entries)
    return {"batch-arrival": ratio >= 0.3,
            "batch-ratio": round(ratio, 3), "batch-at": at}


def sig_time_span(entries):
    ts = [e["mtime"] for e in entries if e["mtime"] > 0]
    if not ts:
        return {"span-days": None}
    try:
        lo, hi = min(ts), max(ts)
        return {"span-days": (datetime.fromtimestamp(hi) - datetime.fromtimestamp(lo)).days,
                "earliest": f"{datetime.fromtimestamp(lo):%Y-%m-%d}",
                "latest": f"{datetime.fromtimestamp(hi):%Y-%m-%d}"}
    except (ValueError, OSError):
        return {"span-days": None}


# ═══ D. 独立性：命中说明可能是多个单元 ═══

def sig_composition(entries):
    files = [e for e in entries if not e["dir"]]
    c = Counter()
    for e in files:
        c[bucket(os.path.splitext(e["name"])[1])] += 1
    real = {k: v for k, v in c.items() if k != "other"}
    return {"file-count": len(files),
            "dir-count": len(entries) - len(files),
            "kind-count": len(real),
            "composition": dict(c.most_common(8))}


def sig_shell(entries, base):
    if len(entries) != 1 or not entries[0]["dir"]:
        return {"is-shell": False}
    parent = os.path.basename(base.rstrip(os.sep))
    if entries[0]["name"] == parent:
        return {"is-shell": True, "shell-inner": entries[0]["name"]}
    return {"is-shell": False}


def sig_junction(entries):
    names = [e["name"] for e in entries if e.get("reparse")]
    return {"reparse-count": len(names), "reparse-names": names[:8]}


def sig_name_content_match(entries, base):
    """第零步：目录名与实际文件名是否一致。

    从文件名提取统一前缀，和目录名比对。
    不一致时不武断下结论，只标记出来让人去看。
    """
    name = os.path.basename(base.rstrip(os.sep)).lower()
    files = [e["name"] for e in entries if not e["dir"]]
    if not files:
        return {"name-content-match": None,
                "_evidence": "本层无文件，无法比对"}

    # 提取每个文件的前缀（去掉编号和扩展名）
    stems = []
    for f in files[:40]:
        s = os.path.splitext(f)[0]
        s = re.sub(r"[\s_\-]*\(?\d+\)?\s*$", "", s)     # 去掉末尾编号
        s = re.sub(r"\d+$", "", s)                      # 去掉纯数字尾
        stems.append(s.strip().lower())
    if not stems:
        return {"name-content-match": None, "_evidence": "无法提取前缀"}

    c = Counter(stems)
    top, topn = c.most_common(1)[0]
    ratio = topn / len(stems)

    # 目录名里有没有这个前缀
    hit = bool(top) and len(top) >= 2 and top in name

    return {
        "name-content-match": hit,
        "content-prefix": top[:40],
        "prefix-ratio": round(ratio, 3),
        "_evidence": (f"文件前缀 '{top[:30]}' 占 {ratio:.0%}；"
                      f"目录名里{'含' if hit else '不含'}该前缀"),
    }


def sig_nested_dup(entries, base):
    """同名嵌套目录：A/A/ 的快速特征。

    不比对哈希（成本高），只报告存在性和各自文件数，
    供下游决定是否需要深度比对。
    """
    if len(entries) != 1 or not entries[0]["dir"]:
        return {"nested-same-name": False}
    parent = os.path.basename(base.rstrip(os.sep))
    inner_name = entries[0]["name"]
    if inner_name != parent:
        return {"nested-same-name": False}

    inner = os.path.join(base, inner_name)
    try:
        assert_no_links(inner)
        with os.scandir(inner) as it:
            inner_items = list(it)
    except OSError:
        inner_items = []
    inner_files = sum(1 for e in inner_items if e.is_file(follow_symlinks=False))
    inner_dirs = len(inner_items) - inner_files

    outer_files = len([e for e in entries if not e["dir"]])
    return {
        "nested-same-name": True,
        "outer-file-count": outer_files,
        "inner-file-count": inner_files,
        "inner-dir-count": inner_dirs,
        "_evidence": (f"内层同名目录有 {inner_files} 文件 / {inner_dirs} 子目录；"
                      f"外层有 {outer_files} 个平级文件"),
    }

SIGNAL_FUNCS = [
    sig_unity, sig_vm, sig_engine_payload, sig_save_binding, sig_bin_plus_data,
    sig_sequence, sig_volume_parts,
    sig_batch_arrival, sig_time_span,
    sig_composition, sig_junction,
]


def analyze(path):
    path = os.path.abspath(path)
    try:
        entries = scan(path)
        size, nfiles = subtree_size(path)
    except (OSError,SafetyError):
        return {'path':path,'dir_name':os.path.basename(path),'empty':None,
                'complete':False,'error':'无法完整读取目录；不能据此判断为空','signals':{}}
    r = {"path": path, "dir_name": os.path.basename(path.rstrip(os.sep)),
         "empty": len(entries) == 0, "complete":True,"signals": {}}
    if not entries:
        return r
    r["size-gb"] = round(size / 2**30, 2)
    r["file-count-total"] = nfiles
    for fn in SIGNAL_FUNCS:
        try:
            r["signals"].update(fn(entries))
        except Exception as e:
            r["signals"][f"_err_{fn.__name__}"] = str(e)
    # 需要 base 路径的信号，单独调用
    for fn in (sig_name_content_match, sig_nested_dup, sig_shell):
        try:
            r["signals"].update(fn(entries, path))
        except Exception as e:
            r["signals"][f"_err_{fn.__name__}"] = str(e)
    return r


def fmt(r):
    L = ["=" * 74,
         f"{r['dir_name']}   {r.get('size-gb', 0)} GB   {r.get('file-count-total', 0)} 文件"]
    if not r.get('complete',True):
        return '\n'.join(L+[r['error']])
    if r["empty"]:
        L.append("  (空目录)")
        return "\n".join(L)

    s = r["signals"]

    # 第零步：名实一致性
    ncm = s.get("name-content-match")
    if ncm is False:
        L.append("  [!! 名实不符]")
        L.append(f"    {s.get('_evidence')}")
        L.append("    → 目录名不可信，一切判断以文件名为准")
    elif ncm is True:
        L.append(f"  [名实一致] 文件前缀 '{s.get('content-prefix')}' "
                 f"占 {s.get('prefix-ratio', 0):.0%}")

    if s.get("nested-same-name"):
        L.append("  [同名嵌套]")
        L.append(f"    内层 {s.get('inner-file-count')} 文件 / "
                 f"{s.get('inner-dir-count')} 子目录")
        L.append(f"    外层 {s.get('outer-file-count')} 个平级文件")
        L.append("    → 必须比哈希才能判断是否重复")

    bind = []
    if s.get("unity-layout"):
        bind.append(f"Unity 布局 {s.get('unity-evidence')}")
    if s.get("vm-layout"):
        bind.append(f"虚拟机 {s.get('vm-evidence')}")
    if s.get("engine-payload-count"):
        bind.append(f"引擎资源 {s['engine-payload-count']} 个")
    if s.get("save-binding"):
        bind.append(f"存档目录 {s.get('save-dirs')}")
    if s.get("bin-plus-data"):
        bind.append(f"二进制+数据目录 {s.get('data-dirs')}")
    if s.get("sequence-detected"):
        q = s["sequence"]
        bind.append(f"连续序列 {q['ext']} {q['range']} "
                    f"({q['count']} 个, 覆盖 {q['coverage']:.0%})")
    for v in s.get("volume-groups", [])[:2]:
        miss = v["missing"] if v["missing"] else "无"
        bind.append(f"分卷 {v['base']} {v['range']} 缺{miss}")

    if bind:
        L.append("  [结构绑定]")
        for b in bind:
            L.append(f"    * {b}")

    if s.get("batch-arrival"):
        L.append(f"  [到达聚集] {s.get('batch-ratio', 0):.0%} 同秒 ({s.get('batch-at')})")
    if s.get("span-days") is not None:
        L.append(f"  [时间跨度] {s['span-days']} 天 "
                 f"({s.get('earliest')} ~ {s.get('latest')})")

    L.append(f"  [构成] {s.get('file-count', 0)} 文件 / "
             f"{s.get('dir-count', 0)} 目录 / {s.get('kind-count', 0)} 种类型")
    if s.get("composition"):
        L.append(f"         {s['composition']}")

    if s.get("is-shell"):
        L.append(f"  [套壳] 内层 {s['shell-inner']}")
    if s.get("reparse-count"):
        L.append(f"  [Junction] {s['reparse-count']} 个 {s.get('reparse-names')}")
    return "\n".join(L)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    as_json = "--json" in sys.argv
    paths = [a for a in sys.argv[1:] if not a.startswith("--")]
    results = []
    for p in paths:
        if not os.path.isdir(p):
            print(f"跳过（不是目录）: {p}")
            continue
        results.append(analyze(p))
    if as_json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for r in results:
            print(fmt(r))
    return 1 if any(not r.get('complete',True) for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
