#!/usr/bin/env python3
"""
rules.py —— 规则包加载与同步

三层优先级（后者覆盖前者）：
  1. 内置默认（core/ 里的 Python 常量）
  2. 规则包（rules/default.json 或远程拉取的缓存）
  3. 本地 policy.json 的 preferences

规则包是"什么不能动""什么该去哪"这类**可共享的判断**。
路径、个人分类偏好属于本地，不进规则包。

用法：
    python core/rules.py show                     查看当前生效规则
    python core/rules.py check rules/default.json  校验规则包格式
    python core/rules.py sync [--url URL]          从远程拉取（需手动指定 URL）
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
DEFAULT_RULES = HERE.parent / "rules" / "default.json"
CACHE_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "packtidy" / "rules"

REQUIRED = {"version", "minPacktidy", "protected", "categories"}
OPTIONAL = {"nestedAllow", "nestedDeny", "mapping", "updated", "changelog"}
# v1 字段（向后兼容）
LEGACY = {"crossRules", "newCategories", "categoryNodes"}
LIST_FIELDS = {"protected", "nestedAllow", "nestedDeny", "categoryNodes"}
DICT_FIELDS = {"newCategories"}


class RulesError(ValueError):
    pass


def validate(data):
    """校验规则包结构。返回 (ok, 问题列表)。

    ok=False 表示有阻断性问题；
    列表里可能含非阻断的警告（如未知字段），调用方按 ok 决定是否继续。
    """
    problems = []
    warnings = []

    if not isinstance(data, dict):
        return False, ["顶层必须是对象"]

    for key in REQUIRED:
        if key not in data:
            problems.append(f"缺少必需字段: {key}")

    # 未知字段只警告，不影响合法性——前向兼容：
    # 新版本写的规则包，旧版本应该能用了解的字段，忽略不认识的
    unknown = set(data) - REQUIRED - OPTIONAL - {
        k for k in data if k.startswith("_")}
    warnings = []
    if unknown:
        warnings.append(f"未知字段（忽略）: {', '.join(sorted(unknown))}")

    # 类型检查
    if "version" in data and not isinstance(data["version"], int):
        problems.append("version 必须是整数")
    if "protected" in data:
        v = data["protected"]
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            problems.append("protected 必须是字符串数组")
    if "categories" in data:
        v = data["categories"]
        if not isinstance(v, dict):
            problems.append("categories 必须是对象")
        else:
            for name, info in v.items():
                if name.startswith("_"):
                    continue
                if not isinstance(info, dict):
                    problems.append(f"categories.{name} 必须是对象")
                    continue
                if "keys" not in info or not isinstance(info["keys"], list):
                    problems.append(f"categories.{name} 需要 keys 数组")
                if "suggest" in info and not isinstance(info["suggest"], list):
                    problems.append(f"categories.{name}.suggest 必须是数组")

    if "mapping" in data:
        v = data["mapping"]
        if not isinstance(v, dict):
            problems.append("mapping 必须是对象")
        else:
            cats = set(data.get("categories", {})) - {
                k for k in data.get("categories", {}) if k.startswith("_")}
            for name, target in v.items():
                if name.startswith("_"):
                    continue
                if name not in cats:
                    warnings.append(f"mapping.{name} 对应的类别不存在（忽略）")
                if target is not None and not isinstance(target, str):
                    problems.append(f"mapping.{name} 必须是字符串或 null")

    return (not problems), problems + warnings


def load(path=None):
    """加载规则包。找不到就返回空结构（用内置默认）。"""
    p = Path(path) if path else DEFAULT_RULES
    if not p.is_file():
        return {"_missing": True, "version": 0}
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise RulesError(f"规则包无法解析: {exc}") from exc
    ok, problems = validate(data)
    if not ok:
        raise RulesError("规则包格式错误:\n  " + "\n  ".join(problems))
    return data


def apply_to(data, safety_mod=None, decisions_mod=None):
    """把规则包的值注入到模块的模块级常量上。

    这是唯一改模块状态的地方，便于测试隔离。
    """
    changed = []

    if safety_mod is not None:
        for field, const in (
            ("protected", "PROTECTED_NAMES"),
            ("nestedAllow", "NESTED_ALLOW"),
            ("nestedDeny", "NESTED_DENY"),
        ):
            if field in data and isinstance(data[field], list):
                setattr(safety_mod, const, {x.casefold() for x in data[field]})
                changed.append(f"safety.{const}")

    if decisions_mod is not None:
        # v2：categories + mapping
        if "categories" in data:
            cats = data["categories"]
            mapping = data.get("mapping") or {}

            # 转成 decisions 用的 CROSS_RULES：keys → [目标目录名]
            cross = []
            for name, info in cats.items():
                if name.startswith("_"):
                    continue
                keys = info.get("keys") or []
                target = mapping.get(name)
                if target is None:
                    # 没映射时退回 suggest 里的候选名
                    target_list = info.get("suggest") or []
                else:
                    target_list = [target]
                if keys and target_list:
                    cross.append({"keys": keys, "to": target_list})
            if cross:
                decisions_mod.CROSS_RULES = cross
                changed.append(f"decisions.CROSS_RULES(v2, {len(cross)}条)")

            # 有映射名字的当作 categoryNodes
            nodes = {m for m in mapping.values() if isinstance(m, str)}
            if nodes:
                decisions_mod.CATEGORY_NODE_NAMES = nodes
                changed.append("decisions.CATEGORY_NODE_NAMES(v2)")

        # v1 兼容
        if "crossRules" in data:
            rules = [{"keys": r["keys"], "to": r["to"]}
                     for r in data["crossRules"]]
            decisions_mod.CROSS_RULES = rules
            changed.append("decisions.CROSS_RULES(v1)")

        if "newCategories" in data:
            decisions_mod.NEW_CATEGORIES = {
                name: {"parent": info.get("parent"), "keys": info["keys"]}
                for name, info in data["newCategories"].items()
            }
            changed.append("decisions.NEW_CATEGORIES(v1)")

        if "categoryNodes" in data:
            decisions_mod.CATEGORY_NODE_NAMES = set(data["categoryNodes"])
            changed.append("decisions.CATEGORY_NODE_NAMES(v1)")

    return changed


def cmd_show(args):
    try:
        data = load(args.file)
    except RulesError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1

    if data.get("_missing"):
        print("未找到规则包，使用内置默认")
        return 0

    print(f"规则包: {args.file or DEFAULT_RULES}")
    print(f"  版本    {data.get('version')}")
    print(f"  更新    {data.get('updated', '—')}")
    print(f"  兼容    packtidy >= {data.get('minPacktidy')}")
    print()
    for field in ("protected", "nestedAllow", "nestedDeny", "categoryNodes"):
        if field in data:
            print(f"  {field:16} {len(data[field])} 项")
    if "crossRules" in data:
        print(f"  {'crossRules':16} {len(data['crossRules'])} 条")
    if "newCategories" in data:
        print(f"  {'newCategories':16} {len(data['newCategories'])} 个")
    return 0


def cmd_check(args):
    try:
        data = json.loads(Path(args.file).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        print(f"无法读取: {exc}", file=sys.stderr)
        return 1

    ok, problems = validate(data)
    if ok:
        print(f"✓ 格式正确: {args.file}")
        return 0
    print(f"✗ 发现 {len(problems)} 个问题:", file=sys.stderr)
    for p in problems:
        print(f"  - {p}", file=sys.stderr)
    return 1


def cmd_sync(args):
    """从远程拉取规则包。刻意要求显式 URL——不内置任何默认地址。"""
    if not args.url:
        print("需要 --url。规则包不内置默认来源，由使用者决定信任谁。",
              file=sys.stderr)
        print("\n示例:", file=sys.stderr)
        print("  python core/rules.py sync --url https://example.com/packtidy-rules.json",
              file=sys.stderr)
        return 1

    import urllib.request
    try:
        with urllib.request.urlopen(args.url, timeout=20) as r:
            raw = r.read()
    except Exception as exc:
        print(f"拉取失败: {type(exc).__name__}", file=sys.stderr)
        return 1

    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except ValueError:
        print("远程内容不是有效 JSON", file=sys.stderr)
        return 1

    ok, problems = validate(data)
    if not ok:
        print("远程规则包格式错误，拒绝写入:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = CACHE_DIR / "remote.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"已缓存: {out}")
    print(f"  版本 {data.get('version')} / 更新 {data.get('updated', '—')}")
    print()
    print("生效方式：在 policy.json 里设 \"rules\": \"<路径>\"，或下次运行时自动读缓存")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("show", help="查看规则包内容摘要")
    s.add_argument("--file")
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("check", help="校验规则包格式")
    s.add_argument("file")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("sync", help="从远程拉取规则包")
    s.add_argument("--url")
    s.set_defaults(func=cmd_sync)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
