"""Journaled file operations. PowerShell entry points delegate to this module.

The TSV remains append-only with eight columns. Each mutation has a durable
prepare record in both journals and a mirrored recovery manifest before it runs.
The manifest contains exact paths, object identity and planned reference edits.
Interrupted operations block further work until `recover` reconciles them.
"""
import argparse
from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import uuid

from safety import (SafetyError, MARKER_FIELDS, PROTECTED_NAMES, assert_no_links,
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

    @contextmanager
    def locked(self, repair=False):
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
        if tx.get('effect') not in ('move','mkdir','rmdir'):
            raise SafetyError('恢复记录动作无效')
        if tx.get('staging') and not within(tx['staging'],self.root/'_organize/staging',inclusive=False):
            raise SafetyError('恢复暂存路径越界')
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


def guard(path, root, markers, descendants=False, allow_root=False):
    if not os.path.isabs(str(path)):
        raise SafetyError('动作路径必须是绝对路径')
    path = normal(path)
    if not within(path, root, inclusive=allow_root):
        raise SafetyError('路径越界或指向整理根目录')
    kind = block_kind(path, markers, descendants)
    if kind:
        raise SafetyError('凭据类路径已排除' if kind == 'SECRET' else '命中保护标记 ' + kind)
    assert_no_links(path)
    if any(part.casefold() in PROTECTED_NAMES for part in Path(path).parts):
        raise SafetyError('命中受保护目录')
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
        root_key = key(path)
        for folder, dirs, files in os.walk(path, followlinks=False, onerror=fail):
            for name in dirs + files:
                child = Path(folder)/name
                if is_reparse(child):
                    raise SafetyError('目录包含链接或重解析项')
                if name.casefold() not in PROTECTED_NAMES or not child.is_dir():
                    continue
                if name.casefold() in env_dirs:
                    # 环境目录只在两种情况下拦：
                    #   1. 它本身就是被移动对象
                    #   2. 它在项目根下一层（典型 venv 位置）
                    # 深处的一律放过——那是程序自带的资源
                    # （如 PyCharm 的 typeshed/stdlib/venv 只是类型存根目录）
                    depth = len(Path(child).relative_to(path).parts)
                    if key(child) != root_key and depth > 2:
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


def transaction(journal, run, op, effect, before, after, reason, inverse_of=None):
    markers = load_markers(journal.root)
    if effect == 'move':
        guard(before,journal.root,markers,descendants=True)
        guard(after,journal.root,markers)
        if not os.path.lexists(before) or os.path.lexists(after):
            raise SafetyError('源不存在或目标已存在')
        if within(after,before):
            raise SafetyError('不能把目录移动到自身内部')
        source_id = identity(before)
        if source_id['dev'] != os.stat(Path(after).parent).st_dev:
            raise SafetyError('拒绝跨卷移动；当前只支持同卷原子改名')
        edits = reference_edits(journal.root,before,after)
    else:
        guard(after if effect == 'mkdir' else before,journal.root,markers,descendants=effect=='rmdir')
        source_id = identity(before) if effect == 'rmdir' else None
        edits = []
        if effect == 'mkdir' and os.path.lexists(after):
            raise SafetyError('建目录目标已存在')
        if effect == 'rmdir' and (not os.path.isdir(before) or os.listdir(before)):
            raise SafetyError('撤销建目录时目录非空，保留')
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
    journal.save_manifest(tx)
    journal.append(run,op,before,after,json.dumps(tx_meta(tx),ensure_ascii=False),'pending')
    # Recheck immediately after recording intent. A failed check leaves an explicit
    # unresolved prepare for recovery, never an unrecorded filesystem operation.
    if effect == 'move':
        guard(before,journal.root,load_markers(journal.root),descendants=True)
        guard(after,journal.root,load_markers(journal.root))
        if not same_identity(before,source_id,content=True) or os.path.lexists(after):
            raise SafetyError('操作对象或目标在预检后发生变化')
        rename_noreplace(before, after)
    elif effect == 'mkdir':
        staging=Path(tx['staging'])
        assert_no_links(staging)
        staging.parent.mkdir(parents=True,exist_ok=True)
        os.mkdir(staging)
        tx['identity'] = identity(staging)
        journal.save_manifest(tx)
        guard(after,journal.root,load_markers(journal.root))
        if tx['identity']['dev'] != os.stat(Path(after).parent).st_dev:
            raise SafetyError('目标目录不在同一卷')
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
    return {k:tx[k] for k in ('reason','effect','identity','inverse_of','lineage')} | {'tx':tx['id']}


def current_identity(row, rows, active, path):
    """Redo of mkdir creates a new directory ID in the same operation lineage."""
    lineage=meta(row).get('lineage') or row['seq']
    for candidate in reversed(completed(rows)):
        data=meta(candidate)
        if (candidate['seq'] in active and (data.get('lineage') or candidate['seq'])==lineage
                and data.get('effect') in ('move','mkdir') and key(candidate['after'])==key(path)):
            return data.get('identity')
    return meta(row).get('identity')


def ensure_parents(journal,run,path,reason):
    missing = []
    current = Path(path)
    while not current.exists():
        missing.append(current)
        current = current.parent
    for folder in reversed(missing):
        transaction(journal,run,'mkdir','mkdir','-',str(folder),reason)


def collision(path, reserved):
    path = Path(path)
    base, ext = (path.stem,path.suffix) if not path.is_dir() else (path.name,'')
    result, n = path, 1
    while os.path.lexists(result) or key(result) in reserved:
        result = path.with_name(f'{base}_{n}{ext}')
        n += 1
    return str(result)


DEFAULT_POLICY = {
    'auto_approve': {
        'rename': True,
        'merge_to_known_zone': True,
        'zones': [],
        'max_gb': 400,
    },
    'never_touch': ['SECRET', 'FOREIGN', 'WORKSPACE', 'NO_TOUCH', 'ASK'],
}


def load_policy(path):
    """读 policy。缺失时用保守默认（只自动改名，跨目录需人批）。"""
    if not path:
        return dict(DEFAULT_POLICY)
    assert_no_links(path)
    try:
        import json
        raw = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as exc:
        raise SafetyError('policy 文件无法解析') from exc
    policy = dict(DEFAULT_POLICY)
    for key, value in (raw or {}).items():
        if not key.startswith('_'):
            policy[key] = value
    return policy


def plan(args):
    """S4 审批：从单元清点和归组结果生成待批准的 actions.tsv。

    只做机械映射，不做语义判断：
      rename   → 直接生成 rename 动作
      merge    → 目标在 zones 内才生成 move
      其余     → 跳过并报告

    删除永不自动生成（policy 里没有这个开关）。
    """
    probe = Path(args.probe) if args.probe else Path(args.root)/'_organize'/'probe'
    if not probe.is_dir():
        raise SafetyError('探针结果目录不存在')
    policy = load_policy(args.policy)
    zone_names = {str(name).casefold() for name in
                  (policy.get('auto_approve',{}) or {}).get('zones',[]) or []}
    markers = load_markers(args.root)

    actions, skipped, blocked = [], [], []
    for name in sorted(os.listdir(probe)):
        if not name.endswith('.json') or name.startswith(('units_','group_','dup_')):
            continue
        data = json.loads((probe/name).read_text(encoding='utf-8-sig'))
        path = data.get('path')
        if not path or not os.path.lexists(path):
            continue
        op = data.get('action')
        reason = str(data.get('unit_why') or '-').replace('\t',' ')[:200]
        if op == 'rename' and data.get('rename_to'):
            name = str(data['rename_to']).strip()
            # rename_to 必须是单个合法文件名：不含分隔符、不是说明文字
            if (not name or len(name) > 200 or
                    re.search(r'[\\/:*?"<>|\x00-\x1f]', name) or
                    name.rstrip(' .') != name):
                skipped.append((path, 'rename_to 不是合法文件名（可能是说明文字）'))
            else:
                target = Path(path).with_name(name)
                if os.path.lexists(target):
                    skipped.append((path, '改名已生效'))
                else:
                    actions.append(dict(op='rename',src=path,dst=name,reason=reason))
        elif op == 'merge' and data.get('merge_target'):
            target = Path(str(data['merge_target']))
            zone = target.name.casefold()
            if zone_names and zone in zone_names:
                actions.append(dict(op='move',src=path,dst=str(target),reason=reason))
            else:
                skipped.append((path,'merge 目标不在信任区'))
        elif op in ('split','enter'):
            skipped.append((path,f'{op} 需要先归组，不自动生成'))
        elif op in ('keep','ask'):
            continue
        else:
            skipped.append((path,f'未处理的 action={op}'))

    out = Path(args.out) if args.out else Path(args.root)/'_organize'/'plan.tsv'
    if actions and not args.dry_run:
        rows = [dict(op=a['op'],src=a['src'],dst=a['dst'],reason=a['reason']) for a in actions]
        data = encode_tsv(rows,('op','src','dst','reason'))
        assert_no_links(out.parent) if out.parent.exists() else out.parent.mkdir(parents=True,exist_ok=True)
        atomic_write(out,data)

    if not args.quiet:
        print(f'可自动执行 {len(actions)} 项')
        for action in actions:
            print(f"  {action['op']:<7} {Path(action['src']).name[:50]}")
        if skipped:
            print(f'\n需人定 {len(skipped)} 项')
            for path,why in skipped[:40]:
                print(f'  {Path(path).name[:36]:<38} {why}')
            if len(skipped)>40:
                print(f'  ... 另有 {len(skipped)-40} 项')
        if actions and not args.dry_run:
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
    markers = load_markers(args.root)
    for row in rows:
        if row['confidence'] not in ('high','medium'):
            continue
        reason = row['hypothesis']+' | '+row['evidence']
        if row['verdict'] == 'split':
            raise SafetyError('split 必须先按单元归组并提供 actions.tsv；不再按扩展名自动拆分')
        if row['verdict'] == 'rename':
            actions.append(dict(op='rename',src=row['path'],dst=row['target'],reason=reason))
        elif row['verdict'] == 'merge':
            guard(row['path'],args.root,markers,descendants=True)
            source = Path(row['path'])
            children = sorted(source.iterdir()) if source.is_dir() else [source]
            actions.extend(dict(op='move',src=str(p),dst=row['target'],reason=reason) for p in children)
    return actions


def preflight(root, actions):
    markers, prepared, blocked, reserved = load_markers(root), [], [], set()
    for index, action in enumerate(actions,1):
        try:
            op, src, dst = action['op'],action['src'],action['dst']
            if op == 'keep':
                continue
            if op not in ('move','rename','mkdir'):
                raise SafetyError('未知动作类型')
            if op == 'mkdir':
                target = guard(dst,root,markers)
                if os.path.isdir(target):
                    continue
                if os.path.lexists(target):
                    raise SafetyError('建目录目标是文件')
                src = '-'
            else:
                src = guard(src,root,markers,descendants=True)
                if not os.path.lexists(src):
                    raise SafetyError('源不存在')
                if op == 'rename':
                    if not dst or dst in ('.','..') or re.search(r'[\\/:\x00-\x1f]',dst) or dst.rstrip(' .') != dst:
                        raise SafetyError('rename 的目标必须是有效文件名')
                    if os.name == 'nt' and re.match(r'(?i)^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)',dst):
                        raise SafetyError('rename 使用了系统保留名称')
                    target = guard(str(Path(src).with_name(dst)),root,markers)
                    if key(src) == key(target):
                        continue
                    if os.path.lexists(target) or key(target) in reserved:
                        raise SafetyError('目标名称已存在')
                else:
                    destination = guard(dst,root,markers)
                    if os.path.lexists(destination) and not os.path.isdir(destination):
                        raise SafetyError('move 的目标必须是目录')
                    if key(Path(src).parent) == key(destination):
                        continue
                    target = collision(Path(destination)/Path(src).name,reserved)
                    guard(target,root,markers)
                if within(target,src):
                    raise SafetyError('不能把目录移动到自身内部')
                reference_edits(root,src,target)  # validate JSON before any operation
            reserved.add(key(target))
            prepared.append(dict(op=op,src=src,dst=target,reason=action.get('reason','-')))
        except (OSError,ValueError) as exc:
            # Intentionally omit all action paths, including SECRET descendants.
            message = str(exc) if isinstance(exc,SafetyError) else '读取失败，已停止此动作'
            blocked.append(f'动作 {index}: {message}')
    return prepared, blocked


def apply(args):
    root = normal(args.root)
    if not os.path.isdir(root):
        raise SafetyError('整理根目录不存在')
    assert_no_links(root)
    actions = read_actions(args)
    if not 1 <= args.batch_size <= 200:
        raise SafetyError('批次大小必须为 1–200')
    if args.allow_cross_drive:
        raise SafetyError('当前版本只支持同卷移动，不能启用跨盘')
    journal = Journal(root)
    run = run_id()

    def execute():
        if journal.unresolved():
            raise SafetyError('存在未完成操作；请先运行 recover')
        prepared, blocked = preflight(root,actions)
        for message in blocked:
            print('拦截 '+message)
        if args.dry_run:
            for action in prepared:
                print(f"[dry] {action['op']}: {action['src']} -> {action['dst']}")
            print(f'预演完成：将执行 {len(prepared)} 项，拦截 {len(blocked)} 项；未修改文件')
            return 1 if blocked else 0
        done = 0
        for index,action in enumerate(prepared):
            if index and index % args.batch_size == 0:
                try:
                    answer = input('继续下一批? (y/N) ')
                except EOFError:
                    answer = 'n'
                if answer.lower() != 'y':
                    break
            op,src,target = action['op'],action['src'],action['dst']
            ensure_parents(journal,run,Path(target).parent,action['reason'])
            transaction(journal,run,op,'mkdir' if op=='mkdir' else 'move',src,target,action['reason'])
            done += 1
            print(f'{op}: {src} -> {target}')
        print(f'完成：成功 {done} | 拦截 {len(blocked)} | run_id={run}')
        print('日志: '+str(journal.path))
        return 1 if blocked else 0

    if args.dry_run:
        return execute()
    with journal.locked():
        return execute()


def inverse_action(row):
    m = meta(row)
    effect = m.get('effect')
    if not effect:
        raise SafetyError('旧日志缺少对象身份与事务信息；保留文件，请人工核对')
    if effect == 'move':
        return 'move',row['after'],row['before']
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
                markers = load_markers(root)
                if src != '-':
                    guard(src,root,markers,descendants=True)
                    if not os.path.lexists(src):
                        counts['skip'] += 1
                        continue
                    if not same_identity(src,current_identity(row,rows,active,src),content=True):
                        raise SafetyError('操作对象已被替换或修改，保留文件')
                if dst != '-':
                    guard(dst,root,markers)
                if effect == 'rmdir' and os.listdir(src):
                    counts['skip'] += 1
                    continue
                if effect != 'rmdir' and os.path.lexists(dst):
                    if row['op'] == 'rename':
                        p = Path(dst)
                        dst = collision(p.with_name(p.stem+'_undone'+p.suffix),set())
                        guard(dst,root,markers)
                    else:
                        counts['conflict'] += 1
                        continue
                if args.dry_run:
                    print(f'[dry] undo seq {row["seq"]}: {src} -> {dst}')
                else:
                    if dst != '-':
                        ensure_parents(journal,run,Path(dst).parent,'撤销时恢复父目录')
                    transaction(journal,run,'undo-'+row['op'],effect,src,dst,
                                f'撤销 seq {row["seq"]}',inverse_of=row['seq'])
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
            markers = load_markers(journal.root)
            for path in (src,dst):
                if path != '-':
                    guard(path,journal.root,markers,descendants=path==src)
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
            else:
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
    p.add_argument('--batch-size',type=int,default=50)
    p.add_argument('--allow-cross-drive',action='store_true')
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
    p.add_argument('--policy')
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
