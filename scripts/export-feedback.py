#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地反馈摘要导出器。

默认只输出产物类型、数量和有限的结构化状态枚举。不会把阶段文件中的任意
说明、路径、文件名、标记或原始行复制到摘要。脚本只在本地读取和写入，不联网。

用法：
  python export-feedback.py --root /mnt/disk
  python export-feedback.py --root /mnt/disk --out G:/_organize/feedback-summary.md
"""
import argparse
import collections
import datetime
import json
import os
import stat
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path

TEXT_EXT = {".json", ".tsv", ".log", ".md", ".txt", ".csv"}
SKIP_NAMES = {"feedback-summary.md", "ui.html", "policy.json"}
MAX_FILE_BYTES = 200_000

# 仅这些固定状态值可进入输出。键和值都来自程序定义，不回显输入中的自由文本。
SAFE_ENUMS = {
    "action": {"keep", "enter", "split", "rename", "merge", "ask", "uncertain"},
    "status": {"pending", "done", "completed", "applied", "undone", "failed",
               "success", "error", "skipped", "blocked"},
    "confidence": {"high", "medium", "low"},
    "kind": {"file", "dir", "directory", "container", "whole", "unclear",
             "empty", "code", "archive", "duplicate", "identical", "prefix",
             "pair", "protected", "nestedallow", "nesteddeny", "no_rule_match",
             "cross_rule", "reparse_point", "symlink"},
    "type": {"file", "dir", "directory", "container", "whole", "unclear",
             "empty", "code", "archive", "duplicate", "identical", "prefix",
             "pair", "protected", "nestedallow", "nesteddeny", "no_rule_match",
             "cross_rule", "reparse_point", "symlink"},
    "signal": {"protected", "nestedallow", "nesteddeny", "no_rule_match",
               "cross_rule", "duplicate", "identical", "prefix", "pair",
               "secret", "foreign", "workspace", "no_touch", "ask"},
    "rule": {"protected", "nestedallow", "nesteddeny", "no_rule_match",
             "cross_rule", "duplicate", "identical", "prefix", "pair"},
    "result": {"keep", "enter", "split", "rename", "merge", "ask", "uncertain",
               "success", "failed", "blocked", "skipped", "verified"},
}

DISPLAY_ENUM = {
    "nestedallow": "nestedAllow",
    "nesteddeny": "nestedDeny",
    "no_rule_match": "no_rule_match",
    "cross_rule": "cross_rule",
    "no_touch": "NO_TOUCH",
    "reparse_point": "reparse_point",
}


def is_reparse(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def collect_artifacts(organize):
    """List small supported artifacts without exposing their names in output."""
    found = []
    if not organize.is_dir():
        return found
    for dirpath, dirnames, filenames in os.walk(organize, followlinks=False):
        safe_dirs = []
        for name in dirnames:
            try:
                if name != '__pycache__' and not is_reparse(Path(dirpath)/name):
                    safe_dirs.append(name)
            except OSError:
                continue
        dirnames[:] = safe_dirs
        for name in filenames:
            if name in SKIP_NAMES or Path(name).suffix.lower() not in TEXT_EXT:
                continue
            path = Path(dirpath) / name
            try:
                if is_reparse(path) or path.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            found.append(path)
    return sorted(found, key=lambda p: str(p).lower())


def safe_enum_counts(data):
    """Return counts of allowlisted status values found in structured JSON."""
    counts = collections.Counter()
    stack = [(None, data)]
    visited = 0
    while stack and visited < 50_000:
        key, value = stack.pop()
        visited += 1
        normalized_key = str(key).casefold() if key is not None else ""
        allowed = SAFE_ENUMS.get(normalized_key)
        if allowed:
            values = value if isinstance(value, list) else [value]
            for item in values:
                if isinstance(item, str):
                    normalized_value = item.casefold()
                    if normalized_value in allowed:
                        counts[(normalized_key, normalized_value)] += 1
        if isinstance(value, dict):
            stack.extend(value.items())
        elif isinstance(value, list):
            stack.extend((None, item) for item in value)
    return counts


def summarize(artifacts):
    """Collect safe aggregate metrics; never retain arbitrary input strings."""
    type_counts = collections.Counter()
    enum_counts = collections.Counter()
    json_valid = json_invalid = text_lines = tsv_rows = 0

    for path in artifacts:
        suffix = path.suffix.lower()
        kind = "JSON" if suffix == ".json" else "TSV" if suffix == ".tsv" else "文本"
        type_counts[kind] += 1
        try:
            if suffix == ".json":
                data = json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
                json_valid += 1
                enum_counts.update(safe_enum_counts(data))
            elif suffix == ".tsv":
                with path.open("r", encoding="utf-8-sig", errors="replace") as stream:
                    tsv_rows += sum(1 for _ in stream)
            else:
                with path.open("r", encoding="utf-8-sig", errors="replace") as stream:
                    text_lines += sum(1 for _ in stream)
        except (OSError, ValueError, RecursionError):
            if suffix == ".json":
                json_invalid += 1

    return type_counts, enum_counts, json_valid, json_invalid, text_lines, tsv_rows


def main():
    ap = argparse.ArgumentParser(description="本地生成仅含安全统计的反馈摘要（不联网、不上传）")
    ap.add_argument("--root", required=True, help="整理任务的根目录")
    ap.add_argument("--out", default=None, help="输出路径，默认 <root>/_organize/feedback-summary.md")
    # 兼容旧命令行；输出是固定短摘要，不再截取原始输入行。
    ap.add_argument("--max-lines", type=int, default=40, help=argparse.SUPPRESS)
    args = ap.parse_args()

    root = Path(os.path.abspath(args.root))
    if not root.is_dir():
        sys.exit("根目录不存在")
    organize = root / "_organize"
    out = Path(os.path.abspath(args.out)) if args.out else organize / "feedback-summary.md"
    for path in (organize, out):
        if any(os.path.lexists(p) and is_reparse(p) for p in (path, *path.parents)):
            ap.error('反馈产物或输出路径含链接或重解析点')
    artifacts = collect_artifacts(organize)
    type_counts, enum_counts, json_valid, json_invalid, text_lines, tsv_rows = summarize(artifacts)

    lines = [
        "# 本地反馈摘要（未发送）",
        "",
        "- 生成时间：%s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "- 摘要策略：仅输出类型、数量和受控状态枚举；不包含输入文本、路径或文件名。",
        "- 产物数量：%d" % len(artifacts),
        "- **本文件由本地脚本生成，未联网、未上传、未提交。**",
        "",
        "## 产物类型与数量",
        "",
    ]
    if type_counts:
        for kind in ("JSON", "TSV", "文本"):
            if type_counts[kind]:
                lines.append("- %s：%d" % (kind, type_counts[kind]))
    else:
        lines.append("- 无可统计的产物")
    lines += [
        "- JSON 解析成功：%d；失败：%d" % (json_valid, json_invalid),
        "- TSV 行数：%d；文本行数：%d（只计数，未复制内容）" % (tsv_rows, text_lines),
        "",
        "## 受控状态与规则信号",
        "",
    ]
    if enum_counts:
        for (key, value), count in sorted(enum_counts.items()):
            label = DISPLAY_ENUM.get(value, value)
            lines.append("- %s=%s：%d" % (key, label, count))
    else:
        lines.append("- 未找到允许输出的状态或规则信号")
    lines.append("")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print("已生成本地统计摘要。")
    print("产物 %d 个 | 未联网、未上传、未提交。" % len(artifacts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
