"""Shared lexical path, marker and reparse rules (Python 3.9+)."""
import csv
import io
import os
from pathlib import Path
import stat

BLOCKING_KINDS = {'SECRET', 'FOREIGN', 'WORKSPACE', 'NO_TOUCH', 'ASK'}
KINDS = BLOCKING_KINDS | {'NOTE'}
# 命中即拒绝移动父目录的名字。
#
# 判断标准：移动父目录会不会真的破坏它？
#   venv / node_modules  含绝对路径依赖，移了要重建 → 保护
#   .git                 含工作树索引，移了可能损坏 → 保护
#   program files 等     系统目录，不该动 → 保护
#
# __pycache__ 不在此列：它是 Python 的编译缓存，自动生成、可随时删，
# 移动父目录不影响正确性（最多重新编译一次）。把它当保护项会让几乎所有
# 含 Python 代码的目录都无法整理。
PROTECTED_NAMES = {
    'steamlibrary', 'steamapps', 'games', 'my games', 'epic games', 'battle.net',
    'shaderpacks', 'appdata', 'program files', 'program files (x86)', 'windows',
    'system volume information', '$recycle.bin', '.git', 'node_modules', '.venv',
    'site-packages', '_organize',
}


# ═══════════════════════════════════════════════════════════
# 嵌套白名单：保护区内可以动的点
#
# 场景：C:\Program Files\某软件\ 整体不能动，
#       但 C:\Users\x\Downloads\ 属于"在受保护区里、但内容该整理"。
#
# 判定顺序（后者优先）：
#   1. 命中 PROTECTED_NAMES  → 默认保护
#   2. 命中 NESTED_ALLOW     → 放行，可整理
#   3. 命中 NESTED_DENY      → 强制保护（即使父级被放行）
# ═══════════════════════════════════════════════════════════

# 这些名字出现在受保护区内时，其中内容仍可整理
NESTED_ALLOW = {
    "downloads", "download", "桌面", "desktop", "documents", "文档",
    "temp", "tmp", "缓存", "cache", "临时", "捕获", "captures",
    "screenshots", "screenshot", "截图", "录屏", "recordings",
    "output", "outputs", "导出", "export",
    "_待删除", "_重复待确认", "_未归类",
}

# 这些名字即使出现在被放行的区域里，也不动
NESTED_DENY = {
    "node_modules", ".git", "site-packages", "venv", ".venv",
    "steamapps", "appdata", "windows", "system32", "syswow64",
    "program files", "program files (x86)", "programdata",
    "$recycle.bin", "system volume information",
}


def nested_verdict(path):
    """判断路径的嵌套保护状态。

    返回 (允许整理, 依据)
      允许=True  可以整理
      允许=False 不动
    """
    parts = []
    try:
        import os as _os
        p = _os.path.abspath(_os.fspath(path))
        while True:
            head, tail = _os.path.split(p)
            if not tail:
                break
            parts.append(tail.casefold())
            if head == p:
                break
            p = head
    except (TypeError, ValueError):
        return (False, "路径无法解析")

    parts.reverse()   # 从根到叶

    # 从浅到深扫描，后面的覆盖前面的
    allow_at = -1
    deny_at = -1
    allow_name = deny_name = ""

    for i, seg in enumerate(parts):
        if seg in NESTED_DENY:
            deny_at, deny_name = i, seg
        elif seg in NESTED_ALLOW:
            allow_at, allow_name = i, seg
        elif seg in PROTECTED_NAMES:
            deny_at, deny_name = i, seg

    if deny_at > allow_at:
        return (False, f"'{deny_name}' 在更深处，优先保护")
    if allow_at >= 0:
        return (True, f"'{allow_name}' 是允许整理的区域")
    return (True, "无保护标记")


class SafetyError(ValueError):
    pass


def normal(path):
    """Canonical separators/case are compared with key(); never resolve a link."""
    value = os.path.normpath(os.path.abspath(os.fspath(path)))
    if os.name == 'nt':
        if value.startswith(('\\\\?\\', '\\\\.\\')):
            raise SafetyError('不支持设备路径或扩展路径别名')
        drive, tail = os.path.splitdrive(value)
        if ':' in tail:
            raise SafetyError('不支持备用数据流路径')
        # Win32 treats trailing dots/spaces as aliases for the ordinary name.
        tail = os.sep.join(part.rstrip(' .') for part in tail.split(os.sep))
        value = drive + tail
        # Expand NTFS 8.3 aliases without following a junction as realpath would.
        # For new destinations expand their nearest existing ancestor.
        current, missing = value, []
        while not os.path.lexists(current):
            parent, leaf = os.path.split(current)
            if parent == current or not leaf:
                break
            missing.append(leaf)
            current = parent
        if os.path.lexists(current):
            import ctypes
            function = ctypes.windll.kernel32.GetLongPathNameW
            function.argtypes = (ctypes.c_wchar_p,ctypes.c_wchar_p,ctypes.c_uint32)
            function.restype = ctypes.c_uint32
            buffer = ctypes.create_unicode_buffer(32768)
            length = function(current,buffer,len(buffer))
            if not length or length >= len(buffer):
                raise SafetyError('无法规范化 Windows 路径别名')
            value = os.path.join(buffer.value,*reversed(missing)) if missing else buffer.value
    return value


def key(path):
    return os.path.normcase(normal(path))


def within(path, parent, inclusive=True):
    p, root = key(path), key(parent)
    try:
        return os.path.commonpath([p, root]) == root and (inclusive or p != root)
    except ValueError:
        return False


def is_reparse(path):
    st = os.lstat(path)
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, 'st_file_attributes', 0) & 0x400)


def assert_no_links(path):
    """Check all existing ancestors too, including a junction used as Root."""
    current = Path(normal(path))
    while True:
        if os.path.lexists(current) and is_reparse(current):
            raise SafetyError('链接或重解析路径已拒绝')
        if current.parent == current:
            break
        current = current.parent


def read_tsv(path, fields):
    if not os.path.exists(path):
        return []
    assert_no_links(path)
    with open(path, encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f, delimiter='\t')
        if reader.fieldnames != list(fields):
            raise SafetyError('TSV 表头不符合约定: ' + str(path))
        rows = []
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise SafetyError('TSV 行列数不符合约定: ' + str(path))
            rows.append(row)
        return rows


MARKER_FIELDS = ('path', 'kind', 'by', 'note', 'ts')


def load_markers(root):
    rows = read_tsv(Path(root)/'_organize/markers.tsv', MARKER_FIELDS)
    for row in rows:
        if row['kind'] not in KINDS or not os.path.isabs(row['path']):
            raise SafetyError('保护标记格式错误，停止操作')
        row['path'] = normal(row['path'])
    return rows


def marker_hits(path, markers, descendants=False):
    return [m for m in markers if within(path, m['path']) or
            (descendants and within(m['path'], path))]


def block_kind(path, markers, descendants=False):
    kinds = {m['kind'] for m in marker_hits(path, markers, descendants)}
    # A NOTE may add information but can never revoke protection.
    for kind in ('SECRET', 'FOREIGN', 'WORKSPACE', 'NO_TOUCH', 'ASK'):
        if kind in kinds:
            return kind
    return None


def encode_tsv(rows, fields):
    out = io.StringIO(newline='')
    writer = csv.DictWriter(out, fields, delimiter='\t', lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode('utf-8')


def replace_path(value, old, new):
    if not isinstance(value, str) or not os.path.isabs(value) or not within(value, old):
        return value
    rel = os.path.relpath(normal(value), normal(old))
    return normal(new) if rel == '.' else os.path.join(normal(new), rel)
