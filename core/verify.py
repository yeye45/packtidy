#!/usr/bin/env python3
"""
verify.py —— 用已知答案的样本验证判据

单元测试验的是"函数对不对"，这里验的是"判断准不准"。
两者不同：函数可以全对，但组合起来的判断仍可能错。

三类验证：
  ① 判据回归      给样本 + 期望结论，跑一遍看命中率
  ② 真实盘回归    记录人工确认过的判断，防止后续改动打破
  ③ 规则覆盖      规则包的类别能覆盖多少真实内容

用法：
    python core/verify.py run cases/               跑全部用例
    python core/verify.py run cases/ --verbose     显示每个用例结果
    python core/verify.py record <root> <out.json> 从当前判断生成用例
    python core/verify.py coverage <root>          规则覆盖统计
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent


def load_cases(path):
    """加载用例。支持单文件或目录。"""
    p = Path(path)
    cases = []
    if p.is_file():
        files = [p]
    elif p.is_dir():
        files = sorted(p.glob("*.json"))
    else:
        raise FileNotFoundError(f"用例路径不存在: {path}")

    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            print(f"跳过 {f.name}: {exc}", file=sys.stderr)
            continue
        items = data.get("cases") if isinstance(data, dict) else data
        if not isinstance(items, list):
            continue
        for c in items:
            if isinstance(c, dict):
                c["_source"] = f.name
                cases.append(c)
    return cases


def run_case(case, decisions_mod):
    """跑一个用例，返回 (通过, 说明)。"""
    kind = case.get("kind", "suggest")

    if kind == "shape":
        # 验内容形态检测
        path = case.get("path")
        expect = case.get("expect")
        if not path or not os.path.isdir(path):
            return None, "路径不存在，跳过"
        shape = decisions_mod.content_shape(path)
        if not shape:
            return False, "无法采样"
        actual = shape.get("kind")
        return (actual == expect,
                f"期望 {expect} / 实际 {actual}"
                f"（主类型 {shape.get('dominant')} "
                f"{shape.get('ratio', 0):.0%}）")

    if kind == "stem":
        actual = decisions_mod.name_stem(case.get("name", ""))
        expect = case.get("expect", "")
        return (actual == expect, f"期望 {expect!r} / 实际 {actual!r}")

    if kind == "suggest":
        # 验建议
        shape = case.get("shape")
        path = case.get("path")
        if path and os.path.isdir(path):
            shape = decisions_mod.content_shape(path)
        sug = decisions_mod.suggest_group(
            case.get("group", ""), case.get("parent", ""),
            case.get("zones") or [], node_path=case.get("nodePath"),
            group_path=path if (path and os.path.isdir(path)) else None)

        expect_mode = case.get("expectMode")
        expect_check = case.get("expectNeedsCheck")
        ok = True
        parts = []
        if expect_mode:
            hit = sug["mode"] == expect_mode
            ok = ok and hit
            parts.append(f"mode 期望 {expect_mode} / 实际 {sug['mode']}")
        if expect_check is not None:
            hit = bool(sug.get("needsCheck")) == bool(expect_check)
            ok = ok and hit
            parts.append(f"needsCheck 期望 {expect_check} / "
                         f"实际 {sug.get('needsCheck')}")
        return ok, "；".join(parts)

    return None, f"未知用例类型: {kind}"


def cmd_run(args):
    import decisions as dec_mod

    # 加载规则包
    try:
        import rules
        pack = rules.load()
        if not pack.get("_missing"):
            rules.apply_to(pack, decisions_mod=dec_mod)
    except Exception:
        pass

    cases = load_cases(args.path)
    if not cases:
        print("没有用例")
        return 1

    passed = failed = skipped = 0
    failures = []

    for c in cases:
        try:
            ok, msg = run_case(c, dec_mod)
        except Exception as exc:
            ok, msg = False, f"异常 {type(exc).__name__}: {exc}"

        name = c.get("name") or c.get("group") or c.get("path") or "?"
        src = c.get("_source", "")

        if ok is None:
            skipped += 1
            if args.verbose:
                print(f"  跳过  {name:36} {msg}")
        elif ok:
            passed += 1
            if args.verbose:
                print(f"  ✓     {name:36} {msg}")
        else:
            failed += 1
            failures.append((name, src, msg))
            print(f"  ✗     {name:36} {msg}")

    total = passed + failed
    print()
    print(f"通过 {passed} / {total}"
          + (f" · 跳过 {skipped}" if skipped else ""))
    if failures:
        print()
        print("失败明细:")
        for name, src, msg in failures:
            print(f"  [{src}] {name}")
            print(f"      {msg}")
    return 0 if not failed else 1


def cmd_record(args):
    """从当前判断生成用例——人工确认后作为回归基准。"""
    import decisions as dec_mod

    try:
        import rules
        pack = rules.load()
        if not pack.get("_missing"):
            rules.apply_to(pack, decisions_mod=dec_mod)
    except Exception:
        pass

    root = args.root
    zones = dec_mod.collect_zones(root)

    # 复用 decisions.py 的节点构建
    try:
        from decisions import load_probes, build_nodes
    except ImportError:
        print("无法导入节点构建函数", file=sys.stderr)
        return 1

    probe_dir = Path(root) / "_organize" / "probe"
    probes, units, groups = load_probes(probe_dir)
    nodes = build_nodes(root, probes, units, groups)

    cases = []
    for n in nodes:
        for g in n["groups"]:
            sug = dec_mod.suggest_group(
                g["name"], n["name"], zones, n["path"])
            case = {
                "name": f"{n['name']} / {g['name']}",
                "kind": "suggest",
                "group": g["name"],
                "parent": n["name"],
                "nodePath": n["path"],
                "expectMode": sug["mode"],
            }
            if sug.get("needsCheck"):
                case["expectNeedsCheck"] = True
            cases.append(case)

    out = {
        "_about": "从当前判断自动生成。人工确认后作为回归基准。",
        "_root": root,
        "cases": cases,
    }
    Path(args.out).write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已生成 {len(cases)} 个用例: {args.out}")
    print("  注意：这是当前判断的快照，不等于正确。")
    print("  要作为回归基准，得先人工核对一遍。")
    return 0


def cmd_coverage(args):
    """规则包能覆盖多少真实内容。"""
    import decisions as dec_mod

    try:
        import rules
        pack = rules.load()
        if not pack.get("_missing"):
            rules.apply_to(pack, decisions_mod=dec_mod)
    except Exception:
        pass

    root = args.root
    zones = dec_mod.collect_zones(root)

    try:
        from decisions import load_probes, build_nodes
    except ImportError:
        print("无法导入节点构建", file=sys.stderr)
        return 1

    probe_dir = Path(root) / "_organize" / "probe"
    probes, units, groups = load_probes(probe_dir)
    nodes = build_nodes(root, probes, units, groups)

    stats = {"total": 0, "matched": 0, "unmatched": 0, "conflict": 0}
    unmatched_names = []

    for n in nodes:
        for g in n["groups"]:
            stats["total"] += 1
            sug = dec_mod.suggest_group(
                g["name"], n["name"], zones, n["path"])
            if sug.get("needsCheck"):
                stats["conflict"] += 1
            if sug["mode"] == "keep" and sug.get("evidence"):
                stats["unmatched"] += 1
                unmatched_names.append(g["name"])
            elif sug["mode"] in ("move", "new"):
                stats["matched"] += 1

    total = stats["total"] or 1
    print(f"规则覆盖统计  ({root})")
    print(f"  总组数        {stats['total']}")
    print(f"  已匹配        {stats['matched']}  "
          f"({stats['matched']/total:.0%})")
    print(f"  未匹配        {stats['unmatched']}  "
          f"({stats['unmatched']/total:.0%})")
    print(f"  需人工/AI     {stats['conflict']}  "
          f"({stats['conflict']/total:.0%})")

    if unmatched_names:
        print()
        print("未匹配的组（这些是规则包该补的）:")
        for name in unmatched_names[:20]:
            print(f"  {name}")
        if len(unmatched_names) > 20:
            print(f"  …另有 {len(unmatched_names)-20} 个")

    return 0


def main(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("run", help="跑用例")
    s.add_argument("path")
    s.add_argument("--verbose", action="store_true")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("record", help="从当前判断生成用例")
    s.add_argument("root")
    s.add_argument("out")
    s.set_defaults(func=cmd_record)

    s = sub.add_parser("coverage", help="规则覆盖统计")
    s.add_argument("root")
    s.set_defaults(func=cmd_coverage)

    args = p.parse_args(argv)
    sys.path.insert(0, str(HERE))
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
