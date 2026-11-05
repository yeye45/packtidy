#!/usr/bin/env python3
"""压缩包只读清单：不解压，只读文件头，回答"这个包里是什么类型的东西"。

支持 RAR5 / ZIP / RAR4（仅签名判定）。其他格式只报签名。
用法:
  python peek_archive.py <包路径> [--limit 40] [--json]
输出: 条目名 + 大小 + 扩展名构成 + 判定（是否单一种类）
"""
import io
import json
import os
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BUCKETS = {
    ".jpg": "img", ".jpeg": "img", ".png": "img", ".gif": "img", ".webp": "img", ".bmp": "img",
    ".mp4": "vid", ".mkv": "vid", ".avi": "vid", ".mov": "vid", ".wmv": "vid", ".webm": "vid", ".ts": "vid",
    ".mp3": "aud", ".wav": "aud", ".flac": "aud", ".m4a": "aud", ".aac": "aud", ".ogg": "aud",
    ".doc": "doc", ".docx": "doc", ".xls": "doc", ".xlsx": "doc", ".ppt": "doc", ".pptx": "doc", ".pdf": "doc", ".txt": "doc", ".epub": "doc",
    ".zip": "arc", ".7z": "arc", ".rar": "arc", ".iso": "arc",
    ".exe": "bin", ".dll": "bin", ".msi": "bin", ".apk": "bin", ".jar": "bin",
    ".py": "code", ".js": "code", ".json": "code", ".yml": "code", ".html": "code", ".css": "code", ".cpp": "code", ".c": "code", ".h": "code",
    ".db": "data", ".dat": "data", ".cfg": "data", ".ini": "data", ".log": "data", ".sqlite": "data",
    ".assets": "data", ".ress": "data", ".resource": "data", ".bundle": "data", ".pak": "data",
    ".dll": "bin", ".exe": "bin", ".so": "bin", ".dylib": "bin", ".sys": "bin",
    ".aspx": "code", ".cs": "code", ".lua": "code", ".toml": "code", ".xml": "code",
}


def bucket(name):
    ext = os.path.splitext(name)[1].lower()
    return BUCKETS.get(ext, "other")


def vint(f):
    r = 0
    sh = 0
    while True:
        b = f.read(1)
        if not b:
            return None
        b = b[0]
        r |= (b & 0x7F) << sh
        if not (b & 0x80):
            return r
        sh += 7


def rar5_entries(path, limit):
    out = []
    total = 0
    with open(path, "rb") as f:
        f.read(8)  # RAR5 signature
        while True:
            if not f.read(4):
                break
            hsize = vint(f)
            if hsize is None:
                break
            body = f.read(hsize)
            if len(body) < hsize:
                break
            g = io.BytesIO(body)
            htype = vint(g)
            hflags = vint(g) or 0
            if hflags & 0x0001:
                vint(g)
            dsize = vint(g) if (hflags & 0x0002) else 0
            if htype == 2:
                fflags = vint(g) or 0
                usize = vint(g)
                vint(g)  # attrs
                if fflags & 0x0002:
                    vint(g)
                if fflags & 0x0004:
                    g.read(4)
                vint(g)  # comp method
                vint(g)  # host os
                nlen = vint(g) or 0
                raw = g.read(nlen)
                try:
                    name = raw.decode("utf-8")
                except UnicodeDecodeError:
                    name = raw.decode("cp932", "replace")
                total += 1
                if len(out) < limit:
                    out.append((name, usize))
            if dsize:
                f.seek(dsize, 1)
    return out, total


def zip_entries(path, limit):
    import zipfile
    out = []
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        for info in z.infolist()[:limit]:
            out.append((info.filename, info.file_size))
        return out, len(names)


def rar_entries(path, limit):
    """RAR4：优先 rarfile，无 unrar 后端时退回 NoRar 报错。"""
    try:
        import rarfile
    except ImportError:
        return [], None
    try:
        with rarfile.RarFile(path) as rf:
            infos = rf.infolist()
            out = [(i.filename, i.file_size) for i in infos[:limit]]
            return out, len(infos)
    except Exception:
        return [], None


def sevenzip_entries(path, limit):
    """7z：用 py7zr 只读元数据，不解压内容。"""
    try:
        import py7zr
    except ImportError:
        return [], None
    try:
        with py7zr.SevenZipFile(path, mode='r') as z:
            names = z.getnames()
            out = [(n, None) for n in names[:limit]]
            return out, len(names)
    except Exception:
        # 加密头或密码保护，getnames 会失败
        return [], None


def detect(path):
    with open(path, "rb") as f:
        head = f.read(16)
    if head[:6] == b"Rar!\x1a\x07":
        return "rar5" if head[6] == 0x01 else "rar4"
    if head[:4] == b"PK\x03\x04":
        return "zip"
    if head[:6] == b"7z\xbc\xaf\x27\x1c":
        return "7z"
    return "unknown"


def peek(path, limit=40):
    kind = detect(path)
    size = os.path.getsize(path)
    res = {"path": path, "format": kind, "size_gb": round(size / 2 ** 30, 2),
           "entries_sampled": 0, "entries_total": None, "buckets": {}, "verdict": None, "sample": []}
    if kind == "rar5":
        items, total = rar5_entries(path, limit)
    elif kind == "rar4":
        items, total = rar_entries(path, limit)
    elif kind == "zip":
        items, total = zip_entries(path, limit)
    elif kind == "7z":
        items, total = sevenzip_entries(path, limit)
    else:
        res["verdict"] = f"无法解析（{kind}），需外部工具"
        return res

    res["entries_total"] = total
    res["entries_sampled"] = len(items)
    c = Counter(bucket(n) for n, _ in items)
    res["buckets"] = dict(c.most_common())
    res["sample"] = [(n, round(s / 2 ** 20) if s else None) for n, s in items[:12]]

    real = {k: v for k, v in c.items() if k != "other"}
    if not real:
        # 别急着说"无扩展名"——可能是类型表里没收录
        exts = Counter()
        noext = 0
        for n, _ in items:
            e = os.path.splitext(n)[1].lower()
            if e:
                exts[e] += 1
            else:
                noext += 1
        if exts:
            top_ext = ", ".join(f"{e}×{k}" for e, k in exts.most_common(6))
            res["verdict"] = f"未收录类型（扩展名分布：{top_ext}）"
        else:
            res["verdict"] = f"抽样 {len(items)} 条全无扩展名"
    elif len(real) == 1:
        only = next(iter(real))
        res["verdict"] = f"单一构成：{only}"
    else:
        top = max(real, key=real.get)
        share = real[top] / sum(real.values())
        res["verdict"] = f"混合，以 {top} 为主（{share:.0%}）"
    return res


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    limit = 40
    as_json = "--json" in sys.argv
    argv = list(sys.argv[1:])
    if "--limit" in argv:
        i = argv.index("--limit")
        limit = int(argv[i + 1])
        del argv[i:i + 2]
    args = [a for a in argv if not a.startswith("--")]

    results = []
    for p in args:
        if not os.path.isfile(p):
            print(f"跳过（不是文件）: {p}")
            continue
        r = peek(p, limit)
        results.append(r)
        if as_json:
            continue
        print("=" * 70)
        print(f"{os.path.basename(p)}   {r['size_gb']}GB   {r['format']}")
        print(f"  条目: 抽 {r['entries_sampled']} / 共 {r['entries_total']}")
        print(f"  构成: {r['buckets']}")
        print(f"  判定: {r['verdict']}")
        for n, mb in r["sample"]:
            print(f"    - {n}   {mb}MB")
    if as_json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
