#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地反馈摘要导出器 —— 只在本机读写，不联网、不上传、不自动提交。

用途：把 <root>/_organize/ 下的阶段产物（probe/*.json、*.tsv、journal 等）
汇总成一份脱敏的 Markdown 摘要，供你自己检查后手动复制到 GitHub Issues。

边界（刻意设计）：
  - 自动的只有「导出」：读本地文件、脱敏、写一个本地 .md
  - 不自动「上传」：无网络请求、无剪贴板写入、不打开浏览器、
    不调用任何外部命令、不发送任何数据
  - 输出文件留在你自己的磁盘上，是否提交、提交哪几行，完全由你决定

用法：
  python export-feedback.py --root G:/
  python export-feedback.py --root G:/ --out G:/_organize/feedback-summary.md --max-lines 60
"""
import argparse
import datetime
import os
import sys
from pathlib import Path

# 仅使用标准库中的本地文件/字符串设施；本文件不含任何网络或进程调用。
TEXT_EXT = {".json", ".tsv", ".log", ".md", ".txt", ".csv"}
SKIP_NAMES = {"feedback-summary.md", "ui.html", "policy.json"}
MAX_FILE_BYTES = 200_000
MAX_WALK_NAMES = 2000


def letters(n):
    """1 -> A, 27 -> AA"""
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


# 常见通名不脱敏：替换它们会让摘要读不通，而且不含个人信息
COMMON_WORDS = {
    "文件", "文档", "资料", "素材", "备份", "下载", "图片", "视频", "音乐",
    "游戏", "软件", "工具", "模板", "数据", "项目", "工作", "其他", "杂项",
    "新建文件夹", "Desktop", "Downloads", "Documents", "Pictures", "Videos",
    "Music", "Temp", "temp", "tmp", "backup", "Backup", "data", "Data",
    "src", "lib", "bin", "assets", "static", "public", "dist", "build",
}


def collect_names(root, limit=MAX_WALK_NAMES):
    """只读名字，不读文件内容；最多向下两层，避免扫穿整个资料盘。

    跳过通名（COMMON_WORDS）——它们不含隐私，替换后反而让摘要读不懂。
    只收长度 >= 3 且不在通名表里的名字。
    """
    names = set()
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d != "_organize"]
            rel = os.path.relpath(dirpath, root)
            depth = 0 if rel == "." else rel.count(os.sep) + 1
            if depth >= 2:
                dirnames[:] = []
            for n in list(dirnames) + list(filenames):
                stem = Path(n).stem
                if len(stem) < 3:
                    continue
                if stem in COMMON_WORDS or n in COMMON_WORDS:
                    continue
                names.add(n)
                if len(names) >= limit:
                    return names
    except OSError:
        pass
    return names


def build_aliases(names):
    """真实名 -> 稳定占位符；保留扩展名，因为它对判断单元类型有用且不含隐私。"""
    d = f = 0
    out = []
    for n in sorted(names, key=lambda x: (-len(x), x)):
        suffix = Path(n).suffix
        if suffix and len(suffix) <= 10 and not n.startswith("."):
            f += 1
            alias = "file-%02d%s" % (f, suffix)
        else:
            d += 1
            alias = "dir-%s" % letters(d)
        out.append((n, alias))
    return out  # 已按长度降序，长名先替换，避免子串被提前破坏


def redact(text, root, aliases):
    """脱敏。按"先还原转义再替换"的顺序，避免 JSON 里的 \\\\ 双反斜杠漏网。"""
    # JSON 里路径是 C:\\Users\\eryis，正则和字符串替换都要覆盖三种形态
    forms = {
        str(root),                                  # C:\Users\eryis
        str(root).replace("\\", "/"),              # C:/Users/eryis
        str(root).replace("\\", "\\\\"),         # C:\\Users\\eryis（JSON）
    }
    for form in sorted(forms, key=len, reverse=True):
        if form and form != ".":
            text = text.replace(form, "<root>")

    # 再兜底：把 <root> 之外残留的盘符路径也压平
    text = text.replace("\\\\", "/").replace("\\", "/")

    for real, alias in aliases:
        if not real:
            continue
        for form in {real, real.replace("\\", "/"), real.replace("\\", "\\\\")}:
            text = text.replace(form, alias)
    return text


def collect_artifacts(organize):
    found = []
    for dirpath, dirnames, filenames in os.walk(organize):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for n in sorted(filenames):
            if n in SKIP_NAMES or Path(n).suffix.lower() not in TEXT_EXT:
                continue
            p = Path(dirpath) / n
            try:
                if p.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            found.append(p)
    return sorted(found, key=lambda p: str(p).lower())


def main():
    ap = argparse.ArgumentParser(description="本地生成脱敏反馈摘要（不联网、不上传）")
    ap.add_argument("--root", required=True, help="整理任务的根目录，例如 G:/")
    ap.add_argument("--out", default=None, help="输出路径，默认 <root>/_organize/feedback-summary.md")
    ap.add_argument("--max-lines", type=int, default=40, help="每个产物最多保留的行数")
    a = ap.parse_args()

    root = Path(a.root).resolve()
    if not root.is_dir():
        sys.exit("根目录不存在：%s" % root)
    organize = root / "_organize"
    out = Path(a.out).resolve() if a.out else organize / "feedback-summary.md"

    aliases = build_aliases(collect_names(root))
    artifacts = collect_artifacts(organize) if organize.is_dir() else []

    lines = [
        "# 本地反馈摘要（未发送）",
        "",
        "- 生成时间：%s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "- 根目录：`<root>`（已脱敏）",
        "- 产物数量：%d" % len(artifacts),
        "- **本文件由本地脚本生成，未联网、未上传、未提交。** 请自行检查后，",
        "  只复制需要的几行到 GitHub Issues；不满意可以直接删掉本文件。",
        "",
        "脱敏规则：真实目录名 -> `dir-A`…，真实文件名 -> `file-01.ext`…；",
        "扩展名保留，因为它说明单元类型但不包含个人信息。映射表不会写入本文件。",
        "",
    ]
    if not artifacts:
        lines += ["> 未在 `<root>/_organize/` 下找到可导出的产物。",
                  "> 可以手动把相关阶段的 JSON/TSV 片段贴进 Issue 模板。", ""]

    for p in artifacts:
        rel = p.relative_to(organize).as_posix() if organize.is_dir() else p.name
        lines += ["## %s" % redact(rel, root, aliases), ""]
        try:
            raw = p.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as e:
            lines += ["```text", "读取失败：%s" % e, "```", ""]
            continue
        body = raw.splitlines()
        truncated = len(body) > a.max_lines
        body = body[:a.max_lines]
        lines += ["```text", redact("\n".join(body), root, aliases)]
        if truncated:
            lines.append("…（已截断，原文件共更多行）")
        lines += ["```", ""]

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print("已生成：%s" % out)
    print("产物 %d 个 | 脱敏名 %d 个 | 未联网、未上传、未提交。" % (len(artifacts), len(aliases)))
    print("请自己打开文件检查，再手动复制需要的片段到 GitHub Issues。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
