#!/usr/bin/env python3
"""Check S1/S2/S3 for top-level and discovered nested nodes; 0=complete, 1=missing."""
import argparse
import json
import os
from pathlib import Path
import sys

from safety import (SafetyError, assert_no_links, block_kind, is_reparse, key,
                    load_markers, normal, within)

SKIP_NAMES = {
    '$recycle.bin', 'system volume information', '_organize',
    '_待删除', '_重复待确认', '_未归类',
}
ACTIONS = {'keep','enter','split','rename','merge','ask','skip','unclear'}


def _units_ok(value):
    """total_units accepts int, and float whose value equals an integer."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value >= 0
    if isinstance(value, float):
        return value >= 0 and value == int(value)
    return False


def cross_departures(root):
    from executor import active_events, completed, cross_scopes, meta, read_journal

    rows = read_journal(Path(root)/'_organize/journal.tsv')
    active = active_events(rows)
    result = []
    for row in completed(rows):
        data = meta(row)
        if row['seq'] not in active or data.get('effect') != 'cross-move':
            continue
        source, _ = cross_scopes(root, data.get('cross'), row['before'], row['after'])
        if key(source) == key(root):
            result.append((row['before'], row['after']))
    return result


def check(root, probe):
    root, probe = normal(root), Path(probe)
    assert_no_links(root)
    assert_no_links(probe)
    markers = load_markers(root)
    expected, probes, units, groups, errors, stale = {}, {}, {}, {}, [], []

    derived = set()
    enter_children = set()
    departures = None
    reported_departures = set()

    def add(path, base=root, is_derived=False):
        nonlocal departures
        value = normal(path if os.path.isabs(path) else os.path.join(base,path))
        if not within(value,root,inclusive=False):
            if departures is None:
                departures = cross_departures(root)
            for before, after in departures:
                if within(value, after):
                    if before not in reported_departures:
                        stale.append({'node': os.path.relpath(before, root), 'reason': '已跨卷移出'})
                        reported_departures.add(before)
                    return None
            raise SafetyError('阶段产物引用了根目录之外或根目录自身')
        if block_kind(value,markers):
            return None
        assert_no_links(value)
        expected[key(value)] = value
        if is_derived:
            derived.add(key(value))
        return key(value)

    for entry in os.scandir(root):
        if entry.name.casefold() in SKIP_NAMES or is_reparse(entry.path):
            continue
        if entry.is_dir(follow_symlinks=False):
            add(entry.path)
    if probe.is_dir():
        for file in sorted(probe.glob('*.json')):
            if file.name.startswith('dup_'):
                continue
            try:
                assert_no_links(file)
                with file.open(encoding='utf-8-sig') as f:
                    obj = json.load(f)
                if not isinstance(obj,dict) or not isinstance(obj.get('path'),str):
                    raise SafetyError('JSON 需要对象及 path 字段')
                node = add(obj['path'])
                if node is None:
                    continue
                if file.name.startswith('units_'):
                    destination = units
                    if not _units_ok(obj.get('total_units')):
                        raise SafetyError('total_units 必须是非负整数')
                    children = obj.get('sub_containers',[])
                elif file.name.startswith('group_'):
                    destination = groups
                    if not isinstance(obj.get('groups'),list):
                        raise SafetyError('groups 必须是数组')
                    children = []
                else:
                    destination = probes
                    if obj.get('action') not in ACTIONS:
                        raise SafetyError('action 无效或缺失')
                    children = obj.get('enter_children',[])
                if node in destination:
                    raise SafetyError('同一节点存在重复阶段产物')
                children = children or []
                if not isinstance(children,list):
                    raise SafetyError('子节点清单必须是数组')
                for child in children:
                    value = child.get('path') if isinstance(child,dict) else child
                    if not isinstance(value,str) or not value:
                        raise SafetyError('子节点缺少有效 path')
                    child_key = add(value,expected[node],is_derived=True)
                    if child_key is not None and destination is probes and obj.get('action') == 'enter':
                        enter_children.add(child_key)
                destination[node] = obj
            except (OSError,ValueError) as exc:
                errors.append({'node':'<阶段产物读取错误>', 'action':None,'units':None,
                               'groups':None,'status':'ERROR',
                               'missing':[str(exc) if isinstance(exc,SafetyError)
                                          else 'JSON 无法解析或文件无法读取']})
    rows = []
    for node,path in sorted(expected.items()):
        if block_kind(path,markers):
            continue
        action = probes.get(node,{}).get('action')
        missing = []
        if not os.path.isdir(path):
            # 源路径已不存在（通常是已迁移或已删除），单独诊断，不算遗漏
            stale.append({'node': os.path.relpath(path, root), 'reason': '源路径已不存在'})
            continue
        if node not in probes and (node not in derived or node in enter_children):
            missing.append('S1探针')
        if action in ('split','enter') and node not in units:
            missing.append('S2清点')
        if node in units and node not in groups:
            missing.append('S3归组')
        rows.append({'node':os.path.relpath(path,root),'action':action,
                     'units':units.get(node,{}).get('total_units'),
                     'groups':len(groups[node]['groups']) if node in groups else None,
                     'status':'MISSING' if missing else 'ok','missing':missing})
    return rows+errors+[{'status':'stale', **x} for x in stale]


def main(argv=None):
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',nargs='?',default=os.getcwd())
    parser.add_argument('--probe')
    parser.add_argument('--json',action='store_true')
    args = parser.parse_args(argv)
    try:
        rows = check(args.root,args.probe or Path(args.root)/'_organize/probe')
    except (OSError,ValueError):
        rows = [{'node':'<根目录或标记读取错误>','status':'ERROR',
                 'missing':['无法安全读取覆盖范围']}]
    failed = any(row.get('status') not in ('ok', 'stale') for row in rows)
    if args.json:
        print(json.dumps(rows,ensure_ascii=False,indent=2))
    else:
        for row in rows:
            status = row.get('status', 'ok')
            detail = row.get('missing') or [row.get('reason') or '']
            label = {'ok': 'ok', 'stale': '过时', 'MISSING': '遗漏'}.get(status, status)
            print(f"{row.get('node','-')}: {label} {' / '.join(str(x) for x in detail if x)}")
        print(f"{'存在遗漏或读取错误' if failed else '覆盖完整'}：{len(rows)} 个节点/诊断")
    return int(failed)


if __name__ == '__main__':
    sys.exit(main())
