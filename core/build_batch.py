#!/usr/bin/env python3
"""
build_batch.py —— 把盘上所有待处理项汇总成一份决策清单

不依赖 probe/*.json，直接扫盘 + 按规则建议。
一次性生成所有待办，让用户在一屏里看完、改完。
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ══════════════════════════════════════════════════════════
# 每个待处理项：路径、名称、类型、说明、建议、候选
# ══════════════════════════════════════════════════════════

def size_of(p, limit_files=200000):
    total = n = 0
    try:
        if os.path.isfile(p):
            return os.path.getsize(p), 1
        for dp, dns, fns in os.walk(p):
            for fn in fns:
                try:
                    total += os.path.getsize(os.path.join(dp, fn))
                    n += 1
                except OSError:
                    pass
                if n >= limit_files:
                    return total, n
    except OSError:
        pass
    return total, n


def count_top(p):
    try:
        return len(os.listdir(p))
    except OSError:
        return 0


# ══════════════════════════════════════════════════════════
# u 目录的逐项判定表
# ══════════════════════════════════════════════════════════
U_ITEMS = {
    "seek": ("游戏", "8 个 Unity 小游戏（同构发行）", "move", "G:\\游戏"),
    "Game": ("游戏", "多娜多娜 + Rebirth2 两个 galgame", "move", "G:\\游戏"),
    "Minecraft": ("游戏", "MC 启动环境", "move", "G:\\游戏"),
    "saves": ("游戏存档", "6 个世界存档，与 Minecraft\\saves 高度重叠", "review",
              "与 Minecraft\\saves 重叠，需确认保留哪份"),
    "PycharmProjects": ("代码", "4 个 Python 工程（calucater/douban/excel/pythonProject）",
                        "move", "G:\\代码"),
    "PHP编程入门指南源码": ("代码", "PHP 教程配套源码", "move", "G:\\代码"),
    "calucater": ("重复", "与 PycharmProjects\\calucater 13/13 同路径同大小",
                  "delete", "重复副本"),
    "Pycharm-Professional-2022.1": ("软件", "PyCharm Professional 安装目录", "move",
                                    "G:\\文件\\软件安装包"),
    "Pycharm-Professional-2022.1_Protable.7z": ("软件", "同版本便携包", "move",
                                                "G:\\文件\\软件安装包"),
    "RETAKE": ("漫画", "漫画《RE-TAKE》106 张图 + 单行本 docx/pdf", "move", "G:\\漫画"),
    "AutoCAD2024.0.1": ("软件", "AutoCAD 2024 0.1 版安装包", "move",
                        "G:\\文件\\软件安装包"),
    "AdskNLM_20210617": ("软件", "Autodesk 许可破解工具", "move", "G:\\文件\\软件安装包"),
    "AdskNLM_20210617.exe": ("软件", "同上，exe 形态", "move", "G:\\文件\\软件安装包"),
    "AdskNLM.exe": ("软件", "同上", "move", "G:\\文件\\软件安装包"),
    "Maya-2024-2023-Patch.exe": ("软件", "Maya 补丁", "move", "G:\\文件\\软件安装包"),
    "v2rayN-Core": ("软件", "代理客户端", "move", "G:\\文件\\软件安装包"),
    "EasyImageX2.Debug": ("软件", "装机工具", "move", "G:\\文件\\软件安装包"),
    "Google": ("浏览器数据", "Chrome/Edge 用户数据", "move", "G:\\文件\\个人文档"),
    ".mediaInfo": ("存储卡残留", "SQLite 索引，内含不存在的路径", "delete", "无有效内容"),
    "LOST.DIR": ("存储卡残留", "FAT 孤儿簇", "delete", "无有效内容"),
    "Android": ("存储卡残留", "仅 1 个 0 字节 .nomedia 的空骨架", "delete", "无有效内容"),
    "1": ("杂项", "9 个文件横跨实验动画/凝胶电泳/招生pdf/微信照片/ToDesk", "review",
          "内容互不相关，需逐项判"),
    "新建文件夹": ("游戏", "实为 BFBC2 游戏", "move", "G:\\游戏"),
    "Doki Doki Literature Club_CHS.exe": ("游戏", "galgame 本体", "move", "G:\\游戏"),
    "1993大专辩论赛决赛.mp4": ("视频", "辩论赛录像", "move", "G:\\影视"),
    "怒放.mp4": ("视频", "影片", "move", "G:\\影视"),
    "2-【药理三分钟】硝酸甘油：炸药还是救命药？心绞痛怎么发生的？为什么能救命？.mp4":
        ("视频", "科普短片", "move", "G:\\影视"),
    "jdk-18_windows-x64_bin.msi": ("软件", "Java 开发工具包安装包", "move",
                                   "G:\\文件\\软件安装包"),
    "dxwebsetup.exe": ("软件", "DirectX 运行时安装器", "move", "G:\\文件\\软件安装包"),
    "图吧工具箱": ("软件", "32KB 加密片段，无法识别", "review", "加密无法判断"),
    "演示文稿111.pptx": ("文档", "演示文稿", "move", "G:\\文件\\个人文档"),
    "xioahua.pptx": ("文档", "演示文稿", "move", "G:\\文件\\个人文档"),
    "p.pdf": ("文档", "MATLAB 拟合曲线图", "move", "G:\\文件\\个人文档"),
    "新建 Microsoft Word 文档.pdf": ("文档", "MATLAB 拟合曲线图（另一份导出）", "move",
                                     "G:\\文件\\个人文档"),
    "3344.py": ("代码", "Python 脚本", "move", "G:\\代码"),
    "l.py": ("代码", "Python 脚本", "move", "G:\\代码"),
    "q.py": ("代码", "Python 脚本", "move", "G:\\代码"),
    "w.py": ("代码", "Python 脚本", "move", "G:\\代码"),
    "1.bat": ("代码", "批处理脚本", "move", "G:\\代码"),
    "favorites_2022_1_19.html": ("浏览器数据", "书签导出", "move", "G:\\文件\\个人文档"),
    "Microsoft Edge 密码.csv": ("凭据", "明文浏览器密码", "keep", "已标 SECRET，不动"),
    "好萌动漫-NiceMoe - 最新の地址.txt": ("网页残留", "网址清单", "delete", "导流页"),
    "山东省2023年普通高等学校招生考试信息平台 - 报名信息查看.pdf": ("他人材料", "招生信息",
                                                                    "keep", "含个人信息"),
    "附件1：社会实践推介卡 (1).zip": ("文档", "社会实践材料", "move", "G:\\文件\\个人文档"),
    "新建文本文档.txt": ("空文件", "0 字节", "delete", "无内容"),
    "新建文本文档 (2).txt": ("空文件", "0 字节", "delete", "无内容"),
    "新建文本文档 (3).txt": ("空文件", "0 字节", "delete", "无内容"),
    "2328050116  连玺博": ("他人材料", "含姓名", "keep", "已标 SECRET，不动"),
    "2328050117刘守烨": ("他人材料", "117 文件", "keep", "已标 SECRET，不动"),
    "print": ("他人材料", "含三张准考证", "keep", "已标 SECRET，不动"),
    "study": ("他人材料", "他人作业", "keep", "已标 SECRET，不动"),
}


def scan_u(root):
    items = []
    u = os.path.join(root, "u")
    if not os.path.isdir(u):
        return items
    for name in sorted(os.listdir(u)):
        p = os.path.join(u, name)
        info = U_ITEMS.get(name)
        if not info:
            info = ("未分类", "未在判定表中", "review", "需要确认")
        kind, desc, action, target = info
        sz, cnt = size_of(p)
        items.append({
            "path": p, "name": name, "kind": kind, "desc": desc,
            "action": action, "target": target,
            "gb": round(sz / 2**30, 2), "files": cnt,
            "is_dir": os.path.isdir(p),
        })
    return items


def scan_pending(root):
    """_待删除 里的项"""
    items = []
    d = os.path.join(root, "_待删除")
    if not os.path.isdir(d):
        return items
    for name in sorted(os.listdir(d)):
        p = os.path.join(d, name)
        sz, cnt = size_of(p)
        items.append({
            "path": p, "name": name,
            "gb": round(sz / 2**30, 2), "files": cnt,
            "is_dir": os.path.isdir(p),
        })
    return items


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", required=True)
    ap.add_argument("--out")
    args = ap.parse_args()

    root = os.path.abspath(args.root)

    u_items = scan_u(root)
    pending = scan_pending(root)

    # 汇总
    total_move = sum(1 for x in u_items if x["action"] == "move")
    total_delete = sum(1 for x in u_items if x["action"] == "delete")
    total_review = sum(1 for x in u_items if x["action"] == "review")
    total_keep = sum(1 for x in u_items if x["action"] == "keep")

    data = {
        "root": root,
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "u": u_items,
        "pending": pending,
        "summary": {
            "uTotal": len(u_items),
            "move": total_move,
            "delete": total_delete,
            "review": total_review,
            "keep": total_keep,
            "pendingTotal": len(pending),
            "pendingGB": round(sum(x["gb"] for x in pending), 2),
        },
    }

    out = Path(args.out) if args.out else Path(root) / "_organize" / "batch.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"已生成: {out}")
    print(f"  u: {len(u_items)} 项")
    print(f"     移动 {total_move} · 删除 {total_delete} · 待确认 {total_review} · 不动 {total_keep}")
    print(f"  _待删除: {len(pending)} 项 / {data['summary']['pendingGB']} GB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
