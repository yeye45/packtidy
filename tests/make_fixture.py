#!/usr/bin/env python3
"""造一份合成探针数据，用于 UI 预览与回归测试（不依赖任何真实盘）。"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

NODES = [
    ("AI工具", "enter", "high", 108,
     "一个 AI 生成流水线的一站式堆放处，339.64GB / 86309 文件，"
     "13 个顶层项混着五种形态：可运行整合包环境、模型权重、工作流、训练资产。",
     [
         ("ComfyUI 图像生成运行环境（便携包 + 秋叶整合包）", 2, "multi-version"),
         ("语音合成与转换工具链（So-VITS-SVC / VITS / MoeGoe / UVR5 人声分离）", 4, "theme"),
         ("模型权重库（SD 六类 + YaguruMagiku 底模 + FLUX 配套件 + Whisper 家族）", 9, "theme"),
         ("视频与动画生成（AnimateDiff / SVD / Wan / CogVideo / 数字人）", 23, "theme"),
         ("人像写真与人物一致性（壁纸、肖像、姿势、多副本）", 12, "theme"),
         ("电商与商业场景出图（换装试衣、产品、室内、海报）", 19, "theme"),
         ("换脸、转绘与风格迁移（万物迁移 / Kontext / FusionX / 风格化）", 17, "theme"),
         ("修图与增强（放大、去噪、局部重绘）", 11, "theme"),
         ("LoRA 训练（数据集、配置、脚本）", 3, "theme"),
         ("综合合集与入门教程", 7, "form-cluster"),
     ]),
    ("Adobe", "enter", "high", 182,
     "专业设计软件安装包库：Adobe CC 跨 2017-2021 五个年代 × win/mac 双平台，"
     "外加 Audition 2025 与 Autodesk AutoCAD 四版链。",
     [
         ("Adobe 2017 Windows 发行包", 12, "theme"),
         ("Adobe 2018 Windows 发行包", 14, "theme"),
         ("Adobe 2019 Windows 发行包", 15, "theme"),
         ("Adobe 2020 Windows 发行包（rar + exe 双封装）", 33, "multi-version"),
         ("Adobe 2021 Windows 发行包", 28, "theme"),
         ("Adobe 2018 macOS 发行包", 14, "theme"),
         ("Adobe 2019 macOS 发行包", 16, "theme"),
         ("Adobe 2020 macOS + Acrobat Mac", 17, "theme"),
         ("Adobe 2021 macOS 发行包", 23, "theme"),
         ("散装单体（Captivate + Fresco）", 2, "form-cluster"),
         ("Acrobat Windows 两代", 2, "multi-version"),
         ("AutoCAD 版本链 + 绿色版（非 Adobe 出品，误归本区）", 5, "form-cluster"),
     ]),
    ("番", "enter", "high", 98,
     "番剧库：81 个作品目录按发布组命名，外加嵌套的 番\\番\\ 6 作、"
     "Video 里的 8 个零散片、5 个错放的成人音声包。",
     [
         ("失业魔王（同一部作品被拆成两处，集数互补）", 2, "episode-complement"),
         ("为美好的世界献上祝福！（同 IP 三个来源）", 3, "multi-version"),
         ("哥布林杀手（S1 + S2）", 2, "multi-version"),
         ("忍者与杀手的两人生活（目录名二写，集数互补）", 2, "episode-complement"),
         ("鹿乃子乃子虎视眈眈（音译两写，集数互补）", 2, "episode-complement"),
         ("吊带袜天使（旧作 + 新作）", 2, "multi-version"),
         ("转生史莱姆（同 IP 三处，有内容重叠）", 3, "multi-version"),
         ("我的妹妹哪有这么可爱（顶层与嵌套各一份）", 2, "cross-layer-dup"),
         ("如果有妹妹就好了（顶层与嵌套各一份）", 2, "cross-layer-dup"),
         ("转生乙女破灭 Flag（跨层三份）", 3, "cross-layer-dup"),
         ("哥哥有爱就没问题（同作品两套 BD）", 2, "multi-version"),
         ("番\\番\\ 嵌套遗留（顶层无副本）", 2, "form-cluster"),
         ("ASMR 成人音声压缩包（与番剧无关）", 5, "form-cluster"),
         ("动漫单集录屏散件", 5, "form-cluster"),
         ("非番剧视频散件（电影 / 新闻）", 2, "form-cluster"),
     ]),
    ("BaiduNetdiskDownload", "split", "high", 66,
     "百度网盘下载落地根目录：36 个互不相关的独立成套资源 + 31 个散装大文件，"
     "没有任何共同的归档主题。",
     [
         ("编程课程", 4, "theme"),
         ("游戏", 6, "theme"),
         ("VOCALOID 编辑器与声库", 4, "theme"),
         ("语音合成模型与工具包", 3, "theme"),
         ("Windows 10 LTSC 镜像", 3, "multi-version"),
         ("安卓刷机资源", 4, "theme"),
         ("电影", 4, "theme"),
         ("设计创意软件安装包", 4, "theme"),
         ("Windows 软件安装包", 5, "theme"),
         ("MATLAB 安装包", 2, "theme"),
         ("教材电子书 PDF", 3, "theme"),
     ]),
    ("H_Library", "enter", "high", 428,
     "已建成四轴顶层分类的私人媒体库：Documents / Doujin / Media / Software，"
     "Media 下再按尺度 × 形态交叉分桶。",
     [
         ("Documents 文档类", 55, "theme"),
         ("Doujin 同人创作", 81, "theme"),
         ("Media 影音图像", 202, "theme"),
         ("Software 软件", 92, "theme"),
     ]),
    ("Documents", "enter", "high", 15,
     "旧系统 Documents 目录的整盘快照：6 个游戏自管存档目录 + 游戏平台配置 + 软件工作产物。",
     [
         ("游戏存档（6 套，各自独立不可再生）", 6, "theme"),
         ("影视成品（同一工具链产出）", 3, "theme"),
         ("零信息办公文档残件", 2, "form-cluster"),
     ]),
    ("Temp", "split", "high", 38,
     "用户自建的暂存区，把三份来源不同、年份不同的旧东西搬进来就停了。",
     [
         ("成人向游戏本体与 CG 素材", 7, "theme"),
         ("镜像图集", 3, "theme"),
         ("视频下载与抓取工具", 7, "theme"),
         ("系统装机与硬件工具", 5, "theme"),
         ("编程教材课程", 4, "theme"),
         ("运行时不相关杂物", 6, "form-cluster"),
     ]),
    ("STEAM", "keep", "high", None,
     "Steam 客户端完整安装目录：steam.exe + 39 个 dll + 18 个运行时子目录。", []),
    ("DCIM", "keep", "high", None,
     "小米手机整份相机胶卷根目录，带 HyperOS 相册元数据，4195 文件 / 54.43GB。", []),
    ("sd-webui-aki-v4.4", "keep", "high", None,
     "秋叶整合包，自带 Python 3.10 + CUDA torch + 39 个底模（158.5GB）+ 2401 张历史出图。", []),
    ("PPT模板", "keep", "high", None,
     "105 个三位编号主题分类的模板库，14697 文件 / 197.43GB。", []),
    ("Tentacle-LoRA", "keep", "high", None,
     "已训练完成的 LoRA 工程工作区，含 4 个 epoch 权重与完整训练日志。", []),
    ("文件", "split", "medium", 53,
     "长期积累的杂物层：53 个散件跨软件包、文档、网页残留、硬件固件等多类。",
     [
         ("软件安装包", 7, "theme"),
         ("网页存档", 7, "form-cluster"),
         ("个人文档", 10, "theme"),
         ("安卓 APK", 3, "theme"),
     ]),
    ("Windows 11 x64 虚拟机（加密）", "keep", "high", None,
     "一台整盘加密的 VMware 虚拟机，16 个 vmdk 分片缺一不可。", []),
    ("系统镜像", "rename", "medium", None,
     "两个 Win10 Pro WIM，不同 build，不是重复。bf.wim 名字零信息需要补版本号。", []),
]

ZONES = [
    "G:\\AI工具", "G:\\Adobe", "G:\\影视", "G:\\游戏", "G:\\系统镜像",
    "G:\\番", "G:\\VOCALOID", "G:\\H_Library", "G:\\H_Library\\Media\\ASMR",
    "G:\\文件", "G:\\文件\\软件安装包", "G:\\文件\\个人文档",
    "G:\\backup", "G:\\tempH\\归档",
]


def main():
    base = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        tempfile.mkdtemp(prefix="fo-fixture-"))
    root = base / "root"
    probe = root / "_organize" / "probe"
    probe.mkdir(parents=True, exist_ok=True)

    for name, action, conf, units, what, groups in NODES:
        path = root / name
        path.mkdir(parents=True, exist_ok=True)

        # S1
        (probe / f"probe_{abs(hash(name)) & 0xFFFFFF:06x}.json").write_text(
            json.dumps({
                "path": str(path), "action": action, "confidence": conf,
                "is_unit": "whole",
                "actual": {"what": what},
                "unit_why": "依据：" + what[:80],
                "rename_to": None,
            }, ensure_ascii=False, indent=2), encoding="utf-8")

        # S2
        if units:
            (probe / f"units_{name.replace(chr(92),'_')}.json").write_text(
                json.dumps({"path": str(path), "total_units": units},
                           ensure_ascii=False), encoding="utf-8")

        # S3
        if groups:
            (probe / f"group_{name.replace(chr(92),'_')}.json").write_text(
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
                    "solo_units": ["解压教程（必看）"],
                    "uncertain": [{"unit": "F丨3", "why": "名字零信息"}],
                }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"fixture root: {root}")
    print(f"  {len(NODES)} 节点 / "
          f"{sum(len(g) for *_, g in NODES)} 组")
    return 0


if __name__ == "__main__":
    sys.exit(main())
