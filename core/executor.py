"""Journaled file operations. PowerShell entry points delegate to this module.

The TSV remains append-only with eight columns. Each mutation has a durable
prepare record in both journals and a mirrored recovery manifest before it runs.
The manifest contains exact paths, object identity and planned reference edits.
Interrupted operations block further work until `recover` reconciles them.
"""
import argparse
from contextlib import contextmanager
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import uuid

import cross_volume
import rules
from safety import (SafetyError, MARKER_FIELDS, NESTED_DENY, PROTECTED_NAMES, assert_no_links,
                    nested_verdict,
                    block_kind, encode_tsv, is_reparse, key, load_markers,
                    normal, read_tsv, replace_path, within)

FIELDS = ('seq', 'run_id', 'ts', 'op', 'before', 'after', 'reason', 'status')
PATH_KEYS = {'path', 'src', 'dst', 'target', 'merge_target', 'before', 'after',
             'enter_children', 'loose_files_as_units', 'empty', 'units',
             'solo_units', 'paths', 'shell_inner'}


def now():
    return datetime.now(timezone.utc).isoformat()


def run_id(prefix='run'):
    return prefix + '-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:12]


def identity(path):
    st = os.stat(path, follow_symlinks=False)
    if not st.st_ino:
        raise SafetyError('文件系统未提供稳定对象编号，拒绝自动移动')
    return {'dev':st.st_dev, 'ino':st.st_ino, 'dir':os.path.isdir(path),
            'size':st.st_size, 'mtime_ns':st.st_mtime_ns}


def same_identity(path, expected, content=False):
    if not os.path.lexists(path) or not expected or is_reparse(path):
        return False
    actual = identity(path)
    names = ('dev', 'ino', 'dir')
    if content and not actual['dir']:
        names += ('size', 'mtime_ns')
    return all(actual[n] == expected[n] for n in names)


def reviewed_identity(path):
    st = os.stat(path, follow_symlinks=False)
    if stat.S_ISLNK(st.st_mode) or getattr(st, 'st_file_attributes', 0) & 0x400:
        raise SafetyError('预演中的源已消失或变为链接')
    if not st.st_ino:
        raise SafetyError('文件系统未提供稳定对象编号，拒绝确认预演')
    return dict(dev=st.st_dev, ino=st.st_ino, size=st.st_size, mtime_ns=st.st_mtime_ns,
                change_ns=st.st_ctime_ns, mode=st.st_mode)


def reviewed_source(path):
    root = Path(path)
    found = reviewed_identity(root)
    result = {'identity': found}
    if not stat.S_ISDIR(found['mode']):
        return result
    checksum = hashlib.sha256()
    directories = [root]
    while directories:
        for child in sorted(directories.pop().iterdir()):
            child_id = reviewed_identity(child)
            if stat.S_ISDIR(child_id['mode']):
                directories.append(child)
            entry = (child.relative_to(root).as_posix(), child_id)
            checksum.update(json.dumps(entry, sort_keys=True).encode('ascii') + b'\n')
    if reviewed_identity(root) != found:
        raise SafetyError('采集预演清单时源目录发生变化，请重新预演')
    result['tree_sha256'] = checksum.hexdigest()
    return result


def directory_identity(path):
    found = identity(path)
    if not found['dir'] or is_reparse(path):
        raise SafetyError('预演中的目标父目录不是普通目录')
    return {field: found[field] for field in ('dev', 'ino', 'dir')}


def check_reviewed_parent(path, expected):
    if not same_identity(path, expected):
        raise SafetyError('目标父目录在预演后发生变化，请重新预演')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def sync_directory(path):
    if os.name != 'nt':
        descriptor=os.open(str(path),os.O_RDONLY | getattr(os,'O_DIRECTORY',0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def rename_noreplace(before, after):
    """Publish without overwriting a racing destination; never fall back to POSIX rename."""
    if os.name == 'nt':
        os.rename(before,after)
        return
    if sys.platform.startswith('linux'):
        import ctypes
        library=ctypes.CDLL(None,use_errno=True)
        function=getattr(library,'renameat2',None)
        if function is None:
            raise SafetyError('当前系统缺少安全的不覆盖移动接口')
        function.argtypes=(ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint)
        function.restype=ctypes.c_int
        # Linux renameat2(AT_FDCWD, old, AT_FDCWD, new, RENAME_NOREPLACE).
        if function(-100,os.fsencode(before),-100,os.fsencode(after),1):
            error=ctypes.get_errno()
            raise OSError(error,os.strerror(error))
        sync_directory(Path(before).parent)
        if Path(before).parent != Path(after).parent:
            sync_directory(Path(after).parent)
        return
    if sys.platform == 'darwin':
        import ctypes
        library=ctypes.CDLL(None,use_errno=True)
        function=getattr(library,'renamex_np',None)
        if function is None:
            raise SafetyError('当前系统缺少安全的不覆盖移动接口')
        function.argtypes=(ctypes.c_char_p,ctypes.c_char_p,ctypes.c_uint)
        function.restype=ctypes.c_int
        # Apple renamex_np RENAME_EXCL; unsupported filesystems fail closed.
        if function(os.fsencode(before),os.fsencode(after),4):
            error=ctypes.get_errno()
            raise OSError(error,os.strerror(error))
        sync_directory(Path(before).parent)
        if Path(before).parent != Path(after).parent:
            sync_directory(Path(after).parent)
        return
    raise SafetyError('当前系统未实现安全的不覆盖移动；只读工具仍可使用')


def atomic_write(path, data):
    path = Path(path)
    assert_no_links(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.organizer-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_journal(path):
    rows = read_tsv(path, FIELDS)
    previous = 0
    for row in rows:
        row['seq'] = int(row['seq'])
        if row['seq'] <= previous:
            raise SafetyError('日志序号重复或乱序，停止操作')
        previous = row['seq']
    return rows


def meta(row):
    try:
        value = json.loads(row['reason'])
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def completed(rows):
    """One completion per transaction; also expose historical done records."""
    result, seen = [], set()
    for row in rows:
        if row['status'] != 'done' or row['op'] == 'note':
            continue
        tx = meta(row).get('tx')
        if tx and tx in seen:
            raise SafetyError('日志中同一事务被重复完成')
        if tx:
            seen.add(tx)
        result.append(row)
    return result


def active_events(rows):
    suppressed = set()
    active = set()
    for row in reversed(completed(rows)):
        if row['seq'] in suppressed:
            continue
        active.add(row['seq'])
        parent = meta(row).get('inverse_of')
        if parent:
            suppressed.add(int(parent))
        elif row['op'].startswith('undo-'):
            # Recognize old undo history without modifying historical rows.
            match = re.search(r'撤销 seq (\d+)', row['reason'])
            if match:
                suppressed.add(int(match.group(1)))
    return active


class Journal:
    def __init__(self, root, path=None):
        self.root = Path(normal(root))
        if not self.root.is_dir():
            raise SafetyError('整理根目录不存在')
        if any(part.casefold() in PROTECTED_NAMES for part in self.root.parts):
            raise SafetyError('整理根目录位于受保护目录内')
        self.path = Path(path or self.root/'_organize/journal.tsv')
        token = digest(key(self.path).encode('utf-8'))[:24]
        local = os.environ.get('LOCALAPPDATA')
        if not local:
            local = os.environ.get('XDG_STATE_HOME') or str(Path.home()/'.local/state')
        self.mirror_dir = Path(local)/'packtidy-mirror'/token
        self.mirror = self.mirror_dir/'journal.tsv'
        self.tx_dir = self.root/'_organize/transactions'

    def check_marker_files(self, repair=False):
        primary = self.root/'_organize/markers.tsv'
        mirror = self.mirror_dir/'markers.tsv'
        assert_no_links(primary)
        assert_no_links(mirror)
        if primary.exists():
            return
        if mirror.exists():
            if not repair:
                raise SafetyError('主保护标记缺失；请先运行 recover')
            read_tsv(mirror, MARKER_FIELDS)
            atomic_write(primary, mirror.read_bytes())
        elif any((d/'marker-update.json').exists() for d in (primary.parent, self.mirror_dir)):
            raise SafetyError('主保护标记和镜像均缺失；请先人工核对保护标记')

    def protection(self, policy=None):
        if self.pending_marker_update():
            raise SafetyError('保护标记写入尚未完成；先运行 recover')
        self.check_marker_files()
        return load_protection(self.root, policy)

    @contextmanager
    def locked(self, repair=False, prepare=True):
        assert_no_links(self.path)
        assert_no_links(self.mirror_dir)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.parent/'executor.lock'
        assert_no_links(lock)
        with open(lock, 'a+b') as f:
            f.seek(0, os.SEEK_END)
            if f.tell() == 0:
                f.write(b'0')
                f.flush()
            f.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise SafetyError('已有执行或撤销进程正在使用此目录') from exc
            try:
                if prepare:
                    self.ensure_ready(repair=repair)
                yield self
            finally:
                f.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(f, fcntl.LOCK_UN)

    def ensure_ready(self, repair=False):
        """Both destinations must work before any user file is mutated."""
        assert_no_links(self.path)
        assert_no_links(self.mirror)
        self.mirror_dir.mkdir(parents=True, exist_ok=True)
        if repair and self.mirror.exists():
            primary = self.path.read_bytes() if self.path.exists() else b''
            mirror = self.mirror.read_bytes()
            # Explicit recovery may restore a missing suffix (including a torn
            # final line), but never replace divergent historical records.
            if primary != mirror and mirror.startswith(primary):
                read_journal(self.mirror)
                atomic_write(self.path, mirror)
        rows = read_journal(self.path)
        mirror_rows = read_journal(self.mirror)
        if mirror_rows and not rows:
            raise SafetyError('本机镜像有历史记录而主日志为空；请先恢复主日志')
        # A detached drive may have missed a completion. Do not silently overwrite it.
        if mirror_rows and rows[:len(mirror_rows)] != mirror_rows:
            raise SafetyError('主日志与本机镜像不一致；请先恢复日志')
        header = encode_tsv([], FIELDS)
        if not self.path.exists():
            atomic_write(self.path, header)
        if not self.mirror.exists() or len(mirror_rows) < len(rows):
            atomic_write(self.mirror, self.path.read_bytes())
        for path in (self.path, self.mirror):
            with open(path, 'ab') as f:
                f.flush()
                os.fsync(f.fileno())
        if self.pending_marker_update():
            if repair:
                self.recover_markers()
            else:
                raise SafetyError('保护标记写入尚未完成；先运行 recover')
        self.check_marker_files(repair=repair)

    def pending_marker_update(self):
        candidates=[]
        for path in (self.mirror_dir/'marker-update.json',self.root/'_organize/marker-update.json'):
            assert_no_links(path)
            if path.exists():
                data=json.loads(path.read_text(encoding='utf-8-sig'))
                if (data.get('state') not in ('pending','done') or
                        digest(data['after'].encode('utf-8')) != data.get('after_hash')):
                    raise SafetyError('标记恢复记录格式或校验无效')
                if data['state']=='pending':
                    candidates.append(data)
        if not candidates:
            return None
        if any(c != candidates[0] for c in candidates):
            raise SafetyError('保护标记恢复记录冲突')
        return candidates[0]

    def save_marker_update(self, data):
        encoded=json.dumps(data,ensure_ascii=False,indent=2).encode('utf-8')
        atomic_write(self.mirror_dir/'marker-update.json',encoded)
        atomic_write(self.root/'_organize/marker-update.json',encoded)

    def update_markers(self, data):
        path=self.root/'_organize/markers.tsv'
        tx={'id':uuid.uuid4().hex,'state':'pending',
            'before_hash':digest(path.read_bytes()) if path.exists() else None,
            'after_hash':digest(data),'after':data.decode('utf-8')}
        self.save_marker_update(tx)
        self.recover_markers()

    def recover_markers(self):
        tx=self.pending_marker_update()
        if not tx:
            return
        path=self.root/'_organize/markers.tsv'
        current=digest(path.read_bytes()) if path.exists() else None
        if current not in (tx['before_hash'],tx['after_hash']):
            raise SafetyError('保护标记被外部修改；保留现场等待人工核对')
        data=tx['after'].encode('utf-8')
        atomic_write(self.mirror_dir/'markers.tsv',data)
        atomic_write(path,data)
        tx['state']='done'
        self.save_marker_update(tx)

    def append(self, run, op, before='-', after='-', reason='-', status='done'):
        rows = read_journal(self.path)
        seq = rows[-1]['seq']+1 if rows else 1
        row = dict(zip(FIELDS, (seq, run, now(), op, str(before), str(after), reason, status)))
        # csv quoting preserves literal tabs/newlines in human notes.
        import io
        out = io.StringIO(newline='')
        csv.DictWriter(out, FIELDS, delimiter='\t', lineterminator='\n').writerow(row)
        data = out.getvalue().encode('utf-8')
        # Mirror first: if the state drive disappears, recovery evidence survives.
        for path in (self.mirror, self.path):
            assert_no_links(path)
            with open(path, 'ab') as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
        return seq

    def save_manifest(self, tx):
        tx['generation'] = tx.get('generation',0) + 1
        data = json.dumps(tx, ensure_ascii=False, indent=2).encode('utf-8')
        atomic_write(self.mirror_dir/'transactions'/f"{tx['id']}.json", data)
        atomic_write(self.tx_dir/f"{tx['id']}.json", data)

    def unresolved(self):
        rows = read_journal(self.path)
        terminal = {meta(r).get('tx') for r in rows if r['status'] in ('done','aborted')}
        found = {}
        for directory in (self.mirror_dir/'transactions', self.tx_dir):
            assert_no_links(directory)
            if not directory.exists():
                continue
            for path in sorted(directory.glob('*.json')):
                assert_no_links(path)
                tx = json.loads(path.read_text(encoding='utf-8-sig'))
                self.validate_manifest(tx)
                if tx['id'] not in terminal:
                    previous = found.get(tx['id'])
                    if previous and previous.get('generation',0) == tx.get('generation',0) and previous != tx:
                        raise SafetyError('同一版本恢复记录冲突，停止操作')
                    if not previous or previous.get('generation',0) < tx.get('generation',0):
                        found[tx['id']] = tx
        return list(found.values())

    def validate_manifest(self, tx):
        if not isinstance(tx,dict) or not re.fullmatch('[0-9a-f]{32}',tx.get('id','')):
            raise SafetyError('恢复记录格式无效')
        if tx.get('effect') not in ('move','mkdir','rmdir','cross-move'):
            raise SafetyError('恢复记录动作无效')
        if tx.get('staging') and not within(tx['staging'],self.root/'_organize/staging',inclusive=False):
            raise SafetyError('恢复暂存路径越界')
        if tx['effect'] == 'cross-move':
            cross_scopes(self.root, tx.get('cross', {}), tx.get('before'), tx.get('after'))
        else:
            for name in ('before','after'):
                value = tx.get(name)
                if value != '-' and (not isinstance(value,str) or not within(value,self.root,inclusive=False)):
                    raise SafetyError('恢复记录路径越界')
        for edit in tx.get('edits',[]):
            path = edit.get('path','')
            if not (key(path)==key(self.root/'_organize/markers.tsv') or
                    within(path,self.root/'_organize/probe',inclusive=False)):
                raise SafetyError('恢复引用路径越界')
            if digest(edit['after'].encode('utf-8')) != edit['after_hash']:
                raise SafetyError('恢复引用内容校验失败')


@dataclass
class Protection:
    markers: list
    extra_names: set


def load_protection(root, policy=None):
    policy = rules.load_root_policy(root) if policy is None else policy
    pack = rules.load_for_root(root, policy=policy)
    extra_names = ({name.casefold() for name in pack.get('protected', [])} - PROTECTED_NAMES)
    extra_names.update({name.casefold() for name in pack.get('nestedDeny', [])} - NESTED_DENY)
    markers = load_markers(root) + no_go_markers(root, policy)
    return Protection(markers, extra_names)


def no_go_markers(root, policy):
    no_go = policy.get('no_go_zones', [])
    if not isinstance(no_go, list) or any(not isinstance(p, str) or not p.strip() for p in no_go):
        raise SafetyError('policy 的 no_go_zones 必须是路径数组')
    markers = []
    for path in no_go:
        path = normal(Path(root)/path)
        if not within(path, root):
            raise SafetyError('policy 的禁区路径超出整理根目录')
        markers.append({'path': path, 'kind': 'NO_TOUCH'})
    return markers


def guard(path, root, protection, descendants=False, allow_root=False):
    if not os.path.isabs(str(path)):
        raise SafetyError('动作路径必须是绝对路径')
    path = normal(path)
    if not within(path, root, inclusive=allow_root):
        raise SafetyError('路径越界或指向整理根目录')
    kind = block_kind(path, protection.markers, descendants)
    if kind:
        raise SafetyError('凭据类路径已排除' if kind == 'SECRET' else '命中保护标记 ' + kind)
    assert_no_links(path)
    # 嵌套判定：受保护区内可以有允许点（C:\Program Files\x\Downloads 可动）
    allowed, why = nested_verdict(path)
    if not allowed:
        raise SafetyError('命中受保护目录：' + why)
    if any(part.casefold() in protection.extra_names for part in Path(path).parts):
        raise SafetyError('命中规则包保护目录')
    if descendants and os.path.isdir(path):
        # Never enter a link; fail on read errors instead of assuming an empty tree.
        def fail(exc):
            raise exc
        # 保护检查分两级：
        #   一、绝对不能出现在树里的：链接 / 重解析点（移了会断）
        #   二、作为「目标本身」时拒绝的：venv / node_modules 等环境目录
        #
        # 第二类只在它是被移动对象本身时才算危险——移动整个项目（内部含 venv）
        # 是正常操作，venv 记的是基础解释器路径，不是自身位置。
        env_dirs = {'venv', '.venv', 'node_modules', 'site-packages'}
        # 这些名字只在浅层（depth <= 2）才当作真系统目录
        SYSTEM_LIKE = {'windows', 'program files', 'program files (x86)',
                       'appdata', 'system volume information', '$recycle.bin',
                       'steamapps', 'steamlibrary', 'games', 'battle.net',
                       'epic games', 'my games', 'shaderpacks'}
        root_key = key(path)
        for folder, dirs, files in os.walk(path, followlinks=False, onerror=fail):
            for name in dirs + files:
                child = Path(folder)/name
                if is_reparse(child):
                    raise SafetyError('目录包含链接或重解析项')
                if name.casefold() in protection.extra_names and child.is_dir():
                    raise SafetyError('目录包含规则包保护目录')
                if name.casefold() not in PROTECTED_NAMES or not child.is_dir():
                    continue
                depth = len(Path(child).relative_to(path).parts)
                if name.casefold() in env_dirs:
                    # 环境目录只在两种情况下拦：
                    #   1. 它本身就是被移动对象
                    #   2. 它在项目根下一层（典型 venv 位置）
                    # 深处的一律放过——那是程序自带的资源
                    # （如 PyCharm 的 typeshed/stdlib/venv 只是类型存根目录）
                    if key(child) != root_key and depth > 2:
                        continue
                elif name.casefold() in SYSTEM_LIKE:
                    # 系统类名字只对浅层有意义：
                    #   windows / program files / appdata 出现在第三层以下
                    #   多半是程序自己的资源目录（PyCharm 的 pydevd\windows）
                    #   不是真的系统目录
                    if depth > 2:
                        continue
                raise SafetyError('目录包含受保护的程序或状态目录')
    return path


def reference_edits(root, old, new):
    """Prepare all edits before mutation; update only schema path fields."""
    edits = []
    directory = Path(root)/'_organize'
    markers = directory/'markers.tsv'
    if markers.exists():
        rows = load_markers(root)
        changed = False
        for row in rows:
            value = replace_path(row['path'], old, new)
            changed |= value != row['path']
            row['path'] = value
        if changed:
            edits.append(make_edit(markers, encode_tsv(rows, MARKER_FIELDS)))

    def rewrite(obj, field=None):
        if isinstance(obj, dict):
            return {k:rewrite(v, k) for k,v in obj.items()}
        if isinstance(obj, list):
            return [rewrite(v, field) for v in obj]
        return replace_path(obj, old, new) if field in PATH_KEYS else obj

    probes = directory/'probe'
    assert_no_links(probes)
    if probes.exists():
        for path in sorted(probes.glob('*.json')):
            assert_no_links(path)
            data = json.loads(path.read_text(encoding='utf-8-sig'))
            updated = rewrite(data)
            if updated != data:
                edits.append(make_edit(path, (json.dumps(updated,ensure_ascii=False,indent=2)+'\n').encode('utf-8')))
    # Placement/action TSVs are historical approved inputs, not silently rewritten.
    return edits


def make_edit(path, after):
    return {'path':str(path), 'before_hash':digest(Path(path).read_bytes()),
            'after_hash':digest(after), 'after':after.decode('utf-8')}


def finish_references(edits):
    for edit in edits:
        path = Path(edit['path'])
        assert_no_links(path)
        current = digest(path.read_bytes())
        if current == edit['after_hash']:
            continue
        if current != edit['before_hash']:
            raise SafetyError('引用文件已被外部修改；停止并等待恢复')
        atomic_write(path, edit['after'].encode('utf-8'))


def sync_reference_mirror(journal, edits):
    for edit in edits:
        if key(edit['path']) == key(journal.root/'_organize/markers.tsv'):
            atomic_write(journal.mirror_dir/'markers.tsv',edit['after'].encode('utf-8'))


def check_external_root(root, external):
    if (not isinstance(external, str) or not os.path.isabs(external) or
            within(external, root) or within(root, external)):
        raise SafetyError('授权的跨卷目标根必须是与整理根独立的绝对路径')
    return Path(normal(external))


def cross_scopes(root, authority, before, after):
    if not isinstance(authority, dict):
        raise SafetyError('跨卷授权记录格式无效')
    external = check_external_root(root, authority.get('root'))
    if (not all(isinstance(path, str) and os.path.isabs(path) for path in (before, after)) or
            not ((within(before, root, inclusive=False) and within(after, external, inclusive=False)) or
                 (within(before, external, inclusive=False) and within(after, root, inclusive=False)))):
        raise SafetyError('跨卷移动路径超出已授权的根')
    return (Path(root), external) if within(before, root) else (external, Path(root))


def cross_authority(root, external):
    assert_no_links(root)
    assert_no_links(external)
    for path in (root, external):
        if not Path(path).is_dir():
            raise SafetyError('已授权的跨卷根必须是现有目录')
        cross_volume.require_local_root(path)
    if not cross_volume.different_volume(root, external):
        raise SafetyError('外部目标根必须位于其他卷')
    return dict(root=str(external), root_id=identity(external), journal_root_id=identity(root))


def cross_guard(journal, authority, before, after):
    source_root, target_root = cross_scopes(journal.root, authority, before, after)
    external = Path(authority['root'])
    for root, expected in ((journal.root, authority.get('journal_root_id')), (external, authority.get('root_id'))):
        assert_no_links(root)
        cross_volume.require_local_root(root)
        if not same_identity(root, expected):
            raise SafetyError('跨卷移动的已授权根或卷已变化')
    primary, other = journal.protection(), Journal(external).protection()
    markers = primary.markers + other.markers
    protections = {journal.root: Protection(markers, primary.extra_names),
                   external: Protection(markers, other.extra_names)}
    for root in protections:
        if block_kind(root/'_organize'/'cross-volume', markers, descendants=True):
            raise SafetyError('跨卷暂存区域命中保护标记')
    for path, root in ((before, source_root), (after, target_root)):
        if within(path, root/'_organize'):
            raise SafetyError('跨卷移动拒绝内部状态目录')
        if block_kind(path, markers, descendants=True):
            raise SafetyError('跨卷路径命中两端的保护标记')
        guard(path, root, protections[root])
        parent = Path(path).parent
        if not parent.is_dir() or cross_volume.different_volume(parent, root):
            raise SafetyError('跨卷移动要求两端的父目录已存在且位于各自的卷')

    def check(relative):
        if relative != '.':
            guard(Path(before)/relative, source_root, protections[source_root])
            guard(Path(after)/relative, target_root, protections[target_root])

    return check


@contextmanager
def cross_lock(journal, authority, before, after):
    cross_scopes(journal.root, authority, before, after)
    external = Path(authority['root'])
    assert_no_links(external)
    cross_volume.require_local_root(journal.root)
    cross_volume.require_local_root(external)
    if not same_identity(external, authority.get('root_id')):
        raise SafetyError('跨卷移动的已授权根或卷已变化')
    other = Journal(external)
    with other.locked(prepare=False):
        if other.unresolved():
            raise SafetyError('跨卷目标根存在未完成操作；请先恢复该根')
        yield


def transfer_paths(journal, tx):
    source, target = cross_scopes(journal.root, tx['cross'], tx['before'], tx['after'])
    return (source/'_organize'/'cross-volume'/f"{tx['id']}.original",
            target/'_organize'/'cross-volume'/f"{tx['id']}.stage"/'payload')


def make_transfer_parent(path, volume):
    assert_no_links(path)
    path.mkdir(parents=True, exist_ok=True)
    if cross_volume.different_volume(path, volume):
        raise SafetyError('跨卷移动的暂存区域位于其他卷')
    sync_directory(path.parent)


def make_transfer_area(path, volume):
    assert_no_links(path)
    make_transfer_parent(path.parent.parent, volume)
    path.parent.mkdir(mode=0o777 if os.name == 'nt' else 0o700)
    sync_directory(path.parent.parent)
    return identity(path.parent)


def check_transfer_area(path, expected):
    assert_no_links(path)
    if not same_identity(path.parent, expected):
        raise SafetyError('跨卷移动的暂存区域已变化，保留现场')


def clean_transfer_area(path, expected):
    if not os.path.lexists(path.parent):
        return
    check_transfer_area(path, expected)
    path.parent.rmdir()
    sync_directory(path.parent.parent)


def stage_cross_move(journal, tx, expected_parent=None):
    before, after, authority = tx['before'], tx['after'], tx['cross']
    _, stage = transfer_paths(journal, tx)
    check = cross_guard(journal, authority, before, after)
    tx['stage_area_id'] = make_transfer_area(stage, Path(after).parent)
    journal.save_manifest(tx)
    check_transfer_area(stage, tx['stage_area_id'])
    cross_volume.copy_inventory(before, stage, tx['cross_tree'], sync_directory, check)
    check = cross_guard(journal, authority, before, after)
    verify_original(before, tx, check)
    stage_tree = cross_volume.inventory(stage, tx['stage_area_id']['dev'], check)
    if cross_volume.fingerprint(stage_tree) != tx['cross_fingerprint']:
        raise SafetyError('跨卷目标副本校验失败，保留原件')
    tx['published_id'] = identity(stage)
    journal.save_manifest(tx)
    check_transfer_area(stage, tx['stage_area_id'])
    if expected_parent is not None:
        check_reviewed_parent(Path(after).parent, expected_parent)
    cross_volume.publish(stage, after, rename_noreplace)


def verify_original(path, tx, check):
    tree = cross_volume.inventory(path, tx['identity']['dev'], check)
    if tree != tx['cross_tree']:
        raise SafetyError('跨卷移动原件已变化，保留现场')


def verify_published(path, tx, check):
    if not same_identity(path, tx.get('published_id'), content=True):
        raise SafetyError('跨卷移动的已发布副本无法识别，保留现场')
    tree = cross_volume.inventory(path, tx['published_id']['dev'], check)
    if cross_volume.fingerprint(tree) != tx['cross_fingerprint']:
        raise SafetyError('跨卷移动的已发布副本已变化，保留现场')
    return tree


def finish_cross_move(journal, tx):
    before, after, authority = tx['before'], tx['after'], tx['cross']
    original, stage = transfer_paths(journal, tx)
    check = cross_guard(journal, authority, before, after)
    published = verify_published(after, tx, check)
    if os.path.lexists(before):
        if tx.get('state') in ('purging', 'done') or os.path.lexists(original):
            raise SafetyError('跨卷原路径已被重新占用，保留现场')
        verify_original(before, tx, check)
        make_transfer_parent(original.parent, Path(before).parent)
        if tx.get('state') != 'retiring':
            tx['state'] = 'retiring'
            journal.save_manifest(tx)
        assert_no_links(original)
        cross_volume.publish(before, original, rename_noreplace)
    if os.path.lexists(original):
        if tx.get('state') not in ('retiring', 'purging') or not same_identity(original, tx['identity']):
            raise SafetyError('跨卷移动的隔离原件无法识别，保留现场')
        if tx.get('state') != 'purging':
            verify_original(original, tx, check)
            tx['state'] = 'purging'
            journal.save_manifest(tx)
        check = cross_guard(journal, authority, before, after)
        remaining = cross_volume.inventory(original, tx['identity']['dev'], check)
        cross_volume.remove_inventory(original, tx['cross_tree'], remaining, Path(after), published)
        sync_directory(original.parent)
    elif tx.get('state') not in ('purging', 'done'):
        raise SafetyError('跨卷原件位置无法确定，保留现场')
    verify_published(after, tx, check)
    clean_transfer_area(stage, tx.get('stage_area_id'))


def recover_cross_move(journal, tx):
    before, after = tx['before'], tx['after']
    if not same_identity(after, tx.get('published_id')):
        if tx.get('state') in ('retiring', 'purging', 'done') or not os.path.lexists(before):
            raise SafetyError('跨卷移动的已发布副本无法识别，保留现场')
        # The copy may be partial. Preserve it and leave the original in place.
        check = cross_guard(journal, tx['cross'], before, after)
        verify_original(before, tx, check)
        return False
    finish_cross_move(journal, tx)
    return True


def transaction(journal, run, op, effect, before, after, reason, inverse_of=None,
                cross=None, expected_fingerprint=None, expected_source=None, expected_parent=None):
    if effect == 'cross-move':
        with cross_lock(journal, cross, before, after):
            return execute_transaction(journal, run, op, effect, before, after, reason,
                                       inverse_of, cross, expected_fingerprint, expected_source, expected_parent)
    return execute_transaction(journal, run, op, effect, before, after, reason,
                               inverse_of, cross, expected_fingerprint, expected_source, expected_parent)


def execute_transaction(journal, run, op, effect, before, after, reason, inverse_of,
                        cross, expected_fingerprint, expected_source, expected_parent):
    protection = journal.protection()
    if effect == 'move':
        guard(before,journal.root,protection,descendants=True)
        guard(after,journal.root,protection)
        if not os.path.lexists(before) or os.path.lexists(after):
            raise SafetyError('源不存在或目标已存在')
        if within(after,before):
            raise SafetyError('不能把目录移动到自身内部')
        source_id = identity(before)
        if source_id['dev'] != os.stat(Path(after).parent).st_dev:
            raise SafetyError('拒绝跨卷移动；当前只支持同卷原子改名')
        edits = reference_edits(journal.root,before,after)
    elif effect == 'cross-move':
        check = cross_guard(journal, cross, before, after)
        if not os.path.lexists(before) or os.path.lexists(after):
            raise SafetyError('源不存在或目标已存在')
        source_id = identity(before)
        tree = cross_volume.inventory(before, source_id['dev'], check)
        fingerprint = cross_volume.fingerprint(tree)
        if expected_fingerprint is not None and fingerprint != expected_fingerprint:
            raise SafetyError('跨卷操作对象内容或权限已改变，保留文件')
        edits = reference_edits(journal.root,before,after)
    else:
        guard(after if effect == 'mkdir' else before,journal.root,protection,descendants=effect=='rmdir')
        source_id = identity(before) if effect == 'rmdir' else None
        edits = []
        if effect == 'mkdir' and os.path.lexists(after):
            raise SafetyError('建目录目标已存在')
        if effect == 'rmdir' and (not os.path.isdir(before) or os.listdir(before)):
            raise SafetyError('撤销建目录时目录非空，保留')
    if expected_source is not None and reviewed_identity(before) != expected_source:
        raise SafetyError('源在预演后发生变化，请重新预演')
    if expected_parent is not None:
        check_reviewed_parent(Path(after).parent, expected_parent)
    tx = {'id':uuid.uuid4().hex,'run_id':run,'op':op,'effect':effect,
          'before':str(before),'after':str(after),'reason':reason,
          'inverse_of':inverse_of,'identity':source_id,'edits':edits}
    if inverse_of:
        parent=next((r for r in completed(read_journal(journal.path)) if r['seq']==inverse_of),None)
        if parent is None:
            raise SafetyError('被撤销的日志编号不存在')
        tx['lineage']=meta(parent).get('lineage') or inverse_of
    else:
        tx['lineage']=None
    if effect=='mkdir':
        tx['staging']=str(journal.root/'_organize/staging'/tx['id'])
    elif effect == 'cross-move':
        tx.update(cross=cross, cross_tree=tree, cross_fingerprint=fingerprint)
    journal.save_manifest(tx)
    journal.append(run,op,before,after,json.dumps(tx_meta(tx),ensure_ascii=False),'pending')
    # Recheck immediately after recording intent. A failed check leaves an explicit
    # unresolved prepare for recovery, never an unrecorded filesystem operation.
    if effect == 'move':
        protection = journal.protection()
        guard(before,journal.root,protection,descendants=True)
        guard(after,journal.root,protection)
        if not same_identity(before,source_id,content=True) or os.path.lexists(after):
            raise SafetyError('操作对象或目标在预检后发生变化')
        if expected_source is not None and reviewed_identity(before) != expected_source:
            raise SafetyError('源在预演后发生变化，请重新预演')
        if expected_parent is not None:
            check_reviewed_parent(Path(after).parent, expected_parent)
        rename_noreplace(before, after)
    elif effect == 'cross-move':
        if expected_source is not None and reviewed_identity(before) != expected_source:
            raise SafetyError('源在预演后发生变化，请重新预演')
        stage_cross_move(journal, tx, expected_parent)
        finish_cross_move(journal, tx)
    elif effect == 'mkdir':
        staging=Path(tx['staging'])
        assert_no_links(staging)
        staging.parent.mkdir(parents=True,exist_ok=True)
        os.mkdir(staging)
        tx['identity'] = identity(staging)
        journal.save_manifest(tx)
        guard(after,journal.root,journal.protection())
        if tx['identity']['dev'] != os.stat(Path(after).parent).st_dev:
            raise SafetyError('目标目录不在同一卷')
        if expected_parent is not None:
            check_reviewed_parent(Path(after).parent, expected_parent)
        rename_noreplace(staging,after)
    else:
        os.rmdir(before)
    finish_references(edits)
    sync_reference_mirror(journal,edits)
    tx['state'] = 'done'
    journal.save_manifest(tx)
    journal.append(run,op,before,after,json.dumps(tx_meta(tx),ensure_ascii=False),'done')
    return tx


def tx_meta(tx):
    metadata = {k:tx[k] for k in ('reason','effect','identity','inverse_of','lineage')} | {'tx':tx['id']}
    if tx['effect'] == 'cross-move':
        metadata.update(cross=tx['cross'], cross_fingerprint=tx['cross_fingerprint'],
                        identity=tx.get('published_id'))
    return metadata


def current_identity(row, rows, active, path):
    """Redo of mkdir creates a new directory ID in the same operation lineage."""
    lineage=meta(row).get('lineage') or row['seq']
    for candidate in reversed(completed(rows)):
        data=meta(candidate)
        if (candidate['seq'] in active and (data.get('lineage') or candidate['seq'])==lineage
                and data.get('effect') in ('move','mkdir','cross-move') and key(candidate['after'])==key(path)):
            return data.get('identity')
    return meta(row).get('identity')


def existing_parent(path):
    missing = []
    current = Path(path)
    while not os.path.lexists(current):
        missing.append(current)
        current = current.parent
    return current, missing


def ensure_parents(journal, run, path, reason, reviewed=None, created=None):
    current, missing = existing_parent(path)
    expected = None
    if reviewed is not None:
        expected = created.get(key(current))
        if expected is None and key(current) == key(reviewed['parent']):
            expected = reviewed['parent_id']
        if expected is None:
            raise SafetyError('目标父目录在预演后出现或消失，请重新预演')
        check_reviewed_parent(current, expected)
    for folder in reversed(missing):
        tx = transaction(journal, run, 'mkdir', 'mkdir', '-', str(folder), reason,
                         expected_parent=expected)
        if reviewed is not None:
            expected = tx['identity']
            created[key(folder)] = expected
    return expected


def collision(path, reserved):
    path = Path(path)
    base, ext = (path.stem,path.suffix) if not path.is_dir() else (path.name,'')
    result, n = path, 1
    while os.path.lexists(result) or key(result) in reserved:
        result = path.with_name(f'{base}_{n}{ext}')
        n += 1
    return str(result)


def approval_settings(policy):
    approval = policy.get('auto_approve', {})
    if not isinstance(approval, dict):
        raise SafetyError('policy 的 auto_approve 必须是对象')
    rename = approval.get('rename', True)
    merge = approval.get('merge_to_known_zone', True)
    zones = approval.get('zones', policy.get('zones', []))
    max_gb = approval.get('max_gb', 400)
    if not isinstance(rename, bool) or not isinstance(merge, bool):
        raise SafetyError('policy 的自动批准开关必须是布尔值')
    if not isinstance(zones, list) or any(not isinstance(name, str) for name in zones):
        raise SafetyError('policy 的 zones 必须是目录名数组')
    if (type(max_gb) not in (int, float) or max_gb < 0 or
            (isinstance(max_gb, float) and not math.isfinite(max_gb))):
        raise SafetyError('policy 的 max_gb 必须是非负有限数字')
    return rename, merge, {name.casefold() for name in zones}, max_gb * 10**9


def fits_size_limit(path, limit):
    if not os.path.isdir(path):
        return os.stat(path, follow_symlinks=False).st_size <= limit

    def fail(exc):
        raise exc

    total = 0
    for directory, _, files in os.walk(path, onerror=fail):
        for name in files:
            total += os.stat(Path(directory)/name, follow_symlinks=False).st_size
            if total > limit:
                return False
    return True


def plan(args):
    """S4 审批：从单元清点和归组结果生成待批准的 actions.tsv。

    只做机械映射，不做语义判断：
      rename   → 直接生成 rename 动作
      merge    → 目标在 zones 内才生成 move
      其余     → 跳过并报告

    删除永不自动生成（policy 里没有这个开关）。
    """
    journal = Journal(args.root)
    root = journal.root
    assert_no_links(root)
    probe = Path(args.probe) if args.probe else root/'_organize'/'probe'
    assert_no_links(probe)
    if not probe.is_dir():
        raise SafetyError('探针结果目录不存在')
    policy = rules.load_root_policy(root)
    protection = journal.protection(policy)
    if args.policy:
        assert_no_links(args.policy)
        try:
            policy = json.loads(Path(args.policy).read_text(encoding='utf-8-sig'))
        except (OSError, ValueError) as exc:
            raise SafetyError('policy 文件无法解析') from exc
        if not isinstance(policy, dict):
            raise SafetyError('policy 必须是对象')
        protection.markers.extend(no_go_markers(root, policy))
    allow_rename, allow_merge, zone_names, max_bytes = approval_settings(policy)
    has_secrets = any(marker['kind'] == 'SECRET' for marker in protection.markers)

    actions, skipped, blocked = [], [], 0
    for name in sorted(os.listdir(probe)):
        if not name.endswith('.json') or name.startswith(('units_','group_','dup_')):
            continue
        path_to_probe = probe/name
        assert_no_links(path_to_probe)
        data = json.loads(path_to_probe.read_text(encoding='utf-8-sig'))
        if not isinstance(data, dict):
            raise SafetyError('探针结果必须是对象')
        path = data.get('path')
        if not isinstance(path, str) or not path:
            continue
        op = data.get('action')
        if op in ('keep', 'ask'):
            continue
        try:
            path = guard(path,root,protection,descendants=True)
        except (OSError, ValueError):
            blocked += 1
            continue
        if not os.path.lexists(path):
            continue
        # Probe notes are free text and may mention unrelated protected paths.
        reason = '探针建议（有凭据标记，省略原始说明）' if has_secrets else (
            str(data.get('unit_why') or '-').replace('\t',' ')[:200])
        if op == 'rename' and data.get('rename_to'):
            if not allow_rename:
                skipped.append((path, '策略未批准自动改名'))
                continue
            name = str(data['rename_to']).strip()
            # rename_to 必须是单个合法文件名：不含分隔符、不是说明文字
            if (not name or len(name) > 200 or
                    re.search(r'[\\/:*?"<>|\x00-\x1f]', name) or
                    name.rstrip(' .') != name):
                skipped.append((path, 'rename_to 不是合法文件名（可能是说明文字）'))
            else:
                target = Path(path).with_name(name)
                try:
                    guard(target,root,protection)
                except (OSError, ValueError):
                    blocked += 1
                    continue
                if os.path.lexists(target):
                    skipped.append((path, '改名已生效'))
                elif not fits_size_limit(path, max_bytes):
                    skipped.append((path, '超过策略的体积上限'))
                else:
                    actions.append(dict(op='rename',src=path,dst=name,reason=reason))
        elif op == 'merge' and data.get('merge_target'):
            if not allow_merge:
                skipped.append((path, '策略未批准自动合并'))
                continue
            try:
                target = Path(guard(str(data['merge_target']),root,protection))
                guard(target/Path(path).name,root,protection)
            except (OSError, ValueError):
                blocked += 1
                continue
            if key(target.parent) != key(root) or target.name.casefold() not in zone_names:
                skipped.append((path,'merge 目标不在信任区'))
            elif not fits_size_limit(path, max_bytes):
                skipped.append((path, '超过策略的体积上限'))
            else:
                actions.append(dict(op='move',src=path,dst=str(target),reason=reason))
        elif op in ('split','enter'):
            skipped.append((path,f'{op} 需要先归组，不自动生成'))
        else:
            skipped.append((path,f'未处理的 action={op}'))

    out = Path(args.out) if args.out else root/'_organize'/'plan.tsv'
    wrote = not args.dry_run and (bool(actions) or os.path.lexists(out))
    if wrote:
        atomic_write(out,encode_tsv(actions,('op','src','dst','reason')))

    if not args.quiet:
        print(f'可自动执行 {len(actions)} 项')
        for action in actions:
            print(f"  {action['op']:<7} {Path(action['src']).name[:50]}")
        if blocked:
            print(f'已拦截 {blocked} 项（不列路径）')
        if skipped:
            print(f'\n需人定 {len(skipped)} 项')
            for path,why in skipped[:40]:
                print(f'  {Path(path).name[:36]:<38} {why}')
            if len(skipped)>40:
                print(f'  ... 另有 {len(skipped)-40} 项')
        if wrote:
            print(f'\n已写 {out}')
    return 0

def read_actions(args):
    if args.rename:
        return [{'op':'rename','src':args.rename[0],'dst':args.rename[1],'reason':args.reason}]
    if args.actions:
        if not os.path.isfile(args.actions):
            raise SafetyError('动作表不存在')
        return read_tsv(args.actions,('op','src','dst','reason'))
    if not os.path.isfile(args.plan):
        raise SafetyError('决策表不存在')
    rows = read_tsv(args.plan,('path','hypothesis','evidence','verdict','confidence','target','note'))
    actions = []
    protection = Journal(args.root).protection()
    has_secrets = any(marker['kind'] == 'SECRET' for marker in protection.markers)
    for row in rows:
        if row['confidence'] not in ('high','medium'):
            continue
        reason = ('探针建议（有凭据标记，省略原始说明）' if has_secrets
                  else row['hypothesis']+' | '+row['evidence'])
        if row['verdict'] == 'split':
            raise SafetyError('split 必须先按单元归组并提供 actions.tsv；不再按扩展名自动拆分')
        if row['verdict'] == 'rename':
            actions.append(dict(op='rename',src=row['path'],dst=row['target'],reason=reason))
        elif row['verdict'] == 'merge':
            guard(row['path'],args.root,protection,descendants=True)
            source = Path(row['path'])
            children = sorted(source.iterdir()) if source.is_dir() else [source]
            actions.extend(dict(op='move',src=str(p),dst=row['target'],reason=reason) for p in children)
    return actions


def preflight(root, actions, journal=None, external_roots=(), review=False):
    journal = journal or Journal(root)
    protection = journal.protection()
    prepared, blocked, reserved = [], [], set()
    sources, source_parents, moved_targets, destination_parents = set(), set(), set(), set()
    checked_external = set()
    for index, action in enumerate(actions,1):
        try:
            op, src, dst = action['op'],action['src'],action['dst']
            cross = None
            if op == 'keep':
                continue
            if op not in ('move','rename','mkdir'):
                raise SafetyError('未知动作类型')
            if op == 'mkdir':
                target = guard(dst,root,protection)
                if os.path.isdir(target):
                    continue
                if os.path.lexists(target):
                    raise SafetyError('建目录目标是文件')
                if key(target) in moved_targets:
                    raise SafetyError('建目录目标已被前序动作占用')
                if key(target) in reserved or key(target) in destination_parents:
                    continue
                src = '-'
            else:
                src = guard(src,root,protection,descendants=True)
                if not os.path.lexists(src):
                    raise SafetyError('源不存在')
                source_key = key(src)
                parents = {os.path.normcase(str(p)) for p in Path(source_key).parents}
                if (source_key in sources or source_key in source_parents or
                        source_key in destination_parents or parents & sources):
                    raise SafetyError('源路径与前序动作重叠')
                if op == 'rename':
                    if not dst or dst in ('.','..') or re.search(r'[\\/:*?"<>|\x00-\x1f]',dst) or dst.rstrip(' .') != dst:
                        raise SafetyError('rename 的目标必须是有效文件名')
                    if os.name == 'nt' and re.match(r'(?i)^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)',dst):
                        raise SafetyError('rename 使用了系统保留名称')
                    target = guard(str(Path(src).with_name(dst)),root,protection)
                    if key(src) == key(target):
                        continue
                    if os.path.lexists(target) or key(target) in reserved:
                        raise SafetyError('目标名称已存在')
                else:
                    if os.path.isabs(dst):
                        cross = next((item for item in external_roots if within(dst, item['root'])), None)
                    if cross:
                        target = str(Path(normal(dst))/Path(src).name)
                        check = cross_guard(journal, cross, src, target)
                        if review and cross['root'] not in checked_external:
                            if Journal(cross['root']).unresolved():
                                raise SafetyError('跨卷目标根存在未完成操作；请先恢复该根')
                            checked_external.add(cross['root'])
                        if os.path.lexists(target) or key(target) in reserved:
                            raise SafetyError('跨卷移动目标已被占用')
                        cross_volume.inventory(src, identity(src)['dev'], check, content=False)
                    else:
                        destination = guard(dst,root,protection)
                        if os.path.lexists(destination) and not os.path.isdir(destination):
                            raise SafetyError('move 的目标必须是目录')
                        if key(Path(src).parent) == key(destination):
                            continue
                        target = collision(Path(destination)/Path(src).name,reserved)
                        guard(target,root,protection)
                if within(target,src):
                    raise SafetyError('不能把目录移动到自身内部')
            target_key = key(target)
            target_parents = {os.path.normcase(str(p)) for p in Path(target_key).parents}
            if target_key in sources or target_parents & sources or target_parents & moved_targets:
                raise SafetyError('目标路径依赖于前序移动的目录')
            if target_key in destination_parents:
                raise SafetyError('目标路径将由前序动作创建为父目录')
            parent, missing_parents = existing_parent(Path(target).parent)
            if not parent.is_dir():
                raise SafetyError('目标路径的父级不是目录')
            action_plan = dict(op=op, src=src, dst=target, reason=action.get('reason','-'))
            if review:
                for missing in missing_parents:
                    guard(missing, root, protection)
                action_plan['review'] = {
                    'row': index, 'parent': str(parent), 'parent_id': directory_identity(parent),
                    'source': reviewed_source(src) if src != '-' else None,
                }
            if src != '-':
                if cross_volume.different_volume(src, parent) != bool(cross):
                    raise SafetyError('跨卷移动必须显式授权其他卷上的目标根')
                reference_edits(root,src,target)  # validate JSON before any operation
                sources.add(source_key)
                source_parents.update(parents)
                moved_targets.add(target_key)
            reserved.add(target_key)
            destination_parents.update(target_parents)
            prepared.append(action_plan)
            if cross:
                prepared[-1]['cross'] = cross
        except (OSError,ValueError) as exc:
            # Intentionally omit all action paths, including SECRET descendants.
            message = str(exc) if isinstance(exc,SafetyError) else '读取失败，已停止此动作'
            blocked.append(f'动作 {index}: {message}')
    return prepared, blocked


def receipt_path(value):
    path = Path(normal(value))
    if path.suffix.casefold() != '.json' or any(p.casefold() == '_organize' for p in path.parts):
        raise SafetyError('预演清单须为 _organize 以外的 JSON 文件')
    assert_no_links(path)
    return path


def receipt_state(root, actions, prepared, external_roots):
    return {
        'version': 1,
        'root': root,
        'root_id': directory_identity(root),
        'actions_sha256': digest(json.dumps(actions, sort_keys=True).encode('utf-8')),
        'destination_roots': sorted(
            ({'path': item['root'], 'id': directory_identity(item['root'])} for item in external_roots),
            key=lambda item: item['path'],
        ),
        'steps': [
            dict(action['review'], op=action['op'], src=action['src'], dst=action['dst'])
            for action in prepared
        ],
    }


def write_receipt(path, state, journal):
    if os.path.lexists(path) or not path.parent.is_dir():
        raise SafetyError('预演清单须为现有目录下的新文件，避免覆盖')
    if within(path, journal.root):
        guard(path, journal.root, journal.protection())
    if any(within(path, step['dst']) or (step['src'] != '-' and within(path, step['src']))
           for step in state['steps']):
        raise SafetyError('预演清单不能放入将移动的源或目标目录')
    for external in state['destination_roots']:
        if within(path, external['path']):
            guard(path, external['path'], Journal(external['path']).protection())
    fd, temporary = tempfile.mkstemp(prefix='.packtidy-preview-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            json.dump(state, file, ensure_ascii=False, indent=2)
            file.write('\n')
            file.flush()
            os.fsync(file.fileno())
        assert_no_links(path)
        rename_noreplace(temporary, path)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def verify_receipt(path, actual):
    if not path.is_file():
        raise SafetyError('预演清单不存在，请先预演')
    try:
        reviewed = json.loads(path.read_text(encoding='utf-8'))
    except ValueError as exc:
        raise SafetyError('预演清单格式无效，请重新预演') from exc
    if not isinstance(reviewed, dict) or reviewed.get('version') != 1:
        raise SafetyError('预演清单格式或版本无效，请重新预演')
    if any(reviewed.get(field) != actual[field] for field in ('root', 'root_id', 'destination_roots')):
        raise SafetyError('整理根或授权目标根在预演后发生变化，请重新预演')
    if reviewed.get('actions_sha256') != actual['actions_sha256']:
        raise SafetyError('动作表在预演后发生变化，请重新预演')
    steps = reviewed.get('steps')
    if not isinstance(steps, list) or any(not isinstance(step, dict) for step in steps):
        raise SafetyError('预演清单格式无效，请重新预演')
    before = {step.get('row'): step for step in steps if type(step.get('row')) is int}
    after = {step['row']: step for step in actual['steps']}
    if steps != actual['steps']:
        changed = sorted(row for row in before.keys() | after.keys() if before.get(row) != after.get(row))
        for row in changed:
            print(f'动作 {row}: 源、最终落点或父目录在预演后发生变化')
        raise SafetyError('实际动作与预演清单不符，请重新预演')


def apply(args):
    root = normal(args.root)
    if not os.path.isdir(root):
        raise SafetyError('整理根目录不存在')
    assert_no_links(root)
    actions = read_actions(args)
    receipt = receipt_path(args.receipt) if getattr(args, 'receipt', None) else None
    if not 1 <= args.batch_size <= 200:
        raise SafetyError('批次大小必须为 1–200')
    external_roots = []
    requested = getattr(args, 'destination_root', ()) or ()
    if bool(requested) != args.allow_cross_drive:
        raise SafetyError('--allow-cross-drive 和 --destination-root 必须同时使用')
    for path in requested:
        external = check_external_root(root, path)
        if any(within(external, item['root']) or within(item['root'], external) for item in external_roots):
            raise SafetyError('授权的跨卷目标根不能重复或重叠')
        external_roots.append(cross_authority(root, external))
    journal = Journal(root)
    run = run_id()

    def execute():
        if journal.unresolved():
            raise SafetyError('存在未完成操作；请先运行 recover')
        prepared, blocked = preflight(root, actions, journal, external_roots, review=receipt is not None)
        for message in blocked:
            print('拦截 '+message)
        if receipt is not None:
            if blocked:
                raise SafetyError('整份清单已停止，请处理拦截项后重新预演')
            state = receipt_state(root, actions, prepared, external_roots)
            if args.dry_run:
                write_receipt(receipt, state, journal)
                print(f'已写预演清单：{receipt}')
            else:
                verify_receipt(receipt, state)
                journal.ensure_ready()
        if args.dry_run:
            for action in prepared:
                print(f"[dry] {action['op']}: {action['src']} -> {action['dst']}")
            note = '；只写预演清单' if receipt is not None else '；未修改文件'
            print(f'预演完成：将执行 {len(prepared)} 项，拦截 {len(blocked)} 项{note}')
            return 1 if blocked else 0
        done = 0
        created = {}
        for index,action in enumerate(prepared):
            if index and index % args.batch_size == 0:
                try:
                    answer = input('继续下一批? (y/N) ')
                except EOFError:
                    answer = 'n'
                if answer.lower() != 'y':
                    break
            op,src,target = action['op'],action['src'],action['dst']
            review = action.get('review')
            source = review['source'] if review is not None else None
            if review is not None:
                if os.path.lexists(target):
                    raise SafetyError('预演中的落点已被占用，请重新预演')
                if source is not None and reviewed_source(src) != source:
                    raise SafetyError('源在预演后发生变化，请重新预演')
            cross = action.get('cross')
            parent = Path(target).parent
            parent_id = None
            if cross:
                if review is not None:
                    parent_id = review['parent_id']
            else:
                parent_id = ensure_parents(journal, run, parent, action['reason'], review, created)
            effect = 'cross-move' if cross else 'mkdir' if op == 'mkdir' else 'move'
            tx = transaction(journal, run, op, effect, src, target, action['reason'], cross=cross,
                             expected_source=source['identity'] if source else None, expected_parent=parent_id)
            if review is not None and op == 'mkdir':
                created[key(target)] = tx['identity']
            done += 1
            print(f'{op}: {src} -> {target}')
        print(f'完成：成功 {done} | 拦截 {len(blocked)} | run_id={run}')
        print('日志: '+str(journal.path))
        return 1 if blocked else 0

    if args.dry_run:
        return execute()
    with journal.locked(prepare=receipt is None):
        return execute()


def inverse_action(row):
    m = meta(row)
    effect = m.get('effect')
    if not effect:
        raise SafetyError('旧日志缺少对象身份与事务信息；保留文件，请人工核对')
    if effect in ('move', 'cross-move'):
        return effect,row['after'],row['before']
    if effect == 'mkdir':
        return 'rmdir',row['after'],'-'
    if effect == 'rmdir':
        return 'mkdir','-',row['before']
    raise SafetyError('日志动作无法撤销')


def undo(args):
    if args.seq is not None and args.seq < 0:
        raise SafetyError('Seq 不能为负数')
    path = Path(normal(args.journal))
    if path.parent.name != '_organize' or path.name != 'journal.tsv':
        raise SafetyError('撤销需使用 <root>/_organize/journal.tsv，镜像日志请先恢复到原位置')
    root,run = path.parent.parent,run_id('undo')
    assert_no_links(path)
    journal = Journal(root,path)

    def execute():
        rows = read_journal(path)
        if args.list:
            for rid in dict.fromkeys(r['run_id'] for r in reversed(rows)):
                group = [r for r in rows if r['run_id']==rid]
                print(f"{rid}\t{len([r for r in group if r['status']=='done'])} completed")
            return 0
        if journal.unresolved():
            raise SafetyError('存在未完成操作；请先运行 recover')
        events = completed(rows)
        active = active_events(rows)
        rid = args.run_id
        if args.last:
            rid = events[-1]['run_id'] if events else None
        # A sequence boundary selects original forward operations. Selecting
        # inverse history too would toggle old undo/redo states on repetition.
        if args.seq:
            selected = [r for r in events if r['seq']<=args.seq and
                        not meta(r).get('inverse_of') and r['op'] in ('move','rename','mkdir')]
        else:
            selected = [r for r in events if r['run_id']==rid]
        counts = dict(ok=0,skip=0,conflict=0,fail=0)
        for row in reversed(selected):
            if row['seq'] not in active:
                counts['skip'] += 1
                continue
            try:
                effect,src,dst = inverse_action(row)
                protection = journal.protection()
                cross, fingerprint = None, None
                if effect == 'cross-move':
                    data = meta(row)
                    cross, fingerprint = data.get('cross', {}), data.get('cross_fingerprint', '')
                    if not isinstance(fingerprint, str) or not re.fullmatch('[0-9a-f]{64}', fingerprint):
                        raise SafetyError('跨卷历史记录缺少有效的内容校验值')
                    check = cross_guard(journal, cross, src, dst)
                if src != '-':
                    if not cross:
                        guard(src,root,protection,descendants=True)
                    if not os.path.lexists(src):
                        counts['skip'] += 1
                        continue
                    if not same_identity(src,current_identity(row,rows,active,src),content=True):
                        raise SafetyError('操作对象已被替换或修改，保留文件')
                    if cross and args.dry_run:
                        tree = cross_volume.inventory(src, identity(src)['dev'], check)
                        if cross_volume.fingerprint(tree) != fingerprint:
                            raise SafetyError('跨卷操作对象内容或权限已改变，保留文件')
                if dst != '-' and not cross:
                    guard(dst,root,protection)
                if effect == 'rmdir' and os.listdir(src):
                    counts['skip'] += 1
                    continue
                if effect != 'rmdir' and os.path.lexists(dst):
                    if row['op'] == 'rename':
                        p = Path(dst)
                        dst = collision(p.with_name(p.stem+'_undone'+p.suffix),set())
                        guard(dst,root,protection)
                    else:
                        counts['conflict'] += 1
                        continue
                if args.dry_run:
                    print(f'[dry] undo seq {row["seq"]}: {src} -> {dst}')
                else:
                    if dst != '-' and not cross:
                        ensure_parents(journal,run,Path(dst).parent,'撤销时恢复父目录')
                    transaction(journal,run,'undo-'+row['op'],effect,src,dst,
                                f'撤销 seq {row["seq"]}',inverse_of=row['seq'],cross=cross,
                                expected_fingerprint=fingerprint)
                counts['ok'] += 1
            except (OSError,ValueError) as exc:
                counts['fail'] += 1
                print(f'跳过 seq {row["seq"]}: '+(str(exc) if isinstance(exc,SafetyError) else '文件操作失败'))
                if journal.unresolved():
                    break
        print('撤销完成｜成功 {ok}｜跳过 {skip}｜冲突 {conflict}｜失败 {fail}'.format(**counts))
        if not args.dry_run and counts['ok']:
            print('run_id='+run)
        return 1 if counts['fail'] or counts['conflict'] else 0

    if args.dry_run or args.list:
        return execute()
    with journal.locked():
        return execute()


def recover(args):
    """Conservatively reconcile prepares without guessing or overwriting files."""
    journal = Journal(normal(args.root))
    with journal.locked(repair=True):
        for tx in journal.unresolved():
            effect,src,dst = tx['effect'],tx['before'],tx['after']
            if effect == 'cross-move':
                with cross_lock(journal, tx['cross'], src, dst):
                    applied = recover_cross_move(journal, tx)
                untouched = not applied
            else:
                protection = journal.protection()
                for path in (src,dst):
                    if path != '-':
                        guard(path,journal.root,protection,descendants=path==src)
            if effect == 'move':
                applied = same_identity(dst,tx['identity']) and not os.path.lexists(src)
                untouched = same_identity(src,tx['identity'])
            elif effect == 'mkdir':
                applied = same_identity(dst,tx.get('identity'))
                staging=tx.get('staging')
                # The final destination cannot be ours before staging identity
                # is durable. Preserve orphan staging directories on abort.
                untouched = (bool(staging) and not applied and
                             (not tx.get('identity') or same_identity(staging,tx['identity'])))
            elif effect == 'rmdir':
                applied = not os.path.lexists(src)
                untouched = same_identity(src,tx['identity'])
            if not applied and not untouched:
                raise SafetyError('未完成操作的对象状态不明确；保留现场并人工核对')
            if applied:
                finish_references(tx['edits'])
                sync_reference_mirror(journal,tx['edits'])
            status = 'done' if applied else 'aborted'
            journal.append(tx['run_id'],tx['op'],src,dst,json.dumps(tx_meta(tx),ensure_ascii=False),status)
            print(f"恢复事务 {tx['id']}: {status}")
    return 0


def journal_command(args):
    path = Path(normal(args.path))
    if args.action == 'read':
        print(json.dumps(read_journal(path),ensure_ascii=False))
    elif args.action == 'runs':
        rows = read_journal(path)
        result = []
        for rid in dict.fromkeys(r['run_id'] for r in reversed(rows)):
            group = [r for r in rows if r['run_id']==rid]
            result.append(dict(run_id=rid,entries=len(group),
                               moves=sum(r['op']=='move' and r['status']=='done' for r in group),
                               renames=sum(r['op']=='rename' and r['status']=='done' for r in group),
                               mkdirs=sum(r['op']=='mkdir' and r['status']=='done' for r in group),
                               failures=sum(r['status'].startswith('failed') for r in group),
                               first=group[0]['ts'],last=group[-1]['ts']))
        print(json.dumps(result,ensure_ascii=False))
    else:
        if path.parent.name != '_organize' or path.name != 'journal.tsv':
            raise SafetyError('日志必须位于 <root>/_organize/journal.tsv')
        journal = Journal(path.parent.parent,path)
        with journal.locked():
            print(journal.append(args.run_id,args.op,args.before,args.after,args.reason,args.status))
    return 0


def marker_command(args):
    root = normal(args.root)
    assert_no_links(root)
    journal = Journal(root)

    def execute():
        if journal.pending_marker_update():
            raise SafetyError('保护标记写入尚未完成；先运行 recover')
        journal.check_marker_files()
        rows = load_markers(root)
        if args.action in ('add','remove','check'):
            if not args.path or not os.path.isabs(args.path):
                raise SafetyError('需要绝对路径 --path')
            path = normal(args.path)
            if not within(path,root):
                raise SafetyError('标记路径越界')
        if args.action == 'check':
            from safety import marker_hits
            hits = marker_hits(path,rows,descendants=True)
            kind = block_kind(path,rows,descendants=True)
            print(kind or ('NOTE' if hits else 'no-marker'))
            return 0 if hits else 1
        if args.action in ('add','remove'):
            if args.action == 'add':
                if not args.kind:
                    raise SafetyError('add 需要 --kind')
                if args.kind=='FOREIGN' and args.by!='user':
                    raise SafetyError('FOREIGN 必须由用户声明')
                rows = [r for r in rows if not (key(r['path'])==key(path) and r['kind']==args.kind)]
                rows.append(dict(path=path,kind=args.kind,by=args.by,note=args.note,ts=now()))
            else:
                rows = [r for r in rows if key(r['path'])!=key(path)]
            data = encode_tsv(rows,MARKER_FIELDS)
            journal.update_markers(data)
            print('标记已更新')
        elif args.action == 'secrets':
            print(f"SECRET 项 {sum(r['kind']=='SECRET' for r in rows)} 处（不列路径）")
        else:
            visible = [r for r in rows if block_kind(r['path'],rows)!='SECRET' and
                       (args.action!='pending' or r['kind']=='ASK')]
            for row in visible:
                print(f"{row['path']}\t{row['kind']}\t{row['by']}\t{row['note']}")
            if args.action=='list':
                print(f"SECRET 项 {sum(r['kind']=='SECRET' for r in rows)} 处（不列路径）")
        return 0

    if args.action in ('add','remove'):
        with journal.locked():
            if journal.unresolved():
                raise SafetyError('存在未完成操作；先恢复后再更改标记')
            return execute()
    return execute()


def main(argv=None):
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command',required=True)
    p = sub.add_parser('apply')
    p.add_argument('--root',required=True)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('--actions')
    source.add_argument('--plan')
    source.add_argument('--rename',nargs=2)
    p.add_argument('--reason',default='-')
    p.add_argument('--dry-run',action='store_true')
    p.add_argument('--receipt')
    p.add_argument('--batch-size',type=int,default=50)
    p.add_argument('--allow-cross-drive',action='store_true')
    p.add_argument('--destination-root',action='append',default=[])
    p = sub.add_parser('undo')
    p.add_argument('--journal',required=True)
    selection = p.add_mutually_exclusive_group(required=True)
    selection.add_argument('--run-id')
    selection.add_argument('--last',action='store_true')
    selection.add_argument('--seq',type=int)
    selection.add_argument('--list',action='store_true')
    p.add_argument('--dry-run',action='store_true')
    p = sub.add_parser('plan')
    p.add_argument('--root',required=True)
    p.add_argument('--probe')
    p.add_argument('--policy', help='计划的审批策略，可指定额外的禁区')
    p.add_argument('--out')
    p.add_argument('--dry-run',action='store_true')
    p.add_argument('--quiet',action='store_true')
    p = sub.add_parser('recover')
    p.add_argument('--root',required=True)
    p = sub.add_parser('journal')
    p.add_argument('--path',required=True)
    p.add_argument('--action',choices=('read','runs','append'),required=True)
    p.add_argument('--run-id',default='note')
    p.add_argument('--op',default='note')
    p.add_argument('--before',default='-')
    p.add_argument('--after',default='-')
    p.add_argument('--reason',default='-')
    p.add_argument('--status',default='done')
    p = sub.add_parser('markers')
    p.add_argument('--root',required=True)
    p.add_argument('--action',choices=('list','add','remove','check','pending','secrets'),default='list')
    p.add_argument('--path')
    p.add_argument('--kind',choices=('SECRET','FOREIGN','WORKSPACE','NO_TOUCH','ASK','NOTE'))
    p.add_argument('--by',choices=('user','inferred'),default='user')
    p.add_argument('--note',default='-')
    args = parser.parse_args(argv)
    try:
        return {'apply':apply,'undo':undo,'plan':plan,'recover':recover,
                'journal':journal_command,'markers':marker_command}[args.command](args)
    except (OSError,ValueError) as exc:
        # OS exception strings may include a SECRET pathname. Keep CLI diagnostics
        # generic; recovery manifests contain paths only for authorized operations.
        print('停止: '+(str(exc) if isinstance(exc,SafetyError) else type(exc).__name__+'；请检查日志并运行 recover'),file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
