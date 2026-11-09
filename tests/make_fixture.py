#!/usr/bin/env python3
"""
make_fixture.py —— 造一份合成探针数据，用于 UI 预览与回归测试

**刻意使用虚构的目录名和内容。** 测试数据不应包含任何真实盘的内容——
那会让仓库泄漏用户数据，也让测试依赖特定场景。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


# 名称全部虚构，覆盖各种判定情形
NODES = [
    ("Downloads", "split", "high", 46,
     "下载落地目录：多个互不相关的独立资源混在同一层，没有共同主题。",
     [
         ("编程课程", 4, "theme"),
         ("游戏安装包", 6, "theme"),
         ("设计软件", 4, "theme"),
         ("系统镜像", 3, "multi-version"),
         ("电子书", 3, "theme"),
     ]),

    ("SampleLibrary", "enter", "high", 240,
     "已建成多级分类的媒体库：Documents / Images / Audio / Software，"
     "Images 下再按尺度交叉分桶。",
     [
         ("Documents 文档类", 40, "theme"),
         ("Images 图像", 120, "theme"),
         ("Audio 音频", 55, "theme"),
         ("Software 软件", 25, "theme"),
     ]),

    ("Staging", "split", "high", 30,
     "临时暂存区：三份来源不同、年份不同的旧内容搬进来就停了。",
     [
         ("教学视频", 7, "theme"),
         ("图片集", 3, "theme"),
         ("下载工具", 7, "theme"),
         ("装机工具", 5, "theme"),
         ("教材课程", 4, "theme"),
         ("杂物", 4, "form-cluster"),
     ]),

    ("ProjectA", "keep", "high", None,
     "完整的工程安装目录：可执行体 + 依赖库 + 运行时资源，结构完整。", []),

    ("Photos2023", "keep", "high", None,
     "手机导入的相册根目录，带系统相册元数据，4195 个文件 / 54.43GB。", []),

    ("Toolchain", "keep", "high", None,
     "工具链整合包，自带运行时 + 39 个基础模型 + 历史输出。", []),

    ("Templates", "keep", "high", None,
     "105 个三位编号主题分类的模板库，14697 文件 / 197.43GB。", []),

    ("LoRAWorkspace", "keep", "high", None,
     "已训练完成的 LoRA 工程工作区，含多个 epoch 权重与训练日志。", []),

    ("Misc", "split", "medium", 53,
     "长期积累的杂物层：散件跨软件包、文档、网页残留、硬件固件等多类。",
     [
         ("软件安装包", 7, "theme"),
         ("网页存档", 7, "form-cluster"),
         ("个人文档", 10, "theme"),
         ("安装包集合", 3, "theme"),
     ]),

    ("VMEncrypted", "keep", "high", None,
     "一台整盘加密的虚拟机，16 个磁盘分片缺一不可。", []),

    ("SystemImages", "rename", "medium", None,
     "两个系统镜像，不同 build，不是重复。其中一个名字零信息需补版本号。", []),

    ("OldBackup", "enter", "medium", 12,
     "旧系统备份快照，多层嵌套，内部节点需要分别判定。",
     [
         ("快照批次 2023-12", 5, "multi-version"),
         ("快照批次 2023-10", 4, "multi-version"),
         ("配置导出", 3, "form-cluster"),
     ]),
]

ZONES = [
    "Downloads", "SampleLibrary", "Staging", "ProjectA", "Photos2023",
    "Toolchain", "Templates", "LoRAWorkspace", "Misc", "SystemImages",
    "OldBackup", "Study", "Study/Languages", "Study/Tools",
    "Media", "Media/Videos", "Media/Images",
    "Samples", "Samples/Text", "Samples/Binary",
]


def main():
    base = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        tempfile.mkdtemp(prefix="packtidy-fixture-"))
    root = base / "root"

    # 先清空——重复运行不能叠加旧数据
    if root.exists():
        import shutil
        shutil.rmtree(root)

    probe = root / "_organize" / "probe"
    probe.mkdir(parents=True, exist_ok=True)

    # 候选去向目录。**不写探针**——它们不是待整理的节点，
    # 是"可以往这里移"的目标。coverage 会因此报这几项遗漏，
    # 那是 fixture 的预期状态，不是真遗漏。
    #
    # 想让 coverage 干净，就别拿 fixture 当真实盘测。
    for z in ZONES:
        p = root / z.replace("/", os.sep)
        p.mkdir(parents=True, exist_ok=True)
        (p / "sample.txt").write_text("x", encoding="utf-8")

    total_groups = 0
    for name, action, conf, units, what, groups in NODES:
        path = root / name
        path.mkdir(parents=True, exist_ok=True)
        (path / "sample.txt").write_text("x", encoding="utf-8")

        # S1
        (probe / f"probe_{abs(hash(name)) & 0xFFFFFF:06x}.json").write_text(
            json.dumps({
                "path": str(path), "action": action, "confidence": conf,
                "is_unit": "whole",
                "actual": {"what": what},
                "unit_why": "依据：" + what[:80],
                "rename_to": None,
            }, ensure_ascii=False, indent=2), encoding="utf-8")

        safe = name.replace("\\", "_").replace("/", "_")

        # S2
        if units:
            (probe / f"units_{safe}.json").write_text(
                json.dumps({"path": str(path), "total_units": units},
                           ensure_ascii=False), encoding="utf-8")

        # S3
        if groups:
            (probe / f"group_{safe}.json").write_text(
                json.dumps({
                    "path": str(path),
                    "total_units": units,
                    "groups": [{
                        "name": gname,
                        "units": [f"unit-{i}" for i in range(n)],
                        "why": "同一来源、同一用途、同一批次，拆开各自仍可用。",
                        "relation": rel,
                        "confidence": "high" if rel != "form-cluster" else "medium",
                    } for gname, n, rel in groups],
                    "solo_units": ["单独成项的示例"],
                    "uncertain": [{"unit": "无法判定示例", "why": "名字零信息"}],
                }, ensure_ascii=False, indent=2), encoding="utf-8")
            total_groups += len(groups)

    print(f"fixture root: {root}")
    print(f"  {len(NODES)} 节点 / {total_groups} 组 / {len(ZONES)} 候选去向")
    return 0


if __name__ == "__main__":
    sys.exit(main())
