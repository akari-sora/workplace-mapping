"""Read local Git metadata without fetching or taking optional Git locks."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, LC_ALL="C", GIT_CEILING_DIRECTORIES=str(root.parent))
    try:
        return subprocess.run(
            ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-C", str(root), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, env=env,
        )
    except OSError as exc:
        raise GitError("git is unavailable") from exc


def is_repository(root: Path) -> bool:
    probe = git(root, "rev-parse", "--show-toplevel")
    if probe.returncode == 0:
        return Path(probe.stdout.rstrip("\n")).resolve() == root
    if (root / ".git").exists() or "not a git repository" not in probe.stderr:
        raise GitError("git repository could not be verified")
    return False


def branch(root: Path) -> str | None:
    probe = git(root, "branch", "--show-current")
    if probe.returncode:
        raise GitError("git branch could not be read")
    return probe.stdout.strip() or None


def unpushed(root: Path) -> list[str]:
    probe = git(root, "rev-list", "@{upstream}..HEAD")
    if probe.returncode:
        raise GitError("unpushed commits could not be verified against an upstream")
    return [value[:7] for value in probe.stdout.splitlines()]


def unpushed_subjects(root: Path) -> list[str]:
    probe = git(root, "log", "--format=%H %s", "@{upstream}..HEAD")
    if probe.returncode:
        raise GitError("unpushed commits could not be verified against an upstream")
    return [identity[:7] + (" " + subject if subject else "")
            for identity, _, subject in (line.partition(" ") for line in probe.stdout.splitlines())]


def changed_files(root: Path) -> int:
    probe = git(root, "status", "--porcelain=v1", "--untracked-files=all", "-z")
    if probe.returncode:
        raise GitError("git changes could not be counted")
    changes = iter(probe.stdout.rstrip("\0").split("\0")) if probe.stdout else iter(())
    count = 0
    for entry in changes:
        count += 1
        if "R" in entry[:2] or "C" in entry[:2]:
            next(changes, None)
    return count
