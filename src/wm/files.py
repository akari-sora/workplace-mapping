"""Atomic project file writes and safe text file discovery."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile


@contextmanager
def ledger_lock(path: Path) -> Iterator[None]:
    path = path.resolve()
    if os.name == "nt":
        import msvcrt

        with path.with_name(f".{path.name}.lock").open("a+b") as lock:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        directory = os.open(path.parent, os.O_RDONLY)
        try:
            fcntl.flock(directory, fcntl.LOCK_EX)
            yield
        finally:
            os.close(directory)


def write_text(path: Path, text: str, *, validate: Callable[[Path], None] | None = None,
               permissions: int | None = None) -> None:
    write_bytes(path, text.encode("utf-8"), validate=validate, permissions=permissions)


def write_bytes(path: Path, data: bytes, *, validate: Callable[[Path], None] | None = None,
                permissions: int | None = None) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else None
    handle = NamedTemporaryFile(dir=path.parent, delete=False)
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(data)
        if validate is not None:
            validate(temporary)
        if permissions is not None:
            temporary.chmod(permissions)
        elif mode is not None:
            temporary.chmod(mode)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class MultiWriteError(OSError):
    def __init__(self, path: Path, cause: BaseException):
        super().__init__(str(cause))
        self.path = path
        self.cause = cause


def write_texts(
    changes: Sequence[tuple[Path, str, Callable[[Path], None] | None]],
) -> None:
    """Validate all replacements, then install all or restore prior bytes."""
    prepared: list[tuple[Path, Path, bytes | None, int | None]] = []
    committed: list[tuple[Path, bytes | None, int | None]] = []
    active: Path | None = None
    try:
        for path, text, validate in changes:
            path = path.resolve()
            active = path
            path.parent.mkdir(parents=True, exist_ok=True)
            existed = path.exists()
            original = path.read_bytes() if existed else None
            mode = path.stat().st_mode & 0o777 if existed else None
            handle = NamedTemporaryFile(dir=path.parent, delete=False)
            temporary = Path(handle.name)
            prepared.append((path, temporary, original, mode))
            with handle:
                handle.write(text.encode("utf-8"))
            if validate is not None:
                validate(temporary)
            if mode is not None:
                temporary.chmod(mode)

        for path, temporary, original, mode in prepared:
            active = path
            os.replace(temporary, path)
            committed.append((path, original, mode))
    except BaseException as exc:
        for path, original, mode in reversed(committed):
            restore: Path | None = None
            try:
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    handle = NamedTemporaryFile(dir=path.parent, delete=False)
                    restore = Path(handle.name)
                    with handle:
                        handle.write(original)
                    if mode is not None:
                        restore.chmod(mode)
                    os.replace(restore, path)
            finally:
                if restore is not None:
                    restore.unlink(missing_ok=True)
        raise MultiWriteError(active or changes[0][0], exc) from exc
    finally:
        for _, temporary, _, _ in prepared:
            temporary.unlink(missing_ok=True)


def text_files(directory: Path, *, within: Path | None = None) -> Iterator[Path]:
    for path in sorted(directory.rglob("*")):
        if path.is_file() and (within is None or path.resolve().is_relative_to(within)):
            yield path


def read_text_file(path: Path) -> str | None:
    try:
        data = path.read_bytes()
        if b"\0" not in data:
            return data.decode("utf-8")
    except (OSError, UnicodeError):
        pass
    return None
