"""runtime_paths.py — resolve the writable runtime-state directory for the
display companion (tokens, queues, device approvals, TLS certs, logs).

These files are deliberately kept OUT of the code directory: a plugin's code
dir is refreshed on `/plugin update` (which would wipe device approvals + certs)
and may be read-only. They live under the data root instead — see
scripts/paths.py `runtime_dir()`. Every display script imports `rt()` from here
so writers and readers always agree on the location.

Process-management files (app.pid, app.log, .cert-server.pid, .scheme) are NOT
runtime state — they are recreated on every launch and stay in the code dir.

A sandboxed DM can write the runtime dir and its own campaign folder, and the
display and portal write there from outside the sandbox. They use write_file()
and append_text(), which never write through a link the DM placed.
"""
import contextlib
import os
import pathlib
import secrets
import stat
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))

# Prefer the canonical resolver in scripts/paths.py; fall back to an identical
# computation if that import fails, so writers/readers never diverge.
sys.path.insert(0, os.path.join(_HERE, os.pardir, "scripts"))
try:
    from paths import runtime_dir as _runtime_dir
except ImportError:
    def _runtime_dir():
        raw = os.environ.get("DND_RUNTIME_DIR", "").strip()
        root = os.environ.get("DND_CAMPAIGN_ROOT", "").strip() or "~/.claude/dnd"
        base = pathlib.Path(raw).expanduser() if raw else pathlib.Path(root).expanduser() / ".runtime"
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        return base


RT = str(_runtime_dir())
_INITIAL_RT = RT


def rt(name: str) -> str:
    """Resolve current environment overrides, retaining explicit RT overrides."""
    base = RT if RT != _INITIAL_RT else str(_runtime_dir())
    return os.path.join(base, name)


def config_path(name: str) -> str:
    """Settings that name programs the display runs (the music and image renderers).

    They live in the data root, which the DM cannot write, not in the runtime dir, which it can.
    """
    root = os.environ.get("DND_CAMPAIGN_ROOT", "").strip() or "~/.claude/dnd"
    return os.path.join(os.path.realpath(os.path.expanduser(root)), name)


def scratch_dir(prefix: str) -> str:
    """A new private folder in the data root, for work that a sandboxed DM must not reach mid-way."""
    root = config_path("")
    os.makedirs(root, exist_ok=True)
    return tempfile.mkdtemp(prefix=prefix, dir=root)


_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_DIR_FD = {os.open, os.mkdir, os.rename, os.unlink} <= os.supports_dir_fd and bool(_NOFOLLOW)


def _below(folder: str, root: str) -> list[str]:
    for base in (os.path.abspath(root), os.path.realpath(root)):
        relative = os.path.relpath(folder, base)
        if relative == ".":
            return []
        if relative != os.pardir and not relative.startswith(os.pardir + os.sep):
            return relative.split(os.sep)
    raise OSError(f"{folder} is outside {root}")


def _open_folder(folder: str, root: str) -> int:
    """Open `folder`, creating it if missing, without following a link anywhere below `root`."""
    parts = _below(folder, root)
    fd = os.open(root, os.O_RDONLY | _DIRECTORY)
    try:
        for part in parts:
            with contextlib.suppress(FileExistsError):
                os.mkdir(part, 0o700, dir_fd=fd)
            child = os.open(part, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
    except BaseException:
        os.close(fd)
        raise
    return fd


def write_file(path, data, *, root=None, mode: int = 0o644, durable: bool = False) -> None:
    """Replace `path` with `data` (str is UTF-8) through a fresh temporary file.

    A link at `path`, or at any folder between `root` (default: the file's own folder) and the
    file, is never followed: a link at `path` is replaced, a linked folder raises OSError.
    """
    path = os.path.abspath(path)
    folder, name = os.path.split(path)
    payload = data.encode("utf-8") if isinstance(data, str) else data
    if not _DIR_FD:  # Windows: no dir_fd or O_NOFOLLOW
        fd, temporary = tempfile.mkstemp(dir=folder, prefix=f".{name}.")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
            os.replace(temporary, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temporary)
            raise
        return
    folder_fd = _open_folder(folder, root or folder)
    try:
        temporary = f".{name}.{secrets.token_hex(8)}.tmp"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, mode, dir_fd=folder_fd)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                if durable:
                    stream.flush()
                    os.fsync(stream.fileno())
            os.replace(temporary, name, src_dir_fd=folder_fd, dst_dir_fd=folder_fd)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temporary, dir_fd=folder_fd)
            raise
    finally:
        os.close(folder_fd)


def append_text(path, text: str, *, root=None, separator: str = "", durable: bool = False) -> None:
    """Append `text` (after `separator` when the file already has content).

    Raises OSError rather than append through a link, to a file with other hard links, or to
    anything but a regular file: an append keeps the file, so a hard link would reach its target.
    """
    path = os.path.abspath(path)
    folder, name = os.path.split(path)
    if _DIR_FD:
        folder_fd = _open_folder(folder, root or folder)
        try:
            fd = os.open(name, os.O_WRONLY | os.O_APPEND | os.O_CREAT | _NOFOLLOW, 0o600, dir_fd=folder_fd)
        finally:
            os.close(folder_fd)
    else:
        if os.path.islink(path):
            raise OSError(f"{path} is a link")
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "ab") as stream:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OSError(f"{path} is not a private regular file")
        stream.write(((separator if info.st_size else "") + text).encode("utf-8"))
        if durable:
            stream.flush()
            os.fsync(fd)
