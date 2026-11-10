#!/usr/bin/env python3
"""
packtidy — 顺着结构切分，而不是按扩展名硬套

统一入口。子命令对应六个阶段：

    packtidy scan     <root>            观察一个目录的信号（S1/S2 的原料）
    packtidy coverage <root>            覆盖检查：有没有节点被跳过（S6）
    packtidy plan     <root>            从探针结果生成待批清单（S4）
    packtidy apply    <root> [--actions] 执行
    packtidy undo     <root>            逆序撤销
    packtidy recover  <root>            中断恢复
    packtidy ui       <root>            生成决策界面

也可以直接用 core/ 下的模块：
    python core/signals.py <dir> --json
    python core/coverage.py <root>
    python core/ui.py --root <root>

设计边界：不删除、不猜、一切经 journal 可回滚。
"""
import argparse
import importlib.util
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORE = HERE / "core"

# core/ 下的模块互相用扁平导入（import decisions / from safety import ...），
# 所以 core/ 必须在 sys.path 上。
# 源码目录下能跑是因为当前工作目录恰好包含它——那是隐式依赖，
# wheel 安装到别处就断了。显式加进来。
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))
# rules/default.json 按 HERE/"rules" 查找，安装后仍然是同目录，不用改。
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


def _load(name, files):
    """按文件名加载 core 下的模块。

    不要求 core/ 是包——用 spec_from_file_location 直接加载。
    """
    path = CORE / files
    if not path.is_file():
        sys.exit(f"缺少 {files}（在 {CORE} 下找不到）")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def cmd_scan(args):
    sig = _load("signals", "signals.py")
    targets = args.paths or [os.getcwd()]
    results = [sig.analyze(p) for p in targets if os.path.isdir(p)]
    if args.json:
        import json
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for r in results:
            print(sig.fmt(r))
    return 0


def cmd_coverage(args):
    cov = _load("coverage", "coverage.py")
    probe = Path(args.probe) if args.probe else Path(args.root) / "_organize" / "probe"
    rows = cov.check(args.root, probe)
    bad = [r for r in rows if r.get("status") not in ("ok", "stale")]
    if args.json:
        import json
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        for r in rows:
            st = r.get("status", "ok")
            label = {"ok": "ok", "stale": "过时", "MISSING": "遗漏"}.get(st, st)
            detail = r.get("missing") or [r.get("reason") or ""]
            detail = " / ".join(str(x) for x in detail if x)
            print(f"{r.get('node', '-')}: {label} {detail}".rstrip())
        print()
        print(f"共 {len(rows)} 个节点，遗漏 {len(bad)} 个")
    return 0 if not bad else 1


def cmd_plan(args):
    ex = _load("executor", "executor.py")
    argv = ["plan", "--root", args.root]
    if args.probe:
        argv += ["--probe", args.probe]
    if args.policy:
        argv += ["--policy", args.policy]
    if args.out:
        argv += ["--out", args.out]
    if args.dry_run:
        argv.append("--dry-run")
    return ex.main(argv)


def cmd_apply(args):
    ex = _load("executor", "executor.py")
    argv = ["apply", "--root", args.root]
    if args.actions:
        argv += ["--actions", args.actions]
    elif args.plan:
        argv += ["--plan", args.plan]
    elif args.rename:
        argv += ["--rename"] + list(args.rename)
    else:
        sys.exit("apply 需要 --actions、--plan 或 --rename 之一")
    if args.reason != "-":
        argv += ["--reason", args.reason]
    if args.dry_run:
        argv.append("--dry-run")
    if args.receipt:
        argv += ["--receipt", args.receipt]
    if args.batch_size:
        argv += ["--batch-size", str(args.batch_size)]
    if args.allow_cross_drive:
        argv.append("--allow-cross-drive")
    for destination_root in args.destination_root:
        argv += ["--destination-root", destination_root]
    return ex.main(argv)


def cmd_undo(args):
    ex = _load("executor", "executor.py")
    argv = ["undo", "--journal", args.journal]
    if args.run_id:
        argv += ["--run-id", args.run_id]
    elif args.last:
        argv.append("--last")
    elif args.seq is not None:
        argv += ["--seq", str(args.seq)]
    elif args.list:
        argv.append("--list")
    else:
        sys.exit("undo 需要 --run-id、--last、--seq 或 --list 之一")
    if args.dry_run:
        argv.append("--dry-run")
    return ex.main(argv)


def cmd_recover(args):
    ex = _load("executor", "executor.py")
    return ex.main(["recover", "--root", args.root])


def cmd_ui(args):
    ui = _load("ui", "ui.py")
    argv = ["--root", args.root]
    if args.probe:
        argv += ["--probe", args.probe]
    if args.decisions:
        argv += ["--decisions", args.decisions]
    if args.out:
        argv += ["--out", args.out]
    if args.open:
        argv.append("--open")
    old = sys.argv
    sys.argv = ["ui"] + argv
    try:
        return ui.main()
    finally:
        sys.argv = old


def cmd_decisions(args):
    dec = _load("decisions", "decisions.py")
    argv = ["--root", args.root]
    if args.need_ai:
        argv.append("--need-ai")
    if args.answers:
        argv += ["--answers", args.answers]
    if args.out:
        argv += ["--out", args.out]
    if args.probe:
        argv += ["--probe", args.probe]
    old = sys.argv
    sys.argv = ["decisions"] + argv
    try:
        return dec.main()
    finally:
        sys.argv = old


def cmd_verify(args):
    ver = _load("verify", "verify.py")
    if args.action == "run":
        argv = ["run", args.path or "cases/"]
        if args.verbose:
            argv.append("--verbose")
    elif args.action == "record":
        if not args.root or not args.out:
            sys.exit("record 需要 --root 和 --out")
        argv = ["record", args.root, args.out]
    else:  # coverage
        if not args.root:
            sys.exit("coverage 需要 --root")
        argv = ["coverage", args.root]
    old = sys.argv
    sys.argv = ["verify"] + argv
    try:
        return ver.main(argv)
    finally:
        sys.argv = old


def cmd_rules(args):
    r = _load("rules", "rules.py")
    if args.action == "show":
        argv = ["show"]
        if args.file:
            argv += ["--file", args.file]
    elif args.action == "check":
        if not args.file:
            sys.exit("check 需要 --file")
        argv = ["check", args.file]
    else:  # sync
        argv = ["sync"]
        if args.url:
            argv += ["--url", args.url]
    old = sys.argv
    sys.argv = ["rules"] + argv
    try:
        return r.main(argv)
    finally:
        sys.argv = old


def cmd_setup(args):
    su = _load("setup", "setup.py")
    argv = [args.action]
    if args.action == "apply":
        ans = getattr(args, "answers_pos", None) or getattr(args, "answers", None)
        if not ans:
            sys.exit("用法: packtidy setup apply <root> <answers.json>")
        argv += [args.root, ans]
    else:
        argv.append(args.root)
    old = sys.argv
    sys.argv = ["setup"] + argv
    try:
        return su.main(argv)
    finally:
        sys.argv = old


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(
        prog="packtidy",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scan", help="观察目录的信号（只读）")
    s.add_argument("paths", nargs="*")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_scan)

    s = sub.add_parser("coverage", help="覆盖检查")
    s.add_argument("root")
    s.add_argument("--probe")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_coverage)

    s = sub.add_parser("plan", help="生成待批清单")
    s.add_argument("root")
    s.add_argument("--probe")
    s.add_argument("--policy", help="计划的审批策略，可指定额外的禁区")
    s.add_argument("--out")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_plan)

    s = sub.add_parser("apply", help="执行")
    s.add_argument("root")
    s.add_argument("--actions")
    s.add_argument("--plan")
    s.add_argument("--rename", nargs=2)
    s.add_argument("--reason", default="-")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--receipt", help="预演时写入新的 JSON 清单，执行时核对同一清单")
    s.add_argument("--batch-size", type=int, default=50)
    s.add_argument("--allow-cross-drive", action="store_true")
    s.add_argument("--destination-root", action="append", default=[],
                   help="显式授权的外部目标根目录，可重复指定")
    s.set_defaults(func=cmd_apply)

    s = sub.add_parser("undo", help="逆序撤销")
    s.add_argument("--journal", required=True)
    s.add_argument("--run-id")
    s.add_argument("--last", action="store_true")
    s.add_argument("--seq", type=int)
    s.add_argument("--list", action="store_true")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_undo)

    s = sub.add_parser("recover", help="中断恢复")
    s.add_argument("root")
    s.set_defaults(func=cmd_recover)

    s = sub.add_parser("setup", help="整理前的一次性确认")
    s.add_argument("action", choices=("ask", "apply", "show"))
    s.add_argument("root")
    s.add_argument("answers_pos", nargs="?", help="apply 时的答案文件")
    s.set_defaults(func=cmd_setup)

    s = sub.add_parser("decisions", help="算建议 / 生成 AI 问题 / 回填答案")
    s.add_argument("root")
    s.add_argument("--probe")
    s.add_argument("--out")
    s.add_argument("--need-ai", action="store_true",
                   help="只输出需要 AI 判断的问题清单")
    s.add_argument("--answers", help="AI 判断结果 JSON，回填")
    s.set_defaults(func=cmd_decisions)

    s = sub.add_parser("verify", help="回归验证")
    s.add_argument("action", choices=("run", "record", "coverage"))
    s.add_argument("path", nargs="?", help="run 时的用例路径")
    s.add_argument("--root", help="record/coverage 的盘根")
    s.add_argument("--out", help="record 的输出文件")
    s.add_argument("--verbose", action="store_true")
    s.set_defaults(func=cmd_verify)

    s = sub.add_parser("rules", help="规则包")
    s.add_argument("action", choices=("show", "check", "sync"))
    s.add_argument("--file")
    s.add_argument("--url")
    s.set_defaults(func=cmd_rules)

    s = sub.add_parser("ui", help="生成决策界面")
    s.add_argument("root")
    s.add_argument("--probe")
    s.add_argument("--decisions", help="S4 decisions.json，默认读取根目录的阶段产物")
    s.add_argument("--out")
    s.add_argument("--open", action="store_true")
    s.set_defaults(func=cmd_ui)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError) as exc:
        from safety import SafetyError  # noqa: F401  延迟导入，允许模块缺失

        if isinstance(exc, SafetyError):
            print(f"停止: {exc}", file=sys.stderr)
        else:
            print(f"停止: {type(exc).__name__}；请检查日志并运行 recover",
                  file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.path.insert(0, str(CORE))
    sys.exit(main())
