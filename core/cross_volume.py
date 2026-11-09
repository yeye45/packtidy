"""Strict inventory and verification for the copy part of a cross-volume move."""
from contextlib import contextmanager
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
from types import SimpleNamespace

from safety import SafetyError, assert_no_links


def different_volume(first, second):
    return os.stat(first, follow_symlinks=False).st_dev != os.stat(second, follow_symlinks=False).st_dev


@lru_cache(maxsize=1)
def windows_api():
    import ctypes
    from ctypes import wintypes as w

    class Stream(ctypes.Structure):
        _fields_ = [('size', ctypes.c_longlong), ('name', w.WCHAR * 296)]

    class IoStatus(ctypes.Structure):
        _fields_ = [('status_or_pointer', ctypes.c_void_p), ('information', ctypes.c_size_t)]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    first = kernel.FindFirstStreamW
    first.argtypes = (w.LPCWSTR, ctypes.c_int, ctypes.POINTER(Stream), w.DWORD)
    first.restype = w.HANDLE
    next_stream = kernel.FindNextStreamW
    next_stream.argtypes = (w.HANDLE, ctypes.POINTER(Stream))
    next_stream.restype = w.BOOL
    close = kernel.FindClose
    close.argtypes = (w.HANDLE,)
    close.restype = w.BOOL
    security = ctypes.WinDLL('advapi32', use_last_error=True).GetFileSecurityW
    security.argtypes = (w.LPCWSTR, w.DWORD, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD))
    security.restype = w.BOOL
    move = kernel.MoveFileExW
    move.argtypes = (w.LPCWSTR, w.LPCWSTR, w.DWORD)
    move.restype = w.BOOL
    drive_type = kernel.GetDriveTypeW
    drive_type.argtypes = (w.LPCWSTR,)
    drive_type.restype = w.UINT
    volume_information = kernel.GetVolumeInformationW
    volume_information.argtypes = (w.LPCWSTR, w.LPWSTR, w.DWORD, ctypes.POINTER(w.DWORD),
                                   ctypes.POINTER(w.DWORD), ctypes.POINTER(w.DWORD), w.LPWSTR, w.DWORD)
    volume_information.restype = w.BOOL
    create = kernel.CreateFileW
    create.argtypes = (w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE)
    create.restype = w.HANDLE
    close_handle = kernel.CloseHandle
    close_handle.argtypes = (w.HANDLE,)
    close_handle.restype = w.BOOL
    set_time = kernel.SetFileTime
    set_time.argtypes = (w.HANDLE, ctypes.POINTER(w.FILETIME), ctypes.POINTER(w.FILETIME), ctypes.POINTER(w.FILETIME))
    set_time.restype = w.BOOL
    set_attributes = kernel.SetFileAttributesW
    set_attributes.argtypes = (w.LPCWSTR, w.DWORD)
    set_attributes.restype = w.BOOL
    disposition = kernel.SetFileInformationByHandle
    disposition.argtypes = (w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD)
    disposition.restype = w.BOOL
    query = ctypes.WinDLL('ntdll').NtQueryInformationFile
    query.argtypes = (w.HANDLE, ctypes.POINTER(IoStatus), w.LPVOID, w.ULONG, ctypes.c_int)
    query.restype = w.LONG
    return SimpleNamespace(ctypes=ctypes, w=w, Stream=Stream, IoStatus=IoStatus, first=first,
                           next_stream=next_stream, close=close, security=security, move=move,
                           drive_type=drive_type, volume_information=volume_information,
                           create=create, close_handle=close_handle,
                           set_time=set_time, set_attributes=set_attributes, query=query,
                           disposition=disposition)


def require_local_root(path):
    if os.name != 'nt':
        raise SafetyError('跨卷移动目前只支持 Windows 本地 NTFS 固定磁盘')
    api = windows_api()
    root = Path(path).anchor
    filesystem = api.ctypes.create_unicode_buffer(32)
    if (api.drive_type(root) != 3 or not api.volume_information(
            root, None, 0, None, None, None, filesystem, len(filesystem)) or filesystem.value != 'NTFS'):
        raise SafetyError('跨卷移动目前只支持 Windows 本地 NTFS 固定磁盘')


def windows_permissions(path):
    api = windows_api()
    ctypes = api.ctypes
    # Owner, group, DACL, integrity label, resource attributes, and policy scope.
    info = 0x77
    needed = api.w.DWORD()
    if not api.security(str(path), info, None, 0, ctypes.byref(needed)):
        if ctypes.get_last_error() != 122:  # ERROR_INSUFFICIENT_BUFFER
            raise ctypes.WinError(ctypes.get_last_error())
    descriptor = ctypes.create_string_buffer(needed.value)
    if not api.security(str(path), info, descriptor, needed, ctypes.byref(needed)):
        raise ctypes.WinError(ctypes.get_last_error())
    return hashlib.sha256(descriptor.raw).hexdigest()


def windows_check_streams(path, directory):
    api = windows_api()
    ctypes = api.ctypes
    stream = api.Stream()
    handle = api.first(str(path), 0, ctypes.byref(stream), 0)
    if handle == ctypes.c_void_p(-1).value:
        error = ctypes.get_last_error()
        if directory and error == 38:  # ERROR_HANDLE_EOF
            return
        raise ctypes.WinError(error)
    try:
        if stream.name != '::$DATA' or api.next_stream(handle, ctypes.byref(stream)):
            raise SafetyError('跨卷移动拒绝带备用数据流的对象')
        if ctypes.get_last_error() != 38:
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        api.close(handle)


@contextmanager
def windows_metadata_handle(path, access):
    api = windows_api()
    # Read/write/delete sharing, OPEN_EXISTING, BACKUP_SEMANTICS | OPEN_REPARSE_POINT.
    handle = api.create(str(path), access, 7, None, 3, 0x02200000, None)
    if handle == api.ctypes.c_void_p(-1).value:
        raise api.ctypes.WinError(api.ctypes.get_last_error())
    try:
        yield handle
    finally:
        api.close_handle(handle)


def windows_check_extended_attributes(path):
    api = windows_api()
    size = api.w.ULONG()
    with windows_metadata_handle(path, 8) as handle:  # FILE_READ_EA
        status = api.query(handle, api.ctypes.byref(api.IoStatus()), api.ctypes.byref(size), 4, 7)
    if status != 0 or size.value:
        raise SafetyError('无法验证扩展属性为空，拒绝跨卷移动')


def windows_copy_attributes(path, record):
    api = windows_api()
    ctypes = api.ctypes
    created = record['created_ns'] // 100 + 116444736000000000
    time = api.w.FILETIME(created & 0xffffffff, created >> 32)
    with windows_metadata_handle(path, 0x100) as handle:  # FILE_WRITE_ATTRIBUTES
        if not api.set_time(handle, ctypes.byref(time), None, None):
            raise ctypes.WinError(ctypes.get_last_error())
    # Directory is an intrinsic attribute. Set the archive flag when present.
    if not api.set_attributes(str(path), record['attributes'] & 0x20 or 0x80):
        raise ctypes.WinError(ctypes.get_last_error())


def publish(before, after, rename_noreplace):
    if os.name != 'nt':
        rename_noreplace(before, after)
        return
    api = windows_api()
    # MOVEFILE_WRITE_THROUGH, without COPY_ALLOWED or REPLACE_EXISTING.
    if not api.move(str(before), str(after), 8):
        raise api.ctypes.WinError(api.ctypes.get_last_error())


def file_stat(path, volume):
    assert_no_links(path)
    st = os.stat(path, follow_symlinks=False)
    directory = stat.S_ISDIR(st.st_mode)
    if not st.st_ino or st.st_dev != volume:
        raise SafetyError('跨卷移动拒绝卷内挂载点或无法识别的对象')
    if not directory and (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1):
        raise SafetyError('跨卷移动只支持目录和无硬链接的普通文件')
    if st.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
        raise SafetyError('跨卷移动拒绝带特殊权限位的对象')
    attributes = {}
    if os.name == 'nt':
        # Ordinary directory, archive and normal attributes are supported.
        if st.st_file_attributes & ~0xB0:
            raise SafetyError('跨卷移动拒绝带特殊 Windows 属性的对象')
        windows_check_streams(path, directory)
        windows_check_extended_attributes(path)
        permissions = windows_permissions(path)
        attributes = dict(created_ns=getattr(st, 'st_birthtime_ns', st.st_ctime_ns),
                          attributes=st.st_file_attributes)
    else:
        if hasattr(os, 'listxattr') and os.listxattr(path, follow_symlinks=False):
            raise SafetyError('跨卷移动拒绝带扩展属性或 ACL 的对象')
        permissions = [st.st_uid, st.st_gid]
    return dict(dev=st.st_dev, ino=st.st_ino, dir=directory, mode=stat.S_IMODE(st.st_mode),
                permissions=permissions, mtime_ns=st.st_mtime_ns, size=None if directory else st.st_size,
                **attributes)


def unchanged_handle(st, expected):
    return ((st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_nlink) ==
            (expected['dev'], expected['ino'], expected['size'], expected['mtime_ns'], 1))


def open_reader(path, *, delete=False):
    if os.name != 'nt':
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
        return os.open(path, flags)
    import msvcrt

    api = windows_api()
    # GENERIC_READ | optional DELETE, FILE_SHARE_READ, OPEN_EXISTING, and
    # BACKUP_SEMANTICS | OPEN_REPARSE_POINT allow pinning exact directories too.
    access = 0x80010000 if delete else 0x80000000
    handle = api.create(str(path), access, 1, None, 3, 0x02200000, None)
    if handle == api.ctypes.c_void_p(-1).value:
        raise api.ctypes.WinError(api.ctypes.get_last_error())
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except OSError:
        api.close_handle(handle)
        raise


@contextmanager
def verified_reader(path, expected):
    assert_no_links(path)
    with os.fdopen(open_reader(path), 'rb') as stream:
        if not unchanged_handle(os.fstat(stream.fileno()), expected):
            raise SafetyError('跨卷移动时源文件发生变化')
        yield stream
        if not unchanged_handle(os.fstat(stream.fileno()), expected):
            raise SafetyError('跨卷移动时源文件发生变化')


def file_digest(path, expected):
    with verified_reader(path, expected) as stream:
        return stream_digest(stream)


def stream_digest(stream):
    result = hashlib.sha256()
    for data in iter(lambda: stream.read(1024 * 1024), b''):
        result.update(data)
    return result.hexdigest()


def inventory(path, volume, check=None, *, content=True):
    """Inventory only this tree. Validate each relative path before opening it."""
    result = {}
    root = Path(path)
    todo = [root]
    while todo:
        current = todo.pop()
        relative = current.relative_to(root).as_posix()
        if check is not None:
            check(relative)
        record = file_stat(current, volume)
        if record['dir']:
            todo.extend(sorted(current.iterdir(), reverse=True))
        elif content:
            record['sha256'] = file_digest(current, record)
        result[relative] = record
    return result


def fingerprint(tree):
    records = {
        name: {k: v for k, v in entry.items() if k not in ('dev', 'ino')}
        for name, entry in tree.items()
    }
    return hashlib.sha256(json.dumps(records, sort_keys=True).encode('ascii')).hexdigest()


def copy_inventory(source, target, tree, sync_directory, check):
    """Create only new entries. Check destination permissions before writing data."""
    source, target = Path(source), Path(target)
    volume = os.stat(target.parent, follow_symlinks=False).st_dev
    directories = []
    for relative, record in tree.items():
        check(relative)
        before, after = source / relative, target / relative
        assert_no_links(after)
        if record['dir']:
            after.mkdir()
            directories.append((before, after, record))
            if file_stat(after, volume)['permissions'] != record['permissions']:
                raise SafetyError('目标不能保留源目录权限')
        else:
            with verified_reader(before, record) as reader, open(after, 'xb') as writer:
                if file_stat(after, volume)['permissions'] != record['permissions']:
                    raise SafetyError('目标不能保留源文件权限')
                shutil.copyfileobj(reader, writer, length=1024 * 1024)
                writer.flush()
                shutil.copystat(before, after, follow_symlinks=False)
                if os.name == 'nt':
                    windows_copy_attributes(after, record)
                os.fsync(writer.fileno())
    for before, after, record in reversed(directories):
        shutil.copystat(before, after, follow_symlinks=False)
        if os.name == 'nt':
            windows_copy_attributes(after, record)
        sync_directory(after)
    sync_directory(target.parent)


def remove_inventory(path, original, remaining, target, published):
    """Delete only verified entries from a private quarantine, deepest first."""
    path = Path(path)
    deepest_first = sorted(remaining, key=lambda item: (item != '.', item.count('/')), reverse=True)
    for relative in deepest_first:
        before = original.get(relative)
        current = remaining[relative]
        if before is None or not matches_original(current, before):
            raise SafetyError('隔离原件与记录不一致，保留现场')
    for relative in deepest_first:
        child = path / relative
        record = file_stat(child, remaining[relative]['dev'])
        copy = target / relative
        duplicate = file_stat(copy, published[relative]['dev'])
        if record['dir']:
            verify_purge_copy(duplicate, published[relative])
            remove_original(child, record, original[relative])
        else:
            with verified_reader(copy, duplicate) as reader:
                duplicate['sha256'] = stream_digest(reader)
                if not unchanged_handle(os.fstat(reader.fileno()), duplicate):
                    raise SafetyError('跨卷已发布副本在清理前发生变化，保留隔离原件')
                verify_purge_copy(duplicate, published[relative])
                remove_original(child, record, original[relative])


def verify_purge_copy(actual, expected):
    if actual != expected:
        raise SafetyError('跨卷已发布副本在清理前发生变化，保留隔离原件')


def verify_purge_original(actual, expected):
    if not matches_original(actual, expected):
        raise SafetyError('隔离原件在清理前发生变化，保留现场')


def remove_original(path, record, expected):
    if os.name != 'nt':
        if not record['dir']:
            record['sha256'] = file_digest(path, record)
        verify_purge_original(record, expected)
        path.rmdir() if record['dir'] else path.unlink()
        return
    import msvcrt

    api = windows_api()
    descriptor = open_reader(path, delete=True)
    try:
        st = os.fstat(descriptor)
        if (st.st_dev, st.st_ino, stat.S_ISDIR(st.st_mode)) != (record['dev'], record['ino'], record['dir']):
            raise SafetyError('隔离原件在清理前被替换，保留现场')
        record = file_stat(path, record['dev'])
        if record['ino'] != st.st_ino:
            raise SafetyError('隔离原件在清理前被替换，保留现场')
        if not record['dir']:
            with os.fdopen(descriptor, 'rb', closefd=False) as stream:
                record['sha256'] = stream_digest(stream)
                if not unchanged_handle(os.fstat(descriptor), record):
                    raise SafetyError('隔离原件在清理前发生变化，保留现场')
        verify_purge_original(record, expected)
        # FileDispositionInfo acts on the verified handle, so a racing pathname
        # replacement cannot redirect removal to a different object.
        flag = api.ctypes.c_ubyte(1)
        if not api.disposition(msvcrt.get_osfhandle(descriptor), 4, api.ctypes.byref(flag), 1):
            raise api.ctypes.WinError(api.ctypes.get_last_error())
    finally:
        os.close(descriptor)


def matches_original(current, original):
    return all(original.get(k) == value for k, value in current.items()
               if k != 'mtime_ns' or not current['dir'])
