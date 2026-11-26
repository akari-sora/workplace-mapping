"""Copy the selected packaged template into a project directory."""

from __future__ import annotations

import os
from pathlib import Path
from shutil import copymode
from typing import Any

from .config import UsageError, result, valid_name


def init(name: str, lang: str, directory: str) -> tuple[int, dict[str, Any]]:
    if not valid_name(name):
        raise UsageError("name must use lowercase letters, digits and hyphens")
    root = Path(directory).expanduser().resolve()
    config = root / "workplace.toml"
    if config.exists() or config.is_symlink():
        raise UsageError("workplace.toml already exists")
    templates = Path(os.environ.get("WM_TEMPLATES", Path(__file__).parent / "templates")) / lang
    entries = sorted(templates.rglob("*"))
    if not any(path.is_file() for path in entries):
        raise UsageError(f"no templates for {lang}")
    files, kept = [], []
    for source in entries:
        relative = source.relative_to(templates)
        target = root.joinpath(*(part.replace("__name__", name) for part in relative.parts))
        target.resolve().relative_to(root)
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            relative_target = target.relative_to(root).as_posix()
            if target.exists() or target.is_symlink():
                kept.append(relative_target)
                continue
            data = source.read_bytes()
            target.write_bytes(data.replace(b"{{name}}", name.encode("ascii")))
            copymode(source, target)
            files.append(relative_target)
    return result(files=files, kept=kept)
